#!/usr/bin/env bash
# 단계 경계 모델 교대 — 장비 무관 (ADR 0012 Phase 6.5 — ADR 0009 「2026-09-29 제안」 절).
#
# 사용: swap.sh [KEY...]
#   하네스가 단계 경계마다 부른다(장비 파일 config/serving/<device>.yaml의 `command`). 계약은
#   src/adapters/model_swap.py 모듈 docstring 「명령과의 계약」이다 — 끝나면 KEY만 떠 있다.
#   이 run(HARNESS_RUN_ID)이 띄운 서버 중 KEY에 없는 것은 프로세스 그룹째 내리고, 없는 KEY는 하나씩
#   띄운다. KEY가 없으면 이 run의 서버를 전부 내린다(run 끝 내림 — 떠 있는 것이 없으면 0으로 끝난다).
#   남의 run 서버는 건드리지 않는다. 지난 run의 고아는 `HARNESS_RUN_ID=<그 run> bash swap.sh`로 내린다.
#
# 모델 값은 이 파일에 없다 — 기동 조건(util·seqs·창·인자·환경·준비 상한)은 장비 파일, 포트·체크포인트·
# revision은 registry(config/models.yaml)에서 하네스 인터프리터로 읽는다. 장비 차이는 장비 파일이 든다.
#
# 메모리 판정은 nvidia-smi `memory.total`로 가른다 — 장비 파일에 적지 않는다(자동 감지가 더 작다).
#   숫자  → 전용 VRAM(vast.ai 등): 여유 = total − used, 필요 = util × total.
#           페이지 캐시는 호스트 RAM이라 VRAM과 안 다퉈 떼지 않는다 — 다음 기동의 읽기만 느려진다.
#   [N/A] → 통합메모리(GB10 — ATOM): 모델 파일의 페이지 캐시를 뗀 뒤(docs/portability.md §6) 여유 =
#           MemAvailable, 필요 = util × MemTotal + 서버 호스트 몫 + 감시자 하한 + 여분.
#   내린 직후에는 메모리가 돌아올 때까지 MEM_WAIT_S까지 기다렸다가 판정한다.
#
# 안전장치 — 하나라도 걸리면 띄우지 않고 0이 아닌 코드로 끝난다(하네스가 run을 멈춘다).
#   · 감시자(tools/serve/guard.sh)가 떠 있어야 띄우고, **감시자 하나는 run 하나만 지킨다.** 서버를 띄우기
#     직전마다(시도마다 — 한 호출이 두 키를 띄우면 둘 다) 감시자가 살아 있고(pid 파일 "PID STARTTIME"의 번호와
#     시작 시각이 함께 맞는다 — 번호만 맞으면 남이 물려받은 번호일 수 있다) 멈추는 중(STOP_GUARD)이 아니며
#     다른 run 것이 아닌지 다시 보고, 이 run 것으로 적는다($SERVE_DIR/guard.owner = "감시자PID RUN_ID" —
#     멱등). 어긋나면 그 자리에서 실패한다 — 이미 띄운 키는 run 끝 내림이 내린다. 적힌 감시자 PID가 지금
#     감시자와 다르면 옛 기록이라 무효다. run 끝 내림(인자 없음)이 이 run 서버를 다 내리면 그 감시자를 멈춘다
#     (STOP_GUARD) — 다음 run은 새 감시자를 띄운다. 교대 중간(내리고 띄우기 전)에는 멈추지 않는다 — 인자
#     없는 호출에서만. 그래서(감시자 마감을 켰다면) 끝난 run의 감시자 마감이 다음 run 서버를 죽이지 않는다.
#     아무것도 띄우지 않은 run(모델 전 단계에서 끝남)은 감시자를 적지 않아 멈추지도 않는다 — 다음 run이 그
#     감시자를 쓰고, 마감(기본 꺼짐 - guard.sh 머리 주석)을 켰다면 그 감시자가 뜬 때부터 센다.
#   · 이 run 밖의 GPU 프로세스(nvidia-smi compute-apps)가 있으면 띄우지 않는다 — 남의 서버도, 지난 run의
#     고아도. ⚠ 컨테이너에서 nvidia-smi가 호스트 PID를 내면 자기 서버도 남으로 보여 멈춘다(안전한 쪽).
#   · 포트가 이미 쓰이면 띄우지 않는다 — 남의 서버에 준비 확인이 붙는 것을 막는다.
#   · registry source에 @rev가 없으면 띄우지 않는다.
#
# SERVERS 한 줄 = "KEY PGID RUN_ID STARTTIME". STARTTIME은 그룹 리더의 /proc/<pid>/stat 22번 필드다. 리더가
# 살아 있는데 값이 다르면 그 번호를 남이 물려받은 것이라 건드리지 않고 행만 지운다. 리더가 없고 구성원만 남았으면
# 우리 그룹이다 — PGID로 쓰이는 동안 그 번호는 재사용되지 않는다(POSIX 「process group lifetime」).
#
# 준비 판정: 채팅 1토큰 요청이 200 → `/v1/models`에 그 이름이 있고 max_model_len = 장비 파일 창.
# 기동은 한 번에 하나씩이다(동시에 띄우면 메모리 프로파일링이 서로를 센다). vLLM stdout·stderr는 기동마다
# 새 파일 $SERVE_DIR/logs/<key>.<run_id>.<UTC>.log로 간다 — 덮어쓰지 않는다.
#
# stdout에는 JSON 보고 한 줄만 낸다(실패해도 — EXIT 트랩). 사람용 로그는 stderr와 $SERVE_DIR/logs/swap.log.
# 환경: HARNESS_RUN_ID(필수) · HARNESS_DEVICE(띄울 때 필수) · HARNESS_PYTHON · SWAP_DEADLINE_S · SERVE_DIR ·
#   VLLM_BIN · HF_HUB_CACHE · MIN_AVAIL_GIB · SERVER_HOST_GIB · SWAP_MARGIN_GIB(통합메모리 판정의 손잡이).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT" # 하네스 인터프리터가 `src`를 import한다
D="${SERVE_DIR:-$HOME/harness-serve}"
SERVERS="$D/servers" # 한 줄에 "KEY PGID RUN_ID STARTTIME"
OWNER="$D/guard.owner" # "감시자PID RUN_ID" — 그 감시자가 지키는 run
VLLM="${VLLM_BIN:-vllm}"
PY="${HARNESS_PYTHON:-python3}"
RUN_ID="${HARNESS_RUN_ID:-}"
DEVICE="${HARNESS_DEVICE:-}"
DEADLINE_S="${SWAP_DEADLINE_S:-600}"
HF_HUB="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
# 통합메모리 판정 — 조사 ②-3의 식(MemAvailable − util×총량 − 서버당 2~3 GiB ≥ 감시자 12 + 4). 계획값.
MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"
SERVER_HOST_GIB="${SERVER_HOST_GIB:-3}"
SWAP_MARGIN_GIB="${SWAP_MARGIN_GIB:-4}"
# 교대 마감 식의 상수 — config/profiles.yaml의 settle_timeout_s가 이 여섯과 `/v1/models` 확인의 상한
# (src/bench_metrics.py `_TIMEOUT_S`)으로 계산된다(tests가 잠근다).
ATTEMPTS=2
TERM_WAIT_S=20
KILL_WAIT_S=30
MEM_WAIT_S=60
POLL_S=5
# 준비 확인 채팅 요청 한 번의 `curl -m` 상한 — 준비 상한을 넘겨서도 이만큼 붙잡는다.
PROBE_MAX_S=120

