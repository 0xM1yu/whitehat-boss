"""1단계 Index & Graph — symbol table·xref·call graph를 만든다.

architecture.md §3의 통과 조건은 "대상 함수 심볼 해석 성공"이다. 산출물은 `GRAPH`
Evidence이고, 이것이 Evidence Gate E2의 조건 중 하나(`any(e.kind == GRAPH and e.ok)`)다
— 이 단계가 실패하면 어떤 Finding도 E2 위로 올라가지 못한다. 그 사실이 조용히 묻히지
않도록 실패도 `Evidence(ok=False)`로 남는다.
"""

from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.stages import Stage
from src.schemas.evidence import Evidence

INDEX_TOOL = "index"


def run(ctx: RunContext, *, workdir: str = ".") -> Evidence:
    """호출 그래프를 추출한다. `workdir`는 마운트 루트 기준 상대경로다."""
    with ctx.stage(Stage.INDEX):
        return ctx.call_tool(
            tool=INDEX_TOOL,
            args=ToolArgs(argv=[INDEX_TOOL, "--callgraph", workdir], workdir=workdir),
            sandbox=DEFAULT_SANDBOX,
            stage=Stage.INDEX,
        )
