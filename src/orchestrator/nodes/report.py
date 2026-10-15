"""9단계 Dedupe & Report — 규칙 기반 중복 제거와 템플릿 리포트.

architecture.md §3의 9단계는 "규칙 기반 중복 제거, 템플릿 리포트 생성"이다. 모델을
부르지 않는다 — 리포트 문구가 모델마다 달라지면 조건 간 비교에서 사람이 읽는 산출물이
흔들린다.

**`proven`을 `reported`로 자동 전이시키지 않는다.** 전이 표 F-15는 사람 2인 검증으로만
일어난다(disclosure-policy.md). 이 노드는 상태를 바꾸지 않고 읽기만 한다.
"""

from collections import Counter
from dataclasses import dataclass, field

from src.orchestrator.context import RunContext
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.enums import FindingState, PathStepKind
from src.schemas.finding import Finding

# 리포트에 싣는 상태 순서. 위에 있을수록 먼저 나온다.
_STATE_ORDER = (
    FindingState.PROVEN,
    FindingState.VALIDATED,
    FindingState.HYPOTHESIZED,
    FindingState.QUEUED,
)

# 등급 대표 우선순위 (#101). 값이 작을수록 높은 등급이라 dedupe에서 대표로 남는다.
# 문자열 비교("E10" > "E3" 같은 함정)를 피하려 명시 순서로 둔다.
_GRADE_ORDER = {"E4": 0, "E3": 1, "E2": 2, "E1": 3, "E0": 4}
# `grades`에 키가 없을 때. **"E0"으로 접지 않는다** — 게이트가 구조적으로 E0을 줄 수 없는
# Finding(근거 + `repro_spec`)을 "등급 없음"인데 "E0"이라 주장하면, `index_graph`가
# "없다 vs 0건"에 대해 거부한 그 구별이 리포트에서 깨진다. 실 run은 러너가 전량 채운다.
_GRADE_UNKNOWN = "-"
# dedupe 정렬에서 "등급 없음"은 가장 낮게 둬 등급 있는 대표에 밀리게 한다.
_GRADE_RANK_UNKNOWN = 99
# 요약 줄에 등급을 높은 것부터 적는다 (PR #37).
_GRADE_DISPLAY_ORDER = ("E4", "E3", "E2", "E1", "E0")
# PoV 연속 `inspect` 최장 줄이 Finding을 나열하는 하한(#619 (d)). 줄이 Finding 수에 비례해 자라지
# 않게 하는 표시 규칙일 뿐 판정 임계값이 아니다 - 값은 manifest에 Finding별로 전부 남는다.
_INSPECT_LISTED_MIN = 2


