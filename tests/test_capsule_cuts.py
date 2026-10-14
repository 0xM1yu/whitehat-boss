"""`search` 시점 캡슐 예산 절단이 캡슐에 표지로 남는가 (#663).

**세는 자리는 넷이고 함정은 셋이다.**

- `_sections_for`의 `break` — 섹션이 통째로 안 만들어진다(`dropped_sections`)
- `graph_section`의 `stop=True` — 실을 것은 있는데 예산이 없다(`graph_stopped`)
- 그래프 섹션이 일부만 담긴다(`graph_skipped`) — 이슈 본문의 셋 밖이지만 `items=k`가 잘린 값인지
  모르는 같은 모양이라 같이 센다
- `hybrid_retriever._fit_related` — 선별된 조각이 안 든다(`related_skipped`)

함정(세면 안 되는 것): `min(cost, remaining)`(내용을 안 바꾼다), `retrieval_truncated`(청크 개수
절단), 캡슐 채움률.

**`None`과 「안 잘렸다」는 다르다.** 새로 조립한 캡슐은 `CapsuleCuts()`(빈 값)를 들고, `None`은
「기록 안 했다」(이 필드가 생기기 전 데이터)다 — 아래에서 둘 다 고정한다.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from src.adapters.container_tools import index_output_path
from src.adapters.factory import build_deps
from src.adapters.fake_retriever import _AUX_SECTION_TOKENS, FakeRetriever
from src.adapters.hybrid_retriever import HybridRetriever, read_symbol_source
from src.adapters.index_graph import GraphIndex
from src.adapters.sqlite_store import SqliteStateStore, StateDbVersionError
from src.adapters.vllm_retrieval import FakeEmbeddingClient, FakeRerankerClient
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.retriever import Query
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import retrieval
from src.orchestrator.nodes.scout import describe_sections, describe_target
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.capsule import CapsuleCuts, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel, Role
from src.tokens import estimate_tokens

RUN_ID = "run_test_0001"

_PARSER = "void parse_header(void) { helper(); }\n"

# 호출자 둘이 각각 `_AUX_SECTION_TOKENS`보다 큰 줄이다 — 그래야 「첫째는 들고 둘째는 안 들고 뒤의
# `static_signal` 자리(64)는 남는」 예산이 있다(`test_a_graph_section_that_only_partly_fits…`).
_LONG_A = "a" * 400
_LONG_B = "b" * 400
_INDEX = {
    "version": "index-v1",
    "files": ["main.c", "parser.c"],
    "skipped": [],
    "symbols": [
        {"name": _LONG_A, "file": "main.c", "line_start": 5, "line_end": 8},
        {"name": _LONG_B, "file": "main.c", "line_start": 10, "line_end": 15},
        {"name": "parse_header", "file": "parser.c", "line_start": 1, "line_end": 1},
    ],
    "calls": [
        {"caller": _LONG_A, "callee": "parse_header", "file": "main.c", "line": 6},
        {"caller": _LONG_B, "callee": "parse_header", "file": "main.c", "line": 12},
    ],
    "unresolved_calls": 0,
    "unresolved_symbols": 0,
    "parse_errors": [],
}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    (root / "parser.c").write_text(_PARSER, encoding="utf-8")
    (root / "main.c").write_text("int main(void) { parse_header(); }\n", encoding="utf-8")
    return root


def _graph(tmp_path: Path) -> GraphIndex:
    path = index_output_path(tmp_path / "artifacts", RUN_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_INDEX), encoding="utf-8")
    return GraphIndex(path)


def _retriever(kind: str, root: Path, graph: GraphIndex | None = None):  # noqa: ANN202
    if kind == "fake":
        return FakeRetriever(root=root, ids=IdFactory(RUN_ID), run_id=RUN_ID, graph=graph)
    return HybridRetriever(
        root=root,
        ids=IdFactory(RUN_ID),
        run_id=RUN_ID,
        embedder=FakeEmbeddingClient(),
        reranker=FakeRerankerClient(),
        graph=graph,
    )


def _query(budget: int) -> Query:
    return Query(
        run_id=RUN_ID, target=Location(file="parser.c"), keywords=[], budget_tokens=budget
    )


_TARGET_TOKENS = estimate_tokens(_PARSER)
_BOTH = pytest.mark.parametrize("kind", ["fake", "hybrid"])


def _section(capsule: ContextCapsule, kind: str):  # noqa: ANN202
    return next((s for s in capsule.sections if s.kind == kind), None)


@pytest.mark.parametrize(
    "cuts",
    [
        CapsuleCuts(dropped_sections=["static_signal"]),
        CapsuleCuts(graph_stopped=True),
        CapsuleCuts(graph_skipped=1),
        CapsuleCuts(related_skipped=1),
    ],
)
def test_any_one_marker_alone_makes_a_cut(cuts: CapsuleCuts) -> None:
    """`any_cut`이 필드 하나를 빠뜨리면 그 표지만 선 캡슐에 `capsule_cut` 이벤트가 안 난다."""
    assert cuts.any_cut is True


# --- 안 잘린 캡슐은 「빈 값」이다 — None이 아니다 -----------------------------------------


@_BOTH
def test_a_capsule_nothing_cut_records_the_explicit_empty_value_not_none(
    kind: str, source: Path, tmp_path: Path
) -> None:
    """`None`은 「기록 안 했다」다. 새로 조립해 아무것도 안 잘렸으면 **빈 값을 명시한다** —
    둘이 같으면 「안 잘렸다」와 「안 쟀다」가 한 모양이다(CLAUDE.md §0)."""
    capsule = _retriever(kind, source, _graph(tmp_path)).search(
        _query(100_000), CapsuleLevel.LIGHT
    )

    assert capsule.cuts is not None
    assert capsule.cuts == CapsuleCuts()
    assert capsule.cuts.any_cut is False


def test_an_older_capsule_without_the_field_loads_as_not_recorded() -> None:
    """이 필드가 생기기 전에 저장된 캡슐(키 없음)은 `None`으로 읽는다 — 「안 잘렸다」가 아니다."""
    old = {
        "capsule_id": "cap_0001",
        "run_id": RUN_ID,
        "target": {"file": "parser.c"},
        "level": "light",
        "budget_tokens": 100,
        "sections": [],
        "cache_key": "sha256:" + "0" * 64,
    }

    assert ContextCapsule.model_validate(old).cuts is None


# --- 1. `_sections_for`의 break — 섹션이 통째로 빠진다 ----------------------------------


@_BOTH
def test_a_section_dropped_by_the_budget_break_is_named(
    kind: str, source: Path
) -> None:
    """그래프가 없으면 LIGHT는 `target_function` → `static_signal`이고, 예산이
    `target_function`만큼이면 `static_signal`이 `cost > 0 and remaining <= 0`의 break로 빠진다."""
    capsule = _retriever(kind, source).search(_query(_TARGET_TOKENS), CapsuleLevel.LIGHT)

    assert [s.kind for s in capsule.sections] == ["target_function"]
    assert capsule.cuts is not None
    assert capsule.cuts.dropped_sections == ["static_signal"]
    assert capsule.cuts.graph_stopped is False
    assert capsule.cuts.any_cut is True


# --- 2. graph_section의 stop=True --------------------------------------------------------


@_BOTH
def test_a_graph_section_that_cannot_fit_one_entry_is_a_stop_and_names_what_followed(
    kind: str, source: Path, tmp_path: Path
) -> None:
    """실을 것(callers 둘)은 있는데 예산이 0이다. 섹션을 안 만들고 구성을 멈춘다 — 멈춘 자리의
    kind와 그 뒤 전부가 `dropped_sections`, 못 담은 항목 수가 `graph_skipped`다."""
    capsule = _retriever(kind, source, _graph(tmp_path)).search(
        _query(_TARGET_TOKENS), CapsuleLevel.LIGHT
    )

    assert [s.kind for s in capsule.sections] == ["target_function"]
    assert capsule.cuts is not None
    assert capsule.cuts.graph_stopped is True
    assert capsule.cuts.dropped_sections == ["callers", "callees", "static_signal"]
    assert capsule.cuts.graph_skipped == 2


# --- 그래프 섹션이 일부만 담긴다 -----------------------------------------------------------


@_BOTH
def test_a_graph_section_that_only_partly_fits_counts_the_entries_it_left_out(
    kind: str, source: Path, tmp_path: Path
) -> None:
    """섹션은 만들어졌다(`items=1`)는 점이 stop과 다르다 — 뒤 섹션도 안 빠진다. `items=k`가 「전부
    k건」인지 「여럿 중 k건」인지는 캡슐만 봐서는 몰랐다(`fit_names` docstring)."""
    probe = _retriever(kind, source, _graph(tmp_path)).search(
        _query(100_000), CapsuleLevel.LIGHT
    )
    callers = _section(probe, "callers")
    assert callers is not None and callers.items == 2
    first, second = (estimate_tokens(line) for line in callers.body)
    assert second > _AUX_SECTION_TOKENS, "픽스처 전제 — 둘째 줄이 `static_signal`보다 크다"
    # `callers`는 첫째만 들고, 그 뒤 `callees`(0건)·`static_signal`(64)은 들 만큼만 남긴다.
    budget = _TARGET_TOKENS + first + _AUX_SECTION_TOKENS

    capsule = _retriever(kind, source, _graph(tmp_path / "again")).search(
        _query(budget), CapsuleLevel.LIGHT
    )

    assert _section(capsule, "callers").items == 1
    assert _section(capsule, "static_signal") is not None, "픽스처 전제 — 뒤 섹션은 들어갔다"
    assert capsule.cuts is not None
    assert capsule.cuts.graph_skipped == 1
    assert capsule.cuts.graph_stopped is False
    assert capsule.cuts.dropped_sections == []
    assert capsule.cuts.any_cut is True, "일부만 담긴 것도 절단이다 — 다른 표지가 없어도"


# --- 3. `_fit_related` — 본문이 갈린다(그래프 부분 절단과 함께) ---


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "parser.c").write_text(_PARSER, encoding="utf-8")
    for i in range(4):
        # 대상이 C 계열이면 후보도 C 계열 조각만 쓴다(#336) — `.py`로 두면 선별이 0건이다.
        (root / f"mod{i}.c").write_text(
            f"int helper{i}(int token) {{ /* auth token check {i} */ return token; }}\n" * 30,
            encoding="utf-8",
        )
    return root


def _related_query(budget: int) -> Query:
    return Query(
        run_id=RUN_ID, target=Location(file="parser.c"), keywords=["token"], budget_tokens=budget
    )


def test_related_chunks_that_do_not_fit_are_counted(corpus: Path) -> None:
    probe = _retriever("hybrid", corpus).search(_related_query(100_000), CapsuleLevel.EXPANDED)
    related = _section(probe, "related")
    assert related is not None and related.items is not None and related.items >= 2
    total = related.items
    budget = _TARGET_TOKENS + estimate_tokens(related.body[0])

    capsule = _retriever("hybrid", corpus).search(_related_query(budget), CapsuleLevel.EXPANDED)

    assert _section(capsule, "related").items == 1
    assert capsule.cuts is not None
    assert capsule.cuts.related_skipped == total - 1


def test_related_that_fits_no_chunk_is_counted_and_the_section_is_absent(corpus: Path) -> None:
    """한 조각도 안 들면 `related` 섹션이 없다 — 섹션 부재만으로는 「검색이 0건」과 안 갈린다."""
    probe = _retriever("hybrid", corpus).search(_related_query(100_000), CapsuleLevel.EXPANDED)
    total = _section(probe, "related").items

    capsule = _retriever("hybrid", corpus).search(
        _related_query(_TARGET_TOKENS), CapsuleLevel.EXPANDED
    )

    assert _section(capsule, "related") is None
    assert capsule.cuts is not None
    assert capsule.cuts.related_skipped == total


def test_a_search_that_selected_nothing_skipped_nothing(source: Path) -> None:
    """선별 0건은 「조각이 안 들었다」가 아니다 — `related_skipped`는 0이다."""
    capsule = _retriever("hybrid", source).search(
        _related_query(100_000), CapsuleLevel.EXPANDED
    )

    assert _section(capsule, "related") is None
    assert capsule.cuts is not None
    assert capsule.cuts.related_skipped == 0


# --- 함정: `min(cost, remaining)`는 내용을 안 바꾼다 — 세지 않는다 ---------------------------


@_BOTH
def test_the_min_cost_remaining_path_is_not_a_cut(kind: str, source: Path) -> None:
    """`static_signal`은 본문이 없는 64토큰 자리표시자라 남은 예산이 64 미만이어도 `min()`으로 깎여
    **들어간다.** 내용(`body`)은 그대로이므로 절단이 아니다 — 세면 과다 보고다."""
    capsule = _retriever(kind, source).search(_query(_TARGET_TOKENS + 10), CapsuleLevel.LIGHT)

    static = _section(capsule, "static_signal")
    assert static is not None and static.tokens == 10, "픽스처 전제 — min() 경로를 탔다"
    assert capsule.cuts == CapsuleCuts()


# --- 프롬프트 바이트는 그대로다 ------------------------------------------------------------


def _rendered(capsule: ContextCapsule, role: Role) -> str:
    """노드(`scout`·`auditor`)가 부르는 것과 같은 함수들로 프롬프트 body를 만든다."""
    template = load_prompt(role, "v1")
    fields = {
        "target": describe_target(capsule.target),
        "capsule_sections": describe_sections(capsule),
        "source": _PARSER,
    }
    if role is Role.AUDITOR:
        fields["candidate"] = "cand_0001 · 추정 CWE: CWE-787 · 트리아지 신호: (없음)"
    return template.render(
        facts=build_facts(
            run_id=RUN_ID,
            ids={"candidate_id": "cand_0001"},
            artifact_id="art_0001",
            target=capsule.target,
            capsule=capsule,
            source=_PARSER,
        ),
        **fields,
    ).body


@pytest.mark.parametrize("role", [Role.SCOUT, Role.AUDITOR])
def test_the_cut_marker_is_not_in_the_rendered_prompt(role: Role, source: Path) -> None:
    """표지는 run 기록용이다. 프롬프트에 실리면 모델 입력이 바뀌어 비교가 오염된다 — 같은 캡슐에서
    `cuts`만 떼어 낸 것과 **글자 그대로 같아야** 하고, 필드 이름도 안 나온다."""
    capsule = _retriever("fake", source).search(_query(_TARGET_TOKENS), CapsuleLevel.LIGHT)
    assert capsule.cuts is not None and capsule.cuts.any_cut, "픽스처 전제 — 잘린 캡슐"
    bare = capsule.transition(cuts=None)
    assert bare.cuts is None

    body = _rendered(capsule, role)

    assert body == _rendered(bare, role)
    for name in ("cuts", "dropped_sections", "graph_stopped", "graph_skipped", "related_skipped"):
        assert name not in body


# --- 영속·전이 -----------------------------------------------------------------------------


def test_the_marker_survives_the_store_and_transition(tmp_path: Path, source: Path) -> None:
    capsule = _retriever("fake", source).search(_query(_TARGET_TOKENS), CapsuleLevel.LIGHT)
    deps = build_deps(
        load_config("dev_fake"),
        run_id=RUN_ID,
        source_root=source,
        artifacts_root=tmp_path / "a",
    )

    deps.store.put(capsule)

    assert deps.store.get("capsule", capsule.capsule_id) == capsule
    assert capsule.transition(budget_tokens=capsule.budget_tokens + 1).cuts == capsule.cuts


def test_a_state_db_of_the_version_before_the_field_is_refused_naming_the_change(
    tmp_path: Path,
) -> None:
    """`ContextCapsule.cuts`는 `extra="forbid"` 때문에 판을 올린다 — 판 2 파일(키 없는 캡슐을 든)은
    열기를 거부당하고 오류가 그 변경(#663)을 댄다."""
    path = tmp_path / "state.db"
    SqliteStateStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 2")
    conn.close()

    with pytest.raises(StateDbVersionError, match="#663"):
        SqliteStateStore(path)


# --- run 기록: trace 이벤트 -------------------------------------------------------------------


def _context(tmp_path: Path, source: Path) -> RunContext:
    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID)
    deps = build_deps(
        config, run_id=RUN_ID, source_root=source, artifacts_root=tmp_path / "artifacts"
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
        read_source=lambda target: read_symbol_source(source, target),
        monotonic_ms=lambda: 0,
    )


