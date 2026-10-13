"""파이프라인 전역에서 쓰는 열거형.

값은 docs/agent-contracts.md §3, docs/architecture.md §4, docs/knowledge-base.md §4의
문자열을 그대로 옮긴 것이다.
"""

from enum import StrEnum


class Role(StrEnum):
    """실행 계약의 역할 (agent-contracts.md §1)."""

    SCOUT = "scout"
    # 4단계 Scout 풀 (ADR 0010 §3). 한 대상을 셋이 각각 보고, 임계값을 넘은 후보를 고르지도
    # 합치지도 않고 각각 5단계로 보낸다. 겨냥하는 CWE 계열이 멤버마다 다르고 그 배정의
    # 정본은 PROJECT-CONTEXT.md §1 표다 — memory는 CWE-415·416, boundary는 CWE-119·125·787·190,
    # injection은 CWE-78·22·134이며, 계열을 여기 값으로 박지 않는 이유는 배정이 첫 run의
    # 공동오답 행렬로 바뀔 수 있기 때문이다.
    #
    # **왜 `Role` 셋이고 `roles`를 리스트로 넓히는 것이 아닌가.** ADR 0010 §3이 파급을 표로
    # 센다. 고칠 것이 **정말 0인 자리는 둘**이다 — `RunRecord.model_checksums`
    # (`config.model_checksums()`가 `profile.roles.items()`를 그대로 돈다)와 `ProducedBy.role`
    # (필드 타입이 `Role`이다). 나머지는 **상수 두 곳에 멤버당 한 줄**이다:
    # `config.PROMPTED_ROLES`가 `prompt_versions()`와 `factory`의 `clients` 컴프리헨션을
    # (그래서 `RoutingModelClient`를) 함께 가르고, `bench.ROLE_SPEC`이 bench 경로를 가른다.
    # 리스트였다면 그 여섯 전부에 "리스트의 몇 번째"라는 **새 좌표**를 파야 했다 — 줄을
    # 더하는 것과 좌표를 파는 것의 차이가 이 선택의 전부다.
    #
    # ⚠ **역할별 토큰 카운터는 그 여섯에 안 든다 — 줄을 더해도 기록엔 안 실린다.**
    # ADR 0010 §3의 ⚠ 문단이 같은 것을 적는다.
    # `src.tokens.COUNTED_ROLES`가 `("scout", "auditor")`로 박혀 있고
    # `tests/test_tokens.py`가 그 둘을 등식으로 고정한다.
    #
    # ⚠ **2026-09-11에 절반이 바뀌었다** (PR #178 — 풀 팬아웃).
    #
    # - **러너 쪽은 절반 따라온다.** `_step_retrieval`이 `ctx.counter_for_role(item.role.value)`를
    #   부르므로 예전 문장("러너가 `Role.SCOUT.value`로 박혀 있어 역할 카운터가 안 따라온다")은
    #   **거짓이 됐다.** ⚠ **다만 켜지는 것은 그 target의 첫 멤버 하나다** — 캡슐이 공유돼
    #   나머지 멤버는 `using_counter`에 도달하기 전에 반환한다(`_step_retrieval`).
    #   `nodes/scout.py`의 `ROLE = Role.SCOUT`도 이제 `run()`의 **기본값**이지 유일한 역할이 아니다.
    # - **기록 쪽도 #192로 따라왔다.** `Deps.counter_report`가 `COUNTED_ROLES`(단일 기준 상수)를
    #   그대로 돌던 것을, `factory._counted_roles`가 **프로필에 실재하는 자리**를 돌게 바꿨다.
    #   그래서 풀이면 켜지는 첫 멤버(`scout_memory`)를 싣고, 프로필에 없는 `scout`을
    #   `no_source`로 싣던 「없는 것을 싣는다」 거짓 기록이 사라졌다. 단일 프로필은 `scout`을
    #   그대로 싣는다(무회귀). `COUNTED_ROLES` 상수는 「단일 기준」으로 남고
    #   `tests/test_tokens.py`가 그것을 고정한다 — **상수를 늘리지 않고 프로필에서 집합을
    #   끌어온 것**이 요점이다.
    # - **멤버 2·3의 토큰 회계 근거**는 캐시-적중 이벤트의 `counted_by`가 남긴다(#192 ②) —
    #   공유 캡슐을 어느 역할 카운터로 계산했나가 기록에 실린다. ⚠ **예산이 멤버별 토크나이저로
    #   강제되는 것은 별개**이고 공유 캡슐 재검증(#194)에 남는다.
    #
    # **`SCOUT`은 남는다** — `dev_*` 프로필이 단일 `scout`(fake)이다(2026-09-24 전에는 실서빙
    # `local_balanced`·`_hybrid`도 단일 `scout`이었고 그날 지웠다). 풀을 선언하는 것은 canonical
    # `harness_pool`·`harness_pool_hybrid`(`production: false`)이고, 세 Scout 키가 v3 풀
    # 프롬프트를 가리킨다(`PROJECT-CONTEXT.md` §1 — PR #190 때의 v2 별칭 셋은 2026-09-21
    # 교체 뒤 배선이 없다). 프로필은 단일 `scout` 하나 아니면 풀 셋을 선언한다
    # (`config.ProfileEntry`가 그 배타를 본다).
    # ⚠ 여기서 프로필 이름을 세지 마라 — 개수도 이름도 `config/profiles.yaml`에서 센다.
    #
    # 아래 `SINGLE` 주석이 적은 함정이 여기도 그대로다 — `config.PROMPTED_ROLES`와
    # `bench.ROLE_SPEC`에 함께 넣지 않으면 프롬프트 검사·bench 경로가 각각 죽는다.
    SCOUT_MEMORY = "scout_memory"
    SCOUT_BOUNDARY = "scout_boundary"
    SCOUT_INJECTION = "scout_injection"
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
    # 8단계 PoV — Auditor Finding에서 PoC를 만드는 모델-주도 툴 루프(매 홉 `PovAction` · v5는 형제
    # `PovActionNamed`).
    # SINGLE 주석의 함정이 그대로다: `config.PROMPTED_ROLES`·`bench.ROLE_SPEC`에 함께 넣지
    # 않으면 harness check·bench가 죽고, `prompts/pov/`도 같은 커밋에 있어야 test_config가 산다.
    POV = "pov"


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


