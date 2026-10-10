"""need_evidence 루프(ADR 0012 결정 2) — 정지 판정·resolver 호출·동료 확인.

**Fake는 NE 신호를 내지 않는다**(`fake_model.py` — `reason_codes`가 `triage_*` 접두를 절대
안 쓴다). 그래서 이 파일은 `FakeModelClient.complete`를 감싸 역할별로 홉 응답을 스크립트한다
(`_script_ne_client`, `tests/test_pipeline_e2e.py`의 `_failing_model` 꼴). resolver도 대개
스크립트한 함수다 — `test_ne_hop1_uses_the_real_resolver_against_the_real_indexer` 하나만
`src.adapters.symbol_resolver.resolve`를 실 인덱서(`tests/test_symbol_resolver.py`가 쓰는
방법 그대로)와 `tests/fixtures/ne_repo` 픽스처에 태워 돌린다 — resolver 조립 자체의
단위 검사는 `test_symbol_resolver.py` 몫이고, 여기서는 그 real resolver가 루프 안에서
실제로 불려 hop1 프롬프트에 결과가 실리는지만 본다.

**단일 `scout` 자리가 아니라 v3 풀로 돈다.** 바깥 `scout` 역할은 `prompts/scout/`에 v3가
없다(v1·v2뿐) — v3(`ScoutResponse`, 신호가 실리는 스키마)를 쓰려면 풀 멤버 셋(`scout_memory`·
`scout_boundary`·`scout_injection`)이 필요하다(`_poolify_v3`, `tests/test_pipeline_e2e.py`).
그래서 이 파일의 검사는 **셋을 전부 스크립트한다** — 보는 좌석(`scout_memory`, 대개)은
NE 시나리오를, 나머지 둘은 즉시 끝나는 응답(reject 한 번)을 받아 잡음을 줄인다. 단언은
그 좌석의 `candidate_id`로 걸러서 본다.

두 실행 모드(`per_candidate`·`per_stage`)가 같은 스크립트에서 같은 결과를 내야 한다 —
아래 시나리오 전부를 두 모드로 돈다.
"""

import dataclasses
import json
import re
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.adapters.factory import build_deps
from src.adapters.fake_model import FakeModelClient
from src.adapters.symbol_resolver import resolve as real_resolve
from src.config import load_config
from src.interfaces.model_client import ModelGatewayError, ModelResult, SchemaParseError, Usage
from src.orchestrator import runner
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import Stage
from src.schemas.candidate import Candidate, SliceRef
from src.schemas.common import Location
from src.schemas.enums import FindingState, NeStopReason, Role, ScoutSignal
from src.schemas.need_evidence import Resolution, ResolveQuery, RetrievedBlock, Unfilled
from src.schemas.scout_response import AnalysisSummary, RequestedContext, ScoutResponse
from tests.test_pipeline_e2e import TARGET, _events, _of, _poolify_v3, _run, _run_transition
from tests.test_symbol_resolver import _HOP1_ROOT, _graph_index
from tests.test_symbol_resolver import _read_lines as _hop1_read_lines

_MODES = pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])

_SEAT = "scout_memory"  # 시나리오를 스크립트하는 좌석. 나머지 둘은 즉시 reject로 끝낸다.
_OTHER_SEATS = ("scout_boundary", "scout_injection")


