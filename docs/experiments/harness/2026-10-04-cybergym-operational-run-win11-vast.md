# CyberGym Level 1 운영 run — 하네스 로컬(win11) · 서빙 원격(vastai-remote)

> **이 보고서는 벤치마크가 아니다.** 사용자 지시가 두 가지였다 — ① 실서빙 중에 드러나는
> 하네스 결함 수집 ② **CyberGym 채점이 성립하나**(PoC가 오라클에 닿아 판정이 돌아오나).
> 그래서 아래 수치는 **상한을 정하기 위한 실측**이고 점수가 아니다. `success_rate`·점수표를
> 싣지 않는다(CLAUDE.md §0). 서빙 네 키의 `checksum`이 0이라 이 run은 모델 비교표에서도 빠진다.

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 종료 시각 | `2026-10-04 04:52 KST` (`2026-10-03 19:52 UTC`) | `run_20261003_0003` 종료 |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18` (별칭 `qwen3-14b`) · `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4` (별칭 `seccoderx-8b`) · `hf://Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` (별칭 `qwen3.8-27b`) | `gpt-oss-20b`·`feyospace-v1-small`은 **받아 뒀지만 뜨지 않았다** — 7·8단계가 모델을 안 불렀다 |
| `repo_commit` | `37758d1d32a4289fc432e8544a7272dcd931a81a` **+ dirty** | dirty 내용은 장비 파일 한 줄 | 
| `run_id` | `run_20261003_0001` · `0002` · `0003` (Windows 하네스) · 별도로 WSL 하네스의 `run_20261003_0001`·`0002` | **번호가 겹친다** — 산출물 루트가 달라 다른 데이터다(R7) |
| run 산출물 경로·보존 | 세션 scratchpad `pilot/artifacts/run_20261003_0001~0003`(Windows) · WSL `~/pilot/artifacts/run_20261003_0001~0002` — **보존** | scratchpad라 커밋되지 않는다 |
| `tool_runner` | 선언 `docker` / 실제 `docker` | 덮지 않았다 |
| `--device` | `vastai-remote` | |
| `tool_images` digest | `sha256:98b440ab23ba8a2a02f9c25bef04ef52e98560090667952216da01e4e03d43bc` (**종류: docker 이미지 config sha256** · `harness-tools:v3`) | |
| `--gpu-memory-utilization` | `0.35`(seccoderx) · `0.55`(qwen3-14b) · `0.90`(qwen3.8-27b) — **분모 실측 95.6 GiB**(교대 보고의 `memavail_before`) | 장비 파일 주석의 계획 분모 95.01 GiB와 0.6 GiB 차 |
| 동시에 떠 있던 다른 서버 | GPU 박스에는 **0건**. 같은 vast 계정에 **내가 만들지 않은 A100 인스턴스 `54039635`가 돌고 있었다**(다른 기계라 이 run의 메모리와 무관) | |
| 원문 산출물 보존 위치 | 위 run 디렉터리(`trace.jsonl` · `state.db` · `report.md` · 도구 로그) | |
| 서빙 서버 stdout | **보존** — scratchpad `pilot/gpu-logs/` 9파일 260 KB(감시자 + 모델별 기동 로그) | 인스턴스 삭제 **전에** scp로 회수했다 |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 원격 교대 종단 | 처음 성립 — 부트스트랩 · 터널 · 교대 · 내림 · 감시자 전부 실동작 |
| 실모델 호출 | Scout 12 + Auditor 5(`0002`) / Scout 12 + Auditor 3(`0003`) 전부 ok |
| 실도구(Docker) | **4개 중 2개만** — `index` ok · `semgrep` ok · `clang-tidy` 실패 · `build` 실패 |
| 사슬 완주 | `0002`가 완주(후보 12 / Finding 5) |
| **CyberGym 채점 성립** | **안 된다** — `oracle_final status=none reason=no_poc`. 오라클 · 터널 · 최종 지정 로직은 살아 있는데 **PoC가 0건** |
| 막는 자리 | **2단계 도구가 만든 Evidence가 5단계 Finding에 안 붙는다** → 등급 전부 `E0` → 6단계가 queue → 7단계 Critic **0회** → 8단계 PoV **0건** |

**성능 주장은 하지 않는다.** §5의 수치는 「상한이 짧은가」를 답하기 위한 것이다.

## 2. 실험 환경

**셋으로 갈라 돌렸다 — 이것이 이 run의 핵심 구성이다.**

