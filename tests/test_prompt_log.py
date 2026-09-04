"""프롬프트 영속 테스트 (open-questions D19).

**프롬프트 내용을 단언하지 않는다.** 여기서 고정하는 것은 셋뿐이다.

1. 모델 호출 한 번이 파일 하나를 만들고, `model_call`이 그것을 가리킨다
2. 파일에 prefix와 body가 **둘 다** 있다 — 사실 블록만 남기면 W1이 채운 산문 자리가
   기록에서 사라진다(D17)
3. 기록 장치가 없는 경로에서는 `model_call`이 **그대로**다 — `prompt_ref=None`을 늘
   실으면 "기록 장치가 없던 run"과 "프롬프트를 못 쓴 run"이 구별되지 않는다
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import cli
from src.adapters.memory_store import InMemoryStateStore
from src.config import load_config
from src.ids import IdFactory, sha256_text
from src.interfaces.model_client import ModelResult, Prompt, Usage
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_log import DIR_NAME, PromptLog, render
from src.orchestrator.runner import RunSpec
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas import Candidate, FindingState, Location, Role
from src.schemas.run import Budget

RUN_ID = "run_20260901_0001"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
STARTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CONDITION = "B3prime@pipeline"

PROMPT = Prompt(prefix="공통 prefix 블록", body="후보별 본문", prompt_version="v1")


# --- PromptLog 단독 -----------------------------------------------------------


def test_files_are_numbered_in_call_order(tmp_path: Path) -> None:
    log = PromptLog(tmp_path)

    first, _ = log.write(role="scout", stage=4, prompt=PROMPT)
    second, _ = log.write(role="auditor", stage=5, prompt=PROMPT)

    assert first == f"{DIR_NAME}/0001_scout.md"
    assert second == f"{DIR_NAME}/0002_auditor.md"


def test_file_carries_both_prefix_and_body(tmp_path: Path) -> None:
    """사실 블록만 남기면 D17이 지적한 산문 자리표시자가 기록에서 사라진다."""
    log = PromptLog(tmp_path)

    ref, _ = log.write(role="scout", stage=4, prompt=PROMPT)

    text = (tmp_path / ref).read_text(encoding="utf-8")
    assert PROMPT.prefix in text
    assert PROMPT.body in text
    assert "role=scout" in text
    assert "prompt_version=v1" in text


def test_ref_is_run_dir_relative_and_carries_no_run_id(tmp_path: Path) -> None:
    """`prompt_ref`가 run 의존 문자열을 새로 들이면 trace 비교 단위가 바뀐다."""
    log = PromptLog(tmp_path / RUN_ID)

    ref, _ = log.write(role="scout", stage=4, prompt=PROMPT)

    assert RUN_ID not in ref
    assert not Path(ref).is_absolute()
    assert (tmp_path / RUN_ID / ref).is_file()


def test_digest_matches_the_file_and_is_deterministic(tmp_path: Path) -> None:
    log = PromptLog(tmp_path)

    ref, digest = log.write(role="scout", stage=4, prompt=PROMPT)

    written = (tmp_path / ref).read_text(encoding="utf-8")
    assert digest == sha256_text(written)
    assert digest == sha256_text(render(role="scout", stage=4, prompt=PROMPT))


def test_newlines_are_lf_on_every_platform(tmp_path: Path) -> None:
    """CRLF가 섞이면 같은 입력의 두 실행이 장비에 따라 다른 `prompt_sha256`을 남긴다."""
    log = PromptLog(tmp_path)

    ref, _ = log.write(role="scout", stage=4, prompt=PROMPT)

    assert b"\r\n" not in (tmp_path / ref).read_bytes()


def test_write_retry_shares_the_first_seq_without_bumping(tmp_path: Path) -> None:
    """재시도는 1차와 같은 순번에 `_retryK`가 붙고, 카운터를 안 올려 다음 호출이 밀리지 않는다."""
    log = PromptLog(tmp_path)

    first, _ = log.write(role="scout", stage=4, prompt=PROMPT)
    retry, rdigest = log.write_retry(role="scout", stage=4, prompt=PROMPT, index=1)
    second, _ = log.write(role="auditor", stage=5, prompt=PROMPT)

    assert first == f"{DIR_NAME}/0001_scout.md"
    assert retry == f"{DIR_NAME}/0001_scout_retry1.md"  # 1차 순번 공유
    assert second == f"{DIR_NAME}/0002_auditor.md"  # 재시도가 순번을 안 먹었다
    assert rdigest == sha256_text((tmp_path / retry).read_text(encoding="utf-8"))
    assert b"\r\n" not in (tmp_path / retry).read_bytes()


# --- RunContext 배선 ----------------------------------------------------------


class StubModelClient:
    """정해진 값만 돌려주는 ModelClient. 어댑터 동작이 아니라 래퍼 계약을 본다."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201
        if self._fail:
            raise RuntimeError("구조화 출력 파싱 실패")
        value = Candidate(
            candidate_id="cand_0001",
            run_id=RUN_ID,
            artifact_id="art_0001",
            location=Location(file="parser.c"),
            score=0.7,
            state=FindingState.CANDIDATE,
        )
        return ModelResult(
            value=value,
            usage=Usage(input_tokens=40, output_tokens=8, cached_tokens=0),
            model="fake",
        )


