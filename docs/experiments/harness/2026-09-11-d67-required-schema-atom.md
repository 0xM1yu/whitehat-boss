# D67 — 스키마 required가 Auditor의 `path`·`evidence_refs` 채움을 가르는가 — ATOM

> **결론 둘.** ① **[D67](../../open-questions/D/D67.md)의 갈래 ②가 원인이다** — 스키마가 빈
> 리스트를 허용하는 동안 모델은 두 필드를 **20/20에서 비웠고**, `required`로 올리자 성공한
> **13/13에서 채웠다.** ② 그 전환의 대가로 [D46](../../open-questions/D/D46.md)의 **공백 폭주가
> 터지는데, 서버 옵션 `disable_any_whitespace` 하나로 사라진다** — 그 옵션은 D46 등재 이래
> **한 번도 안 재본 선택지 (a)**다.
>
> ⚠ **이 실험이 답하지 않는 것.** D67의 나머지 갈래 둘(①프롬프트가 약하다 ③모델의 성질)은
> 그대로 열려 있다. ②가 참인 것이 ①·③을 배제하지 않는다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **미기재** — 프로브 종료 시각을 따로 안 찍었다. 앵커는 서버 기동 로그다: 조건 A `2026-09-11 14:24:08 KST` · 조건 C `15:18:24 KST` · 조건 D는 그 뒤(로그에 기동 시각 있음) |
| 실제 서빙 모델 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` (`--served-model-name qwen3-coder-30b-a3b`는 별칭) |
| `repo_commit` | 조건 A의 1회차만 `2ed9a98`, 나머지 전부 `2ef2c7d`. **팔 판정 코드는 두 커밋 사이에서 안 바뀌었다** — `2ef2c7d`가 더한 것은 `_diagnose` 하나이고 `_run_arm`·`_schema_for`는 동일하다 |
| `run_id` | **없다.** `cli run`을 안 돌렸다. 이 실험은 하네스의 어댑터만 쓰고 오케스트레이터를 안 지난다 |
| run 산출물 경로·보존 여부 | **넷 중 둘만 남았다.** `/srv/tenants/user/whitehat-boss/d67_probe.json`(조건 B) · `d67_probe_ws_off.json`(C) — 보존됨. ⚠ **조건 A의 JSON은 없다** — 조건 B가 같은 `--out d67_probe.json`으로 덮었다. ⚠ **조건 D의 JSON도 없다** — 조작자가 `min_items` 진단 중에 중단해서(§3.4) 스크립트가 파일을 쓰기 전에 죽었다. **A와 D는 콘솔 출력만 남았고 아래 §3.1·§3.4가 그것이다** — 이 보고서가 그 둘의 유일한 기록이다 |
| 입력 프롬프트 | `/srv/tenants/user/artifacts_20260904/run_20260904_0001/prompts/0002_auditor.md` — **`run_20260904_0001`이 실제로 보낸 원문**(`prompt_version: v1` · `stage: 5`) |
| `tool_runner` | 해당 없음 (도구를 안 돌렸다) |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 |
| `--gpu-memory-utilization` | `0.30` — **분모는 물리 통합메모리 119.7 GiB**(기동 시점 free 110.53 GiB, 요청 35.91 GiB) |
| 동시에 떠 있던 다른 서버 | **없다.** 조건마다 하나씩 띄우고 다음 조건 전에 `pkill`했다 |
| 원문 산출물 보존 위치 | 위 JSON **둘**(B·C) + 아래 서버 로그 넷. **A·D는 이 보고서가 유일한 기록이다** |
| 서빙 서버 stdout | `~/vllm_logs/serve_auditor_8002_ws_default.log`(A·B) · `_ws_off.log`(C) · `_xgrammar_only.log`(D) · `_FAILED_ninja_path.log`(§4 벽 1). **ATOM 홈에만 있고 저장소에 없다** |
| vLLM · 환경 | `0.27.1` · arch `sm121` · venv `/srv/tenants/user/venv-probe` (Python 3.12.3) |

## 1. 요약

**경로 성립 판정이다. 성능을 재지 않았다.**

| 항목 | 결과 |
| --- | --- |
| `stock` 스키마에서 두 필드 채움 | **0 / 20** (네 조건 전부) |
| `required`·`min_items`에서 채움 | **13 / 13** (성공한 호출 전부) |
| 사실 블록에 없는 `evidence_refs` ID | **0 / 13** — 강제해도 환각하지 않았다 |
| `required` JSON 유효율 — `disable_any_whitespace=False` | **5 / 20** (조건 A·B·D 합) |
| `required` JSON 유효율 — `disable_any_whitespace=True` | **5 / 5** · 진단 **10 / 10** |
| 공백 폭주 시 꼬리 공백 | **12,849 ~ 13,199자** · 전부 `output_tokens == max_tokens` |
| 정상 응답 길이 | **332 ~ 522 토큰** — 상한 4,096의 **1/8** |

## 2. 실험 환경

| | |
| --- | --- |
| 장비 | NVIDIA DGX Spark (aarch64, sm121), 통합메모리 119.7 GiB |
| 커널 경로 | attention `FLASHINFER` · NVFP4 GEMM `FlashInferCutlassNvFp4LinearKernel` · MoE `FLASHINFER_CUTLASS` |
| KV cache | 306,608 토큰 / 14.04 GiB. 가중치+non-torch 19.27 GiB · peak activation 2.61 · CUDAGraph 2.13 |
| 하네스가 보낸 샘플링 | `temperature 0.2` · `top_p 0.95` · `seed 20260901` · `max_tokens 4096` (`config/budgets.yaml`) |
| **서버가 정한 샘플링** | `top_k 20` · `repetition_penalty 1.05` — `generation_config.json`이 정하고 **기록 어디에도 안 남는다.** [D29](../../open-questions/D/D29.md)의 실측 사례다 |

## 3. 실험 — 스키마 세 팔 × 서버 네 조건

### 목적

[D67](../../open-questions/D/D67.md) ⚠ ②가 *"제일 싸다"*고 적고 **"아직 안 돌았다"**로 남긴 실험이다 —
*"09-04와 같은 프롬프트를 쓰되 스키마만 required로 올려 재야 한다."*

### 조건

**프롬프트는 재구성하지 않고 재생했다.** D67이 *"이 run의 메타는 `repo_commit`이 미기재라
당시 요청에 실제로 무엇이 실렸는지는 재확인이 남는다"*고 적었으므로, 지금 코드로 다시
렌더하면 그 미기재가 변수로 남는다. `prompt_log.render()`가 넣어 둔
`<!-- prefix -->`·`<!-- body -->` 경계로 보존된 원문을 파싱해 **한 글자도 안 바꾸고** 보냈다.

사실 블록의 `evidence`는 **3건**(`ev_0001` · `ev_0024` · `ev_0025`). 환각 대조의 기준집합이다.

**스키마 세 팔** — `Finding.model_json_schema()`에서 출발해 그 팔의 차이만 얹는다:

| 팔 | 차이 |
| --- | --- |
| `stock` | 없음. `path`·`evidence_refs`가 `default_factory=list`라 **optional**이다 |
| `required` | 두 필드를 `required` 배열에 넣는다 |
| `min_items` | `required` + 두 필드에 `minItems: 1` |

**서버 네 조건** — 팔은 그대로 두고 `vllm serve` 인자만 바꾼다:

| 조건 | `backend` | `disable_any_whitespace` |
| --- | --- | --- |
| **A** (1회차) | `auto` | `False` |
| **B** (2회차, 같은 서버) | `auto` | `False` |
| **C** | `xgrammar` | **`True`** |
| **D** | `xgrammar` | `False` |

### 3.1 결과 — 팔 × 조건

| 조건 | `stock` | `required` | `min_items` |
| --- | --- | --- | --- |
| A `auto` · ws=False | **5/5** (채움 0/5) | 2/5 (채움 2/2) | 2/5 (채움 2/2) |
| B `auto` · ws=False | **5/5** (채움 0/5) | **0/5** | 1/5 (채움 1/1) |
| C `xgrammar` · ws=**True** | **5/5** (채움 0/5) | **5/5** (채움 **5/5**) | **5/5** (채움 **5/5**) |
| D `xgrammar` · ws=False | **5/5** (채움 0/5) | 3/5 (채움 3/3) | 3/5 (채움 3/3) |

「채움」은 `path`·`evidence_refs` **둘 다** 비어 있지 않은 호출 수다. 미상 ID는 **전 조건 0**이다.

⚠ **A와 B는 같은 서버·같은 인자인데 결과가 다르다** (2/5 → 0/5). **n=5로는 참값을 못 좁힌다** —
「40%」를 결과로 적으면 다음 회차가 바로 반증한다. 이 표에서 읽을 것은 비율이 아니라
**조건 C만 전 팔 5/5**라는 것이다.

### 3.2 근거 로그 — 조건 B의 실패 (공백 폭주)

```
  required[0] json=False out_tok=4096/4096 cap=True ws_tail=13066
      err : Expecting ',' delimiter: line 5638 column 1 (char 14259)
      tail: ' \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n  \n  \n\n'
  required[1] json=False out_tok=4096/4096 cap=True ws_tail=13041
      err : Expecting ',' delimiter: line 5627 column 1 (char 14294)
  required[2] json=False out_tok=4096/4096 cap=True ws_tail=13167
      err : Expecting ',' delimiter: line 5680 column 1 (char 14229)
  required[3] json=False out_tok=4096/4096 cap=True ws_tail=13057
      err : Expecting ',' delimiter: line 5636 column 1 (char 13967)
  required[4] json=False out_tok=4096/4096 cap=True ws_tail=13104
      err : Expecting ',' delimiter: line 5654 column 1 (char 13956)
  min_items[0] json=True out_tok=522/4096 cap=False ws_tail=0
  min_items[1] json=False out_tok=4096/4096 cap=True ws_tail=12873
  min_items[2] json=False out_tok=4096/4096 cap=True ws_tail=13026
  min_items[3] json=False out_tok=4096/4096 cap=True ws_tail=12849
  min_items[4] json=False out_tok=4096/4096 cap=True ws_tail=13199
