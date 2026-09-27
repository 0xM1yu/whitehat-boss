# vLLM 0.27.1이 실제로 내는 400 본문과 러너 분류 — ATOM

> [D88](../../open-questions/D/D88.md)의 수정은 **「크기 400만 `maximum context length` 문구를 싣는다」**는 전제에 선다.
> 그 전제는 등재 때 vLLM 소스 판독뿐이었다. 이 보고서는 그 전제를 **우리 장비에서, 하네스 어댑터가 받은
> 응답 그대로** 쟀다. 하네스 run이 아니다 — 어댑터(`OpenAICompatModelClient.complete`)를 직접 불렀다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-26 18:00 KST` — 서버 정리 직후. 로그의 마지막 요청은 17:59:53 |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca` — 별칭 `probe-a`·`probe-b`(같은 가중치) |
| `repo_commit` | `194b08fb96394d3ba46a0bd2e30733380299dd1a`(PR #298 head, clean — 새 detached 워크트리 `/srv/tenants/user/d88-probe/wt`) |
| `run_id` | 없음 — `cli run`이 아니다 |
| run 산출물 경로·보존 여부 | `/srv/tenants/user/d88-probe/out/probe_400.json` 보존(sha256 `c80f572c71ac44895669b60fb198eb12a631a7811e63e4bc769ca6813009853c`, 로컬 회수본과 같다) |
| `tool_runner` | 해당 없음 — 도구를 안 돌렸다 |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 |
| `--gpu-memory-utilization` | 서버마다 `0.10`, 분모 119.7 GiB |
| 동시에 떠 있던 다른 서버 | 없음 — 기동 전 GPU 연산 프로세스 0 · vLLM 프로세스 0, 이 두 서버뿐이었다 |
| 원문 산출물 보존 위치 | 위 `probe_400.json`, 이 보고서 §3 「근거 로그」에 원문 전부 |
| 서빙 서버 stdout | `/srv/tenants/user/d88-probe/logs/probe-a.log`·`probe-b.log` 보존 |

## 1. 요약

**크기 문구를 싣는 400은 컨텍스트 초과 하나뿐이었다.** 설정·요청 모양 거절 다섯은 전부 400이고, 그 본문 어디에도
`maximum context length`가 없다. 하네스 어댑터가 받은 `ModelGatewayError`를 PR #298의 러너 분류에 그대로 넣었다.

| 경우 | 상태 | `_is_size_rejection` | `_rejects_every_request` |
| --- | --- | --- | --- |
| 정상 요청 | 200 | — | — |
| 컨텍스트 초과 | 400 | **참** | 거짓 |
| 출력 상한 > 창 | 400 | 거짓 | **참** |
| 사고 예산 + reasoning parser 없음 | 400 | 거짓 | **참** |
| 템플릿이 모르는 `reasoning_effort` | 400 | 거짓 | **참** |
| 요청 본문 검증(pydantic) | **400** — 422가 아니다 | 거짓 | **참** |
| 사고 예산 + V2 러너 | 400 | 거짓 | **참** |

400이 난 여섯 경우 모두 `_is_droppable`이 참이었다 — 서버가 살아 있고 요청에 답했다는 판정이다.

## 2. 실험 환경

- ATOM `atom-host` — NVIDIA GB10, 통합 메모리 119.7 GiB
- vLLM `0.27.1` · Python `3.12.3`(`/srv/tenants/user/venv-probe`)
- 하네스 코드: 위 `repo_commit`. 프로브 스크립트는 워크트리 밖(`/srv/tenants/user/d88-probe/d88_probe.{sh,py}`)에 두었다

## 3. 실험 1 — 400 본문과 러너 분류

### 목적

경로 성립 판정이다(성능 측정이 아니다). 판정하는 것은 두 가지다. ① vLLM 0.27.1의 400 중 크기 거절만 컨텍스트 초과
문구를 싣는가. ② 하네스 어댑터가 그 본문을 잘리지 않은 채 `detail`로 러너에 넘기는가.

### 조건

서버 둘을 차례로 띄웠다. 기동 방식은 `~/smoke-0924/launch.sh`(2026-09-24 스모크)와 같다. 공통 env는
`VLLM_USE_DEEP_GEMM=0 VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo`다.

```
vllm serve Qwen/Qwen3-0.6B --served-model-name probe-a --host 127.0.0.1 --port 8090 \
  --gpu-memory-utilization 0.10 --max-model-len 4096 --max-num-seqs 4 --enforce-eager \
  --structured-outputs-config.backend xgrammar \
  --chat-template /srv/tenants/user/d88-probe/qwen38_chat_template.jinja
vllm serve Qwen/Qwen3-0.6B --served-model-name probe-b --host 127.0.0.1 --port 8091 \
  --gpu-memory-utilization 0.10 --max-model-len 4096 --max-num-seqs 4 --enforce-eager \
  --structured-outputs-config.backend xgrammar --reasoning-parser qwen3
```

- `probe-a`의 채팅 템플릿은 `Qwen/Qwen3.8-27B@1d4bf0f`의 `chat_template.jinja`다(sha256 `c3cf9e34…a81041`, 양쪽 대조).
  PR #297의 Auditor가 쓰는 템플릿을 0.6B 가중치 위에 얹었다 — 예외 문구는 템플릿이 만들고 가중치와 무관하다.
- 요청은 전부 `OpenAICompatModelClient.complete`로 보냈다. `role=auditor`, 스키마 `{"ok": bool}`, `temperature 0.2`,
  `top_p 0.95`, `seed 0`, `retries 0`이다. 출력 상한은 경우마다 다르다(아래 표). 짧은 프롬프트와 긴 프롬프트(`"word " × 6000`)
  두 가지를 썼다.
- 같은 payload를 urllib로 한 번 더 보내 자르지 않은 본문도 받았다.

| 경우 | 서버 | 어댑터 인자 | 프롬프트 |
| --- | --- | --- | --- |
| 정상 요청 | a | 출력 상한 64 | 짧음 |
| 컨텍스트 초과 | a | 출력 상한 64 | 긺 |
| 출력 상한 > 창 | a | 출력 상한 **8192** | 짧음 |
| 사고 예산 + parser 없음 | a | `extra_params={"thinking_token_budget": 256}` | 짧음 |
| 템플릿이 모르는 `reasoning_effort` | a | `reasoning_effort="high"` | 짧음 |
| 요청 본문 검증 | a | `reasoning_effort="bogus"` | 짧음 |
| 사고 예산 + V2 러너 | b | `extra_params={"thinking_token_budget": 256}` | 짧음 |

### 결과

위 §1 표다. 정상 요청의 응답 본문은 `{"ok": true}`였다(구조화 출력 경로가 선다).

### 근거 로그

**어댑터가 받은 `ModelGatewayError.detail` 원문**(경우마다 이름 한 줄 + 원문 한 줄, `probe_400.json`에서 옮겼다).
본문이 전부 512자 미만이라 `detail`이 자른 곳이 없다. 요청 본문 검증 하나만 자르지 않은 본문과 한 글자가 다르다 —
어댑터가 공백을 접어 `\n  {`가 `\n {`가 됐다(`truncate_detail`).

```
context_overflow
{"error":{"message":"This model's maximum context length is 4096 tokens. However, you requested 64 output tokens and your prompt contains at least 4033 input tokens, for a total of at least 4097 tokens. Please reduce the length of the input prompt or the number of requested output tokens. (parameter=input_tokens, value=4033)","type":"BadRequestError","param":"input_tokens","code":400}}
output_cap_over_window
{"error":{"message":"max_tokens=8192 cannot be greater than max_model_len=max_total_tokens=4096. Please request fewer output tokens. (parameter=max_tokens, value=8192)","type":"BadRequestError","param":"max_tokens","code":400}}
thinking_budget_without_parser
{"error":{"message":"thinking_token_budget is set but reasoning_config is not configured. Please set --reasoning-parser and/or --reasoning-config to use thinking_token_budget.","type":"BadRequestError","param":null,"code":400}}
template_rejects_reasoning_effort
{"error":{"message":"Unexpected reasoning effort high. Supported types are xhigh (default), medium, and low.","type":"BadRequestError","param":null,"code":400}}
request_validation
{"error":{"message":"1 validation error:\n {'type': 'literal_error', 'loc': ('body', 'reasoning_effort'), 'msg': \"Input should be 'none', 'minimal', 'low', 'medium', 'high', 'xhigh' or 'max'\", 'input': 'bogus', 'ctx': {'expected': \"'none', 'minimal', 'low', 'medium', 'high', 'xhigh' or 'max'\"}}","type":"Bad Request","param":"body.reasoning_effort","code":400}}
thinking_budget_with_parser
{"error":{"message":"thinking_token_budget is not yet supported by the V2 model runner. Run vLLM with VLLM_USE_V2_MODEL_RUNNER=0 to use thinking_token_budget.","type":"BadRequestError","param":null,"code":400}}
```

**서버 로그**(`probe-a.log`·`probe-b.log`).

```
(EngineCore pid=2156408) INFO 09-26 17:59:00 [gpu_worker.py:385] Using V2 Model Runner
(APIServer pid=2156220) ERROR 09-26 17:59:53 [hf.py:786] jinja2.exceptions.TemplateError: Unexpected reasoning effort high. Supported types are xhigh (default), medium, and low.
(APIServer pid=2156956) WARNING 09-26 17:59:22 [vllm.py:2267] Model Runner V2 does not yet support the thinking_token_budget request parameter. Set VLLM_USE_V2_MODEL_RUNNER=0 if this is required.
(EngineCore pid=2157129) INFO 09-26 17:59:29 [gpu_worker.py:385] Using V2 Model Runner
```

### 해석

**따라 나오는 것**

- **크기 문구는 컨텍스트 초과에만 실렸다.** 나머지 다섯 본문에는 `maximum context length`가 없다. PR #298의 분류는
  이 여섯 응답에서 소스 판독과 같은 답을 냈다.
- **요청 본문 검증은 400이다.** `type`도 `BadRequestError`가 아니라 `Bad Request`다(Starlette 문구). 러너가
  「요청 모양 = 422」로 가르던 전제(PR #298이 고친 `_DROPPABLE_STATUS` 주석)는 이 서버에서 틀렸다.
- **Qwen3-0.6B는 V2 러너로 돈다**(「Using V2 Model Runner」). 그래서 parser를 걸어도 사고 예산 요청은 거절된다.
  서버가 그 사실을 기동 때 경고로 먼저 알린다.
- **어댑터는 본문을 그대로 넘긴다.** 여섯 경우 전부 `detail`이 본문 전체였다. 가장 긴 것이 388자다.

**따라 나오지 않는 것**

- **`qwen3.8-27b`로는 안 쟀다.** 우리 장비에서 띄운 적이 없고 가중치도 없다. 템플릿 예외는 그 템플릿을 0.6B 위에
  얹어 냈다. 사고 예산이 실모델(V1 러너 여부 포함)에서 어떻게 되는지는 이 실험이 말하지 않는다.
- **표본은 경우당 한 건이다.** 같은 요청을 되풀이한 흔들림은 안 봤다. 다만 이 거절들은 생성 전에 난다.
- 이 실험은 러너를 돌리지 않았다 — run이 멈추는지는 `tests/test_pipeline_e2e.py`의 D88 절(Fake 모델)이 본다.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 없음 | — | — |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 글자 상한 초과(`param: input_text`, `vllm/renderers/params.py:352-363`) | 긴 프롬프트는 토큰 검사(`param: input_tokens`)에서 걸렸고, 글자 검사를 밟게 하는 입력은 안 만들었다 | 글자 상한(입력 몫 × 토크나이저의 토큰당 최대 글자 수, `params.py:348`)을 넘는 프롬프트를 보낸다 |
| 422(`VLLMUnprocessableEntityError`) | 우리 요청이 밟는 경로를 못 찾았다(미디어 URL 등) | 해당 없음 — 하네스 요청은 텍스트뿐이다 |
| 실모델 `qwen3.8-27b`의 사고 예산·템플릿 경로 | 가중치가 없고 띄운 적이 없다 | PR #297의 기동 조건으로 띄워 같은 프로브를 돌린다 |