@dataclass(frozen=True)
class Report:
    """리포트 한 건. 렌더링은 `render()`가 하고 지표 집계는 trace에서 따로 한다."""

    run_id: str
    findings: tuple[Finding, ...]
    duplicates: int
    # 4단계에 못 올라간 수 (W2). ⚠ **아래 둘의 단위가 다르다 — 같은 이름으로 부르지 않는다.**
    #
    # - `deferred`는 **대상** 수다. `candidate_max` 컷이 `_plan_targets`에서, 즉 **팬아웃
    #   전에** 정해지므로 풀에서도 대상 단위 그대로다.
    # - `dropped`는 **시도**(`대상 × Scout 자리`) 수다. `_drop`이 그 시도마다 불린다
    #   (ADR 0010 §3). 단일 `scout`에서는 자리가 하나라 둘이 같고 **풀에서만 갈린다.**
    #
    # ⚠ **`dropped`로 「안 본 대상」을 셀 수 없다** — 멤버 하나가 죽어도 형제가 그 대상을
    # 보므로 시도가 폐기돼도 대상은 분석된다. 그 값은 아래 `unanalyzed_targets`가 낸다(#193 ①).
    # **"무엇을 찾았는가"만 적고 "무엇을 안 봤는가"를
    # 빼면, 예산에 잘린 run의 리포트가 전량을 본 run의 리포트와 같은 얼굴을 한다** —
    # `index_graph`가 "없다 vs 0건"에 대해, `retrieval_truncated`가 "이벤트 부재는
    # 안 잘림"에 대해 거부한 것과 같은 모양이다.
    #
    # 목록이 아니라 수만 싣는다. 목록은 trace의 `target_skipped`에 있고, 리포트는
    # 사람이 읽는 요약이라 수백 줄을 여기 옮기면 Finding이 묻힌다.
    deferred: int = 0
    dropped: int = 0
    # **모든 자리가 실패해 후보를 하나도 못 낸 대상 수** (#193 ①). `dropped`(시도)·`deferred`
    # (예산 컷)와 **또 다른 단위**다 — 팬아웃된 대상 중 어느 멤버도 후보를 못 낸 것이고,
    # 러너가 `dispatched_targets − 후보 낸 대상`으로 센다. 단일 `scout`에서도 성립한다.
    unanalyzed_targets: int = 0
    # 7단계에 못 올라간 Finding 수 (#98 (b)). **위 둘과 같은 줄에 넣지 않는다** — 그쪽 단위는
    # 대상 또는 시도(바로 위)이고 이쪽은 Finding이다.
    # 사람이 읽는 산출물에서 분모를 섞는 것이 trace에서
    # 섞는 것보다 나쁘다.
    #
    # 이 줄이 없으면 절단된 Finding이 **안 보이는 게 아니라 잘못 보인다.** F-9가
    # `hypothesized → hypothesized`라 7단계에 간 건과 못 간 건이 아래 같은 묶음에 나란히
    # 찍힌다 — 실측 `run_20260825_0001`에서 `hypothesized 44건` 중 **36건이 7단계에 못
    # 갔는데** 리포트에 그 사실이 없었다.
    critic_deferred: int = 0
    # 위 수를 만든 상한. **값을 함께 싣는다** — 러너의 run 경고가 `critic_top_n(8)`처럼
    # 값을 찍으므로 이름만 적으면 두 산출물이 같은 사실을 다르게 말한다. 그리고 report.md는
    # config 없이 단독으로 읽히는 문서라 여기 없으면 "36이 상한 8의 결과인지 80의 결과인지"를
    # 이 파일만 보고는 알 수 없다.
    critic_top_n: int = 0
    # finding_id → 등급 값("E3" 등) (#101·PR #37). **러너가 `gate.evaluate`로 계산해 넘긴다.**
    # Finding에 등급 필드가 없어 등급은 게이트 판정이라 trace에만 있었고, 그래서 이 노드가
    # 대표를 고를 때 등급을 못 봐 E3가 사라졌고(#101) 최종 산출물이 등급을 못 실었다(PR #37).
    # 노드가 다시 매기지 않고 러너 값을 받는 것은 `deferred`·`critic_deferred`와 같은 이유다.
    grades: dict[str, str] = field(default_factory=dict)
    # need_evidence 루프가 hop≥1에서 흡수한 예외 건수, `NeStopReason` 값별(ADR 0012 결정 2 —
    # #95). 조용히 두지 않는다 — 흡수는 그 좌석이 직전 홉 판정으로 finish했다는 뜻이라,
    # 몇 건이 어떤 사유로 그랬는지가 안 보이면 "루프가 잘 돌았다"와 "매번 흡수하며 버텼다"가
    # 리포트에서 안 갈린다. `deferred`·`dropped`와 같은 자리다 — 러너가 센 값을 그대로 받는다.
    ne_absorbed: dict[str, int] = field(default_factory=dict)
    # 8단계 PoV가 어느 사유로 섰나 — finding_id → 사유 문자열 (이슈 #357·#358). 러너의
    # `_Accumulator.pov_results`를 그대로 받는다(`ne_absorbed`와 같은 자리).
    #
    # **수가 아니라 이 맵을 받는다.** 시도 수는 `len(...)`, 사유별 수는 `Counter(...values())`라
    # 「시도 수 == 사유별 합」이 **구조적으로** 참이다 — 수 둘을 받으면 그 둘이 어긋난 run을
    # 리포트가 그대로 찍을 수 있고, 그러면 사람이 읽는 산출물에서 분모가 섞인다
    # (`critic_deferred`를 `deferred`와 나눈 것과 같은 판단).
    #
    # 비면 줄을 안 낸다 — 빈 맵은 PoV가 시도되지 않았다는 뜻이다(8단계 앞에서 멈췄거나, PoV 대상이
    # 0건이거나 — `validated`가 0건이고 `pov_attempt_top_n`이 0이거나 그 추가 대상 풀이 비었다 —,
    # 프로필에 pov 좌석이 없다). 그 run에 0건 줄이 나오면 장식이다.
    pov_results: dict[str, str] = field(default_factory=dict)
    # 버린 finding_id → 그것을 대신해 남은 대표 finding_id (이슈 #101 잔여). `duplicates`의 근거다 —
    # 수만 있으면 **무엇과 합쳐졌는지**가 어디에도 없어 정답 대조에서 사라진 Finding(실측
    # `run_20260827_0004`의 `find_0005`)을 추적할 수 없었다. 같은 사실을 trace의
    # `finding_deduped`가 건당 1행으로 싣는다. `render()`는 목록을 안 찍는다 — `deferred`의
    # 목록을 `target_skipped`에 둔 것과 같은 이유다(리포트는 요약이라 수백 줄이 Finding을 묻는다).
    merged_into: dict[str, str] = field(default_factory=dict)
    # 6단계 게이트의 위치 겹침 횟수 `{gated, static_ok, location_ok}` (이슈 #563) - 러너가
    # `gate_eval`(stage=6) 행에서 센 값을 그대로 받는다(`RunRecord.location_overlap`과 같은 값).
    # 비면 줄을 안 낸다 - 게이트가 안 돈 run에 「0/0」을 찍으면 측정한 0으로 읽힌다.
    # `gated`가 0이면(6단계 행은 있으나 전부 `p0` 지름길) 횟수가 아니라 **미측정**으로 찍는다 -
    # 같은 이유다. 줄을 아예 빼지 않는 것은 「안 쟀다」가 리포트에서 사라지면 읽는 쪽이
    # 게이트가 안 돈 run과 구별할 수 없기 때문이다. 같은 말을 `docs/evaluation.md` §5.1이 한다.
    location_overlap: dict[str, int] = field(default_factory=dict)
    # trace에서 센 **분모 전용** 집계 셋(#613·#618, 셋째는 #619 (d)) — 러너가 `RunRecord`에 싣는
    # 값을 그대로 받는다(산식은 `trace.py`). `None`은 그 종류의 trace 행이 없었다(안 쟀다)이고
    # 「미측정」 줄로 찍는다 - `0`을 지어 찍지도, 줄을 빼지도 않는다(CLAUDE.md §0). 임계값이
    # 아니다: 값은 실험팀 몫이다.
    schema_failure_max_run: dict[str, int] | None = None
    schema_recovered_calls: dict[str, int] | None = None
    pov_inspect_max_run: dict[str, int] | None = None
    # 위 셋 중 집계가 던져 `None`이 된 키 → 사유(`RunRecord.trace_aggregate_failures`). 그 키의 줄은
    # 「미측정」이 아니라 「집계 실패」로 찍는다.
    trace_aggregate_failures: dict[str, str] | None = None
    # 채점기를 붙인 run의 최종 PoC 한 줄(`oracle_final` 이벤트의 요약) - 러너가 trace에서 뽑아
    # 넘긴다. 키: `status`·`designation`·`reason`·`error`·`subject`(finding_id)·`poc_sha256`.
    # `None`이면 채점기가 안 돌았거나 8단계의 최종 단계에 못 닿았다 - 줄을 안 낸다
    # (`none`/`failed`는 줄을 내고 사유를 적는다).
    oracle_final: dict[str, str | None] | None = None

    def _oracle_lines(self) -> list[str]:
        """최종 PoC 줄. 판정 값은 trace 밖 `oracle/verdict.json`이 든다 - 여기서 지어내지 않는다."""
        final = self.oracle_final
        if final is None:
            return []
        parts = [f"상태 {final.get('status')}"]
        if final.get("designation"):
            parts.append(f"지정 근거 {final['designation']}")
        why = final.get("reason") or final.get("error")
        if why:
            parts.append(f"사유 {why}")
        subject = final.get("subject")
        if subject:
            folded = self.merged_into.get(subject)
            where = f"{subject} (중복 제거로 대표 {folded}에 합쳐짐)" if folded else subject
            parts.append(f"대상 Finding {where}")
        if final.get("poc_sha256"):
            parts.append(f"PoC sha256 {final['poc_sha256']}")
        pointer = " (판정 값은 oracle/verdict.json)" if final.get("status") == "scored" else ""
        return ["### 최종 PoC (채점기)", "", " · ".join(parts) + pointer, ""]

    def _grade_of(self, finding: Finding) -> str | None:
        """이 Finding의 등급. `grades`에 없으면 `None`("등급 없음")이다 — "E0"이 아니다."""
        return self.grades.get(finding.finding_id)

    def _aggregate_lines(self) -> list[str]:
        """분모 전용 집계 셋(#613·#618, 셋째는 #619 (d))의 줄. `None`은 **생략이 아니라 「미측정」
        줄**이다 - 안 쟀다와 안 그렸다가 페이지에서 갈려야 한다(CLAUDE.md §0). 임계값이 아니다."""
        failures = self.trace_aggregate_failures or {}

        def unmeasured(key: str) -> str:
            # `None`은 「행이 없다」와 「집계가 죽었다」 둘 다다 - 사유가 있으면 그것을 적는다.
            if key in failures:
                return f"집계 실패: {failures[key]}"
            return "미측정 — trace 행이 없거나 집계가 실패했다(실패면 run 경고에 사유)"

        def per_key(label: str, counts: dict[str, int] | None, key: str) -> str:
            if counts is None:
                return f"{label}: {unmeasured(key)}"
            return f"{label}: " + " · ".join(f"{key} {n}" for key, n in counts.items())

        runs = self.pov_inspect_max_run
        if runs is None:
            inspect_line = (
                "PoV 연속 inspect 최장(Finding별, 분모만): "
                + unmeasured("pov_inspect_max_run_by_finding")
            )
        else:
            # 줄이 Finding 수에 비례해 자라지 않게 한다 - 최장 2 이상만 나열하고 나머지는 건수다.
            long_runs = {fid: n for fid, n in runs.items() if n >= _INSPECT_LISTED_MIN}
            rest = len(runs) - len(long_runs)
            listed = " · ".join(f"{fid} {n}" for fid, n in long_runs.items())
            listed = listed or f"최장 {_INSPECT_LISTED_MIN} 이상 없음"
            inspect_line = (
                f"PoV 연속 inspect 최장(Finding별, 분모만 - 최장 {_INSPECT_LISTED_MIN} 이상인 "
                f"Finding만 나열): {listed} · 나열하지 않은 Finding {rest}건 "
                f"(최장 {_INSPECT_LISTED_MIN - 1} 이하)"
            )
        return [
            "### 분모 전용 집계 (임계값 아님)",
            "",
            per_key(
                "연속 스키마 파싱 실패 최장(역할별, 분모만)",
                self.schema_failure_max_run,
                "schema_failure_max_run_by_role",
            ),
            "",
            per_key(
                "스키마 재시도로 살아난 호출(역할별, 분모만)",
                self.schema_recovered_calls,
                "schema_recovered_calls_by_role",
            ),
            "",
            inspect_line,
            "",
        ]

    def render(self) -> str:
        lines = [f"# 취약점 분석 리포트 — {self.run_id}", ""]
        # **등급 분포를 맨 위에 싣는다 (PR #37).** 이 run의 성과(예: 8단계 E3 8건)가 여기
        # 없으면 리포트만 읽는 사람은 trace를 파야 그것을 본다 — F-9가 상태를 안 바꿔
        # E3가 `queued`로 찍히기 때문이다.
        if self.findings:
            dist = Counter(self._grade_of(f) for f in self.findings)
            parts = [f"{g} {dist[g]}건" for g in _GRADE_DISPLAY_ORDER if dist[g]]
            if parts:
                lines += [f"등급 분포: {' · '.join(parts)}", ""]
        if self.location_overlap:
            n = self.location_overlap
            # 비율이 아니라 횟수다. `location_ok` 거짓은 「안 겹쳤다」와 「위치가 없다」를 못 가른다
            # (`RunRecord.location_overlap` 주석) - 그래서 `static_ok`를 나란히 적는다.
            if n["gated"] == 0:
                # 분모가 0이라 「0건 중 … 0건」은 **잰 0**으로 읽힌다(CLAUDE.md §0).
                lines += [
                    "정적 신호 위치 겹침(6단계): 미측정 — 6단계 후보가 전부 `p0`에서 "
                    "단락했다(잰 0이 아니다)",
                    "",
                ]
            else:
                lines += [
                    f"정적 신호 위치 겹침(6단계): {n['gated']}건 중 후보 줄과 겹침 "
                    f"{n['location_ok']}건 · 정적 신호 있음 {n['static_ok']}건 "
                    "(`location_ok` 거짓은 겹치지 않음과 위치 없음을 가르지 않는다)",
                    "",
                ]
        if self.duplicates:
            # 무엇과 합쳤는지는 trace에 건당 1행으로 있다(`merged_into` 필드 주석).
            lines += [
                f"중복 제거: {self.duplicates}건 (무엇과 합쳤는지는 trace의 `finding_deduped`)",
                "",
            ]
        if self.deferred or self.dropped:
            lines += [
                # ⚠ **둘을 한 이름으로 묶지 않는다 — 단위가 다르다**(위 필드 주석).
                # `deferred`는 대상, `dropped`는 시도다. 묶으면 둘 중 하나는 반드시
                # 거짓이 된다: 「대상」으로 부르면 **분석된 대상을 못 봤다고 말하고**
                # (실측: 대상 2건이 전부 분석됐는데 `dropped`가 2였다), 「시도」로 부르면
                # `deferred`를 자리 수만큼 **과소**로 말한다(실측: 풀에서 미수행 시도 3인데
                # `deferred`는 1). 단일 프로필에서는 둘이 같아 이 구분이 안 보인다.
                f"넘긴 대상(예산 초과): {self.deferred}건 · "
                f"완주하지 못한 시도(대상 × Scout 자리, 호출 실패): {self.dropped}건 "
                f"(사유는 trace의 `target_skipped`)",
                "",
            ]
        if self.unanalyzed_targets:
            # `dropped`(시도)와 다른 단위 — 모든 자리가 실패해 후보를 하나도 못 낸 대상 수.
            lines += [
                f"분석되지 않은 대상(모든 Scout 자리 실패): {self.unanalyzed_targets}건 "
                f"(어느 멤버로 무엇이 실패했는지는 trace의 `target_skipped.scout_member`)",
                "",
            ]
        if self.ne_absorbed:
            # 사유별로 적는다 — 한 수로 묶으면 "크기 문제로 버텼다"와 "게이트웨이가 흔들렸다"가
            # 안 갈린다(위 필드 주석과 같은 판단).
            parts = " · ".join(
                f"{reason} {count}건" for reason, count in sorted(self.ne_absorbed.items())
            )
            lines += [
                f"need_evidence 홉 예외 흡수: {parts} (그 좌석은 직전 홉 판정으로 finish했다)",
                "",
            ]
        if self.pov_results:
            # 사유별로 적는다 — 위 `ne_absorbed`와 같은 판단. 한 수로 묶으면 "상한에서 헛돌았다"와
            # "스키마를 못 맞췄다"가 안 갈리고, 그러면 PoC가 0건인 run에서 **왜** 0인지가 사람이
            # 읽는 산출물에 없다. 시도 수와 사유별 수를 **같은 맵에서** 뽑으므로 둘이 어긋나지
            # 않는다(위 필드 주석).
            stops = Counter(self.pov_results.values())
            parts = " · ".join(f"{reason} {count}건" for reason, count in sorted(stops.items()))
            lines += [
                f"PoV 정지 사유 (시도 {len(self.pov_results)}건): {parts}",
                "",
            ]
        if self.critic_deferred:
            # **앞머리 명사구에 잘린 사유를 넣는다.** "반박받지 못한 Finding"으로 적으면
            # `insufficient_evidence`로 6단계에서 떨어진 건(실측 11건)도 같은 이름에 들어가
            # 이 수가 47이어야 맞는 문장이 된다. 사유가 다른 둘을 한 이름으로 묶으면 #98이
            # 지적하는 구별이 리포트에서 다시 합쳐진다 — `target_skipped`가 `reason`을 필드로
            # 두는 것과 같은 이유다.
            lines += [
                f"`critic_top_n`으로 잘린 Finding: {self.critic_deferred}건 "
                f"(상한 {self.critic_top_n} · 목록은 trace의 `critic_skipped`)",
                "",
            ]
        lines += self._oracle_lines()
        lines += self._aggregate_lines()
        for state in _STATE_ORDER:
            group = [f for f in self.findings if f.state is state]
            if not group:
                continue
            lines += [f"## {state.value} ({len(group)}건)", ""]
            lines += [_entry(finding, self._grade_of(finding)) for finding in group]
            lines.append("")
        if not self.findings:
            lines += ["보고할 Finding이 없다.", ""]
        return "\n".join(lines)