```

### 3.3 근거 로그 — 조건 C (`disable_any_whitespace=True`)

기동 로그가 실린 값을 확인한다 — gpt-oss에서 vLLM이 인자를 스스로 채운 전례가 있어 확인이 필요하다:

```
StructuredOutputsConfig(backend='xgrammar', disable_any_whitespace=True, disable_additional_properties=False, reasoning_parser='', reasoning_parser_plugin='', enable_in_reasoning=False)
```

```
  required[0] json=True out_tok=430/4096 cap=False ws_tail=0
  required[1] json=True out_tok=363/4096 cap=False ws_tail=0
  required[2] json=True out_tok=437/4096 cap=False ws_tail=0
  required[3] json=True out_tok=352/4096 cap=False ws_tail=0
  required[4] json=True out_tok=420/4096 cap=False ws_tail=0
  min_items[0] json=True out_tok=417/4096 cap=False ws_tail=0
  min_items[1] json=True out_tok=508/4096 cap=False ws_tail=0
  min_items[2] json=True out_tok=391/4096 cap=False ws_tail=0
  min_items[3] json=True out_tok=332/4096 cap=False ws_tail=0
  min_items[4] json=True out_tok=460/4096 cap=False ws_tail=0
```

### 3.4 근거 로그 — 조건 D (`backend=xgrammar`만, 귀속용)

```
StructuredOutputsConfig(backend='xgrammar', disable_any_whitespace=False, ...)
```

```
  required[0] json=True out_tok=460/4096 cap=False ws_tail=0
  required[1] json=False out_tok=4096/4096 cap=True ws_tail=13097
      err : Expecting ',' delimiter: line 5653 column 1 (char 13923)
  required[2] json=False out_tok=4096/4096 cap=True ws_tail=12998
  required[3] json=False out_tok=4096/4096 cap=True ws_tail=12938
  required[4] json=False out_tok=4096/4096 cap=True ws_tail=12992