def _single_target(tmp_path: Path) -> Path:
    """analyzable 파일 하나뿐인 트리 — target·좌석 수를 손으로 셀 수 있게 한다.

    **멱등이다** — 한 검사 안에서 두 모드를 돌리며 같은 `tmp_path`로 두 번 부를 수 있다
    (두 모드가 같은 결과를 내는지 재는 검사가 그렇게 한다).
    """
    root = tmp_path / "single_target"
    if not root.exists():
        root.mkdir()
        (root / "parser.c").write_text(
            (TARGET / "parser.c").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return root


# `tests/fixtures/ne_repo`의 명세 hop 1 예 대상 — 실 인덱서·실 resolver 인덱싱 결과의
# 경로와 같은 문자열이다(`tests/test_symbol_resolver.py`의 `target_file`과 같은 값).
_HOP1_TARGET_FILE = "android-gif-drawable/src/main/c/giflib/dgif_lib.c"


def _hop1_pipeline_target(tmp_path: Path) -> Path:
    """파이프라인 스캔용 사본 — `dgif_lib.c` 하나만 담는다(`_single_target`과 같은 이유).

    `tests/fixtures/ne_repo`에는 `gif_lib.h`도 있는데(`GIF_STAMP_LEN` 매크로가 거기 있어
    실 인덱서가 둘 다 봐야 한다), 그 파일까지 파이프라인 스캔 루트에 두면 0단계가 그것도
    별도 target(SOURCE artifact)으로 잡아 `_SEAT`의 스크립트가 두 target에 걸쳐 순서가
    꼬인다(모드마다 hop0 개시 순서가 달라 `_script_ne_client`의 역할별 평평한 목록이 어느
    모드에서도 같은 순서로 안 맞는다). 그래서 여기서는 `.c` 하나만 복사해 target을 하나로
    묶는다 — 실 resolver 자체는 이 사본이 아니라 `_HOP1_ROOT`(헤더 포함 원본)를 본다.
    """
    root = tmp_path / "hop1_pipeline_target"
    if not root.exists():
        dest = root / _HOP1_TARGET_FILE
        dest.parent.mkdir(parents=True)
        dest.write_text(
            (_HOP1_ROOT / _HOP1_TARGET_FILE).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return root


def _extract_candidate_id(body: str) -> str:
    """hop0 프롬프트 본문의 FACTS에서 candidate_id를 읽는다(Fake와 같은 파싱)."""
    match = re.search(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)
    assert match, f"hop0 본문에 사실 블록이 없다: {body!r}"
    return json.loads(match.group(1))["ids"]["candidate_id"]


def _request(target: str = "doit") -> tuple[RequestedContext, ...]:
    """되묻기 요청 하나 — 대부분의 시나리오가 이 모양이면 충분하다."""
    return (RequestedContext(kind="function", target=target, purpose="p", request_code="c"),)


def _ne_response(
    signal: ScoutSignal,
    *,
    score: float = 0.8,
    requested: tuple[RequestedContext, ...] = (),
):
    """스크립트 항목 하나 — `candidate_id`를 받아 `ScoutResponse`를 짓는 빌더."""

    def build(candidate_id: str) -> ScoutResponse:
        return ScoutResponse(
            candidate=Candidate(
                candidate_id=candidate_id,
                run_id="run_20260901_0001",
                artifact_id="artifact_0001",
                location=Location(
                    file="parser.c", symbol="parse_header", line_start=3, line_end=6
                ),
                score=score,
                reason_codes=[f"{signal.value}:missing_request:function:doit"],
                state=FindingState.CANDIDATE,
            ),
            analysis_summary=AnalysisSummary(claim="scripted", requested_context=list(requested)),
        )

    return build


def _off_vocab_response(*, score: float = 0.8):
    """스크립트 항목 하나 — `reason_codes[0]`이 세 신호(`ScoutSignal`) 밖이다.

    스키마(`ScoutResponse`)는 통과하지만 `signal_of`가 `None`을 준다 — 자유 문자열인
    `reason_codes`(grammar로 안 묶는다, `scout_response.py` `RequestedContext` docstring과
    같은 이유)라 모델이 세 접두 중 어느 것도 아닌 값을 낼 수 있다."""

    def build(candidate_id: str) -> ScoutResponse:
        return ScoutResponse(
            candidate=Candidate(
                candidate_id=candidate_id,
                run_id="run_20260901_0001",
                artifact_id="artifact_0001",
                location=Location(
                    file="parser.c", symbol="parse_header", line_start=3, line_end=6
                ),
                score=score,
                reason_codes=["off_vocab_code:whatever"],
                state=FindingState.CANDIDATE,
            ),
            analysis_summary=AnalysisSummary(claim="off-vocab"),
        )

    return build


def _script_ne_client(
    monkeypatch: pytest.MonkeyPatch, scripts: dict[str, list[object]]
) -> list[dict[str, object]]:
    """역할별로 홉 응답(또는 예외)을 순서대로 스크립트한다.

    `scripts`에 없는 역할, 또는 스크립트를 다 쓴 뒤의 호출은 실제 `FakeModelClient`로
    넘어간다. 스크립트 항목은 `Exception`(그대로 던진다) 또는 `_ne_response(...)`가
    돌려준 빌더(callable, `candidate_id`를 받는다)다. 호출마다 `(role, hop, body)`를
    돌려주는 목록에 남긴다 — `hop`은 `history` 길이에서 유도한다(빈 history = hop0).
    """
    original = FakeModelClient.complete
    counters: dict[str, int] = {}
    known_ids: dict[str, str] = {}
    calls: list[dict[str, object]] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN202
        key = role.value
        hop = len(history) // 2
        script = scripts.get(key)
        if script is None or counters.get(key, 0) >= len(script):
            return original(self, role=role, prompt=prompt, schema=schema, history=history)
        index = counters.get(key, 0)
        counters[key] = index + 1
        if hop == 0:
            known_ids[key] = _extract_candidate_id(prompt.body)
        calls.append({"role": key, "hop": hop, "body": prompt.body})
        entry = script[index]
        if isinstance(entry, Exception):
            raise entry
        value = entry(known_ids[key])
        return ModelResult(
            value=value,
            usage=Usage(input_tokens=120, output_tokens=25, cached_tokens=0),
            model="scripted",
            raw_text=f"RAW-{key}-hop{hop}",
        )

    monkeypatch.setattr(FakeModelClient, "complete", complete)
    return calls


def _other_seats_script(signal: ScoutSignal = ScoutSignal.REJECT) -> dict[str, list[object]]:
    """`_SEAT` 말고 나머지 풀 좌석 — 한 번에 끝나 잡음을 줄인다."""
    return {seat: [_ne_response(signal)] for seat in _OTHER_SEATS}


def _always_resolves(followup: str = "RETRIEVED: doit @parser.c:3\n") -> object:
    """어떤 요청이든 블록 하나로 채우는 resolver 스텁 — 루프가 계속 돌게 한다."""

    def resolve(query: ResolveQuery) -> Resolution:
        return Resolution(
            followup_text=followup,
            blocks=(RetrievedBlock(name="doit", label="function", file="parser.c", lines=(3,)),),
            unfilled=(),
            already=(),
            truncated=(),
        )

    return resolve


def _nothing_resolves(query: ResolveQuery) -> Resolution:
    return Resolution(followup_text="", blocks=(), unfilled=(), already=(), truncated=())


def _wire_resolver(monkeypatch: pytest.MonkeyPatch, resolver: object | None) -> None:
    """`Dependencies.resolve_requests`에 스텁을 심는다(또는 `None`으로 비운다) — factory는
    손대지 않는다(`resolve_requests=None`이 그대로인 것이 브리프 §0의 전제다)."""
    original_build_deps = build_deps

    def spy_build_deps(*args: object, **kwargs: object) -> object:
        deps = original_build_deps(*args, **kwargs)
        return dataclasses.replace(deps, resolve_requests=resolver)

    monkeypatch.setattr("src.cli.build_deps", spy_build_deps)


def _v3_pool(mode: str | None):  # noqa: ANN202
    """`_poolify_v3`와 같지만 이 파일 전용 별칭 — 읽는 쪽에서 의도(v3 풀)가 바로 보이게."""
    return _poolify_v3(mode)


def _run_ne(
    tmp_path: Path,
    *,
    mode: str | None,
    stop_after: int = int(Stage.SCOUT),
    **overrides: object,
):
    return _run(
        tmp_path,
        configure=_v3_pool(mode),
        target=_single_target(tmp_path),
        stop_after=stop_after,
        **overrides,
    )


def _scout_transitions(trace_path: Path, candidate_id: str) -> list[str]:
    return [
        e["data"]["rule"]
        for e in _of(_events(trace_path), "transition")
        if e["stage"] == int(Stage.SCOUT) and e["subject_id"] == candidate_id
    ]


def _scout_hops(trace_path: Path, candidate_id: str) -> list[dict]:
    return [
        e["data"]
        for e in _of(_events(trace_path), "scout_hop")
        if e["subject_id"] == candidate_id
    ]


def _seat_calls(calls: list[dict[str, object]], role: str = _SEAT) -> list[dict[str, object]]:
    return [c for c in calls if c["role"] == role]


# --- 정지 판정 여섯 + peer_review + reject -----------------------------------


@_MODES
def test_ne_review_after_one_followup_promotes_with_only_scout_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """NE → review: 호출 2회 · hop1 로그(본문)에 RETRIEVED 텍스트 · TRIAGED · 전이 C-1, C-2만."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=_request()),
                _ne_response(ScoutSignal.REVIEW),
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves("RETRIEVED: doit is a helper\n"))

    _, trace_path = _run_ne(tmp_path, mode=mode)

    seat_calls = _seat_calls(calls)
    assert len(seat_calls) == 2, f"hop0·hop1 두 번을 기대했는데: {seat_calls}"
    assert seat_calls[1]["body"] == "RETRIEVED: doit is a helper\n"

    # candidate_id는 두 호출이 공유한다 — 스크립트 클로저가 안 돌려주므로 trace에서 역할로 찾는다.
    scout_hop_events = [
        e for e in _of(_events(trace_path), "scout_hop") if e["data"]["role"] == _SEAT
    ]
    assert scout_hop_events, "scout_memory의 SCOUT_HOP 이벤트가 없다"
    candidate_id = scout_hop_events[0]["subject_id"]

    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-2"]
    hops = _scout_hops(trace_path, candidate_id)
    assert [h["hop"] for h in hops] == [0, 1]
    assert hops[0].get("stop_reason") is None
    assert hops[1]["stop_reason"] == ScoutSignal.REVIEW.value


def _candidate_id_for_seat(trace_path: Path, role: str = _SEAT) -> str:
    events = [e for e in _of(_events(trace_path), "scout_hop") if e["data"]["role"] == role]
    assert events, f"{role}의 SCOUT_HOP 이벤트가 없다"
    return events[0]["subject_id"]


@_MODES
def test_ne_repeat_request_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """직전 홉과 (kind, target) 집합이 같은 요청 — `repeat_request`로 서고 QUEUED다."""
    same_request = _request()
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=same_request),
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=same_request),
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 2, "hop1이 hop0과 같은 요청을 반복했으니 hop2는 없어야 한다"
    candidate_id = _candidate_id_for_seat(trace_path)
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]
    hops = _scout_hops(trace_path, candidate_id)
    assert hops[-1]["stop_reason"] == NeStopReason.REPEAT_REQUEST.value
    transition = next(
        e
        for e in _of(_events(trace_path), "transition")
        if e["subject_id"] == candidate_id and e["data"]["rule"] == "C-3"
    )
    assert transition["data"]["reason"] == "need_evidence_unresolved"


@_MODES
def test_ne_empty_request_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """NE인데 `requested_context`가 비었다 — 되물을 것이 없으니 `empty_request`로 선다."""
    calls = _script_ne_client(
        monkeypatch,
        {_SEAT: [_ne_response(ScoutSignal.NEED_EVIDENCE, requested=())], **_other_seats_script()},
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 1, "빈 요청이면 resolver도 hop1도 없어야 한다"
    candidate_id = _candidate_id_for_seat(trace_path)
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]
    hops = _scout_hops(trace_path, candidate_id)
    assert len(hops) == 1
    assert hops[0]["stop_reason"] == NeStopReason.EMPTY_REQUEST.value


@_MODES
def test_ne_nothing_resolved_when_the_hook_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """이 run에 resolver가 없다(v1 인덱스·미로드와 같은 자리) — `nothing_resolved`로 선다."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request(),
                )
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, None)

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 1, "resolver가 없으면 hop1도 없어야 한다"
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert hops[0]["stop_reason"] == NeStopReason.NOTHING_RESOLVED.value
    # 훅이 없으면 조회가 안 돌았다 — `index_available`을 값으로 적지 않는다(#315 · `trace.py`).
    assert "index_available" not in hops[0]
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


