"""스키마 계약 테스트.

두 가지를 고정한다.

1. **문서 예시가 실제로 파싱된다.** agent-contracts.md §3의 JSON이 스키마와 어긋나면
   여러 곳이 서로 다른 것을 만든다.
2. **문서가 요구한 불변식이 실제로 강제된다.** engineering-guide.md §8.7: 비결정적
   로직(임계값 판정, 상태 전이 조건)에는 반드시 테스트를 남긴다.

모든 값은 문서와 같은 합성 예시다.
"""

import json
import pathlib
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from src.interfaces import ObjectKind, SandboxSpec, ToolArgs, Usage, tool_runner
from src.schemas import (
    ID_PREFIXES,
    Artifact,
    ArtifactKind,
    Budget,
    Candidate,
    CapsuleSection,
    ContextCapsule,
    Decision,
    Evidence,
    EvidenceKind,
    Finding,
    FindingState,
    Location,
    MissingEvidence,
    PathStep,
    PathStepKind,
    ProducedBy,
    RebuttalAxis,
    ReproSpec,
    Role,
    RunRecord,
    Sampling,
    Verdict,
)
from src.schemas.candidate import scout_member_of
from src.schemas.common import PLACEHOLDER_DIGEST
from src.schemas.enums import ScoutSignal
from src.schemas.scout_response import (
    AnalysisCondition,
    AnalysisSummary,
    RequestedContext,
    signal_of,
)
from src.tokens import estimate_tokens

ZERO_DIGEST = PLACEHOLDER_DIGEST
ZERO_COMMIT = "0" * 40
AUDITOR = ProducedBy(role=Role.AUDITOR, model="qwen3.8-27b", prompt_version="v1")


def _finding(**overrides: object) -> Finding:
    """agent-contracts.md §3의 Finding 예시. `state` 등을 덮어써서 쓴다."""
    base: dict[str, object] = {
        "finding_id": "find_0007",
        "run_id": "run_20260901_0001",
        "candidate_id": "cand_0001",
        "title": "parse_header에서 길이 검증 없는 memcpy",
        "cwe": "CWE-787",
        "state": FindingState.HYPOTHESIZED,
        "confidence": 0.6,
        "path": [
            PathStep(step=PathStepKind.SOURCE, symbol="recv_request", note="네트워크 입력"),
            PathStep(step=PathStepKind.SINK, symbol="memcpy", note="고정 크기 스택 버퍼"),
        ],
        "evidence_refs": ["ev_static_0007"],
        "produced_by": AUDITOR,
    }
    return Finding(**(base | overrides))


def _run(**overrides: object) -> RunRecord:
    base: dict[str, object] = {
        "run_id": "run_20260901_0001",
        "condition": "B3prime@pipeline",
        "repo_commit": ZERO_COMMIT,
        "repo_dirty": False,
        "source_hash": ZERO_DIGEST,
        "model_checksums": {"auditor": ZERO_DIGEST, "critic": ZERO_DIGEST},
        "prompt_versions": {"scout": "v1", "auditor": "v1", "critic": "v1"},
        "tool_images": {"semgrep": ZERO_DIGEST, "fuzz": ZERO_DIGEST},
        "tool_runner": "docker",
        "device": "atom",
        "budget": Budget(input_tokens=4_000_000, output_tokens=400_000, gpu_seconds=7200),
        "thresholds": {"scout_threshold": 0.5},
        "seed": 20260901,
        "sampling": Sampling(temperature=0.2, top_p=0.95),
        "kb_snapshot": "kb-2026-08-30",
        "started_at": datetime(2026, 9, 1, 9, 0, 0, tzinfo=UTC),
    }
    return RunRecord(**(base | overrides))


# --- 문서 예시 파싱 (agent-contracts.md §3) ---------------------------------


def test_candidate_example_parses() -> None:
    candidate = Candidate(
        candidate_id="cand_0001",
        run_id="run_20260901_0001",
        artifact_id="art_a1b2c3",
        location=Location(
            file="src/parser/http.c", symbol="parse_header", line_start=120, line_end=188
        ),
        suspected_cwe=["CWE-787", "CWE-190"],
        score=0.72,
        reason_codes=["memcpy_unbounded", "changed_in_diff"],
        evidence_refs=["ev_static_0007"],
        state=FindingState.TRIAGED,
    )
    assert candidate.state is FindingState.TRIAGED


def test_evidence_example_parses_and_serializes_utc() -> None:
    evidence = Evidence(
        evidence_id="ev_san_0031",
        run_id="run_20260901_0001",
        kind=EvidenceKind.SANITIZER,
        source="asan",
        artifact_id="art_a1b2c3",
        location=Location(file="src/parser/http.c", line_start=174),
        payload_ref="artifacts/run_20260901_0001/asan/0031.log",
        digest=ZERO_DIGEST,
        observed_at=datetime(2026, 9, 1, 10, 22, 31, tzinfo=UTC),
    )
    # agent-contracts.md §3 말미: 날짜·시각은 UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`.
    assert evidence.model_dump(mode="json")["observed_at"] == "2026-09-01T10:22:31Z"


def test_decision_example_parses() -> None:
    decision = Decision(
        decision_id="dec_0004",
        run_id="run_20260901_0001",
        finding_id="find_0007",
        verdict=Verdict.UPHOLD,
        rebuttal_axis=["guard", "reachability"],
        rationale="상위 호출부에 길이 상한이 없음(src/net/server.c:88 확인)",
        evidence_refs=["ev_graph_0012"],
        produced_by=ProducedBy(
            role=Role.CRITIC, model="gpt-oss-20b", prompt_version="v1"
        ),
    )
    assert decision.is_grounded


def test_missing_evidence_example_parses() -> None:
    missing = MissingEvidence(
        missing_id="mev_0002",
        run_id="run_20260901_0001",
        finding_id="find_0007",
        need="recv_request 호출부에서 len 상한이 강제되는지",
        how_to_obtain="index.callers(recv_request) 확장 후 guard 검색",
        blocking=True,
    )
    assert missing.blocking


