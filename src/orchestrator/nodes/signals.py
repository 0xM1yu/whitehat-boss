"""2단계 Signal Collection — 정적분석 신호를 모은다.

architecture.md §3의 통과 조건은 **"실패해도 진행(신호 부재로 기록)"**이다. 그래서 이
노드는 도구 실패를 예외로 올리지 않는다 — ToolRunner가 `Evidence(ok=False, error=...)`를
돌려주고 그것이 그대로 반환 목록에 들어간다. 도구가 실패했다는 사실 자체가 모델 비교
지표다(engineering-guide.md §8.3).

신호가 0건인 것과 도구가 죽은 것은 **다른 사건**이다. 전자는 `ok=True`, 후자는
`ok=False`로 남는다. 둘을 섞으면 "정적 신호 없음"이 도구 장애로 오분류된다.
"""

from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.stages import Stage
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence

# 2단계에서 도는 도구. 이름은 ToolRunner의 화이트리스트 안에 있어야 하고, 밖의 이름을
# 쓰면 tool_policy 위반으로 기록된다(조용히 성공하지 않는다).
#
# `build`가 정적분석과 함께 있는 이유: architecture.md §3의 2단계는 sanitizer 로그와
# fuzz coverage도 다루는데 둘 다 빌드가 선행되어야 하고, **Auditor가 재현 명세를 쓸 수
# 있는지가 이 신호로 갈린다**(빌드되는지 모르는 채로 재현 절차를 지어내면 8단계가
# 실행할 수 없는 명세를 받는다). 빌드 실패도 그 자체로 신호이므로 여기서 기록한다.
SIGNAL_TOOLS = ("semgrep", "clang-tidy", "build")


def with_signal(evidences: tuple[Evidence, ...] | list[Evidence]) -> tuple[Evidence, ...]:
    """**무언가를 발견한** Evidence만 고른다. 뒤 단계에 넘길 때 쓴다.

    정적분석 도구는 아무것도 못 찾아도 정상 종료한다(`ok=True`). 그것을 그대로 넘기면
    "돌렸는데 없었다"가 "신호가 있다"로 세어져 benign 후보의 점수가 오르고, Evidence
    Gate E1(`any(e.kind == STATIC and e.ok)`)이 모든 Finding에서 참이 된다.

    발견 여부는 `exit_code`로 판별한다 — 정적분석 도구의 관례이자 이 저장소의 ToolRunner
    계약이다. `Evidence`에 "발견 건수" 필드가 있으면 더 정확하지만 스키마 확장이라
    여기서 하지 않고 open-questions 항목으로 올렸다(이슈 #61 — 2026-10-12 닫음, 필드는 안 넣었다).

    STATIC이 아닌 종류(GRAPH·BUILD 등)는 성공 자체가 신호이므로 `exit_code`를 보지 않는다.
    걸러진 Evidence도 저장소에는 그대로 남는다 — "신호 부재로 기록"이 2단계의 통과 조건이다.
    """
    return tuple(
        evidence
        for evidence in evidences
        if evidence.ok
        and (evidence.kind is not EvidenceKind.STATIC or bool(evidence.exit_code))
    )


def run(ctx: RunContext, *, workdir: str = ".") -> list[Evidence]:
    """정적분석 도구를 정해진 순서대로 돌리고 결과를 **전부** 돌려준다.

    순서를 고정하는 것은 Evidence ID 발급 순서가 재현성의 일부이기 때문이다.
    걸러내는 것은 호출자의 몫이다(`with_signal`) — 실패도 신호 부재도 여기서는 남긴다.
    """
    with ctx.stage(Stage.SIGNALS):
        return [
            ctx.call_tool(
                tool=tool,
                args=ToolArgs(argv=[tool, "--quiet", workdir], workdir=workdir),
                sandbox=DEFAULT_SANDBOX,
                stage=Stage.SIGNALS,
            )
            for tool in SIGNAL_TOOLS
        ]