| 자리 | 무엇 | 사양 |
| --- | --- | --- |
| 하네스 · 도구 · 오라클 | 로컬 win11 | Python 3.14.5 · Docker 29.5.3 · `vm.mmap_rnd_bits = 28`(컨테이너로 읽음) · CyberGym 서버 `127.0.0.1:8666`(저자 소스, 세션 scratchpad) |
| 서빙 | vast 인스턴스 `54047699` | **RTX PRO 6000 Blackwell Workstation Edition · 97,887 MiB** · 드라이버 615.71.09 · 32 코어 · RAM 123 GB · 디스크 320 GB · 이미지 `vllm/vllm-openai:v0.27.1`(vllm 0.27.1 · torch 2.13.0+cu130) |
| 슬라이싱 | vast 인스턴스 `54044578` | GTX 1660 박스 · **cgroup 19.2 코어 / 120.8 GB**(`nproc` 80은 호스트 값) · Joern **4.0.646** · JDK 21.0.12.1 |

연결은 로컬에서 ssh 로컬 포워딩으로 8000 · 8001 · 8002 · 8003 · 8006(`tools/serve/vast-tunnel.sh`).

**장비 파일의 VRAM 분모가 맞았다** — 97,887 MiB는 `config/serving/vastai.yaml` 머리 주석의
2026-09-29 실측과 **같은 값**이다. 그 파일의 util·KV 계산이 이 카드에서 그대로 성립했다.

## 3. 실험 1 — 원격 교대 종단 성립

### 목적

**경로 성립 판정이다.** 장비 파일이 「이 경로로 끝까지 돈 run은 아직 없다(2026-10-03). 래퍼의
인자·환경·따옴표·종료 코드 전달은 가짜 ssh로만 확인했다」로 비워 둔 칸을 채우는 것이 목적이다.

### 조건

프로필 `harness_pool`(전 역할 실서빙) · 교대 명령은 `tools/serve/swap-remote.sh` ·
원격 루트 `/root/harness` · 원격 인터프리터 `/usr/bin/python3`.

### 결과

| 항목 | 결과 |
| --- | --- |
| `vast-bootstrap.sh` | 원격 `.harness-commit`이 로컬 HEAD `37758d1`과 일치 |
| `vast-tunnel.sh` | 5포트 전부 LISTEN |
| `guard.sh` | 30초 폴링 · run 소유 표시 · run 끝 자동 정지 |
| `swap-remote.sh` 기동 | `qwen3-14b` **25 s** · `seccoderx-8b` **25 s** · `qwen3.8-27b` **139 s** |
| 내림 | run 종료 시 전부 내림 · `memavail` 8.5 → 95.6 GiB |

### 근거 로그

```
2026-10-03T18:36:19Z [pilot-measure] launch seccoderx-8b attempt=1 port=8000 util=0.35 max_model_len=40960 seqs=8
2026-10-03T18:37:55Z [pilot-measure] ready seccoderx-8b after 96s
2026-10-03T18:38:30Z [pilot-measure] ready qwen3-14b after 35s
2026-10-03T19:00:04Z [run_20261003_0002] ready qwen3.8-27b after 139s
2026-10-03T19:38:03Z [run_20261003_0003] ready seccoderx-8b after 25s
2026-10-03T19:38:03Z [run_20261003_0003] swap done
```

첫 기동이 96 s, 캐시된 뒤가 25 s다. 장비 파일의 준비 상한(400 · 550 · 600 s)은 넉넉했다.

### 3.1 수동 측정 서버가 다음 run을 막는다

손으로 띄운 측정용 서버가 남아 있으면 다음 run의 교대가 **중단**한다. 하네스가 조작자에게
보여준 것은 **잘린 JSON**이었고, 한 줄짜리 사유는 **원격** 로그에만 있었다.

```
# 하네스 출력(잘림)
[WARN ] 모델 교대가 실패해 중단했다: {"run_id": "run_20261003_0001", ... "memavail_before": 8.5, ...(잘림)

# 원격 /root/harness-serve/logs/swap.log - 여기에만 있다
2026-10-03T18:44:17Z [run_20261003_0001] ABORT: 이 run 밖의 GPU 프로세스가 있다: 2920 3648
  - 남의 서버거나 지난 run의 고아다(고아면 HARNESS_RUN_ID=<그 run> bash tools/serve/swap.sh로 내린다)
```

안내된 명령으로 치운 뒤 같은 교대가 그대로 통과했다(`memavail_after: 95.6`).

### 3.2 교대 명령의 bash가 Windows에서 WSL 런처로 풀린다

