# 하네스 구현 현황

> 기준일: 2026-08-13 · 대상: `src/` 전체
> 갱신 규칙: 단계 상태나 블로커가 바뀌면 이 문서를 먼저 고치고 관련 문서에 반영한다.

---

## 1. 한눈에

**오케스트레이션 로직은 완성, 실제 동작(어댑터)은 대부분 비어 있다.**

`dev_fake` 프로필로 0~9단계가 완주하고, 동일 조건 재실행 시 trace가 재현된다.
비어 있는 부분은 전부 `src/adapters/` 쪽이므로 **오케스트레이터를 고치지 않고 교체 가능**하다.

```
로직 (src/orchestrator/, src/schemas/, src/interfaces/)   완성
실제 동작 (src/adapters/)                                  Fake 위주
```

---

## 2. 파이프라인 단계별 상태

| # | 단계 | 로직 | 실제 동작 | 상태 | 담당 트랙 |
|:-:|---|:-:|---|:-:|---|
| 0 | Preprocess | ✅ | 확장자 분류 + SHA-256 | 🟡 부분 | T2 |
| 1 | Index | ✅ | 디렉터리 존재만 확인 | 🔵 **진행 중** | T3 |
| 2 | Signals | ✅ | 실제 semgrep·clang-tidy·clang (`dev_docker`) / 정규식 2개 (`dev_fake`) | 🟡 부분 | T6 |
| 3 | Retrieval | ✅ | 파일 텍스트 자르기 | 🟡 흉내 | T4 |
| 4 | Scout | ✅ | 가중합 휴리스틱 (Fake) | 🟡 Fake 모델 | T1/T5 |
| 5 | Auditor | ✅ | 템플릿 조합 (Fake) | 🟡 Fake 모델 | T1/T5 |
| 6 | **Gate** | ✅ | **결정론 판정** | 🟢 **완성** | T2 |
| 7 | Critic | ✅ | graph 유무로 uphold/rebut (Fake) | 🟡 Fake 모델 | T1/T5 |
| 8 | Validate | ✅ | 컨테이너에서 빌드·실행 (PoC 부재로 실행까지 못 감) | 🟡 부분 | T6 |
| 9 | **Report** | ✅ | 템플릿 렌더링 | 🟢 **완성** | T2 |

**6·9단계만 최종본이다.** 둘 다 모델·도구를 쓰지 않는 단계라서 지금 코드가 그대로 최종 구현이다.

---

## 3. 완성된 것

### 오케스트레이션 뼈대
- `runner.py` — 0~9단계 순차 실행, `--stop-after` / `--dry-run` 상한 처리
- `context.py` — 모든 부작용이 지나는 단일 통로 (`call_model` / `call_tool` / `evaluate_gate` / `decide` / `apply`)
- 노드 10개 — 각 단계의 입출력 계약 확정

### 판정 계층
- `gate.py` — E0~E4 근거 등급. 순수 함수. 6·7·8단계에서 각각 재판정
- `policy.py` — 6개 결정 지점(`FixedThresholdPolicy`). 임계값은 전부 config에서
- `state_machine.py` — 전이 표와 검증

### 데이터 계약
- `src/schemas/` — 8종 객체. 조건부 불변식을 검증기가 강제
- `src/interfaces/` — 4개 Protocol (ModelClient / Retriever / ToolRunner / StateStore)
- `prompts/{scout,auditor,critic}/v1/` — prefix/body 분리 강제

### 재현성 · 관찰
- `RunRecord` 필수 항목 강제 + `incomplete_fields()` 경고
- `_claim_run_dir()` — 원자적 디렉터리 선점으로 덮어쓰기 차단
- `TraceWriter` — 단계별 JSONL. `model_call` / `retrieval_call` 분리 기록
- `BudgetLedger` — 토큰·GPU초 상한. 소진 시 부분 결과로 리포트

### 평가 보조
- `eval/leakage/` — 누출 검사 7개 중 2개 (hash 교집합, ID 마스킹). CI에서 동작

---

## 4. 진행 중

### 1단계 인덱서 — tree-sitter (T3)

**현재:** `FakeToolRunner`가 디렉터리 존재만 확인하고 `Evidence(kind=GRAPH, ok=True)`를 반환한다.

