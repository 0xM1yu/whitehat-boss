# 평가·벤치마크 프로토콜

## 1. 검증하려는 질문

작은 오픈 모델을 여러 개 묶었을 때 Frontier 모델의 취약점 탐지 성능에 어디까지 근접하며, 그 대가로 비용과 프라이버시에서 얼마나 이득을 얻는가.

목표는 최고 성능이 아니라 **정확도와 비용을 함께 고려한 최적 구조**다. 따라서 모든 비교는 **계산량을 고정한 상태**에서 한다.

## 2. 비교 설계

### 2.1 ~~2×2 설계 (PRD §2.1)~~ — **폐기.** 앙상블 축 정의만 살아 있다

> ### 2026-09-20 — 2×2는 폐기됐다. **Scout 오케스트레이터로 간다**
>
> **PM 결정이다.** 앞서 2026-09-01에 그 트랙이 전량 폐기됐고(`config/budgets.yaml`의
> `output_max` 주석 — *"**2026-09-01 팀 2×2 전량 폐기로 이 제약은 소멸했다**"*), 2026-09-04에
> 실험 프로필 넷이 `config/profiles.yaml`에서 걷혔다([스왑 조사](research/2026-09-04-swap.md)).
> 이번 결정이 **라벨 축까지** 닫는다 — `RunRecord.condition`의 `@2x2`를
> `src/schemas/run.py`의 `CONDITION_PATTERN`에서 **제거했다**(`@pipeline`만 받는다).
> 커밋된 run 기록은 이 값에 의존하지 않는다 — `artifacts/`는 `.gitignore` 대상이다.
>
> **살아 있는 조건 축은 §2.2 하나다.**
>
> ⚠ **그런데 이 절 번호와 아래 각주는 남긴다.** 폐기된 것은 **표**이고, 그 아래 각주가 든
> **앙상블 축 정의(「제작사가 다른 모델」)는 살아서 코드가 강제한다** —
> `src/config.py`의 `_check_family_split`이 그것이고, 그 `ConfigError` 문구가 *"evaluation.md
> §2.1이 조건으로 제작사가 다른 모델을 요구한다"*라고 **이 절 번호를 직접 부른다.**
> 절을 없애면 그 오류가 존재하지 않는 자리를 가리킨다([CLAUDE.md](../CLAUDE.md) §2.3).
> **그 정의는 이제 §2.2의 B3/B3'에 걸린다.**
>
> **이 결정이 [D1](open-questions/D/D1.md)을 닫는다** — *"같은 라벨이 두 축에서 다른 뜻으로
> 쓰인다"*는 질문이 **축이 하나가 되면서 소멸했다.**

| ~~구분~~ | ~~같은 질문을 여러 번~~ | ~~서로 다른 질문으로 역할 분담~~ |
| --- | --- | --- |
| ~~단일 모델~~ | ~~B1~~ | ~~B1'~~ |
| ~~**여러 회사 모델**(앙상블)~~ | ~~B3~~ | ~~B3'~~ |

- ~~**역할 분담 효과** = B1 → B1' 성능 향상~~
- ~~**모델 혼합 효과** = B1' → B3' 성능 향상~~

> 킥오프 발표자료 02-3은 앙상블 축을 "여러 **회사** 모델"로 정의한다. 같은 모델의 self-consistency나 같은 계열의 다른 크기가 아니라 **제작사가 다른 모델**을 섞는 것이 B3/B3'의 조건이다. **Auditor와 Critic을 서로 다른 제작사로 두는 근거가 여기서 나온다.** 어느 모델이 그 두 자리에 앉는지는 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이 정본이고, **이 조건 자체는 모델이 바뀌어도 남는다** — [ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §4가 두 자리의 `family`(가중치 출처 조직)를 `load_config`가 검사하도록 정했고, **2026-09-09에 `src/config.py`의 `_check_family_split`으로 들어왔다** — auditor와 critic이 **다른 모델 키인데** 계열이 같으면 로드가 실패하고, 같은 키(B1이 그렇다)는 정의상 허용한다. `family`가 비어 있는 것은 통과가 아니라 실패다.

