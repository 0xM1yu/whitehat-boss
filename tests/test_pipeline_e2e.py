"""파이프라인 end-to-end 테스트.

이 파일이 PR의 주장을 실증한다. 세 가지를 본다.

1. dev_fake 프로필로 0~9단계가 완주하고 `RunRecord.finished_at`이 채워진다
2. 같은 조건으로 두 번 실행하면 trace JSONL에서 `ts`·`wall_ms`만 뺐을 때 바이트 동일
3. 계획 §6 지표 대응표의 앞 8행이 trace에서 실제로 계산된다

**측정값은 하드코딩하지 않는다.** 제거율·토큰 수는 이 프로젝트가 재려는 값이다.
여기서 단언하는 것은 "산출 가능하다"와, Scout 제거율에 한해 "0%도 100%도 아니다"뿐이다.
"""

import dataclasses
import inspect
import io
import json
import re
import socket
import subprocess
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from slicer_source import assemble
from src import cli, console
from src.adapters.container_tools import index_output_path
from src.adapters.cybergym_oracle import TaskOracle
from src.adapters.factory import build_deps, describe_wiring
from src.adapters.fake_model import FakeModelClient
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.sqlite_store import SqliteStateStore
from src.config import PROMPTED_ROLES, SCOUT_POOL_ROLES, load_config
from src.ids import new_run_id
from src.interfaces.model_client import ModelGatewayError, SchemaParseError
from src.orchestrator import runner, task_loader, trace
from src.orchestrator.runner import RunSpec
from src.orchestrator.stages import GATE_STAGES, Stage
from src.orchestrator.trace import VOLATILE_FIELDS
from src.schemas.candidate import Candidate
from src.schemas.common import PLACEHOLDER_DIGEST, Location
from src.schemas.enums import FindingState, ReasonCode, Role
from src.schemas.finding import Finding
from src.schemas.run import CONDITION_PATTERN, Budget, RunRecord
from src.schemas.scout_response import ScoutResponse
from tests.conftest import ALL_PROFILES, SERVED_SCOUT_KEY, served_scout_config
from tests.oracle_fakes import FakeOracle, submit_script
from tests.test_cybergym_oracle import _AGENT_ID, _API_KEY, _SERVER, _TASK_ID, _FakeOracle

TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
STARTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CONDITION = "B3prime@pipeline"


def _run(tmp_path: Path, name: str = "run", *, profile: str = "dev_fake", **overrides: object):  # noqa: ANN202
    """파이프라인을 한 번 돌리고 (결과, trace 경로)를 돌려준다. 기본은 dev_fake.

    `before`는 run_dir이 생긴 뒤 실행 전에 불린다 — index.json을 그 자리에 놓는 데 쓴다
    (W2). `candidate_max`는 (a′)의 절단 경로를 태우는 데 쓴다.

    `oracle`은 채점기 대역이다 — `cli.execute`가 `build_deps`·`run_pipeline`으로 흘려 보내는 **운영
    배선 그대로** 들어가므로(`ctx.oracle`을 직접 꽂지 않는다) 그 배선이 끊기면 대역이 불리지 않는다.

    `configure`는 **위 셋으로 표현할 수 없는 config 변경**을 받는다 — `config`를 받아
    바꾼 것을 돌려주는 함수다. `execution.mode` 같은 프로필 필드가 그렇다. 이 훅이
    없으면 그런 검사가 이 헬퍼 본문을 복사하게 되고, 그러면 `cli.execute` 인자가
    늘 때 **복사한 쪽만 조용히 뒤처진다**(@sumin0218 리뷰 #161-3).
    """
    config = load_config(profile)
    if "budget" in overrides:
        config = config.transition(budget=overrides.pop("budget"))
    if "candidate_max" in overrides:
        thresholds = config.thresholds.transition(candidate_max=overrides.pop("candidate_max"))
        config = config.transition(thresholds=thresholds)
    if "critic_top_n" in overrides:
        thresholds = config.thresholds.transition(critic_top_n=overrides.pop("critic_top_n"))
        config = config.transition(thresholds=thresholds)
    configure = overrides.pop("configure", None)
    if configure is not None:
        config = configure(config)
    before = overrides.pop("before", None)
    oracle = overrides.pop("oracle", None)
    target = overrides.pop("target", TARGET)
    run_dir = tmp_path / name / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    if before is not None:
        before(run_dir)
    spec = RunSpec(condition=CONDITION, target=target, started_at=STARTED_AT, **overrides)
    trace_path = run_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as stream:
        result, _ = cli.execute(
            config, spec, run_id=run_dir.name, run_dir=run_dir, trace_stream=stream, oracle=oracle
        )
    return result, trace_path


def _events(trace_path: Path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


def test_execute_wires_read_payload_from_the_artifacts_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#247 리뷰 HIGH-1: `cli.execute`가 `read_payload`를 `make_payload_reader`로 배선한다.

    그 줄(`src/cli.py`)을 지우면 Critic·Auditor의 v3가 근거 본문을 못 받는다
    (`RunContext.read_payload` 기본값이 `lambda _ref: None`). `run_pipeline`을 가로채 실제로
    넘어온 reader가 payload 파일을 읽는지 본다 — 배선을 끊으면 red다. 그 reader가 노드까지
    닿는지는 이 검사가 아니라 아래 `test_run_pipeline_carries_read_payload_to_auditor_and_critic`이
    문다.
    """
    captured: dict[str, object] = {}

    def fake_run_pipeline(spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["read_payload"] = kwargs.get("read_payload")
        return None

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    config = load_config("dev_fake")
    run_dir = tmp_path / "run" / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    payload = run_dir / "semgrep" / "ev_0001.log"
    payload.parent.mkdir(parents=True)
    payload.write_text("SEMGREP FINDING BODY", encoding="utf-8")
    spec = RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT)

    cli.execute(config, spec, run_id=run_dir.name, run_dir=run_dir)

    reader = captured["read_payload"]
    assert callable(reader)
    assert reader("artifacts/run_20260901_0001/semgrep/ev_0001.log") == "SEMGREP FINDING BODY"
    assert reader("artifacts/run_20260901_0001/semgrep/missing.log") is None


def _content_aware_auditor_and_critic(config):  # noqa: ANN001, ANN202
    """auditor·critic 자리를 v3 fake로 바꾼다 — 두 노드가 근거 본문을 읽는 버전이다.

    커밋된 fake 키는 v1·v2뿐이라 `_poolify_v3`처럼 `fake`를 복사해 버전만 v3로 올린다.
    Scout 자리는 건드리지 않는다.
    """
    fake_v3 = config.models["fake"].transition(prompt_template_version="v3")
    roles = {**config.profile.roles, "auditor": "fake_v3", "critic": "fake_v3"}
    return config.transition(
        models={**config.models, "fake_v3": fake_v3},
        profile=config.profile.transition(roles=roles),
    )


def test_run_pipeline_carries_read_payload_to_auditor_and_critic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli.execute` → `run_pipeline` → `RunContext` → 노드까지 `read_payload`가 **실제로** 닿는다.

    위 검사는 `run_pipeline`을 가짜로 바꿔 `cli.execute`가 인자를 넘기는지만 보고, 노드
    검사(`test_nodes.py`)는 `ctx.read_payload`를 픽스처에 직접 꽂는다 — 그래서 `run_pipeline`이
    받은 reader를 `RunContext`에 안 심어도 둘 다 초록이다. 여기서는 파이프라인을 진짜로
    돌리고 reader만 표지를 돌려주게 해, auditor·critic 프롬프트 로그에 그 표지가 실렸는지
    본다. `src/cli.py`의 `read_payload=` 줄이나 `runner.run_pipeline`의 `RunContext(...)`
    안 `read_payload=` 줄 중 하나라도 지우면 기본값(`lambda _ref: None`)으로 떨어져 빨개진다.

    fake 도구는 payload 파일을 쓰지 않으므로 reader 공장(`cli.make_payload_reader`)만
    바꾼다 — 파일을 읽는 쪽은 바로 위 검사가 문다.
    """
    marker = "PAYLOAD-BODY-MARKER-read_payload"
    monkeypatch.setattr(cli, "make_payload_reader", lambda _root: lambda _ref: marker)

    _, trace_path = _run(tmp_path, configure=_content_aware_auditor_and_critic)

    prompts = trace_path.parent / "prompts"
    for role in ("auditor", "critic"):
        files = sorted(prompts.glob(f"*_{role}*.md"))
        assert files, f"{role} 프롬프트가 한 건도 안 남았다 — 이 검사가 그 노드를 안 탔다"
        assert any(marker in f.read_text(encoding="utf-8") for f in files), (
            f"{role} v3 프롬프트에 근거 본문이 없다 — read_payload가 노드까지 안 닿았다"
        )


def test_run_pipeline_wires_resolve_requests_from_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Dependencies.resolve_requests`가 `RunContext.resolve_requests`로 그대로 이어진다.

    러너가 `src/adapters/`를 import하지 않으므로(모듈 docstring) resolver 구현은 이 hook으로만
    경계를 넘는다(ADR 0012 결정 2). 조립(`run_pipeline`)이 `deps.resolve_requests`를 `ctx`에
    심지 않으면, need_evidence 루프가 아무리 잘 짜여도 그 hook을 영영 못 본다 — `symbols_for`·
    `read_slice`·`counter_for_role`과 같은 자리인데 그 셋과 달리 아직 조립되지 않았다.
    """
    from src.adapters import factory as factory_mod
    from src.orchestrator import context as context_mod

    def sentinel(_query: object) -> object:
        return None

    captured: dict[str, object] = {}
    original_init = context_mod.RunContext.__init__

    def spy_init(self: object, *args: object, **kwargs: object) -> None:
        captured["resolve_requests"] = kwargs.get("resolve_requests")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(context_mod.RunContext, "__init__", spy_init)

    original_build_deps = factory_mod.build_deps

    def spy_build_deps(*args: object, **kwargs: object) -> object:
        deps = original_build_deps(*args, **kwargs)
        return dataclasses.replace(deps, resolve_requests=sentinel)

    monkeypatch.setattr("src.cli.build_deps", spy_build_deps)

    _run(tmp_path)

    assert captured.get("resolve_requests") is sentinel, (
        "run_pipeline이 deps.resolve_requests를 RunContext에 안 심었다 — 받은 값: "
        f"{captured.get('resolve_requests')!r}"
    )


def test_run_pipeline_hands_the_raw_source_reader_to_the_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli.execute` → `run_pipeline` → `RunContext`까지 `read_raw`가 **실제로** 닿고 대상 파일의
    원본 바이트를 돌려준다. GUARDS의 줄 정렬 검사(`bare_cr`)는 `read_source`가 접기 전의 `\\r`을
    봐야 하므로 이 배선이 있어야 한다 — 단위 검사(`test_source_guards`)는 `ctx.read_raw`를 직접
    꽂아 이 배선이 끊겨도 초록이다. `src/cli.py`의 `read_raw=` 줄이나 `run_pipeline`의
    `RunContext(...)` 안 `read_raw=` 줄을 지우면 기본값(`lambda _file: None`)으로 떨어져
    빨개진다."""
    from src.orchestrator import context as context_mod

    captured: dict[str, object] = {}
    original_init = context_mod.RunContext.__init__

    def spy_init(self: object, *args: object, **kwargs: object) -> None:
        captured["read_raw"] = kwargs.get("read_raw")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(context_mod.RunContext, "__init__", spy_init)

    _run(tmp_path)

    reader = captured.get("read_raw")
    assert callable(reader), f"RunContext가 read_raw를 못 받았다: {reader!r}"
    assert reader("parser.c") == (TARGET / "parser.c").read_bytes()


def _canonical(trace_path: Path) -> bytes:
    """재현성 비교 표현: `ts`·`wall_ms`만 제거한 JSONL."""
    lines = []
    for event in _events(trace_path):
        for field in VOLATILE_FIELDS:
            event.pop(field, None)
        lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines).encode("utf-8")


def _charged(trace_path: Path) -> int:
    """run이 끝난 시점의 누적 input 토큰. `RunRecord.budget`은 할당량이라 소비가 아니다.

    `budget` 이벤트는 **그 시점까지의 누적값**이지 이번 차감분이 아니다. 합산하면
    스냅샷을 여러 번 더하게 되어 아무 뜻도 없는 수가 나온다 — 단조 증가라 대소 비교는
    우연히 맞지만, 그 값을 "소비량"이라 부르면 지표가 거짓이 된다.
    """
    charges = [
        event["data"]["input_tokens"]
        for event in _of(_events(trace_path), "budget")
        if event["data"]["event"] == "charge"
    ]
    return charges[-1] if charges else 0


def _of(events: list[dict], kind: str, *, stage: int | None = None) -> list[dict]:
    return [e for e in events if e["kind"] == kind and (stage is None or e["stage"] == int(stage))]


def _run_transition(trace_path: Path) -> dict:
    """run 객체의 마지막 전이(R-1/R-2) 이벤트. 종료 사유가 여기 남는다.

    `stage`는 이벤트의 최상위 필드이고 `rule`·`reason`은 `data` 안이므로 이벤트 전체를
    돌려준다.
    """
    runs = [e for e in _of(_events(trace_path), "transition") if e["subject_kind"] == "run"]
    assert runs, "run 전이가 없으면 종료 사유를 읽을 수 없다"
    return runs[-1]


# --- 1. 완주 ----------------------------------------------------------------


def test_pipeline_runs_all_stages_end_to_end(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    stages_seen = {e["stage"] for e in _of(_events(trace_path), "stage_start")}
    assert stages_seen == set(range(int(Stage.REPORT) + 1))
    assert result.record.finished_at is not None
    assert result.record.stopped_after is None
    assert result.record.is_complete


def test_run_leaves_a_heartbeat_file_with_final_state(tmp_path: Path) -> None:
    """러너 배선 고정 (이슈 #208 ②): run_dir가 있으면 heartbeat.json이 남고,
    종료 시점의 pid·이벤트 수·단계를 담는다. 단위 테스트는 `Heartbeat`를 직접
    만들지만 이 테스트는 `run_pipeline`이 실제로 그것을 켜고 끄는지를 본다."""
    _result, trace_path = _run(tmp_path)

    heartbeat = trace_path.parent / "heartbeat.json"
    assert heartbeat.exists()
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert payload["pid"] > 0
    assert isinstance(payload["last_event_seq"], int) and payload["last_event_seq"] > 0
    assert isinstance(payload["current_stage"], int)
    assert payload["interval_s"] > 0


def test_pipeline_promotes_one_target_and_filters_the_other(tmp_path: Path) -> None:
    """파이프라인이 실제로 무언가를 통과시키고 무언가를 거른다."""
    result, _ = _run(tmp_path)

    states = {c.location.file: c.state for c in result.candidates}
    assert states["parser.c"] is FindingState.TRIAGED
    assert states["util.c"] is FindingState.QUEUED  # 폐기가 아니라 보류
    assert any(f.state is FindingState.PROVEN for f in result.findings)


def test_pov_generates_a_poc_at_the_validate_stage_for_dev_fake(tmp_path: Path) -> None:
    """dev_fake는 `pov: fake_v2`를 선언하므로 러너가 8단계(VALIDATE)에서 pov를 **추가로** 부른다
    (PoV 배선 — 추가형). `validate.run`은 그대로 돌고(유일한 결정론 E4 게이트), pov는 나란히
    PoC를 만들어 `pov_generated`로 바이트 크기를 기록한다. 실행·게이트는 호스트 후속이다.

    이 검사가 「pov가 죽은 코드가 아니다」를 실증한다 — 러너 라우팅을 끊으면 8단계 pov_generated가
    0건이 되어 빨개진다."""
    _, trace_path = _run(tmp_path)

    gen = _of(_events(trace_path), "pov_generated", stage=int(Stage.VALIDATE))
    assert gen, (
        "dev_fake가 pov를 선언하는데 8단계에서 pov_generated가 0건 — 러너가 pov를 라우팅하지 않는다"
    )
    assert all(e["data"]["poc_bytes"] > 0 for e in gen), "pov가 빈 PoC를 냈다"


def test_vuln_description_reaches_scout_auditor_and_pov_prompts(tmp_path: Path) -> None:
    """과제 설명이 RunSpec → RunContext → retrieval → 캡슐 → 프롬프트까지 닿는다(PoV 배선 —
    새 vuln_description 섹션). Scout는 LIGHT 캡슐로, Auditor는 EXPANDED로, PoV는 그
    EXPANDED(finding_capsules)로 본다 — 한 레벨이라도 주입이 빠지면 그 역할 프롬프트에서 마커가
    사라진다(EXPANDED 주입을 지우면 auditor·pov가 빨개진다)."""
    marker = "VULN-DESC-MARKER-parse_header-heap-overflow"
    _, trace_path = _run(tmp_path, vuln_description=f"{marker}: len > 64 overflows buf")

    prompts = trace_path.parent / "prompts"
    for role in ("scout", "auditor", "pov"):
        files = sorted(prompts.glob(f"*_{role}*.md"))
        assert files, f"{role} 프롬프트가 한 건도 안 남았다"
        assert any(marker in f.read_text(encoding="utf-8") for f in files), (
            f"vuln_description가 {role} 프롬프트에 안 실렸다 — 그 레벨의 캡슐 주입/스레드가 끊겼다"
        )


def test_run_reads_the_description_file_into_the_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run --description-file`이 파일 내용을 RunSpec.vuln_description에 넣는다 — `cli.py`의
    `vuln_description=` 줄을 지우면 빨개진다(그 줄이 유일한 CLI→spec 배선이다)."""
    desc = tmp_path / "description.txt"
    desc.write_text("HEAP OVERFLOW MARKER cli-desc-thread", encoding="utf-8")
    captured: dict[str, object] = {}

    class _Stop(RuntimeError):
        pass

    def fake_execute(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["spec"] = spec
        raise _Stop

    monkeypatch.setattr(cli, "execute", fake_execute)

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--target", str(TARGET), "--seed", "1",
            "--condition", "B3prime@pipeline", "--device", "atom", "--kb-snapshot", "kb-1",
            "--artifacts-root", str(tmp_path / "arts"), "--description-file", str(desc),
        ])

    assert captured["spec"].vuln_description == "HEAP OVERFLOW MARKER cli-desc-thread"


def test_run_rejects_a_missing_description_file(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """없는 `--description-file`은 조용히 넘어가지 않고 exit 2로 멈춘다(슬라이스와 같은 판단)."""
    rc = cli.main([
        "run", "--profile", "dev_fake", "--target", str(TARGET), "--seed", "1",
        "--artifacts-root", str(tmp_path / "arts"),
        "--description-file", str(tmp_path / "nope.txt"),
    ])

    assert rc == 2
    assert "description-file" in capsys.readouterr().err


def _level1_task(root: Path, *, description: str = "DESC MARKER task-dir") -> Path:
    """CyberGym Level 1 과제 번들 하나 — `repo-vul.tar.gz` + `description.txt` **둘뿐**이다.

    2026-10-02 실측: HuggingFace `tasks.json`의 `task_difficulty.level1`이 정확히 그 둘을 든다
    (원천 디렉터리에는 `patch.diff`·`repo-fix.tar.gz`·`error.txt`도 있지만 **정답 재료라 level 1에
    들어가지 않는다**). 그래서 픽스처도 둘만 만든다 — 더 만들면 실제보다 관대한 입력이 된다.
    """
    task = root / "task"
    task.mkdir(parents=True)
    (task / "description.txt").write_text(description, encoding="utf-8")
    src = root / "stage" / "src-vul"
    src.mkdir(parents=True)
    (src / "mod_00.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    with tarfile.open(task / "repo-vul.tar.gz", "w:gz") as tf:
        tf.add(src, arcname="src-vul")
    return task


def test_run_takes_a_level1_task_bundle_as_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 `run --task-dir`가 과제 번들을 풀어 **target과 description을 둘 다** 채운다 (#356).

    2026-10-02까지 `task_loader.load_task`의 **프로덕션 호출처가 0건**이었다 — CLI는
    `--description-file`로 설명만 받고 아카이브는 사람이 손으로 풀어야 했다. 그래서 과제 1건을
    돌리는 경로가 손 절차에 걸려 있었고, 그 절차는 run 기록에 안 남는다.

    `cli.py`의 `--task-dir` 분기를 지우면 여기서 죽는다."""
    task = _level1_task(tmp_path)
    captured: dict[str, object] = {}

    class _Stop(RuntimeError):
        pass

    def fake_execute(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["spec"] = spec
        raise _Stop

    monkeypatch.setattr(cli, "execute", fake_execute)

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--seed", "1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
        ])

    spec = captured["spec"]
    assert spec.vuln_description == "DESC MARKER task-dir"
    # 해제된 트리가 target이다 — 사람이 `--target`을 따로 줄 필요가 없다.
    assert spec.target == tmp_path / "w"
    assert (tmp_path / "w" / "src-vul" / "mod_00.c").is_file()


def _task_run_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture, extra: list[str], *, needle: str
) -> str:
    """`--task-dir` run 하나가 **exit 2이고 빈 run 디렉터리를 안 남기는지** 본다. stderr를 돌려준다.

    거부 갈래마다 같은 두 불변식을 봐야 한다 — 전 판은 그것을 **한 갈래에서만** 봐서 나머지가
    run 디렉터리를 만들어도 초록이었다(적대적 리뷰 2026-10-02 M-3)."""
    arts = tmp_path / "arts"
    rc = cli.main([
        "run", "--profile", "dev_fake", "--seed", "1", "--artifacts-root", str(arts), *extra,
    ])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert needle in err, err
    assert not arts.exists() or not list(arts.iterdir()), "빈 run 디렉터리를 남겼다"
    return err


def test_run_refuses_a_task_work_dir_that_already_has_files(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 **작업 디렉터리를 재사용하지 않는다** — 비어 있지 않으면 exit 2다.

    `task_loader.safe_extract`는 상한 초과로 중단해도 **앞 멤버를 dest에 남긴다**(그 함수의 경고
    주석). 남은 트리 위에 또 풀면 두 과제가 섞이고, 섞인 트리로 돈 run은 무엇을 봤는지 모른다."""
    task = _level1_task(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    (work / "leftover.c").write_text("stale", encoding="utf-8")

    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle="task-work-dir",
    )


def test_run_accepts_a_task_work_dir_that_exists_but_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이미 있지만 **빈** 디렉터리는 받는다 — `mkdir`로 미리 만들어 둔 경우가 정상이다.

    「비어 있지 않으면 거부」를 「있으면 거부」로 좁히는 변이가 여기서 죽는다."""
    task = _level1_task(tmp_path)
    work = tmp_path / "w"
    work.mkdir()

    class _Stop(RuntimeError):
        pass

    monkeypatch.setattr(cli, "execute", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--seed", "1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(work),
        ])

    assert (work / "src-vul" / "mod_00.c").is_file()


def test_run_refuses_a_task_dir_without_the_archive(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """아카이브가 없는 과제는 exit 2다 — 생 예외가 추적으로 새 나가지 않는다."""
    task = tmp_path / "empty"
    task.mkdir()
    work = tmp_path / "w"
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle=f"{task_loader.REPO_ARCHIVE_NAME}가 없다",
    )
    # 사전 검사가 해제 **앞**이라 작업 디렉터리를 만들지도 않는다 — 그 검사를 지우면
    # `safe_extract`가 `mkdir`을 먼저 하고 `FileNotFoundError`(문구에 아카이브 이름이 든다)로
    # 죽어 위 needle은 다른 문구로 바뀌고, 빈 디렉터리가 남는다(#381 리뷰 LOW).
    assert not work.exists(), "아카이브가 없는데 작업 디렉터리를 만들었다"


# 정답 자료 가드(`task_loader.check_task_dir`)가 **프로덕션 경로에 닿는지** 보는 테스트들. 가드는
# 처음에 `load_task`에만 걸렸고 그 함수의 프로덕션 호출처는 0건이다 — `run --task-dir`은
# `read_description`과 `safe_extract`를 따로 부른다. 그래서 `tests/test_task_loader.py`의 단위
# 테스트가 전부 초록이어도 CLI는 정답 자료가 섞인 번들을 그대로 돌렸다(2026-10-03 실측: exit 0 ·
# 해제 트리와 run 디렉터리가 남았다). 내용물은 전부 무해한 자리표시자다 — 가드는 이름만 본다.
_PLACEHOLDER_TEXT = "placeholder, not real task material\n"


def test_run_refuses_a_task_dir_holding_answer_key_files_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 정답 자료가 섞인 번들은 **시작하기 전에** 거부한다 — exit 2 · `[ERROR]` · 쓴 것 0.

    번들 폴더를 `data/<종류>/<id>/*`로 통째로 받으면 과제 둘 옆에 정답 자료 셋(수정 diff · 크래시
    로그 · 패치된 저장소)이 떨어진다 — 저자 데이터셋의 실제 모양이다. 그 디렉터리로 도는 run은
    무효다. 가드가 `load_task`에만 걸려 있던 판에서는 이 run이 **exit 0으로 끝까지 돌았다**(작업
    디렉터리를 풀고 run 디렉터리를 남기고). 작업 디렉터리·run 디렉터리 둘 다 안 만들었는지 본다 —
    쓰고 나서 거부하면 「거부했는데 시작됐다」다."""
    task = _level1_task(tmp_path)
    answer_key = ("patch.diff", "error.txt", "repo-fix.tar.gz")
    for name in answer_key:
        (task / name).write_text(_PLACEHOLDER_TEXT, encoding="utf-8")
    work = tmp_path / "w"

    err = _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle="patch.diff",
    )

    refusal = " ".join(_errors(err))
    assert refusal, f"[ERROR] 줄이 없다: {err}"
    assert all(name in refusal for name in answer_key), refusal
    # 운영자 콘솔(cp949)에 그대로 찍히는 줄이다 — `—`·`⚠` 같은 글자가 하나라도 있으면 stdout이
    # 죽는다. 경로는 개발자 환경의 것이라 뺀다(사용자 이름의 글자가 cp949 밖일 수 있다).
    refusal.replace(repr(str(task)), "<task-dir>").encode("cp949")
    assert _PLACEHOLDER_TEXT.strip() not in err, "거부 문구가 파일 내용을 되풀이했다"
    assert not work.exists(), "정답 자료가 섞인 번들인데 해제 디렉터리를 만들었다"


def test_run_refuses_when_the_task_dir_cannot_be_listed_instead_of_passing_unchecked(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 가드가 **목록을 못 읽어** 검사를 못 하면 통과가 아니라 거부다 — exit 2이고 트레이스백이
    아니다.

    디렉터리를 못 읽는 경우(권한)는 `OSError`다. CLI의 그 `except`를 빼면 트레이스백이 나오고,
    예외를 삼키고 계속하게 고치면 **검사 안 한 번들이 돈다**. 권한을 깎는 것은 Windows에서 안 되므로
    가드를 `PermissionError`를 던지는 대역으로 바꾼다 — 공유 객체(`cli` 모듈 속성)에 건다."""
    task = _level1_task(tmp_path)
    work = tmp_path / "w"

    def deny(path: Path) -> None:
        raise PermissionError(13, "denied (simulated)", str(path))

    monkeypatch.setattr(cli, "check_task_dir", deny)

    err = _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle="검사를 못 했다",
    )

    assert _errors(err), err
    assert not work.exists(), "검사를 못 했는데 해제 디렉터리를 만들었다"


def test_run_accepts_the_authors_generator_output_with_readme_and_submit_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 저자 생성기의 산출 네 파일(Level 1 둘 + `README.md`·`submit.sh`)은 거부하지 않는다.

    `submit.sh`는 채점기 신원(서버 · `task_id` · `agent_id`)의 **유일한 출처**다. 생성기를 마스크
    맵으로 돌렸으면 `task_id`는 마스킹된 값이라 하네스가 다시 만들 수 없고,
    `--oracle-submit`(#408)의 `read_submit_metadata`가 이 파일을 `--task-dir` 디렉터리에서 읽는다.
    허용 목록이 둘뿐이면 채점할 수 있는 번들이 전부 이 가드에 걸려 `--oracle-submit`이 영영 못 돈다
    — 두 PR이 서로 다른 곳에 있어 CI가 못 잡는 결함이다. 허용 목록을 다시 둘로 좁히는 변경이 여기서
    죽는다. 내용은 자리표시자다(실제 `submit.sh`의 서버 주소·id·체크섬은 이 저장소 어디에도 두지
    않는다)."""
    task = _level1_task(tmp_path)
    (task / "README.md").write_text(_PLACEHOLDER_TEXT, encoding="utf-8")
    (task / "submit.sh").write_text("# placeholder, not a real submit script\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class _Stop(RuntimeError):
        pass

    def fake_execute(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["spec"] = spec
        raise _Stop

    monkeypatch.setattr(cli, "execute", fake_execute)

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--seed", "1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
        ])

    # `execute`까지 왔다 = 가드가 막지 않았고 아카이브가 풀렸다.
    assert captured["spec"].target == tmp_path / "w"
    assert (tmp_path / "w" / "src-vul" / "mod_00.c").is_file()


def test_run_refuses_a_corrupt_archive_with_exit_2_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 **gzip이 아닌 아카이브도 exit 2다.**

    이것은 실제로 밟는 함정이다 — HuggingFace의 LFS 파일을 `curl -L` 없이 받으면 **텍스트 포인터
    파일**이 떨어지고, 그것을 풀면 `tarfile.ReadError`가 난다. 전 판은 `FilterError`만 감싸서 그
    예외가 호출자까지 새 트레이스백으로 나왔다(적대적 리뷰 M-2)."""
    task = tmp_path / "task"
    task.mkdir()
    (task / "description.txt").write_text("d", encoding="utf-8")
    # LFS 포인터의 실제 모양.
    (task / "repo-vul.tar.gz").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:0123\nsize 4\n", encoding="utf-8"
    )

    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(tmp_path / "w")],
        needle="풀 수 없다",
    )


def test_run_refuses_a_task_work_dir_that_is_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 `--task-work-dir`가 이미 있는 **파일**이면 exit 2다 — `NotADirectoryError` 트레이스백이
    아니다(#381 리뷰 LOW). 「비어 있지 않은가」를 `iterdir()`로 보는 검사가 디렉터리가 아닌 것에는
    예외를 던진다."""
    task = _level1_task(tmp_path)
    work = tmp_path / "w"
    work.write_text("not a directory", encoding="utf-8")
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle="디렉터리가 아니다",
    )


def test_run_refuses_a_task_work_dir_that_holds_only_a_subdirectory(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 하위 **디렉터리만** 남은 작업 디렉터리도 비어 있지 않은 것이다 — 해제가 중단되면 앞 멤버가
    파일이 아니라 디렉터리(`src-vul/`)로 남을 수 있다. 「파일만 센다」로 좁히는 변이가 여기서
    죽는다(#381 리뷰 LOW)."""
    task = _level1_task(tmp_path)
    work = tmp_path / "w"
    (work / "src-vul").mkdir(parents=True)
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle="비어 있지 않다",
    )


def test_run_refuses_a_task_work_dir_it_cannot_create(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 작업 디렉터리를 **만들 수 없으면**(부모 자리에 파일이 있다) exit 2다 — `OSError`가
    트레이스백으로 새지 않는다. CLI의 `except`에서 `OSError`를 빼는 변이가 여기서 죽는다."""
    task = _level1_task(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the parent directory should be", encoding="utf-8")
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(blocker / "w")],
        needle="풀 수 없다",
    )


def test_run_refuses_an_interpreter_without_extraction_filters_with_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 추출 필터가 없는 인터프리터(3.11.4 전)에서도 exit 2다 — `RuntimeError`가 새지 않고
    **풀기 전이라** 작업 디렉터리를 만들지 않는다. CLI의 `except`에서 `RuntimeError`를 빼는 변이가
    여기서 죽는다. 공유 객체(`tarfile` 모듈)에 건다 — 테스트 모듈의 사본을 바꾸면 안 먹는다."""
    monkeypatch.delattr(task_loader.tarfile, "data_filter")
    task = _level1_task(tmp_path)
    work = tmp_path / "w"
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(work)],
        needle="3.11.4",
    )
    assert not work.exists(), "풀기 전에 거부해야 하는데 작업 디렉터리가 생겼다"


@pytest.mark.parametrize("failure", ["corrupt-archive", "drifted-slices"])
def test_a_refusal_after_the_unpack_tells_the_operator_to_empty_the_work_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture, failure: str
) -> None:
    """🔴 **해제 뒤에 멈추면** 작업 디렉터리에 앞 멤버·트리가 남을 수 있고 같은 인자로 다시 돌면
    「비어 있지 않다」로 거부된다 — 그 길을 막은 뒤에 에러가 **비우라고 말해 준다**(#381 리뷰 LOW).
    비우는 것은 사람이 한다(지우지 않는다). 해제가 상한·손상으로 중단되는 경우와 해제 뒤 drift
    검사가 거부하는 경우, 둘 다 같은 말을 한다."""
    if failure == "corrupt-archive":
        task = tmp_path / "task"
        task.mkdir()
        lfs_pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:0123\nsize 4\n"
        (task / "repo-vul.tar.gz").write_text(lfs_pointer, encoding="utf-8")
        extra: list[str] = []
    else:
        task = _level1_task(tmp_path)
        extra = ["--slices", str(_task_slices(tmp_path, text="int main(void) { return 1; }"))]
    work = tmp_path / "w"

    rc = cli.main([
        "run", "--profile", "dev_fake", "--seed", "1",
        "--artifacts-root", str(tmp_path / "arts"),
        "--task-dir", str(task), "--task-work-dir", str(work), *extra,
    ])

    err = capsys.readouterr().err
    assert rc == 2, err
    assert "비우" in " ".join(_errors(err)), err


def test_run_refuses_both_task_dir_and_description_file(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """설명의 출처가 둘이면 exit 2다 — 어느 것이 캡슐에 실렸는지 기록으로 못 가른다."""
    task = _level1_task(tmp_path)
    desc = tmp_path / "other.txt"
    desc.write_text("other", encoding="utf-8")
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
         "--description-file", str(desc)],
        needle="--description-file",
    )


def test_run_refuses_both_task_dir_and_target(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`--task-dir`와 `--target`을 같이 주면 exit 2다 — 어느 트리를 본 run인지 모르게 된다."""
    task = _level1_task(tmp_path)
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
         "--target", str(TARGET)],
        needle="--target",
    )


_TASK_SOURCE = "src-vul/mod_00.c"
# 개수 가드 에러가 들고 있어야 하는 경로 기준 문장.
_SLICE_PATH_RULE = (
    "슬라이스 경로는 작업 디렉터리 기준(번들 최상위 디렉터리 `src-vul/` 포함)이어야 한다"
)


def _task_slices(
    root: Path,
    *,
    text: str = "int main(void) { return 0; }",
    files: tuple[str, ...] = (_TASK_SOURCE,),
) -> Path:
    """`files`마다 그 파일 1번 줄을 가리키는 슬라이스 하나씩을 든 `slices.json`. 기본은
    `_level1_task`가 풀어 놓는 `src-vul/mod_00.c` 하나다(슬라이스 id는 `slice_0001`부터).

    `code`는 슬라이서 자신의 `assemble`로 만든다 — 렌더러의 줄 규칙이 슬라이서와 같다는 것을 지어낸
    문자열이 아니라 슬라이서 함수로 맞추려는 것이다(`tests/test_slices.py`와 같은 근거 ·
    CLAUDE.md §2.5 「핀 샘플은 저장소에서 온다」). `text`를 다르게 주면 슬라이서가 **다른 내용**을
    봤다는 뜻이라 drift 슬라이스가 된다. `files`에 번들 최상위 접두어(`src-vul/`)가 없는 경로나 다른
    과제의 경로를 주면 풀린 트리에 없는 슬라이스가 된다."""
    items = []
    for number, file in enumerate(files, start=1):
        points = {(file, 1)}
        items.append({
            "id": f"slice_{number:04d}",
            "candidate_key": f"key_{number:04d}",
            "case": "A",
            "entry": {"file": file, "line": 1, "symbol": "main"},
            "members": [f"key_{number:04d}"],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, {file: [text]}),
        })
    path = root / "slices.json"
    path.write_text(
        json.dumps({
            "slice_version": "slice-v2",
            "status": "ok",
            "joern_engine_config": {"maxCallDepth": 2},
            "slices": items,
            "candidate_manifest": {item["candidate_key"]: {"scopes": []} for item in items},
        }),
        encoding="utf-8",
    )
    return path


def test_run_takes_slices_made_from_the_same_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 `--task-dir`와 `--slices`는 **같이 쓴다** — 경로가 작업 디렉터리 기준이고 줄 내용이
    풀린 트리와 같은 슬라이스면 받는다.

    전 판은 둘을 거부했다(*"슬라이스는 이미 풀린 트리에서 나온 것이고 작업 디렉터리는 비어 있어야
    하므로 슬라이서가 본 트리와 같을 수 없다"*). 그런데 `render`는 **같은 디렉터리**를 요구하지
    않는다 — 하네스 사본이 슬라이서가 본 것과 **글자 단위로** 같은지(drift)를 본다. 같은 아카이브를
    푼 트리는 어디서 풀었든 같다. 거부의 대가는 실모델 프로필(Scout 셋이 슬라이스 입력)에서
    `--task-dir`로는 Scout까지 가는 run을 **시작할 수 없다**는 것이었다(적대적 리뷰 HIGH-1).
    ⚠ 받는 조건이 **「이 번들의 슬라이스다」가 아니다** — 경로·줄 내용이 우연히 다 맞는 다른 과제의
    슬라이스는 이 경로로도 들어온다(개수 가드는 경로가 없는 것만 막는다)."""
    task = _level1_task(tmp_path)
    slices = _task_slices(tmp_path)
    captured: dict[str, object] = {}

    class _Stop(RuntimeError):
        pass

    def fake_execute(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["spec"] = spec
        raise _Stop

    monkeypatch.setattr(cli, "execute", fake_execute)

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--seed", "1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
            "--slices", str(slices),
        ])

    assert captured["spec"].slices_path == slices
    assert captured["spec"].target == tmp_path / "w"
    assert (tmp_path / "w" / _TASK_SOURCE).is_file()


def test_run_refuses_slices_that_drifted_from_the_unpacked_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """🔴 같은 경로인데 슬라이서가 **다른 줄 내용**을 봤으면(drift) exit 2다.

    이 검사가 닫는 것은 **경로는 맞고 내용이 다른** 슬라이스뿐이다 — 경로 자체가 트리에 없는
    슬라이스는 `render_slices`가 오류 없이 건너뛰므로 이 테스트가 아니라 바로 위 개수 가드
    (`..._does_not_contain`)가 문다. drift 검사는 **풀린 트리에** 돌아야 한다: 트리는
    `_claim_run_dir` 직전에야 풀리므로 그 전에 `render_slices`를 부르면 트리가 없어 슬라이스가
    **전부 조용히 빠진다** — 검사가 돌았는데 아무것도 안 본 것이다."""
    task = _level1_task(tmp_path)
    slices = _task_slices(tmp_path, text="int main(void) { return 1; }")  # 슬라이서가 본 것 ≠ 번들
    _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
         "--slices", str(slices)],
        needle="drift",
    )


