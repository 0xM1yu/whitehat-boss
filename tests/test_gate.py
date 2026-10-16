"""Evidence Gate 술어 테스트.

게이트는 순수 함수이므로 테스트도 순수하다 — store도 trace도 쓰지 않는다.
등급 판정은 이 시스템이 "측정하려는 값"이 아니라 **문서가 정한 규칙**이므로 여기서는
조건별 등급을 그대로 단언한다(architecture.md §4 표).
"""

from datetime import UTC, datetime
from itertools import product

import pytest

from src.adapters.container_tools import Step, judge
from src.config import load_config
from src.orchestrator import gate
from src.orchestrator.policy import DecisionPoint, FixedThresholdPolicy, RoutingRequest
from src.orchestrator.stages import Stage
from src.schemas import (
    Decision,
    Evidence,
    EvidenceGrade,
    EvidenceKind,
    Finding,
    FindingState,
    Location,
    MissingEvidence,
    PathStep,
    PathStepKind,
    ProducedBy,
    ReasonCode,
    ReproOrigin,
    ReproSpec,
    Role,
    Verdict,
)

RUN_ID = "run_20260901_0001"
OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)
TARGET = Location(file="parser.c", symbol="parse_header", line_start=10, line_end=20)
AUDITOR = ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v1")
CRITIC = ProducedBy(role=Role.CRITIC, model="fake", prompt_version="v1")

FULL_PATH = [
    PathStep(step=PathStepKind.SOURCE, symbol="read_input"),
    PathStep(step=PathStepKind.PROPAGATE, symbol="parse_header"),
    PathStep(step=PathStepKind.SINK, symbol="memcpy"),
]

BLOCKING = MissingEvidence(
    missing_id="mev_0001",
    run_id=RUN_ID,
    finding_id="find_0001",
    need="빌드 플래그",
    how_to_obtain="빌드 로그 첨부",
    blocking=True,
)

# E4가 받는 종류. 게이트의 `_RUNTIME_KINDS`를 import하지 않고 손으로 다시 적는다 — 그 상수가
# 줄어들 때 검사도 같이 줄어 변이가 살아나는 것을 막는다.
RUNTIME_KINDS = (EvidenceKind.SANITIZER, EvidenceKind.CRASH, EvidenceKind.POC_RUN)

# 0도 None도 아닌 종료 코드의 모양 넷 — asan의 기본 종료 코드(1) · 셸이 신호에 매기는 128+n(139) ·
# gdb가 신호로 멈춘 대상에 낸 값(255, 저장소 녹음 전부) · 신호를 음수로 돌려주는 런타임(-11).
# 값이 아니라 「0도 None도 아님」을 문다.
CRASH_EXIT_CODES = (1, 139, 255, -11)


def _evidence(
    evidence_id: str,
    kind: EvidenceKind,
    *,
    ok: bool = True,
    location: Location | None = None,
    exit_code: int | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=RUN_ID,
        kind=kind,
        source="semgrep",
        artifact_id="art_0001",
        location=location,
        payload_ref="logs/semgrep.json",
        digest="sha256:" + "0" * 64,
        observed_at=OBSERVED_AT,
        ok=ok,
        exit_code=exit_code,
        error=None if ok else "도구 실패",
    )


def _finding(
    *,
    refs: list[str],
    path: list[PathStep] | None = None,
    repro: bool = False,
) -> Finding:
    return Finding(
        finding_id="find_0001",
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="검증 없는 memcpy",
        cwe="CWE-787",
        state=FindingState.HYPOTHESIZED,
        confidence=0.6,
        path=path or [],
        evidence_refs=refs,
        produced_by=AUDITOR,
        repro_spec=ReproSpec(steps=["./build.sh"], expected_signal="ASAN") if repro else None,
    )


def _decision(
    verdict: Verdict, *, grounded: bool, axes: tuple[str, ...] = (), version: str | None = None
) -> Decision:
    """`grounded`는 **인용 ID**의 유무다. 반박 축은 `axes`로 따로 준다 - 둘이 근거의 두 갈래다
    (ADR 0015). 기본은 축 없음이라 `grounded=False`면 사유가 `empty_axis`다. `version`은 Critic
    프롬프트 판이다(`produced_by.prompt_version`) - 기본은 `CRITIC`의 v1이다."""
    return Decision(
        decision_id="dec_0001",
        run_id=RUN_ID,
        finding_id="find_0001",
        verdict=verdict,
        rebuttal_axis=list(axes),
        rationale="근거 서술",
        evidence_refs=["ev_graph"] if grounded else [],
        produced_by=CRITIC if version is None else CRITIC.transition(prompt_version=version),
    )