def dedupe(
    findings: list[Finding],
    grades: dict[str, str] | None = None,
    files: dict[str, str | None] | None = None,
) -> tuple[list[Finding], int]:
    """같은 CWE·같은 파일·같은 대상 함수는 한 건으로 본다. 버린 수를 돌려준다.

    무엇과 합쳤는지(버린 것 → 대표)가 필요하면 `dedupe_with_merges`를 부른다 — 이 함수는 그
    결과에서 수만 남긴 것이고 대표 선택 규칙은 그쪽 docstring이 정본이다.
    """
    kept, _ = dedupe_with_merges(findings, grades, files)
    return kept, len(findings) - len(kept)


def dedupe_with_merges(
    findings: list[Finding],
    grades: dict[str, str] | None = None,
    files: dict[str, str | None] | None = None,
) -> tuple[list[Finding], dict[str, str]]:
    """`dedupe`와 같은 판정에 **버린 finding_id → 대표 finding_id**를 더해 돌려준다 (#101 잔여).

    대표는 같은 키에서 처음 남은 것이다 — 아래 정렬이 등급·`finding_id` 순이므로 그 키의
    최고 등급(동급이면 가장 작은 id)이다. 버린 쪽은 언제나 대표보다 뒤에 정렬된 것이다.

    **대표는 등급이 높은 것을 남긴다 (#101 b).** 예전에는 `finding_id` 오름차순으로 처음
    것을 남겨 등급을 안 봤고, 그래서 E3가 낮은 등급 대표에 밀려 리포트에서 사라졌다
    (실측 62→4에서 E3 6건). 등급 우선으로 바꿔도 동급은 `finding_id`로 가르므로
    **재현성은 그대로다** — 어느 것이 남는지가 실행마다 흔들리지 않는다.

    **키는 (CWE, 파일, 대상 함수)다 (#101 a).** sink 심볼만 보면 저장소 전역에서 `memcpy`를
    쓰는 서로 다른 함수가 한 건으로 합쳐졌다(#77의 `gz_load`·`gz_avail`·`gz_look`·`gz_write`
    넷이 전부 `('CWE-787','input','memcpy')`). 대상 함수는 경로의 PROPAGATE 칸이고, 파일은
    러너가 `Candidate.location`에서 얻어 넘긴다 — `Candidate.location.symbol`은 모델이 쓴
    값이고(⏵ 2026-10-13 — `null`이면 하네스가 후보 줄을 품는 함수로 채운다, 이슈 #633) 그 도장이
    못 도는 경우가 있어 여전히 `null`일 수 있다. 그래서 파일만 쓴다.
    """
    grades = grades or {}
    files = files or {}
    kept: list[Finding] = []
    # 키 → 그 키의 대표 finding_id. 예전엔 `set`이라 버리는 순간 대표가 누구인지 잃었다.
    seen: dict[tuple[str | None, str, str], str] = {}
    merged_into: dict[str, str] = {}

    def _rank(finding: Finding) -> tuple[int, str]:
        # 등급 높은 것 먼저, 동급은 finding_id로 — 둘 다 결정론.
        rank = _GRADE_ORDER.get(grades.get(finding.finding_id), _GRADE_RANK_UNKNOWN)
        return (rank, finding.finding_id)

    for finding in sorted(findings, key=_rank):
        key = _dedupe_key(finding, files.get(finding.finding_id))
        if key in seen:
            merged_into[finding.finding_id] = seen[key]
            continue
        seen[key] = finding.finding_id
        kept.append(finding)
    return kept, merged_into


