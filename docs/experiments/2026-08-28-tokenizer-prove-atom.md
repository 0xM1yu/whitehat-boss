# 토크나이저 정확도 Prove — ATOM

`run_20260827_0004`(30B `b1_single`, PWN-003, 실서빙 33 `model_call`) **재분석**이다. 코드 변경 0.
run 산출물(`trace.jsonl`·`prompts/`)과 서빙 모델 `tokenizer.json`에서 다시 읽은 값만 적었다.

## 1. 요약

| 축 | 결과 |
| --- | --- |
| **토크나이저 Prove (D21 (b), 로드맵 4단계)** | 서버 `input_tokens` − 우리 카운트 = **정확히 +13** (33/33). 비율 0.990(상수 보정 시 1.000). 옛 `len//4`는 0.629(≈1.6x 과소) |
| 부수 — critic 실서빙 확인 | critic 7콜이 `qwen3-coder-30b-a3b`로 real 서빙됨(`gpu_ms` 있음). 단 `b1_single`이라 scout·auditor와 **같은 30B**를 쓴다 |
| 의제 — `critic_top_n` | 8은 근거 없는 관례값. per-call 비용은 confound가 커 결정 근거 아님 — 정식 측정이 필요하다 |

## 2. 토크나이저 Prove (메인)

**목적.** D21 (b)로 넣은 실 토크나이저가 실서빙 `input_tokens`와 얼마나 맞는지 증명한다. 그동안
보고서들은 `token_counter` 이벤트로 "어느 카운터를 썼나(source·real)"까지만 남겼고, 그 카운터가
서버 실측과 **얼마나 정확한가**는 숫자로 대조된 적이 없다.

**방법.** 33개 `model_call`의 `input_tokens`(서버 실측)와, 각 `prompt_ref` 파일에서 harness
주석(`<!-- … -->` 줄)을 걷어낸 실제 전송 텍스트를 서빙 스냅샷
(`NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28…`)의 `tokenizer.json`으로 센 값을 대조했다.

| | 서버 합 | 실 토크나이저 | 옛 `len//4` |
| --- | --- | --- | --- |
| 토큰 합(33콜) | 43,222 | 42,793 | 27,169 |
| 비율(합) | 1.000 | **0.990** | 0.629 |
| 비율(median) | — | 0.990 | 0.610 |
| 비율 범위 | — | 0.984 ~ 0.993 | 0.524 ~ 0.715 |

**핵심: 서버 − 우리 = 정확히 13토큰, 33콜 전부 예외 없음**(scout 1362−1349 · auditor 1437−1424 ·
critic 842−829 …). 그 13은 토크나이저 오차가 아니라 **채팅 템플릿 상수**(system+user 메시지
래핑)다 — 더하면 1.000으로 완전 일치한다. role별 비율 차이(critic 0.985 vs scout·auditor 0.990)도
오차가 아니라 프롬프트 크기 대비 +13의 비중 차이일 뿐이다(critic 프롬프트가 작다). **scout 19 ·
auditor 7 · critic 7 = 33콜 전부 포함.**

→ **D21 (b) 토크나이저는 정확도가 증명됐다.** 옛 `len//4`의 ≈1.6x 과소(D21이 든 "1.5~1.8x")가 실
토크나이저로 사라졌고, 남는 건 요청당 상수 +13뿐이다. 원한다면 후속에서 이 상수를 캡슐 예산에
요청당 오버헤드로 반영할 수 있다.

## 3. 부수 — critic는 실서빙됐다 (단, 같은 모델)

Prove가 critic 7콜을 포함하므로 그 콜들이 real인지 확인했다. `trace`의 critic `model_call`:

```
model = qwen3-coder-30b-a3b · ok = True · gpu_ms = 3085·2948·2681·… · gpu_ms_source = request_wall_clock
```

`gpu_ms`가 채워져 있고(Fake면 `None`) `input_tokens`가 실 토크나이저 +13이라(Fake면 `len//4`≈452였을
값) **critic는 real 서빙이 맞다.** 다만 `b1_single`은 scout·auditor·critic이 **같은 `qwen3-coder-30b-a3b`
한 모델·한 포트**를 쓴다 — critic 전용/다른 계열 모델을 따로 띄운 것이 아니다.

## 4. 의제 — `critic_top_n`은 무엇으로 정하나 (per-call 비용은 아직 근거 아님)

