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


class AlreadyEntry(HarnessModel):
    """`Already in SOURCE` 줄 하나를 렌더한 **구조값** - 줄 글을 다시 읽지 않고 쓰라고 싣는다.

    `kind`는 모델이 요청에 쓴 자유 문자열이다(`RequestedContext.kind`) - 줄에 그대로 되울린다.
    나머지는 인덱스가 정한 값이다(`name`은 블록 이름, 파일·줄은 인덱스 표).
    """

    kind: str
    name: str
    file: str
    line_start: int
    line_end: int


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
    # 없는 것이 「그 함수에 원소 대입이 없다」인지 「인덱스가 그것을 모른다」인지를 가르는 값이다.
    # 지금은 `Resolution`에만 실린다(읽는 코드 0) — trace 반영은 후속.
    # 기본값 `True`는 `index_available`과 같은 이유다(이 필드 전의 생성자 호출을 그대로 둔다).
    writes_available: bool = True
    # `followup_text`에서 조각 뒤·꼬리 앞에 **실제로 렌더된** 알림 줄들의 순서(싣다 만 `Already in
    # SOURCE`와 그 생략 한 줄, 그리고 맨 끝에 `unfilled`의 `text` 전부). 글자 상한 때문에
    # `already`의 앞쪽만 실리므로 `already`·`unfilled`만으로는 어느 줄이 실렸는지 못 가린다.
    # `None`은 이 필드가 없던 생성자 호출이다 - 읽는 쪽이 `(*already, *unfilled 줄)`로 본다.
    alert_lines: tuple[str, ...] | None = None
    # `alert_lines` 앞쪽에 **실제로 렌더된** `Already in SOURCE` 줄들의 구조값(같은 순서,
    # 생략 한 줄은 없다). 글자 상한에 걸려 안 실린 줄은 여기에도 없다. 읽는 쪽(Critic 사본)이
    # 렌더된 글을 정규식으로 다시 읽지 않고 이 값에서 줄을 다시 쓰게 하려는 것이다 - `kind`가
    # 모델 글이라 글에서 `kind`/`name` 경계를 되읽으면 모델이 경계를 속인다. `None`은 이 필드가
    # 없던 생성자 호출이다 - `already`가 비지 않았으면 다시 쓸 구조가 없어 가리지 못한 채
    # 주지 않는다.
    already_entries: tuple[AlreadyEntry, ...] | None = None

    @property
    def fixable_count(self) -> int:
        """형식 사유(자리표시자·target 없음·식별자 없음·식별자 아님)만 센 수.

        이것이 0이고 `blocks`·`already`가 둘 다 비었으면(=아무것도 못 채웠는데 형식 문제도
        아니면) `NeStopReason.NOTHING_RESOLVED`로 선다 — `already`(이미 SOURCE에 있음)는 조회
        성공이라 서지 않는다 — "잘 쓴 이름이 전부 조회에 실패했다"와
        "형식이 잘못됐다"를 가르는 값이다.
        """
        return sum(1 for item in self.unfilled if item.reason in FIXABLE_UNFILLED_REASONS)
