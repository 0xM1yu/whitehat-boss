# 오프라인 Joern 슬라이서 — slice-v2

Python 3.11+ 표준 라이브러리만 사용하는 독립 C/C++ 슬라이서다. 모델과 하네스를 import하지
않으며 저장소별로 미리 결과를 만든다. Scout·캡슐 배선은 별도다. 변경 근거는 [D77](open-questions/D/D77.md)다.

## 배포 파일과 실행

[slicer.py](../slicer.py), [slicer_queries.py](../slicer_queries.py),
[slicer_backward.py](../slicer_backward.py), [slicer_candidates.py](../slicer_candidates.py),
[slicer_source.py](../slicer_source.py), [slicer_pipeline.py](../slicer_pipeline.py),
[slicer_checkpoint.py](../slicer_checkpoint.py), [slicer_export.py](../slicer_export.py)를 함께 배포한다.
하네스 설치나 서드파티 런타임 패키지는 필요 없다.

Python과 Joern의 `--repo`는 같은 절대경로의 같은 소스를 가리켜야 한다. 실행 중 소스를
수정하지 않는다. 전용 서버에는 클라이언트 하나만 연결한다. 아래 상한은 실행 예시이며
품질·성능의 권장값으로 실측한 것은 아니다.

```bash
./joern-cli/joern -J-Xss64m -J-Xmx16g --server --server-host 127.0.0.1 --server-port 16242
python slicer.py --repo /work/repo --out /work/results/slices.json \
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

종료 코드는 `ok=0`, `partial=2`, `failed=1`이다. CPG 준비 뒤 질의 실패·위치 누락·분석 절단은
부분 결과를 남긴다. 정상적인 빈 결과와 질의 실패를 구별한다. 출력은 UTF-8·LF이고, 최종
JSON은 임시 파일을 fsync한 뒤 교체한다. 출력 경로 자체가 불가능하면 stderr에 사유를 쓴다.

## 분석 규칙

### Case A — 기존 파라미터 기반 경로

이름이 일치하는 호출이 없고 매개변수가 있는 내부 함수와 지정된 입력 API를 부르는 내부
함수를 후보로 삼는다. 소속 함수 파라미터에서 CPG의 호출 인자로 이어지는 `reachableByFlows`
경로를 구한다. 함수별 sink batch를 유지한다. 매개변수 없는 reader는 0경로일 수 있다.

### Case B — 선택한 호출 인자에서 역추적

지역 배열 식별자를 인자로 받는 호출과, 같은 함수에서 같은 선언을 반복 해제하는 호출을
후보로 삼는다. 해제 wrapper는 free로 흐르는 **매개변수 위치**를 보존한다.

후보마다 호출의 소속 함수·줄·열·code·AST order 경로로 호출 한 개를 선택한다. 모호하면
첫 호출을 임의로 쓰지 않고 실패를 기록한다. ⚠ **이 실패는 그 후보 하나로 끝나지 않는다** —
질의가 `require`로 죽어 `QueryError`가 되고, 후보 루프를 감싼 바깥 `except`까지 올라가
**남은 후보 전부가 처리되지 않는다**(`candidates_not_processed`에 남는다). 매핑 실패
(`candidates_unmapped`)가 후보 하나만 건너뛰고 계속하는 것과 다르다. `trigger_arguments`는 후보 선정 근거이고,
`sink_arguments`는 선택한 호출의 모든 인자다. `strcpy(dst, src)`에서 배열 dst만 보고 src를
놓치지 않도록 구분한다. 반복 해제의 관련 호출은 anchor로도 남긴다.

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
| `slices` | 완료한 후보를 클러스터링한 결과. 실패한 미완성 후보는 넣지 않음. ⚠ **`partial`과 `[]`의 조합은 두 가지를 담는다** — (가) CPG는 만들어졌고 슬라이스가 0건, (나) CPG 준비 중 StackOverflowError 로 **CPG 자체가 없다**. `status` 문자열만으로는 안 갈린다. **가르는 것은 `joern_engine_config`다** — (나)는 `null`이다(후보 카운터도 `null`). 비교표에 넣을지 정하는 쪽은 `status != "failed"` 가 아니라 그것을 본다 |
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
python slicer.py --repo /work/repo --out /work/results/slices.json \
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /opt/joern/joern \
  --checkpoint /work/results/state.db --resume --max-flows-per-candidate 10 \
  --backward-depth 30 --backward-nodes 5000 --query-timeout-seconds 600 \
  --candidate-timeout-seconds 1200

# 분석 설정은 기록에서 읽는다. Joern 요청 없이 완료한 후보만 재조립한다.
python slicer.py --repo /work/repo --out /work/results/exported.json \
  --checkpoint /work/results/state.db --export-only --export-dir /work/results/export
```

Joern HTTP timeout은 서버의 Scala 계산을 취소하지 않는다. OOM·StackOverflow·HTTP 실패는
첫 오류에서 중단하며, 다음 후보마다 timeout을 반복하지 않는다. 전용 서버 재기동 후 resume한다.
자동 재기동·실패 후보 자동 건너뛰기는 하지 않는다. `--candidate-timeout-seconds`는 한 시도의
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

`--out`·`--checkpoint`·`--progress`는 **`--export-dir` 바깥이어야 한다.** export는 그
디렉터리 아래를 전부 `os.replace()` 로 덮으므로, 열린 체크포인트를 거기에 두면 POSIX에서
SQLite 파일이 JSON으로 교체되고 **재개 상태가 조용히 사라진다.** 셋 중 하나라도 그 아래면
체크포인트를 열기 **전에** 거절한다. 셋이 서로 다른 것만으로는 부족하다 — 서로 다르면서
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
⚠ **수치를 인용하기 전에 그 보고서가 실은 배포 코드 해시를 본다** — 셋이 서로 다르고, v1
실서버 보고서 안에서도 zlib 전량과 소규모 회귀가 다른 `slicer.py`로 돌았다(그 보고서 §2 「소스」).

HTTP 전송 방식은 [Joern 서버 문서](https://docs.joern.io/server/), 파싱 통계 해석은
[frontend Report 구현](https://github.com/joernio/joern/blob/4bb889d96ce972e2ded50d0d5765c514c1a032cf/joern-cli/frontends/x2cpg/src/main/scala/io/joern/x2cpg/utils/Report.scala)을 참고했다.
