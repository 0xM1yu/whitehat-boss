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
| `priority` | `--priority-files`를 줬을 때만. 목록 파일 수·후보가 있는 파일 수·1층 후보 수(우선 층의 크기 — `ba`에서는 앞에서 센 길이가 아니라 개수, 아래 「케이스 순서」)·전체 후보 수, 그리고 맞은/안 맞은 파일 이름(`matched_files`·`unmatched_files`, 각 최대 50개 — 사후 감사용). 아래 「우선 파일」 |
| `slices` | 완료한 후보를 클러스터링한 결과. 실패한 미완성 후보는 넣지 않음. ⚠ **`partial`과 `[]`의 조합은 두 가지를 담는다** — (가) CPG는 만들어졌고 슬라이스가 0건, (나) CPG 준비 중 StackOverflowError 로 **CPG 자체가 없다**. `status` 문자열만으로는 안 갈린다. **가르는 것은 `joern_engine_config`다** — (나)는 `null`이다(후보 카운터도 `null`). 비교표에 넣을지 정하는 쪽은 `status != "failed"` 가 아니라 그것을 본다 |
| `shard` | `--shard I/N` 조각일 때만 `{index, count}`. 있으면 전체 결과가 아니다 — 아래 「한 repo를 샤드로 나누기」 |
| `prepare_only` | `--prepare-only` 결과일 때만 `true`. 슬라이스 없음 -> 하네스 `run --slices`가 거부 |
| `prepared_cpg` | 준비 실행·`--cpg` 샤드만. 저장한 CPG의 `path`·`server_bytes`(Joern 보고)·`bytes`·`sha256`(클라이언트가 읽어 잰 값, 못 읽으면 null)·`cpg_seconds`(준비 실행의 CPG 생성 시간) |
| `attempts` | 체크포인트 사용 시 시도별 시작·종료·오류·카운터·서버 정보. 강제 종료 시 ended는 null |
| `measurement_notes`, `diagnostics`, `known_limitations` | 미측정 사유·관측 로그 및 실패 단계·알려진 한계 |

각 slice의 `id`는 정렬된 후보 목록에서 클러스터링 **전에** 발급하는 표시용 순번이다(정렬은 `--case-order`를 따른다 -> 아래 「케이스 순서」).
`candidate_key`는 후보 의미 정보의 SHA-256이며 CPG node ID를 사용하지 않는다. 같은 줄의
호출도 column과 AST 경로로 구분한다. 재개는 같은 절대경로의 원본을 요구한다.

각 slice는 `case`, `entry`, `candidate_key`, `members`, `absorbed`, `lines`, `code`,
`flow_count`, `truncated`, `analysis_kind`, `analysis`, `source_sink`, `line_provenance`를 가진다.
`members`는 모든 구성 후보 키, `absorbed`는 흡수된 표시 ID다. `analysis`는 대표 후보의
원시 분석이고 합쳐졌다면 `member_analysis`에 구성원별 분석을 보존한다.
`line_provenance`는 `data_dependency`, `candidate_anchor`, `context`를 구분한다.
context는 구조와 선언을 합친 보충 범주이며 개별 이유를 더 세분해 측정하지 않는다.

