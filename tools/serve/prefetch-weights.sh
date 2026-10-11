#!/usr/bin/env bash
# 가중치 선취 — 프로필 역할의 모델 가중치를 원격 박스에 미리 받는다(장비 `vastai-remote`).
#
# 사용: SWAP_SSH_HOST=<목적지> [SWAP_SSH_PORT=<포트>] bash tools/serve/prefetch-weights.sh \
#         [--profile harness_pool] [--roles seats|all|<역할,역할…>] [--skip-df-check] [--dry-run]
#   --roles: 기본 `seats` = 프로필의 역할에서 `embedding`·`reranker`를 뺀 것(`vastai-remote`는 그 둘을 서빙하지 않는다).
#     `all`은 그 둘도 받는다.
#   대상은 `config/models.yaml`의 `source: hf://<repo>@<rev>`다(`file://`은 건너뛴다) — `prefetch_weights.py`가 고른다.
#   원격에서 `pip install -q hf_transfer`를 한 번, `hf version`(박스의 hub 버전을 남긴다)을 한 번, 이어 모델마다
#   `HF_HUB_ENABLE_HF_TRANSFER=1 HF_XET_HIGH_PERFORMANCE=1 hf download <repo> --revision <rev> --exclude 'original/*' --exclude 'metal/*'`.
#   hub 1.x는 `hf_transfer`를 안 쓴다(`HF_XET_HIGH_PERFORMANCE`가 그 자리) — 그래서 둘 다 준다. **이 이미지(`vllm/vllm-openai:v0.27.1`)의
#   hub 버전은 미실측**이고(`hf version` 출력이 그 기록이다) 둘의 효과도 이 이미지에서 안 쟀다.
#   `original/`·`metal/`은 같은 가중치를 다른 형식으로 또 든 폴더다(gpt-oss-20b 41.3 GB 중 27.5 GB).
#   받기 전에 원격 `df`의 여유 대 받을 크기 합(`prefetch_weights.py --total-bytes`, HF API)을 비교해 모자라면 exit 2다.
#     이미 받아 둔 몫은 크기 합에서 안 빼므로(상한) 이어받기 때문에 거부되면 --skip-df-check를 준다.
#   --dry-run: ssh 없이 원격에서 돌릴 명령만 찍는다(SWAP_SSH_HOST도, 크기 조회도 필요 없다).
#
# 왜: `config/serving/vastai-remote.yaml`의 `ready_timeout_s`는 가중치가 캐시돼 있다는 전제다 — 300·600 s 좌석에서
#   콜드 기동이 상한을 넘은 적이 있다(gpt-oss-20b ×2 · qwen3.8-27b ×1 · 2026-10-11 KST / feyospace ×1 · 10-06 프로브).
#   좌석별 콜드 실측은 그 파일의 주석이 든다(#571). 파이썬은 `PREFETCH_PYTHON`(기본 `python`)이다.
# keepalive: 여기 ssh는 vast-tunnel.sh·swap-remote.sh와 같은 옵션을 준다. 일회성 ssh인 vast-bootstrap.sh는 일부러 안 준다
#   (멱등이라 끊기면 다시 돌리면 된다).
set -euo pipefail
export PYTHONUTF8=1 # Windows 콘솔 코드페이지(cp949)가 한글 오류 메시지를 깨뜨린다

profile=harness_pool
roles_args=() # 비면 파이썬의 기본(`seats`)이다 - 기본값은 한 자리(prefetch_weights.py)에만 둔다
dry=0
skip_df=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile | --roles)
      if [ $# -lt 2 ]; then
        echo "prefetch-weights: $1에 값이 없다" >&2
        exit 2
      fi
      if [ "$1" = --profile ]; then profile=$2; else roles_args=(--roles "$2"); fi # 빈 값은 파이썬이 거부한다
      shift 2
      ;;
    --dry-run) dry=1; shift ;;
    --skip-df-check) skip_df=1; shift ;;
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
prefetch_py() { "${PREFETCH_PYTHON:-python}" "$here/prefetch_weights.py" --profile "$profile" ${roles_args[@]+"${roles_args[@]}"} "$@"; }
pairs=$(prefetch_py)
# Windows 파이썬의 print는 줄을 \r\n으로 내고 $(...)는 끝 줄바꿈 하나만 지운다 - rev 끝에 \r이 남아 hf가 404를 낸다.
pairs=${pairs//$'\r'/}
# 원격 셸에 인용 없이 넣는 값이라 ssh 전에 전부 한 번 더 거른다(파이썬이 거른 값이어도 파이프를 지났다).
while read -r repo rev; do
  [ -n "$repo" ] || continue
  if ! [[ $repo =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || ! [[ $rev =~ ^[0-9a-f]{40}$ ]]; then
    echo "prefetch-weights: repo/rev 모양이 아니다(rev는 40자 hex): $repo $rev" >&2
    exit 2
  fi
done <<< "$pairs"

port=()
if [ -n "${SWAP_SSH_PORT:-}" ]; then
  port=(-p "$SWAP_SSH_PORT")
fi

ssh_opts=(-n -T -o BatchMode=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=12 -o TCPKeepAlive=yes)
run_remote() { # $1 = 원격 명령. -n: stdin을 안 읽는다(안 그러면 ssh가 아래 반복의 입력을 먹는다)
  if [ "$dry" -eq 1 ]; then
    echo "$1"
  else
    ssh "${ssh_opts[@]}" ${port[@]+"${port[@]}"} -- "$SWAP_SSH_HOST" "$1"
  fi
}
# 캐시 디렉터리(hf가 쓰는 곳)가 놓인 파일시스템의 여유 바이트 - 디렉터리가 아직 없을 수 있어 먼저 만든다
df_cmd='d=${HF_HOME:-$HOME/.cache/huggingface}; mkdir -p "$d" && df -B1 --output=avail "$d" | tail -n 1'

if [ -n "$pairs" ]; then
  run_remote "pip install -q hf_transfer"
  run_remote "hf version"
  if [ "$skip_df" -eq 1 ]; then
    :
  elif [ "$dry" -eq 1 ]; then
    run_remote "$df_cmd"
  else
    need=$(prefetch_py --total-bytes)
    free=$(run_remote "$df_cmd")
    need=${need//[[:space:]]/}
    free=${free//[[:space:]]/}
    if ! [[ $need =~ ^[0-9]+$ ]] || ! [[ $free =~ ^[0-9]+$ ]]; then
      echo "prefetch-weights: 크기를 숫자로 못 읽었다 - 받을 크기 '$need' · 원격 여유 '$free'" >&2
      exit 2
    fi
    if [ "$free" -lt "$need" ]; then
      echo "prefetch-weights: 원격 여유 ${free} B < 받을 크기 합 ${need} B - 디스크가 모자란다(--skip-df-check로 건너뛴다)" >&2
      exit 2
    fi
  fi
fi
while read -r repo rev; do
  [ -n "$repo" ] || continue
  [ "$dry" -eq 1 ] || echo "prefetch-weights: $repo@$rev" >&2
  run_remote "HF_HUB_ENABLE_HF_TRANSFER=1 HF_XET_HIGH_PERFORMANCE=1 hf download $repo --revision $rev --exclude 'original/*' --exclude 'metal/*'"
done <<< "$pairs"
