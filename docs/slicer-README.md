# 오프라인 Joern 슬라이서

[slicer.py](../slicer.py)는 Python 3.11+ 표준 라이브러리만 쓰는 C/C++ 후보 열거 도구다.
함께 배포할 파일은 [slicer_queries.py](../slicer_queries.py)와
[slicer_source.py](../slicer_source.py)다. 하네스 코드를 import하지 않으며 모델 호출도 없다.
저장소별로 미리 `slices.json`을 만든다. 하네스의 캡슐·문맥 확장 경로 연결은 이번 범위 밖이다.

## 실행

슬라이싱 전용 Joern 서버를 시작한다. 한 서버에서 다른 클라이언트의 질의를 동시에 실행하지 않는다.
Python과 Joern에서 `--repo`가 **같은 절대경로의 같은 소스**를 가리켜야 한다.
컨테이너를 나누면 동일한 경로로 마운트하고, 배치 중에는 소스를 수정하지 않는다.

```bash
./joern-cli/joern -J-Xss64m -J-Xmx48g --server --server-host localhost --server-port 16240
python slicer.py --repo /work/target --out /work/slices.json --joern-endpoint http://localhost:16240 --threshold 0.95
```

필요하면 `--max-flows-per-candidate N`과 `--query-timeout-seconds N`을 붙인다.
두 옵션의 기본값은 상한 없음이다. 시간 상한은 **질의 한 건의 HTTP 요청 전체**에 적용하며,
전체 배치의 예산이 아니다. 타임아웃은 서버의 Scala 실행을 취소하지 못하므로 첫 질의 실패에서
배치를 중단한다. 서버를 다시 사용하기 전 진행 중인 실행을 끝내거나 전용 서버를 재시작한다.
CPG는 매 배치마다 새 프로젝트에 생성된다. 프로젝트 정리는 Joern workspace에서 별도로 한다.

Joern 엔진 설정은 `--max-call-depth`, `--max-args-to-allow`,
`--max-output-args-expansion`으로 바꿀 수 있다. 생략하면 설치된 Joern의 기본값을 사용하고,
실제 설정을 `joern_engine_config`에 기록한다. REPL의 기존 implicit 설정은 재사용하지 않는다.
서버와 같은 설치본의 실행 파일을 사용할 수 있으면
`--joern-version-command /opt/joern/joern`으로 실제 `--version` 출력을 기록한다.
생략·실패·빈 출력이면 버전은 `null`이고 `measurement_notes`에 사유가 남는다.

정상 완료의 종료 코드는 `0`, 부분 결과는 `2`, 전체 실패는 `1`이다.
서버 연결·질의 실패에도 JSON을 쓴다. 임시 파일을 완성한 뒤 원자적으로 교체하므로 쓰기 실패가
이전 결과 파일을 잘라내지 않는다. 출력 경로 자체에 쓸 수 없으면 stderr로 사유를 내고
`1`로 종료한다. 잘못된 CLI 인자는 argparse가 실행 전에 거절한다.

## 후보와 줄 복원

- Case A: 이름이 일치하는 호출이 없고 매개변수가 있는 내부 함수와 지정된 입력 API를 부르는
  내부 함수의 합집합이다. 함수 포인터·동명이인·죽은 코드를 구별하지 않는 휴리스틱이다.
- Case B: 지역 배열 식별자를 인자로 받는 호출, 또는 같은 함수에서 같은 선언에 연결된 변수를
  여러 번 해제하는 호출이다. `free`로 흐르는 wrapper의 **매개변수 위치**를 추적하며,
  동명이지만 다른 스코프의 지역 변수를 합치지 않는다. 반복 해제 지점은 함께 보존한다.
- 후보가 속한 함수의 매개변수에서 호출 인자로 흐르는 경로를 구한다. 선언·대입과 제어 조건·
  함수 시그니처·return을 보충하고, 원본의 문장과 괄호를 복원한다. 배열·객체 초기화 목록은
  크기나 값을 바꾸지 않도록 전체를 보존하며, 함수의 조상 탐색은 함수 경계에서 멈춘다.
  파일별 상대경로 헤더를 붙인다.
- `(case, file, line, symbol)`로 후보를 정렬해 ID를 **먼저** 발급한다. 큰 슬라이스부터
  `교집합 줄 수 / 흡수될 슬라이스 줄 수 >= threshold`이면 줄의 합집합으로 흡수한다.
  합집합이 다음 흡수를 가능하게 할 수 있어 변화가 없을 때까지 반복한다.

줄 집합의 원소는 `(file, line)`이며 line은 1부터 시작한다. 따라서 다른 파일의 같은 줄 번호를
같은 근거로 보지 않는다. 원본은 UTF-8로 엄격히 읽고, 읽을 수 없는 파일이나 복원할 수 없는
위치는 별도 카운터와 오류에 남긴다. 출력은 BOM 없는 UTF-8·LF다.

