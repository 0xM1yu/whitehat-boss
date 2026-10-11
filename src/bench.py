"""배포 전 검증 체크리스트(model-serving.md §3)를 실제 엔드포인트에 대고 잰다.

**진단 명령 중 모델 서버에 요청을 보내는 것은 `bench`뿐이다.** `doctor`는 설정과 저장소 상태만
보고 소켓을 열지 않는다(`src/cli.py` 모듈 docstring). 그 경계는 GPU 없는 팀원의 진단이 네트워크
오류로 실패하지 않게 하려는 것이고, 여기서 한 번 더 못박는다 — bench는 서버가 있는 장비에서만
의미가 있으며, 없으면 "측정 못 함"을 사유와 함께 출력하고 끝낸다. (`preflight`(#534)는
ssh·TCP 연결·`docker run`까지만 한다. `run`도 그 서버에 닿지만 파이프라인이지 진단이 아니다.)

측정에는 `OpenAICompatModelClient`를 **그대로** 쓴다. bench 전용 HTTP 경로로 잰 수치는
운영 성능이 아니다. 특히 prefix를 system 메시지로 분리하는 `_request_payload`의 동작이
prefix cache 측정의 전제다(agent-contracts.md §4.2). 프롬프트도 실제 렌더 경로
(`load_prompt().render()` + `build_facts()`)를 쓴다.

`/metrics`를 읽는 계층(이름·파싱·카운터 구간 delta)은 `src/bench_metrics.py`에 있고,
체크리스트 판정과 registry 반영은 `src/bench_report.py`에 있다.

**못 잰 값은 0으로 남기고 사유를 함께 낸다.** 추정치·보간값을 넣지 않는다 —
`Benchmark.is_measured`가 0 여부로 판정하므로 지어낸 값은 미측정 모델을 production
프로필에 올리는 길을 연다(model-serving.md §5).

## 측정 순서가 결과를 바꾼다

B(구조화 출력)를 가장 먼저 돌린다 — 가장 싸게 계약 전체의 성립 여부를 판정하기 때문이다.
그런데 B는 같은 prefix로 여러 번 요청하므로 **그 시점에 prefix cache가 데워진다.** 그래서
D(prefix 적중률)는 절대값을 쓰지 않고 `/metrics` 카운터의 **구간 delta**로 잰다. 절대값은
서버가 언제 떴는지와 앞에서 무엇이 돌았는지에 오염된다.

delta를 써도 "bench가 데운 캐시를 bench가 잰다"는 사실은 남으므로, 그 구간의
`prewarmed_by`와 전체 `measurement_order`를 출력에 남긴다. 순서를 바꿨을 때 수치가 달라진
이유를 나중에 설명할 수 있어야 한다.
"""

import json
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pydantic import BaseModel

from src.adapters.openai_compat import OpenAICompatModelClient
from src.bench_metrics import (
    METRIC_CACHE_CONFIG,
    METRIC_KV_USAGE,
    counter_delta,
    fetch,
    gpu_memory_utilization,
    kv_cache_perc,
    nvidia_smi_mb,
    read_counters,
)
from src.interfaces.model_client import ModelGatewayError, Prompt, SchemaParseError
from src.orchestrator.prompt_loader import PromptTemplate, build_facts
from src.schemas.candidate import Candidate
from src.schemas.common import Location
from src.schemas.decision import DecisionDraft
from src.schemas.enums import CapsuleLevel, Role
from src.schemas.finding import Finding
from src.schemas.pov import PovAction

# 4바이트 ≈ 1토큰. 실제 토크나이저가 아니라 길이 맞춤용 근사다(cli·어댑터와 같은 상수).
CHARS_PER_TOKEN = 4

DEFAULT_SAMPLES = 5
DEFAULT_CONCURRENCY = (8, 16, 32)
DEFAULT_CONTEXTS = (8192, 16384, 32768)

# `Benchmark.tok_s_batch32`에 대응하는 동시 요청 수. 이 값이 `--concurrency`에 없으면
# 그 필드는 미측정(0)으로 남는다 — 다른 동시성 수치를 대신 넣지 않는다.
BATCH32 = 32
# `Benchmark.mem_gb_ctx8k`에 대응하는 context. 나머지 context는 출력 전용이다
# (registry에 대응 필드가 없다).
CTX8K = 8192

_TIMEOUT_S = 60
_BENCH_RUN_ID = "run_20260101_0001"
_TARGET = Location(file="bench.c")

