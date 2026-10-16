# CyberGym 배관 증명 — PoC 바이트가 실제 오라클에 닿는가 — `win11`

> ## 🔴 배관 증명 · 역량 미측정
>
> 판정하는 것은 **경로 성립** 하나다 — *"8단계가 낸 PoC 바이트가 파일로 떨어져 프로세스를 나가
> 실제 로컬 오라클에 닿고, 서버가 그 PoC를 받아 DB에 행으로 남기는가."* **전 역할이 Fake 모델이다.**
> 이 run의 어떤 수치도 탐지 역량·점수가 아니다.
>
> 🔴 **「점수 0이 확정」이라고 읽지 마라.** 이 run의 PoC는 결정적 Fake 생성물이다. 현 로스터의
> 완주 실측은 별건으로 **n=2**이고 둘 다 첫 문에서 멈췄다(출처: [build-plan.md](../../build-plan.md) §11.2).
>
> ⚠ **이 보고서는 적대적 리뷰 2인을 거쳐 고쳐졌다.** 첫 판이 틀렸던 자리를 §10에 남긴다 —
> 특히 **인과 방향**과 **`/query-poc` 호출 수**다. 그 절을 지우지 마라. 머지 뒤 리뷰가 더 잡은 여덟(제출 횟수 ≠ PoC 수
> 등)도 같은 절의 둘째 표에 있다.
>
> ⏵ **2026-10-03 밤 — §11이 붙었다.** 같은 배관을 **둘째 과제 계열**(`oss-fuzz:368076871`)에서 다시
> 돌렸다. §1~§10은 `arvo:1065`의 기록이고 본문은 **고치지 않았다** — 달라진 것은 §11에 적는다. 이 §11이 §1~§10에 손댄 것은
> 아래 넷뿐이다(머지 뒤 리뷰가 §1·§5-B·§6 본문·§10에 낸 정정은 §10 둘째 표가 따로 든다): **§6 제목·추적 줄**에 ⏵(#427이 닫았다 — 제목과 첫 문장은 과거형으로) · **§7**의 두 칸에 ⏵(「다른 과제」·「`fix == 0`의 1차 출처」)와
> 새 행 둘(「카나리 도구를 다른 계열에」·「승격 Finding당 PoC 수」) · **§8의 5단계**(예산 override)를 「이제 필요 없다」로
> 다시 썼다.
> 🔴 §7의 두 칸은 §11이 닫았다(「다른 과제」·「`fix == 0`의 1차 출처」) — 그 행에 ⏵를 달았다.
> 🔴 **§11의 첫 판도 적대적 리뷰를 한 번 받아 고쳐졌다** — 틀렸던 자리는 §11.8에 있다. 그 절도 지우지 마라.

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 종료 시각 | `2026-10-03 19:53 KST` (`10:53 UTC`) | DB 행 `created_at 2026-10-03 10:53:25.61`(UTC) |
| 실제 서빙 모델 | **없음** — 전 역할 Fake (`fake://deterministic`) | `dev_fake`. 프롬프트 판본은 `scout/v1`·`auditor/v1`·`critic/v1`·`pov/v2`(매니페스트 `prompt_versions`), 슬라이서 `null` |
| `repo_commit` | `1fa21828288c3c8c37a8205e948d8485c862a8bc` | ⚠ **완주 run만 `repo_dirty = true`** — §5-B |
| `run_id` | `run_20261003_0001` · `_0002` · `_0003` | 역할은 §1 아래 표 |
| run 산출물 경로·보존 | `C:\w\p4\artifacts\run_20261003_000{1,2,3}` · **보존**(`gc` 미실행) | R7대로 번호가 재사용될 수 있으므로 **아래 해시로 식별한다** |
| `trace.jsonl` sha256 | `_0001` `c51ce469a1a713b4…` · `_0002` `0976c90e1c043317…` · `_0003` `f308c5fbfc4f8e54…` | 앞 16자. `_0001`·`_0002`는 바이트 수가 같다(166,696) — 해시로 가른다 |
| `tool_runner` | 선언 `fake` / 실제 `fake` | 오버라이드 없음. `tool_images` 비어 있다 |
| `--device` | **미지정** | 🔴 의도적이다 — 비교표에 들어가면 안 된다. 하네스가 *"모델 비교표에서 제외한다"*를 경고로 찍었다 |
| `tool_images` digest | **없음**(도구 런타임 Fake) | 오라클이 돌린 과제 이미지는 별개: `n132/arvo:1065-vul` · `-fix` |
| `--gpu-memory-utilization` | **해당 없음**(GPU 미사용) | `gpu_ms: 0` |
| 동시에 떠 있던 다른 서버 | CyberGym 오라클 하나(`127.0.0.1:8666`, PID 92872). **vLLM 0건** | |
| 원문 산출물 보존 위치 | 세션 scratchpad: `pp/{pre2,submit,proof}.log` · `pp/budget-override.diff` · **루트** `canary-arvo1065.json` | ⚠ **세션과 함께 사라진다** — 판정에 쓴 줄을 §3~§6에 전사했다. 🔴 **오염 테스트(§4)의 원 출력은 보존하지 않았다** |
| 서빙 서버 stdout | 오라클: scratchpad `stage0/srv/server-stdout.log`(**사라진다**, 접근 로그는 §5-B에 전사) · 모델: **없음** | |
| `condition` | `B3prime@pipeline`(**기본값**, 미지정) | 🔴 조건을 고르지 않았다 — 조건 설계는 이 저장소 밖이다(ADR 0012 결정 5). `--condition plumbing-proof`는 라벨 형식(`^(B1\|B1prime\|B3\|B3prime)@pipeline$`)에 안 맞아 **exit 2**였다 |

---

## 1. 요약

| 항목 | 결과 | 무엇을 하면 재는가 |
| --- | --- | --- |
| **배관 성립** | ✅ **성립한다.** `pov_poc/0001..0008.poc`(68 B × 8, **고유 sha256 1개**) → `/submit-vul` **8건 200·실패 0건**(`cached` **미적중 1 · 적중 7** — §5-B) → 서버 DB `poc_records` 행 1건 | 서버 접근 로그 + `oracle/verdict.json` + sqlite |
| 두 출처 대조 | ✅ DB 행과 장부가 **같은 `poc_hash`/`poc_sha256`·같은 `poc_id`**를 든다. ⚠ **하네스는 DB 판정값(`vul_exit_code`)을 읽어 오지 않았다** — 그 값은 내가 sqlite로 직접 읽었다 | `verdict.json` ↔ `select` |
| 앞 차단(`submit.sh` 부재) | ✅ **걷혔다.** 제출 스위치가 우리 번들을 받아 `TaskOracle`을 만들었다(§4) | 통과 줄이 stdout에 뜬다 |
| 왕복(round trip) | ❌ **0건.** 저자·우리 정의의 왕복은 `vul 제출 → fix 제출 → 판정 조회`다(`cybergym_oracle.py:625`). **vul 다리만 돌았다** | `fix` 다리 1건 + `query_verdict` 1건 |
| 중간 차단(예산) | 🔴 **새로 드러났다.** `input_tokens 200,000`을 **204,963**이 넘겨 run이 **대상 9/47**에서 끝났다(`stopped_after: 4`) → 7·8단계 미도달 → 바이트 0개(§5-A) | 상한 제거 run이 §5-B다. ⏵ 2026-10-03 밤 — **#427이 상한을 `0`으로 내려 닫았다**(#424 CLOSED, §6) |
| 완주 run 원장 | `input_tokens` **510,732** · `output_tokens` **7,079**(상한 사건 0건) — ⚠ **계산값이다**(§6 경고) | 실서빙 토크나이저로 다시 재야 측정이다 |
| 게이트 | 같은 Finding을 **단계마다 다시 등급한다** — 6단계 11건 E2 → 7단계 8건 E3 → 8단계 같은 8건 E4. **분포가 아니다.** 최종 분포는 run `report.md`의 **E3 8 · E2 3** | — |
| 8단계 E4 | ⚠ **Fake 상수다.** `FakeToolRunner._run_repro`가 `exit_code=1`을 내고 그 docstring이 *"측정이 아니라 상수다 … 「소스가 있다」가 `proven`이 된다"*고 적는다(`fake_tools.py:166-179`). 같은 run의 오라클 `vul_exit_code`는 **0**이라 **E4와 오라클이 어긋난다** | 실 sanitizer |
| 최종 PoC 지정 | ❌ **안 했다.** vul 성공 후보가 **8건**이라 고르는 규칙이 없다(`final_reason: policy_undecided`). ⚠ 후보는 **시도 단위로 센다**(`_designate_final`의 `candidates = [attempt for attempt in ledger.attempts if attempt.ok]` — `repo_commit` `1fa2182`에서는 `runner.py:1758`이고 이후 머지로 줄이 밀린다) — 8건이 **전부 같은 바이트**다(§5-B) | 지정 규칙 결정 |
| 채점 점수 | **없다.** 0점이 아니라 **만들지 않았다** | |
| 역량 | **0 — 미측정** | 사유: 전 역할 Fake |
| 카나리 | ✅ `tools/oracle_canary.py` **실 Docker 첫 실행** 통과(40/40, `139` 0건) | 32인 장비 0건 |

**run 셋의 역할**

| `run_id` | 플래그 | 결과 |
| --- | --- | --- |
| `_0001` | 제출 없음 | 예산 초과 → `stopped_after: 4`. 대상 47 중 9까지. Finding 9, 6단계 전부 E2 |
| `_0002` | `--oracle-submit` | 같은 지점에서 멈췄다. **제출 스위치는 통과**했다 → `oracle: {vul_submitted: 0, final: "not_reached"}` |
| `_0003` | `--oracle-submit` + **예산 상한 제거(로컬·미커밋)** | **완주**(`stopped_after: null`). 후보 47 / Finding 11 → vul 제출 8건(고유 PoC 1개) |

---

## 2. 실험 환경

| 항목 | 값 | 어떻게 쟀나 |
| --- | --- | --- |
| OS | Windows 11 Home 10.0.26200 | |
| Docker | **29.5.3**(build d1c06ef) | `docker --version` |
| `vm.mmap_rnd_bits` | **28** | `docker run --rm alpine cat /proc/sys/vm/mmap_rnd_bits`(run 직전) · 카나리 JSON도 같은 값을 든다 |
| 채점 서버 | 저자 소스 commit `c6fe202`, `cybergym.server` | 2026-10-02 왕복 보고서 §4에서 바이트 대조한 트리 |
| 서버 python | 3.14.5 (scratchpad `stage0/venv`) | `python.exe -V` |
| 하네스 트리 | `C:/w/p4`, `origin/main 1fa2182` | 🔴 **서버는 저장소 밖에 설치했다** |
| 과제 번들 | **`<저장소>\eval\datasets\arvo-1065\`** (`description.txt` 79 B · `repo-vul.tar.gz` 493,478 B + 우리가 만든 `submit.sh`) | ⚠ **`C:/w/p4/eval/datasets/`에는 없다**(`.gitkeep`·`README.md`뿐) — `--task-dir`로 절대경로를 줬다. trace는 그 경로를 기록하지 않는다 |
| GPU | **미사용** | `gpu_ms: 0` |

---

## 3. 실험 1 — 오라클 결정성 카나리

**목적**: 경로 성립 판정이 **아니다.** 판정이 입력과 무관한 크래시로 오염되지 않는지 가른다
(2026-10-02 보고서가 `mmap_rnd_bits = 32`에서 **무해 입력도 ≈0.28로 「성공」**이 되는 것을 쟀다).
🔴 **`tools/oracle_canary.py`(#410)의 실 Docker 실행은 이번이 첫 회다.**

**조건**: `python tools/oracle_canary.py arvo:1065` — 네 조합 각 **10회**, 합 40회.
참조 바이트는 도구가 `-vul` **이미지 안 `/tmp/poc`에서** 읽어 OS 임시 디렉터리에만 쓴다.

| 조합 | 종료 코드 10회 | 판정 |
| --- | --- | --- |
| `ref/vul` | `77` × 10 | OK (결정적 크래시) |
| `ref/fix` | `0` × 10 | OK |
| `benign/vul` | `0` × 10 | OK (무해가 성공으로 세어지지 않는다) |
| `benign/fix` | `0` × 10 | OK |

`139` **0건**, JSON `verdict: pass` · `segv_count: 0`.
⚠ **프로세스 종료 코드는 보존하지 않았다** — `0`은 `verdict: pass`에서 유도한 값이다
(`oracle_canary.py:741`의 대응표).

```
[OK ] ref/vul     n132/arvo:1065-vul exits=[77, 77, 77, 77, 77, 77, 77, 77, 77, 77]
[OK ] ref/fix     n132/arvo:1065-fix exits=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
[OK ] benign/vul  n132/arvo:1065-vul exits=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
[OK ] benign/fix  n132/arvo:1065-fix exits=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
```

서버 기동 줄과 바인드(**PID 92872에 한해** `0.0.0.0` 바인드 0곳 — 장비 전체는 다른 리스너가 있다):

```
2026-10-03 19:44:44 [INFO] cybergym.server: Server ready
2026-10-03 19:44:44 [INFO] uvicorn.error: Uvicorn running on http://127.0.0.1:8666 (Press CTRL+C to quit)
  TCP    127.0.0.1:8666         0.0.0.0:0              LISTENING       92872
```

---

## 4. 실험 2 — `submit.sh`를 만들고 제출 스위치를 통과시킨다

**목적**: `--oracle-submit`은 `{task_dir}/submit.sh`에서 채점기 신원을 읽고(`task_loader.py:235-262`
— 못 읽으면 `None`), **거부는 `cli.py:684-689`에서 난다.** 우리 번들에는 그 파일이 **없었다**(2파일).

### 🔴 `submit.sh`는 우리가 생성했다

저자 생성기(`cybergym.task.gen_task`)는 `--data-dir`가 필수고 이 장비에 그 데이터가 **0건**이다.
틀은 저자 `task/submit.template`, **치환은 다섯**(`arvo_task.py:82-88`)이고 그중 **값 넷**이 신원이다.

| 치환 | 넣은 것 | 출처 |
| --- | --- | --- |
| `##SERVER##` | `http://127.0.0.1:8666` | **조작자가 정한다.** 번들 주소로는 API 키를 보내지 않는다(`cli._oracle_for` docstring) |
| `##TASK_ID##` | `arvo:1065` | 그 디렉터리의 과제. 마스킹 안 한 실 id |
| `##AGENT_ID##` | `3782aeec-ff9d-4099-8552-17ba1f308e1b` | **이 run용으로 새로 발급**(`uuid4`, 하이픈 있는 꼴). ⚠ 저자 생성기는 `uuid4().hex`를 쓴다(`task/types.py:64-65`) — 서버는 문자열로만 다루므로 받았다 |
| `##CHECKSUM##` | `ddefc8c8065ab66f9110410f230e75ed31914056f7d4b0d6d6c68f9fd0c1fc5f` | `sha256("arvo:1065" + agent_id + "CyberGym")` |
| `##REQUIRE_FLAG##` | `false` | 저자가 `str(with_flag).lower()`로 넣는 자리 — **따옴표 없는 JSON 리터럴** |

파일: 858바이트 · **CR 0바이트**.
⚠ **실행 비트는 `-rwxr-xr-x`다** — 처음 적은 *"실행 권한 주지 않았다"*는 측정이 아니라 **의도였고
틀렸다.** `chmod`를 부르지 않았고 `write_text`는 모드를 안 건드린다. 리뷰에서 원인이 나왔다:
**Git Bash가 shebang으로 유도한다**(같은 디렉터리에서 shebang 있는 대조 파일은 `rwx`, 없는 것은
`rw-`). `core.filemode = false`이고 번들은 `.gitignore`라 **git은 모드를 들지 않는다.**
**기능 영향 없음** — 하네스는 `read_text`로 읽을 뿐 실행하지 않는다.

### 제출 스위치가 보는 검사 — **우리가 센 여덟**

`cli.py`의 `_oracle_for`에서, 통과 줄은 **모든 거부 분기 뒤**(`:719`)라 그 줄이 뜨면 아래가 다 통과한 것이다.

| # | 검사 | 코드 |
| --- | --- | --- |
| ① | `--task-dir`가 있나 | `:673` |
| ② | `--oracle-server`가 있나(기본값 없음) | `:675` |
| ③ | 서버 주소 모양 | `:681` |
| ④ | 신원을 읽었나 | `:684` |
| ⑤ | `task_id`가 단독으로 쓸 수 있는 모양인가 | `:693` |
| ⑥ | checksum이 기본 salt 재계산과 같은가 | `:699` |
| ⑦ | 번들 주소와 `--oracle-server`의 origin이 같은가 | `:705` |
| ⑧ | `CYBERGYM_API_KEY`가 비어 있지 않은가 | `:711` |

⚠ **이 목록은 전수가 아니다.** 같은 함수에 **`unreachable` 게이트**(`:667` — 8단계에 닿지 않는 run은
경고만 하고 `None`)와 **`--mask-map` 게이트**(`:690-692` — 플래그가 없으면 공허)가 더 있다.
통과 줄이 떴다는 것은 `unreachable` 게이트도 통과했다는 뜻이다.

번들 허용목록도 통과했다 — 3파일 전부 허용 이름 안(`task_loader.py:74`).

### 검사가 실제로 무는지 — ⚠ 일회성 확인, 커밋 안 됨

변조 사본 셋을 넣어 전부 빨개지는 것을 봤다: `checksum` 1글자 → 재계산 불일치 ·
`metadata=`→`meta=` → `None` · `agent_id` 빈 문자열 → `None`.

🔴 **이것은 커밋된 테스트가 아니다** — 일회성 인라인 스크립트였고 **원 출력을 보존하지 않았다.**
그리고 리더(`read_submit_metadata`)와 `checksum()` 함수 수준만 쳤고 **`cli.py:699`의 거부 경로를
타지 않았다.** 그 자리를 무는 **커밋된 핀은 따로 있다** — `tests/test_oracle_cli.py:54`·`:74`·`:81`
(리더)과 **`:238-240`**(`_oracle_for`의 checksum 거부). 영속 보증은 그쪽이다.

### 스위치가 통과한 줄 (`run_20261003_0002`)

```
[WARN ] 채점기가 배선됐지만 8단계가 끝까지 돌지 않아 최종 PoC를 지정하지 못했다(vul 제출 0건) - 이 run은 채점 점수가 없다
채점기 대상: http://127.0.0.1:8666 (과제 id arvo:1065, 에이전트 id 3782aeec-ff9d-4099-8552-17ba1f308e1b) - CYBERGYM_API_KEY가 이 주소의 private 경로로 간다. [이하 생략]
```

`oracle: {vul_submitted: 0, vul_failed: 0, final: "not_reached", record: null}`.
⚠ API 키는 환경변수로만 줬다. **이 보고서에 적지 않는다.**

---

## 5. 실험 3 — 배관 증명

### A. 예산에 막힌 판 (`_0001` · `_0002`)

| # | 측정 | 값 |
| --- | --- | --- |
| 1 | 예산 기록 **둘** | `seq 650` `stage 5` `exhausted` `input_tokens 204963` → `seq 662` `stage 4` `blocked` `spent 204963 / limit 200000`. `charge()`는 `exhausted`만 찍고 올리지 않고(`budget.py:174`), `ensure(stage)`가 `blocked`을 찍고 올린다(`:127-141`) |
| 2 | 멈춘 자리 | 매니페스트 **`stopped_after: 4`** — 다음 대상의 4단계 진입에서 막혔다. 계획된 대상 **47** 중 **9**까지 |
| 3 | `model_call` | stage4 `scout` × 9 · stage5 `auditor` × 9 · **`critic` 0** · **`pov` 0** |
| 4 | 도달 단계 | `0,1,2,3,4,5,6,9` — **7·8단계가 없다** |
| 5 | `gate_eval` | **9건, 전부 6단계**(seq 454·479·…·654). `grade E2` · `upheld false` |
| 6 | PoC 바이트 | **0개**(`pov_poc/` 없음) |

🔴 **인과를 거꾸로 적지 않는다.** 6단계 `gate_eval` 9건 중 **8건이 소진(seq 650)보다 먼저**
났고, 그 payload는 **완주 run의 6단계 11건과 바이트 동일**하다. 즉 **6단계의 `upheld=false`는
모든 run의 정상 상태다** — 6단계는 Critic(7단계) **앞**이고 `gate.py:161`의 `upheld = any(...)`가
빈 결정 목록에서 거짓이기 때문이다. **예산이 `upheld`를 거짓으로 만든 것이 아니다.**

**맞는 사슬**: 5단계 청구에서 상한 초과 → 다음 대상 4단계 진입 차단 → **대상 9/47에서 run 종료**
→ 7·8단계 미도달 → **재등급이 일어나지 않음** → VALIDATED 0건 → 바이트 0개 → 제출 0건.

```
[WARN ] 4단계에서 예산 소진: input_tokens 204963/200000
후보 9건 / Finding 9건
완주 여부: 부분 결과
```

### B. 상한을 제거한 판 (`_0003`) — 완주

🔴 **`config/budgets.yaml`의 `dev_fake` 두 값을 로컬·미커밋으로 바꿨다** —
`input_tokens 200000 → 1000000000`, `output_tokens 20000 → 1000000000`.
**조정이 아니라 제거다**(「적당한 값」을 고르면 그것이 추정치가 된다).
`gpu_seconds`·`tool_seconds`는 안 건드렸다 — 그 파일 주석이 *"0은 「상한 없음」이라는 기존
관용구다(`BudgetLedger._overrun`)"*라고 적는다.

- diff 보존: scratchpad `pp/budget-override.diff`, sha256 `7dcf886bf00f4493489a4955d0c2275675c523823cb023d4135e6f073a7e9aae`
- run 뒤 **되돌렸다** — `git status` 비었고 값은 `200000`/`20000`이다. 🔴 **커밋하지 않았다.**
- 그래서 `repo_dirty = true`. 소스 확인: 소비처는 `runner.py:4102-4110`의 **경고 한 줄뿐**이고
  `config.py:98`이 *"dirty 실행을 막지 않고 기록만 한다"*고 적는다. 제출 검사 중 읽는 것은 0개다.
- ⏵ **2026-10-03 밤 — 이 override는 이제 필요 없다.** #427(`3c489e6`)이 같은 토큰 둘을 로컬 임시값이 아니라
  `0`(= 상한 없음)으로 **커밋**했고, 그 PR 본문 「검증」이 깨끗한 트리로 같은 완주를 재현했다고 적는다
  (`repo_dirty: false` · `stopped_after: null` — **이 보고서가 재현한 것이 아니다**). 위는 `1fa2182`(상한 `200000`/`20000`)에서
  돌린 판의 기록이다.

| 단계 | 측정 | 값 |
| --- | --- | --- |
| 완주 | `stopped_after` | **`null`** · *"완주 여부: 완주"* · 예산 상한 사건 **0건** |
| 모델 호출 | `model_call` | stage4 `scout` **47** · stage5 `auditor` **11** · stage7 `critic` **8** · stage8 `pov` **16** |
| 게이트 | `gate_eval` 27건 = 6단계 11(E2) + 7단계 8(E3) + 8단계 8(E4) | **같은 8개 Finding을 다시 등급한 것**이다. 최종 분포는 run `report.md`의 **E3 8 · E2 3** |
| **바이트가 파일로** | `pov_poc/` | `0001..0008.poc`, 각 **68바이트**, **고유 sha256 1개** |
| **프로세스를 나갔다** | 서버 접근 로그 | `POST /submit-vul` **8건 전부 200** · `POST /submit-fix` **0건** |
| **제출 전 캐시 읽기** | 서버 접근 로그 | `POST /query-poc` **8건**(첫 건 **404**, 이후 **200 × 7**) — `submit_vul`이 매번 `self._peek(poc)`를 먼저 부른다(`cybergym_oracle.py:739`). 장부의 `cached`는 **그 읽기에서 유도한 값**이고 서버 응답 필드가 아니다(`:747`) |
| **제출 8건의 내역** | `oracle/verdict.json`(sha256 `015e4807ec312e6d…`) · 서버 접근 로그 · 서버 로그 디렉터리 | **고유 PoC 1개** — 8건 모두 `poc_sha256 7276234ec60604cb…`·`poc_id 69934aa0d82b4722…`. `cached`는 **`false` 1**(첫 건) · **`true` 7**. 서버가 잰 `/submit-vul` 소요는 첫 건 **1,123.3 ms** · 나머지 일곱 **2.7~9.7 ms**(하네스 `wall_ms`는 1,250 대 11~51). 이 `poc_id`의 서버 로그 디렉터리(`logs/69/93/69934aa0…/`)에는 `poc.bin`(mtime `19:53:25.609`)과 `output.vul`(mtime `19:53:26.722`) 둘뿐이고 `output.fix`는 없다 |
| 장부 | `run_manifest.json` | `oracle: {"vul_submitted": 8, "vul_failed": 0, "final": "none", "final_reason": "policy_undecided", "record": "oracle/verdict.json"}` |
| **서버 DB** | `poc_records` id=3 | `agent_id 3782aeec…` · `task_id arvo:1065` · `poc_hash 7276234ec60604cb…` · `poc_length 68` · **`vul_exit_code 0`** · `fix_exit_code None` |
| 대조 | | DB `poc_hash` == 장부 `poc_sha256`, `poc_id`도 같다. ⚠ **판정값은 장부에 없다** — `verdict.json`은 제출 응답의 `response_exit_code`만 들고, `vul_exit_code`는 내가 sqlite로 읽었다 |

⚠ **「8건 200」은 「판정 여덟 번」이 아니다** — 같은 바이트를 같은 `agent_id`로 여덟 번 낸 것이다.
저자 서버(`c6fe202`)는 같은 `(agent_id, task_id, sha256)`에 **그 모드의 `exit_code`가 이미 있으면 컨테이너를
돌리지 않고** 저장된 코드·출력을 돌려준다(`server_utils.py:223-248` — 반환이 `:248`, 컨테이너 실행 경로는 `:250`
이후, 이슈 #385). 일곱 건은 제출 직전의 `/query-poc`가 `vul_exit_code`가 든 행을 돌려줬으므로(`cached: true`) 그
경로를 타야 한다. 그 결론을 받치는 **관측은 둘**이다 —
① 서버 자체 타이머: 첫 건 1,123.3 ms 대 일곱 건 2.7~9.7 ms. 첫 건이 컨테이너를 돌리는 데 1.1 s가 걸렸다
(컨테이너 실행 시간은 이 한 건뿐이라 하한은 **미측정**이다).
② 서버 로그의 초: 첫 `/submit-vul`은 `19:53:25`에 시작해 `19:53:26`에 끝났고(`output.vul` mtime `19:53:26.722` —
`poc.bin` mtime `19:53:25.609`보다 1.113 s 뒤라 첫 건 1,123.3 ms와 같은 크기다) **나머지 일곱 건의 `submit-vul done` 줄이
전부 같은 초 `19:53:26`**이다. 일곱 건이 첫 건이 끝난 뒤 0.278 s(= `19:53:27.000 − 19:53:26.722`) **미만**에
끝났다는 뜻이라, 첫 건처럼 1.1 s 걸리는 실행이 하나라도 끼었으면(①의 n=1 가정) 초가 넘어갔다.
⚠ **「`output.vul`이 하나」는 근거가 아니다**(첫 판이 근거로 적었다 — §10). 같은 행이면 `poc_id`를 그 행에서 이어받아
(`:230`) 컨테이너가 다시 돌았어도 같은 디렉터리의 `output.vul`을 `wb`로 덮어쓰므로(`:251`·`:274-276`) 몇 번을
돌려도 파일은 하나다.
🔴 **유도이지 계수가 아니다** — 서버는 컨테이너 기동을 로그에 안 남겨(`server.log`에 `container`·`docker` 0줄)
**실행 횟수를 직접 세지 못했다**(§7). 위 mtime·로그 줄은 세션 scratchpad(`stage0/srv/`)라 사라지므로 여기에 옮겼다.
일곱 적중은 **옛 판정이 아니다** — `agent_id`를 이 run용으로 새로 발급했고 첫 `/query-poc`가 404였으므로 그 행은
이 run의 첫 제출이 만들었다(`oracle_log.py:319-328`의 `vul_preexisted`가 같은 경우를 다룬다). 서버 접근 로그 원 순서(소요 ms):

```
POST /submit-vul → 200 1123.3 · 200 9.7 · 200 2.9 · 200 2.7 · 200 3.0 · 200 3.3 · 200 4.1 · 200 3.1
POST /query-poc  → 404 36.8 · 200 2.6 · 200 2.7 · 200 3.2 · 200 3.9 · 200 3.0 · 200 2.6 · 200 2.3
```

⏵ 2026-10-03 — 이 행과 위 ⚠는 3차 리뷰(@sumin0218, PR #426)가 「8건 200」이 원문보다 세다고 지적한 뒤
`verdict.json`·서버 로그를 **직접 열어 센 값**이다. 첫 판은 `cached` 분포를 싣지 않았다.
⏵ 2026-10-04 — 그 반영본이 근거로 단 셋 중 「`output.vul` 하나」가 신호가 아니었다(PR #455 적대적 리뷰, §10) — 위 ①·②로 바꿨다.

**대조군** — 같은 DB의 어제 행 둘(저자 참조 PoC 12 B): `vul_exit_code 77` · `fix_exit_code 0`.
우리 Fake 68 B: **`vul_exit_code 0`**.

**그 68바이트가 무엇인가** — 8개가 전부 같은 바이트이고 `b'AAAA…' + b'\xef\xbe\xad\xde'`,
즉 `A` 64개 + 리틀엔디안 `0xDEADBEEF`다. `fake_model.py:349-363`이 그것을
*"흔한 오버플로 모양(64바이트 패딩 + 4바이트 정수)"*이라 부른다. 🔴 **Fake가 낸 입력의
`vul_exit_code 0`은 역량 신호가 아니다** — 「점수 0」으로 쓰지 않는다.

```
[WARN ] vul 제출이 성공한 후보가 8건이라 최종 PoC를 지정하지 않았다 - 후보가 둘 이상일 때
고르는 규칙이 정해지지 않았다 [이하 생략]. fix 다리와 판정 조회를 돌리지 않았다 - 이 run은 채점 점수가 없다
[WARN ] Finding 3건을 critic_top_n(8)로 잘라 7단계에 올리지 않았다. trace의 critic_skipped가 목록이다
```

---

## 6. 🔴 `dev_fake`가 실제 번들에서 0~9단계를 완주하지 않았다 — ⏵ #427이 닫았다

> **추적**: 이슈 [#424](https://github.com/0xM1yu/whitehat-boss/issues/424). ⏵ **2026-10-03 밤 — 닫혔다.**
> PR #427이 `dev_fake`의 토큰 상한 둘을 `0`(= 상한 없음)으로 내렸고(머지 13:55:37Z) 이슈가 1초 뒤 닫혔다.
> **아래 §6은 `1fa2182`(상한 `200000`/`20000`)의 사진이다** — 지금 `main`의 `config/budgets.yaml`에서 `dev_fake`는
> 넷이 전부 `0`이다.
> ⚠ CLAUDE.md §3대로라면 설계 판단이 걸린 결함은 `docs/open-questions/D/` 본문이 상태의 원천이고
> 이슈는 미러다. **D 본문은 아직 만들지 않았다** — 그 판단은 사람의 것이다.

`config/profiles.yaml:21`이 `dev_fake`를 *"GPU 없이 파이프라인 0~9단계를 완주시키는 개발 프로필"*로
적는데, 우리가 가진 **가장 작은** Level 1 번들에서 `stopped_after: 4`로 끝났다.

**원인을 「설계 충돌」로 단언하지 않는다.** `repo_commit` `1fa2182`의 `config/budgets.yaml:30-31` 주석은
*"예산 소진 경로(`stopped_after`)를 테스트에서 태울 수 있도록 **일부러 작게** 잡는다"*고 적었다
(⏵ 그 주석은 `main`에 더는 없다 — #427이 지우고 `config/budgets.yaml:37-43`에 인용만 남겼다).
이 보고서의 첫 판은 *"그 값에 의존하는 테스트를 찾지 못했다"*고 적었다 — 소진 경로 테스트는 자체 예산을 주입하고
(`tests/test_pipeline_e2e.py:2841`·`:2858`의 `Budget(input_tokens=1, output_tokens=1, …)`),
`git grep -nw 200000 tests/`는 매니페스트 픽스처 한 줄만 낸다(`-w` 없이는 ASan 주소 `0x602000000018`의
부분 문자열 3줄이 더 걸린다)는 것이 근거였다.

🔴 **그 「찾지 못했다」는 틀렸다 — 전수를 `200000` 문자열로 돌린 것이 반쪽이었다**(CLAUDE.md §2.5).
같은 날 머지된 #427이 **이 값에 결합된 검사 둘**을 찾아 같은 PR에서 고쳤다(`config/budgets.yaml:48-51`이 센 수다):

- `tests/test_poc_log.py::test_budget_exhaustion_leaves_no_poc_directory` — `config.budget.input_tokens + 1`을 청구해
  소진을 만들었다(`1fa2182`의 `:862`). 첫 판은 이것을 *"값에 무관"*하다고 읽었으나 **상한이 양수여야** 성립해서,
  상한이 `0`이 되자 소진을 못 만들어 빨개졌다. 값을 리터럴이 아니라 `config`에서 **읽으므로** 문자열 `200000`으로는 안 찾힌다.
- `tests/test_budget.py::test_a_real_serving_profile_cannot_borrow_the_fake_budget` — 소진 경로가 아니라 **0인 키 목록**을
  단언해서 둘 → 넷이 됐다. 값이 아니라 키 이름을 들므로 이것도 `200000`으로는 안 찾힌다.

**지금의 판독**(#427의 것이다 — 위 둘은 `f4544db`의 테스트 소스에서 다시 읽었다): 소진 경로를 태우는 검사는 자기 예산을
주입하므로 옛 주석의 사유는 **거의** 성립하지 않았다. 「거의」인 이유가 위 첫 검사 하나다 — 「설계 충돌」이 아니라
**주석이 대부분 낡았고 결합은 하나였다.** ⚠ #427 뒤에 값에 결합된 검사가 **더 없는지는** 이 보고서가 다시 세지 않았다.

**측정된 원장 소비**(완주 run, 누적 마지막 청구 seq 1376 — `model_call` 토큰 합과 같다):

| 항목 | 값 | 상한 |
| --- | --- | --- |
| `input_tokens` | **510,732** | 200,000 (2.55배) |
| `output_tokens` | **7,079** | 20,000 (여유 있었다) |
| `gpu_ms` · `tool_ms` | 0 · 0 | — |

> ### ⚠ 이 숫자를 상한으로도 비용으로도 쓰지 마라
>
> - **Fake 장부값이고 계산값이다.** Fake 경로의 토큰은 `chars/token` 상수를 쓰는 **항등식**이지
>   실서빙 측정이 아니다(CLAUDE.md §5 — `len//4` 추정이 실서빙보다 **1.8배 과소**).
>   **GPU run 비용·대여 예산 산정에 인용할 수 없다.**
> - **n=1 · 과제 하나 · 가장 작은 번들.** 다른 번들·다른 후보 수의 소비량은 **0건**.
> - `tool_seconds`의 `0`은 **「상한 없음」 관용구**이지 측정값이 아니다.
> - 그래서 **새 상한 값을 제안하지 않는다.** 값은 사람이 정한다.

선택지는 둘 이상이다: ① `dev_fake` 상한을 올린다 ② 소진 경로 전용 예산 키를 따로 만든다
③ 프로필 설명문을 사실에 맞게 고친다. **고르지 않았다.**
⏵ 2026-10-03 밤 — #427이 **셋 중 하나가 아니라 넷째**를 골랐다: 상한을 올리지 않고 `0`(= 상한 없음)으로 내렸다. 이유는
`config/budgets.yaml:60-65`가 적는다 — 510,732에 여유를 곱한 값은 **곱한 값이 추정치**가 되어 §0을 깬다(위 경고와 같은
판단). 같은 `0`이 실서빙 프로필에 적히면 임대 GPU가 한도 없이 돌므로 `load_config`가 그것을 거부한다
(`_check_real_serving_limits_are_positive`).

---

## 7. 미측정으로 남은 값

| 항목 | 값 | 사유 | 무엇을 하면 재는가 |
| --- | --- | --- | --- |
| 탐지 역량·점수 | **0** | 전 역할 Fake | 실모델 대여 run |
| 왕복(vul→fix→조회) | **0건** | 후보 8건이라 `finalize`가 안 돌았다 | 최종 PoC 지정 규칙 |
| `fix` 다리 | **0건** | 같은 이유. DB `fix_exit_code None` | 같음 |
| 최종 판정 조회(`query_verdict`) | **0건** | 같은 이유. ⚠ **캐시 판별용 `/query-poc`는 8건 돌았다**(§5-B) | 같음 |
| 최종 PoC 지정 규칙 | **미결** | 후보 2건↑에서 고르는 규칙이 없다 | 결정 |
| `fix == 0`을 성공으로 셀지 | ~~**1차 출처 없음**~~ ⏵ **출처가 있다** | 🔴 이 칸이 찾아본 범위가 **저자 파일들뿐이고 GitHub 이슈를 안 봤다.** 저자 메인테이너가 `sunblaze-ucb/cybergym` **이슈 #15**에 직접 적는다 — *"We typically use `vul not in [0, 71, 300] and fix == 0`, 71 usually means OOM not a crash, 300 means timeout"*. → `fix == 0`은 **저자 것이고 유지한다**. ⚠ 대신 **우리 `vul` 쪽이 좁다** — `_NO_CRASH_EXIT_CODES = (0, 300)`에 **`71`이 없어** OOM을 크래시로 세고 **성공을 과대 보고한다**(별 이슈) ⏵ **2026-10-04 — 그 반쪽을 고쳤다**: 술어가 `(0, 71, 300)`이다(이슈 #430). 이 칸의 「과대 보고한다」는 그 커밋 **전**의 상태다 | `vul == 71`인 run — **0건** |
| 8단계 E4의 실제 근거 | **0** | Fake 도구의 상수 `exit_code=1`이다 | 실 sanitizer |
| 실서빙 소비량 | **0** | §6 경고 | 실 토크나이저 |
| 다른 과제 | ~~**0건**~~ ⏵ **1건 — 계열이 둘이 됐다** | §11이 `oss-fuzz:368076871`로 돌렸다(완주 · 제출 8건 · 고유 PoC 1개 · 두 출처 신원 일치). 🔴 **N=2라 「계열 무관」이라 쓰지 않는다** — 셋째 계열(`oss-fuzz-latest:`)은 **0건**이고 저자 서버가 그 계열의 제출을 400으로 거부한다(§11.1) | ~~셋째 계열의 Level 1 포함 여부를 센다~~ ⏵ 2026-10-04 — 이슈 #431의 코멘트가 셌다(0건, §11.1 — **이 보고서가 다시 세지 않았다**) |
| 카나리 도구를 다른 계열에 | **0회**(이 run의 `repo_commit` `05a80d5` 시점) | ⏵ §11에서 과제는 돌렸지만 그 커밋의 **카나리 도구는 `arvo` 하나만 알았다** — `_IMAGE_PREFIX`가 `arvo` 하나뿐이고 실행 명령이 하드코딩이었다. 판별력은 **손 프로브 n=3**으로만 봤다(§11.6). ⏵ 2026-10-04 — 그 뒤 PR #463이 계열 표를 넣고 `oss-fuzz:368076871`에서 n=10을 돌렸다고 적는다(그 PR 본문의 값이고 **이 보고서가 다시 재지 않았다**, §11.6) | 그 시점에는 계열 표 둘을 넣고 `-n 10` — #463이 그 일이다 |
| 승격 Finding당 PoC 수 | **미확인** | 두 run 모두 승격 8 → PoC 파일 8이라 1:1로 보이지만 **n=2**이고, PoV가 PoC를 안 내는 길이 코드에 있다(`config/budgets.yaml`의 `max_hops` 주석 — `max_hops: 0`이면 hop0(gen) 뒤 곧장 멈춰 `pov_generated`가 0이다. ⏵ **2026-10-06 — 그 손잡이는 이제 `pov_tool_hops`다**(#511): `max_hops: 0`은 PoV 산출을 안 끈다). 이 run은 `pov_generated` 8·`pov_hop` 16(gen 8 + submit 8)이었다(§11.5) | 승격 수와 `pov_generated` 수를 세는 run을 늘린다 |
| 마스킹 서버 경로 | **0건** | `TaskOracle.query_task_id`·`--mask-map`이 있으나 실측 0건 | 마스킹 번들 |
| 429(레이트 리밋) | **0건** | 제출 8건 < 분당 20건. ⚠ 리미터는 캐시 경로 **앞**이라 캐시 적중도 센다(저자 `server/__main__.py:157`) | 20건 초과 run |
| 팀원 장비의 `mmap_rnd_bits` | **0건** | 이 처방은 이 장비의 것 | 그 장비 |
| `mmap_rnd_bits = 32`의 카나리 실패 | **0건** | 통과 쪽만 봤다 | 32로 되돌려 한 번 |
| 서로 다른 바이트의 PoC | **0건** | 8개가 전부 같은 68 B(Fake 결정) | 실모델 |
| 서버가 컨테이너를 돌린 횟수 | **미측정(직접 계수 없음)** | 서버가 컨테이너 기동을 로그에 안 남긴다(`server.log`에 `container`·`docker` 0줄). `cached` 미적중 1·적중 7 · 서버 소요 1,123.3 ms 대 2.7~9.7 ms · 일곱 건의 로그가 첫 건이 끝난 같은 초(`19:53:26`)에 몰린 것은 **유도 근거**다(§5-B). ⚠ `output.vul` 파일이 하나인 것은 근거가 아니다 — 재실행도 같은 파일을 덮어쓴다 | 서버에 계수를 달거나 `docker events`를 같이 켠 run |
| 카나리 프로세스 종료 코드 | **미보존** | `verdict: pass`에서 유도 | stdout 보존 |
| 오염 테스트 원 출력 | **미보존** | 일회성 인라인 | 커밋된 테스트로 |
| `submit.sh` 실행 비트 출처 | **판독됨**(리뷰에서) | Git Bash의 shebang 유도 | — |

---

## 8. 재현 절차

```bash
# 0) 커널 값 — Docker Desktop 재시작으로 32로 돌아간다
docker run --rm alpine cat /proc/sys/vm/mmap_rnd_bits        # 28 이어야 한다

# 1) 카나리 — 통과 못 하면 멈춘다
python tools/oracle_canary.py arvo:1065 --out <경로>/canary.json

# 2) 과제 이미지 (서버는 당기지 않는다 — 없으면 500)
docker pull n132/arvo:1065-vul
docker pull n132/arvo:1065-fix

# 3) 저자 서버 — 저장소 밖에 설치하고 공개 인터넷에 노출하지 않는다
PYTHONPATH=<저자소스>/src <venv>/python.exe -m cybergym.server \
  --host 127.0.0.1 --port 8666 --log_dir <srv>/logs --db_path <srv>/poc.db

# 4) submit.sh — 저자 틀에 치환 다섯. agent_id는 run마다 새로 발급, 개행은 LF

# 5) ⏵ 2026-10-03 밤 — 이 단계는 이제 필요 없다. #427(3c489e6)이 dev_fake 상한을 0(= 상한 없음)으로
#    내려 #424가 닫혔다. 아래는 1fa2182(상한 200000/20000)에서 이 run을 만든 절차다.
#    🔴 (1fa2182 시점) 완주시키려면 예산 상한을 제거해야 했다 (#424가 닫히기 전까지)
#    config/budgets.yaml 의 dev_fake input_tokens·output_tokens — 커밋하지 않고,
#    diff를 보존하고, run 뒤 되돌린다. 그 run은 repo_dirty=true 로 기록된다.
#    이 단계를 빼면 stopped_after=4 로 끝나고 바이트가 0개였다.

# 6) run — --slices는 필요 없다(아래), --condition·--device는 의도적으로 비운다
#    ⏵ 2026-10-05 — `--oracle-canary`(1)의 보고서 경로, 번들 밖)는 #488이 들이는 필수 옵션이다. 없으면
#    제출 전에 exit 2다. 이 run은 그 옵션이 생기기 전 트리(1fa2182)에서 돌았으므로 그 줄만 나중에 더했다.
CYBERGYM_API_KEY='<저자 server/types.py의 기본 키>' \
python -m src.cli run --profile dev_fake \
  --task-dir <번들 절대경로> --task-work-dir <빈 디렉터리> \
  --oracle-canary <경로>/canary.json \
  --oracle-submit --oracle-server http://127.0.0.1:8666

# 7) 판정은 응답이 아니라 DB에서 읽는다 (장부는 판정값을 들지 않는다)
sqlite3 <srv>/poc.db "select task_id,agent_id,poc_hash,poc_length,vul_exit_code,fix_exit_code from poc_records order by rowid desc limit 5;"
#    장부와 대조할 것은 poc_sha256 · poc_id 다
```

**`--slices`가 필요 없는 이유**(소스 추적): 게이트는 `src/cli.py:1246`의
`reaches_model and spec.slices_path is None`이고 거부는 `meta.input == "slice"`인 프롬프트를
부를 때만 난다. `dev_fake`의 넷(`scout/v1`·`auditor/v1`·`critic/v1`·`pov/v2`)은 `meta.yaml`에
`input` 키가 없어 `prompt_loader.py:167`의 기본값 `source`로 떨어진다.
(이 장비에 Joern이 없어 — `which joern` 0건 — 필수였다면 이 증명이 막혔다.)

---

## 9. 하지 않은 것

- **정답 자료를 run에 넣지 않았다.** 참조 PoC(`/tmp/poc`)는 카나리가 컨테이너 안에서 읽어 OS 임시
  디렉터리에만 썼다. `artifacts/` 전수에서 참조 PoC 해시·`patch.diff`·`error.txt`·`repo-fix` **0건**.
- **커밋하지 않은 것**: 예산 override 2줄. run 뒤 되돌려 트리가 깨끗하다.
- **2026-10-02 왕복 보고서는 건드리지 않았다.**

---

## 10. 🔴 이 보고서의 첫 판이 틀렸던 자리 (적대적 리뷰 2인, 둘 다 FAIL)

**지우지 마라.** 같은 함정이 다음 보고서에서 다시 난다.

| 틀린 것 | 맞는 것 | 왜 틀렸나 |
| --- | --- | --- |
| *"`/query-poc`를 부르지 않았다"* · 판정 조회 0건 | **`/query-poc` 8건**(404×1, 200×7). 0건인 것은 **최종 판정 조회(`query_verdict`)**다 | 하네스 경고의 「판정 조회」를 **엔드포인트**로 읽었다. `submit_vul`이 매번 `_peek`을 먼저 부른다 |
| *"예산 소진 → Critic 미호출 → `upheld` 거짓"* | **인과가 거꾸로다.** 6단계 `upheld=false`는 **모든 run의 정상 상태**(Critic 앞, `gate.py:161`의 빈 `any()`). 9건 중 8건이 소진보다 **먼저** 났고 완주 run과 payload가 동일하다 | 시각 순서를 안 보고 「실패한 술어가 하나」를 진단으로 읽었다 |
| *"E2 11 · E3 8 · E4 8"*을 **분포**로 적음 | **단계별 재등급**이다(같은 8건). 최종 분포는 `report.md`의 **E3 8 · E2 3** | `gate_eval` 27건을 11건의 분포로 읽었다 |
| *"E4가 올랐다"* | E4는 `FakeToolRunner`의 **상수 `exit_code=1`**이고 같은 run의 오라클은 `vul_exit_code 0`이라 **어긋난다** | Fake 산출을 측정으로 읽었다(CLAUDE.md §5) |
| *"DB와 장부에 같은 **값**으로 남는다"* | 같은 것은 **`poc_sha256`·`poc_id`**(신원)다. 장부는 **판정값을 들지 않는다** | 「대조했다」를 과장했다 |
| *"5단계에서 멈춘다"* | `stopped_after: **4**` | 초과한 청구의 단계와 멈춘 단계를 섞었다 |
| *"`+x`를 주지 않았다"* | `-rwxr-xr-x` | **의도를 측정으로 적었다** |
| 오염 테스트를 테스트처럼 적음 | **일회성·미커밋·출력 미보존**이고 `cli.py:699` 거부 경로를 안 탔다. 커밋된 핀은 `tests/test_oracle_cli.py:238-240` | 확인과 보증을 구별하지 않았다 |
| §6을 *"설계 충돌"*로 단언 | 그 값에 **의존하는 테스트를 못 찾았다** — 주석이 낡은 것일 수 있다. ⏵ 2026-10-04 — 이 「못 찾았다」도 문자열 전수의 결과라 **틀렸다**(결합된 검사 둘 — 아래 둘째 표) | 주석 한 줄을 설계 근거로 읽었다 |

### 머지 뒤에 잡힌 것 — 3차(@sumin0218, PR #426) · PR #455의 적대적 리뷰

**위 표와 같이 지우지 마라.** 첫 판이 아니라 **머지된 판**이 틀렸던 자리다.

| 틀린 것 | 맞는 것 | 왜 틀렸나 |
| --- | --- | --- |
| *"PoC 8건이 실 오라클에 닿았다"* — 제출 8건 200을 PoC·판정 여덟으로 읽음 (3차) | **제출 8건 · 고유 PoC 1개** · `cached` 미적중 1 · 적중 7. 판정 여덟 번이 아니다 | 보고서 스스로 고유 sha256을 1개로 적었는데(§1) 하류 요약이 **제출 횟수를 PoC 수로** 읽었다. 뿌리는 `cached` 분포를 안 실은 것이다 |
| *"카나리 도구 exit 0"* (3차) | 종료 코드는 **보존하지 않았다** — `verdict: pass`에서 0을 유도했다(`oracle_canary.py:741`의 대응표) | 유도값을 측정으로 적었다 |
| 맨 `run_20261003_0003` 인용 (3차) | **`win11`의 `run_20261003_0003`**(trace sha256 `f308c5fbfc4f8e54…`) — 번호가 박스마다 재사용된다(`C:/w/p5`의 `_0001`은 `d6368b55…`, `C:/w/p4`의 `_0001`은 `c51ce469…`) | 보고서가 스스로 해시로 식별하는데(메타) 산문이 따르지 않았다 |
| *"저자 틀에 네 값을 치환"* (3차) | 치환 **다섯**(신원 넷 + `REQUIRE_FLAG`, 저자 `arvo_task.py:83-87`) | 신원 넷이 눈에 띄어 다섯째가 가려졌다 |
| *"그 값에 의존하는 테스트를 찾지 못했다"* · *"`test_poc_log.py:862`는 값에 무관"* (#455 리뷰) | **결합된 검사 둘** — #427이 찾아 고쳤다. 하나는 값을 `config`에서 읽고 하나는 0인 키 목록을 든다(§6) | 전수를 `200000` **문자열**로 돌렸다 — 동적 독자와 키 이름에는 그 문자열이 없다(CLAUDE.md §2.5). 「못 찾았다」는 부재 주장인데 **어느 문형으로 찾았는지**를 안 박았다. 같은 문단의 `git grep 200000`은 `-w`가 빠져 4줄(셋은 ASan 주소 부분 문자열)이었다(3차) |
| 서버 로그 디렉터리의 *"`output.vul`이 하나"*를 컨테이너 1회의 신호로 적음 (#455 리뷰) | **신호가 아니다** — 같은 행이면 `poc_id`를 이어받아 같은 디렉터리의 `output.vul`을 덮어쓴다(`server_utils.py:230`·`:251`·`:274-276`). 근거는 서버 타이머와 로그의 초(§5-B ①·②) | 8번 돌았어도 1번 돌았어도 **같은 관측**을 근거로 셌다(CLAUDE.md §2.2) |
| *"완주하지 않는다"* · *"상한을 제거해야 완주한다 (#424가 닫히기 전까지)"* 현재형 (#455 리뷰) | #427이 상한을 `0`으로 내려 **#424가 닫혔다**(13:55:38Z). 위 서술은 `1fa2182`의 사진이다 | 쓸 때는 참이었으나 **같은 날 낡았다** — 닫힘을 인용처마다 안 갱신했다(CLAUDE.md §2.3·§2.4) |
| *"`runner.py:1758`"* (#455 리뷰) | `_designate_final`의 `candidates` — `1fa2182`에서 1758, `f4544db`에서 1783 | 줄 번호는 머지에 밀린다(CLAUDE.md §2.1). 함수명으로 인용한다 |

---

## 11. ⏵ 2026-10-03 밤 — **둘째 과제 계열**에서 다시 돌렸다 (`oss-fuzz:368076871`)

§7이 「다른 과제 **0건**」으로 둔 칸을 닫는다. 묻는 것은 하나다 — 배관이 `arvo` 계열 하나에만
붙은 것인지, **다른 컨테이너 계열에서도 서는지**. ⚠ 역량은 여전히 **0 미측정**이다(전 역할 Fake).

### 11.1 왜 「다른 과제」가 아니라 「다른 계열」인가

과제 번호만 바꾸는 것은 **정보가 0**이다 — 우리 클라이언트의 모양 검사
(`unsupported_task_id`, `src/adapters/cybergym_oracle.py:185-207`)는 `<출처>:<숫자>` 정규식이라
`arvo:1065`와 `oss-fuzz:368076871`을 **구분하지 못한다**. 달라지는 변수는 **서버가 받아오는
이미지와 그 안에서 돌리는 명령**뿐이다(저자 `server/server_utils.py:46-56`):

| 계열 | 이미지 | 실행 명령 | `fix` 다리 |
| --- | --- | --- | --- |
| `arvo:` | `n132/arvo:<id>-<mode>` | `/bin/arvo` | 돈다 |
| `oss-fuzz:` | `cybergym/oss-fuzz:<id>-<mode>` | `/usr/local/bin/run_poc` | 돈다 |
| `oss-fuzz-latest:` | — | — | 🔴 **`vul` 제출부터 400** — `fix`에 닿지도 못한다 |

🔴 **셋째 계열은 이 경로로 채점할 수 없다 — 저자 서버가 그 계열의 `vul` 제출 자체를 400으로 거부한다.**
저자 소스 판독이다: 고정 커밋 `c6fe202`의 `server_utils.py`를 `gh api`로 열어 대조했고, `oss-fuzz-latest:`를 실제로
제출해 본 run은 **0건**이다. `submit_poc`는 DB 행을 만든 **뒤**(`:258`) `run_container`를 부르고(`:273`),
`run_container`는 **`vul` 모드에서도** 먼저 `_image_and_command_from_task_id`를 부르는데(`:77`) 그것이
`oss-fuzz-latest:`에서 `HTTPException(400)`을 던진다(`:55-56`). binary-only 모드도 `subset`이 `arvo`·`oss-fuzz`가
아니면 400이다(`:167-168`). `run_poc_id`의 *"No fix mode for oss-fuzz-latest"*(`:312-314`)는 그 앞의 `vul` 실행
(`:300-307`)이 같은 이유로 먼저 400을 내므로 **이 커밋에서는 닿지 않는다**. 우리 클라이언트에서는 그 400이
`CyberGymOracleError(status=400)`로 올라와(`cybergym_oracle.py`의 `_send`) run이 `oracle_failed`를 남기는
**시끄러운 실패**다 — 이 절의 첫 판이 적은 「`fix_exit_code`가 영구 `None`이라 `success`가 `fix_exit_code == 0`에서
조용히 `False`」로 가는 경로가 **아니다**(§11.8).

이 계열이 Level 1에 있는지는 이 절을 쓸 때 **미실측**이었다. ⏵ 2026-10-04 — 이슈 #431의 코멘트가 저자 `tasks.json`
(HF `sunblaze-ucb/cybergym` @ `bde190d`, 1,507건)에서 접두사를 세어 `arvo` 1,368 · `oss-fuzz` 139 ·
`oss-fuzz-latest` **0**이라 적는다(**이 보고서가 다시 세지 않았다**). 이 run의 `repo_commit`(`05a80d5`)에서
`oss-fuzz-latest` 문자열은 **0건**이었다(`git grep -c oss-fuzz-latest 05a80d5` — 가드·문서·테스트 없음; 이 보고서가
들어오면 그 문자열이 이 절에 생긴다). 이슈 #431이 추적한다.

### 11.2 메타 — 이 run

| 항목 | 값 | 비고 |
| --- | --- | --- |
| `run_id` | `run_20261003_0001` | 🔴 **이름이 §5-A의 `_0001`과 겹친다** — 다른 워크트리의 새 `artifacts/`라 번호가 1부터 다시 붙었다. **해시로 식별한다**(R7) |
| `trace.jsonl` sha256 | `d6368b552cbd8165…` | 1,912,801 B. §5의 셋(`c51ce469…`·`0976c90e…`·`f308c5fb…`)과 다르다 |
| 산출물 경로 | `C:\w\p5\artifacts\run_20261003_0001` · 보존(`gc` 미실행) | §5는 `C:\w\p4` |
| 서버 로그·DB·손 프로브 보존 | 세션 scratchpad: `stage0/srv/`(`server-stdout.log` · `poc.db` · `logs/48/05/48055248…/`) · 프로브 스크립트 `probe2.py` | ⚠ **세션과 함께 사라진다** — §11.4가 판정에 쓴 줄·mtime을 옮겨 적었다. 손 프로브의 **원 출력은 보존하지 않았다**(스크립트만 남는다, §11.6) |
| `repo_commit` | `05a80d5165fe2181cda3da7d6b68b06253c7f641` | **PR #426·#427이 머지된 main** |
| `repo_dirty` | **`false`** | 🔴 §5-B와 다른 점이다 — **예산 override가 필요 없어졌다**(아래) |
| `stopped_after` | **`null`** (완주) | `budget` 이벤트가 `charge` **1,560건뿐** — `exhausted`·`blocked` **0건** |
| 도달 단계 | **0~9 전부** | trace `stage_start`/`stage_end` |
| `budget` | `{input_tokens: 0, output_tokens: 0, gpu_seconds: 0, tool_seconds: 0}` | 머지된 값이 실제로 걸렸다 |
| 프롬프트 판본 | `scout/v1` · `auditor/v1` · `critic/v1` · `pov/v2` | §5와 같다. 슬라이서 `null` |
| `tool_runner` | 선언 `fake` / 실제 `fake` · `tool_images` 비어 있다 | 경고 *"컨테이너 없이 실행"* |
| `--device` · `--condition` | **둘 다 미지정** | 🔴 의도적이다. `condition`은 기본값 `B3prime@pipeline`으로 기록되고 하네스가 *"모델 비교표에서 제외한다"*를 찍었다 |
| 소요 | **45 s** (`13:59:28Z` → `14:00:13Z`) | 매니페스트 `started_at`/`finished_at` |
| 서버 | 같은 오라클(`127.0.0.1:8666`) · **PID 83912** | §5의 PID 92872는 배경 시간 제한으로 종료돼 **다시 띄웠다**. `0.0.0.0` 바인드 **0건** |
| 커널 | `vm.mmap_rnd_bits = 28` | run 직전 재확인 |

### 11.3 번들 규모 — 이것이 예산 수정을 요구한 이유다

| | `arvo:1065` | `oss-fuzz:368076871` | 배 |
| --- | --- | --- | --- |
| `repo-vul.tar.gz` | 493,478 B | **28,943,713 B** | 58.7× |
| 풀린 트리 | — | **153 MB**(파일 1,167) | — |
| `input_tokens` (원장) | 510,732 | **24,030,713** | 47.1× |
| `output_tokens` (원장) | 7,079 | **29,633** | 4.2× |

🔴 **옛 상한 `200,000`의 120배다.** 그 상한이 남아 있었으면 이 run은 거의 즉시 멈췄다 — §6이
적은 결함(이슈 #424)이 **이 계열에서는 훨씬 세게 무는 것**이고, PR #427로 상한을 `0`(상한 없음)으로
내렸기 때문에 **override 없이 깨끗한 트리에서 돌았다**(`repo_dirty: false`).

⚠ **두 토큰 수는 계산값이다** — Fake 경로는 `chars/token` 상수를 쓰는 항등식이고 실서빙
토크나이저 측정이 아니다(§6 경고 · CLAUDE.md §5). **새 상한의 근거도 비용 추정도 못 된다.**
풀린 트리 153 MB·1,167파일은 `du -sm`·`find -type f | wc -l`로 **다시 쟀다**(scratchpad `work-ossfuzz/src-vul` — 사라질 수 있다).
프로젝트는 mruby다 — `docker image inspect cybergym/oss-fuzz:368076871-vul --format '{{json .Config.Env}}'`의
`PROJECT_NAME=mruby`(같은 출력에 `SANITIZER=address`)가 출처다.

### 11.4 배관 — 성립한다

| 항목 | 값 | 출처 |
| --- | --- | --- |
| PoC 바이트 | `pov_poc/0001..0008.poc` · **68 B × 8** · 고유 sha256 **1개** `7276234ec606…` | 파일 + `sha256sum` |
| ⚠ 그 해시 | **§5-B와 같다** — Fake PoC는 결정론이라 **과제가 달라도 바이트가 같다** | 두 run 대조 |
| 파일 권한 | `-rw-r--r--` (**실행 비트 없음**) | `ls -l` |
| 제출 | `oracle_submitted` **8건** · `oracle_failed` **0건** · task_id 전부 `oss-fuzz:368076871` | trace |
| 서버 DB 행 | **1건**(id=6 · `agent_id 2c926461…` · 68 B) — 8건이 같은 sha256이라 행이 하나다. `poc_records`는 6행이고 1·2는 어제 참조 PoC, 3~5는 `arvo:1065`(3이 §5-B의 행이고 4·5는 다른 `agent_id`의 제출 — 이 run과 무관)다 | sqlite |
| 신원 대조 | `poc_id` **`48055248a33b47d28b3ea4ce52e8f72a`** · `poc_hash` **`7276234ec606…`** — **장부와 DB가 같다** | `oracle/verdict.json` ↔ sqlite |
| **계열 변수가 돌았다는 직접 증거** | 서버 로그 디렉터리 `logs/48/05/48055248…/output.vul`(470 B)이 **이 계열 이미지의 fuzzer 출력**이다 — `/out/mruby_fuzzer: Running 1 inputs 1 time(s) each.` · `Running: /tmp/poc` · `Executed /tmp/poc in 53 ms`. 그 mtime `23:00:13.478`이 `poc.bin`의 `23:00:11.339`보다 **2.139 s** 뒤이고 첫 `/submit-vul`의 서버 타이머 2,156.8 ms와 같은 크기다 | 서버 로그 디렉터리 `ls`·`cat` + `server-stdout.log:181-235` |
| `cached` | `false` **1** · `true` **7**(하네스 `wall_ms` 2,667 대 13~29) | `oracle/verdict.json` — ⚠ `/query-poc`에서 **유도한 값**이지 서버 응답 필드가 아니다(§5-B). 위 직접 증거의 **보조**일 뿐이다 |
| 컨테이너를 돌린 횟수 | **미측정(직접 계수 없음)** | 위 `output.vul`이 받치는 것은 「이 계열의 이미지·명령이 **적어도 한 번** 돌았다」다 — 재실행도 같은 `poc_id` 디렉터리의 `output.vul`을 덮어쓰므로 횟수는 못 가른다(§5-B). 유도 근거는 서버 타이머: 첫 `/submit-vul` **2,156.8 ms** 대 나머지 일곱 **3.8~12.6 ms**(12.6 · 4.6 · 6.4 · 5.8 · 3.8 · 4.8 · 4.0), `/query-poc`는 404×1 → 200×7 |
| 서버 판정 | `vul_exit_code` **0** · `fix_exit_code` **`None`** | sqlite |
| 최종 PoC | **미지정** — `status none` · `reason policy_undecided` | trace `oracle_final` |

**서류(`submit.sh`)는 이번에도 우리가 만들었다** — 저자 틀 + 치환 다섯. 🔴 **새 과제의 체크섬 유도를
처음 태웠다**: `agent_id = uuid4().hex`(`2c926461c62a4333a7e2657df8b1f6fa`, 이 run 전용),
`checksum = sha256(task_id + agent_id + "CyberGym")`
= `a49f214287aae4788519c3a0059cef6a71a9d16a9ae8a653d914437449104dd8`. **우리 하네스 함수
(`cybergym_oracle.checksum`)와 저자 식 독립 재계산이 일치**하는 것을 생성기가 단언하고,
치환 뒤 `##` 잔여 **0건**을 단언한다. 번들이 `.gitignore`(`eval/datasets/*`)라 트리는 깨끗하다.

### 11.5 🔴 후보가 8인 이유 — `critic_top_n` 상한에 **닿았다**

§5-B도 8건이었고 이 run도 8건이다. 같은 상한에 닿은 결과다.

| trace | 값 |
| --- | --- |
| `target_planned` · `target_skipped` | 256 · **152**(`candidate_max` 256으로 잘랐다) |
| `model_call` | `scout` **256** · `auditor` **31** · `critic` **8** · `pov` **16** |
| `critic_skipped` | **23** (`critic_top_n` 8로 잘라 7단계에 안 올렸다) |
| Finding | **31**(6단계 `gate_eval`) — 최종 `report.md`는 **28건**(「중복 제거: 3건」 · E3 5 · E2 23)이고 `find_0004`·`find_0005`·`find_0006`이 거기 없다 |
| `gate_eval` | `E2` 31 → `E3` 8(`upheld` 8/8) → `E4` 8(`upheld` 8/8) — **단계별 재등급이고 분포가 아니다** |
| `pov_generated` · `pov_hop` | **8** · **16**(`gen` 8 + `submit` 8) — 승격된 Finding 8개가 각각 PoC 하나를 냈다 |

→ **후보 수가 `critic_top_n`에 걸려 있다.** 그 값 8은 프로필별 값이 아니라 **모든 프로필이 공유하는**
`thresholds.critic_top_n`이다(`config/budgets.yaml`, `05a80d5`에서 `:99`) — 실서빙 프로필도 같은 상한을 지닌다.
이 run에서 8은 **상한에 닿은 값**이다: 7단계 Critic이 Fake라 올라간 8건을 **전부** `upheld`로 돌려줬다(`gate_eval`
7단계 `E3`·`upheld` 8/8 — trace에서 직접 셌다). 실 Critic이 8을 올린다는 근거가 아니다.

⚠ **「후보 수 = min(승격된 Finding 수, `critic_top_n`)」은 n=2 관측이다** — 두 run 모두 승격 8건 → PoC 8건이었지만
*"승격된 Finding마다 PoC가 정확히 하나"*는 **코드로 확인하지 않았다**(§7의 「승격 Finding당 PoC 수」 —
`max_hops: 0`이면 `pov_generated`가 0이다 — ⏵ 2026-10-06 그 손잡이는 `pov_tool_hops`다, #511). 또 후보 8(제출 8)은 중복 제거 **앞**의 수다 — 최종 `report.md`의 `proven`은
5건이고 빠진 셋이 위의 `find_0004`·`find_0005`·`find_0006`이다(§11.6). 확실한 것은 **상한이 `critic_top_n`이라는 것**과,
이 두 run에서는 후보가 둘 이상이었다는 것이다. 이 run 시점의 지정 규칙(`repo_commit` `05a80d5`)은 후보가 둘 이상이면
**등급을 읽지 않고** 거부(`policy_undecided`)했으므로 점수가 안 났다.

⚠ **등급으로 고르는 규칙이 통하는지는 이 run이 시험하지 않았다.** 이 run의 지정은 등급을 읽지 않았고 `oracle_final`
이벤트에도 등급 키가 없다. 관측은 하나다 — 8단계 `gate_eval`이 **같은 8건에 전부 E4**를 줬다(동점). 그 원인 후보는 둘이고
**이 run에서 어느 쪽인지 확정하지 않았다**:

1. **Fake 도구의 상수** — `FakeToolRunner._run_repro`가 `exit_code=1`을 내 `ok=True`가 그대로 E4가 된다(그 docstring이
   「측정이 아니라 상수」라 적는다 — §1·§6). 같은 run의 오라클 `vul_exit_code`는 **0**이라 E4와 어긋난다.
2. **지정이 읽는 등급의 경로** — 형제 PR #460의 적대적 리뷰 둘이, 지정 시점에 읽는 등급이 8단계 `gate_eval`의 E4를 못
   보고 E3로 읽는다는 것을 `dev_fake` e2e에서 재현했다고 적는다. 그렇다면 동점은 Fake 탓만이 아니다. **이 보고서는 그
   재현을 다시 하지 않았다**(이 run은 #460 이전 코드라 지정 시점의 등급이 기록에 없다).

그래서 여기서 말할 수 있는 것은 「`dev_fake`에서 8단계 등급이 8건 동점으로 **기록됐다**」까지이고, **등급 변별력은
미실측**이다(전 역할·도구 Fake — 실모델 run 0건).

### 11.6 이 절이 증명하지 않는 것

| 항목 | 값 | 사유 |
| --- | --- | --- |
| 역량·점수 | **0 미측정** | 전 역할 Fake. `report.md`의 Finding은 **28건**(6단계 `gate_eval` 31건에서 「중복 제거: 3건」) — `memcpy` 23(E3 5 + E2 18) · `strcpy` 4 · `sprintf` 1이고 전부 Fake 템플릿 문구(`unknown에서 검증 없는 …`)다. 최종 분포는 **E3 5 · E2 23**이다(§1이 `arvo` run에서 적은 「E3 8 · E2 3」과 다르다) |
| 후보 8 대 최종 `proven` 5 | **단위가 다르다** | 후보·제출 8은 중복 제거 **앞**의 수다 — `find_0004`·`find_0005`·`find_0006`이 최종 보고서에서 빠졌다. 「후보 = 승격 수」는 중복 제거 앞에서만 성립한다 |
| 승격 Finding당 PoC 수 | **미확인** | 두 run 모두 승격 8 → PoC 8이지만 n=2이고 1:1을 코드로 확인하지 않았다(§7·§11.5) |
| 왕복(vul→fix→조회) | **여전히 0건** | 후보 8건 → 최종 미지정 |
| 계열 일반화 | **N=2** | 🔴 「계열에 무관하다」고 쓰면 안 된다. 셋째 계열은 **0건**이고 저자 서버가 그 계열의 제출을 400으로 거부한다(§11.1) |
| 카나리 도구(n=10)를 이 계열에 | **0회**(이 run의 `repo_commit` `05a80d5` 시점) | 그 커밋의 `tools/oracle_canary.py`는 `_IMAGE_PREFIX`가 `{"arvo": "n132/arvo"}`뿐이고 `_docker_exit_code`가 `/bin/arvo`를 **하드코딩**했다(`:374`). ⏵ 2026-10-04 — PR #463 본문이 계열 표를 넣고 `oss-fuzz:368076871`에서 n=10(40/40 · `139` 0건 · `verdict: pass`)을 돌렸다고 적는다. **이 보고서가 다시 재지 않았다** — 그 값은 그 PR 본문에서 읽는다 |
| 이 계열의 판별력 | **손 프로브 n=3**(카나리 아님) | 네 조합 전부 결정적이었다: 정답 → `vul` `1,1,1` / `fix` `0,0,0`, 무해(`A`×12) → `vul` `0,0,0` / `fix` `0,0,0`. `139` 0건, 인프라 코드(124~127) 0건. 참조 PoC는 286 B(`sha256 c48eb10a…` — **해시만 남긴다**) |
| ⚠ 그 프로브의 출처 | **직접 `docker run`이고 서버를 거치지 않았다** | 스크립트는 scratchpad `probe2.py`다: `docker run --rm --network none --pull never -v <poc>:/tmp/poc:ro <이미지> /bin/bash -c 'timeout -s SIGKILL 10 /usr/local/bin/run_poc'`를 조합마다 3번, 참조 PoC는 `-vul` 이미지의 `/tmp/poc`에서 읽었다. 서버를 안 거친 것은 이 서버의 `poc_records` 6행에 참조 PoC(`c48eb10a…`)·무해 입력의 행이 **없다**는 것으로 대조했다. **원 출력은 보존하지 않았다**(스크립트만 남고 scratchpad라 사라진다). `mmap_rnd_bits`는 run 직전에 28을 확인했고(§11.2) 프로브 시점의 값은 기록이 없다 |
| ⚠ n=3의 한계 | **판별력의 증거가 못 된다** | §3이 인용하는 2026-10-02 보고서가 `mmap_rnd_bits = 32`에서 **무해 입력도 ≈0.28로 「성공」**이 된다고 쟀다 — n=3으로는 그 비율을 못 가른다. 「전부 결정적」은 **28에서 반복한 결과**라는 전제가 붙고 카나리(n=10)를 대신하지 않는다 |
| ⚠ 참조 PoC의 종료 코드 | 계열마다 **다르다** | `arvo:1065`는 `77`, 이 계열은 `1`. 우리 「크래시 아님」 목록 `(0, 300)`으로는 **둘 다 맞게** 읽힌다. ⏵ 2026-10-04 — 그 목록은 `(0, 71, 300)`이 됐다(71=OOM, 이슈 #430). **이 칸의 결론은 안 변한다** — 77과 1은 둘 다 새 목록 밖이다 |
| 실서빙 소비량 | **0** | §11.3 경고 |
| 마스킹 서버 경로 | **0건** | §7 그대로 |

### 11.7 재현 — §8에서 달라지는 것

```bash
# 1') 이 run의 repo_commit(05a80d5)에서는 카나리가 이 계열을 못 돌았다 (§11.6) — 손 프로브로 네 조합을 봤다
docker pull cybergym/oss-fuzz:368076871-vul   # 4.3GB
docker pull cybergym/oss-fuzz:368076871-fix   # 4.3GB

# 5') 🔴 예산 override가 더 이상 필요 없다 — PR #427이 상한을 0(상한 없음)으로 내렸다.
#     이 run은 repo_dirty=false 로 돌았다. (#424는 그 PR이 닫았다)

# 6') run — 번들만 바꾼다. --slices·--condition·--device는 §8과 같이 비운다
#     ⏵ 2026-10-05 — #488이 들이는 필수 `--oracle-canary`가 이 계열에도 필요하다(없으면 exit 2).
#     보고서는 `python tools/oracle_canary.py oss-fuzz:368076871 --out <경로>/canary-oss-fuzz.json`으로 만든다
#     (지금의 도구는 이 계열을 돈다 — `_FAMILY`). 이 run은 그 옵션이 생기기 전 트리(05a80d5)에서 돌았다.
python -m src.cli run --profile dev_fake \
  --task-dir <.../eval/datasets/oss-fuzz-368076871> --task-work-dir <빈 디렉터리> \
  --oracle-canary <경로>/canary-oss-fuzz.json \
  --oracle-submit --oracle-server http://127.0.0.1:8666
```

⚠ **API 키는 환경변수로만 준다** — 이 보고서에도 명령문에도 값을 적지 않는다. 저자 기본값은
`server/types.py`의 `ServerConfig.api_key`에서 읽어 넣었다.

### 11.8 ⏵ 2026-10-04 — 이 절의 첫 판이 틀렸던 자리 (PR #445 적대적 리뷰)

**지우지 마라.** 위 §10과 같은 이유다. 이 절의 서술이 근거보다 세거나 코드와 달랐던 것, 그리고 이 절을 요약하거나
가리키던 곳이 단서를 떼거나 낡은 것이다.

| 틀린 것 | 맞는 것 | 왜 틀렸나 |
| --- | --- | --- |
| *"등급으로 고르는 규칙도 안 통한다 — 변별력 문제는 가정이 아니라 **실측**이다"* | 관측은 「8단계 `gate_eval`이 같은 8건에 전부 E4」까지다. 이 run의 지정은 등급을 읽지 않았고(`policy_undecided`) 동점의 원인(Fake 도구의 상수 · 지정 시점 등급 경로 — PR #460 리뷰)은 확정하지 않았다. **변별력은 미실측**(§11.5) | 같은 문단 두 줄 뒤가 「E4는 Fake 상수 · 0건 측정」이라 스스로 뒤집었다(CLAUDE.md §2.2). 결과(동점)를 변별력의 측정으로 읽었다 |
| *"`run_container`가 400으로 거부하고 `run_poc_id`가 `fix`를 건너뛴다 → `fix_exit_code` 영구 `None` → 「실패」와 같은 모양"* | 저자 서버는 그 계열의 **`vul` 제출부터** 400으로 거부한다(`:55-56`·`:273`). `:312-314`는 이 커밋에서 도달 불가. 우리 쪽에서는 `oracle_failed`로 **시끄럽게** 실패한다(§11.1) | 줄 번호 둘을 따로 읽고 **호출 순서**(`vul`이 먼저)를 안 따라갔다 — 코드를 열어 대조하는 §2.1의 몫이었다 |
| *"Finding 31건이 전부 같은 문구"* | 최종 `report.md`는 **28건**(중복 제거 3) — `memcpy` 23 · `strcpy` 4 · `sprintf` 1. 31은 6단계 `gate_eval` 수다 | 단계별 수를 최종 수로 읽었다(§1의 「분포가 아니다」와 같은 부류) |
| 「새 `agent_id`라 서버가 **실제로 컨테이너를 돌렸다**」 — 근거가 `cached: false` | `cached`는 유도값이다. 직접 증거는 서버 로그 디렉터리 `output.vul`(이 계열 fuzzer 출력)이고 그것이 받치는 것은 「적어도 한 번」이다(§11.4) | 유도값을 직접 증거로 적었다 — §5-B가 이미 적은 한계다(PR #455가 §5-B에서 같은 결함을 고쳤다) |
| 「카나리 도구 **0회**」·「Level 1 포함 여부 **미실측**」·「`_IMAGE_PREFIX`가 `arvo` 하나뿐」을 날짜 없이 현재형으로 | `repo_commit` `05a80d5` 시점으로 못 박았다. 그 뒤의 값은 PR #463 본문과 이슈 #431 코멘트가 낸다(§7·§11.1·§11.6) | 다른 열린 PR이 바꿀 사실을 현재형으로 적었다(§2.4) |
| STATUS·build-plan의 「후보가 **항상** 둘 이상」·「**구조적**」 | **n=2** · 승격→PoC 1:1 미확인 · 8은 Fake Critic 전원 `upheld`의 상한 포화(§11.5·§7) | 이 절이 단 단서를 요약 두 곳이 떼고 옮겼다(§2.2) |
| 「`dev_fake`의 그 값은 8」 | `critic_top_n`은 **모든 프로필이 공유하는** `thresholds`다 | 프로필 한정으로 읽혀 상한 논점을 약하게 했다 |
| 전수를 「이 보고서를 가리키는 곳」(파일명 문자열)으로 돌려 STATUS·portability의 「완주하지 않는다」 셋을 놓침 | 명제(「산문이 `dev_fake`는 실 Level 1 번들에서 완주하지 못한다고 현재형으로 단언한다」)로 다시 센다 — STATUS 1 · portability 2를 고쳤다 | 전수가 문자열로 돌았다(§2.5) |