## `slices.json` 계약

| 필드 | 의미 |
| --- | --- |
| `slice_version` | `slice-v1`. 읽는 쪽은 모르는 버전을 거절해야 한다 |
| `repo`, `generated_at` | 저장소 디렉터리 이름, UTC 생성 시각 |
| `joern_version`, `joern_engine_config` | 실제 버전 명령 출력, HTTP로 읽은 실제 엔진 설정. 미관측은 `null` |
| `threshold`, `settings` | 포함률 임계값, flow·질의 상한과 엔진 override |
| `status` | `ok`: 질의 완료. `partial`: CPG 생성 이후 실패·누락·상한 초과 또는 StackOverflowError 관측. `failed`: 그 밖의 CPG 준비 실패 |
| `error` | `partial`·`failed`에서만 존재하는 최상위 사유 문자열 |
| `slices` | `failed`이면 `[]`. `ok`와 `[]`의 조합은 실제 후보/슬라이스 0건이다. ⚠ **`partial`과 `[]`의 조합은 두 가지를 담는다** — (가) CPG는 만들어졌고 슬라이스가 0건, (나) CPG 준비 중 StackOverflowError 로 **CPG 자체가 없다**. `status` 문자열만으로는 안 갈린다. **가르는 것은 `joern_engine_config`다** — (나)는 `null`이다(후보 카운터도 `null`). 비교표에 넣을지 정하는 쪽은 `status != "failed"` 가 아니라 그것을 본다 |
| `measurement_notes` | 미측정 값별 사유. 측정하지 않은 값을 0으로 대체하지 않는다 |
| `diagnostics` | 파싱 보고서 관측, 실패한 질의 단계·출력·중단 정책 |
| `known_limitations` | 아래 한계를 포함한 기계 판독용 문자열 배열 |

각 슬라이스는 `id`, `case`, `entry`, `lines`, `code`, `absorbed`, `flow_count`, `truncated`를
가진다. Case A의 entry는 함수, Case B는 위험한 **호출 이름과 호출 줄**이다.
`absorbed`는 모든 흡수 후보 ID를 정렬해 보존한다. 대표의 ID를 다시 매기거나 소각된 ID를
재사용하지 않는다. `flow_count`는 보존한 경로의 수이며, 클러스터에서는 구성 후보별 수를
합산한다. 후보 간 동일 경로를 다시 제거한 고유 경로 수는 아니다.

flow 상한이 N이면 N+1번째 경로의 존재를 확인한 경우에만 `truncated=true`로 기록한다.
정확히 N개로 끝난 경우는 false다. 클러스터의 truncated는 구성 후보 값의 OR다.
절단된 후보가 있으면 배치 상태는 partial이다. 이 상한은 후보별 상한이며 클러스터의 합산
flow_count는 N보다 클 수 있다.

필수 카운터는 실패해도 키가 남고, 미실행·미관측은 `null`이다.

| 카운터 | 관측 기준 |
| --- | --- |
| `files_seen` | Joern 실행 전에 열거한 C/C++ 소스·헤더 수 (`slicer_source.py`의 확장자 집합) |
| `files_parsed`, `files_parse_failed` | 완전한 Joern frontend Report의 Parsed 열. 파일 노드 수로 대체하지 않는다 |
| `candidates_case_a`, `candidates_case_b` | 각 질의가 완료한 후 위치·이름 기준으로 중복 제거한 후보 수 |
| `slices_before_clustering`, `slices_after_clustering` | 완성된 슬라이스의 흡수 전·후 수. 실패한 미완성 후보는 포함하지 않는다 |
| `flows_truncated` | Python CLI flow 상한을 초과한 후보 수. 버린 경로의 총수가 아니다 |
| `queries_aborted` | HTTP·컴파일·런타임 오류 또는 불완전 응답으로 실패한 질의 수. 첫 실패 후 중단한다 |
| `cpg_seconds` | CPG import 요청에 실제로 경과한 시간. 실패한 요청의 경과 시간도 포함한다 |
| `total_seconds` | CLI 실행부터 JSON 직렬화 직전까지 실제 경과 시간 |

