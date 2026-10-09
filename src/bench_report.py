"""측정을 엮어 보고서를 만들고, `--apply`로 registry에 반영한다.

측정은 `src/bench.py`(시나리오 A~E)와 `src/bench_metrics.py`(`/metrics` 판독)에 있다.

측정 프리미티브와 나눈 이유는 수명이 다르기 때문이다. A~E는 무엇을 재는가이고, 여기는
**무엇을 §3 체크리스트의 답으로 인정하는가**다. 후자가 바뀌는 일이 훨씬 잦다.

`--apply`는 `config/models.yaml`의 `benchmark:` 블록만 **라인 단위로** 치환한다.
그 파일은 주석이 많고 **그 주석이 문서**다 — `yaml.safe_dump`로 다시 쓰면 전부 날아간다.
대상 라인을 확실히 찾지 못하면 아무것도 쓰지 않고 실패한다: 부분적으로 망가진 registry는
갱신 실패보다 훨씬 비싸다.
"""

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from src.adapters.openai_compat import OpenAICompatModelClient
from src.bench import (
    BATCH32,
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
from src.orchestrator.nodes import auditor as auditor_node
from src.orchestrator.nodes import scout as scout_node
from src.orchestrator.prompt_loader import PromptTemplate, load_prompt
from src.schemas.enums import Role

# `--apply`가 건드리는 키. `config.Benchmark`의 필드와 **같아야 한다** —
# `tests/test_bench_apply.py::test_apply_keys_cover_every_benchmark_field`가 강제한다.
# 그 검사가 생기기 전에는 이 상수를 아무도 읽지 않아 주석이 지켜지는지 알 수 없었다.
APPLY_KEYS = (
    "measured_at",
    "measured_on",
    "tok_s_single",
    "tok_s_batch32",
    "mem_gb_ctx8k",
    "prefix_hit_ratio_batch32",
)

PASS, FAIL, UNMEASURED = "pass", "fail", "unmeasured"

# model-serving.md §3 체크리스트 7개. 문구는 그 문서와 같게 두고, `source`로 이 도구의
# 어느 출력이 그 항목을 답하는지 못박는다 — 문서에서만 연결되면 둘이 갈라진다.
CHECKLIST: tuple[tuple[str, str], ...] = (
    ("vLLM 버전 × 드라이버/CUDA × GPU 아키텍처 커널 호환성 (기동 성공 여부)", "roles.*.startup.ok"),
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
    # 2026-09-09에 전제가 사라졌다. 이 줄은 원래 "NVFP4 양자화 시 Critic 반박 정확도
    # 저하 여부"였는데, 그 물음은 Critic이 고정밀 체크포인트를 가진 모델일 때만 선다.
    # 확정 로스터의 Critic `gpt-oss-20b`는 **네이티브 MXFP4**라 비교할 원본이 없다
    # (자리의 정본은 PROJECT-CONTEXT.md §1). #93이 그래서 「고정밀 대비 하락폭」이 아니라
    # **「반박 정확도 표본 자체」**로 재정의했다. STATUS.md가 이 코드를 "아직 안 고쳤다"고
    # 가리키고 있었고, 이 줄이 그 가리킴을 닫는다.
    ("Critic 반박 정확도 표본 (#93 재정의 — 고정밀 대비가 아니다)", "미구현 — 평가셋 준비 중"),
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
    wanted = list(roles) if roles else [r for r in PROMPTED_ROLES if r in config.profile.roles]
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


def schema_for(
    role_enum: Role, template: PromptTemplate, declared: type[BaseModel]
) -> type[BaseModel]:
    """그 역할·그 프롬프트 판으로 **노드가 실제로 요청하는** 구조화 출력 스키마.

    scout·auditor 자리는 **버전별로 스키마가 갈린다**(scout v1·v2=Candidate, v3=ScoutResponse 래퍼 ·
    auditor 한 방 판=Finding, 도구 루프 판=AuditorAction). ⏵ 2026-10-06 — 루프 봉투가 둘이라
    v8·v9=`AuditorAction`, v10=`AuditorProcedureAction`이다(위 `AuditorAction`은 v8·v9의 이름).
    ⏵ 2026-10-09 — v11=`AuditorProcedureNullableCweAction`(이슈 #525)이 셋째다.
    노드가 프롬프트 meta가 선언한 것을
    요청하므로(`nodes/scout.py`·`nodes/auditor.py`) bench도 같은 것을 재야 **운영 grammar**를 재는
    것이 된다 — 그 자리에서 `ROLE_SPEC`의 스키마는 캡슐 레벨 전용이 된다.

    **함수로 뺀 이유**: `_measure_role` 안의 `if/elif`로 두면 그 분기를 `elif False:`로 막아도
    검사가 초록이었다(문자열 핀만 있었다 — CLAUDE.md 2.5의 「검사가 무는 것을 세게 적지 않는다」).
    엔드포인트 없이 부를 수 있는 자리로 빼면 성질을 직접 묻는다.
    """
    if role_enum in scout_node.SCOUT_ROLES:
        return scout_node.resolve_output_schema(template.output_schema)
    if role_enum is Role.AUDITOR:
        return auditor_node.resolve_output_schema(template.output_schema)
    return declared


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
    declared, level = ROLE_SPEC[role_enum]
    tokens = config.thresholds.budget_for(level)
    template = load_prompt(role_enum, entry.prompt_template_version)
    schema = schema_for(role_enum, template, declared)
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

    client = _client(config, model_key, role=role)
    args = (client, role_enum, template, schema)
    return {
        **head,
        # 순서가 결과를 바꾼다 — 모듈 docstring(src/bench.py) 참조.
        "structured_output": measure_structured_output(*args, tokens, samples),
        "prefix_cache": measure_prefix_cache(*args, tokens, samples, entry.endpoint),
        "throughput": measure_throughput(*args, tokens, concurrency, entry.endpoint),
        # `server_limit`·`output_max`는 **재보지 않고 건너뛸 context**를 가른다 (#126).
        # `output_max`는 `_client`가 `max_tokens`로 싣는 바로 그 값이어야 한다 — 서버가
        # 400을 내는 조건이 `prompt + max_tokens > max_model_len`이라 둘이 갈라지면
        # 이 판정이 실제 요청과 다른 것을 계산하게 된다.
        "memory": measure_memory(
            *args,
            contexts,
            entry.endpoint,
            entry.context_limit,
            server_limit=startup["max_model_len"],
            output_max=config.thresholds.output_max_for(role),
        ),
        "skipped": None,
    }


def _client(config: HarnessConfig, model_key: str, *, role: str) -> OpenAICompatModelClient:
    """`factory._model_client()`와 **같은 인자**로 만든다.

    다르면 bench가 잰 것이 운영 경로가 아니게 된다. 어긋남은
    `tests/test_bench.py::test_bench_client_matches_the_factory`가 막는다.

    **`max_tokens`도 그 규칙에 포함된다.** "처리량 측정이니 출력 길이를 따로 두는 게
    맞지 않나"를 검토했고, 답은 아니다. 이유 셋이다:

    ⚠ **아래 셋이 말하는 "상한"은 `thresholds.output_max`가 아니다.** 이 함수가 싣는 것은
    `output_max_for(role)`이고(#79의 (c)), 어느 역할이 값을 선언하면 그 역할의 상한은
    전역값과 갈린다. 2의 "닿지 않는다"도 **그 역할의 값으로** 다시 따져야 한다 — 전역값이
    안전하다는 것이 역할값이 안전하다는 뜻이 아니다.

    1. 이 모듈 docstring의 규칙이 그것이다 — bench 전용 파라미터로 잰 수치는 운영 성능이
       아니다. `temperature`·`seed`를 이미 운영값으로 맞춰 두고 출력 상한만 달리 두면
       같은 종류의 어긋남을 하나 남긴다.
    2. **측정값이 왜곡되지 않는다.** `tok_s`는 `produced / elapsed`라 상한은 표본 길이를
       바꿀 뿐 속도를 바꾸지 않는다. 그리고 8/26 실측이 후보당 output 167토큰이라
       **선언이 없는 역할의 상한**(2026-08-28부터 4,096)은 정상 경로에서 닿지 않는다 — 실제로
       걸릴 일이 없다. **Critic 자리에서 같은 방향의 값이 하나 더 있다**:
       `docs/experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md`가
       `DecisionDraft`에서 `finish_reason=length` 0/18 · 최장 2,863토큰을 잰다. ⚠ 다른
       모델·다른 스키마라 위 근거를 **교체하지 않고 더한다**.
       **숫자를 여기 박지 않는 이유**: 상한이 8,192에서 4,096으로 움직였을 때
       이 문단이 낡은 채로 남았다.
    3. 걸리는 경우가 곧 재야 할 이상이다. 2026-08-27 ATOM에서 30B가 폭주해 클라이언트
       타임아웃이 난 상황을 bench가 상한 없이 만나면 체크리스트가 "측정 못 함"으로 끝난다.
       운영과 같은 상한을 두면 그 자리에서 잘리고 수치가 남는다.
       ⚠ 2026-09-24 — 생성이 느린 모델(ATOM 스모크에서 13.3 · 8.1 tok/s)에서는 상한보다 어댑터의
       요청 타임아웃(기본 300초 — 모델이 `request_timeout_s`를 선언하면 그 값)이 먼저 온다.
       그러면 잘린 수치 대신 게이트웨이 오류가 남는다 — 이슈 #261.
       ⏵ 2026-09-29 — 그 두 모델이 1,200 · 1,800 s를 선언해서, 4,096 · 순차 요청이면 이제 상한이
       먼저 온다(계산값). 동시 요청이면 스트림당 속도가 떨어지므로 이 말이 서지 않는다.
    """
    entry = config.models[model_key]
    return OpenAICompatModelClient(
        endpoint=entry.endpoint,
        model_name=model_key,
        temperature=config.sampling.temperature,
        top_p=config.sampling.top_p,
        seed=config.seed,
        # 역할별 값이다(#79의 (c)). `factory._model_client`와 **같은 해석기**를 부른다 —
        # 한쪽만 전역값을 읽으면 이 함수 docstring이 편 논거가 그 자리에서 깨진다.
        max_tokens=config.thresholds.output_max_for(role),
        # 모델별 값이라 registry에서 온다. 여기서 빠뜨리면 bench가 운영과 **다른 요청**을
        # 보내면서 같은 것을 쟀다고 적는다 — 이 함수 docstring이 `max_tokens`에 대해 편
        # 논거가 그대로 걸린다.
        reasoning_effort=entry.reasoning_effort,
        extra_params=entry.request_params(),
        timeout_s=entry.request_timeout_s,
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
    """context별 KV cache 점유 판정. **답하지 못한 context를 사유가 든다** (#111).

    `any(peak > 0)`이라 하나만 살아 있으면 `pass`인데, 예전에는 그 자리에 `reason: None`이
    붙어 **요약만 보면 전 context가 측정된 것과 구별되지 않았다.** 레코드는 정직하다 —
    `call_ok`·`error`가 같은 dict에 있다. 세던 것은 요약이고, 원문이 "3개 중 2개만
    답했다"인데 요약이 무조건부 `pass`를 내는 것이 CLAUDE.md §2.2다.

    **상태 어휘는 바꾸지 않는다.** `pass`를 `fail`이나 새 상태로 뒤집는 것은 설계 판단이고
    과거 run의 판정이 함께 뒤집힌다. 여기서 고치는 것은 **사실이 사라지지 않는 것**뿐이고,
    같은 파일 `_judge_prefix`가 이미 그 모양이다(PASS + 못 답한 부분을 사유에).

    관측: 2026-09-01 ATOM `qwen3-4b`에서 32K가 서버 상한을 넘어 400을 받았는데 이 항목이
    `pass`였다. 같은 모양이 아래 보고서 195·197행에도 있다 —
    `docs/experiments/harness/2026-08-25-serving-nvfp4-atom.md`.
    """
    empty = _empty(measured)
    if empty:
        return empty
    entries = [
        (r["model_key"], ctx, entry)
        for r in measured
        for ctx, entry in r["memory"]["by_context"].items()
        if isinstance(entry, dict)
    ]
    # `call_ok`가 없는 항목은 **실패로 세지 않는다** — `{"skipped": …}`처럼 애초에 재지 않기로
    # 선언한 자리이고(`bench.py`의 `ctx > context_limit` 분기) 그 사유는 레코드에 따로 있다.
    # 여기서 세는 것은 **재려다 실패한** 것뿐이다.
    dead = [f"{key} ctx {ctx}" for key, ctx, entry in entries if not entry.get("call_ok", True)]
    if any(entry.get("kv_cache_usage_perc_peak", 0.0) > 0 for _, _, entry in entries):
        return {"status": PASS, "reason": f"호출이 실패한 context: {dead}" if dead else None}
    if dead:
        # 전부 못 닿았으면 원인은 `/metrics`가 아니다. 아래 고정 사유를 그대로 내면
        # **읽은 적 있는 메트릭을 못 읽었다고 말하게 된다.**
        return {"status": UNMEASURED, "reason": f"호출이 실패한 context: {dead}"}
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

    같은 model key를 여러 역할이 쓰면(`harness_pool`의 scout_memory·scout_injection이 같은
    `qwen3-14b`, 2026-09-25부터) **먼저 나온 역할의
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
    # **`tok_s_batch32`가 어떤 캐시 상태에서 나온 값인지를 옆에 싣는다** (#128).
    # 값을 고치지 않는다 — 읽는 사람이 판단할 재료만 는다.
    ratio, ratio_reason = _batch32_hit_ratio(throughput)
    if ratio is None:
        skipped.append(f"{role}.prefix_hit_ratio_batch32: {ratio_reason}")
    elif "tok_s_batch32" not in fields:
        # 주석 달 대상이 없으면 적중률만 남겨 봐야 무엇의 적중률인지 말할 수 없다.
        skipped.append(
            f"{role}.prefix_hit_ratio_batch32: tok_s_batch32를 쓰지 않으므로 함께 두지 않는다"
        )
    else:
        # **0을 버리지 않는다.** `MEASUREMENTS` 세 항목의 "0 = 미측정" 규칙은 여기에
        # 성립하지 않는다 — 적중률 0은 캐시를 끄고 잰 판의 실측이고, 그게 이 필드가
        # 가장 필요한 경우다. 못 읽은 것은 위 `ratio is None` 갈래로 간다.
        fields["prefix_hit_ratio_batch32"] = ratio
    return fields, skipped


def _batch32_hit_ratio(throughput: Mapping[str, object]) -> tuple[float | None, str]:
    """동시성 `BATCH32` 구간의 prefix cache 적중률. 못 읽으면 `(None, 사유)`.

    **지어내지 않는다.** `counter_delta`가 `hit_ratio: None`을 내는 경우가 둘 있고
    (카운터 판독 실패, 그 구간에 엔진이 요청을 한 건도 못 봄) 둘 다 0으로 접으면
    "캐시가 하나도 안 맞았다"는 적극적인 거짓이 된다.
    """
    levels = throughput.get("by_concurrency")
    level = levels.get(str(BATCH32)) if isinstance(levels, Mapping) else None
    if not isinstance(level, Mapping):
        return None, f"동시성 {BATCH32} 구간이 없어 그 값의 캐시 상태를 모른다"
    delta = level.get("prefix_cache_delta")
    if not isinstance(delta, Mapping):
        return None, "prefix_cache_delta가 없다"
    ratio = delta.get("hit_ratio")
    if ratio is None:
        return None, (
            f"적중률을 읽지 못했다 (status={delta.get('status')}): {delta.get('reason')}"
        )
    return float(ratio), ""


def apply_benchmarks(
    models_path: Path,
    updates: Mapping[str, Mapping[str, float]],
    measured_at: str,
    *,
    measured_on: str | None = None,
) -> dict[str, object]:
    """`benchmark:` 블록의 해당 라인만 갈아 끼운다. 주석·순서·들여쓰기는 보존한다.

    전부 찾은 뒤에 한 번에 쓴다. 모델 하나를 고치다 다음에서 실패하면 registry가 절반만
    갱신된 채 남는데, 그 상태는 갱신 실패보다 훨씬 비싸다.

    `measured_on`은 **어느 장비에서 잰 값인가**다. `None`이면 `null`로 적는다 — 장비를
    모르는 채로 쓴 수치라는 사실 자체가 기록이어야 하고, 빈 문자열이나 호스트명 추측으로
    메우면 그 자리에 거짓이 들어간다(`config.Benchmark.measured_on` 주석).
    """
    lines = models_path.read_text(encoding="utf-8").splitlines(keepends=True)
    edits: dict[int, str] = {}
    for key, fields in sorted(updates.items()):
        block = _benchmark_block(lines, key)
        rendered = {
            "measured_at": f'"{measured_at}"',
            # `json.dumps`로 감싼다. **`f'"{measured_on}"'`은 안 된다** — 이 값은
            # `--device`가 준 사용자 입력이고 ADR 0006이 라벨 집합을 일부러 열어 두었다.
            # 따옴표가 든 라벨은 registry를 파싱 불가로 만들어 이후 모든 커맨드를 죽이고,
            # `C:\atom` 같은 값은 로드는 되지만 YAML 이스케이프로 **조용히 다른 라벨**이
            # 된다(`\a` = BEL). 조용히 다른 값이 되는 쪽이 더 나쁘다 — 이 필드의 존재
            # 이유가 정확히 "어느 장비였는지 나중에 알 수 있다"이기 때문이다.
            # JSON 문자열은 YAML 1.2의 double-quoted 스칼라와 같은 문법이다.
            "measured_on": json.dumps(measured_on, ensure_ascii=False),
        }
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
        "measured_on": measured_on,
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
