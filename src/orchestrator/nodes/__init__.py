"""파이프라인 단계 0~9 노드 (architecture.md §3).

노드 시그니처는 `def run(ctx: RunContext, ...) -> ...`이고 **LangGraph 타입을 노출하지
않는다**(open-questions.md ADR 0002 미확정, ADR 0002). 오케스트레이터를 갈아 끼워도 노드가
그대로 남으려면 노드가 아는 것이 `RunContext` 하나여야 한다.

각 노드가 지키는 계약:

1. 임계값을 직접 비교하지 않는다. `ctx.decide(RoutingRequest)` → `ctx.apply_action()`.
2. 모델·도구 호출은 `ctx.call_model()` / `ctx.call_tool()`만 쓴다.
3. 상태 변경은 `ctx.create()` / `ctx.apply()` / `ctx.apply_action()`만 쓴다.
4. 에러를 삼키지 않는다. 도구 실패도 `Evidence(ok=False)`로 남고, 모델 실패는 사유
   코드로 전이한 뒤 그대로 올라간다.
5. 각 노드가 자기 `stage_start`/`stage_end` 구간을 연다 — 단계별 wall_ms의 출처다.
   4·5단계처럼 후보마다 도는 노드는 구간이 여러 번 열리고, 지표는 그 합이다.
"""

from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    pov,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)

__all__ = [
    "auditor",
    "critic",
    "gate_node",
    "index",
    "pov",
    "preprocess",
    "report",
    "retrieval",
    "scout",
    "signals",
    "validate",
]