@_MODES
def test_ne_resolver_that_finds_nothing_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """resolver는 있지만 아무것도 못 채웠다(`resolved == 0`이고 `fixable_count == 0`) —
    "잘 쓴 이름이 전부 조회에 실패했다"이므로 `nothing_resolved`다."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request(),
                )
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _nothing_resolves)

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 1
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert hops[0]["stop_reason"] == NeStopReason.NOTHING_RESOLVED.value
    assert hops[0]["resolved"] == 0
    assert hops[0]["fixable_count"] == 0
    assert hops[0]["index_available"] is True


def _no_index(query: ResolveQuery) -> Resolution:
    return Resolution(
        followup_text="",
        blocks=(),
        unfilled=(),
        already=(),
        truncated=(),
        index_available=False,
    )


@_MODES
def test_ne_stop_without_an_index_says_so_in_the_hop_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """색인이 없어 조회를 **안 한** 홉도 `nothing_resolved`로 서지만, 홉 이벤트가
    `index_available=False`로 「못 찾음」과 가른다(#315). 2026-09-27 ATOM 스모크 sample_07이 이
    꼴이었다 — 인덱서가 시간 초과로 `index.json`을 못 만들었는데 기록은 `unfilled=""`뿐이었다."""
    _script_ne_client(
        monkeypatch,
        {
            _SEAT: [_ne_response(ScoutSignal.NEED_EVIDENCE, requested=_request())],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _no_index)

    _, trace_path = _run_ne(tmp_path, mode=mode)

    hops = _scout_hops(trace_path, _candidate_id_for_seat(trace_path))
    assert hops[0]["stop_reason"] == NeStopReason.NOTHING_RESOLVED.value
    assert hops[0]["index_available"] is False


@_MODES
def test_ne_context_overflow_without_a_call_when_nothing_fit_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """resolver가 채울 만한 것을 찾았지만(`truncated`) 상한 안에 하나도 못 실었다(`blocks`
    0개) — hop1을 부르지 않고 그대로 `context_overflow`로 선다(`_ne_hop_max_chars`가 만드는
    "호출 없이 context_overflow" 두 경우 중 하나, 지금까지 E2E가 안 탔다)."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [_ne_response(ScoutSignal.NEED_EVIDENCE, requested=_request())],
            **_other_seats_script(),
        },
    )

    def truncated_only(query: ResolveQuery) -> Resolution:
        return Resolution(followup_text="", blocks=(), unfilled=(), already=(), truncated=("doit",))

    _wire_resolver(monkeypatch, truncated_only)

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 1, "블록 0개 + truncated면 hop1을 부르면 안 된다"
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert len(hops) == 1
    assert hops[0]["stop_reason"] == NeStopReason.CONTEXT_OVERFLOW.value
    assert hops[0]["truncated"] == "doit"
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


