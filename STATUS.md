# 하네스 구현 현황

> 기준일: 2026-08-14 · 대상: `src/` 전체
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
| 8 | Validate | ✅ | 컨테이너에서 빌드·실행 (docker·singularity·podman 검증 완료. PoC 부재로 실행까지 못 감) | 🟡 부분 | T6 |
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

**들어왔다.** `src/adapters/container_tools.py`가 Docker·Singularity·podman 안에서 실제 도구를
돌린다. 런타임은 생성자 인자이고 프로필이 고른다 — 개발 프로필은 `dev_docker` /
`dev_singularity`, 실서빙 프로필(`local_balanced`·`b1_single`)은 `podman`이다(2026-08-17,
주 실서빙 장비가 ATOM이므로 — docs/portability.md §3). 프로필이 선언한 런타임이 없는
장비에서는 `--tool-runner`로 덮는다(ADR 0005). 이미지는
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

**Seraph 검증 완료 (2026-08-14, moana-u3).** `dev_singularity` 프로필이 Docker와 같은
결과를 냈다.

- **SIF 변환에 재빌드가 필요 없었다.** Singularity 3.7.3이 buildx가 만든 tar(manifest
  list + attestation manifest 포함)를 `docker-archive://`로 그대로 받아 8개 레이어를
  풀었다(241MB SIF). `DOCKER_BUILDKIT=0` 재빌드도, 레지스트리도 필요 없다.
- **런타임 추상화가 실측으로 확인됐다.** parser.c에서 발화한 룰·줄 번호가 같고
  **semgrep fingerprint까지 동일하다** — fingerprint는 룰·파일·코드 문맥에서 나오므로,
  같다는 것은 같은 룰셋이 같은 코드에 적용됐다는 뜻이다. 격리 옵션도 문법만 바뀌어
  그대로 걸렸다(`--containall --net --network=none`, `--bind ...:/src:ro`).
  결과도 같다: 후보 2건 / Finding 2건 / 완주.
- **`cpu_limit`·`memory_mb`는 SLURM이 대신했다**(`srun --cpus-per-task=2 --mem=4G`).
  `container_tools.UNENFORCED`에 적힌 대로 이 런타임이 강제하지 못하는 항목이다.
- `doctor`의 state DB 경로 검사가 양방향으로 동작하는 것도 확인됐다 — `--artifacts-root`를
  `/data/` 밑에 두면 OK, 저장소 안이면 WARN이다.

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

**잴 도구는 들어왔다 (2026-08-14).** `python -m src.cli bench --profile <name>`이
model-serving.md §3 체크리스트를 실제 엔드포인트에 대고 재고 JSON 하나로 낸다. 남은 것은
ATOM에서 돌리는 일이다.

- **접속하는 유일한 커맨드다.** `doctor`는 여전히 소켓을 열지 않는다(`cli.py` 모듈
  docstring, `test_doctor_never_opens_a_socket`).
- 측정은 `OpenAICompatModelClient`와 실제 프롬프트 렌더 경로를 **그대로** 쓴다. 운영과
  다른 코드로 잰 수치는 운영 성능이 아니다.
- **못 잰 값은 0으로 남기고 사유를 함께 낸다.** `--apply`는 0인 항목을 registry에 쓰지
  않는다 — `Benchmark.is_measured`가 0 여부로 판정하므로 지어낸 값은 미측정 모델을
  production 프로필에 올리는 길을 연다.
- `--apply`는 `models.yaml`의 `benchmark:` 라인만 갈아 끼운다. 그 파일의 주석이 곧
  문서이므로 통째로 다시 쓰지 않고, 대상 블록을 확실히 찾지 못하면 **아무것도 쓰지 않는다.**

**아직 못 재는 것:** §3의 7번째(NVFP4 Critic 반박 정확도). 고정 반박 표본이 있어야 답이
나오므로 평가셋 준비 전까지 범위 밖이고, bench 출력에는 `unmeasured(데이터셋 대기)`로 뜬다.