class RetryingStubModelClient:
    """재시도로 살아난 성공을 흉내낸다 — 2차 body를 실어 올린다(어댑터 계약, #80)."""

    RETRY_BODY = "후보별 본문\n\n[스키마 오류] 재시도가 실제로 보낸 것"

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201, ARG002
        value = Candidate(
            candidate_id="cand_0001",
            run_id=RUN_ID,
            artifact_id="art_0001",
            location=Location(file="parser.c"),
            score=0.7,
            state=FindingState.CANDIDATE,
        )
        return ModelResult(
            value=value,
            usage=Usage(input_tokens=40, output_tokens=8, cached_tokens=0),
            model="fake",
            attempts=2,
            retry_bodies=(self.RETRY_BODY,),
        )


def _context(
    *, prompts: PromptLog | None, fail: bool = False, models: object | None = None
) -> RunContext:
    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    return RunContext(
        run_id=RUN_ID,
        config=config,
        models=models if models is not None else StubModelClient(fail=fail),
        retriever=None,
        tools=None,
        store=InMemoryStateStore(),
        ids=IdFactory(RUN_ID),
        trace=trace,
        ledger=BudgetLedger(Budget(input_tokens=1000, output_tokens=1000, gpu_seconds=0), trace),
        policy=FixedThresholdPolicy(config.thresholds),
        monotonic_ms=lambda: 0,
        prompts=prompts,
    )


def _model_calls(ctx: RunContext) -> list[dict]:
    return [event.data for event in ctx.trace.events if event.kind is EventKind.MODEL_CALL]


def test_unwired_context_leaves_model_call_untouched() -> None:
    ctx = _context(prompts=None)

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    data = _model_calls(ctx)[-1]
    assert "prompt_ref" not in data
    assert "prompt_sha256" not in data


def test_wired_context_points_model_call_at_the_file(tmp_path: Path) -> None:
    ctx = _context(prompts=PromptLog(tmp_path))

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    data = _model_calls(ctx)[-1]
    assert data["prompt_ref"] == f"{DIR_NAME}/0001_scout.md"
    written = (tmp_path / data["prompt_ref"]).read_text(encoding="utf-8")
    assert data["prompt_sha256"] == sha256_text(written)


