"""프로브 계약 테스트. **GPU도 네트워크도 없이 돈다.**

막는 것은 HTTP 계층 하나뿐이고 나머지는 실제 코드를 태운다 — `tests/test_bench.py`와 같은
방식이다. 여기서 고정하는 것은 측정값이 아니라 **절차**다: 이름 판정이 after 스냅샷으로
이뤄지는가, body가 매번 달라지는가, 남의 서버를 거절하는가, 그리고 산출물을 그대로
옮기면 fixture가 되는가.

스텁이 돌려주는 `/metrics`·응답은 전부 **실측 원문**이다. 손으로 쓴 메트릭 문서를 먹이면
`gpu_cache_usage_perc` 사고를 그대로 재현하게 된다(스텁이 기대하는 이름을 찍어 줘서
테스트 26개가 초록이었다).
"""

import ast
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from src.bench_metrics import METRIC_NAMES, METRIC_PREFIX_HITS, METRIC_PREFIX_QUERIES
from tools.probe import probe_metrics

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics"
SLURM_METRICS = FIXTURES / "vllm_0191_slurm_sm86.txt"
SLURM_COMPLETION = FIXTURES / "vllm_0191_chat_completion.json"

ENDPOINT = "http://127.0.0.1:8001/v1"
MODEL = "qwen3-0.6b"


def _after() -> str:
    """요청이 끝난 뒤의 원문. 카운터가 들어 있다."""
    return SLURM_METRICS.read_text(encoding="utf-8")


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
        "completion": SLURM_COMPLETION.read_text(encoding="utf-8"),
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
    """기본값이 바뀌면 ATOM 원문을 Slurm 원문과 직접 대조할 수 없다.

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
    stem = probe_metrics.fixture_stem("0.19.1", "slurm", "sm_86")

    assert stem == SLURM_METRICS.stem
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

    `prompt_tokens_details`는 R6 판정이고(Slurm는 null), `kv_cache_memory_bytes`는
    메트릭이 아니라 `cache_config_info`의 라벨이다 — ATOM에서 숫자로 채워지면
    `mem_gb_ctx8k`를 nvidia-smi 없이 얻는다.
    ⏵ 2026-10-08 — ATOM도 `None`이었다(2026-08-18 — `2026-08-18-serving-atom.md` §3.3).
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


def _imports_of(path: Path) -> set[str]:
    """그 파일이 import 하는 모듈 이름 전부. 점 있는 이름은 그대로 둔다.

    `ast.walk`을 쓰는 것이 요점이다 — 함수 안의 들여쓴 import도 잡는다
    (`probe_metrics.main()`의 `import time`이 실제로 그렇다). 줄 앞을 `startswith`로
    훑으면 그 형태가 통째로 빠져나간다.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _local_files(root: Path, dotted: str) -> list[Path]:
    """`src.bench_metrics` → 저장소 안 실제 파일들. 저장소 모듈이 아니면 빈 목록.

    **경로 위의 패키지 `__init__.py`를 전부 담는다.** `src.bench_metrics`를 import 하면
    `src/__init__.py`도 실행되므로, 그 파일이 비어 있다는 것까지 검사 대상이어야 한다 —
    프로브가 "클론만 하면 된다"고 주장하는 근거가 정확히 그 파일이다.
    """
    parts = dotted.split(".")
    found: list[Path] = []
    for depth in range(1, len(parts) + 1):
        base = root.joinpath(*parts[:depth])
        if (base / "__init__.py").is_file():
            found.append(base / "__init__.py")
        if depth == len(parts) and base.with_suffix(".py").is_file():
            found.append(base.with_suffix(".py"))
    return found


