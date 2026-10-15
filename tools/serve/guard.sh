#!/usr/bin/env bash
# 교대 감시자 — 장비 무관. tools/serve/swap.sh가 띄운 서버($SERVE_DIR/servers의 프로세스 그룹)만 내린다.
# **run마다 하나** 띄운다(swap.sh는 감시자가 없거나 다른 run 것이면 띄우지 않는다):
#
#   setsid nohup bash tools/serve/guard.sh > /dev/null 2>&1 < /dev/null &
#
# 뜨지 못하면(이미 떠 있음 · nvidia-smi · GUARD_CGROUP_DIR · GUARD_CGROUP_PCT · MAX_HOURS · GUARD_MAX_S) 사유를 stderr와
# $SERVE_DIR/logs/guard.log의 「시작 거부」 줄에 남기고 exit 2다 — 위 기동 줄은 stderr를 버린다. 거부는 pid 파일·
# STOP_GUARD를 건드리기 전이다.
#
# 감시자 하나는 run 하나만 지킨다 — 그 run이 서버를 띄우기 직전마다 $SERVE_DIR/guard.owner에 자기를 적고, run 끝
# 내림이 그 run 서버를 다 내리면 STOP_GUARD로 이 감시자를 멈춘다(swap.sh 머리 주석). 떠 있는 감시자가 있으면
# (멈추는 중이 아니면) 새로 뜨지 않는다. pid 파일이 다른 감시자로 바뀌면 조용히 끝난다.
# pid 파일($SERVE_DIR/guard.pid)은 "PID STARTTIME"이다 — STARTTIME은 /proc/<pid>/stat 22번 필드(SERVERS 행과 같은
# 값). 「떠 있다」는 그 번호가 살아 있고 시작 시각까지 같다는 것이다 — 감시자가 정리 없이 죽으면(SIGKILL·OOM·
# 하드리셋) 파일이 남고, 그 번호를 무관한 프로세스가 물려받을 수 있다. 시작 시각이 없는 옛 형식(번호만)은 감시자가
# 아닌 것으로 본다(교대가 거부된다) — 감시자는 run마다 새로 띄우므로 run 사이에 배포하면 걸리지 않는다. pid 파일은
# 자기 것일 때만 지운다 — 끝나는 옛 감시자가 그 사이 뜬 새 감시자의 파일을 지우지 않게. ⚠ 「자기 것인가」와
# 「지운다」 사이의 틈은 남는다(잠금 없이는 못 닫는다). 걸리면 새 감시자가 조용히 끝나고 교대가 「감시자가 안 떠
# 있다」로 거부된다 — 안전한 쪽이다.
#
# 내리는 조건 — 걸리면 TERM → GUARD_TERM_WAIT_S(20 s) 뒤 KILL하고 $SERVE_DIR/KILLED에 사유를 남긴다.
#   1) 마감 — **선택, 기본 꺼짐**(2026-10-15 사용자 결정; 10시간 마감이 10-12 run을 8단계 중간에 끊었다).
#      MAX_HOURS가 양의 정수이면 감시자 시작 후 그 시간에 **지키는 run의 서버만** 내린다. 미지정·0이면 이 층이
#      없다(시작 로그 `deadline=none`). **위험: 마감이 없으면 로컬 run이 run 끝 내림 없이 죽었을 때(PC 종료 ·
#      Ctrl-C 뒤 내림 실패 등) 원격 서버가 GPU를 계속 잡는다 → 사람이 `HARNESS_RUN_ID=<run> bash
#      tools/serve/swap.sh`로 내리고 vast 인스턴스를 지운다.** 다른 run의 서버는(마감을 켰다면) 그 run의 감시자 몫이다.
#   2) 통합메모리(nvidia-smi 메모리 `[N/A]` — GB10 ATOM)에서만: MemAvailable < MIN_AVAIL_GIB — **swap.sh가 띄운
#      서버를 run과 무관하게 전부** 내린다. 장비 보호다 — vLLM의 GPU 할당이 장비 전체 MemAvailable을 깎아,
#      누구의 서버든 떠 있으면 장비 전체가 굶는다. swap.sh의 기동 전 여유 판정과 같은 하한이다.
#      전용 VRAM(vast.ai 등)에서는 MemAvailable로 내리지 않는다 — vLLM이 util만큼 미리 잡아 VRAM이 차 있는 것이
#      정상이고, 호스트 RAM은 GPU 할당과 따로다.
#   3) cgroup(판정은 장비 무관): 상한이 걸린 cgroup의 anon > 상한 × GUARD_CGROUP_PCT% — 2)처럼 run과 무관하게
#      전부 내린다. GUARD_CGROUP_PCT는 1~100 정수(기본 90)이고 아니면 뜨지 않는다. cgroup 상한은 닿는 순간 예고 없이
#      OOM kill이고(ATOM은 memory.high가 max다 — docs/portability.md §6) 공용 계정이면 팀 합산이다. 옛 감시자
#      (2026-09-24~28 스모크)의 「anon > 72 GiB(= 80 GiB × 0.9)」 층이다. 보는 것은 호스트 쪽 메모리다(vLLM의 GPU
#      할당은 cgroup에 안 잡힌다 — docs/experiments/README.md 확정 #4, ATOM).
#      상한은 말단이 아니라 조상에 걸려 있다(확정 #4 — 말단은 max) — 시작 디렉터리에서 위로 올라가며 상한이 양의
#      숫자인 첫 cgroup을 본다. 0은 상한으로 치지 않는다(치면 첫 폴링에 전부 내린다). **자동 탐색은 cgroup v2만이다**
#      — 감시자 자신의 cgroup(/proc/self/cgroup의 `0::` 줄)에서 시작하고, 상한이 경로에 없으면(2026-09-02의
#      ssh.service) 이 층을 건너뛴다 — 우리 프로세스는 그 상한에 안 잡힌다. v1(memory.limit_in_bytes · total_rss)이거나
#      다른 cgroup을 보려면 GUARD_CGROUP_DIR로 시작 디렉터리를 준다 — 거기서 위로 상한을 못 찾으면 뜨지 않는다.
#      ⚠ v1은 가짜 트리로만 확인했다(실제 v1 커널에서 미검증). 무엇을 보는지는 시작 로그의 `cgroup=`이 적는다
#      (건너뛰면 `없음`). ⚠ 가장 가까운 상한 하나만 본다 — 그 위 조상에 더 빡빡한 상한이 있으면(예: 말단 100 GiB ·
#      부모 80 GiB) 부모 쪽 넘침을 못 본다(미탐). 상한을 여럿 보면 탐색이 커진다 — 설계 한계로 둔다.
#   어느 쪽이든 SERVERS에 없는 프로세스는 건드리지 않고, 시작 시각이 다른 PGID(남이 물려받은 번호)도 건너뛴다.
# 30 s마다 자기 서버의 생존·여유 메모리·cgroup anon(`cg_anon=` — 내리는 임계 `cg_kill_at=`, 3) 층이 없으면 `-`,
# 못 읽으면 `?`)을 $SERVE_DIR/logs/guard.log에 적는다. 시작 뒤 cgroup을 못 읽게 되면 경고를 한 번 적는다(다시
# 읽히면 풀린다) — 그동안 3) 층은 멈춘다.
# 멈춤: touch $SERVE_DIR/STOP_GUARD (죽이지 않고 감시만 끝낸다).
# ⚠ STOP_GUARD는 두 뜻이다 — run 끝 내림이 감시자를 멈추는 신호이자 사람의 멈춤 스위치다. run 도중 사람이 만들면
#   감시자가 멈추고, 그 run의 다음 기동은 swap.sh가 「STOP_GUARD가 있다」로 거부해 run이 `model_swap_failed`로
#   멈춘다. 그때까지 떠 있던 서버는 감시 없이 남는다 — 그 단계가 끝나 다음 교대가 거부되고 run 끝 내림(감시자
#   없이도 된다)이 내릴 때까지. 죽이지는 않는다. 새 감시자는 뜨면서 STOP_GUARD를 지운다.
#
# ⚠ run 끝 내림(swap.sh 인자 없음)이 실패해도 러너는 run 결과를 바꾸지 않고 경고로만 남긴다 — 그때는 감시자도
#   안 멈추므로(서버가 남았다) 남은 서버를 (마감을 켰다면) 이 감시자의 마감이나 사람의 `HARNESS_RUN_ID=<run>
#   bash tools/serve/swap.sh`가 치운다 — 마감이 기본 꺼짐이라 보통은 사람이다.
#
# GUARD_MAX_S·GUARD_POLL_S·GUARD_TERM_WAIT_S는 초 단위 덮어쓰기다 — 테스트가 마감을 초로 줄이는 자리이다.
# GUARD_MAX_S가 0이거나 없으면 MAX_HOURS를 따른다. MAX_HOURS·GUARD_MAX_S는 0 이상 정수이고 아니면 뜨지
# 않는다(9자리 이하). 운영 기본값은 마감 없음 · 5 s · 20 s다.
#
# -e는 쓰지 않는다 — 긴 감시 루프라 일시적 실패(이미 사라진 그룹에 kill, /proc 읽기)로 감시자가 죽으면 서버가
# 감시 없이 남는다. 실패를 봐야 하는 자리는 명시적으로 검사한다.
set -uo pipefail
D="${SERVE_DIR:-$HOME/harness-serve}"
SERVERS="$D/servers" # 한 줄에 "KEY PGID RUN_ID STARTTIME"
LOG="$D/logs/guard.log"
MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"
CG_PCT="${GUARD_CGROUP_PCT:-90}" # 옛 72 GiB ÷ 80 GiB — 확정 #4의 「max의 0.9」
MAX_HOURS="${MAX_HOURS:-0}" # 0 = 마감 없음(머리 주석 1)
GUARD_MAX_S="${GUARD_MAX_S:-0}"
POLL_S="${GUARD_POLL_S:-5}"
TERM_WAIT_S="${GUARD_TERM_WAIT_S:-20}"
mkdir -p "$D/logs"
touch "$SERVERS"