def test_no_evidence_refs_is_e0() -> None:
    result = gate.evaluate(_finding(refs=[]), [])

    assert result.grade is EvidenceGrade.E0
    assert not result.ambiguous


def test_static_signal_alone_is_e1() -> None:
    finding = _finding(refs=["ev_static"])
    evidences = [_evidence("ev_static", EvidenceKind.STATIC)]

    assert gate.evaluate(finding, evidences).grade is EvidenceGrade.E1


def test_failed_evidence_does_not_count() -> None:
    """`ok=False`는 실패 기록이지 근거가 아니다. `usable`이 그것을 거른다."""
    finding = _finding(refs=["ev_static"])
    evidences = [_evidence("ev_static", EvidenceKind.STATIC, ok=False)]

    assert gate.evaluate(finding, evidences).grade is EvidenceGrade.E0


def test_location_overlap_alone_is_e1() -> None:
    """정적 신호가 없어도 후보 위치와 겹치면 E1이다."""
    finding = _finding(refs=["ev_build"])
    evidences = [
        _evidence(
            "ev_build",
            EvidenceKind.BUILD,
            location=Location(file="parser.c", line_start=15),
        )
    ]

    result = gate.evaluate(finding, evidences, candidate_location=TARGET)

    assert result.predicates["location_ok"]
    assert result.grade is EvidenceGrade.E1


def test_path_and_graph_without_blocking_is_e2() -> None:
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences)

    assert result.grade is EvidenceGrade.E2
    assert not result.ambiguous


def test_blocking_missing_evidence_holds_at_e1_and_flags_ambiguous() -> None:
    """경로와 그래프는 있는데 blocking이 남았다 — E1↔E2 경계다.

    모델을 부르지 않고 E1으로 확정하되, 그 사실을 남길 수 있어야 한다.
    """
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]
    result = gate.evaluate(finding, evidences, missings=[BLOCKING])

    assert result.grade is EvidenceGrade.E1
    assert result.ambiguous


def test_upheld_with_repro_spec_is_e3() -> None:
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E3


def test_a_harness_synthesized_repro_spec_still_reaches_e3() -> None:
    """#S7: 하네스가 합성한 명세도 E3(`validated`)까지 올라간다 — 등급은 출처로 안 갈린다.
    게이트는 `repro_is_harness`로만 그 사실을 trace에 남긴다."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    finding = finding.model_copy(
        update={"repro_spec": finding.repro_spec.model_copy(update={"origin": ReproOrigin.HARNESS})}
    )
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E3
    assert result.predicates["repro_is_harness"] is True


def test_a_model_repro_spec_is_not_flagged_harness() -> None:
    """모델이 낸 명세(기본 `origin=model`)는 `repro_is_harness`가 거짓이고 등급은 그대로 E3다."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E3
    assert result.predicates["repro_is_harness"] is False


def test_no_repro_spec_is_not_flagged_harness() -> None:
    """명세가 없으면 `repro_is_harness`는 거짓이다(참칭 방지 — None을 harness로 읽지 않는다)."""
    finding = _finding(refs=["ev_static"], path=FULL_PATH, repro=False)

    result = gate.evaluate(finding, [_evidence("ev_static", EvidenceKind.STATIC)])

    assert result.predicates["repro_is_harness"] is False


