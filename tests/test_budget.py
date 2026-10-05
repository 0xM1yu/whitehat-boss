"""예산 원장과 RunContext 래퍼 테스트.

**토큰 수를 단언하지 않는다.** 여기서 고정하는 것은 "모델 호출 한 번이 usage 기록 +
예산 차감 + trace를 한꺼번에 만든다"는 계약과, 소진 시 예외가 나온다는 것뿐이다.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from src.adapters.memory_store import InMemoryStateStore
from src.config import CONFIG_DIR, ConfigError, load_config
from src.ids import IdFactory
from src.interfaces.model_client import ModelResult, Prompt, SchemaParseError, Usage
from src.interfaces.retrieval import RetrievalResult
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import Stage
from src.orchestrator.trace import VOLATILE_FIELDS, EventKind, TraceWriter
from src.schemas import Candidate, Decision, FindingState, Location, ProducedBy, Role, Verdict
from src.schemas.finding import Finding
from src.schemas.run import Budget
from tests.conftest import ALL_PROFILES

RUN_ID = "run_20260901_0001"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
PROMPT = Prompt(prefix="공통 prefix", body="본문", prompt_version="v1")


class StubModelClient:
    """usage만 정해 놓은 ModelClient. 어댑터 동작이 아니라 래퍼 계약을 본다."""

    def __init__(self, *, usage: Usage, fail: bool = False) -> None:
        self._usage = usage
        self._fail = fail
        self.calls = 0

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.calls += 1
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
        return ModelResult(value=value, usage=self._usage, model="fake")


def _usage(input_tokens: int = 40, cached: int = 30) -> Usage:
    return Usage(input_tokens=input_tokens, output_tokens=8, cached_tokens=cached)


def _budget(input_tokens: int = 1000) -> Budget:
    return Budget(input_tokens=input_tokens, output_tokens=1000, gpu_seconds=0)


def _context(
    *, budget: Budget, usage: Usage, fail: bool = False
) -> tuple[RunContext, StubModelClient]:
    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    models = StubModelClient(usage=usage, fail=fail)
    ctx = RunContext(
        run_id=RUN_ID,
        config=config,
        models=models,
        retriever=None,
        tools=None,
        store=InMemoryStateStore(),
        ids=IdFactory(RUN_ID),
        trace=trace,
        ledger=BudgetLedger(budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        monotonic_ms=lambda: 0,
    )
    return ctx, models


def test_charge_accumulates_and_leaves_room() -> None:
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    ledger = BudgetLedger(_budget(), trace)

    ledger.charge(_usage(input_tokens=10, cached=4), stage=Stage.SCOUT)

    assert ledger.remaining()["input_tokens"] < _budget().input_tokens
    assert not ledger.is_exhausted
    assert trace.events[-1].data["event"] == "charge"


def test_zero_limit_is_not_treated_as_exhausted() -> None:
    """`dev_fake`의 `gpu_seconds: 0`은 "GPU를 안 쓴다"는 뜻이지 "0초만 허용"이 아니다."""
    ledger = BudgetLedger(_budget(), TraceWriter(RUN_ID, clock=lambda: NOW))

    ledger.ensure(Stage.SCOUT)  # 예외 없음


# --- 상한 0은 Fake의 뜻이고, 실서빙에서는 로드가 막는다 ----------------------------------------
#
# 위 테스트가 지키는 뜻 — 상한 0은 「검사하지 않는다」 — 은 Fake에게 맞다. 그런데 이 저장소에서
# `0`은 보통 「안 쟀다」이고(CLAUDE.md §0), 같은 `0`이 **실서빙 프로필의 예산**에 적히면 임대 GPU가
# 한도 없이 돈다. 원장은 자기 모델이 실서빙인지 모르므로(`BudgetLedger(budget, trace)`) 막는 자리는
# `load_config`다. 실서빙 판정은 프로필의 `production`이 아니라 `served_models()`가 한다.

_LIMITS = ("input_tokens", "output_tokens", "gpu_seconds", "tool_seconds")


def _config_dir_with_budget(
    dest: Path, *, profile: str, budget: str, limits: dict[str, int | None]
) -> Path:
    """저장소 config를 `dest`로 복사해 `profile`이 `budget` 예산을 쓰게 하고 상한을 덮는다.

    값이 `None`이면 키를 **뺀다** — `tool_seconds`는 안 적으면 0이 되는 유일한 키다(`Budget`).
    """
    shutil.copy(CONFIG_DIR / "models.yaml", dest / "models.yaml")
    raw_budgets = yaml.safe_load((CONFIG_DIR / "budgets.yaml").read_text("utf-8"))
    block = raw_budgets["budgets"][budget]
    for key, value in limits.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value
    (dest / "budgets.yaml").write_text(yaml.safe_dump(raw_budgets, allow_unicode=True), "utf-8")
    raw_profiles = yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text("utf-8"))
    raw_profiles["profiles"][profile]["budget"] = budget
    (dest / "profiles.yaml").write_text(yaml.safe_dump(raw_profiles, allow_unicode=True), "utf-8")
    return dest


@pytest.mark.parametrize("item", _LIMITS)
def test_a_real_serving_profile_refuses_a_zero_limit(tmp_path: Path, item: str) -> None:
    """실모델을 서빙하는 프로필은 상한 넷 중 하나라도 0이면 **로드가** 실패한다.

    네 키를 하나씩 0으로 만든다 — 한 키만 막는 구현(`gpu_seconds`만 본다든지)이 빠지면 나머지
    셋이 빨개진다. 실패해야 하는 자리는 run 중이 아니라 로드다: 돈이 나가기 시작한 뒤에는 늦다.
    """
    config_dir = _config_dir_with_budget(
        tmp_path, profile="harness_pool", budget="default", limits={item: 0}
    )

    with pytest.raises(ConfigError, match=item):
        load_config("harness_pool", config_dir=config_dir)


def test_a_real_serving_budget_that_omits_tool_seconds_is_refused(tmp_path: Path) -> None:
    """`tool_seconds`를 **안 적은** 예산도 0이다 — 빈칸이 곧 한도 없음이 되는 유일한 키.

    이 키가 선택값인 것은 옛 manifest 때문이다(`Budget.tool_seconds`). 실서빙 프로필의 예산을
    새로 만들면서 이 줄을 빠뜨리는 것이 0을 직접 쓰는 것보다 있을 법한 실수다.
    """
    config_dir = _config_dir_with_budget(
        tmp_path, profile="harness_pool", budget="default", limits={"tool_seconds": None}
    )

    with pytest.raises(ConfigError, match="tool_seconds"):
        load_config("harness_pool", config_dir=config_dir)


def test_a_real_serving_profile_cannot_borrow_the_fake_budget(tmp_path: Path) -> None:
    """실서빙 프로필이 `dev_fake` 예산을 가리키면 로드가 실패한다 — 이름 하나 잘못 적는 실수다.

    `dev_fake`의 0은 「Fake는 GPU를 안 쓴다」는 뜻으로 거기 있다. 그 예산을 실서빙 프로필이 쓰면
    같은 0이 「한도 없음」이 된다. 에러가 0인 키를 **전부** 말해야 고칠 자리가 보인다 — 키 이름은
    에러의 안내 문장에도 나오므로 개별 이름이 아니라 **0인 키 목록**을 본다(키 하나만 거르는 가드가
    이 검사를 지나가던 것을 변이로 확인했다).
    """
    config_dir = _config_dir_with_budget(
        tmp_path, profile="harness_pool", budget="dev_fake", limits={}
    )

    with pytest.raises(ConfigError) as raised:
        load_config("harness_pool", config_dir=config_dir)

    # ⏵ `dev_fake`의 토큰 둘도 0(= 상한 없음)으로 내려가 **넷이 전부** 0이다. 목록을 그대로
    # 단언한다 — 키 하나만 거르는 가드가 이 검사를 지나가던 것을 변이로 확인했으므로
    # 「개별 이름이 아니라 목록을 본다」는 성질은 그대로 유지한다.
    assert "['gpu_seconds', 'input_tokens', 'output_tokens', 'tool_seconds']" in str(raised.value)


def test_a_one_second_limit_on_a_real_profile_loads_and_still_stops_the_run(
    tmp_path: Path,
) -> None:
    """막는 것은 0이지 작은 값이 아니다 — 양수 중 가장 작은 1이 그대로 로드되고 그대로 막는다.

    가드가 숫자 하한을 몰래 들이지 않았다는 핀이기도 하다: 이 변경이 정하는 값은 없다.
    """
    config_dir = _config_dir_with_budget(
        tmp_path, profile="harness_pool", budget="default", limits=dict.fromkeys(_LIMITS, 1)
    )
    config = load_config("harness_pool", config_dir=config_dir)
    assert config.served_models(), "이 검사는 실서빙 프로필 위에서 돌아야 한다"
    ledger = BudgetLedger(config.budget, TraceWriter(RUN_ID, clock=lambda: NOW))

    ledger.charge(
        Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=Stage.SCOUT, gpu_ms=1000
    )

    with pytest.raises(BudgetExhausted) as raised:
        ledger.ensure(Stage.AUDITOR)
    assert raised.value.item == "gpu_seconds"


def test_a_fake_only_profile_may_keep_zero_limits(tmp_path: Path) -> None:
    """Fake 전용 프로필의 0은 그대로 로드된다 — 막는 범위가 실서빙에 한정된다는 핀.

    값을 저장소 `dev_fake`에서 읽지 않고 직접 0으로 세운다 — 저장소 값이 바뀌어도 이 경계는
    같아야 하기 때문이다.
    """
    config_dir = _config_dir_with_budget(
        tmp_path, profile="dev_fake", budget="dev_fake", limits=dict.fromkeys(_LIMITS, 0)
    )

    config = load_config("dev_fake", config_dir=config_dir)

    assert config.served_models() == {}
    assert set(config.budget.model_dump().values()) == {0}


def test_every_shipped_profile_that_serves_a_real_model_declares_four_positive_limits() -> None:
    """저장소가 싣고 있는 실서빙 프로필은 예산 상한 넷이 전부 양수다 — 0이 들어오면 여기서 빨개진다.

    위 가드가 실패시키는 것은 **그 프로필을 로드하는 테스트**인데, 가드가 약해지면 그 테스트도
    같이 초록이 된다. 이 검사는 로드된 값을 직접 읽으므로 가드와 따로 선다.

    비어 있는 훑기는 초록이다 — 실서빙 프로필이 하나도 안 보이면(`served_models()`의 뜻이 바뀌었든
    프로필이 다 지워졌든) 아무것도 안 본 채 통과하지 않고 여기서 멈춘다.
    """
    configs = {profile: load_config(profile) for profile in ALL_PROFILES}
    real = {name: config for name, config in configs.items() if config.served_models()}
    assert real, "실서빙 프로필이 하나도 없다 — 이 훑기가 아무것도 보지 않았다"

    zero_limits = {
        name: sorted(item for item, limit in config.budget.model_dump().items() if limit == 0)
        for name, config in real.items()
    }
    assert {name: items for name, items in zero_limits.items() if items} == {}


def test_every_shipped_profile_that_runs_real_containers_declares_a_tool_time_ceiling() -> None:
    """이슈 #436 - 실컨테이너를 도는 프로필은 도구 시간 상한이 양수다.

    `RunContext.call_tool`은 `tool_runner`가 `fake`가 아니면 도구 시간을 **청구한다** - 그런데
    `tool_seconds: 0`은 검사되지 않아(`BudgetLedger._overrun`) 상한이 없다. `dev_fake` 예산의 0은
    「Fake 런타임은 안 청구한다」는 뜻으로 거기 있고, 그 예산을 실컨테이너 프로필 셋이 빌려 쓰고
    있었다. 비어 있는 훑기는 초록이라 하나도 안 보이면 멈춘다."""
    configs = {profile: load_config(profile) for profile in ALL_PROFILES}
    containers = {
        name: config
        for name, config in configs.items()
        if config.profile.adapters.tool_runner != "fake"
    }
    assert containers, "컨테이너 프로필이 하나도 없다 - 이 훑기가 아무것도 보지 않았다"

    assert {name for name, config in containers.items() if config.budget.tool_seconds == 0} == set()


def test_a_container_profile_that_borrows_a_zero_tool_time_budget_is_refused(
    tmp_path: Path,
) -> None:
    """컨테이너 프로필이 `tool_seconds: 0` 예산을 가리키면 로드가 실패한다 - 이름 오기다.

    Fake 모델만 쓰는 프로필이라 위 가드(`served_models()`)는 이것을 못 본다. 토큰·GPU의 0은 Fake라서
    맞고, 이 가드가 무는 것은 도구 시간 하나다."""
    config_dir = _config_dir_with_budget(
        tmp_path, profile="dev_docker", budget="dev_fake", limits={"tool_seconds": 0}
    )

    with pytest.raises(ConfigError, match="tool_seconds"):
        load_config("dev_docker", config_dir=config_dir)


def test_a_fake_runtime_profile_keeps_a_zero_tool_time_limit(tmp_path: Path) -> None:
    """Fake 런타임의 도구 시간은 청구되지 않는다 - 0이 그대로 로드된다(막는 범위는 컨테이너뿐)."""
    config_dir = _config_dir_with_budget(
        tmp_path, profile="dev_fake", budget="dev_fake", limits={"tool_seconds": 0}
    )

    assert load_config("dev_fake", config_dir=config_dir).budget.tool_seconds == 0


def test_ensure_raises_after_limit_reached() -> None:
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    ledger = BudgetLedger(_budget(input_tokens=10), trace)
    ledger.charge(_usage(input_tokens=10, cached=0), stage=Stage.SCOUT)

    with pytest.raises(BudgetExhausted) as exc:
        ledger.ensure(Stage.AUDITOR)

    assert exc.value.item == "input_tokens"
    assert exc.value.stage == Stage.AUDITOR
    assert [e.data["event"] for e in trace.events] == ["exhausted", "blocked"]


def test_call_model_records_usage_and_charges_budget() -> None:
    ctx, models = _context(budget=_budget(), usage=_usage())

    result = ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is True
    assert call.data["cached_tokens"] == result.usage.cached_tokens
    assert ctx.ledger.input_tokens == result.usage.input_tokens
    assert models.calls == 1


_BOGUS = ProducedBy(role=Role.SCOUT, model="wrong", prompt_version="v0")


def _bogus_finding() -> Finding:
    return Finding(
        finding_id="find_0001",
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="t",
        cwe="CWE-787",
        state=FindingState.HYPOTHESIZED,
        confidence=0.5,
        # 기본값이 없다 — `schemas/finding.py` 참조. 이 팩토리는 `produced_by` stamp만 보므로
        # 빈 값으로 둔다(`hypothesized`라 `_promoted_states_need_backing`이 조기 반환한다).
        path=[],
        evidence_refs=[],
        produced_by=_BOGUS,
    )


def _bogus_decision() -> Decision:
    return Decision(
        decision_id="dec_0001",
        run_id=RUN_ID,
        finding_id="find_0001",
        verdict=Verdict.UPHOLD,
        rationale="r",
        produced_by=_BOGUS,
    )


class _BogusProducedByStub:
    """`produced_by`에 **틀린 값**을 넣은 산출물을 낸다 — 실서빙 모델이 자기 role·model을
    모른 채 그 필수 필드를 채우는 상황(@0xM1yu 지적)을 세운다."""

    def __init__(self, value) -> None:  # noqa: ANN001
        self._value = value

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        return ModelResult(value=self._value, usage=_usage(), model="served-30b")


@pytest.mark.parametrize(
    "make_value", [_bogus_finding, _bogus_decision], ids=["finding", "decision"]
)
def test_call_model_stamps_produced_by_over_the_model_value(make_value) -> None:  # noqa: ANN001
    """실서빙 모델이 `produced_by`에 아무 값이나 넣어도 하네스가 진실로 덮는다 (@0xM1yu 요청).

    Fake는 이미 맞게 채우므로, 모델이 **틀린** `produced_by`(role=SCOUT·model=wrong)를 낸
    경우를 세워 override를 본다. `model`은 `ModelResult.model`(실제 응답 모델)을 써야 한다.

    **`produced_by`를 가진 스키마 둘을 다 돈다.** 코드 경로는 하나지만 그 사실이 여기 안
    적혀 있으면 `Decision`이 덮이는지는 아무도 확인하지 않은 것이 된다.
    """
    value = make_value()
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _BogusProducedByStub(value)

    result = ctx.call_model(
        role=Role.AUDITOR, prompt=PROMPT, schema=type(value), stage=Stage.AUDITOR
    )

    pb = result.value.produced_by
    assert pb.role is Role.AUDITOR  # 모델이 쓴 SCOUT이 아니라 호출 역할
    assert pb.model == "served-30b"  # `ModelResult.model` — fallback까지 반영한 실제 응답 모델
    assert pb.prompt_version == PROMPT.prompt_version


def test_call_model_leaves_schemas_without_produced_by_alone() -> None:
    """`produced_by`가 없는 스키마(`Candidate`)에는 그 필드를 붙이지 않는다.

    **가드가 없어지면 조용히 틀린다.** pydantic 2.13.4에서 `produced_by` 없는 모델에
    `model_copy(update=...)`를 하면 예외도 경고도 없고, `model_dump()`에도 안 나타난다 —
    그런데 **객체에는 붙는다**. 그래서 덤프를 보는 단언은 회귀를 못 잡고 `hasattr`만 잡는다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())

    result = ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    assert "produced_by" not in Candidate.model_fields  # 전제: 이 스키마에는 그 필드가 없다
    assert not hasattr(result.value, "produced_by")


