"""gpt-oss-20b structured output — **serve 설정 하나만** 바꿔 가며 §3.3을 반복한다.

[2026-09-09 보고서](../../docs/experiments/harness/2026-09-09-gpt-oss-20b-onboarding-atom.md)
§5가 미측정으로 남긴 셋과, 머리의 정정 블록이 *"플래그 단독 효과는 여전히 미확인"*이라고
적은 A/B를 같은 프로브로 돈다.

## 이 스크립트가 바꾸지 않는 것

**아무것도 안 바꾼다.** 프로브는 고정이고, 바뀌는 것은 **`vllm serve`를 어떻게 띄웠는가**
하나다. 그래서 팔마다 서버를 다시 띄우고 `--label`로 그 조건을 적어 준다.

세 축이 전부 serve 쪽이기 때문이다 — `--structured-outputs-config.backend`(`xgrammar`↔`outlines`) ·
`.reasoning_parser` 명시 · `.enable_in_reasoning`. 클라이언트가 고를 수 있는 축이 아니다.
`config.py`의 `ModelEntry.extra_params` 주석이 같은 경계를 긋는다 — *"서버를 띄우는 값과
가른다. `--structured-outputs-config.*`는 **하네스가 안 보낸다**"*.

## 왜 `curl`이 아닌가

원 실험의 메타가 적는다 — *"원문 산출물 **보존하지 않았다** — `curl` 응답을 파이프로
흘렸다. §3.3 표가 **재구성본**이다."* 그래서 지금 남은 것은 표뿐이고 원문 응답이 없다.
이 스크립트는 **요청과 응답 전문을 JSONL로 남긴다.** 팔이 넷이면 사후에 무엇이 달랐는지를
표가 아니라 원문에서 본다.

## ⚠ §3.3을 「그대로」 반복할 수는 없다 — `max_tokens`가 미기록이다

보고서 §3.3 표에 `max_tokens` 열이 없고, 정정 블록이 *"`max_tokens` | 미기록"*으로 적는다.
그러니 **완전한 반복은 불가능하다.** 여기서는 `3000`을 쓰고 **모든 팔에서 같게 둔다** —
정정 블록이 *"700은 잘려 빈 JSON, 3000은 괜찮다"*고 적은 실측을 따른다. 이것은 §3.3과의
**알려진 차이**이고, 팔 사이에서는 상수이므로 플래그 단독 효과는 그대로 갈린다.
**보고서에 이 줄을 그대로 옮겨 적는다.**

## 스키마 두 벌

| `--schema` | verdict 어휘 | 왜 |
| --- | --- | --- |
| `report33` | `upheld` / `rejected` | **§3.3 그대로.** 플래그 효과를 §3.3과 비교하려면 이쪽 |
| `decision` | `uphold` / `rebut` / `need_evidence` | **배포될 스키마**(`src/schemas/enums.py`) |

정정 블록의 살아 있는 대안 가설이 **어휘 mismatch**다 — *"`upheld`/`rejected`라는 판정
어휘를 주면서 판정할 대상(답안)은 안 줬다"*. 두 스키마를 같은 서버에서 돌리면 그 가설이
플래그 축과 **독립으로** 갈린다. 보고서 §7 후속 2가 *"셋 중 ②가 제일 싸다"*고 적은 것이
이쪽이다.

⚠ **`decision` 팔이 R8을 닫지는 않는다.** R8의 닫히는 조건 **1**은 `cli bench`의
`structured_output.valid_ratio`가 **1.0**인 것이고(`src/bench_report.py`의
`_judge_structured`가 쓰는 통과선 — 값이 나온 것은 통과가 아니다), 그것은 **하네스가
보내는 요청**이다. 이 스크립트는 `curl`과 같은 자리에 있다 — registry 등재 뒤
`cli bench --roles critic`이 따로 온다.

⚠ **조건 3(Critic 반박 정확도)은 `cli bench`에도 없다.** 고정 반박 표본(진짜 N +
오탐 N)이 선행이고 표본 크기·합격선은 D4가 정한다. **이 프로브가 내는 정답률은 그
표본이 아니다** — 팔을 가르는 A/B용이라 합격 판정으로 읽지 않는다.

## 쓰는 법

팔마다 서버를 다시 띄우고, 매번 같은 명령을 `--label`만 바꿔 친다.

    python tools/probe/gpt_oss_structured.py --label baseline-xgrammar --trials 5
    python tools/probe/gpt_oss_structured.py --label reasoning-parser-explicit --trials 5
    python tools/probe/gpt_oss_structured.py --label enable-in-reasoning --trials 5
    python tools/probe/gpt_oss_structured.py --label outlines --trials 5

`--out`은 기본 `gpt_oss_structured.jsonl`이고 **덧붙인다** — 팔 넷이 한 파일에 쌓인다.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 보고서 §3.3의 프롬프트. **정답이 자명한 것을 골랐다**고 적혀 있고, 정답은 UAF 있음이다.
UAF_PROMPT = (
    "Does this C snippet have a use-after-free?\n"
    'char *p=malloc(8); free(p); strcpy(p,"x");'
)

# §3.3이 쓴 스키마. `reason`이고 `rationale`이 아니다 — 배포 스키마와 다른 것이 요점이다.
SCHEMA_REPORT33: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["upheld", "rejected"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}
# 배포될 것. `src/schemas/enums.py`의 `Verdict` + agent-contracts.md §3의 `rationale`.
SCHEMA_DECISION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["uphold", "rebut", "need_evidence"]},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
    "additionalProperties": False,
}
# 각 스키마에서 **정답**에 해당하는 verdict. UAF가 있으므로 「주장을 지지」다.
CORRECT = {"report33": "upheld", "decision": "uphold"}
SCHEMAS = {"report33": SCHEMA_REPORT33, "decision": SCHEMA_DECISION}


def _post(endpoint: str, payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(  # noqa: S310 - 로컬 vLLM이다
        f"{endpoint.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # **500도 관측이다.** §3.3의 `guidance` 조건이 5/5 HTTP 500이었고 그 사유를 못 봤다 —
        # 본문을 버리면 이번에도 못 본다.
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"_raw": body}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # **접속 자체가 실패한 것과 서버가 거절한 것을 가른다** — 상태 0이 전자다.
        # 여기서 안 잡으면 프로브가 통째로 죽고, 그때 **그 팔에서 이미 돈 시행까지
        # 잃는다.** 서버가 아직 안 떴거나 기동 중 죽는 것은 ATOM에서 실제로 있는 일이고
        # (보고서 §1 — flashinfer JIT가 엔진 초기화를 죽였다), 팔마다 재기동하는 이
        # 실험은 그 자리를 매번 지난다.
        return 0, {"_urlerror": str(getattr(exc, "reason", exc))}


def _trial(
    *, endpoint: str, model: str, schema_key: str, structured: bool, effort: str,
    max_tokens: int, timeout: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": UAF_PROMPT}],
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
    }
    if structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "Decision" if schema_key == "decision" else "Verdict",
                "schema": SCHEMAS[schema_key],
                "strict": True,
            },
        }
    started = time.monotonic()
    status, body = _post(endpoint, payload, timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    record: dict[str, Any] = {"status": status, "wall_ms": elapsed_ms}
    if status != 200:
        record["error_body"] = body
        return record

    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    # **vLLM 0.27.1이 쓰는 키는 `reasoning`이다. `reasoning_content`는 없다.**
    # 2026-09-12 ATOM 실측 — 응답 `message`의 키 집합이 `annotations`·`audio`·`content`·
    # `function_call`·`reasoning`·`refusal`·`role`이다(자유 서술·스키마 강제 양쪽 동일).
    #
    # ⚠ **그래서 이 필드로 낸 과거 관측은 무효다.** 09-09 보고서 §3.3이 열어 둔
    # *"`reasoning_content`가 왜 비는가 — 안 갈랐다"*와 §해석의
    # *"스키마 강제와 무관하게 채널이 API 응답에서 갈리지 않는다"*가 그것이다 — 채널은
    # 갈려 나오고, 안 갈린 것은 우리가 없는 키를 읽었기 때문이다
    # (2026-09-12 보고서 §4 — `docs/experiments/harness/`의
    # `2026-09-12-critic-gptoss-token-accounting-atom.md`).
    #
    # **필드 이름도 같이 고친다.** `reasoning_content_len`으로 남기면 산출물만 보고는 어느
    # 키를 읽은 것인지 모르고, 과거 JSONL과 새 JSONL이 같은 이름 아래 다른 것을 담는다.
    reasoning = message.get("reasoning")
    record |= {
        "finish_reason": choice.get("finish_reason"),
        "completion_tokens": (body.get("usage") or {}).get("completion_tokens"),
        "reasoning_empty": not reasoning,
        "reasoning_len": len(reasoning or ""),
        "content": content,
    }
    if not structured:
        record["json_valid"] = None
        record["verdict"] = None
        return record
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        record |= {"json_valid": False, "verdict": None, "json_error": str(exc)}
        return record
    record |= {
        "json_valid": True,
        "verdict": parsed.get("verdict"),
        "reason": parsed.get("reason") or parsed.get("rationale"),
        "correct": parsed.get("verdict") == CORRECT[schema_key],
    }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="gpt-oss-20b structured output 통제 실험")
    parser.add_argument("--label", required=True, help="이 서버 기동 조건의 이름. 팔을 가른다")
    # 포트 재배치로 gpt-oss-20b는 8002다(`config/models.yaml`의 endpoint — 2026-09-24에 8003이었고
    # ⏵ 2026-10-14 Scout가 한 서버가 되며 한 칸 내려왔다). 이 도구로 돈
    # 2026-09-11 보고서의 팔들은 8006에서 떴다 — 그 기록을 다시 돌릴 때는 `--endpoint`로 넘긴다.
    parser.add_argument("--endpoint", default="http://127.0.0.1:8002/v1")
    parser.add_argument("--model", default="gpt-oss-20b")
    parser.add_argument("--schema", default="report33", choices=sorted(SCHEMAS))
    parser.add_argument("--trials", type=int, default=5)
    # §3.3은 `response_format` 없는 대조군도 함께 돌렸다 (3/5 → 3/3). 같이 돈다.
    parser.add_argument("--free-form-trials", type=int, default=3)
    parser.add_argument("--effort", default="high", help="§3.3 전 시행이 high였다")
    parser.add_argument("--max-tokens", type=int, default=3000, help="§3.3은 미기록. 위 docstring")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", type=Path, default=Path("gpt_oss_structured.jsonl"))
    parser.add_argument("--serve-cmd", default="", help="이 팔을 띄운 명령 전문. 기록에 박는다")
    args = parser.parse_args(argv)

    if not args.serve_cmd:
        print("⚠ --serve-cmd 를 안 줬다. 어느 기동 조건이었는지가 기록에 안 남는다 —")
        print("  팔이 넷이면 사후에 라벨과 실제 인자가 어긋나도 아무도 모른다.\n")

    print(f"[팔]    {args.label}")
    print(f"[스키마] {args.schema}  (정답 = {CORRECT[args.schema]})")
    print(f"[서버]  {args.model} @ {args.endpoint}")
    print(f"[상수]  max_tokens={args.max_tokens} reasoning_effort={args.effort}\n")

    structured = [
        _trial(
            endpoint=args.endpoint, model=args.model, schema_key=args.schema, structured=True,
            effort=args.effort, max_tokens=args.max_tokens, timeout=args.timeout,
        )
        for _ in range(args.trials)
    ]
    free = [
        _trial(
            endpoint=args.endpoint, model=args.model, schema_key=args.schema, structured=False,
            effort=args.effort, max_tokens=args.max_tokens, timeout=args.timeout,
        )
        for _ in range(args.free_form_trials)
    ]

    ok = [t for t in structured if t["status"] == 200]
    valid = [t for t in ok if t.get("json_valid")]
    correct = [t for t in valid if t.get("correct")]
    empty_reasoning = [t for t in ok if t.get("reasoning_empty")]

    print(f"  HTTP 200        {len(ok)}/{len(structured)}")
    print(f"  JSON 유효       {len(valid)}/{len(ok) or 0}")
    print(f"  verdict 정답    {len(correct)}/{len(valid) or 0}")
    print(f"  reasoning 빔    {len(empty_reasoning)}/{len(ok) or 0}")
    if valid:
        print(f"  verdict들       {[t.get('verdict') for t in valid]}")
    free_ok = [t for t in free if t["status"] == 200]
    print(f"  자유서술 200    {len(free_ok)}/{len(free)}  (§3.3 대조군)")
    for t in structured:
        if t["status"] != 200:
            detail = json.dumps(t.get("error_body"), ensure_ascii=False)[:160]
            print(f"    ! HTTP {t['status']}: {detail}")

    payload = {
        "probe": "gpt_oss_structured",
        "label": args.label,
        "serve_cmd": args.serve_cmd,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "endpoint": args.endpoint,
        "model": args.model,
        "schema_key": args.schema,
        "schema": SCHEMAS[args.schema],
        "prompt": UAF_PROMPT,
        "correct_verdict": CORRECT[args.schema],
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.effort,
        "max_tokens_note": "§3.3은 max_tokens 미기록. 3000은 이 실험이 고정한 값이고 팔 간 상수다",
        "structured_trials": structured,
        "free_form_trials": free,
    }
    with args.out.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(f"\n[기록] {args.out} 에 덧붙였다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