@_MODES
def test_ne_context_overflow_without_a_call_when_max_chars_is_non_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """`_ne_hop_max_chars`가 0 이하를 주면(남은 토큰 ≤ 0) resolver도 hop1도 부르지 않고
    그대로 `context_overflow`로 선다 — 위 검사와 짝인 나머지 한 경우. 좌석 모델
    (`fake_v3`)의 `context_limit`을 작게 잡아 hop0 usage(120+25)+여유(512)가 이미 그 창을
    넘게 한다(`_ne_hop_max_chars` 공식 — 값은 `context_limit`에 무관하게 음수가 된다:
    `output_max`는 항상 양수라야 하므로 `200 - output_max - 657 < 0`)."""
    resolver_calls: list[ResolveQuery] = []

    def resolver_spy(query: ResolveQuery) -> Resolution:
        resolver_calls.append(query)
        return _nothing_resolves(query)

    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [_ne_response(ScoutSignal.NEED_EVIDENCE, requested=_request())],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, resolver_spy)

    def configure(config):  # noqa: ANN001, ANN202
        base = _v3_pool(mode)(config)
        tiny_seat = base.models["fake_v3"].transition(context_limit=200)
        return base.transition(models={**base.models, "fake_v3": tiny_seat})

    _, trace_path = _run(
        tmp_path,
        configure=configure,
        target=_single_target(tmp_path),
        stop_after=int(Stage.SCOUT),
    )

    assert len(_seat_calls(calls)) == 1, "max_chars <= 0이면 hop1을 부르면 안 된다"
    assert not resolver_calls, "max_chars <= 0이면 resolver를 부르면 안 된다"
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert len(hops) == 1
    assert hops[0]["stop_reason"] == NeStopReason.CONTEXT_OVERFLOW.value
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


