#!/usr/bin/env bash
# ATOM(GB10) 래퍼. **접속·점유 조율만 감싼다** — 측정은 run_probe.sh가 한다.
# ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). 이 파일은 그때의 절차다(tools/probe/README.md 머리).
#
# 스케줄러가 없어 직접 실행한다. 서버와 프로브가 같은 기계에서 도므로 endpoint는
# 127.0.0.1이고, Slurm 클러스터와 같은 값이다.
#
# 사용:
#
#   tmux new -s probe                        # ← SSH(Tailscale 경유)가 끊겨도 살아남는다
#   tools/probe/atom.sh --port 8001 --out ~/probe
#   WAIT_S=900 tools/probe/atom.sh ...       # 기동이 300초를 넘을 때 (2026-08-17 1차 시도)
#
# **계정이 공용일 수 있다.** 그래서 두 가지를 실행 전에 본다:
#
#   1. 포트 점유 — run_probe.sh가 bind를 시도해 이미 쓰이면 죽는다. 남이 띄운 서버에
#      요청을 쏘고 그 수치를 우리 것으로 기록하는 일이 없어야 한다. 프로브도 /v1/models의
#      id를 대조해 한 번 더 막는다.
#   2. GPU 점유 — 이미 다른 프로세스가 카드를 물고 있으면 경고하고 확인을 받는다.
#      통합메모리라 남의 프로세스와 나눠 쓰면 gpu_memory_utilization의 의미가 달라지고,
#      그 수치로 낸 mem_gb_ctx8k는 다른 사람이 재현할 수 없다(model-serving.md §5).
#
# 확인 없이 밀어붙이려면 --force. **사람이 옆에 있을 때만** 쓴다.
set -euo pipefail

# GB10 (Blackwell).
ARCH="${ARCH:-sm_121}"
HOST_LABEL="${HOST_LABEL:-atom}"

OUT=""
PORT="${PORT:-8001}"
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="${2:?--port에 포트를 줘라}"; shift 2 ;;
        --out) OUT="${2:?--out에 디렉터리를 줘라}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "[ERROR] 모르는 인자: $1" >&2; exit 2 ;;
    esac
done
OUT="${OUT:-$HOME/probe}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[atom] host=$(hostname)  port=${PORT} → ${OUT}"
nvidia-smi --query-gpu=name,compute_cap,memory.total,memory.used --format=csv,noheader || true

# 카드를 물고 있는 프로세스. `--query-compute-apps`는 GPU를 쓰는 프로세스만 낸다.
BUSY="$(nvidia-smi --query-compute-apps=pid,process_name,used_memory \
        --format=csv,noheader 2>/dev/null || true)"
if [ -n "$BUSY" ]; then
    echo "[WARN ] 이미 GPU를 쓰는 프로세스가 있다:" >&2
    echo "$BUSY" | sed 's/^/         /' >&2
    echo "[WARN ] 통합메모리를 나눠 쓰면 gpu_memory_utilization의 의미가 달라지고," >&2
    echo "        그 수치로 낸 mem_gb_ctx8k는 다른 사람이 재현할 수 없다." >&2
    if [ "$FORCE" -eq 1 ]; then
        echo "[WARN ] --force가 걸려 그대로 진행한다. 이 사실은 산출물에 남지 않는다." >&2
    else
        printf '계속하려면 yes를 입력해라: '
        read -r answer
        [ "$answer" = "yes" ] || { echo "취소했다"; exit 1; }
    fi
fi

PORT="$PORT" HOST_LABEL="$HOST_LABEL" ARCH="$ARCH" OUT="$OUT" \
    bash "$REPO_ROOT/tools/probe/run_probe.sh"

echo "[atom] 끝났다. run_probe.sh가 vLLM을 내렸다."
echo "[atom] 산출물: $OUT"