# 채움용 코드 조각. 길이는 config(`thresholds.capsule_budget_tokens`)에서 오고
# 여기서는 "무엇으로 채우는가"만 정한다.
_FILLER = "    memcpy(dst + off, src, len);  /* bench filler */\n"
_BASE_SOURCE = "int parse_header(const char *src, size_t len) {\n    char dst[64];\n"

# 역할 → (출력 스키마, 캡슐 레벨). 레벨은 파이프라인에서 그 역할이 실제로 받는 것이다:
# scout은 LIGHT, auditor는 EXPANDED 캡슐을 받는다(runner._step_retrieval·auditor 단계).
# critic은 캡슐 대신 Finding+Evidence를 받으므로 EXPANDED에 준하는 길이로 근사하고,
# 그 사실을 출력의 `capsule_level`에 남긴다.
ROLE_SPEC: dict[Role, tuple[type[BaseModel], CapsuleLevel]] = {
    Role.SCOUT: (Candidate, CapsuleLevel.LIGHT),
    # 풀 세 멤버는 4단계의 같은 자리라 계약이 scout과 같다 — 받는 캡슐도 LIGHT이고 내는
    # 스키마도 Candidate다(ADR 0010 §3). **다른 것은 프롬프트가 겨냥하는 CWE 계열뿐이고,
    # bench가 재는 것은 계열이 아니라 처리량·메모리라 여기서는 셋이 구분되지 않는다.**
    # 그러니 세 줄이 같은 값인 것은 복붙이 아니라 측정 대상이 같다는 뜻이다.
    #
    # 아래 `Role.SINGLE` 주석의 `KeyError`가 여기도 그대로 걸린다.
    Role.SCOUT_MEMORY: (Candidate, CapsuleLevel.LIGHT),
    Role.SCOUT_BOUNDARY: (Candidate, CapsuleLevel.LIGHT),
    Role.SCOUT_INJECTION: (Candidate, CapsuleLevel.LIGHT),
    Role.AUDITOR: (Finding, CapsuleLevel.EXPANDED),
    # **`Decision`이 아니라 `DecisionDraft`다.** bench는 같은 프롬프트 파일을 렌더해
    # 구조화 출력을 재므로, 실서빙 노드(`nodes/critic.py`의 `OUTPUT_SCHEMA`)가 보내는
    # 것과 달라지면 재는 grammar가 운영의 grammar가 아니게 된다 — ID·`produced_by`가
    # 빠진 만큼 필수 필드 수가 다르다.
    Role.CRITIC: (DecisionDraft, CapsuleLevel.EXPANDED),
    # 단일패스는 셋을 한 호출로 한다. 스키마와 레벨을 **지어내지 않고 프롬프트가 선언한
    # 값을 쓴다** — `prompts/single/v2/meta.yaml`의 `output_schema: Finding`이고, 혼자
    # 경로까지 세우므로 문맥은 Auditor 이상이어야 한다.
    #
    # `Role.SINGLE`만 추가하고 여기를 비워 두면 `bench_report._role_report()`의
    # `ROLE_SPEC[role_enum]`이 `KeyError`가 된다 — 고치려던 크래시가 자리만 옮긴다.
    Role.SINGLE: (Finding, CapsuleLevel.EXPANDED),
    # PoV — 모델은 매 홉 `PovAction`(gen/inspect/submit · v5는 형제 `PovActionNamed`)을 낸다(PoV 툴
    # 루프, `prompts/pov/`).
    # `gen`이 실은 `PovSpec`은 순수 조립기가 바이트로 만든다. `_render`에 pov 분기가 있다
    # (FINDING·TARGET·SOURCE·FACTS). 실측은 서빙 호스트 확보 뒤(checksum·benchmark 0).
    # ⏵ 2026-10-08 — 호스트는 생겼다(`--device vastai-remote`, #417). checksum·benchmark는
    # 여전히 0이다(`config/models.yaml`의 `feyospace-v1-small`).
    Role.POV: (PovAction, CapsuleLevel.EXPANDED),
}

MEASUREMENT_ORDER = ("startup", "structured_output", "prefix_cache", "throughput", "memory")

