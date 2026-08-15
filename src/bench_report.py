"""측정을 엮어 보고서를 만들고, `--apply`로 registry에 반영한다.

측정은 `src/bench.py`(시나리오 A~E)와 `src/bench_metrics.py`(`/metrics` 판독)에 있다.

측정 프리미티브와 나눈 이유는 수명이 다르기 때문이다. A~E는 무엇을 재는가이고, 여기는
**무엇을 §3 체크리스트의 답으로 인정하는가**다. 후자가 바뀌는 일이 훨씬 잦다.

`--apply`는 `config/models.yaml`의 `benchmark:` 블록만 **라인 단위로** 치환한다.
그 파일은 주석이 많고 **그 주석이 문서**다 — `yaml.safe_dump`로 다시 쓰면 전부 날아간다.
대상 라인을 확실히 찾지 못하면 아무것도 쓰지 않고 실패한다: 부분적으로 망가진 registry는
갱신 실패보다 훨씬 비싸다.
"""

import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from src.adapters.openai_compat import OpenAICompatModelClient
from src.bench import (
    DEFAULT_CONCURRENCY,
    DEFAULT_CONTEXTS,
    DEFAULT_SAMPLES,
    MEASUREMENT_ORDER,
    ROLE_SPEC,
    BenchApplyError,
    approx_tokens,
    check_startup,
    measure_memory,
    measure_prefix_cache,
    measure_structured_output,
    measure_throughput,
    sized_prompt,
)
from src.bench_metrics import METRIC_KV_USAGE
from src.config import PROMPTED_ROLES, HarnessConfig
from src.orchestrator.prompt_loader import load_prompt
from src.schemas.enums import Role

# `--apply`가 건드리는 키. `config.Benchmark`의 필드와 같아야 한다.
APPLY_KEYS = ("measured_at", "tok_s_single", "tok_s_batch32", "mem_gb_ctx8k")

PASS, FAIL, UNMEASURED = "pass", "fail", "unmeasured"

# model-serving.md §3 체크리스트 7개. 문구는 그 문서와 같게 두고, `source`로 이 도구의
# 어느 출력이 그 항목을 답하는지 못박는다 — 문서에서만 연결되면 둘이 갈라진다.
CHECKLIST: tuple[tuple[str, str], ...] = (
    ("vLLM 버전 × CUDA 13.0 × sm_121 커널 호환성 (기동 성공 여부)", "roles.*.startup.ok"),
    ("역할별 모델 로드 성공 및 실제 통합메모리 점유 측정", "roles.*.memory.mem_gb_ctx8k"),
    (
        "context 8K / 16K / 32K에서 KV cache 점유와 처리량",
        "roles.*.memory.by_context.*.kv_cache_usage_perc_peak",
    ),
    (
        "단일 스트림 tok/s vs 배치(8/16/32 동시 요청) 합산 tok/s",
        "roles.*.throughput.tok_s_single / .by_concurrency",
    ),
    ("automatic prefix caching 동작 확인 및 적중률", "roles.*.prefix_cache.verdict"),
    ("structured output(JSON schema 강제) 동작 확인", "roles.*.structured_output.valid_ratio"),
    ("NVFP4 양자화 시 Critic 반박 정확도 저하 여부", "미구현 — 평가셋 준비 중"),
)

FAKE_SKIP = "fake:// 엔드포인트라 측정 대상이 아니다 — 붙을 서버가 없다"


