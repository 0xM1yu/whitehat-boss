# D63 (a) — `qwen3-reranker-4b`를 우리 입력 모양에서 잰다 — ATOM

> **결론 셋(전부 결정론적, 3회 동일).**
> ① **오늘의 실경로 질의(식별자 하나)에서 4b가 무너진다** — `parse_header` 한 단어를 질의로 주면
> 취약 3건이 무관 4건보다 전부 위로 오지 않는다(**raw 0/3**). 진짜 취약 `parse_header`(V1)가 8건 중
> **7위**, 무관 `list_insert`(I2)가 **1위**다.
> ② **자연어 질의는 선다** — 같은 문서 8건에 서술형 질의를 주면 **3/3**. 그래서 지배 변수는
> **모델 크기가 아니라 질의 모양**이다([09-08 검색 조사](../../research/2026-09-08-retrieval.md)의
> *"식별자 대 자연어 4.2배"*를 우리 문서에서 확증).
> ③ **instruction 틀이 식별자 질의를 살린다** — 질의·문서에 Qwen3-Reranker 틀을 붙이면 Q1이
> **0/3 → 3/3**이 되고 자연어는 3/3 유지. 붙였을 때 **나빠지지 않으므로** 서버가 그 틀을 이미
> 붙이고 있던 게 아니다(이중 적용 아님) — [D63](../../open-questions/D/D63.md) 선택지 (b)가 실제
> 레버라는 첫 신호.
>
> ⚠ **이 실험이 답하지 않는 것.** 문서가 **8건이지 32건이 아니고**, 사람이 고른 **온전한 함수**라
> 실제(40줄 경계에서 기계적으로 잘린 조각)보다 리랭커에 유리하다 — **PASS여도 "실경로에서
> 맞는다"가 아니라 "실경로보다 쉬운 입력에서 맞았다"까지다.** 틀 포맷은 **추정**이라(vLLM 소스로
> 확인 안 함 — D63 미확인②) ③의 방향은 잡되 확정은 아니다. 리랭커-끈 기준선(09-08 §2 배제셋 #3)과
> HF Transformers 참조 대조(배제셋 #2 · vLLM #20532·#21681)는 **여전히 열림**.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험일 | 2026-09-13 (ATOM 로컬 기동 로그 `02:04:58` UTC 부근) |
| 실제 서빙 모델 | HF 캐시 `Qwen/Qwen3-Reranker-4B`(config `source` 리비전 `@22e6836`) — `--served-model-name qwen3-reranker-4b`는 별칭 |
| 기동 인자 | `vllm serve Qwen/Qwen3-Reranker-4B --port 8005 --hf-overrides {"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true} --enforce-eager --gpu-memory-utilization 0.20 --max-model-len 40960` · env `VLLM_ATTENTION_BACKEND=TRITON_ATTN` · `VLLM_USE_DEEP_GEMM=0` · `VLLM_USE_FLASHINFER_SAMPLER=0` · `HF_HUB_OFFLINE=1` (0.6b가 쓴 것과 같은 리랭커 서빙 오버라이드) |
| 엔드포인트 | `POST http://127.0.0.1:8005/v1/rerank` → 200 |
| `checksum` 재검증 | **serve 시점에 안 했다** — 이 run은 순위 품질이지 처리량 벤치가 아니라 `benchmark`를 안 채운다(전부 `0` 유지). config `checksum`은 매니페스트 전체 sha256으로 등재 시 대조됨(#179) |
| `run_id` | **없다.** `cli run`을 안 돌렸다 — 어댑터 밖 직접 `/v1/rerank` 호출이라 오케스트레이터를 안 지난다. **조건 비교표 밖**(evaluation.md §6) |
| 반복 | 질의×모드마다 **3회**. 전 조합 3회 동일(결정론) |
| 동시에 떠 있던 다른 서버 | **없다.** 800x 0개에서 8005 하나만 띄우고 측정 후 PGID로 내렸다 |
| `--gpu-memory-utilization` | `0.20` — 분모는 물리 통합메모리 119 GiB(기동 시점 free 114 GiB) |
| 원문 산출물 | `/srv/tenants/ext01/d63_reranker4b_probe_20260913.txt`(콘솔 출력) · 프로브 `/srv/tenants/ext01/d63_reranker4b_probe.py`. **ATOM 홈에만 있고 저장소엔 없다** |
| vLLM · 환경 | `0.27.1` · arch `sm121` · venv `/srv/tenants/ext01/venv-probe` (Python 3.12) |

## 1. 요약

| 질의 (모양) | raw | templated |
| --- | --- | --- |
| **Q1 `parse_header`** (오늘 실경로 — 식별자 하나) | **0/3** | 3/3 |
| Q2 `parse_header memcpy len` (keywords가 배선되면) | 0/3 | 0/3 |
| Q3 `buffer overflow vulnerability in C string handling` (자연어 대조군) | **3/3** | 3/3 |

판정 = **취약 3건(V)이 무관 4건(I)보다 전부 위인가.** 안전한 문자열처리 1건(S1)은 방해물이라
판정에서 뺀다. 전 조합 결정론(3회 동일).

## 2. 입력

**문서 8건**(각 온전한 C 함수):
- 취약 3 — `V1` 헤더 파싱 `memcpy`(와이어 길이, 경계 없음) · `V2` `strcpy`(네트워크 입력) · `V3` `sprintf`(무한 사용자 입력).
- 안전 1 — `S1` `parse_header_safe`(같은 로직에 경계 검사). **V1의 방해물**: 이름·주제가 취약과 같고 경계만 다르다.
- 무관 4 — `I1` gcd · `I2` 연결리스트 삽입 · `I3` 벡터 내적 · `I4` 퀵소트 분할.

**질의 3종** — 실경로가 오늘 보내는 모양부터 대조군까지: `parse_header`(실경로 — `_query_text()`가
`target.symbol` 하나를 보내고 keywords 호출부가 저장소에 없다, D63 기제) → `parse_header memcpy len`
(keywords가 배선되면 나올 모양) → 자연어 문장(관측 2와 같은 질의, 문서만 실모양).

**모드 2종** — `raw`는 `vllm_retrieval.py:83~95`가 오늘 보내는 그대로. `templated`는 우리가
`<Instruct>/<Query>/<Document>` 틀을 붙여 보낸다(배제셋 #1 / 미확인② 방향 신호).

## 3. 결과 — 순위 (점수 내림차순, 대표 rep)

| 질의·모드 | 순위 | 판정 |
| --- | --- | --- |
| Q1 raw | `I2 > V3 > V2 > I1 > I4 > S1 > V1 > I3` | **0/3** — I2가 1위, V1이 7위 |
| Q1 templated | `V2 > V1 > S1 > V3 > I1 > I4 > I2 > I3` | 3/3 |
| Q2 raw | `S1 > V2 > I2 > V1 > V3 > I3 > I1 > I4` | 0/3 — S1(안전) 1위, I2가 V1보다 위 |
| Q2 templated | `V1 > S1 > V2 > I2 > V3 > I1 > I3 > I4` | 0/3 — I2가 V3보다 위 |
| Q3 raw | `S1 > V2 > V1 > V3 > I4 > I2 > I3 > I1` | 3/3 — 취약 셋이 무관 넷 위(S1이 최상위지만 판정 밖) |
| Q3 templated | `V2 > V1 > V3 > S1 > I2 > I3 > I1 > I4` | 3/3 |

## 4. 해석

**① 질의 모양이 지배한다.** 같은 문서 8건인데 Q1(식별자)에서 0/3, Q3(자연어)에서 3/3이다.
바뀐 것은 질의뿐이다. `parse_header` 한 단어로는 리랭커가 취약 코드를 무관 코드와 못 가른다 —
V1(진짜 `parse_header` 취약)이 7위이고 무관 `list_insert`가 1위다. 이것이 오늘 실경로에 실린
모양이다(D63 기제: `_query_text()`가 `target.symbol` 하나를 보내고 keywords를 넘기는 호출부가
저장소에 없다). **09-08 조사의 「식별자 대 자연어 4.2배」가 우리 문서에서 재현됐다.**

**② keywords만으로는 안 는다.** Q2(`parse_header memcpy len`)도 raw·templated 둘 다 0/3이다.
세 단어를 붙여도 S1(안전)이 1위로 오거나 I2가 취약 위로 온다. 즉 D63이 검토한 「keywords 배선」은
이 표본에서 (a) 판정을 못 넘긴다 — **필요한 것은 몇 단어 더가 아니라 서술형 질의로 보인다.**

**③ instruction 틀이 식별자 질의를 살린다.** Q1이 raw 0/3에서 templated 3/3으로 뒤집힌다.
자연어(Q3)는 틀 유무와 무관하게 3/3이다. **틀을 붙였을 때 나빠지는 조합이 없으므로**(Q1 상승·Q2
정체·Q3 유지) 서버가 `is_original_qwen3_reranker=true`로 그 틀을 **이미 붙이고 있던 게 아니다** —
붙였다면 이중 적용으로 나빠졌을 것이다. **D63 (b)(client가 틀을 붙인다)가 실제 레버라는 첫 신호.**
⚠ 단 내 틀 포맷은 Qwen3-Reranker 문서에서 **추정**한 것이라, 서버가 무엇을 붙이는지의 확정은
vLLM 소스라야 한다(미확인②) — (b) 착수 전 선행.

**④ 점수는 결정론적이다.** 전 조합 3회 동일 — 관측 1(0.6b)의 뒤집힘처럼 재시도로 안 사라진다.

## 5. 안 닫는 것 · 미측정

- **문서 8건 · 온전한 함수.** 실경로는 32건까지이고 40줄 경계에서 잘린 조각이다 — 둘 다 이보다
  어렵다. PASS는 "이 쉬운 입력에서 맞았다"까지다.
- **틀 포맷 추정**(미확인②). 서버가 붙이는 것의 확정은 vLLM 소스.
- **리랭커-끈 기준선 없음**(09-08 §2 배제셋 #3). 리랭킹이 기준선을 **얼마나** 올리는지는 이
  실험 밖 — 부호도 이 표본으로는 못 잰다(embedding 순서를 안 뽑았다).
- **HF Transformers 참조 대조 없음**(배제셋 #2 · vLLM #20532·#21681). "vLLM 점수 버그"와 "모델
  성질"을 이 실험은 못 가른다.
- **판정 정확도(bench)가 아니다.** 센 것은 순위뿐이다.

## 6. 배선 함의

**`config/profiles.yaml`의 리랭커를 0.6b→4b로 지금 그대로 배선하면 안 된다.** 오늘 실경로가 보내는
식별자 질의에서 4b가 0/3이고, 순위는 표시가 아니라 **포함**을 정하므로(D63: `_fit_related`가 순위
접두부를 담고 예산 초과에서 `break`) 틀린 코드가 프롬프트에 실린다. 배선 전 레버 둘:

1. **질의 모양** — `_query_text()`가 식별자 하나 대신 서술형 질의를 만들게 한다. keywords 배선만으로는
   부족하다(§4②). 이쪽이 근본이다(자연어는 틀 없이도 3/3).
2. **client instruction 틀**(D63 (b)) — Q1을 살렸다. 착수 전 미확인②(서버가 무엇을 붙이나)를
   vLLM 소스로 확인.

**상태: [D63](../../open-questions/D/D63.md)은 `열림` 그대로다.** (a)의 첫 실측이 나왔지 D63이
닫힌 게 아니다 — 32건·잘린 조각·기준선·HF 대조가 남았다.
