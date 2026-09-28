#!/usr/bin/env bash
# 교대 감시자 — 장비 무관. tools/serve/swap.sh가 띄운 서버($SERVE_DIR/servers의 프로세스 그룹)만 내린다.
# **run마다 하나** 띄운다(swap.sh는 감시자가 없거나 다른 run 것이면 띄우지 않는다):
#
#   MAX_HOURS=12 setsid nohup bash tools/serve/guard.sh > /dev/null 2>&1 < /dev/null &
#
# 감시자 하나는 run 하나만 지킨다 — 그 run이 처음 띄울 때 $SERVE_DIR/guard.owner에 자기를 적고, run 끝
# 내림이 그 run 서버를 다 내리면 STOP_GUARD로 이 감시자를 멈춘다(swap.sh 머리 주석). 떠 있는 감시자가 있으면
# (멈추는 중이 아니면) 새로 뜨지 않는다. pid 파일이 다른 감시자로 바뀌면 조용히 끝난다.
#
# 내리는 조건 — 걸리면 TERM → GUARD_TERM_WAIT_S(20 s) 뒤 KILL하고 $SERVE_DIR/KILLED에 사유를 남긴다.
#   1) 마감: 감시자 시작 후 MAX_HOURS 시간 — **지키는 run의 서버만** 내린다. run 끝 내림이 안 돌았어도
#      서버가 장비를 붙잡지 않게 하는 자리이고, 다른 run의 서버는 그 run의 감시자 몫이다.
#   2) 통합메모리(nvidia-smi 메모리 `[N/A]` — GB10 ATOM)에서만: MemAvailable < MIN_AVAIL_GIB — **swap.sh가 띄운
#      서버를 run과 무관하게 전부** 내린다. 장비 보호다 — vLLM의 GPU 할당이 장비 전체 MemAvailable을 깎아,
#      누구의 서버든 떠 있으면 장비 전체가 굶는다. swap.sh의 기동 전 여유 판정과 같은 하한이다.
#      전용 VRAM(vast.ai 등)에서는 메모리로 내리지 않는다 — vLLM이 util만큼 미리 잡아 VRAM이 차 있는 것이
#      정상이고, 호스트 RAM은 GPU 할당과 따로다.
#   어느 쪽이든 SERVERS에 없는 프로세스는 건드리지 않고, 시작 시각이 다른 PGID(남이 물려받은 번호)도 건너뛴다.
# 30 s마다 자기 서버의 생존과 여유 메모리를 $SERVE_DIR/logs/guard.log에 적는다.
# 멈춤: touch $SERVE_DIR/STOP_GUARD (죽이지 않고 감시만 끝낸다).
#
# ⚠ run 끝 내림(swap.sh 인자 없음)이 실패해도 러너는 run 결과를 바꾸지 않고 경고로만 남긴다 — 그때는 감시자도
#   안 멈추므로(서버가 남았다) 남은 서버를 이 감시자의 마감이나 사람의 `HARNESS_RUN_ID=<run> bash
#   tools/serve/swap.sh`가 치운다.
#
# GUARD_MAX_S·GUARD_POLL_S·GUARD_TERM_WAIT_S는 초 단위 덮어쓰기다 — 테스트가 마감을 초로 줄이는 자리이고
# 운영 기본값(MAX_HOURS · 5 s · 20 s)은 그대로다.
#
# -e는 쓰지 않는다 — 긴 감시 루프라 일시적 실패(이미 사라진 그룹에 kill, /proc 읽기)로 감시자가 죽으면 서버가
# 감시 없이 남는다. 실패를 봐야 하는 자리는 명시적으로 검사한다.
set -uo pipefail
D="${SERVE_DIR:-$HOME/harness-serve}"
SERVERS="$D/servers" # 한 줄에 "KEY PGID RUN_ID STARTTIME"
LOG="$D/logs/guard.log"
MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"
MAX_HOURS="${MAX_HOURS:-12}"
MAX_S="${GUARD_MAX_S:-$((MAX_HOURS * 3600))}"
POLL_S="${GUARD_POLL_S:-5}"
TERM_WAIT_S="${GUARD_TERM_WAIT_S:-20}"
DEADLINE=$(($(date +%s) + MAX_S))
mkdir -p "$D/logs"
touch "$SERVERS"

live() { # 그 PID가 살아 있다 — 좀비는 죽은 것이다(`kill -0`은 좀비에도 성공한다)
  local state
  state=$(sed 's/.*) //' "/proc/$1/stat" 2> /dev/null | cut -d' ' -f1)
  [ -n "$state" ] && [ "$state" != Z ]
}

