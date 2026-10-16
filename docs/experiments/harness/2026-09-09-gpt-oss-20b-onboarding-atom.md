# `gpt-oss-20b` 온보딩 첫 관문 — ATOM

> **한 줄:** 뜬다. 답도 맞힌다. **그런데 JSON 스키마를 강제하는 순간 못 쓰게 된다.**
> ⚠ 아래 정정 — **그 「못 쓰게 된다」의 원인이 serve 설정인지 스키마 어휘인지 아직 안 갈렸다.**

> **정정 (2026-09-09, [#160](https://github.com/0xM1yu/whitehat-boss/pull/160) 코멘트 후속).** §5가 미측정으로 남긴
> *"`--structured-outputs-config.reasoning_parser` 명시 지정"*을 팀이 ATOM에서 돌렸고
> **verdict 3/3 정답**을 얻었다([#160 코멘트](https://github.com/0xM1yu/whitehat-boss/pull/160#issuecomment-5591142118)).
> **관측은 그대로다** — 2026-09-09 03:14에 이 설정으로 본 것이 0/5이고, 바뀐 것은 그 관측이
> 무엇까지 말하는가다(CLAUDE.md §2.2).
>
> **⚠ 그런데 그 run은 §5가 처방한 실험이 아니다.** §5의 「무엇을 하면 재는가」 칸은(`TEMPLATE.md` §5)
> *"그 플래그를 주고 재기동해 **§3.3 반복**"*인데, 새 run은 §3.3을 반복하지 않고 **넷을 함께 바꿨다**:
>
> | 축 | 이 실험(§3.3) | 새 run |
> | --- | --- | --- |
> | 스키마 verdict 어휘 | `upheld` / `rejected` | **`vulnerable` / `safe`** |
> | 케이스 | 같은 UAF 프롬프트 **5시행** | **서로 다른 3케이스** 각 1회 |
> | 기동 인자 | — | **`--enforce-eager`·`VLLM_USE_FLASHINFER_SAMPLER=0` 추가** (재현 블록은 `VLLM_ATTENTION_BACKEND=TRITON_ATTN`도 적는데 §1이 *"선택지가 그것 하나"*라 이 실험과 같은 값이다) |
> | `max_tokens` | 미기록 | **3000** (700은 잘렸다고 적는다) |
>
> **그래서 관측은 「그 serve 설정에서 3/3」이고, 「플래그가 결정적이다」는 추론이다.**
>
> **어휘 하나만으로도 설명이 된다.** §3.3의 `xgrammar` 조건은 **0/5**이고(「넷」은 오답 수가
> 아니라 **JSON이 유효해 `reason`을 읽을 수 있었던 시행 수**다), 그 넷이 전부
> *"The answer is incomplete."* 류로
> **답안을 채점하는** 모양이었다 — `upheld`/`rejected`라는 판정 어휘를 주면서 판정할 대상(답안)은
> 안 줬기 때문일 수 있다. 같은 §3.3 안에서 `response_format` **없이는 3/3**이었다는 것이 그 방향을
> 받친다: 모델은 답을 알고 있었고 스키마가 과업을 다르게 읽혔다.
>
> **⚠ 그리고 두 run 어느 쪽도 실제 Critic 스키마가 아니다.** 배포될 `Decision.verdict`는
> `uphold` | `rebut` | `need_evidence`이고 사유 필드는 `reason`이 아니라 `rationale`이다
> (`src/schemas/enums.py`의 `Verdict`, [agent-contracts.md](../../agent-contracts.md) §3).
> 그러니 **둘 다 Critic 자리 판정의 근거로는 부족하다.**
>
> **아직 열려 있는 것:** ① 같은 케이스·같은 스키마·같은 `max_tokens`로 **플래그만** 바꾼 A/B,
> ② 실제 `Decision` 스키마로 반복, ③ §6이 남긴 「EngineCore가 해석한 값이 실제 문법 적용에
> 쓰이는가」. **①은 §5가, ③은 §6이 이미 미측정으로 적어 뒀다. ②는 이 정정이 새로 낸 것이다** —
> 원 보고서는 §3.3이 쓴 어휘가 배포될 `Decision` 스키마와 다르다는 것을 한 번도 안 적었고
> §5 미측정 표에도 그 행이 없다. **그리고 셋 중 ②가 제일 싸다**(§7 후속 2).
>
> ### ⚠ 2026-09-11 — ①과 ②가 답을 냈다. **둘 다 부정이다**
>
> [어휘 × 플래그 보고서](2026-09-11-gpt-oss-structured-vocab-flag-atom.md). ①은 verdict
> 시퀀스가 **바이트 단위로 동일**했고, ②는 1/5로 `report33`과 같았다. **서술 어휘
> (`vulnerable`/`safe`)를 세 번째로 재니 0/5로 더 나빴다.**
>
> **그래서 위 3/3의 원인으로 남은 축은 「케이스」 하나다** — 그 run이 바꾼 넷 중
> 플래그·기동 인자·`max_tokens`를 그 실험이 상수로 잡았고 어휘도 기각됐다.
> ⚠ **「#160이 틀렸다」로 읽지 않는다.** 3/3은 관측이고, 갈린 것은 그 원인이 무엇이
> **아닌가**다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-09 03:14 KST` |
| 실제 서빙 모델 | `hf://openai/gpt-oss-20b@6cee5e81ee83917806bbde320786a8fb61efebee` (`--served-model-name gpt-oss-20b`는 별칭이다) |
| `repo_commit` | 하네스 코드를 안 돌렸다 — `vllm serve` + `curl`만 썼다 |
| `run_id` | **없다.** `cli run`을 안 돌렸다. 이 모델은 registry에 없어서 프로필에 못 올린다 |
| run 산출물 경로·보존 여부 | 해당 없음 |
| `tool_runner` | 해당 없음 |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 |
| `--gpu-memory-utilization` | `0.30` — **분모는 물리 통합메모리 119.7 GiB**(35.91 GiB 요청) |
| 동시에 떠 있던 다른 서버 | **없다.** 매 기동 전 `dropcache.py` 실행, 기동 시점 여유 111.93~112.01 GiB |
| 원문 산출물 보존 위치 | **보존하지 않았다** — `curl` 응답을 파이프로 흘렸다. 아래 §3.3 표가 재구성본이다 |
| 서빙 서버 stdout | `~/vllm_8006.log`(1차) · `~/vllm_8006b.log`(2차) · `~/vllm_8006c.log`(3차). **ATOM 홈에만 있고 저장소에 없다** |
| vLLM · 환경 | `0.27.1` · `huggingface_hub 1.27.0` · venv `/srv/tenants/user/venv-probe` |

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| sm_121에서 MXFP4 기동 | **된다** (`quantization=gpt_oss_mxfp4`) |
| MoE backend | **MARLIN** — `Using 'MARLIN' Mxfp4 MoE backend` → `Using MarlinExperts` |
| attention backend | `TRITON_ATTN` — **선택지가 그것 하나**로 보고된다 |
| 가중치+non-torch | **14.91 ~ 16.06 GiB** (3회 기동, 아래 §3.2) |
| KV (util 0.30) | 18.35 ~ 19.58 GiB / **709,817 ~ 757,574 토큰** |
| 별도 에셋 | **Harmony vocab이 필요하다.** 없으면 모든 요청이 500 |
| structured output — `xgrammar` | **5/5 오답** (JSON은 4/5 유효) |
| structured output — `guidance` | **5/5 HTTP 500** |
| structured output 없이 | **3/3 정답** |

**판정: 기동 통과 · structured output 실패.** [model-serving.md](../../model-serving.md) §3 체크리스트의 1번은 통과, 6번은 실패다. ⚠ **6번의 「실패」는 이 스키마·이 serve 설정 범위다** — 머리의 정정 블록을 함께 읽는다. 그리고 6번을 **닫는** 것은 `cli bench`의 `structured_output.valid_ratio`이지 이 `curl` 관문이 아니다.

---

## 2. 실험 환경

전 기동 공통 환경변수:

```
VLLM_USE_DEEP_GEMM=0  VLLM_HOST_IP=127.0.0.1
NCCL_SOCKET_IFNAME=lo  GLOO_SOCKET_IFNAME=lo
```

2·3차는 여기에 `TIKTOKEN_ENCODINGS_BASE=$HOME/tiktoken_encodings`를 더했다(§3.1).

```
vllm serve openai/gpt-oss-20b --port 8006 --served-model-name gpt-oss-20b \
  --max-model-len 32768 --gpu-memory-utilization 0.30 \
  --structured-outputs-config.backend {xgrammar|guidance}
```

⚠ **`--structured-outputs-config.reasoning_parser`를 CLI로 주지 않았다.** 다만 로그는 vLLM이 그것을 **스스로 채웠다**고 적는다 — APIServer의 「non-default args」 줄은 `reasoning_parser=''`인데, EngineCore의 엔진 초기화 줄은 같은 기동에서 `reasoning_parser='openai_gptoss'`다(3회 전부). **따라서 「파서가 없어서 채널 경계를 몰랐다」는 이 로그로 성립하지 않는다.** 명시 지정이 결과를 바꾸는지는 **안 해봤다**(§5·§6).

`config.json`이 선언하는 것:

```
model_type = gpt_oss · num_hidden_layers = 24 · num_local_experts = 32 · hidden_size = 2880
quantization_config = {"quant_method": "mxfp4",
  "modules_to_not_convert": ["model.layers.*.self_attn", "model.layers.*.mlp.router",
                             "model.embed_tokens", "lm_head"]}
```

`model.safetensors.index.json`: shard 3개 · 텐서 459개 · 그중 `blocks`/`scales` **96개**(24층 × 4).
**MoE 전문가만 4비트고 attention·router·embed·lm_head는 아니다** — 위 `modules_to_not_convert`가 그렇게 적는다.

### repo 크기 ≠ 로드 크기

| 대상 | 크기 |
| --- | --- |
| repo 전체 | **39 GB** |
| `model-*.safetensors` (vLLM이 읽는 것) | **13 GB** |
| `metal/` | 13 GB |
| `original/` | 13 GB |

vLLM 자신이 `Checkpoint size: 12.82 GiB`로 보고한다. **`metal/`·`original/`은 vLLM이 안 읽는 사본**이고, 다운로드 시간·디스크의 2/3가 거기다. 다음 온보딩(리랭커 4B·Nemotron-120B)의 비용 추정에 이 구분이 들어가야 한다.

---

## 3. 실험

### 3.1 벽 하나 — Harmony vocab이 모델 밖에 있다

1차 기동은 성공했는데 **모든 요청이 500**이었다. 서버 로그:

```
File ".../vllm/entrypoints/openai/parser/harmony_utils.py", line 100, in get_encoding
  _harmony_encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
openai_harmony.HarmonyError: error downloading or loading vocab file:
  failed to download or load vocab file
```

`openai_harmony.abi3.so`의 문자열이 원인을 지시한다:

```
the env var TIKTOKEN_ENCODINGS_BASE is not set, or invalid
https://openaipublic.blob.core.windows.net/encodings/
o200k_base.tiktoken   cl100k_base.tiktoken
446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d
o200k_harmony  ← o200k_base를 확장해서 만든다
```

**차단이 아니라 느린 것이었다.** 같은 URL에 `curl`을 걸면 HTTP 200이 오는데 20초에 3,613,922 바이트 중 1,572,442 바이트만 왔다 — **약 77 KiB/s** `[계산]`. 라이브러리 내부 타임아웃이 그 전에 끊는다.

**해결**: 파일을 따로 받아(`--retry 5 -C - -m 600`) `TIKTOKEN_ENCODINGS_BASE`에 디렉터리로 물린다. 받은 파일의 sha256이 위 기대값과 **일치**했다.

⚠ **`TIKTOKEN_RS_CACHE_DIR`로는 안 된다.** 같은 디렉터리를 그 변수로 주면 실패가 재현된다 — 그 캐시는 해시 파일명을 쓴다.

⚠ **env var는 `vllm serve` 프로세스 환경에 들어가야 한다.** Harmony 렌더링은 EngineCore가 아니라 **APIServer** 쪽에서 돈다(위 트레이스백의 `entrypoints/openai`).

### 3.2 메모리 — 세 번 재서 범위로 남긴다

같은 인자(`util 0.30`, `max-model-len 32768`)로 세 번 기동했다.

| 회차 | backend | 가중치+non-torch | peak activation | CUDAGraph | KV | KV 토큰 | 기동 시점 여유 | UP까지 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | xgrammar | **16.06 GiB** | 1.5 | 0.58 | 18.35 | 709,817 | 111.93 | 105s |
| 2 | xgrammar | **15.17 GiB** | 1.24 | 0.75 | 19.50 | 754,443 | 111.99 | 100s |
| 3 | guidance | **14.91 GiB** | 1.42 | 0.81 | 19.58 | 757,574 | 112.01 | 130s |

**한 점으로 못 박지 않는다 — 14.91~16.06 GiB다.** 같은 인자에서 1.15 GiB(7.7%) 흔들린다.

파생값 `[계산]`:

- **Marlin 팽창** = 가중치 / checkpoint(12.82) → **1.16 ~ 1.25배**
- **밀도** = 가중치 / 20B → **0.75 ~ 0.80 GiB/B**

⚠ **NVFP4 기준 census 상수(0.58 GiB/B)를 MXFP4에 그대로 쓰면 안 된다** — 관측이 그보다 높다. 두 양자화의 상수는 따로 잡아야 한다.

### 3.3 첫 관문 — structured output

같은 프롬프트를 세 조건으로 돌렸다. 프롬프트는 **정답이 자명한** 것을 골랐다:

```
Does this C snippet have a use-after-free?
char *p=malloc(8); free(p); strcpy(p,"x");
```

**정답은 use-after-free 있음** → 스키마상 `verdict: "upheld"`.

| 조건 | 시행 | JSON 유효 | **verdict 정답** | `completion_tokens` |
| --- | --- | --- | --- | --- |
| `xgrammar` + `response_format` | 5 | 4/5 | **0/5** (전부 `rejected`) | 887·1144·1053·988·1034 |
| `guidance` + `response_format` | 5 | — | — (**5/5 HTTP 500**) | — |
| `response_format` 없음 | 3 | 해당 없음 | **3/3** | 856·701·587 |

전 시행 `finish_reason: stop`이고 `reasoning_effort: "high"`였다.

**`xgrammar` 응답의 `reason` 필드가 무엇을 담았나** — 코드 분석이 아니라 **「어떤 답변을 채점하는」 내부 독백**이다:

```
{"verdict":"rejected","reason":"The answer is incomplete."}
{"verdict":"rejected","reason":"The assistant's final reply was not a valid JSON object."}
{"verdict":"rejected","reason":"The answer does not contain an answer to the original question."}
{"verdict":"rejected","reason":"The user explicitly requested a direct answer …
   However, since the assistant is not allowed to omit explanations, it must comply with the policy. …"}
```

**같은 모델이 `response_format` 없이는 맞힌다:**

```
Yes – that snippet is a classic **use-after-free** bug.
Yes – it's a classic use-after-free.
Yes – the code is a classic **use-after-free**.
```

**`guidance`는 500만 낸다.** 서버 로그에 트레이스백이 없고 응답의 `message`도 빈 문자열이라 사유를 못 봤다.

⚠ **`reasoning_content`가 세 조건 전부에서 비어 있었다.** `response_format` 없는 자유 서술에서도 비었다 — ~~**스키마 강제와 무관하게 채널이 API 응답에서 갈리지 않는다.**~~
>
> **정정 (2026-09-12, ATOM 실측).** **필드 이름이 틀렸다.** vLLM 0.27.1의 응답 `message` 키 집합은 `annotations`·`audio`·`content`·`function_call`·**`reasoning`**·`refusal`·`role`이고 `reasoning_content`는 **없다** — 자유 서술과 스키마 강제 양쪽에서 같다. 올바른 키로 읽으면 채널은 **갈려 나온다**(실 Critic 프롬프트에서 추론 `high` 484~16,381토큰 · `medium` 69~227 · `low` 12~23). 그러므로 취소선 친 문장은 거짓이고, 아래 「왜인지는 안 갈랐다」도 갈렸다 — **우리가 없는 키를 읽었다**. 같은 결함이 `tools/probe/gpt_oss_structured.py`에 있었고 그 필드로 낸 관측(「reasoning 빔」)은 전부 무효다. 근거: [2026-09-12 보고서](2026-09-12-critic-gptoss-token-accounting-atom.md) §4. ⚠ **이 정정이 §3.3의 verdict 결과를 뒤집지는 않는다** — 그쪽은 별개 축이다. 그리고 EngineCore 로그는 `reasoning_parser='openai_gptoss'`로 **채워져 있었다**고 적는다(§2). 즉 관측은 「파서 미설정」이 아니라 **「파서가 설정돼 있는데도 `reasoning_content`가 빈 것」**이다. 왜인지는 **안 갈랐다.**

---

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| `python3`에 `huggingface_hub` 없음 | 시스템 python(`/usr`)에는 없다 | `source /srv/tenants/user/venv-probe/bin/activate` |
| 전 요청 500 | Harmony vocab을 원격에서 받다 실패(§3.1) | `TIKTOKEN_ENCODINGS_BASE`에 로컬 디렉터리 |
| `TIKTOKEN_RS_CACHE_DIR`로 안 풀림 | 그 캐시는 해시 파일명을 쓴다 | 위 변수를 쓴다 |
| `guidance` 500 | **미해결** | 사유 미확인 |

---

## 5. 미측정 항목과 사유

**못 잰 값을 0으로 채우지 않는다.**

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 처리량 (`tok_s_single`·배치) | 이 모델이 registry에 없어 프로필에 못 올린다 → `cli bench`를 못 돌린다 | registry 등재 후 `bench --roles critic` |
| context 8K/16K/32K KV·처리량 | 위와 같다 | 위와 같다 |
| prefix cache 적중률 | 위와 같다 | 위와 같다 |
| **Critic 반박 정확도** | bench 범위 밖 — 고정 반박 표본이 필요하다([model-serving.md](../../model-serving.md) §3 7번, D4) | 표본 데이터셋 확정 후 |
| `guidance` 500의 사유 | 로그에 트레이스백이 없고 응답 `message`가 비었다 | 서버 로그 레벨을 올려 재현 |
| `outlines` 백엔드 | ✅ **2026-09-11에 측정됐다 — 5/5 HTTP 500.** `guidance`와 같은 자리다([9팔 보고서](2026-09-11-gpt-oss-structured-vocab-flag-atom.md) §7). 서버는 정상 기동했고 자유서술은 3/3으로 돈다 — 500은 `response_format`을 실은 요청에서만 난다. ⚠ 사유는 안 갈랐다(로그 레벨) | 측정 완료 |
| `enable_in_reasoning=True` | **안 해봤다** | 그 값으로 재기동해 §3.3 반복 |
| `--structured-outputs-config.reasoning_parser` 명시 지정 | ✅ **2026-09-11에 측정됐다 — 플래그는 아무것도 안 바꾼다.** 같은 케이스·같은 스키마·같은 `max_tokens`로 플래그만 바꾼 A/B에서 **verdict 시퀀스가 바이트 단위로 동일**했다([보고서](2026-09-11-gpt-oss-structured-vocab-flag-atom.md) §3). §2의 관측(EngineCore가 이미 채웠다)이 맞았다 | 측정 완료 |
| *(추가)* 스키마 어휘 | ✅ **2026-09-11에 측정됐다 — 어휘가 아니다.** 실제 `Decision` 어휘 1/5, 서술 어휘 `vulnerable`/`safe` **0/5**(더 나쁘다). 아래 §7 후속 2가 제일 싸다고 적은 그 실험이다 | 측정 완료 |
| 장시간 서빙 안정성 | 스모크 3분 이내 | 연속 서빙 — [swap.md](../../research/2026-09-04-swap.md)가 `#49926`·`#52225`를 든다 |
| ~~`reasoning_content`가 빈 이유~~ | **갈렸다 (2026-09-12)** — 필드 이름이 `reasoning`이고 `reasoning_content`는 없다. 채널은 정상적으로 갈려 나온다 | 해소. [2026-09-12 보고서](2026-09-12-critic-gptoss-token-accounting-atom.md) §4 |

---

## 6. 반증 조건

- **「structured output이 이 모델을 망가뜨린다」가 틀렸다면**: `outlines` 또는 `enable_in_reasoning=True`에서 정답률이 오른다. **둘 다 안 해봤으므로 이 결론은 두 백엔드 범위다.** ⚠ **2026-09-09 — 셋째 경로가 생겼다**: 스키마 어휘가 원인일 수 있다(머리의 정정 블록). 그러면 백엔드를 안 바꿔도 이 결론이 틀린다. ⚠ **2026-09-11 — 그 셋째 경로가 닫혔다.** 어휘 셋을 같은 서버에서 재서 전부 실패했고 서술 어휘가 제일 나빴다([보고서](2026-09-11-gpt-oss-structured-vocab-flag-atom.md) §4). **남은 반증 경로는 `outlines`·`enable_in_reasoning` 둘뿐이다.**
- **「모델은 멀쩡하다」가 틀렸다면**: 자유 서술 3/3은 문제가 자명해서 나온 것일 수 있다. 어려운 표본에서 무너지면 이 문장은 좁혀야 한다. **표본이 하나이고 자명한 것을 골랐다.**
- **「Marlin 팽창 1.16~1.25배」가 틀렸다면**: 다른 크기에서 비율이 달라진다. **20B 한 점이다** — `#50925`가 신고한 1.57배(67→105 GiB)는 더 큰 모델의 값이고 이 관측이 그것을 반증하지 않는다.
- **메모리 범위가 틀렸다면**: 3회는 적다. 이상치가 있으면 범위가 바뀐다.
- **⚠ 검토된 가설 하나 — 「`reasoning_parser`를 CLI로 안 줘서 채널 경계를 몰랐다」**(PR #160에서 팀 제기). **이 로그로는 성립하지 않는다** — CLI에 안 준 것은 맞지만 EngineCore가 `openai_gptoss`로 채웠다(§2). 다만 **「해석된 값이 실제 문법 적용 시점에 쓰였는가」는 우리가 안 봤다.** 그것까지 부정하지는 않는다. 확인법: 그 플래그를 명시하고 §3.3을 반복해 verdict가 0/5에서 움직이는지 본다 — **움직이면 이 실험의 결론이 바뀐다.** ⚠ **2026-09-09 — 그 확인법이 교란된 채로 실행됐다.** 플래그를 준 run이 3/3을 냈으나 §3.3 반복이 아니라 축 넷이 함께 바뀌었다. **머리의 정정 블록**을 읽는다 — 이 문단만 보고 「가설이 반증됐다」로 읽으면 안 된다.

---

## 7. 후속 과제

1. **Critic 자리 판정** — 이 결과로 정할 수 없다. 두 갈래이고 **둘 다 `ADR 0010` 리뷰 사안이다** — 그 문서를 들고 오는 PR은 #148이다(2026-09-09 조회 기준 `origin/main`에 없어 링크가 아니라 번호로 적는다. ADR은 서로를 번호로 참조한다 — [experiments/README.md](../README.md)):
   - **(가) 하네스를 2단으로** — Critic을 자유 서술로 받고 추출 단계를 붙인다. [로스터 조사](../../research/2026-09-08-roster.md) §8이 *"깨지면 「자유 서술 → 별도 추출」 2단이 다음 수다"*로 예비 수를 적어 뒀다. 대가: 코드 변경 + 실패 지점 하나 추가.
   - **(나) Critic 모델 교체** — 하네스 0줄. 대가: 로스터가 `gpt-oss-20b`를 고른 근거를 버린다.
2. **`outlines`·`enable_in_reasoning=True` 두 조건**을 마저 돌린다 — (가)/(나)를 정하기 전에.
   ⚠ **그 앞에 더 싼 것이 생겼다**(머리의 정정 블록) — 스키마 어휘를 실제 `Decision`
   (`uphold` | `rebut` | `need_evidence`, 사유 필드는 `reason`이 아니라 `rationale`)로 바꿔
   §3.3을 그대로 반복하는 것이다. §3.3의 오답이 전부 「답안 채점」 모양이었으므로
   **어휘가 원인이면 serve 설정을 안 건드리고도 뒤집힌다.** 그 A/B가 (가)/(나) 판정의
   입력이고, 지금 두 갈래는 **어느 쪽도 근거가 안 선 상태다.**
3. **registry 등재**(ADR 0010 후속 3, PR A) — 등재해야 `cli bench`로 §5의 미측정 넷을 잰다. 등재 시 `config/models.yaml` 주석에 **`TIKTOKEN_ENCODINGS_BASE`를 기동 조건으로** 적는다.
4. **로스터 §1의 「약 13 GiB(계산)」을 실측 범위로 갱신** — 14.91~16.06이다.

---

## 관련

- [model-serving.md](../../model-serving.md) §3 — 배포 전 필수 검증 체크리스트(이 실험이 1번을 답하고, 6번은 **이 스키마·이 serve 설정 범위로** 답한다 — 머리의 정정 블록)
- [research/2026-09-08-roster.md](../../research/2026-09-08-roster.md) §1·§4·§8 — 로스터, Critic 선정 근거, 온보딩 순서
- [research/2026-09-08-critic-quantization.md](../../research/2026-09-08-critic-quantization.md) — gpt-oss 네이티브 MXFP4·Harmony
- `ADR 0010` — 로스터를 정하는 문서. **2026-09-09에 승인됐다**(팀 확정 + 팀 리뷰). 위 후속 1이 여기로 갔다. **PR #148**
- [experiments/README.md](../README.md) — 보고서 규약
