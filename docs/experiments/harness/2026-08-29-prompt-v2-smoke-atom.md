# 프롬프트 v2 스모크 — v1 대조 run과 서버 직접 프로브 · ATOM

> **run 두 건과 프로브 한 건이 하나의 대조다.** 같은 서버 프로세스(pid 3663640)가 둘을
> 연달아 받았고 `config/models.yaml`의 **한 줄**만 갈렸다. 그래서 파일을 나누지 않는다 —
> 갈라 놓으면 이 폴더가 생긴 사고([README.md](../README.md) 확정 #2, "보고서 둘이 갈렸다")를
> 그대로 재현한다.
>
> **이 보고서의 모든 수치는 2026-08-29 ATOM 세션에서 집계된 값이다.** 그 세션 명령 원문이
> §9에 있다. 집계되지 않은 값은 **`미기재`로 남긴다** — 산출물을 다시 열지 않았고, 역산도
> 하지 않았다. 되찾을 값의 목록이 §8.4에 파일·키 단위로 있다.
>
> **두 run 다 2×2 매트릭스 run이 아니다.** `experiments/2x2/config/run_matrix.yaml`이 정한
> `artifacts_root`를 쓰지 않았고(`--artifacts-root /srv/tenants/user/artifacts`),
> 팀 2×2 실험은 이 세션과 별개로 이미 진행 중이다. 이 두 run이 그쪽과 부딪히는 지점은
> §8.3에 있다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | run B **2026-08-29 17:39 KST 이전**(산출물 mtime `8월 29 17:39` — 그 둘은 run이 끝난 **뒤** 복사한 것이라 **종료 상한**이지 종료 시각이 아니다. 실측은 `미기재`) · v2 **2026-08-29 18:02:56 KST**(서빙 로그 마지막 줄) |
| 실제 서빙 모델 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` — 별칭 `qwen3-coder-30b-a3b`. **별칭이 아니라 `/v1/models`의 `root`로 확인했다**(§2 근거 로그) |
| `repo_commit` | run B `1f4c4da63b40fc17499944c3b49513742af7f0d7` (`main`) · v2 `e111eb8230548e98a1e1ccf04c5228eabb9fa38c` (`measure/v2-smoke-20260829`, **머지 대상 아님**). 양쪽 다 커밋된 상태에서 돌았다(run B는 `git status --porcelain` 무출력, v2는 `git commit -am` 직후). **`RunRecord.repo_dirty`의 실제 기록값은 미기재** |
| `run_id` | `run_20260829_0001` (run B) · `run_20260829_0002` (v2) |
| run 산출물 경로·보존 여부 | `/srv/tenants/user/artifacts/run_20260829_0001` · `/srv/tenants/user/artifacts/run_20260829_0002` — **둘 다 보존** |
| `tool_runner` | 선언 `docker` (`b1_single`) / 실제 `docker`. **오버라이드가 아닌데 경고가 오버라이드라고 신고했다** — D50의 네 번째 관측(§7.2). **PR #55가 고치는 중이다** |
| `--device` | `atom` |
| `tool_images` digest | `harness-tools:v1` = `b5d7048bc0a5` — **docker 이미지 ID 단축 12자리**다. 종류는 docker 이미지 config sha256이고, 같은 태그의 전체값을 [2×2 B1′ 보고서](2026-08-28-2x2-b1prime-qwen3-8b-atom.md) 메타가 `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17`로 적었다(앞 12자리 일치). **이 세션이 직접 받은 것은 단축형이고 전체값은 미기재** |
| `--gpu-memory-utilization` | `0.28` — 분모는 물리 통합메모리 **119.7 GiB** |
| 동시에 떠 있던 다른 서버 | **없음.** 기동 직후 `ss -ltnp`의 8000번대가 8002 하나뿐이다(§2 근거 로그) |
| 원문 산출물 보존 위치 | 각 run 디렉터리에 `trace.jsonl` · `state.db` · `report.md` · `run_manifest.json` · `serve_prefix_cache.txt` · `cli_stdout_runB.log` / `cli_stdout_runV2.log` |
| 서빙 서버 stdout | `/srv/tenants/user/artifacts/logs/serve_30b_runB_20260829_173017.log` — **두 run 디렉터리 양쪽에 사본을 복사했다** |
| `profile` / `condition` | `b1_single` / `B1prime@pipeline` (양쪽 동일) |
| 분석 대상 | `<DS>/blind/PWN-003/source` (`DS` = `/srv/tenants/user/experiments/2x2/dataset/track_a_evaluation_release_v1`) |
| `kb_snapshot` | `kb-none` (자리표시자 — run 경고) |
| `seed` · `sampling` · `source_hash` | **미기재** — `run_manifest.json`을 열지 않았다(§8.4 회수 6번) |

## 1. 요약

**경로 성립과 기제 판정이다. 성능 주장도 인과 주장도 하지 않는다** — 조건당 표본이 1건이고
프롬프트 버전이 갈렸다([evaluation.md](../../evaluation.md) §2.3 규칙 4).

| 항목 | 결과 |
| --- | --- |
| D52 기제 — 프롬프트가 없으면 필드가 안 찬다 | **성립. 항목을 닫는다.** v1 → `null` ×6 / v2 → `triggerable` ×6 (§3) |
| 스키마에 자리를 내면 필드가 나오는가 | **아니다.** 프로브가 선언된 optional 4개를 안 냈다 — 스키마는 필요조건이고 프롬프트가 충분조건이다 (§6) |
| 7단계 critic 진입 | **v2에서 처음.** run B는 Finding 6건이 전부 6단계에서 멈췄다(critic 호출 **0건**) (§4) |
| 두 run의 시간 차 6.1배 | **프롬프트 효과로 읽지 않는다.** 실행한 단계 수와 재시도 건수가 함께 달라졌다 (§4·§5) |
| `output_max 8192`가 폭주에 물린 시간 | **≥60.7%가 순손실**(`critic_failed` 3건). 재시도한 2건까지 합치면 그 5건이 run의 81.6%인데 **그중 버려진 몫은 미기재** (§5) |
| 정상 경로 출력 최대 | v1 **224** 토큰 / v2 **487** 토큰 — 4,096의 각각 1/18.3 · 1/8.4 (§5) |
| PR #49(D47)가 없앤 400 | **사라졌다.** 두 run 합쳐 `model_call` 56건에 **400 흔적 0건**. 항목을 닫는다 (§5) |
| `fail 0`이 `재시도 0`인가 | **아니다.** run B의 성공 호출 하나가 상한을 넘겨 재시도했다(`output_tokens` 8,402) (§5) |
| Finding 스키마 필드 순서 | **위반 0건**(관측 1건). 백엔드가 **강제**하는지는 미판정 (§6) |
| D44의 7단계 분할 | **실서빙에서 처음 세 항으로 닫혔다** — `critic_skipped 0 + gate_eval(7단계) 3 + critic_failed 3 = 6` (§4) |
| D48 검증 | **안 됐다.** `target_skipped`가 양쪽 0건이라 그 코드 경로가 한 번도 안 돌았다 (§7.2) |
| 이 두 run의 비교표 등재 | **제외.** `model_checksums` 자리표시자 · `kb_snapshot: kb-none` — run A·2×2 B1′와 같은 판정 |
| PWN-003 정답 대조 | **미실시** — 산출물을 다시 열지 않았다 |

**연 것 셋 · 닫은 것 둘.** 신설 D53(실패한 호출이 예산 장부에 안 실린다) · D54(머지된 v2를
고르는 모델이 없다) · D55(재시도가 실패율 지표를 갉는다). 닫는 것은 D47·D52다. 전부
[open-questions.md](../../open-questions.md)에 있다.

## 2. 실험 환경

- NVIDIA DGX Spark (GB10, sm_121) · aarch64 · vLLM **0.27.1** · `VLLM_USE_DEEP_GEMM=0`
- `--max-model-len 32768` · `--enable-prefix-caching` · 포트 8002 · docker 29.2.1
- `config/budgets.yaml`: `output_max 8192` · `candidate_max 256` · `critic_top_n 8` · `schema_retries 1`
- **PR #51(`output_max` 8,192 → 4,096)은 두 run 시점에 미머지다.** 두 run 다 8,192로 돌았다.

### 근거 로그

```
LISTEN 0      2048                     127.0.0.1:8002       0.0.0.0:*    users:(("vllm",pid=3663640,fd=43))
            "id": "qwen3-coder-30b-a3b",
            "root": "/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048",
structured_outputs_config=StructuredOutputsConfig(backend='auto', disable_any_whitespace=False
 1004:   376 376/376 376/5000 1902/500000
```

**8000번대에 8002 하나뿐이고, 별칭이 가리키는 실모델이 40 hex 스냅샷 경로다.** keyring은
기동 전후 모두 `376 376/376`으로 같다 — 이번 기동은 키를 먹지 않았다.

> **`disable_any_whitespace=False`가 로그 원문에 있다.** D46의 선택지 (a)가 지목한 서버
> 옵션이 **꺼진 상태**로 두 run이 돌았다는 뜻이다. 이 보고서는 그 옵션을 켜 보지 않았으므로
> (a)에 대해 아무 판정도 하지 않는다.

## 3. 실험 1 — D52: 프롬프트가 없으면 그 필드는 구조적으로 `null`이다

### 목적

`trigger_status`가 언제나 `null`이었던 원인이 **모델**인지 **프롬프트 부재**인지 가른다.
D52는 후자라고 적었고 근거는 저장소 grep 하나였다. 실행으로 확인한다.

### 조건

두 run의 차이는 `config/models.yaml`의 **한 줄**뿐이다.

```
-    prompt_template_version: "v1"
+    prompt_template_version: "v2"
```

```
{'scout': 'v2', 'auditor': 'v2', 'critic': 'v2'}
```

같은 서버 프로세스 · 같은 대상 · 같은 프로필 · 같은 `budgets.yaml`.

### 결과 — 성립

| | 프롬프트 | `state.db`의 `trigger_status` |
| --- | --- | --- |
| run B | v1 | `trigger_status":null` **×6** |
| v2 | v2 | `trigger_status":"triggerable"` **×6** |

### 근거 로그

```
      6 trigger_status":"triggerable"
      6 "produced_by":{"role":"auditor","model":"qwen3-coder-30b-a3b","prompt_version":"v2"}
      3 "produced_by":{"role":"critic","model":"qwen3-coder-30b-a3b","prompt_version":"v2"}
```

run B 쪽은 `trigger_status":null` ×6이고 `produced_by`가
`{"role":"auditor","model":"qwen3-coder-30b-a3b","prompt_version":"v1"}`이다.

저장소 쪽 근거:

```
$ git grep -l trigger_status -- prompts/
prompts/auditor/v2/meta.yaml
prompts/auditor/v2/prefix.md
```

`prompts/auditor/v2/prefix.md` 36~42행이 다섯 값을 정의한다.

```
Then set `trigger_status` to exactly one of:

- `triggerable` — the supplied source/facts establish an externally influenced path to the dangerous operation without a confirmed blocking defense.
- `guarded` — a concrete guard/bound in the supplied source blocks the claimed trigger.
- `no_reach` — the supplied source/facts establish that external input cannot reach the relevant path.
- `reachability_unknown` — a required source/reachability link is absent, so triggerability cannot be established either way.
- `no_signal` — the candidate's suspected dangerous operation/root-cause signal cannot be established from the supplied source/facts.
```

### 해석 ① — D52의 기제가 재현됐다. 항목을 닫는다

**`trigger_status`는 `prompts/auditor/v2/`에만 있다.** v1 프롬프트에 그 문자열이 0건이므로
v1으로 도는 run은 스키마가 자리를 내줘도 값을 못 받는다 — D52가 적은 그대로다.
`produced_by.prompt_version`이 건마다 남으므로 **두 종류의 `None`("프롬프트가 안 시킨 run"과
"시켰는데 모델이 안 낸 run")이 사후에 갈린다**는 `finding.py` 주석의 설계도 함께 성립했다.

**닫는 것은 "저장소에 프롬프트가 없다"이지 "필드가 채워진다"가 아니다.** PR #54가 v2 셋을
`main`에 넣어 D52의 선택지 (a)가 실행됐고, 이 run이 그것이 실제로 동작함을 보였다.
**그러나 기본 설정으로 도는 run은 여전히 전량 `null`이다** — `config/models.yaml`의 어느
모델도 v2를 고르지 않는다(§7.1 D54). 그 잔여가 D54이고, D52를 닫는 문구는 이 둘을 함께
적는다. **저장소에 프롬프트가 생겼다는 것과 기본 경로에서 값이 찬다는 것은 다르다.**

### 따라 나오지 않는 것

- **분포는 안 나왔다.** 다섯 값 중 `triggerable` 하나만 6건이다. D51 (1)의 닫는 조건이
  "emit 분포 관측"인데 1종은 분포가 아니다 — 여기서 `Literal`로 좁히면 못 본 네 값이
  Finding을 통째로 떨군다. **D51 (1)은 열어 둔다.**
- **모델이 옳게 판정했는지는 안 봤다.** 이 실험이 판정하는 것은 "값이 산출물에 남는가"이지
  "그 값이 맞는가"가 아니다. 정답 대조는 미실시다.

## 4. 실험 2 — 6단계 게이트 등급이 v1에서 `E0`, v2에서 `E1` 이상이다

### 목적

두 run의 시간 차(3m16 대 20m01, **6.1배**)가 무엇에서 왔는지 가른다. 프롬프트가 느린 것인가,
아니면 **다른 일을 한 것**인가.

### 결과 — 실행한 단계가 다르다

이벤트 수만으로 모델 호출 구성이 확정된다. **두 사실 위에서만 그렇다** — 아래 표를 읽기 전에
그 둘을 먼저 적는다.

1. **모델을 부르는 노드가 셋뿐이다.** `grep -rn "call_model(" src/`가
   [scout.py](../../../src/orchestrator/nodes/scout.py) 85행 ·
   [auditor.py](../../../src/orchestrator/nodes/auditor.py) 103행 ·
   [critic.py](../../../src/orchestrator/nodes/critic.py) 114행 셋만 낸다. 8단계 `validate`와
   9단계 `report`는 모델을 안 부른다
2. **`cache` 이벤트가 캡슐 조회 1회당 1건이다.**
   [fake_retriever.py](../../../src/adapters/fake_retriever.py) 116행의 `on_cache` 훅이 조회마다
   한 번 불린다. 2026-08-26 run이 같은 성질을 이미 보였다(D39: 604 = scout 542 + auditor 62)

| 이벤트 | run B | v2 | 차 |
| --- | :-: | :-: | :-: |
| `target_planned` | 19 | 19 | 0 |
| `cache` (캡슐 조립) | 25 | 25 | 0 |
| `tool_call` | 13 | 13 | 0 |
| `model_call` | 25 | 31 | **+6** |
| `gate_eval` | 6 | 9 | **+3** |
| `policy_decision` | 25 | 28 | **+3** |
| `transition` | 51 | 54 | **+3** |
| `stage_start` / `stage_end` | 63 / 63 | 69 / 69 | **+6** |
| `budget` | 38 | 41 | **+3** |
| `critic_failed` | (없음) | 3 | +3 |

`_triage_one`이 대상마다 LIGHT 캡슐을 조립하고([runner.py](../../../src/orchestrator/runner.py) 1015행),
`TRIAGED`인 후보에만 EXPANDED를 조립한다(1028행). **`cache 25 = 19 LIGHT + 6 EXPANDED`가
양쪽 같으므로 두 run 다 scout 19회 · auditor 6회다.** 남는 것이 critic이다.

| | scout | auditor | critic | 합 |
| --- | :-: | :-: | :-: | :-: |
| run B | 19 | 6 | **0** | 25 |
| v2 | 19 | 6 | **6** | 31 |

v2의 critic 6건은 `state.db`의 `produced_by` critic/v2 **×3**과 `critic_failed` **3**으로
독립 확인된다.

### 근거 로그

```
이벤트: {'stage_start': 69, 'stage_end': 69, 'token_counter': 1, 'tool_call': 13, 'budget': 41, 'graph_index': 1, 'target_plan': 1, 'target_planned': 19, 'cache': 25, 'model_call': 31, 'transition': 54, 'policy_decision': 28, 'gate_eval': 9, 'critic_failed': 3}
model_call 31 / ok 28 / fail 3
```

run B는 같은 집계에서 `stage_start 63, stage_end 63, tool_call 13, budget 38, target_planned 19,
cache 25, model_call 25, transition 51, policy_decision 25, gate_eval 6`이고 `critic_failed`
항목 자체가 없다. `model_call` 25 / ok 25 / fail 0.

### 해석 ② — run B의 Finding 6건은 전부 `E0`였다

`_analyze`는 **`HYPOTHESIZED`인 Finding에만** critic을 돌린다([runner.py](../../../src/orchestrator/runner.py) 662행).

```python
promoted = state.in_state(FindingState.HYPOTHESIZED)
selected = critic.select(ctx, promoted)
```

그리고 `policy._gate_grade`에서 `QUEUED`로 가는 등급은 **`E0` 하나뿐**이다.

```python
if grade is EvidenceGrade.E0:
    return self._act(Route.QUEUE, FindingState.QUEUED, ReasonCode.INSUFFICIENT_EVIDENCE, basis)
if grade is EvidenceGrade.E1:
    return self._act(Route.HOLD, FindingState.HYPOTHESIZED, ReasonCode.EVIDENCE_WEAK, basis)
```

⇒ critic 호출 0건은 `promoted`가 비었다는 뜻이고, Finding 6건이 존재하는데 비었다면
**여섯 건이 전부 `QUEUED` = 전부 `E0`**다. v2는 여섯 건 전부 critic에 올라갔으므로
**전부 `E1` 이상**이다.

`E0`은 `p1 = static_ok or location_ok`가 거짓이라는 뜻이고, 두 술어 다
`gate.evidences_for(finding, evidences)` — **그 Finding이 `evidence_refs`로 참조한 근거**만
본다([gate.py](../../../src/orchestrator/gate.py) 155~158행이
`[by_id[ref] for ref in finding.evidence_refs if ref in by_id]`로 거른다).

**그리고 `location_ok`는 셈에서 빠진다.** D23이 열려 있고 `Evidence.location`을 채우는 코드가
저장소에 없다 — `grep -n "location" src/adapters/container_tools.py`가 **0건**이다. 그 술어는
구조적으로 언제나 거짓이므로 **`p1`은 `static_ok` 하나로 줄어든다.**

⇒ 확정되는 것은 이것이다.

| | 6단계 등급 | 따라 나오는 것 |
| --- | --- | --- |
| run B | 6건 전부 `E0` | 참조가 **STATIC 근거로 해석된 것이 0건** |
| v2 | 6건 전부 `E1` 이상 | **최소 1건이 STATIC으로 해석됐다** |

**"유효 근거가 0건"이라고 쓰지 않는다.** "STATIC으로 해석된 것이 0건"은 세 경우를 함께
덮는다 — `evidence_refs`가 비었거나, 참조한 것이 전부 비-STATIC이거나, 참조한 id가
`by_id`에 없어 조용히 버려졌거나(158행 `if ref in by_id`). **셋을 가르려면 §8.4 회수
7·8번이 필요하다.** 프로브에서 `evidence_refs`가 아예 빠진 것은 그 셋 중 첫째 경우를
가리키지만, run B가 그 경우였다는 근거는 아직 없다(§6).

`gate_eval` 6 대 9도 같은 자리에서 맞는다 — v2의 9는 **6단계 6건 + 7단계 3건**이다.
`critic.run`은 성공했을 때만 `ctx.evaluate_gate(..., stage=Stage.CRITIC)`에 닿고, 실패한
3건은 그 줄 전에 `SchemaParseError`로 빠진다.

### 해석 ③ — D44의 7단계 분할이 실서빙에서 처음 세 항으로 닫혔다

D44는 "완주했다"가 "전량을 봤다"가 아님을 기록으로 남기려고 7단계를 세 갈래로 갈랐다.
v2가 그 셋을 처음으로 동시에 채웠다.

```
critic_skipped 0  +  gate_eval(stage=7) 3  +  critic_failed 3  =  6  =  선별된 Finding 수
```

- `critic_skipped 0` — Finding 6건이 `critic_top_n 8` 아래라 잘린 것이 없다
- `gate_eval(stage=7) 3` — 판정을 받은 건
- `critic_failed 3` — 7단계에 **들어갔다가** 파싱 실패로 판정을 못 받은 건

세 항이 선별 수와 정확히 닫히므로 **분모가 어디서 줄었는지가 trace만으로 읽힌다.**
[evaluation.md](../../evaluation.md) §6이 요구하는 "Finding 수를 그대로 나란히 놓지 않는다"의
근거가 이 등식이다.

### 따라 나오지 않는 것

- **무엇이 등급을 올렸는지는 모른다.** `p1`의 어느 술어가 뒤집혔는지는 `gate_eval`의
  `predicates`를 봐야 갈리고, 이번에 그 값을 집계하지 않았다(§8.4 회수 1·2번).
- **6.1배를 프롬프트 효과로 읽지 않는다.** v2는 7단계 6건을 **더 돌았고** 재시도도 6건 대
  1건이다(§5). 두 원인이 같은 방향으로 겹쳐 있어 이 표본으로는 분리되지 않는다.
- **`E0`이 v1의 성질인지 이 대상의 성질인지 모른다.** 대상이 PWN-003 하나다.

## 5. 실험 3 — 폭주·재시도가 먹는 시간, 그리고 `fail 0`이 숨긴 것

### 목적

PR #51이 `output_max`를 8,192에서 4,096으로 내리자고 하면서 근거로 든 것이
"run 시간의 지배 항이 폭주이고 그 항이 상한에 정비례한다"이다. 그 근거는
`run_20260828_0002`(run A) 한 건이었고 **그 run은 D47 이전**이다. D47이 닫힌 뒤에도 같은가.

### 결과 — 같다. 그리고 400은 사라졌다

| 항목 | run B | v2 |
| --- | --- | --- |
| `time real` | 3m16.468s = 196.5초 | 20m0.956s = **1,200.956초** |
| `model_call` `wall_ms` 합 | 미기재 | **1,194초 = run의 99.4%** |
| 최장 5건(초) | 미기재 | `[125, 126, 243, 243, 243]` |
| `model_call` ok / fail | 25 / 0 | 28 / 3 |
| **400 흔적** | **0** | **0** |
| `output_tokens` 상위(ok) | `[201, 210, 223, 224, 8402]` | `[418, 469, 473, 477, 487, 8386, 8428, 8481]` |

### 근거 로그

```
model_call 총 시간: 1194 초 / run 총 1201초
가장 오래 걸린 5건(초): [125, 126, 243, 243, 243]
output_tokens 상위8: [418, 469, 473, 477, 487, 8386, 8428, 8481]
8192 초과(= 재시도 합산) 건수: 3
400 흔적: 0
```

```
--- critic_failed 3건 ---
  {"reason": "schema_parse_failed", "error_detail": "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON: EOF while parsing an object at line 3993 column 4 [type=json_inval
  {"reason": "schema_parse_failed", "error_detail": "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON: EOF while parsing an object at line 4013 column 0 [type=json_inval
  {"reason": "schema_parse_failed", "error_detail": "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON: EOF while parsing an object at line 10624 column 0 [type=json_inva
```

서빙 로그의 생성 속도:

```
(APIServer pid=3663640) INFO 08-29 18:02:16 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 65.6 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 2.5%, Prefix cache hit rate: 50.7%
(APIServer pid=3663640) INFO 08-29 18:02:36 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 64.3 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 3.0%, Prefix cache hit rate: 50.7%
```

### 해석 ④ — 시간의 지배 항은 폭주다. PR #49 이후에도 그렇다

`8,192 ÷ 65 ≈ 126초`다. **관측된 125·126초가 상한까지 간 1회 생성과 맞고**, 243초는 그
두 배 — `schema_retries: 1`이라 실패 하나가 2회를 태우는 값과 맞는다.

| | 초 | run 대비 |
| --- | :-: | :-: |
| `critic_failed` 3건 × 243초 — **폭주가 확정된 건** | **729** | **≥60.7% — 판정 0건을 낸 순손실** |
| 여기에 재시도한 호출 2건(125·126초)의 **전체** 시간까지 더하면 | **980** | **81.6%** |

**두 번째 행을 "버려진 시간"으로 읽지 않는다.** 그 2건은 재시도로 **살아났으므로** 버려진
것은 1차 시도 몫뿐인데, 그 크기를 모른다(해석 ⑥). 총 출력이 ~8,400 토큰이고 총 시간이
125·126초라 `8,400 ÷ 126 ≈ 67 tok/s`로 로그와 맞지만, **그 합을 두 시도로 어떻게 가르든
총 시간은 같다** — 시간이 분할을 못 가른다. 확정된 순손실은 첫 행의 729초다.

PR #51이 run A에서 잰 91.2%와 같은 자릿수다. **그리고 이 run은 D47이 닫힌 뒤다** —
`model_call` 56건(25 + 31)에 **400 흔적이 0건**이고, run A에서는 실패 5건 중 4건이 400이었다.
PR #49가 재시도 프롬프트의 입력 초과를 실제로 없앴는데도 **폭주가 먹는 시간 비중은 그대로다.**

⇒ PR #51의 전제가 D47과 무관하게 독립으로 재확인된다. 91.2%가 재시도 되싣기의 산물이라는
반론이 가능했는데, **아니었다.**

### 해석 ⑤ — PR #51에 없던 숫자 두 개

| | run B (v1) | v2 | 4,096 대비 |
| --- | :-: | :-: | :-: |
| 정상 경로 출력 최대(재시도 합산 제외) | **224** | **487** | 18.3배 · **8.4배** |

PR #51의 `budgets.yaml` 주석은 여유를 "3.4배 → 1.7배"로 적었고 그 분모가 8/27의
**2,424**(v1 auditor, `run_20260827_0002`)다. **이 두 run은 그 값 근처에 못 간다.**
2,424를 뒤집지는 못하지만 — 그것이 지금까지의 관측 최대이므로 — **v2 프롬프트로 도는
이 대상·이 모델에서는 4,096이 관측 최대의 8.4배**라는 한 점을 더한다.

### 해석 ⑥ — `fail 0`이 `재시도 0`이 아니다

**run B는 `ok 25 / fail 0`인데 성공 호출의 `output_tokens` 최대가 8,402다.**
`openai_compat.complete()`가 재시도분을 합산하고([openai_compat.py](../../../src/adapters/openai_compat.py) 74~89·94·127행),
요청 하나의 상한이 `max_tokens = output_max = 8,192`다. ⇒ **1회 시도로 8,402가 나올 수 없다.
그 호출은 최소 2회 시도했다.**

같은 셈이 v2의 `8386 · 8428 · 8481` 세 건에 걸린다.

| | 실패로 끝난 재시도 | 회복한 재시도 | 관측 가능한 재시도 총수 |
| --- | :-: | :-: | :-: |
| run B | 0 | **1** | **1** |
| v2 | 3 | **3** | **6** |

**"재시도"라고만 적는다. "폭주"가 아니다.** `a₁ + a₂ = 8,402`이고 두 항이 다 8,192 이하라는
데서 나오는 것은 **`a₁ ≥ 210`**뿐이고, `a₁ = 8,192`(상한까지 간 폭주)는 따라 나오지 않는다 —
1차가 4,000짜리 스키마 위반이고 2차가 4,402여도 같은 합이다. **실패로 끝난 3건은 폭주가
맞다** — `critic_failed`의 `error_detail`이 두 시도 다 `EOF while parsing an object at line
3993 / 4013 / 10624`로 잘렸다고 적었고 243초가 8,192 두 번과 맞는다. 회복한 3건은 그 근거가
없다. **가르는 값은 §8.4 회수 5번**(시도 단위 `wall_ms`·`error_detail`)이다.

**CLI 요약은 그것을 말하지 않는다.** run B의 stdout은 "완주"와 경고 3건뿐이고 재시도가
한 건 있었다는 사실이 어디에도 안 나온다. 이 결함이 §7.1의 D55다.

### 따라 나오지 않는 것

- **재시도 시도별 분할은 모른다.** 8,402가 `8192 + 210`인지 다른 조합인지는 시도 단위 기록이
  없어 못 가른다. 확정된 것은 "2회 이상"뿐이다.
- **8,192 초과 3건 중 하나는 최장 5건에 없다.** 125·126초는 두 건이고 셋째의 `wall_ms`는
  125초 이하다 — 그 호출의 1차 시도가 상한까지 갔는지는 미기재(§8.4 회수 5번).
- **prefix cache 40.5% → 50.7%를 run별로 못 가른다.** 같은 서버가 두 run을 연달아 받아
  누적값이 이어진다. 장부 `cached_tokens`와의 대조(D27)는 이번에 못 한다.

## 6. 실험 4 — 서버 직접 프로브 (하네스 밖)

### 목적

D51 (2)가 미룬 질문 하나 — **구조화 출력 백엔드가 스키마의 property 순서대로 생성하는가.**
`Finding` 스키마를 그대로 실어 `strict: true`로 1회 호출했다.

### 조건

하네스를 거치지 않는다. `max_tokens 1024`, user 메시지 한 줄, `response_format`에
`Finding.model_json_schema()`.

### 결과

```
스키마 선언 순서: ['finding_id', 'run_id', 'candidate_id', 'state', 'path', 'trigger_status', 'confidence', 'rca_draft', 'cwe', 'title', 'evidence_refs', 'missing_evidence', 'repro_spec', 'produced_by']
모델이 낸 순서 : ['finding_id', 'run_id', 'candidate_id', 'state', 'path', 'confidence', 'cwe', 'title', 'repro_spec', 'produced_by']
```

### 해석 ⑦ — 순서 위반 0건. 그러나 "강제"는 아직 아니다

낸 10개가 선언 14개의 **부분수열**이다(자리 1·2·3·4·5·7·9·10·13·14 — 단조 증가).
**순서를 어긴 자리가 없다.** D51 (2)가 "v2 프롬프트로 도는 첫 run이 답을 준다"고 적은
자리에 첫 관측이 생겼다.

**따라 나오지 않는 것: 백엔드가 강제한다는 것.** 스키마 순서를 그대로 따르는 모델이라면
강제가 없어도 같은 결과가 나온다. 가르려면 **순서를 뒤집은 스키마로 대조**해야 하고 그것은
안 했다. 표본도 1건이다. D51 (2)의 판단("맞춰 두는 것 자체는 어느 쪽이든 해롭지 않다")은
그대로 유지되고, 근거가 하나 붙는다.

### 해석 ⑧ — 스키마에 자리를 내는 것으로는 필드가 안 나온다

**안 나온 넷** — `trigger_status` · `rca_draft` · `evidence_refs` · `missing_evidence` —
이 [finding.py](../../../src/schemas/finding.py)에서 **전부 default를 가진 optional**이다.

```python
path: list[PathStep] = Field(default_factory=list)
trigger_status: str | None = None
confidence: float = Field(ge=0.0, le=1.0)
rca_draft: str = ""
evidence_refs: list[Identifier] = Field(default_factory=list)
missing_evidence: list[Identifier] = Field(default_factory=list)
repro_spec: ReproSpec | None = None
```

나온 10개는 **required 8개 전부 + optional 2개**(`path`·`repro_spec`)다. ⇒ `strict: true`가
required를 어긴 것이 아니다. **모델은 required만 확실히 채우고 optional은 프롬프트가
시켜야 낸다.**

이것이 실험 1의 음성 대조군이다. **스키마 자리는 필요조건이고 프롬프트가 충분조건이다** —
같은 서버·같은 스키마인데 v2 프롬프트가 붙은 run에서는 `trigger_status`가 6건 전부 찼다.

**그리고 §4로 이어진다.** 안 나온 넷 중 `evidence_refs`가 6단계 게이트의 `p1`을 정한다.
D23 때문에 `location_ok`가 구조적으로 거짓이라 **`p1 = static_ok`이고, 그 술어는 "참조한
근거 중 STATIC이 있는가"만 묻는다.** 프로브처럼 그 필드를 아예 안 내면 참조 집합이 비고
등급이 `E0`이다 — run B의 여섯 건이 전부 `E0`였던 것과 **모양이 같다.**

**같은 모양이 같은 원인은 아니다.** run B의 `evidence_refs`를 직접 보지 않았으므로, 그것이
비었는지 · 비-STATIC이었는지 · 미해결 id였는지는 §8.4 회수 7·8번이 가른다.

## 7. 이 run들이 연 것과 닫은 것

### 7.1 신설 세 건

| 번호 | 무엇 | 어느 지표가 어떻게 틀리나 | 근거 |
| --- | --- | --- | --- |
| **D53** | 실패한 `model_call`이 예산 장부에 한 푼도 안 실린다 | [evaluation.md](../../evaluation.md) **§5.2** — `input/output token`, `GPU-second`, "validated finding당 GPU-hour"의 **분자가 빈다**. v2에서 **wall-clock 729초**와 출력 토큰 최대 6×8,192가 장부 밖이다(243초의 출처는 `model_call.wall_ms`다. 장부가 받았을 값은 `result.gpu_ms`이고 `gpu_ms_source == "request_wall_clock"`이라 가깝지만 `wall_ms`가 `_record_prompt` **뒤**부터 재므로 조금 크다). **폭주가 잦은 조건일수록 싸 보인다** | `budget` 이벤트 수 = **ok** `model_call` + `tool_call` (38 = 25+13 / 41 = **28**+13, 31이 아니다) |
| **D54** | `config/models.yaml`의 어느 모델도 v2 프롬프트를 고르지 않아 머지된 v2가 도달 불가다 | 지표가 아니라 **도달성** 문제다. 기본 설정으로 도는 모든 run에서 `trigger_status`가 전량 `null`이고, 그것이 D52의 기제로 읽히지 않으면 "모델이 안 냈다"로 오독된다 | `config/models.yaml`의 `prompt_template_version`이 **전 항목 `"v1"`** |
| **D55** | 재시도가 `model_call` 한 건으로 뭉개져 스키마 파싱 실패율이 과소 집계된다 | [evaluation.md](../../evaluation.md) **§5.1** — "스키마 파싱 실패율"의 **분자가 준다**. `context.py`가 그 지표의 분모를 `model_call`로 잡아 뒀는데, 실패했다가 살아난 시도가 `ok=true` 한 건에 흡수된다 | run B가 `fail 0`인데 `output_tokens` 8,402 > `output_max` 8,192 (§5 해석 ⑥) |

> **D54는 "지금 `models.yaml`을 v2로 넘기라"는 지시가 아니다.** 2×2가 도는 중에 넘기면
> [evaluation.md](../../evaluation.md) §2.3 규칙 4로 **전량 재측정**이다. 이 항목은 **미결
> 판단이지 누락이 아니고**, 넘기는 시점은 2×2 전량 완료 후다(§8.2·§8.3 2번).

**D53과 D55는 같은 편향의 두 면이다.** `openai_compat.complete()`의 주석이 어댑터 **안에서**
없앤 것이 정확히 이 편향이다 — "마지막 시도만 세면 재시도가 잦은 모델일수록 비용이 싸
보인다. 조건 간 비교에서 정확히 거꾸로 가는 편향이다"(74~87행). 그 편향이 **한 겹 위에서**
두 번 되살아나 있다: 비용 쪽은 실패 호출이 통째로 빠지고(D53), 품질 쪽은 실패 시도가
성공 한 건에 흡수된다(D55).

**갈라서 등재하는 이유**는 고치는 자리와 읽는 사람이 다르기 때문이다. D53은 `context.py`의
장부 호출이고 비용표가 읽는다. D55는 `model_call` 이벤트의 시도 단위 기록이고 품질표가 읽는다.

#### D55의 사후 복원 규칙

**시도 하나가 요청 하나다** — `complete()`의 재시도 루프가 매 시도마다 `_post()`를 부르고
그것이 `/v1/chat/completions`로 가는 POST 하나다([openai_compat.py](../../../src/adapters/openai_compat.py) 90~94·165~192행).
그래서:

```
서빙 stdout의 POST /v1/chat/completions 수  −  trace의 model_call 수  =  재시도 수
```

**서빙 서버 stdout을 파일로 받고 있는 run은 사후 복원이 된다. 아니면 그 run의 실패율은
못 쓴다.** 이 두 run은 로그를 받았으므로 복원 가능하다 — 검증은 다음 접속 때 한다
(§8.4 회수 13번). 지금 도는 2×2에는 이것이 **즉시** 걸린다(§8.3 1번).

### 7.2 갱신·재확인

| 번호 | 무엇이 달라졌나 |
| --- | --- |
| D46 | **재확인 + 새 사실.** `disable_any_whitespace=False`인 서버에서 같은 모양이 또 났고(EOF at line 3993·4013·10624), **`fail 0`인 run에도 재시도가 있다**는 것이 처음 관측됐다(run B의 8,402 — 그 재시도가 폭주였는지는 미기재). 시간 비중 실측은 §5 |
| D49 | **절박함 내려간다.** D49 자신이 "D47이 (a)로 닫히면 이 항목의 절박함이 내려간다"고 적어 뒀고 그 조건이 충족됐다. **닫지 않는다** — 재시도 본문은 여전히 산출물에 안 남고, D55의 복원 규칙은 서버 로그가 있을 때만 성립한다 |
| D50 | **이 세션에서 세 번 더 났다 — `doctor` · run B · v2.** 셋 다 `프로필과 다른 도구 런타임으로 돌았다: docker`인데 `b1_single`의 선언값도 `docker`다. 등재 근거였던 `run_20260828_0002`까지 **네 번**이고 네 번 다 거짓이다. **이 관측은 항목을 새로 열지 않는다 — PR #55가 이미 (a)로 고치고 있고**(`_tool_runner_overrides` 술어로 선언값과 비교), 이 세 건은 그 PR의 근거를 보탠다. 그래서 이 보고서는 `docs/open-questions.md`의 D50 행을 건드리지 않는다 — 그 행은 #55가 닫는다 |
| D51 | **(2)에 첫 관측 근거**(§6 해석 ⑦). **(1)은 유지** — 분포가 `triggerable` 1종 ×6이라 좁힐 근거가 안 된다 |
| D48 | **검증 안 됐다.** `target_skipped`가 양쪽 **0건**이라 PR #50이 고친 코드 경로가 한 번도 안 돌았다. 회귀 테스트는 있으나 실서빙 관측은 아직 0건이다 |

### 7.3 닫는 것 둘

| 번호 | 근거 |
| --- | --- |
| D47 | `origin/main`의 `openai_compat.py` 122행이 `include_input=False`를 주고, 두 run 합쳐 `model_call` **56건에 400 흔적 0건**이다. run A는 실패 5건 중 4건이 400이었다 |
| D52 | §3. **닫히는 것은 "저장소에 프롬프트가 없다"이고, 기본 설정에서는 여전히 전량 `null`이다** — 그 잔여가 D54다 |

### 7.4 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| `setsid nohup ... &`가 곧바로 "죽었다"를 냈다 | `$!`가 잡은 것은 `setsid`의 PID(3663636)이고 실제 서버는 그 자식(3663640)이다. `setsid`가 즉시 끝나므로 `kill -0`이 실패한다 | `pgrep -af "vllm serve"`로 실 PID를 다시 잡아 `~/vllm_runB.pid`에 덮어썼다. **서버는 죽지 않았다** — 대기 루프의 판정만 틀렸다 |
| `tmux new -s runB`가 세션을 못 만들고 창이 끊겼다 | 미확정. `pgrep -af tmux` 결과 없음, 소켓 디렉터리는 `default`만 있었다 | tmux를 쓰지 않고 진행했다. keyring은 전후 `376 376/376`으로 같아 키 고갈은 아니다 |

### 7.5 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| `gate_eval`의 `predicates`·`grade` | 집계 스크립트가 `kind`만 셌다 | §8.4 회수 1·2번 |
| `input_tokens` · `cached_tokens` · `gpu_ms` | 상동 | §8.4 회수 3번 |
| run별 prefix cache 적중률 | 같은 서버가 두 run을 연달아 받아 누적값이 이어진다 | 서버 로그의 run 경계 시각 값 — §8.4 회수 10번 |
| `seed` · `source_hash` · `sampling` · `repo_dirty` · `stopped_after`(v2) | `run_manifest.json`을 열지 않았다 | §8.4 회수 6번 |
| PWN-003 정답 대조 | 산출물을 다시 열지 않았다 | `report.md` + 정답지 대조 |
| `tool_images` 전체 sha256 | 세션이 `docker images`의 단축 ID만 받았다 | `docker inspect` 로 이미지 ID 전체를 읽는다 |

### 7.6 반증 조건

- **실험 1이 실패했다면** — v2에서도 `trigger_status`가 `null`이었을 것이고, 그러면 원인이
  프롬프트가 아니라 모델이거나 파싱 경로다. `triggerable` ×6이 나왔으므로 **프롬프트 부재가
  원인이라는 D52의 진단이 맞았다.** 반대로 run B에서 값이 찼다면 D52가 틀린 것이었다 —
  안 찼다.
- **실험 2가 실패했다면** — v2에서도 critic 호출이 0건이었을 것이고, 그러면 6단계 게이트가
  프롬프트와 무관하게 막혀 있다는 뜻이다. 6건이 올라갔으므로 **그 벽은 프롬프트로 움직인다.**
- **실험 3이 실패했다면** — 400이 다시 났을 것이고 PR #49가 안 들은 것이다. 0건이므로
  **D47은 닫힌다.** 또 폭주 시간 비중이 낮게 나왔다면 PR #51의 전제가 run A 특유였다는
  뜻이다 — 60.7%가 나왔으므로 **아니다.**
- **실험 4가 실패했다면**(모델이 순서를 어겼다면) — `prompts/auditor/v2` prefix 90~105행이
  요구하는 순서를 스키마로는 못 지킨다는 뜻이고, 순서 재배치(D51 (2), PR #53)가 무효가 된다.
  위반 0건이므로 **무효는 아니다.** 다만 유효라는 증명도 아니다(해석 ⑦).

## 8. 후속

### 8.1 이 보고서가 코드를 안 고치는 이유

**팀 2×2가 도는 중이다.** 셀이 진행되는 동안 하네스를 바꾸면 셀마다 다른 코드로 돈 run이
한 매트릭스에 섞이고, 그것은 [evaluation.md](../../evaluation.md) §2.3 규칙 2·4가 막는 것이다.
PR #51을 지금 머지하지 말라는 것(§8.3 3번)과 **같은 근거**다.

그래서 이 변경은 문서만 바꾼다. 코드 변경은 아래 큐로 넘긴다.

### 8.2 후속 과제 — 2×2 전량 완료 직후 큐

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | **D53** — `context.call_model`의 실패 경로가 예산 장부를 청구하게 한다 | **2×2 완료 대기.** 비용표의 분자가 걸린 값이라 매트릭스 중간에 바뀌면 셀 간 비교가 깨진다 |
| 2 | **D55** — `model_call`에 시도 수를 싣거나 시도마다 이벤트를 낸다 | **2×2 완료 대기.** 상동. 그때까지는 §7.1의 서버 로그 복원 규칙으로 사후 집계한다 |
| 3 | **D54** — v2를 쓸 모델·프로필을 정한다 | **2×2 완료 대기.** §8.3 2번 참조. **미결 판단이지 누락이 아니다** |
| 4 | PR #51(`output_max` 4,096) 머지 | **2×2 완료 대기.** 근거는 §5로 강화됐다 |
| 5 | D48 실서빙 관측 | `target_skipped`가 나는 run을 기다린다. 이번 두 run은 0건이다 |

### 8.3 2×2와 부딪히는 지점 — 알릴 것

**직접 손대지 않는다. 판단과 권한은 실험 담당·PM에게 있다.**

> **이 목록 위에 하나가 더 있고 그것은 이 보고서의 관측이 아니다.** PR #56이 모델
> `generation_config.json`의 `top_k`가 **모델마다 다르다**는 것을 실측했다 —
> `Qwen3-8B`는 20, `Foundation-Sec`·`granite`는 미선언. 그 셋이 정확히 2×2의 B1′ 세 셀이고
> `b3prime_balanced`가 셋을 섞으므로, **"모델 혼합 효과"가 샘플링 차이와 교락돼 있고 그 값이
> `RunRecord` 어디에도 안 남는다.** 아래 다섯보다 무겁다 — 아래는 "다음 run에 걸린다"인데
> 그쪽은 **이미 돌아간 셀이 해석 불가**라는 뜻이다. **정본은 #56이고 여기서 요약하지 않는다.**

1. **[즉시·최우선] 지금 도는 2×2가 서빙 서버 stdout을 파일로 받고 있는가.**
   §7.1의 복원 규칙이 그 로그에 걸려 있다 — 없으면 **그 2×2의 스키마 파싱 실패율은 사후에도
   못 만든다**(D55). [README.md](../README.md)의 「서빙 서버 stdout을 파일로 받는다」가 이미
   `setsid nohup ... > "$LOG" 2>&1 &`를 규약으로 정해 뒀고, 같은 사고가 세 번 있었다.
   **아직 안 받고 있으면 다음 셀 기동 전에 고쳐야 하므로 다른 넷보다 먼저다.**

2. **[즉시] 그 2×2는 v1 프롬프트로 돈다.** `config/models.yaml`의 `prompt_template_version`이
   **전 항목 `"v1"`**이다. PR #54가 넣은 v2 셋을 **고르는 모델이 하나도 없다**(D54).
   ⇒ 그 매트릭스의 `trigger_status`는 **전량 `null`**이고, 그것은 모델의 판단이 아니라
   프롬프트 부재다(§3에서 run B가 실측). **2×2에서 그 필드를 지표로 쓸 계획이면 지금 정해야
   한다.** 다만 **매트릭스가 도는 중에 v2로 넘기면 안 된다** — [evaluation.md](../../evaluation.md)
   §2.3 규칙 4로 전량 재측정이다.

3. **[즉시] PR #51을 2×2 도는 중에 머지하지 않는다.** `output_max`는 `RunRecord.thresholds`에
   실리는 계산 예산이고, 셀마다 다른 상한이면 [evaluation.md](../../evaluation.md) §2.3 규칙 1
   (동일 계산 예산)이 깨진다. 머지 시점은 **전량 완료 후 또는 다음 매트릭스 착수 전**.

4. **[주의] v1으로 도는 셀에서 critic이 아예 안 돌 수 있다.** run B가 Finding 6건 전부
   `E0`로 6단계에서 멈췄다(§4) — `model_call` 25건에 `role=critic`이 0건이다. B3′는 critic이
   조건의 핵심인데 같은 일이 나면 "역할 분담 효과"의 분모가 0이 된다.
   ⇒ **첫 셀의 `model_call`에서 `role=critic` 건수를 먼저 확인**하기를 권한다. 대상이 다르면
   결과도 다를 수 있다 — 이 관측은 PWN-003 한 건이다.

5. **[참고] PR #51의 근거가 강해졌다.** D47이 닫힌 뒤에도 폭주가 v2 run 시간의 **≥60.7%를
   순손실로** 먹었다(§5). 2×2가 나중에 v2로 돌 계획이라면 상한 결정이 비용에 직접 걸린다.

### 8.4 다음 접속 때 회수할 값

**어느 파일의 어느 키인지까지 적는다.** 경로는 `/srv/tenants/user/artifacts/` 아래다.

| # | 파일 | 키 | 무엇을 가르나 |
| --- | --- | --- | --- |
| 1 | `run_20260829_0001/trace.jsonl` | `kind=gate_eval` 6건의 `data.grade` · `data.predicates.p1` · `static_ok` · `location_ok` · `path_ok` · `graph_ok` · `unblocked` | `E0`의 원인 술어. §4 해석 ②가 남긴 유일한 빈칸 |
| 2 | `run_20260829_0002/trace.jsonl` | 같은 키 9건 — `stage`로 6단계 6건과 7단계 3건을 가른 뒤 | 무엇이 등급을 `E1` 이상으로 올렸나 |
| 3 | 양쪽 `trace.jsonl` | `kind=model_call` · `ok=true`의 `data.input_tokens` · `cached_tokens` · `gpu_ms` 합과 `role`별 분해 | D27 — 장부 `cached_tokens`와 서버 `Prefix cache hit rate`의 대조 |
| 4 | 양쪽 `trace.jsonl` | `kind=model_call` · `role=auditor`의 `data.output_tokens` 개별 6건 | 역할별 상한(D46 (c))의 판단 입력. 지금 값(224 / 487)은 역할이 섞여 있다 |
| 5 | `run_20260829_0002/trace.jsonl` | `kind=model_call` · `ok=false` 3건의 `data.wall_ms` · `data.error_detail` 전문 | 243초 3건이 그 셋인지 확정. §5 해석 ④가 속도로 맞춘 것을 직접 확인 |
| 6 | 양쪽 `run_manifest.json` | `thresholds.output_max` · `candidate_max` · `critic_top_n` · `schema_retries` · `seed` · `source_hash` · `repo_dirty` · `tool_images` · `prompt_versions` · `kb_snapshot` · `stopped_after` · `condition` · `device` · `tool_runner` | 이 보고서 메타의 `미기재` 칸 전부 |
| 7 | `run_20260829_0001/state.db` | Finding 6건의 `evidence_refs` 길이와 `path`의 `step` 집합 | §6 해석 ⑧의 추정(`evidence_refs`가 비어 `E0`)을 직접 확인 |
| 8 | `run_20260829_0002/state.db` | 같은 두 키 | 위의 짝. v2에서 무엇이 채워졌나 |
| 9 | 양쪽 `report.md` | Finding 표의 `state` 열 | v2에서 critic을 통과한 3건이 `validated`로 올라갔는지 |
| 10 | `run_20260829_0001/serve_30b_runB_20260829_173017.log` | `Prefix cache hit rate` 전 시계열, 특히 **17:39 전후** | 두 run으로 가른 적중률. 지금은 40.5% → 50.7% 누적값뿐이다 |
| 11 | 양쪽 run의 `prompts/` | `NNNN_scout.md` · `NNNN_auditor.md`의 바이트 수와 `model_call`의 `prompt_sha256` | v1/v2 프롬프트 크기 차 — `input_tokens` 차의 상한 |
| 12 | `run_20260829_0002/trace.jsonl` | `kind=budget` 마지막 건의 `data.output_tokens` 누계 | **D53의 정량.** ok 28건의 `output_tokens` 합과 같은지, 실패 3건이 빠진 만큼이 얼마인지 |
| 13 | `serve_30b_runB_20260829_173017.log` | `"POST /v1/chat/completions"` 줄 수 (run 경계 시각으로 분할) | **D55의 복원 규칙 검증.** run B는 25 + 1 = **26**, v2는 31 + 6 = **37**이 예상값이다(재시도 6건 = 회복 3 + 실패 3). **서버 직접 프로브가 같은 로그에 POST 1건을 더 남기므로 전체는 64여야 하고 마지막 1건이 그것이다** — run 경계 시각으로 자를 때 빼먹지 않는다. 맞으면 규칙이 성립하고, 안 맞으면 그 차이가 새 사실이다 |
| 14 | `run_20260829_0001/run_manifest.json` · `cli_stdout_runB.log` | `condition` · `tool_runner` · `device` · `target`, 그리고 stdout 589 B 전문 | **run B의 명령 원문이 세션 로그에 없다**(§9). 재구성하지 않고 이 둘로 확정한다 |

### 8.5 정리 상태

- **서빙 서버**: 종료함. `kill "$(cat ~/vllm_runB.pid)"` 후 `pgrep -af "vllm serve"` 무출력
- **임시 설정·프로필**: 원복함. 측정 브랜치 `measure/v2-smoke-20260829`(`e111eb8`)는 **머지 대상이 아니고** 저장소는 `main`으로 되돌렸다
- **저장소 변경**: 없음 (측정 브랜치는 서버 로컬에만 있다)
- **산출물**: 두 run 디렉터리 보존, 서버 로그 사본 양쪽에 복사

## 9. 부록 — 재현 절차

**세션 원문이다. 명령은 한 글자도 줄이지 않았다.** 줄인 명령은 재현되지 않고 무엇을
줄였는지도 남지 않는다.

**손댄 곳은 둘이고 여기 적는다.**

1. heredoc 구분자를 `EOF` → `PYEOF`로 바꿨다. 이 파일이 마크다운이라 `EOF`를 그대로 두면
   코드 블록이 그 줄에서 끊긴다. **명령의 뜻은 안 바뀐다**
2. 아주 긴 출력 세 덩이를 `…생략…`으로 **표시하고** 접었다. **명령과 요약 줄은 그대로 있다**
   - `git pull`의 Fast-forward 파일 목록 83건 + `create mode` 목록 (`83 files changed, …`는 남겼다)
   - vLLM `EngineCore` 설정 한 줄의 `compilation_config`·`kernel_config` 딕셔너리(약 2,800자).
     **이 보고서가 인용하는 `structured_outputs_config`는 그 줄에 그대로 남겼다**
   - 같은 블록에서 스냅샷 절대경로가 세 번 반복되는 자리를 `...snapshots/<40 hex>`로 줄였다

그 밖에는 출력 한 줄도 빼지 않았다.

> ⚠ **이 절차를 다시 실행하지 않는다.** 장비를 다른 사람이 쓰고 있고, 팀 2×2가 진행 중이다.
> 재실행이 필요하면 §8.3의 충돌 항목을 먼저 해소한다.

```
PS C:\Windows\System32> ssh atom
Welcome to NVIDIA DGX Spark Version 7.4.0 (GNU/Linux 6.11.0-1016-nvidia aarch64)

 System information as of 2026. 08. 29. (토) 17:26:18 KST

  System load:  0.11                Temperature:             46.0 C
  Usage of /:   34.2% of 467.89GB   Processes:               779
  Memory usage: 5%                  Users logged in:         1
  Swap usage:   4%                  IPv4 address for wlP9s9: REDACTED

Last login: Sat Aug 29 12:02:44 2026 from REDACTED
user@atom-host:~$ echo "== host"; hostname; date; uptime
echo "== 메모리(cgroup)"; cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current 2>/dev/null; free -g | head -2
echo "== 디스크"; df -h /srv/tenants/user | tail -1
== host
atom-host
2026. 08. 29. (토) 17:26:51 KST
 17:26:51 up 61 days,  2:45, 5757 users,  load average: 0.13, 0.28, 0.28
== 메모리(cgroup)
               총계         사용        여분      공유    버퍼/캐시    가용
메모리:          119           7          82           0          31         112
== 디스크
/dev/nvme0n1p3 1007G  186G  812G   19% /srv/tenants
user@atom-host:~$ echo "== 떠 있는 vllm"; pgrep -af "vllm serve" || echo "없음"
echo "== 열린 포트"; ss -ltnp 2>/dev/null | grep -E ':80(0[0-9]|1[0-9])' || echo "8000번대 없음"
echo "== GPU"; nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv 2>/dev/null; nvidia-smi | tail -12
== 떠 있는 vllm
없음
== 열린 포트
8000번대 없음
== GPU
utilization.gpu [%], memory.used [MiB]
0 %, [N/A]
|   0  NVIDIA GB10                    On  |   0000000F:01:00.0 Off |                  N/A |
| N/A   43C    P8              4W /  N/A  | Not Supported          |      0%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|  No running processes found                                                             |
+-----------------------------------------------------------------------------------------+
user@atom-host:~$ cd ~/whitehat-boss && git fetch origin
echo "== 현재"; git rev-parse --abbrev-ref HEAD; git log --oneline -1; git status --porcelain | head
echo "== origin/main"; git log --oneline -1 origin/main          # 1f4c4da 여야 한다
echo "== 뒤처짐(왼쪽=로컬 앞, 오른쪽=원격 앞)"; git rev-list --left-right --count HEAD...origin/main
echo "== 여섯 건"; git log --oneline --merges -15 origin/main | grep -E "#(52|49|50|53|54|40)"
remote: Enumerating objects: 37, done.
remote: Counting objects: 100% (36/36), done.
remote: Compressing objects: 100% (24/24), done.
remote: Total 37 (delta 22), reused 12 (delta 12), pack-reused 1 (from 1)
오브젝트 묶음 푸는 중: 100% (37/37), 26.86 KiB | 146.00 KiB/s, 완료.
github.com:0xM1yu/whitehat-boss URL에서
   93b06e6..880ce60  feat/trigger-status-and-produced-by -> origin/feat/trigger-status-and-produced-by
   44e5999..e4dc43a  fix/d42-output-max-4096 -> origin/fix/d42-output-max-4096
   f1efce7..1f4c4da  main       -> origin/main
== 현재
measure/build-wiring-542
b29e262 (HEAD -> measure/build-wiring-542) chore(threshold): 측정 전용 — candidate_max 256 → 1024 (build 배선 재측정)
== origin/main
1f4c4da (origin/main, origin/HEAD) Merge pull request #40 from 0xM1yu/docs/2026-08-28-tokenizer-prove
== 뒤처짐(왼쪽=로컬 앞, 오른쪽=원격 앞)
1       102
== 여섯 건
1f4c4da Merge pull request #40 from 0xM1yu/docs/2026-08-28-tokenizer-prove
41ac915 Merge pull request #54 from 0xM1yu/feat/prompts-v2-role-split
7926ffe Merge pull request #53 from 0xM1yu/feat/trigger-status-and-produced-by
b0477fb Merge pull request #50 from 0xM1yu/fix/d48-skipped-stage
e0154b5 Merge pull request #49 from 0xM1yu/fix/d47-retry-input-echo
0aba8ab Merge pull request #52 from 0xM1yu/docs/runa-report-corrections
user@atom-host:~/whitehat-boss$ echo "== output_max"; git show origin/main:config/budgets.yaml | grep -E '^\s+output_max'      # 8192 기대(#51 미머지)
echo "== v2 프롬프트"; git ls-tree --name-only origin/main -- prompts/scout/v2 prompts/auditor/v2 prompts/critic/v2
echo "== trigger_status"; git show origin/main:src/schemas/finding.py | grep -n "trigger_status:"
echo "== D47 수정"; git show origin/main:src/adapters/openai_compat.py | grep -n "include_input"
== output_max
  output_max: 8192
== v2 프롬프트
prompts/auditor/v2
prompts/critic/v2
prompts/scout/v2
== trigger_status
111:    trigger_status: str | None = None
== D47 수정
102:                # **`include_input=False`가 그 두 줄을 실행하는 자리다 (D47).** pydantic의
122:                    f"{exc.errors(include_url=False, include_input=False)}"
user@atom-host:~/whitehat-boss$ DS=/srv/tenants/user/experiments/2x2/dataset/track_a_evaluation_release_v1
SNAP=/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048
echo "== 대상"; ls -d "$DS/blind/PWN-003/source" && ls "$DS/blind/PWN-003/source" | head
echo "== 스냅샷"; ls -d "$SNAP" && ls "$SNAP" | head -5
echo "== artifacts"; ls -d /srv/tenants/user/artifacts /srv/tenants/user/artifacts/logs
== 대상
/srv/tenants/user/experiments/2x2/dataset/track_a_evaluation_release_v1/blind/PWN-003/source
Makefile
channel_store.c
record_format.h
record_reader.c
router_main.c
== 스냅샷
/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048
README.md
added_tokens.json
chat_template.jinja
config.json
generation_config.json
== artifacts
/srv/tenants/user/artifacts  /srv/tenants/user/artifacts/logs
user@atom-host:~/whitehat-boss$ # --- 건너뛴 셋 ---
echo "== keyring"; grep "^ *$(id -u):" /proc/key-users || cat /proc/key-users
echo "== docker"; docker --version; docker images | grep harness-tools
echo "== venv/vllm"; source /srv/tenants/user/venv-probe/bin/activate && which vllm python3 && vllm --version

# --- 머지 안 된 커밋이 있는가 ---
cd ~/whitehat-boss
for B in feat/trigger-status-and-produced-by feat/prompts-v2-role-split fix/d47-retry-input-echo fix/d48-skipped-stage; do
  git merge-base --is-ancestor origin/$B origin/main && echo "OK   $B" || { echo "⚠ 미머지 $B:"; git log --oneline origin/main..origin/$B; }
done
== keyring
 1004:   376 376/376 376/5000 1902/500000
== docker
Docker version 29.2.1, build a5c7197
WARNING: This output is designed for human readability. For machine-readable output, please use --format.
harness-tools:v1                              b5d7048bc0a5       1.11GB          249MB
== venv/vllm
/srv/tenants/user/venv-probe/bin/vllm
/srv/tenants/user/venv-probe/bin/python3
0.27.1
OK   feat/trigger-status-and-produced-by
OK   feat/prompts-v2-role-split
OK   fix/d47-retry-input-echo
OK   fix/d48-skipped-stage
(venv-probe) user@atom-host:~/whitehat-boss$ cd ~/whitehat-boss && git checkout main && git pull
'main' 브랜치로 전환합니다
브랜치가 'origin/main'보다 128개 커밋 뒤에 있고, 앞으로 돌릴 수 있습니다.
  (로컬 브랜치를 업데이트하려면 "git pull"을 사용하십시오)
업데이트 중 54c8070..1f4c4da
Fast-forward
 …생략: 변경 파일 83건 목록 + create mode 목록…
 83 files changed, 10318 insertions(+), 305 deletions(-)
(venv-probe) user@atom-host:~/whitehat-boss$ git rev-parse HEAD
1f4c4da63b40fc17499944c3b49513742af7f0d7
(venv-probe) user@atom-host:~/whitehat-boss$ git status --porcelain
(venv-probe) user@atom-host:~/whitehat-boss$ grep -E "candidate_max|critic_top_n|schema_retries|output_max" config/budgets.yaml
  critic_top_n: 8
  # 상한은 아래 `candidate_max`가 따로 든다 — 이 키의 뜻을 조용히 넓히지 않는다.
  # `over_candidate_max`로 남기고, 건수는 run 경고와 report.md에도 올린다 — 안 만들어진
  candidate_max: 256
  schema_retries: 1
  # `schema_retries`가 이미 같은 이유로 여기 있다. **`Sampling`도 검토했다** — 그쪽 docstring이
  output_max: 8192
    # 들어가야 하는데 — (32768 − output_max) / 1.798 / 2 — 그렇게 잡아도 원문이 안 묶이므로
    # **정정(2026-08-28): 이 식이 쓰던 `output_max 2000`은 하네스에 없던 값이었다.**
    # 2,000은 `docs/agent-contracts.md` §1의 실행 계약 **예시**(`token_budget.output_max`)이고
    # 2,000이 아니라 `--max-model-len`이었다. 지금은 위 `thresholds.output_max: 8192`가
(venv-probe) user@atom-host:~/whitehat-boss$ source /srv/tenants/user/venv-probe/bin/activate
SNAP=/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048
SERVE_LOG=/srv/tenants/user/artifacts/logs/serve_30b_runB_$(date +%Y%m%d_%H%M%S).log

setsid nohup env VLLM_USE_DEEP_GEMM=0 VLLM_HOST_IP=127.0.0.1 \
  NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
  "$(which vllm)" serve "$SNAP" \
    --served-model-name qwen3-coder-30b-a3b \
    --host 127.0.0.1 --port 8002 \
    --gpu-memory-utilization 0.28 \
    --max-model-len 32768 \
    --enable-prefix-caching \
  > "$SERVE_LOG" 2>&1 &
VLLM_PID=$!; echo $VLLM_PID > ~/vllm_runB.pid; echo "pid=$VLLM_PID  log=$SERVE_LOG"

for i in $(seq 1 40); do
  curl -s http://127.0.0.1:8002/v1/models >/dev/null 2>&1 && { echo " 떴다"; break; }
  kill -0 "$VLLM_PID" 2>/dev/null || { echo " 죽었다"; tail -30 "$SERVE_LOG"; break; }
  sleep 15; printf .
done
[1] 3663636
pid=3663636  log=/srv/tenants/user/artifacts/logs/serve_30b_runB_20260829_173017.log
[1]+  완료                  setsid nohup env VLLM_USE_DEEP_GEMM=0 VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo "$(which vllm)" serve "$SNAP" --served-model-name qwen3-coder-30b-a3b --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.28 --max-model-len 32768 --enable-prefix-caching > "$SERVE_LOG" 2>&1
 죽었다
nohup: 입력 무시
(venv-probe) user@atom-host:~/whitehat-boss$ pgrep -af "vllm serve" || echo "== 정말 없다"
tail -20 "$SERVE_LOG"
3663640 /srv/tenants/user/venv-probe/bin/python3 /srv/tenants/user/venv-probe/bin/vllm serve /srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048 --served-model-name qwen3-coder-30b-a3b --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.28 --max-model-len 32768 --enable-prefix-caching
(APIServer pid=3663640) INFO 08-29 17:30:22 [api_utils.py:345]
(APIServer pid=3663640) INFO 08-29 17:30:22 [api_utils.py:273] non-default args: {'model_tag': '...snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048', 'host': '127.0.0.1', 'port': 8002, 'model': '...snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048', 'max_model_len': 32768, 'served_model_name': ['qwen3-coder-30b-a3b'], 'gpu_memory_utilization': 0.28, 'enable_prefix_caching': True}
(APIServer pid=3663640) INFO 08-29 17:30:22 [model.py:645] Resolved architecture: Qwen3MoeForCausalLM
(APIServer pid=3663640) INFO 08-29 17:30:22 [model.py:1883] Using max model len 32768
(APIServer pid=3663640) INFO 08-29 17:30:23 [cache.py:296] Using fp8_e4m3 data type to store kv cache. It reduces the GPU memory footprint and boosts the performance. Meanwhile, it may cause accuracy drop without a proper scaling factor
(APIServer pid=3663640) WARNING 08-29 17:30:23 [modelopt.py:1021] Detected ModelOpt NVFP4 checkpoint (quant_algo=NVFP4). Please note that the format is experimental and could change in future.
(APIServer pid=3663640) INFO 08-29 17:30:23 [kernel.py:306] Final IR op priority after setting platform defaults: IrOpPriorityConfig(rms_norm=['native'], fused_add_rms_norm=['native'])
(APIServer pid=3663640) INFO 08-29 17:30:23 [compilation.py:329] Enabled custom fusions: act_quant
(EngineCore pid=3664171) INFO 08-29 17:30:30 [core.py:121] Initializing a V1 LLM engine (v0.27.1) with config: model='...3b554c28e968128990ef4ac2d25ca757bf4ce048', speculative_config=None, ... dtype=torch.bfloat16, max_seq_len=32768, ... quantization=modelopt_fp4, ... kv_cache_dtype=fp8_e4m3, device_config=cuda, structured_outputs_config=StructuredOutputsConfig(backend='auto', disable_any_whitespace=False, disable_additional_properties=False, reasoning_parser='', reasoning_parser_plugin='', enable_in_reasoning=False), ... seed=0, served_model_name=qwen3-coder-30b-a3b, enable_prefix_caching=True, enable_chunked_prefill=True, ...
   ↑ …생략: 이 한 줄의 `compilation_config`·`kernel_config` 딕셔너리(약 2,800자)…
(EngineCore pid=3664171) INFO 08-29 17:30:30 [parallel_state.py:1640] world_size=1 rank=0 local_rank=0 distributed_init_method=tcp://127.0.0.1:49815 backend=nccl
(EngineCore pid=3664171) INFO 08-29 17:30:30 [parallel_state.py:1977] rank 0 in world size 1 is assigned as DP rank 0, PP rank 0, PCP rank 0, TP rank 0, EP rank 0, EPLB rank N/A
(EngineCore pid=3664171) INFO 08-29 17:30:31 [topk_topp_sampler.py:62] Using FlashInfer for top-p & top-k sampling.
(EngineCore pid=3664171) INFO 08-29 17:30:31 [gpu_model_runner.py:5308] Starting to load model /srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048...
(EngineCore pid=3664171) INFO 08-29 17:30:31 [__init__.py:1077] Using FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM
(EngineCore pid=3664171) INFO 08-29 17:30:31 [cuda.py:482] Using FLASHINFER attention backend out of potential backends: ['FLASHINFER', 'TRITON_ATTN'].
(EngineCore pid=3664171) INFO 08-29 17:30:31 [nvfp4.py:285] Using 'FLASHINFER_CUTLASS' NvFp4 MoE backend out of potential backends: ['FLASHINFER_TRTLLM', 'FLASHINFER_CUTEDSL', 'FLASHINFER_CUTEDSL_BATCHED', 'FLASHINFER_CUTLASS', 'VLLM_CUTLASS', 'MARLIN', 'HUMMING', 'EMULATION'].
(EngineCore pid=3664171) INFO 08-29 17:30:32 [weight_utils.py:867] Filesystem type for checkpoints: EXT4. Checkpoint size: 16.85 GiB. Available RAM: 92.41 GiB.
(EngineCore pid=3664171) INFO 08-29 17:30:32 [weight_utils.py:890] Auto-prefetch is disabled because the filesystem (EXT4) is not a recognized network FS (NFS/Lustre). If you want to force prefetching, start vLLM with --safetensors-load-strategy=prefetch.
Loading safetensors checkpoint shards:   0% Completed | 0/4 [00:00<?, ?it/s]
Loading safetensors checkpoint shards:  25% Completed | 1/4 [00:38<01:54, 38.32s/it]
(venv-probe) user@atom-host:~/whitehat-boss$ echo 3663640 > ~/vllm_runB.pid
VLLM_PID=3663640
for i in $(seq 1 60); do
  curl -s http://127.0.0.1:8002/v1/models >/dev/null 2>&1 && { echo " 떴다"; break; }
  kill -0 "$VLLM_PID" 2>/dev/null || { echo " 프로세스가 사라졌다"; tail -40 "$SERVE_LOG"; break; }
  sleep 15; printf .
done
.... 떴다
(venv-probe) user@atom-host:~/whitehat-boss$ # ① 다른 포트 전부 — 보고서 필수 칸
ss -ltnp | grep -E ':800[0-9]'
# ② 서빙 모델이 진짜 그 스냅샷인가 (root가 40 hex 경로여야 한다)
curl -s 127.0.0.1:8002/v1/models | python3 -m json.tool | grep -E '"(id|root)"'
# ③ 구조화 출력 설정 원문 — 보고서에 그대로 옮긴다
grep -o "structured_outputs_config=[^,]*, disable_any_whitespace=[A-Za-z]*" "$SERVE_LOG" | head -1
# ④ keyring 재확인 (기동이 키를 먹는다)
grep "^ *$(id -u):" /proc/key-users
# ⑤ doctor
python3 -m src.cli doctor --profile b1_single --device atom --tool-runner docker \
  --artifacts-root /srv/tenants/user/artifacts
LISTEN 0      2048                     127.0.0.1:8002       0.0.0.0:*    users:(("vllm",pid=3663640,fd=43))
            "id": "qwen3-coder-30b-a3b",
            "root": "/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048",
                    "id": "modelperm-b6766aaa88d315b2",
structured_outputs_config=StructuredOutputsConfig(backend='auto', disable_any_whitespace=False
 1004:   376 376/376 376/5000 1902/500000
[OK   ] config 3종 파싱 성공 (프로필 'b1_single')
[OK   ] 역할 5개가 모두 registry 키로 해석됐다: ['auditor', 'critic', 'embedding', 'reranker', 'scout']
[OK   ] retriever    → FakeRetriever
[OK   ] tool_runner  → ContainerToolRunner
[OK   ] state_store  → SqliteStateStore
[OK   ] scout        → OpenAICompatModelClient   (http://127.0.0.1:8002/v1)
[OK   ] auditor      → OpenAICompatModelClient   (http://127.0.0.1:8002/v1)
[OK   ] critic       → OpenAICompatModelClient   (http://127.0.0.1:8002/v1)
[WARN ] --tool-runner docker 오버라이드가 걸렸다 — 프로필에 적힌 런타임 대신 이 값을 검사한다. 공정성 규칙의 예외이며 run에서는 RunRecord.tool_runner에 남는다 (ADR 0005)
[OK   ] 도구 런타임 docker 확인: /usr/bin/docker
[OK   ] 장비 라벨 'atom' — run 기록과 --apply에 이 값이 남는다
[WARN ] benchmark 미측정 모델: ['qwen3-embedding-0.6b', 'qwen3-reranker-0.6b'] (dev 프로필이라 허용)
[WARN ] benchmark가 일부만 채워진 모델: qwen3-coder-30b-a3b(mem_gb_ctx8k 미측정) — 이 상태로는 그 항목을 뺀 비교만 성립한다 (dev 프로필이라 허용)
[WARN ] model checksum이 자리표시자(전부 0)다: embedding(qwen3-embedding-0.6b, source=미상), reranker(qwen3-reranker-0.6b, source=미상) — 이대로 run하면 어느 모델로 돈 기록인지 지시하지 못해 모델 비교표에서 제외된다 (산출 절차는 model-serving.md §5)
[OK   ] scout 프롬프트 v1 확인
[OK   ] auditor 프롬프트 v1 확인
[OK   ] critic 프롬프트 v1 확인
[WARN ] 실측 전 초기값 임계값: ['scout_threshold', 'capsule_expansion_max', 'requeue_max'] — 조건 간 비교 전에 확정한다 (open-questions D2·D3)
[OK   ] state DB 경로가 저장소 밖이다: /srv/tenants/user/artifacts
(venv-probe) user@atom-host:~/whitehat-boss$ ^C
(venv-probe) user@atom-host:~/whitehat-boss$ which tmux screen
tmux new -s runB
/usr/bin/tmux
/usr/bin/screen
[server exited]
(venv-probe) user@atom-host:~/whitehat-boss$ pgrep -af tmux || echo "tmux 없음"
ls /tmp/tmux-$(id -u) 2>/dev/null || echo "소켓 없음"
grep "^ *$(id -u):" /proc/key-users          # 376보다 줄었으면 정상
pgrep -af "vllm serve" | head -1              # 3663640 살아 있어야 한다
curl -s 127.0.0.1:8002/v1/models >/dev/null && echo "서버 OK"
ls -l /srv/tenants/user/artifacts/run_20260829_0001/serve_prefix_cache.txt \
      /srv/tenants/user/artifacts/run_20260829_0001/cli_stdout_runB.log
tmux 없음
default
 1004:   376 376/376 376/5000 1902/500000
3663640 /srv/tenants/user/venv-probe/bin/python3 /srv/tenants/user/venv-probe/bin/vllm serve /srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048 --served-model-name qwen3-coder-30b-a3b --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.28 --max-model-len 32768 --enable-prefix-caching
서버 OK
-rw-rw-r-- 1 user user  589  8월 29 17:39 /srv/tenants/user/artifacts/run_20260829_0001/cli_stdout_runB.log
-rw-rw-r-- 1 user user 1212  8월 29 17:39 /srv/tenants/user/artifacts/run_20260829_0001/serve_prefix_cache.txt
(venv-probe) user@atom-host:~/whitehat-boss$ cd ~/whitehat-boss
source /srv/tenants/user/venv-probe/bin/activate
export DS=/srv/tenants/user/experiments/2x2/dataset/track_a_evaluation_release_v1
export SERVE_LOG=/srv/tenants/user/artifacts/logs/serve_30b_runB_20260829_173017.log

git switch -c measure/v2-smoke-$(date +%Y%m%d)
awk '/^  [a-z0-9_-]+:/ { inblk = ($0 ~ /^  qwen3-coder-30b-a3b:/) }
     inblk && /prompt_template_version:/ { sub(/"v1"/,"\"v2\"") } { print }' \
  config/models.yaml > /tmp/m.yaml && mv /tmp/m.yaml config/models.yaml

git diff --stat
git diff | grep -E '^[+-].*prompt_template_version'

python3 -c "from src.config import load_config as L; c=L('b1_single'); \
print({r: c.models[c.profile.roles[r]].prompt_template_version for r in ('scout','auditor','critic')})"
새로 만든 'measure/v2-smoke-20260829' 브랜치로 전환합니다
 config/models.yaml | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
-    prompt_template_version: "v1"
+    prompt_template_version: "v2"
{'scout': 'v2', 'auditor': 'v2', 'critic': 'v2'}
(venv-probe) user@atom-host:~/whitehat-boss$ git commit -am 'measure: qwen3-coder-30b-a3b를 프롬프트 v2로 — 머지 대상 아님'
git rev-parse HEAD

time python3 -u -m src.cli run \
  --profile b1_single --device atom --tool-runner docker \
  --condition "B1prime@pipeline" \
  --artifacts-root /srv/tenants/user/artifacts \
  --target "$DS/blind/PWN-003/source" \
  2>&1 | tee ~/runV2.log
[measure/v2-smoke-20260829 e111eb8] measure: qwen3-coder-30b-a3b를 프롬프트 v2로 — 머지 대상 아님
 1 file changed, 1 insertion(+), 1 deletion(-)
e111eb8230548e98a1e1ccf04c5228eabb9fa38c
run_id: run_20260829_0002
산출물: /srv/tenants/user/artifacts/run_20260829_0002
리포트: report.md
후보 19건 / Finding 6건
완주 여부: 완주
[WARN ] 프로필과 다른 도구 런타임으로 돌았다: docker. evaluation.md §2.3 규칙 2의 예외이며 RunRecord.tool_runner에 남는다 (ADR 0005)
[WARN ] model_checksums가 자리표시자(전부 0)다: embedding(qwen3-embedding-0.6b), reranker(qwen3-reranker-0.6b). 어느 모델로 돈 run인지 기록이 지시하지 못한다. 모델 비교표에서 제외한다
[WARN ] kb_snapshot이 자리표시자(kb-none)다
[WARN ] Finding 3건이 7단계 critic 호출 실패로 판정을 못 받았다. 사유는 trace의 critic_failed에 있다

real    20m0.956s
user    0m0.548s
sys     0m0.262s
(venv-probe) user@atom-host:~/whitehat-boss$ R2=/srv/tenants/user/artifacts/run_20260829_0002
grep -E "Prefix cache hit rate" "$SERVE_LOG" | tail -5 | tee "$R2/serve_prefix_cache.txt"
cp ~/runV2.log "$R2/cli_stdout_runV2.log"
cp "$SERVE_LOG" "$R2/" && cp "$SERVE_LOG" /srv/tenants/user/artifacts/run_20260829_0001/
(APIServer pid=3663640) INFO 08-29 18:02:16 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 65.6 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 2.5%, Prefix cache hit rate: 50.7%
(APIServer pid=3663640) INFO 08-29 18:02:26 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 64.9 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 2.8%, Prefix cache hit rate: 50.7%
(APIServer pid=3663640) INFO 08-29 18:02:36 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 64.3 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 3.0%, Prefix cache hit rate: 50.7%
(APIServer pid=3663640) INFO 08-29 18:02:46 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 52.6 tokens/s, Running: 0 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.0%, Prefix cache hit rate: 50.7%
(APIServer pid=3663640) INFO 08-29 18:02:56 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 0.0 tokens/s, Running: 0 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.0%, Prefix cache hit rate: 50.7%
(venv-probe) user@atom-host:~/whitehat-boss$ python3 - "$R2" <<'PYEOF'
import json,sys,os,collections
R=sys.argv[1]
rows=[json.loads(l) for l in open(os.path.join(R,'trace.jsonl'),encoding='utf-8')]
ev=collections.Counter(r.get('kind') for r in rows)
print('이벤트:', dict(ev))
mc=[r for r in rows if r.get('kind')=='model_call']
d=lambda r: (r.get('data') or {})
ok=[r for r in mc if d(r).get('ok') is True]; bad=[r for r in mc if d(r).get('ok') is False]
print('model_call', len(mc), '/ ok', len(ok), '/ fail', len(bad))
print('400 흔적:', sum('400' in json.dumps(r,ensure_ascii=False) for r in bad))
outs=sorted((d(r).get('output_tokens') or 0) for r in ok)
print('output_tokens 상위8:', outs[-8:])
print('8192 초과(= 재시도 합산) 건수:', sum(1 for o in outs if o>8192))
w=[(d(r).get('wall_ms') or r.get('wall_ms') or 0) for r in mc]
print('model_call 총 시간:', round(sum(w)/1000), '초 / run 총 1201초')
print('가장 오래 걸린 5건(초):', [round(x/1000) for x in sorted(w)[-5:]])
print('--- critic_failed 3건 ---')
for r in rows:
    if r.get('kind')=='critic_failed': print(' ', json.dumps(d(r),ensure_ascii=False)[:220])
PYEOF

grep -ao 'trigger_status[^,}]*' "$R2/state.db" | sort | uniq -c
grep -ao '"produced_by":[^}]*}' "$R2/state.db" | sort | uniq -c
이벤트: {'stage_start': 69, 'stage_end': 69, 'token_counter': 1, 'tool_call': 13, 'budget': 41, 'graph_index': 1, 'target_plan': 1, 'target_planned': 19, 'cache': 25, 'model_call': 31, 'transition': 54, 'policy_decision': 28, 'gate_eval': 9, 'critic_failed': 3}
model_call 31 / ok 28 / fail 3
400 흔적: 0
output_tokens 상위8: [418, 469, 473, 477, 487, 8386, 8428, 8481]
8192 초과(= 재시도 합산) 건수: 3
model_call 총 시간: 1194 초 / run 총 1201초
가장 오래 걸린 5건(초): [125, 126, 243, 243, 243]
--- critic_failed 3건 ---
  {"reason": "schema_parse_failed", "error_detail": "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON: EOF while parsing an object at line 3993 column 4 [type=json_inval
  {"reason": "schema_parse_failed", "error_detail": "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON: EOF while parsing an object at line 4013 column 0 [type=json_inval
  {"reason": "schema_parse_failed", "error_detail": "critic 역할의 qwen3-coder-30b-a3b이 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON: EOF while parsing an object at line 10624 column 0 [type=json_inva
      6 trigger_status":"triggerable"
      6 "produced_by":{"role":"auditor","model":"qwen3-coder-30b-a3b","prompt_version":"v2"}
      3 "produced_by":{"role":"critic","model":"qwen3-coder-30b-a3b","prompt_version":"v2"}
(venv-probe) user@atom-host:~/whitehat-boss$ cd ~/whitehat-boss && python3 - <<'PYEOF'
import json,urllib.request
from src.schemas.finding import Finding
s=Finding.model_json_schema()
body={"model":"qwen3-coder-30b-a3b",
 "messages":[{"role":"user","content":"router_store()의 경계 검사 부족에 대한 Finding 하나를 만들어라. 모든 필드를 채운다."}],
 "max_tokens":1024,
 "response_format":{"type":"json_schema","json_schema":{"name":"Finding","schema":s,"strict":True}}}
req=urllib.request.Request("http://127.0.0.1:8002/v1/chat/completions",
    data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
raw=json.load(urllib.request.urlopen(req,timeout=300))["choices"][0]["message"]["content"]
print("스키마 선언 순서:", list(s["properties"].keys()))
print("모델이 낸 순서 :", list(json.loads(raw).keys()))
PYEOF
스키마 선언 순서: ['finding_id', 'run_id', 'candidate_id', 'state', 'path', 'trigger_status', 'confidence', 'rca_draft', 'cwe', 'title', 'evidence_refs', 'missing_evidence', 'repro_spec', 'produced_by']
모델이 낸 순서 : ['finding_id', 'run_id', 'candidate_id', 'state', 'path', 'confidence', 'cwe', 'title', 'repro_spec', 'produced_by']
(venv-probe) user@atom-host:~/whitehat-boss$ kill "$(cat ~/vllm_runB.pid)"; sleep 5; pgrep -af "vllm serve" || echo "서버 정리 완료"
cd ~/whitehat-boss && git checkout main
서버 정리 완료
'main' 브랜치로 전환합니다
(venv-probe) user@atom-host:~/whitehat-boss$ exit
로그아웃
Connection to REDACTED closed.
```

> ⚠ **run B의 `src.cli run` 명령 원문은 이 세션 로그에 없다 — `미기재`.**
> 위 전사는 `doctor` → `tmux` 실패 → `ls -l`(17:39 산출물 확인)에서 곧바로
> `git switch -c measure/v2-smoke`로 넘어간다. 그 사이에 run B가 돌았는데 그 구간이 전사에
> 없다. **재구성해 적지 않는다** — 이 절의 규칙이 "원문"이고, 확인 안 된 명령을 원문 자리에
> 놓으면 다음 사람이 그것을 그대로 돌린다.
>
> 실제로 쓰인 인자는 `run_20260829_0001/run_manifest.json`의
> `condition`·`tool_runner`·`device`가 지시하고 stdout은
> `run_20260829_0001/cli_stdout_runB.log`(589 B)에 있다 — 둘 다 §8.4 회수 6·14번이다.
> **집계 스크립트만은 확인됐다**: 위 v2 블록의 코드에서 `$R2`를 `run_20260829_0001`로 바꾼
> 것이고, 그 결과가 §4·§5의 run B 수치다.