@danielcho02가 "critic 개수·횟수는 회의가 필요"라고 남긴 항목이다. 이 run에서 얻을 수 있는 것과 없는 것을
가른다.

**per-call 비용은 confound가 커 결정 근거로 못 쓴다.** 세 가지가 겹친다.

1. **모델이 실험과 다르다.** 이 run은 `b1_single`이라 세 역할이 같은 30B를 쓴다. 그러나 B1'/B3'의
   critic은 `qwen3-8b-bf16`(8B)이고 B3'는 역할마다 다른 모델이다 — 이 값은 "30B에 프롬프트를 더
   보낼 때"이지 "역할별 모델의 비용"이 아니다.
2. **`gpu_ms_source`가 `request_wall_clock`이다** — 순수 GPU가 아니라 요청 왕복(네트워크·큐잉·prefix
   cache 적중 여부 포함)이라 서버 부하에 흔들린다.
3. **역할 간 차이는 프롬프트 크기 차이다.** 관찰상 per-call GPU(요청 시간)는 auditor(≈5.1s) >
   critic(≈2.7s) > scout(≈2.2s)인데, 이는 프롬프트·출력 크기(auditor 1,635 in / 334 out vs critic
   842 / 176) 때문이지 역할의 본질 비용이 아니다. **"Critic이 가장 비싼 단계"라는 `budgets.yaml`의
   전제는 이 run의 per-call로는 성립하지 않는다** — critic의 실체적 비용은 per-call 연산이 아니라
   B3'에서 다른 계열 모델을 하나 더 띄우는 인프라 쪽일 가능성이 크고, `b1_single`에는 그 비용이 없다.

**따라서 `critic_top_n=8`은 확정 근거가 없는 관례값이다** — `budgets.yaml` 주석도 "상한이 필요"까지만
대고 "왜 8"은 없다(`candidate_max=256`이 관례였던 D24와 같은 상태). 그리고 MDASH 차용은 8의 근거가
아니다 — MDASH는 Validate/Debate **단계 구조**를 줬지 숫자를 주지 않았고, 100+ 에이전트·frontier로
대규모 검증한다. 우리는 "소수 역할·소형 모델로 축소"([prd.md](../prd.md))했으므로 cap이 필요하지만 그
값은 우리 비용 예산이 정할 몫이다.

**개수보다 선별 기준이 먼저다(D26).** 선별은 confidence 내림차순인데 8단계 진입 조건은 `repro_spec`
이고 둘이 독립이라, 상위 8건이 `repro_spec` 없는 것으로 채워질 수 있다 — 실측에서 8→44로 늘려도
8단계가 0건이었다. confidence로 자르는 한 개수를 바꿔도 E3 재료를 놓친다.

**회의에서 정할 두 축과, 그에 필요한 미측정 입력:**

| 축 | 필요한 실측 |
| --- | --- |
| ① 선별 기준 (급함) | confidence만 → `repro_spec` 반영? (D26) — 코드 판단이라 실측보다 결정이 먼저 |
| ② 개수 | 8B critic 비용(`run_0005` 등) · 순수 GPU 또는 격리 요청 · **캡이 실제로 자르는 run**의 손실 |

## 5. 재현

`run_20260827_0004`의 `trace.jsonl`·`prompts/`와 서빙 스냅샷 `tokenizer.json`을 받아, `model_call`
별로 (harness 주석 제거한 프롬프트의 실 토큰 count) vs (`input_tokens`)를 대조했다. 실 토큰 count는
`tokenizers.Tokenizer.from_file(...).encode(text).ids` 길이다. critic real 판정과 per-call 관찰은 같은
`model_call`의 `model`·`gpu_ms`·`gpu_ms_source` 필드에서 읽었다.

## 6. 안 잰 것

- **8B(B1'/B3' 실제 모델)의 토크나이저 정확도.** 이 Prove는 30B 스냅샷 하나에 대한 것이다 — 배선·기법이
  정확하다는 증명이고, 8B 토크나이저도 같은 경로를 타지만 그 숫자는 따로 재지 않았다.
- **정식 critic 비용.** 위 §4의 confound 셋 때문에 per-call은 관찰까지다.
- **+13 상수의 구성.** 채팅 템플릿 래핑으로 추정하나 템플릿을 토큰 단위로 분해해 확인하지는 않았다.
