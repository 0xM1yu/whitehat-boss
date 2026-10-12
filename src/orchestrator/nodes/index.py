"""1단계 Index & Graph — symbol table·xref·call graph를 만든다.

architecture.md §3의 통과 조건은 "대상 함수 심볼 해석 성공"이다. 산출물은 `GRAPH`
Evidence이고, 이것이 Evidence Gate E2의 조건 중 하나(`any(e.kind == GRAPH and e.ok)`)다
— 이 단계가 실패하면 어떤 Finding도 E2 위로 올라가지 못한다. 그 사실이 조용히 묻히지
않도록 실패도 `Evidence(ok=False)`로 남는다.

⏵ **2026-10-13 (#576)** — 이 Evidence는 **run 전역 하나**이고 개별 Finding의 경로를 확인하지
않는다. `ok=True`는 「인덱서가 돌았고 심볼이 1개 이상이었다」까지다. **심볼 0개는 `ok=False`다**
(어댑터 `container_tools.judge()` — 인덱서 stdout의 요약 줄에서 읽는다). `FakeToolRunner`는 심볼을
세지 않으므로 `dev_fake`의 GRAPH는 대상 소스 존재만 말한다.

빈 그래프가 `ok=False`가 되면 **보이는 곳이 셋 늘어난다** (코드로 확인):
- `tool_call` trace 이벤트가 `ok=False`와 `error`(「index: 심볼 0개 …」)를 싣는다
  (`RunContext.call_tool`).
- 모델이 받는 Evidence 목록이 그 GRAPH를 실패로 보인다 — `prompt_loader`의 FACTS `evidence`
  항목은 `"ok": false`, Critic의 `_describe_evidence` 줄은 `failed`다
  (인덱서 실패 때와 같은 모양이다).
- Auditor 탐침 관측 줄(`auditor._probe_observation`)의 `ok=False exit=0 error=…` 모양이 이
  Evidence에 쓰인다. ⚠ 다만 **`index`는 Auditor 탐침 도구가 아니다**(`AUDITOR_TOOLS`는 `index`를
  뺀다) — 그 줄에 실제로 나타나는 일은 지금 없고, 도구 목록이 바뀔 때 생길 모양이다.

**기록만 느는 부수 효과가 하나 더 있다.** `graph_ok`가 거짓이 되므로 `p1 ∧ path_ok`인 Finding은
`gate.evaluate`의 `ambiguous`가 참이 되고(`p1 and not p2 and path_ok and not (graph_ok and
unblocked)`), `RunContext.evaluate_gate`가 `gate_ambiguous`를 기록한다. 그 이벤트는 기록뿐이다 —
모델을 부르지 않고 등급은 그대로 E1이다. 그래서 빈 그래프 run에서는 `gate_ambiguous` 수가 오를 수
있다(인덱서 실패 run에서도 같다).
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
