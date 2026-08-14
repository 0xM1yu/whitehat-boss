"""bench 계약 테스트. **GPU도 네트워크도 없이 돈다.**

막는 것은 HTTP 계층 하나뿐이고 나머지는 실제 코드를 태운다 — 프롬프트 렌더, 스키마
파싱, 판정 로직이 전부 운영 경로다. 여기서 고정하는 것은 측정값이 아니라 **판정**이다:
장부가 0인데 서버가 적중을 보고할 때 무엇이라고 부르는가(R6), 기동 실패가 나머지 측정을
멈추는가, 그리고 못 잰 값이 registry에 새어 들어가지 않는가.

`--apply`·CLI·오버라이드 계약은 `tests/test_bench_apply.py`에 있다(800줄 상한, §8.1).
"""

import json
import re
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
from src.config import load_config
from src.orchestrator.prompt_loader import load_prompt
from src.schemas.candidate import Candidate
from src.schemas.common import Location
from src.schemas.enums import FindingState, Role

RUN_ID = "run_20260101_0001"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TARGET = FIXTURES / "target"
MEASURED_AT = "2026-08-17T00:00:00Z"

# 실측 원문 (2026-08-14, Seraph moana-u4 / vLLM 0.19.1 / Qwen3-0.6B).
# 파일명에 버전·장비를 박아 둔다 — ATOM 원문이 들어오면 두 번째 파일이 되고, 그때 이름이
# 바뀌면 `test_bench_reads_only_names_that_exist_in_every_fixture`가 잡는다.
METRICS_DIR = FIXTURES / "metrics"
SERAPH_METRICS = METRICS_DIR / "vllm_0191_seraph_sm86.txt"
SERAPH_COMPLETION = METRICS_DIR / "vllm_0191_chat_completion.json"

# scout만 실서빙인 프로필. 실제 엔드포인트를 가리키는 역할이 하나는 있어야 스텁이
# 의미를 갖는다.
SERVED_PROFILE = "single_gpu_12g"


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
    payload = json.loads(SERAPH_COMPLETION.read_text(encoding="utf-8"))
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
    text = SERAPH_METRICS.read_text(encoding="utf-8")
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
    ) -> None:
        self.models = models
        self.completion = completion
        self.metrics = list(metrics or [])
        self.calls: list[str] = []

    def __call__(self, target: object, timeout: float | None = None) -> _Response:
        url = str(getattr(target, "full_url", target))
        self.calls.append(url)
        if url.endswith("/metrics"):
            return _Response(self._next_metrics())
        if url.endswith("/models"):
            if self.models is None:
                raise urllib.error.URLError("connection refused")
            return _Response(json.dumps({"data": [{"id": name} for name in self.models]}))
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
    config = load_config(SERVED_PROFILE)
    key = config.model_key_for("scout")
    entry = config.models[key]
    template = load_prompt(Role.SCOUT, entry.prompt_template_version)
    tokens = config.thresholds.budget_for(bench.ROLE_SPEC[Role.SCOUT][1])
    return bench_report._client(config, key), template, tokens


# --- D: R6 판정 --------------------------------------------------------------


def test_null_prompt_tokens_details_is_reported_as_r6_not_an_adapter_bug(
    monkeypatch: pytest.MonkeyPatch, served: tuple
) -> None:
    """장부는 0인데 `/metrics`는 적중 — **어댑터 결함이 아니다.**

    2026-08-11 Seraph 실측이 이 경우였다(적중 99.0%인데 `cached_tokens: 0`). 여기서
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


# --- A: 기동 실패 ------------------------------------------------------------


def test_startup_failure_skips_the_rest_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """죽은 엔드포인트에 C·E까지 태우면 타임아웃만 쌓이고 결과는 못 쓴다."""
    config = load_config(SERVED_PROFILE)
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

    result = bench.check_startup("http://127.0.0.1:8001/v1", "qwen3-4b")

    assert result["ok"] is False
    assert "--served-model-name" in result["reason"]


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
    config = load_config(SERVED_PROFILE)
    deps = build_deps(config, run_id=RUN_ID, source_root=TARGET, artifacts_root=tmp_path)

    from_factory = deps.models.client_for("scout")
    from_bench = bench_report._client(config, config.model_key_for("scout"))

    assert type(from_bench) is type(from_factory)
    assert vars(from_bench) == vars(from_factory)


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
    text = SERAPH_METRICS.read_text(encoding="utf-8")
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
    prompt_tokens = json.loads(SERAPH_COMPLETION.read_text(encoding="utf-8"))["usage"][
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
            "model_key": "qwen3-4b",
            "startup": {"ok": True},
            "skipped": None,
            "prefix_cache": {"verdict": "no_traffic"},
        }
    ]
    assert bench_report._judge_prefix(measured)["status"] == bench_report.UNMEASURED


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

    usage = json.loads(SERAPH_COMPLETION.read_text(encoding="utf-8"))["usage"]
    assert usage["prompt_tokens_details"] is None
    assert call.ok
    assert call.cached_tokens == 0
    assert call.input_tokens == usage["prompt_tokens"]


def test_the_measured_content_is_not_schema_valid(served: tuple) -> None:
    """실측 응답의 content를 교체하는 이유를 고정한다.

    그 호출은 `response_format` 없이 나갔고 `finish_reason: "length"`로 잘린 `<think>`
    블록이 왔다. **이 파일로는 B(구조화 출력)를 검증할 수 없다.** Qwen3 계열이 reasoning
    토큰을 먼저 내놓는다는 사실이 여기 박혀 있다 — ATOM에서 B가 실패하면 첫 용의자다.
    """
    payload = json.loads(SERAPH_COMPLETION.read_text(encoding="utf-8"))
    content = payload["choices"][0]["message"]["content"]

    assert payload["choices"][0]["finish_reason"] == "length"
    assert content.lstrip().startswith("<think>")
    with pytest.raises(ValidationError):
        Candidate.model_validate_json(content)