def test_run_record_example_parses() -> None:
    assert _run().started_at.tzinfo is not None


def test_artifact_parses() -> None:
    artifact = Artifact(
        artifact_id="art_a1b2c3",
        run_id="run_20260901_0001",
        kind=ArtifactKind.SOURCE,
        path="src/parser/http.c",
        digest=ZERO_DIGEST,
    )
    assert artifact.parent_id is None


# --- 기반 규약 --------------------------------------------------------------


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    location = Location(file="src/parser/http.c")
    with pytest.raises(ValidationError):
        location.file = "other.c"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Location(file="a.c", typo_field=1)  # type: ignore[call-arg]


def test_location_overlap_falls_back_to_file_match() -> None:
    """줄 정보가 없으면 파일 일치만으로 겹침 — 파일 단위 정적분석 신호를 잃지 않기 위해서다."""
    with_lines = Location(file="a.c", line_start=10, line_end=20)
    assert with_lines.overlaps(Location(file="a.c", line_start=15))
    assert not with_lines.overlaps(Location(file="a.c", line_start=21))
    assert with_lines.overlaps(Location(file="a.c"))
    assert not with_lines.overlaps(Location(file="b.c", line_start=15))


def test_cwe_pattern_is_enforced() -> None:
    with pytest.raises(ValidationError):
        _finding(cwe="787")


def test_transition_validates_where_model_copy_does_not() -> None:
    """`model_copy`가 검증을 우회한다는 사실을 고정한다.

    이 동작이 바뀌면 common.py의 경고와 `transition()`의 존재 이유가 사라진다.
    """
    draft = _finding()
    # pydantic v2의 model_copy는 검증하지 않는다 → 불변식을 깬 객체가 만들어진다.
    assert draft.model_copy(update={"state": FindingState.PROVEN}).repro_spec is None
    # transition()은 전체 재검증을 거치므로 같은 전이가 막힌다.
    with pytest.raises(ValidationError):
        draft.transition(state=FindingState.PROVEN)


# --- Evidence 불변식 (engineering-guide.md §8.3) ----------------------------


def test_failed_evidence_requires_error() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev_build_0001",
            run_id="run_20260901_0001",
            kind=EvidenceKind.BUILD,
            source="clang",
            artifact_id="art_a1b2c3",
            payload_ref="artifacts/build/0001.log",
            digest=ZERO_DIGEST,
            observed_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC),
            ok=False,
            exit_code=1,
        )


def test_successful_evidence_rejects_error() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev_build_0002",
            run_id="run_20260901_0001",
            kind=EvidenceKind.BUILD,
            source="clang",
            artifact_id="art_a1b2c3",
            payload_ref="artifacts/build/0002.log",
            digest=ZERO_DIGEST,
            observed_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC),
            error="컨테이너 기동 실패",
        )


# --- Finding 상태 불변식 (architecture.md §4) -------------------------------


def test_draft_states_need_no_repro_spec() -> None:
    assert _finding(state=FindingState.QUEUED).repro_spec is None


@pytest.mark.parametrize(
    "state", [FindingState.VALIDATED, FindingState.PROVEN, FindingState.REPORTED]
)
def test_promoted_states_require_repro_spec(state: FindingState) -> None:
    with pytest.raises(ValidationError):
        _finding(state=state)


def test_promoted_state_requires_evidence() -> None:
    spec = ReproSpec(steps=["./build.sh", "./poc"], expected_signal="ASAN heap-buffer-overflow")
    with pytest.raises(ValidationError):
        _finding(state=FindingState.VALIDATED, repro_spec=spec, evidence_refs=[])
    # 근거가 붙으면 통과한다.
    assert _finding(state=FindingState.VALIDATED, repro_spec=spec).state is FindingState.VALIDATED


