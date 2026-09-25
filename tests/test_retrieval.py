"""HybridRetriever와 검색 부품 어댑터.

`dev_hybrid` 프로필로 GPU 없이 광역 수집 → embedding → reranker 경로를 태운다.
실서빙과의 차이는 두 클라이언트 구현뿐이므로(선택은 endpoint 스킴), 여기서 통과하는
경로는 vLLM을 붙여도 같은 경로다.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from src import cli
from src.adapters.factory import build_deps
from src.adapters.fake_retriever import FakeRetriever
from src.adapters.hybrid_retriever import (
    HybridRetriever,
    _chunk_body,
    read_symbol_source,
)
from src.adapters.vllm_retrieval import (
    FakeEmbeddingClient,
    FakeRerankerClient,
    Ranked,
)
from src.config import ConfigError, load_config
from src.ids import IdFactory
from src.interfaces.retriever import Query
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.runner import LateTruncateHook, RunSpec, TruncateHookUnbound
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence
from src.tokens import estimate_tokens

RUN_ID = "run_test_0001"

# `cli.execute()` 배선 검사에 쓰는 실제 대상 트리. 파이프라인은 dry-run으로 3단계까지만
# 돈다 — 여기서 재는 것은 조립이지 분석 결과가 아니다.
_TARGET = Path(__file__).resolve().parent / "fixtures" / "target"


def _need(missing_id: str) -> MissingEvidence:
    return MissingEvidence(
        missing_id=missing_id,
        run_id=RUN_ID,
        finding_id="find_0001",
        need="호출부 문맥",
        how_to_obtain="retrieval 확장",
        blocking=False,
    )


class SpyEmbedder:
    """호출 여부와 배치 크기를 기록하는 EmbeddingClient."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._inner = FakeEmbeddingClient()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return self._inner.embed(texts)


class SpyReranker:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self._inner = FakeRerankerClient()

    def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        self.calls.append(len(documents))
        return self._inner.rerank(query=query, documents=documents, top_n=top_n)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """대상 파일 하나 + 검색 대상 파일 여러 개."""
    (tmp_path / "target.py").write_text(
        "def handle(req):\n    return req.user.token\n", encoding="utf-8"
    )
    # 조각 하나가 넉넉히 커야 "예산이 related를 잘라내는" 경로를 테스트할 수 있다.
    for i in range(4):
        (tmp_path / f"mod{i}.py").write_text(
            f"def helper{i}(token):\n    # auth token check {i}\n    return token\n" * 30,
            encoding="utf-8",
        )
    (tmp_path / "notes.txt").write_text("token token token\n", encoding="utf-8")
    return tmp_path


def _bare_context(trace: TraceWriter) -> RunContext:
    """훅이 붙을 최소 실행 문맥. 절단 이벤트가 trace까지 가는지만 본다."""
    config = load_config("dev_fake")
    deps = build_deps(
        config,
        run_id=RUN_ID,
        source_root=_TARGET,
        artifacts_root=_TARGET.parent,
    )
    return RunContext(
        run_id=RUN_ID,
        config=config,
        models=deps.models,
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        read_source=lambda target: "",
        monotonic_ms=lambda: 0,
    )


def _retriever(root: Path, **kwargs) -> tuple[HybridRetriever, SpyEmbedder, SpyReranker]:
    embedder, reranker = SpyEmbedder(), SpyReranker()
    return (
        HybridRetriever(
            root=root,
            ids=IdFactory(RUN_ID),
            run_id=RUN_ID,
            embedder=embedder,
            reranker=reranker,
            **kwargs,
        ),
        embedder,
        reranker,
    )


def _query(budget: int = 4096, keywords: list[str] | None = None) -> Query:
    return Query(
        run_id=RUN_ID,
        target=Location(file="target.py"),
        keywords=keywords if keywords is not None else ["token"],
        budget_tokens=budget,
    )


# --- 검색 경로가 실제로 타는가 ------------------------------------------------


