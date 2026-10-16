# Joern 슬라이서 실서버 검증 — ATOM

**mock에서 통과한 구현이 실서버 첫 질의에서 실패했다.** 실제 Scala 질의를 실행하면서
타입·연산자 우선순위·합성 CPG 노드 처리를 고쳤다. 작은 C/C++ 입력에서는 후보 열거부터
`slices.json`까지 실행하고, 새 CPG 간 반복 동일성과 절단 기록을 대조했다. zlib 362후보 전량은
모두 처리됐지만 CPG 누락과 flow cap 때문에 `partial`로 남겼다. 전체 하네스의 Scout·캡슐 소비
경로와 취약점 탐지 정확도는 이 실험의 범위 밖이다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험일 | 2026-09-14 KST |
| 실험 종료 시각 | 2026-09-14 03:53:50 KST (최신 회귀 `checks.json` 원격 mtime; zlib 전량 결과·exit mtime은 03:46:39 KST. 결과 JSON `generated_at` `2026-09-13T15:28:50Z`는 실행 시작 시각이다) |
| 장비 | ATOM `atom-host`, 계정 `user`, Linux aarch64 |
| 실제 도구 | 기존 설치 `/srv/tenants/user/tools/joern-cli`, Joern 4.0.605. `fixture-initial.json`의 실제 명령 출력에 `Version: 4.0.605`가 있다 |
| Java / Python | Temurin JDK 21.0.12+8, Python 3.12.3 (ATOM 명령 출력) |
| 소스 | PR #201 구현 커밋 `c4acdd2ec542e047a78c9602e96be57b711c79f5`. zlib 전량은 원격 `slicer.py` `ac94e2350365cbb570c77262d03f7d7094fe12c2ed94e8ab6aa590c76a8db578`을 사용했고, 최신 소규모 회귀는 manifest의 `slicer.py` `80b26532f234a43078ac1d7ce988ade8a6b28f89441f524096ebdf81f7bd31a8`을 사용했다. queries/source 해시는 두 실행에서 각각 `c24e0ca3...`/`14829c8b...`로 일치한다 |
| run_id | 없음. 하네스 run이 아닌 독립 슬라이서 프로브 |
| 모델·GPU / 컨테이너 | 모델·GPU 호출 없음. 호스트 JVM 및 Python 직접 실행, 이미지 digest 해당 없음 |
| `tool_runner` / `--device` | 해당 없음 / `atom` |
| `tool_images` digest / GPU 메모리 util | 해당 없음 / 해당 없음 |
| 동시에 떠 있던 다른 서버 | 전량 run 동안 별도 Joern/vLLM 서버 없음. 최신 코드 소규모 회귀는 전량 run 종료 뒤 별도 포트에서 실행 |
| 서버 | `127.0.0.1:16240`, `-J-Xss64m -J-Xmx16g`. 마지막 회귀 입력은 별도 `16241`, `-J-Xss64m -J-Xmx4g` |
| 엔진 설정 | 실제 inventory에서 maxCallDepth=4, maxArgsToAllow=1000, maxOutputArgsExpansion=1000 |
| 배치 제한 | 질의별 HTTP 전체 120초. 작은 입력은 무제한 및 후보별 flow 1개, zlib은 후보별 flow 10개 |
| 동시 실행 | 시작 시 Joern/vLLM 프로세스 없음. zlib 실행 중 별도 포트에서 최종 작은 회귀 입력을 검증했다. 전체 소요 시간은 단독 성능 벤치마크가 아니다 |
| 원문 기준 디렉터리 | ATOM `/srv/tenants/user/slicer-live-20260913-a0e5514/` — 이하 경로는 여기 기준, 보존 |
| 서버 stdout | zlib 전량 `server-live-16240-20260914.log`, 최신 회귀 `server-live-16241-20260914.log`. 시작 명령·세션·해시는 각각 `results/zlib-full-20260914.launch.json`과 `results/live-suite-20260914-final-run.launch.json`에 보존했다 |
| 검증 코드 | [joern_slicer_live.py](../../../tools/probe/joern_slicer_live.py), [C 입력](../../../tests/fixtures/slicer_live/cases.c), [C++ 입력](../../../tests/fixtures/slicer_live/cases.cpp), 기존 `tests/fixtures/target/` |

