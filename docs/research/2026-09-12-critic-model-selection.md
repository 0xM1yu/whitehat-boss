# Critic 후보 재선정 — 고성능·상주성·판정 정확도를 분리한다

**조사일: 2026-09-12.** 라벨: `[출처주장]`(제작사·벤더의 모델 카드나 배포 문서) ·
`[제3자]`(공개 benchmark·논문·오픈 소스 평가) · `[계산]`(파일 크기와 ATOM 예산으로
계산한 값) · `[미확인]`.

**여기 있는 것은 우리 실측이 아니다.** 외부 문서와 공개 평가로 후보를 좁힌 기록이다.
ATOM에서 실제로 잰 서버 시작·응답·정확도는 [experiments/](../experiments/README.md)에
남긴다. gpt-oss의 structured-output 결함과 그에 대한 코드 수정 요구는
[PR #182](https://github.com/0xM1yu/whitehat-boss/pull/182)에서 다룬다. 이 문서는 그것을
반복하지 않고, **gpt-oss 밖에서 다음에 무엇을 비교할지**만 정한다.

## 0. 답부터

| 물음 | 답 |
| --- | --- |
| ATOM 한 대에서 단독으로 가장 강한 비-gpt-oss 대형 후보는 무엇인가 | **`NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4`**. 단, Qwen Auditor와 동시 상주는 안 된다. |
| 현 Qwen3-Coder-30B-A3B Auditor를 유지한 채 다음 Critic으로 무엇을 재나 | **`NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`**. GB10·vLLM 0.27.1의 직접 배포 근거가 있고, 30B급이라 둘이 함께 뜰 가능성이 있다. |
| 새 모델 전에 반드시 확보할 기준선은 무엇인가 | **`Devstral-Small-2-24B-Instruct-2512`**. 이미 registry·cache·ATOM 기동 이력이 있으나 Critic 판정 실측은 없다. |
| 이 표만으로 운영 Critic을 확정할 수 있나 | **없다.** 공통된 C/C++ claim-verification benchmark가 없으므로, 같은 prompt·도구 예산·schema로 직접 비교해야 한다. |

이 결론은 “공개 벤치마크 최고점”이 아니라 다음 제약을 함께 적용한 결과다.

1. Critic은 `uphold`·`rebut`·`need_evidence`를 근거와 함께 내야 한다.
2. Auditor와 다른 계열이어야 한다는 현재 config 제약을 지킨다.
3. ATOM의 usable unified-memory 예산 안에서 32K context와 최소한의 KV cache를 남긴다.
4. 현 runtime인 vLLM 0.27.1·GB10에 대한 근거가 없으면 “즉시 후보”로 올리지 않는다.

## 1. 왜 일반 코딩 점수로 Critic 1등을 못 고르나

SWE-bench는 issue와 repository에서 패치를 만들어 hidden test를 통과하는지를 재고,
LiveCodeBench는 새 프로그래밍 문제의 풀이를 잰다. 둘 다 다음의 Critic 질문을 직접
채점하지 않는다.

- Auditor가 낸 취약점 claim이 실제 source→sink·권한 경계·재현 경로로 뒷받침되는가.
- patch·guard·invariant가 claim을 반박하기에 충분한가.
- 어느 쪽 근거도 충분하지 않을 때 `need_evidence`로 보류하는가.

[SWE-bench Verified의 설계](https://openai.com/index/introducing-swe-bench-verified/)도 agent
scaffold, tool policy, 실행 횟수가 결과에 영향을 준다는 전제를 둔다. 서로 다른 제작사가
서로 다른 harness에서 공개한 SWE·LCB 수치를 한 줄의 절대 순위로 읽지 않는다.

가장 가까운 공개 seed는 [SecLens](https://github.com/mattersec-labs/seclens)다 `[제3자]`.
이 자료는 실제 CVE의 vulnerable revision 203개와 같은 CVE의 patched revision 203개를
짝으로 두고 verdict·CWE·위치·근거·도구 사용을 기록한다. 다만 이것도 우리 pipeline의
세 범주 contract를 직접 채점하지는 않는다. 최종 결정은 다음 §5의 변환 평가를 통과한
후에만 한다.

현실 C/C++ 취약점 탐지에서 모델의 그럴듯한 설명을 바로 신뢰하면 안 된다는 근거도 있다.
[SecVulEval](https://arxiv.org/abs/2505.19828) `[제3자]`은 근거를 포함한 statement-level
판정을 따로 측정하며, 일반 모델의 false positive 문제가 남는다고 보고한다. 이 문서에서
`uphold` precision과 patched-negative TNR을 accuracy와 분리하는 이유다.

## 2. 후보를 세 운영 모드로 나눈다

### 2.1 현재 pipeline에서 상주하는 Critic

현재 Qwen3-Coder-30B-A3B Auditor가 약 35.6 GiB를 점유하는 관측을 전제로 하면, Critic은
(**출처**: [2026-08-28 track-a pwn003 보고서](../experiments/harness/2026-08-28-pipeline-track-a-pwn003-atom.md)
§"메모리"가 서버 A·B에서 **KV 포함 35.68 / 35.66 GiB**를 적는다 — 같은
`Qwen3-Coder-30B-A3B-Instruct-FP4`다. ⚠ **그 보고서가 스스로 단서를 단다**: 같은 시점
`nvidia-smi`는 **32.2 GiB**를 보고하고 *"무엇이 두 값을 가르는지는 안 쟀다"*. 35.6은
vLLM 자기 회계 쪽이고, 아래 예산 논의는 **둘 중 큰 값을 쓰는 보수적 가정**이다.)
가중치만으로 남은 예산을 거의 다 쓰면 안 된다. KV cache·non-torch 메모리·OS와 request
동시성을 남겨야 한다. 이 모드에서의 목적은 **최대 파라미터**가 아니라 “같은 run에서
정상적으로 판정하는 독립 Critic”이다.

| 순위 | 후보 | 외부 근거 | 상주성 판단 | 결론 |
| --- | --- | --- | --- | --- |
| 1 | **Nemotron 3.5 Lightning 30B-A3B NVFP4** | NVIDIA가 GB10 단일 GPU 및 vLLM 0.27.1 recipe를 직접 제공한다. 30B total/3B active다. [공식 모델 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) `[출처주장]` | Qwen과 함께는 낮은 `gpu_memory_utilization`, 32K, `max-num-seqs=1`부터 확인해야 한다. gpt-oss 서버와 세 모델 상주는 예산 밖이다. | **새 Critic smoke 1순위.** MTP·DSpark는 끄고 JSON contract부터 잰다. |
| 2 | **Devstral Small 2 24B** | Mistral은 SWE-bench Verified 68.0%를 보고하고, 256K context·Apache-2.0를 명시한다. [공식 모델 카드](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512) `[출처주장]` | 이미 ATOM cache·checksum·기동 이력이 있는 유일한 대체재다. | **기준선 1순위.** 공개 SWE 값이 Critic 정확도를 뜻하지 않으므로 운영 승격 전 동일 평가가 필요하다. |
| 3 | **Hunyuan-A13B-Instruct GPTQ-Int4** | 80B total/13B active, LiveCodeBench 63.9·EvalPlus 78.64를 Tencent가 보고한다. vLLM 0.27.1 supported-models 표에는 해당 architecture가 있다. [공식 모델 카드](https://huggingface.co/tencent/Hunyuan-A13B-Instruct) · [공식 GPTQ checkpoint](https://huggingface.co/tencent/Hunyuan-A13B-Instruct-GPTQ-Int4) `[출처주장]` | Int4 파일 약 41.6 GiB `[계산]`. Qwen과는 아주 작은 KV budget에서만 가능성이 있고 GB10 exact recipe는 없다. | **onboarding 2순위.** `gptq_marlin`·32K·낮은 util smoke 전에는 registry에 넣지 않는다. |

### 2.2 Qwen을 내린 단독 고성능 평가

**Nemotron 3 Super 120B-A12B NVFP4**는 이 모드의 1순위다. NVIDIA는 120B total/12B active,
long-context reasoning·agentic workflow 용도를 명시하고, LiveCodeBench v5 81.19를 포함한
공개 표를 제공한다. [NVIDIA 모델 카드](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard)
`[출처주장]`.

NVIDIA는 DGX Spark 전용 배포 절차도 제공한다. [Spark 배포 가이드](https://build.nvidia.com/spark/nemotron/overview)
`[출처주장]`. checkpoint 파일은 약 79.12 GiB `[계산]`라 Qwen 약 35.6 GiB(§2.1의 출처·
단서와 같은 수)와 동시에
안전하게 띄울 수 없다. 따라서 이 모델은 “현재 Critic 교체”가 아니라 **Auditor B 또는
단독 Critic evaluation**으로만 쓴다.

자동 stage swap은 아직 없다. 후보 단위 runner에서 대형 모델을 sleep/wake로 번갈아 쓰면
호출 횟수에 비례해 왕복하므로, 현 구조에서 같은 pipeline에 Super를 넣는 것은 하지 않는다.
단독 run으로 structured-output smoke와 claim-verification pilot을 먼저 남긴다.

### 2.3 보류 후보

| 후보 | 보류 이유 |
| --- | --- |
| **Ling-3.0-flash INT4/FP4** | 124B total/약 5B active, INT4/FP4 checkpoint와 Spark recipe가 있다. 그러나 INT4가 약 77 GB, FP4가 약 70 GB라 Qwen과 상주하지 못하고, 최신 공식 경로는 model-specific parser와 최신 upstream vLLM에 의존한다. [공식 모델 카드](https://huggingface.co/inclusionAI/Ling-3.0-flash) · [vLLM recipe](https://recipes.vllm.ai/inclusionAI/Ling-3.0-flash) `[출처주장]`. |
| **Mistral Small 4 119B-A6B NVFP4** | 119B total/6.5B active, reasoning·agentic·JSON output을 목표로 하고 NVFP4 checkpoint가 70.8 GB다. 그러나 Mistral은 최신 vLLM과 2-way TP recipe를 권장한다. 0.27.1·단일 GB10·Qwen 상주 조합은 미확인이다. [공식 모델 카드](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603-NVFP4) `[출처주장]`. |
| **GLM-4.7-Flash** | 30B/3B-active와 강한 vendor 수치가 있으나, 현 0.27.1이 아니라 최신 vLLM main 지원 근거만 확인됐다. BF16 checkpoint도 약 62.5 GB다. [공식 모델 카드](https://huggingface.co/zai-org/GLM-4.7-Flash) `[출처주장]`. runtime 변경과 함께 별도 onboarding 한다. |
| **DeepSeek V4 Flash** | vendor는 TerminalBench 2.1 82.7, Cybergym 76.7을 보고한다. 그러나 NVFP4 checkpoint가 168 GB이고 vLLM recipe의 기본 최소 VRAM도 170 GB다. 단일 ATOM 대상이 아니다. [공식 카드](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) · [NVIDIA NVFP4 checkpoint](https://huggingface.co/nvidia/DeepSeek-V4-Flash-NVFP4) `[출처주장]`. |

## 3. gpt-oss-120B와 Qwen 계열을 다음 실험에서 빼는 이유

이 선택은 “더 큰 gpt-oss가 반드시 실패한다”는 주장도, “Qwen 계열이 성능이 낮다”는 주장도
아니다.

- `gpt-oss-120b`는 20B보다 큰 open-weight reasoning 모델이지만 Harmony/MXFP4 및
  structured-output 경로를 공유한다. 현재 과제는 그 경로의 결함을 #182에서 분리·수정하는
  것이므로, 같은 계열 확대보다 독립 계열의 counterexample을 먼저 얻는 편이 낫다.
- Qwen3.5-122B-A10B와 Qwen3.6 계열은 공개 code/agent 지표가 강해도, 현재 Auditor가
  Qwen3-Coder다. Auditor와 Critic의 family split은 config가 강제하는 실험 조건이다.

둘 다 이후 대조군으로 가치는 있다. 다만 현 판정은 “다음 한 번의 ATOM 실험에서 gpt-oss
문제와 모델 일반 문제를 구별하는 데 가장 큰 정보량을 주는가”다.

## 4. 외부 API는 운영 모델이 아니라 상한선이다

[SecLens 결과](https://github.com/mattersec-labs/seclens#results) `[제3자]`는 Gemini 3 Flash
Preview를 security-oriented 역할의 강한 reference로, Claude Sonnet 4.6을 개발 review의
강한 reference로 제시한다. 이들은 자체호스팅 ATOM Critic 후보가 아니다.

30–50개 CVE pair pilot에서 API model 하나를 읽기 전용 상한선으로 두면, 로컬 후보의 절대
위치를 알 수 있다. API 점수만 보고 로컬 profile을 바꾸거나, 반대로 API의 결과를 정답으로
자동 채택하지 않는다. 데이터 cutoff와 CVE leakage 가능성은 별도로 기록한다.

## 5. 운영 승인 전에 할 공통 평가

### 5.1 입력과 라벨

SecLens의 CVE 전·후 쌍에서 시작하되, 같은 claim을 세 방식으로 만든다.

| 입력 | 기대 verdict | 필요한 근거 |
| --- | --- | --- |
| vulnerable revision + 실제 finding | `uphold` | source→sink/권한 경계/재현 경로 또는 동등한 code evidence |
| patched revision + 같은 finding | `rebut` | guard·sanitization·invariant·도달 불가를 파일·라인으로 제시 |
| revision에서 필수 code/config/build evidence 제거 | `need_evidence` | 어느 쪽도 입증할 수 없음을 명시 |

각 model에는 같은 `critic/v1` prompt, source bundle, tool budget, temperature, context limit,
output-content schema를 준다. 모델이 요청 ID·run ID·finding ID·producer metadata를 생성하게
하지 않는다. 이 metadata는 node가 context와 registry에서 결정적으로 주입한다.

### 5.2 합격표

| 축 | 반드시 기록할 값 |
| --- | --- |
| 취약점 수용 | `uphold` precision, critical/high recall |
| 오탐 제거 | patched-negative TNR, `rebut` precision |
| 불확실성 | `need_evidence` coverage와 calibration; 유보를 `rebut`로 세지 않는다 |
| 근거 | evidence file/line 정확도, CWE 및 path 참조의 타당성 |
| 계약 | JSON parse율, 허용 verdict 외 문자열 비율, node-generated provenance 일치율 |
| 안정성 | 같은 입력 재실행 일치율, p50/p95 latency, 32K와 64K OOM/timeout |
| 조합 효과 | Auditor와 Critic이 동시에 놓친 critical/high finding 수 |

`uphold`·`rebut`·`need_evidence`의 class 비율이 다르므로 한 개 accuracy나 F1만으로
승인하지 않는다. 임계값은 D4가 표본 크기와 허용 오차를 정한 뒤에 선언한다. 이 조사에서
숫자를 만들어 합격 처리하지 않는다.

**관문 0 — 온보딩 착수 전에 런타임 호환부터 확인한다.** §2.2·§2.3의 보류 후보
(Ling-3.0-flash · Mistral Small 4 · GLM-4.7 · DeepSeek V4 등)는 전부 `[출처주장]`이고,
**현 pinned runtime(vLLM 0.27.1)에서의 JSON·reasoning parser 호환성을 §7이 「확인하지
못한 것」으로 둔다.** 그 확인 없이 위 5.1~5.2 평가에 GPU 시간을 쓰지 않는다 — 계약
실패는 정확도 표에 들어오지도 못한다. 확인 항목은 §7의 넷째 줄과 같은 것이다.

## 6. 실행 순서

1. ~~#182의 strict verdict parser 다음으로 **모델 생성 metadata 제거**를 끝낸다.~~
   **완료 — #182가 둘을 같이 했다**(main `0148719`). 이 문서를 쓸 때는 parser의 「다음」
   단계로 봤는데, 머지된 #182가 `nodes/critic.py`의 `OUTPUT_SCHEMA`를 `DecisionDraft`로
   바꾸며 *"provenance는 하네스가 넣는다"*까지 같은 PR에서 끝냈다 —
   `decision_id`·`run_id`·`finding_id`·`produced_by` 넷을 모델이 안 만든다. §5.1이 요구한
   *"metadata는 node가 결정적으로 주입한다"*는 **이미 성립한 상태에서** 아래 2~6이 시작한다.
2. Devstral Small 2를 실제 `critic/v1`의 양성·오탐·근거 부족 3×3 smoke로 측정한다.
3. gpt-oss를 중지 또는 sleep 처리한 뒤 Lightning을 같은 input과 계약으로 측정한다.
   첫 run은 MTP/DSpark speculative decoding 없이 한다.
4. 3×3에서 contract와 세 verdict를 모두 통과한 후보만 30–50 CVE-pair pilot으로 올린다.
5. Super는 Qwen을 내린 단독 run에서 평가한다. stage-level execution을 구현하기 전에는
   현 pipeline에 배치하지 않는다.
6. profile 변경은 §5의 contract·정확도·상주성 기록을 모두 남긴 뒤에만 한다.

## 7. 확인하지 못한 것

- 후보들이 같은 C/C++ claim-verification set에서 낸 직접 비교 점수.
- Lightning과 Qwen의 실제 동시 상주 memory/latency.
- Hunyuan의 GB10·vLLM 0.27.1·32K 단일 GPU 장기 안정성.
- Ling, Mistral Small 4, GLM-4.7의 현 pinned runtime에서의 JSON·reasoning parser 호환성.
- 서로 다른 모델 계열이 실제 취약점 claim에서 동시에 틀릴 확률.

이 다섯은 문헌으로 메우지 않는다. ATOM 실험의 측정 항목으로 남긴다.
