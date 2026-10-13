"""어댑터 계약 테스트.

두 StateStore 구현은 **같은 테스트를 파라미터로 돌린다.** 계약이 갈라지면 프로필을
바꿨을 때만 나타나는 버그가 생기는데, 그때는 이미 실행 비용을 쓴 뒤다.

**점수·토큰 수 같은 측정값은 단언하지 않는다.** 그 값들은 이 프로젝트가 재려는
대상이지 테스트가 고정할 상수가 아니다. 여기서 고정하는 것은 결정론, 순서 관계,
그리고 문서가 요구한 계약뿐이다.
"""

import errno
import hashlib
import json
import re
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import model_validator

from src.adapters import fake_tools, sqlite_store
from src.adapters.factory import WIRINGS, build_deps
from src.adapters.fake_model import FakeContext, FakeModelClient, FakeModelError, risky_calls_in
from src.adapters.fake_retriever import FakeRetriever, read_raw_source, read_symbol_source
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.routing_model import RoutingModelClient, UnknownRoleError
from src.adapters.sqlite_store import SqliteStateStore
from src.adapters.store_common import KIND_MAP
from src.config import AdapterSelection, HarnessConfig, load_config
from src.ids import IdFactory, new_run_id, sha256_text
from src.interfaces import tool_runner
from src.interfaces.model_client import ChatTurn, Prompt
from src.interfaces.retriever import Query
from src.interfaces.tool_runner import SandboxSpec, ToolArgs
from src.orchestrator.nodes import preprocess
from src.paths import PathEscape
from src.schemas import (
    ArtifactKind,
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
    RunRecord,
    Verdict,
)
from tests.conftest import plant_link, served_scout_config

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


# --- history (ADR 0012 결정 2 — need_evidence 되묻기의 멀티턴) -----------------


def test_fake_model_ignores_an_empty_history() -> None:
    """`history=()`는 지금까지의 단일 턴 동작 그대로다 — 사실 블록은 이번 턴 본문에서 읽는다."""
    result = FakeModelClient().complete(
        role=Role.SCOUT, prompt=_prompt(_context(VULNERABLE_C)), schema=Candidate, history=()
    )
    assert result.value is not None


def test_fake_model_reads_facts_from_the_first_history_turn() -> None:
    """history가 있으면 사실 블록은 **첫 user 턴**(hop0 본문)에서 읽는다.

    후속 턴 본문(`prompt.body`)에는 사실 블록이 없다 — 실제 후속 질문도 그렇다. 이번 턴
    본문을 읽으려 들면 펜스가 없어 `FakeModelError`가 난다.
    """
    hop0_body = _prompt(_context(VULNERABLE_C)).body
    followup = Prompt(prefix="SYSTEM", body="strdup를 봤다 — 어디서 왔나?", prompt_version="v1")

    result = FakeModelClient().complete(
        role=Role.SCOUT,
        prompt=followup,
        schema=Candidate,
        history=(ChatTurn(speaker="user", content=hop0_body),),
    )

    assert result.value.candidate_id == "cand_0001"
    assert result.raw_text == result.value.model_dump_json()


# --- RoutingModelClient -----------------------------------------------------


def test_routing_sends_each_role_to_its_own_client() -> None:
    scout, critic = FakeModelClient(model_name="s"), FakeModelClient(model_name="c")
    router = RoutingModelClient({"scout": scout, "critic": critic})
    assert router.client_for(Role.SCOUT) is scout
    assert router.client_for("critic") is critic
    with pytest.raises(UnknownRoleError, match="auditor"):
        router.client_for(Role.AUDITOR)


def test_routing_forwards_history_to_the_underlying_client() -> None:
    """routing은 그대로 위임한다 — 자기 검사를 두지 않고, 실제 판정은 하위 클라이언트에
    맡긴다.

    Fake가 실제로 history를 받았는지는 결과로 증명한다 — `followup`(아래) 본문에는
    사실 블록이 없으므로, history가 조용히 버려졌다면 `FakeModelError`로 실패한다.
    """
    router = RoutingModelClient({"scout": FakeModelClient(model_name="s")})
    hop0_body = _prompt(_context(VULNERABLE_C)).body
    followup = Prompt(prefix="SYSTEM", body="후속 질문", prompt_version="v1")

    result = router.complete(
        role=Role.SCOUT,
        prompt=followup,
        schema=Candidate,
        history=(ChatTurn(speaker="user", content=hop0_body),),
    )

    assert result.value.candidate_id == "cand_0001"


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


def test_read_raw_source_keeps_the_carriage_returns_the_text_reader_folds(tmp_path: Path) -> None:
    """`read_symbol_source`는 텍스트 모드로 읽어 `\\r`을 `\\n`으로 접는다 — `\\r\\r\\n`은 줄 둘이
    된다. GUARDS의 줄 정렬 검사가 그 앞의 모양을 보려고 원본 바이트를 따로 읽는다. 없는 파일은
    `None`이다."""
    (tmp_path / "x.c").write_bytes(b"a\r\r\nb\rc\r\n")

    assert read_raw_source(tmp_path, "x.c") == b"a\r\r\nb\rc\r\n"
    assert read_symbol_source(tmp_path, Location(file="x.c")) == "a\n\nb\nc\n"
    assert read_raw_source(tmp_path, "missing.c") is None
    with pytest.raises(PathEscape):
        read_raw_source(tmp_path, "../x.c")


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