# 메모리 폴링 간격(초). **실측으로 정한 값이 아니라 절충용 잠정치다** — `Budget.tool_seconds`
# (#65)와 같은 성격이고, 실측이 생기면 조정 대상이다.
#
# **없앤 문제가 "호출 횟수"는 아니다.** 그전에는 sleep이 아예 없었는데, 그렇다고 루프가
# 무한정 도는 것은 아니었다: `nvidia-smi`의 fork+exec 자체가 루프를 묶고 있었다.
# 진짜 문제는 **폴링 속도가 `nvidia-smi`가 얼마나 느린가에 우연히 결정된다**는 것이고,
# 그 전제가 깨지는 자리가 정확히 ATOM이다 — 통합메모리라 `memory.total`이 `[N/A]`로
# **빨리** 돌아온다(`bench_metrics.nvidia_smi_mb()`가 그 경우를 다룬다). 관측 빈도가
# 장비 특성에 좌우되면 장비 간 비교에서 그것이 그대로 교란이 된다.
#
# 절충: 길면 peak를 놓치고, 짧으면 관측이 재려는 대상을 흔든다. 어느 쪽으로 틀렸는지는
# `*_samples`가 함께 나가므로 사후에 알 수 있다.
_POLL_INTERVAL_S = 0.1


class BenchApplyError(RuntimeError):
    """models.yaml의 갱신 대상 블록을 확실히 찾지 못했다. 아무것도 쓰지 않는다."""


# --- 프롬프트 ----------------------------------------------------------------


