"""파이프라인 전역에서 쓰는 열거형.

값은 docs/agent-contracts.md §3, docs/architecture.md §4, docs/knowledge-base.md §4의
문자열을 그대로 옮긴 것이다.
"""

from enum import StrEnum


class Role(StrEnum):
    """실행 계약의 역할 (agent-contracts.md §1)."""

    SCOUT = "scout"
    AUDITOR = "auditor"
    CRITIC = "critic"
    JUDGE = "judge"
    # B1·B3 통합 단일패스. Scout·Auditor·Critic 없이 혼자 경로를 세우고 자기반박하며
    # 출력은 Auditor와 같은 `Finding`이다(`prompts/single/v2/meta.yaml`).
    #
    # **`config.PROMPTED_ROLES`에 `"single"`이 먼저 들어와 있었고 여기가 비어 있었다.**
    # 그래서 `Role("single")`이 `ValueError`였고, `cli._check_prompts`·`cli._prefix_tokens`·
    # `bench_report._role_report`가 전부 그 위를 지나간다. 지금은 세 곳 다
    # `if role in config.profile.roles` 가드에 걸려 안 터지지만, **그 가드가 막는 것은
    # 정확히 single-pass 프로필을 붙이는 경우**다 — 등록의 목적이 그것이므로 프로필이
    # 하나 생기는 순간 harness check·prefix 추정·bench가 함께 죽고 `prompts/single/v2`는
    # 끝내 로드되지 않는다.
    #
    # **[ADR 0004](../../docs/decisions/0004-retrieval-clients-separate-from-model-client.md)가
    # 기각한 것과 다른 종류다.** 그쪽은 `embedding`·`reranker`였고 사유는 "프롬프트도
    # 스키마도 없는 검색 부품"이었다. `single`은 프롬프트 디렉터리와 `output_schema`를
    # 가진 실행 계약 역할이라 그 사유가 적용되지 않는다.
    #
    # `tests/test_config.py`가 `PROMPTED_ROLES ⊆ Role`을 강제한다.
    SINGLE = "single"


class EvidenceKind(StrEnum):
    """Evidence.kind 열거형 (agent-contracts.md §3)."""

    STATIC = "static"
    SANITIZER = "sanitizer"
    CRASH = "crash"
    FUZZ_COVERAGE = "fuzz_coverage"
    BUILD = "build"
    POC_RUN = "poc_run"
    DIFF = "diff"
    GRAPH = "graph"


class ArtifactKind(StrEnum):
    """Artifact.kind.

    제안: architecture.md §3 0단계가 "바이너리·소스·설정 식별"을 요구하나 열거형이
    문서에 없어 신설했다. firmware는 §3의 "펌웨어 추출(binwalk)" 경로용.
    """

    SOURCE = "source"
    BINARY = "binary"
    FIRMWARE = "firmware"
    CONFIG = "config"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    """Decision.verdict 열거형 (agent-contracts.md §3)."""

    UPHOLD = "uphold"
    REBUT = "rebut"
    NEED_EVIDENCE = "need_evidence"


class CapsuleLevel(StrEnum):
    """ContextCapsule.level 열거형 (knowledge-base.md §4)."""

    LIGHT = "light"
    EXPANDED = "expanded"
    FOCUSED = "focused"


class EvidenceGrade(StrEnum):
    """Evidence Gate 근거 등급 (architecture.md §4).

    단일 게이트가 한 번에 매기는 값이 아니다. E3은 Critic 출력이, E4는 도구 검증
    로그가 있어야 성립하므로 6·7·8단계에서 각각 재판정된다.
    """

    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"


class FindingState(StrEnum):
    """후보/Finding 상태 (architecture.md §4 상태 머신).

    제안: 상태 머신은 하나의 체인이지만 agent-contracts.md §3은 Candidate와 Finding에
    각각 state를 둔다. 열거형은 하나로 두고, 어느 값이 어느 객체에 허용되는지는
    각 모델의 Literal 제약으로 표현한다(candidate.py / finding.py 참조).

    `rebutted`는 상태가 아니라 전이 라벨이므로 ReasonCode 쪽에 있다.
    """

    CANDIDATE = "candidate"
    TRIAGED = "triaged"
    HYPOTHESIZED = "hypothesized"
    QUEUED = "queued"
    VALIDATED = "validated"
    PROVEN = "proven"
    REPORTED = "reported"


