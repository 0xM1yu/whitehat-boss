"""어댑터 계약 테스트.

두 StateStore 구현은 **같은 테스트를 파라미터로 돌린다.** 계약이 갈라지면 프로필을
바꿨을 때만 나타나는 버그가 생기는데, 그때는 이미 실행 비용을 쓴 뒤다.

**점수·토큰 수 같은 측정값은 단언하지 않는다.** 그 값들은 이 프로젝트가 재려는
대상이지 테스트가 고정할 상수가 아니다. 여기서 고정하는 것은 결정론, 순서 관계,
그리고 문서가 요구한 계약뿐이다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from src.adapters.factory import WIRINGS, build_deps
from src.adapters.fake_model import FakeContext, FakeModelClient, FakeModelError, risky_calls_in
from src.adapters.fake_retriever import FakeRetriever, read_symbol_source
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.routing_model import RoutingModelClient, UnknownRoleError
from src.adapters.sqlite_store import SqliteStateStore
from src.config import AdapterSelection, load_config
from src.ids import IdFactory, new_run_id, sha256_text
from src.interfaces.model_client import Prompt
from src.interfaces.retriever import Query
from src.interfaces.tool_runner import SandboxSpec, ToolArgs
from src.paths import PathEscape
from src.schemas import (
    Candidate,
    CapsuleLevel,
    Decision,
    Evidence,
    EvidenceKind,
    Finding,
    FindingState,
    Location,
    MissingEvidence,
    Role,
    Verdict,
)

RUN_ID = "run_20260901_0001"
SANDBOX = SandboxSpec(image="fake:latest", cpu_limit=1.0, memory_mb=512, timeout_s=60)
TARGET = Location(file="parser.c", symbol="parse_header")

VULNERABLE_C = """\
#include <string.h>

void parse_header(const char *src, int len) {
    char buf[64];
    memcpy(buf, src, len);
}
"""

BENIGN_C = """\
#include <string.h>