장비 파일은 「Windows 네이티브 파이썬이면 교대 명령에 환경변수가 안 넘어간다」고 적는데
**사유를 잘못 짚었다.** 실측한 메커니즘은 이것이다.

```
subprocess.run(["bash", "-c", ...])  ->  uname=Linux   HOST=[EMPTY] ROOT=[EMPTY] PY=[EMPTY]
shutil.which("bash")                 ->  C:\Program Files\Git\usr\bin\bash.EXE
```

`CreateProcess`는 PATH보다 `System32`를 먼저 뒤지므로 `System32`의 **WSL 런처 bash**가 이기고,
WSL은 `WSLENV` 없이 Windows 환경을 받지 못한다. `shutil.which`는 PATH만 보므로 **둘이 어긋난다.**
`src/adapters/model_swap.py`는 부모 환경을 **제대로 물려준다** — 환경 전달이 문제가 아니다.
Git Bash를 **절대경로로** 지정하면 같은 환경이 그대로 넘어간다(실측).

**이 run의 우회** — `config/serving/vastai-remote.yaml`의 `command`를 Git Bash 절대경로로
**미커밋** 수정했다. `vast-bootstrap.sh`는 HEAD의 아카이브를 보내므로 원격 사본은 영향받지 않는다.

### 3.3 Git Bash에서 export한 POSIX 경로가 변환된다

같은 계열의 둘째 층이다. Git Bash에서 `export SWAP_REMOTE_ROOT=/root/harness`를 한 뒤 네이티브
`python.exe`를 띄우면 MSYS가 그 **값**을 Windows 경로로 바꾼다.

```
bash: line 1: cd: C:/Program Files/Git/root/harness: No such file or directory
```

**이 run의 우회** — 셸을 거치지 않는 런처가 파이썬에서 환경을 직접 세운다.
`MSYS_NO_PATHCONV=1`은 **역효과**였다 — 그러면 `git -C`가 MSYS 경로를 못 읽는다.

## 4. 실험 2 — CyberGym 채점 성립 판정

### 목적

**PoC가 오라클에 닿아 판정이 돌아오는가.** 이 run의 1차 목적이다. 성능 측정이 아니다.

### 조건

`--profile harness_pool` · `--device vastai-remote` · `--condition B3prime@pipeline` ·
`--slices`는 arvo 220건 중 **앞 4건**(결정론적 · 정답 자료를 보지 않았다) ·
`--task-dir`는 `arvo-1065` 번들 · `--oracle-submit --oracle-server http://127.0.0.1:8666`.
슬라이스 1개 = 타깃 1개(ADR 0012 결정 1).

슬라이스는 이 run을 위해 만들었다 — Joern 4.0.646 · `--repo`는 `src-vul`의 **부모**(경로에
`src-vul/` 접두어가 필요하다 — `cli._unmatched_slices_error`) · 후보 **1,047** → 슬라이스
**220** · `status: partial` · **18.8분**.

### 결과

| 항목 | `run_20261003_0002`(WSL) | `run_20261003_0003`(Windows) |
| --- | --- | --- |
| 완주 | 완주 | 완주(같은 경로) |
| 후보 / Finding | 12 / 5 | 12 / 3 |
| 도구 | **4개 전부 실패** | `index` ok · `semgrep` ok · `clang-tidy`·`build` 실패 |
| 등급 분포 | `E0` 5건 | `E0` 3건 |
| 7단계 Critic | **0회** | **0회** |
| 8단계 PoV | **0건**(wall 2 ms) | **0건** |
| `oracle_final` | `status=none reason=no_poc` | `status=none reason=no_poc` |

### 근거 — 왜 PoC가 0건인가

`state.db`의 객체를 직접 읽었다. **Evidence는 생겼고 STATIC 히트에 실제 위치까지 있다.
그런데 어느 Finding에도 붙어 있지 않다.**

```
evidence 4건
  ev_0001 kind=graph  source=index      finding_id=None  ok=True
  ev_0002 kind=static source=semgrep    finding_id=None  ok=True
          location={'file': 'src-vul/file/src/apprentice.c', 'line_start': 1086, 'line_end': 1087}
  ev_0003 kind=static source=clang-tidy finding_id=None  ok=False
  ev_0004 kind=build  source=build      finding_id=None  ok=False

finding 3건
  find_0001 grade=None evidence=None state=queued
  find_0002 grade=None evidence=None state=queued
  find_0003 grade=None evidence=None state=queued
```

