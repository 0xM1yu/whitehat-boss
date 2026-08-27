"""순차 러너 — 단계 0~9를 잇는다 (ADR 0002, engineering-guide.md §1).

**v1은 순차다.** 큐를 스레드로 돌리지 않는다. 그렇게 하려면 StateStore·FakeModelClient·
TraceWriter·BudgetLedger의 전제를 함께 풀어야 한다(`queue.py` docstring).

`src/adapters/`를 import 하지 않는다. 어댑터 조립은 `src/cli.py`가 하고, 러너는 아래
`Dependencies` Protocol과 `read_source` 콜러블만 받는다 — 그래야 "구현체 교체가 설정만으로
가능"이 성립한다(engineering-guide.md §3, `tests/test_policy.py`가 강제).

## 예산 소진

`BudgetExhausted`는 실패가 아니라 **정상적인 종료 경로**다(architecture.md §5). 예외로
죽지 않고 그때까지 만든 Finding으로 9단계 리포트까지 간 뒤 `stopped_after`를 채운다
(전이 표 R-2). 부분 결과라는 사실은 `RunRecord.is_complete`가 False인 것으로 남는다.

## 대상 지정 단위

**심볼 단위다(W2).** 1단계 인덱서 산출물에서 심볼마다 `Location(file, symbol,
line_start, line_end)`을 만들고 그 하나가 후보 하나가 된다. 인덱스가 없는
프로필(`dev_fake`)은 **파일 단위로 되돌아가고 그 사실이 `target_plan`에 남는다.**

그전까지는 artifact당 `Location(file=...)` 하나였고, `read_symbol_source()`가
`line_start is None`에서 파일 전체를 돌려주어 **큰 파일이 통째로 프롬프트에 실렸다** —
zlib의 `contrib/minizip/unzip.c` 67,662자가 그대로 실려 HTTP 400으로 죽은 것이 이것이다.

후보 수와 제거율의 **분모가 바뀌었다.** 심볼 인덱스 도입 전후의 지표를 직접 비교하지
않는다(STATUS.md §10).

## target 순서와 상한 — 정적 신호를 게이트가 아니라 순서로 쓴다

후보 단위가 심볼이 되면 수가 폭증한다. 그렇다고 "정적 신호가 붙은 것만 후보로"
거르지는 않는다 — 그러면 하네스가 semgrep이 이미 찾은 것만 찾게 되고, 이 프로젝트가
재려는 주장("LLM 여럿이 정적분석기가 놓친 것을 찾는다")의 측정 경로가 막힌다. 게다가
만들지 않은 후보는 `queued`도 `skipped`도 아니라 **기록에 아예 안 남는다.**

그래서 신호는 **순서**로 쓴다: 신호가 붙은 artifact의 심볼이 큐 상위, 나머지는 뒤,
`candidate_max`를 넘긴 나머지는 `target_skipped`로 **기록에 남긴다.**

**신호 판정이 심볼이 아니라 artifact 단위인 것은 지금 그것밖에 못 하기 때문이다.**
`Evidence.location`을 채우는 코드가 저장소에 없어(ToolRunner 두 구현 모두 기본값
`None`) 어느 심볼에 붙은 신호인지 알 수 없다. `fake_model._has_static_signal()`도 같은
입력을 보므로 이것이 "원래 하던 것"이기도 하다.

## 후보 하나의 실패가 run 전체를 죽이지 않는다

`ModelGatewayError`·`SchemaParseError`는 후보 단위 실패다. 예전에는 둘 다 뚫고 나갔고
`run_manifest.json`을 쓰는 블록이 그 뒤라 **산출물이 하나도 안 남았다** — zlib 때
`state.db`를 직접 열어야 했던 이유다.

단, 무조건 잡지 않는다. 후보 하나가 컨텍스트를 넘긴 것(그 후보만 버린다)과 서버가
죽은 것(순회를 멈춘다)은 대응이 다르고, 둘을 가르는 정보가 `ModelGatewayError.status`에
있다. 서버는 응답하는데 후보가 계속 죽는 경우를 위해 **연속 폐기 카운터**가 하나 더 있고,
그쪽은 사유가 갈린다(`_TooManyDrops`).

그 카운터가 세는 것은 **체계적 고장의 신호**뿐이다. 크기 거절(400·413)은 "이 후보가
크다"이지 "이 run이 고장 났다"가 아니라서 세지 않는다 — 세면 큰 함수 셋이 연달아 오는
것만으로 멈추고, 그것이 이 절이 견디겠다고 한 바로 그 경우다. 대신 **내리지도 않으므로**
체계적 실패와 번갈아 나와도 상한에 닿는다.

**멈추는 것과 산출물을 잃는 것은 다르다.** 셋 중 어느 갈래로 멈추든 예산 소진과 같은
자리에서 같은 방식으로 끝난다 — 리포트와 매니페스트까지 가고 `stopped_after`·사유가
`RunRecord`에 남는다(전이 표 R-2). 예외가 `write_manifest` 앞을 뚫고 나가면 그때까지의
GPU 시간이 통째로 사라지고, 그것이 이 절이 없애려는 결함이다.
"""

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.config import HarnessConfig
from src.ids import IdFactory, sha256_text
from src.interfaces.model_client import (
    ModelClient,
    ModelGatewayError,
    SchemaParseError,
    error_detail,
)
from src.interfaces.retrieval import RetrievalResult
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner
from src.orchestrator import state_machine
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)
from src.orchestrator.nodes.report import Report
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_log import PromptLog
from src.orchestrator.stages import MAX_STAGE, Stage
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate
from src.schemas.common import PLACEHOLDER_DIGEST, Location
from src.schemas.decision import Decision
from src.schemas.enums import (
    ArtifactKind,
    CapsuleLevel,
    EvidenceKind,
    FindingState,
    ReasonCode,
)
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import RunRecord

# 실제 KB 스냅샷이 붙기 전까지 쓰는 태그. 이 값이 그대로 기록되면 경고를 남긴다 —
# `RunRecord.kb_snapshot`은 재현성 필수 항목이다(engineering-guide.md §5).
DEFAULT_KB_SNAPSHOT = "kb-none"

# git을 못 읽었을 때의 자리표시자. 40 hex라는 형식은 지키되 경고 대상이다.
_UNKNOWN_COMMIT = "0" * 40

# 하네스 저장소 루트. `cwd`를 쓰면 저장소 밖에서 CLI를 돌렸을 때 **다른 저장소**의
# HEAD를 읽는다 — 기록이 조용히 남의 것이 된다.
REPO_ROOT = Path(__file__).resolve().parents[2]