**영향:** Gate E2 조건 `graph_ok`가 내용 없이 참이 되고, 캡슐의 `callers`/`callees` 섹션이 고정 64토큰으로 채워진다 → **E2 판정과 토큰 회계가 동시에 부정확하다** (D13)

**필요 산출물:**
```json
{
  "functions": [{"name","file","line_start","line_end"}],
  "calls":     [{"caller","callee","line"}]
}
```
Evidence는 `payload_ref`로 경로만 들고 있으므로 **스키마 변경 없이 붙는다.**

**연쇄 효과:**
- 캡슐이 파일 단위 → 심볼 단위로 좁혀진다 (`Location`에 `symbol` / `line_start`)
- Gate E1의 `location_ok`(`Location.overlaps`)가 작동하기 시작한다
- **후보 수와 제거율의 분모가 바뀌므로 도입 전/후 지표는 직접 비교하지 않는다**

---

## 5. 미착수 — 빈칸

### 🟡 8단계 실행 환경 — `ContainerToolRunner` (2026-08-14)

**들어왔다.** `src/adapters/container_tools.py`가 Docker·Singularity 안에서 실제 도구를 돌린다.
런타임은 생성자 인자이고 프로필이 고른다(`dev_docker` / `dev_singularity`). 이미지는
`harness-tools:v1` 하나이며 semgrep 룰셋까지 구워 넣는다 — 샌드박스가 `--network=none`이라
레지스트리를 못 보기 때문이고, 그 덕에 이미지 digest가 도구 버전과 룰셋 버전을 함께 고정한다
(준비 절차는 `tools/README.md`).

**해소된 것:** `RunRecord.tool_images`가 채워진다 → 그 run이 비교표에 들어갈 수 있다.

**해소되지 않은 것 — `proven`(E4)은 여전히 도달 불가다.**
E4 조건은 `SANITIZER`/`CRASH`/`POC_RUN` Evidence인데, 이 구현은 **컴파일 성공을 실행으로 세지
않는다.** `asan`·`poc`는 링크와 실행까지 성공해야 `ok=True`이고, 빌드 단계에서 끊기면
`ok=False`다. 그렇게 하지 않으면 **한 번도 돌지 않은 프로그램이 재현 완료로 기록된다** —
게이트가 가장 비싼 곳에서 거짓말을 하게 된다.

지금 픽스처(`tests/fixtures/target/parser.c`)에는 `main`이 없어 링크되지 않고, `./poc`를
만드는 주체도 없다. 즉 **E4를 막고 있는 것은 실행 환경이 아니라 PoC 생성**이다(아래 PoV 항목).
중간 발표 DoD "`proven` 1건 이상 재현"은 PoV가 들어와야 성립한다.

**남은 한계 2가지:**
- `index`가 `clang -fsyntax-only`다. **call graph가 아니라 구문 검사 통과일 뿐인데
  `EvidenceKind.GRAPH`는 그보다 강한 것을 주장한다**(D13). 이걸 두는 이유는 `index`가
  실행 불가가 되면 E2의 `graph_ok`가 무너져 8단계가 영원히 도달 불가가 되기 때문이다.
  **include 경로 설정이 필요한 실제 저장소에서는 이 명령이 실패해 GRAPH Evidence가 생기지
  않고, 그러면 모든 Finding이 E2를 넘지 못한다.** 픽스처는 표준 헤더만 써서 통과한다.
  tree-sitter 인덱서(T3)가 들어오면 이 다리를 걷어낸다.
- clang-tidy는 exit code만으로 "경고 발견"과 "컴파일 실패"를 구분하지 못한다. 출력의
  `clang-diagnostic-error` 표지로 가르는데 문자열 매칭이라 clang이 표기를 바꾸면 깨진다(D11).