# `Verdict.from_text`가 단어 **양끝에서만** 떼는 장식·구두점. 모델이 `**uphold**`·`"uphold"`·
# `— uphold`·`1. uphold`·`“uphold”`로 내는 것이 실측이라 그만큼만 걷는다. 안쪽을 건드리면
# `need_evidence`와 `not vulnerable`이 쪼개진다.
#
# **비ASCII 구두점과 번호 매김이 여기 있는 이유** (#182 재리뷰 L-4): 판정을 목록 항목으로
# 내면(`• uphold`) 그 토큰이 통째로 장식이라 빈 문자열이 되고, 빈 단어는 아래에서 버려진다.
_VERDICT_DECOR = "*\"'`:,.;!?()[]{}<>-_=/|~#%+“”‘’—–•·…0123456789"


def _verdict_words(text: str) -> list[str]:
    """verdict 문자열을 **장식을 뗀 소문자 단어 목록**으로 만든다.

    표준값 층과 동의어 층이 **같은 입력**을 보게 하는 것이 이 함수의 존재 이유다
    (#182 재리뷰 H-A). 예전에는 장식 제거가 표준값 셋에만 걸려 있어서 `Yes`는 UPHOLD인데
    `**Yes**`·`Yes.`가 `None`이었다 — **마침표 하나로 판정이 갈렸다.**
    """
    return [word for word in (token.strip(_VERDICT_DECOR) for token in text.split()) if word]