def _filler(chars: int, variant: int) -> str:
    """`variant`가 매 호출 달라진다 — **의도다.**

    본문까지 완전히 같으면 서버가 body 접두사도 캐시해 prefill이 거의 사라지고, 그러면
    C(처리량)가 운영보다 낙관적으로 나온다. 운영에서는 후보마다 body가 다르므로 공유되는
    것은 prefix뿐이어야 한다.
    """
    head = f"/* bench sample {variant} */\n{_BASE_SOURCE}"
    want = max(0, chars - len(head))
    return head + _FILLER * (want // len(_FILLER) + 1)


# PoV bench 프롬프트에 실을 Finding 요약 근사 — `nodes/pov.py._describe_finding` 모양.
_POV_FINDING = (
    "find_0001 · CWE: CWE-787 · 검증 없는 memcpy\n"
    "근거(rca): parse_header에서 len 미검증\n경로 단계 수: 2"
)


def _render(role: Role, template: PromptTemplate, filler: str) -> Prompt:
    """실제 렌더 경로. 자리표시자 누락은 `render()`가 잡는다."""
    if role is Role.CRITIC:
        # 합성 벤치도 프롬프트의 원문 블록·FACTS를 함께 채운다. 앞 판의 바이트는 유지한다.
        source_fields = {"source": filler} if "{{SOURCE}}" in template.body else {}
        facts = build_facts(
            run_id=_BENCH_RUN_ID,
            ids={"finding_id": "find_0001", "decision_id": "dec_0001"},
            **source_fields,
        )
        return template.render(
            facts=facts,
            claim="검증 없는 memcpy (CWE-787)",
            path="parse_header -> memcpy",
            evidence=filler,
            **source_fields,
        )
    if role is Role.POV:
        # PoV body는 FINDING·TARGET·SOURCE·FACTS를 받는다(캡슐 섹션이 아니라 Auditor의 Finding).
        # `nodes/pov.py._render_prompt`의 필드 모양으로 근사한다 — bench엔 실캡슐이 없어 FACTS의
        # capsule_sections가 빠진다(그만큼 낙관 · 서빙 실측이 선행).
        pov_facts = build_facts(
            run_id=_BENCH_RUN_ID,
            ids={"finding_id": "find_0001"},
            target=_TARGET,
            source=filler,
        )
        return template.render(
            facts=pov_facts,
            finding=_POV_FINDING,
            target=_TARGET.file,
            source=filler,
        )
    facts = build_facts(
        run_id=_BENCH_RUN_ID,
        ids={"candidate_id": "cand_0001", "finding_id": "find_0001"},
        artifact_id="art_0001",
        target=_TARGET,
        source=filler,
        # 선언을 따른다 — `facts_source: false` 판(Auditor v13 · 이슈 #67)은 FACTS에 `source`가
        # 없다. ⚠ 노드와 같은 것은 Auditor뿐: 다른 역할이 선언하면 bench만 빼고 노드는 안 뺀다.
        include_source=template.facts_source,
    )
    fields = {
        "target": _TARGET.file,
        "capsule_sections": "code, callers",
        "source": filler,
    }
    if role is Role.AUDITOR:
        fields["candidate"] = "cand_0001 검증 없는 memcpy (CWE-787) score=0.8"
    return template.render(facts=facts, **fields)


def sized_prompt(role: Role, template: PromptTemplate, tokens: int, variant: int) -> Prompt:
    """렌더 결과가 목표 토큰 수에 준하도록 채움 길이를 맞춘다.

    `source`가 FACTS 블록과 본문에 두 번 실리므로(실제 노드도 그렇다) 채움 길이와 렌더 길이가 선형이
    아니다. ⏵ 2026-10-10 — `facts_source: false` 판(Auditor v13 · 이슈 #67)은 FACTS에 `source`가
    없어 한 번만 실린다(노드도 그렇다). 다른 판은 그대로 두 번이다. 그래서 재보고 조정한다 — 길이를
    상수로 박지 않는다. 목표값은 `thresholds.capsule_budget_tokens`(config)에서 온다.
    """
    want = max(1, tokens) * CHARS_PER_TOKEN
    chars = want
    prompt = _render(role, template, _filler(chars, variant))
    for _ in range(3):
        actual = len(prompt.prefix) + len(prompt.body)
        if actual <= want * 1.1 or chars <= 0:
            break
        chars = max(0, int(chars * want / actual))
        prompt = _render(role, template, _filler(chars, variant))
    return prompt


def approx_tokens(prompt: Prompt) -> int:
    return (len(prompt.prefix) + len(prompt.body)) // CHARS_PER_TOKEN


# --- 호출 -------------------------------------------------------------------


@dataclass(frozen=True)
class Call:
    """호출 하나의 결과. 실패도 값으로 다룬다 — 실패 횟수가 곧 B의 지표다."""

    ok: bool
    output_tokens: int = 0
    cached_tokens: int = 0
    input_tokens: int = 0
    error: str = ""


def call_once(
    client: OpenAICompatModelClient, role: Role, prompt: Prompt, schema: type[BaseModel]
) -> Call:
    try:
        result = client.complete(role=role, prompt=prompt, schema=schema)
    except SchemaParseError as exc:
        # 삼키지 않는다 — 스키마 파싱 실패는 이 프로젝트가 재려는 지표다.
        return Call(False, error=f"SchemaParseError: {exc}")
    except ModelGatewayError as exc:
        return Call(False, error=f"ModelGatewayError: {exc}")
    usage = result.usage
    return Call(
        True,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        input_tokens=usage.input_tokens,
    )


# --- 측정 A~E ---------------------------------------------------------------


def check_startup(endpoint: str, model_key: str) -> dict[str, object]:
    """A. `GET /v1/models`의 id가 registry key와 일치하는가.

    `factory._model_client()`가 `model_name=model_key`를 그대로 실어 보내므로, 여기가
    어긋나면 실제 run의 `/chat/completions`도 같은 이유로 실패한다. 서버를
    `--served-model-name <registry key>`로 띄웠는지를 보는 검사다.
    """
    body, error = fetch(f"{endpoint.rstrip('/')}/models")
    if body is None:
        return {"ok": False, "reason": error, "served_ids": [], "max_model_len": None}
    try:
        entries = list(json.loads(body).get("data", []))
        served = [str(item.get("id", "")) for item in entries]
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "reason": f"/v1/models 응답을 읽지 못했다: {exc}",
            "served_ids": [],
            "max_model_len": None,
        }
    if model_key not in served:
        return {
            "ok": False,
            "reason": (
                f"registry key {model_key!r}가 서버의 모델 목록 {served}에 없다 — "
                "vLLM을 --served-model-name으로 맞추지 않으면 실제 run도 같은 이유로 실패한다"
            ),
            "served_ids": served,
            "max_model_len": None,
        }
    return {
        "ok": True,
        "reason": None,
        "served_ids": served,
        # **서버가 실제로 받는 상한.** registry의 `context_limit`과 **다른 축**이다 —
        # 그쪽은 모델이 원리상 지원하는 길이이고 이쪽은 이 프로세스가 `--max-model-len`
        # 으로 띄워진 값이다. 둘을 같은 것으로 읽으면 스윕 꼭대기가 항상 400을 받는다
        # (#126, 2026-09-02 ATOM에서 두 번째 발생). **이미 부르는 응답에서 필드 하나를
        # 더 읽을 뿐이라 왕복이 늘지 않는다.**
        #
        # **없으면 `None`이다.** 이 키를 안 주는 서버가 있고, 그때는 값을 지어내는 대신
        # 종전대로 재보고 실패를 기록하는 쪽이 맞다.
        "max_model_len": _served_max_model_len(entries, model_key),
    }