live() { # 그 PID가 살아 있다 — 좀비는 죽은 것이다(`kill -0`은 좀비에도 성공한다)
  local state
  state=$(sed 's/.*) //' "/proc/$1/stat" 2> /dev/null | cut -d' ' -f1)
  [ -n "$state" ] && [ "$state" != Z ]
}

starttime() { sed 's/.*) //' "/proc/$1/stat" 2> /dev/null | awk '{print $20}'; }

refuse() { # 뜨지 않는다 — pid 파일·STOP_GUARD를 건드리기 전에만 부른다. 기동 줄이 stderr를 버려 로그에도 남긴다
  echo "$(date -Is) guard $$ 시작 거부 — $1" >> "$LOG"
  echo "$1" >&2
  exit 2
}

cg_read() { # $1 cgroup 디렉터리 → "상한 anon"(바이트). 상한이 양의 숫자가 아니거나 못 읽으면 실패
  local limit anon
  limit=$(cat "$1/memory.max" 2> /dev/null || cat "$1/memory.limit_in_bytes" 2> /dev/null) || return 1
  case "$limit" in '' | 0 | *[!0-9]*) return 1 ;; esac # v2 `max` = 제한 없음 · 0은 상한이 아니다(머리 주석 3)
  [ "$limit" -lt 4611686018427387904 ] || return 1  # v1의 「제한 없음」은 2^63 근처다(9223372036854771712)
  anon=$(awk '$1 == "anon" || $1 == "total_rss" {v = $2} END {print v}' "$1/memory.stat" 2> /dev/null)
  case "$anon" in '' | *[!0-9]*) return 1 ;; esac
  echo "$limit $anon"
}

