# 미해결 리스크와 결정 대기 항목

"아직 안 정해짐"을 문서에 숨기지 않기 위한 목록. 결정되면 해당 문서로 옮기고 여기서 지운다.

## 1. 블로킹 리스크

| # | 항목 | 왜 위험한가 | 결정 기한 | 담당 |
| --- | --- | --- | --- | --- |
| R1 | AI TOP ATOM(sm_121)에서 vLLM 버전·커널 호환성 미검증 | 크리티컬 패스 정지. GB10 전용 실행 지원은 2026-06부터이고 NVFP4 KV cache·speculative decoding은 커뮤니티 fork 수준일 수 있음 | 2026-08-17 | TBD |
| R2 | Critic(Devstral Small 2, dense 24B) NVFP4 양자화 시 반박 정확도 저하 가능성 | B3'의 핵심 효과를 측정 불가하게 만듦 | 2026-08-31 | TBD |
| R3 | 평가 데이터 누출 자동 검증 스크립트 부재 | 코드 리뷰만으로는 보장 불가. 누락 시 모든 비교 결과 무효 | 2026-09-07 | TBD |
| R4 | 단일 노드 대역폭 병목의 실제 한계 미파악 | 4조건 실행 시간이 일정을 초과할 수 있음. ConnectX-7 2대 확장 판단의 전제 | 2026-09-05 | TBD |
| R5 | Zero-day 제보 워크플로의 강제 수단 미정 | 원칙(2인 검증, AI 원문 금지)은 있으나 체크리스트·승인 절차의 운영 방식 미확정 | 2026-09-21 | TBD |

## 2. 설계 결정 대기

| # | 항목 | 선택지 | 관련 문서 |
| --- | --- | --- | --- |
| D1 | 벤치마크 라벨 통합 | PRD §2.1의 2×2와 §5.2의 파이프라인 조건이 같은 라벨(B1/B1'/B3/B3')을 다른 축으로 사용. 라벨 분리 vs 재정의 | [evaluation.md](evaluation.md) §2 |
| D2 | Scout 임계값·단계별 승격 비율 | 실측 후 확정 (초기값 필요) | [architecture.md](architecture.md) §4 |
| D3 | Capsule 확장 횟수 상한 | 초기값 3, 실측 후 조정 | [knowledge-base.md](knowledge-base.md) §5 |
| D4 | Critic 양자화 검증 표본 크기와 허용 하락폭 | 표본 N, 일치율 하락 허용 % | [model-serving.md](model-serving.md) §4 |
| D5 | GPU-hour → 금액 환산식 | 전력 단가, 장비 감가 가정 | [evaluation.md](evaluation.md) §5.3 |
| D6 | 상태 저장소 SQLite vs DuckDB | 쓰기 패턴과 분석 쿼리 비중에 따라 | [engineering-guide.md](engineering-guide.md) §1 |
| D7 | 오케스트레이터 LangGraph v1 확정 여부 | 노드를 순수 함수로 유지한다는 전제 하에 확정 | [engineering-guide.md](engineering-guide.md) §4 |
| D8 | **Evidence Gate와 Critic의 순서** | 킥오프 자료 내부 불일치: 04-6 표는 `Critic(4차) → Evidence Gate(5차)`, 05-2 구조도와 하네스 제안서는 `Evidence Gate → (high-value only) → Critic`. 현재 문서는 후자 채택 | [architecture.md](architecture.md) §3 |
| D9 | 1차 분석 대상의 범위 | 킥오프 04-1은 "펌웨어의 취약점을 분석"으로 명시. 소스 저장소 우선 vs 펌웨어 우선에 따라 Preprocessor·Index 구현 순서가 갈림 | [architecture.md](architecture.md) §3 |

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