def _endless_ne_script(hops: int) -> list[object]:
    """`hops`번 되묻는 NE 스크립트 — 매번 다른 요청이라 `repeat_request`로는 안 선다."""
    return [
        _ne_response(
            ScoutSignal.NEED_EVIDENCE,
            requested=(
                RequestedContext(kind="function", target=f"doit{i}", purpose="p", request_code="c"),
            ),
        )
        for i in range(hops)
    ]


@_MODES
def test_ne_max_hop_exhausts_the_budget_and_queues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """`max_hops`(config 3) 홉을 다 쓰고도 NE면 `max_hop`으로 선다 — 호출은 `1 + max_hops`회."""
    max_hops = load_config("dev_fake").thresholds.max_hops
    script = _endless_ne_script(max_hops + 1)
    calls = _script_ne_client(monkeypatch, {_SEAT: script, **_other_seats_script()})
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == max_hops + 1, (
        f"홉 상한 {max_hops}에 hop0을 더해 {max_hops + 1}회를 기대했다"
    )
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert [h["hop"] for h in hops] == list(range(max_hops + 1))
    assert hops[-1]["stop_reason"] == NeStopReason.MAX_HOP.value
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


@_MODES
@pytest.mark.parametrize("pov_hops", [0, 9])
def test_pov_tool_hops_does_not_move_the_ne_hop_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None, pov_hops: int
) -> None:
    """음성 통제 — PoV 상한(`pov_tool_hops`)을 0으로 내리든 9로 올리든 Scout NE 홉 수는 그대로다.

    위 `test_ne_max_hop_exhausts_the_budget_and_queues`와 같은 시나리오다(#511):
    호출은 `1 + max_hops`회."""
    max_hops = load_config("dev_fake").thresholds.max_hops
    calls = _script_ne_client(
        monkeypatch, {_SEAT: _endless_ne_script(max_hops + 1), **_other_seats_script()}
    )
    _wire_resolver(monkeypatch, _always_resolves())

    def configure(config):  # noqa: ANN001, ANN202
        base = _v3_pool(mode)(config)
        return base.transition(thresholds=base.thresholds.transition(pov_tool_hops=pov_hops))

    _, trace_path = _run(
        tmp_path,
        configure=configure,
        target=_single_target(tmp_path),
        stop_after=int(Stage.SCOUT),
    )

    assert len(_seat_calls(calls)) == max_hops + 1
    hops = _scout_hops(trace_path, _candidate_id_for_seat(trace_path))
    assert [h["hop"] for h in hops] == list(range(max_hops + 1))
    assert hops[-1]["stop_reason"] == NeStopReason.MAX_HOP.value


@_MODES
def test_ne_hop1_parse_failure_is_absorbed_and_keeps_hop0_judgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """hop1 파싱 실패 → `parse_failed`로 흡수하고 **직전 홉(hop0)의 판정**으로 finish한다."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request(),
                ),
                SchemaParseError("scout이 2회 안에 ScoutResponse를 못 만들었다"),
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 2, "hop1은 시도됐다(실패해도 흡수한다)"
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert len(hops) == 2
    assert hops[-1]["stop_reason"] == NeStopReason.PARSE_FAILED.value
    assert hops[-1]["hop"] == 1
    # 직전 홉(hop0)이 NE였으므로 흡수 뒤에도 signal은 NE → C-3 need_evidence_unresolved.
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


@_MODES
def test_ne_hop1_off_vocab_signal_is_absorbed_as_parse_failed_and_keeps_hop0_judgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """hop1 응답이 스키마는 통과하지만 `reason_codes[0]`이 세 신호 밖이다(`signal_of`가
    `None`) — 판정으로 못 쓰니 파싱 실패와 같은 부류로 흡수하고 **직전 홉(hop0)의 판정**으로
    finish한다. 흡수 전에는 다음 반복에서 `session.signal.value`가 `None.value`를 불러
    `AttributeError`로 run 전체가 죽었다."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=_request()),
                _off_vocab_response(),
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 2, "hop1은 시도됐다(신호 이탈이어도 흡수한다)"
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert len(hops) == 2
    assert hops[-1]["hop"] == 1
    assert hops[-1]["stop_reason"] == NeStopReason.PARSE_FAILED.value
    # 스키마는 통과했다(파싱 자체는 됐다) — 신호 이탈과 스키마 실패가 기록에서 갈린다.
    assert hops[-1]["schema_ok"] is True
    assert hops[-1]["signal"] is None
    # 직전 홉(hop0)이 NE였으므로 흡수 뒤에도 signal은 NE → C-3 need_evidence_unresolved.
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


@_MODES
def test_ne_hop1_size_400_is_absorbed_as_context_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """hop1 크기 400(`_is_size_rejection`) → `context_overflow`로 흡수 + hop0 판정 유지."""
    overflow = ModelGatewayError(
        "HTTP 400 maximum context length exceeded",
        status=400,
        detail="This model's maximum context length is 40960 tokens.",
    )
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request(),
                ),
                overflow,
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    result, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 2
    # `stop_after=Stage.SCOUT`라 `is_complete`는 이 검사들 전부에서 항상 False다(의도한
    # 절단) — 흡수가 실제로 됐는지는 run이 **왜** 섰는지로 본다. 흡수 안 됐으면 D88대로
    # `consecutive_target_failures`가 나야 하는데, 여기서는 절단 사유(`user_stop_requested`)
    # 그대로여야 한다.
    assert _run_transition(trace_path)["data"]["reason"] == "user_stop_requested", (
        "크기 거절이 흡수되지 않고 run을 세웠다"
    )
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert hops[-1]["stop_reason"] == NeStopReason.CONTEXT_OVERFLOW.value
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]


