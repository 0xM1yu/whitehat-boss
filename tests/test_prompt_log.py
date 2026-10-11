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
from src.interfaces.model_client import ChatTurn, ModelResult, Prompt, SchemaParseError, Usage
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


# --- hop (ADR 0012 결정 2 — need_evidence 되묻기의 후속 턴) --------------------


def test_hop_greater_than_zero_gets_a_hop_suffixed_filename(tmp_path: Path) -> None:
    """hop=0(기본값)은 지금과 같은 파일명이다. hop>0이면 `_hop{k}`가 붙는다."""
    log = PromptLog(tmp_path)
    followup = Prompt(prefix=PROMPT.prefix, body="후속 질문", prompt_version="v1")

    hop0_ref, _ = log.write(role="scout", stage=4, prompt=PROMPT)
    hop1_ref, _ = log.write(role="scout", stage=4, prompt=followup, hop=1)

    assert hop0_ref == f"{DIR_NAME}/0001_scout.md"
    assert hop1_ref == f"{DIR_NAME}/0002_scout_hop1.md"


def test_hop_retry_suffix_comes_after_the_hop_suffix(tmp_path: Path) -> None:
    """`_retryK`는 `_hop{k}` 뒤에 붙는다 — 1차·재시도가 한 순번으로 묶이는 것과 같은 자리다."""
    log = PromptLog(tmp_path)
    followup = Prompt(prefix=PROMPT.prefix, body="후속 질문", prompt_version="v1")

    log.write(role="scout", stage=4, prompt=PROMPT)
    log.write(role="scout", stage=4, prompt=followup, hop=1)
    retry_ref, _ = log.write_retry(role="scout", stage=4, prompt=followup, index=1, hop=1)

    assert retry_ref == f"{DIR_NAME}/0002_scout_hop1_retry1.md"


def test_a_hop_log_hashes_the_same_whatever_its_sequence(tmp_path: Path) -> None:
    """후속 턴 로그도 **순번을 담지 않는다** — `render()`의 불변식이 hop에서도 선다.

    hop0 참조(`prompts/0001_…`)를 본문에 실으면 그 해시가 실행 순서를 따라간다 —
    같은 요청이 per_stage와 per_candidate에서 다른 `prompt_sha256`을 받는다. 홉 사슬은
    `model_call`의 (`subject_id`, `role`, `hop`)이 이미 든다.
    """
    followup = Prompt(prefix=PROMPT.prefix, body="후속 질문", prompt_version="v1")
    history = (ChatTurn(speaker="user", content=PROMPT.body),)
    other = Prompt(prefix=PROMPT.prefix, body="다른 후보의 hop0", prompt_version="v1")

    def hop1_call(ctx: RunContext) -> dict:
        ctx.call_model(
            role=Role.SCOUT, prompt=followup, schema=Candidate, stage=Stage.SCOUT,
            history=history, hop=1,
        )
        return _model_calls(ctx)[-1]

    early = _context(prompts=PromptLog(tmp_path / "early"))
    early.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)
    late = _context(prompts=PromptLog(tmp_path / "late"))
    late.call_model(role=Role.SCOUT, prompt=other, schema=Candidate, stage=Stage.SCOUT)
    late.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    early_call, late_call = hop1_call(early), hop1_call(late)

    assert early_call["prompt_ref"] != late_call["prompt_ref"]
    assert early_call["prompt_sha256"] == late_call["prompt_sha256"]



