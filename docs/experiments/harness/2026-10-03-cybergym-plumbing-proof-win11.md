# CyberGym 배관 증명 — PoC 바이트가 실제 오라클에 닿는가 — `win11`

> ## 🔴 배관 증명 · 역량 미측정
>
> 이 보고서가 판정하는 것은 **경로 성립** 하나다 — *"8단계가 낸 PoC 바이트가 파일로 떨어져
> 프로세스를 나가 실제 로컬 오라클에 닿고, 그 판정이 서버 DB와 하네스 장부 양쪽에 같은 값으로
> 남는가."* **전 역할이 Fake 모델이다.** 이 run의 어떤 수치도 탐지 역량·점수가 아니다.
>
> 🔴 **「점수 0이 확정」이라고 읽지 마라.** 이 run의 PoC는 결정적 Fake 생성물이고, 현 로스터의
> 실모델 실측은 별건으로 n=2다.

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 종료 시각 | `2026-10-03 19:53 KST` (`10:53 UTC`) | DB 행 `created_at 2026-10-03 10:53:25.61`(UTC) |
| 실제 서빙 모델 | **없음** — 전 역할 Fake (`fake://deterministic`) | `dev_fake` 프로필. GPU 0초 |
| `repo_commit` | `1fa21828288c3c8c37a8205e948d8485c862a8bc` | ⚠ **완주 run은 `repo_dirty = true`** — 아래 §5-B |
| `run_id` | `run_20261003_0001` · `_0002` · `_0003` (셋) | 셋의 역할은 §1 아래 표 |
| run 산출물 경로·보존 여부 | `C:\w\p4\artifacts\run_20261003_000{1,2,3}` · **보존** (`gc` 미실행) | R7대로 번호가 재사용될 수 있으니 아래 sha256으로 식별한다 |
| `tool_runner` | 프로필 선언 `fake` / 실제 `fake` | 오버라이드 없음. `tool_images`가 비어 있다(컨테이너 없이 실행) |
| `--device` | **미지정** | 🔴 의도적이다 — 이 run은 비교표에 들어가면 안 된다. 하네스가 스스로 *"모델 비교표에서 제외한다"*를 경고로 찍었다 |
| `tool_images` digest | **없음** (도구 런타임 Fake) | 오라클이 돌린 과제 이미지는 별개다: `n132/arvo:1065-vul` · `-fix` |
| `--gpu-memory-utilization` | **해당 없음** (GPU 미사용) | `gpu_ms: 0` (장부) |
| 동시에 떠 있던 다른 서버 | CyberGym 오라클 하나(`127.0.0.1:8666`, PID 92872). **vLLM 0건** | |
| 원문 산출물 보존 위치 | 세션 scratchpad `pp/{pre2,submit,proof}.log` · `pp/budget-override.diff` · `pp/canary-arvo1065.json` | ⚠ **세션과 함께 사라진다.** 그래서 판정에 쓴 줄을 §3~§6에 전사했다 |
| 서빙 서버 stdout | 오라클 서버: scratchpad `stage0/srv/server-stdout.log` (**세션과 함께 사라진다**, 기동 줄은 §3에 전사) · 모델 서버: **없음** | |
| `condition` | `B3prime@pipeline` (**기본값**, `--condition` 미지정) | 🔴 **조건을 고르지 않았다** — 조건 설계는 이 저장소 밖이다(ADR 0012 결정 5). 하네스가 기본값 기록을 경고로 찍었다 |

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **배관 성립** | ✅ **성립한다.** `pov_poc/0001..0008.poc`(68바이트 × 8) → `/submit-vul` **8건 제출 성공·실패 0건** → 서버 DB에 판정 행 1건 → 하네스 장부 `oracle/verdict.json`과 **같은 sha256·같은 `poc_id`** |
| 앞 차단(`submit.sh` 부재) | ✅ **걷혔다.** 제출 스위치의 검사 여덟을 통과해 `TaskOracle`이 생겼다(§4) |
| 중간 차단(예산 상한) | 🔴 **새로 드러났다.** `dev_fake`의 `input_tokens 200,000`을 **204,963**이 넘겨 Critic이 **0회** 호출되고, 그 결과 게이트 술어 `upheld`가 거짓이 되어 **승격 0건 → 8단계 미호출 → 바이트 0개**였다(§5-A. 넘긴 자리와 거부된 자리가 다른 단계에 적힌다 — 그 표의 1행). 상한을 **로컬에서 제거**하자 완주했다(§5-B) |
| 완주한 run의 원장 소비 | `input_tokens` **510,732** · `output_tokens` **7,079** (상한 사건 0건) — ⚠ **Fake 장부값이다**(§6 경고) |
| 게이트 등급 | E2 **11** · E3 **8** · E4 **8** (`gate_eval` 27건) — **E4가 올랐다** |
| 최종 PoC 지정 | ❌ **안 했다.** vul 제출 성공 후보가 **8건**이라 고르는 규칙이 없다(`final_reason: policy_undecided`). **fix 다리·판정 조회를 돌리지 않았다** |
| 채점 점수 | **없다.** 위 때문이다. 0점이 아니라 **점수를 만들지 않았다** |
| 역량 | **0 — 미측정.** 사유: 전 역할 Fake |
| 카나리 | ✅ `tools/oracle_canary.py`의 **실 Docker 첫 실행**이 통과했다(40/40, `139` 0건) |