END=$(($(date +%s) + ${DEADLINE_S%.*}))
REPORT=()
ERROR=""
MEM_KIND=""
mkdir -p "$D/logs"
touch "$SERVERS"

log() { echo "$(date -u +%FT%TZ) [$RUN_ID] $*" | tee -a "$D/logs/swap.log" >&2; }
str() { REPORT+=("s|$1=$2"); } # 보고 필드 — 문자열
num() { REPORT+=("j|$1=$2"); } # 보고 필드 — JSON 값(수·참거짓)
item() { REPORT+=("l|$1=$2"); } # 보고 필드 — 문자열 목록에 하나 더한다(빈 값이면 빈 목록만 만든다)
fail() {
  ERROR=$1
  log "ABORT: $1"
  exit "${2:-2}"
}
remaining() { echo $((END - $(date +%s))); }

emit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ -z "$ERROR" ]; then
    ERROR="예기치 않은 종료(exit $rc) — $D/logs/swap.log를 본다"
  fi
  num ok "$([ "$rc" -eq 0 ] && echo true || echo false)"
  str error "$ERROR"
  num memavail_after "$(mem_free_gib 2> /dev/null || echo null)"
  "$PY" - "${REPORT[@]}" << 'PY' || echo '{"ok": false, "error": "보고를 만들지 못했다"}'