class _FixedValueStub:
    """모델이 낸 값을 그대로 돌려주는 ModelClient. 하네스가 무엇을 덮는지만 본다."""

    def __init__(self, value) -> None:  # noqa: ANN001
        self._value = value

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        return ModelResult(value=self._value, usage=_usage(), model="served-8b")


def _self_tagged_candidate() -> Candidate:
    """모델이 `scout_member`를 **스스로 채운** 후보.

    `produced_by`에서 실제로 일어난 일이다 — 스키마에 필드가 있으면 구조화 출력이
    그것을 채우려 들고, 모델은 자기가 풀의 어느 자리에서 불렸는지 모른다.
    """
    return Candidate(
        candidate_id="cand_0001",
        run_id=RUN_ID,
        artifact_id="art_0001",
        location=Location(file="parser.c"),
        score=0.7,
        state=FindingState.CANDIDATE,
        scout_member="scout_injection",
    )


def test_call_model_stamps_scout_member_over_the_model_value() -> None:
    """풀 멤버로 부른 호출의 후보는 **그 멤버**를 단다 — 모델이 쓴 값이 아니라.

    이 필드는 증거다. 모델이 채운 값이 살아남으면 공동오답 행렬은 "누가 무엇을 함께
    놓쳤나"가 아니라 "모델이 자기를 뭐라고 불렀나"의 표가 된다. 그 표가
    「배분을 옮기는 유일한 근거」(docs/research/2026-09-08-roster.md §7)이므로,
    stamp가 틀리면 모델을 Scout 자리 사이에서 옮기는 판단이 통째로 틀린다.

    **진짜 `Role.SCOUT_BOUNDARY`로 부른다.** 이 자리에는 같은 값을 든 자리표시자
    열거형이 있었는데, ADR 0010 §3의 셋이 `Role`에 들어온 뒤로는 그 자리표시자가
    **stamp 경로에서 실제 열거형을 한 번도 안 지나가게** 만들고 있었다 — 풀 멤버가
    `Role`에서 빠지거나 값이 바뀌어도 이 검사는 통과한다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _FixedValueStub(_self_tagged_candidate())

    result = ctx.call_model(
        role=Role.SCOUT_BOUNDARY, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT
    )

    assert result.value.scout_member == "scout_boundary"  # 모델이 쓴 injection이 아니다


def test_call_model_clears_scout_member_under_the_single_scout_role() -> None:
    """풀이 없는 `scout` 호출은 멤버를 **비운다** — 모델이 하나를 적어 냈어도.

    단일 scout 프로필의 후보가 멤버 태그를 달고 나가면, 풀 run과 단일 run의 후보가
    같은 행렬에 섞여 한 멤버의 오답 수가 부풀려진다. 그 run에는 그 자리가 아예
    없었다는 사실이 기록에서 사라진다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _FixedValueStub(_self_tagged_candidate())

    result = ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    assert result.value.scout_member is None


