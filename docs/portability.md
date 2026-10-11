# 하드웨어 독립성

**이 하네스는 특정 장비를 알지 않는다.** 어느 GPU 장비에서든 같은 코드가 돌고, 장비마다
갈리는 것은 설정과 실행 방식뿐이다. 이 문서는 그 성질이 무엇을 뜻하고 무엇으로 확인되는지,
그리고 **아직 지켜지지 않는 항목**이 무엇인지를 적는다.

특정 장비의 접속·잡 실행 절차는 여기 없다. 그건 운용 사항이라
[tools/probe/README.md](../tools/probe/README.md)와 래퍼 스크립트에 있다.
예외 하나 -> Windows에서 원격 교대 실모델 run을 세우는 순서는 §1 「Windows에서 원격 교대 run 절차」.

## 1. 하네스가 장비에 요구하는 것

이 목록이 전부다. 여기 없는 것은 하네스가 신경 쓰지 않는다.

| 요구 | 없으면 | 하네스의 대응 |
| --- | --- | --- |
| Python 3.11.4+ | 아무것도 안 된다 | — |
| OpenAI 호환 엔드포인트 (실서빙 프로필만) | `fake://` 프로필은 그대로 돈다 | 스킴이 어댑터를 고른다. `models.yaml`의 `endpoint` |
| 컨테이너 런타임 (도구 프로필만) | `tool_runner: fake` 프로필은 그대로 돈다 | `doctor`가 PATH를 보고 WARN. `--tool-runner`로 갈아끼운다 |
| GPU | 실서빙·도구 프로필만 필요 | `dev_fake`·`dev_hybrid`는 GPU 없이 0~9단계를 완주한다. 🔴 ⏵ 2026-10-03 — **실제 CyberGym Level 1 번들에서는 완주하지 않는다**(`input_tokens 200,000`을 204,963이 넘겨 `stopped_after: 4`). 합성·소형 타깃 이야기다 — 이슈 [#424](https://github.com/0xM1yu/whitehat-boss/issues/424). ⏵ 2026-10-03 밤 — **닫혔다**: #427이 `dev_fake` 예산의 토큰 상한을 `0`(= 상한 없음)으로 내렸다(위 문장은 `1fa2182`의 사진). 깨끗한 트리 완주 재현은 `dev_fake` 하나다 — 같은 예산을 쓰는 `dev_hybrid` 등의 재현은 이 줄의 근거에 없다 |
| Joern·JDK (실모델 run — 슬라이스를 만들 때만) | 실모델 Scout 좌석의 프롬프트(v3, meta `input: slice`)가 `--slices` 없이 모델에 닿는 run을 시작하지 않는다 — 실모델 run은 슬라이스에 경질 의존이다 | 개발 장비(Windows·WSL)에는 `joern`·`java`가 0건이었다. 슬라이싱은 CPU 장비를 따로 빌려 한다(이슈 #479 (a)(b)) |
| `nvidia-smi --query-gpu`가 **GPU 메모리를 보고할 것** | `mem_gb_ctx8k`를 GB로 못 얻는다 | 사유를 남기고 0으로 둔다. 추정하지 않는다. **PATH에 없는 것과, 있는데 `[N/A]`를 주는 것은 다른 사유로 기록된다** — GB10은 통합메모리라 별도 VRAM이 없어 후자다(2026-08-17 실측). 그 경우 이 쿼리로는 고칠 것이 없다. **`--query-compute-apps`는 같은 장비에서 값을 주지만**(2026-09-03 실측) PID별이고 context 고정이 아니라 이 필드를 바로 채우지 못한다 ([보고서](experiments/harness/2026-09-03-model-swap-sleep-atom.md) §3.4) |

**GPU 아키텍처·드라이버·CUDA 버전·스케줄러·호스트명은 목록에 없다.** 하네스 코드가 그
값들을 읽지 않기 때문이다.

### 실모델 run의 선결조건 (이슈 #479)

2026-10-04 실모델·실도구 run을 세우며 문서가 비어 막혔던 자리다. 코드 결함이 아니라 문서 결함이었다.

- **슬라이스가 먼저다.** 프롬프트 meta가 `input: slice`인 역할이 그 프로필에 있으면(지금은 실모델 Scout
  좌석의 v3) 모델에 닿는 run은 `--slices` 없이 시작하지 않는다(`--dry-run`·낮은 `--stop-after`는 막지
  않는다 — `cli`, ADR 0012 결정 1). 슬라이싱에는 Joern(그 run은 4.0.646)과 JDK(21)가 든다 — 그 run은
  별도 vast 박스에 깔아(그 박스의 사양은 보고서에 근거가 없다) 후보 1,047 → 슬라이스 220을 18.9분에
  만들었다. 슬라이서 장비는 모델 서빙 장비와 별개여도 된다(슬라이스는 파일로 받는다).
- **하네스와 채점 서버(오라클)는 같은 네트워크 namespace에서 돈다.** 오라클은 `127.0.0.1:8666`에만
  바인드한다(`0.0.0.0`은 금지). 이슈 #479 (d)가 보고한 것: WSL2 **기본(NAT) 네트워킹**에서 Windows의
  `127.0.0.1:8666`은 WSL에서 안 닿고, 그 run은 오라클을 WSL 안에 다시 띄우다가 결국 하네스를 Windows로
  옮겨 풀었다. 그리고 WSL에서는 `/mnt/c`의 git worktree를 못 읽어(`.git`이 Windows 절대경로) 원격 교대
  스크립트(`vast-bootstrap.sh`·`swap-remote.sh`가 `git -C "$ROOT" rev-parse HEAD`를 쓴다)를 돌리려면 WSL
  안의 네이티브 clone이 필요하다. ⚠ **이 서술은 이슈 본문의 진술이고 그 로그는 저장소에 없다** — 두 run
  모두 오라클에 닿지 못했다(`vul_submitted: 0`). 저장소 안 보고서가 적는 WSL run `0002`의 실제 실패는
  Docker 소켓이 없는 것이었다(§4).
- ⚠ **미실측**: WSL2 mirrored 네트워킹과 네이티브 Linux에서 위 namespace 문제가 안 생기는지는 **0회 관측**이다(win11·WSL 기본 네트워킹만 했다).
- **이 목록의 접속 가능한 부분은 `cli preflight`(#534)가 한 번에 본다** — 원격 교대 환경변수·bash 해소·터널 포트·원격 커밋·(교대 없는 프로필의) 서빙 포트·채점기 TCP·API 키 모양·카나리 보고서와 현재 Docker VM·과제 번들·도구 이미지의 인덱서. ⚠ 통과가 run의 거부를 전부 되풀이한 것은 아니다(`doctor`도 돌린다). 자동화는 `--strict`로 돌린다 — 건너뛴 점검(WARN)이 있으면 exit 4다. 이 PC에서 실제 원격으로 끝까지 돌려 본 적은 없다(미실측). ⏵ 2026-10-07 — 실 vast 박스에 붙여 셸 셋으로 돌렸다(이슈 #538 본문). 저장소에 남은 결과는 교대 환경변수·bash 해소 두 점검뿐이다(아래).

#### Windows에서 원격 교대 run 절차 (이슈 #538)

장비 `vastai-remote` -> 하네스·도구·오라클은 로컬, 서빙만 vast. 스크립트별 세부는 각 파일 머리 주석.

🔴 **셸 하나로는 preflight를 통과하지 못한다.** 2026-10-07 실 vast 박스, `harness_pool` · main `5828a0a`(이슈 #538 본문):

| 셸 | 교대 환경변수 | bash 해소 |
| --- | --- | --- |
| Git Bash `export` | ERROR -> `SWAP_REMOTE_ROOT`가 `C:/Program Files/Git/root/harness`로 바뀐다 | OK |
| PowerShell `$env:` | OK | ERROR -> `C:\Windows\system32\bash.EXE`(WSL 런처) |
| **PowerShell + `Git\usr\bin`을 PATH 앞에** | **OK** | **OK** |

- 기제 -> MSYS가 네이티브 파이썬에 넘기는 환경변수 값의 POSIX 경로를 바꾼다 · `model_swap._resolved`의 `shutil.which`가 PATH를 앞에서부터 본다(레지스트리 PATH면 System32가 앞).
- 2026-10-08 이 PC 재현 -> 가짜 호스트(`SWAP_SSH_HOST=nohost.invalid`)로 `python -m src.cli preflight --profile harness_pool --device vastai-remote`를 세 셸에서 돌려 위 표와 같은 판정. 원격 커밋 점검은 호스트가 없어 ERROR라 그 너머는 안 봤다.
- Git Bash 대안 -> `MSYS2_ENV_CONV_EXCL="SWAP_REMOTE_ROOT;SWAP_REMOTE_PYTHON"`. 둘 다 빼야 한다. 2026-10-08 이 PC, 두 값 `export` 뒤 `py -3.11`이 받은 `os.environ`:
  - 제외 없음 -> `C:/Program Files/Git/root/harness` · `C:/Program Files/Git/usr/bin/python3`
  - `SWAP_REMOTE_ROOT`만 -> `/root/harness` · `C:/Program Files/Git/usr/bin/python3`
  - 둘 다 -> `/root/harness` · `/usr/bin/python3`
- 같은 날 가짜 호스트 preflight는 둘 다 뺀 형태에서 두 점검 OK. ⚠ preflight는 `SWAP_REMOTE_ROOT`의 모양만 본다(`preflight_remote.py` `check_swap_env`) -> `SWAP_REMOTE_ROOT`만 빼도 OK가 나오고 바뀐 `SWAP_REMOTE_PYTHON`은 못 잡는다. 그 값은 `swap-remote.sh`가 `HARNESS_PYTHON`으로 원격에 넘긴다 -> 원격에서 깨지리라는 것은 코드 읽기이고 실행 확인은 없다. 실 박스·run은 미실측. `MSYS_NO_PATHCONV=1`은 안 쓴다 -> `git -C`가 깨진다(2026-10-04 보고서 §3.3).
- 그래서 1·4는 Git Bash, 5·6은 PowerShell + PATH다. 1·4의 값은 bash 안에서만 쓰여 변환이 안 걸린다(1은 2026-10-07 Git Bash 통과 — #538 본문).

```bash
# Git Bash
# 1) 원격 사본 — 로컬 HEAD를 원격 루트에 푼다. 커밋이 바뀌면 다시(미커밋 변경은 안 간다)
SWAP_SSH_HOST=root@<호스트> SWAP_SSH_PORT=<포트> SWAP_REMOTE_ROOT=/root/harness \
  SWAP_REMOTE_PYTHON=/usr/bin/python3 bash tools/serve/vast-bootstrap.sh
# 2) 원격 감시자 — run마다 하나
ssh -p <포트> root@<호스트> 'cd /root/harness && MAX_HOURS=10 setsid nohup bash tools/serve/guard.sh > /dev/null 2>&1 < /dev/null &'
# 4) 터널 — 별도 창, run 동안 둔다. --loop: ssh가 끊기면 5초 뒤 다시 잇는다
SWAP_SSH_HOST=root@<호스트> SWAP_SSH_PORT=<포트> bash tools/serve/vast-tunnel.sh --loop
```

3) 교대는 손으로 안 한다 -> run이 단계마다 장비 파일 `command`(`swap-remote.sh`)로 원격 `swap.sh`를 부른다. 손으로 띄운 서버가 남으면 다음 run 교대가 ABORT한다([2026-10-04 보고서](experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md) §3.1) -> 원격에서 `HARNESS_RUN_ID=<그 run id> bash tools/serve/swap.sh`로 내린다.

```powershell
# PowerShell — 5·6은 같은 창
$env:PATH = "C:\Program Files\Git\usr\bin;$env:PATH"   # Git 설치 경로의 usr\bin. System32보다 앞
$env:SWAP_SSH_HOST = 'root@<호스트>'
$env:SWAP_SSH_PORT = '<포트>'
$env:SWAP_REMOTE_ROOT = '/root/harness'
$env:SWAP_REMOTE_PYTHON = '/usr/bin/python3'
# CYBERGYM_API_KEY도 이 창에서 $env:로 세운다. 값은 문서·명령문·run 기록에 적지 않는다
# 5) preflight — ERROR가 있으면 exit 2. 0이어야 6으로
python -m src.cli preflight --profile harness_pool --device vastai-remote `
  --task-dir <번들> --oracle-server http://127.0.0.1:8666 --oracle-canary <카나리.json>
# 6) run
python -m src.cli run --profile harness_pool --device vastai-remote --condition B3prime@pipeline `
  --slices <slices.json> --task-dir <번들> --task-work-dir <빈 디렉터리> --artifacts-root <저장소 밖> `
  --oracle-canary <카나리.json> --oracle-submit --oracle-server http://127.0.0.1:8666
```

**run 전에 더하는 것 넷** — 2026-10-11 실서빙 run이 겪은 것이다(그날의 기록은 #605 보고서 §6·§9 — `docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md`, 정본은 여기다).

1. **직결 ssh를 쓴다** — `vastai ssh-url <id>`가 주는 `root@<public_ipaddr>:<direct_port_start>`를 위 `SWAP_SSH_HOST`·`SWAP_SSH_PORT`에 준다. `sshN.vast.ai` 프록시 경유에서는 터널이 두 번 끊겼다. 직결로 바꾼 뒤 3시간 54분 run에서 하네스가 본 전송 오류는 0이었고(`trace.jsonl`) 터널 ssh는 그 사이 한 번 재시작됐다(세션 기록). 같은 run에 `~/.ssh/config`의 `ServerAliveCountMax 12`가 겹쳐 직결 단독 효과는 n=1이고, 프록시가 원인이라는 분리 실험도 없다. ⚠ 직결도 로컬 인터넷 전환은 못 막는다 — `run_20261010_0004`의 내림 ssh가 직결 주소에서 `Connection timed out`이었다(#605 보고서 §6). 그래서 터널은 `--loop`로 띄운다. `vast-tunnel.sh`·`swap-remote.sh`는 `ServerAliveCountMax=12`를 준다(기본 3은 30초 무응답에 터널·교대 ssh가 스스로 끊는다 — 그 예방이다. 위에서 관측된 끊김은 로컬 인터넷 전환 때였고 그 처방은 `--loop`다).
2. **가중치를 선취한다** — 박스를 띄운 뒤 run 전에 `SWAP_SSH_HOST=root@<호스트> SWAP_SSH_PORT=<포트> bash tools/serve/prefetch-weights.sh`(`--dry-run`은 명령만 찍는다). 기본 `--roles`는 좌석 역할 여섯(`scout_memory`·`scout_boundary`·`scout_injection`·`auditor`·`critic`·`pov`)이다 — `vastai-remote`는 `embedding`·`reranker`를 서빙하지 않고, 서빙 좌석은 `seccoderx-8b`·`qwen3-14b`·`qwen3.8-27b`·`gpt-oss-20b`·`feyospace-v1-small` 다섯이다. 그 둘까지 받으려면 `--roles all`이다. ⏵ 2026-10-12 — 기본값이 `all`이었던 판을 바꿨다(sumin 리뷰 #610): `all`은 `original/`·`metal/` 포함 207.69 GB, 좌석 root safetensors는 170.81 GB다 `[계산값 — HF API 파일 크기 합]`. 저장소의 `original/`·`metal/`은 받지 않고(`--exclude`, gpt-oss-20b 41.3 GB 중 27.5 GB `[계산값 — 고정 rev의 HF API 파일 크기 합 · PR #610 검증]`), 받기 전에 원격 `df` 여유가 받을 크기 합보다 작으면 exit 2다(이어받기라 이미 받은 몫 때문에 거부되면 `--skip-df-check`). PoV 좌석(`feyospace-v1-small`)은 약 51 GiB다(10-06 프로브 vLLM 로그 `Model loading took 51.1 GiB` — GPU 로드 크기 · HF 모델 카드 약 55 GB `[출처주장]`), 그 프로브에서 첫(콜드) 기동이 600 s 상한에 걸렸다(n=1). 첫 실패에서 멈춘다(`set -e`) — 다시 돌리면 `hf download`가 이어받는다`[추론 · 미확인]`. 장비 파일의 `ready_timeout_s`는 이 선취를 전제한다 — 이 run에서 선취 없이 `gpt-oss-20b`(상한 300 s)가 콜드 2회 TIMEOUT이었고 따뜻하게는 35 s에 떴다. ⚠ 그 차이가 선취 때문인지는 미검증이다(35 s는 앞 run이 이미 받아 둔 캐시 뒤의 값이라 선취의 효과와 분리되지 않는다 `[추론 — 로그에 다운로드 줄이 없다]`).
3. **오라클 카나리를 이 부팅에서 다시 잰다** — 지금 Docker VM의 `boot_id`가 카나리 JSON의 `host_boot_id`와 다르면 preflight가 ERROR다. 로컬 Docker VM이 다시 부팅됐거나 의심스러울 때 다시 잰다(재기동이 `boot_id`를 바꾸는지는 미실측 · `tools/oracle_canary.py`). `python tools/oracle_canary.py <과제> --out <카나리.json>`(컨테이너 40회, 몇 분)을 이 부팅에서 돌려 그 파일을 `--oracle-canary`에 준다.
4. **`--task-work-dir`는 run마다 빈 디렉터리다** — 앞 run의 해제 트리가 남아 있으면 비어 있지 않다며 exit 2로 거부된다.

`vast-tunnel.sh --loop`의 `STOP_GUARD`는 **로컬 파일**이다 — 이 PC의 `$HOME/harness-serve/STOP_GUARD`(`SERVE_DIR`이 있으면 그 아래)이고 원격 `guard.sh`의 표지와 이름만 같다. 그 표지는 **다음 재연결을 막는다** — 시작 전과 재연결 직전마다 본다. 살아 있는 ssh(터널)는 Ctrl-C로 끝낸다. 옛 표지가 남아 있으면 시작 직후 멈춘다. 재시도 횟수 제한은 일부러 없다(5초 간격).

- 원격 커밋 점검은 `ssh -o BatchMode=yes`다 -> 암호를 묻는 키면 ERROR. 키를 에이전트에 올려 둔다.
- `<slices.json>`의 경로에는 `src-vul/` 접두어가 있어야 한다 -> [slicer-README.md](slicer-README.md) 「`--task-dir`로 쓸 슬라이스」.
- ⚠ 이 절차로 6까지 끝까지 돈 run은 아직 없다 — 미실측. 5의 두 점검까지만 위 표가 근거다. ⏵ 2026-10-11 — 낡았다: 이 절차로 6까지 돌았다. 2026-10-11 실서빙 run 다섯 번 중 하나(`run_20261011_0001`)가 0~9단계를 완주했고 제출은 0건이었다(#605 보고서 §1·§3 — `docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md`).

## 2. 무엇으로 확인되는가

주장이 아니라 돌려서 확인한 것들이다.

| 확인 | 방법 | 현재 |
| --- | --- | --- |
| 실행 코드에 장비 고유명이 없다 | `src/**`를 AST로 파싱해 주석·docstring을 뺀 뒤 장비명·아키·스케줄러 이름을 찾는다 | 0건. 남는 것은 `docker`/`singularity`/`podman`(config가 고르는 런타임 이름)과 ~~`NVFP4`(registry의 양자화 값)~~뿐이고, 둘 다 장비가 아니라 설정을 가리킨다. ⏵ 2026-09-24 — `NVFP4`는 이제 안 남는다: `src/` 문자열 상수(docstring 제외)에 0건이고 registry의 `quantization`에도 없다(가중치는 bf16 그대로 · `gpt-oss-20b`만 MXFP4 — 조민규). 장비명 쪽 판정은 다시 안 쟀다 |
| 전 프로필이 GPU·네트워크 없이 진단을 통과한다 | 모든 프로필에 `doctor` 실행 | ~~8/8~~, ERROR 0건. ⏵ 2026-09-24 — 지금 프로필은 일곱이고 다시 돌려 **7/7 · ERROR 0건**이다(`cli doctor --profile` 일곱, 로스터 밖 정리 뒤) |
| GPU 없이 파이프라인이 완주한다 | `dev_fake`·`dev_hybrid`로 `run` | 완주. 🔴 ⏵ 2026-10-03 — **실 Level 1 번들에서는 `stopped_after: 4`**(#424). 상한을 제거하면 완주한다. ⏵ 2026-10-03 밤 — #427이 그 상한을 내려 **제거 없이 완주한다**(#424 CLOSED — 재현은 그 PR 본문, `dev_fake`) |
| 테스트가 GPU·네트워크 없이 돈다 | CI(`ubuntu-latest`, GPU 없음) | 전 테스트 통과 |
| 프로브가 `pip install` 없이 돈다 | import 그래프를 따라가며 `sys.stdlib_module_names` 밖을 찾는다(`tests/test_probe.py`) | stdlib 밖 0건. `src/__init__.py`까지 따라간다 |
| 측정 절차가 장비 사이에서 같다 | `tools/probe/run_probe.sh` 하나를 두 래퍼가 공유한다 | 래퍼는 잡 실행·접속만 감싼다 |

**주석에 남은 장비명은 의도된 것이다.** `2026-08-14 Seraph 실측`처럼 어떤 사실이 어디서
나왔는지를 밝히는 출처 표기이고, 지우면 수치의 근거가 사라진다. 코드가 그 값을 읽지 않는
한 종속이 아니다.

## 3. 장비에 따라 갈리는 것은 설정이지 코드가 아니다

| 갈리는 것 | 어디서 표현되는가 |
| --- | --- |
| endpoint | `config/models.yaml`의 `endpoint` |
| 컨테이너 런타임 | 프로필의 `adapters.tool_runner` (선언 기준은 아래). 프로필이 적지 못한 장비는 `--tool-runner`로 덮는다 — 실제 사용처는 `dev_docker`를 ATOM에서 돌릴 때다(2026-08-17). 경위는 [decisions/0005](decisions/0005-tool-runner-override.md) |
| 실행 가능한 양자화 | **별도 model key와 별도 프로필.** 오버레이로 `quantization`을 덮지 않는다 — 그건 같은 모델이 아니다 |
| 잡 실행·접속 | 하네스 밖. 래퍼 스크립트와 운용 문서 |

### 프로필은 어느 런타임을 선언하는가

**프로필은 그 프로필을 실제로 돌릴 주 장비의 런타임을 선언하고, 다른 장비가
`--tool-runner`로 덮는다.**

지원 런타임은 셋(docker·singularity·podman)인데 프로필은 하나만 적을 수 있으므로 어느
쪽을 골라도 오버라이드가 남는다. 그러니 질문은 "예외를 없앨 수 있는가"가 아니라
**"어느 장비를 예외로 둘 것인가"**이고, 답은 **주 경로가 예외가 아니어야 한다**이다 —
오버라이드는 [evaluation.md](evaluation.md) §2.3 규칙 2의 예외이고
`RunRecord.tool_runner`에 기록으로 남는다. 주 장비가 상시 예외 상태면 그 기록이
"이 run은 특별했다"를 뜻하지 못하게 된다.

`dev_singularity`가 이미 그 논법이다. Seraph에서 돌리려고 만든 프로필이므로 런타임을
직접 선언하고, 그 장비에서는 오버라이드가 필요 없다.

| 프로필 | 선언 | 주 장비 | 오버라이드가 필요한 곳 |
| --- | --- | --- | --- |
| `harness_pool` · `harness_pool_hybrid` (2026-09-24 전에는 지운 `local_balanced` · `local_balanced_hybrid` · `b1_single`) | `docker` | **ATOM** — 이 프로필들이 부르는 모델이 그 급의 가속기를 요구한다(§5 #1). 다른 장비도 모델을 서빙하지만(§6 Seraph) 이 프로필은 거기서 기동하지 않는다. ⚠ **어느 모델을 부르는지는 로스터가 정하고 그 정본은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이다** — 로스터가 바뀌면 이 칸의 근거도 §5 #1에서 다시 센다 | 없음 |
| `dev_docker` | `docker` | GPU 없는 팀원 노트북 | ATOM에서는 **이제 필요 없다**(2026-08-21 이후 docker 존재). 그전에는 `--tool-runner podman`이었고, 그 사례가 [decisions/0005](decisions/0005-tool-runner-override.md)의 근거다 |
| `dev_singularity` | `singularity` | Seraph | 없음 |

✅ **조건 비교용 실서빙 프로필이 전부 `docker`다(2026-08-28).** 2026-08-17~08-28
사이에는 `local_balanced`·`b1_single`만 `podman`이라 이 표에 두 값이 섞여 있었고,
`tool_images` digest가 런타임 사이에서 비교되지 않으므로(§5 #4) **두 무리를 같은 비교표에
올릴 수 없었다.** 그 벽이 사라졌다. 되돌린 경위와 근거는
[decisions/0005](decisions/0005-tool-runner-override.md)의 마지막 절.

**⚠ 되돌릴 당시의 동기는 지금 문서에 없다.** 그때 `docker`로 맞춘 직접적인 이유는 팀 2×2
실험 프로필 넷과 런타임을 일치시키는 것이었고(ADR 0005 마지막 절이 그 시점 기록으로
남아 있다), **그 넷은 2026-09-01에 트랙째 폐기됐고 2026-09-04에 저장소에서 걷어냈다.** 결론(`docker`로 통일)은 그대로
유효하지만 — ATOM에 docker가 있고 주 실서빙 장비가 ATOM이다 — 근거를 ADR에서 읽을 때
그 넷이 저장소에 없다는 것을 알고 읽어야 한다.

**런타임별로 프로필을 복제하지 않는다.** 조건 축(B1/B3')에 장비 축이 섞이고, 조건이 늘
때마다 장비 수만큼 프로필이 곱해진다([decisions/0005](decisions/0005-tool-runner-override.md)가
기각한 대안이다).

지금은 endpoint가 갈리지 않는다. 서버와 클라이언트를 같은 노드에서 돌리므로 어느 장비에서든
`127.0.0.1`이다. 그래서 `config/`는 한 벌이고 장비별 오버레이가 없다. 이 전제가 깨지면
(원격에서 붙거나, 공용 계정이라 포트가 사용자별로 갈리거나) 다시 판단한다 —
[decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md)의 "후속" 참조.

## 4. 그래도 어디서 돌았는지는 기록한다

하네스가 장비를 모르는 것과, 그 결과가 어느 장비에서 나왔는지 아는 것은 다른 문제다.
**다른 하드웨어에서 잰 수치는 비교 대상이 아니다.**

| 기록 | 값 | 없으면 |
| --- | --- | --- |
| `RunRecord.device` | `--device`가 준 라벨. 없으면 `null` | 같은 프로필로 돈 두 run이 기록만으로 완전히 같아 보인다 |
| `Benchmark.measured_on` | `bench --apply`가 수치와 함께 쓴다 | registry 값이 어느 하드웨어 기준인지 사라진다 |

`--device`는 **명시 라벨 전용이다. 설정을 바꾸지 않고, 호스트명으로 자동으로 채우지도
않는다.** 근거는 [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md).
[evaluation.md](evaluation.md) §2.3 규칙 5가 이 기록 위에 서 있다.

⚠ **`bench --apply`는 잰 값을 `config/models.yaml`에 쓴다**(model-serving.md §3). 어느 역할의
모델에 쓰이는지는 `--profile`이 정하므로, **프로필이 낡은 로스터를 가리키는 동안 `--apply`를
돌리면 폐기 예정 자리에 실측이 박힌다.** 로스터의 정본은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이고
재배선에서 손대는 자리는 그 문서 §3의 넷이다. **아래 세 줄은 커맨드 모양의 예시이지 「이
프로필로 지금 재라」가 아니다** — `--profile` 값은 지금 배선된 것(`config/profiles.yaml`)에서
고른다.

```bash
python -m src.cli run    --profile harness_pool --target <경로> --slices <slices.json> --device atom --condition B3prime@pipeline
python -m src.cli bench  --profile harness_pool --apply --device atom
python -m src.cli doctor --profile harness_pool --device seraph
```

## 5. 이식성 결함 목록

**어떤 환경에서 하네스가 완주하지 못하는 항목이다. 이 표가 비면 목표가 달성된 것이다.**

각 항목은 "어느 장비가 특별한가"가 아니라 **"하네스의 어느 부분이 아직 환경을 가린다"**로
읽는다.

| # | 항목 | 못 도는 환경 | 상태 |
| --- | --- | --- | --- |
| 1 | **실서빙 프로필이 참조하는 모델 일부가 Blackwell 이상을 요구한다.** ⚠ **개수와 모델 이름을 이 칸에 현재형으로 적지 않는다** — 어느 자리에 무엇이 앉는지의 정본은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1, 실제 배선은 `config/profiles.yaml`, 각 모델의 `quantization`은 `config/models.yaml`이고 셋이 다 움직인다. **등재 시점(2026-08-17)의 셈은 `local_balanced` 5개 중 3개**였고 그 셋은 `qwen3-8b`(`fp4`)·`qwen3-coder-30b-a3b`(`nvfp4`)·`devstral-small-2-24b`(`nvfp4`)였다 — **그중 마지막의 `nvfp4`는 2026-09-04에 `fp8`로 반증됐다**(아래). `b1_single`은 세 역할이 전부 한 모델이라는 성질이 그대로다 | Blackwell(sm_100+) 미만 | 미해결. 해법: 같은 모델의 다른 양자화를 별도 model key + 별도 프로필로 둔다. **registry 문제이지 코드 문제가 아니다** **2026-08-25 — 요구 자체는 그대로이고, Blackwell 쪽이 실측됐다.** [NVFP4 보고서](experiments/harness/2026-08-25-serving-nvfp4-atom.md)에서 `qwen3-8b`·`qwen3-coder-30b-a3b` **2종이 sm_121에서 추가 환경변수 없이 떴고** 30B는 구조화 출력까지 정상이다(5/5). **이 항목은 닫히지 않는다** — "못 도는 환경" 칸이 `Blackwell 미만`이고 ATOM은 sm_121 = Blackwell이므로 거기서 뜬 것은 이 항목을 **확인**하지 반증하지 않는다. 닫힌 것은 인접한 다른 불확실성이다: `config/models.yaml`의 `qwen3-4b` 주석이 열어 둔 "다른 양자화(`nvfp4`)도 같은 종류의 백엔드 문제를 따로 만날 수 있다"가 이 2종에 한해 닫혔다(#5의 `VLLM_USE_DEEP_GEMM=0`에 대응하는 NVFP4 쪽 벽은 **없다**). **셋 중 `devstral-small-2-24b`는 여전히 미검증이다** — 받지도 띄우지도 않았고 registry `source`는 `null`이다. **2026-09-04 — 앞 문장의 「띄우지도 않았다」도, 「registry `source`는 `null`이다」도, 이 행이 그 모델에 붙인 `nvfp4`도 반증됐다.** ⚠ **「받지도」는 반증으로 세지 않는다** — 캐시 스냅샷 날짜가 2026-08-25라는 것까지가 관측이고, 그 문장이 걸려 있던 nvfp4 빌드는 지금도 안 받았다([확정 #7](experiments/README.md)). [다섯 상주 보고서](experiments/harness/2026-09-04-five-model-residency-atom.md) §7이 revision `55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128`으로 sm_121에 처음 띄웠고(받아진 것은 2026-08-25), 그 체크포인트의 양자화는 **`fp8`이다** — `config.json`의 `quant_method: 'fp8'` · vLLM 기동 로그 `quantization=fp8` · 파일 크기 24.0 GiB(24B × 1바이트, bf16이면 약 48 GiB). **그러므로 이 행이 세는 「3개」는 다시 세야 한다** — 같은 표 #2가 fp8을 **Ada(sm_89) 이상**으로 세므로 이 모델은 #1이 아니라 #2와 같은 부류다. ⚠ **그렇다고 「Blackwell 미만에서 돈다」가 나오지는 않는다** — 관측은 sm_121 하나뿐이고 Ada에서 돌린 적이 없다. **셋을 둘로 고쳐 행을 옮기는 편집은 이 PR에서 하지 않았다** — 두 행의 「못 도는 환경」 칸이 다르므로(`Blackwell 미만` 대 `Ada 미만`) 항목의 판정 자체를 바꾸는 일이고, 그건 별도 건이다. **`source`·`checksum`은 실험 시점에 비어 있었고 서버를 내린 뒤에 채웠다** — 띄운 것과 해시를 만든 것은 다르고, 서버가 떠 있는 동안 24 GiB를 읽으면 페이지 캐시가 차서 다른 서버가 죽으므로(같은 보고서 §4.3) 순서가 제약이었다. **지금 registry에는 둘 다 들어 있다** — `config/models.yaml`의 `devstral-small-2-24b`이고 manifest는 `config/checksums/devstral-small-2-24b.txt`다. 그날 `doctor` 화면(같은 보고서 §10)이 낸 checksum 자리표시자 WARN은 채워지기 **전**의 출력이다. 기동에 mistral 네이티브 로더 인자(`--tokenizer-mode mistral --config-format mistral --load-format mistral`)가 필요한 것과 이 모델이 멀티모달이라는 것도 같은 절에 있다. **2026-09-09 — 다시 셀 이유가 하나 더 늘었다.** 로스터가 확정되며 실서빙 자리의 모델이 바뀌었고(정본 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1), 이 행이 세는 대상은 **프로필이 실제로 부르는 모델**이다. **그래서 세는 자리는 이 행이 아니라 `config/profiles.yaml` × `config/models.yaml`이다** — 재배선이 끝난 뒤에 세고, 세었으면 그 값이 여기 온다. ⏵ **2026-09-24 — 재배선은 끝났다**(`harness_pool`·`_hybrid`). 그리고 가중치는 bf16 그대로 쓴다(`gpt-oss-20b`만 원래 MXFP4 — 조민규). 두 프로필이 부르는 키의 `quantization`은 `bf16`·`fp16`·`mxfp4`이고 `nvfp4`·`fp4`는 없다(`config/models.yaml`). **이 행의 판정(Blackwell 이상 요구)은 다시 세지 않았다.** |
| 2 | **`single_gpu_12g`의 scout `qwen3-4b`가 `fp8`이다.** FP8 텐서코어는 Ada(sm_89) 이상이다 | Ada(sm_89) 미만 — Ampere 포함 | ~~미해결. **셋 중 진단 비용이 가장 크다**(아래)~~ **2026-09-24 소멸 — 그 프로필과 키를 지웠다**(로스터 밖 정리). 아래 「#2가 #1보다 위험한 이유」는 그때의 서술이다 |
| 4 | `tool_images` digest를 **런타임 사이에서도 아키텍처 사이에서도** 비교할 수 없다. 런타임마다 적는 값의 종류가 다르고(docker=이미지 config sha256 / singularity=SIF 파일 sha256 / podman=RepoDigest), 같은 Dockerfile이라도 아키텍처가 다르면 **다른 이미지**라 digest가 다르다 | 런타임 혼용(docker↔singularity↔podman) **또는** 아키텍처 혼용(x86_64↔aarch64) | [STATUS.md](../STATUS.md) §10. `RunRecord.tool_runner`가 **런타임 축만** 드러낸다 — 아키텍처 축은 기록에 없고 `device` 라벨이 §6 표를 거쳐 간접적으로 가리킬 뿐이다. **두 축 모두 실측으로 뒷받침된다**(아래) |
| 5 | **`qwen3-4b`(`fp8`)가 sm_121에서 `VLLM_USE_DEEP_GEMM=0` 없이 기동하지 않는다.** DeepGEMM이 fp8 scale-factor 레이아웃을 변환하지 못해 가중치 로딩 후처리에서 죽는다(`Unknown SF transformation`, 2026-08-17 ATOM 실측) | ATOM(sm_121) — **플래그 없이** | **해소(기동 인자로).** 플래그를 주면 뜨고, 그 사실과 오진 방향이 `config/models.yaml`의 해당 모델 주석에 있다(⏵ 2026-09-24 — 그 `qwen3-4b` 항목은 로스터 밖 정리로 지웠다 — git 이력과 [ADR 0007](decisions/0007-atom-qwen3-4b-first-measurement.md)). **#2와 반대 방향이라 같은 행에 넣지 않았다** — #2는 "카드가 낡아서" 못 돌고 이쪽은 "카드는 충분히 새로운데 백엔드가 그 레이아웃을 못 다뤄서" 못 돈다. R1(커널 호환성)은 bf16으로 통과했고 양자화 커널은 그 범위 밖이었다 — 이것이 그 첫 사례다 |
| 6 | **`PocLog.write`의 `open(…, "xb")`(`x` = `O_CREAT`+`O_EXCL`)가 Windows에서 끊어진 심링크를 따라간다.** `pov_poc/NNNN.poc.tmp` 자리에 없는 곳을 가리키는 링크가 심겨 있으면 PoC 바이트가 **run_dir 밖 링크 대상에 새 파일로** 쓰이고, `write()`는 **성공 ref·digest를 돌려준다** — `os.replace`가 링크 자체를 rename하므로 `NNNN.poc`도 심링크로 남아 trace의 `poc_ref`·`poc_sha256`이 run_dir 밖을 가리킨다. **살아 있는 링크는 `CREATE_NEW`가 거절한다**(`FileExistsError`) — 구멍은 **끊어진 링크 한 갈래**다 | Windows + **심링크 생성 권한이 있는** 계정. 권한이 없으면 `tests/test_poc_log.py:314`의 그 검사가 `skip`되어 증상이 안 보인다 — **저장소의 초록이 장비 권한에 달려 있다**. CI(`ubuntu-latest`)는 마지막 성공 run `37227620542`(2026-10-04 · `93f78f2`)에서 통과 -> 그 뒤 Actions 미가동(결제) | **해소 ⏵ 2026-10-09 — 선택지 (b)**: 연 뒤 우리 fd(`fstat`)와 경로 말단(`lstat`)의 신원이 다르면 거절하고 우리가 만든 대상 파일만 지운다(`poc_log._refuse_if_followed`). 아래는 등재 때 서술 -> 이슈 [#565](https://github.com/0xM1yu/whitehat-boss/issues/565)이 선택지 (a)~(d)를 든다. **2026-10-09 실측**(Windows 11 10.0.26200 · CPython 3.14.5 · 버리는 프로브 2개): 끊어진 링크에서 `open(…,"xb")`와 `os.open(O_CREAT`+`O_EXCL`+`O_BINARY)`가 **둘 다 따라가고**(살아 있는 링크는 둘 다 거절), `hasattr(os, "O_NOFOLLOW")`는 **False**다 — `poc_log` 모듈 docstring이 적은 *"Windows의 `CREATE_NEW`가 심링크 말단을 거절하는지 … 못 쟀다"*는 이 실측으로 낡았다. **Linux는 다시 재지 않았다** — 그쪽 근거는 그 docstring의 WSL Ubuntu(Python 3.12.3) 실측(둘 다 거절)이다. ⚠ **형제 쓰는 쪽이 안 걸리는 이유는 `O_EXCL`이 아니다** — `fake_tools.write_input`(`src/adapters/fake_tools.py:73`)이 `open` **앞에** `unlink`를 하고 `ContainerToolRunner`도 그 함수를 쓴다. `PocLog.write`에는 그 `unlink`가 없다. 같은 장비에서 링크를 심는 검사 11건 중 **실패는 `poc_log` 하나**다 |

**`#`는 고정 ID다.** 해소된 항목을 지울 때 뒤 번호를 당기지 않는다 — [model-serving.md](model-serving.md) §3이
`#5`를 이름으로 가리킨다(`config/models.yaml`의 `qwen3-4b` 주석도 가리켰는데 그 키는 2026-09-24에 지웠다).
(#3은 R1이었다: ATOM의 로컬 TCP 차단으로 모델을 못 띄우던 항목. 2026-08-17 관리자 예외로
해소됐고, 애초에 하네스가 고칠 수 있는 항목이 아니어서 §6의 관측 기록으로만 남긴다.)

**#4의 "두 축"은 이제 추측이 아니다.** 같은 이미지를 세 런타임에서 만든 digest 실측이 셋
있고, 그중 하나는 아키텍처까지 다르다.

```
docker      sha256:ccb6a6d9969b255f733496bf508ec141cdc547145b8708a6a2ae1e9d44467f12  x86_64 · 이미지 config sha256
singularity sha256:88a0fd4ba8f040e2abd03c8ef03c1ae3645436f702689cdff29057ea03779d20  x86_64 · SIF 파일 sha256
podman      sha256:3a63ba2eb504b1b1fd9378fe5df9b682418b79f86eb2b6650c2ee4c2d635a256  aarch64 · RepoDigest
```

앞의 둘은 **같은 이미지**인데 값이 다르다(런타임 축). 세 번째는 그 위에 아키텍처 축이
겹친 경우다 — ATOM에서 arm64로 따로 빌드했으므로 값이 같을 수가 없다.

**그런데 판정은 같다.** 세 run의 semgrep fingerprint가 일치한다([decisions/0005](decisions/0005-tool-runner-override.md)의
상태 갱신 조건). 즉 **"digest가 다르다"와 "다른 도구로 돌았다"는 다른 말**이고, 지금 기록은
그 둘을 구분해 주지 못한다. 이것이 이 항목이 아직 결함인 이유다.

**#2가 #1보다 위험한 이유.** 프로필 이름과 주석이 **메모리 조건만** 말한다("12GB 카드 1장",
"Qwen3-4B(FP8) 약 8.0GB"). 30B·24B를 쓰는 #1의 프로필은 12GB 카드에서 **크기로도** 걸리니
사람이 원인을 금방 안다. 반면 `single_gpu_12g`는 이름이 정확히 "12GB 카드용"이라 그 카드를
가진 사람이 **고를 프로필**이고, 메모리 계산도 맞고, **세대 때문에** 죽는다. 조건을 다
만족했는데 죽으므로 원인을 찾는 비용이 훨씬 크다.

**양쪽 완주가 끝내 불가능하다고 판명되면 단일 장비로 좁히는 것이 옳은 결론이다** —
반쪽짜리 이중 지원이 최악이다. 지금 그 결정을 내리는 것이 아니라, 그 상황이 왔을 때
무엇이 옳은지를 미리 못박는다.

## 6. 현재 확인된 환경

⏵ 2026-09-29 — **`atom`은 사용이 끝났다**(팀 통보). 아래 `atom` 행은 과거 관측 기록이고, 이후 실측은 vast.ai다([D81](open-questions/D/D81.md)).

이 표는 **하네스의 요구사항이 아니라 관측 기록이다.** 새 환경이 늘면 줄이 늘 뿐,
하네스가 이 값을 읽지는 않는다.

| 라벨 | 아키텍처 | 컨테이너 런타임 | 확인된 것 |
| --- | --- | --- | --- |
| `atom` | Blackwell (sm_121), CUDA 13.0, **aarch64** — NVIDIA DGX OS(Ubuntu 24.04), Arm 20코어. 할당: 통합메모리 128 GB 중 **공식 할당 80 GiB**(34→64(2026-08-25 PL 공식 — cgroup `memory.max`=64와 일치, `memory.high`=58, 2026-08-19·08-20 실측)→80으로 2026-08-31 확장 — cgroup `memory.max`=85,899,345,920=80 GiB 실측 확인(2026-09-01T04:39:13Z 읽기 전용 재확인에서 같은 값). `memory.high`는 **이번 확장과 별개로 8/25~8/26 사이에 58에서 `max`로 이미 풀렸고**(8/25 실측 58 → 8/26·8/28 run이 `max`를 두 번 확인) **소프트 스로틀이 없어 상한에 닿는 순간 예고 없이 OOM kill — 방어선을 직접 잡는다**. 별도 VRAM이 없어 모델과 데이터가 이 안에서 함께 돈다. 공용 계정이라 팀 합산), GPU **공유**, CPU는 `cpu.max=max`라 **강제 없음**(nproc 20 전부 사용 가능), 디스크 1 TiB NVMe Gen5 전용 파티션<br><br>⚠ **2026-08-31에 바뀐 값이다.** 그전까지 이 칸은 "PL 공식 할당 64 GiB, `memory.high`=58"이었고 그것은 **2026-08-19·08-20 실측**이 근거였다. `memory.high`는 그와 별개로 **8/25~8/26 사이에 58→`max`로 풀렸다** — 8/25 실측은 아직 58이었고 8/26·8/28 run이 `max`를 두 번 확인했다. | **docker 29.2.1 + podman 4.9.3**(rootless) 공존. singularity 없음.<br><br>⚠ **2026-08-21에 바뀐 값이다.** 그전까지 이 칸은 "podman만, Docker 데몬 접근 없음"이었고 그것은 **2026-08-17 실측**이 근거였다(`32e2fb5`가 `src/config.py` 주석에, `075544e`가 이 표에 넣었다). 4일 뒤 `ssh atom 'podman --version; docker --version'`이 둘 다 내놓았고, 이후 실행 기록이 그쪽을 따른다 — **2026-08-25·26·27 ATOM run이 전부 docker로 돌았다**(`run_20260827_0004` 완주, `doctor … --tool-runner docker` → `/usr/bin/docker` 확인). **podman으로 도구를 돌린 run은 아래 8/17 한 건뿐이다.** | 2026-08-17 harness-tools:v1 **arm64 현지 빌드** → `dev_docker --tool-runner podman` **완주**(후보 2/Finding 2, `run_20260817_0001`) — 이 저장소의 유일한 podman 도구 실행 기록이다. **semgrep fingerprint가 로컬 x86_64/docker 산출물과 일치** — 아키텍처가 다른 첫 대조다([STATUS.md](../STATUS.md) §5, [decisions/0005](decisions/0005-tool-runner-override.md)). **R1 커널 호환성 통과**: vLLM 0.27.1 + torch 2.13.0+cu130이 `(12, 1)`·`NVIDIA GB10`을 인식. **모델 서빙 확인**: 로컬 TCP 차단이 같은 날 풀린 뒤 `qwen3-4b`(fp8)가 `127.0.0.1:8001`에서 떴다 — `VLLM_USE_DEEP_GEMM=0` 필요(§5 #5), 실측은 `config/models.yaml` |
| `seraph` | Ampere (sm_86), **x86_64**(로컬 x86 이미지 tar를 그대로 변환해 돌린 것이 근거 — STATUS.md §5), CUDA 12.2 상한, vLLM 0.19.1까지 | Singularity 3.7.3 (podman 없음, 2026-08-17 실측) | 2026-08-11 구조화 출력·prefix cache, 2026-08-13 Singularity 도구 실행, 2026-08-14 `/metrics` 원문 |

**`atom`의 로컬 TCP 차단 — 2026-08-17 발생, 같은 날 해소.** 테넌트에서 로컬 주소로의 TCP
접속이 막혀 있었다. 같은 프로세스에서 `bind('127.0.0.1', p)` → `listen` → `connect`가
`ConnectionRefusedError: [Errno 111]`로 떨어졌고(`ss -ltn`에는 LISTEN으로 보이며 `lo`도
UP이었다), `192.168.0.10`은 `[Errno 113] No route to host`였다. 인터넷 아웃바운드는 정상이었다.
vLLM은 `world_size=1`이어도 TCPStore에 자기 자신으로 붙으므로 기동이 안 됐고, 하네스도
`http://127.0.0.1:8001`로 붙으므로 추론 요청이 성립하지 않았다.

**관리자 예외가 적용돼 풀렸다.** 근거는 자기보고가 아니라 실측이다 — `config/models.yaml`의
`qwen3-4b`에 `measured_at: 2026-08-17T15:16:27Z` / `measured_on: atom` / 단일 39.65 tok/s가
들어가 있고(⏵ 2026-09-24 — 그 항목은 로스터 밖 정리로 지웠다 — 값은 git 이력과 [ADR 0007](decisions/0007-atom-qwen3-4b-first-measurement.md)에 있다), 그 값은 서버가 `127.0.0.1:8001`에서 떠서 응답했어야 나온다. **지운 기록이 아니라
남긴 기록인 이유**: 같은 종류의 테넌트에서 또 만날 수 있고, 증상(`ss`에는 LISTEN인데
`connect`가 거부)이 하네스 버그로 오진되기 쉬운 모양이다.

**이 사실을 §5(이식성 결함 목록)에 넣지 않는 것은 의도다.** §5는 "하네스의 어느 부분이
아직 환경을 가리는가"를 적는 곳인데, 이건 하네스가 무엇을 하든 바뀌지 않는 **그 테넌트의
설정**이다. §5에 넣으면 고칠 주체가 우리인 항목과 아닌 항목이 한 표에 섞이고, 그 표가
"이 표가 비면 목표 달성"이라는 뜻을 잃는다. 관측 기록인 여기(§6)가 맞는 자리다.

**`atom`의 페이지 캐시 — 루트 없이 떨어뜨린다 (2026-09-04 실측).** 통합메모리 장비라 모델
파일을 읽어 생긴 리눅스 페이지 캐시가 CUDA 할당과 **같은 풀**을 놓고 다툰다. 안 떨어뜨리면
총량이 남아 보여도 기동이 죽는다 — 여분 7 GiB · 버퍼/캐시 35 GiB인 상태에서 `init_device`의
**첫 CUDA 호출**이 `torch.AcceleratorError: CUDA error: out of memory`로 떨어졌다
([다섯 상주 보고서](experiments/harness/2026-09-04-five-model-residency-atom.md) §4.3).

`drop_caches`는 권한이 없다 — `sudo -n`이 "암호가 필요합니다"를 내고
`/proc/sys/vm/drop_caches`에 직접 쓰면 "허가 거부"다. **파일 단위
`posix_fadvise(POSIX_FADV_DONTNEED)`는 소유자 권한으로 된다.** 그 캐시는 우리가 읽은 모델
파일에서 온 것이라 전부 우리 소유이기 때문이다.

```python
import os, glob
for f in glob.glob('/srv/tenants/ext01/.cache/huggingface/hub/**/*.safetensors', recursive=True):
    fd = os.open(f, os.O_RDONLY)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    os.close(fd)
```

모델 파일 44개에 걸어 `free -g` 기준 버퍼/캐시 **35 → 8 GiB**, 여분 **81 → 108 GiB**였다
(같은 보고서 §6). **기동 사이마다 돌린다** — 모델을 읽을 때마다 캐시가 다시 찬다.
⏵ 2026-09-29 — 교대 명령(`tools/serve/swap.sh`)이 통합메모리 장비(`nvidia-smi` 메모리 `[N/A]`)에서
기동마다 이 절차를 돈다. 전용 VRAM 장비에서는 캐시가 VRAM과 안 다퉈 떼지 않는다.

**`atom` — Auditor(util 0.80)가 떠 있는 동안 Joern(슬라이서)을 돌리지 않는다.** 로딩·배선 조사
(2026-09-24, 저장소 밖) ③36의 규칙이고, 저장소 안의 근거는 2026-09-28 정상 표본 스모크다
([보고서](experiments/harness/2026-09-28-ne-loop-smoke-benign-atom.md)) — Auditor를 포함한 재실행 동안
MemAvailable 최저가 16.2 GiB로 감시자 하한 12 GiB와 4.2 GiB 차였고, Scout 단계에서 슬라이서 둘과 겹친
구간은 28.5 GiB였다. JVM 하나가 그 몇 GiB를 더 쓰면 감시자가 서버를 내린다[추론]. 교대 명령의 기동 전
여유 검사도 Auditor에서 얇다 — 필요량 114.76 GiB(계산값) 대 빈 장비 MemAvailable 실측 114.6~115.4 GiB(다섯
날 — 잰 조건이 같지 않다)라 날에 따라 통과·거부가 갈린다(`config/serving/atom.yaml`의 그 행).
**운영 규약이다 — 코드가 강제하지 않는다.** 슬라이서는 하네스 run 밖에서 돌고(run 입력 `--slices`를
만든다), 교대 명령의 남의-프로세스 검사는 GPU 프로세스(`nvidia-smi` compute-apps)만 본다 — CPU에서 도는
Joern JVM은 안 보인다. 사후 방어선은 감시자(`tools/serve/guard.sh`)의 두 메모리 층이다 — MemAvailable이
12 GiB 밑으로 내려가거나, 상한이 걸린 cgroup(감시자 자신의 cgroup에서 위로 찾는다 — ATOM에서 경로에 걸리면
위 표의 `memory.max` 80 GiB)의 anon이 상한의 90%를 넘으면 Auditor를 포함해 교대 명령이 띄운 서버를 run과 무관하게
전부 TERM → KILL한다. 그 run은 서버를 잃고 멈춘다[추론 — 서버가 죽으면 호출이 `model_gateway_unavailable`로
끝난다].

**이 항목도 §5가 아니라 여기다.** 서버 기동은 하네스 밖이라(그 경계는
[experiments/README.md](experiments/README.md)의 "서빙 서버 stdout을 파일로 받는다" 절이
적는다) 하네스가 고칠 대상이 아니다. 다만 위 TCP 차단과 성격이 하나 다르다 — 그쪽은 **그
테넌트의 설정**이고 이쪽은 **통합메모리 장비의 성질**이라, 같은 부류의 장비가 늘면 거기서도
만난다. 그래도 §5에 안 넣는 이유는 같다: 하네스 코드가 없앨 수 있는 항목이 아니다.

접속·잡 실행·노드 선택 같은 운용 절차는 [tools/probe/README.md](../tools/probe/README.md)에
있다. 그 내용이 이 문서에 없는 것은 의도다 — 하네스가 알 필요가 없는 사실이기 때문이다.

## 7. 관련 문서

- [model-serving.md](model-serving.md) §3 배포 전 검증 체크리스트 · §5 Model Registry
- [evaluation.md](evaluation.md) §2.3 공정성 규칙
- [decisions/0005](decisions/0005-tool-runner-override.md) `--tool-runner` 오버라이드
- [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md) 장비 기록
- [tools/probe/README.md](../tools/probe/README.md) 같은 절차로 `/metrics` 받기, 장비별 운용