### 2.2 파이프라인 구성 조건 (PRD §5.2)

| 조건 | 구성 | 비교 목적 |
| --- | --- | --- |
| B1 | 단일 LLM 또는 단순 Retrieval 기반 소스코드 분석 | Source-only 접근의 한계 확인 |
| B1' | 바이너리·펌웨어 artifact를 포함한 분석 | 입력 범위 확장 효과 검증 |
| B3 | Multi-Agent Orchestration(Scout, Auditor, Critic) 적용 | 역할 분리·모델 협업 효과 검증 |
| B3' | B3 + Evidence Gate + Tool Validation | 오탐 감소 및 근거 품질 향상 검증 |

> 하네스의 `single`(`prompts/single/v2/`)은 단일 패스 프롬프트 이름이지 이 표의 B1이 아니다 -> `roles`에 `single`을 둔 프로필이 없어 `cli run`에 배선되지 않는다(2026-10-10 `config/profiles.yaml` 확인). 조건 B1의 뜻은 실험팀 몫이다.

> 조건 B1'(바이너리·펌웨어 포함)는 지금 코드에서 **바이너리 입력이 후보가 되지 않는다** — 러너(`src/orchestrator/runner.py`의 `_ANALYZABLE`)는 바이너리 artifact를 받지만, 인덱스가 생기는 프로필(`tool_runner`가 `fake`가 아닌 것 — `harness_pool` 등)의 슬라이스 없는 경로에서는 인덱서(`tools/harness-tools/indexer.py`)가 바이너리를 안 받아 `symbols_for()`가 비고 `no_symbol_artifacts`로 세며, `--slices` run은 `_plan_slice_targets`가 슬라이스 조각만 대상으로 잡는다. **그래서 이 조건을 재려면 인덱서의 바이너리 지원((a), [architecture.md](architecture.md) §3 1단계의 책임)이 선행이다.** 지금 재는 대상은 오픈소스 소스다(2026-09-19 팀 구두 확인, `run_id` 없음 — [D79](open-questions/D/D79.md)).

> ~~2.1의 2×2와 2.2의 파이프라인 조건은 같은 라벨을 쓰지만 축이 다르다. 실행 시 `RunRecord.condition`에 **어느 정의를 쓴 조건인지** 명시한다(`B1@2x2` / `B1@pipeline`). 라벨 통합 여부는 [open-questions.md](open-questions.md)에서 확정한다.~~
>
> **2026-09-20 — 축이 하나가 됐다.** 2×2가 폐기되어(§2.1) 이 표의 조건이 **유일한 정의**다.
> `RunRecord.condition`은 `@pipeline`만 받는다(`src/schemas/run.py`의 `CONDITION_PATTERN`).
> ⚠ **접미사 자체는 남긴다** — 기존 `@pipeline` 값의 형식을 지키고, 축이 다시 늘 때 자리가 된다.
> 라벨 통합 질문([D1](open-questions/D/D1.md))은 **이 결정으로 닫혔다.**
>
> ⚠ **앙상블 축 정의는 §2.1 각주가 계속 든다** — B3/B3'가 요구하는 *"제작사가 다른 모델"*이
> 그것이고 `src/config.py`의 `_check_family_split`이 강제한다.

### 2.3 공정성 규칙