@pytest.mark.parametrize(
    ("files", "matched", "unmatched_ids"),
    [
        # 슬라이서를 번들 최상위 `src-vul/`**에서** 돌려 경로에 그 접두어가 없다 — 가장 자연스러운
        # 슬라이싱 루트이고, 풀린 트리(`--task-work-dir` 기준)에서는 전부 없는 파일이다.
        pytest.param(("mod_00.c",), 0, ["slice_0001"], id="all-missing-the-bundle-prefix"),
        # 다른 과제의 slices.json — 경로가 아예 다르다.
        pytest.param(("other/file.c",), 0, ["slice_0001"], id="foreign-task"),
        # 일부만 안 맞는다 — 0건이 아니어서 「통째로 비었다」만 보는 검사를 통과한다.
        pytest.param((_TASK_SOURCE, "mod_00.c"), 1, ["slice_0002"], id="partly-missing"),
    ],
)
def test_run_refuses_slices_the_unpacked_tree_does_not_contain(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    files: tuple[str, ...],
    matched: int,
    unmatched_ids: list[str],
) -> None:
    """🔴 **렌더된 슬라이스 수가 입력 슬라이스 수와 다르면 exit 2다** — 침묵 0건 run을 막는다.

    `render_slices`는 entry 파일이 트리에 없는 슬라이스를 **조용히 건너뛴다**(drift가 아니라
    「없음」이라 오류가 아니다). `--task-dir`는 해제 트리가 번들 **최상위**(`src-vul/`의 부모)라
    슬라이서를 `src-vul/`에서 돌린 자연스러운 슬라이스는 전부 걸러지고, run은 `후보 0건 / 완주 /
    rc 0`으로 끝난다 — 아무것도 안 봤는데 정상으로 보이는 0이다(CLAUDE.md §0). 2026-10-03
    `dev_fake`로 재현했다(trace에 `unmatched_slices:1, planned:0`뿐, stderr 경고 없음).
    기존 `--target` 경로는 사람이 `--target <w>/src-vul`로 루트를 골라 이 문제가 안 난다.

    에러는 몇 건 중 몇 건이 안 맞았는지, **어느** 슬라이스인지, 경로 기준이 무엇인지를 말한다."""
    task = _level1_task(tmp_path)
    slices = _task_slices(tmp_path, files=files)

    err = _task_run_is_refused(
        tmp_path, capsys,
        ["--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
         "--slices", str(slices)],
        needle=_SLICE_PATH_RULE,
    )

    total = len(files)
    assert f"{total}건 중 {total - matched}건" in err, err
    for slice_id in unmatched_ids:
        assert slice_id in err, err
    for slice_id in {f"slice_{n:04d}" for n in range(1, total + 1)} - set(unmatched_ids):
        assert slice_id not in err, f"맞은 슬라이스 {slice_id}를 안 맞았다고 말한다"


def test_run_accepts_an_empty_slice_set_with_a_task_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """슬라이스 **0건**짜리 `slices.json`은 받는다 — 슬라이서가 후보를 못 찾은 정상 산출물이다.

    개수 가드는 「입력 수 ≠ 렌더 수」이지 「렌더 수가 0」이 아니다 — 0 = 0은 어긋남이 아니다.
    「렌더가 비면 거부」로 좁히는 변이가 여기서 죽는다."""
    task = _level1_task(tmp_path)
    slices = _task_slices(tmp_path, files=())

    class _Stop(RuntimeError):
        pass

    def fake_execute(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        raise _Stop

    monkeypatch.setattr(cli, "execute", fake_execute)

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--seed", "1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
            "--slices", str(slices),
        ])


def test_a_serving_profile_starts_from_a_task_bundle_with_slices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """🔴 실모델 프로필(Scout 셋이 슬라이스 입력 v3)에서 `--task-dir`로 Scout까지 가는 run을
    **시작할 수 있다** — `_claim_run_dir`에 닿는 것으로 본다.

    적대적 리뷰 HIGH-1의 재현을 뒤집은 것이다: 전 판은 같은 인자에서 `--slices` 없이는 슬라이스
    가드가, 주면 `--task-dir` 게이트가 막아 **어느 쪽으로도 못 시작했다**(문구도 서로를 가리켰다).
    `execute`가 아니라 `_claim_run_dir`에서 세운다 — 그 뒤는 실서빙 엔드포인트가 필요하다."""
    _refuse_sockets(monkeypatch)
    task = _level1_task(tmp_path)
    slices = _task_slices(tmp_path)

    class _Stop(RuntimeError):
        """모든 게이트를 지나 run_dir 확보까지 왔다는 표시."""

    def _boom(*_: object, **__: object) -> tuple[str, Path]:
        raise _Stop

    monkeypatch.setattr(cli, "_claim_run_dir", _boom)
    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "harness_pool", "--seed", "1",
            "--condition", "B1prime@pipeline", "--device", "atom", "--kb-snapshot", "kb-1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
            "--slices", str(slices),
        ])

    assert not _errors(capsys.readouterr().err), "라벨·슬라이스를 다 줬는데 거부됐다"
    assert (tmp_path / "w" / _TASK_SOURCE).is_file()


@pytest.mark.parametrize(
    ("defect", "needle"),
    [
        pytest.param("no-labels", "실서빙", id="serving-label-gate"),
        pytest.param("no-slices", "슬라이스 입력", id="slice-input-guard"),
        pytest.param("undescribed-option", "Auditor SOURCE 옵션", id="auditor-source-options-gate"),
        pytest.param("unknown-device", "장비 파일", id="swap-serving-file-gate"),
    ],
)
def test_a_gate_refusal_leaves_the_task_work_dir_free_for_the_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    defect: str,
    needle: str,
) -> None:
    """🔴 **뒤의 게이트가 거부하면 작업 디렉터리에 트리가 남지 않는다** — 오타를 고친 재시도가 산다.

    해제는 모든 게이트 **뒤**, `_claim_run_dir` **앞**이다. 앞에서 풀면 게이트가 exit 2를 낸 뒤에도
    트리가 남아 같은 인자로 다시 돌면 「비어 있지 않다」로 거부된다(적대적 리뷰 2026-10-02 M-1 ·
    #381 리뷰 MEDIUM-1 — 그 수정에 핀이 없어 해제를 옛 위치로 되돌려도 초록이었다). 그래서
    **거부 직후의 작업 디렉터리**와 **고친 인자로의 재시도**를 둘 다 본다.

    해제 앞에 서는 게이트 **넷을 각각** 건다 — 실서빙 라벨 게이트(`--device`·`--condition` 없음) ·
    슬라이스 입력 가드(`--slices` 없음) · Auditor SOURCE 옵션 meta 게이트 · 교대 프로필의 장비
    파일 게이트(없는 `--device`). 처음 판은 앞 둘만 걸어 해제를 뒤 둘의 **앞**으로 옮겨도 초록이었다
    (#381 독립 검토 LOW). Auditor 게이트는 실제 검출기(`_undescribed_source_options`)를 **갈아끼워**
    세운다 — 저장소 설정에는 서술 안 된 옵션이 켜진 프로필이 없고, 이 테스트가 보는 것은 검출이
    아니라 게이트가 **서는 자리**다."""
    _refuse_sockets(monkeypatch)
    task = _level1_task(tmp_path)
    slices = ["--slices", str(_task_slices(tmp_path))]
    work = tmp_path / "w"
    base = [
        "run", "--profile", "harness_pool", "--seed", "1",
        "--artifacts-root", str(tmp_path / "arts"),
        "--task-dir", str(task), "--task-work-dir", str(work),
    ]
    labels = ["--condition", "B1prime@pipeline", "--device", "atom", "--kb-snapshot", "kb-1"]
    good = base + labels + slices
    first = {
        "no-labels": base + slices,
        "no-slices": base + labels,
        "undescribed-option": good,
        "unknown-device": base + [*labels[:3], "no-such-device", *labels[4:]] + slices,
    }[defect]
    real_detector = cli._undescribed_source_options
    if defect == "undescribed-option":
        monkeypatch.setattr(
            cli, "_undescribed_source_options", lambda config: ["bogus(auditor/v4)"]
        )

    rc = cli.main(first)

    err = capsys.readouterr().err
    assert rc == 2, err
    assert needle in " ".join(_errors(err)), err
    assert not work.exists() or not any(work.iterdir()), "게이트가 거부했는데 트리가 풀렸다"

    class _Stop(RuntimeError):
        """고친 인자로 모든 게이트를 지나 run_dir 확보까지 왔다는 표시."""

    def _boom(*_: object, **__: object) -> tuple[str, Path]:
        raise _Stop

    monkeypatch.setattr(cli, "_undescribed_source_options", real_detector)
    monkeypatch.setattr(cli, "_claim_run_dir", _boom)
    with pytest.raises(_Stop):
        cli.main(good)
    assert (work / _TASK_SOURCE).is_file()


def test_run_refuses_a_task_work_dir_without_a_task_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`--task-work-dir`만 주면 exit 2다 — 조용히 무시하면 사람은 과제가 풀린 줄 안다."""
    _task_run_is_refused(
        tmp_path, capsys,
        ["--target", str(TARGET), "--task-work-dir", str(tmp_path / "w")],
        needle="--task-work-dir",
    )


def test_run_refuses_a_task_dir_without_a_task_work_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`--task-dir`만 주면 exit 2다 — 해제 자리를 암묵 기본값으로 지어내지 않는다."""
    task = _level1_task(tmp_path)
    _task_run_is_refused(
        tmp_path, capsys, ["--task-dir", str(task)], needle="--task-work-dir",
    )