def run_bench(
    config: HarnessConfig,
    *,
    roles: Sequence[str] | None = None,
    samples: int = DEFAULT_SAMPLES,
    concurrency: Sequence[int] = DEFAULT_CONCURRENCY,
    contexts: Sequence[int] = DEFAULT_CONTEXTS,
) -> dict[str, object]:
    """프로필의 실서빙 역할을 A~E로 재고 §3 체크리스트 요약까지 붙여 돌려준다."""
    wanted = list(roles) if roles else list(PROMPTED_ROLES)
    measured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    results: dict[str, object] = {}
    for role in wanted:
        if role not in config.profile.roles:
            results[role] = {"skipped": f"프로필 {config.profile_name!r}에 없는 역할이다"}
            continue
        results[role] = _measure_role(config, role, samples, concurrency, contexts)
    return {
        "profile": config.profile_name,
        "measured_at": measured_at,
        "tool_runner": config.profile.adapters.tool_runner,
        "measurement_order": list(MEASUREMENT_ORDER),
        "samples": samples,
        "concurrency": list(concurrency),
        "contexts": list(contexts),
        "roles": results,
        "checklist": checklist_summary(results),
    }


def _measure_role(
    config: HarnessConfig,
    role: str,
    samples: int,
    concurrency: Sequence[int],
    contexts: Sequence[int],
) -> dict[str, object]:
    """역할 하나. A가 실패하면 나머지는 건너뛰고 사유를 남긴다.

    죽은 엔드포인트에 C·E까지 태우면 타임아웃만 쌓이고 결과는 어차피 못 쓴다.
    """
    entry = config.model_for(role)
    model_key = config.model_key_for(role)
    head: dict[str, object] = {"model_key": model_key, "endpoint": entry.endpoint}
    if entry.is_fake:
        return {**head, "skipped": FAKE_SKIP}

    role_enum = Role(role)
    schema, level = ROLE_SPEC[role_enum]
    tokens = config.thresholds.budget_for(level)
    template = load_prompt(role_enum, entry.prompt_template_version)
    startup = check_startup(entry.endpoint, model_key)
    head |= {
        "capsule_level": level.value,
        "body_target_tokens": tokens,
        "prompt_tokens_approx": approx_tokens(sized_prompt(role_enum, template, tokens, 0)),
        "startup": startup,
    }
    if not startup["ok"]:
        skipped = f"기동 확인 실패로 건너뛴다: {startup['reason']}"
        return {**head, **dict.fromkeys(MEASUREMENT_ORDER[1:], None), "skipped": skipped}

    client = _client(config, model_key)
    args = (client, role_enum, template, schema)
    return {
        **head,
        # 순서가 결과를 바꾼다 — 모듈 docstring(src/bench.py) 참조.
        "structured_output": measure_structured_output(*args, tokens, samples),
        "prefix_cache": measure_prefix_cache(*args, tokens, samples, entry.endpoint),
        "throughput": measure_throughput(*args, tokens, concurrency, entry.endpoint),
        "memory": measure_memory(*args, contexts, entry.endpoint, entry.context_limit),
        "skipped": None,
    }


def _client(config: HarnessConfig, model_key: str) -> OpenAICompatModelClient:
    """`factory._model_client()`와 **같은 인자**로 만든다.

    다르면 bench가 잰 것이 운영 경로가 아니게 된다. 어긋남은
    `tests/test_bench.py::test_bench_client_matches_the_factory`가 막는다.
    """
    entry = config.models[model_key]
    return OpenAICompatModelClient(
        endpoint=entry.endpoint,
        model_name=model_key,
        temperature=config.sampling.temperature,
        top_p=config.sampling.top_p,
        seed=config.seed,
        retries=config.thresholds.schema_retries,
    )


# --- 체크리스트 요약 ---------------------------------------------------------


def checklist_summary(results: Mapping[str, object]) -> list[dict[str, object]]:
    """§3 체크리스트 7개의 통과/실패/미측정.

    측정된 역할이 하나도 없으면 전부 `unmeasured`다 — "실패"로 적으면 서버가 안 떠 있는
    것과 서버가 잘못된 것이 같은 표기가 된다.
    """
    measured = [value for value in results.values() if _is_measured(value)]
    judges = (
        _judge_startup,
        _judge_memory,
        _judge_context,
        _judge_throughput,
        _judge_prefix,
        _judge_structured,
    )
    summary = [
        {"item": item, "source": source, **judge(measured)}
        for (item, source), judge in zip(CHECKLIST[:6], judges, strict=True)
    ]
    item, source = CHECKLIST[6]
    summary.append(
        {
            "item": item,
            "source": source,
            "status": UNMEASURED,
            "reason": "미측정(데이터셋 대기) — 고정 반박 표본이 준비되면 별도 도구로 잰다",
        }
    )
    return summary