def test_static_signal_carries_first_hit_location(target_root: Path) -> None:
    """D23: Fake도 첫 정적 매치의 파일·줄을 Evidence.location에 싣는다.

    등급은 이 값과 무관하다(`p1 = static_ok or location_ok`) — 채우는 목적은 신호를
    심볼 단위로 귀속하는 후속 PR의 입력이다(+ 2026-10-04부터 content-aware 프롬프트의 FACTS·Critic
    줄 — 이슈 #475). 신호가 없는 파일은 위치도 없다.
    """
    hit = _runner(target_root).run("semgrep", _args("parser.c"), SANDBOX)
    assert hit.location is not None
    assert hit.location.file == "parser.c"
    assert hit.location.line_start == 5  # memcpy(buf, src, len) 줄

    miss = _runner(target_root).run("semgrep", _args("util.c"), SANDBOX)
    assert miss.location is None


def test_fake_clang_tidy_carries_no_location(target_root: Path) -> None:
    """#248: Fake도 컨테이너처럼 **semgrep만** location을 싣는다. clang-tidy는 None이라
    두 어댑터의 location 계약이 갈리지 않는다."""
    evidence = _runner(target_root).run("clang-tidy", _args("parser.c"), SANDBOX)

    assert evidence.ok
    assert evidence.location is None


