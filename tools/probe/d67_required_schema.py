"""D67 (#172) — 스키마 자리가 `path`·`evidence_refs`의 **채움**을 가르는지 잰다.

D67 본문의 ⚠ ②가 지목한 실험이다 — *"09-04와 같은 프롬프트를 쓰되 스키마만 required로
올려 재야 한다 … **그것은 아직 안 돌았다.**"*

## 무엇을 안 하는가

**「모델이 왜 안 채우는가」를 답하지 않는다.** D67의 갈래 셋(①프롬프트가 약하다
②스키마가 빈 리스트를 허용한다 ③모델의 성질) 중 **②만** 가른다. ②가 거짓으로 나와도
①·③은 그대로 열려 있다.

## 왜 팔이 셋인가 — 「required」만으로는 09-04와 축이 안 맞는다

D67 ⓑ가 적은 축 차이가 여기서 되살아난다. **`required`로 올려도 빈 리스트는 스키마를
만족한다** — 강제되는 것은 *키가 나오는가*이고, 09-04가 센 것은 *리스트가 비었는가*다.
`find_0001`은 `"path": []` · `"evidence_refs": []`로 **키는 둘 다 있었다.** 그러니 팔을
`required` 하나만 두면 09-04와 같은 축을 재지 못하고, 08-29 프로브가 밟은 자리를 한 번 더
밟는다.

| 팔 | 스키마 | 무엇을 가르는가 |
| --- | --- | --- |
| `stock` | 두 필드를 `required`에서 **뺀다** | **대조군.** 09-04 관측이 이 조건에서 나왔다 |
| `required` | 두 필드를 `required`에 넣는다 | 키를 강제하면 채우는가 |
| `min_items` | `required` + `minItems: 1` | 빈 리스트를 막으면 **무엇을** 넣는가 |

**`min_items` 팔은 채움률을 재려고 있는 게 아니다** — 그 팔에서 채움률은 정의상 100%다.
재는 것은 **모델이 무엇을 넣는가**이고, 그래서 이 스크립트는 돌아온 `evidence_refs`가
**사실 블록의 `evidence` ID 집합 안에 있는지** 대조한다. 밖의 ID가 나오면 그것은
v1 규칙(*"사실 블록의 `evidence`에 없는 근거 ID를 만들어 내지 않는다"*) 위반이고,
**D67 선택지 (d)가 만들려는 모양**(값은 채워지는데 그 값의 뜻이 없다 — D23과 같은 계열)이
스키마 강제만으로도 생긴다는 뜻이다. 그 경우 (d)는 더 나빠진다.

## 왜 프롬프트를 재구성하지 않고 재생하는가

D67이 남긴 것: *"이 run의 메타는 `repo_commit`이 **미기재**라 당시 요청에 실제로 무엇이
실렸는지는 **재확인이 남는다**."* 프롬프트를 지금 코드로 다시 만들면 그 미기재가 그대로
변수로 남는다 — 지금 렌더한 것이 그때 간 것과 같다는 보장이 없고, 그 차이가 결과에 섞이면
단일 변수가 깨진다(evaluation.md §2.3).

**`{run_dir}/prompts/000N_auditor.md`가 그때 실제로 간 요청이다.** `prompt_log.render()`가
`<!-- prefix -->`·`<!-- body -->`로 기계가 되읽을 경계를 넣어 뒀으므로 그대로 파싱해
**한 글자도 안 바꾸고** 재생한다. 그러면 팔 사이에서 움직이는 것이 스키마 하나뿐이 된다.

⚠ **재구성 경로를 두지 않는다.** 프롬프트 파일이 없으면 **실패한다.** 재구성으로
조용히 넘어가면 산출물만 보고는 어느 경로로 쟀는지 모르고, 그때 이 실험의 결론은
"단일 변수"라는 전제를 잃는다.

## 쓰는 법

    python tools/probe/d67_required_schema.py \
        --run-dir /path/to/artifacts/run_20260904_0001 \
        --profile harness_pool --trials 5 \
        --out d67_probe.json

`--run-dir`의 `prompts/`에서 `_auditor.md`(1차만, `_retryK`는 제외)를 순번대로 읽는다.
`--prompt-index`로 하나만 고를 수 있다. 기본은 첫 파일 하나다 — **프롬프트를 여럿 섞으면
팔 간 차이와 프롬프트 간 차이가 분리되지 않는다.**
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.adapters.factory import _model_client  # noqa: E402
from src.config import load_config  # noqa: E402
from src.interfaces.model_client import Prompt  # noqa: E402
from src.orchestrator.prompt_log import BODY_MARK, HEADER, PREFIX_MARK  # noqa: E402
from src.schemas.enums import Role  # noqa: E402
from src.schemas.finding import Finding  # noqa: E402

# `HEADER`는 format 문자열이라 그대로는 못 쓴다. 채워진 값을 되읽기 위한 패턴이다.
_HEADER_RE = re.compile(
    re.escape(HEADER)
    .replace(re.escape("{role}"), r"(?P<role>[^\s]+)")
    .replace(re.escape("{stage}"), r"(?P<stage>\d+)")
    .replace(re.escape("{version}"), r"(?P<version>[^\s]+)")
)

# 사실 블록은 body의 **마지막** ```json 펜스다 (prompt_loader.build_facts docstring).
_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

ARMS = ("stock", "required", "min_items")
FIELDS = ("path", "evidence_refs")


def _schema_for(arm: str) -> dict[str, Any]:
    """팔에 맞는 JSON 스키마. **`Finding`에서 출발해 그 팔의 차이만 얹는다.**

    Pydantic 서브클래스로 만들지 않는 것은 `minItems`가 필드 타입이 아니라 스키마
    제약이라 두 팔의 생성 경로가 갈리기 때문이다. 여기서는 **한 곳에서 같은 dict을
    복사해 한 군데만 고친다** — 팔 사이에서 스키마 외의 것이 달라질 자리를 없앤다.
    """
    schema = Finding.model_json_schema()
    if arm == "stock":
        # ⚠ **대조군은 두 필드를 `required`에서 직접 뺀다.** 2026-09-20에 `Finding`이 그 둘을
        # required로 올렸고(이슈 #172), 그때까지 이 자리는 `model_json_schema()`를 **그대로
        # 돌려줬다.** 그 순간 대조군이 실험군과 바이트 동일해지는데 **아무 신호도 안 난다** —
        # 다시 돌리면 `stock`이 100% 채우고, 읽는 사람은 09-11 관측이 재현 안 된다고 읽는다.
        # 아래 `min_items` 팔이 스키마 변화에 `SystemExit`을 두는 것과 같은 자리다.
        required = [name for name in schema.get("required", []) if name not in FIELDS]
        if len(required) == len(schema.get("required", [])):
            raise SystemExit(
                "stock 팔에서 뺄 것이 없었다 — `Finding`의 두 필드가 required가 아니다. "
                "대조군과 실험군이 같아지므로 그대로 돌리지 않는다"
            )
        schema["required"] = required
        return schema
    required = list(schema.get("required", []))
    for name in FIELDS:
        if name not in required:
            required.append(name)
    schema["required"] = required
    if arm == "min_items":
        for name in FIELDS:
            # `$ref`/`anyOf`로 감싸이지 않은 순수 배열이라 properties에서 바로 잡힌다.
            prop = schema["properties"][name]
            if prop.get("type") != "array":
                raise SystemExit(
                    f"{name}이 배열이 아니다({prop.get('type')!r}) — 스키마가 바뀌었다. "
                    "minItems를 얹을 자리를 다시 찾는다"
                )
            prop["minItems"] = 1
    return schema


def _parse_prompt_file(path: Path) -> tuple[Prompt, dict[str, str]]:
    """`prompt_log.render()`가 쓴 파일을 `Prompt`로 되읽는다."""
    text = path.read_text(encoding="utf-8")
    header = _HEADER_RE.search(text)
    if header is None:
        raise SystemExit(f"{path}: 하네스 프롬프트 헤더가 없다. 이 파일이 맞나")
    try:
        _, rest = text.split(PREFIX_MARK, 1)
        prefix, body = rest.split(BODY_MARK, 1)
    except ValueError as exc:
        raise SystemExit(f"{path}: prefix/body 경계를 못 찾았다") from exc
    prompt = Prompt(
        prefix=prefix.strip("\n"),
        body=body.strip("\n"),
        prompt_version=header.group("version"),
    )
    return prompt, {"role": header.group("role"), "stage": header.group("stage")}


def _facts_evidence_ids(body: str) -> list[str]:
    """사실 블록에 실려 간 `evidence` ID들. 환각 대조의 기준집합이다."""
    fences = _JSON_FENCE.findall(body)
    if not fences:
        raise SystemExit("body에 ```json 펜스가 없다 — 사실 블록을 못 찾았다")
    facts = json.loads(fences[-1])
    return [item["evidence_id"] for item in facts.get("evidence", [])]


@dataclass
class ArmResult:
    arm: str
    trials: int = 0
    errors: list[str] = field(default_factory=list)
    # 필드별: 키가 나온 횟수 / 비어 있지 않은 횟수 / 원소 총수
    present: Counter = field(default_factory=Counter)
    nonempty: Counter = field(default_factory=Counter)
    items: Counter = field(default_factory=Counter)
    # `evidence_refs` 중 사실 블록에 **없는** ID (v1 규칙 위반)
    unknown_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "trials": self.trials,
            "ok": self.trials - len(self.errors),
            "errors": self.errors,
            "fields": {
                name: {
                    "key_present": self.present[name],
                    "nonempty": self.nonempty[name],
                    "total_items": self.items[name],
                }
                for name in FIELDS
            },
            "unknown_evidence_refs": sorted(set(self.unknown_refs)),
        }


def _diagnose(client: Any, *, arm: str, prompt: Prompt, trials: int) -> list[dict[str, Any]]:
    """실패의 **성격**을 가른다 — 상한에 잘렸나, 문법이 깨졌나. 처방이 다르다.

    ⚠ **왜 어댑터의 예외를 안 읽나.** `SchemaParseError`가 들고 오는 `usage`는 재시도의
    **합계**다. 합계에서 "잘렸다"를 추론하는 것이 CLAUDE.md §2.2가 기록한 사고 그대로다 —
    *"a₁ + a₂ = 8,402이고 두 항이 다 8,192 이하라는 데서 나오는 것은 a₁ ≥ 210뿐"*.
    응답 **하나**를 봐야 답이 나온다.

    그래서 어댑터가 만드는 payload를 그대로 쓰되(`_request_payload`) **재시도 없이** 한 번
    보내고 원문을 남긴다. 보내는 것이 같으므로 위 팔에서 깨진 것과 같은 요청이다.
    """
    schema = _schema_for(arm)
    shim = type(
        "Finding", (Finding,), {"model_json_schema": classmethod(lambda cls, *a, **k: schema)}
    )
    payload = client._request_payload(prompt.prefix, prompt.body, shim)  # noqa: SLF001
    out: list[dict[str, Any]] = []
    for _ in range(trials):
        try:
            posted = client._post(payload)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            out.append({"error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        # **이름으로 읽는다.** 튜플을 `raw, usage = …`로 풀면 필드가 하나 늘 때 이 자리가 전
        # 호출에서 `ValueError`를 맞고 위 `except`로 떨어져 GPU가 쓴 생성을 `{"error": …}`로
        # 버린다 — #550 작업 중 테스트로 재현(`tests/test_probe.py`), 실서빙 관측 아님.
        #
        # **`content`는 `None`일 수 있다**(`PostedResponse`). 아래 `len(raw)`가 `TypeError`로 죽으면
        # 이 함수 밖(`main`)까지 뚫고 나가 이미 쓴 진단이 전부 버려진다 — 위 `except`와 같은 모양의
        # `error` 행으로 남기고 계속 간다. 서버가 준 사유·토큰은 문구에 싣는다.
        if posted.content is None:
            tokens = posted.usage.output_tokens if posted.output_tokens_reported else "미보고"
            out.append(
                {
                    "error": f"content가 null이다 (finish_reason={posted.finish_reason}, "
                    f"output_tokens={tokens})"
                }
            )
            continue
        raw, usage = posted.content, posted.usage
        # **서버가 `completion_tokens`를 안 실었으면 `usage.output_tokens`의 0은 잰 값이 아니다**
        # (CLAUDE.md §0) — `None`으로 남기고 `hit_cap`도 지어내지 않는다.
        reported = posted.output_tokens_reported
        record: dict[str, Any] = {
            "max_tokens_sent": payload.get("max_tokens"),
            "output_tokens": usage.output_tokens if reported else None,
            # **서버가 말한 종료 사유다**(`length`·`stop`·`abort` …, 못 읽으면 `null`).
            # 아래 `hit_cap`은 토큰 수를 상한과 비교한 추정이라 상한과 같은 값에서의 `stop`과
            # `length`를 못 가른다.
            "finish_reason": posted.finish_reason,
            # **상한에 닿았나.** 이 한 응답의 값이라 합계 함정이 없다.
            "hit_cap": usage.output_tokens >= int(payload.get("max_tokens") or 0)
            if reported
            else None,
            "chars": len(raw),
            # 꼬리가 진단이다 — 잘린 JSON은 중간에서 끊기고, 공백 폭주(D46 · #79)는
            # 꼬리가 공백뿐이다.
            "trailing_whitespace": len(raw) - len(raw.rstrip()),
            "tail": raw[-200:],
        }
        try:
            json.loads(raw)
            record["json_valid"] = True
        except json.JSONDecodeError as exc:
            record |= {"json_valid": False, "json_error": str(exc)}
        out.append(record)
    return out


def _run_arm(client: Any, *, arm: str, prompt: Prompt, trials: int, known: set[str]) -> ArmResult:
    result = ArmResult(arm=arm)
    schema = _schema_for(arm)
    # `complete()`는 Pydantic 클래스를 받아 `model_json_schema()`를 부른다. 팔마다
    # 다른 스키마를 실으려면 그 메서드만 갈아끼운 얇은 클래스를 넘긴다 — 파싱은
    # 그대로 `Finding`이 한다(응답 검증 경로를 팔 사이에서 안 바꾼다).
    shim = type(
        "Finding",  # 서버에 가는 json_schema.name을 stock과 같게 둔다
        (Finding,),
        {"model_json_schema": classmethod(lambda cls, *a, **k: schema)},
    )
    for _ in range(trials):
        result.trials += 1
        try:
            model_result = client.complete(role=Role.AUDITOR, prompt=prompt, schema=shim)
        except Exception as exc:  # noqa: BLE001 - 실패도 관측이다. 삼키지 않고 센다
            result.errors.append(f"{type(exc).__name__}: {exc}")
            continue
        value = model_result.value
        raw = value.model_dump(mode="json")
        for name in FIELDS:
            if name in raw:
                result.present[name] += 1
            got = getattr(value, name, []) or []
            if got:
                result.nonempty[name] += 1
            result.items[name] += len(got)
        for ref in value.evidence_refs or []:
            if ref not in known:
                result.unknown_refs.append(ref)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D67 — 스키마 required가 채움을 가르는가")
    parser.add_argument("--run-dir", type=Path, required=True, help="보존된 run 디렉터리")
    parser.add_argument("--profile", default="harness_pool")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument(
        "--prompt-index", type=int, default=0, help="prompts/의 몇 번째 auditor 파일"
    )
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    parser.add_argument("--out", type=Path, default=Path("d67_probe.json"))
    parser.add_argument(
        "--diagnose",
        nargs="*",
        default=None,
        choices=ARMS,
        help="이 팔들의 응답 원문을 재시도 없이 한 번씩 더 받아 실패 성격을 가른다. "
        "인자 없이 주면 --arms 전부",
    )
    args = parser.parse_args(argv)

    prompts_dir = args.run_dir / "prompts"
    if not prompts_dir.is_dir():
        raise SystemExit(
            f"{prompts_dir}가 없다. 이 run은 프롬프트를 안 남겼다 — 재구성으로 넘어가지 "
            "않는다(모듈 docstring). 프롬프트가 남은 run을 가리키거나, 이 실험은 "
            "「원문 재생」이 아니라 「재구성」임을 보고서에 적고 따로 짠다"
        )
    # `_retryK`는 1차가 아니므로 뺀다 — 재시도 본문은 서버가 고쳐 보낸 것이라 원문이 아니다.
    files = sorted(p for p in prompts_dir.glob("*_auditor.md") if "_retry" not in p.name)
    if not files:
        raise SystemExit(f"{prompts_dir}에 auditor 1차 프롬프트가 없다")
    if not 0 <= args.prompt_index < len(files):
        raise SystemExit(f"--prompt-index는 0..{len(files) - 1} 범위다 (파일 {len(files)}개)")
    path = files[args.prompt_index]

    prompt, meta = _parse_prompt_file(path)
    known = set(_facts_evidence_ids(prompt.body))

    config = load_config(args.profile)
    model_key = config.profile.roles["auditor"]
    # **`role`은 키워드 필수다** (PR #185) — 출력 상한이 역할별로 갈릴 수 있으므로
    # 기본값을 두면 역할을 빠뜨린 조립 지점이 조용히 전역값을 실어 보낸다. 이 프로브는
    # auditor 프롬프트를 재생하므로 그 역할의 상한으로 보내야 재생이 성립한다.
    # 이 줄이 없던 동안 이 스크립트는 `TypeError`로 죽었다.
    client = _model_client(model_key, config, role=Role.AUDITOR.value)

    print(f"[프롬프트] {path}  (version={prompt.prompt_version} stage={meta['stage']})")
    print(f"[모델]     {model_key} @ {config.models[model_key].endpoint}")
    print(f"[사실블록] evidence {len(known)}건 — {sorted(known) if known else '없음'}")
    if not known:
        print("  ⚠ 사실 블록의 evidence가 비었다. v1은 '없는 ID를 만들지 않는다'이므로")
        print("    이 프롬프트에서 evidence_refs가 비는 것은 지시대로다 — 다른 순번을 고른다")
    print()

    results = [
        _run_arm(client, arm=arm, prompt=prompt, trials=args.trials, known=known)
        for arm in args.arms
    ]

    print(f"{'팔':<10} {'성공':>5} {'path 채움':>10} {'refs 채움':>10} {'미상 ID':>8}")
    for r in results:
        ok = r.trials - len(r.errors)
        print(
            f"{r.arm:<10} {ok:>3}/{r.trials:<2} "
            f"{r.nonempty['path']:>7}/{ok:<2} "
            f"{r.nonempty['evidence_refs']:>7}/{ok:<2} "
            f"{len(set(r.unknown_refs)):>8}"
        )
        for err in r.errors[:3]:
            print(f"    ! {err[:120]}")

    diagnostics: dict[str, list[dict[str, Any]]] = {}
    if args.diagnose is not None:
        targets = args.diagnose or args.arms
        print("\n[진단] 재시도 없이 한 번씩 더 보낸다 — 잘렸나 문법이 깨졌나")
        for arm in targets:
            rows = _diagnose(client, arm=arm, prompt=prompt, trials=args.trials)
            diagnostics[arm] = rows
            for i, row in enumerate(rows):
                if "error" in row:
                    print(f"  {arm}[{i}] ! {row['error'][:110]}")
                    continue
                print(
                    f"  {arm}[{i}] json={row['json_valid']} "
                    f"out_tok={row['output_tokens']}/{row['max_tokens_sent']} "
                    f"cap={row['hit_cap']} finish={row['finish_reason']} "
                    f"ws_tail={row['trailing_whitespace']}"
                )
                if not row["json_valid"]:
                    print(f"      err : {row.get('json_error', '')[:100]}")
                    print(f"      tail: {row['tail'][-90:]!r}")

    payload = {
        "probe": "d67_required_schema",
        "issue": 172,
        "prompt_file": str(path),
        "prompt_version": prompt.prompt_version,
        "prompt_sha256_source": "원문 재생 — run_dir의 1차 프롬프트를 파싱했다",
        "facts_evidence_ids": sorted(known),
        "profile": args.profile,
        "model_key": model_key,
        "endpoint": config.models[model_key].endpoint,
        "trials_per_arm": args.trials,
        "arms": [r.as_dict() for r in results],
        "diagnostics": diagnostics,
    }
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"\n[기록] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