def test_expanded_level_calls_embedding_and_reranker(corpus: Path) -> None:
    retriever, embedder, reranker = _retriever(corpus)
    capsule = retriever.search(_query(), CapsuleLevel.EXPANDED)

    assert embedder.calls, "embedding이 호출되지 않았다"
    assert reranker.calls, "reranker가 호출되지 않았다"
    assert any(section.kind == "related" for section in capsule.sections)


def test_light_level_does_not_retrieve(corpus: Path) -> None:
    """Scout는 후보 전량에 대해 도는 단계라 여기서 임베딩을 부르면 배칭 전제가 무너진다."""
    retriever, embedder, reranker = _retriever(corpus)
    capsule = retriever.search(_query(), CapsuleLevel.LIGHT)

    assert embedder.calls == []
    assert reranker.calls == []
    assert {section.kind for section in capsule.sections} == {"target_function", "static_signal"}


def test_embedding_is_batched_in_one_call(corpus: Path) -> None:
    """후보를 한 건씩 호출하면 왕복 지연이 곧 병목이 된다(model-serving.md §2)."""
    retriever, embedder, _ = _retriever(corpus)
    retriever.search(_query(), CapsuleLevel.EXPANDED)

    assert len(embedder.calls) == 1
    # query 1건 + 후보 N건이 한 배치로 나간다.
    assert embedder.calls[0] > 1


# --- 섹션 내용이 실측인가 -----------------------------------------------------


def test_related_tokens_come_from_selected_chunks_not_a_constant(corpus: Path) -> None:
    """고정값을 쓰면 캡슐 예산 초과 경로가 영원히 안 타고 그 경로가 검증되지 않는다."""
    retriever, _, _ = _retriever(corpus)
    capsule = retriever.search(_query(), CapsuleLevel.EXPANDED)

    related = next(s for s in capsule.sections if s.kind == "related")
    assert related.items is not None and related.items > 0
    # FakeRetriever의 보조 섹션 고정값(64)과 우연히 같을 수는 있으나, items가 여러 건인데
    # 토큰이 그 상수라면 실측이 아니다.
    assert related.tokens >= related.items


def test_target_file_is_excluded_from_related(corpus: Path) -> None:
    """대상 자신은 target_function이 이미 담고 있다. 두 번 세면 토큰이 부풀고
    reranker 상위가 자기 자신으로 채워진다."""
    retriever, _, _ = _retriever(corpus)
    query = _query()
    selected = retriever._retrieve(query)

    assert selected, "선별 결과가 비었다 — 이 픽스처에서는 후보가 있어야 한다"
    assert all(chunk.file != query.target.file for chunk in selected)


def test_non_source_files_are_not_collected(corpus: Path) -> None:
    """notes.txt에 키워드가 있어도 소스 확장자가 아니면 후보가 아니다."""
    retriever, _, _ = _retriever(corpus)
    selected = retriever._retrieve(_query())

    assert all(not chunk.file.endswith(".txt") for chunk in selected)


# --- 예산·결정론 --------------------------------------------------------------


def test_sections_never_exceed_the_capsule_budget(corpus: Path) -> None:
    retriever, _, _ = _retriever(corpus)
    capsule = retriever.search(_query(budget=40), CapsuleLevel.EXPANDED)

    assert capsule.used_tokens <= capsule.budget_tokens