import json
import sys

report = {}
for item in sys.argv[1:]:
    kind, _, rest = item.partition("|")
    path, _, raw = rest.partition("=")
    node = report
    *parents, leaf = path.split("/")
    for part in parents:
        node = node.setdefault(part, {})
    if kind == "l":
        items = node.setdefault(leaf, [])
        if raw:
            items.append(raw)
    elif not raw:
        node[leaf] = None
    elif kind == "j":
        node[leaf] = json.loads(raw)
    else:
        node[leaf] = raw
print(json.dumps(report, ensure_ascii=False))
PY
  exit "$rc"
}
trap emit EXIT

# --- 메모리 ----------------------------------------------------------------------

gpu_mem() { # "TOTAL USED"(MiB, 첫 GPU) — 통합메모리면 숫자가 아니다(`[N/A]`)
  nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits |
    awk -F', *' 'NR == 1 {print $1, $2}'
}

detect_mem() {
  local out total
  out=$(gpu_mem) || fail "nvidia-smi로 GPU 메모리를 못 읽었다"
  total=${out%% *}
  case "$total" in
    '' | *[!0-9]*) MEM_KIND=unified ;;
    *) MEM_KIND=vram ;;
  esac
}

mem_free_gib() {
  if [ "$MEM_KIND" = vram ]; then
    gpu_mem | awk '{printf "%.1f", ($1 - $2) / 1024}'
  else
    awk '/^MemAvailable:/ {printf "%.1f", $2 / 1048576}' /proc/meminfo
  fi
}

need_gib() { # $1 = util
  if [ "$MEM_KIND" = vram ]; then
    gpu_mem | awk -v u="$1" '{printf "%.1f", u * $1 / 1024}'
  else
    awk -v u="$1" -v h="$SERVER_HOST_GIB" -v g="$MIN_AVAIL_GIB" -v m="$SWAP_MARGIN_GIB" \
      '/^MemTotal:/ {printf "%.1f", u * $2 / 1048576 + h + g + m}' /proc/meminfo
  fi
}

drop_cache() { # 통합메모리 — 모델 파일의 페이지 캐시를 뗀다(루트 없이 된다 — docs/portability.md §6)
  "$PY" - "$HF_HUB" << 'PY'
import glob
import os
import sys

for path in glob.glob(os.path.join(sys.argv[1], "**", "*.safetensors"), recursive=True):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        continue
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)
PY
}

wait_for_memory() { # $1 key $2 util — 여유가 필요 이상이 될 때까지(MEM_WAIT_S · 마감)
  local key=$1 util=$2 until free need label
  until=$(($(date +%s) + MEM_WAIT_S))
  [ "$MEM_KIND" = unified ] && label="MemAvailable" || label="VRAM 여유(total − used)"
  while :; do
    if [ "$MEM_KIND" = unified ]; then drop_cache; fi
    free=$(mem_free_gib)
    need=$(need_gib "$util")
    if awk -v f="$free" -v n="$need" 'BEGIN {exit !(f >= n)}'; then
      num "server/$key/memavail_before" "$free"
      return 0
    fi
    if [ "$(date +%s)" -ge "$until" ] || [ "$(remaining)" -le 0 ]; then
      fail "$key: $label ${free} GiB < 필요 ${need} GiB(util $util) — ${MEM_WAIT_S}s·마감까지 기다렸다"
    fi
    sleep "$POLL_S"
  done
}

