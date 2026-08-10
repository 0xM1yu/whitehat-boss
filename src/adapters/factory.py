"""어댑터 조립 (engineering-guide.md §3).

**구체 어댑터를 import 하는 유일한 파일이다.** 오케스트레이터 노드가 vLLM·SQLite는
물론 이 패키지의 클래스도 직접 import 하지 않아야 "구현체 교체가 설정만으로 가능"이
성립한다. 노드는 `Deps`가 들고 있는 4개 Protocol만 본다.

역할별 ModelClient 선택은 `ModelEntry.endpoint`의 스킴이 정한다. 프로필 단위로 어댑터를
하나 고르는 구조였다면 `single_gpu_12g`(scout만 실서빙)를 표현할 수 없다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.adapters.fake_model import FakeModelClient
from src.adapters.fake_retriever import CacheHook, FakeRetriever
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.hybrid_retriever import HybridRetriever, TruncateHook
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.openai_compat import OpenAICompatModelClient
from src.adapters.routing_model import RoutingModelClient
from src.adapters.sqlite_store import SqliteStateStore
from src.adapters.vllm_retrieval import (
    FakeEmbeddingClient,
    FakeRerankerClient,
    VllmEmbeddingClient,
    VllmRerankerClient,
)
from src.config import HarnessConfig, ModelEntry
from src.ids import IdFactory
from src.interfaces.model_client import ModelClient
from src.interfaces.retrieval import EmbeddingClient, RerankerClient
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


@dataclass(frozen=True)
class _BuildArgs:
    """빌더가 쓸 수 있는 입력 전부.

    세 어댑터의 생성 인자가 서로 다르지만 빌더 시그니처를 하나로 두면 매핑을 한 가지
    타입으로 다룰 수 있다. 각 빌더는 필요한 것만 꺼내 쓴다.
    """

    config: HarnessConfig
    ids: IdFactory
    run_id: str
    source_root: Path
    artifacts_root: Path
    on_cache: CacheHook | None = None
    on_truncate: TruncateHook | None = None


@dataclass(frozen=True)
class _Wiring:
    """구현 클래스와 그 생성 방법.

    둘을 한 곳에 두는 것이 요점이다. 구현 이름을 별도 상수로 따로 두면 실제 배선과
    갈라질 수 있고, **그것이 이 파일이 한 번 저지른 결함이다** — `adapters.retriever`를
    설정에서 검증만 하고 읽지 않은 채 `FakeRetriever`를 하드코딩했었다.
    """

    impl: type
    build: Callable[[_BuildArgs], object]


_RETRIEVERS: dict[str, _Wiring] = {
    "fake": _Wiring(
        FakeRetriever,
        lambda a: FakeRetriever(
            root=a.source_root, ids=a.ids, run_id=a.run_id, on_cache=a.on_cache
        ),
    ),
    # embedding·reranker의 실서빙 여부는 여기서 정하지 않는다 — 모델과 마찬가지로
    # `ModelEntry.endpoint` 스킴이 역할별로 판정한다. 그래서 embedding만 띄우고
    # reranker는 Fake로 두는 구성이 프로필만으로 표현된다.
    "hybrid": _Wiring(
        HybridRetriever,
        lambda a: HybridRetriever(
            root=a.source_root,
            ids=a.ids,
            run_id=a.run_id,
            embedder=_embedding_client(a.config),
            reranker=_reranker_client(a.config),
            on_cache=a.on_cache,
            on_truncate=a.on_truncate,
        ),
    ),
}

_TOOL_RUNNERS: dict[str, _Wiring] = {
    "fake": _Wiring(
        FakeToolRunner,
        lambda a: FakeToolRunner(ids=a.ids, run_id=a.run_id, root=a.source_root),
    ),
}

_STATE_STORES: dict[str, _Wiring] = {
    "memory": _Wiring(InMemoryStateStore, lambda a: InMemoryStateStore()),
    "sqlite": _Wiring(
        SqliteStateStore,
        lambda a: SqliteStateStore(a.artifacts_root / a.run_id / "state.db"),
    ),
}

# `AdapterSelection`의 필드 이름 → 그 필드가 고를 수 있는 구현들.
#
# `tests/test_adapters.py::test_every_adapter_choice_is_wired`가 이 표의 키 집합과
# `AdapterSelection`의 `Literal` 값 집합을 대조한다. 설정에 값을 추가하고 배선을
# 잊으면 거기서 깨진다 — 주석으로 부탁하는 대신 CI가 막는다.
WIRINGS: dict[str, dict[str, _Wiring]] = {
    "retriever": _RETRIEVERS,
    "tool_runner": _TOOL_RUNNERS,
    "state_store": _STATE_STORES,
}


def build_deps(
    config: HarnessConfig,
    *,
    run_id: str,
    source_root: Path,
    artifacts_root: Path,
    on_cache: CacheHook | None = None,
    on_truncate: TruncateHook | None = None,
) -> Deps:
    """프로필 하나에 대응하는 어댑터 묶음을 만든다."""
    ids = IdFactory(run_id)
    args = _BuildArgs(
        config=config,
        ids=ids,
        run_id=run_id,
        source_root=source_root,
        artifacts_root=artifacts_root,
        on_cache=on_cache,
        on_truncate=on_truncate,
    )
    clients: dict[str, ModelClient] = {
        role: _model_client(key, config) for role, key in config.profile.roles.items()
    }
    return Deps(
        models=RoutingModelClient(clients),
        retriever=_build("retriever", args),
        tools=_build("tool_runner", args),
        store=_build("state_store", args),
        ids=ids,
    )


def _build(field: str, args: _BuildArgs) -> Any:
    """`adapters.<field>` 값이 고른 구현을 만든다.

    설정값을 읽는 곳이 이 함수 하나뿐이므로 "설정에 있는데 아무도 안 읽는" 상태가
    구조적으로 생기지 않는다.
    """
    choice = getattr(args.config.profile.adapters, field)
    return WIRINGS[field][choice].build(args)


def _embedding_client(config: HarnessConfig) -> EmbeddingClient:
    key = config.profile.roles["embedding"]
    entry = config.models[key]
    if entry.is_fake:
        return FakeEmbeddingClient()
    return VllmEmbeddingClient(endpoint=entry.endpoint, model_name=key)


def _reranker_client(config: HarnessConfig) -> RerankerClient:
    key = config.profile.roles["reranker"]
    entry = config.models[key]
    if entry.is_fake:
        return FakeRerankerClient()
    return VllmRerankerClient(endpoint=entry.endpoint, model_name=key)


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


