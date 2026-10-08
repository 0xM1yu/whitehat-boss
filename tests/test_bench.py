"""bench 계약 테스트. **GPU도 네트워크도 없이 돈다.**

막는 것은 HTTP 계층 하나뿐이고 나머지는 실제 코드를 태운다 — 프롬프트 렌더, 스키마
파싱, 판정 로직이 전부 운영 경로다. 여기서 고정하는 것은 측정값이 아니라 **판정**이다:
장부가 0인데 서버가 적중을 보고할 때 무엇이라고 부르는가(R6), 기동 실패가 나머지 측정을
멈추는가, 그리고 못 잰 값이 registry에 새어 들어가지 않는가.

`--apply`·CLI·오버라이드 계약은 `tests/test_bench_apply.py`에 있다(800줄 상한, §8.1).
"""

import http.client
import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from src import bench, bench_metrics, bench_report
from src.adapters.factory import build_deps
from src.bench_metrics import (
    METRIC_EXTERNAL_HITS,
    METRIC_KV_USAGE,
    METRIC_NAMES,
    METRIC_PREFIX_HITS,
    METRIC_PREFIX_QUERIES,
    METRIC_TOKENS_CACHED,
    METRIC_TOKENS_RECOMPUTED,
)
from src.interfaces.model_client import Prompt
from src.orchestrator.prompt_loader import load_prompt
from src.schemas.candidate import Candidate
from src.schemas.common import Location
from src.schemas.enums import FindingState, Role
from tests.conftest import served_scout_config

RUN_ID = "run_20260101_0001"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TARGET = FIXTURES / "target"
MEASURED_AT = "2026-08-17T00:00:00Z"

# 실측 원문 (2026-08-14, Slurm <node> / vLLM 0.19.1 / Qwen3-0.6B).
# 파일명에 버전·장비를 박아 둔다 — ATOM 원문이 들어오면 두 번째 파일이 되고, 그때 이름이
# 바뀌면 `test_bench_reads_only_names_that_exist_in_every_fixture`가 잡는다.
# ⏵ 2026-10-08 — ATOM 원문은 2026-08-18에 받았으나(`2026-08-18-serving-atom.md` §3.5) 이 디렉터리에
#   안 들어왔고, ATOM은 2026-09-29 사용 종료(#340)다. 두 번째 파일은 아직 없다.
METRICS_DIR = FIXTURES / "metrics"
SLURM_METRICS = METRICS_DIR / "vllm_0191_slurm_sm86.txt"
SLURM_COMPLETION = METRICS_DIR / "vllm_0191_chat_completion.json"

# scout만 실서빙인 config. 실제 엔드포인트를 가리키는 역할이 하나는 있어야 스텁이
# 의미를 갖는다. (옛 `single_gpu_12g` 프로필의 모양 — `tests/conftest.py`의 합성 config.)


def _candidate_json() -> str:
    """스텁이 돌려줄 유효 응답. 스키마로 만들어 필드 이름을 손으로 적지 않는다."""
    return Candidate(
        candidate_id="cand_0001",
        run_id=RUN_ID,
        artifact_id="art_0001",
        location=Location(file="bench.c"),
        score=0.7,
        state=FindingState.CANDIDATE,
    ).model_dump_json()


def completion_fixture(content: str | None = None, *, cached: int | None = None) -> str:
    """실측 응답 원문(`prompt_tokens_details: null`의 실물)을 스텁 응답으로 쓴다.

    **content는 교체할 수 있다.** 원문의 content는 `finish_reason: "length"`로 잘린
    `<think>` 블록이라 어떤 출력 스키마로도 파싱되지 않는다 — 그 호출은
    `response_format` 없이 나갔기 때문이다. 스키마 파싱이 성공해야 하는 테스트만
    교체하고, **usage 봉투는 원문 그대로** 쓴다. R6를 재현하는 것은 봉투 쪽이다.
    """
    payload = json.loads(SLURM_COMPLETION.read_text(encoding="utf-8"))
    if content is not None:
        payload["choices"][0]["message"]["content"] = content
    if cached is not None:
        payload["usage"]["prompt_tokens_details"] = {"cached_tokens": cached}
    return json.dumps(payload)


def metrics_fixture(
    overrides: Mapping[str, float] | None = None,
    *,
    labels: Mapping[str, str] | None = None,
) -> str:
    """실측 `/metrics` 원문을 읽어 지정한 메트릭의 **값만** 치환한다.

    손으로 쓴 메트릭 문자열을 없애기 위한 헬퍼다. 스텁이 bench가 기대하는 이름을 그대로
    찍어 주는 바람에 존재하지 않는 이름(`vllm:gpu_cache_usage_perc`)이 테스트 26개를
    전부 통과했다(2026-08-14). **원문에 없는 이름을 주면 여기서 죽는다** — 그 길을 막는
    것이 이 헬퍼의 존재 이유다.

    앵커에 이름 뒤 `{`까지 넣는 이유: `vllm:prefix_cache_hits_created`처럼 같은 접두사를
    가진 계열이 원문에 함께 있다.

    `labels`는 `cache_config_info`의 라벨 값을 바꾼다(`enable_prefix_caching` 등).
    값 라인 전체를 재조립하지 않으므로 나머지 20여 개 라벨은 실측 그대로 남는다.
    """
    text = SLURM_METRICS.read_text(encoding="utf-8")
    for name, value in (overrides or {}).items():
        pattern = re.compile(r"^(" + re.escape(name) + r"\{[^}]*\}) .*$", re.MULTILINE)
        text, count = pattern.subn(r"\g<1> " + repr(float(value)), text)
        assert count == 1, f"{name}: 원문에서 {count}개 일치했다 (정확히 1개여야 한다)"
    for key, value in (labels or {}).items():
        pattern = re.compile(re.escape(key) + r'="[^"]*"')
        text, count = pattern.subn(key + '="' + value + '"', text)
        assert count == 1, f"{key} 라벨: 원문에서 {count}개 일치했다 (정확히 1개여야 한다)"
    return text


