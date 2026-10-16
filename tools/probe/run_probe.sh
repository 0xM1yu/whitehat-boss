#!/usr/bin/env bash
# 측정 절반. **두 장비에서 완전히 같아야 하는 부분이 전부 여기 있다.**
#
# seraph.sh와 atom.sh는 이 스크립트를 부르기까지의 방식만 다르다(srun이냐 직접 실행이냐,
# conda 활성화가 필요하냐). 서버를 어떻게 띄우고 무엇을 재는지가 갈리면 두 장비의 원문
# 차이를 "vLLM 버전 차이"라고 말할 수 없게 된다.
#
# 서버와 클라이언트를 **같은 노드**에서 돌린다. 그래서 endpoint는 항상 127.0.0.1이고,
# 장비별로 host가 갈리지 않는다.
#
# 필요한 값은 아래 :? 로 강제한다 — 기본값을 두면 라벨이 틀린 채로 fixture가 만들어지고,
# 그 파일이 CI를 통과한다.
set -euo pipefail

PORT="${PORT:?PORT를 넘겨라 (예: 8001)}"
HOST_LABEL="${HOST_LABEL:?HOST_LABEL을 넘겨라 (예: seraph / atom)}"
ARCH="${ARCH:?ARCH를 넘겨라 (예: sm_86 / sm_121)}"
OUT="${OUT:?OUT을 넘겨라 (산출물 디렉터리)}"

# 0단계에서 Qwen3-0.6B로 통일한 이유가 이것이다 — 같은 모델이어야 원문 차이가 순수하게
# vLLM 버전 차이로 분리된다. MODEL_NAME은 --served-model-name이고, 프로브가 /v1/models의
# id와 대조해 남의 서버를 거른다.
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen3-0.6B}"
MODEL_NAME="${MODEL_NAME:-qwen3-0.6b}"

# 0단계와 같은 값. 바꾸면 ATOM 원문을 Seraph 원문과 직접 대조할 수 없다.
REQUESTS="${REQUESTS:-6}"
PREFIX_TOKENS="${PREFIX_TOKENS:-240}"

# 이 비율이 다르면 같은 카드·같은 모델에서도 점유가 통째로 달라진다(model-serving.md §5).
# 실측 원문의 cache_config_info가 0.6이었으므로 그 값을 기본으로 둔다.
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.6}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
# prefix cache가 이 측정의 대상이므로 명시적으로 켠다. 기본값이 버전마다 다르고, 꺼진
# 채로 재면 "적중 0"이 서버 설정 탓인지 prefix 탓인지 갈리지 않는다.
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:---enable-prefix-caching}"

# 기동 대기 상한. `probe_metrics.py`가 받는 값인데 여기서 넘기지 않아 **도달 불가**였다
# (2026-08-17: ATOM 1차 시도가 정확히 이 300초에서 끝났고 늘릴 방법이 없었다).
WAIT_S="${WAIT_S:-300}"

# **인터페이스가 여러 개인 장비에서 vLLM이 자기 자신에게 못 붙는다.** world_size=1이어도
# torch.distributed의 TCPStore를 띄우는데, 자기 주소로 WiFi 인터페이스를 골라 거기로
# 접속하려다 실패한다 (2026-08-17 ATOM 실측):
#
#   distributed_init_method=tcp://192.168.0.10:54029 backend=nccl
#   [c10d] The client socket has failed to connect to [atom-host]:54029
#          (errno: 113 - No route to host)
#   TCP client failed ... retrying (try=0, timeout=600000ms, ...)
#
# `timeout=600000ms`라 **10분짜리 재시도 루프**에 갇히고 매번 C++ 백트레이스를 찍는다.
# 프로브는 그전에 포기하므로 사람은 원인을 모른 채 끝난다.
#
# **이미 설정된 값은 덮지 않는다.** 서버와 클라이언트를 같은 노드에서 돌리는 이 절차에서는
# loopback이 맞지만 다중 노드 환경에서는 틀린 값이고, 그 환경의 사람이 명시적으로 준 값을
# 스크립트가 조용히 뒤집으면 ADR 0005가 "런타임 자동 감지"를 기각한 이유와 같은 실패가
# 된다 — 같은 명령이 장비에 따라 다른 것을 하고 그 사실이 어디에도 안 남는다.
export VLLM_HOST_IP="${VLLM_HOST_IP:-127.0.0.1}"
export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-lo}"
export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-lo}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python}"
ENDPOINT="http://127.0.0.1:${PORT}/v1"

mkdir -p "$OUT"

# 이미 쓰이는 포트면 여기서 죽는다. **남이 띄운 서버에 요청을 쏘고 그 수치를 우리 것으로
# 기록하는 일이 없어야 한다** — 계정이 공용일 수 있다. ss·lsof는 새 장비에 없을 수
# 있으므로 파이썬으로 본다(프로브를 돌리는 이상 파이썬은 반드시 있다).
"$PYTHON" - "$PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as probe:
    # 서버가 bind할 때와 같은 조건으로 본다. 이게 없으면 **직전 run이 남긴 TIME_WAIT
    # 소켓**을 "사용 중"으로 오판한다 — 프로브 자신의 트래픽이 그런 소켓을 만들므로
    # (urllib은 매 요청 Connection: close), 같은 포트로 다시 재려면 60초를 기다려야 한다.
    # 실측 창이 짧은 장비에서 그 오판은 "남의 서버"라는 틀린 결론까지 낸다.
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(
            f"[ERROR] 포트 {port}가 이미 쓰이고 있다: {exc}\n"
            "        다른 사람의 서버일 수 있다. PORT로 빈 포트를 주거나, 그 서버가 "
            "내 것인지 확인하고 내려라."
        )
PY

echo "[run_probe] vLLM 기동: ${MODEL_PATH} → ${MODEL_NAME} @ ${ENDPOINT}"
# shellcheck disable=SC2086  # VLLM_EXTRA_ARGS는 여러 인자로 쪼개져야 한다
vllm serve "$MODEL_PATH" \
    --served-model-name "$MODEL_NAME" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    --max-model-len "$MAX_MODEL_LEN" \
    $VLLM_EXTRA_ARGS \
    >"$OUT/vllm-server.log" 2>&1 &
SERVER_PID=$!

# 프로브가 실패하든 성공하든 서버를 내린다. Seraph에서는 srun이 노드를 반납하지만 ATOM은
# 직접 실행이라, 여기서 안 내리면 GPU를 물고 남아 다음 사람이 못 쓴다.
cleanup() {
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "[run_probe] vLLM 종료 (pid ${SERVER_PID})"
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# vLLM 버전은 /metrics도 /v1/models도 주지 않는다(실측 확인). 서버를 띄운 **그 환경에서**
# 직접 물어 파일명에 박는다 — 사람이 손으로 적으면 틀린 이름의 fixture가 CI를 통과한다.
VLLM_VERSION="$("$PYTHON" -c 'import vllm; print(vllm.__version__)')"
echo "[run_probe] vLLM ${VLLM_VERSION} / ${HOST_LABEL} / ${ARCH}"

"$PYTHON" "$REPO_ROOT/tools/probe/probe_metrics.py" \
    --endpoint "$ENDPOINT" \
    --model "$MODEL_NAME" \
    --vllm-version "$VLLM_VERSION" \
    --host-label "$HOST_LABEL" \
    --arch "$ARCH" \
    --out "$OUT" \
    --requests "$REQUESTS" \
    --prefix-tokens "$PREFIX_TOKENS" \
    --wait "$WAIT_S"