서버 기본 Java는 8이므로 기동 시 `JAVA_HOME=/srv/tenants/user/tools/jdk21`과 해당 `bin`을
PATH 앞에 지정했다. 기존 체크아웃이나 기존 서버를 교체하지 않고 전용 디렉터리에서 실행했다.
Joern 4.0.605의 실행기는 `--version`을 경고하면서 시작 배너를 내므로 버전 원문에는 경고도
그대로 남아 있다. 버전 명령에 stdin을 닫아, 대화형 REPL이 입력을 기다리는 경우를 막았다.

> **경과 시간 표기.** 이 보고서의 초 단위 값은 전부 **소수 2자리로 반올림**해 싣는다 — 실측이 아니라 **계산값**이고, 원값은 이 보고서가 가리키는 산출 JSON과 `progress.jsonl`에 있다. 예외는 `120.0036`초·`120.001`초 둘로, **120초 제한을 넘은 것 자체가 그 값의 내용**이라 2자리로 깎으면 정확히 `120.00`이 되어 사실이 사라진다. ⚠ **시간은 성능 비교가 아니다** — 그 유보는 해당 수치 옆에 적혀 있다.

## 1. 실제 실행에서 드러난 결함

상태와 로그는 [이슈 #202](https://github.com/0xM1yu/whitehat-boss/issues/202)에 추적했다.

> ### 2026-09-20 — 이 절을 든 PR이 그 이슈를 닫는다
>
> 아래 다섯은 **이 보고서가 기록한 세션 안에서 다 고쳐졌다.** 한 커밋 `c4acdd2`
> (`2026-09-14T00:37:42+09:00` — 이슈가 열린 `2026-09-13T13:29:48Z`에서 **2시간 7분 54초 뒤**)가
> 다섯을 전부 담았고, 메타 표 「소스」 행이 이 보고서의 실행을 그 커밋에 건다. 세션
> 중간의 진행도 남아 있다 — §2.1은 **그보다 앞선 판**(`results/live-suite-1/`)을
> *"타입·합성 노드·비교식 수정 후이며, 함수 단위 sink 묶음 변경 전이다"*라고 적고,
> §3이 최종 코드의 zlib 전량이다. main에는 squash 머지
> `ebcd157`로 [PR #201](https://github.com/0xM1yu/whitehat-boss/pull/201)과 함께 들어왔다.
> 그런데 **#201과 [#203](https://github.com/0xM1yu/whitehat-boss/pull/203)이 둘 다
> `Refs #202`로 걸어** 자동 닫기가 걸리지 않았고, 코멘트도 없이 남았다. `Refs`는
> [CLAUDE.md](../../../CLAUDE.md) §3 규칙 2가 **일부만 다룰 때** 쓰라는 것이고 이 경우는
> 다섯 전부였다.
>
> 이 줄을 쓴 시점의 main(`f00574b`)에서 다섯의 수정이 사는 자리는 차례로
> `slicer_queries.py` 141행 · 82–104행(`sourceNode`·`ancestors` 정의)과
> 262행(`context_query` 적용) · 219행 ·
> 135–136행과 231행 · 233–240행이고, 넷째의 partial 사유는 `slicer.py` 628–632행이다.
>
> ⚠ **「고쳤다」를 「CI가 지킨다」로 읽지 않는다.** 다섯 중 회귀 테스트가 생긴 것은
> **넷째 하나**다(`tests/test_slicer.py` 711–720행). 둘째를 덮는 것처럼 보이는
> `test_context_query_stops_before_leaving_a_method`(545–551행)는 **`c4acdd2` 이전 판과
> 바이트 동일**하고 그것이 고정하는 경계도 수정 전부터 있었다 — `tests/` 전체에서
> `sourceNode`가 **0건**이라 그 함수를 되돌려도 초록이다. 그 공백은 사고가 아니라 그 파일
> docstring이 스스로 적어 둔 경계다 —
> *"질의 정합의 유일한 검증은 별도 ATOM run 이고 CI 가 아니다"*.
>
> ⚠ **다섯째를 「이제 기한에 안 걸린다」로 읽지 않는다.** 그 근거는 아래 표 마지막 행과
> 그 뒤 문단이 든 두 값뿐이고, 이 절이 그 자리에서 *"두 값은 첫 질의의 관측이며 전체
> 처리량 개선율이 아니다"*라고 이미 유보했다.
>
> ⚠ 이 줄을 쓴 시점(2026-09-20 조회)에 이슈는 `OPEN`이다. 머지되며 닫힌다.

| 관측 원문 (results/ 아래) | 원인 | 수정 |
| --- | --- | --- |
| `fixture-initial.json` | `overlays.l.flatten`이 이미 문자열인 목록을 Char로 펼쳐 ujson 변환이 컴파일되지 않는다 | 중복 flatten 제거 |
| `fixture-fix1.json`, `context-debug.json` | 함수 첫 줄의 METHOD_REF와 TYPE_DECL이 합성 global 조상으로 올라가 형제 함수·파일 첫 주석을 넣는다 | 소스 AST와 합성 노드를 구분하고 조상 탐색 경계를 적용 |
| `zlib-cap100.json` | `truncated`의 비교식이 Scala 연산자 우선순위 때문에 튜플 비교로 파싱된다 | 비교식 괄호 적용 |
| `zlib-cap10.json`, `orphan-sinks.json` | `contrib/iostream3/zfstream.h`의 호출 인자 6개는 method가 null이다. 정렬 키 접근이 NPE로 실패한다 | 제외한 sink 수를 `flow_sinks_without_method`에 기록하고 partial 사유를 남김 |
| `zlib-fixed-trace/progress.jsonl` | 호출 인자마다 새 엔진을 만들며 첫 flow 질의가 120.0036초 후 제한에 걸렸다 | 같은 함수의 sink를 묶어 엔진 생성 횟수를 줄임 |

마지막 행의 제한 도달 후 전용 서버 프로세스 그룹을 종료하고 새 서버를 띄웠다. HTTP 타임아웃은
서버 계산을 취소하지 못하기 때문이다. 묶음 변경 후 첫 flow 질의는
`zlib-batched-trace/progress.jsonl`에서 3.67초였다. **두 값은 첫 질의의 관측이며 전체 처리량
개선율이 아니다.**

Joern 4.0.605의 [reachableByFlows 구현](https://github.com/joernio/joern/blob/v4.0.605/dataflowengineoss/src/main/scala/io/joern/dataflowengineoss/language/ExtendedCfgNode.scala)은
호출마다 엔진을 생성하고 결과를 먼저 구한다. 함수 묶음은 반복 생성 비용을 줄이지만
`--max-flows-per-candidate`가 묶음 내부 메모리·계산량까지 제한하지는 않는다.

## 2. 작은 C/C++ 입력 — 실서버 회귀

### 2.1 초기 실서버 판

아래 수치는 `results/live-suite-1/`의 각 JSON counters 원문이다. 이 판은 타입·합성 노드·
비교식 수정 후이며, 함수 단위 sink 묶음 변경 전이다. 행 간 시간을 성능 비교로 해석하지 않는다.

| 원문 | status | Case A / B | 흡수 전 / 후 | 절단 후보 | 중단 질의 | 누락 위치 | total_seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `full-1.json` | ok | 9 / 5 | 14 / 9 | 0 | 0 | 0 | 34.73 |
| `full-2.json` | ok | 9 / 5 | 14 / 9 | 0 | 0 | 0 | 53.50 |
| `cap-1.json` | partial | 9 / 5 | 14 / 9 | 12 | 0 | 0 | 71.51 |

`checks.json`이 네 검사 묶음의 통과를 기록한다. `case-b.json`에서 해제 후보 두 개는 모두
`double_release`에 속한다. wrapper가 실제로 해제하는 두 번째 인자와 같은 선언의 반복 해제만
선택하고, 다른 인자나 중첩 스코프의 동명 변수는 합치지 않았다. `consume`·`consume_cpp`도
배열 인자 후보에 포함됐다. 배열 초기화 값, 형제 함수 배제, 교차함수 호출의 문맥을 검사했다.

`full-1.json`과 `full-2.json`의 **slices 배열은 완전히 동일**했다. 생성 시각·실행 시간은
실측 메타데이터라 그 비교에서 제외했다. `cap-1.json`은 중단 질의 없이 12개 후보의 N+1번째
경로를 관측했고, partial 및 CLI 종료 코드 2를 기록했다. 후보 ID는 흡수 후에도 유지된다.

### 2.2 최종 코드 판 — 2026-09-14

최신 프로브는 `/srv/tenants/user/slicer-live-20260913-a0e5514/results/live-suite-20260914-final-run/`에
원문을 보존했다. `full-1`과 `full-2`는 각각 새 CPG에서 실행했고, `cap-1`은 후보당 flow 1개로
실행했다.

첫 시도는 `--out-dir`를 미리 만들어 프로브 계약을 위반해 `FileExistsError`로 종료됐다. 해당
실패 stderr는 원격 `results/live-suite-20260914-final.probe.stderr.log`에 보존했고, 아래 표와
checks는 새 출력 경로 `live-suite-20260914-final-run`의 통과 실행만 가리킨다.

| 원문 | status | Case A / B | slices 전 / 후 | flows_truncated | queries_aborted | locations_unmapped | candidates_not_processed | total_seconds | CLI exit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `full-1.json` | ok | 9 / 5 | 14 / 9 | 0 | 0 | 0 | 0 | 16.73 | 0 |
| `full-2.json` | ok | 9 / 5 | 14 / 9 | 0 | 0 | 0 | 0 | 30.82 | 0 |
| `cap-1.json` | partial | 9 / 5 | 14 / 9 | 12 | 0 | 0 | 0 | 47.92 | 2 |

최신 `checks.json`의 `status`는 `passed`다. `full-1`·`full-2`의 slices 배열은 code를 포함해
완전히 동일했고, 두 실행 모두 `files_seen=4`, `files_in_cpg=4`, `files_missing_from_cpg=0`,
`locations_unmapped=0`, `flow_sinks_without_method=0`이었다. `case-b.json`은 후보 5개를
기록했고 심볼은 `consume`, `consume_cpp`, `memcpy`, `release_second`, `free`였다. `free`와
`release_second`는 모두 `double_release` scope에 속했다. `cap-1`은 `Flow cap reached` 12건을
기록했으며, 중단 질의 없이 종료 코드 2를 반환했다.

## 3. 최종 코드와 zlib 전량 실행

zlib 입력은 기존 ATOM 저장소의 `e3dc0a85b7032e98380dec011bc8f2c2ee0d8fca`를
`git archive`로 전용 디렉터리에 펼친 사본이다. 원본 체크아웃은 변경하지 않았다. 전체 입력
파일 해시는 기존 `results/zlib-cap10.launch.json`에 있고, 이번 실행의 명령·코드 해시는
`results/zlib-full-20260914.launch.json`에 있다.

### 3.1 전량 결과

| 측정 항목 | 값 |
| --- | --- |
| 결과 `status` / CLI exit | `partial` / `2` |
| 후보 Case A / B | `200` / `162` |
| 후보 처리 | `362/362` 처리, `candidates_not_processed=0` |
| slices 전 / 후 | `362` / `203` |
| 파일 seen / CPG / missing | `79` / `76` / `3` |
| 누락 파일 | `test/example.c`, `test/infcover.c`, `test/minigzip.c` |
| `flow_sinks_without_method` | `6` |
| `flows_truncated` | `293` |
| `queries_aborted` | `0` |
| `locations_unmapped` / `candidates_unmapped` | `3` / `0` |
| flow query 수 / elapsed 합 | `362` / `6241.86 s` (progress 계산값) |
| context query 수 / elapsed 합 | `878` / `5589.07 s` (progress 계산값) |
| flow 평균 / 최대 / 30초 초과 | `17.24 s` / `45.05 s` / `69건` (progress 계산값) |
| context 평균 / 최대 | `6.37 s` / `23.13 s` (progress 계산값) |
| `cpg_seconds` / `total_seconds` | `8.01` / `11868.30` |
| `engine_flows_truncated` | `null` (Joern 공개 응답에서 미측정) |
| `files_parsed`, `files_parse_failed`, `files_cpg_failed` | `null` (frontend Report가 HTTP 응답에 없음) |

`error` 필드는 CPG에 없는 파일 3개, enclosing method가 없는 sink 6개, 복원하지 못한 위치
3개, flow cap에 걸린 slice 293개를 열거한다. CPG가 불완전한 상태에서 누락을 0경로로 바꾸지
않았고, 모든 후보가 처리됐다는 사실과 결과의 `partial` 상태를 함께 보존했다. `total_seconds`는
전량 실행의 벽시계 기록이며 단독 성능 벤치마크가 아니다.

`progress.jsonl`에는 import·inventory·case A·case B와 362개의 flow 종료, 878개의 context
종료가 모두 기록됐다. `responses.jsonl`에는 HTTP 왕복 원문 323,559,056바이트가 있다. 주요
원문 해시는 다음과 같다.

| 원문 | 바이트 | SHA-256 |
| --- | ---: | --- |
| `results/zlib-full-20260914.json` | 3,531,266 | `ecb136def8498a0c1b7448097af9f8eaef15ab2e31e64fe19920f40b9f864e53` |
| `results/zlib-full-20260914-trace/progress.jsonl` | 176,624 | `68dde00afcca4f2c81c2f9d5eb04142d92cb382629ec2d6322654d3d53203106` |
| `results/zlib-full-20260914-trace/responses.jsonl` | 323,559,056 | `7d7e9498a2ca9cc488d4deaff4501cc0f3eee183ed75fa4faec87a07be81d3fb` |
| `results/zlib-full-20260914-trace/exit-code.txt` | 1 | `d4735e3a265e16eee03f59718b9b5d03019c07d8b6c51f90da3a666eec13ab35` |
| `results/zlib-full-20260914.launch.json` | 905 | `91b6a354f3461dd64c4e4905e3f7be2b8d90b9af3368c9b1f492ab89180cc560` |
| `server-live-16240-20260914.log` | 22,473 | `afde75a19e347dcd5c5b61adf18262947efdda062cf357b657c5394e35cd17a0` |

서버 로그에서 `error`, `exception`, `outofmemory`, `shutdown`, `fatal` 키워드를 검색한 결과는
없었다. 결과가 partial인 원인은 서버 크래시가 아니라 위의 관측된 소스 누락·sink 제외·flow
cap이다.

### 3.2 실행 코드 대조

전량 실행의 원격 `slicer.py`와 PR #201 파일은 SHA가 달랐지만, `git diff --no-index` 대조에서
차이는 `LIMITATIONS` docstring 한 줄(`method batches`와 `sinks` 표현)뿐이었다. 실행 로직의
차이는 관측되지 않았다. 전량 원격 해시는 `slicer.py` `ac94e2350365cbb570c77262d03f7d7094fe12c2ed94e8ab6aa590c76a8db578`,
`slicer_queries.py` `c24e0ca3c650e3b5a0c1a341b3ac1dbf25af7825825175f2f23879dcd3b635e7`,
`slicer_source.py` `14829c8bec7a4b10a4cc842f0aa841f16059b8adb7de527a451f674e47e42c57`다.
최신 소규모 회귀는 PR 파일 해시 `80b26532f234a43078ac1d7ce988ade8a6b28f89441f524096ebdf81f7bd31a8`을
manifest에 기록했다.

## 4. 재현

서버와 클라이언트에서 입력의 절대경로가 같아야 한다. 전용 서버 한 대에 한 클라이언트만
붙인다. [실행 계약](../../slicer-README.md)의 JVM 기동 예시를 따른 뒤 다음을 실행한다.

```bash
python3 tools/probe/joern_slicer_live.py --endpoint http://127.0.0.1:16240 --out-dir /work/new-live-run
python3 slicer.py --repo /work/zlib --out /work/zlib-slices.json --joern-endpoint http://127.0.0.1:16240 --max-flows-per-candidate 10 --query-timeout-seconds 120
```

`run_slicer_recorded.py`는 측정 디렉터리에 보존한 계측 래퍼다. 실제 JoernClient를 상속해 질의
시작·종료 시간, 질의문·원응답을 JSONL에 쓰고 원래 CLI를 실행한다. HTTP 응답을 대체하지 않는다.

## 5. 판정의 범위

- 실서버가 Case A/B, 흐름, 문맥 질의를 처리하는지와 실패·절단을 산출물에 남기는지를 확인했다.
- frontend Report 표는 HTTP 응답에 나타나지 않았다. `files_parsed`·`files_parse_failed`·
  `files_cpg_failed`는 null과 사유를 유지한다. CPG 파일 노드 수를 파싱 성공 수로 바꾸지 않았다.
- 엔진 내부에서 버린 경로 수는 공개 응답으로 관측하지 못해 `engine_flows_truncated=null`이다.
- 취약점 라벨 정답 대조, 탐지율·오탐률, 전체 코드 커버리지, 하네스 Scout·캡슐 연결은 측정하지 않았다.
  Joern 실서버에서 후보·slice 생성과 partial accounting을 확인했지만, 그것이 Scout 판정 정확도를
  뜻하지는 않는다. [D74](../../open-questions/D/D74.md)는 이 실험 시점에 열려 있었다 —
  2026-09-18에 **(a) 후보 열거에만**으로 닫혔고, 캡슐 경로의 선행조건은
  [D78](../../open-questions/D/D78.md)로 넘어갔다.