def test_a_hop_log_carries_the_history_it_actually_sent(tmp_path: Path) -> None:
    """hop>0에 실제로 간 요청은 `[system]+history+[이번 turn]`이다 — 로그 파일에 history가
    없으면 `eval/leakage`가 그 바이트를 못 보고 #80의 「그 파일이 곧 실제 요청」이 거짓이 된다
    (#306 리뷰). history 내용은 순번을 안 들이므로 위 해시 불변식과 양립한다."""
    followup = Prompt(prefix=PROMPT.prefix, body="후속 질문", prompt_version="v1")
    history = (
        ChatTurn(speaker="user", content="hop0 본문 — 유출 탐지 대상"),
        ChatTurn(speaker="assistant", content='{"hop0": "응답 원문"}'),
    )
    log = PromptLog(tmp_path)

    ref, digest = log.write(role="scout", stage=4, prompt=followup, hop=1, history=history)
    retry, _ = log.write_retry(
        role="scout", stage=4, prompt=followup, index=1, hop=1, history=history
    )

    for path in (ref, retry):
        text = (tmp_path / path).read_text(encoding="utf-8")
        assert text.index(PROMPT.prefix) < text.index("hop0 본문 — 유출 탐지 대상")
        assert text.index('{"hop0": "응답 원문"}') < text.index("후속 질문")
        assert "speaker=assistant" in text
    assert digest == sha256_text((tmp_path / ref).read_text(encoding="utf-8"))
    assert render(role="scout", stage=4, prompt=PROMPT) == render(
        role="scout", stage=4, prompt=PROMPT, history=()
    )


def test_run_context_passes_history_into_the_hop_log(tmp_path: Path) -> None:
    """배선 — `call_model(history=…)`가 그 history를 로그 파일까지 넘긴다."""
    ctx = _context(prompts=PromptLog(tmp_path))
    followup = Prompt(prefix=PROMPT.prefix, body="후속 질문", prompt_version="v1")
    history = (ChatTurn(speaker="user", content="hop0 본문 — 배선"),)

    ctx.call_model(
        role=Role.SCOUT, prompt=followup, schema=Candidate, stage=Stage.SCOUT,
        history=history, hop=1,
    )

    ref = _model_calls(ctx)[-1]["prompt_ref"]
    assert "hop0 본문 — 배선" in (tmp_path / ref).read_text(encoding="utf-8")

# --- RunContext 배선 ----------------------------------------------------------