def test_the_probe_imports_nothing_outside_the_standard_library() -> None:
    """새 장비에서 **`pip install` 없이** 돌아야 한다. 이 성질의 유일한 자동 방어선이다.

    앞선 판(제3자 패키지 5개 이름을 나열한 거부 목록)은 세 곳이 뚫려 있었다: 목록에 없는
    패키지, 함수 안의 들여쓴 import, 그리고 **`src/__init__.py`**. 마지막이 특히 나빴다 —
    프로브가 "저장소를 클론만 하면 된다"고 주장하는 근거가 바로 그 파일이 비어 있다는
    것인데, 검사가 그 파일을 아예 읽지 않았다.

    그래서 거부 목록을 버리고 **`sys.stdlib_module_names`로 허용 목록**을 쓴다. 그리고
    모듈 이름을 나열하는 대신 `probe_metrics`에서 시작해 **저장소 안 모듈을 따라간다** —
    프로브가 새 `src.*` 모듈을 쓰기 시작해도 그 모듈이 자동으로 검사에 들어온다.
    """
    root = Path(probe_metrics.__file__).resolve().parents[2]
    pending = [Path(probe_metrics.__file__)]
    seen: set[Path] = set()
    offenders: dict[str, set[str]] = {}

    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        outside = set()
        for dotted in _imports_of(path):
            local = _local_files(root, dotted)
            if local:
                pending.extend(local)
                continue
            if dotted.split(".")[0] not in sys.stdlib_module_names:
                outside.add(dotted)
        if outside:
            offenders[str(path.relative_to(root))] = outside

    assert not offenders, f"stdlib 밖의 import: {offenders}"
    # 그물이 실제로 무엇을 훑었는지 고정한다. 그래프가 비면 위 단언이 공허하다.
    followed = {str(path.relative_to(root)).replace("\\", "/") for path in seen}
    assert "src/__init__.py" in followed
    assert "src/bench_metrics.py" in followed
    assert "src/console.py" in followed


# --- critic_output_budget --------------------------------------------------
#
# 이 프로브의 결론은 두 판단에 얹혀 있고 **둘 다 테스트가 없으면 조용히 틀린다**:
# ① Critic 프롬프트에 캡슐·원문이 안 실린다(그래서 21,021은 이 경로의 수가 아니다)
# ② 공백 폭주 응답이 상한 표본에서 빠진다(#79를 config에 적지 않는다).
# 나머지(인자 파싱·출력 포맷)는 안 고정한다 — 틀려도 결론이 안 바뀐다.


def _budget_row(*, hit_cap: bool, chars: int, trailing: int) -> dict[str, Any]:
    return {
        "hit_cap": hit_cap,
        "chars": chars,
        "trailing_whitespace": trailing,
        "output_tokens": 9999,
        "input_tokens": 1000,
        "est_tokens": 500,
        "json_valid": True,
    }


def test_a_whitespace_runaway_is_kept_out_of_the_cap_sample() -> None:
    """상한까지 공백으로 찬 응답의 `output_tokens`는 상한의 근거가 아니다 (#79).

    그 값으로 상한을 정하면 **버그를 config에 적는 것**이고, 그 뒤 모든 폭주가 그 값에
    비례해 비싸진다 — 폭주 차단이 상한의 유일한 목적인데 정반대로 간다.
    """
    from tools.probe.critic_output_budget import _summarize

    summary = _summarize([_budget_row(hit_cap=True, chars=40000, trailing=39000)])

    assert summary["whitespace_runaway"] == 1
    assert summary["cap_sample"]["n"] == 0, "공백 폭주가 상한 표본에 들어갔다"
    assert summary["ok"] == 1, "폭주도 성공 호출이다 — 분모에서 빼지 않는다"


def test_an_answer_that_filled_the_cap_with_content_stays_in_the_sample() -> None:
    """역도 성립해야 한다. 필터가 상한 도달을 전부 버리면 **잴 것을 안 재게 된다.**"""
    from tools.probe.critic_output_budget import _summarize

    summary = _summarize([_budget_row(hit_cap=True, chars=40000, trailing=3)])

    assert summary["whitespace_runaway"] == 0
    assert summary["cap_sample"]["max"] == 9999