def test_static_location_survives_a_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#248 회귀: 상대 root면 `safe_join`이 resolve한 workdir과 형태가 어긋나
    `relative_to`가 `ValueError`로 죽었다(도구는 예외를 안 던지는 계약). root를 resolve해
    완주한다."""
    root = tmp_path / "target"
    root.mkdir()
    (root / "parser.c").write_text(VULNERABLE_C, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = FakeToolRunner(ids=IdFactory(RUN_ID), run_id=RUN_ID, root=Path("target"))

    evidence = runner.run("semgrep", _args("parser.c"), SANDBOX)

    assert evidence.ok  # 예외로 죽지 않는다
    assert evidence.location is not None
    assert evidence.location.file == "parser.c"


def test_tool_failure_becomes_evidence_not_exception(target_root: Path) -> None:
    """engineering-guide.md §8.3: 도구 실패도 Evidence로 기록한다."""
    evidence = _runner(target_root).run("build", _args("없는디렉터리"), SANDBOX)
    assert not evidence.ok
    assert evidence.error


def test_fake_gdb_uses_crash_evidence_and_requires_a_source(target_root: Path) -> None:
    """dev_fake에서도 gdb가 정책 밖 도구로 거절되지 않고 실행류 계약을 따른다.

    Fake의 `ok=True`는 대상 소스가 있다는 뜻이지(D80) 컨테이너 `judge()`의 stdout
    수명주기 판정이 아니다.
    """
    runner = _runner(target_root)

    debugged = runner.run("gdb", _args("parser.c"), SANDBOX)
    missing = runner.run("gdb", _args("없는디렉터리"), SANDBOX)

    assert debugged.ok
    assert debugged.kind is EvidenceKind.CRASH
    assert debugged.source == "gdb"
    assert debugged.exit_code == 1
    assert not missing.ok
    assert missing.kind is EvidenceKind.CRASH
    assert missing.error == "재현 대상 소스가 없다"


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


# --- 도구 요청에 데이터로 싣는 입력 (#386 ①) -------------------------------------------

# NUL·CRLF·비-UTF-8 — 텍스트로 쓰거나 개행을 번역하면 달라지는 바이트다.
TRICKY_INPUT = bytes.fromhex("000d0a0d0aff") + b"A" * 8


def _input_args(tool: str, data: bytes = TRICKY_INPUT) -> ToolArgs:
    return ToolArgs(argv=[tool, tool_runner.INPUT_PLACEHOLDER], workdir=".", input_bytes=data)


@pytest.fixture
def writer_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, bytes]]:
    """`fake_tools.write_input`에 간 호출을 기록한다 — 실제 쓰기는 그대로 한다."""
    calls: list[tuple[Path, bytes]] = []
    real = fake_tools.write_input

    def recording(directory: Path, data: bytes) -> Path:
        calls.append((Path(directory), data))
        return real(directory, data)

    monkeypatch.setattr(fake_tools, "write_input", recording)
    return calls


def test_write_input_keeps_every_byte_and_names_the_file_after_them(tmp_path: Path) -> None:
    """바이트로 쓴다 — 텍스트 쓰기는 Windows에서 개행을 번역한다(`poc_log` 모듈 docstring)."""
    path = fake_tools.write_input(tmp_path / "scratch", TRICKY_INPUT)

    assert path.parent == tmp_path / "scratch"
    assert path.read_bytes() == TRICKY_INPUT
    assert path.name == f"input-{hashlib.sha256(TRICKY_INPUT).hexdigest()[:16]}.poc"


def test_write_input_name_follows_the_bytes_not_the_length(tmp_path: Path) -> None:
    other = bytes(len(TRICKY_INPUT))  # 같은 길이, 다른 바이트

    first = fake_tools.write_input(tmp_path, TRICKY_INPUT)
    second = fake_tools.write_input(tmp_path, other)
    again = fake_tools.write_input(tmp_path, TRICKY_INPUT)

    assert first != second
    assert again == first
    assert (first.read_bytes(), second.read_bytes()) == (TRICKY_INPUT, other)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([first.name, second.name])


def test_write_input_refuses_and_removes_a_file_that_reads_back_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """번역된 바이트가 「의도한 내용」으로 굳지 않는다 — 틀린 입력 파일은 없는 파일보다 나쁘다."""
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"translated")

    with pytest.raises(OSError, match="되읽은") as excinfo:
        fake_tools.write_input(tmp_path, TRICKY_INPUT)

    assert str(tmp_path) not in str(excinfo.value), "예외 문구에 호스트 경로가 실렸다"
    assert list(tmp_path.iterdir()) == []


def _input_file_name(data: bytes) -> str:
    return f"input-{hashlib.sha256(data).hexdigest()[:16]}{fake_tools.INPUT_SUFFIX}"


@pytest.mark.parametrize("kind", ["symlink", "dangling", "hardlink"])
def test_write_input_does_not_write_through_a_planted_link(tmp_path: Path, kind: str) -> None:
    """🔴 **보안 경계다.** scratch는 RW 마운트이고 대상 코드가 거기서 돈다 — 입력 이름은 PoC의
    sha256에서 나와 **예측되므로** 코드가 그 자리에 링크를 심어 둘 수 있고, `write_bytes`는 따라가
    **마운트 밖 파일을 PoC로 덮는다**(끊어진 링크면 새 파일을 만든다 · 되읽기도 같은 링크를 따라가
    통과한다). 하드링크도 같은 inode에 쓰는 같은 구멍이다(Windows에서도 만들어진다)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside.txt"
    if kind != "dangling":
        outside.write_bytes(b"host file - must stay")
    planted = scratch / _input_file_name(TRICKY_INPUT)
    plant_link(kind, planted, outside)
    assert planted.is_symlink() or planted.exists(), "전제 — 링크가 심어졌다"

    path = fake_tools.write_input(scratch, TRICKY_INPUT)

    assert path == planted
    assert path.read_bytes() == TRICKY_INPUT
    assert not path.is_symlink()
    if kind == "dangling":
        assert not outside.exists(), "끊어진 링크를 따라가 마운트 밖에 파일을 만들었다"
    else:
        assert outside.read_bytes() == b"host file - must stay", "링크를 따라가 바깥 파일을 덮었다"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_write_input_refuses_a_link_that_appears_between_the_unlink_and_the_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """`unlink` 뒤 `xb`의 **둘째 반쪽** — 지운 직후 같은 이름에 링크가 생겨도(경쟁) 따라가지 않고
    거절한다. `xb`를 `wb`로 바꾸는 변이는 위 검사(미리 심은 링크)에는 초록이고 여기서 죽는다. 그사이
    심긴 것은 남의 것이라 지우지 않는다(`poc_log.PocLog.write`의 「임시 경로」와 같은 선례)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host file - must stay")
    plant_link(kind, tmp_path / "probe", outside)  # 못 만드는 환경이면 여기서 건너뛴다
    (tmp_path / "probe").unlink()
    target = scratch / _input_file_name(TRICKY_INPUT)
    real_unlink = Path.unlink

    def racing_unlink(self: Path, missing_ok: bool = False) -> None:
        real_unlink(self, missing_ok=missing_ok)
        if self == target:
            plant_link(kind, self, outside)  # 경쟁 — 지운 직후 같은 이름에 링크

    monkeypatch.setattr(Path, "unlink", racing_unlink)

    with pytest.raises(OSError):
        fake_tools.write_input(scratch, TRICKY_INPUT)

    monkeypatch.undo()
    assert outside.read_bytes() == b"host file - must stay", "경쟁으로 심긴 링크를 따라갔다"
    assert target.is_symlink() or target.exists(), "남이 심은 항목을 지웠다"


def test_the_input_suffix_is_not_an_analyzable_kind() -> None:
    """`--target`이 저장소 루트라 `artifacts/`가 대상 트리 안에 있으면 이전 run의 입력이 다음 run의
    분석 대상이 된다 — `.bin`이면 BINARY로 분류돼 Scout 프롬프트에 실린다(`poc_log` 모듈 docstring의
    「이름」 절, `tests/test_poc_log.py`가 같은 것을 문다)."""
    from src.orchestrator import runner

    kind = preprocess.classify(Path("input-0123456789abcdef" + fake_tools.INPUT_SUFFIX))

    assert fake_tools.INPUT_SUFFIX not in preprocess._KIND_BY_SUFFIX
    assert kind is ArtifactKind.UNKNOWN
    assert kind not in runner._ANALYZABLE


def test_fake_runner_writes_the_input_outside_the_target_tree(
    target_root: Path, writer_calls: list[tuple[Path, bytes]]
) -> None:
    """읽기 전용 마운트 정책(engineering-guide.md §7) — 대상 트리에는 한 바이트도 안 쓴다.
    쓰면 다음 run의 `preprocess`가 그 파일을 대상으로 센다."""
    before = sorted(p.relative_to(target_root).as_posix() for p in target_root.rglob("*"))

    evidence = _runner(target_root).run("poc", _input_args("poc"), SANDBOX)

    assert evidence.ok is True
    assert [data for _, data in writer_calls] == [TRICKY_INPUT]
    directory = writer_calls[0][0].resolve()
    assert target_root.resolve() not in [directory, *directory.parents]
    assert sorted(p.relative_to(target_root).as_posix() for p in target_root.rglob("*")) == before


def test_fake_runner_leaves_no_input_file_behind(
    target_root: Path,
    tmp_path: Path,
    writer_calls: list[tuple[Path, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """쓰고 바로 지운다 — dev_fake는 파일 산출물이 없는 자리라 임시 디렉터리가 호출마다 쌓이면
    안 된다. 임시 위치를 이 테스트의 디렉터리로 돌려 **정말 거기에 썼다가 지웠는지** 본다."""
    litter = tmp_path / "tmp"
    litter.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(litter))

    evidence = _runner(target_root).run("poc", _input_args("poc"), SANDBOX)

    assert evidence.ok is True
    assert writer_calls[0][0].parent == litter, "임시 디렉터리를 안 썼다 — 이 검사가 공허하다"
    assert list(litter.iterdir()) == []


def test_fake_runner_without_input_never_calls_the_writer(
    target_root: Path, writer_calls: list[tuple[Path, bytes]]
) -> None:
    _runner(target_root).run("poc", _args(".", "poc"), SANDBOX)

    assert writer_calls == []


def test_fake_runner_writes_an_empty_input_too(
    target_root: Path, writer_calls: list[tuple[Path, bytes]]
) -> None:
    """`b""`는 입력이다 — `is not None`을 진리값 판정(`if args.input_bytes:`)으로 바꾸면 0바이트
    PoC가 「입력 없음」으로 둔갑해 쓰기가 건너뛰어진다. 쓰기가 빈 바이트로 **한 번** 나갔는지
    본다."""
    evidence = _runner(target_root).run("poc", _input_args("poc", b""), SANDBOX)

    assert evidence.ok is True
    assert [data for _, data in writer_calls] == [b""]


def test_fake_runner_rejects_before_it_writes_anything(
    target_root: Path, writer_calls: list[tuple[Path, bytes]]
) -> None:
    """정책 밖 도구·network·마운트 밖 경로는 거절이 **먼저**다 — 거절된 요청의 바이트는 안 쓴다."""
    placeholder = tool_runner.INPUT_PLACEHOLDER
    outside = ToolArgs(argv=["curl", placeholder], workdir=".", input_bytes=TRICKY_INPUT)
    escaped = ToolArgs(argv=["poc", placeholder], workdir="../secrets", input_bytes=TRICKY_INPUT)

    assert not _runner(target_root).run("curl", outside, SANDBOX).ok
    assert not _runner(target_root).run("poc", escaped, SANDBOX).ok
    assert not _runner(target_root).run(
        "poc", _input_args("poc"), SANDBOX.transition(network=True)
    ).ok
    assert writer_calls == []


@pytest.mark.parametrize("tool", ["asan", "gdb", "poc"])
def test_fake_repro_outcome_is_a_constant_whatever_the_input_is(
    target_root: Path, tool: str
) -> None:
    """🔴 입력 배선은 **관측만** 바꾼다. `_run_repro`의 `ok`·`exit_code`는 측정이 아니라 상수이고,
    입력 바이트에 흔들리면 Evidence Gate E4의 뜻이 움직인다 — 「소스가 있다」가 곧 `proven`인
    자리다(이슈 #232). 다른 바이트·빈 입력·입력 없음이 전부 같은 결과여야 한다."""
    runner = _runner(target_root)
    zeros = bytes(len(TRICKY_INPUT))

    results = [
        runner.run(tool, _input_args(tool, data), SANDBOX) for data in (TRICKY_INPUT, zeros, b"")
    ]
    results.append(runner.run(tool, _args(".", tool), SANDBOX))

    assert [(e.ok, e.exit_code, e.error) for e in results] == [(True, 1, None)] * 4


def test_fake_repro_without_a_source_still_fails_whatever_the_input_is(target_root: Path) -> None:
    """실패 쪽 상수(`exit_code=2`)도 입력과 무관하다."""
    missing = ToolArgs(
        argv=["poc", tool_runner.INPUT_PLACEHOLDER],
        workdir="없는디렉터리",
        input_bytes=TRICKY_INPUT,
    )

    evidence = _runner(target_root).run("poc", missing, SANDBOX)

    assert (evidence.ok, evidence.exit_code) == (False, 2)


def test_fake_runner_turns_an_unwritable_input_into_failed_evidence(
    target_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """도구 실패도 Evidence다 — 예외를 던지지 않는다(engineering-guide.md §8.3). 문구는 모델 관측에
    실리므로 호스트 경로를 안 든다."""

    def broken(directory: Path, data: bytes) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device", "/host/secret/dir/input.poc")

    monkeypatch.setattr(fake_tools, "write_input", broken)

    evidence = _runner(target_root).run("poc", _input_args("poc"), SANDBOX)

    assert evidence.ok is False
    assert evidence.exit_code == 2
    assert "입력" in (evidence.error or "")
    assert "/host/secret" not in (evidence.error or "")


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


def test_sqlite_closes_as_a_context_manager_and_twice_is_harmless(tmp_path: Path) -> None:
    """`with`가 닫고, 닫힌 뒤의 조회는 실패한다 — 닫는다고 적고 안 닫는 구현을 잡는다(#156).
    두 번 닫아도 예외가 없다 — CLI가 정상·실패 경로에서 각각 부를 수 있다."""
    with SqliteStateStore(tmp_path / "state.db") as store:
        store.put(_candidate("cand_0001"))
    with pytest.raises(sqlite3.ProgrammingError):
        store.get("candidate", "cand_0001")
    store.close()
    InMemoryStateStore().close()  # 계약만 맞춘다 — 놓을 자원이 없다


def test_the_state_store_contract_declares_close() -> None:
    """CLI가 `deps.store.close()`를 부르는 근거는 `StateStore` 계약이다(#156). 이 저장소에는 정적
    타입 검사가 없어 계약에서 `close`가 빠져도 런타임·CI가 모른다(#540 리뷰) — 선언을 직접 본다."""
    from src.interfaces.state_store import StateStore

    assert "close" in StateStore.__dict__
    for impl in (InMemoryStateStore, SqliteStateStore):
        assert callable(getattr(impl, "close", None)), impl.__name__


def test_sqlite_payload_is_schema_json(tmp_path: Path) -> None:
    """저장 형태가 스키마 JSON이어야 읽을 때 검증이 돈다."""
    store = SqliteStateStore(tmp_path / "state.db")
    store.put(_candidate("cand_0001"))
    row = store._conn.execute("SELECT payload FROM objects").fetchone()  # noqa: SLF001
    assert json.loads(row[0])["candidate_id"] == "cand_0001"


# --- state.db 저장 형태 판 게이트 (이슈 #281) --------------------------------


def _user_version(path: Path) -> int:
    # `with sqlite3.connect(...)`는 트랜잭션만 닫고 연결은 안 닫는다 — 명시적으로 닫는다.
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _legacy_db(path: Path, payload: str) -> None:
    """게이트 전 코드가 만든 파일의 모양 — 표는 있고 판은 0이다. DDL은 그 코드의 것 그대로다."""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE objects (kind TEXT NOT NULL, obj_id TEXT NOT NULL, run_id TEXT, "
        "payload TEXT NOT NULL, PRIMARY KEY (kind, obj_id));"
        "CREATE INDEX objects_by_run ON objects (kind, run_id);"
    )
    conn.execute("INSERT INTO objects VALUES ('candidate', 'cand_0001', ?, ?)", (RUN_ID, payload))
    conn.commit()
    conn.close()


def _stamped_db(path: Path, version: int) -> None:
    """판 `version`인 파일 — 지금 코드로 만들고 행 하나를 쓴 뒤 판만 바꾼다."""
    with SqliteStateStore(path) as first:
        first.put(_candidate("cand_0001"))
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.close()


def test_a_new_state_db_carries_the_current_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    SqliteStateStore(path).close()
    assert sqlite_store.STATE_DB_SCHEMA_VERSION >= 1  # 0은 「판 없음」이라 쓰면 안 된다
    assert _user_version(path) == sqlite_store.STATE_DB_SCHEMA_VERSION


def test_a_pre_gate_state_db_still_opens_and_is_not_restamped(tmp_path: Path) -> None:
    """게이트 전 코드가 만든 파일(판 0)은 종전대로 연다 — 보존된 run을 프로브가 읽는다
    (`tools/probe/critic_output_budget.py`). 판을 새로 적지 않는다: 안의 행이 어느 모양인지
    모른다."""
    path = tmp_path / "state.db"
    _legacy_db(path, _candidate("cand_0001").model_dump_json())
    with SqliteStateStore(path) as store:
        assert store.get("candidate", "cand_0001") == _candidate("cand_0001")
        store.put(_candidate("cand_0002"))
    assert _user_version(path) == 0


def test_a_state_db_of_the_current_version_reopens(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _stamped_db(path, sqlite_store.STATE_DB_SCHEMA_VERSION)
    with SqliteStateStore(path) as store:
        assert store.get("candidate", "cand_0001") is not None


def _spy_connect(
    monkeypatch: pytest.MonkeyPatch, authorizer: object = None
) -> list[tuple[sqlite3.Connection, list[str]]]:
    """`sqlite_store`가 여는 연결과 그 SQL을 모은다. `monkeypatch.context()` 안에서 쓴다."""
    seen: list[tuple[sqlite3.Connection, list[str]]] = []
    real = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real(*args, **kwargs)
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        if authorizer is not None:
            conn.set_authorizer(authorizer)
        seen.append((conn, statements))
        return conn

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", connect)
    return seen


@pytest.mark.parametrize(
    ("stored", "code_version", "says"),
    [
        (2, None, "더 새 코드"),
        (5, None, "더 새 코드"),
        (2**31 - 1, None, "더 새 코드"),
        (-1, None, "더 옛 코드"),
        (1, 2, "더 옛 코드"),
    ],
)
def test_a_state_db_of_another_version_is_refused_before_any_row_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored: int,
    code_version: int | None,
    says: str,
) -> None:
    """판이 다르면 여는 자리에서 거부한다 — 되읽기의 `ValidationError`가 아니라 이유를 대고 멈춘다.

    저장된 행은 **읽을 수 없는 행**이다 — 행을 읽고 검증한 뒤에 거부하면 `ValidationError`가 먼저
    난다. 행을 읽는 SQL이 실행되지 않았고 파일에 쓰는 SQL도 없었음을 실행된 문장으로 직접 본다.
    연결이 닫혔는지도 OS 파일 잠금이 아니라 그 연결에 질의해 본다(Linux는 열린 파일도 지워진다)."""
    path = tmp_path / "state.db"
    _stamped_db(path, stored)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE objects SET payload = '{\"bogus\": 1}'")
    conn.commit()
    conn.close()
    with monkeypatch.context() as patched:
        if code_version is not None:
            patched.setattr(sqlite_store, "STATE_DB_SCHEMA_VERSION", code_version)
        seen = _spy_connect(patched)
        with pytest.raises(sqlite_store.StateDbVersionError, match=says) as raised:
            SqliteStateStore(path)
    assert "#281" in str(raised.value)
    [(opened, statements)] = seen
    assert not any(
        s.upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE")) or "payload" in s
        for s in statements
    ), statements
    with pytest.raises(sqlite3.ProgrammingError):  # 닫힌 연결
        opened.execute("select 1")
    path.unlink()
    assert not path.exists()


def test_a_new_state_db_is_never_left_with_a_table_but_no_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """표를 만들고 판을 적는 사이에 실패해도 (표 있음 · 판 0)이 남지 않는다 — 그 모양은 「게이트 전
    파일」이라 다음에 열 때 판을 안 적고 어떤 판의 코드든 받는다(게이트가 조용히 꺼진다).
    판 기록만 거부하는 authorizer로 그 사이의 실패를 흉내낸다."""

    def deny_version_write(action: int, name: str | None, arg: str | None, *_: object) -> int:
        denied = action == sqlite3.SQLITE_PRAGMA and name == "user_version" and arg is not None
        return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK

    path = tmp_path / "state.db"
    with monkeypatch.context() as patched:
        _spy_connect(patched, deny_version_write)
        with pytest.raises(sqlite3.DatabaseError):
            SqliteStateStore(path)
    conn = sqlite3.connect(path)
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert (len(tables) > 0) == (version == sqlite_store.STATE_DB_SCHEMA_VERSION), (tables, version)


def test_a_refused_state_db_is_left_untouched(tmp_path: Path) -> None:
    """거부가 파일을 고치지 않는다 — 판도 행도 그대로다(그 판의 체크아웃이 다시 연다)."""
    path = tmp_path / "state.db"
    _stamped_db(path, sqlite_store.STATE_DB_SCHEMA_VERSION + 1)
    before = path.read_bytes()
    with pytest.raises(sqlite_store.StateDbVersionError):
        SqliteStateStore(path)
    assert path.read_bytes() == before


def _normalized_ddl_hash(ddl: str) -> str:
    """DDL에서 SQL 주석(`-- …` · `/* … */`)을 떼고 공백을 한 칸으로 접어 sha256을 낸다.

    식별자·키워드 대소문자는 그대로다.
    ponytail: 문자열 리터럴 안의 `--`도 주석으로 읽는다 — 이 DDL엔 리터럴이 없다.
    생기면 토크나이저로 바꾼다.
    """
    body = re.sub(r"/\*.*?\*/", " ", ddl, flags=re.DOTALL)
    body = re.sub(r"--[^\n]*", " ", body)
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()


# (STATE_DB_SCHEMA_VERSION, `_SCHEMA`의 정규화 해시). **`_SCHEMA`(DDL)를 고쳤으면 같은 커밋에서
# 판을 올리고 이 짝을 같이 갱신한다.** 주석만 고친 것은 해시를 안 바꾼다. (이슈 #594 (a))
# ⚠ 이 짝은 **DDL만** 문다. 모델 쪽(`store_common.KIND_MAP`의 필드)은 DDL이 그대로라 여기서
# 안 잡히고, 아래 `_PINNED_KIND_FIELDS`가 그 자리를 문다.
_PINNED_SCHEMA = (1, "6a9e60b5a393697a75091c7a5bc4f5a5bd1ff057dae198eec3ab3423737cb43a")


def test_the_state_db_ddl_and_its_version_move_together() -> None:
    now = (sqlite_store.STATE_DB_SCHEMA_VERSION, _normalized_ddl_hash(sqlite_store._SCHEMA))
    assert now == _PINNED_SCHEMA, (
        f"`_SCHEMA` 또는 `STATE_DB_SCHEMA_VERSION`이 바뀌었다: {now}. DDL을 고쳤으면 판을 올리고 "
        "`_PINNED_SCHEMA`를 이 값으로 갱신한다(같은 커밋). 판만 올렸다면 짝만 갱신한다."
    )


def test_the_ddl_normalizer_ignores_comments_and_spacing_but_not_a_token() -> None:
    base = sqlite_store._SCHEMA
    tail = base.replace("NOT NULL,\n    PRIMARY", "NOT NULL, -- 뒤 주석\n    PRIMARY")
    commented = ("-- 머리 주석\n" + tail).replace("kind    TEXT", "/* 블록 */ kind\n\n  TEXT")
    assert commented.count("--") == 2 and "/*" in commented  # 변이가 실제로 일어났다
    assert _normalized_ddl_hash(commented) == _normalized_ddl_hash(base)
    # 음성 — 토큰 하나(열 제약)를 바꾸면 해시가 달라진다.
    changed = base.replace("payload TEXT NOT NULL", "payload TEXT")
    assert changed != base
    assert _normalized_ddl_hash(changed) != _normalized_ddl_hash(base)


def _kind_field_fingerprint(model: type) -> str:
    """모델의 (필드 이름, 타입 표기) 짝을 정렬해 sha256을 낸다.

    표기는 `repr(FieldInfo.annotation)`이다 — 기본값·`Field` 제약·validator는 안 든다.
    """
    items = sorted((name, repr(f.annotation)) for name, f in model.model_fields.items())
    return hashlib.sha256(repr(items).encode("utf-8")).hexdigest()


def _kind_field_fingerprints() -> dict[str, str]:
    return {kind: _kind_field_fingerprint(model) for kind, (model, _) in KIND_MAP.items()}


# (STATE_DB_SCHEMA_VERSION, 종류별 필드 지문). **`KIND_MAP` 모델의 필드를 더하거나(기본값이
# 있어도) 지우거나 타입을 바꿨으면 같은 커밋에서 판을 올리고 이 짝을 같이 갱신한다** — 저장은
# JSON 한 컬럼이라 DDL이 그대로여서 `_PINNED_SCHEMA`가 못 무는 자리다(이슈 #594 ·
# `sqlite_store.py` 모듈 docstring의 올림 규칙).
# 지문은 `86c3a26a`(이 브랜치 머리)에서 쟀고 그때 판은 1이었다.
# ⚠ 이 짝이 무는 것은 **필드 이름 집합 · 타입 표기 · `KIND_MAP` 종류 집합**이다. 참인 명제는
# 「이 지문으로 바뀌면 죽는다」다 — validator·기본값·`Field` 제약만 바뀌면 지문은 그대로이고,
# 그 경우도 판을 올려야 할 수 있다(모듈 docstring의 「값 집합을 넓히거나 좁히기」·「뜻만 바뀐
# 필드」. `Finding.cwe`가 그 예다 — `src/schemas/finding.py`의 `_FindingCwe`).
# ⚠ 표기는 pydantic·파이썬 판에 달렸다(잰 환경은 pydantic 2.12.4 · CPython 3.11). 판이 올라
# 표기만 바뀌면 모델이 그대로여도 빨개진다 — 그때는 저장 형태 판을 올리지 말고 지문만 갱신한다.
_PINNED_KIND_FIELDS = (
    1,
    {
        "artifact": "8eea8b0fd18b150d52aab684c6d943cf7069367d2b42522ff178cd2b3204d6d2",
        "candidate": "380295d3958c46e31d5a9f9e961b9bf2c2add5f9f9b9846430b30df007560550",
        "evidence": "8183ed893060ffe77f9e86633bd74d83cec759b6d53432d218ecbdfa15f4d5ef",
        "finding": "ef67fb76b9918b65b86c8aa885c6afe9536312b771d87dfd7e2f46d88cf44464",
        "missing_evidence": "ad6b9a45c5d8ba5c46018fb6748b9cfb9f42bca0b11ce97e7d54afc35eddc522",
        "decision": "cfd7cc671c05ba7169fc283348f03d9cb7a665cf1e3009bcc8e6501f7bf2ca01",
        "capsule": "17f838e61acf16b926e78038ac8ca36a268ecfc4f267b0bd9d11b6b858e5359b",
        "run": "c3af41e4e66801d954c4cd3a456a516d84eb2bf4e0a7d2707b1c5c0fd4ec44b3",
    },
)


def test_the_stored_model_fields_and_the_db_version_move_together() -> None:
    pinned_version, pinned = _PINNED_KIND_FIELDS
    live = _kind_field_fingerprints()
    moved = sorted(k for k in set(live) | set(pinned) if live.get(k) != pinned.get(k))
    assert (sqlite_store.STATE_DB_SCHEMA_VERSION, live) == (pinned_version, pinned), (
        f"저장되는 모델의 필드가 바뀌었거나 판이 움직였다 — 바뀐 종류 {moved} · 판 "
        f"{pinned_version} -> {sqlite_store.STATE_DB_SCHEMA_VERSION}. 필드가 바뀌었으면 "
        "`STATE_DB_SCHEMA_VERSION`을 올리고 이 짝을 같은 커밋에서 "
        f"{(sqlite_store.STATE_DB_SCHEMA_VERSION, live)}로 갱신한다."
    )


def test_the_kind_field_fingerprint_bites_a_new_field_but_not_prose_or_a_validator() -> None:
    """지문이 무는 것과 안 무는 것.

    양성 핀은 저장소에서 샌 실형태다 — `RunRecord.keep_responses`(`src/schemas/run.py:361`)가
    기본값 있는 필드 하나로 커밋 `f0cdba76`에 들어오면서 판을 안 올렸다.
    """

    class WithAnotherKeptList(RunRecord):
        another_kept_list: list[str] | None = None

    class ProseOnly(RunRecord):
        """docstring만 다른 모델 — 모듈 docstring이 「docstring만 바뀐 것은 아니다」라 적는다."""

    class ValidatorOnly(RunRecord):
        @model_validator(mode="after")
        def _accepts_everything(self) -> "ValidatorOnly":
            return self

    base = _kind_field_fingerprint(RunRecord)
    assert _kind_field_fingerprint(WithAnotherKeptList) != base  # 양성
    assert _kind_field_fingerprint(ProseOnly) == base  # 음성
    # 음성이고 **한계**다 — validator만 바뀌어도 판을 올려야 할 수 있는데 지문은 안 움직인다.
    assert _kind_field_fingerprint(ValidatorOnly) == base


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
    # 그리고 **그 사실이 기록에 남는다** — 폴백이 조용하지 않다는 것이 D21 (b)의 절반이다.
    assert deps.counter_report == {
        "scout_counter": "approx",
        "scout_source": None,
        "scout_reason": "no_source",
        "auditor_counter": "approx",
        "auditor_source": None,
        "auditor_reason": "no_source",
    }


def _with_source(config: HarnessConfig, role: str, source: str) -> HarnessConfig:
    """`role`이 쓰는 모델의 `source`만 바꾼 설정. `config/models.yaml`은 안 건드린다."""
    key = config.model_key_for(role)
    entries = dict(config.models)
    entries[key] = entries[key].model_copy(update={"source": source})
    return config.model_copy(update={"models": entries})


def test_counter_report_says_file_absent_when_the_snapshot_is_not_there(
    target_root: Path, tmp_path: Path
) -> None:
    """`source`는 있는데 그 경로에 파일이 없으면 `file_absent`다.

    `no_source`(설정이 그렇게 생겼다)와 다른 사건이다 — 이쪽은 캐시를 받아야 한다.
    이 구별이 없으면 ATOM에서 스냅샷이 안 받아진 run과 `dev_fake` run이 기록에서 같아 보인다.
    """
    source = f"file://{(tmp_path / 'nowhere').as_posix()}"
    config = _with_source(served_scout_config(), "scout", source)
    deps = build_deps(
        config, run_id=RUN_ID, source_root=target_root, artifacts_root=tmp_path / "artifacts"
    )

    assert deps.counter_for_role("scout") is None
    assert deps.counter_report["scout_counter"] == "approx"
    assert deps.counter_report["scout_source"] == source
    assert deps.counter_report["scout_reason"] == "file_absent"


def test_one_profile_mixes_real_and_fake(target_root: Path, tmp_path: Path) -> None:
    """한 프로필 안에서 scout만 실서빙된다 — RoutingModelClient가 있는 이유다.

    옛 `single_gpu_12g`가 그 모양이었다(2026-09-24 삭제). 지금은 `tests/conftest.py`의
    합성 config다.
    """
    deps = build_deps(
        served_scout_config(),
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


def test_counter_for_role_returns_none_for_a_role_the_profile_lacks(
    target_root: Path, tmp_path: Path
) -> None:
    """프로필에 없는 역할이면 `ConfigError`가 아니라 `None`이다.

    `build_deps`가 클라이언트를 `if role in config.profile.roles`로 거르는데 이 클로저만
    안 걸렀다. `_step_retrieval`은 그 카운터를 `Stage.RETRIEVAL` 검사 뒤·`Stage.SCOUT` 검사
    **앞**에서 부르므로, 4단계 앞에서 멈추는 run이 scout 없는 프로필로 돌면 거기서 죽는다.
    ⚠ 풀에서는 그 역할이 `scout`이 아니라 멤버 키다(PR #178).

    **그리고 `counter_report`는 프로필에 없는 역할을 싣지 않는다** — `_counted_roles`가
    프로필에 실재하는 자리만 돈다(풀 팬아웃 #178 후속). 없는 `scout`을 `no_source`로 싣던
    것이 「없는 것을 싣는다」는 거짓 기록이었다.
    """
    base = load_config("dev_fake")
    roles = {r: k for r, k in base.profile.roles.items() if r != "scout"}
    config = base.model_copy(update={"profile": base.profile.model_copy(update={"roles": roles})})
    deps = build_deps(
        config, run_id=RUN_ID, source_root=target_root, artifacts_root=tmp_path / "artifacts"
    )

    assert deps.counter_for_role("scout") is None
    assert "scout_counter" not in deps.counter_report, "프로필에 없는 scout이 기록에 실렸다"
    assert "scout_source" not in deps.counter_report
    assert deps.counter_report["auditor_counter"] == "approx"


def test_counter_report_of_a_pool_profile_names_the_first_member_not_scout(
    target_root: Path, tmp_path: Path
) -> None:
    """풀 프로필의 `token_counter`는 죽은 `scout`이 아니라 **켜지는 첫 멤버**를 싣는다.

    풀은 캡슐을 첫 멤버(`scout_memory`)가 만들어 그 카운터로 예산을 정하고 2·3번 멤버는
    캐시된 캡슐을 받는다. 그래서 실리는 scout 자리는 `scout_memory` 하나다 — `scout`은
    프로필에 없으므로 이벤트에서 빠진다(풀 팬아웃 #178 후속, 옛 「없는 것을 싣는다」).

    ⚠ 멤버 2·3의 토큰 회계 근거는 여전히 기록에 없다(공유 캡슐이 지운다) — 그쪽은
    공유 캡슐 토크나이저 항목과 함께 닫힌다.
    """
    base = load_config("dev_fake")
    pool_roles = {
        "scout_memory": "fake_v2",
        "scout_boundary": "fake_v2",
        "scout_injection": "fake_v2",
        "auditor": "fake",
        "critic": "fake",
        "embedding": "fake",
        "reranker": "fake",
    }
    config = base.model_copy(
        update={"profile": base.profile.model_copy(update={"roles": pool_roles})}
    )
    deps = build_deps(
        config, run_id=RUN_ID, source_root=target_root, artifacts_root=tmp_path / "artifacts"
    )

    assert "scout_counter" not in deps.counter_report, "죽은 scout이 실렸다"
    assert deps.counter_report["scout_memory_counter"] == "approx"
    assert "scout_boundary_counter" not in deps.counter_report, (
        "2·3번 멤버는 캡슐을 공유해 카운터를 안 켜므로 기록에도 안 실린다"
    )
    assert deps.counter_report["auditor_counter"] == "approx"