def run(
    ctx: RunContext,
    *,
    findings: list[Finding],
    deferred: int = 0,
    dropped: int = 0,
    unanalyzed_targets: int = 0,
    critic_deferred: int = 0,
    critic_top_n: int = 0,
    grades: dict[str, str] | None = None,
    files: dict[str, str | None] | None = None,
    ne_absorbed: dict[str, int] | None = None,
    pov_results: dict[str, str] | None = None,
    location_overlap: dict[str, int] | None = None,
    schema_failure_max_run: dict[str, int] | None = None,
    schema_recovered_calls: dict[str, int] | None = None,
    pov_inspect_max_run: dict[str, int] | None = None,
    trace_aggregate_failures: dict[str, str] | None = None,
    oracle_final: dict[str, str | None] | None = None,
) -> Report:
    """중복을 제거하고 리포트 객체를 만든다. 상태는 바꾸지 않는다.

    `deferred`·`dropped`·`critic_deferred`·`ne_absorbed`·`pov_results`·`location_overlap`는 러너가
    센 값을 그대로 받는다 — 노드가 다시 세면 경고와 리포트가 다른 수를 말할 수 있다.
    `critic_top_n`도 같은 이유로 받는다: 노드가 `ctx.config`에서 다시 읽으면 러너의 경고와
    갈릴 여지가 생긴다. `grades`도 같다 — 등급은 게이트 판정이라 러너가 `gate.evaluate`로 매긴
    값을 받고 여기서 다시 매기지 않는다. `files`(finding_id → 파일)도 러너가
    `Candidate.location`에서 얻어 넘긴다 — dedupe 키가 파일을 가르는 데 쓴다(#101 a).

    버린 Finding마다 `finding_deduped`를 하나 낸다(#101 잔여) — `subject_id`가 버린 것이고
    `representative`가 대신 남은 것이다. 중복이 없는 run은 0건이다(**9단계에 도달한 run에서
    이벤트 부재 = 안 합쳤다** — 그 전에 크래시한 run은 0건이어도 모른다).
    """
    grades = grades or {}
    files = files or {}
    with ctx.stage(Stage.REPORT):
        kept, merged_into = dedupe_with_merges(findings, grades, files)
        for finding in sorted(findings, key=lambda f: f.finding_id):
            representative = merged_into.get(finding.finding_id)
            if representative is None:
                continue
            cwe, file, target = _dedupe_key(finding, files.get(finding.finding_id))
            ctx.trace.emit(
                EventKind.FINDING_DEDUPED,
                stage=Stage.REPORT,
                subject_kind="finding",
                subject_id=finding.finding_id,
                representative=representative,
                # 등급을 함께 싣는다 — 대표가 등급 우선으로 골랐는지(#101 b)를 이 행만 보고
                # 대조하려면 둘 다 있어야 한다. 등급이 없으면 `null`이다("E0"이 아니다).
                grade=grades.get(finding.finding_id),
                representative_grade=grades.get(representative),
                # 합친 근거인 키 세 칸. 셋째 칸은 CWE가 `None`이면 후보 ID다(`_dedupe_key`).
                cwe=cwe,
                file=file,
                target=target,
            )
    duplicates = len(findings) - len(kept)
    return Report(
        run_id=ctx.run_id,
        findings=tuple(kept),
        duplicates=duplicates,
        merged_into=merged_into,
        unanalyzed_targets=unanalyzed_targets,
        deferred=deferred,
        dropped=dropped,
        critic_deferred=critic_deferred,
        critic_top_n=critic_top_n,
        grades=dict(grades),
        ne_absorbed=dict(ne_absorbed) if ne_absorbed else {},
        pov_results=dict(pov_results) if pov_results else {},
        location_overlap=dict(location_overlap) if location_overlap else {},
        schema_failure_max_run=schema_failure_max_run,
        schema_recovered_calls=schema_recovered_calls,
        pov_inspect_max_run=pov_inspect_max_run,
        trace_aggregate_failures=trace_aggregate_failures,
        oracle_final=dict(oracle_final) if oracle_final else None,
    )