def test_run_warns_when_the_task_has_no_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """설명이 없으면 **경고하고 계속 간다** — Level 0 과제에는 설명이 없는 것이 정상이다.

    막으면 정상 입력을 거부하고, 조용히 넘어가면 "설명을 줬는데 안 실렸다"와 구별되지 않는다."""
    task = _level1_task(tmp_path)
    (task / "description.txt").unlink()
    captured: dict[str, object] = {}

    class _Stop(RuntimeError):
        pass

    def fake_execute(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["spec"] = spec
        raise _Stop

    monkeypatch.setattr(cli, "execute", fake_execute)

    with pytest.raises(_Stop):
        cli.main([
            "run", "--profile", "dev_fake", "--seed", "1",
            "--artifacts-root", str(tmp_path / "arts"),
            "--task-dir", str(task), "--task-work-dir", str(tmp_path / "w"),
        ])

    assert captured["spec"].vuln_description is None
    assert "description.txt" in capsys.readouterr().err


def test_run_still_requires_a_source_when_neither_flag_is_given(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`--target`도 `--task-dir`도 없으면 exit 2다 — `--target`을 선택으로 바꾼 대가를 여기서 문다.

    argparse의 `required=True`를 뗐으므로 그 검사를 `run()`이 들어야 한다. 안 들면 `None`이
    `RunSpec`까지 흘러간다."""
    rc = cli.main([
        "run", "--profile", "dev_fake", "--seed", "1",
        "--artifacts-root", str(tmp_path / "arts"),
    ])

    assert rc == 2
    assert "--target" in capsys.readouterr().err


def _poolify(mode: str | None):  # noqa: ANN202
    """`dev_fake`를 Scout 풀로 바꾸는 `configure` 훅 (in-test 전용).

    커밋된 풀 프로필 `harness_pool`(PR #190의 `local_balanced_pool`, 2026-09-24 이름 변경)은
    **실 모델**이라 GPU 없이는 못 돈다.
    이 e2e 검사들은 팬아웃 **제어흐름**만 보므로 Fake로 충분하고, 그래서 실 프로필을 태우는
    대신 여기서 `dev_fake` 기반 풀을 조립한다 — 풀 세 자리는 v2 전용 풀 프롬프트를 부르는
    `fake_v2`, 나머지는 `fake`다. (실 프로필의 로드·게이트 정합은 `test_config.py`가 본다.)
    """
    from src.config import ExecutionPolicy

    pool = {
        "scout_memory": "fake_v2",
        "scout_boundary": "fake_v2",
        "scout_injection": "fake_v2",
        "auditor": "fake",
        "critic": "fake",
        "embedding": "fake",
        "reranker": "fake",
    }

    def configure(config):  # noqa: ANN001, ANN202
        updates: dict[str, object] = {"roles": pool}
        if mode is not None:
            updates["execution"] = ExecutionPolicy(mode=mode)
        return config.transition(profile=config.profile.transition(**updates))

    return configure


def _poolify_v3(mode: str | None = None):  # noqa: ANN202
    """`dev_fake`를 **v3** Scout 풀로 바꾼다 — `_poolify`(v2)와 달리 ScoutResponse 경로를 태운다.

    v3 scout 프롬프트는 `output_schema: ScoutResponse`라, fake가 `Candidate`가 아니라
    `ScoutResponse`(래퍼)를 낸다. 그러면 `call_model`은 `scout_member`를 안 찍고(그 타입에
    필드가 없다) `scout._ask_model`이 래퍼를 풀어 안쪽 `candidate`에 직접 stamp한다.
    커밋된 fake 키는 v1(`fake`)·v2(`fake_v2`)뿐이라, v3 fake 엔트리는 여기서 조립한다
    (models.yaml 주석대로 in-test — `fake`를 복사해 버전만 v3로).

    `mode`는 `_poolify`와 같은 자리다(`execution.mode`) — 두 실행 모드에서 v3 풀을 재는
    검사가 매번 이 조립을 새로 쓰지 않게 한다.
    """
    def configure(config):  # noqa: ANN001, ANN202
        models = {
            **config.models,
            "fake_v3": config.models["fake"].transition(prompt_template_version="v3"),
        }
        pool = {
            "scout_memory": "fake_v3",
            "scout_boundary": "fake_v3",
            "scout_injection": "fake_v3",
            "auditor": "fake",
            "critic": "fake",
            "embedding": "fake",
            "reranker": "fake",
        }
        updates: dict[str, object] = {"roles": pool}
        if mode is not None:
            from src.config import ExecutionPolicy

            updates["execution"] = ExecutionPolicy(mode=mode)
        return config.transition(models=models, profile=config.profile.transition(**updates))

    return configure


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_every_seats_hop0_finishes_before_any_seats_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """4단계는 개시(hop0)와 정착으로 나뉜다(ADR 0012 결정 2) — **모든 좌석의 개시가 어느
    좌석의 정착보다 먼저 끝난다.**

    per_stage는 표를 단계 순서로 도는 것만으로 성립한다(`_run_stage_for_all`을 개시로 한
    번, 정착으로 한 번 돈다). per_candidate는 target마다 국면 A(전 좌석 개시) → 국면
    B(국면 A에서 살아남은 좌석마다 정착)로 다시 짠다 — 예전에는 좌석 하나가 개시부터
    게이트까지 관통한 뒤에야 다음 좌석으로 갔다. 동료 확인(peer review)이 이 순서에
    기댄다 — 국면 B가 시작될 때는 이미 전 좌석의 hop0 신호를 알아야 한다.

    **target이 하나뿐인 트리로 돈다.** per_candidate는 target마다 국면을 새로 도므로
    (target1의 국면 B가 target2의 국면 A보다 먼저 온다), target이 둘 이상이면 "모든
    개시가 첫 정착보다 먼저"가 **전역으로는 거짓이다** — 이 검사가 재는 것은 **같은
    target 안**의 순서이지, target을 가로지르는 순서가 아니다.
    """
    from src.orchestrator.nodes import scout as scout_node

    single_target = tmp_path / "single_target"
    single_target.mkdir()
    (single_target / "parser.c").write_text(
        (TARGET / "parser.c").read_text(encoding="utf-8"), encoding="utf-8"
    )

    events: list[str] = []
    original_open = scout_node.open_session
    original_finish = scout_node.finish

    def spy_open(*args: object, **kwargs: object) -> object:
        session = original_open(*args, **kwargs)
        events.append(f"open:{session.role.value}")
        return session

    def spy_finish(*args: object, **kwargs: object) -> object:
        result = original_finish(*args, **kwargs)
        session = args[1] if len(args) > 1 else kwargs["session"]
        events.append(f"finish:{session.role.value}")
        return result

    monkeypatch.setattr(scout_node, "open_session", spy_open)
    monkeypatch.setattr(scout_node, "finish", spy_finish)

    _run(tmp_path, configure=_poolify_v3(mode), target=single_target)

    opens = [e for e in events if e.startswith("open:")]
    finishes = [e for e in events if e.startswith("finish:")]
    assert len(opens) == 3, f"좌석 셋의 개시를 기대했는데: {opens}"
    assert len(finishes) == 3, f"좌석 셋의 정착을 기대했는데: {finishes}"
    first_finish_at = events.index(finishes[0])
    assert all(events.index(o) < first_finish_at for o in opens), (
        f"어떤 정착이 어느 개시보다 먼저 일어났다 — 순서: {events}"
    )


def test_scout_pool_v3_unwraps_scout_response_and_stamps_member(tmp_path: Path) -> None:
    """v3 풀은 fake가 낸 `ScoutResponse`를 풀어 안쪽 `Candidate`만 흘리고 멤버를 stamp한다.

    이것이 리뷰 #239 MEDIUM-1이 「0건」이라고 한 경로다 — `_poolify`(v2)는 `Candidate`를 바로
    내므로 `call_model`의 stamp만 타지, `scout._ask_model`의 `isinstance(value, ScoutResponse)`
    언랩·수동 stamp는 안 탄다. 언랩이 빠지면 `run`이 `Candidate` 대신 `ScoutResponse`를 흘려
    `ctx.create`에서 죽고, stamp가 어긋나면 아래 멤버 집합이 틀린다.
    """
    result, _ = _run(tmp_path, configure=_poolify_v3())

    by_target: dict[str, list[str | None]] = {}
    for candidate in result.candidates:
        key = f"{candidate.location.file}:{candidate.location.symbol}"
        by_target.setdefault(key, []).append(candidate.scout_member)

    assert by_target, "v3 풀인데 후보가 안 나왔다 — ScoutResponse 언랩이 흐름을 끊었을 수 있다"
    for key, members in by_target.items():
        assert sorted(m or "" for m in members) == [
            "scout_boundary",
            "scout_injection",
            "scout_memory",
        ], f"{key}: v3 ScoutResponse 언랩 후 멤버 stamp가 어긋난다 — got {members}"


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_scout_pool_fans_one_target_into_three_members(tmp_path: Path, mode: str | None) -> None:
    """풀은 대상 하나를 멤버 셋이 각각 봐서 후보 셋을 낸다 (ADR 0010 §3 방식 ①).

    합집합이지 투표가 아니다 — 세 후보가 합쳐지지 않고 각자 남는다. 각 후보의
    `scout_member`는 하네스가 stamp해(모델이 아니라 `RunContext.call_model`) 어느 자리에서
    왔는지 증언한다. 이 태그가 공동오답 행렬의 원천이다. 두 실행 모드 모두에서 돈다
    (Option C — 팬아웃을 계획에서 편다).
    """
    result, _ = _run(tmp_path, configure=_poolify(mode))

    by_target: dict[str, list[str | None]] = {}
    for candidate in result.candidates:
        key = f"{candidate.location.file}:{candidate.location.symbol}"
        by_target.setdefault(key, []).append(candidate.scout_member)

    assert by_target, "풀인데 후보가 하나도 안 나왔다"
    for key, members in by_target.items():
        assert sorted(m or "" for m in members) == [
            "scout_boundary",
            "scout_injection",
            "scout_memory",
        ], f"{key}: 멤버 셋이 각각 후보를 내야 한다 (방식 ①) — got {members}"


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_scout_pool_builds_the_light_capsule_once_per_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """멤버 셋이 같은 대상을 봐도 3단계 LIGHT 캡슐은 대상당 한 번만 만든다 (ADR 0010 §3).

    `state.capsules` 캐시가 첫 멤버의 캡슐을 나머지에 물려 준다 — 그러지 않으면 검색이
    멤버 수만큼(대상당 3배) 붙고 세 멤버가 조금씩 다른 캡슐을 볼 수 있다. `retrieval.run`을
    감시해 LIGHT 레벨 호출이 후보 수가 아니라 **대상 수**와 같음을 고정한다.
    """
    from src.orchestrator.nodes import retrieval as retrieval_node
    from src.schemas.enums import CapsuleLevel

    original = retrieval_node.run
    light_calls: list[tuple[object, ...]] = []

    def spy(ctx: object, *, target: object, level: object, **kwargs: object) -> object:  # noqa: ANN401
        if level is CapsuleLevel.LIGHT:
            light_calls.append((target.file, target.symbol, target.line_start, target.line_end))
        # 러너가 넘기는 나머지(`slice_id` — 슬라이스 없는 run이면 `None`)는 그대로 흘린다.
        return original(ctx, target=target, level=level, **kwargs)

    monkeypatch.setattr(retrieval_node, "run", spy)
    result, _ = _run(tmp_path, configure=_poolify(mode))

    targets = {(c.location.file, c.location.symbol) for c in result.candidates}
    assert len(light_calls) == len(targets), (
        f"LIGHT 캡슐은 대상당 한 번(대상 {len(targets)})인데 {len(light_calls)}회 만들었다 — "
        "풀 멤버마다 다시 만들면 이 수가 후보 수(대상당 3)로 뛴다"
    )
    assert len(set(light_calls)) == len(light_calls), "같은 대상을 두 번 build했다 — 캐시 miss"


# `_drop`이 게이트웨이 실패에 붙이는 사유. 러너 상수를 그대로 읽는다 — 문자열을
# 여기 복사하면 러너가 사유를 바꿀 때 이 검사가 조용히 0건을 세게 된다.
_REASON_GATEWAY_ERROR = runner._REASON_GATEWAY


def _with_mode(mode: str):  # noqa: ANN202
    """`execution.mode`만 바꾸는 `configure` 훅. 역할은 안 건드린다(단일 scout 그대로)."""
    from src.config import ExecutionPolicy

    def configure(config):  # noqa: ANN001, ANN202
        profile = config.profile.transition(execution=ExecutionPolicy(mode=mode))
        return config.transition(profile=profile)

    return configure


def _break_one_role(monkeypatch: pytest.MonkeyPatch, broken: str) -> None:
    """`broken` 역할의 모델 호출만 **체계적으로** 실패시킨다 (HTTP 422).

    **422인 것이 핵심이다.** 크기 거절(413과 컨텍스트 초과 문구의 400)은 러너가 **일부러**
    연속 폐기 카운터에 안 센다 — 그 주석이 *"이 후보에 대한 사실이지 run에 대한 사실이
    아니다"*라고 적는다. 그걸로 재면 차단기가 안 걸리는 것이 **맹점 때문인지 설계 때문인지 못
    가른다.** 크기가 아닌 400은 세지 않고 첫 건에서 멈추므로(D88) 역시 카운터를 못 잰다.
    422는 크기가 아니라 체계적 신호로 센다(`_DROPPABLE_STATUS` 주석).
    """
    original = FakeModelClient.complete

    def complete(  # noqa: ANN001
        self, *, role: Role, prompt: object, schema: object, history: object = ()
    ) -> object:
        if role.value == broken:
            raise ModelGatewayError(f"{broken} 체계적 고장(테스트)", status=422)
        return original(self, role=role, prompt=prompt, schema=schema, history=history)

    monkeypatch.setattr(FakeModelClient, "complete", complete)


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_a_systematically_broken_single_scout_trips_the_circuit_breaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """**대조군.** 단일 `scout`가 매번 422로 죽으면 차단기가 걸리고 run이 미완주가 된다.

    아래 풀 검사의 짝이다. 이것이 없으면 풀에서 차단기가 안 걸리는 것이 **팬아웃 때문인지
    주입 방식이 틀려서인지** 못 가른다 — 같은 고장·같은 상한·같은 fixture로 한쪽만 걸리는
    것을 보여야 팬아웃이 원인이라고 말할 수 있다.

    **두 모드 모두 돈다.** 풀 검사가 두 모드를 재는데 대조군이 한쪽만 재면, 다른 모드에서
    갈림이 실재한다는 것이 **증거 없이 주장**된다.
    """
    monkeypatch.setattr(runner, "_MAX_CONSECUTIVE_DROPS", 2)
    _break_one_role(monkeypatch, Role.SCOUT.value)

    configure = None if mode is None else _with_mode(mode)
    result, _ = _run(tmp_path, configure=configure)

    assert result.record.stopped_after is not None, (
        "단일 scout가 전량 422인데 차단기가 안 걸렸다 — 주입이 안 먹었다는 뜻이다"
    )
    assert not result.record.is_complete


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_a_broken_pool_member_trips_the_circuit_breaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """풀 멤버 하나가 전량 실패하면 차단기가 걸려 run이 **비교표에서 빠진다** (D68 수정).

    기제: **연속 폐기 카운터가 역할별이다.** 한 단계의 작업 단위가 target × 역할로 펼쳐지고
    `consecutive`가 역할을 키로 세므로, 고장난 멤버(`scout_boundary`)의 실패가 형제의 성공에
    지워지지 않고 쌓여 `_MAX_CONSECUTIVE_DROPS`에 닿는다. 그래서 `stopped_after`가 채워지고
    `is_complete`가 **False**가 된다 — Scout 커버리지 1/3이 빈 run이 정상 완주로 기록되던
    맹점(옛 `_does_not_trip_..._yet`)이 닫혔다.

    ⚠ **단일 `scout`과 대조**: `test_a_systematically_broken_single_scout_trips_the_circuit_breaker`
    가 같은 결과를 단일 역할에서 낸다. 풀에서도 같아진 것이 이 수정의 요점이다.
    """
    monkeypatch.setattr(runner, "_MAX_CONSECUTIVE_DROPS", 2)
    _break_one_role(monkeypatch, "scout_boundary")

    result, trace_path = _run(tmp_path, configure=_poolify(mode))

    assert result.record.stopped_after is not None, (
        "풀 멤버 하나가 전량 422인데 차단기가 안 걸렸다 — 역할별 카운터(D68)가 회귀했다. "
        "형제 멤버의 성공이 고장난 멤버의 연속 카운터를 지우고 있다"
    )
    assert not result.record.is_complete, (
        "차단기가 걸렸는데 is_complete가 참이다 — `stopped_after`가 곧 불완전이다(§6)"
    )
    assert "scout_boundary" not in {c.scout_member for c in result.candidates}, (
        "고장난 멤버가 후보를 냈다 — 주입이 안 먹었다"
    )

    # 폐기가 상한에 실제로 닿았는지 — 상한 미만이면 다른 이유로 멈춘 것이라 이 검사가 무의미하다.
    dropped = [
        e
        for e in _of(_events(trace_path), "target_skipped")
        if e["data"].get("reason") == _REASON_GATEWAY_ERROR
    ]
    assert len(dropped) >= runner._MAX_CONSECUTIVE_DROPS, (
        f"게이트웨이 폐기가 {len(dropped)}건뿐이라 상한({runner._MAX_CONSECUTIVE_DROPS})에 "
        "못 미친다 — fixture의 4단계 대상 수를 늘린다"
    )
    # #193 ① — 폐기 이벤트가 어느 자리에서 죽었는지(scout_member)를 싣는다.
    assert all(e["data"].get("scout_member") == "scout_boundary" for e in dropped), (
        "target_skipped에 scout_member가 없거나 다르다 — 고장난 것은 scout_boundary다(#193 ①)"
    )


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_pool_capsule_cache_hits_are_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """풀에서 accumulator 캐시 적중이 `cache` 이벤트로 실린다 (#193 ②).

    풀은 target당 캡슐을 첫 멤버가 만들고 2·3번 멤버는 `state.capsules`가 물려 준다. 그
    단락이 `retrieval.run()` **앞**이라 Retriever의 `on_cache`를 안 지나므로, 러너가 직접
    `record_cache(hit=True)`를 낸다 — 안 그러면 `cache` 이벤트가 target당 1건으로 줄어
    hit ratio가 낮게 보인다(실측 0.667 → 0.40). 이 검사는 그 적중이 이벤트에 실리는지 본다.
    """
    _, trace_path = _run(tmp_path, configure=_poolify(mode))
    cache = _of(_events(trace_path), "cache")
    assert cache, "cache 이벤트가 0 — on_cache 미배선"
    hits = [e for e in cache if e["data"]["hit"]]
    assert hits, "풀인데 캐시 적중 이벤트가 0 — accumulator 단락이 이벤트를 안 낸다(#193 ②)"
    targets = {e["data"]["key"] for e in cache}
    assert len(cache) > len(targets), (
        "cache 이벤트 수가 target 수와 같다 — accumulator 적중이 안 실렸다(#193 ② 회귀)"
    )
    # #192 조건② — 공유 캡슐이 어느 역할 카운터로 계산됐나가 적중 이벤트에 남는다.
    accumulator_hits = [e for e in hits if "counted_by" in e["data"]]
    assert accumulator_hits, (
        "적중 이벤트에 counted_by가 없다 — 공유 캡슐 회계 근거가 안 남는다(#192 ②)"
    )
    assert all(e["data"]["counted_by"] == "scout_memory" for e in accumulator_hits), (
        "공유 캡슐을 만든 것은 첫 멤버(scout_memory)인데 counted_by가 다르다"
    )


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_the_report_does_not_call_dropped_attempts_unanalyzed_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """풀에서 멤버 하나가 죽어도 **대상은 분석된다.** 리포트가 그것을 부정하면 안 된다.

    `state.dropped`는 `_drop` 호출 수이고 `_drop`은 **시도(`target × Scout 자리`)마다**
    불린다. 단일 `scout`에서는 자리가 하나라 시도 수 = target 수여서 「대상 N건」이 참이었다.
    **풀에서는 거짓이 된다** — 형제 멤버가 같은 대상을 보므로 시도가 죽어도 대상은 살아 있다.

    **실측으로 갈렸다**: `scout_boundary`만 전량 422일 때 대상 둘이 **둘 다 후보를 냈는데**
    리포트가 *"분석하지 못한 대상 … 2건"*을 실었다. `report.py`가 자기 주석에
    *"사람이 읽는 산출물에서 분모를 섞는 것이 trace에서 섞는 것보다 나쁘다"*라고 적어 둔
    바로 그 자리다.

    이 검사는 **수가 아니라 이름**을 고정한다 — 수를 시도 단위로 두는 것은 이 PR의 선택이고
    (「못 본 대상 수」를 따로 내는 것은 미해결로 등재했다), 그 선택이 사람에게 **거짓으로
    읽히지 않는 것**이 여기서 지킬 최소다.
    """
    monkeypatch.setattr(runner, "_MAX_CONSECUTIVE_DROPS", 99)
    _break_one_role(monkeypatch, "scout_boundary")

    result, _ = _run(tmp_path, configure=_poolify(mode))
    rendered = result.report.render()

    analyzed = {(c.location.file, c.location.symbol) for c in result.candidates}
    assert len(analyzed) >= 2, "대상이 하나도 분석 안 됐다 — 이 run은 이 검사의 사례가 아니다"
    assert result.report.dropped > 0, "폐기가 0건이면 이 검사가 아무것도 안 본다"

    assert "분석하지 못한 대상" not in rendered, (
        f"대상 {len(analyzed)}건이 전부 분석됐는데 리포트가 "
        f"「분석하지 못한 대상 {result.report.dropped}건」이라고 적는다 — "
        "시도 수를 대상 수로 부른다"
    )
    assert "시도" in rendered, (
        "폐기 수가 무엇의 수인지 리포트가 말하지 않는다 — 사람이 target 수로 읽는다"
    )


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_deferred_stays_a_target_count_and_is_not_labelled_as_attempts(
    tmp_path: Path, mode: str | None
) -> None:
    """`deferred`는 **대상** 수다 — `dropped`(시도)와 한 이름으로 묶으면 거짓이 된다.

    `candidate_max` 컷은 `_plan_targets`에서, 즉 **팬아웃 전에** 정해진다(`state.deferred =
    len(deferred)`). 그래서 풀에서도 대상 단위 그대로다. **`dropped`만 시도 단위다.**

    두 수를 한 라벨로 묶으면 어느 쪽이든 하나는 거짓이다:
    「대상」으로 부르면 `dropped`가 **분석된 대상을 못 봤다고 말하고**, 「시도」로 부르면
    `deferred`를 자리 수만큼 **과소**로 말한다. 이 검사는 후자를 막는다 — 앞의 것은
    `test_the_report_does_not_call_dropped_attempts_unanalyzed_targets`가 막는다.

    **이 조합을 보는 검사가 없었다.** 기존 deferred 검사는 단일 프로필이라 자리가 하나여서
    두 단위가 같아 보이고, 풀 검사는 `deferred`가 0인 시나리오였다. 그 사이로 샜다.
    """
    result, trace_path = _run(
        tmp_path, configure=_poolify(mode), candidate_max=1, before=_with_index()
    )
    rendered = result.report.render()

    assert result.report.deferred > 0, "넘긴 대상이 0이면 이 검사가 아무것도 안 본다"

    # 팬아웃이 이 수에 곱해지지 않았는지 본다. `_with_index()`라 인덱스 없는 artifact가
    # 0이므로 `계획 + 넘김 = 심볼 대상`이 성립한다 — 곱셈이 끼면 좌변이 커져 깨진다.
    planned = _plan(trace_path)
    assert planned["fallback_artifacts"] == 0, (
        "인덱스 없는 artifact가 있으면 아래 등식이 다른 이유로도 깨진다 — 픽스처 전제가 바뀌었다"
    )
    assert result.report.deferred + planned["planned"] == planned["symbol_targets"], (
        f"넘긴 대상({result.report.deferred}) + 계획({planned['planned']})이 "
        f"심볼 대상 수({planned['symbol_targets']})와 안 맞는다 — 팬아웃이 이 수에 곱해졌다"
    )

    # ⚠ **수가 아니라 라벨을 본다.** `dropped == deferred`인 픽스처에서 수로 단언하면
    # 렌더가 옳아도 부정 단언이 실패한다 — 그때 실패 메시지가 원인을 오도한다.
    line = next((ln for ln in rendered.splitlines() if "넘긴 대상" in ln), "")
    assert line, f"넘김·폐기 줄이 리포트에 없다 — 렌더: {rendered!r}"
    deferred_label, _, attempts_label = line.partition("·")
    assert f"넘긴 대상(예산 초과): {result.report.deferred}건" in deferred_label, (
        f"`deferred`({result.report.deferred})가 대상 라벨로 안 실린다 — 줄: {line!r}"
    )
    assert "시도" not in deferred_label, (
        f"`deferred`를 시도 라벨로 싣는다 — 풀에서 자리 수만큼 과소로 읽힌다: {deferred_label!r}"
    )
    assert "시도(대상 × Scout 자리" in attempts_label, (
        f"`dropped` 쪽이 시도 라벨을 안 든다 — 줄: {line!r}"
    )


def test_manifest_is_written_with_reproducibility_fields(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == result.record.run_id
    assert manifest["condition"] == CONDITION
    assert manifest["source_hash"].startswith("sha256:")
    assert manifest["thresholds"]["scout_threshold"] > 0
    assert manifest["finished_at"].endswith("Z")


def test_incomplete_manifest_fields_are_reported_not_faked(tmp_path: Path) -> None:
    """컨테이너 digest가 없으면 지어내지 않고 경고한다 (engineering-guide.md §5)."""
    result, _ = _run(tmp_path)

    assert result.record.tool_images == {}
    assert any("tool_images" in warning for warning in result.warnings)


def test_the_record_carries_the_compile_condition(tmp_path: Path) -> None:
    """컴파일 조건이 비교표에 나타나야 한다 (D32 / #74).

    D32: *"`RunRecord`에 없으므로 두 run을 나란히 놓았을 때 컴파일 조건이 같았는지 다른지가
    표에 나타나지 않는다."* 배선이 끊기면 **여기서만** 드러난다 — 어댑터는 제 값을 내고
    스키마는 필드를 갖고 있는데 그 사이가 안 이어진 상태가 전부 초록으로 통과한다.

    Fake로 돈 run이라 조건은 **비어 있다**. 빈 값은 *"기록했으나 컴파일한 도구가 없었다"*
    이고 `None`은 *"이 manifest는 남기지 않았다"*(옛 기록)다 — `model_sampling`의 관용구다.
    """
    result, _ = _run(tmp_path)

    assert result.record.compile_flags is not None
    assert result.record.compile_flags.std == {}


# --- 하네스 저장소 상태 기록 (P3) --------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _fresh_repo(root: Path) -> Path:
    """커밋 1개짜리 깨끗한 git 저장소."""
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("first", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


def test_clean_repo_is_recorded_as_not_dirty(tmp_path: Path) -> None:
    repo = _fresh_repo(tmp_path)

    commit, dirty, warnings = runner._repo_state(repo)

    assert len(commit) == 40
    assert dirty is False
    assert warnings == []


def test_dirty_repo_is_recorded(tmp_path: Path) -> None:
    """실측(2026-08-11): config/models.yaml·profiles.yaml을 고친 상태로 세 run이 돌았다.

    모델도 엔드포인트도 바뀐 실행인데 manifest만 보면 커밋이 그대로라 재현 가능해 보였다.

    dirty는 읽기 실패가 아니라 사실이므로 `_repo_state`는 경고하지 않는다 — "비교표에
    넣을 수 없다"는 판단은 `incomplete_fields`가 하고, 아래 별도 테스트가 그것을 본다.
    """
    repo = _fresh_repo(tmp_path)
    (repo / "a.txt").write_text("changed", encoding="utf-8")

    _, dirty, warnings = runner._repo_state(repo)

    assert dirty is True
    assert warnings == []


def test_untracked_file_also_counts_as_dirty(tmp_path: Path) -> None:
    """`git status --porcelain`은 미추적 파일도 낸다. 그것도 재현을 깨뜨린다."""
    repo = _fresh_repo(tmp_path)
    (repo / "scratch.yaml").write_text("local: true", encoding="utf-8")

    _, dirty, _ = runner._repo_state(repo)

    assert dirty is True


def test_non_repository_records_unknown_not_false(tmp_path: Path) -> None:
    """git이 없거나 저장소가 아니면 "확인 못 함"이다.

    불리언 두 값으로는 이 상태를 표현할 수 없다. `False`로 적으면 "깨끗하다"는
    적극적인 거짓말이 되므로 `None`을 쓴다.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    commit, dirty, warnings = runner._repo_state(plain)

    assert dirty is None
    assert commit == runner._UNKNOWN_COMMIT
    assert warnings


def test_dirty_and_unknown_both_reach_incomplete_fields(tmp_path: Path) -> None:
    """경고가 `incomplete_fields`를 통해 실제로 사용자에게 도달하는지 본다.

    `_repo_state`가 값을 잘 만들어도 그 값이 경고 목록에 연결되지 않으면 run은
    여전히 조용히 비교표에 들어간다.
    """
    result, _ = _run(tmp_path)
    record = result.record

    def repo_warnings(dirty: bool | None) -> list[str]:
        # `incomplete_fields`는 tool_images·kb_snapshot 경고도 함께 낸다. 목록이 비었는지만
        # 보면 repo_dirty 경고가 아예 없어도 통과한다 — 그래서 해당 경고만 골라낸다.
        found = runner.incomplete_fields(record.transition(repo_dirty=dirty))
        return [w for w in found if "repo_dirty" in w]

    assert repo_warnings(False) == []
    assert repo_warnings(True), "dirty한 run이 경고 없이 비교표에 들어간다"
    assert repo_warnings(None), "확인 못 한 run이 경고 없이 비교표에 들어간다"


def _checksum_warnings(record: RunRecord, served: dict[str, str]) -> list[str]:
    """checksum 자리표시자 경고만 골라낸다. 다른 경고가 섞이면 단언이 공허해진다.

    `served`는 `HarnessConfig.served_models()`다 — 커밋된 프로필이든 합성 config든 같은 자리다.
    """
    found = runner.incomplete_fields(record, served_models=served)
    return [w for w in found if "model_checksums" in w]


def test_fake_roles_do_not_warn_about_placeholder_checksums(tmp_path: Path) -> None:
    """Fake 모델의 checksum 0은 빠진 값이 아니라 **맞는 값**이다.

    경고하면 `dev_*` 프로필의 모든 run이 매번 같은 줄을 뱉고, 그러면 진짜 문제가 묻힌다 —
    `incomplete_fields`가 `tool_runner`를 목록에서 뺀 것과 같은 논법이다.
    """
    result, _ = _run(tmp_path)

    assert _checksum_warnings(result.record, load_config("dev_fake").served_models()) == []


def test_missing_run_labels_names_every_gap_the_operator_must_fill() -> None:
    """사람이 줘야 하는 세 값이 비면 **run이 시작하기 전에** 그 사실을 이름으로 말한다.

    셋 다 run 시점의 사실이라 사후에 정직하게 채울 수 없다 — CLAUDE.md §0이 "안 잰 값은
    0으로 두고 사유를 남긴다. 그럴듯한 값으로 채우지 않는다"고 못박은 자리다.
    `incomplete_fields()`가 device·kb_snapshot에 같은 판정을 하지만 그것은 run이 **끝난
    뒤**다(`_new_record`가 레코드를 만들 때 불린다).

    **`condition`은 값 비교로 판정할 수 없다.** 기본값 `B3prime@pipeline`은 파이프라인
    조건의 **정당한 값**이라, `== DEFAULT_CONDITION`으로 재면 그 라벨을 의도해서 준 run까지
    경고한다. 그래서 이 함수는 해석된 값이 아니라 **사람이 준 값**을 받는다 — `None`이
    "미지정"이다. 그 구분이 없으면 검사가 매 run 같은 줄을 뱉고 진짜 결여가 묻힌다.
    """
    gaps = runner.missing_run_labels(
        device=None, kb_snapshot=runner.DEFAULT_KB_SNAPSHOT, condition=None
    )
    assert [w for w in gaps if "device" in w], "장비를 안 준 run이 조용히 시작한다"
    assert [w for w in gaps if "kb_snapshot" in w]
    assert [w for w in gaps if "condition" in w], "조건을 안 준 run이 기본 라벨로 조용히 남는다"

    filled = runner.missing_run_labels(
        device="atom", kb_snapshot="kb-20260901", condition="B1prime@pipeline"
    )
    assert filled == []

    explicit = runner.missing_run_labels(
        device="atom", kb_snapshot="kb-20260901", condition=runner.DEFAULT_CONDITION
    )
    assert explicit == [], "기본값과 같은 라벨을 명시한 run까지 경고하면 검사가 무뎌진다"


def test_the_pre_run_check_and_incomplete_fields_use_the_same_sentence(tmp_path: Path) -> None:
    """같은 결여를 두 곳이 다른 문장으로 말하면 그 갈라짐이 곧 다음 결함이다.

    러너가 `unmeasured_gpu_warning`을 따로 뽑은 이유가 이것이고 그 docstring이 근거를 적어
    뒀다 — "러너가 문구를 따로 만들면 두 곳이 갈라진다 … 이 저장소가 반복해서 고쳐 온
    '선언과 실제의 괴리'가 정확히 그 형태다." run 전 검사와 run 후 판정도 같은 자리다.
    """
    result, _ = _run(tmp_path)
    record = result.record.transition(device=None, kb_snapshot=runner.DEFAULT_KB_SNAPSHOT)
    after = runner.incomplete_fields(record)

    before = runner.missing_run_labels(
        device=None, kb_snapshot=runner.DEFAULT_KB_SNAPSHOT, condition="B1prime@pipeline"
    )
    assert before, "run 전 검사가 아무 말도 하지 않는다"
    for sentence in before:
        assert sentence in after, f"run 전과 후가 다른 문장을 쓴다: {sentence!r}"


def test_run_names_the_gaps_before_it_claims_a_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """GPU를 쓰기 전에 듣는다. `cli._check_checksums`가 이미 그 논법을 적어 뒀다 —
    "5분(실서빙이면 그보다 훨씬 길다)을 쓰고 나서 '이 run은 비교표에 못 넣는다'를 듣는
    것보다 지금 듣는 편이 싸다."

    **`_claim_run_dir`보다 앞이어야 한다.** 그 뒤에 두면 아무것도 안 한 run이 디렉터리와
    번호를 남기고, 그 번호는 R7(`gc`가 지운 run_id를 같은 날 새 run이 재사용한다)이 서 있는
    자리와 같다. 그래서 이 테스트는 문구뿐 아니라 **순서**를 단언한다.
    """

    class _Stop(RuntimeError):
        """`_claim_run_dir`에 도달했다는 표시. 여기서 멈춰야 순서를 볼 수 있다."""

    def _boom(*_: object, **__: object) -> tuple[str, Path]:
        raise _Stop

    monkeypatch.setattr(cli, "_claim_run_dir", _boom)
    with pytest.raises(_Stop):
        cli.main(
            [
                "run", "--profile", "dev_fake", "--target", str(TARGET),
                "--seed", "20260901", "--artifacts-root", str(tmp_path),
            ]
        )

    err = capsys.readouterr().err
    assert "device" in err, "장비 결여를 run_dir을 잡기 전에 말하지 않는다"
    assert "condition" in err, "조건 결여를 run_dir을 잡기 전에 말하지 않는다"
    assert not list(tmp_path.iterdir()), "판정 전에 이미 디렉터리를 만들었다"


def test_the_blocking_messages_do_not_name_an_axis_the_pattern_rejects() -> None:
    """실서빙 run을 막는 문구가 **존재할 수 없는 조건 축**을 사유로 들지 않는다.

    **왜 필요한가.** `BLOCKING_WHEN_SERVING`의 키는 주석이 아니라 **운영자에게 나가는
    출력**이다 — `cli.py`가 그것을 찍고 exit 2로 거부한다. 그런데 그 문구가 드는 사유는
    `CONDITION_PATTERN`이 무엇을 받느냐에 달려 있고, **패턴은 움직인다.** 축이 하나
    없어지면 그 축을 근거로 대던 문구는 *일어날 수 없는 일*을 이유로 run을 막는다.

    > **사고 —** 2026-09-20에 2×2 축을 `CONDITION_PATTERN`에서 뺐는데
    > `_CONDITION_DEFAULTED`는 *"2×2 축으로 돌린 run이 pipeline 라벨로 남으면"*을 그대로
    > 들고 있었다. 같은 PR이 이 파일의 **docstring은 고쳤고** 프로덕션 문자열은 놓쳤다 —
    > 산문과 출력이 정면으로 어긋난 채 CI가 초록이었다(CLAUDE.md §2.3).

    **두 모양만 본다** — `N×N` 리터럴과 `@축`이다. 문구 전체를 패턴과 대조하면 일반 산문이
    전부 걸리고, 그러면 다음 사람이 이 검사를 지운다.

    > **사고 하나 더 — 2026-09-20에 위 사고의 거울상을 밟았다.** 첫 판은 「축」이라는 낱말
    > **앞의 토큰**만 봤다. 구멍이 셋이다 — ① 「축」이라는 낱말이 없거나 그 앞에 한글이
    > 끼면(`2×2 실험 축`) 통째로 놓치고, ② `BLOCKING_WHEN_SERVING`의 **값은 안 봤고**,
    > ③ `retriever 축` 같은 무관한 정상 문구를 막았다. 검출기를 위 두 축으로 갈아치우면서
    > **이 docstring의 그 문장을 안 고쳤다** — 이번엔 코드를 고치고 산문을 놓쳤다.
    >
    > ⚠ **그 구멍 목록도 한 번 틀렸다.** 4라운드 전까지 ②를 *"`2X2`를 놓친다"*로 적었는데,
    > 첫 판의 `[0-9A-Za-z×]+`는 `X`가 `[A-Za-z]`에 들어 있어 **`2X2 축`을 잡는다.**
    > 코드를 안 열고 앞 라운드의 인라인 주석을 옮긴 것이다([CLAUDE.md](../CLAUDE.md) §2.1).
    """
    # 두 모양을 본다. **`N×N` 리터럴**이 실제로 터진 것이고(2×2·2x2·2X2·2 × 2·전각),
    # **`@축`**은 패턴의 어휘와 직접 대조한다. 「축」 앞 토큰만 보던 첫 판은 구멍이 셋이었다 —
    # `2×2 실험 축`처럼 한글이 끼면 끊기고, `2X2`를 놓치고, `retriever 축` 같은 무관한
    # 정상 문구를 막았다(2라운드 리뷰가 주입으로 전부 재현).
    # 숫자 경계를 둔다 — 없으면 `8192 × 2회`·`512x256` 같은 곱셈·치수 표기가 축으로 읽힌다.
    matrix_axis = re.compile(r"(?<![0-9０-９])[2２]\s*[×xX✕✖╳]\s*[2２](?![0-9０-９])")
    # **어휘를 패턴에서 뽑고 조건 라벨에 앵커한다.** 예전에는 `축 not in CONDITION_PATTERN`,
    # 즉 정규식 **소스 문자열에 대한 부분문자열 검사**였다 — `@B`·`@line`은 소스에 우연히
    # 들어 있어 통과하고 `ops@example.com`은 `example`을 축으로 신고했다(3라운드 리뷰 실측).
    labels = re.findall(r"\(([^)]+)\)", CONDITION_PATTERN)[0]
    allowed = set(re.findall(r"@(\w+)", CONDITION_PATTERN))
    # 두 형태를 받는다 — **조건 라벨이 붙은 `B1@축`**과 **맨 `@축`**. 라벨형만 보면
    # 장래에 다른 축이 폐기될 때의 맨 언급(`@ensemble`)을 통째로 잃는다 —
    # *"오탐보다 미탐이 나쁘다"*. 맨 형태는 앞이 낱말이 아닐 때만 받아 `ops@example.com`을 뺀다.
    suffixed_axis = re.compile(rf"(?:{labels})@(\w+)|(?<![\w.])@(\w+)")

    def rejected(text: str) -> list[str]:
        found = [m.group(0) for m in matrix_axis.finditer(text)]
        found += [
            axis
            for m in suffixed_axis.finditer(text)
            for axis in [m.group(1) or m.group(2)]
            if axis not in allowed
        ]
        return found

    # **오염 핀.** 아래가 없으면 두 문구가 깨끗해지는 순간 이 단언이 빈 리스트끼리 비교하는
    # 공회전이 되고, 검출기가 고장 나도 영영 초록이다.
    # ⚠ **검출기가 둘이므로 핀도 둘이다.** 첫 판은 `N×N` 핀 하나뿐이라 `@축` 쪽을 무력화해도
    # 통과했다 — 절반만 붙드는 핀은 안 붙드는 것과 같다(3라운드 리뷰가 변이로 재현).
    assert rejected("2×2 축으로 돌린 run이 pipeline 라벨로 남으면"), "N×N 검출기가 죽었다"
    assert rejected("condition이 B1@ensemble로 남는다"), "라벨형 @축 검출기가 죽었다"
    assert rejected("조건 축이 @ensemble였다"), "맨 @축 검출기가 죽었다"
    assert not rejected(f"조건은 {runner.DEFAULT_CONDITION} 하나다"), "검출기가 정상 조건을 잡는다"
    assert not rejected("문의는 ops@example.com 으로"), "검출기가 이메일을 축으로 읽는다"
    assert not rejected("출력 상한 8192 × 2회"), "검출기가 곱셈 표기를 축으로 읽는다"

    offenders = [
        f"{axis!r}: {text}"
        for gap, flag in runner.BLOCKING_WHEN_SERVING.items()
        for text in (gap, flag)  # 키(문구)와 값(플래그명) 둘 다 본다
        for axis in rejected(text)
    ]
    assert offenders == [], (
        "차단 문구가 CONDITION_PATTERN이 받지 않는 축을 사유로 든다 — 운영자가 "
        "존재할 수 없는 시나리오를 이유로 거부당한다.\n" + "\n".join(offenders)
    )


def test_a_serving_profile_refuses_to_start_without_the_labels(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    empty_slices: Path,
) -> None:
    """**실서빙 run에서는 경고로 부족하다** (@sumin0218 리뷰 #119).

    경고 한 줄은 긴 run의 출력에 묻힌다. 그리고 그 run은 시작하기도 전에 이미 못 쓰는
    것이 확정돼 있다 — `incomplete_fields()`가 `device`가 비면 "모델 비교표에서
    제외한다"고 판정하고, `condition`이 없으면 **어느 조건으로 돈 run인지 산출물로 알 수
    없다**(`schemas/run.py`). 그러니 GPU를 태우기 전에 **막는다.**

    ⚠ **2026-09-20 — 이 문장의 근거가 바뀌었다.** 예전에는 *"`condition`이 기본값으로 굳으면
    2×2 축으로 돌린 run이 pipeline 라벨을 달아 비교표가 섞인다"*였는데, 2×2가 폐기되고
    `CONDITION_PATTERN`이 `@pipeline`만 받게 되어 **그 혼동은 일어날 수 없다.** 막는 이유는
    이제 「축이 섞인다」가 아니라 「조건이 기록되지 않는다」다. 검사 자체는 그대로 유효하다.

    **판별 기준은 `production`이 아니라 `served_models()`다.** 저장소의 프로필이 전부
    `production: false`라 그 필드로는 아무것도 갈리지 않는다(직접 확인). `served_models()`는
    dev 프로필을 0으로, 실서빙 프로필을 1개 이상으로 가른다. **개수는 적지 않는다** —
    프로필이 늘고 줄 때마다 틀리는 서술이 된다.

    `kb_snapshot`은 막지 않는다 — `kb-none`은 아직 KB가 없다는 정직한 표기이고
    `incomplete_fields()`도 그것만은 "제외한다"고 말하지 않는다.
    """
    _refuse_sockets(monkeypatch)
    code = cli.main(
        [
            "run", "--profile", "harness_pool", "--target", str(TARGET),
            "--slices", str(empty_slices),
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code == 2, "실서빙 프로필이 라벨 없이 그대로 시작한다"
    assert _errors(err), "거부했는데 [ERROR] 줄이 없다"
    assert not list(tmp_path.iterdir()), "거부된 run이 디렉터리를 남겼다"


def _refuse_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 테스트가 네트워크를 치면 즉시 죽는다.

    실서빙 프로필을 겨눈 첫 `run` 테스트다(처음엔 `b1_single` — 2026-09-24에 지웠고 지금은
    `harness_pool`). 게이트가 회귀해 run이 실제로 시작되면 로스터 엔드포인트로 나가고
    요청 타임아웃(모델이 선언한 `request_timeout_s`, 선언이 없으면 `_TIMEOUT_S`)까지 매달릴 수
    있다 — CI에서 "느리다"로 보이지 "게이트가 사라졌다"로 안 보인다. 같은 그물을
    `test_doctor_never_opens_a_socket`이 이미 쓴다.
    """

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("게이트가 막았어야 하는 run이 소켓을 열었다")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


def _errors(stderr: str) -> list[str]:
    """`[ERROR]` 줄만 골라낸다.

    **`"device" in err`로 단언하면 안 된다.** 그 낱말은 `[WARN ] 시작 전 —` 줄에서도 나오고
    그쪽은 **차단 여부와 무관하게 모든 결여에 대해 찍힌다**(`cli.run`). WARN 문자열을 보는
    단언은 게이트가 절반 사라져도 통과한다 — 아래 두 테스트 docstring의 실측이 그것이다.
    """
    return [line for line in stderr.splitlines() if line.startswith("[ERROR]")]


@pytest.fixture
def empty_slices(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """비었지만 믿을 수 있는 `slices.json` — `load_slices`를 통과하고 타깃은 0개다.

    **실서빙 게이트 테스트가 제 게이트만 보게 한다.** `harness_pool`의 Scout 세 자리는 슬라이스
    입력(v3)이라 `--slices`가 없으면 슬라이스 가드도 exit 2로 막는다 — 그러면 실서빙 게이트가
    사라져도 거부 테스트가 초록이다. `tmp_path` 밖에 두는 것은 그 테스트들이 「run 디렉터리를
    안 남겼다」를 `tmp_path`가 비었는지로 보기 때문이다.
    """
    path = tmp_path_factory.mktemp("slices") / "slices.json"
    path.write_text(
        json.dumps(
            {
                "slice_version": "slice-v2",
                "status": "ok",
                "joern_engine_config": {"maxCallDepth": 2},
                "slices": [],
                "candidate_manifest": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_serving_profile_refuses_when_only_the_device_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture, empty_slices: Path
) -> None:
    """**게이트의 절반씩을 따로 잠근다.** 둘을 한 번에 뺀 케이스만 보면 절반이 무검사다.

    뮤테이션으로 실측했다 — `BLOCKING_WHEN_SERVING`에서 한 원소를 빼도 게이트 테스트가
    전부 초록이었다(`9 passed`). 나머지 하나가 여전히 차단해 `code != 0`이 유지되기
    때문이고, 그때 단언하던 `"device" in err`·`"condition" in err`는 차단과 무관한 WARN
    줄에서 왔다. 통째로 비울 때만 1건이 깨졌다 — **CLAUDE.md §5의 "테스트가 통과했으니
    맞겠지"가 그대로 재현된 자리다.**

    이 테스트는 `_DEVICE_MISSING`이 목록에서 빠지는 순간 죽는다 — condition·kb_snapshot을
    다 줬으므로 남는 결여가 device 하나다.
    """
    code = cli.main(
        [
            "run", "--profile", "harness_pool", "--target", str(TARGET),
            "--condition", "B1@pipeline", "--kb-snapshot", "kb-20260901",
            "--slices", str(empty_slices),
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code == 2, "장비만 빠졌을 때 실서빙 run이 그대로 시작한다"
    assert _errors(err), "거부했는데 [ERROR] 줄이 없다"
    assert not list(tmp_path.iterdir()), "거부된 run이 디렉터리를 남겼다"


def test_a_serving_profile_refuses_when_only_the_condition_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture, empty_slices: Path
) -> None:
    """위와 짝. `_CONDITION_DEFAULTED`가 목록에서 빠지는 순간 죽는다.

    조건 라벨만 빠진 run이 가장 조용한 사고다 — 죽지도 않고 **기본 라벨로 기록된다.**
    2026-08-25·08-26 ATOM run 둘이 정확히 그 모양이었다.
    """
    code = cli.main(
        [
            "run", "--profile", "harness_pool", "--target", str(TARGET),
            "--device", "atom", "--kb-snapshot", "kb-20260901",
            "--slices", str(empty_slices),
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code == 2, "조건만 빠졌을 때 실서빙 run이 그대로 시작한다"
    assert _errors(err), "거부했는데 [ERROR] 줄이 없다"
    assert not list(tmp_path.iterdir()), "거부된 run이 디렉터리를 남겼다"


def test_a_malformed_condition_is_refused_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """형식에 안 맞는 라벨도 **시작 전에** 막는다 — 결여만 막으면 절반이다.

    `RunRecord.condition`이 `CONDITION_PATTERN`으로 같은 값을 거부하지만 그 판정은
    `_new_record`에서, 즉 **0단계를 지난 뒤**에 일어난다. 그때는 이미 run 디렉터리와
    번호가 나갔고 빈 디렉터리가 남는다 — R7(`gc`가 지운 run_id를 같은 날 새 run이
    재사용한다)이 서 있는 자리와 같다.

    **선존재 결함이다**(`origin/main`에서도 `exit=1` · 디렉터리 1건 잔류). 이 PR이 만든 것이
    아니지만, 이 PR이 세운 게이트가 그 구멍을 덮지 않으면 "거부는 `_claim_run_dir` 앞이라
    run 번호도 안 쓴다"는 이 PR의 주장이 절반만 참이 된다.

    **형식은 문자열만 보면 안다** — 프로필도 엔드포인트도 필요 없으므로 dev 프로필에서도
    막는다. 이건 정책 선택이 아니라 반드시 죽을 값에 대한 빠른 실패다.

    `B3prime_large@pipeline`을 고른 것은 `CONDITION_PATTERN`이 받는 넷(`B1`·`B1prime`·`B3`·
    `B3prime`) **밖의** 이름이면서 축 접미사(`@pipeline`)는 올바른 값이라서다 — "조건 이름이
    틀렸다"와 "축 표기가 틀렸다"를 가르는 자리이고, 그 조합을 사람이 실제로 친다.
    """
    code = cli.main(
        [
            "run", "--profile", "dev_fake", "--target", str(TARGET),
            "--condition", "B3prime_large@pipeline", "--device", "atom",
            "--kb-snapshot", "kb-20260901",
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code != 0, "형식에 안 맞는 라벨로 run이 시작했다"
    assert "B3prime_large@pipeline" in err, "어느 값이 문제인지 말하지 않는다"
    assert not list(tmp_path.iterdir()), "거부된 run이 디렉터리를 남겼다"


def test_a_dry_run_is_not_blocked_by_the_serving_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """모델을 한 번도 안 부르는 run은 막지 않는다 — 게이트의 근거가 "비용"이기 때문이다.

    `--dry-run`은 `DRY_RUN_LAST_STAGE`(RETRIEVAL)에서 끊기고 Scout은 그 **다음** 단계다.
    게다가 이 테스트가 쓰는 실서빙 프로필은 `retriever: fake`라 0~3단계에서 실서빙
    클라이언트가 한 번도 불리지 않는다 — GPU 비용이 0이다.

    **막으면 §0을 게이트가 유도한다.** 통과시키려고 `--device atom --condition B3prime@…`을
    지어내 붙이면, 아무것도 재지 않은 dry-run의 매니페스트에 실서빙 장비·조건 라벨이 박힌다.
    "그럴듯한 값으로 채우지 않는다"를 검사가 어기게 만드는 셈이다.

    경고는 그대로 낸다 — 알려 주는 것과 막는 것은 다르다.

    **슬라이스 가드도 여기서 선다.** `--slices`를 안 주지만 Scout에 닿지 않으므로 슬라이스
    입력 프롬프트(v3)를 부르지 않는다 — 가드가 「모델에 닿는가」를 안 보게 되면 이 run이
    exit 2로 걸린다(2026-09-25 변이로 확인).
    """
    code = cli.main(
        [
            "run", "--profile", "harness_pool", "--target", str(TARGET), "--dry-run",
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code == 0, "모델을 안 부르는 dry-run이 게이트에 막혔다"
    assert "시작 전 —" in err, "막지 않더라도 결여는 말해야 한다"
    assert not _errors(err), "dry-run에 [ERROR]가 붙었다"


def test_a_blank_label_counts_as_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture, empty_slices: Path
) -> None:
    """빈 문자열은 "줬다"가 아니다 — `--device ""`가 게이트와 사후 판정을 **둘 다** 통과했다.

    실측: `--device "" --condition B1@pipeline --kb-snapshot kb-x`로 돌면 exit 0으로 완주하고
    `run_manifest.json`의 `device`가 빈 문자열로 남는다. `missing_run_labels`는
    `device is None`만 보고 `incomplete_fields`도 `record.device is None`만 봐서, 라벨 없는
    run이 **"정상 run"으로 기록된다** — 이 PR이 막으려던 것보다 더 조용한 실패다.

    두 곳을 **함께** 좁힌다. 갈라지면 안 된다는 것이 이 PR 자신의 논거다.
    """
    code = cli.main(
        [
            "run", "--profile", "harness_pool", "--target", str(TARGET),
            "--device", "   ", "--condition", "B1@pipeline", "--kb-snapshot", "kb-20260901",
            "--slices", str(empty_slices),
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code == 2, "공백뿐인 장비 라벨이 실서빙 run을 통과시킨다"
    assert _errors(err), "거부했는데 [ERROR] 줄이 없다"
    assert not list(tmp_path.iterdir()), "거부된 run이 디렉터리를 남겼다"


def test_the_default_condition_matches_the_pattern() -> None:
    """`DEFAULT_CONDITION`(러너)과 `CONDITION_PATTERN`(스키마)이 갈리면 안 된다.

    전자는 `--condition`을 안 준 run에 **실제로 기록되는 값**이고 후자는 `RunRecord`가
    그 값을 검증하는 정규식이다. 두 상수가 다른 파일에 있어 한쪽만 바뀌면, 기본값으로 돈
    모든 run이 0단계 뒤에 죽는다 — `malformed_condition()`이 `None`을 통과시키므로
    시작 전 검사도 못 잡는다.
    """
    assert re.fullmatch(CONDITION_PATTERN, runner.DEFAULT_CONDITION), (
        f"기본 조건 라벨 {runner.DEFAULT_CONDITION!r}이 RunRecord의 패턴을 통과하지 못한다"
    )


def test_a_fake_profile_still_only_warns(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """dev 스모크는 장비 라벨 없이 도는 것이 정상이라 막으면 그쪽이 죽는다.

    이 테스트가 없으면 위 게이트가 조용히 전 프로필로 넓어져도 아무도 모른다 —
    `_check_checksums`가 Fake 역할을 세지 않는 것과 같은 논법이다.
    """
    code = cli.main(
        [
            "run", "--profile", "dev_fake", "--target", str(TARGET),
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    assert code == 0, "Fake 프로필 스모크가 막혔다"
    assert "시작 전 —" in capsys.readouterr().err, "경고까지 사라지면 안 된다"
    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]

    # **기록되는 값을 리터럴로 박는다.** `--condition`을 안 준 run에 실제로 무엇이 남는지가
    # 이 PR의 계약이다(argparse 기본값을 없애고 `run()`이 해석한다) — 상수를 참조하면
    # 그 값이 바뀌어도 통과한다.
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["condition"] == "B3prime@pipeline"


def test_a_serving_profile_with_the_labels_passes_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    empty_slices: Path,
) -> None:
    """라벨을 주면 게이트를 통과한다 — 막는 것이 목적이 아니라 **묻는** 것이 목적이다.

    `_claim_run_dir`에 도달했다는 것으로 통과를 본다. 그 뒤는 실서빙 엔드포인트가 필요해
    이 테스트의 범위가 아니다.

    `--slices`도 준다 — Scout 세 자리가 슬라이스 입력(v3)이라 없으면 슬라이스 가드가 먼저
    막는다(바로 아래 테스트). 그래서 이 테스트가 **두 게이트의 양성 대조**다 — 가드가
    `--slices`를 줘도 막도록 넓어지면 여기서 걸린다.
    """

    class _Stop(RuntimeError):
        """게이트를 지나 run_dir 확보까지 왔다는 표시."""

    def _boom(*_: object, **__: object) -> tuple[str, Path]:
        raise _Stop

    monkeypatch.setattr(cli, "_claim_run_dir", _boom)
    with pytest.raises(_Stop):
        cli.main(
            [
                "run", "--profile", "harness_pool", "--target", str(TARGET),
                "--condition", "B1prime@pipeline", "--device", "atom",
                "--slices", str(empty_slices),
                "--seed", "20260901", "--artifacts-root", str(tmp_path),
            ]
        )

    err = capsys.readouterr().err
    assert not _errors(err), "라벨을 다 줬는데 거부됐다"
    # `kb_snapshot`은 막지 않는다 — 경고만 나는 것이 계약이다.
    assert "kb_snapshot" in err, "막지 않는 결여의 경고까지 사라졌다"


def test_a_slice_input_profile_refuses_to_start_without_slices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """슬라이스 입력 프롬프트(v3 Scout)를 부르는 run은 `--slices` 없이 시작하지 않는다
    (ADR 0012 결정 1).

    v3는 `SOURCE`를 슬라이스(원본 줄번호 gutter·파일 배너)로 정의하는데, 슬라이스가 없으면
    그 자리에 심볼 본문이 가고 **오류 없이** 어긋난다(#278). 라벨은 다 준다 — 남는 결여가
    `--slices` 하나여야 실서빙 게이트가 아니라 이 가드가 막았다는 것이 선다. 그래서 거부
    줄이 `--slices`를 대는지까지 본다(실서빙 게이트의 거부 줄은 그 이름을 안 댄다).

    `execute`를 막아 둔다. 가드가 사라지면 이 run은 실서빙 프로필로 도구 컨테이너와
    엔드포인트에 나간다 — 빨간 불이 느린 테스트로 보이면 안 된다(`_refuse_sockets`와 같은 뜻).
    """

    class _Started(RuntimeError):
        """가드를 지나 run 디렉터리를 잡고 실행까지 들어왔다."""

    def _boom(*_: object, **__: object) -> None:
        raise _Started

    monkeypatch.setattr(cli, "execute", _boom)
    code = cli.main(
        [
            "run", "--profile", "harness_pool", "--target", str(TARGET),
            "--condition", "B1@pipeline", "--device", "atom", "--kb-snapshot", "kb-20260901",
            "--seed", "20260901", "--artifacts-root", str(tmp_path),
        ]
    )

    err = capsys.readouterr().err
    assert code == 2, "슬라이스 입력 프롬프트를 부르는 run이 --slices 없이 시작한다"
    assert [line for line in _errors(err) if "--slices" in line], "거부 줄이 --slices를 안 댄다"
    assert not list(tmp_path.iterdir()), "거부된 run이 디렉터리를 남겼다"


def test_a_served_role_with_a_placeholder_checksum_is_named(tmp_path: Path) -> None:
    """실서빙 역할의 0은 경고하고 **어느 역할의 어느 model key인지**를 담는다.

    이름이 없으면 역할이 다섯인 프로필에서 어디를 채워야 하는지 알 수 없다.
    """
    result, _ = _run(tmp_path)
    served = load_config("harness_pool").served_models()
    # dev_fake로 돈 run의 기록을 갈아 끼운다. 이 검사가 보는 것은 "자리표시자 digest +
    # 실서빙 역할"이라는 조합 하나뿐이다.
    record = result.record.transition(model_checksums=dict.fromkeys(served, PLACEHOLDER_DIGEST))

    warnings = _checksum_warnings(record, served)

    assert warnings, "실서빙 역할이 전부 자리표시자인데 경고가 없다"
    assert "auditor(qwen3.8-27b)" in warnings[0]
    assert "비교표에서 제외" in warnings[0]


def test_a_mixed_profile_warns_only_about_its_served_role(tmp_path: Path) -> None:
    """scout만 실서빙이고 나머지는 Fake인 프로필 — 옛 `single_gpu_12g`의 모양(2026-09-24 삭제,
    지금은 `tests/conftest.py`의 합성 config).

    프로필 단위로 켜고 끄면 이 프로필에서 실서빙 역할을 통째로 놓치거나(끄면) Fake 네 개를
    헛되이 경고한다(켜면). 판정이 역할별이어야 하는 이유가 정확히 이 프로필이다.
    """
    result, _ = _run(tmp_path)
    served = served_scout_config().served_models()
    assert served == {"scout": SERVED_SCOUT_KEY}, "합성 config가 바뀌면 이 검사의 전제가 무너진다"

    record = result.record.transition(
        model_checksums={"scout": PLACEHOLDER_DIGEST, "auditor": PLACEHOLDER_DIGEST}
    )
    warnings = _checksum_warnings(record, served)

    assert warnings and f"scout({SERVED_SCOUT_KEY})" in warnings[0]
    assert "auditor" not in warnings[0], "Fake 역할이 경고에 섞였다"


def test_manifest_carries_the_repo_dirty_flag(tmp_path: Path) -> None:
    """기록이 manifest까지 실제로 흘러가는지 본다 — 필드가 빠지면 재현 판단이 불가능하다."""
    _, trace_path = _run(tmp_path)

    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert "repo_dirty" in manifest
    assert manifest["repo_dirty"] in (True, False, None)


# --- 2. 재현성 --------------------------------------------------------------


def test_two_runs_are_byte_identical_after_dropping_volatile_fields(tmp_path: Path) -> None:
    """재현성 기준 그대로: ts·wall_ms만 제거하면 두 실행의 trace가 바이트 동일하다."""
    _, first = _run(tmp_path, "a")
    _, second = _run(tmp_path, "b")

    assert _canonical(first) == _canonical(second)
    assert _canonical(first) != b""  # 빈 파일끼리 비교해 통과하는 것을 막는다


def test_trace_actually_carries_the_volatile_fields(tmp_path: Path) -> None:
    """위 비교가 공허하지 않다는 증거 — 제거 대상이 실제로 파일에 있다."""
    _, first = _run(tmp_path, "a")

    assert all("ts" in json.loads(line) for line in first.read_text("utf-8").splitlines())


# --- 2b. 채점기(oracle) — 운영 배선을 끝까지 돌려 본다 (#386) ------------------------------------
#
# 아래는 `cli.execute` → `build_deps` → `run_pipeline` → `RunContext`를 **진짜로** 지난다 —
# `tests/test_oracle_wiring.py`는 `ctx.oracle`을 직접 꽂아 러너 함수를 보므로 그 배선이 끊겨도
# 초록이다. 후보 수가 갈림길이다(후보 N개 ≠ 최종 1개를 가르는 것이 이 PR의 차단 결함이다):
# 기본 타깃은 8단계에 **후보가 하나** 오고(그 하나가 최종이다), `_with_index()`로 심볼 타깃을
# 만들면 **후보가 둘** 온다.
#
# ⏵ **2026-10-04 — `dev_fake`의 후보 둘은 PoC 바이트가 같다**(Fake PoV는 결정론이라 finding이
# 달라도 같은 68바이트를 낸다 — 실측 run이 제출 8건에 고유 PoC 1개였던 것과 같은 모양이다).
# 그래서 지정이 **접어** `sole_poc`을 내고 `fix` 다리가 한 번 돈다 — 앞 판은 그것을 `grade_tie`로
# 거부해 왕복이 0번이었다. 🔴 **그 접기 때문에 이 파일은 `top_grade`를 끝까지 못 보인다** —
# 바이트가 같으면 어느 후보를 지정해도 제출이 같아 라벨이 `sole_poc`이다. 등급이 실제로
# **읽히는지**는 아래 둘째 검사의 `candidate_grades`·리포트 등급이 묶고, 사슬의 단계별 라벨은
# `tests/test_oracle_wiring.py` 끝 절이 직접 호출로 묶는다.


def _oracle_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e["kind"].startswith("oracle")]


def test_several_candidates_with_the_same_bytes_are_folded_and_the_fix_leg_runs_once(
    tmp_path: Path,
) -> None:
    """운영 배선을 지나 대역이 불린다. 후보 둘 모두 vul로 나가고(바이트는 run이 남긴 PoC
    사이드카 `pov_poc/NNNN.poc`와 **내용으로** 견준다), **두 PoC가 바이트까지 같아** 지정이
    접어 `sole_poc`을 낸다 - fix 다리·판정 조회가 정확히 한 번 돈다.

    ⏵ **2026-10-04 - 이 검사가 뒤집힌 자리다.** 앞 판은 「지정 안 함 · fix 0번 ·
    `grade_tie`」를 물었다. 어느 후보를 지정해도 **제출될 바이트가 같은데** 거부한 것이고,
    그것이 실측 run(제출 8 = 고유 PoC 1)의 점수를 0으로 만든 규칙이다. 접기의 **전제**를 이
    검사가 직접 센다 - 두 사이드카 바이트가 같지 않으면 아래 단언이 전부 다른 사건이다."""
    oracle = FakeOracle()

    result, trace_path = _run(tmp_path, before=_with_index(), oracle=oracle)

    events = _events(trace_path)
    generated = _of(events, "pov_generated", stage=int(Stage.VALIDATE))
    assert len(generated) == 2, "8단계에 finding이 둘 안 왔다 - 이 검사의 전제가 깨졌다"
    # 동점의 **원인**을 문다: Fake `_run_repro`는 소스 존재만 봐 재현이 둘 다 성공하고, 8단계가
    # 둘 다 E4로 올린다 - 그래서 최댓값이 동점이다. (전 판은 지정이 그 E4를 못 봐 E3 동점이었다.)
    grades = {e["data"]["grade"] for e in _of(events, "gate_eval", stage=int(Stage.VALIDATE))}
    assert grades == {"E4"}, "동점의 전제(8단계 등급이 둘 다 E4)가 깨졌다"
    sidecars = sorted((trace_path.parent / "pov_poc").glob("*.poc"))
    bodies = [p.read_bytes() for p in sidecars]
    assert bodies == oracle.vul_calls
    assert len(set(bodies)) == 1, "접기의 전제(두 PoC가 같은 바이트)가 깨졌다"
    assert len(_of(events, "oracle_submitted")) == 2
    assert oracle.final_calls == [(bodies[0], oracle.vul_results[0])]
    (final,) = _of(events, "oracle_final")
    assert final["subject_id"] == "find_0001"
    assert (final["data"]["status"], final["data"]["reason"]) == ("scored", None)
    assert final["data"]["designation"] == "sole_poc"
    assert result.record.oracle == {
        "vul_submitted": 2, "vul_failed": 0, "final": "scored",
        "final_reason": None, "record": "oracle/verdict.json",
    }  # fmt: skip
    assert not any("동점" in w for w in result.warnings)
    sidecar = json.loads((trace_path.parent / "oracle" / "verdict.json").read_text("utf-8"))
    assert sidecar["final"]["candidate_grades"] == {"find_0001": "E4", "find_0002": "E4"}
    assert result.record.is_complete  # 추가형 - run의 완주를 건드리지 않는다


class _OracleThatRatesTheLoserHigher(FakeOracle):
    """둘째 `vul` 제출에만 크래시 코드를 돌려주는 대역 - 채점기 값으로 고르면 **둘째**가 이긴다.

    규칙이 내부 등급이면 채점기가 둘째를 더 좋게 말해도 지정은 안 흔들린다. 지정이 그 값을 읽는
    변이(`response_exit_code`가 큰 쪽 · 크래시인 쪽)는 이 대역에서 첫째 대신 둘째를 고른다.
    """

    def submit_vul(self, poc: bytes):  # noqa: ANN201
        self._vul_exit_code = 0 if not self.vul_calls else 139
        return super().submit_vul(poc)


def test_the_designation_sees_the_e4_the_validate_stage_gave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 후보가 둘이고 **재현이 한 쪽만 성공하면** 지정은 그쪽(E4)을 고른다.

    전 판은 지정이 보는 등급이 8단계 `validate.run`의 런타임 Evidence(`produced`)를 버려 E4를
    못 봤다 - 후보가 둘 이상이면 어느 run에서나 **E3 동점**이라 `top_grade`가 도달 불가였다
    (적대적 리뷰 2026-10-04, `dev_fake` 실측: 8단계 `gate_eval`은 find_0001 E4 · find_0002 E3인데
    지정이 받은 `grades`는 E3 · E3).

    이 검사의 전제는 **8단계 `gate_eval`이 둘을 가른다**는 것이다 - 안 갈라지면 아래 단언이 전부
    공허하다. 지정 근거는 그 `gate_eval`과 **같은 등급**에서 나와야 한다. 그리고 지정은 채점기
    값을 안 읽는다 - 대역이 진 쪽(find_0002)에 더 센 크래시 코드를 돌려줘도 이긴 쪽이 안 바뀐다.

    ⚠ **`designation`은 `top_grade`가 아니라 `sole_poc`이다**(⏵ 2026-10-04) - `dev_fake`의 두
    PoC가 **바이트까지 같아** 지정이 먼저 접고, 접어서 하나면 「고를 것이 없었다」가 더 센
    사실이다(어느 후보를 지정해도 제출이 같다). 그래서 **이 검사는 라벨로 등급을 묶지 못한다**
    — 묶는 것은 아래 `candidate_grades`와 리포트 등급이고, 라벨 쪽은
    `tests/test_oracle_wiring.py`가 직접 호출로 묶는다. ⚠ 같은 이유로 이긴 쪽이 `find_0001`인
    것은 등급 때문인지 `finding_id` 꼬리 때문인지 **이 검사만으로는 안 갈린다**.
    """
    real_repro = FakeToolRunner._run_repro
    asan_calls: list[str] = []

    def second_asan_fails(self, tool, kind, args, workdir):  # noqa: ANN001, ANN202
        if tool == "asan":
            asan_calls.append(tool)
            if len(asan_calls) == 2:
                return self.make_evidence(
                    tool, kind, args, ok=False, exit_code=2, error="재현 실패(검사가 심었다)"
                )
        return real_repro(self, tool, kind, args, workdir)

    monkeypatch.setattr(FakeToolRunner, "_run_repro", second_asan_fails)
    oracle = _OracleThatRatesTheLoserHigher()

    result, trace_path = _run(tmp_path, before=_with_index(), oracle=oracle)

    events = _events(trace_path)
    validate_grades = {
        e["subject_id"]: e["data"]["grade"] for e in _of(events, "gate_eval", stage=Stage.VALIDATE)
    }
    assert validate_grades == {"find_0001": "E4", "find_0002": "E3"}, "8단계가 안 갈랐다(전제)"
    assert len(oracle.vul_calls) == 2
    assert len(oracle.vul_results) == 2
    (final,) = _of(events, "oracle_final")
    assert final["subject_id"] == "find_0001"
    assert (final["data"]["status"], final["data"]["designation"]) == ("scored", "sole_poc")
    assert oracle.final_calls == [(oracle.vul_calls[0], oracle.vul_results[0])]
    # 지정이 **읽은** 등급이 장부에 남고 8단계 `gate_eval`과 같다 - 어긋나면 이 줄이 빨개진다.
    sidecar = json.loads((trace_path.parent / "oracle" / "verdict.json").read_text("utf-8"))
    assert sidecar["final"]["candidate_grades"] == validate_grades
    # 리포트도 같은 `_grades_by_finding`을 부르므로 8단계 등급을 보인다 - 재현에 성공한 Finding이
    # `[E3]`이 아니라 `[E4]`다(그 전에는 `proven`인데 E3으로 찍혔다).
    assert sorted(re.findall(r"\*\*\[(E\d)\]", result.report.render())) == ["E3", "E4"]
    assert result.record.is_complete


def test_one_candidate_is_the_final_and_the_fix_leg_runs_once_through_the_production_wiring(
    tmp_path: Path,
) -> None:
    """후보가 하나뿐이면 그 하나가 최종이다(`sole_candidate` - 위치 규칙이 아니다). 운영 배선을 지나
    fix 다리·판정 조회가 정확히 한 번 돈다. 바이트는 run이 남긴 PoC 사이드카와 **내용으로** 견준다.
    """
    oracle = FakeOracle()

    result, trace_path = _run(tmp_path, oracle=oracle)

    events = _events(trace_path)
    (generated,) = _of(events, "pov_generated", stage=int(Stage.VALIDATE))
    (sidecar,) = sorted((trace_path.parent / "pov_poc").glob("*.poc"))
    assert oracle.vul_calls == [sidecar.read_bytes()]
    assert oracle.final_calls == [(sidecar.read_bytes(), oracle.vul_results[0])]
    (final,) = _of(events, "oracle_final")
    assert final["subject_id"] == generated["subject_id"]
    assert (final["data"]["status"], final["data"]["designation"]) == ("scored", "sole_candidate")
    assert len(_of(events, "oracle_submitted")) == 1
    assert result.record.is_complete  # 추가형 - run의 완주를 건드리지 않는다


def test_an_oracle_that_is_not_wired_leaves_no_trace_of_it(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path, before=_with_index())

    assert _oracle_events(_events(trace_path)) == []
    assert not (trace_path.parent / "oracle").exists()
    assert result.record.oracle is None
    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["oracle"] is None
    assert not any("채점기" in warning for warning in result.warnings)


def test_two_runs_stay_byte_identical_when_the_oracle_returns_fresh_values(
    tmp_path: Path,
) -> None:
    """재현성 기준(`trace.py`): 서버·장치가 정하는 값이 trace에 새면 같은 입력의 두 run이 갈린다. PR
    #408 리뷰가 잰 것은 둘이다 - 95줄 중 1줄이 `poc_id`(uuid4)였고, 같은 서버에 다시 묻자 `cached`가
    False에서 True로 다른 줄이 갈렸다. 같은 입력에서 `fix`가 0 5회·139 5회 나온 장치 의존도
    있다(`docs/experiments/README.md`의 2026-10-02 행). 후보가 하나라 판정이 있는 최종 단계까지
    돈다. 그래서 두 대역이 **휘발·환경 의존 값을 전부 다르게** 낸다 - 호출마다 새 `poc_id`, 두 번째
    run은 캐시 적중, 종료 코드와 판정이 갈린다. 어느 것이든 trace에 들어가면 이 검사가 죽는다. 값은
    사이드카에만 있고 두 사이드카는 **다르다**(그래서 이 비교가 공허하지 않다)."""
    first_oracle = FakeOracle()
    second_oracle = FakeOracle(
        vul_cached=True, fix_cached=True, vul_exit_code=0, fix_exit_code=139, success=False
    )

    _, first = _run(tmp_path, "a", oracle=first_oracle)
    _, second = _run(tmp_path, "b", oracle=second_oracle)

    (final,) = _of(_events(first), "oracle_final")
    assert final["data"]["status"] == "scored", "판정이 있는 최종 단계가 안 돌았다 - 공허하다"
    assert _canonical(first) == _canonical(second)
    assert first_oracle.vul_calls == second_oracle.vul_calls
    sidecars = [
        json.loads((path.parent / "oracle" / "verdict.json").read_text(encoding="utf-8"))
        for path in (first, second)
    ]
    finals = [sidecar["final"] for sidecar in sidecars]
    for field in ("poc_id", "vul_exit_code", "fix_exit_code", "success", "cached"):
        assert finals[0][field] != finals[1][field], f"{field}: 두 사이드카가 같다 - 공허한 비교"
    for path, sidecar in zip((first, second), sidecars, strict=True):
        text = path.read_text(encoding="utf-8")
        assert sidecar["final"]["poc_id"] not in text
        assert all(c["poc_id"] not in text for c in sidecar["candidates"])


def test_two_runs_stay_byte_identical_when_the_failures_read_differently(tmp_path: Path) -> None:
    """실패 쪽 같은 성질. 오류 문구는 URL·포트·OS 로캘의 errno 문구를 든다(영어와 한국어 Windows가
    다르다) - 같은 입력의 두 run이 문구만 달라도 trace는 같아야 한다. 문구는 사이드카에만 간다.
    예외 **종류**는 trace에 남는 사실이라 같게 둔다(`OSError(errno, 문구)`는 errno에 따라 하위
    클래스로 바뀌니 문구 한 인자로만 만든다)."""
    english = OSError("[Errno 111] Connection refused: http://127.0.0.1:8666/submit-vul")
    korean = OSError("[WinError 10061] 대상 컴퓨터에서 연결을 거부했습니다: localhost:9000")
    first_oracle = FakeOracle(vul_errors={0: english})
    second_oracle = FakeOracle(vul_errors={0: korean})

    _, first = _run(tmp_path, "a", oracle=first_oracle)
    _, second = _run(tmp_path, "b", oracle=second_oracle)

    assert len(_of(_events(first), "oracle_failed")) == 1, "실패가 안 났다 - 이 검사가 공허하다"
    assert _canonical(first) == _canonical(second)
    details = [
        [
            row["error_detail"]
            for row in json.loads((p.parent / "oracle" / "verdict.json").read_text("utf-8"))[
                "candidates"
            ]
        ]
        for p in (first, second)
    ]
    assert details[0] != details[1], "두 사이드카의 오류 문구가 같다 - 공허한 비교"


def test_oracle_events_sit_inside_a_stage_8_interval_and_carry_wall_ms(tmp_path: Path) -> None:
    """도커 왕복이 단계 시간 장부에서 빠지던 결함(이벤트가 8단계 `stage_end` 뒤에 났고 `wall_ms`가
    없었다). 이벤트의 구간으로 단계를 가른다 -
    `test_the_auditor_source_is_read_inside_the_auditor_stage`와 같은 방식."""
    _, trace_path = _run(tmp_path, oracle=FakeOracle())

    open_stage, seen = None, []
    for event in _events(trace_path):
        if event["kind"] == "stage_start":
            open_stage = event["stage"]
        elif event["kind"] == "stage_end":
            open_stage = None
        elif event["kind"].startswith("oracle"):
            seen.append((event["kind"], open_stage, event["wall_ms"] is not None))
    assert seen == [("oracle_submitted", 8, True), ("oracle_final", 8, True)]


def test_submission_failures_are_visible_in_the_warnings_and_the_manifest(tmp_path: Path) -> None:
    """제출이 전부 실패해도 run은 완주로 끝난다(추가형) - 그래서 `trace.jsonl`을 열기 전에는 성공한
    run과 같은 얼굴이던 것을 경고와 매니페스트에 올린다."""
    oracle = FakeOracle(vul_errors={0: OSError("down"), 1: OSError("down")})

    result, trace_path = _run(tmp_path, before=_with_index(), oracle=oracle)

    assert result.record.is_complete
    expected = {
        "vul_submitted": 0, "vul_failed": 2, "final": "none",
        "final_reason": "all_vul_submissions_failed", "record": "oracle/verdict.json",
    }  # fmt: skip
    assert result.record.oracle == expected
    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["oracle"] == expected
    assert any("vul 제출 2건이 예외로 실패했다" in w for w in result.warnings)
    assert any("vul 제출이 전부 실패해" in w and "채점 점수가 없다" in w for w in result.warnings)
    final = _of(_events(trace_path), "oracle_final")[0]["data"]
    assert (final["status"], final["reason"]) == ("none", "all_vul_submissions_failed")
    assert oracle.final_calls == []


def test_a_run_cut_short_before_stage_8_ends_says_the_final_was_not_reached(
    tmp_path: Path,
) -> None:
    """채점기를 켰는데 8단계에 못 닿으면(`--stop-after 7` 같은) 매니페스트가 `not_reached`다 -
    「최종 PoC가 없었다」(`none`)와 다른 사건이다."""
    oracle = FakeOracle()

    result, trace_path = _run(tmp_path, stop_after=int(Stage.CRITIC), oracle=oracle)

    assert oracle.vul_calls == [] and oracle.final_calls == []
    assert result.record.oracle == {
        "vul_submitted": 0, "vul_failed": 0, "final": "not_reached", "final_reason": None,
        "record": None,
    }  # fmt: skip
    assert _oracle_events(_events(trace_path)) == []
    assert any("8단계가 끝까지 돌지 않아" in warning for warning in result.warnings)


def _http_server(monkeypatch: pytest.MonkeyPatch) -> _FakeOracle:
    server = _FakeOracle()
    monkeypatch.setattr(urllib.request, "urlopen", server)
    return server


def _task_oracle() -> TaskOracle:
    return TaskOracle(server=_SERVER, task_id=_TASK_ID, agent_id=_AGENT_ID, api_key=_API_KEY)


def test_the_real_client_runs_the_fix_container_once_even_with_several_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """차단 결함을 HTTP에서 본다: 후보 둘이면 `/submit-vul`은 둘이고 `/submit-fix`는 **정확히
    한 번**이다 - 비최종 PoC에 `-fix` 이미지가 도는 것이 차단 결함이었고, N건 전부에 도는 것과
    0번 도는 것 사이의 **하나**가 맞다. 같은 바이트의 둘째 제출은 이 run이 만든 행의 캐시라
    컨테이너가 안 돈다 - 그것을 옛 판정이라 경고하지 않는다.

    ⏵ **2026-10-04 - 이 검사가 뒤집힌 자리다.** 앞 판은 `/submit-fix` **0번**과 `grade_tie`를
    물었다. 두 후보의 PoC가 바이트까지 같아(둘째가 캐시 적중인 것이 그 증거다) 지정이 접고
    왕복이 돈다 - **실 HTTP 경로에서 왕복이 0번이던 것을 깨는 자리**가 여기다.
    """
    server = _http_server(monkeypatch)

    result, trace_path = _run(tmp_path, before=_with_index(), oracle=_task_oracle())

    paths = [call.url.path for call in server.calls]
    assert paths.count("/submit-vul") == 2 and paths.count("/submit-fix") == 1
    assert server.containers == 2  # vul 한 번 + fix 한 번 (둘째 vul은 이 run의 캐시)
    sidecar = json.loads((trace_path.parent / "oracle" / "verdict.json").read_text("utf-8"))
    assert [c["cached"] for c in sidecar["candidates"]] == [False, True]
    # `scored`인 final에는 `reason` 키가 없다 - `None`이 아니라 **없다**(사유가 안 났다).
    assert sidecar["final"]["status"] == "scored" and "reason" not in sidecar["final"]
    assert sidecar["final"]["designation"] == "sole_poc"
    assert not any("캐시" in warning for warning in result.warnings)


def test_the_real_client_runs_the_fix_container_exactly_once_for_the_sole_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """후보가 하나면 `/submit-vul` 하나에 `/submit-fix` 하나다 - 그 하나가 최종이다."""
    server = _http_server(monkeypatch)

    result, trace_path = _run(tmp_path, oracle=_task_oracle())

    paths = [call.url.path for call in server.calls]
    assert paths.count("/submit-vul") == 1 and paths.count("/submit-fix") == 1
    assert server.containers == 2  # vul 한 번 + fix 한 번
    sidecar = json.loads((trace_path.parent / "oracle" / "verdict.json").read_text("utf-8"))
    assert [c["cached"] for c in sidecar["candidates"]] == [False]
    assert sidecar["final"]["status"] == "scored" and sidecar["final"]["success"] is True
    assert sidecar["final"]["designation"] == "sole_candidate"
    assert sidecar["final"]["cached"] == {"vul": False, "fix": False}
    assert (sidecar["final"]["vul_exit_code"], sidecar["final"]["fix_exit_code"]) == (77, 0)
    assert not any("캐시" in warning for warning in result.warnings)


def test_a_second_run_on_the_same_bundle_is_flagged_as_a_stale_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent_id는 번들이 정한 값이라 같은 번들로 run을 반복하면 서버 캐시를 공유한다(이슈 #385).
    `dev_fake`의 PoC는 결정론이라 둘째 run의 두 다리가 모두 옛 판정이다 - 컨테이너가 안 돌았다는
    것을 기록과 경고가 말한다."""
    server = _http_server(monkeypatch)
    _run(tmp_path, "first", oracle=_task_oracle())
    containers = server.containers

    result, trace_path = _run(tmp_path, "second", oracle=_task_oracle())

    assert server.containers == containers, "둘째 run은 컨테이너를 안 돌렸어야 한다"
    sidecar = json.loads((trace_path.parent / "oracle" / "verdict.json").read_text("utf-8"))
    assert sidecar["final"]["cached"] == {"vul": True, "fix": True}
    stale = [w for w in result.warnings if "이전 판정의 캐시다" in w]
    assert stale and "vul" in stale[0] and "fix" in stale[0]


def test_the_api_key_never_reaches_a_run_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """키는 trace·매니페스트·사이드카·프롬프트·보고서 어디에도 안 남는다 - 실제 클라이언트가 실제
    헤더로 키를 보낸 run의 산출물 전부를 바이트로 훑는다(서버가 헤더를 받았다는 것도 함께 본다)."""
    server = _http_server(monkeypatch)
    _, trace_path = _run(tmp_path, oracle=_task_oracle())

    assert any(call.headers.get("x-api-key") == _API_KEY for call in server.calls)
    scanned = [p for p in trace_path.parent.rglob("*") if p.is_file()]
    assert scanned, "산출물이 없다 - 이 검사가 공허하다"
    for path in scanned:
        assert _API_KEY.encode() not in path.read_bytes(), path.name


# --- `cli.main run --oracle-submit` — 플래그에서 서버까지 ---------------------------------------


def _oracle_task(root: Path, **script: object) -> Path:
    """과제 번들 + 그 번들이 서버 대역과 맞는 `submit.sh`.

    소스는 `TARGET` 픽스처다 - `_level1_task`의 한 줄짜리 소스에서는 `dev_fake`가 8단계에 finding을
    못 올려 낼 PoC가 없다.
    """
    task = root / "task"
    task.mkdir(parents=True)
    (task / "description.txt").write_text("DESC MARKER oracle", encoding="utf-8")
    src = root / "stage" / "src-vul"
    src.mkdir(parents=True)
    for fixture in TARGET.iterdir():
        if fixture.is_file():
            (src / fixture.name).write_bytes(fixture.read_bytes())
    with tarfile.open(task / "repo-vul.tar.gz", "w:gz") as tf:
        tf.add(src, arcname="src-vul")
    fields = {"server": _SERVER, "task_id": _TASK_ID, "agent_id": _AGENT_ID, **script}
    (task / "submit.sh").write_text(submit_script(**fields), encoding="utf-8")
    _oracle_canary(root, fields["task_id"])
    return task


def _oracle_canary(root: Path, task_id: object) -> Path:
    """통과하는 카나리 보고서 - 채점 run은 이것 없이 서지 않는다(이슈 #383).

    거절 규칙은 `tests/test_canary_report.py`·`tests/test_oracle_cli.py`가 든다. 여기서는 사슬이
    서버까지 이어지는지만 보므로 통과하는 보고서 하나로 족하다. **합성값이다.**
    """
    path = root / "canary.json"
    path.write_text(
        json.dumps(
            {
                "task": task_id,
                "verdict": "pass",
                "ok": True,
                "repeat": 10,
                "below_measured_repeat": False,
                "mmap_rnd_bits": "28",
                "rnd_bits_above_28": False,
                "rnd_bits_unmeasured": False,
                "measured_at": "2026-10-03T17:42:07+00:00",
                # 게이트가 요약만 믿지 않게 보는 관측 칸 셋(§2.2).
                "completed": True,
                "segv_count": 0,
                "problems": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _oracle_run(  # noqa: ANN202
    tmp_path: Path, *extra: str, server: str | None = _SERVER, task: Path | None = None
):
    argv = [
        "run", "--profile", "dev_fake", "--seed", "1",
        "--artifacts-root", str(tmp_path / "arts"),
        "--task-dir", str(task or _oracle_task(tmp_path)),
        "--task-work-dir", str(tmp_path / "w"),
        "--oracle-canary", str(tmp_path / "canary.json"),
        "--oracle-submit", *extra,
    ]  # fmt: skip
    if server is not None:
        argv += ["--oracle-server", server]
    return cli.main(argv)


def test_cli_run_submits_the_final_poc_through_the_flag_to_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`cli.run`이 `_oracle_for`를 부르고 그 결과를 `execute`에 넘기고 `build_deps`가 `Deps`에 싣고
    러너가 `ctx`에 심는 사슬 전부를 **실제 `TaskOracle`**로 지난다. 사슬의 어느
    고리(`execute(oracle=)` · `build_deps(oracle=)` · `RunContext(oracle=)`)를 끊어도 서버가
    아무것도 안 받아 이 검사가 죽는다."""
    server = _http_server(monkeypatch)
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, _API_KEY)

    rc = _oracle_run(tmp_path)

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    paths = [call.url.path for call in server.calls]
    assert paths.count("/submit-fix") == 1 and paths.count("/submit-vul") >= 1
    run_dir = next((tmp_path / "arts").iterdir())
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["oracle"]["final"] == "scored"
    assert _SERVER in captured.out and _TASK_ID in captured.out  # 대상이 run 전에 찍힌다
    assert "[ERROR]" not in captured.err
    assert _API_KEY not in captured.out + captured.err
    assert all(_API_KEY.encode() not in p.read_bytes() for p in run_dir.rglob("*") if p.is_file())


def test_cli_run_refuses_a_masked_task_id_before_creating_a_run_or_submitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--mask-map` 없이 마스킹된 id의 번들은 컨테이너를 돌리기 전에 exit 2다 - 비용을 낸 뒤에야
    알게 되지 않는다."""
    server = _http_server(monkeypatch)
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, _API_KEY)
    task = _oracle_task(tmp_path, task_id="9c73e92e52b7")

    rc = _oracle_run(tmp_path, task=task)

    err = capsys.readouterr().err
    assert rc == 2 and "[ERROR]" in err and "9c73e92e52b7" in err
    assert server.calls == []
    assert not (tmp_path / "arts").exists() or not list((tmp_path / "arts").iterdir())
    assert not (tmp_path / "w").exists() or not any((tmp_path / "w").iterdir())


def test_cli_run_refuses_a_bundle_that_names_another_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """키가 번들이 고른 호스트로 가는 길이 없다 - 주소가 다르면 요청 전에 멈춘다."""
    server = _http_server(monkeypatch)
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, _API_KEY)
    task = _oracle_task(tmp_path, server="http://evil.example:9")

    rc = _oracle_run(tmp_path, task=task)

    assert rc == 2 and "evil.example" in capsys.readouterr().err
    assert server.calls == []


@pytest.mark.parametrize(
    "extra",
    [["--dry-run"], ["--stop-after", "7"], ["--stop-after", "0"]],
    ids=["dry-run", "stop-after-7", "stop-after-0"],
)
def test_cli_run_that_cannot_reach_stage_8_warns_instead_of_demanding_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, extra: list[str]
) -> None:
    """키가 없고 서버 주소도 없는데 `--dry-run`·낮은 `--stop-after`면 **막지 않고** 경고한다 -
    모델에 안 닿는 run은 막지 않는다는 이 파일의 원칙. 전엔 키 부재로 exit 2였고 제출은 어차피
    0건이었다."""
    server = _http_server(monkeypatch)
    monkeypatch.delenv(cli._ORACLE_KEY_ENV, raising=False)

    rc = _oracle_run(tmp_path, *extra, server=None)

    err = capsys.readouterr().err
    assert rc == 0, err
    assert "[WARN ] --oracle-submit이지만" in err and "아무것도 제출하지 않는다" in err
    assert "[ERROR]" not in err
    assert server.calls == []
    run_dir = next((tmp_path / "arts").iterdir())
    assert "oracle" not in {p.name for p in run_dir.iterdir()}


def test_cli_run_with_a_failing_server_completes_and_says_so_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """서버가 죽어 있어도 run은 완주하고(추가형) exit 0이다. 달라지는 것은 stderr의 경고와
    매니페스트다 - 「완주 여부: 완주」만 보이고 채점기가 한 일이 0건이던 것이 이 결함이었다."""

    def down(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, _API_KEY)

    rc = _oracle_run(tmp_path)

    captured = capsys.readouterr()
    assert rc == 0 and "완주 여부: 완주" in captured.out
    assert "[WARN ] 채점기 vul 제출 1건이 예외로 실패했다" in captured.err
    assert "[WARN ] 채점기에 낼 최종 PoC를 지정하지 못했다" in captured.err
    run_dir = next((tmp_path / "arts").iterdir())
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["oracle"]["vul_failed"] == 1 and manifest["oracle"]["final"] == "none"


# --- 3. 지표 산출 가능성 ----------------------------------------------------


def test_evidence_gate_runs_at_all_three_stages(tmp_path: Path) -> None:
    """`stages.GATE_STAGES`가 문서가 아니라 사실이어야 한다 (W3).

    `gate.py`는 "`evaluate()`는 세 단계에서 각각 호출된다"고 단정하는데 8단계에는 호출이
    **없었다.** `GATE_STAGES` 상수의 src/ 참조가 0건이었고, 코드가 문서를 배신하는
    상태였다.
    """
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)

    for stage in GATE_STAGES:
        assert _of(events, "gate_eval", stage=stage), f"{int(stage)}단계에 게이트 판정이 없다"


def test_e4_is_reachable_and_only_at_the_stage_that_runs_the_tools(tmp_path: Path) -> None:
    """E4는 8단계에서만 성립한다. 그 단계가 게이트를 안 부르면 **영원히 집계되지 않는다.**

    E4의 조건은 `SANITIZER`/`CRASH`/`POC_RUN` Evidence인데 그것을 만드는 단계가 8단계뿐이다
    (`validate._call`). 그래서 이 등급이 trace에 나타난다는 것 자체가 W3이 닫혔다는 뜻이고,
    그전에는 도구 검증 제거율의 분자가 **구조적으로** 0이었다.

    **등급 값 자체를 고정하지 않는다** — 그것은 Fake 도구가 성공을 내주는 성질에 기대는
    값이다. 고정하는 것은 "E4가 나타난다면 그 단계는 8이다"라는 관계다.
    """
    _, trace_path = _run(tmp_path)
    graded = _of(_events(trace_path), "gate_eval")

    e4 = [e for e in graded if e["data"]["grade"] == "E4"]
    assert e4, "E4가 한 번도 안 나오면 8단계 게이트가 배선되지 않았다"
    assert {e["stage"] for e in e4} == {int(Stage.VALIDATE)}
    assert all(e["data"]["p4"] for e in e4)


def test_gate_grade_does_not_regress_between_stages(tmp_path: Path) -> None:
    """등급은 근거가 쌓이며 올라가는 값이다. 8단계가 6·7단계보다 낮으면 배선이 틀렸다.

    8단계 게이트에 앞 단계 Evidence·Decision을 안 넘기면 정확히 이 증상이 난다 —
    2단계 static 근거가 `usable`에서 빠져 `p1`이 거짓이 되고, Critic 판정이 없어 `p3`도
    거짓이 된다. **낮아진 등급은 예외가 아니라 조용히 틀린 값으로 기록된다.**
    """
    _, trace_path = _run(tmp_path)
    order = ["E0", "E1", "E2", "E3", "E4"]

    seen = [
        (e["stage"], order.index(e["data"]["grade"]))
        for e in _of(_events(trace_path), "gate_eval")
        if e["subject_id"] == "find_0001"
    ]

    assert len(seen) == len(GATE_STAGES)
    assert [grade for _, grade in seen] == sorted(grade for _, grade in seen)


def test_stage_removal_rates_are_derivable(tmp_path: Path) -> None:
    """계획 §6 대응표 1~4행. 분모가 0이면 산출 자체가 불가능하므로 그것부터 본다."""
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)

    scout = _of(events, "transition", stage=Stage.SCOUT)
    created = [e for e in scout if e["data"]["rule"] == "C-1"]
    promoted = [e for e in scout if e["data"]["reason"] == ReasonCode.SCOUT_PROMOTED.value]
    assert created, "4단계 생성 기록이 없으면 제거율의 분모가 없다"
    scout_rate = 1 - len(promoted) / len(created)

    graded = _of(events, "gate_eval", stage=Stage.GATE)
    assert graded
    gate_rate = sum(1 for e in graded if e["data"]["grade"] == "E0") / len(graded)

    critic = _of(events, "transition", stage=Stage.CRITIC)
    assert critic
    rejected = {ReasonCode.CRITIC_REBUTTED.value, ReasonCode.CRITIC_NEED_EVIDENCE.value}
    critic_rate = sum(1 for e in critic if e["data"]["reason"] in rejected) / len(critic)

    # 8단계 재현 시도의 결과 사유 **전부**. trace.py의 이벤트→지표 표와 같은 집합이고,
    # 접두사 `repro_*`로 세면 evidence_below_proven(F-10b)이 분모에서 빠진다(#68).
    repro = {
        ReasonCode.REPRO_FAILED.value,
        ReasonCode.REPRO_CONFIRMED.value,
        ReasonCode.EVIDENCE_BELOW_PROVEN.value,
    }
    # 이 집합은 **두 벌**이다 — 여기와 `trace.py`의 이벤트→지표 표(그쪽이 정본). 손으로
    # 유지하다 접두사 산식(`count(repro_*)`)이 새 사유를 빠뜨렸다(#68). 같이 움직이게 묶는다.
    # 표지가 움직이면 아래 집합이 비어 단언이 곧바로 터진다 — 조용히 통과할 길이 없다.
    denominator = (trace.__doc__ or "").split("「제거율 — Tool」 산식:", 1)[-1].split("분모", 1)[-1]
    assert {
        name.strip() for name in denominator.split("count(", 1)[-1].split(")", 1)[0].split("∪")
    } == repro, "trace.py의 8단계 제거율 분모와 이 테스트가 세는 사유 집합이 갈라졌다"
    validated = [
        e for e in _of(events, "transition", stage=Stage.VALIDATE) if e["data"]["reason"] in repro
    ]
    assert validated, "8단계까지 간 Finding이 없으면 도구 검증 제거율이 정의되지 않는다"
    tool_rate = sum(
        1 for e in validated if e["data"]["reason"] == ReasonCode.REPRO_FAILED.value
    ) / len(validated)

    for rate in (scout_rate, gate_rate, critic_rate, tool_rate):
        assert 0.0 <= rate <= 1.0


def test_scout_removal_rate_is_not_degenerate(tmp_path: Path) -> None:
    """0%도 100%도 아니어야 한다 — 파이프라인이 실제로 후보를 거르고 있다는 뜻이다."""
    _, trace_path = _run(tmp_path)
    scout = _of(_events(trace_path), "transition", stage=Stage.SCOUT)
    created = [e for e in scout if e["data"]["rule"] == "C-1"]
    promoted = [e for e in scout if e["data"]["reason"] == ReasonCode.SCOUT_PROMOTED.value]

    assert 0.0 < 1 - len(promoted) / len(created) < 1.0


def test_token_and_cache_metrics_are_derivable(tmp_path: Path) -> None:
    """계획 §6 대응표 5~8행."""
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)
    calls = _of(events, "model_call")
    assert calls

    ok_calls = [e for e in calls if e["data"]["ok"]]
    assert all(
        {"input_tokens", "output_tokens", "cached_tokens"} <= set(e["data"]) for e in ok_calls
    )
    inputs = sum(e["data"]["input_tokens"] for e in ok_calls)
    cached = sum(e["data"]["cached_tokens"] for e in ok_calls)
    assert inputs > 0
    # prefix를 재사용했으므로 캐시된 몫이 있어야 한다. 0이면 prefix가 흔들렸다는 뜻이다.
    assert 0 < cached / inputs <= 1

    capsule_cache = _of(events, "cache")
    assert capsule_cache, "cache 이벤트가 없으면 캡슐 적중률을 잴 수 없다 (on_cache 미배선)"
    hit_ratio = sum(1 for e in capsule_cache if e["data"]["hit"]) / len(capsule_cache)
    assert 0.0 <= hit_ratio <= 1.0

    parse_failure_rate = sum(1 for e in calls if not e["data"]["ok"]) / len(calls)
    assert 0.0 <= parse_failure_rate <= 1.0


def test_role_and_stage_are_present_for_per_role_aggregation(tmp_path: Path) -> None:
    """지표는 역할·단계별로 쪼개야 한다. 그 열이 실제로 있는지 본다."""
    _, trace_path = _run(tmp_path)
    calls = _of(_events(trace_path), "model_call")

    # pov도 8단계에서 모델을 부른다(PoV 배선 — 추가형). dev_fake가 pov:fake_v2라 포함된다.
    assert {e["data"]["role"] for e in calls} == {"scout", "auditor", "critic", "pov"}
    assert all(e["stage"] is not None and e["data"]["model"] for e in calls)


# --- 중단 경로 --------------------------------------------------------------


def test_budget_exhaustion_still_reaches_the_report(tmp_path: Path) -> None:
    """예산 소진은 실패가 아니라 종료 경로다 (architecture.md §5)."""
    tiny = Budget(input_tokens=1, output_tokens=1, gpu_seconds=0)
    result, trace_path = _run(tmp_path, budget=tiny)

    assert result.record.stopped_after is not None
    assert result.record.finished_at is not None
    assert not result.record.is_complete  # 부분 결과는 비교표에 넣지 않는다
    assert _of(_events(trace_path), "stage_start", stage=Stage.REPORT)
    assert any("예산 소진" in warning for warning in result.warnings)


def test_budget_exhaustion_is_still_recorded_as_budget_exhausted(tmp_path: Path) -> None:
    """**예산 경로의 대조군.**

    사용자 중단 사유를 새로 만들면서 예산 경로를 같이 망가뜨릴 수 있다. 중단 쪽만
    보면 그 사실을 모른다 — 예산이 실제로 소진된 run은 여전히 `budget_exhausted`여야
    한다.
    """
    tiny = Budget(input_tokens=1, output_tokens=1, gpu_seconds=0)
    _, trace_path = _run(tmp_path, budget=tiny)

    assert _run_transition(trace_path)["data"]["reason"] == ReasonCode.BUDGET_EXHAUSTED.value


def test_stop_after_retrieval_makes_no_model_calls(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path, stop_after=int(Stage.RETRIEVAL))

    assert _of(_events(trace_path), "model_call") == []
    assert result.record.stopped_after == int(Stage.RETRIEVAL)
    assert result.candidates == ()


def test_user_stop_is_not_recorded_as_budget_exhaustion(tmp_path: Path) -> None:
    """`--stop-after`로 자른 run과 완주한 run의 사유가 달라야 한다.

    실측(2026-08-11): 예산 4,000,000 중 1,067 토큰만 쓴 run이
    `{"rule":"R-2","reason":"budget_exhausted"}`로 기록됐다. `BudgetExhausted`는 한
    번도 던져지지 않았다. evaluation.md가 조건 간 비교에서 중단 사유를 지표로 쓰므로,
    그대로 두면 "예산 초과로 중단됨"이라는 존재하지 않는 결론이 나온다.
    """
    stopped = _run_transition(_run(tmp_path, "stopped", stop_after=int(Stage.SCOUT))[1])
    complete = _run_transition(_run(tmp_path, "complete")[1])

    assert stopped["data"]["reason"] == ReasonCode.USER_STOP_REQUESTED.value
    assert stopped["data"]["rule"] == "R-2"
    # 대조군: 완주하면 사유가 없다. 두 쪽을 함께 봐야 "달라졌다"가 성립한다.
    assert complete["data"]["reason"] is None
    assert complete["data"]["rule"] == "R-1"


def test_dry_run_is_recorded_as_dry_run_not_user_stop(tmp_path: Path) -> None:
    """`--dry-run`은 사용자가 중단한 것이 아니라 실행 모드다.

    두 상한이 동시에 걸릴 수 있으므로(`--dry-run --stop-after 5`) 사유를 하나로
    묶으면 어느 쪽이 잘랐는지가 기록에서 사라진다. 이 PR의 주제가 "기록이 사실과
    다르지 않게 한다"이므로 여기서 뭉개지 않는다.
    """
    _, trace_path = _run(tmp_path, dry_run=True)

    assert _run_transition(trace_path)["data"]["reason"] == ReasonCode.DRY_RUN.value


def test_the_lower_cap_is_the_one_recorded(tmp_path: Path) -> None:
    """두 상한이 함께 오면 실제로 자른 쪽이 기록된다.

    `RunSpec.stop_at()`이 단계 수와 사유를 함께 내므로 둘이 갈라질 수 없다 — 따로
    계산하면 P1과 같은 종류의 결함이 된다.
    """
    # dry-run 상한(3)보다 낮게 자르면 사용자 지정이 이긴다.
    user_wins = _run_transition(
        _run(tmp_path, "user", dry_run=True, stop_after=int(Stage.SIGNALS))[1]
    )
    # dry-run 상한보다 높게 자르면 dry-run이 이긴다.
    dry_wins = _run_transition(
        _run(tmp_path, "dry", dry_run=True, stop_after=int(Stage.AUDITOR))[1]
    )

    assert user_wins["data"]["reason"] == ReasonCode.USER_STOP_REQUESTED.value
    assert user_wins["stage"] == int(Stage.SIGNALS)
    assert dry_wins["data"]["reason"] == ReasonCode.DRY_RUN.value
    assert dry_wins["stage"] == int(Stage.RETRIEVAL)


def test_retrieval_cost_is_charged_only_when_retrieval_runs(tmp_path: Path) -> None:
    """검색 호출이 trace와 예산에 잡힌다.

    `HybridRetriever`는 embedding·reranker를 실제로 호출하는데 그 비용을 보고할 경로가
    없었다. Fake에서는 0이라 안 보이지만 실서빙에서는 실제 GPU 시간이고, 빠지면
    evaluation.md §5.2의 토큰 분리와 "finding당 GPU-hour"가 전부 낙관적으로 나온다.

    **개수·토큰 수를 상수로 기대하지 않는다.** 그 값들은 이 프로젝트가 재려는 대상이다.
    두 프로필의 상대 비교로만 검증한다.
    """
    _, plain_trace = _run(tmp_path, "plain")
    _, hybrid_trace = _run(tmp_path, "hybrid", profile="dev_hybrid")

    assert _of(_events(plain_trace), "retrieval_call") == []
    calls = _of(_events(hybrid_trace), "retrieval_call")
    # embedding과 reranker가 각각 최소 한 번. 개수를 세지 않고 집합으로 본다.
    assert {e["data"]["component"] for e in calls} == {"embedding", "reranker"}
    assert all(e["data"]["input_tokens"] > 0 for e in calls)

    # 검색이 도는 쪽이 실제로 더 많이 쓴다. 같으면 계량기가 안 붙은 것이다.
    # `RunRecord.budget`은 할당량이라 프로필이 같으면 같다 — 장부(`budget` 이벤트)를 본다.
    assert _charged(hybrid_trace) > _charged(plain_trace)


def test_retrieval_calls_carry_the_stage_they_happened_in(tmp_path: Path) -> None:
    """단계 없이 남기면 "3단계 검색 비용" 같은 질문에 답할 수 없다."""
    _, trace_path = _run(tmp_path, profile="dev_hybrid")
    events = _events(trace_path)

    assert all(e["stage"] is not None for e in _of(events, "retrieval_call"))
    assert all(e["stage"] is not None for e in _of(events, "cache"))


# --- CLI --------------------------------------------------------------------


def test_doctor_passes_on_dev_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["doctor", "--profile", "dev_fake", "--artifacts-root", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0  # WARN만으로는 실패하지 않는다
    assert "[WARN " in out and "[ERROR" not in out


def test_console_output_survives_a_non_utf8_terminal() -> None:
    """Windows 기본 콘솔(cp949)에서 `—` 하나로 doctor가 죽던 것을 막는다.

    pytest는 출력을 UTF-8로 잡아 이 문제가 테스트에 안 보였다. 스트림을 직접 만들어 본다.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp949")

    console.force_utf8(stream)
    stream.write("실측 전 초기값 임계값 — 조건 간 비교 전에 확정한다")  # 예외가 나면 실패

    assert stream.encoding == "utf-8"


def test_doctor_output_differs_between_profiles(tmp_path: Path) -> None:
    """`dev_fake`와 `dev_hybrid`의 유일한 차이(retriever)가 진단에 드러나야 한다.

    두 출력이 같으면 설정이 무시되고 있어도 알아챌 방법이 없다. `adapters.retriever`가
    선언만 되고 읽히지 않던 시기에 doctor가 정확히 그 상태였다.
    """
    fake = _doctor_text("dev_fake", tmp_path)
    hybrid = _doctor_text("dev_hybrid", tmp_path)

    assert fake != hybrid
    assert "HybridRetriever" in hybrid
    assert "HybridRetriever" not in fake


def test_doctor_warns_that_a_slice_input_profile_needs_slices(tmp_path: Path) -> None:
    """`run`이 `--slices` 없이 거부할 프로필을 doctor가 미리 말한다 — **WARN이지 ERROR가 아니다.**

    doctor는 `--slices`를 받지 않으니 막을 대상이 없다. ERROR로 두면 설정이 멀쩡한 풀 프로필의
    `doctor`가 exit 1이 된다. 슬라이스 입력 프롬프트가 없는 프로필(`dev_fake` — scout v1)에는
    그 줄이 없어야 한다 — 전 프로필에 뜨는 경고는 아무도 안 읽는다.
    """

    def slice_lines(profile: str) -> list[str]:
        return [line for line in _doctor_text(profile, tmp_path).splitlines() if "--slices" in line]

    pool = slice_lines("harness_pool")
    assert pool, "슬라이스 입력 프로필인데 doctor가 --slices를 말하지 않는다"
    assert all(line.startswith("[WARN ") for line in pool), f"WARN이 아니다: {pool}"
    assert slice_lines("dev_fake") == [], "슬라이스 입력이 없는 프로필에도 경고한다"


@pytest.mark.parametrize("profile", ["dev_fake", "dev_hybrid"])
def test_describe_wiring_matches_what_build_deps_makes(profile: str, tmp_path: Path) -> None:
    """doctor가 말하는 이름과 실제로 만들어진 객체가 같아야 한다.

    `describe_wiring()`은 부작용 있는 객체를 만들지 않으므로 `build_deps()`와 갈라질 수
    있다 — 선언과 실제의 괴리는 이 PR이 고치는 결함과 정확히 같은 종류다. 출력의
    **모든 행**을 대조한다. 일부만 보면 나머지가 조용히 어긋난다.
    """
    config = load_config(profile)
    deps = build_deps(
        config, run_id="run_20260901_0001", source_root=TARGET, artifacts_root=tmp_path
    )
    actual = {
        "retriever": type(deps.retriever).__name__,
        "tool_runner": type(deps.tools).__name__,
        "state_store": type(deps.store).__name__,
    }
    for role in PROMPTED_ROLES:
        # single-pass 전용 역할은 이 프로필의 build_deps가 만들지 않는다
        if role not in config.profile.roles:
            continue
        actual[role] = type(deps.models.client_for(role)).__name__
    if config.profile.adapters.retriever == "hybrid":
        actual["embedding"] = type(deps.retriever.embedder).__name__
        actual["reranker"] = type(deps.retriever.reranker).__name__

    assert describe_wiring(config) == actual


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_doctor_never_opens_a_socket(profile: str, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """`doctor`는 엔드포인트에 접속하지 않는다 (src/cli.py 모듈 docstring).

    `describe_wiring()`이 모델·검색 클라이언트를 실제로 만든다. 지금은 그 생성자들이
    필드 대입뿐이라 안전하지만, 그 사실을 보장하는 것이 docstring 한 줄뿐이었다.
    누가 `__init__`에 헬스체크나 커넥션 풀을 넣으면 doctor가 조용히 네트워크를 치고
    GPU 없는 팀원의 진단이 네트워크 오류로 실패한다 — 설정 오류와 구별되지 않는다.

    `describe_wiring`만 감싸지 않고 `doctor` 전체를 검사한다. 나중에 다른 접속 코드가
    들어와도 같은 그물에 걸려야 한다.
    """

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("doctor가 소켓을 열었다")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)

    assert cli.doctor(profile, artifacts_root=tmp_path)


def test_some_profile_actually_points_at_a_real_endpoint() -> None:
    """위 테스트가 공허해지지 않게 한다.

    모든 프로필이 `fake://`뿐이면 소켓 검사는 아무것도 검증하지 않는다. 실서빙
    엔드포인트를 가리키는 프로필이 최소 하나 있어야 그 검사에 의미가 생긴다.
    프로필 이름을 박지 않고 `ModelEntry.is_fake`로 판정한다.
    """
    served = [
        profile
        for profile in ALL_PROFILES
        for config in [load_config(profile)]
        if any(not config.model_for(role).is_fake for role in config.profile.roles)
    ]
    assert served, "실서빙 엔드포인트를 가리키는 프로필이 없어 소켓 검사가 공허하다"


def _doctor_text(profile: str, artifacts_root: Path) -> str:
    return "\n".join(check.render() for check in cli.doctor(profile, artifacts_root=artifacts_root))


def test_doctor_fails_on_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["doctor", "--profile", "nope", "--artifacts-root", str(tmp_path)])

    assert code == 1
    assert "[ERROR" in capsys.readouterr().out


def test_estimate_reports_gpu_seconds_as_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """실측치가 없으면 숫자를 지어내지 않는다."""
    code = cli.main(
        [
            "estimate",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "추정 불가" in out
    assert "예상 모델 호출" in out


def _estimate_input_line(budget: Budget) -> str:
    """`estimate`가 입력 토큰 예산을 말하는 줄. 저장소 값이 아니라 **주입한** 예산으로 돈다."""
    config = load_config("dev_fake").transition(budget=budget)
    return next(line for line in cli.estimate(config, TARGET) if line.startswith("예상 input 토큰"))


def test_estimate_labels_a_zero_token_budget_as_no_limit() -> None:
    """`estimate`는 토큰 상한 0을 「예산이 0」이 아니라 「상한 없음」으로 찍는다.

    상한 0은 「검사 안 함」이다(`BudgetLedger._overrun`). `dev_fake`의 토큰 상한이 0이 된 뒤(#427)
    이 줄이 「약 N / 예산 0 (예산 0)」을 내 0을 두 번 되풀이했고 「못 돈다」로 읽힐 수 있었다
    (#435 — 실제 오판 사건은 0건). 그 분기는 `aae1f7f`(2026-08-08)가 넣은 뒤 #427 전까지
    `config/budgets.yaml`의 어느 판도 입력 토큰 0을 든 적이 없어(`git log -p`로 확인) 한 번도 안
    타던 자리였고, 이 줄을 무는 검사도 0건이었다.

    값을 저장소 `dev_fake`에서 읽지 않고 직접 세운다 — 저장소 값이 바뀌어도 이 경계는 같아야 한다.
    **필드마다 값을 다르게 둔다** — 입력·출력·시간 상한이 같은 값이면 `estimate`가 틀린 필드를
    읽어도 핀이 초록이다. 입력 토큰 상한만 호출마다 0과 양수로 갈리고 나머지 셋은 반대쪽 값이다.
    양수 쪽은 `10**9`(실질 무한)와 `1`(가장 작은 양수)을 둘 다 둔다 — 문턱을 `if budget > 1`처럼
    올려 읽는 변이를 잡는다.
    ⚠ **문구 핀이다** — 철자를 단언하지 성질을 단언하지 않는다. 문구를 바꾸면 이 핀도 같이 바꾼다.
    """
    no_limit = _estimate_input_line(
        Budget(input_tokens=0, output_tokens=10**9, gpu_seconds=10**9, tool_seconds=10**9)
    )
    capped = _estimate_input_line(Budget(input_tokens=10**9, output_tokens=0, gpu_seconds=0))
    smallest = _estimate_input_line(Budget(input_tokens=1, output_tokens=0, gpu_seconds=0))

    assert "상한 없음" in no_limit
    assert "(예산 0)" not in no_limit
    assert "%" in capped, "양수 상한이면 예산 대비 비율이 나온다"
    assert "상한 없음" not in smallest, "가장 작은 양수 상한은 「상한 없음」이 아니다"
    assert "%" in smallest


def test_cli_run_writes_trace_manifest_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--condition",
            CONDITION,
            "--seed",
            "20260901",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    assert code == 0
    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (run_dir / "trace.jsonl").read_text(encoding="utf-8").strip()
    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "report.md").read_text(encoding="utf-8").startswith("#")
    assert "완주" in capsys.readouterr().out


def test_run_warns_only_when_the_tool_runner_actually_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`run`의 예외 신고는 **값이 바뀐** run에만 붙는다 (D50).

    **이 경로가 사건이 난 자리다.** `run_20260828_0002`가 `b1_single`(선언 docker)에
    `--tool-runner docker`로 돌고도 "프로필과 다른 도구 런타임" 경고를 달아 보고서 메타를
    쓰는 사람을 오도했다. 술어(`_tool_runner_overrides`)와 `doctor` 경로는 각각 테스트가
    있지만 **`run`이 그 술어를 실제로 쓰는지는 검사되지 않았다** — 그 한 줄이 사건의 자리라
    회귀 가드를 여기 둔다. `is not None`으로 되돌리면 첫 단언이 깨진다.

    두 프로필 다 도구는 `fake`로 돈다 — 선언값만 다르므로 CI에 docker가 없어도 성립한다.
    """
    same = cli.main(
        [
            "run", "--profile", "dev_fake", "--target", str(TARGET),
            "--condition", CONDITION, "--seed", "20260901",
            "--artifacts-root", str(tmp_path / "same"), "--tool-runner", "fake",
        ]
    )
    assert same == 0
    assert "프로필과 다른 도구 런타임" not in capsys.readouterr().err

    diff = cli.main(
        [
            "run", "--profile", "dev_docker", "--target", str(TARGET),
            "--condition", CONDITION, "--seed", "20260901",
            "--artifacts-root", str(tmp_path / "diff"), "--tool-runner", "fake",
        ]
    )
    assert diff == 0
    assert "프로필과 다른 도구 런타임으로 돌았다: fake" in capsys.readouterr().err


def test_two_runs_on_the_same_day_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """같은 날 두 번 돌리면 디렉터리도 run_id도 갈라져야 한다.

    수정 전에는 둘 다 `run_YYYYMMDD_0001`이라 두 번째 실행이 첫 번째의
    trace·manifest·report를 조용히 덮었다. `mkdir(exist_ok=True)` + `"w"` 모드가
    겹친 결과다. evaluation.md §2.3은 같은 조건을 seed만 바꿔 최소 3회 반복하도록
    요구하는데, 이 상태로는 그 반복이 물리적으로 성립하지 않는다.

    **디렉터리 수와 run_id를 모두 본다.** 하나만 보면 나머지가 조용히 어긋난다.
    """
    for seed in (1000, 2000):
        cli.main(
            [
                "run",
                "--profile",
                "dev_fake",
                "--target",
                str(TARGET),
                "--seed",
                str(seed),
                "--artifacts-root",
                str(tmp_path),
            ]
        )

    run_dirs = sorted(p for p in tmp_path.iterdir() if p.is_dir())
    assert len(run_dirs) == 2, f"실행 2회인데 디렉터리가 {len(run_dirs)}개다"

    manifests = [
        json.loads((d / "run_manifest.json").read_text(encoding="utf-8")) for d in run_dirs
    ]
    assert len({m["run_id"] for m in manifests}) == 2
    # 앞 run이 덮이지 않았다는 증거 — 두 seed가 모두 남아 있어야 한다.
    assert {m["seed"] for m in manifests} == {1000, 2000}


def test_run_dir_name_matches_the_run_id_inside_it(tmp_path: Path) -> None:
    """디렉터리 이름과 `RunRecord.run_id`가 갈라지면 지금 결함보다 나쁘다.

    수정 전에는 `new_run_id`가 cli.run()과 cli.execute()에서 각각 불렸다. 입력이
    같아 우연히 일치했을 뿐이라, 순번이 들어오는 순간 갈라질 자리였다. 그래서
    "run_id는 한 번만 만든다"를 여기서 고정한다.
    """
    for _ in range(2):
        cli.main(
            [
                "run",
                "--profile",
                "dev_fake",
                "--target",
                str(TARGET),
                "--artifacts-root",
                str(tmp_path),
            ]
        )

    for run_dir in (p for p in tmp_path.iterdir() if p.is_dir()):
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_id"] == run_dir.name


def test_a_directory_claimed_between_scan_and_mkdir_is_not_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """훑기와 만들기 사이의 틈을 실제로 벌려 본다.

    `_next_sequence`가 고른 번호를 그 사이에 다른 프로세스가 가져간 상황이다. 순차
    테스트로는 이 경합을 자연스럽게 만들 수 없으므로 훑기 결과를 고정해 재현한다.
    `mkdir(exist_ok=True)`로 되돌리면 여기서 앞 run의 파일이 사라진다 — 그것이 원래
    결함의 정확한 모양이다.
    """
    monkeypatch.setattr(cli, "_next_sequence", lambda *_: 1)
    taken = tmp_path / new_run_id(datetime.now(UTC), 1)
    taken.mkdir()
    (taken / "trace.jsonl").write_text("먼저 온 run의 것", encoding="utf-8")

    cli.main(
        ["run", "--profile", "dev_fake", "--target", str(TARGET), "--artifacts-root", str(tmp_path)]
    )

    assert (taken / "trace.jsonl").read_text(encoding="utf-8") == "먼저 온 run의 것"
    assert (tmp_path / new_run_id(datetime.now(UTC), 2)).is_dir()


def test_estimate_revalidates_capsules_with_a_role_the_profile_declares() -> None:
    """`estimate`가 캡슐을 다시 검증할 때 쓰는 카운터 역할이 **그 프로필에 실재해야 한다.**

    `deps.store.query("capsule")`는 `using_counter` **밖**이면 글자수 근사로
    `CapsuleSection._tokens_match_body`를 다시 돈다. 실 토크나이저로 조립된 캡슐이면
    저장값과 재계산값이 갈려 `ValidationError`로 죽는다 — `estimate --profile b1_single`이
    실제로 그렇게 죽었고, 그것을 막은 것이 `counter_for_role(...)`로 감싼 그 `with`다(#84).

    **풀에서 처음엔 그 막음이 안 섰다.** 역할 키를 `"scout"`으로 박아 뒀는데 풀 프로필에는
    그 키가 없어(`_declares_one_scout_arrangement`가 단일과 풀의 공존을 금지한다)
    `counter_for_role`이 `None`을 돌려줬다. **`_capsule_counter_role`이 그것을 고쳤다** —
    풀이면 조립한 첫 멤버(`SCOUT_POOL_ROLES[0]` = `scout_memory`)를 돌려주므로 재검증이 같은
    카운터를 본다. 아래 두 단언이 그 불변식을 고정한다.

    ⚠ **`harness_pool`(PR #190의 `local_balanced_pool`, 2026-09-24 이름 변경)이 커밋된 실 풀
    프로필이다** — 이 검사가 가리키던 그 "커밋하는 순간"이 왔고, 그 순간에도 막음이 선다는
    것을 여기서 지킨다. Fake는
    `source`가 없어 `counter_for_role`이 `None`이라 이 검사는 **역할 키의 실재**만 본다(값이 아니라
    자리) — 실 토크나이저에서의 조립·재검증 일치는 아래 `..._the_runner_actually_uses`가 잡는다.
    """
    from src.cli import _capsule_counter_role

    base = load_config("dev_fake")
    pool = base.transition(
        profile=base.profile.transition(
            roles={
                "scout_memory": "fake_v2",
                "scout_boundary": "fake_v2",
                "scout_injection": "fake_v2",
                "auditor": "fake",
                "critic": "fake",
                "embedding": "fake",
                "reranker": "fake",
            }
        )
    )

    for name, config in (("단일", base), ("풀", pool)):
        role = _capsule_counter_role(config)
        assert role in config.profile.roles, (
            f"{name} 프로필에 없는 역할 {role!r}로 캡슐을 재검증한다 — "
            f"`counter_for_role`이 None을 주고 #84의 막음이 사라진다. "
            f"선언된 역할: {sorted(config.profile.roles)}"
        )

    assert _capsule_counter_role(pool) == SCOUT_POOL_ROLES[0], (
        "풀에서 캡슐을 **조립하는** 것은 첫 멤버다 — `runner._scout_roles`가 "
        "`SCOUT_POOL_ROLES` 순서를 유지하고 `_step_retrieval`이 첫 멤버의 캡슐을 캐시한다. "
        "재검증도 같은 역할이어야 조립·검증이 같은 카운터를 본다"
    )


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_the_estimate_counter_role_is_the_one_the_runner_actually_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """위 검사를 **실동작에 묶는다.** 구현식을 다시 적는 것으로는 부족하다.

    위 검사가 단언하는 `== SCOUT_POOL_ROLES[0]`은 `_capsule_counter_role`의 구현 그대로다.
    정작 지켜야 할 불변식은 *"조립한 역할 = 재검증하는 역할"*인데, **조립하는 쪽을 안 본다.**
    `_scout_roles`가 순서를 뒤집도록 회귀하면 러너는 `scout_injection`으로 조립하고
    `estimate`는 `scout_memory`로 재검증해 #84가 되살아나는데 위 검사는 **초록이다.**

    여기서는 풀 run을 실제로 돌려 `counter_for_role`이 **요청된 인자**를 집계하고, 그중
    Scout 자리의 것이 `_capsule_counter_role`과 같은지 본다.

    ⚠ **집계 결과가 「멤버 셋」이 아니라 하나인 것이 정상이다** — 캡슐이 target당 하나라
    나머지 멤버는 `using_counter`에 도달하기 전에 캐시로 반환한다(`_step_retrieval`).
    """
    from src.adapters import factory as factory_mod
    from src.cli import _capsule_counter_role

    asked: list[str] = []
    original = factory_mod.build_deps

    def spy_build_deps(*args: object, **kwargs: object) -> object:
        deps = original(*args, **kwargs)
        inner = deps.counter_for_role

        def wrapped(role: str) -> object:
            asked.append(role)
            return inner(role)

        return dataclasses.replace(deps, counter_for_role=wrapped)

    monkeypatch.setattr("src.cli.build_deps", spy_build_deps)
    result, _ = _run(tmp_path, configure=_poolify(mode))

    scout_asked = {r for r in asked if r.startswith("scout")}
    assert scout_asked, (
        f"Scout 자리 카운터가 한 번도 요청되지 않았다 — 요청된 것: {sorted(set(asked))}"
    )

    assert result.candidates, "후보가 없다 — 이 run은 이 검사의 사례가 아니다"
    assert scout_asked == {SCOUT_POOL_ROLES[0]}, (
        f"러너가 캡슐을 조립하며 요청한 Scout 카운터가 {sorted(scout_asked)}다 — "
        f"`cli._capsule_counter_role`은 {SCOUT_POOL_ROLES[0]!r}를 재검증에 쓴다. "
        "둘이 갈리면 조립과 검증이 다른 카운터를 보고 #84가 되살아난다"
    )
    assert _capsule_counter_role.__module__ == "src.cli"  # 위 비교의 대상이 그 함수임을 못박는다


def test_expected_calls_counts_one_scout_call_per_pool_member() -> None:
    """`estimate`의 호출 상한이 **Scout 자리 수**를 센다 (ADR 0010 §3 팬아웃).

    캡슐은 target당 하나인데 4단계는 **멤버마다 한 번** 돈다. 「캡슐 하나당 scout 1회」로
    세면 풀 프로필의 scout 호출이 **실제의 1/3**로 나오고, 그 값으로 예산을 잡으면 run이
    도중에 `BudgetExhausted`로 부분 결과만 남긴다 — `estimate`가 막으라고 있는 사건이
    정확히 그것이다.

    auditor·critic은 **후보** 수를 따르므로 팬아웃과 함께 는다. critic만 `critic_top_n`이
    다시 막는다.

    ⚠ **scout는 후보당 한 번이 아니라 `1 + max_hops`번이다**(ADR 0012 결정 2 — need_evidence
    루프의 되묻기 상한). v1·v2(신호 없음)는 늘 1회뿐이라 이 상한을 안 채우지만, 예산은
    최악(v3가 매번 `max_hops`까지 되묻는 경우)을 견뎌야 한다.
    """
    from src.cli import _expected_calls

    base = load_config("dev_fake")
    pool = base.transition(
        profile=base.profile.transition(
            roles={
                "scout_memory": "fake_v2",
                "scout_boundary": "fake_v2",
                "scout_injection": "fake_v2",
                "auditor": "fake",
                "critic": "fake",
                "embedding": "fake",
                "reranker": "fake",
            }
        )
    )
    capsules = 4
    top_n = base.thresholds.critic_top_n
    max_hops = base.thresholds.max_hops
    # scout(1 + max_hops) + auditor(1 + auditor_tool_hops). ⏵ 2026-10-04 — auditor 쪽이 1에서
    # 늘었다: 도구 루프가 켜지면 후보당 `1 + auditor_tool_hops`회다(이슈 #419 ·
    # `nodes/auditor._run_hops`). 저장소 기본이 16이라 **상수로 적으면 끈 run의 값이 된다.**
    per_candidate = (1 + max_hops) + (1 + base.thresholds.auditor_tool_hops)

    assert _expected_calls(base, capsules) == capsules * per_candidate + min(capsules, top_n), (
        "단일 scout 프로필의 값이 바뀌었다 — 회귀다"
    )

    members = len(SCOUT_POOL_ROLES)
    candidates = capsules * members
    assert _expected_calls(pool, capsules) == candidates * per_candidate + min(
        candidates, top_n
    ), (
        f"풀인데 호출 상한이 멤버 수({members})를 안 센다 — "
        "캡슐 하나가 후보 셋이 되고 4·5단계가 그만큼 돈다"
    )


def test_estimate_does_not_consume_a_run_sequence(tmp_path: Path) -> None:
    """추정은 산출물을 남기지 않으므로 순번도 소비하면 안 된다.

    소비하면 `estimate`를 몇 번 돌렸는지에 따라 다음 run의 번호가 달라져, 번호가
    "그날 몇 번째 실행인가"를 더 이상 뜻하지 않는다.
    """
    for _ in range(3):
        cli.main(
            [
                "estimate",
                "--profile",
                "dev_fake",
                "--target",
                str(TARGET),
                "--artifacts-root",
                str(tmp_path),
            ]
        )

    assert [p for p in tmp_path.iterdir() if p.name.startswith(cli.RUN_DIR_PREFIX)] == []


def test_cli_run_can_emit_sarif(tmp_path: Path) -> None:
    cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--format",
            "sarif",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    sarif = json.loads((run_dir / "report.sarif.json").read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]


def test_seed_override_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    """실제로 쓰인 seed가 기록되어야 재현 실행을 재구성할 수 있다."""
    cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--seed",
            "4242",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 4242


def test_gc_is_dry_run_by_default(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    kept = [tmp_path / f"run_2026090{i}_0001" for i in range(1, 4)]
    for path in kept:
        path.mkdir()
    (tmp_path / "not-a-run").mkdir()

    code = cli.main(["gc", "--keep", "1", "--artifacts-root", str(tmp_path)])

    assert code == 0
    assert all(path.is_dir() for path in kept)  # 기본은 아무것도 지우지 않는다
    assert "삭제 예정" in capsys.readouterr().out


def test_gc_apply_removes_only_old_run_dirs(tmp_path: Path) -> None:
    runs = [tmp_path / f"run_2026090{i}_0001" for i in range(1, 4)]
    for path in runs:
        path.mkdir()
    other = tmp_path / "not-a-run"
    other.mkdir()

    cli.main(["gc", "--keep", "1", "--apply", "--yes", "--artifacts-root", str(tmp_path)])

    assert not runs[0].exists() and not runs[1].exists()
    assert runs[2].is_dir()  # 최신 1개는 남는다
    assert other.is_dir()  # run_ 접두사가 아닌 디렉터리는 건드리지 않는다


# --- GPU 회계 판정 (2026-08-17) ----------------------------------------------


def test_a_fake_run_does_not_warn_about_unmeasured_gpu_time(tmp_path: Path) -> None:
    """`dev_fake`는 실서빙 역할이 하나도 없다 — 잴 GPU가 없는 것은 결함이 아니다.

    경고하면 dev 프로필의 모든 run이 매번 같은 줄을 뱉고 진짜 문제가 묻힌다
    (`model_checksums`에서 Fake 역할을 뺀 것과 같은 논법).
    """
    result, _ = _run(tmp_path)

    assert not [w for w in result.warnings if "GPU 시간" in w]


def test_an_unmeasured_served_role_is_named(tmp_path: Path) -> None:
    """실서빙 역할이 GPU 시간을 못 남기면 그 run의 비용은 미상이다.

    2026-08-17까지 모델 호출이 GPU 시간을 **아예 청구하지 못했으므로**, 그 상태로 돈 run은
    전부 여기 걸려야 한다. 경고에 역할 이름이 없으면 어느 역할을 고쳐야 하는지 알 수 없다.
    """
    served = served_scout_config().served_models()
    assert served == {"scout": SERVED_SCOUT_KEY}, "이 검사의 전제가 무너졌다"

    warning = runner.unmeasured_gpu_warning(
        {role: 3 for role in ("scout", "auditor") if role in served}
    )

    assert warning is not None
    assert "scout(3회)" in warning
    assert "auditor" not in warning  # Fake 역할은 교집합에서 빠진다
    assert "비교표에서 제외" in warning
    assert runner.unmeasured_gpu_warning({}) is None


# --- 인덱서 산출물 연결 (D13) ---------------------------------------------------


def test_some_profile_pairs_a_hybrid_retriever_with_a_real_indexer() -> None:
    """이 조합이 없으면 `HybridRetriever`의 그래프 섹션 분기가 죽은 코드가 된다.

    이 그물을 친 2026-08-20에는 index.json을 내는 프로필이 전부 `retriever: fake`였고
    (dev_docker·dev_singularity·local_balanced·b1_single), `dev_hybrid`는
    `tool_runner: fake`라 산출물이 없었다. 그래서 `dev_hybrid_docker`를 만들었다.
    2026-09-04에 `local_balanced_hybrid`가 둘째로 이 조합에 들어왔다.
    이 저장소는 "픽스처가 실전을 대표하지 못해 생긴 결함"을 이미 두 번 겪었다 —
    clang의 include 경로와 tree-sitter 최상위 한 겹. 둘 다 테스트는 초록이었다.

    프로필 **이름을 박지 않는다.** 조합으로 판정해야 이름이 바뀌어도 이 그물이 남는다
    (`test_some_profile_actually_points_at_a_real_endpoint`와 같은 방식).
    """
    paired = [
        profile
        for profile in ALL_PROFILES
        for adapters in [load_config(profile).profile.adapters]
        if adapters.retriever == "hybrid" and adapters.tool_runner != "fake"
    ]

    assert paired, "hybrid Retriever가 실제 index.json을 읽는 프로필이 하나도 없다"


def test_graph_index_is_traced_exactly_once_per_run(tmp_path: Path) -> None:
    """`unresolved_*`가 trace에 남지 않으면 그 저장소의 그래프 커버리지를 사후에 못 센다.

    **커버리지를 모르는 것과 커버리지가 완전한 것은 다르다.** `dev_fake`는 index.json이
    없으므로 `status=missing`이 정답이고, 그 사실 자체가 기록되어야 한다.
    """
    run_dir = tmp_path / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    spec = RunSpec(
        condition="B3prime@pipeline",
        target=TARGET,
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        cli.execute(
            load_config("dev_fake"),
            spec,
            run_id=run_dir.name,
            run_dir=run_dir,
            trace_stream=stream,
        )

    events = [
        json.loads(line)
        for line in (run_dir / "trace.jsonl").read_text("utf-8").splitlines()
        if json.loads(line)["kind"] == "graph_index"
    ]

    assert len(events) == 1, f"graph_index 이벤트가 {len(events)}건이다 — run당 1건이어야 한다"
    assert events[0]["data"]["status"] == "missing"


def test_token_counter_is_traced_exactly_once_and_before_any_model_call(tmp_path: Path) -> None:
    """어느 카운터로 셌는지가 안 남으면 그 run이 "토크나이저 적용 후"인지 사후에 못 가른다.

    폴백이 조용했던 동안에는 이것을 run **전에** 사람이 손으로 확인하는 수밖에 없었다
    (2026-08-26 재측정 §근거 로그). 그 확인을 run이 스스로 하게 만드는 것이 이 이벤트다.

    **`graph_index`와 달리 0건이 허용되지 않는다.** 그쪽은 지연 로드라 조회가 없으면 안
    실리지만, 카운터는 `build_deps` 시점에 정해져 있다 — 그리고 **모델을 부르기 전에**
    나와야 그 뒤 어디서 죽어도 분모가 남는다(`target_plan`과 같은 계약).
    """
    run_dir = tmp_path / "run_20260901_0002"
    run_dir.mkdir(parents=True)
    spec = RunSpec(
        condition="B3prime@pipeline",
        target=TARGET,
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        cli.execute(
            load_config("dev_fake"),
            spec,
            run_id=run_dir.name,
            run_dir=run_dir,
            trace_stream=stream,
        )

    lines = (run_dir / "trace.jsonl").read_text("utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    counters = [i for i, e in enumerate(events) if e["kind"] == "token_counter"]
    models = [i for i, e in enumerate(events) if e["kind"] == "model_call"]

    assert len(counters) == 1, f"token_counter가 {len(counters)}건이다 — run당 1건이어야 한다"
    assert models, "이 픽스처는 모델을 부른다 — 안 불렀다면 순서 검사가 공회전한다"
    assert counters[0] < models[0], "token_counter가 첫 model_call보다 뒤에 나왔다"

    data = events[counters[0]]["data"]
    # `dev_fake`는 전 역할이 fake라 `source`가 없다 — `approx`/`no_source`가 정답이고
    # 그 사실 자체가 기록되어야 한다(`graph_index`의 `status=missing`과 같은 자리).
    assert data == {
        "scout_counter": "approx",
        "scout_source": None,
        "scout_reason": "no_source",
        "auditor_counter": "approx",
        "auditor_source": None,
        "auditor_reason": "no_source",
    }

    # **해석된 경로가 새면 재현성 기준이 깨진다**(`orchestrator/trace.py`). 절대경로 조각이
    # 값에 섞이지 않는지 여기서 못박는다 — 나중에 필드를 늘릴 때 밟을 자리다.
    assert not [v for v in data.values() if isinstance(v, str) and ("/" in v or "\\" in v)]

# --- 9. 심볼 단위 target (W2) ------------------------------------------------
#
# `dev_fake`는 `tool_runner: fake`라 index.json을 내지 않는다. 그래서 두 경로를 각각
# 태운다 — 인덱스가 없을 때(파일 단위 폴백)와 있을 때(심볼 단위). 한쪽만 태우면
# 나머지 절반이 아무도 안 보는 코드가 된다(`test_index_graph.py`와 같은 논법).

# 픽스처의 실제 함수 정의와 줄 범위. 어긋나면 아래 첫 테스트가 잡는다 — 손으로 적은
# 인덱스가 픽스처와 갈라지면 이 절의 단언이 전부 거짓 위에 서게 된다.
_FIXTURE_INDEX = {
    "version": "index-v1",
    "files": ["parser.c", "util.c"],
    "skipped": [],
    "symbols": [
        {"name": "parse_header", "file": "parser.c", "line_start": 19, "line_end": 26},
        {"name": "recv_request", "file": "parser.c", "line_start": 28, "line_end": 31},
        {"name": "copy_name", "file": "util.c", "line_start": 14, "line_end": 24},
        {"name": "name_length", "file": "util.c", "line_start": 26, "line_end": 29},
    ],
    "calls": [
        {"caller": "recv_request", "callee": "parse_header", "file": "parser.c", "line": 30},
        {"caller": "parse_header", "callee": "memcpy", "file": "parser.c", "line": 24},
    ],
    "unresolved_calls": 0,
    "unresolved_symbols": 0,
    "parse_errors": [],
}


def _with_index(document: object = None):  # noqa: ANN202
    """컨테이너 러너가 쓰는 **바로 그 경로**에 index.json을 놓는 `before` 훅.

    `FakeToolRunner`의 `index`는 대상 존재만 확인하고 산출물을 내지 않으므로, 이렇게
    두면 `dev_fake`로 심볼 단위 경로를 태울 수 있다. 경로를 손으로 조립하지 않고
    `index_output_path()`에 묻는 이유는 배선이 어긋나도 초록불이 나오는 것을 막기
    위해서다(`test_index_graph._write_index`와 같은 처리).
    """

    def place(run_dir: Path) -> None:
        path = index_output_path(run_dir.parent, run_dir.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = _FIXTURE_INDEX if document is None else document
        path.write_text(json.dumps(body), encoding="utf-8")

    return place


def _plan(trace_path: Path) -> dict:
    events = _of(_events(trace_path), "target_plan")
    assert len(events) == 1, f"target_plan은 run당 1건이다: {len(events)}건"
    return events[0]["data"]


def test_fixture_index_matches_the_real_fixture() -> None:
    """위 인덱스가 픽스처와 갈라지면 이 절의 심볼 단위 테스트가 전부 거짓이 된다."""
    for symbol in _FIXTURE_INDEX["symbols"]:
        lines = (TARGET / symbol["file"]).read_text(encoding="utf-8").splitlines()
        assert symbol["name"] in lines[symbol["line_start"] - 1]


def test_targets_are_symbols_when_the_index_is_there(tmp_path: Path) -> None:
    """후보 단위가 파일에서 심볼로 바뀐다. **분모가 바뀌는 지점이다**(STATUS.md §10)."""
    result, trace_path = _run(tmp_path, before=_with_index())

    plan = _plan(trace_path)
    assert plan["symbol_targets"] == len(_FIXTURE_INDEX["symbols"])
    assert plan["fallback_artifacts"] == 0
    assert plan["planned"] == len(_FIXTURE_INDEX["symbols"])
    # 후보가 artifact 수(2)가 아니라 심볼 수(4)만큼 생긴다.
    assert len(result.candidates) == len(_FIXTURE_INDEX["symbols"])


def test_candidate_locations_carry_the_symbol_and_its_lines(tmp_path: Path) -> None:
    """`read_symbol_source()`가 파일 전체를 돌려주던 원인이 후보 위치에 있었다."""
    result, _ = _run(tmp_path, name="sym", before=_with_index())

    located = {
        (c.location.file, c.location.symbol, c.location.line_start) for c in result.candidates
    }
    assert located == {
        (s["file"], s["name"], s["line_start"]) for s in _FIXTURE_INDEX["symbols"]
    }


def test_missing_index_falls_back_to_files_and_says_so(tmp_path: Path) -> None:
    """`dev_fake`는 index.json이 없다. 후보를 안 만들면 그 프로필의 run이 통째로 빈다.

    폴백 자체는 W2 이전 동작이라 회귀가 아니다. **되돌아갔다는 사실이 기록에 남는 것**이
    새로 얻는 것이다.
    """
    result, trace_path = _run(tmp_path)

    plan = _plan(trace_path)
    assert plan["fallback_artifacts"] == plan["analyzable_artifacts"] == 2
    assert plan["symbol_targets"] == 0
    assert len(result.candidates) == 2
    assert all(c.location.symbol is None for c in result.candidates)


def test_artifacts_without_symbols_make_no_candidates(tmp_path: Path) -> None:
    """`.py`·바이너리는 인덱서에 넘어가지 않고, 함수 정의가 없는 `.h`도 심볼이 없다.

    zlib run에서 artifact 300건 중 C 소스가 12건인데 후보가 32건 생겼다 — 대상 선정이
    새고 있었고, 심볼이 후보를 정하면서 그 자리가 닫힌다.
    """
    only_parser = {
        **_FIXTURE_INDEX,
        "symbols": [s for s in _FIXTURE_INDEX["symbols"] if s["file"] == "parser.c"],
    }

    _, trace_path = _run(tmp_path, before=_with_index(only_parser))

    plan = _plan(trace_path)
    assert plan["no_symbol_artifacts"] == 1  # util.c
    assert plan["symbol_targets"] == 2


def test_a_header_function_definition_becomes_a_non_slice_target(tmp_path: Path) -> None:
    """함수 정의를 가진 `.h`는 (위 테스트의 함수 없는 `.h`와 반대로) 심볼이 있으므로 비슬라이스
    run에서 `_Target`을 하나 더 만든다(`runner.py:972` `ctx.symbols_for(artifact.path)`).
    index-v2 보고서(2026-09-26-index-v2-atom.md)의 표본 셋은 헤더 함수 정의가 0개라 이 경로를 안
    다뤘다(§5 「말하지 않는 것」·§6).
    """
    target = tmp_path / "src"
    target.mkdir()
    (target / "parser.c").write_text((TARGET / "parser.c").read_text(encoding="utf-8"), "utf-8")
    (target / "util.c").write_text((TARGET / "util.c").read_text(encoding="utf-8"), "utf-8")
    (target / "util.h").write_text("static inline int clamp(int v) {\n    return v;\n}\n", "utf-8")
    index = {
        **_FIXTURE_INDEX,
        "files": [*_FIXTURE_INDEX["files"], "util.h"],
        "symbols": [
            *_FIXTURE_INDEX["symbols"],
            {"name": "clamp", "file": "util.h", "line_start": 1, "line_end": 3},
        ],
    }

    result, trace_path = _run(tmp_path, target=target, before=_with_index(index))

    plan = _plan(trace_path)
    assert plan["analyzable_artifacts"] == 3
    assert plan["symbol_targets"] == len(index["symbols"]) == 5
    assert plan["no_symbol_artifacts"] == 0
    assert ("util.h", "clamp") in {
        (c.location.file, c.location.symbol) for c in result.candidates
    }


# --- 10. (a′) 정적 신호는 게이트가 아니라 순서다 ------------------------------


def test_signal_artifact_symbols_stay_above_no_signal_ones(tmp_path: Path) -> None:
    """신호 붙은 artifact의 심볼은 무신호 심볼 위에 남는다(#70, #249 리뷰 HIGH-1).

    거르지는 않는다 — 신호 없는 심볼도 후보가 되고 뒤로 갈 뿐이다. `parser.c`의
    `memcpy`(24줄)는 `parse_header`(19~26)에 들어 rank 2다. 같은 파일 `recv_request`
    (28~31)는 그 한 줄을 안 품지만 artifact에 신호가 있어 **rank 1**이라, 신호 없는
    `aaa.c:quiet`(rank 0) 아래로 내려가지 않는다.

    ⚠ 첫 판(#249)은 `recv_request`를 rank 0으로 내려 `quiet` 아래로 밀었다 — `Evidence.
    location`이 `results[0]` 한 줄만 실어 같은 파일의 두 번째 히트를 못 봤기 때문이다.
    `candidate_max`로 자르면 **실히트 파일의 심볼이 잘리고 무신호 심볼이 분석**됐다.
    경로 순서로는 `aaa.c`가 앞이지만 신호가 순서를 뒤집는다.
    """
    target = tmp_path / "src"
    target.mkdir()
    (target / "parser.c").write_text((TARGET / "parser.c").read_text(encoding="utf-8"), "utf-8")
    (target / "aaa.c").write_text("int quiet(void)\n{\n    return 0;\n}\n", encoding="utf-8")
    index = {
        **_FIXTURE_INDEX,
        "files": ["aaa.c", "parser.c"],
        "symbols": [
            {"name": "quiet", "file": "aaa.c", "line_start": 1, "line_end": 4},
            *[s for s in _FIXTURE_INDEX["symbols"] if s["file"] == "parser.c"],
        ],
    }

    result, trace_path = _run(tmp_path, name="order", target=target, before=_with_index(index))

    assert _plan(trace_path)["signal_artifacts"] == 1
    files = [c.location.file for c in result.candidates]
    # parse_header(rank 2) · recv_request(rank 1) 둘 다 quiet(rank 0) 위에 남는다.
    assert files == ["parser.c", "parser.c", "aaa.c"]


def test_a_hit_symbol_in_a_later_file_beats_a_non_hit_symbol_in_an_earlier_one(
    tmp_path: Path,
) -> None:
    """히트가 든 심볼(rank 2)은 **파일 순서를 가로질러** 같은 artifact의 비히트 심볼(rank 1)을
    앞선다 — #249가 있는 이유다(#257 1).

    위 검사의 기대값은 #249 이전 순서로도 나와서, 3단을 불리언으로 접거나(`-min(rank, 1)`)
    #248이 채운 위치를 순서가 안 쓰게 해도(`signal_lines = []`) 전체 pytest가 초록이었다(#257
    본문의 두 변이). 여기서는 두 변이 모두 `[a1, a2]`를 낸다.

    **위치 없는 신호를 넣지 않는다** — 그 심볼의 순위는 #257 2가 아직 정하지 않았다.
    """
    target = tmp_path / "src"
    target.mkdir()
    # `aaa.c`: a1에 히트(3줄) · a2는 같은 artifact의 다른 심볼. `zzz.c`: z2에 히트.
    # 히트 모양은 Fake semgrep 규칙(`fake_tools._STATIC_PATTERNS`의 memcpy — 길이가 상수 아님)이다.
    (target / "aaa.c").write_text(
        "void a1(char *d, char *s, int n)\n{\n    memcpy(d, s, n);\n}\n"
        "int a2(void)\n{\n    return 0;\n}\n",
        encoding="utf-8",
    )
    (target / "zzz.c").write_text(
        "void z2(char *d, char *s, int n)\n{\n    memcpy(d, s, n);\n}\n", encoding="utf-8"
    )
    index = {
        **_FIXTURE_INDEX,
        "files": ["aaa.c", "zzz.c"],
        "symbols": [
            {"name": "a1", "file": "aaa.c", "line_start": 1, "line_end": 4},
            {"name": "a2", "file": "aaa.c", "line_start": 5, "line_end": 8},
            {"name": "z2", "file": "zzz.c", "line_start": 1, "line_end": 4},
        ],
    }

    result, trace_path = _run(
        tmp_path, name="cross", target=target, candidate_max=2, before=_with_index(index)
    )

    assert _plan(trace_path)["signal_artifacts"] == 2
    assert [c.location.symbol for c in result.candidates] == ["a1", "z2"]


def test_symbol_signal_rank_is_three_tier() -> None:
    """`_symbol_signal_rank`의 3단 (#70). rank 1이 무신호(0)를 안 내리는 게 #249 HIGH-1이다.

    2=이 심볼에 히트 위치 · 1=artifact 신호는 있으나 이 심볼에 든 줄 미확인(같은 파일의
    다른 히트·위치 없는 신호·인덱스 없는 폴백) · 0=신호 없음.
    """
    sym = Location(file="a.c", symbol="f", line_start=19, line_end=26)

    assert runner._symbol_signal_rank(sym, [24], has_static=True) == 2  # 히트가 이 심볼에
    assert runner._symbol_signal_rank(sym, [30], has_static=True) == 1  # 신호 있으나 이 심볼 아님
    assert runner._symbol_signal_rank(sym, [], has_static=True) == 1  # 줄 미확인(위치 없는 신호)
    assert runner._symbol_signal_rank(sym, [24], has_static=False) == 0  # 신호 없음
    no_line = Location(file="a.c", symbol="f")
    assert runner._symbol_signal_rank(no_line, [24], has_static=True) == 1  # 폴백(줄 없음)


def test_candidate_max_defers_the_rest_and_records_them(tmp_path: Path) -> None:
    """예산에 잘린 target이 흔적 없이 사라지면 (a′)를 한 의미가 없다."""
    result, trace_path = _run(tmp_path, name="cut", before=_with_index(), candidate_max=1)

    plan = _plan(trace_path)
    skipped = _of(_events(trace_path), "target_skipped")

    assert plan["planned"] == 1
    assert plan["deferred"] == len(_FIXTURE_INDEX["symbols"]) - 1
    assert len(skipped) == plan["deferred"]
    assert {e["data"]["reason"] for e in skipped} == {"over_candidate_max"}
    # 무엇이 잘렸는지가 남는다. 수만 남기면 목록을 사후에 복원할 수 없다.
    assert all(e["data"]["symbol"] for e in skipped)
    assert len(result.candidates) == 1


def test_deferred_targets_reach_the_warning_and_the_report(tmp_path: Path) -> None:
    """trace에만 남기면 사용자가 run 결과로 읽는 것에는 안 보인다.

    deferred가 많은 run과 0건인 run이 겉보기에 같으면 "조용히 절단하지 않는다"가
    기록의 절반에서만 성립한다.
    """
    result, _ = _run(tmp_path, name="warn", before=_with_index(), candidate_max=1)

    assert any("candidate_max" in warning for warning in result.warnings)
    # ⚠ **`deferred`의 앵커는 「넘긴 대상」이다** — `dropped`와 한 f-string에 있으므로
    # 「완주하지 못한 시도」로 단언하면 **`deferred`가 0이어도 통과한다.**
    assert "넘긴 대상" in result.report.render()


def test_a_full_run_says_nothing_about_deferral(tmp_path: Path) -> None:
    """0건일 때도 줄이 나오면 그 문장이 신호가 아니라 장식이 된다."""
    result, _ = _run(tmp_path, name="full", before=_with_index())

    assert not any("candidate_max" in warning for warning in result.warnings)
    assert "넘긴 대상" not in result.report.render()
    assert "완주하지 못한 시도" not in result.report.render()
    # 절단이 없는 run에는 `critic_skipped`도 없다 — 이벤트 부재가 곧 "안 잘렸다"이다.
    assert not any("critic_top_n" in warning for warning in result.warnings)
    assert "잘린 Finding" not in result.report.render()


def _max_hops_zero(config):  # noqa: ANN001, ANN202
    """`max_hops=0`으로 내린 config — PoV가 hop0(gen) 뒤 곧장 MAX_HOP이라 submit에 못 닿는다
    (`test_pov_loop_with_max_hops_zero_stops_before_submitting`이 그 경로를 단위로 고정한다).
    최종 PoC 0건 run을 **monkeypatch 없이 설정 축으로** 만드는 자리다."""
    return config.transition(thresholds=config.thresholds.transition(max_hops=0))


def test_a_pov_run_that_submits_nothing_says_why_in_the_warning_and_the_report(
    tmp_path: Path,
) -> None:
    """PoV가 최종 PoC를 0건 냈으면 **왜 0인지**가 run 경고와 리포트에 있다 (#357·#358).

    `deferred`·`critic_failed`를 같은 자리에 올린 논거 그대로다(바로 위 검사들) — trace에만 남기면
    사용자가 run 결과로 읽는 것에는 안 보인다. 여기서는 한 겹 더 나쁘다: pov는 **추가형이라 상태를
    안 바꾸므로** Finding이 멀쩡히 찍힌 채 PoC만 조용히 0이 된다.

    ⚠ 경고가 무는 것은 **「`run_dir`에서 꺼낼 수 있는 최종 PoC가 0건」**이다. 사유 어휘가
    `submitted`를 셋으로 쪼개서 그렇다 — PoC 없는 submit과 쓰기 실패가 각자 사유를 받는다
    (`runner._pov_result`). **「그 PoC가 터지는가」는 말하지 않는다** — 그 판정은 오라클이
    낸다. 범위는 `runner._pov_shortfall_warning` docstring이 든다.
    """
    result, trace_path = _run(
        tmp_path, name="pov_zero", before=_with_index(), configure=_max_hops_zero
    )

    events = _events(trace_path)
    assert not _of(events, "pov_generated"), "픽스처 전제 — 최종 PoC가 0건이어야 한다"
    skipped = _of(events, "pov_skipped")
    assert skipped, "픽스처 전제 — pov가 돌았고 전부 섰다"
    assert any("회수 가능한 최종 PoC가 0건" in warning for warning in result.warnings)
    # **사유별 수가 경고 문장 안에 있다.** "0이다"만 적으면 왜 0인지가 여전히 trace에만 있고,
    # 그러면 이 검사가 막으려는 명제(「왜 0인지가 기록에 있다」)의 절반만 성립한다.
    assert any(f"max_hop {len(skipped)}건" in warning for warning in result.warnings)
    assert "PoV 정지 사유" in result.report.render()


def test_a_full_run_says_nothing_about_a_pov_shortfall(tmp_path: Path) -> None:
    """PoV가 submit에 닿은 run에는 그 경고가 **없다** — 0건일 때도 줄이 나오면 그 문장이 신호가
    아니라 장식이 된다(`test_a_full_run_says_nothing_about_deferral`과 같은 틀).

    ⚠ 리포트의 「PoV 정지 사유」 줄은 **정상 run에도 난다**(`submitted N건`) — 그쪽은 시도가 있으면
    적는 집계이고(`ne_absorbed`와 같은 자리), 경고는 부족을 알리는 문장이라 발화 조건이 다르다.
    그래서 여기서 단언하는 것은 경고의 부재이고 리포트 줄의 부재가 아니다.
    """
    result, trace_path = _run(tmp_path, name="pov_full", before=_with_index())

    assert _of(_events(trace_path), "pov_generated"), "픽스처 전제 — dev_fake는 PoC를 낸다"
    assert not any("회수 가능한 최종 PoC가 0건" in warning for warning in result.warnings)
    assert "PoV 정지 사유 (시도 2건): submitted 2건" in result.report.render()


def test_critic_truncation_reaches_the_trace_the_warning_and_the_report(tmp_path: Path) -> None:
    """7단계 절단도 `candidate_max`와 같은 대우를 받는다 (D26 (b)).

    여기서는 target 절단보다 한 겹 더 나쁘다. F-9(`repro_spec_missing`)가
    `hypothesized → hypothesized`라 7단계에 **간** 건과 **못 간** 건이 리포트의 같은
    묶음에 나란히 찍힌다 — 대상이 사라져서 안 보이는 것이 아니라 **전량을 본 것처럼**
    읽힌다(실측 `run_20260825_0001`: 승격 44 · 선별 8 · 절단 36).
    """
    result, trace_path = _run(tmp_path, name="critic_cut", before=_with_index(), critic_top_n=1)

    events = _events(trace_path)
    skipped = _of(events, "critic_skipped")
    promoted = [
        e for e in _of(events, "transition") if e["data"].get("reason") == "promoted_to_critic"
    ]

    assert skipped, "절단이 있는데 이벤트가 없으면 D26 (b) 이전으로 돌아간 것이다"
    assert len(skipped) == len(promoted) - 1  # critic_top_n=1
    # stage 7이 분할로 닫힌다: 못 올라간 건 + 올라간 건 = 승격한 건.
    assert len(skipped) + len(_of(events, "gate_eval", stage=7)) == len(promoted)
    assert {e["data"]["reason"] for e in skipped} == {"over_critic_top_n"}
    # 무엇이 잘렸는지가 남는다. 수만 남기면 목록을 사후에 복원할 수 없다.
    assert all(e["subject_id"] for e in skipped)
    # 사용자가 run 결과로 읽는 두 곳.
    assert any("critic_top_n" in warning for warning in result.warnings)
    # 앞머리에 잘린 사유가, 괄호에 **상한 값**이 찍혀야 한다. 이름만 적으면 러너의 run
    # 경고(`critic_top_n(1)`)와 같은 사실을 다르게 말하게 된다.
    rendered = result.report.render()
    assert "`critic_top_n`으로 잘린 Finding" in rendered
    assert "(상한 1 ·" in rendered

# --- 11. 후보 하나의 실패가 run 전체를 죽이지 않는다 (4-2) --------------------
#
# zlib 때 `ModelGatewayError`가 뚫고 나갔고 `run_manifest.json`을 쓰는 블록이 그 뒤라
# **산출물이 하나도 안 남았다** — `state.db`를 직접 열어야 했던 이유다. 후보가 수백 개인
# 실대상 run에서는 하나가 터지면 그때까지의 GPU 시간이 전부 사라진다.
#
# `FakeModelClient`에 실패 주입 손잡이를 달지 않는다. 테스트만을 위한 분기를 프로덕션
# 어댑터에 두지 않고, monkeypatch로 감싸면 `cli.execute()` 전체 경로가 그대로 돈다 —
# 매니페스트가 남는지를 실제로 물으려면 그 경로여야 한다.


def _critic_json_truncated() -> SchemaParseError:
    """`run_20260827_0002`의 critic 실패(`seq=1893`)를 그대로 옮긴 예외 (D44).

    **합성 문자열을 쓰지 않는 이유가 이 실패의 정체에 있다.** 이것은 Decision의 어느 필드가
    틀렸다는 것이 아니라 **JSON이 안 닫힌 채 끝났다**는 것이다 — 모델이 객체를 열고 공백을
    끝없이 뱉다가 출력 상한에 잘려 `EOF while parsing an object`가 났다. 그 run의 실패
    9건이 전부 이 모양이고(auditor 8 · critic 1), 잘린 입력값의 꼬리는 전부 공백·탭
    연속이다. 실패율은 캡슐 레벨과 함께 움직인다 — scout(LIGHT) 64건 0%, auditor·critic
    (EXPANDED) 14건 중 9건이다.

    가드가 사유를 보존하는지 재는 것이 이 테스트의 절반이므로 사유 문자열이 실제와 같은
    모양이어야 한다. 잔여 위험은 D46이 들고 있다.
    """
    return SchemaParseError(
        "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: "
        "1 validation error for Decision Invalid JSON: EOF while parsing an object "
        "at line 2722 column 0 [type=json_invalid]"
    )


def _failing_model(
    monkeypatch: pytest.MonkeyPatch,
    errors: list[Exception | None],
    *,
    role_under_test: Role = Role.SCOUT,
) -> None:
    """N번째 `role_under_test` 호출에서 준비된 예외를 던진다. 목록이 짧으면 그 뒤는 정상이다.

    역할을 인자로 받는 것은 D44 때문이다 — critic 단계의 실패는 후보 단위가 아니라 Finding
    단위라 같은 주입기를 다른 역할에 걸어야 두 경로를 같은 방식으로 잴 수 있다.
    """
    original = FakeModelClient.complete
    calls = {"n": 0}

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN202
        if role is role_under_test:
            index = calls["n"]
            calls["n"] += 1
            if index < len(errors) and errors[index] is not None:
                raise errors[index]
        return original(self, role=role, prompt=prompt, schema=schema, history=history)

    monkeypatch.setattr(FakeModelClient, "complete", complete)


def _context_overflow() -> ModelGatewayError:
    """vLLM이 컨텍스트 초과에 주는 400. 본문에 실린 문장이 우리가 가장 알고 싶은 값이다."""
    detail = (
        "This model's maximum context length is 32768 tokens. "
        "However, you requested 43200 tokens."
    )
    return ModelGatewayError(f"HTTP 400 {detail}", status=400, detail=detail)


def test_a_context_overflow_drops_only_that_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """후보 하나가 컨텍스트를 넘겼다 — 그 후보만 버리고 계속 간다."""
    _failing_model(monkeypatch, [_context_overflow()])

    result, trace_path = _run(tmp_path, name="drop", before=_with_index())

    skipped = _of(_events(trace_path), "target_skipped")
    assert [e["data"]["reason"] for e in skipped] == ["gateway_error"]
    # 나머지 세 개는 정상으로 돌았다.
    assert len(result.candidates) == len(_FIXTURE_INDEX["symbols"]) - 1


def test_the_skip_records_the_stage_that_actually_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`target_skipped.stage`가 죽은 노드를 가리킨다 (D48).

    `_triage_one`은 3~6단계를 연달아 태운다. 예전에는 `_drop`이 `Stage.SCOUT`을 박아
    auditor에서 죽은 target도 4로 기록됐다 — `run_20260828_0002`의 3건이 전부 그랬고,
    실패한 `model_call`은 셋 다 5단계였다. 두 사건은 뜻이 다르다: scout 실패는 후보
    생성 자체가 안 된 것이고 auditor 실패는 후보는 났는데 심층 분석이 막힌 것이다.

    **되돌리면 이 테스트가 깨진다** — 박아 둔 값이 4라 5를 못 낸다.
    """
    _failing_model(monkeypatch, [_context_overflow()], role_under_test=Role.AUDITOR)

    result, trace_path = _run(tmp_path, name="skip-stage", before=_with_index())

    skipped = _of(_events(trace_path), "target_skipped")
    assert [e["stage"] for e in skipped] == [int(Stage.AUDITOR)]
    # 후보는 났다 — 그것이 scout 실패와 갈리는 지점이다.
    assert len(result.candidates) == len(_FIXTURE_INDEX["symbols"])


def test_a_scout_failure_is_still_recorded_at_the_scout_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4단계에서 죽으면 4로 남는다 — 위 변경이 값을 한 칸 밀어 버리지 않았다.

    이 짝이 없으면 "auditor에서 5가 나온다"만 잠기고 **전부 5로 박는 잘못된 수정도
    통과한다.**
    """
    _failing_model(monkeypatch, [_context_overflow()], role_under_test=Role.SCOUT)

    result, trace_path = _run(tmp_path, name="skip-stage-scout", before=_with_index())

    skipped = _of(_events(trace_path), "target_skipped")
    assert [e["stage"] for e in skipped] == [int(Stage.SCOUT)]
    assert len(result.candidates) == len(_FIXTURE_INDEX["symbols"]) - 1


def test_the_manifest_survives_a_dropped_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**이것이 4-2의 요점이다.** 산출물이 없으면 그때까지의 GPU 시간이 전부 사라진다."""
    _failing_model(monkeypatch, [_context_overflow()])

    _run(tmp_path, name="manifest", before=_with_index())

    manifest = tmp_path / "manifest" / "run_20260901_0001" / "run_manifest.json"
    assert manifest.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["run_id"] == "run_20260901_0001"


def test_the_server_side_error_stops_the_run_but_keeps_the_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """서버가 죽었으면 남은 target 수백 개를 전부 실패시키며 돌면 안 된다.

    상태 코드가 없다 = 연결 실패·타임아웃이다. 400과 같은 예외 타입이지만 다른 사건이고,
    그 둘을 가르는 정보가 4-1이 살린 응답 본문과 상태 코드에 있다.

    **멈추는 것과 산출물을 잃는 것은 다르다.** 예산 소진과 같은 자리에서 같은 방식으로
    끝난다(전이 표 R-2) — 그러지 않으면 이 변경이 없애려던 결함이 그대로 남는다.
    """
    dead = ModelGatewayError("http://127.0.0.1:8001 호출 실패: Connection refused")
    _failing_model(monkeypatch, [dead])

    result, trace_path = _run(tmp_path, name="dead", before=_with_index())

    assert not result.record.is_complete
    assert _run_transition(trace_path)["data"]["reason"] == "model_gateway_unavailable"
    assert (tmp_path / "dead" / "run_20260901_0001" / "run_manifest.json").is_file()
    # 첫 target에서 멈췄다. 나머지는 태우지 않는다.
    assert len(result.candidates) == 0


def test_a_five_hundred_also_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5xx는 요청이 아니라 서버 쪽 사건이다. 후보를 바꿔도 낫지 않는다."""
    _failing_model(monkeypatch, [ModelGatewayError("HTTP 503", status=503, detail="overloaded")])

    result, trace_path = _run(tmp_path, name="503", before=_with_index())

    assert _run_transition(trace_path)["data"]["reason"] == "model_gateway_unavailable"
    assert (tmp_path / "503" / "run_20260901_0001" / "run_manifest.json").is_file()


def test_a_schema_failure_drops_only_that_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`retries` 한도 소진도 후보 단위 실패다. 지표는 안 잃는다 — 분자는 `model_call`에 있다."""
    _failing_model(monkeypatch, [SchemaParseError("scout이 2회 안에 Candidate를 못 만들었다")])

    result, trace_path = _run(tmp_path, name="schema", before=_with_index())

    skipped = _of(_events(trace_path), "target_skipped")
    assert [e["data"]["reason"] for e in skipped] == ["schema_parse_failed"]
    assert len(result.candidates) == len(_FIXTURE_INDEX["symbols"]) - 1
    # 실패한 호출 자체는 `model_call ok=False`로 남는다. 잡았다고 분자가 사라지지 않는다.
    failed = [e for e in _of(_events(trace_path), "model_call") if not e["data"]["ok"]]
    assert len(failed) == 1


def test_three_oversized_targets_in_a_row_do_not_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**크기 거절은 체계적 고장이 아니다.**

    컨텍스트 초과 400은 "이 후보가 크다"이지 "이 run이 고장 났다"가 아니다. 세 건을 연속으로
    세면 큰 함수가 몰려 오는 것만으로 멈추는데, 그것은 이 절이 정확히 견디겠다고 한 경우다 —
    정렬이 신호 붙은 심볼을 앞으로 모으므로 드문 일도 아니다.

    **D88의 두 기각을 여기서 무는 짝이다.** 「첫 성공 전엔 크기도 센다」와 「같은 문구가 N번
    연속이면 멈춘다」(vLLM 0.27.1은 같은 서버의 초과 문구가 전부 같다)는 상한이 지금 값(3)이면
    둘 다 이 run을 세운다.
    """
    _failing_model(
        monkeypatch, [_context_overflow(), _context_overflow(), _context_overflow()]
    )

    result, trace_path = _run(tmp_path, name="big", before=_with_index())

    assert len(_of(_events(trace_path), "target_skipped")) == 3
    # 네 번째는 정상으로 돌았다 — 멈추지 않았다는 뜻이다.
    assert len(result.candidates) == 1
    assert result.record.is_complete


def test_size_rejections_do_not_reset_the_counter_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**세지 않는 것과 지우는 것은 다르다.**

    크기 거절이 카운터를 내리면 체계적 실패와 번갈아 나오는 경우에 어느 쪽도 상한에
    닿지 않고 target 수백 개를 전부 태운다. 올리지도 내리지도 않으므로 스키마 → 400 →
    스키마 → 스키마가 1·1·2·3으로 닿는다.
    """
    _failing_model(
        monkeypatch,
        [
            SchemaParseError("첫 번째"),
            _context_overflow(),
            SchemaParseError("세 번째"),
            SchemaParseError("네 번째"),
        ],
    )

    result, trace_path = _run(tmp_path, name="mixed", before=_with_index())

    assert len(_of(_events(trace_path), "target_skipped")) == 4
    assert len(result.candidates) == 0
    # **사유가 게이트웨이 중단과 갈린다.** 서버는 응답하는데 후보가 계속 죽는 상태다.
    assert _run_transition(trace_path)["data"]["reason"] == "consecutive_target_failures"
    assert (tmp_path / "mixed" / "run_20260901_0001" / "run_manifest.json").is_file()


def test_a_completed_target_resets_the_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완주가 리셋 조건이다. 흩어진 실패는 체계적 고장이 아니다."""
    _failing_model(
        monkeypatch,
        [_context_overflow(), None, _context_overflow(), None],
    )

    result, trace_path = _run(tmp_path, name="reset", before=_with_index())

    assert len(_of(_events(trace_path), "target_skipped")) == 2
    assert len(result.candidates) == 2


def test_dropped_targets_reach_the_warning_and_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """폐기도 절단과 같은 자리에 남는다 — 사용자가 읽는 것에 안 보이면 절반만 성립한다."""
    _failing_model(monkeypatch, [_context_overflow()])

    result, _ = _run(tmp_path, name="dropwarn", before=_with_index())

    assert any("호출 실패로 버렸다" in warning for warning in result.warnings)
    assert "완주하지 못한 시도" in result.report.render()


def test_the_gateway_detail_survives_into_the_run_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**4-1이 4-2의 전제다.** zlib에서 우리가 가장 알고 싶었던 M이 버려졌다.

    `model_call`의 `error`가 클래스 이름뿐이면 무엇이 얼마나 넘쳤는지가 산출물 어디에도
    없다. 상세가 `error_detail`로 남고, 폐기 이벤트에도 같은 문자열이 붙는다.
    """
    _failing_model(monkeypatch, [_context_overflow()])

    _, trace_path = _run(tmp_path, name="detail", before=_with_index())

    failed = [e for e in _of(_events(trace_path), "model_call") if not e["data"]["ok"]]
    assert "43200 tokens" in failed[0]["data"]["error_detail"]
    assert failed[0]["data"]["error"] == "ModelGatewayError"
    skipped = _of(_events(trace_path), "target_skipped")
    assert "43200 tokens" in skipped[0]["data"]["error_detail"]



def test_a_critic_schema_failure_drops_only_that_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**critic 호출 하나가 죽어도 run은 산출물까지 간다 (D44).**

    이 가드가 없던 동안 `run_20260827_0002`는 16 m 26 s를 돌고 critic 첫 호출의
    `SchemaParseError`로 죽었고, 그 run 디렉터리에는 `report.md`도 `run_manifest.json`도
    남지 않았다 — 모델 호출 78건이 trace에만 있어 어느 커밋으로 돈 run인지 기록이
    지시하지 못했다. 후보 단위(`_triage_all`)에는 있던 처리가 Finding 단위에는 없었다.
    """
    _failing_model(
        monkeypatch,
        [_critic_json_truncated()],
        role_under_test=Role.CRITIC,
    )

    result, trace_path = _run(tmp_path, name="critic-schema", before=_with_index())

    failed = _of(_events(trace_path), "critic_failed")
    assert [e["data"]["reason"] for e in failed] == ["schema_parse_failed"]
    assert failed[0]["subject_kind"] == "finding"
    # **사유를 보존한다.** 이것이 없으면 "critic이 왜 못 돌았는가"를 보려고 다시
    # `state.db`를 열어야 하고, 그것이 이 결함이 만들던 상태다.
    assert "EOF while parsing an object" in failed[0]["data"]["error_detail"]
    # **산출물이 남는다** — 이 테스트의 존재 이유다. `report.md` 파일 자체는 `cli`가 쓰므로
    # (`cli.py`의 `run_dir / "report.md"`) 러너 층위에서는 9단계가 리포트를 만들어 냈는지를
    # 본다. 죽은 run에서 없어진 것이 정확히 이 둘이다.
    assert (trace_path.parent / "run_manifest.json").is_file()
    assert result.report.render().startswith("#")
    # 실패한 호출 자체는 `model_call ok=False`로 남는다. 잡았다고 분자가 사라지지 않는다.
    assert len([e for e in _of(_events(trace_path), "model_call") if not e["data"]["ok"]]) == 1
    assert result.record.is_complete


def _model_writes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate: dict[str, object],
    finding: dict[str, object],
) -> None:
    """Fake가 낸 Scout·Auditor 출력의 필드를 덮어 **사실 블록을 안 따른 모델**을 흉내 낸다 (D83).

    값은 `transition()`으로 만든다 — 스키마를 통과하는 출력이어야 실제 파서가 내보낼 수 있다.
    v3(`ScoutResponse`)는 안쪽 `candidate`를 고친다.
    """
    original = FakeModelClient.complete

    def complete(  # noqa: ANN001
        self, *, role: Role, prompt: object, schema: object, history: object = ()
    ) -> object:
        result = original(self, role=role, prompt=prompt, schema=schema, history=history)
        value = result.value
        if isinstance(value, ScoutResponse):
            value = value.transition(candidate=value.candidate.transition(**candidate))
        elif isinstance(value, Candidate):
            value = value.transition(**candidate)
        elif isinstance(value, Finding):
            value = value.transition(**finding)
        return result.model_copy(update={"value": value})

    monkeypatch.setattr(FakeModelClient, "complete", complete)


def _on_sqlite(configure=None):  # noqa: ANN001, ANN202
    """저장소를 `sqlite`로 바꾸는 `configure` 훅 — run 뒤 `state.db`를 열어 행을 센다.

    실서빙 프로필은 전부 `state_store: sqlite`다. `memory`로는 덮인 행을 run 밖에서 못 본다.
    """

    def wrapped(config):  # noqa: ANN001, ANN202
        if configure is not None:
            config = configure(config)
        adapters = config.profile.adapters.transition(state_store="sqlite")
        return config.transition(profile=config.profile.transition(adapters=adapters))

    return wrapped


_SCOUT_PATHS = pytest.mark.parametrize(
    "scout_path", [None, _poolify_v3()], ids=["single_v1_candidate", "pool_v3_scout_response"]
)


@_SCOUT_PATHS
def test_a_model_written_initial_state_does_not_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scout_path: object
) -> None:
    """D83 ① — Scout가 `state: "triaged"`, Auditor가 `state: "queued"`를 써도 매니페스트까지 간다.

    둘 다 스키마는 받는 값이라 파싱은 통과하고, 생성 행 조회(C-1·F-1)에서 `IllegalTransition`이
    났다 — 그 예외를 잡는 곳이 러너에도 cli에도 없어 `run_manifest.json`이 안 남았다. 하네스가
    초기 `state`를 도장 찍으면 생성은 늘 C-1(`candidate`)·F-1(`hypothesized`)이다.
    """
    _model_writes(
        monkeypatch,
        candidate={"state": FindingState.TRIAGED},
        finding={"state": FindingState.QUEUED},
    )

    result, trace_path = _run(tmp_path, name="state", configure=_on_sqlite(scout_path))

    assert (trace_path.parent / "run_manifest.json").is_file()
    assert result.report.render().startswith("#")
    events = _events(trace_path)
    created = [
        e
        for e in _of(events, "transition")
        if e["data"]["from_state"] is None and e["subject_kind"] in ("candidate", "finding")
    ]
    to_state = {kind: {e["data"]["to_state"] for e in created if e["subject_kind"] == kind}
                for kind in ("candidate", "finding")}
    assert to_state == {"candidate": {"candidate"}, "finding": {"hypothesized"}}
    overridden = {e["subject_id"] for e in _of(events, "issued_override")
                  if "state" in e["data"]["fields"].split(",")}
    assert overridden == {e["subject_id"] for e in created}, "덮은 state가 trace에 안 남았다"


@_SCOUT_PATHS
def test_model_written_foreign_ids_do_not_overwrite_other_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scout_path: object
) -> None:
    """D83 ② — 모델이 **모든** 출력에 같은 남의 ID·다른 `run_id`를 써도 객체가 안 합쳐진다.

    저장소 `put()`은 `(kind, obj_id)` upsert라 예외 없이 앞 행을 덮었고, `transition`의
    `subject_id`는 모델 값이라 trace도 한 ID로 뭉쳤다. 도장 뒤에는 저장소 행 수가 생성 수와 같고
    전부 이 run의 `run_id`로 조회된다.
    """
    _model_writes(
        monkeypatch,
        # `score`는 단일 scout(v1) 경로에서 fixture 대상 둘을 다 승격시켜 Finding을 둘 이상 만든다 —
        # 하나면 덮임을 못 잰다. v3 풀은 신호로 판정해 점수를 안 보고, 좌석 셋이 이미 여럿을 낸다.
        candidate={"candidate_id": "cand_0001", "run_id": "run_19990101_0001", "score": 1.0},
        finding={"finding_id": "find_0001", "run_id": "run_19990101_0001"},
    )

    result, trace_path = _run(tmp_path, name="ids", configure=_on_sqlite(scout_path))

    events = _events(trace_path)
    created = [
        e
        for e in _of(events, "transition")
        if e["data"]["from_state"] is None and e["subject_kind"] in ("candidate", "finding")
    ]
    store = SqliteStateStore(trace_path.parent / "state.db")
    for kind in ("candidate", "finding"):
        ids = [e["subject_id"] for e in created if e["subject_kind"] == kind]
        assert len(ids) > 1, f"{kind}가 둘 이상 생겨야 덮임을 잰다 — fixture를 늘린다"
        assert len(set(ids)) == len(ids), f"{kind} 생성 trace가 한 ID로 뭉쳤다: {ids}"
        stored = store.query(kind, run_id=result.record.run_id)
        assert sorted(obj.model_dump()[f"{kind}_id"] for obj in stored) == sorted(ids), (
            f"저장소 {kind} 행이 생성 수와 다르다 — 덮였거나 다른 run_id로 들어갔다"
        )
    assert _of(events, "issued_override"), "덮은 ID가 trace에 안 남았다"


def test_the_seventh_stage_still_splits_three_ways_when_a_critic_call_dies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`critic_skipped + critic_failed + gate_eval(stage=7) = 7단계에 올라간 Finding 수`.

    `EventKind.CRITIC_SKIPPED` 주석이 원래 두 항으로 적어 둔 분할이다. 실패 건을
    `critic_skipped`에 합쳤다면 이 등식은 여전히 맞지만 그 이벤트의 뜻("정의상 안 들어간
    건")이 깨지고, 아무 이벤트도 안 내면 좌변이 우변보다 작아져 **차이가 어디로 갔는지
    기록이 지시하지 못한다.** 셋으로 나눈 근거가 이것이다.
    """
    _failing_model(
        monkeypatch,
        [_critic_json_truncated()],
        role_under_test=Role.CRITIC,
    )

    result, trace_path = _run(tmp_path, name="critic-split", before=_with_index())

    events = _events(trace_path)
    # **분모는 trace가 아니라 결과 객체에서 온다.** 좌변을 만든 이벤트로 우변을 세면
    # 등식이 자기 자신을 증명한다.
    reached_critic = len(result.findings)
    accounted = (
        len(_of(events, "critic_skipped"))
        + len(_of(events, "critic_failed"))
        + len(_of(events, "gate_eval", stage=Stage.CRITIC))
    )
    assert accounted == reached_critic, f"7단계 분할이 안 닫힌다: {accounted} != {reached_critic}"


def test_the_symbols_actually_analysed_are_named_in_the_trace(tmp_path: Path) -> None:
    """**실행한 대상의 이름이 남는다 (D45).**

    `run_20260827_0004`의 정답 대조에서 "근본 원인 `router_define`이 후보 19건에 있었는가"를
    물었는데 run 산출물로 답할 수 없었다. `target_plan`은 `symbol_targets: 19`처럼 개수만
    싣고, 이름이 남는 곳은 `target_skipped`뿐인데 그 run은 `deferred: 0`이라 0건이었다.
    `Candidate.location`도 못 메운다 — 그것은 모델이 쓴 값이고 그 run에서는 `symbol`이 전부
    `null`이었다. 인덱서는 이름을 알고 있는데 그 값이 실행 기록으로 안 건너왔다.
    """
    _, trace_path = _run(tmp_path, name="planned", before=_with_index())

    events = _events(trace_path)
    planned = _of(events, "target_planned")
    # 인덱서가 아는 이름이 그대로 온다. 개수가 아니라 이름이 이 테스트의 대상이다.
    assert {e["data"]["symbol"] for e in planned} == {s["name"] for s in _FIXTURE_INDEX["symbols"]}
    # 파일·줄도 함께 온다 — 이름만으로는 같은 이름의 `static` 함수 둘을 못 가른다(W2).
    assert {(e["data"]["file"], e["data"]["line_start"]) for e in planned} == {
        (s["file"], s["line_start"]) for s in _FIXTURE_INDEX["symbols"]
    }


def test_the_planned_list_and_the_planned_count_agree(tmp_path: Path) -> None:
    """`count(target_planned) = target_plan.planned`, 그리고 `target_plan`은 여전히 1건이다.

    **분모를 새로 만들지 않는 것이 이 이벤트의 조건이다.** 같은 수를 두 곳에서 말하되 한쪽은
    개수, 한쪽은 이름이다. 두 수가 갈리면 어느 쪽을 믿을지가 사라지고, `target_plan`이 run당
    1건이라는 계약(이 파일의 다른 검사가 잠근다)도 함께 지켜져야 목록이 분모를 대체하지
    않는다.
    """
    _, trace_path = _run(tmp_path, name="planned-count", before=_with_index())

    events = _events(trace_path)
    plan = _of(events, "target_plan")
    assert len(plan) == 1, "target_plan은 run당 1건이다 — 목록이 분모를 대체하지 않는다"
    assert len(_of(events, "target_planned")) == plan[0]["data"]["planned"]


def _critic_context_overflow() -> ModelGatewayError:
    """`run_20260828_0001`이 7단계에서 받은 400을 그대로 옮긴 예외 (D44 후속).

    **합성이 아니다.** `output_max: 8192`가 실리면서 `입력 24,577 + 출력 8,192 = 32,769`이
    `--max-model-len 32768`을 넘겨 vLLM이 요청을 거절했다. 상한이 없던 시절에는 서버가
    남은 공간만큼 생성하고 끝났을 요청이다.
    """
    detail = (
        "This model's maximum context length is 32768 tokens. However, you requested "
        "8192 output tokens and your prompt contains at least 24577 input tokens, "
        "for a total of at least 32769 tokens."
    )
    return ModelGatewayError(f"HTTP 400 {detail}", status=400, detail=detail)


def test_a_request_level_rejection_in_critic_drops_only_that_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**크기 400은 이 Finding에 대한 사실이지 run에 대한 사실이 아니다 (D44 후속).**

    처음에는 critic 루프에서 `ModelGatewayError`를 일부러 안 잡았고, 그 판단대로
    `run_20260828_0001`이 7단계 400에서 `stopped_after: 7`로 끊겼다 — 후보 단위였다면
    `_triage_all`이 하나만 버리고 완주했을 사건이다. 판정을 복사하지 않고 `_is_droppable`로
    빼서 두 자리가 같은 규칙을 쓰게 했다.
    """
    _failing_model(monkeypatch, [_critic_context_overflow()], role_under_test=Role.CRITIC)

    result, trace_path = _run(tmp_path, name="critic-400", before=_with_index())

    failed = _of(_events(trace_path), "critic_failed")
    assert [e["data"]["reason"] for e in failed] == ["gateway_error"]
    assert "32768" in failed[0]["data"]["error_detail"]
    # **run이 끊기지 않는다** — 이 테스트의 존재 이유다.
    assert result.record.is_complete
    assert (trace_path.parent / "run_manifest.json").is_file()


def test_a_server_side_gateway_error_in_critic_still_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**5xx는 서버 쪽 사건이라 그대로 올려 보낸다.** 남은 Finding을 태워도 얻는 게 없다.

    `_is_droppable`이 가르는 축이 이것이다 — 크기 400은 요청 하나의 문제이고 503은 run의
    문제다(크기가 아닌 400도 run의 문제라 첫 건에서 멈춘다 — D88).
    올려 보내도 `run_pipeline`이 받아 매니페스트까지 가므로 산출물은 남는다.
    """
    _failing_model(
        monkeypatch,
        [ModelGatewayError("HTTP 503", status=503, detail="overloaded")],
        role_under_test=Role.CRITIC,
    )

    result, trace_path = _run(tmp_path, name="critic-503", before=_with_index())

    assert _of(_events(trace_path), "critic_failed") == []
    assert _run_transition(trace_path)["data"]["reason"] == "model_gateway_unavailable"
    assert (trace_path.parent / "run_manifest.json").is_file()
    assert not result.record.is_complete


# ---------------------------------------------------------------------------
# D88 — 400은 크기만의 값이 아니다
#
# vLLM(0.27.1)은 컨텍스트 초과 말고도 요청 모양·서버 설정 때문에 400을 준다. **크기 400만**
# 「이 후보에 대한 사실」이고, 나머지 400은 다음 요청도 같으므로 첫 건에서 멈춘다. 크기 400이라도
# **한 자리가 시도한 대상에서 한 번도 성공하지 못하면** 그 자리는 아무것도 못 봤다 — 루프 끝에서
# 멈춘다(`run_20260925_0006`의 모양).
# ---------------------------------------------------------------------------


# 이 절의 서버 문구 핀은 전부 실측이다(`unreadable_body` 하나만 어댑터 자신의 문구다).
# 2026-09-26 ATOM 프로브의 원문은 `_PROBE` §3 「근거 로그」에 있고 아래는 그 줄을 인용한다 —
# vLLM 0.27.1이 준 본문을 하네스 어댑터가 받은 `detail` 그대로다. 문구를 만드는 소스도 함께 적는다.
_PROBE = "docs/experiments/harness/2026-09-26-vllm-400-bodies-atom.md"

# 양성 핀 — 크기 거절. **저장소의 실측 문구다**(보고서 파일:줄).
_SIZE_REJECTIONS = {
    # docs/experiments/harness/2026-08-25-serving-nvfp4-atom.md:236-239
    "2026-08-25": (
        "This model's maximum context length is 32768 tokens. However, you requested 0 output "
        "tokens and your prompt contains at least 32769 input tokens, for a total of at least "
        "32769 tokens. Please reduce the length of the input prompt or the number of requested "
        "output tokens. (parameter=input_tokens, value=32769)"
    ),
    # docs/experiments/harness/2026-08-28-runA-harness-validation-atom.md:334-335 — 보고서가
    # 잘라 옮긴 모양 그대로다(본문 JSON째).
    "2026-08-28": (
        '{"error":{"message":"This model\'s maximum context length is 32768 tokens. '
        "However, you requested 8192 output t…"
    ),
    # docs/experiments/harness/2026-09-26-slice-smoke-atom.md:165-166 — vLLM 0.27.1
    "2026-09-26": (
        "This model's maximum context length is 16384 tokens. However, you requested 4096 "
        "output tokens and your prompt contains at least 12289 input tokens, for a total of "
        "at least 16385"
    ),
    # _PROBE:98 — 같은 날 프로브. 어댑터가 받은 본문 JSON째다(`param: input_tokens`).
    # 소스는 vllm/renderers/params.py:449-459.
    "2026-09-26-probe": (
        '{"error":{"message":"This model\'s maximum context length is 4096 tokens. However, '
        'you requested 64 output tokens and your prompt contains at least 4033 input tokens, '
        'for a total of at least 4097 tokens. Please reduce the length of the input prompt or '
        'the number of requested output tokens. (parameter=input_tokens, '
        'value=4033)","type":"BadRequestError","param":"input_tokens","code":400}}'
    ),
}

# 음성 핀 — 크기가 아닌 400. **2026-09-26 ATOM 실측 원문이다**(`_PROBE`의 줄). 고른 기준은
# 「우리 요청이 실제로 밟을 수 있는가」이고, 줄마다 그 경로를 적는다.
_RUN_REJECTIONS = {
    # _PROBE:102 — vllm/v1/engine/input_processor.py:111-119. 요청에 `thinking_token_budget`을
    # 싣는데 서버를 `--reasoning-parser` 없이 띄웠다(`ModelEntry.thinking_token_budget`).
    "thinking_budget_without_parser": (
        '{"error":{"message":"thinking_token_budget is set but reasoning_config is not '
        'configured. Please set --reasoning-parser and/or --reasoning-config to use '
        'thinking_token_budget.","type":"BadRequestError","param":null,"code":400}}'
    ),
    # _PROBE:108 — vllm/v1/engine/input_processor.py:121-125. 같은 예산을 V2 러너 모델에
    # 실었다(`src/config.py:251-252`가 적는 그 400이다). parser가 있어도 난다.
    "thinking_budget_on_v2_runner": (
        '{"error":{"message":"thinking_token_budget is not yet supported by the V2 model '
        'runner. Run vLLM with VLLM_USE_V2_MODEL_RUNNER=0 to use '
        'thinking_token_budget.","type":"BadRequestError","param":null,"code":400}}'
    ),
    # _PROBE:104 — Qwen/Qwen3.8-27B@1d4bf0f `chat_template.jinja:46-49`. 사고를 켠 채 템플릿이
    # 모르는 `reasoning_effort`(`high`)를 보냈다. jinja `TemplateError`를 vLLM이 400으로 바꾼다.
    "template_rejects_reasoning_effort": (
        '{"error":{"message":"Unexpected reasoning effort high. Supported types are xhigh '
        '(default), medium, and low.","type":"BadRequestError","param":null,"code":400}}'
    ),
    # _PROBE:100 — vllm/renderers/params.py:225-237. 역할 출력 상한이 서버 창보다 크다
    # (`output_max_by_role`).
    "output_cap_over_window": (
        '{"error":{"message":"max_tokens=8192 cannot be greater than '
        'max_model_len=max_total_tokens=4096. Please request fewer output tokens. '
        '(parameter=max_tokens, value=8192)","type":"BadRequestError","param":"max_tokens","cod'
        'e":400}}'
    ),
    # _PROBE:106 — vllm/entrypoints/serve/utils/server_utils.py:498-541. 요청 본문 검증 —
    # 422가 아니라 400이다(`type`도 `Bad Request`).
    "request_validation": (
        '{"error":{"message":"1 validation error:\\n {\'type\': \'literal_error\', \'loc\': '
        '(\'body\', \'reasoning_effort\'), \'msg\': \\"Input should be \'none\', \'minimal\', '
        '\'low\', \'medium\', \'high\', \'xhigh\' or \'max\'\\", \'input\': \'bogus\', '
        '\'ctx\': {\'expected\': \\"\'none\', \'minimal\', \'low\', \'medium\', \'high\', '
        '\'xhigh\' or \'max\'\\"}}","type":"Bad Request","param":"body.reasoning_effort","code"'
        ':400}}'
    ),
    # src/adapters/openai_compat.py:354 — 400인데 본문을 못 읽었다(어댑터 자신의 문구 — 서버
    # 실측이 아니다). 모르는 400은 크기로 안 읽는다.
    "unreadable_body": "(응답 본문을 읽지 못했다: OSError)",
}


def test_the_observed_400_pins_are_quoted_from_the_probe_report() -> None:
    """**실측 핀은 보고서 원문과 글자 그대로 같다.** 핀을 고치고 보고서를 안 고치면(또는 거꾸로)
    「실측」이라는 말이 거짓이 된다 — 그래서 대조를 기계에 맡긴다. 어댑터 자신의 문구
    (`unreadable_body`)와 다른 날 보고서의 핀은 이 보고서에 없으므로 뺀다.
    """
    report = (Path(__file__).resolve().parents[1] / _PROBE).read_text(encoding="utf-8")
    observed = {
        name: detail
        for name, detail in {**_SIZE_REJECTIONS, **_RUN_REJECTIONS}.items()
        if name not in {"2026-08-25", "2026-08-28", "2026-09-26", "unreadable_body"}
    }
    assert len(observed) == 6, "전제: 프로브 원문 핀이 여섯이다"
    missing = [name for name, detail in observed.items() if detail not in report.splitlines()]
    assert missing == [], f"보고서 원문과 다른 핀: {missing}"


def _rejected(detail: str) -> ModelGatewayError:
    """어댑터가 400에 올리는 예외 모양(`openai_compat._post`) 그대로."""
    return ModelGatewayError(f"HTTP 400 {detail}", status=400, detail=detail)


def _every_call_fails(monkeypatch: pytest.MonkeyPatch, broken: Role, error: Exception) -> None:
    """`broken` 역할의 모델 호출을 **매번** 같은 예외로 실패시킨다(`_failing_model`은 N번째만)."""
    original = FakeModelClient.complete

    def complete(self, *, role, prompt, schema, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if role is broken:
            raise error
        return original(self, role=role, prompt=prompt, schema=schema, **kwargs)

    monkeypatch.setattr(FakeModelClient, "complete", complete)


def _calls(trace_path: Path, role: Role) -> list[dict]:
    """그 역할의 `model_call` 이벤트. 러너가 몇 번 불렀는지를 trace에서 센다."""
    return [e for e in _of(_events(trace_path), "model_call") if e["data"]["role"] == role.value]


@pytest.mark.parametrize("detail", list(_SIZE_REJECTIONS.values()), ids=list(_SIZE_REJECTIONS))
def test_a_context_overflow_400_is_a_size_rejection(detail: str) -> None:
    """양성 핀. 이것이 빨개지면 큰 후보 하나가 run을 세운다 — W2가 견디겠다고 한 경우다."""
    assert runner._is_size_rejection(_rejected(detail))


@pytest.mark.parametrize("detail", list(_RUN_REJECTIONS.values()), ids=list(_RUN_REJECTIONS))
def test_a_400_without_the_overflow_phrase_is_not_a_size_rejection(detail: str) -> None:
    """음성 핀. 이것이 빨개지면 설정 400이 다시 「이 후보가 크다」로 읽혀 run이 안 멈춘다 (D88)."""
    assert not runner._is_size_rejection(_rejected(detail))


def test_a_413_is_a_size_rejection_whatever_its_body_says() -> None:
    """413은 본문 초과다 — 서버 문구로 가르는 것은 400뿐이다."""
    assert runner._is_size_rejection(ModelGatewayError("HTTP 413", status=413))


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_a_config_400_at_the_auditor_stops_the_run_at_the_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """**설정 400은 다음 요청도 같다 — 첫 건에서 멈춘다 (D88).**

    Auditor를 `--reasoning-parser` 없이 띄우면 사고 예산을 실은 요청이 전부 400이다. 예전에는
    400이면 전부 크기 거절로 읽어 카운터에 안 셌고, run은 Auditor 결과가 전부 빈 채 완주했다.

    **세지 않고 첫 건에서 멈추는 이유.** 후보 단위에서는 Scout가 안 올린 대상이 카운터를 0으로
    되돌리고 키가 Scout 자리라(D73), 세는 방식으로는 Auditor가 전부 거절해도 안 설 수 있다.
    """
    _every_call_fails(
        monkeypatch, Role.AUDITOR, _rejected(_RUN_REJECTIONS["thinking_budget_without_parser"])
    )

    configure = None if mode is None else _with_mode(mode)
    result, trace_path = _run(tmp_path, before=_with_index(), configure=configure)

    assert len(_calls(trace_path, Role.AUDITOR)) == 1, "설정 400을 받고도 다음 대상을 또 불렀다"
    assert result.record.stopped_after == int(Stage.AUDITOR)
    assert not result.record.is_complete
    # 서버 사망(`model_gateway_unavailable`)과 가른다 — 서버는 살아 있고 요청을 거절했다.
    assert _run_transition(trace_path)["data"]["reason"] == "consecutive_target_failures"
    assert (trace_path.parent / "run_manifest.json").is_file()


def test_a_config_400_in_critic_stops_the_run_at_the_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Critic 루프에는 카운터가 없다 — 그래도 설정 400은 첫 건에서 멈춘다 (D88).**

    카운터를 두지 않는 이유(`_drop_finding` — 상한이 다른 두 루프에 같은 카운터를 두면 어느
    쪽이 닿았는지 사유에서 사라진다)는 그대로다. 첫 건 정지는 카운터가 아니다.
    """
    _every_call_fails(
        monkeypatch, Role.CRITIC, _rejected(_RUN_REJECTIONS["template_rejects_reasoning_effort"])
    )

    result, trace_path = _run(tmp_path, name="critic-config-400", before=_with_index())

    assert len(_calls(trace_path, Role.CRITIC)) == 1, "설정 400을 받고도 다음 Finding을 또 불렀다"
    assert result.record.stopped_after == int(Stage.CRITIC)
    assert not result.record.is_complete
    assert _run_transition(trace_path)["data"]["reason"] == "consecutive_target_failures"


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_a_scout_that_never_succeeds_stops_the_run_even_on_size_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """**`run_20260925_0006`의 모양 — 서버 창이 작아 모든 호출이 크기 400이다 (D88).**

    크기 400 하나하나는 그 후보에 대한 사실이라 카운터에 안 센다. 그러나 한 자리가 시도한 대상
    전부에서 거절되면 그 자리는 아무것도 못 봤다 — 그 run을 완주로 두면 「취약점이 없다」로 읽힌다.

    **끝까지 시도한 뒤에** 멈춘다. 앞 몇 건만 보고 멈추면 큰 대상이 정렬 앞에 몰린 run이 늘 같은
    자리에서 선다 — `test_three_oversized_targets_in_a_row_do_not_stop_the_run`이 그 짝이다.
    """
    _every_call_fails(monkeypatch, Role.SCOUT, _context_overflow())

    configure = None if mode is None else _with_mode(mode)
    result, trace_path = _run(tmp_path, before=_with_index(), configure=configure)

    assert len(_calls(trace_path, Role.SCOUT)) == len(_FIXTURE_INDEX["symbols"]), (
        "끝까지 시도하지 않았다 — 앞 몇 건으로 멈추면 정상 창에서도 선다"
    )
    assert not result.record.is_complete, "Scout가 한 번도 성공 못 했는데 run이 완주로 남는다"
    assert _run_transition(trace_path)["data"]["reason"] == "consecutive_target_failures"


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_an_auditor_that_never_succeeds_stops_the_run_even_when_the_scout_seat_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """**백스톱의 키는 그 단계에서 모델을 부른 자리다 — Scout 자리가 아니다 (D88, D73).**

    후보 단위에서는 Scout가 안 올린 대상이 그 자리의 「성공」이다. 키를 Scout 자리로 두면 Auditor가
    전부 거절돼도 안 선다 — 이 fixture에서 대상 넷 중 Auditor까지 가는 것은 일부뿐이다.
    """
    _every_call_fails(monkeypatch, Role.AUDITOR, _context_overflow())

    configure = None if mode is None else _with_mode(mode)
    result, trace_path = _run(tmp_path, before=_with_index(), configure=configure)

    auditor = _calls(trace_path, Role.AUDITOR)
    assert auditor and not any(e["data"]["ok"] for e in auditor), "전제: Auditor가 전부 거절됐다"
    assert len(auditor) < len(_calls(trace_path, Role.SCOUT)), "전제: Scout가 안 올린 대상이 있다"
    assert not result.record.is_complete, "Auditor가 한 번도 성공 못 했는데 run이 완주로 남는다"


def test_a_critic_that_never_succeeds_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """고른 Finding 전부에서 Critic이 실패하면 7단계는 아무것도 못 봤다 (D88)."""
    _every_call_fails(monkeypatch, Role.CRITIC, _critic_context_overflow())

    result, trace_path = _run(tmp_path, name="critic-starved", before=_with_index())

    critic = _calls(trace_path, Role.CRITIC)
    assert len(critic) > 1, "전제: Critic이 둘 이상을 봤다 — 하나뿐이면 첫 건 정지와 못 가른다"
    assert result.record.stopped_after == int(Stage.CRITIC)
    assert not result.record.is_complete


def test_a_seat_whose_only_attempt_fails_saw_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**작은 run에도 바닥이 없다 (D88, 리뷰 MEDIUM).**

    자리당 시도가 한 건이면 그 한 건의 실패 — 스키마 실패 한 번이어도 — 로 run이 선다. 예전에는
    연속 상한(3)에 못 닿아 완주했다. 그 run은 그 자리에서 아무것도 못 봤으므로 완주로 두지 않는
    것이 성공 0 백스톱의 정의다.

    **최소 시도 수를 넣어 이 검사를 고치지 마라.** 그 수는 근거 없는 상수이고, 넣는 순간 대상
    하나짜리 run의 창 오설정이 다시 완주로 남는다.
    """
    _failing_model(monkeypatch, [SchemaParseError("단 한 번의 스키마 실패(테스트)")])

    result, trace_path = _run(tmp_path, name="one-shot", before=_with_index(), candidate_max=1)

    assert len(_calls(trace_path, Role.SCOUT)) == 1, "전제: 자리당 시도가 한 건뿐이다"
    assert not result.record.is_complete, "그 자리가 아무것도 못 봤는데 run이 완주로 남는다"
    assert _run_transition(trace_path)["data"]["reason"] == "consecutive_target_failures"


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_a_pool_seat_whose_every_call_overflows_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """**4단계는 자리마다 모델·프롬프트가 다르다** — 한 자리가 전부 크기 400이면 그 자리는
    아무것도 못 봤다. D68이 체계적 실패로 멈추게 한 「Scout 커버리지 1/3이 빈 run」을 크기
    400으로도 멈춘다 (D88).
    """
    _every_call_fails(monkeypatch, Role("scout_boundary"), _context_overflow())

    result, _ = _run(tmp_path, before=_with_index(), configure=_poolify(mode))

    assert {c.scout_member for c in result.candidates} == {"scout_memory", "scout_injection"}, (
        "전제: 나머지 두 자리는 후보를 냈다"
    )
    assert not result.record.is_complete, "한 자리가 한 번도 성공 못 했는데 run이 완주로 남는다"


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_one_seats_candidates_overflowing_the_auditor_do_not_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    """**5단계부터는 모델을 부르는 역할이 Auditor 하나다 (D88).**

    백스톱을 Scout 자리(`item.role`)로 키 잡으면 한 자리의 후보들만 Auditor 창을 넘어도 「그
    자리가 한 번도 성공 못 했다」가 되어 run이 선다. Auditor는 다른 자리 후보에서 멀쩡히 돌았으므로
    오정지다 — 크기 400은 여전히 그 후보에 대한 사실이다.

    자리를 고르는 수단이 호출 순서다(Auditor 프롬프트는 자리를 안 싣는다). 팬아웃 순서가
    `SCOUT_POOL_ROLES` 그대로라(`runner._scout_roles`) 셋째마다 둘째가 `scout_boundary`다 —
    그 전제를 trace로 확인한다.
    """
    original = FakeModelClient.complete
    calls = {"n": 0}

    def complete(self, *, role, prompt, schema, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if role is Role.AUDITOR:
            index = calls["n"]
            calls["n"] += 1
            if index % len(SCOUT_POOL_ROLES) == 1:
                raise _context_overflow()
        return original(self, role=role, prompt=prompt, schema=schema, **kwargs)

    monkeypatch.setattr(FakeModelClient, "complete", complete)

    result, trace_path = _run(tmp_path, before=_with_index(), configure=_poolify(mode))

    skipped = _of(_events(trace_path), "target_skipped", stage=Stage.AUDITOR)
    assert skipped, "전제: Auditor 거절이 났다"
    assert {e["data"]["scout_member"] for e in skipped} == {"scout_boundary"}, (
        "전제: 한 자리(scout_boundary)의 Auditor 호출만 거절했다"
    )
    assert result.record.is_complete, (
        "Auditor는 다른 자리 후보에서 성공했는데 run이 섰다 — 백스톱 키가 Scout 자리다"
    )


# ---------------------------------------------------------------------------
# ADR 0009 결과 2 — 러너의 단계 단위 루프
#
# `execution.mode`는 PR B가 스키마로 열었고 **이 PR이 러너를 그 값에 붙였다.**
# 아래 둘은 짝이다 — 하나는 단계 단위가 실제로 도는지를, 다른 하나는 기본값이
# 안 바뀌었는지를 본다. 두 번째가 없으면 첫 번째를 만족시키려고 기본 경로를 바꿔도
# 초록이 나온다.
# ---------------------------------------------------------------------------


def _per_stage(config: object) -> object:
    """`execution.mode: per_stage`를 선언한 config를 만든다.

    `config/profiles.yaml`을 고치지 않는다 — 프로필에 `execution`을 적는 것은 ADR 0009
    결과 **4**이고 그 값은 교대 실측 뒤에 정해진다. 여기서 필요한 것은 러너가 그 값을
    보는지뿐이라 config 객체에서만 세운다.

    `_run(..., configure=_per_stage)`로 넘긴다 — 두 ADR-0009 검사가 **같은 실행 경로**를
    타야 한다(@sumin0218 리뷰 #161-3).
    """
    from src.config import ExecutionPolicy

    profile = config.profile.transition(execution=ExecutionPolicy(mode="per_stage"))  # type: ignore[attr-defined]
    return config.transition(profile=profile)  # type: ignore[attr-defined]


def _stage_order(trace_path: Path, kind: str = "transition") -> list[int]:
    """trace에 실린 `kind` 이벤트의 단계 번호를 seq 순으로 뽑는다."""
    return [e["stage"] for e in _events(trace_path) if e["kind"] == kind and e["stage"] is not None]


def _is_non_decreasing(values: list[int]) -> bool:
    return all(a <= b for a, b in zip(values, values[1:], strict=False))


def test_per_candidate_is_the_default_and_interleaves_stages(tmp_path: Path) -> None:
    """**기본값은 지금 동작이다.** 이 검사는 PR C 전후로 똑같이 통과해야 한다.

    후보 단위에서는 target 하나가 3→6을 관통하고 다음 target이 다시 3으로 돌아온다.
    그래서 단계 번호 열이 **단조 증가가 아니다** — 되돌아오는 자리가 있다.

    ⚠ **이 검사가 PR C의 안전망이다.** 단계 단위 루프를 넣으면서 기본 경로까지 바꾸면
    선언 안 한 프로필의 run이 조용히 달라지고, ADR 0009이 이 결정을 「되돌리기 싼」
    것으로 부른 근거가 사라진다.
    """
    config = load_config("dev_fake")
    assert config.profile.execution is None or config.profile.execution.mode == "per_candidate"

    _, trace_path = _run(tmp_path, name="default", before=_with_index())

    stages = _stage_order(trace_path)
    assert stages, "전이 이벤트가 하나도 없다 — 검사가 아무것도 안 본다"
    assert not _is_non_decreasing(stages), (
        f"후보 단위인데 단계가 단조 증가다: {stages}. target이 하나뿐이면 이 검사는 "
        "아무것도 못 본다 — 픽스처를 확인해라"
    )


def test_per_stage_finishes_a_stage_for_every_target_before_the_next_starts(
    tmp_path: Path,
) -> None:
    """**단계 단위의 정의 그 자체다.** 모든 target이 K단계를 마쳐야 K+1이 시작한다.

    이것이 성립해야 「한 번에 한 역할의 모델만 상주」가 가능하다. ADR 0009이 교대
    횟수를 512회[계산]에서 3~5회[계산]로 줄인다고 적은 근거가 이 순서다 — 순서가
    안 서면 그 수는 나오지 않는다.

    **단계 번호가 단조 증가여야 한다.** 되돌아가는 자리가 하나라도 있으면 그 지점에서
    앞 단계 모델을 다시 깨워야 한다.
    """
    _, trace_path = _run(tmp_path, name="stage", before=_with_index(), configure=_per_stage)

    stages = _stage_order(trace_path)
    assert stages, "전이 이벤트가 하나도 없다"
    assert _is_non_decreasing(stages), (
        f"per_stage인데 단계가 되돌아간다: {stages}. 러너가 `execution.mode`를 안 보고 있다"
    )


def test_every_triage_step_takes_the_same_arguments() -> None:
    """`_TRIAGE_STEPS`의 콜러블 전부가 같은 시그니처여야 한다 (@sumin0218 리뷰 #161-1).

    **하나라도 다르면 디스패처가 특수분기를 갖고, 그 항목은 표에서 죽은 값이 된다.**
    표는 권위 있어 보이는데 아무도 그 칸을 안 읽으므로, 단계를 재배열하거나 앞에
    하나 끼울 때 표는 맞아 보이는데 엉뚱한 함수가 돈다.

    **재현으로 크기를 쟀다.** 표의 `Stage.SCOUT`↔`Stage.AUDITOR` 콜러블을 맞바꾸면
    **64 failed**로 시끄럽게 잡히는데, `Stage.RETRIEVAL` 항목을 `_step_gate`로 바꿔도
    **104 passed**였다 — 첫 항목에 무엇을 넣어도 아무도 몰랐다. `_step_retrieval`만
    `(ctx, item)`이라 `_run_step`이 `if stage is Stage.RETRIEVAL`로 우회했기 때문이다.

    ⚠ **이 검사가 막는 것은 「죽은 표 항목」이지 시그니처 취향이 아니다.** 새 단계를
    더할 때 `state`를 안 쓰더라도 받아라 — 안 받으면 디스패처가 다시 갈라진다.
    """
    expected = ["ctx", "item", "state"]
    for stage, step in runner._TRIAGE_STEPS:
        params = list(inspect.signature(step).parameters)
        assert params == expected, (
            f"{stage.name}의 `{step.__name__}`이 {params}를 받는다 — 표의 다른 항목과 "
            f"다르면 디스패처가 이 단계를 특수분기로 부르게 되고 표의 이 칸은 죽는다"
        )


def test_the_triage_table_covers_stages_three_to_six_in_order() -> None:
    """표가 3~6단계를 순서대로 정확히 덮어야 한다.

    두 실행 모드가 이 표 하나를 읽으므로(`_triage_by_candidate`·`_triage_by_stage`),
    표가 곧 「3~6단계가 무엇인가」의 정의다. 단계를 더하거나 빼면 여기가 먼저 바뀐다.

    ⚠ **4단계(Scout)가 두 행이다**(ADR 0012 결정 2) — 개시(hop0)와 정착(동료 확인 →
    need_evidence 루프 → 판정)으로 나뉜다. 둘 다 `Stage.SCOUT`이라 `_triage_by_stage`의
    단계 컷(`last < stage`)은 그대로고, `_run_stage_for_all`이 이 표를 따라 한 번은
    개시로, 한 번은 정착으로 전 target을 돈다 — 모든 좌석의 개시가 어느 정착보다 먼저
    끝나는 것이 그래서 성립한다.
    """
    assert [stage for stage, _ in runner._TRIAGE_STEPS] == [
        Stage.RETRIEVAL,
        Stage.SCOUT,
        Stage.SCOUT,
        Stage.AUDITOR,
        Stage.GATE,
    ]
    assert [step.__name__ for _, step in runner._TRIAGE_STEPS] == [
        "_step_retrieval",
        "_step_scout_open",
        "_step_scout_settle",
        "_step_auditor",
        "_step_gate",
    ]


@pytest.mark.parametrize(
    ("step", "field", "stage_pair"),
    [
        (runner._step_scout_open, "capsule", "4단계가 3단계보다"),
        (runner._step_scout_settle, "scout_session", "정착이 개시보다"),
        (runner._step_auditor, "candidate", "5단계가 4단계보다"),
        (runner._step_gate, "finding", "6단계가 5단계보다"),
    ],
)
def test_a_step_run_out_of_order_raises_instead_of_flowing_none_onward(
    step: object, field: str, stage_pair: str
) -> None:
    """앞 단계가 안 채운 값으로 뒷 단계를 부르면 **경계에서 멈춰야 한다**.

    **왜 전용 예외인가 (@sumin0218 리뷰 #161-4).** 원래는 `assert`였다. `assert`는
    `python -O`에서 사라져 `None`이 노드 깊숙이 흘러 불명확한 `TypeError`가 된다.
    그리고 `origin/main`의 `src/`에는 `assert`가 **0건**이다 — 이 저장소가 불변식을
    알리는 방식은 전용 예외다(`ConfigError`·`PromptError`·`BenchApplyError`·
    `_TooManyDrops`).

    **타입이 `ModelGatewayError`·`SchemaParseError`와 달라야 하는 이유가 핵심이다.**
    저 둘은 "이 후보가 실패했다"라 `_drop`이 그 후보만 버리고 계속 가는데, 이쪽은
    "루프가 단계를 잘못된 순서로 돌았다"라 다음 후보도 똑같이 실패한다. 같은 타입이면
    폐기 카운터가 배선 결함을 후보 실패로 세고, 상한을 채워 `_TooManyDrops`가 되면서
    **원인이 「연속 target 실패」로 기록된다.**

    ⚠ **이 검사가 없으면 가드를 지워도 스위트가 초록이다.** 실제로 확인했다 — 가드를
    떼고 전체를 돌리면 1141건이 전부 통과했다. 그래서 여기 있다.
    """
    item = runner._InFlight(target=object(), reached=runner._Reached())  # type: ignore[arg-type]

    with pytest.raises(runner._StageOutOfOrder, match=stage_pair):
        step(object(), item, object())  # type: ignore[operator]

    assert getattr(item, field) is None, "가드가 값을 채우지 않고 거절해야 한다"


def test_a_timeout_while_the_server_was_generating_drops_only_that_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """타임아웃이어도 **서버가 그동안 토큰을 냈으면** 그 후보만 버린다 (#261 (다)).

    `run_20260924_0001`이 300,021 ms 타임아웃으로 멈췄는데 그 5분 동안 서버는 13.4 tok/s로
    생성하고 있었다(이슈 #261 「재현 / 근거」). 상태 코드가 없다는 것만으로는 「죽음」과
    「느림」이 안 갈린다 — 어댑터가 `/metrics` 생성 카운터로 가른 `generating`을 러너가 읽는다.
    """
    slow = ModelGatewayError(
        "http://127.0.0.1:8000/v1 호출 실패: timed out", detail="timed out", generating=True
    )
    _failing_model(monkeypatch, [slow])

    result, trace_path = _run(tmp_path, name="slow", before=_with_index())

    assert result.record.is_complete, "느린 후보 하나가 run을 세웠다 — #261이 그대로다"
    skipped = _of(_events(trace_path), "target_skipped")
    assert [e["data"]["reason"] for e in skipped] == ["gateway_error"]
    assert len(result.candidates) == len(_FIXTURE_INDEX["symbols"]) - 1


def test_a_timeout_with_no_generation_still_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """생성 카운터가 안 늘었으면(행) 또는 못 읽었으면(모름) 지금처럼 run을 멈춘다.

    「모름」을 「느림」으로 읽으면 죽은 서버 앞에서 후보를 연속 폐기 상한까지 태운다 —
    `None`은 통과가 아니다(`ModelEntry.family`와 같은 규칙).
    """
    for name, generating in (("hung", False), ("unknown", None)):
        stuck = ModelGatewayError(
            "http://127.0.0.1:8000/v1 호출 실패: timed out",
            detail="timed out",
            generating=generating,
        )
        _failing_model(monkeypatch, [stuck])

        _, trace_path = _run(tmp_path, name=name, before=_with_index())

        assert _run_transition(trace_path)["data"]["reason"] == "model_gateway_unavailable", name



def _with_definitions_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cli`가 읽는 설정에서 `auditor_definitions`를 켜고, Auditor 좌석을 **Fake가 아닌** v1
    엔트리로 바꾼다(#351 리뷰 MEDIUM-2). Fake Auditor는 가드 밖이라
    (`cli._undescribed_source_options` — Fake 클라이언트는 프롬프트 문장을 안 읽는다) 그대로
    두면 가드를 못 본다. 엔드포인트는 닿지 않는 주소다 -> run은 `execute` 전에 서고, doctor는
    접속 줄만 더 낸다."""
    real = cli.load_config

    def load(profile: str, **kwargs: object):  # noqa: ANN202
        config = real(profile, **kwargs)
        key = config.model_key_for("auditor")
        served = config.models[key].transition(
            endpoint="http://127.0.0.1:9/v1", family="not-fake-auditor"
        )
        return config.transition(
            thresholds=config.thresholds.transition(auditor_definitions=1),
            models={**config.models, "served_auditor": served},
            profile=config.profile.transition(
                roles={**config.profile.roles, "auditor": "served_auditor"}
            ),
        )

    monkeypatch.setattr(cli, "load_config", load)


# 위 헬퍼가 Auditor를 Fake 밖으로 옮기면 실서빙 프로필이 돼 라벨 없는 run은 그 가드에서 먼저 선다.
_SERVED_LABELS = ("--condition", "B3prime@pipeline", "--device", "atom", "--kb-snapshot", "kb-1")


def test_a_fake_auditor_is_not_held_by_the_source_options(tmp_path: Path) -> None:
    """dev 프로필(Fake Auditor · v1)은 옵션이 켜져 있어도 가드에 안 걸린다 -> GPU 없는 스모크가
    산다. 2026-10-03부터 `budgets.yaml`이 옵션 넷을 켠다(#349)."""
    assert load_config("dev_fake").thresholds.auditor_source_options_on()
    assert "source_options" not in _doctor_text("dev_fake", tmp_path)


def test_a_run_with_an_undescribed_auditor_source_option_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """켜진 Auditor SOURCE 옵션을 프롬프트 meta가 안 들면 run이 서지 않는다 — 프롬프트는 옛
    SOURCE를 서술하는데 오류 없이 도는 것을 막는다(#351 리뷰 MEDIUM-2)."""

    def _boom(*_: object, **__: object) -> None:
        raise AssertionError("가드를 지나 실행에 들어왔다")

    monkeypatch.setattr(cli, "execute", _boom)
    _with_definitions_on(monkeypatch)
    code = cli.main([
        "run", "--profile", "dev_fake", "--target", str(TARGET), "--seed", "1",
        *_SERVED_LABELS, "--artifacts-root", str(tmp_path / "arts"),
    ])

    err = capsys.readouterr().err
    assert code == 2
    assert [line for line in _errors(err) if "auditor_definitions" in line]
    assert not (tmp_path / "arts").exists() or not list((tmp_path / "arts").iterdir())


def test_a_run_that_stops_before_the_auditor_is_not_held_by_the_source_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auditor에 안 닿는 run(`--stop-after` 4)은 막지 않는다 — 어긋날 프롬프트를 안 부른다."""

    class _Started(RuntimeError):
        pass

    def _boom(*_: object, **__: object) -> None:
        raise _Started

    monkeypatch.setattr(cli, "execute", _boom)
    _with_definitions_on(monkeypatch)
    with pytest.raises(_Started):
        cli.main([
            "run", "--profile", "dev_fake", "--target", str(TARGET), "--seed", "1",
            *_SERVED_LABELS, "--stop-after", "4", "--artifacts-root", str(tmp_path / "arts"),
        ])


def test_doctor_errors_on_an_undescribed_auditor_source_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor는 같은 어긋남을 ERROR로 댄다 — thresholds는 프로필 공통이라 설정 자체의 문제다.
    Fake Auditor인 기본 dev 설정에는 그 줄이 없다."""
    assert "source_options" not in _doctor_text("dev_fake", tmp_path)
    _with_definitions_on(monkeypatch)
    text = _doctor_text("dev_fake", tmp_path)
    lines = [line for line in text.splitlines() if "source_options" in line]
    assert lines and all(line.startswith("[ERROR") for line in lines)
