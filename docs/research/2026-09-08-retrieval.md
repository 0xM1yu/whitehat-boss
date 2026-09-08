# 리랭커·임베더 조사 — 식별자 질의 × 40줄 C/C++ 조각

**조사일 2026-09-08.** 여기 있는 수치는 전부 **남의 측정**이다. 라벨은 넷이다 —
`[출처주장]`(벤더·저자 자기 측정) · `[제3자]`(측정자 명시) · `[계산]`(우리 산술) ·
`[미확인]`. 우리 장비 실측은 [experiments/](../experiments/README.md)에 있고 이 문서에 없다.

**조사 도구 제약.** 이 저장소의 deep-research 규약은 firecrawl·exa MCP를 전제하는데
이 세션에 둘 다 없어 **WebSearch·WebFetch로 대신했다.** 사이트 전문 크롤이 안 되므로
PDF 본문에서 못 꺼낸 표가 몇 있다 — §8에 무엇을 못 읽었는지 적었다.

## 0. 팀이 물은 것과 문헌이 답한 것

팀은 *"리랭커가 파라미터가 너무 작아서 제대로 작동 안 하고 있다"*고 보고 키우려 한다.
문헌은 그 직관을 **부분적으로만** 지지한다. 갈리는 지점이 이 문서의 전부다.

| 주장 | 문헌의 답 |
| --- | --- |
| 0.6B는 코드에서 약하다 | **지지한다.** 벤더 자기 표에서 0.6B 리랭커는 코드에서 **1차 검색기보다 나쁘다** |
| 그러니 키우면 된다 | **4B까지만.** 4B→8B는 MTEB-Code에서 **+0.02점**이고 MTEB-R에서는 **−0.74점**이다 |
| 크기가 지금 손실의 원인이다 | **답하지 못한다.** 문헌이 재놓은 더 큰 손실이 따로 있다 — **질의 모양**이 4.2배다 |

---

## 1. 벤더 자기 표 안에서 0.6B 리랭커는 코드에서 **순손실**이다

[Qwen3-Reranker-0.6B 카드](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)의 평가표는
첫 행이 **리랭킹 없는 1차 검색기**(Qwen3-Embedding-0.6B top-100)이고 아래가 그것을
리랭킹한 결과다. 전부 `[출처주장]`(Qwen 자체 측정, 2026-09-08 관측), 델타는 `[계산]`.

| 구성 | MTEB-R | 델타 | MTEB-Code | 델타 |
| --- | --- | --- | --- | --- |
| Qwen3-Embedding-0.6B (리랭킹 없음) | 61.82 | — | **75.41** | — |
| + Qwen3-Reranker-0.6B | 65.80 | +3.98 | **73.42** | **−1.99** |
| + Qwen3-Reranker-4B | 69.76 | +7.94 | 81.20 | +5.79 |
| + Qwen3-Reranker-8B | 69.02 | +7.20 | 81.22 | +5.81 |

**0.6B 리랭커는 텍스트에서 +3.98을 벌고 코드에서 −1.99를 잃는다.** 만든 쪽의 표에서다.

⚠ **이 표는 우리 질의 모양이 아니다.** MTEB-Code의 질의는 자연어 문장이거나 코드 조각이지
맨 식별자가 아니다. 그리고 **"0.6B라서 나쁘다"와 "코드 분포에 약해서 나쁘다"를 이 데이터가
가르지 못한다** — 같은 모델이 텍스트에서는 이득이기 때문이다.

## 2. 크기의 수익 곡선 — 4B가 변곡점이고 8B는 아니다

