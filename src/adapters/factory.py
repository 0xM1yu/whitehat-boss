"""어댑터 조립 (engineering-guide.md §3).

**구체 어댑터를 import 하는 유일한 파일이다.** 오케스트레이터 노드가 vLLM·SQLite는
물론 이 패키지의 클래스도 직접 import 하지 않아야 "구현체 교체가 설정만으로 가능"이
성립한다. 노드는 `Deps`가 들고 있는 4개 Protocol만 본다.

역할별 ModelClient 선택은 `ModelEntry.endpoint`의 스킴이 정한다. 프로필 단위로 어댑터를
하나 고르는 구조였다면 scout만 실서빙인 프로필(옛 `single_gpu_12g`, 2026-09-24 삭제)을
표현할 수 없다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from src.adapters.container_tools import ContainerToolRunner, index_output_path
from src.adapters.fake_model import FakeModelClient
from src.adapters.fake_retriever import CacheHook, FakeRetriever
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.generation_config import declared_sampling_by_role
from src.adapters.hybrid_retriever import HybridRetriever, TruncateHook, UsageHook
from src.adapters.index_graph import GraphIndex, IndexHook, SliceBody
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.model_swap import SwapTo, swap_runner
from src.adapters.openai_compat import OpenAICompatModelClient
from src.adapters.routing_model import RoutingModelClient
from src.adapters.slices import (
    SliceInputError,
    SliceSet,
    load_slices,
    render_slices,
    slice_refs,
    slicer_summary,
    split_source,
)
from src.adapters.sqlite_store import SqliteStateStore
from src.adapters.symbol_resolver import resolve
from src.adapters.tokenizer import (
    REASON_NO_SOURCE,
    counter_for_source,
    counter_reason_for_source,
)
from src.adapters.vllm_retrieval import (
    FakeEmbeddingClient,
    FakeRerankerClient,
    VllmEmbeddingClient,
    VllmRerankerClient,
)
from src.config import (
    PROMPTED_ROLES,
    SCOUT_POOL_ROLES,
    HarnessConfig,
    ModelEntry,
    check_serving,
    load_serving,
)
from src.ids import IdFactory
from src.interfaces.model_client import ModelClient
from src.interfaces.oracle import Oracle
from src.interfaces.retrieval import EmbeddingClient, RerankerClient
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner
from src.paths import safe_join
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from src.schemas.need_evidence import Resolution, ResolveQuery
from src.schemas.run import CompileFlags, ModelSampling
from src.tokens import COUNTED_ROLES


def _no_slices(slice_id: str) -> str:
    """`Deps.read_slice`의 기본값 — `build_deps` 밖에서 조립된 `Deps`만 이것을 든다.

    `build_deps`는 늘 렌더 조회를 넣는다(`--slices`가 없으면 빈 dict의 조회). 어느 쪽이든 부를
    자리가 없다 — 불렸으면 배선 결함이다.
    """
    raise KeyError(f"렌더된 슬라이스가 없다 — {slice_id}")


def _read_rendered(rendered: dict[str, str]) -> Callable[[str], str]:
    """`build_deps`가 `Deps`와 Retriever에 **같이** 넣는 렌더 조회. 없는 id는 이름을 대고 멈춘다.

    타깃이 되는지(artifact 멤버십 — 러너 `_plan_slice_targets`)와 렌더되는지(`is_file` —
    `render_slices`)는 다른 술어다. 둘 다 run 시작 때 같은 트리를 봐서 현실 입력으로는 안 갈리지만,
    갈리면(시작과 전처리 사이의 트리 변경) 맨 `KeyError` 트레이스백으로 죽었다(#285 리뷰).
    """

    def read(slice_id: str) -> str:
        try:
            return rendered[slice_id]
        except KeyError:
            raise SliceInputError(
                f"{slice_id}: 렌더가 없다 — run 시작 때 entry 파일이 대상 트리에 없던 슬라이스다"
            ) from None

    return read


def _read_target_lines(root: Path, relative: str) -> Sequence[str]:
    """NE resolver의 `read_lines` hook — 대상 트리 파일 하나를 줄 목록으로.

    `slices.split_source`를 그대로 쓴다(#259 회피 선례 — `"\n"`로만 나눈다. CR을 접고
    UTF-8이 아닌 바이트는 latin-1로 대체한다 — #254/#255가 슬라이서 쪽에서 정한 정책이고
    파일 하나의 인코딩 문제로 run 전체가 죽지 않는 이유가 그것이다). 못 읽으면
    `OSError`/`PathEscape`(`ValueError` 하위형)가 그대로 오른다 — `resolve()`가 그
    요청·블록만 건너뛴다(그 안에서 잡는다).
    """
    return split_source(safe_join(root, relative).read_bytes())


def _slice_lines(loaded: SliceSet | None) -> Callable[[str], SliceBody] | None:
    """Retriever가 슬라이스 질의의 그래프 섹션 본문으로 쓰는 줄 집합 조회(#220 ①).

    `_read_rendered`와 같은 자리·같은 실패 모양이다 — 없는 id는 이름을 대고 멈춘다.
    """
    if loaded is None:
        return None
    lines = {item.id: frozenset(item.lines) for item in loaded.slices}

    def read(slice_id: str) -> SliceBody:
        try:
            return lines[slice_id]
        except KeyError:
            raise SliceInputError(
                f"{slice_id}: 슬라이스 줄이 없다 — 이 run의 slices.json에 없는 id다"
            ) from None

    return read


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
    # `ToolRunner` Protocol에 `images()`를 넣는 것이 최종형이지만 `src/interfaces/`는
    # 여러 모듈이 함께 보는 결합점이라 지금은 이 dataclass가 나른다. 두 구현이 이미
    # `images()`를 갖고 있으므로 승격은 Protocol에 한 줄 추가하는 일이 된다.
    tool_images: dict[str, str] = field(default_factory=dict)
    # 역할 → 그 모델이 generation_config.json에 선언한 샘플링 (#99). `RunRecord.model_sampling`이
    # 된다. `tool_images`와 같은 성격 — 러너는 `src/adapters/`를 import 하지 않으므로(계층 규칙)
    # 어댑터가 모델 파일을 읽어 만든 값을 이 dataclass가 나른다.
    model_sampling: dict[str, ModelSampling] = field(default_factory=dict)
    # 그 run의 clang 계열 도구가 쓴 컴파일 조건 (#74). `RunRecord.compile_flags`가
    # 된다. `tool_images`와 같은 성격이고 같은 이유로 **구현에게 묻는다** — 여기서 상수를
    # 조립하면 Fake와 컨테이너가 같은 조건으로 기록된다.
    compile_flags: CompileFlags | None = None
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
    # 아래 셋(`callees_of`·`call_sites_of`·`definitions_of`)은 **`demote_nonproduct` 키워드**를
    # 받는다(기본 꺼짐 — `GraphIndex.definitions_of` docstring). 러너가 `auditor_definitions`를 켠
    # run에서만 켠다.
    #
    # 함수 위치 → 그 본문 안 호출이 부르는 함수의 정의 위치(#349). Auditor `SOURCE`가 피호출
    # 함수 본문을 붙이는 데 쓴다(`runner._auditor_source`). `symbols_for`와 같은 모양·같은 이유로
    # 콜러블이고, 기본값 `None`("그래프 없음")이 이 필드 없이 조립된 `Deps`의 옛 동작을 지킨다.
    callees_of: Callable[..., list[Location] | None] = lambda _target, **_kw: None
    # 함수 위치 → 그 함수를 부르는 호출 지점(#349 (b)). `callees_of`와 같은 자리.
    call_sites_of: Callable[..., list[Location] | None] = lambda _target, **_kw: None
    # (이름들, 가까운 파일) → 함수 정의 위치. 매크로가 이름만 바꿔 부르는 함수를 찾는다
    # (C4 — 래퍼·별칭 따라가기, `runner._auditor_source`). `callees_of`와 같은 자리.
    functions_named: Callable[..., list[Location] | None] = lambda _names, _file, **_kw: None
    # 함수 위치 → 그 본문의 필드 경유 호출(`sys->alloc(…)`)마다 (필드, 갈 수 있는 함수)(index-v3
    # `fn_bindings`, #304 · C6). Auditor `SOURCE`가 간접 피호출 본문을 붙인다 — 후보 수 상한은
    # 필드마다다. v3 표가 없으면 `None`. `callees_of`와 같이 `demote_nonproduct`를 받는다.
    indirect_calls_of: Callable[..., list[tuple[str, list[Location]]] | None] = (
        lambda _target, **_kw: None
    )
    # (이름, 가까운 파일) → 그 이름에 쓰는 지점 / (필드, 가까운 파일) → 그 필드에 쓰는 지점
    # (index-v3 `writes`·`assigns`·`field_assigns`, #304 · C5(a)). Auditor `SOURCE`의 쓰기 지점 창.
    writes_of: Callable[[str, str], list[Location] | None] = lambda _name, _file: None
    field_writes_of: Callable[[str, str], list[Location] | None] = lambda _field, _file: None
    # (이름들, 가까운 파일) → 매크로·타입·파일 변수·enum 상수의 정의 위치(#349 (f)). `callees_of`와
    # 같은 자리.
    definitions_of: Callable[..., list[Location] | None] = lambda _names, _file, **_kw: None
    # (파일, 함수 이름) → 그 함수의 지역·파라미터 이름(`locals` 표). 정의 조회가 본문 이름에서
    # 이것을 뺀다(`runner._auditor_source`). 기본값 `None`("v2 표 없음")은 뺄 것이 없다는 뜻이다.
    locals_of: Callable[[str, str], frozenset[str] | None] = lambda _file, _func: None
    # `--slices`로 준 슬라이스 → 타깃(ADR 0012 결정 1). `None`이면 슬라이스 없이 도는 run이고
    # 러너는 지금처럼 파일·심볼 타깃을 만든다. 빈 튜플("슬라이스 0개")과 섞지 않는다.
    slice_refs: tuple[SliceRef, ...] | None = None
    # `RunRecord.slicer`가 될 슬라이서 산출 요약. `slice_refs`와 같이 `None`이다.
    slicer: dict[str, str | int | None] | None = None
    # 슬라이스 id → 렌더(gutter·배너). `build_deps`가 run 전에 **전부** 렌더해 둔 dict의 조회다 —
    # drift가 첫 모델 호출 전에 `SliceInputError`로 나야 해서다(run 도중에 나면 앞 슬라이스의 호출을
    # 이미 태운 뒤다).
    # Scout `SOURCE`와 Retriever의 `target_function`이 이 한 조회에서 나온다 — `read_source`가
    # Retriever와 같은 함수여야 하는 것과 같은 이유다.
    read_slice: Callable[[str], str] = _no_slices
    # 역할 → 그 역할 모델의 실 토크나이저 카운터. `None`이면 그 역할은 기본 카운터
    # (글자수 근사 `len//4`)를 유지한다(#96 (b)).
    #
    # **`symbols_for`와 같은 모양이다** — 러너·RunContext는 `src/adapters/`를 import 하지
    # 않으므로 `counter_for_source`를 타입이 아니라 **콜러블로** 경계를 넘겨야 한다. 러너가
    # 역할 캡슐을 조립하기 직전에 `using_counter(counter_for_role(role))`로 켠다. 기본값이
    # "토크나이저 없음"인 것도 `symbols_for`와 같은 의도다 — 이 필드 없이 조립돼도 옛
    # 동작(글자수 근사)이 남는다.
    counter_for_role: Callable[[str], Callable[[str], int] | None] = lambda _role: None
    # 위 카운터가 **무엇으로 정해졌는지**를 run 산출물에 남기기 위한 스칼라 dict.
    # 러너가 run 시작 시 그대로 `token_counter` 이벤트로 낸다.
    #
    # **타입이 아니라 dict인 것이 요점이다** — `IndexSummary.as_trace_fields()`와 같은
    # 이유다. 오케스트레이터가 어댑터 타입을 import 하면 "노드는 구체 어댑터를 모른다"가
    # 깨진다(engineering-guide.md §3, `tests/test_policy.py`가 검사한다).
    #
    # 기본값이 빈 dict인 것도 `symbols_for`·`counter_for_role`과 같은 의도다 — 이 필드 없이
    # 조립된 `Deps`(단위 테스트)가 이벤트를 못 내도 파이프라인은 옛 동작 그대로 돈다.
    counter_report: dict[str, str | None] = field(default_factory=dict)
    # need_evidence resolver hook (ADR 0012 결정 2). `symbols_for`와 같은 자리 — 기본값
    # `None`("resolver 없음")이 이 필드 없이 조립된 `Deps`(단위 테스트)의 옛 동작을 지킨다.
    # `build_deps`는 `symbol_resolver.resolve`를 꽂는다 — 인덱스 판을 run 전에 모르므로 늘
    # 꽂고, v1·인덱스 없음은 `resolve()`가 빈 결과로 가른다.
    resolve_requests: Callable[[ResolveQuery], Resolution] | None = None
    # 단계 경계 모델 교대(ADR 0012 Phase 6.5). `build_deps`가 교대 프로필 + 장비일 때만
    # `model_swap.swap_runner`로 만든다. 기본 `None`("교대 없음")이 이 필드 없이 조립된 `Deps`의
    # 옛 동작이다 — `resolve_requests`와 같은 자리.
    swap_to: SwapTo | None = None
    # 채점기(oracle) 클라이언트 (#386). `build_deps`는 **만들지 않고 받아서 싣는다** — 서버 주소는
    # 조작자(`--oracle-server`)가, 과제 id는 과제 번들이 정하고 그것을 아는 것은 CLI다
    # (`cli._oracle_for`). 기본 `None`이 「제출 안 함」이고 그것이 이 필드 없이 조립된 `Deps`의 옛
    # 동작이다.
    oracle: Oracle | None = None
    # 그 채점기가 기댄 카나리 보고서의 `{canary_path, canary_sha256}`(#487). `slicer`와 같이 러너가
    # 만들지 않고 받는다 - `RunRecord.oracle` 요약에 합쳐진다. `None`이면 안 합친다.
    canary_ref: dict[str, str] | None = None


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
    # 슬라이스 렌더 조회와 그 슬라이서 run의 `run_signature` — Retriever가 슬라이스 타깃의
    # `target_function`을 세고 캐시 키를 가르는 데 쓴다(#220 ②). `Deps.read_slice`와 같은 것이다.
    read_slice: Callable[[str], str] | None = None
    slice_signature: str | None = None
    # 슬라이스 id → 줄 집합. Retriever가 슬라이스 질의의 그래프 섹션 본문으로 쓴다(#220 ①).
    slice_lines: Callable[[str], SliceBody] | None = None

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
            # **index.json을 내는 프로필은 대부분 이쪽이다** — 내는 조건은
            # `tool_runner`가 docker·singularity인 것이고, 그중 `retriever: hybrid`인
            # dev_hybrid_docker·harness_pool_hybrid만 아래 hybrid 빌더로 간다.
            # 여기 그래프를 안 물리면 1단계 산출물을 아무도 읽지 않는다.
            graph=a.graph_index(),
            read_slice=a.read_slice,
            slice_signature=a.slice_signature,
            slice_lines=a.slice_lines,
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
            read_slice=a.read_slice,
            slice_signature=a.slice_signature,
            slice_lines=a.slice_lines,
        ),
    ),
}

# docker·singularity·podman은 **같은 클래스**다. 로컬과 ATOM은 docker, Slurm는 Docker 데몬
# 접근이 없어 singularity — 계약과 판정은 하나이고 명령 조립만 갈린다.
#
# (이 주석은 "ATOM은 데몬 접근도 singularity도 없어 podman"이라고 적고 있었다. 그
# 2026-08-17 실측이 2026-08-21에 반증됐다 — ATOM에 docker 29.2.1과 podman 4.9.3이 함께
# 있다. 프로필 선언값도 2026-08-28에 docker로 되돌렸다: ADR 0005 마지막 절.
# `src/config.py`의 같은 반증 서술과 짝이다.)
#
# 그래서 `describe_wiring()`의 구현 이름만으로는 셋이 구분되지 않는다.
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


def _counted_roles(config: HarnessConfig) -> tuple[str, ...]:
    """`token_counter`가 실을 역할 = 러너가 실제로 카운터를 켜는 자리.

    풀은 캡슐을 **첫 멤버**가 만들어 그 카운터로 예산을 정하고(공유), 2·3번 멤버는 캐시된
    캡슐을 받아 카운터를 안 켠다. 그래서 켜지는 scout 자리는 `SCOUT_POOL_ROLES[0]` 하나다.
    단일 프로필은 `scout`. auditor는 자기 캡슐을 켠다. `COUNTED_ROLES`(단일 기준 상수)를
    프로필에 없는 `scout`째로 돌던 것이 **없는 역할에 `no_source`를 싣던** 원인이었다
    (풀 팬아웃 #178 후속, 이슈 #192).

    멤버 2·3이 첫 멤버의 캡슐을 물려받을 때 **어느 역할로 계산됐나**는 캐시-적중 이벤트의
    `counted_by`가 따로 남긴다(`runner._step_retrieval`·`context.record_cache`) — 그래서 이
    이벤트가 `scout` 자리를 하나만 실어도 회계 근거가 지워지지 않는다. ⚠ 예산이 멤버별
    토크나이저로 **강제**되는 것은 별개이고 공유 캡슐 재검증(#194)에 남는다.
    """
    roles = config.profile.roles
    if all(member in roles for member in SCOUT_POOL_ROLES):
        scout_places: tuple[str, ...] = (SCOUT_POOL_ROLES[0],)
    else:
        scout_places = tuple(r for r in COUNTED_ROLES if r != "auditor")
    return tuple(role for role in (*scout_places, "auditor") if role in roles)


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
    slices_path: Path | None = None,
    device: str | None = None,
    oracle: Oracle | None = None,
    canary_ref: dict[str, str] | None = None,
) -> Deps:
    """프로필 하나에 대응하는 어댑터 묶음을 만든다.

    `slices_path`를 믿을 수 없으면 여기서 `SliceInputError`로 멈춘다 — 모델을 부르기 전이다.
    **렌더(drift 검사)까지 여기서 전부 한다** — run 도중에 렌더하면 앞 슬라이스의 **모델 호출을
    태운 뒤** 죽는다(`slices` 모듈 docstring).
    ⏵ 2026-10-02 정정: *"러너는 그 예외를 안 잡으므로 … 산출물 없이"*는 낡았다. 최후 방어선이
    생겨 **매니페스트는 남는다**(사유 `unforeseen_failure`, `report.md`는 없다). 태운 호출이
    안 돌아오는 것이 이 설계의 이유다.

    `device`는 교대 프로필의 장비 파일(`config/serving/<device>.yaml`)을 고른다. 파일이 없거나
    registry와 어긋나면 `ConfigError`로 멈춘다 — 역시 모델을 부르기 전이다.
    """
    loaded = load_slices(slices_path) if slices_path is not None else None
    rendered = render_slices(loaded, source_root) if loaded is not None else {}
    read_slice = _read_rendered(rendered)
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
        read_slice=read_slice,
        slice_signature=loaded.run_signature if loaded is not None else None,
        slice_lines=_slice_lines(loaded),
        graph=graph,
    )
    # 프롬프트를 받는 역할에만 ModelClient를 만든다. 전 역할을 돌면 embedding·reranker
    # 엔드포인트를 향해 `/chat/completions`를 쏘는 클라이언트가 생긴다 — 아무도 부르지
    # 않아 드러나지 않을 뿐 잘못된 배선이다(ADR 0004: 그 둘은 `Role`이 아니다).
    clients: dict[str, ModelClient] = {
        role: _model_client(config.profile.roles[role], config, role=role)
        for role in PROMPTED_ROLES
        if role in config.profile.roles
    }
    tools = _build("tool_runner", args)

    # 역할 → 실 카운터. `source`가 없거나(fake 등) 그 경로에 `tokenizer.json`이 없으면
    # `None`이라 러너가 기본 카운터(글자수 근사)를 유지한다. `counter_for_source`가
    # source별로 캐시하므로 러너가 후보마다 불러도 토크나이저는 run당 한 번만 로드된다.
    # **역할 가드가 위 `clients`와 같은 조건이다.** `model_key_for`가 없는 역할에
    # `ConfigError`를 던지는데, `_step_retrieval`은 그 카운터를 `Stage.RETRIEVAL` 검사 뒤·
    # `Stage.SCOUT` 검사 **앞**에서 부른다 — 4단계 앞에서 멈추는 run이 scout 없는 프로필로
    # 돌면 거기서 죽는다.
    # ⚠ **풀에서는 그 역할이 `scout`이 아니라 멤버 키다**(PR #178) — 가드가 보는 집합이
    # `profile.roles`라 멤버도 그대로 지난다. 지금 프로필 전부가 scout·auditor를
    # 정의하므로 실현되지 않지만,
    # 두 줄 위와 모양이 다른 것 자체가 다음 사람이 밟을 자리다.
    def counter_for_role(role: str) -> Callable[[str], int] | None:
        if role not in config.profile.roles:
            return None
        source = config.model_for(role).source
        return counter_for_source(source) if source else None

    def counter_report() -> dict[str, str | None]:
        """`token_counter` 이벤트의 `data`. **필드 이름이 곧 지표의 열 이름이다.**

        `IndexSummary.as_trace_fields()`와 같은 자리·같은 계약이다 — 어댑터가 스칼라 dict를
        만들고 오케스트레이터는 그것만 받는다. 키 집합을 고정하는 것은 둘이다:
        `tests/test_pipeline_e2e.py`의 `test_token_counter_is_traced_exactly_once_…`(trace에
        실제로 실린 `data`를 통째로 대조)와 `tests/test_adapters.py`(여기서 만든 dict를 직접
        대조).

        **`counter_for_role`과 같은 판정에서 나온다**(`_resolve_counter`). 갈리면 러너가 켠
        카운터와 기록이 어긋나고, 어긋난 기록은 없는 것보다 나쁘다.

        **`build_deps()`에서 즉시 불린다 — 카운터가 여기서 만들어진다.** 그래서 토크나이저
        로드(수 MB 파싱)와 "파일이 있는데 깨짐 → 예외"가 `runner._step_retrieval`이 아니라 조립
        시점에 일어난다. 의도다 — `token_counter`는 **모델을 부르기 전에** 나와야 하고
        (`orchestrator/trace.py`), 깨진 토크나이저로 도는 run은 첫 호출 전에 죽는 편이 낫다.
        `counter_for_source`가 캐시하므로 로드 횟수는 늘지 않고, `doctor`는
        `describe_wiring()`만 쓰므로 이 경로를 지나지 않는다.

        **해석된 경로는 담지 않는다** — trace 재현성 기준이 경로 절댓값을 금지하고
        (`orchestrator/trace.py`) HF 캐시 경로는 `Path.home()`을 지난다. `source`에 리비전이
        박혀 있으므로 스냅샷은 그것으로 특정된다.
        """
        fields: dict[str, str | None] = {}
        for role in _counted_roles(config):
            source = config.model_for(role).source if role in config.profile.roles else None
            counter = counter_for_role(role)
            fields[f"{role}_counter"] = "real" if counter is not None else "approx"
            fields[f"{role}_source"] = source
            # `source`가 없으면 해석을 시도조차 안 했으므로 사유는 `no_source`다.
            # 있는데 `None`이면 왜인지는 어댑터가 안다 — 여기서 다시 추론하지 않는다.
            if counter is not None:
                fields[f"{role}_reason"] = None
            elif source:
                fields[f"{role}_reason"] = counter_reason_for_source(source)
            else:
                fields[f"{role}_reason"] = REASON_NO_SOURCE
        return fields

    return Deps(
        # 채점기(#386) — factory가 만들지 않고 받는다. 서버 주소와 과제 신원을 아는 것은 CLI다.
        oracle=oracle,
        canary_ref=canary_ref,
        models=RoutingModelClient(clients),
        retriever=_build("retriever", args),
        tools=tools,
        store=_build("state_store", args),
        ids=ids,
        # 구현에게 묻는다. 여기서 이미지 이름을 조립하면 어댑터가 무엇을 실제로 물렸는지와
        # 갈라질 수 있고, 갈라진 재현성 기록은 없는 것보다 나쁘다.
        tool_images=tools.images(),
        # 같은 이유로 구현에게 묻는다 (#74). Fake는 빈 조건을 내고, 그 빈 값은
        # `RunRecord.compile_flags`의 `None`(옛 manifest)과 구분된다.
        compile_flags=tools.compile_flags(),
        # 실서빙 모델 파일에서 선언 샘플링을 읽는다. Fake·source 없는 모델은 빠지고, 전부
        # 그러면 빈 dict다 — `RunRecord.model_sampling`의 `None`(옛 manifest)과 구분된다.
        model_sampling=declared_sampling_by_role(config),
        # Retriever가 캡슐의 그래프 섹션을 채우는 것과 **같은 로더**다. 따로 파싱하면
        # 후보의 근사와 그래프의 근사가 갈리고 그 차이가 어느 기록에도 안 남는다.
        symbols_for=graph.symbols_for,
        callees_of=graph.callees_of,
        call_sites_of=graph.call_sites_of,
        functions_named=graph.functions_named,
        indirect_calls_of=graph.indirect_calls_of,
        writes_of=graph.writes_of,
        field_writes_of=graph.field_writes_of,
        definitions_of=graph.definitions_of,
        locals_of=graph.locals_of,
        slice_refs=slice_refs(loaded) if loaded is not None else None,
        slicer=slicer_summary(loaded) if loaded is not None else None,
        # Retriever에 넣은 것과 **같은 조회**다(위 `_BuildArgs`) — Scout `SOURCE`와 캡슐의
        # `target_function` 토큰이 한 렌더에서 나온다.
        read_slice=read_slice,
        counter_for_role=counter_for_role,
        counter_report=counter_report(),
        # NE resolver 배선(ADR 0012 결정 2). `graph`는 인덱스 판을 몰라도 늘 만들어지므로
        # 훅은 항상 꽂는다 — v1·인덱스 없음 판정은 `resolve()` 안에서 한다(그 모듈 docstring).
        resolve_requests=partial(
            resolve, table=graph.table, read_lines=partial(_read_target_lines, source_root)
        ),
        swap_to=_swap_to(config, run_id=run_id, device=device),
    )


def _swap_to(config: HarnessConfig, *, run_id: str, device: str | None) -> SwapTo | None:
    """교대 프로필 + 장비면 장비 파일의 명령으로 교대 콜러블을 만든다. 아니면 `None`.

    **장비가 없으면 만들지 않는다** — 모델에 안 닿는 run(`--dry-run`·낮은 `--stop-after`)은 교대도
    안 하므로 장비 없이 조립돼야 한다. 닿는 run은 `cli.run`이 `--device` 없이 먼저 거부한다.
    """
    swap = config.swap
    if swap is None or device is None:
        return None
    serving = load_serving(device)
    check_serving(config, serving)
    return swap_runner(
        serving.command,
        models=config.models,
        deadline_s=swap.settle_timeout_s,
        run_id=run_id,
        device=device,
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
            client = _model_client(config.profile.roles[role], config, role=role)
            wiring[role] = type(client).__name__
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


def _model_client(model_key: str, config: HarnessConfig, *, role: str) -> ModelClient:
    """endpoint 스킴이 구현을 고른다. 역할마다 따로 판정하므로 혼용이 가능하다.

    `model_key`가 곧 `ProducedBy.model`에 남는 이름이다 — registry 키와 결과 기록이
    갈라지면 조건 비교에서 어느 모델이었는지 되짚을 수 없다.

    **`role`에 기본값을 두지 않는다.** 출력 상한이 역할별로 갈릴 수 있으므로
    (`Thresholds.output_max_for`), 기본값이 있으면 역할을 빠뜨린 조립 지점이 조용히
    전역값을 실어 보낸다 — `max_tokens`가 필수 키워드인 것과 정확히 같은 사유다.
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
        # **역할별 값이다** — 선언이 없으면 `thresholds.output_max`를 그대로 돌려준다
        # (#79의 (c)). 해석은 전부 `output_max_for`에서 하고 여기서 분기하지 않는다.
        max_tokens=config.thresholds.output_max_for(role),
        # **모델별 값이다** — 위 넷과 달리 `config.sampling`·`thresholds`(프로필 공통)가
        # 아니라 registry 항목에서 온다. 선언 안 한 모델은 `None`이라 요청에 안 실린다.
        reasoning_effort=entry.reasoning_effort,
        # 같은 규칙의 나머지 모델별 값(ADR 0012 Phase 6.5). 비었으면 payload 키 집합이 그대로다.
        extra_params=entry.request_params(),
        timeout_s=entry.request_timeout_s,
        retries=config.thresholds.schema_retries,
    )


