"""ContainerToolRunner — Docker·Singularity·podman 안에서 실제 도구를 돌리는 ToolRunner.

계약은 `FakeToolRunner`와 같다(engineering-guide.md §8.3): **실패해도 예외를 던지지 않고
`Evidence(ok=False, error=...)`를 돌려준다.** 예외는 샌드박스 자체를 띄울 수 없는 경우뿐이고
(`SandboxUnavailable`), 그 판정은 생성 시점에 한다 — run 도중에 알면 그때까지 쓴 시간을 버린다.

## exit code 관례 (이슈 #61)

`nodes/signals.with_signal()`이 STATIC Evidence를 `exit_code`로 거른다. 도구 기본값은 그
관례와 맞지 않아 아래 플래그로 강제한다.

| 도구 | 플래그 | 효과 |
| --- | --- | --- |
| semgrep | `--error` | 발견 시 1. 기본값은 발견해도 0이라 모든 정적 신호가 "없음"이 된다 |
| clang-tidy | `--warnings-as-errors=*` | 경고가 1건이라도 있으면 non-zero |

**clang-tidy는 exit code만으로 "경고를 찾음"과 "대상이 컴파일되지 않음"을 구분하지 못한다.**
둘 다 non-zero다. 그대로 두면 컴파일 실패가 정적 신호로 집계된다. 그래서 출력에
`clang-diagnostic-error`가 있으면 도구 실패(`ok=False`)로 내린다 — 남은 구분 수단이 출력
문자열뿐이라는 사실 자체가 #61 항목이다(⏵ 2026-10-12 닫음 — 서빙 2 run에서 마커가
성립했고 `Evidence.findings`는 안 넣었다).

## 로그 규약 (engineering-guide.md §6)

`Evidence.digest`는 로그 파일 내용의 sha256이고 **로그의 각 절 첫 줄이 실제로 실행된
명령**이다. `ctx.call_tool`이 trace에 남기는 argv는 노드가 만든 자리표시자이고 진짜 명령은
이 모듈이 조립하므로(`plan()`) 둘은 다르다. `Evidence` 스키마에 argv 필드가 없으므로
실행 명령을 로그에 적어 digest에 포함시킨다.
그러면 "무엇이 실행됐는지"가 사후 검증 가능하다.

## 런타임이 강제하지 못하는 항목

Singularity 3.7.3은 `cpu_limit`·`memory_mb`를 강제하지 못한다(HPC에서는 SLURM 소관).
실행은 성공했으므로 `ok=True`이고 `Evidence.error`로 흘리지 않는다 — 자원 상한이 안 걸린
것은 실행 실패가 아니다. `UNENFORCED`에 적고 tools/README.md가 안내한다.

**반대 방향도 있다: `read_only`는 Singularity에서 끌 수 없다.** SIF가 immutable이라 컨테이너
루트가 항상 읽기 전용이다. 그래서 `read_only=False`를 주면 Docker에서만 루트 쓰기가 열려
두 런타임의 결과가 갈린다 — 아래 semgrep 크래시가 정확히 루트 쓰기 금지에서 나온 것이므로
가상의 위험이 아니다. `DEFAULT_SANDBOX`가 True 고정이라 지금은 드러나지 않고, 끄는 호출부가
생기면 그때 거절해야 한다(`container_argv` 주석). `UNENFORCED`에 넣지 않은 것은 그쪽이
"강제하지 **못하는** 항목"이고 이것은 "**끌 수 없는** 항목"이라 뜻이 반대이기 때문이다.

## asan·gdb·poc는 실행까지 성공해야 ok=True다

Evidence Gate E4(proven)의 조건이 `SANITIZER`/`CRASH`/`POC_RUN` Evidence다. 컴파일 성공을
실행으로 세면 **한 번도 돌지 않은 프로그램이 재현 완료로 기록된다.** 그래서 `ok=False`로
내리는 갈래가 셋이다 — 명령이 하나도 안 돈 경우(`not steps`), 빌드에서 끊긴 경우
(`len(steps) < total`), **대상의 판정으로 읽을 수 없는 경우**(asan·gdb는 대상을 띄우지도
못한 경우이고, `poc`는 그보다 넓다 — `137`이면 이미지는 떴어도 대상 자신의 OOM과 갈리지
않는다). 마지막 것을 알아내는 방법이 도구마다 다르다.

**asan**은 마지막 명령이 대상 바이너리라 종료 코드 125·126·127(`_EXEC_FAILED`)이 곧
「띄우지 못함」이고, 그 뒤로는 종료 코드를 보지 않는다 — 뜨기만 하면 크래시(non-zero)든
깨끗한 종료(exit 0)든 `ok=True`다. ⏵ **2026-10-09 — `poc`는 더 넓은 판별자를 쓴다**
(ADR 0014 · #513): `_poc_is_harness_failure`가 `_EXEC_FAILED`에 `124`·`137`·`255`·`>255`·음수를
더하고, 데몬 미도달 `exit 1`도 가른다. 과제 이미지를 우리가 안 만들기 때문에 환경이 낼 수 있는
코드가 더 넓다. **gdb**는 마지막 명령이 **gdb 자신**이라 종료 코드로는
대상이 떴는지 알 수 없다(대상이 안 떠도 1·255, 대상이 `return 127;`이면 127 — 2026-09-30
실측, gdb 12.1·15.1). 그래서 gdb가 **stdout**에 남기는 대상 수명주기 줄이 있을 때만 떴다고
본다. 오류(`Error in sourced command file`·`During startup program exited with code N`)는
stderr다.

그것이 `judge()` docstring의 *"돌았는데 아무것도 못 찾음: `ok=True, exit_code=0`"*이다.
**즉 `ok=True`가 보증하는 것은 「실행됐다」까지이지 「크래시했다」가 아니다.** 그 차이를 가르는
곳은 이 파일이 아니라 게이트다 — `gate.evaluate()`의 `p4`가 `ok`·`kind`에 더해 **종료 코드가 0도
`None`도 아닐 것**을 요구하므로 exit 0은 `ok=True`여도 E4가 아니다(이슈 #232). `ok`의 뜻은
「떴다」가 맞아서 `judge()`는 안 바꿨다 — exit 0을 여기서 `ok=False`로 내리면 그 건은
F-11(`queued`)로 가는데 게이트 쪽은 F-10b(`validated` 홀드)로 보낸다. 대신 비-0은 **크래시의 대용
지표일 뿐이다** — 대상이 스스로 비-0을 돌려줘도 같은 값이 나온다. 산출물을 `expected_signal`과
대조하는 코드는 여전히 **없다**.

⏵ **2026-10-09 (ADR 0014) — 컨테이너 경로의 E4가 열렸다.** `poc`가 과제 자신의 `-vul` 이미지를
돌리므로 그 Evidence는 **실제로 뜬 프로그램**의 것이다. 🔴 **열린 것은 「실행됐다」까지다** —
게이트 전이(`p4`)는 종료 코드가 0도 `None`도 아닐 것을 따로 요구하고(#232), 그 run이 실제로 E4를
내는지는 **미실측이다**(그 뒤로 돈 run이 0건). 전 판은 *"픽스처에 `main`이 없어 링크되지 않으므로
컨테이너 경로에서 E4는 도달 불가"*였고 그 전제가 바뀐 것이다 — 우리가 링크하지 않는다.
⚠ **Fake 경로에는 그 방벽이 없다** — `fake_tools._run_repro()`가 실행 대신 대상 소스의 존재를 보고
`ok=True`를 낸다.

⏵ **2026-10-13 (#232) — 환경이 낸 종료 코드는 `p4`에 안 닿는다 (코드 대조).** `124`는 `_execute`가
타임아웃을 `ok=False`로 쓰고 `judge()`까지 오지 않는다. `125`~`127`은 asan에서 `_EXEC_FAILED`로,
`poc`에서 `_poc_is_harness_failure`로 `ok=False`가 되고 gdb는 수명주기 줄이 없으면 `ok=False`다.
`poc`의 `137`·`255`·`>255`·음수도 `ok=False`다. ⚠ **남은 구멍 하나: asan은 `137`·`255`·`>255`·음수를
거르지 않는다**(위 「한 도구만 바꾼다」) — 그 값이 `ok=True`로 올라와 `p4`를 참으로 만든다. 환경의
SIGKILL(`--memory`)과 대상 자신의 OOM이 갈리지 않고, `255`는 gdb 신호 정지의 값이기도 해서
게이트에서 일괄로 못 뺐다. 이 PR은 바꾸지 않았고 `p4` 스위치를 다시 여는 날 함께 정한다.

⏵ **2026-10-13 (#232) — 크래시 관측을 기록한다 (읽지 않는다).** `observe_crash()`가 대상 로그의
sanitizer 배너를 찾아 `Evidence.crashed`·`banner`에 싣는다. **어느 소비자도 이 값을 읽지 않는다** —
배너 철자가 sanitizer 문서에서 왔고 실제 로그로 대조한 것은 재생 14건뿐이라 대부분 미검증이다
(`_CRASH_BANNER` 주석). 실제 `poc_run` 크래시 녹음이 쌓이면 그것으로 철자를 대조한 뒤
`p4`를 읽게 할지 정한다.

## 입력 바이트 — `poc`는 먹인다, 나머지 셋은 안 받는다 (ADR 0014)

`ToolArgs.input_bytes`가 있으면 그 도구의 scratch(`/work`)에 `write_input`으로 쓰고, 로그 머리의
노드 argv에서 자리표(`INPUT_PLACEHOLDER`)를 `/work/<이름>`으로 바꾼다 — 호스트 경로는 컨테이너
안에서 안 풀린다.

⏵ **2026-10-09 (ADR 0014) — `poc`가 그 바이트를 실제로 먹인다.** 그 도구는 과제 자신의 `-vul`
이미지를 돌리고 쓴 파일을 `POC_IN_IMAGE`(`/tmp/poc`)로 **읽기 전용** 바인드한다. 그래서 **그
도구의 관측(`ok`·`exit`·꼬리)은 PoC의 함수다.** 전에는 `{SCRATCH}/poc`라는 **없는 실행 파일**을
돌려 매 홉 `exit 127`이었고(실서빙 run `run_20261007_0001`의 `poc_run` Evidence 7건 전부
`ok=0`) 어떤 바이트를 내도 관측이 같았다.

🔴 **나머지 셋(`asan`·`gdb`·`build`)은 그대로 안 받는다 — 한 도구만 바꿨다.** 셋도 받게 하려면
**우리가 대상을 빌드**해야 하고, 그것이 기각된 A안이다(우리 clang이 CyberGym 번들을 컴파일하지
못한다 — 이슈 #507 · 과제 1,507건의 빌드 규칙이 제각각). 셋의 명령은
`tests/test_container_tools.py::test_no_executed_container_command_receives_the_input_today`가
계속 문다.
`tests/test_container_tools.py::test_no_executed_container_command_receives_the_input_today`가
그것을 실행된 argv로 잰다(그 parametrize에서 `poc`가 빠졌다 — 그쪽은
`tests/test_poc_tool_feeds_the_input.py`가 든다). 쓰지 못하면 **실행하지 않고**
`Evidence(ok=False)`다.

## index는 이제 진짜 그래프를 만든다 (이슈 #63)

`index`는 `tools/harness-tools/indexer.py`(tree-sitter)다. 그전에는
`clang -fsyntax-only`였는데 **구문 검사일 뿐이고 `EvidenceKind.GRAPH`라는 이름이 그보다
강한 것을 주장했다.** 더 나쁜 것은 include 경로가 필요한 실제 저장소에서 그 명령이
실패해 `Evidence(ok=False)`가 되고, `gate.py`가 `usable = [ev for ev in evidences if ev.ok]`로
거르므로 **모든 Finding이 E2를 넘지 못했다는 점이다.** tree-sitter는 전처리도 링크도 하지
않아 include 경로 없이 파싱되므로 그 벽이 사라진다.

**여전히 근사다.** 구문만 보므로 함수 포인터 호출·매크로로 감싼 호출·같은 이름의 `static`
함수를 구분하지 못한다. 놓친 것은 조용히 버리지 않고 센다 — 호출부의 누락은
`unresolved_calls`, 심볼의 누락은 `unresolved_symbols`다. 그 값이 크면 그 저장소에서 이
그래프의 커버리지가 낮다는 뜻이고, E2를 얼마나 믿을지는 그것을 보고 판단해야 한다.

**캡슐까지 이어졌다(2026-08-20).** 산출물(`{SCRATCH}/index.json`)의 호스트 경로는
`index_output_path()`가 내고, `adapters/index_graph.py`가 그것을 읽어 두 Retriever의
`callers`/`callees` 섹션을 채운다. `types`는 캡슐 섹션이 아니다 — index-v2에 타입 표가
생겼지만 NE resolver가 요청받았을 때 주는 조회 표이고 캡슐로 싣지 않는다
(`index_graph.GRAPH_SECTION_KINDS` 주석). 헤더(`.h`)와 `.inc`도 인덱서에 넘어간다 — `.inc`는
실제 코드가 통째로 들어 있어 빠지면 대상의 대부분이 사라지고, `.h`에는 매크로·타입 정의와
`static inline` 함수가 있다(`_INDEX_SUFFIXES` 주석).

## semgrep 룰셋은 이미지에 구워 넣는다

`--config=p/c`는 레지스트리에서 룰을 내려받는데 샌드박스는 `network=False`라
`--network=none`으로 뜬다. 우회는 없다 — `network=True`는 disclosure-policy.md 위반이라
이 클래스가 거부해야 하는 값이다. 그래서 이미지 빌드 시점에 룰셋을 받아 `/opt/semgrep-rules`
아래에 두고 런타임에는 로컬 경로만 쓴다. 룰셋 버전이 이미지 digest에 포함되므로
`RunRecord.tool_images`가 도구 버전과 룰셋 버전을 함께 고정한다(tools/README.md).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from src.adapters.fake_tools import TOOL_KINDS, SandboxUnavailable, sources, write_input
from src.ids import IdFactory
from src.interfaces.tool_runner import SandboxSpec, ToolArgs, with_input_path
from src.paths import PathEscape, safe_join
from src.schemas.common import Location
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence
from src.schemas.run import CompileFlags

# `nodes/sandbox.DEFAULT_SANDBOX.image`와 같아야 한다. 어댑터는 오케스트레이터를 import 하지
# 않으므로(engineering-guide.md §3) 값을 복제하고 tests/test_container_tools.py가 대조한다.
# v2부터 인덱서가 index-v2(조회 표)를 낸다 — v1 이미지는 지우지 않는다(지난 run 매니페스트가
# 그 digest를 적었다). ⏵ 2026-10-04 — #304부터 이 트리로 빌드한 v3 이미지는 index-v3를 낸다(태그는
# 그대로다 — 그 전에 빌드한 v3 이미지는 다시 빌드해야 v3 표가 나온다, Dockerfile 머리 주석).
# ⏵ 같은 날 인덱서가 index-v4(`includes`·`enum_constants`)가 됐다 — 태그는 여전히 v3다.
DEFAULT_IMAGE = "harness-tools:v3"

# 컨테이너 안 경로. 분석 대상은 항상 읽기 전용이고, 쓰기는 scratch 한 곳에서만 일어난다.
MOUNT = "/src"
SCRATCH = "/work"

# 이미지에 구워 넣은 룰셋 경로. 런타임에 레지스트리를 보지 않는다.
SEMGREP_CONFIGS = ("/opt/semgrep-rules/c", "/opt/semgrep-rules/harness")

# 이미지에 구워 넣은 인덱서(`tools/harness-tools/indexer.py`)와 그 산출물 이름.
# 산출물은 scratch에 떨어지므로 호스트에서는
# `{artifacts_root}/{run_id}/scratch/index/{INDEX_FILE}`이고, 그 경로를 조립하는 곳은
# `index_output_path()` 하나다 — `adapters/index_graph.py`가 읽는다(이슈 #63).
_INDEXER = "/opt/indexer.py"
INDEX_FILE = "index.json"

# 인덱서에 넘길 파일 목록. **명령줄에 안 싣고 scratch에 쓴다** - 호스트의 docker 명령줄이 파일
# 수에 비례해 커지면 Windows `CreateProcess`의 상한(32,767자)에 걸린다: 소스 1,200개(37,274자)
# 프로브가 `[WinError 206]`으로 죽었고(S20 Phase 15 감사가 이 Windows 호스트에서 낸 프로브 -
# run ID 없음, 다시 재지 않았다), 그 `OSError`는 `ok=False`·exit 127이 되어 **인덱스 없이
# run이 조용히 이어졌다.** 목록은 NUL로 구분한 경로들이다(공백·개행이 든 이름도 안 깨진다).
#
# 풀어 넘기는 일은 컨테이너 안에서 일어난다. `sh`가 아니라 `python3 -c`인 것은 이미지가 이미
# `python3 /opt/indexer.py`로 도는 곳이라 새 전제가 없고, `xargs`는 목록이 길면 인덱서를 **여러
# 번** 나눠 부르는데 인덱서는 한 번에 전체를 봐야 해서다(`index.json`이 호출마다 덮인다).
# `os.execvp`라 인덱서의 종료 코드·stdout·stderr가 그대로 컨테이너의 것이 된다. 코드에 백슬래시·
# 큰따옴표가 없다(`bytes(1)` = NUL) - Windows 명령줄 인용을 거쳐도 안 변한다.
# 인자: `<목록 파일> <실행할 인자들...>` - 앞 인자 뒤에 목록의 경로가 이어 붙는다.
#
# 한계: 컨테이너 안 `execve`의 인자 상한(리눅스 기본 약 2MB)이다 - 경로 길이 60자 기준 수만 개.
INDEX_LIST_FILE = "index-files.txt"
_INDEX_LAUNCHER = (
    "import os,sys;"
    "fs=open(sys.argv[1],'rb').read().split(bytes(1));"
    "os.execvp('python3',['python3',*sys.argv[2:],*map(os.fsdecode,filter(None,fs))])"
)

# 이미지에 구워 넣은 비대화형 gdb 명령 파일. pwndbg 같은 UI 확장을 기본 경로에 싣지 않는다.
_GDB_SCRIPT = "/opt/gdb.batch"

# scratch 하위 디렉터리 이름은 도구 이름이다(`_execute`가 `tool`로 만든다).
# `nodes/index.INDEX_TOOL`과 같은 값이어야 하지만 어댑터는 오케스트레이터를 import 하지
# 않으므로(engineering-guide.md §3) 여기 복제하고 tests/test_container_tools.py가 대조한다.
#
# **이 파일 안에서도 이 상수를 써야 한다.** 두 곳이 `tool == "index"` 리터럴을 쓰고 있었고,
# 두 상수를 함께 바꾸면 대조 테스트는 통과하는데 리터럴만 뒤처져 인덱서가 조용히
# `_COMPILE_SUFFIXES`를 받는다 — `.inc`가 빠져 #66의 실패가 재현된다.
INDEX_TOOL = "index"

# 인덱서가 stdout에 남기는 요약 한 줄(`indexer.main()`)의 심볼 수. `judge()`가 0개를 가른다(#576).
# 줄 머리에 고정한다 — 줄 중간에 같은 철자가 있어도 요약으로 읽지 않는다.
# `tests/test_indexer.py`가 실제 인덱서 출력으로 이 철자를 문다.
#
# **인덱서 쪽은 안 고친다.** `tools/harness-tools/indexer.py`는 심볼 0개를 실패로 안 본다(그
# 스크립트 docstring의 「종료 코드」 절 — 헤더뿐인 대상은 정상이라 exit 0). 그리고 그 파일은
# 이미지의 `/opt/indexer.py`와 **바이트가 같아야** 한다 — `src/preflight.py`가 sha256을 대조하고
# 한 글자(docstring 포함)만 달라도 ERROR다. 그래서 「0개 = 근거 아님」은 읽는 쪽 `judge()`가 든다.
# 그 스크립트가 「`judge()`는 `index`를 exit 0이면 ok로 본다」를 현재형으로 말하는 자리는
# **셋**이다 — 모듈 docstring 둘(`judge()`가 … 판정한다 / 「빈 그래프로 E2가 열린다」는
# 문제가 남는데 … open-questions의 그 항목에 적었다 — 이슈 #576이 그 뿌리를 든다)과
# 코드 주석 하나(`judge()`가 exit 0을 …).
# 셋 다 이 날 이후 낡았다(#576 — 0개는 `ok=False`로 내려 E2가 안 열린다). **그 파일은
# 못 고친다** — 이미지와 바이트가 같아야 해서다(위). 그래서 정정은 여기와 그 항목에만 있다.
_INDEX_SYMBOL_COUNT = re.compile(r"^# index: (\d+) symbols,", re.MULTILINE)

# 런타임이 `SandboxSpec`의 어느 필드를 강제하지 못하는가. 실행 실패가 아니므로 Evidence에는
# 남기지 않는다(모듈 docstring).
UNENFORCED: dict[str, tuple[str, ...]] = {
    "docker": (),
    # podman은 rootless인데도 cpu_limit·memory_mb를 강제한다 — 2026-08-17 ATOM 실측에서
    # `--memory=256m`을 준 컨테이너 안 `memory.max`가 정확히 268435456(256MiB)이었다.
    # cgroup v2 위임 덕이다. 그래서 singularity가 아니라 docker와 같은 편(빈 튜플)이다.
    "podman": (),
    "singularity": ("cpu_limit", "memory_mb"),
}

# 컴파일 대상으로 넘길 확장자. 헤더·파이썬은 clang에 직접 주지 않는다.
#
# **비교는 소문자로 내려서 한다**(`.CPP`·`.C`도 잡힌다) - `preprocess.classify`가 같은 방식이다.
# 한쪽만 대소문자를 가리면 분류는 SOURCE인데 도구는 그 파일을 안 받는다(`_suffix`).
# ⚠ `.C`(대문자)는 C++ 소스이고 소문자로 내리면 `.c`다 - 언어 판정은 `_is_cxx`가 원문 철자를 본다.
#
# C++의 다른 철자(`.cxx`·`.c++`·`.cp`)는 clang이 C++로 아는 확장자다. 헤더(`.hpp`·`.hh`·`.hxx`)와
# `.tcc`는 `.h`와 같이 여기에 없다.
#
# **아래 `_INDEX_SUFFIXES`와 일부러 갈라져 있다. 합치면 build가 깨진다.**
# `.inc`는 `#include`로 펼쳐지는 함수 본문 조각이라 **단독 컴파일이 되지 않는다.**
# clang 계열(build·clang-tidy·asan·gdb)은 `target.c`만 주면 전처리가 `.inc`를 끌어오므로
# 직접 줄 이유가 없고, 직접 주면 컴파일이 실패해 `ok=False`가 된다 —
# **일어나지 않은 실패가 신호로 기록된다.**
_COMPILE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cp")

# `_COMPILE_SUFFIXES` 중 C++인 것. 나머지가 C다.
#
# **이 목록은 `_COMPILE_SUFFIXES`의 부분집합이어야 한다.** 여기 있는데 저기 없으면 그
# 확장자는 애초에 컴파일 대상이 아니라 이 분류가 한 번도 쓰이지 않고, 저기 있는데 여기
# 판정에서 빠지면 C++ 파일이 C 방언을 받아 clang이 거부한다 — 2026-08-25 실측
# (`run_20260825_0001`)의 `invalid argument '-std=gnu17' not allowed with 'C++'` 5건이
# 정확히 그것이다.
_CXX_SUFFIXES = (".cc", ".cpp", ".cxx", ".c++", ".cp")

# 인덱서(`index`)에만 넘길 확장자. tree-sitter는 **전처리를 하지 않아서** 갈라진다 —
# `clang -fsyntax-only`를 쓰던 시절에는 전처리가 `#include`를 펼쳐 `.inc`를 자동으로
# 끌어왔지만 지금은 직접 주지 않으면 28바이트짜리 `#include` 한 줄만 보고 그 뒤의
# 17KB를 못 본다. 그리고 **에러가 아니므로 "그 파일에 함수가 없다"와 구별되지 않는다.**
#
# **여기 있는 확장자는 전부 `tools/harness-tools/indexer.py`의 `_GRAMMARS`에도 있어야
# 한다**(`tests/test_indexer.py`가 부분집합을 문다). 그 스크립트는
# 이미지 안에서 독립 실행되고 `src/`를 import 하지 않아 목록이 양쪽에 따로 존재한다 —
# **어긋나면 예외가 아니라 `skipped`에 이름만 남고 조용히 빈 그래프가 나온다.**
#
# ⏵ **이 목록은 더 이상 `_COMPILE_SUFFIXES`를 포함하지 않는다.** 컴파일 쪽에 `.cxx`·`.c++`·`.cp`가
# 들어왔는데 인덱서 문법 표(`_GRAMMARS`)에는 없고, 그 스크립트는 이미지 `harness-tools:v3`에 sha로
# 핀되어 있어 못 고친다. 그래서 **인덱서가 파싱하는 확장자만 직접 적는다** - 보내 봐야 `skipped`에
# 이름만 남는다. 같은 이유로 `.hpp`는 보내고(`_GRAMMARS`에 있다) `.hh`·`.hxx`는 안 보낸다.
# ⚠ **인덱서는 대소문자를 가린다**(`path.suffix not in _GRAMMARS`) - `.CPP`·`.H`는 컴파일 대상이지만
# 인덱스에는 안 들어간다. 대소문자·나머지 C++ 철자를 인덱스에 넣으려면 인덱서와 이미지를 올려야 한다
# (`_BAKED_INDEXER_SHA256`).
#
# **`.h`도 보낸다(#63, index-v2).** 매크로·타입 정의의 대부분과 헤더 `static inline` 함수가
# 거기 있어서, 안 보내면 NE resolver가 쓸 조회 표가 비고 슬라이스가 헤더 함수에 걸칠 때
# Auditor가 그 함수 대신 앞뒤 창을 받는다. v1 심볼·간선에도 헤더 함수가 들어간다 — 따로
# 「조회 전용」 목록을 두면 한 index.json이 같은 파일에 두 답(v2 표엔 있고 `symbols_for`는
# `[]`)을 내고, 프로세스 경계를 넘는 확장자 목록이 하나 더 생긴다. 헤더는 여전히 clang에
# 직접 주지 않는다 — 단독 컴파일 대상이 아니다(`_COMPILE_SUFFIXES`).
#
# ⚠ `tools/harness-tools/indexer.py` ~212줄의 주석(*"`.hpp`는 알지만 아직 넘어오지 않는다"*)은
# **낡았다** - 지금은 이 목록이 `.hpp`를 보낸다. 그 파일은 이미지 sha 핀
# (`_BAKED_INDEXER_SHA256`)이라 주석 한 줄도 못 고친다 - 이 줄이 정정이다.
# 인덱서가 안 받는 SOURCE는 로그 머리의 `# 인덱서 미전달: …`이 센다.
#
# ⚠ **대상 트리 밖의 헤더는 여전히 안 온다** — 인덱서는 `sources(workdir)`(대상 트리)만 받는다.
# 평가 표본의 `build_context/generated_headers`(설정 매크로)는 슬라이서만 `-I`로 본다
# (`slicer_compile.py`의 generated_headers).
_INDEX_SUFFIXES = (".c", ".cc", ".cpp", ".inc", ".h", ".hpp")

# 컴파일 방언. **clang을 쓰는 모든 도구가 같은 값을 쓴다 — 단 그 규칙은 언어 안에서만
# 성립한다.**
#
# 원래 이 상수는 문자열 하나(`-std=gnu17`)였고 세 도구가 그것을 공유했다. 그런데
# `_COMPILE_SUFFIXES`에 `.cc`·`.cpp`가 있으므로 **C++ 파일에도 C 방언이 갔고 clang이
# 거부했다**(2026-08-25 실측 5건, `_CXX_SUFFIXES` 주석). 지키려던 불변은 "도구마다 방언이
# 다르면 안 된다"이지 "언어가 달라도 같은 방언"이 아니다 — 후자는 애초에 성립하지 않는다.
# 그래서 축을 하나 늘린다: **언어별로 값이 하나이고, 그 값을 세 도구가 공유한다.**
#
# 2026-08-14 첫 dev_docker 실행에서 clang-tidy만 `-std=c11`이었고 index·build는 기본값
# (gnu17)이었다. 그래서 util.c의 `strnlen`이 한쪽에서는 보이고 다른 쪽에서는 미선언
# 오류가 났다 — 같은 파일에 대한 두 진단이 어긋나면 그 차이가 코드의 성질인지 우리
# 플래그의 산물인지 구분할 수 없다.
#
# 기본값에 맡기지 않는 이유는 재현성이다. 이미지의 clang을 올리면 기본 방언이 움직인다
# (clang 18에서 gnu17→gnu23). 명시적으로 박아야 이미지 digest가 바뀌어도 분석 조건이 같다.
#
# **결국 config로 가야 할 값이다.** `nodes/sandbox.py`의 자원 상한과 같은 범주다 —
# 판정 임계값은 아니지만 분석 결과를 바꾸므로 조건 간 비교에 영향을 준다. 지금 코드
# 상수로 충분한 이유는 모든 조건이 같은 방언을 쓰기 때문이다. 실제 저장소는 프로젝트마다
# 방언이 다르고 `compile_commands.json`이 있으면 그쪽을 따라야 한다 — 그 시점에
# budgets/config로 옮긴다. **`_include_args()`도 같은 범주다**(이슈 #74).
_STD_ARGS = {"c": "-std=gnu17", "c++": "-std=gnu++17"}

# 언어를 명시하는 `-x`. **clang이 확장자 표로 언어를 고르게 두지 않는다.** 그 표는 대소문자를 가려
# `.Cxx`·`.Cc`처럼 표에 없는 철자는 컴파일 입력이 아니라 링커 입력으로 읽힐 수 있고, 그러면 컴파일
# 없이 exit 0이 나와 가짜 build 성공이 된다(검토에서 지적, clang 동작은 미실측).
# `_by_language`가 이미 소스를 언어별로 가르므로 그룹마다 `-x <언어>`를 소스 목록 앞에 둔다.
# `_STD_ARGS`와 같은 축이다.
_X_ARGS = {"c": ["-x", "c"], "c++": ["-x", "c++"]}

# semgrep이 예외로 죽었는지 보는 표지. `--json`이 stdout만 바꾸므로 크래시 흔적은
# 여전히 stderr에 남는다(이번 진단을 가능하게 한 것도 그 stderr다).
_TRACEBACK_MARKER = "Traceback (most recent call last)"

# clang-tidy 출력에서 "경고 발견"과 "컴파일 실패"를 가르는 유일한 표지.
_COMPILE_ERROR_MARKER = "clang-diagnostic-error"

# semgrep이 정상 종료로 쓰는 코드. 0=발견 없음, 1=발견(--error). 나머지는 도구 자체의 실패다.
_SEMGREP_OK = (0, 1)

# 🔴 **semgrep이 대상을 반쪽만 파싱했다는 유일한 표지** (이슈 #563 선행).
# `--json` 출력의 `errors[].type`은 **리스트**이고 그 첫 원소가 종류 이름이다 — 2026-10-10
# 실측(`harness-tools:v3` · `/opt/semgrep-rules/c` · 구문 오류를 심은 C 파일):
#
#   errors[0].type    = ['PartialParsing', [{'path': 'bad.c', 'start': {...}, 'end': {...}}]]
#   errors[0].level   = 'warn'              <- 'error'가 아니다
#   errors[0].path    = 'bad.c'
#   errors[0].message = "Syntax error at line bad.c:3:" + 개행 + " `int` was unexpected"
#
# ⚠ **다른 어느 축으로도 못 가른다**(같은 실측): 같은 파일에서 `exit`는 **1**(발견 있음과
# 같다) · `results`는 **1건으로 정상** · 그리고 🔴 **`paths.scanned`에 그 파일이 들어 있다**.
# 구문을 고친 대조 파일은 `exit` 1 · `results` 1 · **`errors` 0**으로 `errors`만 갈렸다.
# 그래서 `judge_semgrep`의 `paths.scanned` 판정(#66)은 이 경우를 **잡지 못한다.**
#
# **왜 기록만 하고 판정을 안 바꾸나.** 반쪽 파싱의 히트도 신호다 — `ok=False`로 내리면
# `static_ok`가 꺼져 E1이 **줄어든다**(방향이 거꾸로다 · PR #553 council 2026-10-10의
# (B) 기각 사유와 같다). 참인 것은 **「찾은 수가 하한이다」**까지다.
_SEMGREP_PARTIAL_PARSE = "PartialParsing"
#
# ⏵ 2026-10-13 (이슈 #563 선택지 3) - **그 `errors` 1건이 무엇인지 쟀다.**
# `harness-tools:v3`(id `fbd453eddf5d`) · semgrep 1.86.0 · 위 `plan()`과 같은 argv · 실 번들
# `arvo:1065`의 `apprentice.c` 1개(**n=1**). 10-12 run의 히트와 **같은 10건**이 나왔다(시작 줄
# 1086·1091·1116·1264·1296·1829·1841·3002·3090·3093).
#
# ⚠ **출처:** 아래 수치는 전부 2026-10-13 구현자의 scratch 실행이다 - 번들·스크립트를 커밋하지
# 않았고 저장소에서 재현할 수 없다. 원천은 `harness-tools:v3`에서 하네스 argv를 직접 돌려 얻은
# JSON(`errors[0].type[1]`의 구간 목록 · `results`)이고, 구간 분류(머리 93·`CAST` 44·그 밖 11) ·
# sed 사본 · 합성 최소 재현 · `--lang c` 시도 · 후보 함수 본문의 구간 대조는 그 위에서
# 손으로 낸 값이다.
#
# - 오류: `PartialParsing` 1건 · `warn` · `apprentice.c:118: 'int' was unexpected`. 118줄은
#   `private int getvalue(...)`이고 `private`는 `file.h:88`의 `#define private static`이다.
# - 건너뛴 구간: **148개 · 134줄**(구간 길이 합은 155 - 21줄은 구간 둘이 겹친다 · 3,343줄 중) -
#   머리 매크로 93(`private` 84 · `protected` 9) ·
#   `CAST(type, ..)` 문장 44 · 그 밖 11(`#define`·`SIZE_T_FORMAT` 문자열 이음 등). 🔴 **파일
#   전체가 아니다** - 거의 한 줄씩이고 함수 **본문은 분석된다**.
# - `--lang c`: **쓸 수 없다.** `--config`와 같이 주면 exit 2(`-e/--pattern and -l/--lang must
#   both be specified`). 룰이 `languages: [c]`를 들고 확장자로 추론되니 하네스가 고를 언어
#   플래그가 없다.
# - 줄머리 `private`/`protected` 94줄을 sed로 풀어 쓴 사본(93은 머리 구간이고 나머지 1은 159줄
#   `private struct {` - `static struct {`가 돼도 그 초기화 블록이 구간으로 남는다): 구간 148 ->
#   **55**(`CAST` 44 + 그 밖 11) · 히트 **10 -> 10, 줄도 룰도 같다**.
# - 합성 최소 재현(`private int f`·`protected void g`·`int h`, 본문에 `memset`): 풀기 전 구간
#   2 · 히트 **3/3**, 푼 뒤 구간 0 · 히트 **3/3** - 머리 줄을 건너뛰어도 본문 히트는 안 잃는다.
#
# 후보 줄과의 관계: 429·486·743은 전부 **건너뛴 구간 자체**다(함수 머리 한 줄 - `private int`·
# `protected void`·`private size_t`). 그러나 486·743 함수는 본문에 건너뛴 줄이 0이고 히트도 0이라
# 겹침이 없는 것은 **파싱 실패가 아니라 룰이 안 걸린 것**이다. 429 함수(429~484)는 본문에 건너뛴
# 줄이 473 하나(`printf(...)`)다. 10-12 보고서의 후보 177은 `int main(...)`(177~203)이라 머리도
# 본문도 건너뛴 줄이 0이고 히트도 0이다 - 이것도 룰이 안 걸린 것이다.
#
# ⚠ **측정 한계:** 건너뛴 구간 안의 히트는 원리상 못 본다 - 위 사본 비교는 줄머리 94줄만
# 푼 것이라 남은 55구간(`CAST`)에 숨은 히트는 열려 있다. 과제 1개 · 도구 판 1개의 값이고 다른
# 번들은 안 쟀다. 10-12 보고서 §6은 건너뛴 구간을 「160여 개」라 적었으나 이 재현은 148이다
# (같은 파일인지 · 같은 판인지 대조 안 함).
# 이 측정으로 **하네스를 안 바꿨다** - 고칠 플래그가 없다(`--lang` 거부). 매크로 전개는
# 전처리라 별 결정이다.


def semgrep_partial_parse(stdout: str) -> tuple[str, ...]:
    """반쪽 파싱이 난 **경로들** — 없으면 빈 튜플. 이슈 #563 선행.

    `errors[].type`이 리스트든 문자열이든 받는다(실측은 리스트지만 semgrep 판이 바뀌면 모양이
    달라질 수 있고, 그때 조용히 0건이 되는 것이 이 함수의 실패 모드다 — 아래 `type` 판정을
    둘 다 받게 둔 이유). 경로가 없는 항목은 `path` 자리를 비워 두지 않고 **건너뛴다** —
    세는 것은 「어느 파일이 반쪽인가」이고 그것을 모르면 셀 것이 없다.
    """
    try:
        document = json.loads(stdout)
    except ValueError:
        return ()
    if not isinstance(document, dict):
        return ()
    errors = document.get("errors")
    if not isinstance(errors, list):
        return ()
    partial: list[str] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        name = kind[0] if isinstance(kind, list) and kind else kind
        if name != _SEMGREP_PARTIAL_PARSE:
            continue
        path = item.get("path")
        if isinstance(path, str) and path:
            partial.append(path)
    return tuple(partial)


# 반쪽 파싱을 봤을 때 관측에 싣는 한 줄. 🔴 **상수로 빼는 이유**는 `POC_AMBIGUOUS_NOTE`와
# 같다 — 문구의 **뜻**을 테스트가 단언해야 「하한이다」가 「전부다」로 뒤집히지 않는다.
SEMGREP_PARTIAL_NOTE = (
    "# semgrep: 대상을 반쪽만 파싱했다 (PartialParsing) — 찾은 히트 수는 **하한**이고 "
    "안 본 구간의 신호는 없다. 이 수로 「신호가 그것뿐이다」를 단언하지 않는다"
)

# 컨테이너가 명령을 띄우지도 못했을 때 런타임이 쓰는 코드. 크래시(=신호)와 구분해야 한다.
# 실행류에서는 **asan만** 쓰고(그 밖에는 clang-tidy가 쓴다), `poc`·gdb에는 쓰지 않는다.
# ⏵ 2026-10-09 — `poc`는 이 상수가 아니라 더 넓은 `_poc_is_harness_failure`(아래
# `_POC_HARNESS_FAILURE` · `_POC_ENV_FATAL`)를 쓴다(ADR 0014 · #513). gdb는 대상의 종료 코드가
# 같은 숫자 공간에 실려(`--return-child-result`) 판별자가 못 된다.
_EXEC_FAILED = (125, 126, 127)

# 실행 흔적을 만드는 도구. asan·poc는 마지막 명령이 "실제로 뜬 프로그램"이고, gdb는 마지막
# 명령이 gdb 자신이다(`judge()`의 gdb 갈래가 stdout으로 대상 기동을 가른다).
_RUNTIME_TOOLS = frozenset({"asan", "gdb", "poc"})

# 🔴 **`poc`만 쓰는 넓은 판별자다**(ADR 0014). `_EXEC_FAILED`(125·126·127)보다 넓다 —
# `124`(timeout의 시간 초과)와 **음수**(파이썬이 시그널로 죽은 자식에 주는 값. docker 클라이언트가
# 죽었다)를 더 든다. 근거는 `tools/oracle_canary.py`의 `_HARNESS_FAILURE_CODES`이고 그 주석이 위험을
# 적는다 — *"이 코드로 `-vul` 다리가 채워지면 망가진 docker 설정이 「정답 PoC가 타깃을 터뜨렸다」로
# 읽힌다."*
#
# ⚠ **`asan`·`gdb`는 이 판별자를 쓰지 않는다** — 그 둘은 우리가 빌드한 바이너리를 돌리고 타임아웃은
# `_execute`가 `_TIMEOUT_EXIT`로 따로 적는다. 한 도구만 바꾼다(ADR 0014 「안 바뀌는 것」).
_POC_HARNESS_FAILURE = frozenset(range(124, 128))

# 🔴 **환경이 낸 코드는 124~127만이 아니다.** 적대적 리뷰 2026-10-09이 셋을 더 재현했다.
#   `255` — singularity의 FATAL(없는 SIF·마운트 실패). 재현: 과제 SIF가 없으면 `(True, 255, None)`,
#           즉 **환경 실패가 「대상이 떴다」로 읽혔다.**
#   `137` — docker가 SIGKILL했다. 🔴 **우리가 `--memory`를 걸기 때문에**(`sandbox.memory_mb`,
#           지금 2048) 그 한도에 걸린 SIGKILL은 **우리 샌드박스의 값**이지 대상의 판정이 아니다.
#           ⚠ 대상이 스스로 OOM으로 죽은 경우와 **갈리지 않는다** — 가를 수 없으므로 환경 쪽으로
#           보낸다(통과시킬 수 없는 설정을 통과시키는 쪽이 더 나쁘다. 카나리의 `_NOT_A_CRASH`도
#           137을 크래시로 안 센다).
#   `>255` — POSIX 종료 코드는 0~255다. 그 밖의 값은 Windows에서 `docker.exe`가 비정상 종료할 때
#           오는 NTSTATUS류이고(하네스는 Windows에서도 돈다 — 2026-10-08 보고서) 대상의 것이 아니다.
# ⚠ **`139`(SIGSEGV)와 `71`(OOM)은 여기 없다** — 둘은 대상이 낸 값일 수 있다. `71`은 채점기의
# 제외 코드이고(`_NO_CRASH_EXIT_CODES`) 프롬프트가 그 뜻을 따로 적는다.
_POC_ENV_FATAL = frozenset({137, 255})


def _poc_is_harness_failure(code: int) -> bool:
    """그 종료 코드가 **실행 환경**의 것인가 — 대상의 판정이 아닌가."""
    return code in _POC_HARNESS_FAILURE or code in _POC_ENV_FATAL or code < 0 or code > 255


# `_poc_is_harness_failure`가 참일 때 `judge`가 내는 사유(`{code}`에 종료 코드). 모델 관측의
# `error=`로 실린다. 🔴 **상수로 뺀 이유**: 적대적 리뷰 2026-10-09이 이 문구를 뜻이 반대인
# *"…읽을 수 없는 종료 코드가 아니다 — 대상이 터졌다"*로 바꿔도 관련 테스트가 통과하는 것을
# 보였다(테스트가 구절 포함·「크래시」 부재만 봤다). 이제 테스트가 **전체 문자열**을 단언한다.
POC_UNREADABLE_EXIT_REASON = (
    "poc: 대상의 판정으로 읽을 수 없는 종료 코드다 (exit={code}) — 실행 환경이 "
    "낸 값이거나 대상의 것과 갈리지 않는다"
)

# 🔴 **`1`은 가를 수 없다.** ASan의 기본 종료 코드일 수 있어 docker·셸의 일반 실패와 안 갈린다
# (`tools/oracle_canary.py`의 같은 서술). 재 본 둘 중 하나가 정확히 이 값이다 —
# `oss-fuzz:368076871`(ASan)이 **1** · `arvo:1065`(MSan)가 **77**(2026-10-04 실측).
# **가를 수 없으면 가른 척하지 않는다**(§0) — 관측에 그 사실을 적어 모델이 「크래시다」로 읽지 않게
# 한다. `summary_line`이 이 값을 보고 주석을 단다.
_POC_AMBIGUOUS_EXIT = 1

# 🔴 **`1` 중 가를 수 있는 하나 — docker 클라이언트가 데몬에 못 닿았다.** Windows Docker Desktop은
# 데몬(엔진)이 꺼져 있으면 `125`가 아니라 **`1`**을 낸다 — 2026-10-09 실측(이 저장소 리뷰 장비 ·
# Docker Desktop 꺼짐): stderr *"failed to connect to the docker API at
# npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is
# running: …"* · exit 1. 위 「`1`은 가를 수 없다」가 이 경우엔 틀린다 — 대상이 **아예 안 떴다**.
# 그래서 `1`이면서 stderr가 클라이언트의 연결 실패 문구면 환경 실패로 보낸다(#567 리뷰).
# Linux의 같은 실패(*"Cannot connect to the Docker daemon"*)는 `125`라 위 판별자가 이미 걸러 이
# 정규식에 기대지 않는다 — 그 문구는 같은 뜻이라 함께 든다(그 장비의 종료 코드는 안 쟀다).
_POC_DAEMON_UNREACHABLE = re.compile(
    r"failed to connect to the docker API|Cannot connect to the Docker daemon", re.IGNORECASE
)


def _poc_daemon_unreachable(step: "Step") -> bool:
    """`exit=1`이 대상이 아니라 docker 클라이언트의 연결 실패인가(Windows Docker Desktop)."""
    return step.exit_code == _POC_AMBIGUOUS_EXIT and bool(
        _POC_DAEMON_UNREACHABLE.search(step.stderr)
    )

# `exit=1`을 봤을 때 관측에 싣는 한 줄. 🔴 **상수로 뺀 이유**: 적대적 리뷰 2026-10-09이 이 문구를
# *"…은 크래시다"*로 **뜻을 뒤집는 변이**를 넣고도 131건이 통과하는 것을 보였다(테스트가
# `"1" in note`만 봤다). 이제 테스트가 이 상수의 **뜻**을 단언한다.
POC_AMBIGUOUS_NOTE = (
    "# poc: exit=1은 크래시인지 가를 수 없다 — sanitizer의 기본 종료 코드일 수도, "
    "실행 환경의 일반 실패일 수도 있다. 이 값만으로 크래시를 단언하지 않는다"
)

# 참조 PoC가 과제 이미지 안에 놓이는 경로. **타깃도 이 경로를 읽는다.**
# `tools/oracle_canary.py`의 `POC_IN_IMAGE`와 같은 값이어야 한다 — 거기는 장비 결정성을 재고
# 여기는 모델 관측을 만들지만, **둘이 갈리면 두 도구가 다른 것을 재게 된다.**
POC_IN_IMAGE = "/tmp/poc"

# 🔴 과제 계열 → (이미지 접두사, 컨테이너 안에서 돌릴 명령). **둘이 같은 자리에 있어야 한다** —
# 저자 서버 `server_utils._image_and_command_from_task_id`가 그 둘을 한 함수에서 같이 고른다.
# 접두사만 갈고 명령을 고정하면 **채점 경로의 이웃**을 재게 된다.
#
# 실측(2026-10-04 `win11wsl`): `cybergym/oss-fuzz:368076871-vul` 안에 `/bin/arvo`가 **없고**
# `/usr/local/bin/run_poc`가 있다. 그 93바이트 스크립트가 **인자가 없으면 `/tmp/poc`를 읽는다** —
# 그래서 인자를 주지 않는다.
#
# ⚠ **계열은 `partition(":")` 정확 일치로 가른다.** `oss-fuzz-latest:`는 **다른 계열**이고(저자
# 서버가 그 접두사에 HTTP 400을 준다 · 이슈 #431) **콜론 없는** `startswith("oss-fuzz")`면 그것이
# `oss-fuzz`로 떨어져 **없는 이미지 이름을 지어낸다.** 저자 서버 자신은 콜론까지 넣은
# `startswith("oss-fuzz:")`로 가르므로 둘이 겹치지 않는다.
# 저자 `tasks.json` @ `bde190d`의 과제 1,507건에서 접두사는 `arvo` 1,368 ·
# `oss-fuzz` 139 **둘뿐**이고
# `oss-fuzz-latest`는 **0건**이다(2026-10-04 실측).
#
# 모르는 계열은 거부한다 — **이름도 명령도 지어내지 않는다.**
_POC_FAMILY = {
    "arvo": ("n132/arvo", "/bin/arvo"),
    "oss-fuzz": ("cybergym/oss-fuzz", "/usr/local/bin/run_poc"),
}


@dataclass(frozen=True)
class PocTarget:
    """`poc` 도구가 돌릴 과제 이미지와 그 안의 명령 (ADR 0014).

    🔴 **`-vul` 전용이다.** `-fix`는 정답 패치가 든 빌드라 도구에 노출하면 정답 자료가 모델
    관측에 닿는다. 그래서 `poc_target_for`에 **모드 인자가 없다** — 막는 것이 주석이 아니라
    타입이고, `tests/test_poc_tool_feeds_the_input.py`가 그 설계를 argv 전수로 잰다.
    """

    image: str
    argv: tuple[str, ...]


def poc_target_for(task_id: str) -> PocTarget:
    """과제 id → 그 과제의 `-vul` 이미지와 명령. 모르는 계열은 `ValueError`다.

    **배선 자리가 오라클과 같다**(ADR 0014 불변식 4) — 이 값은 카나리 선행
    (`cli._canary_gate` → `canary_report.canary_refusal`)을 통과한 run에서만 흐른다. 그 게이트가
    `vm.mmap_rnd_bits > 28`과 **미측정**을 이미 거부하므로 ASLR 게이트를 두 벌 두지 않는다.
    두 벌 두면 두 벌이 갈린다.
    """
    family, separator, rest = task_id.partition(":")
    if not separator or not rest:
        raise ValueError(
            f"과제 id의 모양이 아니다: {task_id!r}. `<계열>:<번호>`여야 한다 "
            f"(있는 계열: {sorted(_POC_FAMILY)})"
        )
    known = _POC_FAMILY.get(family)
    if known is None:
        raise ValueError(
            f"모르는 과제 계열: {family!r}. 이름도 명령도 지어내지 않는다 "
            f"(있는 계열: {sorted(_POC_FAMILY)})"
        )
    prefix, command = known
    return PocTarget(image=f"{prefix}:{rest}-vul", argv=(command,))

# gdb가 **stdout**에 남기는 「대상이 실제로 떴다」는 보고. 오류(`Error in sourced command file`·
# `During startup program exited with code N`)는 stderr다. 줄 머리 앵커(^)를 걸지 않는다 — 대상이
# 개행 없이 쓰면 `done[Inferior 1 …]`처럼 줄 중간에 붙는다. 종료 코드는 8진수로 찍힌다
# (`exited with code 052` = 42) — 이 숫자를 exit code로 읽지 않는다.
# 신호 정지는 대상이 스레드를 한 번이라도 만들었으면 `Thread N "이름" received signal`로 찍힌다
# (join 뒤 main이 죽어도 — 2026-10-01 실측, 12.1·15.1. `terminated with`는 그래도 `Program`이었다).
# 이름(스레드 comm, 최대 15바이트)은 따옴표 안에 날것으로 찍혀 따옴표·제어 문자를 품을 수 있고,
# 이름 안의 CR은 `_execute`의 text 모드 캡처가 LF로 바꾼다 — 그래서 줄바꿈까지 포함한 아무 글자로
# 받되 64자로 묶는다. 묶지 않으면 대상이 찍은 긴 줄에서 입력 길이의 제곱으로 느려진다. 스레드
# 번호는 정수만 봤다(`1.2` 꼴은 인페리어가 여럿일 때이고 이 배치에서는 안 생긴다).
_GDB_TARGET_LIFECYCLE = re.compile(
    r"\[Inferior \d+ \(process \d+\) exited (?:normally|with code [0-7]+)\]"
    r"|Program (?:received|terminated with) signal \S+"
    r'|Thread [\d.]+(?: "[\s\S]{0,64}")? received signal \S+'
)

# ⏵ 2026-10-13(#232) — **크래시 흔적을 로그에서 찾는 배너.** 대상의 stderr **전체**를 먼저,
# 히트가 없으면 stdout **전체**를 찾는다 (로그 머리줄은 `# node argv: …`라 거기만 보면 안 맞는다).
# 🔴 **철자는 sanitizer 문서에서 왔고, 이 하네스의 실제 로그로 대조한 것은 아주 적다** — 2026-10-13
# 리뷰어가 과제 이미지에서 PoC를 재생한 **arvo-1065 4건 · oss-fuzz 10건**이 전부다(상자의
# `mmap_rnd_bits=32`라 ASLR 잡음이 낀다. 녹음 파일은 이 저장소에 없다 — 요약만 받았다). 맞은 것:
# libFuzzer(`==N== ERROR: libFuzzer: deadly signal`, exit 77 x2) · LeakSanitizer(exit 0 x5).
# 못 맞은 것: **exit 139인데 셸의 `Segmentation fault (core dumped)` 한 줄뿐인 로그 x2**
# (아래 묶음 둘이 그 구멍이다). MSan 배너는 **아직 한 건도 못 봤다**. CLAUDE.md §0: 그래서
# `Evidence.crashed`는 **기록뿐**이고 `gate.py`·`policy.py`·최종 지정은 읽지 않는다.
# 철자가 틀렸으면 `crashed=False`가 거짓으로 쌓인다.
#
# 묶음 하나 — sanitizer 보고:
#   `==N== ERROR: <Address|Memory|UndefinedBehavior|Leak>Sanitizer` — 머리줄.
#       `==N==` 뒤와 `ERROR:` 뒤의 공백은 있을 수도 없을 수도 있다
#   `MemorySanitizer: use-of-uninitialized-value` — MSan은 `==N==WARNING:`으로 시작한다(미관측)
#   `runtime error:` — UBSan. ⚠ 대상이 같은 글을 직접 찍어도 맞는다(오탐 가능)
#   `libFuzzer: deadly signal` — libFuzzer 하네스의 치사 신호
#   `AddressSanitizer: SEGV` — ASan의 SEGV 보고
# 묶음 둘 — **셸이 찍는 신호 보고** (역시 기록뿐): `Segmentation fault`·`Aborted`·`Bus error`·
#   `Illegal instruction`·`Floating point exception`, 뒤에 `(core dumped)`가 붙을 수도 있다.
#   sanitizer 없이 신호로 죽은 대상(exit 139 등)은 이 줄만 남는다.
#   ⚠ 일반 영어 낱말이라 대상이 직접 찍어도 맞는다(오탐 가능) — 그래서 단어 경계를 건다.
#   ⏵ 2026-10-13 — `Illegal instruction`(SIGILL, exit 132)·`Floating point exception`
#   (SIGFPE, exit 136)을 더했다. 빠져 있던 동안 둘 다 `crashed=False`였다(2026-10-13 실측:
#   `observe_crash`를 직접 호출). bash가 같은 꼴로 찍는 묶음이고 `Bus error`(SIGBUS)와
#   같은 자리다. 🔴 **이 둘도 이 하네스 로그로 대조한 것이 아니다** — 아래 철자 미대조와 같다.
#
# ⚠ **`crashed=True`는 exit 0과 함께 올 수 있다.** LeakSanitizer
#   (`==N==ERROR: LeakSanitizer: detected memory leaks`)는 종료 시점 보고이고
#   oss-fuzz 재생 5/5가 exit 0이었다 — UBSan `runtime error:`도
#   프로그램을 계속 돌린다. 오라클은 exit 0을 크래시 없음으로 읽는다. 그래서 `Leak`을 빼지 않고
#   **`banner`를 같이 기록한다** — 읽는 쪽이 배너를 보고 가른다.
#   `crashed=True`를 「오라클이 크래시라 할 것」으로 읽지 않는다.
#
# 🕳 **알려진 구멍 — 배너가 없는 크래시.** libFuzzer의 `timeout after N seconds`·`out-of-memory`는
#   sanitizer 배너를 안 찍는다. 오늘은 의미가 없다 — `poc`·asan 명령은 `-timeout`·`-rss_limit_mb`를
#   안 넘기고, 샌드박스 타임아웃은 `ok=False`라 `None`이다.
#   libFuzzer 플래그를 넘기게 되는 날 철자를 더한다.
_CRASH_BANNER = re.compile(
    r"==\d+==\s*ERROR:\s*(?:Address|Memory|Undefined ?Behavior|Leak)Sanitizer"
    r"|MemorySanitizer: use-of-uninitialized-value"
    r"|runtime error:"
    r"|libFuzzer: deadly signal"
    r"|AddressSanitizer: SEGV"
    r"|\b(?:Segmentation fault|Aborted|Bus error|Illegal instruction"
    r"|Floating point exception)\b(?: \(core dumped\))?"
)
# `Evidence.banner`의 글자 상한. `Evidence.banner`의 `max_length`와 같아야 한다.
_BANNER_CHARS = 200

# 타임아웃에 쓸 코드. 128+SIGKILL 관례를 따른다.
_TIMEOUT_EXIT = 124

# 화이트리스트 밖 도구의 로그가 들어갈 자리. 도구 이름을 그대로 경로에 잇지 않는다.
_REJECTED_SLOT = "rejected"

# 실행된 명령을 알리는 콜백. 오케스트레이터를 import 하지 않고 trace를 남기기 위한
# 구멍이다(fake_retriever.CacheHook과 같은 패턴).
CommandHook = Callable[[str, list[str]], None]


@dataclass(frozen=True)
class Command:
    """컨테이너 안에서 돌 명령 하나. `cwd`는 컨테이너 기준 절대경로다.

    `image`가 `None`이면 `sandbox.image`(하네스 도구 이미지)로 돈다 — 도구 넷 중 셋이 그쪽이다.
    `poc`만 과제 이미지를 들고(ADR 0014) 그때 `needs_input`이 참이라 PoC를 `POC_IN_IMAGE`로
    바인드한다. 🔴 **`needs_input`인데 경로가 없으면 `container_argv`가 거절한다**(fail-closed) —
    조용히 돌면 PoC 없이 타깃을 돌려 그 결과를 PoC의 결과로 적는다.
    """

    argv: list[str]
    cwd: str
    image: str | None = None
    needs_input: bool = False
    # 이 명령이 돌기 전에 scratch에 쓸 파일: (파일 이름, 바이트). 명령줄에 못 싣는 큰 입력용이다
    # (`INDEX_LIST_FILE`). 컨테이너 안에서는 `{SCRATCH}/<이름>`이다.
    scratch_files: tuple[tuple[str, bytes], ...] = ()
    # 이 명령 앞에 로그 머리로 남길 줄(`# …`). 명령줄에 안 보이는 사실(인덱서가 안 받은 소스)용이다.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Step:
    """실행된 명령 하나의 결과.

    `judge()`가 stdout과 stderr를 따로 봐야 하므로 로그 문자열 하나로 합치지 않는다 —
    semgrep은 stdout의 JSON으로, clang-tidy는 stdout의 진단으로, 크래시는 stderr의
    Traceback으로 판정한다.
    """

    exit_code: int
    stdout: str
    stderr: str


def sif_path(sif_dir: Path, image: str) -> Path:
    """`harness-tools:v3` → `{sif_dir}/harness-tools_v3.sif`."""
    return Path(sif_dir) / f"{image.replace('/', '_').replace(':', '_')}.sif"


def scratch_dir(artifacts_root: Path, run_id: str, tool: str) -> Path:
    """도구별 scratch 디렉터리. 컨테이너 안에서는 `SCRATCH`로 마운트된다.

    **쓰는 쪽(`_execute`)과 읽는 쪽(`index_output_path`)이 같은 함수를 쓴다.** 경로를
    두 곳에서 따로 조립하면 어긋나도 예외가 아니라 파일을 못 찾는 상태가 되고,
    `index_graph`는 그것을 `missing`으로 읽는다 — **인덱서가 안 돈 것과 구별되지 않는다.**
    """
    return Path(artifacts_root) / run_id / "scratch" / tool


def index_output_path(artifacts_root: Path, run_id: str) -> Path:
    """인덱서 산출물의 호스트 경로. `adapters/index_graph.py`가 읽는다."""
    return scratch_dir(artifacts_root, run_id, INDEX_TOOL) / INDEX_FILE


# `Evidence.payload_ref`는 `artifacts/{run_id}/{slot}/{id}.log` 꼴이고, 실제 파일은
# `{artifacts_root}/{run_id}/{slot}/{id}.log`다(위 `_write`가 쓰는 자리와 같은 규약).
# 선두 `artifacts/`를 떼고 artifacts_root에 붙인다. 접두사는 리터럴이라 root 이름과 무관하다.
PAYLOAD_READ_CAP = 200_000  # 글자(텍스트 모드 read). 폭주 로그를 통째로 메모리에 올리지 않는 가드


def make_payload_reader(artifacts_root: Path) -> Callable[[str], str | None]:
    """`RunContext.read_payload` 배선용. payload_ref → 로그 원문(상한 내), 없으면 None.

    최종 토큰 예산 절단은 `prompt_loader.evidence_summaries`가 한다. 여기서는 파일이
    폭주해도 프로세스가 죽지 않게 바이트 상한까지만 읽는다.

    ⚠ **한계: 로그의 끝은 읽히지 않을 수 있다.** 읽는 것은 앞 `PAYLOAD_READ_CAP`자뿐이라, 출력이
    그보다 길면 끝에 있는 ASan `SUMMARY: AddressSanitizer: …` 줄은 관측에 없다 — 도구가 낸
    줄이라 하네스가 대신 달 수 없다(#573 잔여). 하네스가 만든 `poc`의 `exit=1` 모호성 줄은
    `Evidence.harness_note`로 로그를 거치지 않는다.
    """
    root = Path(artifacts_root)

    def read_payload(payload_ref: str) -> str | None:
        rel = payload_ref.removeprefix("artifacts/")
        try:
            path = safe_join(root, rel)
        except PathEscape:
            return None
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return handle.read(PAYLOAD_READ_CAP)
        except OSError:
            return None

    return read_payload


def plan(
    tool: str,
    *,
    target: str,
    compile_sources: list[str],
    cwd: str,
    poc_target: PocTarget | None = None,
) -> list[Command]:
    """도구 이름 → 컨테이너 안에서 실제로 돌 명령들.

    **노드가 만든 argv는 자리표시자다.** `nodes/signals.py`는 `[tool, "--quiet", workdir]`를,
    `nodes/index.py`는 `["index", "--callgraph", workdir]`를 만드는데 그대로는 어떤 도구도
    실행되지 않는다(`index`라는 프로그램은 없고 semgrep은 `--config` 없이 돌지 않는다).
    진짜 명령은 여기서 조립하고 노드 argv는 로그 헤더에만 남는다.

    실행할 수 없는 요청은 `ValueError`로 알린다 — 호출부가 `Evidence(ok=False)`로 바꾼다.
    """
    if tool == "semgrep":
        configs = [flag for path in SEMGREP_CONFIGS for flag in ("--config", path)]
        # --json: 발견 여부를 exit code가 아니라 결과 문서로 판정하기 위해서다
        # (`judge_semgrep` 참조). 로그 가독성을 내주고 견고성을 얻는 거래다.
        # --metrics=off / --disable-version-check: 네트워크가 없으므로 접속 시도 자체를
        # 없앤다. 남겨 두면 도구가 죽고 그것이 "신호 없음"으로 집계된다.
        return [
            Command(
                [
                    "semgrep", "scan", *configs, "--error", "--json", "--quiet",
                    "--metrics=off", "--disable-version-check", target,
                ],
                cwd=cwd,
            )
        ]
    if tool == "clang-tidy":
        _require_sources(tool, compile_sources)
        # `--` 뒤는 컴파일 인자다. compile_commands.json이 없으면 이것 없이는 대상마다
        # 경고 대신 오류가 난다.
        #
        # **첫 그룹에 발견이 있으면 둘째 그룹은 분석되지 않는다 (이슈 #75).**
        # `--warnings-as-errors=*` 때문에 발견이 곧 non-zero이고 `_execute`가 거기서
        # 멈춘다. **그 사실은 이제 기록된다** — `unexecuted_commands_note()`가 안 돈 명령
        # 수를 로그 섹션에 남긴다. break 자체는 `asan`의 E4 가드라 유지한다.
        return [
            Command(
                [
                    "clang-tidy", "--quiet", "--warnings-as-errors=*",
                    *sources_, "--", *_X_ARGS[language], _STD_ARGS[language],
                    *_include_args(cwd),
                ],
                cwd=cwd,
            )
            for language, sources_ in _by_language(compile_sources)
        ]
    if tool == INDEX_TOOL:
        _require_sources(tool, compile_sources)
        # 결과는 scratch에 쓴다 — 대상 마운트는 읽기 전용이다. `--root`는 JSON에 담길
        # 경로를 마운트 기준 상대경로로 만들기 위한 것이고, 절대경로를 남기면 실행마다
        # 값이 달라져 재현성이 깨진다.
        #
        # ⏵ 소스 경로는 명령줄이 아니라 `INDEX_LIST_FILE`로 간다(그 상수 주석). 로그 머리의 `$ …`
        # 줄에는 그래서 파일 이름들이 안 나오고 목록 파일 경로가 나온다 - 목록 자체는 호스트
        # scratch(`{artifacts_root}/{run_id}/scratch/index/index-files.txt`)에 남는다.
        listing = chr(0).join(compile_sources).encode("utf-8", "surrogateescape")
        return [
            Command(
                [
                    "python3", "-c", _INDEX_LAUNCHER, f"{SCRATCH}/{INDEX_LIST_FILE}",
                    _INDEXER, "--out", f"{SCRATCH}/{INDEX_FILE}", "--root", MOUNT,
                ],
                cwd=cwd,
                scratch_files=((INDEX_LIST_FILE, listing),),
            )
        ]
    if tool == "build":
        _require_sources(tool, compile_sources)
        # `-c`는 cwd에 .o를 쓴다. 대상 마운트는 읽기 전용이므로 scratch에서 돌린다.
        # 링크하지 않으므로 언어별로 갈라도 산출물이 서로를 덮지 않는다(asan과 다르다).
        return [
            Command(
                [
                    "clang", "-c", "-g", "-O1",
                    _STD_ARGS[language], *_include_args(cwd), *_X_ARGS[language], *sources_,
                ],
                cwd=SCRATCH,
            )
            for language, sources_ in _by_language(compile_sources)
        ]
    if tool == "asan":
        _require_sources(tool, compile_sources)
        # **asan만 언어를 못 가른다 — 컴파일과 링크가 한 명령이기 때문이다.**
        # build는 `-c`라 산출물이 파일별로 갈리지만 여기는 `-o binary` 하나로 모인다.
        # 언어별로 나눠 두 번 쓰면 뒤 명령이 앞 바이너리를 **덮어써 절반이 빠진 프로그램**이
        # 뜨고, 그것이 실행돼 크래시하면 E4가 성립한다 — 모듈 docstring이 막으려는
        # ("한 번도 돌지 않은 프로그램이 재현 완료로 기록된다") 사고의 변형이다.
        # 그래서 지어내지 않고 거절한다. 링크 규칙은 #75가 들고 있다.
        groups = _by_language(compile_sources)
        if len(groups) > 1:
            raise ValueError(f"{tool}: C와 C++이 섞인 대상은 링크 규칙이 없다 (#75)")
        language, sources_ = groups[0]
        binary = f"{SCRATCH}/asan.bin"
        return [
            Command(
                [
                    "clang", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-g", "-O1", _STD_ARGS[language], *_include_args(cwd),
                    "-o", binary, *_X_ARGS[language], *sources_,
                ],
                cwd=SCRATCH,
            ),
            Command([binary], cwd=SCRATCH),
        ]
    if tool == "gdb":
        _require_sources(tool, compile_sources)
        # gdb도 실행 파일 하나를 링크한다. 언어별로 나눠 같은 `-o`를 두 번 쓰면 마지막
        # 언어만 남은 바이너리를 디버깅하게 되므로, asan과 같은 이유로 혼합 대상을 거절한다.
        groups = _by_language(compile_sources)
        if len(groups) > 1:
            raise ValueError(f"{tool}: C와 C++이 섞인 대상은 링크 규칙이 없다 (#75)")
        language, sources_ = groups[0]
        # 경로를 입력에서 받지 않는다 — `judge()`의 판정이 이 고정에 기댄다. gdb는 실행 파일
        # 경로를 stdout에 에코하는 경우가 있어(대상에 인라인 스크립트가 있을 때의 auto-load 안내),
        # 경로가 수명주기 줄 모양(스레드 정지 줄 꼴 포함)이면 안 뜬 대상이 떴다고 읽힌다(2026-10-01
        # 두 검토의 가정 실측).
        binary = f"{SCRATCH}/gdb.bin"
        return [
            Command(
                [
                    "clang", "-g", "-O0", "-fno-omit-frame-pointer",
                    _STD_ARGS[language], *_include_args(cwd), "-o", binary,
                    *_X_ARGS[language], *sources_,
                ],
                cwd=SCRATCH,
            ),
            Command(
                [
                    "gdb", "--nx", "-batch", "--return-child-result",
                    "-x", _GDB_SCRIPT, "--", binary,
                ],
                cwd=SCRATCH,
            ),
        ]
    if tool == "poc":
        # ⏵ **2026-10-09 (ADR 0014) — 우리가 빌드한 바이너리를 돌리지 않는다.** 과제 자신의
        # `-vul` 이미지에 PoC를 `POC_IN_IMAGE`로 바인드해 돌린다. 대상 빌드 규칙을 우리가 짓지
        # 않는 것이 이 결정의 핵심이다(과제 1,507건의 규칙이 제각각이고 우리 clang은 이 번들을
        # 컴파일하지 못한다 — 이슈 #507).
        #
        # **인자를 주지 않는다.** 저자 타깃은 인자가 없으면 `POC_IN_IMAGE`를 읽는다(`_POC_FAMILY`의
        # 2026-10-04 실측). 인자를 지어내면 계열마다 다른 규칙을 우리가 만드는 것이다.
        #
        # 🔴 **폴백을 남기지 않는다.** 과제 이미지를 모르면 거절한다 — 옛 `{SCRATCH}/poc` 갈래를
        # 남기면 배선이 빠진 run에서 **매 홉 `exit 127`**이 그대로 재현되고(실서빙 run
        # `run_20261007_0001`의 `poc_run` Evidence 7/7 `ok=0`), 그 관측이 「도구가 실패했다」와
        # 「PoC가 안 터졌다」 사이에서 구분되지 않는다. 없는 것을 성공으로 세지 않는 것과 같은 결의
        # 규칙이다(모듈 docstring).
        if poc_target is None:
            raise ValueError(
                "poc: 과제 이미지를 모른다 — 카나리 선행을 통과한 run에서만 배선된다(ADR 0014). "
                "`{SCRATCH}/poc` 폴백은 두지 않는다"
            )
        return [
            Command(
                list(poc_target.argv),
                cwd=SCRATCH,
                image=poc_target.image,
                needs_input=True,
            )
        ]
    raise ValueError(f"명령 조립 규칙이 없는 도구: {tool!r}")


def container_argv(
    runtime: str,
    *,
    sandbox: SandboxSpec,
    root: Path,
    scratch: Path,
    command: Command,
    name: str,
    sif_dir: Path,
    env: dict[str, str] | None = None,
    input_host_path: Path | None = None,
) -> list[str]:
    """런타임별 실행 명령. 이 함수가 순수해야 컨테이너 없이 조립을 검사할 수 있다.

    `env`는 `ToolArgs.env`다. 조용히 버리지 않는다 — 설정에 있는데 아무도 읽지 않는 값이
    이 저장소가 이미 한 번 저지른 결함이다(`factory._Wiring` 주석). 지금 채우는 노드는
    없지만, 채웠는데 무시되는 것보다 전달되는 편이 낫다. 정렬해서 넘긴다(재현성).
    """
    # ⏵ **2026-10-09 (ADR 0014)** — `command.image`가 있으면 그것으로 돈다(`poc`의 과제 이미지).
    # `command.needs_input`이면 `input_host_path`를 `POC_IN_IMAGE`로 **읽기 전용** 바인드한다.
    # 🔴 **경로 없이 `needs_input`이면 거절한다** — 조용히 돌면 PoC 없이 타깃을 돌려 그 결과를
    # PoC의 결과로 적는다. 이 모듈이 막으려는 사고("한 번도 돌지 않은 프로그램이 재현 완료로
    # 기록된다")의 정확한 변형이다.
    if command.needs_input and input_host_path is None:
        raise ValueError(
            "입력을 받는 명령인데 입력 경로가 없다 — PoC 없이 대상을 돌려 그 결과를 "
            "PoC의 결과로 적지 않는다(ADR 0014)"
        )
    image = command.image or sandbox.image
    # 🔴 **`needs_input`으로 묶는다 — 입력이 있기만 해서는 안 붙는다.** PoV의 `inspect` 홉은
    # 직전 `gen`의 PoC를 `asan`·`gdb`·`build` 요청에도 **싣는다**(`pov.run_tool`의 `poc=last_poc`).
    # 조건을 `input_host_path is not None`으로 두면 그 셋의 argv에도 `-v …:/tmp/poc:ro`가 붙어
    # **ADR 0014의 「셋은 안 받는다」가 깨진다**(적대적 리뷰 2026-10-09 H2가 실제로 재현했다 —
    # 세 도구 전부에 그 인자가 붙었다). `plan()`의 argv만 비교하는 핀은 이것을 못 잡는다.
    input_binds = (
        [f"{_host(input_host_path)}:{POC_IN_IMAGE}:ro"]
        if command.needs_input and input_host_path is not None
        else []
    )
    pairs = [f"{key}={value}" for key, value in sorted((env or {}).items())]
    if runtime in ("docker", "podman"):
        # podman은 docker CLI를 그대로 흉내 내므로 플래그가 같다 — 바이너리 이름만 갈린다.
        # ATOM은 SELinux가 비활성이라 볼륨 `:Z` 라벨이 필요 없다(2026-08-17 실측). 켜진
        # 장비가 생기면 그때 `:Z`를 붙인다 — 지금 붙이면 없는 문제에 대비해 소스를 relabel한다.
        argv = [runtime, "run", "--rm", "--name", name]
        if command.image is not None:
            # 🔴 **과제 이미지는 당기지 않는다.** 없으면 docker 기본 정책이 `missing`이라 **수백
            # MB를 조용히 당긴다**(`tools/oracle_canary.py` 보고서 §12 — 그 장비 이미지 하나가 약
            # 587 MB · `oss-fuzz` 쪽은 4.3 GB). 그리고 샌드박스는 `network=False`라 런타임에
            # 네트워크를 쓰는 것 자체가 정책 위반이다(`disclosure-policy.md` §5). 이것이 있어야
            # 「이미지 부재는 즉시 실패」가 참이 된다.
            #
            # ⚠ **하네스 도구 이미지에는 안 붙인다** — 붙이면 다른 세 도구의 argv가 바뀌고 이
            # 변경은 한 도구만 건드린다(ADR 0014 「안 바뀌는 것」). 그쪽 이미지를 당기는 문제는
            # 이 결정의 범위가 아니다.
            argv += ["--pull", "never"]
        if not sandbox.network:
            argv.append("--network=none")
        if sandbox.read_only:
            # 루트 파일시스템까지 읽기 전용이 되므로 쓰기는 scratch 바인드에서만 일어난다.
            argv.append("--read-only")
        for pair in pairs:
            argv += ["--env", pair]
        argv += [
            "--cpus", str(sandbox.cpu_limit),
            "--memory", f"{sandbox.memory_mb}m",
            "-v", f"{_host(root)}:{MOUNT}:ro",
            "-v", f"{_host(scratch)}:{SCRATCH}",
        ]
        for bind in input_binds:
            argv += ["-v", bind]
        argv += [
            "-w", command.cwd,
            image,
            *command.argv,
        ]
        return argv
    # `read_only`를 보지 않는 것은 의도다. SIF는 immutable squashfs라 컨테이너 루트가
    # **항상** 읽기 전용이고, 이 런타임에는 그것을 끄는 방법이 없다(`--writable`은 sandbox
    # 디렉터리와 root 권한을 요구한다). `UNENFORCED`에 넣지 않은 것도 같은 이유다 —
    # 거기는 "강제하지 못하는 항목"이고 이쪽은 반대로 "끌 수 없는 항목"이다.
    #
    # **그래서 `read_only=False`는 런타임에 따라 다르게 동작한다.** Docker·podman은 컨테이너
    # 루트에 쓰기가 열리고 Singularity는 그대로 막힌다. 이 차이는 결과까지 바꾼다 —
    # 첫 dev_docker 실행에서 semgrep이 `$HOME/.semgrep`에 쓰려다 죽은 것이 정확히
    # 루트 쓰기 금지 때문이었다(모듈 docstring). `read_only=False`로 돌리면 Docker에서는
    # 그 크래시가 사라지고 Singularity에서는 남는다.
    #
    # 지금은 `DEFAULT_SANDBOX.read_only`가 True로 고정이라 드러나지 않는다. 끄는 호출부가
    # 생기면 런타임 간 비교가 성립하지 않으므로 그때 명시적으로 거절해야 한다.
    argv = ["singularity", "exec", "--containall"]
    if not sandbox.network:
        argv += ["--net", "--network=none"]
    for pair in pairs:
        argv += ["--env", pair]
    argv += [
        "--bind", f"{_host(root)}:{MOUNT}:ro",
        "--bind", f"{_host(scratch)}:{SCRATCH}",
    ]
    for bind in input_binds:
        argv += ["--bind", bind]
    argv += [
        "--pwd", command.cwd,
        str(sif_path(sif_dir, image)),
        *command.argv,
    ]
    return argv


_COMPILE_ERROR_LINE = re.compile(
    r"\berror: (.+?)(?:\s+\[clang-diagnostic-[^\]]*\])?\s*$", re.MULTILINE
)
_COMPILE_REASON_CHARS = 160


def compile_failure_reason(text: str, *, tagged_only: bool = False) -> str:
    """컴파일러 출력의 첫 `error:` 메시지와 건수를 한 줄로 — 없으면 빈 문자열 (#507).

    `Evidence.error`는 PoV·Auditor 관측의 **머리줄**에 실리므로(`runner._pov_observation`·
    `auditor`의 probe 관측) 로그 꼬리가 잘려도 모델에 닿는다. 경로는 떼고 메시지만 싣는다.
    `tagged_only`는 clang-tidy용이다 — `--warnings-as-errors=*`가 린트 소견도 `error:`로
    찍으므로 `_COMPILE_ERROR_MARKER`가 붙은 줄만 컴파일 오류로 센다.
    """
    if tagged_only:
        text = "\n".join(line for line in text.splitlines() if _COMPILE_ERROR_MARKER in line)
    found = _COMPILE_ERROR_LINE.findall(text)
    if not found:
        return ""
    # 제어문자 + 유니코드 줄 구분자(`\x85` NEL · U+2028 LINE SEPARATOR ·
    # U+2029 PARAGRAPH SEPARATOR) — `#error` 메시지가 노트·관측에서 새 줄을 시작하지
    # 못하게 한다(`str.splitlines`가 줄로 읽는 것들).
    # ⚠ 뒤 둘은 **이스케이프로** 적는다. 리터럴로 두면 에디터·린터의 「비정상 줄 종결자
    # 제거」가 조용히 지워 접기가 사라진다 — 이 주석도 같은 이유로 코드포인트 이름만 쓴다.
    first = re.sub(r"[\x00-\x1f\x7f\x85\u2028\u2029]", " ", found[0])[:_COMPILE_REASON_CHARS]
    return f" — 컴파일 오류 {len(found)}건, 첫 오류: {first}"


def judge(tool: str, steps: list[Step], total: int) -> tuple[bool, int | None, str | None]:
    """실행 결과 → (ok, exit_code, error). `ok`와 `exit_code`의 의미를 섞지 않는다.

    - 도구가 죽음/실행 불가: `ok=False`
    - 명령이 하나도 안 돎(`steps`가 빔): `ok=False, exit_code=None` - 돌려줄 코드가 없다
    - 돌았는데 아무것도 못 찾음: `ok=True, exit_code=0`
    - 돌아서 뭔가 찾음: `ok=True, exit_code!=0`
    - **예외 하나 — `index`가 심볼 0개를 냈다: `ok=False, exit_code=0`** (#576). 인덱서는 그것을
      실패로 안 보지만 빈 그래프는 E2의 근거가 못 된다. 그래서 「`ok=False`면 exit가 0이 아니다」는
      성립하지 않는다 — `ok`를 먼저 보는 쪽(`gate.evaluate`의 `usable`)은 영향이 없다.

    이 관례가 깨지면 `with_signal()`이 benign 표본을 거르지 못하고 Evidence Gate E1이
    모든 Finding에서 참이 된다.

    **`exit_code`는 그 명령이 반환한 값을 그대로 남긴다.** 합성하면 기록에 작은 거짓이
    들어가고, 합성할 필요도 없다 — 거르는 쪽이 `ok`를 먼저 보므로 **도구 자신이 죽은 것**은
    `ok=False`에서 걸린다. asan·poc는 마지막 명령이 대상이라 그 값이 대상의 것이지만, gdb는
    마지막 명령이 gdb 프로세스라 대상이 스스로 끝났을 때만 대상의 값과 같다
    (`--return-child-result`).

    ⚠ **「크래시」를 한 문단 안에서 두 뜻으로 쓰지 않는다.** 아래 `_RUNTIME_TOOLS` 갈래가
    말하는 크래시는 **대상 프로그램**의 것이고 그쪽은 `ok=True`(신호)다 — gdb 갈래도 같다
    (stdout의 수명주기 줄로 대상이 떴는지 먼저 가른다). 그리고 그 갈래는 `with_signal()`이
    보지 않는다 — 유일한 프로덕션 호출부(`runner.py`)가 `signals.run()`의 산출물만 받고
    `SIGNAL_TOOLS`는 `semgrep`·`clang-tidy`·`build` 셋이다.
    """
    if not steps:
        # 명령이 하나도 안 돌았으면 돌려줄 종료 코드가 없다 - 127을 지어내면 「대상이 안 떴다」로
        # 읽혀 8단계가 측정을 못 낸 건으로 보류한다(#326). `plan()`은 argv를 안 받고 명령을 늘 하나
        # 이상 내므로 도달 불가 방어 갈래다 - 종료 코드를 지어내지 않으려고 `None`이다.
        return False, None, f"{tool}: 실행된 명령이 없다"
    last = steps[-1]
    code = last.exit_code
    if tool in _RUNTIME_TOOLS:
        if len(steps) < total:
            return False, code, (
                f"{tool}: 빌드 단계에서 끊겨 프로그램이 실행되지 않았다"
                + compile_failure_reason(last.stderr)
            )
        if tool == "gdb":
            # 마지막 명령이 gdb 자신이다 — 종료 코드는 대상이 떴는지 못 알린다(안 떠도
            # 1·255, 대상이 `return 127;`이면 127 — 2026-09-30 실측, gdb 12.1·15.1).
            # gdb가 stdout에 남긴 대상 수명주기 줄로 판정한다.
            if _GDB_TARGET_LIFECYCLE.search(last.stdout) is None:
                return False, code, f"gdb: 대상을 띄우지 못했다 (exit={code}) — 로그의 stderr 참조"
            return True, code, None
        if tool == "poc":
            # ⏵ **2026-10-09 (ADR 0014)** — `poc`는 과제 `-vul` 이미지를 돌리므로 판별자가 넓다.
            # 🔴 **실행 환경이 낸 코드를 타깃의 판정으로 읽지 않는다**: `124` 타임아웃 · `125`
            # docker 자체 실패(데몬 오류·이미지 없음) · `126` 실행 불가 · `127` 명령 없음 ·
            # **음수**(시그널로 죽은 자식 — docker 클라이언트가 죽었다).
            # `tools/oracle_canary.py`가 그 위험을 적는다 — *"이 코드로 `-vul` 다리가 채워지면
            # 망가진 docker 설정이 「정답 PoC가 타깃을 터뜨렸다」로 읽힌다."*
            if _poc_daemon_unreachable(last):
                return False, code, (
                    "poc: docker 클라이언트가 데몬에 닿지 못했다 (exit=1) — 실행 환경의 실패이지 "
                    "대상의 판정이 아니다"
                )
            if _poc_is_harness_failure(code):
                # ⏵ 2026-10-09 (적대적 리뷰) — **「띄우지 못했다」로 단정하지 않는다.** `137`은
                # docker SIGKILL이고 위 `_POC_ENV_FATAL` 주석이 **대상 자신의 OOM과 갈리지
                # 않는다**고 적는다 — 그 경우 이미지는 **떴다**. 한 문구로 참인 것은 「대상의
                # 판정으로 읽을 수 없다」까지다. 문구는 `POC_UNREADABLE_EXIT_REASON`이고
                # 테스트가 전체 철자를 문다.
                return False, code, POC_UNREADABLE_EXIT_REASON.format(code=code)
            # 여기까지 오면 대상이 실제로 떴다. 비-0은 실패가 아니라 신호다.
            # `1`의 애매함은 숨기지 않고 `summary_line`이 `Evidence.harness_note`로 관측에 적는다.
            return True, code, None
        # asan: 마지막 명령이 우리가 빌드한 대상 바이너리다.
        if code in _EXEC_FAILED:
            return False, code, f"{tool}: 실행 파일을 띄우지 못했다 (exit={code})"
        # 여기까지 오면 마지막 명령(대상)이 실제로 떴다. 크래시(non-zero)는 실패가 아니라 신호다.
        return True, code, None
    if tool == "semgrep":
        return judge_semgrep(last)
    if tool == "clang-tidy":
        # **명령을 띄우지도 못한 것은 신호가 아니다.** asan이 위에서 하는 `_EXEC_FAILED`
        # 판정과 같은 것이고(`poc`는 위에서 `_poc_is_harness_failure`를 쓴다), 그 상수의 주석이
        # 이미 "크래시(=신호)와 구분해야 한다"고 적어 뒀다.
        # 여기에 없던 동안 **컨테이너 기동 실패가 정적 신호로 집계됐다** — 2026-08-28
        # `run_20260828_0001`에서 keyring 쿼터로 docker가 컨테이너를 못 띄워 도구 13건이
        # 전부 exit 125였는데, semgrep·build는 `ok=False`로 떨어진 반면 clang-tidy만
        # `ok=True`로 올라가 `signal_artifacts: 3`이 만들어졌다.
        if code in _EXEC_FAILED:
            return False, code, f"clang-tidy: 명령을 띄우지 못했다 (exit={code})"
        # 진단은 stdout으로 나온다("N warnings generated."만 stderr다).
        if _COMPILE_ERROR_MARKER in last.stdout:
            # exit code로는 "경고 발견"과 구분되지 않는다(모듈 docstring, #61).
            return False, code, (
                "clang-tidy: 대상이 컴파일되지 않는다"
                + compile_failure_reason(last.stdout, tagged_only=True)
            )
        # **non-zero인데 진단이 한 줄도 없으면 돌지 않은 것이다.** `--warnings-as-errors=*`가
        # non-zero를 만드는 경로는 경고 발견뿐이고, 그 경고는 stdout에 찍힌다(`--quiet`는
        # "N warnings generated." 요약만 지운다). 위 `_EXEC_FAILED`가 지금 아는 코드를 막지만
        # **런타임이 다른 코드로 실패하면 그쪽은 또 신호가 된다** — `judge_semgrep`이
        # exit code 대신 결과 문서의 유무로 판정하기로 한 것과 같은 논법이고, 그 판정이
        # 나온 계기(2026-08-14, 한 번도 돌지 않은 semgrep이 정적 신호를 만들었다)가
        # clang-tidy에서 2026-08-28에 그대로 재현됐다.
        if code != 0 and not last.stdout.strip():
            return False, code, (
                f"clang-tidy가 진단을 한 줄도 내지 않았다 (exit={code}). 로그의 stderr 참조"
            )
        return True, code, None
    if tool == INDEX_TOOL and code == 0:
        # ⏵ 2026-10-13 (#576) — **심볼 0개의 그래프는 근거가 못 된다.** 인덱서는 그것을 실패로 안
        # 내지만(`indexer.py` 종료 코드 절 — 헤더뿐인 대상은 정상이다) 게이트의 `graph_ok`는 이
        # Evidence의 `ok`만 보므로, 여기서 `ok=True`를 내면 **빈 그래프 위에서 E2가 열린다.**
        # 요약 줄이 없거나 못 읽으면 건드리지 않는다 — 안 잰 것을 0으로 읽지 않는다(CLAUDE.md §0).
        match = _INDEX_SYMBOL_COUNT.search(last.stdout)
        if match is not None and int(match.group(1)) == 0:
            return False, 0, "index: 심볼 0개 — 그래프가 비어 E2 근거가 못 된다 (#576)"
    if code == 0:
        return True, 0, None
    return False, code, f"{tool} 실패 (exit={code})" + (
        compile_failure_reason(last.stderr) if tool == "build" else ""
    )


def _banner_line(text: str, start: int) -> str:
    """`start`가 든 줄. 제어 문자는 공백으로, 200자로 묶는다 — 대상이 찍은 날것이다.

    ⚠ **줄이 상한을 넘으면 창을 `start`에서 잡는다.** 종전에는 줄머리부터 200자를 떼서,
    배너 앞에 긴 접두가 같은 줄에 붙으면 **배너 철자가 창 밖으로 나갔다** — 그러면 `banner`가
    배너를 안 들고 있어 「읽는 쪽이 가른다」(`_CRASH_BANNER` 주석)가 성립하지 않는다.
    2026-10-13 실측(이 함수를 직접 호출): `"X" * 300 + "==1==ERROR: AddressSanitizer: …"`의
    저장된 줄이 `X` 200자였다. 접두를 버린 것은 `…`로 표시한다. 줄이 상한 안이면
    종전 그대로 줄머리부터다 — 짧은 줄에서 접두를 잃지 않는다.
    """
    begin = text.rfind("\n", 0, start) + 1
    end = text.find("\n", start)
    end = len(text) if end == -1 else end
    line = re.sub(r"[\x00-\x1f\x7f]", " ", text[begin:end]).strip()
    if len(line) <= _BANNER_CHARS or start == begin:
        return line[:_BANNER_CHARS]
    from_hit = re.sub(r"[\x00-\x1f\x7f]", " ", text[start:end]).strip()
    return f"…{from_hit}"[:_BANNER_CHARS]


def observe_crash(tool: str, steps: list[Step], total: int) -> tuple[bool | None, str | None]:
    """실행 로그에서 크래시 흔적을 관측한다 → (crashed, banner). **기록뿐이다** (#232).

    - `True` — 배너(`_CRASH_BANNER`) 또는 gdb의 신호 정지 줄이 있다. `banner`는 그 첫 줄.
    - `False` — 대상이 떴고 로그를 읽었는데 없다.
    - `None` — **안 쟀다**: 런타임 도구가 아니거나, 명령이 다 안 돌아 대상이 안 떴다(빌드에서 끊김).

    호출부가 `judge()`의 `ok`가 참일 때만 부른다 — 대상이 안 뜬 건(`poc`의 환경 실패 코드 포함)에는
    관측이 없고 `Evidence`가 `ok=False`에 `crashed`를 못 싣게 막는다.

    로그는 **마지막 명령**의 stderr과 stdout이고 **stderr를 먼저 본다**(sanitizer 보고와 셸의
    신호 보고가 둘 다 stderr로 가므로 — 아래 주석). asan·poc는 그 명령이 대상이고, 앞 명령은 빌드라
    컴파일러 출력이 배너 철자를 우연히 품어도 대상의 흔적으로 안 읽는다. gdb는 마지막 명령이
    gdb 자신이라 대상 수명주기 줄(`_GDB_TARGET_LIFECYCLE`)로 가른다 — 신호 정지는 `True`, 정상
    종료는 `False`. 같은 로그에 둘 다 있으면 신호 정지가 이긴다.
    """
    if tool not in _RUNTIME_TOOLS or not steps or len(steps) < total:
        return None, None
    last = steps[-1]
    if tool == "gdb":
        for match in _GDB_TARGET_LIFECYCLE.finditer(last.stdout):
            if not match.group(0).startswith("[Inferior"):
                return True, _banner_line(last.stdout, match.start())
        # 수명주기 줄이 아예 없으면 대상이 안 뜬 것이고 `judge()`가 이미 `ok=False`로 내렸다.
        return (False, None) if _GDB_TARGET_LIFECYCLE.search(last.stdout) else (None, None)
    # ⚠ **stderr를 먼저 본다.** 종전에는 `f"{stdout}\n{stderr}"` 한 덩이에서 첫 히트를 썼고,
    # stdout이 앞에 붙으므로 **대상이 직접 찍은 오탐 문구가 stderr의 진짜 배너를 이겼다**.
    # 2026-10-13 실측(이 함수를 직접 호출): stdout에 `runtime error: …`(대상이 찍은 글),
    # stderr에 `==1==ERROR: AddressSanitizer: heap-buffer-overflow`를 두니 `banner`가 stdout
    # 줄이었다. sanitizer 보고와 셸의 신호 보고는 **둘 다 stderr로 간다**(`_CRASH_BANNER`의
    # 묶음 둘 주석). stderr에 히트가 없으면 stdout으로 내려간다 — `2>&1`로 합쳐진 로그와
    # stdout으로 배너를 찍는 대상이 그쪽이다.
    # ⚠ 한 스트림 안에서는 여전히 **첫 히트**다 — 같은 stderr에 오탐이 진짜 배너보다 앞서면
    # 오탐이 이긴다. 그것은 안 고쳤다(묶음별 우선순위가 필요하고 실측 사례가 없다).
    for stream in (last.stderr, last.stdout):
        match = _CRASH_BANNER.search(stream)
        if match is not None:
            return True, _banner_line(stream, match.start())
    return False, None


def judge_semgrep(step: Step) -> tuple[bool, int, str | None]:
    """semgrep은 **exit code를 믿지 않는다.** 실측 근거가 있다.

    2026-08-14 첫 `dev_docker` 실행에서 semgrep이 `--read-only` 아래 `$HOME/.semgrep`에
    설정 파일을 쓰려다 처리되지 않은 예외로 죽었다. 파이썬 크래시라 exit code가 1이었고,
    `_SEMGREP_OK = (0, 1)`이 그것을 "발견함"으로 읽어 **한 번도 돌지 않은 도구가 정적
    신호를 만들었다.** `nodes/signals.py`가 경고하는 혼동이 반대 방향으로 실현된 것이다.

    그래서 `--json`이 만든 결과 문서의 유무로 "실제로 돌았는지"를 판정한다. 문서가
    없거나 stderr에 Traceback이 있으면 도구 실패다. `_SEMGREP_OK`는 그 다음에야 쓰인다 —
    2·7 같은 semgrep 자신의 오류 코드는 여전히 실패다.

    `exit_code`는 프로세스가 반환한 값 그대로다. 발견 여부는 `--error`가 exit code로
    표현하고, 결과 문서는 그것이 **믿을 만한지**만 판정한다.
    """
    if _TRACEBACK_MARKER in step.stderr:
        return False, step.exit_code, "semgrep이 예외로 죽었다 (stderr의 Traceback 참조)"
    if semgrep_results(step.stdout) is None:
        return False, step.exit_code, (
            f"semgrep이 결과 문서를 내지 않았다 (exit={step.exit_code}). 로그의 stderr 참조"
        )
    if step.exit_code not in _SEMGREP_OK:
        return False, step.exit_code, f"semgrep 실행 실패 (exit={step.exit_code})"
    scanned = semgrep_scanned(step.stdout)
    if scanned is not None and not scanned:
        # 문서는 정상인데 **연 파일이 0개**다. `.inc`처럼 확장자가 언어로 매핑되지 않은
        # 대상이 그렇다(#66) — exit 0·results 0이라 지금까지 "정적 신호 없음"으로 읽혔지만,
        # 도구가 그 파일을 **아예 안 본 것**이다. `judge_semgrep`이 결과 문서 유무로
        # "돌았는가"를 가르는 것과 같은 논법으로, `paths.scanned`가 "그 파일을 봤는가"를 가른다.
        #
        # **`_SEMGREP_OK` 검사 뒤다.** semgrep이 자기 오류(2·7)로 죽으면 스캔 목록이 비는
        # 것은 **결과이지 원인이 아니다** — 앞에 두면 관측하지 않은 원인(확장자 매핑)을
        # 사유로 적게 된다(§0). 판정과 `exit_code`는 어느 순서든 같다.
        return False, step.exit_code, (
            f"semgrep이 어떤 파일도 스캔하지 않았다 (exit={step.exit_code}) — 확장자가 "
            f"언어로 매핑되지 않은 대상(.inc 등). '정적 신호 없음'과 '도구가 안 봄'을 가른다"
        )
    return True, step.exit_code, None


def semgrep_results(stdout: str) -> list | None:
    """`--json` 출력의 `results`. 문서가 아니거나 형태가 다르면 `None`(= 돌지 않았다).

    파싱 실패를 성공 쪽으로 해석하지 않는다 — 판정이 틀릴 때 "도구가 죽었다"로 기우는
    편이 "신호를 찾았다"로 기우는 것보다 훨씬 싸다.
    """
    try:
        document = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    results = document.get("results")
    return results if isinstance(results, list) else None


def semgrep_scanned(stdout: str) -> list | None:
    """`--json` 출력의 `paths.scanned` — semgrep이 실제로 연 파일 목록.

    `None`은 "이 출력 형태에 목록이 없다"(옛 픽스처·비정상 출력)이고, **`[]`는 문서는
    정상인데 연 파일이 0개**다. 둘을 가르는 이유는 `judge_semgrep`이 전자엔 손대지 않고
    (결과 문서 유무 판정이 이미 잡는다) 후자만 "도구가 안 봤다"로 내리기 때문이다(#66).
    실제 semgrep은 항상 `paths`를 내므로 `[]`는 `.inc` 같은 미매핑 대상에서만 나온다.
    """
    try:
        document = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return None
    scanned = paths.get("scanned")
    return scanned if isinstance(scanned, list) else None


def unexecuted_commands_note(executed: int, total: int) -> str | None:
    """break로 안 돈 명령을 기록에 남길 한 줄. 다 돌았으면 `None` (#75).

    `_execute`는 앞 명령이 non-zero면 break한다 — 그 break는 `asan`의 "빌드 실패면 실행하지
    않는다"(E4 조건)를 지키는 장치라 유지한다. 대신 **언어 그룹이 갈리는 도구**(clang-tidy와
    `build` 둘 다 `_by_language`를 쓴다)에서 앞 그룹의 발견이 뒤 그룹을 가리고 그 사실이
    어디에도 안 남던 것을 기록으로 남긴다. 판정(`judge`)·등급·스키마는 건드리지 않는다.

    **문구는 원인을 단정하지 않는다.** 이 함수는 `tool`을 받지 않아 `asan`처럼 언어 그룹이
    갈릴 수 없는 도구(혼합 대상은 `plan()`이 거절한다)에서도 불린다. 정확한 사유는 `judge()`가
    `Evidence.error`에 따로 내므로 여기서는 **관측한 사실만** 적는다.
    """
    if executed >= total:
        return None
    return f"# 실행되지 않은 명령 {total - executed}개 — 앞 명령이 non-zero로 끝나 break됨"


def summary_line(tool: str, steps: list[Step]) -> str | None:
    """로그 머리에 붙일 줄. `--json`으로 잃은 가독성을 여기서 되돌린다.
    semgrep이 대상을 반쪽만 파싱했으면 **두 줄**이다(개행으로 이어 한 반환값).

    ⏵ **2026-10-09 (ADR 0014) — `poc`의 `exit 1`에 애매함을 적는다.** ⏵ 2026-10-13 (#573) 이 줄은
    `poc`에서는 로그가 아니라 `Evidence.harness_note`로 가고 두 관측(`runner._pov_observation` ·
    `nodes/auditor._probe_observation`)이 머리줄 뒤에 붙인다 — 로그의 머리·꼬리에 두면 읽기 상한과
    꼬리 상한에 잘렸다. 🔴 **`1`은 ASan의 기본 종료
    코드일 수도, docker·셸의 일반 실패일 수도 있어 갈리지 않는다** — 재 본 둘 중 하나가 정확히
    이 값이다(`oss-fuzz:368076871`(ASan) **1** · `arvo:1065`(MSan) **77**, 2026-10-04 실측).
    **가를 수 없으면 가른 척하지 않는다**(§0): 등급은 `judge`가 「떴다」로 두고, 모델이
    「크래시다」로 읽지 않게 그 사실만 관측에 싣는다.
    """
    if tool == "poc":
        # 데몬에 못 닿은 `1`은 애매하지 않다 — `judge`가 환경 실패로 이미 갈랐다.
        if steps and steps[-1].exit_code == _POC_AMBIGUOUS_EXIT and not _poc_daemon_unreachable(
            steps[-1]
        ):
            return POC_AMBIGUOUS_NOTE
        return None
    if tool != "semgrep" or not steps:
        return None
    results = semgrep_results(steps[-1].stdout)
    if results is None:
        return "# semgrep: 결과 문서 없음 — 아래 stderr 참조"
    head = f"# semgrep: {len(results)} findings"
    # ⏵ 2026-10-10 (이슈 #563 선행) — 반쪽 파싱을 **같은 반환값의 둘째 줄에** 적는다. 떨어뜨려 두면
    # 관측 꼬리 상한에서 잘려 수만 남는다(`POC_AMBIGUOUS_NOTE`가 그렇게 잘린 선례 — #573, 그 줄은
    # 이제 로그 밖 `Evidence.harness_note`다. 이 semgrep 줄은 여전히 로그 머리에 산다).
    # ⚠ 모델 사본에서는 이 노트를 `prompt_loader`가 빼고 영어 표지를 `errors:` 줄에 싣는다 — 접기가
    # 로그 머리를 80자에서 자르기 때문이다(적대적 리뷰 2026-10-10).
    partial = semgrep_partial_parse(steps[-1].stdout)
    if partial:
        shown = ", ".join(partial[:3]) + (" 외" if len(partial) > 3 else "")
        detail = f"{SEMGREP_PARTIAL_NOTE} ({len(partial)}개: {shown})"
        return head + chr(10) + detail
    return head


def first_hit_location(tool: str, steps: list[Step]) -> Location | None:
    """정적 신호의 **첫 발견 위치**를 `Evidence.location`에 실을 수 있게 뽑는다 (#70 초석).

    지금은 semgrep `--json`의 `results[0]`만 읽는다 — `path`·`start.line`·`end.line`을
    옮긴다. semgrep은 명령행에 컨테이너 마운트 경로(`{MOUNT}/<rel>`)를 받으므로 `path`가
    `/src/parser.c`로 나온다(실측). 그 접두를 벗겨 `Candidate.location.file`(저장소 상대
    경로)과 같은 좌표계로 맞춘다 — 안 벗기면 `overlaps`의 파일 비교가 어긋나 `location_ok`가
    계속 거짓이다. clang-tidy는 출력이 텍스트라 정규화 위험이 커 **여기서 다루지 않는다**(#70 후속 —
    미착수, #507이 선행).

    **채워도 게이트 등급은 안 바뀐다**(#70): `p1 = static_ok or location_ok`이고
    STATIC Evidence에만 location이 붙어 `static_ok`가 이미 참이다. 이 값의 쓰임은 등급이
    아니라 신호를 심볼 단위로 귀속하는 후속 PR(신호 1건당 Evidence)의 입력이다(+ 2026-10-04부터
    content-aware 프롬프트의 FACTS·Critic 줄 — 이슈 #475: 이 값을 바꾸면 그 판의 프롬프트
    바이트가 바뀐다). 형태가
    어긋나면 채우지 않는다 — 틀린 위치를 넣느니 `None`이 낫다(`location_ok`는 그대로 거짓).
    """
    if tool != "semgrep" or not steps:
        return None
    results = semgrep_results(steps[-1].stdout)
    if not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    path = first.get("path")
    start = first.get("start")
    if not isinstance(path, str) or not path or not isinstance(start, dict):
        return None
    # semgrep이 스캔한 컨테이너 경로(`/src/parser.c`)를 저장소 상대(`parser.c`)로 되돌린다.
    file = path.removeprefix(MOUNT + "/") if path.startswith(MOUNT + "/") else path
    line_start = start.get("line")
    if not isinstance(line_start, int) or line_start < 1:
        return None
    # end가 없거나 형태가 어긋나면 채우지 않는다(`overlaps`가 `line_end or line_start`로
    # 처리한다). 합성한 값을 넣지 않는 것이 docstring의 "형태가 어긋나면 채우지 않는다"다.
    end = first.get("end")
    end_line = end.get("line") if isinstance(end, dict) else None
    line_end = end_line if isinstance(end_line, int) and end_line >= line_start else None
    return Location(file=file, line_start=line_start, line_end=line_end)


class ContainerToolRunner:
    """`ToolRunner` 구현. 런타임만 다르고 계약·판정은 하나다."""

    def __init__(
        self,
        *,
        ids: IdFactory,
        run_id: str,
        root: Path,
        artifacts_root: Path,
        runtime: str = "docker",
        image: str = DEFAULT_IMAGE,
        sif_dir: Path | None = None,
        observed_at: datetime | None = None,
        on_command: CommandHook | None = None,
        poc_target: PocTarget | None = None,
    ) -> None:
        if runtime not in UNENFORCED:
            raise ValueError(f"모르는 런타임: {runtime!r}. 있는 것: {sorted(UNENFORCED)}")
        if shutil.which(runtime) is None:
            # Evidence조차 만들 수 없는 유일한 경우다. run 도중이 아니라 조립 시점에
            # 터뜨려 사용자가 5분 뒤가 아니라 즉시 알게 한다.
            raise SandboxUnavailable(f"{runtime} 실행 파일이 PATH에 없다")
        self.runtime = runtime
        self.ids = ids
        self.run_id = run_id
        self.root = Path(root)
        self.artifacts_root = Path(artifacts_root)
        # 컨테이너 이름에 artifacts 루트를 가르는 값을 싣는다(#303). `run_id`는 루트마다
        # `run_YYYYMMDD_NNNN`이라 루트가 다른 두 run이 같은 날 같은 id를 갖고, 같은 장비에서
        # 동시에 돌면 이름이 겹친다 — 2026-09-26 ATOM 실측에서 두 run 모두 도구 호출 12건씩이
        # exit 125로 조용히 빠졌다(#303).
        # 루트 경로의 해시라 같은 루트의 순차 run은 지금처럼 `run_id`가 가른다.
        self._name_tag = hashlib.sha256(
            str(self.artifacts_root.resolve()).encode("utf-8")
        ).hexdigest()[:8]
        self.image = image
        self.sif_dir = Path(
            sif_dir or os.environ.get("HARNESS_SIF_DIR") or self.artifacts_root / "sif"
        )
        # 시각을 주입받는다 — 재현 실행에서 같은 값이 나와야 trace를 대조할 수 있다.
        self.observed_at = observed_at or datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        self.on_command = on_command
        # `poc` 도구가 돌릴 과제 이미지 (ADR 0014). `None`이면 `poc`가 **거절한다** —
        # 폴백을 지어내지 않는다. 배선 자리가 오라클과 같아서(`poc_target_for` docstring) 카나리
        # 선행을 통과하지 않은 run에서는 여기가 비고 `poc`는 실패 Evidence가 된다.
        self.poc_target = poc_target

    # --- ToolRunner ---------------------------------------------------------

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence:
        evidence_id = self.ids.next("evidence")
        kind = TOOL_KINDS.get(tool)
        if kind is None:
            return self._fail(
                evidence_id,
                tool,
                EvidenceKind.BUILD,
                args,
                127,
                f"tool_policy 밖 도구: {tool!r}. 허용: {sorted(TOOL_KINDS)}",
            )
        if sandbox.network:
            # disclosure-policy.md §5: 외부 시스템 실행 금지. 조용히 통과시키면 정책이 없다.
            return self._fail(evidence_id, tool, kind, args, 1, "network=True는 허용되지 않는다")
        try:
            resolved = safe_join(self.root, args.workdir)
        except PathEscape as exc:
            return self._fail(evidence_id, tool, kind, args, 1, str(exc))
        if not resolved.exists():
            return self._fail(evidence_id, tool, kind, args, 2, f"workdir가 없다: {args.workdir}")
        if self.runtime == "singularity":
            # 🔴 **명령이 쓸 이미지를 **전부** 본다.** ⏵ 2026-10-09 (ADR 0014) — `poc`는 과제
            # `-vul` 이미지를 돌리고 그 이미지는 Docker 이미지다(`n132/arvo:…`). SIF로 변환해 둔
            # 장비는 없으므로 여기서 **먼저 거절한다**. 안 막으면 `container_argv`가 없는 SIF
            # 경로를 만들고 singularity가 그 실패로 **임의의 종료 코드**를 내는데, 그 값이
            # `_POC_HARNESS_FAILURE` 밖이면 **대상의 판정으로 읽힌다** — 이 모듈이 막으려는
            # 바로 그 사고다.
            images = [sandbox.image]
            if tool == "poc" and self.poc_target is not None:
                images.append(self.poc_target.image)
            for image in images:
                sif = sif_path(self.sif_dir, image)
                if not sif.exists():
                    # 자동으로 pull 하지 않는다 — 실행 중 네트워크는 network=False와 충돌한다.
                    # 오류 문구에는 호스트 경로를 안 싣는다 — `Evidence.error`는 PoV·Auditor 관측
                    # 머리줄로 모델에 가고 로그 본문도 같은 문구다. SIF 파일 이름만 든다.
                    return self._fail(
                        evidence_id,
                        tool,
                        kind,
                        args,
                        127,
                        f"SIF가 없다: {sif.name} (변환 절차는 tools/README.md)",
                    )
        try:
            commands = self._plan_for(tool, resolved)
        except ValueError as exc:
            return self._fail(evidence_id, tool, kind, args, 2, str(exc))
        return self._execute(evidence_id, tool, kind, args, sandbox, commands)

    def images(self) -> dict[str, str]:
        """`RunRecord.tool_images`에 들어갈 이미지 → digest.

        **못 구하면 빈 dict다.** 지어낸 값이 들어가면 재현성 기록 자체가 거짓이 되고,
        `runner.incomplete_fields()`가 경고를 내는 편이 옳은 동작이다.

        🔴 **⏵ 2026-10-09 (ADR 0014) — 이 run에 배선된(돌 수 있는) 이미지 전부를 든다.** `poc`가
        한 번도 안 불린 run에도 과제 이미지가 실린다(채점기가 서면 늘 배선된다). `poc`가 과제 `-vul`
        이미지로 나가므로 하네스 이미지 하나만 적으면 **그 run의 재현성 기록이 거짓**이 된다 —
        돈 것이 둘인데 하나만 적힌다. ADR 0005(`docs/decisions/0005-tool-runner-override.md`)가
        `--tool-runner` 예외를 열며 *"예외를 열되 사후에 구분 가능하게"* `RunRecord.tool_runner`를
        필수로 추가한 것과 같은 거래다: 여기서는 **샌드박스 이미지가 도구마다 갈리는 예외**를
        열고 기록 필드로 값을 치른다.

        🔴 **하나라도 못 구하면 전부 버린다 — 빈 dict다.** 부분 사전을 돌려주면
        `runner.incomplete_fields()`가 `not record.tool_images`로만 보므로 **경고가 안 뜨고**,
        그 run이 digest 하나가 빠진 채 비교표에 들어간다(적대적 리뷰 2026-10-09 H3이 재현했다 —
        하네스 이미지 inspect만 실패시키면 과제 이미지 하나로 「완전한 기록」이 됐다). 전 판의
        안전장치가 *"못 구하면 빈 dict다"*였고 그것을 **유지한다** — 이 결정이 치르는 값은
        「어느 쪽이 빠졌는지 모른다」이고, 그것은 로그의 `$ {argv}` 줄이 답한다.
        """
        ran = [self.image] + ([self.poc_target.image] if self.poc_target else [])
        digests = {image: self._image_digest(image) for image in ran}
        if any(digest is None for digest in digests.values()):
            return {}
        return {image: digest for image, digest in digests.items() if digest is not None}

    def compile_flags(self) -> CompileFlags:
        """`RunRecord.compile_flags`에 들어갈 컴파일 조건 (#74).

        **`images()`와 같은 자리다** — factory가 구현에게 묻고, 여기서 낸 값이 그대로
        재현성 기록이 된다. 그래서 `plan()`이 쓰는 **바로 그 상수**를 돌려준다. 값을
        여기서 다시 적으면 `plan()`이 움직였을 때 기록만 옛 값으로 남고, 그 갈라짐은
        어디에도 안 뜬다.

        `-I`는 **고정분만** 담는다. `_include_args()`가 내는 둘 중 대상 디렉터리는
        명령마다 달라 두 run을 나란히 놓는 데 못 쓰고, `MOUNT`는 두 갈래 어느 쪽에서도
        항상 간다. 실제로 간 argv는 `_execute`가 로그에 남기는 `$ {argv}` 줄이 답한다.
        """
        return CompileFlags(std=dict(_STD_ARGS), fixed_include_dirs=[MOUNT])

    # --- 실행 ---------------------------------------------------------------

    def _plan_for(self, tool: str, resolved: Path) -> list[Command]:
        """`workdir`는 파일일 수도 디렉터리일 수도 있다(노드가 artifact 경로를 그대로 준다).

        **넘길 확장자가 도구마다 다르다.** 인덱서만 `.inc`를 직접 받는다 — 이유는
        `_COMPILE_SUFFIXES`·`_INDEX_SUFFIXES` 주석에 있다. 한 목록으로 합치면 clang
        계열이 단독 컴파일되지 않는 조각을 받아 없는 실패를 만들어낸다.
        """
        # 리터럴이 아니라 상수를 본다. 갈리면 인덱서가 조용히 `_COMPILE_SUFFIXES`를 받아
        # `.inc`가 빠지고, #66이 기록한 실패(28바이트짜리 `#include` 한 줄만 분석하고
        # 17KB를 보지 않았다)가 그대로 재현된다.
        candidates = sources(resolved)
        commands = plan(
            tool,
            target=_in_container(self.root, resolved),
            compile_sources=[
                _in_container(self.root, path) for path in candidates if _wanted(tool, path)
            ],
            cwd=_in_container(self.root, resolved if resolved.is_dir() else resolved.parent),
            poc_target=self.poc_target,
        )
        withheld = _withheld_note(tool, candidates)
        if withheld is None:
            return commands
        return [replace(command, notes=(withheld,)) for command in commands]

    def _execute(
        self,
        evidence_id: str,
        tool: str,
        kind: EvidenceKind,
        args: ToolArgs,
        sandbox: SandboxSpec,
        commands: list[Command],
    ) -> Evidence:
        scratch = scratch_dir(self.artifacts_root, self.run_id, tool)
        scratch.mkdir(parents=True, exist_ok=True)
        node_argv = args.argv
        # 쓴 입력의 호스트 경로. ⏵ 2026-10-09 (ADR 0014) — `needs_input`인 명령은 이것을
        # `POC_IN_IMAGE`로 바인드받는다. 입력이 없으면 `None`이고 그런 명령은 `container_argv`가
        # 거절한다(fail-closed).
        input_host_path: Path | None = None
        if args.input_bytes is not None:
            # 컨테이너 안 경로는 `/work/<이름>`이다 — 호스트 경로를 적으면 안 풀린다. 명령은 이
            # 경로를 받지 않지만(모듈 docstring) 머리줄에 남아 `Evidence.digest`가 입력에 달린다.
            # 오류 문구에는 호스트 경로를 안 싣는다 — 관측으로 모델에 간다.
            try:
                written = write_input(scratch, args.input_bytes)
            except OSError as exc:
                reason = exc.strerror or type(exc).__name__
                return self._fail(
                    evidence_id, tool, kind, args, 2, f"입력을 scratch에 쓰지 못했다: {reason}"
                )
            input_host_path = written
            node_argv = with_input_path(args.argv, f"{SCRATCH}/{written.name}")
        sections = [f"# node argv: {' '.join(node_argv)}", f"# runtime: {self.runtime}"]
        steps: list[Step] = []
        for index, command in enumerate(commands):
            name = f"harness-{self.run_id}-{self._name_tag}-{evidence_id}-{index}"
            # 명령줄에 못 싣는 입력(인덱서 파일 목록)을 컨테이너가 보는 scratch에 쓴다. 호스트
            # 경로는 오류 문구에 안 싣는다 - `Evidence.error`는 관측으로 모델에 간다.
            try:
                _write_scratch_files(scratch, command.scratch_files)
            except OSError as exc:
                reason = exc.strerror or type(exc).__name__
                return self._fail(
                    evidence_id, tool, kind, args, 2, f"입력 파일을 scratch에 쓰지 못했다: {reason}"
                )
            # 로그 머리가 무엇을 읽혔는지 말한다 - 목록 파일은 명령줄에 안 실려서 이 줄이 없으면
            # `digest`가 목록과 이어지지 않는다(같은 명령줄에 목록만 다른 두 run이 구별되지 않는다).
            sections.extend(command.notes)
            sections.extend(_scratch_file_line(name, data) for name, data in command.scratch_files)
            # `container_argv`는 입력을 받는 명령에 경로가 없으면 던진다(ADR 0014). 그것은
            # **배선 결함**이지 샌드박스 부재가 아니므로 `ToolRunner` 계약대로 Evidence로 낸다 —
            # 예외를 던지는 것은 Evidence조차 만들 수 없는 경우뿐이다.
            try:
                argv = container_argv(
                    self.runtime,
                    sandbox=sandbox,
                    root=self.root,
                    scratch=scratch,
                    command=command,
                    name=name,
                    sif_dir=self.sif_dir,
                    env=args.env,
                    input_host_path=input_host_path,
                )
            except ValueError as exc:
                return self._fail(evidence_id, tool, kind, args, 2, str(exc))
            if self.on_command is not None:
                self.on_command(tool, argv)
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=sandbox.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self._kill(name)
                sections.append(f"$ {' '.join(argv)}\n(timeout after {sandbox.timeout_s}s)")
                return self._write(
                    evidence_id,
                    tool,
                    kind,
                    args,
                    sections,
                    ok=False,
                    exit_code=_TIMEOUT_EXIT,
                    error=f"timeout after {sandbox.timeout_s}s",
                )
            except OSError as exc:
                sections.append(f"$ {' '.join(argv)}\n({type(exc).__name__}: {exc})")
                return self._write(
                    evidence_id,
                    tool,
                    kind,
                    args,
                    sections,
                    ok=False,
                    exit_code=127,
                    error=f"{self.runtime} 실행 실패: {exc}",
                )
            sections.append(
                f"$ {' '.join(argv)}\n(exit={proc.returncode})\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )
            steps.append(Step(proc.returncode, proc.stdout, proc.stderr))
            if proc.returncode != 0:
                break
        note = unexecuted_commands_note(len(steps), len(commands))
        if note is not None:
            sections.append(note)
        ok, exit_code, error = judge(tool, steps, len(commands))
        # 기록뿐이다(#232) — 대상이 뜬(`ok`) 런타임 도구에만 잰다. 게이트는 이 값을 읽지 않는다.
        crashed, banner = observe_crash(tool, steps, len(commands)) if ok else (None, None)
        summary = summary_line(tool, steps)
        harness_note = None
        if summary is not None and tool == "poc":
            # 🔴 **`poc`의 줄은 로그에 넣지 않고 `Evidence.harness_note`로 싣는다**(이슈 #573).
            # 하네스가 만든 줄이라 로그를 거칠 이유가 없고, 거치면 두 상한에 잘린다: 모델 관측은
            # 로그의 읽기 상한(`PAYLOAD_READ_CAP` 200,000자) 안에서 **마지막 2,000자**만 싣는데
            # (`runner._POV_OBSERVATION_TAIL_CHARS`), 꼬리에 붙인 줄은 출력이 200,000자를 넘으면
            # 읽히지도 않았다(독립 재현: 150,000자까지 있고 250,000자에서 없다). 로그에 머리·꼬리로
            # 남겨 두면 짧은 로그에서는 관측에 같은 줄이 둘 이상 나온다. v5 prefix가 *"관측에 그
            # 사실이 한 줄로 붙는다"*고 약속하는 자리다 — 두 소비자(`runner._pov_observation` ·
            # `nodes/auditor._probe_observation`)가 이 필드를 머리줄 뒤에 붙인다.
            harness_note = summary
        elif summary is not None:
            # 머리에 넣는다 — JSON 본문 앞에 결론이 있어야 사람이 로그를 읽는다.
            sections.insert(2, summary)
        return self._write(
            evidence_id,
            tool,
            kind,
            args,
            sections,
            ok=ok,
            exit_code=exit_code,
            error=error,
            location=first_hit_location(tool, steps),
            harness_note=harness_note,
            crashed=crashed,
            banner=banner,
        )

    def _kill(self, name: str) -> None:
        """타임아웃 뒤에도 컨테이너는 살아 있다. 방치하면 run마다 하나씩 쌓인다.

        **docker·podman은 정리가 필요하다.** `docker run`은 데몬에 요청을 보내는 클라이언트라
        클라이언트를 죽여도 컨테이너는 데몬 밑에서 계속 돈다. podman은 데몬이 없지만 컨테이너를
        conmon이 감시하므로 `podman run`을 죽여도 conmon 밑에 남을 수 있다 — 둘 다 이름으로
        지운다. `singularity exec`는 그 프로세스 자신이 컨테이너라 타임아웃이 프로세스를 죽이면
        함께 끝난다 — 지울 대상이 없다.
        """
        if self.runtime not in ("docker", "podman"):
            return
        subprocess.run(
            [self.runtime, "rm", "--force", name], capture_output=True, timeout=30, check=False
        )

    def _image_digest(self, image: str) -> str | None:
        """한 이미지의 digest. ⏵ 2026-10-09 — 이미지를 **인자로** 받는다(`images()`가 둘을 센다).

        🔴 **`--pull never`도 안 붙인다** — `image inspect`는 읽기이고 이미지를 당기지 않는다.
        없으면 `None`이고 그 이미지는 기록에서 빠진다(지어내지 않는다).
        """
        if self.runtime == "singularity":
            sif = sif_path(self.sif_dir, image)
            return f"sha256:{_file_sha256(sif)}" if sif.exists() else None
        # RepoDigests는 **docker에서는 push된 적 없는 로컬 빌드 이미지에 비어 있다.** 그
        # 경우 .Id를 쓴다 — 둘 다 그 이미지를 유일하게 지시하는 sha256이다. podman은 로컬
        # 빌드에도 RepoDigests를 채우지만(2026-08-17 실측) 같은 포맷이라 이 경로가 그대로 된다.
        out = _command_output(
            [
                self.runtime, "image", "inspect", image,
                "--format", "{{index .RepoDigests 0}}|{{.Id}}",
            ]
        )
        if out is None:
            return None
        repo_digest, _, image_id = out.partition("|")
        for candidate in (repo_digest.rpartition("@")[2], image_id):
            if _is_sha256(candidate):
                return candidate
        return None

    # --- Evidence -----------------------------------------------------------

    def _fail(
        self,
        evidence_id: str,
        tool: str,
        kind: EvidenceKind,
        args: ToolArgs,
        exit_code: int,
        error: str,
    ) -> Evidence:
        """실행 전에 거절된 경우. 프로세스를 띄우지 않는다.

        `ok=False`에 정수 `exit_code`를 적으므로 `validate._not_measured`가 「측정 못 함」
        (`validated` 보류)으로 센다 - 명세 실패(F-11, `exit_code=None`)가 아니다. `validate`
        경로에서는 그 분류가 맞다(#670): 모델이 쓴 요청(`repro_spec.steps` -> `args.argv`)은
        `plan()`에 닿지 않고 도구는 고정된 build 단계와 `tool_for` 대응표가 고른다. 거절 사유는
        대상 내용이나 하네스 쪽 사정이다 - 정책 밖 도구(127 - 그 대응표로는 닿지 않는다) ·
        네트워크 요청 · 경로 이탈 · workdir 없음 · SIF 없음 · `_plan_for`의 `ValueError`(`plan()`의
        넷 - C·C++ 혼합 대상 · 컴파일할 C 소스 없음 · `poc_target` 없음 · 조립 규칙 없는 도구 - 과
        루트 밖으로 풀리는 소스 경로) · 스크래치 쓰기 실패 · 입력 없는 `poc`. 스크래치 쓰기
        실패는 `input_bytes`를 실은 요청에서만 나는데 `validate._call`은 입력을 안 싣는다.
        `poc_target` 없음·입력 없는 `poc`는 `poc` 실행에서만 나는데 `validate`는 `poc`를 안
        돌린다.
        """
        return self._write(
            evidence_id,
            tool,
            kind,
            args,
            [f"# 실행하지 않았다: {error}"],
            ok=False,
            exit_code=exit_code,
            error=error,
        )

    def _write(
        self,
        evidence_id: str,
        tool: str,
        kind: EvidenceKind,
        args: ToolArgs,
        sections: list[str],
        *,
        ok: bool,
        exit_code: int | None,
        error: str | None,
        location: Location | None = None,
        harness_note: str | None = None,
        crashed: bool | None = None,
        banner: str | None = None,
    ) -> Evidence:
        """로그를 실제로 쓰고 그 내용의 sha256을 digest로 삼는다.

        디렉터리 이름은 **화이트리스트 안의 이름만** 쓴다. `tool`은 검증되지 않은 문자열이라
        (`ctx.call_tool(tool=...)`은 지금 상수만 넘기지만 스키마가 강제하지는 않는다)
        그대로 경로에 이으면 `../`가 run 디렉터리 밖에 파일을 만든다 — `src/paths.py`가
        `workdir`에 대해 막는 것과 같은 종류의 구멍이다.
        """
        body = "\n".join(sections) + "\n"
        data = body.encode("utf-8")
        slot = tool if tool in TOOL_KINDS else _REJECTED_SLOT
        path = self.artifacts_root / self.run_id / slot / f"{evidence_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 바이트로 쓰고 **그 바이트를** 해시한다 — 텍스트 쓰기는 Windows에서 LF를 CRLF로 번역해
        # `digest`가 파일과 어긋난다(#387). `orchestrator/poc_log.py`의 쓰기 절이 같은 선례다.
        path.write_bytes(data)
        return Evidence(
            evidence_id=evidence_id,
            run_id=self.run_id,
            kind=kind,
            source=tool,
            artifact_id=args.workdir,
            location=location,
            payload_ref=f"artifacts/{self.run_id}/{slot}/{evidence_id}.log",
            digest=f"sha256:{hashlib.sha256(data).hexdigest()}",
            observed_at=self.observed_at,
            ok=ok,
            exit_code=exit_code,
            error=error,
            harness_note=harness_note,
            crashed=crashed,
            banner=banner,
        )


def _require_sources(tool: str, compile_sources: list[str]) -> None:
    if not compile_sources:
        raise ValueError(f"{tool}: 컴파일할 C 소스가 없다")


def _include_args(cwd: str) -> list[str]:
    """`-I` 인자. **대상 루트와 artifact 디렉터리 둘뿐이다.**

    2026-08-25 실측(`run_20260825_0001`)에서 build 실패 27건 중 20건이
    `fatal error: 'zlib.h' file not found`·`'zutil.h' file not found`였다. 파일을 고립시켜
    컴파일하니 `#include "zlib.h"`가 아무 데서도 안 보인 것이다.

    **"헤더가 있는 모든 디렉터리"로 넓히지 않는다.** 그러면 개수가 저장소 크기에 비례해
    늘어나 상한이 없고, 대규모 저장소에서 인자가 폭발한다. 둘로 고정하면 개수가 상수이고
    관측된 20건을 전부 덮는다 — `zlib.h`·`zutil.h`는 대상 루트에 있고, 로컬 헤더는
    artifact 디렉터리에 있다.

    **네 도구(build·clang-tidy·asan·gdb)가 같은 값을 받아야 한다.** `_STD_ARGS` 주석이 방언에
    대해 적어 둔 이유가 그대로 걸린다 — 갈리면 같은 파일에 대한 두 진단이 어긋나고, 그
    차이가 코드의 성질인지 우리 플래그의 산물인지 구분할 수 없다.

    artifact가 대상 루트 자체면 둘이 같으므로 하나로 줄인다(중복 인자를 남기지 않는다 —
    로그가 곧 `digest`라 무의미한 차이도 기록에 남는다).
    """
    directories = [cwd] if cwd == MOUNT else [cwd, MOUNT]
    return [flag for directory in directories for flag in ("-I", directory)]


def _write_scratch_files(scratch: Path, files: tuple[tuple[str, bytes], ...]) -> None:
    """`write_input`과 같은 규칙으로 쓴다 - 있던 항목은 `unlink`하고 `xb`(`O_EXCL`)로 새로 만든다.

    scratch는 RW 마운트라 말단 심링크를 따라가면 마운트 밖 파일을 덮는다(`write_input` docstring).
    이름은 호출부(`plan`)가 정한 상수이고 입력에서 오지 않는다.
    """
    for name, data in files:
        path = scratch / name
        path.unlink(missing_ok=True)
        with open(path, "xb") as handle:
            handle.write(data)


def _scratch_file_line(name: str, data: bytes) -> str:
    """scratch에 쓴 목록 파일을 로그에 묶는 줄. 항목은 NUL로 가른다(`INDEX_LIST_FILE`)."""
    entries = data.count(bytes(1)) + 1 if data else 0
    return f"# scratch file {name}: {entries} entries sha256:{hashlib.sha256(data).hexdigest()}"


def _withheld_note(tool: str, candidates: list[Path]) -> str | None:
    """인덱서가 안 받은 소스를 확장자별로 센 줄. 없으면 `None`.

    분류(`sources`)는 SOURCE로 읽었지만 `_INDEX_SUFFIXES`에 없어 보내지 않은 파일이다(`.hh`·`.cxx`·
    `.CPP`…). 세지 않으면 그 파일들은 인덱스에서 이유도 개수도 없이 빠진다. 철자는 원문 그대로 센다.
    """
    if tool != INDEX_TOOL:
        return None
    counts = Counter(path.suffix for path in candidates if not _wanted(tool, path))
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "# 인덱서 미전달: " + " ".join(f"{suffix}×{count}" for suffix, count in ranked)


def _wanted(tool: str, path: Path) -> bool:
    """이 도구에 이 파일을 넘기는가. 인덱서는 **원문 철자**로, clang 계열은 소문자로 내려 가린다.

    인덱서는 `path.suffix not in _GRAMMARS`로 대소문자를 가려 `.CPP`를 `skipped`로 버린다 -
    그런 파일은 보내지 않는다(보내 봐야 이름만 남는다). clang 계열은 `.CPP`·`.C`를 컴파일 대상으로
    받는다. 리터럴이 아니라 상수를 본다 - 갈리면 인덱서가 조용히 `_COMPILE_SUFFIXES`를 받아 `.inc`가
    빠지고, #66이 기록한 실패(28바이트짜리 `#include` 한 줄만 분석하고 17KB를 보지 않았다)가
    그대로 재현된다.
    """
    if tool == INDEX_TOOL:
        return path.suffix in _INDEX_SUFFIXES
    return path.suffix.lower() in _COMPILE_SUFFIXES


def _is_cxx(source: str) -> bool:
    """C++ 소스인가. 소문자로 내려 보되 **`.C`(대문자)만은 C++**이다.

    내리면 `.c`가 되어 C 방언(`-std=gnu17`)을 받고 clang이 거부한다.
    """
    suffix = Path(source).suffix
    return suffix.lower() in _CXX_SUFFIXES or suffix == ".C"


def _by_language(compile_sources: list[str]) -> list[tuple[str, list[str]]]:
    """소스를 언어별로 가른다. 순서는 C 먼저, C++ 다음으로 고정한다(재현성).

    **한 clang 호출로 두 방언을 표현할 수 없다.** `-std=`는 위치 인자가 아니라 마지막
    값이 이기므로, 섞인 목록에 하나를 주면 다른 언어가 반드시 거부된다. 그래서 호출
    자체를 가른다 — `plan()`이 이미 `list[Command]`를 돌려주므로 새 구조가 아니다.

    **섞이는 경우는 `workdir`가 디렉터리일 때만 생긴다.** artifact가 파일이면
    `fake_tools.sources()`가 자기 자신 1건을 돌려주므로 언어가 하나다.

    빈 그룹은 내보내지 않는다 — 소스 없는 clang 호출은 그 자체로 실패한다.
    """
    groups: dict[str, list[str]] = {"c": [], "c++": []}
    for source in compile_sources:
        groups["c++" if _is_cxx(source) else "c"].append(source)
    return [(language, sources_) for language, sources_ in groups.items() if sources_]


def _in_container(root: Path, path: Path) -> str:
    """호스트 경로 → 컨테이너 안 경로. 마운트 밖은 `safe_join`이 이미 걸렀다.

    **루트 밖으로 풀리는 소스(루트 안 심링크)는 `safe_join`이 못 본다** - 그 경로는 `ValueError`로
    거절한다. 문구에는 **풀기 전** 경로의 루트 기준 상대 이름만 싣는다(어느 소스인지 알려야
    한다) - 호스트 경로는 안 싣는다(`relative_to`의 기본 문구는 두 경로를 다 든다). 풀기 전
    경로도 루트 밑이 아니면 파일 이름만 싣는다.
    """
    base = Path(root).resolve()
    try:
        relative = path.resolve().relative_to(base).as_posix()
    except ValueError:
        try:
            shown = path.relative_to(base).as_posix()
        except ValueError:
            shown = path.name
        raise ValueError(f"대상 루트 밖으로 풀리는 소스 경로가 있다: {shown}") from None
    return MOUNT if relative == "." else f"{MOUNT}/{relative}"


def _host(path: Path) -> str:
    """바인드 마운트에 쓸 호스트 경로. Windows에서도 `C:/...` 형태여야 한다."""
    return Path(path).resolve().as_posix()


def _is_sha256(value: str) -> bool:
    """`Sha256Digest`가 요구하는 형태인지. 아니면 지어내지 않고 버린다."""
    prefix, _, body = value.partition(":")
    return prefix == "sha256" and len(body) == 64 and all(c in "0123456789abcdef" for c in body)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_output(argv: list[str]) -> str | None:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()