def _served_max_model_len(entries: Sequence[object], model_key: str) -> int | None:
    """`/v1/models`의 해당 항목이 선언한 `max_model_len`. 없거나 이상하면 `None`.

    **`model_key`의 항목만 본다.** 한 서버가 여러 모델을 서빙하면 항목마다 값이 다를 수
    있고, 아무 항목이나 집으면 다른 모델의 상한으로 이 모델의 스윕을 자르게 된다.
    """
    for item in entries:
        if not isinstance(item, dict) or str(item.get("id", "")) != model_key:
            continue
        value = item.get("max_model_len")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            # 문자열·`None`·0 이하를 상한으로 쓰면 스윕이 통째로 건너뛰어진다. 못 읽은
            # 것과 "상한이 0"은 다르고, 전자면 종전 동작이 맞다.
            return None
        return value
    return None


def measure_structured_output(
    client: OpenAICompatModelClient,
    role: Role,
    template: PromptTemplate,
    schema: type[BaseModel],
    tokens: int,
    samples: int,
) -> dict[str, object]:
    """B. 실제 출력 스키마로 `samples`회 요청해 유효 JSON 비율을 낸다.

    가장 먼저·가장 싸게 판정한다. 이 항목이 무너지면 agent-contracts.md의 계약 전체가
    재설계 대상이라 나머지 측정에 비용을 쓸 이유가 없다.
    """
    calls = [
        call_once(client, role, sized_prompt(role, template, tokens, i), schema)
        for i in range(samples)
    ]
    failures = [call.error for call in calls if not call.ok]
    valid = samples - len(failures)
    return {
        "samples": samples,
        "valid": valid,
        "valid_ratio": round(valid / samples, 4) if samples else 0.0,
        "failures": failures,
    }


def verdict_for(cached: int, delta: Mapping[str, object]) -> tuple[str, str]:
    """D의 판정. 규칙을 한 곳에 모은다.

    `both_hit` / `metrics_only` / `ledger_only` / `neither` / `caching_disabled`,
    그리고 캐시에 대해 아무 주장도 하지 않는 `no_traffic` / `metrics_unavailable` /
    `metrics_reset`.
    """
    status = delta.get("status")
    if status == "metrics_unavailable":
        return "metrics_unavailable", f"/metrics를 읽지 못해 대조 불가: {delta.get('reason')}"
    if status == "metrics_reset":
        return "metrics_reset", str(delta.get("reason"))
    if status == "no_traffic":
        # 캐시에 대해 아무 주장도 하지 않는 판정이다.
        return "no_traffic", str(delta.get("reason"))
    metrics_hit = float(delta.get("hits_delta") or 0.0) > 0
    ledger_hit = cached > 0
    if metrics_hit and ledger_hit:
        return "both_hit", f"정상 — 장부를 신뢰할 수 있다. {_substitute_note(delta)}"
    if metrics_hit and not ledger_hit:
        return "metrics_only", (
            "서버가 prompt_tokens_details를 주지 않는다 (R6 재현). /metrics는 적중을 "
            f"보고하므로 어댑터 결함이 아니다 — 장부만 0이다. {_substitute_note(delta)}"
        )
    if ledger_hit and not metrics_hit:
        return "ledger_only", (
            "장부는 적중인데 /metrics 증분이 0이다. 카운터 이름이 바뀌었거나 다른 서버를 "
            "긁고 있다 — 무엇인지 확인하기 전까지 이 수치를 쓰지 않는다"
        )
    # 여기부터가 "적중이 없다"이다. 두 원인을 가른다 — 앞은 서버를 다시 띄우면 되고,
    # 뒤는 prefix가 공유되지 않거나 캐시가 매번 축출되는 진짜 조사 대상이다.
    enabled = delta.get("prefix_caching_enabled")
    if enabled is False:
        return "caching_disabled", (
            f"{METRIC_CACHE_CONFIG}의 enable_prefix_caching이 False다. 서버가 그렇게 떠 "
            "있으므로 bench가 할 수 있는 일이 없다 — 서버를 다시 띄워야 한다"
        )
    if enabled is None:
        return "neither", (
            "적중이 0이다. "
            f"{METRIC_CACHE_CONFIG}의 enable_prefix_caching 라벨을 읽지 못해 "
            "'꺼져 있다'와 '켜져 있는데 안 맞는다'를 가르지 못했다"
        )
    return "neither", (
        "enable_prefix_caching은 True인데 적중이 0이다 — prefix가 공유되지 않거나 캐시가 "
        "매번 축출되고 있다"
    )