def test_call_model_leaves_schemas_without_scout_member_alone() -> None:
    """`scout_member`가 없는 스키마(`Finding`)에는 그 필드를 붙이지 않는다.

    `produced_by` 가드와 같은 이유다 — `model_copy(update=...)`는 없는 필드도 조용히
    객체에 붙이고 `model_dump()`에는 안 내보낸다. Finding이 이 태그를 달기 시작하면
    9단계 dedupe가 어느 필드를 봐야 하는지가 새 미결이 된다(ADR 0010 §3은 `Candidate`만
    정했다).
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _FixedValueStub(_bogus_finding())

    result = ctx.call_model(
        role=Role.AUDITOR, prompt=PROMPT, schema=Finding, stage=Stage.AUDITOR
    )

    assert "scout_member" not in Finding.model_fields  # 전제: 이 스키마에는 그 필드가 없다
    assert not hasattr(result.value, "scout_member")


def test_call_model_records_failure_and_reraises() -> None:
    """스키마 파싱 실패율의 분자가 여기서 나온다. 삼키면 지표가 0이 된다."""
    ctx, _ = _context(budget=_budget(), usage=_usage(), fail=True)

    with pytest.raises(RuntimeError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is False
    assert call.data["error"] == "RuntimeError"


class _FailingWithSpendStub:
    """이미 토큰·GPU를 쓴 뒤 `SchemaParseError`로 소진되는 클라이언트 (D53).

    실서빙의 `openai_compat.complete()`가 재시도를 소진하면 그때까지의 usage·gpu_ms를
    예외에 실어 올린다. 이 스텁은 그 계약을 세워 `call_model`이 실패분을 청구하는지 본다.
    """

    def __init__(self, *, usage: Usage, gpu_ms: int, attempts: int) -> None:
        self._usage = usage
        self._gpu_ms = gpu_ms
        self._attempts = attempts
        self.calls = 0

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.calls += 1
        raise SchemaParseError(
            "3회 안에 스키마를 만들지 못했다",
            usage=self._usage,
            gpu_ms=self._gpu_ms,
            gpu_ms_source="request_wall_clock",
            attempts=self._attempts,
        )


class _AttemptsStub:
    """재시도 끝에 성공한 호출. `attempts`를 실은 `ModelResult`를 낸다 (D55)."""

    def __init__(self, *, usage: Usage, attempts: int) -> None:
        self._usage = usage
        self._attempts = attempts

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        value = Candidate(
            candidate_id="cand_0001",
            run_id=RUN_ID,
            artifact_id="art_0001",
            location=Location(file="parser.c"),
            score=0.7,
            state=FindingState.CANDIDATE,
        )
        return ModelResult(value=value, usage=self._usage, model="fake", attempts=self._attempts)


def test_a_failed_call_charges_what_it_already_spent() -> None:
    """실패한 호출도 이미 쓴 토큰·GPU를 장부에 청구한다 (D53).

    실패 경로가 청구를 건너뛰면 evaluation.md §5.2의 분자(input/output token·GPU-second)가
    비고, 폭주가 잦은 조건일수록 싸 보인다 — 조건 비교에서 정확히 거꾸로 가는 편향이다.
    v2 스모크에서 `critic_failed` 3건 × 243초(run의 60.7%)가 그렇게 장부 밖으로 샜다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _FailingWithSpendStub(
        usage=Usage(input_tokens=300, output_tokens=30, cached_tokens=100),
        gpu_ms=243_000,
        attempts=2,
    )

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.CRITIC, prompt=PROMPT, schema=Candidate, stage=Stage.CRITIC)

    assert ctx.ledger.input_tokens == 300  # 실패분이 장부에 실렸다
    assert ctx.ledger.gpu_ms == 243_000
    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is False
    assert call.data["input_tokens"] == 300  # 실패 이벤트도 소비를 싣는다
    assert call.data["output_tokens"] == 30
    assert call.data["attempts"] == 2
    # 이벤트가 장부와 어긋나면 잡는다 (#82 리뷰 F11) — gpu·source·cached도 균질하게 싣는다.
    # cached(100)는 input(300)과 다른 값이라 캐시 비율 분모 오염(input엔 들어가고 cached엔
    # 안 들어감)도 이 단언이 잡는다.
    assert call.data["gpu_ms"] == 243_000
    assert call.data["gpu_ms_source"] == "request_wall_clock"
    assert call.data["cached_tokens"] == 100


