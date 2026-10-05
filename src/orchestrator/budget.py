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
        # **컨테이너 도구 시간은 GPU와 다른 칸에 쌓는다.** 2026-08-17까지 이 값이
        # `gpu_seconds`로 청구되고 있었는데, 그때 0이 아닌 값을 만든 네 도구(semgrep·
        # clang-tidy·build·asan)는 **GPU를 쓰지 않는다.** evaluation.md §5.3의
        # "finding당 GPU-hour"가 그 값을 분자로 쓰므로 섞인 채로 두면 핵심 비용 지표가
        # CPU 시간으로 오염된다.
        self.tool_ms = 0
        # `gpu_ms=None`으로 온 호출 수 — **"GPU를 안 썼다"가 아니라 "안 쟀다"의 개수다.**
        # Fake 어댑터가 여기 들어온다.
        #
        # ## 지금 이 값을 읽는 곳은 없다. 지우지 마라
        #
        # **`incomplete_fields()`의 입력이 아니다.** 오래 그렇게 적혀 있었지만 거짓이었고,
        # 실제 입력은 `RunContext.gpu_unmeasured_roles`다 — 그쪽은 **모델 호출만** 역할별로
        # 세고 `runner`가 실서빙 역할과 교집합해서 넘긴다.
        #
        # 그래도 남기는 이유: 지워도 `charge()`의 `if gpu_ms is None` 분기는 그대로라
        # **동작** 구별을 잃는 것은 아니지만, **그 횟수의 관측 가능성**을 잃는다. 원장이
        # "몇 번을 못 쟀는지" 자체를 모르게 된다.
        #
        # **이대로 게이트에 연결하면 안 된다.** `RunContext.record_retrieval()`이 검색
        # 호출도 `charge()`로 보내고 Fake embedding·reranker는 `gpu_ms=None`을 주므로,
        # 이 값은 **모델 호출과 검색 호출을 섞어 센다.** 판정에 쓰면 Fake 검색이 "미측정
        # GPU"로 잡혀 멀쩡한 `dev_*` run이 매번 비교표에서 빠진다 — checksum 자리표시자
        # 판정에서 Fake 역할을 빼는 것과 같은 논법이다(`incomplete_fields()` 주석).
        # 쓰려면 두 경로를 먼저 갈라야 한다.
        #
        # `budget` 이벤트에 실어 실제로 소비시키는 선택지도 있는데 그것은 **지표 열 추가**라
        # 성격이 다르다. 설명 없는 미소비 값은 다음 사람이 죽은 코드로 지우므로 사유를
        # 여기 남긴다. ⚠ **한때 이 문장이 `nodes/validate.py`의 8단계 게이트 등급을 같은
        # 처리의 선례로 들었다** — 그쪽은 2026-09-20에 소비되기 시작했다(F-10b · #68).
        # 선례가 없어진 것이지 이 자리의 사유가 없어진 것은 아니다.
        self.unmeasured_gpu_calls = 0

    @property
    def gpu_seconds(self) -> int:
        """사람이 읽는 파생값. **판정과 누산은 언제나 `gpu_ms`가 한다.**"""
        return self.gpu_ms // 1000

    @property
    def tool_seconds(self) -> int:
        """`gpu_seconds`와 같은 파생값. 누산과 판정은 `tool_ms`가 한다."""
        return self.tool_ms // 1000

    @property
    def budget(self) -> Budget:
        """`RunRecord.budget`에 박제할 상한. 원장은 이 값을 바꾸지 않는다."""
        return self._budget

    def _overrun(self) -> tuple[str, int, int] | None:
        """넘긴 항목 (이름, 소비, 상한). 상한이 0인 항목은 검사하지 않는다.

        `dev_fake` 예산의 `gpu_seconds: 0`이 "Fake는 GPU를 안 쓴다"는 뜻이지
        "0초만 허용"이 아니기 때문이다.

        **실서빙 프로필의 0은 여기서 안 막는다 — 로드에서 막는다.** 이 원장은 자기 모델이 실서빙인지
        모르므로, 실모델을 서빙하는 프로필의 예산에 0이 있으면 `load_config`가 거부한다
        (`config._check_real_serving_limits_are_positive`). 그래서 `load_config`를 지나 여기 오는
        0은 Fake 전용 프로필의 것이다.
        """
        # **비교는 밀리초로, 보고는 초로.** 파생값(초)으로 비교하면 상한 직전의 999 ms가
        # 반복해서 버려져 상한이 실제보다 늦게 걸린다. 반대로 메시지에 ms를 그대로 내면
        # `gpu_seconds 8492/7200000`처럼 이름과 단위가 어긋난다 — 마지막 항목의 나눗수가
        # 그 변환이다.
        checks = (
            ("input_tokens", self.input_tokens, self._budget.input_tokens, 1),
            ("output_tokens", self.output_tokens, self._budget.output_tokens, 1),
            ("gpu_seconds", self.gpu_ms, self._budget.gpu_seconds * 1000, 1000),
            ("tool_seconds", self.tool_ms, self._budget.tool_seconds * 1000, 1000),
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

    def charge(
        self, usage: Usage, *, stage: int, gpu_ms: int | None = 0, tool_ms: int | None = 0
    ) -> None:
        """이미 일어난 소비를 기록한다. 넘겨도 되돌리지 않고 `budget` 이벤트만 남긴다.

        `cached_tokens`는 `input_tokens`의 부분집합이므로 따로 더하지 않는다
        (interfaces/model_client.py `Usage`). 캐시 효과는 `model_call` 이벤트에서
        `Σ cached / Σ input`으로 따로 계산한다.

        **`gpu_ms=None`은 0으로 더하지 않는다.** `None`은 "안 쟀다"이고 0은 "GPU를 안
        썼다"라, 더해 버리면 두 사실이 합쳐져 나중에 구분할 수 없다. 미측정 사실은
        `unmeasured_gpu_calls`가 따로 센다 — **그 값을 읽는 곳은 아직 없고**, 판정을
        하는 것은 `RunContext.gpu_unmeasured_roles`다(그 필드 주석).
        """
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        if gpu_ms is None:
            self.unmeasured_gpu_calls += 1
        else:
            self.gpu_ms += gpu_ms
        # `tool_ms`도 같은 규칙이다. Fake 런타임의 도구 시간이 `None`으로 오는데, 그것은
        # GPU도 컨테이너도 아니라 **하네스가 자기를 잰 값**이라 장부에 넣을 대상이 아니다
        # (`RunContext.call_tool`에 그 판단과 근거가 있다). 미측정 개수는 따로 세지 않는다 —
        # 도구 시간은 재현성 기록이 아니라 상한이고, Fake 런타임이면 `tool_images`가 이미
        # "컨테이너 없이 돌았다"를 판정한다(`incomplete_fields`).
        if tool_ms is not None:
            self.tool_ms += tool_ms
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
            tool_ms=self.tool_ms,
            item=overrun[0] if overrun else None,
        )

    def remaining(self) -> dict[str, int | None]:
        """항목별 잔량. 음수가 되지 않게 자른다 — 리포트에서 읽는 값이다.

        **상한 0인 항목은 `None`이다** -> 0은 `_overrun`이 검사하지 않는 값(상한 없음)인데
        잔량 0으로 내면 「소진」으로 읽힌다(#483 · `estimate` 출력의 같은 오독이 #435).
        `0`은 상한이 있고 다 쓴 경우에만 나온다.
        """

        def left(limit: int, spent: int, per_unit: int) -> int | None:
            if limit == 0:
                return None
            # 잔량도 ms로 뺀 뒤 내림한다. 초끼리 빼면 소비 쪽이 먼저 내림되어 **잔량이
            # 실제보다 최대 1초 많게** 나온다 — 리포트에서 읽는 값이라 낙관적으로 틀리는
            # 방향이다.
            return max(0, limit * per_unit - spent) // per_unit

        return {
            "input_tokens": left(self._budget.input_tokens, self.input_tokens, 1),
            "output_tokens": left(self._budget.output_tokens, self.output_tokens, 1),
            "gpu_seconds": left(self._budget.gpu_seconds, self.gpu_ms, 1000),
            "tool_seconds": left(self._budget.tool_seconds, self.tool_ms, 1000),
        }