def _is_measured(value: object) -> bool:
    return isinstance(value, dict) and value.get("skipped") is None and "startup" in value


def _empty(measured: Sequence[Mapping[str, object]]) -> dict[str, object] | None:
    if measured:
        return None
    return {"status": UNMEASURED, "reason": "측정된 역할이 없다 (전부 fake이거나 건너뜀)"}


def _judge_startup(measured: Sequence[Mapping[str, object]]) -> dict[str, object]:
    empty = _empty(measured)
    if empty:
        return empty
    failed = [r["model_key"] for r in measured if not r["startup"]["ok"]]
    if failed:
        return {"status": FAIL, "reason": f"기동 확인 실패: {failed}"}
    return {"status": PASS, "reason": None}


def _judge_memory(measured: Sequence[Mapping[str, object]]) -> dict[str, object]:
    empty = _empty(measured)
    if empty:
        return empty
    if any(r["memory"]["mem_gb_ctx8k"] > 0 for r in measured):
        return {"status": PASS, "reason": None}
    reasons = {str(r["memory"]["mem_gb_ctx8k_reason"]) for r in measured}
    return {"status": UNMEASURED, "reason": "; ".join(sorted(reasons))}


def _judge_context(measured: Sequence[Mapping[str, object]]) -> dict[str, object]:
    empty = _empty(measured)
    if empty:
        return empty
    peaks = [
        entry.get("kv_cache_usage_perc_peak", 0.0)
        for r in measured
        for entry in r["memory"]["by_context"].values()
        if isinstance(entry, dict)
    ]
    if any(peak > 0 for peak in peaks):
        return {"status": PASS, "reason": None}
    return {"status": UNMEASURED, "reason": f"/metrics의 {METRIC_KV_USAGE}를 읽지 못했다"}


def _judge_throughput(measured: Sequence[Mapping[str, object]]) -> dict[str, object]:
    empty = _empty(measured)
    if empty:
        return empty
    single = [r["throughput"]["tok_s_single"] for r in measured]
    batch = [r["throughput"]["tok_s_batch32"] for r in measured]
    if all(value > 0 for value in single) and all(value > 0 for value in batch):
        return {"status": PASS, "reason": None}
    stated = [r["throughput"]["tok_s_batch32_reason"] for r in measured]
    reasons = [reason for reason in stated if reason]
    return {
        "status": UNMEASURED if reasons else FAIL,
        "reason": "; ".join(reasons) or "output 토큰이 0이다 — 호출이 전부 실패했을 수 있다",
    }


