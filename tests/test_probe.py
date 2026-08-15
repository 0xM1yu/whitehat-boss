"""프로브 계약 테스트. **GPU도 네트워크도 없이 돈다.**

막는 것은 HTTP 계층 하나뿐이고 나머지는 실제 코드를 태운다 — `tests/test_bench.py`와 같은
방식이다. 여기서 고정하는 것은 측정값이 아니라 **절차**다: 이름 판정이 after 스냅샷으로
이뤄지는가, body가 매번 달라지는가, 남의 서버를 거절하는가, 그리고 산출물을 그대로
옮기면 fixture가 되는가.

스텁이 돌려주는 `/metrics`·응답은 전부 **실측 원문**이다. 손으로 쓴 메트릭 문서를 먹이면
`gpu_cache_usage_perc` 사고를 그대로 재현하게 된다(스텁이 기대하는 이름을 찍어 줘서
테스트 26개가 초록이었다).
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.bench_metrics import METRIC_NAMES, METRIC_PREFIX_HITS, METRIC_PREFIX_QUERIES
from tools.probe import probe_metrics

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics"
SERAPH_METRICS = FIXTURES / "vllm_0191_seraph_sm86.txt"
SERAPH_COMPLETION = FIXTURES / "vllm_0191_chat_completion.json"

ENDPOINT = "http://127.0.0.1:8001/v1"
MODEL = "qwen3-0.6b"


def _after() -> str:
    """요청이 끝난 뒤의 원문. 카운터가 들어 있다."""
    return SERAPH_METRICS.read_text(encoding="utf-8")


def _before() -> str:
    """**첫 관측 전** 원문. `vllm:` 계열 전체가 아직 없다.

    실측 원문에서 그 줄들을 걷어내 만든다 — 손으로 쓰지 않는다. 이것이 프로브가 순서를
    지켜야 하는 이유의 실물이고, 아래 두 테스트가 그것을 단언한다.
    """
    keep = []
    for line in _after().splitlines():
        body = line[1:].strip() if line.startswith("#") else line
        for prefix in ("HELP ", "TYPE "):
            if body.startswith(prefix):
                body = body[len(prefix) :]
        if not body.startswith("vllm:"):
            keep.append(line)
    return "\n".join(keep)


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
    """`urllib.request.urlopen` 대역. `/metrics`를 순서대로 내주고 POST 본문을 기록한다.

    `tests/test_bench.py`의 같은 이름 스텁과 다른 점은 **POST 페이로드를 남긴다**는 것
    하나다. prefix가 고정이고 body가 갈리는지는 보낸 것을 봐야만 판정할 수 있다.
    """

    def __init__(
        self,
        *,
        models: list[str] | None = None,
        metrics: list[str] | None = None,
        completion: str | None = None,
        fail_models_times: int = 0,
    ) -> None:
        self.models = models
        self.metrics = list(metrics or [])
        self.completion = completion
        self.fail_models_times = fail_models_times
        self.payloads: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def __call__(self, target: object, timeout: float | None = None) -> _Response:
        url = str(getattr(target, "full_url", target))
        self.calls.append(url)
        if url.endswith("/metrics"):
            return _Response(self.metrics.pop(0) if len(self.metrics) > 1 else self.metrics[0])
        if url.endswith("/models"):
            if self.fail_models_times > 0:
                self.fail_models_times -= 1
                raise urllib.error.URLError("connection refused")
            if self.models is None:
                raise urllib.error.URLError("connection refused")
            return _Response(json.dumps({"data": [{"id": name} for name in self.models]}))
        if url.endswith("/chat/completions"):
            self.payloads.append(json.loads(getattr(target, "data", b"{}").decode("utf-8")))
            if self.completion is None:
                raise urllib.error.URLError("connection refused")
            return _Response(self.completion)
        raise AssertionError(f"스텁이 모르는 URL: {url}")


def _serve(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _Server:
    server = _Server(**kwargs)
    monkeypatch.setattr(urllib.request, "urlopen", server)
    return server


def _served(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _Server:
    """정상 서버 한 대. before → after 순으로 원문을 내준다."""
    defaults: dict[str, Any] = {
        "models": [MODEL],
        "metrics": [_before(), _after()],
        "completion": SERAPH_COMPLETION.read_text(encoding="utf-8"),
    }
    return _serve(monkeypatch, **{**defaults, **kwargs})


def _run(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "endpoint": ENDPOINT,
        "model": MODEL,
        "requests": 3,
        "prefix_tokens": 240,
        "max_tokens": 32,
        "wait_s": 10.0,
        "sleep": lambda _seconds: None,
    }
    return probe_metrics.probe(**{**defaults, **overrides})


# --- 측정 순서 ---------------------------------------------------------------


def test_the_before_snapshot_genuinely_lacks_the_counters() -> None:
    """이 파일의 나머지 테스트가 공허하지 않다는 것을 먼저 못박는다.

    before 원문에 카운터가 이미 있으면 "after로 판정한다"는 다음 테스트가 아무것도
    지키지 않는다. 프로브가 순서를 지켜야 하는 이유가 여기 있다.
    """
    missing = probe_metrics._missing_names(_before())

    assert METRIC_PREFIX_QUERIES in missing
    assert METRIC_PREFIX_HITS in missing
    assert probe_metrics._missing_names(_after()) == []


def test_missing_names_are_judged_on_the_after_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """before로 판정하면 "이 서버에는 그 이름이 없다"는 틀린 결론이 난다.

    Prometheus 카운터는 첫 관측 전에는 노출되지 않는다. 스텁이 before → after 순으로
    다른 문서를 내주므로, 프로브가 어느 쪽을 봤는지가 결과로 드러난다.
    """
    _served(monkeypatch)

    report = _run()

    assert report["missing_metric_names"] == []
    assert report["_before_text"] != report["_after_text"]


def test_metrics_are_not_scraped_before_the_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """호출 순서 자체를 본다. `/models` → `/metrics` → 요청 → `/metrics`."""
    server = _served(monkeypatch)

    _run()

    kinds = [url.rsplit("/", 1)[-1] for url in server.calls]
    first_metrics = kinds.index("metrics")
    first_completion = kinds.index("completions")
    assert kinds[0] == "models"
    assert first_metrics < first_completion
    # 마지막 요청 뒤에도 긁는다 — 그것이 구간 delta의 끝점이자 이름 판정의 근거다.
    assert kinds.index("metrics", first_completion) > first_completion


# --- 자극 --------------------------------------------------------------------


def test_bodies_differ_between_requests_but_the_prefix_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prefix가 갈리면 캐시가 안 걸리고, body가 같으면 서버가 body까지 캐시한다.

    둘 다 측정을 왜곡하므로 보낸 페이로드로 직접 본다.
    """
    server = _served(monkeypatch)

    _run(requests=4)

    prefixes = {payload["messages"][0]["content"] for payload in server.payloads}
    bodies = [payload["messages"][1]["content"] for payload in server.payloads]
    assert len(prefixes) == 1
    assert len(set(bodies)) == len(bodies) == 4


