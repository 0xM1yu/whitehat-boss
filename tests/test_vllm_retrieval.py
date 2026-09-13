"""`VllmRerankerClient`가 Qwen3-Reranker 양식을 붙여 보내는지 — GPU·네트워크 없이 payload만 본다.

**왜 이 테스트가 있나.** vLLM `/rerank`(pooling)는 Qwen3-Reranker에 instruction 템플릿을
안 붙인다(2026-09-13 vLLM 0.27.1 소스 확인). 그래서 client가 붙이는데, 안 붙이면 모델은
훈련 때 기대한 양식이 통째로 빠진 맨 텍스트를 받아 실경로 질의(식별자 하나)에서 순위가
무너진다(D63 (a)). 이 검사는 그 래핑이 payload에 실제로 실리는지를 못박는다.
"""

from src.adapters import vllm_retrieval
from src.adapters.vllm_retrieval import VllmRerankerClient


def test_reranker_wraps_query_and_documents_in_qwen3_template(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    def fake_post(url, payload, api_key):  # noqa: ANN001, ANN202, ARG001
        captured["payload"] = payload
        return {
            "results": [
                {"index": i, "relevance_score": 1.0 - i * 0.1}
                for i in range(len(payload["documents"]))
            ]
        }

    monkeypatch.setattr(vllm_retrieval, "_post", fake_post)

    client = VllmRerankerClient(endpoint="http://x/v1", model_name="qwen3-reranker-4b")
    result = client.rerank(query="parse_header", documents=["codeA", "codeB"], top_n=2)

    payload = captured["payload"]
    assert payload["query"].startswith("<Instruct>: ")
    assert "<Query>: parse_header" in payload["query"]
    assert payload["documents"] == ["<Document>: codeA", "<Document>: codeB"]
    # 래핑이 순서를 안 바꾸므로 index는 원래 문서 순서로 되돌아온다.
    assert [r.index for r in result.ranked] == [0, 1]


def test_reranker_instruction_is_overridable(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    def fake_post(url, payload, api_key):  # noqa: ANN001, ANN202, ARG001
        captured["payload"] = payload
        return {"results": []}

    monkeypatch.setattr(vllm_retrieval, "_post", fake_post)

    client = VllmRerankerClient(
        endpoint="http://x/v1", model_name="m", instruction="CUSTOM TASK"
    )
    client.rerank(query="q", documents=["d"], top_n=1)

    assert "<Instruct>: CUSTOM TASK" in captured["payload"]["query"]