def test_promoted_state_requires_path() -> None:
    """validated 이상은 source→sink 경로도 요구한다.

    게이트(`p3`의 `path_ok`)가 경로 없는 Finding을 E3에서 막지만 스키마도 같은 것을
    막는다 — 그 전이가 스키마에서 터진 것이 이슈 #154의 크래시였다. 게이트 회귀가
    이 갈래를 뚫어도 run이 죽지 않도록(그 대신 검증 단계에서 걸리도록) 층을 따로 고정한다.
    """
    spec = ReproSpec(steps=["./build.sh", "./poc"], expected_signal="ASAN heap-buffer-overflow")
    with pytest.raises(ValidationError, match="source→sink 경로"):
        _finding(state=FindingState.VALIDATED, repro_spec=spec, path=[])
    # 경로가 붙으면 통과한다.
    assert _finding(state=FindingState.VALIDATED, repro_spec=spec).state is FindingState.VALIDATED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 표준 어휘는 항등 (enum 어휘를 쓰는 모델 — devstral 등).
        ("uphold", Verdict.UPHOLD),
        ("rebut", Verdict.REBUT),
        ("need_evidence", Verdict.NEED_EVIDENCE),
        ("  Uphold  ", Verdict.UPHOLD),
        # 맨 앞 단어가 표준값이면 뒤에 사유가 붙어도 읽는다.
        ("uphold: no guard prevents the use-after-free", Verdict.UPHOLD),
        ("rebut - the path is guarded", Verdict.REBUT),
        ("rebutted", Verdict.REBUT),
        # 짧은 명백한 단독 동의어 (전체 문자열).
        ("Yes", Verdict.UPHOLD),
        ("vulnerable", Verdict.UPHOLD),
        ("unsafe", Verdict.UPHOLD),  # ⚠ 부분문자열 `safe`에 끌려 REBUT이 되면 안 된다
        ("no", Verdict.REBUT),
        ("safe", Verdict.REBUT),
        ("not vulnerable", Verdict.REBUT),
        ("unclear", Verdict.NEED_EVIDENCE),
        # 장식·라벨이 붙은 실제 LLM 출력 모양. 표준값이 **단어 하나로** 서 있으면 읽는다.
        ("**uphold**", Verdict.UPHOLD),
        ('"uphold"', Verdict.UPHOLD),
        ("verdict: uphold", Verdict.UPHOLD),
        ("`need_evidence`", Verdict.NEED_EVIDENCE),
        # ⚠ 단어 경계 없는 접두어 매칭이 **거부를 지지로** 읽던 넷 (#182 리뷰 H1 실측).
        # `startswith("uphold")`만 보면 아래 셋이 전부 UPHOLD였고, 마지막 것은 토큰 순서로만
        # REBUT이 됐다 — 같은 문장이 어순으로 판정이 갈리면 그것은 판정이 아니다.
        ("upholding is not warranted, this is a false positive", None),
        ("uphold would be wrong here; the guard holds, so I rebut", None),
        ("uphold or rebut", None),
        ("rebut or uphold", None),
        # 부정문 안의 표준값은 읽지 않는다 — 표준값이 **앞에 서야** 그 문장의 판정이다.
        ("I would not uphold this claim", None),
        # 미해석 — 문장형 자유 서술은 추측하지 않고 None (호출자가 need_evidence로 보낸다).
        # 이 넷은 예전 부분문자열 매퍼가 **REBUT으로 오분류**해 진짜 취약을 기각하던 것들이다.
        ("not safe", None),
        ("not a false positive", None),
        ("this is a genuine use-after-free vulnerability", None),
        ("requires additional evidence to confirm", None),
        ("asdf qwer", None),
        # ⚠ **동의어 층이 표준값 층과 같은 정규화를 거친다** (#182 재리뷰 H-A 실측).
        # 장식 제거는 표준값 셋에만 걸려 있었고 동의어는 **raw 문자열**을 봤다 — 그래서
        # `Yes`는 UPHOLD인데 `**Yes**`·`Yes.`·`"Yes"`가 전부 `None`이었다. 방향이 위 H1과
        # 반대다: `None` → `need_evidence` → 게이트 `p3`가 `upheld`를 요구 → **E3가 막혀
        # 진짜 취약이 `validated`에 못 간다.** 마침표 하나로 갈리면 그것은 판정이 아니다.
        ("**Yes**", Verdict.UPHOLD),
        ("Yes.", Verdict.UPHOLD),
        ('"Yes"', Verdict.UPHOLD),
        ("Yes!", Verdict.UPHOLD),
        ("yes,", Verdict.UPHOLD),
        ("unsafe.", Verdict.UPHOLD),
        ("not vulnerable.", Verdict.REBUT),
        ('"not vulnerable"', Verdict.REBUT),
        ("(safe)", Verdict.REBUT),
        ("`need evidence`", Verdict.NEED_EVIDENCE),
        # 목록 기호·강조·번호 매김도 장식이다 (#182 재리뷰 L-4). 모델이 판정을 항목으로 낸다.
        ("— uphold", Verdict.UPHOLD),
        ("• uphold", Verdict.UPHOLD),
        ("1. uphold", Verdict.UPHOLD),
        ("“uphold”", Verdict.UPHOLD),
        ("_uphold_", Verdict.UPHOLD),
        # 라벨은 **두 겹**이고 구분자는 `:`만이 아니다 (#182 재리뷰 M-2). 한 겹·공백 뒤
        # 콜론만 걷던 판에서는 `**verdict**: uphold`만 우연히 통과해 **마크다운 표기로
        # 판정이 갈렸다.** 라벨 자리에 표준값이 없을 때만 걷는다 — `uphold: no guard…`는
        # 그대로다(아래 M-1 항 참조).
        ("final verdict: uphold", Verdict.UPHOLD),
        ("verdict = uphold", Verdict.UPHOLD),
        ("verdict:uphold", Verdict.UPHOLD),
        ("**verdict**: uphold", Verdict.UPHOLD),
        ("verdict: not vulnerable", Verdict.REBUT),
        # ⚠ **CWE 이름은 verdict가 아니다** (#182 재리뷰 M-3). `use-after-free`를 UPHOLD로
        # 읽으면 Scout이 겨냥하는 CWE 계열(415/416 · 119/125/787/190 · 78/22/134) 중
        # **한 계열만 매핑돼 Critic 제거율이 대상 CWE에 따라 달라진다**(evaluation.md §2.3).
        # 근거도 n=5 중 1건뿐이었다. 빼면 이 응답은 `need_evidence`(보류)다 — 안전한 방향.
        ("use-after-free", None),
        ("use after free", None),
        ("`use-after-free`", None),
        # 표준값 둘은 **머리 단어가 표준값이어도** 읽지 않는다 (#182 재리뷰 M-1 — 유지).
        # 사람에겐 `rebut: …not an uphold case`가 안 모호하지만, 머리 단어를 택하는 규칙은
        # `uphold or rebut`을 UPHOLD로 만들어 **H1이 없앤 어순 의존을 되살린다.** 틀릴 때
        # `None`(→보류)은 안전한 방향이고 UPHOLD(→지지)는 아니다.
        ("rebut: this is not an uphold case", None),
    ],
)
def test_verdict_from_text_reads_only_explicit_verdicts(
    text: str, expected: "Verdict | None"
) -> None:
    """**맨 앞 단어가 표준값일 때**와 짧은 단독 동의어만 읽고, 자유 서술은 `None`으로 둔다.

    단어 경계가 요점이다 — `startswith(verdict.value)`만 보면 `upholding …false positive`가
    UPHOLD가 되고 `uphold or rebut`·`rebut or uphold`가 어순으로만 갈린다(#182 리뷰 H1).

    부분 문자열로 의미를 추측하지 않는다 — *"unsafe"*(전체)는 UPHOLD지만 *"not safe"*·
    *"not a false positive"*는 오분류 위험이라 `None`이고, 호출자가 `need_evidence`로 보낸다.
    미해석을 `uphold`(지지)로 소비하지 않는 것이 요점이다(진짜 취약 기각·거짓 validated 방지).
    """
    assert Verdict.from_text(text) is expected


