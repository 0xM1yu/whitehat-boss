"""1단계 Index & Graph — symbol table·xref·call graph를 만든다.

architecture.md §3의 통과 조건은 "대상 함수 심볼 해석 성공"이다. 산출물은 `GRAPH`
Evidence이고, 이것이 Evidence Gate E2의 조건 중 하나(`any(e.kind == GRAPH and e.ok)`)다
— 이 단계가 실패하면 어떤 Finding도 E2 위로 올라가지 못한다. 그 사실이 조용히 묻히지
않도록 실패도 `Evidence(ok=False)`로 남는다.

⏵ **2026-10-13 (#576)** — 이 Evidence는 **run 전역 하나**이고 개별 Finding의 경로를 확인하지
않는다. `ok=True`는 「인덱서가 돌았고 심볼이 1개 이상이었다」까지다. **심볼 0개는 `ok=False`다**
(어댑터 `container_tools.judge()` — 인덱서 stdout의 요약 줄에서 읽는다). `FakeToolRunner`는 심볼을
세지 않으므로 `dev_fake`의 GRAPH는 대상 소스 존재만 말한다.
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
