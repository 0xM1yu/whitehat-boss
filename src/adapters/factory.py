"""어댑터 조립 (engineering-guide.md §3).

**구체 어댑터를 import 하는 유일한 파일이다.** 오케스트레이터 노드가 vLLM·SQLite는
물론 이 패키지의 클래스도 직접 import 하지 않아야 "구현체 교체가 설정만으로 가능"이
성립한다. 노드는 `Deps`가 들고 있는 4개 Protocol만 본다.

역할별 ModelClient 선택은 `ModelEntry.endpoint`의 스킴이 정한다. 프로필 단위로 어댑터를
하나 고르는 구조였다면 `single_gpu_12g`(scout만 실서빙)를 표현할 수 없다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.adapters.container_tools import ContainerToolRunner, index_output_path
from src.adapters.fake_model import FakeModelClient
from src.adapters.fake_retriever import CacheHook, FakeRetriever
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.hybrid_retriever import HybridRetriever, TruncateHook, UsageHook
from src.adapters.index_graph import GraphIndex, IndexHook
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
from src.config import PROMPTED_ROLES, HarnessConfig, ModelEntry
from src.ids import IdFactory
from src.interfaces.model_client import ModelClient
from src.interfaces.retrieval import EmbeddingClient, RerankerClient
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner
from src.schemas.common import Location


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
    # 컨테이너 이미지 → digest. `RunRecord.tool_images`가 되고, 비어 있으면 그 run은
    # 비교표에서 제외된다(runner.incomplete_fields).
    #
    # `ToolRunner` Protocol에 `images()`를 넣는 것이 최종형이지만 `src/interfaces/` 변경은
    # 전원 공지 대상이라(roadmap.md §3) 지금은 이 dataclass가 나른다. 두 구현이 이미
    # `images()`를 갖고 있으므로 승격은 Protocol에 한 줄 추가하는 일이 된다.
    tool_images: dict[str, str] = field(default_factory=dict)
    # file → 그 파일에 정의된 심볼 위치. 러너가 후보 목록을 만드는 데 쓴다(W2).
    #
    # **`read_source`와 같은 모양이다** — 러너는 `src/adapters/`를 import 하지 않으므로
    # `GraphIndex`를 타입으로 넘길 수 없고, `Location`(=`src/schemas/`)만 경계를 넘는
    # 콜러블로 나른다. `Retriever` Protocol에 메서드를 다는 길도 있었지만 "무엇이
    # 대상인가"는 검색이 아니라 인덱스의 질문이라 그쪽에 두지 않았다.
    #
    # 기본값이 "인덱스 없음"(`None`)인 것은 의도다. 이 필드 없이 조립된 `Deps`가
    # 후보 0건을 만들면 파이프라인이 조용히 비고, 폴백이면 최소한 옛 동작이 남는다.
    symbols_for: Callable[[str], list[Location] | None] = lambda _file: None


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
    on_usage: UsageHook | None = None
    on_index: IndexHook | None = None

    # **한 run에 하나만 만든다.** `graph_index()`가 호출마다 새 로더를 만들던 시절에는
    # Retriever와 `symbols_for`가 각각 하나씩 갖게 되고, 그러면 지연 로드가 두 번 일어나
    # `on_index` 훅이 두 번 불린다 — `graph_index` 이벤트는 run당 1건이 계약이다
    # (`trace.py`). 그래서 `build_deps()`가 만들어 여기에 넣는다.
    graph: GraphIndex | None = None

    def graph_index(self) -> GraphIndex:
        """1단계 인덱서 산출물을 읽을 로더. **한 run에 한 인스턴스다.**

        **두 Retriever가 같은 것을 받는다.** 갈리면 같은 index.json에서 다른 캡슐이 나와
        `dev_fake`↔`dev_docker` 비교가 오염된다(`index_graph` 모듈 docstring).

        경로 조립을 여기서 하지 않고 `container_tools.index_output_path()`에 묻는다 —
        쓰는 쪽과 읽는 쪽이 갈리면 예외가 아니라 파일을 못 찾는 상태가 되고, 그것은
        "인덱서가 안 돌았다"로 기록된다.

        `tool_runner: fake`인 프로필에서는 그 경로에 파일이 생기지 않는다. 그래도 같은
        로더를 넘긴다 — 존재 판정은 로더가 하고 결과는 `status=missing`이다.
        """
        if self.graph is None:
            raise ValueError("build_deps()가 GraphIndex를 넣지 않았다")
        return self.graph


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
            root=a.source_root,
            ids=a.ids,
            run_id=a.run_id,
            on_cache=a.on_cache,
            # **index.json을 내는 프로필이 전부 이쪽이다**(dev_docker·dev_singularity·
            # local_balanced·b1_single). 여기 그래프를 안 물리면 1단계 산출물을 아무도
            # 읽지 않는다.
            graph=a.graph_index(),
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
            on_usage=a.on_usage,
            graph=a.graph_index(),
        ),
    ),
}

# docker·singularity·podman은 **같은 클래스**다. 로컬은 docker, Seraph는 Docker 데몬 접근이
# 없어 singularity, ATOM은 데몬 접근도 singularity도 없어 podman — 계약과 판정은 하나이고
# 명령 조립만 갈린다. 그래서 `describe_wiring()`의 구현 이름만으로는 셋이 구분되지 않는다.
# `doctor`가 런타임 이름을 따로 출력하는 이유다(`cli._check_tool_runtime`).
_TOOL_RUNNERS: dict[str, _Wiring] = {
    "fake": _Wiring(
        FakeToolRunner,
        lambda a: FakeToolRunner(ids=a.ids, run_id=a.run_id, root=a.source_root),
    ),
    "docker": _Wiring(
        ContainerToolRunner,
        lambda a: ContainerToolRunner(
            runtime="docker",
            ids=a.ids,
            run_id=a.run_id,
            root=a.source_root,
            artifacts_root=a.artifacts_root,
        ),
    ),
    "podman": _Wiring(
        ContainerToolRunner,
        lambda a: ContainerToolRunner(
            runtime="podman",
            ids=a.ids,
            run_id=a.run_id,
            root=a.source_root,
            artifacts_root=a.artifacts_root,
        ),
    ),
    "singularity": _Wiring(
        ContainerToolRunner,
        lambda a: ContainerToolRunner(
            runtime="singularity",
            ids=a.ids,
            run_id=a.run_id,
            root=a.source_root,
            artifacts_root=a.artifacts_root,
        ),
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
    on_usage: UsageHook | None = None,
    on_index: IndexHook | None = None,
) -> Deps:
    """프로필 하나에 대응하는 어댑터 묶음을 만든다."""
    ids = IdFactory(run_id)
    # 경로 조립을 여기서 하지 않고 `container_tools.index_output_path()`에 묻는다 —
    # 쓰는 쪽과 읽는 쪽이 갈리면 예외가 아니라 "인덱서가 안 돌았다"로 기록된다.
    graph = GraphIndex(index_output_path(artifacts_root, run_id), on_index=on_index)
    args = _BuildArgs(
        config=config,
        ids=ids,
        run_id=run_id,
        source_root=source_root,
        artifacts_root=artifacts_root,
        on_cache=on_cache,
        on_truncate=on_truncate,
        on_usage=on_usage,
        on_index=on_index,
        graph=graph,
    )
    # 프롬프트를 받는 역할에만 ModelClient를 만든다. 전 역할을 돌면 embedding·reranker
    # 엔드포인트를 향해 `/chat/completions`를 쏘는 클라이언트가 생긴다 — 아무도 부르지
    # 않아 드러나지 않을 뿐 잘못된 배선이다(ADR 0004: 그 둘은 `Role`이 아니다).
    clients: dict[str, ModelClient] = {
        role: _model_client(config.profile.roles[role], config)
        for role in PROMPTED_ROLES
        if role in config.profile.roles
    }
    tools = _build("tool_runner", args)
    return Deps(
        models=RoutingModelClient(clients),
        retriever=_build("retriever", args),
        tools=tools,
        store=_build("state_store", args),
        ids=ids,
        # 구현에게 묻는다. 여기서 이미지 이름을 조립하면 어댑터가 무엇을 실제로 물렸는지와
        # 갈라질 수 있고, 갈라진 재현성 기록은 없는 것보다 나쁘다.
        tool_images=tools.images(),
        # Retriever가 캡슐의 그래프 섹션을 채우는 것과 **같은 로더**다. 따로 파싱하면
        # 후보의 근사와 그래프의 근사가 갈리고 그 차이가 어느 기록에도 안 남는다.
        symbols_for=graph.symbols_for,
    )


def describe_wiring(config: HarnessConfig) -> dict[str, str]:
    """설정이 고르는 구현체 이름. `doctor`가 출력한다.

    프로필이 무엇을 선언했는지가 아니라 **무엇이 실제로 물리는지**를 보여주는 것이
    목적이다. `dev_fake`와 `dev_hybrid`의 유일한 차이가 retriever인데 진단 출력이
    똑같으면, 설정이 무시되고 있어도 알아챌 방법이 없다.

    부작용은 만들지 않는다 — `doctor`는 엔드포인트에 접속하지 않는 것이 계약이다
    (`src/cli.py` 모듈 docstring). 어댑터 3종은 `_Wiring.impl`에서 이름만 뽑는다
    (`SqliteStateStore`는 생성 시점에 파일을 만든다). 모델·검색 클라이언트는 생성자가
    필드 대입뿐이라 실제로 만들어 이름을 읽는다 — `build_deps()`와 같은 함수를 쓰므로
    갈라질 수 없다.
    """
    wiring = {
        field: WIRINGS[field][getattr(config.profile.adapters, field)].impl.__name__
        for field in WIRINGS
    }
    for role in PROMPTED_ROLES:
        if role in config.profile.roles:
            wiring[role] = type(_model_client(config.profile.roles[role], config)).__name__
    # 검색 클라이언트는 hybrid일 때만 만들어진다. fake retriever 프로필에서 이 줄을
    # 출력하면 "쓰이지도 않는데 물려 있다"로 읽힌다.
    if config.profile.adapters.retriever == "hybrid":
        wiring["embedding"] = type(_embedding_client(config)).__name__
        wiring["reranker"] = type(_reranker_client(config)).__name__
    return wiring


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