# --- 프로세스 --------------------------------------------------------------------

procs_in() { # PGID들 → 그 그룹에 산 프로세스(좀비 제외)의 PID. /proc만 본다(pgrep 없이).
  [ $# -gt 0 ] || return 0
  cat /proc/[0-9]*/stat 2> /dev/null | awk -v groups=" $* " '{
    pid = $1
    sub(/.*\) /, "")
    if ($1 != "Z" && index(groups, " " $3 " ")) print pid
  }' || true
}

starttime() { sed 's/.*) //' "/proc/$1/stat" 2> /dev/null | awk '{print $20}'; }

group_ok() { # $1 PGID $2 기록한 STARTTIME — 그 번호가 아직 우리 그룹을 가리키는가(모듈 머리 주석)
  [ ! -e "/proc/$1/stat" ] || [ "$(starttime "$1")" = "${2:-}" ]
}

alive() { group_ok "$1" "${2:-}" && [ -n "$(procs_in "$1")" ]; }

ours() { awk -v r="$RUN_ID" '$3 == r {print $1, $2, $4}' "$SERVERS"; }

forget() {
  awk -v k="$1" -v r="$RUN_ID" '!($1 == k && $3 == r)' "$SERVERS" > "$SERVERS.tmp"
  mv "$SERVERS.tmp" "$SERVERS"
}

stop_group() { # $1 PGID $2 STARTTIME. TERM → TERM_WAIT_S 안에 안 죽으면 KILL → 그룹이 빌 때까지 KILL_WAIT_S
  local pg=$1 start=${2:-} i
  if ! group_ok "$pg" "$start"; then
    log "pgid $pg는 남이 물려받았다(시작 시각이 다르다) — 건드리지 않고 행만 지운다"
    return 0
  fi
  kill -TERM -- "-$pg" 2> /dev/null || return 0
  for ((i = 0; i < TERM_WAIT_S; i++)); do
    alive "$pg" "$start" || return 0
    sleep 1
  done
  log "pgid $pg가 TERM ${TERM_WAIT_S}s 안에 안 끝났다 — KILL"
  kill -KILL -- "-$pg" 2> /dev/null || true
  for ((i = 0; i < KILL_WAIT_S; i++)); do
    alive "$pg" "$start" || return 0
    sleep 1
  done
  return 1
}

foreign_check() { # 이 run 밖의 GPU 프로세스가 있으면 멈춘다
  local mine listed foreign
  mine=$(procs_in $(ours | while read -r _ pg start; do group_ok "$pg" "$start" && echo "$pg"; done))
  listed=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2> /dev/null | tr -d ' ') || listed=""
  foreign=$(printf '%s\n' "$listed" | grep -vxF -f <(printf '%s\n' $mine 0) | grep . || true)
  if [ -n "$foreign" ]; then
    fail "이 run 밖의 GPU 프로세스가 있다: $(echo $foreign) — 남의 서버거나 지난 run의 고아다(고아면 HARNESS_RUN_ID=<그 run> bash tools/serve/swap.sh로 내린다)"
  fi
}

guard_pid() { awk 'NR == 1 {print $1}' "$D/guard.pid" 2> /dev/null || true; } # 파일은 "PID STARTTIME"

live() { # 그 PID가 살아 있다 — 좀비는 죽은 것이다(`kill -0`은 좀비에도 성공한다)
  local state
  state=$(sed 's/.*) //' "/proc/$1/stat" 2> /dev/null | cut -d' ' -f1)
  [ -n "$state" ] && [ "$state" != Z ]
}

guard_alive() { # pid 파일의 번호가 살아 있고 시작 시각까지 같으며(guard.sh 머리 주석) 멈추는 중이 아니다
  local gp="" gs=""
  read -r gp gs 2> /dev/null < "$D/guard.pid" || true
  [ -n "$gp" ] && [ -n "$gs" ] && live "$gp" && [ "$(starttime "$gp")" = "$gs" ] && [ ! -e "$D/STOP_GUARD" ]
}