def _judge_prefix(measured: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """`metrics_only`는 통과로 본다 — **caching은 동작하고 있다.**

    다만 장부(`Usage.cached_tokens`)가 0이라 R6은 해소되지 않는다. 그 구분이 사유에
    남아야 "적중률을 못 쟀다"와 섞이지 않는다.
    """
    empty = _empty(measured)
    if empty:
        return empty
    verdicts = {r["model_key"]: r["prefix_cache"]["verdict"] for r in measured}
    disabled = [key for key, value in verdicts.items() if value == "caching_disabled"]
    if disabled:
        # 서버 기동 인자 문제다. 측정이 아니라 운영이 고칠 것이므로 사유에 그렇게 적는다.
        return {
            "status": FAIL,
            "reason": (
                f"{disabled}는 enable_prefix_caching=False로 떠 있다 — 서버를 다시 띄워야 한다"
            ),
        }
    if any(value == "neither" for value in verdicts.values()):
        return {"status": FAIL, "reason": f"prefix caching 미동작: {verdicts}"}
    unclear = {
        key: value
        for key, value in verdicts.items()
        if value in {"metrics_unavailable", "metrics_reset", "ledger_only", "no_traffic"}
    }
    if unclear:
        return {"status": UNMEASURED, "reason": f"판정 불가: {unclear}"}
    blind = [key for key, value in verdicts.items() if value == "metrics_only"]
    return {
        "status": PASS,
        "reason": (
            f"caching은 동작한다. 다만 {blind}는 장부에 0으로 잡힌다 — 서버가 "
            "prompt_tokens_details를 주지 않는다 (R6 미해소)"
            if blind
            else None
        ),
    }


def _judge_structured(measured: Sequence[Mapping[str, object]]) -> dict[str, object]:
    empty = _empty(measured)
    if empty:
        return empty
    ratios = {r["model_key"]: r["structured_output"]["valid_ratio"] for r in measured}
    if all(ratio == 1.0 for ratio in ratios.values()):
        return {"status": PASS, "reason": None}
    return {"status": FAIL, "reason": f"유효 JSON 비율이 1.0이 아니다: {ratios}"}


# --- registry 반영 -----------------------------------------------------------


def pending_updates(report: Mapping[str, object]) -> tuple[dict[str, dict[str, float]], list[str]]:
    """`--apply`가 쓸 값과, 쓰지 않기로 한 것들의 사유.

    **0인 값은 제외한다.** `Benchmark.is_measured`가 0 여부로 판정하므로, 못 잰 값을
    넣으면 미측정 모델이 측정된 것으로 보이고 production 프로필로 가는 길이 열린다.

    같은 model key를 여러 역할이 쓰면(b1_single은 셋 다 같은 모델) **먼저 나온 역할의
    값을 쓴다.** 역할마다 캡슐 레벨이 달라 body 길이가 다르므로 수치도 다르다 — 평균이나
    최대를 취하면 어느 조건의 수치인지 말할 수 없게 된다. 어느 역할에서 왔는지는 사유에
    남는다.
    """
    updates: dict[str, dict[str, float]] = {}
    skipped: list[str] = []
    for role, value in (report.get("roles") or {}).items():
        if not _is_measured(value):
            continue
        key = str(value["model_key"])
        if key in updates:
            skipped.append(
                f"{role}: {key}는 앞선 역할의 값을 이미 썼다 (역할마다 body 길이가 다르다)"
            )
            continue
        fields, reasons = _fields_for(role, value)
        skipped.extend(reasons)
        if fields:
            updates[key] = fields
    return updates, skipped


def _fields_for(role: str, value: Mapping[str, object]) -> tuple[dict[str, float], list[str]]:
    throughput, memory = value["throughput"], value["memory"]
    fields: dict[str, float] = {}
    skipped: list[str] = []
    for name, measured, reason in (
        ("tok_s_single", throughput["tok_s_single"], "동시 요청 1의 output 토큰이 0이다"),
        ("tok_s_batch32", throughput["tok_s_batch32"], throughput["tok_s_batch32_reason"]),
        ("mem_gb_ctx8k", memory["mem_gb_ctx8k"], memory["mem_gb_ctx8k_reason"]),
    ):
        if measured > 0:
            fields[name] = float(measured)
        else:
            skipped.append(f"{role}.{name}: {reason}")
    # gpu_memory_utilization 없이 적힌 점유는 다른 사람이 재현할 수 없다. registry에
    # 들어갈 자격이 없으므로 값이 있어도 뺀다.
    if "mem_gb_ctx8k" in fields and memory["gpu_memory_utilization"] is None:
        fields.pop("mem_gb_ctx8k")
        skipped.append(
            f"{role}.mem_gb_ctx8k: gpu_memory_utilization을 얻지 못해 재현 불가라 쓰지 않는다 "
            f"({memory['gpu_memory_utilization_reason']})"
        )
    return fields, skipped


def apply_benchmarks(
    models_path: Path, updates: Mapping[str, Mapping[str, float]], measured_at: str
) -> dict[str, object]:
    """`benchmark:` 블록의 해당 라인만 갈아 끼운다. 주석·순서·들여쓰기는 보존한다.

    전부 찾은 뒤에 한 번에 쓴다. 모델 하나를 고치다 다음에서 실패하면 registry가 절반만
    갱신된 채 남는데, 그 상태는 갱신 실패보다 훨씬 비싸다.
    """
    lines = models_path.read_text(encoding="utf-8").splitlines(keepends=True)
    edits: dict[int, str] = {}
    for key, fields in sorted(updates.items()):
        block = _benchmark_block(lines, key)
        rendered = {"measured_at": f'"{measured_at}"'}
        rendered |= {name: repr(float(value)) for name, value in fields.items()}
        for name, text in rendered.items():
            index = _value_line(lines, block, name, key)
            if index in edits:
                raise BenchApplyError(f"{key}의 {name} 라인이 겹친다 — 갱신하지 않는다")
            edits[index] = _replaced(lines[index], text)
    if not edits:
        return {"path": str(models_path), "updated": {}, "lines": 0}
    patched = list(lines)
    for index, text in edits.items():
        patched[index] = text
    _atomic_write(models_path, "".join(patched))
    return {
        "path": str(models_path),
        "updated": {key: dict(fields) for key, fields in updates.items()},
        "measured_at": measured_at,
        "lines": len(edits),
    }


def _benchmark_block(lines: Sequence[str], key: str) -> range:
    """`models:` 아래 `  <key>:` 블록 안의 `    benchmark:` 값 라인 범위.

    "확실히 하나"가 아니면 실패한다 — 어림짐작으로 고른 블록에 쓰는 것보다 안 쓰는 편이 낫다.
    """
    starts = [i for i, line in enumerate(lines) if line.rstrip() == f"  {key}:"]
    if len(starts) != 1:
        raise BenchApplyError(f"models.yaml에서 모델 {key!r} 블록을 하나로 특정하지 못했다")
    start = starts[0]
    end = _next_outdent(lines, start + 1, len(lines), "    ")
    marks = [i for i in range(start, end) if lines[i].rstrip() == "    benchmark:"]
    if len(marks) != 1:
        raise BenchApplyError(f"모델 {key!r}의 benchmark 블록을 하나로 특정하지 못했다")
    return range(marks[0] + 1, _next_outdent(lines, marks[0] + 1, end, "      "))


def _next_outdent(lines: Sequence[str], begin: int, end: int, indent: str) -> int:
    """`indent`보다 얕은 첫 비어 있지 않은 줄. 없으면 `end`."""
    return next(
        (i for i in range(begin, end) if lines[i].strip() and not lines[i].startswith(indent)),
        end,
    )


def _value_line(lines: Sequence[str], block: range, name: str, key: str) -> int:
    found = [i for i in block if lines[i].lstrip().startswith(f"{name}:")]
    if len(found) != 1:
        raise BenchApplyError(f"모델 {key!r}의 benchmark.{name} 라인을 하나로 특정하지 못했다")
    return found[0]


def _replaced(line: str, rendered: str) -> str:
    """`      tok_s_single: 0  # 주석` → 값만 바꾸고 들여쓰기·주석은 남긴다."""
    head, _, tail = line.partition(":")
    body = tail.rstrip("\n")
    comment = body[body.index("#") :] if "#" in body else ""
    newline = "\n" if line.endswith("\n") else ""
    return f"{head}: {rendered}{'  ' + comment if comment else ''}{newline}"


def _atomic_write(path: Path, text: str) -> None:
    """같은 디렉터리에 임시 파일로 쓰고 바꿔치기한다. 쓰다 죽어도 원본이 남는다."""
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
