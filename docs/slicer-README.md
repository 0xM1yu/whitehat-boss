# 오프라인 Joern 슬라이서 — slice-v2

Python 3.11+ 표준 라이브러리만 사용하는 독립 C/C++ 슬라이서다. 모델과 하네스를 import하지
않으며 저장소별로 미리 결과를 만든다. Scout·캡슐 배선은 별도다. 변경 근거는 [D77](open-questions/D/D77.md)다.

## 배포 파일과 실행

[slicer.py](../tools/slicer/slicer.py), [slicer_queries.py](../tools/slicer/slicer_queries.py),
[slicer_backward.py](../tools/slicer/slicer_backward.py), [slicer_candidates.py](../tools/slicer/slicer_candidates.py),
[slicer_source.py](../tools/slicer/slicer_source.py), [slicer_pipeline.py](../tools/slicer/slicer_pipeline.py),
[slicer_checkpoint.py](../tools/slicer/slicer_checkpoint.py), [slicer_export.py](../tools/slicer/slicer_export.py),
[slicer_library.py](../tools/slicer/slicer_library.py), [slicer_compile.py](../tools/slicer/slicer_compile.py),
[slicer_errors.py](../tools/slicer/slicer_errors.py), [slicer_claims.py](../tools/slicer/slicer_claims.py)를 함께 배포한다 — `tools/slicer/` 디렉터리째다. 서로를 이름으로 import(`import slicer_queries`)하고 실행 파일의
디렉터리가 import 경로 첫 칸이라서다. `tools/merge_slicer_shards.py`는 `tools/slicer/`를 자기 옆에서 찾고
`tools/slice_sharded.py`는 저장소 레이아웃 그대로를 전제한다.
후보별 질의(flows·context·backward)의 로직은 inventory 뒤에 **서버 세션당 한 번** 함수로 정의하고,
후보마다 그 함수 호출만 보낸다 — 질의마다 Scala 전체를 다시 컴파일하던 비용을 없앤다. 세션에 남는 것은
함수뿐이고 후보 데이터는 호출 안에만 있다. 서버를 새로 띄우면 다음 run이 다시 정의한다.
하네스 설치나 서드파티 런타임 패키지는 필요 없다.

Python과 Joern의 `--repo`는 같은 절대경로의 같은 소스를 가리켜야 한다. 실행 중 소스를
수정하지 않는다. 전용 서버에는 클라이언트 하나만 연결한다. 아래 상한은 실행 예시이며
품질·성능의 권장값으로 실측한 것은 아니다.

```bash
./joern-cli/joern -J-Xss64m -J-Xmx16g --server --server-host 127.0.0.1 --server-port 16242
python tools/slicer/slicer.py --repo /work/repo --out /work/results/slices.json \
  --joern-endpoint http://127.0.0.1:16242 \
  --joern-version-command /opt/joern/joern \
  --checkpoint /work/results/state.db --progress /work/results/progress.jsonl \
  --export-dir /work/results/export --threshold 0.95 \
  --max-flows-per-candidate 10 --backward-depth 30 --backward-nodes 5000 \
  --query-timeout-seconds 600 --candidate-timeout-seconds 1200
```

`--max-call-depth`, `--max-args-to-allow`, `--max-output-args-expansion`은 Joern EngineConfig
override다. 생략 시 설치본 기본값을 읽어 `joern_engine_config`에 기록한다. 분석 상한은
생략하면 제한하지 않는다. heartbeat 기본 간격은 CLI `--heartbeat-seconds 30`이다.

`--compile-commands`는 `compile_commands.json`을 받아 c2cpg의 `--compilation-database`로 켠다.
컴파일되는 번역단위만 그 실제 플래그로 파싱하므로 미컴파일 파일과 비활성 `#ifdef` 분기가
그래프에서 빠진다. 생략하면 모든 소스를 파싱하는 기존 동작이다. c2cpg가 무시하는 폴더
`test/`·`tests/`·`CMakeFiles/`·점디렉토리와 비-C/C++ 확장자 항목은 기대집합에서 빼므로
누락으로 오인하지 않는다. 뺀 개수는 `build_cpg` diagnostic의 `ignored_units`에 남긴다. 두
한계를 유의한다.

- **빌드 설정 하나만 본다.** DB 하나 = 설정 하나이고, 다른 설정에서만 컴파일되는 코드는
  그래프에 없다. 여러 설정의 union은 시도하지 않는다 -> #271.
- **비활성 분기는 파싱하지 않는다.** c2cpg는 `-D` 미지정 시 `__linux__`·`__x86_64__` 같은
  타깃 매크로를 정의하지 않으므로, 그 매크로에 걸린 분기가 비활성이 되어 빠질 수 있다. 이
  매크로 동작의 실증은 Joern 실행이 필요하고 풀 슬라이싱에서 확인한다.

종료 코드는 `ok=0`, `partial=2`, `failed=1`이다. CPG 준비 뒤 질의 실패·위치 누락·분석 절단은
부분 결과를 남긴다. 정상적인 빈 결과와 질의 실패를 구별한다. 출력은 UTF-8·LF이고, 최종
JSON은 임시 파일을 fsync한 뒤 교체한다. 출력 경로 자체가 불가능하면 stderr에 사유를 쓴다.

## 분석 규칙

### Case A — 기존 파라미터 기반 경로