# 분석 대상이 되는 artifact 종류. config·unknown은 캡슐을 만들지 않는다.
_ANALYZABLE = (ArtifactKind.SOURCE, ArtifactKind.BINARY, ArtifactKind.FIRMWARE)

# `--dry-run`이 도달하는 마지막 단계. 여기까지는 모델을 부르지 않는다.
DRY_RUN_LAST_STAGE = int(Stage.RETRIEVAL)

# `target_skipped.reason`. 왜 4단계에 못 올라갔는가를 하나로 뭉치지 않는다 — 예산에
# 잘린 것과 호출이 실패한 것은 대응이 다르다(`IndexSummary.status`와 같은 논법).
_REASON_OVER_MAX = "over_candidate_max"
_REASON_GATEWAY = "gateway_error"
_REASON_SCHEMA = "schema_parse_failed"

# **이 후보가 크다**는 뜻의 HTTP 상태. 400은 vLLM이 컨텍스트 초과에 주는 값이고
# 413은 본문 초과다. 다음 후보는 작을 수 있으므로 **이 후보에 대한 사실**이지 run에
# 대한 사실이 아니다 — 그래서 아래 `_MAX_CONSECUTIVE_DROPS`가 세지 않는다.
_SIZE_STATUS = frozenset({400, 413})

# 후보만 버리고 계속 가도 되는 HTTP 상태. **요청 하나가 거절된 것**이다.
# 422(스키마 거절)는 크기가 아니라 요청 모양의 문제라 체계적 신호로 센다.
#
# 그 밖(상태 없음 = 연결 실패·타임아웃, 5xx = 서버 오류)은 서버 쪽 사건이라 잡지
# 않는다. 잡으면 남은 target 수백 개를 전부 실패시키며 돈다.
_DROPPABLE_STATUS = _SIZE_STATUS | frozenset({422})

# 연속 폐기 상한. 체계적 고장(모델이 스키마를 전혀 못 맞춘다·요청 모양이 틀렸다)이면
# 남은 target을 전부 태우고도 쓸 수 있는 결과가 안 나온다.
#
# **크기 거절(`_SIZE_STATUS`)은 여기에 세지 않는다.** 세면 큰 심볼 셋이 연달아 오는
# 것만으로 run이 멈추는데, 그것은 W2가 정확히 견디겠다고 한 경우다("후보 하나가
# 컨텍스트를 넘겼다 → 그 후보만 버린다"). 정렬이 신호 붙은 artifact를 앞으로 모으므로
# 큰 함수가 뭉쳐 오는 것은 드문 일도 아니다.
#
# **번갈아 실패하는 경우는 여전히 걸린다.** 크기 거절은 카운터를 올리지도 않지만
# **내리지도 않는다** — 리셋 조건은 오직 완주다. 그래서 스키마 → 400 → 스키마 →
# 400 → 스키마는 1·1·2·2·3으로 상한에 닿는다. 이 값이 잡으려는 것이 그 모양이다.
#
# **크기 거절만 계속 나오는 run은 멈추지 않는다.** 그래도 폭주하지 않는 것은
# `candidate_max`가 이미 목록을 묶어 두기 때문이고, 끝난 뒤에는 모든 행에 서버가
# 말해 준 사유가 붙은 완전한 기록이 남는다 — 중간에 끊긴 run보다 진단이 쉽다.
# 400이 크기가 아닌 이유(페이로드 자체가 틀렸다)로 올 수도 있는데, 그 경우에도 결과는
# "전량 스킵 + 매 행에 사유"라 한 번 보면 갈린다.
#
# **잠정치다.** 실대상 run에서 후보 단위 실패가 얼마나 자주 나는지 재 본 적이 없다 —
# 그 측정의 입력이 이 변경 뒤의 첫 실대상 run이다(`thresholds.candidate_max`와 같은 상태).
_MAX_CONSECUTIVE_DROPS = 3


class _TooManyDrops(RuntimeError):
    """연속 폐기 상한. **서버가 죽은 것과 다른 사건이라 다른 타입이다.**

    게이트웨이가 응답을 주는데도 후보가 계속 실패하는 상태다 — 프롬프트·예산·스키마 쪽을
    봐야 하고, 서버를 살리는 것으로는 낫지 않는다. 같은 예외로 올리면 `RunRecord`의 중단
    사유가 하나로 뭉쳐 그 구별이 사라진다.

    러너 밖으로 새지 않는다. `run_pipeline`이 잡아 `stopped_after`와 사유로 바꾼다.
    """


class Dependencies(Protocol):
    """어댑터 묶음. `src/adapters/factory.py`의 `Deps`가 구조적으로 이것을 만족한다.

    Protocol로 받는 이유는 러너가 어댑터 패키지를 import 하지 않기 위해서다.
    """

    models: ModelClient
    retriever: Retriever
    tools: ToolRunner
    store: StateStore
    ids: IdFactory
    # 컨테이너 이미지 → digest. 러너가 만들지 않고 받는다 — 무엇이 실제로 실행됐는지는
    # 어댑터만 알고, 러너가 추측해 채우면 그 기록은 사실과 갈라진다.
    tool_images: dict[str, str]
    # file → 그 파일에 정의된 심볼 위치. `None`이면 인덱스가 없다는 뜻이고 러너는 파일
    # 단위로 되돌아간다. `[]`("봤는데 심볼 0건")와 섞지 않는다 — `index_graph`가 캡슐의
    # 그래프 섹션에 대해 세운 것과 같은 구별이다.
    symbols_for: Callable[[str], list[Location] | None]


