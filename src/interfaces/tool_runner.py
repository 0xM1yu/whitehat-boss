"""ToolRunner 인터페이스 (engineering-guide.md §3·§7)."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import ConfigDict, Field, StrictBytes, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr
from src.schemas.evidence import Evidence


class SandboxSpec(HarnessModel):
    """Docker sandbox 실행 조건.

    기본값이 곧 정책이다(engineering-guide.md §7): 네트워크 차단, 읽기 전용 마운트,
    CPU·메모리·시간 상한. 상한을 0이나 음수로 두면 격리가 무의미해지므로 `gt=0`이다.
    `network=True`는 disclosure-policy.md의 "외부 시스템 실행 금지"와 충돌할 수 있어
    호출부에서 명시적으로 켜야만 켜진다.
    """

    image: NonEmptyStr
    network: bool = False
    read_only: bool = True
    cpu_limit: float = Field(gt=0)
    memory_mb: int = Field(gt=0)
    timeout_s: int = Field(gt=0)


# argv 원소가 이 문자열과 **같을 때만** 자리표다(부분 문자열은 아니다). 모델이 쓴
# `repro_spec.steps`가 argv에 그대로 들어가는 자리(`validate._call`)가 있어서, 자리표를 품은 자유
# 문장이 검증에 걸려 run을 죽이면 안 된다 — 정확히 같은 원소는 `validate._call`이 요청을 만들기
# 전에 거절한다. 이름이 긴 것은 모델·파일 이름이 우연히 같아지지 않게 하려는 것이다.
INPUT_PLACEHOLDER = "{harness_input}"


class ToolArgs(HarnessModel):
    """샌드박스 안에서 실행할 명령.

    쉘 문자열이 아니라 argv 배열이다 — 문자열로 받으면 대상 파일명에 들어간 메타문자가
    쉘에 해석된다.

    `input_bytes`는 도구 **요청에** 데이터로 싣는 바이트다(PoV의 PoC). 어댑터가 자기 namespace에
    **쓴다** — 컨테이너는 그 도구의 scratch에, fake는 임시 디렉터리에 쓰고 바로 지운다. 자리표
    (`INPUT_PLACEHOLDER`)를 경로로 바꾸는 곳은 **컨테이너의 로그 머리(`# node argv:`)뿐**이다 —
    노드는 컨테이너 안 경로를 모르고, fake는 argv를 안 바꾼다. 지금은 어느 어댑터도 그 파일을
    명령의 인자로 안 넘긴다(#513). **자리표와 바이트는 함께 있어야 한다**: 자리표만 있으면 로그
    머리에 안 풀린 자리표가 남고, 바이트만 있으면 로그 머리가 쓴 경로를 알릴 자리가 없다.
    `b""`도 입력이다. 원바이트는 `repr`·검증 오류 문구에서 뺀다 — 8 MiB까지 갈 수 있는 PoC가
    로그로 새지 않게.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    argv: list[NonEmptyStr] = Field(min_length=1)
    workdir: NonEmptyStr
    env: dict[str, str] = Field(default_factory=dict)
    input_bytes: StrictBytes | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _placeholder_pairs_with_bytes(self) -> "ToolArgs":
        has_placeholder = INPUT_PLACEHOLDER in self.argv
        if has_placeholder and self.input_bytes is None:
            raise ValueError(
                f"argv에 자리표 {INPUT_PLACEHOLDER!r}가 있는데 input_bytes가 없다 — "
                "바이트를 싣거나 자리표를 argv에서 뺀다"
            )
        if self.input_bytes is not None and not has_placeholder:
            raise ValueError(
                f"input_bytes가 있는데 argv에 자리표 {INPUT_PLACEHOLDER!r}가 없다 — "
                "어댑터가 바이트를 둔 경로를 알릴 자리가 없다. argv에 자리표를 넣는다"
            )
        return self


def with_input_path(argv: Sequence[str], path: str) -> list[str]:
    """`argv`의 자리표 원소를 `path`로 바꾼 새 목록 — 컨테이너가 로그 머리의 노드 argv를 만든다."""
    return [path if arg == INPUT_PLACEHOLDER else arg for arg in argv]


class ToolRunner(Protocol):
    """정적분석·build·sanitizer·fuzz·PoC 실행.

    **실패해도 예외를 던지지 않고 `Evidence(ok=False, error=...)`를 반환한다.**
    engineering-guide.md §8.3("도구 실패도 Evidence로 기록")과 architecture.md §3의
    2단계 통과 조건("실패해도 진행")이 그렇게 요구한다. 도구가 실패했다는 사실 자체가
    모델 비교 지표이므로 삼키면 안 된다.

    샌드박스를 띄우지 못하는 등 Evidence조차 만들 수 없는 경우에만 예외를 던진다.
    """

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence: ...
