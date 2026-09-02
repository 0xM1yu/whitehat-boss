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
문자열뿐이라는 사실 자체가 #61 항목이다.

## 로그 규약 (engineering-guide.md §6)

`Evidence.digest`는 로그 파일 내용의 sha256이고 **로그의 각 절 첫 줄이 실제로 실행된
명령**이다. `ctx.call_tool`이 trace에 남기는 argv는 노드가 만든 자리표시자이고 진짜 명령은
이 모듈이 조립하므로(`plan()`) 둘은 다르다. `Evidence` 스키마에 argv 필드가 없으므로
(스키마 변경은 전원 공지 대상, roadmap.md §3) 실행 명령을 로그에 적어 digest에 포함시킨다.
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

## asan·poc는 실행까지 성공해야 ok=True다

Evidence Gate E4(proven)의 조건이 `SANITIZER`/`CRASH`/`POC_RUN` Evidence다. 컴파일 성공을
실행으로 세면 **한 번도 돌지 않은 프로그램이 재현 완료로 기록된다.** 그래서 빌드·링크
실패는 `ok=False`이고, 바이너리가 실제로 떠서 크래시한 경우만 `ok=True`(exit_code에 크래시
코드)다. 지금 픽스처에는 `main`이 없어 링크되지 않으므로 **E4는 여전히 도달 불가**다 —
PoC를 만드는 주체(PoV 노드)가 생기기 전까지는 그것이 사실이고, 그 사실대로 기록한다.

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
`callers`/`callees` 섹션을 채운다. `types`는 아직 아니다 — index-v1에 타입 데이터가 없다.
헤더(`.h`)는 `_INDEX_SUFFIXES`에 없어 인덱서에 넘어가지 않으므로 헤더에 정의된
`static inline` 함수는 아직 심볼에 없다. `.inc`는 넘어간다 — 그쪽은 실제 코드가 통째로
들어 있어 빠지면 대상의 대부분이 사라지기 때문이다(`_INDEX_SUFFIXES` 주석).

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
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.adapters.fake_tools import TOOL_KINDS, SandboxUnavailable, sources
from src.ids import IdFactory
from src.interfaces.tool_runner import SandboxSpec, ToolArgs
from src.paths import PathEscape, safe_join
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence

# `nodes/sandbox.DEFAULT_SANDBOX.image`와 같아야 한다. 어댑터는 오케스트레이터를 import 하지
# 않으므로(engineering-guide.md §3) 값을 복제하고 tests/test_container_tools.py가 대조한다.
DEFAULT_IMAGE = "harness-tools:v1"

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

# scratch 하위 디렉터리 이름은 도구 이름이다(`_execute`가 `tool`로 만든다).
# `nodes/index.INDEX_TOOL`과 같은 값이어야 하지만 어댑터는 오케스트레이터를 import 하지
# 않으므로(engineering-guide.md §3) 여기 복제하고 tests/test_container_tools.py가 대조한다.
#
# **이 파일 안에서도 이 상수를 써야 한다.** 두 곳이 `tool == "index"` 리터럴을 쓰고 있었고,
# 두 상수를 함께 바꾸면 대조 테스트는 통과하는데 리터럴만 뒤처져 인덱서가 조용히
# `_COMPILE_SUFFIXES`를 받는다 — `.inc`가 빠져 #66의 실패가 재현된다.
INDEX_TOOL = "index"

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
# **아래 `_INDEX_SUFFIXES`와 일부러 갈라져 있다. 합치면 build가 깨진다.**
# `.inc`는 `#include`로 펼쳐지는 함수 본문 조각이라 **단독 컴파일이 되지 않는다.**
# clang 계열(build·clang-tidy·asan)은 `target.c`만 주면 전처리가 `.inc`를 끌어오므로
# 직접 줄 이유가 없고, 직접 주면 컴파일이 실패해 `ok=False`가 된다 —
# **일어나지 않은 실패가 신호로 기록된다.**
_COMPILE_SUFFIXES = (".c", ".cc", ".cpp")

# `_COMPILE_SUFFIXES` 중 C++인 것. 나머지가 C다.
#
# **이 목록은 `_COMPILE_SUFFIXES`의 부분집합이어야 한다.** 여기 있는데 저기 없으면 그
# 확장자는 애초에 컴파일 대상이 아니라 이 분류가 한 번도 쓰이지 않고, 저기 있는데 여기
# 판정에서 빠지면 C++ 파일이 C 방언을 받아 clang이 거부한다 — 2026-08-25 실측
# (`run_20260825_0001`)의 `invalid argument '-std=gnu17' not allowed with 'C++'` 5건이
# 정확히 그것이다.
_CXX_SUFFIXES = (".cc", ".cpp")

