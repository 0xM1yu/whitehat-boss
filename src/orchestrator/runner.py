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
되돌아가고 그 사실이 `target_plan`에 남는다.** `--slices`로 돈 run은 **슬라이스 1개가 대상
하나다**(ADR 0012 결정 1 — `_plan_slice_targets`) — Scout는 그 렌더를(`input: slice` 프롬프트일
때만 — `input: source`면 entry를 품는 함수 본문, #529), Auditor는 슬라이스가
걸친 함수들의 원본을 entry 함수부터 문자 수 상한 안에서 본다(`_step_scout`·`_auditor_source`).

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

그래서 신호는 **순서**로 쓴다: 신호가 붙은 심볼이 큐 상위(히트가 든 심볼 > 신호 artifact의
다른 심볼 > 무신호), 나머지는 뒤, `candidate_max`를 넘긴 나머지는 `target_skipped`로
**기록에 남긴다.**

**신호 판정은 심볼 단위다(#70).** STATIC `Evidence.location`의 줄을 품는 심볼에만
히트 순위(rank 2)가 붙는다 — 큰 파일에서 semgrep이 한 곳을 짚으면 그 심볼만 큐 상위로
간다. 위치를 못 뽑은 신호(실 clang-tidy — `container_tools.first_hit_location`은 semgrep만
읽는다)만 걸린 artifact는 심볼 전부가 rank 1이고, 인덱스 없는 폴백의 파일 단위 target도
그 artifact에 STATIC 신호가 있으면 rank 1이다(없으면 0 — `_symbol_signal_rank`).
**그래서 그 artifact는 다른 파일의 위치 있는 히트(rank 2) 뒤로 간다** — 정렬 키 첫 항이
`-signal_rank`라 파일 순서보다 앞선다(`_order_and_cut`). #249 전에는 신호 artifact의
심볼이 모두 동급이었으므로 그때와 다른 순서이고, `candidate_max`가 물면 그 artifact의
심볼이 다른 파일의 히트보다 먼저 잘린다.
이 동작을 받아들였다(#257 2).
`--slices` run에서는 더 좁다: 슬라이스 target의 `Location`은 `line_start`=entry 줄이고
`line_end`가 없다(`src/adapters/slices.py`). `_symbol_signal_rank`가 `end = line_end or start`로
보므로 rank 2는 히트 줄이 entry 줄과 같을 때뿐이다 -> entry 줄이 아닌 곳에 걸린 semgrep
히트는 clang-tidy만 걸린 것과 같은 rank 1이다(`_plan_slice_targets`의 ponytail, 코드 읽기 +
함수 직접 호출 — `--slices` run으로 순서를 재지는 않았다).
**점수 축은 아직 artifact 단위다**: `fake_model._has_static_signal()`은 심볼별로
거르지 않은 evidence를 보므로 가산점은 artifact 전체에 간다 — 순서만 심볼 단위다.

## 후보 하나의 실패가 run 전체를 죽이지 않는다

`ModelGatewayError`·`SchemaParseError`는 후보 단위 실패다. 예전에는 둘 다 뚫고 나갔고
`run_manifest.json`을 쓰는 블록이 그 뒤라 **산출물이 하나도 안 남았다** — zlib 때
`state.db`를 직접 열어야 했던 이유다.

단, 무조건 잡지 않는다. 후보 하나가 컨텍스트를 넘긴 것(그 후보만 버린다)과 서버가
죽은 것(순회를 멈춘다)은 대응이 다르고, 둘을 가르는 정보가 `ModelGatewayError.status`에
있다 — **400이면 서버가 준 문구까지 본다(#296).** vLLM은 요청 모양·서버 설정 때문에도 400을
주고, 그 400은 다음 요청도 같으므로 첫 건에서 멈춘다. 서버는 응답하는데 후보가 계속 죽는
경우를 위해 **연속 폐기 카운터**가 하나 더 있고, 그쪽은 사유가 갈린다(`_TooManyDrops`).

그 카운터가 세는 것은 **체계적 고장의 신호**뿐이다. 크기 거절(413과 컨텍스트 초과 문구의
400)은 "이 후보가 크다"이지 "이 run이 고장 났다"가 아니라서 세지 않는다 — 세면 큰 함수 셋이
연달아 오는 것만으로 멈추고, 그것이 이 절이 견디겠다고 한 바로 그 경우다. 대신 **내리지도
않으므로** 체계적 실패와 번갈아 나와도 상한에 닿는다. 그리고 **끝까지 시도한 뒤** 한 자리가
한 번도 성공하지 못했으면 원인이 크기여도 멈춘다 — 그 자리는 아무것도 못 봤다.

**멈추는 것과 산출물을 잃는 것은 다르다.** 장애 갈래 넷(교대 실패·게이트웨이 장애·연속 폐기·
스키마 파싱 실패)은 예산 소진과 같은 자리에서 같은 방식으로 끝난다 — 리포트와 매니페스트까지
가고 `RunRecord`에는 `stopped_after`가 남는다(전이 표 R-2). **사유는 `RunRecord`에 없다** — trace의
R-2 `transition` 이벤트(`subject_kind: run`)의 `reason`이 든다. 같은 자리에서 끝나도 **밖에서 보이는
것은 갈린다**: 예산 소진은 종료 코드 0이고 이 넷은 `cli`가 stderr `[ERROR]` 줄과 3으로 돌려준다
(`RunResult.stop_reason` — `src/cli.py` 모듈 docstring). 예상 밖 예외(`UNFORESEEN_FAILURE`)는 이
넷과 또 다르다 — 매니페스트·trace는 남지만 `RunResult`가 안 나오고(리포트 파일 없음) 예외를 다시
던져 `run`은 트레이스백(1)으로 끝난다. 예외가 `write_manifest` 앞을 뚫고 나가면 그때까지의
GPU 시간이 통째로 사라지고, 그것이 이 절이 없애려는 결함이다.
"""

import contextlib
import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Protocol

from src.config import SCOUT_POOL_ROLES, ConfigError, HarnessConfig
from src.ids import IdFactory, sha256_digest, sha256_text
from src.interfaces.model_client import (
    ModelClient,
    ModelGatewayError,
    ModelSwapError,
    SchemaParseError,
    error_detail,
    is_size_rejection,
    truncate_detail,
)
from src.interfaces.oracle import Oracle
from src.interfaces.retrieval import RetrievalResult
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner
from src.orchestrator import gate, oracle_log, source_gate, state_machine
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import TAIL_WHITESPACE_CHARS, RunContext
from src.orchestrator.entry_scope import (
    ENTRY_WINDOW_LINES,
    enclosing_scope,
    narrowest_symbol,
    spans,
)
from src.orchestrator.heartbeat import Heartbeat
from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    pov,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)
from src.orchestrator.nodes.report import Report
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.poc_log import PocLog
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_loader import _HARNESS_HEADER_LINE, load_prompt
from src.orchestrator.prompt_log import PromptLog
from src.orchestrator.source_definitions import (
    definition_names,
    macro_alias_target,
    slice_line_refs,
    slice_names_first,
)
from src.orchestrator.source_guards import scan_guards
from src.orchestrator.stages import MAX_STAGE, Stage
from src.orchestrator.trace import EventKind, TraceValue, TraceWriter
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate, SliceRef
from src.schemas.capsule import ContextCapsule
from src.schemas.common import PLACEHOLDER_DIGEST, Location
from src.schemas.decision import Decision
from src.schemas.enums import (
    ArtifactKind,
    AuditorStopReason,
    CapsuleLevel,
    EvidenceGrade,
    EvidenceKind,
    FindingState,
    NeStopReason,
    PovStopReason,
    ReasonCode,
    Role,
    ScoutSignal,
    Verdict,
)
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.need_evidence import Resolution, ResolveQuery
from src.schemas.run import CONDITION_PATTERN, CompileFlags, ModelSampling, RunRecord
from src.schemas.scout_response import RequestedContext
from src.tokens import estimate_tokens, using_counter

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
    "어느 조건으로 돌린 run인지 산출물이 지시하지 못한다"
)

# **실서빙 run에서 이 둘이 비면 시작 전에 막는다.** 경고 한 줄은 긴 run의 출력에 묻히고,
# 그 run은 시작하기도 전에 못 쓰는 것이 확정돼 있다 — `device`는 `incomplete_fields()`가
# "모델 비교표에서 제외한다"고 **판정하는** 값이고, `condition`은 기본값으로 굳으면 **어느
# 조건으로 돈 run인지 산출물이 지시하지 못한다**. 둘 다 GPU를 태운 뒤에 듣는 것이
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

# 크기 거절의 판정(`_is_size_rejection`)과 그 문구는 `interfaces.model_client`에 있다 - Auditor
# 노드가 같은 판정을 써야 하는데 노드는 러너를 import할 수 없다(#514). **413과 컨텍스트 초과 문구가
# 실린 400만 「이 후보가 크다」다** - 다음 후보는 작을 수 있으므로 이 후보에 대한 사실이고, 아래
# `_MAX_CONSECUTIVE_DROPS`가 세지 않는다. 크기가 아닌 400(#296)은 `_rejects_every_request`가 받는다.
_is_size_rejection = is_size_rejection

# 후보만 버리고 계속 가도 되는 HTTP 상태 — **서버는 살아 있고 요청 하나에 답했다.** 그 거절이
# 이 후보에 대한 사실(크기)인지 run에 대한 사실(크기가 아닌 400)인지는 상태만으로 못 가른다 —
# `_drop`이 가른다. 422는 크기가 아니라 체계적 신호로 센다. vLLM 0.27.1은 요청 모양 거절도 422가
# 아니라 400으로 준다(#296).
#
# 그 밖(상태 없음 = 연결 실패·타임아웃·계약 밖 200, 5xx = 서버 오류, 이 셋 밖의 4xx = 401·404·
# 429 등)은 후보 하나의 답이 아니라 잡지 않는다. 잡으면 남은 target 수백 개를 전부 실패시키며
# 돈다. **예외는 서버가 생성 중이던 타임아웃 하나다**(`ModelGatewayError.generating`, #261) —
# 판정은 `_is_droppable`에 있다.
_DROPPABLE_STATUS = frozenset({400, 413, 422})

# 연속 폐기 상한. 체계적 고장(모델이 스키마를 전혀 못 맞춘다 등)이면
# 남은 target을 전부 태우고도 쓸 수 있는 결과가 안 나온다.
#
# **크기 거절(`_is_size_rejection`)은 여기에 세지 않는다.** 세면 큰 심볼 셋이 연달아 오는
# 것만으로 run이 멈추는데, 그것은 W2가 정확히 견디겠다고 한 경우다("후보 하나가
# 컨텍스트를 넘겼다 → 그 후보만 버린다"). 정렬이 신호 붙은 심볼을 앞으로 모으므로
# 큰 함수가 뭉쳐 오는 것은 드문 일도 아니다.
#
# **번갈아 실패하는 경우는 여전히 걸린다.** 크기 거절은 카운터를 올리지도 않지만
# **내리지도 않는다** — 내리는 것은 죽지 않고 끝난 시도뿐이다. 그래서 스키마 → 400 → 스키마 →
# 400 → 스키마는 1·1·2·2·3으로 상한에 닿는다. 이 값이 잡으려는 것이 그 모양이다.
#
# **크기 거절만 계속 나오는 run은 중간에 멈추지 않는다.** 그래도 폭주하지 않는 것은
# `candidate_max`가 이미 목록을 묶어 두기 때문이다. 대신 **끝까지 시도한 뒤** 한 자리가 한 번도
# 성공하지 못했으면 멈춘다(`_stop_if_a_caller_saw_nothing`, #296) — 서버 창을 작게 띄우면 크기
# 거절이 전량이 되는데(`run_20260925_0006`), 그 run을 완주로 두면 「못 봤다」가 「없다」로
# 읽힌다. 예전에는 여기서 *"한 번 보면 갈린다"*며 받아들였다 — 사람이 본다는 전제였다.
#
# **카운터가 둘이고 상한은 이 값 하나다(#197).** 키·리셋·서는 조건은 `_ConsecutiveDrops`가 정한다.
#
# **잠정치다.** 실대상 run에서 후보 단위 실패가 얼마나 자주 나는지 재 본 적이 없다 —
# 그 측정의 입력이 이 변경 뒤의 첫 실대상 run이다(`thresholds.candidate_max`와 같은 상태).
_MAX_CONSECUTIVE_DROPS = 3


def _is_droppable(exc: ModelGatewayError) -> bool:
    """이 게이트웨이 오류가 **요청 하나에 대한 답**인가 — 서버가 살아 있어 그 시도를 버릴 수 있는가.

    후보 단위(`_triage_by_candidate`)·단계 단위(`_run_stage_for_all`)·Finding 단위
    (`_analyze`의 critic 루프) **셋**이 같은 판정을 쓴다.
    **함수로 뺀 이유가 그것이다** — 두 곳이 각자 `status not in _DROPPABLE_STATUS`를 적으면
    한쪽만 고쳐질 때 같은 오류가 자리에 따라 다르게 처리된다.

    `status`가 없으면(연결 실패·타임아웃·비-JSON·비-UTF-8·모양이 다른 200) 서버 쪽 사건이라
    버릴 수 없다. 5xx와 `_DROPPABLE_STATUS` 밖의 4xx(401·404·429 등)도 같다 — 상태로는 「요청 하나에
    대한 답」인지 가를 수 없다.
    **예외 하나 — 타임아웃인데 서버가 그동안 토큰을 냈다**(`generating is True`, 어댑터가
    `/metrics`로 잰다). 느린 것이지 죽은 것이 아니다(#261). 연속 폐기 상한은 그대로 센다 —
    느린 후보가 셋 연달아면 run이 선다.

    ⚠ **버릴 수 있다고 계속 가는 것은 아니다(#296).** 크기가 아닌 400도 여기서 참이지만
    `_drop`·`_drop_finding`이 첫 건에서 run을 멈춘다. 여기서 거짓으로 돌려 올려 보내면
    `run_pipeline`이 `MODEL_GATEWAY_UNAVAILABLE`로 적는다 — 서버는 살아 있고 요청을 거절했으므로
    그 사유는 틀린다. (401·404·429도 같은 사유로 가고 같은 이유로 부정확하다 — 바꾸지 않았다.)
    """
    if exc.generating is True:
        return True
    return exc.status is not None and exc.status in _DROPPABLE_STATUS


def _rejects_every_request(exc: Exception) -> bool:
    """크기가 아닌 400인가 — 요청 모양·서버 설정 거절이라 **다음 요청도 같다**(#296).

    그래서 세지 않고 첫 건에서 멈춘다 — 세면 상한까지 같은 거절을 더 태울 뿐이다. 이 정지가 생길
    때(#296)는 이유가 하나 더 있었다: 카운터 키가 Scout 자리였고 Scout가 안 올린 대상의 완주가 그
    키를 지워 뒤 단계가 전부 거절해도 상한에 안 닿을 수 있었다. 키가 `_caller(단계, 자리)`가 된
    뒤(#197)로는 Auditor 키를 Auditor를 안 거친 완주가 못 지운다 —
    `test_a_scout_that_promotes_nothing_does_not_clear_the_auditors_key`가 후보 단위에서 문다.
    """
    return (
        isinstance(exc, ModelGatewayError) and exc.status == 400 and not _is_size_rejection(exc)
    )


def _caller(stage: Stage, seat: Role) -> tuple[Stage, Role | None]:
    """**그 단계에서 모델을 부른 자리** — 성공 0 백스톱(#296)과 자리 연속 폐기 카운터(#197)의 키다.

    4단계만 Scout 자리마다 모델·프롬프트가 다르다. 5단계부터는 부르는 역할이 하나라, 자리
    (`item.role` — 팬아웃에서 정해지는 4단계 자리)로 나누면 한 자리의 후보만 창을 넘어도 「그
    자리가 한 번도 성공 못 했다」가 되어 멈춘다 — 그 역할은 다른 자리 후보에서 멀쩡히 돌았는데.
    연속 폐기도 같다 — 5단계 Auditor 장애를 Scout 자리 수만큼의 버킷으로 나눠 세지 않는다.
    """
    return (stage, seat if stage is Stage.SCOUT else None)


@dataclass
class _ConsecutiveDrops:
    """연속 폐기 카운터 둘 — **run 카운터와 자리 카운터**(#197).

    상한은 둘 다 `_MAX_CONSECUTIVE_DROPS`다.

    | 카운터 | 키 | 지우는 것 | 막는 것 |
    | --- | --- | --- | --- |
    | `run` | 없음 | 후보: 죽지 않고 끝난 시도 · 단계: 행의 성공 | 전 자리 장애(대상 둘 이상) |
    | `by_caller` | `_caller(단계, 자리)` | 그 단계를 지난 시도 | 멤버 하나의 전량 실패(#195) |

    **둘인 이유.** 자리 카운터 하나만 두면(#195 ~ #197) 풀 3자리 전역 장애에서 각 자리가
    따로 상한까지 올라가야 해서 3번째가 아니라 7번째 시도에 섰다. run 카운터 하나만
    두면(#195 전) 고장난 멤버의 실패를 형제의 완주가 매번 지워 영영 안 섰다(#195가 고친 맹점).

    **run 카운터는 폐기가 서로 다른 대상 둘 이상에 걸쳐야 선다(PR #584 리뷰 §1).** 아니면 자리 수 ≥
    상한일 때 한 대상의 팬아웃 전멸만으로 멀쩡한 나머지 대상까지 잃는다. 그 전멸은 전 자리 장애와
    둘째 대상이 죽기 전까지 가를 수 없어, 전 자리 장애는 `max(상한, 자리 수 + 1)`번째 시도에 선다
    (자리 3 · 상한 3이면 4번째 — 자리 키만으로는 7번째).

    **자리 카운터의 키가 실패한 단계를 따른다.** 예전 키(`item.role`)는 4단계 자리라 5단계
    Auditor 장애가 Scout 자리 수만큼의 버킷으로 갈렸고, Scout가 안 올린 대상의 완주가 그
    자리 키를 지워 Auditor가 전부 실패해도 상한에 안 닿을 수 있었다. 이제 Auditor 키는
    Auditor를 지난 시도만 지운다.

    단일 `scout`에서 run 카운터는 예전 카운터와 같다(키가 하나였고 완주가 지웠다). 자리
    카운터가 그보다 먼저 닿는 경우는 뒤 단계의 실패 사이에 그 단계를 안 거친 완주가 낀 때뿐이다.
    """

    run: int = 0
    run_targets: set["_TargetKey"] = field(default_factory=set)
    by_caller: dict[tuple[Stage, Role | None], int] = field(default_factory=dict)

    def passed(self, callers: Iterable[tuple[Stage, Role | None]]) -> None:
        """그 단계들을 지났다 — 그 자리의 앞 실패는 체계적 고장이 아니었다."""
        for key in callers:
            self.by_caller[key] = 0

    def completed(self) -> None:
        """시도 하나가 **죽지 않고 끝났다**(끝까지 갔든 승격이 안 돼 멈췄든) — run 고장이 아니다."""
        self.run = 0
        self.run_targets.clear()


def _stop_if_a_caller_saw_nothing(starved: set[tuple[Stage, Role | None]]) -> None:
    """시도한 자리 중 **한 번도 성공하지 못한 것**이 있으면 멈춘다 — 그 자리는 아무것도 못 봤다.

    실패 하나하나는 크기 거절처럼 그 후보에 대한 사실일 수 있다. 그래서 **끝까지 시도한 뒤에**
    본다 — 앞 몇 건으로 판정하면 큰 대상이 정렬 앞에 몰린 run이 정상 창에서도 늘 같은 자리에서
    선다(정렬이 결정론적이라 다시 돌려도 같다). 전부 실패했다면 원인이 무엇이든 — 작게 띄운
    서버 창이 그랬다(`run_20260925_0006`) — 그 run을 완주로 두면 「못 봤다」가 「없다」로
    읽힌다(#296). 상수가 없다: 「한 번도」는 세는 값이 아니다.
    """
    if not starved:
        return
    names = ", ".join(
        f"{int(stage)}단계" + (f" {seat.value}" if seat is not None else "")
        for stage, seat in sorted(starved, key=lambda key: (int(key[0]), key[1] or ""))
    )
    raise _TooManyDrops(f"{names}: 시도한 대상에서 한 번도 성공하지 못했다 — 아무것도 못 봤다")


class _TooManyDrops(RuntimeError):
    """연속 폐기 상한. **서버가 죽은 것과 다른 사건이라 다른 타입이다.**

    게이트웨이가 응답을 주는데도 후보가 계속 실패하는 상태다 — 프롬프트·예산·스키마 쪽을
    봐야 하고, 서버를 살리는 것으로는 낫지 않는다. 같은 예외로 올리면 `RunRecord`의 중단
    사유가 하나로 뭉쳐 그 구별이 사라진다.

    **같은 사유로 두 정지가 더 온다(#296)** — 크기가 아닌 400(다음 요청도 같으므로 첫 건에서,
    `_drop`·`_drop_finding`)과, 한 자리가 한 번도 성공하지 못한 루프(끝에서,
    `_stop_if_a_caller_saw_nothing`). 둘 다 「서버는 살아 있는데 요청이 죽는다」라
    `ReasonCode.CONSECUTIVE_TARGET_FAILURES`의 주석이 가르는 그 칸이다. 사유 이름의 「연속」은
    첫 쓰임에서 왔다.

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
    # 그 run의 clang 계열 도구가 쓴 컴파일 조건 (#74). `tool_images`와 같은 이유로
    # 러너가 만들지 않고 받는다 — 무엇이 실제로 argv에 갔는지는 어댑터만 알고, 러너가
    # 상수를 다시 적으면 그 기록은 사실과 갈라진다.
    #
    # `None`의 뜻은 `RunRecord.compile_flags`가 정한 것과 같다 — **"이 기록은 남기지
    # 않았다"**이지 "컴파일 조건이 없었다"가 아니다. 뒤엣것은 빈 `CompileFlags`다.
    compile_flags: CompileFlags | None
    # file → 그 파일에 정의된 심볼 위치. `None`이면 인덱스가 없다는 뜻이고 러너는 파일
    # 단위로 되돌아간다. `[]`("봤는데 심볼 0건")와 섞지 않는다 — `index_graph`가 캡슐의
    # 그래프 섹션에 대해 세운 것과 같은 구별이다.
    symbols_for: Callable[[str], list[Location] | None]
    # 아래 셋(`callees_of`·`call_sites_of`·`definitions_of`)은 `demote_nonproduct` 키워드를 받는다
    # (기본 꺼짐 — `auditor_definitions`를 켠 run에서만 러너가 켠다).
    #
    # 함수 위치 → 그 본문이 부르는 함수의 정의 위치(#349). `symbols_for`와 같은 경계다.
    callees_of: Callable[..., list[Location] | None]
    # 함수 위치 → 그 함수를 부르는 호출 지점(#349 (b)). 같은 경계다.
    call_sites_of: Callable[..., list[Location] | None]
    # (이름들, 가까운 파일) → 함수 정의 위치 — 매크로 별칭이 부르는 함수(C4). 같은 경계다.
    functions_named: Callable[..., list[Location] | None]
    # 함수 위치 → 필드 경유 호출마다 (필드, 갈 수 있는 함수)(index-v3, C6). `demote_nonproduct`를
    # 받는다. 같은 경계다.
    indirect_calls_of: Callable[..., list[tuple[str, list[Location]]] | None]
    # (이름|필드, 가까운 파일) → 쓰는 지점(index-v3, C5(a)). 같은 경계다.
    writes_of: Callable[[str, str], list[Location] | None]
    field_writes_of: Callable[[str, str], list[Location] | None]
    # (이름들, 가까운 파일) → 매크로·타입·파일 변수·enum 상수의 정의 위치(#349 (f)). 같은 경계다.
    definitions_of: Callable[..., list[Location] | None]
    # (파일, 함수 이름) → 그 함수의 지역·파라미터 이름. 정의 조회가 본문 이름에서 뺀다. 같은 경계다.
    locals_of: Callable[[str, str], frozenset[str] | None]
    # `--slices`의 슬라이스(ADR 0012 결정 1). `None`이면 위 `symbols_for`로 타깃을 만든다.
    slice_refs: tuple[SliceRef, ...] | None
    # `RunRecord.slicer`가 될 슬라이서 요약. 러너가 만들지 않고 받는다(`tool_images`와 같은 이유).
    slicer: dict[str, str | int | None] | None
    # `RunRecord.serving_launch_intended`가 될 장비 파일의 의도한 기동 인자(#99). 교대 프로필 +
    # 장비가 아니면 `None`. 러너는 장비 파일을 읽지 않고 받는다 - 이 값은 의도지 실제 argv의 증거가
    # 아니다.
    serving_launch_intended: dict[str, list[str]] | None
    # 슬라이스 id → 렌더(gutter·배너). `input: slice` 프롬프트의 슬라이스 타깃 Scout `SOURCE`다
    # (`input: source`면 함수 본문 — #529). 어댑터가 run 전에 전부
    # 렌더해 둔 것을 콜러블로 받는다 — `read_source`와 같이 러너는 렌더러를 import 하지 않는다.
    read_slice: Callable[[str], str]
    # 역할 → 그 역할 모델의 실 토크나이저 카운터(#96 (b)). `None`이면 기본 카운터
    # (글자수 근사)를 유지한다. `symbols_for`와 같은 경로다 — factory가 채우고 러너는
    # `ctx`로 받아 역할 캡슐 조립 직전에 `using_counter(...)`로 켠다. 러너가
    # `src/adapters/`를 import 하지 않으려고 콜러블로 받는다.
    counter_for_role: Callable[[str], Callable[[str], int] | None]
    # 위 카운터가 무엇으로 정해졌는지 (#96 (b)). `token_counter` 이벤트의 `data`가 된다.
    # `IndexSummaryLike`와 같은 이유로 **스칼라 dict**다 — 러너가 어댑터 타입을 모른다.
    counter_report: dict[str, str | None]
    # need_evidence resolver hook (ADR 0012 결정 2). `None`이면 그 run에 resolver가 없다
    # (인덱스가 v1이거나 미로드) — `symbols_for`·`counter_for_role`과 같은 자리다. 러너는
    # `src/adapters/`를 import 하지 않으므로 resolver 구현이 아니라 이 콜러블로 경계를
    # 넘는다. 부르는 쪽은 Scout 정착 단계의 need_evidence 루프(`_run_ne_hops`)다.
    resolve_requests: Callable[[ResolveQuery], Resolution] | None
    # 단계 경계 모델 교대 (ADR 0012 Phase 6.5 — 콜드 재기동). `keys`만 떠 있게 하고 교대 명령의
    # 보고를 스칼라 dict로 돌려준다(`model_swap` 이벤트의 `report.*`). `keys`가 비면 전부 내린다.
    # 실패는 `ModelSwapError`(`ModelGatewayError` 하위형)다 — **러너는 단계 진입 교대에서 이 계약을
    # 믿는다.** 그 밖의 예외는 최후 방어선이 **사유 `unforeseen_failure`로** 받는다 — 매니페스트는
    # 남지만 「교대가 실패했다」가 기록에서 사라진다(⏵ 2026-10-02 정정: 전 판은 *"매니페스트 없이
    # `run_pipeline` 밖으로 샌다"*라 적었다). 계약은 구현이 지킨다 — 실 구현
    # `src/adapters/model_swap.py`가 예상 밖 `Exception`을 `ModelSwapError`로 감싼다. run 끝
    # 내림만은 `finally`라 무엇이든 경고로 줄인다(`run_pipeline`). `None`이면 교대가 없는 run이다 —
    # 교대 선언이 없거나, 장비가 없어 조립하지 않았다(모델에 안 닿는 run). 교대 명령 실행은
    # 어댑터가 하고 러너는 이 콜러블만 받는다(`resolve_requests`와 같은 자리).
    swap_to: Callable[[tuple[str, ...]], dict[str, TraceValue]] | None
    # 채점기 (#386). `None`이면 이 run은 PoC를 제출하지 않는다 — `swap_to`와 같은 경계다.
    oracle: Oracle | None


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
    # 슬라이서 산출물(`slices.json`). 주면 슬라이스 1개가 타깃 1개다(ADR 0012 결정 1) — 하네스
    # run 밖에서 미리 돈 것을 파일로 받는다. `None`이면 파일·심볼 타깃(지금 경로).
    slices_path: Path | None = None
    # CyberGym 과제 설명(description.txt). 주면 캡슐에 `vuln_description` 섹션으로 실린다
    # (PoV 배선). config가 아니라 과제에서 오므로 여기 있다 — `slices_path`와 같은 성격이다.
    vuln_description: str | None = None
    # 성공한 호출의 최종 응답 원문도 남길 역할(#612) — `RunContext.keep_responses`. 기본은 꺼짐.
    keep_responses: tuple[str, ...] = ()
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
    # 9단계 전에 멈춘 사유 — `finish_run`에 넘긴 값 그대로다(`None` = 끝까지 갔다). `RunRecord`는
    # 사유를 안 들고(trace에만 있다) `cli.run`이 종료 코드를 정하려면 여기서 받아야 한다.
    # **기본값이 없다** — `None`이 「끝까지 갔다」로 읽혀서, 새 생성처가 빠뜨리면 조용한 0이 된다.
    stop_reason: ReasonCode | None


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
    # 이 심볼의 정적 신호 순위: 2=이 심볼에 히트 위치, 1=artifact에 신호는 있으나 이 심볼
    # 미확정(results[0] 한 줄·위치 없는 신호·폴백), 0=신호 없음(#70). 큐 순서에만 쓴다
    # (등급 아님). `_symbol_signal_rank`가 매긴다.
    signal_rank: int
    # 슬라이스 타깃이면 그 슬라이스(ADR 0012 결정 1). id만이 아니라 통째로 드는 것은 4·5단계가
    # 더 꺼내 쓰기 때문이다 — Scout는 렌더 id와 `given_end`·entry(`slice_criterion`, ⏵ 2026-10-13
    # #529 `input: slice` 판만 — `source` 판은 entry를 품는 함수 본문을 받는다)를, Auditor는
    # entry 줄(감싸는 함수를 찾는 데)과 슬라이스 줄(걸친 함수를 찾는 데 — `_auditor_source`)을 본다.
    slice: SliceRef | None = None

    @property
    def slice_id(self) -> str | None:
        """entry가 같은 두 슬라이스를 가르는 값 — 타깃 키(`_target_key`)와 `Candidate.slice_id`
        도장이 이것을 쓴다."""
        return self.slice.slice_id if self.slice is not None else None


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
    # `--slices`로 세운 타깃 수와, entry 파일이 이 run의 분석 대상 artifact가 아니라 못 세운 수.
    slice_targets: int = 0
    unmatched_slices: int = 0
    collected: list[_Target] = field(default_factory=list)
    targets: list[_Target] = field(default_factory=list)


# `_target_key`가 만드는 키 — (file, symbol, line_start, line_end, slice_id). 한 이름으로 두는 것은
# 칸이 늘 때 아래 캐시·집합의 타입 표기가 같이 따라오게 하려는 것이다 — slice id가 붙은 뒤에도
# 그 셋이 네 칸짜리 튜플로 적혀 있었다.
_TargetKey = tuple[str, str | None, int | None, int | None, str | None]


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
    # 조금씩 다른 캡슐을 볼 수 있다(토크나이저별 예산 컷). 키는 `_target_key` — target
    # Location에 slice id를 더한 것이다(entry가 같은 두 슬라이스가 한 캡슐을 나눠 갖지 않게).
    capsules: dict[_TargetKey, ContextCapsule] = field(default_factory=dict)
    # 그 캡슐을 **어느 역할 카운터로 계산했나**(공유 캡슐 토큰 회계 근거, #192 조건②). 첫 멤버가
    # 자기 토크나이저로 `tokens`를 정하고 2·3번 멤버는 그것을 공유하므로, 그 사실을 캐시-적중
    # 이벤트에 실어 「멤버 2·3의 프롬프트를 무엇으로 셌나」가 기록에서 안 지워지게 한다.
    capsule_counter: dict[_TargetKey, str] = field(default_factory=dict)
    # 팬아웃된(deferred 아닌) 대상 키 집합 (#193 ①). 「모든 자리가 실패한 대상 수」 =
    # 이 집합에서 후보를 낸 대상을 뺀 것. 단일 `scout`에서도 성립한다(자리가 하나).
    dispatched_targets: set[_TargetKey] = field(default_factory=set)
    # target별 각 Scout 자리의 hop0 신호 (ADR 0012 결정 2 — 동료 확인). `_step_scout_open`이
    # 적고 `_step_scout_settle`이 읽는다 — 같은 target의 다른 좌석이 review면 이 좌석의 NE
    # 루프는 안 돈다. hop0에서 실패해 버려진 좌석은 여기 안 남는다("신호 없음"으로 친다).
    hop0_signals: dict[_TargetKey, dict[Role, ScoutSignal | None]] = field(default_factory=dict)
    # hop≥1에서 흡수한 예외 건수, `NeStopReason` 값별(ADR 0012 결정 2). 조용히 두지
    # 않는다 — `deferred`·`dropped`와 같은 자리로 리포트에 싣는다(`_analyze` 끝).
    ne_absorbed: dict[str, int] = field(default_factory=dict)
    # 7단계 Critic 판정. **8단계 게이트 재판정의 입력이다** — 없으면 `p3`(uphold)이 거짓이
    # 되어 asan이 실패한 run에서 등급이 7단계보다 낮게 기록된다.
    decisions: dict[str, tuple[Decision, ...]] = field(default_factory=dict)
    workdirs: dict[str, str] = field(default_factory=dict)
    # 8단계 PoV가 쓸 캡슐 — finding_id로 연다(PoV 배선 — b-1). Auditor가 Finding을 만든
    # capsule을 그대로 물려 준다(러너가 캡슐을 넘기고 pov 노드 시그니처는 안 바뀐다). 미스면
    # `_run_pov`가 pov를 건너뛰고 기록만 한다 — 추가형이라 상태는 validate.run이 이미 정했다.
    finding_capsules: dict[str, ContextCapsule] = field(default_factory=dict)
    # 8단계 PoV가 쓸 `SOURCE` — **Auditor가 본 것과 같은 문자열**이다(사용자 지적 2026-10-02).
    # `_Once`라 PoV가 꺼내 써도 `_auditor_source`가 다시 돌지 않는다(= `auditor_source` 이벤트도
    # 한 번뿐이고 인덱스 조회도 한 번뿐이다). 비슬라이스도 Auditor의 최초 읽기를 캐시한다.
    # Critic·PoV의 재읽기로 파일 변경이나 새 읽기 실패를 끌어들이지 않는다.
    finding_sources: dict[str, "_Once"] = field(default_factory=dict)
    # 7단계 Critic이 받을 **Auditor 도구 루프의 홉 관측** — finding_id → 홉 관측의
    # Critic용 사본(Auditor가 홉마다 본 관측에서 모델이 고른 식별자를 가린 것 - 가림에 실패한 홉은
    # 빠진다. 모델 응답 턴은 없다).
    # `auditor.run(observations=...)`가 채우고, 홉 0(한 방 판·도구 루프 꺼짐)이면 빈 튜플이다.
    # `finding_sources`와 같은 자리에 두는 이유가 같다 — Finding을 만든 5단계가 본 것을 7단계가
    # 물려받는다(#600 H2ⓐ). 8단계 PoV는 안 읽는다.
    finding_observations: dict[str, tuple[str, ...]] = field(default_factory=dict)
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
    # 그 Finding들의 id. **수가 아니라 누구인가**를 8단계가 읽는다 — 판정을 못 받고 버려진 Finding은
    # 상태가 안 바뀌어(`HYPOTHESIZED` 그대로) 상태로는 못 가르고, `_pov_targets`가 추가 대상 풀에서
    # 뺀다. `critic_failed`와 **한 자리에서만** 갱신된다(`_drop_finding`).
    critic_failed_ids: set[str] = field(default_factory=set)
    # 8단계 PoV가 어느 사유로 섰나 — finding_id → 사유 문자열 (이슈 #357·#358).
    # 어휘는 **10사유 완전분할**이다: `PovStopReason` 여섯 값 + enum 밖 **넷**.
    #   `_POV_CAPSULE_MISSING`      캡슐 미스 — 루프에 못 들어가 enum 값을 못 받는다
    #   `_POV_SUBMITTED_WITHOUT_POC` submit했는데 PoC가 `None`이다(gen 없는 submit)
    #   `_POV_SUBMITTED_UNPERSISTED` submit했고 PoC도 있는데 사이드카 쓰기가 실패했다
    #   `_POV_ABORTED`               흡수되지 않은 예외로 루프가 끊겼다(`_run_pov`의 `finally`)
    # ⚠ **`b""`(0바이트)는 PoC 「있음」이다** — `submitted_without_poc`가 아니라 사이드카가
    # 써졌으면 `submitted`이고 안 써졌으면 `submitted_unpersisted`다(`_pov_result`).
    # 🔴 `submitted*` 둘은 **`submitted`를 쪼갠 것**이다 — 안 쪼개면 그 둘이 「최종 PoC 있음」으로
    # 섞여 장부가 거짓이 된다. 넷 다 실제로 그렇게 샜고 적대적 리뷰가 각각 재현했다.
    #
    # **시도 수와 사유별 수를 따로 들지 않는다.** 시도 수는 `len(...)`, 사유별 수는
    # `Counter(...values())`다.
    # 🔴 **「시도 수 == 사유별 합」을 보장이라고 쓰지 마라 — 그것은 모든 dict의 항진명제다**
    # (`len(d) == sum(Counter(d.values()).values())`). 적대적 리뷰가 그 과장을 짚었다. 한 필드가
    # 사는 이유는 그 등식이 아니라 **갱신 자리가 하나**라는 것이다 — 두 필드로 두면 한 출구가
    # 둘 중 하나만 올려 어긋날 자리가 생긴다.
    # **실제로 재는 성질은 「출구마다 항목이 정확히 하나」**이고 그것을 지키는 것은 등식이 아니라
    # `_POV_EXITS` 표 테스트와 `_run_pov`의 `finally`다.
    #
    # ⚠ 그래도 안 세는 것: 같은 `finding_id`로 `_run_pov`를 두 번 부르면 뒤가 앞을 덮어
    # 「시도 2 · 항목 1」이 된다. 지금 `_analyze`는 `findings` dict(키=finding_id)를 돌아 한 run에
    # 같은 id가 두 번 안 들어가지만, **구조가 막는 것이 아니다**(적대적 리뷰가 직접 불러 보였다).
    #
    # 위 `ne_absorbed`와 같은 자리다 — 러너가 세고 경고·리포트가 그 값을 그대로 받는다.
    pov_results: dict[str, str] = field(default_factory=dict)
    # 좌석은 있는데 8단계 대상이 0건일 때의 사유 한 문장(`_pov_pool_empty_reason`), 아니면 `None`.
    pov_pool_empty: str | None = None
    # 채점기(oracle)의 모든 vul 시도와 최종 지정 단계 (#386). 채점기를 안 켠 run에서는 비어 있고
    # `wired`가 거짓이라 요약·경고·파일이 없다. 예산 소진으로 중간에 끊겨도 호출자가 읽어 경고와
    # 매니페스트에 올려야 하므로 지역 변수가 아니라 여기 둔다(이 클래스의 존재 이유).
    oracle_ledger: oracle_log.OracleLedger = field(default_factory=oracle_log.OracleLedger)

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
    read_payload: Callable[[str], str | None] = lambda _ref: None,
    read_raw: Callable[[str], bytes | None] = lambda _file: None,
    trace: TraceWriter,
    run_dir: Path | None = None,
    cache_hook: LateCacheHook | None = None,
    truncate_hook: LateTruncateHook | None = None,
    usage_hook: LateUsageHook | None = None,
    index_hook: LateIndexHook | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """단계 0~9를 순서대로 돌리고 `RunRecord`를 닫는다."""
    # **교대를 선언했는데 교대 콜러블이 없으면 모델에 닿는 run은 시작하지 않는다**(ADR 0012
    # Phase 6.5). 서버가 안 뜬 채 4단계에 들어가면 게이트웨이 오류로 멈추고 그 사유는 틀린다.
    # CLI(`cli.run`)가 같은 것을 먼저 막는다 — 이것은 CLI 밖의 호출자 가드다. 기준은 CLI와 같다:
    # Scout에 안 닿는 run(`--dry-run`·낮은 `--stop-after`)은 교대도 안 하므로 막지 않는다.
    if config.swap is not None and deps.swap_to is None and spec.stop_at()[0] >= Stage.SCOUT:
        raise ConfigError(
            f"프로필 {config.profile_name!r}은 교대를 선언했는데 교대 콜러블이 없다 — 장비 "
            "(`--device`) 없이 조립한 deps로는 모델에 닿는 run을 시작하지 않는다"
        )
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
        read_slice=deps.read_slice,
        read_payload=read_payload,
        read_raw=read_raw,
        symbols_for=deps.symbols_for,
        callees_of=deps.callees_of,
        call_sites_of=deps.call_sites_of,
        functions_named=deps.functions_named,
        indirect_calls_of=deps.indirect_calls_of,
        writes_of=deps.writes_of,
        field_writes_of=deps.field_writes_of,
        definitions_of=deps.definitions_of,
        locals_of=deps.locals_of,
        counter_for_role=deps.counter_for_role,
        # **`write_manifest`와 같은 가드다** — 산출물 디렉터리가 있고 실제로 도는 run일
        # 때만 프롬프트를 남긴다(해소). 조건을 따로 만들면 매니페스트는 있는데 프롬프트는
        # 없는 run이 생기고, 그 차이의 이유가 어디에도 안 남는다.
        prompts=PromptLog(run_dir) if run_dir is not None and not spec.dry_run else None,
        keep_responses=frozenset(spec.keep_responses),
        # PoC 사이드카(#355) — **위 `prompts`와 같은 가드다.** 조건을 따로 만들면 프롬프트는
        # 있는데 PoC는 없는 run이 생기고 그 차이의 이유가 어디에도 안 남는다.
        pocs=PocLog(run_dir) if run_dir is not None and not spec.dry_run else None,
        # need_evidence resolver hook (ADR 0012 결정 2) — `symbols_for`·`counter_for_role`과
        # 같은 경계다. `None`이면 이 run에 resolver가 없다.
        resolve_requests=deps.resolve_requests,
        # 단계 경계 교대(ADR 0012 Phase 6.5) — `resolve_requests`와 같은 경계다.
        swap_to=deps.swap_to,
        # 채점기(#386) — `swap_to`와 같은 경계다. `None`이면 제출하지 않는다.
        oracle=deps.oracle,
        # CyberGym 과제 설명(PoV 배선). `retrieval.run`이 캡슐에 실어 Scout·Auditor·pov가 본다.
        vuln_description=spec.vuln_description,
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
        spec,
        config,
        ctx.run_id,
        started_at,
        artifacts,
        deps.tool_images,
        deps.model_sampling,
        deps.compile_flags,
        slicer=deps.slicer,
        serving_launch_intended=deps.serving_launch_intended,
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
    state = _Accumulator(
        oracle_ledger=oracle_log.OracleLedger(
            wired=deps.oracle is not None,
            # 사이드카는 `prompts`·`pocs`와 같은 가드다 — 산출물 디렉터리가 있고 실제로 도는 run일
            # 때만.
            log=oracle_log.OracleLog(run_dir) if run_dir is not None and not spec.dry_run else None,
        )
    )
    stopped_after = None if stop_reason is None else last
    # 최후 방어선이 잡아 둔 예상 밖 예외 — 매니페스트를 쓴 **뒤** 다시 던진다(`finally` 바로 뒤).
    unforeseen: Exception | None = None
    # heartbeat: 러너가 살아 있는지를 밖에서 읽게 한다(이슈 #208 ②). `PromptLog`와 같은
    # 가드다 — 산출물 디렉터리가 있고 실제로 도는 run일 때만 켠다. 모델 호출이 멈춰도 이
    # 스레드는 틱해 "죽음↔느림"을 가른다.
    heartbeat = None
    if run_dir is not None and not spec.dry_run:
        heartbeat = Heartbeat(
            run_dir / "heartbeat.json",
            seq_of=lambda: len(trace.events),
            stage_of=lambda: int(ctx.current_stage),
            clock=now,
        ).start()
    try:
        _analyze(ctx, artifacts, state, last, slice_refs=deps.slice_refs)
    except BudgetExhausted as exc:
        # 예산 소진이 사용자 지정 상한보다 먼저 왔다. 사유를 덮어쓴다 — 실제로 실행을
        # 끊은 것이 이쪽이다.
        stopped_after, stop_reason = exc.stage, ReasonCode.BUDGET_EXHAUSTED
        warnings.append(f"{exc.stage}단계에서 예산 소진: {exc.item} {exc.spent}/{exc.limit}")
        # `_analyze`의 PoV 루프가 최종 지정 실패를 `add_note`로 붙여 올린다 — 이 갈래가 예외를
        # 삼키므로 여기서 안 읽으면 그 사유는 산출물 어디에도 안 남는다(#532).
        warnings.extend(getattr(exc, "__notes__", ()))
    except ModelSwapError as exc:
        # **아래 게이트웨이 갈래보다 먼저 잡는다** — `ModelSwapError`는 그 하위형이다. 떠 있던
        # 서버가 죽은 것이 아니라 다음 단계 서버를 띄우지 못한 것이라 대응이 다르다(장비 파일과
        # 교대 명령의 로그를 본다). 교대(`_swap`)에서 난 실패면 `model_swap ok=false`가 이미
        # 남았다 — 교대 밖에서 올라온 것(방어선 경로)에는 그 이벤트가 없다.
        stopped_after = int(ctx.current_stage)
        stop_reason = ReasonCode.MODEL_SWAP_FAILED
        warnings.append(f"모델 교대가 실패해 중단했다: {error_detail(exc)}")
    except ModelGatewayError as exc:
        # 서버 쪽 사건이라 남은 target을 태워도 얻는 것이 없다. **그래도 여기까지 만든
        # 것은 남긴다** — 예산 소진과 같은 자리에서 같은 방식으로 끝낸다(전이 표 R-2).
        # 이 갈래가 없던 동안에는 예외가 `write_manifest` 앞을 뚫고 나가 산출물이 하나도
        # 안 남았고, zlib 때 `state.db`를 직접 열어야 했던 이유가 그것이다.
        stopped_after = int(ctx.current_stage)
        stop_reason = ReasonCode.MODEL_GATEWAY_UNAVAILABLE
        # 상태 없음(끊김·타임아웃·계약 밖 200)·5xx·`_DROPPABLE_STATUS` 밖 4xx가 모두 여기로 온다 —
        # 경고는 원인을 단언하지 않는다(「응답하지 않아」는 계약 밖 200에 거짓이다) — 원인은
        # detail이다.
        warnings.append(f"게이트웨이 실패로 중단했다: {error_detail(exc)}")
        warnings.extend(getattr(exc, "__notes__", ()))  # 위 예산 갈래와 같은 이유(#532)
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
    except Exception as exc:
        # 🔴 **최후 방어선 (S0) — 범위는 `_analyze`다.** 위 다섯은 **예상한** 형이고, 이 갈래는
        # **`_analyze`에서 난 예상 밖 형 전부**다. 그런 예외가 하나라도 나면 `write_manifest`(아래)
        # 앞을 뚫고 나가 **산출물이 통째로 사라졌다** — 위 `ModelGatewayError` 갈래의 주석이 적어 둔
        # zlib 사고가 정확히 그것이고, 그 갈래는 **그 형에만** 방어선을 세웠다.
        # ⚠ **`_analyze`가 정상으로 끝난 뒤 후처리(GPU 경고·게이트 재등급·`report.run`)에서
        # 처음 나는 예외는 안 덮는다** — 이 `try`가 감싸는 것은 `_analyze` 하나다. 범위를 넓히는
        # 것은 별 작업이다(아래 `finally`(스왑 내림)와 섞이면 원래 예외가 덮인다).
        #
        # 🔴 **삼키지 않는다 — 적고, 매니페스트를 쓰고, 다시 던진다.**
        # 삼키면 `tests/test_stage_swap.py`의 예상 밖 예외 검사 셋이 `DID NOT RAISE`가 된다 —
        # 그쪽은 **예상 밖 예외가 올라오는 것**을 계약으로 못 박았다. (#533 이전에는 호출자
        # (`cli.execute`)가 run을 성공으로 읽는 것도 깨졌다 — 지금은 `cli.run`이 허용목록 밖 사유를
        # 3으로 돌려줘 그 갈래는 막혔다.) 최후 방어선의 뜻은 "삼킨다"가 아니라 "**증거를 남기고**
        # 올려 보낸다"다. 쓰고 던지는 자리는 `finally` 바로 뒤다.
        stopped_after = int(ctx.current_stage)
        stop_reason = ReasonCode.UNFORESEEN_FAILURE
        unforeseen = exc
    finally:
        # **run 끝 내림 — 성공·실패 모두**(ADR 0012 Phase 6.5). 멈춘 run이 서버를 붙들고 남으면
        # 다음 run이 못 뜬다. 교대가 실패한 run도 내린다 — 명령이 일부만 띄우고 죽었을 수 있다.
        # 교대 명령은 자기 run의 서버만 내리고 떠 있는 것이 없으면 아무것도 안 한다.
        #
        # **여기서는 `ModelSwapError`만 잡지 않는다.** `finally`에서 새는 예외는 run의 결과(아래
        # 매니페스트)와 원래 예외를 통째로 덮는다. 그래서 교대 콜러블이 계약 밖 예외를 내도,
        # `_swap`이 `ok=false`를 기록하다 실패해도 경고 하나로 줄인다. `ModelSwapError`면 `_swap`이
        # `ok=false`를 trace에 먼저 남겼고, 그 밖의 예외면 이 경고가 유일한 흔적이다.
        # `BaseException`(인터럽트)은 잡지 않는다 — 그때는 원래 예외도 결과도 덮인다.
        try:
            try:
                _swap(ctx, (), stage=None)
            except Exception as exc:
                warnings.append(
                    "run 끝 모델 내림이 예외로 끝났다 — 서버가 남았을 수 있다: "
                    f"{type(exc).__name__}: {exc}"
                )
        finally:
            # 어느 갈래로 끝나든 스레드를 세우고 종료 상태를 마지막으로 한 번 남긴다 — **내림
            # 뒤에**. 내림이 도는 동안 heartbeat가 서 있으면 외부 감시자(`tools/monitor.py`)가
            # 살아 있는 run을 `DEAD`로 끝낸다. 내림이 오래 멈추면 감시자는 그것을 stuck(살아
            # 있는데 멈춤)으로 본다 — 죽음과 갈린다.
            if heartbeat is not None:
                heartbeat.stop()

    # 채점기를 켠 run의 제출 요약을 매니페스트에 싣는다(#386) — 아래 두 `finish_run` 호출(예상 밖
    # 예외 경로와 정상 경로)이 같은 `record`를 쓰므로 여기서 한 번 바꾼다. 안 켠 run은 `None`이라
    # 기록이 그대로다.
    oracle_summary = state.oracle_ledger.summary()
    if oracle_summary is not None:
        # 어느 카나리 보고서에 기댔는지(#487) - 안 기댄 run은 키를 새로 만들지 않는다.
        record = record.transition(oracle={**oracle_summary, **(deps.canary_ref or {})})

    if unforeseen is not None:
        # 🔴 **후처리를 건너뛴다 — 매니페스트만 쓰고 원 예외를 던진다**(적대적 리뷰 2026-10-02
        # HIGH 1).
        # 후처리의 `gate.evaluate` 재등급은 `_analyze`의 6단계 게이트와 **같은 순수 함수**라 그
        # 결함이 거기서 **또 난다** — 2차 예외가 `write_manifest` 앞을 뚫고 원 스택을 덮었다. 렌더는
        # 던지는 순간 쓸 곳(`cli.run`)에 못 가 어차피 안 쓰이고, `report.run`이 남기는 9단계 진입은
        # 거기 간 적 없는 run을 간 것으로 읽히게 한다.
        # 매니페스트마저 못 쓰면(디스크·권한) 그 예외는 `add_note`로 **원 예외에 붙이고** 원 예외를
        # 던진다. ⚠ 아래 정상 경로의 `finish_run`·`write_manifest`와 같은 호출이다 — 인자를 바꾸면
        # 두 곳을 같이 고친다.
        #
        # ⚠ **`report.md`는 안 남는다.** 리포트를 파일로 쓰는 것은 `cli.run`이 `execute` **반환
        # 뒤**에 한다 — 여기서 던지면 그 자리에 도달하지 않는다. 프로세스는 exit 1이고 남는 것은
        # 매니페스트·trace·`state.db`다(적대적 리뷰 2026-10-02 실측). 리포트까지 남기려면 쓰기를
        # `run_pipeline` 안으로 옮기는 별 작업이 필요하다.
        try:
            crashed = state_machine.finish_run(
                record,
                finished_at=max(now(), started_at),
                stopped_after=stopped_after,
                reason=stop_reason,
                store=ctx.store,
                trace=trace,
            )
            if run_dir is not None and not spec.dry_run:
                write_manifest(run_dir, crashed)
        except Exception as exc:
            unforeseen.add_note(f"매니페스트를 남기지 못했다: {type(exc).__name__}: {exc}")
        raise unforeseen

    # **GPU 회계 판정은 여기서 한다.** `_new_record`는 파이프라인 시작 전에 불리므로 그
    # 시점에는 모델 호출이 하나도 없다. 실서빙 역할과 교집합해서 Fake 역할이 섞이지 않게
    # 한다 — scout만 실서빙인 프로필(옛 `single_gpu_12g`)에서 Fake 네 역할이 걸리면
    # `dev_*` 프로필이 매 run 같은 줄을 뱉고 진짜 문제가 묻힌다(checksum 검사와 같은 논법).
    served = config.served_models()
    gpu_warning = unmeasured_gpu_warning(
        {role: count for role, count in ctx.gpu_unmeasured_roles.items() if role in served}
    )
    if gpu_warning:
        warnings.append(gpu_warning)
    if ctx.swap_unreported:
        warnings.append(
            f"교대 명령이 올린 서버의 기동 인자를 보고하지 않았다: {sorted(ctx.swap_unreported)}. "
            "기록(`model_swap`의 `report.server.<key>.*`)이 이 run의 기동 조건을 지시하지 못한다"
        )

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
    # **8단계 PoV의 「조용한 0」도 같은 자리에 올린다 (이슈 #357·#358).** 위 경고들의 논거가
    # 그대로인데 여기서는 한 겹 더 나쁘다 — pov는 추가형이라 상태를 안 바꾸므로 Finding이
    # 멀쩡히 찍힌 채 **최종 PoC만 0이 되고**, 그 사유가 trace에만 있으면 run 결과를 읽는 쪽에서
    # "PoV를 돌렸다"와 "PoV가 전부 섰다"가 같은 얼굴을 한다.
    pov_shortfall = _pov_shortfall_warning(state.pov_results)
    if pov_shortfall is not None:
        warnings.append(pov_shortfall)
    if state.pov_pool_empty is not None:
        warnings.append(state.pov_pool_empty)
    # **5단계 도구 루프의 「probe 0」도 같은 자리에 올린다 (#617).** 루프가 돌았는데 도구가 한 번도
    # 안 돌았다는 사실은 홉 수와 함께 trace에만 있다.
    probe_warning = _auditor_zero_probe_warning(trace)
    if probe_warning is not None:
        warnings.append(probe_warning)
    # **공백 폭주 꼬리도 같은 자리다 (#79).** 이 키가 `true`인 것은 닫힌 #79의 재개 조건이라
    # trace를 열지 않고도 run 결과에서 보여야 한다.
    tail_warning = _whitespace_tail_warning(trace)
    if tail_warning is not None:
        warnings.append(tail_warning)
    # **빈 `answer`로 거절된 Auditor 호출도 같은 자리에 올린다 (#602).** 후보가 C-6으로 버려지는데
    # 사유(`error_detail`)와 원문은 호출 단위 이벤트에만 있어 빈도를 아무도 세지 않았다.
    empty_answer_warning = _auditor_empty_answer_warning(trace)
    if empty_answer_warning is not None:
        warnings.append(empty_answer_warning)
    # **Critic에 실은 홉 관측이 이 좌석의 입력 몫을 넘을 수 있다는 사실도 같은 자리에 올린다 (#600
    # H2ⓐ).** 하네스는 그 절을 자르지 않으므로(저자 결정 — 자동 절단 없음) 경고가 없으면 크기 거절
    # (`critic_failed`)이 나기 전까지 trace에만 있다.
    hop_obs_warning = _critic_hop_observations_warning(trace)
    if hop_obs_warning is not None:
        warnings.append(hop_obs_warning)
    # **그리고 크기 거절로 버린 후보도 (#514).** 러너가 그 후보를 `_drop`으로 받아 run은 가므로
    # 조용히 넘어가기 쉽다.
    warnings.extend(_auditor_overflow_warnings(trace))
    # **채점기의 실패와 「점수가 없다」도 같은 자리에 올린다 (#386).** 추가형이라 run은 완주로
    # 끝나므로 이 경고가 없으면 제출이 전부 실패한 run이 `trace.jsonl`을 열기 전에는 성공한 run과
    # 같은 얼굴이다. 채점기를 안 켠 run은 빈 목록이라 경고가 없다.
    warnings.extend(state.oracle_ledger.warnings())

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
    # 최종 산출물에 등급을 못 실었다. 계산은 `_grades_by_finding`(순수 함수 — `gate_eval`을
    # 재발행하지 않는다)이고 최종 PoC 지정(`_finalize_oracle`)이 **같은 함수**를 부른다.
    #
    # **E4도 이 경로로 나온다 (#460 리뷰).** 8단계 `validate.run`이 만든 런타임 근거(`produced`)를
    # `_analyze`가 `state.evidences`에 모으므로 이 등급이 8단계 `gate_eval`과 같다. 그 전에는 그것을
    # 버려 E4가 `ctx.store`에만 있었고 이 경로·지정이 둘 다 E3으로 읽었다.
    # ⚠ **그래서 리포트의 등급·대표 선정도 E4에 닿는다** - 재현에 성공한 Finding이 `[E3]`이 아니라
    # `[E4]`로 찍히고 `dedupe`의 대표로 E4가 먼저 남는다(`report.dedupe`가 등급 우선이다).
    #
    # ⚠ **「Fake에선 E4가 원리상 도달 불가」를 두 번째 이유로 달았던 것은 거짓이라 뺐다.**
    # `FakeToolRunner._run_repro()`는 실행 대신 대상 소스의 존재를 보므로 Fake 경로에서 E4는
    # 실제로 나고, `test_e4_is_reachable_and_only_at_the_stage_that_runs_the_tools`가 그것을
    # 단언한다(2026-08-21). 못 넘는 것은 **컨테이너 러너에 가짜 `repro_spec`이 갈 때**이고
    # 그것은 빌드가 끊겨서다 — 「Fake auditor」와 「Fake 도구 러너」는 다른 축이다.
    grades = {fid: grade.value for fid, grade in _grades_by_finding(state).items()}
    # 「모든 자리가 실패해 분석되지 않은 대상 수」(#193 ①). 팬아웃된 대상 중 후보를 하나도
    # 못 낸 것 — 형제 멤버가 같은 대상을 보므로 `dropped`(시도)로는 못 세던 값이다.
    # 슬라이스 타깃은 `slice_id`로만 맞춘다 — 후보 `location`은 모델이 `FACTS.target`에서 복사하는데
    # `input: source` 판은 그것이 entry가 아니라 넓힌 함수 범위라 dispatched 키(entry)와 안 맞는다
    # (#529).
    analyzed_slices = {c.slice_id for c in state.candidates if c.slice_id is not None}
    analyzed_targets = {
        _target_key(c.location, None) for c in state.candidates if c.slice_id is None
    }
    unanalyzed_targets = sum(
        1
        for key in state.dispatched_targets
        if (key[-1] not in analyzed_slices if key[-1] is not None else key not in analyzed_targets)
    )
    rendered = report.run(
        ctx,
        findings=findings,
        deferred=state.deferred,
        dropped=state.dropped,
        unanalyzed_targets=unanalyzed_targets,
        critic_deferred=state.critic_deferred,
        critic_top_n=config.thresholds.critic_top_n,
        files=files,
        grades=grades,
        ne_absorbed=state.ne_absorbed,
        pov_results=state.pov_results,
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
        stop_reason=stop_reason,
    )


def _analyze(
    ctx: RunContext,
    artifacts: list[Artifact],
    state: _Accumulator,
    last: int,
    *,
    slice_refs: tuple[SliceRef, ...] | None = None,
) -> None:
    """1~8단계. 9단계 리포트는 예산이 끊겨도 돌아야 하므로 호출자가 따로 부른다."""
    if last < Stage.INDEX:
        return
    graph = index.run(ctx, workdir=".")
    plan = _plan_targets(
        ctx,
        [a for a in artifacts if a.kind in _ANALYZABLE],
        graph,
        state,
        last,
        slice_refs=slice_refs,
    )
    _triage_all(ctx, plan, state, last)
    if last < Stage.CRITIC:
        return
    # **절단 수를 노드가 아니라 여기서 센다.** 노드가 세고 러너가 또 세면 경고와 리포트가
    # 다른 수를 말할 수 있다(report.py `run()`의 `deferred`·`dropped`와 같은 처리).
    promoted = state.in_state(FindingState.HYPOTHESIZED)
    selected = critic.select(ctx, promoted)
    state.critic_deferred = len(promoted) - len(selected)
    # 고른 것이 없으면 Critic 모델을 띄울 이유가 없다. 폐기 판정 try **밖**이다 — 교대 실패는
    # Finding 하나가 아니라 run의 사건이다(`run_pipeline`의 `ModelSwapError` 갈래).
    if selected:
        _swap_for(ctx, Stage.CRITIC)
    for finding in selected:
        try:
            after, decision = critic.run(
                ctx,
                finding=finding,
                evidences=state.evidences.get(finding.finding_id, ()),
                missings=state.missings.get(finding.finding_id, ()),
                # _Once가 저장한 문자열이라 Critic 뒤 PoV도 같은 소스를 받는다.
                source=state.finding_sources.get(finding.finding_id),
                # Auditor가 도구 루프에서 본 관측 — 추론은 안 간다(#600 H2ⓐ · critic.py 모듈
                # docstring).
                hop_observations=state.finding_observations.get(finding.finding_id, ()),
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
            # **크기 거절은 이 Finding에 대한 사실이다 (해소 후속).** 처음에는 여기서
            # `ModelGatewayError`를 일부러 안 잡았다 — `_DROPPABLE_STATUS` 판정을 복사하면
            # 같은 규칙이 두 곳에 따로 산다는 이유였고, "그때는 `_drop`의 갈래를 Finding
            # 쪽으로 끌어오는 것이 맞다"고 적어 뒀다. **그때가 첫 실서빙 run에서 바로 왔다**:
            # 2026-08-28 `run_20260828_0001`이 7단계에서 400(`입력 24,577 + 출력 8,192 >
            # 컨텍스트 32,768`)을 받고 `stopped_after: 7`로 끊겼다. 후보였다면 하나만 버리고
            # 완주했을 사건이다.
            #
            # 그래서 판정을 복사하지 않고 `_is_droppable`로 **빼서 공유한다.** 서버 쪽
            # 사건(상태 없음·5xx)은 그대로 올려 보내고 `run_pipeline`이 매니페스트까지 간다.
            # 크기가 아닌 400은 이 Finding이 아니라 run에 대한 사실이라 `_drop_finding`이
            # 첫 건에서 멈춘다(#296).
            if not _is_droppable(exc):
                raise
            _drop_finding(ctx, state, finding, _REASON_GATEWAY, exc)
            continue
        # 판정을 버리지 않는다. 8단계 게이트가 `p3`을 세려면 이 값이 필요하다.
        state.decisions[finding.finding_id] = (decision,)
        state.update(after)
    # 고른 Finding 전부에서 실패했으면 7단계는 아무것도 못 봤다(#296). 카운터가 아니다 —
    # `_drop_finding`이 카운터를 두지 않는 이유는 그대로다.
    if selected and state.critic_failed == len(selected):
        _stop_if_a_caller_saw_nothing({_caller(Stage.CRITIC, Role.CRITIC)})
    if last < Stage.VALIDATE:
        return
    # `validate.run`(유일한 결정론 E4 게이트)을 **먼저 전부** 돌린다. pov는 그 뒤에 추가로 돈다
    # (PoV 배선 — 추가형). 둘을 finding마다 번갈아 돌리면 pov 실패(재발생 갈래)가 뒤 finding의
    # validate.run을 건너뛰어 E4 게이트가 빠진다 — pov는 게이트를 어지럽히면 안 된다.
    # **pov의 대상은 이 목록으로 시작한다 — `validate.run`이 `QUEUED`로 내린 Finding도 포함한다.**
    # (`pov_attempt_top_n`이 양수면 `_pov_targets`가 `validated` **밖의** Finding을 뒤에 더 붙인다 —
    # 그 추가 대상은 `validate.run`을 안 돈다. 이 목록의 `validate.run` 루프는 안 넓어진다.) 재현
    # 명세가 부르는 `./poc`는 PoV가 낸 PoC 바이트를 파일로 써야 생기는데(STATUS.md §5 — E4를
    # 막는 것은 실행 환경이 아니라 PoC 생성), 이 pov는 바이트를 trace와 run 산출물 사이드카
    # (`pov_poc/`, #355)에 남긴다. ⏵ 2026-10-09 (ADR 0014) — 컨테이너 `poc`는 그 바이트를 과제
    # `-vul` 이미지에 먹이므로 실도구 경로의 E4 **전제**는 섰다(게이트 전이는 그 뒤다). 그 앞의
    # `validate.run`이 재현 실패로 내린 것이 pov가 돌 이유를 지우지 않는다. `validate.run` **뒤**
    # 상태로 거르면 그 강등이 pov의 입장 조건이 되는 순환이고 PoV 시도가 준다 — 바꾸려면 사람이
    # 정한다(`tests/test_nodes.py`의 핀이 지금 동작을 고정한다).
    validated = state.in_state(FindingState.VALIDATED)
    for finding in validated:
        after, produced = validate.run(
            ctx,
            finding=finding,
            workdir=state.workdirs.get(finding.finding_id, "."),
            evidences=state.evidences.get(finding.finding_id, ()),
            missings=state.missings.get(finding.finding_id, ()),
            decisions=state.decisions.get(finding.finding_id, ()),
        )
        state.update(after)
        # **8단계가 만든 런타임 근거(build와 asan·poc 실행 로그)를 모은다.** 버리면
        # `_grades_by_finding`이 8단계 `gate_eval`과 다른 등급(E4 대신 E3)을 계산해 최종 PoC
        # 지정(`_designate_final`)이 E4를 못 본다 - 후보가 둘 이상이면 어느 run에서나 E3 동점이라
        # `top_grade`가 도달 불가였다(#460 리뷰). 이것은 **하네스가 도구를 돌려 낸 내부 근거**이고
        # 채점기 응답이 아니다 - 채점기 값은 `oracle_ledger`에만 들어가고 이 사전에는 안 닿는다
        # (`test_the_oracle_leg_leaves_the_graded_evidences_alone`이 문다). 새 튜플이다 - 5단계가
        # 넣은 튜플을 안 바꾼다.
        if produced:
            state.evidences[finding.finding_id] = (
                *state.evidences.get(finding.finding_id, ()),
                *produced,
            )
    # PoV는 프로필이 pov 좌석을 선언할 때만 돈다. **`served_models()`가 아니라 `profile.roles`로
    # 본다** — fake pov(dev_*)도 좌석이라 라우팅이 돌아야 dev_fake로 검증된다(served면 fake 빠짐).
    pov_targets = _pov_targets(ctx, state, validated)
    if not pov_targets and Role.POV.value in ctx.config.profile.roles:
        state.pov_pool_empty = _pov_pool_empty_reason(ctx, state, validated)
    if pov_targets and Role.POV.value in ctx.config.profile.roles:
        swap = ctx.config.swap
        # pov 모델 교대는 pov가 `swap.swappable`에 있을 때만 부른다 — **이 `if`가 헛교대를 막는
        # 자리다.** pov가 fake 엔드포인트·비-swappable이면 아예 안 부른다. `_swap_for`는 불리면
        # swappable로 한 번 더 거르지만, 목록 밖 역할로 부르면 앞 단계 서버를 내리는 빈 교대라
        # 막는다.
        if swap is not None and Role.POV.value in swap.swappable:
            _swap_for(ctx, Stage.VALIDATE)
        validated_ids = {f.finding_id for f in validated}
        # validate.run이 전이시킨 최신 finding으로 돈다(상태는 안 바꾸지만 기록 일관성).
        for finding in pov_targets:
            try:
                _run_pov(ctx, state, state.findings.get(finding.finding_id, finding))
            except (BudgetExhausted, ModelGatewayError) as stop:
                # 🔴 **오라클에 이미 낸 PoC가 있으면 최종 지정은 하고 올린다**(#532) — 그냥 올리면
                # `_finalize_oracle`을 건너뛰어 그 PoC의 `fix` 다리·판정 조회를 잃고
                # `oracle.final`이 `not_reached`가 된다. 「낸 PoC」는 `_designate_final`이 읽는
                # `candidates`(submit했고 vul 제출이 성공한 시도)다. **추가 대상**
                # (`pov_attempt_top_n`)에서 끊긴 run은 후보가 0건이어도 이 단계를 돈다
                # (`none`/`no_poc`) — 예산은 종전대로, 게이트웨이는 #532에서 새로다. 둘 다 아니면
                # `not_reached`다.
                # 사유는 새로 안 적는다 — 같은 예외를 올리면 `run_pipeline`의 갈래가 적는다.
                # `except Exception`으로 안 넓힌 것은 일부러다 — 핸들러가 `raise`로 다시 올리므로
                # 어느 예외가 run을 멈추느냐는 안 바뀐다. 바뀌는 것은 **임의 예외에서도 오라클
                # 네트워크 호출(`fix` 다리·판정 조회)이 돈다**는 것이다(핀:
                # `test_a_non_target_exception_…`).
                # 버릴 수 있는 게이트웨이 오류는 루프가 흡수해 여기 안 온다
                # (`_pov_classify_exception`). `ModelSwapError`(하위형)도 이 `except`에 들지만 이
                # 루프 안에는 교대를 부르는 경로가 없다(`_swap_for`·`_swap`을 전수로 열어 확인했다 —
                # 루프 앞·단계 진입·Critic 앞·run 끝뿐) — 그래서 전용 핀이 없다.
                # ⚠ `_finalize_oracle`이 흡수하는 것은 `oracle.finalize`의 `Exception`과 장부
                # 쓰기의 `OSError`뿐이다 — 그 밖에서 던지면 원 예외가 가려지므로 `add_note`로
                # 붙이고 원 예외를 올린다. 그 예외를 `run_pipeline`의 예산·게이트웨이 갈래가
                # **삼키므로** 그 갈래가 `__notes__`를 읽어 `warnings`에 싣는다(안 읽으면 사유가
                # 버려진다 — 매니페스트 실패의 `add_note`는 `raise`로 올라가 트레이스백에 찍히는
                # 것과 다르다. `ModelSwapError` 갈래는 안 읽는다 — 위 이유로 여기서 안 온다).
                # 그 실경로는 못 찾았다(미확인).
                if state.oracle_ledger.candidates or finding.finding_id not in validated_ids:
                    try:
                        _finalize_oracle(ctx, state)
                    except Exception as exc:
                        stop.add_note(f"최종 지정을 돌리지 못했다: {type(exc).__name__}: {exc}")
                raise
    # 채점기의 최종 단계는 **pov가 돌았든 안 돌았든** 8단계 끝에서 한 번이다(#386) — 낼 PoC가
    # 없었다는 사실도 기록이다(`status=none`). 예외로 위 루프가 끊기면 여기 못 오고 `oracle.final`이
    # `not_reached`로 남는다 — **위 `except`가 받는 예외로 끊긴 run은 예외다**(낸 PoC가 있거나 추가
    # 대상에서 끊겼으면 최종 지정을 하고 올린다. 그 밖의 예외는 이 예외에 안 든다).
    _finalize_oracle(ctx, state)


def _pov_targets(
    ctx: RunContext, state: _Accumulator, validated: list[Finding]
) -> list[Finding]:
    """PoV를 시도할 Finding. 기본은 `validated` 그대로고, 임계가 양수면 그 **밖에서** 더 붙인다.

    **`validated`는 빼지 않는다.** `validated`는 전부 그대로 돌고, `pov_attempt_top_n`은 그 뒤에
    붙일 수의 상한이다. 그래서 이 상한이 잘라 낸 Finding은 **종전에도 PoV를 안 돌던 것**이고,
    **상한 자체가** 시도할 대상을 조용히 잃게 하지는 않는다(`candidate_max`가 잘린 후보를
    `target_skipped`로 남겨야 했던 이유가 여기엔 없다 — 그쪽은 파이프라인에서 빼는 상한이다).
    ⚠ 다만 **추가 대상은 예산을 더 쓴다** — 거기서 `BudgetExhausted`나 서버 쪽 `ModelGatewayError`가
    나면 run은 그 자리에서 끝나지만, 앞 대상이 이미 낸 PoC의 최종 지정(`fix` 다리·판정)은 하고
    끝난다(`_analyze` · #532). 그 밖의 예외는 최종 지정을 안 돈다.

    **추가 대상 풀 — `validated` 밖에서 Critic이 밀어 내지 않은 Finding.** 둘을 **뺀다.**
    ① **Critic이 반박(F-6)으로 판정한 것** — `gate.normalize` 뒤 판정이 `rebut`인 것이다(근거 없는
    반박은 `uphold`로 정규화돼 정책이 `QUEUED`로 안 보내므로 **든다**). 보류(`need_evidence`, F-7)는
    **든다** — 「아니다」가 아니라 「모르겠다」라서다(⏵ 2026-10-11, 이슈 #604: 전엔 같이 뺐고 보류
    5/5인 run의 풀이 비었다). Critic 응답의 verdict를 못 읽으면 `to_decision`이 `need_evidence`로
    보내므로(`critic_verdict_unmapped` trace) 그 Finding도 보류로 풀에 든다 —
    2026-10-11 `run_20261011_0001`의 5/5 보류(이슈 #604)는 `critic_verdict_unmapped` 0건이라
    파서 폴백 0건 — 파서 폴백이 아니라 모델이 낸 값이다(trace 계수 2026-10-11 · 모델 판단인지
    입력 구조 탓인지는 미실측).
    ② **판정 호출이 실패해 버려진 것**(`_drop_finding`) — 상태가 안 바뀌어
    (`HYPOTHESIZED` 그대로) 상태로는 못 가르고 `state.critic_failed_ids`로 안다. **든다:**
    Critic을 **안 거친** Finding(`critic_top_n`에 잘린 것 · 6단계가 E0로 `QUEUED`한 F-2), 재현
    명세가 없어 멈춘 것(F-9), 근거가 약해 `hypothesized`에 둔 것(F-3 · 등급 미달의 `critic_upheld`).
    판정표는 `tests/test_nodes.py`의 `test_the_extra_pov_pool_excludes_…`다.

    **왜 빼나.** 풀에 두면 **하네스 Critic이 「아니다」라고 한 Finding의 PoC가 공식 최종 제출이 될
    수 있다** — 다른 후보가 없으면(`sole_candidate`) 그것이 최종이고, 상한이 남으면 반박도 풀에
    든다. 정렬 키(판정 계단 → `confidence` → `finding_id`)는 반박을 맨 뒤(3)에 두지만
    (`_verdict_ranks_by_finding`) 계단은 **순서**일 뿐 거르지 못한다 — 그래서 이 필터가 먼저고 3은
    **지금 닿지 않는 값**이다(⏵ 2026-10-12). 이 논거는 반박에만 선다. 보류는 풀에 들되 자리를
    uphold **뒤** 미경유 **뒤**로 낮춘다(아래 정렬 키 · `_designation_key`의 첫 계단).

    **왜 뚫었나.** 2026-10-03 첫 실모델·실도구 run은 완주했는데 PoV가 **0건**이었다. 그 run의
    Finding 셋은 `evidence_refs`와 `repro_spec`이 전부 비어 6단계가 E0로 `QUEUED`
    (`insufficient_evidence`)에 내렸고 Critic을 한 번도 안 거쳤다 (⏵ **2026-10-08 — 인용이 비어서
    E0가 되는 기제는 끊었다**: 이슈 #552로 6단계가 타깃 관측 Evidence를 직접 붙인다. 이 길을 둔
    이유는 남는다 — 관측이 0건인 타깃과 F-9는 그대로다)
    (`docs/experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md` §4) —
    8단계 진입 시점의 `validated`가 비어 있었다. Level 1은 재현 명세의 근거를 주지 않아
    (`repro_spec`이 `null`인 것이 정상이다 — 이슈 #354~#360) 인용이 고쳐져도 F-9에서 멈출 수 있다.
    PoC가 없으면 채점 점수가 없다(`oracle.final`이 `no_poc`). 그래서 등급이 못 올라간 Finding에도
    PoV를 시도할 길을 뒀다.

    ⚠ **이 필터는 그 run의 풀을 바꾸지 않는다.** 필터는 반박된 Finding의 PoC가 최종 제출이 되는
    것을 막지만, 이 기능이 처음 겨냥한 run(`run_20261003_0003`)의 Finding 셋은 Critic을 안 거쳐
    `state.decisions`가 비어 있고(그래서 전부 풀에 든다), 셋 다 **Auditor가 스스로 낸 부정
    결론**이다 — `trigger_status=no_signal` · `confidence` 0.10·0.10·0.05(같은 날 WSL
    `run_20261003_0002`의 다섯은 `reachability_unknown` 3 · `no_signal` 2, 전부 E0·Critic 0회).
    풀은 그것을 거르지 않는다(결정 대기). **그런 Finding에서 PoV가 낼 PoC가 있는지는
    미측정이다**. 출처는 위 보고서 §4·결과 표다.

    ⏵ 2026-10-12 — `pov_attempt_top_n`(8 — `config/budgets.yaml`)을 켠 실모델 run은 이미 있다.
    그중 8단계가 돌아 PoV가 채점기에 PoC를 낸 run은 둘이다.
    - 2026-10-08(`run_20261007_0001` · Critic 미경유 E0 Finding 5건 · 제출 7건) —
      `docs/experiments/harness/2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md` §3·§5
    - 2026-10-12(PoC 4건·채점 0) —
      `docs/experiments/harness/2026-10-12-serving-run-arvo1065-vastai-remote.md` §4·§5
      (PR #621, 미머지)

    10-08 보고서는 PoC를 Finding의 `trigger_status`에 잇지 않아(Auditor 판정은 no_signal 1 ·
    guarded 2 · triggerable 2) 부정 결론 Finding발 PoC가 있었는지는 그 보고서로 안 갈린다 —
    위 「미측정」은 그대로다(#615). 10-10 넷은 8단계 전에 죽었고 10-11은 PoV 풀이 비었다
    (`docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md` §1·§3).

    🔴 **`validate.run` 루프는 안 넓힌다.** 그쪽은 유일한 결정론 E4 게이트이고 `from=validated`를
    요구한다 — 거기에 다른 상태를 넣으면 상태 기계(F-10~F-12)가 깨진다. 넓히는 것은 **PoV
    쪽뿐**이고, PoV는 추가형이라 상태를 안 바꾼다(`_run_pov`).

    ⚠ **이것만으로 등급이 오르지는 않는다.** ⏵ 2026-10-09 (ADR 0014) — `poc` 도구가 그 바이트를
    과제 이미지에 먹이게 됐지만 **게이트 전이는 별건이다**(`p4`가 종료 코드까지 본다 — #232).
    이 함수가 바꾸는 것은 **채점기에 낼 후보가 생기는가**이고 내부 등급이 아니다.

    자르는 순서는 Critic 판정(uphold → 미경유 → 보류) 다음 `critic.select`와 **같은 키**다 —
    `confidence` 내림차순, 동점은 `finding_id`(정렬이 흔들리면 재현 실행이 갈라진다).
    ⚠ **`confidence`는 모델이 적은 수고 게이트가 계산한 값이 아니다** — 그래서 이것은
    등급 규칙이 아니라 **순서 규칙**이다. 상한은 **위 풀을 거른 뒤**에
    건다. 상한이 0이면 이 정렬은 아예 돌지 않는다.
    """
    limit = ctx.config.thresholds.pov_attempt_top_n
    if limit <= 0:
        return validated
    already = {finding.finding_id for finding in validated}
    verdicts = _verdict_ranks_by_finding(state)
    rest = sorted(
        (
            f
            for f in state.findings.values()
            if f.finding_id not in already and not _critic_set_aside(state, f.finding_id)
        ),
        key=lambda f: (verdicts[f.finding_id], -f.confidence, f.finding_id),
    )
    return [*validated, *rest[:limit]]


def _critic_set_aside(state: _Accumulator, finding_id: str) -> bool:
    """7단계가 이 Finding을 **밀어 냈나** — 판정 호출이 실패해 버려졌거나(`_drop_finding`), Critic이
    반박(F-6)으로 판정했다. 보류(`need_evidence`, F-7)는 안 밀어 낸 것이다 — 풀에 들고 정렬·지정에서
    뒤로 간다(`_verdict_ranks_by_finding`). ⏵ 2026-10-11 — 전엔 보류도 뺐다(이슈 #604). 판정은
    `gate.normalize` 뒤 값으로 읽는다 — 정책(`_critic_verdict`)이 읽는 값과 같아야 `QUEUED`(F-6)와
    이 판정이 어긋나지 않는다. Critic 응답의 verdict를 못 읽으면 `to_decision`이 `need_evidence`로
    보내므로(`critic_verdict_unmapped` trace) 그 Finding도 보류로 풀에 든다 —
    2026-10-11 `run_20261011_0001`의 5/5 보류(이슈 #604)는 `critic_verdict_unmapped` 0건이라
    파서 폴백 0건 — 파서 폴백이 아니라 모델이 낸 값이다(trace 계수 2026-10-11 · 모델 판단인지
    입력 구조 탓인지는 미실측).

    ⚠ **판정은 지금 Finding당 하나다**(`state.decisions[...] = (decision,)`가 7단계 루프의 한 곳에서
    **대입**이라 항상 1-튜플이다). 복수가 되면 게이트의 읽는 법(`gate.evaluate`의 `upheld`는
    `any(normalize == UPHOLD)`)과 이 `any(normalize == REBUT)`가 갈릴 수 있다 — 그때 맞출 것."""
    return finding_id in state.critic_failed_ids or any(
        gate.normalize(decision) is Verdict.REBUT
        for decision in state.decisions.get(finding_id, ())
    )


def _auditor_zero_probe_warning(trace: TraceWriter) -> str | None:
    """Auditor 도구 루프의 홉이 1건 이상 났는데 `probe`가 한 번도 **실행되지** 않았으면 run 경고,
    아니면 `None`(#617).

    홉 이벤트가 1건도 없으면 말할 것이 없다 — 루프가 꺼졌거나, 켰어도 첫 모델 호출이
    전부 실패한 run이다(이벤트는 호출 성공 뒤에만 나간다 - `EventKind.AUDITOR_HOP` 주석. 크기 거절만
    예외라 첫 호출이 전부 크기 거절이면 `context_overflow` 홉만 남는데 그것은 `action`이 없는
    홉이라 아래에서 뺀다 - 도구가 안 돈 것이 아니라 모델이 한 번도 못 답한 것이다). 못 가른다.

    `stop_reason`이 달린 `probe` 홉(`MAX_HOP`·`REPEAT_REQUEST`)은 세지 않는다 - 그 홉의 조사는
    안 돌았다(`_run_hops`). 도구 실패(`observation_error`)로 끝난 probe 홉은 세고, 왜 안 골랐는지는
    이 run이 가르지 못한다."""
    hops = [e.data for e in trace.events if e.kind is EventKind.AUDITOR_HOP and "action" in e.data]
    ran = sum(1 for d in hops if d.get("action") == "probe" and "stop_reason" not in d)
    if not hops or ran:
        return None
    lookups = sum(1 for d in hops if d.get("action") == "lookup")
    answers = sum(1 for d in hops if d.get("action") == "answer")
    return (
        f"Auditor 도구 루프가 {len(hops)}홉 났는데(lookup {lookups} · answer {answers}) "
        f"probe가 한 번도 실행되지 않았다 — 5단계에서 도구가 한 번도 안 돌았다 (#617)"
    )


def _whitespace_tail_warning(trace: TraceWriter) -> str | None:
    """공백뿐인 꼬리의 `length` 시도가 1건 이상이면 run 경고, 아니면 `None`(#79).

    `model_call.attempt_tail_whitespace_only`의 `true` 칸을 센다(역할별). `false`·`null`(해당
    없음·원문 없음)은 세지 않는다 - 경고가 없다는 것은 「공백 꼬리가 없었다」가 아니라 「판정할 수
    있었던 시도에 없었다」다. 칸의 `null` 두 뜻은 `trace.py`에 있다.
    """
    by_role: dict[str, int] = {}
    for event in trace.events:
        if event.kind is not EventKind.MODEL_CALL:
            continue
        raw = event.data.get("attempt_tail_whitespace_only")
        hits = sum(1 for cell in json.loads(raw) if cell is True) if isinstance(raw, str) else 0
        if hits:
            role = str(event.data.get("role"))
            by_role[role] = by_role.get(role, 0) + hits
    if not by_role:
        return None
    detail = ", ".join(f"{role} {count}시도" for role, count in sorted(by_role.items()))
    # 창 크기는 `TAIL_WHITESPACE_CHARS`에서 포매팅해 넣는다 - 숫자를 문구에 박으면 상수가
    # 움직일 때 경고가 거짓을 말한다(CLAUDE.md §2.3).
    return (
        f"length 절단 시도의 꼬리 {TAIL_WHITESPACE_CHARS}자가 전부 공백인 시도: {detail} - "
        "이슈 #79 재개봉 조건. trace model_call의 attempt_tail_whitespace_only가 어느 호출인지 "
        "가리킨다"
    )


# `schemas/auditor.py`의 `_check_action` 거절 문구 앞부분 - 봉투마다 payload 이름(`finding` ↔
# `procedure`)이 달라 그 앞에서 끊는다. 다른 액션의 같은 검사(`lookup 액션은 …`)와 다른 액션의
# 필드가 실린 거절(`… 다른 액션의 필드가 실렸다`)은 이 문구가 아니다.
_EMPTY_ANSWER_PHRASE = "answer 액션은 비지 않은"


def _auditor_empty_answer_warning(trace: TraceWriter) -> str | None:
    """비어 있는 `answer`를 스키마가 거절한 Auditor 호출이 있으면 run 경고, 아니면 `None`(#602).
    재시도가 소진돼 `SchemaParseError`로 끝난 호출만 센다.

    **호출 단위다** - 시도(`attempts`)가 아니라 `model_call` 이벤트 한 건이 한 번이다(한 호출이
    두 시도 모두 거절돼도 버려진 후보는 하나). 홉 번호를 보지 않으므로 hop 0도 센다.

    ⚠ **마지막 시도의 오류만 본다** - `error_detail`이 소진 시점의 `last_error`를 싣는다
    (`openai_compat`). 첫 시도가 빈 `answer`였다가 둘째 시도가 다른 이유로 거절되면 세지 않고,
    재시도로 살아난 호출(`ok: true`)도 세지 않는다. 그래서 이 수는 「후보를 잃은 호출」의 하한이다.
    `error_detail`은 512자에서 잘리지만 이 문구는 그 앞(모델 이름 · 스키마 이름 바로 뒤)에 있다."""
    dropped = sum(
        1
        for event in trace.events
        if event.kind is EventKind.MODEL_CALL
        and event.data.get("role") == Role.AUDITOR.value
        and event.data.get("ok") is False
        and event.data.get("error") == SchemaParseError.__name__
        and _EMPTY_ANSWER_PHRASE in str(event.data.get("error_detail", ""))
    )
    if not dropped:
        return None
    return (
        f"Auditor answer에 procedure/finding이 비어 거절된 호출 {dropped}건(#602) — "
        f"후보가 C-6으로 버려졌다; 원문은 model_call.response_refs"
    )


def _critic_hop_observations_warning(trace: TraceWriter) -> str | None:
    """Critic에 Auditor 홉 관측을 실은 요청이 (a) 추정 입력 몫을 넘을 수 있거나 (b) 크기 거절을
    받아 관측 절을 빼고 다시 물은 건이 있거나 (c) 홉의 Critic 사본을 못 만든 건
    (`observation_sanitize_failed`)이 있으면 run 경고, 아니면 `None`(#600 H2ⓐ).

    `critic_hop_observations`는 두 모양이다: 관측을 실은 호출마다 1건(`over_input_share`)과, 그
    호출이 400/413으로 거절돼 `critic.run`이 절을 통째로 빼고 한 번 다시 물었다는 표지
    (`observations_dropped`).
    (a)는 글자 수 ÷ 최악 비 1.22의 **계산값**이라 「넘을 수 있다」이지 「넘었다」가 아니다 - 서버의
    크기 거절은 (b)가 실제로 센다. 관측 절을 뺀 프롬프트도 넘는 건(`over_without_observations`)은
    원인이 관측이 아니라 따로 센다."""
    events = [e.data for e in trace.events if e.kind is EventKind.CRITIC_HOP_OBSERVATIONS]
    dropped = [d for d in events if d.get("observations_dropped")]
    carried = [d for d in events if not d.get("observations_dropped")]
    over = [d for d in carried if d.get("over_input_share")]
    # 가림에 실패해 Critic 사본을 못 만든 홉 - Critic 절의 사본 수가 돈 홉 수보다 작은 이유다.
    unsanitized = sum(
        1
        for e in trace.events
        if e.kind is EventKind.AUDITOR_HOP and "observation_sanitize_failed" in e.data
    )
    if not over and not dropped and not unsanitized:
        return None
    parts = [f"Critic 요청 {len(carried)}건이 Auditor 홉 관측을 실었다"]
    if unsanitized:
        parts.append(f"Critic 사본을 만들지 못한 홉 {unsanitized}건(가림 실패 · 사유는 trace)")
    if over:
        alone = sum(1 for d in over if d.get("over_without_observations"))
        parts.append(
            f"그중 {len(over)}건의 추정 입력이 입력 몫({over[0].get('input_share_tokens')}토큰)을 "
            f"넘을 수 있다 - 글자/토큰 최악 비 1.22로 센 계산값이고 하네스는 자르지 않았다"
            f"(관측 절 없이도 넘는 건 {alone})"
        )
    if dropped:
        parts.append(
            f"{len(dropped)}건은 서버가 크기로 거절해 관측 절을 통째로 빼고 한 번 다시 물었다 "
            "(실측한 거절이다)"
        )
    return " · ".join(parts) + ". trace의 critic_hop_observations가 목록이다 (#600)"


def _auditor_overflow_warnings(trace: TraceWriter) -> list[str]:
    """크기 거절당한 Auditor 도구 루프 홉마다 run 경고 한 줄(#514) - 후보 id와 홉.

    그 후보는 버려졌고(`queued` + `target_skipped`) run은 갔다. 연속 폐기 카운터가 크기 거절을
    안 세므로(`_drop`) 이 줄이 없으면 호출 실패 총계 한 줄 말고는 어느 후보가 몇 번째 홉에서
    잘렸는지 `trace.jsonl`을 열어야 안다. 홉이 많을수록 잘릴 확률이 오르는 상한
    `auditor_tool_hops`의 근거가 이 줄들이다.
    """
    return [
        f"후보 {e.subject_id}의 Auditor 홉 {e.data['hop']}에서 크기 거절 — 후보를 버렸다(#514)"
        for e in trace.events
        if e.kind is EventKind.AUDITOR_HOP
        and e.data.get("stop_reason") == AuditorStopReason.CONTEXT_OVERFLOW.value
    ]


def _pov_pool_empty_reason(ctx: RunContext, state: _Accumulator, validated: list[Finding]) -> str:
    """좌석은 있는데 8단계 대상이 0건일 때의 한 문장 (이슈 #357 잔여) — `validated` 밖 Finding이
    왜 대상이 못 됐는지 다섯 칸으로 센다. 판정 실패·반박 둘은 `_critic_set_aside`가 밀어 내는
    쪽, 판정 없음·업홀드 미승격 둘은 들이는 쪽이다(판정은 `gate.normalize` 뒤 값). 보류는
    K>0에서 풀에 들므로(PR #608 · `_critic_set_aside`) 보류 칸은 K>0에서 항상 0이고 K=0에서만
    정보 칸이다. `_critic_set_aside`와 독립으로 센다(K>0일 때만 불린다). K = `pov_attempt_top_n`.

    ⚠ **「8단계 PoV는 한 건도 시도되지 않았다」는 PoV 시도 0건이다 — 8단계 span의 부재가 아니다.**
    채점기를 붙인 run은 대상이 0건이어도 `_finalize_oracle`이 8단계 span 안에서 `oracle_final`을
    남긴다(리뷰 #606)."""
    # 풀이 비면 `validated`도 비므로 이 칸(`validated {len}`)은 구조상 0이다 — 방어로 둔다.
    # 보류 칸에는 verdict 파서 폴백이 섞인다 — 못 읽은 verdict는 `to_decision`이 `need_evidence`로
    # 보낸다(`schemas/decision.py`). 가르는 값은 trace `critic_verdict_unmapped` 건수다(계수기는
    # 두지 않는다: K>0에서 이 칸은 0이다).
    inside = {f.finding_id for f in validated}
    rebut = hold = failed = unjudged = other = 0
    for finding_id in state.findings:
        if finding_id in inside:
            continue
        verdicts = {gate.normalize(d) for d in state.decisions.get(finding_id, ())}
        if finding_id in state.critic_failed_ids:
            failed += 1
        elif Verdict.REBUT in verdicts:
            rebut += 1
        elif Verdict.NEED_EVIDENCE in verdicts:
            hold += 1
        elif not verdicts:
            unjudged += 1
        else:
            other += 1
    limit = ctx.config.thresholds.pov_attempt_top_n
    rest = f" · 업홀드 미승격 {other}" if other else ""
    why = (
        "pov_attempt_top_n이 0이라 validated 밖은 후보가 아니다(상한 0)"
        if limit <= 0
        else "validated 밖 후보도 Critic이 모두 밀어 냈거나 없다"
    )
    return (
        f"PoV 대상 0건 — validated {len(validated)} · Critic 반박 {rebut}"
        f" · 보류(파서 폴백 포함) {hold} · 판정 실패 {failed} · Critic 미경유 {unjudged}{rest}"
        f" · pov_attempt_top_n {limit}. "
        f"8단계 PoV는 한 건도 시도되지 않았다({why})"
    )


# PoV 관측에 싣는 도구 출력 꼬리 길이. **잰 값이 아니라 설계 상수다** — ASan 요약(`SUMMARY:` 줄과
# 그 위 스택 몇 프레임)이 들어가는 폭이고, 글자 상한은 토큰을 묶지 않는다(실측 비 1.22~3.32,
# `runner.py`의 토큰 환산 주석 참조). 목적은 폭주 차단이다.
_POV_OBSERVATION_TAIL_CHARS = 2000

# `RunContext.read_payload`와 같은 모양 — evidence payload 참조를 원문으로 바꾼다. `None`은
# "로그가 없다"는 정상 상태다(`dev_fake`).
ReadPayload = Callable[[str], str | None]

# libFuzzer 하네스의 진입점 이름. CyberGym의 arvo·oss-fuzz 대상이 공통으로 쓰는 **표준 이름
# 하나**다 — 변주를 미리 넣지 않는다(실측으로 못 찾는 과제가 나오면 그때 센다).
_FUZZ_ENTRYPOINT_NAME = "LLVMFuzzerTestOneInput"

# 진입점 조회가 PoV 프롬프트에 실을 수 있는 글자 상한. **잰 값이 아니라 설계 상수다** —
# 진입점 함수 하나를 덮는 폭이고, 글자 상한은 토큰을 묶지 않는다(메모리 — 실측 비 1.22~3.32).
# 목적은 폭주 차단이다.
_FUZZ_ENTRYPOINT_CHARS = 4000

# resolver 조회(`ctx.resolve_requests`)에 넘기는 `max_chars`. **원문 상한과 다른 상수다** —
# resolver의 `_fit_budget`은 헤더·배너·줄번호 gutter까지 더한 **렌더 길이**를 이 값과 비교한다
# (`symbol_resolver.render_fragment`). `_FUZZ_ENTRYPOINT_CHARS`를 그대로 넘기면 원문이 그 상한
# 밑이어도 gutter overhead 때문에 렌더가 넘쳐 블록이 통째로 `truncated`에 빠지고, 그러면
# `_fuzz_entrypoint`가 `ctx.read_source`를 아예 안 불러 아래 `len(source) >
# _FUZZ_ENTRYPOINT_CHARS` 자르기가 죽은 코드가 된다(적대적 리뷰 2026-10-02 MEDIUM-1). resolver는
# **위치만** 쓰고(아래 `_fuzz_entrypoint`가 `ctx.read_source`로 원문을 직접 읽는다), 글자 상한은
# 그 원문에 한 번만 건다 — 그래서 여기는 넉넉하게 잡는다.
_FUZZ_ENTRYPOINT_RESOLVE_MAX_CHARS = 1_000_000


def _fuzz_entrypoint(ctx: RunContext, target_file: str) -> dict[str, object]:
    """PoV 프롬프트에 실을 **퍼즈 진입점**(`LLVMFuzzerTestOneInput`)과 그 본문.

    CyberGym Level 1은 libFuzzer 하네스에 먹일 raw 바이트를 만드는 과업이고, **바이트가 어느
    함수의 어느 인자로 들어가는지**가 앞단 포맷 조건을 읽는 출발점이다. 문헌이 꼽는 지배적
    실패는 크래시 로직이 아니라 "취약 경로 미도달"이다(CyberGym 논문 arXiv 2506.02548 — **남이
    주장한 것**이고 우리 실측이 아니다). 그 사실이 없으면 PoV는 매직·헤더를 추측으로 맞춘다.
    2026-10-02 실측으로 `LLVMFuzzerTestOneInput`은 `src/`·`prompts/` 전체에 **0건**이었다.

    **조회는 기존 resolver를 재사용한다**(`ctx.resolve_requests`). 이름으로 함수 본문을 찾는
    수단이 그것뿐이다 — `symbols_for`는 (파일 → 심볼)이고 `definitions_of`는 `macros`·`types`·
    `file_vars`·`enum_constants`만 본다(`src/adapters/index_graph.py`의 `DEFINITION_TABLES`). 그래서
    새 훅·새
    어댑터 배선이 0이다.
    resolver는 `table("functions")`가 없으면 빈 `Resolution`을 돌려주므로(`need_evidence` 모듈)
    인덱스 없는 run에서도 예외 없이 "못 찾았다"로 떨어진다.

    **못 찾아도 dict를 돌려준다** — `found=False` + `reason`. 키를 빼면 "안 찾았다"와 "찾았는데
    없다"가 구별되지 않는다(CLAUDE.md §0). `reason`이 가르는 것은 여섯이다(⏵ 2026-10-02 정정 —
    전 판은 "다섯"이라 적고 아래 여섯을 나열했다): resolver 훅 자체가 없음(프로덕션에서는 안
    닿는다 — factory가 항상 꽂는다) · `None` 반환(부분 구현) · **인덱스가 없거나 v1**(빈
    `Resolution` — `unfilled`조차 빈 것이 그 서명) · 함수 표에 이름이 없음 · 상한에 잘림 · 위치는
    있는데 원문을 못 읽음.

    ⚠ **`source`는 `ctx.read_source`로 직접 읽는다.** `resolution.followup_text`를 실으면 NE 루프의
    후속 턴 포장(머리·gutter·*"ScoutResponse JSON을 내라"*)이 FACTS에 들어간다 — 아래 그 자리의
    주석이 근거다(적대적 리뷰 2026-10-02 H-1).
    """
    missing: dict[str, object] = {
        "found": False,
        "name": _FUZZ_ENTRYPOINT_NAME,
        "file": "",
        "lines": [],
        "source": "",
        "reason": "",
    }
    if ctx.resolve_requests is None:
        # ⏵ 2026-10-02 정정(적대적 리뷰 LOW): 전 판은 이 사유에 "인덱스 v1 또는 미로드"를 적어
        # 아래(:1232 부근) **다른 갈래**의 사유와 겹쳤다. 이 갈래는 **훅 자체가 안 꽂힌** 경우고
        # (factory가 프로덕션에서는 항상 꽂으므로 실서빙에서는 안 닿는다 — 위 docstring), 인덱스
        # 판이 v1인지는 훅이 꽂힌 **다음에** `resolve()` 안에서 갈린다(:1232의 빈 `Resolution`).
        # 고칠 자리가 다르니 사유도 다르다.
        missing["reason"] = "이 run에 resolve_requests 훅 자체가 안 꽂혔다 — 조회 수단이 없었다"
        return missing
    query = ResolveQuery(
        requests=(
            RequestedContext(
                kind="function",
                target=_FUZZ_ENTRYPOINT_NAME,
                purpose="PoC 바이트가 들어가는 진입점과 앞단 파서를 읽는다",
                request_code="pov_fuzz_entrypoint",
            ),
        ),
        hop=1,
        target_file=target_file,
        # 슬라이스가 이미 보여준 줄을 모르는 자리다 — 비워 두면 resolver가 `already`로 빼지 않고
        # 블록을 채운다. PoV의 `SOURCE`와 겹칠 수 있지만, 겹침을 숨기는 것보다 **진입점이 확실히
        # 실리는 쪽**이 낫다(겹침은 모델이 읽고 무시할 수 있으나 부재는 못 메운다).
        visible={},
        # resolver 자체의 budget 컷이 아니라 `read_source` 뒤 자르기 하나로 상한을 건다 —
        # 위 `_FUZZ_ENTRYPOINT_RESOLVE_MAX_CHARS` 주석.
        max_chars=_FUZZ_ENTRYPOINT_RESOLVE_MAX_CHARS,
    )
    try:
        resolution = ctx.resolve_requests(query)
    except (OSError, ValueError) as exc:
        # ⏵ 2026-10-02 추가(적대적 리뷰 LOW) — **이 조회는 보조 정보지 pov의 필수 전제가
        # 아니다.** `safe_join`의 `PathEscape`(`ValueError` 하위형, `src/paths.py`)처럼 "조용히
        # 안 넘기고 실패시킨다"로 설계된 예외가 인덱스 쪽 문제(손상된 경로 등)로 올라오면, 이
        # 함수 **밖**에는 이것을 잡는 except가 없다(`_run_pov_hops`의 `except (ModelGatewayError,
        # SchemaParseError)`는 이 타입들을 안 본다) — 그러면 진입점 탐색 하나가 run 전체를
        # 멈춘다. 체계적 pov 실패는 run을 못 멈춘다는 원칙(§ 이 함수 docstring)에 맞춰 여기서
        # 삼키고 "못 찾았다"로 내려간다.
        missing["reason"] = f"진입점 조회가 예외로 실패했다: {type(exc).__name__}: {exc}"
        return missing
    if resolution is None:
        # 훅이 꽂혀 있는데 `None`을 돌려주는 배선(스텁·부분 구현)이 실재한다 — 2026-10-02에
        # `test_run_pipeline_wires_resolve_requests_from_deps`가 그 경로로 터졌다. 타입은
        # `Resolution`을 약속하지만 경계에서 믿지 않는다.
        missing["reason"] = "resolver가 Resolution 대신 None을 돌려줬다 — 배선이 부분 구현이다"
        return missing
    block = next((b for b in resolution.blocks if b.name == _FUZZ_ENTRYPOINT_NAME), None)
    if block is None:
        if _FUZZ_ENTRYPOINT_NAME in resolution.truncated:
            # resolver의 budget 컷(`_FUZZ_ENTRYPOINT_RESOLVE_MAX_CHARS`)은 넉넉해 이 갈래는
            # 함수 하나가 그만큼 거대할 때만 닿는다 — 아래 `len(source) > _FUZZ_ENTRYPOINT_CHARS`
            # 자르기(원문 상한)와는 다른 상한이다(적대적 리뷰 2026-10-02 MEDIUM-1).
            missing["reason"] = (
                f"진입점을 찾았으나 resolver 조회 상한"
                f"({_FUZZ_ENTRYPOINT_RESOLVE_MAX_CHARS:,}자, 렌더 기준)에 통째로 잘렸다"
            )
        elif not resolution.index_available:
            # **인덱스가 없거나 v1이다.** resolver는 `table("functions")`가 없으면 조회를 안 하고
            # `index_available=False`를 돌려준다(#315 — 전에는 `unfilled`조차 비는 서명으로
            # 갈랐다). 이것을 "함수 표에 없다"와 같은 사유로 적으면 **고칠 자리가 인덱스인지
            # 이름인지 안 갈린다**
            # (§0). 훅은 factory가 **항상** 꽂으므로(`src/adapters/factory.py`) 위의
            # `resolve_requests is None` 갈래는 프로덕션에서 안 닿는다 — 여기가 그 자리다.
            missing["reason"] = "인덱스가 없거나 v1이다(함수 표 없음) — 조회가 아무 사유도 못 냈다"
        else:
            # ⏵ 2026-10-02 정정(적대적 리뷰 LOW): 전 판은 여기서 늘 "함수 표에 없다"고 적었는데
            # `resolution.unfilled`가 비지 않은 사유는 그것 하나가 아니다 —
            # `symbol_resolver._unfilled_lookup_reason`은 "인덱스에는 있는데 파일을 못 읽어
            # 여기까지 왔을 때"도 같은 자리로 보낸다(그 함수 docstring). 추측하지 않고
            # resolver가 **실제로 낸** 사유(`Unfilled.text`)를 그대로 싣는다.
            unfilled = next(
                (u for u in resolution.unfilled if u.target == _FUZZ_ENTRYPOINT_NAME), None
            )
            missing["reason"] = (
                f"진입점 조회가 실패했다 — {unfilled.text}"
                if unfilled is not None
                else (
                    f"인덱스의 함수 표에 {_FUZZ_ENTRYPOINT_NAME}가 없다 — "
                    "이 과제의 하네스 진입점 이름이 다를 수 있다"
                )
            )
        return missing
    # 🔴 **`resolution.followup_text`를 싣지 않는다.** 그것은 함수 본문이 아니라 NE 루프의 **후속
    # 턴 전체**다(`render_followup`) — `## Retrieved context (hop 1)` 머리와 줄번호 gutter, 그리고
    # 꼬리에 *"output one complete ScoutResponse JSON"*이 붙는다. FACTS는 body.md가 *"참조 가능한
    # 사실 전부"*라 선언하는 블록이라, 거기에 **다른 역할의 지시**가 실리면 PoV가 그것을 사실로
    # 읽는다(적대적 리뷰 2026-10-02 H-1). 그래서 위치만 받고 **원문은 직접 읽는다.**
    lines = sorted(block.lines)
    lo, hi = lines[0], lines[-1]
    try:
        source = ctx.read_source(Location(file=block.file, line_start=lo, line_end=hi))
    except (OSError, ValueError) as exc:
        # 위 `ctx.resolve_requests` 호출의 같은 가드 — 인덱스가 가리키는 파일이 대상 루트 밖이면
        # `read_source`(`safe_join`)가 `PathEscape`(`ValueError`)로 실패시킨다. 보조 조회 하나가
        # run 전체를 멈추면 안 된다.
        missing["file"] = block.file
        missing["lines"] = [lo, hi]
        missing["reason"] = (
            f"{block.file}:{lo}-{hi} 읽기가 예외로 실패했다: {type(exc).__name__}: {exc}"
        )
        return missing
    if not source:
        # 위치는 있는데 원문을 못 읽었다 — 인덱스와 디스크가 어긋난 것이고, 빈 `source`를
        # `found=True`로 내보내면 "찾았다"가 거짓이 된다.
        missing["file"] = block.file
        missing["lines"] = [lo, hi]
        missing["reason"] = f"{block.file}:{lo}-{hi}를 인덱스가 가리키는데 원문을 못 읽었다"
        return missing
    # 🔴 **직접 읽으므로 상한을 직접 건다.** resolver의 `max_chars`는 그쪽이 렌더할 때만 걸리고
    # `read_source`에는 안 걸린다 — 안 자르면 거대한 하네스 함수 하나가 프롬프트를 혼자 키운다.
    # **앞부분을 남긴다**: 진입점의 머리(인자·최소 길이 검사·첫 파서 호출)가 PoV가 쓸 것이고
    # 꼬리는 정리 코드다. 자른 사실은 `reason`에 남긴다(조용히 자르지 않는다 — §0).
    reason = ""
    if len(source) > _FUZZ_ENTRYPOINT_CHARS:
        reason = (
            f"원문 {len(source)}자를 앞 {_FUZZ_ENTRYPOINT_CHARS}자로 잘랐다 — 뒤가 빠졌다"
        )
        source = source[:_FUZZ_ENTRYPOINT_CHARS]
    return {
        "found": True,
        "name": block.name,
        "file": block.file,
        # resolver가 준 줄은 **블록이 걸친 줄 집합**이라 범위로 줄인다 — 모델이 쓸 것은 범위다.
        "lines": [lo, hi],
        "source": source,
        "reason": reason,
    }


class _Once:
    """콜러블을 한 번만 부르고 그 결과를 들고 있는다 — 두 역할이 **같은 문자열**을 봐야 할 때.

    `_step_auditor`가 `_auditor_source`를 이걸로 감싸 Auditor에게 주고, 같은 객체를
    `state.finding_sources`에 넣어 8단계 PoV가 다시 꺼내 쓴다. 다시 부르면 안 되는 이유가 둘이다:
    ① `_auditor_source`가 `auditor_source` trace 이벤트를 **낸다** — 두 번 내면 장부에 같은 Auditor
    호출이 둘로 보인다(§0 — 센 것과 안 센 것을 섞지 않는다). ② 인덱스 조회(`symbols_for`·
    `callees_of`·`call_sites_of`)를 다시 돌려 공짜가 아니다.

    ⚠ **`partial`로는 안 된다** — 콜러블이기만 하면 노드가 매 호출마다 다시 돈다.
    """

    __slots__ = ("_call", "_text")

    def __init__(self, call: Callable[[], str]) -> None:
        self._call = call
        self._text: str | None = None

    def __call__(self) -> str:
        if self._text is None:
            self._text = self._call()
        return self._text


# 캡슐 미스로 pov가 루프에 **못 들어간** 사유. `PovStopReason` 밖인 이유는 그 enum이 루프의
# 정지 사유이고 이것은 그 앞에서 갈리기 때문이다. 한 이름으로 두는 것은 `POV_SKIPPED` 이벤트의
# `reason`과 `_Accumulator.pov_results`의 값이 **같은 철자**라야 사유별 집계를 trace와 대조할
# 수 있어서다 — 리터럴을 두 자리에 적으면 한쪽만 고치는 변이를 막는 것이 사람뿐이다.
# 🔴 **그 대조가 아래 셋에는 성립하지 않는다.** `submitted_without_poc`·`submitted_unpersisted`는
# trace에 없는 철자다 — `_pov_finish`가 그 갈래에도 `reason=submitted`를 낸다. `aborted`는 trace에
# 이벤트를 아예 안 낸다(`_run_pov`의 `finally`가 계수기에만 적는다). 즉 계수기 어휘 **열 중
# 일곱만** trace와 철자가 같다(enum 여섯 + `capsule_missing`). 경고문이 그 사실을 적어 두는 이유가
# 이것이고, 철자 자체는 `test_the_counter_vocabulary_spellings_are_pinned`가 리터럴로 묶는다.
_POV_CAPSULE_MISSING = "capsule_missing"

# 루프가 `SUBMITTED`로 끝났는데 **조립된 PoC가 없는**(`poc is None`) 갈래. 모델이 gen을 한 번도
# 안 하고 submit하면(hop0에서 바로든 inspect만 하다가든) 이렇게 된다 — `_pov_finish`는 그때
# `POV_GENERATED`가 아니라 `POV_SKIPPED`(`reason=submitted`)를 낸다.
# 🔴 사유를 갈라 두는 이유: 안 가르면 `submitted`가 **PoC 있는 것과 없는 것을 같이 덮어서**
# 계수기만 보고 「최종 PoC 0건」을 말할 수 없다. 그 run은 PoC가 0건인데 경고가 안 났다(미탐).
# `POV_GENERATED`가 **난** submit은 `submitted`(사이드카가 써졌거나 쓰는 배선이 아니다)와
# `submitted_unpersisted`(쓰기가 실패했다)로 갈리고, **안 난** submit이 이 갈래다.
_POV_SUBMITTED_WITHOUT_POC = "submitted_without_poc"


# 루프가 `SUBMITTED`로 끝나고 PoC도 있었는데 **사이드카로 남지 못한** 갈래 — 0바이트 PoC(`b""`)도
# 쓰기가 실패하면 여기다. `_pov_finish`가 `ctx.pocs.write`의 `OSError`를 삼켜 이벤트에
# `poc_write_error`만 싣고 `ref`를 `None`으로 낸다(디스크 꽉 찼을 때·읽기 전용 run 디렉터리·
# Windows 260자 초과). 🔴 그때 바이트는 메모리에만 있다가 사라지고 trace에는 sha256만 남는다 —
# **회수 불가다.**
# 🔴 이것을 `submitted`로 세면 장부가 「최종 PoC 있음」이라 적는데 **파일이 없다.** 적대적 리뷰가
# `pov_poc`를 일반 파일로 막아 재현했다(`pov_generated` 2건 전부 `poc_write_error`인데 리포트는
# `submitted 2건`, 경고 0건). 사유로 가르는 이유가 그것이다.
_POV_SUBMITTED_UNPERSISTED = "submitted_unpersisted"

# 루프가 **흡수되지 않은 예외로 끊긴** 갈래. `_run_pov`의 `finally`가 아무도 사유를 안 적었을
# 때만 넣는다(`setdefault`). 이것이 없으면 그 finding이 계수기에서 **사라져** 「출구마다 항목이
# 정확히 하나」가 깨진다 — 적대적 리뷰가 설정 오타(`PromptError`)와 잠긴 `state.db`
# (`sqlite3.OperationalError`)로 재현했다.
# ⚠ **이 항목이 어디까지 닿는지는 예외 형이 정한다.** `run_pipeline`이 받는 형(`BudgetExhausted`·
# `ModelSwapError`·`ModelGatewayError`·`SchemaParseError`)이면 run이 거기서 끝나도 경고와 보고서가
# 이 항목을 싣는다. 그 밖의 형은 경고·보고서를 짓기 **전에** run을 떠난다 — `Exception`이면 S0
# 방어선이 매니페스트만 쓰고 다시 던지고(`finish_run`·`write_manifest`는 PoV 값을 안 받는다),
# 아니면(`KeyboardInterrupt` 등) 그것도 없다. 어느 쪽이든 `aborted`는 `state.pov_results`에만
# 남고 다른 어디에도 안 닿는다.
_POV_ABORTED = "aborted"


def _pov_result(
    reason: PovStopReason, poc: bytes | None, ref: str | None, *, persisting: bool
) -> str:
    """계수기에 적을 사유 — `SUBMITTED`만 **PoC 유무와 회수 가능성**으로 갈린다.

    나머지 사유는 PoC가 중간까지 나왔어도 **최종**이 아니다(`_pov_finish`가 `POV_GENERATED`를
    `SUBMITTED`이고 PoC가 있을 때만 낸다). 그래서 사유를 그대로 쓴다.

    `persisting`은 **이 run이 사이드카를 쓰는 배선인가**다(`ctx.pocs is not None`). 단위
    테스트처럼 배선이 없는 run은 `ref`가 `None`인 것이 정상이라 미회수로 세지 않는다 —
    그것까지 세면 「안 쓰는 run」과 「쓰려다 실패한 run」이 한 사유로 뭉친다.

    🔴 **빈 바이트열(`b""`)은 PoC 「있음」이다 — `poc is None`으로 가르고 `not poc`로 가르지
    않는다.** `repeat count=0`이면 `assemble_poc`가 `b""`를 내는데, 이 저장소의 입장은
    *"0바이트 PoC도 **파일이다** … libFuzzer 입력으로는 **유효한 답**"*이고
    (`tests/test_poc_log.py:181-186`) `_pov_finish`도 그것을 **쓴다**(`:528-535`가 핀). 그
    두 docstring이 **참값 검사(`if poc:`)가 변이로 살아남았던 자리**라고 적는다.
    ⚠ 적대적 리뷰 셋 중 둘이 `b""`를 결함으로 올렸고 나는 한 번 `not poc`로 고쳤다 — 그
    파일을 **열어 본** 리뷰만 반례가 아니라고 판정했다(CLAUDE.md §2.1). 여기를 참값 검사로
    바꾸면 사이드카에는 파일이 있는데 계수기는 「없음」이라 적어 **둘이 어긋난다**.
    """
    if reason is not PovStopReason.SUBMITTED:
        return reason.value
    if poc is None:
        return _POV_SUBMITTED_WITHOUT_POC
    if persisting and ref is None:
        return _POV_SUBMITTED_UNPERSISTED
    return reason.value


def _pov_finish_and_record(
    ctx: RunContext,
    state: _Accumulator,
    finding: Finding,
    reason: PovStopReason,
    poc: bytes | None,
    *,
    terminal: Mapping[str, TraceValue] | None = None,
) -> tuple[bytes | None, PovStopReason]:
    """루프 종단 + 계수기 기록. 반환은 `_run_pov_hops`가 쥔 **2-튜플 그대로**다.

    🔴 **기록이 여기 있는 이유**: 회수 가능성을 가르려면 `_pov_finish`의 3번째 반환값 `ref`가
    필요한데, `_run_pov_hops`의 반환은 2-튜플이라 `ref`를 `_run_pov`까지 못 올린다. 두 함수의
    반환형은 테스트가 쥐고 있어 못 넓힌다(`_run_pov_hops` 2-튜플 · `_pov_finish` 4-튜플 —
    `test_the_pov_loop_return_shapes_and_signatures_stay_put`). 그래서 `ref`가 아직 손에 있는 이
    자리에서 적는다 — `_run_pov_hops`의 네 출구(흡수된 예외·submit·반복·`max_hop`)가 `_pov_finish`를
    직접 부르지 않고 이 함수를 부른다.
    """
    poc_out, reason_out, ref, _digest = _pov_finish(ctx, finding, reason, poc, terminal=terminal)
    state.pov_results[finding.finding_id] = _pov_result(
        reason_out, poc_out, ref, persisting=ctx.pocs is not None
    )
    return poc_out, reason_out


def _pov_shortfall_warning(results: Mapping[str, str]) -> str | None:
    """PoV가 **회수 가능한 최종 PoC를 0건** 냈을 때의 run 경고. 아니면 `None` (이슈 #357·#358).

    사유별 수를 **같은 문장에** 싣는다 — "0이다"만 적으면 **왜** 0인지가 여전히 trace에만 있고,
    그러면 이 경고가 막으려는 것의 절반만 성립한다.

    **말하는 명제는 「`run_dir`에서 꺼낼 수 있는 최종 PoC가 0건」이다.** `submitted`로 세는
    조건이 셋 다 참일 때뿐이라 그렇다 — `SUBMITTED`로 끝났고, 조립된 PoC가 있고(`poc is not None`.
    빈 바이트열도 있음이다), 사이드카가 실제로 **써졌다**(사이드카를 안 쓰는 배선의 run은 이
    조건을 안 본다 — `_pov_result`의 `persisting`). 어긋난 조건은 각자 사유를 받는다:
    `SUBMITTED`가 아니면 정지 사유 그대로 · PoC가 없으면 `submitted_without_poc`(gen 없이
    submit) · 쓰기가 실패했으면 `submitted_unpersisted`.

    ⚠ **전에 두 번 과장했다.** ① 사유만 세던 판은 gen 없는 submit을 `submitted`로 적어
    **미탐**이 났다. ② 그것을 닫은 판은 *"「최종 PoC 0건」을 정확히 말한다"*고 적었는데
    **쓰기 실패 갈래에서 거짓**이었다(적대적 리뷰가 `pov_poc`를 막아 재현했다). 둘 다 필드를
    늘리지 않고 **사유 어휘로** 닫았다.

    ⚠ **여전히 말하지 않는 것 둘.**
    ① 예외가 올라가도 `finally`가 사유를 **먼저** 넣는다 — 다만 그 기록이 보고서까지 가는지는
    `run_pipeline`이 그 형을 받는지에 달렸다. `BudgetExhausted`·`ModelSwapError`·
    `ModelGatewayError`·`SchemaParseError`는 받아 경고를 달고 보고서를 내므로 사유도 같이 실리고,
    그 밖의 형은 run과 함께 보고서도 잃는다.
    ② **PoC가 「터지는가」는 아무것도 안 말한다.** 여기서 센 것은 바이트의 존재와 회수
    가능성뿐이다. 그 판정은 오라클이 낸다(범위 밖).

    ⏵ 2026-10-11 — 빈 맵 중 「좌석은 있는데 대상 0건」은 `pov_pool_empty`가 따로 말한다
    (이 함수는 그대로 `None`을 돌려준다).
    """
    # 빈 맵은 「`_run_pov`가 한 번도 사유를 못 적었다」다. 이유는 셋이고 이 맵만으로는 못 가른다:
    # ① 8단계 앞에서 멈췄다 ② `_pov_targets`가 0건이라 `_run_pov`가 안 불렸다(VALIDATED가 0건이고
    # `pov_attempt_top_n`이 0이거나, 양수인데 Critic이 밀어 내지 않은 추가 대상이 0건이다)
    # ③ 프로필에 pov
    # 좌석이 없다(`REQUIRED_ROLES`에 pov가 없어 스키마상 합법이다. 다만 지금 커밋된 프로필은 전부
    # 좌석을 든다 — 2026-10-03 `config/profiles.yaml`을 `load_config`로 확인했다). 「좌석이 있는데
    # 대상이 0건」을 따로 말하려면 필드가 하나 더 필요하다(범위 밖 — 이 경고의 명제는 「PoV를
    # 시도한 run」에 한정이다).
    # ⏵ 2026-10-11 — `_Accumulator.pov_pool_empty`가 ② 갈래(좌석 있음 · 대상 0건)를 따로 말한다
    # (`_pov_pool_empty_reason`). ①③은 이 맵으로도 여전히 못 가른다.
    if not results:
        return None
    stops = Counter(results.values())
    if stops[PovStopReason.SUBMITTED.value] == 0:
        parts = " · ".join(f"{reason} {count}건" for reason, count in sorted(stops.items()))
        return (
            f"PoV 시도 {len(results)}건에서 회수 가능한 최종 PoC가 0건이다: {parts}. "
            f"해당 finding의 홉·종단은 trace의 pov_hop·pov_generated·pov_skipped에서 찾는다"
            f"(aborted는 종단 이벤트가 없고, trace의 reason은 submitted까지만 갈라 "
            f"이 어휘와 철자가 다르다)"
        )
    return None


def _run_pov(ctx: RunContext, state: _Accumulator, finding: Finding) -> None:
    """8단계 PoV — Finding에서 PoC를 만드는 모델-주도 툴 루프를 돌린다 (추가형·b-1).

    `validate.run`과 **나란히** 도는 추가 경로다. finding의 상태는 이미 `validate.run`이 정했으므로
    여기서는 상태를 바꾸지 않는다. 캡슐은 Auditor가 물려 준 `state.finding_capsules`에서 연다.

    **캡슐 미스는 finding을 죽이지 않는다** — `pov_skipped`로 남기고 넘어간다(추가형이라 상태는
    `validate.run`이 이미 정했다, #296과 같은 결). 캡슐이 있으면 `_run_pov_hops`가
    gen→도구→되먹임→submit 루프를 돌려 PoC를 만든다 — 홉/종단 이벤트와 예외 흡수(서버 쪽 사건은
    올려 보냄)는 루프가 낸다. 최종 후보 PoC(보통 마지막 `gen`)를 run 산출물 사이드카로 남기는
    것도 루프 종단(`_pov_finish`)이 한다(#355).
    ⏵ **2026-10-09 (ADR 0014) — PoC를 도구에 먹이는 쪽이 섰다**(이슈 #513). `poc` 도구가 과제
    자신의 `-vul` 이미지를 돌린다. **E4 게이트 전이는 여전히 별건이다** — `p4`가 종료 코드까지
    보고(#232) 그 run이 실제로 E4를 내는지는 미실측이다.

    **어느 출구로 끝나도 `state.pov_results`에 이 finding의 사유를 하나 남긴다**(이슈 #357·#358) —
    캡슐 미스 갈래 포함. 그 전에는 PoC가 0건인 run에서 **왜 0인지가 trace에만** 있었고, pov가
    추가형이라 상태를 안 바꾸므로 Finding이 멀쩡히 찍힌 채 산출만 조용히 0이 됐다.
    ⚠ 예외가 올라가도 `finally`가 사유를 **먼저** 넣는다 — 다만 그 기록이 보고서까지 가는지는
    `run_pipeline`이 그 형을 받는지에 달렸다. `BudgetExhausted`·`ModelSwapError`·
    `ModelGatewayError`·`SchemaParseError`는 받아 경고를 달고 보고서를 내므로 사유도 같이 실리고,
    그 밖의 형은 run과 함께 보고서도 잃는다.

    **PoC는 채점기로 나간다**(#386 — `--oracle-submit`일 때만). v3 이상 루프의 중간 `gen`마다
    (마지막 홉의 gen은 빼고) `_run_pov_hops`가 `designatable=False`로 `vul` 다리에 내보내
    원시 종료 코드를 다음 홉에 싣는다.
    루프가 `SUBMITTED`로 끝나 바이트가 있으면 이 함수가 같은 바이트를 `designatable=True`로 내어
    모델의 최종 지정 신호를 기록한다. `fix` 다리와 판정은 8단계 끝에서 run당 한 번, 지정한 하나에만
    돈다(`_finalize_oracle` — 후보가 여럿이면 같은 바이트를 접은 뒤 ⓪Critic 판정 ①내부 근거 등급
    ②`confidence` ③작은 바이트 ④PoC 해시의 전순서로 가른다, `_designate_final`).
    `MAX_HOP`·`REPEAT_SPEC`·`PARSE_FAILED`로 끝난 루프의 마지막 바이트는 최종 지정 후보가 아니지만,
    그 전에 완료된 중간 gen 제출은 장부에 남는다. 그 전에는 이 함수가 루프 반환값을 **버렸고**,
    바이트가 채점기로 갈 길이 없었다(`pov_poc/NNNN.poc`은 사람이 회수하는 파일이었다, #386 ①).
    """
    capsule = state.finding_capsules.get(finding.finding_id)
    if capsule is None:
        state.pov_results[finding.finding_id] = _POV_CAPSULE_MISSING
        ctx.trace.emit(
            EventKind.POV_SKIPPED,
            stage=Stage.VALIDATE,
            subject_kind="finding",
            subject_id=finding.finding_id,
            reason=_POV_CAPSULE_MISSING,
        )
        return
    # 🔴 `finally`로 **빈칸을 메운다.** `_run_pov_hops`의 `except`는
    # `(ModelGatewayError, SchemaParseError)`뿐이고, 그 밖의 형이 올라가면
    # `_pov_finish_and_record`가 안 불려 사유가 **0건**이었다.
    # 적대적 리뷰가 설정만으로 닿는 둘을 재현했다 — pov 좌석 `prompt_template_version` 오타의
    # `PromptError`(`run()`은 `_check_prompts`를 안 부르고 `doctor()`만 부른다)와 잠긴 `state.db`의
    # `sqlite3.OperationalError`(`call_tool`의 `store.put`은 `try` 밖이고 `OSError`가 아니다).
    # ⏵ 2026-10-10 도구 호출 둘레의 `except BudgetExhausted`(#394)는 홉 이벤트를 낸 뒤 그대로
    # 올리므로 사유 0건은 여전하고 이 `finally`가 메운다.
    # `setdefault`라 정상 출구가 적은 사유를 덮지 않는다 — 아무도 안 적었을 때만 들어간다.
    # 채점기 제출(#386)은 이 `try` 안이다: `_submit_vul_to_oracle`이 `Exception`을 스스로 흡수하므로
    # `finally`가 루프가 이미 적은 사유(`submitted`)를 덮을 일이 없고, 올라가는 인터럽트가 있어도
    # 사유는 남는다.
    try:
        poc, reason = _run_pov_hops(ctx, state, finding, capsule)
        if reason is PovStopReason.SUBMITTED and poc is not None:
            _submit_vul_to_oracle(
                ctx, state, finding, poc, designatable=True, in_validate_span=False
            )
    finally:
        state.pov_results.setdefault(finding.finding_id, _POV_ABORTED)


def _submit_vul_to_oracle(
    ctx: RunContext,
    state: _Accumulator,
    finding: Finding,
    poc: bytes,
    *,
    designatable: bool,
    in_validate_span: bool,
) -> str:
    """PoC를 `vul` 다리에 내보내고 다음 홉 관측 한 줄을 돌려준다 (#386).

    채점기가 없으면 부수효과 없이 `oracle_vul=unavailable`을 돌려준다. `designatable=False`인 중간
    gen은 원시 종료 코드를 조회하고, `True`인 모델 submit만 최종 지정 후보가 된다.

    **`fix` 다리는 여기서 돌지 않는다.** 저자 FAQ는 fix 다리를 「최종 PoC 한 개」에만 돌리라고
    적는데 클라이언트는 그것을 강제하지 않는다. 각 vul 제출마다 `vul`·`fix`를 다 내면
    (`run_round_trip`) 비최종 PoC에도 `-fix` 이미지가 돈다. 그래서 각 제출에는 `vul`만 내고
    최종은 `_finalize_oracle`이 지정한다.

    **추가형이다** — finding 상태를 바꾸지 않고 Evidence도 만들지 않는다(채점은 저장소 밖 기준이고
    내부 등급 E4는 실행 로그를 따로 요구한다, #232 · `interfaces/oracle.py`). 기록만 남긴다.

    **예외를 흡수한다** — 채점기는 저장소 밖 서비스이고 그 사건이 run을 멈추면 앞 단계 산출물까지
    잃는다(`_run_pov`의 추가형 계약 그대로). `Exception`만 흡수한다 — TERM·HUP(`SystemExit`)과
    Ctrl-C는 올라간다. 다만 `oracle_failed`로 **사유를 남긴다** — 그래야 「제출했는데 실패했다」가
    조용히 「미제출」(이벤트 부재)과 같아지지 않는다.

    **도커 왕복을 단계 시간에 넣는다** — 이 호출은 `Stage.VALIDATE` 구간(`stage_start`~`stage_end`)
    안에서 돌고 이벤트가 `wall_ms`를 단다. 구간 밖에서 내면 이 이벤트가 8단계 `stage_end` 뒤에 나가
    왕복이 단계 시간 장부에서 빠진다. 값은 장부(`state.oracle_ledger`)와 사이드카에 쌓이고,
    trace에는 결정론 필드만 간다(분류는 `oracle_log` 모듈 docstring).

    **이미 그 구간 안이면 다시 열지 않는다(`in_validate_span=True`, #518)** — 중간 gen은
    `_run_pov_hops`가 연 8단계 구간 안에서 불린다. 여기서 또 열면 `StageSpan`이 중첩을 몰라
    `stage_start`·`stage_end` 쌍이 겹치고, 안쪽 `wall_ms`가 바깥 구간에 이미 든 시간을 한 번 더
    세 8단계·run 합계 latency(`trace.py` 지표표)가 부푼다. 평면으로 구간을 읽는 쪽은 안쪽
    `stage_end` 뒤의 `pov_hop`을 구간 밖으로 읽는다. 기본값을 두지 않는다 — 호출부가 자기가
    구간 안인지를 정한다(`designatable`과 같은 이유).
    """
    if ctx.oracle is None:
        return "oracle_vul=unavailable"
    with contextlib.nullcontext() if in_validate_span else ctx.stage(Stage.VALIDATE):
        started = ctx.monotonic_ms()
        if not poc:
            attempt = oracle_log.vul_empty(finding.finding_id, poc, designatable=designatable)
            ctx.trace.emit(
                EventKind.ORACLE_FAILED,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                wall_ms=0,
                leg="vul",
                designatable=designatable,
                poc_sha256=attempt.poc_sha256,
                poc_bytes=0,
                error=attempt.error,
                reason=oracle_log.ERROR_EMPTY_POC,
            )
            state.oracle_ledger.add_attempt(attempt)
            return "oracle_vul=unavailable"
        try:
            submission = ctx.oracle.submit_vul(poc)
        except Exception as exc:  # 저장소 밖 서비스 — 사유를 남기고 run은 계속한다.
            attempt = oracle_log.vul_failed(
                finding.finding_id,
                poc,
                exc,
                max(0, ctx.monotonic_ms() - started),
                designatable=designatable,
            )
            ctx.trace.emit(
                EventKind.ORACLE_FAILED,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                wall_ms=attempt.wall_ms,
                leg="vul",
                designatable=designatable,
                poc_sha256=attempt.poc_sha256,
                poc_bytes=attempt.poc_bytes,
                error=attempt.error,
            )
            observation = "oracle_vul=unavailable"
        else:
            exit_code: int | None = None
            query_error: Exception | None = None
            if not designatable:
                try:
                    exit_code = ctx.oracle.query_vul(poc)
                except Exception as exc:  # 조회도 추가형 — 실패를 기록하고 다음 모델 홉은 계속한다.
                    query_error = exc
                    ctx.trace.emit(
                        EventKind.ORACLE_FAILED,
                        stage=Stage.VALIDATE,
                        subject_kind="finding",
                        subject_id=finding.finding_id,
                        wall_ms=max(0, ctx.monotonic_ms() - started),
                        leg="query_vul",
                        designatable=designatable,
                        poc_sha256=hashlib.sha256(poc).hexdigest(),
                        poc_bytes=len(poc),
                        error=type(exc).__name__,
                    )
            # 조회가 **예외 없이** 비어 돌아온 것도 기록한다 - 제출이 성공한 직후의 빈 답은 이상이다
            # (`finalize`는 행이 없으면 예외를 낸다). 안 적으면 서버 설정이 틀려도 모든 gen이
            # 조용히 `unavailable`을 받는다.
            no_verdict = not designatable and query_error is None and exit_code is None
            attempt = oracle_log.vul_ok(
                finding.finding_id,
                poc,
                submission,
                max(0, ctx.monotonic_ms() - started),
                designatable=designatable,
                observation_exit_code=exit_code,
                observation_error=(
                    oracle_log.OBSERVATION_NO_VERDICT
                    if no_verdict
                    else None
                    if query_error is None
                    else type(query_error).__name__
                ),
                observation_error_detail=(
                    "" if query_error is None else oracle_log.clip_error(query_error)
                ),
            )
            ctx.trace.emit(
                EventKind.ORACLE_SUBMITTED,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                wall_ms=attempt.wall_ms,
                leg="vul",
                designatable=designatable,
                task_id=submission.task_id,
                poc_sha256=attempt.poc_sha256,
                poc_bytes=attempt.poc_bytes,
            )
            observation = (
                f"oracle_vul exit_code={exit_code}"
                if exit_code is not None
                else "oracle_vul=unavailable"
            )
            # 서버 캐시 hit(같은 바이트를 앞서 냈다 - 이슈 #385)면 그 코드는 **이번 환경의 새
            # 측정이 아닐 수 있다.** 모델이 새 값으로 읽지 않게 표시한다.
            if exit_code is not None and submission.cached:
                observation += " cached=true"
        state.oracle_ledger.add_attempt(attempt)
        return observation


def _grades_by_finding(state: _Accumulator) -> dict[str, EvidenceGrade]:
    """Finding별 내부 근거 등급. **순수 함수** — `gate_eval`을 재발행하지 않는다.

    **두 자리가 같은 값을 봐야 해서 함수로 뽑았다.** 최종 PoC 지정(`_designate_final`)이 리포트보다
    **먼저** 돈다 — 8단계 끝의 `_finalize_oracle`이고 리포트는 그 뒤다. 그래서 한 자리에서 계산해
    넘길 수 없고, 둘이 이것을 부른다. 등급이 두 자리에서 갈리면 리포트가 보여주는 등급과 지정의
    근거가 달라진다.

    `ctx.evaluate_gate`가 아니라 `gate.evaluate`를 쓰는 이유는 그쪽이 `gate_eval`을 재발행해 제거율
    분모를 이중 계상하기 때문이다. `evidences_for`로 이 Finding이 참조하는 근거만 거르는 이유는
    6·7·8단계 게이트가 전부 그렇게 하기 때문이고, 안 거르면 artifact 단위 근거 묶음의 다른 Finding
    근거로 등급이 오른다.

    🔴 **입력은 `state`의 Finding·근거·판정·누락뿐이고, 근거에는 8단계가 만든 런타임 근거가 든다**
    (`_analyze`가 `validate.run`의 `produced`를 모은다 — #460 리뷰). 그래서 여기서 나는 등급은
    8단계 `gate_eval`이 낸 등급과 **같다.** 그 근거를 안 모으던 때는 후보가 전부 E3로 읽혀 지정이
    어느 run에서나 등급 동점이었고, 그때의 규칙은 동점에서 거부였으므로 점수가 안 났다(그 거부는
    ⏵ 2026-10-04에 사슬로 바뀌었다 — `_designate_final`).
    채점기 결과(`OracleLedger`·`VulSubmission`)는 **이 입력에 안
    닿는다** — 채점기 값이 등급을 올리면 지정이 채점기 값으로 고르는 것과 같아진다.

    ⚠ **실도구 run에서 E4가 나는지는 이 함수의 몫이 아니다.** ⏵ 2026-10-09 (ADR 0014) — 컨테이너
    러너의 `poc`는 PoC를 과제 이미지에 먹이므로 그 도구 실행은 PoC의 함수다. 그래도 **후보 등급이
    갈릴지는 미실측**이다 — 그 뒤로 돈 run이 0건이다.
    """
    by_cand = {c.candidate_id: c.location for c in state.candidates}
    return {
        f.finding_id: gate.evaluate(
            f,
            gate.evidences_for(f, state.evidences.get(f.finding_id, ())),
            state.decisions.get(f.finding_id, ()),
            state.missings.get(f.finding_id, ()),
            candidate_location=by_cand.get(f.candidate_id),
        ).grade
        for f in state.findings.values()
    }


def _confidences_by_finding(state: _Accumulator) -> dict[str, float]:
    """Finding별 `confidence`. **`_grades_by_finding`과 키 집합이 같다** — 둘 다 `state.findings`를
    전수로 돈다. 최종 PoC 지정이 이 둘과 `_verdict_ranks_by_finding`(같은 전수)을 나란히 읽고,
    셋 중 어느 하나에만 없는 `finding_id`는 지정이 `grade_unknown`으로 거부한다(`_designate_final`).

    ⚠ **`confidence`는 모델이 적은 수다** — 게이트가 계산한 등급과 다른 종류의 값이고, 그래서
    지정 사슬에서 등급 **뒤**에 온다. 채점기 응답에서는 안 나온다(Auditor가 Finding에 적는다) —
    그 불변식은 `test_the_oracle_leg_leaves_the_graded_evidences_alone`이 문다.
    """
    return {f.finding_id: f.confidence for f in state.findings.values()}


def _verdict_ranks_by_finding(state: _Accumulator) -> dict[str, int]:
    """Finding별 Critic 판정 계단 - **0 = uphold · 1 = 판정 없음(미경유) ·
    2 = 보류(`need_evidence`) · 3 = 반박(`rebut`)**이다. 이슈 #604 (b)가 제안한 순서
    (`uphold > 미경유 > need_evidence`) 그대로고 반박이 맨 뒤다. 추가 풀 정렬(`_pov_targets`)과
    최종 지정(`_designation_key`)이 **같은 값**을 읽는다. 판정은 `gate.normalize` 뒤 값이고, 키
    집합은 `_grades_by_finding`과 같다(`state.findings` 전수).

    ⏵ 2026-10-12 - 앞 판은 미경유를 uphold와 같은 0에 두었다(@sumin0218 리뷰): 미경유는 6단계 E0
    `QUEUED`와 `critic_top_n` 절단분이라 Critic이 **보증한** 것이 아닌데 uphold와 동점이 돼
    등급이 가렸다.

    **반박(3)은 지금 정렬에 닿지 않는 값이다** - `_pov_targets`가 `_critic_set_aside`로 반박을
    풀에서 먼저 빼고, 최종 후보는 그 풀의 PoC뿐이다. 그래도 값을 3으로 둔 것은 풀 필터가 풀리거나
    다른 호출처가 생겨도 반박이 보류 **앞**에 서지 않게 하는 방어 심층이다(`test_nodes.py`가 값을
    문다).

    **uphold를 먼저 본다.** 판정은 Finding당 1-튜플 가정이다(`_critic_set_aside` docstring과 같다).
    둘 이상이 되면 `gate.evaluate`의 `upheld`가 `any(normalize == UPHOLD)`라 uphold가 하나라도
    있으면 게이트는 그 Finding을 uphold로 읽으므로 이 함수도 0으로 맞춘다 - 다음은 반박, 그다음
    보류다. 지금은 `state.decisions[...] = (decision,)` 한 자리 대입이라 닿지 않는다."""
    ranks: dict[str, int] = {}
    for f in state.findings.values():
        verdicts = {gate.normalize(d) for d in state.decisions.get(f.finding_id, ())}
        ranks[f.finding_id] = (
            0
            if Verdict.UPHOLD in verdicts
            else 3
            if Verdict.REBUT in verdicts
            else 2
            if Verdict.NEED_EVIDENCE in verdicts
            else 1
        )
    return ranks


# 최종 PoC 지정 사슬의 단계 이름. **`_designation_key`가 내는 튜플과 자리가 하나씩 맞다** — 앞에서
# 보아 처음 값이 갈리는 자리가 그 run의 `designation`이다. 이름과 키의 길이가 어긋나면
# `zip(..., strict=True)`가 터진다(키를 늘리고 이름을 안 붙이는 변이를 그 자리가 막는다).
_DESIGNATION_CHAIN = (
    oracle_log.DESIGNATION_CRITIC_VERDICT,
    oracle_log.DESIGNATION_TOP_GRADE,
    oracle_log.DESIGNATION_TOP_CONFIDENCE,
    oracle_log.DESIGNATION_SMALLEST_POC,
    oracle_log.DESIGNATION_POC_DIGEST,
)


def _designation_key(
    attempt: oracle_log.VulAttempt,
    grades: Mapping[str, EvidenceGrade],
    confidences: Mapping[str, float],
    verdicts: Mapping[str, int],
) -> tuple[int, int, float, int, str]:
    """최종 PoC 정렬 키 — **작을수록 먼저**다. 읽는 것은 하네스가 만든 값뿐이다.

    ⓪Critic 판정(uphold 0 < 미경유 1 < 보류 2 < 반박 3) ①등급 내림차순 ②`confidence` 내림차순
    ③PoC 바이트 **작은** 쪽 ④PoC 해시 사전순. ⏵ 2026-10-11 — ⓪을 붙였다: 보류 Finding이 풀에 들면서
    「모르겠다」고 한 PoC가 등급·`confidence`로 uphold PoC를 밀어낼 수 있어서다(이슈 #604).
    판정 계단이 등급보다 앞서므로 **등급이 낮은 PoC가 계단이 뒤인 PoC를 이긴다**(E0 미경유 >
    E4 보류, E2 uphold > E3 미경유) — 의도한 것이다. ⏵ 2026-10-12 — 미경유를 uphold와 같은
    계단에서 떼어 그 사이에 세웠다(@sumin0218 리뷰, `_verdict_ranks_by_finding`).

    🔴 **④가 이 키를 전순서로 만든다.** 서로 다른 PoC는 해시가 다르므로 앞 셋이 전부 같아도
    갈린다 — 그래서 「동점이라 못 가렸다」는 분기가 없다. ④는 **내용**에서 나오는 값이고 자리
    (처음·마지막)가 아니다. 자리로 고르지 않는다는 규칙은 그대로다 — 같은 후보 묶음을 어떤
    순서로 넣어도 같은 PoC가 지정된다.

    ⚠ **④는 약한 근거다.** 거기까지 와서 갈린 run은 `designation`이 `poc_digest`로 찍힌다 —
    읽는 쪽이 「해시로 골랐다」를 알아야 한다(§0).
    """
    return (
        verdicts[attempt.finding_id],
        -gate.GRADE_ORDER.index(grades[attempt.finding_id]),
        -confidences[attempt.finding_id],
        attempt.poc_bytes,
        attempt.poc_sha256,
    )


def _designate_final(
    ledger: oracle_log.OracleLedger,
    grades: Mapping[str, EvidenceGrade],
    confidences: Mapping[str, float],
    verdicts: Mapping[str, int],
) -> tuple[oracle_log.VulAttempt | None, str, str | None]:
    """run당 한 번, `vul` 제출이 성공한 후보 중에서 최종 PoC를 지정한다 (#386).

    돌려주는 것은 `(지정한 시도, 지정 근거, None)`이거나 `(None, "", 지정 못 한 사유)`다.
    **넷으로 갈린다:**

    - **후보가 없다** → 지정하지 않는다(`no_poc`·`all_vul_submissions_failed`·`rate_limited` —
      모델 `submit`이 없던 것(중간 gen이 `vul`에 나갔어도 후보가 아니다 · #620), 낸 것이 다 실패한
      것, 한도(HTTP 429)가 전부 삼킨 것은 **서로 다른
      사건이다**, §0. 사유를 가르는 것은 `oracle_log.OracleLedger.none_reason`이고 이 함수는
      그것을 그대로 싣는다). `fix` 다리와 판정 조회는 0번이다.
    - **후보가 하나다** → 그 하나가 최종이다(`sole_candidate`). **이것은 위치 규칙이 아니다** —
      고를 후보가 하나뿐이라 선택이 없고, 순위도 자리(처음·마지막)도 쓰지 않는다. 「유일한
      것」이 「첫째」로 바뀌는 것이 아니다. `fix` 다리와 판정 조회는 정확히 한 번 돈다.
    - **등급·`confidence`·판정 계단을 모르는 후보가 있다** → 지정하지 않는다(`grade_unknown` —
      이름은 등급 미상에서 굳었지만 이 검사는 셋을 같이 본다). **방어 분기다** —
      후보의 `finding_id`는
      `_run_pov`가 `state.findings`에서 받은 것이고 `_grades_by_finding`·
      `_confidences_by_finding`이 그 전부를 세므로 현 러너로는 닿지 않는다(직접 호출 검사만 문다 —
      실영향은 없다).
    - **후보가 둘 이상이다** → `_designation_key`의 **전순서**로 하나를 고른다. 같은 바이트끼리
      먼저 접고(`poc_sha256`), 접은 뒤 남은 PoC가 하나면 `sole_poc`다 — 어느 후보를 지정해도
      **제출되는 바이트가 같아** 선택이 없다. 둘 이상 남으면 사슬의 어느 단계가 갈랐는지가
      `designation`이다(`critic_verdict`·`top_grade`·`top_confidence`·`smallest_poc`·`poc_digest`).
      `verdicts`는 Finding별 Critic 판정 계단이고(`_verdict_ranks_by_finding`) **필수 인자다** -
      기본값이 있으면 빠뜨린 호출처가 계단을 조용히 꺼 버린다(⏵ 2026-10-12, @sumin0218 리뷰).

    ⏵ **2026-10-04 — 동점 거부를 없앴다.** 2026-10-03 동결 규칙은 「내부 근거 등급 최댓값 ·
    동점이면 거부」였고 이 함수가 `grade_tie`를 냈다. 그 규칙이 실측에서 **점수를 0으로
    만들었다**: 후보 8건이 전부 **같은 PoC 바이트**였고(제출 8 = 고유 PoC 1 = DB 행 1) 등급이
    Finding 단위라 8건이 동점이었다. 어느 쪽을 지정해도 제출될 바이트가 같은데 거부한 것이다.
    고친 모양은 둘이다 — ① 같은 바이트는 **접는다**(선택이 없는 것을 선택 실패로 적지 않는다)
    ② 남은 동점은 거부가 아니라 **사슬의 다음 단계**로 넘긴다. 사슬의 끝(PoC 해시)이 전순서라
    `grade_tie`가 닿지 않는 분기가 되어 **사유 상수째 지웠다**(`oracle_log`).

    🔴 **이 함수는 채점기가 돌려준 값을 읽지 않는다.** 후보의 `submission`에는
    `response_exit_code` 같은 오라클 응답 값이 실려 **지정 시점에 손에 있는데도** 그렇다. 이것은
    **이 저장소의 결정이고 저자의 금지 문장이 아니다** — 저자 `FAQ.md`는 최종 답을 에이전트가
    정확히 하나 지정하라고 요구하고 any-of가 무차별 대입을 보상한다고 적을 뿐 고르는 방법을
    금지하지 않는다(조사 문서 1.3절 — docs/research/2026-09-29-cybergym-pov-design.md). 그 값으로
    후보를 가르면 후보를 채점기에 물어 고르는 best-of가 되어 읽지 않기로 했다. 읽는 것은
    `attempt.ok`(제출이 성공했나 — 후보 자격이고 판정이 아니다)와 **하네스가 만든 값**
    (Critic 판정 계단 · 내부 등급 · `confidence` · PoC 바이트 수 · PoC 해시)뿐이다. 내부 등급은
    하네스가 도구를 돌려 낸 근거(8단계 `produced`)에서 나오고 채점기 응답에서
    안 나온다(`_grades_by_finding`).

    `test_designation_does_not_read_oracle_fields`가 **`submission` 접근**(속성·`vars`·`repr`·
    `==`)을 분기마다(사슬이 갈린 자리 다섯 · 같은 바이트 · 등급·`confidence`·판정 계단 미상 ·
    후보 하나) 문다.
    ⚠ **못 무는 것:** `submission` 말고 `grades`·`confidences` 사전에 채점기 값이 섞이는 경로
    (러너 쪽 `test_the_oracle_leg_leaves_the_graded_evidences_alone`이 문다 — 한계는 그
    docstring), `verdicts` 사전(`_verdict_ranks_by_finding` 안에서 채점기 장부를 읽는 변이는
    지금 안 잡힌다 — 이 함수 밖의 값이라 함정이 못 본다)과 후보 0건(읽을 `submission`이 없다).

    「최종 지정 후보」는 모델이 `submit`했고 `vul` 제출도 **성공한** PoC다. 중간 gen은 판정 되먹임을
    위해 `vul`에 제출해도 후보가 아니며, 모델 submit의 제출이 실패하면 `vul` 쪽 결과가 없어 후보가
    아니다(그 run의 경고가 실패 건수를 알린다).

    ⏵ **2026-10-11 — 위 「읽지 않는다」는 하네스 코드를 묶는 제약이다.** 어느 gen이 후보가 되는지는
    모델의 `submit`이 정하고(`poc_sha256`으로 앞선 gen을 지명하거나, 안 적으면 마지막 gen) 그것은 이
    제약 밖이다 — 모델이 관측의 `oracle_vul` 줄을 읽고 고르는 일을 이 제약이 막지 않는다(council).
    """
    candidates = ledger.candidates
    if not candidates:
        return None, "", ledger.none_reason()
    if len(candidates) == 1:
        return candidates[0], oracle_log.DESIGNATION_SOLE_CANDIDATE, None
    # 등급·confidence·판정 계단 중 모르는 값이 있는 후보가 하나라도 있으면 거부한다 —
    # 모르는 값을 0으로 깔면 **없는 근거로** 나머지를 고르게 된다(CLAUDE.md §0: 안 잰 값은
    # 0이 아니라 미실측 표시다).
    if any(
        attempt.finding_id not in grades
        or attempt.finding_id not in confidences
        or attempt.finding_id not in verdicts
        for attempt in candidates
    ):
        return None, "", oracle_log.REASON_GRADE_UNKNOWN
    # `finding_id` 꼬리는 **사슬의 단계가 아니다** — 같은 바이트를 낸 후보가 여럿일 때(접기
    # 대상) 어느 `finding_id`로 기록할지를 입력 순서가 아니라 id로 정하려는 것뿐이다. 제출될
    # 바이트는 그 묶음 안에서 같으므로 이 꼬리가 PoC를 바꾸지 않는다. 그래서 `_DESIGNATION_CHAIN`
    # 에 이름이 없고 `zip(..., strict=True)`가 그 길이를 맞춰 둔다.
    ordered = sorted(
        candidates,
        key=lambda a: (*_designation_key(a, grades, confidences, verdicts), a.finding_id),
    )
    picked = ordered[0]
    # **같은 바이트를 접는 자리다.** 접어서 하나만 남으면 고를 것이 없다 — 지정이 어느 후보를
    # 가리키든 채점기에 나가는 바이트가 같다(`_score`가 내보내는 것은 `attempt.poc`다).
    if all(attempt.poc_sha256 == picked.poc_sha256 for attempt in ordered[1:]):
        return picked, oracle_log.DESIGNATION_SOLE_POC, None
    # 어느 단계가 갈랐나 — **다른 바이트를 든 가장 센 후보**와 견준다. 같은 바이트끼리의 차이는
    # 제출될 PoC를 바꾸지 않으므로 갈랐다고 적지 않는다.
    rival = next(a for a in ordered if a.poc_sha256 != picked.poc_sha256)
    decided = next(
        name
        for name, mine, theirs in zip(
            _DESIGNATION_CHAIN,
            _designation_key(picked, grades, confidences, verdicts),
            _designation_key(rival, grades, confidences, verdicts),
            strict=True,
        )
        if mine != theirs
    )
    return picked, decided, None


def _score(
    ctx: RunContext, oracle: Oracle, picked: oracle_log.VulAttempt, designation: str
) -> oracle_log.FinalAttempt:
    """지정한 하나에 `fix` 다리와 판정 조회를 돌린다. 저장소 밖 서비스라 예외는 흡수하고 남긴다."""
    # 지정되는 것은 vul 제출이 성공한 시도뿐이라 바이트와 제출 결과가 있다.
    assert picked.poc is not None and picked.submission is not None
    started = ctx.monotonic_ms()
    try:
        outcome = oracle.finalize(picked.poc, picked.submission)
    except Exception as exc:  # 사유를 남기고 run은 계속한다.
        return oracle_log.FinalAttempt(
            status=oracle_log.STATUS_FAILED,
            wall_ms=max(0, ctx.monotonic_ms() - started),
            designation=designation,
            attempt=picked,
            error=type(exc).__name__,
            error_detail=oracle_log.clip_error(exc),
        )
    return oracle_log.FinalAttempt(
        status=oracle_log.STATUS_SCORED,
        wall_ms=max(0, ctx.monotonic_ms() - started),
        designation=designation,
        attempt=picked,
        outcome=outcome,
    )


def _finalize_oracle(ctx: RunContext, state: _Accumulator) -> None:
    """채점기의 최종 단계 — **run당 한 번**, 후보 하나만 (#386). 채점기가 없으면 아무것도 안 한다.

    `_designate_final`이 최종 PoC를 지정하면 그 하나에만 `fix` 다리와 판정 조회를 돌린다. 점수는
    후보 N건의 any-of가 아니라 이 호출 하나다 — 그래서 `oracle_final`이 run당 1건이고
    `oracle_submitted`는 판정 값을 안 든다(`trace.py`). **지정된 후보가 없는 사건**
    (`no_poc`·`all_vul_submissions_failed`·`rate_limited`·`grade_unknown`)은 `status=none`이고,
    지정했는데 `finalize`가 예외로 끝난 `status=failed`와 다르다 — 같은 모양이면 「최종 PoC가
    없다」가 「최종 PoC가 실패했다」로 읽힌다(§0). **후보가 둘 이상이면 같은 바이트를 접은 뒤
    ⓪Critic 판정 ①등급 ②confidence ③작은 바이트 ④PoC 해시의 전순서로 하나를 고른다** — 동점 거부는
    2026-10-04에 없어졌고 그 사유(`grade_tie`)도 지웠다(`_designate_final`의 ⏵).

    **추가형이다** — 상태·Evidence를 안 건드리고 예외를 흡수한다(`_submit_vul_to_oracle`과 같은
    계약). 이 단계는 8단계가 **끝까지 돌았을 때** 불린다. 루프가 `BudgetExhausted`·
    `ModelGatewayError`로 끊겨도 오라클에 이미 낸 후보가 있거나(`candidates`) `pov_attempt_top_n`의
    추가 대상에서 끊겼으면 `_analyze`가 이 단계를 부른 뒤 예외를 올린다 — 앞 대상이 이미 낸 PoC의
    최종 지정을 잃지 않으려는 것이다(#532). `pov_attempt_top_n`이 8이라(`config/budgets.yaml`) 추가
    대상이 도는 것은 설정상 평상시다 — 이 경로는 드문 예외가 아니다(그 비율은 안 쟀다). 그 밖의
    중단은 불리지 않고 매니페스트의 `oracle.final`이 `not_reached`로 남는다.

    ⚠ **그 밖의 중단은 둘이고 축이 다르다.** ① 그 두 예외 **밖의** 예외로 끊긴 run(예외 종류) ②
    그 두 예외라도 낸 후보 없이 `validated` 대상에서 끊긴 run(위치·후보 유무). 핀은
    `tests/test_oracle_wiring.py`다.
    ⚠ **흡수하는 것은 둘이다:** `oracle.finalize`의 `Exception`(`_score`)과 사이드카 쓰기의
    `OSError`(`oracle_log` 장부). 지정(`_designate_final`)·`trace.emit`은 안 감쌌고 던질 실경로를
    못 찾았다(미확인) — 그래서 `_analyze`가 이 함수의 예외를 원 예외의 `add_note`로 붙여 원 예외를
    올리고, `run_pipeline`의 예산·게이트웨이 갈래가 그 노트를 `warnings`에 싣는다.
    ⚠ **추가 대상에서 후보 0건으로 끊긴 run은 `not_reached`가 아니라 `none`/`no_poc`로 남는다** —
    의도한 것이 아니라 이 호출의 부수 효과다(후보 없음은 `_designate_final`이 `no_poc`로 싣는다).
    「PoV가 끝까지 돌아 submit을 못 냈다(후보 없음 - 중간 gen은 `vul_intermediate`가 센다)」와
    「예산·게이트웨이가 PoV를 끊었다」를 이 요약은 못 가른다 —
    후자는 run 수준의 `stopped_after`·`BUDGET_EXHAUSTED`·`MODEL_GATEWAY_UNAVAILABLE`·경고가 말한다.
    후보가 있어 `scored`가 된 run도 **부분 후보 집합 위의 지정**이다(`vul_candidates` - 중간 gen이
    없던 run은 `vul_submitted` - 와 `stopped_after`로 재구성한다).
    """
    if ctx.oracle is None:
        return
    ledger = state.oracle_ledger
    with ctx.stage(Stage.VALIDATE):
        grades = _grades_by_finding(state)
        picked, designation, reason = _designate_final(
            ledger, grades, _confidences_by_finding(state), _verdict_ranks_by_finding(state)
        )
        if picked is None:
            final = oracle_log.FinalAttempt(status=oracle_log.STATUS_NONE, reason=reason)
        else:
            final = _score(ctx, ctx.oracle, picked, designation)
        # 지정이 읽은 등급을 사이드카에 남긴다(후보가 둘 이상일 때만 읽는다). 같은 `grades`다.
        candidates = ledger.candidates
        read = (
            {a.finding_id: grades[a.finding_id].value for a in candidates if a.finding_id in grades}
            if len(candidates) > 1
            else None
        )
        ledger.set_final(final, grades=read)
        ctx.trace.emit(
            EventKind.ORACLE_FINAL,
            stage=Stage.VALIDATE,
            subject_kind=None if picked is None else "finding",
            subject_id=None if picked is None else picked.finding_id,
            wall_ms=final.wall_ms,
            # 키 집합은 `status`와 무관하게 같다 — 해당 없는 값은 `None`(안 정해졌다)이다.
            status=final.status,
            designation=final.designation,
            reason=final.reason,
            poc_sha256=None if picked is None else picked.poc_sha256,
            poc_bytes=None if picked is None else picked.poc_bytes,
            task_id=None if final.outcome is None else final.outcome.task_id,
            error=final.error,
            record_ref=ledger.record_ref,
        )


def _run_pov_hops(
    ctx: RunContext, state: _Accumulator, finding: Finding, capsule: ContextCapsule
) -> tuple[bytes | None, PovStopReason]:
    """8단계 PoV 툴 루프 (Option C) — 모델이 매 홉 `PovAction`(v5는 형제 `PovActionNamed`)을 내고
    도구를 돌린다.

    `_run_ne_hops`를 미러한다. `gen`은 `PovSpec`을 실어 순수 `assemble_poc`가 인프로세스로
    바이트를 만들고(모델-작성 코드 실행 없음), 그 PoC를 도구(poc)로 검증해 결과 Evidence를
    **다음 홉 user 턴**으로 되먹인다. `inspect`는 진단 도구(asan/build/gdb/poc)를 돌린다. `submit`은
    루프를 끝낸다 — 마지막 `gen`의 PoC가 final 후보다. ⏵ 2026-10-11: `poc_sha256`이 이미 낸 gen을
    지명하면 그 gen이다(`_resolve_submit_digest` — 못 찾으면 마지막 gen).

    **정지 판정 순서**(홉을 받은 뒤) — 1. `submit` → SUBMITTED 2. `gen`인데 이미 낸 어느 gen과든
    같은 **PoC 바이트**(digest 집합 조회 — A→B→A도 선다) → REPEAT_SPEC(spec JSON이 아니라 조립
    바이트로 본다 — rationale 재작성으로 못
    피하고, 다른 인코딩·같은 바이트도 잡는다) 3. `pov_tool_hops` 소진 → MAX_HOP. 홉 호출 자체가
    실패하면 흡수 여부를 `_pov_classify_exception`이 가른다(`_run_pov`의 추가형 계약 그대로 — 서버
    쪽 사건은 올려 보낸다). 반환은 (최종 후보 — 지명한 gen 또는 마지막 `gen` PoC, 없으면 `None` — 와
    정지 사유)다 — 이 2-튜플 모양은 호출부 20여 곳이 쥐고 있다.
    ⏵ **2026-10-11 (#550) — 스키마 파싱 실패는 홉 하나를 쓰고 루프가 계속 돈다**: `stop_reason` 없는
    `POV_HOP`을 내고 `[스키마 오류]` 노트(+ 아직 못 받은 관측)를 실어 같은 자리를 다시 묻는다.
    `last_poc`·`seen`은 그대로다. `PARSE_FAILED`로 서는 것은 둘뿐이다(⏵ 2026-10-12 — 둘째를
    더했다) — `hop >= pov_tool_hops`(마지막 홉)의 실패, 또는 **그 호출의 모든 시도가
    `finish_reason == "length"`로 끝난** 실패
    (`_pov_parse_truncated`)다. 후자는 재질문 없이 서고 종단 `POV_HOP`이 `parse_truncated`를
    싣는다 — 같은 프롬프트가 같은 상한에 닿는 것이 체계적일 수 있어서다(그 가능성은 이슈 #550의
    find_0005 한 건에서 봤고 재질문이 절단을 살리는 비율은 측정한 run이 0이다). 일부 시도만
    `length`이거나 못 쟀으면(`None`·빈 `attempt_log`) 절단으로 치지 않고 재질문한다.
    ⚠ **연속 파싱 실패의 횟수 상한은 없다** — 비절단 실패는 매 홉 이어질 수 있어
    `pov_tool_hops + 1`번까지 묻는다(이슈 #613이 쫓는다).
    게이트웨이 예외(`GATEWAY_ERROR`·`CONTEXT_OVERFLOW`)는 그 홉에서 곧장 선다.
    ⚠ **한계 — v5 prefix는 이 재질문 턴을 서술하지 않는다**(`prompts/pov/v5/prefix.md` 「홉 예산」:
    — 요지: 관측의 첫 줄은 홉 줄이고 hop 0에는 그 줄이 없다). ⏵ 2026-10-13 — v6 prefix는 서술한다
    (`prompts/pov/v6/prefix.md` 「Output」: 홉 줄 + `[스키마 오류]` 노트).
    hop0 재질문은 본문 **끝**에 홉 줄+노트가 붙고, hop≥1 재질문은 둘째 줄이 도구 요약이
    아니라 노트다. v5까지의 서술은 후속 판의 몫으로 남았고 v6가 했다(행동 어휘·스키마는 불변).

    ⏵ **2026-10-12 (#619) — `inspect`는 이미 돌린 도구를 다시 안 돌린다**(`ran` — 키는 도구뿐:
    `inspect`는 늘 직전 gen의 바이트를 싣고 gen이 `poc` 키를 덮는다). 옛 결과에
    `inspect_repeat …` 줄을 달아 되먹이고 루프는 안 선다 — 모델 홉은 쓴다(`MAX_HOP` 유효). 인프라
    실패(`pov.INFRA_FAILURE_EXITS`)는 기억하지 않아 재시도를 막지 않는다.

    ⏵ **2026-10-13 (#507) — 2단계 컴파일 실패는 hop0 본문 끝의 하네스 노트로 알린다**
    (`compile_note` — 코드를 읽는 판만 · 실패 Evidence가 없으면 `None`이고 바이트가 종전과 같다).
    `inspect build`·`asan`·`gdb`를 거절하지는 않는다. hop 0의 `pov_hop`이 `compile_note`·
    `compile_note_tools`를 싣는다.

    ⚠ **추가형이라 finding 상태를 바꾸지 않는다.**
    ⏵ **2026-10-09 (ADR 0014) — 컨테이너 `poc`는 PoC를 먹인다**(과제 `-vul` 이미지의
    `/tmp/poc`)라 그 도구의 관측은 **PoC의 함수**다. ⚠ **나머지는 아니다** — dev_fake는 대상 소스
    존재만 보고, 컨테이너의 `asan`·`gdb`·`build`는 우리가 빌드한 대상을 돌려 그 관측이 PoC와
    무관하다(셋을 바꾸려면 대상 빌드 규칙이 필요하다 — 이슈 #507). `inspect` 홉 관측에서 PoC의
    함수인 것은 입력의 크기·digest 줄(`_pov_observation`)뿐이다 —
    그 전까지 실서빙 pov 루프를 돌리면 도구 관측으로는 spec의 효과를 못 읽는다(모델이 언제
    submit할지는 안 쟀다). E4 게이트 전이도 후속이다. 중간 gen은 별도로 `vul` 판정을 읽어 다음
    모델 홉에 싣는다(v3 이상 - #354/#386①). meta가 `hop_header`를 켠 판(v4~)은 followup **맨
    앞**에 홉 줄을 더 받는다(`_pov_hop_line` · #511) - 그 줄이 있어야 모델이 마감을 안다. 여기서는
    홉 이벤트(`POV_HOP`)와 종단 이벤트(`POV_GENERATED`=submit·`POV_SKIPPED`)를 내고, 종단에서
    **최종 후보 PoC(보통 마지막 `gen`)를 run 산출물 사이드카로 남긴다**(`_pov_finish` — #355).
    종단 이벤트는 `last_gen_excluded_while_earlier_not`(`_terminal_fields` — 로그)를 단다: 코드를
    읽는 판(v3 이상)의 submit·repeat_spec·max_hop 출구만이고, 흡수된 예외 출구와 v2에는 없다.
    그 파일이 **없는** 경로가 있다: `BudgetExhausted`·서버 쪽 게이트웨이 오류는 올려 보내므로
    종단을 건너뛴다(그 한계와 `pov_hop`(gen)의 `poc_sha256`이 하는 일은 `poc_log` 모듈 docstring).
    도구 호출에서 난 `BudgetExhausted`는 그 홉의 `pov_hop`을 낸 **뒤에** 올린다(#394).
    홉 상한은 PoV 전용 `thresholds.pov_tool_hops`다(이슈 #511) — NE 루프의 `max_hops`와 갈랐다.

    `_pov_finish`는 사이드카의 `(ref, digest)`를 3·4번째로 내지만 이 함수의 반환은 위 2-튜플
    그대로다 — 네 출구는 `_pov_finish_and_record`를 불러 `ref`를 계수기 사유에 쓰고 2-튜플만
    돌려받는다(`digest`는 그 함수가 버린다). 채점기 제출(`_run_pov`)은 이 반환의 바이트를 메모리에서
    그대로 낸다. 반환을 넓히는 슬라이스는 그 함수에서 `ref`·`digest`를 그대로 올리면 되고
    디렉터리를 글로브할 필요가 없다.
    """
    pov_hops = ctx.config.thresholds.pov_tool_hops
    workdir = state.workdirs.get(finding.finding_id, ".")
    with ctx.stage(Stage.VALIDATE):
        last_poc: bytes | None = None
        # 지금까지 조립한 PoC의 digest(`poc_log`와 같은 `sha256:` 형식) → 그 바이트. 반복 판정은
        # 키 조회이고, `submit`이 `poc_sha256`으로 지명한 gen을 여기서 찾는다.
        seen: dict[str, bytes] = {}
        # 도구 → (마지막으로 돌린 홉, 증거) — `inspect`가 같은 도구를 또 요청하면 안 돌린다(#619).
        ran: dict[str, tuple[int, Evidence]] = {}
        # gen마다의 `vul` 종료 코드(없으면 `None`) — **로그 전용**이다. 종단 이벤트의 계측 플래그만
        # 읽고 지정(`_designate_final`)은 읽지 않는다.
        gen_codes: list[int | None] = []
        followup = ""
        # 홉 줄을 붙이기 **전**의 followup(= 직전 홉의 도구 관측) — 다음 호출이 파싱에 실패하면
        # 그 관측을 아직 모델이 못 받았으므로 다시 물을 때 같이 싣는다(`prev` history에 안 들었다).
        observation = ""
        # hop0 호출이 파싱에 실패했을 때 다시 열면서 본문 끝에 붙일 오류 노트(#550).
        retry_note: str | None = None
        prev: pov.PovHop | None = None
        hop = 0
        # 퍼즈 진입점 조회 결과 — hop0에서 채우고 **hop0 이벤트에만** 싣는다. 루프 밖에서 먼저
        # 묶는 이유는 조회가 던져도 `except` 갈래가 이 이름을 안전하게 읽어야 해서다.
        fuzz: dict[str, object] | None = None
        # 루프 내내 안 바뀐다(설정값) — 홉마다 다시 읽지 않는다. hop0의 `source=` 결정과
        # 매 홉의 관측 꼬리(`_pov_observation`)와 중간 gen의 vul 제출·판정 줄 셋 다 이것을 본다
        # (적대적 리뷰 2026-10-02 MEDIUM-4 — v2는 이 PR 전과 바이트가 같아야 한다. 관측 꼬리도
        # 이 PR의 새 입력이다).
        pov_wide = (
            ctx.config.model_for(Role.POV.value).prompt_template_version
            in pov._WIDE_INPUT_VERSIONS
        )
        # ⏵ 2026-10-13 (이슈 #507) — 2단계가 이미 잰 컴파일 실패를 hop0 본문에 알리는 노트. 코드를
        # 읽는 판(`pov_wide`)에만 싣는다(v2는 이 PR 전과 바이트가 같다 — `fuzz_entrypoint`와 같은
        # 경계). 실패 Evidence가 없으면 `None`이라 본문도 trace도 종전 그대로다. 도구는 거절하지
        # 않는다.
        compile_failures = ctx.compile_failures.get(workdir, ()) if pov_wide else ()
        compile_note = signals.compile_note(compile_failures, workdir)
        # hop0 `pov_hop` 이벤트에만 싣는 계측 — 노트를 붙인 호출이 있었다는 사실과 그것이 짚은
        # 도구들(`build`가 실패했을 때만 `build,asan,gdb`). 이 키가 있는 run에서
        # hop ≥ 0의 `tool`이 그 도구들인 비율이 「노트 뒤 재선택률」이다.
        note_trace = (
            signals.compile_note_trace(compile_failures) if compile_note is not None else None
        )
        while True:
            started = ctx.monotonic_ms()
            try:
                if prev is None:
                    # 넓은 입력 판은 슬라이스 여부와 무관하게 Auditor의 최초 읽기를 보관한
                    # `_Once`를 받는다. 이전 판 또는 캐시 부재 시 노드가 캡슐 대상을 직접 읽는다.
                    entrypoint = _fuzz_entrypoint(ctx, capsule.target.file)
                    # 🔴 **조회 결과를 홉 이벤트에 싣는다**(아래 `fuzz=`). 안 남기면 「진입점 탐색
                    # 성공률」이 영영 미실측이고, 성공 0이 나왔을 때 모델 탓인지 조회 탓인지 못
                    # 가른다(§0). **새 EventKind를 만들지 않는다** — 홉 수를 세는 검사들이
                    # `POV_HOP` 개수를 단언하므로 이벤트를 더하면 그것이 깨진다. 이 측정은
                    # **버전과 무관하게** 늘 한다 — v2가 배선되더라도 진입점 탐색 성공률은
                    # 관측해 둔다(모델에게 실제로 실리는지와는 다른 질문이다).
                    fuzz = entrypoint
                    # ⚠ **v2는 `state.finding_sources`를 안 본다** — Auditor의 넓은 SOURCE를
                    # 모델 프롬프트에 실으려면(§ `_run_pov_hops` 윗줄) 배선된 pov 버전이
                    # `pov._WIDE_INPUT_VERSIONS`에 있어야 한다. 아니면 `source=None`을 넘겨
                    # `nodes.pov._render_prompt`가 예전 entry-only 갈래로 가게 한다(적대적 리뷰
                    # 2026-10-02 MEDIUM-4 — v2는 이 PR 전과 바이트가 같아야 한다).
                    wide_source = (
                        state.finding_sources.get(finding.finding_id) if pov_wide else None
                    )
                    current = pov.open_hop(
                        ctx,
                        finding=finding,
                        capsule=capsule,
                        source=wide_source,
                        fuzz_entrypoint=entrypoint,
                        retry_note=retry_note,
                        compile_note=compile_note,
                        hop=hop,
                    )
                else:
                    current = pov.continue_hop(
                        ctx, finding=finding, prev=prev, followup=followup
                    )
            except (ModelGatewayError, SchemaParseError) as exc:
                # `BudgetExhausted`는 안 잡는다 — 추가형 계약대로 올려 보낸다(run의 사실).
                reason = _pov_classify_exception(exc)
                if reason is None:
                    raise
                latency_s = (ctx.monotonic_ms() - started) / 1000.0
                truncated = _pov_parse_truncated(exc)
                if reason is PovStopReason.PARSE_FAILED and hop < pov_hops and not truncated:
                    # 파싱 실패는 **홉 하나를 쓴 것**이다(#550) — `stop_reason` 없이 이벤트를 내고
                    # 같은 자리를 다시 묻는다. `last_poc`·`seen`·`prev`는 그대로다.
                    _emit_pov_hop(
                        ctx, finding, hop=hop, failed=(exc, latency_s),
                        fuzz=fuzz if hop == 0 else None,
                        hop0_note=note_trace if hop == 0 else None,
                    )
                    hop += 1
                    version = (
                        ctx.config.model_for(Role.POV.value).prompt_template_version
                        if prev is None
                        else prev.version
                    )
                    note = _pov_parse_retry_note(
                        hop, pov_hops, exc, with_hop_line=load_prompt(Role.POV, version).hop_header
                    )
                    if prev is None:
                        retry_note = note
                    else:
                        followup = f"{note}\n{observation}"
                        # 다시 묻는 호출이 `hop`번 홉이 되게 한다(`model_call.hop`=`POV_HOP.hop`).
                        prev = replace(prev, hop=hop - 1)
                    continue
                _emit_pov_hop(
                    ctx, finding, hop=hop, stop_reason=reason.value,
                    failed=(exc, latency_s), fuzz=fuzz if hop == 0 else None,
                    hop0_note=note_trace if hop == 0 else None,
                    parse_truncated=truncated,
                )
                return _pov_finish_and_record(ctx, state, finding, reason, last_poc)
            action = current.action
            if action.action == "submit":
                # 지명이 없거나 못 찾으면 마지막 gen이다(종전 동작).
                named = getattr(action, "poc_sha256", None)  # v5 봉투(`PovActionNamed`)만 든다
                named_poc, submit_digest = _resolve_submit_digest(named, seen)
                _emit_pov_hop(
                    ctx, finding, hop=hop, action="submit",
                    stop_reason=PovStopReason.SUBMITTED.value,
                    fuzz=fuzz if hop == 0 else None,
                    hop0_note=note_trace if hop == 0 else None,
                    submit_digest=submit_digest,
                )
                return _pov_finish_and_record(
                    ctx, state, finding, PovStopReason.SUBMITTED,
                    last_poc if named_poc is None else named_poc,
                    terminal=_terminal_fields(pov_wide, gen_codes),
                )
            hop_poc: bytes | None = None  # 이 홉의 gen이 조립한 PoC — inspect 홉에는 없다
            oracle_observation = "oracle_vul=unavailable"
            if action.action == "gen":
                # 반복 판정은 조립된 **바이트**의 digest를 **본 적 있는 모든 gen**과 견준다 — spec
                # JSON에는 `rationale`(바이트에 안 드는 자유 텍스트)이 섞여 재설명만 바꾼 같은 PoC를
                # 못 잡고(적대적 리뷰), 직전 한 gen만 보면 A→B→A 진동이 안 선다.
                hop_poc = assemble_poc(action.spec)
                digest = sha256_digest(hop_poc)
                if digest in seen:
                    _emit_pov_hop(
                        ctx, finding, hop=hop, action="gen", poc=hop_poc,
                        stop_reason=PovStopReason.REPEAT_SPEC.value,
                        fuzz=fuzz if hop == 0 else None,
                        hop0_note=note_trace if hop == 0 else None,
                    )
                    # 서게 한 gen이 마지막 gen이다 — A→B→A면 A(직전 `last_poc`는 B)다.
                    return _pov_finish_and_record(
                        ctx, state, finding, PovStopReason.REPEAT_SPEC, hop_poc,
                        terminal=_terminal_fields(pov_wide, gen_codes),
                    )
                seen[digest] = hop_poc
                last_poc = hop_poc
                # 마지막 홉(`hop >= pov_hops`)의 gen은 관측을 읽을 다음 홉이 없다 - 내면 제출·조회
                # 두 번이 레이트 예산만 쓰고 버려진다.
                if pov_wide and hop < pov_hops:
                    oracle_observation = _submit_vul_to_oracle(
                        ctx, state, finding, hop_poc, designatable=False, in_validate_span=True
                    )
                gen_codes.append(_oracle_code(oracle_observation))
                tool, tool_poc = "poc", hop_poc
            else:  # inspect — 새 PoC 없이 **직전 gen의** PoC로 진단(아직 gen이 없으면 `None`)
                tool, tool_poc = action.tool, last_poc
            # ⚠ `asan`·`gdb`·`build`가 PoC를 받게 바뀌면 키에 바이트를 넣어라(container_tools 핀).
            prior = ran.get(tool) if action.action == "inspect" else None
            try:
                evidence = (
                    prior[1]
                    if prior is not None
                    else pov.run_tool(
                        ctx, finding=finding, tool=tool, workdir=workdir, poc=tool_poc
                    )
                )
            except BudgetExhausted as exc:
                # 🔴 **그 홉의 이벤트를 내고 다시 던진다**(#394). `call_tool`도 `ledger.ensure`를
                # 하므로 예산은 이 자리에서도 끊긴다 — gen PoC를 조립한 **뒤**, 홉 이벤트를 내기
                # **전**이다. 안 내면 오라클 제출이 없는 run·마지막 홉·v2에서 그 PoC의 해시는 trace
                # 어디에도 없다(제출이 있으면 `oracle_submitted`가 같은 해시를 싣는다 - 홉과의
                # 연결만 없다). 내면 다음 홉에서 끊긴 경로와 같아진다(그 홉 gen 이벤트의
                # `poc_sha256`이 잃은 PoC를 특정한다).
                # 원장은 안 건드린다 — `ensure`가 막은 호출은 도구를 안 돌렸고 청구도 없다.
                # 예외는 그대로 올린다(추가형 계약 — 예산 소진은 run의 사실). 종단(`_pov_finish`)은
                # 여전히 안 부른다 — 파일은 이 경로에서도 없다(`poc_log` 모듈 docstring 「한계」).
                # 이벤트 쓰기가 던지면 원 예외가 가려지므로 `add_note`로 붙이고 원 예외를 올린다.
                try:
                    _emit_pov_hop(
                        ctx, finding, hop=hop, action=action.action, poc=hop_poc,
                        failed=(exc, None), wall_ms=max(0, ctx.monotonic_ms() - started),
                        fuzz=fuzz if hop == 0 else None,
                        hop0_note=note_trace if hop == 0 else None,
                    )
                except Exception as emit_exc:
                    exc.add_note(
                        f"pov_hop을 내지 못했다: {type(emit_exc).__name__}: {emit_exc}"
                    )
                raise
            if prior is None:
                if evidence.exit_code in pov.INFRA_FAILURE_EXITS:
                    ran.pop(tool, None)  # 인프라 실패는 기억하지 않는다 — 재시도를 막지 않는다
                else:
                    ran[tool] = (hop, evidence)
            # 반복 홉은 도구를 안 돌렸으니 옛 결과를 이 홉의 것으로 싣지 않는다(#619).
            fresh = evidence if prior is None else None
            repeat_of = None if prior is None else (tool, prior[0])
            if hop >= pov_hops:
                _emit_pov_hop(
                    ctx, finding, hop=hop, action=action.action, evidence=fresh, poc=hop_poc,
                    stop_reason=PovStopReason.MAX_HOP.value, inspect_repeat_of=repeat_of,
                    fuzz=fuzz if hop == 0 else None,
                    hop0_note=note_trace if hop == 0 else None,
                )
                return _pov_finish_and_record(
                    ctx, state, finding, PovStopReason.MAX_HOP, last_poc,
                    terminal=_terminal_fields(pov_wide, gen_codes),
                )
            _emit_pov_hop(
                ctx, finding, hop=hop, action=action.action, evidence=fresh, poc=hop_poc,
                inspect_repeat_of=repeat_of, fuzz=fuzz if hop == 0 else None,
                hop0_note=note_trace if hop == 0 else None,
            )
            # 도구 요청에 실은 PoC를 싣는다 — gen이면 `last_poc`는 방금 이 홉이 조립한 것이다.
            followup = _pov_observation(
                action.action,
                evidence,
                read_payload=ctx.read_payload,
                wide=pov_wide,
                poc=last_poc,
            )
            if prior is not None:
                why = (
                    f"같은 바이트로 홉 {prior[0]}에서 돌렸다"
                    if tool == "poc"
                    else f"{tool}는 gen의 바이트와 상관없이 홉 {prior[0]}의 결과와 같다"
                )
                followup = (
                    f"inspect_repeat tool={tool} same_as_hop={prior[0]} rerun=false — {why} — "
                    f"재실행하지 않았다(아래는 그 결과).\n{followup}"
                )
            if action.action == "gen" and pov_wide:
                # 판정 줄은 값이 없거나 조회가 실패해도 생략하지 않는다. 원시 종료 코드만 싣고
                # 의미를 파생하지 않는다. 모델이 이 관측을 본 뒤 submit할지를 정한다.
                followup = f"{followup}\n{oracle_observation}"
            # 홉 줄은 **맨 앞**이다 - 이 followup에 답할 홉 번호와 마지막 홉 번호(#511). 위 `hop`은
            # 방금 끝낸 홉이라 모델이 답할 홉은 `hop + 1`이고, 여기까지 왔다는 것은
            # `hop < pov_hops`라는 뜻이라(`MAX_HOP`은 위에서 이미 갈렸다) `remaining`은 0 이상이다.
            # 줄을 받는 판은 meta가 `hop_header`로 선언한다(`prompt_loader` docstring) - v2·v3는
            # 바이트 그대로다.
            observation = followup
            if load_prompt(Role.POV, current.version).hop_header:
                followup = f"{_pov_hop_line(hop + 1, pov_hops)}\n{followup}"
            prev = current
            hop += 1


# 접두 지명의 최소 길이 — 짧은 접두가 우연히 맞아 엉뚱한 gen을 가리키는 것을 막는다.
_MIN_DIGEST_PREFIX = 8
_ORACLE_VUL_CODE = re.compile(r"oracle_vul exit_code=(-?\d+)")


def _oracle_code(observation: str) -> int | None:
    """`_submit_vul_to_oracle`의 관측 줄에서 원시 종료 코드를 뗀다(`unavailable`이면 `None`)."""
    match = _ORACLE_VUL_CODE.match(observation)
    return None if match is None else int(match.group(1))


def _resolve_submit_digest(
    named: str | None, seen: Mapping[str, bytes]
) -> tuple[bytes | None, str]:
    """`submit`의 `poc_sha256`을 이미 낸 gen에 푼다 → (지명된 바이트 또는 `None`, `submit_digest`).

    스키마는 길이 80만 걸고 형식은 검증하지 않으므로(`PovActionNamed` — 81자 이상은 파싱 실패라
    여기에 안 온다) **여기서 정규화한다** — 앞뒤 공백을 떼고
    소문자로 바꾸고 `sha256:` 접두를 뗀 뒤, 8자 이상이고 **정확히 한 gen의 digest에** 전체나 접두로
    맞을 때만 `matched`다. 지명이 없으면 `absent`, 그 밖(7자 이하·16진 밖 글자·어느 gen에도 안 맞음·
    둘 이상에 맞음)은 전부 `unknown`이고 바이트는 `None`이다 — 호출부가 마지막 gen으로 돌아간다.

    **`seen`만 본다.** 채점기 값(`state.oracle_ledger`)을 읽지 않는다는 것은 이 docstring의
    보장이다 — 장부로 고르는 변이는 **결과가 같은 입력에서는** 테스트가 못 가른다(다른 입력에서는
    `tests/test_pov_prompt_v4.py`의 `test_an_unnamed_submit_still_designates_the_last_gen…`이
    잡는다).
    """
    if named is None:
        return None, "absent"
    want = named.strip().lower().removeprefix("sha256:")
    if len(want) < _MIN_DIGEST_PREFIX:
        return None, "unknown"
    hits = [poc for digest, poc in seen.items() if digest.removeprefix("sha256:").startswith(want)]
    return (hits[0], "matched") if len(hits) == 1 else (None, "unknown")


def _terminal_fields(pov_wide: bool, gen_codes: Sequence[int | None]) -> dict[str, TraceValue]:
    """종단 이벤트의 계측 — 마지막 gen의 `vul` 코드가 제외 코드(또는 없음)인데 앞선 gen 중에
    제외 코드 밖이 있었나. v5 규율(「셋 밖이면 다음 턴에 submit」)의 준수를 사후에 세는 **로그**이고
    지정 입력이 아니다. 코드를 읽는 판(`pov_wide`)만 싣는다."""
    if not pov_wide:
        return {}
    *earlier, last = gen_codes or [None]
    lost = last is None or last in oracle_log.NO_CRASH_VUL_EXIT_CODES
    won = any(c is not None and c not in oracle_log.NO_CRASH_VUL_EXIT_CODES for c in earlier)
    return {"last_gen_excluded_while_earlier_not": lost and won}


def _pov_hop_line(hop: int, last_hop: int) -> str:
    """followup 맨 앞 줄 - 모델이 지금 답하는 홉(`hop`)·마지막 홉(`last_hop`)·그 뒤 남은 홉 수.

    기계가 읽는 모양이라 키 이름과 순서를 바꾸면 prefix(`prompts/pov/v4/prefix.md`의 「홉 예산」)와
    `tests/test_pov_prompt_v4.py`가 같이 움직여야 한다. `_pov_observation`의 출력에 넣지 않는다 -
    그쪽의 v2 바이트 동일이 핀이다."""
    return f"hop={hop} last_hop={last_hop} remaining={last_hop - hop}"


def _pov_parse_retry_note(
    hop: int, last_hop: int, exc: Exception, *, with_hop_line: bool
) -> str:
    """파싱 실패 뒤 재질문에 싣는 노트 — 홉 줄(`hop_header` 판만) + `[스키마 오류] <사유>`.

    자리는 둘이다: hop≥1이면 followup의 **머리**(노트 다음에 직전 관측), hop 0이면 첫 본문의 **끝**
    (`open_hop(retry_note=)`). 사유는 어댑터가 `include_input=False`로 만든 `model_error_text`를
    512자로 자른 것이다 — 예외 문자열(`str(exc)`)을 싣지 않는다: 응답 원문 조각(`input_value`)과
    pydantic URL을 든다. 그 텍스트가 비면(Fake) 고정 문구다. 모든 시도가 `length`면 재질문이
    없어 이 함수는 불리지 않는다(`_pov_parse_truncated`)."""
    line = f"{_pov_hop_line(hop, last_hop)}\n" if with_hop_line else ""
    model_text = getattr(exc, "model_error_text", "")
    detail = truncate_detail(model_text) if model_text else _PARSE_NOTE_NO_TEXT
    return f"{line}[스키마 오류] {detail}"


# `SchemaParseError.model_error_text`가 빌 때(Fake·옛 예외)의 노트 사유 — 예외 문자열을 대신 싣지
# 않는다: 어댑터의 `str(exc)`는 응답 원문 조각(`input_value`)과 pydantic URL을 든다.
_PARSE_NOTE_NO_TEXT = "응답이 PovAction 스키마를 만족하지 못했다"


def _pov_parse_truncated(exc: Exception) -> bool:
    """파싱 실패가 **모든 시도가 `finish_reason == "length"`로 끝난** 것인가 — 서버의 말이다.

    빈 `attempt_log`(Fake·잃음)·`None`·`length` 밖 사유가 하나라도 있으면 거짓이다 — 안 쟀거나
    일부만 절단이면 절단이라 단정하지 않는다(CLAUDE.md §0). 참이면 루프가 같은 프롬프트를 다시
    묻지 않는다."""
    log = exc.attempt_log if isinstance(exc, SchemaParseError) else ()
    return bool(log) and all(a.finish_reason == "length" for a in log)


def _pov_observation(
    action: str,
    evidence: Evidence,
    *,
    read_payload: ReadPayload,
    wide: bool = True,
    poc: bytes | None = None,
) -> str:
    """도구 결과를 다음 홉 user 턴으로 보낼 관측 텍스트(비지 않아야 한다 — `Prompt.body`).

    ⏵ **2026-10-02 — 도구 출력 꼬리를 싣는다.** 전 판은 스칼라 여섯(`tool`·`action`·`ok`·`exit`·
    `kind`·`error`)만 줬다. 그러면 PoV는 **왜 그렇게 됐는지를 모른다** — ASan이 어떤 위반을 어느
    오프셋에서 잡았는지, gdb가 대상을 띄웠는지가 전부 payload 로그에만 있고 관측에는 없었다.
    프롬프트는 *"관측을 읽고 실패 이유를 판단한 뒤 고쳐라"*라 지시하는데 **읽을 것이 없었다.**

    꼬리를 쓰는 이유: ASan 보고서는 **끝**에 요약(`SUMMARY: AddressSanitizer: …`)이 오고 gdb도
    수명주기 줄이 뒤에 온다. 앞을 자르면 컴파일 잡음만 남는다.

    ⚠ **`read_payload`가 `None`을 주는 것은 정상 상태다**(`dev_fake`는 로그 파일을 안 쓴다 —
    `context.read_payload` 주석). 그때는 한 줄이고, 입력이 없으면 전 판과 **바이트가 같다**.
    🔴 **참조 PoC(`/tmp/poc`) 바이트가 이 경로로 프롬프트에 들어가면 run이 무효다.** 여기 실리는
    것은 도구 **stdout/stderr 로그**와 모델 자신의 PoC의 크기·digest이고 PoC 바이트 자체가
    아니다 — 로그의 분리는 payload를 쓰는 쪽(`container_tools`)이 지킨다.

    `wide=False`(`pov._WIDE_INPUT_VERSIONS`에 없는 이전 판 v2)면 **꼬리를 안 싣는다** — v2
    prefix.md는 여전히 "관측 한 줄"이라 서술하고(`prompts/pov/v2/prefix.md:7`), 전 판과 바이트가
    같아야 한다(적대적 리뷰 2026-10-02 MEDIUM-4). `_run_pov_hops`가 설정된 pov 버전을 보고 정한다.

    ⏵ **#386 ① — 직전 `gen`의 PoC 크기와 sha256을 싣는다**(`poc` — `pov.run_tool`이 도구 요청에
    실은 바이트, 맨 hex라 trace의 `poc_sha256`과 바로 대조된다). `gen` 홉의 도구는 `poc`라
    그 결과도 PoC의 함수지만(ADR 0014) `inspect` 홉의 셋은 아니고, 이 줄은 **어느 홉에서나**
    PoC의 함수라 되먹임이 어느 PoC에 대한 것인지 가른다. 필드 이름이 `last_gen_*`인 것은
    일부러다 — ⏵ 2026-10-09: 처음 이유는 *"`input_*`는 도구가 받았다고 읽히는데 prefix가 그 반대를
    적는다"*였고 그것은 **낡았다**(v5부터 받는다고 적는다). 이름을 그대로 두는 이유는 이 줄이
    **직전 `gen`**의 바이트를 가리킨다는 것이고(`inspect` 홉에서도 그렇다) `input_*`는 그 홉의
    도구가 받은 것으로 읽힌다. 입력이 없으면(hop0 `inspect`) 줄 자체가 없다 —
    `b""`는 입력이다(`last_gen_bytes=0`). `error=`는 자유 문구라 **맨 끝**이다.
    `wide=False`면 안 싣는다(꼬리와 같은 경계).
    """
    detail = f" error={evidence.error}" if evidence.error else ""
    head = (
        f"tool={evidence.source} action={action} ok={evidence.ok} "
        f"exit={evidence.exit_code} kind={evidence.kind.value}"
    )
    if not wide:
        return head + detail
    if poc is not None:
        head += f" last_gen_bytes={len(poc)} last_gen_sha256={hashlib.sha256(poc).hexdigest()}"
    head += detail
    # `payload_ref`는 `NonEmptyStr`이라 **항상 있다**(`schemas/evidence.py:35`) — 참조가 없는
    # 경우를 가르지 않는다. 없는 것은 **로그**이고 그것은 `read_payload`가 `None`으로 답한다.
    body = read_payload(evidence.payload_ref)
    if not body:
        return head
    # ⏵ 2026-10-02 추가(적대적 리뷰 LOW) — **자르기 전에** 하네스 머리줄을 뺀다.
    # `evidence_summaries`(Auditor·Critic 근거 본문)가 이미 하는 것과 같은 장치를 재사용한다 —
    # `# node argv: …`·`# runtime: …`·`$ docker run … -v <호스트 절대경로>:/src:ro` 같은 줄이
    # 짧은 로그에서는 꼬리 전체를 차지해 호스트 경로가 그대로 프롬프트에 실렸다.
    body = _HARNESS_HEADER_LINE.sub("", body)
    tail = body[-_POV_OBSERVATION_TAIL_CHARS :]
    cut = "" if len(body) == len(tail) else f"(앞 {len(body) - len(tail)}자 생략)\n"
    return f"{head}\n--- {evidence.source} 출력 꼬리 ---\n{cut}{tail}"


def _pov_classify_exception(exc: Exception) -> PovStopReason | None:
    """PoV 홉 호출 예외를 흡수 여부로 가른다 — pov 추가형 계약(옛 `_run_pov` 단발판이 쓰던
    `_is_droppable` 판정 그대로).

    버릴 수 있는 게이트웨이 실패는 흡수해 이 finding만 건너뛰고, **스키마 파싱 실패는 마지막
    홉에서, 또는 전 시도가 `length`로 끝났을 때** finding을 건너뛴다(그 밖의 앞 홉 실패는 루프가 홉
    하나를 쓰고 다시 묻는다 — #550),
    서버 쪽 사건(status 없음·5xx = `not _is_droppable`)은 `None`으로 두어 올려 보낸다 —
    run의 사실이라 `run_pipeline`이 멈춘다. 크기 거절만 CONTEXT_OVERFLOW로 따로 센다(폭주 차단).
    `BudgetExhausted`는 여기 안 온다 — 루프가 안 잡고 올려 보낸다.
    ⏵ 2026-10-10 도구 호출(`run_tool`)에서 난 것만은 루프가 그 홉의 이벤트를 낸 뒤 올린다(#394).
    ⏵ 2026-10-11 이 함수는 **사유만** 가른다 — `PARSE_FAILED`로 실제 서는지는 루프가 홉 여유를 보고
    정한다(마지막 홉이거나, ⏵ 2026-10-12부터는 전 시도가 절단일 때도 선다 · `_run_pov_hops`, #550).

    ⚠ NE 루프와 다르다: 크기 아닌 400(체계적 거절)을 NE는 `_rejects_every_request`로 올려 보내
    run을 멈추지만(#296), pov는 추가형이라 `_is_droppable`(400 포함)로 **흡수해** finding만
    건너뛴다 — 체계적 pov 실패가 파이프라인을 멈추면 안 된다는 계약(옛 `_run_pov` docstring)."""
    if isinstance(exc, SchemaParseError):
        return PovStopReason.PARSE_FAILED
    if isinstance(exc, ModelGatewayError):
        if not _is_droppable(exc):
            return None
        if _is_size_rejection(exc):
            return PovStopReason.CONTEXT_OVERFLOW
        return PovStopReason.GATEWAY_ERROR
    return None


def _emit_pov_hop(
    ctx: RunContext,
    finding: Finding,
    *,
    hop: int,
    action: str | None = None,
    evidence: Evidence | None = None,
    poc: bytes | None = None,
    stop_reason: str | None = None,
    failed: tuple[Exception, float | None] | None = None,
    wall_ms: int | None = None,
    fuzz: Mapping[str, object] | None = None,
    hop0_note: Mapping[str, TraceValue] | None = None,
    submit_digest: str | None = None,
    parse_truncated: bool = False,
    inspect_repeat_of: tuple[str, int] | None = None,
) -> None:
    """`POV_HOP` 이벤트 하나 — hop0 포함 홉마다. `failed`면 그 홉의 호출 자체가 실패한 것이다
    (`_emit_scout_hop`의 `failed_hop`과 같은 자리 — 성공한 응답이 없어 따로 채운다). 그 홉의
    **도구 호출**이 예산에서 끊긴 경우도 `failed`로 낸다(#394) — 그때는 응답이 있어 `action`이
    같이 실리고 `schema_ok`가 참이다. 이 경우 경과는 `data`가 아니라 최상위 `wall_ms`로 보낸다
    (`failed`의 시간은 `None`) — Fake 경로의 결정론 trace에 시계값을 `data`로 넣지 않는다.

    `poc`는 **그 홉의 gen이 조립한 PoC**다 — 있으면 맨 hex sha256을 `poc_sha256`으로 싣는다(종단
    이벤트의 `poc_sha256`과 같은 형식이라 바로 대조된다). 종단을 건너뛰는 중단 경로(예산 소진·서버
    쪽 게이트웨이 오류)에서 잃은 PoC를 특정하는 흔적이다. 그 홉 자신의 도구 호출이 예산에서
    끊겨도 루프가 이 이벤트를 내고 다시 던진다(#394). 그 밖의 형으로 도구 호출에서 끊기면 이벤트가
    없다(`poc_log` 모듈 docstring의 「한계」).

    `fuzz`는 **hop0에만** 준다 — 퍼즈 진입점 조회의 결과다(`_fuzz_entrypoint`). 그것을 여기 싣는
    이유는 「진입점 탐색 성공률」이 미실측으로 남지 않게 하는 것이고, **새 EventKind를 만들지 않는
    이유**는 홉 수를 세는 검사들이 `POV_HOP` 개수를 단언하기 때문이다(이벤트를 더하면 깨진다).

    `hop0_note`도 **hop0에만** 준다 — hop0 본문에 컴파일 실패 노트를 붙인 호출이었다는 계측이다
    (`compile_note`·`compile_note_tools` — 이슈 #507). 키가 없으면 노트가 없었다.

    `submit_digest`는 `submit` 홉에만 준다 — `poc_sha256` 지명의 결과(`absent`·`matched`·`unknown`).

    `inspect_repeat_of`는 `(도구, 옛 홉)`이다 — 같은 (도구, 입력)을 이미 돌려 **이번엔 안 돌린**
    `inspect` 홉(#619)이라 `tool`·`inspect_repeat_of_hop`만 싣고 도구 결과 키(`tool_ok`…)는 없다.
    """
    data: dict[str, TraceValue] = {"hop": hop}
    if fuzz is not None:
        data["fuzz_found"] = bool(fuzz["found"])
        data["fuzz_file"] = str(fuzz["file"])
        data["fuzz_reason"] = str(fuzz["reason"])
    if hop0_note is not None:
        data.update(hop0_note)
    if action is not None:
        data["action"] = action
    if evidence is not None:
        data["tool"] = evidence.source
        data["tool_ok"] = evidence.ok
        data["exit_code"] = evidence.exit_code
        data["evidence_kind"] = evidence.kind.value
    if inspect_repeat_of is not None:
        data["tool"], data["inspect_repeat_of_hop"] = inspect_repeat_of
    if poc is not None:
        data["poc_sha256"] = hashlib.sha256(poc).hexdigest()
    if stop_reason is not None:
        data["stop_reason"] = stop_reason
    if submit_digest is not None:
        data["submit_digest"] = submit_digest
    if failed is not None:
        exc, latency_s = failed
        data["schema_ok"] = not isinstance(exc, SchemaParseError)
        if latency_s is not None:
            data["latency_s"] = latency_s
        data["error"] = type(exc).__name__
        data["error_detail"] = error_detail(exc)
    if parse_truncated:
        data["parse_truncated"] = True
    ctx.trace.emit(
        EventKind.POV_HOP,
        stage=Stage.VALIDATE,
        subject_kind="finding",
        subject_id=finding.finding_id,
        wall_ms=wall_ms,
        **data,
    )


def _pov_finish(
    ctx: RunContext,
    finding: Finding,
    reason: PovStopReason,
    poc: bytes | None,
    *,
    terminal: Mapping[str, TraceValue] | None = None,
) -> tuple[bytes | None, PovStopReason, str | None, str | None]:
    """루프 종단 이벤트. `POV_GENERATED`는 **모델이 submit해 최종 PoC가 나온 경우만** 낸다 —
    나머지(max_hop·repeat·흡수된 예외·gen 없는 submit)는 `POV_SKIPPED`다. 부분 PoC(중간 gen까지는
    했으나 submit 못 함)는 SKIPPED에 `poc_bytes`/`poc_sha256`과 사이드카 키로 남긴다.

    ⚠ 예전에는 PoC가 있기만 하면 GENERATED라 max_hop·parse_failed도 「생성됨」으로 과대 집계됐다
    (적대적 리뷰 2026-09-30). CyberGym 최종 답은 submit된 PoC 하나뿐이라 그것만 GENERATED다.

    **바이트가 있으면 사이드카 파일로 남기고** 그 참조·digest를 이 이벤트에 싣는다(`poc_ref`·
    `poc_digest`, #355) — submit 못 한 부분 PoC도 남긴다(회수가 목적이라 종단 사유로 가르지 않는다).
    **쓰기가 실패하면(`OSError`) 흡수한다** — PoV는 추가형이고 사이드카는 부가 산출물이라 run을
    죽이지 않는다. 이벤트가 `poc_ref` 대신 `poc_write_error`·`poc_write_error_detail`을 싣고 종단
    사유·종류는 그대로다. `ctx.pocs`가 `None`이면(단위 테스트·`--dry-run`) 이 키들이 **아예 없다**.
    **Evidence를 달지 않는다**는 것과 digest 형식이 아래 `poc_sha256`과 다른 이유는 `poc_log` 모듈
    docstring에 있다.

    반환은 `(poc, reason, ref, digest)`다 — `ref`·`digest`는 방금 쓴 사이드카의 것이고 안 썼으면
    (배선 없음·바이트 없음·쓰기 실패) `None`이다. 읽는 곳은 아직 없다(`_run_pov_hops`가 `[:2]`로
    뗀다) — 파일 참조가 필요해질 때 디렉터리를 글로브하지 않고 쓰게 내 둔다.

    `terminal`은 종단 이벤트에 그대로 얹는 계측 필드다(`_terminal_fields`)."""
    ref: str | None = None
    digest: str | None = None
    sidecar: dict[str, TraceValue] = {}
    if poc is not None and ctx.pocs is not None:
        try:
            ref, digest = ctx.pocs.write(poc)
        except OSError as exc:
            sidecar = {
                "poc_write_error": type(exc).__name__,
                # `str(exc)`가 아니라 `strerror`다 — 앞의 것은 파일의 절대경로를 싣고 trace는
                # 경로를 안 싣는다(재현성 비교 단위).
                # ⚠ `strerror`는 사람이 읽는 부가 문구이지 안정 키가 아니다 — OS 로케일을 따르고
                # (이 장비 한국어 Windows에서 `mkdir`·`os.replace`는 한국어, `open`은 영어
                # `File exists` — 2026-10-02 실측) 합성 예외에는 `None`이라 빈 문자열이다.
                # 기계가 읽는 키는 `poc_write_error`다.
                "poc_write_error_detail": exc.strerror or "",
            }
        else:
            sidecar = {"poc_ref": ref, "poc_digest": digest}
    if reason is PovStopReason.SUBMITTED and poc is not None:
        ctx.trace.emit(
            EventKind.POV_GENERATED,
            stage=Stage.VALIDATE,
            subject_kind="finding",
            subject_id=finding.finding_id,
            poc_bytes=len(poc),
            poc_sha256=hashlib.sha256(poc).hexdigest(),
            **sidecar,
            **(terminal or {}),
        )
        return poc, reason, ref, digest
    data: dict[str, TraceValue] = {"reason": reason.value}
    if poc is not None:
        data["poc_bytes"] = len(poc)
        data["poc_sha256"] = hashlib.sha256(poc).hexdigest()
        data.update(sidecar)
    data.update(terminal or {})
    ctx.trace.emit(
        EventKind.POV_SKIPPED,
        stage=Stage.VALIDATE,
        subject_kind="finding",
        subject_id=finding.finding_id,
        **data,
    )
    return poc, reason, ref, digest


def _symbol_signal_rank(location: Location, signal_lines: list[int], has_static: bool) -> int:
    """이 심볼의 정적 신호 순위 — 클수록 큐 상위 (#70). 순서에만 쓴다(등급 아님).

    - **2** = 신호 줄이 이 심볼 범위에 든다(정확히 여기에 붙은 히트).
    - **1** = artifact에 정적 신호는 있으나 이 심볼에 든 줄을 확인 못 했다. `Evidence.location`이
      `results[0]` 한 줄만 싣거나(#70 초석) 위치를 못 뽑은 신호(실 clang-tidy)라 같은 파일의
      다른 히트를 못 본 경우다. **1로 두는 것이 핵심이다** — artifact에 신호가 있는데 이
      심볼만 0으로 내리면 같은 파일의 두 번째 실히트가 신호 없는 심볼(0) 아래로 밀린다
      (#249 리뷰 HIGH-1). 신호 1건당 Evidence(#70 (a) — 미착수, 2026-10-12 닫음)가 오면 이 1이
      2/0으로 갈린다.
    - **0** = 이 artifact에 정적 신호가 없다.
    """
    if not has_static:
        return 0
    start = location.line_start
    if start is not None:
        end = location.line_end or start
        if any(start <= line <= end for line in signal_lines):
            return 2
    return 1


def _plan_targets(
    ctx: RunContext,
    artifacts: list[Artifact],
    graph: Evidence,
    state: _Accumulator,
    last: int,
    *,
    slice_refs: tuple[SliceRef, ...] | None = None,
) -> _TargetPlan:
    """2단계를 artifact마다 돌리고 4단계에 올릴 target 목록을 확정한다 (W2).

    **모델을 한 번 부르기 전에 끝난다.** 그래서 `target_plan`이 실린 뒤에 죽어도 분모가
    남고, 정적 신호로 **전역** 정렬을 할 수 있다 — artifact 하나를 끝까지 태우는
    구조에서는 뒤쪽 artifact의 신호를 아직 모르므로 앞쪽의 신호 없는 심볼이 먼저 돈다.

    도구 호출 횟수는 예전과 같다. 그때도 2단계가 artifact 전부에 대해 돌았다.

    **`slice_refs`가 있으면 슬라이스 1개가 타깃 1개다**(ADR 0012 결정 1) — 심볼 인덱스로
    타깃을 만들지 않고, 2단계는 슬라이스가 가리키는 artifact에만 돈다.
    """
    plan = _TargetPlan(analyzable_artifacts=len(artifacts))
    if last < Stage.SIGNALS:
        return plan
    if slice_refs is not None:
        _plan_slice_targets(ctx, plan, artifacts, graph, slice_refs)
        _order_and_cut(ctx, plan, state)
        return plan
    for artifact in artifacts:
        evidences, signal_lines, has_static = _artifact_signals(ctx, artifact, graph)
        if has_static:
            plan.signal_artifacts += 1
        locations = ctx.symbols_for(artifact.path)
        if locations is None:
            # 인덱스가 없다(`dev_fake`·버전 모름·깨짐). 파일 단위로 되돌아간다 — 후보를
            # 안 만들면 그 프로필의 run이 통째로 비고, 폴백은 W2 이전 동작이라 회귀가
            # 아니다. **되돌아갔다는 사실이 기록에 남는 것**이 새로 얻는 것이다. 심볼 줄이
            # 없으므로 `_symbol_signal_rank`가 rank 1(신호 있음)/0(없음)으로 떨어진다.
            plan.fallback_artifacts += 1
            locations = [Location(file=artifact.path)]
        elif not locations:
            # 인덱스는 있는데 이 파일에 심볼이 없다(`.py`·바이너리는 인덱서에 넘어가지
            # 않고, 함수 정의가 없는 `.h`도 여기로 온다). 후보를 만들지 않는다 — 대상 선정이
            # 새던 자리가 여기서 닫힌다(zlib run의 후보 32건 중 C 소스는 12건이었다).
            plan.no_symbol_artifacts += 1
            continue
        else:
            plan.symbol_targets += len(locations)
        plan.collected += [
            _Target(
                artifact=artifact,
                location=location,
                evidences=evidences,
                signal_rank=_symbol_signal_rank(location, signal_lines, has_static),
            )
            for location in locations
        ]
    _order_and_cut(ctx, plan, state)
    return plan


def _artifact_signals(
    ctx: RunContext, artifact: Artifact, graph: Evidence
) -> tuple[tuple[Evidence, ...], list[int], bool]:
    """artifact 하나의 2단계 — (그 artifact의 근거, 정적 신호가 걸린 줄들, 정적 신호 유무).

    **정적 신호만 순서에 쓴다.** `with_signal()`이 남기는 것에는 `build`(BUILD)도 있는데
    그쪽은 성공 자체가 신호라 **모든 artifact에 붙는다** — 그것까지 세면 정렬 키가 상수가
    되어 신호 순서가 아무 일도 하지 않는다.

    줄은 #70에서 채운 `Evidence.location`이다. 그 줄을 품는 심볼에만 신호를 귀속한다 —
    예전엔 artifact의 모든 심볼에 똑같이 붙었다. **점수 축과는 갈린다**:
    `fake_model._has_static_signal()`은 여전히 artifact 단위 evidence를 보므로 가산점은
    artifact 전체에 간다. 순서만 심볼 단위로 정밀해진다(#70 후속 — 미착수 · 등급 영향 0).
    """
    found = signals.with_signal(signals.run(ctx, workdir=artifact.path))
    static = [ev for ev in found if ev.kind is EvidenceKind.STATIC]
    signal_lines = [
        ev.location.line_start
        for ev in static
        if ev.location is not None and ev.location.line_start is not None
    ]
    return (graph, *found), signal_lines, bool(static)


def _plan_slice_targets(
    ctx: RunContext,
    plan: _TargetPlan,
    artifacts: list[Artifact],
    graph: Evidence,
    refs: tuple[SliceRef, ...],
) -> None:
    """슬라이스 1개 = 타깃 1개(ADR 0012 결정 1). 2단계는 슬라이스가 가리키는 artifact에만 한 번씩.

    entry 파일이 이 run의 분석 대상 artifact가 아니면 타깃을 못 세운다 — **세어서**
    `target_plan.unmatched_slices`에 남긴다. 슬라이서와 하네스가 다른 트리를 봤다는 신호라
    조용히 버리면 「슬라이스가 적었다」로 읽힌다.

    순위는 entry 줄로 매긴다(`_symbol_signal_rank`) — 히트가 entry 줄에 걸리면 2, 같은
    artifact의 다른 줄이면 1이다.
    ponytail: entry 한 줄만 본다 — 슬라이스 줄(`SliceRef.lines`)에 든 히트로 매기지 않는다.
    `candidate_max`가 슬라이스를 실제로 자르는 run이 오면 그 줄로 매긴다.
    """
    by_path = {artifact.path: artifact for artifact in artifacts}
    seen: dict[str, tuple[tuple[Evidence, ...], list[int], bool]] = {}
    for ref in refs:
        artifact = by_path.get(ref.location.file)
        if artifact is None:
            plan.unmatched_slices += 1
            continue
        if artifact.path not in seen:
            seen[artifact.path] = _artifact_signals(ctx, artifact, graph)
            plan.signal_artifacts += seen[artifact.path][2]
        evidences, signal_lines, has_static = seen[artifact.path]
        plan.slice_targets += 1
        plan.collected.append(
            _Target(
                artifact=artifact,
                location=ref.location,
                evidences=evidences,
                signal_rank=_symbol_signal_rank(ref.location, signal_lines, has_static),
                slice=ref,
            )
        )


def _order_and_cut(ctx: RunContext, plan: _TargetPlan, state: _Accumulator) -> None:
    """(a′) — 정적 신호를 게이트가 아니라 순서로 쓰고, 상한 초과분을 기록에 남긴다.

    정렬 키의 첫 항이 `-signal_rank`라 순위가 높은(히트 위치가 든) 심볼이 앞에 온다.
    나머지 세 항은 index.json의 순서와 같다 — 후보 순서가 곧 ID 발급 순서라 흔들리면
    두 run의 trace를 대조할 수 없다(engineering-guide.md §5).

    **신호는 심볼 단위다(#70), 단 3단이다.** 히트 줄이 든 심볼은 rank 2, 같은 artifact의
    나머지 심볼은 rank 1(신호는 있으나 이 심볼에 든 줄 미확인), 신호 없는 artifact는 0.
    rank 1을 두는 이유는 같은 파일의 두 번째 히트를 신호 없는 심볼 아래로 안 내리기
    위해서다(#249 리뷰). 위치 없는 신호(실 clang-tidy)·인덱스 없는 폴백은 rank 1로 떨어진다
    (artifact에 STATIC 신호가 있을 때 — 없으면 0).
    """
    plan.collected.sort(
        key=lambda item: (
            -item.signal_rank,
            item.location.file,
            item.location.line_start or 0,
            item.location.symbol or "",
            # entry가 같은 슬라이스끼리의 순서 — 슬라이서가 매긴 id 순(슬라이스 없으면 모두 "").
            item.slice_id or "",
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
        slice_targets=plan.slice_targets,
        unmatched_slices=plan.unmatched_slices,
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
        _emit_planned(ctx, item.location, slice_id=item.slice_id)
    # 팬아웃되는(deferred 아닌) 대상 키. 리포트가 「모든 자리가 실패해 분석되지 않은 대상 수」를
    # 이것에서 뺀다(#193 ①) — 후보를 하나도 못 낸 대상이 곧 그 수다.
    state.dispatched_targets = {
        _target_key(item.location, item.slice_id) for item in plan.targets
    }
    state.deferred = len(deferred)
    for item in deferred:
        _emit_skipped(
            ctx, item.location, stage=Stage.SIGNALS, reason=_REASON_OVER_MAX,
            slice_id=item.slice_id,
        )


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
    # 4단계 개시(hop0)가 채우고 정착이 읽는다 — 둘이 표에서 갈린 자리다(ADR 0012 결정 2).
    # `_step_scout_settle`이 이것 없이 불리면 배선 결함이다(`_StageOutOfOrder`).
    scout_session: scout.ScoutSession | None = None
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


def _target_key(location: Location, slice_id: str | None) -> _TargetKey:
    """캡슐 캐시 키. 같은 대상을 보는 풀 멤버 item들이 같은 키를 낸다.

    **`slice_id`가 키에 든다**(ADR 0012 결정 1). 슬라이스 타깃의 `location`은 entry라, entry가
    같은 두 슬라이스가 위치만으로는 한 키가 되어 둘째가 첫째의 캡슐·「분석됨」을 물려받는다.
    기본값을 두지 않는다 — 부르는 자리가 슬라이스를 잊으면 조용히 합쳐지기 때문이다.

    **튜플인 것은 의도다.** 예전에는 네 필드를 `::`로 이어 문자열로 접었는데, 구분자가
    값 안에 나타나면 서로 다른 대상이 같은 키가 된다. 캐시가 섞이면 **멤버가 다른
    대상의 캡슐을 보고** 그 사실이 기록 어디에도 안 남는다. 지금 필드 값으로 그 충돌이
    일어나는지는 안 셌다 — 튜플이면 **세어 볼 필요 자체가 없다**(PR #178 리뷰 L2).
    """
    return (location.file, location.symbol, location.line_start, location.line_end, slice_id)


def _cache_key_str(key: _TargetKey) -> str:
    """`_target_key` 튜플을 `cache` 이벤트의 문자열 라벨로. **캐시 조회 키가 아니라 이벤트
    라벨이다** — 충돌은 무해하다(hit ratio는 이벤트 수를 센다). 그래서 튜플을 그냥 잇는다.

    `slice_id`는 있을 때만 붙인다 — 슬라이스 없는 run의 라벨이 이 필드 이전과 같게 남는다."""
    *location, slice_id = key
    label = "::".join("" if part is None else str(part) for part in location)
    return label if slice_id is None else f"{label}::{slice_id}"


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

    **회계는 이제 샌 곳을 메운다(#193 ②).** 이 캐시가 `retrieval.run()` **앞에서** 단락하므로
    Retriever의 `on_cache` 훅을 지나지 않는데, 적중 분기가 **`ctx.record_cache(..., hit=True)`를
    직접 낸다** — 그래서 `cache` 이벤트 수가 캡슐 조립 시도 수와 같아지고 hit ratio가 실측
    0.667로 돌아온다(옛 단락에서는 9→5, 0.40으로 낮게 보였다). 러너의 캐시이므로 러너가
    이벤트를 낸다. `evaluation.md` §5.2가 capsule cache hit ratio를 지표로 들고 `LateCacheHook`이
    *"캐시 이벤트가 새면 hit ratio가 0으로 보인다"*고 적는 그 누출을 닫는다.

    역할 캡슐 조립~검증을 그 역할 모델의 토크나이저로 감싼다(#96 (b) 3b). 첫 멤버의
    카운터로 켠다 — 캡슐이 공유되므로 예산 컷도 그 하나로 정해진다. `counter_for_role`이
    `None`을 주면(fake·토크나이저 없음) `using_counter`가 아무것도 바꾸지 않아 기본
    카운터(글자수 근사)가 유지된다.

    **`capsule_budget_tokens`는 풀에서 마진으로 강제된다(#194).** 캐시 적중은 `model_validate`를
    다시 타지 않아 첫 멤버(qwen) 기준으로만 검증되는데, 계열이 다른 멤버는 같은 텍스트를 최대 ~11%
    더 센다(2026-09-13 ATOM 실측 — 그때 로스터의 mistral `scout_boundary`: 캡슐 합 +4%. 2026-09-21
    교체 뒤 Scout 세 자리는 모두 qwen 계열이다). 그래서 `nodes.retrieval`이 풀
    프로필에서 예산을 `_POOL_CAPSULE_MARGIN`(0.90)만큼 줄여 담는다 — 그 최대 갈림까지 세 자리
    전부 상한 안에 든다. 캡슐을 멤버마다 재계산하지 않고(공유 보장 유지) 담을 때 여유만 둔다.
    ⚠ **Fake 프로필에서는 전 역할이 같은 기본 카운터라 이 갈림이 없다** — 마진은 무해하다.
    """
    key = _target_key(item.target.location, item.target.slice_id)
    cached = state.capsules.get(key)
    if cached is not None:
        item.capsule = cached
        # 이 accumulator 캐시가 `retrieval.run()` 앞에서 단락하므로 Retriever의 `on_cache`를
        # 안 지난다 — 그 단락을 세지 않으면 `cache` 이벤트 수가 캡슐 조립 시도 수보다 적어
        # hit ratio가 낮게 보인다(풀 팬아웃 #178 후속·#193). 러너의 캐시이므로 러너가 낸다.
        # `counted_by`로 이 공유 캡슐이 **어느 역할 카운터로 계산됐나**를 실어, 멤버 2·3의
        # 토큰 회계 근거가 기록에서 안 지워지게 한다(#192 조건②).
        ctx.record_cache(_cache_key_str(key), hit=True, counted_by=state.capsule_counter.get(key))
        return True
    with using_counter(ctx.counter_for_role(item.role.value)):
        # 슬라이스 타깃이면 slice id를 넘긴다 — LIGHT 캡슐의 `target_function`이 슬라이스 렌더를
        # 세고, Retriever의 캐시가 entry가 같은 두 슬라이스를 가른다(#220 ②). ⚠ `input: source`
        # 선언(서빙 v4)의 Scout는 프롬프트에 함수 본문을 받으므로 이 토큰 수는 그 프롬프트와 다른
        # 것을 센다 — 캡슐 예산이 `SOURCE`를 안 묶는 것과 같은 한계다(#529).
        item.capsule = retrieval.run(
            ctx,
            target=item.target.location,
            level=CapsuleLevel.LIGHT,
            slice_id=item.target.slice_id,
        )
    state.capsules[key] = item.capsule
    state.capsule_counter[key] = item.role.value
    return True


def _step_scout_open(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """4단계 개시 — hop0만(ADR 0012 결정 2). 후보를 만들지 않는다. **항상 `True`다** —

    탈락 여부는 아직 안 정해진다(v1·v2 점수 경로도, v3 신호도 정착이 본다). 세션을
    `item`에 싣고, 이 좌석의 hop0 신호를 `state`에 target 키로 적는다 — 같은 target의
    **다른** 좌석이 이것을 동료 확인(정착)에서 읽는다.

    **여기서 `ctx.stage(Stage.SCOUT)`를 연다** — 노드(`scout.open_session`)는 더 이상 자기
    stage span을 안 열므로(개시·정착이 갈리면서 "언제 열고 닫나"가 노드 하나의 일이 아니게
    됐다), 이 표 항목이 그 자리를 대신한다. `run()`(직접 호출 경로)은 자기 span을 그대로 연다.
    """
    item.reached.stage = Stage.SCOUT
    if item.capsule is None:
        raise _StageOutOfOrder("4단계가 3단계보다 먼저 돌았다 — capsule이 비어 있다")
    with ctx.stage(Stage.SCOUT):
        session = scout.open_session(
            ctx,
            artifact=item.target.artifact,
            capsule=item.capsule,
            evidences=item.target.evidences,
            role=item.role,
            slice=item.target.slice,
        )
    item.scout_session = session
    key = _target_key(item.target.location, item.target.slice_id)
    state.hop0_signals.setdefault(key, {})[item.role] = session.signal
    return True


def _step_scout_settle(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """4단계 정착 — 동료 확인 → need_evidence 루프(ADR 0012 결정 2 · #95) → 판정 적용.

    승격되지 않은 후보는 여기서 멈춘다(`False`), `_step_scout`(하나였던 시절)과 같다.
    `_step_scout_open`과 같은 이유로 여기서 `ctx.stage(Stage.SCOUT)`를 연다 — need_evidence
    루프의 되묻기 호출(hop≥1)도 이 span 안에서 돈다.
    """
    item.reached.stage = Stage.SCOUT
    session = item.scout_session
    if session is None:
        raise _StageOutOfOrder("Scout 정착이 개시보다 먼저 돌았다 — scout_session이 비어 있다")
    with ctx.stage(Stage.SCOUT):
        candidate = scout.finish(ctx, _settle_scout_session(ctx, item, state, session))
    item.candidate = candidate
    state.candidates.append(candidate)
    return candidate.state is FindingState.TRIAGED


def _settle_scout_session(
    ctx: RunContext, item: _InFlight, state: _Accumulator, session: scout.ScoutSession
) -> scout.ScoutSession:
    """`finish()`에 넘길 최종 세션을 정한다 — 신호가 없으면(v1·v2) 손대지 않는다.

    v3(신호가 있음)면 hop0을 포함해 홉마다 `SCOUT_HOP`을 낸다. 신호가 NE가 아니면(review·
    reject) 그 자리에서 끝난다. NE면 동료 확인 → 아니면 need_evidence 루프(`_run_ne_hops`).
    """
    if session.signal is None:
        return session
    if session.signal is not ScoutSignal.NEED_EVIDENCE:
        _emit_scout_hop(ctx, item, session, stop_reason=session.signal.value)
        return session
    key = _target_key(item.target.location, item.target.slice_id)
    peers = state.hop0_signals.get(key, {})
    if any(signal is ScoutSignal.REVIEW for role, signal in peers.items() if role != item.role):
        _emit_scout_hop(ctx, item, session, stop_reason=NeStopReason.PEER_REVIEW.value)
        return session
    return _run_ne_hops(ctx, item, state, session)


# 한 홉이 RETRIEVED에 실을 수 있는 글자 상한(폭주 차단 — 토큰 상한이 아니다. 글자 상한은
# 토큰을 안 묶는다). `_AUDITOR_SOURCE_CHARS`와 값을 맞췄다(1-b 선례) — Auditor는
# `src/orchestrator`를 상수로 재사용하지만(같은 계층) resolver(`src/adapters`)는 이 값을
# import 할 수 없어(계층 규칙) 여기 새로 둔다.
_HOP_CHARS_MAX = 15_000

# 직전 홉 usage에서 뺄 여유. **설계값이고 미실측이다** — 추정이 빗나가면 크기 400이 나고
# `NeStopReason.CONTEXT_OVERFLOW`가 받친다. 첫 실대상 run이 이 값의 근거를 만든다
# (`_MAX_CONSECUTIVE_DROPS`와 같은 상태).
_HOP_TOKEN_MARGIN = 512

# Scout 좌석 글자/토큰 비의 **실측 하한**
# (docs/experiments/harness/2026-09-26-slice-smoke-atom.md:158 — 좌석별 3.28~4.23). 하한을
# 쓰는 것은 넘치는 쪽보다 못 미치는 쪽이 안전해서다 — 실제 글자/토큰 비가 더 크면 이 상한이
# 실제보다 적게 담아 넘기지 않는다. Auditor의 1.22~3.32와 다르다(토크나이저가 다르다,
# `_AUDITOR_SOURCE_CHARS` 주석). RETRIEVED 코드 블록만의 비는 안 쟀다 — 이 상한은 **계산값**이다.
_HOP_CHARS_PER_TOKEN = 3.28


def _ne_visible(
    slice_ref: SliceRef | None, shown: tuple[tuple[str, int], ...] | None = None
) -> dict[str, frozenset[int]]:
    """`ResolveQuery.visible` — **모델이 실제로 본** 줄을 파일별로 묶는다.

    슬라이스가 없는 target(`--slices` 없이 돈 run)은 `{}`다 — resolver가 "이미 SOURCE에
    있다"를 가를 근거가 없다는 뜻이지, 빈 파일 목록이 아니다.

    `input: source` 선언(#529)은 `shown`이 늘 있고 그 값은 **함수 본문 범위**의 줄이다 — 슬라이스가
    걸치지 않은 줄도 모델이 봤으니 `visible`이 되고, `SliceRef.lines` 전체는 쓰지 않는다.

    `shown`은 Scout 창 트림(`scout._fit_slice`, `scout_slice_trim`)이 실제로 보낸 (file, line)
    집합이다(세션의 `shown_lines`). 트림이 돌았으면 `SliceRef.lines` 전체가 아니라 이 집합을 써야
    한다 — 안 그러면 **모델이 못 본 줄**을 resolver가 "이미 SOURCE에 있다"로 답해 중복으로
    버린다(#375 M-2). `None`이면 안 잘렸다는 뜻이라 `SliceRef.lines` 전체를 쓴다.
    """
    if slice_ref is None:
        return {}
    pairs = slice_ref.lines if shown is None else shown
    grouped: dict[str, set[int]] = {}
    for file, line in pairs:
        grouped.setdefault(file, set()).add(line)
    return {file: frozenset(lines) for file, lines in grouped.items()}


def _ne_hop_max_chars(ctx: RunContext, role: Role, session: scout.ScoutSession) -> int:
    """이 홉이 resolver에 요청할 글자 상한 — `min(15,000, floor(남은 토큰 × 3.28))`.

    남은 토큰 = 창 − 이 역할의 출력 상한 − 직전 홉 usage(입력+출력) − 여유. 0 이하면
    0이다 — 호출자가 그 값을 보고 호출 없이 `context_overflow`로 선다.
    """
    model_key = ctx.config.model_key_for(role.value)
    context_limit = ctx.config.models[model_key].context_limit
    output_max = ctx.config.thresholds.output_max_for(role.value)
    remaining_tokens = (
        context_limit
        - output_max
        - session.usage.input_tokens
        - session.usage.output_tokens
        - _HOP_TOKEN_MARGIN
    )
    if remaining_tokens <= 0:
        return 0
    return min(_HOP_CHARS_MAX, int(remaining_tokens * _HOP_CHARS_PER_TOKEN))


def _ne_classify_exception(exc: Exception) -> NeStopReason | None:
    """hop≥1 예외를 흡수 여부 표로 가른다(ADR 0012 결정 2). `None`이면 **흡수하지 않는다** —

    크기가 아닌 400(`_rejects_every_request`)뿐이다. 다음 요청도 같으므로 흡수하면 #296이
    막은 조용한 전량 실패가 되살아난다 — 호출자가 그대로 올려 러너 핸들러(`_drop`)가
    run을 멈추게 한다.
    """
    if isinstance(exc, BudgetExhausted):
        return NeStopReason.BUDGET_EXHAUSTED
    if isinstance(exc, SchemaParseError):
        return NeStopReason.PARSE_FAILED
    if isinstance(exc, ModelGatewayError):
        if _is_size_rejection(exc):
            return NeStopReason.CONTEXT_OVERFLOW
        if _rejects_every_request(exc):
            return None
        return NeStopReason.GATEWAY_ERROR
    return None


def _requests_json(requests: tuple[object, ...]) -> str:
    return json.dumps(
        [{"kind": r.kind, "target": r.target} for r in requests], ensure_ascii=False  # type: ignore[attr-defined]
    )


def _usage_json(usage: object) -> str:
    return json.dumps(
        {
            "input_tokens": usage.input_tokens,  # type: ignore[attr-defined]
            "output_tokens": usage.output_tokens,  # type: ignore[attr-defined]
            "cached_tokens": usage.cached_tokens,  # type: ignore[attr-defined]
        }
    )


def _emit_scout_hop(
    ctx: RunContext,
    item: _InFlight,
    session: scout.ScoutSession,
    *,
    stop_reason: str | None = None,
    resolution: Resolution | None = None,
    failed_hop: tuple[int, Exception, float] | None = None,
) -> None:
    """`SCOUT_HOP` 이벤트 하나 — hop0을 포함해 홉마다(신호가 있는 v3 좌석만).

    `subject_id`(candidate_id)·`role`(`data`)·`hop`(`data`)이 조인 키다 — 같은 키의
    `model_call.prompt_ref`로 붙는다(#95). `followup_text`는 이벤트에 경로를 새로 싣지
    않는다 — 그 조인만으로 로그를 다시 찾을 수 있다.

    `failed_hop`이 있으면 **그 홉의 시도 자체가 실패했다**(흡수된 hop≥1 예외) —
    `session`은 그 직전(성공한 마지막) 홉의 것이고, `hop`·`schema_ok`·`raw_text`·`requests`·
    `usage`는 실패한 시도의 것으로 따로 채운다(성공한 응답이 없어 `session`에서 못 읽는다).
    """
    if failed_hop is not None:
        hop, exc, latency_s = failed_hop
        usage = getattr(exc, "usage", None)
        data: dict[str, object] = {
            "hop": hop,
            "role": item.role.value,
            "signal": session.signal.value if session.signal is not None else None,
            "schema_ok": not isinstance(exc, SchemaParseError),
            "requests": "[]",
            "raw_text": None,
            "latency_s": latency_s,
            "usage": None if usage is None else _usage_json(usage),
        }
    else:
        data = {
            "hop": session.hop,
            "role": item.role.value,
            "signal": session.signal.value if session.signal is not None else None,
            "schema_ok": True,
            "requests": _requests_json(session.requested_context),
            "raw_text": session.raw_text,
            "latency_s": session.latency_s,
            "usage": _usage_json(session.usage),
        }
    if stop_reason is not None:
        data["stop_reason"] = stop_reason
    if resolution is not None:
        data["resolved"] = len(resolution.blocks) + len(resolution.already)
        data["already"] = len(resolution.already)
        data["fixable_count"] = resolution.fixable_count
        data["unfilled"] = ",".join(item_.reason.value for item_ in resolution.unfilled)
        data["truncated"] = ",".join(resolution.truncated)
        # 색인이 없어 조회를 안 했는지 — `nothing_resolved`의 두 원인을 가른다(#315).
        data["index_available"] = resolution.index_available
    ctx.trace.emit(
        EventKind.SCOUT_HOP,
        stage=Stage.SCOUT,
        subject_kind="candidate",
        subject_id=session.candidate_id,
        **data,
    )


def _run_ne_hops(
    ctx: RunContext, item: _InFlight, state: _Accumulator, session: scout.ScoutSession
) -> scout.ScoutSession:
    """need_evidence 루프 본체 — hop1..`max_hops`. 정지하면 그 판정의 세션을 돌려준다.

    **정지 판정 순서**(hop k+1을 부르기 전에 본다) — 1. 신호가 NE가 아님(호출자가 이미
    걸렀다, 여기서는 hop≥1 응답이 NE를 벗어난 경우) 2. 빈 요청 3. 직전 홉과 같은
    (kind, target) 집합 4. resolver 없음 또는 아무것도 못 채움 — "잘 쓴 이름이 전부 조회에
    실패했다"와 "형식이 잘못됐다"를 가른다(`Resolution.fixable_count`) 5. `max_hops` 소진.
    글자 상한(`_ne_hop_max_chars`)이 만드는 "호출 없이 context_overflow" 두 경우(남은 토큰
    ≤ 0, 블록 0인데 truncated 있음)는 4의 처리 안에 끼운다. hop k+1 호출 자체가 실패하면
    (게이트웨이·스키마·예산) 별도 표로 흡수 여부를 가른다(`_ne_classify_exception`).
    """
    max_hops = ctx.config.thresholds.max_hops
    previous_keys: frozenset[tuple[str, str]] | None = None
    target_file = item.target.location.file
    # 트림이 돌았으면 모델이 본 줄만 — `SliceRef.lines` 전체가 아니다(#375 M-2).
    visible = _ne_visible(item.target.slice, session.shown_lines)
    while True:
        requests = session.requested_context
        if session.signal is not ScoutSignal.NEED_EVIDENCE:
            # hop≥1 응답이 review·reject로 돌아섰다 — 최종 신호로 끝난다.
            _emit_scout_hop(ctx, item, session, stop_reason=session.signal.value)
            return session
        if not requests:
            _emit_scout_hop(ctx, item, session, stop_reason=NeStopReason.EMPTY_REQUEST.value)
            return session
        keys = frozenset((r.kind, r.target) for r in requests)
        if previous_keys is not None and keys == previous_keys:
            _emit_scout_hop(ctx, item, session, stop_reason=NeStopReason.REPEAT_REQUEST.value)
            return session
        if ctx.resolve_requests is None:
            _emit_scout_hop(ctx, item, session, stop_reason=NeStopReason.NOTHING_RESOLVED.value)
            return session
        max_chars = _ne_hop_max_chars(ctx, item.role, session)
        if max_chars <= 0:
            _emit_scout_hop(ctx, item, session, stop_reason=NeStopReason.CONTEXT_OVERFLOW.value)
            return session
        resolution = ctx.resolve_requests(
            ResolveQuery(
                requests=requests,
                hop=session.hop + 1,
                target_file=target_file,
                visible=visible,
                max_chars=max_chars,
            )
        )
        if not resolution.blocks and resolution.truncated:
            # 채울 만한 것을 찾았는데 상한 때문에 하나도 못 실었다 — 크기 문제다.
            _emit_scout_hop(
                ctx,
                item,
                session,
                stop_reason=NeStopReason.CONTEXT_OVERFLOW.value,
                resolution=resolution,
            )
            return session
        resolved = len(resolution.blocks) + len(resolution.already)
        if resolved == 0 and resolution.fixable_count == 0:
            _emit_scout_hop(
                ctx,
                item,
                session,
                stop_reason=NeStopReason.NOTHING_RESOLVED.value,
                resolution=resolution,
            )
            return session
        if session.hop >= max_hops:
            _emit_scout_hop(
                ctx, item, session, stop_reason=NeStopReason.MAX_HOP.value, resolution=resolution
            )
            return session
        # **이 홉(session.hop) 자신의 이벤트를 hop k+1을 시도하기 전에 낸다.** hop k+1
        # 호출이 실패하면(흡수하든 안 하든) 그 실패는 이 홉의 응답과는 다른 사건이고,
        # 특히 흡수하지 않는 경우 아래서 그대로 올려 run이 멈춘다 — 그 전에 이 홉의
        # 이벤트가 이미 안 나갔으면 이 좌석은 SCOUT_HOP이 하나도 없는 채로 run이 선다.
        _emit_scout_hop(ctx, item, session, resolution=resolution)
        started = ctx.monotonic_ms()
        try:
            next_session = scout.continue_session(ctx, session, resolution.followup_text)
        except (ModelGatewayError, SchemaParseError, BudgetExhausted) as exc:
            absorbed = _ne_classify_exception(exc)
            if absorbed is None:
                # 크기 아닌 400이다 — 흡수하지 않는다. 그대로 올려 러너 핸들러(`_drop`)가
                # run을 멈추게 한다(#296). 이 홉(session.hop) 자신의 이벤트는 바로 위에서
                # 이미 났다 — 실패한 것은 그다음 시도(hop k+1)다.
                raise
            state.ne_absorbed[absorbed.value] = state.ne_absorbed.get(absorbed.value, 0) + 1
            latency_s = (ctx.monotonic_ms() - started) / 1000.0
            _emit_scout_hop(
                ctx,
                item,
                session,
                stop_reason=absorbed.value,
                failed_hop=(session.hop + 1, exc, latency_s),
            )
            return session
        if next_session.signal is None:
            # hop k+1 응답이 스키마는 통과했지만(`ScoutResponse.candidate.reason_codes[0]`)
            # `ScoutSignal` 세 접두 밖이다 — `signal_of`가 `None`을 준다. hop0은 호출자
            # (`_settle_scout_session`)가 이미 NE로 걸러 여기 안 오므로 이 분기는 hop≥1뿐이다.
            # 판정으로 쓸 수 없으니 파싱 실패와 같은 부류로 흡수한다: 직전 홉(`session`,
            # 아직 재대입 전)의 판정으로 finish하고, 이 홉(`next_session`) 자신의 `SCOUT_HOP`은
            # 따로 낸다. `schema_ok`는 참으로 남는다(구조화 출력 자체는 파싱됐다) — 스키마
            # 실패(`parse_failed`의 다른 경로, `_ne_classify_exception`)와 신호 이탈이
            # 기록에서 갈린다. 흡수하지 않으면 다음 반복에서 `session.signal.value`가
            # `None.value`를 불러 run 전체가 `AttributeError`로 죽는다.
            reason = NeStopReason.PARSE_FAILED.value
            state.ne_absorbed[reason] = state.ne_absorbed.get(reason, 0) + 1
            _emit_scout_hop(ctx, item, next_session, stop_reason=reason)
            return session
        session = next_session
        previous_keys = keys


def _step_auditor(ctx: RunContext, item: _InFlight, state: _Accumulator) -> bool:
    """5단계 — EXPANDED 캡슐 + 심층 분석.

    auditor는 EXPANDED 캡슐을 보므로 auditor 카운터로 켠다. 역할마다 모델(=토크나이저)이
    다를 수 있어 scout와 따로 감싼다 — 조립과 `CapsuleSection` 검증이 같은 카운터를 봐야
    한다(`src/tokens.py` docstring).

    슬라이스 타깃이면 캡슐의 대상이 후보 위치(entry)가 아니라 **entry를 감싸는 함수**다
    (`_auditor_target`). `SOURCE`는 그 함수에 슬라이스가 걸친 다른 함수들을 이어 붙인 것이다
    (`_auditor_source`). 후보의 위치는 그대로 entry다.

    도구 루프(이슈 #419)가 Finding 없이 서면(`AuditorHopLimit`) **그 후보만 `False`로 멈춘다**(아래
    `except`) — Finding이 없으니 6단계 게이트로 갈 것이 없고, 후보는 노드가 이미 `queued`로 내렸다.
    ⏵ 2026-10-13(#514) 루프 안의 모델 호출이 크기 거절당하면(`context_overflow`) 노드가 그 홉
    이벤트와 C-5를 단 뒤 **게이트웨이 예외를 그대로 올린다** - 이 함수는 받지 않고 호출자의
    `_drop`이 그 후보만 버린다(`target_skipped`).
    """
    item.reached.stage = Stage.AUDITOR
    candidate = item.candidate
    if candidate is None:
        raise _StageOutOfOrder("5단계가 4단계보다 먼저 돌았다 — candidate가 비어 있다")
    evidences, artifact = item.target.evidences, item.target.artifact
    anchor, ref = _auditor_target(ctx, item.target), item.target.slice
    with using_counter(ctx.counter_for_role(Role.AUDITOR.value)):
        expanded = retrieval.run(ctx, target=anchor, level=CapsuleLevel.EXPANDED)
    # 콜러블이라 노드가 5단계 구간 안에서 부른다(`auditor.run` docstring). `_Once`로 감싸는 이유는
    # **8단계 PoV가 같은 문자열을 다시 쓰기 때문**이다(아래 `state.finding_sources`).
    source = (
        _Once(partial(ctx.read_source, expanded.target))
        if ref is None
        else _Once(partial(_auditor_source, ctx, ref, anchor, candidate.candidate_id))
    )
    # 도구 루프가 홉마다 본 관측을 받는 통로 — Finding과 함께 7단계 Critic에 물려 준다(#600 H2ⓐ).
    observations: list[str] = []
    try:
        finding, missings = auditor.run(
            ctx,
            candidate=candidate,
            capsule=expanded,
            evidences=evidences,
            source=source,
            observations=observations,
            # 노드는 artifact 루트를 모른다(캡슐 대상은 파일 경로뿐) — 러너가 넘긴다. 도구 루프의
            # `probe`가 이 디렉터리에서 도구를 돌린다. 아래 `state.workdirs`와 같은 값이다.
            workdir=artifact.path,
        )
    except auditor.AuditorHopLimit:
        # **도구 루프가 Finding 없이 섰다**(같은 요청 반복 · 조사만으로 상한 소진 — 이슈 #419). 둘
        # 다 모델이 낸 **정상적인 답**이라 한 후보의 정지일 뿐이다 — 노드가 이미 후보를 `queued`로
        # 내렸고 (`_queue_candidate`) 정지 사유는 trace `auditor_hop.stop_reason`이 든다. 여기서 안
        # 받으면 `run_pipeline`의 최후 방어선이 `UNFORESEEN_FAILURE`로 run 전체를 멈춘다: 100후보
        # run에서 첫 반복 요청 하나가 나머지 99후보를 감사도 못 하게 지운다.
        # `ModelGatewayError`·`SchemaParseError`처럼 `_drop`으로 세지 않는다 — 그 카운터는 **체계적
        # 고장**을 재는 값이고 이 정지는 모델 호출의 실패가 아니다. 다만 `False`는 이 후보가
        # Finding을 못 냈다는 뜻이라 다음 단계(6 게이트)로 안 간다. 모든 후보가 이렇게 서면 5단계는
        # 「응답은 했는데 아무도 못 끝냈다」이고 run은 멈추지 않는다 — 그 비율은 `auditor_hop`의
        # `stop_reason`이 센다.
        return False
    item.finding, item.missings = finding, tuple(missings)
    state.update(finding)
    state.evidences[finding.finding_id] = evidences
    state.missings[finding.finding_id] = tuple(missings)
    state.workdirs[finding.finding_id] = artifact.path
    # 8단계 PoV가 이 Finding의 PoC를 만들 때 쓸 캡슐을 물려 준다(PoV 배선 — b-1).
    state.finding_capsules[finding.finding_id] = expanded
    # 🔴 **그리고 Auditor가 본 `SOURCE`를 그대로 물려 준다**(사용자 지적 2026-10-02).
    # 안 물려 주던 동안 PoV는 `ctx.read_source(capsule.target)`으로 **entry 함수 하나**만 봤다
    # (`nodes/pov._render_prompt`의 `source is None` 갈래) — Auditor는 결정 1-b = 넓은 (i)로
    # 슬라이스가 걸친 함수 전부를 보는데 **바이트를 써야 하는 쪽이 그보다 좁게 봤다.** CyberGym
    # Level 1의 지배적 실패가 "취약 경로 미도달"이고 그것을 가르는 것이 앞단 파서의 포맷 조건이라,
    # 포맷을 담은 함수가 entry 밖에 있으면 PoV가 영영 못 봤다.
    # 비슬라이스도 Auditor가 처음 읽은 문자열을 재사용한다(사용자 선택 B 보완안, 2026-10-05).
    state.finding_sources[finding.finding_id] = source
    state.finding_observations[finding.finding_id] = tuple(observations)
    return True


# entry를 품는 심볼을 모를 때 Auditor가 읽는 entry 앞뒤 줄 수. 값과 근거는 `entry_scope`가 든다 —
# Scout(#529)와 같은 상수다. 테스트가 이 이름을 가져다 쓰고 `_auditor_target`이 호출 때 읽는다.
_AUDITOR_WINDOW_LINES = ENTRY_WINDOW_LINES


def _auditor_target(ctx: RunContext, target: _Target) -> Location:
    """5단계 캡슐의 대상. 슬라이스 타깃이면 entry 줄을 감싸는 함수, 아니면 타깃 위치 그대로.

    **Auditor는 슬라이스가 아니라 원본을 본다** — ADR 0012 결정 1-b = 넓은 (i)(2026-09-25 팀 합의.
    Auditor 프롬프트가 입력을 `original source code`로 선언한다). 슬라이스 타깃의 위치는 entry(줄
    하나, `line_end` 없음)라 그대로 두면 Auditor가 그 한 줄만 읽는다 — 함수 머리도 버퍼 선언도 없이.

    감싸는 함수는 인덱스(`symbols_for`)에서 entry 줄을 품는 심볼이고, 여럿이면 가장 좁은 것이다.
    품는 심볼을 모르면 **entry 앞뒤 `_AUDITOR_WINDOW_LINES`줄**이다 — 파일 전체가 아니다. 인덱스가
    없을 때만이 아니라 **있어도** 그렇다: entry가 어느 정의에도 안 드는 줄(프로토타입·전역·
    매크로 — 2026-09-26 표본 셋에서 헤더에 entry가 있는 슬라이스 7개가 전부 프로토타입 한
    줄이었다)이면 `symbols_for()`가 품는 심볼을 못 주고, 이름을 못 뽑은 정의도 인덱서가 버린다.
    파일 전체는 상한 없이 프롬프트에 실려 위 모듈 docstring의 unzip.c(67,662자 → HTTP 400)를
    다시 연다. W2는 같은 `[]`에서 후보를 아예 안
    만들지만 슬라이스 타깃은 이미 서 있다. 창의 `line_end`는 파일 끝을 넘을 수 있다 — 읽기가 자른다.

    이것은 **캡슐의 대상**이고 `SOURCE`의 첫 함수다 — 슬라이스가 걸친 다른 함수는
    `_auditor_source`가 그 뒤에 붙인다(결정 1-b = 넓은 (i)).
    """
    if target.slice is None:
        return target.location
    entry = target.slice.entry
    # 공용 헬퍼(`entry_scope`) — Scout `_ask_model`(#529)도 같은 규칙으로 entry를 넓힌다.
    return enclosing_scope(ctx.symbols_for(entry.file), entry, _AUDITOR_WINDOW_LINES)


# Auditor `SOURCE`의 문자 수 상한 — 슬라이스가 걸친 함수를 앞에서부터 넣다가 넘치는 함수는 뺀다
# (entry 함수는 늘 넣는다 — `_auditor_source`). 15,000은 계산으로 골랐고 **그 뒤 실서빙에서 쟀다.**
#
# **[실측] 이 상한으로 실제 Auditor에 태운 147호출이 입력 몫 안에 들었다 — 초과 0건.**
# 2026-09-25 ATOM(`atom-host`) · `qwen3.6-35b-a3b` 프롬프트 v1 · 프로필 `atom_slice_auditor` ·
# 브랜치 `exp/atom-slice-smoke` @ `41f802a`(머지 안 함) · run `run_20260925_0002`·`0003`·`0004`
# (표본 셋 v2 슬라이스 241개 중 Scout 임계를 넘은 147개). 서버가 센 `input_tokens`가 최대 **15,437**
# (p50 3,277)이고 입력 몫 20,480에 **여유 5,043토큰**이다. 그 몫은 서빙 `--max-model-len` 24,576에서
# `output_max` 4,096을 뺀 값이다. ⚠ 그 run의 실서빙 키는 `checksum`이 0이라 비교표 밖이다(§0).
#
# ⚠ **글자 수가 토큰 수를 묶어 주지 않는다 — 이 상한의 한계다.** 같은 측정에서 글자/토큰 비가
# **1.22~3.32**로 벌어졌고(2026-09-24 zlib에서 잰 2.51~2.97보다 훨씬 넓다), **글자가 가장 큰
# 프롬프트가 토큰이 가장 큰 프롬프트가 아니었다** — 37,081자가 13,944토큰이고 20,917자가
# 15,437토큰이다. 그래서 「15,000자가 몇 토큰인가」로는 못 잡는다. 이 상한이 실제로 하는 일은
# **폭주 차단**이다 — 상한이 없으면 SOURCE가 최대 412,554자였다(표본 셋, 2026-09-25 로컬 실측).
# 드는 근거는 위 실측이지 글자에서 토큰으로 가는 환산이 아니다.
#
# ⚠ 그 147호출은 **프롬프트 전체**를 잰 것이고 `SOURCE`는 그 일부다 — 같은 run의 SOURCE는 최대
# 14,995자였다(상한에 걸린 슬라이스 5 · 뺀 함수 249). entry 함수는 상한을 안 받는다
# (`_auditor_source`의 ⚠ · `anchor_chars`).
#
# ⚠ **2026-09-29 — 위 [실측]은 프롬프트 v1의 것이다.** registry의 Auditor가 v3가 되며(근거 본문
# 배선과 같은 변경) 입력이 +3,212~8,740토큰 늘 수 있다 — **계산값**이다: prefix +6,469자 · body 틀
# +194자 · FACTS의 근거 본문 최대 4,000자(JSON 이스케이프 전) = 10,663자를 위 글자/토큰 비
# 1.22~3.32로 나눴다. 그 측정의 입력 몫(20,480)이면 여유 5,043을 넘을 수 있고, 지금 넘치는지는
# 서빙 창에 달렸다(`config/models.yaml`의 `qwen3.8-27b` 기동 조건). **v3의 입력 토큰은 안
# 쟀다(미실측)** — 이 상한이 v3에서도 입력 몫에 든다는 실측은 없다. 첫 v3 run의 `input_tokens`
# 최대가 그 답이다.
# ⏵ 2026-09-29 — 같은 날 Auditor가 v4가 됐다. 같은 계산으로 +4,923~13,396토큰(계산값 — prefix
# +12,251자 · body 틀 +92자 · 근거 본문 4,000자 = 16,343자). v4 입력 토큰은 미실측이다
# (`config/models.yaml`의 `qwen3.8-27b` — v3는 스모크가 2호출로 쟀다, 그 항목의 ⏵).
# ⏵ 2026-10-03 — **v6가 됐다**(#418·#421). 같은 계산으로 **+7,330~19,948토큰**(계산값 — prefix
# +20,244자 · body 틀 +92자 · 근거 본문 4,000자 = 24,336자). **v4로 돈 run이 0건이라 위 v4 수는
# 끝까지 미실측이고, v6도 0건이다** — 둘 다 `docs/experiments/` grep(2026-10-03)이고 `artifacts/`는
# gitignore라 **커밋된 보고서만 센 것**이다. ⚠ v6 자체는 **하네스 밖에서 돌았다**(#418 본문의
# 비교표 — 실험팀 프롬프트 A/B). 여기서 0건이라는 것은 **이 저장소의 run 기록**에 대한 말이다.
# ⚠ **창 53,248(입력 몫 20,480)이면 v6는 하한에서도 넘는다** — 15,437 + 7,330 = 22,767.
# v4는 그 창에서 경계에 걸쳤고(20,360) v6는 더 들어간다. 아래 ⚠의 영어 prefix 비 2.29로 잡아도
# 15,437 + 10,627 = 26,064다. **다만 「항상 넘는다」로 적지 않는다** — 넘지 않으려면 혼합 비가
# 24,336/5,043 ≈ 4.83을 넘어야 하고, 근거 본문 4,000자는 **상한이라 0일 수도 있어** 그때 문턱이
# 20,336/5,043 ≈ 4.03으로 내려간다. **영어 텍스트의 비를 4 아래로 묶은 실측이 없다**(가진 것은
# v3 스모크의 「2.29 이상」 하나다) — 그래서 이것은 **추정이고 상한 증명이 아니다.**
# 창이 `context_limit` 262,144면 입력 몫 196,608이라 상한 35,385(= 15,437 + 19,948)도 안쪽이고,
# ⏵ 2026-10-03 — 그 입력 몫은 출력 상한 인상(32,768 → 65,536) 뒤의 값이다. 그 전은 229,376이었다.
# 53,248은 그 전 계획값이라 지금 경로가 아니다.
# ⏵ 2026-10-04 — **v7이 됐다**(v6 + SOURCE 옵션 넷의 서술, #349). prefix가 v6보다 +2,050자 더
# 길고(23,448자 · ⏵ 같은 날 래퍼·별칭 서술을 더해 23,950자 · ⏵ 간접 피호출·쓰기 지점 창 서술을 더해
# 25,045자) SOURCE 상한이 토큰이 됐다 — 그
# 계산은 `config/models.yaml`의 `qwen3.8-27b` 항목이
# 든다. v7 입력 토큰도 미실측이다.
# ⏵ 2026-10-09 — 그때 서빙 판은 v10이었다(prefix 56,906자 — v7의 2.2배). 같은 식의 hop0 상한은
# 33,462~64,489토큰(계산값 · 그 계산은 `config/models.yaml`의 같은 항목, #556)이라 입력 몫 196,608
# 안이다. 도구 루프는 홉마다 history가 쌓여 뒤 홉이 더 크다. v10 입력 토큰도 미실측이다.
# ⏵ 2026-10-09 — 서빙 판은 v11이다(이슈 #525 — prefix 56,872자로 v10보다 34자 짧다). 위 hop0
# 상한은 v10으로 센 그대로다(다시 안 셌다 — 짧아진 쪽이라 상한 쪽 계산값으로는 남는다).
# ⏵ 2026-10-10 — 서빙 판은 v13이다(이슈 #67 — v11 + FACTS에서 `source` 사본을 뺀다). hop0 user 턴이
# 원문의 JSON 이스케이프 사본만큼 짧아진다 — 위 상한은 다시 안 셌다(같은 이유로 상한 쪽 계산값으로는
# 남는다).
# ⏵ 2026-10-11 — 서빙 판은 v14다(이슈 #67 — v12 + FACTS에서 `source` 사본을 뺀다). hop0 user 턴은
# v13과
# 같고 prefix는 v12 쪽이다 — 위 상한은 다시 안 셌다.
# ⏵ 2026-10-13 — 서빙 판은 v15다(S19 — v14 + 계약 문장 여섯). hop0 user 턴(body)은 v14와 바이트
# 같고 prefix만 다르다 — 위 상한은 다시 안 셌다.
# ⚠ 위 비 1.22~3.32는 v1 입력(한글 섞인 prefix · `qwen3.6-35b-a3b`)의 것이다 —
# v3 스모크(영어 prefix)는 비가 2.29 이상(계산값)이라 위 상한은 영어 prefix 몫에는
# 보수적일 수 있다(같은 항목의 ⏵).
# ⏵ 2026-10-04 — FACTS의 evidence 항목마다 `location` 키(없으면 `null`)가 더 실린다(이슈 #475) —
# 위 합에 미산입이다. 수치는 바꾸지 않았다(새 계산값을 지어내지 않는다).
# ponytail: registry가 서빙 `--max-model-len`을 들게 되면 거기서 유도한다(40,960 기동이면 커진다).
_AUDITOR_SOURCE_CHARS = 15_000

# 피호출 함수 본문 하나의 글자 상한(#349 (a) — `auditor_callee_hops`가 1일 때만 쓰인다). 짧은
# 검증·길이 계산·래퍼 함수를 겨냥한다 — 큰 함수 하나가 `_AUDITOR_SOURCE_CHARS`를 다 먹어 다른
# 피호출 함수가 못 들어가는 것을 막는다. **정한 값이지 잰 값이 아니다** — s500 재측정에서 본다.
_AUDITOR_CALLEE_CHARS = 4_000
# ⚠ 이 상한과 아래 `_AUDITOR_DEFINITION_CHARS`는 `auditor_source_tokens`가 양수여도 **글자**다 —
# 하나가 너무 큰지를 거르는 값이고, 전체 상한만 토큰으로 센다.

# 정의(매크로·타입·파일 변수·enum 상수) 하나의 글자 상한(#349 (f) — `auditor_definitions`가 1일
# 때만). 큰 구조체·초기화 테이블이 상한을 먹는 것을 막는다. **정한 값이지 잰 값이 아니다.**
_AUDITOR_DEFINITION_CHARS = 2_000
# 큰 enum(상한을 넘는 enum 상수의 정의)은 통째로 버리지 않고 머리 줄 · 물은 상수마다 그 줄
# 앞뒤 이 줄 수 · 닫는 줄만 남겨 싣는다(`_trim_enum`). index-v4가 enum 상수를 enum 정의 전체로 싣자
# 「크기 초과로 버림」이 29 -> 142로 늘었다(2026-10-04 로컬 274표본 · run ID 없음). **정한
# 값이지 잰 값이 아니다.**
_AUDITOR_ENUM_CONTEXT_LINES = 5
# `_trim_enum`이 enum 정의인지 보는 머리 패턴 — 첫 `{` 앞에 `enum` 낱말.
_ENUM_HEAD = re.compile(r"\benum\b")
# entry 함수를 줄일 때(#349 — `auditor_entry_trim`이 1이고 entry 함수가 상한을 넘을 때만) 슬라이스
# 줄·entry 줄 앞뒤로 남기는 줄 수. 함수 머리(여는 `{`까지, 앞 `_AUDITOR_ENTRY_HEAD_LINES`줄 안)와
# 닫는 줄은 늘 남긴다. **정한 값이지 잰 값이 아니다.**
_AUDITOR_ENTRY_CONTEXT_LINES = 20
_AUDITOR_ENTRY_HEAD_LINES = 10
# 줄인 entry 함수에서 뺀 구간 표시. 정의 조회(`source_definitions`)는 이 표시의 낱말을 안 본다.
_OMITTED = "/* ... {} lines omitted ... */"
_OMITTED_LINE = re.compile(r"/\* \.\.\. \d+ lines omitted \.\.\. \*/")
# 호출자 창을 붙이려고 **읽는** 호출 지점 수의 상한(#349 (b)) — 이미 실린 범위와 겹쳐 건너뛴
# 지점은 안 센다. 널리 불리는 함수면 호출 지점이 수백이라 상한이 찬 뒤에도 지점마다 파일을 읽고
# `callers_dropped`가 지점 수가 된다(#351 리뷰). 순서는 `call_sites_of`가 정한다(같은 파일 →
# 같은 디렉터리 → 나머지). **정한 값이지 잰 값이 아니다.**
_AUDITOR_CALLER_SITES = 8
# 래퍼 너머(C4 — `auditor_callee_wrappers`가 1일 때만). 붙인 피호출 함수가 이 줄 수 이하이고 그
# 본문이 정의를 아는 함수를 1~`_AUDITOR_WRAPPER_CALLS`개만 부르면 래퍼로 보고 그 함수들을
# 붙인다 — `gf_strdup` → `gf_mem_strdup` 같은 껍데기 뒤의 실제 구현. 래퍼 너머는
# `_AUDITOR_WRAPPER_DEPTH`단까지다. 매크로 별칭(`ND_TCHECK` → `ND_TCHECK2`)은
# `_AUDITOR_ALIAS_ROUNDS`번까지 다시 찾는다. **셋 다 정한 값이지 잰 값이 아니다**
# — 근거는 팀 검수 83표본의 래퍼 예(1~2단 · 이름만 바꿔 넘기는 짧은 함수)다.
_AUDITOR_WRAPPER_LINES = 10
_AUDITOR_WRAPPER_CALLS = 2
_AUDITOR_WRAPPER_DEPTH = 2
_AUDITOR_ALIAS_ROUNDS = 2
# 간접 피호출(C6 — `auditor_indirect_callees`). 실린 함수의 필드 경유 호출 하나(필드 하나)가 갈 수
# 있는 후보 중 아직 안 실린 것이 이보다 많으면 어느 것인지 몰라 **그 필드의** 후보는 하나도 안
# 붙인다 — `alloc`처럼 흔한 필드 이름이 여러 구현에 묶인 저장소에서 엉뚱한 본문이 쌓이지 않게.
# 상한은 필드마다다(PR #468 리뷰 MEDIUM-2 — 함수 단위 합이면 `s->read`(1)와 `s->write`(3)를 같이
# 부르는 함수에서 둘 다 버려졌다). **정한 값이지 잰 값이 아니다.**
_AUDITOR_INDIRECT_MAX = 3
# 쓰기 지점 창(C5(a) — `auditor_write_sites`)의 반폭. 쓰는 줄 앞뒤 이 줄 수를 그 함수 범위 안으로
# 자른다(`_caller_window`와 같은 창). 할당·길이 계산이 대개 그 줄 바로 앞뒤라 작게 둔다. 이미 실린
# 범위와 겹치면 쓰는 줄 쪽 남은 조각으로 더 자른다(`_clip_around`). **정한 값.**
_AUDITOR_WRITE_LINES = 3


def _is_short(location: Location) -> bool:
    """래퍼 판정의 길이 조건 — 범위를 모르면 래퍼로 안 본다."""
    if location.line_start is None or location.line_end is None:
        return False
    return location.line_end - location.line_start + 1 <= _AUDITOR_WRAPPER_LINES


def _auditor_source(ctx: RunContext, ref: SliceRef, anchor: Location, candidate_id: str) -> str:
    """슬라이스 타깃의 5단계 `SOURCE`. 노드가 5단계 구간 안에서 부른다(`_step_auditor`) — 슬라이스
    타깃이 아니면 부르지 않고 노드가 캡슐 대상(`anchor`)을 그대로 읽는다.

    결정 1-b = 넓은 (i)(2026-09-25 팀 합의): **슬라이스가 걸친 함수의 원본을 전부** 준다. 맨 앞이
    entry 함수(`anchor` — `_auditor_target`)이고, 나머지는 슬라이스 줄마다 그 줄을 품는 가장 좁은
    인덱스 심볼을 (파일, 시작 줄) 순으로 붙인다. 둘 이상이면 함수마다 `파일:시작-끝` 배너를 단다 —
    하나면 배너 없이 그 함수 그대로라 넓히기 전과 바이트가 같다.

    상한(`_AUDITOR_SOURCE_CHARS`)은 함수 단위다 — 앞에서부터 들어가는 함수를 넣고 넘치는 것은
    뺀다(중간을 자르면 끊긴 함수를 본다). entry 함수는 상한보다 커도 넣는다. `anchor` 범위 안의 줄은
    `anchor`가 덮고, 어느 심볼에도 안 드는 줄(구조체 필드·전역·프로토타입·매크로)은 `SOURCE`에 안
    싣고 센다 — 읽는 곳은 하나뿐이다: `auditor_definitions`가 1이면 정의 순서를 정하려고 슬라이스
    줄의 이름만 읽는다(아래 「정의」). 넣은 함수·뺀 함수·못 본 줄·글자 수는 `auditor_source`
    이벤트가 든다 — EXPANDED 캡슐의 `target_function`은 entry 함수만 센다.

    ⚠ **상한은 entry 함수를 안 본다** — 뒤에 붙는 함수만 거른다. 감싸는 함수가 거대하면 `SOURCE`
    하나가 상한과 무관히 커지고, 서빙 입력 몫을 넘으면 400으로 거절된다. 이 함수가 그 경로를 막지
    않으므로 **보이게만 한다** — `anchor_chars`가 entry 함수 혼자의 크기다(#289 리뷰 LOW-2).
    `chars`만으로는 함수가 둘 이상일 때 entry 혼자 때문인지 합 때문인지 안 갈린다. entry 함수에도
    상한을 둘지는 이 이벤트가 실서빙에서 내는 값을 보고 정한다.
    ⏵ `auditor_entry_trim`이 1이면 entry 함수가 상한을 넘을 때 줄인다(`_trim_entry`) — 그때
    `entry_omitted_lines`가 뺀 줄 수를 들고, `anchor_chars`는 줄이기 전 크기 그대로다.

    **GUARDS(`auditor_guards`)** — 1이고 sink를 아는 슬라이스(Case B)면 entry 함수 **바로 뒤에**
    `source_guards`가 훑은 sink 앞 가드 블록을 붙인다. entry 함수 몫이라 상한에 밀려 빠지지
    않고, 대신 그 크기를 **먼저** 세어 뒤에 붙는 함수가 그만큼 덜 든다(「+N more」 줄도 블록의
    일부다). 줄이기(`auditor_entry_trim`) **전**의 원문을 훑는다 — 잘려 안 보이는 검사도 행으로
    남는다. 함수가 아니라 `functions`에 안 들고, `guards`·`guards_dropped`가 따로 센다. **안 붙이는
    경우**(Case A · 품는 함수를 모르는 창 앵커 · 줄이 어긋날 수 있는 파일 · sink를 못 찾음)는 사유를
    `guards_skipped`가 든다. 0이면 이 단계가 없다.
    ⚠ **PoV도 이 블록을 받는다** — 아래 `_step_auditor`가 이 SOURCE 문자열을
    `state.finding_sources`로 물려 주고 8단계 PoV가 `pov._WIDE_INPUT_VERSIONS`인 판에서
    그대로 프롬프트에 싣는다. 옵션을 켜면 PoV 프롬프트에도 GUARDS가 든다.

    **정의(#349 (f))** — `auditor_definitions`가 1이면 위 함수들 바로 뒤(피호출 함수보다 앞)에,
    실린 함수가 쓰는 매크로·타입·파일 변수·enum 상수의 정의(`ctx.definitions_of`)를 붙인다. 정의
    하나가 `_AUDITOR_DEFINITION_CHARS`를 넘으면 안 싣고 전체 상한은 같이 받는다 — 단 **enum**은
    통째로 버리지 않고, 물은 상수마다 앞뒤 `_AUDITOR_ENUM_CONTEXT_LINES`줄만 남겨 블록 하나로 싣는다
    (`_trim_enum` — 같은 enum의 상수가 더 오면 그 블록을 넓힌다). 조회하는 이름은
    코드 자리의 것만이고(주석·문자열 속 낱말은 뺀다) 함수마다 **그 함수의** 지역·파라미터와 필드
    전용 이름도 뺀 것이며(`source_definitions.definition_names`), 슬라이스 줄에 나온 이름의 정의가
    먼저다. **조회는 본문의 파일마다 그 파일 기준(`near_file`)이다**(`_definitions_by_file`) — 한
    이름이 파일마다 다른 정의로 풀리면 둘 다 싣고, 본문이 모두 앵커 파일이면 본 조회는 한 번
    부른다(별칭 라운드는 따로다 — 별칭 대상은 그 별칭 매크로 정의를 푼 파일의 기준으로만 조회한다).
    이미 실린 enum 블록에서 줄 머리 상수가 아닌 이름(typedef 이름·태그)은 낱말로 보이면 보이는
    것으로 보고 세지 않는다(정의가 아니라 언급이어도 — `widen_enum`의 한계).
    이 위생은 `auditor_definitions`를 켠 run만 쓴다 — 정의·피호출·호출자 조회가 모두
    비제품 경로(`tests/`·`examples/` 등)를 한 단계 낮춰 「하나뿐」을 정한다(`demote_nonproduct`).
    붙인 수·뺀 수는 `definitions`·`definitions_dropped`가 들고, 그중 잘라 실은 enum 수는
    `definitions_trimmed`가 든다(SOURCE에 실린 것만 센다). 0이면 이 단계가 없고 `SOURCE`·
    이벤트는 옛 모양 그대로다.

    **피호출 함수 본문(#349 (a))** — `auditor_callee_hops`가 1이면 위 함수들 뒤에, 실린 함수의 본문
    안 호출이 부르는 함수의 본문(`ctx.callees_of`)을 붙인다. entry 함수의 피호출 함수가 먼저,
    그다음 실린 순서대로다. 이미 실린 범위와 겹치는 것·`_AUDITOR_CALLEE_CHARS`를 넘는 것은
    싣지 않고, 전체 상한은 같이 받는다. 붙인 수·뺀 수는 `callees`·`callees_dropped`가 든다.
    0이면 이 단계가 없고 `SOURCE`·이벤트는 옛 모양 그대로다(결정 1-b).

    **래퍼·별칭 너머(C4)** — `auditor_callee_wrappers`가 1이면 붙인 피호출 함수가 짧은 래퍼일 때
    그 너머 함수를, 실은 매크로가 이름만 바꿔 넘기면 그 대상 정의(정의 단계)·함수(피호출 단계)를
    더 붙인다(`_AUDITOR_WRAPPER_*`·`_AUDITOR_ALIAS_ROUNDS` 주석). 붙인 수는 `wrapper_callees`·
    `alias_callees`·`alias_definitions`가 든다. 0이면 이 셋이 이벤트에 없고 피호출은 1단에서
    멈춘다 — 이 값이 생기기 전 모양 그대로다.

    **간접 피호출(C6)** — `auditor_indirect_callees`가 1이면 피호출 단계 끝에, 실린 함수의 필드 경유
    호출(`sys->alloc(…)`)마다 그 필드에 묶인 함수(`ctx.indirect_calls_of`) 중 아직 안 실린 것을
    피호출처럼 붙인다. 그 수가 필드마다 `_AUDITOR_INDIRECT_MAX`를 넘으면 그 필드만 건너뛴다.

    **호출자 창(#349 (b))** — `auditor_caller_lines`가 N이면 정의·피호출 함수 다음에, entry
    함수를 부르는 호출 지점(`ctx.call_sites_of`)의 앞뒤 N줄을 호출자 함수 범위 안으로 잘라
    붙인다. 이미 실린 범위와 겹치는 지점은 건너뛰고(호출자가 슬라이스에 걸쳐 이미 실렸거나 피호출
    함수로 실린 경우 · 같은 호출자 안의 이웃 호출), 겹치지 않는 지점을 앞에서부터
    `_AUDITOR_CALLER_SITES`개까지 읽는다. 찾은 지점 수는 `call_sites`가 든다. 창은 함수가 아니라
    `functions`에 안 든다 — `callers`·`callers_dropped`가 따로 센다.

    **쓰기 지점 창(C5(a))** — `auditor_write_sites`가 N이면 맨 뒤(호출자 창 다음 — 호출자 창이 먼저
    자리를 잡아 쓰기 창에 밀리지 않는다, PR #468 리뷰 MEDIUM-1)에, 슬라이스 줄에 나온 이름·필드에
    쓰는 지점(`ctx.writes_of`·`ctx.field_writes_of`)의 앞뒤 `_AUDITOR_WRITE_LINES`줄을 쓰는 함수
    범위로 잘라 N곳까지 붙인다(`_write_sites`가 고르고 순서를 정한다). 창은 `functions`에 안 든다
    — `write_windows`·`write_windows_dropped`가 따로 센다.

    ponytail: 범위가 겹치는 함수는 거르지 않는다 — 창 `anchor`(entry를 품는 심볼을 모를 때)나
    겹치는 심볼(겹침을 내는 인덱스 — index-v2도 `symbols`는 안 겹친다)이면 같은 줄이 두 블록에
    실릴 수 있다. 겹침을 내는 인덱스가 오면 품는
    관계로 거른다.

    **게이트(`auditor_source_gate`)** — 1이면 이벤트에 `gate_reasons`(`source_gate.reasons` — 쉼표로
    이은 사유 코드)가 더 실린다. 읽기 전용 관측이라 `SOURCE`·프롬프트는 안 바뀐다.
    """
    symbols = {file: ctx.symbols_for(file) or [] for file in {file for file, _ in ref.lines}}
    spanned: dict[Location, None] = {}
    uncovered = 0
    in_anchor = [ref.entry.line_start] if ref.entry.file == anchor.file else []
    for file, line in ref.lines:
        if file == anchor.file and spans(anchor, line):
            in_anchor.append(line)
            continue
        symbol = narrowest_symbol(symbols[file], line)
        if symbol is None:
            uncovered += 1
        else:
            spanned[symbol] = None
    anchor_text = ctx.read_source(anchor)
    # GUARDS(`auditor_guards`)는 줄이기 **전**의 원문을 훑는다 — 아래 `_trim_entry`가 잘라 낸 검사도
    # 행으로 남는다. 켜지 않았으면 `guard_scan`이 `None`이라 블록도 이벤트 필드도 없다.
    guard_scan = (
        scan_guards(ref, anchor, anchor_text, ctx.read_source, ctx.read_raw)
        if ctx.config.thresholds.auditor_guards
        else None
    )
    guard_blocks = [guard_scan.block] if guard_scan and guard_scan.block else []
    # 상한의 단위(#349 (c)): `auditor_source_tokens`가 0이면 글자(`_AUDITOR_SOURCE_CHARS`), 아니면
    # Auditor 모델 토크나이저로 센 토큰(`counter_for_role`). 토크나이저가 없으면(CI·dev) 기본 근사
    # (`estimate_tokens`)로 세고 이벤트가 그것을 밝힌다.
    token_cap = ctx.config.thresholds.auditor_source_tokens
    model_counter = ctx.counter_for_role(Role.AUDITOR.value) if token_cap else None
    measure = (model_counter or estimate_tokens) if token_cap else len
    cap = token_cap or _AUDITOR_SOURCE_CHARS
    anchor_chars = len(anchor_text)
    anchor_end = (anchor.line_start or 1) + anchor_text.count("\n")
    trim_entry = ctx.config.thresholds.auditor_entry_trim
    # 「상한을 넘었다」와 「줄였다」를 가른다 — 슬라이스 줄이 함수 전체에 퍼지면 넘어도 뺄
    # 줄이 0이다.
    over_cap = bool(trim_entry) and measure(anchor_text) > cap
    omitted = 0
    if over_cap:
        anchor_text, omitted = _trim_entry(anchor, anchor_text, in_anchor)
    blocks = [_block(anchor, anchor_text, end=anchor_end)]
    # 정의 조회가 읽는 (위치, 원문) — 배너(`파일:줄`)와 뺀 구간 표시의 낱말은 코드가 아니다(#351
    # 리뷰). 위치를 같이 든다: 지역·파라미터는 함수마다 따로 빼야 해서다(`source_definitions`).
    bodies = [(anchor, _OMITTED_LINE.sub("", anchor_text))]
    size, dropped = measure(blocks[0]), 0
    # GUARDS는 entry 함수 몫이라 상한에 안 밀리지만 크기는 먼저 센다 — 뒤에 붙는 함수가 그만큼
    # 덜 든다.
    size += sum(1 + measure(block) for block in guard_blocks)
    carried = [anchor]
    # 총 상한에 걸려 뺀 함수도 「본 것」에 둔다 — 피호출 단계가 같은 함수를 다시 세지 않게.
    seen = [anchor]
    for symbol in sorted(spanned, key=lambda symbol: (symbol.file, symbol.line_start or 0)):
        seen.append(symbol)
        text = ctx.read_source(symbol)
        block = _block(symbol, text)
        if size + 1 + measure(block) > cap:
            dropped += 1
            continue
        blocks.append(block)
        bodies.append((symbol, text))
        carried.append(symbol)
        size += 1 + measure(block)
    with_definitions = ctx.config.thresholds.auditor_definitions
    # 정의 조회 위생(`source_definitions`)은 `auditor_definitions`를 켠 run만 쓴다 — 아래 피호출·
    # 호출자 조회도 같은 규칙(비제품 경로 강등)으로 「하나뿐」을 정한다. 끈 run은 옛 호출 그대로라
    # 측정이 있는 옵션 조합(피호출만·호출자 창만)의 조회 결과가 안 바뀐다. 래퍼·별칭 너머(C4)는
    # 따로 `auditor_callee_wrappers`가 켠다 — 그 값이 0이면 피호출·정의 단계가 C4 이전 모양이다.
    hygiene = {"demote_nonproduct": True} if with_definitions else {}
    wrappers = ctx.config.thresholds.auditor_callee_wrappers
    definitions = definitions_dropped = alias_definitions = 0
    # 실은 매크로가 다른 이름 하나로 바꿔 넘기면 그 이름(C4 — `macro_alias_target`). 아래 별칭
    # 라운드가 그 이름의 정의를, 피호출 단계가 그 이름의 함수 본문을 찾는다.
    aliases: set[str] = set()
    # 정의를 조회한 이름(정의 단계가 채운다) — 별칭 함수 조회가 이 안에서만 찾는다.
    asked: set[str] = set()
    # 별칭 대상 → 그 별칭 매크로의 **정의를 푼 파일들**(먼저 나온 순). 정의 조회는 **본문의 파일
    # 마다** 부르고(`_definitions_by_file` — 앵커 파일 하나로 부르면 다른 파일에서 실린 함수의
    # 이름이 앵커의 include 그래프로 풀린다, PR #485 리뷰 P2-3), 별칭 대상은 그 별칭을 가진
    # 정의를 푼 파일들의 기준으로만 조회한다 — 그 매크로를 쓴 모든 파일로 귀속하면 `MODE`가 다른
    # 정의(상수 5)로 풀린 파일의 기준으로도 대상을 찾아 무관한 정의가 붙는다(2라운드 F3).
    alias_files: dict[str, list[str]] = {}

    definitions_trimmed = 0
    trimmed_enums: dict[tuple[str, int | None, int | None], _TrimmedEnum] = {}

    def widen_enum(definition: Location, cut: _TrimmedEnum) -> bool:
        """이미 잘라 실은 enum에 그 enum의 다른 상수를 더한다 — 블록을 그 자리에서 다시 만든다.
        합친 블록이 정의 하나의 상한이나 전체 상한을 넘거나 그 상수가 줄 머리에 안 나오면 이미 실린
        블록은 그대로 두고 못 더한 상수 하나를 `definitions_dropped`가 센다. 단 줄 머리 상수가 아닌
        이름(typedef 이름·태그·한 줄 다중 상수의 둘째)이 **이미 실린 블록에 낱말로 있으면** 보이는
        것으로 보고 조용히 `False`다 — 보이는 정의를 못 실었다고 세면 `DEFINITION_DROPPED` 게이트
        사유가 거짓 양성이다(2라운드 F1). 판정한 이름은 `cut.settled`에 둔다.
        ⚠ **한계(수정 전과 같은 침묵 — 미해결)**: 낱말이 있다는 것이 그 이름의 **정의**가 있다는
        뜻은 아니다. 끝줄 값(`AAA_ALIAS = OP005_B,`)이나 창 안 주석이 언급만 해도 보이는 것으로
        세므로, 그 정의가 SOURCE에 없는데도 `definitions_dropped`가 **과소 집계**될 수 있다. 특성화
        테스트: `test_limitation_a_constant_only_mentioned_in_the_block_counts_as_shown`."""
        nonlocal size, definitions_dropped
        name = definition.symbol
        if not name or name in cut.names or name in cut.settled:
            return False
        names = [*cut.names, name]
        trimmed = _trim_enum(cut.text, names)
        shown = blocks[cut.index].split("\n", 1)[1]  # 배너 줄 뒤 — 파일 이름의 낱말은 정의가 아니다
        if trimmed is None and re.search(rf"\b{re.escape(name)}\b", shown):
            cut.settled.add(name)
            return False
        block = _block(definition, trimmed, end=cut.end) if trimmed is not None else None
        if (
            block is None
            or len(block) > _AUDITOR_DEFINITION_CHARS
            or size + measure(block) - measure(blocks[cut.index]) > cap
        ):
            cut.settled.add(name)
            definitions_dropped += 1
            return False
        size += measure(block) - measure(blocks[cut.index])
        blocks[cut.index] = block
        cut.names = names
        return True

    def carry_definition(definition: Location, resolved_by: list[str]) -> bool:
        """`resolved_by`는 이 정의를 푼 조회의 파일들이다 — 별칭 대상이 그 기준을 물려받는다."""
        nonlocal size, definitions, definitions_dropped, definitions_trimmed
        key = (definition.file, definition.line_start, definition.line_end)
        if (cut := trimmed_enums.get(key)) is not None:
            return widen_enum(definition, cut)
        if any(_overlaps(definition, other) for other in seen):
            return False
        seen.append(definition)
        text = ctx.read_source(definition)
        block = _block(definition, text)
        # 상한을 넘는 enum은 물은 상수 주변만 남겨 다시 잰다(`_trim_enum`) — 통째로 버리지 않는다
        end = (definition.line_start or 1) + text.count("\n")
        is_trimmed = False
        if len(block) > _AUDITOR_DEFINITION_CHARS and definition.symbol:
            trimmed = _trim_enum(text, [definition.symbol])
            if trimmed is not None:
                block = _block(definition, trimmed, end=end)
                is_trimmed = True
        if len(block) > _AUDITOR_DEFINITION_CHARS or size + 1 + measure(block) > cap:
            definitions_dropped += 1
            return False
        blocks.append(block)
        size += 1 + measure(block)
        definitions += 1
        if is_trimmed:  # 실린 것만 센다 — 줄여 놓고 상한에 걸려 못 실은 enum은 위에서 dropped다
            definitions_trimmed += 1
            trimmed_enums[key] = _TrimmedEnum(text, end, [definition.symbol or ""], len(blocks) - 1)
        if wrappers and (target := macro_alias_target(text)) is not None:
            aliases.add(target)
            files = alias_files.setdefault(target, [])
            files += [file for file in resolved_by if file not in files]
        return True

    if with_definitions:
        # 실린 함수 원문이 쓰는 이름 → 매크로·타입·파일 변수·enum 상수 정의(#349 (f)). 정의는 작고
        # 판단 가치가 커서(버퍼 크기·필드 배치) 피호출 함수보다 먼저 싣는다. 이름은 함수마다 그
        # 함수의 지역·파라미터·필드 전용 이름을 뺀 것이고, 슬라이스 줄에 나온 이름의 정의가 먼저다.
        # 조회는 본문의 파일마다 그 파일 기준이다 — 본문이 모두 앵커 파일이면 **이 본 조회**는 한
        # 번 부르는 예전 호출 그대로다(아래 별칭 라운드는 예전부터 라운드마다 추가 호출이다).
        by_file: dict[str, set[str]] = {}
        for body in bodies:
            by_file.setdefault(body[0].file, set()).update(definition_names([body], ctx.locals_of))
        found = _definitions_by_file(ctx, by_file, hygiene)
        for definition in slice_names_first(list(found), ctx.read_source, ref.lines):
            carry_definition(definition, found[definition])
        # 별칭 라운드(C4) — `ND_TCHECK` → `ND_TCHECK2`처럼 실은 매크로가 이름만 바꿔 넘기면 그
        # 대상의 정의를 한 번 더 찾는다. 대상이 다시 별칭이면 `_AUDITOR_ALIAS_ROUNDS`까지 간다.
        # 이미 조회한 이름은 다시 안 찾는다(파일 기준 — 다른 파일에서 쓴 같은 이름은 그 파일
        # 기준으로 한 번 더). `asked`가 정의를 조회한 이름이다 — 아래 피호출 단계도 이 이름들의
        # 함수만 찾는다(마지막 라운드가 새로 낸 대상은 한 단계 더 간 것이라 뺀다).
        asked_in = {(name, file) for file, file_names in by_file.items() for name in file_names}
        asked = {name for name, _ in asked_in}
        for _ in range(_AUDITOR_ALIAS_ROUNDS):
            wanted: dict[str, set[str]] = {}
            for target in sorted(aliases):
                for file in alias_files[target]:
                    if (target, file) not in asked_in:
                        wanted.setdefault(file, set()).add(target)
            if not wanted:
                break
            asked_in |= {(target, file) for file, targets in wanted.items() for target in targets}
            asked |= set().union(*wanted.values())
            for definition, resolved_by in _definitions_by_file(ctx, wanted, hygiene).items():
                if carry_definition(definition, resolved_by):
                    alias_definitions += 1
    hops = ctx.config.thresholds.auditor_callee_hops
    indirect = ctx.config.thresholds.auditor_indirect_callees
    callees = callees_dropped = wrapper_callees = alias_callees = 0
    indirect_callees = indirect_skipped = 0
    if hops:
        # 피호출 함수 1단(#349 (a)) + 래퍼 너머(C4). 붙인 피호출 함수가 짧고
        # (`_AUDITOR_WRAPPER_LINES`줄 이하) 그 본문이 정의를 아는 함수를
        # 1~`_AUDITOR_WRAPPER_CALLS`개만 부르면 래퍼로 보고 그 함수들을 바로 뒤에 붙인다 —
        # `gf_strdup` → `gf_mem_strdup`처럼 래퍼만 실리고 실제 구현이 빠지던 자리다. 래퍼 너머는
        # `_AUDITOR_WRAPPER_DEPTH`단까지다. 래퍼가 아닌 함수는 1단에서 멈춘다.
        def carry_callee(callee: Location) -> bool:
            nonlocal size, callees, callees_dropped
            if any(_overlaps(callee, other) for other in seen):
                return False
            seen.append(callee)
            block = _block(callee, ctx.read_source(callee))
            if len(block) > _AUDITOR_CALLEE_CHARS or size + 1 + measure(block) > cap:
                callees_dropped += 1
                return False
            blocks.append(block)
            size += 1 + measure(block)
            callees += 1
            return True

        def through_wrapper(callee: Location, depth: int) -> None:
            nonlocal wrapper_callees
            if not wrappers or depth > _AUDITOR_WRAPPER_DEPTH or not _is_short(callee):
                return
            inner = ctx.callees_of(callee, **hygiene) or []
            if not 1 <= len(inner) <= _AUDITOR_WRAPPER_CALLS:
                return
            for target in inner:
                if carry_callee(target):
                    wrapper_callees += 1
                    through_wrapper(target, depth + 1)

        for owner in list(carried):
            for callee in ctx.callees_of(owner, **hygiene) or []:
                if carry_callee(callee):
                    through_wrapper(callee, 1)
        # 별칭 매크로가 부르는 함수(`#define FT_FRAME_EXTRACT(...) FT_Stream_ExtractFrame(...)`).
        # 매크로 본문 안의 호출은 인덱스 호출 표에 없어 위 `callees_of`로는 안 닿는다 -> 이름으로
        # 찾는다. 정의를 조회한 이름만이다 — `M` → `M2` → `M3` → `impl`이면 `M2`·`M3`까지가
        # `_AUDITOR_ALIAS_ROUNDS`(2)번이고 `impl`은 셋째라 안 찾는다(PR #452 리뷰 LOW-1).
        if forwarded := aliases & asked:
            for function in ctx.functions_named(sorted(forwarded), anchor.file, **hygiene) or []:
                if carry_callee(function):
                    alias_callees += 1
                    through_wrapper(function, 1)
        # 간접 피호출(C6 — #304). `sys->alloc(…)`처럼 필드 경유로 부르는 함수는 `callees_of`가 못
        # 푼다 -> index-v3 `fn_bindings`로 그 필드에 묶인 함수를 후보로 받아 붙인다. 상한은
        # 필드마다이고 이미 실린 후보는 안 센다 — 아직 안 실린 후보가 `_AUDITOR_INDIRECT_MAX`개를
        # 넘는 필드는 건너뛰고 `indirect_skipped`가 센다. 비제품 경로 강등은 피호출 조회와 같다
        # (`hygiene`).
        if indirect:
            for owner in list(carried):
                for _field, targets in ctx.indirect_calls_of(owner, **hygiene) or []:
                    fresh = [t for t in targets if not any(_overlaps(t, o) for o in seen)]
                    if len(fresh) > _AUDITOR_INDIRECT_MAX:
                        indirect_skipped += 1
                        continue
                    for target in fresh:
                        if carry_callee(target):
                            indirect_callees += 1
                            through_wrapper(target, 1)
    # 호출자 창은 피호출 함수 **뒤**다 — 피호출 함수가 entry를 되부르면(상호 재귀) 그 본문이
    # 먼저 통째로 실리고 창은 겹쳐 빠진다. 앞이면 본문 대신 창 조각만 남고 아무도 안 셌다
    # (#351 자체 검토).
    caller_lines = ctx.config.thresholds.auditor_caller_lines
    callers = callers_dropped = call_sites = 0
    if caller_lines and anchor.symbol:
        sites = ctx.call_sites_of(anchor, **hygiene) or []
        call_sites = len(sites)
        tried = 0
        for site in sites:
            if tried == _AUDITOR_CALLER_SITES:
                break
            window = _caller_window(ctx, site, caller_lines)
            # 이미 실린 범위와 겹치는 지점(같은 호출자 안의 이웃 호출 · 슬라이스가 지난 호출자)은
            # 읽지도 세지도 않고, 읽는 지점 수 상한도 안 먹는다 — 다른 호출자가 굶지 않게.
            if any(_overlaps(window, other) for other in seen):
                continue
            tried += 1
            seen.append(window)
            block = _block(window, ctx.read_source(window))
            if size + 1 + measure(block) > cap:
                callers_dropped += 1
                continue
            blocks.append(block)
            size += 1 + measure(block)
            callers += 1
    # 쓰기 지점 창(C5(a) — #304)은 호출자 창 **뒤**다 — 앞이면 쓰기 창이 먼저 `seen`에 들어가 그와
    # 겹치는 호출자 창(이 옵션 전에는 실리던 창)이 통째로 빠졌다(PR #468 리뷰 MEDIUM-1). 이미 실린
    # 범위와 겹치는 쓰기 창은 쓰는 줄 쪽 남은 조각으로 자르고, 쓰는 줄 자체가 실려 있으면 안 붙인다
    # (읽는 지점 수 상한도 안 먹는다).
    write_sites = ctx.config.thresholds.auditor_write_sites
    write_windows = write_windows_dropped = write_sites_found = 0
    if write_sites:
        sites = _write_sites(ctx, ref, carried, anchor.file)
        write_sites_found = len(sites)
        tried = 0
        for site in sites:
            if tried == write_sites:
                break
            window = _clip_around(
                _caller_window(ctx, site, _AUDITOR_WRITE_LINES), site.line_start or 1, seen
            )
            if window is None:
                continue
            tried += 1
            seen.append(window)
            block = _block(window, ctx.read_source(window))
            if size + 1 + measure(block) > cap:
                write_windows_dropped += 1
                continue
            blocks.append(block)
            size += 1 + measure(block)
            write_windows += 1
    # 줄인 entry 함수는 혼자여도 배너를 단다 — 원래 줄 번호를 되짚을 곳이 배너뿐이다(#351 리뷰).
    # GUARDS가 붙어도 단다 — 그 행의 `파일:줄`을 코드 줄에 되짚을 곳이 배너뿐이다. 블록은 entry 함수
    # **바로 뒤**, 다른 함수·정의·피호출 본문보다 앞이다.
    blocks[1:1] = guard_blocks
    source = "\n".join(blocks) if len(blocks) > 1 or omitted else anchor_text
    ctx.trace.emit(
        EventKind.AUDITOR_SOURCE,
        stage=Stage.AUDITOR,
        subject_kind="candidate",
        subject_id=candidate_id,
        slice_id=ref.slice_id,
        # 함수가 아닌 블록을 전부 뺀다 — 정의·호출자 창·쓰기 지점 창·GUARDS(0 또는 1). 래퍼·별칭·
        # 간접 피호출은 함수 본문이라 센다. 옵션마다 따로 더한 식이 합쳐질 때 하나가 빠지면
        # `functions`가 조용히 틀린다.
        functions=len(blocks) - definitions - callers - write_windows - len(guard_blocks),
        dropped=dropped,
        uncovered_lines=uncovered,
        chars=len(source),
        anchor_chars=anchor_chars,
        **({"entry_omitted_lines": omitted, "entry_over_cap": over_cap} if trim_entry else {}),
        **(
            {
                "callees": callees,
                "callees_dropped": callees_dropped,
                **(
                    {"wrapper_callees": wrapper_callees, "alias_callees": alias_callees}
                    if wrappers
                    else {}
                ),
                **(
                    {"indirect_callees": indirect_callees, "indirect_skipped": indirect_skipped}
                    if indirect
                    else {}
                ),
            }
            if hops
            else {}
        ),
        **(
            {
                "write_windows": write_windows,
                "write_windows_dropped": write_windows_dropped,
                "write_sites_found": write_sites_found,
            }
            if write_sites
            else {}
        ),
        **(
            {"callers": callers, "callers_dropped": callers_dropped, "call_sites": call_sites}
            if caller_lines
            else {}
        ),
        **(
            {
                "definitions": definitions,
                "definitions_dropped": definitions_dropped,
                "definitions_trimmed": definitions_trimmed,
                **({"alias_definitions": alias_definitions} if wrappers else {}),
            }
            if with_definitions
            else {}
        ),
        **(guard_scan.event_fields() if guard_scan else {}),
        **(
            {"tokens": measure(source), "token_counter": "model" if model_counter else "estimate"}
            if token_cap
            else {}
        ),
        **(
            {
                "gate_reasons": ",".join(
                    source_gate.reasons(
                        ref, anchor, uncovered_lines=uncovered, functions_dropped=dropped,
                        callees_dropped=callees_dropped, callers_dropped=callers_dropped,
                        definitions_dropped=definitions_dropped,
                    )
                )
            }
            if ctx.config.thresholds.auditor_source_gate
            else {}
        ),
    )
    return source


def _write_sites(
    ctx: RunContext, ref: SliceRef, carried: list[Location], near_file: str
) -> list[Location]:
    """쓰기 지점 창(C5(a))을 붙일 후보 지점 — 고유하고 순서가 정해진 것.

    - 이름: 슬라이스 줄에 나온 이름 중 **실린 함수의** 지역·파라미터가 아닌 것(`writes_of`). 그리고
      **쓰는 함수의** 지역·파라미터인 이름의 지점은 버린다 — `writes_of`는 파일 안에서 함수를 안
      가른다(그 docstring). 파일 변수 `buf`를 찾는데 다른 함수 `g`의 `char *buf = malloc(8);`이
      잡히면 Auditor가 할당 크기를 오판한다(PR #468 리뷰 HIGH-1)
    - 필드: 슬라이스 줄에 필드로 나온 이름(`field_writes_of`)
    - 순서: 이름·필드가 슬라이스 줄에 **처음 나온 순서**(`slice_line_refs` — 알파벳 순이면 앞쪽
      이름이 창을 먼저 먹고 길이 필드가 굶었다, 리뷰 MEDIUM-3). 그 안은 조회 순서다. 그다음
      **함수가 다른 지점이 먼저** — 함수마다 첫 지점이 다 나온 뒤에 둘째 지점이 온다(안정 정렬)
    - 같은 줄(파일·줄)은 한 번만 — 한 줄이 두 이름을 쓰면 창 하나다(리뷰 LOW-1)
    """
    own: set[str] = set()
    for function in carried:
        if function.symbol:
            own |= ctx.locals_of(function.file, function.symbol) or frozenset()
    found: dict[tuple[str, int], Location] = {}
    for name, is_field in slice_line_refs(ctx.read_source, ref.lines):
        if is_field:
            sites = ctx.field_writes_of(name, near_file) or []
        elif name in own:
            continue
        else:
            sites = [
                site
                for site in ctx.writes_of(name, near_file) or []
                if not (site.symbol and name in (ctx.locals_of(site.file, site.symbol) or ()))
            ]
        for site in sites:
            found.setdefault((site.file, site.line_start or 0), site)
    rank: dict[tuple[str, str | None], int] = {}
    order: list[tuple[int, int, Location]] = []
    for position, site in enumerate(found.values()):
        key = (site.file, site.symbol)
        order.append((rank.get(key, 0), position, site))
        rank[key] = rank.get(key, 0) + 1
    return [site for _, _, site in sorted(order, key=lambda item: item[:2])]


def _clip_around(window: Location, line: int, seen: list[Location]) -> Location | None:
    """`window`에서 `seen`의 범위와 겹치는 쪽을 깎고 `line`을 품는 조각만 남긴다. `line` 자체가
    이미 실린 범위 안이면 `None`. 같은 파일에 범위 없는 위치(파일 전체)가 있어도 `None`."""
    start, end = window.line_start or line, window.line_end or line
    for other in seen:
        if other.file != window.file:
            continue
        if other.line_start is None:
            return None
        other_end = other.line_end or other.line_start
        if other.line_start <= line <= other_end:
            return None
        if other_end < line:
            start = max(start, other_end + 1)
        else:
            end = min(end, other.line_start - 1)
    return Location(file=window.file, symbol=window.symbol, line_start=start, line_end=end)


def _caller_window(ctx: RunContext, site: Location, lines: int) -> Location:
    """호출 지점 앞뒤 `lines`줄 — 그 줄을 품는 가장 좁은 심볼(호출자 함수)이 있으면 그 범위로
    자른다. 호출자 머리를 넘어 앞 함수까지 싣지 않게 하려는 것이다."""
    line = site.line_start or 1
    start, end = line - lines, line + lines
    enclosing = narrowest_symbol(ctx.symbols_for(site.file) or [], line)
    if enclosing is not None and enclosing.line_start is not None:
        start = max(start, enclosing.line_start)
        end = min(end, enclosing.line_end or end)
    return Location(file=site.file, symbol=site.symbol, line_start=max(1, start), line_end=end)


def _trim_entry(anchor: Location, text: str, keep_lines: list[int | None]) -> tuple[str, int]:
    """상한을 넘는 entry 함수를 줄인다(#349 — `auditor_entry_trim`). 남기는 줄은 함수 머리(여는
    `{`가 나오는 줄까지, 앞 `_AUDITOR_ENTRY_HEAD_LINES`줄 안) · 닫는 줄 · `keep_lines`(entry 줄과
    이 함수 안 슬라이스 줄) 앞뒤 `_AUDITOR_ENTRY_CONTEXT_LINES`줄이다. 뺀 구간마다
    `/* ... N lines omitted ... */` 한 줄을 남긴다 — 줄 번호는 배너 범위와 이 표시로 되짚는다
    (줄였으면 함수가 하나여도 배너를 단다 — `_auditor_source`).
    돌려주는 것은 줄인 원문과 뺀 줄 수다. 줄여도 상한을 넘을 수 있다 — 더 줄이지 않는다.
    """
    lines = text.split("\n")
    start = anchor.line_start or 1
    head = next(
        (i for i, line in enumerate(lines[:_AUDITOR_ENTRY_HEAD_LINES]) if "{" in line), 0
    )
    keep = set(range(head + 1)) | {len(lines) - 1}
    for line in keep_lines:
        if line is not None:
            at = line - start
            keep |= set(
                range(at - _AUDITOR_ENTRY_CONTEXT_LINES, at + _AUDITOR_ENTRY_CONTEXT_LINES + 1)
            )
    out: list[str] = []
    omitted = gap = 0
    for i, line in enumerate(lines):
        if i in keep:
            if gap:
                out.append(_OMITTED.format(gap))
                omitted, gap = omitted + gap, 0
            out.append(line)
        else:
            gap += 1
    return "\n".join(out), omitted


def _overlaps(a: Location, b: Location) -> bool:
    """두 위치의 줄 범위가 같은 파일에서 겹치는가. 범위가 없으면(파일 전체) 같은 파일이면 겹친다."""
    if a.file != b.file:
        return False
    if a.line_start is None or b.line_start is None:
        return True
    return a.line_start <= (b.line_end or b.line_start) and b.line_start <= (
        a.line_end or a.line_start
    )


def _definitions_by_file(
    ctx: RunContext, names_by_file: dict[str, set[str]], hygiene: dict
) -> dict[Location, list[str]]:
    """파일마다 그 파일 본문이 쓰는 이름을 **그 파일 기준**(`near_file`)으로 조회해 이은 정의 위치
    → 그 정의를 **푼 파일들**(별칭 대상이 그 파일들의 기준으로만 조회된다). 같은 위치는 한 번이고,
    같은 이름이 파일마다 다른 정의로 풀리면 둘 다 든다 — 블록 배너에 위치가 있어 구별되고, 하나로
    합치면 한쪽 본문이 틀린 정의를 본다. 순서는 파일 순(각 조회가 정한 순서 그대로)이다 — 본문이 한
    파일뿐이면 `ctx.definitions_of` 한 번의 결과 그대로다.
    ponytail: 비제품 강등(`demote_nonproduct`)은 **한 조회 안에서만** 순서를 정한다 — 파일 사이는
    파일 순 first-fit이라, 앞 파일 조회의 `tests/` 정의가 뒤 파일 조회의 제품 정의보다 앞서 상한을
    먹을 수 있다. 코드 줄(`found`는 파일 순 연결 · `slice_names_first`는 슬라이스 줄 이름만
    당긴다)로 입증했을 뿐 실행하거나 재지 않았다. 필요하면 조회들을 모은 뒤 `is_nonproduct_path`로
    한 번 더 정렬한다(그 함수는 `src/adapters/` 쪽에 있어 러너가 직접 못 쓴다)."""
    found: dict[Location, list[str]] = {}
    for file, names in names_by_file.items():
        for definition in ctx.definitions_of(names, file, **hygiene) or []:
            files = found.setdefault(definition, [])
            if file not in files:
                files.append(file)
    return found


@dataclass
class _TrimmedEnum:
    """`SOURCE`에 잘라 실은 enum 하나 — 같은 enum의 다른 상수를 나중에 물으면(같은 묶음의 다음
    상수 · 별칭 라운드) `blocks[index]`를 그 상수까지 넓혀 다시 만든다. 첫 상수 주변만 싣고
    둘째를 겹친다며 건너뛰면 둘째 정의가 사라진다(PR #485 리뷰 P2-4).

    ⚠ **한계(수정 전과 같은 선행 결함 — 미해결)**: 이 기억은 enum이 **줄 머리 상수로** 먼저 실렸을
    때만 선다. 같은 범위의, 줄 머리 상수가 아닌 이름(typedef 이름·태그·비머리 상수)이 줄 머리
    상수보다 **먼저 처리되면** `_trim_enum`이 `None`이라 enum이 통째로 `dropped`되고 그 범위가
    `seen`에 올라, 뒤따르는 상수는 겹침으로 안 실린다(조용히 사라진다). 처리 순서는 정의 조회의
    이름 정렬(`definitions_of`는 `sorted(set(names))`) 다음에 슬라이스 줄에 나온 이름을 앞으로 당긴
    것이라 ① 슬라이스 줄에 타입 이름만 있고 상수는 다른 줄일 때뿐 아니라 ② 타입·태그 이름이 상수보다
    **정렬상 앞서는** enum(`enum AVPixelFormat` ↔ `AV_PIX_FMT_…` — `AVP` < `AV_`)은 타입과 상수가
    모두 슬라이스 줄에 있어도 난다 — **이 PR이 겨냥한 FFmpeg 꼴에서 상수 주변만 싣는 기능이 걸리지
    않는다.** ③ 줄마다 상수가 여럿인 enum에서 비머리 상수가 먼저 물려도 같다. 특성화 테스트:
    `test_limitation_a_type_name_asked_first_drops_the_whole_enum_and_hides_its_constants`(①) ·
    `test_limitation_a_type_name_that_sorts_before_its_constants_hides_them_on_the_slice`(②).
    후속 수정 방향(설계 변경이라 이 PR에서 안 한다): 크기로 dropped된 타입 이름 행을 `seen`에 안
    올려 상수 행이 먼저 시도하게 한다."""

    text: str  # 줄이기 전 enum 원문
    end: int  # 줄이기 전 끝 줄 — 배너가 든다
    names: list[str]  # 지금 블록에 든 상수들 — `_trim_enum`에 넘기는 목록이다
    index: int  # `blocks`에서의 자리
    # 이미 판정한 이름 — 블록에 낱말로 보이는 이름(typedef 이름·태그·한 줄 다중 상수의 둘째 —
    # 정의가 아니라 언급일 수도 있다, `widen_enum`의 한계)과 못 더해 `dropped`로 센 상수. 같은
    # 이름을 또 물어도 세지 않는다. **`names`와 따로 둔다** — 줄
    # 머리 상수가 아닌 이름이 `names`에 들어가면 `_trim_enum`이 `None`을 내 그 뒤 상수가 못
    # 더해진다.
    settled: set[str] = field(default_factory=set)


def _trim_enum(text: str, names: list[str]) -> str | None:
    """상한을 넘는 enum 정의를 머리(여는 `{`까지) · `names` 상수마다 그 줄 앞뒤
    `_AUDITOR_ENUM_CONTEXT_LINES`줄 · 닫는 줄만 남겨 줄인다 — 창이 겹치면 한 구간이다. 뺀 구간마다
    `_OMITTED` 한 줄 — 줄 번호는 배너와 그 표시로 되짚는다. enum이 아니거나(첫 `{` 앞에 `enum`이
    없다) 그 이름 **하나라도** 줄 머리 상수로 안 나오면 `None`(줄이지 않는다 — 일부만 싣고 다 실은
    것처럼 보이지 않게).
    ⚠ 값을 안 적은 상수는 앞 상수에서 하나씩 세는데, 뺀 구간이 그 앞이면 모델이 값을 못 센다."""
    lines = text.split("\n")
    head = next((i for i, line in enumerate(lines) if "{" in line), None)
    if head is None or not _ENUM_HEAD.search("\n".join(lines[: head + 1])):
        return None
    width = _AUDITOR_ENUM_CONTEXT_LINES
    keep = set(range(head + 1)) | {len(lines) - 1}
    for name in names:
        own = re.compile(rf"\s*{re.escape(name)}\b")
        hit = next((i for i, line in enumerate(lines) if i > head and own.match(line)), None)
        if hit is None:
            return None
        keep |= set(range(max(0, hit - width), hit + width + 1))
    out, gap = [], 0
    for i, line in enumerate(lines):
        if i in keep:
            if gap:
                out.append(_OMITTED.format(gap))
                gap = 0
            out.append(line)
        else:
            gap += 1
    return "\n".join(out)


def _block(location: Location, text: str, end: int | None = None) -> str:
    """`SOURCE`에 함수가 둘 이상일 때 그 하나 — 슬라이스 렌더와 같은 꼴의 배너 + 원문. 끝 줄은
    읽힌 줄로 센다 — 창은 파일 끝을 넘을 수 있고 읽기가 자른다. 줄인 entry 함수(`_trim_entry`)는
    줄이기 전 끝 줄을 `end`로 받는다."""
    start = location.line_start or 1
    end = end or start + text.count("\n")
    return f"/* ===== {location.file}:{start}-{end} ===== */\n{text}"


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
    (Stage.SCOUT, _step_scout_open),
    (Stage.SCOUT, _step_scout_settle),
    (Stage.AUDITOR, _step_auditor),
    (Stage.GATE, _step_gate),
)

# 국면을 가르는 자리 — `_step_scout_settle`의 인덱스(표에서 읽는다, 하드코딩하지 않는다).
# **국면 A**(전 좌석 개시)는 이 인덱스 앞, **국면 B**(정착부터 끝까지)는 이 인덱스부터다
# (`_triage_by_candidate`의 두 국면).
_SCOUT_SETTLE_INDEX = next(
    index for index, (_, step) in enumerate(_TRIAGE_STEPS) if step is _step_scout_settle
)
_PHASE_A_STEPS = _TRIAGE_STEPS[:_SCOUT_SETTLE_INDEX]
_PHASE_B_STEPS = _TRIAGE_STEPS[_SCOUT_SETTLE_INDEX:]

# 단계 → 그 단계에서 **모델을 부르는 역할** (ADR 0012 Phase 6.5 — 단계 단위 교대가 읽는다).
# 4·5단계는 위 표의 단계이고 7단계는 `_analyze`의 Critic이다. 8단계(VALIDATE)는 프로필이 pov
# 좌석을 선언할 때만 pov 모델을 부른다(PoV 배선 — 추가형). 여기 없는 단계는 모델을 안 부른다
# — 3단계 검색의 embedding·reranker는 `SwapPolicy`가 교대에서 거절한다(ADR 0012 결정 3 뒤).
#
# **교대 코드는 `Stage`로 갈라 쓰지 않고 이 표만 읽는다.** 모델을 부르는 좌석이 생기면 여기 한
# 줄이다. 줄을 빼먹으면 `tests/test_stage_swap.py`의 성질 테스트(모든 호출 시점에 그 모델이 떠
# 있다)가 그 좌석의 첫 호출에서 빨개진다. 역할 자체는 각 스텝이 따로 들고 있다(`_step_auditor`의
# `Role.AUDITOR` 등) — 둘이 어긋나는 것을 잡는 것이 그 성질 테스트다.
#
# ⚠ **8단계 pov 줄이 있어도 pov를 안 올린 프로필에선 이 단계 교대가 안 난다** — 막는 자리는
# `_analyze`다: 거기서 `_swap_for(VALIDATE)`를 pov가 `swap.swappable`에 있을 때만 부른다.
# (`_swap_for`는 목록 밖 역할로 불리면 앞 서버를 내리는 빈 교대를 내므로, 안 부르는 것이 가드다.)
# 이 표의 pov 줄은 「8단계가 부르는 역할은 pov다」를 뜻한다 — 빼면 pov 교대가 서버 없이
# 첫 호출에서 빨개진다(성질 테스트).
_ROLES_CALLED: dict[Stage, Callable[[RunContext], tuple[Role, ...]]] = {
    Stage.SCOUT: _scout_roles,
    Stage.AUDITOR: lambda _ctx: (Role.AUDITOR,),
    Stage.CRITIC: lambda _ctx: (Role.CRITIC,),
    Stage.VALIDATE: lambda _ctx: (Role.POV,),
}


def _swap_for(ctx: RunContext, stage: Stage) -> None:
    """교대하는 run이면 `stage`가 부르는 모델만 떠 있게 한다 (ADR 0012 Phase 6.5).

    대상 = 상주 ∪ (그 단계 역할 ∩ 교대) → 실서빙 모델 키. Fake 역할은 서버가 없어 빼고
    (`served_models()`), 두 역할이 한 키면 한 번만 넣는다. 키는 정렬한다 — 띄우는 순서는 교대
    명령이 정한다. 교대가 없거나 그 단계가 모델을 안 부르면 아무것도 안 한다.
    """
    swap = ctx.config.swap
    roles_called = _ROLES_CALLED.get(stage)
    if swap is None or roles_called is None:
        return
    wanted = {*swap.resident, *(r.value for r in roles_called(ctx) if r.value in swap.swappable)}
    served = ctx.config.served_models()
    _swap(ctx, tuple(sorted({served[role] for role in wanted if role in served})), stage=stage)


def _swap(ctx: RunContext, keys: tuple[str, ...], *, stage: Stage | None) -> None:
    """교대 한 번을 부르고 `model_swap`을 남긴다. 실패(`ModelSwapError`)면 `ok=false`를 **먼저**
    남기고 올려 보낸다. 그 밖의 예외는 남기지 않고 그대로 올린다 — 계약 밖이다
    (`Dependencies.swap_to`).

    교대 콜러블이 없는 run(교대 없음)에서는 아무것도 안 한다.
    """
    if ctx.swap_to is None:
        return
    started = ctx.monotonic_ms()
    try:
        report = ctx.swap_to(keys)
    except ModelSwapError as exc:
        ctx.record_model_swap(
            keys,
            stage=stage,
            ok=False,
            wall_ms=ctx.monotonic_ms() - started,
            report=exc.report,
            error=error_detail(exc),
        )
        raise
    ctx.record_model_swap(
        keys, stage=stage, ok=True, wall_ms=ctx.monotonic_ms() - started, report=report
    )
    # 실제로 띄운 기동 조건은 명령의 보고로만 기록에 남는다(의도한 인자는
    # `serving_launch_intended`) — 키별로 어떤 필드든 하나라도 있으면 보고한 것으로 본다(그대로 둔
    # 서버는 `action`만 보고할 수 있다). 기동 인자를 실제로 실었는지는 보지 않는다.
    ctx.swap_unreported.update(
        key for key in keys if not any(name.startswith(f"server.{key}.") for name in report)
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
    후보 단위에서는 (#197 전) 카운터가 run 전체에 하나였고 리셋 조건이 「후보 하나가 3~6단계를
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
    # **성공 0 백스톱은 표의 행이 아니라 단계 번호 단위로 본다(#95).** Scout가 두 행
    # (개시·정착, 둘 다 `Stage.SCOUT`)으로 갈리면서, 행마다 바로 확인하면 개시 행만 보고
    # "이 자리는 아무것도 못 봤다"고 너무 일찍 판단하게 된다 — 그 시점엔 정착이 아직 후보를
    # 하나도 못 만들었다(개시는 후보를 안 만든다). 그래서 같은 단계 번호의 행을 전부 돈
    # 뒤에만 `_stop_if_a_caller_saw_nothing`을 부른다 — `_run_stage_for_all`은 이제 그 판단을
    # 내리지 않고 그 재료(`starved`)만 돌려준다.
    pending_starved: set[tuple[Stage, Role | None]] = set()
    steps = _TRIAGE_STEPS
    for row, (stage, step) in enumerate(steps):
        if last < stage:
            return
        # 단계에 **처음 들어갈 때**(Scout 정착 행은 개시 행과 같은 단계라 다시 안 부른다) 그 단계가
        # 부르는 모델만 떠 있게 한다. 올릴 대상이 없으면 부르지 않는다.
        if items and (row == 0 or steps[row - 1][0] != stage):
            _swap_for(ctx, stage)
        items, starved = _run_stage_for_all(ctx, stage, step, items, state)
        pending_starved |= starved
        next_stage = steps[row + 1][0] if row + 1 < len(steps) else None
        if next_stage != stage:
            _stop_if_a_caller_saw_nothing(pending_starved)
            pending_starved = set()


def _run_stage_for_all(
    ctx: RunContext,
    stage: Stage,
    step: TriageStep,
    items: list[_InFlight],
    state: _Accumulator,
) -> tuple[list[_InFlight], set[tuple[Stage, Role | None]]]:
    """한 단계를 살아 있는 target 전부에 태우고, (다음 단계로 갈 것, 이 행에서 아무것도
    못 본 caller 집합)을 돌려준다.

    폐기 판정은 후보 단위(`_triage_by_candidate`)와 **같은 표**다 — `_is_droppable`이
    그 판정을 함수로 들고 있는 이유가 정확히 이것이다(그 docstring). 여기서 다시
    `status not in _DROPPABLE_STATUS`를 적으면 한쪽만 고쳐질 때 같은 오류가 모드에
    따라 다르게 처리된다.

    ## 연속 폐기 카운터는 둘이다 — run과 자리 (#197, `_ConsecutiveDrops`)

    `items`가 target × 역할로 펼쳐진다. **자리 카운터**는 `_caller(stage, item.role)`을 키로
    센다 — 4단계에서는 Scout 자리, 5단계부터는 부르는 역할 하나다. 풀 멤버 하나가 전량
    실패하면 그 자리 키가 형제의 성공에 지워지지 않고 쌓여 `_MAX_CONSECUTIVE_DROPS`에
    닿는다(#195) — 그래서 Scout 커버리지 1/3이 빈 run이 `stopped_after`를 채우고
    `is_complete=False`가 된다(비교표에서 빠진다, [evaluation.md](../../evaluation.md) §6).
    **run 카운터**는 키 없이 세고 아무 성공이나 지운다(서는 조건은 `_ConsecutiveDrops`).

    > **왜 둘인가.** 자리를 안 가른 카운터 하나였을 때는 고장난 멤버가 매번 죽어도 형제의
    > 성공이 그때마다 0으로 지웠다 — 상한에 영원히 안 닿았다(#195). 역할(`item.role`) 키
    > 하나로 바꾸자(#195) 반대로 풀 3자리 전역 장애가 3번째가 아니라 7번째 시도에 섰고,
    > 5단계 Auditor 장애가 Scout 세 버킷으로 갈렸다(#197). 둘을 같이 두면 각자 한쪽을 막는다.

    카운터는 이 호출(단계표의 한 행) 안에서만 산다 — 단계 단위의 리셋 경계(`_triage_by_stage`
    1번)는 그대로다. 그래서 행 하나 안에서 자리 키의 단계는 늘 같다.

    `tests/test_pipeline_e2e.py`의 `test_a_broken_pool_member_trips_the_circuit_breaker`(자리)·
    `test_a_pool_wide_outage_stops_as_soon_as_a_second_target_fails`(run)가 이 동작을 고정한다.

    ## 성공 0 백스톱 (#296)

    이 행을 다 돈 뒤 **모델을 부른 자리 중 한 번도 성공하지 못한 것**(`starved`)을 돌려준다
    — **멈추지는 않는다.** 그 판단(`_stop_if_a_caller_saw_nothing`)은 호출자(`_triage_by_stage`)
    가 **같은 단계 번호의 행을 전부 돈 뒤**에 내린다 — Scout가 개시·정착 두 행으로 갈리면서
    (둘 다 `Stage.SCOUT`), 행 하나만 보고 판단하면 정착이 아직 후보를 못 만든 시점에
    "이 자리는 아무것도 못 봤다"로 너무 일찍 판정하게 된다(#95). 크기 거절은 카운터에 안
    세므로, 작게 띄운 서버 창이 전량을 거절해도 카운터로는 안 선다. 키는 `item.role`이
    아니라 `_caller`다 — 5단계부터는 부르는 역할이 하나라서다(자리 연속 폐기 카운터와 같은 키).
    """
    # 연속 폐기를 run과 자리로 센다(같은 이유·같은 표를 `_triage_by_candidate`가 든다).
    drops = _ConsecutiveDrops()
    tried = {_caller(stage, item.role) for item in items}
    passed: set[tuple[Stage, Role | None]] = set()
    survivors: list[_InFlight] = []
    for item in items:
        role = item.role
        try:
            keep = step(ctx, item, state)
        except ModelGatewayError as exc:
            if not _is_droppable(exc):
                # 서버 쪽 사건이다. `run_pipeline`이 받아 리포트·매니페스트까지 간다.
                raise
            _drop(
                ctx,
                state,
                item.target,
                _REASON_GATEWAY,
                exc,
                drops,
                stage=item.reached.stage,
                role=role,
            )
        except SchemaParseError as exc:
            _drop(
                ctx, state, item.target, _REASON_SCHEMA, exc, drops,
                stage=item.reached.stage, role=role,
            )
        else:
            # 이 자리가 이 단계를 마쳤다. 앞의 실패는 이 자리의 체계적 고장이 아니었고, 이 단계가
            # run 전체로 고장 난 것도 아니다.
            drops.passed({_caller(stage, role)})
            drops.completed()
            passed.add(_caller(stage, role))
            if keep:
                survivors.append(item)
    return survivors, tried - passed


def _triage_by_candidate(
    ctx: RunContext, plan: _TargetPlan, state: _Accumulator, last: int
) -> None:
    """target을 차례로 태운다. **후보 하나의 실패로 run을 잃지 않는다.**

    `BudgetExhausted`는 여기서 잡지 않는다 — 실패가 아니라 정상 종료 경로이고
    `run_pipeline`이 처리한다(모듈 docstring).

    **연속 폐기 카운터는 둘이다 — run과 자리(#197).** 팬아웃 뒤 시도 단위가 `(target, role)`이다.
    자리 카운터는 시도가 죽은 단계의 `_caller(단계, 자리)`를 키로 세므로, 풀 멤버 하나가 전량
    실패하면 그 자리가 자기 카운터로 상한에 닿는다 — 형제 멤버의 완주는 그 키를 안 건드린다.
    run 카운터는 키 없이 세고 죽지 않고 끝난 시도가 지운다. 전문은 `_ConsecutiveDrops`와
    `_run_stage_for_all`의 「둘이다」 절에 있다.

    `ModelGatewayError`·`SchemaParseError`는 후보 단위 실패다. 그 후보만 버리고 계속
    간다. 다만 무조건 잡으면 안 된다:

    | 사건 | 판정 | 대응 |
    | --- | --- | --- |
    | 후보 하나가 컨텍스트를 넘겼다 | 413, 또는 400 + 컨텍스트 초과 문구 | 그 후보만 버린다 |
    | 요청 모양·서버 설정이 틀렸다 | 그 밖의 400 | 첫 건에서 멈춘다(#296) |
    | 서버 쪽 사건 | `status`가 없거나 5xx, 400·413·422 밖의 4xx | 즉시 멈춘다 |
    | 스키마를 못 맞췄다 | `SchemaParseError` | 그 후보만 버린다 |

    **사유를 가르지 않고 센다.** 게이트웨이와 스키마를 따로 세면 둘이 번갈아
    실패할 때 어느 쪽도 상한에 닿지 않고 target 수백 개를 전부 태운다. run 카운터의 리셋
    조건이 "완주"인 것도 같은 이유다 — 사유가 섞여 있다는 사실이 "이 run은 체계적으로
    고장 났다"는 판정을 무르게 하지 않는다.

    **끝까지 돈 뒤 성공 0 백스톱을 본다(#296).** 여기서는 시도 하나가 3~6단계를 관통하므로
    단계별 성공을 `reached`에서 거꾸로 읽는다 — 시도가 들어간 단계는 전부 지났고, 실패했으면
    마지막 단계만 못 지났다. 「Scout가 안 올렸다」도 시도의 완주라 자리 키로 세면 Auditor가
    전부 거절해도 안 선다 — 그래서 키가 `_caller`다.

    ## Scout는 국면 둘로 갈린다(ADR 0012 결정 2)

    4단계는 표에서 개시(`_step_scout_open`)와 정착(`_step_scout_settle`) 두 행이다(둘 다
    `Stage.SCOUT`). **target 하나 안에서 좌석 순서가 바뀐다** — 「좌석1 전 단계 → 좌석2 …」가
    아니라 「전 좌석 개시(국면 A) → 국면 A에서 살아남은 좌석마다 정착부터 끝까지(국면
    B)」다. 동료 확인(같은 target의 다른 좌석이 hop0에서 review인가)이 이 순서에 기댄다 —
    국면 B가 시작될 때는 이미 전 좌석의 hop0 신호를 안다. 역할별 호출 **순서**는 그대로라
    (좌석마다 `_scout_roles(ctx)` 순서로 한 번씩 개시하고, 그다음 그 순서로 정착한다) 인덱스
    기반 실패 주입은 안 흔들린다.

    예외 처리(`_drop`·연속 카운터·`tried`/`passed`)는 **국면마다** 같은 패턴을 적용한다 —
    국면 A에서 실패한 좌석은 국면 B에 들어가지 않는다(그 시도는 거기서 끝난다). **리셋은
    시도가 끝날 때만 한다** — 국면 A에서 죽었을 때, 또는 국면 B가 끝났을 때. run 카운터는
    완주(국면 B가 예외 없이 끝남 — 승격이 안 돼 멈춘 것 포함)만 지우고, 자리 카운터는 그 시도가
    **다 지난 단계**의 키만 지운다(죽은 단계는 빼고). 국면 A만 통과한 것은 아무것도 지우지 않는다 —
    4단계 키는 개시와 정착이 같은 키라, 개시 통과가 지우면 정착이 매번 죽어도 키가 0과 1을 오가며
    상한에 안 닿는다. 국면을 가르는 자리는 하드코딩하지 않고 표에서 읽는다(`_SCOUT_SETTLE_INDEX`,
    `_PHASE_A_STEPS`/`_PHASE_B_STEPS`).
    """
    # 연속 폐기를 **run과 자리로** 센다(#197, `_ConsecutiveDrops`). 시도 단위가
    # `(target, role)`이고, 풀에서 한 멤버가 전량 실패해도 형제 멤버의 성공이 매번 지우던
    # 단일 카운터로는 상한에 안 닿았다(#195). 자리 카운터가 그 멤버를 `_MAX_CONSECUTIVE_DROPS`에
    # 닿게 하고, run 카운터가 서로 다른 대상 둘 이상에 걸친 연속 폐기를 세운다.
    #
    # **국면 A·B가 같은 카운터를 공유한다** — target 여러 개, 국면 둘을 거치며 누적된다.
    drops = _ConsecutiveDrops()
    tried: set[tuple[Stage, Role | None]] = set()
    passed: set[tuple[Stage, Role | None]] = set()
    # Option C — target × Scout 자리로 편다(풀이면 멤버 셋, 단일이면 하나). 풀에서는 같은
    # target의 첫 멤버가 만든 캡슐을 `state.capsules`가 나머지에 물려 준다.
    roles = _scout_roles(ctx)
    for target in plan.targets:
        survivors: dict[Role, _InFlight] = {}
        # 국면 A — 전 좌석의 RETRIEVAL + Scout 개시(hop0)까지.
        for role in roles:
            reached = _Reached()
            item = _InFlight(target=target, reached=reached, role=role)
            finished = False
            try:
                _triage_one(
                    ctx, target, state, last, reached, role=role, item=item, steps=_PHASE_A_STEPS
                )
            except ModelGatewayError as exc:
                if not _is_droppable(exc):
                    # 서버 쪽 사건이다. 여기서 잡으면 남은 target을 전부 실패시키며 돈다.
                    #
                    # **그래도 run을 죽이지는 않는다.** `run_pipeline`이 이 예외를 받아
                    # 리포트와 매니페스트까지 간 뒤 중단 사유를 적는다 — 그러지 않으면
                    # 이 변경이 없애려던 "산출물이 하나도 안 남는다"가 그대로 남는다.
                    raise
                # 셀지·멈출지는 `_drop`이 가른다 — 크기 거절은 안 세고, 크기가 아닌 400은
                # 첫 건에서 멈춘다(#296).
                _drop(
                    ctx,
                    state,
                    target,
                    _REASON_GATEWAY,
                    exc,
                    drops,
                    stage=reached.stage,
                    role=role,
                )
            except SchemaParseError as exc:
                _drop(
                    ctx, state, target, _REASON_SCHEMA, exc, drops,
                    stage=reached.stage, role=role,
                )
            else:
                # 국면 A만 통과했다 — 「완주」는 아직이다(국면 B 끝에서만 리셋한다).
                finished = True
                survivors[role] = item
            entered = {_caller(stage, role) for stage, _ in _TRIAGE_STEPS if stage <= reached.stage}
            tried |= entered
            passed |= entered if finished else entered - {_caller(reached.stage, role)}
            if not finished:
                # 시도가 여기서 끝났다 — 죽은 단계 앞까지는 지났다.
                drops.passed(entered - {_caller(reached.stage, role)})
        # 국면 B — 국면 A에서 살아남은 좌석만, Scout 정착부터 끝까지.
        for role, item in survivors.items():
            reached = item.reached
            finished = False
            try:
                _triage_one(
                    ctx, target, state, last, reached, role=role, item=item, steps=_PHASE_B_STEPS
                )
            except ModelGatewayError as exc:
                if not _is_droppable(exc):
                    raise
                _drop(
                    ctx, state, target, _REASON_GATEWAY, exc, drops,
                    stage=reached.stage, role=role,
                )
            except SchemaParseError as exc:
                _drop(
                    ctx, state, target, _REASON_SCHEMA, exc, drops,
                    stage=reached.stage, role=role,
                )
            else:
                # 죽지 않고 끝났다(국면 B까지, 승격이 안 돼 멈춘 것도 같다) — run 고장이 아니다.
                drops.completed()
                finished = True
            entered = {_caller(stage, role) for stage, _ in _TRIAGE_STEPS if stage <= reached.stage}
            tried |= entered
            through = entered if finished else entered - {_caller(reached.stage, role)}
            passed |= through
            # 지난 단계의 자리 키만 지운다 — Scout가 안 올린 완주는 Auditor 키를 안 건드린다.
            drops.passed(through)
    _stop_if_a_caller_saw_nothing(tried - passed)


def _drop(
    ctx: RunContext,
    state: _Accumulator,
    target: _Target,
    reason: str,
    exc: Exception,
    drops: _ConsecutiveDrops,
    *,
    stage: Stage,
    role: Role,
) -> None:
    """시도 하나를 버리고 기록한다. 연속 상한에 닿으면 `_TooManyDrops`를 올린다.

    **센다면 두 카운터를 같이 올린다(#197)** — run 카운터와 `_caller(stage, role)` 자리
    카운터. 멈추는 조건은 `_ConsecutiveDrops`에 있고 메시지가 어느 쪽인지 적는다.

    ⚠ **시도는 `(target, Scout 자리)`다 — target이 아니다.** 팬아웃 뒤 `items`가 그 곱으로
    펼쳐지고 이 함수가 시도마다 불린다. 단일 `scout`에서는 자리가 하나라 둘이 같고,
    **풀에서만 갈린다.** `role`을 `target_skipped.scout_member`로 실어(#193 ①) 어느 자리가
    죽었는지 남긴다 — 그래야 「모든 자리가 실패한 대상」을 trace에서 셀 수 있고, 리포트의
    `unanalyzed_targets`(팬아웃된 대상 − 후보 낸 대상)가 그 수를 요약으로 낸다.

    **무엇을 세고 언제 멈추는지를 여기서 가른다(#296)** — 기록은 어느 갈래든 먼저 남긴다.
    - 크기 거절(`_is_size_rejection`)은 "이 후보에 대한 사실이지 run에 대한 사실이 아니다".
      카운터를 건드리지 않는다 — **내리지도 않으므로** 체계적 실패와 번갈아 나와도 상한에 닿는다.
    - 크기가 아닌 400(`_rejects_every_request`)은 다음 요청도 같으므로 **첫 건에서** 멈춘다.
    - 나머지(스키마 실패·422·생성 중 타임아웃)는 센다.
    예전에는 호출자가 `systematic=exc.status not in {400, 413}`을 넘겼고, 그래서 400이면
    무엇이든 크기였다.

    **`stage`는 호출자가 준다 (PR #50).** 예전에는 여기서 `Stage.SCOUT`으로 박았는데,
    `_triage_one`은 3~6단계를 연달아 태우므로 auditor에서 죽은 target도 기록은 4가 됐다.
    `run_20260828_0002`의 `target_skipped` 3건이 전부 그랬다 — 실패한 `model_call`은
    셋 다 5단계 auditor인데 skip 기록은 4다. `_emit_skipped`가 자기 docstring에
    "**`stage`가 곧 어디서 잘렸는가다**"라고 적어 뒀고, 박아 둔 값은 그 문장을 못 지킨다.
    """
    _emit_skipped(
        ctx, target.location, stage=stage, reason=reason, detail=error_detail(exc),
        member=role.value, slice_id=target.slice_id,
    )
    state.dropped += 1
    if _rejects_every_request(exc):
        raise _TooManyDrops(
            f"{int(stage)}단계 요청이 크기가 아닌 사유로 거절됐다 — 다음 요청도 같다: "
            f"{error_detail(exc)}"
        ) from exc
    if _is_size_rejection(exc):
        return
    key = _caller(stage, role)
    drops.run += 1
    drops.run_targets.add(_target_key(target.location, target.slice_id))
    drops.by_caller[key] = drops.by_caller.get(key, 0) + 1
    if drops.run >= _MAX_CONSECUTIVE_DROPS and len(drops.run_targets) > 1:
        raise _TooManyDrops(
            f"후보 {drops.run}건이 연속으로 실패했다(자리 무관, 서로 다른 대상 "
            f"{len(drops.run_targets)}개에 걸쳐). 마지막 사유: {exc}"
        ) from exc
    if drops.by_caller[key] >= _MAX_CONSECUTIVE_DROPS:
        seat = f" {key[1].value}" if key[1] is not None else ""
        raise _TooManyDrops(
            f"후보 {_MAX_CONSECUTIVE_DROPS}건이 연속으로 실패했다({int(stage)}단계{seat}). "
            f"마지막 사유: {exc}"
        ) from exc


def _drop_finding(
    ctx: RunContext, state: _Accumulator, finding: Finding, reason: str, exc: Exception
) -> None:
    """Finding 하나를 7단계에서 버리고 기록한다 (해소). `_drop`의 Finding 판이다.

    **`_drop`과 달리 연속 폐기 카운터가 없다.** 그 장치(`_TooManyDrops`)는 target 수백 개를
    태우는 것을 막으려는 것인데 `selected`는 `critic_top_n`으로 이미 묶여 있다(실측 8 이하).
    상한이 다른 두 루프에 같은 카운터를 두면 어느 쪽이 상한에 닿았는지가 사유에서 사라진다.

    **카운터가 아닌 정지 둘은 여기도 있다(#296).** 크기가 아닌 400은 기록한 뒤 첫 건에서
    멈추고(다음 Finding도 같다), 고른 Finding 전부가 실패하면 `_analyze`가 루프 끝에서 멈춘다.
    둘 다 `stopped_after`가 7이라 어느 루프였는지가 사유에서 안 사라진다.

    **`state.dropped`가 아니라 `critic_failed`를 올린다.** 그쪽은 시도를 세고 이쪽은
    Finding을 세므로 한 수로 묶으면 분모가 섞인다(`_Accumulator` 주석과 같은 판단).
    """
    state.critic_failed += 1
    state.critic_failed_ids.add(finding.finding_id)
    ctx.trace.emit(
        EventKind.CRITIC_FAILED,
        stage=Stage.CRITIC,
        subject_kind="finding",
        subject_id=finding.finding_id,
        reason=reason,
        error_detail=error_detail(exc),
    )
    if _rejects_every_request(exc):
        raise _TooManyDrops(
            f"{int(Stage.CRITIC)}단계 요청이 크기가 아닌 사유로 거절됐다 — 다음 요청도 같다: "
            f"{error_detail(exc)}"
        ) from exc


def _emit_planned(ctx: RunContext, target: Location, *, slice_id: str | None) -> None:
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
        # 슬라이스 타깃이면 그 id — entry가 같은 두 슬라이스를 가른다. 없으면 키를 안 만든다
        # (슬라이스 없는 run의 이벤트가 이 필드 이전과 같게 남는다). `_emit_skipped`와 같은 규칙.
        **({"slice_id": slice_id} if slice_id is not None else {}),
    )


def _emit_skipped(
    ctx: RunContext,
    target: Location,
    *,
    stage: Stage,
    reason: str,
    detail: str = "",
    member: str | None = None,
    slice_id: str | None = None,
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
        # 어느 Scout 자리가 이 target에서 죽었나(#193 ①). 팬아웃 뒤 시도 단위가 `(target, 자리)`라,
        # 이것이 없으면 「모든 자리가 실패한 대상」과 「한 자리만 실패한 대상」을 못 가른다.
        # 단일 프로필은 `scout` 하나라 항상 같다. `None`이면 예산 절단(팬아웃 전)이라 자리가 없다.
        **({"scout_member": member} if member is not None else {}),
        **({"slice_id": slice_id} if slice_id is not None else {}),
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
    item: _InFlight | None = None,
    steps: tuple[tuple[Stage, TriageStep], ...] = _TRIAGE_STEPS,
) -> _InFlight:
    """target 하나를 한 Scout 자리(`role`)로 `steps`(기본은 3~6단계 전부)에 태운다.

    승격되지 않은 후보는 멈춘다. **후보 단위(`execution.mode: per_candidate`, 기본값)의
    경로다.** 단계 단위는 `_triage_by_stage`가 **같은 `_TRIAGE_STEPS`를 바깥 루프만
    뒤집어** 돈다. 풀 프로필에서 `_triage_by_candidate`가 target × 멤버로 이 함수를 여러
    번 부르고(Option C), 첫 멤버가 만든 캡슐을 `state.capsules`가 나머지에 물려 준다.

    **`item`을 넘기면 그 객체에 이어서 쌓는다.** Scout 개시·정착을 국면으로 나눌 때
    (`_triage_by_candidate`) 같은 시도(target, role)의 두 호출이 같은 `_InFlight`
    (`capsule`·`scout_session`)를 봐야 하므로, 국면 A 호출이 만든 `item`을 국면 B 호출에
    그대로 다시 넘긴다. `None`이면(기본, `steps`도 기본일 때만 쓰는 옛 경로) 새로 만든다.

    **`reached.stage`를 노드에 들어가기 직전에 올린다 (PR #50).** 끝난 뒤에 올리면 그
    노드에서 죽은 target이 앞 단계로 기록된다 — 고치려던 것이 정확히 그 어긋남이다.
    (그 올리는 일은 이제 각 `_step_*`이 자기 첫 줄에서 한다.)
    """
    resolved_item = (
        item if item is not None else _InFlight(target=target, reached=reached, role=role)
    )
    for stage, step in steps:
        if last < stage:
            return resolved_item
        if not step(ctx, resolved_item, state):
            return resolved_item
    return resolved_item


def _recorded_thresholds(config: HarnessConfig) -> dict[str, float]:
    """`RunRecord.thresholds` — 설정값이 아니라 **실제로 도는 값**이다.

    `auditor_tool_hops`는 프로필 공통이라 Fake Auditor를 쓰는 프로필에도 걸리는데, 그 좌석은
    `Finding` 한 방 판이라 루프를 안 돈다(`nodes.auditor.loop_problem`의 면제). 그대로 싣으면
    manifest는 「켰다」인데 `auditor_hop`은 0건이라 「켰는데 한 번에 answer했다」로 읽힌다 —
    안 도는 값은 키를 뺀다(`Thresholds.flatten`이 꺼진 상한을 적는 방식).
    """
    thresholds = config.thresholds.flatten()
    looped = Role.AUDITOR.value in config.profile.roles and auditor.tool_hops_in_effect(
        config.model_for(Role.AUDITOR.value).prompt_template_version,
        config.thresholds.auditor_tool_hops,
    )
    if not looped:
        thresholds.pop("auditor_tool_hops", None)
    return thresholds


def _new_record(
    spec: RunSpec,
    config: HarnessConfig,
    run_id: str,
    started_at: datetime,
    artifacts: list[Artifact],
    tool_images: dict[str, str],
    model_sampling: dict[str, ModelSampling],
    compile_flags: CompileFlags | None,
    slicer: dict[str, str | int | None] | None = None,
    serving_launch_intended: dict[str, list[str]] | None = None,
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
        thresholds=_recorded_thresholds(config),
        seed=config.seed,
        # 하네스가 **보낸** 샘플링.
        sampling=config.sampling,
        # 각 실서빙 모델이 `generation_config.json`에 **선언한** 샘플링 (#99). 서버가
        # 모델 파일에서 적용하는 `top_k` 등을 하네스는 안 보내므로 이 필드로만 기록에 남는다.
        # ⏵ 2026-10-08 — 위 줄(2026-08-29)은 낡았다. registry `sampling`을 선언한 모델은
        # `temperature`·`top_p`·`top_k`·`min_p`·`presence_penalty`를 요청에 싣고(#261, `top_k`는
        # 2026-09-26 선언부터) 그 값은 아래 `model_request_params`에 남는다. `repetition_penalty`는
        # 여전히 안 보낸다.
        # 어댑터가 읽어 factory가 `deps`에 실은 값이다 — 러너는 `src/adapters/`를 import 하지
        # 않으므로(계층 규칙) `tool_images`와 같은 경로로 받는다.
        model_sampling=model_sampling,
        # registry가 선언해 하네스가 **요청에 실은** 모델별 파라미터. `model_sampling`과
        # 달리 어댑터를 안 거친다 — config에서 바로 유도되므로 `deps`를 탈 이유가 없다.
        model_reasoning_effort=config.reasoning_efforts(),
        model_request_params=config.request_params(),
        # 장비 파일이 의도한 기동 인자(#99) - 어댑터가 읽어 `deps`로 나른다. 의도지 증거가 아니다.
        serving_launch_intended=serving_launch_intended,
        # 어댑터가 낸 컴파일 조건 (#74). `tool_images`와 같은 경로로 받는다 — 러너는
        # `src/adapters/`를 import 하지 않으므로 `_STD_ARGS`를 여기서 읽을 수 없고, 읽을 수
        # 있더라도 읽으면 안 된다: 값을 두 곳에 두면 갈라진다.
        compile_flags=compile_flags,
        # `--slices`로 돈 run의 슬라이서 산출(ADR 0012 결정 1). 어댑터가 만든 값을 받는다.
        slicer=slicer,
        keep_responses=list(spec.keep_responses) or None,
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
        # Fake 런타임이면 컨테이너가 정말 없다. 그 밖의 런타임에서 비는 것은 「이미지 digest를
        # 못 기록했다」다 - `tool_images`는 run 시작 전(`build_deps`)에 `images()`가 채우므로 도구
        # 단계에 닿았는지와 무관하고, 비는 사유는 이미지가 로컬에 없음 · 런타임에 못 닿음 ·
        # sif 없음이다(보고서 4절의 WSL run은 Docker 소켓이 없었다). 「컨테이너 없이 실행」이라
        # 쓰면 설정 오류로 읽힌다(#479 (e)).
        runtime = record.tool_runner
        why = (
            "컨테이너 없이 실행"
            if runtime == "fake"
            else f"{runtime} 런타임인데 도구 이미지 digest를 기록하지 못했다 - "
            "이미지가 없거나 런타임에 닿지 못했다"
        )
        warnings.append(f"tool_images가 비어 있다 ({why}). 모델 비교표에서 제외한다")
    if served_models:
        # **Fake 역할은 호출자가 이미 빼고 준다.** Fake 모델의 checksum 0은 빠진 값이
        # 아니라 **맞는 값**이다 — `FakeModelClient`는 프로세스 안에서 도는 코드라 해시할
        # 가중치도 토크나이저도 존재하지 않는다. 없는 것을 "미기록"으로 경고하면 dev
        # 프로필의 모든 run이 매번 이 줄을 뱉고, 그러면 진짜 문제가 묻힌다 — `tool_runner`를
        # 이 목록에서 뺀 것과 같은 논법이다(`schemas/run.py`의 그 필드 주석).
        #
        # 판정이 **역할별**인 것도 의도다. 한 프로필 안에서 Fake와 실서빙이 섞일 수 있으므로
        # (옛 `single_gpu_12g`), 프로필 단위로 켜고 끄면 그 프로필의 실서빙 역할을 놓친다.
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