def test_the_prefix_is_long_enough_to_be_worth_caching() -> None:
    """0단계는 약 240토큰이었다. 짧으면 블록 경계에 걸리지 않아 적중이 0으로 나온다."""
    prefix = probe_metrics.build_prefix(240)

    assert len(prefix) >= 240 * probe_metrics.CHARS_PER_TOKEN
    # 요청마다 같은 문자열이어야 한다 — 생성이 결정론적인지까지 본다.
    assert prefix == probe_metrics.build_prefix(240)


def test_the_measured_defaults_match_the_committed_fixture() -> None:
    """기본값이 바뀌면 ATOM 원문을 Seraph 원문과 직접 대조할 수 없다.

    실측 원문의 `prefix_cache_queries_total`이 1458 = 243 × 6이다.
    """
    assert probe_metrics.DEFAULT_REQUESTS == 6
    assert probe_metrics.DEFAULT_PREFIX_TOKENS == 240


# --- 남의 서버를 재지 않는다 -------------------------------------------------


def test_a_foreign_server_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """공용 계정에서 남이 띄운 서버에 요청을 쏘고 우리 수치로 기록하는 길을 막는다."""
    server = _served(monkeypatch, models=["someone-elses-model"])

    with pytest.raises(probe_metrics.ProbeError) as caught:
        _run()

    assert "someone-elses-model" in str(caught.value)
    assert server.payloads == []  # 한 건도 쏘지 않았다


