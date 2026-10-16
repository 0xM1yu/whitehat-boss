#!/usr/bin/env bash
# Slurm(REDACTED-cluster) 래퍼. **잡 실행 방식만 감싼다** — 측정은 run_probe.sh가 한다.
#
# 로그인 노드에서 돌린다. srun 하나가 컴퓨트 노드에서 vLLM과 프로브를 **함께** 띄우므로
# endpoint는 그 노드 안의 127.0.0.1이다. 무거운 작업을 로그인 노드에서 하지 말라는 규정
# (tools/README.md)에 맞는 유일한 방식이기도 하다.
#
# 사용:
#
#   tmux new -s probe                        # ← 먼저 tmux. 아래 주의 참조
#   sinfo -p REDACTED-partition                  # ← idle 노드 확인. u 노드는 고정이 아니다
#   tools/probe/slurm.sh --node REDACTED-node --out /data/<계정>/probe
#
# **--node에 기본값을 두지 않는다.** 비어 있는 노드를 그때그때 쓰는 것이 이 클러스터의
# 운용 방식이고, 노드마다 카드가 다르다(u1은 6GB, u3·u4는 12GB, u8은 Prolog failure로
# 사용 금지). 기본값을 두면 남이 쓰는 노드에 얹히거나 6GB 카드에서 재고도 그 사실이
# 산출물에 안 남는다. 범위(`REDACTED-node-range`)로 주면 `Node count specification invalid`다.
#
# **tmux 안에서 돌려라.** srun은 SSH가 끊기면 job까지 죽는다. 외부망에서는 접속 포트가
# 다르다(`ssh -p REDACTED-port`). 끝나면 srun이 노드를 반납한다.
set -euo pipefail

PARTITION="${PARTITION:-REDACTED-partition}"
# A2000. u3·u4에서 실측한 값이다.
ARCH="${ARCH:-sm_86}"
HOST_LABEL="${HOST_LABEL:-slurm}"

# 홈 디렉터리 용량 규정이 있어 캐시를 /data/ 아래로 보낸다. 안 그러면 모델을 내려받다가
# 쿼터에 걸려 죽는다.
DATA_ROOT="${DATA_ROOT:-/data/${USER:-$(id -un)}}"
CONDA_SH="${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-vllm}"

NODE=""
OUT=""
PORT="${PORT:-8001}"
while [ $# -gt 0 ]; do
    case "$1" in
        --node) NODE="${2:?--node에 노드 이름을 줘라}"; shift 2 ;;
        --out) OUT="${2:?--out에 디렉터리를 줘라}"; shift 2 ;;
        --port) PORT="${2:?--port에 포트를 줘라}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "[ERROR] 모르는 인자: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$NODE" ]; then
    echo "[ERROR] --node가 필요하다. 기본값을 두지 않는 이유는 이 파일 머리에 있다." >&2
    echo "        비어 있는 노드: sinfo -p ${PARTITION}" >&2
    exit 2
fi
OUT="${OUT:-$DATA_ROOT/probe}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ ! -f "$CONDA_SH" ]; then
    echo "[ERROR] conda.sh가 없다: $CONDA_SH — CONDA_SH로 경로를 넘겨라." >&2
    exit 2
fi

echo "[slurm] ${PARTITION} / ${NODE} / port ${PORT} → ${OUT}"

# 인용에 주의: 바깥에서 확장할 값은 그대로 두고, 노드 안에서 확장할 값만 \$로 막는다.
srun --partition="$PARTITION" --nodes=1 --nodelist="$NODE" \
     --cpus-per-task=4 --mem=16G --gres=gpu:1 \
     bash -s <<EOF
set -euo pipefail

# **activate 전에 source 한다.** 비대화형 셸에는 conda 훅이 없어 conda activate가
# "shell not initialized"로 죽는다.
source "$CONDA_SH"
conda activate "$CONDA_ENV"

# conda 환경의 libstdc++를 앞세운다. 노드의 시스템 libstdc++가 낡아 GLIBCXX_3.4.xx를
# 못 찾으면 torch import에서 죽는다.
export LD_LIBRARY_PATH="\$CONDA_PREFIX/lib:\${LD_LIBRARY_PATH:-}"

# 홈 디렉터리 용량 규정. 모델과 pip 캐시가 홈으로 가면 쿼터에 걸린다.
export PIP_CACHE_DIR="$DATA_ROOT/.cache/pip"
export HF_HOME="$DATA_ROOT/.cache/huggingface"
mkdir -p "\$PIP_CACHE_DIR" "\$HF_HOME"

echo "[slurm] node=\$(hostname)  python=\$(command -v python)"
# 어느 카드에서 쟀는지를 로그에 남긴다 — u 노드는 고정이 아니라 6GB일 수도 있다.
nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv,noheader || true

PORT="$PORT" HOST_LABEL="$HOST_LABEL" ARCH="$ARCH" OUT="$OUT" \\
    bash "$REPO_ROOT/tools/probe/run_probe.sh"
EOF

echo "[slurm] 끝났다. srun이 노드를 반납했다."
echo "[slurm] 산출물: $OUT"
