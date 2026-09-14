# 슬라이서 slice-v2 실서버 검증 — fixture 재개 동등성과 표본 3종 — ATOM ([D75](../../open-questions/D/D75.md))

> **결론 셋.** ① **중단·재개가 연속 실행과 같은 `slices`를 낸다** — fixture에서 `flow_saved`
> 커밋 직후 `KeyboardInterrupt`를 넣고 재개했더니 5개 slice가 같았고, 재개 실행의 HTTP
> trace에 `flows` 질의가 **2회가 아니라 1회** 들어 있다(§3). ② **이슈 #205가 지목한
> `src/tftpcmd.c` switch 누락이 없어졌다** — `slice_0043`이 `switch`·`case` 5개·`default`·
> `break` 7개를 **전부** 들고 있다(§4.3). ③ **v1에서 재개를 구조적으로 막고 있던 것은
> Joern 버전 파싱이었다** — 런처 배너에 매 호출 timestamp가 붙는데 그것을 통째로 버전으로
> 저장해 `run_contract` digest가 매번 달라졌다(§5).
>
> **sample_03은 이 보고서 작성 시점에 실행 중이다.** §4의 그 행은 `미측정`이고, 채워지기
> 전까지 [D75](../../open-questions/D/D75.md)는 **열림**이다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | fixture·sample_01·sample_02 = `2026-09-15 01:20 KST`. **sample_03 = 진행 중** |
| 측정한 것 | **Joern 정적 슬라이싱뿐.** 모델·GPU를 쓰지 않는다 — 탐지 정확도·recall·오탐률이 아니다 |
| 실제 서빙 모델 | **없음.** 이 실험에 모델이 없다 |
| `repo_commit` | `bac5a1a` (`feat(slicer): sink-first 역추적·제어문 보존·단계별 재개 (slice-v2)`), clean |
| 배포 코드 SHA-256 | `slicer.py` `0373767aece9c27935084bed4437ef73dfbe2d8541b81fcc273d7fce7f0589f4` · `slicer_backward.py` `f657961c916cf32ad088c51f8ff3c5cc3213d664588eb41f95d260818ea1165a` · `slicer_pipeline.py` `c49478f9f5e268daf6a72a36e6b4923c5a0549d7e05c763bd5e60e0754b27dce` · `slicer_checkpoint.py` `afa578cab980e928d57ed10c2c9d86c79df4657d4eaf867d2fe7126cb8eada04` · `slicer_source.py` `1be67a06e7bdfff64b3aaaf547e6b855757cf1d97221a7083fa67752bdcd051a` · `slicer_queries.py` `fd0df2d9b5c7216d78738545bdac3a326662dd414b386bf381c7640d14d5ad36` · `slicer_candidates.py` `4901ede40cb9d2ef96e9e90eea2a84a6370fd942bac621e89ad5a9442755a1c8` · `slicer_export.py` `8335ccd5e0c828b36ddbd9d0c767dcfa1901e2c6fc168e9e828fc70ee53ce330` |
| 코드 전송 검증 | 로컬 워킹트리와 ATOM 사본을 **파일 11개 전부 SHA-256 대조**해 일치를 확인한 뒤 실행했다 |
| `run_id` | **없다.** 하네스 run이 아니라 독립 슬라이서 실행이라 `RunRecord`가 없다 — 비교표 밖이다 |
| run 산출물 경로·보존 여부 | ATOM `/srv/tenants/ext01/slicer-live-20260913-a0e5514/v2-20260915-rerun/` — **보존**. run별로 `result.json`·`progress.jsonl`·`trace/responses.jsonl`·`server.log`·`exit-code.txt`·`resources.jsonl`·`export/` |
| `tool_runner` | 해당 없음 (하네스 프로필을 쓰지 않는다) |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 — 컨테이너를 쓰지 않고 호스트에서 직접 실행했다 |
| `--gpu-memory-utilization` | 해당 없음. **GPU를 쓰지 않는다** |
| 동시에 떠 있던 다른 서버 | **없다.** 실행 전 `ss -tlnp`로 16240~16249 전부 미청취를 확인했고, 스크립트가 매 run 시작 전 16242 점유를 확인해 점유 시 **남의 서버를 건드리지 않고 중단**한다 |
| 원문 산출물 보존 위치 | 위 run 디렉터리 — 보존 |
| 서빙 서버 stdout | run별 `runs/<name>/server.log` — **보존**. Joern 서버는 run마다 새로 띄우고 `start_new_session`으로 분리된 process group에 둔다 |
| private ground truth | **ATOM에 올리지 않았다.** 로컬 번들에서만 읽어 슬라이서 결과와 대조했다 |