추가 카운터는 `files_read_failed`, `files_in_cpg`, `files_missing_from_cpg`, `files_cpg_failed`,
`methods_internal`, `calls`, `calls_unresolved`, `unknown_nodes`, `locations_unmapped`,
`candidates_unmapped`, `candidates_not_processed`, `engine_flows_truncated`, `directories_skipped`,
`flow_sinks_without_method`다.
`directories_skipped`는 순회하지 않은 디렉터리 심볼릭 링크 수이며 partial과 사유를 남긴다.
`flow_sinks_without_method`는 소속 함수가 없는 호출 인자 노드 수다. 해당 노드는 flow 질의에서
제외하고 partial과 사유를 남긴다. CPG 불완전성을 정상적인 0경로로 해석하지 않는다.
`files_in_cpg`·`files_missing_from_cpg`는 성공적으로 읽은 소스 파일을 기준으로 센다.
`files_cpg_failed`는 Report의 Got a CPG 열이며 파싱 실패와 구분한다.
`calls`는 operator를 제외하고, `calls_unresolved`는 callee가 빈 호출을 센다.
`locations_unmapped`는 질의 응답에서 복원하지 못한 위치의 관측 횟수이므로 같은 위치가
여러 질의에서 다시 나오면 다시 센다. `candidates_not_processed`는 전체 후보 열거가 완료된
경우에만 중단된 후보와 이후 미실행 후보를 센다.

## 알려진 한계와 검증 범위

요청된 한계 세 가지를 항상 출력한다: 함수 내부에서 닫히는 stack overflow의 미포착,
`free` 인자의 별칭 미해결, 함수를 넘는 이중 해제 미포착.
Case A는 파싱·호출 엣지 누락 때문에 오히려 후보가 늘 수 있다. Report가 HTTP에 실리지 않으면
파싱 성공·실패 총수는 알 수 없다. 관측된 개별 실패 로그는 별도로 보존한다.

Joern 엔진 자체가 내부 상한으로 버린 경로 수는 공개 API로 얻지 못하므로
`engine_flows_truncated=null`과 사유를 남긴다. `truncated=false`는 CLI flow 상한에 관한
관측이며 엔진의 완전한 탐색을 보증하지 않는다. `reachableByFlows`는 한 호출 안에서 결과를
먼저 구하므로 같은 함수의 sink를 묶어 엔진 생성 비용을 줄인다. 상한은 이후 함수 묶음의
질의를 멈추지만, 한 묶음 안의 메모리·계산량은 제한하지 못한다.
이 동작은 [Joern의 traversal 구현](https://github.com/joernio/joern/blob/4bb889d96ce972e2ded50d0d5765c514c1a032cf/dataflowengineoss/src/main/scala/io/joern/dataflowengineoss/language/ExtendedCfgNode.scala)에 근거한다.

구조 회복은 원본 문장·괄호와 CPG 제어 조상을 이용한 보충이다. 전처리·매크로 확장 결과나
완전한 컴파일 단위를 재생성하지 않는다. 매개변수 없는 reader 함수는 지정된 flow 시작점이
없어 경로 수가 0일 수 있다. 클러스터링은 수집한 줄을 잃지 않지만, CPG가 처음부터 놓친 줄을
알아내는 보증은 아니다.

동일한 원본과 동일한 Joern 응답에 대해 `slices` 배열은 code를 포함해 결정론적이다.
생성 시각·실행 시간은 실측 메타데이터이므로 전체 JSON 바이트 비교에서 제외한다.
Joern 버전·엔진 설정·생성된 CPG가 달라진 실행 사이의 동등성은 보증하지 않는다.

```bash
python -m pytest -q tests/test_slicer.py
```

테스트는 HTTP 경계에서 Joern을 mock한다. 포함률 경계, 합집합 무손실, ID 유지, 출력 결정론,
줄 복원, 실패 기록을 검사한다. 실서버를 띄우지 않으며 실제 Joern 설치본의 Scala 질의 실행과
실대상 커버리지·성능은 이 테스트의 검증 범위에 포함되지 않는다.

전용 Joern 서버에 대한 선택 실행 검증은 다음과 같다. C/C++ 입력은 분석만 하며 실행하지 않는다.
출력 디렉터리는 매번 새 경로여야 한다. 코드·입력 해시, 실제 CLI 결과와 종료 코드, Case B 원문,
새 CPG 반복 시 slices 동일성 및 flow 상한 검사를 보존한다.

```bash
python tools/probe/joern_slicer_live.py --endpoint http://127.0.0.1:16240 --out-dir /work/live-check-01
```

ATOM Joern 4.0.605에서 실행한 결과와 범위는
[실서버 검증 보고서](experiments/harness/2026-09-14-joern-slicer-live-atom.md)에 기록한다.

HTTP 전송 방식은 [Joern 서버 문서](https://docs.joern.io/server/), 파싱 통계 해석은
[frontend Report 구현](https://github.com/joernio/joern/blob/4bb889d96ce972e2ded50d0d5765c514c1a032cf/joern-cli/frontends/x2cpg/src/main/scala/io/joern/x2cpg/utils/Report.scala)을 참고했다.
