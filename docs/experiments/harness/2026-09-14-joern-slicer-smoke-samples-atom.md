# Joern 슬라이서 공개 스모크 표본 3종 — ATOM

PM이 같은 표본에 대해 공유한 슬라이스 수와 비교하려고, PR #201의 Joern 슬라이서를
`repo_smoke_test_bundle.zip`의 runner 입력 01·02·03에 실행했다. 번들 안의 실행 스크립트나
저장소 코드는 실행하지 않았고 C/C++ 정적 색인만 수행했다. private ground truth는 ATOM에
올리지 않았으며, 슬라이서 실행이 끝난 결과에만 로컬에서 대조했다.

이 측정은 **smoke test**다. 번들의 ground truth는 저장소별 알려진 finding 한 건이며 완전한
라벨이 아니다. 빌드·재현·leakage 검사는 번들 자체에서 미실시로 표시돼 있다. 따라서 아래
결과로 취약점 탐지 recall이나 오탐률을 계산하지 않는다.

> **경과 시간 표기.** 이 보고서의 초 단위 값은 전부 **소수 2자리로 반올림**해 싣는다 — 실측이 아니라 **계산값**이고, 원값은 이 보고서가 가리키는 산출 JSON과 `progress.jsonl`에 있다. 예외는 `120.0036`초·`120.001`초 둘로, **120초 제한을 넘은 것 자체가 그 값의 내용**이라 2자리로 깎으면 정확히 `120.00`이 되어 사실이 사라진다. ⚠ **시간은 성능 비교가 아니다** — 그 유보는 해당 수치 옆에 적혀 있다.

## 1. 입력과 실행 조건

| 항목 | 값 |
| --- | --- |
| 실험일 | 2026-09-14 KST |
| 장비 | ATOM `atom-host`, 계정 `user`, Linux aarch64 |
| 코드 | PR #201 커밋 `c4acdd2ec542e047a78c9602e96be57b711c79f5`; `slicer.py` SHA-256 `80b26532f234a43078ac1d7ce988ade8a6b28f89441f524096ebdf81f7bd31a8`, `slicer_queries.py` `c24e0ca3c650e3b5a0c1a341b3ac1dbf25af7825825175f2f23879dcd3b635e7`, `slicer_source.py` `14829c8bec7a4b10a4cc842f0aa841f16059b8adb7de527a451f674e47e42c57` |
| 입력 번들 | `repo_smoke_test_bundle.zip`, SHA-256 `512ab6933a78ad1973293ee27d8456137cc5ab6d17df16166c887d36e2aa3bf2` |
| ATOM 입력 | `runner_samples/sample_XX/repo`만 업로드; private GT는 로컬에만 보존 |
| Joern | 4.0.605, Temurin JDK 21, `-J-Xss64m -J-Xmx16g`, `127.0.0.1:16240` |
| 모델·GPU | 사용하지 않음 |
| flow cap | 후보당 10개 |
| 질의 제한 | sample 01·02는 120초. sample 03은 import가 120초에 중단된 실패 원문을 보존한 뒤 600초로 재실행 |
| 동시성 | 전용 Joern 서버에 슬라이서 클라이언트 하나씩 순차 실행 |
| run_id | 없음. 하네스 run이 아닌 독립 슬라이서 스모크 |
| 원문 기준 디렉터리 | ATOM `/srv/tenants/user/slicer-smoke-20260914-512ab693/` |

PM 공유값은 slice 01/02/03 = 71/28/167, ground-truth 파일 축소 줄 수 = 12/57/206이다.
그 실행의 코드 버전, 후보 정의, flow cap, 클러스터링, 줄 수 계산법은 제공되지 않았다.
따라서 slice 수는 우리 결과의 클러스터링 전·후와 함께 싣고, 줄 수는 서로 다른 두 관측을
분리한다.

- `GT 범위 줄`: private GT가 지정한 `[line_start, line_end]` 안에서 최종 slices가 보존한
  고유 `(file, line)` 수다.
