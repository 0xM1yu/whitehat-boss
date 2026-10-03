#!/usr/bin/env bash
# 원격 사본 설치 — 로컬 HEAD의 교대 몫을 원격 루트에 풀고 `.harness-commit`을 쓴다(장비 `vastai-remote`).
#
# 사용: SWAP_SSH_HOST=<목적지> SWAP_REMOTE_ROOT=<원격 루트> SWAP_REMOTE_PYTHON=<원격 파이썬> \
#         [SWAP_SSH_PORT=<포트>] bash tools/serve/vast-bootstrap.sh
#   로컬에서 돈다. 멱등이다 — 커밋이 바뀔 때마다 다시 돌린다(교대 래퍼가 커밋이 다르면 멈춘다).
#
# 무엇을 보내나 — `git archive HEAD -- src config tools/serve`. 원격 swap.sh가 장비 파일·registry를
#   `src.config`로 읽는 데 필요한 것만이다. 원격에 git 자격증명이 필요 없고, 보낸 것이 로컬 HEAD와 같다.
#   ⚠ 미커밋 변경은 안 간다 — 그런 변경이 있으면 경고만 하고 HEAD를 보낸다.
# 인터프리터 — 원격 SWAP_REMOTE_PYTHON이 `load_serving("vastai-remote")`를 통과하는지 풀어 놓은 사본에서 본다.
#   필요한 것은 pyyaml·pydantic이다(vllm 이미지의 파이썬에는 둘 다 있다 — vllm의 의존성이다). 실패하면 설치하지
#   않고 2로 끝난다 — 공용 박스의 환경을 이 스크립트가 바꾸지 않는다.
# 덮어쓰기 — 원격 루트가 있는데 `.harness-commit`이 없으면(이 스크립트가 만든 곳이 아니면) 건드리지 않고 2로
#   끝난다. 새 사본은 옆 디렉터리에 먼저 풀고 검사를 통과한 뒤에만 바꾼다.
set -euo pipefail

for name in SWAP_SSH_HOST SWAP_REMOTE_ROOT SWAP_REMOTE_PYTHON; do
  if [ -z "${!name:-}" ]; then
    echo "vast-bootstrap: $name이(가) 비었다" >&2
    exit 2
  fi
done

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
COMMIT=$(git -C "$ROOT" rev-parse HEAD)
PATHS=(src config tools/serve)
if [ -n "$(git -C "$ROOT" status --porcelain -- "${PATHS[@]}")" ]; then
  echo "vast-bootstrap: ⚠ ${PATHS[*]}에 미커밋 변경이 있다 — 보내는 것은 HEAD($COMMIT)다" >&2
fi

q() { # 원격 셸용 작은따옴표 인용 — swap-remote.sh와 같다
  printf "'%s'" "${1//\'/\'\\\'\'}"
}

R=$(q "$SWAP_REMOTE_ROOT")
remote="set -e; R=$R; T=\"\$R.incoming\""
remote+='; if [ -e "$R" ] && [ ! -f "$R/.harness-commit" ]; then echo "vast-bootstrap: $R가 있는데 이 스크립트가 만든 곳이 아니다 — 안 건드린다" >&2; exit 2; fi'
remote+='; rm -rf "$T"; mkdir -p "$T"; tar -x -C "$T"'
remote+="; printf '%s\n' $(q "$COMMIT") > \"\$T/.harness-commit\""
remote+="; cd \"\$T\"; if ! $(q "$SWAP_REMOTE_PYTHON") -c 'from src.config import load_serving; load_serving(\"vastai-remote\")'; then echo \"vast-bootstrap: 원격 파이썬이 장비 파일을 못 읽는다 — pyyaml·pydantic을 본다\" >&2; exit 2; fi"
remote+='; cd /; rm -rf "$R"; mv "$T" "$R"; echo "vast-bootstrap: $R ← $(cat "$R/.harness-commit")"'

port=()
if [ -n "${SWAP_SSH_PORT:-}" ]; then
  port=(-p "$SWAP_SSH_PORT")
fi
git -C "$ROOT" archive --format=tar HEAD -- "${PATHS[@]}" |
  ssh -T -o BatchMode=yes ${port[@]+"${port[@]}"} "$SWAP_SSH_HOST" "$remote"
