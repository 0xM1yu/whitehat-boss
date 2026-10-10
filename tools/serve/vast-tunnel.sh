#!/usr/bin/env bash
# 원격 서빙 터널 — 로컬 127.0.0.1:<포트>를 원격 박스의 같은 포트로 잇는다(장비 `vastai-remote`).
#
# 사용: SWAP_SSH_HOST=<목적지> [SWAP_SSH_PORT=<포트>] bash tools/serve/vast-tunnel.sh
#   run 전에 별도 터미널에서 띄우고 run 동안 둔다. 끊기면 모델 호출과 교대 뒤 2차 확인(`/v1/models`)이 실패한다.
#   registry(`config/models.yaml`)의 엔드포인트가 `127.0.0.1`이라 하네스 코드는 그대로다.
#
# PORTS는 `config/serving/vastai-remote.yaml`의 서버 행이 registry에서 받는 포트다 — 손으로 적었고
# `tests/test_remote_swap.py`가 그 집합과 같은지 잠근다(행이나 포트가 바뀌면 그 테스트가 빨개진다).
set -euo pipefail

PORTS=(8000 8001 8002 8003 8006)

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
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 "${args[@]}" -- "$SWAP_SSH_HOST"
