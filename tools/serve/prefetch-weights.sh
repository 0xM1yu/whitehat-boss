#!/usr/bin/env bash
# 가중치 선취 — 프로필 역할의 모델 가중치를 원격 박스에 미리 받는다(장비 `vastai-remote`).
#
# 사용: SWAP_SSH_HOST=<목적지> [SWAP_SSH_PORT=<포트>] bash tools/serve/prefetch-weights.sh \
#         [--profile harness_pool] [--roles auditor,critic,pov|all] [--dry-run]
#   대상은 `config/models.yaml`의 `source: hf://<repo>@<rev>`다(`file://`은 건너뛴다) — `prefetch_weights.py`가 고른다.
#   원격에서 모델마다 `pip install -q hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 hf download <repo> --revision <rev>`.
#   --dry-run: ssh 없이 원격에서 돌릴 명령만 찍는다(SWAP_SSH_HOST도 필요 없다).
#
# 왜: `config/serving/vastai-remote.yaml`의 `ready_timeout_s`는 가중치가 캐시돼 있다는 전제다 — 콜드 기동은 그 안에
#   안 끝난다(#571 · 2026-10-11 실측). 파이썬은 `PREFETCH_PYTHON`(기본 `python`)이다.
set -euo pipefail
export PYTHONUTF8=1 # Windows 콘솔 코드페이지(cp949)가 한글 오류 메시지를 깨뜨린다

profile=harness_pool
roles=all
dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) profile=${2:?"--profile에 값이 없다"}; shift 2 ;;
    --roles) roles=${2:?"--roles에 값이 없다"}; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *)
      echo "prefetch-weights: 알 수 없는 인자 '$1'" >&2
      exit 2
      ;;
  esac
done

if [ "$dry" -eq 0 ] && [ -z "${SWAP_SSH_HOST:-}" ]; then
  echo "prefetch-weights: SWAP_SSH_HOST가 비었다" >&2
  exit 2
fi

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
pairs=$("${PREFETCH_PYTHON:-python}" "$here/prefetch_weights.py" --profile "$profile" --roles "$roles")

port=()
if [ -n "${SWAP_SSH_PORT:-}" ]; then
  port=(-p "$SWAP_SSH_PORT")
fi

# repo·rev는 파이썬이 `[\w.-]/[\w.-]`·40자 hex로 거른 값이라 원격 셸 인용이 필요 없다.
while read -r repo rev; do
  [ -n "$repo" ] || continue
  remote="pip install -q hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 hf download $repo --revision $rev"
  if [ "$dry" -eq 1 ]; then
    echo "$remote"
  else
    echo "prefetch-weights: $repo@$rev" >&2
    ssh -n -T -o BatchMode=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=12 -o TCPKeepAlive=yes \
      ${port[@]+"${port[@]}"} -- "$SWAP_SSH_HOST" "$remote"
  fi
done <<< "$pairs"