def test_a_failed_calls_charge_can_exhaust_the_budget() -> None:
    """실패분 청구가 상한을 넘기면 다음 단계 진입이 막힌다 — 실패 한 건이 run 종료 시점을
    바꿀 수 있다 (#82 (a)의 의도된 귀결, 리뷰 F12).

    이 PR 전에는 실패한 호출이 장부를 안 건드려 이 전이가 없었다. `gpu_seconds=0`(검사 안 함)이
    아니라 `input_tokens` 상한으로 세워 실패분 청구가 실제로 `ensure()`를 막는지 본다.
    """
    ctx, _ = _context(budget=_budget(input_tokens=100), usage=_usage())
    ctx.models = _FailingWithSpendStub(
        usage=Usage(input_tokens=100, output_tokens=8192, cached_tokens=0),
        gpu_ms=100,
        attempts=2,
    )

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.CRITIC, prompt=PROMPT, schema=Candidate, stage=Stage.CRITIC)

    # 실패분이 input_tokens 100을 청구해 상한(100)에 닿았다 — 다음 ensure가 막는다.
    with pytest.raises(BudgetExhausted):
        ctx.ledger.ensure(Stage.AUDITOR)


def test_the_attempt_count_lands_on_a_successful_model_call() -> None:
    """재시도가 성공으로 끝나도 시도 수가 `model_call`에 남는다 (D55).

    이 값이 없으면 1차 실패 후 살아난 호출이 성공 한 건과 구별되지 않아 §5.1 실패율이 0%로
    읽힌다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _AttemptsStub(usage=_usage(), attempts=3)

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is True
    assert call.data["attempts"] == 3


def test_call_model_blocks_before_spending_when_exhausted() -> None:
    ctx, models = _context(budget=_budget(input_tokens=5), usage=_usage(input_tokens=10, cached=0))
    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    with pytest.raises(BudgetExhausted):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.AUDITOR)

    assert models.calls == 1  # 두 번째는 호출 자체가 없어야 한다


def test_stage_span_pairs_start_and_end() -> None:
    ctx, _ = _context(budget=_budget(), usage=_usage())

    with ctx.stage(Stage.SCOUT):
        pass

    kinds = [e.kind for e in ctx.trace.events]
    assert kinds == [EventKind.STAGE_START, EventKind.STAGE_END]
    assert ctx.trace.events[-1].data["ok"] is True


def test_stage_span_records_failure_without_swallowing() -> None:
    ctx, _ = _context(budget=_budget(), usage=_usage())

    with pytest.raises(ValueError), ctx.stage(Stage.SCOUT):
        raise ValueError("노드 실패")

    end = ctx.trace.events[-1]
    assert end.kind is EventKind.STAGE_END
    assert end.data["ok"] is False
    assert end.data["error"] == "ValueError"


def test_canonical_lines_drop_only_volatile_fields() -> None:
    """재현성 기준: `ts`·`wall_ms`만 빼면 두 실행의 JSONL이 바이트 동일해야 한다."""
    ctx, _ = _context(budget=_budget(), usage=_usage())
    with ctx.stage(Stage.SCOUT):
        pass

    lines = ctx.trace.canonical_lines()

    assert lines
    for field in VOLATILE_FIELDS:
        assert all(f'"{field}"' not in line for line in lines)
    assert all('"seq"' in line for line in lines)


# --- GPU 시간 회계 (2026-08-17 실측 기반) -------------------------------------

# `artifacts/run_20260813_0001/trace.jsonl`의 `tool_call.wall_ms` 11건을 그대로 옮긴 값.
# **합계 8,492 ms인데 그 run의 장부에는 3초가 남았다** — 청구 지점이 초로 내림해서
# 342~906 ms짜리 8건이 통째로 0으로 청구됐기 때문이다. 같은 도구를 같은 순서로 돈 세
# run이 3/6/8초를 남겼는데 실제 차이는 1.6배였다(장부는 2.7배).
MEASURED_TOOL_WALL_MS = (493, 1053, 342, 348, 906, 356, 367, 345, 1979, 342, 1961)

# 같은 11건을 ATOM(aarch64/podman)에서 돌린 값. **이쪽이 더 강한 단언이다** — 전부 1초
# 미만이라 예전 내림에서는 11/11이 0으로 청구됐다. 즉 그 run의 도구 시간은 장부에
# 통째로 없었다. 아키텍처가 다르면 같은 도구도 다른 시간을 쓴다는 사실도 함께 남는다.
ATOM_TOOL_WALL_MS = (299, 929, 321, 272, 932, 258, 248, 281, 335, 258, 358)


def _ledger(gpu_seconds: int = 0) -> BudgetLedger:
    budget = Budget(input_tokens=10**9, output_tokens=10**9, gpu_seconds=gpu_seconds)
    return BudgetLedger(budget, TraceWriter(RUN_ID, clock=lambda: NOW))


def test_the_ledger_keeps_every_millisecond_of_the_measured_run() -> None:
    """이 테스트가 이 결함의 재발을 막는 유일한 장치다.

    단언은 **8,492 ms**다. 3초가 아니다 — 초로 내림하는 코드가 돌아오면 여기서 걸린다.

    **`BudgetLedger.charge()`를 직접 부른다.** `RunContext.call_tool`을 거치면 Fake 런타임
    프로필에서 11건이 전부 `None`으로 청구되고, 그러면 이 테스트는 아무것도 지키지 않은 채
    통과한다. 아래 두 단언이 그 공허함을 막는다 —
    `test_pipeline_e2e.test_trace_actually_carries_the_volatile_fields`와 같은 방어다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    for wall_ms in MEASURED_TOOL_WALL_MS:
        ledger.charge(zero_usage, stage=2, gpu_ms=wall_ms)

    assert ledger.gpu_ms == sum(MEASURED_TOOL_WALL_MS) == 8492
    assert ledger.gpu_seconds == 8  # 파생값. 예전 장부의 3이 아니다
    # 공허하지 않다는 증거: 청구가 실제로 일어났고(0도 None도 아니다) 미측정으로 샌 것이
    # 하나도 없다. 이 둘이 없으면 청구 경로가 통째로 죽어도 위 단언이 통과할 수 있다.
    assert ledger.gpu_ms > 0
    assert ledger.unmeasured_gpu_calls == 0


