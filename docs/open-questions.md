# 미해결 리스크와 결정 대기 항목

"아직 안 정해짐"을 문서에 숨기지 않기 위한 목록. 결정되면 해당 문서로 옮기고 여기서 지운다.

## 이 목록 관리 방식 (D-시리즈 워크플로)

새 결함·결정이 나오면 아래 순서로 다룬다. **이 문서가 진실의 소스이고, GitHub Issue는 그 추적용이다.**

1. **등재**: `docs/open-questions/Dn.md` 를 만들고(다음 번호 Dn), 아래 §2 표에 **한 줄**을 넣는다 — `| Dn | 항목 | 상태 | [본문](open-questions/Dn.md) |`. 본문은 파일에만 쓴다.
2. **이슈화**: 미해결이면 GitHub Issue 를 만든다 (제목 `[Dn] 요약`, 라벨 `D-series`). 대기 사유가 있으면 이슈 본문에 적고 열어 둔다.
3. **수정·닫기**: 고칠 때는 **`Dn.md` 를 먼저** 고치고(해소면 제목에 취소선 + 상태를 `닫힘`으로), **PR 본문에 `Closes #<이슈>`** 를 적어 머지 시 자동으로 닫는다. 일부만 다루면 `Refs #<이슈>`.
4. **머지**: 하네스 팀이 자체 리뷰·머지한다(2026-08-30~).