def _dedupe_key(finding: Finding, file: str | None) -> tuple[str | None, str, str]:
    """(CWE, 파일, 대상 함수). 셋이 같아야 한 건이다 (#101 a).

    ⏵ 2026-10-09 (이슈 #525 선택지 1) — CWE는 `None`일 수 있다(v11의 `no_signal`). 이 키는
    **정렬되지 않고** 집합 소속만 본다(`dedupe`의 `seen` — 순서는 `_rank`가 등급·`finding_id`로
    정한다). 그래서 `None`을 그대로 둔다 — 실제 CWE와는 다른 칸이다.
    ⚠ **셋째 칸은 `None`이면 후보 ID다.** `no_signal`은 `path`가 언제나 비고(v10·v11은 `audit`가
    null이라, v12는 노드가 비워서 — `nodes/auditor._finding_from_procedure`) 아래 `_target_symbol`이
    **모델이 쓴 제목**으로 떨어진다. 그대로 두면 키가 `(None, 파일, 제목)`이 되어 **서로 다른
    후보의 부정 결론이 제목만 같으면 한 건으로 합쳐진다** — v10까지는 지어낸 CWE가 서로 달라
    갈라졌을 수 있던 자리다. 그래서 `None`이면 대상 함수 대신 `candidate_id`를 쓴다: 다른 후보는
    안 묶이고, 같은 후보의 `no_signal` 둘만 한 건이다. CWE가 있는 키는 종전 그대로다. 실제 제목
    분포(합쳐졌을 빈도)는 미실측이다.

    대상 함수는 **경로의 PROPAGATE 칸**이다 — Finding을 서로 다르게 만드는 것이 그것이고,
    SOURCE는 호출자가 없으면 `"input"` 상수로 접히며(`fake_model`) SINK는 여럿이 공유한다
    (`memcpy`). PROPAGATE가 없으면 sink, 그것도 없으면 제목으로 대신한다.
    """
    if finding.cwe is None:
        return (None, file or "", finding.candidate_id)
    return (finding.cwe, file or "", _target_symbol(finding))


