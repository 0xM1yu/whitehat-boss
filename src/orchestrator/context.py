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
from src.interfaces.model_client import (
    ModelClient,
    ModelResult,
    Prompt,
    SchemaT,
    Usage,
    error_detail,
)
from src.interfaces.retrieval import RetrievalResult
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import SandboxSpec, ToolArgs, ToolRunner
from src.orchestrator import gate, state_machine
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.gate import GateResult
from src.orchestrator.policy import Action, RoutingPolicy, RoutingRequest
from src.orchestrator.prompt_log import PromptLog
from src.orchestrator.stages import GATE_STAGES, Stage
from src.orchestrator.trace import EventKind, StageSpan, TraceValue, TraceWriter
from src.schemas.common import Location, ProducedBy
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
    # file → 그 파일에 정의된 심볼 위치. 러너가 후보 목록을 만든다(W2).
    #
    # `read_source`와 달리 **미배선 기본값이 예외가 아니라 `None`("인덱스 없음")이다.**
    # 그쪽은 조용히 ""를 돌려주면 배선 실수가 모델 성능 지표로 둔갑하지만, 이쪽의
    # `None`은 실제로 일어나는 정상 상태이고(`dev_fake`) 러너가 파일 단위 폴백으로
    # 처리하며 그 사실을 `target_plan.fallback_artifacts`에 남긴다.
    symbols_for: Callable[[str], list[Location] | None] = field(
        default=lambda _file: None
    )
    # 역할 → 그 역할 모델의 실 토크나이저 카운터 (D21 (b)). `None`이면 기본 카운터
    # (글자수 근사 `len//4`)를 유지한다. `symbols_for`와 같은 성격 — 어댑터가 만들고
    # factory가 주입하며, 러너가 역할 캡슐 조립 직전에
    # `using_counter(ctx.counter_for_role(role))`로 켠다. RunContext는 `src/adapters/`를
    # import 하지 않으므로 콜러블로 받는다. **미배선 기본값이 "토크나이저 없음"**인 것은
    # 의도다 — 이 필드 없이 만든 RunContext(단위 테스트)가 옛 동작을 그대로 남긴다.
    counter_for_role: Callable[[str], Callable[[str], int] | None] = field(
        default=lambda _role: None
    )
    # 렌더된 프롬프트를 run 산출물에 남기는 곳 (D19). `read_source`와 마찬가지로 조립부가
    # 주입한다 — run_dir을 아는 것은 러너이고 노드도 이 객체도 그것을 몰라야 한다.
    #
    # **`None`이면 아무것도 쓰지 않고 `model_call`도 그대로다.** 산출물 디렉터리가 없는
    # 호출 경로(단위 테스트, `--dry-run`)에서 빈 참조를 남기면 "안 썼다"와 "쓸 곳이
    # 없었다"가 구별되지 않는다.
    prompts: PromptLog | None = None
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

        **실패 이벤트에 클래스 이름만 싣던 것이 zlib에서 조사를 막았다.** vLLM이 컨텍스트
        초과를 응답 본문에만 담는데(`ModelGatewayError.detail`) 기록에는 `error`
        문자열 하나뿐이라, 무엇이 얼마나 넘쳤는지가 산출물 어디에도 없었다.
        `_error_detail()`이 잘라낸 상세를 함께 싣는다.
        """
        self.ledger.ensure(stage)
        # **예산 확인 뒤, 시계 시작 전.** 뒤인 이유는 `ensure`가 막은 호출에는 대응하는
        # `model_call`이 없어서 파일만 남으면 개수가 어긋나기 때문이고, 앞인 이유는 이
        # 파일 쓰기가 모델 지연(`wall_ms`)에 섞이면 안 되기 때문이다.
        prompt_fields = self._record_prompt(role=role, stage=stage, prompt=prompt)
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
                **prompt_fields,
                schema=schema.__name__,
                ok=False,
                error=type(exc).__name__,
                error_detail=error_detail(exc),
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
            **prompt_fields,
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
        # **`produced_by`를 하네스가 stamp한다.** 그것은 어느 역할·모델·프롬프트 버전이
        # 만들었는지(evaluation.md §2.3 조건 비교의 기반)인데, 모델은 자기 model 이름도
        # prompt_version도 모른다. 그런데 `produced_by`가 Finding·Decision의 필수 필드라
        # 실서빙에서는 모델이 아무 값이나 채워 넣었다 — Fake는 하네스가 채우는데(fake_model)
        # 그 비대칭이 결함이었다(윤경 지적). 여기서 덮어 실서빙도 Fake와 같아진다.
        #
        # `produced_by`가 있는 스키마(Finding·Decision)만 덮는다. `model`은 `result.model`을
        # 쓴다 — fallback이 동작하면 요청 모델과 다를 수 있고 그것이 실제로 응답한 모델이다
        # (`ModelResult.model` 주석). Fake는 `result.model`이 이미 `model_name`이라 무변경이다.
        #
        # **`model_copy(update=)`는 저장소가 두 곳에서 금지한 호출인데(`common.py`
        # `HarnessModel` docstring · `finding.py` `Finding` docstring, 둘 다 Finding을 이름으로
        # 지목한다) 이 자리에서만 안전하다.** 금지의 이유는 그것이 `model_validator`를
        # 우회한다는 것인데, `produced_by`를 읽는 검증기가 없다 —
        # `Finding._promoted_states_need_backing`이 보는 것은 `state`·`repro_spec`·
        # `evidence_refs`·`path`뿐이고 `Decision`에는 `model_validator` 자체가 없다. 들어오는
        # 값도 파싱 시점에 이미 검증됐다. **상태 전이는 여기서도 예외가 아니다** — `state`를
        # 이 방식으로 바꾸면 검증기가 조용히 우회되므로 `transition()`을 써야 한다.
        # `transition()`을 여기 쓰지 않는 것은 반환 타입이 `HarnessModel`이라
        # `ModelResult[SchemaT]`의 `SchemaT`가 죽기 때문이고, 그 비용을 질 이유가 없다.
        if "produced_by" in type(result.value).model_fields:
            stamped = result.value.model_copy(
                update={
                    "produced_by": ProducedBy(
                        role=role, model=result.model, prompt_version=prompt.prompt_version
                    )
                }
            )
            result = result.model_copy(update={"value": stamped})
        return result

    def _record_prompt(
        self, *, role: Role, stage: Stage, prompt: Prompt
    ) -> dict[str, TraceValue]:
        """프롬프트를 산출물로 남기고 `model_call`에 실을 참조를 돌려준다 (D19).

        **`prompts`가 없으면 빈 dict다.** `prompt_ref=None`을 늘 싣지 않는 것은 그러면
        기록 장치가 없던 run과 프롬프트를 못 쓴 run이 trace에서 같은 모양이 되기
        때문이다 — `graph_index`가 "없다"와 "0건"을 가르는 것과 같은 이유다.

        본문은 trace에 싣지 않는다. 그 판단의 근거는 `prompt_log` 모듈 docstring에 있다.
        """
        if self.prompts is None:
            return {}
        ref, digest = self.prompts.write(role=role.value, stage=int(stage), prompt=prompt)
        return {"prompt_ref": ref, "prompt_sha256": digest}

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

        **`stage`는 `GATE_STAGES` 안이어야 한다.** E3은 Critic 출력이, E4는 도구 검증
        로그가 있어야 성립하므로 게이트는 그 세 단계에서만 뜻이 있다. 그 밖에서 부르면
        아직 성립할 수 없는 술어를 거짓으로 기록하게 되고, 그 행이 제거율의 분모에
        섞인다. 여기서 막지 않으면 `GATE_STAGES`는 **아무도 안 보는 상수**가 된다 —
        실제로 그랬고, 그동안 8단계에는 호출이 아예 없었다(W3).
        """
        if stage not in GATE_STAGES:
            raise ValueError(
                f"게이트는 {[int(s) for s in GATE_STAGES]}단계에서만 판정한다: {int(stage)}"
            )
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

    def record_token_counter(self, fields: dict[str, TraceValue]) -> None:
        """이 run이 어느 토큰 카운터로 셌는가 (D21 (b)). **run당 정확히 1건이다.**

        `record_graph_index`와 같은 모양이다 — 어댑터 타입이 아니라 **스칼라 dict**만 경계를
        넘는다(engineering-guide.md §3). 필드를 만드는 쪽은 `factory.counter_report()`이고
        테스트가 그 키 집합을 고정한다.

        **`graph_index`와 다른 점은 발행 시점이다.** 그쪽은 Retriever가 그래프를 처음 조회할
        때 훅으로 실려 0건일 수 있는데, 카운터는 `build_deps` 시점에 이미 정해져 있으므로
        **모델을 부르기 전에 무조건 낸다**(`target_plan`과 같은 계약). 그 뒤 어디서 죽어도
        "무엇으로 쟀는가"는 남는다.

        이 이벤트가 없던 동안에는 폴백이 조용했다 — `tokenizers`가 없거나 `tokenizer.json`이
        없으면 글자수 근사로 돌면서 trace에도 `RunRecord`에도 아무것도 안 남았고, 그래서
        2026-08-26 재측정은 run **전에** 사람이 손으로 세 모델을 확인해야 했다.
        """
        self.trace.emit(EventKind.TOKEN_COUNTER, stage=self.current_stage, **fields)

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

    def record_retrieval_truncated(self, collected: int, kept: int) -> None:
        """광역 수집 절단을 trace에 남긴다. Retriever의 `on_truncate` 훅에 연결된다.

        **예산에는 청구하지 않는다.** 잘려 나간 조각은 임베딩에 넘어가지도 않았으므로
        비용이 발생하지 않았고, 여기서 장부를 건드리면 `retrieval_call`이 이미 청구한
        것과 겹쳐 센다. 이 이벤트가 남기는 것은 비용이 아니라 **범위**다.

        "전부 봤다"와 "상한까지만 봤다"는 다른 사건인데, 이 훅이 배선되기 전에는 캡슐도
        trace도 그 둘을 구별하지 못했다(D13, W7).
        """
        self.trace.emit(
            EventKind.RETRIEVAL_TRUNCATED,
            stage=self.current_stage,
            collected=collected,
            kept=kept,
        )

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