def test_an_empty_cap_sample_reports_null_not_zero() -> None:
    """표본이 비면 `0`이 아니라 `null`이다.

    이 저장소에서 `0`은 **미실측 표시**다(CLAUDE.md §0). 빈 분포를 0으로 적으면
    "재봤더니 0이었다"로 읽힌다.
    """
    from tools.probe.critic_output_budget import _distribution

    assert _distribution([]) == {"n": 0, "min": None, "median": None, "max": None, "mean": None}


def test_the_reconstructed_critic_prompt_carries_no_capsule_and_no_source() -> None:
    """프로브의 v1 metadata-only 재구성에는 캡슐도 대상 원문도 안 실린다.

    이 핀은 `_reconstruct`만 검증한다. SOURCE를 받는 실 Critic v6·v7·v8(서빙은 v8)의 노드 배선·
    입력 크기는 검증하지 않는다 — 그 배선은 `test_critic_v6_wiring.py`가 별도로 문다.
    """
    from src.orchestrator.prompt_loader import load_prompt
    from src.schemas.enums import EvidenceKind
    from src.schemas.enums import Role as _Role
    from tests.test_gate import _evidence, _finding  # noqa: PLC0415
    from tools.probe.critic_output_budget import _reconstruct

    finding = _finding(refs=[])
    built = _reconstruct(
        finding,
        [_evidence("ev_static", EvidenceKind.STATIC)],
        template=load_prompt(_Role.CRITIC, "v1"),
        run_id="run_20260813_0001",
    )

    facts = json.loads(re.findall(r"```json\s*\n(.*?)\n```", built.prompt.body, re.DOTALL)[-1])
    assert facts["capsule_sections"] == [], "Critic 사실 블록에 캡슐이 실렸다"
    assert facts["source"] == "", "Critic 사실 블록에 대상 원문이 실렸다"
    assert facts["target"] is None
    # `evidence_refs`가 비면 근거 블록도 빈다 — 그래서 이 측정은 프롬프트 크기의 **하한**이다.
    assert facts["evidence"] == []
    assert built.evidence_count == 0


def test_the_probe_draft_schema_is_the_one_the_live_critic_node_sends() -> None:
    """`--schema draft`가 실서빙 `OUTPUT_SCHEMA`와 **같은 객체**여야 한다.

    2026-09-12 프로브는 `DecisionDraft`가 PR #182 브랜치에만 있던 시점에 돌아
    `--schema decision`으로 쟀고, #182가 머지되며 그 서술이 실서빙과 어긋났다
    (보고서 §3.3의 ⚠). 그 어긋남을 잡는 것이 사람이었다 — 여기에 걸어 둔다.

    같은 값인지(`==`)가 아니라 **같은 객체인지**(`is`)를 본다. 스키마를 갈아끼우고
    프로브만 안 따라오면 여기서 죽는다.
    """
    from src.orchestrator.nodes.critic import OUTPUT_SCHEMA  # noqa: PLC0415
    from tools.probe.critic_output_budget import _schema_for  # noqa: PLC0415

    assert _schema_for("draft") is OUTPUT_SCHEMA


def test_the_probe_decision_arm_is_an_upper_bound_on_the_draft_arm() -> None:
    """`Decision` 필드 집합이 `DecisionDraft`를 **포함**해야 한다.

    보고서 §3.3·§3.4와 `config/budgets.yaml`의 `output_max` 주석이 "여기 수는
    실서빙에 대한 상한"이라 적는 근거가 이 포함 관계 하나다. 관계가 뒤집히면
    그 문장들이 거짓이 되므로 여기서 막는다 — **실측이 아니라 구조적 논증이고,
    그 논증의 전제만 고정한다.**
    """
    from src.schemas.decision import Decision, DecisionDraft  # noqa: PLC0415

    assert set(DecisionDraft.model_fields) < set(Decision.model_fields)


