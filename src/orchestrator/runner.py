"""순차 러너 — 단계 0~9를 잇는다 (ADR 0002, engineering-guide.md §1).

**v1은 순차다.** 큐를 스레드로 돌리지 않는다. 그렇게 하려면 StateStore·FakeModelClient·
TraceWriter·BudgetLedger의 전제를 함께 풀어야 한다(`queue.py` docstring).

`src/adapters/`를 import 하지 않는다. 어댑터 조립은 `src/cli.py`가 하고, 러너는 아래
`Dependencies` Protocol과 `read_source` 콜러블만 받는다 — 그래야 "구현체 교체가 설정만으로
가능"이 성립한다(engineering-guide.md §3, `tests/test_policy.py`가 강제).

## 예산 소진

`BudgetExhausted`는 실패가 아니라 **정상적인 종료 경로**다(architecture.md §5). 예외로
죽지 않고 그때까지 만든 Finding으로 9단계 리포트까지 간 뒤 `stopped_after`를 채운다
(전이 표 R-2). 부분 결과라는 사실은 `RunRecord.is_complete`가 False인 것으로 남는다.

## 대상 지정 단위

지금은 **파일 단위**로 캡슐을 만든다(`Location(file=...)`, symbol 없음). 실제 심볼
인덱스가 1단계에 들어오면 심볼 단위로 좁힌다 — 그때 후보 수가 늘고 제거율의 분모가
바뀌므로, 두 시점의 측정값을 직접 비교하지 않는다.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.config import HarnessConfig
from src.ids import IdFactory, sha256_text
from src.interfaces.model_client import ModelClient
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner
from src.orchestrator import state_machine
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)
from src.orchestrator.nodes.report import Report
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import MAX_STAGE, Stage
from src.orchestrator.trace import TraceWriter
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate
from src.schemas.common import Location
from src.schemas.enums import ArtifactKind, CapsuleLevel, FindingState
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import RunRecord

# 실제 KB 스냅샷이 붙기 전까지 쓰는 태그. 이 값이 그대로 기록되면 경고를 남긴다 —
# `RunRecord.kb_snapshot`은 재현성 필수 항목이다(engineering-guide.md §5).
DEFAULT_KB_SNAPSHOT = "kb-none"

# git을 못 읽었을 때의 자리표시자. 40 hex라는 형식은 지키되 경고 대상이다.
_UNKNOWN_COMMIT = "0" * 40

# 분석 대상이 되는 artifact 종류. config·unknown은 캡슐을 만들지 않는다.
_ANALYZABLE = (ArtifactKind.SOURCE, ArtifactKind.BINARY, ArtifactKind.FIRMWARE)

# `--dry-run`이 도달하는 마지막 단계. 여기까지는 모델을 부르지 않는다.
DRY_RUN_LAST_STAGE = int(Stage.RETRIEVAL)


class Dependencies(Protocol):
    """어댑터 묶음. `src/adapters/factory.py`의 `Deps`가 구조적으로 이것을 만족한다.

    Protocol로 받는 이유는 러너가 어댑터 패키지를 import 하지 않기 위해서다.
    """

    models: ModelClient
    retriever: Retriever
    tools: ToolRunner
    store: StateStore
    ids: IdFactory


@dataclass(frozen=True)
class RunSpec:
    """한 번의 실행을 결정하는 값 전부. 여기 없는 것은 config에서 온다."""

    condition: str
    target: Path
    stop_after: int | None = None
    dry_run: bool = False
    kb_snapshot: str = DEFAULT_KB_SNAPSHOT
    # 재현 실행에서 같은 run_id·타임스탬프가 나오도록 주입한다.
    started_at: datetime | None = None

    def last_stage(self) -> int:
        """이번 실행이 도달할 마지막 단계."""
        last = MAX_STAGE if self.stop_after is None else min(self.stop_after, MAX_STAGE)
        return min(last, DRY_RUN_LAST_STAGE) if self.dry_run else last


@dataclass(frozen=True)
class RunResult:
    """실행 산출물. 지표 집계는 여기가 아니라 trace에서 한다."""

    record: RunRecord
    report: Report
    candidates: tuple[Candidate, ...]
    findings: tuple[Finding, ...]
    warnings: tuple[str, ...]


class CacheHookUnbound(RuntimeError):
    """캐시 훅이 RunContext에 연결되기 전에 불렸다."""


class LateCacheHook:
    """Retriever는 생성 시점에 훅을 받는데 `RunContext`는 그 뒤에 만들어진다.

    조립 순서 때문에 생기는 구멍이라 늦은 바인딩으로 메운다. **바인딩 전 호출은
    조용히 무시하지 않고 터뜨린다** — 캐시 이벤트가 새면 cache hit ratio가 0으로
    보이고, 그것은 "캐시가 안 맞았다"와 구분되지 않는다.
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, key: str, hit: bool) -> None:
        if self._ctx is None:
            raise CacheHookUnbound("캐시 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_cache(key, hit)


@dataclass
class _Accumulator:
    """단계 사이에 들고 다니는 중간 산출물.

    예산 소진으로 중간에 끊겨도 그때까지의 결과로 리포트를 만들어야 하므로,
    지역 변수가 아니라 호출자가 들고 있는 객체에 쌓는다.
    """

    candidates: list[Candidate] = field(default_factory=list)
    findings: dict[str, Finding] = field(default_factory=dict)
    evidences: dict[str, tuple[Evidence, ...]] = field(default_factory=dict)
    missings: dict[str, tuple[MissingEvidence, ...]] = field(default_factory=dict)
    workdirs: dict[str, str] = field(default_factory=dict)

    def update(self, finding: Finding) -> None:
        self.findings[finding.finding_id] = finding

    def in_state(self, state: FindingState) -> list[Finding]:
        return [f for f in self.findings.values() if f.state is state]


def run_pipeline(
    spec: RunSpec,
    *,
    config: HarnessConfig,
    deps: Dependencies,
    read_source: Callable[[Location], str],
    trace: TraceWriter,
    run_dir: Path | None = None,
    cache_hook: LateCacheHook | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """단계 0~9를 순서대로 돌리고 `RunRecord`를 닫는다."""
    now = clock or (lambda: datetime.now(UTC))
    started_at = spec.started_at or now()
    ctx = RunContext(
        run_id=trace.run_id,
        config=config,
        models=deps.models,
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        read_source=read_source,
    )
    if cache_hook is not None:
        cache_hook.bind(ctx)

    artifacts = preprocess.run(ctx, source_root=spec.target)
    record, warnings = _new_record(spec, config, ctx.run_id, started_at, artifacts)
    ctx.record = record
    ctx.store.put(record)

    last = spec.last_stage()
    state = _Accumulator()
    stopped_after = None if last >= MAX_STAGE else last
    try:
        _analyze(ctx, artifacts, state, last)
    except BudgetExhausted as exc:
        stopped_after = exc.stage
        warnings.append(f"{exc.stage}단계에서 예산 소진: {exc.item} {exc.spent}/{exc.limit}")

    findings = list(state.findings.values())
    rendered = report.run(ctx, findings=findings)
    finished = state_machine.finish_run(
        record,
        finished_at=max(now(), started_at),
        stopped_after=stopped_after,
        store=ctx.store,
        trace=trace,
    )
    if run_dir is not None and not spec.dry_run:
        write_manifest(run_dir, finished)
    return RunResult(
        record=finished,
        report=rendered,
        candidates=tuple(state.candidates),
        findings=tuple(findings),
        warnings=tuple(warnings),
    )


def _analyze(ctx: RunContext, artifacts: list[Artifact], state: _Accumulator, last: int) -> None:
    """1~8단계. 9단계 리포트는 예산이 끊겨도 돌아야 하므로 호출자가 따로 부른다."""
    if last < Stage.INDEX:
        return
    graph = index.run(ctx, workdir=".")
    for artifact in [a for a in artifacts if a.kind in _ANALYZABLE]:
        _triage_one(ctx, artifact, graph, state, last)
    if last < Stage.CRITIC:
        return
    for finding in critic.select(ctx, state.in_state(FindingState.HYPOTHESIZED)):
        after, _ = critic.run(
            ctx,
            finding=finding,
            evidences=state.evidences.get(finding.finding_id, ()),
            missings=state.missings.get(finding.finding_id, ()),
        )
        state.update(after)
    if last < Stage.VALIDATE:
        return
    for finding in state.in_state(FindingState.VALIDATED):
        after, _ = validate.run(
            ctx, finding=finding, workdir=state.workdirs.get(finding.finding_id, ".")
        )
        state.update(after)


def _triage_one(
    ctx: RunContext, artifact: Artifact, graph: Evidence, state: _Accumulator, last: int
) -> None:
    """한 artifact를 2~6단계에 태운다. 승격되지 않은 후보는 거기서 멈춘다."""
    if last < Stage.SIGNALS:
        return
    target = Location(file=artifact.path)
    found = signals.with_signal(signals.run(ctx, workdir=artifact.path))
    evidences = (graph, *found)
    if last < Stage.RETRIEVAL:
        return
    capsule = retrieval.run(ctx, target=target, level=CapsuleLevel.LIGHT)
    if last < Stage.SCOUT:
        return
    candidate = scout.run(ctx, artifact=artifact, capsule=capsule, evidences=evidences)
    state.candidates.append(candidate)
    if candidate.state is not FindingState.TRIAGED or last < Stage.AUDITOR:
        return
    expanded = retrieval.run(ctx, target=target, level=CapsuleLevel.EXPANDED)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=expanded, evidences=evidences)
    state.update(finding)
    state.evidences[finding.finding_id] = evidences
    state.missings[finding.finding_id] = tuple(missings)
    state.workdirs[finding.finding_id] = artifact.path
    if last < Stage.GATE:
        return
    graded, _ = gate_node.run(
        ctx,
        finding=finding,
        evidences=evidences,
        missings=tuple(missings),
        candidate_location=candidate.location,
    )
    state.update(graded)


def _new_record(
    spec: RunSpec,
    config: HarnessConfig,
    run_id: str,
    started_at: datetime,
    artifacts: list[Artifact],
) -> tuple[RunRecord, list[str]]:
    """재현성 기록을 만든다. 빈 필수 항목은 경고로 남긴다.

    evaluation.md §6: 기록이 불완전한 run의 결과는 비교표에 넣지 않는다. 그러니 조용히
    비워 두는 대신, 무엇이 왜 비었는지를 호출자가 볼 수 있게 돌려준다.
    """
    commit, warnings = _repo_commit()
    record = RunRecord(
        run_id=run_id,
        condition=spec.condition,
        repo_commit=commit,
        source_hash=source_hash(artifacts),
        model_checksums=config.model_checksums(),
        prompt_versions=config.prompt_versions(),
        # 컨테이너 digest는 실제 Docker 실행이 붙어야 생긴다. 없는 값을 지어내면
        # 재현성 기록 자체가 거짓이 되므로 비워 두고 경고한다.
        tool_images={},
        budget=config.budget,
        thresholds=config.thresholds.flatten(),
        seed=config.seed,
        sampling=config.sampling,
        kb_snapshot=spec.kb_snapshot,
        started_at=started_at,
    )
    warnings.extend(incomplete_fields(record))
    return record, warnings


def incomplete_fields(record: RunRecord) -> list[str]:
    """비교표에 넣을 수 없게 만드는 빈 항목 (engineering-guide.md §5)."""
    warnings = []
    if not record.tool_images:
        warnings.append("tool_images가 비어 있다 (컨테이너 없이 실행). 모델 비교표에서 제외한다")
    if record.kb_snapshot == DEFAULT_KB_SNAPSHOT:
        warnings.append(f"kb_snapshot이 자리표시자({DEFAULT_KB_SNAPSHOT})다")
    if record.repo_commit == _UNKNOWN_COMMIT:
        warnings.append("repo_commit이 자리표시자다")
    return warnings


def source_hash(artifacts: list[Artifact]) -> str:
    """분석 대상 전체의 다이제스트 집계 (계획 D-d).

    `repo_commit`만으로는 미커밋 변경을 잡지 못하고, 대상이 저장소 밖일 수도 있다.
    경로를 정렬해 넣으므로 파일 순서가 달라져도 같은 값이 나온다.
    """
    lines = sorted(f"{artifact.path}:{artifact.digest}" for artifact in artifacts)
    return sha256_text("\n".join(lines))


def _repo_commit() -> tuple[str, list[str]]:
    """하네스 저장소의 HEAD. 못 읽으면 자리표시자와 경고를 돌려준다."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _UNKNOWN_COMMIT, [f"repo_commit을 읽지 못했다: {type(exc).__name__}"]
    commit = completed.stdout.strip()
    if len(commit) != 40:
        return _UNKNOWN_COMMIT, [f"repo_commit 형식이 아니다: {commit!r}"]
    return commit, []


def write_manifest(run_dir: Path, record: RunRecord) -> Path:
    """`run_manifest.json` = RunRecord. 이 파일이 없으면 그 run은 비교표에 못 들어간다."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run_manifest.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path
