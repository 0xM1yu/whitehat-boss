# 슬라이서 slice-v2 실서버 검증 — 재개 동등성·제어문 복구·표본 3종 완주 — ATOM ([D77](../../open-questions/D/D77.md))

> **결론 넷.** ① **중단·재개가 연속 실행과 같은 `slices`를 낸다** — fixture에서 `flow_saved`
> 커밋 직후 `KeyboardInterrupt`를 넣고 재개했더니 slice 5개가 같았고, 재개 실행의 HTTP
> trace에 `flows` 질의가 **2회가 아니라 1회** 들어 있다(§3). ② **이슈 #205가 지목한
> `src/tftpcmd.c` switch 누락이 없어졌다** — `slice_0043`이 `switch`·`case` 5개·`default`·
> `break` 여덟 줄을 **전부** 들고 있다(§4.4). ③ **표본 셋이 모두 완주했다** —
> `queries_aborted=0`·`candidates_not_processed=0`. 그중 sample_03은 **재개로 되살렸다**:
> 1차가 600초 질의 기한에 걸려 105후보를 남기고 멈췄는데, 같은 체크포인트로 재개하니
> **완료 후보 331건을 재질의 없이 건너뛰고** 나머지를 끝냈다(§4.2). ④ **v1에서 재개를
> 구조적으로 막고 있던 것은 Joern 버전 파싱이었다**(§5).
>
> **이 실험이 코드 결함 둘을 실제로 잡았다** — 둘 다 단위 테스트가 못 잡고 실서버에서만
> 드러났다. (a) 버전 배너의 timestamp가 재개 계약을 매번 깨뜨린 것, (b) 문맥 질의가
> **JVM 문자열 상수 상한(65,535바이트)** 을 넘겨 질의 자체가 컴파일되지 않은 것.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-15 07:08 KST` (sample_03 재개 종료). fixture·sample_01·sample_02는 같은 날 `03:09 KST`까지 |
| 측정한 것 | **Joern 정적 슬라이싱뿐.** 모델·GPU를 쓰지 않는다 — 탐지 정확도·recall·오탐률이 아니다 |
| 실제 서빙 모델 | **없음.** 이 실험에 모델이 없다 |
| `repo_commit` | `98e8b9c` (`fix(slicer): 문맥 질의가 JVM 문자열 상수 상한을 넘지 않게 쪼갠다`), clean |
| 배포 코드 SHA-256 | `slicer.py` `0373767aece9c27935084bed4437ef73dfbe2d8541b81fcc273d7fce7f0589f4` · `slicer_queries.py` `e977dcfd2918e2c88c437cf7ed3414b11e79f4cc23600526c697a1072b53a966` · `slicer_backward.py` `f657961c916cf32ad088c51f8ff3c5cc3213d664588eb41f95d260818ea1165a` · `slicer_pipeline.py` `c49478f9f5e268daf6a72a36e6b4923c5a0549d7e05c763bd5e60e0754b27dce` · `slicer_checkpoint.py` `afa578cab980e928d57ed10c2c9d86c79df4657d4eaf867d2fe7126cb8eada04` · `slicer_source.py` `1be67a06e7bdfff64b3aaaf547e6b855757cf1d97221a7083fa67752bdcd051a` · `slicer_candidates.py` `4901ede40cb9d2ef96e9e90eea2a84a6370fd942bac621e89ad5a9442755a1c8` · `slicer_export.py` `8335ccd5e0c828b36ddbd9d0c767dcfa1901e2c6fc168e9e828fc70ee53ce330` |
| 코드 전송 검증 | 로컬 워킹트리와 ATOM 사본을 SHA-256으로 대조해 일치를 확인한 뒤 실행했다. **셀 수 있는 것은 위 행의 배포 코드 8개**(`slicer*.py` — `code_hashes`가 세는 그 집합이다)이고, 해시를 실은 것도 그 8개다. ⚠ 이전 판은 「12개」라고 적었는데 **그 수의 근거가 이 문서에 없어 지웠다**(§0) — 도구·fixture까지 대조했더라도 이 문서가 그것을 적지 않았다 |
| `run_id` | **없다.** 하네스 run이 아니라 독립 슬라이서 실행이라 `RunRecord`가 없다 — 비교표 밖이다 |
| run 산출물 경로·보존 여부 | ATOM `/srv/tenants/user/slicer-live-20260913-a0e5514/v2-20260915-rerun2/` — **보존**. run별로 `result.json`·`progress.jsonl`·`trace/responses.jsonl`·`server.log`·`client.log`·`exit-code.txt`·`resources.jsonl`·`export/` |
| `tool_runner` | 해당 없음 (하네스 프로필을 쓰지 않는다) |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 — 컨테이너를 쓰지 않고 호스트에서 직접 실행했다 |
| `--gpu-memory-utilization` | 해당 없음. **GPU를 쓰지 않는다** |
| 동시에 떠 있던 다른 서버 | **없다.** 실행 전 `ss -tlnp`로 16240~16249 전부 미청취를 확인했고, 스크립트가 매 run 시작 전 16242 점유를 확인해 점유 시 **남의 서버를 건드리지 않고 중단**한다 |
| 원문 산출물 보존 위치 | 위 run 디렉터리 — 보존. 폐기한 1차 시도도 지우지 않고 `v2-20260915-rerun/`에 남겼다 |
| 서빙 서버 stdout | run별 `runs/<name>/server.log` — **보존**. Joern 서버는 run마다 새로 띄우고 `start_new_session`으로 분리된 process group에 둔다 |
| private ground truth | **ATOM에 올리지 않았다.** 로컬 번들에서만 읽어 슬라이서 결과와 대조했다. 이 문서는 파생 줄 수에 더해 **§4.6에서 GT 범위의 줄 좌표와 CVE 번호를 싣는다** — Case A와 Case B를 가른 근거가 그 좌표라 빼면 그 판정의 근거가 사라진다. **번들 자체(파일·주석·판정 레이블)는 싣지 않는다.** 이 저장소는 비공개다 |

## 1. 요약

[D77](../../open-questions/D/D77.md)의 닫히는 조건 다섯이 **모두 실서버에서 확인됐다.**

| 조건 | 결과 | 근거 | 확인 범위 |
| --- | --- | --- | --- |
| 후보의 정확한 sink 식별, A/B별 흡수, 구성 후보 metadata 보존 | **확인** | §3.3 · §4.3 | fixture + 표본 3종 |
| switch label·`break`/`continue`/`goto` 원문 복구 | **확인** | §3.3 · §4.4 | fixture + sample_01 실대상 |
| flow 저장 후 중단·재개가 flow를 재요청하지 않고 연속 실행과 같은 결과 | **확인** | §3.1 · §3.2 · §4.2 | **동등성은 fixture에서.** 실대상은 「재질의 없음」까지 |
| 입력·코드·Joern·설정이 다르면 재개 거부, 미실측을 수치로 채우지 않음 | **확인** | §5 · §6 | 계약 5항목 |
| sample 01/02/03 결과·미처리·절단·로그 기록 | **확인** | §4 | 표본 3종 전부 완주 |

**성능 주장을 하지 않는다.** 아래 `total_seconds`는 경과 시간이지 벤치마크가 아니다 — 같은
장비에서 v1과 v2를 나란히 재측정한 것이 아니라 서로 다른 날 각 1회씩 돈 값이다.

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | ATOM `atom-host`, 계정 `user`, Linux aarch64 |
| Joern | **4.0.605** — `result.json`의 `joern_version` 필드에서 읽은 값이다 |
| JVM | Temurin OpenJDK **21.0.12** (`/srv/tenants/user/tools/jdk21`) |
| JVM heap 설정 | `-J-Xss64m -J-Xmx16g`. 서버가 보고한 `heap_max_bytes` = `17179869184` (16 GiB) |
| Joern EngineConfig | `maxCallDepth=4`, `maxArgsToAllow=1000`, `maxOutputArgsExpansion=1000` — **설치본 기본값을 읽은 것**이고 override하지 않았다 |
| 포트 | `127.0.0.1:16242` (전용). run마다 서버를 새로 띄우고 그 process group만 종료했다 |
| 분석 상한 | `--max-flows-per-candidate 10` · `--backward-depth 30` · `--backward-nodes 5000` · `--candidate-timeout-seconds 1200` |
| 질의 상한 | sample_03만 `--query-timeout-seconds 600`, 나머지는 `120` |
| 동시성 | 전용 서버 하나에 슬라이서 클라이언트 하나. run끼리도 순차 |

## 3. 실험 1 — fixture

### 목적

**경로 성립 판정이다.** 중단·재개가 연속 실행과 같은 결과를 내는지, 완료한 후보의 질의를
다시 보내지 않는지, 제어문이 복구되는지를 실제 Joern에서 본다. 성능 측정이 아니다.

### 조건

[공개 fixture](../../../tests/fixtures/slicer_v2/main.c) 2파일 48줄(`main.c` 44 · `helper.c` 4). run 셋을 순서대로 돌렸다.

1. `fixture-full` — 연속 실행.
2. `fixture-interrupt` — [중단 주입 래퍼](../../../tools/interrupt_after_flow_saved.py)로 **첫
   `flow_saved` 커밋 직후** `KeyboardInterrupt`. 저장 전에 중단하면 재개할 상태가 없으므로
   원래 `save_stage`를 먼저 끝내고 그 반환에서 올린다.
3. `fixture-resume` — `fixture-interrupt`가 남긴 **같은 체크포인트**에 `--resume`.
   Joern 서버는 새로 띄웠다(재개는 CPG를 다시 만든다).

### 3.1 결과 — 재개 동등성

| 항목 | fixture-full | fixture-interrupt | fixture-resume |
| --- | ---: | ---: | ---: |
| `status` / exit | `partial` / 2 | `partial` / 2 | `partial` / 2 |
| 입력 파일 수 (`files_seen` / `files_in_cpg`) | 2 / 2 | 2 / 2 | 2 / 2 |
| Case A / Case B 후보 | 2 / 8 | 2 / 8 | 2 / 8 |
| 완료 후보 (`slices_before_clustering`) | 10 | **0** | 10 |
| `slices_after_clustering` | **5** | 0 | **5** |
| `flows_truncated` / `backward_truncated` | 1 / 0 | 0 / 0 | 1 / 0 |
| `queries_aborted` | 0 | 0 | 0 |
| `candidates_not_processed` | 0 | **10** | 0 |
| `candidate_stages_reused` | 0 | 0 | **1** |
| `cpg_seconds` / `total_seconds` | 1.61 / 13.33 | 1.60 / 3.21 | 1.57 / 12.90 |

**`fixture-full`의 `slices`와 `fixture-resume`의 `slices`가 같다.** 스위트가
`full['slices'] == resumed['slices']`를 직접 비교해 `slices_equal: true`를 기록했다.
서로 다른 두 스위트 실행(코드 수정 전·후)에서 같은 결과가 나왔다.

### 3.2 근거 로그 — 완료 후보의 질의가 다시 나가지 않았다

run별 HTTP trace(`trace/responses.jsonl`)의 단계별 요청 수다. **요약이 아니라 실제 요청을 센 것**이다.

| run | `import` | `inventory` | `case_a` | `case_b` | `flows` | `backward` | `context` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixture-full | 1 | 1 | 1 | 1 | **2** | 8 | 20 |
| fixture-interrupt | 1 | 1 | 1 | 1 | **1** | 0 | 0 |
| fixture-resume | 1 | 1 | 1 | 1 | **1** | 8 | 20 |

```
[02:42:11] resume equivalence: {"check": "fixture_resume_equivalence", "slices_equal": true,
 "full_slices": 5, "resumed_slices": 5, "interrupt_status": "partial",
 "interrupt_candidates_not_processed": 10,
 "interrupt_stage_counts": {"case_a": 1, "case_b": 1, "flows": 1, "import": 1, "inventory": 1},
 "resume_stage_counts": {"backward": 8, "case_a": 1, "case_b": 1, "context": 20, "flows": 1,
 "import": 1, "inventory": 1}, "resume_candidates_reused": 0,
 "resume_candidate_stages_reused": 1, "resume_queries_aborted": 0}