def test_waiting_gives_up_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버가 끝내 안 뜨면 사유와 함께 죽는다. 빈 산출물을 남기지 않는다."""
    _served(monkeypatch, models=None)

    with pytest.raises(probe_metrics.ProbeError) as caught:
        _run(wait_s=4.0)

    assert "서버가 뜨지 않았다" in str(caught.value)


def test_the_server_may_come_up_late(monkeypatch: pytest.MonkeyPatch) -> None:
    """기동 대기가 실제로 재시도하는지 본다 — 한 번 실패하고 포기하면 srun에서 못 쓴다."""
    _served(monkeypatch, fail_models_times=2)

    assert _run()["served_ids"] == [MODEL]


# --- 산출물 ------------------------------------------------------------------


def test_the_stem_reproduces_the_committed_fixture_name() -> None:
    """받아서 그대로 옮기면 fixture가 되어야 한다 — 손으로 이름을 바꾸는 단계는 없다.

    커밋된 파일 이름과 대조하므로, 명명 규칙이 바뀌면 여기서 드러난다.
    """
    stem = probe_metrics.fixture_stem("0.19.1", "seraph", "sm_86")

    assert stem == SERAPH_METRICS.stem
    assert (FIXTURES / f"{stem}.txt").exists()


def test_only_the_after_snapshot_is_a_fixture_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`tests/fixtures/metrics/*.txt`는 전부 `METRIC_NAMES`를 갖고 있어야 한다.

    before 스냅샷을 `.txt`로 두면 그 규칙을 깨는 파일이 복사 한 번으로 fixture 디렉터리에
    들어간다. 산출물 디렉터리에서 같은 glob을 돌려 그 일이 불가능한지 본다 —
    `test_bench_reads_only_names_that_exist_in_every_fixture`와 같은 검사다.
    """
    _served(monkeypatch)
    report = _run()

    written = probe_metrics.write_outputs(report, tmp_path, "vllm_0191_atom_sm121")

    candidates = sorted(tmp_path.glob("*.txt"))
    assert [path.name for path in candidates] == ["vllm_0191_atom_sm121.txt"]
    for path in candidates:
        assert probe_metrics._missing_names(path.read_text(encoding="utf-8")) == []
    assert Path(written["before_metrics"]).suffix == ".prom"
    assert Path(written["fixture_completion"]).name == "vllm_0191_atom_sm121_chat_completion.json"