def test_pool_profile_shrinks_the_capsule_budget_by_the_margin() -> None:
    """풀 프로필에서 3단계 예산이 마진(#194)만큼 줄어 Query에 실린다. 단일은 안 줄어든다.

    공유 캡슐을 첫 멤버(qwen) 토크나이저로 재는데 계열이 다른 멤버(2026-09-13 실측 때의 mistral
    `scout_boundary`)가
    같은 텍스트를 최대 ~11% 더 세므로, 첫 멤버 기준 예산을 factor로 줄여 담아야 세 자리가
    전부 상한 안에 든다(2026-09-13 ATOM 실측). 단일 프로필은 자리가 하나라 마진이 없다 —
    실측 없이 상한을 줄이면 §0의 "안 잰 값으로 채우지 않는다"에 걸린다.
    """
    from src.config import SCOUT_POOL_ROLES
    from src.orchestrator.nodes import retrieval as retrieval_node
    from src.orchestrator.nodes.retrieval import _POOL_CAPSULE_MARGIN

    trace = TraceWriter(RUN_ID)
    ctx = _bare_context(trace)
    target = Location(file="x.c", symbol="f", line_start=1, line_end=2)

    captured: list[int] = []
    inner = ctx.retriever.search

    def spy(query: Query, level: CapsuleLevel) -> object:
        captured.append(query.budget_tokens)
        return inner(query, level)

    ctx.retriever.search = spy  # type: ignore[method-assign]
    base = ctx.config.thresholds.budget_for(CapsuleLevel.LIGHT)

    # 단일 `scout` 프로필(dev_fake) — 마진 없이 상한 그대로 나간다.
    retrieval_node.run(ctx, target=target, level=CapsuleLevel.LIGHT)
    assert captured[-1] == base, "단일 프로필에 마진이 붙었다"

    # 풀 세 자리를 선언하면 예산이 마진만큼 줄어든다.
    pool_roles = {role: "fake" for role in SCOUT_POOL_ROLES}
    pool_roles.update({"auditor": "fake", "critic": "fake"})
    ctx.config = ctx.config.transition(profile=ctx.config.profile.transition(roles=pool_roles))
    retrieval_node.run(ctx, target=target, level=CapsuleLevel.LIGHT)
    assert captured[-1] == int(base * _POOL_CAPSULE_MARGIN), "풀 예산에 마진이 안 붙었다"
    assert captured[-1] < base


def test_two_instances_produce_the_same_sections(corpus: Path) -> None:
    """같은 입력에 같은 캡슐이 나오지 않으면 재현성이 깨진다(engineering-guide.md §5)."""
    first, _, _ = _retriever(corpus)
    second, _, _ = _retriever(corpus)

    a = first.search(_query(), CapsuleLevel.EXPANDED)
    b = second.search(_query(), CapsuleLevel.EXPANDED)

    assert a.cache_key == b.cache_key
    assert [s.model_dump() for s in a.sections] == [s.model_dump() for s in b.sections]


def test_cache_hit_is_reported_on_second_search(corpus: Path) -> None:
    events: list[tuple[str, bool]] = []
    retriever, _, _ = _retriever(corpus, on_cache=lambda key, hit: events.append((key, hit)))

    retriever.search(_query(), CapsuleLevel.EXPANDED)
    retriever.search(_query(), CapsuleLevel.EXPANDED)

    assert [hit for _, hit in events] == [False, True]


def test_cache_key_differs_from_fake_retriever(corpus: Path) -> None:
    """캡슐 구성 규칙이 다르면 캐시 키도 달라야 한다(knowledge-base.md §6).

    같으면 두 구현이 만든 캡슐이 같은 키를 공유해 조건 비교가 오염된다.
    """
    hybrid, _, _ = _retriever(corpus)
    fake = FakeRetriever(root=corpus, ids=IdFactory(RUN_ID), run_id=RUN_ID)
    query = _query()

    assert hybrid.cache_key_for(query, CapsuleLevel.EXPANDED) != fake.cache_key_for(
        query, CapsuleLevel.EXPANDED
    )


# --- 슬라이스 타깃 (ADR 0012 결정 1 · #220 ②) ---------------------------------

# 같은 entry(`target.py`:1)를 가진 두 슬라이스의 렌더. `build_deps`가 Retriever에 넣는 것도 렌더
# dict의 `__getitem__`이다 — 여기서는 그 dict를 손으로 만든다.
_RENDERS = {
    "slice_0001": "/* ===== target.py ===== */\n1| def handle(req):",
    "slice_0002": "/* ===== target.py ===== */\n1| def handle(req):\n2|     return req.user.token",
}