def test_the_ledger_keeps_a_run_where_every_call_was_under_a_second() -> None:
    """ATOM(aarch64/podman) 실측 11건. **11건 전부 1초 미만이다.**

    예전 내림에서는 이 run의 도구 시간이 **통째로 0**이었다 — 8/11이 아니라 11/11이다.
    x86 케이스보다 강한 단언이라 둘 다 남긴다: 하나가 통과해도 다른 하나가 걸리는
    조합이 있어야 그물이 촘촘해진다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    for wall_ms in ATOM_TOOL_WALL_MS:
        ledger.charge(zero_usage, stage=2, gpu_ms=wall_ms)

    assert ledger.gpu_ms == sum(ATOM_TOOL_WALL_MS) == 4491
    assert ledger.gpu_seconds == 4  # 예전 장부는 0이었다
    # 공허 방지: 11건 전부 1초 미만이라는 전제 자체를 고정한다. 이 전제가 깨지면 이
    # 테스트가 "1초 미만도 청구된다"를 더는 증명하지 못한다.
    assert all(ms < 1000 for ms in ATOM_TOOL_WALL_MS)
    assert ledger.unmeasured_gpu_calls == 0


def test_a_sub_second_call_is_not_charged_as_zero() -> None:
    """342 ms 한 건. 예전 코드는 `342 // 1000 == 0`으로 통째로 버렸다."""
    ledger = _ledger()

    ledger.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, gpu_ms=342)

    assert ledger.gpu_ms == 342


def test_an_unmeasured_call_is_counted_not_added_as_zero() -> None:
    """`None`은 "GPU를 안 썼다"가 아니라 "안 쟀다"다.

    0으로 더하면 두 사실이 합쳐져 나중에 구분할 수 없다 — `repo_dirty`·`device`가 `None`을
    쓰는 것과 같은 이유다.

    **`incomplete_fields()`가 이 카운터를 읽지는 않는다.** 오래 그렇게 적혀 있었지만
    거짓이었고 실제 입력은 `RunContext.gpu_unmeasured_roles`다. 여기서 고정하는 것은
    원장이 두 사실을 섞지 않는다는 것뿐이다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    ledger.charge(zero_usage, stage=4, gpu_ms=None)
    ledger.charge(zero_usage, stage=4, gpu_ms=120)

    assert ledger.gpu_ms == 120
    assert ledger.unmeasured_gpu_calls == 1