owner() { # 지금 감시자가 지키는 run — 옛 감시자가 적은 줄은 무효다
  local gp p r
  gp=$(guard_pid)
  [ -n "$gp" ] && [ -f "$OWNER" ] && read -r p r < "$OWNER" && [ "$p" = "$gp" ] && echo "$r"
}

claim_guard() { # 서버를 띄우기 직전마다 — 감시자가 떠 있고 이 run 것인지 다시 보고 적는다(모듈 머리 주석)
  local held
  if [ -e "$D/STOP_GUARD" ]; then
    fail "STOP_GUARD가 있다 — 감시자가 멈추는 중이다(run 끝 내림이나 사람이 멈췄다). 감시 없이 띄우지 않는다 — 새 감시자를 띄운다(뜨면서 STOP_GUARD를 지운다)"
  fi
  guard_alive || fail "감시자가 안 떠 있다 — tools/serve/guard.sh를 먼저 띄운다(run마다 하나 — run 끝 내림이 멈춘다)"
  held=$(owner) || held=""
  if [ -n "$held" ] && [ "$held" != "$RUN_ID" ]; then
    fail "감시자(pid $(guard_pid))는 run $held를 지킨다 — 감시자 하나는 run 하나만 지킨다. 그 run의 서버를 내리고(HARNESS_RUN_ID=$held bash tools/serve/swap.sh) 새 감시자를 띄운다"
  fi
  echo "$(guard_pid) $RUN_ID" > "$OWNER"
}

release_guard() { # run 끝 내림 뒤 — 이 run이 지키게 한 감시자를 멈춘다. 이 run 서버가 남았으면 안 멈춘다
  local held
  held=$(owner) || return 0
  [ "$held" = "$RUN_ID" ] && [ -z "$(ours)" ] || return 0
  touch "$D/STOP_GUARD"
  rm -f "$OWNER"
  str guard stopped
  log "감시자 $(guard_pid)를 멈춘다 — run $RUN_ID의 서버를 다 내렸다"
}

port_busy() { (exec 3<> "/dev/tcp/127.0.0.1/$1") 2> /dev/null; }

# --- 모델 ------------------------------------------------------------------------

row() { # KEY → 셸 대입문. 장비 파일은 하네스 스키마로(오타·범위 밖 값이 여기서 죽는다), registry는 원문으로.
  "$PY" - "$DEVICE" "$1" << 'PY'
import shlex
import sys
from urllib.parse import urlparse

import yaml

from src.config import CONFIG_DIR, load_serving

device, key = sys.argv[1:]
row = load_serving(device).servers.get(key)
if row is None:
    sys.exit(f"장비 파일 {device}.yaml에 {key} 행이 없다")
entry = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))["models"][key]
lora_base = entry.get("lora_base")
if lora_base:
    # 어댑터 엔트리: base를 띄우고(아래 launch가 --enable-lora로 이 key를 얹는다) adapter는 레포 내 경로.
    repo, _, rev = lora_base.removeprefix("hf://").partition("@")
    adapter = str((CONFIG_DIR.parent / (entry.get("source") or "").removeprefix("file://")).resolve())
else:
    repo, _, rev = (entry.get("source") or "").removeprefix("hf://").partition("@")
    adapter = ""
values = {
    "port": urlparse(entry["endpoint"]).port,
    "repo": repo,
    "rev": rev,
    "adapter": adapter,
    "lora": "1" if lora_base else "",
    "util": row.util,
    "seqs": row.max_num_seqs,
    "maxlen": row.max_model_len,
    "probe": row.probe,
    "ready_s": int(row.ready_timeout_s),
}
for name, value in values.items():
    print(f"{name}={shlex.quote(str(value))}")
print("extra=(" + " ".join(shlex.quote(arg) for arg in row.extra_args) + ")")
print("envs=(" + " ".join(shlex.quote(f"{k}={v}") for k, v in row.env.items()) + ")")
PY
}