# `Verdict.from_text`가 읽는 **전체 문자열 단독 동의어**. 문장 속 부분 문자열이 아니라
# verdict 필드 전체가 정확히 이것일 때만 매핑한다(위 docstring). 애매한 것(예: `"not safe"`)은
# 넣지 않는다 — 미해석(`None`)으로 두고 `need_evidence`로 보내는 편이 오분류보다 안전하다.
# ⏵ 2026-10-11 — 그 보류도 이제 8단계 추가 풀에 든다(uphold·미경유 뒤, 이슈 #604).
#
# ⚠ **CWE 이름을 넣지 않는다** (#182 재리뷰 M-3). `use-after-free`가 여기 있었는데 그것은
# 판정이 아니라 약점 종류이고, Scout이 겨냥하는 CWE 계열(415/416 · 119/125/787/190 ·
# 78/22/134) 중 **한 계열만 매핑되면 Critic 제거율이 대상 CWE에 따라 달라진다**
# (evaluation.md §2.3 — 조건 간 비교의 분자가 대상에 따라 다른 규칙으로 만들어진다).
# 빼면 그 응답은 `need_evidence`(보류)이고 그것이 안전한 방향이다.
_VERDICT_AFFIRM = frozenset({"yes", "vulnerable", "unsafe", "upheld", "exploitable"})
_VERDICT_DENY = frozenset(
    {"no", "safe", "rebutted", "false positive", "benign", "not vulnerable", "rejected"}
)
_VERDICT_DEFER = frozenset(
    {"need evidence", "unclear", "insufficient", "inconclusive", "unknown", "cannot determine"}
)


class Verdict(StrEnum):
    """Decision.verdict 열거형 (agent-contracts.md §3)."""

    UPHOLD = "uphold"
    REBUT = "rebut"
    NEED_EVIDENCE = "need_evidence"

    @classmethod
    def from_text(cls, text: str) -> "Verdict | None":
        """자유 verdict를 표준 Verdict로 읽는다. **못 읽으면 `None`**(추측하지 않는다).

        **왜 자유 텍스트인가.** verdict를 스키마에 `enum`으로 강제하면 grammar 마스크가
        모델의 맞는 추론을 틀린 토큰으로 덮는다 — gpt-oss-20b 실측(2026-09-12): `enum` 강제
        verdict 2/5, `enum` 제거(자유 문자열) **5/5**, rationale은 둘 다 정답.
        ⚠ **원천: PR #182 본문 (원문 미커밋).** 그 run의 JSONL·스크립트가 저장소에 없다 —
        같은 날 커밋된 `docs/experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md`
        는 **다른 실험**이고(`enum` 강제 팔을 안 돌렸다) §5가 그 사실의 자리다. 그래서 요청은
        verdict를 자유 문자열로 받고(`DecisionDraft`) 여기서 표준값에 매핑한다.

        **부분 문자열로 의미를 추측하지 않는다.** *"unsafe"*가 `safe`를 담는다고 REBUT으로,
        *"uphold: no guard…"*가 `no`를 담는다고 REBUT으로 보내면 **근거 있는 진짜 취약을
        기각한다.** 그래서 여기서 읽는 것은 **① 장식을 뗀 단어 하나가 표준값일 때**와
        **② 장식을 뗀 전체가 짧고 명백한 단독 동의어일 때**뿐이다. 문장형 자유 서술은 `None`이다.

        **①과 ②가 같은 정규화를 본다**(`_verdict_words`). 장식 제거가 ①에만 걸려 있던 판
        (#182 재리뷰 H-A)은 `Yes`를 UPHOLD로 읽고 `**Yes**`·`Yes.`·`"Yes"`를 `None`으로
        읽었다 — **마침표 하나로 판정이 갈렸고**, 그 `None`은 `need_evidence`를 거쳐
        게이트 `p3`(`upheld` 요구)에 걸려 **진짜 취약이 `validated`에 못 갔다.** H1이
        오탐 통과 쪽으로 치우쳤다면 이쪽은 반대 방향으로 치우친 같은 결함이다.

        **①의 단어 경계가 요점이다.** 접두어만 보던 판(`t.startswith(verdict.value)`,
        #182 리뷰 H1)은 *"upholding is not warranted, this is a false positive"*와
        *"uphold or rebut"*을 둘 다 UPHOLD로 읽었다 — **거부가 지지가 됐고**,
        *"rebut or uphold"*와는 어순으로만 갈렸다. `uphold`만 무제한 접두어 매칭이고
        부정 동의어는 전체 일치를 요구했으므로 **틀리는 방향이 오탐 통과 쪽으로 치우쳐
        있었다.** 지금은 셋을 함께 본다.

        - **단어 양끝의 장식만 뗀다** — `**uphold**`·`"uphold"`·`` `need_evidence` ``·
          `• uphold`·`1. uphold`가 실제 출력 모양이다. 안쪽은 건드리지 않아
          `need_evidence`·`not vulnerable`이 쪼개지지 않는다.
        - **표준값이 둘 이상 나오면 읽지 않는다**(`"uphold or rebut"`). 어느 쪽이 판정인지는
          이 함수가 정할 일이 아니다. 머리 단어가 표준값이어도 마찬가지다
          (`"rebut: this is not an uphold case"` → `None`) — 머리를 택하는 규칙은
          `"uphold or rebut"`을 UPHOLD로 만들어 위 어순 의존을 되살린다.
        - **판정은 맨 앞 단어다**(`"uphold: no guard…"` → UPHOLD). 그 앞에 선 것이 표준값이
          아니면 이름표로 보고 **첫 구분자(`:`·`=`)까지 한 번** 걷는다 —
          `"verdict: uphold"`·`"final verdict = uphold"`·`"verdict:uphold"`가 같은 판정이다.
          그래서 부정문 속 표준값(*"I would not uphold this"*)은 `None`으로 남는다 —
          앞에 서지 않은 표준값은 그 문장의 판정이 아니다.
        - **CWE 이름은 판정이 아니다**(`"use-after-free"` → `None`, 재리뷰 M-3).

        **`None`은 지지가 아니다.** 호출자(`DecisionDraft.to_decision`)가 미해석을
        `need_evidence`(보류)로 보내고 원문을 trace에 남긴다 — `uphold`로 소비하지 않는다
        (그렇지 않으면 임의 문자열이 경로·근거 있는 Finding을 `validated`로 통과시킨다).
        """
        lowered = text.strip().lower()
        values = {verdict.value for verdict in cls}
        # 라벨을 **한 번** 걷는다 — 첫 구분자 앞에 표준값이 없으면 그 앞은 이름표다.
        # `uphold: no guard…`는 앞이 이미 판정이라 걷지 않고, `final verdict = uphold`는
        # 걷는다. 구분자를 `:` 하나로 모아 공백 없는 `verdict:uphold`도 같이 갈린다.
        label, _, tail = lowered.replace("=", ":").partition(":")
        words = _verdict_words(lowered)
        if tail and not values & set(_verdict_words(label)):
            words = _verdict_words(tail)
        if len(values & set(words)) > 1:
            return None
        head = words[0] if words else ""
        if head in values:
            return cls(head)
        # **동의어도 같은 정규화를 본다.** raw 문자열을 보던 동안 `**Yes**`·`Yes.`가 `None`
        # 이었고, 그 `None`은 `need_evidence`를 거쳐 **E3를 막았다**(#182 재리뷰 H-A).
        normalized = " ".join(words)
        if normalized in _VERDICT_AFFIRM:
            return cls.UPHOLD
        if normalized in _VERDICT_DENY:
            return cls.REBUT
        if normalized in _VERDICT_DEFER:
            return cls.NEED_EVIDENCE
        return None


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