- `GT 파일 줄`: GT 범위를 한 줄이라도 포함한 최종 slice에서 같은 파일에 속한 고유
  `(file, line)` 수다. 호출 경로 때문에 GT 범위 밖 함수가 포함될 수 있다.

## 2. 결과

| 표본 | PM slice | 우리 전 / 후 | PM GT 줄 | 우리 GT 범위 줄 / 범위 크기 | 우리 GT 파일 줄 | status / exit | flow cap 도달 | 중단 질의 / 미처리 후보 | total_seconds |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 01 | 71 | 143 / 55 | 12 | 17 / 29 (58.6%) | 34 | `partial` / 2 | 135 | 0 / 0 | 1021.60 |
| 02 | 28 | 52 / 29 | 57 | 50 / 60 (83.3%) | 76 | `partial` / 2 | 29 | 0 / 0 | 771.16 |
| 03 | 167 | 415 / 146 | 206 | 199 / 252 (79.0%) | 294 | `partial` / 2 | 380 | 1 / 21 | 13051.84 |

sample 01의 GT 범위를 포함한 최종 slice는 55개 중 36번째였고, sink를 entry로 삼은 Case B다.
클러스터링으로 후보 5개를 한 slice에 합쳤으며 `flow_count=50`, `truncated=true`다. sample 02의
GT 범위를 포함한 최종 slice는 29개 중 16번째였고 Case A다. 다른 파일에서 시작한 호출 경로가
GT 파일로 이어졌으며 `flow_count=10`, `truncated=true`다. 두 표본 모두 알려진 GT 범위를
완전히 잃지는 않았지만 flow가 잘렸으므로, 보존한 줄 수를 무제한 열거 결과로 읽을 수 없다.

sample 03은 OOM 전까지 나온 최종 slice 146개 중 5개가 GT 범위를 포함했다. 그 다섯의
GT 범위 줄 합집합은 199줄이고, 같은 파일 줄 합집합은 294줄이다. 가장 앞선 것은 최종 순서
118번째 Case B이며 후보 32개를 합친 slice다(`flow_count=320`, `truncated=true`). 뒤의 네
slice와 GT 범위가 겹치므로 개별 slice의 줄 수를 더하지 않고 고유 `(file, line)`만 셌다.

PM의 12·57·206이 위 두 줄 정의 중 어느 것인지는 알 수 없다. 01의 우리 값은 17 또는 34이고,
02는 50 또는 76, 03은 199 또는 294다. 정의가 확정되기 전에는 각각의 차이를 축소 성능
차이라고 판정하지 않는다.

## 3. sample 03의 120초 import 실패

첫 실행은 `query_timeout_seconds=120`에서 import 요청이 120.001초에 중단됐다. 결과는
`status=failed`, CLI exit 1, `queries_aborted=1`이며 후보와 slice 수는 측정되지 않아 null이다.
Joern HTTP 제한은 서버 계산을 취소하지 않으므로 결과의 지시에 따라 전용 서버를 종료하고
Java 21로 다시 띄웠다. 기본 `java`가 Java 8이라 첫 재기동이 class version 오류로 끝난 로그도
보존했다. 분석 재시도는 같은 flow cap 10을 유지하고 공통 HTTP 제한만 600초로 높였다.

600초 재시도는 후보 436건 중 415건을 결과에 넣은 뒤 다음 flow 질의에서
`java.lang.OutOfMemoryError: Java heap space`를 받았다. 이 질의의 종료 이벤트까지 세면 flow
end는 416건이지만 `slices_before_clustering=415`이고 `candidates_not_processed=21`이다.
슬라이서는 `queries_aborted=1`, `status=partial`, CLI exit 2를 기록했다. 전용 서버 상태가
불확실하다는 결과 지시에 따라 서버를 종료했고 포트와 관련 프로세스가 사라진 것을 확인했다.
따라서 PM 167과 우리 146의 차이 21은 공교롭게 미처리 후보 21과 같지만, **미처리 후보 하나가
최종 slice 하나가 된다는 뜻은 아니다.** 이 실행으로 sample 03의 완전한 최종 slice 수는
측정되지 않았다.