probe_chat() { # 1토큰 추론이 200으로 돌아와야 준비됐다 — /v1/models만으로는 답하는지 모른다
  local m body
  m=$(remaining)
  if [ "$m" -gt "$PROBE_MAX_S" ]; then m=$PROBE_MAX_S; fi
  if [ "$m" -lt 1 ]; then return 1; fi
  body="{\"model\":\"$key\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}"
  curl -sf -m "$m" -H 'Content-Type: application/json' -d "$body" \
    "http://127.0.0.1:$port/v1/chat/completions" > /dev/null
}

served_window() { # /v1/models에 그 이름이 있으면 max_model_len을 낸다(하네스와 같은 판정)
  "$PY" - "$key" "$port" << 'PY'
import sys

from src.bench import check_startup

key, port = sys.argv[1:]
served = check_startup(f"http://127.0.0.1:{port}/v1", key)
if not served["ok"]:
    sys.exit(served["reason"])
print(served["max_model_len"])
PY
}

launch() { # 전역 key·port·…(row의 대입문)으로 띄우고 준비될 때까지 본다. 안 되면 ATTEMPTS까지 다시.
  local attempt logf pid start t0 now window
  local -a args
  if [ -n "$lora" ]; then
    # 어댑터 엔트리: base를 `$key-base`로 띄우고 그 위에 `$key` LoRA를 얹는다. 요청 model=$key가 어댑터를 탄다.
    args=(serve "$repo" --revision "$rev" --served-model-name "$key-base" --host 127.0.0.1
      --port "$port" --gpu-memory-utilization "$util" --max-model-len "$maxlen"
      --max-num-seqs "$seqs" --enable-lora --max-lora-rank 16 --lora-modules "$key=$adapter" "${extra[@]}")
  else
    args=(serve "$repo" --revision "$rev" --served-model-name "$key" --host 127.0.0.1
      --port "$port" --gpu-memory-utilization "$util" --max-model-len "$maxlen"
      --max-num-seqs "$seqs" "${extra[@]}")
  fi
  str "server/$key/args" "${args[*]}"
  str "server/$key/revision" "$rev"
  num "server/$key/util" "$util"
  num "server/$key/max_num_seqs" "$seqs"
  num "server/$key/max_model_len" "$maxlen"
  for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    num "server/$key/attempts" "$attempt"
    foreign_check
    wait_for_memory "$key" "$util"
    if port_busy "$port"; then fail "포트 $port가 이미 쓰인다 — $key를 띄우지 않는다"; fi
    claim_guard # 배치 시작의 사진이 아니라 이 기동 직전의 감시자다 — 앞 키를 띄우는 사이에 끝났을 수 있다
    logf="$D/logs/$key.$RUN_ID.$(date -u +%Y%m%dT%H%M%SZ).log"
    if [ -e "$logf" ]; then fail "로그 $logf가 이미 있다 — 덮어쓰지 않는다"; fi
    str "server/$key/log" "$logf"
    log "launch $key attempt=$attempt port=$port util=$util max_model_len=$maxlen seqs=$seqs log=$logf"
    t0=$(date +%s)
    # 9>&- — 서버가 잠금 fd를 물려받으면 살아 있는 동안 잠금이 안 풀려 다음 교대가 못 들어온다.
    env "${envs[@]}" setsid "$VLLM" "${args[@]}" > "$logf" 2>&1 < /dev/null 9>&- &
    pid=$!
    start=$(starttime "$pid")
    echo "$key $pid $RUN_ID $start" >> "$SERVERS" # 준비 전에 적는다 — 여기서 끊겨도 run 끝 내림이 내린다
    while :; do
      if probe_chat; then
        window=$(served_window) || fail "$key: 채팅은 답하는데 /v1/models 확인이 실패했다(위 stderr)"
        if [ "$window" != "$maxlen" ]; then
          fail "$key의 서빙 창 max_model_len $window ≠ 장비 파일 $maxlen"
        fi
        now=$(date +%s)
        num "server/$key/ready_s" "$((now - t0))"
        str "server/$key/action" launch
        num "server/$key/load_s" "$(sed -n 's/.*Model loading took .* and \([0-9.]*\) seconds.*/\1/p' "$logf" | head -1)"
        str "server/$key/vllm_version" "$(sed -n 's/.* version \([0-9][0-9.]*\).*/\1/p' "$logf" | head -1)"
        log "ready $key after $((now - t0))s"
        return 0
      fi
      if ! alive "$pid" "$start"; then
        log "DIED $key (attempt $attempt) — $logf 꼬리:"
        tail -25 "$logf" >&2 || true
        break
      fi
      now=$(date +%s)
      if [ "$now" -ge "$END" ]; then
        fail "마감 SWAP_DEADLINE_S=${DEADLINE_S}s 안에 $key가 준비되지 않았다 — 띄운 것은 run 끝 내림이 내린다" 3
      fi
      if [ "$((now - t0))" -ge "$ready_s" ]; then
        log "TIMEOUT $key ready_timeout_s=${ready_s} (attempt $attempt)"
        break
      fi
      sleep "$POLL_S"
    done
    stop_group "$pid" "$start" || fail "$key 프로세스 그룹 $pid가 KILL 뒤에도 남았다" 3
    forget "$key"
  done
  fail "$key가 ${ATTEMPTS}번 다 안 떴다 — 마지막 로그 $logf" 3
}