> **왜 한 항목당 한 파일인가.** 예전에는 표 한 행이 본문 전체(최대 5,500자)를 들고 있었다.
> 그래서 서로 다른 D 를 고치는 두 PR 이 같은 표를 건드렸고, 열을 하나 추가하는 PR 은 55행
> **전부**와 충돌했다(#86 ↔ #87). `docs/decisions/` 가 쓰는 구조로 바꿔 그 원인을 없앴다 —
> 본문 편집은 인덱스를 건드리지 않는다. `tests/test_open_questions_layout.py` 가 강제한다.

번호는 **재사용하지 않는다**(해소돼도 번호는 남긴다) — 회의록·PR·이슈가 그 번호로 참조하기 때문. **소각된 번호: D56** — 2026-08-30 커밋 `57f66e1`이 등재 당일 철회하면서 표에서 통째로 지웠다(내용은 `experiments/2x2/README.md`와 PR #56으로 갔다). 표의 최대가 D55라 그대로 두면 다음 등재자가 D56을 재사용한다. **다음 등재는 D57이다.**

## 1. 블로킹 리스크

| # | 항목 | 왜 위험한가 | 결정 기한 | 담당 |
| --- | --- | --- | --- | --- |
| R1 | ~~AI TOP ATOM(sm_121)에서 vLLM 버전·커널 호환성 미검증~~ · ~~로컬 TCP 차단으로 기동 불가~~ → **둘 다 2026-08-17 해소.** vLLM 0.27.1이 aarch64에 설치되고 torch 2.13.0+cu130이 `(12, 1)`·`NVIDIA GB10`을 인식했으며(대체 서빙 스택 검토는 불필요), 도구 실행 경로도 같은 날 완주했다(STATUS.md §5). 관리자 예외로 로컬 TCP가 풀린 뒤 **`qwen3-4b`가 `127.0.0.1:8001`에서 떠서 실측이 나왔다**(`config/models.yaml`, 2026-08-17T15:16:27Z) — **단 `VLLM_USE_DEEP_GEMM=0`이 필요하다**([portability.md](portability.md) §5 #5). **남은 것은 성격이 또 바뀌었다**: 환경 제약이 아니라 **아직 안 잰 항목**이다 — model-serving.md §3 체크리스트 7개 중 1개 통과·1개 부분이고, NVFP4 KV cache·speculative decoding·prefix cache 적중률·structured output은 미검증이다 | 크리티컬 패스 재개. **해소 주체가 장비 관리자에서 다시 우리로 돌아왔다** — 이제 막는 것이 없고 `cli bench`를 돌리면 답이 나온다 | 커널·기동 2026-08-17 완료 / 체크리스트 잔여는 측정 대기 | TBD |
| R2 | Critic(Devstral Small 2, dense 24B) NVFP4 양자화 시 반박 정확도 저하 가능성 | B3'의 핵심 효과를 측정 불가하게 만듦 | 2026-08-31 | TBD |
| R3 | 평가 데이터 누출 자동 검증 스크립트 부재 | 코드 리뷰만으로는 보장 불가. 누락 시 모든 비교 결과 무효. **부분 착수**: `eval/leakage/`가 [evaluation.md](evaluation.md) §4 표의 7개 중 2개(hash 교집합, CVE·flag·문제명·patch diff 마스킹)를 구현하고 CI에서 돈다. 남은 5개(Patch Leakage 출처 검사, 중복 데이터 제거, Time Split, Prompt Logging, Reproducibility)는 미구현이므로 **해결 아님**. **2026-08-22 — Prompt Logging 행은 절반만 움직였다**: D19 구현으로 모델 입력 전체가 `{run_dir}/prompts/`에 남고 누출 스캐너의 기본 대상에도 들어갔지만, §4 표가 그 행에 요구하는 **자동 검증("로그 존재 검사")은 여전히 미구현**이다 — 프롬프트 파일이 없는 run을 무효로 판정하는 코드가 없다. **구현 수는 2 그대로다**. **2026-08-22 — 스캔 면이 실행 환경에 좌우된다는 것이 드러났다**: `RUN_GLOBS`가 `REPO_ROOT.glob("artifacts/*/…")`이라 **저장소 밖 `--artifacts-root`로 돈 run은 기본 호출에서 한 건도 안 잡힌다.** ATOM 검증에서 인자 없는 `python -m eval.leakage`가 경로 4개를 스캔했는데 그것은 그 체크아웃에 남아 있던 **옛 run들**이고, 방금 돈 `run_20260821_0007`의 프롬프트는 `--scan`으로 명시해야 들어왔다(5개). `artifacts/`는 gitignore돼 있어 커밋 오염은 없지만, **같은 명령이 장비에 남은 잔여물에 따라 다른 것을 검사한다** — "검사했다"가 무엇을 검사한 것인지 명령만 보고 알 수 없다 | 2026-09-07 | TBD |
| R4 | 단일 노드 대역폭 병목의 실제 한계 미파악 | 4조건 실행 시간이 일정을 초과할 수 있음. ConnectX-7 2대 확장 판단의 전제 | 2026-09-05 | TBD |
| R5 | Zero-day 제보 워크플로의 강제 수단 미정 | 원칙(2인 검증, AI 원문 금지)은 있으나 체크리스트·승인 절차의 운영 방식 미확정 | 2026-09-21 | TBD |
| R6 | prefix cache 효과를 장부에 잡지 못한다 (`cached_tokens`가 항상 0) | [evaluation.md](evaluation.md) §5.2의 "cache — prefix" 지표가 산출 불가. B3'의 주장 중 하나가 prefix 재사용으로 입력 토큰을 줄인다는 것인데, 그 효과를 재지 못하면 조건 간 비교에서 이점이 0으로 보인다. **어댑터 결함이 아니다**: 하네스를 거치지 않고 raw HTTP로 직접 호출해도 응답의 `prompt_tokens_details`가 null로 온다(2026-08-11 세라프 실측). 서버가 값을 주지 않는 것이다. 세라프는 드라이버 535(CUDA 12.2 상한)라 vLLM 0.19.1까지만 쓸 수 있어 최신 vLLM의 동작을 확인할 방법이 없다. **ATOM(CUDA 13.0)에서 먼저 확인한다** — 낡은 서버 동작에 맞춰 `_usage_from`을 고치면 ATOM에서 오히려 틀린 코드가 된다. **2026-08-22 ATOM 실측 — 범위가 넓어졌다**: 지금까지 이 항목의 관측은 `cli bench` 경로에서 나왔는데, **`cli run` 경로도 같다.** `run_20260821_0004`(prefix caching on, fp8 4B)의 `model_call` 네 건이 전부 `cached_tokens: 0`인데 같은 run의 vLLM 서버 로그는 `Prefix cache hit rate: 19.9%`를 찍었다([실험 보고서](experiments/harness/2026-08-22-pipeline-atom.md) §3 해석 ⑨). **서버는 재사용을 하고 있다** — 즉 "캐시가 안 맞았다"가 아니라 "응답이 그 사실을 싣지 않는다"이고, 어느 경로로 부르든 장부가 그 효과를 0으로 본다. **2026-08-22 재확인** — `run_20260821_0007`(서버 argv에 `--enable-prefix-caching`)의 `model_call` 네 건도 전부 `cached_tokens: 0`이다. **다만 이번에는 서버 쪽 적중률을 찍지 않아** 19.9% 같은 대조값이 없다 — 재확인된 것은 **장부가 0이라는 사실 하나**이고, "서버는 재사용하는데 응답이 안 싣는다"는 판정의 근거는 여전히 `run_20260821_0004`의 그 로그다. **2026-08-23 — 세 번째 확인, 대조값은 이번에도 없다**([실험 보고서](experiments/harness/2026-08-23-pipeline-zlib-atom.md)): `--enable-prefix-caching`으로 띄운 zlib run의 실서빙 256콜이 `cached_tokens` **0 / 514,960 (0.0%)**이다. **표본이 4콜에서 256콜이 됐는데도 0이다** — "우연히 안 맞았다"는 설명이 이 규모에서는 성립하지 않는다. 그러나 **서버 쪽 적중률은 또 못 받았다**: vLLM을 포그라운드로 띄우고 창을 닫았다. 판정의 근거는 여전히 `run_20260821_0004`의 19.9% 하나이고, **같은 유실이 세 번째**라 [experiments/README.md](experiments/README.md)에 `tee` 규약을 넣었다 | 2026-09-07 | TBD |
| R7 | `gc`가 지운 run_id를 같은 날 새 run이 재사용한다 | 회의록·이슈·비교표에 적어 둔 `run_20260811_0001`이 **다른 데이터를 가리키게 된다.** 그 run_id로 남긴 결론을 나중에 검증할 수 없고, 틀렸다는 사실조차 드러나지 않는다 — 이 PR이 고친 세 결함과 같은 계열(조용히 틀린 값)이다. `cli._claim_run_dir`는 같은 날 쓰인 번호의 max+1을 고르므로, `gc --apply`로 `_0001`이 사라지면 그 번호가 다시 난다. **v1에서는 막지 않기로 했다** — 지우는 것은 명시적 사용자 조작이고 산출물은 이미 사라진 뒤다. 해법 후보: (a) 삭제한 번호를 `artifacts_root`에 기록해 건너뛴다 (b) `gc`가 같은 날 run은 지우지 않는다. **2026-08-22 실측 — 이 시나리오는 아직 실현되지 않았다**: `run_20260821_0004`가 산출물 전량과 함께 보존돼 있고(`state.db` 28,672 B · `trace.jsonl` 18,625 B, mtime 2026-08-22 01:42:23 KST), 같은 날 뒤이어 난 `run_20260821_0007`은 `_0004`가 아니라 다음 번호를 받았다. **`gc --apply`를 돌리지 않았기 때문이고, 항목이 해소된 것이 아니다** — 번호 재사용은 삭제가 선행돼야 일어난다 | TBD | TBD |

## 2. 설계 결정 대기

> **⏸ 모델 선정 대기 보류 (2026-08-30, 5차 회의).** scout·auditor·critic 모델과 오케스트레이션 방안(큰 모델 하나 vs 소형 앙상블)이 확정되기 전까지 그에 종속된 항목은 수정을 보류한다 — 기조가 바뀌면 실측 축이 통째로 달라져 지금 고치는 것이 무의미하다. 해당: **D2·D3·D4·D24·D28**(임계값·양자화·점수·후보수), **D29**(샘플링 등가화 남은 부분), **D51**(열거 강제), **D54**(v2 모델 배선). 미해결 D는 GitHub Issue로 추적한다 — **현재 예외 넷은 D9·D19·D20·D21이고 이슈를 만드는 것이 남았다**(D9는 범위 결정 대기, 나머지 셋은 부분 해소라 잔여가 각 행에 있다).

| # | 항목 | 상태 | 본문 |
| --- | --- | --- | --- |
| D1 | 벤치마크 라벨 통합 | 열림 | [본문](open-questions/D1.md) |
| D2 | Scout 임계값·단계별 승격 비율 | 보류 | [본문](open-questions/D2.md) |
| D3 | Capsule 확장 횟수 상한 | 보류 | [본문](open-questions/D3.md) |
| D4 | Critic 양자화 검증 표본 크기와 허용 하락폭 | 보류 | [본문](open-questions/D4.md) |
| D5 | GPU-hour → 금액 환산식 | 열림 | [본문](open-questions/D5.md) |
| D6 | 상태 저장소 SQLite vs DuckDB | 결정 | [본문](open-questions/D6.md) |
| D7 | 오케스트레이터 LangGraph v1 확정 여부 | 결정 | [본문](open-questions/D7.md) |
| D8 | **Evidence Gate와 Critic의 순서** | 닫힘 | [본문](open-questions/D8.md) |
| D9 | 1차 분석 대상의 범위 | 열림 | [본문](open-questions/D9.md) |
| D10 | 재평가 횟수를 어디에 보관하는가 | 열림 | [본문](open-questions/D10.md) |
| D11 | Evidence에 "발견 건수"가 없다 | 열림 | [본문](open-questions/D11.md) |
| D13 | **캡슐의 그래프 파생 섹션(`callers`/`callees`/`types`)을 무엇이 채우는가** | 열림 | [본문](open-questions/D13.md) |
| D12 | build를 몇 단계에서 실행하는가 | 열림 | [본문](open-questions/D12.md) |
| D14 | **재현성 비교가 `data` 안의 시간 파생값을 빼지 않는다** | 열림 | [본문](open-questions/D14.md) |
| D15 | **`Budget.tool_seconds`의 값에 실대상 근거가 없다** | 열림 | [본문](open-questions/D15.md) |
| D16 | **확장자로 대상을 고르는 도구가 `.inc`를 조용히 건너뛴다** | 열림 | [본문](open-questions/D16.md) |
| D17 | **프롬프트의 "사실 블록이 전부다"가 산문 자리표시자를 전부 장식으로 만든다** | 열림 | [본문](open-questions/D17.md) |
| D18 | **8단계 정책이 게이트 등급을 읽지 않는다** | 열림 | [본문](open-questions/D18.md) |
| D19 | **프롬프트 본문이 실행 산출물에 남지 않아 W1을 실행에서 검증할 수 없다** | 부분 | [본문](open-questions/D19.md) |
| D20 | **Scout이 캡슐로 호출 그래프를 받지 못한다** | 부분 | [본문](open-questions/D20.md) |
| D21 | **토큰 계상이 실서빙 실측보다 1.8배 과소다** | 부분 | [본문](open-questions/D21.md) |
| D22 | **`callers`가 사실 블록 최상위 키로 고정돼 있어 안 본 것을 "0건"으로 말한다** | 열림 | [본문](open-questions/D22.md) |
| D23 | **`Evidence.location`을 채우는 코드가 저장소에 없어 Gate E1의 `location_ok`가 구조적으로 거짓이다** | 열림 | [본문](open-questions/D23.md) |
| D24 | **후보 1건당 토큰·시간을 한 번도 재지 않아 `candidate_max`에 근거가 없다** | 보류 | [본문](open-questions/D24.md) |
| D25 | ~~`queue_maxsize`를 소비하는 코드가 후보 경로에 없다~~ | 닫힘 | [본문](open-questions/D25.md) |
| D26 | ~~6단계 통과 조건(E2 = `path ∧ graph ∧ unblocked`)과 8단계 진입 조건(`repro_spec`)이 서로를 배제한다~~ | 닫힘 | [본문](open-questions/D26.md) |
| D27 | **장부 `cached_tokens`가 실서빙과 Fake에서 역전돼 있다** | 열림 | [본문](open-questions/D27.md) |
| D28 | **Scout 점수에 해상도가 없다 — 원인 미확정** | 보류 | [본문](open-questions/D28.md) |
| D29 | **서버 `generation_config`의 `top_k`가 run 기록 어디에도 안 남는다** | 보류 | [본문](open-questions/D29.md) |
| D30 | **`single`이라는 이름에 세 문서가 서로 다른 뜻을 붙인다** | 열림 | [본문](open-questions/D30.md) |
| D31 | ~~`Evidence.ok`가 "실패"와 "해당 없음"을 못 가른다~~ | 결정 | [본문](open-questions/D31.md) |
| D32 | **컴파일 플래그(`-std`·`-I`)가 코드 상수인데 `RunRecord`에 없다** | 열림 | [본문](open-questions/D32.md) |
| D33 | ~~`run_20260825_0001`이 옛 build 배선의 마지막 완전 측정이다~~ | 닫힘 | [본문](open-questions/D33.md) |
| D34 | **언어별 Command 분할에서 첫 그룹의 발견이 둘째 그룹을 가린다** | 열림 | [본문](open-questions/D34.md) |
| D35 | ~~9단계 `dedupe()`가 E3를 지운다~~ | 부분 | [본문](open-questions/D35.md) |
| D36 | ~~`report.md`에 E 등급이 없다 — run의 성과가 최종 산출물에 안 보인다~~ | 닫힘 | [본문](open-questions/D36.md) |
| D37 | **`run_manifest.json`에 `profile`이 없다** | 열림 | [본문](open-questions/D37.md) |
| D38 | **E4는 Fake auditor 프로필에서 원리상 도달 불가다 — 조건 명시 항목** | 결함 아님 | [본문](open-questions/D38.md) |
| D39 | **심볼 단위 후보에서 캡슐 캐시는 원리상 무효다 — 조건 명시 항목** | 결함 아님 | [본문](open-questions/D39.md) |
| D40 | **8단계가 dedupe 이전이라 같은 파일에 도구를 반복 실행한다** | 열림 | [본문](open-questions/D40.md) |
| D41 | **예산이 실제로 캡슐을 묶었는지 run이 남기지 않는다** | 열림 | [본문](open-questions/D41.md) |
| D42 | ~~요청당 출력 상한이 없다 — 하네스가 `max_tokens`를 안 보낸다~~ | 닫힘 | [본문](open-questions/D42.md) |
| D43 | **캡슐 재검증이 `using_counter` 밖에서 돌아 저장과 로드가 갈린다** | 부분 | [본문](open-questions/D43.md) |
| D44 | ~~critic 단계의 파싱 실패가 run 전체를 죽인다~~ | 닫힘 | [본문](open-questions/D44.md) |
| D45 | ~~실행한 대상의 심볼이 기록에 안 남는다 — 건너뛴 대상만 남는다~~ | 닫힘 | [본문](open-questions/D45.md) |
| D46 | **구조화 출력이 공백 폭주로 안 닫힌 JSON을 낸다 — 상한을 키워도 안 닫힌다** | 열림 | [본문](open-questions/D46.md) |
| D47 | ~~스키마 재시도가 실패한 응답 원문을 프롬프트에 되싣는다 — D42의 상한이 그 자리에서 입력 초과로 되돌아온다~~ | 닫힘 | [본문](open-questions/D47.md) |
| D48 | ~~`target_skipped.stage`가 실제로 실패한 단계를 안 가리킨다~~ | 닫힘 | [본문](open-questions/D48.md) |
| D49 | **재시도 프롬프트가 산출물에 안 남는다 — 실제로 서버에 간 요청을 재현할 수 없다** | 열림 | [본문](open-questions/D49.md) |
| D50 | ~~`--tool-runner` 경고가 값을 비교하지 않아 오버라이드가 아닌 run을 오버라이드로 신고한다~~ | 닫힘 | [본문](open-questions/D50.md) |
| D51 | **`trigger_status`의 보류 두 건 — (2) 필드 순서는 닫혔고 (1) 열거 강제가 남았다** | 보류 | [본문](open-questions/D51.md) |
| D52 | ~~`trigger_status`를 내라고 시키는 프롬프트가 저장소에 없어 그 필드는 구조적으로 항상 `null`이다~~ | 닫힘 | [본문](open-questions/D52.md) |
| D53 | **실패한 `model_call`이 예산 장부에 한 푼도 안 실린다 — 폭주가 잦은 조건일수록 싸 보인다** | 열림 | [본문](open-questions/D53.md) |
| D54 | **`config/models.yaml`의 어느 모델도 v2 프롬프트를 고르지 않아 머지된 v2가 도달 불가다** | 보류 | [본문](open-questions/D54.md) |
| D55 | **재시도가 `model_call` 한 건으로 뭉개져 "스키마 파싱 실패율"이 과소 집계된다** | 열림 | [본문](open-questions/D55.md) |

## 3. 팀·운영 미정

| # | 항목 | 관련 문서 |
| --- | --- | --- |
| O1 | 멘티 8명의 개인 트랙 배정 (T1~T8) | [roadmap.md](roadmap.md) §3 |
| O2 | 트랙 간 동기화 방식과 주간 통합 시점 | [roadmap.md](roadmap.md) §3 |
| O3 | 단계별 세부 역할 분담 | [roadmap.md](roadmap.md) §3 |
| O4 | 평가 데이터셋 실제 확보 상태 (6종 전부 TBD) | [evaluation.md](evaluation.md) §3 |
| O5 | 제보처 무응답 시 대기 기간·후속 조치 | [disclosure-policy.md](disclosure-policy.md) §3 |
| O7 | 하드웨어(AI TOP ATOM) 사양·유지보수 항목(Model Registry, 4개 인터페이스, 재현성)이 킥오프 발표자료에는 없음 — 발표 범위에 포함할지 | [model-serving.md](model-serving.md), [engineering-guide.md](engineering-guide.md) |

> O6(킥오프 PDF 대조)은 2026-08-07 완료. 결과는 D8·D9·O7과 각 문서 반영분으로 이관.

## 4. 기록 규칙

- 항목이 해결되면 결정 내용을 관련 문서에 반영하고, 되돌리기 비싼 결정이면 `docs/decisions/`에 ADR을 남긴다.
- 담당이 `TBD`인 채로 기한을 넘긴 항목은 주간 통합 회의의 첫 안건으로 올린다.
