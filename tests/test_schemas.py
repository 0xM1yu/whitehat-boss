"""스키마 계약 테스트.

두 가지를 고정한다.

1. **문서 예시가 실제로 파싱된다.** agent-contracts.md §3의 JSON이 스키마와 어긋나면
   8개 트랙이 서로 다른 것을 만든다(roadmap.md §3).
2. **문서가 요구한 불변식이 실제로 강제된다.** engineering-guide.md §8.7: 비결정적
   로직(임계값 판정, 상태 전이 조건)에는 반드시 테스트를 남긴다.

모든 값은 문서와 같은 합성 예시다.
"""

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from src.interfaces import ObjectKind, SandboxSpec, ToolArgs, Usage
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
    ReproSpec,
    Role,
    RunRecord,
    Sampling,
    Verdict,
)

ZERO_DIGEST = "sha256:" + "0" * 64
ZERO_COMMIT = "0" * 40
AUDITOR = ProducedBy(role=Role.AUDITOR, model="qwen3-coder-30b-a3b", prompt_version="v1")


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
            role=Role.CRITIC, model="devstral-small-2-24b", prompt_version="v1"
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


def test_capsule_expansion_count_has_no_hardcoded_cap() -> None:
    """확장 상한(초기값 3)은 config에서 오는 정책값이다(engineering-guide.md §8.5).

    스키마가 상한을 박으면 실측 후 조정이 코드 변경이 된다.
    """
    assert _capsule(expansion_count=7).expansion_count == 7


# --- RunRecord 불변식 (evaluation.md §2·§6) --------------------------------


@pytest.mark.parametrize("condition", ["B1@2x2", "B1prime@2x2", "B3@pipeline", "B3prime@pipeline"])
def test_valid_conditions(condition: str) -> None:
    assert _run(condition=condition).condition == condition


@pytest.mark.parametrize("condition", ["B1", "B2@2x2", "B1'@2x2", "B3prime@other", "b1@2x2"])
def test_invalid_conditions_are_rejected(condition: str) -> None:
    """어느 정의(2×2 vs 파이프라인)의 조건인지 라벨에 없으면 비교표가 섞인다."""
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