if [ -f "$D/guard.pid" ] && live "$(cat "$D/guard.pid")" && [ ! -e "$D/STOP_GUARD" ]; then
  echo "감시자 $(cat "$D/guard.pid")가 이미 떠 있다 — 그 run이 끝나면 멈춘다(또는 touch $D/STOP_GUARD)" >&2
  exit 2
fi
if ! total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits); then
  echo "nvidia-smi로 GPU 메모리를 못 읽었다 — 메모리 판정을 못 가르므로 뜨지 않는다" >&2
  exit 2
fi
case "$(echo "$total" | awk -F', *' 'NR == 1 {print $1}')" in
  '' | *[!0-9]*) UNIFIED=1 ;;
  *) UNIFIED=0 ;;
esac
rm -f "$D/STOP_GUARD" "$D/KILLED"
echo $$ > "$D/guard.pid"

mine() { [ "$(cat "$D/guard.pid" 2> /dev/null)" = "$$" ]; }

owner_run() { # 이 감시자가 지키는 run(swap.sh가 적는다). 없으면 빈 문자열
  local p r
  [ -f "$D/guard.owner" ] && read -r p r < "$D/guard.owner" && [ "$p" = "$$" ] && echo "$r"
}

starttime() { sed 's/.*) //' "/proc/$1/stat" 2> /dev/null | awk '{print $20}'; }

group_ok() { [ ! -e "/proc/$1/stat" ] || [ "$(starttime "$1")" = "${2:-}" ]; }

kill_rows() { # $1 사유 $2 run(빈 문자열이면 전부) — TERM → 대기 → KILL
  local reason=$1 run=$2 rows key pg r start
  echo "$(date -Is) KILL reason=$reason run=${run:-*}" | tee -a "$LOG" > "$D/KILLED"
  rows=$(awk -v r="$run" 'r == "" || $3 == r' "$SERVERS")
  while read -r key pg r start; do
    [ -n "${pg:-}" ] && group_ok "$pg" "$start" && kill -TERM -- "-$pg" 2> /dev/null &&
      echo "$(date -Is) TERM $key -$pg $r" >> "$LOG"
  done <<< "$rows"
  sleep "$TERM_WAIT_S"
  while read -r key pg r start; do
    [ -n "${pg:-}" ] && group_ok "$pg" "$start" && kill -KILL -- "-$pg" 2> /dev/null &&
      echo "$(date -Is) KILL $key -$pg $r" >> "$LOG"
  done <<< "$rows"
  mine && rm -f "$D/guard.pid"
}

echo "$(date -Is) guard $$ start unified=$UNIFIED min_avail=${MIN_AVAIL_GIB}GiB deadline=$(date -d @$DEADLINE -Is)" >> "$LOG"
EVERY=$((30 / POLL_S))
[ "$EVERY" -ge 1 ] || EVERY=1
n=0
while :; do
  if ! mine; then
    echo "$(date -Is) guard $$ 다른 감시자가 대신했다 — 끝낸다" >> "$LOG"
    exit 0
  fi
  if [ -f "$D/STOP_GUARD" ]; then
    echo "$(date -Is) guard $$ stop requested" >> "$LOG"
    rm -f "$D/guard.pid"
    exit 0
  fi
  avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  if [ $((n % EVERY)) -eq 0 ]; then
    up=""
    while read -r key pg _ start; do
      [ -n "${pg:-}" ] && group_ok "$pg" "$start" && kill -0 -- "-$pg" 2> /dev/null && up="$up $key"
    done < "$SERVERS"
    awk -v t="$(date -Is)" -v a="$avail_kb" -v u="$up" -v o="$(owner_run)" \
      'BEGIN {printf "%s avail=%.1fGiB run=%s up=[%s ]\n", t, a / 1048576, o, u}' >> "$LOG"
  fi
  if [ "$UNIFIED" = 1 ] && [ "$avail_kb" -lt $((MIN_AVAIL_GIB * 1048576)) ]; then
    kill_rows "MemAvailable<${MIN_AVAIL_GIB}GiB" ""
    exit 0
  fi
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    run=$(owner_run)
    if [ -n "$run" ]; then
      kill_rows "deadline_${MAX_S}s" "$run"
    else
      echo "$(date -Is) guard $$ 마감 — 지키던 run이 없어 아무것도 안 내린다" | tee -a "$LOG" > "$D/KILLED"
      rm -f "$D/guard.pid"
    fi
    exit 0
  fi
  n=$((n + 1))
  sleep "$POLL_S"
done