## 4. 원문과 시간

| 표본 | import | flow end / 합 / 최대 | context end / 합 / 최대 |
| --- | ---: | ---: | ---: |
| 01 | 3.88초 | 143 / 381.91초 / 5.53초 | 315 / 630.90초 / 4.28초 |
| 02 | 1.96초 | 52 / 307.58초 / 7.58초 | 99 / 450.18초 / 6.30초 |
| 03 | 204.98초 | 416 / 6945.78초 / 107.75초 | 908 / 5869.66초 / 33.29초 |

합·최대는 `progress.jsonl`의 end 이벤트 `elapsed_seconds`를 계산한 값이다. sample 03의 flow
416건에는 OOM으로 끝난 마지막 질의 한 건이 포함되며, 결과 slice 수는 415다. `total_seconds`는
각 결과 JSON의 벽시계 값이고 이 장비에서 동시에 무엇이 돌았는지 전부 통제한 성능 벤치마크가
아니다.

| 원문 | 바이트 | SHA-256 |
| --- | ---: | --- |
| `results/sample_01.json` | 282,357 | `9e83bffc1845b141b41021ea3afe268788a5a38f13965418ee7d83f3174e52b6` |
| `results/sample_01-trace/responses.jsonl` | 20,336,563 | `bbfe2067dd9b679b7bf9d4fea964db0b9ed445838630c27776b0610c40ea427e` |
| `results/sample_02.json` | 98,946 | `34568383d2003eccf97bc758e7969f7b7c2480592b2cc3f0664d4445150081ba` |
| `results/sample_02-trace/responses.jsonl` | 5,390,400 | `7409f9b67840ba1ad1e95a2d151b8fddccc2f2097cc14f3252bbe3c3069933fd` |
| `results/sample_03.json` | 3,256,739 | `f0f18fdcb5077a1c4bb744c29839223b35a2642bb358f1468f40524746a5d943` |
| `results/sample_03-trace/responses.jsonl` | 486,182,759 | `c52415a11351a726e460ff05fa8335790c46d7a370f8b2dadb359ae059257c73` |
| `results/smoke-summary.json` | 14,310 | `d0260059a1655324464dc313038ce18068ccd510d7db5adbcfbcd6e35f3a54cc` |
| `smoke-artifacts.tar.gz` | 20,777,868 | `66ed66da4df728514e2819d3ddc17844b26b734153b3d6215d89f0080f3e9066` |

요약 JSON은 실행 명령·코드 해시·시각·counters·단계별 시간과 개별 원문 해시를 싣는다.
압축 묶음은 결과·HTTP 응답·progress·exit code·서버 로그·실행 코드를 포함하며 ATOM 원문
디렉터리와 로컬 `_workspace/atom-slicer/results/smoke-samples-20260914/`에 보존했다.

## 5. 해석 범위

- `slices_after_clustering`은 최종 전달 단위이고 PM의 slice 수와 가장 가까운 비교 대상이다.
  다만 PM의 클러스터링 규칙과 cap이 없으므로 동일 지표라고 확정하지 않는다.
- 세 실행에서 private GT는 후보 생성 전에 서버로 전달하지 않았다. 사후 포함 여부 평가에만
  썼으며 private GT 원문·취약 위치·CVE 식별자는 이 저장소에 싣지 않는다.
- GT 한 건의 포함은 저장소 전체 recall이 아니다. GT 밖 slice를 오탐으로 세지도 않는다.
- `partial`은 결과가 없다는 뜻이 아니라 측정된 절단을 숨기지 않았다는 뜻이다. flow cap에 닿은
  후보는 N+1번째 경로 존재를 확인했지만 그 뒤 경로 수와 내용은 미측정이다.
