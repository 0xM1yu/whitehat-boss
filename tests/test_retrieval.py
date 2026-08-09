"""HybridRetriever와 검색 부품 어댑터.

`dev_hybrid` 프로필로 GPU 없이 광역 수집 → embedding → reranker 경로를 태운다.
실서빙과의 차이는 두 클라이언트 구현뿐이므로(선택은 endpoint 스킴), 여기서 통과하는
경로는 vLLM을 붙여도 같은 경로다.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from src.adapters.factory import build_deps
from src.adapters.fake_retriever import FakeRetriever
from src.adapters.hybrid_retriever import HybridRetriever, read_symbol_source
from src.adapters.vllm_retrieval import (
    FakeEmbeddingClient,
    FakeRerankerClient,
    Ranked,
)
from src.config import ConfigError, load_config
from src.ids import IdFactory
from src.interfaces.retriever import Query
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence

RUN_ID = "run_test_0001"


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


def test_truncation_is_reported_not_silent(corpus: Path) -> None:
    """상한에 걸려 잘린 것을 조용히 두면 '전부 봤다'로 읽힌다."""
    seen: list[tuple[int, int]] = []
    retriever, _, _ = _retriever(corpus, max_chunks=1, on_truncate=lambda a, b: seen.append((a, b)))
    retriever.search(_query(), CapsuleLevel.EXPANDED)

    assert seen and seen[0][1] == 1


# --- expand -------------------------------------------------------------------


def test_expand_pulls_one_more_from_the_pool(corpus: Path) -> None:
    """예산이 초기 채움을 자른 경우에만 풀에 남은 조각이 있다.

    풀이 이미 소진됐으면 `related`를 늘리는 대신 다음 보조 섹션으로 넘어가는 것이
    정상이다 — 그래서 여기서는 예산을 좁혀 잘리는 상황을 만든다.
    """
    measure, _, _ = _retriever(corpus)
    pool = measure._retrieve(_query())
    assert len(pool) >= 3, "이 픽스처는 최소 3개 조각을 선별해야 한다"

    # 앞의 2건만 들어가고, static_signal(64) 뒤로 확장 여지가 30 남는 예산.
    target_tokens = max(1, len(read_symbol_source(corpus, Location(file="target.py"))) // 4)
    budget = target_tokens + pool[0].tokens + pool[1].tokens + 64 + 30

    retriever, _, _ = _retriever(corpus)
    capsule = retriever.search(_query(budget=budget), CapsuleLevel.EXPANDED)
    before = next(s for s in capsule.sections if s.kind == "related")
    assert before.items == 2, "예산이 앞의 2건만 남기도록 잡히지 않았다"

    expanded = retriever.expand(capsule, _need("mev_0001"))
    after = next(s for s in expanded.sections if s.kind == "related")

    assert after.items == (before.items or 0) + 1
    assert after.tokens >= before.tokens
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
            load_config("dev_hybrid"), spec, run_dir=run_dir, trace_stream=stream
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