def _substitute_note(delta: Mapping[str, object]) -> str:
    """장부 대체 수치를 한 줄로. `metrics_only`에서 이 숫자가 R6의 성격을 바꾼다.

    "서버가 장부값을 안 준다"에서 "장부는 0이지만 구간 캐시 토큰은 N개로 얻을 수 있다"가
    된다. **다만 구간 합계라 개별 `model_call`에 배분할 수 없다** — 그래서 하네스의
    `BudgetLedger`는 이 값을 쓰지 않는다(장부 반영 여부는 별건).
    """
    cached = delta.get("prompt_tokens_cached_delta")
    if cached is None:
        return str(delta.get("prompt_tokens_cached_delta_reason") or "대체 수치 없음")
    external = delta.get("external_prefix_cache_hits_delta")
    split = f", 그중 external {external:+.0f}" if isinstance(external, (int, float)) else ""
    return (
        f"metrics 기준 구간 캐시 토큰 {cached:.0f}개(local+external{split}) — 대체 경로 있음. "
        "구간 합계라 개별 model_call에 배분할 수 없어 BudgetLedger에는 반영하지 않는다"
    )


def measure_prefix_cache(
    client: OpenAICompatModelClient,
    role: Role,
    template: PromptTemplate,
    schema: type[BaseModel],
    tokens: int,
    samples: int,
    endpoint: str,
) -> dict[str, object]:
    """D. 같은 prefix 반복 호출의 장부(`Usage.cached_tokens`)와 `/metrics`를 대조한다.

    R6 판정 지점이다. 장부가 0이어도 거기서 끝내지 않는다 — `/metrics`가 적중을 보고하면
    **어댑터 결함이 아니라 서버가 `prompt_tokens_details`를 주지 않는 것**이고, 그 사실이
    출력에 드러나야 한다(2026-08-11 Slurm 실측이 이 경우였다: 적중 99.0%인데 장부는 0).

    카운터는 **구간 delta**로 본다. 절대값은 서버 기동 시점과 앞 구간(B)에 오염된다.
    """
    before = read_counters(endpoint)
    calls = [
        call_once(client, role, sized_prompt(role, template, tokens, i), schema)
        for i in range(samples)
    ]
    after = read_counters(endpoint)
    calls_ok = sum(1 for call in calls if call.ok)
    delta = counter_delta(before, after, calls_ok=calls_ok)
    cached = sum(call.cached_tokens for call in calls)
    prompt_tokens = sum(call.input_tokens for call in calls)
    verdict, note = verdict_for(cached, delta)
    return {
        "verdict": verdict,
        "note": note,
        "ledger": {
            "cached_tokens": cached,
            "prompt_tokens": prompt_tokens,
            "hit_ratio": round(cached / prompt_tokens, 4) if prompt_tokens else 0.0,
        },
        "metrics_delta": delta,
        "calls_ok": calls_ok,
        # 앞 구간(B)이 같은 prefix로 이미 요청했으므로 이 구간의 캐시는 데워져 있다.
        # 적중률을 cold start 수치로 읽지 않게 남긴다.
        "prewarmed_by": ["structured_output"],
    }


def measure_throughput(
    client: OpenAICompatModelClient,
    role: Role,
    template: PromptTemplate,
    schema: type[BaseModel],
    tokens: int,
    concurrency: Sequence[int],
    endpoint: str,
) -> dict[str, object]:
    """C. 동시 요청 1과 `concurrency`별 합산 tok/s.

    output 토큰은 응답 usage의 `completion_tokens`를, 시간은 벽시계를 쓴다. 구간마다
    prefix cache 카운터 delta를 함께 찍는다 — tok/s가 캐시 덕인지 아닌지 사후에
    판별되지 않으면 그 수치로 다음 장비를 예측할 수 없다.
    """
    levels = [1, *sorted(set(concurrency) - {1})]
    by_level: dict[str, object] = {}
    for level in levels:
        by_level[str(level)] = _one_level(client, role, template, schema, tokens, level, endpoint)
    single = by_level.get("1")
    batch32 = by_level.get(str(BATCH32))
    return {
        "by_concurrency": by_level,
        "tok_s_single": float(single["tok_s"]) if isinstance(single, dict) else 0.0,
        "tok_s_batch32": float(batch32["tok_s"]) if isinstance(batch32, dict) else 0.0,
        "tok_s_batch32_reason": (
            None
            if batch32
            else f"--concurrency에 {BATCH32}가 없어 재지 않았다 "
            "(다른 동시성 값으로 대신하지 않는다)"
        ),
    }


