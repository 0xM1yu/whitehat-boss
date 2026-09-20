# 문서 인덱스

소형 Open-Weight LLM 앙상블 기반 취약점 연구 Multi-Agent Orchestration 프로젝트의 실행 문서 모음.

- 팀: 화햇대장 (WHITEHAT SCHOOL 4기)
- 원본 문서(같은 폴더): [prd.md](prd.md), [Multi-Agent Vulnerability Harness.md](Multi-Agent%20Vulnerability%20Harness.md), 킥오프 발표자료 PDF(28p, 이미지 스캔본 — 텍스트 레이어 없음)

## 문서 지도

| 문서 | 다루는 것 | 주 독자 |
| --- | --- | --- |
| **[PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md)** | **모델 로스터·인덱서의 정본.** 모델을 현재형으로 읽기 전에 여기부터 (CLAUDE.md §7) | 전원 |
| [architecture.md](architecture.md) | 파이프라인 단계, 구성 요소 책임, 상태 전이 | 전원 |
| [agent-contracts.md](agent-contracts.md) | 역할별 실행 계약, 입출력 스키마, 프롬프트 규약 | 하네스/에이전트 담당 |
| [knowledge-base.md](knowledge-base.md) | KB 6계층, Evidence Store, Hybrid Retrieval, Context Capsule | 검색/데이터 담당 |
| [model-serving.md](model-serving.md) | Model Registry, 하드웨어 프로필, 양자화·서빙 검증 절차 | 인프라 담당 |
| [portability.md](portability.md) | 하네스가 장비에 요구하는 것, 그 독립성의 확인 방법, 이식성 결함 대장 | 인프라 담당 |
| [evaluation.md](evaluation.md) | B1/B1'/B3/B3' 조건, 데이터셋, 누출 방지, 지표 정의 | 평가 담당 |
| [engineering-guide.md](engineering-guide.md) | 저장소 구조, 4개 인터페이스, 재현성, 관찰 가능성, 개발 규칙 | 개발 전원 |
| [build-plan.md](build-plan.md) | **앞으로 무엇을 어느 순서로 짓는가** — 단계별 모델 자리, 성능 레버, 파인튜닝, 실행 순서 | 전원 |
| [disclosure-policy.md](disclosure-policy.md) | 취약점 제보 워크플로, 2인 검증, 연구 윤리·법적 경계 | 전원 |
| [open-questions.md](open-questions.md) | 미해결 리스크와 결정 대기 항목 | PL/PM |
| [experiments/](experiments/) | 실험 보고서 원문과 **대조로 확정된 사실** | 전원 |
| [decisions/](decisions/) | 되돌리기 비싼 결정의 기록(ADR) | 전원 |

## 문서 규칙

1. PRD는 "무엇을 왜"를, `docs/`는 "어떻게"를 담는다. 목표 변경은 PRD를 먼저 고치고 여기에 반영한다.
   **이 저장소는 일정을 들지 않는다** — 날짜는 실측의 출처로만 쓴다(규칙 4).
2. 되돌리기 비싼 결정(오케스트레이터, 상태 저장소, 모델 계열, 평가 프로토콜)은 [decisions/](decisions/)에 ADR로 남긴다.
3. 아직 정해지지 않은 것은 지어내지 말고 `TBD`로 [open-questions.md](open-questions.md)에 적는다.
4. 측정값은 출처(실측/문서/추정)를 표기한다. 추정치는 `(추정)`으로 명시한다.
5. 실험 보고서는 **그 시점의 관측 기록이므로 주장을 소급 편집하지 않는다.** 서식 정리는
   누구나 하되 주장 수정은 작성자만 한다. 이후 대조로 정정된 사실은
   [experiments/README.md](experiments/README.md)의 확정 표가 담는다.