**1차 실행에서 잡힌 결함 (2026-08-14, 수정 완료):**
- **semgrep이 한 번도 돌지 않았는데 정적 신호로 집계됐다.** `--read-only` 아래
  `$HOME/.semgrep/settings.yml`을 쓰려다 처리되지 않은 예외로 죽었고, 파이썬 크래시라
  exit=1이었으며, exit 1이 "발견함"으로 읽혔다. 이미지에 `ENV HOME=/work`를 넣어 원인을
  없애고(이미 `TMPDIR`로 겪은 것과 같은 클래스라 도구별 대응 대신 쓰기 경로를 한곳에
  모았다), `judge()`는 `--json` 결과 문서의 유무로 "실제로 돌았는지"를 판정하게 했다.
  **교훈: 빌드 시점 연막 검사는 `--read-only`를 재현하지 못한다.** 회귀 그물은
  `tests/test_container_tools.py::test_real_container_splits_vulnerable_from_benign`가 맡는다.
- **clang-tidy만 `-std=c11`이었다.** 같은 `util.c`가 index에서는 통과하고 clang-tidy에서는
  `strnlen` 미선언 오류였다. 모든 clang 도구를 `-std=gnu17`로 통일했다(`_STD_ARG`).

**Slurm 검증은 별도다.** <node>의 SIF 변환은 아직이다.

### 🔴 PoV(PoC) 생성

**현재:** Auditor가 `ReproSpec(steps=["./build.sh --sanitize=address", "./poc <file>"])`를 만들지만, **`poc` 실행파일을 만드는 주체가 없다.**

**선택지:**
| 방향 | 내용 |
|---|---|
| A | 데이터셋에 딸린 정답 PoC 재생 (CyberGym/OSS-Fuzz) |
| B | LLM이 PoC 생성 — `Role.POV` + `prompts/pov/v1/` + `nodes/pov.py` 추가 |
| C | 퍼징으로 크래시 발견 (비용 큼, 후순위) |

`Role` 열거형과 프롬프트 로더 구조상 **B는 노드 하나 추가로 붙는다.**

### 🟡 진짜 정적분석 (2단계)

현재 정규식 2개:
```
\bmemcpy\s*\([^,]+,[^,]+,\s*[a-zA-Z_]     길이가 상수가 아닌 memcpy
\b(gets|strcpy|sprintf)\s*\(              위험 함수
```
`dev_fake` 프로필에서만 쓰인다. `dev_docker`/`dev_singularity`에서는 이미지에 구워 넣은
semgrep 룰셋(공개 `semgrep-rules/c` + `tools/harness-tools/rules/`)이 돈다 —
`p/c` 같은 레지스트리 참조는 `--network=none` 아래에서 실패하므로 쓸 수 없다.
`tools/harness-tools/rules/harness-c.yaml`이 위 정규식과 같은 것을 잡는다: 두 프로필이 같은
표본에서 같은 판정을 내야 결과를 비교할 수 있다.

### 🟡 0단계 보완

- `file(1)` 기반 분류 (현재 확장자 매핑) — `media_type`에 MIME이 아니라 확장자가 들어감
- 펌웨어 추출(binwalk) — `Artifact.parent_id`가 한 번도 세팅되지 않음
- 심볼릭 링크 방어 — `is_file()`이 링크를 따라가 루트 밖을 읽을 수 있음
- 제외 규칙 — `.git/`, 빌드 산출물이 전부 Artifact로 잡힌다

### 🟡 실서빙 어댑터 검증

`OpenAICompatModelClient` / `vllm_retrieval.py` / `HybridRetriever`는 구현돼 있으나 ATOM 검증 전이다.

---

## 6. 블로킹 이슈

| # | 항목 | 기한 | 상태 |
|---|---|---|:-:|
| **R1** | ATOM(GB10, sm_121) × vLLM 커널 호환성 미검증 | **2026-08-17** | 미착수 |
| **R3** | 누출 검증 7개 중 5개 미구현 (Patch Leakage, 중복 제거, Time Split, Prompt Logging, Reproducibility) | 2026-09-07 | 부분 |
| **R6** | prefix cache가 장부에 `cached_tokens: 0`으로 기록됨 | 2026-09-07 | ATOM 대기 |
| R4 | 단일 노드 대역폭 병목 미파악 | 2026-09-05 | 미착수 |

### R1 — 크리티컬 패스

`model-serving.md` §3 체크리스트 7개 전부 미체크:

```
[ ] vLLM × CUDA 13.0 × sm_121 커널 호환성
[ ] 역할별 모델 로드 및 실제 통합메모리 점유
[ ] context 8K/16K/32K에서 KV cache 점유와 처리량
[ ] 단일 스트림 tok/s vs 배치(8/16/32) 합산 tok/s
[ ] automatic prefix caching 동작 및 적중률       ← R6 판정 지점
[ ] structured output(JSON schema 강제) 동작
[ ] NVFP4 양자화 시 Critic 반박 정확도 저하
```

**이것이 통과하기 전까지 `models.yaml`의 `benchmark` 값은 전부 0이고, 프로덕션 프로필로 올릴 수 없다.**

### R6 — 배경

2026-08-11 Slurm 실측: vLLM `/metrics` 기준 prefix 재사용 **99.0%**(1067 중 1056),
`wall_ms` 5495 → 2540 (2.2배 단축)이 확인됐으나 응답의 `prompt_tokens_details`가 `null`이라
하네스 장부에는 `cached_tokens: 0`으로 잡힌다.

**어댑터 결함이 아니다.** 서버가 값을 주지 않는다. Slurm는 드라이버 535(CUDA 12.2 상한)라
vLLM 0.19.1까지만 쓸 수 있어 최신 동작 확인이 불가능하다. **ATOM(CUDA 13.0)에서 먼저 확인한다.**

---

## 7. 검증 완료 (2026-08-11, Slurm)

하드웨어·버전 무관 항목이라 ATOM으로 그대로 이전된다.

| # | 항목 | 결과 |
|---|---|---|
| V1 | pydantic `$defs` 중첩 스키마의 구조화 출력 강제 | ✅ 5회 요청 전부 유효 JSON |
| V2 | prefix를 system 메시지로 분리한 캐시 전략 | ✅ 재사용 99.0%, 응답 2.2배 단축 |

**V1이 성립하지 않았다면 `agent-contracts.md`의 계약과 `SchemaParseError` 경로 전체를 재설계해야 했다.**

### 같은 검증에서 발견된 결함 (수정 완료)

| 이슈 | 내용 | 조치 |
|---|---|---|
| 1 | run_id가 같은 날 실행끼리 충돌해 산출물이 덮임 | `_claim_run_dir()` 원자적 선점 |
| 2 | `--stop-after` 중단인데 사유가 `budget_exhausted`로 기록 | `ReasonCode.USER_STOP_REQUESTED` 분리 |
| 4 | 워킹트리 dirty 여부 미기록 | `RunRecord.repo_dirty` 추가 |

---

## 8. 모델 비교표에서 제외되는 항목

`incomplete_fields()`가 판정한다. 아래가 채워지기 전 run은 조건 간 비교에 넣지 않는다.

| 항목 | 사유 | 해소 조건 |
|---|---|---|
| `tool_images` | 컨테이너 미사용 (`fake` tool_runner) | `dev_docker`/`dev_singularity` 프로필 + `harness-tools:v1` 이미지 |
| `kb_snapshot` | KB 미구축 (`kb-none` 자리표시자) | KB 구축 + `--kb-snapshot` |
| `model_checksums` | registry가 합성 예시값(전부 0) | 실서빙 모델 확정 |
| GPU-second | 실측 전 | R1 통과 |

---

## 9. 미해결 결정 사항

| # | 항목 | 영향 |
|---|---|---|
| **D2** | `scout_threshold: 0.5`가 근거 없는 초기값 | 조건 간 비교 전 확정 필요. `cli.py`의 `PROVISIONAL_THRESHOLDS`가 `doctor`에서 경고. **2026-08-14 실측: benign 표본이 정확히 0.50으로 경계 통과.** 지금 바꾸지 않는다 — 픽스처가 떨어지도록 맞춘 값이 될 뿐이다 |
| **D9** | 소스 저장소 우선 vs 펌웨어 우선 | 0·1단계 구현 순서가 갈린다 |
| **D13** | 캡슐의 `callers`/`callees`를 무엇이 채우는가 | 1단계 인덱서 도입으로 해소 예정 |
| D1 | B1/B1'/B3/B3' 라벨이 2×2와 파이프라인 두 축으로 사용됨 | `RunRecord.condition`에 `@2x2`/`@pipeline` 명시로 임시 봉합 |
| D10 | 재평가 카운터가 run 범위 in-memory | 다중 run·resume 시 `requeue_limit_reached`가 성립 안 함 |
| D11 | `Evidence`에 "발견 건수" 필드 부재 | **semgrep은 해소**: `--json` 결과 문서의 유무로 판정하므로 exit code 관례에 의존하지 않는다(2026-08-14 크래시가 "발견함"으로 집계된 사건 이후). **clang-tidy는 여전히 미해결** — 컴파일 실패와 경고 발견이 같은 exit code라 출력 문자열(`clang-diagnostic-error`)로 가른다 |
| D12 | build 실행 단계 소속 (2단계 vs 8단계) | 문서와 구현 불일치. 현재 코드는 2단계에 프로브 |
| D3 | `capsule_expansion_max: 3` 초기값 | 실측 후 조정 |