def _slice_retriever(kind: str, root: Path, *, signature: str = "sig_0001"):  # noqa: ANN202
    kwargs = {"read_slice": _RENDERS.__getitem__, "slice_signature": signature}
    if kind == "fake":
        return FakeRetriever(root=root, ids=IdFactory(RUN_ID), run_id=RUN_ID, **kwargs)
    return _retriever(root, **kwargs)[0]


def _slice_query(slice_id: str) -> Query:
    return Query(
        run_id=RUN_ID,
        target=Location(file="target.py", line_start=1, symbol="handle"),
        budget_tokens=4096,
        slice_id=slice_id,
    )


@pytest.mark.parametrize("kind", ["fake", "hybrid"])
def test_twin_slices_get_their_own_capsule_built_from_their_render(corpus: Path, kind: str) -> None:
    """entry가 같은 두 슬라이스는 다른 캡슐이다. 키에 slice id가 없으면 둘째가 첫째의 캡슐을
    재사용하고, `target_function` 토큰이 자기가 싣지 않는 코드를 센다."""
    retriever = _slice_retriever(kind, corpus)

    capsules = {sid: retriever.search(_slice_query(sid), CapsuleLevel.LIGHT) for sid in _RENDERS}

    assert capsules["slice_0001"].capsule_id != capsules["slice_0002"].capsule_id
    for sid, capsule in capsules.items():
        (section,) = [s for s in capsule.sections if s.kind == "target_function"]
        assert section.tokens == estimate_tokens(_RENDERS[sid])


@pytest.mark.parametrize("kind", ["fake", "hybrid"])
def test_another_slicer_run_changes_the_capsule_key(corpus: Path, kind: str) -> None:
    """slice id는 슬라이서 run 안의 순번이다(`slicer_candidates.py`의 `slice_{number:04d}`) — 다른
    run에서는 같은 id가 다른 슬라이스일 수 있다. 그래서 `run_signature`가 키에 든다."""
    query = _slice_query("slice_0001")

    first = _slice_retriever(kind, corpus, signature="sig_0001").cache_key_for(
        query, CapsuleLevel.LIGHT
    )
    second = _slice_retriever(kind, corpus, signature="sig_0002").cache_key_for(
        query, CapsuleLevel.LIGHT
    )

    assert first != second


def test_truncation_is_reported_not_silent(corpus: Path) -> None:
    """상한에 걸려 잘린 것을 조용히 두면 '전부 봤다'로 읽힌다."""
    seen: list[tuple[int, int]] = []
    retriever, _, _ = _retriever(corpus, max_chunks=1, on_truncate=lambda a, b: seen.append((a, b)))
    retriever.search(_query(), CapsuleLevel.EXPANDED)

    assert seen and seen[0][1] == 1


def test_truncation_reaches_the_trace_not_just_the_callback(corpus: Path) -> None:
    """훅이 trace 이벤트가 돼야 절단을 사후에 셀 수 있다 (W7).

    `HybridRetriever`도 `build_deps()`도 이 훅을 받을 준비가 되어 있었는데
    `cli.execute()`가 만들어 넘기지 않아, **절단이 아무 데도 안 남았다.**
    """
    trace = TraceWriter(RUN_ID)
    hook = LateTruncateHook()
    retriever, _, _ = _retriever(corpus, max_chunks=1, on_truncate=hook)
    hook.bind(_bare_context(trace))

    retriever.search(_query(), CapsuleLevel.EXPANDED)

    truncated = [e for e in trace.events if e.kind is EventKind.RETRIEVAL_TRUNCATED]
    assert len(truncated) == 1
    assert truncated[0].data["kept"] == 1
    assert truncated[0].data["collected"] > truncated[0].data["kept"]


