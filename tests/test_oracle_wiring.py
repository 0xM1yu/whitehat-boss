"""채점기(oracle) 배선 — 8단계가 낸 최종 PoC가 프로세스를 나가는가 (#386).

`_run_pov`가 루프 반환값을 **버리던** 것이 #386 ①이다. 아래는 그 출구가 실제로 열렸는지를
`dev_fake` 파이프라인으로 보고, 판정이 trace에만 남고 **내부 등급(E4)을 안 올리는 것**까지 묶는다.

`submit.sh` 파서와 CLI 조립 가드는 모델 없이 순수하게 본다.
"""

import argparse
from datetime import datetime
from pathlib import Path

import pytest

from src import cli
from src.adapters.cybergym_oracle import _was_cached
from src.interfaces.oracle import OracleOutcome
from src.orchestrator import runner
from src.orchestrator.task_loader import read_submit_metadata
from src.orchestrator.trace import EventKind
from src.schemas.enums import EvidenceKind, PovStopReason
from tests.test_nodes import _pov_state, ctx  # noqa: F401

_OUTCOME = OracleOutcome(
    task_id="9c73e92e52b7",
    poc_id="fc1ded8c085d4d43aea9c7cd255d8155",
    poc_hash="cee730778d0668a6cebf3abc399ac17a35ebb9d2f080c110ba0ff809cea8347a",
    poc_length=12,
    vul_exit_code=77,
    fix_exit_code=0,
    success=True,
    cached=False,
)


class _Recorder:
    """제출된 바이트를 들고 있는 `Oracle` 대역."""

    def __init__(
        self, outcome: OracleOutcome | None = None, raises: Exception | None = None
    ) -> None:
        self.outcome = outcome or _OUTCOME
        self.raises = raises
        self.submitted: list[bytes] = []

    def submit(self, poc: bytes) -> OracleOutcome:
        self.submitted.append(poc)
        if self.raises is not None:
            raise self.raises
        return self.outcome


def _run(ctx, oracle):  # noqa: ANN001, ANN202, F811
    ctx.oracle = oracle
    state, finding = _pov_state(ctx)
    runner._run_pov(ctx, state, finding)
    return finding


def _events(ctx, kind):  # noqa: ANN001, ANN202, F811
    return [e for e in ctx.trace.events if e.kind is kind]


def test_the_final_poc_reaches_the_oracle(ctx) -> None:  # noqa: F811
    """#386 ①: 루프가 SUBMITTED로 끝나면 그 **바이트가** 채점기로 간다 — 전엔 버려졌다."""
    oracle = _Recorder()

    finding = _run(ctx, oracle)

    generated = _events(ctx, EventKind.POV_GENERATED)
    assert len(generated) == 1, "dev_fake가 최종 PoC를 안 냈다 — 이 테스트의 전제가 깨졌다"
    assert len(oracle.submitted) == 1, "최종 PoC가 채점기로 안 갔다(#386 ①)"
    assert len(oracle.submitted[0]) == generated[0].data["poc_bytes"]
    verdict = _events(ctx, EventKind.ORACLE_VERDICT)
    assert len(verdict) == 1 and verdict[0].subject_id == finding.finding_id


def test_the_verdict_is_recorded_with_exit_codes_and_cache_state(ctx) -> None:  # noqa: F811
    """판정이 trace에 그대로 남는다 — 종료 코드 둘·success·cached."""
    _run(ctx, _Recorder())

    data = _events(ctx, EventKind.ORACLE_VERDICT)[0].data
    assert data["vul_exit_code"] == 77
    assert data["fix_exit_code"] == 0
    assert data["success"] is True
    assert data["cached"] is False
    assert data["task_id"] == "9c73e92e52b7"
    assert data["poc_sha256"] == _OUTCOME.poc_hash


def test_a_mode_that_did_not_run_is_not_written_as_zero(ctx) -> None:  # noqa: F811
    """⚠ §0: `fix`가 안 돌았으면(`None`) 0으로 적지 않는다 — `vul`에서 크래시가 안 난 경우다."""
    outcome = OracleOutcome(
        task_id="t",
        poc_id=None,
        poc_hash="h",
        poc_length=3,
        vul_exit_code=0,
        fix_exit_code=None,
        success=False,
        cached=None,
    )

    _run(ctx, _Recorder(outcome))

    data = _events(ctx, EventKind.ORACLE_VERDICT)[0].data
    assert data["fix_exit_code"] == "not_run", "안 돌았다를 0으로 적었다"
    assert data["vul_exit_code"] == 0, "진짜 0이 not_run으로 바뀌었다"
    assert data["cached"] == "unknown", "못 가린 캐시 상태를 거짓으로 적었다"
    assert data["success"] is False


def test_the_verdict_does_not_become_internal_evidence(ctx) -> None:  # noqa: F811
    """채점 판정은 Evidence가 아니다 — 게이트가 읽는 근거를 늘리지 않는다(#232)."""
    before = {e.evidence_id for e in ctx.store.query("evidence")}

    _run(ctx, _Recorder())

    after = [e for e in ctx.store.query("evidence") if e.evidence_id not in before]
    assert not [e for e in after if e.kind is EvidenceKind.CRASH], "판정을 CRASH 근거로 승격했다"