### 🟢 실서빙 프로필이 실제 도구를 쓴다 (2026-08-14)

`local_balanced`·`b1_single`의 `tool_runner`가 `fake` → `docker`가 됐다. 그전에는 두
프로필로 완주시켜도 `RunRecord.tool_images`가 비어 §8 규칙상 비교표에서 제외됐다 —
**중간 발표 DoD "B1 조건 1회 완주"를 프로필 정의만으로는 만족시킬 수 없었다.**

`retriever`는 `fake`로 뒀다. hybrid로 올리면 embedding·reranker까지 실서빙이 되어 ATOM에
모델 5개를 동시에 띄워야 하고, R1 검증의 실패 지점이 그만큼 늘어난다.

**2026-08-17: 그 `docker`가 `podman`이 됐다.** 프로필은 주 실서빙 장비의 런타임을 선언하고
다른 장비가 덮는다는 기준을 세웠고([portability.md](docs/portability.md) §3), 이 두 프로필의
주 실서빙 장비는 ATOM(podman만 있다)이다. 그전에는 ATOM의 **모든** 실서빙 run이
`--tool-runner podman`을 달아야 했고, 그 오버라이드가 evaluation.md §2.3 규칙 2의 예외라
**주 경로가 상시 예외 상태**였다. 두 프로필의 모델이 전부 `nvfp4`/`fp4`라 Blackwell 미만
장비에서는 애초에 기동하지 않으므로([portability.md](docs/portability.md) §5 #1) 반대 방향의
대가는 없다.

`--tool-runner {fake,docker,singularity,podman}`는 그대로 남는다. 프로필이 선언한 런타임이
없는 장비에서 쓰고, 지금 실제 사용처는 `dev_docker`를 ATOM에서 돌릴 때다. evaluation.md
§2.3 규칙 2의 예외이므로 **실제로 쓰인 값이 `RunRecord.tool_runner`에 남는다**(ADR 0005).

---

## 6. 블로킹 이슈

| # | 항목 | 기한 | 상태 |
|---|---|---|:-:|
| **R1** | ATOM(GB10, sm_121) × vLLM 커널 호환성 미검증 | **2026-08-17** | 계측 도구 완료 · 실측 대기 |
| **R3** | 누출 검증 7개 중 5개 미구현 (Patch Leakage, 중복 제거, Time Split, Prompt Logging, Reproducibility) | 2026-09-07 | 부분 |
| **R6** | prefix cache가 장부에 `cached_tokens: 0`으로 기록됨 | 2026-09-07 | 대체 경로 확인(`prompt_tokens_cached_total`) · 장부 반영은 별건 · ATOM 대기 |
| R4 | 단일 노드 대역폭 병목 미파악 | 2026-09-05 | 미착수 |

### R1 — 크리티컬 패스

`model-serving.md` §3 체크리스트 7개 전부 미체크. **재는 도구는 `cli bench`이고
(2026-08-14), 각 항목이 bench 출력의 어느 필드로 답해지는지는 그 문서에 적혀 있다.**

```
[ ] vLLM × CUDA 13.0 × sm_121 커널 호환성
[ ] 역할별 모델 로드 및 실제 통합메모리 점유
[ ] context 8K/16K/32K에서 KV cache 점유와 처리량
[ ] 단일 스트림 tok/s vs 배치(8/16/32) 합산 tok/s
[ ] automatic prefix caching 동작 및 적중률       ← R6 판정 지점
[ ] structured output(JSON schema 강제) 동작
[ ] NVFP4 양자화 시 Critic 반박 정확도 저하
```

마지막 항목만 bench 범위 밖이다 — 고정 반박 표본이 있어야 답이 나온다(평가셋 준비 중).

**이것이 통과하기 전까지 `models.yaml`의 `benchmark` 값은 전부 0이고, 프로덕션 프로필로 올릴 수 없다.**
`bench --apply`가 그 값을 채우지만 **0인 항목은 쓰지 않는다.** 채워지지 않았다는 것은
재지 못했다는 뜻이고, 그 상태가 그대로 보여야 한다.

### R6 — 배경

2026-08-11 Seraph 실측: vLLM `/metrics` 기준 prefix 재사용 **99.0%**(1067 중 1056),
`wall_ms` 5495 → 2540 (2.2배 단축)이 확인됐으나 응답의 `prompt_tokens_details`가 `null`이라
하네스 장부에는 `cached_tokens: 0`으로 잡힌다.

**어댑터 결함이 아니다.** 서버가 값을 주지 않는다. Seraph는 드라이버 535(CUDA 12.2 상한)라
vLLM 0.19.1까지만 쓸 수 있어 최신 동작 확인이 불가능하다. **ATOM(CUDA 13.0)에서 먼저 확인한다.**

**판정은 자동화됐다 (2026-08-14).** `bench`의 D 항목이 장부(`Usage.cached_tokens`)와
`/metrics`를 함께 보고 판정한다: `both_hit`(정상) · `metrics_only`(위 상황의 재현 —
어댑터가 아니라 서버가 원인이라는 사실이 출력에 드러난다) · `ledger_only`(이론상 없는
조합) · `caching_disabled`(`enable_prefix_caching=False` — 서버를 다시 띄워야 한다) ·
`neither`(켜져 있는데 적중 0 = 진짜 조사 대상) · `no_traffic` / `metrics_unavailable` /
`metrics_reset`(캐시에 대해 아무 주장도 하지 않음). **카운터는 절대값이 아니라 구간
delta로 읽는다** — 앞 측정이 데운 캐시와 서버 기동 시점이 절대값에 그대로 섞이기 때문이다.

**대체 경로가 확인됐다 (2026-08-14, Seraph moana-u4 실측).** `/metrics`의
`vllm:prompt_tokens_cached_total` 구간 delta가 **응답의 `prompt_tokens_details.cached_tokens`가
주었어야 할 값과 같은 단위(토큰)**다. 실측 원문에서 응답은 6회 전부 `null`인데 이 카운터는
1120(질의 1458 중 76.8%)이었다. 즉 R6는 "값을 얻을 수 없다"가 아니라 **"응답 경로로는 못
얻고 metrics 경로로는 얻는다"**로 성격이 바뀌었다.

- **장부 반영은 별건이다.** 이 값은 **구간 합계라 개별 `model_call`에 배분할 수 없다.**
  `BudgetLedger`·`_usage_from`은 손대지 않았고, bench 출력의 note에도 그렇게 적힌다.
- **어느 카운터를 장부로 쓸지는 정하지 않았다.** `prefix_cache_hits_total`은 **local만**,
  `prompt_tokens_cached_total`은 **local + external**을 센다(HELP 원문). 이번 실측에서
  둘 다 1120으로 같았지만 그건 external이 0이었기 때문이다 — 값이 같다고 같은 것을 세는
  것이 아니다. KV connector를 쓰기 시작하면 갈라지므로, bench는
  `external_prefix_cache_hits_delta`를 함께 실어 **갈라진 시점이 사후에 판별되게** 한다.

**실측이 잡아낸 결함 (같은 날 수정).** bench가 읽던 `vllm:gpu_cache_usage_perc`는
**존재하지 않는 이름**이었다(`vllm:kv_cache_usage_perc`가 맞다). E(메모리)가 이 버전에서
항상 빈손이었는데 **스텁이 bench가 기대하는 이름을 그대로 찍어 주어 테스트 26개가 전부
초록이었다.** 그래서 (1) 이름을 `src/bench_metrics.py`의 `METRIC_NAMES` 상수로 모으고 (2) 스텁을
실측 원문(`tests/fixtures/metrics/`)으로 교체했으며 (3) `METRIC_NAMES`가 fixture에 전부
존재하는지 파일별로 대조하는 테스트를 두었다. ATOM 원문이 들어와 이름이 달라지면 그
테스트가 깨지는 것이 의도다.

---

## 7. 검증 완료 (2026-08-11, Seraph)

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
| `tool_images` | 컨테이너 미사용 (`fake` tool_runner) | `dev_docker`/`dev_singularity`/`local_balanced`/`b1_single` 프로필 + `harness-tools:v1` 이미지 |
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

### tool_images digest는 런타임 사이에서도 아키텍처 사이에서도 비교할 수 없다

같은 이미지인데 런타임마다 값이 다르다(2026-08-14·17 실측).

```
Docker      sha256:ccb6a6d9969b255f733496bf508ec141cdc547145b8708a6a2ae1e9d44467f12  (이미지 config sha256)
Singularity sha256:88a0fd4ba8f040e2abd03c8ef03c1ae3645436f702689cdff29057ea03779d20  (SIF 파일 sha256)
podman      sha256:3a63ba2eb504b1b1fd9378fe5df9b682418b79f86eb2b6650c2ee4c2d635a256  (RepoDigest — ATOM에서 별도 빌드)
```

Docker는 이미지 config의 sha256을, Singularity는 SIF 파일 자체의 sha256을, podman은
RepoDigest를 기록한다(`container_tools._image_digest()`). 셋 다 그 런타임 안에서는 이미지를
유일하게 지시하지만, **서로 비교할 수 있는 값이 아니다.**

**비교를 막는 축이 둘이다.**

| 축 | 왜 갈리는가 | 기록에 남는가 |
|---|---|---|
| 런타임 (docker ↔ singularity ↔ podman) | 런타임마다 다른 종류의 sha256을 적는다 | `RunRecord.tool_runner` |
| 아키텍처 (x86_64 ↔ aarch64) | 같은 Dockerfile이라도 아키텍처가 다르면 **다른 이미지**다 | **없다** — `device` 라벨이 [portability.md](docs/portability.md) §6 표를 거쳐 간접적으로 가리킬 뿐이다 |

위 podman 값은 **두 축이 겹친** 경우다 — 기록 포맷도 다르고, ATOM(aarch64)에서 따로 빌드한
다른 아키텍처의 이미지이기도 하다.

→ **"tool_images가 같으면 같은 도구로 돌린 것"이라는 추론은 같은 런타임·같은 아키텍처
안에서만 성립한다.** 지금은 각 run을 한 환경에서 돌리므로 문제가 없지만, ATOM(podman)·
Seraph(singularity)·로컬(docker)을 섞어 쓰기 시작하면 조건 간 비교에서 걸린다. 반대로 값이
다르다고 다른 이미지인 것도 아니다 — 위 세 값 중 docker·singularity는 같은 이미지다. 해법(예:
양쪽에서 같은 값이 나오는 이미지 식별자를 쓰기)은 정하지 않았다.

### 두 장비의 결과를 같은 비교 셀에 넣지 않는다

ATOM(GB10, sm_121)과 Seraph(A2000, sm_86)는 **둘 다 지원 대상이다** — 한쪽으로 이관하는
것이 아니라 파이프라인 전체가 양쪽에서 각각 완주해야 한다(Seraph는 이식성 요구사항이다).

그래서 같은 프로필·같은 조건으로 돈 두 run이 다른 하드웨어의 결과일 수 있다.
`model_checksums`는 설계상 장비 불변이고 endpoint는 애초에 기록되지 않으므로 **기록만 놓고
보면 두 run이 완전히 같아 보인다.** `RunRecord.device`와 `Benchmark.measured_on`이 그 둘을
가른다([decisions/0006](docs/decisions/0006-record-the-device-a-run-happened-on.md)).

→ 장비별 제약, moana 노드가 고정되지 않는다는 것, 그리고 **아직 한쪽에서 완주하지 못하는
항목의 대장**은 [docs/portability.md](docs/portability.md)에 모았다. 첫 항목은
`local_balanced`·`b1_single`이 NVFP4 모델을 써서 Seraph(sm_86)에서 완주 불가라는 것이다.

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