class ReproOrigin(StrEnum):
    """`ReproSpec`를 누가 만들었나 (#S7 — 재현 명세 합성).

    `model` — Auditor 모델이 낸 명세. 지금까지의 유일한 경우다.
    `harness` — 하네스가 합성한 명세. CyberGym Level 1처럼 과제가 재현 방법을 안 줘서
    모델이 `repro_spec`을 비워 내면, 그 Finding은 E3(`validated`)에 못 올라 8단계(PoC
    생성)가 아예 안 돈다(F-9 `repro_spec_missing`). 하네스가 아는 것(후보 위치·sink)으로
    명세를 채워 **8단계가 돌게** 한다. ⚠ **E4(`proven`)는 이 출처와 무관하게 여전히 실행
    로그(runtime Evidence, 게이트 `p4`)를 요구한다** — 합성 명세만으로는 아무것도 실행되지
    않았으므로 proven이 되지 않는다. 그래서 「validated인데 합성 명세로 올라왔다」를 비교표가
    가를 수 있게 이 값을 Finding이 싣고 게이트가 trace에 남긴다.
    """

    MODEL = "model"
    HARNESS = "harness"


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


class ScoutSignal(StrEnum):
    """Scout v3가 `reason_codes[0]`의 접두로 내는 최종 판정 신호 (ADR 0012 결정 2).

    v3는 이 셋 중 하나로 점수를 묶는다(약한 review의 낮은 점수 하나만 예외 —
    `prompts/scout_*/v3/prefix.md`). `scout_response.signal_of()`가 문자열에서 뽑는다.
    """

    REVIEW = "triage_review"
    REJECT = "triage_reject"
    NEED_EVIDENCE = "triage_need_evidence"