_PROMPTS = pathlib.Path(__file__).resolve().parent.parent / "prompts" / "critic"


def _v7_axis_list() -> list[str]:
    """v7 판정 규칙(§5)이 적는 축 목록 - 백틱 축 이름으로 시작하는 목록 줄만. 앞 절의 `source`
    fails 줄은 안 센다."""
    text = (_PROMPTS / "v7" / "prefix.md").read_text(encoding="utf-8")
    section = text[text.index("### 5. Apply the verdict rules") :]
    section = section[: section.index("Other mismatches")]
    return [line.split("`")[1] for line in section.splitlines() if line.startswith("- `")]


def _v6_axis_list() -> list[str]:
    text = (_PROMPTS / "v6" / "prefix.md").read_text(encoding="utf-8")
    tail = text[text.index("Use only these prompt-level axis values:") :].split("\n\n")[1]
    return [part.strip() for part in tail.replace("\n", " ").split(",")]


def test_the_axis_vocabulary_matches_the_critic_prompts() -> None:
    """열 값의 원천은 프롬프트다 (ADR 0015) - v6·v7이 글자까지 같은 열 개를 들고 enum이 그것과 같다.

    프롬프트를 읽어 대조한다. enum을 손으로 지어내면 모든 `rebut`이 어휘 밖으로 읽혀
    `need_evidence`로
    간다 - 그 어긋남을 이 검사가 먼저 잡는다. 어휘를 바꾸는 프롬프트 판이 생기면 이 검사가 빨개진다.
    """
    v7, v6 = _v7_axis_list(), _v6_axis_list()

    assert len(v7) == len(v6) == 10
    assert set(v7) == set(v6) == {axis.value for axis in RebuttalAxis}
    assert len(RebuttalAxis) == 10


def test_every_axis_the_prompts_list_reads_back_as_itself() -> None:
    """양성: 프롬프트가 쓴 철자 그대로가 `from_text`로 읽힌다."""
    for text in _v7_axis_list() + _v6_axis_list():
        assert RebuttalAxis.from_text(text) is RebuttalAxis(text)


@pytest.mark.parametrize(
    "text",
    [
        "GUARD",
        "Guard",
        "guard-check",
        "guard_check",
        "guards",
        "guard,reachability",
        "guard reachability",
        "`guard`",
        '"guard"',
        "guard.",
        "input-reachability",
        "compile flag",
        "무근거",
        "",
        "   ",
    ],
)
def test_axis_from_text_never_guesses(text: str) -> None:
    """음성 핀 - 대소문자 접기·부분 문자열·장식 제거·구분자 정규화를 **하지 않는다.**

    고른 기준: 모델이 자유 문자열 필드에 낼 수 있는 어휘 변형이다(대소문자 · 접미 · 합침 · 장식 ·
    하이픈 ·
    한국어). 이 중 하나라도 읽히면 첫 v7 run의 무효 축 비율이 그만큼 가려진다.
    """
    assert RebuttalAxis.from_text(text) is None


@pytest.mark.parametrize("text", ["guard ", " guard", "\tguard\n"])
def test_axis_from_text_strips_surrounding_whitespace_only(text: str) -> None:
    assert RebuttalAxis.from_text(text) is RebuttalAxis.GUARD


def test_decision_grounding_is_ids_or_a_fully_valid_axis_list() -> None:
    """`is_grounded` = `bool(evidence_refs)` 또는 (축이 비어 있지 않고 전부 어휘 안)."""

    def decision(axes: list[str], refs: list[str]) -> Decision:
        return Decision(
            decision_id="dec_0004",
            run_id="run_20260901_0001",
            finding_id="find_0007",
            verdict=Verdict.REBUT,
            rebuttal_axis=axes,
            rationale="r",
            evidence_refs=refs,
            produced_by=ProducedBy(role=Role.CRITIC, model="m", prompt_version="v7"),
        )

    assert decision(["guard"], []).is_grounded
    assert decision(["guard", "sink"], []).is_grounded
    assert decision([], ["ev_0001"]).is_grounded
    assert decision(["무근거"], ["ev_0001"]).is_grounded
    assert not decision([], []).is_grounded
    assert not decision(["무근거"], []).is_grounded
    assert not decision(["guard", "무근거"], []).is_grounded
    assert decision([], []).ungrounded_reason == "empty_axis"
    assert decision(["GUARD"], []).ungrounded_reason == "invalid_axis"
    assert decision(["guard"], []).ungrounded_reason is None


def test_decision_draft_keeps_the_axis_list_free_text() -> None:
    """스키마는 enum을 강제하지 않는다 - grammar 마스크가 맞는 답을 덮는다 (gpt-oss-20b, PR #182).
    어휘 밖 축도 파싱은 성공하고 검증은 `is_grounded`가 한다."""
    from src.schemas.decision import DecisionDraft

    draft = DecisionDraft(verdict="rebut", rebuttal_axis=["guard-check"], rationale="r")

    assert draft.rebuttal_axis == ["guard-check"]
    axis_schema = DecisionDraft.model_json_schema()["properties"]["rebuttal_axis"]
    assert "enum" not in json.dumps(axis_schema)


def test_decision_draft_maps_unreadable_verdict_to_need_evidence() -> None:
    """읽히지 않는 verdict는 `need_evidence`(보류)이지 `uphold`(지지)가 아니다.

    자유 서술 verdict가 `uphold`로 소비되면 경로·근거 있는 Finding이 validated로 새어
    나간다(#182 리뷰 P1). `to_decision`이 그것을 막는다.
    """
    from src.schemas.common import ProducedBy
    from src.schemas.decision import DecisionDraft

    draft = DecisionDraft(verdict="I cannot rule out a use-after-free here", rationale="…")
    produced_by = ProducedBy(role=Role.CRITIC, model="gpt-oss-20b", prompt_version="v1")
    decision = draft.to_decision(
        decision_id="dec_0001", run_id="run_20260901_0001", finding_id="find_0001",
        produced_by=produced_by,
    )
    assert decision.verdict is Verdict.NEED_EVIDENCE
    assert decision.produced_by.model == "gpt-oss-20b"  # 하네스 주입, 모델 출력 아님
    assert not draft.verdict_interpreted()


