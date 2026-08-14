# 미해결 리스크와 결정 대기 항목

"아직 안 정해짐"을 문서에 숨기지 않기 위한 목록. 결정되면 해당 문서로 옮기고 여기서 지운다.

## 1. 블로킹 리스크

| # | 항목 | 왜 위험한가 | 결정 기한 | 담당 |
| --- | --- | --- | --- | --- |
| R1 | AI TOP ATOM(sm_121)에서 vLLM 버전·커널 호환성 미검증 | 크리티컬 패스 정지. GB10 전용 실행 지원은 2026-06부터이고 NVFP4 KV cache·speculative decoding은 커뮤니티 fork 수준일 수 있음 | 2026-08-17 | TBD |
| R2 | Critic(Devstral Small 2, dense 24B) NVFP4 양자화 시 반박 정확도 저하 가능성 | B3'의 핵심 효과를 측정 불가하게 만듦 | 2026-08-31 | TBD |
| R3 | 평가 데이터 누출 자동 검증 스크립트 부재 | 코드 리뷰만으로는 보장 불가. 누락 시 모든 비교 결과 무효. **부분 착수**: `eval/leakage/`가 [evaluation.md](evaluation.md) §4 표의 7개 중 2개(hash 교집합, CVE·flag·문제명·patch diff 마스킹)를 구현하고 CI에서 돈다. 남은 5개(Patch Leakage 출처 검사, 중복 데이터 제거, Time Split, Prompt Logging, Reproducibility)는 미구현이므로 **해결 아님** | 2026-09-07 | TBD |
| R4 | 단일 노드 대역폭 병목의 실제 한계 미파악 | 4조건 실행 시간이 일정을 초과할 수 있음. ConnectX-7 2대 확장 판단의 전제 | 2026-09-05 | TBD |
| R5 | Zero-day 제보 워크플로의 강제 수단 미정 | 원칙(2인 검증, AI 원문 금지)은 있으나 체크리스트·승인 절차의 운영 방식 미확정 | 2026-09-21 | TBD |
| R6 | prefix cache 효과를 장부에 잡지 못한다 (`cached_tokens`가 항상 0) | [evaluation.md](evaluation.md) §5.2의 "cache — prefix" 지표가 산출 불가. B3'의 주장 중 하나가 prefix 재사용으로 입력 토큰을 줄인다는 것인데, 그 효과를 재지 못하면 조건 간 비교에서 이점이 0으로 보인다. **어댑터 결함이 아니다**: 하네스를 거치지 않고 raw HTTP로 직접 호출해도 응답의 `prompt_tokens_details`가 null로 온다(2026-08-11 세라프 실측). 서버가 값을 주지 않는 것이다. 세라프는 드라이버 535(CUDA 12.2 상한)라 vLLM 0.19.1까지만 쓸 수 있어 최신 vLLM의 동작을 확인할 방법이 없다. **ATOM(CUDA 13.0)에서 먼저 확인한다** — 낡은 서버 동작에 맞춰 `_usage_from`을 고치면 ATOM에서 오히려 틀린 코드가 된다 | 2026-09-07 | TBD |
| R7 | `gc`가 지운 run_id를 같은 날 새 run이 재사용한다 | 회의록·이슈·비교표에 적어 둔 `run_20260811_0001`이 **다른 데이터를 가리키게 된다.** 그 run_id로 남긴 결론을 나중에 검증할 수 없고, 틀렸다는 사실조차 드러나지 않는다 — 이 PR이 고친 세 결함과 같은 계열(조용히 틀린 값)이다. `cli._claim_run_dir`는 같은 날 쓰인 번호의 max+1을 고르므로, `gc --apply`로 `_0001`이 사라지면 그 번호가 다시 난다. **v1에서는 막지 않기로 했다** — 지우는 것은 명시적 사용자 조작이고 산출물은 이미 사라진 뒤다. 해법 후보: (a) 삭제한 번호를 `artifacts_root`에 기록해 건너뛴다 (b) `gc`가 같은 날 run은 지우지 않는다 | TBD | TBD |