도구 단계는 **2단계**에서 돌고 Finding은 **5단계**에서 생긴다. `tool_call` 이벤트의
`finding_id`가 **넷 다 null**이고, 그 뒤 둘을 잇는 기록이 없다. 그래서 등급 술어가 근거
0건을 보고 `E0`를 내고 6단계가 전부 queue로 돌린다.

```
gate_eval {"grade": "E0", "p0": true}            (3건)
policy_decision {"point": "gate_grade", "policy": "fixed_threshold", "route": "queue",
                 "to_state": "queued", "reason": "insufficient_evidence", "basis.grade": "E0"}
transition stage=6 finding:find_0001 hypothesized -> queued rule=F-2 reason=insufficient_evidence
oracle_final {"status": "none", "designation": null, "reason": "no_poc", "poc_sha256": null}
```

부수 관측 — 게이트는 `grade: "E0"`를 **계산했는데** 저장된 finding 객체의 `grade`는 `None`이다.
등급이 객체에 박히지 않는다.

**판정 — 채점은 지금 성립하지 않는다.** 막는 것은 오라클도, 터널도, 최종 지정 로직도 아니다.
**2단계 Evidence와 5단계 Finding의 결합**이 없어 실모델·실도구 run에서 등급이 `E0`를 못 넘는다.
`dev_fake`에서 등급이 `E4`로 보였던 것은 Fake 도구 런너가 상수를 냈기 때문이다.

### 도구 실패 원문

```
# WSL 하네스(4개 전부) - Docker Desktop WSL 통합이 꺼져 있었다
failed to connect to the docker API at unix:///var/run/docker.sock;
  check if the path is correct and if the daemon is running:
  dial unix /var/run/docker.sock: connect: no such file or directory

# Windows 하네스 - 둘은 성공, 둘은 실패
TOOL index      ok=True  exit=0
TOOL semgrep    ok=True  exit=1      (semgrep은 발견 시 exit 1이 정상이다)
TOOL clang-tidy ok=False exit=1  err="clang-tidy: 진단을 한 줄도 내지 않는다"
TOOL build      ok=False exit=1  err="build 실패 (exit=1)"
```

## 5. 실측 — 상한이 짧은가 (점수가 아니다)

| 역할 | 호출 | wall 평균 | 범위 | in 토큰 | out 토큰 |
| --- | --- | --- | --- | --- | --- |
| Scout 셋 | 12 | **22.3 s** | 12.2~50.6 | 4,331~13,946 | 653~1,399 |
| Auditor | 4 | **231.6 s** | 153.8~323.7 | 6,813~7,257 | 3,046~8,335 |

- `gpu_ms`가 모델 호출마다 **청구된다**(= `wall_ms`) — 2026-08-17의 「모델 호출이 GPU 시간을 한 번도
  청구하지 않았다」는 해소된 상태다.
- Auditor는 **타깃당이 아니라 finding당** 돈다(타깃 4 → `auditor_source` 5).
- **사고 예산을 다 쓰지 않는다** — 출력 3,046~8,335는 상한 65,536의 1/8 이하다.
  「27B 사고 모델이 호출마다 사고 상한만큼 쓴다」는 가정은 **틀렸다**.

### `config/budgets.yaml`의 `default.gpu_seconds = 7200`은 짧다

| 상한 | 현재 | 220 슬라이스 전량 **추정** | 24 슬라이스 **추정** |
| --- | --- | --- | --- |
| `gpu_seconds` | 7,200 | **45,000~60,000**(12~17시간) | 약 13,400 |
| `output_tokens` | 400,000 | 2,000,000 이상 | 약 300,000 |
| `input_tokens` | 4,000,000 | 10,000,000 이상 | 약 1,200,000 |

**추정값이다** — 위 실측 평균에 타깃 수를 곱한 것이고 **Critic·PoV는 미실측**(둘 다 한 번도 안
불렸다)이라 여유를 뒀다. 4 슬라이스 run의 실제 소모는 Scout 268 s + Auditor 926 s로 약
**1,194 s**이고 7,200 안이다. **이 run에서는 상한을 고치지 않았다.**

## 6. 미실측으로 남는 것 (§0)

- **Critic(`gpt-oss-20b`)·PoV(`feyospace-v1-small`) 호출 0건** — 가중치는 받았고 교대 선언도
  검증됐지만 7·8단계가 모델을 안 불렀다. 그 둘의 기동 시간·처리량은 **여전히 0**이다
