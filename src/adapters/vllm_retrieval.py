"""vLLM의 embedding·rerank 엔드포인트에 붙는 검색 부품 어댑터.

`openai_compat.py`와 같은 방침이다 — 의존성은 stdlib `urllib.request`뿐이고, vLLM
지식은 이 파일 밖으로 새지 않는다. 호출자는 `src/interfaces/retrieval.py`의 두
Protocol만 본다.

**Fake 구현이 같은 파일에 있는 이유**: 역할별 실서빙 혼용이 이 프로젝트의 기본
전제다(`single_gpu_12g`는 scout만 실서빙). embedding만 띄우고 reranker는 Fake로 두는
구성이 실제로 필요하고, 그 판정은 `ModelEntry.endpoint` 스킴이 역할별로 한다.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence

from src.ids import sha256_text
from src.interfaces.model_client import Usage
from src.interfaces.retrieval import EmbeddingResult, Ranked, RerankResult

_TIMEOUT_S = 300

# Fake 임베딩 차원. 실제 모델(0.6B는 1024)과 맞출 이유가 없다 — 코사인 유사도의
# 상대 순서만 결정론적이면 파이프라인 경로 검증에는 충분하다.
_FAKE_DIM = 16

# 4바이트 ≈ 1토큰. 게이트웨이가 usage를 안 주거나 Fake일 때 쓰는 근사다(어댑터 공통 상수).
_CHARS_PER_TOKEN = 4


class RetrievalGatewayError(RuntimeError):
    """embedding·rerank 게이트웨이에 도달하지 못했거나 HTTP 오류."""


class VllmEmbeddingClient:
    """`EmbeddingClient` 구현. OpenAI 호환 `/embeddings`를 쓴다.

    vLLM은 `--runner pooling`(구버전 `--task embed`)으로 띄운 모델에 대해 이 경로를
    연다. 응답의 `data`는 `index` 필드를 갖지만 순서 보장이 명시돼 있지 않으므로
    **정렬해서 입력 순서에 맞춘다** — 어긋나면 유사도가 엉뚱한 문서에 붙는데 그건
    조용히 틀리는 종류의 버그다.
    """

    def __init__(self, *, endpoint: str, model_name: str, api_key: str = "EMPTY") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(usage=_no_tokens())
        started = time.monotonic()
        data = _post(
            f"{self.endpoint}/embeddings",
            {"model": self.model_name, "input": list(texts)},
            self.api_key,
        )
        elapsed = int(time.monotonic() - started)
        rows = sorted(data["data"], key=lambda row: int(row.get("index", 0)))
        return EmbeddingResult(
            vectors=[[float(x) for x in row["embedding"]] for row in rows],
            usage=_usage_from(data.get("usage"), texts),
            gpu_seconds=elapsed,
        )


class VllmRerankerClient:
    """`RerankerClient` 구현. vLLM의 `/rerank`를 쓴다.

    Qwen3-Reranker는 원본이 causal LM이라 그냥 띄우면 이 경로가 열리지 않는다.
    `--hf_overrides`로 `Qwen3ForSequenceClassification` + `classifier_from_token`을
    주어야 score API에 물린다(.claude/slurm/README.md §7.2). 그 설정 없이 띄운
    서버는 404를 돌려주고, 여기서는 `RetrievalGatewayError`로 드러난다.
    """

    def __init__(self, *, endpoint: str, model_name: str, api_key: str = "EMPTY") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key

    def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> RerankResult:
        if not documents or top_n <= 0:
            return RerankResult(usage=_no_tokens())
        started = time.monotonic()
        data = _post(
            f"{self.endpoint}/rerank",
            {
                "model": self.model_name,
                "query": query,
                "documents": list(documents),
                "top_n": min(top_n, len(documents)),
            },
            self.api_key,
        )
        return RerankResult(
            ranked=[
                Ranked(index=int(row["index"]), score=float(row["relevance_score"]))
                for row in data.get("results", [])
            ],
            usage=_usage_from(data.get("usage"), [query, *documents]),
            gpu_seconds=int(time.monotonic() - started),
        )


class FakeEmbeddingClient:
    """결정론적 Fake. 해시로 벡터를 만든다.

    `FakeModelClient`와 달리 여기서는 해시를 써도 된다. 그쪽은 해시 난수가 "제거율"을
    입력과 무관한 값으로 만들어 측정을 무의미하게 했지만, 임베딩의 산출물은 판정이
    아니라 **후보 정렬 순서**뿐이다. 순서가 실행마다 같기만 하면 재현성이 성립한다.
    """

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[_hash_vector(text) for text in texts],
            usage=_estimated_usage(texts),
        )


class FakeRerankerClient:
    """결정론적 Fake. 입력 순서를 그대로 상위 `top_n`으로 돌려준다.

    점수를 해시로 흩뜨리지 않는다. 앞단 embedding이 이미 유사도로 정렬해 넘기므로,
    Fake 리랭커의 역할은 "재선별 경로가 실제로 타는가"를 확인하는 것뿐이다.
    """

    def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> RerankResult:
        count = max(0, min(top_n, len(documents)))
        return RerankResult(
            ranked=[Ranked(index=i, score=1.0 - i / max(len(documents), 1)) for i in range(count)],
            usage=_estimated_usage([query, *documents]),
        )


def _no_tokens() -> Usage:
    return Usage(input_tokens=0, output_tokens=0, cached_tokens=0)


def _estimated_usage(texts: Sequence[str]) -> Usage:
    """입력 길이에서 토큰을 근사한다.

    Fake라고 0으로 두지 않는다. 0이면 "검색이 공짜"와 "계량기가 안 붙었다"가 구별되지
    않고, dev_fake에서 비용 경로가 도는지 확인할 수 없다. 출력은 벡터·점수라 생성
    토큰이 없으므로 `output_tokens`는 0이다.
    """
    return Usage(
        input_tokens=sum(len(text) for text in texts) // _CHARS_PER_TOKEN,
        output_tokens=0,
        cached_tokens=0,
    )


def _usage_from(usage: dict | None, texts: Sequence[str]) -> Usage:
    """게이트웨이가 usage를 주면 그것을, 아니면 길이 근사를 쓴다.

    vLLM의 pooling·rerank 응답은 usage를 안 실어 보내는 버전이 있다. 그때 0으로
    기록하면 실서빙 비용이 장부에서 사라진다.
    """
    if not usage:
        return _estimated_usage(texts)
    prompt = int(usage.get("prompt_tokens", 0)) or int(usage.get("total_tokens", 0))
    return Usage(input_tokens=prompt, output_tokens=0, cached_tokens=0)


def _hash_vector(text: str) -> list[float]:
    # sha256_text는 `sha256:<hex>` 형태(Sha256Digest 스키마와 같은 표기)라 접두어를 뗀다.
    hex_digest = sha256_text(text).split(":", 1)[-1]
    return [int(hex_digest[i * 2 : i * 2 + 2], 16) / 255.0 for i in range(_FAKE_DIM)]


def _post(url: str, payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(  # noqa: S310 - 스킴은 config가 정한 http(s)다
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RetrievalGatewayError(f"{url} 호출 실패: {exc}") from exc