`source_sink`는 슬라이서가 source·sink로 둔 위치다(#429, GT source/sink 대조용).
취약 쌍이라는 판정이 아니라 분석의 시작·끝 지점이다.

| | Case A | Case B |
| --- | --- | --- |
| `basis` | `flow_endpoints` | `backward_boundaries` |
| `sources` | 보존 경로의 머리 = 후보 함수 파라미터 (`kind: parameter`) | 역추적이 멈춘 경계 (`kind` = 경계 사유) |
| `sinks` | 보존 경로의 끝 = sink 인자 (`kind: flow_end`) | 후보 호출 자체 = `entry` (`kind: candidate_call`) |
| `pairs` | 경로별 source -> sink 쌍 | 비움 (경로 미열거) |

- 각 위치는 `{file, line, kind}`이고 `lines`와 같은 상대경로다. 원문에 없는 위치는 버리고 `unmapped`에 센다.
- 절단된 A는 보존한 경로의 끝점만 든다.
- 절단된 B: 상한(`backward_depth`·`backward_states`)에서 버린 노드는 `sources`에 없다. 그 부모도 `dependency_root`가 안 된다. `call_depth`로 멈춘 호출에도 그 사유가 안 붙는다. 절단은 `truncated`·`analysis.truncation_reasons`로만 보인다.
- A의 `endpoints`는 선택 필드다. 조회가 `Try`로 계산 -> 실패하면 `endpoints` 대신 `endpoints_error`(원문은 `analysis`에 남음). 이때 `basis: endpoints_unavailable`·전부 비움 = 미기록. `flow_endpoints`의 빈 목록(0쌍 기록)과 다르다. 슬라이스 자체는 degraded가 아니다.
- degraded 슬라이스: A는 `basis: none`·전부 비움, B는 `basis: candidate_only`·sink만 든다.
- 클러스터는 구성원의 합집합이다. 구성원끼리 `basis`가 다르면 `mixed`다(구성원별 원천은 `member_analysis`).

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
- `--wall-seconds`로 끊기면 순서상 못 닿은 후보가 degraded -> 그 순서를 `--priority-files`·`--case-order`(아래)로 바꿀 수 있다
- `--wall-seconds` -> 샤드 서버가 다 뜬 뒤부터 잰다. 모든 샤드가 준비 체크포인트에서 시작하므로 끊긴 샤드가 있어도 병합이
  거부되지 않는다(끊긴 후보는 degraded). 생략하면 제한 없음
- 시작 전 포트가 비었는지 확인(남은 서버에 샤드가 붙는 것 방지). 서버가 뜨기 전에 죽으면 바로 실패하고 `joern.log` 경로를 낸다
- 종료 코드 -> 0 모든 후보 완료 · 3 `--out`은 썼지만 미완료 후보 있음(degraded, summary.json `merge`·`dead_shards`·
  `wall_stopped_shards`) · 1 실패(준비·서버·병합·export 실패, 또는 모든 샤드 죽음) · 2 사용법 오류 · 128+시그널
- 샤드 종료 2는 정상(샤드 결과는 항상 partial). 1이나 시그널 종료는 `dead_shards`. 완료 여부는 병합 카운터(`complete`/`candidates`)로 본다
- SIGTERM·SIGHUP -> 샤드·서버를 멈추고 summary.json을 쓴다. 실패 경로도 전부 summary.json을 쓴다. ⚠ 이 도구에 SIGKILL이면
  서버·샤드(각자 세션)가 남는다
- 서버·샤드는 **프로세스 그룹째, 한꺼번에** 멈춘다 -> 모든 그룹에 SIGTERM -> 그룹이 다 빌 때까지 최대 `STOP_GRACE_SECONDS`(10초, 정한 값 — 그룹마다가 아니라 모두에게 한 번) ->
  남은 그룹에 SIGKILL -> 산 프로세스가 없는지 확인(최대 `KILL_VERIFY_SECONDS` 10초, 정한 값). `grace=0`이면 SIGTERM 없이 바로 SIGKILL이다 — `--restart-on-deadline`의
  재시작이 쓴다(버려진 질의를 아직 계산하는 서버는 아래 관측처럼 SIGTERM에 안 나간다. SIGTERM이 안 갔다는 것을 테스트가 고정한다). SIGKILL 뒤에도 남은 pid는 stderr 경고와 summary.json
  `unstopped_pids`(없으면 키가 없다 — 준비 서버 · 벽시계로 멈춘 샤드 · 끝의 서버 · `main()`의 마지막 정리, 그리고 `--restart-on-deadline`의 옛 슬라이서·서버와 안 뜬 새 서버(아래), 여섯 정지 지점 모두에서 테스트가 고정한다)로만 알리고 **종료 코드는 안 바뀐다**.
  `joern`은 JVM을 자식으로 띄우는 `sh`라서 런처만 기다리면 JVM이 남는다 -> 질의를 돌리는 JVM은 SIGTERM에 안 나간다(셧다운 훅이 그 질의가 끝날 때까지 못 끝난다 — 훅은
  scala-repl-pp 0.6.4의 `EmbeddedRepl.shutdown`이고 Joern 저장소 밖에 있다. jstack 원문은 남기지 않았다). 2026-10-03 E1(#465)에서 서버 8개가 run보다 오래 남았다.
  관측 -> dev 박스 2026-10-03 · 실 Joern 4.0.605 · 힙 2g · 서버 단독 · **원본 로그는 못 남겼다**(박스가 지워졌다. 터미널 출력을 옮겨 적은 `sigterm_probe.out.txt` ·
  `stop_live.out.txt`가 저장소 밖에 있고 옮겨 적은 것이라고 적혀 있다): 유휴 서버는 SIGTERM 0.5 · 0.5 · 0.7초에 그룹이 비었고(3회), 질의(`Thread.sleep(120000)`)를 돌리는
  서버는 30초 뒤에도 JVM이 살아 있다가 SIGKILL 0.01초에 사라졌다(1회). 이 도구의 정지를 실 Joern에 건 확인(같은 박스·날짜·힙, 각 1회)은 **e6d3f1a의 `stop()`**(프로세스마다 스레드가 확인했다 —
  지금 코드가 아니다)으로 유휴 0.88초, 질의 중인 서버 10.02초(유예 10초 뒤 SIGKILL)에 `[]`이고 남은 프로세스가 없었다. ⚠ **지금 코드(`_terminate` · `_wait_gone`, 폴러 하나)는 실 Joern에
  건 적이 없다 — 미실측**(시험용 스텁 서버로만 쟀다. 아래). 위 시간들은 전부 좀비를 산 것에서 뺀 확인으로 쟀다 -> 마지막 스레드를 기다리는 아래 규칙으로는 더 길다
  (SIGTERM 쪽은 다시 못 쟀다 — 박스가 없다. SIGKILL 쪽은 아래: 몇백 밀리초가 더해졌다). 유예 10초는 이 유휴 시간(≤ 1초 안팎)보다 훨씬 크게 잡은 정한 값이다
- 한 번의 `/proc` 훑기로 모든 그룹을 보고(0.1초 쉬고 다시 훑는다) **유예 하나를 모두가 나눠 쓴다**. 옛 판(프로세스마다 스레드가 `/proc`을 다시 훑었다 — 7143a61)은
  SIGTERM을 무시하는 시험용 서버 8 · 32개를 `/proc`에 프로세스가 1,030 · 1,076개일 때 12.5 · 18.5초, 3,023 · 3,071개일 때 16.5 · 31.1초에 멈췄다(10초가 유예). 고친 판은 같은
  설정에서 10.0 · 10.1 · 10.1 · 10.2초(프로세스 1,029 · 1,077 · 3,029 · 3,077개) — 유예에 0.1~0.2초다. **4점 · 각 1회라 서버 수와 `/proc` 크기에 안 따른다고 말할 표본은 아니다.**
  관측 -> 로컬 WSL2 2026-10-04 · 시험용 스텁 서버(실 Joern 아님) · 각 1회 · 원본 로그 `stopbench/before_7143a61.log` · `after_poller.log`와 측정 스크립트(저장소 밖).
  테스트가 시간(< 0.6 × N × 유예)과 훑은 횟수(틱당 한 번)를 고정한다 -> 한계: N=8 하나, 상한이 유예의 4.8배라 서버마다 유예를 따로 쓰는 판(8 × 유예)은 잡지만 그보다 덜 늘어나는 판은
  통과하고, `/proc` 크기는 테스트가 안 바꾼다
- 「산 프로세스」는 `/proc`에서 센다 -> 아직 안 거둔 좀비는 산 것이 아니지만 **다른 스레드가 아직 끝나는 중인 좀비 리더는 산 것이다**(`/proc/<pid>/task`에 리더 말고 항목이 있다) —
  그 스레드들이 소켓을 쥐고 있다. `/proc/<pid>/stat`은 바이트로 읽어 `errors="replace"`로 푼다(커널이 15바이트에서 자른 프로세스 이름이 다바이트 문자 중간에서 끊기면 UTF-8이 아니다 —
  못 읽는 것을 「없다」로 읽으면 그룹이 비었다고 보고 SIGKILL 없이 끝난다). `/proc`이 이 프로세스의 pid 네임스페이스가 아니면(`/proc/self`가 우리 pid를 안 가리킨다 — 호스트 `/proc`을 둔 채
  pid 네임스페이스만 가른 샌드박스) 그 `/proc`에는 우리 그룹이 없으므로 쓰지 않고 `killpg(그룹, 0)`으로 센다(안 거둔 좀비도 구성원으로 센다 — 남았다고 과대 보고하는 쪽이다).
  관측 -> 같은 박스·날짜·힙 2g·실 Joern·3회(원본 로그 없음 — `linger_probe.out.txt`는 옮겨 적은 것): SIGKILL 뒤 포트가 연결을 받은 시간이 0.57 · 0.55 · 0.15초(첫 거부까지)였고
  좀비 리더만 보던 판의 정지 호출(그 판의 `stop(grace=0)`, 지금은 `stop_all(…, grace=0)`)은 그 사이인 5 · 4 · 115밀리초에 돌아왔다. 이 규칙이 **main에서** 막는 것은 연속한 두 run의
  `ensure_ports_free`(bind)가 죽은 서버의 아직 안 닫힌 포트에서 최대 0.57초 거부하는 것이다(연결을 받던 시간으로 본 추정 — bind 거부는 따로 안 쟀다). 이 브랜치의 `--restart-on-deadline`은
  같은 포트에 새 서버를 띄우며 `wait_servers`(연결만 본다)가 죽어 가는 서버를 새 서버로 착각할 수 있는 경로를 더하므로 정지는 마지막 스레드가 끝날 때까지 기다린다. 고친 뒤 같은 확인(같은 박스·날짜·3회,
  **f74d0a5의 `stop(grace=0)`** — 지금 코드는 실 Joern 미실측) -> 257 · 282 · 394밀리초에 돌아왔고 반환 뒤에 연결을 받은 적이 없다(시험용 서버: 메인 스레드가 먼저 끝나고 스레드가 소켓을 쥔 프로세스)

- `--restart-on-deadline`(기본 꺼짐 — 꺼짐이면 동작도 summary.json도 그대로다) -> 샤드의 질의가 slicer의 클라이언트 데드라인(`--` 뒤의
  `--query-timeout-seconds`, 또는 `--candidate-timeout-seconds`가 남긴 시간 중 짧은 쪽 — 둘 다 없으면 데드라인이 없어 이 옵션은 아무것도 안 한다)에
  걸리면 그 샤드의 slicer와 서버를 SIGKILL하고, 같은 `I/N`을 새 서버에서 `--resume --cpg`로 다시 띄워 **다음 후보부터** 잇는다. 근거 -> 데드라인은 서버의
  계산을 안 멈추고(아래 「Joern HTTP timeout은 …」) 서버는 질의를 하나씩 받으므로 그 샤드의 뒤따르는 질의가 전부 그 뒤에 줄을 선다. 관측 -> dev 박스
  2026-10-03 · libxml2 · Joern 4.0.605 · 64bd566 슬라이서 · 데드라인 60초 · 샤드 하나(2샤드 claim 큐의 0번): 후보 셋이 연달아 60초에 걸렸고(포이즌
  3연속으로 그 샤드가 멈췄다) 서버 CPU는 내내 높았다(246초 평균 ≈ 18.9코어, 계산값). 클라이언트를 죽인 뒤에도 `1+1` 질의가 90.5초 동안 답이 없었다.
  원자료 저장소 밖 · 단일 실행
- 트리거 -> 샤드의 `--progress` 로그(`shardI/progress.jsonl`)에 새로 붙은 `candidate_failed`의 `error`가 `Query deadline exceeded` 또는
  `HTTP request failed: timed out`으로 시작할 때(`DEADLINE_ERRORS`; 둘 다 `JoernClient`가 만든다 — 뒤의 것은 `urlopen`의 소켓 타임아웃이 같은 값이라 데드라인
  스레드와 거의 동시에 울릴 수 있는 경우로, 코드 판독이고 관측된 것은 앞의 것뿐이다). 테스트가 실제 slicer가 데드라인에서 쓰는 이벤트로 앞의 문구를 맞춘다.
  `Candidate deadline exceeded`(질의를 보내기 전에 후보 예산이 다 된 것)는 서버에 남은 계산이 없으므로 트리거가 아니다. 메인 루프가 `POLL_SECONDS`(5초)마다 본다
- 다시 띄울 때 -> ① 그 샤드의 slicer와 서버를 그룹째 SIGKILL(위 `stop_all(…, grace=0)`) ② 죽은 서버의 `workspace/`를 지운다(`importCpg`가 CPG를 거기에 복사한다 —
  8.7MB CPG에 서버 하나의 workspace가 17MB였다, `cpg.bin`·`cpg.bin.tmp` 두 벌) ③ 걸린 후보 키를 `shardI/skip_candidates.txt`에 적는다(재시작마다 늘어난다)
  ④ 새 서버를 `shardI/restartN/`에서 같은 포트·힙으로 띄운다(여러 샤드가 한꺼번에 걸리면 서버는 함께 뜬다) ⑤ 같은 `I/N`·`--claim-db`·체크포인트로
  `--resume --cpg <저장한 CPG> --skip-candidates shardI/skip_candidates.txt`를 실행한다(로그는 `slicer.N.log`, 진행 로그는 같은 파일에 이어 붙는다).
  샤드가 하나(`--shards 1`, claim 큐 없음)여도 같다. 죽인 시도는 체크포인트 `attempts`에 `ended: null`로 남는다(강제 종료와 같다)
- ⚠ **`--skip-candidates`가 없으면 「다음 후보」가 아니다** -> 같은 `I/N`의 `--resume`은 자기 선점을 다시 가져오고, 질의가 실패한 후보는 체크포인트에 저장 단계가
  없어서 걸렸던 후보를 첫 후보로 또 자르고 데드라인을 한 번 더 태운다. 관측 -> 같은 박스·날짜의 서버를 죽이고 `--resume --cpg`로 다시 띄운 두 번째
  프로세스의 첫 질의가 첫 번째가 걸렸던 후보였고 60초를 다시 태웠다(1회)
- `slicer.py --skip-candidates <파일>` -> 후보 키 한 줄에 하나(빈 줄·`#` 줄 무시, BOM 허용). 이 프로세스는 그 후보에 질의하지 않는다 — 체크포인트에 이미
  complete면 그대로 쓰고, 아니면 포이즌 연속 뒤와 같은 앵커만의 degraded 슬라이스를 내며 **저장하지 않는다**. 선점은 그 샤드의 것으로 남고, 결과 `error`에
  안 자른 개수가 적히고(그래서 `ok`로 읽히지 않는다) 진행 로그에 `candidate_skipped`가 남는다. 실행 계약에 안 든다(`--shard`처럼 어떤 후보를 자르느냐이지
  어떻게 자르느냐가 아니다). `--export-only`·`--prepare-only`와는 효과가 없어 거부. 이 도구는 이 플래그를 직접 달므로 `--` 뒤에 쓰면 거부한다
- 걸린 후보의 결과 -> 체크포인트에 저장되지 않고 선점은 그 샤드에 남는다 -> 병합 export에서 안 끝난 후보라 degraded(옵션을 끈 run과 같다). 목록 없는
  `--resume`이 나중에 그 후보를 다시 자른다. ⚠ **옵션을 켠 run과 끈 run은 같은 슬라이스를 내지 않는다** -> 끈 run은 걸린 서버 뒤에 줄 선 후보가 연달아
  걸려 degraded가 되고(포이즌 3연속이면 그 샤드가 청구를 멈춘다) 켠 run은 그 후보를 새 서버에서 자른다. 우선 파일 목록(위)과 같이 **켠/끈 run을 같은 표본의
  비교로 보지 않는다**
- 한도 -> `--max-restarts-per-shard`(기본 5, 정한 값): 한 샤드가 그만큼 다시 떴으면 그 뒤로는 그대로 둔다(걸려도 안 건드린다, summary.json
  `restart.limit_hit`에 샤드 번호). 그리고 `--wall-seconds`가 `RESTART_MIN_WALL_SECONDS`(60초, 정한 값)보다 적게 남았으면 다시 안 띄운다 — 작은 그래프에서 kill부터
  그 샤드의 다음 질의까지 ≈ 16초였다(아래). 새 서버가 안 뜨면(또는 옛 서버가 SIGKILL 뒤에도 남으면) 그 샤드는 죽은 채로 `dead_shards`에 들고 이유가
  `restart.errors`에 남는다(남은 pid는 어느 쪽이든 `unstopped_pids`에도 든다)
- 재시작 비용 -> 관측 -> 같은 박스·날짜·libxml2(CPG 8.7MB)·힙 10g·1회: 새 서버가 듣기까지 5.74초 + 새 slicer 프로세스 시작에서 첫 질의까지
  10.15초(프로세스 시작·`joern --version`·체크포인트 열기를 합쳐 6.05초, 저장한 CPG의 `importCpg` 1.30초, 인벤토리 0.67초, 라이브러리 정의 1.91초) =
  약 15.9초(두 값의 합, 계산값). 여기에 kill이 끝나기를 기다리는 시간이 더해진다 -> 그 실험은 0.01초로 쟀지만 스레드가 남은 좀비 리더를 뺀 옛 기준이었고, 고친
  기준(위)으로는 힙 2g 서버 3회에 0.26 · 0.28 · 0.39초였다(힙 10g은 미측정). 큰 그래프의 `importCpg`와 부하가 있는 박스의 값도 미측정.
  근거 -> 이 박스의 E2 노트(저장소 밖)
- 라이브 비교(옵션 끔 ↔ 켬) -> 관측 -> dev 박스 2026-10-03 · 코드 2a7384e · libxml2(FT 우선 파일 10줄 전체) · 4샤드 · 힙 8g · `--wall-seconds 600` · 데드라인 60초 · 각
  1회 · 박스는 다른 에이전트 작업과 공유(옵션 끔은 부하 6에서, 켬은 35에서 시작). **둘 다 완료 0/6,048.** 끔: 샤드마다 후보 3개가 연달아 60초에 걸리고 포이즌
  3연속으로 206초에 스스로 끝났다(총 272초, 시작한 후보 12개 전부 걸림). 켬: 재시작 20회(샤드당 5 = 한도 · 실패 0 · 남은 프로세스 0), 시작한 후보 32개 중 28개가
  60초에 걸렸고(나머지 4개는 벽시계에 끊겼다) 616초에 벽시계로 끝났다 · 재시작 배치 시각 75 · 158 · 242 · 347 · 436초, kill부터 새 slicer 실행까지
  8.3 · 8.8 · 9.9 · 13.5 · 18.8초. 해석 -> 이 표본은 우선 파일 순서의 앞쪽 후보가 전부 무거워(첫 12개는 E1의 첫 질의와 같고 FT 원 run도 0건이었다) 옵션이 「3번 시도하고 멈춤」을
  「벽시계까지 28번 시도」로 바꿨을 뿐 완료를 늘리지 못했다. **가벼운 후보가 뒤따르는 표본에서의 이득은 미측정**이고 60초는 이 확인용 압박 설정이지 운영값이 아니다.
  원자료 저장소 밖
- summary.json `restart`(옵션을 켠 run에만 있다) -> `max_per_shard` · `per_shard`(샤드별 재시작 수) · `total` · `events`(`shard`·`at_seconds`(샤드 시작 뒤 초)·
  `candidates`(그때 목록에 더한 키)·`seconds`(kill부터 새 slicer 실행까지)) · `limit_hit` · `errors`

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
- **하네스(Scout)까지는 안 간다** -> 출력 `slices[]`는 **후보 ID 순**이고 우선 순서를 담지 않는다(우선 순서는
  `candidate_manifest`의 삽입 순서에만 남는다). 러너는 `(-signal_rank, file, line, symbol, slice_id)`로 **다시 정렬**해
  `candidate_max`(`config/budgets.yaml`)에서 자르므로 목록은 거기서 보이지 않는다. `RunRecord.slicer`에도 목록 사용 여부가
  안 남는다. 슬라이스가 `candidate_max`를 넘으면 전체로 잘린 우선 파일 슬라이스가 컷 밖에 남을 수 있다(실 run에서 걸리는지
  **미측정**). Scout도 그 순서를 따르게 하려면 슬라이스별 tier를 싣고 `runner._order_and_cut`이 읽는 **별도 PR**이 필요하다
  (열린 PR #378의 선택지 (b) — 미머지라 이 저장소에는 그 결함 본문이 아직 없다)
- 순서가 체크포인트에 묶인다 -> 목록의 개수·sha256(**정렬한** 목록의 `slicer_checkpoint.digest` -> 목록 줄 순서와 무관)이
  `settings.priority_files`(실행 계약). 목록이 다른 샤드는 병합 거부(`Shard mismatch on run_contract`), 다른 목록으로
  `--resume`도 거부. `--prepare-only`에 주면 준비 체크포인트를 받은 샤드가 그 순서를 따른다(`slice_sharded.py`는 `--` 뒤
  인자로 둘 다에 넘김)
- ⚠ 샤드를 손으로 `--resume --cpg <파일> --shard I/N`으로 돌릴 때도 준비 때와 같은 `--priority-files`를 다시 준다. 빠뜨리거나
  다르면 실행 계약이 달라 `Checkpoint mismatch: run_contract (differs in: settings.priority_files)`로 거부 -> 새 체크포인트가
  아니라 **같은 목록을 다시 주는 것**이 해법이다
- 결과 `priority` -> 목록 파일 수 · 후보가 있는 파일 수 · 1층 후보 수(우선 층의 크기. `ba`에서는 앞에서 센 길이가 아니라 개수다 -> 「케이스 순서」) · 전체 후보 수 · 맞은/안 맞은 파일 이름(`matched_files`·
  `unmatched_files`, 각 최대 50개로 **사후 감사**용). 목록의 어느 파일도 후보와 안 맞으면 **stderr 한 줄** + `measurement_notes.
  priority_files`에 「기본 순서 그대로」, 일부만 안 맞으면 안 맞은 파일 수(run은 실패시키지 않음)
- `--export-only`와 같이 주면 거부(순서는 이미 체크포인트에 있음)
- ⚠ **발견 단계 상한이 우선 정렬보다 앞이다** -> `CASE_A_CANDIDATE_CAP`(`slicer_queries.py`)의 `.take`는 `collect()`·
  `prioritize()` **전에** Joern 안에서 자른다. 캡에 닿은 저장소에서는 목록 파일의 후보가 **아예 없을 수** 있고, 그러면 결과가
  오타와 **구별되지 않는다**(둘 다 `unmatched_files`에만 뜬다)
- ⚠ 목록이 틀리면 정답 후보가 뒤로 밀린다 -> lwan 한 표본에서 첫 GT 슬라이스 171 -> 297초, 느린 장비에서는 900초 예산에
  GT 슬라이스 4 -> 1(**미머지 PR #378의 관측 3**에서 온 수 · 2026-10-02 vast · run ID 없음 · 원자료 저장소 밖 · 여기서 재측정 안 함)

⏵ **케이스 순서** -> `--case-order {ab,ba}`(기본 `ab`)는 후보를 자르는 순서의 첫 열쇠를 정한다. 후보 정렬 키의 첫 칸이 case라서(`slicer_candidates.collect`)
`ab`(지금까지의 순서)는 **Case A 전부 → Case B 전부**, `ba`는 **Case B 전부 → Case A 전부**다. 각 case 안의 순서(파일·줄·심볼·selector)는 그대로다.
⚠ `--priority-files`를 같이 주면 `ab`는 **층 먼저**, `ba`는 **케이스 먼저**다 -> `ab`: 우선 A → 우선 B → 나머지 A → 나머지 B(#377 (b)가 낸 순서 그대로). `ba`: 우선 B → 나머지 B → 우선 A → 나머지 A(어떤 Case A도 Case B보다 먼저 돌지 않는다). 아래 「`--priority-files`와 같이 주면」.
**후보 집합은 그대로고 처리 순서가 바뀐다. 그 순서로 ID를 새로 매기므로 ID·`slices[]` 순서·`absorbed`·export 파일 이름이 같이 바뀐다(아래 「바뀌는 것」).
끊기거나(`slice_sharded.py --wall-seconds`·kill) poison 연속(`POISON_STOP_STREAK`)이 나면 어느 후보가 degraded로 남는지도 순서에 따라 달라진다 -> 그런 표본에서 `ab` run과 `ba` run은 비교하지 않는다**(「우선 파일」과 같은 이유)
- 왜 있나 -> `ab`에서는 모든 Case A가 모든 Case B보다 먼저 돈다(`--priority-files`가 없을 때). `slice_sharded.py`의 `--wall-seconds`가 Case A를 자르다 다 쓰이면 Case B는 하나도 못 닿고 degraded(앵커만)로 남는다.
  `--priority-files`가 있으면 `ab`는 층 먼저라 우선 B가 나머지 A보다 앞이지만 우선 A 뒤다 -> 우선 A가 예산을 다 쓰면 우선 B도 못 닿는다. `ba`는 우선 파일이 있어도 **모든 B가 어떤 A보다 먼저**다.
  **`ba`가 정답 관련 슬라이스를 얼마나 더 온전히 자르는지는 미측정이다 -> 그래서 기본값은 `ab`다**(기본값 변경은 측정 뒤의 별도 변경)
- 그대로인 것 -> 후보 집합과 수 · `candidate_key`(순서와 무관한 해시) · 온전히 잘린 후보의 슬라이스(클러스터링 전의 줄·코드·분석)
- ⚠ 클러스터링 뒤의 슬라이스까지 같은 것은 **후보가 10,000개 미만일 때뿐이다** -> `cluster()`는 크기가 같은 슬라이스를 ID **문자열**로 가르고
  (`slicer_source.py`의 정렬 키와 마지막 `sorted`) `slice_10000`은 `slice_9999`보다 앞에 정렬된다. 9,999번까지는 문자열 순서가 숫자 순서라 `ab`·`ba`가 같은 묶음·같은 대표를 낸다(번호만 다르다). `--priority-files`의 분할은 ID를 안 건드리므로(`collect()`가 매긴 그대로) 이 전제는 우선 파일 유무·층 먼저/케이스 먼저와 무관하다.
  10,000개 이상이면 **같은 후보 둘이 순서에 따라 다른 대표로 묶일 수 있다** -> 줄 집합이 같은 후보 X·Y를 `cluster()`에 직접 넣어 확인: X·Y가 9,999·10,000번이면 대표는 Y, 10,000·10,001번이면 대표는 X.
  `slices[]`도 번호 순이 아니라 ID 문자열 순이다. **이론이 아니다** -> FT 실행 아카이브(`ft_level1_vuln_slices_20261002.zip`의 샘플별 `summary.json`)에서 후보 수가 적힌 샘플 283개 중 91개가 10,000개 이상이다
  (최대 61,944 · 예 `hs-b9c6358375fb9ae6`(hhvm) 12,874 · 2026-10-03 로컬 집계 · run ID 없음 · 원자료 저장소 밖). 동률을 가르는 방식 자체는 바꾸지 않았다 — 기존 출력이 달라진다
- 바뀌는 것 -> 처리 순서 · 체크포인트의 `candidates` 순서 · **후보 ID `slice_NNNN`**: 정렬 **뒤에** 발급하므로 `ba`면 Case B가 앞 번호를 받는다.
  ID를 담은 것이 전부 따라 바뀐다 -> 출력 `slices[]`의 순서(ID 순이라 `ba`면 B 슬라이스가 앞에 온다) · `absorbed`의 ID · export 파일 이름(`raw/`·`numbered/`의 `<case><번호>_…`) · `candidate_manifest`와 `MANIFEST.json`의 `id`.
  같은 후보가 `ab` run과 `ba` run에서 다른 ID를 가지므로 두 run을 맞댈 때는 `id`가 아니라 `candidate_key`로 잇는다
- `--priority-files`와 같이 주면 -> `prioritize()`가 목록을 안정 분할하는데 **무엇을 나누는지가 순서마다 다르다**(`slicer_candidates.PRIORITY_PER_CASE`).
  **`ab`는 층 먼저**: `collect()` 목록 전체를 우선 층·나머지 층으로 나누므로 **우선 A → 우선 B → 나머지 A → 나머지 B**다(#377 (b)가 낸 순서 그대로 — 바꾸지 않았다).
  **`ba`는 케이스 먼저**: case 묶음마다 나누므로 **우선 B → 나머지 B → 우선 A → 나머지 A**다 — 어떤 Case A도(우선 파일이든 아니든) Case B보다 먼저 돌지 않는다.
  `ba`를 층 먼저(우선 B → 우선 A → 나머지 B → 나머지 A)로 나누면 우선 A의 `flows`가 나머지 B를 막는다 -> 측정 E1에서 6표본 중 5표본의 정답 후보가 나머지 B였고
  우선 A의 `flows`(600초 데드라인)가 그것을 막았다(조정자 보고 · 이 저장소에 보고서·run ID 없음 · 원자료 저장소 밖). 층 안에서는 둘 다 `collect()` 순서고 ID는 `collect()`가 매긴 그대로다.
  결과 `priority.first_tier_candidates`는 두 순서에서 같은 정의의 **우선 층의 크기**, 곧 엔트리나 앵커가 목록의 파일에 있는 후보 수다. `ab`에서는 처리 순서의 앞 그 수만큼이 우선 층이지만
  `ba`에서는 우선 B만 앞에 오고 우선 A는 나머지 B 뒤에 오므로 **앞에서부터 센 길이가 아니라 개수다**
- 순서가 체크포인트에 묶인다 -> `settings.case_order`(항상 기록, 기본 `ab`)가 실행 계약이다. 다른 값으로 `--resume`하거나 플래그를 빠뜨리면
  `Checkpoint mismatch: run_contract (differs in: settings.case_order)`로 거부, 값이 다른 샤드 체크포인트는 병합 거부(`Shard mismatch on run_contract`)
  -> 해법은 새 체크포인트가 아니라 **같은 값을 다시 주는 것**이다. ⚠ 이 플래그와 별개로 `slicer*.py`가 한 줄이라도 바뀌면 코드 해시(`code_hashes`)가 달라져, 그 전에 만든 체크포인트는
  `--resume`도 `--export-only`도 `Checkpoint mismatch: run_contract (differs in: code_hashes)`로 거부된다(위 「한 repo를 샤드로 나누기」의 「`slicer*.py`가 같아야 한다」) -> 그 코드로 되돌리거나 새 체크포인트로 처음부터 간다
- 샤드 -> 손으로 돌릴 때는 준비 실행(`--prepare-only`)과 모든 샤드(`--resume --cpg … --shard I/N`)에 같은 값을 준다. `--export-only`는 병합 체크포인트의 실행 계약에서 읽으므로 안 줘도 된다.
  `slice_sharded.py`는 `--` 뒤 인자를 준비·모든 샤드에 그대로 넘기고 export에는 안 넘기므로 `-- --case-order ba`만 붙이면 된다

```bash
python tools/slice_sharded.py --repo /work/repo --out /work/slices.json --work-dir /work/run \
  --joern /opt/joern/joern-cli/joern -- --case-order ba
```

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
재기동 후 resume이 답이다(`slicer.py`는 자동 재기동을 안 한다 — 저장 지점에서 이어간다. 샤드 실행은 `slice_sharded.py --restart-on-deadline`이 한다, 위).
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
