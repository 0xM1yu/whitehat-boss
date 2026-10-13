"""Critic 출력 상한 실측 — `run_20260912_0001`의 Finding에서 7단계 프롬프트를 **재구성**해 잰다.

`config/budgets.yaml`의 `output_max` 주석이 이 측정을 약속해 뒀다 — *"절단이 실제로 나는지는
`expanded` 캡슐 실측이 답한다. 어느 역할에 얼마를 주는지는 그 실측이 정한다."* 그 실측이
이것이고, **캡슐 부분은 틀렸다** (아래 「프롬프트는 작다」).

## 재생이 아니라 재구성이다 — 그리고 그 차이가 결과의 성질을 정한다

`run_20260912_0001`의 `prompts/`에 **critic 프롬프트가 0건**이다. Critic이 한 번도 안 불렸기
때문이고, 원인은 D67(#172)이었다(2026-09-12 run 기준 — 서빙은 6단계 부착으로 근거 블록이 안 빈다):
Finding 120건 전부가 `evidence_refs=[]`이라
`gate.evaluate`가 첫 줄에서 E0을 반환하고(`gate.py` *"if not finding.evidence_refs"*)
E2 미만은 7단계에 못 간다.

그래서 `d67_required_schema.py`와 **반대 경로**다. 저쪽은 남은 프롬프트 파일을 한 글자도
안 바꾸고 재생하고, 이쪽은 저장된 Finding에서 다시 만든다. 그 대가를 산출물에 라벨로 박는다
(`labels` 필드):

- `reconstructed: true` — 그때 간 요청이 아니라 **지금 코드로 다시 만든 것**이다.
- `gate_bypassed: true` — 게이트가 막은 건을 손으로 넘겼다.
- `evidence_refs_empty: true` — **근거 블록이 비어 있다.** 2026-09-12 run의 재구성에서는 D67이 살아
  있었다 —
  서빙은 6단계 부착으로 근거 블록이 안 빈다. 이 재구성에서는 이것이 실제 상태이지만,
  근거가 붙으면 프롬프트는 이것보다 **커진다**. 그러므로 이 측정이 내는
  프롬프트 크기는 **하한**이다.
- `run_id: null` — 하네스 run이 아니므로 조건 비교표 밖이다(evaluation.md §6).

## 프롬프트는 작다 — 21,021은 이 경로의 수가 아니다

**이 probe의 metadata-only 재구성 한정이다**(`_reconstruct` — v1 입력 모양):
프롬프트 판은 registry에서 읽어 기록하지만 v6의 SOURCE·근거 본문·위치·Auditor 홉 관측(#600)은
재구성하지 않는다.
그 판의 실 입력 토큰으로 읽지 않는다. SOURCE 자리표시자가 있으면 빈 문자열로 채운다.
`build_facts`에 넘기는 인자는 `run_id`·`ids`·`evidences` **셋뿐**이다(content-aware 판은
`evidence_bodies`·`evidence_locations`를 더 넘긴다 — 이 probe의 경로가 아니다). `capsule=`도
`source=`도 `target=`도 안 넘긴다. 그래서 Critic의 사실 블록은
`capsule_sections: []` · `source: ""` · `target: null`이고, **대상 원문도 캡슐도 안 실린다**
(agent-contracts.md §2.3이 `rca_draft`를 빼는 것과 같은 설계다 — Critic은 앞 단계의 서술이
아니라 주장·경로·근거만 받는다).

가변부는 넷이다: `CLAIM`(title+cwe) · `PATH`(경로 단계 한 줄씩) · `EVIDENCE`(근거 한 줄씩) ·
`FACTS`(근거 id/kind/ok). 고정부는 prefix 하나다.

**그러므로 2026-09-12 ATOM 프로브의 21,021 토큰은 이 경로의 수가 아니다** — 그쪽은
`bench.sized_prompt()`의 filler가 만든 합성 프롬프트다. 거기서 역산한
「하드 천장 11,747」(= 32,768 − 21,021)도 같이 재유도 대상이고, **이 스크립트의 1단계가 그
답을 낸다**(모델 호출 0회).

## 왜 팔이 하나인가 — 반사실 팔을 안 만든다

`evidence_refs`가 전부 비었으므로 재구성한 120건은 `title`과 경로 길이만 다르다. 꼬리가
안 생긴다. "D67이 고쳐졌다면"을 흉내내 같은 `artifact_id`의 Evidence를 붙이는 팔을 검토했고
**기각했다**:

1. **출력 길이를 움직이는 축이 아니다.** 같은 합성 프롬프트에서 스키마만 바꿨을 때
   최대 출력이 2,956 → 10,471로 3.5배 움직였다. 근거 줄 몇 개가 그 급으로 움직이지 않는다.
2. **붙이는 규칙을 아무도 안 정했다.** 지어낸 규칙이 낳은 수는 단서가 떨어져 나간 뒤에도
   살아남는다 — CLAUDE.md §2.2가 기록한 사고가 정확히 그 모양이고, 이슈 #157이 그 결함
   클래스다.

**진짜 꼬리는 근거 블록이 붙은 입력(서빙 6단계 부착 — D67은 2026-10-12 닫았다)으로 다시 돌려야
나온다.** 그때는 재구성이 아니라 실측이다.

## 공백 폭주를 상한 표본에서 뺀다

이슈 #79가 이 저장소에서 이미 잡은 결함이다 — 구조화 출력이 공백으로 상한까지 차오른다.
그 응답의 `completion_tokens`를 상한 근거로 쓰면 **버그를 config에 적는 것**이고, 그 뒤로
모든 폭주가 그 값에 비례해 비싸진다(폭주 차단이 상한의 유일한 목적인데 정반대로 간다).
그래서 응답마다 비공백 문자 수를 세고, 상한에 닿았는데 절반 넘게 공백인 응답은
`whitespace_runaway`로 **따로 센다**(`summary.cap_sample`에서 빠진다).

## 쓰는 법

1단계만 — 모델을 안 부른다. 재구성이 맞는지, 프롬프트가 실제로 얼마인지 먼저 본다:

    python tools/probe/critic_output_budget.py \
        --state-db /srv/tenants/user/artifacts/run_20260912_0001/state.db \
        --profile harness_pool --model-key gpt-oss-20b --dry-run \
        --out ~/probe_results/critic_budget_dryrun.json

2단계 — 두 질문이 `--max-tokens`로 갈린다:

    # (a) 오늘의 상한이 실제 프롬프트에서 절단을 내는가
    ... --schema draft --max-tokens 4096  --trials 3
    # (b) 안 자르면 분포가 어디까지 가는가
    ... --schema draft --max-tokens 24000 --trials 3

`--schema draft`를 **먼저** 돌린다 — #182가 켜는 쪽이고 꼬리가 거기 있다. `decision`도
같이 재서 **높은 쪽**으로 눈금한다(저장소가 오늘 돌리는 것은 `decision`이다).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.adapters.factory import _model_client  # noqa: E402
from src.adapters.sqlite_store import SqliteStateStore  # noqa: E402
from src.bench import check_startup  # noqa: E402
from src.config import load_config  # noqa: E402
from src.interfaces.model_client import Prompt  # noqa: E402
from src.orchestrator import gate  # noqa: E402
from src.orchestrator.nodes.critic import (  # noqa: E402
    _describe_claim,
    _describe_evidence,
    _describe_path,
)
from src.orchestrator.prompt_loader import PromptTemplate, build_facts, load_prompt  # noqa: E402
from src.schemas.decision import Decision, DecisionDraft  # noqa: E402
from src.schemas.enums import Role  # noqa: E402
from src.schemas.evidence import Evidence  # noqa: E402
from src.schemas.finding import Finding  # noqa: E402
from src.tokens import estimate_tokens  # noqa: E402

ROLE = Role.CRITIC

# 서버 상한 판정에 얹는 여유. 추정 입력이 실측보다 작으므로(D21: `len//4`가 실서빙보다
# ~1.8배 과소) 추정만으로 부등식을 걸면 400을 맞는 요청이 통과한다.
_CEILING_MARGIN_TOKENS = 2048


def _schema_for(name: str) -> type:
    """`--schema`가 고르는 구조화 출력 스키마.

    **`draft`가 실서빙이다** — `nodes/critic.OUTPUT_SCHEMA`가 `DecisionDraft`이고
    `test_the_probe_draft_schema_is_the_one_the_live_critic_node_sends`가 그 동일성을
    건다. `decision`은 필드가 넷 더 많은 **상한 팔**이다(`DecisionDraft` ⊂ `Decision`).

    2026-09-12 보고서 §3.3·§3.4는 `decision`으로 쟀다 — 그때 `DecisionDraft`가 PR #182
    브랜치에만 있었기 때문이고, #182가 머지된 지금은 `draft`가 실서빙과 맞는 팔이다.
    """
    return Decision if name == "decision" else DecisionDraft


@dataclass(frozen=True)
class Reconstructed:
    """한 Finding에서 되만든 Critic 프롬프트와 그 크기."""

    finding_id: str
    prompt: Prompt
    evidence_count: int
    path_steps: int
    chars: int
    est_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "evidence_count": self.evidence_count,
            "path_steps": self.path_steps,
            "chars": self.chars,
            "est_tokens": self.est_tokens,
        }


def _reconstruct(
    finding: Finding, evidences: list[Evidence], *, template: PromptTemplate, run_id: str
) -> Reconstructed:
    """v1 입력 모양의 metadata-only 조립. 프롬프트 판은 호출자가 고른 대로 기록한다.

    content-aware 버전은 `evidence_summaries`로 근거 본문을 더 실으므로 이 되만듦과
    달라진다 — 이 프로브는 본문 없는 조립의 토큰 예산을 재는 자리다. SOURCE를 받는
    판의 원문 블록도 빈 문자열이며 현재 실 입력을 재구성했다는 뜻이 아니다.

    `decision_id`를 하네스가 발급한 값이 아니라 자리표시자로 채운다 — 그 run은 Critic을
    안 불렀으므로 발급된 적이 없다. 길이에 미치는 영향이 상수 한 줄이라 분포를 안 움직인다.
    """
    own = gate.evidences_for(finding, evidences)
    prompt = template.render(
        facts=build_facts(
            run_id=run_id,
            ids={"finding_id": finding.finding_id, "decision_id": "dec_probe"},
            evidences=own,
        ),
        claim=_describe_claim(finding),
        path=_describe_path(finding),
        evidence=_describe_evidence(own),
        **({"source": ""} if "{{SOURCE}}" in template.body else {}),
    )
    return Reconstructed(
        finding_id=finding.finding_id,
        prompt=prompt,
        evidence_count=len(own),
        path_steps=len(finding.path),
        chars=len(prompt.prefix) + len(prompt.body),
        est_tokens=estimate_tokens(prompt.prefix) + estimate_tokens(prompt.body),
    )


def _distribution(values: list[int]) -> dict[str, Any]:
    """분포 요약. **표본이 비면 0이 아니라 `null`이다** — 0은 미실측 표시이지 값이 아니다."""
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None, "mean": None}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "mean": round(statistics.fmean(values), 1),
    }


def _measure_one(client: Any, payload: dict, max_tokens: int) -> dict[str, Any]:
    """응답 **하나**를 재시도 없이 받아 기록한다.

    ⚠ **어댑터의 `complete()`를 안 쓴다.** 그쪽이 들고 오는 `usage`는 재시도의 **합계**라
    "잘렸나"를 합계에서 추론하게 되고, 그것이 CLAUDE.md §2.2가 기록한 사고 그대로다 —
    *"a₁ + a₂ = 8,402이고 두 항이 다 8,192 이하라는 데서 나오는 것은 a₁ ≥ 210뿐"*.
    꼬리가 이 결정의 전부이므로 재시도가 그것을 삼키면 안 된다.
    """
    try:
        posted = client._post(payload)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - 실패도 관측이다. 삼키지 않고 센다
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}
    # **이름으로 읽는다.** `_post`가 튜플일 때 `raw, usage = …`로 풀면 필드가 하나 늘 때 전 호출에서
    # `ValueError`를 맞고 위 `except`로 떨어져 GPU가 쓴 생성을 버린다 — #550 작업 중 테스트로
    # 재현(`tests/test_probe.py`), 실서빙 관측 아님.
    #
    # **`content`는 `None`일 수 있다**(`PostedResponse`). 아래 `.rstrip()`이 `AttributeError`로
    # 죽으면 `main`이 행을 루프 뒤에야 쓰므로 그때까지 쓴 생성이 전부 버려진다 — 위 `except`와
    # 같은 모양의 `error` 행으로 남기고 계속 간다. 서버가 준 사유·토큰은 문구에 싣는다(GPU가
    # 이미 쓴 값이다).
    if posted.content is None:
        tokens = posted.usage.output_tokens if posted.output_tokens_reported else "미보고"
        return {
            "error": f"content가 null이다 (finish_reason={posted.finish_reason}, "
            f"output_tokens={tokens})"
        }
    raw, usage = posted.content, posted.usage
    # **서버가 `completion_tokens`를 안 실었으면 `usage.output_tokens`의 0은 잰 값이 아니다**
    # (CLAUDE.md §0). 그 0이 `hit_cap`과 `cap_sample`로 가서 `output_max`를 정하는 분포에 들어가면
    # 이 프로브가 재려던 값을 지어낸 0이 채운다 — `None`으로 남기고 `_summarize`가 표본에서 뺀다.
    reported = posted.output_tokens_reported
    stripped = raw.rstrip()
    record: dict[str, Any] = {
        # **서버가 센 입력이다.** `est_tokens`(len//4)와 나란히 남겨 실제 Critic
        # 프롬프트에서 그 추정의 오차를 처음으로 잰다(D21과 같은 축).
        "input_tokens": usage.input_tokens,
        "cached_tokens": usage.cached_tokens,
        "output_tokens": usage.output_tokens if reported else None,
        "max_tokens_sent": max_tokens,
        # **서버가 말한 종료 사유다**(`length`·`stop`·`abort` …, 못 읽으면 `null`). 아래 `hit_cap`은
        # 토큰 수를 상한과 비교한 **추정**이라 상한과 같은 값에서 난 `stop`과 `length`를 못
        # 가른다. 이 필드가 그 둘을 가른다 — 이 프로브가 상한을 정하려고 재는 물음이 바로 그것이다.
        "finish_reason": posted.finish_reason,
        "hit_cap": usage.output_tokens >= max_tokens if reported else None,
        "chars": len(raw),
        # 꼬리가 공백뿐이면 #79의 공백 폭주다. 그 응답의 출력 토큰을 상한 근거로 쓰면
        # 버그를 config에 적는 것이라 `summary.cap_sample`에서 뺀다.
        "trailing_whitespace": len(raw) - len(stripped),
        "nonspace_chars": len("".join(raw.split())),
        "tail": raw[-200:],
    }
    try:
        json.loads(raw)
        record["json_valid"] = True
    except json.JSONDecodeError as exc:
        record |= {"json_valid": False, "json_error": str(exc)[:200]}
    return record


def _is_whitespace_runaway(row: dict[str, Any]) -> bool:
    """상한까지 갔는데 그 대부분이 공백인가 (#79).

    **둘을 함께 본다.** 상한에 안 닿은 응답의 꼬리 공백은 서식이지 폭주가 아니고,
    상한에 닿았어도 내용이 차 있으면 그것은 진짜 긴 출력이다.
    """
    return bool(row.get("hit_cap")) and row.get("trailing_whitespace", 0) > row["chars"] // 2


def _load_findings(state_db: Path, run_id: str | None) -> tuple[list[Finding], list[Evidence], str]:
    """state.db에서 Finding·Evidence만 읽는다.

    **캡슐은 안 읽는다.** `SqliteStateStore._load`가 `model_validate`로 되돌리므로
    `using_counter` 밖에서 캡슐을 조회하면 `CapsuleSection._tokens_match_body`가
    실 토크나이저 프로필에서 터진다(#84, `src/tokens.py`의 정정 블록). Critic 프롬프트가
    캡슐을 안 싣는 덕에 여기서는 읽을 이유도 없다.
    """
    store = SqliteStateStore(state_db)
    findings: list[Finding] = store.query("finding")  # type: ignore[assignment]
    evidences: list[Evidence] = store.query("evidence")  # type: ignore[assignment]
    if run_id:
        findings = [f for f in findings if f.run_id == run_id]
        evidences = [e for e in evidences if e.run_id == run_id]
    if not findings:
        raise SystemExit(f"{state_db}에 Finding이 없다 (run_id={run_id})")
    seen = {f.run_id for f in findings}
    if len(seen) > 1:
        raise SystemExit(f"run이 여럿이다({sorted(seen)}) — --run-id로 하나를 고른다")
    return findings, evidences, findings[0].run_id


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Critic 출력 상한 — 재구성 프롬프트로 잰다")
    parser.add_argument("--state-db", type=Path, required=True, help="보존된 run의 state.db")
    parser.add_argument("--profile", default="harness_pool")
    parser.add_argument(
        "--model-key",
        default=None,
        help="프로필의 critic 대신 이 registry 키로 잰다. 배선이 아직 커밋 안 된 경우",
    )
    parser.add_argument("--run-id", default=None, help="state.db에 run이 여럿일 때")
    parser.add_argument("--schema", choices=("draft", "decision"), default="draft")
    parser.add_argument("--top", type=int, default=20, help="프롬프트가 큰 상위 N건만 보낸다")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="요청에 실을 출력 상한. 기본은 config의 역할별 값(= 오늘의 상한)",
    )
    parser.add_argument(
        "--server-limit",
        type=int,
        default=None,
        help="서버의 max_model_len. 기본은 /v1/models에서 읽는다",
    )
    parser.add_argument("--dry-run", action="store_true", help="1단계만 — 모델을 안 부른다")
    parser.add_argument("--out", type=Path, default=Path("critic_output_budget.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.state_db.is_file():
        raise SystemExit(f"{args.state_db}가 없다")

    config = load_config(args.profile)
    model_key = args.model_key or config.profile.roles[ROLE.value]
    if model_key not in config.models:
        raise SystemExit(f"{model_key!r}가 registry에 없다. 있는 것: {sorted(config.models)}")
    entry = config.models[model_key]
    template = load_prompt(ROLE, entry.prompt_template_version)

    # --- 1단계: 재구성 (모델 호출 0회) -----------------------------------
    findings, evidences, source_run_id = _load_findings(args.state_db, args.run_id)
    built = [_reconstruct(f, evidences, template=template, run_id=source_run_id) for f in findings]
    built.sort(key=lambda r: (-r.chars, r.finding_id))
    grounded = sum(1 for r in built if r.evidence_count)

    print(f"[출처]     {args.state_db}  run_id={source_run_id}")
    print(f"[모델]     {model_key} @ {entry.endpoint}  prompt={ROLE.value}/{template.version}")
    print("[입력 범위] metadata-only 재구성: SOURCE·근거 본문·위치는 빠져 있다")
    print(f"[Finding]  {len(built)}건 · Evidence {len(evidences)}건")
    print(
        f"[근거 붙은 Finding] {grounded}/{len(built)}  ← 0이면 근거 블록이 빈 재구성이다"
        f"(2026-09-12 run에서는 D67이 살아 있었다)"
    )
    print(f"[프롬프트 추정토큰] {_distribution([r.est_tokens for r in built])}")
    print(f"[프롬프트 글자]     {_distribution([r.chars for r in built])}")

    selected = built[: args.top]
    payload: dict[str, Any] = {
        "probe": "critic_output_budget",
        "issue": 79,
        "labels": {
            "reconstructed": True,
            "gate_bypassed": True,
            "adapter_retry_bypassed": True,
            "evidence_refs_empty": grounded == 0,
            "run_id": None,
            "note": (
                "하네스 run이 아니다 — 조건 비교표 밖이다(evaluation.md §6). "
                "근거 블록이 비어 있으면 프롬프트 크기는 하한이다(D67/#172)."
                " SOURCE·근거 본문·위치는 재구성하지 않아 content-aware 실 입력이 아니다."
            ),
        },
        "source_state_db": str(args.state_db),
        "source_run_id": source_run_id,
        "profile": args.profile,
        "model_key": model_key,
        "endpoint": entry.endpoint,
        "prompt_version": template.version,
        "reasoning_effort": entry.reasoning_effort,
        "schema": args.schema,
        "findings_total": len(built),
        "findings_with_evidence": grounded,
        "prompt_est_tokens": _distribution([r.est_tokens for r in built]),
        "prompt_chars": _distribution([r.chars for r in built]),
        "reconstructed": [r.as_dict() for r in built],
        "selected": [r.finding_id for r in selected],
    }

    if args.dry_run:
        payload["dry_run"] = True
        _write(args.out, payload)
        return 0

    # --- 2단계: 실측 -----------------------------------------------------
    schema = _schema_for(args.schema)
    client = _model_client(model_key, config, role=ROLE.value)
    max_tokens = args.max_tokens or config.thresholds.output_max_for(ROLE.value)
    client.max_tokens = max_tokens

    server_limit = args.server_limit
    if server_limit is None:
        startup = check_startup(entry.endpoint, model_key)
        server_limit = startup.get("max_model_len")  # type: ignore[assignment]
        if not startup.get("ok"):
            print(f"  ⚠ /v1/models: {startup.get('reason')}")
    print(f"[상한]     max_tokens={max_tokens} · server max_model_len={server_limit}")

    rows: list[dict[str, Any]] = []
    for item in selected:
        # **보낼 수 없는 것은 보내지 않는다.** vLLM은 `prompt_tokens + max_tokens >
        # max_model_len`이면 절단이 아니라 400을 낸다(`bench.measure_memory`의 #126 가드와
        # 같은 부등식). 그것을 "재려다 실패"로 집계하면 D72가 적은 세 번째 부류
        # ——「우리가 못 재게 만든 것」——이 다시 「못 잼」에 흡수된다.
        need = item.est_tokens + _CEILING_MARGIN_TOKENS + max_tokens
        if server_limit is not None and need > server_limit:
            rows.append(
                {
                    "finding_id": item.finding_id,
                    "misconfigured": (
                        f"추정 입력 {item.est_tokens} + 여유 {_CEILING_MARGIN_TOKENS} + "
                        f"max_tokens {max_tokens} = {need} > max_model_len {server_limit} — "
                        "보내면 전건 400이다. --max-tokens를 내린다"
                    ),
                }
            )
            continue
        request = client._request_payload(item.prompt.prefix, item.prompt.body, schema)  # noqa: SLF001
        for trial in range(args.trials):
            row = _measure_one(client, request, max_tokens) | {
                "finding_id": item.finding_id,
                "trial": trial,
                "est_tokens": item.est_tokens,
            }
            rows.append(row)
            if "error" in row:
                print(f"  {item.finding_id}[{trial}] ! {row['error'][:100]}")
                continue
            print(
                f"  {item.finding_id}[{trial}] json={row['json_valid']} "
                f"in={row['input_tokens']}(est {item.est_tokens}) "
                f"out={row['output_tokens']}/{max_tokens} cap={row['hit_cap']} "
                f"finish={row['finish_reason']} ws_tail={row['trailing_whitespace']}"
            )

    payload |= {
        "max_tokens_sent": max_tokens,
        "server_max_model_len": server_limit,
        "trials_per_finding": args.trials,
        "calls": rows,
        "summary": _summarize(rows),
    }
    s = payload["summary"]
    print(
        f"\n[요약] ok {s['ok']}/{s['calls']} · json {s['json_valid']} · "
        f"상한도달 {s['hit_cap']} · 공백폭주 {s['whitespace_runaway']} · "
        f"토큰미보고 {s['output_tokens_unreported']}"
    )
    print(f"[상한 표본] {s['cap_sample']}   ← 공백 폭주를 뺀 출력 분포")
    print(f"[입력 실측] {s['input_tokens_measured']} · 추정 대비 {s['est_over_measured']}배")
    _write(args.out, payload)
    return 0


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """상한을 정하는 표본과 그 밖의 것을 **가른다**.

    `whitespace_runaway`를 `cap_sample`에서 빼는 것이 이 함수의 전부다 — 그 값으로 상한을
    정하면 #79를 config에 적는 것이고, 그 뒤 모든 폭주가 그 값에 비례해 비싸진다.
    """
    ok = [r for r in rows if "error" not in r and "misconfigured" not in r]
    # 서버가 출력 토큰을 안 실은 행(`output_tokens is None`)은 분포에 못 들어간다 — 0으로 안 센다.
    measured = [r for r in ok if r["output_tokens"] is not None]
    cap_sample = [r for r in measured if not _is_whitespace_runaway(r)]
    ratios = [r["input_tokens"] / r["est_tokens"] for r in ok if r["est_tokens"]]
    return {
        "calls": len(rows),
        "ok": len(ok),
        "errors": sum(1 for r in rows if "error" in r),
        "misconfigured": sum(1 for r in rows if "misconfigured" in r),
        "json_valid": sum(1 for r in ok if r.get("json_valid")),
        "output_tokens_unreported": len(ok) - len(measured),
        "hit_cap": sum(1 for r in ok if r["hit_cap"]),
        "whitespace_runaway": sum(1 for r in ok if _is_whitespace_runaway(r)),
        "cap_sample": _distribution([r["output_tokens"] for r in cap_sample]),
        "input_tokens_measured": _distribution([r["input_tokens"] for r in ok]),
        # 실서빙이 센 입력 ÷ `len//4` 추정. 1보다 크면 추정이 과소다(D21).
        "est_over_measured": round(statistics.fmean(ratios), 3) if ratios else None,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"[기록] {path}")


if __name__ == "__main__":
    raise SystemExit(main())