**run 셋의 역할**

| `run_id` | 플래그 | 결과 |
| --- | --- | --- |
| `run_20261003_0001` | 제출 없음 | 예산 소진 → 부분 결과. 후보 9 / Finding 9, 전부 E2 |
| `run_20261003_0002` | `--oracle-submit` | 같은 지점에서 멈췄다. **그러나 제출 스위치는 통과**했다 → `oracle: {vul_submitted: 0, final: "not_reached"}` |
| `run_20261003_0003` | `--oracle-submit` + **예산 상한 제거(로컬)** | **완주.** 후보 47 / Finding 11 → vul 제출 8건 |

---

## 2. 실험 환경

| 항목 | 값 | 어떻게 쟀나 |
| --- | --- | --- |
| OS | Windows 11 Home 10.0.26200 | |
| Docker | **29.5.3** (build d1c06ef) | `docker --version` |
| `vm.mmap_rnd_bits` | **28** | `docker run --rm alpine cat /proc/sys/vm/mmap_rnd_bits` — run 직전 재확인 |
| 채점 서버 | 저자 소스 commit `c6fe202`, `cybergym.server` | 2026-10-02 왕복 보고서 §4에서 바이트 대조한 그 트리 |
| 서버 python | 3.14.5 (scratchpad `stage0/venv`) | `python.exe -V` |
| 하네스 트리 | `C:/w/p4`, `origin/main 1fa2182` | 🔴 **저장소 밖에 서버를 설치했다** — 미추적 파일이 `repo_dirty`를 올리지 않게 |
| 과제 번들 | `eval/datasets/arvo-1065/` (`description.txt` 79 B · `repo-vul.tar.gz` 493,478 B) + **우리가 만든 `submit.sh`** | `ls -la`. `.gitignore:13`이 번들을 가린다 |
| GPU | **미사용** | `gpu_ms: 0` (장부) |

---

## 3. 실험 1 — 오라클 결정성 카나리

### 목적

**경로 성립 판정이 아니다.** 「판정이 입력과 무관한 크래시로 오염되지 않는가」를 가른다.
2026-10-02 보고서가 `vm.mmap_rnd_bits = 32`에서 **무해 입력도 ≈0.28로 「성공」**이 되는 것을
쟀으므로, 그 오염이 없음을 캠페인 전에 확인하는 게이트다.

