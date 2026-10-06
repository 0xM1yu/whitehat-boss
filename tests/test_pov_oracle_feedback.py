"""S3: intermediate vul observations reach the model, never the designation key.

Fixtures use the existing real PoV loop; HTTP calls are replaced at the adapter boundary.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.adapters import cybergym_oracle
from src.config import load_config
from src.interfaces.oracle import VulSubmission
from src.orchestrator import oracle_log, runner
from src.orchestrator.nodes import pov
from src.orchestrator.prompt_loader import load_prompt
from src.orchestrator.trace import EventKind
from src.schemas.enums import EvidenceGrade, Role
from src.schemas.pov import PovAction
from tests.oracle_fakes import TASK_ID, FakeOracle
from tests.test_nodes import _pov_state, ctx  # noqa: F401
from tests.test_pov_loop import _gen, _scripted, _with_pov_version


class ReadingOracle(FakeOracle):
    def __init__(self, codes=(300, 71), *, query_error=None, **kwargs):
        super().__init__(**kwargs)
        self.codes = iter(codes)
        self.query_calls = []
        self.query_error = query_error

    def query_vul(self, poc):
        self.query_calls.append(poc)
        if self.query_error:
            raise self.query_error
        return next(self.codes)


def _feedback_run(ctx, oracle, actions=None, *, version="v3", source=None):  # noqa: ANN001, ANN202, F811
    state, finding = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=oracle is not None)
    if source is not None:
        state.finding_sources[finding.finding_id] = runner._Once(lambda: source)
    scripted = _scripted(_with_pov_version(ctx, version), actions or [
        _gen(count=1), _gen(count=9), PovAction(action="submit", rationale="selected layout"),
    ])
    scripted.oracle = oracle
    before = dict(state.evidences), dict(state.findings)
    runner._run_pov(scripted, state, finding)
    assert (state.evidences, state.findings) == before
    return scripted, state, finding


def test_each_gen_reads_raw_vul_codes_into_the_next_model_turn(ctx):  # noqa: ANN001, F811
    oracle = ReadingOracle()
    scripted, state, finding = _feedback_run(ctx, oracle)
    assert oracle.vul_calls == [b"A", b"A" * 9, b"A" * 9]
    assert oracle.query_calls == [b"A", b"A" * 9]
    assert not oracle.final_calls
    prompts = [prompt for role, prompt in scripted.models.prompts if role is Role.POV]
    assert "oracle_vul exit_code=300" in prompts[1].body
    assert "oracle_vul exit_code=71" in prompts[2].body
    assert "crashed=" not in prompts[1].body + prompts[2].body
    assert [a.designatable for a in state.oracle_ledger.attempts] == [False, False, True]
    assert state.oracle_ledger.submitted == 3
    assert len(state.oracle_ledger.candidates) == 1
    runner._finalize_oracle(scripted, state)
    assert [poc for poc, _ in oracle.final_calls] == [b"A" * 9]
    assert state.oracle_ledger.record()["candidates"][0]["observation"]["exit_code"] == 300
    assert state.findings[finding.finding_id] == finding


@pytest.mark.parametrize("failure", ["submit", "query", "not_run", "not_wired"])
def test_unreadable_verdict_is_explicit(ctx, failure):  # noqa: ANN001, F811
    oracle = ReadingOracle(
        codes=(None,),
        vul_errors={0: RuntimeError("submit unavailable")} if failure == "submit" else {},
        query_error=RuntimeError("query unavailable") if failure == "query" else None,
    ) if failure != "not_wired" else None
    scripted, state, _ = _feedback_run(ctx, oracle, [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    prompt = scripted.models.last_for(Role.POV)
    assert "oracle_vul=unavailable" in prompt.body
    assert "oracle_vul exit_code=0" not in prompt.body
    if failure in {"submit", "query"}:
        failures = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_FAILED]
        assert failures
    if failure == "query":
        row = state.oracle_ledger.record()["candidates"][0]
        assert row["observation"]["error"] == "RuntimeError"
        assert row["observation"]["error_detail"] == "query unavailable"
    if oracle is None:
        assert state.oracle_ledger.attempts == []


def test_intermediate_small_bytes_cannot_win_the_frozen_designation_chain():
    sub = VulSubmission(TASK_ID, "id", 77)
    ledger = oracle_log.OracleLedger(wired=True)
    ledger.add_attempt(oracle_log.vul_ok("f", b"a", sub, 1, designatable=False))
    final = oracle_log.vul_ok("f", b"long final", sub, 1, designatable=True)
    ledger.add_attempt(final)
    winner, _, reason = runner._designate_final(ledger, {"f": EvidenceGrade.E0}, {"f": 0.5})
    assert winner is final and reason is None
    assert ledger.submitted == 2
    assert ledger.summary()["vul_submitted"] == 2
    assert [row.get("designatable", True) for row in ledger.record()["candidates"]] == [False, True]


def test_only_intermediate_attempts_leave_no_final_candidate():
    ledger = oracle_log.OracleLedger(wired=True)
    ledger.add_attempt(oracle_log.vul_ok("f", b"a", VulSubmission(TASK_ID, "id", 77), 1,
                                      designatable=False))
    assert ledger.candidates == []
    assert ledger.submitted == 1
    assert ledger.none_reason() == oracle_log.REASON_NO_POC

    ledger.add_attempt(
        oracle_log.vul_failed(
            "f", b"b", RuntimeError("intermediate failure"), 1, designatable=False
        )
    )
    assert ledger.none_reason() == oracle_log.REASON_NO_POC


def test_zero_bytes_are_rejected_before_any_oracle_submission(ctx):  # noqa: ANN001, F811
    state, finding = _pov_state(ctx)
    ctx.oracle = ReadingOracle()
    runner._submit_vul_to_oracle(ctx, state, finding, b"", designatable=True)
    assert ctx.oracle.vul_calls == []
    failures = [e for e in ctx.trace.events if e.kind is EventKind.ORACLE_FAILED]
    assert failures[-1].data["reason"] == "empty_poc"
    row = state.oracle_ledger.record()["candidates"][-1]
    assert (row["status"], row["error"]) == ("skipped", "empty_poc")


@pytest.mark.parametrize("code", [None, 0, 71, 300, 139])
def test_task_oracle_query_reads_only_the_vul_field_without_running_fix(monkeypatch, code):
    client = cybergym_oracle.TaskOracle("http://unused", TASK_ID, "agent", api_key="key")
    calls = []
    def peek(self, poc):
        calls.append(poc)
        return SimpleNamespace(vul_exit_code=code, fix_exit_code=0)
    monkeypatch.setattr(cybergym_oracle.TaskOracle, "_peek", peek)
    def forbidden(*args, **kwargs):
        pytest.fail("query_vul must not execute either submit leg")
    monkeypatch.setattr(cybergym_oracle, "submit_fix", forbidden)
    monkeypatch.setattr(cybergym_oracle, "submit_vul", forbidden)
    assert client.query_vul(b"own bytes") == code
    assert calls == [b"own bytes"]


def test_task_oracle_missing_row_is_unavailable(monkeypatch):
    monkeypatch.setattr(cybergym_oracle.TaskOracle, "_peek", lambda *args: None)
    client = cybergym_oracle.TaskOracle("http://unused", TASK_ID, "agent", api_key="key")
    assert client.query_vul(b"own bytes") is None


def test_wired_pov_gets_source_and_feedback_separately(ctx):  # noqa: ANN001, F811
    cfg = load_config(profile="harness_pool")
    version = cfg.model_for("pov").prompt_template_version
    assert version == "v3" and version in pov._WIDE_INPUT_VERSIONS
    # Repository witness: tests/test_nodes.py SOURCE banner and line gutter fixture.
    source = "/* ===== parser.c ===== */\n20| int parse_header(void) { return 0; }\n"
    scripted, _, _ = _feedback_run(ctx, None, [
        _gen(count=1), PovAction(action="submit", rationale="selected"),
    ], version=version, source=source)
    first = scripted.models.prompts[0][1]
    assert source in first.body
    assert "oracle_vul=unavailable" in scripted.models.last_for(Role.POV).body


def test_wired_prefix_explains_raw_observation_and_model_designation():
    version = load_config(profile="harness_pool").model_for("pov").prompt_template_version
    prefix = load_prompt(Role.POV, version).prefix
    assert "## 채점기 vul 관측" in prefix
    for text in ("oracle_vul exit_code=", "oracle_vul=unavailable", "fix", "submit", "중간"):
        assert text in prefix
    meta = Path(f"prompts/pov/{version}/meta.yaml").read_text(encoding="utf-8")
    assert "미배선(disk-only)" not in meta


def test_live_runner_prose_distinguishes_intermediate_from_final_submission():
    run_doc = runner._run_pov.__doc__ or ""
    hops_doc = runner._run_pov_hops.__doc__ or ""
    assert "중간 `gen`마다" in run_doc
    assert "`designatable=False`" in run_doc
    assert "최종 지정 후보가 아니지만" in run_doc
    assert "v3의 중간 gen은" in hops_doc
    assert "부분 PoC(`MAX_HOP`·`REPEAT_SPEC`·`PARSE_FAILED`" not in run_doc


def test_live_oracle_prose_does_not_call_every_vul_submission_a_candidate():
    """Repository witnesses: the stale forms came from the reviewed S3 source lines."""
    sources = {
        "src/interfaces/oracle.py": (
            "각 제출 PoC에는 `submit_vul`",
            "후보마다는 `submit_vul`",
        ),
        "src/adapters/cybergym_oracle.py": (
            "중간 gen과 모델 submit마다 `submit_vul`",
            "러너는 후보마다 `submit_vul`",
        ),
        "src/orchestrator/oracle_log.py": (
            "모든 vul 시도와 최종 지정 단계",
            "후보별 vul 시도와 최종 단계",
        ),
        "src/orchestrator/runner.py": (
            "모든 vul 시도와 최종 지정 단계",
            "채점기(oracle)의 후보별 vul 시도",
        ),
        "docs/agent-contracts.md": (
            "(모든 `vul` 시도마다)",
            "(후보마다)와",
        ),
    }
    for path, (required, stale) in sources.items():
        text = Path(path).read_text(encoding="utf-8")
        assert required in text, path
        assert stale not in text, path


# --- review fixes (PR #499 adversarial review) ------------------------------------------------


def _prompts(scripted):  # noqa: ANN001, ANN202
    return [prompt.body for role, prompt in scripted.models.prompts if role is Role.POV]


def test_exit_code_zero_is_a_real_observation_not_unavailable(ctx):  # noqa: ANN001, F811
    scripted, _, _ = _feedback_run(ctx, ReadingOracle(codes=(0, 0)), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    body = _prompts(scripted)[-1]
    assert "oracle_vul exit_code=0" in body
    assert "oracle_vul=unavailable" not in body


def test_cached_vul_row_is_marked_in_the_observation(ctx):  # noqa: ANN001, F811
    scripted, _, _ = _feedback_run(ctx, ReadingOracle(codes=(300,), vul_cached=True), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    assert "oracle_vul exit_code=300 cached=true" in _prompts(scripted)[-1]


def test_fresh_vul_row_carries_no_cache_marker(ctx):  # noqa: ANN001, F811
    scripted, _, _ = _feedback_run(ctx, ReadingOracle(codes=(300,), vul_cached=False), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    assert "cached" not in _prompts(scripted)[-1]


def test_missing_row_after_a_successful_submit_is_recorded(ctx):  # noqa: ANN001, F811
    _, state, _ = _feedback_run(ctx, ReadingOracle(codes=(None,)), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    ledger = state.oracle_ledger
    assert ledger.attempts[0].observation_error == oracle_log.OBSERVATION_NO_VERDICT
    assert ledger.observation_failed == 1
    assert ledger.summary()["vul_observation_failed"] == 1
    assert any("조회" in w for w in ledger.warnings())


def test_query_failure_shows_in_the_summary_and_warnings(ctx):  # noqa: ANN001, F811
    _, state, _ = _feedback_run(ctx, ReadingOracle(query_error=RuntimeError("down")), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    ledger = state.oracle_ledger
    assert ledger.failed == 0  # 제출은 성공했다 - 조회만 실패했다
    assert ledger.summary()["vul_observation_failed"] == 1
    assert any("조회" in w for w in ledger.warnings())


def test_clean_run_keeps_the_manifest_keys_it_had():
    ledger = oracle_log.OracleLedger(wired=True)
    sub = VulSubmission(TASK_ID, "id", 77)
    ledger.add_attempt(oracle_log.vul_ok("f", b"a", sub, 1, designatable=True))
    assert set(ledger.summary()) == {
        "vul_submitted", "vul_failed", "final", "final_reason", "record",
    }


def test_summary_says_when_intermediate_feedback_was_used(ctx):  # noqa: ANN001, F811
    _, state, _ = _feedback_run(ctx, ReadingOracle())
    assert state.oracle_ledger.summary()["vul_intermediate"] == 2


def test_warning_counts_candidates_not_submissions():
    sub = VulSubmission(TASK_ID, "id", 77)
    ledger = oracle_log.OracleLedger(wired=True)
    for name in ("a", "b"):
        ledger.add_attempt(oracle_log.vul_ok("f", name.encode(), sub, 1, designatable=False))
    for name in ("c", "d"):
        ledger.add_attempt(oracle_log.vul_ok(name, name.encode(), sub, 1, designatable=True))
    ledger.set_final(oracle_log.FinalAttempt(
        status=oracle_log.STATUS_NONE, reason=oracle_log.REASON_GRADE_UNKNOWN,
    ))
    text = next(w for w in ledger.warnings() if "후보" in w)
    assert "후보가 2건" in text and "4건" not in text


def test_empty_poc_is_not_a_failed_vul_submission(ctx):  # noqa: ANN001, F811
    oracle = ReadingOracle()
    scripted, state, _ = _feedback_run(ctx, oracle, [
        _gen(count=0), PovAction(action="submit", rationale="model decision"),
    ])
    ledger = state.oracle_ledger
    assert oracle.vul_calls == []
    assert ledger.failed == 0 and ledger.submitted == 0
    assert ledger.none_reason() == oracle_log.REASON_NO_POC
    assert ledger.summary()["vul_empty_poc"] == 2
    assert not any("예외로 실패" in w for w in ledger.warnings())
    rows = ledger.record()["candidates"]
    assert {(r["status"], r["error"]) for r in rows} == {("skipped", "empty_poc")}


def test_submit_helper_has_no_default_for_designatable(ctx):  # noqa: ANN001, F811
    state, finding = _pov_state(ctx)
    ctx.oracle = ReadingOracle()
    with pytest.raises(TypeError):
        runner._submit_vul_to_oracle(ctx, state, finding, b"x")


def test_failed_intermediates_alone_do_not_look_like_a_failed_submit(ctx):  # noqa: ANN001, F811
    oracle = ReadingOracle(vul_errors={0: RuntimeError("down")})
    _, state, _ = _feedback_run(ctx, oracle, [_gen(count=1), _gen(count=1)])
    ledger = state.oracle_ledger
    assert [a.designatable for a in ledger.attempts] == [False]
    assert ledger.failed == 1
    assert ledger.none_reason() == oracle_log.REASON_NO_POC


def test_empty_intermediate_alone_is_not_designatable(ctx):  # noqa: ANN001, F811
    _, state, _ = _feedback_run(ctx, ReadingOracle(), [_gen(count=0), _gen(count=0)])
    ledger = state.oracle_ledger
    assert [a.designatable for a in ledger.attempts] == [False]
    assert ledger.none_reason() == oracle_log.REASON_NO_POC


def test_designation_reads_no_grades_when_intermediates_are_present(ctx):  # noqa: ANN001, F811
    scripted, state, _ = _feedback_run(ctx, ReadingOracle(), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    runner._finalize_oracle(scripted, state)
    assert state.oracle_ledger.grades is None


def test_the_discarded_last_hop_gen_is_not_sent_to_the_oracle(ctx):  # noqa: ANN001, F811
    pov_hops = ctx.config.thresholds.pov_tool_hops
    oracle = ReadingOracle(codes=tuple(300 for _ in range(pov_hops + 5)))
    _feedback_run(ctx, oracle, [_gen(count=n) for n in range(1, pov_hops + 5)])
    # hop0..pov_hops-1 의 gen만 다음 홉이 읽는다 - 마지막 홉의 gen은 관측을 읽을 홉이 없다.
    assert len(oracle.vul_calls) == pov_hops


def test_the_oracle_guard_follows_pov_tool_hops_not_max_hops(ctx):  # noqa: ANN001, F811
    """중간 제출 가드도 PoV 전용 상한을 읽는다 — 적대적 검토 2026-10-06의 생존 변이.

    정지 검사만 `pov_tool_hops`로 옮기고 이 가드를 `max_hops`로 남기는 한 줄 변이가
    **아무 테스트도 빨갛게 하지 않았다.** 그러면 `max_hops < pov_tool_hops`인 설정에서
    그 홉부터 `oracle_vul=unavailable`이 조용히 나간다.
    """
    thresholds = ctx.config.thresholds.transition(pov_tool_hops=5, max_hops=0)
    ctx.config = ctx.config.transition(thresholds=thresholds)
    oracle = ReadingOracle(codes=tuple(300 for _ in range(10)))
    _feedback_run(ctx, oracle, [_gen(count=n) for n in range(1, 10)])
    assert len(oracle.vul_calls) == 5


def test_trace_events_say_whether_a_submission_was_intermediate(ctx):  # noqa: ANN001, F811
    scripted, _, _ = _feedback_run(ctx, ReadingOracle(codes=(300,)), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    sent = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_SUBMITTED]
    assert [e.data["designatable"] for e in sent] == [False, True]


def test_the_v3_prefix_does_not_contradict_the_oracle_line():
    prefix = load_prompt(Role.POV, "v3").prefix
    body = load_prompt(Role.POV, "v3").body
    flat = " ".join(prefix.split())
    # 오라클 줄은 모델 자신의 바이트에 대한 값이다 - 「관측은 입력의 결과가 아니다」가 그 줄까지
    # 덮으면 안 된다.
    for stale in (
        "고치고 관측을 근거로 고쳤다고 적지 않는다",
        "관측이 당신 입력에 반응하지 않으므로",
        "- 관측이 크래시를 안 보여도",
        "근거는 SOURCE와 FACTS뿐이다",
        "마지막 `gen`의 PoC가 최종답",
        "특히 71을",
    ):
        assert stale not in flat, stale
    assert "아래 JSON이 참조 가능한 사실 전부다" not in body
    assert "도구 관측" in flat and "oracle_vul" in flat
    assert "cached=true" in flat


def test_prose_no_longer_says_every_vul_submission_is_a_candidate():
    """Spelling pins (CLAUDE.md 2.5: they bite the exact forms found, not the proposition).

    Witnesses are the pre-fix lines the PR #499 review found by a loose sweep; the proposition is
    'intermediate gens also go to the vul leg, only the model submit is a candidate'.
    """
    stale = {
        "src/cli.py": ["후보마다 vul 다리만"],
        "docs/engineering-guide.md": ["후보마다 `submit_vul`, run당"],
        "docs/build-plan.md": ["(v3는 미배선)", "후보마다는 `submit_vul`만"],
        "docs/open-questions/D/D99.md": ["`gdb`를 받는데 배선된 `prompts/pov/v2"],
        "STATUS.md": ["**현재:** `Role.POV` + `prompts/pov/v2/`"],
        "src/orchestrator/trace.py": ["**후보(finding)마다\n    # 1행**"],
        "src/orchestrator/runner.py": ["(`vul_submitted` 수와 `stopped_after`로 재구성한다)"],
    }
    for path, phrases in stale.items():
        text = Path(path).read_text(encoding="utf-8")
        for phrase in phrases:
            assert phrase not in text, (path, phrase)


def test_summary_counts_candidates_apart_from_intermediate_attempts():
    sub = VulSubmission(TASK_ID, "id", 77)
    ledger = oracle_log.OracleLedger(wired=True)
    ledger.add_attempt(oracle_log.vul_ok("f", b"a", sub, 1, designatable=False))
    ledger.add_attempt(oracle_log.vul_failed("f", b"b", RuntimeError("x"), 1, designatable=False))
    ledger.add_attempt(oracle_log.vul_ok("f", b"c", sub, 1, designatable=True))
    summary = ledger.summary()
    # 성공한 중간 1 + 실패한 중간 1 + 최종 1: 둘의 차(2 - 2)가 후보 수가 아니다.
    assert (summary["vul_submitted"], summary["vul_intermediate"]) == (2, 2)
    assert summary["vul_candidates"] == 1


def test_a_run_without_intermediates_has_no_candidate_key():
    ledger = oracle_log.OracleLedger(wired=True)
    ledger.add_attempt(oracle_log.vul_ok("f", b"a", VulSubmission(TASK_ID, "id", 77), 1,
                                      designatable=True))
    assert "vul_candidates" not in ledger.summary()


def test_cached_marker_never_rides_on_an_unavailable_line(ctx):  # noqa: ANN001, F811
    scripted, _, _ = _feedback_run(ctx, ReadingOracle(codes=(None,), vul_cached=True), [
        _gen(count=1), PovAction(action="submit", rationale="model decision"),
    ])
    body = _prompts(scripted)[-1]
    assert "oracle_vul=unavailable" in body and "cached" not in body


def test_ledger_constructors_have_no_default_for_designatable():
    sub = VulSubmission(TASK_ID, "id", 77)
    with pytest.raises(TypeError):
        oracle_log.vul_ok("f", b"a", sub, 1)
    with pytest.raises(TypeError):
        oracle_log.vul_failed("f", b"a", RuntimeError("x"), 1)


def test_no_live_source_says_pov_v3_is_unwired():
    """Proposition pin (CLAUDE.md 2.5): 'the wired PoV prompt is v2 / v3 is not wired'.

    Scans every live doc and source (dated reports under docs/research and docs/experiments and
    this tests/ tree are excluded) for the forms the review found plus the nearby phrasings.
    """
    import re

    forms = re.compile(
        r"v3(는|가|은)?\s*(아직\s*)?미배선|미배선\s*`?v3|배선된\s*v2|v3(는|가)\s*아직\s*배선"
    )
    skipped = ("docs/research", "docs/experiments", "docs/decisions", "tests", "_workspace", ".git")
    offenders = []
    for suffix in ("*.md", "*.py", "*.yaml"):
        for path in Path(".").rglob(suffix):
            posix = path.as_posix()
            if posix.startswith(skipped):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if forms.search(line):
                    offenders.append(f"{posix}:{number}")
    assert not offenders, offenders


def test_candidate_key_is_present_at_zero_when_intermediates_exist():
    ledger = oracle_log.OracleLedger(wired=True)
    ledger.add_attempt(oracle_log.vul_ok("f", b"a", VulSubmission(TASK_ID, "id", 77), 1,
                                      designatable=False))
    summary = ledger.summary()
    # 모델이 submit하지 않은 run - 키가 없으면 `vul_submitted`(=1)가 후보 수로 읽힌다.
    assert (summary["vul_submitted"], summary["vul_candidates"]) == (1, 0)
