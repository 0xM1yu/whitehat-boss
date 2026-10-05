"""8단계 PoV — Auditor가 세운 Finding에서 PoC를 만드는 모델-주도 툴 루프 노드 (Option C).

매 홉 모델이 `PovAction`(gen/inspect/submit) 하나를 낸다. `gen`은 `PovSpec`을 실어 순수 함수
`assemble_poc`(러너)가 결정론적으로 바이트를 만들고, 그 PoC를 도구로 대상에서 실행해 관측을 다음
홉으로 되먹인다(fork B-iii — 모델-작성 코드 실행 없음). 러너 8단계가 `validate.run`과 나란히
`_run_pov_hops`로 이 노드의 홉 헬퍼를 부른다(추가형). 이 노드는 프롬프트 렌더 + 모델 호출 + 도구
실행 헬퍼만 든다 — 루프 제어·정지 판정·이벤트는 러너(`_run_pov_hops`)에 있다.

`Stage.VALIDATE` 아래에서 돈다 — 새 단계 번호를 만들지 않는다(Stage는 0~9 고정).
구체 어댑터 모듈을 import하지 않는다(orchestrator 규약, tests/test_policy.py).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from src.interfaces.model_client import ChatTurn, Prompt
from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.nodes.scout import describe_target
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding
from src.schemas.pov import PovAction

ROLE = Role.POV
# 이 역할의 구조화 출력 스키마 — 매 홉 `PovAction`이다(gen/inspect/submit). `_call_hop`이 이걸
# 요청하고, bench·계약 검사(`NODE_OUTPUT_SCHEMAS`/`ROLE_SPEC`)도 같은 값을 본다 — 한 역할 한 스키마.
OUTPUT_SCHEMA = PovAction

# 넓은 입력(Auditor가 본 SOURCE·퍼즈 진입점·경로 심볼)을 받는 판 — `critic._CONTENT_AWARE_VERSIONS`
# ·`auditor._CONTENT_AWARE_VERSIONS`와 같은 패턴이다(자기 상수다, 다른 노드 것을 import하지
# 않는다). 2026-10-05부터 `config/models.yaml`의 실 PoV 좌석은 v3라 이 넓은 입력을 받는다. v2는
# 2026-10-02 PR 전과 바이트가 같아야 한다(`docs/evaluation.md` §2.3-4, 프롬프트가 바뀌면
# 이전 run과 직접 비교하지 않는다. v2로 돈 run은 0건이라 비교가 깨질 대상도 없지만, v2는
# 이 PR 전과 같은 입력 모양을 유지하는 판이다. v3 배선으로 SOURCE 확대와 채점기 관측이 함께
# 켜졌지만 둘은 서로 다른 입력 변경이다(#354/#386①).
# ⚠ **`PovAction.tool`의 `gdb` enum은 이 게이트 밖이다** — 스키마 차원이라 v2에도 들어간다
# (`src/schemas/pov.py`의 그 필드 주석이 이 한계를 적는다). 그래도 v2 prefix는 gdb를 안 광고하므로
# 실제로 고르지는 않는다(`prompts/pov/<판>/prefix.md`의 `inspect` 줄이 모델이 볼 도구 목록이다).
_WIDE_INPUT_VERSIONS = frozenset({"v3"})


def _render_prompt(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None,
    fuzz_entrypoint: Mapping[str, object] | None = None,
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
    return template.render(
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
    """
    prompt = _render_prompt(
        ctx,
        finding=finding,
        capsule=capsule,
        source=source,
        fuzz_entrypoint=fuzz_entrypoint,
    )
    return _call_hop(ctx, finding=finding, prompt=prompt, history=(), hop=0)


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
        schema=OUTPUT_SCHEMA,
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


def run_tool(ctx: RunContext, *, finding: Finding, tool: str, workdir: str) -> Evidence:
    """PoV 홉에서 도구를 돌린다 — gen이 만든 PoC를 대상에서 실행/진단(asan·build·gdb·poc).

    ⚠ **PoC 바이트를 도구 입력으로 쓰지 않는다**(`ToolArgs`는 도구 이름과 workdir만 싣는다).
    fake 도구는 workdir의 대상 소스 존재만 보고 ok를 낸다(`fake_tools._run_repro`) — dev_fake는
    루프 배선(gen→도구→Evidence→되먹임)을 고정하는 자리다. 이 함수가 쓰는 파일은 없다 —
    조립된 바이트는 루프가 종단(`_pov_finish`)에 닿으면 `_run_pov_hops`의 반환값과 종단
    이벤트(`poc_bytes`·`poc_sha256`)로 남고, 그 종단에서 러너가 마지막 gen의 PoC를 run 산출물
    사이드카(`pov_poc/`, #355)로 쓴다. ⚠ 루프가 종단에 닿기 전에 끊기면 바이트는 안 남는다
    (#394). 특히 예산이 이 도구 호출 자신에서 소진되면(`ctx.call_tool`의 `ensure`) 그 홉의
    gen 이벤트가 나가기 전이라 해시도 안 남는다 — 앞 홉이 이미 낸 gen 이벤트의 `poc_sha256`만
    남는 경로와 구별한다(`docs/agent-contracts.md`). 컨테이너 경로(호스트)에서 PoC를
    **마운트 파일로 도구에 넘길** 때 바이트를 materialize한다(`validate._call`이
    `[tool, *steps]`를 넘기는 자리와 같은 계약).
    """
    return ctx.call_tool(
        tool=tool,
        args=ToolArgs(argv=[tool], workdir=workdir),
        sandbox=DEFAULT_SANDBOX,
        stage=Stage.VALIDATE,
        subject_id=finding.finding_id,
    )


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
            f"{finding.finding_id} · CWE: {finding.cwe} · {finding.title}\n"
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
        f"{finding.finding_id} · CWE: {finding.cwe} · {finding.title}",
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
