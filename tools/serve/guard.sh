#!/usr/bin/env bash
# 교대 감시자 — 장비 무관. tools/serve/swap.sh가 띄운 서버($SERVE_DIR/servers의 프로세스 그룹)만 내린다.
# 교대를 쓰는 run 전에 먼저 띄운다(swap.sh는 감시자가 없으면 띄우지 않는다):
#
#   MAX_HOURS=12 setsid nohup bash tools/serve/guard.sh > /dev/null 2>&1 < /dev/null &
#
# 내리는 조건 — 하나라도 걸리면 전부 TERM → 20 s 뒤 KILL하고 $SERVE_DIR/KILLED에 사유를 남긴다.
#   1) 마감: 감시자 시작 후 MAX_HOURS 시간 — run 끝 내림이 안 돌았어도 서버가 장비를 붙잡지 않게.
#   2) 통합메모리(nvidia-smi 메모리 `[N/A]` — GB10 ATOM)에서만: MemAvailable < MIN_AVAIL_GIB. vLLM의 GPU
#      할당이 장비 전체 MemAvailable을 깎아, 장비 전체가 굶는 것을 막는다(swap.sh의 판정과 같은 하한).
#      전용 VRAM(vast.ai 등)에서는 메모리로 내리지 않는다 — vLLM이 util만큼 미리 잡아 VRAM이 차 있는 것이
#      정상이고, 호스트 RAM은 GPU 할당과 따로다.
# 30 s마다 자기 서버의 생존과 여유 메모리를 $SERVE_DIR/logs/guard.log에 적는다.
# 멈춤: touch $SERVE_DIR/STOP_GUARD (죽이지 않고 감시만 끝낸다).
#
# ⚠ run 끝 내림(swap.sh 인자 없음)이 실패해도 러너는 run 결과를 바꾸지 않고 경고로만 남긴다 — 그때 남은
#   서버를 치우는 것은 이 감시자의 마감이거나 사람의 `HARNESS_RUN_ID=<run> bash tools/serve/swap.sh`다.
set -u
D="${SERVE_DIR:-$HOME/harness-serve}"
SERVERS="$D/servers" # 한 줄에 "KEY PGID RUN_ID"
LOG="$D/logs/guard.log"
MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"
MAX_HOURS="${MAX_HOURS:-12}"
DEADLINE=$(($(date +%s) + MAX_HOURS * 3600))
mkdir -p "$D/logs"
touch "$SERVERS"
rm -f "$D/STOP_GUARD" "$D/KILLED"
echo $$ > "$D/guard.pid"

total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2> /dev/null | awk 'NR == 1')
case "$total" in
  '' | *[!0-9]*) UNIFIED=1 ;;
  *) UNIFIED=0 ;;
esac

kill_all() {
  echo "$(date -Is) KILL reason=$1" | tee -a "$LOG" > "$D/KILLED"
  local key pg run
  while read -r key pg run; do
    [ -n "${pg:-}" ] && kill -TERM -- "-$pg" 2> /dev/null && echo "$(date -Is) TERM $key -$pg $run" >> "$LOG"
  done < "$SERVERS"
  sleep 20
  while read -r key pg run; do
    [ -n "${pg:-}" ] && kill -KILL -- "-$pg" 2> /dev/null && echo "$(date -Is) KILL $key -$pg $run" >> "$LOG"
  done < "$SERVERS"
  rm -f "$D/guard.pid"
}

echo "$(date -Is) guard start unified=$UNIFIED min_avail=${MIN_AVAIL_GIB}GiB deadline=$(date -d @$DEADLINE -Is)" >> "$LOG"
n=0
while :; do
  avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  if [ $((n % 6)) -eq 0 ]; then
    up=""
    while read -r key pg _; do
      [ -n "${pg:-}" ] && kill -0 -- "-$pg" 2> /dev/null && up="$up $key"
    done < "$SERVERS"
    awk -v t="$(date -Is)" -v a="$avail_kb" -v u="$up" \
      'BEGIN {printf "%s avail=%.1fGiB up=[%s ]\n", t, a / 1048576, u}' >> "$LOG"
  fi
  if [ "$UNIFIED" = 1 ] && [ "$avail_kb" -lt $((MIN_AVAIL_GIB * 1048576)) ]; then
    kill_all "MemAvailable<${MIN_AVAIL_GIB}GiB"
    exit 0
  fi
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    kill_all "deadline_${MAX_HOURS}h"
    exit 0
  fi
  if [ -f "$D/STOP_GUARD" ]; then
    echo "$(date -Is) guard stop requested" >> "$LOG"
    rm -f "$D/guard.pid"
    exit 0
  fi
  n=$((n + 1))
  sleep 5
done