```

**연속 실행은 `flows`를 2회, 재개 실행은 1회 보냈다.** 차이 1회가 중단 전에 `flow_saved`로
커밋된 그 후보다. `candidate_stages_reused=1`이 같은 것을 카운터 쪽에서 가리킨다.

**따라 나오지 않는 것.** `context`가 양쪽 다 20회다 — 중단된 후보는 `flow_saved`까지만
커밋됐고 `context_saved`는 아니었으므로 문맥 확장은 재개 때 다시 돈다. 이 실험은
**flow·backward 단계의 재요청 없음**을 보이지, 모든 단계가 재사용된다는 뜻이 아니다.

### 3.3 근거 — Case B 계측과 같은 줄의 두 sink

fixture `main.c:14`는 한 줄에 `strcpy`가 둘이다.

```c
    strcpy(first, relay(tainted)); strcpy(second, independent);
```

후보 manifest가 그 둘을 **열과 AST order 경로로 갈랐다.**

```
key=516abad7fdb5 sym=strcpy line=14 col=36 ast_path=[2, 10]  code='strcpy(second, independent)'
key=bf621dc0ae5a sym=strcpy line=14 col=5  ast_path=[2, 9]   code='strcpy(first, relay(tainted))'
```

두 후보 키가 다르고, 이후 클러스터링이 줄 집합 겹침으로 둘을 한 slice(`slice_0004`)로 묶은
뒤에도 `members`에 **두 키가 모두 남는다.** 흡수는 후보 정체성을 지우지 않는다.

Case B slice 셋은 전부 `flow_count = null`이고 `analysis_kind = backward_ddg`다. Case A
`slice_0002`(`control`, `main.c:20`)만 `flow_count = 10`으로 `--max-flows-per-candidate 10`
상한에 닿아 `truncated=true`이고, 그래서 배치 status가 `partial`이다. **Case 간 흡수는
한 건도 없다** — A 두 개(`slice_0001`·`slice_0002`)는 아무것도 흡수하지 않았다.

fixture의 `control` 함수는 `switch`/`case`/`default`, `break`, `continue`, `goto`, label을
한 함수에 모아 둔 것이다. 그 함수를 덮는 slice가 `if (choose()) break;` ·
`if (i == 1) continue;` · `if (op == 7) goto cleanup;`과 `cleanup:` label을 원문대로 들고 있다.

## 4. 실험 2 — 공개 스모크 표본

### 목적

실대상에서 완주 여부와 제어문 복구를 본다. **탐지 성능 측정이 아니다.**

### 조건

[PR #203의 v1 측정](https://github.com/0xM1yu/whitehat-boss/pull/203)과 같은 입력
(`repo_smoke_test_bundle.zip`의 `runner_samples/sample_XX/repo`, ATOM
`/srv/tenants/user/slicer-smoke-20260914-512ab693/inputs/`)과 같은 flow cap 10을 썼다.
표본마다 새 run 디렉터리·새 `state.db`·새 Joern 서버다.

### 4.1 결과 — 완주 판정

| 표본 | 입력 파일 | Case A / B 후보 | 완료 후보 | 전 / 후 | flow 절단 | backward 절단 | `queries_aborted` | `candidates_not_processed` | status / exit | `cpg_seconds` | `total_seconds` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| sample_01 | 6 | 48 / 102 | 150 | 150 / 68 | 41 | 6 | **0** | **0** | `partial` / 2 | 3.81 | 1443.46 |
| sample_02 | 6 | 17 / 35 | 52 | 52 / 30 | 13 | 15 | **0** | **0** | `partial` / 2 | 3.60 | 179.23 |
| sample_03 | 155 | 131 / 305 | 436 | 436 / 143 | 96 | 183 | **0** | **0** | `partial` / 2 | 202.53 | 1505.52 (재개 구간) |

**표본 셋 모두 완주 기준을 만족한다** — `queries_aborted == 0`이고
`candidates_not_processed == 0`이다. `status`가 `partial`인 이유는 **오직 분석 절단**이다.

sample_03의 `total_seconds`는 **재개 구간만**이다. 1차 시도는 `12732.39`초를 쓰고 기한에
걸렸다(§4.2). 두 구간의 합은 약 `14238`초(≈3.96시간)이며 이것은 **계산값**이다.

절단의 내역은 다음과 같다. **어느 후보가 왜 잘렸는지를 적는다.**

| 표본 | Case A 절단 (전부 `--max-flows-per-candidate 10` 상한) | Case B 절단 사유 (`truncation_reasons` 조합별) |
| --- | --- | --- |
| sample_01 | 41건, 전부 `flow_count=10` | 6건 전부 `backward_depth`. 6건 모두 `slice_0087`에 들어갔다 |
| sample_02 | 13건, 전부 `flow_count=10` | 15건 — `backward_depth` 9, `backward_depth`+`call_depth` 6 |
| sample_03 | 96건, 전부 `flow_count=10` | 183건 — `backward_depth` 73, `backward_states`+`call_depth` 45, `backward_depth`+`call_depth` 37, `call_depth` 17, 셋 모두 8, `backward_states` 3 |

sample_01의 Case B 절단 6건은 `node_count` 251~355, `state_count` 346~666이다.
**두 값이 다르다** — 상태는 노드와 호출 stack의 조합이므로 같은 노드를 서로 다른 호출
경로로 다시 방문한다. 이 차이가 `--backward-nodes`(상태 상한)와 노드 수를 같은 값으로
읽으면 안 되는 이유다.

### 4.2 sample_03 — 기한에 걸린 run을 재개가 되살렸다

1차 시도는 후보 **331건**을 끝낸 뒤 Case B `backward` 질의 하나가 **600초 기한**을 넘겨
중단됐다(`queries_aborted=1`, `candidates_not_processed=105`). 이것은 붕괴가 아니라
**지정한 상한**이다 — `--query-timeout-seconds 600`은 이 표본에 대해 의뢰된 값이다.

같은 체크포인트에 **설정을 바꾸지 않고** `--resume`을 걸어 새 서버에서 이어 돌렸다.

| 관측 | 값 | 무엇을 뜻하나 |
| --- | ---: | --- |
| `candidates_reused` · `candidate_stages_reused` | 331 · 331 | 1차가 끝낸 후보를 **전부** 재사용했다 |
| 재개 구간의 `flows` 요청 | **0** | Case A 131건이 모두 재사용 집합에 있어 **한 건도 다시 묻지 않았다** |
| 재개 구간의 `backward` 요청 | **105** | 미처리로 남았던 Case B 후보 수와 **정확히 같다** |
| 재개 구간의 `context` 요청 | 297 | 새로 처리한 105건의 문맥 확장 |
| 기한에 걸렸던 그 후보 | `complete` | 새 서버에서는 같은 600초 안에 끝났다 |
| 최종 `queries_aborted` · `candidates_not_processed` | 0 · 0 | 완주 |

`attempts` 배열이 두 시도를 **둘 다** 들고 있다 — 1차는 `aborted=1`·`notproc=105`,
2차는 `0`·`0`이다. 재개가 1차의 실패를 지우지 않는다.

**읽히는 것.** 같은 후보가 1차에서는 600초를 넘고 2차에서는 안 넘었다. **여기까지가
실측이다.** 따라서 그 후보의 질의가 **본질적으로 600초를 넘는다고 말할 수 없다.**

**원인은 재지 않았다.** 두 서버의 관측 가능한 차이는 나이다 — 1차는 그 시점까지 3.5시간을
돌았고 2차는 갓 띄운 것이다. 그래서 **누적 heap 압박을 가설로 둔다.** 확정하지 않는 이유는
그것을 뒷받침할 측정이 없기 때문이다: `server`에 기록된 것은 `java_version`·`process`·
`started_ms`·`heap_max_bytes`(= `-J-Xmx16g` **설정값**)뿐이고, **heap 점유량·GC pause·질의별
부하는 재지 않았다.** 같은 보고서가 §4.5에서 엔진 비결정성도 관측했으므로 서버 나이 말고도
후보가 있다. 확정하려면 GC log나 JFR 같은 직접 측정이 필요하다 — 이번 범위 밖이다.

**따라 나오지 않는 것.** 이 재개는 **완주**를 보였지, 재개 결과가 무중단 실행과 같다는
것을 보이지 않았다 — sample_03의 무중단 실행이 존재하지 않아 대조군이 없다.
바이트 동등성은 fixture에서만 측정했다(§3.1).

### 4.3 기준값과의 대조

**세 열은 서로 다른 것을 센다.** 실험팀(PM) 값의 코드 버전·후보 정의·flow cap·클러스터링·
줄 계산법은 우리에게 제공되지 않았고, v1은 이 저장소의 `slice-v1` 실행이다.

| 표본 | PM slice (기준) | v1 전 / 후 | v2 전 / 후 | PM GT 줄 (기준) | v1 GT 범위 / 파일 | v2 GT 범위 / 파일 | GT 범위 크기 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sample_01 | 71 | 143 / 55 | **150 / 68** | 12 | 17 / 34 | **18 / 39** | 29 |
| sample_02 | 28 | 52 / 29 | **52 / 30** | 57 | 50 / 76 | **51 / 77** | 60 |
| sample_03 | 167 | 415 / 146 (OOM 중단) | **436 / 143** | 206 | 199 / 294 | **199 / 677** | 252 |

v1 값의 원천은 [PR #203의 스모크 보고서](https://github.com/0xM1yu/whitehat-boss/pull/203)다.
`GT 범위 줄`·`GT 파일 줄`의 정의는 그 보고서의 것을 그대로 썼다 — 전자는 GT가 지정한
`[line_start, line_end]` 안에서 최종 slices가 보존한 고유 `(file, line)` 수, 후자는 그 범위를
한 줄이라도 포함한 slice가 같은 파일에서 보존한 고유 `(file, line)` 수다.

**v1의 sample_03 값은 완주가 아니다** — 436후보 중 415건에서 OOM으로 멈춘 상태의 관측이다.
따라서 `146`과 `143`을 같은 조건의 두 값으로 읽으면 안 된다.

**PM의 12·57·206이 위 두 정의 중 어느 것인지는 여전히 알 수 없다.** v1 보고서가 그렇게
적었고 그 사이에 정의가 확정되지 않았다. 따라서 이 차이를 축소 성능 차이로 읽지 않는다.

#### 차이가 어디서 오는가 — 갈라서 적는다

**(a) Case B 의미 변경.** v1은 A·B 모두 소속 함수 파라미터를 source로 쓰는 공통
`reachableByFlows`였고 v2의 B는 선택한 sink 인자에서의 backward DDG다. 세는 대상이 달라
B의 줄 집합이 달라진다. sample_03의 `GT 파일 줄`이 `294 → 677`로 크게 는 것이 그 방향이다 —
GT가 CWE-415(이중 해제)이고 v2에서 GT 범위를 덮은 slice 넷이 전부 `_zip_dirent_finalize`·
`_zip_buffer_free`·`_zip_string_free`·`_zip_source_call` 같은 **해제 경로 Case B**다.
다만 **후보별로 분해해 확인하지는 않았다**(§6).

**(b) 후보 정체성 — 같은 줄의 같은 이름 호출이 갈렸다.** v1의 후보 키에는 selector가 없어
`(case, file, line, symbol)`이 같으면 한 후보였다. v2는 열과 AST 경로를 넣는다. **실측으로
확인했다** — v2 sample_01의 후보 150개를 v1 방식 키로 묶으면 **정확히 143개**가 되고, 이는
v1이 보고한 143과 같다. 갈라진 7건은 `src/common.c:60`·`:65`·`:96`, `src/ftpcmd.c:217`의
`logit` 호출처럼 한 줄에 같은 이름 호출이 둘인 자리다. sample_02(52)와 sample_03(436)은
두 방식이 같은 수를 내므로, 그 두 표본에는 같은 줄 중복이 없다.

**(c) ground-truth 정의 차이.** 위에 적은 대로 PM 기준의 줄 정의가 확정되지 않았다.
우리 두 정의 사이의 차이(예: sample_03의 199 대 677)가 PM 값(206)과의 간격보다 크다.

### 4.4 근거 — 이슈 #205가 지목한 switch가 복구됐다

[#205](https://github.com/0xM1yu/whitehat-boss/issues/205)는 v1 관측으로
*"sample_01 slice_0043의 src/tftpcmd.c switch에서 break 및 case label 누락"*을 적었다.
같은 파일 같은 switch(`src/tftpcmd.c:323`~`367`)를 v2 결과에서 다시 셌다.

| slice | case | entry | switch 범위에서 보존한 줄 | 누락된 구조 줄 |
| --- | --- | --- | ---: | --- |
| `slice_0043` | A | `src/tftpcmd.c:296:read_client_command` | 33 | **없음** |
| `slice_0067` | B | `src/common.c:92:strlcat` | 35 | **없음** |
| `slice_0146` | B | `src/tftpcmd.c:260:snprintf` | 33 | **없음** |

세 slice 모두 `switch (op) {`(323), `case RRQ:`(324), `case WRQ:`(336), `case DATA:`(348),
`case ERROR:`(354), `case ACK:`(359), `default:`(364)와 그 범위의 `break;` **여덟 줄**
(329·334·341·346·352·357·362·367)을 **전부** 들고 있다 — 셋 다 여덟 줄을 빠짐없이 들었다. 서로 다른 두 스위트
실행에서 같은 결과가 나왔다.

**따라 나오지 않는 것.** 이것은 이 switch 하나에 대한 관측이다. 구조 복구가 모든 제어
구조에서 완전하다는 뜻이 아니고, 복구된 조각이 컴파일된다는 뜻도 아니다.

### 4.5 절단된 Case A는 CPG를 다시 만들면 같은 경로를 고르지 않는다

같은 코드·같은 입력으로 **스위트를 두 번** 돌려 sample_01·sample_02 결과를 대조했다.
카운터는 전부 같았고(`150/68`·`52/30`, 절단 수까지 동일) 각 slice의 **`lines`와 `code`도
같았다.** 다른 것이 딱 하나 있었다 — sample_02 `slice_0016`(Case A, `truncated=true`,
`flow_count=10`)의 `analysis.lines`와 `line_provenance`다.

| 관측 | 1차 | 2차 |
| --- | --- | --- |
| `analysis.lines` 길이 | 571 | 551 |
| 한쪽에만 있는 줄 | `cJSON.c:229`, `cJSON.c:246` | `cJSON.c:211`, `cJSON.c:220` |

**우리 질의는 결정론적이다** — `flows_query`는 sink를 `nodeKey`로, batch를 메서드
좌표로 정렬한 뒤 `take(N+1)`한다. 두 실행의 `flows_query` 소스는 바이트가 같다. 따라서
갈림은 **엔진이 돌려준 경로 집합 자체**에서 온다. 상한에 걸린 후보는 발견된 경로 중 앞
10개만 남기므로, 엔진이 다른 집합을 돌려주면 남는 10개가 달라진다.

**읽히는 것.** `truncated=true`인 Case A 후보의 `analysis`는 **CPG 재생성 사이에서 안정하지
않다.** 계약 문서가 *"Joern 재파싱 후 결과 동등성은 별도 실측으로 확인한다"*고 유보해 둔
자리에 들어갈 실측이 이것이고, 답은 **절단된 후보에 대해서는 같지 않다**이다.

**따라 나오지 않는 것.** 최종 `lines`·`code`는 두 실행에서 같았다. 그러나 표본이 2회이므로
"최종 줄 집합은 항상 같다"로 일반화하지 않는다.

### 4.6 슬라이스가 쓸 만한가 — 크기와 GT 포착

완주와 별개로 **나온 조각이 읽을 만한 크기인지**를 쟀다. `lines` 수 기준이다.

| 표본 | slice 수 | 중앙값 | p90 | 최대 | 500줄 초과 | 최대 members |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| sample_01 | 68 | 40 | 99 | 451 | **0** | 36 |
| sample_02 | 30 | 12 | 121 | 332 | **0** | 11 |
| sample_03 | 143 | 82 | 555 | **5,626** | **16 (11%)** | **207** |

**작은 대상에서는 조각이 촘촘하다.** sample_01의 GT(CVE-2020-20276)를 덮은 Case B 슬라이스
`slice_0084`는 **16줄**이고, 그 16줄이 취약 사슬 전체를 담는다 — 진입
`handle_PORT(ctrl_t *, char *str)`, 버퍼 선언 `char addr[INET_ADDRSTRLEN]`,
`sscanf(str, ...)`로 들어온 `a,b,c,d`, **`sprintf(addr, "%d.%d.%d.%d", a, b, c, d)`**,
그리고 선택된 sink `strlcpy(ctrl->data_address, addr, ...)`. guard와 early return이
중괄호 짝을 맞춘 채 남았고 빈 줄·주석·무관한 정리 코드(`uev_io_stop`·`close`)는 빠졌다.

**취약 줄을 잡은 것은 Case A가 아니라 Case B다.** 같은 GT 범위를 덮은 Case A 슬라이스
`slice_0010`(rank 10)에는 `sscanf`(443)도 `sprintf`(444)도 **없다.** 두 줄을 들고 있는 것은
`slice_0082`·`slice_0084` 둘 다 Case B다 — v2에서 바꾼 sink-first 역추적이 실제 CVE에서
값을 낸 자리가 여기다.

GT 범위 29줄 중 합집합이 18줄을 보존했고 누락 11줄의 내역은 **빈 줄 5(435·441·445·452·455),
주석 2(442·446), 실코드 4(437·438·456·457)** 다. 그 넷은 `addr`의 역방향 의존이 아니다 —
437·438은 `data_sd` 정리, 456·457은 sink 뒤의 로깅·응답이다. 즉 실코드 22줄 중 18줄
(81.8%)이고, **이 값은 계산값이다.**

**큰 대상에서는 조각이 커진다.** sample_03(libzip, 155파일)은 중앙값 82줄로 본체는 멀쩡한데
**꼬리가 두껍다** — 16개가 500줄을 넘고 최대 `slice_0341`은 후보 **207개**가 합쳐진
**5,626줄**이다. GT를 덮은 넷도 1,116~5,626줄이라 **그대로 캡슐 본문으로 쓰기에는 크다.**

**그 5,626줄이 전부 역방향 의존인 것은 아니다.** `slice_0341`의 `line_provenance`를 직접
집계하면(`sample_03` 재개 run의 `result.json`) `data_dependency` **2,114줄**,
`candidate_anchor` **207줄**, `context` **4,580줄**이고 셋의 합집합이 정확히 5,626줄이다.
어느 쪽에도 겹치지 않는 **context 단독이 3,512줄**로 절반을 넘는다. 즉 이 조각의 크기는
의존 + anchor + **보수적 문맥 확장**의 합집합이고, 여기에 후보 207개의 클러스터링이 겹친다.
**의존만으로 5,626줄이 나온 것이 아니다.** (수치는 전부 그 파일에서 센 [실측]이다.)

**따라 나오지 않는 것.** 이 절은 크기와 GT 포착만 본다. 큰 조각이 **틀렸다**는 뜻이 아니고,
작은 조각이 판정에 **충분하다**는 뜻도 아니다 — 모델
판정 정확도는 이 실험 밖이다. 임계값 `0.95`를 낮추거나 크기 상한을 두면 꼬리가 어떻게
움직이는지도 재지 않았다.

## 5. 막힌 것과 해결

**이 절이 이 실험의 소득이다.** 아래 둘은 단위 테스트가 못 잡았고 실서버에서만 드러났다.

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 첫 시도의 `fixture-resume`가 `Checkpoint mismatch: run_contract`로 거부됐다 | `joern --version`을 모르는 런처가 **매 호출 timestamp가 붙은 배너**를 뱉는데, 그 출력 전체를 `joern_version`으로 저장했다. 계약 digest가 매 실행 달라져 **재개가 구조적으로 불가능**했다 | `read_version`이 배너에서 **semantic version만** 뽑고, 후보가 유일하지 않으면 `null`로 둔다. 원문 출력은 `diagnostics`의 `version_command`에 남긴다. 고친 뒤 `joern_version`은 `4.0.605` |
| sample_03이 후보 208건에서 `String constant is too long for the JVM`으로 중단됐다 | `context_query`가 줄 목록을 Scala 소스에 **문자열 리터럴 하나로** 박는다. 클래스 파일의 `CONSTANT_Utf8` 길이 필드가 16비트라 **65,535바이트가 상한**인데, 한 후보에서 **218,895바이트**가 나와 질의 자체가 컴파일되지 않았다 | `joined_literal`이 값을 쪼개 `List(...).mkString`으로 런타임에 잇는다. `+`로 잇지 않는 이유는 컴파일러가 인접 리터럴을 **다시 하나로 접기** 때문이다. 고친 뒤 같은 표본이 **436건 전부** 처리됐다 |
| sample_03 1차가 600초 질의 기한에서 105후보를 남기고 멈췄다 | `--query-timeout-seconds 600`은 의뢰된 값이다. 3.5시간 돌아간 서버에서 Case B 질의 하나가 기한을 넘었다 — **원인은 재지 않았다**(§4.2) | 같은 체크포인트에 **설정을 바꾸지 않고** 재개했다. 331건을 재질의 없이 건너뛰고 나머지를 끝냈으며, 기한에 걸렸던 그 후보도 새 서버에서는 통과했다(§4.2) |
| 스위트 2회차에서 `port in use; refusing to touch another session's server`로 중단 | joern 런처는 셸이고 **그 셸의 종료가 JVM의 포트 해제를 뜻하지 않는다.** 런처 프로세스만 `wait()`하고 다음 서버를 띄우려 했다 | 종료 루틴이 SIGTERM 후 **포트 해제와 process group 소멸을 둘 다 확인**할 때까지 기다리고, 안 되면 SIGKILL로 올린다. 다른 세션 서버는 여전히 건드리지 않는다 |
| 이전 세션이 남긴 `runs/`가 재실행을 막았다 | 체크포인트와 trace 디렉터리는 **덮어쓰기를 거부하도록 설계**돼 있다 (`--resume` 없이 기존 `state.db`면 `ValueError`, trace는 `exist_ok=False`) | 지우지 않고 별도 경로로 옮겨 보존했다. 실패한 run의 `state.db`는 재사용하지 않았다 |

## 6. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 실대상 규모의 재개 **동등성** | sample_03은 재개로 **완주**했지만 대조군이 없다 — 무중단 sample_03 실행이 존재하지 않는다. 바이트 동등성은 fixture에서만 쟀다 | sample_02(179초)를 무중단으로 한 번, 중단·재개로 한 번 돌려 `slices`를 대조한다 |
| `files_parsed` · `files_parse_failed` · `files_cpg_failed` | Joern frontend Report가 HTTP 응답에 완전한 형태로 실리지 않았다. **CPG의 파일 노드 수를 파싱 성공 수로 대체하지 않는다** | 서버 stdout에서 Report 전문을 읽어 합계 행까지 대조한다 |
| `engine_flows_truncated` | Joern 엔진이 내부 상한으로 버린 경로 수를 공개 API가 내주지 않는다 | Joern 쪽 계측이 생기기 전에는 못 잰다 |
| 절단 Case A `analysis`의 안정성 범위 | §4.5는 재실행 **2회** 관측이다. 갈림의 빈도·크기를 재지 않았다 | 같은 입력을 N회 돌려 절단 후보별 경로 집합의 갈림을 센다 |
| v1 대비 줄 집합 차이의 후보별 분해 | v1 결과와 v2 결과를 후보 키로 잇는 대조를 하지 않았다. **후보 키 정의 자체가 달라져서**(§4.3 (b)) 단순 조인이 성립하지 않는다 | v1 키(selector 없는 4-튜플)로 양쪽을 묶어 줄 집합 차집합을 낸다 |
| `context_saved`에서 중단했을 때의 재개 동등성 | 이번 중단 지점은 `flow_saved` 한 곳이다 | 래퍼의 중단 조건을 `context_saved`로 바꿔 같은 비교를 한 번 더 돌린다 |
| v1과 v2의 성능 비교 | 같은 날 나란히 재측정하지 않았다. `total_seconds`는 서로 다른 날 각 1회 값이다 | 같은 서버·같은 순서로 두 버전을 교대 실행한다 |
| 기한 상한을 바꾼 재개 | `query_timeout_seconds`는 `run_contract`의 `settings`에 들어가므로 **기한만 늘려 재개할 수 없다**(계약 불일치로 거부된다) | 기한류가 분석 의미를 바꾸지 않는다는 판단이 서면 계약에서 빼는 변경을 따로 낸다 — 이 PR 범위 밖이다 |

## 7. 반증 조건

**이 실험이 실패했다면 무엇이 틀린 것이었나.**

- `fixture-resume`의 `slices`가 `fixture-full`과 달랐다면 — 단계별 체크포인트가 재개
  지점에서 상태를 온전히 복원하지 못한다는 뜻이고, [D77](../../open-questions/D/D77.md)의
  재개 조건이 깨진다. **실제로 첫 시도에서 이 비교가 실패했고**(§5 첫 행), 원인은 재개
  로직이 아니라 버전 파싱이었다. 비교가 없었다면 그 결함이 통과했을 것이다.
- 재개 실행의 `flows` 요청이 2회였다면 — 완료 단계를 재사용하지 않고 다시 질의한다는
  뜻이다. fixture에서 1회, sample_03 재개에서 **0회**였으므로 §3.2·§4.2의 재사용 주장이 선다.
- `slice_0043`에 `break`나 `case`가 여전히 없었다면 — §4.4의 구조 복구가 이 사례를 못
  고친 것이고 [#205](https://github.com/0xM1yu/whitehat-boss/issues/205)의 관측이 그대로
  남는다.
- `queries_aborted`나 `candidates_not_processed`가 0이 아니었다면 — 완주가 아니므로
  slice 수를 기준값과 비교하는 §4.3의 행이 성립하지 않는다. **sample_03 1차가 정확히 그
  상태였고**, 그래서 그 시도의 수치를 최종값으로 쓰지 않고 재개해서 다시 쟀다.
- §4.5에서 두 실행의 `lines`가 달랐다면 — 최종 슬라이스 내용 자체가 재생성 사이에서
  흔들린다는 뜻이고, 그러면 슬라이스를 캡슐 입력으로 쓰는 모든 후속 판정이 흔들린다.
  같았으므로 흔들리는 범위는 지금까지 **절단된 Case A의 `analysis`까지**다.

## 8. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 중간 | 실대상 규모의 재개 **동등성** 대조 (sample_02 중단·재개) | 미착수 (§6) |
| 중간 | 절단 Case A `analysis` 갈림의 빈도·크기 | 미착수 (§4.5 · §6) |
| 중간 | v1↔v2 줄 집합 차이의 후보별 분해 | 미착수 (§6) |
| 중간 | **큰 대상의 슬라이스 꼬리**(sample_03에서 16개가 500줄 초과, 최대 5,626줄·207 members) — 임계값·크기 상한이 꼬리를 어떻게 바꾸는지 | 미착수 (§4.6). [D74](../../open-questions/D/D74.md)의 「슬라이스를 캡슐 본문으로 쓰면」 판단에 직접 걸린다 |
| 낮음 | 기한류(`query_timeout_seconds`)를 재개 계약에서 빼는 변경 | 별도 판단 필요 (§6) |
| 낮음 | **`slicer.py`를 800줄 아래로 쪼갠다** — 현재 868줄로 [engineering-guide](../../engineering-guide.md) §2의 상한을 넘는다(base·main은 783줄이었다) | **이번에 안 한다** — 쪼개면 `slicer*.py` 집합이 바뀌어 §2 표에 실은 **배포 코드 8개의 실측 해시가 이 run을 만든 코드를 더 이상 가리키지 않는다**(§0). 다음 측정이 이 보고서를 대체할 때가 그 자리다 |
| 낮음 | PM 기준 `GT 줄` 정의 확인 | 외부 확인 필요. 정의 전까지 §4.3의 대조는 기준 열 병기로만 쓴다 |
| 범위 밖 | Scout·캡슐 배선 | **이번 범위가 아니다** — [D74](../../open-questions/D/D74.md)의 결정을 대신하지 않는다 |

## 9. 정리 상태

- **Joern 서버**: run마다 띄우고 run마다 종료했다. 포트 16242만 사용했고 매 종료에서 포트
  해제와 process group 소멸을 확인했다. **16240·16241 등 다른 세션의 서버는 건드리지
  않았다** — 실행 시작 시점에 애초에 떠 있는 서버가 없었다. 실험 종료 후 16242도 비었다.
- **임시 설정·프로필**: 없음. 하네스 설정을 건드리지 않았다.
- **저장소 변경**: 있음 — 슬라이서 구현·계약 문서·이 보고서.
- **ATOM 산출물**: 보존. 폐기한 1차 시도도 지우지 않고 남겼다.

## 10. 부록 — 재현 절차

전용 서버와 클라이언트의 전체 명령이다. 줄이지 않았다.

```bash
export JAVA_HOME=/srv/tenants/user/tools/jdk21
export PATH="$JAVA_HOME/bin:$PATH"
cd /srv/tenants/user/slicer-live-20260913-a0e5514/v2-20260915-rerun2

# 서버 — run마다 새로 띄우고 그 process group만 종료한다.
setsid /srv/tenants/user/tools/joern-cli/joern -J-Xss64m -J-Xmx16g \
  --server --server-host 127.0.0.1 --server-port 16242 \
  > runs/<run>/server.log 2>&1 < /dev/null &

# 연속 실행 (fixture-full, sample_01, sample_02, sample_03 1차)
python3 tools/run_slicer_recorded.py runs/<run>/trace \
  --repo <repo> \
  --out runs/<run>/result.json \
  --checkpoint runs/<run>/state.db \
  --progress runs/<run>/progress.jsonl \
  --export-dir runs/<run>/export \
  --joern-endpoint http://127.0.0.1:16242 \
  --joern-version-command /srv/tenants/user/tools/joern-cli/joern \
  --max-flows-per-candidate 10 \
  --backward-depth 30 \
  --backward-nodes 5000 \
  --candidate-timeout-seconds 1200 \
  --query-timeout-seconds 120

# sample_03만 질의 상한이 다르다: --query-timeout-seconds 600

# 중단 주입 (fixture-interrupt) — 위와 같은 인자에 래퍼만 바꾼다.
python3 tools/interrupt_after_flow_saved.py runs/fixture-interrupt/trace <같은 인자>

# 재개 (fixture-resume, sample_03 재개) — 중단된 run의 체크포인트를 그대로 쓰고
# 분석 인자를 하나도 바꾸지 않은 채 --resume만 더한다. 서버는 새로 띄운다.
python3 tools/run_slicer_recorded.py runs/sample_03-resume/trace \
  --checkpoint runs/sample_03/state.db \
  --out runs/sample_03-resume/result.json \
  --progress runs/sample_03-resume/progress.jsonl \
  --export-dir runs/sample_03-resume/export \
  <나머지 인자 동일> --resume
```

스위트를 순서대로 돌린 스크립트(`run_suite_v2.py`)와 재개 스크립트(`resume_sample_03.sh`)는
ATOM의 run 디렉터리에 있다 — 저장소에는 없다. `suite.log`·`suite-summary.json`이 run별 명령
원문·서버 PID·PGID·시작/종료 epoch을 들고 있고, `runs/sample_03-resume/result.json`의
`attempts`가 sample_03의 두 시도를 모두 들고 있다.
