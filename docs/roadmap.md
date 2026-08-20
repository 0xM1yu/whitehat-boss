# 로드맵과 완료 기준

프로젝트 기간: 2026-08-03 ~ 2026-10-17

## 1. 마일스톤

| 단계 | 일자 | 목표 | 산출물 | 완료 기준(DoD) |
| --- | --- | --- | --- | --- |
| 킥오프 | 2026-08-03 | 프로젝트 기반 마련 | 역할 분담표, 로컬 서빙 환경, 후보 모델 조사, 비교지표 정의서 | 8개 트랙 담당자 확정 · 장비에서 모델 1종 기동 성공 · [evaluation.md](evaluation.md) 지표 표 합의 |
| 1차 | 2026-08-17 | 모델 서빙 환경 안정화 | 소형 모델 서빙 파이프라인, 단일 모델 베이스라인 설계 | [model-serving.md](model-serving.md) §3 체크리스트 전 항목 실측 완료 · Registry `benchmark` 값 채워짐 · B1 조건 1회 완주 |
| 2차 | 2026-08-31 | Multi-Agent 구조 설계 및 앙상블 실험 | Orchestration 구조 완성, 앙상블 실험 결과, **Harness v1** | Scout→Auditor→Critic 경로가 스키마 위반 없이 동작 · 단계별 제거율 기록 · 4개 조건 중 최소 2개 실행 |
| 중간 발표 | 2026-09-05 | 실현 가능성 검증 | End-to-End 취약점 도출 시연, Frontier 대비 비교(초안) | 알려진 정답 샘플에서 `proven` 1건 이상 재현 · 비교표 초안의 비용 칸이 실측값 |
| 3차 | 2026-09-07 | Harness 안정성·재현성 확보 | 고도화 Harness, Agent 간 통신 안정화 | 동일 seed·조건 재실행 시 결과 재현 · `RunRecord` 필수 항목 100% 기록 · 누출 검증 스크립트 CI 통과 |
| 4차 | 2026-09-21 | 정량 비교 및 실전 확장 | 비용·성능·Privacy 벤치마크 결과표, Zero-day 헌팅 시작 | 4개 조건 전부 동일 예산에서 3회 반복 완료 · benign 표본 오탐률 산출 |
| 5차 | 2026-10-05 | 제보 가능한 성과 확보 | 취약점 제보, 헌팅·벤치마크 종합 | [disclosure-policy.md](disclosure-policy.md) 절차를 통과한 제보 1건 이상 |
| 최종 발표 | 2026-10-17 | 성과 공유·공개 | 오픈소스 공개, 비교 리포트/논문, 발표 자료 | 외부인이 README만으로 재현 실행 가능 · 비교표 전 칸 실측 |

## 2. 크리티컬 패스

```
장비 검증(vLLM×sm_121) ──> 서빙 안정화 ──> Harness v1 ──> 4조건 실행 ──> 비교표
        ▲ 실패 시 전체 일정 지연                    ▲
        └ 1차(08-17) 전 반드시 판정                 └ 누출 검증 스크립트가 없으면 결과 무효
```

우선순위가 충돌하면 이 순서로 지킨다: **장비 검증 > 누출 방지 > 4조건 실행 > 신규 취약점 헌팅.** 헌팅은 가장 매력적이지만 앞의 셋이 없으면 결과를 주장할 수 없다.

## 3. 8-way 트랙

멘토 1 + PL 1 + 멘티 8명(PM 포함). 멘티 8명은 2인 1조로 시간을 나눠 쓰지 않고, **각자 하나의 트랙을 전담하여 프로젝트 시작부터 끝까지 병렬로 진행**한다.

명단 (킥오프 발표자료 01):

| 구분 | 이름 | 비고 |
| --- | --- | --- |
| MENTOR | 조현우 | |
| PL | @HUMBLE25 | |
| 멘티 | @0xM1yu | 12반 · PM |
| 멘티 | 김하은 | 12반 |
| 멘티 | @sumin0218 | 20반 |
| 멘티 | @danielcho02 | 31반 |
| 멘티 | 서영채 | 11반 |
| 멘티 | 김예준 | 18반 |
| 멘티 | 김레빈 | 29반 |
| 멘티 | 류한솔 | 35반 |

트랙 후보(배정은 협의 후 확정):

| # | 트랙 | 주 산출물 | 관련 문서 |
| --- | --- | --- | --- |
| T1 | 서빙·인프라 | vLLM/LiteLLM 기동, 양자화 실측, Registry | [model-serving.md](model-serving.md) |
| T2 | 오케스트레이터 | 파이프라인 노드, 상태 머신, checkpoint | [architecture.md](architecture.md) |
| T3 | 인덱싱·그래프 | tree-sitter, symbol/xref/call graph | [knowledge-base.md](knowledge-base.md) |
| T4 | Retrieval·Capsule | hybrid retrieval, 캡슐 정책, 캐시 | [knowledge-base.md](knowledge-base.md) |
| T5 | 에이전트·프롬프트 | Scout/Auditor/Critic 프롬프트와 계약 | [agent-contracts.md](agent-contracts.md) |
| T6 | 도구 검증 | 정적분석·build·sanitizer·fuzz 래퍼, sandbox | [engineering-guide.md](engineering-guide.md) |
| T7 | 평가·데이터셋 | 데이터셋 구축, 누출 검증, 지표 파이프라인 | [evaluation.md](evaluation.md) |
| T8 | 헌팅·제보·리포트 | 실제 대상 헌팅, 2인 검증, 리포트·논문 | [disclosure-policy.md](disclosure-policy.md) |

**트랙 배정과 동기화 방식은 아직 미정이다(TBD).** → [open-questions.md](open-questions.md)

### 병렬 작업의 동기화 지점

8개 트랙이 동시에 움직이므로 인터페이스가 계약이다.

1. `src/schemas/`와 `src/interfaces/`는 **변경 시 전원 공지**. 이 두 곳이 트랙 간 유일한 결합점이다.
2. 각 트랙은 상대 트랙의 구현이 아니라 인터페이스에 대고 개발한다(가짜 구현 허용).
3. 주간 통합 시점을 정해 전체 파이프라인을 1회 완주시킨다. 완주 실패는 그 주의 최우선 과제다.

## 4. 리스크가 일정에 미치는 영향

| 리스크 | 영향 | 완화 |
| --- | --- | --- |
| vLLM × sm_121 비호환 | 크리티컬 패스 정지 | 1차(2026-08-17) 전 판정, 실패 시 대체 서빙 스택 즉시 검토 |
| Critic 양자화 품질 저하 | B3' 효과 측정 불가 | 고정밀도 fallback 프로필 준비 |
| 단일 노드 대역폭 병목 | 4조건 실행 시간 초과 | 중간 발표 전 한계 파악, 후보 수·context 상한 조정 |
| 누출 검증 미비 | 모든 비교 결과 무효 | 3차(2026-09-07)까지 CI 필수 |
| 제보 프로세스 미정의 | 5차 성과 지연 | [disclosure-policy.md](disclosure-policy.md) 선행 확정 |
