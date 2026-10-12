"""vLLM의 embedding·rerank 엔드포인트에 붙는 검색 부품 어댑터.

`openai_compat.py`와 같은 방침이다 — 의존성은 stdlib `urllib.request`뿐이고, vLLM
지식은 이 파일 밖으로 새지 않는다. 호출자는 `src/interfaces/retrieval.py`의 두
Protocol만 본다.

**Fake 구현이 같은 파일에 있는 이유**: 역할별 실서빙 혼용이 이 프로젝트의 기본
전제다(옛 `single_gpu_12g`는 scout만 실서빙이었다). embedding만 띄우고 reranker는 Fake로 두는
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

# Qwen3-Reranker instruction 양식. **vLLM `/rerank`는 이 계열에 템플릿을 안 붙인다**
# (`get_score_template`은 `jina_vl`만 구현 · `is_original_qwen3_reranker`는 점수 계산법만
# 설정 — 2026-09-13 vLLM 0.27.1 소스 확인). 모델이 훈련 때 기대하는 양식이 통째로 빠지면
# 실경로 질의(식별자 하나)의 순위가 무너진다. 그래서 여기서 붙인다.
#
# ⚠ **전제: 서빙 리랭커가 Qwen3-Reranker 계열이다.** `factory._reranker_client`는 `is_fake`만
# 보고 이 클라이언트를 고르므로 **모든 비-Fake 리랭커**가 이 양식을 받는다. 서버가 자기
# 템플릿을 붙이는 모델(예: `jina_vl`은 `get_score_template` 구현)로 바꾸면 **양식이 두 번
# 붙는다** — 그런 모델을 등재하면 여기 모델 키 가드가 필요하다. 지금 로스터의 리랭커
# (0.6b·4b)는 둘 다 Qwen3라 무해하다.
# ⚠ **전제는 vLLM 버전에도 걸린다.** 「Qwen3엔 안 붙인다」는 0.27.1 소스에서 확인한 것이다
# (저장소는 vLLM 버전을 고정하지 않는다 — `pyproject.toml` 런타임 의존성에 없다). 서버를
# 올려 그쪽이 Qwen3에 템플릿을 붙이기 시작하면 **모델을 안 바꿔도 양식이 두 번 붙고 순위가
# 조용히 망가진다** — 예외도 테스트 실패도 안 난다(payload 검사는 client가 붙였는지만 본다).
# 버전을 올릴 때 이 전제를 재확인한다(확정 수단은 위 두 소스 파일).
# ⚠ **`instruction`은 아직 config로 못 바꾼다** — `_reranker_client`가 안 넘겨 기본값만 산다
# (생성자 파라미터라 테스트·직접 호출로만 오버라이드). config 배선은 후속.
#
# 공식 전체 양식은 system prefix + `<Instruct>/<Query>/<Document>` + `<think>` suffix로
# **생성 채점**이지만(모델 README), `/rerank`(pooling)로는 prefix·suffix를 못 싣는다.
# `<Instruct>/<Query>/<Document>` **부분 양식**만 실을 수 있는데, 2026-09-13 후속 실측에서
# 그 부분 양식이 정식 생성 채점과 **거의 같았다**(맨질의 FAIL · 부분양식 PASS ≈ 생성 PASS).
# ⚠ 표본이 작고(8건·질의 2) 실조각·32건 검증은 배선 후에 남았다(이슈 #152, 닫힘 — canonical 경로
# 밖).
#
# ⚠ **이 문자열은 그 실측이 쓴 것과 글자까지 같다** — 보고서 §2가 같은 문자열을 싣는다
# (2026-09-13-d63-reranker-template-atom.md). 배포하는 instruction과 잰 instruction이 달라지면
# "부분 양식이 정식과 ≈"의 근거가 이 상수에 안 붙는다. 바꾸려면 다시 재고 그 보고서를 함께 고친다.
_RERANK_INSTRUCTION = (
    "Given a code-security query, retrieve C code snippets that contain "
    "the vulnerability the query describes."
)

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
        elapsed_ms = int((time.monotonic() - started) * 1000)
        rows = sorted(data["data"], key=lambda row: int(row.get("index", 0)))
        return EmbeddingResult(
            vectors=[[float(x) for x in row["embedding"]] for row in rows],
            usage=_usage_from(data.get("usage"), texts),
            gpu_ms=elapsed_ms,
            gpu_ms_source="request_wall_clock",
        )


class VllmRerankerClient:
    """`RerankerClient` 구현. vLLM의 `/rerank`를 쓴다.

    Qwen3-Reranker는 원본이 causal LM이라 그냥 띄우면 이 경로가 열리지 않는다.
    `--hf_overrides`로 `Qwen3ForSequenceClassification` + `classifier_from_token`을
    주어야 score API에 물린다(2026-08-11 실측). 그 설정 없이 띄운
    서버는 404를 돌려주고, 여기서는 `RetrievalGatewayError`로 드러난다.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        api_key: str = "EMPTY",
        instruction: str = _RERANK_INSTRUCTION,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.instruction = instruction

    def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> RerankResult:
        if not documents or top_n <= 0:
            return RerankResult(usage=_no_tokens())
        # Qwen3-Reranker 부분 양식을 붙여 보낸다(위 `_RERANK_INSTRUCTION` 주석). 서버가
        # 안 붙이므로 여기서 안 붙이면 모델은 맨 텍스트를 받는다. usage 추정도 실제 보낸
        # (양식 포함) 문자열로 센다 — 안 그러면 검색 토큰이 과소 계상된다.
        wrapped_query = f"<Instruct>: {self.instruction}\n<Query>: {query}"
        wrapped_docs = [f"<Document>: {doc}" for doc in documents]
        started = time.monotonic()
        data = _post(
            f"{self.endpoint}/rerank",
            {
                "model": self.model_name,
                "query": wrapped_query,
                "documents": wrapped_docs,
                "top_n": min(top_n, len(documents)),
            },
            self.api_key,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return RerankResult(
            ranked=[
                Ranked(index=int(row["index"]), score=float(row["relevance_score"]))
                for row in data.get("results", [])
            ],
            usage=_usage_from(data.get("usage"), [wrapped_query, *wrapped_docs]),
            gpu_ms=elapsed_ms,
            gpu_ms_source="request_wall_clock",
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

    **GPU 시간은 반대로 간다(`gpu_ms=None`).** 모순이 아니라 재현성 때문이다 — 토큰은
    입력의 **결정론적 함수**(`len // 4`)라 Fake에서도 같은 입력이면 같은 값이 나오지만,
    wall-clock은 아니다. Fake 경로에서 잰 시간은 하네스가 자기를 잰 값이라 장부에 넣을
    대상도 아니다(`RunContext.call_tool`의 같은 판단).
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
        # ⚠ **범위: `openai_compat`의 끊긴 연결 방어(`IncompleteRead`·생 `OSError`·비-UTF-8)는
        # 이 어댑터에 없다.** 그런 예외와 응답 인덱싱 오류는 `RetrievalGatewayError`가 아니라
        # 생으로 샌다. 러너가 이 예외를 이름으로 잡는 곳이 없어 어차피 `unforeseen_failure`로
        # 적히고(매니페스트는 최후 방어선이 남긴다) 사유가 같으니, 고쳐도 기록은 안 달라진다.
        # canonical `harness_pool`은 `retriever: fake`라 이 어댑터를 안 쓴다 — 실서버 검색은
        # `harness_pool_hybrid`만 부른다.
        raise RetrievalGatewayError(f"{url} 호출 실패: {exc}") from exc