| 벤치마크 | 0.6B | 4B | 8B | 0.6B→4B | 4B→8B | 출처 |
| --- | --- | --- | --- | --- | --- | --- |
| MTEB-Code | 73.42 | 81.20 | 81.22 | **+7.78** | **+0.02** | `[출처주장]` Qwen 카드 |
| MTEB-R | 65.80 | 69.76 | 69.02 | +3.96 | **−0.74** | `[출처주장]` 같은 곳 |
| CoIR | 65.18 | 73.91 | 미공개 | **+8.73** | — | `[제3자: Jina]` [v3 카드](https://huggingface.co/jinaai/jina-reranker-v3) |
| BEIR | 56.28 | 61.16 | 미공개 | +4.88 | — | `[제3자: Jina]` 같은 곳 |
| Struct-IR (준구조) | 41.9 | 55.6 | 미공개 | **+13.7** | — | `[제3자: Jina]` [v3.5 공지](https://jina.ai/en-US/news/jina-reranker-v3-5-faster-listwise-reranking-hybrid-attention-self-distillation/) |

델타는 `[계산]`.

**세 가지가 읽힌다.**

1. **8B를 살 이유가 문헌에 없다.** 파라미터 2배·프리필 FLOPs 2배 `[계산]`에 MTEB-Code
   +0.02점이고 MTEB-R은 오히려 내려간다.
2. **코드가 텍스트보다 크기에 민감하다.** 0.6B→4B가 MTEB-R에서 +3.96인데 MTEB-Code에서
   +7.78이다 `[계산]`. Struct-IR의 +13.7이 같은 방향의 더 센 신호다 — 준구조 데이터일수록
   작은 리랭커가 무너진다.
3. **같은 체감이 코드 도메인 전용 모델에서도 나온다.** [SweRank](https://arxiv.org/html/2505.07849v2)(Salesforce)의
   함수 단위 Acc@10 `[출처주장]`: 검색만 82.12 → 7B 리랭커 86.13 → **32B 리랭커 88.69**.
   **7B→32B(4.6배 비용)가 사는 것은 +2.56pt다** `[계산]`. ⚠ 이 실험의 질의는 GitHub 이슈
   본문이고 **평균 382.56 토큰**이다 `[출처주장]` — 우리 모양이 아니다.

## 3. 그런데 지배적 손실은 크기가 아니다 — **질의 모양**이다

### 3.1 같은 코퍼스·같은 모델에서 질의 모양만 바꾼 측정

[Scientific Code Search at Scale](https://arxiv.org/html/2607.05443v1)(Pantha et al., 2026-07)이
**동일 코퍼스에서 질의 유형만 바꿔** 쟀다. 언어 구성에 **C 14.3% · C++ 12.5%**가 들어 있다
`[출처주장]`.

| 질의 유형 | MRR@10 |
| --- | --- |
| 함수 docstring | **.76** |
| 클래스 docstring | .74 |
| 클래스 식별자 | .25 |
| **함수 식별자** | **.18** |

`[출처주장]`. **.76 → .18, 4.2배다** `[계산]`. 논문 표현 그대로 *"Identifier-based queries,
however, prove far more challenging"*.

**이것이 우리가 서 있는 자리에 가장 가까운 단일 측정이다.** 우리 질의는 `_query_text()`가
만드는 **식별자 한 개**다 — [`src/adapters/hybrid_retriever.py`](../../src/adapters/hybrid_retriever.py)에서
`keywords`를 넘기는 호출부가 저장소에 없다.

⚠ **검색기 측정이지 리랭커 측정이 아니다.** 식별자 질의에 리랭커를 붙인 수치는 이 논문에도 없다.

### 3.2 짧은 키워드 질의는 모델을 가리지 않고 붕괴시킨다

[CoREB](https://arxiv.org/abs/2605.04615)(Xue et al., 2026-05)의 text-to-code를 **같은 정답,
다른 질의 형식**으로 3분할한 결과 nDCG@10 `[출처주장]`:

| 검색 모델 | Canonical (약 120 tok) | Full (약 431 tok) | Search (약 19 tok 키워드) |
| --- | --- | --- | --- |
| C2LLM-7B | 0.582 | 0.578 | **0.004** |
| Qwen3-Embedding-4B | 0.510 | 0.504 | **0.015** |
| Qwen3-Embedding-0.6B | 0.452 | 0.460 | **0.008** |
| Qwen3-Embedding-8B | 0.477 | 0.381 | **0.005** |

**임베더를 0.6B에서 8B로 13배 키워도 짧은 질의에서는 0.008 → 0.005다.** 크기가 이 축을
움직이지 못한다.

⚠ **저자들의 처방은 리랭킹이 아니다** — *"Query expansion techniques such as HyDE or
Query2doc are the most promising path forward"*. 그리고 **그들은 그것을 돌려보지 않았다.**
"유망하다"이지 "해결됐다"가 아니다.

**그리고 우리는 19 토큰도 아니다.** 이 표의 가장 왼쪽 끝보다 더 바깥이다.

### 3.3 질의 확장은 믿을 만한가 — 양방향이다

| 출처 | 방향 | 내용 |
| --- | --- | --- |
| [CoREB](https://arxiv.org/abs/2605.04615) | 찬성(제안만) | HyDE·Query2doc이 *"most promising path forward"* — **실험 없음** `[출처주장]` |
| [GAR](https://arxiv.org/abs/2212.10692) | 찬성(측정) | 생성 기반 질의 확장이 CodeSearchNet SOTA `[출처주장]`. 단 **질의가 docstring**인 설정이고 2022년 작업이다 |
| [CORE-Bench](https://arxiv.org/html/2606.11864) | 반대(측정) | *"rewritten queries often fail to improve retrieval and can even reduce performance"* `[출처주장]` |

**식별자 질의를 확장이 구제한다는 직접 측정은 찾지 못했다.** 비용도 있다 — 질의당 LLM 1회이고
우리는 질의 수가 심볼 수만큼이다.

## 4. C/C++ 직접 증거 — 검색 신호의 대부분이 식별자 문자열이다

[CLARC](https://arxiv.org/html/2603.04484v1)(2026-03)은 **C/C++ 전용** 코드 검색 벤치마크다.
식별자를 익명화·무작위화했을 때 MRR `[출처주장]`:

| 모델 | 표준 | Neutralized | Randomized |
| --- | --- | --- | --- |
| voyage-code-3 | 86.93 | 84.22 (−2.7%) | 80.68 (−7.1%) |
| nomic-embed-code | 91.61 | 67.65 (−26.1%) | **48.23 (−47.4%)** |
| OASIS (Long) | 63.53 | 36.08 (−43.2%) | **30.51 (−52.0%)** |

논문 표현: *"current code search models still rely on superficial lexical features rather
than code semantics"*.

**우리에게 양날이다.** 식별자가 신호를 지고 있다면 — ① 정답 조각이 그 식별자를 **글자 그대로**
품을 때는 강한 신호이고, 그건 BM25·grep이 제일 잘하는 일이다. ② 정답이 **호출자 쪽**이라
그 문자열이 없으면 신호가 **없다.** 임베더·리랭커의 "의미 이해"가 ②를 메워줄 것이라는 기대를
이 논문이 정면으로 반증한다.

## 5. 리랭커가 애초에 맞는 도구인가

### 5.1 강한 검색기 위에서 리랭킹은 **음수가 될 수 있다**

[CoREB](https://arxiv.org/html/2605.04615) Figure 8 — 최강 개방 검색기 C2LLM-7B의 top-128을
리랭킹했을 때 Δ nDCG@10(%) `[출처주장]`:

| 리랭커 | text→code | code→text | code→code |
| --- | --- | --- | --- |
| Jina Reranker v3 | −5.1 | −18.2 | +1.8 |
| Qwen3-Reranker-0.6B | −2.8 | −3.2 | +2.1 |
| **Qwen3-Reranker-4B** | **−0.1** | **−3.2** | **+3.3** |
| CoREB-Reranker (4B 도메인 파인튜닝) | +2.4 | +1.9 | +4.7 |

캡션: *"No baseline is net-positive across all three tasks; only our fine-tuned
CoREB-Reranker achieves this."* 저자 설명은 *"reranking amplifies noise"*다.

**이것이 이 조사에서 제일 불편한 결과다.** **4B로 키워도 부호가 안 바뀌었고, 바꾼 것은
크기가 아니라 도메인 파인튜닝이었다.**

⚠ **우리 구조에서 이 음수는 조용하다.** `_fit_related()`가 리랭커 순서대로 담다가 예산을
넘는 **첫 조각에서 끊는다** — 3등이 안 맞으면 4·5등도 같이 빠진다. 순위가 표시 순서가 아니라
**포함 여부**를 정하므로, 리랭커가 노이즈를 증폭하면 모델이 **틀린 코드를 본다.**

### 5.2 하이브리드가 이미 있을 때의 증분 — 범위가 −.152부터 +6.25pt까지다

| 출처 | 설정 | 리랭킹 증분 |
| --- | --- | --- |
| [Scientific Code Search](https://arxiv.org/html/2607.05443v1) | dense → Hybrid-Rerank | **+.010 MRR@10 / +.003 nDCG@10** `[출처주장]` |
| [CoREB](https://arxiv.org/html/2605.04615) | C2LLM-7B → Qwen3-Reranker-4B | text→code **−0.1%** `[출처주장]` |
| [SweRank](https://arxiv.org/html/2505.07849v2) | SweRankEmbed-7B → SweRankLLM-7B | **+4.01 ~ +6.25pt** `[출처주장]` |
| [Horváth·Pietriková](https://arxiv.org/html/2607.09161) | semantic → quality-rerank | **−.152 nDCG@5** `[출처주장]` |

**가르는 변수는 "1차 검색기가 이미 포화했는가"다.** 검색기가 강하고 코퍼스가 작으면 0이거나
음수, 약하고 도메인 특화면 크게 듣는다. **우리가 어느 쪽인지는 우리만 잴 수 있다.**

### 5.3 렉시컬·그래프가 대신할 수 있는가

- [Practical Code RAG at Scale](https://arxiv.org/html/2510.20609v1)(JetBrains, 2025-10)
  `[출처주장]`: **code→code에서 BM25+word 분할이 dense를 약 10pt 앞서고** 9배 빠르다.
  **NL→code에서는 dense가 이긴다**(0.72 대 0.57 nDCG). 권고가 태스크별로 갈린다.
  **우리 질의는 식별자다 — NL도 code도 아니고 어휘성으로는 code 쪽이다.**
- [LARGER](https://arxiv.org/html/2605.16352v1)(Emory, 2026-05) `[출처주장]`: 렉시컬 매치에
  그래프 확장을 앵커링. 그래프 확장을 빼면 55.7 → 48.2(**−13.5%**)로 단일 최대 기여.
  ⚠ **dense 임베더·신경 리랭커와 이름으로 비교한 기준선이 이 논문에 없다** — BM25와
  에이전트들만 비교했다. "리랭커보다 낫다"고 읽으면 원문보다 센 진술이 된다.
- [CodeAnchor](https://arxiv.org/html/2606.26979v1)(2026-06) `[출처주장]`: grep 기준선
  Func@5 0.8321 → 호출·상속 태그 추가 0.8540. 저자 논평 — *"explicit tags provide
  verifiable facts … reducing hallucination risk compared to relying on implicit
  similarity judgments"*. ⚠ **임베딩+리랭킹을 실제로 돌려 이긴 것이 아니라 정성 논평이다.**

**우리 파이프라인은 Joern CPG 슬라이서를 앞에 두려 한다.** 위 셋이 전부 같은 방향을 가리킨다 —
**그래프·렉시컬이 이 태스크에서 지고 있는 무게가 크다.**

### 5.4 청킹 40줄은 문헌이 지지하는 쪽이다

[How Does Chunking Affect Retrieval-Augmented Code Completion?](https://arxiv.org/html/2605.04763v1)(2026-05,
864개 설정) `[출처주장]`:

| 전략 | RepoEval API-level EM | RepoEval Line-level EM | CrossCodeEval EM |
| --- | --- | --- | --- |
| Function | 42.27% | 51.27% | 24.21% |
| cAST | 45.93% | 56.54% | 28.19% |
| **Sliding Window** | **46.23%** | **56.91%** | **28.40%** |

*"Function chunking is never Pareto-optimal."* 최적 청크는 **비공백 2,000자**다.

**우리 `CHUNK_LINES = 40`은 sliding window이고 그쪽이 이긴다.** 다만 C 코드 비공백 35자/줄로
잡으면 40줄이 약 1,400자로 **최적치보다 작다** `[계산, 가정 명시]`.

⚠ 이 연구의 태스크는 **코드 완성**이고 리랭커를 쓰지 않는다. 우리 태스크가 아니다.

## 6. 서빙 — 크기를 의심하기 전에 먼저 배제할 것

### 6.1 vLLM의 Qwen3-Reranker 점수 정확성 결함이 **열려 있다**

| 이슈 | 날짜 | 상태(2026-09-08 관측) | 내용 |
| --- | --- | --- | --- |
| [#20532](https://github.com/vllm-project/vllm/issues/20532) | 2025-07-06 | **Open** | 공식 Qwen3-Reranker가 Rerank API 400을 반환. 같은 환경에서 `bge-reranker-v2-m3`는 정상 |
| [#21681](https://github.com/vllm-project/vllm/issues/21681) | 2025-07-27 | **Open** | **vLLM과 Transformers의 점수가 다르고 순위가 뒤바뀐다.** vLLM `0.907 / 0.810 / 0.807 / 0.076` 대 Transformers `0.957 / 0.001 / 0.000 / 0.000` |
| [#35412](https://github.com/vllm-project/vllm/issues/35412) | 2026-02-26 | Closed (수정 여부 `[미확인]`) | Qwen3-VL-Reranker에서 *"ranking results are almost completely reversed"* |

`[출처주장]`(이슈 작성자 진술). **우리 0.27.1에서 재현되는지는 `[미확인]`이다.**

> **#21681의 증상 — 무관한 문서에 0.81을 주는 것 — 은 「모델이 작아서 못한다」와
> 관측적으로 구분되지 않는다.** 우리가 본 순위 뒤집힘이 어느 쪽인지 가르지 않은 채
> 모델을 키우면, 키운 뒤에도 같은 증상이 남았을 때 원인을 다시 못 가른다.

### 6.2 프롬프트 템플릿

[vLLM 예제 문서](https://docs.vllm.ai/en/v0.9.2/examples/offline_inference/qwen3_reranker.html)가
명시한다 — *"Please use the query_template and document_template to format the query and
document for better reranker results"* `[출처주장]`. 우리 어댑터는 `query`·`documents`를
**그대로** 보낸다. ⚠ 다만 우리 기동 인자에 `is_original_qwen3_reranker: true`가 들어 있어
**서버가 이미 틀을 붙이고 있을 가능성**이 있다 — 확인 전에 손대면 두 번 붙인다.

### 6.3 sm_121

vLLM에 열린 sm_121 이슈들([#31128](https://github.com/vllm-project/vllm/issues/31128) ·
[#36821](https://github.com/vllm-project/vllm/issues/36821) ·
[#43906](https://github.com/vllm-project/vllm/issues/43906))은 **전부 FP8·NVFP4·MoE 경로**다
`[출처주장]`. **fp16 dense pooling 모델에 영향이 있다는 보고는 찾지 못했다** `[미확인]` —
우리가 이미 0.6B fp16으로 띄웠다는 것이 기본 경로가 산다는 증거다.

[vLLM DGX Spark 글](https://vllm.ai/blog/2026-06-01-vllm-dgx-spark)(2026-06-01)은
`--gpu-memory-utilization 0.85`와 `max-num-seqs 4`를 권한다 `[출처주장]`. ⚠ **디코드 지배
서빙 기준이다.** 리랭킹은 프리필 지배·고배치라 그 권고를 그대로 옮기면 안 된다.

### 6.4 비용 산술 `[계산]` — 전부 추정이다

가정: C/C++ 비공백 35자/줄 · 3.2자/토큰 · 후보 100개 리랭킹.

- 40줄 조각 ≈ 1,400자 ≈ **약 440 토큰**
- 질의당 프리필 ≈ 100 × 440 ≈ **44,000 토큰**
- 프리필 FLOPs ≈ 2 × P × T → **4B는 6.7배, 8B는 13.3배**
- fp16 가중치: 0.6B 약 1.2 GB · 4B 약 8 GB · 8B 약 16 GB — **119.7 GiB에 8B도 여유롭다**

**점당 비용**: 0.6B→4B는 6.7배에 MTEB-Code +7.78점. 4B→8B는 추가 2배에 **+0.02점**.

**GB10에서의 절대 처리량은 `[미확인]`이다.**

## 7. 후보 표 — 코드 결과가 공개된 것만

| 모델 | 크기 | 라이선스 | 최대 길이 | vLLM | 코드 지표 |
| --- | --- | --- | --- | --- | --- |
| Qwen3-Reranker-4B | 4B | Apache-2.0 | 32k | 지원(`--hf-overrides`) | MTEB-Code **81.20** `[출처주장]` · CoIR 73.91 `[제3자: Jina]` |
| Qwen3-Reranker-8B | 8B | Apache-2.0 | 32k | 동일 | MTEB-Code 81.22 `[출처주장]` |
| Qwen3-Reranker-0.6B (현행) | 0.6B | Apache-2.0 | 32k | 동일 | MTEB-Code 73.42 `[출처주장]` — **기준선 75.41보다 낮다** |
| jina-reranker-v3 | 0.6B | CC-BY-NC-4.0 | 131k listwise | `[미확인]` | CoIR 70.64 `[출처주장]` |
| mxbai-rerank-large-v2 | 1.5B | Apache-2.0 | 8k | 문서에 명시 | CoIR 70.87 `[제3자: Jina]` |
| bge-reranker-v2-m3 | 0.6B | Apache-2.0 | **512** | 네이티브 | MTEB-Code 41.38 `[제3자: Qwen]` — 참패 |
| CodeRankLLM | 7B | `[미확인]` | `[미확인]` | `[미확인]` | 코드 전용 학습 `[출처주장]` |
| SweRankLLM-Small · Large | 7B · 32B | `[미확인]` | `[미확인]` | `[미확인]` | 이슈 로컬라이제이션 전용 `[출처주장]` |

⚠ **CoIR 평균·MTEB-Code·Jina 자체 25개 스위트는 같은 것을 재고 있지 않다.** 열을 가로로
읽어 순위를 매기면 안 된다.

**라이선스 주의** — jina 계열은 **비상업**이다. SFR-Embedding-Code도 CC-BY-NC-4.0이다.

## 8. 이 조사가 못 읽은 것

- **PDF 본문 추출 실패**: jina-reranker-v3 논문 표 · "Recall Before Rerank" · Qwen3
  Embedding 테크리포트의 MTEB-Code 열 · EMNLP 2025 Findings "How Good are LLM-based
  Rerankers?". **이들에서 인용한 수치는 하나도 없다.**
- **접근 실패**: ScienceDirect의 코드 검색 질의 확장 실증 연구(HTTP 403).
- **검색 예산 소진**으로 "질의 확장이 코드 검색을 해치는가"의 2026년 실증 탐색을 마치지
  못했다 — §3.3의 결론은 그만큼 불완전하다.
- **1차 출처와 충돌하는 리스티클류**가 검색 상위에 다수 떴다. 예: *"Qwen3-Reranker BEIR
  평균 약 77%"* — Jina 측정은 0.6B에서 **56.28**이다. **이 부류에서 인용한 수치는 하나도 없다.**
- **에이전트를 겨냥한 지시문은 열람한 페이지에서 발견되지 않았다.**

## 9. 우리 입력 모양에 문헌이 답하는 것과 답하지 못하는 것

### 답하는 것

1. **0.6B 리랭커는 코드에서 1차 검색기보다 나쁘다** — 만든 쪽 표에서 −1.99점.
2. **4B가 변곡점이고 8B는 살 이유가 없다** — MTEB-Code 4B→8B가 +0.02, MTEB-R은 −0.74.
3. **짧은 질의는 크기로 못 고친다** — 임베더 13배에도 0.008 → 0.005.
4. **식별자 질의는 docstring 질의보다 4.2배 나쁘다** — 같은 코퍼스·같은 모델.
5. **C/C++ 코드 검색은 식별자 문자열에 크게 의존한다** — 무작위화에 −47%·−52%.
6. **40줄 sliding window 청킹은 지지된다** — 함수 단위보다 3.57~5.64pt 높다.
7. **강한 검색기 위에서 리랭킹은 음수가 될 수 있다** — 4B로도 부호가 안 바뀌었다.
8. **vLLM의 점수 정확성 결함 둘이 열려 있다.**

### 답하지 못하는 것

1. **맨 식별자 질의에 리랭커를 붙인 측정이 문헌에 존재하지 않는다.** CoREB는 짧은 질의로
   검색기만 쟀고, Scientific Code Search도 검색기만이다. **"4B로 키우면 식별자 질의가
   좋아지는가"에 답한 논문이 없다.**
2. **공개 벤치마크 중 우리 질의 길이에 닿는 것이 없다.** CoIR의 최단이 CosQA 평균 37 토큰,
   CoREB의 Search가 19 토큰이다. **우리는 1~5 토큰**이다. 모든 수치가 우리 분포 바깥에서 나왔다.
3. **40줄 고정 창이 함수 중간을 자른 조각을 리랭커가 어떻게 점수 매기는지** 잰 연구가 없다.
4. **질의 확장이 식별자 질의를 구제하는지** 직접 측정이 없다 — 제안과 반대 측정이 공존한다.
5. **우리 하이브리드 검색기가 포화했는지**를 문헌이 모른다. 그것이 리랭킹 증분의 부호를
   정하는 변수인데 우리만 잴 수 있다.
6. **C/C++ 취약점 분석 태스크의 검색 벤치마크가 없다.** CLARC은 C/C++이지만 리랭커를
   평가하지 않았고 태스크가 취약점이 아니다.
7. **GB10 sm_121에서 pooling·rerank 워크로드의 처리량이 공개된 바 없다.**

## 10. 그래서 순서가 바뀐다

문헌이 잰 효과 크기 순으로만 적는다. **우리 코퍼스 측정이 아니다.**

| 순위 | 손잡이 | 문헌의 효과 크기 |
| --- | --- | --- |
| 1 | **질의 모양** (식별자 → 자연어) | `.18 → .76` MRR@10, **4.2배** `[출처주장]`+`[계산]` — 다만 확장의 신뢰성은 미검증 |
| 2 | **렉시컬·그래프 앵커링** | 그래프 확장 어블레이션 −13.5% · code→code에서 BM25가 dense +10pt `[출처주장]` |
| 3 | **리랭커 0.6B → 4B** | MTEB-Code +7.78 · CoIR +8.73, 6.7배 비용 `[출처주장]`+`[계산]`. **0.6B가 음수이므로 "개선"이 아니라 "손실 제거"에 가깝다** |
| 4 | **도메인 파인튜닝** | CoREB-Reranker가 4B 베이스에서 유일하게 3태스크 전부 양수 `[출처주장]` |
| 5 | 리랭커 4B → 8B | **+0.02점, 2배 비용** `[출처주장]`+`[계산]` — 하지 않는다 |

**그리고 3번을 하기 전에 배제할 것이 셋 있다.** ① vLLM 점수를 HF Transformers 참조와
대조해 #21681 계열을 배제하고 ② 프롬프트 템플릿이 이미 붙는지 확인하고
③ **리랭커를 끈 하이브리드 순위를 기준선으로 남긴다.** ③이 없으면 §5.1의 "증분이 음수"인
경우를 우리가 겪고 있는지조차 알 수 없다 — 키운 뒤에도 모른다.