class NeStopReason(StrEnum):
    """need_evidence 루프가 서는 사유 (ADR 0012 결정 2 · Notion 「Scout RAG baseline」).

    ADR 0012 결정 2가 정지를 **여섯**으로 든다 — 신호가 NE가 아님(`TRIAGE_REVIEW`·
    `TRIAGE_REJECT` 둘로 가른다) · 파싱 실패(`PARSE_FAILED`) · 3홉 초과(`MAX_HOP`) ·
    직전과 같은 요청 반복(`REPEAT_REQUEST`) · `requested_context`가 비었음(`EMPTY_REQUEST`) ·
    resolver가 하나도 못 줌(`NOTHING_RESOLVED`). 사유는 명세의 것이고 문자열 값은 이
    하네스가 붙였다. 그 여섯 밖은 `PEER_REVIEW`(같은 ADR이 붙인 제안 초안)와
    `CONTEXT_OVERFLOW`·`BUDGET_EXHAUSTED`·`GATEWAY_ERROR`(하네스가 더한 실행 실패)다.

    이 값을 내는 것은 `runner._run_ne_hops`(와 동료 확인)이고, 홉 이벤트(`SCOUT_HOP`)의
    `stop_reason`으로 남는다.
    """

    TRIAGE_REVIEW = "triage_review"
    TRIAGE_REJECT = "triage_reject"
    MAX_HOP = "max_hop"
    PARSE_FAILED = "parse_failed"
    REPEAT_REQUEST = "repeat_request"
    EMPTY_REQUEST = "empty_request"
    NOTHING_RESOLVED = "nothing_resolved"
    PEER_REVIEW = "peer_review"
    CONTEXT_OVERFLOW = "context_overflow"
    BUDGET_EXHAUSTED = "budget_exhausted"
    GATEWAY_ERROR = "gateway_error"


class PovStopReason(StrEnum):
    """PoV 툴 루프(Option C)가 서는 사유. `NeStopReason`을 미러한 모델-주도 루프다.

    루프 제어 셋 — `SUBMITTED`(모델이 submit) · `REPEAT_SPEC`(이미 낸 gen과 같은 PoC 바이트 재생성)
    · `MAX_HOP`(홉 상한 소진). 홉 호출 예외 셋(`_pov_classify_exception`이 가른다, pov 추가형
    계약대로) — `PARSE_FAILED`(스키마 파싱 실패 · ⏵ 2026-10-11 **마지막 홉에서 선다** · ⏵ 2026-10-12
    **또는 전 시도가 `finish_reason == "length"`로 끝난 홉에서도 선다** — 그 밖의 앞 홉 실패는 홉
    하나를 쓰고 루프가 다시 묻는다, #550. 둘은 값이 같고 이벤트의 `parse_truncated` 키로 가른다)
    · `CONTEXT_OVERFLOW` (크기 거절) · `GATEWAY_ERROR`(버릴 수 있는 게이트웨이).
    **서버 쪽 사건(status 없음·5xx)과 예산 소진은 흡수하지 않고 올려 보낸다** — run의 사실이라
    여기 값이 없다(`NeStopReason`이 `BUDGET_EXHAUSTED`를 흡수한 것과 다른 점: pov는 추가형이라
    run-정지 사건을 안 삼킨다).

    이 값을 내는 것은 `runner._run_pov_hops`이고, 홉 이벤트(`POV_HOP`)의 `stop_reason`으로
    남는다(`NeStopReason` ↔ `SCOUT_HOP`과 같은 짝).
    """

    SUBMITTED = "submitted"
    REPEAT_SPEC = "repeat_spec"
    MAX_HOP = "max_hop"
    PARSE_FAILED = "parse_failed"
    CONTEXT_OVERFLOW = "context_overflow"
    GATEWAY_ERROR = "gateway_error"


