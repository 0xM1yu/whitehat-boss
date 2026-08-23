# 0004. embedding·reranker를 ModelClient가 아닌 별도 인터페이스로 둔다

- 상태: 제안
- 일자: 2026-08-10
- 관련: [knowledge-base.md](../knowledge-base.md) §3, [engineering-guide.md](../engineering-guide.md) §3, [agent-contracts.md](../agent-contracts.md) §1, [open-questions.md](../open-questions.md) D13

## 배경

`config/profiles.yaml`은 처음부터 `embedding`·`reranker` 역할을 들고 있었지만, 그 값을
쓰는 코드가 없었다. `Role`은 `SCOUT / AUDITOR / CRITIC / JUDGE` 넷뿐이고,
`factory.build_deps()`는 `Retriever`를 `FakeRetriever`로 **하드코딩**하고 있어
`adapters.retriever` 설정값도 읽지 않았다(읽히는 것은 `state_store`뿐이었다).

결과적으로 두 모델을 GPU에 올려도 아무도 호출하지 않는 상태였다. 프로필만 보면
"실서빙 중"으로 읽히므로, 이 어긋남은 벤치마크 조건표(B1/B3)를 조용히 오염시킨다.

이걸 고치려면 embedding·reranker를 어디에 붙일지 정해야 했다.

## 결정

`src/interfaces/retrieval.py`에 `EmbeddingClient`·`RerankerClient` Protocol을 새로 만든다.
`Role` 열거형은 **건드리지 않는다.**

모델 선택 규칙은 기존과 같다 — 프로필의 `roles.embedding` / `roles.reranker`가 registry
키를 가리키고, `ModelEntry.endpoint` 스킴이 실서빙과 Fake를 역할별로 가른다. 그래서
embedding만 띄우고 reranker는 Fake로 두는 구성이 프로필만으로 표현된다.

`adapters.retriever`에 `hybrid`를 추가하고 `factory`가 실제로 그 값을 읽는다.
`retriever: hybrid`인 프로필은 두 역할을 요구하며, 없으면 **설정 로드 시점에** 실패한다
(factory에서 `KeyError`로 터지면 어느 프로필이 잘못됐는지가 스택 아래에 묻힌다).

## 대안과 기각 사유

| 대안 | 기각 사유 |
| --- | --- |
| `Role`에 `EMBEDDING`/`RERANKER` 추가 | `Role`은 agent-contracts.md §1의 **실행 계약 역할**이고 프롬프트 버전·출력 스키마·재시도 정책이 붙는 개념이다. 프롬프트도 스키마도 없는 검색 부품을 같은 열거형에 넣으면 그 계약이 흐려지고, `RunRecord.prompt_versions`처럼 Role을 키로 쓰는 기록에 빈 항목이 생긴다 |
| `ModelClient.complete()`를 확장해 벡터도 반환 | 계약이 "스키마 강제 구조화 출력"인데 반환 타입이 둘로 갈린다. 모든 호출부가 분기해야 하고 `ModelResult.usage`의 의미도 달라진다 |
| Retriever 구현체가 vLLM을 직접 호출 | engineering-guide.md §3의 "도메인 로직은 인터페이스에만 의존" 위반. Fake 대체가 불가능해져 GPU 없는 팀원이 이 경로를 못 돈다 |
| `adapters.retriever`를 계속 무시하고 하드코딩 | 설정에 있는 값이 동작에 영향을 주지 않는 상태가 유지된다. 프로필이 거짓말을 하는 것이 이 ADR이 고치려는 문제다 |

## 결과

- `dev_hybrid` 프로필로 **GPU 없이** 광역 수집 → embedding → reranker 재선별 경로가 돈다.
  실서빙과의 차이는 두 클라이언트 구현뿐이므로, 여기서 통과하는 경로는 vLLM을 붙여도 같다.
- `HybridRetriever`는 `callers`/`callees`/`types` 섹션을 만들지 않는다. 그 셋은 call
  graph에서 나와야 하는데 1단계 Index가 `FakeToolRunner`를 거치기 때문이다 → D13.
- `CapsuleSection.kind`가 `NonEmptyStr`로 열려 있어(스키마 주석: "Progressive Expansion이
  어떤 섹션을 더 붙일지 아직 확정되지 않아 열어 둔다") `related` 추가에 스키마 변경이
  필요하지 않았다.
- 캡슐 정책 버전을 `hybrid-v1`로 따로 둔다. 구성 규칙이 다른데 캐시 키가 같으면 두
  구현이 만든 캡슐이 섞여 조건 비교가 오염된다(knowledge-base.md §6).
- **후속 (PR #4에서 처리)**: 이 결정의 근거("`Role`을 키로 쓰는 기록에 빈 항목이
  생긴다")가 코드 쪽에는 남아 있었다. `config.prompt_versions()`가 프로필의 전 역할을
  순회해 `RunRecord.prompt_versions`에 `embedding: v1`·`reranker: v1`이 적히고 있었고,
  `build_deps()`는 그 두 역할에도 채팅 완성용 `ModelClient`를 만들고 있었다.
  `config.PROMPTED_ROLES`를 단일 출처로 두어 둘 다 정리했다. `model_checksums()`는
  그대로 전 역할을 담는다 — 임베딩 모델이 바뀌면 검색 결과가 바뀌므로 재현성 기록에서
  뺄 수 없고, `RunRecord`가 두 dict를 독립 필드로 선언하고 있어 키 집합이 달라도 된다.
- **후속 (2026-08-23)**: "`Role` 열거형은 건드리지 않는다"는 이 문서의 문장이
  `Role.SINGLE` 추가로 한 번 깨졌다. **이 결정의 근거는 깨지 않는다.** 기각한 것은
  `EMBEDDING`/`RERANKER`였고 사유는 "프롬프트도 스키마도 없는 검색 부품"이었는데,
  `single`은 `prompts/single/v2/`와 `output_schema: Finding`을 가진 실행 계약 역할이라
  그 사유가 적용되지 않는다 — 오히려 위 표의 기준("프롬프트 버전·출력 스키마·재시도
  정책이 붙는 개념")을 만족한다.
  왜 지금 추가했는가: `config.PROMPTED_ROLES`에 `"single"`이 먼저 들어와 있었고
  열거형은 비어 있어 `Role("single")`이 `ValueError`였다. `cli._check_prompts`·
  `cli._prefix_tokens`·`bench_report._role_report` 셋이 그 위를 지나가는데,
  `if role in config.profile.roles` 가드가 막고 있던 것이 **정확히 single-pass 프로필을
  붙이는 경우**라 등록의 목적이 달성되는 순간 셋이 함께 죽는 상태였다.
  `tests/test_config.py`가 `PROMPTED_ROLES ⊆ Role`과 `ROLE_SPEC` 커버리지를 강제한다.
