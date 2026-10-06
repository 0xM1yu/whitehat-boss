# 하네스 구현 현황

> ⚠ **보관 — 2026-09-24 [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 5.** 실험 기록의
> 원천은 Notion 팀 워크스페이스다. 이 파일은 옮기지 않고 보관한다 — 절마다 든 날짜의 기록으로 읽고,
> 아래 「갱신 규칙」도 그날까지의 규칙이다. **현재 상태의 정본은 [PROJECT-CONTEXT.md](PROJECT-CONTEXT.md)·
> [docs/architecture.md](docs/architecture.md)·D·R 본문이다**([CLAUDE.md](CLAUDE.md) §6).

> 대상: `src/` 전체 · 기준일: **절마다 다르다.** 각 절이 자기 날짜를 들고, 이 문서 전체가
> 한 날짜 기준이라는 표기는 쓰지 않는다.
>
> ⚠ **왜 기준일을 없앴나 — 두 번 틀렸다.** 처음에는 `2026-08-17`에 멈춰 있었는데 본문은 그 뒤로
> 계속 갱신됐고, 2026-09-20에 그것을 `2026-09-19`로 고칠 때 값을 `git log -1 -- STATUS.md`로
> 뽑았다. 그것은 **파일**의 최종 커밋이지 각 절이 쓰인 시각이 아니다 —
> [CLAUDE.md](CLAUDE.md) §2.4가 정확히 그 방법을 금지하고, 실제로 아래 §5의 여러 행은 08월대다.
> **한 날짜로 덮을 수 있는 문서가 아니라는 것이 결론이다.**
>
> 갱신 규칙: 단계 상태나 블로커가 바뀌면 이 문서를 먼저 고치고 관련 문서에 반영한다.

---

## 1. 한눈에

**오케스트레이션 로직은 완성, 실제 동작(어댑터)은 대부분 비어 있다.**

`dev_fake` 프로필로 0~9단계가 완주하고, 동일 조건 재실행 시 trace가 재현된다.
🔴 **⏵ 2026-10-03 — 실제 CyberGym Level 1 번들에서는 완주하지 않는다**(`input_tokens 200,000`을
204,963이 넘겨 `stopped_after: 4`). 이슈 [#424](https://github.com/0xM1yu/whitehat-boss/issues/424).
⏵ **2026-10-03 밤 — 닫혔다.** PR #427이 `dev_fake`의 토큰 상한을 `0`(= 상한 없음)으로 내려 이슈가 닫혔다(13:55:38Z) —
깨끗한 트리로 완주한 재현은 그 PR 본문이 적는다(`repo_dirty: false` · `stopped_after: null`). 위 문장은 `1fa2182`의 사진이다.
비어 있는 부분은 전부 `src/adapters/` 쪽이므로 **오케스트레이터를 고치지 않고 교체 가능**하다.

```
로직 (src/orchestrator/, src/schemas/, src/interfaces/)   완성
실제 동작 (src/adapters/)                                  Fake 위주
```

---

## 2. 파이프라인 단계별 상태

| # | 단계 | 로직 | 실제 동작 | 상태 |
|:-:|---|:-:|---|:-:|
| 0 | Preprocess | ✅ | 확장자 분류 + SHA-256 | 🟡 부분 |
| 1 | Index | ✅ | 실제 tree-sitter 인덱서 (`dev_docker`·`dev_singularity`·실서빙) / 디렉터리 존재만 확인 (`dev_fake`) | 🔵 **진행 중** |
| 2 | Signals | ✅ | 실제 semgrep·clang-tidy·clang (`dev_docker`) / 정규식 2개 (`dev_fake`) | 🟡 부분 |
| 3 | Retrieval | ✅ | 파일 텍스트 자르기 + 실제 호출 그래프의 `callers`/`callees` (인덱서가 돈 프로필) / embedding·reranker 재선별 (`dev_hybrid*`) | 🟡 부분 |
| 4 | Scout — 풀이면 역할 셋 | ✅ | 가중합 휴리스틱 (Fake) | 🟡 Fake 모델 |
| 5 | Auditor | ✅ | 템플릿 조합 (Fake) | 🟡 Fake 모델 |
| 6 | **Gate** | ✅ | **결정론 판정** | 🟢 **완성** |
| 7 | Critic | ✅ | graph 유무로 uphold/rebut (Fake) | 🟡 Fake 모델 |
| 8 | Validate | ✅ | 컨테이너에서 빌드·실행 (docker·singularity·podman 검증 완료. PoC 부재로 실행까지 못 감) | 🟡 부분 |
| 9 | **Report** | ✅ | 템플릿 렌더링 | 🟢 **완성** |

**6·9단계만 최종본이다.** 둘 다 모델·도구를 쓰지 않는 단계라서 지금 코드가 그대로 최종 구현이다.

**4단계 「Scout」는 역할 하나가 아닐 수 있다 (2026-09-09).** 로스터가 Scout를 **풀 세
역할**(`scout_memory`·`scout_boundary`·`scout_injection`)로 쪼갰고, **기존 `scout` 한 역할
프로필도 그대로 유효하다**([ADR 0010](docs/decisions/0010-harness-structure-and-model-roster.md) §3).
⚠ **이 표가 말하는 것은 로직·어댑터의 상태이지 모델 이름이 아니다** — 어느 모델이 어느 자리에
앉는지는 [PROJECT-CONTEXT.md](PROJECT-CONTEXT.md) §1이 정본이다.

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

**현재 (2026-08-15 갱신):** 인덱서는 **들어왔다.** 컨테이너를 쓰는 프로필에서는 `index`
노드가 `indexer.py --callgraph`를 돌려(`nodes/index.py`) 실제 심볼 표와 호출 그래프를 낸다.
`dev_fake`에서만 `FakeToolRunner`가 디렉터리 존재만 확인하고 `Evidence(kind=GRAPH, ok=True)`를
반환한다.

**산출물이 캡슐까지 왔다 (2026-08-20).** `scratch/index/index.json`을
`adapters/index_graph.py`가 읽어 **두 Retriever의** `callers`/`callees`를 채운다.
`FakeRetriever`가 주 경로다 — index.json을 내는 조건은 `retriever`가 아니라
`tool_runner`가 docker·singularity인 것이고, 그렇게 내는 프로필이 **대부분**
`retriever: fake`이기 때문이다. `dev_hybrid_docker`·`local_balanced_hybrid`가 hybrid 쪽
경로를 태운다(2026-09-04 확인).

**`types`는 채우지 않는다.** index-v1에 타입 데이터가 없어(`build_index()`는 symbols·calls
뿐) 섹션 자체를 없앴다 — 고정 64토큰으로 두면 없는 근거를 지어내는 것이고, 이름만 남기면
다음 사람이 "채워야 하는데 비었다"로 읽는다. (⏵ 2026-09-26 — index-v2가 `types` 표를 낸다.
캡슐 섹션으로는 여전히 싣지 않는다 — NE resolver가 요청받았을 때 주는 조회 표다.)

**바로잡음: 고정 64토큰이 오염시킨 것은 토큰 회계이지 E2 판정이 아니었다.**
`gate.py`의 `graph_ok`는 `any(ev.kind is GRAPH for ev in usable)`로 **Evidence만** 보고
캡슐 섹션을 읽지 않는다. E2의 실제 약점은 따로 있고 그대로 남아 있다 — 심볼 0개도
`Evidence(ok=True)`라 **빈 그래프 위에서 E2가 열린다**(open-questions D13 ②).

**실제 산출물 형식** (`indexer.INDEX_VERSION == "index-v1"`, 읽는 쪽이 모르는 버전은 거절).
⏵ 2026-09-26 — 지금은 `index-v2`다: `calls`가 callee → [{file, line, caller, args}]로 묶였고
조회 표 여덟이 더해졌다(`tools/README.md` 인덱서 절). ⏵ 2026-10-03 — `index-v3`(#304)가 v2 표를
그대로 두고 `writes`·`fn_bindings`·`field_calls`를 더한다. ⏵ 2026-10-04 — `index-v4`가 v3 표를 그대로
두고 `includes`·`enum_constants`를 더한다(`types`의 앞선 선언 typedef 행에 `forward`). 아래는 v1 시절
모양이다:
```json
{
  "version": "index-v1",
  "files": ["parser.c"], "skipped": [], "parse_errors": [],
  "symbols": [{"name": "parse_header", "file": "parser.c", "line_start": 9, "line_end": 22}],
  "calls":   [{"caller": "parse_header", "callee": "memcpy", "file": "parser.c", "line": 20}],
  "unresolved_calls": 0, "unresolved_symbols": 0
}
```
**스키마는 바뀌지 않았다 — 다만 `payload_ref`를 거치지도 않는다.** 인덱서 산출물의 호스트
경로는 `container_tools.index_output_path()`가 조립하고 `index_graph`가 그것을 직접 읽는다.
`Evidence.payload_ref`가 가리키는 것은 **도구 로그(`.log`)**이지 `index.json`이 아니다
(`container_tools.py`·`fake_tools.py`). 구현 전에는 `payload_ref`로 나를 것으로 적어 뒀는데,
결론(스키마 무변경)만 맞고 경로가 달라졌다.

**여기까지가 된 것이고, 아래는 그 뒤에 붙었다 (2026-08-23, W2):**
- 캡슐이 **심볼 단위**가 됐다. `index.json`의 `symbols`가 `GraphIndex.symbols_for()`를 거쳐
  후보 목록이 되고, 후보 하나가 심볼 하나다. 인덱스가 없는 프로필은 파일 단위로
  되돌아가며 그 사실이 `target_plan.fallback_artifacts`에 남는다.
- **후보 수와 제거율의 분모가 바뀌었다.** 전/후 지표를 직접 비교하지 않는다(§10).
- Gate E1의 `location_ok`(`Location.overlaps`)는 **그래도 작동하지 않는다.** 원인이 후보
  위치가 아니라 `Evidence.location`이 어디서도 채워지지 않는 것이었다 — 이 문단이 오래
  틀리게 짚어 온 자리다(§10 정정, open-questions D23).
  ⚠ **2026-09-24 — #248 전의 서술이다.** #248부터 semgrep이 첫 히트 위치를 싣는다(§10의 같은 날 노트).

---

## 5. 미착수 — 빈칸

### 🟡 8단계 실행 환경 — `ContainerToolRunner` (2026-08-14)

**들어왔다.** `src/adapters/container_tools.py`가 Docker·Singularity·podman 안에서 실제 도구를
돌린다. 런타임은 생성자 인자이고 프로필이 고른다 — 개발 프로필은 `dev_docker` /
`dev_singularity`, 조건 비교용 실서빙 프로필(2026-08-28 기준 `local_balanced`·`b1_single`)은
전부 `docker`다(주 실서빙 장비가 ATOM이므로 — docs/portability.md §3.
2026-08-17~08-28 사이 그 둘은 `podman`이었다. 되돌린 경위는 ADR 0005 마지막 절).
**목록의 정본은 `config/profiles.yaml`이다 — 여기 개수를 현재형으로 못박지 않는다.**
프로필이 선언한 런타임이 없는 장비에서는 `--tool-runner`로 덮는다(ADR 0005). 기본 이미지는
`harness-tools:v3`이며(2026-09-28 v2에 비대화 gdb를 더했다 — v1·v2는 지난 run의 digest 때문에
지우지 않는다) semgrep 룰셋까지 구워 넣는다 — 샌드박스가 `--network=none`이라
레지스트리를 못 보기 때문이고, 그 덕에 이미지 digest가 도구 버전과 룰셋 버전을 함께 고정한다
(준비 절차는 `tools/README.md`).

**해소된 것:** `RunRecord.tool_images`가 채워진다 → 그 run이 비교표에 들어갈 수 있다.

**해소되지 않은 것 — 실도구 경로에서 `proven`(E4)은 여전히 도달 불가다.**

> **2026-08-21 정정**: 그전에는 **판정 자체가 없었다.** 8단계가 Evidence Gate를 부르지
> 않아 E4는 그것을 만들 수 있는 유일한 단계에서 평가되지 못했고 `gate_eval` trace에 한
> 번도 나타나지 않았다(W3). 지금은 세 단계 전부에서 판정하며 Fake 경로에서 E4가 실제로
> 나온다(`test_e4_is_reachable_and_only_at_the_stage_that_runs_the_tools`). 아래 문단은
> **실도구 경로**에 대한 서술이고 그쪽은 그대로다.

E4 조건은 `SANITIZER`/`CRASH`/`POC_RUN` Evidence(⏵ 2026-10-02 — 그리고 종료 코드가 0도 미측정도
아닐 것, 이슈 #232)인데, 이 구현은 **컴파일 성공을 실행으로 세지 않는다.** `asan`·`gdb`·`poc`는
링크와 실행까지 성공해야 `ok=True`이고, 빌드 단계에서 끊기면 `ok=False`다. 그렇게 하지 않으면
**한 번도 돌지 않은 프로그램이 재현 완료로 기록된다** — 게이트가 가장 비싼 곳에서 거짓말을 하게
된다.

gdb는 2026-09-28 독립 `ContainerToolRunner` 실행에서 정상 종료·SIGSEGV 백트레이스를 모두
냈다. 다만 `validate.tool_for()`는 아직 gdb를 선택하지 않으므로 **파이프라인의 실도구 E4
도달을 보인 것은 아니다.** 그 실행 규칙은 도구 배선 후 후속 범위다. ⏵ 2026-09-30 — gdb가
대상을 띄웠는지는 종료 코드가 아니라 gdb가 stdout에 남긴 대상 수명주기 줄로 가른다(안 뜬
대상은 `ok=False` — `tests/test_gdb_lifecycle_judgement.py`).

지금 픽스처(`tests/fixtures/target/parser.c`)에는 `main`이 없어 링크되지 않고, `./poc`를
만드는 주체도 없다. 즉 **E4를 막고 있는 것은 실행 환경이 아니라 PoC 생성**이다(아래 PoV 항목).
중간 발표 DoD "`proven` 1건 이상 재현"은 PoV가 들어와야 성립한다.

**남은 한계 2가지:**
- ~~`index`가 `clang -fsyntax-only`다~~ — **걷어냈다(2026-08-14).** tree-sitter 인덱서가
  실제 심볼 표와 호출 그래프를 낸다. 남은 것은 **빈 그래프도 `Evidence(ok=True)`라 심볼
  0개 위에서 E2가 열린다**는 점이다(D13 ②). 그래프 자체도 근사이고 놓친 것은
  `unresolved_calls`·`unresolved_symbols`가 세며, 그 값은 이제 `graph_index` trace
  이벤트로 남는다 — 그전에는 아무도 집계할 수 없었다. **run당 최대 1건이고 0건일 수
  있다**: 로더가 지연 로드라 후보가 하나도 4단계를 통과하지 못하면 이벤트가 아예 없다.
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
  `strnlen` 미선언 오류였다. 현재 clang 계열 도구는 C에 `-std=gnu17`, C++에
  `-std=gnu++17`을 쓴다(`_STD_ARGS`).

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

**ATOM 검증 완료 (2026-08-17, aarch64 / podman).** `dev_docker --tool-runner podman`이
Docker와 같은 결과를 냈다. **도구 실행 경로는 검증됐고, 모델 서빙은 환경 제약으로 대기다.**

```
run_id      run_20260817_0001      후보 2건 / Finding 2건 / 완주
tool_runner podman                 device      atom
repo_commit 4ada193…(dirty: False) condition   B3prime@pipeline
tool_images {'harness-tools:v1': 'sha256:3a63ba2eb504b1b1fd9378fe5df9b682418b79f86eb2b6650c2ee4c2d635a256'}
```

- **아키텍처가 달라도 판정이 같다 — 처음 확인됐다.** semgrep fingerprint가 로컬
  (x86_64/docker) `run_20260813_0002`·`0003`과 **문자 단위로 같다**(`ev_0002`
  `2f5f28f1…0bce6_0`, `ev_0005` `766c3342…3c1e99_0`). 2026-08-14의 docker ↔ singularity
  대조는 같은 x86_64끼리였고(SIF가 로컬 x86 tar를 그대로 변환한 것이다, 위 항목), ATOM
  이미지는 그 장비에서 arm64로 따로 빌드했다. 그래서 이번이 **런타임과 아키텍처가 동시에
  다른 첫 대조**다. 이것으로 [ADR 0005](docs/decisions/0005-tool-runner-override.md)가
  스스로 정한 상태 갱신 조건이 충족되어 그 ADR이 `승인`이 됐다.
- **R1의 커널 호환성 항목은 통과했다.** vLLM 0.27.1이 aarch64에 설치되고
  `torch.cuda.get_device_capability()`가 `(12, 1)`·`NVIDIA GB10`을 인식했다
  (torch 2.13.0+cu130 / CUDA 13.0). **대체 서빙 스택 검토는 불필요하다.**
- **로컬 TCP 차단은 같은 날 해소됐고, 모델이 실제로 떴다.** 차단이 걸려 있던 동안에는
  같은 프로세스에서 `bind('127.0.0.1', p)` → `listen` → `connect`가
  `ConnectionRefusedError: [Errno 111]`로 떨어졌고(`ss -ltn`에는 LISTEN으로 보이고 `lo`도
  UP이었다), `192.168.0.10`은 `[Errno 113] No route to host`였다. vLLM은 `world_size=1`
  이어도 TCPStore에 자기 자신으로 붙으므로 그동안 기동 자체가 불가능했다. **관리자 예외가
  적용된 뒤 `qwen3-4b`가 `127.0.0.1:8001`에서 서빙됐다** — 근거는 registry의 실측이다
  (`config/models.yaml`: `measured_at: 2026-08-17T15:16:27Z`, `measured_on: atom`,
  단일 39.65 tok/s · batch32 127.69 tok/s). **기동에는 `VLLM_USE_DEEP_GEMM=0`이 필요하다**
  — 없으면 가중치를 다 읽은 뒤 죽는다(§6 R1, `models.yaml` 주석).
- `nvidia-smi --query-gpu=memory.total,memory.used`가 이 장비에서 `[N/A], [N/A]`다.
  통합메모리라 별도 VRAM이 없어 보고할 값이 없다 — nvidia-smi는 PATH에 있고 정상 종료한다.
  `mem_gb_ctx8k`의 유일한 소스가 그 값이므로 §3 체크리스트 2번은 다른 소스가 정해질
  때까지 답이 나오지 않는다.

### 🟡 PoV(PoC) 생성 — 러너 배선·과제 로더·툴 루프 착지, 실서빙 전

**현재:** `Role.POV` + `prompts/pov/v3/`(2026-10-05 배선, 그전은 v2) + `nodes/pov.py`(다중 홉 툴 루프 — 매 홉 `PovAction` → `gen`이면
`PovSpec` → 순수·결정론 `assemble_poc` → 바이트, council 2026-09-30 fork B-iii)에 더해, 러너가
`Stage.VALIDATE`에서 `validate.run`과 **나란히** pov를 `_run_pov_hops` 루프로 부른다(추가형 — 캡슐
미스·스키마·버릴 수 있는 게이트웨이 실패는 finding을 안 죽이고 `pov_skipped`로 남기고, 서버 쪽
사건·예산 소진은 run으로 올린다). CyberGym Level 1
과제 로더(`task_loader` — repo-vul.tar.gz 안전 해제 + description.txt)와 설명 주입(캡슐 `vuln_description`
섹션 → Scout·Auditor·pov)도 붙었다. PoV 모델 **`feyospace-v1-small`**(조민규 선정)는 §1 8단계 pov 좌석으로
`harness_pool`·`_hybrid`에 배선됐다(swap·장비 파일 포함) — checksum·benchmark 미실측(호스트 확보 뒤).

⚠ **호스트 후속 전에는 실서빙 pov 루프를 켜지 않는다** — PoC 바이트를 읽는 도구 명령이 아직 없어
(`run_tool`이 PoC를 입력 파일로 싣지만 — `ToolArgs.input_bytes`, #511 — 어느 명령도 그 파일을 인자로
받지 않는다) 도구 관측이 PoC와 무관하다. v3 관측의 입력 크기·digest 줄만 PoC의 함수다. 그 전에는
`harness_pool`의 8단계를 돌리지 않는다.

**남은 것(서빙+도구 되는 호스트 필요):** `poc` 실행파일 materialize + E4 게이트 · feyospace 실서빙
실측(checksum·benchmark·홉수). 외부 CyberGym oracle 제출은 `run --oracle-submit`(기본 꺼짐)으로 들어왔다 —
대역으로만 검증했고 하네스 run으로 실서버 왕복을 잰 것은 0건이다. ⏵ **2026-10-03 — 「대역으로만」은
vul 다리에 한해 깨졌다**(`win11`의 `run_20261003_0003`(trace sha256 `f308c5fbfc4f8e54…`)이 실서버에 8건
제출, 전부 200 — **고유 PoC 1개**, `cached` 미적중 1·적중 7). **왕복(vul→fix→조회)은
여전히 0건**이다 — 후보 8건이라 최종 PoC를 지정하지 않았다
([보고서](docs/experiments/harness/2026-10-03-cybergym-plumbing-proof-win11.md)). ⏵ **고르는 규칙은 동결·배선됐다**(2026-10-03, 조민규 · council 뒤) — 그때의 규칙은
**내부 근거 등급 최댓값, 동점이면 거부**였고(⏵ 2026-10-04에 뒤의 거부가 사슬로 바뀌었다 — 아래)
지정 로직이 채점기 값을 안 읽는다는 불변식에 테스트가 붙었다. 지정이 보는 등급은 8단계 `gate_eval`과 같다(`runner._grades_by_finding` — 8단계 도구 근거까지
센다). 🔴 **`dev_fake`에서는 그래도 점수가 안 난다** — Fake `_run_repro`가 대상 소스의 존재만 봐 재현이
전 후보에서 성공하므로 후보가 전부 E4 동점이었다(`grade_tie`). ⏵ **2026-10-04 — 그 거부를 없앴다.**
실측 run의 후보 8건이 **같은 바이트 1개**였고(제출 8 = 고유 PoC 1 = DB 행 1) 어느 쪽을 지정해도
제출될 바이트가 같은데 거부한 것이다. 같은 바이트는 접고(`sole_poc`) 남은 동점은 사슬
(①등급 ②`confidence` ③작은 바이트 ④PoC 해시 — ④가 전순서다)로 넘긴다. `grade_tie`는 닿지 않는
분기가 되어 사유 상수째 지웠다(`runner._designate_final`). 🔴 **실도구 run에서 후보 등급이 갈릴지는 미실측이다** — PoC가 `./poc`로
materialize되기 전(위 호스트 후속)에는 8단계 도구 실행이 PoC와 무관하다. `PovSpec` 표현력 절벽(계산 크로스필드)이
실측되면 gen-script(코드열람·Python 스크립팅) 승격을 별도로 검토한다.

⏵ **2026-10-03 밤 — 둘째 과제 계열**(`oss-fuzz:368076871`, `cybergym/oss-fuzz` 이미지)**에서도 같은 모양이다** —
완주 · 제출 8건 전부 200 · 실패 0 · **고유 PoC 1개**(`cached` 미적중 1 · 적중 7) · `repo_dirty false`
([보고서](docs/experiments/harness/2026-10-03-cybergym-plumbing-proof-win11.md) §11). **왕복은 두 계열 모두 0건**이고
사유도 같다(후보 8건 → 그 시점의 지정 규칙이 등급을 읽지 않고 거부). 후보가 8인 것은 `critic_top_n`(모든 프로필 공통
상한)에 **닿은** 값이다 — 이 run은 Fake Critic이 올라간 8건을 전부 `upheld`로 돌려줬다. ⚠ 「Finding이 둘 이상 승격되면
후보가 둘 이상」은 **n=2 관측**이고 승격 Finding마다 PoC가 하나씩 나오는지는 코드로 확인하지 않았다(배관 증명 보고서 §11.5·§7). 등급
변별력은 **미실측**이다 — 8단계 E4 8건 동점은 `dev_fake`의 기록이고 원인 후보는 보고서 §11.5가 든다.

**선택지:**
| 방향 | 내용 |
|---|---|
| A | 데이터셋에 딸린 정답 PoC 재생 (CyberGym/OSS-Fuzz) |
| B | LLM이 PoC 생성 — `Role.POV` + `prompts/pov/v3/` + `nodes/pov.py` **← 루프 착지(호스트 전)** |
| C | 퍼징으로 크래시 발견 (비용 큼, 후순위) |

⚠ **중간 발표 DoD "`proven` 1건 이상 재현"은 아직이다** — 루프는 Finding→다중 홉(`PovAction`)→(submit 시)
최종 PoC 바이트까지이고(그 PoC를 채점기에 내보내 판정을 기록하는 길은 `--oracle-submit`으로 들어왔다 —
⏵ 2026-10-03 vul 다리 제출 8건(고유 PoC 1개) 실측 · 왕복은 0건), 실제 크래시 재현은 후속(서빙+도구 호스트 배선)이다.

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
⚠ **그 항목이 묻던 「고정밀 대비 하락폭」은 2026-09-09에 성립을 잃었다** — Critic 자리가
바뀌었고 새 자리에는 비교할 고정밀 체크포인트가 없다
([model-serving.md](docs/model-serving.md) §4 머리 · [D4](docs/open-questions/D/D4.md)).

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
**주 경로가 상시 예외 상태**였다. `local_balanced`는 모델 5개 중 3개가, `b1_single`은 세
역할이 전부 Blackwell 이상을 요구하므로 두 프로필 모두 그 미만 장비에서는 애초에 기동하지
않는다([portability.md](docs/portability.md) §5 #1) — 그래서 반대 방향의 대가가 없다.
⚠ **그 「5개 중 3개」는 2026-08-17 시점의 셈이다** — portability.md §5 #1은 이제 개수를
현재형으로 적지 않는다(프로필이 부르는 모델이 로스터를 따라 움직인다).

**2026-08-28: 그 `podman`이 다시 `docker`가 됐다.** 기준("프로필은 주 실서빙 장비의 런타임을
선언한다")은 그대로이고 바뀐 것은 ATOM의 런타임이 무엇이냐다 — 위 문단의 "ATOM(podman만
있다)"이 반증됐다(docker 29.2.1 · podman 4.9.3 공존, 2026-08-28 `which` 재확인). 되돌린
직접 근거는 **선언과 기록이 어긋나 있었다**는 것이다: `b1_single`로 돈 run 둘
(`run_20260827_0001`·`0004`)이 `RunRecord.tool_runner: docker`였다 — 위 문단이 피하려던
"주 경로가 상시 예외 상태"가 실제로 벌어지고 있었다. 유지 근거였던 "podman으로 이미 돈
run과 digest가 갈린다"도 사실이 아니었다 — run 14건 중 podman은 `run_20260817_0001`
하나이고 그것은 dev 프로필이다. 상세는 ADR 0005 마지막 절.

`--tool-runner {fake,docker,singularity,podman}`는 그대로 남는다. 프로필이 선언한 런타임이
없는 장비에서 쓰고, 지금 남는 사용처는 `dev_singularity`를 ATOM에서 돌릴 때다(ATOM에
singularity가 없다). evaluation.md §2.3 규칙 2의 예외이므로 **실제로 쓰인 값이
`RunRecord.tool_runner`에 남는다**(ADR 0005).

---

## 6. 블로킹 이슈

| # | 항목 | 본문 |
| --- | --- | --- |
| **R1** | ATOM(sm_121) × vLLM 커널 호환성 · 로컬 TCP 차단 — 커널·기동은 2026-08-17 해소, §3 체크리스트 잔여 | [R1.md](docs/open-questions/R/R1.md) |
| **R3** | 누출 검증 7개 중 5개 미구현 | [R3.md](docs/open-questions/R/R3.md) |
| **R6** | prefix cache가 장부에 `cached_tokens: 0`으로 기록됨 | [R6.md](docs/open-questions/R/R6.md) |
| R4 | 단일 노드 대역폭 병목 미파악 | [R4.md](docs/open-questions/R/R4.md) |

> **상태는 여기서 관리하지 않는다.** 정본은 [docs/open-questions.md](docs/open-questions.md) §1 표와
> 각 `Rn.md` 의 머리 표다. 예전에는 이 표가 자체 `상태` 열을 들고 있었고 **인덱스와 갈렸다** —
> 여기는 R6을 "대체 경로 확인"이라 하는데 인덱스 행은 08-23 에서 멈춰 그 경로를 몰랐다.
> 이 표는 §3·§5 를 읽는 사람이 R 을 놓치지 않도록 남긴 포인터일 뿐이다.

### R1 — 크리티컬 패스

`model-serving.md` §3 체크리스트 7개 중 **1번이 통과했다.** 재는 도구는 `cli bench`이고
(2026-08-14), 각 항목이 bench 출력의 어느 필드로 답해지는지는 그 문서에 적혀 있다.

**"커널 호환성 통과"와 "체크리스트 1번 통과"는 원래 다른 사실이고, 이제 둘 다 답이 있다.**
앞은 vLLM 0.27.1이 aarch64에 설치되고 torch가 `(12, 1)`·`NVIDIA GB10`을 인식한다는 것이다.
뒤의 판정 필드는 `roles.*.startup.ok` — **서버가 실제로 떠서 `/v1/models`를 주는가**이고,
로컬 TCP 차단이 풀린 뒤 `qwen3-4b`가 떠서 registry에 실측이 들어갔다(§5). **단 조건이
붙는다 — `VLLM_USE_DEEP_GEMM=0`이 없으면 이 모델은 기동하지 않는다.** 통과는 "그 인자를
준 상태에서"이고, 인자를 빼면 1번이 다시 실패한다.

**나머지 6개는 여전히 미측정이고, 4번만 부분이다.** registry에 있는 값은 `tok_s_single`과
`tok_s_batch32` 둘뿐이라 8/16 동시 요청은 아직 답이 없다. `mem_gb_ctx8k`는 0인데 이것은
"0 GB"가 아니라 **안 쟀다**는 뜻이다(§5의 `nvidia-smi [N/A]` — 소스가 정해져야 답이 나온다).
측정은 [ADR 0007](docs/decisions/0007-atom-qwen3-4b-first-measurement.md)에 정본으로 남겼다.
**단 그 안의 원문 자리는 비어 있다** — `cli bench`는 JSON을 stdout으로만 내고 아무도 파일로
받지 않아서, `checklist` 배열처럼 registry에 자리가 없는 필드는 터미널과 함께 사라졌다.
그래서 위 "미측정"과 "쟀는데 registry에 안 들어가는 값"을 지금은 구분할 수 없다.

```
[x] vLLM × CUDA 13.0 × sm_121 커널 호환성   ← 2026-08-17 기동 확인. VLLM_USE_DEEP_GEMM=0 필요
[ ] 역할별 모델 로드 및 실제 통합메모리 점유    ← mem_gb_ctx8k=0. nvidia-smi --query-gpu가 [N/A]라 소스 대기
[ ] context 8K/16K/32K에서 KV cache 점유와 처리량
[~] 단일 스트림 tok/s vs 배치(8/16/32) 합산 tok/s ← 단일 39.65 · batch32 127.69. 8·16 미측정
[ ] automatic prefix caching 동작 및 적중률       ← R6 판정 지점
[ ] structured output(JSON schema 강제) 동작
[ ] NVFP4 양자화 시 Critic 반박 정확도 저하
```

마지막 항목만 bench 범위 밖이다 — 고정 반박 표본이 있어야 답이 나온다(평가셋 준비 중).
⚠ **그 항목의 전제는 2026-09-09에 사라졌다** — 위 체크박스 문구는
`src/bench_report.py`의 `CHECKLIST`와 같은 것이고 **2026-09-09에 그 줄을 고쳤다**(#93 재정의 반영). 무엇이
무너졌고 무엇이 대신 서는지는 [model-serving.md](docs/model-serving.md) §4 머리와
[D4](docs/open-questions/D/D4.md)가 적는다.

**이것이 통과하기 전까지 어느 모델도 프로덕션 프로필로 올릴 수 없다.** ⚠ 그 이유는 한 가지가
아니다 — 「`benchmark` 값이 전부 0」도, 「전부 부분 측정」도 틀리다. `cli._check_benchmarks`는
프로필의 역할 모델만 보고 **미측정과 부분 측정을 각각 한 줄씩** 낸다. `local_balanced`·
`local_balanced_hybrid`에 실제로 돌리면 두 줄이 다 나온다 — **2026-09-08 실행 기준**으로
**미측정 셋**(`devstral-small-2-24b`·`qwen3-embedding-0.6b`·`qwen3-reranker-0.6b`, 세 항이
전부 0)과 **부분 측정 둘**(`qwen3-8b`·`qwen3-coder-30b-a3b`, `mem_gb_ctx8k`만 0)이다.
⚠ **그 다섯은 그날 프로필이 부르던 모델이지 지금의 로스터가 아니다** — 정본은
[PROJECT-CONTEXT.md](PROJECT-CONTEXT.md) §1이고, 재배선되면 이 두 줄의 내용이 바뀐다. 그 표가 **registry
미등재**로 표시한 자리가 프로필에 오르면 benchmark 이전에 checksum부터 자리표시자다.

두 프로필(`local_balanced`·`local_balanced_hybrid`)이
`production: false`라 지금은 WARN이고, production으로 올리는 순간 같은 두 줄이 ERROR다.
`mem_gb_ctx8k`는 registry 전 항목이 0이라 **완전 측정은 아직 하나도 없다.** 처리량이 채워진
것은 `qwen3-4b`(`39.65 · 127.69` — 위 체크리스트의 `[~]`, 정본은 ADR 0007)와 2026-09-04 ATOM
실측인 `qwen3-8b`(`35.78 · 388.46`)·`qwen3-coder-30b-a3b`(`53.4 · 66.42`) 셋뿐이다. 뒤 둘의
원천 보고서는 `config/models.yaml`의 각 블록이 경로로 드는데 **그 파일은 #142로 들어왔다** —
등재 당시에는 이 문단이 그 PR보다 먼저 도는 순간이 있어 링크로 걸지 않았다.
`bench --apply`가 그 값을 채우지만 **0인 항목은 쓰지 않는다.** 채워지지 않았다는 것은
재지 못했다는 뜻이고, 그 상태가 그대로 보여야 한다.


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
| `tool_images` | 컨테이너 미사용 (`fake` tool_runner) | `dev_docker`/`dev_singularity`/`local_balanced`/`b1_single` 프로필 + `harness-tools:v3` 이미지 |
| `kb_snapshot` | KB 미구축 (`kb-none` 자리표시자) | KB 구축 + `--kb-snapshot` |
| `model_checksums` | registry가 합성 예시값(전부 0) | 실서빙 모델 확정 + checksum 산출 (model-serving.md §5) |
| GPU-second | ~~실측 전~~ → **코드가 청구하지 않았다** (2026-08-17 발견). 모델 호출은 `ModelResult`에 GPU 시간 필드가 아예 없었고, 검색·도구는 초로 내림해 1초 미만을 버렸다 | 청구 경로 수정 + 실서빙 run. `incomplete_fields()`가 실서빙 역할의 미측정을 판정한다 |

**`model_checksums` 행은 2026-08-17까지 이 표에만 있고 코드에는 없었다.** `incomplete_fields()`가
그 필드를 보지 않았으므로 checksum이 전부 0인 채로 돈 run도 경고 없이 통과했다 — 기록상
재현 가능해 보이는데 어느 모델이었는지는 지시하지 못하는 상태였고, `repo_dirty`가 막으려던
것과 같은 계열의 구멍이다. 이제 코드가 판정한다.

**단, 세지 않는 역할이 둘 있다.**

1. **Fake 역할.** `FakeModelClient`는 프로세스 안에서 도는 코드라 해시할 가중치도
   토크나이저도 없고, 그 checksum 0은 미기록이 아니라 정확한 값이다.
2. **`retriever: fake`인 프로필의 embedding·reranker** (2026-09-01, 이슈 #108).
   그 클라이언트는 hybrid 빌더 안에서만 만들어져 **한 번도 뜨지 않는다.** 그런데
   `b1_single`·`local_balanced`가 그 두 역할에 실모델을 선언해서(hybrid로 올리는 날 쓸
   모델을 적어 둔 것이다) 안 뜨는 모델의 미실측 checksum이 이 판정에 들어왔다 —
   실서빙 세 역할이 전부 실측인 프로필이 "미실측"으로 신고됐다.

판정은 **역할별**이라 `single_gpu_12g`처럼 섞인 프로필에서도 실서빙 역할만 걸린다.
`doctor`가 같은 규칙으로 run 전에 미리 WARN한다.

**기록은 좁히지 않았다.** `RunRecord.model_checksums`는 계속 선언한 전 역할을 담는다 —
프로필이 선언한 사실이고 빠짐이 없어야 한다. 갈린 것은 **판정 대상**뿐이다
(`config.served_models()`).

**바로 위 GPU-second 행이 같은 계열의 두 번째다.** `model_checksums` 행을 고친 그날
(2026-08-17), 이 표는 GPU-second도 "`incomplete_fields()`가 판정한다" 아래 올려 두고 있었는데
코드는 그 값을 **청구조차 하지 않고 있었다** — 모델 호출에는 GPU 시간 필드가 없었고, 검색과
도구는 초로 내림해 1초 미만을 버렸다. 한 행을 고치면서 바로 다음 행을 같은 상태로 남긴
것이다. **표에 적는 것과 코드가 판정하는 것은 다르다**는 사실이 이 문단의 값이고, 다음에
이 표에 행을 추가할 때 먼저 물어야 할 질문이기도 하다: *그 판정을 실제로 하는 코드가
있는가?*

---

## 9. 미해결 결정 사항

> 2026-09-24 — 이 표는 그날 D10·D12·D3 세 행만 다시 보고 표시를 달았다. 나머지 행은 행마다 적힌 날짜까지의
> 기록이라 그날의 상태가 아니다 — 예컨대 D13 행의 「해소(2026-08-20)」는 인덱스·본문의 상태와 다르다.
> 현재 상태는 D 본문이 든다(머리의 보관 배너).

| # | 항목 | 영향 |
|---|---|---|
| **D2** | `scout_threshold: 0.5`가 근거 없는 초기값 | 조건 간 비교 전 확정 필요. `cli.py`의 `PROVISIONAL_THRESHOLDS`가 `doctor`에서 경고. **2026-08-14 실측: benign 표본이 정확히 0.50으로 경계 통과.** 지금 바꾸지 않는다 — 픽스처가 떨어지도록 맞춘 값이 될 뿐이다 |
| **D9** | ~~소스 저장소 우선 vs 펌웨어 우선~~ | **해소(2026-09-19 — 결정은 2026-09-09)** — [ADR 0010](docs/decisions/0010-harness-structure-and-model-roster.md) §5가 1차 대상을 **C/C++ 소스 저장소**로 확정했고(확정된 슬라이서가 소스 기반 CPG), 살아 있는 소스를 거기 맞춘 것이 2026-09-19다. **바이너리·펌웨어는 2차다** — **이 문서 §5**의 "펌웨어 추출(binwalk)" 빈칸이 그 2차 경로이고, `Artifact.parent_id`를 세팅하는 코드가 없는 상태는 그대로다. 실인덱스에서 바이너리가 후보를 못 내는 것은 별건이다(D79) |
| **D13** | 캡슐의 `callers`/`callees`를 무엇이 채우는가 | **해소(2026-08-20)** — 인덱서 산출물이 `index_graph`를 거쳐 두 Retriever의 두 섹션을 채운다. **2026-08-21** 그 섹션의 본문이 프롬프트에 실리고(W1) 절단이 trace에 남는다(W7). **2026-08-23** 심볼 단위 캡슐이 들어왔다(W2) — `names_for()`의 본문 판정이 줄 범위가 됐고 `HybridRetriever._collect()`의 같은 전제도 함께 고쳤다. 남은 것: `types`(index-v2 필요), 빈 그래프의 E2, 절단 2종 |
| **D17** | 프롬프트의 "사실 블록이 전부다"가 산문 자리표시자를 장식으로 만든다 | **신규(2026-08-21)** — 지금은 사실 블록에 같은 내용을 넣어 우회하고 대가는 토큰 2배 계상. 고치면 `prompt_template_version`이 올라가 이전 결과와 비교가 끊긴다 |
| **D18** | ~~8단계 정책이 게이트 등급을 읽지 않는다~~ | **해소(2026-09-20 — 커밋 `3e5c19f`, 선택지 (a))** — 8단계 승격이 `ok`에 더해 게이트 등급을 읽는다. 재현에 성공해도 E4 미만이면 무전이로 `validated`에 홀드한다(F-10b, `evidence_below_proven`). ⚠ **그 F-10b 갈래는 오늘 노드 경로에서 안 탄다** — `ok=True`면 게이트 등급이 반드시 E4다. 고친 것은 구조이지 오늘의 관측이 아니다. 그전에는 `ok` 하나로 정해졌고 그것이 §5의 "지금은 세 단계 전부에서 판정한다"를 한정하고 있었다. 내용은 [open-questions D18](docs/open-questions.md)에만 둔다 |
| **D1** | ~~B1/B1'/B3/B3' 라벨이 2×2와 파이프라인 두 축으로 사용됨~~ | **해소(2026-09-20)** — 2×2가 폐기되고 Scout 오케스트레이터로 확정돼 **축이 하나만 남았다.** `CONDITION_PATTERN`에서 `@2x2`를 뺐다 |
| **D10** | 재평가 횟수를 **아무 데도 보관하지 않는다** (2026-08-31 D25로 in-memory 카운터 제거) | **닫힘(2026-09-25 — [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 6 승인 · 상태는 [D10](docs/open-questions/D/D10.md) 본문이 든다)** — 결정 6은 재평가(requeue) 경로를 배선하지 않기로 해 보관할 횟수를 없앤다. 영향은 「`requeue_count` 생산자가 없어 `requeue_limit_reached`가 단일 run 안에서도 발동하지 않는다」다. D10 본문 끝의 resume·스키마 버전 잔여(구버전 `state.db` 로드)는 [D86](docs/open-questions/D/D86.md)이 든다 |
| D11 | `Evidence`에 "발견 건수" 필드 부재 | **semgrep은 해소**: `--json` 결과 문서의 유무로 판정하므로 exit code 관례에 의존하지 않는다(2026-08-14 크래시가 "발견함"으로 집계된 사건 이후). **clang-tidy는 여전히 미해결** — 컴파일 실패와 경고 발견이 같은 exit code라 출력 문자열(`clang-diagnostic-error`)로 가른다 |
| **D12** | build 실행 단계 소속 (2단계 vs 8단계) | **닫힘(2026-09-25 — 선택지 (a) · [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 승인 · 상태는 [D12](docs/open-questions/D/D12.md) 본문이 든다)** — [architecture.md](docs/architecture.md) §3을 코드에 맞췄다. build는 2단계 신호 도구다(`src/orchestrator/nodes/signals.py:26`의 `SIGNAL_TOOLS`). 그전의 영향은 「문서와 구현 불일치. 현재 코드는 2단계에 프로브」였다 |
| D3 | `capsule_expansion_max: 3` 초기값 | 실측 후 조정. ⚠ 2026-09-24 — 확장 상한은 need_evidence 루프의 `max_hops`로 대체 예정이다([ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 6) — 그 값을 배선하는 PR이 D3을 닫는다 |

---

## 10. 해석 시 주의

### Scout 풀 팬아웃 전/후 지표를 직접 비교하지 않는다

**2026-09-11에 배선됐다(PR #178, ADR 0010 §3).** ⚠ **provisional 풀 프로필
`local_balanced_pool`이 배선됐다(PR #190, `production: false`) — 실서빙 프로필은 여전히
단일 `scout`이라 그쪽으로 돈 run은 「이전」이다.** 아직 `local_balanced_pool`로 돈 run이
없으므로 분모 단절의 **실측**은 첫 풀 run에서 난다 — 그 run의 지표를 단일 scout run과
직접 비교하지 않는다.

4단계의 작업 단위가 `target`에서 `(target × Scout 자리)`로 바뀌면 아래 축들이 끊긴다.
⚠ **`deferred` 행만 「안 끊긴다」가 요지다** — 같은 표에 둔 것은 옆 둘(`dropped`·`count(target_skipped)`)과 헷갈리기 때문이다. **나머지는 전부 끊기는 축이고, 마지막 행(capsule cache 적중률)도 그렇다.**

| 축 | 단일 `scout` | 풀 셋 |
|---|---|---|
| 후보의 단위 | 심볼 하나당 1건 | **심볼 하나당 3건** (합집합 — 투표가 아니다) |
| 제거율의 **분자**(제거된 후보 수)가 서는 모집단 | 후보 = 대상 (1:1) | **후보 = 대상 × 자리 수** — 모집단이 자리 수만큼 커지므로 분모를 그대로 두면 비율이 1을 넘을 수 있다 `[계산]`. 단위를 맞추려면 분모도 자리 수만큼 곱한다. ⚠ **어느 값이 분모인가는 여기서 정하지 않는다** — §10의 *"세 수를 섞으면 같은 run이 세 개의 다른 제거율을 낸다"* 문단이 정본이다 |
| `dropped` | 못 본 target 수 | **시도 수** — 멤버 하나가 죽어도 형제가 그 대상을 본다([D70](docs/open-questions/D/D70.md)) |
| `count(target_skipped)` | 못 본 target 수 | **두 단위의 혼합** — `over_candidate_max`는 대상(팬아웃 전), `gateway_error`는 시도. `reason`으로 먼저 가른다 |
| `deferred` | 못 본 target 수 | **대상 그대로** — `candidate_max` 컷이 팬아웃 전이다. `dropped`와 **같은 단위가 아니다** |
| capsule cache 적중률 | Retriever 훅이 전량 기록 | `state.capsules`가 앞에서 단락해 **이벤트가 준다**(실측 9→5 · 0.667→0.40, 같은 항목) |

**역할별 토큰 카운터도 갈린다** — 풀은 `scout`을 안 켜고 그 target의 **첫 멤버**만 켜는데
`token_counter` 이벤트는 여전히 `scout`을 싣는다([D69](docs/open-questions/D/D69.md)).

### 심볼 인덱스 도입 전/후 지표를 직접 비교하지 않는다

**2026-08-23에 일어났다(W2).** 예고가 아니라 기록이다. 캡슐 대상이 파일 단위
(`Location(file=...)`, symbol 없음)에서 **심볼 단위**로 바뀌었고, 끊긴 축이 다섯이다.

| 축 | 이전 | 이후 |
|---|---|---|
| 후보의 단위 | artifact 하나당 1건 | 심볼 하나당 1건 |
| 제거율의 분모 | 분석 대상 artifact 수 | 인덱싱된 함수 수 (`target_plan.symbol_targets`) |
| 헤더·파이썬·바이너리 artifact | 후보 1건씩 생겼다 | **후보가 안 생긴다** (심볼이 없다) |
| Scout가 보는 캡슐 | `target_function` + `static_signal` | `callers`·`callees`가 추가됐다 (D20) |
| 캡슐 캐시 | `v2` / `hybrid-v2` | `v3` / `hybrid-v3` — **전량 무효** |

앞의 셋은 분모를 바꾸고, 넷째는 Scout 점수 분포를 바꾸며, 다섯째는 cache hit ratio를
이전 run과 비교할 수 없게 만든다.

**임계값은 이 변경에서 건드리지 않았다.** `scout_threshold`(D2)의 입력이 넷째 축 때문에
바뀌지만, 실모델 표본이 2점뿐이라 그 2점으로 임계를 옮기면 근거가 아니라 맞춤이 된다.

**인덱스가 없는 프로필은 파일 단위로 되돌아간다.** `dev_fake`·`dev_hybrid`가 그렇고, 그
사실은 `target_plan.fallback_artifacts`에 남는다 — 같은 지표를 볼 때 두 종류의 run이
섞여 있을 수 있으므로 이 값으로 먼저 갈라야 한다.

**2026-08-23 실측 — 분모가 얼마나 바뀌는지가 나왔다**([실험 보고서](docs/experiments/harness/2026-08-23-pipeline-zlib-atom.md)).
zlib에서 분석 대상 artifact **79건**이 심볼 **542건**이 됐다. 그중 **31건**은 심볼이 0건이라
후보를 만들지 않았고(`.h`·바이너리 등), **286건**은 `candidate_max: 256`에 잘려
`target_skipped`로 남았다. **즉 이 run의 제거율 분모는 79도 542도 아닌 256이다** — 세 수를
섞으면 같은 run이 세 개의 다른 제거율을 낸다. 분모로 쓸 값은 `target_plan.planned`다.

### Gate E1의 `location_ok`는 여전히 작동하지 않는다 — 원인은 후보 위치가 아니었다

> ⚠ **2026-09-24 — 이 절의 제목과 2026-08-23 문단들(「왼쪽에 값이 없다」·「어느 쪽도 `location=`을 넘기지
> 않는다」·「…채워야 하고, 그것은 별개의 작업이다」)은 #248 전의 서술이다.**
> #248부터 semgrep은 첫 히트 위치를 `Evidence.location`에 싣는다(컨테이너 `first_hit_location()`과
> Fake 짝이 함께). clang-tidy는 그대로 `None`이다. 등급 영향이 0인 것은 아래 2026-08-25 블록 그대로다 —
> 위치를 싣는 것이 STATIC Evidence뿐이다. 본문은 날짜 기록이라 고치지 않고, 경과는
> [D23](docs/open-questions/D/D23.md) 본문이 든다.

**2026-08-23 정정.** 이 문서는 오래 "심볼 단위로 좁혀지면 `location_ok`가 비로소 작동을
시작한다(지금은 `Evidence.location`이 후보 위치와 겹칠 일이 사실상 없다)"고 적어 왔다.
**원인을 틀리게 짚었다.** 겹칠 일이 없는 것이 아니라 **왼쪽에 값이 없다.**

`Evidence(...)`를 만드는 자리는 실질적으로 둘인데(`container_tools`·`fake_tools`) **어느
쪽도 `location=`을 넘기지 않는다.** 기본값 `None`이고, `gate.py`가
`ev.location is not None and ev.location.overlaps(candidate_location)`으로 왼쪽 항을 먼저
보므로 판정이 구조적으로 거짓이다.

심볼 단위 후보는 그 조건의 **오른쪽**만 정밀하게 만든다. `location_ok`를 열려면 semgrep
JSON과 clang-tidy 출력에서 줄 번호를 파싱해 `Evidence.location`을 채워야 하고, 그것은
별개의 작업이다(open-questions D23).

**2026-08-23 실측으로 확인됐다**([실험 보고서](docs/experiments/harness/2026-08-23-pipeline-zlib-atom.md)): 심볼 단위로 돌린 두 run에서
`location_ok`가 **픽스처 6건 · zlib 55건 전부 false**다. 후보 위치가 심볼로 정밀해졌는데도
값이 안 바뀌었다.

**2026-08-25 — 한 겹 더 정정한다. 채워도 등급이 안 바뀐다.**
위 문단은 "`Evidence.location`을 채우면 `location_ok`가 열린다"로 읽히는데, **열려도 등급은
그대로다.** `gate.py`의 술어가 `p1 = static_ok or location_ok`— **`or`**이기 때문이다.

[542건 보고서](docs/experiments/harness/2026-08-25-pipeline-zlib542-atom.md) §3 해석 ⑤의 실측:

| 집합 | `static_ok` | `location_ok`가 살아나면 |
| --- | :-: | --- |
| E2 **44건** | **True** | `p1`이 **이미 True**라 아무것도 안 바뀐다 |
| E0 **11건** | **False** | `static_ok`가 False = **STATIC Evidence 자체가 없다.** 줄 번호를 넣을 대상이 없다 |

→ **이 구성에서 `Evidence.location`을 채워 등급이 바뀌는 Finding은 0건이다.** `p1`의 `or`
구조나 술어 설계가 함께 오지 않으면 파서를 만드는 일의 등급 영향은 **0**이다
(open-questions D23). **`location_ok`는 이번이 세 번째 전건 false 확인**이다 — 픽스처 6 ·
zlib 55 · 오늘 63.

**그러면 D23의 값은 어디에 남는가**: 등급 축이 아니라 **후보 정렬 해상도**다. #70이 정적
신호를 심볼 단위로 올렸다 — 히트가 든 심볼은 상위(rank 2), 같은 artifact의 다른 심볼은
rank 1, 무신호는 0. 예전에는 큰 파일의 **모든 심볼이 똑같이 "신호 있음"**이었고
`runner._plan_targets()`가 artifact 단위였다.

### `report.md`의 "중복 제거 N건"을 제거율로 읽지 않는다

`run_20260823_0002`의 report.md 첫머리가 "중복 제거: 43건"이다. Finding 47건 중 43건이
접혔다는 뜻인데, **이것은 오탐 제거율이 아니다.** 이유 셋:

1. **뭉치는 키를 Fake가 만들었다.** `nodes/report.dedupe()`가 `(finding.cwe, _sink(finding))`로
   묶고 `_sink`는 `finding.path[-1].symbol`이다. 그 `cwe`와 `path`를 만든 auditor가 Fake다 —
   47건이 4개 sink로 접힌 것은 **Fake가 sink를 몇 종류만 만든다**는 뜻일 수 있고, 이 run은
   그 구별을 하지 못한다.
2. **dedupe는 상태를 바꾸지 않는다.** `report.run()`이 "상태는 바꾸지 않는다"고 못박고 있다.
   evaluation.md가 요구하는 단계별 제거율은 `policy_decision` 전이에서 나오고, 이쪽은
   9단계 렌더링이다. **두 축이다.**
3. **trace에 없다.** `dedupe` 이벤트가 없어 이 수는 `Report.duplicates` 필드와 report.md
   첫 줄에만 있다. 지표로 쓰려면 텍스트를 파싱해야 한다.

**이벤트를 만들지 않은 것은 판단이다** — 이 값이 아직 어떤 지표의 입력도 아니고, 필요해질
때 만드는 것이 맞다. 다만 그때까지 **이 수를 성능 주장에 쓰지 않는다.**

### GPU 회계 수정(2026-08-17) 전/후 비용 지표를 직접 비교하지 않는다

세 가지가 한꺼번에 바뀌었다:

| 축 | 이전 | 이후 |
|---|---|---|
| 모델 호출의 GPU 시간 | **청구되지 않았다** (`ModelResult`에 필드가 없었다) | `gpu_ms`로 청구된다 |
| 도구 시간 | `gpu_seconds`로 청구됐다 — **그 값의 0 아닌 부분은 전부 CPU 시간이었다** | `tool_ms`로 분리, 별도 예산(`Budget.tool_seconds`) |
| 단위 | 초 단위 내림 (1초 미만은 버려졌다) | 밀리초 |

→ **"finding 1건당 GPU-hour"의 분자가 다른 것을 세고 있었다.** 이전 값은 GPU를 가장 많이
쓰는 경로(모델 호출)를 0으로 두고 GPU를 쓰지 않는 경로(semgrep·clang)를 세고 있었으므로,
두 시점의 값은 크기 비교가 아니라 **정의 자체가 다르다.** 같은 표에 넣지 않는다.

측정 여부는 이제 기록에 남는다 — `gpu_ms_source`가 실측인지 wall-clock 근사인지 가르고,
실서빙 역할이 한 번도 못 잰 run은 `incomplete_fields()`가 비교표에서 제외한다(§8).

### 그래프 섹션 연결(2026-08-20) 전/후 캡슐 크기를 직접 비교하지 않는다

`callers`/`callees`가 **고정 64토큰에서 실측으로** 바뀌었고 `types`(역시 64)는 **사라졌다.**
캡슐 크기는 예산 소진 경로와 제거율에 들어가므로 **이 변경 전후의 지표는 같은 축이 아니다.**

바뀐 방향이 한쪽이 아니다:

| 상황 | 이전 | 이후 |
|---|---|---|
| index.json 없음 (`dev_fake`·`dev_hybrid`) | 세 섹션 192토큰 | 섹션 **부재** (0토큰) |
| index.json 있고 간선 많음 | 192토큰 | 실측 — 더 클 수도 작을 수도 |
| index.json 있고 간선 0건 | 192토큰 | `tokens=0, items=0` 섹션 |

`CAPSULE_POLICY_VERSION`을 올려(`v1`→`v2`, `hybrid-v1`→`hybrid-v2`) 캐시 키가 갈리므로
**이전 실행의 캡슐 캐시와 섞이지는 않는다.** 섞이지 않는 것과 비교 가능한 것은 다르다.

→ 인덱서 도입 때와 같은 종류의 단절이다. 두 시점의 "캡슐 1건당 토큰"과 그것에서 유도되는
비용 지표(finding 1건당 GPU-hour)를 한 표에 넣지 않는다.

### 프롬프트 본문 적재(2026-08-21) 전/후 탐지 품질 지표를 직접 비교하지 않는다

**바뀐 것은 캡슐 크기가 아니라 프롬프트 내용이다.** 바로 위 D13 항목과 성격이 다르다 —
그쪽은 섹션 토큰 수가 고정값에서 실측으로 바뀌어 **캡슐 자체**가 달라졌고, 이쪽은 캡슐의
숫자가 한 글자도 안 바뀐 채 **모델이 보는 것**만 달라졌다.

| 축 | 이전 | 이후 |
|---|---|---|
| `CapsuleSection`의 `tokens`·`items` | 실측 | **같다** |
| 캡슐 예산·`used_tokens` | — | **같다** |
| 프롬프트가 나르는 `callers`/`callees` | **이름표뿐**(`callers(312 tok)`) | 호출 지점 줄이 산문·사실 블록 양쪽에 |
| 사실 블록의 `callers` | **항상 빈 배열** (러너가 안 넘겼다) | 캡슐의 `callers` 섹션에서 파생 |

→ **모델의 입력이 달라졌으므로 발견 수·제거율·정밀도는 같은 축이 아니다.** 이전 값은
"모델이 호출 그래프를 못 본 상태"의 값이고, 그 상태에서는 **"못 봐서 못 찾은 것"과 "보고도
못 찾은 것"이 구별되지 않는다.** 그래서 이전 수치는 하네스가 아니라 Fake 어댑터를 잰 값에
가깝다.

`CAPSULE_POLICY_VERSION`은 **올리지 않았다.** 캡슐 구성 규칙(어떤 섹션이 몇 토큰)이 바뀌지
않았고 캡슐 캐시는 run 안에서만 사는 in-memory dict라 이전 실행과 섞일 경로가 없다
(knowledge-base.md §6의 상승 조건에 해당하지 않는다). **캐시 키가 같은 것과 지표가 비교
가능한 것은 다르다** — 위 표가 그 차이다.

비용 축도 함께 움직인다: 그래프 섹션이 산문과 사실 블록에 **두 번** 실리므로 프롬프트
토큰이 그만큼 늘었다. `source`가 이미 같은 상태였고(`bench.sized_prompt` docstring), 그
중복을 걷는 것은 프롬프트 v2의 일이다(open-questions D17).

### 8단계 게이트 배선(2026-08-21) 전/후 도구 검증 제거율을 직접 비교하지 않는다

그전에는 8단계가 Evidence Gate를 부르지 않아 `gate_eval` 이벤트가 6·7단계에만 있었다.
E4는 그것을 성립시킬 수 있는 유일한 단계에서 판정되지 못했으므로 **"E4가 0건"은 관측이
아니라 배선 부재였다.** 이전 run의 등급 분포를 지금 것과 같은 표에 넣으면 분모가 다르다
(`gate_eval` 행 수가 Finding당 2개 → 3개).

### `items`가 전부인지 잘린 것인지는 캡슐만 봐서는 모른다

`callers`/`callees`/`related` 모두 예산에 들어가는 만큼만 담고 `items`에 담은 수를 적는다.
**`items=3`이 "간선이 3개"인지 "30개 중 3개만 들어갔다"인지 구별되지 않는다.**
같은 종류가 하나 더 있다 — `target_function`이 예산을 다 먹으면 그래프 섹션이 아예 발행되지
못해 "인덱서가 안 돌았다"와 같은 모양이 되는 것(실측 임계: focused 32KB / expanded 96KB).

~~`HybridRetriever.MAX_CHUNKS`(400) 절단이 `on_truncate` 콜백으로만 드러난다~~ —
**2026-08-21 해소.** `cli.execute()`가 훅을 배선해 `retrieval_truncated` 이벤트가 남는다
(`collected`/`kept`). **이벤트 부재가 곧 "안 잘림"이므로 분모로 쓸 수 없다** —
`graph_index`가 run당 0건일 수 있는 것과 같은 종류의 주의다.

남은 둘은 open-questions D13에 있다.

### 재시도 토큰 회계 수정(2026-08-21)은 **커밋된 측정값에 영향이 없다**

`OpenAICompatModelClient.complete()`가 재시도 시 `gpu_ms`는 합산하면서 토큰은 마지막
시도분만 반환하고 있었다. 고쳤고, 보통 이런 변경에는 지표 단절이 따라붙는데 **이번에는
소급 영향이 0건**이다. 판정 근거를 남긴다:

| 확인 대상 | 결과 |
|---|---|
| 커밋된 manifest | `tests/fixtures/manifests/run_20260817_0004.json` **하나뿐**이고 `tool_runner: fake` + checksum 전부 자리표시자 |
| 그 run이 이 경로를 탔는가 | **아니다.** `FakeModelClient`에는 재시도 루프 자체가 없다 |
| 실모델 run | `docs/experiments/harness/2026-08-18-serving-atom.md` §2가 산출물을 **"보존하지 않았다"**고 명시 — 재계산할 대상이 없다 |

→ 그러므로 §10의 다른 항목들과 달리 **"이 시점 전후를 같은 표에 넣지 마라"가 아니다.**
비교 가능하다. 다만 **앞으로 실서빙 run이 쌓이면 이 수정 이전 값은 존재하지 않는다**는
사실만 기록해 둔다.

D14가 같은 형태의 판정(현재 6개 trace에 `gpu_ms` 키가 없어 실제 영향 0)을 한 선례다.

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

**2026-08-25 — 실대상 542건에서도 같다. 오히려 더 선명하다.**
[542건 보고서](docs/experiments/harness/2026-08-25-pipeline-zlib542-atom.md) §3 해석 ⑨에서
`graph_ok` **55/55** · `path_ok` **55/55** · `upheld` **8/8** · `confidence` **0.6 고정** —
**네 값이 전부 100%이거나 상수다.** Fake auditor가 항상 GRAPH Evidence를 붙이고
SOURCE/PROPAGATE/SINK 3단계를 만들며 Fake Critic이 전부 uphold하기 때문이다.
**E2 44건은 Fake의 산물이지 파이프라인 성능이 아니다** — 대상이 픽스처에서 실저장소로
바뀌어도 이 문장은 그대로다.

### `output_max` 하향(2026-08-28, 8,192 → 4,096) 전/후 run 시간과 실패율을 직접 비교하지 않는다

`output_max`는 두 지표에 직접 걸린다.

- **run 시간.** `run_20260828_0002`는 869초 중 **792.6초(91.2%)를 실패한 `model_call` 5건**에
  썼다. 폭주는 상한까지 생성하므로 그 항이 상한에 정비례한다 — 상한을 절반으로 하면 이
  항도 절반이 된다. run A(8,192)와 그 뒤 run의 총 시간은 같은 조건이 아니다.
- **구조화 출력 실패율.** 상한을 내리면 **정상 응답이 잘려 파싱 실패로 뒤집히는** 경우가
  생길 수 있다. 관측 최대는 auditor 2,424(`run_20260827_0002` seq=1707, `ok=true`)라 4,096은
  그 1.7배지만, 그 최대값은 **run 하나에서 나온 한 건**이다.

**자르고 있다는 신호를 이 필드로는 못 가른다.** `model_call`의 `output_tokens`는
`openai_compat.complete()`가 **재시도분까지 합산한 값**이라(`src/adapters/openai_compat.py`
89·94·127행) `ok=true` 호출 하나의 값을 `output_max`와 견줘도 절단이 안 갈린다. 1차가
상한에 잘리고 2차가 살아난 호출이 있었다면 `output_tokens`는 두 시도의 **합**이라
`== output_max`가 되지 않는다. `output_max 8,192`에서 합이 상한을 넘은 `ok=true` 호출이
**네 건** 있다(run B `8,402`, v2 `8,386·8,428·8,481`).

**⚠ 그 네 건이 "1차가 상한에 잘렸다"는 증거는 아니다.** `a₁ + a₂ = 8,402`이고 두 항이 다
`8,192` 이하라는 데서 나오는 것은 `a₁ ≥ 210`뿐이다 — [v2 스모크 보고서](docs/experiments/harness/2026-08-29-prompt-v2-smoke-atom.md)
§5가 그 추론을 명시적으로 거부한다("재시도라고만 적는다. 폭주가 아니다").
여기서 말하는 것은 **재시도가 있었다**는 것뿐이고, 이 문단의 논지에는 그것으로 충분하다 —
합산 값이므로 등식 판정이 성립하지 않는다는 것이 논지다.

**그러므로 "8,192에서 신호가 안 나왔다"는 절단이 없었다는 뜻이 아니다** — 등식이 성립하는
경우가 거의 없을 뿐이다. 덧붙여, 상한에 잘린 응답이 JSON을 못 닫아 파싱에 실패하면 그 시도는
`ok=false` 경로로 가는데 그 이벤트에도 이제 `output_tokens`가 실린다(#82 — 예외가 usage를
들고 온 경우 값, 아니면 `None`). 재시도가 살아나면 호출 전체는 `ok=true`가 되고 그 값은 다시 합산이다.

지금 할 수 있는 것은 `ok=true`이면서 `output_tokens >= output_max`인 호출을 훑는 것뿐이고,
그것이 말해 주는 것도 "이 호출이 재시도했다"까지다 — 합이 상한을 넘는다고 어느 시도가
상한에 닿았다는 뜻은 아니기 때문이다. **시도별로 가르는 것은 `model_call.attempts`다**(#82·#83,
D55 닫힘 — 시도 수를 필드로 싣는다), 역할별 상한(D46 **(c)**)을 그 전에 발동시킬 판별식은
아직 없다.

**실제로 쓰인 값은 `RunRecord.thresholds`에 박제되므로** 어느 상한으로 돈 run인지는 사후에
갈린다. 막는 것은 비교뿐이다.

### Scout 점수 산출 방법론이 미정이다

프롬프트가 정하는 것은 `score | 0.0~1.0. 더 볼 가치의 정도` 한 줄뿐이다.
모델별로 점수 분포가 달라질 수 있어, 같은 임계값이 조건마다 다른 의미가 될 수 있다.

**2026-08-25 — 분포를 처음 쟀고, 우려보다 나쁘다.**
[542건 보고서](docs/experiments/harness/2026-08-25-pipeline-zlib542-atom.md) §3 해석 ⑦: 실서빙
Qwen3-4B가 zlib 심볼 **542건에 고유값 9개**만 쓰고 **71%(386건)가 정확히 `0.0`**이다.
`0.45`와 `0.65` 사이가 **완전 공백**이라 **임계값을 그 안 어디에 두든 승격이 55건으로
같다.** 즉 위 문단이 걱정한 것("모델별로 분포가 달라진다")보다 앞선 문제가 있다 —
**분포라고 부를 만한 것이 없다.**

`scout_threshold: 0.5`가 안전한 값인 것은 맞지만 **값을 잘 골라서가 아니라 해상도가
없어서**다. 0.45로 한 칸 내리면 55 → 78건(+42%)으로 튄다. **⚠ 이것은 Fake의 성질이
아니다** — scout은 `single_gpu_12g`의 유일한 실서빙 역할이다. 원인은 미확정이고 별도
항목으로 열었다(open-questions **D28**. 임계값 쪽은 D2).
루브릭 명시 또는 점수 결정론화 검토 필요. (D2와 함께 논의)

---

### `trigger_status` 도입·필드 재배치(2026-08-29) 전/후 구조화 출력 지표를 직접 비교하지 않는다

`Finding`에 필드를 하나 더하고 선언 순서를 바꾸면 `model_json_schema()`가 달라지고, 그
스키마가 그대로 `response_format.json_schema.schema`로 서버에 실린다
(`adapters/openai_compat._request_payload`). **구조화 출력 백엔드가 컴파일하는 문법이
달라진다는 뜻이다.**

그래서 [evaluation.md](docs/evaluation.md) §5.1의 **구조화 출력 실패율**을 이 변경 전후로
직접 비교할 수 없다. 특히 D46(공백 폭주)이 **문법 의존**이라는 것이 실측으로 나와 있다 —
같은 모델이 `run_20260827_0002`에서는 scout 64건을 전부 실패했는데 `run_20260827_0004`에서는
critic 7/7을 성공했고, D46 본문이 그것을 "필연이 아니라 입력 의존"이라고 적었다.

**막는 것은 비교뿐이고 측정 자체는 유효하다.** `RunRecord`가 `repo_commit`을 들고 있으므로
어느 쪽 스키마로 돈 run인지는 사후에 갈린다.

## 11. 관련 문서

| 문서 | 다루는 것 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 파이프라인 단계, 상태 전이 |
| [docs/agent-contracts.md](docs/agent-contracts.md) | 역할별 실행 계약, 입출력 스키마 |
| [docs/model-serving.md](docs/model-serving.md) | 하드웨어, Registry, 배포 전 검증 체크리스트 |
| [docs/evaluation.md](docs/evaluation.md) | 조건 정의, 누출 방지, 지표 |
| [docs/open-questions.md](docs/open-questions.md) | 미해결 항목 원본 |
| [docs/experiments/](docs/experiments/) | 실험 보고서 원문과 대조로 확정된 사실 |
| [docs/build-plan.md](docs/build-plan.md) | 앞으로 무엇을 어느 순서로 짓는가 (설계이지 현황이 아니다) |