1. 모든 조건은 **동일 계산 예산**(input/output token 상한, GPU-second 상한)에서 실행한다.
2. 조건 간 차이는 프로필과 파이프라인 단계 활성화만. retrieval 설정·프롬프트 버전·데이터셋은 고정한다.
   - **예외 하나**: `--tool-runner`가 프로필의 도구 런타임을 덮어쓸 수 있다([decisions/0005](decisions/0005-tool-runner-override.md)). **조건 비교에 쓰는 실서빙 프로필은 이 예외를 쓰지 않는다** — 각 프로필이 **선언한** 런타임이 주 실서빙 장비(ATOM)에 실제로 있기 때문이다([portability.md](portability.md) §3). **그 프로필이 어느 것인지는 `config/profiles.yaml`이 답한다** — 2026-08-28 시점에는 `b1_single`·`local_balanced` 둘이었고 **둘 다 `docker`를 선언한다**(그날 `podman`에서 되돌렸다 — [decisions/0005](decisions/0005-tool-runner-override.md) 마지막 절). (⏵ 2026-09-24 — 그 둘은 지웠다. 지금 실서빙 프로필은 `harness_pool`·`_hybrid`이고 둘 다 `docker`다.) **여기 개수를 현재형으로 못박지 않는 것은 의도다** — 프로필이 늘고 줄 때마다 그 숫자가 틀린다. 그 프로필의 역할에 어느 모델이 앉는지는 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이 정본이고, **그것이 바뀌어도 이 규칙은 그대로다.** 예외가 남는 곳은 프로필이 선언한 런타임이 그 장비에 없는 경우다(예: `dev_singularity`를 ATOM에서 — ATOM에는 docker와 podman이 있고 singularity는 없다). 실제로 쓰인 값은 언제나 `RunRecord.tool_runner`에 남는다. **런타임이 다른 run은 같은 비교 셀에 넣지 않는다.** `tool_images` digest가 런타임 사이에서 비교되지 않기 때문이다([portability.md](portability.md) §5 #4). 그 프로필들이 같은 값을 선언하므로 지금은 이 규칙이 **과거 run에만** 걸린다 — podman으로 도구를 돌린 run은 `run_20260817_0001`(dev 프로필) 하나이고 그것은 조건 비교 대상이 아니다.
3. 같은 조건은 seed를 바꿔 최소 3회 반복하고 분산을 함께 보고한다.
4. 프롬프트 버전이 바뀌면 이전 결과와 직접 비교하지 않고 재측정한다.
5. **장비가 다른 run은 같은 비교 셀에 넣지 않는다.** 두 장비 모두 지원 대상이므로([portability.md](portability.md)) 같은 프로필·같은 조건의 run이 다른 하드웨어의 결과일 수 있다. 실제로 쓰인 장비는 `RunRecord.device`에 남는다([decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md)). `--device`는 규칙 2의 **예외가 아니다** — 설정을 한 글자도 바꾸지 않고 기록만 남기므로 `--tool-runner`와 성격이 다르다.
6. **Evidence 등급(E2 이상)을 run 사이에 나란히 놓을 때는 인덱서 상태를 같은 칸에 적는다.** E2의 `graph_ok`는 1단계 `index` Evidence **하나**(run 전역)의 `ok`라, 인덱서가 실패한 run은 전 Finding이 E2·E3를 **동시에** 잃고(E4는 `p2`에 안 기댄다) 서는 run과 등급이 갈린다 — 등급이 근거 품질이 아니라 인덱서 가동률을 섞어 잰다. 인덱서 상태는 trace의 `tool_call`(`tool=index`)의 `ok`·`error`가 답한다(심볼 0개는 `ok=False`). 이 열 없이는 등급 비교를 하지 않는다 (이슈 #576 · [architecture.md](architecture.md) §4 E2 행).
   ⏵ **그 `ok=False`에는 경계가 있다 — 이 변경이 든 하네스 commit 전 run은 심볼 0개도 `ok=True`였다**(`judge()`의 `index` 갈래가 그 commit에 들어왔다 — 날짜가 아니라 commit이 가르는 값이다). 그래서 그 날 앞뒤 run의 `graph_ok`·E2는 같은 값이 아니고, 가르는 값은 `RunRecord`의 하네스 commit이다(§6). **다만 영향은 작다** — 심볼 0개가 기록된 커밋된 run은 **확인된 것이 0건**이다: 커밋된 실서빙 run 셋 중 심볼 수가 보고서에 남은 것은 [2026-10-12 run](experiments/harness/2026-10-12-serving-run-arvo1065-vastai-remote.md) 하나이고 그 값은 **심볼 321**이며(그 보고서 §3 retrieval 행의 `graph_index`), 나머지 둘(10-08·10-11)의 보고서에는 그 수가 없다. **「0건」은 전수가 아니라 그 기록이 없다는 뜻이다**(CLAUDE.md §0 — 안 잰 것을 0으로 읽지 않는다).

## 3. 평가 데이터셋

| 데이터 | 설명 | 확보 상태 |
| --- | --- | --- |
| CTF 시스템 해킹 문제 | BOF, UAF, Command injection 등 정답이 명확한 샘플 | TBD |
| Juliet Test Suite | C/C++ 취약 코드와 정상 코드 쌍 | TBD |
| 공개 CVE 패치 샘플 | 취약 버전 / 패치 버전 쌍 | TBD |
| 공개 펌웨어 샘플 | 실제 펌웨어 구조 | TBD |
| 정상 샘플(Benign) | 알려진 취약점 없는 code/binary/firmware | TBD |
| Fuzzing Crash 샘플 | sanitizer 또는 crash log 존재 | TBD |

**Benign 샘플은 선택이 아니라 필수다.** 패치된 버전, 안전한 API 사용 예제, crash가 없는 펌웨어 컴포넌트를 함께 넣어 "위험 함수가 있다"만으로 취약점 판정하는 편향을 잡는다. 취약/정상 비율은 최소 1:1로 유지한다.

오탐률 측정용 표본은 150건 규모로 고정하고, 프롬프트·모델 변경 시 회귀 측정에 재사용한다.

## 4. 데이터 누출 방지

| 기준 | 구현 방법 | 자동 검증 |
| --- | --- | --- |
| Train/Test 분리 | KB 인덱싱 대상에서 평가셋 artifact hash 제외 | hash 교집합 검사 |
| CVE ID Masking | CVE 번호·문제명·flag·write-up 제목을 프롬프트·context에서 치환 | 정규식 스캔 |
| Patch Leakage 방지 | 평가 대상의 patch diff를 retrieval 결과에서 배제 | capsule 내 diff 출처 검사 |
| 중복 데이터 제거 | 동일 코드·challenge·firmware 버전의 train/test 중복 차단 | 함수 단위 digest 비교 |
| Time Split | 기준 시점 이후 데이터는 KB에서 제외 | L0 수집 시각 필터 |
| Prompt Logging | 모델 입력 context 전체를 JSONL/OTel trace로 기록 | 로그 존재 검사 |
| Reproducibility | 동일 artifact·model·retrieval 조건에서 결과 재현 | 재실행 diff |

**코드 리뷰만으로는 보장할 수 없다.** 위 항목은 CI에서 실행되는 자동 검증 스크립트로 구현하고, 한 건이라도 실패하면 해당 run을 무효 처리한다. 스크립트 담당자·마감은 [open-questions.md](open-questions.md).

## 5. 지표

### 5.1 탐지 품질

| 지표 | 정의 |
| --- | --- |
| Recall@known | 정답이 알려진 샘플에서 탐지한 비율 |
| Precision | `proven` + `validated` 중 사람 검토에서 참으로 확인된 비율 |
| False Positive Rate | benign 샘플에서 취약점으로 판정한 비율 |
| AI-Human 일치도 | AI 판정과 사람 판정의 일치율 |
| 단계별 후보 제거율 | Scout / Evidence Gate / Critic / Tool Validation 각 단계 통과율. ⏵ **2026-10-08 — Evidence Gate 축은 그 날 앞뒤가 같은 지표가 아니다**(이슈 #552): 6단계가 모델 인용 대신 타깃 관측 Evidence로 등급을 매기게 되어 그전 값에는 **인용 순응률**이 섞여 있었다. 한 열에 이어 쓰지 않는다. 산출식 정본은 `trace.py`의 이벤트→지표 표 |
| 정적 신호 위치 겹침 (이슈 #563) | 6단계 게이트가 Evidence 위치와 후보 위치를 겹쳐 본 **횟수** - `RunRecord.location_overlap = {gated, static_ok, location_ok}`(`run_manifest.json`)와 `report.md`의 한 줄. 셋 다 `gate_eval`(stage=6) 행에서 센 정수다: `gated` = `location_ok` 키가 있는 행(`evidence_refs`가 비어 `p0`으로 단락한 행은 뺀다), `static_ok`·`location_ok` = 그중 참인 행. **run 단위 값이고 비율을 저장하지 않는다** - 둘을 나누면 그 run의 겹침률이다. 6단계 행이 0건이면 키 자체가 없다(`null` - 0이 아니라 「안 쟀다」). `gated`가 0인데 키가 있으면(6단계 행은 있으나 전부 `p0` 지름길) 겹침은 **미측정**이다 - 잰 0이 아니다. run·과제 사이에서 합산하지 않는다(§2.3의 비교 규칙대로 같은 프로필·같은 과제의 반복 run끼리만). ⚠ `location_ok` 거짓은 「안 겹쳤다」·「위치를 실은 Evidence가 없다」·「후보 위치가 없다」를 못 가른다 - `static_ok`가 참인데 `location_ok`가 거짓인 행이 #563의 모양이다. 7·8단계 행은 세지 않는다(후보 위치를 안 넘긴다). **이 필드 이전 run의 값은 보고서에서 손으로 읽은 것뿐이다** - #248 뒤 6단계 `location_ok`는 `run_20260924_0003` 36/36 참(스모크 · `checksum` 0 · [보고서](experiments/harness/2026-09-24-roster-smoke-atom.md) §3) · 2026-10-12 보고서 run(`run_20261011_0001`, 16:48Z 시작) 6/6 거짓(`static_ok` 6/6 참 - stage-6 `gate_eval` 6행을 `C:\w\s17-artifacts\run_20261011_0001\trace.jsonl`에서 재계수, 저장소 밖이라 안 커밋됐다)이고 과제가 다르다. `run_20261007_0001`은 6단계 행이 전부 `p0`만 참인 지름길이라 이 지표로는 **미측정**이다(D106의 오프라인 재등급 5/5 거짓은 재구성한 풀의 계산값이고 run 실측이 아니다). 산출식 정본은 `trace.py`의 이벤트→지표 표 |
| 스키마 파싱 실패율 | 모델·역할별 구조화 출력 실패 비율. **분자는 "일어난 실패"(시도 단위)** — 분자 `Σ(attempts−1 if ok else attempts) − count(error="ModelGatewayError")`, 분모 `Σ attempts`. 성공 호출의 실패는 `attempts−1`, 소진(`SchemaParseError`)은 `attempts`, 게이트웨이 거절은 파싱 대상이 없어 뺀다. 산출식 정본은 `trace.py`의 이벤트→지표 표 |

> ⚠ **`proven`은 「크래시가 재현됐다」가 아니다.** 그 상태를 여는 등급(E4)이 요구하는 것은
> 런타임 Evidence의 `ok=True`와 **0도 미측정도 아닌 `exit_code`**인데(2026-10-02부터 — 그전에는
> `ok=True`뿐이었다), 그 `ok`가 뜻하는 것은 **도구가 실행됐다**는 것까지이고 비-0은 **크래시의
> 대용 지표**일 뿐이다 — 대상이 스스로 비-0을 돌려줘도 같은 값이 나온다.
> `container_tools.judge()`는 런타임 도구의 exit 0(=크래시 없음)도 `ok=True`로 올리되 게이트가 그
> 건을 E4로 안 받고, Fake는 실행 자체를 하지 않고 종료 코드를 상수 `1`로 채운다. 산출물을
> `expected_signal`과 대조하는 코드는 없다.
>
> 그래서 Precision의 **분모**(`proven` + `validated`)에서 `proven`이 세는 것은 「재현된
> 건수」가 아니라 **「도구가 돌았고 비-0으로 끝난 건수」**다. ⏵ 2026-10-13(#232) **이 열을 누가
> 쓰든 `proven`의 뜻은 하나다 — 「실행됐고 비-0으로 끝났다」.** 재현 확정이 아니고(판정은 오라클 —
> ADR 0013·0014) 크래시 관측(`Evidence.crashed`)도 아직 이 열을 가르지 않는다(기록뿐이다). 「실행됐고 비-0으로 끝났다」와 실제 재현의 차이를 가르는 것은 같은 행의
> **사람 검토**이고 그쪽이 분자다. **§5.2의 `proven finding당 GPU-hour`도 `proven`이 분모다.**
>
> **2026-10-02에 `proven`의 정의가 좁아졌다(이슈 #232) — 그 날 앞뒤 run의 `proven`은 같은 값이
> 아니다.** 도구가 떴고 exit 0으로 끝난 재현은 그전에는 `proven`(`repro_confirmed`)이었고 지금은
> `validated`에 홀드된다(F-10b, `evidence_below_proven`). **`queued`(F-11, `repro_failed`)로 가는 것이
> 아니다** — 그래서 「제거율 — Tool」(분자 `repro_failed`, 분모 `repro_failed ∪ repro_confirmed ∪
> evidence_below_proven` — 산식 정본은 `trace.py`)의 분자에 안 들고, 분모 안에서 `repro_confirmed`에서
> `evidence_below_proven`으로 옮겨 앉을 뿐이다. **그 값은 이 변경으로 올라가지 않는다 — 지표가 좋아진
> 것이 아니다.** 줄어드는 것은 `proven` 건수이고, 그것을 분모로 쓰는 §5.2 `proven finding당
> GPU-hour`는 그 날 앞뒤를 이어 읽지 않는다 — 가르는 값은 `RunRecord`의 하네스 commit이다(§6).
> Precision의 분모는 `proven`과 `validated`의 합이라 이 이동만으로는 안 변한다. 컨테이너 run에서 이
> 변경이 `proven`을 얼마나 줄였는지는 **미실측(0)**이다 — 사유: asan이 exit 0으로 끝난 run이 없다.
> → [architecture.md](architecture.md) §4 · [open-questions.md](open-questions.md) D80(열림)

### 5.2 비용·효율

| 지표 | 정의 |
| --- | --- |
| input / output / cached token | 반드시 분리 기록 |
| 검색(embedding·reranker) 토큰·GPU-second | 모델 호출과 **분리 집계**한다. trace의 `retrieval_call` 이벤트가 소스이고 `model_call`과 분모가 다르다 — 검색 호출에는 출력 스키마가 없으므로 "스키마 파싱 실패율"에 섞이면 그 값이 희석된다 |
| cache hit ratio | prefix cache + capsule cache |
| GPU-second, peak memory | 역할별. **출처 라벨과 함께 읽는다** — 아래 참조 |
| wall-clock latency | 단계별, run 전체 |
| validated finding당 GPU-hour | 핵심 비용 지표. **분자에 컨테이너 도구 시간을 넣지 않는다** — semgrep·clang-tidy·build·asan은 GPU를 쓰지 않는다. 그 시간은 `Budget.tool_seconds`가 따로 센다 |
| proven finding당 GPU-hour | 상동 |
| 백만 input token당 유효 finding | 토큰 효율 |
| false positive 1건당 사람 triage 시간 | 실측(타이머 기록) |

**GPU-second는 출처 라벨과 함께 읽는다.** 지금 그 값은 요청 wall-clock에서 나오고
(`ModelResult.gpu_ms_source == "request_wall_clock"`), 그 근사는 **v1의 순차 실행 전제
위에서만** 성립한다 — vLLM은 continuous batching을 하므로 큐 대기가 섞이고 동시 요청이면
같은 GPU 시간이 두 번 세어진다. 큐를 스레드로 돌리게 되면 이 지표를 먼저 다시 정의해야
한다. 라벨은 trace의 `model_call`·`retrieval_call` 이벤트에 실리므로 **데이터만 보고도**
어느 방식으로 잰 값인지 알 수 있다.

**안 잰 것은 0이 아니라 `None`이다.** Fake 어댑터는 잴 GPU가 없으므로 `None`을 낸다.
실서빙 역할의 호출이 `None`으로 남으면 그 run의 GPU 비용은 미상이고, `incomplete_fields()`가
역할 이름과 함께 경고해 **비교표에서 제외**한다(2026-08-17까지 모델 호출이 GPU 시간을 아예
청구하지 못했으므로 그 이전 run은 전부 이 상태다).

**스키마 실패·PoV `inspect` 반복은 분모만 세운다 (#613·#618·#619 (d)).** (2026-10-13) 이슈 #613이 첫째를,
#618이 둘째를, #619 선택지 (d) 「계측만 한다」가 셋째를 요구했다. run 끝에 trace에서 센 집계 셋이
`run_manifest.json`과 `report.md`에 남는다 - `schema_failure_max_run_by_role`(역할별 연속
`SchemaParseError` `model_call`의 최장 길이 - (역할, 대상)별 연속의 역할별 최댓값),
`schema_recovered_calls_by_role`(역할별 `ok`이고 `attempts>1`인 호출 수 - 재시도로 살아난 호출),
`pov_inspect_max_run_by_finding`(Finding별 연속 `inspect` 홉의 최장 길이 - `action`을 실은 홉만
세므로 도구 호출이 예산에서 끊긴 홉도 든다. 행동을 실은 홉이 하나도 없는 Finding - PoV 루프가
첫 홉 전에 던졌거나 모든 홉의 호출이 실패한 Finding - 은 dict에 **없다**(`0`이 아니다)). 산식은
`src/orchestrator/trace.py`의 같은 이름 함수다. **하네스는 이 값으로 동작하지 않는다** - 연속 실패
상한 같은 임계값은 이 집계를 본 **실험팀이** 정한다(ADR 0012 결정 5). 해당 종류의 trace 행이 하나도
없으면 값은 `None`이다(안 쟀다 - `0`이 아니다). 집계 함수가 던져서 `None`이 된 키는 `run_manifest.json`의
`trace_aggregate_failures`(키 → 사유)에도 들고 report.md가 그 줄에 「집계 실패: 사유」를 적는다 - 「행이
없다」와 「집계가 죽었다」를 `None` 하나로는 못 가른다.

### 5.3 Frontier 대비 비교표

최종 산출물의 모든 칸을 **실측**으로 채운다.

| 항목 | 본 시스템 | Frontier 기준선 |
| --- | --- | --- |
| 탐지 성능 | 측정값 | 공개 기준선(CodeQL 27, GPT-4 55 등) |
| 취약점 1건당 비용 | 대여 박스 가동시간 × 시급(⏵ 2026-10-12 — 전력·감가 환산은 ATOM 종료로 소멸. 가동시간은 §5.2의 GPU-hour(도구 시간 제외)와 다른 양이다) | API 요금 |
| 사람 검토 시간 | 실측 | 실측 또는 문헌 |
| 외부 전송 데이터량 | 0 (온프레미스) — ⏵ 2026-10-12 운영이 vast.ai 대여 박스라 분석 대상 소스가 그 박스로 간다. 이 칸의 정의·값은 미정 | 전송 코드량 |

비용 환산식(GPU-hour → 금액)은 ~~전력 단가와 감가 가정을 명시해 고정한다~~ ⏵ 2026-10-12 — 운영 장비가 대여(vast.ai)라 박스 가동시간 × 시급으로 계산한다([10-11 실서빙 보고서](experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md) §7 「계산값(대여 시간 × 시급)」 · 시급은 저장료 제외 $1.444/hr). 전력·감가 가정은 ATOM 사용 종료(2026-09-29, PR #340)로 전제가 소멸했다 — [D5](open-questions/D/D5.md). 비교표 칸을 어떻게 채울지는 실험팀 몫이다([ADR 0012](decisions/0012-harness-canon-v2.md) 결정 5).

> 킥오프 02-2는 "자체 장비에서 실행한 로컬 모델은 (CyberGym 규칙상) 비용 0으로 산정 가능"이라고 본다. 이 프로젝트는 **두 값을 모두** 보고한다: 외부 벤치마크 규칙에 맞춘 API 비용 0, 그리고 실제 소모 자원인 GPU-hour 환산 비용. 후자를 빼면 "싸다"는 주장이 검증 불가능해진다. ⏵ 2026-10-12 — 「GPU-hour 환산 비용」은 대여 박스 가동시간 × 시급이다(10-11 보고서 §7 · 10-12 보고서(PR #621, 미머지) §11 — 둘 다 계산값).

## 6. 실행 기록

모든 run은 `RunRecord`([agent-contracts.md](agent-contracts.md) §3)를 남긴다. 최소 항목: repo commit/source hash, **하네스 저장소의 dirty 여부**, model·tokenizer·quantization checksum, prompt·agent spec 버전, tool/container image digest, budget·threshold, random seed, KB 스냅샷 버전.

repo commit만으로는 하네스 자신의 미커밋 변경을 잡지 못한다 — source hash는 *분석 대상*만 본다. 설정을 고친 채로 돈 run은 commit이 같아도 재현되지 않으므로 dirty 여부가 최소 항목이다.

기록이 불완전한 run의 결과는 비교표에 넣지 않는다.

**단 "완주했다"가 "전량을 봤다"는 아니다 (D44).** critic 호출이 Finding 단위로 실패하면 그
Finding만 빠지고 run은 정상 종료하므로 `RunRecord.is_complete`가 참이고 매니페스트도 남는다.
그 run은 비교표에 **들어갈 수 있지만 Finding 수를 그대로 나란히 놓으면 안 된다** — 분모가
줄어든 것이 조건 차이가 아니라 파싱 실패이기 때문이다. trace의 `critic_failed`가 그 수이고,
0이 아닌 run은 그 값을 함께 적는다. 같은 성질의 절단이 `target_skipped`·`critic_skipped`에도
있다. **4단계에도 같은 것이 있다** — Scout 풀에서 한 대상의 모든 자리가 실패하면 그 대상은
분석되지 않는데, 리포트의 `unanalyzed_targets`(팬아웃된 대상 − 후보를 낸 대상)가 그 수이고
0이 아닌 run은 그 값을 함께 적는다. `dropped`(시도 수)와 **단위가 다르다** — 형제 멤버가 같은
대상을 보므로 시도가 폐기돼도 대상은 분석될 수 있다.

**거꾸로, 한 자리가 아무것도 못 본 run은 완주가 아니다 (D88).** 크기가 아닌 400(요청 모양·서버
설정 거절 — 다음 요청도 같다)은 첫 건에서, 시도한 대상에서 한 번도 성공하지 못한 자리가 있는
루프는 끝에서 run을 멈춰 `stopped_after`를 채운다 — 그 run은 비교표에서 빠진다. 시도 수에
바닥이 없다 — 자리당 시도가 한 건이면 그 한 건의 실패로 빠진다. 일부만 실패한 run은 위 문단
그대로다.

**샘플링은 보낸 값·선언값·의도한 기동 인자까지 기록되고 서버의 실효값은 안 기록된다 (D29).** 보낸 값은
`sampling`·`model_request_params`, 모델 파일이 선언한 값은 `model_sampling`, 장비 파일이 의도한 기동 인자는
`serving_launch_intended`(교대 run만 — **의도지 실제 argv의 증거가 아니다**; 교대 명령이 보고한 실제 argv는
trace `model_swap`의 `report.server.<key>.args`)다. 서버가 실제로 적용한 기본 샘플링은 서버에 묻지 않아 **미측정**이다
— `--generation-config vllm` 여부는 인자에 적혔을 때만 위 두 곳에서 읽힌다.

## 7. MVP 성공 기준

완전 자동 Zero-day 탐지가 아니다. **취약점 후보 수집·우선순위화·RCA 초안·근거 정리·오탐 검토를 자동화해 분석 시간을 단축하는지**가 기준이다.

- 최소: 4개 조건 모두 동일 예산에서 실행되고 지표가 기록된다
- 기본: benign 표본에서 오탐이 유의미하게 감소한다(B3 → B3')
- 최종: 제보 가능한 신규 취약점 확보 → [disclosure-policy.md](disclosure-policy.md)
