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
# 루트 — SWAP_REMOTE_ROOT는 절대경로이고 `/`로 끝나지 않아야 한다(swap-remote.sh와 같은 규칙). 아니면 ssh 전에 2로
#   끝난다. 상대경로면 원격 가드(로그인 디렉터리 기준)와 삭제(`cd /` 뒤)가 서로 다른 곳을 가리켰고, 뒤 슬래시면
#   `<루트>.incoming`이 루트 안에 생겨 새 사본까지 같이 지워졌다.
# 덮어쓰기 — 원격 루트가 있는데 `.harness-commit`이 40자 hex 한 줄이 아니면(이 스크립트가 만든 곳이 아니면) 건드리지
#   않고 2로 끝난다. 새 사본은 옆 디렉터리 `<루트>.incoming`에 먼저 풀고 검사를 통과한 뒤에만 바꾼다.
#   ⚠ 「만든 곳」의 증거는 그 마커 하나다 — 같은 모양의 마커를 둔 남의 디렉터리는 못 가른다.
#   ⚠ 재실행은 루트를 통째로 갈아끼운다 — 그 아래에 운영자가 둔 것(가중치·캐시 등)은 지워진다. 그런 것은 루트 밖에
#   둔다(교대 상태는 원격 swap.sh의 SERVE_DIR(기본 ~/harness-serve)라 루트 밖이다). `<루트>.incoming`도 이 스크립트
#   몫이라 있으면 묻지 않고 지운다(실패한 이전 실행의 잔재를 치우는 자리다).
#   ⚠ tar 스트림이 멤버 경계에서 정확히 끊기면 tar는 0으로 끝난다 — 파일 수를 대조하지 않으므로 빠진 채 설치되고
#   HEAD 도장이 찍힌다(2026-10-04 `head -c`로 자른 스트림을 `tar -x`에 넣어 실측: 경계는 tar 0·swap.sh 없음, 멤버 중간은
#   tar 2 `Unexpected EOF`). 중간에서 끊기면 `set -e`가 거기서 멈춘다. 경계에서 끊긴 트리는 첫 교대에서
#   `bash tools/serve/swap.sh`가 파일이 없어 실패한다.
set -euo pipefail

for name in SWAP_SSH_HOST SWAP_REMOTE_ROOT SWAP_REMOTE_PYTHON; do
  if [ -z "${!name:-}" ]; then
    echo "vast-bootstrap: $name이(가) 비었다" >&2
    exit 2
  fi
done
case $SWAP_REMOTE_ROOT in
  /*[!/]) ;; # 절대경로이고 `/`로 끝나지 않는다 — `/` 하나와 `//`도 여기서 걸린다
  *)
    echo "vast-bootstrap: SWAP_REMOTE_ROOT이(가) 절대경로가 아니거나 /로 끝난다($SWAP_REMOTE_ROOT) — /root/harness처럼 적는다" >&2
    exit 2
    ;;
esac

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
# 마커 판정 — 40자 hex 한 줄이어야 한다(`$g`가 hex가 아니면 비우고, 길이가 40이 아니면 거부)
remote+='; if [ -e "$R" ]; then g=$(cat "$R/.harness-commit" 2> /dev/null || :); case $g in *[!0-9a-f]*) g= ;; esac; [ "${#g}" -eq 40 ] || { echo "vast-bootstrap: $R가 있는데 이 스크립트가 만든 곳이 아니다 — 안 건드린다" >&2; exit 2; }; fi'
remote+='; rm -rf "$T"; mkdir -p "$T"; tar -x -C "$T"'
remote+="; printf '%s\n' $(q "$COMMIT") > \"\$T/.harness-commit\""
remote+="; cd \"\$T\"; if ! $(q "$SWAP_REMOTE_PYTHON") -c 'from src.config import load_serving; load_serving(\"vastai-remote\")'; then echo \"vast-bootstrap: 원격 파이썬이 장비 파일을 못 읽는다 — pyyaml·pydantic을 본다\" >&2; exit 2; fi"
remote+='; cd /; rm -rf "$R"; mv "$T" "$R"; echo "vast-bootstrap: $R ← $(cat "$R/.harness-commit")"'

port=()
if [ -n "${SWAP_SSH_PORT:-}" ]; then
  port=(-p "$SWAP_SSH_PORT")
fi
git -C "$ROOT" archive --format=tar HEAD -- "${PATHS[@]}" |
  ssh -T -o BatchMode=yes ${port[@]+"${port[@]}"} -- "$SWAP_SSH_HOST" "$remote"