## 1. 요약

[D75](../../open-questions/D/D75.md)의 닫히는 조건 다섯 중 **넷이 실서버에서 확인됐고 하나가 미완**이다.

| 조건 | 결과 | 근거 |
| --- | --- | --- |
| 후보의 정확한 sink 식별, A/B별 흡수, 구성 후보 metadata 보존 | **확인** | §3.3 · §4.2 |
| switch label·`break`/`continue`/`goto` 원문 복구 (fixture와 실대상) | **확인** | §3.3 · §4.3 |
| flow 저장 후 중단·재개가 flow를 재요청하지 않고 연속 실행과 같은 결과 | **확인** | §3.1 · §3.2 |
| 입력·코드·Joern·설정이 다르면 재개 거부, 미실측을 수치로 채우지 않음 | **확인** | §5 · §6 |
| sample 01/02/03 결과·미처리·절단·로그 기록 | **01·02 확인, 03 미측정** | §4.1 |

**성능 주장을 하지 않는다.** 아래 `total_seconds`는 경과 시간이지 벤치마크가 아니다 — 같은
장비에서 v1과 v2를 나란히 재측정한 것이 아니라 서로 다른 날 각 1회씩 돈 값이다.

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | ATOM `aitopatom-d06a`, 계정 `ext01`, Linux aarch64 |
| Joern | **4.0.605** — `result.json`의 `joern_version` 필드에서 읽은 값이다 |
| JVM | Temurin OpenJDK **21.0.12** (`/srv/tenants/ext01/tools/jdk21`) |
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

[공개 fixture](../../../tests/fixtures/slicer_v2/main.c) 2파일 44줄. run 셋을 순서대로 돌렸다.

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
| `cpg_seconds` / `total_seconds` | 1.61 / 13.70 | 1.58 / 3.22 | 1.58 / 13.22 |

**`fixture-full`의 `slices`와 `fixture-resume`의 `slices`가 같다.** 스위트가
`full['slices'] == resumed['slices']`를 직접 비교해 `slices_equal: true`를 기록했다.

### 3.2 근거 로그 — 완료 후보의 질의가 다시 나가지 않았다

run별 HTTP trace(`trace/responses.jsonl`)의 단계별 요청 수다. **요약이 아니라 실제 요청을 센 것**이다.

| run | `import` | `inventory` | `case_a` | `case_b` | `flows` | `backward` | `context` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixture-full | 1 | 1 | 1 | 1 | **2** | 8 | 20 |
| fixture-interrupt | 1 | 1 | 1 | 1 | **1** | 0 | 0 |
| fixture-resume | 1 | 1 | 1 | 1 | **1** | 8 | 20 |