@_MODES
def test_ne_hop1_non_size_400_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """hop1의 크기 아닌 400(`_rejects_every_request`) — **흡수하지 않는다.** run이 선다(D88)."""
    not_size = ModelGatewayError(
        "HTTP 400 bad request shape", status=400, detail="unknown parameter 'thinking_token_budget'"
    )
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request(),
                ),
                not_size,
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    result, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 2, "hop1은 시도됐지만 흡수되지 않았다"
    # `stop_after=Stage.SCOUT`라 is_complete는 다른 검사에서도 항상 False다(의도한 절단) —
    # 흡수하지 않았다는 것은 **사유**로 본다: D88대로 `consecutive_target_failures`여야
    # 한다(절단 사유 `user_stop_requested`가 아니다).
    assert _run_transition(trace_path)["data"]["reason"] == "consecutive_target_failures", (
        "크기 아닌 400이 흡수됐다 — D88이 되살아났다"
    )
    # 흡수하지 않았으므로 이 좌석에 대한 마지막 SCOUT_HOP은 hop0뿐이다(hop1 실패 이벤트 없음).
    candidate_id = _candidate_id_for_seat(trace_path)
    hops = _scout_hops(trace_path, candidate_id)
    assert [h["hop"] for h in hops] == [0]


@_MODES
def test_ne_peer_review_stops_other_ne_seats_without_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """한 좌석이 review면 **다른** NE 좌석은 호출 없이 `peer_review`로 선다(ADR 0012 결정 2)."""
    ne_request = _request()
    calls = _script_ne_client(
        monkeypatch,
        {
            "scout_memory": [_ne_response(ScoutSignal.REVIEW)],
            "scout_boundary": [
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=ne_request),
                _ne_response(ScoutSignal.REVIEW),  # 불려서는 안 된다
            ],
            "scout_injection": [
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=ne_request),
                _ne_response(ScoutSignal.REVIEW),  # 불려서는 안 된다
            ],
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls, "scout_memory")) == 1
    assert len(_seat_calls(calls, "scout_boundary")) == 1, "동료가 review인데 hop1을 불렀다"
    assert len(_seat_calls(calls, "scout_injection")) == 1, "동료가 review인데 hop1을 불렀다"

    for seat in ("scout_boundary", "scout_injection"):
        candidate_id = _candidate_id_for_seat(trace_path, seat)
        hops = _scout_hops(trace_path, candidate_id)
        assert len(hops) == 1, f"{seat}: 동료 확인 뒤에도 홉 이벤트가 하나여야 한다"
        assert hops[0]["stop_reason"] == NeStopReason.PEER_REVIEW.value
        # 아직 NE 신호이므로(동료 확인이 신호를 안 바꾼다) 판정은 미해소로 큐잉된다.
        assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]

    memory_id = _candidate_id_for_seat(trace_path, "scout_memory")
    assert _scout_transitions(trace_path, memory_id) == ["C-1", "C-2"]