def _target_symbol(finding: Finding) -> str:
    """분석 대상 함수 — 경로의 PROPAGATE 심볼. 없으면 sink, 경로가 없으면 제목."""
    for step in finding.path:
        if step.step is PathStepKind.PROPAGATE:
            return step.symbol
    if finding.path:
        return finding.path[-1].symbol
    return finding.title


# `cwe`가 `None`인 Finding의 표시(`_entry`) — ⏵ 2026-10-09, 이슈 #525 선택지 1.
_NO_CWE = "CWE 없음"


def _entry(finding: Finding, grade: str | None) -> str:
    # **앞머리에 등급을 박는다 (PR #37).** state(`queued` 등)만 찍으면 E3에 도달한 Finding도
    # `queued`로 보인다 — F-9가 상태를 안 바꾸기 때문이다. 등급이 이 run의 성과다.
    # 등급이 없으면(`grades`에 키 없음) "E0"이 아니라 `-`로 찍는다(위 `_GRADE_UNKNOWN`).
    # ⏵ 2026-10-09 — `cwe`가 `None`(v11의 `no_signal`)이면 `_NO_CWE`로 적는다. `f"{None}"`은
    # `None`이라는 글자를 찍어 CWE 이름처럼 읽힌다. CWE가 있으면 종전과 같은 줄이다.
    cwe = finding.cwe if finding.cwe is not None else _NO_CWE
    return (
        f"- **[{grade or _GRADE_UNKNOWN}] {finding.title}** "
        f"({cwe}, confidence {finding.confidence:.2f})\n"
        f"  - id: `{finding.finding_id}` · 근거 {len(finding.evidence_refs)}건(하네스 관측)"
    )
