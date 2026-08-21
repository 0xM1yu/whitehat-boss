"""1단계 인덱서 산출물(`index.json`)을 읽어 캡슐의 그래프 섹션으로 바꾼다 (D13).

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
모양이 된다. 지금 target이 `Location(file=...)`이라 `read_symbol_source()`가 파일 전체를
돌려주기 때문이고, 실측 임계는 focused(8,000토큰) 32KB / expanded(24,000토큰) 96KB다
(`src/tokens.CHARS_PER_TOKEN`=4 기준). 심볼 단위 캡슐이 들어오면 `target_function`이 심볼로
좁혀져 사라지는 한계다 — open-questions D13에 적었다.

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
from src.tokens import estimate_tokens

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
# 채우려면 인덱서에 타입 수집을 넣고 `INDEX_VERSION`을 올려야 한다(D13).
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

    **`unresolved_*`를 캡슐에 싣지 않고 여기에 둔다.** 캡슐은 후보 하나당 토큰
    매니페스트인데 이 값들은 저장소 전체의 스칼라라 후보마다 달라지지 않고 토큰 비용도
    없다. 섹션으로 실으면 텍스트가 아닌 숫자가 토큰 회계에 들어간다.

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
        # file → 그 파일에 정의된 심볼 이름. 간선의 반대쪽 끝 판정에 쓴다.
        self._defined: dict[str, set[str]] = {}
        # 호출 간선 원본. (caller, callee, file, line)
        self._calls: list[tuple[str, str, str, int]] = []

    # --- 조회 -----------------------------------------------------------

    def names_for(self, kind: str, file: str) -> list[str] | None:
        """`kind` 섹션에 실을 항목. `None`은 **그래프 자체가 없다**는 뜻이다.

        빈 리스트(`[]`)와 섞지 않는다 — 그쪽은 "봤는데 간선이 0건"이다. 호출자는 이
        구별을 그대로 캡슐에 옮긴다(모듈 docstring 표).

        ## 간선의 반대쪽 끝이 이미 캡슐 본문 안이면 싣지 않는다

        **전제: 지금 target은 `Location(file=X)`이고 `line_start`가 없어
        `read_symbol_source()`가 파일 전체를 돌려준다** — 즉 `target_function` 섹션이 X
        전체다. 그래서 X 안에서 X 안으로 가는 간선은 본문에 이미 있고, 또 적으면 토큰만
        부푼다(`HybridRetriever._collect()`가 대상 파일을 후보에서 빼는 것과 같은 이유).

        **심볼 단위 캡슐(D13 후속)이 이 전제를 깬다.** `target_function`이 심볼 한 개의
        줄 범위로 좁혀지면 같은 파일 안의 호출자·피호출자가 더는 본문에 없고, 그때 이
        필터는 **실재하는 간선을 숨긴다.** 그 작업에서 조건을 "파일이 같은가"가 아니라
        "본문 줄 범위 안인가"로 바꿔야 한다.

        `symbols`에 아예 없는 이름(`memcpy` 같은 외부 함수)은 남긴다 — 본문 안에 없는
        것이 확실하기 때문이다.
        """
        if kind not in GRAPH_SECTION_KINDS:
            raise ValueError(f"그래프 섹션이 아니다: {kind}")
        if self._load().status != STATUS_LOADED:
            return None

        defined = self._defined.get(file, frozenset())
        if kind == "callers":
            # 이 파일에 정의된 심볼로 들어오는 호출. `row_file`이 호출자가 정의된
            # 파일이므로(인덱서가 호출자 본문을 훑으며 모은다) `!= file`이 곧 외부다.
            rows = [
                (caller, row_file, line)
                for caller, callee, row_file, line in self._calls
                if callee in defined and row_file != file
            ]
        else:
            # 이 파일에서 나가는 호출. 대상이 이 파일에 정의돼 있으면 본문 안이라 뺀다.
            rows = [
                (callee, row_file, line)
                for _, callee, row_file, line in self._calls
                if row_file == file and callee not in defined
            ]
        # 정렬·중복 제거로 결정론을 지킨다. 같은 입력에 같은 캡슐이 나오지 않으면
        # engineering-guide.md §5의 재현성이 깨진다.
        return sorted({render_entry(name, row_file, line) for name, row_file, line in rows})

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
    kind: str, graph: "GraphIndex", target_file: str, remaining: int
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
    names = graph.names_for(kind, target_file)
    if names is None:
        return None, False
    tokens, items, body = fit_names(names, remaining)
    if names and items == 0:
        return None, True
    return CapsuleSection(kind=kind, tokens=tokens, items=items, body=body), False


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
    지금 같은 상태이고, 절단을 캡슐에 남기는 것은 별건으로 D13에 있다.
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


def _defined_symbols(symbols: list[dict]) -> dict[str, set[str]]:
    defined: dict[str, set[str]] = {}
    for symbol in symbols:
        defined.setdefault(symbol["file"], set()).add(symbol["name"])
    return defined


def _call_edges(calls: list[dict]) -> list[tuple[str, str, str, int]]:
    return [(c["caller"], c["callee"], c["file"], int(c["line"])) for c in calls]