void safe_copy(const char *src) {
    char buf[64];
    strncpy(buf, src, sizeof(buf) - 1);
}
"""


@pytest.fixture
def target_root(tmp_path: Path) -> Path:
    root = tmp_path / "target"
    root.mkdir()
    (root / "parser.c").write_text(VULNERABLE_C, encoding="utf-8")
    (root / "util.c").write_text(BENIGN_C, encoding="utf-8")
    return root


def _query(budget_tokens: int = 4000) -> Query:
    return Query(run_id=RUN_ID, target=TARGET, budget_tokens=budget_tokens)


def _args(workdir: str = ".", *argv: str) -> ToolArgs:
    """`workdir`는 샌드박스에 마운트된 루트 기준 상대경로다."""
    return ToolArgs(argv=list(argv) or ["run"], workdir=workdir)


def _runner(root: Path) -> FakeToolRunner:
    return FakeToolRunner(ids=IdFactory(RUN_ID), run_id=RUN_ID, root=root)


# --- IdFactory --------------------------------------------------------------


def test_ids_are_deterministic_and_sequential() -> None:
    a, b = IdFactory(RUN_ID), IdFactory(RUN_ID)
    assert [a.next("candidate") for _ in range(3)] == [b.next("candidate") for _ in range(3)]
    assert a.issued() == {"candidate": 3}


def test_unknown_object_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="알 수 없는 객체 종류"):
        IdFactory(RUN_ID).next("ghost")


def test_run_id_comes_from_injected_time() -> None:
    """시각을 주입받아야 재현 실행이 같은 run_id를 만든다."""
    assert new_run_id(datetime(2026, 9, 1, tzinfo=UTC)) == RUN_ID


# --- FakeModelClient --------------------------------------------------------


def _prompt(context: FakeContext, *, prefix: str = "SYSTEM") -> Prompt:
    return Prompt(
        prefix=prefix,
        body=f"분석하라.\n\n```json\n{context.model_dump_json()}\n```",
        prompt_version="v1",
    )


def _context(source: str, **overrides: object) -> FakeContext:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "ids": {
            "candidate_id": "cand_0001",
            "finding_id": "find_0001",
            "decision_id": "dec_0001",
            "missing_id": "mev_0001",
        },
        "artifact_id": "art_0001",
        "target": TARGET,
        "capsule_sections": [{"kind": "target_function", "tokens": 12}],
        "source": source,
        "evidence": [],
        "callers": ["recv_request"],
    }
    return FakeContext.model_validate(base | overrides)


def test_same_prompt_gives_same_output() -> None:
    prompt = _prompt(_context(VULNERABLE_C))
    first = FakeModelClient().complete(role=Role.SCOUT, prompt=prompt, schema=Candidate)
    second = FakeModelClient().complete(role=Role.SCOUT, prompt=prompt, schema=Candidate)
    assert first.value == second.value


def test_score_rises_with_evidence_not_with_noise() -> None:
    """점수가 입력 신호에서 나온다. 구체 값이 아니라 순서 관계만 고정한다."""
    client = FakeModelClient()
    bare = client.complete(
        role=Role.SCOUT, prompt=_prompt(_context(BENIGN_C)), schema=Candidate
    ).value
    risky = client.complete(
        role=Role.SCOUT, prompt=_prompt(_context(VULNERABLE_C)), schema=Candidate
    ).value
    backed = client.complete(
        role=Role.SCOUT,
        prompt=_prompt(
            _context(
                VULNERABLE_C,
                evidence=[{"evidence_id": "ev_0001", "kind": EvidenceKind.STATIC, "ok": True}],
            )
        ),
        schema=Candidate,
    ).value
    assert bare.score < risky.score < backed.score
    assert bare.suspected_cwe == []
    assert risky.suspected_cwe


def test_prefix_reuse_shows_up_as_cached_tokens() -> None:
    """cache hit ratio(evaluation.md §5.2)의 분자가 실제로 생기는지 확인한다."""
    client = FakeModelClient()
    prompt = _prompt(_context(VULNERABLE_C))
    first = client.complete(role=Role.SCOUT, prompt=prompt, schema=Candidate)
    second = client.complete(role=Role.SCOUT, prompt=prompt, schema=Candidate)
    assert first.usage.cached_tokens == 0
    assert 0 < second.usage.cached_tokens <= second.usage.input_tokens


def test_auditor_omits_repro_spec_without_build_evidence() -> None:
    """빌드 방법을 모르면 재현 명세를 지어내지 않는다 → Gate가 E3으로 올리지 못한다."""
    client = FakeModelClient()
    without = client.complete(
        role=Role.AUDITOR, prompt=_prompt(_context(VULNERABLE_C)), schema=Finding
    ).value
    with_build = client.complete(
        role=Role.AUDITOR,
        prompt=_prompt(
            _context(
                VULNERABLE_C,
                evidence=[{"evidence_id": "ev_0002", "kind": EvidenceKind.BUILD, "ok": True}],
            )
        ),
        schema=Finding,
    ).value
    assert without.repro_spec is None
    assert with_build.repro_spec is not None
    assert without.state is FindingState.HYPOTHESIZED


def test_auditor_requests_missing_evidence_without_static_signal() -> None:
    finding = FakeModelClient().complete(
        role=Role.AUDITOR,
        prompt=_prompt(_context(VULNERABLE_C, capsule_sections=[{"kind": "target_function"}])),
        schema=Finding,
    ).value
    assert finding.missing_evidence == ["mev_0001"]


def test_critic_produces_an_ungrounded_rebuttal_without_graph_evidence() -> None:
    """agent-contracts.md §2.3의 정규화 경로를 태울 입력이 실제로 만들어져야 한다.

    스키마는 근거 없는 반박을 막지 않는다. 정규화와 지표 기록은 오케스트레이터 책임이고
    (PR 1 미해결 항목), 그 코드를 태울 입력이 여기서 나온다.
    """
    client = FakeModelClient()
    ungrounded = client.complete(
        role=Role.CRITIC, prompt=_prompt(_context(VULNERABLE_C)), schema=Decision
    ).value
    grounded = client.complete(
        role=Role.CRITIC,
        prompt=_prompt(
            _context(
                VULNERABLE_C,
                evidence=[{"evidence_id": "ev_0003", "kind": EvidenceKind.GRAPH, "ok": True}],
            )
        ),
        schema=Decision,
    ).value
    assert ungrounded.verdict is Verdict.REBUT
    assert not ungrounded.is_grounded
    assert grounded.verdict is Verdict.UPHOLD
    assert grounded.is_grounded


def test_unknown_schema_fails_loudly() -> None:
    with pytest.raises(FakeModelError, match="모르는 출력 스키마"):
        FakeModelClient().complete(
            role=Role.SCOUT, prompt=_prompt(_context(VULNERABLE_C)), schema=Evidence
        )


def test_missing_fact_block_fails_loudly() -> None:
    prompt = Prompt(prefix="SYSTEM", body="사실 블록 없음", prompt_version="v1")
    with pytest.raises(FakeModelError, match="사실 블록"):
        FakeModelClient().complete(role=Role.SCOUT, prompt=prompt, schema=Candidate)


def test_risky_call_detection_ignores_substrings() -> None:
    assert risky_calls_in("memcpy(a, b, n);") == ["memcpy"]
    assert risky_calls_in("my_memcpyx(a);") == []


# --- RoutingModelClient -----------------------------------------------------


def test_routing_sends_each_role_to_its_own_client() -> None:
    scout, critic = FakeModelClient(model_name="s"), FakeModelClient(model_name="c")
    router = RoutingModelClient({"scout": scout, "critic": critic})
    assert router.client_for(Role.SCOUT) is scout
    assert router.client_for("critic") is critic
    with pytest.raises(UnknownRoleError, match="auditor"):
        router.client_for(Role.AUDITOR)


# --- FakeRetriever ----------------------------------------------------------


def test_capsule_stays_within_budget(target_root: Path) -> None:
    """캡슐 예산 초과는 스키마가 막는다 — 어댑터가 먼저 잘라야 한다."""
    retriever = FakeRetriever(root=target_root, ids=IdFactory(RUN_ID), run_id=RUN_ID)
    capsule = retriever.search(_query(budget_tokens=8), CapsuleLevel.LIGHT)
    assert capsule.used_tokens <= capsule.budget_tokens


def test_capsule_cache_reports_hit_and_miss(target_root: Path) -> None:
    events: list[tuple[str, bool]] = []
    retriever = FakeRetriever(
        root=target_root,
        ids=IdFactory(RUN_ID),
        run_id=RUN_ID,
        on_cache=lambda key, hit: events.append((key, hit)),
    )
    first = retriever.search(_query(), CapsuleLevel.LIGHT)
    second = retriever.search(_query(), CapsuleLevel.LIGHT)
    assert first is second
    assert [hit for _, hit in events] == [False, True]


def test_capsule_key_separates_levels(target_root: Path) -> None:
    """같은 심볼이라도 Scout용과 Auditor용 캡슐은 다른 물건이다."""
    retriever = FakeRetriever(root=target_root, ids=IdFactory(RUN_ID), run_id=RUN_ID)
    assert retriever.cache_key_for(_query(), CapsuleLevel.LIGHT) != retriever.cache_key_for(
        _query(), CapsuleLevel.EXPANDED
    )


def test_expand_returns_new_capsule_with_incremented_count(target_root: Path) -> None:
    """`transition()`을 타야 예산 검증기가 실제로 돈다(`model_copy`는 우회한다)."""
    retriever = FakeRetriever(root=target_root, ids=IdFactory(RUN_ID), run_id=RUN_ID)
    capsule = retriever.search(_query(), CapsuleLevel.LIGHT)
    need = MissingEvidence(
        missing_id="mev_0001",
        run_id=RUN_ID,
        finding_id="find_0001",
        need="호출부 길이 상한",
        how_to_obtain="index.callers 확장",
        blocking=True,
    )
    expanded = retriever.expand(capsule, need)
    assert expanded.expansion_count == capsule.expansion_count + 1
    assert expanded.used_tokens <= expanded.budget_tokens
    assert capsule.expansion_count == 0  # 원본은 그대로 (frozen)


def test_read_symbol_source_slices_by_line(target_root: Path) -> None:
    whole = read_symbol_source(target_root, Location(file="parser.c"))
    sliced = read_symbol_source(target_root, Location(file="parser.c", line_start=3, line_end=6))
    assert "memcpy" in whole
    assert sliced
    assert len(sliced) < len(whole)


@pytest.mark.parametrize("escape", ["../outside.c", "/etc/passwd"])
def test_source_outside_root_is_refused(target_root: Path, escape: str) -> None:
    """`Location.file`은 모델·인덱스에서 오는 값이라 루트 밖을 가리킬 수 있다.

    pathlib은 오른쪽이 절대경로면 왼쪽을 통째로 버리므로 검증 없이는 조용히 새어 나간다.
    """
    (target_root.parent / "outside.c").write_text(VULNERABLE_C, encoding="utf-8")
    with pytest.raises(PathEscape):
        read_symbol_source(target_root, Location(file=escape))


# --- FakeToolRunner ---------------------------------------------------------


def test_static_tool_finds_signal_only_where_it_exists(target_root: Path) -> None:
    runner = _runner(target_root)
    hit = runner.run("semgrep", _args("parser.c"), SANDBOX)
    miss = runner.run("semgrep", _args("util.c"), SANDBOX)
    assert hit.ok and miss.ok  # 신호 부재는 실패가 아니다
    assert hit.exit_code != miss.exit_code
    assert hit.kind is EvidenceKind.STATIC


def test_tool_failure_becomes_evidence_not_exception(target_root: Path) -> None:
    """engineering-guide.md §8.3: 도구 실패도 Evidence로 기록한다."""
    evidence = _runner(target_root).run("build", _args("없는디렉터리"), SANDBOX)
    assert not evidence.ok
    assert evidence.error


def test_tool_outside_policy_is_recorded_as_failure(target_root: Path) -> None:
    evidence = _runner(target_root).run("curl", _args(), SANDBOX)
    assert not evidence.ok
    assert "tool_policy" in (evidence.error or "")


def test_network_sandbox_is_refused(target_root: Path) -> None:
    """disclosure-policy.md §5: 외부 시스템 실행 금지."""
    evidence = _runner(target_root).run("semgrep", _args(), SANDBOX.transition(network=True))
    assert not evidence.ok
    assert "network" in (evidence.error or "")


@pytest.mark.parametrize("workdir", ["../secrets", "/etc"])
def test_workdir_outside_mount_is_refused(target_root: Path, workdir: str) -> None:
    """모델·인덱스에서 온 경로가 마운트 밖을 가리키면 실행하지 않는다(src/paths.py)."""
    evidence = _runner(target_root).run("semgrep", _args(workdir), SANDBOX)
    assert not evidence.ok
    assert "루트" in (evidence.error or "")


def test_tool_results_are_deterministic(target_root: Path) -> None:
    first = _runner(target_root).run("semgrep", _args(), SANDBOX)
    second = _runner(target_root).run("semgrep", _args(), SANDBOX)
    assert first == second


# --- StateStore (두 구현 공통 계약) -----------------------------------------


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path):  # noqa: ANN201
    if request.param == "memory":
        return InMemoryStateStore()
    return SqliteStateStore(tmp_path / "state.db")


def _candidate(candidate_id: str, run_id: str = RUN_ID) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        run_id=run_id,
        artifact_id="art_0001",
        location=TARGET,
        score=0.7,
        state=FindingState.CANDIDATE,
    )


def test_put_then_get_round_trips(store) -> None:  # noqa: ANN001
    obj = _candidate("cand_0001")
    assert store.put(obj) == "cand_0001"
    assert store.get("candidate", "cand_0001") == obj


def test_get_missing_returns_none(store) -> None:  # noqa: ANN001
    assert store.get("candidate", "cand_9999") is None


def test_put_is_idempotent_on_same_id(store) -> None:  # noqa: ANN001
    store.put(_candidate("cand_0001"))
    updated = _candidate("cand_0001").transition(state=FindingState.TRIAGED)
    store.put(updated)
    assert store.get("candidate", "cand_0001") == updated
    assert len(store.query("candidate")) == 1


def test_query_filters_by_run_id(store) -> None:  # noqa: ANN001
    store.put(_candidate("cand_0001"))
    store.put(_candidate("cand_0002", run_id="run_20260901_0002"))
    assert len(store.query("candidate", run_id=RUN_ID)) == 1


def test_query_returns_ids_in_ascending_order(store) -> None:  # noqa: ANN001
    """두 구현의 정렬 계약이 같아야 한다.

    노드가 결과를 순회하는 순간 순서가 곧 실행 순서다. memory와 sqlite가 다른 순서를
    주면 프로필을 바꿨을 때만 재현이 깨지고, 원인을 찾는 데 실행 비용을 다시 쓴다.
    """
    for candidate_id in ("cand_0003", "cand_0001", "cand_0002"):
        store.put(_candidate(candidate_id))
    ids = [obj.candidate_id for obj in store.query("candidate")]
    assert ids == sorted(ids)


def test_query_filters_by_state(store) -> None:  # noqa: ANN001
    store.put(_candidate("cand_0001"))
    store.put(_candidate("cand_0002").transition(state=FindingState.TRIAGED))
    assert len(store.query("candidate", state=FindingState.TRIAGED)) == 1


def test_unknown_filter_field_fails_loudly(store) -> None:  # noqa: ANN001
    """오타 필터가 "결과 0건"으로 보이면 파이프라인이 멈춘 것과 구별되지 않는다."""
    store.put(_candidate("cand_0001"))
    with pytest.raises(AttributeError, match="ghost"):
        store.query("candidate", ghost=1)


def test_unsupported_object_is_rejected(store) -> None:  # noqa: ANN001
    with pytest.raises(TypeError, match="저장할 수 없는"):
        store.put(SANDBOX)  # type: ignore[arg-type]


def test_sqlite_survives_reopen(tmp_path: Path) -> None:
    """checkpoint/resume이 성립하려면 프로세스 밖에서 살아남아야 한다."""
    path = tmp_path / "state.db"
    first = SqliteStateStore(path)
    first.put(_candidate("cand_0001"))
    first.close()
    assert SqliteStateStore(path).get("candidate", "cand_0001") is not None


def test_sqlite_payload_is_schema_json(tmp_path: Path) -> None:
    """저장 형태가 스키마 JSON이어야 읽을 때 검증이 돈다."""
    store = SqliteStateStore(tmp_path / "state.db")
    store.put(_candidate("cand_0001"))
    row = store._conn.execute("SELECT payload FROM objects").fetchone()  # noqa: SLF001
    assert json.loads(row[0])["candidate_id"] == "cand_0001"


# --- factory ----------------------------------------------------------------


def test_dev_fake_wires_every_role_to_a_fake(target_root: Path, tmp_path: Path) -> None:
    deps = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=target_root, artifacts_root=tmp_path
    )
    assert isinstance(deps.store, InMemoryStateStore)
    for role in ("scout", "auditor", "critic"):
        assert isinstance(deps.models.client_for(role), FakeModelClient)


def test_counter_for_role_is_none_for_fake_models(target_root: Path, tmp_path: Path) -> None:
    """fake 모델엔 토크나이저 `source`가 없어 역할 카운터가 `None`이다 (D21 (b) 3b).

    두 가지를 함께 본다: **배선이 연결됐다**(`Deps`에 `counter_for_role`이 있고 factory가
    채운다)와 **dev_fake는 무영향이다**(`None` → 러너의 `using_counter`가 no-op → 기본
    카운터 `len//4` 유지). 실 토크나이저로 세는 것은 `test_tokenizer_adapter.py`가 본다.
    """
    deps = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=target_root, artifacts_root=tmp_path
    )
    assert deps.counter_for_role("scout") is None
    assert deps.counter_for_role("auditor") is None


def test_single_gpu_12g_mixes_real_and_fake(target_root: Path, tmp_path: Path) -> None:
    """한 프로필 안에서 scout만 실서빙된다 — RoutingModelClient가 있는 이유다."""
    deps = build_deps(
        load_config("single_gpu_12g"),
        run_id=RUN_ID,
        source_root=target_root,
        artifacts_root=tmp_path,
    )
    assert not isinstance(deps.models.client_for("scout"), FakeModelClient)
    assert isinstance(deps.models.client_for("auditor"), FakeModelClient)
    assert isinstance(deps.store, SqliteStateStore)


@pytest.mark.parametrize("field", sorted(WIRINGS))
def test_every_adapter_choice_is_wired(field: str) -> None:
    """`AdapterSelection`의 `Literal` 값 집합 == 팩토리 매핑의 키 집합.

    `adapters.retriever`가 설정에 선언돼 있는데 factory가 읽지 않아 `FakeRetriever`가
    하드코딩된 채로 머물렀던 적이 있다. 설정이 조용히 무시되면 프로필이 거짓말을 하고,
    그 상태로 뽑은 벤치마크는 무효다.

    누가 `Literal`에 값을 추가하고 배선을 잊으면 여기서 깨진다 — 주석으로 부탁하는
    대신 CI가 막는다.
    """
    declared = set(get_args(AdapterSelection.model_fields[field].annotation))
    assert declared == set(WIRINGS[field])


def test_adapter_fields_and_wiring_tables_cover_each_other() -> None:
    """필드 자체가 빠지는 것도 막는다. 매핑을 안 만들면 위 테스트가 아예 안 돈다."""
    assert set(WIRINGS) == set(AdapterSelection.model_fields)


def test_deps_share_one_id_factory(target_root: Path, tmp_path: Path) -> None:
    """ID가 갈라지면 두 실행의 trace를 대조할 수 없다."""
    deps = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=target_root, artifacts_root=tmp_path
    )
    deps.tools.run("build", _args(), SANDBOX)
    assert deps.ids.issued()["evidence"] == 1
    assert sha256_text("x").startswith("sha256:")