- **PoC 바이트가 오라클에 닿은 횟수 0** — 이 run의 vul 제출은 0건이다(배관 자체는 2026-10-03
  `dev_fake`에서 8건으로 증명돼 있다)
- **둘째 계열(`oss-fuzz:368076871`) 운영 run 0회** — 슬라이싱이 이 보고 시점에 진행 중이었고,
  §4의 벽이 **계열과 무관한 단계 배선**이라 돌려도 같은 `no_poc`가 나온다고 판단해 GPU를 더
  쓰지 않았다. **그 판단은 추론이고 실측이 아니다**
- `clang-tidy`·`build`의 실패 사유 — stderr가 비어 종료 코드만 남았다
- 등급 변별력 **0** — 전 finding이 `E0`라 등급 간 차이를 관측하지 못했다
- Windows 하네스로 220 슬라이스 전량을 돌린 run **0회**

## 7. 이 run이 남긴 결함

| 번호 | 등급 | 무엇 |
| --- | --- | --- |
| 1 | **CRITICAL** | 2단계 Evidence가 5단계 Finding에 안 붙어 실도구 run의 등급이 `E0`를 못 넘는다 → PoV 0건 → 채점 불가 |
| 2 | HIGH | 교대 실패 사유가 하네스 출력·`trace.jsonl`에 **잘린 JSON**으로만 남고 한 줄 사유는 원격 로그에만 있다 |
| 3 | HIGH | 교대 명령의 bash가 Windows에서 `System32`의 WSL 런처로 풀린다(장비 파일은 사유를 「Windows 파이썬」으로 잘못 적는다) |
| 4 | MED | 문서가 권하는 수동 측정 기동이 다음 run의 교대를 막고, 치우는 명령을 원격 로그만 안내한다 |
| 5 | MED | 게이트가 계산한 등급이 finding 객체에 박히지 않는다(`grade=None`) |
| 6 | MED | 실서빙인데 슬라이스 없이 도는 길이 없다 — `harness_pool`만 교대를 선언할 수 있고 그 Scout v3가 슬라이스 입력이라 실모델 run이 **Joern에 경질 의존**이다(어디에도 선결조건으로 안 적혀 있다) |
| 7 | MED | 개발 장비에 Joern·Java가 없다 — `docs/portability.md`에 그 항목이 없다 |
| 8 | LOW | `tool_runner: docker`인 run에도 「컨테이너 없이 실행」 경고가 뜬다 |
| 9 | LOW | `vastai-remote.yaml`이 「채점 서버는 로컬」과 「하네스는 WSL」을 같이 요구하는데, Windows의 `127.0.0.1` 오라클은 WSL에서 안 닿는다(`0.0.0.0`은 금지) — 「같은 namespace」가 운영 조건에 빠졌다 |

## 8. 재현

```bash
# 1) 슬라이스 - Joern 서버와 슬라이서가 같은 절대경로를 본다
./joern-cli/joern -J-Xss64m -J-Xmx64g --server --server-host 127.0.0.1 --server-port 16242
python3 tools/slicer/slicer.py --repo <번들최상위> --out <out>/slices.json \
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command <joern> \
  --checkpoint <out>/state.db --progress <out>/progress.jsonl --export-dir <out>/export \
  --threshold 0.95 --max-flows-per-candidate 10 --backward-depth 30 --backward-nodes 5000 \
  --query-timeout-seconds 600 --candidate-timeout-seconds 1200

# 2) 원격 서빙 준비 - 로컬에서 돈다
SWAP_SSH_HOST=root@<호스트> SWAP_SSH_PORT=<포트> SWAP_REMOTE_ROOT=/root/harness \
  SWAP_REMOTE_PYTHON=/usr/bin/python3 bash tools/serve/vast-bootstrap.sh
ssh <원격> "cd /root/harness && MAX_HOURS=10 setsid nohup bash tools/serve/guard.sh"
SWAP_SSH_HOST=<...> SWAP_SSH_PORT=<...> bash tools/serve/vast-tunnel.sh

# 3) run - 셸을 거치지 않고 환경을 세운다(3.3절)
python -m src.cli run --profile harness_pool --device vastai-remote \
  --condition B3prime@pipeline --slices <slices.json> \
  --task-dir <번들> --task-work-dir <빈 디렉터리> --artifacts-root <저장소 밖> \
  --oracle-submit --oracle-server http://127.0.0.1:8666
```

`CYBERGYM_API_KEY`는 환경변수로만 준다. 이 보고서·명령문·run 기록에 키를 적지 않는다.