class StubModelClient:
    """정해진 값만 돌려주는 ModelClient. 어댑터 동작이 아니라 래퍼 계약을 본다."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
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

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201, ARG002
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


def test_call_model_threads_hop_into_the_model_call_event_and_the_log_name(
    tmp_path: Path,
) -> None:
    """`hop`은 `model_call`에 실리고, 그 홉의 1차 프롬프트 파일명에도 반영된다."""
    ctx = _context(prompts=PromptLog(tmp_path))

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)
    ctx.call_model(
        role=Role.SCOUT,
        prompt=PROMPT,
        schema=Candidate,
        stage=Stage.SCOUT,
        history=(ChatTurn(speaker="user", content="이전 턴"),),
        hop=1,
    )

    calls = _model_calls(ctx)
    assert calls[0]["hop"] == 0
    assert calls[1]["hop"] == 1
    assert calls[1]["prompt_ref"] == f"{DIR_NAME}/0002_scout_hop1.md"


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


class _RetriedThenFailedClient:
    """재시도를 보낸 뒤 소진돼 실패한다 — 소비(`usage`·`gpu_ms`)와 재시도 본문을 예외에 싣는다."""

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201, ARG002
        raise SchemaParseError(
            "3회 안에 스키마를 만들지 못했다",
            usage=Usage(input_tokens=300, output_tokens=30, cached_tokens=0),
            gpu_ms=243_000,
            gpu_ms_source="request_wall_clock",
            attempts=2,
            retry_bodies=("재시도가 실제로 보낸 것",),
        )


def test_a_failed_retry_record_write_keeps_the_charge_the_event_and_the_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D66 — 실패 경로에서 재시도 본문 쓰기가 터져도 ① 이미 쓴 소비가 청구되고 ② 실패
    `model_call` 이벤트가 남고 ③ **원 예외**가 올라온다. 디스크 full 대신 쓰기를 OSError로
    바꿔 세운다. 수정 전에는 OSError가 원 `SchemaParseError`를 덮고 청구·이벤트가 둘 다 없었다."""
    prompts = PromptLog(tmp_path)

    def disk_full(*_a: object, **_k: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(prompts, "write_retry", disk_full)
    ctx = _context(prompts=prompts, models=_RetriedThenFailedClient())

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    assert ctx.ledger.input_tokens == 300 and ctx.ledger.gpu_ms == 243_000
    (call,) = _model_calls(ctx)
    assert call["ok"] is False and call["error"] == "SchemaParseError"
    assert call["retry_record_error"] == "OSError(errno=28)"


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


def test_pipeline_writes_one_primary_prompt_per_model_call(tmp_path: Path) -> None:
    """**1차 파일**이 호출당 하나다.

    재시도가 있으면 `_retryK` 파일이 같은 순번으로 더 붙으므로(#80) 그것을 빼고 센다 —
    dev_fake는 재시도가 없어 실제로는 파일 수와 호출 수가 같지만, 불변식은 "호출당 파일
    하나"가 아니라 "호출당 **1차** 파일 하나"다(재시도 파일은 별개).
    """
    run_dir, trace_path = _run(tmp_path)

    calls = _traced_model_calls(trace_path)
    primary = sorted(
        p for p in (run_dir / DIR_NAME).glob("*.md") if "_retry" not in p.name
    )
    assert calls, "모델 호출이 없으면 이 테스트가 아무것도 보지 않는다"
    assert len(primary) == len(calls)


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


# --- 실패한 응답 원문 (#603) --------------------------------------------------


def test_a_failed_response_lands_beside_its_prompt_with_the_same_sequence_number(
    tmp_path: Path,
) -> None:
    """응답 파일은 직전 `write`와 같은 순번을 쓰고 카운터를 올리지 않는다. LF·바이트 동일."""
    log = PromptLog(tmp_path)
    raw0, raw1 = "첫 응답\r\n원문", '{"a": 1}'

    log.write(role="pov", stage=8, prompt=PROMPT)
    log.write(role="pov", stage=8, prompt=PROMPT, hop=1)
    ref0, digest0 = log.write_response(role="pov", stage=8, index=0, hop=1, text=raw0)
    ref1, digest1 = log.write_response(role="pov", stage=8, index=1, hop=1, text=raw1)
    nxt, _ = log.write(role="auditor", stage=5, prompt=PROMPT)

    assert ref0 == f"{DIR_NAME}/0002_pov_hop1_response.txt"
    assert ref1 == f"{DIR_NAME}/0002_pov_hop1_response_retry1.txt"
    assert nxt == f"{DIR_NAME}/0003_auditor.md"  # 응답이 순번을 안 먹었다
    assert (tmp_path / ref0).read_bytes() == raw0.encode("utf-8")  # 바이트 그대로(\r 보존)
    assert (tmp_path / ref1).read_bytes() == raw1.encode("utf-8")
    assert digest0 == sha256_text(raw0) and digest1 == sha256_text(raw1)
    assert b"\r\n" in (tmp_path / ref0).read_bytes()  # 응답 안의 \r\n은 건드리지 않는다


class _ExhaustedWithRawClient:
    """스키마 소진 — 두 시도의 원문을 예외에 싣는다."""

    RAWS = ('{"action": "answer"}', '{"action": "answer", "x": 1}\n')

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201, ARG002
        raise SchemaParseError(
            "소진",
            usage=Usage(input_tokens=300, output_tokens=30, cached_tokens=0),
            gpu_ms=1000,
            gpu_ms_source="request_wall_clock",
            attempts=2,
            retry_bodies=("재시도 본문",),
            raw_texts=self.RAWS,
        )


def test_call_model_writes_the_failed_responses_and_the_trace_points_at_them(
    tmp_path: Path,
) -> None:
    """실패 호출: 응답 파일이 원문 그대로 남고 `model_call`이 `response_refs`·sha로 가리킨다.
    성공 호출에는 파일도 필드도 없다."""
    ctx = _context(prompts=PromptLog(tmp_path), models=_ExhaustedWithRawClient())

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = _model_calls(ctx)
    refs = json.loads(call["response_refs"])  # JSON 배열 문자열 — `TraceValue`가 스칼라다
    assert refs == [
        f"{DIR_NAME}/0001_scout_response.txt",
        f"{DIR_NAME}/0001_scout_response_retry1.txt",
    ]
    for ref, raw in zip(refs, _ExhaustedWithRawClient.RAWS, strict=True):
        assert (tmp_path / ref).read_bytes() == raw.encode("utf-8")
    assert json.loads(call["response_sha256"]) == [
        sha256_text(r) for r in _ExhaustedWithRawClient.RAWS
    ]

    ok_ctx = _context(prompts=PromptLog(tmp_path / "ok"))
    ok_ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)
    (ok,) = _model_calls(ok_ctx)
    assert "response_refs" not in ok and "response_sha256" not in ok
    assert not list((tmp_path / "ok").rglob("*_response*"))