class AuditorStopReason(StrEnum):
    """Auditor(5단계) 도구 홉 루프가 서는 사유 (이슈 #419).

    `PovStopReason`을 미러한 모델-주도 루프다. **넷이다** — 노드가 내는 것이 이게 전부이고 홉
    이벤트(`EventKind.AUDITOR_HOP`)의 `stop_reason`으로 남는다(`NeStopReason` ↔ `SCOUT_HOP`,
    `PovStopReason` ↔ `POV_HOP`과 같은 짝). 앞 셋은 **모델이 낸 답**이고 `CONTEXT_OVERFLOW`는
    서버가 낸 거절이다 - 모델이 아무것도 못 낸 홉이라 산문이 말하는 「답 없이 끝나는 경우」에
    들지 않는다.

    - `ANSWERED` — 모델이 `answer`로 Finding을 냈다. 정상 종료이고, 종전 한 방 경로와
      같은 출력(Finding 하나)으로 5단계를 빠져나간다.
    - `REPEAT_REQUEST` — **앞선 어느 홉과** 같은 요청을 또 냈다(같은 도구, 또는 같은
      `(kind, target)` 집합 - 직전 홉만 보던 판은 A→B→A→B가 안 섰다, #514). NE 루프의
      `NeStopReason.REPEAT_REQUEST`와 같은 자리다 - 단 NE는 아직 직전 홉만 비교하고
      (`runner._run_ne_hops`의 `previous_keys`) Auditor는 앞선 모든 홉을 비교한다(#514).
      그쪽은 요청 키를 `frozenset((kind, target))`으로 보고(`runner._run_ne_hops`), pov는
      조립 **바이트**로 본다(`PovStopReason.REPEAT_SPEC`). Auditor는 액션이 둘(`lookup`·`probe`)이라
      비교 축도 둘이다. **Finding이 없다.**
    - `MAX_HOP` — 상한(`thresholds.auditor_tool_hops`)을 조사만으로 소진했다. **Finding이
      없다.**
    - `CONTEXT_OVERFLOW` — 그 홉의 **모델 호출이 크기 거절**(`model_client.is_size_rejection` -
      413 또는 컨텍스트 초과 문구가 실린 400)당했다(#514). 홉 16개가 쌓인 입력이 창을 넘을 수 있다
      (`config.py`의 `auditor_tool_hops` 주석의 계산값). `PovStopReason.CONTEXT_OVERFLOW`와 같은
      뜻이다. 이 홉은 응답이 없어 `action`이 없고 `error`·`error_detail`이 대신 실린다. **Finding이
      없다.**

    ⚠ **Finding이 없는 정지는 셋이다 - `REPEAT_REQUEST` · `MAX_HOP` · `CONTEXT_OVERFLOW`.** 셋 다
    후보를 폐기하지 않고 `queued`에 두며(`nodes/auditor._queue_candidate`) 전이 표의 사유는 **C-5
    `capsule_expansion_exhausted`다**(`nodes/auditor._NO_FINDING_REASON` - 딱 맞는 행이 없어 가장
    가까운 것을 쓴 것이다. C-6·C-7은 파싱 실패·예산 소진이라는 거짓 진단이 된다). 그래서 `queued`
    후보의 사유만으로는 이 정지와 진짜 캡슐 확장 소진이 안 갈린다 - **홉 이벤트의 `stop_reason`을
    조인한다.** 「Finding 없음」을 세려면 값을 다 센다(`max_hop`만 세면 `repeat_request`로 선 후보를
    놓친다). 앞 둘은 `AuditorHopLimit`을 올려 러너가 후보 단위로 받고, `CONTEXT_OVERFLOW`는 **원래
    게이트웨이 예외가 그대로 올라가** 러너의 `_drop`이 받는다(`target_skipped` · 연속 폐기 카운터가
    크기 거절을 안 센다).

    ⚠ **`nothing_resolved`는 없다.** `lookup`이 이름을 하나도 못 채운 것은 정지 사유가 아니다 — 못
    찾은 이름은 `Not resolved:` 줄로 되먹여 모델이 이름을 고치게 한다. 그 사실은 홉 이벤트의
    `resolved`(0)와 `unfilled`·`truncated`가 센다. 값을 미리 두면 그것을 세는 소비자가 영영 0건인
    행을 기대한다.

    ⚠ **노드는 모델 호출의 실패를 삼키지 않는다.** 스키마 파싱 실패·크기 거절·예산 소진은 5단계 본선
    사건이라(`PovStopReason`의 `PARSE_FAILED`·`GATEWAY_ERROR`에 해당하는 값이 여기 없다) 그대로 올려
    보내 C-6·C-7 전이와 예산 정지가 종전처럼 돈다(`nodes/auditor._call_model`이 `_queue_candidate`를
    부르고 다시 올린다). pov는 **추가형**이라 서버 쪽 사건만 올리고 나머지를 흡수했지만, Auditor는
    파이프라인 본선이어서 노드가 삼키면 러너의 연속 폐기 카운터와 성공 0 백스톱(#296)이 못 본다 -
    모든 후보가 크기 거절인 run이 완주로 읽힌다. 크기 거절만 사유와 이벤트(`CONTEXT_OVERFLOW`)를
    **더한 뒤** 올린다. 조회·도구 쪽 하네스 실패는 C-6을 안 단다(모델이 스키마를 못 맞춘 것이
    아니다) - 홉 이벤트의 `observation_error`로 남는다.
    """

    ANSWERED = "answered"
    REPEAT_REQUEST = "repeat_request"
    MAX_HOP = "max_hop"
    CONTEXT_OVERFLOW = "context_overflow"