def test_truncate_hook_refuses_to_fire_before_it_is_bound() -> None:
    """조용히 무시하면 절단 이벤트가 새고, 그러면 '안 잘렸다'와 구별되지 않는다.

    나머지 세 훅(`LateCacheHook`·`LateUsageHook`·`LateIndexHook`)과 같은 계약이다.
    """
    with pytest.raises(TruncateHookUnbound):
        LateTruncateHook()(400, 1)


def test_cli_passes_the_truncate_hook_into_the_adapters(monkeypatch, tmp_path: Path) -> None:
    """배선의 시작점이 `cli.execute()`다. 여기서 안 넘기면 나머지가 다 준비돼도 죽는다.

    훅 자체가 아니라 **넘겼는가**를 본다 — `dev_fake`는 절단이 일어나지 않는 프로필이고,
    이 결함은 절단 로직이 아니라 조립 순서에서 났다.
    """
    passed: dict[str, object] = {}
    real = cli.build_deps

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        passed.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "build_deps", spy)
    run_dir = tmp_path / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    cli.execute(
        load_config("dev_fake"),
        RunSpec(condition="B3prime@pipeline", target=_TARGET, dry_run=True),
        run_id=run_dir.name,
        run_dir=run_dir,
    )

    assert isinstance(passed.get("on_truncate"), LateTruncateHook)


# --- expand -------------------------------------------------------------------