---

## 10. 해석 시 주의

### 심볼 인덱스 도입 전/후 지표를 직접 비교하지 않는다

현재 캡슐 대상은 **파일 단위**(`Location(file=...)`, symbol 없음)다.
1단계 인덱서가 들어오면 심볼 단위로 좁혀지고 **후보 수와 제거율의 분모가 바뀐다.**

### Fake 어댑터의 수치는 성능이 아니다

`FakeModelClient`의 점수 가중치(`0.30 + 0.20 + 0.20 + 0.10`)는
**테스트 대역의 휴리스틱**이며 임계값 경계에 걸리지 않도록 잡은 값이다.
실서빙 모델의 `score` 산출 방식과 무관하다.

**2026-08-14 실측 — 그 "경계에 걸리지 않도록"이 실제 도구에서는 성립하지 않았다.**
`dev_docker`로 돌리자 benign 표본 `util.c`가 `scout_threshold`와 정확히 같은 **0.50**으로
통과했다. 여유가 사라진 이유는 가중치가 아니라 **신호원이 바뀌었기 때문**이다: Fake
신호 생성기(정규식 2개)는 `util.c`에서 아무것도 잡지 않지만, 실제 정적분석기는 잡는다
(clang-tidy `DeprecatedOrUnsafeBufferHandling`, semgrep `insecure-use-string-copy-fn` —
둘 다 `_s` 계열을 쓰지 않는 모든 복사를 잡는 룰이라 경계를 지킨 코드도 걸린다).

**더 중요한 것은 그 다음이다. 오탐이 끝까지 살아남았다.**
정적분석의 오탐은 정상이고 그것을 거르는 것이 뒤 단계의 역할인데, 이번 실행에서는 못
걸렀다 — Fake auditor는 항상 path를 만들고 Fake critic은 graph만 있으면 uphold하므로
`util.c`의 Finding도 E3까지 올라가 8단계에 도달했다. `dev_fake`가 "후보 2건 / Finding 1건",
`dev_docker`가 "후보 2건 / Finding 2건"인 차이가 그 증거다.

→ **Fake 모델로는 오탐 제거율을 측정할 수 없다.** 제거율 지표는 실서빙 모델이 붙기
전까지 의미가 없고, 그때까지 나온 수치를 "파이프라인이 후보를 거른다"의 근거로 쓰지
않는다. 도구도 픽스처도 임계값도 이 때문에 고치지 않는다(D2).

### Scout 점수 산출 방법론이 미정이다

프롬프트가 정하는 것은 `score | 0.0~1.0. 더 볼 가치의 정도` 한 줄뿐이다.
모델별로 점수 분포가 달라질 수 있어, 같은 임계값이 조건마다 다른 의미가 될 수 있다.
루브릭 명시 또는 점수 결정론화 검토 필요. (D2와 함께 논의)

---

## 11. 관련 문서

| 문서 | 다루는 것 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 파이프라인 단계, 상태 전이 |
| [docs/agent-contracts.md](docs/agent-contracts.md) | 역할별 실행 계약, 입출력 스키마 |
| [docs/model-serving.md](docs/model-serving.md) | 하드웨어, Registry, 배포 전 검증 체크리스트 |
| [docs/evaluation.md](docs/evaluation.md) | 조건 정의, 누출 방지, 지표 |
| [docs/open-questions.md](docs/open-questions.md) | 미해결 항목 원본 |
| [docs/roadmap.md](docs/roadmap.md) | 마일스톤과 DoD |