@_MODES
def test_ne_reject_with_high_score_is_queued_not_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """reject + 점수 1.0 → QUEUED `scout_reject`다(D82 ⑤) — 판정은 점수가 아니라 신호다."""
    calls = _script_ne_client(
        monkeypatch,
        {_SEAT: [_ne_response(ScoutSignal.REJECT, score=1.0)], **_other_seats_script()},
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 1, "reject는 루프를 안 돈다"
    candidate_id = _candidate_id_for_seat(trace_path)
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-3"]
    transition = next(
        e
        for e in _of(_events(trace_path), "transition")
        if e["subject_id"] == candidate_id and e["data"]["rule"] == "C-3"
    )
    assert transition["data"]["reason"] == "scout_reject"


@_MODES
def test_ne_loop_keeps_one_candidate_id_across_hops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """루프가 홉을 여러 번 돌아도 candidate_id는 하나다 — model_call·scout_hop·transition이
    전부 같은 subject_id를 쓴다."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request("a"),
                ),
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request("b"),
                ),
                _ne_response(ScoutSignal.REVIEW),
            ],
            **_other_seats_script(),
        },
    )
    _wire_resolver(monkeypatch, _always_resolves())

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 3
    candidate_id = _candidate_id_for_seat(trace_path)
    model_calls = [
        e
        for e in _of(_events(trace_path), "model_call")
        if e["subject_id"] == candidate_id and e["data"]["role"] == _SEAT
    ]
    assert [c["data"]["hop"] for c in model_calls] == [0, 1, 2]
    hop_subjects = {
        e["subject_id"]
        for e in _of(_events(trace_path), "scout_hop")
        if e["data"]["role"] == _SEAT
    }
    assert hop_subjects == {candidate_id}
    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-2"]


def test_ne_two_modes_agree_on_transitions_and_hop_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 스크립트에서 두 모드가 같은 전이 집합과 같은 SCOUT_HOP 다중집합을 낸다.

    이벤트 순서는 모드마다 다를 수 있어 순서 무관으로 비교한다. `latency_s`는 실행마다
    다른 wall-clock이라(순번이 든 값과 같은 이유로) 비교에서 뺀다.
    """
    ne_request = _request()

    def script() -> dict[str, list[object]]:
        return {
            _SEAT: [
                _ne_response(ScoutSignal.NEED_EVIDENCE, requested=ne_request),
                _ne_response(ScoutSignal.REVIEW),
            ],
            **_other_seats_script(),
        }

    results = {}
    for mode in (None, "per_stage"):
        _script_ne_client(monkeypatch, script())
        _wire_resolver(monkeypatch, _always_resolves())
        _, trace_path = _run_ne(tmp_path, mode=mode, name=f"mode-{mode}")
        candidate_id = _candidate_id_for_seat(trace_path)
        transitions = sorted(
            (e["data"]["rule"], e["data"]["from_state"], e["data"]["to_state"], e["data"]["reason"])
            for e in _of(_events(trace_path), "transition")
            if e["subject_id"] == candidate_id
        )
        hops = [
            tuple(sorted((k, v) for k, v in h.items() if k != "latency_s"))
            for h in _scout_hops(trace_path, candidate_id)
        ]
        results[mode] = (transitions, sorted(hops, key=str))

    assert results[None][0] == results["per_stage"][0], "두 모드의 전이 집합이 다르다"
    assert results[None][1] == results["per_stage"][1], "두 모드의 SCOUT_HOP 다중집합이 다르다"


@_MODES
def test_scout_hop_event_carries_resolver_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """홉 이벤트에 resolver 통계(`resolved`·`already`·`fixable_count`·`unfilled`·`truncated`)가
    실린다."""
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=_request(),
                ),
                _ne_response(ScoutSignal.REVIEW),
            ],
            **_other_seats_script(),
        },
    )

    def resolver(query: ResolveQuery) -> Resolution:
        return Resolution(
            followup_text="RETRIEVED: doit\nAlready in SOURCE: helper\nNot resolved: ghost",
            blocks=(RetrievedBlock(name="doit", label="function", file="parser.c", lines=(3,)),),
            unfilled=(
                Unfilled(
                    kind="function", target="ghost", reason="not_found", text="Not resolved: ghost"
                ),
            ),
            already=("helper",),
            truncated=(),
        )

    _wire_resolver(monkeypatch, resolver)

    _, trace_path = _run_ne(tmp_path, mode=mode)

    assert len(_seat_calls(calls)) == 2
    candidate_id = _candidate_id_for_seat(trace_path)
    hop0 = _scout_hops(trace_path, candidate_id)[0]
    assert hop0["resolved"] == 2, "len(blocks) + len(already) = 1 + 1"
    assert hop0["already"] == 1
    assert hop0["fixable_count"] == 0
    assert hop0["unfilled"] == "not_found"


# --- 실 resolver E2E (계획 §7-7) ----------------------------------------------


@_MODES
def test_ne_hop1_uses_the_real_resolver_against_the_real_indexer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """루프가 스크립트한 resolve 훅이 아니라 **실 resolver**(`symbol_resolver.resolve` + 실
    인덱서의 index-v2 표 — v3도 그 표는 같다)와 Fake history로 끝까지 돈다.

    `_SEAT`가 hop0에 명세 hop 1 예의 요청(`variable:Buf`·`function:strdup`)을 내고, hop1에서
    review를 낸다. hop1 프롬프트 로그에 실 resolver가 만든 RETRIEVED 조각과 UNFILLED 줄이
    그대로 실려야 한다 — `assignment in the same function` 조각은 없다(실 인덱서의 `assigns`
    표가 원소 대입을 안 올리고 resolver가 v3 `writes` 표를 안 읽는다,
    `tests/test_symbol_resolver.py` 모듈 docstring의 발견과 같다).
    """
    calls = _script_ne_client(
        monkeypatch,
        {
            _SEAT: [
                _ne_response(
                    ScoutSignal.NEED_EVIDENCE,
                    requested=(
                        RequestedContext(
                            kind="variable", target="Buf", purpose="p", request_code="c"
                        ),
                        RequestedContext(
                            kind="function", target="strdup", purpose="p", request_code="c"
                        ),
                    ),
                ),
                _ne_response(ScoutSignal.REVIEW),
            ],
            **_other_seats_script(),
        },
    )
    graph = _graph_index(_HOP1_ROOT, tmp_path)
    resolver = partial(real_resolve, table=graph.table, read_lines=_hop1_read_lines(_HOP1_ROOT))
    _wire_resolver(monkeypatch, resolver)

    result, trace_path = _run(
        tmp_path,
        configure=_v3_pool(mode),
        target=_hop1_pipeline_target(tmp_path),
        stop_after=int(Stage.SCOUT),
    )

    assert len(_seat_calls(calls)) == 2, "hop0·hop1 두 번을 기대했는데"
    model_calls = [e for e in _of(_events(trace_path), "model_call") if e["data"]["role"] == _SEAT]
    subjects = {e["subject_id"] for e in model_calls}
    assert len(subjects) == 1, "루프 동안 candidate_id는 하나여야 한다"
    candidate_id = subjects.pop()
    assert sorted(c["data"]["hop"] for c in model_calls) == [0, 1]

    hop1_call = next(c for c in model_calls if c["data"]["hop"] == 1)
    hop1_log = (trace_path.parent / hop1_call["data"]["prompt_ref"]).read_text(encoding="utf-8")
    assert "/* --- retrieved: Buf (declaration (local)) --- */" in hop1_log
    assert "/* --- retrieved: GIF_STAMP_LEN (macro named in the declaration) --- */" in hop1_log
    assert "Not resolved: function:strdup (C standard library symbol)." in hop1_log
    assert "assignment in the same function" not in hop1_log

    assert _scout_transitions(trace_path, candidate_id) == ["C-1", "C-2"]
    # RETRIEVED는 Evidence가 되지 않는다 — 캡슐을 Evidence로 묶는 유일한 자리(5단계
    # Auditor)가 `stop_after=Stage.SCOUT`라 아예 안 돈다(구조로 보장된다, `findings`가
    # 비었다는 것으로 그 자리에 안 갔다는 것만 확인한다 — 이 확인은 "안 갔다"이지
    # RETRIEVED 자체를 Evidence로 바꾸는 코드가 없다는 것의 전수 증명은 아니다).
    assert result.findings == ()


