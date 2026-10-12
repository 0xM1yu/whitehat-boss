"""run 산출물을 밖에서 읽어 상태·플래그를 내는 stdlib 폴러 (이슈 #208 ③④).

`heartbeat.json`(살아있음)·`trace.jsonl`(진행·에러)·`run_manifest.json`(완주)을 읽어
`{alive, stuck, error_storm, budget_exhausted, crashed, incomplete}`를 도출한다. **run에 붙지
않고 파일만 본다.** 죽이지 않는다 — 가시화·경보만 한다. kill은 요청 타임아웃(모델이 선언한
`request_timeout_s`, 선언이 없으면 `_TIMEOUT_S`)과 차단기 소관이다.

**종료 코드:** 0 — 산출물(매니페스트)이 나와 폴링을 끝냈다(예산·차단기로 **예상한** 중단 포함) ·
2 — run_dir을 못 읽는다 · 3 — **비정상 종료**: DEAD(매니페스트 없이 heartbeat가 낡았다) 또는
CRASHED(러너의 최후 방어선이 잡은 예상 밖 예외 — 매니페스트는 남는다).
⚠ `--once`는 비대칭이다 — CRASHED면 3을 내지만 DEAD면 0이다(DEAD 판정은 폴 루프 안이라 `--once`가
그 앞에서 끝난다. 기존 동작이고 바꾸지 않았다).
⚠ **이 3은 `python -m src.cli run`의 3과 다른 사건이다**(모집단이 서로소다) — `run`은 차단기·교대
실패·게이트웨이 장애·스키마 파싱 실패로 멈춘 run에 3을 내지만(#533) 여기서는 그것들이 0이고,
CRASHED는 `run`이 1(트레이스백)로 낸다. 이 도구의 코드는 `run`의 코드를 따라가지 않는다.

**임계값은 추측 상수가 아니라 코드 상수에서 온다(§0).**

- **stuck 하드 상한 T = (schema_retries+1) × 요청 타임아웃.** 요청 타임아웃은 프로필이 부르는
  모델 중 가장 긴 `request_timeout_s`이고, 선언이 없으면 `_TIMEOUT_S`다(`call_timeout_s`,
  ADR 0012 Phase 6.5).
  ⚠ 이 몫은 **지금 config**에서 읽는다 — `schema_retries`와 달리 매니페스트에 안 남기 때문이다.
  registry의 타임아웃을 바꾼 뒤 옛 run을 보면 그 run이 실제로 쓴 값과 T가 갈린다.
  어댑터가 한 호출을 그만큼까지
  재시도·타임아웃하므로, 그 시간을 넘긴 침묵은 정상 호출로 설명되지 않는다 = 확정 wedge.
  `schema_retries`는 **끝난 run**은 `run_manifest.json`의 값을 쓰고(그 run에 실제 적용된 값),
  **도는 run**은 매니페스트가 아직 없으므로 config 기본을 쓴다(폴 루프가 매니페스트가 생기면
  다시 읽어 T를 좁힌다). 역할별 조기경보(healthy 꼬리에서 뽑는 더 짧은 T)는 baseline 실측이
  선행이라 여기 넣지 않는다 — 이 하드 상한은 오경보 0으로 지금 방어된다.
- **error_storm K = `_MAX_CONSECUTIVE_DROPS`.** 차단기가 잡으려는 모양(**진행 없이 한
  자리에서 연속 실패**)을 밖에서 읽는다 — K와 역할 축(`scout_member`)은 차단기의 자리 카운터와
  맞추고, `systematic` 축 하나만 trace에 없어 근사로 남는다(크기 거절 오탐). 실제 트립은 종료 전이
  사유로 정확히 잡는다(`_error_storm` 참조).

`derive_status`와 그 보조는 **순수**하다 — 임계값을 인자로 받아 파일·시계·상수에 안 매인다.
상수는 `main`이 늦게 import한다(폴러를 안 켜도 순수 함수는 가볍게 테스트된다).
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

# 경로 실행(`python tools/monitor.py`)은 sys.path[0]이 tools/라 `src`를 못 찾는다(#616).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if TYPE_CHECKING:
    from src.config import HarnessConfig

# 실패 사유 (runner._REASON_GATEWAY·_REASON_SCHEMA와 같은 값 — test_monitor가 대조해 고정한다).
# `over_candidate_max`(runner._REASON_OVER_MAX)는 실패가 아니라 예산 절단이라 제외한다.
_FAILURE_REASONS = frozenset({"gateway_error", "schema_parse_failed"})
# 진행(리셋) 이벤트 — 하나라도 나오면 그 앞의 연속 실패는 끊긴다(차단기의 리셋 조건을 근사).
_PROGRESS_KINDS = frozenset({"target_planned", "gate_eval", "transition"})
# 차단기가 실제로 터진 사후 신호 (runner가 run 종료 전이에 남기는 사유).
_BREAKER_REASON = "consecutive_target_failures"
# 러너의 최후 방어선이 run 종료 전이에 남기는 사유(`ReasonCode.UNFORESEEN_FAILURE` — test_monitor가
# 대조해 고정한다). 그 run은 **매니페스트를 남기므로** `has_manifest`만 보면 정상 종료와 같다.
_CRASH_REASON = "unforeseen_failure"
# run 도중 예산이 걸린 라이브 신호 (budget.py의 EventKind.BUDGET event 값).
_BUDGET_EVENTS = frozenset({"exhausted", "blocked"})


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path: Path) -> dict | None:
    """없거나·반쯤 쓰였거나·갱신 중이면 None. 폴러는 산출물이 아직 없거나 라이터가 원자
    교체 중일 때도 돈다 — Windows에선 `os.replace`와 이 읽기가 겹쳐 일시 OSError가 나므로
    그것도 삼킨다."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_trace(path: Path) -> list[dict]:
    """trace.jsonl을 읽어 이벤트 목록으로. 마지막 줄이 반쯤 쓰였으면 버린다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 갱신 중 잘린 마지막 줄
    return events


def _is_failure(event: dict) -> bool:
    kind = event.get("kind")
    if kind == "critic_failed":
        return True
    if kind == "target_skipped":
        return (event.get("data") or {}).get("reason") in _FAILURE_REASONS
    return False


def _member_of(event: dict) -> str:
    """실패를 어느 카운터로 셀지. target_skipped는 `scout_member`(어느 자리가 죽었나),
    critic_failed는 자리 개념이 없어 단일 버킷으로 둔다."""
    if event.get("kind") == "critic_failed":
        return "__critic__"
    return (event.get("data") or {}).get("scout_member") or "__unknown__"


def _error_storm(events: list[dict], k: int) -> bool:
    """차단기가 잡으려는 모양 — **진행 없이 한 자리에서 연속된 실패 K개** — 을 밖에서 읽는다.

    두 갈래다. **사후·정확:** 차단기가 실제로 터진 run은 종료 전이 사유가
    `consecutive_target_failures`라 그것으로 확정한다. **라이브·근사:** 진행 이벤트
    (`target_planned`·`gate_eval`·`transition` — 차단기의 리셋 조건을 근사)가 끼기 전까지,
    **`scout_member`(자리)별로** 실패를 세어 한 자리가 K개에 닿으면 storm으로 본다. 차단기의 자리
    카운터와 같은 축이다 — 그래서 대상 하나가 풀 멤버 셋에서 각 1회씩 죽는 것(자리별 1)은 storm이
    아니다. ⚠ 차단기의 run 카운터는 자리를 안 가르므로 그 모양도 차단기는 터질 수 있고(#197)
    라이브는 그것을 미탐한다 — 종료 전이 사유가 뜬 뒤에야 참이다.

    ⚠ **차단기와 값(K)·역할 축은 맞추되, systematic 축 하나가 근사로 남는다.** `role`은
    `target_skipped.scout_member`에 실려 있어 위처럼 자리별로 세지만, `_drop`이 크기 거절
    (413과 컨텍스트 초과 문구의 400, `runner._is_size_rejection`)을 카운터에서 빼는 그 구분은
    trace에 판정으로 안 실린다 — 크기 거절도 real gateway 실패와 똑같이 `gateway_error`로만
    남는다. 그래서 **크기 거절이 한 자리에서 K회 연속되면 오탐이 난다**(차단기는 그것을 일부러
    안 센다, `_MAX_CONSECUTIVE_DROPS` 주석). 이 오탐을 없애려면 폴러가 `error_detail`에 러너와
    같은 판정을 걸거나 `_emit_skipped`가 판정을 한 필드 실어야 하고, 그건 이 PR 밖이다. 여기서는
    그 한계를 명시만 한다. 크기가 아닌 400은 러너가 첫 건에서 멈추므로(#296) 사후 판정이 잡는다.
    """
    if k <= 0:
        return False
    if any(
        e.get("subject_kind") == "run" and (e.get("data") or {}).get("reason") == _BREAKER_REASON
        for e in events
    ):
        return True
    runs: dict[str, int] = {}
    for e in reversed(events):
        if _is_failure(e):
            member = _member_of(e)
            runs[member] = runs.get(member, 0) + 1
            if runs[member] >= k:
                return True
        elif e.get("kind") in _PROGRESS_KINDS:
            break  # 진행 이벤트 = 리셋. 그 앞은 다른 국면이다.
        # 그 외(over_candidate_max·stage_start·budget·model_call 등)는 중립 — 건너뛴다.
    return False


def _completion(manifest: dict | None, events: list[dict]) -> tuple[bool, bool]:
    """(incomplete, budget_exhausted). 매니페스트가 정본이고, budget 사유는 trace에만 있다."""
    if manifest is None:
        incomplete = True  # 매니페스트 부재 = 아직 안 끝났거나 크래시
    else:
        finished = manifest.get("finished_at") is not None
        stopped = manifest.get("stopped_after") is not None
        incomplete = (not finished) or stopped  # RunRecord.is_complete의 정의
    # 라이브 신호(budget.py의 EventKind.BUDGET, event=exhausted/blocked)와 사후 신호
    # (run 종료 전이 reason=budget_exhausted)를 함께 본다 — 라이브 쪽이 run 도중에 뜨므로,
    # is_complete가 run 끝에만 채워지는 그 타이밍 문제를 이 플래그가 넘는다(#212 리뷰 L7).
    budget = any(
        (e.get("kind") == "budget" and (e.get("data") or {}).get("event") in _BUDGET_EVENTS)
        or (
            e.get("subject_kind") == "run"
            and (e.get("data") or {}).get("reason") == "budget_exhausted"
        )
        for e in events
    )
    return incomplete, budget


def _crashed(events: list[dict]) -> bool:
    """예상 밖 예외로 멈춘 run인가 — 종료 전이의 사유로 사후 확정한다(`_error_storm`의 차단기 갈래와
    같은 방식). 매니페스트가 없어도(쓰기가 실패해도) 전이는 trace에 먼저 남는다."""
    return any(
        e.get("subject_kind") == "run" and (e.get("data") or {}).get("reason") == _CRASH_REASON
        for e in events
    )


def derive_status(
    *,
    heartbeat: dict | None,
    events: list[dict],
    manifest: dict | None,
    now: datetime,
    stuck_after_s: float,
    error_storm_k: int,
    alive_grace: int = 3,
) -> dict:
    """파일 스냅샷 + 임계값 → 상태 플래그. 순수 함수."""
    hb_ts = _parse_ts(heartbeat["ts"]) if heartbeat and heartbeat.get("ts") else None
    interval = float(heartbeat.get("interval_s", 0)) if heartbeat else 0.0
    hb_age = (now - hb_ts).total_seconds() if hb_ts else None
    # 살아있음: heartbeat가 c×N 안에 갱신됐다. c는 놓친 틱 한둘을 견디는 작은 정수다.
    alive = hb_age is not None and hb_age <= max(1.0, alive_grace * interval)

    # 첫 trace 이벤트 전에는 event_age가 "모름"이다 — heartbeat로 폴백하면 그 값이 계속
    # 갱신돼 "정상"으로 읽힌다(#212 리뷰 L3). 이벤트가 0건이면 None으로 두고 stuck을 안 세운다.
    last_ts = _parse_ts(events[-1]["ts"]) if events else None
    event_age = (now - last_ts).total_seconds() if last_ts else None
    # stuck: 살아있는데(heartbeat 틱) trace가 하드 상한을 넘겨 침묵 = 확정 wedge.
    stuck = bool(alive and event_age is not None and event_age >= stuck_after_s)

    incomplete, budget = _completion(manifest, events)
    return {
        "alive": alive,
        "stuck": stuck,
        "error_storm": _error_storm(events, error_storm_k),
        "budget_exhausted": budget,
        "crashed": _crashed(events),
        "incomplete": incomplete,
        # 산출물이 나왔는가 = 폴링을 끝내도 되는가. `incomplete`(비교표용 is_complete 정의)와
        # 다른 질문이다 — 중단된 run도 매니페스트를 남기므로 여기서 끝나야 한다(#212 리뷰 N1).
        "has_manifest": manifest is not None,
        "current_stage": heartbeat.get("current_stage") if heartbeat else None,
        "last_event_seq": events[-1]["seq"]
        if events
        else (heartbeat or {}).get("last_event_seq"),
        "heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
        "event_age_s": round(event_age, 1) if event_age is not None else None,
        "stuck_after_s": stuck_after_s,
    }


def call_timeout_s(config: "HarnessConfig", *, default: float) -> float:
    """T의 한 호출 몫 — 프로필이 부르는 모델 중 가장 긴 `request_timeout_s`, 없으면 `default`."""
    declared = (config.models[key].request_timeout_s for key in config.profile.roles.values())
    return max([default, *(value for value in declared if value is not None)])


def stuck_ceiling_s(
    manifest: dict | None, timeout_s: float, default_retries: int
) -> tuple[float, str]:
    """(T, 근거 문자열). **끝난 run**은 매니페스트의 `schema_retries`를 쓰고(그 run에 실제
    적용된 값), **도는 run**은 매니페스트가 아직 없어 config 기본으로 떨어진다 — 호출자가
    매 폴에서 다시 읽어 매니페스트가 생기면 T가 좁혀진다(§0 출처 명기)."""
    retries, basis = default_retries, f"config 기본 schema_retries={default_retries}"
    if manifest:
        thresholds = manifest.get("thresholds") or {}
        if "schema_retries" in thresholds:
            retries = int(thresholds["schema_retries"])
            basis = "manifest.thresholds.schema_retries"
    return (retries + 1) * timeout_s, f"({retries}+1)×{timeout_s:g}s [{basis}]"


def _flag(status: dict, key: str, mark: str) -> str:
    return mark if status.get(key) else "-"


def format_report(run_dir: Path, status: dict) -> str:
    return (
        f"[{run_dir.name}] stage={status['current_stage']} seq={status['last_event_seq']} "
        f"alive={'O' if status['alive'] else 'X'} hb={status['heartbeat_age_s']}s "
        f"event_age={status['event_age_s']}s | "
        f"stuck={_flag(status, 'stuck', '!')} storm={_flag(status, 'error_storm', '!')} "
        f"budget={_flag(status, 'budget_exhausted', '!')} "
        f"crashed={_flag(status, 'crashed', '!')} "
        f"incomplete={_flag(status, 'incomplete', 'O')}"
    )


def snapshot(
    run_dir: Path, *, now: datetime, timeout_s: float, default_retries: int, error_storm_k: int
) -> tuple[dict, str]:
    """run_dir의 세 파일을 읽어 (상태, T 근거)를 낸다. 매니페스트를 매번 다시 읽어
    끝난 run에서는 T가 실제 적용값으로 좁혀진다(리뷰 M1)."""
    manifest = load_json(run_dir / "run_manifest.json")
    stuck_after_s, basis = stuck_ceiling_s(manifest, timeout_s, default_retries)
    status = derive_status(
        heartbeat=load_json(run_dir / "heartbeat.json"),
        events=read_trace(run_dir / "trace.jsonl"),
        manifest=manifest,
        now=now,
        stuck_after_s=stuck_after_s,
        error_storm_k=error_storm_k,
    )
    return status, basis


def _write_status_atomic(path: Path, status: dict) -> None:
    """heartbeat와 같은 관용구 — tmp→os.replace. 읽는 쪽이 반쯤 쓰인 status를 안 본다(L1)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run 산출물 밖에서 상태·플래그를 읽는다 (이슈 #208).")
    ap.add_argument("run_dir", type=Path, help="산출물 디렉터리 (heartbeat.json 등이 있는 곳)")
    ap.add_argument("--interval", type=float, default=5.0, help="폴 주기(초). --once면 무시")
    ap.add_argument("--once", action="store_true", help="한 번만 읽고 끝낸다")
    ap.add_argument("--status-file", action="store_true", help="run_dir/status.json에도 쓴다")
    ap.add_argument(
        "--profile",
        default="dev_fake",
        help="도는 run의 config 기본 schema_retries를 읽을 프로필. schema_retries는 오늘 "
        "전 프로필 공통값이라 어느 것이든 같지만, 갈리는 날을 대비해 인자로 둔다",
    )
    args = ap.parse_args(argv)

    # run_dir을 못 읽으면 여기서 보고하고 끝낸다(#212 리뷰 N6). 이게 없으면 경로 오타·권한
    # 문제가 load_json에서 "파일 없음"과 뭉쳐 폴러가 조용히 영원히 돈다. (레이스로 인한 일시
    # OSError는 load_json이 삼켜 넘긴다 — 여기서 거르는 것은 디렉터리 자체가 안 읽히는 경우다.)
    if not args.run_dir.is_dir():
        print(f"[ERROR] run_dir을 읽을 수 없다: {args.run_dir}", file=sys.stderr)
        return 2

    # 상수는 여기서 늦게 import한다 — 순수 함수 테스트를 무겁게 하지 않기 위해.
    from src.adapters.openai_compat import _TIMEOUT_S
    from src.config import load_config
    from src.orchestrator.runner import _MAX_CONSECUTIVE_DROPS

    config = load_config(args.profile)
    default_retries = int(config.thresholds.schema_retries)
    timeout_s = call_timeout_s(config, default=_TIMEOUT_S)
    print(f"# T=(schema_retries+1)×{timeout_s:g}s · error_storm K={_MAX_CONSECUTIVE_DROPS}")

    while True:
        # 매니페스트를 매 폴에서 다시 읽는다 — 끝난 run에서 T가 실제 적용값으로 좁혀진다(M1).
        status, basis = snapshot(
            args.run_dir,
            now=datetime.now(UTC),
            timeout_s=timeout_s,
            default_retries=default_retries,
            error_storm_k=_MAX_CONSECUTIVE_DROPS,
        )
        print(f"{format_report(args.run_dir, status)}  [T {basis}]", flush=True)
        if args.status_file:
            _write_status_atomic(args.run_dir / "status.json", status)
        # 러너의 최후 방어선이 잡은 run은 매니페스트를 남기지만 **정상 종료가 아니다** — 아래
        # `has_manifest` 종료보다 먼저 가른다(그렇지 않으면 예전에 DEAD로 잡히던 크래시가 exit 0이
        # 된다).
        if status["crashed"]:
            print(
                f"[{args.run_dir.name}] CRASHED - 예상 밖 예외로 멈췄다({_CRASH_REASON}). "
                "매니페스트·trace는 남았다.",
                file=sys.stderr,
            )
            return 3
        # 산출물(매니페스트)이 나오면 끝난 것 — 중단된 run도 매니페스트를 남기므로 여기서
        # 끝나야 한다(#212 리뷰 N1). `incomplete`(is_complete 정의)로 끝내면 예산 소진·차단기
        # 트립 등 stopped_after가 찬 run이 영영 안 끝나고 아래 DEAD로 오판됐다.
        if args.once or status["has_manifest"]:
            return 0
        # 크래시 탈출(L2): 매니페스트가 없는데(위에서 안 끝났으니) heartbeat가 있었다가
        # c×N을 넘어 stale = run이 조용히 죽었다. 여기 오는 것은 매니페스트가 실제로 없는
        # 경우뿐이므로 문구가 관측과 일치한다. em 대시는 cp949에서 깨져 하이픈을 쓴다(N7).
        if not status["alive"] and status["heartbeat_age_s"] is not None:
            print(
                f"[{args.run_dir.name}] DEAD - heartbeat {status['heartbeat_age_s']}s stale, "
                "매니페스트 없음. run이 조용히 죽었다.",
                file=sys.stderr,
            )
            return 3
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