def _squeeze(ctx: RunContext, tokens: int) -> None:
    budgets = {"light": tokens, "expanded": tokens, "focused": tokens}
    thresholds = ctx.config.thresholds.transition(capsule_budget_tokens=budgets)
    ctx.config = ctx.config.transition(thresholds=thresholds)


def _cut_events(ctx: RunContext) -> list:
    return [e for e in ctx.trace.events if e.kind is EventKind.CAPSULE_CUT]


def test_a_cut_capsule_leaves_one_trace_event_that_carries_the_marker(
    tmp_path: Path, source: Path
) -> None:
    ctx = _context(tmp_path, source)
    _squeeze(ctx, _TARGET_TOKENS)

    capsule = retrieval.run(ctx, target=Location(file="parser.c"), level=CapsuleLevel.LIGHT)

    assert capsule.cuts is not None and capsule.cuts.any_cut, "픽스처 전제 — 잘렸다"
    (event,) = _cut_events(ctx)
    assert event.subject_kind == "capsule"
    assert event.subject_id == capsule.capsule_id
    assert event.stage == int(Stage.RETRIEVAL)
    assert event.data == {
        "level": "light",
        "dropped_sections": ",".join(capsule.cuts.dropped_sections),
        "graph_stopped": capsule.cuts.graph_stopped,
        "graph_skipped": capsule.cuts.graph_skipped,
        "related_skipped": capsule.cuts.related_skipped,
    }