# --- 두 프로브가 `_post`의 반환을 이름으로 읽는다 (#550) ----------------------------------------
#
# `OpenAICompatModelClient._post`는 한때 `(content, usage)` 튜플이었고 `finish_reason`을 더하며
# 셋으로 늘었다. 이 두 프로브는 그것을 `raw, usage = client._post(payload)`로 풀고 있었고,
# 풀기가 **둘 다 `except Exception` 안**이라 전 호출이
# `{"error": "ValueError: too many values to unpack"}`가 되어 GPU가 이미 쓴 생성이 버려졌다
# (#550 리뷰에서 3회 중 3회 실패로 재현). 그때 이 파일의 다른 핀은 둘 다 `_post`를 안 타서
# 전체 스위트(5,096개)가 초록이었다. 그래서 여기는 **실제 `_post`**를 탄다 — `urlopen`만 갈아
# 끼운다(`tests/test_model_call_instrumentation.py`의 `_serve`). 반환 모양이 또 바뀌어 프로브가
# 못 따라오면 행에 `error`가 생겨 여기서 죽는다.


def test_the_critic_budget_probe_reads_a_healthy_response_through_the_real_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """건강한 200 한 건이 `error` 없는 행이 되고, 그 행이 서버의 종료 사유를 든다.

    이 프로브는 「1회·재시도 없음·상한에 닿았나」로 `output_max`를 정하는 도구라 종료 사유가
    바로 그 물음의 답이다 — `hit_cap`은 토큰 수를 상한과 비교한 추정이라 `length`와 상한 값에서의
    `stop`을 못 가른다."""
    from tests.test_model_call_instrumentation import (  # noqa: PLC0415
        GOOD,
        TRUNCATED,
        Tiny,
        _client,
        _completion,
        _serve,
    )
    from tools.probe.critic_output_budget import _measure_one  # noqa: PLC0415

    _serve(
        monkeypatch,
        [
            _completion(TRUNCATED, 4096, "length"),
            _completion(GOOD, 12, "stop"),
            _completion(GOOD, 7, None),
        ],
    )
    client = _client()
    payload = client._request_payload("공통 지시", "후보 본문", Tiny)  # noqa: SLF001

    rows = [_measure_one(client, payload, 4096) for _ in range(3)]

    for row in rows:
        assert "error" not in row, row  # 시그니처가 또 어긋나면 여기서 죽는다
    assert [row["finish_reason"] for row in rows] == ["length", "stop", None]
    assert [row["output_tokens"] for row in rows] == [4096, 12, 7]
    assert rows[0]["hit_cap"] is True
    assert rows[0]["json_valid"] is False  # 절단된 JSON — 응답 원문도 행까지 왔다
    assert rows[1]["json_valid"] is True