class _Response:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Server:
    """`urllib.request.urlopen` 대역. URL로 갈래를 나누고 호출을 전부 기록한다.

    `openai_compat`과 `bench`가 같은 함수를 쓰므로 이 하나로 둘 다 막힌다.
    """

    def __init__(
        self,
        *,
        models: list[str] | None = None,
        completion: str | None = None,
        metrics: list[str] | None = None,
        max_model_len: int | None = None,
    ) -> None:
        self.models = models
        self.completion = completion
        self.metrics = list(metrics or [])
        # 실제 vLLM의 `/v1/models` 응답에 있는 필드다. `None`이면 그 키가 아예 없는
        # 서버를 흉내낸다 — 그쪽이 종전 동작으로 떨어져야 한다(#126).
        self.max_model_len = max_model_len
        self.calls: list[str] = []

    def __call__(self, target: object, timeout: float | None = None) -> _Response:
        url = str(getattr(target, "full_url", target))
        self.calls.append(url)
        if url.endswith("/metrics"):
            return _Response(self._next_metrics())
        if url.endswith("/models"):
            if self.models is None:
                raise urllib.error.URLError("connection refused")
            entries: list[dict[str, object]] = [{"id": name} for name in self.models]
            if self.max_model_len is not None:
                for entry in entries:
                    entry["max_model_len"] = self.max_model_len
            return _Response(json.dumps({"data": entries}))
        if url.endswith("/chat/completions"):
            if self.completion is None:
                raise urllib.error.URLError("connection refused")
            return _Response(self.completion)
        raise AssertionError(f"스텁이 모르는 URL: {url}")

    def _next_metrics(self) -> str:
        if not self.metrics:
            raise urllib.error.URLError("no metrics endpoint")
        return self.metrics.pop(0) if len(self.metrics) > 1 else self.metrics[0]

    @property
    def completions(self) -> list[str]:
        return [url for url in self.calls if url.endswith("/chat/completions")]


@pytest.fixture
def served() -> tuple:
    """실서빙 역할 하나의 (client, template, tokens). 실제 config·프롬프트를 쓴다."""
    config = served_scout_config()
    key = config.model_key_for("scout")
    entry = config.models[key]
    template = load_prompt(Role.SCOUT, entry.prompt_template_version)
    tokens = config.thresholds.budget_for(bench.ROLE_SPEC[Role.SCOUT][1])
    return bench_report._client(config, key, role="scout"), template, tokens


# --- D: R6 판정 --------------------------------------------------------------


