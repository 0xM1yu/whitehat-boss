# Embedder — Document(조각) 인코딩 포맷

Qwen3-Embedding은 비대칭 구조다: **query 쪽에만** instruction을 붙이고, **document(코드 조각) 쪽은
instruction 없이 원문만** 인코딩한다. 그래서 이 파일은 `v1/`, `v2/` ... 버전이 올라가도 바뀌지
않는다 — 프롬프트 튜닝의 대상은 `source/`, `sink/` 아래의 query 쪽 prefix.md/body.md뿐이다.

## 입력 형식

`data/chunks/chunks_manifest.jsonl`의 각 행이 그대로 document 입력이다. 별도 가공 없이 `text`
필드를 그대로 인코딩기에 넣는다.

```json
{
  "chunk_id": "sample_01/A0006_handle_TYPE_ftpcmd_354.c::chunk0",
  "text": "/* ===== src/ftpcmd.c ===== */\n    37| static int is_cont(char *msg)\n..."
}
```

`text`는 `/* ===== <path> ===== */` 헤더 + 원본 줄번호 gutter를 그대로 보존한다 (scout/README.md의
"경로 표시가 없으면 cross-file을 안 읽는다" 관찰을 검색 단계에도 그대로 적용 — `src/chunk_slices.py`가
청크 경계가 헤더를 벗어나면 활성 파일 헤더를 합성해서 앞에 붙여 둔다).

## 사전계산

조각 벡터는 저장소(샘플)당 1회만 계산해 캐시한다 — query만 매번 새로 인코딩한다 (하네스 문서
"② 리랭커: 미리 계산이 안 된다" 대비, "① 임베딩: 조각 벡터는 미리 계산해 둘 수 있다" 항목).
