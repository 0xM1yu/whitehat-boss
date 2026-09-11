"""순차 러너 — 단계 0~9를 잇는다 (ADR 0002, engineering-guide.md §1).

**v1은 순차다.** 큐를 스레드로 돌리지 않는다. 그렇게 하려면 S1이 남긴 전제 셋을 함께
풀어야 한다(계획 §5.1):

1. `StateStore` 두 구현에 락이 없고 `sqlite3.connect(check_same_thread=True)` 기본값이다
2. `FakeModelClient._seen_prefixes`가 보호되지 않아 캐시 적중률이 실행마다 달라진다
3. `TraceWriter.seq`·`BudgetLedger` 누산에 락이 없어 순번과 비용이 뒤섞인다

셋을 함께 풀지 않으면 조용히 깨지는 대신 즉시 실패하도록 지금 상태를 유지한다.
**이 목록은 삭제된 `queue.py` 모듈 docstring이 들고 있던 것을 옮겨 온 것이다**(이슈 #71) —
그 파일이 사라지면서 근거가 저장소에서 없어질 뻔했다.

`src/adapters/`를 import 하지 않는다. 어댑터 조립은 `src/cli.py`가 하고, 러너는 아래
`Dependencies` Protocol과 `read_source` 콜러블만 받는다 — 그래야 "구현체 교체가 설정만으로
가능"이 성립한다(engineering-guide.md §3, `tests/test_policy.py`가 강제).

## 예산 소진

`BudgetExhausted`는 실패가 아니라 **정상적인 종료 경로**다(architecture.md §5). 예외로
죽지 않고 그때까지 만든 Finding으로 9단계 리포트까지 간 뒤 `stopped_after`를 채운다
(전이 표 R-2). 부분 결과라는 사실은 `RunRecord.is_complete`가 False인 것으로 남는다.

## 대상 지정 단위

**심볼 단위다(W2).** 1단계 인덱서 산출물에서 심볼마다 `Location(file, symbol,
line_start, line_end)`을 만든다. 인덱스가 없는 프로필(`dev_fake`)은 **파일 단위로
되돌아가고 그 사실이 `target_plan`에 남는다.**

⚠ **그 `Location` 하나가 후보 **몇 개**가 되는지는 Scout 자리 수가 정한다.** 단일
`scout`면 하나이고, **풀이면 멤버마다 하나씩 셋이다** — 4단계에서 대상 하나를 세 멤버가
각각 보고 그 후보들이 합쳐지지 않은 채 5단계로 간다(ADR 0010 §3 방식 ① 합집합).
어느 멤버에서 왔는지는 `Candidate.scout_member`가 든다. **「대상 하나 = 후보 하나」로
읽으면 풀 run의 후보 수와 제거율 분모를 셋 배로 잘못 읽는다.**

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

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.config import SCOUT_POOL_ROLES, HarnessConfig
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
from src.orchestrator import gate, state_machine
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
from src.schemas.capsule import ContextCapsule
from src.schemas.common import PLACEHOLDER_DIGEST, Location
from src.schemas.decision import Decision
from src.schemas.enums import (
    ArtifactKind,
    CapsuleLevel,
    EvidenceKind,
    FindingState,
    ReasonCode,
    Role,
)
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import CONDITION_PATTERN, ModelSampling, RunRecord
from src.tokens import using_counter

# 실제 KB 스냅샷이 붙기 전까지 쓰는 태그. 이 값이 그대로 기록되면 경고를 남긴다 —
# `RunRecord.kb_snapshot`은 재현성 필수 항목이다(engineering-guide.md §5).
DEFAULT_KB_SNAPSHOT = "kb-none"

# `--condition`을 안 줬을 때 기록에 남는 라벨. **cli가 아니라 여기 있다** —
# `missing_run_labels()`가 이 값을 경고 문구에 실어야 하는데 러너가 cli를 import 할 수는
# 없다. `DEFAULT_KB_SNAPSHOT`과 같은 성격(사람이 주는 값의 기본치)이라 나란히 둔다.
DEFAULT_CONDITION = "B3prime@pipeline"

# **run 전 검사와 run 후 판정이 같은 문장을 쓰게 하려고 뽑았다.** 두 곳이 문구를 따로
# 만들면 갈라지고, 그러면 같은 결여가 run 전과 후에 다른 이름으로 불린다 —
# `unmeasured_gpu_warning`을 따로 뽑은 것과 같은 이유이고 그 docstring에 근거가 있다.
_DEVICE_MISSING = (
    "device가 비어 있다 (--device 미지정). 어느 장비에서 돌았는지 기록되지 않아 "
    "모델 비교표에서 제외한다"
)
_KB_PLACEHOLDER = f"kb_snapshot이 자리표시자({DEFAULT_KB_SNAPSHOT})다"

# 이쪽은 run **전에만** 낼 수 있다. 레코드에는 해석된 값이 들어가 있어서 "기본값이 찍혔다"와
# "그 라벨을 의도해서 줬다"가 구별되지 않기 때문이다 — `missing_run_labels` docstring 참조.
_CONDITION_DEFAULTED = (
    f"condition이 기본값({DEFAULT_CONDITION})으로 기록된다 (--condition 미지정). "
    "2×2 축으로 돌린 run이 pipeline 라벨로 남으면 어느 축이었는지를 산출물로 가를 수 없다"
)

# **실서빙 run에서 이 둘이 비면 시작 전에 막는다.** 경고 한 줄은 긴 run의 출력에 묻히고,
# 그 run은 시작하기도 전에 못 쓰는 것이 확정돼 있다 — `device`는 `incomplete_fields()`가
# "모델 비교표에서 제외한다"고 **판정하는** 값이고, `condition`은 잘못 굳으면 비교표가
# **섞인다**(`schemas/run.py`의 `CONDITION_PATTERN` 주석). 둘 다 GPU를 태운 뒤에 듣는 것이
# 가장 비싼 종류다.
#
# **`kb_snapshot`은 여기 없다.** `kb-none`은 아직 KB가 없다는 정직한 표기이고
# `incomplete_fields()`도 그것만은 "제외한다"고 말하지 않는다 — 경고로 충분하다.
#
# **막는 기준은 `profile.production`이 아니다.** 저장소의 프로필이 **전부**
# `production: false`라 그 필드로는 dev와 실서빙이 갈리지 않는다(2026-09-02 실측).
# `HarnessConfig.served_models()`가 그것을 가른다 — dev 프로필은 0, 실서빙 프로필은 1개
# 이상이다. **개수를 적지 않는 것은 의도다** — 프로필이 늘고 줄 때마다 이 주석이 틀린다.
#
# **문장 → 플래그 이름 매핑이다.** 거부 문구가 `--device 와 --condition 을 명시한다`처럼
# 고정 문장이면 이미 준 플래그를 다시 대라고 말하게 된다 — 조작자가 그것을 의심하며 시간을
# 태운다. 실제로 빠진 것만 이름으로 부르려면 문장에서 플래그를 되찾을 수 있어야 한다.
#
# **`served_models()`에는 `retriever` 축이 접혀 있다.** `retriever: fake`인 실서빙
# 프로필에서 이 게이트가 세는 것은 scout·auditor·critic뿐이다. hybrid 실서빙 프로필에서는
# embedding·reranker도 세어져 게이트 범위가 넓어진다.
BLOCKING_WHEN_SERVING = {_DEVICE_MISSING: "--device", _CONDITION_DEFAULTED: "--condition"}


def malformed_condition(condition: str | None) -> str | None:
    """조건 라벨이 형식에 안 맞으면 그 사유, 맞거나 미지정이면 `None`.

    **`RunRecord.condition`이 같은 정규식으로 이미 거부한다.** 그런데 그 판정은
    `_new_record`에서, 즉 **0단계를 지난 뒤**에 일어난다 — 그때는 run 디렉터리와 번호가
    이미 나갔고, `ValidationError`가 트레이스백으로 올라와 cli가 종료 코드조차 못 돌려준다.
    남는 빈 디렉터리는 R7(`gc`가 지운 run_id를 같은 날 새 run이 재사용한다)이 서 있는
    자리와 같다.

    **형식은 문자열만 보면 안다** — 프로필도 엔드포인트도 필요 없다. 그래서
    `missing_run_labels()`의 실서빙 게이트와 달리 **어느 프로필에서든** 막는다. 정책
    선택이 아니라 반드시 죽을 값에 대한 빠른 실패다.

    **결여와 따로 두는 이유.** `missing_run_labels()`는 "사람이 줘야 하는 값이 비었나"를
    묻고 이쪽은 "준 값이 형식에 맞나"를 묻는다. 한 함수에 넣으면 `None`(미지정)과
    빈 문자열·오타가 같은 목록에 섞여 실서빙 게이트가 오타까지 조건부로 봐야 한다.

    정규식은 `RunRecord`가 쓰는 것을 그대로 가져온다 — 두 곳이 다른 패턴을 들면 여기서
    통과한 값이 뒤에서 죽고, 그게 이 함수가 없애려는 상태다.
    """
    if condition is None or re.match(CONDITION_PATTERN, condition):
        return None
    return (
        f"condition {condition!r}이 라벨 형식에 맞지 않는다 — {CONDITION_PATTERN}. "
        "이대로 두면 0단계를 지난 뒤 기록을 만들 때 죽고 빈 run 디렉터리가 남는다"
    )

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


def _is_droppable(exc: ModelGatewayError) -> bool:
    """이 게이트웨이 오류가 **요청 하나에 대한 사실**인가 — 그 하나만 버리고 계속 갈 수 있는가.

    후보 단위(`_triage_by_candidate`)·단계 단위(`_run_stage_for_all`)·Finding 단위
    (`_analyze`의 critic 루프) **셋**이 같은 판정을 쓴다.
    **함수로 뺀 이유가 그것이다** — 두 곳이 각자 `status not in _DROPPABLE_STATUS`를 적으면
    한쪽만 고쳐질 때 같은 오류가 자리에 따라 다르게 처리된다.

    `status`가 없으면(연결 실패·타임아웃·비-JSON 응답) 서버 쪽 사건이라 버릴 수 없다.
    """
    return exc.status is not None and exc.status in _DROPPABLE_STATUS


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
    # 역할 → 그 역할 모델의 실 토크나이저 카운터(#96 (b)). `None`이면 기본 카운터
    # (글자수 근사)를 유지한다. `symbols_for`와 같은 경로다 — factory가 채우고 러너는
    # `ctx`로 받아 역할 캡슐 조립 직전에 `using_counter(...)`로 켠다. 러너가
    # `src/adapters/`를 import 하지 않으려고 콜러블로 받는다.
    counter_for_role: Callable[[str], Callable[[str], int] | None]
    # 위 카운터가 무엇으로 정해졌는지 (#96 (b)). `token_counter` 이벤트의 `data`가 된다.
    # `IndexSummaryLike`와 같은 이유로 **스칼라 dict**다 — 러너가 어댑터 타입을 모른다.
    counter_report: dict[str, str | None]


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
    # 3단계 LIGHT 캡슐을 target별로 한 번만 만들어 공유한다 (ADR 0010 §3 "캡슐은 한 번
    # 만든다"). Scout 풀은 대상 하나를 멤버 셋이 각각 보므로 팬아웃 뒤 세 `_InFlight`가
    # 같은 target을 공유하는데, 캡슐을 멤버마다 다시 만들면 검색 호출이 3배가 되고 세 멤버가
    # 조금씩 다른 캡슐을 볼 수 있다(토크나이저별 예산 컷). 키는 target Location이다.
    capsules: dict[tuple[str, str | None, int | None, int | None], ContextCapsule] = field(
        default_factory=dict
    )
    # 7단계 Critic 판정. **8단계 게이트 재판정의 입력이다** — 없으면 `p3`(uphold)이 거짓이
    # 되어 asan이 실패한 run에서 등급이 7단계보다 낮게 기록된다.
    decisions: dict[str, tuple[Decision, ...]] = field(default_factory=dict)
    workdirs: dict[str, str] = field(default_factory=dict)
    # 4단계에 못 올라간 수 (W2). 예산에 잘린 것과 호출이 실패한 것을 가른다.
    # ⚠ **둘의 단위가 다르다** — `deferred`는 **대상**(`candidate_max` 컷이 팬아웃 전이다),
    # `dropped`는 **시도**(`대상 × Scout 자리`). 단일 `scout`에서만 둘이 같다.
    # **여기 두는 이유가 이 클래스의 존재 이유와 같다** — 예산 소진으로 중간에 끊겨도
    # 호출자가 그 수를 읽어 경고와 리포트에 올려야 한다.
    deferred: int = 0
    dropped: int = 0
    # 7단계에 못 올라간 Finding 수 (#98 (b)). **위 둘과 단위가 다르다** — `deferred`·
    # `dropped`는 **대상 또는 시도**(바로 위)이고 이쪽은 Finding을 센다.
    # 한 수로 묶으면 경고와 리포트에서
    # 분모가 섞인다(`target_skipped`를 `critic_skipped`와 나눈 것과 같은 이유).
    critic_deferred: int = 0
    # 7단계에 **들어갔다가** critic 호출이 실패해 버린 Finding 수 (해소). `critic_deferred`와
    # 나누는 이유는 그 필드를 `deferred`·`dropped`와 나눈 이유와 같다 — 이쪽은 "안 올라갔다"
    # 이고 저쪽은 "올라가서 죽었다"라 한 수로 묶으면 7단계 분모가 섞인다.
    critic_failed: int = 0

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
        counter_for_role=deps.counter_for_role,
        # **`write_manifest`와 같은 가드다** — 산출물 디렉터리가 있고 실제로 도는 run일
        # 때만 프롬프트를 남긴다(해소). 조건을 따로 만들면 매니페스트는 있는데 프롬프트는
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
        spec, config, ctx.run_id, started_at, artifacts, deps.tool_images, deps.model_sampling
    )
    ctx.record = record
    ctx.store.put(record)

    # **어느 토큰 카운터로 셀 것인가를 여기서 남긴다 (#96 (b)).** `target_plan`과 같은
    # 계약이다 — run당 1건이고 **모델을 한 번 부르기 전에** 낸다. 카운터는 `build_deps`
    # 시점에 이미 정해져 있으므로 늦출 이유가 없고, 그 뒤 어디서 죽어도 이 run이 무엇으로
    # 쟀는지는 남아야 한다. 이것이 없던 동안 폴백은 조용했고, "토크나이저로 쟀다"를
    # 사람이 run 전에 손으로 확인해 보고서에 붙이는 것이 유일한 증거였다.
    ctx.record_token_counter(dict(deps.counter_report))

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
    except SchemaParseError as exc:
        # **후위 방어선이다 (해소).** 후보 단위는 `_triage_all`이, Finding 단위는 `_analyze`의
        # critic 루프가 이미 잡으므로 여기까지 오는 것은 **아직 가드가 없는 경로**뿐이다.
        # 그런 경로가 생겼을 때 산출물이 통째로 사라지는 대신 매니페스트와 리포트까지 가고
        # 중단 사유가 남는다 — `ModelGatewayError` 갈래가 같은 이유로 있고, 그 주석이
        # "이 갈래가 없던 동안에는 예외가 `write_manifest` 앞을 뚫고 나가 산출물이 하나도
        # 안 남았다"고 적어 둔 그 사고의 재발을 막는다.
        stopped_after = int(ctx.current_stage)
        stop_reason = ReasonCode.SCHEMA_PARSE_FAILED
        warnings.append(f"스키마 파싱 실패로 중단했다: {error_detail(exc)}")

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
            f"시도(대상 × Scout 자리) {state.dropped}건을 호출 실패로 버렸다. "
            f"사유는 trace의 target_skipped와 그 target의 model_call에 있다. "
            f"⚠ 풀에서는 이 수가 「못 본 대상 수」가 아니다"
        )
    # **7단계 절단도 같은 자리에 올린다 (#98 (b)).** 위 두 경고의 논거가 그대로 적용되는데,
    # 여기서는 한 겹 더 나쁘다 — target 절단은 대상이 사라져서 안 보이지만 Finding 절단은
    # F-9가 상태를 안 바꿔서 **44줄이 멀쩡히 찍힌 채** 전량을 본 것처럼 읽힌다.
    if state.critic_deferred:
        warnings.append(
            f"Finding {state.critic_deferred}건을 critic_top_n"
            f"({config.thresholds.critic_top_n})로 잘라 7단계에 올리지 않았다. "
            f"trace의 critic_skipped가 목록이다"
        )
    # **7단계에서 죽은 Finding도 같은 자리에 올린다 (해소).** 위 절단 경고와 같은 논거인데
    # 여기서는 한 겹 더 나쁘다 — 절단은 사람이 상한을 정해서 일어나지만 이쪽은 예정에 없던
    # 손실이고, 경고가 없으면 critic을 7건 돌린 run과 3건이 죽은 run이 겉보기에 같아진다.
    if state.critic_failed:
        warnings.append(
            f"Finding {state.critic_failed}건이 7단계 critic 호출 실패로 판정을 못 받았다. "
            f"사유는 trace의 critic_failed에 있다"
        )

    findings = list(state.findings.values())
    # 후보 위치를 얻는다 (#101 a·candidate_location). Finding은 `candidate_id`만 들고
    # 파일은 `Candidate.location`에 있다. `Candidate.location.symbol`은 모델이 쓴 값이라
    # `null`일 수 있어 dedupe 키에는 파일만 쓴다.
    by_cand = {c.candidate_id: c.location for c in state.candidates}
    files = {
        f.finding_id: (by_cand[f.candidate_id].file if f.candidate_id in by_cand else None)
        for f in findings
    }
    # **등급을 리포트에 넘긴다 (#101·PR #37).** Finding에 등급 필드가 없어 등급은 게이트
    # 판정이라 trace에만 있었고, 그래서 리포트가 대표를 고를 때 등급을 못 보고(E3 삭제)
    # 최종 산출물에 등급을 못 실었다. `gate.evaluate` 순수 함수로 다시 매긴다.
    #
    # **`evidences_for`로 이 Finding이 참조하는 근거만 거른다** — 6·7·8단계 게이트가 전부
    # 그렇게 하고(`gate_node`·`critic`·`validate`), 안 거르면 artifact 단위 근거 묶음의 다른
    # Finding 근거로 등급이 올라가 제거율이 무의미해진다. Fake auditor에선 근거가 전량이라
    # 무동작이지만 **실서빙 auditor가 붙으면 켜진다**(#36의 B1′/B3′). `ctx.evaluate_gate`가
    # 아니라 순수 함수라 `gate_eval`을 재발행하지 않는다(제거율 분모 이중 계상 방지).
    #
    # **E4는 이 경로로 안 나온다** — asan 근거가 `state.evidences`가 아니라 `ctx.store`에만
    # 들어가고, Fake auditor에선 E4가 원리상 도달 불가다(해소). E4가 실제로 열리면 그 근거를
    # 여기로 모으는 일이 따라온다(`validate.run`이 `produced`를 반환하는데 지금 버린다).
    grades = {
        f.finding_id: gate.evaluate(
            f,
            gate.evidences_for(f, state.evidences.get(f.finding_id, ())),
            state.decisions.get(f.finding_id, ()),
            state.missings.get(f.finding_id, ()),
            candidate_location=by_cand.get(f.candidate_id),
        ).grade.value
        for f in findings
    }
    rendered = report.run(
        ctx,
        findings=findings,
        deferred=state.deferred,
        dropped=state.dropped,
        critic_deferred=state.critic_deferred,
        critic_top_n=config.thresholds.critic_top_n,
        files=files,
        grades=grades,
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
        try:
            after, decision = critic.run(
                ctx,
                finding=finding,
                evidences=state.evidences.get(finding.finding_id, ()),
                missings=state.missings.get(finding.finding_id, ()),
            )
        except SchemaParseError as exc:
            # **Finding 하나의 실패로 run을 잃지 않는다 (해소).** `_triage_all`이 후보에 대해
            # 하는 것과 같은 판정이고, 여기에 그 처리가 없던 동안 `run_20260827_0002`는
            # 16 m 26 s를 돌고 critic 첫 호출에서 죽어 **산출물이 하나도 안 남았다** —
            # 모델 호출 78건이 trace에만 있고 `report.md`도 `run_manifest.json`도 없어
            # 어느 커밋으로 돈 run인지 기록이 지시하지 못했다.
            _drop_finding(ctx, state, finding, _REASON_SCHEMA, exc)
            continue
        except ModelGatewayError as exc:
            # **요청 단위 거절은 이 Finding에 대한 사실이다 (해소 후속).** 처음에는 여기서
            # `ModelGatewayError`를 일부러 안 잡았다 — `_DROPPABLE_STATUS` 판정을 복사하면
            # 같은 규칙이 두 곳에 따로 산다는 이유였고, "그때는 `_drop`의 갈래를 Finding
            # 쪽으로 끌어오는 것이 맞다"고 적어 뒀다. **그때가 첫 실서빙 run에서 바로 왔다**:
            # 2026-08-28 `run_20260828_0001`이 7단계에서 400(`입력 24,577 + 출력 8,192 >
            # 컨텍스트 32,768`)을 받고 `stopped_after: 7`로 끊겼다. 후보였다면 하나만 버리고
            # 완주했을 사건이다.
            #
            # 그래서 판정을 복사하지 않고 `_is_droppable`로 **빼서 공유한다.** 서버 쪽
            # 사건(상태 없음·5xx)은 그대로 올려 보내고 `run_pipeline`이 매니페스트까지 간다.
            if not _is_droppable(exc):
                raise
            _drop_finding(ctx, state, finding, _REASON_GATEWAY, exc)
            continue
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
    # **실행할 대상의 이름을 남긴다 (해소).** 위 `target_plan`은 개수만 싣고 이름을 싣는 것은
    # 아래 `_emit_skipped`뿐이었다 — **잘린 대상은 이름이 남고 실행된 대상은 안 남았다.**
    # 그래서 정답 대조가 "무엇을 봤는가"를 물을 때 기록은 "몇 개를 봤는가"까지만 답했다.
    #
    # 모델을 부르기 전에 낸다. `target_plan`과 같은 계약이고, 그 뒤 어디서 죽어도 이 run이
    # 무엇을 보려 했는지는 남는다.
    for item in plan.targets:
        _emit_planned(ctx, item.location)
    state.deferred = len(deferred)
    for item in deferred:
        _emit_skipped(ctx, item.location, stage=Stage.SIGNALS, reason=_REASON_OVER_MAX)


@dataclass
class _Reached:
    """target 하나가 어느 단계까지 갔는지 (PR #50).

    **가변인 것이 목적이다.** `_triage_one`이 예외로 빠져나가면 반환값이 없으므로,
    "어디까지 갔는가"를 돌려줄 길이 이것뿐이다. 호출자가 만들어 넘기고 호출자가 읽는다.

    기본값이 `RETRIEVAL`인 이유는 `_triage_one`의 첫 모델 호출이 3단계 캡슐 조립이기
    때문이다 — 그전에 죽으면(예: 인덱스 조회) 3이 맞고, 0으로 두면 없는 단계를 적는다.
    """

    stage: Stage = Stage.RETRIEVAL


@dataclass
class _InFlight:
    """target 하나가 **단계 사이에** 들고 가는 것.

    후보 단위에서는 이 값들이 `_triage_one`의 지역 변수였다 — 한 함수 안에서 3~6단계가
    연달아 돌기 때문이다. 단계 단위에서는 4단계와 5단계 사이에 다른 target 수백 개가
    끼므로 **어딘가에 놓여 있어야 한다.**

    `_Accumulator`가 아니라 여기 두는 이유: `_Accumulator`는 run 전체의 산출물(후보·
    Finding·근거)을 모으는 자리이고, 이쪽은 **한 target이 다음 단계에 넘길 입력**이다.
    섞으면 「부분 결과」가 무엇인지가 흐려진다 — 그 구별이 ADR 0009이 재정의하라고
    적은 둘 중 하나다.
    """

    target: _Target
    reached: _Reached
    # 이 item이 맡은 4단계 Scout 자리 (Option C — 팬아웃을 계획에서 한다). 단일 프로필은
    # `Role.SCOUT` 하나, 풀 프로필은 target 하나가 `scout_memory`·`scout_boundary`·
    # `scout_injection` 세 item으로 펼쳐지고 각자 이 role을 들고 온다. `_step_scout`이
    # `scout.run(role=...)`에 넘기고, `scout_member` stamp는 그 role에서 나온다.
    role: Role = Role.SCOUT
    capsule: ContextCapsule | None = None
    candidate: Candidate | None = None
    finding: Finding | None = None
    missings: tuple[MissingEvidence, ...] = ()


class _StageOutOfOrder(RuntimeError):
    """앞 단계가 채웠어야 할 값이 비어 있다 — **배선 결함이지 후보의 실패가 아니다.**

    `ModelGatewayError`·`SchemaParseError`와 **다른 타입인 것이 요점이다.** 저 둘은
    "이 후보가 실패했다"라서 `_drop`이 그 후보만 버리고 계속 가는데, 이쪽은
    "루프가 단계를 잘못된 순서로 돌았다"라 다음 후보도 똑같이 실패한다. 같은 타입으로
    올리면 폐기 카운터가 배선 결함을 후보 실패로 세고, 상한 셋을 채워 `_TooManyDrops`가
    되면서 **원인이 「연속 target 실패」로 기록된다.** `_TooManyDrops`가 게이트웨이
    중단과 다른 타입인 사유(그 docstring)와 같은 사유다.

    **그래서 `_run_stage_for_all`·`_triage_by_candidate`가 이것을 안 잡는다** —
    `run_pipeline`까지 올라가 리포트·매니페스트를 남기고 중단 사유로 적힌다.

    ⚠ **`assert`를 안 쓴다.** `origin/main`의 `src/`에 `assert`가 0건이고, 이 저장소가
    불변식을 알리는 방식은 전용 예외다(`ConfigError`·`PromptError`·`BenchApplyError`·
    `_TooManyDrops`). 그리고 `assert`는 `python -O`에서 사라져 값이 노드 깊숙이 흘러
    불명확한 `TypeError`가 된다(@sumin0218 리뷰 #161-4).
    """


def _scout_roles(ctx: RunContext) -> tuple[Role, ...]:
    """이 프로필의 4단계 Scout 자리. 풀이면 멤버 셋, 아니면 단일 `scout` 하나.

    `config.SCOUT_POOL_ROLES`가 정본이다 — 프로필이 그 셋을 선언했으면 풀이고, 아니면
    단일 `scout`이다. 둘 중 하나임은 `ProfileEntry._declares_one_scout_arrangement`가 이미
    강제하므로 여기서는 「풀이 온전히 선언됐나」만 본다. 순서는 `SCOUT_POOL_ROLES` 그대로라
    팬아웃 순서·ID 발급 순서가 두 run 사이에서 안 흔들린다.
    """
    roles = ctx.config.profile.roles
    if all(member in roles for member in SCOUT_POOL_ROLES):
        return tuple(Role(member) for member in SCOUT_POOL_ROLES)
    return (Role.SCOUT,)


def _target_key(location: Location) -> tuple[str, str | None, int | None, int | None]:
    """캡슐 캐시 키. 같은 대상을 보는 풀 멤버 item들이 같은 키를 낸다.

    **튜플인 것은 의도다.** 예전에는 네 필드를 `::`로 이어 문자열로 접었는데, 구분자가
    값 안에 나타나면 서로 다른 대상이 같은 키가 된다. 캐시가 섞이면 **멤버가 다른
    대상의 캡슐을 보고** 그 사실이 기록 어디에도 안 남는다. 지금 필드 값으로 그 충돌이
    일어나는지는 안 셌다 — 튜플이면 **세어 볼 필요 자체가 없다**(PR #178 리뷰 L2).
    """
    return (location.file, location.symbol, location.line_start, location.line_end)


def _step_retrieval(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """3단계 — LIGHT 캡슐. 항상 다음 단계로 넘긴다.

    **캡슐은 target당 한 번 만든다** (ADR 0010 §3). 풀 프로필에서는 같은 target을 보는
    멤버 셋이 각각 이 단계를 지나므로, 첫 멤버가 만든 캡슐을 `state.capsules`에 넣어
    나머지가 재사용한다 — **세 멤버가 같은 캡슐을 본다.** 단일 프로필은 target당 item이
    하나라 캐시가 항상 miss이고 동작이 그대로다.

    ⚠ **「검색 작업량이 3배가 되는 것을 막는다」가 아니다.** Retriever가 이미 자기 `_cache`를
    들고 있어(`fake_retriever.search`·`hybrid_retriever.search`의 첫 줄) 이 캐시를 꺼도
    **실제 검색 수는 같다** — 실측: 이 캐시 켬/끔에서 `search()` 호출 5↔9인데 캐시 미스는
    **양쪽 3으로 동일**하다 `[실측 2026-09-11]`.

    ⚠ **바뀌는 것은 회계다.** 이 캐시가 `retrieval.run()` **앞에서** 단락하므로 Retriever의
    `on_cache` 훅을 지나지 않아 **`cache` 이벤트가 준다**(9→5, 적중률 0.667→0.40).
    `evaluation.md` §5.2가 capsule cache hit ratio를 지표로 들고 `LateCacheHook`이
    *"캐시 이벤트가 새면 hit ratio가 0으로 보이고 「캐시가 안 맞았다」와 구분되지 않는다"*고
    적는다 — **이 단락은 그 누출을 설계로 만든다.** 미해결로 등재돼 있다(인덱스 참조).

    역할 캡슐 조립~검증을 그 역할 모델의 토크나이저로 감싼다(#96 (b) 3b). 첫 멤버의
    카운터로 켠다 — 캡슐이 공유되므로 예산 컷도 그 하나로 정해진다. `counter_for_role`이
    `None`을 주면(fake·토크나이저 없음) `using_counter`가 아무것도 바꾸지 않아 기본
    카운터(글자수 근사)가 유지된다.

    ⚠ **그래서 `capsule_budget_tokens`가 멤버 2·3에는 사실상 강제되지 않는다.** 캐시 적중은
    `model_validate`를 다시 타지 않으므로, 첫 멤버 기준으로 상한 이내인 캡슐이 다른 계열
    토크나이저 기준으로 상한을 넘어도 **어디서도 안 걸리고 기록에도 안 남는다.** 로스터는
    풀 세 자리를 서로 다른 세 모델로 정해 두었다(`CONTEXT.md` §1 — 그중 하나는 계열도
    다르다). 미해결로 등재돼 있다(인덱스 참조). ⚠ **Fake 프로필에서는 전 역할이 같은 기본
    카운터라 이 갈림이 재현되지 않는다** — 테스트로 못 잡는 자리다.
    """
    key = _target_key(item.target.location)
    cached = state.capsules.get(key)
    if cached is not None:
        item.capsule = cached
        return True
    with using_counter(ctx.counter_for_role(item.role.value)):
        item.capsule = retrieval.run(ctx, target=item.target.location, level=CapsuleLevel.LIGHT)
    state.capsules[key] = item.capsule
    return True


def _step_scout(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """4단계 — 분류. 승격되지 않은 후보는 여기서 멈춘다(`False`)."""
    item.reached.stage = Stage.SCOUT
    if item.capsule is None:
        raise _StageOutOfOrder("4단계가 3단계보다 먼저 돌았다 — capsule이 비어 있다")
    candidate = scout.run(
        ctx,
        artifact=item.target.artifact,
        capsule=item.capsule,
        evidences=item.target.evidences,
        role=item.role,
    )
    item.candidate = candidate
    state.candidates.append(candidate)
    return candidate.state is FindingState.TRIAGED


def _step_auditor(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """5단계 — EXPANDED 캡슐 + 심층 분석.

    auditor는 EXPANDED 캡슐을 보므로 auditor 카운터로 켠다. 역할마다 모델(=토크나이저)이
    다를 수 있어 scout와 따로 감싼다 — 조립과 `CapsuleSection` 검증이 같은 카운터를 봐야
    한다(`src/tokens.py` docstring).
    """
    item.reached.stage = Stage.AUDITOR
    candidate = item.candidate
    if candidate is None:
        raise _StageOutOfOrder("5단계가 4단계보다 먼저 돌았다 — candidate가 비어 있다")
    evidences, artifact = item.target.evidences, item.target.artifact
    with using_counter(ctx.counter_for_role(Role.AUDITOR.value)):
        expanded = retrieval.run(ctx, target=item.target.location, level=CapsuleLevel.EXPANDED)
    finding, missings = auditor.run(
        ctx, candidate=candidate, capsule=expanded, evidences=evidences
    )
    item.finding, item.missings = finding, tuple(missings)
    state.update(finding)
    state.evidences[finding.finding_id] = evidences
    state.missings[finding.finding_id] = tuple(missings)
    state.workdirs[finding.finding_id] = artifact.path
    return True


def _step_gate(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """6단계 — Evidence Gate 등급."""
    item.reached.stage = Stage.GATE
    finding, candidate = item.finding, item.candidate
    if finding is None or candidate is None:
        raise _StageOutOfOrder("6단계가 5단계보다 먼저 돌았다 — finding 또는 candidate가 비어 있다")
    graded, _ = gate_node.run(
        ctx,
        finding=finding,
        evidences=item.target.evidences,
        missings=item.missings,
        candidate_location=candidate.location,
    )
    state.update(graded)
    return True


# 한 단계의 실행자. **네 `_step_*`이 전부 이 모양이어야 한다** — 하나라도 갈리면
# 디스패처가 특수분기를 갖게 되고, 그 단계의 표 항목은 아무도 안 읽는 죽은 값이 된다.
# `state`를 안 쓰는 단계도 받는다(`_step_retrieval`).
TriageStep = Callable[[RunContext, "_InFlight", "_Accumulator"], bool]

# 3~6단계를 도는 순서와 각 단계의 실행자. **후보 단위와 단계 단위가 같은 표를 읽는다** —
# 두 경로가 각자 순서를 적으면 한쪽만 고쳐질 때 같은 run이 모드에 따라 다른 단계를 탄다.
#
# **표를 통해서만 부른다.** 특수분기를 하나라도 두면 그 항목이 죽는다 — 표는 권위 있어
# 보이는데 아무도 그 칸을 안 읽으므로, 단계를 재배열하거나 앞에 하나 끼울 때 표는
# 맞아 보이는데 엉뚱한 함수가 돈다. 재현으로 크기를 쟀다(@sumin0218 리뷰 #161-1): SCOUT↔
# AUDITOR 콜러블을 맞바꾸면 64건이 실패하는데, 예전 코드에서 RETRIEVAL 항목을 엉뚱한
# 함수로 바꿔도 **104건 전부 통과했다.** `tests/test_pipeline_e2e.py`의
# `test_every_triage_step_takes_the_same_arguments`가 그 균일성을 고정한다.
_TRIAGE_STEPS: tuple[tuple[Stage, TriageStep], ...] = (
    (Stage.RETRIEVAL, _step_retrieval),
    (Stage.SCOUT, _step_scout),
    (Stage.AUDITOR, _step_auditor),
    (Stage.GATE, _step_gate),
)


def _triage_all(ctx: RunContext, plan: _TargetPlan, state: _Accumulator, last: int) -> None:
    """3~6단계를 돈다. **어느 루프인지는 프로필의 `execution.mode`가 정한다** (ADR 0009).

    선언 안 한 프로필은 `per_candidate`다 — 지금까지의 경로 그대로이고, 그것이 ADR 0009이
    이 결정을 「되돌리기 싼」 것으로 부른 근거다. `tests/test_pipeline_e2e.py`의
    `test_per_candidate_is_the_default_and_interleaves_stages`가 그 보존을 고정한다.
    """
    execution = ctx.config.profile.execution
    if execution is not None and execution.mode == "per_stage":
        _triage_by_stage(ctx, plan, state, last)
        return
    _triage_by_candidate(ctx, plan, state, last)


def _triage_by_stage(ctx: RunContext, plan: _TargetPlan, state: _Accumulator, last: int) -> None:
    """**모든 target이 K단계를 마친 뒤에 K+1이 시작한다** (ADR 0009 결과 2).

    후보 단위와 **같은 `_TRIAGE_STEPS`를 읽고 바깥 루프만 뒤집는다.** 두 경로가 각자
    단계 순서를 적으면 한쪽만 고쳐질 때 같은 run이 모드에 따라 다른 단계를 탄다.

    ## 이 루프에서 뜻이 바뀌는 둘 (ADR 0009 「함께 옮겨야 하는 것」)

    **1. 연속 폐기 카운터가 「이 단계에서 몇 건이 연속으로 죽었나」가 된다.**
    후보 단위에서는 카운터가 run 전체에 하나였고 리셋 조건이 「후보 하나가 3~6단계를
    완주」였다. 여기서는 **단계마다 0에서 시작하고**, 「이 target이 이 단계를 마쳤다」가
    리셋이다. 바뀌는 이유는 뜻이 실제로 달라서다 — 단계 단위에서 4단계를 전부 통과한
    run은 「scout는 멀쩡하다」를 증명한 것이고, 그 사실을 5단계 실패가 지우면 안 된다.
    반대로 한 단계 안에서 연속 셋이 죽으면 **그 단계가 체계적으로 고장 난 것**이고
    남은 단계를 태울 이유가 없다.

    ⚠ **그래서 run 전체의 연속 폐기 상한은 모드에 따라 다르다.** `per_stage`에서는
    단계 넷이 각각 최대 2건까지 견디므로 run 하나가 견디는 총 폐기 수가 늘어난다.
    `_MAX_CONSECUTIVE_DROPS`는 그대로 두었다 — 그 값이 잠정치라고 자기 주석이 적고
    (*"실대상 run에서 후보 단위 실패가 얼마나 자주 나는지 재 본 적이 없다"*), 여기서
    모드별로 가르면 **재 보기도 전에 두 값을 갖게 된다.**

    **2. 부분 결과가 「K단계까지 간 후보 전부」가 된다.** 예산이 5단계 도중에 소진되면
    후보 단위에서는 완주한 후보 몇과 아무것도 아닌 나머지가 남지만, 단계 단위에서는
    **4단계를 마친 후보 전부**가 남는다. `BudgetExhausted`를 여기서 잡지 않는 것은
    후보 단위와 같다 — `run_pipeline`이 처리하고, 그때까지 `state`에 쌓인 것이 곧
    부분 결과다. 이 루프는 그 「쌓인 것」을 단계 경계에서 가지런하게 만든다.
    """
    # Option C — 팬아웃을 계획에서 한다. target 하나가 Scout 자리마다 item 하나로 펼쳐진다
    # (풀이면 셋, 단일이면 하나). 스텝 계약은 1:1 그대로라 `_run_stage_for_all`·다운스트림은
    # 안 바뀐다. `candidate_max`는 target을 자르지 후보를 자르지 않으므로 컷은 위 `_plan_targets`
    # 그대로이고, 여기서 target당 멤버 수만큼 후보가 나온다.
    roles = _scout_roles(ctx)
    items = [
        _InFlight(target=target, reached=_Reached(), role=role)
        for target in plan.targets
        for role in roles
    ]
    for stage, step in _TRIAGE_STEPS:
        if last < stage:
            return
        items = _run_stage_for_all(ctx, stage, step, items, state)


def _run_stage_for_all(
    ctx: RunContext,
    stage: Stage,
    step: TriageStep,
    items: list[_InFlight],
    state: _Accumulator,
) -> list[_InFlight]:
    """한 단계를 살아 있는 target 전부에 태우고, 다음 단계로 갈 것만 돌려준다.

    폐기 판정은 후보 단위(`_triage_by_candidate`)와 **같은 표**다 — `_is_droppable`이
    그 판정을 함수로 들고 있는 이유가 정확히 이것이다(그 docstring). 여기서 다시
    `status not in _DROPPABLE_STATUS`를 적으면 한쪽만 고쳐질 때 같은 오류가 모드에
    따라 다르게 처리된다.

    ## ⚠ 한계 — Scout 풀에서 이 카운터는 체계적 고장을 못 잡는다 (미해결, PR #178 리뷰)

    **풀 멤버 하나가 전량 실패해도 이 카운터는 상한에 안 닿는다.** `items`가 target ×
    역할로 펼쳐져 있는데 `consecutive`는 그 열 전체에 **하나뿐이고, 성공 하나가 그것을
    0으로 되돌린다** — 즉 카운터가 역할을 안 가른다. 그래서 고장난 멤버가 매번 죽어도
    **형제 멤버의 성공이 그때마다 카운터를 지운다.**

    그래서 그 run은 `stopped_after`가 `None`인 채 끝나고 `is_complete`가 **참이 된다** —
    Scout 커버리지의 1/3이 통째로 빈 run이 정상 완주로 보이고 비교표에 들어간다.
    `scout_member`별 집계가 어디에도 없어(`report.py`·`run.py`·`trace.py` grep 0건)
    **어느 멤버가 빠졌는지도 기록에서 못 되짚는다.**

    ⚠ **위 "연속 셋이 죽으면 그 단계가 체계적으로 고장 난 것"은 단일 `scout`에서만
    참이다.** 풀에서는 한 역할의 실패가 연속으로 쌓이지 않고, 반대로 연속 셋이 **서로
    다른 역할**일 수 있다.

    **오늘은 물리지 않는다** — `config/profiles.yaml`의 어느 프로필도 풀을 선언하지
    않는다. **물리는 시점은 풀 프로필을 커밋하는 PR이다.** 그 PR이 이 한계를 먼저
    처리한다 — 고치는 방향은 연속 실패를 **역할별로** 세거나, `scout_member`별 산출 수를
    기록에 남겨 0건인 멤버가 보이게 하는 것이다.

    `tests/test_pipeline_e2e.py`의
    `test_a_broken_pool_member_does_not_trip_the_circuit_breaker_yet`가 이 한계를 실측으로
    고정하고 **이 서술과 묶는다** — 고치면 그 검사가 실패해서 이 절을 지우게 만든다.
    """
    consecutive = 0
    survivors: list[_InFlight] = []
    for item in items:
        try:
            keep = step(ctx, item, state)
        except ModelGatewayError as exc:
            if not _is_droppable(exc):
                # 서버 쪽 사건이다. `run_pipeline`이 받아 리포트·매니페스트까지 간다.
                raise
            consecutive = _drop(
                ctx,
                state,
                item.target,
                _REASON_GATEWAY,
                exc,
                consecutive,
                stage=item.reached.stage,
                systematic=exc.status not in _SIZE_STATUS,
            )
        except SchemaParseError as exc:
            consecutive = _drop(
                ctx, state, item.target, _REASON_SCHEMA, exc, consecutive, stage=item.reached.stage
            )
        else:
            # 이 단계를 마쳤다. 앞의 실패는 이 단계의 체계적 고장이 아니었다.
            consecutive = 0
            if keep:
                survivors.append(item)
    return survivors


def _triage_by_candidate(
    ctx: RunContext, plan: _TargetPlan, state: _Accumulator, last: int
) -> None:
    """target을 차례로 태운다. **후보 하나의 실패로 run을 잃지 않는다.**

    `BudgetExhausted`는 여기서 잡지 않는다 — 실패가 아니라 정상 종료 경로이고
    `run_pipeline`이 처리한다(모듈 docstring).

    ⚠ **Scout 풀에서는 연속 폐기 카운터가 체계적 고장을 못 잡는다.**
    **풀 멤버 하나가 전량 실패해도 상한에 안 닿는다.** 이 루프도 같은 모양이다:
    `consecutive = 0` 리셋이 target 루프 **안의 역할 루프**에 있어 형제 멤버의 성공이
    매번 카운터를 지운다. 전문(결과·범위·고치는 방향)은 `_run_stage_for_all`의
    「한계」 절에 있다.

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
    # Option C — target × Scout 자리로 편다(풀이면 멤버 셋, 단일이면 하나). 각 (target, role)이
    # 한 시도이고, 연속 폐기 카운터는 그 시도 단위로 센다 — 한 멤버가 완주하면 리셋된다.
    # 풀에서는 같은 target의 첫 멤버가 만든 캡슐을 `state.capsules`가 나머지에 물려 준다.
    roles = _scout_roles(ctx)
    for target in plan.targets:
        for role in roles:
            # 이번 시도가 어디까지 갔는지를 `_triage_one`이 여기에 적는다 (PR #50). 예외는
            # 어느 노드에서 났는지를 안 들고 온다 — 들고 오게 하려면 `ModelGatewayError`·
            # `SchemaParseError`에 필드를 늘려야 하고 그것은 `src/interfaces/` 변경이다.
            # 실패 단계를 아는 유일한 자리가 `_triage_one` 자신이라 그쪽이 적고 이쪽이 읽는다.
            reached = _Reached()
            try:
                _triage_one(ctx, target, state, last, reached, role=role)
            except ModelGatewayError as exc:
                if not _is_droppable(exc):
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
                    ctx,
                    state,
                    target,
                    _REASON_GATEWAY,
                    exc,
                    consecutive,
                    stage=reached.stage,
                    systematic=systematic,
                )
            except SchemaParseError as exc:
                consecutive = _drop(
                    ctx, state, target, _REASON_SCHEMA, exc, consecutive, stage=reached.stage
                )
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
    stage: Stage,
    systematic: bool = True,
) -> int:
    """시도 하나를 버리고 기록한다. 연속 상한에 닿으면 `_TooManyDrops`를 올린다.

    ⚠ **시도는 `(target, Scout 자리)`다 — target이 아니다.** 팬아웃 뒤 `items`가 그 곱으로
    펼쳐지고 이 함수가 시도마다 불린다. 단일 `scout`에서는 자리가 하나라 둘이 같고,
    **풀에서만 갈린다.** 그래서 `state.dropped`와 `count(target_skipped)`로 **「못 본 대상
    수」를 셀 수 없다** — 형제 멤버가 같은 대상을 보기 때문이다. 미해결로 등재돼 있다.

    `systematic=False`는 "이 후보에 대한 사실이지 run에 대한 사실이 아니다"라는 뜻이다
    (크기 거절). 버리고 기록하는 것은 같고 카운터만 건드리지 않는다 — **내리지도
    않으므로** 체계적 실패와 번갈아 나와도 상한에 닿는다.

    **`stage`는 호출자가 준다 (PR #50).** 예전에는 여기서 `Stage.SCOUT`으로 박았는데,
    `_triage_one`은 3~6단계를 연달아 태우므로 auditor에서 죽은 target도 기록은 4가 됐다.
    `run_20260828_0002`의 `target_skipped` 3건이 전부 그랬다 — 실패한 `model_call`은
    셋 다 5단계 auditor인데 skip 기록은 4다. `_emit_skipped`가 자기 docstring에
    "**`stage`가 곧 어디서 잘렸는가다**"라고 적어 뒀고, 박아 둔 값은 그 문장을 못 지킨다.
    """
    _emit_skipped(ctx, target.location, stage=stage, reason=reason, detail=error_detail(exc))
    state.dropped += 1
    if not systematic:
        return consecutive
    if consecutive + 1 >= _MAX_CONSECUTIVE_DROPS:
        raise _TooManyDrops(
            f"후보 {_MAX_CONSECUTIVE_DROPS}건이 연속으로 실패했다. 마지막 사유: {exc}"
        ) from exc
    return consecutive + 1


def _drop_finding(
    ctx: RunContext, state: _Accumulator, finding: Finding, reason: str, exc: Exception
) -> None:
    """Finding 하나를 7단계에서 버리고 기록한다 (해소). `_drop`의 Finding 판이다.

    **`_drop`과 달리 연속 폐기 카운터가 없다.** 그 장치(`_TooManyDrops`)는 target 수백 개를
    태우는 것을 막으려는 것인데 `selected`는 `critic_top_n`으로 이미 묶여 있다(실측 8 이하).
    상한이 다른 두 루프에 같은 카운터를 두면 어느 쪽이 상한에 닿았는지가 사유에서 사라진다.

    **`state.dropped`가 아니라 `critic_failed`를 올린다.** 그쪽은 시도를 세고 이쪽은
    Finding을 세므로 한 수로 묶으면 분모가 섞인다(`_Accumulator` 주석과 같은 판단).
    """
    state.critic_failed += 1
    ctx.trace.emit(
        EventKind.CRITIC_FAILED,
        stage=Stage.CRITIC,
        subject_kind="finding",
        subject_id=finding.finding_id,
        reason=reason,
        error_detail=error_detail(exc),
    )


def _emit_planned(ctx: RunContext, target: Location) -> None:
    """4단계에 올린 target 하나를 기록한다 (해소).

    **`_emit_skipped`와 같은 필드를 같은 이름으로 싣는다.** 둘을 나란히 놓고 세는 것이 이
    이벤트의 용도라, 한쪽만 `symbol`을 `None`으로 두거나 이름을 달리 하면 대조가 안 된다.
    빈 문자열·0으로 채우는 관용구도 그쪽에서 가져온다 — `Location`의 `symbol`·`line_start`가
    옵셔널이고(파일 단위 폴백에서는 실제로 빈다) 그 사실 자체가 #95의 폴백 여부를 지시한다.

    **`reason`이 없다.** 그쪽은 "왜 못 갔는가"를 싣지만 이쪽은 간 것이라 사유가 없다.
    빈 사유를 넣으면 두 이벤트가 같은 모양이 되어 합쳐 세는 실수를 부른다.
    """
    ctx.trace.emit(
        EventKind.TARGET_PLANNED,
        stage=Stage.SIGNALS,
        file=target.file,
        symbol=target.symbol or "",
        line_start=target.line_start or 0,
    )


def _emit_skipped(
    ctx: RunContext, target: Location, *, stage: Stage, reason: str, detail: str = ""
) -> None:
    """4단계에 올리지 못한 target 하나를 기록한다.

    `Candidate`를 만들어 `queued`로 두지 않는 이유는 `EventKind.TARGET_SKIPPED` 주석에
    적었다 — 점수를 지어내야 하고 그 값이 Scout 점수 분포(#91의 입력)에 섞인다.

    **`stage`가 곧 어디서 잘렸는가다.** 예산 절단은 2단계 뒤 계획 시점이고 후보 단위
    실패는 그 target이 실제로 죽은 노드다 — 둘을 한 값으로 적으면 예산에 잘린 target이
    "4단계에 들어갔다가 죽었다"로 읽힌다. `reason`과 중복이 아니라 서로를 검증한다.

    **후보 단위 실패가 4로 고정이 아니게 된 것은 PR #50부터다.** `_drop`이 `Stage.SCOUT`을
    박고 있었는데 `_triage_one`은 3~6단계를 연달아 태우므로 auditor에서 죽은 target도 4로
    남았다. 지금은 `_Reached`가 실제 단계를 들고 오므로 3~6이 나올 수 있다 — 3은 **LIGHT**
    캡슐 조립(실서빙 retriever에서만), 4는 scout, 5는 **EXPANDED 캡슐 조립 또는** auditor,
    6은 게이트다.

    **5가 그 둘을 겹치는 것은 카운터 경계를 따라간 결과다.** `_triage_one`이 `reached.stage`를
    EXPANDED 조립 **앞에서** 올리는데, 그 조립이 이미 auditor 카운터 안에서 도는 자리이기
    때문이다(그 자리 `using_counter` 주석). 갈라야 할 때는 그 target에 auditor `model_call`이
    있는지 본다 — 없으면 조립에서 죽은 것이다. 자동 채점이 5를 "auditor 모델 실패"로 세면
    조립 실패가 그 분자에 섞인다.

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


def _triage_one(
    ctx: RunContext,
    target: _Target,
    state: _Accumulator,
    last: int,
    reached: _Reached,
    *,
    role: Role = Role.SCOUT,
) -> None:
    """target 하나를 한 Scout 자리(`role`)로 3~6단계에 태운다. 승격되지 않은 후보는 멈춘다.

    **후보 단위(`execution.mode: per_candidate`, 기본값)의 경로다.** 단계 단위는
    `_triage_by_stage`가 **같은 `_TRIAGE_STEPS`를 바깥 루프만 뒤집어** 돈다. 풀 프로필에서
    `_triage_by_candidate`가 target × 멤버로 이 함수를 여러 번 부르고(Option C), 첫 멤버가
    만든 캡슐을 `state.capsules`가 나머지에 물려 준다.

    **`reached.stage`를 노드에 들어가기 직전에 올린다 (PR #50).** 끝난 뒤에 올리면 그
    노드에서 죽은 target이 앞 단계로 기록된다 — 고치려던 것이 정확히 그 어긋남이다.
    (그 올리는 일은 이제 각 `_step_*`이 자기 첫 줄에서 한다.)
    """
    item = _InFlight(target=target, reached=reached, role=role)
    for stage, step in _TRIAGE_STEPS:
        if last < stage:
            return
        if not step(ctx, item, state):
            return


def _new_record(
    spec: RunSpec,
    config: HarnessConfig,
    run_id: str,
    started_at: datetime,
    artifacts: list[Artifact],
    tool_images: dict[str, str],
    model_sampling: dict[str, ModelSampling],
) -> tuple[RunRecord, list[str]]:
    """재현성 기록을 만든다. 빈 필수 항목은 경고로 남긴다.

    evaluation.md §6: 기록이 불완전한 run의 결과는 비교표에 넣지 않는다. 그러니 조용히
    비워 두는 대신, 무엇이 왜 비었는지를 호출자가 볼 수 있게 돌려준다.
    """
    commit, dirty, warnings = _repo_state()
    record = RunRecord(
        run_id=run_id,
        condition=spec.condition,
        # `--profile`이 고른 이름 그대로다. `RunSpec`이 아니라 config에서 오는 것은
        # 그 docstring이 정한 대로다 — "여기 없는 것은 config에서 온다".
        profile=config.profile_name,
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
        # 하네스가 **보낸** 샘플링.
        sampling=config.sampling,
        # 각 실서빙 모델이 `generation_config.json`에 **선언한** 샘플링 (#99). 서버가
        # 모델 파일에서 적용하는 `top_k` 등을 하네스는 안 보내므로 이 필드로만 기록에 남는다.
        # 어댑터가 읽어 factory가 `deps`에 실은 값이다 — 러너는 `src/adapters/`를 import 하지
        # 않으므로(계층 규칙) `tool_images`와 같은 경로로 받는다.
        model_sampling=model_sampling,
        # registry가 선언해 하네스가 **요청에 실은** 모델별 파라미터. `model_sampling`과
        # 달리 어댑터를 안 거친다 — config에서 바로 유도되므로 `deps`를 탈 이유가 없다.
        model_reasoning_effort=config.reasoning_efforts(),
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


def missing_run_labels(
    *, device: str | None, kb_snapshot: str, condition: str | None
) -> list[str]:
    """**사람이 줘야 하는 값**의 결여. run이 시작하기 전에 판정한다.

    `incomplete_fields()`가 끝난 run의 레코드를 보고 device·kb_snapshot에 같은 말을 하지만
    그때는 이미 비용을 썼다. `cli._check_checksums`가 doctor에서 그 논법을 적어 뒀는데
    ("`run`이 끝난 뒤 `incomplete_fields()`가 같은 판정을 하지만, **돌리기 전에 알 수 있는
    사실이다**") **`run` 진입에는 그 검사가 없었고 `run()`은 `doctor`를 부르지 않는다.**

    **레코드가 아니라 사람이 준 값을 받는다.** `condition`을 해석된 값으로는 판정할 수 없어
    서다 — 기본값 `B3prime@pipeline`은 파이프라인 조건의 **정당한 값**이라
    `== DEFAULT_CONDITION`으로 재면 그 라벨을 의도해서 준 run까지 경고한다. 매 run 같은 줄이
    뜨면 사람이 경고를 안 읽게 되고, 그것이 `_check_device`가 "없는 비교를 근거로 경고하면
    헛경고가 된다"고 적은 바로 그 실패다. 여기서는 `None`이 "안 줬다"이고 그것만이 결여다.

    `device`·`kb_snapshot`은 `incomplete_fields()`와 **같은 문장**을 쓴다(`_DEVICE_MISSING`·
    `_KB_PLACEHOLDER`). 셋 다 run 시점의 사실이라 사후에 채우면 CLAUDE.md §0이 금지하는
    지어낸 값이 된다.

    **빈 문자열도 결여다.** `--device ""`는 "줬다"가 아니라 라벨이 없는 것이고, 그것을
    통과시키면 라벨 없는 run이 *정상 run*으로 기록된다. `incomplete_fields()`도 같은 식으로
    좁혀 두 판정이 갈라지지 않게 한다.

    **`kb_snapshot`은 값 비교로 충분하다** — `kb-none`은 구성상 "아직 KB가 없다"는 뜻이라
    의도해서 주는 정당한 값이 될 수 없다(그 상수 주석).

    **이 함수 자체는 경고만 낸다.** 다만 결과를 받은 `cli.run()`이 `served_models()`가 비지
    않은 프로필에서는 `BLOCKING_WHEN_SERVING`에 든 둘을 **exit 2로 거부한다** — dev 프로필의
    스모크 run은 장비 라벨 없이 도는 것이 정상이라 그쪽만 경고로 남는다. 이 함수가 사는 이유는
    값을 강제하는 것이 아니라 **비용을 쓰기 전에 말하는 것**이고, 강제는 호출부의 판단이다.
    """
    warnings = []
    if not (device or "").strip():
        warnings.append(_DEVICE_MISSING)
    if not (kb_snapshot or "").strip() or kb_snapshot == DEFAULT_KB_SNAPSHOT:
        warnings.append(_KB_PLACEHOLDER)
    if condition is None:
        warnings.append(_CONDITION_DEFAULTED)
    return warnings


def incomplete_fields(
    record: RunRecord,
    *,
    served_models: Mapping[str, str] | None = None,
    gpu_unmeasured_roles: Mapping[str, int] | None = None,
) -> list[str]:
    """비교표에 넣을 수 없게 만드는 빈 항목 (engineering-guide.md §5).

    `served_models`는 **이 배선에서 실제로 뜨는 역할**의 역할 → model key다
    (`HarnessConfig.served_models` — Fake 역할과, `retriever: fake`인 프로필의
    embedding·reranker를 뺀다). checksum 자리표시자 판정에만 쓴다. `None`이면 그 검사를
    건너뛴다 — 모르는 채로 "괜찮다"고 말하지 않고, 판정할 근거가 없으면 판정하지 않는다.

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
    if not (record.kb_snapshot or "").strip() or record.kb_snapshot == DEFAULT_KB_SNAPSHOT:
        # 문구는 `missing_run_labels()`와 공유한다 — 같은 결여를 run 전과 후가 다른 이름으로
        # 부르면 그 갈라짐이 곧 다음 결함이다.
        warnings.append(_KB_PLACEHOLDER)
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
    if not (record.device or "").strip():
        # `tool_runner`는 여기 없다 — config에서 유도되어 빌 수가 없기 때문이다. 이 값은
        # 사람이 `--device`를 안 주면 실제로 빈다. 두 장비가 모두 지원 대상인 이상
        # (docs/portability.md) 장비를 모르는 run은 비교표의 어느 칸에도 놓을 수 없다.
        #
        # 문구는 `missing_run_labels()`와 공유한다(`_DEVICE_MISSING`). 그쪽이 run **전에**
        # 같은 말을 하므로, 사람이 이 줄을 run이 끝난 뒤에 처음 보는 일은 없어야 한다.
        warnings.append(_DEVICE_MISSING)
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