def test_failed_call_still_leaves_the_prompt(tmp_path: Path) -> None:
    """실패 분기가 "스키마 파싱 실패율"의 분자다. 입력이 없으면 조사할 수 없다."""
    ctx = _context(prompts=PromptLog(tmp_path), fail=True)

    with pytest.raises(RuntimeError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    data = _model_calls(ctx)[-1]
    assert data["ok"] is False
    assert (tmp_path / data["prompt_ref"]).is_file()


def test_retry_call_persists_the_actual_sent_request(tmp_path: Path) -> None:
    """재시도로 살아난 성공은 실제로 간 2차 요청을 파일로 남기고, `sent_sha256`으로 가리킨다.

    `prompt_sha256`(1차)만 남으면 실제 서버에 간 것과 다른 것을 가리켜 오도가 된다(D49·#80).
    """
    ctx = _context(prompts=PromptLog(tmp_path), models=RetryingStubModelClient())

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    data = _model_calls(ctx)[-1]
    # 1차 파일과 재시도 파일이 한 순번으로 남는다.
    assert data["prompt_ref"] == f"{DIR_NAME}/0001_scout.md"
    retry_path = tmp_path / DIR_NAME / "0001_scout_retry1.md"
    assert retry_path.is_file()
    # 재시도 파일이 실제로 간 body를 담는다.
    assert RetryingStubModelClient.RETRY_BODY in retry_path.read_text(encoding="utf-8")
    # `sent_sha256`이 그 실제 요청을 가리키고, 1차 해시와 다르다 — 오도가 사라진다.
    assert data["sent_sha256"] == sha256_text(retry_path.read_text(encoding="utf-8"))
    assert data["sent_sha256"] != data["prompt_sha256"]


def test_non_retry_call_adds_no_sent_hash(tmp_path: Path) -> None:
    """재시도가 없으면 `prompt_sha256`이 곧 실제 간 것이라 `sent_sha256` 키를 늘리지 않는다."""
    ctx = _context(prompts=PromptLog(tmp_path))

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    data = _model_calls(ctx)[-1]
    assert "sent_sha256" not in data
    assert not (tmp_path / DIR_NAME / "0001_scout_retry1.md").exists()


def test_budget_exhaustion_leaves_no_orphan_file(tmp_path: Path) -> None:
    """막힌 호출에는 대응하는 `model_call`이 없다. 파일만 남으면 개수가 어긋난다."""
    ctx = _context(prompts=PromptLog(tmp_path))
    ctx.ledger.charge(
        Usage(input_tokens=1000, output_tokens=1000, cached_tokens=0), stage=Stage.SCOUT
    )

    with pytest.raises(Exception):  # noqa: B017 - 예외 종류는 test_budget이 본다
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    assert not (tmp_path / DIR_NAME).exists()


# --- 파이프라인 ----------------------------------------------------------------


def _run(tmp_path: Path, **overrides: object):  # noqa: ANN202
    config = load_config("dev_fake")
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir(parents=True)
    spec = RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT, **overrides)
    trace_path = run_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as stream:
        cli.execute(config, spec, run_id=run_dir.name, run_dir=run_dir, trace_stream=stream)
    return run_dir, trace_path


def _traced_model_calls(trace_path: Path) -> list[dict]:
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    return [event["data"] for event in events if event["kind"] == EventKind.MODEL_CALL.value]


def test_pipeline_writes_one_prompt_per_model_call(tmp_path: Path) -> None:
    run_dir, trace_path = _run(tmp_path)

    calls = _traced_model_calls(trace_path)
    files = sorted((run_dir / DIR_NAME).glob("*.md"))
    assert calls, "모델 호출이 없으면 이 테스트가 아무것도 보지 않는다"
    assert len(files) == len(calls)


def test_every_traced_ref_resolves_under_the_run_dir(tmp_path: Path) -> None:
    run_dir, trace_path = _run(tmp_path)

    for data in _traced_model_calls(trace_path):
        path = run_dir / data["prompt_ref"]
        assert path.is_file()
        assert data["prompt_sha256"] == sha256_text(path.read_text(encoding="utf-8"))


def test_dry_run_leaves_no_prompt_artifacts(tmp_path: Path) -> None:
    """`write_manifest`와 같은 가드다 — 매니페스트가 없는 run에 프롬프트만 남지 않는다."""
    run_dir, _ = _run(tmp_path, dry_run=True)

    assert not (run_dir / DIR_NAME).exists()
