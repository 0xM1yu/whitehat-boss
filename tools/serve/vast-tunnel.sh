#!/usr/bin/env bash
# 원격 서빙 터널 — 로컬 127.0.0.1:<포트>를 원격 박스의 같은 포트로 잇는다(장비 `vastai-remote`).
#
# 사용: SWAP_SSH_HOST=<목적지> [SWAP_SSH_PORT=<포트>] bash tools/serve/vast-tunnel.sh [--loop]
#   run 전에 별도 터미널에서 띄우고 run 동안 둔다. 끊기면 모델 호출과 교대 뒤 2차 확인(`/v1/models`)이 실패한다.
#   registry(`config/models.yaml`)의 엔드포인트가 `127.0.0.1`이라 하네스 코드는 그대로다.
#   --loop: ssh가 끝나면 5초 뒤 다시 잇는다 — 횟수 제한은 일부러 없다(끊김이 run 전체를 죽이지 않게). 끄는 것은 Ctrl-C이고,
#     막는 것은 이 PC의 `${SERVE_DIR:-$HOME/harness-serve}/STOP_GUARD`다 — **로컬 파일**이고 원격 guard.sh의 표지와 이름만 같다.
#     표지는 다음 재연결을 막는다(시작 전과 재연결 직전마다 본다) — 살아 있는 ssh는 Ctrl-C로 끝낸다.
#     옛 표지가 남아 있으면 시작 직후 멈춘다. SIGTERM은 자식 ssh가 끝나야 처리된다(bash trap 의미론) - 신호 처리·Ctrl-C 종료는 테스트하지 않았다.
#
# keepalive: ServerAliveCountMax의 기본 3 × Interval 10 s = 30초 무응답이면 터널이 스스로 끊긴다(#605 보고서 §6 —
#   `docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md`, #605가 머지돼야 저장소에 있다). 12로 올려 2분을 견딘다.
#   **예방이다** — 관측된 끊김(2026-10-11, 로컬 인터넷 전환 때)의 처방은 keepalive가 아니라 `--loop` 재연결이다(같은 §6).
#
# PORTS는 `config/serving/vastai-remote.yaml`의 서버 행이 registry에서 받는 포트다 — 손으로 적었고
# `tests/test_remote_swap.py`가 그 집합과 같은지 잠근다(행이나 포트가 바뀌면 그 테스트가 빨개진다).
set -euo pipefail

# ⏵ 2026-10-14 — 한 칸씩 당겨졌다(그 전 8000 8001 8002 8003 8006). `scout_boundary`가 `qwen3-14b`로
#   옮겨 Scout 서버가 둘에서 하나가 되었고 뒤 단계 포트가 전부 내려왔다 — PROJECT-CONTEXT.md §1.
PORTS=(8000 8001 8002 8005)

loop=0
if [ $# -gt 1 ]; then
  echo "vast-tunnel: 인자가 너무 많다 — 쓸 수 있는 것은 --loop 하나뿐이다" >&2
  exit 2
fi
case "${1:-}" in
  "") ;;
  --loop) loop=1 ;;
  *)
    echo "vast-tunnel: 알 수 없는 인자 '$1' — 쓸 수 있는 것은 --loop뿐이다" >&2
    exit 2
    ;;
esac

if [ -z "${SWAP_SSH_HOST:-}" ]; then
  echo "vast-tunnel: SWAP_SSH_HOST가 비었다" >&2
  exit 2
fi

args=()
for p in "${PORTS[@]}"; do
  args+=(-L "127.0.0.1:$p:127.0.0.1:$p")
done
if [ -n "${SWAP_SSH_PORT:-}" ]; then
  args+=(-p "$SWAP_SSH_PORT")
fi
# ExitOnForwardFailure: 로컬 포트가 이미 쓰이면 반쯤 열린 터널로 남지 않고 끝난다.
cmd=(ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=12 -o TCPKeepAlive=yes
  "${args[@]}" -- "$SWAP_SSH_HOST")

if [ "$loop" -eq 0 ]; then
  exec "${cmd[@]}"
fi
stop="${SERVE_DIR:-$HOME/harness-serve}/STOP_GUARD"
trap 'exit 130' INT TERM
while [ ! -e "$stop" ]; do
  "${cmd[@]}" || echo "vast-tunnel: ssh가 끝났다(exit $?)" >&2
  [ -e "$stop" ] && break
  echo "vast-tunnel: 5초 뒤 다시 잇는다" >&2
  sleep 5
done
echo "vast-tunnel: $stop이(가) 있어 멈춘다" >&2