def _one_level(
    client: OpenAICompatModelClient,
    role: Role,
    template: PromptTemplate,
    schema: type[BaseModel],
    tokens: int,
    level: int,
    endpoint: str,
) -> dict[str, object]:
    before = read_counters(endpoint)
    prompts = [sized_prompt(role, template, tokens, variant) for variant in range(level)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=level) as pool:
        calls = list(pool.map(lambda p: call_once(client, role, p, schema), prompts))
    elapsed = time.monotonic() - started
    produced = sum(call.output_tokens for call in calls)
    return {
        "concurrency": level,
        "wall_s": round(elapsed, 3),
        "output_tokens": produced,
        "tok_s": round(produced / elapsed, 2) if elapsed > 0 else 0.0,
        "failures": [call.error for call in calls if not call.ok],
        # 이 구간의 캐시 적중률. tok/s가 캐시 덕인지 사후에 판별하려면 옆에 있어야 한다.
        "prefix_cache_delta": counter_delta(
            before, read_counters(endpoint), calls_ok=sum(1 for call in calls if call.ok)
        ),
    }


def measure_memory(
    client: OpenAICompatModelClient,
    role: Role,
    template: PromptTemplate,
    schema: type[BaseModel],
    contexts: Sequence[int],
    endpoint: str,
    context_limit: int,
    *,
    server_limit: int | None = None,
    output_max: int,
) -> dict[str, object]:
    """E. context별 KV cache 점유.

    **`nvidia-smi`의 점유는 context에 따라 거의 변하지 않는다.** vLLM이 기동 시
    `gpu_memory_utilization` 비율만큼 선점하고 가중치를 뺀 나머지를 KV cache로 잡기
    때문이다. 그래서 8K와 32K의 값이 같아도 측정 실패가 아니다 — 그 사실을 사유로
    남긴다. context에 따라 실제로 변하는 것은 `vllm:kv_cache_usage_perc`이고, §3
    체크리스트 3번은 그것으로 답한다.

    요청이 끝나면 KV cache가 반납되므로 **요청이 떠 있는 동안** 표본을 뜬다. 이건 추측이
    아니라 실측이다 — 2026-08-14 Slurm 원문은 요청 6회가 전부 끝난 뒤 긁은 것이고
    `vllm:kv_cache_usage_perc`가 **0.0**이다. 사후에 한 번만 긁으면 이 항목은 언제나 0이
    나온다.
    """
    by_context: dict[str, object] = {}
    for ctx in contexts:
        if ctx > context_limit:
            by_context[str(ctx)] = {
                "skipped": f"registry의 context_limit({context_limit})을 넘는다",
            }
            continue
        # **서버가 못 받는 것은 재보지 않는다** (#126). vLLM은
        # `prompt_tokens + max_tokens > max_model_len`이면 400을 낸다 —
        # 재려다 실패한 것이 아니라 **애초에 못 재는 것**이므로 `dead` 집계가 아니라
        # `skipped`로 간다(`_judge_context`가 그 둘을 가른다).
        #
        # `server_limit`이 `None`이면 이 분기가 통째로 없는 것과 같다 — 상한을 안 주는
        # 서버에서 스윕을 좁히면 **잴 수 있는 것을 안 재게 된다**. 그쪽은 "모른다"가 실제
        # 상태라 기본값이 있고, `output_max`는 **부르는 쪽이 언제나 아는 값**이라 없다:
        # 기본값 `0`이면 이 부등식이 `ctx > server_limit`으로 퇴화해, 2026-09-02 ATOM에서
        # 400을 받은 조건(`ctx + 4,096 > 32,768`)이 다시 통과한다. `_model_client`가
        # `role`에 기본값을 안 두는 사유와 같은 자리다(PR #185).
        if server_limit is not None and ctx + output_max > server_limit:
            by_context[str(ctx)] = {
                "skipped": (
                    f"서버의 max_model_len({server_limit})을 넘는다 — "
                    f"프롬프트 {ctx} + output_max {output_max} = {ctx + output_max}"
                ),
            }
            continue
        by_context[str(ctx)] = _one_context(client, role, template, schema, ctx, endpoint)
    util, util_error = gpu_memory_utilization(endpoint)
    ctx8k = by_context.get(str(CTX8K))
    mb = float(ctx8k.get("nvidia_smi_mb_peak", 0.0)) if isinstance(ctx8k, dict) else 0.0
    return {
        "by_context": by_context,
        "gpu_memory_utilization": util,
        "gpu_memory_utilization_reason": util_error,
        "mem_gb_ctx8k": round(mb / 1024, 3) if mb else 0.0,
        "mem_gb_ctx8k_reason": (
            "이 값은 context에 따라 변하지 않는다 — vLLM이 기동 시 gpu_memory_utilization "
            "비율만큼 선점하기 때문이다. 8K와 32K가 같아도 측정 실패가 아니다. context에 "
            "따라 변하는 것은 kv_cache_usage_perc이며 by_context에 있다"
            if mb
            # **아래 사유를 뭉개지 않는다.** 이 값의 유일한 소스가 nvidia-smi이므로
            # (/metrics는 비율만 주고 GB를 주지 않는다) 그쪽이 왜 값을 못 줬는지가 곧
            # 이 필드가 0인 이유다. "nvidia-smi가 없어서"로 고정해 두면 통합메모리
            # 장비에서 거짓이 된다(2026-08-17 GB10 실측).
            else f"{_ctx8k_reason(ctx8k)} (/metrics는 비율만 주고 GB를 주지 않는다)"
        ),
    }