def test_the_d67_diagnose_probe_reads_a_healthy_response_through_the_real_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--diagnose`의 한 번짜리 진단이 같은 자리에서 같은 이유로 죽던 것을 막는다.

    진단의 물음은 「잘렸나, 문법이 깨졌나」이고 이 행의 `finish_reason`이 그 답의 직접 증거다."""
    from src.interfaces.model_client import Prompt  # noqa: PLC0415
    from tests.test_model_call_instrumentation import (  # noqa: PLC0415
        GOOD,
        TRUNCATED,
        _client,
        _completion,
        _serve,
    )
    from tools.probe.d67_required_schema import _diagnose  # noqa: PLC0415

    _serve(monkeypatch, [_completion(TRUNCATED, 4096, "length"), _completion(GOOD, 12, "stop")])
    prompt = Prompt(prefix="공통 지시", body="후보 본문", prompt_version="v1")

    rows = _diagnose(_client(), arm="required", prompt=prompt, trials=2)

    for row in rows:
        assert "error" not in row, row
    assert [row["finish_reason"] for row in rows] == ["length", "stop"]
    assert [row["output_tokens"] for row in rows] == [4096, 12]
    assert rows[0]["json_valid"] is False
    assert rows[1]["json_valid"] is True


# --- `content: null`과 `completion_tokens` 미보고 (#550 3라운드) --------------------------------
#
# 위 두 핀은 건강한 200만 탔다. 서버가 `message.content`를 `null`로 주면
# (`PostedResponse.content`가 `str | None`인 이유) 두 프로브가 `raw.rstrip()`
# (`AttributeError`)·`len(raw)`(`TypeError`)에서 죽었다 — **`except Exception` 밖**이라 프로브
# 전체가 중단됐고, `critic_output_budget.main`은 행을 루프 뒤에야 쓰므로 그때까지 쓴 생성이
# 전부 버려졌다. 그 크래시는 원래 있었으나 프로브가 튜플 풀기에서 먼저 죽어 가려져 있었고,
# 풀기를 고치면서 드러났다.
#
# 서버가 `usage.completion_tokens`를 안 실으면 `usage.output_tokens`는 0이다(`_usage_from`이
# 채운다). 이 프로브들은 그 값으로 `hit_cap`과 `output_max`를 정하는 분포(`cap_sample`)를
# 만드므로 지어낸 0이 재려던 값을 대신하면 안 된다(CLAUDE.md §0).


def test_the_critic_budget_probe_records_a_null_content_as_an_error_row_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`content: null`이 크래시가 아니라 `error` 행이고, 다음 호출이 이어진다.

    크래시였다면 첫 줄의 `_measure_one`이 예외를 던져 이 테스트가 죽는다. 두 번째 호출이 정상
    행을 내는 것이 「이어서 간다」(= 이미 모은 행이 안 버려진다)의 증거다. 서버가 준 사유·토큰은
    버려지지 않고 `error` 문구에 남는다 — GPU가 이미 쓴 값이다."""
    from tests.test_model_call_instrumentation import (  # noqa: PLC0415
        GOOD,
        Tiny,
        _client,
        _completion,
        _serve,
    )
    from tools.probe.critic_output_budget import _measure_one  # noqa: PLC0415

    _serve(monkeypatch, [_completion(None, 4096, "length"), _completion(GOOD, 12, "stop")])
    client = _client()
    payload = client._request_payload("공통 지시", "후보 본문", Tiny)  # noqa: SLF001

    rows = [_measure_one(client, payload, 4096) for _ in range(2)]

    assert set(rows[0]) == {"error"}  # 기존 `except Exception` 행과 같은 모양이다
    assert "null" in rows[0]["error"]
    assert "finish_reason=length" in rows[0]["error"]
    assert "output_tokens=4096" in rows[0]["error"]
    assert "error" not in rows[1]
    assert rows[1]["finish_reason"] == "stop"


def test_the_d67_diagnose_probe_records_a_null_content_as_an_error_row_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위와 같은 일을 `--diagnose`가 한다 — `len(raw)`가 `TypeError`로 죽던 자리다."""
    from src.interfaces.model_client import Prompt  # noqa: PLC0415
    from tests.test_model_call_instrumentation import (  # noqa: PLC0415
        GOOD,
        _client,
        _completion,
        _serve,
    )
    from tools.probe.d67_required_schema import _diagnose  # noqa: PLC0415

    _serve(monkeypatch, [_completion(None, 4096, "length"), _completion(GOOD, 12, "stop")])
    prompt = Prompt(prefix="공통 지시", body="후보 본문", prompt_version="v1")

    rows = _diagnose(_client(), arm="required", prompt=prompt, trials=2)

    assert set(rows[0]) == {"error"}
    assert "finish_reason=length" in rows[0]["error"]
    assert "output_tokens=4096" in rows[0]["error"]
    assert "error" not in rows[1]
    assert rows[1]["finish_reason"] == "stop"


@pytest.mark.parametrize(
    "reply_kwargs",
    [{"tokens": None}, {"tokens": None, "usage": False}],
    ids=["completion_tokens_absent", "usage_object_absent"],
)
def test_the_critic_budget_probe_does_not_record_an_unreported_token_count_as_a_measured_zero(
    monkeypatch: pytest.MonkeyPatch, reply_kwargs: dict[str, Any]
) -> None:
    """서버가 출력 토큰을 안 실으면 행이 `output_tokens: null`·`hit_cap: null`이고 표본에서 빠진다.

    0으로 남기면 `hit_cap: False`가 「상한에 안 닿았다」는 측정처럼 읽히고 그 0이 `cap_sample`(출력
    상한을 정하는 분포)에 들어간다. 반대편(서버가 **0이라고 보고한** 응답)은 `0`·`False`로 남아
    표본에 들어간다 — 둘이 한 값으로 합쳐지지 않는다."""
    from tests.test_model_call_instrumentation import (  # noqa: PLC0415
        GOOD,
        Tiny,
        _client,
        _completion,
        _serve,
    )
    from tools.probe.critic_output_budget import _measure_one, _summarize  # noqa: PLC0415

    _serve(
        monkeypatch,
        [
            _completion(GOOD, **reply_kwargs, finish_reason="stop"),
            _completion(GOOD, 0, "stop"),
            _completion(GOOD, 77, "stop"),
        ],
    )
    client = _client()
    payload = client._request_payload("공통 지시", "후보 본문", Tiny)  # noqa: SLF001

    rows = [_measure_one(client, payload, 4096) for _ in range(3)]

    unreported, reported_zero, measured = rows
    assert "error" not in unreported  # 멀쩡한 200은 이것 때문에 오류가 되지 않는다
    assert unreported["output_tokens"] is None
    assert unreported["hit_cap"] is None
    assert unreported["finish_reason"] == "stop"  # 따로 실린 종료 사유는 그대로다
    assert reported_zero["output_tokens"] == 0
    assert reported_zero["hit_cap"] is False
    assert measured["output_tokens"] == 77

    summary = _summarize([r | {"est_tokens": 500} for r in rows])

    assert summary["output_tokens_unreported"] == 1
    assert summary["ok"] == 3  # 성공 호출이다 — 분모에서 빼지 않는다
    assert summary["cap_sample"]["n"] == 2  # 미보고 행만 빠진다
    assert summary["cap_sample"]["min"] == 0  # 보고된 0은 표본에 남는다
    assert summary["cap_sample"]["max"] == 77


@pytest.mark.parametrize(
    "reply_kwargs",
    [{"tokens": None}, {"tokens": None, "usage": False}],
    ids=["completion_tokens_absent", "usage_object_absent"],
)
def test_the_d67_diagnose_probe_does_not_record_an_unreported_token_count_as_a_measured_zero(
    monkeypatch: pytest.MonkeyPatch, reply_kwargs: dict[str, Any]
) -> None:
    """`--diagnose` 행도 같다 — 미보고는 `null`이고, 보고된 0은 `0`·`False`다."""
    from src.interfaces.model_client import Prompt  # noqa: PLC0415
    from tests.test_model_call_instrumentation import (  # noqa: PLC0415
        GOOD,
        _client,
        _completion,
        _serve,
    )
    from tools.probe.d67_required_schema import _diagnose  # noqa: PLC0415

    _serve(
        monkeypatch,
        [_completion(GOOD, **reply_kwargs, finish_reason="stop"), _completion(GOOD, 0, "stop")],
    )
    prompt = Prompt(prefix="공통 지시", body="후보 본문", prompt_version="v1")

    unreported, reported_zero = _diagnose(_client(), arm="required", prompt=prompt, trials=2)

    assert "error" not in unreported
    assert unreported["output_tokens"] is None
    assert unreported["hit_cap"] is None
    assert unreported["finish_reason"] == "stop"
    assert reported_zero["output_tokens"] == 0
    assert reported_zero["hit_cap"] is False
