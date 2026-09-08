# 평가·벤치마크 프로토콜

## 1. 검증하려는 질문

작은 오픈 모델을 여러 개 묶었을 때 Frontier 모델의 취약점 탐지 성능에 어디까지 근접하며, 그 대가로 비용과 프라이버시에서 얼마나 이득을 얻는가.

목표는 최고 성능이 아니라 **정확도와 비용을 함께 고려한 최적 구조**다. 따라서 모든 비교는 **계산량을 고정한 상태**에서 한다.

## 2. 비교 설계

### 2.1 2×2 설계 (PRD §2.1)

| 구분 | 같은 질문을 여러 번 | 서로 다른 질문으로 역할 분담 |
| --- | --- | --- |
| 단일 모델 | B1 | B1' |
| **여러 회사 모델**(앙상블) | B3 | B3' |

- **역할 분담 효과** = B1 → B1' 성능 향상
- **모델 혼합 효과** = B1' → B3' 성능 향상

> 킥오프 발표자료 02-3은 앙상블 축을 "여러 **회사** 모델"로 정의한다. 같은 모델의 self-consistency나 같은 계열의 다른 크기가 아니라 **제작사가 다른 모델**을 섞는 것이 B3/B3'의 조건이다. Critic을 Devstral(Mistral 계열)로 두는 근거가 여기서 나온다.

### 2.2 파이프라인 구성 조건 (PRD §5.2)

| 조건 | 구성 | 비교 목적 |
| --- | --- | --- |
| B1 | 단일 LLM 또는 단순 Retrieval 기반 소스코드 분석 | Source-only 접근의 한계 확인 |
| B1' | 바이너리·펌웨어 artifact를 포함한 분석 | 입력 범위 확장 효과 검증 |
| B3 | Multi-Agent Orchestration(Scout, Auditor, Critic) 적용 | 역할 분리·모델 협업 효과 검증 |
| B3' | B3 + Evidence Gate + Tool Validation | 오탐 감소 및 근거 품질 향상 검증 |

> 2.1의 2×2와 2.2의 파이프라인 조건은 같은 라벨을 쓰지만 축이 다르다. 실행 시 `RunRecord.condition`에 **어느 정의를 쓴 조건인지** 명시한다(`B1@2x2` / `B1@pipeline`). 라벨 통합 여부는 [open-questions.md](open-questions.md)에서 확정한다.

### 2.3 공정성 규칙

