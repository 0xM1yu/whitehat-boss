"""1단계 인덱서 산출물(`index.json`)을 읽어 캡슐의 그래프 섹션으로 바꾼다 (#63).

`tools/harness-tools/indexer.py`가 컨테이너 안에서 심볼 표와 호출 그래프를 내고,
`ContainerToolRunner`가 그것을 scratch에 떨어뜨린다. 여기가 **읽는 쪽**이다.

**`FakeRetriever`와 `HybridRetriever`가 이 모듈을 함께 쓴다.** 두 구현이 같은 index.json
에서 다른 섹션을 내면 `dev_fake`와 `dev_docker`의 결과를 비교할 수 없다 —
`rules/harness-c.yaml` 첫 주석이 정적 룰셋에 대해 편 논법과 같다. 그래서 로딩·필터·
토큰 계산이 전부 여기 한 곳에 있고, 두 retriever는 `names_for()`와 `fit_names()`를 부른다
(Hybrid는 후보 풀 순서에 `neighbor_files()`도 부른다 — #336).

## "없다"와 "0건"을 섞지 않는다

`signals.py`의 ok vs exit_code, 인덱서의 `skipped`·`unresolved_*`와 같은 원칙이다.
조용히 빈 섹션을 내면 **"그래프가 비었다"와 "인덱서가 안 돌았다"가 구별되지 않는다.**

| 상태 | `names_for()` | 캡슐에 나타나는 모양 |
| --- | --- | --- |
| index.json 없음 (`dev_hybrid`·`dev_fake`) | `None` | 섹션 **부재** |
| 버전 모름 / JSON 깨짐 | `None` | 섹션 **부재** + trace에 사유 |
| 있고, 간선 0개 | `[]` | `tokens=0, items=0` |
| 있고, 간선 N개 | `[...]` | `tokens=실측, items=k` |

**네 번째 갈래가 하나 더 있고 그것은 구별되지 않는다**: index.json이 멀쩡한데
`target_function`이 레벨 예산을 통째로 먹으면 그래프 섹션이 발행되지 못해 1행과 같은
모양이 된다. 실측 임계는 focused(8,000토큰) 32KB / expanded(24,000토큰) 96KB다
(`src/tokens.CHARS_PER_TOKEN`=4 기준).

**W2가 그 갈래를 좁혔다. 없애지는 못했다.** 심볼 단위 target에서는 `target_function`이
함수 하나라 저 임계에 닿는 일이 거의 없다. 그러나 인덱스가 없는 프로필(`dev_fake`)은
여전히 파일 단위 target으로 되돌아가므로 그쪽에서는 옛 갈래가 그대로 산다 —
`tests/test_index_graph.py`가 그 동작을 알려진 한계로 박제해 둔 이유다.

## 심볼 목록도 여기서 낸다

`symbols_for()`가 러너의 후보 목록을 만든다(W2). 후보 단위가 파일에서 심볼로 바뀌는
배선의 데이터원이 캡슐의 그래프 섹션과 **같은 index.json**이라는 것이 요점이다 —
따로 파싱하면 후보의 근사와 그래프의 근사가 갈리고, 그 차이는 어느 기록에도 안 남는다.

## 실패해도 예외를 던지지 않는다

모르는 버전이나 깨진 JSON 때문에 run 전체가 죽으면 도구 불일치가 파이프라인 장애가
된다. `container_tools`·`fake_tools`가 "실패해도 예외 대신 `ok=False`를 돌려준다"로
정한 것과 같은 판단이다. 대신 사유를 **센다** — `IndexSummary.status`가 훅으로 올라가
trace의 `graph_index` 이벤트가 된다.

## 이 그래프가 못 잡는 것

인덱서 자신의 근사(함수 포인터 호출·매크로·`#if` 양쪽 가지)는 그쪽 docstring에 있고
`unresolved_calls`·`unresolved_symbols`가 센다. **읽는 쪽이 추가로 만드는 오차가 하나
있다**: 인덱서가 파일 스코프를 구분하지 않으므로(`static` 동명 함수) 다른 파일의 같은
이름 함수를 부르는 호출이 이 파일의 `callers`로 잘못 붙을 수 있다. 이것은
`unresolved_*`가 세지 않는 종류의 오차라 여기 적어 둔다.

## 여기서 만든 줄이 그대로 프롬프트에 실린다

`render_entry()`가 만든 문자열이 `CapsuleSection.body`에 담겨 `scout.describe_sections()`의
산문과 `prompt_loader.build_facts()`의 사실 블록 양쪽으로 간다. 그래서 이 모듈은 토큰
회계뿐 아니라 **모델이 실제로 보는 문맥**을 만든다.

토큰 수는 처음부터 그 문자열의 길이에서 나왔으므로(`src/tokens.estimate_tokens`) 본문을
싣는다고 숫자가 달라지지 않았다. `CapsuleSection`이 그 둘의 일치를 검증기로 강제한다 —
줄을 버리면서 토큰만 남기는 경로는 이제 스키마에서 죽는다.

줄의 형식 자체(`render_entry()`)와 그것을 되읽는 `name_of()`는 `src/graph_entry.py`에
**나란히** 있다. 내용상 여기 있어야 하지만 되읽는 쪽이 `orchestrator/nodes/auditor.py`라
층 규칙에 걸린다 — 사유는 그 모듈 docstring에 적었다.
"""

import json
import posixpath
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from operator import itemgetter
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.graph_entry import render_entry
from src.schemas.capsule import CapsuleSection
from src.schemas.common import Location
from src.tokens import estimate_tokens

# 슬라이스의 줄 집합 — (file, line). 슬라이스 타깃 캡슐의 「본문」이다(`GraphIndex.names_for`).
SliceBody = frozenset[tuple[str, int]]

# `indexer.py`의 `INDEX_VERSION`이 이 안에 있어야 한다.
#
# 그 스크립트는 이미지 안에서 살고 `src/`를 import 하지 않아 **형식이 양쪽에 따로
# 존재한다**(`container_tools._INDEX_SUFFIXES` ↔ `indexer._GRAMMARS`와 같은 이중화).
# 어긋나면 예외가 아니라 `unsupported_version`이 되고 그래프 섹션이 통째로 사라지므로,
# `tests/test_indexer.py`가 두 값을 대조해 그 어긋남을 막는다. v1·v2·v3도 계속 읽는다 — 그 이미지로
# 돈 run의 산출물이 남아 있고, 캡슐 그래프는 네 형식에서 같은 간선을 낸다(`_call_edges`).
# v3는 v2 표를 그대로 두고 `V3_TABLES`를 더한 것이다(#304). v4는 v3 위에 `V4_TABLES`를 더하고
# `types`의 앞선 선언 typedef 행에 `forward`를 붙인 것이다(`indexer.py`의 index-v4 절).
SUPPORTED_INDEX_VERSIONS = ("index-v1", "index-v2", "index-v3", "index-v4")
_INDEX_V1 = "index-v1"
_INDEX_V3 = "index-v3"
_INDEX_V4 = "index-v4"

# index-v2의 조회 표 — NE resolver(#63 후속)가 `GraphIndex.table()`로 읽는다. 모양은
# `indexer.py` 모듈 docstring의 표다. **캡슐 섹션이 아니다**(`GRAPH_SECTION_KINDS` 주석).
V2_TABLES = (
    "functions",
    "macros",
    "types",
    "file_vars",
    "fields",
    "locals",
    "assigns",
    "field_assigns",
    "calls",
)
# index-v3가 더한 표(#304) — 쓰기 지점(`writes`)·함수 포인터 바인딩(`fn_bindings`)·필드 경유
# 간접 호출(`field_calls`). v2 문서에서는 `table()`이 `None`을 낸다(「없다」 — 「0건」이 아니다).
V3_TABLES = ("writes", "fn_bindings", "field_calls")
# index-v4가 더한 표 — 파일별 `#include` 지시문(`includes` — 키가 이름이 아니라 **파일**이다)과 enum
# 상수(`enum_constants` — 범위는 그 enum 정의 전체). v3 이하 문서에서는 `table()`이 `None`을 낸다.
V4_TABLES = ("includes", "enum_constants")
# Auditor `SOURCE`가 정의 줄을 붙이는 표(#349 (f) — `definitions_of`). 이 순서로 보고 처음
# 걸린 표에서 정한다. `enum_constants`는 v4 문서에만 있다 — 없는 표는 건너뛴다.
DEFINITION_TABLES = ("macros", "types", "file_vars", "enum_constants")
# `definitions_of`의 include 도달성(v4 `includes`)이 앵커 파일에서 따라가는 깊이와 파일 수 상한.
# **정한 값이지 잰 값이 아니다.** 상한에 걸려 잘린 닫힘에서는 닿는 정의가 하나뿐으로 보여도 상한
# 밖에 둘째가 있을 수 있다 — 그 경우를 세지는 않는다.
_INCLUDE_DEPTH = 8
_INCLUDE_FILES = 2000