## 2. 설계 결정 대기

| # | 항목 | 선택지 | 관련 문서 |
| --- | --- | --- | --- |
| D1 | 벤치마크 라벨 통합 | PRD §2.1의 2×2와 §5.2의 파이프라인 조건이 같은 라벨(B1/B1'/B3/B3')을 다른 축으로 사용. 라벨 분리 vs 재정의 | [evaluation.md](evaluation.md) §2 |
| D2 | Scout 임계값·단계별 승격 비율 | 실측 후 확정 (초기값 필요). **2026-08-14 첫 실도구 실행 실측**: benign 표본(`util.c`)이 `scout_threshold: 0.5`와 **정확히 같은 0.50**으로 통과했다. 0.30(`_SCORE_BASE`) + 0.20(`_W_STATIC_SIGNAL`)이고 risky_call 가산점은 없다 — 즉 순전히 정적 신호 하나 때문이다. `fake_model._SCORE_BASE` 주석은 benign이 0.40에 머물러 0.1의 여유가 있다고 봤는데, 그 전제가 **Fake 신호 생성기(정규식 2개)**를 기준으로 한 것이었다. 실제 정적분석기는 benign에도 신호를 준다(clang-tidy `DeprecatedOrUnsafeBufferHandling`, semgrep `c.lang.security.insecure-use-string-copy-fn` — 둘 다 `_s` 계열을 쓰지 않는 모든 복사를 잡는 룰이라 경계를 지킨 코드도 걸린다). **임계값은 지금 바꾸지 않는다**: 0.55로 올리면 그것은 근거가 아니라 *픽스처가 통과하지 못하게* 맞춘 값이고, `>=`를 `>`로 바꾸는 것은 앞으로의 모든 비교를 조용히 바꾸므로 더 나쁘다. 데이터셋 확보 후 실측으로 정한다. **함께 드러난 것**: 오탐을 거르는 것은 뒤 단계의 일인데 이번 실행에서 못 걸렀다 — Fake auditor는 항상 path를 만들고 Fake critic은 graph만 있으면 uphold하므로 두 Finding 모두 E3까지 올라갔다. **Fake 모델로는 오탐 제거율을 측정할 수 없다**(STATUS.md §10) | [architecture.md](architecture.md) §4 |
| D3 | Capsule 확장 횟수 상한 | 초기값 3, 실측 후 조정 | [knowledge-base.md](knowledge-base.md) §5 |
| D4 | Critic 양자화 검증 표본 크기와 허용 하락폭 | 표본 N, 일치율 하락 허용 % | [model-serving.md](model-serving.md) §4 |
| D5 | GPU-hour → 금액 환산식 | 전력 단가, 장비 감가 가정 | [evaluation.md](evaluation.md) §5.3 |
| D6 | 상태 저장소 SQLite vs DuckDB | 쓰기 패턴과 분석 쿼리 비중에 따라. **v1은 SQLite로 시작** — [ADR 0001](decisions/0001-state-store-sqlite.md)(상태: 제안). 되돌리는 비용이 낮아 지금 확정하지 않아도 된다 | [engineering-guide.md](engineering-guide.md) §1 |
| D7 | 오케스트레이터 LangGraph v1 확정 여부 | 노드를 순수 함수로 유지한다는 전제 하에 확정. **v1은 순차 최소 러너, LangGraph 보류** — [ADR 0002](decisions/0002-minimal-runner-langgraph-deferred.md)(상태: 제안). 노드 시그니처에 프레임워크 타입을 넣지 않아 이 항목이 열린 채로 유지된다 | [engineering-guide.md](engineering-guide.md) §4 |
| D8 | **Evidence Gate와 Critic의 순서** | 킥오프 자료 내부 불일치: 04-6 표는 `Critic(4차) → Evidence Gate(5차)`, 05-2 구조도와 하네스 제안서는 `Evidence Gate → (high-value only) → Critic`. 현재 문서는 후자 채택 | [architecture.md](architecture.md) §3 |
| D9 | 1차 분석 대상의 범위 | 킥오프 04-1은 "펌웨어의 취약점을 분석"으로 명시. 소스 저장소 우선 vs 펌웨어 우선에 따라 Preprocessor·Index 구현 순서가 갈림 | [architecture.md](architecture.md) §3 |
| D10 | 재평가 횟수를 어디에 보관하는가 | 지금은 `queue.py`가 **run 범위 in-memory 카운터**로 들고 있다. Candidate는 SQLite에 `queued`로 남지만 "몇 번 재평가됐는지"는 run이 끝나면 사라지므로, 다중 run·resume에서 `requeue_limit_reached` 전이가 성립하지 않는다(매 run이 0부터 센다). 선택지: (a) `Candidate.requeue_count` 필드 추가 (b) StateStore에 run 밖 카운터 테이블 (c) 단일 run 전제를 명문화하고 resume 비지원 확정 — **현재 코드는 (c) 가정**. (a)는 `src/schemas/` 변경이라 전원 공지 대상. **resume를 지원하게 되면 스키마 버전 문제도 함께 풀어야 한다**: `adapters/sqlite_store.py`가 `model_validate`로 객체를 되읽으므로 구버전 `state.db`의 `RunRecord`는 필수 필드(`repo_dirty` 등) 누락으로 로드에 실패한다. 지금 안전한 이유는 호환성이 있어서가 아니라 `state.db`가 run 디렉터리 안에 있어 **구버전을 읽는 경로 자체가 없기** 때문이다. resume 설계 시 마이그레이션 또는 버전 게이트가 필요하다 | [architecture.md](architecture.md) §3 Queue 규칙 |
| D11 | Evidence에 "발견 건수"가 없다 | 정적분석 도구는 아무것도 못 찾아도 정상 종료하므로 `Evidence(kind=static, ok=true)`가 남는다. 그대로 두면 "돌렸는데 없었다"가 신호로 세어져 Scout 점수가 오르고 Evidence Gate E1이 모든 Finding에서 참이 된다. 지금은 `nodes/signals.with_signal()`이 `exit_code`로 판별하는데, 이는 ToolRunner의 관례에 기댄 것이다 — **Semgrep은 발견이 있어도 0을 반환하는 모드가 있고 clang-tidy는 다른 관례를 쓴다.** 선택지: (a) `Evidence.findings: int` 추가(전원 공지 대상) (b) 신호 없음은 Evidence로 남기지 않고 trace에만 기록 (c) 도구별 어댑터가 `ok`의 의미를 계약으로 고정. **2026-08-14: (c)를 채택하고 `ContainerToolRunner`가 플래그로 관례를 성립시킨다** — semgrep `--error`(붙이지 않으면 발견해도 0이라 모든 정적 신호가 "없음"이 된다), clang-tidy `--warnings-as-errors=*`. semgrep은 0·1만 정상 종료로 보고 나머지는 도구 실패(`ok=False`)다. **남은 구멍**: clang-tidy는 exit code만으로 "경고를 찾음"과 "대상이 컴파일되지 않음"을 구분하지 못한다 — 둘 다 non-zero다. 지금은 출력에 `clang-diagnostic-error`가 있으면 컴파일 실패로 보고 `ok=False`로 내리는데, **문자열 매칭이라 clang이 표기를 바꾸면 조용히 깨진다.** 그때는 컴파일 실패가 정적 신호로 집계된다 — (a)가 최종 해법인 이유가 여기 있다. **2026-08-14 실측으로 semgrep 부분은 해소**: `--json`을 붙이고 결과 문서의 유무로 "실제로 돌았는지"를 판정한다. 계기는 exit code 관례가 조용히 깨진 사건이다 — semgrep이 `--read-only` 아래 `$HOME/.semgrep`에 쓰려다 처리되지 않은 예외로 죽었고, 파이썬 크래시라 exit=1이었으며, `_SEMGREP_OK = (0, 1)`이 그것을 "발견함"으로 읽어 **한 번도 돌지 않은 도구가 정적 신호를 만들었다.** 이제 문서가 없거나 stderr에 Traceback이 있으면 `ok=False`다. `exit_code`는 프로세스가 반환한 값 그대로 남긴다(합성하지 않는다) — `--error`와 `--json`이 함께 동작하는 것을 실측으로 확인했다(발견 0건 → exit 0, 발견 있음 → exit 1). **clang-tidy 부분은 그대로 미해결**이고, 마커의 범위를 분명히 해 둔다: `clang-diagnostic-error`는 **컴파일 불가만** 잡는다. `--warnings-as-errors`로 승격된 `clang-diagnostic-*` 경고(예: `implicit-function-declaration`)는 파일이 실제로 컴파일된 상태의 진단이므로 정당한 발견으로 센다 | [agent-contracts.md](agent-contracts.md) §3, [architecture.md](architecture.md) §3 |
| D13 | **캡슐의 그래프 파생 섹션(`callers`/`callees`/`types`)을 무엇이 채우는가** | knowledge-base.md §4의 레벨별 구성은 이 셋을 포함하지만, 이들은 call graph에서 나와야 하고 **1단계 Index는 `FakeToolRunner`를 거친다** — 실제 심볼 테이블이 없다. `FakeRetriever`는 이 셋을 고정 64토큰으로 채우는데, 없는 그래프에서 나온 척하는 값이라 Evidence Gate E2와 토큰 회계가 동시에 거짓말을 한다. **현재 코드는 (b) 가정**: `HybridRetriever`([ADR 0004](decisions/0004-retrieval-clients-separate-from-model-client.md))가 검색으로 실제 확보한 것만 `related` 섹션으로 내보내고 그래프 파생 섹션은 비운다. 선택지: (a) 실 인덱서(ToolRunner)를 만들어 세 섹션을 채운다 (b) `related` 하나로 유지하고 knowledge-base.md §4를 코드에 맞춘다 (c) 두 경로를 공존시키고 인덱서 유무에 따라 구성을 바꾼다. 부수 미해결: `HybridRetriever.MAX_CHUNKS`(400) 상한이 `on_truncate` 콜백으로만 드러나고 오케스트레이터가 아직 그 훅을 trace에 연결하지 않는다. **2026-08-14 임시 다리**: `ContainerToolRunner`에서 `index`는 `clang -fsyntax-only`다 — **call graph가 아니라 구문 검사 통과일 뿐이고 `EvidenceKind.GRAPH`라는 이름은 그보다 강한 것을 주장한다.** 이걸 두는 이유는 `index`가 실행 불가로 `ok=False`가 되면 E2의 `graph_ok`가 무너져 **모든 Finding이 E2 위로 못 올라가고 8단계가 영원히 도달 불가**가 되기 때문이다. 한계: include 경로 설정이 필요한 실제 저장소에서는 `-fsyntax-only`가 실패해 GRAPH Evidence가 생기지 않는다(픽스처는 표준 헤더만 쓰므로 통과한다). tree-sitter 인덱서가 (a)로 들어오면 이 다리를 걷어낸다 | [knowledge-base.md](knowledge-base.md) §4, [architecture.md](architecture.md) §3 |
| D12 | build를 몇 단계에서 실행하는가 | [architecture.md](architecture.md) §3은 build를 8단계(Tool Validation) 소관으로 두고 2단계는 sanitizer 로그를 "파싱"하는 단계로 정의한다. 그런데 Auditor는 빌드 가능 여부를 모르면 `repro_spec`을 쓰지 않고, 그러면 E3이 성립하지 않아 **8단계가 영원히 도달 불가**가 된다(도구 검증 제거율의 분모가 0). 현재 코드는 2단계에 build 프로브를 넣어 해결했다. 선택지: (a) §3을 코드에 맞춰 2단계에 build를 포함하도록 고친다 (b) `repro_spec` 생성 조건에서 BUILD 요구를 빼고 8단계가 빌드 실패를 직접 처리한다 | [architecture.md](architecture.md) §3 |

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
</content>