1. 모든 조건은 **동일 계산 예산**(input/output token 상한, GPU-second 상한)에서 실행한다.
2. 조건 간 차이는 프로필과 파이프라인 단계 활성화만. retrieval 설정·프롬프트 버전·데이터셋은 고정한다.
   - **예외 하나**: `--tool-runner`가 프로필의 도구 런타임을 덮어쓸 수 있다([decisions/0005](decisions/0005-tool-runner-override.md)). **조건 비교에 쓰는 실서빙 프로필은 이 예외를 쓰지 않는다** — 각 프로필이 **선언한** 런타임이 주 실서빙 장비(ATOM)에 실제로 있기 때문이다([portability.md](portability.md) §3). 그 프로필은 `b1_single`·`local_balanced` **둘이고 전부 `docker`를 선언한다**(2026-08-28에 앞의 둘을 `podman`에서 되돌렸다 — [decisions/0005](decisions/0005-tool-runner-override.md) 마지막 절). 예외가 남는 곳은 프로필이 선언한 런타임이 그 장비에 없는 경우다(예: `dev_singularity`를 ATOM에서 — ATOM에는 docker와 podman이 있고 singularity는 없다). 실제로 쓰인 값은 언제나 `RunRecord.tool_runner`에 남는다. **런타임이 다른 run은 같은 비교 셀에 넣지 않는다.** `tool_images` digest가 런타임 사이에서 비교되지 않기 때문이다([portability.md](portability.md) §5 #4). 둘이 같은 값을 선언하므로 지금은 이 규칙이 **과거 run에만** 걸린다 — podman으로 도구를 돌린 run은 `run_20260817_0001`(dev 프로필) 하나이고 그것은 조건 비교 대상이 아니다.
3. 같은 조건은 seed를 바꿔 최소 3회 반복하고 분산을 함께 보고한다.
4. 프롬프트 버전이 바뀌면 이전 결과와 직접 비교하지 않고 재측정한다.
5. **장비가 다른 run은 같은 비교 셀에 넣지 않는다.** 두 장비 모두 지원 대상이므로([portability.md](portability.md)) 같은 프로필·같은 조건의 run이 다른 하드웨어의 결과일 수 있다. 실제로 쓰인 장비는 `RunRecord.device`에 남는다([decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md)). `--device`는 규칙 2의 **예외가 아니다** — 설정을 한 글자도 바꾸지 않고 기록만 남기므로 `--tool-runner`와 성격이 다르다.

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
| 단계별 후보 제거율 | Scout / Evidence Gate / Critic / Tool Validation 각 단계 통과율 |
| 스키마 파싱 실패율 | 모델·역할별 구조화 출력 실패 비율. **분자는 "일어난 실패"(시도 단위)** — 분자 `Σ(attempts−1 if ok else attempts) − count(error="ModelGatewayError")`, 분모 `Σ attempts`. 성공 호출의 실패는 `attempts−1`, 소진(`SchemaParseError`)은 `attempts`, 게이트웨이 거절은 파싱 대상이 없어 뺀다. 산출식 정본은 `trace.py`의 이벤트→지표 표 |

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

### 5.3 Frontier 대비 비교표

최종 산출물의 모든 칸을 **실측**으로 채운다.

| 항목 | 본 시스템 | Frontier 기준선 |
| --- | --- | --- |
| 탐지 성능 | 측정값 | 공개 기준선(CodeQL 27, GPT-4 55 등) |
| 취약점 1건당 비용 | GPU-hour → 전력·감가 환산 | API 요금 |
| 사람 검토 시간 | 실측 | 실측 또는 문헌 |
| 외부 전송 데이터량 | 0 (온프레미스) | 전송 코드량 |

비용 환산식(GPU-hour → 금액)은 전력 단가와 감가 가정을 명시해 고정한다. → [open-questions.md](open-questions.md)

> 킥오프 02-2는 "자체 장비에서 실행한 로컬 모델은 (CyberGym 규칙상) 비용 0으로 산정 가능"이라고 본다. 이 프로젝트는 **두 값을 모두** 보고한다: 외부 벤치마크 규칙에 맞춘 API 비용 0, 그리고 실제 소모 자원인 GPU-hour 환산 비용. 후자를 빼면 "싸다"는 주장이 검증 불가능해진다.

## 6. 실행 기록

모든 run은 `RunRecord`([agent-contracts.md](agent-contracts.md) §3)를 남긴다. 최소 항목: repo commit/source hash, **하네스 저장소의 dirty 여부**, model·tokenizer·quantization checksum, prompt·agent spec 버전, tool/container image digest, budget·threshold, random seed, KB 스냅샷 버전.

repo commit만으로는 하네스 자신의 미커밋 변경을 잡지 못한다 — source hash는 *분석 대상*만 본다. 설정을 고친 채로 돈 run은 commit이 같아도 재현되지 않으므로 dirty 여부가 최소 항목이다.

기록이 불완전한 run의 결과는 비교표에 넣지 않는다.

**단 "완주했다"가 "전량을 봤다"는 아니다 (D44).** critic 호출이 Finding 단위로 실패하면 그
Finding만 빠지고 run은 정상 종료하므로 `RunRecord.is_complete`가 참이고 매니페스트도 남는다.
그 run은 비교표에 **들어갈 수 있지만 Finding 수를 그대로 나란히 놓으면 안 된다** — 분모가
줄어든 것이 조건 차이가 아니라 파싱 실패이기 때문이다. trace의 `critic_failed`가 그 수이고,
0이 아닌 run은 그 값을 함께 적는다. 같은 성질의 절단이 `target_skipped`·`critic_skipped`에도
있다.

## 7. MVP 성공 기준

완전 자동 Zero-day 탐지가 아니다. **취약점 후보 수집·우선순위화·RCA 초안·근거 정리·오탐 검토를 자동화해 분석 시간을 단축하는지**가 기준이다.

- 최소: 4개 조건 모두 동일 예산에서 실행되고 지표가 기록된다
- 기본: benign 표본에서 오탐이 유의미하게 감소한다(B3 → B3')
- 최종: 제보 가능한 신규 취약점 확보 → [disclosure-policy.md](disclosure-policy.md)
