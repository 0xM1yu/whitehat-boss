"""RunContext — 노드가 보는 실행 문맥.

노드 시그니처는 `def run(ctx: RunContext, ...) -> ...`이고 LangGraph 타입을 노출하지
않는다(open-questions.md D7 미확정, ADR 0002). 오케스트레이터를 갈아 끼워도 노드가
그대로 남으려면 노드가 아는 것이 이 객체 하나여야 한다.

**부작용은 전부 여기를 지난다.** 모델 호출·도구 실행·게이트 판정·정책 결정·상태 전이가
각각 대응하는 trace 이벤트와 짝지어져 있어서, 노드가 이 래퍼를 쓰는 한 기록 누락이
일어나지 않는다. 노드가 `ctx.models.complete()`를 직접 부르면 usage가 예산에 잡히지
않고 `model_call`이 비어 비용 지표가 무너진다 — 그 경로를 만들지 않는 것이 설계다.

`src/adapters/`의 구체 구현을 import 하지 않는다. 의존성은 `src/interfaces/`의 Protocol로만
받으며 어댑터 조립은 `src/adapters/factory.py`가 한다(engineering-guide.md §3).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import perf_counter

from src.config import HarnessConfig
from src.ids import IdFactory
from src.interfaces.model_client import ModelClient, ModelResult, Prompt, SchemaT, Usage
from src.interfaces.retrieval import RetrievalResult
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import SandboxSpec, ToolArgs, ToolRunner
from src.orchestrator import gate, state_machine
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.gate import GateResult
from src.orchestrator.policy import Action, RoutingPolicy, RoutingRequest
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, StageSpan, TraceValue, TraceWriter
from src.schemas.common import Location
from src.schemas.decision import Decision
from src.schemas.enums import FindingState, ReasonCode, Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import RunRecord

# 도구 실행은 토큰을 쓰지 않는다. 매번 만들지 않도록 상수로 둔다.
_NO_TOKENS = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)


def _default_monotonic_ms() -> int:
    return int(perf_counter() * 1000)


def _unwired_read_source(_target: Location) -> str:
    """`read_source`가 배선되지 않은 채 호출됐다. 빈 문자열로 넘기지 않는다.

    조용히 ""를 돌려주면 프롬프트에 원문이 빠진 채로 모델이 호출되고, 그 결과가
    "모델이 못 찾았다"로 집계된다 — 배선 실수가 모델 성능 지표로 둔갑한다.
    """
    raise NotImplementedError(
        "RunContext.read_source가 배선되지 않았다. 파일 읽기 구현(read_symbol_source)은 "
        "어댑터 쪽에 있고 조립부가 주입한다 — 오케스트레이터는 그것을 import 하지 않는다."
    )


@dataclass
class RunContext:
    """한 run의 의존성과 기록 장치.

    `frozen=True`가 아닌 것은 `record`(RunRecord)가 실행 중에 갱신되기 때문이다.
    나머지 필드는 생성 후 바뀌지 않는다.
    """

    run_id: str
    config: HarnessConfig
    models: ModelClient
    retriever: Retriever
    tools: ToolRunner
    store: StateStore
    ids: IdFactory
    trace: TraceWriter
    ledger: BudgetLedger
    policy: RoutingPolicy
    record: RunRecord | None = None
    # **역할별 GPU 미측정 횟수.** 원장이 아니라 여기서 센다 — 원장은 상한 관리자이고,
    # 역할 차원을 넣으면 "역할별 상한"이 필요하다는 압력이 생긴다. evaluation.md §5.2가
    # 요구하는 것은 역할별 **지표**이지 역할별 상한이 아니다. 역할을 아는 곳이 여기이고,
    # 이 값은 `runner.unmeasured_gpu_warning()`의 입력이 된다.
    gpu_unmeasured_roles: dict[str, int] = field(default_factory=dict)
    # 마지막으로 진입한 단계. Retriever 콜백이 자기 단계를 모르므로 여기서 채운다.
    current_stage: Stage = Stage.PREPROCESS
    # 대상 심볼의 원문을 읽는다. `ContextCapsule`은 본문을 담지 않는 매니페스트이므로
    # 프롬프트에 실을 코드는 여기서 온다. 구현은 Retriever와 **같은 함수**여야 한다 —
    # 두 곳이 다르게 읽으면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다.
    read_source: Callable[[Location], str] = field(default=_unwired_read_source)
    # 테스트가 고정할 수 있도록 주입한다. `wall_ms`는 재현성 비교에서 제거되는 값이다.
    monotonic_ms: Callable[[], int] = field(default=_default_monotonic_ms)

    # --- 단계 ---

    def stage(self, stage: Stage) -> StageSpan:
        """`with ctx.stage(Stage.SCOUT):` — `stage_start`/`stage_end`를 짝지어 남긴다.

        진입한 단계를 기억한다. Retriever의 콜백(`on_cache`·`on_usage`)은 자기가 몇
        단계에서 불렸는지 모르는데, 캡슐 캐시는 3·5·7단계에서 모두 발생하고 검색 비용은
        `ledger.charge(stage=)`가 단계를 요구한다. 단계 없이 남기면 "3단계 적중률" 같은
        질문에 답할 수 없다.

        v1 전제: 러너가 순차 실행이라 "마지막에 진입한 단계"로 충분하다(계획 §5.1
        "동시성 없음이 v1 전제다" — `trace.py`도 같은 전제 위에 있다). 단계를 동시에
        돌리게 되면 콜백에 단계를 명시적으로 실어 보내야 한다.
        """
        self.current_stage = stage
        return self.trace.stage_span(stage, monotonic_ms=self.monotonic_ms)

    # --- 모델 ---

    def call_model(
        self,
        *,
        role: Role,
        prompt: Prompt,
        schema: type[SchemaT],
        stage: Stage,
        subject_kind: str | None = None,
        subject_id: str | None = None,
    ) -> ModelResult[SchemaT]:
        """모델 호출 + usage 기록 + 예산 차감 + trace. 노드는 이것만 쓴다.

        실패해도 `model_call`을 남기고 예외를 그대로 올린다 — "스키마 파싱 실패율"의
        분자가 여기서 나오므로 삼키면 지표가 0이 된다(evaluation.md §5.1).
        예산 소진이면 호출 전에 `BudgetExhausted`가 나므로 토큰을 쓰지 않는다.
        """
        self.ledger.ensure(stage)
        started = self.monotonic_ms()
        try:
            result = self.models.complete(role=role, prompt=prompt, schema=schema)
        except Exception as exc:
            self.trace.emit(
                EventKind.MODEL_CALL,
                stage=stage,
                subject_kind=subject_kind,
                subject_id=subject_id,
                wall_ms=self.monotonic_ms() - started,
                role=role.value,
                model=self.config.model_key_for(role.value),
                prompt_version=prompt.prompt_version,
                schema=schema.__name__,
                ok=False,
                error=type(exc).__name__,
            )
            raise
        self.trace.emit(
            EventKind.MODEL_CALL,
            stage=stage,
            subject_kind=subject_kind,
            subject_id=subject_id,
            wall_ms=self.monotonic_ms() - started,
            role=role.value,
            model=result.model,
            prompt_version=prompt.prompt_version,
            schema=schema.__name__,
            ok=True,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cached_tokens,
            # GPU 시간과 **그 출처**를 함께 남긴다. 라벨이 기록에 없으면 반년 뒤 이 수치가
            # wall-clock 근사였다는 사실을 데이터만 보고 알 수 없다
            # (`ModelResult.gpu_ms_source` 주석 — 순차 실행 전제에 의존한다).
            gpu_ms=result.gpu_ms,
            gpu_ms_source=result.gpu_ms_source,
        )
        # 모델 호출은 지금까지 GPU 시간을 **한 번도 청구하지 않았다** — `ModelResult`에 그
        # 값이 없었기 때문이다(2026-08-17 확인). GPU를 가장 많이 쓰는 경로가 장부에 0으로
        # 남았고, 그래서 `gpu_seconds` 상한이 한 번도 걸린 적이 없다.
        if result.gpu_ms is None:
            # 세는 것은 "GPU를 안 썼다"가 아니라 **"안 쟀다"**다. Fake 모델이 여기 들어오고,
            # 러너가 실서빙 역할과 교집합해서 비교표 제외 판정을 낸다.
            name = role.value
            self.gpu_unmeasured_roles[name] = self.gpu_unmeasured_roles.get(name, 0) + 1
        self.ledger.charge(result.usage, stage=stage, gpu_ms=result.gpu_ms)
        return result

    # --- 도구 ---

    def call_tool(
        self,
        *,
        tool: str,
        args: ToolArgs,
        sandbox: SandboxSpec,
        stage: Stage,
        subject_id: str | None = None,
    ) -> Evidence:
        """도구 실행 + trace. ToolRunner는 실패해도 `Evidence(ok=False)`를 돌려준다."""
        self.ledger.ensure(stage)
        started = self.monotonic_ms()
        evidence = self.tools.run(tool, args, sandbox)
        elapsed = max(0, self.monotonic_ms() - started)
        self.store.put(evidence)
        self.trace.emit(
            EventKind.TOOL_CALL,
            stage=stage,
            subject_kind="evidence",
            subject_id=evidence.evidence_id,
            wall_ms=elapsed,
            tool=tool,
            argv=" ".join(args.argv),
            ok=evidence.ok,
            exit_code=evidence.exit_code,
            error=evidence.error,
            finding_id=subject_id,
        )
        # **밀리초를 그대로 넘긴다.** 여기서 `// 1000`을 하던 것이 이 결함의 세 지점 중
        # 하나였다 — 도구 11건 중 8건(342~906 ms)이 0으로 청구됐다(2026-08-17 실측).
        # 단위 변환은 원장이 한다.
        #
        # **단, Fake 런타임의 시간은 청구하지 않는다.** `FakeToolRunner`의 1~2 ms는 GPU도
        # 컨테이너도 아니고 **하네스가 자기 자신을 잰 값**이다. 그것을 장부에 넣으면 무의미한
        # 숫자가 비용 지표에 섞이고, 같은 입력의 두 실행이 다른 기록을 남긴다. 예전에는
        # `// 1000`이 그 값을 전부 0으로 만들어 **내림이 재현성을 우연히 지켜 주고
        # 있었다** — 이 분기가 그 우연을 의도로 바꾼다.
        #
        # 판단 기준은 새로 만들지 않았다. `doctor`가 이미 같은 식으로 갈라
        # "tool_runner가 Fake라 컨테이너 런타임이 필요 없다"를 판정한다(`cli._check_tool_runtime`).
        # 어댑터를 import 하지 않고 config를 읽는 것도 이 파일의 기존 방식 그대로다
        # (`self.config.model_key_for(...)`).
        #
        # **`--tool-runner` 오버라이드와 갈라질 수 없다.** 오버라이드는 `cli.load_profile()`이
        # config를 만들 때 이미 반영하고(`adapters.transition(tool_runner=...)`), 러너는
        # `RunRecord.tool_runner`에 **같은 식**을 박는다. 여기서 읽는 값과 기록에 남는 값이
        # 같은 표현이라 어긋날 수 없다 — 오버라이드가 나중에 적용되는 구조였다면 이 분기가
        # 조용히 틀린 값을 만들었을 것이므로, 그렇지 않은 이유를 여기 남긴다.
        #
        # **반대 선례 하나를 처리해 둔다.** `vllm_retrieval._estimated_usage`는 "Fake라고
        # 0으로 두지 않는다"고 적는다. 구분은 재현성이다 — 토큰은 입력의 결정론적
        # 함수(`len // 4`)라 Fake에서도 같은 입력이면 같은 값이지만, wall-clock은 아니다.
        #
        # **`gpu_ms`가 아니라 `tool_ms`다.** 여기서 도는 네 도구(semgrep·clang-tidy·build·
        # asan)는 GPU를 쓰지 않는다. 2026-08-17까지 이 시간이 `gpu_seconds`로 청구됐고,
        # 그래서 그 필드의 0 아닌 값은 **전부 CPU 시간**이었다 — evaluation.md §5.3의
        # "finding당 GPU-hour"가 그 값을 분자로 쓴다.
        is_fake_runtime = self.config.profile.adapters.tool_runner == "fake"
        self.ledger.charge(_NO_TOKENS, stage=stage, tool_ms=None if is_fake_runtime else elapsed)
        return evidence

    # --- 게이트 ---

    def evaluate_gate(
        self,
        finding: Finding,
        evidences: Sequence[Evidence],
        decisions: Sequence[Decision] = (),
        missings: Sequence[MissingEvidence] = (),
        *,
        stage: Stage,
        candidate_location: Location | None = None,
    ) -> GateResult:
        """순수 판정(gate.evaluate) + `gate_eval`·`gate_ambiguous` 기록.

        `ambiguous`는 기록만 하고 모델을 호출하지 않는다 — 등급은 E1로 확정된다.
        """
        result = gate.evaluate(
            finding, evidences, decisions, missings, candidate_location=candidate_location
        )
        self.trace.emit(
            EventKind.GATE_EVAL,
            stage=stage,
            subject_kind="finding",
            subject_id=finding.finding_id,
            grade=result.grade.value,
            **dict(result.predicates),
        )
        if result.ambiguous:
            self.trace.emit(
                EventKind.GATE_AMBIGUOUS,
                stage=stage,
                subject_kind="finding",
                subject_id=finding.finding_id,
                grade=result.grade.value,
                boundary="E1-E2",
            )
        return result

    def record_critic_normalized(self, decision: Decision, *, stage: Stage) -> None:
        """근거 없는 `rebut` → `uphold` 정규화를 지표에 남긴다 (agent-contracts.md §2.3).

        분모가 `model_call(role=critic)`이므로 판정과 별개의 이벤트로 남긴다.
        """
        self.trace.emit(
            EventKind.CRITIC_NORMALIZED,
            stage=stage,
            subject_kind="decision",
            subject_id=decision.decision_id,
            finding_id=decision.finding_id,
            original_verdict=decision.verdict.value,
        )

    def record_cache(self, key: str, hit: bool, *, cache: str = "capsule") -> None:
        """캡슐 캐시 적중 기록 (knowledge-base.md §6). Retriever의 `on_cache` 훅에 연결된다."""
        self.trace.emit(EventKind.CACHE, stage=self.current_stage, cache=cache, key=key, hit=hit)

    def record_graph_index(self, fields: dict[str, TraceValue]) -> None:
        """1단계 인덱서 산출물의 로드 결과. Retriever의 `on_index` 훅에 연결된다.

        **`unresolved_*`를 캡슐이 아니라 여기로 보낸다.** 그 값들은 저장소 전체의
        스칼라라 후보마다 달라지지 않고 토큰 비용도 없다 — 섹션으로 실으면 텍스트가
        아닌 숫자가 토큰 회계에 들어간다. 반면 "그 저장소에서 E2를 얼마나 믿을지"는
        지표 해석의 입력이고, **여기 남지 않은 사건은 측정할 수 없다**(trace.py).

        `status`가 함께 실리는 것이 요점이다. 인덱서가 안 돈 것(`missing`)과 형식이
        어긋난 것(`unsupported_version`)과 실제로 간선이 0건인 것은 대응이 전부 다르다.

        **dataclass가 아니라 dict를 받는다.** 어댑터 타입을 여기서 import 하면 "노드는
        구체 어댑터를 모른다"가 깨진다(engineering-guide.md §3). 필드 이름을 만드는 쪽은
        `index_graph.IndexSummary.as_trace_fields()`이고 테스트가 그 집합을 고정한다.
        """
        self.trace.emit(EventKind.GRAPH_INDEX, stage=self.current_stage, **fields)

    def record_retrieval(self, component: str, result: RetrievalResult, candidates: int) -> None:
        """검색 호출의 비용을 trace와 예산에 반영한다. Retriever의 `on_usage` 훅에 연결된다.

        `model_call`이 아니라 `retrieval_call`로 남긴다 — trace.py 대응표의 "스키마 파싱
        실패율" 분모가 `model_call` 전체이고, 스키마가 없는 검색 호출이 섞이면 그 값이
        조용히 희석된다. embedding·reranker는 `Role`도 아니다(ADR 0004).
        """
        stage = self.current_stage
        self.trace.emit(
            EventKind.RETRIEVAL_CALL,
            stage=stage,
            component=component,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cached_tokens,
            gpu_ms=result.gpu_ms,
            gpu_ms_source=result.gpu_ms_source,
            candidates=candidates,
        )
        self.ledger.charge(result.usage, stage=stage, gpu_ms=result.gpu_ms)

    # --- 정책과 전이 ---

    def decide(self, request: RoutingRequest) -> Action:
        """정책 판정 + `policy_decision` 기록. 노드는 임계값을 직접 비교하지 않는다."""
        action = self.policy.decide(request)
        self.trace.emit(
            EventKind.POLICY_DECISION,
            stage=request.stage,
            subject_kind=request.subject_kind,
            subject_id=request.subject_id,
            point=request.point.value,
            policy=action.policy,
            route=action.route.value,
            to_state=action.to_state.value if action.to_state else None,
            reason=action.reason_code.value if action.reason_code else None,
            **{f"basis.{key}": value for key, value in action.basis.items()},
        )
        return action

    def apply(
        self,
        subject: state_machine.Subject,
        *,
        to: FindingState,
        reason: ReasonCode,
        stage: Stage,
    ) -> state_machine.Subject:
        """상태 전이. `state_machine.apply()`로만 간다."""
        return state_machine.apply(
            subject, to=to, reason=reason, stage=stage, store=self.store, trace=self.trace
        )

    def apply_action(
        self, subject: state_machine.Subject, action: Action, *, stage: Stage
    ) -> state_machine.Subject:
        """정책이 상태를 정한 경우의 지름길. `to_state`가 없으면 그대로 둔다(EXPAND 등)."""
        if action.to_state is None or action.reason_code is None:
            return subject
        return self.apply(subject, to=action.to_state, reason=action.reason_code, stage=stage)

    def create(
        self,
        subject: state_machine.Subject,
        *,
        reason: ReasonCode | None,
        stage: Stage,
    ) -> state_machine.Subject:
        """신규 객체 저장 + 생성 기록 (전이 표 C-1·F-1)."""
        return state_machine.create(
            subject, reason=reason, stage=stage, store=self.store, trace=self.trace
        )
