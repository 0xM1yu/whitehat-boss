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
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import SandboxSpec, ToolArgs, ToolRunner
from src.orchestrator import gate, state_machine
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.gate import GateResult
from src.orchestrator.policy import Action, RoutingPolicy, RoutingRequest
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, StageSpan, TraceWriter
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
    # 대상 심볼의 원문을 읽는다. `ContextCapsule`은 본문을 담지 않는 매니페스트이므로
    # 프롬프트에 실을 코드는 여기서 온다. 구현은 Retriever와 **같은 함수**여야 한다 —
    # 두 곳이 다르게 읽으면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다.
    read_source: Callable[[Location], str] = field(default=_unwired_read_source)
    # 테스트가 고정할 수 있도록 주입한다. `wall_ms`는 재현성 비교에서 제거되는 값이다.
    monotonic_ms: Callable[[], int] = field(default=_default_monotonic_ms)

    # --- 단계 ---

    def stage(self, stage: Stage) -> StageSpan:
        """`with ctx.stage(Stage.SCOUT):` — `stage_start`/`stage_end`를 짝지어 남긴다."""
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
        )
        self.ledger.charge(result.usage, stage=stage)
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
        self.ledger.charge(_NO_TOKENS, stage=stage, gpu_seconds=elapsed // 1000)
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
        self.trace.emit(EventKind.CACHE, cache=cache, key=key, hit=hit)

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
