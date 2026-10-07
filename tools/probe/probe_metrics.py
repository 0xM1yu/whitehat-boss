"""vLLM `/metrics` 원문을 같은 절차로 받아 내는 장비 무관 프로브.

2026-08-14 Seraph에서 손으로 조립해 돌린 절차를 그대로 코드화한 것이다. 그 절차가
대화에만 있었기 때문에 ATOM에서 같은 것을 하려면 사람이 명령을 다시 조립해야 했고,
그러면 **두 장비의 원문이 같은 절차에서 나왔다고 보장할 수 없다.** 원문 차이가 순수하게
vLLM 버전 차이로 분리되려면 모델·프롬프트·요청 수가 같아야 한다.

산출물은 그대로 `tests/fixtures/metrics/`의 fixture가 된다(어느 파일인지는 README).

## 측정 순서가 결과를 바꾼다 — 요청보다 먼저 긁지 않는다

**Prometheus 카운터는 첫 관측 전에는 `/metrics`에 나타나지 않는다.** 서버를 띄우자마자
긁으면 `vllm:prefix_cache_queries_total`도 `vllm:prefix_cache_hits_total`도 문서에 없고,
그 상태를 보고 "이 서버에는 그 이름이 없다"고 결론 내리면 틀린다. 그래서

    /v1/models 확인 → before 스냅샷 → 요청 N회 → after 스냅샷

순서를 지키고, **이름 존재 여부는 반드시 after 스냅샷으로 판정한다**(`_missing_names`).
before 스냅샷은 구간 delta의 시작점일 뿐 이름 판정에 쓰지 않는다.

## 이름을 여기에 다시 적지 않는다

확인하는 메트릭 이름은 `src.bench_metrics.METRIC_NAMES`에서 온다. 라벨
(`kv_cache_memory_bytes` 등)은 `cache_config_labels()`에서 온다. 프로브가 이름을 문자열로
다시 적으면 bench가 읽는 것과 프로브가 확인하는 것이 갈라지는데, 정확히 그 자리에서
`gpu_cache_usage_perc` 사고가 났다(존재하지 않는 이름인데 테스트 26개가 초록이었다,
2026-08-14).

`src.bench_metrics`는 **stdlib만 import 한다**(`src/__init__.py`도 docstring뿐이다). 따라서
이 프로브는 저장소를 클론하기만 하면 `pip install` 없이 돈다 — 새 장비에 pydantic이나
requests가 없어도 된다. `jq`·`curl`도 쓰지 않는다(conda의 libcurl이 경고를 쏟는다).

## 남의 서버를 재지 않는다

`/v1/models`의 id가 `--model`과 다르면 **아무것도 재지 않고 죽는다.** 공용 계정에서 남이
띄운 서버에 요청을 쏘고 그 수치를 우리 것으로 기록하는 일이 없어야 한다. `src/bench.py`의
`check_startup()`이 registry key로 하는 검사와 같은 것을, registry 없이 한다.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 스크립트로 직접 실행된다(`python tools/probe/probe_metrics.py`). 저장소 루트가
# sys.path에 없으면 `src`를 못 찾으므로 여기서 넣는다 — 새 장비에서 PYTHONPATH를
# 맞추라는 안내를 하나 더 두는 것보다 싸다.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bench_metrics import (  # noqa: E402
    METRIC_CACHE_CONFIG,
    METRIC_NAMES,
    CacheCounters,
    config_labels_from,
    counter_delta,
    counters_from,
    fetch,
    metrics_url,
    parse_prometheus,
)
from src.console import force_utf8  # noqa: E402

# 4바이트 ≈ 1토큰. 실제 토크나이저가 아니라 prefix 길이를 맞추기 위한 근사다.
# `src/bench.py`의 `CHARS_PER_TOKEN`과 같은 값이지만 import 하지 않는다 — 그 모듈은
# pydantic을 끌어오고, 이 프로브가 의존성 없이 도는 것이 요구사항이다.
CHARS_PER_TOKEN = 4

# 0단계 실측과 같은 값. 원문 `vllm:prefix_cache_queries_total`이 1458 = 243 × 6이고
# 적중이 1120이었다(`tests/fixtures/metrics/vllm_0191_seraph_sm86.txt`). 이 둘을 바꾸면
# ATOM 원문을 Seraph 원문과 직접 대조할 수 없다.
DEFAULT_REQUESTS = 6
DEFAULT_PREFIX_TOKENS = 240

DEFAULT_MAX_TOKENS = 32
DEFAULT_WAIT_S = 300.0
_TIMEOUT_S = 120
_POLL_S = 2.0

# system 메시지로 나가는 고정 prefix. **길이가 요점이다.** 짧으면 prefix cache 블록 경계
# (실측 서버는 block_size=16)에 걸리지 않아 적중이 0으로 나오고, 그러면 "캐시가 안 걸린다"는
# 틀린 결론이 난다.
_PREFIX_HEAD = (
    "You are a static analysis assistant for C and C++ memory safety review.\n"
    "Answer with one short sentence. Do not explain your reasoning.\n"
)
_PREFIX_FILLER = (
    "Rule: report any copy whose destination bound is not derived from the source length.\n"
)

# 요청마다 달라지는 본문. **매번 같으면 서버가 본문 접두사까지 캐시해 측정이 왜곡된다** —
# 운영에서 공유되는 것은 prefix뿐이므로 여기서도 그래야 한다.
_BODY = "Review call site {index}: memcpy(dst_{index}, src_{index}, len_{index});"


class ProbeError(RuntimeError):
    """잴 수 없다. 아무 파일도 쓰지 않고 끝낸다."""


# --- HTTP -------------------------------------------------------------------


def _post_json(url: str, payload: dict[str, Any]) -> str:
    """POST 하나. `urllib`만 쓴다.

    `src/adapters/openai_compat.py`를 재사용하지 않는 것은 그 모듈이 pydantic과 하네스
    스키마를 끌어오기 때문이다. 여기서 적는 것은 OpenAI 호환 API의 표준 경로이지 vLLM이
    버전마다 바꾸는 이름이 아니다 — 갈라지면 안 되는 것은 메트릭 이름이고, 그쪽은
    `METRIC_NAMES`를 지난다.
    """
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
        return response.read().decode("utf-8")


def wait_for_models(endpoint: str, model: str, *, wait_s: float, sleep: Any) -> list[str]:
    """서버가 뜰 때까지 기다렸다가 `/v1/models`의 id를 돌려준다.

    **`model`이 그 목록에 없으면 `ProbeError`다.** 공용 장비에서 남이 띄운 서버를 재고
    우리 수치로 기록하는 것을 막는 유일한 검사다.
    """
    url = f"{endpoint.rstrip('/')}/models"
    waited = 0.0
    last = "아직 시도하지 않았다"
    while waited <= wait_s:
        body, error = fetch(url)
        if body is not None:
            try:
                served = [str(item.get("id", "")) for item in json.loads(body).get("data", [])]
            except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                raise ProbeError(f"{url} 응답을 읽지 못했다: {exc}") from exc
            if model not in served:
                raise ProbeError(
                    f"{url}가 내놓은 모델 {served}에 {model!r}이 없다 — 다른 사람이 띄운 "
                    "서버이거나 --served-model-name이 다르다. 재지 않고 끝낸다"
                )
            return served
        last = error or "사유 불명"
        sleep(_POLL_S)
        waited += _POLL_S
    raise ProbeError(f"{wait_s:.0f}초 동안 서버가 뜨지 않았다: {last}")


# --- 프롬프트 ----------------------------------------------------------------


def build_prefix(tokens: int) -> str:
    """목표 토큰 수에 준하는 고정 prefix. 요청마다 **똑같아야** 캐시가 걸린다."""
    want = max(1, tokens) * CHARS_PER_TOKEN
    text = _PREFIX_HEAD
    while len(text) < want:
        text += _PREFIX_FILLER
    return text


def build_body(index: int) -> str:
    return _BODY.format(index=index)


# --- 측정 -------------------------------------------------------------------


def _call(endpoint: str, model: str, prefix: str, index: int, max_tokens: int) -> dict[str, Any]:
    """호출 하나. 실패도 값으로 다룬다 — 몇 건이 실패했는지가 판정에 들어간다."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prefix},
            {"role": "user", "content": build_body(index)},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    try:
        raw = _post_json(f"{endpoint.rstrip('/')}/chat/completions", payload)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "raw": None}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"응답이 JSON이 아니다: {exc}", "raw": raw}
    return {"ok": True, "error": None, "raw": raw, "usage": parsed.get("usage") or {}}


