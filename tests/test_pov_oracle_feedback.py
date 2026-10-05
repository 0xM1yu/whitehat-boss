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
    runner._submit_vul_to_oracle(ctx, state, finding, b"")
    assert ctx.oracle.vul_calls == []
    failures = [e for e in ctx.trace.events if e.kind is EventKind.ORACLE_FAILED]
    assert failures[-1].data["reason"] == "empty_poc"
    assert "empty_poc" in state.oracle_ledger.record()["candidates"][-1]["error_detail"]


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
    assert "크래시한 걸 final로" not in prefix
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

