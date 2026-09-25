"""1단계 인덱서 산출물(`index.json`)을 읽어 캡슐의 그래프 섹션으로 바꾼다 (#63).

`tools/harness-tools/indexer.py`가 컨테이너 안에서 심볼 표와 호출 그래프를 내고,
`ContainerToolRunner`가 그것을 scratch에 떨어뜨린다. 여기가 **읽는 쪽**이다.

**`FakeRetriever`와 `HybridRetriever`가 이 모듈을 함께 쓴다.** 두 구현이 같은 index.json
에서 다른 섹션을 내면 `dev_fake`와 `dev_docker`의 결과를 비교할 수 없다 —
`rules/harness-c.yaml` 첫 주석이 정적 룰셋에 대해 편 논법과 같다. 그래서 로딩·필터·
토큰 계산이 전부 여기 한 곳에 있고, 두 retriever는 `names_for()`와 `fit_names()`만 부른다.

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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from src.graph_entry import render_entry
from src.schemas.capsule import CapsuleSection
from src.schemas.common import Location
from src.tokens import estimate_tokens

# 슬라이스의 줄 집합 — (file, line). 슬라이스 타깃 캡슐의 「본문」이다(`GraphIndex.names_for`).
SliceBody = frozenset[tuple[str, int]]

# `indexer.py`의 `INDEX_VERSION`과 같아야 한다.
#
# 그 스크립트는 이미지 안에서 살고 `src/`를 import 하지 않아 **형식이 양쪽에 따로
# 존재한다**(`container_tools._INDEX_SUFFIXES` ↔ `indexer._GRAMMARS`와 같은 이중화).
# 어긋나면 예외가 아니라 `unsupported_version`이 되고 그래프 섹션이 통째로 사라지므로,
# `tests/test_indexer.py`가 두 값을 대조해 그 어긋남을 막는다.
SUPPORTED_INDEX_VERSION = "index-v1"

# 이 모듈이 채우는 섹션. `types`는 없다 — index-v1에 타입 데이터가 없기 때문이다
# (`indexer.build_index()`가 내는 것은 symbols·calls뿐이고 struct·class·typedef를 뽑는
# 코드가 없다). 데이터원이 없는데 섹션을 발행하면 이 모듈이 없애려는 거짓말이 된다.
# 채우려면 인덱서에 타입 수집을 넣고 `INDEX_VERSION`을 올려야 한다(#63).
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
        `.h`처럼 인덱서에 넘어가지 않는 파일과 인덱서가 아예 안 돈 run을 한 값으로
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
        그 행의 `file`·`line`은 호출 지점이지 정의 위치가 아니다(`index-v1` 스키마).
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
        if document.get("version") != SUPPORTED_INDEX_VERSION:
            # `indexer.py` docstring이 "읽는 쪽이 모르는 버전은 거절한다"고 정했다.
            # 거절하는 쪽 코드가 여기다.
            return IndexSummary(status=STATUS_UNSUPPORTED)
        try:
            defined = _defined_symbols(document["symbols"])
            calls = _call_edges(document["calls"])
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
        except (KeyError, TypeError, ValueError):
            # 버전은 맞는데 필드가 없거나 형이 다르다. 부분 결과를 쓰면 어디까지 믿을 수
            # 있는지 알 수 없으므로 통째로 거절한다. 여기 도달하면 `_defined`·`_calls`는
            # 초기값 그대로다 — 절반만 채워진 그래프가 남지 않는다.
            return IndexSummary(status=STATUS_MALFORMED)
        self._defined, self._calls = defined, calls
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


def _call_edges(calls: list[dict]) -> list[tuple[str, str, str, int]]:
    return [(c["caller"], c["callee"], c["file"], int(c["line"])) for c in calls]