def test_a_failed_response_write_keeps_the_original_error_and_lands_as_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """응답 쓰기가 OSError여도 원 예외·청구·이벤트는 살고 사유가 `response_record_error`다."""
    prompts = PromptLog(tmp_path)

    def disk_full(*_a: object, **_k: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(prompts, "write_response", disk_full)
    ctx = _context(prompts=prompts, models=_ExhaustedWithRawClient())

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    assert ctx.ledger.input_tokens == 300
    (call,) = _model_calls(ctx)
    assert call["ok"] is False
    assert call["response_record_error"] == "OSError(errno=28)"
    assert "response_refs" not in call


# --- 실패 응답 기록의 경계 (#603 라운드 2) -----------------------------------


class _RaisingClient:
    """대본의 예외를 그대로 던진다 — 소비(`usage`)는 예외가 들고 있다."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201, ARG002
        raise self._exc


def _exhausted(raw_texts: tuple[str, ...]) -> SchemaParseError:
    return SchemaParseError(
        "소진",
        usage=Usage(input_tokens=300, output_tokens=30, cached_tokens=0),
        gpu_ms=1000,
        gpu_ms_source="request_wall_clock",
        attempts=max(len(raw_texts), 1),
        raw_texts=raw_texts,
    )


def _call_failing(ctx: RunContext, *, hop: int = 0) -> None:
    with pytest.raises(SchemaParseError):
        ctx.call_model(
            role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT, hop=hop
        )


def test_failed_response_files_carry_the_hop_in_their_names(tmp_path: Path) -> None:
    ctx = _context(prompts=PromptLog(tmp_path), models=_RaisingClient(_exhausted(("a", "b"))))

    _call_failing(ctx, hop=3)

    (call,) = _model_calls(ctx)
    assert json.loads(call["response_refs"]) == [
        f"{DIR_NAME}/0001_scout_hop3_response.txt",
        f"{DIR_NAME}/0001_scout_hop3_response_retry1.txt",
    ]


def test_a_lone_surrogate_in_a_response_is_written_and_keeps_the_original_error(
    tmp_path: Path,
) -> None:
    """모델 JSON의 외톨이 서로게이트가 쓰기를 터뜨려 원 예외·청구를 가리면 안 된다."""
    raw = "앞" + chr(0xD800) + "뒤"  # 리터럴 이스케이프는 pytest 재작성이 못 쓴다
    ctx = _context(prompts=PromptLog(tmp_path), models=_RaisingClient(_exhausted((raw,))))

    _call_failing(ctx)

    (call,) = _model_calls(ctx)
    (ref,) = json.loads(call["response_refs"])
    assert (tmp_path / ref).read_bytes() == raw.encode("utf-8", errors="surrogatepass")
    assert "response_record_error" not in call
    assert ctx.ledger.input_tokens == 300


def test_no_raw_text_means_no_response_key_and_no_file(tmp_path: Path) -> None:
    ctx = _context(prompts=PromptLog(tmp_path), models=_RaisingClient(_exhausted(())))

    _call_failing(ctx)

    (call,) = _model_calls(ctx)
    assert "response_refs" not in call and "response_sha256" not in call
    assert "response_record_error" not in call
    assert not list(tmp_path.rglob("*_response*"))


def test_without_a_prompt_log_the_original_error_propagates_and_no_response_key() -> None:
    ctx = _context(prompts=None, models=_RaisingClient(_exhausted(("a",))))

    _call_failing(ctx)

    (call,) = _model_calls(ctx)
    assert "response_refs" not in call and "response_record_error" not in call
    assert ctx.ledger.input_tokens == 300


def test_any_error_while_recording_responses_keeps_the_original_error_and_the_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError만이 아니다 — 어떤 예외든 사유로 남기고 원 예외·청구·이벤트는 산다."""
    prompts = PromptLog(tmp_path)

    def boom(*_a: object, **_k: object) -> None:
        raise AttributeError("x")

    monkeypatch.setattr(prompts, "write_response", boom)
    ctx = _context(prompts=prompts, models=_RaisingClient(_exhausted(("a",))))

    _call_failing(ctx)

    (call,) = _model_calls(ctx)
    assert call["response_record_error"] == "AttributeError"
    assert ctx.ledger.input_tokens == 300


def _scripted_adapter(monkeypatch: pytest.MonkeyPatch, replies: list[object]):  # noqa: ANN202
    """실 어댑터(`complete`)를 대본 응답으로 돌린다 — `raw_texts`가 예외에 실리는 길까지 잰다."""
    from src.adapters.openai_compat import OpenAICompatModelClient, PostedResponse
    from src.interfaces.model_client import ModelGatewayError

    queue = list(replies)

    def fake_post(self, payload: dict):  # noqa: ANN001, ANN202, ARG001
        reply = queue.pop(0)
        if reply == "gateway":
            raise ModelGatewayError("거절", status=400, detail="x")
        usage = Usage(input_tokens=5, output_tokens=5, cached_tokens=0)
        return PostedResponse(
            content=reply, usage=usage, finish_reason="stop", output_tokens_reported=True
        )

    monkeypatch.setattr(OpenAICompatModelClient, "_post", fake_post)
    return OpenAICompatModelClient(
        endpoint="http://127.0.0.1:9/v1", model_name="t", temperature=0.0, top_p=1.0,
        max_tokens=64, seed=1, retries=1,
    )


def test_a_null_content_attempt_does_not_mask_the_schema_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1차 `content=None`·2차 검증 실패 — 원 `SchemaParseError`·이벤트 하나·청구가 산다.
    기록은 두 칸이고 1차 파일은 비어 있다."""
    client = _scripted_adapter(monkeypatch, [None, '{"x": 1}'])
    ctx = _context(prompts=PromptLog(tmp_path), models=client)

    _call_failing(ctx)

    (call,) = _model_calls(ctx)  # 정확히 하나
    refs = json.loads(call["response_refs"])
    assert len(refs) == 2
    assert (tmp_path / refs[0]).read_bytes() == b""
    assert (tmp_path / refs[1]).read_bytes() == b'{"x": 1}'
    assert ctx.ledger.input_tokens == 10


def test_a_gateway_error_after_a_schema_failure_still_leaves_the_first_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1차 스키마 실패·2차 게이트웨이 거절 — 1차 원문 하나가 남는다. 1차 거절이면 안 남는다."""
    from src.interfaces.model_client import ModelGatewayError

    ctx = _context(
        prompts=PromptLog(tmp_path), models=_scripted_adapter(monkeypatch, ['{"x": 1}', "gateway"])
    )
    with pytest.raises(ModelGatewayError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)
    (call,) = _model_calls(ctx)
    (ref,) = json.loads(call["response_refs"])
    assert (tmp_path / ref).read_bytes() == b'{"x": 1}'

    other = tmp_path / "first"
    ctx2 = _context(
        prompts=PromptLog(other), models=_scripted_adapter(monkeypatch, ["gateway"])
    )
    with pytest.raises(ModelGatewayError):
        ctx2.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)
    (call2,) = _model_calls(ctx2)
    assert "response_refs" not in call2
    assert not list(other.rglob("*_response*"))