class ReasonCode(StrEnum):
    """상태 전이 사유 코드.

    agent-contracts.md §2.4는 "사유 코드는 열거형으로 고정한다(자유 텍스트 금지)"만
    요구하고 목록은 주지 않는다. 아래는 architecture.md §3·§4와 agent-contracts.md §1
    계약 위반 표에서 도출한 **초안이며, 구현이 진행되면 확장될 수 있다.**
    확정 목록이 아니므로 여기에 없는 사유가 필요하면 추가하고 문서에 반영한다.
    """

    # 4단계 Scout
    SCOUT_PROMOTED = "scout_promoted"
    BELOW_SCOUT_THRESHOLD = "below_scout_threshold"
    # 5단계 Auditor
    AUDITOR_HYPOTHESIZED = "auditor_hypothesized"
    # 6단계 Evidence Gate
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EVIDENCE_WEAK = "evidence_weak"
    PROMOTED_TO_CRITIC = "promoted_to_critic"
    # 7단계 Critic
    CRITIC_UPHELD = "critic_upheld"
    CRITIC_REBUTTED = "critic_rebutted"
    CRITIC_NEED_EVIDENCE = "critic_need_evidence"
    # 반박에는 실패했으나 재현 명세가 없어 validated로 올릴 수 없는 경우 (architecture.md §4 E3).
    REPRO_SPEC_MISSING = "repro_spec_missing"
    # 8단계 Tool Validation
    REPRO_CONFIRMED = "repro_confirmed"
    REPRO_FAILED = "repro_failed"
    # run 종료 (state_machine.py R-2). 아래 둘과 BUDGET_EXHAUSTED가 "왜 9단계까지 못
    # 갔는가"를 구분한다. 하나로 묶으면 예산이 실제로 소진된 run과 사용자가 자른 run이
    # 기록에서 같아 보이고, evaluation.md가 중단 사유를 조건 간 비교 지표로 쓰므로
    # "예산 초과로 중단됨" 같은 존재하지 않는 결론이 나온다.
    #
    # `--dry-run`을 USER_STOP_REQUESTED에 묶지 않은 것도 같은 이유다. dry-run은 사용자가
    # 중단한 것이 아니라 실행 모드이고, 두 상한은 동시에 걸릴 수 있다
    # (`--dry-run --stop-after 5`). 사유가 하나면 어느 쪽이 상한을 결정했는지가 사라진다 —
    # 어느 쪽이 이겼는지는 `RunSpec.stop_at()`이 단계 수와 함께 정한다.
    USER_STOP_REQUESTED = "user_stop_requested"
    DRY_RUN = "dry_run"
    # 계약 위반 (agent-contracts.md §1)
    BUDGET_EXHAUSTED = "budget_exhausted"
    SCHEMA_PARSE_FAILED = "schema_parse_failed"
    # run 중단 (W2). 후보 하나가 실패한 것은 `target_skipped`로 남고 run은 계속 간다 —
    # 아래 둘은 **더 갈 이유가 없어서 러너가 멈춘 경우**다. 하나로 묶지 않는 이유는
    # 대응이 다르기 때문이다: 앞은 서버를 살려야 하고, 뒤는 서버가 살아 있는데 후보가
    # 계속 죽는 것이라 프롬프트·예산·스키마를 봐야 한다.
    MODEL_GATEWAY_UNAVAILABLE = "model_gateway_unavailable"
    CONSECUTIVE_TARGET_FAILURES = "consecutive_target_failures"
    TOOL_POLICY_VIOLATION = "tool_policy_violation"
    CAPSULE_EXPANSION_EXHAUSTED = "capsule_expansion_exhausted"
    # Queue 규칙 (architecture.md §3)
    NEW_EVIDENCE_REQUEUE = "new_evidence_requeue"
    # 재평가 횟수 상한(config의 requeue_max) 초과. 폐기하지 않고 queued를 유지한다.
    REQUEUE_LIMIT_REACHED = "requeue_limit_reached"
    # 제보 (disclosure-policy.md) — 자동 전이 없음, 사람 승인으로만
    HUMAN_VERIFIED = "human_verified"


class PathStepKind(StrEnum):
    """Finding.path 각 단계의 종류 (agent-contracts.md §3 Finding 예시)."""

    SOURCE = "source"
    PROPAGATE = "propagate"
    SINK = "sink"