# 인덱서(`index`)에만 넘길 확장자. tree-sitter는 **전처리를 하지 않아서** 갈라진다 —
# `clang -fsyntax-only`를 쓰던 시절에는 전처리가 `#include`를 펼쳐 `.inc`를 자동으로
# 끌어왔지만 지금은 직접 주지 않으면 28바이트짜리 `#include` 한 줄만 보고 그 뒤의
# 17KB를 못 본다. 그리고 **에러가 아니므로 "그 파일에 함수가 없다"와 구별되지 않는다.**
#
# **여기 있는 확장자는 전부 `tools/harness-tools/indexer.py`의 `_GRAMMARS`에도 있어야
# 한다**(반대는 아니다 — `_GRAMMARS`는 아직 안 보내는 `.h`·`.hpp`도 안다). 그 스크립트는
# 이미지 안에서 독립 실행되고 `src/`를 import 하지 않아 목록이 양쪽에 따로 존재한다 —
# **어긋나면 예외가 아니라 `skipped`에 이름만 남고 조용히 빈 그래프가 나온다.**
#
# `.h`는 아직 없다. 헤더의 `static inline`이 심볼에서 빠지는 것은 별개의 알려진
# 구멍이고(모듈 docstring), 이 목록과는 다른 판단이 필요하다.
_INDEX_SUFFIXES = (*_COMPILE_SUFFIXES, ".inc")

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

# semgrep이 예외로 죽었는지 보는 표지. `--json`이 stdout만 바꾸므로 크래시 흔적은
# 여전히 stderr에 남는다(이번 진단을 가능하게 한 것도 그 stderr다).
_TRACEBACK_MARKER = "Traceback (most recent call last)"

# clang-tidy 출력에서 "경고 발견"과 "컴파일 실패"를 가르는 유일한 표지.
_COMPILE_ERROR_MARKER = "clang-diagnostic-error"

# semgrep이 정상 종료로 쓰는 코드. 0=발견 없음, 1=발견(--error). 나머지는 도구 자체의 실패다.
_SEMGREP_OK = (0, 1)

# 컨테이너가 명령을 띄우지도 못했을 때 런타임이 쓰는 코드. 크래시(=신호)와 구분해야 한다.
_EXEC_FAILED = (125, 126, 127)

# 실행 흔적을 만드는 도구. 마지막 명령이 "실제로 뜬 프로그램"이다.
_RUNTIME_TOOLS = frozenset({"asan", "poc"})

# 타임아웃에 쓸 코드. 128+SIGKILL 관례를 따른다.
_TIMEOUT_EXIT = 124

# 화이트리스트 밖 도구의 로그가 들어갈 자리. 도구 이름을 그대로 경로에 잇지 않는다.
_REJECTED_SLOT = "rejected"

# 실행된 명령을 알리는 콜백. 오케스트레이터를 import 하지 않고 trace를 남기기 위한
# 구멍이다(fake_retriever.CacheHook과 같은 패턴).
CommandHook = Callable[[str, list[str]], None]


@dataclass(frozen=True)
class Command:
    """컨테이너 안에서 돌 명령 하나. `cwd`는 컨테이너 기준 절대경로다."""

    argv: list[str]
    cwd: str


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
    """`harness-tools:v1` → `{sif_dir}/harness-tools_v1.sif`."""
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


def plan(tool: str, *, target: str, compile_sources: list[str], cwd: str) -> list[Command]:
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
                    *sources_, "--", _STD_ARGS[language], *_include_args(cwd),
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
        return [
            Command(
                [
                    "python3", _INDEXER, "--out", f"{SCRATCH}/{INDEX_FILE}",
                    "--root", MOUNT, *compile_sources,
                ],
                cwd=cwd,
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
                    _STD_ARGS[language], *_include_args(cwd), *sources_,
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
                    "-o", binary, *sources_,
                ],
                cwd=SCRATCH,
            ),
            Command([binary], cwd=SCRATCH),
        ]
    if tool == "poc":
        # PoC 실행 파일을 만드는 주체가 아직 없다(STATUS.md §5 PoV). 없는 것을 성공으로
        # 세지 않는다 — 그러면 E4가 실행되지 않은 프로그램 위에서 성립한다.
        return [Command([f"{SCRATCH}/poc"], cwd=SCRATCH)]
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
) -> list[str]:
    """런타임별 실행 명령. 이 함수가 순수해야 컨테이너 없이 조립을 검사할 수 있다.

    `env`는 `ToolArgs.env`다. 조용히 버리지 않는다 — 설정에 있는데 아무도 읽지 않는 값이
    이 저장소가 이미 한 번 저지른 결함이다(`factory._Wiring` 주석). 지금 채우는 노드는
    없지만, 채웠는데 무시되는 것보다 전달되는 편이 낫다. 정렬해서 넘긴다(재현성).
    """
    pairs = [f"{key}={value}" for key, value in sorted((env or {}).items())]
    if runtime in ("docker", "podman"):
        # podman은 docker CLI를 그대로 흉내 내므로 플래그가 같다 — 바이너리 이름만 갈린다.
        # ATOM은 SELinux가 비활성이라 볼륨 `:Z` 라벨이 필요 없다(2026-08-17 실측). 켜진
        # 장비가 생기면 그때 `:Z`를 붙인다 — 지금 붙이면 없는 문제에 대비해 소스를 relabel한다.
        argv = [runtime, "run", "--rm", "--name", name]
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
            "-w", command.cwd,
            sandbox.image,
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
        "--pwd", command.cwd,
        str(sif_path(sif_dir, sandbox.image)),
        *command.argv,
    ]
    return argv


