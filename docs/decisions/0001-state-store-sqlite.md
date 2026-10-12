# 0001. 상태 저장소를 SQLite로 시작한다

- 상태: 제안
- 일자: 2026-08-08
- 관련: [engineering-guide.md](../engineering-guide.md) §1·§3, [open-questions.md](../open-questions.md) D6

## 배경

`StateStore`는 run·candidate·evidence·metric을 쓰고 읽는다. 후보 선택지는 SQLite와
DuckDB였고, 판단 기준은 "쓰기 패턴과 분석 쿼리 비중"이다(D6).

v1 시점에 확실한 것은 쓰기 쪽이다. 파이프라인은 후보·Finding·Evidence를 건건이 append
하고 상태 전이마다 갱신한다 — 한 run에서 수백~수천 건의 작은 쓰기다. 분석 쿼리는 아직
없다. 조건 간 비교표는 `trace.jsonl`을 읽어 만들 예정이고(evaluation.md §5), 그것은
저장소가 아니라 파일이다.

즉 **DuckDB가 강한 쪽(대량 스캔·집계)의 부하가 아직 존재하지 않는다.** 없는 부하를 근거로
저장소를 고르면 나중에 실측했을 때 뒤집힐 가능성이 높다.

## 결정

v1은 SQLite로 간다. 파일 하나(`artifacts/<run_id>/state.db`)에 run 단위로 쓰고, 노드는
`StateStore` Protocol만 본다. 동시성은 도입하지 않는다 —
`sqlite3.connect(check_same_thread=True)` 기본값을 그대로 두어, 순차 전제를 어기면 조용히
깨지는 대신 즉시 실패하게 한다.

## 대안과 기각 사유

| 대안 | 기각 사유 |
| --- | --- |
| DuckDB | 강점인 열 지향 집계를 쓸 부하가 아직 없다. 건별 갱신이 주 패턴인 지금은 이점이 측정되지 않는다 |
| Postgres | 단일 노드 개발 환경에 서버 프로세스를 하나 더 얹는다. 팀원 대부분이 GPU도 없는 상태에서 기동 의존성을 늘리지 않는다 |
| 파일(JSONL)만 | 상태 갱신(전이)이 append-only와 맞지 않는다. 최신 상태를 읽으려면 매번 전체 재생이 필요하다 |
| 인메모리만 | 테스트에는 충분하지만 run이 끝나면 사라진다. 재평가·resume의 근거가 남지 않는다 |

## 결과

- 얻는 것: 의존성 0개(표준 라이브러리), 파일 하나로 끝나는 백업·삭제, 두 구현
  (memory/sqlite)을 같은 계약 테스트로 검증 가능
- 잃는 것: 대량 집계 성능. 조건 간 비교가 저장소 쿼리로 옮겨 가면 다시 봐야 한다
- **되돌리는 비용: 낮다.** 노드는 `StateStore` Protocol만 알고, 어댑터를 하나 더 쓰고
  `profiles.yaml`의 `adapters.state_store` 값을 바꾸면 된다. 이 낮은 비용이 지금 결정을
  미루지 않아도 되는 이유다

## 후속

- 재검토 조건: 조건 간 비교를 `trace.jsonl`이 아니라 저장소 쿼리로 만들게 되거나, 한 run의
  Evidence가 수십만 건 규모가 되면
- run 사이를 넘는 상태(재평가 횟수 등)를 저장할지는 별도 항목이다 — open-questions D10
  - ⚠ **2026-09-24 — D10은 [ADR 0012](0012-harness-canon-v2.md) 결정 6이 승인되면 닫힌다** (⏵ 2026-09-25 — 승인됐고 닫혔다) — 재평가를
    배선하지 않으니 보관할 횟수가 없다. run을 넘는 상태 중 남는 것, 곧 resume의 스키마 버전 문제(구버전
    `state.db`를 `model_validate`로 되읽으면 필수 필드 누락으로 로드가 실패한다)는
    [D86](../open-questions/D/D86.md)이 승계한다
  - ⏵ 2026-10-10 — 여는 자리의 저장 형태 판 게이트가 섰다(이슈 #281, `src/adapters/sqlite_store.py`). 판을 적은
    파일은 판이 다르면 로드 **전에** 거부된다. 판 0(게이트 전 파일)은 종전대로 열려 위 실패가 그대로 남는다
    (⏵ 2026-10-12 — [D86](../open-questions/D/D86.md) 닫음: resume 비지원이 현 상태다)