# 정의 조회가 「저장소에서 하나뿐」 판정에서 제품 경로 뒤로 미루는 디렉터리 이름
# (`demote_nonproduct` — `definitions_of`·`callees_of`·`call_sites_of`·`functions_named`·
# `fn_pointer_targets`·`indirect_calls_of`). 두 갈래다: 테스트·예제·
# 문서·번들 라이브러리가 쓰는 흔한 이름(퍼징은 `_FUZZ_PREFIXES` 접두도), 그리고 **다른 플랫폼·빌드
# 시스템 몫의 사본**(`xcode/`·`windows/`…) — 리눅스 빌드를 대상으로 본다는 전제라 거기 있는 정의는
# 효력 있는 정의가 아니라고 본다. 뒤 갈래를 넣은 근거는 로컬 측정 1건이다: libzip 계열 저장소
# 하나에서 비제품 경로의 정의가 제품 함수로 586건 들어왔고 그중 527건이 `xcode/`, 51건이
# `regress/`였다(run ID 없음). `unittest(s)`·`testsuite`는 FT Level 1 Auditor 입력
# 270표본(2026-10-03 로컬, run ID 없음)의 잡음 경로 집계에 나온 이름이다. 같은 집계의
# `ios`·`android`는 넣지 않았다 — 제품 포팅 계층일 수 있다. **목록은 정한 값이지 잰 값이 아니다.**
_NONPRODUCT_DIRS = frozenset(
    {
        "test", "tests", "testing", "unittest", "unittests", "testsuite", "regress",
        "example", "examples", "sample", "samples", "demo", "demos", "contrib", "doc", "docs",
        "third_party", "third-party", "thirdparty", "vendor",
        "fuzz", "fuzzer", "fuzzers", "fuzzing", "oss-fuzz",
        "xcode", "windows", "win32", "win64", "msvc",
    }
)
# 퍼징 하네스 디렉터리의 접두(`fuzz_targets/`·`fuzz-corpus/`). 맨 `fuzz` 접두는 `src/fuzzy/`(제품
# 퍼지 매칭)까지 먹어서 구분자까지 본다.
_FUZZ_PREFIXES = ("fuzz_", "fuzz-")


def is_nonproduct_path(path: str) -> bool:
    """`path`의 디렉터리 성분(파일 이름은 안 본다 — `lib/test_util.c`는 제품이다)에 비제품 이름이
    있는가. 대소문자를 접는다."""
    return any(
        part in _NONPRODUCT_DIRS or part.startswith(_FUZZ_PREFIXES)
        for part in path.lower().split("/")[:-1]
    )


def _product_first(items: list, file_of: Callable[[Any], str]) -> list:
    """제품 경로 항목이 있으면 그것만, 없으면 전부. 비제품 정의는 **제품 정의가 없을 때만** 후보로
    남는다 — 강등이지 삭제가 아니다."""
    product = [item for item in items if not is_nonproduct_path(file_of(item))]
    return product or list(items)


# 이름 → 기록 목록 모양인 표의 기록마다 있어야 하는 키. `locals`·`assigns`는
# 파일 → 함수 → 이름 → [줄]이라 따로 본다(`_v2_tables`).
_RECORD_KEYS = {
    "functions": ("file", "line_start", "line_end", "params"),
    "macros": ("file", "line_start", "line_end"),
    "types": ("file", "line_start", "line_end", "kind"),
    "file_vars": ("file", "line_start", "line_end"),
    "fields": ("type", "file", "line_start", "line_end"),
    "field_assigns": ("file", "line", "func", "lhs", "rhs", "op"),
    "calls": ("file", "line", "caller", "args"),
    "writes": ("file", "line", "func", "lhs", "rhs", "op", "form", "base"),
    "fn_bindings": ("file", "line", "func", "fn", "form"),
    "field_calls": ("file", "line", "caller", "args", "expr"),
    # v4 — `includes`는 파일 → 지시문 목록이지만 모양(키 → 기록 목록)이 같아 같은 검사를 탄다.
    "includes": ("line", "path", "system"),
    "enum_constants": ("file", "line_start", "line_end", "enum"),
}
# 위 키 중 줄 번호 — 정수여야 한다(`_v2_tables`).
_LINE_KEYS = frozenset({"line", "line_start", "line_end"})

# 이 모듈이 채우는 섹션. `types`는 없다 — index-v1에는 타입 데이터가 없고, index-v2에는
# 있지만(`types` 표) **캡슐로 싣지 않는다.** 표는 NE resolver가 모델이 요청했을 때 준다 —
# 캡슐마다 실으면 Scout 프롬프트가 늘 커지는데, 그 프롬프트가 서빙 창을 넘는 결함이 열려 있다
# (#63 후속 · `tests/test_index_graph.py`의 v2 절이 캡슐이 v1과 같음을 박는다).
GRAPH_SECTION_KINDS = ("callers", "callees")

# `IndexSummary.status` 값. "못 읽었다"의 사유를 하나로 뭉치지 않는다 — 인덱서가 안 돈
# 것(missing)과 형식이 어긋난 것(unsupported_version)은 대응이 다르다.
STATUS_MISSING = "missing"
STATUS_UNSUPPORTED = "unsupported_version"
STATUS_MALFORMED = "malformed"
STATUS_LOADED = "loaded"

# 로드 결과를 오케스트레이터에 알리는 훅. 어댑터는 오케스트레이터를 import 하지 않으므로
# 콜백으로만 알린다(engineering-guide.md §3, `on_cache`·`on_usage`와 같은 모양).
IndexHook = Callable[["IndexSummary"], None]


@dataclass(frozen=True)
class IndexSummary:
    """로드 결과와 인덱서가 놓친 것의 수.

    **`unresolved_*`를 캡슐에 싣지 않고 여기에 둔다.** 캡슐은 대상 하나당 토큰
    매니페스트인데(풀에서는 멤버 셋이 그 하나를 공유한다 — ADR 0010 §3) 이 값들은
    저장소 전체의 스칼라라 대상마다 달라지지 않고 토큰 비용도 없다. 섹션으로 실으면
    텍스트가 아닌 숫자가 토큰 회계에 들어간다.

    대신 trace에 남긴다 — "그 저장소에서 E2를 얼마나 믿을지"는 지표 해석의 입력이고,
    trace가 지표의 원본 데이터다(trace.py 모듈 docstring).
    """

    status: str
    files: int = 0
    skipped: int = 0
    symbols: int = 0
    calls: int = 0
    unresolved_calls: int = 0
    unresolved_symbols: int = 0
    parse_errors: int = 0

    def as_trace_fields(self) -> dict[str, str | int]:
        """`graph_index` 이벤트의 `data`. **필드 이름이 곧 지표의 열 이름이다.**

        오케스트레이터가 이 dataclass를 import 하면 "노드는 구체 어댑터를 모른다"가
        깨진다(`src/orchestrator/`·`src/interfaces/`에 어댑터 import가 0건이다). 그래서
        타입이 아니라 **스칼라 dict**만 경계를 넘고, 받는 쪽은 이 메서드 하나만 아는
        구조적 타입으로 받는다(`runner.IndexSummaryLike`).

        `asdict`를 쓰므로 필드를 추가하면 trace에도 자동으로 실린다. 이름을 바꾸면
        지표의 열 이름이 바뀌므로 `tests/test_retrieval.py`가 필드 집합을 고정한다.
        """
        return asdict(self)