def test_the_saved_document_is_byte_identical_to_what_the_server_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.gitattributes`가 `tests/fixtures/metrics/**`를 `-text`로 빼 둔 이유다.

    텍스트 모드로 쓰면 Windows에서 `\\n`이 `\\r\\n`이 되고, ATOM 원문을 Windows 경유로
    받는 순간 저장소의 "실측 원문"이 서버가 내보낸 것과 달라진다. 개행 개수가 아니라
    **바이트 동일성**을 단언한다 — 리눅스 CI에서도 공허하지 않은 유일한 형태다.
    """
    _served(monkeypatch)
    report = _run()

    written = probe_metrics.write_outputs(report, tmp_path, "vllm_0191_atom_sm121")

    for key, field in (
        ("fixture_metrics", "_after_text"),
        ("before_metrics", "_before_text"),
        ("fixture_completion", "_completion_raw"),
    ):
        assert Path(written[key]).read_bytes() == report[field].encode("utf-8"), key


def test_the_report_omits_the_raw_documents_it_already_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """원문은 파일로 나가고 리포트에는 판정만 남는다. 리포트가 원문 사본이 되면 안 된다."""
    _served(monkeypatch)
    report = _run()

    written = probe_metrics.write_outputs(report, tmp_path, "vllm_0191_atom_sm121")
    summary = json.loads(Path(written["report"]).read_text(encoding="utf-8"))

    assert not [key for key in summary if key.startswith("_")]
    assert summary["missing_metric_names"] == []
    # 사람이 읽는 요약도 실제로 조립되는지 본다 — 렌더가 죽으면 실측 현장에서 죽는다.
    assert any("fixture로 옮길 것은" in line for line in probe_metrics.render(report, written))


# --- 판정 --------------------------------------------------------------------


def test_r6_and_the_label_that_is_not_a_metric_are_both_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """출력에 반드시 있어야 하는 둘.

    `prompt_tokens_details`는 R6 판정이고(Seraph는 null), `kv_cache_memory_bytes`는
    메트릭이 아니라 `cache_config_info`의 라벨이다 — ATOM에서 숫자로 채워지면
    `mem_gb_ctx8k`를 nvidia-smi 없이 얻는다.
    """
    _served(monkeypatch)

    report = _run()

    assert report["prompt_tokens_details"] is None
    assert report["cache_config"]["kv_cache_memory_bytes"] == "None"
    assert report["cache_config"]["enable_prefix_caching"] == "True"
    assert report["cache_config"]["gpu_memory_utilization"] == "0.6"


def test_the_counter_interval_comes_from_the_shared_delta_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """구간 판정을 프로브가 다시 구현하지 않는다 — bench와 같은 함수를 지난다.

    before에 카운터가 없으므로 그 스냅샷을 읽을 수 없고, 그 사실이 `metrics_unavailable`로
    정직하게 나온다. 0으로 접거나 "적중 없음"으로 단정하지 않는 것이 계약이다.
    """
    _served(monkeypatch)

    delta = _run()["metrics_delta"]

    assert delta["status"] == "metrics_unavailable"
    assert METRIC_PREFIX_QUERIES in str(delta["reason"])


def test_a_warm_server_yields_a_real_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버가 이미 데워져 있으면 before에도 카운터가 있고 구간이 성립한다.

    같은 문서를 두 번 주므로 증분은 0이다 — 그것을 `no_traffic`으로 부르는 것이 옳고,
    "캐시가 안 걸린다"로 단정하지 않는 것이 `counter_delta`의 계약이다.
    """
    _served(monkeypatch, metrics=[_after(), _after()])

    delta = _run()["metrics_delta"]

    assert delta["status"] == "no_traffic"
    assert delta["prefix_caching_enabled"] is True


def test_failed_calls_are_counted_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """요청이 전부 실패해도 죽지 않는다. 실패 건수와 사유가 리포트에 남는다."""
    _served(monkeypatch, completion=None)

    report = _run()

    assert report["calls_ok"] == 0
    assert len(report["call_errors"]) == 3
    assert report["_completion_raw"] is None


# --- 의존성 ------------------------------------------------------------------


def test_the_probe_names_no_vllm_metric_of_its_own() -> None:
    """이름은 전부 `src.bench_metrics`를 지나야 한다.

    프로브 소스에 `vllm:` 문자열이 하나라도 있으면 그 순간 이름이 두 곳으로 갈린다 —
    `gpu_cache_usage_perc` 사고가 정확히 그 자리에서 났다.
    """
    source = Path(probe_metrics.__file__).read_text(encoding="utf-8")

    # 문자열 리터럴로 적힌 이름만 잡는다. docstring이 산문으로 이름을 **설명**하는 것은
    # 막을 이유가 없다 — 갈라져서 문제가 되는 것은 코드가 조회에 쓰는 값이다.
    assert re.findall(r"""["']vllm:""", source) == []
    assert METRIC_NAMES  # import 경로가 살아 있다는 것까지 본다


def test_main_forces_utf8_before_printing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows 콘솔(cp949)에서 `—`가 `UnicodeEncodeError`를 낸다.

    **저장소가 두 번 물린 결함이다**(`src/console.py`): `cli doctor`가 6줄 찍고 죽었고
    `eval.leakage`가 첫 줄에서 죽었다. 둘 다 pytest가 출력을 UTF-8로 잡아 테스트에는
    보이지 않았으므로, 출력 인코딩이 아니라 **호출 여부**를 본다. 수치를 다 받아 놓고
    화면에 못 찍는 것이 실측 현장에서 최악이다.
    """
    _served(monkeypatch)
    streams: list[object] = []
    monkeypatch.setattr(probe_metrics, "force_utf8", streams.append)

    code = probe_metrics.main(
        [
            *("--endpoint", ENDPOINT, "--model", MODEL),
            *("--vllm-version", "0.19.1", "--host-label", "atom", "--arch", "sm_121"),
            *("--out", str(tmp_path), "--requests", "2", "--wait", "5"),
        ]
    )

    assert code == 0
    assert len(streams) == 2  # stdout·stderr 둘 다
    assert (tmp_path / "vllm_0191_atom_sm121.txt").exists()


def test_main_writes_nothing_when_it_cannot_measure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """잴 수 없으면 종료 코드 1이고 산출물이 없다. 빈 fixture가 남으면 안 된다."""
    _served(monkeypatch, models=["someone-elses-model"])

    code = probe_metrics.main(
        [
            *("--endpoint", ENDPOINT, "--model", MODEL),
            *("--vllm-version", "0.19.1", "--host-label", "atom", "--arch", "sm_121"),
            *("--out", str(tmp_path / "out"), "--wait", "5"),
        ]
    )

    assert code == 1
    assert not (tmp_path / "out").exists()


def test_the_probe_imports_nothing_outside_the_standard_library() -> None:
    """새 장비에서 `pip install` 없이 돌아야 한다.

    `src.bench_metrics`가 stdlib만 쓰기 때문에 성립하는 성질이고, 그 모듈에 pydantic이
    들어오는 순간 조용히 깨진다. requirements.txt를 지우고 돌려 볼 수는 없으므로
    import 그래프로 본다.
    """
    import src.bench_metrics

    third_party = {"pydantic", "yaml", "requests", "httpx", "numpy"}
    import src.console

    for module in (probe_metrics, src.bench_metrics, src.console):
        names = {
            line.split()[1].split(".")[0]
            for line in Path(module.__file__).read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from ")) and len(line.split()) > 1
        }
        assert not (names & third_party), f"{module.__name__}: {names & third_party}"