class UnfilledReason(StrEnum):
    """resolver가 요청 하나를 못 채운 사유 (명세 §4.3 UNFILLED 사유 8개).

    뒤 넷(`PLACEHOLDER`·`NO_TARGET`·`NO_IDENTIFIER`·`NOT_IDENTIFIER`)은 **형식** 사유다 —
    target 문자열 자체가 조회할 수 있는 모양이 아니다. 앞 넷은 target은 멀쩡한데 그
    이름으로 저장소를 조회한 결과가 사유다. `FIXABLE_UNFILLED_REASONS`가 그 구별을
    코드로 든다 — "잘 쓴 이름이 전부 조회에 실패했다"(그래서 `nothing_resolved`)와
    "형식이 잘못됐다"를 가른다. 형식 사유가 있으면 모델에게 다시 물을 수 있으므로
    그 자리에서 멈추지 않는다.
    """

    OUTSIDE_REPO = "outside_repo"
    NOT_FOUND = "not_found"
    CLOSEST_NAME = "closest_name"
    LIBC = "libc"
    PLACEHOLDER = "placeholder"
    NO_TARGET = "no_target"
    NO_IDENTIFIER = "no_identifier"
    NOT_IDENTIFIER = "not_identifier"


FIXABLE_UNFILLED_REASONS: frozenset[UnfilledReason] = frozenset(
    {
        UnfilledReason.PLACEHOLDER,
        UnfilledReason.NO_TARGET,
        UnfilledReason.NO_IDENTIFIER,
        UnfilledReason.NOT_IDENTIFIER,
    }
)


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
    # v3 신호 판정(ADR 0012 결정 2 — C-3에 사유 행을 늘린다). 신호가 없으면(v1·v2, 또는
    # 세 신호 밖 접두) 위 `BELOW_SCOUT_THRESHOLD`로 지금처럼 판정한다.
    SCOUT_REJECT = "scout_reject"
    NEED_EVIDENCE_UNRESOLVED = "need_evidence_unresolved"
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
    # 재현엔 성공했으나 게이트 등급이 E4 미만이라 proven으로 올리지 않는다 (F-10b).
    EVIDENCE_BELOW_PROVEN = "evidence_below_proven"
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
    # 대응이 다르기 때문이다: 앞은 서버·연결 쪽을 봐야 하고, 뒤는 서버가 살아 있는데 후보가
    # 계속 죽는 것이라 프롬프트·예산·스키마를 봐야 한다.
    # ⚠ **앞(`MODEL_GATEWAY_UNAVAILABLE`)은 「서버가 죽었다」보다 넓다** — 후보 하나의 답이 아닌
    # `ModelGatewayError` 전부다(`runner._is_droppable`이 거짓인 것): 상태 없음(끊김·타임아웃·
    # 계약 밖 200)·5xx·`400·413·422` 밖의 4xx(401·404·429 등). 가르는 것은 trace의
    # `model_call.error_detail`이다.
    MODEL_GATEWAY_UNAVAILABLE = "model_gateway_unavailable"
    CONSECUTIVE_TARGET_FAILURES = "consecutive_target_failures"
    # 단계 경계 교대(ADR 0012 Phase 6.5)가 다음 단계 모델을 못 띄웠다 — 교대 명령 실패·마감
    # 초과·준비 확인의 창 불일치(`ModelSwapError`). 위 `MODEL_GATEWAY_UNAVAILABLE`과 가르는 것은
    # 대응이 다르기 때문이다: 그쪽은 떠 있던 서버와의 통신이 어긋난 것이라 서버·연결을 보고,
    # 이쪽은 띄우지를 못한 것이라 장비 파일(`config/serving/<device>.yaml`)과 교대 명령의 로그를
    # 본다.
    MODEL_SWAP_FAILED = "model_swap_failed"
    TOOL_POLICY_VIOLATION = "tool_policy_violation"
    CAPSULE_EXPANSION_EXHAUSTED = "capsule_expansion_exhausted"
    # Queue 규칙 (architecture.md §3)
    NEW_EVIDENCE_REQUEUE = "new_evidence_requeue"
    # 재평가 횟수 상한(config의 requeue_max) 초과. 폐기하지 않고 queued를 유지한다.
    REQUEUE_LIMIT_REACHED = "requeue_limit_reached"
    # 🔴 **예상 밖 예외로 멈춘 run** (최후 방어선 — `runner.run_pipeline`의 맨 `except Exception`).
    # ⚠ **범위는 `_analyze`(1~8단계)다** — `_analyze`가 정상으로 끝난 뒤 후처리(GPU 경고·게이트
    # 재등급·리포트 렌더·`finish_run`)에서 **처음** 나는 예외는 이 사유를 못 받고 매니페스트도 안
    # 남는다. `_analyze`에서 난 뒤에는 후처리를 건너뛰므로 2차 예외가 원 예외를 덮지 않는다.
    # 위 다섯 사유(`BUDGET_EXHAUSTED`·`MODEL_SWAP_FAILED`·`MODEL_GATEWAY_UNAVAILABLE`·
    # `CONSECUTIVE_TARGET_FAILURES`·`SCHEMA_PARSE_FAILED`)는 **예상한** 형이고 이것은 그 밖 전부다.
    # 기존 27개 중 맞는 것이 **없어서** 새로 만들었다 — `MODEL_GATEWAY_UNAVAILABLE`로 적으면
    # 「서버가 죽었다」는 거짓 진단이 장부에 남고, 그 run을 보는 사람이 서버 로그를 뒤진다.
    # ⚠ 이 사유가 붙은 run은 **예외가 올라간 run이다** — 러너가 매니페스트를 쓴 뒤 다시 던지므로
    # 산출물은 남지만 run은 실패다: `RunResult`가 안 나와 `run`은 트레이스백(종료 코드 1)으로
    # 끝난다. 위 다섯 중 예산 소진은 정상 종료(0)이고 나머지 넷은 `RunResult`로 돌아와 종료 코드
    # 3이다(#533) — 종료 코드가 0인 정지 사유는 사용자 상한 둘(`USER_STOP_REQUESTED`·`DRY_RUN`)과
    # 예산 소진뿐이다.
    UNFORESEEN_FAILURE = "unforeseen_failure"
    # 제보 (disclosure-policy.md) — 자동 전이 없음, 사람 승인으로만
    HUMAN_VERIFIED = "human_verified"


class PathStepKind(StrEnum):
    """Finding.path 각 단계의 종류 (agent-contracts.md §3 Finding 예시)."""

    SOURCE = "source"
    PROPAGATE = "propagate"
    SINK = "sink"
