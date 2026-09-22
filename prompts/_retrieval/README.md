# `_retrieval/` — 검색(embedder·reranker) 실험 프롬프트

이 트리는 **하네스가 로드하지 않는다.** 실험용 프롬프트를 저장소에 보존만 한다.
디렉터리 앞의 `_`가 `PROMPTED_ROLES`·`Role`·`prompt_loader`와 계약 검사(`test_prompted_roles_matches_prompt_directories`)에서 이 트리를 전부 뺀다. 그래서 여기 내용은 **어떤 실행에도 배선되지 않는다.**
embedding·reranker가 구조화 출력 역할이 아니라는 판단은 [ADR 0004](../../docs/decisions/0004-retrieval-clients-separate-from-model-client.md)다.

## 실제로 도는 것

검색 단계가 실행 시 쓰는 것은 이 프롬프트가 아니라 파이썬 상수 둘이고, 내용도 여기 프롬프트와 다르다.

- reranker instruction -> `src/adapters/vllm_retrieval.py` `_RERANK_INSTRUCTION`
- embedding query 텍스트 -> `src/adapters/hybrid_retriever.py` `_query_text` (symbol + keywords, 둘 다 비면 파일 경로)

`prompts/_retrieval/`를 이 상수와 연결할지는 미결이다(#233의 후속). **연결 전까지 여기 파일은 서술이지 지시가 아니다.**

## 이 저장소에 없는 경로

`embedder/DOCUMENT_FORMAT.md`와 일부 `reranker/*/meta.yaml`이 청킹 파이프라인 기준 경로를 현재형으로 든다. 그 경로들은 **이 저장소 트리에 실재하지 않는다** — 청킹→임베딩→리랭킹 실험 파이프라인의 상정 경로이고, 하네스 코드가 아니다.

- `data/chunks/chunks_manifest.jsonl` — 없음
- `src/chunk_slices.py` — 없음
- `scout/README.md` — 없음

읽는 사람은 이 문서들의 경로·현재형 서술을 **하네스 실행의 사실이 아니라 실험 상정**으로 본다.
