"""캡슐 그래프 섹션 한 줄의 형식. **렌더러와 파서가 나란히 있는 유일한 자리다.**

`callers`/`callees` 섹션의 본문은 `이름 파일:줄`(`memcpy parser.c:20`) 꼴이고, 그 문자열의
길이가 곧 섹션의 토큰 수다(`src/tokens.estimate_tokens`). 만드는 쪽과 되읽는 쪽이 갈리면
형식을 바꾼 사람이 한쪽만 고치고 다른 쪽은 **조용히 틀린 값**을 낸다 — 이 저장소가
`indexer._GRAMMARS`↔`container_tools._INDEX_SUFFIXES`와 `INDEX_VERSION`↔
`SUPPORTED_INDEX_VERSIONS`에서 이미 두 번 처리한 이중화다. 그 둘은 프로세스 경계 때문에
갈라질 수밖에 없었지만 이쪽은 그럴 이유가 없다.

## 왜 `adapters/index_graph.py`가 아니라 여기인가

내용상으로는 그쪽 모듈의 것이다. 그런데 되읽는 쪽이 `orchestrator/nodes/auditor.py`이고,
`tests/test_policy.py::test_orchestrator_source_has_no_adapter_import`가
`src/orchestrator/**`에서 `src.adapters` import를 **0건으로 강제한다**
(engineering-guide.md §3: "구현체 교체가 설정만으로 가능"의 근거).

그래서 선택지는 둘뿐이었다: (a) 파서를 auditor 쪽에 복사한다 (b) 짝을 통째로 양쪽이
import 할 수 있는 층으로 옮긴다. (a)는 이 모듈이 없애려는 이중화를 새로 만드는 것이라
(b)로 갔다. `src/tokens.py`가 `CapsuleSection` 검증을 위해 같은 이유로 올라온 것과 같은
처리다 — `src/ids.py`·`src/paths.py`와 같은 층이다.
"""


def render_entry(name: str, file: str, line: int) -> str:
    """섹션 한 줄. 토큰 수가 이 문자열의 길이에서 나오므로 렌더러가 하나여야 한다.

    `callers`·`callees` 둘 다 **호출 지점**을 적는다. 두 섹션이 같은 `calls` 행에서
    나오므로 형식이 갈릴 이유가 없고, `items`의 뜻도 양쪽 모두 "호출 지점 수"로 같다.
    """
    return f"{name} {file}:{line}"


def name_of(entry: str) -> str:
    """`render_entry()`가 만든 줄에서 심볼 이름만 되돌린다.

    C 식별자에는 공백이 없으므로 첫 공백까지가 이름이다. 공백이 없는 줄은 통째로 이름으로
    본다 — 형식이 어긋난 줄을 조용히 빈 문자열로 만들지 않는다.

    `tests/test_index_graph.py`가 왕복(render→parse)을 고정한다.
    """
    return entry.split(" ", 1)[0]
