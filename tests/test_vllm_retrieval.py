"""`VllmRerankerClient`가 Qwen3-Reranker 양식을 붙여 보내는지 — GPU·네트워크 없이 payload만 본다.

**왜 이 테스트가 있나.** vLLM `/rerank`(pooling)는 Qwen3-Reranker에 instruction 템플릿을
안 붙인다(2026-09-13 vLLM 0.27.1 소스 확인). 그래서 client가 붙이는데, 안 붙이면 모델은
훈련 때 기대한 양식이 통째로 빠진 맨 텍스트를 받아 실경로 질의(식별자 하나)에서 순위가
무너진다(D63 (a)). 이 검사는 그 래핑이 payload에 실제로 실리는지를 못박는다.
"""

from pathlib import Path

from src.adapters import vllm_retrieval
from src.adapters.vllm_retrieval import _RERANK_INSTRUCTION, VllmRerankerClient

# 배포하는 instruction의 실측 근거가 실린 보고서. 상수 주석이 "글자까지 같다"고 주장하는 그 문서다.
_TEMPLATE_REPORT = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "experiments"
    / "harness"
    / "2026-09-13-d63-reranker-template-atom.md"
)


def test_the_deployed_instruction_matches_the_measured_one_in_the_report() -> None:
    """`_RERANK_INSTRUCTION`이 그 근거 보고서 §2의 문자열과 **글자까지 같다.**

    상수 주석이 *"이 문자열은 그 실측이 쓴 것과 글자까지 같다"*고 적는데, 그 동일성을
    지키는 것이 없으면(테스트가 `_RERANK_INSTRUCTION`을 안 봤다) instruction 본문을
    바꿔도 초록이라 **근거가 상수에 붙어 있다는 주장이 조용히 거짓이 된다**(#189 리뷰 M2·L2).
    선례는 `test_the_contract_example_output_max_matches_the_config` — 문서의 선언값과
    코드 값을 묶는다. 여기서는 배포 문자열과 잰 문자열을 묶는다.

    보고서를 바꾸려면 다시 재고(§2), 코드 상수도 같이 고쳐야 이 검사가 통과한다.
    """
    report = _TEMPLATE_REPORT.read_text(encoding="utf-8")
    assert _RERANK_INSTRUCTION in report, (
        "배포하는 `_RERANK_INSTRUCTION`이 근거 보고서 §2의 instruction 문자열과 다르다. "
        "둘 중 하나가 바뀌었으면 다시 재고 둘을 맞춘다 (§0 · §2.3).\n"
        f"  상수: {_RERANK_INSTRUCTION!r}\n"
        f"  보고서: {_TEMPLATE_REPORT.relative_to(_TEMPLATE_REPORT.parents[3]).as_posix()}"
    )


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
