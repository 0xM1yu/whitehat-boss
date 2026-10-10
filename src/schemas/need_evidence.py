"""need_evidence resolver의 계약 (ADR 0012 결정 2 · Notion 「Scout RAG baseline」).

**이 모듈은 이름·모양만 고정한다.** resolver 구현·러너 루프는 여기 없다.
`ResolveQuery`가 루프 쪽에서 resolver로 가는 요청이고 `Resolution`이 그 응답이다 —
`Dependencies.resolve_requests`(`src/orchestrator/runner.py`)가 그 사이의 hook이다.
"""

from pydantic import Field

from src.schemas.common import HarnessModel
from src.schemas.enums import FIXABLE_UNFILLED_REASONS, UnfilledReason
from src.schemas.scout_response import RequestedContext


class ResolveQuery(HarnessModel):
    """resolver 호출 하나의 입력. 루프가 홉마다 새로 만든다.

    `source_text` 필드는 없다 — resolver는 이 질의로 무엇이 이미 보이는지(`visible`)만
    받고, 대상 파일 원문은 스스로 읽는다(factory의 `read_lines` 훅). 명세가 쓰는 이름은
    원문을 이 객체에 실어 나르라는 뜻이 아니다.
    """

    requests: tuple[RequestedContext, ...]
    # 1홉이 첫 되묻기다 — hop0은 최초 판정이라 되묻기 질의를 만들지 않는다.
    hop: int = Field(ge=1)
    target_file: str
    # 파일 → 그 파일에서 슬라이스가 이미 보여준 줄(`SliceRef.lines`에서 만든다). resolver가
    # "이미 SOURCE에 있다"(`Resolution.already`)를 가르는 데 쓴다.
    visible: dict[str, frozenset[int]]
    # 이 홉이 RETRIEVED에 실을 수 있는 글자 상한 — 폭주 차단이지 토큰 상한이 아니다
    # (글자 상한은 토큰을 안 묶는다). 루프가 홉마다(남은 예산 기반) 계산해 넘긴다 —
    # factory는 상한을 묶지 않는다.
    max_chars: int = Field(ge=0)


class RetrievedBlock(HarnessModel):
    """resolver가 채운 블록 하나 — 후속 턴 RETRIEVED에 렌더된다."""

    name: str
    label: str
    file: str
    lines: tuple[int, ...]


class Unfilled(HarnessModel):
    """요청 하나를 못 채운 사유. 명세 §4.3의 UNFILLED 사유 8개가 `reason`으로 온다."""

    kind: str
    target: str
    reason: UnfilledReason
    # 렌더된 한 줄 (`Not resolved: …` — 명세 §4.3).
    text: str


class Resolution(HarnessModel):
    """resolver 호출 하나의 출력.

    개수 필드(`resolved_count` 등)는 안 든다 — `len(blocks)`·`len(unfilled)`에서 나오는
    **파생값**이라 필드로 저장하면 원본과 갈릴 수 있다(CLAUDE.md §0 "실측한 것과
    추정한 것을 섞지 않는다"와 같은 이유 — 계산값은 계산값이라고 적어야 하는데, 필드로
    저장하면 그 표시가 없어진다). 필요한 파생값은 `@property`로 둔다.
    """

    followup_text: str
    blocks: tuple[RetrievedBlock, ...]
    unfilled: tuple[Unfilled, ...]
    already: tuple[str, ...]
    # 크기 상한(위 `ResolveQuery.max_chars` — 폭주 차단) 때문에 통째로 뺀 블록 이름들.
    truncated: tuple[str, ...]
    # 조회할 색인 표가 있었는가. `False`면 색인 없음·v1이라 **조회를 아예 안 했다** — 같은 빈
    # 결과라도 「조회했는데 못 찾음」과 다르다(#315). 예전에는 네 칸이 다 비었다는 것으로만
    # 가렸다(암묵 규칙). 기본값 `True`는 이 필드가 생기기 전의 생성자 호출을 그대로 둔다.
    index_available: bool = True
    # 원소·역참조 쓰기 표(index-v3 `writes`)가 있었는가(#304). `False`면 대입 라벨(3·4·13)에
    # `buf[i] = x`·`*p = x` 꼴이 **원천적으로 못 든다**(v2 이하 인덱스·인덱스 없음) — 대입 조각이
    # 없는 것이 「그 함수에 원소 대입이 없다」인지 「인덱스가 그것을 모른다」인지 이 값으로 가른다.
    # 기본값 `True`는 `index_available`과 같은 이유다(이 필드 전의 생성자 호출을 그대로 둔다).
    writes_available: bool = True

    @property
    def fixable_count(self) -> int:
        """형식 사유(자리표시자·target 없음·식별자 없음·식별자 아님)만 센 수.

        이것이 0이고 `blocks`·`already`가 둘 다 비었으면(=아무것도 못 채웠는데 형식 문제도
        아니면) `NeStopReason.NOTHING_RESOLVED`로 선다 — `already`(이미 SOURCE에 있음)는 조회
        성공이라 서지 않는다 — "잘 쓴 이름이 전부 조회에 실패했다"와
        "형식이 잘못됐다"를 가르는 값이다.
        """
        return sum(1 for item in self.unfilled if item.reason in FIXABLE_UNFILLED_REASONS)