def test_candidate_state_rejects_finding_only_values() -> None:
    """상태 열거형은 하나지만 객체별로 허용 값이 다르다(enums.FindingState docstring)."""
    with pytest.raises(ValidationError):
        Candidate(
            candidate_id="cand_0002",
            run_id="run_20260901_0001",
            artifact_id="art_a1b2c3",
            location=Location(file="src/parser/http.c"),
            score=0.5,
            state=FindingState.PROVEN,  # type: ignore[arg-type]
        )


# --- Scout v3 requested_context · conditions (D82 ③④) ---------------------


def test_requested_context_accepts_the_v3_object_and_rejects_a_string() -> None:
    """D82 ③ — v3 프롬프트가 내는 4키 객체가 스키마를 통과하고, 문자열은 거부된다.

    main에서는 반대였다 — `AnalysisSummary.requested_context: list[NonEmptyStr]`라
    객체가 거부되고 문자열만 통과했다(`scout_response.py:46`). 이 재현 테스트는 지금
    저장소에 없다 — `git grep -n requested_context -- tests`가 0건이다.
    """
    summary = AnalysisSummary(
        claim="strdup 결과를 free하지 않는다",
        requested_context=[
            {
                "kind": "function",
                "target": "strdup",
                "purpose": "정의 확인",
                "request_code": "missing_request:function:strdup:정의",
            }
        ],
    )
    assert summary.requested_context == [
        RequestedContext(
            kind="function",
            target="strdup",
            purpose="정의 확인",
            request_code="missing_request:function:strdup:정의",
        )
    ]
    with pytest.raises(ValidationError):
        AnalysisSummary(
            claim="x", requested_context=["missing_request:function:strdup:정의"]
        )


def test_requested_context_target_may_be_empty() -> None:
    """target이 빈 문자열이어도 통과한다 — `UnfilledReason.NO_TARGET`(no target was given)이
    resolver 쪽에서 그 값을 받아야 하기 때문이다(`kind`는 `Literal`로 묶지 않는다)."""
    context = RequestedContext(
        kind="context",
        target="",
        purpose="이 변수의 가능한 값",
        request_code="missing_request:context::가능한 값",
    )
    assert context.target == ""


def test_analysis_condition_accepts_an_optional_description() -> None:
    """D82 ④ — boundary v3가 허용하는 `description`을 스키마가 더 이상 거부하지 않는다.

    `condition`·`status`만으로도 여전히 유효하다 — 요구가 아니라 허용이다.
    """
    AnalysisCondition(
        condition="길이 검증 없음", status="confirmed", description="strcpy 호출 앞에 가드가 없다"
    )
    AnalysisCondition(condition="길이 검증 없음", status="confirmed")


@pytest.mark.parametrize(
    ("reason_codes", "expected"),
    [
        (["triage_review"], ScoutSignal.REVIEW),
        (["triage_reject"], ScoutSignal.REJECT),
        # 명세 §4.2 원문 예. 접두만 보고 나머지(`missing_request:function:doit`)는 안 본다.
        (["triage_need_evidence:missing_request:function:doit"], ScoutSignal.NEED_EVIDENCE),
        ([], None),
        (["unknown_reason"], None),
    ],
)
def test_signal_of_reads_the_first_reason_codes_prefix(
    reason_codes: list[str], expected: ScoutSignal | None
) -> None:
    """`signal_of`가 `reason_codes[0]`의 접두를 본다. 접두 비교는 명세 밖이다(함수 docstring)."""
    assert signal_of(reason_codes) is expected


# --- Scout 풀 멤버 태깅 (ADR 0010 §3) --------------------------------------


