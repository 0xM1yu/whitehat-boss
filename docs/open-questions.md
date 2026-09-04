# 미해결 리스크와 결정 대기 항목

"아직 안 정해짐"을 문서에 숨기지 않기 위한 목록. 결정되면 결론을 해당 문서로 옮기고
여기 행은 상태만 바꿔 남긴다 — 번호를 재사용하지 않기 위해서다(아래 관리 방식).

## 이 목록 관리 방식 (D·R 시리즈 워크플로)

새 결함·결정이 나오면 아래 순서로 다룬다. **이 문서가 진실의 소스이고, GitHub Issue는 그 추적용이다.**

1. **등재**: `docs/open-questions/D/` 아래에 다음 번호로 `Dn.md` 를 만들고, 아래 §2 표에 **한 줄**(번호·항목·상태·본문 링크)을 넣는다. 본문은 파일에만 쓰고 표에는 안 쓴다.
   블로킹 리스크(R)는 `docs/open-questions/R/Rn.md` 와 §1 표로, **같은 형식**이다.
2. **이슈화**: 미해결 **D** 는 GitHub Issue 를 만든다 (제목 `[Dn] 요약`, 라벨 `D-series`). 대기 사유가 있으면 이슈 본문에 적고 열어 둔다.
   **R 은 이슈를 만들지 않는다** — 개별 결함이 아니라 프로젝트 수준 리스크이고, 상태의 원천은 §1 표와 `Rn.md` 다.
3. **수정·닫기**: 고칠 때는 **`Dn.md` 를 먼저** 고치고(해소면 제목에 취소선 + 상태를 `닫힘`으로), **PR 본문에 `Closes #<이슈>`** 를 적어 머지 시 자동으로 닫는다. 일부만 다루면 `Refs #<이슈>`.
4. **머지**: 하네스 팀이 자체 리뷰·머지한다(2026-08-30~).