# --- 단위 검사 — visible 묶기 · max_chars 계산 · 예외 분류 --------------------


def test_ne_visible_groups_slice_lines_by_file() -> None:
    ref = SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=5, symbol="parse_header"),
        given_end="sink",
        entry=Location(file="parser.c", line_start=5, symbol="memcpy"),
        lines=(("parser.c", 3), ("parser.c", 4), ("util.c", 10)),
    )

    visible = runner._ne_visible(ref)

    assert visible == {"parser.c": frozenset({3, 4}), "util.c": frozenset({10})}


def test_ne_visible_is_empty_without_a_slice() -> None:
    assert runner._ne_visible(None) == {}


def _minimal_ctx(tmp_path: Path) -> RunContext:
    """`_ne_hop_max_chars`처럼 `ctx.config`만 읽는 단위 검사용 — 파이프라인을 안 돌린다."""
    config = load_config("dev_fake")
    trace = runner.TraceWriter("run_test_0001")
    deps = build_deps(
        config, run_id="run_test_0001", source_root=tmp_path, artifacts_root=tmp_path / "artifacts"
    )
    return RunContext(
        run_id="run_test_0001",
        config=config,
        models=deps.models,
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
    )


def test_ne_hop_max_chars_caps_at_the_hop_ceiling(tmp_path: Path) -> None:
    """남은 예산이 넉넉하면 15,000자 상한(`_HOP_CHARS_MAX`)에서 묶인다.

    fake 모델의 `context_limit`은 32,768, 전역 `output_max`는 4,096이다(config/budgets.yaml).
    입력·출력 합 150에 여유 512를 빼도 남는 토큰이 충분해 3.28을 곱하면 상한을 넘는다.
    """
    ctx = _minimal_ctx(tmp_path)
    session = SimpleNamespace(usage=Usage(input_tokens=100, output_tokens=50, cached_tokens=0))

    assert runner._ne_hop_max_chars(ctx, Role.SCOUT, session) == runner._HOP_CHARS_MAX


def test_ne_hop_max_chars_is_zero_when_remaining_is_non_positive(tmp_path: Path) -> None:
    """직전 홉이 이미 창을 거의 다 썼으면(남은 토큰 ≤ 0) 0이다 — 호출자가 여기서 선다."""
    ctx = _minimal_ctx(tmp_path)
    # 32,768 − 4,096 − 30,000 − 0 − 512 < 0.
    session = SimpleNamespace(usage=Usage(input_tokens=30_000, output_tokens=0, cached_tokens=0))

    assert runner._ne_hop_max_chars(ctx, Role.SCOUT, session) == 0


def test_ne_classify_exception_maps_the_q6_table() -> None:
    """hop≥1 예외 분류(ADR 0012 결정 2) — 크기 아닌 400만 `None`(흡수하지 않는다)이다."""
    size_400 = ModelGatewayError(
        "HTTP 400", status=400, detail="This model's maximum context length is 10 tokens."
    )
    not_size_400 = ModelGatewayError("HTTP 400", status=400, detail="unknown request field")
    other_gateway = ModelGatewayError("HTTP 422", status=422, detail="x")

    assert runner._ne_classify_exception(size_400) is NeStopReason.CONTEXT_OVERFLOW
    assert runner._ne_classify_exception(not_size_400) is None, "크기 아닌 400은 흡수하지 않는다"
    assert runner._ne_classify_exception(other_gateway) is NeStopReason.GATEWAY_ERROR
    assert (
        runner._ne_classify_exception(SchemaParseError("파싱 실패")) is NeStopReason.PARSE_FAILED
    )
    assert (
        runner._ne_classify_exception(BudgetExhausted(4, "input_tokens", 100, 100))
        is NeStopReason.BUDGET_EXHAUSTED
    )
