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
        # **밀리초로 누산한다.** 초로 받으면 호출 지점마다 내림이 일어나 1초 미만 호출이
        # 통째로 0이 된다 — 2026-08-17 실측에서 도구 11건의 `wall_ms` 합계 8,492 ms가
        # 장부에 3초로 남았고 그중 8건(342~906 ms)이 0으로 청구됐다. 같은 도구를 같은
        # 순서로 돈 세 run이 3/6/8초를 남겼다: 실제 차이는 1.6배인데 장부는 2.7배였다 —
        # 내림이 기계 노이즈를 증폭했다.
        #
        # 단위 변환은 여기 한 곳에서만 한다. 호출 지점에서 나누면 지점이 늘 때마다 같은
        # 결함이 다시 생긴다 — 이 결함이 정확히 그렇게 생겼다(세 지점이 각자 내림했고,
        # 모델 경로는 아예 인자가 없었다).
        self.gpu_ms = 0
        # `gpu_ms=None`으로 온 호출 수 — **"GPU를 안 썼다"가 아니라 "안 쟀다"의 개수다.**
        # Fake 어댑터가 여기 들어온다. 실서빙 역할에서 이 값이 0이 아니면 그 run의
        # GPU 비용은 미상이고, `runner.incomplete_fields()`가 그 사실로 판정한다.
        self.unmeasured_gpu_calls = 0

    @property
    def gpu_seconds(self) -> int:
        """사람이 읽는 파생값. **판정과 누산은 언제나 `gpu_ms`가 한다.**"""
        return self.gpu_ms // 1000

    @property
    def budget(self) -> Budget:
        """`RunRecord.budget`에 박제할 상한. 원장은 이 값을 바꾸지 않는다."""
        return self._budget

    def _overrun(self) -> tuple[str, int, int] | None:
        """넘긴 항목 (이름, 소비, 상한). 상한이 0인 항목은 검사하지 않는다.

        `dev_fake` 예산의 `gpu_seconds: 0`이 "Fake는 GPU를 안 쓴다"는 뜻이지
        "0초만 허용"이 아니기 때문이다.
        """
        # **비교는 밀리초로, 보고는 초로.** 파생값(초)으로 비교하면 상한 직전의 999 ms가
        # 반복해서 버려져 상한이 실제보다 늦게 걸린다. 반대로 메시지에 ms를 그대로 내면
        # `gpu_seconds 8492/7200000`처럼 이름과 단위가 어긋난다 — 마지막 항목의 나눗수가
        # 그 변환이다.
        checks = (
            ("input_tokens", self.input_tokens, self._budget.input_tokens, 1),
            ("output_tokens", self.output_tokens, self._budget.output_tokens, 1),
            ("gpu_seconds", self.gpu_ms, self._budget.gpu_seconds * 1000, 1000),
        )
        for item, spent, limit, per_unit in checks:
            if limit > 0 and spent >= limit:
                return item, spent // per_unit, limit // per_unit
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

    def charge(self, usage: Usage, *, stage: int, gpu_ms: int | None = 0) -> None:
        """이미 일어난 소비를 기록한다. 넘겨도 되돌리지 않고 `budget` 이벤트만 남긴다.

        `cached_tokens`는 `input_tokens`의 부분집합이므로 따로 더하지 않는다
        (interfaces/model_client.py `Usage`). 캐시 효과는 `model_call` 이벤트에서
        `Σ cached / Σ input`으로 따로 계산한다.

        **`gpu_ms=None`은 0으로 더하지 않는다.** `None`은 "안 쟀다"이고 0은 "GPU를 안
        썼다"라, 더해 버리면 두 사실이 합쳐져 나중에 구분할 수 없다. 미측정 사실은
        `unmeasured_gpu_calls`가 따로 센다 — 그 값이 `incomplete_fields()`의 입력이다.
        """
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        if gpu_ms is None:
            self.unmeasured_gpu_calls += 1
        else:
            self.gpu_ms += gpu_ms
        overrun = self._overrun()
        self._trace.emit(
            EventKind.BUDGET,
            stage=stage,
            event="exhausted" if overrun else "charge",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            # 초와 밀리초를 **둘 다** 남긴다. 초만 남기면 이 결함이 그대로 돌아온다 —
            # 사후 집계가 볼 수 있는 유일한 정확값이 ms다.
            gpu_seconds=self.gpu_seconds,
            gpu_ms=self.gpu_ms,
            item=overrun[0] if overrun else None,
        )

    def remaining(self) -> dict[str, int]:
        """항목별 잔량. 음수가 되지 않게 자른다 — 리포트에서 읽는 값이다."""
        return {
            "input_tokens": max(0, self._budget.input_tokens - self.input_tokens),
            "output_tokens": max(0, self._budget.output_tokens - self.output_tokens),
            # 잔량도 ms로 뺀 뒤 내림한다. 초끼리 빼면 소비 쪽이 먼저 내림되어 **잔량이
            # 실제보다 최대 1초 많게** 나온다 — 리포트에서 읽는 값이라 낙관적으로 틀리는
            # 방향이다.
            "gpu_seconds": max(0, self._budget.gpu_seconds * 1000 - self.gpu_ms) // 1000,
        }
