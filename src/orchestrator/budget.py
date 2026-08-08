"""Budget Ledger (architecture.md §5, evaluation.md §2.3).

evaluation.md §2.3의 공정성 규칙: 모든 조건은 **동일 예산**에서 실행한다. 그래서 상한은
config의 `Budget`에서만 오고(`budgets.yaml`), 원장은 그 값을 복사해 들고 있는다 —
실행 중에 config를 다시 읽으면 조건 간 비교가 흔들린다.

**소진 판정과 과금은 분리한다.** `ensure()`는 단계에 들어가기 전에 묻는 것이고,
`charge()`는 이미 일어난 소비를 기록하는 것이다. 마지막 호출이 상한을 넘겨도 그
호출은 이미 토큰을 썼으므로 되돌리지 않는다 — 넘긴 사실을 남기고 다음 `ensure()`에서
막는다. 쓰지 않은 것처럼 기록하면 비용 지표가 실제보다 작아진다.

소진되면 `BudgetExhausted`를 던지고, 러너가 받아 `state_machine.finish_run()`으로
`stopped_after`를 채운다(전이 표 R-2). 노드는 이 예외를 삼키지 않는다.
"""

from src.interfaces.model_client import Usage
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.run import Budget


class BudgetExhausted(Exception):
    """예산 상한 도달. 어느 항목이 언제 넘었는지를 메시지에 담는다."""

    def __init__(self, stage: int, item: str, spent: int, limit: int) -> None:
        super().__init__(f"{stage}단계 진입 불가: {item} {spent}/{limit} 소진")
        self.stage = stage
        self.item = item
        self.spent = spent
        self.limit = limit


class BudgetLedger:
    """run 하나의 토큰·GPU 시간 소비 원장.

    **v1은 순차 실행 전제다.** 누산에 락이 없다. 큐를 스레드로 돌리게 되면 여기와
    TraceWriter·StateStore·FakeModelClient의 전제를 함께 풀어야 한다(계획 §5.1).
    """

    def __init__(self, budget: Budget, trace: TraceWriter) -> None:
        self._budget = budget
        self._trace = trace
        self.input_tokens = 0
        self.output_tokens = 0
        self.gpu_seconds = 0

    @property
    def budget(self) -> Budget:
        """`RunRecord.budget`에 박제할 상한. 원장은 이 값을 바꾸지 않는다."""
        return self._budget

    def _overrun(self) -> tuple[str, int, int] | None:
        """넘긴 항목 (이름, 소비, 상한). 상한이 0인 항목은 검사하지 않는다.

        `dev_fake` 예산의 `gpu_seconds: 0`이 "Fake는 GPU를 안 쓴다"는 뜻이지
        "0초만 허용"이 아니기 때문이다.
        """
        checks = (
            ("input_tokens", self.input_tokens, self._budget.input_tokens),
            ("output_tokens", self.output_tokens, self._budget.output_tokens),
            ("gpu_seconds", self.gpu_seconds, self._budget.gpu_seconds),
        )
        for item, spent, limit in checks:
            if limit > 0 and spent >= limit:
                return item, spent, limit
        return None

    @property
    def is_exhausted(self) -> bool:
        return self._overrun() is not None

    def ensure(self, stage: int) -> None:
        """단계에 들어가도 되는지 묻는다. 소진이면 기록하고 예외를 던진다."""
        overrun = self._overrun()
        if overrun is None:
            return
        item, spent, limit = overrun
        self._trace.emit(
            EventKind.BUDGET,
            stage=stage,
            event="blocked",
            item=item,
            spent=spent,
            limit=limit,
        )
        raise BudgetExhausted(stage, item, spent, limit)

    def charge(self, usage: Usage, *, stage: int, gpu_seconds: int = 0) -> None:
        """이미 일어난 소비를 기록한다. 넘겨도 되돌리지 않고 `budget` 이벤트만 남긴다.

        `cached_tokens`는 `input_tokens`의 부분집합이므로 따로 더하지 않는다
        (interfaces/model_client.py `Usage`). 캐시 효과는 `model_call` 이벤트에서
        `Σ cached / Σ input`으로 따로 계산한다.
        """
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.gpu_seconds += gpu_seconds
        overrun = self._overrun()
        self._trace.emit(
            EventKind.BUDGET,
            stage=stage,
            event="exhausted" if overrun else "charge",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            gpu_seconds=self.gpu_seconds,
            item=overrun[0] if overrun else None,
        )

    def remaining(self) -> dict[str, int]:
        """항목별 잔량. 음수가 되지 않게 자른다 — 리포트에서 읽는 값이다."""
        return {
            "input_tokens": max(0, self._budget.input_tokens - self.input_tokens),
            "output_tokens": max(0, self._budget.output_tokens - self.output_tokens),
            "gpu_seconds": max(0, self._budget.gpu_seconds - self.gpu_seconds),
        }