def _missing_names(after_text: str) -> list[str]:
    """`METRIC_NAMES` 중 이 서버에 **없는** 이름.

    **after 스냅샷으로만 판정한다.** 카운터는 첫 관측 전에는 노출되지 않으므로 before로
    보면 전부 "없음"으로 나온다(모듈 docstring).
    """
    present = {name for name, _, _ in parse_prometheus(after_text)}
    return [name for name in METRIC_NAMES if name not in present]


def _snapshot(endpoint: str) -> tuple[str | None, CacheCounters, str | None]:
    """`/metrics`를 **한 번만** 긁어 원문과 카운터를 함께 낸다.

    두 번 긁으면 그 사이의 요청이 어느 쪽 구간에 잡혔는지 알 수 없다(`read_counters`의
    같은 판단). 여기서는 이유가 하나 더 있다 — **저장하는 원문이 리포트의 delta가 나온
    바로 그 문서여야** fixture와 판정이 어긋나지 않는다. 두 번 긁으면 fixture에는 없는
    적중이 리포트에 적히는 일이 가능해진다.
    """
    text, error = fetch(metrics_url(endpoint))
    if text is None:
        return None, CacheCounters(None, None, error), error
    return text, counters_from(text), None


def probe(
    *,
    endpoint: str,
    model: str,
    requests: int,
    prefix_tokens: int,
    max_tokens: int,
    wait_s: float,
    sleep: Any,
) -> dict[str, Any]:
    """0단계 절차 그대로. 돌려주는 dict가 곧 리포트다."""
    served = wait_for_models(endpoint, model, wait_s=wait_s, sleep=sleep)

    # 1) before — 구간 delta의 시작점. 이름 판정에는 쓰지 않는다.
    before_text, before, before_error = _snapshot(endpoint)

    # 2) 같은 prefix + 서로 다른 body로 N회.
    prefix = build_prefix(prefix_tokens)
    calls = [_call(endpoint, model, prefix, index, max_tokens) for index in range(requests)]
    ok_calls = [call for call in calls if call["ok"]]

    # 3) after — 이름 판정과 delta의 끝점. 라벨도 이 문서에서 나온다.
    after_text, after, after_error = _snapshot(endpoint)
    if after_text is None:
        raise ProbeError(f"after 스냅샷을 받지 못했다: {after_error}")

    labels = config_labels_from(after_text)
    labels_error = None if labels else f"{METRIC_CACHE_CONFIG}가 /metrics에 없다"
    first_ok = ok_calls[0] if ok_calls else None
    usage = first_ok["usage"] if first_ok else {}
    return {
        "endpoint": endpoint,
        "model": model,
        "served_ids": served,
        "requests": requests,
        "prefix_tokens_requested": prefix_tokens,
        "prefix_chars": len(prefix),
        "calls_ok": len(ok_calls),
        "call_errors": [call["error"] for call in calls if not call["ok"]],
        # `METRIC_NAMES` 중 이 서버에 없는 이름. 비어 있지 않으면 bench가 이 서버에서
        # 그 값을 못 읽는다는 뜻이고, fixture로 넣는 순간 CI가 같은 사실을 말한다.
        "missing_metric_names": _missing_names(after_text),
        "metrics_delta": counter_delta(before, after, calls_ok=len(ok_calls)),
        "cache_config": labels,
        "cache_config_reason": labels_error,
        # 응답이 prefix cache 적중을 장부로 주는가(R6). Seraph는 null이었다.
        "prompt_tokens_details": usage.get("prompt_tokens_details", None),
        "usage_first_ok": usage,
        "before_metrics_reason": before_error,
        "_before_text": before_text,
        "_after_text": after_text,
        "_completion_raw": first_ok["raw"] if first_ok else None,
    }