def _candidate(**overrides: object) -> Candidate:
    """4단계 Scout 출력 하나. `scout_member`만 바꿔 가며 쓴다."""
    base: dict[str, object] = {
        "candidate_id": "cand_0003",
        "run_id": "run_20260901_0001",
        "artifact_id": "art_a1b2c3",
        "location": Location(file="src/parser/http.c", symbol="parse_header"),
        "score": 0.61,
        "state": FindingState.CANDIDATE,
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def test_scout_member_is_absent_when_the_profile_has_no_pool() -> None:
    """풀이 없는 프로필(`roles:`에 `scout` 하나)의 후보는 멤버를 **주장하지 않는다**.

    여기서의 `None`은 CLAUDE.md §0의 미실측 표시가 아니다 — "이 run에 풀이 없었다"는
    관측이다. 풀 프로필로 돈 후보에는 하네스가 반드시 값을 stamp하므로 `None`이 남지
    않고, 그래서 두 뜻이 한 값에 겹치지 않는다. 겹치게 두면 단일 scout run의 후보가
    공동오답 행렬(ADR 0010 §3)의 어느 칸에 들어갈지 판단할 근거가 사라진다.
    """
    assert _candidate().scout_member is None


def test_scout_member_refuses_the_single_scout_role() -> None:
    """`"scout"`은 풀 멤버 이름이 아니다.

    담기면 행렬에 **없는 자리**가 생긴다 — 세 멤버의 겹침을 세는 표에 네 번째 열이
    끼고, 단일 scout 프로필로 돈 후보가 풀 멤버의 오답으로 집계된다. 그 표가
    「배분을 옮기는 유일한 근거」(docs/research/2026-09-08-roster.md §7)라 그 한 열이
    모델을 다른 자리로 옮기는 판단까지 끌고 간다.
    """
    with pytest.raises(ValidationError):
        _candidate(scout_member="scout")


def test_scout_member_accepts_the_pool_member_role_key() -> None:
    """풀 멤버 역할 키(`Role.value`)를 그대로 담는다.

    역할 키인 것이 요점이다 — `RunRecord.model_checksums`·`prompt_versions`가 같은
    키를 쓰므로, 행렬의 한 칸에서 "어느 모델이 어느 checksum으로 무엇을 놓쳤나"까지
    조인 없이 따라간다(ADR 0010 §3 "역할 키로 이미 동작한다").
    """
    assert _candidate(scout_member="scout_boundary").scout_member == "scout_boundary"


def test_scout_member_of_tags_pool_roles_and_nothing_else() -> None:
    """stamp가 쓰는 판별 규칙 자체를 고정한다.

    풀 멤버는 `scout_` 접두 역할이고 단일 `scout`은 아니다(ADR 0010 §3). 규칙이
    느슨해져 `auditor`까지 통과하면, 5단계가 만든 것이 4단계 멤버의 발화로 기록되어
    공동오답 행렬이 **Scout이 낸 적 없는 오답**을 세게 된다.
    """
    assert scout_member_of("scout_memory") == "scout_memory"
    assert scout_member_of("scout_boundary") == "scout_boundary"
    assert scout_member_of("scout_injection") == "scout_injection"
    assert scout_member_of("scout") is None
    assert scout_member_of("auditor") is None
    assert scout_member_of("single") is None


# --- ContextCapsule 불변식 (architecture.md §3 3단계) ----------------------


def _capsule(**overrides: object) -> ContextCapsule:
    base: dict[str, object] = {
        "capsule_id": "cap_0001",
        "run_id": "run_20260901_0001",
        "target": Location(file="src/parser/http.c", symbol="parse_header"),
        "level": "expanded",
        "budget_tokens": 24000,
        "sections": [
            CapsuleSection(kind="target_function", tokens=900),
            CapsuleSection(kind="callers", tokens=1400, items=3),
        ],
        "cache_key": ZERO_DIGEST,
    }
    return ContextCapsule(**(base | overrides))


def test_capsule_sections_must_fit_budget() -> None:
    assert _capsule().used_tokens == 2300
    with pytest.raises(ValidationError):
        _capsule(budget_tokens=1000)


def test_section_tokens_cannot_drift_from_its_body() -> None:
    """본문을 담은 섹션은 `tokens`가 그 본문에서 계산한 값이어야 한다 (W1).

    **이 검증기가 이 저장소가 반복해서 잡아온 결함 부류를 구조적으로 없앤다** — 숫자와
    내용이 각각 계산되어 조용히 갈라지는 것(`fit_names`가 개수만 깎던 것, 확장 경로의
    `items=1`, `gpu_seconds`가 CPU 시간을 세던 것). 한 객체 안에서 서로를 강제하면
    갈라질 자리가 없다.
    """
    body = ["main main.c:12", "memcpy parser.c:20"]
    honest = sum(estimate_tokens(line) for line in body)

    assert CapsuleSection(kind="callers", tokens=honest, items=2, body=body).body == body
    # 줄은 그대로 두고 토큰만 깎는 경로(`min()`)가 여기서 죽는다.
    with pytest.raises(ValidationError, match="본문에서 계산한 값"):
        CapsuleSection(kind="callers", tokens=honest - 1, items=2, body=body)
    with pytest.raises(ValidationError, match="본문 줄 수"):
        CapsuleSection(kind="callers", tokens=honest, items=99, body=body)


def test_section_without_body_is_a_legitimate_state_not_a_violation() -> None:
    """`body=[]`는 "본문이 없다"가 아니라 "이 섹션은 본문을 여기 담지 않는다"이다.

    셋이 전부 정당하다: `target_function`(원문은 프롬프트가 따로 읽는다),
    보조 섹션(데이터원이 아직 없다), **간선 0건**(그래프를 봤는데 없었다).
    검증기가 여기 걸리면 "없다"와 "0건"의 구별이 무너진다.
    """
    assert CapsuleSection(kind="target_function", tokens=900).body == []
    assert CapsuleSection(kind="static_signal", tokens=64).body == []
    assert CapsuleSection(kind="callers", tokens=0, items=0).items == 0


def test_transition_keeps_the_body_and_its_validator() -> None:
    """`transition()`은 전체 재검증을 거치므로 본문도 왕복해야 한다.

    `model_copy`였다면 검증기를 우회해 본문 없는 섹션이 조용히 생겼을 것이다.
    """
    body = ["main main.c:12"]
    section = CapsuleSection(kind="callers", tokens=estimate_tokens(body[0]), items=1, body=body)
    capsule = _capsule(sections=[section])

    moved = capsule.transition(expansion_count=1)

    assert moved.sections[0].body == body


def test_capsule_expansion_count_has_no_hardcoded_cap() -> None:
    """확장 상한(초기값 3)은 config에서 오는 정책값이다(engineering-guide.md §8.5).

    스키마가 상한을 박으면 실측 후 조정이 코드 변경이 된다.
    """
    assert _capsule(expansion_count=7).expansion_count == 7


# --- RunRecord 불변식 (evaluation.md §2·§6) --------------------------------


@pytest.mark.parametrize(
    "condition", ["B1@pipeline", "B1prime@pipeline", "B3@pipeline", "B3prime@pipeline"]
)
def test_valid_conditions(condition: str) -> None:
    assert _run(condition=condition).condition == condition


@pytest.mark.parametrize(
    "condition", ["B1", "B2@pipeline", "B1'@pipeline", "B3prime@other", "b1@pipeline", "B1@2x2"]
)
def test_invalid_conditions_are_rejected(condition: str) -> None:
    """이름이 넷 밖이거나 축이 `pipeline`이 아니면 비교표가 섞인다.

    `B1@2x2`는 2×2 폐기(2026-09-20) 이후 무효가 된 축의 회귀 핀이다.
    """
    with pytest.raises(ValidationError):
        _run(condition=condition)


def test_finished_at_cannot_precede_started_at() -> None:
    with pytest.raises(ValidationError):
        _run(finished_at=datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC))