def _ctx8k_reason(ctx8k: object) -> str:
    """8K 측정이 남긴 nvidia-smi 사유. 없으면 그 사실을 그대로 적는다."""
    if isinstance(ctx8k, dict):
        reason = ctx8k.get("nvidia_smi_mb_reason")
        if reason:
            return str(reason)
    return "8K context 측정이 없어 nvidia-smi 사유를 알 수 없다"


def _one_context(
    client: OpenAICompatModelClient,
    role: Role,
    template: PromptTemplate,
    schema: type[BaseModel],
    ctx: int,
    endpoint: str,
) -> dict[str, object]:
    prompt = sized_prompt(role, template, ctx, variant=ctx)
    peak_perc: list[float] = []
    peak_mb: list[float] = []
    # 마지막 폴링이 준 사유. 값을 한 번이라도 얻으면 `None`으로 덮이므로 남는 것은
    # **끝까지 못 얻었을 때의 실제 이유**다 — 세 경우를 뭉뚱그리지 않는다(`nvidia_smi_mb`).
    mb_reason: str | None = "요청이 도는 동안 nvidia-smi를 한 번도 재지 못했다"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call_once, client, role, prompt, schema)
        while not future.done():
            perc = kv_cache_perc(endpoint)
            if perc is not None:
                peak_perc.append(perc)
            used, mb_reason = nvidia_smi_mb()
            if used is not None:
                peak_mb.append(used)
            time.sleep(_POLL_INTERVAL_S)
        call = future.result()
    return {
        "requested_tokens": ctx,
        "prompt_tokens_approx": approx_tokens(prompt),
        "call_ok": call.ok,
        "error": call.error or None,
        # 필드 이름은 메트릭 이름을 그대로 따른다. 어느 메트릭에서 온 값인지가 출력만
        # 보고 드러나야 한다 — 옛 이름(gpu_cache_usage_perc)이 존재하지 않는 메트릭이었다.
        "kv_cache_usage_perc_peak": max(peak_perc) if peak_perc else 0.0,
        "kv_cache_usage_perc_reason": (
            None if peak_perc else f"{METRIC_KV_USAGE}를 /metrics에서 읽지 못했다"
        ),
        "nvidia_smi_mb_peak": max(peak_mb) if peak_mb else 0.0,
        "nvidia_smi_mb_reason": None if peak_mb else mb_reason,
        # **peak이 몇 표본에서 나왔는지 함께 남긴다.** `*_peak: 0.0` + `reason`은 "한 번도
        # 못 쟀다"만 구별하고, **3 표본에서 나온 peak과 300 표본에서 나온 peak을 구별하지
        # 못한다.** 폴링 간격이 생기면서 표본이 줄어 그 구멍이 커지므로 함께 넣는다 —
        # 캡슐의 `items=k`가 잘린 값인지 캡슐만 봐서는 모르는 것(#63 잔여 (b))과 같은
        # 모양이다.
        #
        # 필드 이름이 곧 지표의 열 이름이다(`IndexSummary.as_trace_fields` docstring).
        # `*_peak`과 짝이 되게 `*_samples`로 붙인다.
        "kv_cache_usage_perc_samples": len(peak_perc),
        "nvidia_smi_mb_samples": len(peak_mb),
    }