def test_null_prompt_tokens_details_is_reported_as_r6_not_an_adapter_bug(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """장부는 0인데 `/metrics`는 적중 — **어댑터 결함이 아니다.**

    2026-08-11 Slurm 실측이 이 경우였다(적중 99.0%인데 `cached_tokens: 0`). 여기서
    판정이 `neither`로 접히면 "prefix caching이 안 돈다"는 정반대 결론이 기록된다.
    """
    client, template, tokens = served
    server = _Server(
        completion=completion_fixture(_candidate_json()),
        metrics=[
            metrics_fixture(
                {METRIC_PREFIX_QUERIES: 0, METRIC_PREFIX_HITS: 0, METRIC_TOKENS_CACHED: 0}
            ),
            metrics_fixture(
                {METRIC_PREFIX_QUERIES: 5, METRIC_PREFIX_HITS: 4, METRIC_TOKENS_CACHED: 4}
            ),
        ],
    )
    monkeypatch.setattr(urllib.request, "urlopen", server)

    result = bench.measure_prefix_cache(
        client, Role.SCOUT, template, Candidate, tokens, 2, client.endpoint
    )

    assert result["verdict"] == "metrics_only"
    assert "어댑터 결함이 아니다" in result["note"]
    assert result["ledger"]["cached_tokens"] == 0
    assert result["metrics_delta"]["hits_delta"] == 4


def test_prefix_cache_uses_the_interval_delta_not_the_absolute_counter(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """앞 구간(B)이 남긴 카운터가 이 구간의 적중률로 새면 안 된다.

    절대값을 쓰면 서버가 언제 떴는지와 앞에서 무엇이 돌았는지가 그대로 섞인다.
    """
    client, template, tokens = served
    server = _Server(
        completion=completion_fixture(_candidate_json(), cached=90),
        metrics=[
            metrics_fixture({METRIC_PREFIX_QUERIES: 1000, METRIC_PREFIX_HITS: 990}),
            metrics_fixture({METRIC_PREFIX_QUERIES: 1002, METRIC_PREFIX_HITS: 991}),
        ],
    )
    monkeypatch.setattr(urllib.request, "urlopen", server)

    result = bench.measure_prefix_cache(
        client, Role.SCOUT, template, Candidate, tokens, 2, client.endpoint
    )

    assert result["verdict"] == "both_hit"
    assert result["metrics_delta"]["queries_delta"] == 2
    assert result["metrics_delta"]["hit_ratio"] == 0.5
    # 데워진 캐시를 잰 것이라는 사실이 남아야 cold start 수치로 오독되지 않는다.
    assert result["prewarmed_by"] == ["structured_output"]


def test_decreasing_counters_invalidate_the_interval(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """카운터가 줄면 서버가 재기동한 것이다. 0으로 접으면 '적중 없음'이라는 거짓말이 된다."""
    client, template, tokens = served
    server = _Server(
        completion=completion_fixture(_candidate_json(), cached=0),
        metrics=[
            metrics_fixture({METRIC_PREFIX_QUERIES: 1000, METRIC_PREFIX_HITS: 990}),
            metrics_fixture({METRIC_PREFIX_QUERIES: 3, METRIC_PREFIX_HITS: 2}),
        ],
    )
    monkeypatch.setattr(urllib.request, "urlopen", server)

    result = bench.measure_prefix_cache(
        client, Role.SCOUT, template, Candidate, tokens, 1, client.endpoint
    )

    assert result["verdict"] == "metrics_reset"


def test_missing_metrics_is_not_the_same_as_no_cache_hit(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    client, template, tokens = served
    server = _Server(completion=completion_fixture(_candidate_json(), cached=0), metrics=[])
    monkeypatch.setattr(urllib.request, "urlopen", server)

    result = bench.measure_prefix_cache(
        client, Role.SCOUT, template, Candidate, tokens, 1, client.endpoint
    )

    assert result["verdict"] == "metrics_unavailable"


def test_a_cut_metrics_response_is_a_failed_measurement_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`http.client.HTTPException`은 `OSError`가 아니다 — 응답 중간에 끊기면(`IncompleteRead`)
    그대로 새 나간다. 이 함수들의 계약은 「못 재면 `None`/오류 문자열」이고 측정 실패는 사건이
    아니다(호출처가 `openai_compat._post`의 try **밖**이라 새면 러너까지 간다 — 적대적 리뷰
    2026-10-02)."""

    def cut(url, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise http.client.IncompleteRead(b"# HELP", 100)

    monkeypatch.setattr(urllib.request, "urlopen", cut)

    assert bench_metrics.generation_tokens("http://127.0.0.1:9/v1") is None
    text, error = bench_metrics.fetch("http://127.0.0.1:9/metrics")
    assert text is None
    assert error is not None and "IncompleteRead" in error


# --- A: 기동 실패 ------------------------------------------------------------


def test_startup_failure_skips_the_rest_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """죽은 엔드포인트에 C·E까지 태우면 타임아웃만 쌓이고 결과는 못 쓴다."""
    config = served_scout_config()
    server = _Server(models=None, metrics=[metrics_fixture()])
    monkeypatch.setattr(urllib.request, "urlopen", server)

    result = bench_report._measure_role(
        config, "scout", samples=3, concurrency=[8], contexts=[8192]
    )

    assert result["startup"]["ok"] is False
    assert "기동 확인 실패로 건너뛴다" in result["skipped"]
    for stage in bench.MEASUREMENT_ORDER[1:]:
        assert result[stage] is None
    assert server.completions == []  # 모델을 한 번도 부르지 않았다


def test_startup_compares_served_id_with_the_registry_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버가 다른 이름으로 떠 있으면 실제 run의 `/chat/completions`도 같은 이유로 죽는다."""
    monkeypatch.setattr(urllib.request, "urlopen", _Server(models=["Qwen/Qwen3-4B"]))

    result = bench.check_startup("http://127.0.0.1:8001/v1", "qwen3.8-27b")

    assert result["ok"] is False
    assert "--served-model-name" in result["reason"]


def test_startup_keeps_the_servers_max_model_len(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버가 실제로 받는 상한을 응답에서 읽어 둔다 (#126).

    `context_limit`(registry)과 다른 축이다. 이 값을 안 들고 오면 스윕 꼭대기가
    `prompt + max_tokens > max_model_len`으로 **항상 400**을 받는다.
    """
    monkeypatch.setattr(
        urllib.request, "urlopen", _Server(models=["qwen3.8-27b"], max_model_len=32768)
    )

    result = bench.check_startup("http://127.0.0.1:8001/v1", "qwen3.8-27b")

    assert result["ok"] is True
    assert result["max_model_len"] == 32768


def test_startup_reports_no_max_model_len_when_the_server_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """그 키가 없는 서버도 있다. 그때는 종전 동작으로 떨어져야 한다 (#126)."""
    monkeypatch.setattr(urllib.request, "urlopen", _Server(models=["qwen3.8-27b"]))

    result = bench.check_startup("http://127.0.0.1:8001/v1", "qwen3.8-27b")

    assert result["ok"] is True
    assert result["max_model_len"] is None


# --- B: 구조화 출력 ----------------------------------------------------------


def test_schema_failures_are_counted_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """스키마 파싱 실패는 이 프로젝트가 재려는 지표다. 삼키면 영원히 0이 된다."""
    client, template, tokens = served
    monkeypatch.setattr(
        urllib.request, "urlopen", _Server(completion=completion_fixture('{"nope": 1}'))
    )

    result = bench.measure_structured_output(
        client, Role.SCOUT, template, Candidate, tokens, samples=2
    )

    assert result["valid"] == 0
    assert result["valid_ratio"] == 0.0
    assert all("SchemaParseError" in reason for reason in result["failures"])


def test_bodies_differ_between_samples_so_only_the_prefix_is_shared(served: tuple) -> None:
    """본문까지 같으면 서버가 body 접두사도 캐시해 처리량이 낙관적으로 나온다."""
    _, template, tokens = served
    first = bench.sized_prompt(Role.SCOUT, template, tokens, 0)
    second = bench.sized_prompt(Role.SCOUT, template, tokens, 1)

    assert first.prefix == second.prefix
    assert first.body != second.body


def test_body_length_follows_the_configured_capsule_budget(served: tuple) -> None:
    """길이를 상수로 박지 않는다 — `thresholds.capsule_budget_tokens`에서 온다."""
    _, template, tokens = served
    small = bench.approx_tokens(bench.sized_prompt(Role.SCOUT, template, tokens, 0))
    large = bench.approx_tokens(bench.sized_prompt(Role.SCOUT, template, tokens * 4, 0))

    assert small < large


# --- 운영 경로와 같은 클라이언트 ----------------------------------------------


def test_bench_client_matches_the_factory(tmp_path: Path) -> None:
    """다르면 bench가 잰 것이 운영 성능이 아니다 (bench.py 모듈 docstring 원칙 1)."""
    config = served_scout_config()
    deps = build_deps(config, run_id=RUN_ID, source_root=TARGET, artifacts_root=tmp_path)

    from_factory = deps.models.client_for("scout")
    from_bench = bench_report._client(config, config.model_key_for("scout"), role="scout")

    assert type(from_bench) is type(from_factory)
    assert vars(from_bench) == vars(from_factory)

    # ⚠ **위 대조만으로는 부족하다.** `vars()`가 같은 것은 두 쪽이 같은 값을 넣어서일
    # 수도 있고 **둘 다 기본값이라서**일 수도 있다. registry에 아직 `reasoning_effort`를
    # 선언한 모델이 없어서, 그 인자를 bench 쪽에만 안 넘겨도 이 검사가 통과했다 —
    # 어긋남이 **모델을 등재하는 날** 처음 드러난다.
    #
    # 그래서 registry가 선언한 상태를 만들어 한 번 더 본다.
    declared = config.transition(
        models={
            **config.models,
            config.model_key_for("scout"): config.models[
                config.model_key_for("scout")
            ].transition(reasoning_effort="high"),
        }
    )
    key = declared.model_key_for("scout")
    assert bench_report._client(declared, key, role="scout").reasoning_effort == "high", (
        "registry가 선언했는데 bench 클라이언트가 안 받았다 — bench가 잰 것이 운영 경로가 아니다"
    )

    # 역할별 `output_max`(D46 (c))도 **정확히 같은 자리**다. 저장소 config에 아직 선언이
    # 없으므로 위 `vars()` 대조는 둘 다 전역값이라서 통과한다 — 어긋남이 값을 처음
    # 넣는 날 드러난다. `reasoning_effort`가 그렇게 지나갔던 자리라 함께 막는다.
    capped = config.transition(
        thresholds=config.thresholds.transition(output_max_by_role={"scout": 8192})
    )
    assert bench_report._client(capped, key, role="scout").max_tokens == 8192, (
        "config가 역할별 상한을 선언했는데 bench 클라이언트가 전역값을 실었다"
    )


# --- 실측 원문 대조 ----------------------------------------------------------
#
# 아래 두 이름은 bench가 읽지 않는다(`METRIC_NAMES`에 없다). 교차 검증에만 쓴다 —
# 원문의 수치들이 서로 맞는지 보는 것이 개별 값을 박는 것보다 강하다.
METRIC_BY_SOURCE = "vllm:prompt_tokens_by_source_total"
METRIC_PROMPT_TOKENS = "vllm:prompt_tokens_total"

# 실측 시 보낸 요청 수. `prompt_tokens_total = 243 × 6`이 성립한다.
MEASURED_REQUESTS = 6


def _samples(text: str | None = None) -> list[tuple[str, dict[str, str], float]]:
    return bench_metrics.parse_prometheus(text if text is not None else metrics_fixture())


def _counters(monkeypatch: pytest.MonkeyPatch, text: str) -> bench_metrics.CacheCounters:
    """`read_counters`를 실제로 태운다 — 스냅샷 조립 로직을 테스트가 복제하지 않는다."""
    monkeypatch.setattr(urllib.request, "urlopen", _Server(metrics=[text]))
    return bench_metrics.read_counters("http://127.0.0.1:8001/v1")


def test_parser_survives_the_real_metrics_document() -> None:
    """원문 **전체**를 그대로 먹인다. 필요한 줄만 잘라 넣으면 검증이 아니다.

    히스토그램 `_bucket`(`le="+Inf"` 포함)·`_created` 게이지·`_sum`/`_count`가 섞인
    실제 문서에서 파서가 한 줄도 흘리지 않는지가 검증 대상이다.
    """
    text = SLURM_METRICS.read_text(encoding="utf-8")
    samples = bench_metrics.parse_prometheus(text)

    parsed = [name for name, _, _ in samples if name.startswith("vllm:")]
    in_file = [line for line in text.splitlines() if line.startswith("vllm:")]
    assert len(parsed) == len(in_file)
    assert any(name.endswith("_bucket") for name in parsed)
    assert any(name.endswith("_created") for name in parsed)


def test_sum_metric_does_not_absorb_the_external_series() -> None:
    """local과 external은 원문에 함께 있다. 부분 일치였다면 여기서 드러난다.

    실측에서 external이 0이라 값으로는 티가 나지 않으므로 **0이 아닌 값으로 바꿔서** 본다.
    KV connector를 쓰는 순간 조용히 틀어지는 것이 이 검사가 막는 것이다.
    """
    samples = _samples(metrics_fixture({METRIC_EXTERNAL_HITS: 7}))
    names = {name for name, _, _ in samples}
    assert {METRIC_PREFIX_HITS, METRIC_EXTERNAL_HITS} <= names  # 공허한 테스트가 아니다

    local = bench_metrics._sum_metric(samples, METRIC_PREFIX_HITS)
    external = bench_metrics._sum_metric(samples, METRIC_EXTERNAL_HITS)
    assert external == 7.0
    assert local != local + external  # external이 local에 섞이지 않았다
    assert local == bench_metrics._sum_metric(_samples(), METRIC_PREFIX_HITS)  # 원문 값 그대로


def test_measured_counters_are_internally_consistent() -> None:
    """원문의 수치들이 서로 맞는지 본다.

    파서가 라벨을 잘못 묶거나 계열을 섞으면 이 관계가 깨진다 — 개별 값을 박는 것보다
    강한 그물이다.
    """
    samples = _samples()
    by_source = {
        labels["source"]: value
        for name, labels, value in samples
        if name == METRIC_BY_SOURCE and "source" in labels
    }
    queries = bench_metrics._sum_metric(samples, METRIC_PREFIX_QUERIES)
    hits = bench_metrics._sum_metric(samples, METRIC_PREFIX_HITS)
    prompt_tokens = json.loads(SLURM_COMPLETION.read_text(encoding="utf-8"))["usage"][
        "prompt_tokens"
    ]

    # 처리한 프롬프트 토큰 = 계산한 것 + 캐시에서 온 것
    assert by_source["local_compute"] + by_source["local_cache_hit"] == queries
    assert bench_metrics._sum_metric(samples, METRIC_PROMPT_TOKENS) == queries
    assert queries == prompt_tokens * MEASURED_REQUESTS
    # external이 0이므로 local 적중과 local+external 캐시 토큰이 같다. **같은 것을 세서가
    # 아니다** — `prompt_tokens_cached_total`의 HELP는 "(local + external)"이다.
    assert by_source["external_kv_transfer"] == 0.0
    assert hits == by_source["local_cache_hit"]
    assert bench_metrics._sum_metric(samples, METRIC_TOKENS_CACHED) == hits


def test_bench_reads_only_names_that_exist_in_every_fixture() -> None:
    """`METRIC_NAMES`가 실측 원문에 전부 존재하는지 **파일별로** 본다.

    **이 테스트가 깨지는 것이 의도다.** ATOM 원문이 들어왔을 때 이름이 하나 달라지면
    여기서 실패하고, 그 실패가 정확히 우리가 사려는 신호다 — 그때 버전별 이름 맵을
    도입하면 된다. 지금 미리 관대하게 만들면 `gpu_cache_usage_perc` 사고가 그대로
    재발한다(그 이름은 존재하지 않는데 테스트 26개가 전부 초록이었다).
    ⏵ 2026-10-08 — ATOM 원문은 이 저장소에 안 들어왔다(위 `METRICS_DIR` 주석의 ⏵). 그 프로브는
    부재 이름 1건(`vllm:prompt_tokens_recomputed_total`)을 적었다
    (`2026-08-18-serving-atom.md` §3.3).
    """
    fixtures = sorted(METRICS_DIR.glob("*.txt"))
    assert fixtures, f"{METRICS_DIR}에 메트릭 원문이 없다"

    missing = {
        path.name: [
            name
            for name in METRIC_NAMES
            if name
            not in {n for n, _, _ in bench_metrics.parse_prometheus(path.read_text("utf-8"))}
        ]
        for path in fixtures
    }
    assert not any(missing.values()), f"원문에 없는 이름: {missing}"


def test_kv_cache_usage_is_read_from_the_gauge_the_server_actually_exposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이름이 틀렸던 항목이다. 파서가 gauge 라인을 읽어내는 데까지가 이 파일의 몫이다.

    원문의 값이 0.0인 것은 요청이 전부 끝난 뒤 스크레이프했기 때문이다 — 그래서 "0이
    아님"을 단언하지 않는다. 폴링·peak 로직은 값만 치환한 합성 스냅샷으로 본다.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _Server(metrics=[metrics_fixture()]))
    assert bench_metrics.kv_cache_perc("http://127.0.0.1:8001/v1") == 0.0

    monkeypatch.setattr(
        urllib.request, "urlopen", _Server(metrics=[metrics_fixture({METRIC_KV_USAGE: 0.42})])
    )
    assert bench_metrics.kv_cache_perc("http://127.0.0.1:8001/v1") == 0.42


def test_gpu_memory_utilization_comes_from_the_measured_config_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--apply`의 전제 조건이다. 실측 라벨은 `gpu_memory_utilization="0.6"`이다."""
    monkeypatch.setattr(urllib.request, "urlopen", _Server(metrics=[metrics_fixture()]))
    value, error = bench_metrics.gpu_memory_utilization("http://127.0.0.1:8001/v1")

    assert error is None
    assert 0.0 < value <= 1.0


def test_cache_config_labels_carry_the_names_that_are_not_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`kv_cache_memory_bytes`는 메트릭이 아니라 이 게이지의 **라벨**이다.

    프로브가 이 이름을 문자열로 다시 적지 않게 하려고 라벨을 통째로 내보낸다. 값을
    해석하지 않는 것도 계약이다 — 실측 원문에서 이 라벨은 문자열 `"None"`이고
    (Slurm는 이 값을 채우지 않는다), float으로 바꾸려 들면 거기서 죽는다. ATOM에서
    숫자로 채워지면 그때 `mem_gb_ctx8k`를 nvidia-smi 없이 얻을 수 있다.
    ⏵ 2026-10-08 — ATOM도 `None`이었다(2026-08-18 — `2026-08-18-serving-atom.md` §3.3).
    """
    monkeypatch.setattr(urllib.request, "urlopen", _Server(metrics=[metrics_fixture()]))
    labels, error = bench_metrics.cache_config_labels("http://127.0.0.1:8001/v1")

    assert error is None
    assert labels["kv_cache_memory_bytes"] == "None"
    assert labels["enable_prefix_caching"] == "True"
    # `gpu_memory_utilization()`이 float으로 주는 값과 **같은 라벨**에서 온다. 두 경로가
    # 갈라지면 프로브와 bench가 다른 것을 보고 있다는 뜻이다.
    monkeypatch.setattr(urllib.request, "urlopen", _Server(metrics=[metrics_fixture()]))
    value, _ = bench_metrics.gpu_memory_utilization("http://127.0.0.1:8001/v1")
    assert float(labels["gpu_memory_utilization"]) == value


def test_cache_config_labels_report_absence_instead_of_an_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """라벨이 없는 것과 못 읽은 것을 가른다. 둘 다 `{}`로만 주면 사유가 사라진다."""
    without = "\n".join(
        line
        for line in metrics_fixture().splitlines()
        if not line.startswith(bench_metrics.METRIC_CACHE_CONFIG)
    )
    monkeypatch.setattr(urllib.request, "urlopen", _Server(metrics=[without]))
    labels, error = bench_metrics.cache_config_labels("http://127.0.0.1:8001/v1")

    assert labels == {}
    assert bench_metrics.METRIC_CACHE_CONFIG in str(error)


# --- 구간이 성립하지 않는 경우 -----------------------------------------------


def test_zero_engine_traffic_is_not_reported_as_a_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """엔진이 본 요청이 0이면 캐시에 대해 아무 주장도 하지 않는다.

    **성공 호출이 있는데 엔진이 본 요청이 0이면 엔드포인트 불일치가 확정이다.** 역할마다
    포트가 다르고, 이 스택에서는 장부가 항상 0이라(R6) `ledger_only`가 발화할 수 없다 —
    이 검사가 없으면 엔드포인트 불일치가 "캐시가 안 걸린다"로 보고된다.
    """
    snapshot = _counters(monkeypatch, metrics_fixture())
    delta = bench_metrics.counter_delta(snapshot, snapshot, calls_ok=3)

    assert delta["status"] == "no_traffic"
    assert "엔드포인트 불일치" in delta["reason"]
    assert bench.verdict_for(0, delta)[0] == "no_traffic"


def test_zero_traffic_with_zero_successful_calls_blames_the_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _counters(monkeypatch, metrics_fixture())
    delta = bench_metrics.counter_delta(snapshot, snapshot, calls_ok=0)

    assert delta["status"] == "no_traffic"
    assert "요청이 전부 실패했다" in delta["reason"]


def test_no_traffic_is_unmeasured_not_a_failure() -> None:
    """pass도 fail도 아니다. 캐시 동작에 대한 정보가 없는 구간이다."""
    measured = [
        {
            "model_key": "qwen3.8-27b",
            "startup": {"ok": True},
            "skipped": None,
            "prefix_cache": {"verdict": "no_traffic"},
        }
    ]
    assert bench_report._judge_prefix(measured)["status"] == bench_report.UNMEASURED


def test_a_context_whose_call_failed_does_not_vanish_from_the_pass() -> None:
    """8K가 살아 있어도 32K의 HTTP 400이 `any` 아래에서 사라지지 않는다 (이슈 #111).

    `_judge_context`는 context들을 `any(peak > 0)` 한 비트로 접는다. 레코드는 정직한데
    (`call_ok: False`·`error`가 같은 dict에 있다) **고치기 전 요약이 원문보다 셌다** —
    답하지 못한 context가 있는데 무조건부 `pass`에 `reason: None`이 붙었다(CLAUDE.md §2.2).

    2026-09-01 ATOM 실측에서 32K가 `--max-model-len 32768`을 넘어 400을 받았고,
    체크리스트는 `pass`였다. 같은 모양이 2026-08-25 보고서에도 있다.

    **상태는 바꾸지 않는다.** `pass`를 `fail`로 뒤집는 것은 설계 판단이고 과거 run의
    판정이 함께 뒤집힌다 — 여기서 고정하는 것은 **사실이 사라지지 않는 것**뿐이다.
    `src/bench_report.py`의 `_judge_prefix`가 이미 그 모양이다(PASS + 못 답한 부분을 사유에).
    """
    measured = [
        {
            "model_key": "qwen3.8-27b",
            "startup": {"ok": True},
            "skipped": None,
            "memory": {
                "by_context": {
                    "8192": {"call_ok": True, "kv_cache_usage_perc_peak": 0.02665},
                    "32768": {
                        "call_ok": False,
                        "kv_cache_usage_perc_peak": 0.0,
                        "error": "ModelGatewayError: ... HTTP 400",
                    },
                }
            },
        }
    ]

    verdict = bench_report._judge_context(measured)

    assert verdict["status"] == bench_report.PASS  # 상태는 그대로다
    assert "32768" in str(verdict["reason"]), "답하지 못한 context가 요약에서 사라졌다"
    assert "8192" not in str(verdict["reason"]), "답한 context를 실패로 무고했다"


def test_all_contexts_failing_is_not_blamed_on_metrics() -> None:
    """전부 호출 실패면 원인은 `/metrics`가 아니다 — 고정 사유가 거짓이 된다.

    고치기 전 코드는 peak이 전부 0이면 무조건 "`/metrics`의 …를 읽지 못했다"를 냈다. 호출이
    안 닿아 0인 경우에도 그 문장이 나가서 **읽은 적 있는 메트릭을 못 읽었다고 말했다.**
    이번 run에서는 8K가 살아 발현하지 않았다 — 코드 독해에서 나온 경로다.
    """
    measured = [
        {
            "model_key": "qwen3.8-27b",
            "startup": {"ok": True},
            "skipped": None,
            "memory": {
                "by_context": {
                    "8192": {"call_ok": False, "kv_cache_usage_perc_peak": 0.0},
                    "32768": {"call_ok": False, "kv_cache_usage_perc_peak": 0.0},
                }
            },
        }
    ]

    verdict = bench_report._judge_context(measured)

    assert verdict["status"] == bench_report.UNMEASURED
    assert METRIC_KV_USAGE not in str(verdict["reason"]), "안 읽은 것이 아니라 안 닿은 것이다"
    assert "8192" in str(verdict["reason"]) and "32768" in str(verdict["reason"]), (
        "사유까지 지우면 #111과 같은 정보 소실이다"
    )


def test_a_decreasing_substitute_invalidates_only_that_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """부분 리셋이면 그 필드만 무효다. 0으로 접으면 note에 '캐시 토큰 -50개'가 실린다."""
    before = _counters(
        monkeypatch,
        metrics_fixture(
            {METRIC_PREFIX_QUERIES: 100, METRIC_PREFIX_HITS: 50, METRIC_TOKENS_CACHED: 50}
        ),
    )
    after = _counters(
        monkeypatch,
        metrics_fixture(
            {METRIC_PREFIX_QUERIES: 200, METRIC_PREFIX_HITS: 120, METRIC_TOKENS_CACHED: 10}
        ),
    )
    delta = bench_metrics.counter_delta(before, after, calls_ok=2)

    assert delta["status"] == "ok"  # queries/hits 구간은 살아 있다
    assert delta["hits_delta"] == 70.0
    assert delta["prompt_tokens_cached_delta"] is None
    assert "감소했다" in delta["prompt_tokens_cached_delta_reason"]
    assert delta["prompt_tokens_recomputed_delta"] == 0.0  # 0은 정상 측정값이다


def test_disabled_prefix_caching_is_split_from_a_cache_that_never_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`enable_prefix_caching=False`는 서버 기동 인자 문제다 — bench가 할 일이 없다.

    켜져 있는데 적중이 0인 것과 한 판정에 뭉치면 조사할 것과 다시 띄울 것이 섞인다.
    """
    disabled = {"enable_prefix_caching": "False"}
    before = _counters(
        monkeypatch,
        metrics_fixture({METRIC_PREFIX_QUERIES: 0, METRIC_PREFIX_HITS: 0}, labels=disabled),
    )
    after = _counters(
        monkeypatch,
        metrics_fixture({METRIC_PREFIX_QUERIES: 300, METRIC_PREFIX_HITS: 0}, labels=disabled),
    )

    verdict, note = bench.verdict_for(0, bench_metrics.counter_delta(before, after, calls_ok=3))
    assert verdict == "caching_disabled"
    assert "서버를 다시 띄워야 한다" in note


def test_enabled_but_never_hitting_stays_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    """실측 원문의 라벨은 True다. 그 상태에서 적중이 0이면 진짜 조사 대상이다."""
    before = _counters(
        monkeypatch, metrics_fixture({METRIC_PREFIX_QUERIES: 0, METRIC_PREFIX_HITS: 0})
    )
    after = _counters(
        monkeypatch, metrics_fixture({METRIC_PREFIX_QUERIES: 300, METRIC_PREFIX_HITS: 0})
    )

    verdict, note = bench.verdict_for(0, bench_metrics.counter_delta(before, after, calls_ok=3))
    assert verdict == "neither"
    assert "축출" in note


# --- 장부 대체 경로 ----------------------------------------------------------


def test_metrics_only_note_carries_the_substitute_token_count(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """R6가 "장부값을 안 준다"에서 "대체 경로가 있다"로 바뀌는 지점이다.

    구간 합계라 개별 `model_call`에 배분할 수 없다는 사실도 함께 실려야 한다 —
    `BudgetLedger`에 넣을 수 있다고 읽히면 그 자체가 다음 결함이 된다.
    """
    client, template, tokens = served
    server = _Server(
        completion=completion_fixture(_candidate_json()),  # prompt_tokens_details: null
        metrics=[
            metrics_fixture(
                {METRIC_PREFIX_QUERIES: 0, METRIC_PREFIX_HITS: 0, METRIC_TOKENS_CACHED: 0}
            ),
            metrics_fixture(
                {
                    METRIC_PREFIX_QUERIES: 400,
                    METRIC_PREFIX_HITS: 300,
                    METRIC_TOKENS_CACHED: 300,
                    METRIC_TOKENS_RECOMPUTED: 12,
                }
            ),
        ],
    )
    monkeypatch.setattr(urllib.request, "urlopen", server)

    result = bench.measure_prefix_cache(
        client, Role.SCOUT, template, Candidate, tokens, 2, client.endpoint
    )

    assert result["verdict"] == "metrics_only"
    assert result["metrics_delta"]["prompt_tokens_cached_delta"] == 300.0
    assert result["metrics_delta"]["prompt_tokens_recomputed_delta"] == 12.0
    assert result["metrics_delta"]["external_prefix_cache_hits_delta"] == 0.0
    assert "300" in result["note"]
    assert "BudgetLedger" in result["note"]


def test_measured_response_envelope_makes_the_ledger_zero(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """장부가 0인 것은 어댑터가 아니라 **응답 봉투** 때문이다 — 실물로 고정한다."""
    client, template, tokens = served
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _Server(completion=completion_fixture(_candidate_json())),
    )

    call = bench.call_once(
        client, Role.SCOUT, bench.sized_prompt(Role.SCOUT, template, tokens, 0), Candidate
    )

    usage = json.loads(SLURM_COMPLETION.read_text(encoding="utf-8"))["usage"]
    assert usage["prompt_tokens_details"] is None
    assert call.ok
    assert call.cached_tokens == 0
    assert call.input_tokens == usage["prompt_tokens"]


def test_the_measured_content_is_not_schema_valid(served: tuple) -> None:
    """실측 응답의 content를 교체하는 이유를 고정한다.

    그 호출은 `response_format` 없이 나갔고 `finish_reason: "length"`로 잘린 `<think>`
    블록이 왔다. **이 파일로는 B(구조화 출력)를 검증할 수 없다.** Qwen3 계열이 reasoning
    토큰을 먼저 내놓는다는 사실이 여기 박혀 있다 — ATOM에서 B가 실패하면 첫 용의자다.
    ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). 지금 실측 장비는 vast다(D81).
    """
    payload = json.loads(SLURM_COMPLETION.read_text(encoding="utf-8"))
    content = payload["choices"][0]["message"]["content"]

    assert payload["choices"][0]["finish_reason"] == "length"
    assert content.lstrip().startswith("<think>")
    with pytest.raises(ValidationError):
        Candidate.model_validate_json(content)


# --- nvidia-smi 사유 (2026-08-17 ATOM 실측) -----------------------------------


class _Completed:
    """`subprocess.run`의 반환을 흉내 낸다. 필요한 세 필드만 있다."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _smi(monkeypatch: pytest.MonkeyPatch, *, found: bool = True, **kwargs: object) -> None:
    path = "/usr/bin/nvidia-smi" if found else None
    monkeypatch.setattr(bench_metrics.shutil, "which", lambda _: path)
    monkeypatch.setattr(bench_metrics.subprocess, "run", lambda *a, **k: _Completed(**kwargs))


def test_unified_memory_reports_na_not_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """GB10은 통합메모리라 memory.used를 보고하지 않는다 (2026-08-17 ATOM 실측).

        nvidia-smi --query-gpu=name,compute_cap,memory.total,memory.used --format=csv,noheader
        → NVIDIA GB10, 12.1, [N/A], [N/A]

    nvidia-smi는 PATH에 있고 정상 종료한다. 사유가 "PATH에 없거나 실패했다"로 남으면
    **거짓이고**, 다음 사람이 드라이버를 뒤진다. 고칠 것이 없다는 사실 자체가 정보다.
    """
    _smi(monkeypatch, stdout="[N/A]\n")

    value, reason = bench_metrics.nvidia_smi_mb()

    assert value is None
    assert reason is not None
    assert "통합메모리" in reason and "PATH" not in reason.split("—")[0]


def test_a_missing_binary_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _smi(monkeypatch, found=False)

    value, reason = bench_metrics.nvidia_smi_mb()

    assert value is None
    assert reason == "nvidia-smi가 PATH에 없다"


def test_a_nonzero_exit_carries_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """비정상 종료는 세 번째 경우와 다르다 — 그 오류를 봐야 한다."""
    _smi(monkeypatch, returncode=9, stderr="Failed to initialize NVML\n")

    value, reason = bench_metrics.nvidia_smi_mb()

    assert value is None
    assert reason is not None
    assert "9" in reason and "NVML" in reason


def test_a_number_is_returned_without_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _smi(monkeypatch, stdout="1024\n2048\n")

    assert bench_metrics.nvidia_smi_mb() == (2048.0, None)


# --- context 스윕 상한 (#126) -------------------------------------------------


def _stub_sweep(monkeypatch: pytest.MonkeyPatch, measured: list[int]) -> None:
    """`_one_context`를 실제로 부르지 않고 어느 context가 시도됐는지만 센다."""
    monkeypatch.setattr(
        bench,
        "_one_context",
        lambda client, role, template, schema, ctx, endpoint: measured.append(ctx) or {},
    )
    monkeypatch.setattr(bench, "gpu_memory_utilization", lambda endpoint: (0.0, "스텁"))


def test_context_sweep_skips_what_the_server_cannot_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`prompt + output_max > max_model_len`인 context는 **재보지 않는다** (#126).

    2026-09-02 ATOM에서 스윕 꼭대기(32,768)가 `output_max` 4,096과 합쳐져
    `--max-model-len` 32,768을 넘어 **항상 400**을 받았다. 두 번째 발생이다.
    재려다 실패한 것이 아니라 **애초에 못 재는 것**이므로 사유를 남기고 건너뛴다.
    """
    measured: list[int] = []
    _stub_sweep(monkeypatch, measured)

    result = bench.measure_memory(
        None,
        Role.SCOUT,
        None,
        None,
        (8192, 32768),
        "http://x",
        262144,
        server_limit=32768,
        output_max=4096,
    )

    assert measured == [8192], "서버가 못 받는 context에 요청을 보냈다"
    skipped = result["by_context"]["32768"]["skipped"]
    assert "max_model_len" in skipped and "4096" in skipped


def test_context_sweep_keeps_measuring_when_the_server_limit_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """상한을 안 주는 서버에서는 **종전대로** 재보고 실패를 기록한다 (#126).

    이슈가 명시한 조건이다 — 값이 없다고 스윕을 좁히면 잴 수 있는 것을 안 재게 된다.
    """
    measured: list[int] = []
    _stub_sweep(monkeypatch, measured)

    bench.measure_memory(
        None,
        Role.SCOUT,
        None,
        None,
        (8192, 32768),
        "http://x",
        262144,
        server_limit=None,
        output_max=4096,
    )

    assert measured == [8192, 32768]


def test_the_context_sweep_makes_the_caller_state_the_output_cap() -> None:
    """`output_max`에 기본값을 두지 않는다 — 빠뜨리면 #126 가드가 **조용히 약해진다**.

    기본값 `0`이면 `ctx + output_max > server_limit`이 `ctx > server_limit`으로 퇴화한다.
    2026-09-02 ATOM에서 스윕 꼭대기가 400을 받은 조건이 정확히 `ctx + 4,096 > 32,768`
    이었으므로, 퇴화한 가드는 그 run을 **다시 통과시킨다** — 재려다 실패한 것이 아닌 것을
    `dead`로 세면서.

    `_model_client`가 `role`에 기본값을 안 두는 사유와 같은 자리다(PR #185).
    """
    with pytest.raises(TypeError, match="output_max"):
        bench.measure_memory(
            None,
            Role.SCOUT,
            None,
            None,
            (8192, 32768),
            "http://x",
            262144,
            server_limit=32768,
        )


def test_context_sweep_still_honours_the_registry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """registry `context_limit` 분기는 그대로다 — 서버 상한과 **다른 축**이다 (#126)."""
    measured: list[int] = []
    _stub_sweep(monkeypatch, measured)

    result = bench.measure_memory(
        None,
        Role.SCOUT,
        None,
        None,
        (8192, 32768),
        "http://x",
        16384,
        server_limit=131072,
        output_max=4096,
    )

    assert measured == [8192]
    assert "context_limit" in result["by_context"]["32768"]["skipped"]


# --- 메모리 폴링 --------------------------------------------------------------


def test_memory_polling_sleeps_between_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    """폴링 사이에 간격이 있어야 한다. 없으면 관측이 재려는 대상을 흔든다.

    그전에는 `while not future.done():` 안에 sleep이 없어 **폴링 속도가 `nvidia-smi`가
    얼마나 느린가에 우연히 결정됐다.** 그 전제가 깨지는 자리가 ATOM이다 — 통합메모리라
    `memory.total`이 `[N/A]`로 빨리 돌아온다. 관측 빈도가 장비 특성에 좌우되면 장비 간
    비교에서 그것이 그대로 교란이 된다.
    """
    slept: list[float] = []
    _stub_polling(monkeypatch, slept)

    bench._one_context(
        client=None, role=Role.SCOUT, template=None, schema=None, ctx=8192, endpoint="http://x"
    )

    assert slept, "폴링 사이에 sleep이 없다"
    assert set(slept) == {bench._POLL_INTERVAL_S}


def test_memory_reports_how_many_samples_the_peak_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`*_peak`만으로는 3 표본에서 나온 값과 300 표본에서 나온 값이 구별되지 않는다.

    `*_reason`은 "한 번도 못 쟀다"만 가르므로 그 사이가 통째로 비어 있었다. 폴링 간격이
    생기면서 표본이 줄어 구멍이 커지므로 개수를 함께 낸다 — 캡슐의 `items=k`가 잘린
    값인지 캡슐만 봐서는 모르는 것(D13 잔여 (b))과 같은 모양이다.
    """
    counts: dict[str, int] = {}
    _stub_polling(monkeypatch, [], counts=counts)

    result = bench._one_context(
        client=None, role=Role.SCOUT, template=None, schema=None, ctx=8192, endpoint="http://x"
    )

    assert result["kv_cache_usage_perc_samples"] == counts["kv"]
    assert result["nvidia_smi_mb_samples"] == counts["smi"]
    # 표본이 있으면 peak도 그 표본에서 나온 값이다.
    assert result["nvidia_smi_mb_peak"] == 2048.0
    assert result["nvidia_smi_mb_reason"] is None


def _stub_polling(
    monkeypatch: pytest.MonkeyPatch, slept: list[float], counts: dict[str, int] | None = None
) -> None:
    """GPU도 네트워크도 없이 폴링 루프를 몇 바퀴 돌린다.

    호출 회수 자체는 스레드 스케줄링에 달려 있어 고정하지 않는다. 고정하는 것은
    **관계**다: 보고된 표본 수가 실제로 값을 얻은 횟수와 같은가.
    """
    seen = counts if counts is not None else {}
    seen.setdefault("kv", 0)
    seen.setdefault("smi", 0)
    done = threading.Event()

    def fake_kv(endpoint: str) -> float:
        seen["kv"] += 1
        if seen["kv"] >= 3:
            done.set()
        return 0.5

    def fake_smi() -> tuple[float, None]:
        seen["smi"] += 1
        return 2048.0, None

    def fake_call(client, role, prompt, schema):  # noqa: ANN001, ANN202
        done.wait(timeout=5)
        return bench.Call(True)

    monkeypatch.setattr(bench, "kv_cache_perc", fake_kv)
    monkeypatch.setattr(bench, "nvidia_smi_mb", fake_smi)
    monkeypatch.setattr(bench, "call_once", fake_call)
    monkeypatch.setattr(
        bench,
        "sized_prompt",
        lambda role, template, ctx, variant: Prompt(
            prefix="p", body="b", prompt_version="v1"
        ),
    )
    monkeypatch.setattr(bench, "approx_tokens", lambda prompt: 0)
    monkeypatch.setattr(bench.time, "sleep", slept.append)
