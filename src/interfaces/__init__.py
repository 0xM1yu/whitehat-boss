"""교체 가능한 4개 인터페이스 (engineering-guide.md §3).

도메인 로직은 이 4개에만 의존한다. 오케스트레이터 노드에서 vLLM·LiteLLM·SQLite를
직접 import 하는 것은 금지다 — 어댑터(`src/adapters/`, PR 2)를 거친다.

`src/interfaces/`는 `src/schemas/`와 함께 여러 모듈이 함께 의존하는 유일한 결합점이다.
"""

from src.interfaces.model_client import ModelClient, ModelResult, Prompt, SchemaT, Usage
from src.interfaces.retriever import Query, Retriever
from src.interfaces.state_store import ObjectKind, StateStore, StoredObject
from src.interfaces.tool_runner import SandboxSpec, ToolArgs, ToolRunner

__all__ = [
    "ModelClient",
    "ModelResult",
    "ObjectKind",
    "Prompt",
    "Query",
    "Retriever",
    "SandboxSpec",
    "SchemaT",
    "StateStore",
    "StoredObject",
    "ToolArgs",
    "ToolRunner",
    "Usage",
]