# --- 산출물 ------------------------------------------------------------------


def _slug(value: str) -> str:
    """파일명에 들어갈 조각. 영숫자만 남긴다 (`sm_86` → `sm86`)."""
    cleaned = re.sub(r"[^0-9a-zA-Z]", "", value).lower()
    if not cleaned:
        raise ProbeError(f"파일명에 쓸 수 없는 값이다: {value!r}")
    return cleaned


def fixture_stem(vllm_version: str, host_label: str, arch: str) -> str:
    """`tests/fixtures/metrics/`의 명명 규칙. 0.19.1 + seraph + sm_86 → vllm_0191_seraph_sm86.

    **손으로 이름을 바꾸는 단계가 남으면 안 된다** — 받아서 그대로 옮기면 fixture가 된다.
    """
    return f"vllm_{_slug(vllm_version)}_{_slug(host_label)}_{_slug(arch)}"


def write_outputs(report: dict[str, Any], out_dir: Path, stem: str) -> dict[str, str]:
    """원문 2개 + 응답 + 리포트를 쓴다. 어느 것이 fixture인지가 확장자로 갈린다.

    after 스냅샷만 `.txt`다. **before 스냅샷을 `.txt`로 두면 안 된다** — 그 문서에는
    카운터가 아직 없고(모듈 docstring), `tests/fixtures/metrics/`로 복사되면
    `test_bench_reads_only_names_that_exist_in_every_fixture`가 `*.txt`를 전부 훑으므로
    "이름이 없다"고 실패한다. Prometheus 노출 형식의 관용 확장자인 `.prom`으로 둬서 실수로
    fixture가 되는 길을 막는다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    after = out_dir / f"{stem}.txt"
    _write_raw(after, report["_after_text"])
    written["fixture_metrics"] = str(after)

    if report["_before_text"] is not None:
        before = out_dir / f"{stem}.before.prom"
        _write_raw(before, report["_before_text"])
        written["before_metrics"] = str(before)

    if report["_completion_raw"] is not None:
        completion = out_dir / f"{stem}_chat_completion.json"
        _write_raw(completion, report["_completion_raw"])
        written["fixture_completion"] = str(completion)

    summary = {key: value for key, value in report.items() if not key.startswith("_")}
    path = out_dir / f"{stem}.probe.json"
    _write_raw(path, json.dumps(summary, ensure_ascii=False, indent=2))
    written["report"] = str(path)
    return written


def _write_raw(path: Path, text: str) -> None:
    """서버가 보낸 바이트 그대로 쓴다. **개행을 번역하지 않는다.**

    `Path.write_text`의 기본 텍스트 모드는 Windows에서 `\\n`을 `\\r\\n`으로 바꾼다. 이
    파일들은 `tests/fixtures/metrics/`로 들어가고, `.gitattributes`가 그 경로를 `-text`로
    빼 두었다 — "실측 원문은 서버가 내보낸 바이트 그대로 둔다"가 그 규칙의 이유다.
    Windows를 경유해 ATOM 원문을 받으면 여기서 CRLF가 섞이고, 그러면 저장소의 원문이
    서버가 실제로 내보낸 것과 달라진다. `bench_report._atomic_write`가 `newline=""`을
    쓰는 것과 같은 판단이다.
    """
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def render(report: dict[str, Any], written: dict[str, str]) -> list[str]:
    """사람이 읽는 요약. 판정에 필요한 것만 낸다."""
    delta = report["metrics_delta"]
    labels = report["cache_config"]
    missing = report["missing_metric_names"]
    details = report["prompt_tokens_details"]
    lines = [
        f"endpoint      : {report['endpoint']}  (served: {report['served_ids']})",
        f"요청          : {report['calls_ok']}/{report['requests']} 성공"
        f"  (prefix {report['prefix_tokens_requested']}토큰 목표 / {report['prefix_chars']}자)",
        f"없는 메트릭   : {missing if missing else '없다 — METRIC_NAMES 전부 존재한다'}",
        f"카운터 구간   : status={delta.get('status')} "
        f"queries={delta.get('queries_delta')} hits={delta.get('hits_delta')} "
        f"hit_ratio={delta.get('hit_ratio')}",
        f"캐시 토큰     : prompt_tokens_cached_delta={delta.get('prompt_tokens_cached_delta')}",
        f"prefix caching: enable_prefix_caching={labels.get('enable_prefix_caching')}"
        f"  gpu_memory_utilization={labels.get('gpu_memory_utilization')}",
        # ATOM에서 이 값이 숫자면 mem_gb_ctx8k를 nvidia-smi 없이 얻을 수 있다.
        # Seraph는 문자열 "None"이었다.
        # ⏵ 2026-10-08 — ATOM도 None이었다(2026-08-18-serving-atom.md §3.3).
        f"kv_cache_memory_bytes: {labels.get('kv_cache_memory_bytes')}",
        f"prompt_tokens_details: {details}"
        f"{'  (R6 재현 — 서버가 장부를 주지 않는다)' if details is None else ''}",
    ]
    if report["cache_config_reason"]:
        lines.append(f"[WARN ] cache_config: {report['cache_config_reason']}")
    lines.extend(f"[WARN ] 호출 실패: {error}" for error in report["call_errors"])
    lines.extend(["", "산출물:"])
    for key in ("fixture_metrics", "fixture_completion", "before_metrics", "report"):
        if key in written:
            lines.append(f"  {key:18} {written[key]}")
    names = [
        Path(written[key]).name
        for key in ("fixture_metrics", "fixture_completion")
        if key in written
    ]
    lines.extend(
        [
            "",
            "fixture로 옮길 것은 아래 둘뿐이다 (before_metrics는 옮기지 않는다 — 카운터가 "
            "아직 없어 CI가 '이름이 없다'고 실패한다):",
            f"  cp {' '.join(names)} tests/fixtures/metrics/",
        ]
    )
    return lines


# --- 진입점 -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/probe/probe_metrics.py",
        description="vLLM /metrics 원문을 장비 무관 절차로 받아 fixture 후보로 저장한다",
    )
    parser.add_argument("--endpoint", required=True, help="예: http://127.0.0.1:8001/v1")
    parser.add_argument("--model", required=True, help="--served-model-name과 같아야 한다")
    # 아래 셋은 파일명에 박힌다. `/metrics`도 `/v1/models`도 vLLM 버전과 아키텍처를 주지
    # 않으므로(실측 확인) 추론하지 않고 받는다 — 지어낸 이름의 fixture가 CI를 통과하는
    # 것이 최악이다.
    parser.add_argument("--vllm-version", required=True, help="예: 0.19.1")
    parser.add_argument("--host-label", required=True, help="예: seraph / atom")
    parser.add_argument("--arch", required=True, help="예: sm_86 / sm_121")
    parser.add_argument("--out", type=Path, required=True, help="산출물 디렉터리")
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
    parser.add_argument("--prefix-tokens", type=int, default=DEFAULT_PREFIX_TOKENS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_S, help="기동 대기 상한(초)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """종료 코드는 **프로브가 제대로 돌았는가**만 뜻한다.

    없는 메트릭 이름이나 캐시 미적중은 판정 결과이지 프로브의 실패가 아니므로 0으로
    끝내고 리포트에 싣는다 — `src/cli.py`의 `bench`가 체크리스트 실패를 종료 코드로
    옮기지 않는 것과 같은 이유다(그러면 서버 없는 장비에서 CI가 빨개진다).
    """
    import time

    # Windows 콘솔(cp949)에서는 이 파일의 출력에 있는 `—`가 `UnicodeEncodeError`를 내고
    # **출력 도중에 죽는다.** 저장소가 이미 두 번 물린 결함이라 진입점마다 부른다
    # (`src/console.py`). 실측 현장에서 수치를 다 받아 놓고 화면에 못 찍는 것이 최악이다.
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        report = probe(
            endpoint=args.endpoint,
            model=args.model,
            requests=args.requests,
            prefix_tokens=args.prefix_tokens,
            max_tokens=args.max_tokens,
            wait_s=args.wait,
            sleep=time.sleep,
        )
        written = write_outputs(
            report, args.out, fixture_stem(args.vllm_version, args.host_label, args.arch)
        )
    except ProbeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    for line in render(report, written):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
