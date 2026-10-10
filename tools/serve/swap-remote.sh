#!/usr/bin/env bash
# 원격 교대 — 하네스는 로컬, 서빙은 원격 박스(vast 등) (ADR 0009 「2026-09-29 제안」 절 6의 2026-10-03 노트).
#
# 사용: swap-remote.sh [KEY...]
#   장비 파일 config/serving/vastai-remote.yaml의 `command`다. 받은 KEY를 그대로 들고 원격 박스에서
#   tools/serve/swap.sh를 돌린다 — 교대 계약(src/adapters/model_swap.py 「명령과의 계약」)은 원격의 swap.sh가
#   지키고, 이 파일은 그 stdout·stderr·종료 코드를 ssh로 그대로 돌려준다. 감시자·pid·메모리 판정도 원격이다.
#
# 환경 — 비면 ssh를 열지 않고 2로 끝난다(모르는 호스트·경로로 가지 않는다).
#   SWAP_SSH_HOST       ssh 목적지(`user@host` 또는 ~/.ssh/config의 Host 별칭)
#   SWAP_REMOTE_ROOT    원격 저장소 루트(vast-bootstrap.sh가 채운 곳). 절대경로이고 `/`로 끝나지 않는다 —
#                       아니면 이것도 ssh 전에 2로 끝난다. vast-bootstrap.sh가 같은 값을 같은 규칙으로 거른다
#                       (상대경로는 로그인 디렉터리 기준과 `/` 기준이 갈려 가드가 안 본 곳을 지웠다)
#   SWAP_REMOTE_PYTHON  원격 swap.sh가 장비 파일·registry를 읽을 인터프리터(pyyaml·pydantic)
#   HARNESS_RUN_ID      하네스가 준다. 원격 swap.sh가 자기 run 서버를 가르는 값이다
#   SWAP_SSH_PORT       (선택) ssh 포트. vast는 보통 22가 아니다
#   HARNESS_DEVICE · SWAP_DEADLINE_S는 하네스가 준 값을 그대로 넘긴다. HARNESS_PYTHON은 넘기지 않는다 —
#   로컬 인터프리터 경로라 원격에서 뜻이 없다. 그 자리를 SWAP_REMOTE_PYTHON이 채운다.
#
# 커밋 고정 — 원격 루트의 .harness-commit(vast-bootstrap.sh가 쓴다)이 로컬 `git rev-parse HEAD`와 다르면
#   원격에서 swap.sh를 부르지 않고 2로 끝난다. 장비 파일 검사(`check_serving`)는 로컬 사본으로 하고 기동은
#   원격 사본으로 하므로, 둘이 갈리면 검사가 본 값과 띄운 값이 다르다. ⚠ 커밋만 본다 — 로컬의 미커밋 변경은
#   못 가른다(그 run은 `repo_dirty`로 비교표에서 빠진다).
#
# 따옴표 — 원격 셸에 가는 값은 전부 작은따옴표로 감싸고 값 안의 작은따옴표는 '\''로 끊는다. 원격 로그인 셸이
#   POSIX 셸이면 그대로 읽힌다. KEY·run id에 공백·따옴표·$가 들어도 한 인자로 간다(tests/test_remote_swap.py).
#
# Windows 네이티브 파이썬 — ~~`bash`가 WSL 런처로 풀리고 환경변수가 안 넘어간다. 하네스를 WSL·Linux 안에서
#   돌린다~~ ⏵ 2026-10-04 정정(#477): 환경 전달은 멀쩡했다. 원인은 실행 파일 해소다 — `CreateProcess`가 PATH보다
#   `System32`를 먼저 뒤져 `bash`가 WSL 런처(System32 아래의 `bash.exe`)로 풀렸고, WSL은 `WSLENV` 없이 Windows
#   환경을 못 받아 변수가 비었다. 이제 `model_swap`이 `command[0]`을 `shutil.which`로 먼저 푼다.
#   ⚠ 남는 함정 하나 — Git Bash에서 `export SWAP_REMOTE_ROOT=/root/harness`를 하고 네이티브 파이썬을 띄우면 MSYS가
#   그 **값**을 Windows 경로(`C:/Program Files/Git/root/harness`)로 바꾼다. 교대 환경 변수는 셸을 거치지 않고
#   세운다(PowerShell `$env:` 또는 파이썬 런처). `MSYS_NO_PATHCONV=1`은 역효과다 — `git -C`가 MSYS 경로를 못 읽는다.
#   ⏵ 2026-10-05 (#495 리뷰) — `which`는 PATH를 **앞에서부터** 본다. `Git/usr/bin`이 System32보다 **앞에** 있는 PATH
#   (Git Bash로 띄운 셸)에서만 Git Bash가 잡혀 환경이 그대로 넘어가고, 그보다 앞에 없는 PATH(레지스트리 PATH —
#   PowerShell·cmd)에서는 같은 런처가 잡힌다. 그때는 **교대를 시도할 때** `ModelSwapError`로 실패한다 — 교대면 run이
#   `model_swap_failed`로 멈추고 run 끝 내림이면 경고로 남으며, 교대하지 않는 run(`--dry-run`·낮은 `--stop-after`)은
#   영향이 없다(그 run의 run 끝 내림이 경고 한 줄과 `model_swap ok=false` 이벤트 한 줄을 남긴다).
#   `SWAP_SSH_HOST`이 비었다 같은 엉뚱한 사유로 실패하는
#   대신 원인을 바로 말한다. 풀려면 Git Bash로 하네스를 띄우거나 PATH 앞에 `Git/usr/bin`을 두거나 장비 파일
#   `command[0]`에 bash 절대경로를 적는다(추적 파일을 고치면 `repo_dirty`로 그 run은 비교표에서 빠진다 — 실험
#   run에서는 앞의 둘을 쓴다). 막는 것은 System32의 `bash.exe`뿐이다 — WindowsApps의 별칭은 안 막고, PATH에서
#   `bash`를 못 찾아 `which`가 못 풀면 못 막는다(`CreateProcess`는 System32를 먼저 본다). `doctor --device`는
#   이 판정을 안 한다. 이 변경 뒤에 Windows 네이티브로 원격 교대를 끝까지 돌린 run은 없다 —
#   미실측(`_resolved`의 두 PATH 해소만 2026-10-05 이 PC에서 쟀다).
#   ⏵ 2026-10-08 (#538) — 위 풀이 하나만 따르면 다른 쪽이 ERROR다(Git Bash `export` -> 환경변수 · PowerShell -> bash
#   해소). 통과한 조합과 순서는 docs/portability.md 「Windows에서 원격 교대 run 절차」.
# ⚠ 하네스가 마감에 이 프로세스를 죽여도 원격 swap.sh는 자기 마감(SWAP_DEADLINE_S)까지 돈다 — ssh를 TTY 없이
#   열어 HUP이 안 간다. 계약상 swap.sh는 그 마감에 스스로 끝난다.
set -euo pipefail