def judge(tool: str, steps: list[Step], total: int) -> tuple[bool, int, str | None]:
    """실행 결과 → (ok, exit_code, error). `ok`와 `exit_code`의 의미를 섞지 않는다.

    - 도구가 죽음/실행 불가: `ok=False`
    - 돌았는데 아무것도 못 찾음: `ok=True, exit_code=0`
    - 돌아서 뭔가 찾음: `ok=True, exit_code!=0`

    이 관례가 깨지면 `with_signal()`이 benign 표본을 거르지 못하고 Evidence Gate E1이
    모든 Finding에서 참이 된다.

    **`exit_code`는 프로세스가 반환한 값을 그대로 남긴다.** 합성하면 기록에 작은 거짓이
    들어가고, 합성할 필요도 없다 — `with_signal()`이 `ok`를 먼저 보므로 크래시는
    `ok=False`에서 이미 걸린다.
    """
    if not steps:
        return False, 127, f"{tool}: 실행된 명령이 없다"
    last = steps[-1]
    code = last.exit_code
    if tool in _RUNTIME_TOOLS:
        if len(steps) < total:
            return False, code, f"{tool}: 빌드 단계에서 끊겨 프로그램이 실행되지 않았다"
        if code in _EXEC_FAILED:
            return False, code, f"{tool}: 실행 파일을 띄우지 못했다 (exit={code})"
        # 여기까지 오면 프로그램이 실제로 떴다. 크래시(non-zero)는 실패가 아니라 신호다.
        return True, code, None
    if tool == "semgrep":
        return judge_semgrep(last)
    if tool == "clang-tidy":
        # **명령을 띄우지도 못한 것은 신호가 아니다.** `_RUNTIME_TOOLS`가 위에서 하는 판정과
        # 같은 것이고, 그 상수의 주석이 이미 "크래시(=신호)와 구분해야 한다"고 적어 뒀다.
        # 여기에 없던 동안 **컨테이너 기동 실패가 정적 신호로 집계됐다** — 2026-08-28
        # `run_20260828_0001`에서 keyring 쿼터로 docker가 컨테이너를 못 띄워 도구 13건이
        # 전부 exit 125였는데, semgrep·build는 `ok=False`로 떨어진 반면 clang-tidy만
        # `ok=True`로 올라가 `signal_artifacts: 3`이 만들어졌다.
        if code in _EXEC_FAILED:
            return False, code, f"clang-tidy: 명령을 띄우지 못했다 (exit={code})"
        # 진단은 stdout으로 나온다("N warnings generated."만 stderr다).
        if _COMPILE_ERROR_MARKER in last.stdout:
            # exit code로는 "경고 발견"과 구분되지 않는다(모듈 docstring, #61).
            return False, code, "clang-tidy: 대상이 컴파일되지 않는다"
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
    if code == 0:
        return True, 0, None
    return False, code, f"{tool} 실패 (exit={code})"


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
    """로그 머리에 붙일 한 줄. `--json`으로 잃은 가독성을 여기서 되돌린다."""
    if tool != "semgrep" or not steps:
        return None
    results = semgrep_results(steps[-1].stdout)
    if results is None:
        return "# semgrep: 결과 문서 없음 — 아래 stderr 참조"
    return f"# semgrep: {len(results)} findings"


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
        self.image = image
        self.sif_dir = Path(
            sif_dir or os.environ.get("HARNESS_SIF_DIR") or self.artifacts_root / "sif"
        )
        # 시각을 주입받는다 — 재현 실행에서 같은 값이 나와야 trace를 대조할 수 있다.
        self.observed_at = observed_at or datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        self.on_command = on_command

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
            sif = sif_path(self.sif_dir, sandbox.image)
            if not sif.exists():
                # 자동으로 pull 하지 않는다 — 실행 중 네트워크는 network=False와 충돌한다.
                return self._fail(
                    evidence_id,
                    tool,
                    kind,
                    args,
                    127,
                    f"SIF가 없다: {sif} (변환 절차는 tools/README.md)",
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
        """
        digest = self._image_digest()
        return {self.image: digest} if digest else {}

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
        suffixes = _INDEX_SUFFIXES if tool == INDEX_TOOL else _COMPILE_SUFFIXES
        return plan(
            tool,
            target=_in_container(self.root, resolved),
            compile_sources=[
                _in_container(self.root, path)
                for path in sources(resolved)
                if path.suffix in suffixes
            ],
            cwd=_in_container(self.root, resolved if resolved.is_dir() else resolved.parent),
        )

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
        sections = [f"# node argv: {' '.join(args.argv)}", f"# runtime: {self.runtime}"]
        steps: list[Step] = []
        for index, command in enumerate(commands):
            name = f"harness-{self.run_id}-{evidence_id}-{index}"
            argv = container_argv(
                self.runtime,
                sandbox=sandbox,
                root=self.root,
                scratch=scratch,
                command=command,
                name=name,
                sif_dir=self.sif_dir,
                env=args.env,
            )
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
        summary = summary_line(tool, steps)
        if summary is not None:
            # 머리에 넣는다 — JSON 본문 앞에 결론이 있어야 사람이 로그를 읽는다.
            sections.insert(2, summary)
        return self._write(
            evidence_id, tool, kind, args, sections, ok=ok, exit_code=exit_code, error=error
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

    def _image_digest(self) -> str | None:
        if self.runtime == "singularity":
            sif = sif_path(self.sif_dir, self.image)
            return f"sha256:{_file_sha256(sif)}" if sif.exists() else None
        # RepoDigests는 **docker에서는 push된 적 없는 로컬 빌드 이미지에 비어 있다.** 그
        # 경우 .Id를 쓴다 — 둘 다 그 이미지를 유일하게 지시하는 sha256이다. podman은 로컬
        # 빌드에도 RepoDigests를 채우지만(2026-08-17 실측) 같은 포맷이라 이 경로가 그대로 된다.
        out = _command_output(
            [
                self.runtime, "image", "inspect", self.image,
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
        """실행 전에 거절된 경우. 프로세스를 띄우지 않는다."""
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
        exit_code: int,
        error: str | None,
    ) -> Evidence:
        """로그를 실제로 쓰고 그 내용의 sha256을 digest로 삼는다.

        디렉터리 이름은 **화이트리스트 안의 이름만** 쓴다. `tool`은 검증되지 않은 문자열이라
        (`ctx.call_tool(tool=...)`은 지금 상수만 넘기지만 스키마가 강제하지는 않는다)
        그대로 경로에 이으면 `../`가 run 디렉터리 밖에 파일을 만든다 — `src/paths.py`가
        `workdir`에 대해 막는 것과 같은 종류의 구멍이다.
        """
        body = "\n".join(sections) + "\n"
        slot = tool if tool in TOOL_KINDS else _REJECTED_SLOT
        path = self.artifacts_root / self.run_id / slot / f"{evidence_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return Evidence(
            evidence_id=evidence_id,
            run_id=self.run_id,
            kind=kind,
            source=tool,
            artifact_id=args.workdir,
            payload_ref=f"artifacts/{self.run_id}/{slot}/{evidence_id}.log",
            digest=f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}",
            observed_at=self.observed_at,
            ok=ok,
            exit_code=exit_code,
            error=error,
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

    **세 도구(build·clang-tidy·asan)가 같은 값을 받아야 한다.** `_STD_ARGS` 주석이 방언에
    대해 적어 둔 이유가 그대로 걸린다 — 갈리면 같은 파일에 대한 두 진단이 어긋나고, 그
    차이가 코드의 성질인지 우리 플래그의 산물인지 구분할 수 없다.

    artifact가 대상 루트 자체면 둘이 같으므로 하나로 줄인다(중복 인자를 남기지 않는다 —
    로그가 곧 `digest`라 무의미한 차이도 기록에 남는다).
    """
    directories = [cwd] if cwd == MOUNT else [cwd, MOUNT]
    return [flag for directory in directories for flag in ("-I", directory)]


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
        groups["c++" if source.endswith(_CXX_SUFFIXES) else "c"].append(source)
    return [(language, sources_) for language, sources_ in groups.items() if sources_]


def _in_container(root: Path, path: Path) -> str:
    """호스트 경로 → 컨테이너 안 경로. 마운트 밖은 `safe_join`이 이미 걸렀다."""
    relative = path.resolve().relative_to(Path(root).resolve()).as_posix()
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
