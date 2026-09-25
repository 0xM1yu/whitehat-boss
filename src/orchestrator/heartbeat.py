"""러너가 살아 있는지를 밖에서 읽게 하는 heartbeat 파일 (이슈 #208 ②).

타이머 스레드가 N초마다 `run_dir/heartbeat.json`을 갱신한다. **모델 호출 하나가
멈춰도** — 어댑터가 `(schema_retries+1)×`요청 타임아웃(모델의 `request_timeout_s`, 없으면
`_TIMEOUT_S`)초까지 침묵할 수 있다 — 이 스레드는
계속 틱하므로 "프로세스가 죽었나 / 느린 호출 중인가"를 밖에서 가른다. trace 이벤트는
단계·호출 경계에서만 나 그 사이 침묵하지만 heartbeat는 안 그친다.

`_INTERVAL_S`(N)는 판정 상수가 아니라 **갱신 해상도**다. 죽음 판정("stale")은 외부
관찰자가 heartbeat의 나이 > c×N으로 내리고, 그 c도 놓친 틱 한둘을 견디는 작은 정수지
실측값이 아니다. 그래서 파일에 `interval_s`를 실어 관찰자가 N을 코드에서 안 세도 되게 한다.
"""

import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.schemas.common import to_utc_iso

# 갱신 주기(초). 판정 임계값이 아니라 해상도 knob이다(모듈 docstring). 잦을수록 죽음을
# 빨리 보지만 그만큼 자주 쓴다 — trace처럼 핫루프가 아니라 비용은 무시할 수준이다.
_INTERVAL_S = 5.0


class Heartbeat:
    """N초마다 heartbeat.json을 원자적으로 갱신하는 데몬 스레드.

    `seq_of`·`stage_of`는 살아 있는 러너 상태를 읽는 콜러블이다(각각 trace 이벤트 수와
    현재 단계). 스레드가 매 틱 호출하므로 최신값이 실린다.
    """

    def __init__(
        self,
        path: Path,
        *,
        seq_of: Callable[[], int],
        stage_of: Callable[[], int],
        pid: int | None = None,
        clock: Callable[[], datetime] | None = None,
        interval_s: float = _INTERVAL_S,
    ) -> None:
        self._path = path
        self._seq_of = seq_of
        self._stage_of = stage_of
        self._pid = os.getpid() if pid is None else pid
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self) -> None:
        payload = {
            "ts": to_utc_iso(self._clock()),
            "pid": self._pid,
            "last_event_seq": self._seq_of(),
            "current_stage": self._stage_of(),
            "interval_s": self._interval_s,
        }
        # 형제 라이터(`PromptLog`·`write_manifest`)와 같은 관용구 — 쓰기 직전에 디렉터리를
        # 보장한다. 현 CLI 경로에선 run_dir이 이미 있지만, 이 방어가 없으면 첫 쓰기가
        # 디렉터리 부재에 걸릴 여지가 남는다.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 원자적 교체 — 갱신 중 크래시가 나도 관찰자가 반쯤 쓰인 파일을 읽지 않는다.
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def _loop(self) -> None:
        # `wait`는 stop이 서면 즉시 True로 깨어 마지막 틱까지 기다리지 않는다.
        while not self._stop.wait(self._interval_s):
            try:
                self._write()
            except OSError:
                # 최선노력 쓰기다. Windows에서 관찰자가 파일을 읽는 순간 `os.replace`가
                # 일시적으로 막힐 수 있는데, 여기서 스레드가 죽으면 heartbeat가 멈춰
                # **살아 있는 러너가 죽은 것처럼 보인다.** 이 틱만 건너뛰고 다음에 다시 쓴다.
                continue

    def start(self) -> "Heartbeat":
        # 첫 틱은 즉시 — 첫 주기를 기다리지 않는다. **`_loop`와 같은 이유로 견딘다:**
        # 첫 쓰기가 일시적 OSError(Windows 잠금·ATOM 공유 스토리지)에 걸려도 스레드는
        # 그대로 띄운다. 여기서 예외를 올리면 heartbeat 하나가 run 전체 산출물을
        # 없앤다 — `run_pipeline`의 heartbeat 켜기가 `try`(매니페스트·리포트) 밖이라
        # 첫 쓰기 예외가 그 앞을 뚫고 나가기 때문(#210 리뷰 H1). 첫 틱을 놓쳐도 다음
        # 틱이 파일을 만든다.
        try:
            self._write()
        except OSError:
            pass
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 1.0)
            self._thread = None
        try:
            self._write()  # 종료 시점의 마지막 상태를 남긴다(최선노력).
        except OSError:
            pass

    def __enter__(self) -> "Heartbeat":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