def test_upheld_without_repro_spec_stays_e2() -> None:
    """E3의 조건 자체가 "재현 명세 존재"다 (architecture.md §4)."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E2
    assert not result.predicates["p3"]


def test_upheld_with_repro_but_no_path_is_not_e3() -> None:
    """경로 없는 upheld+repro는 E3가 아니다 (D65).

    E3은 `validated`로 승격되는데 `validated`는 스키마가 source→sink 경로를 요구한다
    (`finding.py`). 경로 없이 E3를 주면 그 전이가 `ValidationError`로 실서빙 run을 7단계에서
    죽인다 — `run_20260904_0001`이 그렇게 끝났다. `p3`가 `path_ok`를 포함하므로, 경로가 빈
    이 Finding은 E2(경로 없어 실패)를 지나 E1로 떨어진다.
    """
    finding = _finding(refs=["ev_static"], path=[], repro=True)
    evidences = [_evidence("ev_static", EvidenceKind.STATIC)]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E1
    assert not result.predicates["p3"]
    assert not result.predicates["path_ok"]
    assert result.predicates["upheld"] and result.predicates["has_repro_spec"]


def test_upheld_with_repro_and_path_but_no_graph_is_not_e3() -> None:
    """경로가 있어도 그래프 근거가 없으면 E3가 아니다 — E1에서 E3로 건너뛰지 못한다 (#154).

    6단계에서 E1으로 보류된 Finding도 상태가 `hypothesized`라 7단계에 올라온다(러너는 등급이
    아니라 상태로 고른다). `p3`가 E2의 조건을 안 보면 거기서 반박 실패 하나로 E3가 돼
    `validated`로 간다. 경로는 이어졌으므로 E1↔E2 경계(`ambiguous`)이고 E1으로 남는다.
    """
    finding = _finding(refs=["ev_static"], path=FULL_PATH, repro=True)
    evidences = [_evidence("ev_static", EvidenceKind.STATIC)]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E1
    assert not result.predicates["p3"]
    assert result.predicates["path_ok"] and not result.predicates["graph_ok"]
    assert result.predicates["upheld"] and result.predicates["has_repro_spec"]
    assert result.ambiguous


def test_upheld_with_repro_but_blocking_missing_is_not_e3() -> None:
    """blocking 근거 요청이 남아 있으면 반박 실패·재현 명세가 있어도 E3가 아니다 (#154).

    `blocking=True`는 "이 근거 없이는 Evidence Gate를 통과시키지 않는다"다(`MissingEvidence`).
    6단계에서 그것이 E2를 막는데 7단계에서 E3를 열어 주면 그 약속이 한 단계 만에 풀린다.
    """
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(
        finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)], [BLOCKING]
    )

    assert result.grade is EvidenceGrade.E1
    assert not result.predicates["p3"]
    assert not result.predicates["unblocked"]
    assert result.predicates["upheld"] and result.predicates["has_repro_spec"]
    assert result.ambiguous


# E3의 `upheld` 축이 가질 수 있는 판정 모양 여섯. **근거 없는 `rebut`을 넣는 것이 요점이다** -
# 전에는 이
# 모양이 `uphold`로 정규화돼 `upheld`가 참이 됐고, 그래서 E3의 다섯째 조건(반박 실패)을 **반박을
# 못 한
# Critic**이 채웠다(ADR 0015). 열거는 손으로 적는다 - `gate.normalize`를 되부르면 같이 틀린다.
_VERDICT_SHAPES = (
    ("uphold", Verdict.UPHOLD, {"grounded": True}),
    ("need_evidence", Verdict.NEED_EVIDENCE, {"grounded": True}),
    ("rebut_with_a_cited_id", Verdict.REBUT, {"grounded": True}),
    ("rebut_with_vocabulary_axes", Verdict.REBUT, {"grounded": False, "axes": ("guard",)}),
    ("rebut_without_id_or_axis", Verdict.REBUT, {"grounded": False}),
    (
        "rebut_with_an_axis_outside_the_vocabulary",
        Verdict.REBUT,
        {"grounded": False, "axes": ("guard-check",)},
    ),
)


def test_e3_requires_every_e2_condition_over_all_combinations() -> None:
    """E3 <=> E2의 세 조건 + 반박 실패 + 재현 명세 - 다섯 축의 조합 전부 (#154).

    위 두 테스트는 축을 하나씩 핀으로 박는다. 이것은 성질 자체를 단언한다 - 어느 조합에서도
    E2의 조건 하나라도 빠진 채 E3가 나오지 않고, 다섯이 다 서면 E3다(좁히기만 한 것도 아니다).
    정적 근거는 늘 둬서 `p1`이 서고 런타임 근거는 안 둬서 `p4`는 안 선다 - E4는 이 성질 밖이다.

    ⏵ 2026-10-13 (ADR 0015) - 판정 축이 둘에서 **여섯 모양**이 됐다. 전에는
    `uphold`/`need_evidence`뿐이라
    근거 없는 `rebut`이 `upheld`를 세우는 길이 이 성질 검사에 **안 보였다.** 이제 반박 모양 넷이
    들어와
    「`upheld` 축은 판정이 `uphold`일 때만 참」을 같이 문다 - 근거 없는 `rebut`이 E3를 채우면
    여기서 빨개진다.
    """
    static = _evidence("ev_static", EvidenceKind.STATIC)
    graph = _evidence("ev_graph", EvidenceKind.GRAPH)
    for has_path, has_graph, unblocked, repro in product((False, True), repeat=4):
        for shape, verdict, kwargs in _VERDICT_SHAPES:
            evidences = [static, graph] if has_graph else [static]
            finding = _finding(
                refs=[ev.evidence_id for ev in evidences],
                path=FULL_PATH if has_path else None,
                repro=repro,
            )

            result = gate.evaluate(
                finding,
                evidences,
                [_decision(verdict, **kwargs)],
                [] if unblocked else [BLOCKING],
            )

            axes = (has_path, has_graph, unblocked, shape == "uphold", repro)
            assert (result.grade is EvidenceGrade.E3) == all(axes), (shape, axes)


# `prompts/critic/v7/prefix.md:193-202`가 적는 열 값. **손으로 다시 적었다** - `RebuttalAxis`를
# 순회하면 enum이
# 줄어들 때 검사도 같이 줄어 변이가 산다. 프롬프트와의 대조는 `test_schemas.py`가 파일을 읽어 한다.
VOCABULARY = (
    "guard",
    "reachability",
    "aliasing",
    "lifetime",
    "arithmetic",
    "compile_flag",
    "input_reachability",
    "source",
    "propagate",
    "sink",
)


@pytest.mark.parametrize("axis", VOCABULARY)
def test_a_rebut_with_a_vocabulary_axis_is_grounded_without_any_id(axis: str) -> None:
    """v7은 rebut의 `evidence_refs`를 비운다 - 어휘 안의 축이 근거다 (ADR 0015)."""
    decision = _decision(Verdict.REBUT, grounded=False, axes=(axis,))

    assert decision.is_grounded
    assert gate.normalize(decision) is Verdict.REBUT
    assert not gate.was_normalized(decision)


def test_a_rebut_with_several_vocabulary_axes_is_grounded() -> None:
    decision = _decision(Verdict.REBUT, grounded=False, axes=("guard", "sink", "guard"))

    assert gate.normalize(decision) is Verdict.REBUT


def test_a_rebut_with_neither_an_id_nor_an_axis_becomes_need_evidence() -> None:
    """근거 없는 반박은 반박이 아니다 (agent-contracts.md §2.3). **`uphold`가 아니다** - 반박을 못
    한
    Critic이 Finding을 승격시키면 안 된다 (ADR 0015)."""
    decision = _decision(Verdict.REBUT, grounded=False)

    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE
    assert gate.was_normalized(decision)
    assert decision.ungrounded_reason == "empty_axis"
    assert decision.invalid_axes == []


# 어휘 밖 모양. 앞 넷은 Critic 목소리가 예측한 변형이고(대소문자 · 접미 · 합침 · 장식) 나머지는
# 어휘가
# 아닌 것이다. **실제로 나타날 수 있는 형태로 골랐다** - 모델이 영어 어휘를
# 변형하거나(`guard-check`) 한국어로
# 쓰거나(`무근거`) 한 필드에 둘을 합치는(`guard,reachability`) 것은 자유 문자열 필드의 정상 실패다.
@pytest.mark.parametrize(
    "axis",
    ["GUARD", "Guard", "guard-check", "guard,reachability", "`guard`", "무근거", "guards"],
)
def test_a_rebut_with_an_axis_outside_the_vocabulary_becomes_need_evidence(axis: str) -> None:
    decision = _decision(Verdict.REBUT, grounded=False, axes=(axis,))

    assert not decision.is_grounded
    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE
    assert gate.was_normalized(decision)
    assert decision.ungrounded_reason == "invalid_axis"
    assert decision.invalid_axes == [axis]


def test_one_bad_axis_among_good_ones_spoils_the_rebut() -> None:
    """`every`다 - 하나라도 어휘 밖이면 그 반박은 근거가 아니다. 어느 축이 무효인지는 값이
    싣는다."""
    decision = _decision(Verdict.REBUT, grounded=False, axes=("guard", "무근거", "sink"))

    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE
    assert decision.invalid_axes == ["무근거"]


def test_surrounding_whitespace_on_an_axis_is_tolerated() -> None:
    """`from_text`는 `strip()`만 한다 (`Verdict.from_text`처럼). `"guard "`는 어휘 안이다.

    앞뒤 공백은 의미가 아니라 직렬화 잡음이다(ADR 0015 「허용 정규화는 strip() 하나」).
    공백을 어휘 밖으로 치면 「어휘 변형으로 대량 need_evidence」를 공백이 만든다.
    대소문자·장식·구분자는 어휘 밖이다 - 아래 음성 핀이 그것을 든다.
    """
    decision = _decision(Verdict.REBUT, grounded=False, axes=(" guard ", "sink\t"))

    assert decision.is_grounded
    assert gate.normalize(decision) is Verdict.REBUT


@pytest.mark.parametrize("axes", [(), ("무근거",), ("guard",)])
def test_a_cited_id_grounds_a_rebut_whatever_the_axes_say(axes: tuple[str, ...]) -> None:
    """v6의 길 - 서빙 중인 v6의 rebut은 등록된 ID를 인용한다. `or evidence_refs`를 유지했다."""
    decision = _decision(Verdict.REBUT, grounded=True, axes=axes)

    assert decision.is_grounded
    assert decision.ungrounded_reason is None
    assert gate.normalize(decision) is Verdict.REBUT


# --- v8 이상: 근거는 반박 축뿐이다 (ADR 0015 ⏵ 2026-10-15, @danielcho02 승인) ---


@pytest.mark.parametrize("version", ["v6", "v7"])
def test_an_id_only_rebut_of_an_older_prompt_version_stays_grounded(version: str) -> None:
    """v6·v7은 종전 그대로다 - ID만 든 `rebut`은 근거 있는 반박이고 정규화되지 않는다."""
    decision = _decision(Verdict.REBUT, grounded=True, version=version)

    assert decision.is_grounded
    assert gate.normalize(decision) is Verdict.REBUT
    assert not gate.was_normalized(decision)


def test_an_id_only_rebut_of_v8_is_normalized_with_its_own_reason() -> None:
    """v8은 `rebut`에 Evidence ID를 금지한다 - ID만 든 `rebut`은 근거가 아니라 규약 위반이다.
    사유는 `empty_axis`와 가르는 `rebut_ids_only`다 (`critic_normalized`의 `reason`이
    그대로 쓴다)."""
    decision = _decision(Verdict.REBUT, grounded=True, version="v8")

    assert not decision.is_grounded
    assert decision.ungrounded_reason == "rebut_ids_only"
    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE
    assert gate.was_normalized(decision)


def test_a_v8_rebut_with_a_vocabulary_axis_stands_with_or_without_an_id() -> None:
    for grounded in (False, True):
        decision = _decision(Verdict.REBUT, grounded=grounded, axes=("guard",), version="v8")

        assert decision.is_grounded and decision.ungrounded_reason is None
        assert gate.normalize(decision) is Verdict.REBUT
        assert not gate.was_normalized(decision)


def test_a_v8_rebut_with_an_axis_outside_the_vocabulary_is_not_saved_by_an_id() -> None:
    """ID가 있어도 v8에서는 축이 어휘 밖이면 근거가 없다 - 사유는 `invalid_axis`다."""
    decision = _decision(Verdict.REBUT, grounded=True, axes=("guard-check",), version="v8")

    assert decision.ungrounded_reason == "invalid_axis"
    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE


def test_a_v8_id_only_rebut_does_not_raise_the_gate_to_e3() -> None:
    """정규화된 `need_evidence`는 `upheld`를 세우지 않는다 - 게이트 `p3`이 안 선다."""
    static = _evidence("ev_static", EvidenceKind.STATIC)
    graph = _evidence("ev_graph", EvidenceKind.GRAPH)
    finding = _finding(refs=[static.evidence_id, graph.evidence_id], path=FULL_PATH, repro=True)

    result = gate.evaluate(
        finding, [static, graph], [_decision(Verdict.REBUT, grounded=True, version="v8")]
    )

    assert result.predicates["upheld"] is False


@pytest.mark.parametrize("verdict", [Verdict.UPHOLD, Verdict.NEED_EVIDENCE])
@pytest.mark.parametrize("grounded", [True, False])
def test_uphold_and_need_evidence_of_v8_are_never_touched(verdict: Verdict, grounded: bool) -> None:
    decision = _decision(verdict, grounded=grounded, version="v8")

    assert gate.normalize(decision) is verdict
    assert not gate.was_normalized(decision)


@pytest.mark.parametrize("verdict", [Verdict.UPHOLD, Verdict.NEED_EVIDENCE])
@pytest.mark.parametrize("grounded", [True, False])
@pytest.mark.parametrize("axes", [(), ("guard",), ("무근거",)])
def test_uphold_and_need_evidence_are_never_touched(
    verdict: Verdict, grounded: bool, axes: tuple[str, ...]
) -> None:
    """정규화는 `rebut`만 건드린다. 근거가 있든 없든, 축이 어떻든 두 판정은 그대로다."""
    decision = _decision(verdict, grounded=grounded, axes=axes)

    assert gate.normalize(decision) is verdict
    assert not gate.was_normalized(decision)


def test_a_normalized_rebut_is_not_upheld_for_the_gate() -> None:
    """근거 없는 `rebut`이 `upheld`를 세우지 않는다 - 게이트가 보는 것은 정규화 뒤 값이다."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.REBUT, grounded=False)])

    assert not result.predicates["upheld"]
    assert result.grade is EvidenceGrade.E2


def _critic_route(
    policy: FixedThresholdPolicy, verdict: Verdict, grade: EvidenceGrade, *, has_spec: bool = True
):  # noqa: ANN202
    """`nodes/critic.run`이 정책에 넘기는 신호 그대로 - 정규화된 `verdict`와 그 판정으로 매긴
    등급."""
    return policy.decide(
        RoutingRequest(
            point=DecisionPoint.CRITIC_VERDICT,
            stage=Stage.CRITIC,
            subject_kind="finding",
            subject_id="find_0001",
            signals={
                "verdict": verdict.value,
                "grade": grade.value,
                "has_repro_spec": has_spec,
            },
        )
    )


def test_a_v7_shaped_rebut_no_longer_reaches_validated() -> None:
    """2026-10-10 PR #588 적대적 리뷰의 사슬을 되만든다 (ADR 0015).

    사슬: v7 모양의 `rebut`(`evidence_refs=[]`) -> `gate.normalize` -> `upheld` -> `p3` -> E3 ->
    정책 `validated`(사유 `critic_upheld`). **실제 노드 순서 그대로** 돈다: 게이트가 판정으로 등급을
    매기고, 정책이 정규화된 `verdict`와 그 등급을 읽는다 (`nodes/critic.run`).

    **이 사슬이 `validated`를 낼 수 있다는 것도 같이 문다** - 같은 Finding·Evidence에 `uphold`를
    주면
    `validated`다. 그 기준선이 없으면 아래 단언은 입력이 `validated`에 못 닿아서 통과한 것일 수
    있다.
    """
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]
    policy = FixedThresholdPolicy(load_config("dev_fake").thresholds)

    def route(decision: Decision):  # noqa: ANN202
        grade = gate.evaluate(finding, evidences, [decision]).grade
        return _critic_route(policy, gate.normalize(decision), grade)

    baseline = route(_decision(Verdict.UPHOLD, grounded=True))
    assert baseline.to_state is FindingState.VALIDATED
    assert baseline.reason_code is ReasonCode.CRITIC_UPHELD

    v7_rebut_without_axis = route(_decision(Verdict.REBUT, grounded=False))
    assert v7_rebut_without_axis.to_state is FindingState.QUEUED
    assert v7_rebut_without_axis.reason_code is ReasonCode.CRITIC_NEED_EVIDENCE

    v7_rebut_with_a_bad_axis = route(_decision(Verdict.REBUT, grounded=False, axes=("무근거",)))
    assert v7_rebut_with_a_bad_axis.to_state is FindingState.QUEUED
    assert v7_rebut_with_a_bad_axis.reason_code is ReasonCode.CRITIC_NEED_EVIDENCE

    # 어휘 안의 축을 든 v7 모양 rebut은 진짜 반박이다 - 역시 `validated`가 아니고 사유가 다르다.
    v7_rebut = route(_decision(Verdict.REBUT, grounded=False, axes=("guard",)))
    assert v7_rebut.to_state is FindingState.QUEUED
    assert v7_rebut.reason_code is ReasonCode.CRITIC_REBUTTED


def test_a_normalized_need_evidence_is_routed_like_one_the_model_emitted() -> None:
    """정규화된 `need_evidence`는 모델이 낸 것과 **같은 값**이라 정책이 둘을 가르지 못한다 (ADR
    0015)."""
    policy = FixedThresholdPolicy(load_config("dev_fake").thresholds)

    normalized = gate.normalize(_decision(Verdict.REBUT, grounded=False))

    assert _critic_route(policy, normalized, EvidenceGrade.E3) == _critic_route(
        policy, Verdict.NEED_EVIDENCE, EvidenceGrade.E3
    )


def test_grounded_rebut_is_not_normalized() -> None:
    decision = _decision(Verdict.REBUT, grounded=True)

    assert gate.normalize(decision) is Verdict.REBUT
    assert not gate.was_normalized(decision)


def test_need_evidence_is_not_a_failed_rebuttal() -> None:
    decision = _decision(Verdict.NEED_EVIDENCE, grounded=False)

    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE
    assert not gate.was_normalized(decision)


@pytest.mark.parametrize("exit_code", CRASH_EXIT_CODES)
@pytest.mark.parametrize("kind", RUNTIME_KINDS)
def test_runtime_evidence_is_e4(kind: EvidenceKind, exit_code: int) -> None:
    """런타임 Evidence가 **0도 미측정도 아닌** 종료 코드를 들면 E4다 (#232).

    양성 핀이다 — 아래 음성 핀 둘과 한 쌍이다. 한쪽만 있으면 `p4`를 항상 거짓으로 만드는
    변이(또는 항상 참인 원래 식)가 초록으로 남는다. 종료 코드가 비-0이라는 것은 **크래시의
    대용 지표**이고 크래시했다는 관측이 아니다 — 그 한계는 `p4`가
    배너 관측(`Evidence.crashed`, 기록뿐이다)을 안 읽고 종료 코드만 읽는다는 것이다.
    """
    finding = _finding(refs=["ev_run"])
    evidences = [_evidence("ev_run", kind, exit_code=exit_code)]

    result = gate.evaluate(finding, evidences)

    assert result.predicates["p4"]
    assert result.grade is EvidenceGrade.E4


def _e3_finding_with(runtime: Evidence) -> tuple[Finding, list[Evidence]]:
    """8단계가 게이트를 다시 돌릴 때의 입력 모양 — E3까지 올라온 Finding과 그 근거 + 런타임 근거."""
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
        runtime,
    ]
    finding = _finding(refs=[ev.evidence_id for ev in evidences], path=FULL_PATH, repro=True)
    return finding, evidences


@pytest.mark.parametrize("kind", RUNTIME_KINDS)
def test_a_clean_exit_does_not_lift_e3_to_e4(kind: EvidenceKind) -> None:
    """도구가 떴고(`ok=True`) 종료 코드가 0이면 E4가 아니다 — E3에 남는다 (#232).

    `ok=True`는 「떴다」이지 「크래시했다」가 아니다(`container_tools.judge()`는 exit 0도
    `ok=True`로 올린다). 그 건이 `proven`이 되면 크래시 0건인 run이 재현 완료로 기록된다. 등급이
    E0이 아니라 E3에 남는 것도 문다 — 깨끗한 종료가 근거를 지우는 것은 아니다.
    """
    finding, evidences = _e3_finding_with(_evidence("ev_run", kind, exit_code=0))

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert not result.predicates["p4"]
    assert result.grade is EvidenceGrade.E3


@pytest.mark.parametrize("kind", RUNTIME_KINDS)
def test_an_unmeasured_exit_code_is_not_a_crash(kind: EvidenceKind) -> None:
    """`exit_code=None`은 **안 쟀다**이고 안 잰 것을 크래시로 읽지 않는다 (#232, CLAUDE.md §0).

    ⚠ **`exit_code != 0`으로 쓰면 이 검사가 빨개진다.** `None != 0`은 참이라 미측정이 E4가
    되는데, 위 두 검사(`test_runtime_evidence_is_e4`·`test_a_clean_exit_does_not_lift_e3_to_e4`)는
    종료 코드를 항상 정수로 넣어 **그 변이에서도 초록이다**(측정 — 이 검사의 세 인스턴스와 아래
    전수 검사만 빨개진다). 프로덕션은 항상 정수를 채우므로(`Evidence(`를 만드는 곳이
    `fake_tools`·`container_tools` 둘이고 둘 다 정수를 넘긴다) 실동작 차이는 `_evidence()`처럼
    코드를 안 넣는 테스트 픽스처에서만 난다.
    """
    finding, evidences = _e3_finding_with(_evidence("ev_run", kind, exit_code=None))

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert not result.predicates["p4"]
    assert result.grade is EvidenceGrade.E3


def test_p4_needs_a_usable_runtime_evidence_with_a_crash_exit_over_all_combinations() -> None:
    """p4 ⇔ 런타임 종류 ∧ `ok` ∧ 종료 코드가 0도 None도 아님 — 8 × 2 × 6 = 96조합 전부 (#232).

    종류 8 × ok 2 × 종료 코드 6(None·0·비-0 넷). 위 핀들은 축을 하나씩 박는다. 이것은 성질
    자체를 단언한다 — 어느 종류에서도, 실패한 기록이든 미측정이든 깨끗한 종료든 셋 중 하나라도
    빠진 채 E4가 나오지 않고, 셋이 다 서면 E4다(좁히기만 한 것도 아니다). 비-런타임 종류
    (`STATIC`·`BUILD`·`GRAPH`·`FUZZ_COVERAGE`·`DIFF`)가 비-0 종료 코드를 들어도 E4가 아닌 것도
    여기서 물린다.
    """
    for kind, ok, exit_code in product(EvidenceKind, (True, False), (None, 0, *CRASH_EXIT_CODES)):
        evidences = [_evidence("ev_run", kind, ok=ok, exit_code=exit_code)]

        result = gate.evaluate(_finding(refs=["ev_run"]), evidences)

        crashed = kind in RUNTIME_KINDS and ok and exit_code is not None and exit_code != 0
        assert result.predicates["p4"] is crashed, (kind, ok, exit_code)
        assert (result.grade is EvidenceGrade.E4) is crashed, (kind, ok, exit_code)


@pytest.mark.parametrize(
    ("last_exit_code", "is_e4"),
    [(1, True), (0, False)],
    ids=["crash-exit", "clean-exit"],
)
def test_what_judge_returns_for_asan_decides_e4_through_the_gate(
    last_exit_code: int, is_e4: bool
) -> None:
    """`judge()`가 **실제로 내는** 튜플을 `Evidence`로 감싸 게이트에 넣는다 (#232).

    `judge()`는 크래시도 깨끗한 종료도 `ok=True`로 올리고, 둘을 가르는 것은 게이트의 종료 코드
    조건이다. 두 끝을 따로 무는 검사는 있어도 이어서 무는 검사는 이것뿐이다 — 한쪽이 바뀌면
    (예: `judge()`가 런타임 도구의 `exit_code`를 `None`으로 접으면) 개별 검사는 초록인 채
    연결만 끊긴다.
    """
    ok, exit_code, error = judge("asan", [Step(0, "", ""), Step(last_exit_code, "", "")], 2)
    assert (ok, error) == (True, None)  # 전제 — 둘 다 「떴다」다
    evidences = [_evidence("ev_asan", EvidenceKind.SANITIZER, ok=ok, exit_code=exit_code)]

    result = gate.evaluate(_finding(refs=["ev_asan"]), evidences)

    assert (result.grade is EvidenceGrade.E4) is is_e4


def test_grade_comparison_is_ordered() -> None:
    assert gate.at_least(EvidenceGrade.E3, EvidenceGrade.E2)
    assert gate.at_least(EvidenceGrade.E2, EvidenceGrade.E2)
    assert not gate.at_least(EvidenceGrade.E1, EvidenceGrade.E2)


def test_evidences_for_follows_ref_order() -> None:
    """참조 순서가 곧 판정 순서다 — 순서가 흔들리면 재현성 비교가 깨진다."""
    finding = _finding(refs=["ev_b", "ev_a"])
    pool = [
        _evidence("ev_a", EvidenceKind.STATIC),
        _evidence("ev_b", EvidenceKind.GRAPH),
        _evidence("ev_other", EvidenceKind.BUILD),
    ]

    assert [e.evidence_id for e in gate.evidences_for(finding, pool)] == ["ev_b", "ev_a"]