```
[00:52:38] resume equivalence: {"check": "fixture_resume_equivalence", "slices_equal": true,
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

fixture의 `control` 함수는 `switch`/`case`/`default`, `break`, `continue`, `goto`,
label을 한 함수에 모아 둔 것이다. 그 함수를 덮는 slice가
`if (choose()) break;` · `if (i == 1) continue;` · `if (op == 7) goto cleanup;`과
`cleanup:` label을 원문 그대로 들고 있다.

### 해석

재개가 연속 실행과 같은 `slices`를 낸다는 것이 **실제 Joern에서** 성립한다. 다만 표본은
fixture 1건이고 중단 지점도 `flow_saved` 한 곳이다 — `context_saved`에서의 중단이나 실대상
규모에서의 재개는 이 실험이 보이지 않는다(§6).

## 4. 실험 2 — 공개 스모크 표본

### 목적

실대상에서 제어문 복구와 완주 여부를 본다. **탐지 성능 측정이 아니다.**

### 조건

[PR #203의 v1 측정](https://github.com/0xM1yu/whitehat-boss/pull/203)과 같은 입력
(`repo_smoke_test_bundle.zip`의 `runner_samples/sample_XX/repo`, ATOM
`/srv/tenants/ext01/slicer-smoke-20260914-512ab693/inputs/`)과 같은 flow cap 10을 썼다.
표본마다 새 run 디렉터리·새 `state.db`·새 Joern 서버다.

### 4.1 결과 — 완주 판정

| 표본 | 입력 파일 | Case A / B 후보 | 완료 후보 | 클러스터링 전 / 후 | flow 절단 | backward 절단 | `queries_aborted` | `candidates_not_processed` | status / exit | `cpg_seconds` | `total_seconds` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| sample_01 | 6 | 48 / 102 | 150 | 150 / 68 | 41 | 6 | **0** | **0** | `partial` / 2 | 3.87 | 1453.75 |
| sample_02 | 6 | 17 / 35 | 52 | 52 / 30 | 13 | 15 | **0** | **0** | `partial` / 2 | 3.49 | 175.34 |
| sample_03 | 미측정 | 131 / 305 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | **진행 중** | 미측정 | 미측정 |

**sample_01·02는 완주 기준을 만족한다** — `queries_aborted == 0`이고
`candidates_not_processed == 0`이다. `status`가 `partial`인 이유는 오직 분석 절단이다.

절단의 내역은 다음과 같다. **어느 후보가 왜 잘렸는지를 적는다.**

| 표본 | Case A 절단 | Case B 절단 사유 | 절단 후보가 들어간 slice |
| --- | --- | --- | --- |
| sample_01 | 41건, 전부 `flow_count=10` — `--max-flows-per-candidate 10` 상한 | 6건 전부 `backward_depth` (`--backward-depth 30`) | Case B 6건은 모두 `slice_0087` |
| sample_02 | 13건, 전부 `flow_count=10` — 같은 상한 | 9건 `backward_depth`, 6건 `backward_depth` + `call_depth` | 여러 slice에 분산 |
| sample_03 | 미측정 | 미측정 | 미측정 |

sample_01의 Case B 절단 6건은 `node_count` 251~355, `state_count` 346~666이다.
**두 값이 다르다** — 상태는 노드와 호출 stack의 조합이므로 같은 노드를 서로 다른 호출
경로로 다시 방문한다. 이 차이가 `--backward-nodes`(상태 상한)와 노드 수를 같은 값으로
읽으면 안 되는 이유다.

### 4.2 기준값과의 대조

**세 열은 서로 다른 것을 센다.** 실험팀(PM) 값의 코드 버전·후보 정의·flow cap·클러스터링·
줄 계산법은 우리에게 제공되지 않았고, v1은 이 저장소의 `slice-v1` 실행이다.

| 표본 | PM slice (기준) | v1 전 / 후 | v2 전 / 후 | PM GT 줄 (기준) | v1 GT 범위 / 파일 | v2 GT 범위 / 파일 | GT 범위 크기 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sample_01 | 71 | 143 / 55 | **150 / 68** | 12 | 17 / 34 | **18 / 39** | 29 |
| sample_02 | 28 | 52 / 29 | **52 / 30** | 57 | 50 / 76 | **51 / 77** | 60 |
| sample_03 | 167 | 415 / 146 (OOM 중단) | **미측정** | 206 | 199 / 294 | **미측정** | 252 |

v1 값의 원천은 [PR #203의 스모크 보고서](https://github.com/0xM1yu/whitehat-boss/pull/203)다.
`GT 범위 줄`·`GT 파일 줄`의 정의는 그 보고서의 것을 그대로 썼다 — 전자는 GT가 지정한
`[line_start, line_end]` 안에서 최종 slices가 보존한 고유 `(file, line)` 수, 후자는 그 범위를
한 줄이라도 포함한 slice가 같은 파일에서 보존한 고유 `(file, line)` 수다.

**PM의 12·57·206이 위 두 정의 중 어느 것인지는 여전히 알 수 없다.** v1 보고서가 그렇게
적었고 그 사이에 정의가 확정되지 않았다. 따라서 이 차이를 축소 성능 차이로 읽지 않는다.

#### v1과 v2의 차이가 어디서 오는가 — 갈라서 적는다

**(a) Case B 의미 변경.** v1은 A·B 모두 소속 함수 파라미터를 source로 쓰는 공통
`reachableByFlows`였고 v2의 B는 선택한 sink 인자에서의 backward DDG다. 세는 대상이 달라
B의 줄 집합이 달라진다. 이것이 sample_01의 `55 → 68` 대부분을 설명할 후보이지만,
**후보별로 분해해 확인하지는 않았다**(§6).

**(b) 후보 정체성 — 같은 줄의 같은 이름 호출이 갈렸다.** v1의 후보 키에는 selector가 없어
`(case, file, line, symbol)`이 같으면 한 후보였다. v2는 열과 AST 경로를 넣는다. **실측으로
확인했다** — v2 sample_01의 후보 150개를 v1 방식 키로 묶으면 **정확히 143개**가 되고, 이는
v1이 보고한 143과 같다. 갈라진 7건은 `src/common.c:60`·`:65`·`:96`, `src/ftpcmd.c:217`의
`logit` 호출처럼 한 줄에 같은 이름 호출이 둘인 자리다. sample_02는 두 방식 모두 52로,
같은 줄 중복이 없어 v1과 v2의 후보 수가 같다.

**(c) ground-truth 정의 차이.** 위에 적은 대로 PM 기준의 줄 정의가 확정되지 않았다.
우리 두 정의 사이의 차이(예: sample_01의 18 대 39)가 PM 값(12)과의 간격보다 크다.

### 4.3 근거 — 이슈 #205가 지목한 switch가 복구됐다

[#205](https://github.com/0xM1yu/whitehat-boss/issues/205)는 v1 관측으로
*"sample_01 slice_0043의 src/tftpcmd.c switch에서 break 및 case label 누락"*을 적었다.
같은 파일 같은 switch(`src/tftpcmd.c:323`~`367`)를 v2 결과에서 다시 셌다.

| slice | case | entry | switch 범위에서 보존한 줄 | 누락된 구조 줄 |
| --- | --- | --- | ---: | --- |
| `slice_0043` | A | `src/tftpcmd.c:296:read_client_command` | 33 | **없음** |
| `slice_0067` | B | `src/common.c:92:strlcat` | 35 | **없음** |
| `slice_0146` | B | `src/tftpcmd.c:260:snprintf` | 33 | **없음** |

세 slice 모두 `switch (op) {`(323), `case RRQ:`(324), `case WRQ:`(336), `case DATA:`(348),
`case ERROR:`(354), `case ACK:`(359), `default:`(364)와 그 범위의 `break;` 일곱 줄
(329·334·341·346·352·357·362·367 중 해당 줄)을 **전부** 들고 있다.

**따라 나오지 않는 것.** 이것은 이 switch 하나에 대한 관측이다. 구조 복구가 모든 제어
구조에서 완전하다는 뜻이 아니고, 복구된 조각이 컴파일된다는 뜻도 아니다.

## 5. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 첫 시도의 `fixture-resume`가 `Checkpoint mismatch: run_contract`로 거부됐다 | `joern --version`을 모르는 런처가 **매 호출 timestamp가 붙은 배너**를 뱉는데, 그 출력 전체를 `joern_version`으로 저장했다. 계약 digest가 매 실행 달라져 **재개가 구조적으로 불가능**했다 | `read_version`이 배너에서 **semantic version만** 뽑고, 후보가 유일하지 않으면 `null`로 둔다. 원문 출력은 `diagnostics`의 `version_command`에 남긴다. 재개 후 `joern_version`은 `4.0.605` |
| 스위트 2회차에서 `port in use; refusing to touch another session's server`로 중단 | joern 런처는 셸이고 **그 셸의 종료가 JVM의 포트 해제를 뜻하지 않는다.** 런처 프로세스만 `wait()`하고 다음 서버를 띄우려 했다 | 종료 루틴이 SIGTERM 후 **포트 해제와 process group 소멸을 둘 다 확인**할 때까지 기다리고, 안 되면 SIGKILL로 올린다. 다른 세션 서버는 여전히 건드리지 않는다 |
| 이전 세션이 남긴 `runs/`가 재실행을 막았다 | 체크포인트와 trace 디렉터리는 **덮어쓰기를 거부하도록 설계**돼 있다 (`--resume` 없이 기존 `state.db`면 `ValueError`, trace는 `exist_ok=False`) | 지우지 않고 `runs-attempt1-portguard/`로 옮겨 보존했다. 실패한 run의 `state.db`는 재사용하지 않았다 |

## 6. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| sample_03 전 항목 | 이 보고서 작성 시점에 **실행 중**이다. 436 후보(A 131 / B 305)로 v1과 같은 규모이고 v1은 같은 입력에서 3.6시간 뒤 OOM으로 멈췄다 | 스위트 완료 후 §4의 해당 행을 실측으로 채운다. 완주 판정은 `queries_aborted == 0`과 `candidates_not_processed == 0` |
| `files_parsed` · `files_parse_failed` · `files_cpg_failed` | Joern frontend Report가 HTTP 응답에 완전한 형태로 실리지 않았다. **CPG의 파일 노드 수를 파싱 성공 수로 대체하지 않는다** | 서버 stdout에서 Report 전문을 읽어 합계 행까지 대조한다 |
| `engine_flows_truncated` | Joern 엔진이 내부 상한으로 버린 경로 수를 공개 API가 내주지 않는다 | Joern 쪽 계측이 생기기 전에는 못 잰다 |
| v1 대비 줄 집합 차이의 후보별 분해 | v1 결과와 v2 결과를 후보 키로 잇는 대조를 하지 않았다. **후보 키 정의 자체가 달라져서**(§4.2 (b)) 단순 조인이 성립하지 않는다 | v1 키(selector 없는 4-튜플)로 양쪽을 묶어 줄 집합 차집합을 낸다 |
| `context_saved`에서 중단했을 때의 재개 동등성 | 이번 중단 지점은 `flow_saved` 한 곳이다 | 래퍼의 중단 조건을 `context_saved`로 바꿔 같은 비교를 한 번 더 돌린다 |
| 실대상 규모에서의 재개 동등성 | fixture(2파일 44줄)에서만 확인했다 | sample_02(175초) 정도에서 같은 중단·재개·대조를 돌린다 |
| v1과 v2의 성능 비교 | 같은 날 나란히 재측정하지 않았다. `total_seconds`는 서로 다른 날 각 1회 값이다 | 같은 서버·같은 순서로 두 버전을 교대 실행한다 |

## 7. 반증 조건

**이 실험이 실패했다면 무엇이 틀린 것이었나.**

- `fixture-resume`의 `slices`가 `fixture-full`과 달랐다면 — 단계별 체크포인트가 재개
  지점에서 상태를 온전히 복원하지 못한다는 뜻이고, [D75](../../open-questions/D/D75.md)의
  재개 조건이 깨진다. **실제로 첫 시도에서 이 비교가 실패했고**(§5 첫 행), 원인은 재개
  로직이 아니라 버전 파싱이었다. 비교가 없었다면 그 결함이 통과했을 것이다.
- 재개 실행의 `flows` 요청이 2회였다면 — 완료 단계를 재사용하지 않고 다시 질의한다는
  뜻이다. 1회였으므로 §3.2의 재사용 주장이 선다.
- `slice_0043`에 `break`나 `case`가 여전히 없었다면 — §4.3의 구조 복구가 이 사례를 못
  고친 것이고 [#205](https://github.com/0xM1yu/whitehat-boss/issues/205)의 관측이 그대로
  남는다.
- `queries_aborted`나 `candidates_not_processed`가 0이 아니었다면 — 완주가 아니므로
  slice 수를 기준값과 비교하는 §4.2의 행 자체가 성립하지 않는다. sample_03이 지금 그
  상태이고, 그래서 그 행이 `미측정`이다.

## 8. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | sample_03 완주 여부와 §4의 빈 칸 | **실행 중** |
| 높음 | [D75](../../open-questions/D/D75.md) 상태 전환 | sample_03이 완주해야 닫힘 조건 다섯이 모두 찬다 |
| 중간 | `context_saved` 중단·실대상 규모 재개 동등성 | 미착수 (§6) |
| 중간 | v1↔v2 줄 집합 차이의 후보별 분해 | 미착수 (§6) |
| 낮음 | PM 기준 `GT 줄` 정의 확인 | 외부 확인 필요. 정의 전까지 §4.2의 대조는 기준 열 병기로만 쓴다 |
| 범위 밖 | Scout·캡슐 배선 | **이번 범위가 아니다** — [D74](../../open-questions/D/D74.md)의 결정을 대신하지 않는다 |

## 9. 정리 상태

- **Joern 서버**: run마다 띄우고 run마다 종료했다. 포트 16242만 사용했고 매 종료에서 포트
  해제와 process group 소멸을 확인했다. **16240·16241 등 다른 세션의 서버는 건드리지
  않았다** — 실행 시작 시점에 애초에 떠 있는 서버가 없었다. sample_03 실행 중에는 그 run의
  서버가 떠 있다(§10에 기록 위치).
- **임시 설정·프로필**: 없음. 하네스 설정을 건드리지 않았다.
- **저장소 변경**: 있음 — 구현 커밋 `bac5a1a`와 이 보고서.
- **ATOM 산출물**: 보존. 이전 세션의 실패한 run 디렉터리도 지우지 않고
  `runs-attempt1-portguard/`로 옮겨 남겼다.

## 10. 부록 — 재현 절차

전용 서버와 클라이언트의 전체 명령이다. 줄이지 않았다.

```bash
export JAVA_HOME=/srv/tenants/ext01/tools/jdk21
export PATH="$JAVA_HOME/bin:$PATH"
cd /srv/tenants/ext01/slicer-live-20260913-a0e5514/v2-20260915-rerun

# 서버 — run마다 새로 띄우고 그 process group만 종료한다.
setsid /srv/tenants/ext01/tools/joern-cli/joern -J-Xss64m -J-Xmx16g \
  --server --server-host 127.0.0.1 --server-port 16242 \
  > runs/<run>/server.log 2>&1 < /dev/null &

# 연속 실행 (fixture-full, sample_01, sample_02)
python3 tools/run_slicer_recorded.py runs/<run>/trace \
  --repo <repo> \
  --out runs/<run>/result.json \
  --checkpoint runs/<run>/state.db \
  --progress runs/<run>/progress.jsonl \
  --export-dir runs/<run>/export \
  --joern-endpoint http://127.0.0.1:16242 \
  --joern-version-command /srv/tenants/ext01/tools/joern-cli/joern \
  --max-flows-per-candidate 10 \
  --backward-depth 30 \
  --backward-nodes 5000 \
  --candidate-timeout-seconds 1200 \
  --query-timeout-seconds 120

# 중단 주입 (fixture-interrupt) — 위와 같은 인자에 래퍼만 바꾼다.
python3 tools/interrupt_after_flow_saved.py runs/fixture-interrupt/trace <같은 인자>

# 재개 (fixture-resume) — 중단 run의 체크포인트를 그대로 쓰고 --resume만 더한다.
python3 tools/run_slicer_recorded.py runs/fixture-resume/trace \
  --checkpoint runs/fixture-interrupt/state.db <같은 인자> --resume

# sample_03만 질의 상한이 다르다: --query-timeout-seconds 600
```

스위트 전체를 순서대로 돌린 스크립트는 ATOM의 `run_suite_v2.py`다 — 저장소에 없다.
`suite.log`·`suite-summary.json`이 run별 명령 원문·서버 PID·PGID·시작/종료 epoch을 들고 있다.

### sample_03 진행 상태 (이 보고서 작성 시점)

| 항목 | 값 |
| --- | --- |
| 단계 | 후보 열거 완료(436 = A 131 / B 305), 후보별 슬라이싱 진행 중 |
| 체크포인트 | `runs/sample_03/state.db` — 단계별로 커밋 중 |
| 진행 로그 | `runs/sample_03/progress.jsonl` |
| Joern 서버 | `runs/sample_03/server-launch.json`에 PID·PGID·포트 기록 |
| 재개 명령 | 위 재개 블록에서 `--checkpoint runs/sample_03/state.db`로 `--resume`. 서버는 먼저 재기동한다 |