cg_find() { # $1 cgroup 디렉터리 → 거기서 위로 올라가며 상한이 양의 숫자인 첫 cgroup. cgroup 밖에 닿으면 실패
  local dir=${1%/}
  while [ -e "$dir/cgroup.procs" ]; do # 모든 cgroup(v1·v2)에 있다 — memory 컨트롤러가 없는 말단도 지나간다
    if cg_read "$dir" > /dev/null; then
      echo "$dir"
      return 0
    fi
    [ "$dir" != "${dir%/*}" ] || return 1
    dir=${dir%/*}
  done
  return 1
}

case "$CG_PCT" in # 1~100 정수만 — 소수·문자는 셸 산술에서 감시자가 죽고, 0은 첫 폴링에 전부 내린다
  [1-9] | [1-9][0-9] | 100) ;;
  *) refuse "GUARD_CGROUP_PCT=$CG_PCT — 1~100 정수여야 한다" ;;
esac
for name in MAX_HOURS GUARD_MAX_S; do # 0 이상 정수만 — 문자는 셸 산술에서 감시자가 죽는다
  case "${!name}" in # 9자리 상한: 64비트를 넘기면 마감이 조용히 꺼지거나 date가 범위 밖이다
    '' | *[!0-9]* | ??????????*) refuse "$name=${!name} — 9자리 이하의 0 이상 정수여야 한다(0 = 마감 없음)" ;;
  esac
done
MAX_S=$((10#$GUARD_MAX_S)) # 10#: 앞의 0을 8진수로 읽지 않는다
[ "$MAX_S" -gt 0 ] || MAX_S=$((10#$MAX_HOURS * 3600))
DEADLINE=""
[ "$MAX_S" -le 0 ] || DEADLINE=$(($(date +%s) + MAX_S))
# 이미 떠 있는 감시자 — 번호가 살아 있고 시작 시각까지 같아야 한다(머리 주석).
if [ -f "$D/guard.pid" ]; then
  read -r old_pid old_start < "$D/guard.pid" || true
  if [ -n "${old_pid:-}" ] && [ -n "${old_start:-}" ] && live "$old_pid" &&
    [ "$(starttime "$old_pid")" = "$old_start" ] && [ ! -e "$D/STOP_GUARD" ]; then
    refuse "감시자 $old_pid가 이미 떠 있다 — 그 run이 끝나면 멈춘다(또는 touch $D/STOP_GUARD)"
  fi
fi
if ! total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits); then
  refuse "nvidia-smi로 GPU 메모리를 못 읽었다 — 메모리 판정을 못 가르므로 뜨지 않는다"
fi
case "$(echo "$total" | awk -F', *' 'NR == 1 {print $1}')" in
  '' | *[!0-9]*) UNIFIED=1 ;;
  *) UNIFIED=0 ;;
esac
if [ -n "${GUARD_CGROUP_DIR:-}" ]; then
  CG_DIR=$(cg_find "$GUARD_CGROUP_DIR") ||
    refuse "GUARD_CGROUP_DIR=$GUARD_CGROUP_DIR에서 위로 상한(memory.max·memory.limit_in_bytes)이 양의 숫자인 cgroup을 못 찾았다 — 보라고 한 층을 건너뛰지 않고 뜨지 않는다"
else # 자기 cgroup에서 — 상한이 경로에 없으면 이 층을 건너뛴다(머리 주석 3)
  CG_DIR=$(cg_find "/sys/fs/cgroup$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup 2> /dev/null)") || CG_DIR=""
fi
rm -f "$D/STOP_GUARD" "$D/KILLED"
echo "$$ $(starttime $$)" > "$D/guard.pid"

mine() { [ "$(awk 'NR == 1 {print $1}' "$D/guard.pid" 2> /dev/null)" = "$$" ]; }

owner_run() { # 이 감시자가 지키는 run(swap.sh가 적는다). 없으면 빈 문자열
  local p r
  [ -f "$D/guard.owner" ] && read -r p r < "$D/guard.owner" && [ "$p" = "$$" ] && echo "$r"
}

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

deadline_at=none
[ -z "$DEADLINE" ] || deadline_at=$(date -d @"$DEADLINE" -Is)
echo "$(date -Is) guard $$ start unified=$UNIFIED min_avail=${MIN_AVAIL_GIB}GiB cgroup=${CG_DIR:-없음} cgroup_pct=$CG_PCT deadline=$deadline_at" >> "$LOG"
EVERY=$((30 / POLL_S))
[ "$EVERY" -ge 1 ] || EVERY=1
n=0
cg_warned=0
while :; do
  if ! mine; then
    echo "$(date -Is) guard $$ 다른 감시자가 대신했다 — 끝낸다" >> "$LOG"
    exit 0
  fi
  if [ -f "$D/STOP_GUARD" ]; then
    echo "$(date -Is) guard $$ stop requested" >> "$LOG"
    mine && rm -f "$D/guard.pid"
    exit 0
  fi
  avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  cg="" # "상한 anon" — 3) 층이 이번 폴링에 본 값
  if [ -n "$CG_DIR" ]; then
    if cg=$(cg_read "$CG_DIR"); then
      cg_warned=0
    elif [ "$cg_warned" = 0 ]; then # 한 번만 — 다시 읽히면 풀린다
      echo "$(date -Is) guard $$ ⚠ cgroup $CG_DIR를 못 읽는다 — 다시 읽힐 때까지 cgroup 층이 멈춘다" >> "$LOG"
      cg_warned=1
    fi
  fi
  if [ $((n % EVERY)) -eq 0 ]; then
    up=""
    while read -r key pg _ start; do
      [ -n "${pg:-}" ] && group_ok "$pg" "$start" && kill -0 -- "-$pg" 2> /dev/null && up="$up $key"
    done < "$SERVERS"
    left=none # 마감이 없으면 none, 있으면 남은 초
    [ -z "$DEADLINE" ] || left=$((DEADLINE - $(date +%s)))s
    awk -v t="$(date -Is)" -v a="$avail_kb" -v c="$cg" -v d="$CG_DIR" -v p="$CG_PCT" -v u="$up" \
      -v o="$(owner_run)" -v l="$left" 'BEGIN {
        split(c, x, " ") # cg_anon: 3) 층이 없으면 -, 못 읽으면 ?
        g = c != "" ? sprintf("%.1fGiB cg_kill_at=%.1fGiB", x[2] / 1073741824, x[1] / 100 * p / 1073741824) : (d == "" ? "-" : "?")
        printf "%s avail=%.1fGiB cg_anon=%s run=%s up=[%s ] deadline_left=%s\n", t, a / 1048576, g, o, u, l
      }' >> "$LOG"
  fi
  if [ "$UNIFIED" = 1 ] && [ "$avail_kb" -lt $((MIN_AVAIL_GIB * 1048576)) ]; then
    kill_rows "MemAvailable<${MIN_AVAIL_GIB}GiB" ""
    exit 0
  fi
  if [ -n "$cg" ]; then
    read -r cg_limit cg_anon <<< "$cg"
    if [ "$cg_anon" -gt $((cg_limit / 100 * CG_PCT)) ]; then
      kill_rows "cgroup_anon>${CG_PCT}%" "" # 2)와 같이 전부 — 상한은 run이 아니라 cgroup 단위다
      exit 0
    fi
  fi
  if [ -n "$DEADLINE" ] && [ "$(date +%s)" -gt "$DEADLINE" ]; then
    run=$(owner_run)
    if [ -n "$run" ]; then
      kill_rows "deadline_${MAX_S}s" "$run"
    else
      echo "$(date -Is) guard $$ 마감 — 지키던 run이 없어 아무것도 안 내린다" | tee -a "$LOG" > "$D/KILLED"
      mine && rm -f "$D/guard.pid"
    fi
    exit 0
  fi
  n=$((n + 1))
  sleep "$POLL_S"
done
