"""도구 실행 샌드박스 기본값 (engineering-guide.md §7).

**기본값이 곧 정책이다**: 네트워크 차단, 읽기 전용 마운트, CPU·메모리·시간 상한.
`network=True`는 disclosure-policy.md의 "외부 시스템 실행 금지"와 충돌하므로 호출부가
명시적으로 켜야만 켜지고, ToolRunner는 그 경우 실행 없이 `ok=False`로 기록한다.

여기 있는 수치는 판정 임계값이 아니라 **아직 실측하지 않은 자원 상한**이라 config가
아니라 코드에 있다. Docker 실행이 실제로 들어오면 실측값을 `budgets.yaml`로 옮기고 이
상수를 지운다 — 그때는 조건 간 비교에 영향을 주는 값이 되기 때문이다.
"""

from src.interfaces.tool_runner import SandboxSpec

DEFAULT_SANDBOX = SandboxSpec(
    image="harness-tools:v1",
    network=False,
    read_only=True,
    cpu_limit=1.0,
    memory_mb=2048,
    timeout_s=300,
)