> **왜 한 항목당 한 파일인가.** 예전에는 표 한 행이 본문 전체(최대 5,500자)를 들고 있었다.
> 그래서 서로 다른 항목을 고치는 두 PR 이 같은 표를 건드렸고, 열을 하나 추가하는 PR 은 55행
> **전부**와 충돌했다(#86 ↔ #87). `docs/decisions/` 가 쓰는 구조로 바꿔 그 원인을 없앴다 —
> 본문 편집은 인덱스를 건드리지 않는다. `tests/test_open_questions_layout.py` 가 강제한다.
> **R 도 2026-09-01에 같은 구조로 옮겼다** — 그때까지 R 은 어떤 검사도 안 받아 1,611자 행(R6)이 있었다.

번호는 **재사용하지 않는다**(해소돼도 번호는 남긴다) — 회의록·PR·이슈가 그 번호로 참조하기 때문. **소각된 번호: D56** — 2026-08-30 커밋 `57f66e1`이 등재 당일 철회하면서 표에서 통째로 지웠다(내용은 `experiments/2x2/README.md`와 PR #56으로 갔다). 표의 최대가 D55였을 때 그대로 두면 다음 등재자가 D56을 재사용했을 것이라 D57로 건너뛰었다 — 2026-09-01에 D57이, 2026-09-02에 D58~D62가 등재됐다(등재 없이 열려 있던 이슈 #114·#121·#124·#126·#128을 규약대로 등재). **2026-09-04에 D63·D64·D65가 등재됐다** — 셋 다 이슈보다 등재가 먼저이고, 이슈는 이 등재가 머지된 뒤에 연다(위 1→2단계). **다음 등재는 D66다.**

## 1. 블로킹 리스크

| # | 항목 | 상태 | 본문 |
| --- | --- | --- | --- |
| R1 | ATOM(sm_121) × vLLM 커널 호환성 · 로컬 TCP 차단 | 부분 | [본문](open-questions/R/R1.md) |
| R2 | Critic(Devstral Small 2, dense 24B) NVFP4 양자화 시 반박 정확도 저하 가능성 | 열림 | [본문](open-questions/R/R2.md) |
| R3 | 평가 데이터 누출 자동 검증 스크립트 부재 | 부분 | [본문](open-questions/R/R3.md) |
| R4 | 단일 노드 대역폭 병목의 실제 한계 미파악 | 열림 | [본문](open-questions/R/R4.md) |
| R5 | Zero-day 제보 워크플로의 강제 수단 미정 | 열림 | [본문](open-questions/R/R5.md) |
| R6 | prefix cache 효과를 장부에 잡지 못한다 (`cached_tokens`가 항상 0) | 열림 | [본문](open-questions/R/R6.md) |
| R7 | `gc`가 지운 run_id를 같은 날 새 run이 재사용한다 | 보류 | [본문](open-questions/R/R7.md) |

## 2. 설계 결정 대기

> **⏸ 모델 선정 대기 보류 (2026-08-30, 5차 회의).** scout·auditor·critic 모델과 오케스트레이션 방안(큰 모델 하나 vs 소형 앙상블)이 확정되기 전까지 그에 종속된 항목은 수정을 보류한다 — 기조가 바뀌면 실측 축이 통째로 달라져 지금 고치는 것이 무의미하다. 해당: **D2·D3·D4·D24·D28**(임계값·양자화·점수·후보수), **D29**(샘플링 등가화 남은 부분), **D51**(열거 강제), **D54**(v2 모델 배선). 미해결 D는 GitHub Issue로 추적한다 — **현재 예외 넷은 D9·D19·D20·D21이고 이슈를 만드는 것이 남았다**(D9는 범위 결정 대기, 나머지 셋은 부분 해소라 잔여가 각 행에 있다).

| # | 항목 | 상태 | 본문 |
| --- | --- | --- | --- |
| D1 | 벤치마크 라벨 통합 | 열림 | [본문](open-questions/D/D1.md) |
| D2 | Scout 임계값·단계별 승격 비율 | 보류 | [본문](open-questions/D/D2.md) |
| D3 | Capsule 확장 횟수 상한 | 보류 | [본문](open-questions/D/D3.md) |
| D4 | Critic 양자화 검증 표본 크기와 허용 하락폭 | 보류 | [본문](open-questions/D/D4.md) |
| D5 | GPU-hour → 금액 환산식 | 열림 | [본문](open-questions/D/D5.md) |
| D6 | 상태 저장소 SQLite vs DuckDB | 결정 | [본문](open-questions/D/D6.md) |
| D7 | 오케스트레이터 LangGraph v1 확정 여부 | 결정 | [본문](open-questions/D/D7.md) |
| D8 | **Evidence Gate와 Critic의 순서** | 닫힘 | [본문](open-questions/D/D8.md) |
| D9 | 1차 분석 대상의 범위 | 열림 | [본문](open-questions/D/D9.md) |
| D10 | 재평가 횟수를 어디에 보관하는가 | 열림 | [본문](open-questions/D/D10.md) |
| D11 | Evidence에 "발견 건수"가 없다 | 열림 | [본문](open-questions/D/D11.md) |
| D13 | **캡슐의 그래프 파생 섹션(`callers`/`callees`/`types`)을 무엇이 채우는가** | 열림 | [본문](open-questions/D/D13.md) |
| D12 | build를 몇 단계에서 실행하는가 | 열림 | [본문](open-questions/D/D12.md) |
| D14 | **재현성 비교가 `data` 안의 시간 파생값을 빼지 않는다** | 열림 | [본문](open-questions/D/D14.md) |
| D15 | **`Budget.tool_seconds`의 값에 실대상 근거가 없다** | 열림 | [본문](open-questions/D/D15.md) |
| D16 | ~~확장자로 대상을 고르는 도구가 `.inc`를 조용히 건너뛴다~~ | 닫힘 | [본문](open-questions/D/D16.md) |
| D17 | **프롬프트의 "사실 블록이 전부다"가 산문 자리표시자를 전부 장식으로 만든다** | 열림 | [본문](open-questions/D/D17.md) |
| D18 | **8단계 정책이 게이트 등급을 읽지 않는다** | 열림 | [본문](open-questions/D/D18.md) |
| D19 | **프롬프트 본문이 실행 산출물에 남지 않아 W1을 실행에서 검증할 수 없다** | 부분 | [본문](open-questions/D/D19.md) |
| D20 | **Scout이 캡슐로 호출 그래프를 받지 못한다** | 부분 | [본문](open-questions/D/D20.md) |
| D21 | **토큰 계상이 실서빙 실측보다 1.8배 과소다** | 부분 | [본문](open-questions/D/D21.md) |
| D22 | **`callers`가 사실 블록 최상위 키로 고정돼 있어 안 본 것을 "0건"으로 말한다** | 열림 | [본문](open-questions/D/D22.md) |
| D23 | **`Evidence.location`을 채우는 코드가 저장소에 없어 Gate E1의 `location_ok`가 구조적으로 거짓이다** | 열림 | [본문](open-questions/D/D23.md) |
| D24 | **후보 1건당 토큰·시간을 한 번도 재지 않아 `candidate_max`에 근거가 없다** | 보류 | [본문](open-questions/D/D24.md) |
| D25 | ~~`queue_maxsize`를 소비하는 코드가 후보 경로에 없다~~ | 닫힘 | [본문](open-questions/D/D25.md) |
| D26 | ~~6단계 통과 조건(E2 = `path ∧ graph ∧ unblocked`)과 8단계 진입 조건(`repro_spec`)이 서로를 배제한다~~ | 닫힘 | [본문](open-questions/D/D26.md) |
| D27 | **장부 `cached_tokens`가 실서빙과 Fake에서 역전돼 있다** | 열림 | [본문](open-questions/D/D27.md) |
| D28 | **Scout 점수에 해상도가 없다 — 원인 미확정** | 보류 | [본문](open-questions/D/D28.md) |
| D29 | **서버 `generation_config`의 `top_k`가 run 기록 어디에도 안 남는다** | 보류 | [본문](open-questions/D/D29.md) |
| D30 | **`single`이라는 이름에 세 문서가 서로 다른 뜻을 붙인다** | 열림 | [본문](open-questions/D/D30.md) |
| D31 | ~~`Evidence.ok`가 "실패"와 "해당 없음"을 못 가른다~~ | 결정 | [본문](open-questions/D/D31.md) |
| D32 | **컴파일 플래그(`-std`·`-I`)가 코드 상수인데 `RunRecord`에 없다** | 열림 | [본문](open-questions/D/D32.md) |
| D33 | ~~`run_20260825_0001`이 옛 build 배선의 마지막 완전 측정이다~~ | 닫힘 | [본문](open-questions/D/D33.md) |
| D34 | ~~언어별 Command 분할에서 첫 그룹의 발견이 둘째 그룹을 가린다~~ | 닫힘 | [본문](open-questions/D/D34.md) |
| D35 | ~~9단계 `dedupe()`가 E3를 지운다~~ | 부분 | [본문](open-questions/D/D35.md) |
| D36 | ~~`report.md`에 E 등급이 없다 — run의 성과가 최종 산출물에 안 보인다~~ | 닫힘 | [본문](open-questions/D/D36.md) |
| D37 | ~~`run_manifest.json`에 `profile`이 없다~~ | 닫힘 | [본문](open-questions/D/D37.md) |
| D38 | **E4는 Fake auditor 프로필에서 원리상 도달 불가다 — 조건 명시 항목** | 결함 아님 | [본문](open-questions/D/D38.md) |
| D39 | **심볼 단위 후보에서 캡슐 캐시는 원리상 무효다 — 조건 명시 항목** | 결함 아님 | [본문](open-questions/D/D39.md) |
| D40 | **8단계가 dedupe 이전이라 같은 파일에 도구를 반복 실행한다** | 열림 | [본문](open-questions/D/D40.md) |
| D41 | **예산이 실제로 캡슐을 묶었는지 run이 남기지 않는다** | 열림 | [본문](open-questions/D/D41.md) |
| D42 | ~~요청당 출력 상한이 없다 — 하네스가 `max_tokens`를 안 보낸다~~ | 닫힘 | [본문](open-questions/D/D42.md) |
| D43 | **캡슐 재검증이 `using_counter` 밖에서 돌아 저장과 로드가 갈린다** | 부분 | [본문](open-questions/D/D43.md) |
| D44 | ~~critic 단계의 파싱 실패가 run 전체를 죽인다~~ | 닫힘 | [본문](open-questions/D/D44.md) |
| D45 | ~~실행한 대상의 심볼이 기록에 안 남는다 — 건너뛴 대상만 남는다~~ | 닫힘 | [본문](open-questions/D/D45.md) |
| D46 | **구조화 출력이 공백 폭주로 안 닫힌 JSON을 낸다 — 상한을 키워도 안 닫힌다** | 열림 | [본문](open-questions/D/D46.md) |
| D47 | ~~스키마 재시도가 실패한 응답 원문을 프롬프트에 되싣는다 — D42의 상한이 그 자리에서 입력 초과로 되돌아온다~~ | 닫힘 | [본문](open-questions/D/D47.md) |
| D48 | ~~`target_skipped.stage`가 실제로 실패한 단계를 안 가리킨다~~ | 닫힘 | [본문](open-questions/D/D48.md) |
| D49 | **재시도 프롬프트가 산출물에 안 남는다 — 실제로 서버에 간 요청을 재현할 수 없다** | 열림 | [본문](open-questions/D/D49.md) |
| D50 | ~~`--tool-runner` 경고가 값을 비교하지 않아 오버라이드가 아닌 run을 오버라이드로 신고한다~~ | 닫힘 | [본문](open-questions/D/D50.md) |
| D51 | **`trigger_status`의 보류 두 건 — (2) 필드 순서는 닫혔고 (1) 열거 강제가 남았다** | 보류 | [본문](open-questions/D/D51.md) |
| D52 | ~~`trigger_status`를 내라고 시키는 프롬프트가 저장소에 없어 그 필드는 구조적으로 항상 `null`이다~~ | 닫힘 | [본문](open-questions/D/D52.md) |
| D53 | ~~실패한 `model_call`이 예산 장부에 한 푼도 안 실린다 — 폭주가 잦은 조건일수록 싸 보인다~~ | 닫힘 | [본문](open-questions/D/D53.md) |
| D54 | **`config/models.yaml`의 어느 모델도 v2 프롬프트를 고르지 않아 머지된 v2가 도달 불가다** | 보류 | [본문](open-questions/D/D54.md) |
| D55 | ~~재시도가 `model_call` 한 건으로 뭉개져 "스키마 파싱 실패율"이 과소 집계된다~~ | 닫힘 | [본문](open-questions/D/D55.md) |
| D57 | **`served_models()`가 호출되지 않는 역할까지 서빙으로 세어, 안 뜨는 모델의 미실측 `checksum: 0`이 비교표 제외 판정에 들어온다** | 부분 | [본문](open-questions/D/D57.md) |
| D58 | **샘플링 부재를 서술하는 주석 셋이 낡았다 (#99 ③ 이후 두 판본 공존)** | 열림 | [본문](open-questions/D/D58.md) |
| D59 | **Critic과 Auditor가 다른 계열이어야 한다는 조건을 강제하는 장치가 코드에 없다** | 열림 | [본문](open-questions/D/D59.md) |
| D60 | ~~bench --apply가 --device 없이도 registry에 measured_on: null을 영구히 쓴다~~ | 닫힘 | [본문](open-questions/D/D60.md) |
| D61 | ~~bench의 context 스윕 꼭대기가 서버 max_model_len을 몰라 항상 400을 받는다~~ | 닫힘 | [본문](open-questions/D/D61.md) |
| D62 | **bench가 판마다 같은 프롬프트를 보내 두 번째 판부터 자기 캐시를 맞는다 — (b) 채택, 왜곡 제거 (a)는 열림** | 부분 | [본문](open-questions/D/D62.md) |
| D63 | **리랭커 순위를 우리가 실제로 보내는 입력 모양에서 한 번도 재지 않았다** | 열림 | [본문](open-questions/D/D63.md) |
| D64 | **Auditor·Critic을 DeepSeek 계열로 교체하는 제안 — 등재와 미확인 항목** | 열림 | [본문](open-questions/D/D64.md) |
| D65 | **게이트가 E3를 주는 조건과 스키마가 `validated`를 허용하는 조건이 다르다 — 실서빙 run이 7단계에서 죽는다** | 열림 | [본문](open-questions/D/D65.md) |

## 3. 팀·운영 미정

**2026-09-01 — O 계열은 전부 닫혔다.** 트랙(T1~T8) 단위 개발이 폐기되고 하네스를 두 명이
맡기로 정해지면서 O1~O3(트랙 배정·트랙 간 동기화·단계별 역할 분담)은 **전제가 사라졌다.**
O4(평가 데이터셋 6종 TBD)는 데이터셋 확보가 진행 중이고 파인튜닝은 리서치 단계다.
O5(제보처 무응답 대기 기간)와 O7(킥오프 발표 범위)도 함께 닫는다.

> O6(킥오프 PDF 대조)은 그보다 앞선 2026-08-07에 완료됐다. 결과는 D8·D9·O7과 각 문서
> 반영분으로 이관했다.

**행을 남기지 않는다.** D·R 과 달리 O 는 다른 문서·이슈가 번호로 참조하지 않아
번호 재사용 위험이 없고, 본문도 전부 200자 미만이라 파일로 뺄 것이 없었다.

## 4. 기록 규칙

- 항목이 해결되면 결정 내용을 관련 문서에 반영하고, 되돌리기 비싼 결정이면 `docs/decisions/`에 ADR을 남긴다.
- **기한은 두지 않는다.** R과 D를 가르는 것은 날짜가 아니라 성격이다 — R은 프로젝트 수준의
  블로킹 리스크이고 D는 개별 결함·설계 판단이다. 두 계열의 머리 표는 `| 상태 | 관련 문서 |`로 같다.
