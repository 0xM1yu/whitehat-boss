"""어댑터 조립 (engineering-guide.md §3).

**구체 어댑터를 import 하는 유일한 파일이다.** 오케스트레이터 노드가 vLLM·SQLite는
물론 이 패키지의 클래스도 직접 import 하지 않아야 "구현체 교체가 설정만으로 가능"이
성립한다. 노드는 `Deps`가 들고 있는 4개 Protocol만 본다.

역할별 ModelClient 선택은 `ModelEntry.endpoint`의 스킴이 정한다. 프로필 단위로 어댑터를
하나 고르는 구조였다면 `single_gpu_12g`(scout만 실서빙)를 표현할 수 없다.
"""

from dataclasses import dataclass
from pathlib import Path

from src.adapters.fake_model import FakeModelClient
from src.adapters.fake_retriever import CacheHook, FakeRetriever
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.openai_compat import OpenAICompatModelClient
from src.adapters.routing_model import RoutingModelClient
from src.adapters.sqlite_store import SqliteStateStore
from src.config import HarnessConfig, ModelEntry
from src.ids import IdFactory
from src.interfaces.model_client import ModelClient
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner


@dataclass(frozen=True)
class Deps:
    """노드가 보는 전부. 4개 인터페이스 + ID 발급기.

    `IdFactory`가 여기 있는 것은 ID가 재현성의 일부이기 때문이다 — 노드가 각자
    ID를 만들면 두 실행의 trace를 대조할 수 없다(engineering-guide.md §5).
    """

    models: ModelClient
    retriever: Retriever
    tools: ToolRunner
    store: StateStore
    ids: IdFactory


def build_deps(
    config: HarnessConfig,
    *,
    run_id: str,
    source_root: Path,
    artifacts_root: Path,
    on_cache: CacheHook | None = None,
) -> Deps:
    """프로필 하나에 대응하는 어댑터 묶음을 만든다."""
    ids = IdFactory(run_id)
    clients: dict[str, ModelClient] = {
        role: _model_client(key, config) for role, key in config.profile.roles.items()
    }
    return Deps(
        models=RoutingModelClient(clients),
        retriever=FakeRetriever(root=source_root, ids=ids, run_id=run_id, on_cache=on_cache),
        tools=FakeToolRunner(ids=ids, run_id=run_id, root=source_root),
        store=_state_store(config, run_id=run_id, artifacts_root=artifacts_root),
        ids=ids,
    )


def _model_client(model_key: str, config: HarnessConfig) -> ModelClient:
    """endpoint 스킴이 구현을 고른다. 역할마다 따로 판정하므로 혼용이 가능하다.

    `model_key`가 곧 `ProducedBy.model`에 남는 이름이다 — registry 키와 결과 기록이
    갈라지면 조건 비교에서 어느 모델이었는지 되짚을 수 없다.
    """
    entry: ModelEntry = config.models[model_key]
    if entry.is_fake:
        return FakeModelClient(model_name=model_key)
    return OpenAICompatModelClient(
        endpoint=entry.endpoint,
        model_name=model_key,
        temperature=config.sampling.temperature,
        top_p=config.sampling.top_p,
        seed=config.seed,
        retries=config.thresholds.schema_retries,
    )


def _state_store(config: HarnessConfig, *, run_id: str, artifacts_root: Path) -> StateStore:
    if config.profile.adapters.state_store == "memory":
        return InMemoryStateStore()
    return SqliteStateStore(artifacts_root / run_id / "state.db")
