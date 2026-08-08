"""ToolRunner 인터페이스 (engineering-guide.md §3·§7)."""

from typing import Protocol

from pydantic import Field

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


class ToolArgs(HarnessModel):
    """샌드박스 안에서 실행할 명령.

    쉘 문자열이 아니라 argv 배열이다 — 문자열로 받으면 대상 파일명에 들어간 메타문자가
    쉘에 해석된다.
    """

    argv: list[NonEmptyStr] = Field(min_length=1)
    workdir: NonEmptyStr
    env: dict[str, str] = Field(default_factory=dict)


class ToolRunner(Protocol):
    """정적분석·build·sanitizer·fuzz·PoC 실행.

    **실패해도 예외를 던지지 않고 `Evidence(ok=False, error=...)`를 반환한다.**
    engineering-guide.md §8.3("도구 실패도 Evidence로 기록")과 architecture.md §3의
    2단계 통과 조건("실패해도 진행")이 그렇게 요구한다. 도구가 실패했다는 사실 자체가
    모델 비교 지표이므로 삼키면 안 된다.

    샌드박스를 띄우지 못하는 등 Evidence조차 만들 수 없는 경우에만 예외를 던진다.
    """

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence: ...