def test_only_completed_runs_count_for_comparison() -> None:
    finished = datetime(2026, 9, 1, 11, 0, 0, tzinfo=UTC)
    assert not _run().is_complete
    assert _run(finished_at=finished).is_complete
    # 예산 소진으로 중단된 run은 부분 결과다 (evaluation.md §6).
    assert not _run(finished_at=finished, stopped_after=5).is_complete


def test_stopped_after_is_bounded_to_pipeline_stages() -> None:
    with pytest.raises(ValidationError):
        _run(stopped_after=10)


def test_repo_commit_must_be_sha1() -> None:
    with pytest.raises(ValidationError):
        _run(repo_commit=ZERO_DIGEST)


# --- 인터페이스 값 타입 ------------------------------------------------------


def test_object_kind_matches_id_prefixes() -> None:
    """StateStore가 다루는 종류와 ID 접두사 목록이 갈라지면 조용히 빈 결과가 나온다."""
    assert set(get_args(ObjectKind)) == set(ID_PREFIXES)


def test_cached_tokens_cannot_exceed_input() -> None:
    assert Usage(input_tokens=100, output_tokens=10, cached_tokens=100).cached_tokens == 100
    with pytest.raises(ValidationError):
        Usage(input_tokens=100, output_tokens=10, cached_tokens=101)


def test_sandbox_defaults_are_locked_down() -> None:
    """engineering-guide.md §7: 네트워크 차단·읽기 전용이 기본값이다."""
    sandbox = SandboxSpec(image="harness/fuzz:latest", cpu_limit=2.0, memory_mb=4096, timeout_s=600)
    assert sandbox.network is False
    assert sandbox.read_only is True


def test_sandbox_limits_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SandboxSpec(image="harness/fuzz:latest", cpu_limit=0, memory_mb=4096, timeout_s=600)


def test_tool_args_reject_empty_argv() -> None:
    assert ToolArgs(argv=["semgrep", "--config=auto"], workdir="/src").env == {}
    with pytest.raises(ValidationError):
        ToolArgs(argv=[], workdir="/src")


# --- 도구 요청에 데이터로 싣는 입력 바이트 (#386 ①) ---------------------------------


def _placeholder_argv(*rest: str) -> list[str]:
    return ["poc", tool_runner.INPUT_PLACEHOLDER, *rest]


def test_tool_args_input_defaults_to_none() -> None:
    """옵션 칸이다 — 기존 호출부(`ToolArgs(argv, workdir)`)는 한 줄도 안 바뀐다."""
    assert ToolArgs(argv=["poc"], workdir="/src").input_bytes is None


def test_tool_args_reject_a_placeholder_without_bytes() -> None:
    """자리표가 있는데 채울 바이트가 없으면 안 풀린 자리표가 로그 머리에 **그대로** 남는다 —
    경계에서 빨리 실패한다. 메시지는 무엇을 고칠지 적는다."""
    with pytest.raises(ValidationError, match="input_bytes"):
        ToolArgs(argv=_placeholder_argv(), workdir="/src")


def test_tool_args_reject_bytes_without_a_placeholder() -> None:
    """반대 방향 — 바이트를 실었는데 어댑터가 둔 경로를 알릴 자리표가 없다."""
    with pytest.raises(ValidationError, match="자리표"):
        ToolArgs(argv=["poc"], workdir="/src", input_bytes=b"x")


def test_tool_args_accept_the_pair_including_an_empty_input() -> None:
    """`b""`도 입력이다 — PoC가 0바이트일 수 있다(`repeat` `count=0`). 참·거짓으로 판정하면
    그 입력이 조용히 「없음」이 된다."""
    for data in (bytes.fromhex("000d0aff"), b""):
        args = ToolArgs(argv=_placeholder_argv(), workdir="/src", input_bytes=data)
        assert args.input_bytes == data


def test_tool_args_placeholder_is_a_whole_element_not_a_substring() -> None:
    """모델이 쓴 `repro_spec.steps`가 argv에 그대로 들어간다(`validate._call`). 자리표를 **품은**
    문자열이 run을 죽이면 안 된다 — 원소가 자리표와 **같을 때만** 자리표다. 같은 원소는 노드가
    도구에 가기 전에 거절한다(`tests/test_nodes.py`)."""
    embedded = f"cat {tool_runner.INPUT_PLACEHOLDER} | ./poc"

    assert ToolArgs(argv=["poc", embedded], workdir="/src").input_bytes is None


def test_tool_args_input_must_be_bytes_not_text() -> None:
    """텍스트가 조용히 UTF-8로 바뀌어 PoC가 달라지지 않는다."""
    with pytest.raises(ValidationError):
        ToolArgs(argv=_placeholder_argv(), workdir="/src", input_bytes="AAAA")


def test_tool_args_never_echo_the_bytes_in_repr_or_in_a_validation_error() -> None:
    """원바이트는 로그·예외 문구로 새지 않는다 — 8 MiB까지 갈 수 있고 PoC다."""
    secret = b"SECRET-POC-BYTES"
    carrying = ToolArgs(argv=_placeholder_argv(), workdir="/src", input_bytes=secret)

    assert "SECRET-POC-BYTES" not in repr(carrying)
    assert "SECRET-POC-BYTES" not in str(carrying)
    with pytest.raises(ValidationError) as excinfo:
        ToolArgs(argv=["poc"], workdir="/src", input_bytes=secret)
    assert "SECRET-POC-BYTES" not in str(excinfo.value)


def test_with_input_path_replaces_only_whole_placeholder_elements() -> None:
    """어댑터가 부르는 치환 — 새 목록을 돌려주고 입력 목록은 안 건드린다."""
    placeholder = tool_runner.INPUT_PLACEHOLDER
    argv = ["poc", placeholder, f"x{placeholder}", placeholder]

    resolved = tool_runner.with_input_path(argv, "/work/input-1.poc")

    assert resolved == ["poc", "/work/input-1.poc", f"x{placeholder}", "/work/input-1.poc"]
    assert argv == ["poc", placeholder, f"x{placeholder}", placeholder]