이름이 일치하는 호출이 **`CASE_A_CALL_LIMIT`회(기본 1) 이하**이고 매개변수가 있는 내부
함수와 지정된 입력 API를 부르는 내부 함수를 후보로 삼는다. 호출이 아예 없는 함수만 보면
**한 번 호출되는 핸들러 진입점**(예: TLS `client_handle_hello`)을 놓치므로 K회 이하로 완화한다
(#219). 완화된 집합은 대형 저장소에서 폭증한다. 예전에는 그 payload가 Joern stdout을 넘겨
슬라이스가 통째로 실패했는데, #250이 payload를 파일로 옮겨 그 overrun은 사라졌다. 그래도
`CASE_A_CANDIDATE_CAP`으로 유계하는 것은 후보 수(와 하류 per-candidate 비용)를 묶기
위해서다 — 입력 API 호출자는 집합이 작아 유계 대상이 아니다. 소속 함수 파라미터에서 CPG의 호출 인자로 이어지는
`reachableByFlows` 경로를 구한다. 함수별 sink batch를 유지한다. 매개변수 없는 reader는
0경로일 수 있다.

### Case B — 선택한 호출 인자에서 역추적

지역 배열 식별자를 인자로 받는 호출, 같은 함수에서 같은 선언을 반복 해제하는 호출, 위험
write 함수(memcpy·strcpy 계열), command/SQL 실행 호출(system·popen·exec*·apr_proc_create·sqlite3_*·mysql_* 계열), 그리고 연산자 앵커 — 배열 인덱스 **쓰기**(`a[i] = …`), deref/field
**쓰기**(`p->x = …`·`*p = …`·`s.f = …`), 포인터 산술(피연산자가 포인터 타입이거나 메모리 접근인
`p ± i`) — 를 후보로 삼는다. 인덱스·deref/field **읽기**는 제외한다(폭증 방지·recall 한계). 해제
wrapper는 free로 흐르는 **매개변수 위치**를 보존한다. write·연산자 집합은 대형 저장소에서
폭증하므로(WavPack 실측: 인덱스 접근 3,042·add/sub 3,309) 좁힌 뒤 각각 상한(`CASE_B_*_CAP`)으로
유계한다. 인덱스 **읽기**와 정수만의 산술은 제외한다 — 유계를 위한 recall 한계다.

후보마다 호출의 소속 함수·줄·열·code·AST order 경로로 호출 한 개를 선택한다. 모호하면
첫 호출을 임의로 쓰지 않고 실패를 기록한다. **이 실패는 그 후보 하나로 끝난다** —
질의가 `require`로 죽어 `QueryError`가 되지만, 후보 루프가 그것을 잡아 `candidates_failed`에
세고 다음 후보로 계속한다(#219). 매핑 실패(`candidates_unmapped`)가 후보 하나만 건너뛰고
계속하는 것과 같은 결이다. ⚠ 단 `poisoned`(서버 상태 불확실) 실패가 **연속 `POISON_STOP_STREAK`회**면
run을 멈춘다 — 서버가 그 뒤로 회복 못 하기 때문이고, 재기동 후 `--resume`이 저장 지점에서 잇는다. `trigger_arguments`는 후보 선정 근거이고,
`sink_arguments`는 선택한 호출의 모든 인자다. 지역배열 인자 패턴에서는 `trigger_arguments`가
그 배열 인자만이라 둘이 갈린다 — 무엇이 이 호출을 후보로 만들었는지가 남는다. ⚠ 단
**WRITERS·exec·operator 앵커는 트리거가 전 인자다** — 그 필터는 인자 하나가 아니라 호출 전체로
발화하므로(`writers.contains(c) || execs.contains(c) || operators.contains(c)`) 그 후보에서는 `trigger_arguments`가
`sink_arguments`와 같아진다. `memcpy(dst, src, n)`의 dst·src·n을 모두 역추적하려는 의도다.
반복 해제의 관련 호출은 anchor로도 남긴다.

선택한 인자에서 `ddgIn` 관계를 역추적한다. 내부 호출의 반환과 formal parameter/actual
argument 경계는 호출 위치 stack으로 연결한다. 의존성 그래프를 수집하며 모든 경로를 열거하지
않는다. 파라미터·외부/미해결 호출·의존성 끝은 `boundaries`에 기록한다. 복잡한 alias와 출력
인자·동적 호출에 대한 완전한 추적은 보장하지 않는다.

`--backward-depth`는 DDG 및 명시적 함수 경계 이동 수, `--backward-nodes`는 방문 상태
수 상한이다. 상태는 노드와 호출 stack의 조합이므로 `state_count`와 `node_count`가 다를 수
있다. 새로운 상태가 남은 채 깊이·상태·호출 깊이 제한에 걸리면 `truncated=true`와
`truncation_reasons`를 기록한다. 방문한 상태의 순환 재방문은 절단으로 세지 않는다.

### 원문 복원과 클러스터링

선언·대입·조건·함수 시그니처·return을 보충한다. 관련 제어 구조의 case/default와
break/continue/goto, 같은 함수의 label과 goto를 보수적으로 보존하고 새 guard의 선언도
고정점까지 보충한다. lexical 복구는 여러 줄 문장·괄호·initializer와 jump/label을 복구한다.
원문에 없는 break를 만들지 않는다. 전체 함수의 실행 동등성이나 독립 컴파일을 보장하지 않는다.

같은 Case끼리 큰 슬라이스부터 `교집합 줄 수 / 흡수 대상 줄 수 >= threshold`이면 합집합으로
흡수한다. 변화가 없을 때까지 반복한다. `(file, line)`을 기준으로 하므로 파일 간 같은 줄
번호가 섞이지 않는다. 모든 구성 후보의 metadata와 분석 결과를 보존한다.

## 후보 식별과 slice-v2 계약

v1과 분석 의미 및 필드가 다르므로 `slice_version`은 `slice-v2`다. 읽는 쪽은 모르는 버전을
거절한다. 이전 v1 결과는 기준선으로 보존하며 v2 체크포인트로 변환해 섞지 않는다.

| 필드 | 의미 |
| --- | --- |
| `repo`, `generated_at`, `threshold`, `settings` | 저장소 이름·생성 UTC 시각·클러스터 임계값·실행 설정 |
| `status`, `error` | 정상·부분·실패. error는 부분/실패에서만 존재 |
| `joern_version`, `joern_engine_config`, `server` | 실제 버전 명령, 엔진 설정, JVM 버전·프로세스·기동시각·heap 최대 바이트 |
| `run_signature` | 원본 파일 bytes SHA-256 목록·절대경로·배포 코드 해시·설정·Joern 버전·계약 버전의 해시 |
| `candidate_manifest` | 모든 후보의 ID·case·entry·정확한 selector·선정 이유·인자·anchor를 후보 키로 조회 |
| `priority` | `--priority-files`를 줬을 때만. 목록 파일 수·후보가 있는 파일 수·1층 후보 수·전체 후보 수, 그리고 맞은/안 맞은 파일 이름(`matched_files`·`unmatched_files`, 각 최대 50개 — 사후 감사용). 아래 「우선 파일」 |
| `slices` | 완료한 후보를 클러스터링한 결과. 실패한 미완성 후보는 넣지 않음. ⚠ **`partial`과 `[]`의 조합은 두 가지를 담는다** — (가) CPG는 만들어졌고 슬라이스가 0건, (나) CPG 준비 중 StackOverflowError 로 **CPG 자체가 없다**. `status` 문자열만으로는 안 갈린다. **가르는 것은 `joern_engine_config`다** — (나)는 `null`이다(후보 카운터도 `null`). 비교표에 넣을지 정하는 쪽은 `status != "failed"` 가 아니라 그것을 본다 |
| `shard` | `--shard I/N` 조각일 때만 `{index, count}`. 있으면 전체 결과가 아니다 — 아래 「한 repo를 샤드로 나누기」 |
| `prepare_only` | `--prepare-only` 결과일 때만 `true`. 슬라이스 없음 -> 하네스 `run --slices`가 거부 |
| `prepared_cpg` | 준비 실행·`--cpg` 샤드만. 저장한 CPG의 `path`·`server_bytes`(Joern 보고)·`bytes`·`sha256`(클라이언트가 읽어 잰 값, 못 읽으면 null)·`cpg_seconds`(준비 실행의 CPG 생성 시간) |
| `attempts` | 체크포인트 사용 시 시도별 시작·종료·오류·카운터·서버 정보. 강제 종료 시 ended는 null |
| `measurement_notes`, `diagnostics`, `known_limitations` | 미측정 사유·관측 로그 및 실패 단계·알려진 한계 |

각 slice의 `id`는 정렬된 후보 목록에서 클러스터링 **전에** 발급하는 표시용 순번이다.
`candidate_key`는 후보 의미 정보의 SHA-256이며 CPG node ID를 사용하지 않는다. 같은 줄의
호출도 column과 AST 경로로 구분한다. 재개는 같은 절대경로의 원본을 요구한다.

각 slice는 `case`, `entry`, `candidate_key`, `members`, `absorbed`, `lines`, `code`,
`flow_count`, `truncated`, `analysis_kind`, `analysis`, `line_provenance`를 가진다.
`members`는 모든 구성 후보 키, `absorbed`는 흡수된 표시 ID다. `analysis`는 대표 후보의
원시 분석이고 합쳐졌다면 `member_analysis`에 구성원별 분석을 보존한다.
`line_provenance`는 `data_dependency`, `candidate_anchor`, `context`를 구분한다.
context는 구조와 선언을 합친 보충 범주이며 개별 이유를 더 세분해 측정하지 않는다.

| 계측 | Case A | Case B |
| --- | --- | --- |
| `analysis_kind` | `parameter_paths` | `backward_ddg` |
| `flow_count` | 보존한 Joern 경로 수 | null + 경로 미열거 사유 |
| 상세 계측 | 경로 수·절단 여부 | node_count·state_count·dependency_count·boundaries |
| 절단 집계 | `flows_truncated` | `backward_truncated` |

클러스터의 A flow_count는 후보별 보존 경로 수의 **합**이다. 고유 경로 수가 아니다. B의
노드 수를 flow_count로 변환하지 않는다. 클러스터의 truncated는 구성 후보 값의 OR다.

기존 필수 카운터인 files_seen/files_parsed/files_parse_failed, candidates_case_a/b,
slices_before/after_clustering, flows_truncated, queries_aborted, cpg_seconds, total_seconds를
유지한다. 미관측은 null과 사유다. CPG 파일 노드 수를 파싱 성공 수로 대체하지 않는다.
추가 파일·노드·위치 카운터도 유지한다. `candidates_reused`는 재사용한 완료 후보,
`candidate_stages_reused`는 저장 상태를 읽은 후보 수다. `queries_aborted`와 시간은 현재 시도의
관측이며 이전 오류를 지우지 않고 attempts에 남긴다. 완료 후보 통계는 재사용분을 포함한다.

## 체크포인트와 재개

SQLite WAL + synchronous FULL로 단계 payload·checksum·완료 상태를 한 트랜잭션에 저장한다.
OS 잠금으로 같은 체크포인트의 동시 쓰기를 막는다. 강제 종료 뒤 남는 .lock 파일은 잠금
자체가 아니므로 지우지 않는다. 정상 종료에서 DB와 잠금 handle을 명시적으로 닫는다.
WAL은 로컬 파일시스템에서 사용한다. 활성 DB를 공유할 때 단일 .db 파일만 복사하지 않는다.

`pending → flow_saved → context_saved → complete` 순서다. context 고정점 반복 중에도
검증된 줄 집합을 저장한다. flow_saved에서 재개하면 flow를 다시 요청하지 않는다. 완료 상태와
그에 포함된 카운터·오류·결과를 함께 복원한다.

체크포인트 실행은 실제 `--joern-version-command`를 요구한다. 재개할 때 원본·코드·분석 설정·
Joern 버전·overlay·EngineConfig·후보 manifest가 다르면 거부한다. 첫 후보 발견이 끝나기 전
실패한 실행은 해당 전처리부터 다시 수행한다. CPG는 재개 시 다시 생성하며 파싱 중간 재개는 없다.

```bash
# 같은 분석 인자에 --resume을 추가한다. 실패한 전용 Joern 서버는 먼저 재기동한다.
python tools/slicer/slicer.py --repo /work/repo --out /work/results/slices.json \
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /opt/joern/joern \
  --checkpoint /work/results/state.db --resume --max-flows-per-candidate 10 \
  --backward-depth 30 --backward-nodes 5000 --query-timeout-seconds 600 \
  --candidate-timeout-seconds 1200

# 분석 설정은 기록에서 읽는다. Joern 요청 없이 완료한 후보만 재조립한다.
python tools/slicer/slicer.py --repo /work/repo --out /work/results/exported.json \
  --checkpoint /work/results/state.db --export-only --export-dir /work/results/export
```

### 한 repo를 샤드로 나누기

한 repo 안의 후보는 원래 순차라 벽시계 시간이 후보 수에 비례한다. 후보별 질의(flows·backward·context)는
CPG를 읽기만 한다 → 여러 Joern 서버에 나눌 수 있다.

⚠ **샤드 결과가 샤딩 없는 run과 같다는 보장은 없다.** `--cpg` 없이는 샤드마다 CPG를 새로 만들고, Joern
데이터플로는 같은 입력에도 다른 경로를 고를 수 있다 — 샤딩 없는 재실행끼리도 마찬가지다. `--cpg`(아래 #368)면
CPG는 하나다.
- 잘린 Case A → [2026-09-15 보고서](experiments/harness/2026-09-15-joern-slicer-v2-atom.md) §4.5
  (절단된 Case A는 CPG를 다시 만들면 같은 경로를 고르지 않는다, Joern 4.0.605 · 재실행 2회)
- 안 잘린 Case A도 흔들린 예 → 2026-09-30 vast · 평가셋 main sample_33의 `TestKDFa`가 같은 코드
  두 번 실행끼리 flows 155 ↔ 139줄(#345 본문)
- 관측 → 2026-09-29 vast · Joern 4.0.636 · 평가셋 main sample_38 한 표본 · 후보 1,980: 8샤드 병합 ↔
  샤딩 없음 -> 1,965 동일(잘린 후보 포함), 다른 15는 전부 잘린 후보라 안 잘린 후보 중 다른 것 0. run ID 없음 · 원자료 저장소 밖

- `--shard I/N` → 후보 목록의 0부터 센 위치 p 중 `p % N == I`만 자른다(N ≥ 2, `--claim-db`면 아래 공유 대기열). 체크포인트 필수
- 샤드마다 전용 Joern 서버·자기 체크포인트. CPG는 `--cpg`가 없으면 샤드마다 새로 만들고, 있으면 준비 실행이
  저장한 하나를 읽는다(아래 #368)
- 샤드 자신의 `slices.json`은 일부라 `partial`(종료 코드 2)이고 `shard` 필드(`{index, count}`)를 단다.
  하네스 `run --slices`는 `shard` 필드가 있는 파일을 거부한다
- `tools/merge_slicer_shards.py`가 체크포인트를 합친다 → 실행 계약·EngineConfig·후보 manifest·준비 CPG
  기록(`prepared`)이 샤드끼리 다르면 거부(다른 CPG를 자른 것). 완료 안 된 후보가 있으면 종료 코드 2.
  같은 준비 체크포인트에서 복사한 샤드는 준비 실행의 시도 기록을 하나씩 들고 있다 -> 병합에선 한 번만
- 합친 체크포인트의 `--export-only`가 결과다. 클러스터링은 여기서 한 번. 샤드가 빠졌거나 중간에
  죽었으면 export가 `partial` + 「unfinished candidates」 — 그 후보는 샤딩 없는 run과 같이 anchor-only
  degraded 슬라이스로 나온다(`candidates_degraded`, #364). `--resume`은 여전히 그 후보를 다시 시도한다
- 병합 top-level은 첫 샤드의 발견 단계 기록을 쓰고 오류 목록만 모든 샤드의 합집합이다.
  슬라이싱 중 top-level `diagnostics`(예: flows 싱크 cap 도달)는 병합 export에 안 남는다 —
  각 슬라이스의 `analysis.sinks_capped`에는 남는다
- 샤드 실행부터 export까지 `slicer*.py`가 같아야 한다 — 코드 해시가 실행 계약이라 중간에 코드가
  바뀌면 `Checkpoint mismatch: run_contract`
- 샤드 수는 CPU·메모리로 정한다. 2026-09-29 8샤드 CPU 최대 약 47코어(30초 표본) · 메모리 미측정

⏵ #370 **한 번에 돌리기** -> `tools/slice_sharded.py --repo … --out … --work-dir … --joern … -- <분석 인자>`가 준비(#368)·서버 N개
동시 기동·샤드(#369 공유 대기열)·병합·export를 한 번에 한다. POSIX 전용(killpg)

- `--` 뒤 인자 -> 준비·모든 샤드에 그대로. export는 안 받는다(병합 체크포인트의 실행 계약에서 설정을 읽음).
  도구가 직접 다는 플래그(`--joern-endpoint`·`--checkpoint`·`--shard`·`--claim-db`·`--export-dir` 등, 약어·`=` 꼴 포함)는 거부
- 샤드 수 `--shards auto`(기본) = min(코어 ÷ `--cores-per-shard`, 메모리 ÷ `--memory-per-shard-gb`, 후보 ÷
  `--min-candidates-per-shard`, `--max-shards`), 최소 1. 어느 항이 정했는지 summary.json `decided_by`·`--dry-run`에 남는다
- 코어 -> CPU affinity와 cgroup 할당량(v2 `cpu.max`, v1 `cpu.cfs_quota_us`) 중 작은 쪽. `/proc/self/cgroup`의 자기 cgroup과
  조상까지 본다(컨테이너의 `nproc`은 호스트 코어를 셀 수 있다)
- 메모리 -> MemAvailable과 cgroup 한도(v2 `memory.max`, v1 `memory.limit_in_bytes`) 중 작은 쪽. cgroup 파일을 하나도
  못 읽으면 summary.json `memory.warning`과 stderr에 경고
- ⚠ 기본값은 전부 정한 값이지 잰 값이 아니다 -> `--cores-per-shard 4` · `--heap 20g` · `--memory-per-shard-gb` = heap + 4GB(JVM
  힙 밖 + slicer 프로세스 몫) · `--min-candidates-per-shard 20` · `--max-shards 32`. 위 단일 서버 예시의 `-Xmx16g`도 예시값
- ⚠ `auto`가 적은 고정 샤드보다 빠르다는 근거 없음. 비교는 하나뿐 -> #373 PR 본문의 30샤드 621초 ↔ 2샤드 664초. 같은 장비에서
  다른 8샤드 작업이 동시에 돌던 중이라 차이 43초(계산값)를 샤드 수 탓으로 못 돌린다
- `--wall-seconds`로 끊기면 순서상 못 닿은 후보가 degraded -> 그 순서를 `--priority-files`(아래)로 바꿀 수 있다
- `--wall-seconds` -> 샤드 서버가 다 뜬 뒤부터 잰다. 모든 샤드가 준비 체크포인트에서 시작하므로 끊긴 샤드가 있어도 병합이
  거부되지 않는다(끊긴 후보는 degraded). 생략하면 제한 없음
- 시작 전 포트가 비었는지 확인(남은 서버에 샤드가 붙는 것 방지). 서버가 뜨기 전에 죽으면 바로 실패하고 `joern.log` 경로를 낸다
- 종료 코드 -> 0 모든 후보 완료 · 3 `--out`은 썼지만 미완료 후보 있음(degraded, summary.json `merge`·`dead_shards`·
  `wall_stopped_shards`) · 1 실패(준비·서버·병합·export 실패, 또는 모든 샤드 죽음) · 2 사용법 오류 · 128+시그널
- 샤드 종료 2는 정상(샤드 결과는 항상 partial). 1이나 시그널 종료는 `dead_shards`. 완료 여부는 병합 카운터(`complete`/`candidates`)로 본다
- SIGTERM·SIGHUP -> 샤드·서버를 멈추고 summary.json을 쓴다. 실패 경로도 전부 summary.json을 쓴다. ⚠ 이 도구에 SIGKILL이면
  서버·샤드(각자 세션)가 남는다

⏵ #369 **공유 대기열** -> `--claim-db <파일>`(모든 샤드가 같은 SQLite 파일)을 주면 샤드가 `p % N` 대신 아직 아무도
안 가져간 후보를 하나씩 가져가 자른다 -> 느린 후보가 한 샤드에 몰려도 나머지 샤드가 남은 후보를 처리한다.
포이즌 3연속인 샤드는 더 가져가지 않고 멈춘다(남은 후보는 다른 샤드 몫). 병합은 그대로

- `--shard I/N`·`--checkpoint` 필수. `I/N`은 몫이 아니라 선점 주인 이름이다
- run마다 **새 파일**. 파일이 처음 생길 때 N을, 첫 샤드가 실행 서명·후보 목록 digest를 적는다 -> 다른 run이 남긴
  파일은 N이 다르면 열 때, 서명·후보가 다르면 자르기 전에 거부(남은 파일이 모든 후보를 「이미 가져감」으로 만드는 것을 막음)
- 호스트 로컬 파일시스템에 둔다. WAL이라 프로세스끼리 공유 메모리가 필요하고 네트워크·9p 마운트에선 깨진다.
  WAL로 못 바꾸면 결과 오류로 멈춘다
- 샤드 종료 코드 2는 정상이다(샤드 결과는 일부라 `partial`)
- 가져가고 죽은 샤드의 후보는 **같은 `I/N`의 `--resume`으로만** 돌아온다. 다른 번호의 샤드는 가져가지 않는다.
  임대 만료가 없다(살아서 느린 주인과 죽은 주인을 하트비트 없이 못 가르고, 둘이 같은 후보를 자르면 병합이 어긋남).
  안 살리면 export에서 degraded. `tools/merge_slicer_shards.py --claim-db <파일> ...`이 미완료 후보 키와 선점한
  샤드·시각을 stderr에 찍는다

```bash
# 샤드 i = 0..N-1, 모두 같은 claims.db (run마다 새 파일)
python tools/slicer/slicer.py --repo /work/repo --out /work/s0/slices.json \
  --joern-endpoint http://127.0.0.1:16243 --joern-version-command /opt/joern/joern \
  --checkpoint /work/s0/state.db --shard 0/4 --claim-db /work/claims.db
# 병합: 미완료가 있으면 키와 주인을 찍고 종료 코드 2 -> 그 샤드를 --resume
python tools/merge_slicer_shards.py --claim-db /work/claims.db /work/merged.db /work/s*/state.db
```

⏵ #377 (b) **우선 파일** -> `--priority-files <파일>`을 주면 엔트리나 앵커 하나라도 그 파일에 있는 후보를 먼저 자른다.
**처리 순서만 바꾼다. 끊기면 어느 후보가 전체 슬라이스인지가 바뀐다 -> 끊긴 표본에서 켠/끈 run은 비교하지 않는다**
- 목록 형식 -> 한 줄에 경로 하나. 저장소 기준 상대 경로 또는 `--repo` 아래 절대 경로. 후보 파일과 같은 정규화
  (`relative_file`) -> `./a.c`·`x//y.c`·역슬래시도 맞는다. UTF-8(BOM 허용). 빈 줄·`#`로 시작하는 줄 무시. 줄 끝 주석은
  없다(`#`도 파일 이름에 올 수 있음 -> `a.c # 메모`는 그 전체가 경로)
- 매칭 규칙 -> **정규화한 파일 문자열이 정확히 같아야** 한다. **대소문자를 구분**하고, 부분 문자열·basename·글롭·디렉터리·
  `file:line`은 **안 맞는다** -> 맞는 것은 **파일 하나하나**뿐. 안 맞는 경로는 조용히 무시된다(아래 `priority` 감사 기록을 본다)
- 목록의 출처 -> **과제 설명(Level 1 입력)에서만 만든다.** `patch.diff`·`error.txt`·수정본 저장소에서 파일 목록을 뽑으면
  **정답 누출**이고 그 run은 무효다([evaluation.md §4](evaluation.md)). `eval/leakage`는 프롬프트·run 산출물만 보므로 이
  입력 통로는 **스캔하지 않는다** -> 사람이 지킨다. 도구 자체는 어떤 목록이든 받는다(검증 안 함)
- 그대로인 것 -> 후보 집합. 층 안에서는 원래 순서, 후보 ID(`slice_NNNN`)도 원래 번호. 온전히 잘린 후보는 목록이 있든
  없든 같은 슬라이스
- 바뀔 수 있는 것 -> 어느 후보가 degraded로 남는가. 끊겼을 때(`--wall-seconds`·kill) 못 닿은 후보, 그리고 끝까지 돌아도
  poison 연속(`POISON_STOP_STREAK`) 뒤 후보(단일·`--shard`는 나머지를 degraded로, `--claim-db` 샤드는 그 샤드가 청구를 멈춤).
  둘 다 순서를 따르므로 실패 없이 끝까지 돈 run에서만 목록 유무와 무관하게 같은 슬라이스. **상한이 걸린 표본(끊김·poison)에서
  켠/끈 run을 비교하면 「차이 없음」이 아니라 「표본이 다름」이다** -> 비교하지 않는다
- **하네스(Scout)까지는 안 간다** -> 출력 `slices[]`는 **후보 ID 순**이고 우선 순서를 담지 않는다(아래 「우선 순서를
  읽을 곳」). 러너는 `(-signal_rank, file, line, symbol, slice_id)`로 **다시 정렬**해
  `candidate_max`(`config/budgets.yaml`)에서 자르므로 목록은 거기서 보이지 않는다. `RunRecord.slicer`에도 목록 사용 여부가
  안 남는다. 슬라이스가 `candidate_max`를 넘으면 전체로 잘린 우선 파일 슬라이스가 컷 밖에 남을 수 있다(실 run에서 걸리는지
  **미측정**). Scout도 그 순서를 따르게 하려면 슬라이스별 tier를 싣고 `runner._order_and_cut`이 읽는 **별도 PR**이 필요하다
  (열린 PR #378의 선택지 (b) — 미머지라 이 저장소에는 그 결함 본문이 아직 없다)
- 우선 순서를 읽을 곳 -> 체크포인트의 `candidates` 목록(병합 체크포인트도 그대로)과, 후보 발견을 **스스로 한** run
  (`--export-only`도 `--cpg` 샤드도 아닌 run)의 `candidate_manifest` 삽입 순서. ⚠ **발견 기록을 체크포인트에서 되읽은 결과의
  `candidate_manifest`에는 없다** -> `--export-only`(따라서 병합 뒤 export로 내는 모든 샤딩 run, `slice_sharded.py` 포함)와
  `--cpg` 샤드. 체크포인트가 그 기록(`baseline`)을 키 정렬로 저장하므로(`slicer_checkpoint.canonical`의 `sort_keys=True`)
  되읽은 manifest는 **`candidate_key`(SHA-256 hex) 순**이고 우선 순서와도 ID 순서와도 무관하다. `--export-dir`의
  `candidate-manifest.json`도 결과와 같은 순서다(#434)
- 순서가 체크포인트에 묶인다 -> 목록의 개수·sha256(**정렬한** 목록의 `slicer_checkpoint.digest` -> 목록 줄 순서와 무관)이
  `settings.priority_files`(실행 계약). 목록이 다른 샤드는 병합 거부(`Shard mismatch on run_contract`), 다른 목록으로
  `--resume`도 거부. `--prepare-only`에 주면 준비 체크포인트를 받은 샤드가 그 순서를 따른다(`slice_sharded.py`는 `--` 뒤
  인자로 둘 다에 넘김)
- ⚠ 샤드를 손으로 `--resume --cpg <파일> --shard I/N`으로 돌릴 때도 준비 때와 같은 `--priority-files`를 다시 준다. 빠뜨리거나
  다르면 실행 계약이 달라 `Checkpoint mismatch: run_contract (differs in: settings.priority_files)`로 거부 -> 새 체크포인트가
  아니라 **같은 목록을 다시 주는 것**이 해법이다
- 결과 `priority` -> 목록 파일 수 · 후보가 있는 파일 수 · 1층 후보 수 · 전체 후보 수 · 맞은/안 맞은 파일 이름(`matched_files`·
  `unmatched_files`, 각 최대 50개로 **사후 감사**용). 목록의 어느 파일도 후보와 안 맞으면 **stderr 한 줄** + `measurement_notes.
  priority_files`에 「기본 순서 그대로」, 일부만 안 맞으면 안 맞은 파일 수(run은 실패시키지 않음)
- `--export-only`와 같이 주면 거부(순서는 이미 체크포인트에 있음)
- ⚠ **발견 단계 상한이 우선 정렬보다 앞이다** -> `CASE_A_CANDIDATE_CAP`(`slicer_queries.py`)의 `.take`는 `collect()`·
  `prioritize()` **전에** Joern 안에서 자른다. 캡에 닿은 저장소에서는 목록 파일의 후보가 **아예 없을 수** 있고, 그러면 결과가
  오타와 **구별되지 않는다**(둘 다 `unmatched_files`에만 뜬다)
- ⚠ 목록이 틀리면 정답 후보가 뒤로 밀린다 -> lwan 한 표본에서 첫 GT 슬라이스 171 -> 297초, 느린 장비에서는 900초 예산에
  GT 슬라이스 4 -> 1(**미머지 PR #378의 관측 3**에서 온 수 · 2026-10-02 vast · run ID 없음 · 원자료 저장소 밖 · 여기서 재측정 안 함)

⏵ #368 **준비 한 번** -> `--prepare-only --cpg-out <파일>`이 CPG 생성과 후보 발견을 한 번 하고 그래프를 저장한다(아무
후보도 안 자름). 그 체크포인트를 샤드마다 복사해 `--resume --cpg <파일> --shard I/N`으로 돌리면 샤드는 저장소를 다시
파싱하지 않고(`importCpg`) 후보 발견도 건너뛴다 -> 모든 샤드가 같은 그래프·같은 후보 목록을 본다. 분석 인자는 준비
실행과 같게(실행 계약이 검사한다).
- 준비 실행의 `--out`은 슬라이스가 아니다(`prepare_only: true`, `slices: []`) -> 하네스가 거부
- 준비 실행은 새 `--checkpoint`·없는 `--cpg-out`만 받는다. 실패하면 둘 다 새 경로로 처음부터. 기존 CPG를 덮지
  않는다(돌고 있는 샤드가 읽는 중일 수 있음)
- 저장이 끝나면 클라이언트가 그 파일을 읽어 크기·SHA-256을 체크포인트 `prepared`에 남긴다. `--cpg` 샤드는 Joern에
  묻기 전에 그 기록이 있는지, 주어진 파일이 같은 크기·SHA-256인지 본다 -> 아니면 거부. 저장 중 죽은 준비 실행의
  체크포인트(후보는 있고 `prepared`는 없음)도 거부
- ⚠ 클라이언트가 Joern과 같은 파일시스템에서 `--cpg-out`·`--cpg`를 읽어야 한다. 못 읽으면 준비 실행은
  `bytes`·`sha256`을 null로 두고(미측정) 샤드는 그 체크포인트를 거부
- 샤드의 `counters.cpg_seconds`는 그 샤드의 `importCpg` 시간이다. 파싱 시간은 `prepared_cpg.cpg_seconds`
- 실 Joern 관측 → 2026-10-01 · vast · Joern 4.0.605 · libmspack: 준비 25초, 2샤드 11분, 후보 1,326/1,326, 슬라이스 212(#371 본문).
  run ID 없음 · 원자료 저장소 밖. 이 리뷰 반영(`prepared` 검증) 뒤로는 실 Joern으로 다시 돌린 기록 없음

```bash
# 준비 한 번 (서버 하나). --out은 준비 기록이지 슬라이스가 아니다
python tools/slicer/slicer.py --repo /work/repo --out /work/prep/prepare.json \
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /opt/joern/joern \
  --checkpoint /work/prep/state.db --prepare-only --cpg-out /work/prep/cpg.bin --max-flows-per-candidate 10
# 샤드 i = 0..N-1: 체크포인트를 복사해 각자 다른 서버에서
cp /work/prep/state.db /work/s0/state.db
python tools/slicer/slicer.py --repo /work/repo --out /work/s0/slices.json \
  --joern-endpoint http://127.0.0.1:16243 --joern-version-command /opt/joern/joern \
  --checkpoint /work/s0/state.db --resume --cpg /work/prep/cpg.bin --shard 0/4 --max-flows-per-candidate 10
```

```bash
# 샤드 i = 0..N-1, 각자 다른 서버. 분석 인자는 전부 같게
python tools/slicer/slicer.py --repo /work/repo --out /work/s0/slices.json \
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /opt/joern/joern \
  --checkpoint /work/s0/state.db --shard 0/4 --max-flows-per-candidate 10
python tools/merge_slicer_shards.py /work/merged.db /work/s0/state.db /work/s1/state.db \
  /work/s2/state.db /work/s3/state.db
python tools/slicer/slicer.py --repo /work/repo --out /work/results/slices.json \
  --checkpoint /work/merged.db --export-only
```

Joern HTTP timeout은 서버의 Scala 계산을 취소하지 않는다. 후보 하나의 실패
(OOM·StackOverflow·timeout·HTTP)는 `candidates_failed`에 세고 다음 후보로 계속한다(#219) —
한 후보가 run 전체를 버리지 않는다. ⚠ 단 `poisoned` 실패가 **연속 `POISON_STOP_STREAK`(3)회**면
멈춘다: 서버가 계산 중인 runaway 질의 뒤로 후보마다 timeout을 반복하므로, 그때는 전용 서버
재기동 후 resume이 답이다(자동 재기동은 안 한다 — 저장 지점에서 이어간다).
`--candidate-timeout-seconds`는 한 시도의
후보 처리 안에서 각 질의의 남은 시간도 제한한다. Python의 원문 조립·디스크 쓰기를 강제
취소하는 프로세스 상한은 아니다. heartbeat는 대기 중임을 뜻하며 진도 증가로 해석하지 않는다.

A의 reachableByFlows는 내부에서 결과를 먼저 모은다. 출력 cap은 한 batch의 heap 상한이
아니다. B도 방문 상태를 제한하지만 CPG 자체와 한 번의 DDG 이웃 조회 비용까지 제한하지는
않는다. JVM 자원과 프로세스 수명은 외부 실행 관리에서 제한한다.

## 소스 export와 실험 기록

`--export-dir`은 raw/*.c, numbered/*.txt, slices.json, candidate-manifest.json, MANIFEST.json,
SHA256SUMS를 쓴다. 이름은 Case·표시 ID·대표 symbol·원본 basename·줄·전체 후보 키로 구성한다.
raw의 코드에는 줄 번호를 삽입하지 않는다. numbered는 `file:line | 원문` 형식이다. 교차파일
slice의 전체 출처는 파일 내부 헤더와 manifest를 읽는다.

`--out`·`--checkpoint`·`--progress`(와 `--cpg-out`·`--cpg`)는 **`--export-dir` 바깥이어야 한다.** export는 그
디렉터리 아래를 전부 `os.replace()` 로 덮으므로, 열린 체크포인트를 거기에 두면 POSIX에서
SQLite 파일이 JSON으로 교체되고 **재개 상태가 조용히 사라진다.** 그중 하나라도 그 아래면
체크포인트를 열기 **전에** 거절한다. 서로 다른 것만으로는 부족하다 — 서로 다르면서
전부 export 디렉터리 안일 수 있다.

`--export-dir`이 **이미 파일로 존재하면** 같은 자리에서 거절한다. export는 첫 쓰기에서야
부모를 mkdir하므로, 그 오타는 **분석이 다 끝난 뒤에야** 터진다 — `--out`에는 올바른 `ok`
결과가 이미 쓰였는데 종료 코드는 `1`(실패)이 된다.

각 파일은 원자적으로 교체하고 manifest와 checksum 목록을 마지막에 쓴다. 디렉터리 전체의
원자적 교체는 아니므로 전달 전에 checksum을 검증한다. 재export 시 남은 옛 파일을 glob으로
읽지 말고 manifest의 파일 목록을 사용한다.

[HTTP 기록 래퍼](../tools/run_slicer_recorded.py)는 실제 payload·REPL 로그·질의·경과시간을
trace 디렉터리에 남긴다. 재시도마다 새 trace 경로를 준다.

**`trace/responses.jsonl`과 `--progress`는 관측 로그이지 재개 상태가 아니다.** 둘 다 append로
열어 쓰기만 하고 어느 경로도 다시 읽지 않는다 — 재개가 읽는 것은 SQLite 체크포인트뿐이다.
그래서 재개는 **새 trace 디렉터리**로 돌려도 완료 단계를 그대로 재사용한다. 이 둘이 없거나
지워져도 재개는 성립하고, 반대로 이 둘만 있고 체크포인트가 없으면 재개할 수 없다.
[중단 주입 래퍼](../tools/interrupt_after_flow_saved.py)는 그 래퍼를 감싸 첫 `flow_saved`
**커밋 직후** KeyboardInterrupt를 올린다 — 재개 동등성을 실서버에서 재는 수단이다. 저장 전에
중단하면 재개할 상태가 없으므로 원래 `save_stage`를 먼저 끝내고 그 반환에서 올린다.

```bash
python tools/run_slicer_recorded.py /work/results/trace-attempt-1 --repo /work/repo --out /work/results/slices.json
python tools/interrupt_after_flow_saved.py /work/results/trace-interrupt --repo /work/repo --out /work/results/slices.json --checkpoint /work/results/state.db
python -m pytest -q tests/test_slicer.py tests/test_slicer_v2.py
```

테스트는 HTTP mock, 실제 파일/SQLite, 별도 자식 프로세스 중단을 사용한다. Joern 서버를
CI에서 띄우지 않는다. [공개 fixture](../tests/fixtures/slicer_v2/main.c)와
[교차파일 함수](../tests/fixtures/slicer_v2/helper.c)는 별도의 실서버 정적 분석에도 사용한다.
같은 원본과 응답의 slices는 결정론적이다. 시간·시도 기록까지 같은 bytes라는 뜻은 아니다.
Joern 재파싱 후 결과 동등성은 별도 실측으로 확인한다.

기존 한계인 함수 내부 stack overflow 후보 누락, free alias 미해결, 함수 간 이중 해제 후보
누락은 유지된다. fuzzy parsing·매크로·동적 호출이 빠진 그래프를 완전한 그래프로 만들지는 않는다.

근거: [Joern data flow 단계](https://docs.joern.io/cpgql/data-flow-steps/),
[4.0.605 경로 API 구현](https://github.com/joernio/joern/blob/v4.0.605/dataflowengineoss/src/main/scala/io/joern/dataflowengineoss/language/ExtendedCfgNode.scala),
[4.0.605 backward slicer](https://github.com/joernio/joern/blob/v4.0.605/dataflowengineoss/src/main/scala/io/joern/dataflowengineoss/slicing/DataFlowSlicing.scala).

전용 Joern 서버에 대한 선택 실행 검증은 다음과 같다. C/C++ 입력은 분석만 하며 실행하지 않는다.
출력 디렉터리는 매번 새 경로여야 한다. 코드·입력 해시, 실제 CLI 결과와 종료 코드, Case B 원문,
새 CPG 반복 시 slices 동일성 및 flow 상한 검사를 보존한다.

```bash
python tools/probe/joern_slicer_live.py --endpoint http://127.0.0.1:16240 --out-dir /work/live-check-01
```

ATOM Joern 4.0.605에서 실행한 결과와 범위는 **보고서 셋에 나뉘어 있다.** v1 코드(`c4acdd2`)로
돌린 [실서버 검증](experiments/harness/2026-09-14-joern-slicer-live-atom.md)과
[공개 스모크 표본 3종](experiments/harness/2026-09-14-joern-slicer-smoke-samples-atom.md) 둘이
2026-09-14이고, 이 문서가 서술하는 slice-v2의
[실서버 검증](experiments/harness/2026-09-15-joern-slicer-v2-atom.md)이 2026-09-15이다.
⚠ **수치를 인용하기 전에 그 보고서의 메타 표에서 배포 코드 해시를 본다 — 보고서와 코드
판이 1:1이 아니다.** v1 둘은 같은 커밋이고, 스모크 표본은 실서버 보고서의 소규모 회귀와
`slicer.py`까지 같다(`80b26532…`). 정작 그 실서버 보고서 **안에서** zlib 전량이 다른
판(`ac94e235…`)으로 돌았고, slice-v2는 또 다르다(`0373767a…`).

HTTP 전송 방식은 [Joern 서버 문서](https://docs.joern.io/server/), 파싱 통계 해석은
[frontend Report 구현](https://github.com/joernio/joern/blob/4bb889d96ce972e2ded50d0d5765c514c1a032cf/joern-cli/frontends/x2cpg/src/main/scala/io/joern/x2cpg/utils/Report.scala)을 참고했다.