# --- 본체 ------------------------------------------------------------------------

[[ "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]] || fail "HARNESS_RUN_ID가 없거나 파일 이름에 못 쓰는 글자가 있다: '$RUN_ID'"
str run_id "$RUN_ID"
str device "$DEVICE"
item models ""
for key in "$@"; do item models "$key"; done

exec 9>> "$D/swap.lock"
flock -w "$(remaining)" 9 || fail "교대 잠금($D/swap.lock)을 마감 안에 못 잡았다 — 다른 교대가 돌고 있다"
detect_mem
str memory "$MEM_KIND"
num memavail_before "$(mem_free_gib)"
log "swap → [${*:-전부 내림}] memory=$MEM_KIND"

# 1) 이 run의 서버 중 원하지 않는 것과 죽은 것을 내린다.
wanted=" $* "
while read -r key pg start; do
  if [[ "$wanted" == *" $key "* ]] && alive "$pg" "$start"; then continue; fi
  log "stop $key (pgid $pg)"
  stop_group "$pg" "$start" || fail "$key 프로세스 그룹 $pg가 KILL 뒤에도 남았다" 3
  forget "$key"
  str "server/$key/action" stop
done < <(ours)
if [ $# -eq 0 ]; then release_guard; fi

# 2) 원하는 것 — 떠 있으면 요청 한 건으로 확인해 두고, 없으면 하나씩 띄운다.
if [ $# -gt 0 ]; then
  [ -n "$DEVICE" ] || fail "HARNESS_DEVICE가 없다 — 어느 장비 파일로 띄울지 모른다"
fi
for key in "$@"; do
  spec=$(row "$key") || fail "장비 파일·registry에서 $key를 못 읽었다(위 stderr)"
  eval "$spec"
  [ "$probe" = chat ] || fail "$key: 준비 판정 $probe는 교대 대상이 아니다 — 검색 모델은 교대하지 않는다"
  [ -n "$rev" ] || fail "$key: registry source(또는 lora_base)에 @rev가 없다 — revision 없이 띄우지 않는다"
  if [ -n "$lora" ]; then
    [ -n "$adapter" ] || fail "$key: lora_base는 있는데 source(어댑터 경로)가 비었다"
    [ -d "$adapter" ] || fail "$key: 어댑터 경로가 없다: $adapter (레포 번들에 models/가 들어왔는지 확인)"
  fi
  read -r pg start < <(ours | awk -v k="$key" '$1 == k {print $2, $3}') || pg=""
  if [ -n "$pg" ]; then
    if probe_chat; then
      log "keep $key"
      str "server/$key/action" keep
      continue
    fi
    log "stale $key — 응답이 없어 다시 띄운다"
    stop_group "$pg" "$start" || fail "$key 프로세스 그룹 $pg가 KILL 뒤에도 남았다" 3
    forget "$key"
  fi
  launch
done
log "swap done → [${*:-}]"