def test_the_unmeasured_counter_mixes_model_and_retrieval_calls() -> None:
    """이 카운터를 그대로 판정에 쓰면 Fake 검색이 "미측정 GPU"로 잡힌다.

    `record_retrieval()`도 `charge()`로 들어오고 Fake embedding·reranker는 `gpu_ms=None`을
    준다. 그래서 이 값은 **모델 호출과 검색 호출을 섞어 센다** — 판정에 쓰면 멀쩡한
    `dev_*` run이 매번 비교표에서 빠진다(checksum 판정에서 Fake 역할을 빼는 것과 같은
    이유).

    이 테스트는 고칠 것을 요구하는 것이 아니라 **함정을 박제한다.** 다음 사람이 카운터를
    게이트에 연결하면 여기서 "모델만 세야 한다"는 전제를 다시 만나게 된다. 갈라야 할
    것이 무엇인지는 `unmeasured_gpu_calls` 필드 주석에 있다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    zero = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    ctx.record_retrieval("embedding", RetrievalResult(usage=zero, gpu_ms=None), candidates=3)

    assert ctx.ledger.unmeasured_gpu_calls == 1
    # 판정에 실제로 쓰이는 쪽은 검색 호출을 세지 않는다 — 모델 호출만 역할별로 담는다.
    assert ctx.gpu_unmeasured_roles == {}


def test_the_budget_event_carries_milliseconds_too() -> None:
    """초만 남기면 사후 집계가 볼 정확값이 사라지고 이 결함이 그대로 돌아온다."""
    ledger = _ledger()

    ledger.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, gpu_ms=1500)

    event = ledger._trace.events[-1]
    assert event.data["gpu_ms"] == 1500
    assert event.data["gpu_seconds"] == 1


def test_a_fake_runtime_tool_call_is_not_charged_as_time() -> None:
    """`FakeToolRunner`의 1~2 ms는 GPU도 컨테이너도 아니라 **하네스가 자기를 잰 값**이다.

    장부에 넣으면 무의미한 숫자가 비용 지표에 섞이고, 같은 입력의 두 실행이 다른 기록을
    남긴다. 예전에는 `// 1000`이 그 값을 전부 0으로 만들어 **내림이 재현성을 우연히 지켜
    주고 있었다** — 이 검사는 그 우연을 의도로 고정한다.

    판단 기준은 `doctor`가 이미 쓰는 것과 같다(`cli._check_tool_runtime`의 `== "fake"`).
    """
    ledger = _ledger()
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.ledger = ledger
    assert ctx.config.profile.adapters.tool_runner == "fake", "이 검사의 전제가 무너졌다"

    ledger.charge(
        Usage(input_tokens=0, output_tokens=0, cached_tokens=0),
        stage=2,
        gpu_ms=None if ctx.config.profile.adapters.tool_runner == "fake" else 1500,
    )

    assert ledger.gpu_ms == 0
    assert ledger.unmeasured_gpu_calls == 1


def test_the_exhaustion_report_uses_the_unit_its_name_claims() -> None:
    """밖으로 나가는 숫자는 `item` 이름이 뜻하는 단위여야 한다.

    원장은 ms로 누산하고 ms로 판정하지만, 예외·trace·`remaining()`은 전부 초를 낸다.
    변환을 빼먹으면 `gpu_seconds 4491/4 소진` 같은 메시지가 사람에게 간다 — 이름과
    단위가 어긋난 숫자를 기록에 남기는 것이 이번 결함과 같은 계열이다.
    """
    ledger = _ledger(gpu_seconds=4)
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)
    for wall_ms in ATOM_TOOL_WALL_MS:
        ledger.charge(zero_usage, stage=2, gpu_ms=wall_ms)
    assert ledger.gpu_ms == 4491  # 내부는 ms

    with pytest.raises(BudgetExhausted) as exc:
        ledger.ensure(Stage.AUDITOR)

    assert exc.value.item == "gpu_seconds"
    assert (exc.value.spent, exc.value.limit) == (4, 4)  # ms였다면 4491/4000
    assert ledger.remaining()["gpu_seconds"] == 0
    blocked = ledger._trace.events[-1].data
    assert (blocked["spent"], blocked["limit"]) == (4, 4)


def test_the_tool_budget_actually_stops_a_run() -> None:
    """**지금까지 한 번도 걸린 적 없는 상한이 실제로 걸리는지 본다.**

    도구 시간이 `gpu_seconds`로 청구되던 시절에는 내림 때문에 소비가 거의 오르지 않아
    상한이 사실상 무한이었다. 지표 문제가 아니라 **안전장치가 죽어 있던 것**이다.
    """
    budget = Budget(input_tokens=10**9, output_tokens=10**9, gpu_seconds=0, tool_seconds=8)
    ledger = BudgetLedger(budget, TraceWriter(RUN_ID, clock=lambda: NOW))
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    for wall_ms in MEASURED_TOOL_WALL_MS:  # 합계 8,492 ms > 8초
        ledger.charge(zero_usage, stage=2, tool_ms=wall_ms)

    assert ledger.tool_ms == 8492
    with pytest.raises(BudgetExhausted) as exc:
        ledger.ensure(Stage.AUDITOR)
    assert exc.value.item == "tool_seconds"
    assert (exc.value.spent, exc.value.limit) == (8, 8)


def test_tool_time_does_not_land_in_the_gpu_ledger() -> None:
    """도구 넷(semgrep·clang-tidy·build·asan)은 GPU를 쓰지 않는다.

    2026-08-17까지 그 시간이 `gpu_seconds`로 청구됐고, evaluation.md §5.3의 "finding당
    GPU-hour"가 그 값을 분자로 쓴다 — 섞이면 핵심 비용 지표가 CPU 시간으로 오염된다.
    """
    ledger = _ledger()

    ledger.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, tool_ms=5000)

    assert ledger.tool_ms == 5000
    assert ledger.gpu_ms == 0  # GPU 칸은 도구가 건드리지 않는다


def test_an_unlimited_item_has_no_remaining_not_zero() -> None:
    """#483: 상한 0은 「상한 없음」이라 잔량이 `None`이다. `0`은 상한이 있고 다 쓴 항목뿐이다.

    `dev_fake`처럼 네 상한이 다 0인 원장은 아무리 써도 소진이 아니다 — 잔량을 0으로 내면
    읽는 쪽이 소진으로 오독한다(#435가 `estimate` 출력에서 겪은 그 오독).
    """
    unlimited = BudgetLedger(
        Budget(input_tokens=0, output_tokens=0, gpu_seconds=0),
        TraceWriter(RUN_ID, clock=lambda: NOW),
    )
    usage = Usage(input_tokens=500, output_tokens=50, cached_tokens=0)
    unlimited.charge(usage, stage=4, gpu_ms=3000)
    assert not unlimited.is_exhausted
    assert unlimited.remaining() == {
        "input_tokens": None,
        "output_tokens": None,
        "gpu_seconds": None,
        "tool_seconds": None,
    }

    spent = _ledger(gpu_seconds=2)
    spent.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, gpu_ms=2500)
    left = spent.remaining()
    assert left["gpu_seconds"] == 0  # 상한 2초를 다 썼다
    assert left["tool_seconds"] is None  # 상한 0 -> 없음
    assert left["input_tokens"] == 10**9
