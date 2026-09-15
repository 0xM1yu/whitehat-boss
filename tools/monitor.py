"""run 산출물을 밖에서 읽어 상태·플래그를 내는 stdlib 폴러 (이슈 #208 ③④).

`heartbeat.json`(살아있음)·`trace.jsonl`(진행·에러)·`run_manifest.json`(완주)을 읽어
`{alive, stuck, error_storm, budget_exhausted, incomplete}`를 도출한다. **run에 붙지 않고
파일만 본다.** 죽이지 않는다 — 가시화·경보만 한다(kill은 `_TIMEOUT_S`·차단기 소관).

**임계값은 추측 상수가 아니라 코드 상수에서 온다(§0).**

- **stuck 하드 상한 T = (schema_retries+1) × `_TIMEOUT_S`.** 어댑터가 한 호출을 그만큼까지
  재시도·타임아웃하므로, 그 시간을 넘긴 침묵은 정상 호출로 설명되지 않는다 = 확정 wedge.
  `schema_retries`는 그 run의 `run_manifest.json`에 남은 값을 쓰고(그 run에 실제 적용된 값),
  없으면 config 기본을 쓴다. 역할별 조기경보(healthy 꼬리에서 뽑는 더 짧은 T)는 baseline
  실측이 선행이라 여기 넣지 않는다 — 이 하드 상한은 오경보 0으로 지금 방어된다.
- **error_storm K = `_MAX_CONSECUTIVE_DROPS`.** 차단기가 같은 실패 K회 연속에 내부에서
  멈추는 그 수를, 밖에서도 같은 기준으로 읽는다.

`derive_status`와 그 보조는 **순수**하다 — 임계값을 인자로 받아 파일·시계·상수에 안 매인다.
상수는 `main`이 늦게 import한다(폴러를 안 켜도 순수 함수는 가볍게 테스트된다).
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_FAIL_KINDS = ("target_skipped", "critic_failed")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path: Path) -> dict | None:
    """없거나 반쯤 쓰인 파일은 None. 폴러는 산출물이 아직 없거나 갱신 중일 때도 돈다."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_trace(path: Path) -> list[dict]:
    """trace.jsonl을 읽어 이벤트 목록으로. 마지막 줄이 반쯤 쓰였으면 버린다."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
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


def _error_storm(events: list[dict], k: int) -> bool:
    """마지막 K개의 실패 이벤트가 **같은 사유**면 storm. K개 미만이면 아니다."""
    if k <= 0:
        return False
    fails = [e for e in events if e.get("kind") in _FAIL_KINDS]
    if len(fails) < k:
        return False
    reasons = [(e.get("data") or {}).get("reason") for e in fails[-k:]]
    return reasons[0] is not None and all(r == reasons[0] for r in reasons)


def _completion(manifest: dict | None, events: list[dict]) -> tuple[bool, bool]:
    """(incomplete, budget_exhausted). 매니페스트가 정본이고, budget 사유는 trace에만 있다."""
    if manifest is None:
        incomplete = True  # 매니페스트 부재 = 아직 안 끝났거나 크래시
    else:
        finished = manifest.get("finished_at") is not None
        stopped = manifest.get("stopped_after") is not None
        incomplete = (not finished) or stopped  # RunRecord.is_complete의 정의
    budget = any(
        e.get("subject_kind") == "run" and (e.get("data") or {}).get("reason") == "budget_exhausted"
        for e in events
    )
    return incomplete, budget


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

    last_ts = _parse_ts(events[-1]["ts"]) if events else hb_ts
    event_age = (now - last_ts).total_seconds() if last_ts else None
    # stuck: 살아있는데(heartbeat 틱) trace가 하드 상한을 넘겨 침묵 = 확정 wedge.
    stuck = bool(alive and event_age is not None and event_age >= stuck_after_s)

    incomplete, budget = _completion(manifest, events)
    return {
        "alive": alive,
        "stuck": stuck,
        "error_storm": _error_storm(events, error_storm_k),
        "budget_exhausted": budget,
        "incomplete": incomplete,
        "current_stage": heartbeat.get("current_stage") if heartbeat else None,
        "last_event_seq": events[-1]["seq"]
        if events
        else (heartbeat or {}).get("last_event_seq"),
        "heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
        "event_age_s": round(event_age, 1) if event_age is not None else None,
        "stuck_after_s": stuck_after_s,
    }


def stuck_ceiling_s(
    manifest: dict | None, timeout_s: float, default_retries: int
) -> tuple[float, str]:
    """(T, 근거 문자열). schema_retries는 그 run의 매니페스트 값을 먼저 쓴다(§0 출처 명기)."""
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
        f"incomplete={_flag(status, 'incomplete', 'O')}"
    )


def snapshot(run_dir: Path, *, now: datetime, stuck_after_s: float, error_storm_k: int) -> dict:
    """run_dir의 세 파일을 읽어 상태를 낸다."""
    return derive_status(
        heartbeat=load_json(run_dir / "heartbeat.json"),
        events=read_trace(run_dir / "trace.jsonl"),
        manifest=load_json(run_dir / "run_manifest.json"),
        now=now,
        stuck_after_s=stuck_after_s,
        error_storm_k=error_storm_k,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run 산출물 밖에서 상태·플래그를 읽는다 (이슈 #208).")
    ap.add_argument("run_dir", type=Path, help="산출물 디렉터리 (heartbeat.json 등이 있는 곳)")
    ap.add_argument("--interval", type=float, default=5.0, help="폴 주기(초). --once면 무시")
    ap.add_argument("--once", action="store_true", help="한 번만 읽고 끝낸다")
    ap.add_argument("--status-file", action="store_true", help="run_dir/status.json에도 쓴다")
    args = ap.parse_args(argv)

    # 상수는 여기서 늦게 import한다 — 순수 함수 테스트를 무겁게 하지 않기 위해.
    from src.adapters.openai_compat import _TIMEOUT_S
    from src.config import load_config
    from src.orchestrator.runner import _MAX_CONSECUTIVE_DROPS

    default_retries = int(load_config("dev_fake").thresholds.schema_retries)
    manifest0 = load_json(args.run_dir / "run_manifest.json")
    stuck_after_s, basis = stuck_ceiling_s(manifest0, _TIMEOUT_S, default_retries)
    print(f"# stuck 상한 T={stuck_after_s:g}s = {basis} · error_storm K={_MAX_CONSECUTIVE_DROPS}")

    while True:
        status = snapshot(
            args.run_dir,
            now=datetime.now(UTC),
            stuck_after_s=stuck_after_s,
            error_storm_k=_MAX_CONSECUTIVE_DROPS,
        )
        print(format_report(args.run_dir, status), flush=True)
        if args.status_file:
            (args.run_dir / "status.json").write_text(
                json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if args.once or not status["incomplete"]:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