def test_an_oracle_failure_is_recorded_and_the_run_continues(ctx) -> None:  # noqa: F811
    """채점기는 저장소 밖 서비스다 — 예외를 흡수하되 **사유를 남긴다**(미제출과 구별)."""
    _run(ctx, _Recorder(raises=RuntimeError("server refused")))

    failed = _events(ctx, EventKind.ORACLE_FAILED)
    assert len(failed) == 1
    assert failed[0].data["error"] == "RuntimeError"
    assert "server refused" in failed[0].data["error_detail"]
    assert not _events(ctx, EventKind.ORACLE_VERDICT), "실패인데 판정을 남겼다"


def test_without_an_oracle_nothing_is_submitted(ctx) -> None:  # noqa: F811
    """기본(미배선)이면 제출도 이벤트도 없다 — 이벤트 부재 = 제출 안 함."""
    state, finding = _pov_state(ctx)
    ctx.oracle = None

    runner._run_pov(ctx, state, finding)

    assert not _events(ctx, EventKind.ORACLE_VERDICT)
    assert not _events(ctx, EventKind.ORACLE_FAILED)
    assert _events(ctx, EventKind.POV_GENERATED), "PoV 자체는 돌았어야 한다"


def test_a_loop_without_a_final_poc_does_not_submit(
    ctx,  # noqa: ANN001, F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SUBMITTED가 아니면(최종 PoC 없음) 제출하지 않는다 — 중간 gen 바이트는 안 내보낸다."""
    oracle = _Recorder()
    ctx.oracle = oracle
    state, finding = _pov_state(ctx)
    called: list[object] = []

    def _no_final(*args: object, **kwargs: object) -> tuple[None, PovStopReason]:
        called.append(args)
        return None, PovStopReason.MAX_HOP

    monkeypatch.setattr(runner, "_run_pov_hops", _no_final)

    runner._run_pov(ctx, state, finding)

    assert called, "루프를 안 불렀다"
    assert not oracle.submitted, "최종 PoC가 없는데 제출했다"
    assert not _events(ctx, EventKind.ORACLE_VERDICT)


# --- submit.sh 파서 (과제 신원을 지어내지 않는다) ---

_SUBMIT = (
    "#!/bin/bash\n"
    "curl -X POST http://127.0.0.1:8666/submit-vul \\\n"
    "  -F 'metadata={\"task_id\": \"9c73e92e52b7\", \"agent_id\": \"dcd41fef\", "
    '"checksum": "x"}\' \\\n'
    '  -F "file=@${POC_FILE}"\n'
)


def test_submit_metadata_is_read_from_the_bundle(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(_SUBMIT, encoding="utf-8")

    metadata = read_submit_metadata(task)

    assert metadata is not None
    assert metadata.server == "http://127.0.0.1:8666"
    assert metadata.task_id == "9c73e92e52b7"
    assert metadata.agent_id == "dcd41fef"


@pytest.mark.parametrize(
    "text",
    ["", "#!/bin/bash\necho hi\n", "curl http://h/submit-vul\n", "metadata={bad json}\n"],
)
def test_unreadable_submit_metadata_is_none_not_invented(tmp_path: Path, text: str) -> None:
    """못 읽으면 `None`이다 — 신원을 지어내 제출하지 않는다(§0)."""
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(text, encoding="utf-8")

    assert read_submit_metadata(task) is None


def test_missing_submit_script_is_none(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()

    assert read_submit_metadata(task) is None


# --- CLI 조립 가드 (설정 오류가 조용히 미제출로 떨어지지 않는다) ---


def _args(task_dir: Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(oracle_submit=True, task_dir=task_dir)


def test_oracle_is_none_when_the_flag_is_off() -> None:
    assert cli._oracle_for(argparse.Namespace(oracle_submit=False, task_dir=None)) is None


def test_oracle_submit_without_a_task_dir_is_an_error() -> None:
    assert cli._oracle_for(_args()) is cli._ORACLE_ERROR


def test_oracle_submit_without_metadata_is_an_error(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()

    assert cli._oracle_for(_args(task)) is cli._ORACLE_ERROR


def test_oracle_submit_without_a_key_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """키가 없으면 멈춘다 — 제출하려 한 run이 조용히 0건 제출로 끝나지 않는다."""
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(_SUBMIT, encoding="utf-8")
    monkeypatch.delenv(cli._ORACLE_KEY_ENV, raising=False)

    assert cli._oracle_for(_args(task)) is cli._ORACLE_ERROR


def test_oracle_is_built_from_the_bundle_and_env_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(_SUBMIT, encoding="utf-8")
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "secret-key")

    oracle = cli._oracle_for(_args(task))

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    assert oracle.server == "http://127.0.0.1:8666"
    assert oracle.task_id == "9c73e92e52b7"
    assert oracle.agent_id == "dcd41fef"
    assert oracle.api_key == "secret-key"


# --- query_task_id (마스킹 서버) ---


def test_task_oracle_uses_query_task_id_for_the_db_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """마스킹 서버에선 제출은 masked, 조회는 unmask된 id로 가야 한다 — DB가 unmask로 저장한다."""
    from src.adapters import cybergym_oracle as oc

    calls: dict[str, str] = {}

    def _submit_vul(server: str, task_id: str, *a: object, **k: object) -> object:
        calls["submit_vul"] = task_id
        return oc.SubmitResult(task_id=task_id, response_exit_code=0, output="", poc_id="p1")

    def _submit_fix(server: str, task_id: str, *a: object, **k: object) -> object:
        calls["submit_fix"] = task_id
        return oc.SubmitResult(task_id=task_id, response_exit_code=0, output="", poc_id="p1")

    def _query_verdict(server: str, task_id: str, *a: object, **k: object) -> object:
        calls["query"] = task_id
        return oc.OracleVerdict(
            task_id=task_id,
            agent_id="a",
            poc_id="p1",
            poc_hash="h",
            vul_exit_code=0,
            fix_exit_code=0,
            created_at=None,
            updated_at=None,
        )

    monkeypatch.setattr(oc, "submit_vul", _submit_vul)
    monkeypatch.setattr(oc, "submit_fix", _submit_fix)
    monkeypatch.setattr(oc, "query_verdict", _query_verdict)

    oracle = oc.TaskOracle(
        server="http://x",
        task_id="9c73e92e52b7",
        agent_id="a",
        api_key="k",
        query_task_id="arvo:1065",
    )
    oracle.submit(b"xx")

    assert calls["submit_vul"] == "9c73e92e52b7"
    assert calls["submit_fix"] == "9c73e92e52b7"
    assert calls["query"] == "arvo:1065", "조회에 masked id를 썼다 — 마스킹 서버에서 404로 떨어진다"


def test_task_oracle_falls_back_to_submit_task_id_when_no_query_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`query_task_id`가 None이면 마스크 없이 띄운 서버다 — 세 호출에 같은 id를 쓴다."""
    from src.adapters import cybergym_oracle as oc

    seen: list[str] = []
    def _mk_submit(s: str, t: str, *a: object, **k: object) -> object:
        seen.append(t)
        return oc.SubmitResult(task_id=t, response_exit_code=0, output="", poc_id="p")

    def _mk_query(s: str, t: str, *a: object, **k: object) -> object:
        seen.append(t)
        return oc.OracleVerdict(
            task_id=t,
            agent_id="a",
            poc_id="p",
            poc_hash="h",
            vul_exit_code=0,
            fix_exit_code=0,
            created_at=None,
            updated_at=None,
        )

    monkeypatch.setattr(oc, "submit_vul", _mk_submit)
    monkeypatch.setattr(oc, "submit_fix", _mk_submit)
    monkeypatch.setattr(oc, "query_verdict", _mk_query)

    oc.TaskOracle(server="s", task_id="arvo:1065", agent_id="a", api_key="k").submit(b"x")

    assert seen == ["arvo:1065", "arvo:1065", "arvo:1065"]


# --- CLI mask-map ---


def test_mask_map_unmasks_the_task_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--mask-map`이 있으면 CLI가 TaskOracle에 unmask된 query_task_id를 넘긴다."""
    import json as _json

    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(_SUBMIT, encoding="utf-8")
    mask = tmp_path / "mask.json"
    _json.dump({"arvo:1065": "9c73e92e52b7"}, mask.open("w", encoding="utf-8"))
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")

    args = argparse.Namespace(oracle_submit=True, task_dir=task, mask_map=mask)
    oracle = cli._oracle_for(args)

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    assert oracle.task_id == "9c73e92e52b7"
    assert oracle.query_task_id == "arvo:1065"


def test_mask_map_missing_entry_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(_SUBMIT, encoding="utf-8")
    mask = tmp_path / "mask.json"
    _json.dump({"arvo:9999": "deadbeef0000"}, mask.open("w", encoding="utf-8"))
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")

    args = argparse.Namespace(oracle_submit=True, task_dir=task, mask_map=mask)

    assert cli._oracle_for(args) is cli._ORACLE_ERROR


def test_no_mask_map_leaves_query_task_id_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--mask-map`을 안 주면 마스크 없이 띄운 서버라는 뜻 — `query_task_id`가 `None`이다."""
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(_SUBMIT, encoding="utf-8")
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")

    args = argparse.Namespace(oracle_submit=True, task_dir=task, mask_map=None)
    oracle = cli._oracle_for(args)

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    assert oracle.query_task_id is None


# --- 캐시 판별 (#385) ---


def test_cache_state_is_unknown_when_the_stamp_is_unreadable() -> None:
    """시각을 못 읽으면 `None`이다 — 「캐시 아님」으로 단정하지 않는다(§0)."""
    started = datetime(2026, 10, 3, 1, 0, 0)

    assert _was_cached(None, started) is None
    assert _was_cached("not-a-time", started) is None
    assert _was_cached("2026-10-03T00:59:00", started) is True
    assert _was_cached("2026-10-03 01:00:05", started) is False
