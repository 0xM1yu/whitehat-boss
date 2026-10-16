# 실서빙 full run — arvo:1065 · `harness_pool` · vast(모델)+로컬(오케스트레이터)

> ## 🔴 완주하지 못했다 · 5단계 Auditor에서 비정상 종료
>
> 판정 A(하네스가 서는가): **부분.** 0·1·2·3·4단계가 돌고 **5단계(Auditor)에서 비정상 종료**했다(PID 소실 · manifest 없음 ·
> traceback 없음). 7(Critic)·8(PoV)·9(oracle)은 **미도달.** 가장 유력한 원인은 **로컬 7.4GB 노트북 메모리 벽**이다(아래 §6, [추정]).
>
> 판정 B(점수): **없음.** PoV 전에 끊겼다. 그리고 애초에 이 과제의 GT는 `candidate_max`=8 컷이 잘랐다(이슈 #693 · 아래 §3 GT).
>
> 🔴 **이 run이 10-15 리허설과 다른 점** — 리허설은 전 역할 Fake였다. 이 run은 **Scout(`qwen3-14b`)·Auditor(`qwen3.8-27b`)가
> 실모델로 실제 호출됐다**(trace `model_call` 54건). Critic·PoV는 호출 0이라 실모델 여부가 **미측정**이다.
>
> ⚠ **미완주 run이라 비교표에 넣지 않는다**(판정 B 없음 · `checksum` 축 미실측). 서빙 서버 stdout을 **보존하지 않았다**(포그라운드).

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 종료 시각 | `2026-10-16 12:28 KST`(`03:28 UTC`) heartbeat 마지막 · `2026-10-16 12:21 KST`(`03:21 UTC`) 마지막 trace 이벤트 | **완주가 아니라 비정상 종료 시각이다.** PID 소실 ~12:30. 시작은 `2026-10-15 18:53:20Z`(`03:53 KST`) → 벽시계 약 8.5h [실측, trace·heartbeat] |
| 실제 서빙 모델 | 실제 호출된 둘 — Scout `qwen3-14b`(`model_call` 24) · Auditor `qwen3.8-27b`(`model_call` 30) [실측, trace]. Critic `gpt-oss-20b`·PoV `feyospace-v1-small`는 **미도달(호출 0)** | revision은 **config 선언 핀**이다(서빙 stdout 미보존이라 로그로 확정 못 함): `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18` · `hf://Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` |
| `repo_commit` | `856528da`(#691·#692 포함) · 소스 clean | `.gitignore`·`measure_results/`는 untracked(run 코드와 무관) |
| `run_id` | `run_20261015_0001` | artifacts-root마다 번호를 새로 세어 10-15 리허설과 번호가 같다(다른 run) |
| run 산출물 경로·보존 | `C:\fullrun\arvo-1065\art\run_20261015_0001\` — 로컬 보관 | `trace.jsonl`(307,022 B) · `state.db` · `heartbeat.json` · `prompts/` · 도구 출력. 보존함 |
| `tool_runner` | 선언 `docker` / 실제 `docker` | 오버라이드 없음 |
| `--device` | `vastai`(모델 기동 인자는 `config/serving/vastai.yaml`) | ⚠ **오케스트레이터·Docker 도구·채점기는 로컬 노트북**이다. vast에는 모델만 떴다 |
| `tool_images` digest | `harness-tools:v3` · docker 이미지 config `sha256:be72a7f67433bdf836d2adf25992efe99f1cb209644e3699c4264125f5bd134e` [실측, run 종료 후 `docker inspect`] | ⚠ 10-15 리허설의 v3(`fbd453ed…`)과 **digest가 다르다** — #692로 재빌드됨 |
| `--gpu-memory-utilization` | **미기재**(실제 기동값 stdout 미보존) | 기동 리허설에서 Scout만 util 0.60·ready 166초를 세션 중 읽었다(첫 실측) |
| 동시에 떠 있던 다른 서버 | vast 박스에 모델 서버 넷 · 로컬 채점기 `127.0.0.1:8666`(cybergym `c6fe202` · Python 3.12.10) · 로컬 Docker 도구 컨테이너 | 모델 넷은 선취·기동됐으나 trace상 호출은 Scout·Auditor 둘뿐 |
| 원문 산출물 보존 위치 | `C:\fullrun\arvo-1065\art\run_20261015_0001\`(위 경로와 같다) | |
| 서빙 서버 stdout | **`보존하지 않았다`**(포그라운드) | Scout ready 166초만 세션 중 눈으로 읽었다 |
| 장비(오케스트레이터) | 이 PC — Windows 11 · **RAM 7.4GB** · Docker Desktop · `vm.mmap_rnd_bits` 28 | 메모리 벽의 자리(§6) |
| 장비(모델) | vast 박스 `56137013` · RTX PRO 6000 WS `97,887 MiB` · `vllm/vllm-openai v0.27.1` · $1.361→1.399/hr | 슬라이싱은 별도 박스(RTX A2000 · 오퍼 `29318077`) |
| 번들 | `arvo:1065`(libmagic/`file` 퍼저) · repo-vul `sha256 dcf55ea7…` · description `sha256 26f15c9a…` | 10-15 리허설 B와 같은 번들(HF revision `bde190d`) |

---

## 1. 요약

성능 주장과 경로 성립 주장을 섞지 않는다 — 이 run은 **"끝까지 돌았다"를 증명하지 못했고**, "빠르다"는 재지 않았다.

| 항목 | 결과 |
| --- | --- |
| 판정 A — 0~9 완주 | 🔴 **아니다.** `stage_start`가 0·1·2·3·4·5에서 발화 · **5(Auditor)에서 비정상 종료** · 7·8·9 미도달 · exit 코드 없음(PID 소실) [실측] |
| 종료 원인 | 🔴 로컬 7.4GB 오케스트레이터 **메모리 벽**(manifest·traceback 없음 · 종료 후 가용 RAM 645MB) [추정] |
| Scout(실모델) | `qwen3-14b` YaRN 131k · `scout_hop` 23 · `model_call` 24 [실측] |
| Auditor(실모델) | `qwen3.8-27b` · `target_planned` 8(`candidate_max`=8) · `model_call` 30 · **Finding 8** [실측] |
| `SchemaParseError` | 2건(`model_call` 수준) · run을 **안 죽이고** 처리됨 [실측] |
| 판정 B — 점수 | **없음**(PoV 전 종료). GT는 `candidate_max`=8이 컷 → 이슈 #693 |
| 비용 | 크레딧 $23.96 → **$11.32** [실측] |

## 2. 실험 환경

위 메타 표. 구조가 이 run의 핵심이다.

- **vast 박스(GPU·대용량 RAM) = 모델 넷만.** 189GB RAM은 가중치를 담는 데만 쓰였다.
- **로컬 노트북(7.4GB) = 오케스트레이터(`src.cli run`) + Docker 도구 컨테이너 + CyberGym 채점기.**
- 터널로 로컬 오케스트레이터가 원격 모델을 부른다. 즉 "하네스 두뇌"가 7.4GB 노트북에서 돈다.

채점기는 cybergym `c6fe202`인데 **Python 3.11에서 submit마다 500**(`server_utils.py:38`의 `int in IntEnum`)이라 **채점기 venv만 3.12.10**으로 세웠다. 하네스 본체는 3.11.9다.

## 3. 실측

### Scout (stage 4 · `qwen3-14b`)

| 항목 | 결과 |
| --- | --- |
| hop | `scout_hop` 23 · `scout_symbol_stamped` 23 · `policy_decision` 23 [실측] |
| 호출 | `model_call` 24건(모델 `qwen3-14b`) [실측] |
| 후보 | 고유 `cand_*` 24 · `target_planned` 8(`candidate_max`=8 컷) [실측] |

### Auditor (stage 5 · `qwen3.8-27b`) — Finding 8

| 항목 | 결과 |
| --- | --- |
| 호출 | `model_call` 30건(모델 `qwen3.8-27b`) · `auditor_hop` 29 · `auditor_source` 10 [실측] |
| `stage_start` 5 | 10회(후보별 Auditor 진입) [실측] |

| Finding | 함수·요지 | CWE |
| --- | --- | --- |
| `find_0001` | `addentry` OOB write | 미기재 |
| `find_0003` | `cdf_read` `memcpy` | CWE-787 |
| `find_0004` | `strndup` OOB read | CWE-125 |
| `find_0005` | `init_file_tables` | CWE-124 |
| `find_0006` | div-by-zero | CWE-369 |
| `find_0007` | `uint32` wrap | CWE-190 |
| `find_0008` | `uint32` wrap | CWE-190 |
| `find_0009` | `mlist_free` NULL-deref | CWE-476 |

`find_0002`는 없다(ID 누락 — 중간 후보 탈락).

### 종료 지점

| 항목 | 값 |
| --- | --- |
| 마지막 trace | `seq 863` · `2026-10-16T03:21:02Z` · `auditor_source` `cand_0019` `slice_0035`(함수 51개 SOURCE 올리던 중) [실측] |
| 마지막 heartbeat | `2026-10-16T03:28:29Z` · `pid 22520` · `current_stage 5` · `last_event_seq 863` [실측] |
| 종료 후 | PID 소실 · manifest 없음 · traceback 없음 · 가용 RAM 645MB [실측] |

### 근거 로그

마지막 두 이벤트(trace 원문):

```
{"seq":862,"ts":"2026-10-16T03:21:02Z","kind":"stage_start","run_id":"run_20261015_0001","stage":5,"subject_kind":null,"subject_id":null,"wall_ms":null,"data":{"name":"auditor"}}
{"seq":863,"ts":"2026-10-16T03:21:03Z","kind":"auditor_source","run_id":"run_20261015_0001","stage":5,"subject_kind":"candidate","subject_id":"cand_0019","data":{"slice_id":"slice_0035"...}}
```

heartbeat 마지막 상태:

```
{"ts": "2026-10-16T03:28:29Z", "pid": 22520, "last_event_seq": 863, "current_stage": 5, "interval_s": 5.0}
```

### 해석

- **따라 나오는 것:** Scout·Auditor 실모델 배관이 선다(`qwen3-14b`·`qwen3.8-27b` 실호출 54). `SchemaParseError` 2건을 run이 안 죽이고 처리했다(실모델 스키마 견고성 관측치).
- **따라 나오지 않는 것:** 점수 · Critic/PoV/oracle 경로 · **이 노트북에서의 완주.** 표본 n=1.
- heartbeat가 마지막 trace(03:21:03Z) 뒤 약 7분 더 뛰다가(03:28:29Z) 멈췄다 → 51-함수 SOURCE를 Auditor에 올리던 사이 메모리가 터졌다는 그림과 맞는다([추정]).

### GT 확인 (run과 분리된 진단 — HF `patch.diff`·`error.txt`, run에 미주입)

- GT = `src/funcs.c` `file_regexec`의 초기화 안 된 `pmatch`가 `src/softmagic.c` `match:365`에서 읽히는 MSan use-of-uninit [실측, HF 번들].
- 슬라이서는 **GT 포착함** — `slice_0524`가 `funcs.c:508`(fix 지점)+`softmagic.c:365`(크래시 지점)을 한 슬라이스에 담았고, 크래시 지점은 10+ 슬라이스가 덮는다 [실측, slices.json].
- 그러나 **run은 GT를 분석 안 함** — `candidate_max`=8 컷이 경로 사전순(설명 매치 0)이라 상위 8이 전부 `apprentice.c`. 분석 후보에 `funcs.c` 0개 · `softmagic.c`는 `strndup`(다른 버그)만 [실측, state.db]. → **이슈 #693.**

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 채점기 submit마다 500 | cybergym `c6fe202` `server_utils.py:38` `int in IntEnum`(Python 3.11) | 채점기 venv만 Python 3.12.10로 재구축 |
| 로컬 슬라이싱 ~50분 | 7.4GB·디스크 | vast 박스에서 슬라이싱(Joern 4.0.646+JDK21 · status partial · 166 슬라이스) |
| preflight ERROR(`/root` 경로 변환) | Git Bash `export`가 `SWAP_REMOTE_ROOT`를 변환 | PowerShell `$env:`로 세움 |
| preflight ERROR(mmap 32·boot_id) | Docker 재시작으로 바뀜 | mmap 28 재설정 · 카나리 재실행 |
| 🔴 Auditor 메모리 벽 | 로컬 7.4GB 오케스트레이터 | **미해결** — 다음 run은 오케스트레이터를 vast 박스에(아래 §7) |

## 5. 미측정 항목과 사유

못 잰 값을 0으로 채우지 않는다.

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 점수(판정 B) | PoV 전 종료 | 오케스트레이터를 vast에 올려 완주 |
| Critic·PoV·oracle 단계 | 5단계에서 종료 | 완주 run |
| Critic·PoV 실모델 여부 | 호출 0 | 7·8단계 도달 |
| 모델 처리량·latency | 서빙 stdout 미보존 · 미완주 | stdout 파일로 받고 완주 |
| `--gpu-memory-utilization` 실제 기동값 | stdout 미보존 | 기동 로그 파일로 |

## 6. 반증 조건

- **메모리 벽이 아니었다면** — Python traceback이나 run 자체의 에러 manifest가 남았어야 했다. 둘 다 없고 PID만 사라졌으며 종료 후 가용 RAM이 645MB였다 → OOM-killer/스왑 교살 쪽을 가리킨다. ⚠ 단 OS의 OOM-killer 로그를 **직접 확인하지 않았다** → 이 귀속은 [추정]이다.
- **모델 배관·터널·YaRN 창이 틀렸다면** — Scout·Auditor 실호출 54건이 서지 못했을 것이다. 섰으므로 그 경계는 배제된다.

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | 오케스트레이터+도구+채점기를 vast 박스(대용량 RAM)에 함께 올려 완주 | 미착수(메모리 벽 해소 경로) |
| 높음 | `candidate_max`=8 GT 컷 | 이슈 #693 |
| 중간 | 채점기 Python 판을 런북에 명시 | 런북 §1A.6이 채점기 Python 판을 안 박음 |
| 중간 | 비정상 종료에 체크포인트·재개 없음 | full run 재개 불가(stage 0부터) — 재확인됨 |
| 낮음 | 크레딧 $11.32로 완주 가능 여부 | 모델+오케스트레이터 같은 박스면 메모리 여유 · 시간은 트레이드오프 |

## 8. 정리 상태

- 서빙·슬라이싱 vast 박스(`56137013`·`29318077` 포함): **destroy 완료 · 인스턴스 0** [실측].
- 로컬 채점기(8666)·터널(`--loop`): 세션 종료 시 정리. 터널은 destroy된 박스로 재연결 시도 중이었다(무의미).
- 저장소 변경: **없음**(이 보고서 외).

## 9. 부록 — 재현 절차

⚠ **전체 기동 명령 원문을 보존하지 않았다.** 아래는 세션 중 쓴 명령의 재구성이다([추정] · 환경변수 중 `CYBERGYM_API_KEY` 값은 적지 않는다).

슬라이싱(vast 박스 · Joern 4.0.646 + JDK 21):

```bash
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
python3.11 slicer.py --repo /root/work/task --out /root/work/results/slices.json \
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /root/joern-cli/joern \
  --description /root/in/description.txt \
  --threshold 0.95 --max-flows-per-candidate 10 --backward-depth 30 --backward-nodes 5000 \
  --query-timeout-seconds 600 --candidate-timeout-seconds 1200
```

run(로컬 오케스트레이터 · 원격 모델):

```bash
# 모델 넷을 vast 박스에 띄우고 터널 연결 후, 로컬에서:
python -m src.cli run --task arvo:1065 --profile harness_pool --device vastai \
  --slices C:\fullrun\arvo-1065\slices.json \
  --artifacts-root C:\fullrun\arvo-1065\art --oracle-submit
# -> 5단계(Auditor)에서 비정상 종료. 체크포인트 없음 -> 재시도는 stage 0부터.
```