class GraphIndex:
    """`index.json` 지연 로더.

    **생성 시점에 읽지 않는다.** `build_deps()`는 파이프라인 1단계(Index)보다 먼저 돌아
    그때는 파일이 아직 없다. 3단계(Retrieval)가 1단계 뒤라는 것만이 순서 보장이므로
    (`runner._analyze`) 최초 조회에서 읽는다.
    """

    def __init__(self, path: Path | None, *, on_index: IndexHook | None = None) -> None:
        self.path = path
        self.on_index = on_index
        self._summary: IndexSummary | None = None
        # file → 그 파일에 정의된 심볼 (이름, line_start, line_end). 간선의 반대쪽 끝
        # 판정과 `symbols_for()`가 함께 쓴다.
        #
        # **줄 범위를 버리지 않는 것이 W2의 전부다.** index-v1이 심볼마다 이 둘을 이미
        # 내는데(`indexer.index_file()`) 예전 로더는 이름만 남기고 버렸고, 그래서 후보
        # 위치가 파일뿐이라 `read_symbol_source()`가 파일 전체를 프롬프트에 실었다.
        self._defined: dict[str, list[tuple[str, int, int]]] = {}
        # 호출 간선 원본. (caller, callee, file, line)
        #
        # `file`은 **호출자가 정의된 파일**, `line`은 **호출 지점 줄**이다
        # (`indexer._walk_calls`). 그래서 (file, line)이 곧 호출 지점이고, 그것이 본문
        # 줄 범위 안인지로 "이미 캡슐에 있는가"를 판정할 수 있다.
        self._calls: list[tuple[str, str, str, int]] = []
        # index-v2 조회 표(읽기 전용). v1이거나 못 읽었으면 `None`이다(`table()`).
        self._tables: dict[str, Mapping[str, Any]] | None = None
        # 인덱싱한 파일(`files`) — v4 include 경로를 저장소 파일로 풀 때 쓴다(`_resolve_include`).
        self._files: frozenset[str] = frozenset()
        # 지연 캐시 — 파일 이름 → 그 이름의 파일들, (포함 디렉터리, 경로, 꺾쇠) → 푼 파일, 앵커 →
        # include 닫힘. 조회마다 다시 만들면 정의 조회 하나가 저장소 전체를 훑는다.
        self._by_base: dict[str, list[str]] | None = None
        self._resolved: dict[tuple[str, str, bool], tuple[str, ...]] = {}
        self._closures: dict[str, frozenset[str]] = {}

    # --- 조회 -----------------------------------------------------------

    def symbols_for(self, file: str) -> list[Location] | None:
        """그 파일에 정의된 심볼의 위치. `None`은 **그래프 자체가 없다**는 뜻이다.

        러너가 후보 목록을 만드는 데 쓴다(W2). 세 갈래를 호출자가 그대로 기록에 옮긴다:

        | 반환 | 뜻 | 러너가 하는 일 |
        | --- | --- | --- |
        | `None` | 인덱스 없음·버전 모름·깨짐 | 파일 단위 target으로 되돌아간다 |
        | `[]` | 인덱스는 있는데 이 파일에 심볼 0건 | 후보를 만들지 않는다 |
        | `[...]` | 심볼 N건 | 심볼당 target 하나 |

        `names_for()`가 "없다"와 "0건"을 가르는 것과 **같은 구별**이고 같은 이유다 —
        `.py`처럼 인덱서에 넘어가지 않는 파일과 인덱서가 아예 안 돈 run을 한 값으로
        뭉치면 후보가 사라진 이유를 사후에 알 수 없다.

        정렬은 index.json의 순서(file, line_start, name)를 그대로 쓴다. 후보 순서가 곧
        ID 발급 순서라 흔들리면 두 run의 trace를 대조할 수 없다(engineering-guide.md §5).
        """
        if self._load().status != STATUS_LOADED:
            return None
        return [
            Location(file=file, symbol=name, line_start=start, line_end=end)
            for name, start, end in self._defined.get(file, [])
        ]

    def names_for(
        self, kind: str, target: Location, body: SliceBody | None = None
    ) -> list[str] | None:
        """`kind` 섹션에 실을 항목. `None`은 **그래프 자체가 없다**는 뜻이다.

        빈 리스트(`[]`)와 섞지 않는다 — 그쪽은 "봤는데 간선이 0건"이다. 호출자는 이
        구별을 그대로 캡슐에 옮긴다(모듈 docstring 표).

        ## 규칙은 둘이고, 둘 다 "본문"을 기준으로 한다

        여기서 **본문**은 `read_symbol_source(root, target)`이 돌려주는 범위다. 그것이
        `target_function` 섹션이자 프롬프트에 실리는 원문이므로, 두 판정이 그 정의를
        공유해야 캡슐이 자기 내용과 어긋나지 않는다.

        1. **선택** — 무엇이 이 캡슐의 간선인가. `callers`는 본문으로 들어오는 호출,
           `callees`는 본문에서 나가는 호출.
        2. **제외** — 간선의 반대쪽 끝이 **본문 안**이면 뺀다. 이미 원문에 있으니 또
           적으면 토큰만 부푼다.

        ## 판정이 "파일이 같은가"에서 "본문 줄 범위 안인가"로 바뀌었다 (W2)

        예전 필터는 **target이 파일 전체라는 전제** 위에 있었다 — `line_start`가 없어
        `read_symbol_source()`가 파일을 통째로 돌려주던 시절이다. 심볼 단위 target에서는
        같은 파일의 형제 함수가 더는 본문에 없으므로 그 필터를 그대로 두면 **실재하는
        간선을 숨긴다.**

        줄 범위 판정은 두 target 종류에 모두 맞는다: `line_start`가 없으면 `_in_body()`가
        파일 전체를 본문으로 보므로 **폴백 프로필(`dev_fake`)의 캡슐은 한 글자도 바뀌지
        않는다.** 그것이 이 판정을 고른 이유다 — 경로를 둘로 나누면 두 프로필이 다른
        코드를 타고, 이 모듈이 없애려던 것이 정확히 그 갈라짐이다.

        `symbols`에 아예 없는 이름(`memcpy` 같은 외부 함수)은 남긴다 — 본문 안에 없는
        것이 확실하기 때문이다.

        ## 슬라이스 타깃의 본문은 슬라이스다 (#220 ①)

        `run --slices`면 LIGHT `target_function`과 Scout `SOURCE`가 슬라이스 렌더다. 위 등식을
        지키는 본문은 `target`(entry 줄 하나)의 범위가 아니라 **슬라이스의 (file, line) 집합**
        (`body`)이다 — entry만 보면 Case B(sink 줄)는 감싸는 함수의 호출자가, Case A(함수 머리)는
        본문의 호출이 빠졌다. 「본문 안에 정의」는 정의 **범위**가 그 집합과 겹치는가로 본다 —
        시작 줄로 보면 머리 줄을 안 품은 슬라이스(K&R 머리 · 손으로 만든 줄 집합)에서 callers가
        조용히 빈다. 실 슬라이서는 본문 줄이 뽑히면 함수 머리를 통째로 넣어(`slicer_source.py`의
        `recover_lines`) 대개 둘이 같지만 그 보장에 기대지 않는다. 집합은 파일을 가로질러도 된다
        (군집 슬라이스) — 양 끝이 다 보이는 간선은 어느 섹션에도 없다.

        ponytail: 슬라이스가 걸친 **모든** 함수의 외부 호출자를 싣는다 — 널리 불리는 헬퍼 본문을
        지나는 슬라이스면 그 호출자들이 정렬 순서대로 예산을 먼저 먹을 수 있다. 「뿌리 함수의
        호출자만」으로 좁히면 Case B의 sink 함수(슬라이스 안에서 불린다)의 호출자가 빠져서 좁히지
        않았다 — 실 `slices.json`에서 섹션 줄 수를 재고 정한다.
        """
        if kind not in GRAPH_SECTION_KINDS:
            raise ValueError(f"그래프 섹션이 아니다: {kind}")
        if self._load().status != STATUS_LOADED:
            return None

        in_body, defined_in_body = self._rules(target, body)
        if kind == "callers":
            rows = [
                (caller, row_file, line)
                for caller, callee, row_file, line in self._calls
                # 선택: 본문 안에 정의된 심볼로 들어오는 호출.
                if defined_in_body(callee)
                # 제외: 호출 지점이 본문 안이면 원문에 이미 있다(자기 재귀·형제 호출).
                and not in_body(row_file, line)
            ]
        else:
            rows = [
                (callee, row_file, line)
                for _, callee, row_file, line in self._calls
                # 선택: 본문 안에서 나가는 호출.
                if in_body(row_file, line)
                # 제외: 대상 정의가 본문 안이면 원문에 이미 있다.
                and not defined_in_body(callee)
            ]
        # 정렬·중복 제거로 결정론을 지킨다. 같은 입력에 같은 캡슐이 나오지 않으면
        # engineering-guide.md §5의 재현성이 깨진다.
        return sorted({render_entry(name, row_file, line) for name, row_file, line in rows})

    def table(self, name: str) -> Mapping[str, Any] | None:
        """index-v2 조회 표 하나 — NE resolver가 읽는다. `None`은 **v2 표가 없다**는 뜻이다.

        | 반환 | 뜻 |
        | --- | --- |
        | `None` | 없음·버전 모름·깨짐, **index-v1**(표 없음), 그 판에 없는 표(v2의 V3·V4 등) |
        | `{}` | v2인데 그 표가 0건 |
        | `{...}` | 표. 모양은 `indexer.py` 모듈 docstring의 표다 |

        `names_for()`가 「없다」와 「0건」을 가르는 것과 **같은 구별**이다 — v1에서 `{}`를 내면
        「인덱스가 v2가 아니다」와 「찾았는데 없다」가 섞이고, resolver는 그 둘을 모델에게 다르게
        알려야 한다.

        **읽기 전용이다.** 표는 run 동안 모든 조회가 공유한다 — 고치면 다음 조회가 오염된다.
        **캡슐로 가지 않는다** — 이 메서드를 부르는 것은 resolver뿐이다(`GRAPH_SECTION_KINDS` 주석).
        표의 정의 **위치**는 `definitions_of`도 읽는다 — Auditor `SOURCE`가 정의 줄을 원문으로 싣는
        것이지(#349 (f)) 표가 캡슐 섹션이 되는 것은 아니다.
        """
        if name not in V2_TABLES + V3_TABLES + V4_TABLES:
            raise ValueError(f"조회 표가 아니다: {name}")
        self._load()
        return None if self._tables is None else self._tables.get(name)

    def neighbor_files(self, target: Location) -> list[str] | None:
        """대상의 호출 그래프 이웃이 정의된 파일들. `None`은 **그래프 자체가 없다**는 뜻이다.

        hybrid 검색이 후보 풀을 대상 근처부터 채우는 데 쓴다(#336). 두 갈래를 합친다:

        - callers -> 대상 심볼을 부르는 호출 지점의 파일(간선의 `file`은 호출자가 정의된 파일)
        - callees -> 대상 본문 안의 호출 지점이 부르는 이름이 정의된 파일

        대상 파일 자신은 뺀다(검색 쪽에서 따로 맨 앞에 둔다). 정렬해서 돌려준다(결정론).
        ⚠ 이름만으로 잇는다 — 같은 이름의 `static` 함수가 여러 파일에 있으면 그 파일이 다
        이웃이 된다(`_rules` docstring의 같은 한계). 후보 풀의 순서만 바꾸는 용도라 넓게 잡아도
        되지만, 이웃이 풀을 넘치게 많으면 같은 폴더 층이 굶는다 — 실 저장소 빈도는 안 잼.
        """
        if self._load().status != STATUS_LOADED:
            return None
        defined_in: dict[str, set[str]] = {}
        for file, symbols in self._defined.items():
            for name, _, _ in symbols:
                defined_in.setdefault(name, set()).add(file)
        files: set[str] = set()
        for _, callee, file, line in self._calls:
            if target.symbol and callee == target.symbol:
                files.add(file)
            if self._in_body(file, line, target):
                files |= defined_in.get(callee, set())
        files.discard(target.file)
        return sorted(files)

    def locals_of(self, file: str, func: str) -> frozenset[str] | None:
        """`file`의 함수 `func` 안에서 선언된 이름 — 지역 변수와 **파라미터**(`locals` 표).

        `None`은 v2 표가 없다는 뜻이다(인덱스 없음·v1·깨짐). 표에 없는 함수·파일은 **빈 집합**이다 —
        「없다」와 「0건」을 가르는 `definitions_of`와 같은 구별이다. Auditor `SOURCE`의 정의 조회가
        함수 본문의 이름 중 지역·파라미터를 빼는 데 쓴다 — 그 이름은 다른 파일의 매크로·타입·파일
        변수와 철자가 같아도 그것을 가리키지 않는다.

        표의 키는 **함수 이름**이다(`#if` 양쪽 가지의 같은 이름 함수는 한 항목으로 합쳐진다).
        ⚠ 이름만 본다 — `struct buffer *buffer`의 태그 `buffer`는 변수 `buffer`와 이름공간이 달라서
        지역이어도 정의를 가리킬 수 있다. 가르는 일은 읽는 쪽이 한다(`source_definitions`).
        """
        if self._load().status != STATUS_LOADED or self._tables is None:
            return None
        return frozenset(self._tables["locals"].get(file, {}).get(func, ()))

    def definitions_of(
        self, names: Iterable[str], near_file: str, *, demote_nonproduct: bool = False
    ) -> list[Location] | None:
        """이름들 중 매크로·타입(struct/union/enum/typedef)·파일 스코프 변수·enum 상수(v4)인 것의
        **정의 위치**.

        `None`은 v2 표가 없다는 뜻이다(인덱스 없음·v1). Auditor `SOURCE`가 실린 코드가 쓰는
        `#define`·구조체 정의를 붙이는 데 쓴다(#349 (f)) — 버퍼 크기·필드 배치를 모르면 경계 판단을
        못 한다. 정의를 고르는 기본 규칙은 `callees_of`와 같다(아래 접기·도달성은 여기만):

        - `near_file`에 정의가 있으면 그것, 없으면 저장소 전체에서 **하나뿐일 때만**
        - 여럿이면(`#if` 양쪽 가지 · 같은 이름 여러 파일) 싣지 않는다 — 이름만으로 못 가른다.
          **v4 문서는 한 번 더 본다**(아래 「include 도달성」)
        - 표는 `DEFINITION_TABLES` 순서로 보고 처음 걸린 표에서 정한다
        - enum 상수(v4 `enum_constants`)의 위치는 **그 enum 정의 전체**다 — 같은 enum의 상수 여럿을
          물으면 같은 위치가 여러 번 나오고, 겹치는 범위는 읽는 쪽(`runner`)이 한 번만 싣는다

        **같은 사실의 행은 하나로 센다**(`_definition_rows` — 판에 상관없다).
        `typedef struct Z {…} Z;`는 `types`에 범위가 같은 행 둘(struct · typedef)을 내는데 둘로 세면
        「하나뿐」이 영영 안 선다.
        v4는 `typedef struct X X;`(앞선 선언 — `forward`)도 **같은 파일에** 그 이름의 본문 행이
        있으면 뺀다. 다른 파일의 본문이나 별칭 이름이 다른 태그의 본문은 그 행을 못 대신하므로
        뺄 근거가 아니다 — 두 행이 남는다(index-v3와 같다). 그다음은 아래 규칙대로다: 앵커
        파일에 있는 행이 먼저고, 아니면 앵커의 include 닫힘에 든 행이 하나뿐일 때 그것이다.
        본문 파일이 앵커면 본문, 앵커가 typedef 헤더만 include하면 typedef 한 줄이고, **둘 다
        닿거나 둘 다 안 닿으면 안 붙는다.**

        **include 도달성(v4).** 후보가 여럿이고 `near_file`에 없으면, `near_file`이 `#include`로
        닿는 파일(`_include_closure` — 깊이 `_INCLUDE_DEPTH`·파일 `_INCLUDE_FILES`까지)의 후보만
        남긴다. **하나 남을 때만** 그것을 싣고, 0이거나 여럿이면 예전처럼 안 싣는다. `TRUE`·`size_t`
        처럼 여러 헤더가 정의하는 이름이 앵커가 실제로 include하는 헤더 하나로 좁혀진다. include
        경로는 파일 경로 접미로 푼다(`_resolve_include`) — `-I` 설정·매크로 경로·대상 트리 밖
        헤더는 모른다. v3 이하 문서(`includes` 표 없음)는 이 단계가 없다.

        순서는 (파일, 시작 줄)이다(결정론).

        `demote_nonproduct`(기본 꺼짐 — Auditor `SOURCE`만 켠다)면 「저장소에서 하나뿐」 판정에서
        비제품 경로(`is_nonproduct_path` — `tests/`·`examples/`·`third_party/` 등)의 정의가 한 단계
        낮다. **표를 가로질러 본다**: 그 이름의 제품 정의가 정의 표 어디에든, 또는 제품 **함수**
        (`_defined`)로 있으면 비제품 행은 정의 표 모두에서 후보가 아니다. 그다음 남은 행으로 위 규칙
        (표 순서 · 하나뿐)을 그대로 돈다 —
        - `DUP`이 `lib/`와 `tests/`의 매크로면 `lib/`의 것으로 정해진다
        - 제품 typedef `node`와 비제품 매크로 `node`면 매크로 표를 건너뛰고 typedef가 나온다
        - 제품 쪽이 **함수뿐**이면(`tests/`의 `#define F(p) …`가 제품 함수 `F`를 덮는 꼴) 그 이름은
          싣지 않는다 — 함수 본문은 `callees_of`의 몫이다
        제품 정의가 하나도 없으면 비제품 정의도 그대로 쓴다 — **삭제가 아니다.** 순서도 제품 정의가
        먼저다(읽는 쪽이 상한까지만 싣는다). `near_file`이 비제품이면 강등하지 않는다 — 그 코드가
        쓰는 정의는 형제 비제품 경로에 있는 것이 정상이다.

        **강등이 도달성보다 먼저다.** 제품 앵커가 `tests/` 헤더를 include하는 일은 드물어 순서가
        결과를 바꾸는 경우가 적고, 강등을 먼저 두면 강등만 켠 조회(v3 이하 문서)와 같은 행에서
        출발한다. 강등이 비제품 행을 다 뺀 뒤 남은 제품 행이 하나도 안 닿으면 아무것도 안 싣는다.
        """
        if self._load().status != STATUS_LOADED or self._tables is None:
            return None
        demote = demote_nonproduct and not is_nonproduct_path(near_file)
        wanted = sorted(set(names))
        product_names = self._product_definition_names(wanted) if demote else frozenset()
        found: list[Location] = []
        for name in wanted:
            for table in DEFINITION_TABLES:
                rows = self._tables.get(table, {}).get(name)
                if rows and name in product_names:
                    rows = [row for row in rows if not is_nonproduct_path(row["file"])]
                if not rows:
                    continue
                rows = _definition_rows(rows)
                here = [row for row in rows if row["file"] == near_file]
                candidates = here or self._reachable(rows, near_file)
                if len(candidates) == 1:
                    row = candidates[0]
                    found.append(
                        Location(
                            file=row["file"],
                            symbol=name,
                            line_start=row["line_start"],
                            line_end=row["line_end"],
                        )
                    )
                break
        return sorted(
            found,
            key=lambda loc: (
                demote and is_nonproduct_path(loc.file),
                loc.file,
                loc.line_start or 0,
                loc.symbol or "",
            ),
        )

    def _reachable(self, rows: list, near_file: str) -> list:
        """후보 행이 여럿이면 `near_file`의 include 닫힘에 든 행이 **하나뿐일 때** 그 행만, 아니면
        그대로.

        v4 `includes` 표가 없으면(v3 이하) 아무것도 안 한다 — 옛 문서의 조회 결과가 안 바뀐다.
        """
        assert self._tables is not None
        if len(rows) <= 1 or "includes" not in self._tables:
            return list(rows)
        closure = self._include_closure(near_file)
        reached = [row for row in rows if row["file"] in closure]
        return reached if len(reached) == 1 else list(rows)

    def _include_closure(self, near_file: str) -> frozenset[str]:
        """`near_file`과 그것이 `#include`로 (전이적으로) 닿는 저장소 파일. 깊이 `_INCLUDE_DEPTH`,
        파일 `_INCLUDE_FILES`까지다 — 넘치면 거기서 자른다. 앵커마다 한 번 계산해 둔다."""
        cached = self._closures.get(near_file)
        if cached is not None:
            return cached
        assert self._tables is not None
        includes = self._tables["includes"]
        seen = {near_file}
        frontier = [near_file]
        for _ in range(_INCLUDE_DEPTH):
            following = []
            for file in frontier:
                for row in includes.get(file, ()):
                    for target in self._resolve_include(file, row["path"], row["system"]):
                        if target not in seen and len(seen) < _INCLUDE_FILES:
                            seen.add(target)
                            following.append(target)
            if not following:
                break
            frontier = following
        closure = frozenset(seen)
        self._closures[near_file] = closure
        return closure

    def _resolve_include(self, includer: str, path: str, system: bool) -> tuple[str, ...]:
        """include 경로 → 저장소 파일들. 못 풀면 빈 튜플이다(libc 헤더 · 대상 트리 밖).

        1. 따옴표 include(`system`이 거짓)는 포함 파일의 디렉터리 기준 경로가 인덱싱한 파일이면
           그것 하나다 — 컴파일러가 먼저 보는 자리다(`../`도 여기서 푼다).
        2. 아니면 경로 **접미**가 같은 파일(`foo/bar.h`는 `foo/bar.h`·`x/foo/bar.h`)을 찾는다. 앞의
           `./`·`../`는 떼고 맞춘다. 여럿이면 경로 성분이 가장 적은 것, 그래도 여럿이면 **전부** —
           어느 `-I`가 이기는지 모르니 하나를 고르지 않는다(닫힘이 넓어질 뿐이고 그만큼 「하나뿐」이
           덜 선다).
        """
        here = posixpath.dirname(includer)
        key = (here, path, system)
        cached = self._resolved.get(key)
        if cached is not None:
            return cached
        norm = path.replace("\\", "/")
        local = posixpath.normpath(posixpath.join(here, norm))
        if not system and local in self._files:
            resolved: tuple[str, ...] = (local,)
        else:
            parts = [part for part in norm.split("/") if part not in ("", ".", "..")]
            suffix = "/".join(parts)
            if self._by_base is None:
                self._by_base = {}
                for file in sorted(self._files):
                    self._by_base.setdefault(posixpath.basename(file), []).append(file)
            candidates = self._by_base.get(parts[-1], []) if parts else []
            matches = [
                file for file in candidates if file == suffix or file.endswith("/" + suffix)
            ]
            fewest = min((file.count("/") for file in matches), default=0)
            resolved = tuple(file for file in matches if file.count("/") == fewest)
        self._resolved[key] = resolved
        return resolved

    def _product_definition_names(self, names: list[str]) -> frozenset[str]:
        """`names` 중 제품 경로에 정의가 하나라도 있는 이름 — 정의 표와 함수(`_defined`)를 함께
        본다. `definitions_of`의 표를 가로지른 강등이 쓴다(v2 표가 실려 있을 때만 부른다)."""
        assert self._tables is not None
        wanted = set(names)
        product = {
            name
            for name in wanted
            for table in DEFINITION_TABLES
            if any(
                not is_nonproduct_path(row["file"])
                for row in self._tables.get(table, {}).get(name, ())
            )
        }
        for file, symbols in self._defined.items():
            if is_nonproduct_path(file):
                continue
            product.update(name for name, _, _ in symbols if name in wanted)
        return frozenset(product)

    def callees_of(
        self, target: Location, *, demote_nonproduct: bool = False
    ) -> list[Location] | None:
        """대상 본문 안의 호출이 부르는 함수의 **정의 위치**. `None`은 그래프 자체가 없다는 뜻이다.

        Auditor 입력이 슬라이스가 지나간 함수만 싣고 그 함수들이 부르는 함수의 본문은 안 싣던
        자리를 채운다(#349 (a)). 호출 지점이 대상 본문
        (`_in_body`) 안인 간선의 callee 이름을 정의로 푼다:

        - 대상 파일에 그 이름의 정의가 있으면 그것(같은 파일 `static`이 이긴다)
        - 없으면 저장소 전체에서 정의가 **하나뿐일 때만** 그것 — 여럿이면 이름만으로는 어느
          정의인지 모르므로(`_rules` docstring의 한계) 싣지 않는다

        대상 자신(재귀)은 뺀다. 순서는 본문 안 첫 호출 줄 순이다(결정론 — 같은 줄이면 이름 순).

        `demote_nonproduct`는 `definitions_of`와 같다 — 켜면 제품 경로 정의가 비제품 정의보다 앞서
        「하나뿐」을 정하고(제품 정의가 없으면 비제품 정의도 쓴다), 제품 정의의 callee가 먼저 온다.
        대상 파일이 비제품이면 강등하지 않는다.
        """
        if self._load().status != STATUS_LOADED:
            return None
        demote = demote_nonproduct and not is_nonproduct_path(target.file)
        defined: dict[str, list[tuple[str, int, int]]] = {}
        for file, symbols in self._defined.items():
            for name, start, end in symbols:
                defined.setdefault(name, []).append((file, start, end))
        first_call: dict[str, int] = {}
        for _, callee, file, line in self._calls:
            if self._in_body(file, line, target) and callee not in first_call:
                first_call[callee] = line
            elif self._in_body(file, line, target):
                first_call[callee] = min(first_call[callee], line)
        found: list[Location] = []
        for callee in sorted(first_call, key=lambda name: (first_call[name], name)):
            if callee == target.symbol:
                continue
            here = [d for d in defined.get(callee, []) if d[0] == target.file]
            candidates = here or (
                _product_first(defined.get(callee, []), itemgetter(0))
                if demote
                else defined.get(callee, [])
            )
            if len(candidates) != 1:
                continue
            file, start, end = candidates[0]
            found.append(Location(file=file, symbol=callee, line_start=start, line_end=end))
        if demote:
            found.sort(key=lambda loc: is_nonproduct_path(loc.file))  # 안정 정렬 — 호출 줄 순 유지
        return found

    def functions_named(
        self, names: Iterable[str], near_file: str, *, demote_nonproduct: bool = False
    ) -> list[Location] | None:
        """이름들 중 **함수**인 것의 정의 위치. `None`은 그래프가 없다는 뜻이다.

        매크로가 이름만 바꿔 부르는 함수
        (`#define FT_FRAME_EXTRACT(...) FT_Stream_ExtractFrame(...)`)를 찾는 데 쓴다 -- 매크로 본문
        안의 호출은 인덱스 호출 표에 없어 `callees_of`로는 안 닿는다(#349 후속, C4). 고르는 규칙은
        `callees_of`와 같다: `near_file`에 정의가 있으면 그것, 없으면 저장소 전체에서 **하나뿐일
        때만**(`demote_nonproduct`면 제품 경로가 먼저 「하나뿐」을 정한다). 순서는 이름 순이다.
        """
        if self._load().status != STATUS_LOADED:
            return None
        demote = demote_nonproduct and not is_nonproduct_path(near_file)
        wanted = set(names)
        defined: dict[str, list[tuple[str, int, int]]] = {}
        for file, symbols in self._defined.items():
            for name, start, end in symbols:
                if name in wanted:
                    defined.setdefault(name, []).append((file, start, end))
        found: list[Location] = []
        for name in sorted(defined):
            here = [d for d in defined[name] if d[0] == near_file]
            candidates = here or (
                _product_first(defined[name], itemgetter(0)) if demote else defined[name]
            )
            if len(candidates) != 1:
                continue
            file, start, end = candidates[0]
            found.append(Location(file=file, symbol=name, line_start=start, line_end=end))
        return found

    def call_sites_of(
        self, target: Location, *, demote_nonproduct: bool = False
    ) -> list[Location] | None:
        """대상 함수를 부르는 **호출 지점**(줄 하나 — `symbol`은 호출자 이름). `None`은 그래프가
        없다는 뜻이다.

        Auditor 입력이 호출자 쪽 검사(인자 길이·NULL 확인)를 못 보던 자리를 채운다(#349 (b)).
        `callees_of`와 같은 이름 규칙의 반대 방향이다:

        - 대상 파일 안의 호출은 늘 싣는다(같은 파일 정의가 이긴다)
        - 다른 파일의 호출은 대상 이름의 정의가 저장소 전체에서 **하나뿐일 때만** — 여럿이면
          그 호출이 어느 정의로 가는지 이름만으로 모른다
        - 대상 본문 안의 호출(재귀)은 뺀다 — 원문에 이미 있다

        순서는 대상 파일 → 대상과 같은 디렉터리 → 나머지, 그 안에서 (파일, 줄)이다(결정론 —
        읽는 쪽이 앞 몇 개만 쓰므로 가까운 호출자가 먼저 온다 · #351 리뷰). 심볼 없는 대상은 `[]`다.

        `demote_nonproduct`는 `definitions_of`와 같다 — 켜면 「정의가 하나뿐」을 **제품 경로
        정의만** 세고(비제품 정의는 경쟁자가 아니다), 비제품 경로의 호출 지점은 맨 뒤로 민다. 다만
        **같은 이름의 비제품 정의가 어딘가 있으면 비제품 호출 전체를 안 싣는다** — 모의 구현을
        부르는 이웃 호출(`tests/mock/user.c` → `tests/mock/dup.c`)은 어느 정의로 가는지 이름만으로
        모르고, 그 정의를 가진 파일 자신의 호출도 같다. 제품 호출은 그대로다. 대상 파일이 비제품이면
        강등하지 않는다.
        """
        if self._load().status != STATUS_LOADED:
            return None
        if not target.symbol:
            return []
        demote = demote_nonproduct and not is_nonproduct_path(target.file)
        # 파일 수가 아니라 **정의 수**다 — `callees_of`·`definitions_of`와 같은 규칙(한 파일
        # 안 `#if` 양쪽 가지도 둘이다).
        homes = [
            (file, start)
            for file, symbols in self._defined.items()
            for name, start, _ in symbols
            if name == target.symbol
        ]
        rivals = _product_first(homes, itemgetter(0)) if demote else homes
        # 강등을 켜면 경쟁자가 제품 정의 하나뿐이어도 **같은 이름의 비제품 정의가 따로 있을 수
        # 있다**(`copied` — 모의 구현·예제 사본). 그러면 비제품 호출은 어느 정의로 가는지 모른다
        # (`tests/mock/user.c`가 `tests/mock/dup.c`를 부른다) — 이름만으로 못 가르니 안 싣는다.
        # 그 정의를 가진 파일 자신의 호출도 여기 든다. 제품 호출은 영향이 없다.
        copied = demote and len(homes) > len(rivals)
        sites = {
            Location(file=file, symbol=caller, line_start=line, line_end=line)
            for caller, callee, file, line in self._calls
            if callee == target.symbol
            and not self._in_body(file, line, target)
            and (
                file == target.file
                or (len(rivals) == 1 and not (copied and is_nonproduct_path(file)))
            )
        }
        here = posixpath.dirname(target.file)
        return sorted(
            sites,
            key=lambda loc: (
                demote and is_nonproduct_path(loc.file),
                loc.file != target.file,
                posixpath.dirname(loc.file) != here,
                loc.file,
                loc.line_start or 0,
            ),
        )

    # --- index-v3 조회 (#304) ---------------------------------------------
    #
    # 넷 다 **새 메서드**다 — 위의 `callees_of`·`call_sites_of`·`definitions_of`는 그대로다. 이름을
    # 잇는 규칙은 그쪽과 같다: 같은 파일이 먼저, 다른 파일은 그 이름의 정의가 저장소 전체에서
    # **하나뿐일 때만**. `None`은 v3 표가 없다는 뜻이다(인덱스 없음·v1·v2).

    def writes_of(self, name: str, near_file: str) -> list[Location] | None:
        """이름 `name`에 **쓰는 지점**(줄 하나 — `symbol`은 감싼 함수).

        버퍼가 어디서 할당되고 어디서 채워지는지를 Auditor가 못 보던 자리(#304)를 채운다. 합치는
        것: `assigns`(이름 대입 `p = malloc(n)` · 복합 대입)와 `writes`의 `base == "name"` 행(원소
        `p[i] = x` · 역참조 `*p = x` · 선언 초기화 `char *p = malloc(n)`). 필드는
        `field_writes_of`다.

        - `near_file` 안의 쓰기가 있으면 그것만
        - 없으면 `name`이 파일 스코프 변수로 **하나뿐일 때만**(`file_vars`) 저장소 전체 — 지역
          이름은 파일마다 따로라 다른 파일의 같은 이름을 잇지 않는다. 「하나뿐」은 헤더 밖 선언이
          있으면 그것만 센다 — 헤더의 `extern char *g;`와 `.c`의 정의가 둘로 세지면 흔한 전역이
          한 번도 이어지지 않는다(#464 리뷰). 그때도 그 이름이 **지역·파라미터인 함수**(`locals`)의
          행은 뺀다 — 다른 파일 함수의 같은 이름 지역은 그 전역이 아니다

        ⚠ 파일 안에서는 함수를 가르지 않는다 — 같은 파일 다른 함수의 같은 지역 이름도 든다. 행의
        `symbol`(감싼 함수)로 호출자가 거른다. 증감(`i++`)은 인덱서가 안 싣는다(`indexer.py` 표).
        순서는 (파일, 줄)이다.
        """
        tables = self._v3()
        if tables is None:
            return None
        sites = {
            (file, func, line)
            for file, funcs in tables["assigns"].items()
            for func, names in funcs.items()
            for line in names.get(name, ())
        }
        sites |= {
            (row["file"], row["func"], row["line"])
            for row in tables["writes"].get(name, ())
            if row["base"] == "name"
        }
        here = {site for site in sites if site[0] == near_file}
        if not here and _single_global(tables["file_vars"].get(name, ())):
            scoped = tables["locals"]
            here = {
                (file, func, line)
                for file, func, line in sites
                if name not in scoped.get(file, {}).get(func, {})
            }
        return sorted(
            (
                Location(file=file, symbol=func, line_start=line, line_end=line)
                for file, func, line in here
            ),
            key=lambda loc: (loc.file, loc.line_start or 0, loc.symbol or ""),
        )

    def field_writes_of(self, field: str, near_file: str) -> list[Location] | None:
        """필드 `field`에 **쓰는 지점**(줄 하나 — `symbol`은 감싼 함수).

        `->payload_packet_len = …` 같은 길이 필드가 어디서 정해지는지(#304). `field_assigns`(필드
        대입)와 `writes`의 `base == "field"` 행(`vv->buf[i] = x` · `*vv->ptr = x`)을 합친다.

        - `near_file` 안의 쓰기는 늘 싣는다
        - 다른 파일의 쓰기는 그 필드를 선언한 struct가 저장소 전체에서 **하나뿐일 때만**(`fields`) —
          여럿이면 그 쓰기가 어느 struct의 필드인지 이름만으로 모른다(`call_sites_of`와 같은 규칙)

        순서는 `near_file` → 같은 디렉터리 → 나머지, 그 안에서 (파일, 줄)이다.
        """
        tables = self._v3()
        if tables is None:
            return None
        rows = list(tables["field_assigns"].get(field, ())) + [
            row for row in tables["writes"].get(field, ()) if row["base"] == "field"
        ]
        unique = len(tables["fields"].get(field, ())) == 1
        sites = {
            Location(
                file=row["file"], symbol=row["func"], line_start=row["line"], line_end=row["line"]
            )
            for row in rows
            if row["file"] == near_file or unique
        }
        return _near_first(sites, near_file)

    def fn_pointer_targets(
        self, field: str, near_file: str, *, demote_nonproduct: bool = False
    ) -> list[Location] | None:
        """함수 포인터 필드 `field`에 묶인 함수들의 **정의 위치**(#304 · 간접 호출 해석).

        `fn_bindings`(`o->alloc = my_alloc` · `.free_ctx = sshpam_free_ctx`)에서 함수 이름을 모으고,
        그 이름을 `functions` 표의 정의로 푼다.

        - 바인딩은 `near_file` 것은 늘, 다른 파일 것은 필드를 선언한 struct가 **하나뿐일 때만** —
          `alloc`처럼 흔한 필드 이름이 여러 struct에 있으면 다른 파일의 바인딩이 같은 필드인지
          모른다
        - 함수 이름 → 정의는 바인딩이 있는 파일의 정의가 먼저, 없으면 저장소 전체에서 하나뿐일 때만
          (`callees_of`와 같은 규칙)

        ⚠ 이름 하나에 바인딩이 여럿이면 **전부** 후보다 — 어느 것이 그 호출 시점의 값인지는
        구문만으로 모른다. 위치 초기화(`{ my_init, my_free }`)는 인덱서가 안 싣는다. 순서는
        (파일, 시작 줄)이다.

        `demote_nonproduct`는 `callees_of`와 같다 — 켜면 제품 경로 바인딩이 있을 때 비제품
        경로 바인딩(`tests/`의 `o->alloc = fake_alloc`)을 빼고, 함수 이름 → 정의의 「하나뿐」도
        제품 경로가 먼저 정한다(`_product_first` — 강등이지 삭제가 아니다). `near_file`이
        비제품이면 강등하지 않는다.
        """
        tables = self._v3()
        if tables is None:
            return None
        demote = demote_nonproduct and not is_nonproduct_path(near_file)
        unique = len(tables["fields"].get(field, ())) == 1
        rows = [
            row
            for row in tables["fn_bindings"].get(field, ())
            if row["file"] == near_file or unique
        ]
        if demote:
            rows = _product_first(rows, itemgetter("file"))
        found: set[Location] = set()
        for row in rows:
            definitions = list(tables["functions"].get(row["fn"], ()))
            candidates = [d for d in definitions if d["file"] == row["file"]] or (
                _product_first(definitions, itemgetter("file")) if demote else definitions
            )
            if len(candidates) != 1:
                continue
            d = candidates[0]
            found.add(
                Location(
                    file=d["file"],
                    symbol=row["fn"],
                    line_start=d["line_start"],
                    line_end=d["line_end"],
                )
            )
        return sorted(found, key=lambda loc: (loc.file, loc.line_start or 0, loc.symbol or ""))

    def indirect_calls_of(
        self, target: Location, *, demote_nonproduct: bool = False
    ) -> list[tuple[str, list[Location]]] | None:
        """대상 본문 안의 필드 경유 호출(`vv->varint_get64s(…)`)마다 **(필드, 갈 수 있는 함수의 정의
        위치)**.

        `callees_of`가 못 푸는 간접 호출(#304)을 `field_calls` →
        `fn_pointer_targets(필드, target.file)`로 잇는다. 본문 판정은 `callees_of`와 같은
        `_in_body`이고 대상 자신은 뺀다. 순서는 본문 안 첫 호출 줄 순(같은 줄이면 필드 이름 순),
        필드 안은 `fn_pointer_targets`의 순서다. 후보가 없는 필드는 안 낸다. 필드별로 묶는
        것은 「어느 것인지 모른다」가 필드 하나 단위의 문제라서다 — 러너가 후보 수 상한을 필드마다
        건다. 두 필드가 같은 함수에 묶이면 둘 다에 든다. `demote_nonproduct`는
        `fn_pointer_targets`로 넘긴다.
        """
        tables = self._v3()
        if tables is None:
            return None
        first_call: dict[str, int] = {}
        for field, rows in tables["field_calls"].items():
            for row in rows:
                if self._in_body(row["file"], row["line"], target):
                    first_call[field] = min(first_call.get(field, row["line"]), row["line"])
        found: list[tuple[str, list[Location]]] = []
        for field in sorted(first_call, key=lambda name: (first_call[name], name)):
            targets = [
                loc
                for loc in self.fn_pointer_targets(
                    field, target.file, demote_nonproduct=demote_nonproduct
                )
                or ()
                if not (loc.symbol == target.symbol and loc.file == target.file)
            ]
            if targets:
                found.append((field, targets))
        return found

    def indirect_callees_of(
        self, target: Location, *, demote_nonproduct: bool = False
    ) -> list[Location] | None:
        """`indirect_calls_of`를 펼친 것 — 필드 순서대로 후보를 잇고 같은 정의는 한 번만 낸다."""
        grouped = self.indirect_calls_of(target, demote_nonproduct=demote_nonproduct)
        if grouped is None:
            return None
        found: list[Location] = []
        for _, targets in grouped:
            found.extend(loc for loc in targets if loc not in found)
        return found

    def _v3(self) -> Mapping[str, Mapping[str, Any]] | None:
        """v3 표가 다 있으면 표 묶음, 아니면 `None`(인덱스 없음·깨짐·v1·v2)."""
        if self._load().status != STATUS_LOADED or self._tables is None:
            return None
        if not all(name in self._tables for name in V3_TABLES):
            return None
        return self._tables

    # --- 본문 판정 -------------------------------------------------------
    #
    # 둘이 한 정의를 공유한다: **본문 = `read_symbol_source()`가 돌려주는 범위.**
    # 심볼 target이면 그 심볼의 줄 범위, 파일 target이면 그 파일 전체다. 슬라이스 타깃이면
    # 슬라이스의 줄 집합이다(`body` — `names_for` docstring 마지막 절).

    def _rules(
        self, target: Location, body: SliceBody | None
    ) -> tuple[Callable[[str, int], bool], Callable[[str], bool]]:
        """(호출 지점이 본문 안인가, 이름의 정의가 본문 안인가) — 두 규칙이 쓰는 한 본문.

        ⚠ **두 규칙의 정밀도가 다르다.** 호출 지점 쪽은 `(file, line)`으로 파일을 보는데,
        정의 쪽(`shown`)은 **이름만** 든다. 군집 슬라이스가 파일을 가로지르면 여러 파일의
        이름이 한 집합에 섞여, 같은 이름이 슬라이스와 안 겹치는 다른 파일에도 정의돼 있으면
        그쪽으로 가는 호출까지 callers에 실린다(#288 리뷰 LOW-2 ·
        `test_a_slice_body_cannot_tell_two_definitions_apart_by_file`이 박았다).

        **지금은 좁힐 수 없다** — 인덱스의 `calls` 행은 callee를 **파일 없는 이름**으로만 들고,
        그 행의 `file`·`line`은 호출 지점이지 정의 위치가 아니다(index-v1·v2 둘 다 — v2가 callee로
        묶었어도 정의 파일은 안 든다. C 구문만으로는 어느 정의로 가는지 모른다).
        비슬라이스 경로는 `target.file`로 스코프돼 이 문제가 없다. 인덱스가 callee 파일을
        들게 되면 `(file, name)`으로 좁힌다.
        """
        if body is None:
            return (
                lambda file, line: self._in_body(file, line, target),
                lambda name: self._defined_in_body(name, target),
            )
        lines: dict[str, list[int]] = {}
        for file, line in body:
            lines.setdefault(file, []).append(line)
        # 정의 **범위**가 슬라이스 줄과 겹치면 본문 안이다 — 시작 줄이 아니다(docstring).
        shown = {
            name
            for file, file_lines in lines.items()
            for name, start, end in self._defined.get(file, [])
            if any(start <= line <= (end or start) for line in file_lines)
        }
        return (lambda file, line: (file, line) in body), shown.__contains__

    def _in_body(self, file: str, line: int, target: Location) -> bool:
        """(file, line)이 본문 안인가."""
        if file != target.file:
            return False
        if target.line_start is None:
            return True
        return target.line_start <= line <= (target.line_end or target.line_start)

    def _defined_in_body(self, name: str, target: Location) -> bool:
        """`name`의 정의가 본문 안인가.

        파일 target에서는 "같은 파일에 정의됐는가"와 같아져 예전 동작이 그대로 남는다.
        심볼 target에서는 자기 자신만 걸러지고, **형제 함수는 본문 밖이라 살아남는다** —
        그것이 이 변경으로 새로 보이게 되는 간선이다.
        """
        return any(
            defined == name and self._in_body(target.file, start, target)
            for defined, start, _ in self._defined.get(target.file, [])
        )

    @property
    def summary(self) -> IndexSummary:
        """로드 결과. 아직 안 읽었으면 여기서 읽는다."""
        return self._load()

    # --- 내부 -----------------------------------------------------------

    def _load(self) -> IndexSummary:
        """최초 1회만 읽고 훅을 부른다.

        훅이 후보 수에 비례해 불리면 같은 값이 trace에 중복으로 쌓여 재현성 비교가
        지저분해진다. run당 한 번이 계약이다.
        """
        if self._summary is not None:
            return self._summary
        self._summary = self._read()
        if self.on_index is not None:
            self.on_index(self._summary)
        return self._summary

    def _read(self) -> IndexSummary:
        if self.path is None or not self.path.is_file():
            return IndexSummary(status=STATUS_MISSING)
        try:
            document = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            # 읽기 실패와 파싱 실패를 나누지 않는다 — 둘 다 "산출물을 못 읽었다"이고
            # 대응(그래프 섹션 미발행 + 사유 기록)이 같다.
            return IndexSummary(status=STATUS_MALFORMED)
        if not isinstance(document, dict):
            return IndexSummary(status=STATUS_MALFORMED)
        version = document.get("version")
        if version not in SUPPORTED_INDEX_VERSIONS:
            # `indexer.py` docstring이 "읽는 쪽이 모르는 버전은 거절한다"고 정했다.
            # 거절하는 쪽 코드가 여기다.
            return IndexSummary(status=STATUS_UNSUPPORTED)
        try:
            defined = _defined_symbols(document["symbols"])
            calls = _call_edges(document["calls"], version)
            tables = _v2_tables(document, version) if version != _INDEX_V1 else None
            files = frozenset(document["files"])
            summary = IndexSummary(
                status=STATUS_LOADED,
                files=len(document["files"]),
                skipped=len(document["skipped"]),
                symbols=len(document["symbols"]),
                calls=len(calls),
                unresolved_calls=int(document["unresolved_calls"]),
                unresolved_symbols=int(document["unresolved_symbols"]),
                parse_errors=len(document["parse_errors"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            # 버전은 맞는데 필드가 없거나 형이 다르다. 부분 결과를 쓰면 어디까지 믿을 수
            # 있는지 알 수 없으므로 통째로 거절한다. 여기 도달하면 `_defined`·`_calls`·
            # `_tables`는 초기값 그대로다 — 절반만 채워진 그래프도 표도 남지 않는다.
            return IndexSummary(status=STATUS_MALFORMED)
        self._defined, self._calls, self._tables = defined, calls, tables
        self._files = files
        return summary


def graph_section(
    kind: str,
    graph: "GraphIndex",
    target: Location,
    remaining: int,
    body: SliceBody | None = None,
) -> tuple[CapsuleSection | None, bool]:
    """그래프 섹션 하나를 만든다. `(섹션, 멈춰야 하는가)`.

    **두 Retriever가 같은 함수를 부른다.** 예전에는 이 분기가 `fake_retriever`와
    `hybrid_retriever`에 각각 복사돼 있었고, 양쪽 docstring이 "갈리면 안 된다"고 서로에게
    부탁하고 있었다. 부탁 대신 한 함수로 만들면 갈릴 자리가 없다 —
    `rules/harness-c.yaml` 첫 주석·`index_output_path()`와 같은 처리다.

    | 반환 | 뜻 | 호출자가 할 일 |
    | --- | --- | --- |
    | `(None, False)` | 그래프 자체가 없다 (없음·버전 모름·깨짐) | 섹션을 **만들지 않고** 다음으로 |
    | `(None, True)` | 실을 것은 있는데 예산이 없다 | 섹션 구성을 **멈춘다** |
    | `(섹션, False)` | 실었다. 간선 0건이면 `tokens=0, items=0, body=[]` | 붙이고 계속 |

    2행과 3행의 0건을 섞지 않는 것이 이 모듈의 원칙이다 — 0건 섹션으로 위장하면
    "예산이 없었다"가 "그래프가 비었다"로 기록된다.

    **여기서 만든 섹션에는 `min()`을 다시 걸면 안 된다.** `fit_names`가 이미
    `remaining` 이하로 맞춰 돌려주므로 깎을 것이 없고, 굳이 깎으면 줄은 그대로 두고
    토큰만 줄이는 것이 되어 `CapsuleSection`의 검증기에서 죽는다.
    """
    names = graph.names_for(kind, target, body)
    if names is None:
        return None, False
    # `fitted`로 받는다 — 매개변수 `body`(SliceBody: (file, line) 집합)와 섹션의 `body`
    # (담은 줄 `list[str]`)는 **다른 것**이다. 한 이름에 둘을 담으면 `names_for` 아래에
    # 슬라이스 본문을 다시 쓰는 코드가 들어왔을 때 조용히 틀린다(#288 리뷰 LOW-1).
    tokens, items, fitted = fit_names(names, remaining)
    if names and items == 0:
        return None, True
    return CapsuleSection(kind=kind, tokens=tokens, items=items, body=fitted), False


def fit_names(names: list[str], remaining: int) -> tuple[int, int, list[str]]:
    """예산에 들어가는 만큼만 담는다. (토큰 합, 담은 개수, **담은 줄**).

    `HybridRetriever._fit_related`와 같은 계약이다. 숫자만 `min()`으로 깎으면 items는
    전체인데 tokens는 잘린 값이 되어 **매니페스트가 자기 내용과 어긋난다.** 보조 섹션이
    고정 64토큰이던 동안에는 이 경로가 사실상 안 탔지만, callee가 수백 개인 큰 파일에서는
    실제로 걸린다.

    **담은 줄을 버리지 않고 함께 돌려준다.** 예전에는 `(토큰합, 개수)`만 내고 문자열을
    버렸고, 그래서 캡슐이 "몇 토큰짜리 callers가 3건 있다"까지만 알고 그 3건이 무엇인지는
    몰랐다 — 모델에게 보여줄 수 있는 것이 없었다. 호출자가 이 줄을 `CapsuleSection.body`에
    넣고, 스키마 검증기가 `tokens == sum(estimate_tokens(줄))`을 강제한다.

    **반환하는 토큰 합은 `remaining` 이하임이 보장된다.** 호출자가 여기에 다시 `min()`을
    걸면 안 된다 — 줄은 그대로 두고 토큰만 깎는 것이 되어 스키마 검증기에서 죽는다.

    **`items=k`가 `k < len(names)`인지는 캡슐만 봐서는 구별되지 않는다.** `_fit_related`도
    지금 같은 상태이고, 절단을 캡슐에 남기는 것은 별건으로 #63에 있다.
    """
    total = 0
    fitted: list[str] = []
    for name in names:
        cost = estimate_tokens(name)
        if total + cost > remaining:
            break
        total += cost
        fitted.append(name)
    return total, len(fitted), fitted


def _defined_symbols(symbols: list[dict]) -> dict[str, list[tuple[str, int, int]]]:
    """file → (이름, line_start, line_end). **index.json의 순서를 보존한다.**

    예전에는 `set`이라 순서도 줄 범위도 없었다. 순서는 `symbols_for()`가 내는 후보
    순서가 되고 그것이 ID 발급 순서라 재현성의 일부다 — `build_index()`가 이미
    (file, line_start, name)으로 정렬해 두므로 여기서 다시 정렬하지 않는다.
    """
    defined: dict[str, list[tuple[str, int, int]]] = {}
    for symbol in symbols:
        entry = (symbol["name"], int(symbol["line_start"]), int(symbol["line_end"]))
        defined.setdefault(symbol["file"], []).append(entry)
    return defined


def _call_edges(calls: list | dict, version: str) -> list[tuple[str, str, str, int]]:
    """(caller, callee, 호출 지점 file, 호출 지점 line). **두 형식에서 같은 간선이다.**

    index-v1의 `calls`는 간선 목록이고, index-v2~v4는 같은 간선을 callee → 호출 지점(+ 인자)으로
    묶었다. 캡슐 그래프 섹션은 이 튜플만 보므로 형식이 바뀌어도 한 글자도 안 바뀐다 —
    `tests/test_index_graph.py`의 v2 절이 박는다. 버전과 모양이 어긋나면(v1인데 묶음, v2인데
    목록) 여기서 예외가 나고 `_read`가 문서를 통째로 거절한다.
    """
    if version == _INDEX_V1:
        return [(c["caller"], c["callee"], c["file"], int(c["line"])) for c in calls]
    return [
        (site["caller"], callee, site["file"], int(site["line"]))
        for callee, sites in calls.items()
        for site in sites
    ]


def _v2_tables(document: dict, version: str) -> dict[str, Mapping[str, Any]]:
    """v2 조회 표(v3면 `V3_TABLES`까지)를 검사하고 읽기 전용으로 얼린다. 모양이 다르면 예외 —
    `_read`가 통째로 거절한다. v2 문서에는 `V3_TABLES`가 없고 결과에도 없다(`table()`이 `None`).

    기록의 필수 키와 **줄 번호의 형**까지 본다. 절반만 맞는 표를 받으면 resolver가 조회 도중에
    죽거나, 더 나쁘게는 빈 결과를 「못 찾았다」로 모델에게 전한다. 표는 값을 고치지 않고 내주므로
    (`_freeze`) `"12"`가 그대로 resolver까지 간다 — v1처럼 `int()`로 읽어 들이는 자리가 없다.
    줄 번호 밖의 값 형(`file`·`params` 등)은 v2 표에서는 안 본다 — v1도 안 본다.

    **v3 문서는 더 본다**(#464 리뷰). v3 조회(`writes_of` 등)는 행 값을 `Location`과 집합 원소로
    바로 쓰므로, 줄 0이나 매핑 `func`가 통과하면 조회 도중 `ValidationError`·`TypeError`로 죽는다.
    그래서 `V3_TABLES`는 값 형·허용 값을 다(`_V3_VALUES`), v3 조회가 함께 읽는 `field_assigns`는
    `file`·`func`를 본다. **v4 문서는** `V4_TABLES`도 같은 식으로 본다(`_V4_VALUES`).
    """
    tables = {}
    v4 = version == _INDEX_V4
    v3 = version == _INDEX_V3 or v4
    for name in V2_TABLES + (V3_TABLES if v3 else ()) + (V4_TABLES if v4 else ()):
        table = document[name]
        if not isinstance(table, dict):
            raise TypeError(f"{version}의 {name}는 매핑이다")
        if name in _RECORD_KEYS:
            keys = _RECORD_KEYS[name]
            checks = {
                **(_V3_VALUES.get(name, {}) if v3 else {}),
                **(_V4_VALUES.get(name, {}) if v4 else {}),
            }
            for records in table.values():
                for record in records:
                    if not isinstance(record, dict) or not all(key in record for key in keys):
                        raise KeyError(f"{version}의 {name} 기록에는 {keys}가 있어야 한다")
                    if not all(_is_line(record[key]) for key in keys if key in _LINE_KEYS):
                        raise TypeError(f"{version}의 {name} 기록의 줄 번호는 1 이상 정수다")
                    if not all(check(record[key]) for key, check in checks.items()):
                        raise TypeError(f"{version}의 {name} 기록의 값 형이 다르다")
        else:  # 파일 → 함수 → 이름 → [줄]
            for functions in table.values():
                for names in functions.values():
                    for lines in names.values():
                        if not all(_is_line(line) for line in lines):
                            raise TypeError(f"{version}의 {name}는 줄 번호 목록이다")
        tables[name] = _freeze(table)
    return tables


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _one_of(*allowed: str) -> Callable[[Any], bool]:
    return lambda value: isinstance(value, str) and value in allowed


# v3 문서에서 행 값마다 보는 형(`_v2_tables`). 허용 값은 `indexer.py`의 v3 절이 낸 것 그대로다.
_V3_VALUES: dict[str, dict[str, Callable[[Any], bool]]] = {
    "field_assigns": {"file": _is_name, "func": _is_name},
    "writes": {
        "file": _is_name,
        "func": _is_name,
        "lhs": _is_str,
        "rhs": _is_str,
        "op": _is_str,
        "form": _one_of("element", "deref", "init"),
        "base": _one_of("name", "field"),
    },
    "fn_bindings": {
        "file": _is_name,
        "func": lambda value: value is None or _is_name(value),
        "fn": _is_name,
        "form": _one_of("assign", "designated"),
    },
    "field_calls": {
        "file": _is_name,
        "caller": _is_name,
        "args": lambda value: isinstance(value, list) and all(_is_str(arg) for arg in value),
        "expr": _is_str,
    },
}


# v4 문서에서 더 보는 값 형(`_v2_tables`). `includes`는 키가 파일이다. `types`의 `forward`는 앞선
# 선언 typedef 행에만 있다(있으면 `true` — 같은 파일에 본문 행이 있을 때만 단다).
_V4_VALUES: dict[str, dict[str, Callable[[Any], bool]]] = {
    "includes": {"path": _is_name, "system": lambda value: isinstance(value, bool)},
    "enum_constants": {"file": _is_name, "enum": lambda value: value is None or _is_name(value)},
}


# `types` 행 중 본문 있는 태그의 `kind` — `indexer._TAG_KINDS`와 같다(인덱서는 `src/`를 import 하지
# 않아 값이 양쪽에 있다). 본문 없는 `struct X;`는 표에 안 든다.
_TAG_KINDS = frozenset({"struct", "union", "enum"})


def _definition_rows(rows: Iterable[Mapping[str, Any]]) -> list:
    """정의 표 행에서 **같은 사실**을 하나로 접는다 — `definitions_of`의 「하나뿐」 판정 앞.

    - 같은 (파일, 시작 줄, 끝 줄)은 하나다 — `typedef struct Z {…} Z;`가 struct·typedef 두 행을 낸다
    - 앞선 선언 행(v4 `forward` — `typedef struct X X;`)은 **같은 파일에** 본문 있는 태그 행
      (`struct`·`union`·`enum`)이 있을 때만 뺀다. 표시만 믿지 않고 그 전제를 여기서 다시 본다 —
      인덱서도 같은 전제로 표시하지만(`indexer._lookup_tables`), 다른 파일의 같은 이름 행
      (`typedef int X;`)이나 이름이 다른 태그의 본문 때문에 지우면 후보가 줄어 도달성 검사를
      건너뛰고 엉뚱한 정의가 붙는다
    """
    rows = list(rows)
    tagged_files = {row["file"] for row in rows if row.get("kind") in _TAG_KINDS}
    distinct: dict[tuple[str, int, int], Mapping[str, Any]] = {}
    for row in rows:
        # 앞선 선언을 먼저 거른다 — 같은 줄에 본문이 있어 범위가 같으면 접기가 본문 행을 가린다
        if not (row.get("forward") and row["file"] in tagged_files):
            distinct.setdefault((row["file"], row["line_start"], row["line_end"]), row)
    return list(distinct.values())


# 헤더 확장자 — `writes_of`의 「파일 스코프 변수가 하나뿐」에서 `extern` 선언을 빼는 근사다(표에는
# `extern` 여부가 없다). 헤더에만 선언이 있으면 그것을 센다.
_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")


def _single_global(rows: Iterable[Mapping[str, Any]]) -> bool:
    """파일 스코프 변수 선언 행들이 전역 하나를 가리키는가 — 헤더 밖 선언이 있으면 그것만 센다."""
    rows = list(rows)
    outside = [row for row in rows if not str(row["file"]).endswith(_HEADER_SUFFIXES)]
    return len(outside or rows) == 1


def _near_first(sites: Iterable[Location], near_file: str) -> list[Location]:
    """`near_file` → 같은 디렉터리 → 나머지, 그 안에서 (파일, 줄) — `call_sites_of`와 같은 순서."""
    here = posixpath.dirname(near_file)
    return sorted(
        sites,
        key=lambda loc: (
            loc.file != near_file,
            posixpath.dirname(loc.file) != here,
            loc.file,
            loc.line_start or 0,
            loc.symbol or "",
        ),
    )


def _is_line(value: Any) -> bool:
    """줄 번호인가 — 1 이상 JSON 정수(`Location`이 `ge=1`이다 — 0이 통과하면 조회 도중에 죽는다).

    `bool`은 `int`의 하위형이라 `isinstance`로 보면 `true`가 통과한다."""
    return type(value) is int and value >= 1


def _freeze(value: Any) -> Any:
    """JSON 값 → 읽기 전용(매핑은 `MappingProxyType`, 목록은 튜플)."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def source_lines(text: str) -> list[str]:
    r"""인덱스 줄 번호와 같은 좌표로 원문을 줄로 나눈다 -> `\n`에서만 끊고 줄 끝 `\r`만 뗀다.

    인덱서는 tree-sitter 행(`start_point[0] + 1`)을 쓰고 그 행은 `\n`에서만 넘어간다.
    `str.splitlines()`는 폼피드(`\x0c`)·`\x1c`~`\x1e`·`\x85`·` `에서도 끊어 그 글자마다
    한 줄씩 밀린다 -> 폼피드가 많은 파일(ImageMagick 등)에서 함수 범위가 주석부터 시작하고
    끝이 잘렸다(#446). 슬라이서 좌표는 `slices.split_source`가 같은 이유로 따로 맞춘다(#259).

    단독 CR 파일은 여전히 어긋난다 -> tree-sitter는 단독 `\r`에서 행을 안 넘기는데 호출자가
    `read_text`(universal newline)로 읽어 CR을 `\n`으로 바꾼다. 맞추려면 `newline=""`로 읽어야 한다.
    """
    return [line.removesuffix("\r") for line in text.split("\n")]