# --- 기존 manifest 호환 (2026-08-17) -----------------------------------------

# **추적되는 fixture 하나가 유일한 CI 강제 장치다.**
#
# 출처: `artifacts/run_20260817_0004/run_manifest.json`을 그대로 복사한 것이다.
#   - 언제  : 2026-08-17 (로컬)
#   - 무엇  : `dev_fake` 프로필 스모크 run. `--device verify`, `tool_runner: fake`,
#             `tool_images` 비어 있음. **ATOM run이 아니다** — 그 run의 산출물은 그
#             장비에 있고 이 저장소에 없다.
#   - 왜    : `budget`에 `tool_seconds`가 **없다.** 그 필드가 생기기 전에 쓰인 기록이므로,
#             옵셔널로 추가했다는 주장을 실제로 검증한다.
#
# `artifacts/`는 `.gitignore`에 있어 저장소 상태가 아니다. 그래서 아티팩트를 훑는
# 테스트는 CI에서 manifest 0개로 **공허하게 통과한다** — 추적되는 fixture만이 강제력을
# 가진다. 스키마에 필수 필드를 늘리려는 사람은 이 테스트에서 먼저 걸려야 한다.
MANIFEST_FIXTURE = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "manifests" / "run_20260817_0004.json"
)


def test_a_manifest_written_before_tool_seconds_still_loads() -> None:
    """`Budget.tool_seconds`를 옵셔널로 넣은 근거가 이것이다.

    필수로 넣었다면 이 fixture가 `model_validate`에서 통째로 실패한다 —
    `Benchmark.measured_on`·`ModelEntry.source`가 같은 이유로 기본값을 뒀다.
    """
    payload = json.loads(MANIFEST_FIXTURE.read_text(encoding="utf-8"))
    assert "tool_seconds" not in payload["budget"], "fixture가 낡지 않았다 — 검사가 공허해진다"

    record = RunRecord.model_validate(payload)

    assert record.budget.tool_seconds == 0  # "상한이 없었다"는 사실이지 지어낸 값이 아니다
    assert record.tool_runner and record.device is not None
    # 이 필드 이전에 쓰인 기록이므로 `None`이다 — "확인 못 함"이 아니라 "남기지 않았다"다.
    # 새 run은 `config.profile_name`에서 무조건 유도되므로 `None`이 나올 수 없다 (이슈 #76).
    assert record.profile is None
    # 같은 사유로 `compile_flags`도 `None`이다 (D32 / #74). 새 run은 어댑터에게 물어
    # 항상 값을 남기므로(비어도) `None`은 **옛 manifest에서만** 나온다.
    assert "compile_flags" not in payload, "fixture가 낡지 않았다 — 검사가 공허해진다"
    assert record.compile_flags is None


# ## required 두 필드 — 이 계약은 모델이 무엇을 채우는가를 가른다
#
# 2026-09-11 ATOM 실측(이슈 #172)이 근거다. 스키마가 빈 리스트를 기본값으로 허용하는 동안
# 모델은 `path`·`evidence_refs`를 채우지 않았고, `required`로 올리자 채웠다.
# **그 계약을 지키는 검사가 이 파일에 없었다** — 기본값을 되돌려도 CI가 초록이었다.


def test_the_finding_schema_requires_the_two_fields_the_model_kept_omitting() -> None:
    """`path`·`evidence_refs`가 `required`에 있고 `missing_evidence`는 없다.

    **`missing_evidence`를 함께 단언하는 이유**는 셋이 형태가 같아서다 — 전부
    `list[Identifier]`라 "리스트 셋을 다 required로" 같은 일괄 수정이 자연스러워 보인다.
    그쪽은 모델이 「무엇이 없는가」를 알릴 때만 채우는 선택 필드라 강제하면 빈 배열을
    받게 된다. 세 번째를 음성으로 고정해 그 일괄 수정을 막는다.

    ⚠ **`minItems`는 단언하지 않는다 — 없어야 하는 것이다.** 같은 실측에서 `required`와
    채움이 같았고, 빈 리스트는 근거 없는 `hypothesized` 초안의 정당한 출력이다.
    """
    required = set(Finding.model_json_schema()["required"])

    assert "path" in required
    assert "evidence_refs" in required
    assert "missing_evidence" not in required, (
        "선택 필드다 — required로 올리면 모델이 빈 배열을 채워 보낸다"
    )

    props = Finding.model_json_schema()["properties"]
    for name in ("path", "evidence_refs"):
        assert "minItems" not in props[name], f"{name}에 minItems가 붙었다 — 제약만 는다"


def test_the_d67_probe_control_arm_differs_from_its_treatment_arm() -> None:
    """프로브의 `stock` 팔이 `required` 팔과 **같으면 안 된다.**

    **이 검사가 있는 이유는 실측이 아니라 사고다.** `stock`은 한때
    `Finding.model_json_schema()`를 그대로 돌려줬다. 두 필드를 required로 올리는 순간
    **대조군이 실험군과 바이트 동일**해지는데 프로브는 아무 신호도 내지 않았다 —
    다시 돌리면 `stock`이 100% 채우고, 읽는 쪽은 09-11 관측이 재현되지 않는다고 읽는다.
    **대조군이 조용히 사라지는 것은 실패한 실험보다 나쁘다.**
    """
    from tools.probe.d67_required_schema import _schema_for

    stock = _schema_for("stock")
    required = _schema_for("required")

    assert stock != required, "대조군이 실험군과 같다 — 프로브가 아무것도 안 가른다"
    for name in ("path", "evidence_refs"):
        assert name not in stock["required"], f"stock 팔에 {name}이 남아 있다"
        assert name in required["required"], f"required 팔에 {name}이 없다"
