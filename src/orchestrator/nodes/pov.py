"""8단계 PoV — Auditor가 세운 Finding에서 PoC를 만드는 모델-주도 툴 루프 노드 (Option C).

매 홉 모델이 `PovAction`(v5는 `PovActionNamed`) — gen/inspect/submit 하나를 낸다. `gen`은
`PovSpec`을 실어 순수 함수 `assemble_poc`(러너)가 결정론적으로 바이트를 만들고, 그 PoC를 도구로
대상에서 실행해 관측을 다음 홉으로 되먹인다(fork B-iii — 모델-작성 코드 실행 없음). 러너 8단계가
`validate.run`과 나란히 `_run_pov_hops`로 이 노드의 홉 헬퍼를 부른다(추가형).
이 노드는 프롬프트 렌더 + 모델 호출 + 도구 실행 헬퍼만 든다 —
루프 제어·정지 판정·이벤트는 러너(`_run_pov_hops`)에 있다.

`Stage.VALIDATE` 아래에서 돈다 — 새 단계 번호를 만들지 않는다(Stage는 0~9 고정).
구체 어댑터 모듈을 import하지 않는다(orchestrator 규약, tests/test_policy.py).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from src.interfaces.model_client import ChatTurn, Prompt
from src.interfaces.tool_runner import INPUT_PLACEHOLDER, ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.nodes.scout import describe_target
from src.orchestrator.prompt_loader import PromptError, build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding
from src.schemas.pov import PovAction, PovActionNamed

ROLE = Role.POV
# 이 역할의 구조화 출력 스키마 — v2~v4는 매 홉 `PovAction`이다(gen/inspect/submit; v5는
# `PovActionNamed` — 아래 ⏵). `_call_hop`이 판의 스키마를 요청하고, bench·계약 검사
# (`NODE_OUTPUT_SCHEMAS`/`ROLE_SPEC`)는 이 값(`PovAction`)을 본다.
OUTPUT_SCHEMA = PovAction

# ⏵ 2026-10-11 — 판마다 요청 스키마가 갈린다: v2~v4는 `PovAction`, v5는 그 형제
# `PovActionNamed`(`submit`이 `poc_sha256`으로 앞선 gen을 지명한다 — 이슈 #549 ②). 어느 쪽인지는
# 프롬프트 meta의 `output_schema` 선언이 정한다(`nodes/auditor.py`·`nodes/scout.py`의
# `resolve_output_schema`와 같은 방식). 집합 밖 선언은 `PromptError`다. `OUTPUT_SCHEMA`는 v2~v4의
# 값(`PovAction`)으로 남는다.
ALLOWED_OUTPUT_SCHEMAS: tuple[type[PovAction], ...] = (PovAction, PovActionNamed)
_SCHEMA_BY_NAME = {schema.__name__: schema for schema in ALLOWED_OUTPUT_SCHEMAS}


def resolve_output_schema(name: str) -> type[PovAction]:
    """프롬프트 meta의 `output_schema` 이름을 이 역할이 허용하는 스키마로 푼다."""
    try:
        return _SCHEMA_BY_NAME[name]
    except KeyError:
        raise PromptError(
            f"pov meta의 output_schema `{name}`은 이 역할이 낼 수 없다 — "
            f"허용: {sorted(_SCHEMA_BY_NAME)}"
        ) from None

# 넓은 입력(Auditor가 본 SOURCE·퍼즈 진입점·경로 심볼)을 받는 판 — `critic._CONTENT_AWARE_VERSIONS`
# ·`auditor._CONTENT_AWARE_VERSIONS`와 같은 패턴이다(자기 상수다, 다른 노드 것을 import하지
# 않는다). 2026-10-05부터 v3가, 2026-10-08부터 v4가 `config/models.yaml`의 실 PoV 좌석이라 이
# 넓은 입력을 받는다. v2는 2026-10-02 PR 전과 바이트가 같아야 한다(`docs/evaluation.md` §2.3-4,
# 프롬프트가 바뀌면 이전 run과 직접 비교하지 않는다. v2로 돈 run은 0건이라 비교가 깨질 대상도
# 없지만, v2는 이 PR 전과 같은 입력 모양을 유지하는 판이다. v3 배선으로 SOURCE 확대와 채점기
# 관측이 함께 켜졌지만 둘은 서로 다른 입력 변경이다(#354/#386①).
# 🔴 **이 집합은 판이 하나 늘 때마다 같이 자란다** - v2를 뺀 새 판을 여기 안 넣으면 그 판에서
# 넓은 입력이 **조용히 꺼진다**(프롬프트는 그대로 로드되는데 입력이 v2로 돌아간다. 이유는
# `prompt_loader` docstring의 「버전 이름으로 분기하지 않는 이유」). 막는 것은
# `tests/test_pov_loop.py`의 `test_every_pov_prompt_version_but_v2_gets_the_wide_inputs`와 doctor의
# 「내용 인식」 검사다.
# ⚠ **`PovAction.tool`의 `gdb` enum은 이 게이트 밖이다** — 스키마 차원이라 v2에도 들어간다
# (`src/schemas/pov.py`의 그 필드 주석이 이 한계를 적는다). 그래도 v2 prefix는 gdb를 안 광고하므로
# 실제로 고르지는 않는다(`prompts/pov/<판>/prefix.md`의 `inspect` 줄이 모델이 볼 도구 목록이다).
_WIDE_INPUT_VERSIONS = frozenset({"v3", "v4", "v5", "v6"})

# 실행 환경이 낸 종료 코드(타임아웃·컨테이너 실행 실패) — 대상에 대해 말하지 않는다. 이 값의 실행은
# 반복 판정에 기억하지 않는다(#619). `container_tools`의 `_TIMEOUT_EXIT`·`_EXEC_FAILED`와 같다
# (노드는 어댑터를 import 못 한다 — `tests/test_pov_loop.py`가 같음을 문다).
INFRA_FAILURE_EXITS = frozenset({124, 125, 126, 127})


def _render_prompt(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None,
    fuzz_entrypoint: Mapping[str, object] | None = None,
    retry_note: str | None = None,
    compile_note: str | None = None,
) -> Prompt:
    """hop0 프롬프트를 렌더한다 — 사실 블록(FACTS)이 여기 실린다.

    `open_hop`이 부른다. 후속 홉은 이걸 다시 렌더하지 않고 prefix·version만 재사용한다
    (`continue_hop` — 사실은 hop0 본문에 한 번만 실린다).

    ⚠ **`source`는 버전과 무관하게 받은 그대로 쓴다** — 이 파라미터는 이 PR 전부터 있던 공개
    계약이고(`test_pov_open_hop_reads_the_source_override`, main `d85ea62`), 직접 호출하는 쪽이
    뭘 넣든 그대로 존중한다. **버전에 따라 달라지는 것은 러너가 이 자리에 "무엇을 넣는가"다** —
    8단계 루프(`runner._run_pov_hops`)는 `_WIDE_INPUT_VERSIONS`가 아니면 `state.finding_sources`를
    조회하지 않고 `None`을 넘긴다(적대적 리뷰 2026-10-02 MEDIUM-4). `fuzz_entrypoint`는 이 PR이
    새로 만든 파라미터라 공개 계약이 없으므로 여기서 직접 게이트한다.
    """
    version = ctx.config.model_for(ROLE.value).prompt_template_version
    wide = version in _WIDE_INPUT_VERSIONS
    effective_fuzz = fuzz_entrypoint if wide else None
    template = load_prompt(ROLE, version)
    text = ctx.read_source(capsule.target) if source is None else source()
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids={"finding_id": finding.finding_id},
            target=capsule.target,
            capsule=capsule,
            source=text,
            # 러너가 만들어 넘긴다 — 노드는 `ctx.resolve_requests`를 쓰지 않는다
            # (`context.py:171-173`). 못 찾은 경우에도 사유가 든 dict가 온다. `wide`가 아니면
            # `None`이라 `build_facts`가 키 자체를 안 만든다(opt-in, `prompt_loader.FACT_KEYS`).
            fuzz_entrypoint=effective_fuzz,
        ),
        finding=_describe_finding(finding, wide=wide),
        target=describe_target(capsule.target),
        source=text,
    )
    # 본문 **끝**에 붙는 것은 둘이고 둘 다 `None`이면 바이트 그대로다. ① `compile_note` — 2단계가
    # 잰 컴파일 실패를 알리는 하네스 노트(이슈 #507 · `signals.compile_note`). 하네스 사실이라
    # FACTS 뒤에 따로 둔다(프롬프트 body는 불변이다). ② `retry_note` — hop0 파싱 실패 뒤 다시 물을
    # 때만의 오류 노트. 받은 그대로 쓴다(`source`와 같다) — **어느 판에 싣는가는 러너가
    # 정한다**(`_WIDE_INPUT_VERSIONS`).
    tails = [note for note in (compile_note, retry_note) if note is not None]
    if not tails:
        return prompt
    return Prompt(
        prefix=prompt.prefix,
        body="\n\n".join([prompt.body, *tails]),
        prompt_version=prompt.prompt_version,
    )


@dataclass(frozen=True)
class PovHop:
    """PoV 툴 루프 홉 하나의 결과 — `scout.ScoutSession`의 최소판.

    `history`는 **다음 홉으로 보낼** 문답 전부다(이 홉의 user body + assistant raw 포함).
    `prefix`·`version`은 hop0에서 고정돼 후속 홉이 재사용한다 — system prefix는 홉마다 다시
    붙는 메시지라 다시 렌더하지 않는다(`interfaces/model_client.ChatTurn` 주석).
    """

    action: PovAction
    hop: int
    raw_text: str
    prefix: str
    version: str
    history: tuple[ChatTurn, ...]


def open_hop(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None = None,
    fuzz_entrypoint: Mapping[str, object] | None = None,
    retry_note: str | None = None,
    compile_note: str | None = None,
    hop: int = 0,
) -> PovHop:
    """hop0 — 사실 블록이 실린 첫 프롬프트로 `PovAction`을 받는다.

    `source`는 `SOURCE` 블록에 실을 원문을 주는 콜러블이다. ⏵ **2026-10-02까지 러너가 이걸 안
    넘겼다** — 그래서 PoV는 `None` 갈래로 `ctx.read_source(capsule.target)`, 즉 **entry 함수
    하나**만 봤다. `_WIDE_INPUT_VERSIONS`인 판에서는 러너가 Auditor가 본 것과 **같은 문자열**을
    넘긴다(`runner._Once` · `state.finding_sources`) — 그 판단은 **러너가** 한다(이 함수는 받은
    `source`를 버전과 무관하게 그대로 쓴다, `_render_prompt` 주석). 비슬라이스 타깃도 Auditor의
    최초 읽기를 캐시하므로 그 뒤 파일이나 reader가 달라져도 같은 문자열을 받는다.

    `fuzz_entrypoint`는 **PoV만 받는 사실**이다 — libFuzzer 하네스의 진입점과 그 본문이고,
    「바이트가 어디로 들어가는가」의 답이다. 러너가 만든다(`runner._fuzz_entrypoint`). 이것은
    `_WIDE_INPUT_VERSIONS`가 아니면 `_render_prompt`가 무시한다 — 이 파라미터는 이 PR이 새로
    만든 것이라 `source`와 달리 지킬 기존 계약이 없다(적대적 리뷰 2026-10-02 MEDIUM-4).

    ⏵ 2026-10-11 (#550) — `retry_note`·`hop`은 **hop0 호출이 스키마 파싱에 실패해 다시 열 때만**
    준다. 실패한 호출도 홉 하나를 쓴 것이라(`runner._run_pov_hops`) 다시 여는 호출은 `hop`번 홉이고,
    노트는 본문 끝에 붙는다. 둘 다 기본값이면 첫 호출과 바이트가 같다.

    ⏵ **2026-10-13 (이슈 #507) — `compile_note`는 2단계가 이미 잰 컴파일 실패를 hop0 본문 끝에
    싣는다** (`signals.compile_note` — 없으면 `None`이고 바이트가 종전과 같다). 러너가
    `_WIDE_INPUT_VERSIONS` 판에만 준다(v2는 이 PR 전과 바이트가 같아야 한다). 도구를 거절하지
    않는다 — 모델이 그 뒤에도 `inspect build`를 고르면 종전처럼 돈다. hop0 파싱 실패 뒤 다시 열
    때도 같은 노트가 실린다.
    """
    prompt = _render_prompt(
        ctx,
        finding=finding,
        capsule=capsule,
        source=source,
        fuzz_entrypoint=fuzz_entrypoint,
        retry_note=retry_note,
        compile_note=compile_note,
    )
    return _call_hop(ctx, finding=finding, prompt=prompt, history=(), hop=hop)


def continue_hop(ctx: RunContext, *, finding: Finding, prev: PovHop, followup: str) -> PovHop:
    """hop k(= `prev.hop + 1`) — 도구 관측(`followup`)을 다음 user 턴으로 보낸다.

    사실 블록은 hop0 본문(`history[0]`)에 한 번만 실리므로 여기서는 다시 렌더하지 않고
    prefix·version을 재사용한다(`scout.continue_session`과 같은 규율).
    """
    prompt = Prompt(prefix=prev.prefix, body=followup, prompt_version=prev.version)
    return _call_hop(
        ctx, finding=finding, prompt=prompt, history=prev.history, hop=prev.hop + 1
    )


def _call_hop(
    ctx: RunContext,
    *,
    finding: Finding,
    prompt: Prompt,
    history: Sequence[ChatTurn],
    hop: int,
) -> PovHop:
    """모델 호출 하나 — hop0·hop k 공통. 예외는 그대로 올린다(호출자가 흡수 여부를 정한다)."""
    result = ctx.call_model(
        role=ROLE,
        prompt=prompt,
        schema=resolve_output_schema(load_prompt(ROLE, prompt.prompt_version).output_schema),
        stage=Stage.VALIDATE,
        subject_kind="finding",
        subject_id=finding.finding_id,
        history=history,
        hop=hop,
    )
    raw = result.raw_text or ""
    new_history = (
        *history,
        ChatTurn(speaker="user", content=prompt.body),
        ChatTurn(speaker="assistant", content=raw),
    )
    return PovHop(
        action=result.value,
        hop=hop,
        raw_text=raw,
        prefix=prompt.prefix,
        version=prompt.prompt_version,
        history=new_history,
    )


def run_tool(
    ctx: RunContext, *, finding: Finding, tool: str, workdir: str, poc: bytes | None = None
) -> Evidence:
    """PoV 홉에서 도구를 돌린다 — asan·build·gdb·poc.

    `poc`가 있으면 **데이터로** 싣는다: `ToolArgs.input_bytes`가 그 바이트이고 argv가 자리표를 든다
    (`[tool, INPUT_PLACEHOLDER]`). 어댑터가 자기 namespace의 파일로 쓴다(컨테이너는 로그 머리의
    자리표만 그 경로로 바꾼다) — 이 노드는 컨테이너 안 경로를 모른다. `gen` 홉은 그 홉이 조립한
    PoC를, `inspect` 홉은 **직전 gen의** PoC를 싣고 아직 gen이 없으면 `None`이다(argv가 `[tool]`
    하나). 출처는 모델 spec에서 `assemble_poc`이 조립한 바이트뿐이다.

    ⏵ **2026-10-09 (ADR 0014) — 컨테이너의 `poc`는 그 바이트를 먹인다.** 과제 `-vul` 이미지의
    `/tmp/poc`로 바인드하므로 **그 도구의 관측은 PoC의 함수다**. ⚠ **나머지는 아니다** — fake는
    쓰고 지우며 대상 소스 존재만 보고 ok를 내고(`fake_tools._run_repro`), 컨테이너의
    `asan`·`gdb`·`build`는 우리가 빌드한 대상을 돌려 그 관측이 PoC의 함수가 아니다(셋을
    바꾸려면 대상
    빌드 규칙이 필요하고 그것이 기각된 A안이다 — 이슈 #507). 그 셋에서 PoC의 함수인 것은 러너가
    관측에 싣는 입력의 크기·digest 줄뿐이다(`runner._pov_observation`). 원바이트는 trace에
    안 간다 — `call_tool`이 크기와 digest만 싣는다.
    ⏵ **2026-10-13 (#507) — 대신 모델에게 알린다, 거절하지 않는다**: 2단계 `build`·`clang-tidy`가
    컴파일 오류로 실패했으면 hop0 본문이 그 사실(건수·첫 오류)과 「`build`·`asan`·`gdb`는 같은
    컴파일 명령이라 같은 방식으로 실패하리라」를 하네스 노트로 싣는다(`open_hop(compile_note=)`).
    이 함수는 그 뒤에도 요청받은 도구를 종전 그대로(argv 불변) 돌린다 — 실패 관측이
    `compile_failure_reason`을 머리줄에 단다.

    이 함수는 파일을 안 쓴다(쓰는 것은 어댑터) — 조립된 바이트는 루프가 종단(`_pov_finish`)에
    닿으면 `_run_pov_hops`의 반환값과 종단 이벤트(`poc_bytes`·`poc_sha256`)로 남고, 그 종단에서
    러너가 최종 후보(보통 마지막 gen)의 PoC를 run 산출물 사이드카(`pov_poc/`, #355)로 쓴다.
    ⚠ 루프가 종단에 닿기 전에 끊기면 바이트는 안 남는다(#394). 예산이 이 도구 호출 자신에서 소진되면
    (`ctx.call_tool`의 `ensure`) 이 함수는 `BudgetExhausted`를 그대로 올리고, 루프가 그것을 받아
    그 홉의 gen 이벤트(`poc_sha256`)를 낸 뒤 다시 던진다 — 해시는 남는다(`docs/agent-contracts.md`).
    """
    return ctx.call_tool(
        tool=tool,
        args=ToolArgs(
            argv=[tool] if poc is None else [tool, INPUT_PLACEHOLDER],
            workdir=workdir,
            input_bytes=poc,
        ),
        sandbox=DEFAULT_SANDBOX,
        stage=Stage.VALIDATE,
        subject_id=finding.finding_id,
    )


def _cwe_label(finding: Finding) -> str:
    """Finding 서술 줄의 CWE 칸. ⏵ 2026-10-09 (이슈 #525 선택지 1) — `None`(v11의 `no_signal`)이면
    `(없음)`이다 — Auditor의 후보 서술(`추정 CWE: (없음)`)과 같은 표시다. `f"{None}"`은 `None`이라는
    글자를 CWE 자리에 찍는다. CWE가 있으면 그 값 그대로라 종전과 **같은 바이트**다 — 이 줄이 PoV
    요청의 일부다.
    """
    return "(없음)" if finding.cwe is None else finding.cwe


def _describe_finding(finding: Finding, *, wide: bool = True) -> str:
    """PoV 프롬프트에 실을 Finding 요약.

    ⏵ **2026-10-02 — 넓혔다**(사용자 지적). 전 판은 `len(finding.path)`만 실어 **경로 단계의 심볼과
    주석을 버렸다** — 프롬프트가 *"경로 단계로 흐름을 확인하라"*고 지시하는데 **단계 개수만** 줬다
    (`prompts/pov/*/prefix.md`의 도달 절차 3). source→sink의 함수 이름이 없으면 PoV는 `SOURCE`에서
    어느 함수가 앞단 파서이고 어느 것이 sink인지 못 가른다. `trigger_status`·`repro_spec` 유무도
    같은 이유로 싣는다 — Auditor가 재현 근거를 이미 찾았는지가 다음 액션을 가른다.

    ⚠ **`repro_spec`의 본문은 안 싣고 유무만 싣는다.** 필드는 `steps`(샌드박스에서 돌릴 명령 줄)과
    `expected_signal`이고(`src/schemas/finding.py`의 `ReproSpec`), `steps`는 **자유 문자열**이라
    참조 PoC 경로(`/tmp/poc`)를 가리킬 수 있다 — 그 바이트가 프롬프트 경로로 들어가면 run이 무효다.
    본문을 실을지는 그 필드가 실서빙에서 무엇으로 채워지는지 본 뒤에 정한다(미실측).
    ⏵ 2026-10-02 정정: 전 판은 필드를 `cmd`·`input_ref`라 적었는데 **그런 필드는 없다**(§2.1 —
    문서가 코드를 서술하면 그 코드를 열어 대조한다).

    `wide=False`(`_WIDE_INPUT_VERSIONS`에 없는 이전 판 v2)면 **옛 모양 그대로**다 —
    `len(finding.path)`만 싣는다(`_render_prompt`가 결정한다, 적대적 리뷰 2026-10-02 MEDIUM-4).
    """
    if not wide:
        return (
            f"{finding.finding_id} · CWE: {_cwe_label(finding)} · {finding.title}\n"
            f"근거(rca): {finding.rca_draft}\n"
            f"경로 단계 수: {len(finding.path)}"
        )
    steps = "\n".join(
        # `step.step`(source/propagate/sink)을 같이 싣는다 — 이름만으로는 **어느 것이 sink인지**
        # 모르고, 프롬프트의 도달 절차 3이 요구하는 것이 그 구분이다.
        f"  {i}. [{step.step}] {step.symbol}" + (f" — {step.note}" if step.note else "")
        for i, step in enumerate(finding.path, start=1)
    )
    lines = [
        f"{finding.finding_id} · CWE: {_cwe_label(finding)} · {finding.title}",
        f"근거(rca): {finding.rca_draft}",
        f"경로 단계 수: {len(finding.path)}",
    ]
    if steps:
        lines.append(f"경로(source→sink):\n{steps}")
    if finding.trigger_status is not None:
        lines.append(f"촉발 상태(trigger_status): {finding.trigger_status}")
    # 유무만 — 위 docstring의 누출 가드.
    lines.append(
        "재현 근거(repro_spec): " + ("있다" if finding.repro_spec is not None else "없다")
    )
    return "\n".join(lines)
