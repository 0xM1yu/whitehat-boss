#!/usr/bin/env bash
# 원격 서빙 터널 — 로컬 127.0.0.1:<포트>를 원격 박스의 같은 포트로 잇는다(장비 `vastai-remote`).
#
# 사용: SWAP_SSH_HOST=<목적지> [SWAP_SSH_PORT=<포트>] bash tools/serve/vast-tunnel.sh [--loop]
#   run 전에 별도 터미널에서 띄우고 run 동안 둔다. 끊기면 모델 호출과 교대 뒤 2차 확인(`/v1/models`)이 실패한다.
#   registry(`config/models.yaml`)의 엔드포인트가 `127.0.0.1`이라 하네스 코드는 그대로다.
#   --loop: ssh가 끝나면 5초 뒤 다시 잇는다 — 횟수 제한은 일부러 없다(끊김이 run 전체를 죽이지 않게). 멈춤은 Ctrl-C이거나
#     이 PC의 `${SERVE_DIR:-$HOME/harness-serve}/STOP_GUARD`다 — **로컬 파일**이고 원격 guard.sh의 표지와 이름만 같다.
#     옛 표지가 남아 있으면 시작 직후 멈춘다. SIGTERM은 자식 ssh가 끝나야 처리된다(bash trap 의미론).
#
# keepalive: ServerAliveCountMax의 기본 3 × Interval 10 s = 30초 무응답이면 터널이 스스로 끊긴다(2026-10-11 run,
#   docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md §6). 12로 올려 2분을 견딘다.
#
# PORTS는 `config/serving/vastai-remote.yaml`의 서버 행이 registry에서 받는 포트다 — 손으로 적었고
# `tests/test_remote_swap.py`가 그 집합과 같은지 잠근다(행이나 포트가 바뀌면 그 테스트가 빨개진다).
set -euo pipefail

PORTS=(8000 8001 8002 8003 8006)

loop=0
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