def test_expand_does_not_partial_add_an_overflowing_pool_chunk(corpus: Path) -> None:
    """다음 풀 조각이 남은 예산에 통째로 안 들어가면 붙이지 않는다 (부분 절단 금지, D13).

    예전에는 `min(chunk.tokens, remaining)`으로 조각을 반쪽만 세어 `items`를 늘렸다 —
    본문 없이 토큰만 더한 것이라 캡슐이 자기 내용과 어긋났다. 이제 `related`의 본문이
    실리므로(각 조각의 `file:line`+코드), 조각은 **통째로 들어가거나 안 들어간다.**
    남은 예산이 조각 하나보다 작으면 늘리지 않고 그대로 돌려준다.
    """
    measure, _, _ = _retriever(corpus)
    pool = measure._retrieve(_query())
    assert len(pool) >= 3, "이 픽스처는 최소 3개 조각을 선별해야 한다"

    # 앞의 2건만 들어가고, 세 번째 조각을 통째로 담기엔 부족한 슬랙만 남는 예산.
    # 조각 비용은 `_chunk_body`(file:line+코드)로 잰다 — 코드가 쓰는 단위와 같아야
    # 파일 경로가 길어져도 `before.items == 2`가 조용히 뒤집히지 않는다.
    target_tokens = max(1, len(read_symbol_source(corpus, Location(file="target.py"))) // 4)
    related_cost = sum(estimate_tokens(_chunk_body(chunk)) for chunk in pool[:2])
    budget = target_tokens + related_cost + 64 + 30

    retriever, _, _ = _retriever(corpus)
    capsule = retriever.search(_query(budget=budget), CapsuleLevel.EXPANDED)
    before = next(s for s in capsule.sections if s.kind == "related")
    assert before.items == 2, "예산이 앞의 2건만 남기도록 잡히지 않았다"
    assert before.body, "related 섹션에 선별한 코드 본문이 실려야 한다 (D13)"

    expanded = retriever.expand(capsule, _need("mev_0001"))
    after = next(s for s in expanded.sections if s.kind == "related")

    # related는 안 늘어난다(부분 절단 금지)…
    assert after.items == 2, "통째로 안 들어가는 조각을 반쪽만 붙이면 안 된다"
    assert after.tokens == before.tokens
    # …하지만 조각 하나 못 넣었다고 확장을 포기하진 않는다 — 남은 예산으로 다른 섹션이
    # 붙어 캡슐 전체는 자란다(🔴1: 이른 `return` 제거).
    assert expanded.expansion_count == capsule.expansion_count + 1


def test_expand_returns_same_capsule_when_budget_is_full(corpus: Path) -> None:
    """'상한 초과'와 '더 붙일 것이 없음'은 다른 사건이라 섞지 않는다."""
    retriever, _, _ = _retriever(corpus)
    capsule = retriever.search(_query(budget=40), CapsuleLevel.EXPANDED)

    # 예산을 꽉 채운 캡슐은 확장되지 않고 그대로 돌아온다.
    filled = capsule.transition(
        sections=[{"kind": "target_function", "tokens": capsule.budget_tokens}]
    )
    same = retriever.expand(filled, _need("mev_0002"))
    assert same is filled


# --- 설정·조립 ----------------------------------------------------------------


def test_dev_hybrid_profile_builds_a_hybrid_retriever(tmp_path: Path) -> None:
    config = load_config("dev_hybrid")
    deps = build_deps(
        config, run_id=RUN_ID, source_root=tmp_path, artifacts_root=tmp_path
    )
    assert isinstance(deps.retriever, HybridRetriever)


def test_dev_fake_profile_still_builds_a_fake_retriever(tmp_path: Path) -> None:
    config = load_config("dev_fake")
    deps = build_deps(
        config, run_id=RUN_ID, source_root=tmp_path, artifacts_root=tmp_path
    )
    assert isinstance(deps.retriever, FakeRetriever)


def test_pipeline_actually_calls_embedding_and_reranker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이 프로젝트가 실제로 겪은 버그의 회귀 테스트.

    프로필에 `embedding`·`reranker`가 적혀 있는데 `factory`가 Retriever를 Fake로
    하드코딩해서 **두 모델이 한 번도 호출되지 않았다**. 설정만 보면 실서빙 중으로
    읽히므로 벤치마크 조건표가 조용히 오염된다.

    어댑터 단위 테스트로는 이 버그를 못 잡는다 — 어댑터는 정상이었고 배선이 문제였다.
    그래서 파이프라인을 실제로 완주시키고 호출 여부를 본다.
    """
    from datetime import UTC, datetime

    from src import cli
    from src.adapters import factory
    from src.orchestrator.runner import RunSpec

    embedder, reranker = SpyEmbedder(), SpyReranker()
    monkeypatch.setattr(factory, "_embedding_client", lambda config: embedder)
    monkeypatch.setattr(factory, "_reranker_client", lambda config: reranker)

    # Scout가 후보를 하나도 승격시키지 않으면 EXPANDED 캡슐이 안 만들어지고 검색도
    # 안 탄다. 승격이 일어나는 픽스처를 쓴다(test_pipeline_e2e.py와 같은 대상).
    target = Path(__file__).resolve().parent / "fixtures" / "target"
    run_dir = tmp_path / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    spec = RunSpec(
        condition="B3prime@pipeline",
        target=target,
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        result, _ = cli.execute(
            load_config("dev_hybrid"),
            spec,
            run_id=run_dir.name,
            run_dir=run_dir,
            trace_stream=stream,
        )

    assert result.findings, "Auditor까지 도달하지 못했다 — 이 픽스처는 승격되어야 한다"
    assert embedder.calls, "파이프라인이 완주했는데 embedding이 한 번도 호출되지 않았다"
    assert reranker.calls, "파이프라인이 완주했는데 reranker가 한 번도 호출되지 않았다"


def test_hybrid_without_retrieval_roles_fails_at_config_load(tmp_path: Path) -> None:
    """factory에서 KeyError로 터지면 어느 프로필이 잘못됐는지가 스택 아래에 묻힌다."""
    import shutil

    import yaml

    config_dir = tmp_path / "config"
    shutil.copytree(Path("config"), config_dir)
    doc = yaml.safe_load((config_dir / "profiles.yaml").read_text("utf-8"))
    broken = doc["profiles"]["dev_hybrid"]
    del broken["roles"]["reranker"]
    (config_dir / "profiles.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), "utf-8")

    with pytest.raises((ConfigError, ValueError), match="reranker"):
        load_config("dev_hybrid", config_dir=config_dir)