def test_the_event_carries_every_field_of_the_marker_with_nonzero_values(
    tmp_path: Path, source: Path
) -> None:
    """위 실행 경로는 `graph_skipped`·`related_skipped`가 0인 캡슐이다 — 둘 중 하나를 이벤트에서
    빼도 안 빨개진다. 필드마다 **다른 0 아닌 값**을 줘서 하나라도 빠지거나 바뀌면 죽게 한다."""
    ctx = _context(tmp_path, source)
    capsule = _retriever("fake", source).search(_query(100_000), CapsuleLevel.LIGHT).transition(
        cuts={
            "dropped_sections": ["callees", "static_signal"],
            "graph_stopped": True,
            "graph_skipped": 3,
            "related_skipped": 2,
        }
    )

    retrieval._record_cuts(ctx, capsule)

    (event,) = _cut_events(ctx)
    assert event.data == {
        "level": "light",
        "dropped_sections": "callees,static_signal",
        "graph_stopped": True,
        "graph_skipped": 3,
        "related_skipped": 2,
    }
    assert event.subject_id == capsule.capsule_id


def test_a_capsule_nothing_cut_leaves_no_event(tmp_path: Path, source: Path) -> None:
    """이벤트 부재 = 안 잘렸다(`retrieval_truncated`·`scout_slice_trimmed`와 같은 읽기)."""
    ctx = _context(tmp_path, source)
    _squeeze(ctx, 100_000)

    capsule = retrieval.run(ctx, target=Location(file="parser.c"), level=CapsuleLevel.LIGHT)

    assert capsule.cuts == CapsuleCuts()
    assert _cut_events(ctx) == []