need() {
  if [ -z "${!1:-}" ]; then
    echo "swap-remote: $1이(가) 비었다 — $2" >&2
    exit 2
  fi
}
need SWAP_SSH_HOST "원격 교대 호스트를 모른다"
need SWAP_REMOTE_ROOT "원격 저장소 루트를 모른다"
need SWAP_REMOTE_PYTHON "원격 하네스 인터프리터를 모른다"
need HARNESS_RUN_ID "하네스가 안 줬다(Windows면 머리 주석의 실행 파일 해소 항목 — 이 bash가 WSL 런처일 수 있다)"
case $SWAP_REMOTE_ROOT in
  /*[!/]) ;; # 절대경로이고 `/`로 끝나지 않는다 — `/` 하나와 `//`도 여기서 걸린다
  *)
    echo "swap-remote: SWAP_REMOTE_ROOT이(가) 절대경로가 아니거나 /로 끝난다($SWAP_REMOTE_ROOT) — /root/harness처럼 적는다" >&2
    exit 2
    ;;
esac

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
COMMIT=$(git -C "$ROOT" rev-parse HEAD 2> /dev/null) || {
  echo "swap-remote: 로컬 커밋을 못 읽었다($ROOT) — 원격 사본과 대조할 수 없다" >&2
  exit 2
}

q() { # 원격 셸용 작은따옴표 인용 — 값 안의 ' 는 '\'' 로 끊는다
  printf "'%s'" "${1//\'/\'\\\'\'}"
}

remote="cd $(q "$SWAP_REMOTE_ROOT") || exit 2"
remote+="; want=$(q "$COMMIT"); got=\$(cat .harness-commit 2> /dev/null)"
remote+="; [ \"\$got\" = \"\$want\" ] || { echo \"swap-remote: 원격 커밋(\${got:-없음})이 로컬(\$want)과 다르다 — vast-bootstrap.sh를 다시 돌린다\" >&2; exit 2; }"
remote+="; exec env HARNESS_RUN_ID=$(q "$HARNESS_RUN_ID")"
remote+=" HARNESS_DEVICE=$(q "${HARNESS_DEVICE:-}")"
remote+=" HARNESS_PYTHON=$(q "$SWAP_REMOTE_PYTHON")"
if [ -n "${SWAP_DEADLINE_S:-}" ]; then
  remote+=" SWAP_DEADLINE_S=$(q "$SWAP_DEADLINE_S")"
fi
remote+=" bash tools/serve/swap.sh"
for key in "$@"; do
  remote+=" $(q "$key")"
done

port=()
if [ -n "${SWAP_SSH_PORT:-}" ]; then
  port=(-p "$SWAP_SSH_PORT")
fi
# -n: stdin을 안 읽는다 · -T: TTY 없음 · BatchMode: 암호를 묻지 않는다(하네스는 프롬프트에 답할 수 없어
# 마감까지 매달린다). 호스트 키 검사는 사용자의 ssh 설정을 따른다 — 여기서 끄지 않는다.
exec ssh -n -T -o BatchMode=yes -o ServerAliveInterval=10 ${port[@]+"${port[@]}"} -- "$SWAP_SSH_HOST" "$remote"