@dataclass(frozen=True)
class RunSpec:
    """한 번의 실행을 결정하는 값 전부. 여기 없는 것은 config에서 온다."""

    condition: str
    target: Path
    stop_after: int | None = None
    dry_run: bool = False
    kb_snapshot: str = DEFAULT_KB_SNAPSHOT
    # 어느 장비에서 돌리는가. `--device`가 준 라벨 그대로이고, 안 주면 `None`이다.
    # config에서 오지 않으므로 여기 있다 — `kb_snapshot`과 같은 성격이다.
    device: str | None = None
    # 재현 실행에서 같은 run_id·타임스탬프가 나오도록 주입한다.
    started_at: datetime | None = None

    def stop_at(self) -> tuple[int, ReasonCode | None]:
        """도달할 마지막 단계와, 완주가 아니라면 **무엇이 잘랐는지**.

        단계 수와 사유를 한 함수에서 함께 낸다. 따로 계산하면 갈라지고, 갈라진 기록은
        이 PR이 고치는 결함과 같은 종류가 된다.

        상한이 둘 다 걸릴 수 있으므로(`--dry-run --stop-after 5`) **낮은 쪽이 이긴다.**
        같으면 `dry_run`을 적는다 — dry-run은 단계 수뿐 아니라 "모델을 부르지 않았다"까지
        뜻하는 더 강한 진술이라, 동점에서 그쪽을 적는 편이 기록이 더 정확하다.
        """
        last = MAX_STAGE if self.stop_after is None else min(self.stop_after, MAX_STAGE)
        if self.dry_run and DRY_RUN_LAST_STAGE <= last:
            return DRY_RUN_LAST_STAGE, ReasonCode.DRY_RUN
        return last, None if last >= MAX_STAGE else ReasonCode.USER_STOP_REQUESTED


@dataclass(frozen=True)
class RunResult:
    """실행 산출물. 지표 집계는 여기가 아니라 trace에서 한다."""

    record: RunRecord
    report: Report
    candidates: tuple[Candidate, ...]
    findings: tuple[Finding, ...]
    warnings: tuple[str, ...]


class CacheHookUnbound(RuntimeError):
    """캐시 훅이 RunContext에 연결되기 전에 불렸다."""