```

⚠ **조건 D의 `min_items` 진단은 안 쟀다** — 조작자가 `required` 진단을 보고 중단했다(Ctrl+C).
팔 판정(3/5)은 그 전에 끝났으므로 §3.1의 값은 유효하고, **진단만 없다.**

### 해석

**① 스키마 optionality가 생략의 원인이다.** `stock`은 네 조건 전부에서 **5/5 성공하면서 채움
0/5**다. 같은 서버·같은 프롬프트에서 `required`만 올리면 성공한 호출이 **전부** 채운다.
09-04의 관측(150건 중 `path` 100% 빔 · `evidence_refs` 148건 빔)이 **이 조건에서 재현됐고**,
그 조건이 스키마 하나로 뒤집힌다.

**② 강제가 환각을 만들지 않았다.** `min_items: 1`로 **빈 리스트를 금지**했는데도 돌아온
`evidence_refs`는 전부 사실 블록의 3건 안에 있었다(미상 0/13). v1 규칙(*"사실 블록의
`evidence`에 없는 근거 ID를 만들어 내지 않는다"*)이 지켜졌다. **D67 선택지 (d)를 걱정하게
했던 모양**(값은 채워지는데 그 값의 뜻이 없다 — D23 계열)이 **스키마 강제 경로에서는
관측되지 않았다.**

**③ 실패는 절단이 아니라 공백 폭주다.** 실패한 응답은 전부 `output_tokens == max_tokens`지만,
**성공한 응답은 332~522토큰**이다. 내용이 상한의 1/8이므로 **상한이 모자라서 잘린 것이
아니다** — 꼬리 12,849~13,199자가 전부 `\n  \n\n  ` 반복이고 파서는 그 공백이 시작되는
자리(char ≈ 13,850~14,310)에서 끊긴다. [D42](../../open-questions/D/D42.md)가
*"`output_max`를 키우면 더 늦게 잘릴 뿐"*이라 적은 것의 직접 증거이고, **`output_max` 조정은
이 실패의 처방이 아니다.**

**④ 원인은 `disable_any_whitespace` 단독이다.** 조건 C와 D가 `backend='xgrammar'`로 같고 그
옵션만 다른데, **D에서 폭주가 그대로 재발한다**(진단 4/5, `ws_tail ≈ 13,000` 동일 모양).
그러므로 C의 개선을 `backend` 명시에 돌릴 수 없다. **[D46](../../open-questions/D/D46.md)
선택지 (a)가 이 모양에는 듣는다.**

**⑤ 따라 나오지 않는 것.**

- **「D46이 닫힌다」로 안 읽는다.** 이 실험이 본 것은 **한 프롬프트 · 한 모델 · 한 역할**이다.
  D46의 관측 범위는 scout(LIGHT) 64건 · auditor · critic에 걸쳐 있고 *"입력 의존"*이라 적는다.
  여기서 보인 것은 **스키마 엄격도도 손잡이라는 것**이지 그것이 유일한 손잡이라는 것이 아니다.
- **「D67이 닫힌다」로 안 읽는다.** 갈래 ②가 참인 것이 ①(프롬프트가 약하다)·③(모델의 성질)을
  배제하지 않는다. 같은 스키마를 다른 모델에 보내면 ③이 갈린다.
- **비율을 결론으로 안 쓴다.** 조건 A·B가 같은 인자에서 2/5와 0/5를 냈다. n=5다.
- **이 run은 비교표 밖이다.** `cli run`을 안 돌렸고 `run_id`가 없다.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 엔진 초기화가 `FileNotFoundError: 'ninja'`로 죽었다 | flashinfer가 NVFP4 GEMM 커널을 JIT 빌드하는데(`flashinfer_mm_fp4` → `get_gemm_sm120_module_cutlass_fp4` → `run_ninja`) `ninja`가 PATH에 없었다. **없는 게 아니라 안 보였다** — `/srv/tenants/user/venv-probe/bin/ninja`에 있고 `nvcc`는 `/usr/local/cuda/bin/nvcc`에 있다 | 기동 시 `PATH=/srv/tenants/user/venv-probe/bin:/usr/local/cuda/bin:$PATH` |
| `--structured-outputs-config.disable_any_whitespace true`가 기동을 거부했다 | `disable_any_whitespace is only supported for xgrammar and guidance backends` — `backend`가 `auto`였다 | `--structured-outputs-config.backend xgrammar`를 함께 준다. **그래서 조건 D(귀속용)가 필요해졌다** |
| `auto`가 무엇으로 풀렸는지 못 봤다 | vLLM 0.27.1이 해석된 structured output 백엔드를 로그에 안 남긴다(`grep` 0건) | **못 풀었다.** 조건 D를 따로 돌려 `xgrammar` 명시의 효과를 분리했다 |
| 조작자가 프로브를 서버 기동 30초 뒤에 돌려 전량 `Connection refused` | 가중치 로딩에 2.5분이 걸린다 | `until curl -s -f …/v1/models; do sleep 10; done` |
| **조건 D의 산출물 JSON을 통째로 잃었다** | 프로브가 **마지막에 한 번** 파일을 쓴다. 진단 중 `Ctrl+C`가 그 앞에서 죽여 팔 판정까지 함께 사라졌다 | **미해결.** 콘솔 출력을 이 보고서에 옮겨 적는 것으로 메웠다 — 다음 회차 전에 고칠 자리다(§7 5번) |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 조건 D의 `min_items` 진단 | 조작자가 중단했다. **그 중단이 조건 D의 JSON도 함께 날렸다**(§4) — 팔 판정 3/5는 콘솔에만 있다 | 조건 D를 다시 띄워 같은 명령. 프로브를 고친 뒤가 낫다 |
| 처리량·지연 | **이 실험의 범위 밖이다.** 경로 성립만 봤다 | `cli bench --roles auditor` |
| 다른 프롬프트에서의 재현 | 프롬프트를 **하나**만 썼다(`0002_auditor.md`). 여럿을 섞으면 팔 간 차이와 프롬프트 간 차이가 안 갈린다 | `--prompt-index`를 바꿔 반복. auditor 1차 프롬프트가 **152건** 보존돼 있다 |
| 다른 모델에서의 재현 | D67 갈래 ③(모델의 성질)을 가르는 실험이고 이 실험의 범위가 아니다 | 같은 프로브를 다른 auditor 모델에 |
| `disable_any_whitespace`가 **다른 역할**(scout·critic)에서도 듣는가 | auditor만 봤다 | D46의 관측 범위대로 세 역할 |
| `guidance` 백엔드 | 안 해봤다. [2026-09-09 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md) §3.3이 다른 모델에서 5/5 HTTP 500을 봤다 | 같은 프로브를 그 백엔드로 |
| 서버가 결정론적인가 | 하네스가 `seed 20260901`을 싣는데도 같은 payload 5회가 다른 결과를 냈다. **원인을 안 갈랐다** | 별건 — `engineering-guide.md` §5 재현성 |

## 6. 반증 조건

- **「스키마 optionality가 원인이다」가 틀렸다면**: `required` 조건에서도 두 필드가 비어야
  한다. **13/13에서 채웠으므로 이 방향은 살아 있다.** ⚠ 다만 **한 프롬프트**의 결과다 —
  그 프롬프트가 유난히 채우기 쉬운 것이면 좁혀야 한다.
- **「`disable_any_whitespace`가 원인이다」가 틀렸다면**: 조건 D(`xgrammar`만)에서도 폭주가
  사라져야 한다. **재발했으므로(4/5) 이 귀속은 살아 있다.**
- **「강제가 환각을 만들지 않는다」가 틀렸다면**: `min_items` 팔에서 사실 블록 밖 ID가 나와야
  한다. **0/13이다.** ⚠ **기준집합이 3건뿐이라** 모델이 고를 수 있는 값이 좁았다는 설명도
  배제되지 않는다 — evidence가 많이 실린 프롬프트에서 다시 봐야 한다.

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | **D67 등재문 갱신** — ② 확정, 선택지에 「스키마 required + 서버 `disable_any_whitespace`」를 더한다 | 이 보고서와 함께 |
| 2 | **D46 등재문 갱신** — 선택지 (a) 첫 실측. 그리고 **D42 인용 정정**(§7.1) | 이 보고서와 함께 |
| 3 | `gpt-oss-20b`의 R8에 같은 옵션이 걸리는가 | 그 레시피는 이미 `backend xgrammar`다. 옵션 한 줄이면 된다 |
| 4 | 프롬프트 여럿·모델 여럿으로 범위 넓히기 | §5 |
| 5 | **프로브가 중단에 산출물을 잃는다** | `--out`을 마지막에 한 번 쓴다. 팔이 끝날 때마다 쓰거나 `finally`에서 쓰면 이번 손실이 안 난다 |
| 6 | 서버 비결정성 | §5 마지막 줄. 별건 |

### 7.1 D46이 D42를 잘못 인용한다

[D46](../../open-questions/D/D46.md)이 선택지 (a)를 접는 근거로 이렇게 적는다 —
*"D42 자신이 「서버 옵션(`disable_any_whitespace`)으로 닫히지 않는다」고 적어 뒀다."*

[D42](../../open-questions/D/D42.md)의 원문은 **그 항목 자신**에 대한 진술이고, 오히려 그
옵션이 무엇을 하는지를 적고 있다:

> **이 항목은** 서버 옵션(`disable_any_whitespace`)으로 닫히지 않는다 — 그것은 **공백 폭주 한
> 종류만 막는** 서버측 우회이고 하네스의 계약이 아니다

D42는 *"하네스가 `output_max`를 안 보낸다"*이고, 그 계약 결함이 서버 옵션으로 안 닫힌다는
말이다. **「공백 폭주가 그 옵션으로 안 막힌다」가 아니다.** 요약이 원문보다 세진 자리이고
([CLAUDE.md](../../../CLAUDE.md) §2.2), 그 결과 **한 번도 안 재본 선택지가 재본 것처럼 읽혔다.**

## 8. 정리 상태

- **서빙 서버**: **내렸다.** `pkill -f "vllm serve"` 후 8002에 프로세스 없음.
  ⚠ **GPU 반환은 수치로 확인하지 못했다** — `nvidia-smi --query-gpu=memory.used`가 이 장비에서
  `[N/A]`를 준다(통합메모리. `mem_gb_ctx8k`가 registry 전반에서 0인 것과 같은 원인이다)
- **임시 설정·프로필**: 없음. `config/`를 안 건드렸다
- **저장소 변경**: `tools/probe/d67_required_schema.py`(PR #175) · 이 보고서

## 9. 부록 — 재현 절차

**환경변수 포함 전체 원문이다.** 조건 A·B는 마지막 두 인자가 없고, 조건 D는
`--structured-outputs-config.disable_any_whitespace true`가 없다.

```bash
SNAP=/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048
PATH=/srv/tenants/user/venv-probe/bin:/usr/local/cuda/bin:$PATH \
VLLM_USE_DEEP_GEMM=0 \
setsid nohup /srv/tenants/user/venv-probe/bin/vllm serve "$SNAP" \
  --port 8002 --served-model-name qwen3-coder-30b-a3b \
  --max-model-len 32768 --gpu-memory-utilization 0.30 \
  --structured-outputs-config.backend xgrammar \
  --structured-outputs-config.disable_any_whitespace true \
  > ~/vllm_logs/serve_auditor_8002_ws_off.log 2>&1 &
```

**`PATH`가 인자의 일부다** — 빼면 §4의 첫 벽을 그대로 밟는다.

뜰 때까지 기다린 뒤(가중치 로딩 약 2.5분):

```bash
until curl -s -f http://127.0.0.1:8002/v1/models >/dev/null 2>&1; do sleep 10; done
```

```bash
cd /srv/tenants/user/whitehat-boss && /srv/tenants/user/venv-probe/bin/python tools/probe/d67_required_schema.py --run-dir /srv/tenants/user/artifacts_20260904/run_20260904_0001 --profile local_balanced --trials 5 --diagnose required min_items --out d67_probe_ws_off.json
```

**기동 로그에서 실린 값을 확인한다** — 인자를 줬다고 실렸다고 읽지 않는다:

```bash
grep -o "StructuredOutputsConfig([^)]*)" ~/vllm_logs/serve_auditor_8002_ws_off.log | head -1
```