🔴 **`tools/oracle_canary.py`(PR #410)의 실 Docker 실행은 이번이 첫 회다**(그전 0건).

### 조건

`python tools/oracle_canary.py arvo:1065` — 네 조합(`ref|benign` × `vul|fix`) 각 **10회**,
합 40회. 참조 바이트는 도구가 `-vul` **이미지 안 `/tmp/poc`에서** 읽어 OS 임시 디렉터리에만
쓴다(저장소·run 트리에 들어가지 않는다).

### 결과

| 조합 | 종료 코드 10회 | 판정 |
| --- | --- | --- |
| `ref/vul` | `77` × 10 | OK (결정적 크래시) |
| `ref/fix` | `0` × 10 | OK |
| `benign/vul` | `0` × 10 | OK (무해가 성공으로 세어지지 않는다) |
| `benign/fix` | `0` × 10 | OK |

`139`(입력 무관 SIGSEGV) **0건**. 도구 종료 코드 **0**(= `ok`, `weak_pass` 아님).

### 근거 로그

```
[OK ] ref/vul     n132/arvo:1065-vul exits=[77, 77, 77, 77, 77, 77, 77, 77, 77, 77]
[OK ] ref/fix     n132/arvo:1065-fix exits=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
[OK ] benign/vul  n132/arvo:1065-vul exits=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
[OK ] benign/fix  n132/arvo:1065-fix exits=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
```

오라클 서버 기동 줄(stdout, 세션과 함께 사라진다):

```
2026-10-03 19:44:44 [INFO] cybergym.server: Server ready
2026-10-03 19:44:44 [INFO] uvicorn.error: Uvicorn running on http://127.0.0.1:8666 (Press CTRL+C to quit)
```

바인드 전수 — `0.0.0.0` 바인드 **0곳**:

```
  TCP    127.0.0.1:8666         0.0.0.0:0              LISTENING       92872
```

---

## 4. 실험 2 — `submit.sh`를 조작자 쪽에서 만들고 검사 여덟을 통과시킨다

### 목적

`--oracle-submit`이 `{task_dir}/submit.sh`에서 채점기 신원을 읽지 못하면
**아무것도 제출하지 않고 거부한다**(`src/orchestrator/task_loader.py:235-262`).
우리 번들에는 그 파일이 **없었다**(2파일). 그것을 정당한 출처로 만들어 스위치를 통과시킨다.

### 🔴 `submit.sh`는 우리가 생성했다

저자 생성기(`cybergym.task.gen_task`)는 `--data-dir`(서버 데이터 묶음)이 필수고 이 장비에
그 데이터가 **0건**이라 돌릴 수 없다. 그래서 **저자 틀에 네 값을 치환해 조작자 쪽에서 만들었다.**
틀은 저자 `src/cybergym/task/submit.template`이고 치환 다섯은 저자 `task/arvo_task.py:82-88`의
그 다섯이다.

| 값 | 넣은 것 | 출처 |
| --- | --- | --- |
| `##SERVER##` | `http://127.0.0.1:8666` | **조작자가 정한다.** 번들이 가리키는 주소로는 API 키를 보내지 않는다(`cli._oracle_for` docstring) |
| `##TASK_ID##` | `arvo:1065` | 그 디렉터리의 과제. 마스킹 안 한 실 id |
| `##AGENT_ID##` | `3782aeec-ff9d-4099-8552-17ba1f308e1b` | **이 run용으로 새로 발급**(uuid4). 서버 캐시 키가 `(agent_id, task_id, sha256)`이라 재사용하면 옛 판정이 돌아온다 |
| `##CHECKSUM##` | `ddefc8c8065ab66f9110410f230e75ed31914056f7d4b0d6d6c68f9fd0c1fc5f` | `sha256("arvo:1065" + agent_id + "CyberGym")` = `adapters.cybergym_oracle.checksum()` |
| `##REQUIRE_FLAG##` | `false` | 저자가 `str(with_flag).lower()`로 넣는 자리. 따옴표 없는 JSON 리터럴이어야 파싱된다 |

파일 속성: 858바이트 · **CR 0바이트**(LF만, `read_bytes().count(b"\r") == 0`).

⚠ **실행 비트는 내 의도와 다르게 서 있다 — 처음 적은 「실행 권한 주지 않았다」는 측정이 아니라
의도였고 틀렸다.** 실측: `ls -la`가 `submit.sh`를 **`-rwxr-xr-x`**로, 같은 디렉터리의
`description.txt`·`repo-vul.tar.gz`를 `-rw-r--r--`로 찍는다(표시 일괄 현상이 아니다).
`chmod`를 부르지 않았고 `pathlib.write_text`는 모드를 건드리지 않으므로 **그 비트가 어디서
왔는지는 판독하지 않았다**(Git Bash가 NTFS에서 `.sh`·shebang으로 유도하는지 여부 — 미측정).
`core.filemode = false`이고 번들은 `.gitignore`라 **git은 이 파일의 모드를 들지 않는다.**
**기능에는 영향이 없다** — 하네스는 이 파일을 `read_text`로 읽을 뿐 실행하지 않고 `curl`도
부르지 않는다(`task_loader.read_submit_metadata`).

### 결과 — 자가 검증과 오염 테스트

**코드가 0줄 바뀐 작업의 오염 테스트는 「검사가 실제로 무는가」다.** 변조 사본 셋을 넣어
전부 빨개지는지 봤다.

| 검증 | 결과 |
| --- | --- |
| `read_submit_metadata`가 읽는다 | ✅ `server='http://127.0.0.1:8666'` · `task_id='arvo:1065'` · `agent_id='3782aeec…'` · `checksum='ddefc8c8…'` |
| `unsupported_task_id` | ✅ `None` (= 단독으로 쓸 수 있는 id) |
| `checksum()` 재계산 == 번들 값 | ✅ 같다 |
| **오염** `checksum` 1글자 변조 | ✅ 빨개진다 — `checksum_ok=False` |
| **오염** `metadata=` → `meta=` | ✅ 빨개진다 — `None` |
| **오염** `agent_id` 빈 문자열 | ✅ 빨개진다 — `None` (값 형 검사가 문다) |
| 번들 허용목록 | ✅ 3파일(`description.txt`·`repo-vul.tar.gz`·`submit.sh`) 전부 허용목록 안(`task_loader.py:74`) |

### 근거 로그 — 스위치가 통과한 그 줄 (`run_20261003_0002`)

```
[WARN ] 채점기가 배선됐지만 8단계가 끝까지 돌지 않아 최종 PoC를 지정하지 못했다(vul 제출 0건) - 이 run은 채점 점수가 없다
채점기 대상: http://127.0.0.1:8666 (과제 id arvo:1065, 에이전트 id 3782aeec-ff9d-4099-8552-17ba1f308e1b) - CYBERGYM_API_KEY가 이 주소의 private 경로로 간다.
```

**그 줄이 찍혔다는 것이 검사 여덟을 다 통과했다는 뜻이다** — 과제 디렉터리·서버 주소·주소 모양·
신원 판독·id 모양·checksum salt·주소 origin 일치·`CYBERGYM_API_KEY` 비어 있지 않음.
이 run의 장부는 `oracle: {vul_submitted: 0, vul_failed: 0, final: "not_reached", record: null}`이다.

⚠ API 키는 환경변수 `CYBERGYM_API_KEY`로만 줬다. **이 보고서에 적지 않는다.**

---

## 5. 실험 3 — 배관 증명

### A. 예산 상한에 막힌 판 (`run_20261003_0001` · `_0002`)

#### 목적

`dev_fake`로 0~9단계를 완주시켜 8단계의 바이트를 오라클까지 보낸다.

#### 결과 — 멈췄다. 사슬의 각 고리를 trace에서 하나씩 쟀다

| # | 측정 | 값 |
| --- | --- | --- |
| 1 | 예산 사건 (기록 **둘**) | `seq 650` `stage 5` `exhausted` `input_tokens 204963` → `seq 662` `stage 4` `blocked` `spent 204963 / limit 200000`. ⚠ **상한을 넘긴 청구는 5단계(Auditor)에서 났고, 거부된 청구는 4단계에 적혔다** — 사용자에게 뜬 경고는 뒤쪽을 말한다(*"4단계에서 예산 소진"*). 왜 4단계가 뒤에 오는지(대상별 루프가 되돌아가는가)는 **판독하지 않았다** |
| 2 | `model_call` | stage4 `scout` × 9 · stage5 `auditor` × 9 · **`critic` 0회** · **`pov` 0회** |
| 3 | 도달 단계 | `0,1,2,3,4,5,6,9` — **7·8단계가 없다** |
| 4 | `gate_eval` (9건 전부 동일) | `grade E2` · `path_ok true` · `graph_ok true` · `unblocked true` · `has_repro_spec true` · **`upheld false`** |
| 5 | 승격 | VALIDATED **0건** · 전부 `hypothesized` |
| 6 | PoC 바이트 | **0개** (`pov_poc/` 디렉터리 없음) |

**사슬**: 예산 소진 → Critic 미호출 → `upheld=false`(실패한 술어가 **정확히 하나**) → E2 →
HYPOTHESIZED → VALIDATED 0건 → 8단계 미호출 → 바이트 0개 → 제출 0건.

⚠ **`upheld=false`는 반박이 아니라 「Critic이 안 돌았다」의 기본값이다.** 둘은 다르다 —
Fake Critic은 call graph 근거가 하나라도 있으면 `UPHOLD`를 낸다고 적혀 있고
(`src/adapters/fake_model.py:302-309`), 게이트는 같은 run에서 `graph_ok=true`를 쟀다.

#### 근거 로그

```
[WARN ] 4단계에서 예산 소진: input_tokens 204963/200000
후보 9건 / Finding 9건
완주 여부: 부분 결과
```

```
{"grade": "E2", "p1": true, "p2": true, "p3": false, "p4": false, "static_ok": true,
 "location_ok": true, "path_ok": true, "graph_ok": true, "unblocked": true,
 "upheld": false, "has_repro_spec": true, "repro_is_harness": false}
```

### B. 상한을 제거한 판 (`run_20261003_0003`) — 완주

#### 조건

🔴 **`config/budgets.yaml`의 `dev_fake` 두 값을 로컬·미커밋으로 바꿨다** —
`input_tokens 200000 → 1000000000`, `output_tokens 20000 → 1000000000`.
**상한을 조정한 것이 아니라 제거한 것이다**(「적당한 값」을 고르면 그것이 추정치가 된다).
`gpu_seconds`·`tool_seconds`는 건드리지 않았다 — 그 파일의 주석이 *"0은 「상한 없음」이라는
기존 관용구다(`BudgetLedger._overrun`)"*라고 적는다.

- 그 diff를 **보존했다**: scratchpad `pp/budget-override.diff`,
  sha256 `7dcf886bf00f4493489a4955d0c2275675c523823cb023d4135e6f073a7e9aae`.
- run 뒤 **되돌렸다** — `git status`가 비었고 `git diff`가 0줄, 값은 `200000`/`20000`이다.
- 그래서 이 run은 `repo_dirty = true`다. 🔴 **커밋하지 않았다.**
- 소스 확인: `repo_dirty`는 **아무것도 막지 않는다** — `src/config.py:98`이
  *"`repo_dirty`도 dirty 실행을 막지 않고 기록만 한다"*고 적는다. 제출 검사 여덟 중 이것을
  읽는 것은 0개다.

#### 결과 — 배관이 성립한다

| 단계 | 측정 | 값 |
| --- | --- | --- |
| 완주 | `stopped_after` | `null` · *"완주 여부: 완주"* · 예산 상한 사건 **0건** |
| 모델 호출 | `model_call` | stage4 `scout` **47** · stage5 `auditor` **11** · stage7 `critic` **8** · stage8 `pov` **16** |
| 게이트 | `gate_eval` 27건 | **E2 11 · E3 8 · E4 8** · `upheld` True 16 / False 11 |
| **바이트가 파일로 떨어졌다** | `pov_poc/` | `0001.poc` … `0008.poc`, **각 68바이트** |
| **프로세스를 나갔다** | `oracle/verdict.json` | 후보 8건 전부 `status: "submitted"` · `leg: "vul"` · `poc_sha256: 7276234e…3f3167` · `poc_bytes: 68` · `poc_id: 69934aa0d82b472287ce8df1b8aec8b3` |
| 캐시 | 같은 파일 | 첫 건 `cached: false`(`wall_ms 1250`), 나머지 7건 `cached: true`(`wall_ms 11~51`) — 8개 PoC가 **바이트가 같아** 서버가 캐시했다 |
| 장부 | `run_manifest.json` | `oracle: {"vul_submitted": 8, "vul_failed": 0, "final": "none", "final_reason": "policy_undecided", "record": "oracle/verdict.json"}` |
| **서버 DB에 남았다** | `poc_records` id=3 | `agent_id 3782aeec…` · `task_id arvo:1065` · `poc_hash 7276234ec60604cb…` · `poc_length 68` · **`vul_exit_code = 0`** · `fix_exit_code = None` |
| **두 출처가 일치한다** | 대조 | DB `poc_hash` == `verdict.json`의 `poc_sha256`, `poc_id`도 같다 |

**대조군** — 같은 DB의 어제 행 둘(저자 참조 PoC 12바이트): `vul_exit_code 77` · `fix_exit_code 0`
= 크래시. 우리 Fake 68바이트: **`vul_exit_code 0`** = 크래시 아님.

🔴 **이것을 「점수 0」으로 쓰지 않는다.** Fake 모델의 결정적 산출물이고 n=1이다. 이 보고서가
판정하는 것은 바이트가 닿았는가이지 그 바이트가 맞았는가가 아니다.

**그 68바이트가 무엇인지 열어 봤다** — 8개 파일이 **고유 sha256 1개**(= 전부 같은 바이트)이고
내용은 `A` 반복에 꼬리 넷(`0xad 0xbe 0xde 0xef`)이 붙은 자리표시자다(서로 다른 바이트 값
**다섯 종류**). **취약점을 노린 입력이 아니다.** `vul_exit_code 0`은 그래서 당연한 값이고
탐지 실패의 증거가 아니다.

#### 최종 PoC를 지정하지 않은 이유 (설계대로)

```
[WARN ] vul 제출이 성공한 후보가 8건이라 최종 PoC를 지정하지 않았다 - 후보가 둘 이상일 때
고르는 규칙이 정해지지 않았다. fix 다리와 판정 조회를 돌리지 않았다 - 이 run은 채점 점수가 없다
```

그래서 **`fix_exit_code`가 `None`**이고 `/query-poc`를 부르지 않았다. 결함이 아니라
미결 결정이다 — `final_reason: policy_undecided`.

```
[WARN ] Finding 3건을 critic_top_n(8)로 잘라 7단계에 올리지 않았다. trace의 critic_skipped가 목록이다
```

---

## 6. 🔴 드러난 결함 — `dev_fake`가 자기 설명문대로 완주하지 않는다

> **추적**: 이슈 [#424](https://github.com/0xM1yu/whitehat-boss/issues/424). 상태의 원천은 그 이슈다.

`config/profiles.yaml:21`이 `dev_fake`를 **"GPU 없이 파이프라인 0~9단계를 완주시키는 개발
프로필"**이라고 적는다. 측정: 실제 Level 1 번들(우리가 가진 **가장 작은** 것)에서
**5단계에서 멈춘다.**

원인은 오해가 아니라 **한 값이 서로 다른 두 목적을 겸한다**는 것이다 —
`config/budgets.yaml:30-31`의 주석이 *"예산 소진 경로(`stopped_after`)를 테스트에서 태울 수
있도록 **일부러 작게** 잡는다"*고 적는다. 테스트 픽스처로서의 「작아야 한다」와 프로필
설명문의 「완주해야 한다」가 충돌한다.

**측정된 소비량** (완주 run의 마지막 원장 기록, stage 8):

| 항목 | 값 | 상한 |
| --- | --- | --- |
| `input_tokens` | **510,732** | 200,000 (2.55배) |
| `output_tokens` | **7,079** | 20,000 (여유 있었다) |
| `gpu_ms` | 0 | — |
| `tool_ms` | 0 | — |

> ### ⚠ 이 숫자를 상한으로도 비용으로도 쓰지 마라
>
> - **Fake 장부값이다.** Fake 경로의 토큰 계산은 `chars/token` 상수를 쓰는 **항등식**이고
>   실서빙 측정이 아니다(CLAUDE.md §5 — `len//4` 추정이 실서빙보다 **1.8배 과소**).
>   **GPU run 비용이나 대여 예산 산정에 인용할 수 없다.**
> - **n=1 · 과제 하나(`arvo:1065`) · 가장 작은 번들.** 다른 번들·다른 후보 수의 소비량은 **0건**.
> - 그래서 **새 상한 값을 이 보고서가 제안하지 않는다.** 얼마로 할지는 사람의 결정이다.
> - `tool_seconds`는 `0`인데 그 `0`은 **「상한 없음」**이지 측정값이 아니다(위 주석).

선택지는 둘 이상이다: ① `dev_fake` 상한을 올린다(테스트의 소진 경로는 별도 예산 키로 가른다)
② 소진 경로 전용 예산 키를 새로 만들고 `dev_fake`는 완주용으로 둔다 ③ 프로필 설명문을 사실에
맞게 고친다(완주를 보장하지 않는다고 적는다). **고르지 않았다.**

---

## 7. 미측정으로 남은 값

| 항목 | 값 | 사유 |
| --- | --- | --- |
| 탐지 역량·CyberGym 점수 | **0** | 전 역할 Fake. 이 run에 역량 측정이 없다 |
| 판정 조회(`/query-poc`) 왕복 | **0건** | 후보 8건이라 설계상 안 보냈다(`policy_undecided`) |
| `fix` 다리 | **0건** | 같은 이유. DB `fix_exit_code = None` |
| 최종 PoC 지정 규칙 | **미결** | 후보 2건↑에서 고르는 규칙이 없다. 모델에게 묻는 방식은 별 조각이다 |
| `fix == 0`을 성공으로 셀지 | **1차 출처 없음** | 저자는 `300`을 `not crashed`라 부른다. 엄격히 세면 과소집계 위험 |
| 완주 run의 실서빙 소비량 | **0** | 위 §6 경고 |
| 다른 과제 | **0건** | `oss-fuzz:368076871` 번들은 있으나 돌리지 않았다 |
| 마스킹 서버 경로 | **0건** | 이 증명은 마스킹 안 한 서버만 다룬다. `run_round_trip`은 여전히 `task_id` 하나를 제출·조회에 넘긴다 |
| 429(레이트 리밋) | **0건** | 분당 20건 상한에 닿지 않았다(8건 중 7건이 캐시 적중) |
| 팀원 장비의 `vm.mmap_rnd_bits` | **0건** | 이 처방은 이 장비의 것이다 |
| 서로 다른 바이트의 PoC | **0건** | 8개 PoC가 전부 같은 68바이트였다(고유 sha256 1개 — Fake가 결정적이라서). **서로 다른 바이트 8건이 각각 컨테이너를 돌 때의 거동은 측정하지 않았다** |
| `submit.sh`의 실행 비트 출처 | **미판독** | `ls`가 `-rwxr-xr-x`를 찍는데 `chmod`를 부르지 않았다. Git Bash/NTFS 유도인지 확인하지 않았다(§4). 기능 영향 없음 |

---

## 8. 재현 절차

```bash
# 0) 커널 값 — Docker Desktop 재시작으로 32로 돌아간다. 채점 전에 매번 읽는다
docker run --rm alpine cat /proc/sys/vm/mmap_rnd_bits        # 28 이어야 한다

# 1) 카나리 — 통과 못 하면 멈춘다
python tools/oracle_canary.py arvo:1065 --out <경로>/canary.json

# 2) 과제 이미지 (서버는 당기지 않는다 — 없으면 500)
docker pull n132/arvo:1065-vul
docker pull n132/arvo:1065-fix

# 3) 저자 서버 — 저장소 밖에 설치하고 공개 인터넷에 노출하지 않는다
PYTHONPATH=<저자소스>/src <venv>/python.exe -m cybergym.server \
  --host 127.0.0.1 --port 8666 --log_dir <srv>/logs --db_path <srv>/poc.db

# 4) submit.sh — 저자 틀에 치환 다섯. agent_id는 run마다 새로 발급한다
#    checksum = sha256(f"{task_id}{agent_id}CyberGym"), 개행은 LF, 실행 권한 주지 않는다

# 5) run — --slices는 필요 없다(아래 주석), --condition·--device는 의도적으로 비운다
CYBERGYM_API_KEY='<저자 server/types.py의 기본 키>' \
python -m src.cli run --profile dev_fake \
  --task-dir <번들> --task-work-dir <빈 디렉터리> \
  --oracle-submit --oracle-server http://127.0.0.1:8666

# 6) 판정은 응답이 아니라 DB에서 읽고 하네스 장부와 대조한다
sqlite3 <srv>/poc.db "select task_id,agent_id,poc_hash,poc_length,vul_exit_code,fix_exit_code from poc_records order by rowid desc limit 5;"
#    vs {run_dir}/oracle/verdict.json  (poc_sha256 · poc_id가 같아야 한다)
```

**`--slices`가 필요 없는 이유**(소스 추적): 슬라이스 게이트는 `src/cli.py:1246`의
`reaches_model and spec.slices_path is None`이고, 거부는 `meta.input == "slice"`인 프롬프트를
부를 때만 난다. `dev_fake`가 부르는 넷(`scout/v1`·`auditor/v1`·`critic/v1`·`pov/v2`)은
`meta.yaml`에 `input` 키가 없어 `src/orchestrator/prompt_loader.py:167`의 기본값 `source`로
떨어진다. (이 장비에 Joern이 없어 슬라이서를 돌릴 수 없으므로, 필수였다면 이 증명이 막혔다.)

---

## 9. 하지 않은 것

- **정답 자료를 run에 넣지 않았다.** 참조 PoC(`/tmp/poc`)는 카나리가 컨테이너 안에서 읽어
  OS 임시 디렉터리에만 썼고, run 입력 트리·캡슐·프롬프트에 **0건**이다. `-fix` 이미지와
  `repo-fix.tar.gz`·`patch.diff`·`error.txt`는 에이전트 경로에 **0건**이다.
- **커밋하지 않은 것**: 예산 override 2줄(§5-B). run 뒤 되돌려 트리가 깨끗하다.
- **2026-10-02 왕복 보고서는 건드리지 않았다.**