class LateCacheHook:
    """Retriever는 생성 시점에 훅을 받는데 `RunContext`는 그 뒤에 만들어진다.

    조립 순서 때문에 생기는 구멍이라 늦은 바인딩으로 메운다. **바인딩 전 호출은
    조용히 무시하지 않고 터뜨린다** — 캐시 이벤트가 새면 cache hit ratio가 0으로
    보이고, 그것은 "캐시가 안 맞았다"와 구분되지 않는다.
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, key: str, hit: bool) -> None:
        if self._ctx is None:
            raise CacheHookUnbound("캐시 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_cache(key, hit)


class UsageHookUnbound(RuntimeError):
    """검색 비용 훅이 RunContext에 연결되기 전에 불렸다."""


class LateUsageHook:
    """`LateCacheHook`과 같은 조립 순서 문제를 같은 방식으로 메운다.

    **바인딩 전 호출은 조용히 무시하지 않고 터뜨린다** — 비용 이벤트가 새면 검색이
    장부에 안 잡히고, 그것은 "검색이 공짜"와 구분되지 않는다. Fake에서 0으로 보이던
    것이 실서빙에서는 실제 GPU 시간이다.
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, component: str, result: RetrievalResult, candidates: int) -> None:
        if self._ctx is None:
            raise UsageHookUnbound("검색 비용 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_retrieval(component, result, candidates)


class TruncateHookUnbound(RuntimeError):
    """절단 훅이 RunContext에 연결되기 전에 불렸다."""


class LateTruncateHook:
    """`LateCacheHook`과 같은 조립 순서 문제를 같은 방식으로 메운다.

    **바인딩 전 호출은 조용히 무시하지 않고 터뜨린다** — 이 이벤트가 새면 광역 수집이
    상한에 걸려 잘린 run이 **"전부 봤다"와 구별되지 않는다.** 검색 범위를 모르는 것과
    범위가 완전한 것은 다르다(`LateIndexHook`이 커버리지에 대해 편 논법과 같다).
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, collected: int, kept: int) -> None:
        if self._ctx is None:
            raise TruncateHookUnbound("절단 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_retrieval_truncated(collected, kept)


class IndexSummaryLike(Protocol):
    """인덱스 훅이 받는 것의 구조적 계약.

    구체 타입(`adapters.index_graph.IndexSummary`)을 import 하면 오케스트레이터가 어댑터를
    아는 상태가 된다(engineering-guide.md §3). `src/orchestrator/`와 `src/interfaces/`에
    어댑터 import는 지금 0건이고 그 상태를 유지한다.
    """

    def as_trace_fields(self) -> dict[str, str | int]: ...


class IndexHookUnbound(RuntimeError):
    """인덱스 훅이 RunContext에 연결되기 전에 불렸다."""


class LateIndexHook:
    """`LateCacheHook`과 같은 조립 순서 문제를 같은 방식으로 메운다.

    **바인딩 전 호출은 조용히 무시하지 않고 터뜨린다** — 이 이벤트가 새면
    `unresolved_calls`·`unresolved_symbols`가 trace에 안 남고, 그러면 그 저장소에서
    호출 그래프 커버리지가 얼마였는지 사후에 답할 수 없다. **커버리지를 모르는 것과
    커버리지가 완전한 것은 다르다.**
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, summary: IndexSummaryLike) -> None:
        if self._ctx is None:
            raise IndexHookUnbound("인덱스 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_graph_index(summary.as_trace_fields())


@dataclass(frozen=True)
class _Target:
    """4단계에 올릴 대상 하나. artifact가 아니라 **심볼**이 단위다(W2).

    `evidences`를 들고 다니는 것은 2단계가 artifact당 한 번만 돌기 때문이다 — 같은
    파일의 심볼 수백 개가 같은 신호를 공유한다. target마다 도구를 다시 돌리면 zlib에서
    도구 호출이 세 자릿수에서 네 자릿수가 된다.
    """

    artifact: Artifact
    location: Location
    evidences: tuple[Evidence, ...]
    # 이 artifact에 정적 신호가 붙었는가. **심볼이 아니라 artifact 단위다** —
    # `Evidence.location`이 비어 있어 어느 심볼의 신호인지 알 수 없다(모듈 docstring).
    signal: bool


@dataclass
class _TargetPlan:
    """target 목록과 그것을 세는 값들. 그대로 `target_plan` 이벤트가 된다.

    분모를 러너 지역 변수로 두지 않는 이유는 `_triage_all`이 폐기 건수를 더 얹기
    때문이다 — 세는 곳이 둘로 갈리면 경고와 trace가 다른 수를 말한다.
    """

    analyzable_artifacts: int = 0
    symbol_targets: int = 0
    fallback_artifacts: int = 0
    no_symbol_artifacts: int = 0
    signal_artifacts: int = 0
    collected: list[_Target] = field(default_factory=list)
    targets: list[_Target] = field(default_factory=list)


@dataclass
class _Accumulator:
    """단계 사이에 들고 다니는 중간 산출물.

    예산 소진으로 중간에 끊겨도 그때까지의 결과로 리포트를 만들어야 하므로,
    지역 변수가 아니라 호출자가 들고 있는 객체에 쌓는다.
    """

    candidates: list[Candidate] = field(default_factory=list)
    findings: dict[str, Finding] = field(default_factory=dict)
    evidences: dict[str, tuple[Evidence, ...]] = field(default_factory=dict)
    missings: dict[str, tuple[MissingEvidence, ...]] = field(default_factory=dict)
    # 7단계 Critic 판정. **8단계 게이트 재판정의 입력이다** — 없으면 `p3`(uphold)이 거짓이
    # 되어 asan이 실패한 run에서 등급이 7단계보다 낮게 기록된다.
    decisions: dict[str, tuple[Decision, ...]] = field(default_factory=dict)
    workdirs: dict[str, str] = field(default_factory=dict)
    # 4단계에 못 올라간 target 수 (W2). 예산에 잘린 것과 호출이 실패한 것을 가른다.
    # **여기 두는 이유가 이 클래스의 존재 이유와 같다** — 예산 소진으로 중간에 끊겨도
    # 호출자가 그 수를 읽어 경고와 리포트에 올려야 한다.
    deferred: int = 0
    dropped: int = 0
    # 7단계에 못 올라간 Finding 수 (D26 (b)). **위 둘과 단위가 다르다** — `deferred`·
    # `dropped`는 target을 세고 이쪽은 Finding을 센다. 한 수로 묶으면 경고와 리포트에서
    # 분모가 섞인다(`target_skipped`를 `critic_skipped`와 나눈 것과 같은 이유).
    critic_deferred: int = 0

    def update(self, finding: Finding) -> None:
        self.findings[finding.finding_id] = finding

    def in_state(self, state: FindingState) -> list[Finding]:
        return [f for f in self.findings.values() if f.state is state]


def run_pipeline(
    spec: RunSpec,
    *,
    config: HarnessConfig,
    deps: Dependencies,
    read_source: Callable[[Location], str],
    trace: TraceWriter,
    run_dir: Path | None = None,
    cache_hook: LateCacheHook | None = None,
    truncate_hook: LateTruncateHook | None = None,
    usage_hook: LateUsageHook | None = None,
    index_hook: LateIndexHook | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """단계 0~9를 순서대로 돌리고 `RunRecord`를 닫는다."""
    now = clock or (lambda: datetime.now(UTC))
    started_at = spec.started_at or now()
    ctx = RunContext(
        run_id=trace.run_id,
        config=config,
        models=deps.models,
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        read_source=read_source,
        symbols_for=deps.symbols_for,
        # **`write_manifest`와 같은 가드다** — 산출물 디렉터리가 있고 실제로 도는 run일
        # 때만 프롬프트를 남긴다(D19). 조건을 따로 만들면 매니페스트는 있는데 프롬프트는
        # 없는 run이 생기고, 그 차이의 이유가 어디에도 안 남는다.
        prompts=PromptLog(run_dir) if run_dir is not None and not spec.dry_run else None,
    )
    if cache_hook is not None:
        cache_hook.bind(ctx)
    if truncate_hook is not None:
        truncate_hook.bind(ctx)
    if usage_hook is not None:
        usage_hook.bind(ctx)
    if index_hook is not None:
        index_hook.bind(ctx)

    artifacts = preprocess.run(ctx, source_root=spec.target)
    record, warnings = _new_record(
        spec, config, ctx.run_id, started_at, artifacts, deps.tool_images
    )
    ctx.record = record
    ctx.store.put(record)

    last, stop_reason = spec.stop_at()
    state = _Accumulator()
    stopped_after = None if stop_reason is None else last
    try:
        _analyze(ctx, artifacts, state, last)
    except BudgetExhausted as exc:
        # 예산 소진이 사용자 지정 상한보다 먼저 왔다. 사유를 덮어쓴다 — 실제로 실행을
        # 끊은 것이 이쪽이다.
        stopped_after, stop_reason = exc.stage, ReasonCode.BUDGET_EXHAUSTED
        warnings.append(f"{exc.stage}단계에서 예산 소진: {exc.item} {exc.spent}/{exc.limit}")
    except ModelGatewayError as exc:
        # 서버 쪽 사건이라 남은 target을 태워도 얻는 것이 없다. **그래도 여기까지 만든
        # 것은 남긴다** — 예산 소진과 같은 자리에서 같은 방식으로 끝낸다(전이 표 R-2).
        # 이 갈래가 없던 동안에는 예외가 `write_manifest` 앞을 뚫고 나가 산출물이 하나도
        # 안 남았고, zlib 때 `state.db`를 직접 열어야 했던 이유가 그것이다.
        stopped_after = int(ctx.current_stage)
        stop_reason = ReasonCode.MODEL_GATEWAY_UNAVAILABLE
        warnings.append(f"게이트웨이가 응답하지 않아 중단했다: {error_detail(exc)}")
    except _TooManyDrops as exc:
        # 서버는 응답하는데 후보가 계속 죽는다. 위와 사유를 나눈다 — 대응이 다르다.
        stopped_after = int(ctx.current_stage)
        stop_reason = ReasonCode.CONSECUTIVE_TARGET_FAILURES
        warnings.append(f"{exc}")

    # **GPU 회계 판정은 여기서 한다.** `_new_record`는 파이프라인 시작 전에 불리므로 그
    # 시점에는 모델 호출이 하나도 없다. 실서빙 역할과 교집합해서 Fake 역할이 섞이지 않게
    # 한다 — `single_gpu_12g`처럼 scout만 실서빙인 프로필에서 Fake 네 역할이 걸리면
    # `dev_*` 프로필이 매 run 같은 줄을 뱉고 진짜 문제가 묻힌다(checksum 검사와 같은 논법).
    served = config.served_models()
    gpu_warning = unmeasured_gpu_warning(
        {role: count for role, count in ctx.gpu_unmeasured_roles.items() if role in served}
    )
    if gpu_warning:
        warnings.append(gpu_warning)

    # **4단계에 못 올라간 target을 경고로 올린다 (W2).** trace의 `target_skipped`가
    # 목록이지만 사용자가 run 결과로 읽는 것은 cli 출력과 report.md다. 여기 안 올리면
    # deferred가 200건인 run과 0건인 run이 겉보기에 같아지고, 그러면 "조용히 절단하지
    # 않는다"가 기록 절반에서만 성립한다.
    #
    # 예산 소진 경고와 **같은 자리·같은 경로**다. 새 통로를 내지 않는다.
    if state.deferred:
        warnings.append(
            f"target {state.deferred}건을 candidate_max"
            f"({config.thresholds.candidate_max})로 잘랐다. trace의 target_skipped가 목록이다"
        )
    if state.dropped:
        warnings.append(
            f"target {state.dropped}건을 후보 단위 호출 실패로 버렸다. "
            f"사유는 trace의 target_skipped와 그 target의 model_call에 있다"
        )
    # **7단계 절단도 같은 자리에 올린다 (D26 (b)).** 위 두 경고의 논거가 그대로 적용되는데,
    # 여기서는 한 겹 더 나쁘다 — target 절단은 대상이 사라져서 안 보이지만 Finding 절단은
    # F-9가 상태를 안 바꿔서 **44줄이 멀쩡히 찍힌 채** 전량을 본 것처럼 읽힌다.
    if state.critic_deferred:
        warnings.append(
            f"Finding {state.critic_deferred}건을 critic_top_n"
            f"({config.thresholds.critic_top_n})로 잘라 7단계에 올리지 않았다. "
            f"trace의 critic_skipped가 목록이다"
        )

    findings = list(state.findings.values())
    rendered = report.run(
        ctx,
        findings=findings,
        deferred=state.deferred,
        dropped=state.dropped,
        critic_deferred=state.critic_deferred,
        critic_top_n=config.thresholds.critic_top_n,
    )
    finished = state_machine.finish_run(
        record,
        finished_at=max(now(), started_at),
        stopped_after=stopped_after,
        reason=stop_reason,
        store=ctx.store,
        trace=trace,
    )
    if run_dir is not None and not spec.dry_run:
        write_manifest(run_dir, finished)
    return RunResult(
        record=finished,
        report=rendered,
        candidates=tuple(state.candidates),
        findings=tuple(findings),
        warnings=tuple(warnings),
    )


def _analyze(ctx: RunContext, artifacts: list[Artifact], state: _Accumulator, last: int) -> None:
    """1~8단계. 9단계 리포트는 예산이 끊겨도 돌아야 하므로 호출자가 따로 부른다."""
    if last < Stage.INDEX:
        return
    graph = index.run(ctx, workdir=".")
    plan = _plan_targets(ctx, [a for a in artifacts if a.kind in _ANALYZABLE], graph, state, last)
    _triage_all(ctx, plan, state, last)
    if last < Stage.CRITIC:
        return
    # **절단 수를 노드가 아니라 여기서 센다.** 노드가 세고 러너가 또 세면 경고와 리포트가
    # 다른 수를 말할 수 있다(report.py `run()`의 `deferred`·`dropped`와 같은 처리).
    promoted = state.in_state(FindingState.HYPOTHESIZED)
    selected = critic.select(ctx, promoted)
    state.critic_deferred = len(promoted) - len(selected)
    for finding in selected:
        after, decision = critic.run(
            ctx,
            finding=finding,
            evidences=state.evidences.get(finding.finding_id, ()),
            missings=state.missings.get(finding.finding_id, ()),
        )
        # 판정을 버리지 않는다. 8단계 게이트가 `p3`을 세려면 이 값이 필요하다.
        state.decisions[finding.finding_id] = (decision,)
        state.update(after)
    if last < Stage.VALIDATE:
        return
    for finding in state.in_state(FindingState.VALIDATED):
        after, _ = validate.run(
            ctx,
            finding=finding,
            workdir=state.workdirs.get(finding.finding_id, "."),
            evidences=state.evidences.get(finding.finding_id, ()),
            missings=state.missings.get(finding.finding_id, ()),
            decisions=state.decisions.get(finding.finding_id, ()),
        )
        state.update(after)


def _plan_targets(
    ctx: RunContext,
    artifacts: list[Artifact],
    graph: Evidence,
    state: _Accumulator,
    last: int,
) -> _TargetPlan:
    """2단계를 artifact마다 돌리고 4단계에 올릴 target 목록을 확정한다 (W2).

    **모델을 한 번 부르기 전에 끝난다.** 그래서 `target_plan`이 실린 뒤에 죽어도 분모가
    남고, 정적 신호로 **전역** 정렬을 할 수 있다 — artifact 하나를 끝까지 태우는
    구조에서는 뒤쪽 artifact의 신호를 아직 모르므로 앞쪽의 신호 없는 심볼이 먼저 돈다.

    도구 호출 횟수는 예전과 같다. 그때도 2단계가 artifact 전부에 대해 돌았다.
    """
    plan = _TargetPlan(analyzable_artifacts=len(artifacts))
    if last < Stage.SIGNALS:
        return plan
    for artifact in artifacts:
        found = signals.with_signal(signals.run(ctx, workdir=artifact.path))
        evidences = (graph, *found)
        # **정적 신호만 순서에 쓴다.** `with_signal()`이 남기는 것에는 `build`(BUILD)도
        # 있는데 그쪽은 성공 자체가 신호라 **모든 artifact에 붙는다** — 그것까지 세면
        # 정렬 키가 상수가 되어 (a′)가 아무 일도 하지 않는다.
        #
        # 판정을 `fake_model._has_static_signal()`과 같은 축에 둔다. 점수에서 가산점을
        # 주는 신호와 순서를 정하는 신호가 다르면 (a′)가 "원래 하던 것"에서 벗어난다.
        signal = any(evidence.kind is EvidenceKind.STATIC for evidence in found)
        if signal:
            plan.signal_artifacts += 1
        locations = ctx.symbols_for(artifact.path)
        if locations is None:
            # 인덱스가 없다(`dev_fake`·버전 모름·깨짐). 파일 단위로 되돌아간다 — 후보를
            # 안 만들면 그 프로필의 run이 통째로 비고, 폴백은 W2 이전 동작이라 회귀가
            # 아니다. **되돌아갔다는 사실이 기록에 남는 것**이 새로 얻는 것이다.
            plan.fallback_artifacts += 1
            locations = [Location(file=artifact.path)]
        elif not locations:
            # 인덱스는 있는데 이 파일에 심볼이 없다(`.h`·`.py`·바이너리는 인덱서에
            # 넘어가지 않는다). 후보를 만들지 않는다 — 대상 선정이 새던 자리가 여기서
            # 닫힌다(zlib run의 후보 32건 중 C 소스는 12건이었다).
            plan.no_symbol_artifacts += 1
            continue
        else:
            plan.symbol_targets += len(locations)
        plan.collected += [
            _Target(artifact=artifact, location=location, evidences=evidences, signal=signal)
            for location in locations
        ]
    _order_and_cut(ctx, plan, state)
    return plan


def _order_and_cut(ctx: RunContext, plan: _TargetPlan, state: _Accumulator) -> None:
    """(a′) — 정적 신호를 게이트가 아니라 순서로 쓰고, 상한 초과분을 기록에 남긴다.

    정렬 키의 첫 항이 `not signal`이라 `False < True`로 신호 붙은 쪽이 앞에 온다.
    나머지 세 항은 index.json의 순서와 같다 — 후보 순서가 곧 ID 발급 순서라 흔들리면
    두 run의 trace를 대조할 수 없다(engineering-guide.md §5).

    **신호는 artifact 단위다.** 심볼 단위로 하려면 `Evidence.location`이 있어야 하는데
    그것을 채우는 코드가 저장소에 없다(모듈 docstring).
    """
    plan.collected.sort(
        key=lambda item: (
            not item.signal,
            item.location.file,
            item.location.line_start or 0,
            item.location.symbol or "",
        )
    )
    limit = ctx.config.thresholds.candidate_max
    plan.targets = plan.collected[:limit]
    deferred = plan.collected[limit:]
    ctx.trace.emit(
        EventKind.TARGET_PLAN,
        stage=Stage.SIGNALS,
        analyzable_artifacts=plan.analyzable_artifacts,
        symbol_targets=plan.symbol_targets,
        fallback_artifacts=plan.fallback_artifacts,
        no_symbol_artifacts=plan.no_symbol_artifacts,
        signal_artifacts=plan.signal_artifacts,
        planned=len(plan.targets),
        deferred=len(deferred),
        candidate_max=limit,
    )
    state.deferred = len(deferred)
    for item in deferred:
        _emit_skipped(ctx, item.location, stage=Stage.SIGNALS, reason=_REASON_OVER_MAX)


def _triage_all(ctx: RunContext, plan: _TargetPlan, state: _Accumulator, last: int) -> None:
    """target을 차례로 태운다. **후보 하나의 실패로 run을 잃지 않는다.**

    `BudgetExhausted`는 여기서 잡지 않는다 — 실패가 아니라 정상 종료 경로이고
    `run_pipeline`이 처리한다(모듈 docstring).

    `ModelGatewayError`·`SchemaParseError`는 후보 단위 실패다. 그 후보만 버리고 계속
    간다. 다만 무조건 잡으면 안 된다:

    | 사건 | 판정 | 대응 |
    | --- | --- | --- |
    | 후보 하나가 컨텍스트를 넘겼다 | `status`가 요청 단위 거절(4xx) | 그 후보만 버린다 |
    | 서버가 죽었다 | `status`가 없거나 5xx | 즉시 멈춘다 |
    | 스키마를 못 맞췄다 | `SchemaParseError` | 그 후보만 버린다 |

    **연속 폐기를 하나의 카운터로 센다.** 게이트웨이와 스키마를 따로 세면 둘이 번갈아
    실패할 때 어느 쪽도 상한에 닿지 않고 target 수백 개를 전부 태운다. 리셋 조건이
    "완주"인 것도 같은 이유다 — 사유가 섞여 있다는 사실이 "이 run은 체계적으로
    고장 났다"는 판정을 무르게 하지 않는다.
    """
    consecutive = 0
    for target in plan.targets:
        try:
            _triage_one(ctx, target, state, last)
        except ModelGatewayError as exc:
            if exc.status is None or exc.status not in _DROPPABLE_STATUS:
                # 서버 쪽 사건이다. 여기서 잡으면 남은 target을 전부 실패시키며 돈다.
                #
                # **그래도 run을 죽이지는 않는다.** `run_pipeline`이 이 예외를 받아
                # 리포트와 매니페스트까지 간 뒤 중단 사유를 적는다 — 그러지 않으면
                # 이 변경이 없애려던 "산출물이 하나도 안 남는다"가 그대로 남는다.
                raise
            # 크기 거절은 이 후보에 대한 사실이라 체계적 고장으로 세지 않는다.
            # 올리지도 내리지도 않는다 — 사유는 `_MAX_CONSECUTIVE_DROPS` 주석에 있다.
            systematic = exc.status not in _SIZE_STATUS
            consecutive = _drop(
                ctx, state, target, _REASON_GATEWAY, exc, consecutive, systematic=systematic
            )
        except SchemaParseError as exc:
            consecutive = _drop(ctx, state, target, _REASON_SCHEMA, exc, consecutive)
        else:
            # 완주했다. 앞의 실패는 체계적 고장이 아니었다.
            consecutive = 0


def _drop(
    ctx: RunContext,
    state: _Accumulator,
    target: _Target,
    reason: str,
    exc: Exception,
    consecutive: int,
    *,
    systematic: bool = True,
) -> int:
    """target 하나를 버리고 기록한다. 연속 상한에 닿으면 `_TooManyDrops`를 올린다.

    `systematic=False`는 "이 후보에 대한 사실이지 run에 대한 사실이 아니다"라는 뜻이다
    (크기 거절). 버리고 기록하는 것은 같고 카운터만 건드리지 않는다 — **내리지도
    않으므로** 체계적 실패와 번갈아 나와도 상한에 닿는다.
    """
    _emit_skipped(
        ctx, target.location, stage=Stage.SCOUT, reason=reason, detail=error_detail(exc)
    )
    state.dropped += 1
    if not systematic:
        return consecutive
    if consecutive + 1 >= _MAX_CONSECUTIVE_DROPS:
        raise _TooManyDrops(
            f"후보 {_MAX_CONSECUTIVE_DROPS}건이 연속으로 실패했다. 마지막 사유: {exc}"
        ) from exc
    return consecutive + 1


def _emit_skipped(
    ctx: RunContext, target: Location, *, stage: Stage, reason: str, detail: str = ""
) -> None:
    """4단계에 올리지 못한 target 하나를 기록한다.

    `Candidate`를 만들어 `queued`로 두지 않는 이유는 `EventKind.TARGET_SKIPPED` 주석에
    적었다 — 점수를 지어내야 하고 그 값이 Scout 점수 분포(D2의 입력)에 섞인다.

    **`stage`가 곧 어디서 잘렸는가다.** 예산 절단은 2단계 뒤 계획 시점이고 후보 단위
    실패는 4단계다 — 둘을 한 값으로 적으면 예산에 잘린 target이 "4단계에 들어갔다가
    죽었다"로 읽힌다. `reason`과 중복이 아니라 서로를 검증한다.

    **`error_detail`은 있을 때만 싣는다.** 예산 절단에는 상세가 없고, 그 자리에 빈
    문자열을 넣으면 "상세가 비어 있었다"와 "상세라는 개념이 없다"가 같은 모양이 된다.
    """
    ctx.trace.emit(
        EventKind.TARGET_SKIPPED,
        stage=stage,
        file=target.file,
        symbol=target.symbol or "",
        line_start=target.line_start or 0,
        reason=reason,
        **({"error_detail": detail} if detail else {}),
    )


def _triage_one(ctx: RunContext, target: _Target, state: _Accumulator, last: int) -> None:
    """target 하나를 3~6단계에 태운다. 승격되지 않은 후보는 거기서 멈춘다."""
    location, artifact, evidences = target.location, target.artifact, target.evidences
    if last < Stage.RETRIEVAL:
        return
    capsule = retrieval.run(ctx, target=location, level=CapsuleLevel.LIGHT)
    if last < Stage.SCOUT:
        return
    candidate = scout.run(ctx, artifact=artifact, capsule=capsule, evidences=evidences)
    state.candidates.append(candidate)
    if candidate.state is not FindingState.TRIAGED or last < Stage.AUDITOR:
        return
    expanded = retrieval.run(ctx, target=location, level=CapsuleLevel.EXPANDED)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=expanded, evidences=evidences)
    state.update(finding)
    state.evidences[finding.finding_id] = evidences
    state.missings[finding.finding_id] = tuple(missings)
    state.workdirs[finding.finding_id] = artifact.path
    if last < Stage.GATE:
        return
    graded, _ = gate_node.run(
        ctx,
        finding=finding,
        evidences=evidences,
        missings=tuple(missings),
        candidate_location=candidate.location,
    )
    state.update(graded)


def _new_record(
    spec: RunSpec,
    config: HarnessConfig,
    run_id: str,
    started_at: datetime,
    artifacts: list[Artifact],
    tool_images: dict[str, str],
) -> tuple[RunRecord, list[str]]:
    """재현성 기록을 만든다. 빈 필수 항목은 경고로 남긴다.

    evaluation.md §6: 기록이 불완전한 run의 결과는 비교표에 넣지 않는다. 그러니 조용히
    비워 두는 대신, 무엇이 왜 비었는지를 호출자가 볼 수 있게 돌려준다.
    """
    commit, dirty, warnings = _repo_state()
    record = RunRecord(
        run_id=run_id,
        condition=spec.condition,
        repo_commit=commit,
        repo_dirty=dirty,
        source_hash=source_hash(artifacts),
        model_checksums=config.model_checksums(),
        prompt_versions=config.prompt_versions(),
        # 어댑터가 실제로 물린 이미지의 digest다. Fake로 돌면 비어 있고, 그때는
        # `incomplete_fields()`가 경고하는 것이 옳은 동작이다 — 없는 값을 지어내면
        # 재현성 기록 자체가 거짓이 된다.
        tool_images=dict(tool_images),
        # 프로필에 적힌 값이 아니라 `--tool-runner` 오버라이드까지 적용된 뒤의 값이다.
        # cli가 config를 갈아 끼우고 나서 러너를 부르므로(seed와 같은 경로) 여기서
        # config를 그대로 읽는 것이 곧 "실제로 쓴 런타임"이다.
        tool_runner=config.profile.adapters.tool_runner,
        # config가 아니라 spec에서 온다 — 장비 라벨은 설정이 아니라 이 실행의 사실이다.
        device=spec.device,
        budget=config.budget,
        thresholds=config.thresholds.flatten(),
        seed=config.seed,
        sampling=config.sampling,
        kb_snapshot=spec.kb_snapshot,
        started_at=started_at,
    )
    # Fake 역할은 빼고 준다. 그쪽의 checksum 0은 빠진 값이 아니라 맞는 값이다
    # (`incomplete_fields`의 그 검사 주석).
    warnings.extend(incomplete_fields(record, served_models=config.served_models()))
    return record, warnings


def unmeasured_gpu_warning(roles: Mapping[str, int] | None) -> str | None:
    """실서빙 역할이 GPU 시간을 못 남겼다는 경고. 없으면 `None`.

    **`incomplete_fields()`와 러너가 같은 정의를 쓰기 위해 따로 뽑았다.** 판정에 필요한
    값이 run이 **끝난 뒤에야** 나오는데(`_new_record`는 파이프라인 시작 전에 불린다)
    러너가 문구를 따로 만들면 두 곳이 갈라진다 — 이 저장소가 반복해서 고쳐 온 "선언과
    실제의 괴리"가 정확히 그 형태다.

    evaluation.md §5.2·§5.3이 GPU-second를 비용 지표의 분자로 쓴다. 실서빙 호출이 그 값을
    못 남겼다면 이 run의 비용은 **미상**이고, 그대로 비교표에 넣으면 "쟀는데 0"과 "안
    쟀다"가 같은 칸에 들어간다 — 2026-08-17까지 모델 호출이 GPU 시간을 아예 청구하지
    못했던 것이 정확히 그 상태였다.
    """
    if not roles:
        return None
    detail = ", ".join(f"{role}({count}회)" for role, count in sorted(roles.items()))
    return (
        f"GPU 시간을 못 잰 실서빙 호출이 있다: {detail}. 이 run의 GPU 비용은 미상이라 "
        "모델 비교표에서 제외한다"
    )


def incomplete_fields(
    record: RunRecord,
    *,
    served_models: Mapping[str, str] | None = None,
    gpu_unmeasured_roles: Mapping[str, int] | None = None,
) -> list[str]:
    """비교표에 넣을 수 없게 만드는 빈 항목 (engineering-guide.md §5).

    `served_models`는 **Fake가 아닌 역할**의 역할 → model key다. checksum 자리표시자
    판정에만 쓴다. `None`이면 그 검사를 건너뛴다 — 모르는 채로 "괜찮다"고 말하지 않고,
    판정할 근거가 없으면 판정하지 않는다.

    `gpu_unmeasured_roles`는 **실서빙 역할 중 GPU 시간을 못 잰 것**의 역할 → 호출 수다.
    러너가 원장에서 뽑아 `served_models`와 교집합해서 넘긴다 — Fake 역할이 섞이면 `dev_*`
    프로필이 매 run 같은 줄을 뱉고 진짜 문제가 묻힌다(checksum 검사와 같은 논법).
    """
    warnings = []
    if not record.tool_images:
        warnings.append("tool_images가 비어 있다 (컨테이너 없이 실행). 모델 비교표에서 제외한다")
    if served_models:
        # **Fake 역할은 호출자가 이미 빼고 준다.** Fake 모델의 checksum 0은 빠진 값이
        # 아니라 **맞는 값**이다 — `FakeModelClient`는 프로세스 안에서 도는 코드라 해시할
        # 가중치도 토크나이저도 존재하지 않는다. 없는 것을 "미기록"으로 경고하면 dev
        # 프로필의 모든 run이 매번 이 줄을 뱉고, 그러면 진짜 문제가 묻힌다 — `tool_runner`를
        # 이 목록에서 뺀 것과 같은 논법이다(`schemas/run.py`의 그 필드 주석).
        #
        # 판정이 **역할별**인 것도 의도다. `single_gpu_12g`처럼 한 프로필 안에서 Fake와
        # 실서빙이 섞이므로, 프로필 단위로 켜고 끄면 그 프로필의 실서빙 역할을 놓친다.
        placeholder = sorted(
            f"{role}({key})"
            for role, key in served_models.items()
            if record.model_checksums.get(role) == PLACEHOLDER_DIGEST
        )
        if placeholder:
            warnings.append(
                f"model_checksums가 자리표시자(전부 0)다: {', '.join(placeholder)}. "
                "어느 모델로 돈 run인지 기록이 지시하지 못한다. 모델 비교표에서 제외한다"
            )
    gpu_warning = unmeasured_gpu_warning(gpu_unmeasured_roles)
    if gpu_warning:
        warnings.append(gpu_warning)
    if record.kb_snapshot == DEFAULT_KB_SNAPSHOT:
        warnings.append(f"kb_snapshot이 자리표시자({DEFAULT_KB_SNAPSHOT})다")
    if record.repo_commit == _UNKNOWN_COMMIT:
        warnings.append("repo_commit이 자리표시자다")
    if record.repo_dirty is not False:
        # True("dirty했다")와 None("확인 못 함") 둘 다 경고 대상이다. 어느 쪽이든 이 run을
        # repo_commit으로 재현할 수 있다고 말할 수 없다.
        state = (
            "하네스 저장소에 미커밋 변경이 있었다"
            if record.repo_dirty
            else "하네스 저장소 상태를 확인하지 못했다"
        )
        warnings.append(f"repo_dirty: {state}. 모델 비교표에서 제외한다")
    if record.device is None:
        # `tool_runner`는 여기 없다 — config에서 유도되어 빌 수가 없기 때문이다. 이 값은
        # 사람이 `--device`를 안 주면 실제로 빈다. 두 장비가 모두 지원 대상인 이상
        # (docs/portability.md) 장비를 모르는 run은 비교표의 어느 칸에도 놓을 수 없다.
        warnings.append(
            "device가 비어 있다 (--device 미지정). 어느 장비에서 돌았는지 기록되지 않아 "
            "모델 비교표에서 제외한다"
        )
    return warnings


def source_hash(artifacts: list[Artifact]) -> str:
    """분석 대상 전체의 다이제스트 집계 (계획 D-d).

    `repo_commit`만으로는 미커밋 변경을 잡지 못하고, 대상이 저장소 밖일 수도 있다.
    경로를 정렬해 넣으므로 파일 순서가 달라져도 같은 값이 나온다.
    """
    lines = sorted(f"{artifact.path}:{artifact.digest}" for artifact in artifacts)
    return sha256_text("\n".join(lines))


def _repo_state(repo_root: Path = REPO_ROOT) -> tuple[str, bool | None, list[str]]:
    """하네스 저장소의 HEAD와 미커밋 변경 여부. 못 읽으면 자리표시자와 경고를 돌려준다.

    `repo_commit`만으로는 **하네스 자신의** 미커밋 변경을 잡지 못한다. `source_hash`는
    *분석 대상*만 보므로(위 `source_hash` 주석) 그 사이에 구멍이 있었다 — 설정을 고친
    채로 돈 run이 manifest만 보면 그 커밋으로 재현 가능해 보였다.

    `repo_root`를 인자로 받는 것은 두 가지 때문이다. 첫째, 예전에는 `cwd`를 상속해서
    저장소 밖에서 CLI를 돌리면 **다른 저장소의** HEAD를 읽었다. 둘째, 그래야 clean/dirty
    두 경우를 임시 저장소로 실제로 태워 볼 수 있다.

    dirty 판정은 `git status --porcelain`이 한다.
    """
    commit, warnings = _git_line(repo_root, "rev-parse", "HEAD")
    if commit is None or len(commit) != 40:
        detail = warnings or [f"repo_commit 형식이 아니다: {commit!r}"]
        return _UNKNOWN_COMMIT, None, [f"repo_commit을 읽지 못했다: {detail[0]}"]

    # `--porcelain`의 기본 출력에는 미추적 파일이 들어간다. **그대로 둔다** —
    # `--untracked-files=no`를 붙이고 싶어질 수 있으나 그러면 안 된다. 미추적 파일도
    # 동작을 바꾼다: `prompts/` 아래 새 버전 디렉터리, `src/` 아래 새 모듈은 커밋되기
    # 전에도 실행에 영향을 준다. `repo_dirty`가 답하는 질문은 "지금 코드가 HEAD와
    # 같은가"이고, 미추적 파일이 있으면 같지 않다.
    #
    # 판정을 느슨하게 하지 않는 이유는 비대칭 때문이다. 잘못된 dirty는 경고 한 줄을
    # 낭비하지만, 잘못된 clean은 재현 불가능한 run을 비교표에 넣어 결과를 무효로 만든다.
    status, status_warnings = _git_line(repo_root, "status", "--porcelain")
    if status is None:
        return commit, None, [f"저장소 상태를 확인하지 못했다: {status_warnings[0]}"]
    # dirty는 읽기 실패가 아니라 사실이므로 여기서 경고하지 않는다. "이 run은 비교표에
    # 넣을 수 없다"는 판단은 `incomplete_fields`가 한 곳에서 내린다 — 양쪽이 다 경고하면
    # 같은 사실이 두 줄로 나와 진짜 문제가 묻힌다.
    return commit, bool(status), []


def _git_line(repo_root: Path, *args: str) -> tuple[str | None, list[str]]:
    """`git` 출력 한 덩어리. 실패하면 `None`과 사유를 돌려준다."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, [f"git {args[0]} 실패: {type(exc).__name__}"]
    return completed.stdout.strip(), []


def write_manifest(run_dir: Path, record: RunRecord) -> Path:
    """`run_manifest.json` = RunRecord. 이 파일이 없으면 그 run은 비교표에 못 들어간다."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run_manifest.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path
