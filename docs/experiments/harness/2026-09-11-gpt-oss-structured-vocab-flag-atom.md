# `gpt-oss-20b` 구조화 출력 — 어휘 · 플래그 · enum 순서 · 속성 순서 · 백엔드 (9팔) — ATOM

> **한 줄:** 아홉 팔 전부 무너진다. **어휘도, 플래그도, enum 위치도, 속성 순서도 원인이 아니다.**
> 같은 JSON 안에서 **`reason`은 "clear use-after-free"라고 쓰고 `verdict`는 `safe`를 낸다.**

> **이 실험이 닫은 것.** [2026-09-09 온보딩 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md) §5의
> 미측정 셋 중 **둘**(`reasoning_parser` 명시 · `outlines`)과, §7 후속 2가 *"제일 싸다"*고 적은
> **실제 `Decision` 스키마 반복**. **셋 다 부정이다.**

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | 첫 팔 `2026-09-11 20:14:28 KST`. **나머지 팔의 시각은 JSONL의 `recorded_at`에 있다** — 옮겨 적지 않는다(옮겨 적으면 두 곳이 갈린다) |
| 실제 서빙 모델 | `openai/gpt-oss-20b` (`--served-model-name gpt-oss-20b`는 별칭). ⚠ **revision을 이 세션에서 재확인하지 않았다** — registry(`config/models.yaml`)가 `@6cee5e81ee83917806bbde320786a8fb61efebee`를 들고 HF 캐시에서 해석됐다 |
| `repo_commit` | `089366c` (ATOM 체크아웃, 브랜치 `probe/d67-and-gpt-oss`) |
| `run_id` | **없다.** `cli run`을 안 돌렸다 — 이 모델은 아직 어느 프로필의 역할도 아니다 |
| run 산출물 경로·보존 여부 | 해당 없음 |
| `tool_runner` | 해당 없음 (모델 호출만) |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 |
| `--gpu-memory-utilization` | `0.30` (분모 = 물리 통합메모리 **119.7 GiB**) |
| 동시에 떠 있던 다른 서버 | **없다.** 8006 하나뿐이고 **네 번 재기동**했다(조건별로) |
| 원문 산출물 보존 위치 | **`/srv/tenants/user/gpt_oss_structured.jsonl` (9줄, 팔당 1줄).** 요청·응답 전문 포함 |
| 서빙 서버 stdout | `~/vllm_logs/gptoss_8006_xgrammar.log` · `_rparser.log` · `_order.log` · `_outlines.log` |

⚠ **`run_id`가 없으므로 이 결과는 비교표에 들어가지 않는다**([evaluation.md](../../evaluation.md) §6).
프로브는 `curl`과 같은 자리이고, [R8](../../open-questions/R/R8.md)의 닫히는 조건 1은
`cli bench`가 답한다.

⚠ **라벨 하나가 서버를 잘못 가리킨다.** 마지막 팔이 `rparser-describe`로 기록됐는데
**실제로는 `outlines` 서버에서 돌았다**(같은 스크립트를 재사용했고 `--label`을 안 바꿨다).
JSONL에 같은 라벨이 **두 줄** 있고 뒤가 outlines다 — `serve_cmd`도 앞 팔의 값이라
**라벨과 `serve_cmd` 둘 다로는 못 가른다. 순서로 가른다.** 아래 표가 정본이다.

## 1. 요약

**아홉 팔. 축 다섯을 갈랐다.**

| # | 팔 | 갈린 축 | HTTP 200 | JSON 유효 | **verdict 정답** |
| --- | --- | --- | --- | --- | --- |
| 1 | `xgrammar-report33` | 기준선 (§3.3 어휘) | 5/5 | 5/5 | 1/5 |
| 2 | `xgrammar-decision` | 실제 `Decision` 어휘 | 5/5 | 5/5 | 1/5 |
| 3 | `rparser-report33` | **`reasoning_parser` 명시** | 5/5 | 5/5 | 1/5 |
| 4 | `rparser-decision` | 위 둘 동시 | 5/5 | 5/5 | 1/5 |
| 5 | `rparser-describe` | **서술 어휘** `vulnerable`/`safe` | 5/5 | 5/5 | **0/5** |
| 6 | `rparser-flipped` | **enum 순서 뒤집음** | 5/5 | 5/5 | 1/5 |
| 7 | `rparser-reason-first` | **속성 순서** (`reason` 먼저) | 5/5 | **3/5** | **0/3** |
| 8 | `outlines-decision` | **백엔드 `outlines`** | **0/5** | — | — |
| 9 | (라벨은 `rparser-describe`) | **`outlines` + 서술 어휘** | **0/5** | — | — |

| 항목 | 결과 |
| --- | --- |
| **자유서술 대조군** | **아홉 팔 전부 3/3 — 27/27** |
| **「문제 없음」 쪽 쏠림** | 유효 33시행 중 **28 (85%)** |
| `reasoning_content` | **전 팔 비었다.** `completion_tokens`는 983~2,898 |
| `outlines`·`guidance` | **둘 다 HTTP 500** — 쓸 수 있는 백엔드는 `xgrammar` 하나뿐이다 |

## 2. 실험 환경

- ATOM (NVIDIA DGX Spark, aarch64, sm_121), vLLM 0.27.1
- venv `/srv/tenants/user/venv-probe`, 저장소 `/srv/tenants/user/whitehat-boss`
- 프로브 `tools/probe/gpt_oss_structured.py` (PR #175). **이 실험이 그 프로브의 첫 실행이다**
- 프로브가 팔 간 상수를 잡는다 — 같은 프롬프트(UAF 한 케이스), `max_tokens=3000`,
  `reasoning_effort=high`, `trials=5`. 기동 인자 원문은 [§11](#11-부록--재현-절차)

## 3. 실험 1 — 2×2 (스키마 어휘 × `reasoning_parser`)

### 목적

**경로 성립 판정이다.** 09-09 §3.3의 실패가 ① 스키마 어휘 때문인지
② `reasoning_parser`를 CLI로 안 줘서인지를 가른다.

### 결과

| 팔 | `reasoning_parser` | verdict 시퀀스 |
| --- | --- | --- |
| `xgrammar-report33` | 안 줌 | `rejected, rejected, rejected, rejected, upheld` |
| `rparser-report33` | **명시** | `rejected, rejected, rejected, rejected, upheld` |
| `xgrammar-decision` | 안 줌 | `rebut, rebut, rebut, uphold, rebut` |
| `rparser-decision` | **명시** | `rebut, rebut, rebut, uphold, rebut` |

### 근거 로그

```
[팔]    xgrammar-report33      verdict들 ['rejected','rejected','rejected','rejected','upheld']
[팔]    rparser-report33       verdict들 ['rejected','rejected','rejected','rejected','upheld']
[팔]    xgrammar-decision      verdict들 ['rebut','rebut','rebut','uphold','rebut']
[팔]    rparser-decision       verdict들 ['rebut','rebut','rebut','uphold','rebut']
```

### 해석

**`reasoning_parser` 가설이 기각됐다.** 09-09 §6이 *"확인법: 그 플래그를 명시하고 §3.3을
반복해 verdict가 0/5에서 움직이는지 본다"*고 적었다. **안 움직였다 — 순서까지 같다.**
같은 §2의 관측(*"EngineCore가 `openai_gptoss`로 채웠다"*)이 맞았다.

**어휘 가설도 기각됐다.** §7 후속 2가 *"어휘가 원인이면 serve 설정을 안 건드리고도
뒤집힌다"*고 적었는데 안 뒤집혔다.

⚠ **시퀀스 동일성은 따로 읽는다.** 이 조건에서 **재현성이 있다**는 뜻이고 「우연히 1/5」이
아니다. **다만 그 재현성의 기제(seed·캐시·결정성)는 이 실험이 안 갈랐다.**

## 4. 실험 2 — 서술 어휘

실험 1의 두 어휘는 **둘 다 「주장에 대한 판정」**이다. #160이 3/3을 낸
`vulnerable`/`safe`는 **「코드에 대한 서술」**이라 종류가 다르다.

**서버·프롬프트·`max_tokens`·JSON 스키마 이름(`Verdict`)까지 같고 `enum` 두 값만 다르다.**

| 팔 | verdict 시퀀스 | 정답 |
| --- | --- | --- |
| `rparser-describe` | `safe, safe, safe, safe, safe` | **0/5** |

### 해석

**어휘 가설이 완전히 기각된다.** 세 어휘 전부 실패했고 **서술 어휘가 제일 나쁘다.**
2값 enum에서 전패 확률은 `(1/2)^5 = 3.1%` `[계산]`이라 「헷갈린다」로 설명되지 않는다.

**그리고 `reason`이 맞는다** — 이것이 이 실험의 전환점이다.

```
2  1284 | The code clearly dereferences a pointer after it has been freed,
          which is a classic use-after-free scenario and results in undefined behaviour.
3  2898 | The code definitely uses memory after it has been freed. Passing a pointer to
          `strcpy` after a `free(p)` is undefined behaviour – a classic use-after-free bug.
```

**같은 JSON 객체 안에서 `reason`은 UAF라 쓰고 `verdict`는 `safe`다.**

## 5. 실험 3 — enum 순서 (위치 편향 ↔ 의미 편향)

### 목적

실험 1·2에서 모델이 고른 값이 **세 스키마 모두 index 1**이었고 정답은 모두 index 0이었다
(15시행 중 13이 index 1). **위치 때문인지 의미 때문인지 갈린다.**

### 조건

`describe`와 **`enum` 배열 순서만** 다르다 — `["safe", "vulnerable"]`. 정답이 **index 1**이 된다.

### 결과

| 팔 | 정답 위치 | verdict 시퀀스 | 정답 |
| --- | --- | --- | --- |
| `rparser-flipped` | **1** | `safe, safe, safe, vulnerable, safe` | 1/5 |

### 해석

**위치 편향이 기각됐다.** 정답을 index 1로 옮겼는데도 모델은 **`safe`(index 0)**를 4/5로
골랐다 — **위치가 아니라 의미를 따라간다.**

**남는 것은 「문제 없음」 쪽 쏠림이다.** 네 어휘에 걸쳐:

| 스키마 | 「문제 없음」 값 | 그 값의 index | 고른 비율 |
| --- | --- | --- | --- |
| `report33` ×2 | `rejected` | 1 | 4/5씩 |
| `decision` ×2 | `rebut` | 1 | 4/5씩 |
| `describe` | `safe` | 1 | 5/5 |
| `flipped` | `safe` | **0** | 4/5 |
| `reason_first` | `safe` | 1 | 3/3 |

**유효 33시행 중 28 (85%).** 위치와 무관하다.

## 6. 실험 4 — 속성 순서 (`reason`을 먼저)

### 목적

세 스키마 모두 `verdict`가 **첫 속성**이라 모델이 `reason`을 쓰기 전에 verdict 토큰을
낸다. **「추론 전에 커밋한다」가 원인인지** 가른다. 맞으면 처방은
`Decision` 필드 순서 한 줄이다.

### 조건

`describe`와 **속성·`required` 순서만** 다르다 — `reason` 먼저, `verdict` 나중.

### 결과

| 팔 | HTTP 200 | JSON 유효 | verdict 정답 |
| --- | --- | --- | --- |
| `rparser-reason-first` | 5/5 | **3/5** | **0/3** |

### 근거 로그 — **이 보고서에서 제일 중요한 다섯 줄**

```
1 json True  | safe | The code demonstrates a clear use-after-free. After the call to
                      `free(p)` the block that `p` points to is returned to the allocator;
                      the pointer still holds that address, but that…
2 json False | None | {"reason":"Yes, the snippet demonstrates a classic **use-after-free**
                      (which is undefined behaviour). After calling `free(p)` the pointer
                      `p` becomes a *dangling pointer* – it stil…            ← 잘림
3 json False | None | {"reason":"Yes. This snippet contains a use-after-free. After `free(p)`
                      the pointer `p` becomes a *dangling pointer*…           ← 잘림
4 json True  | safe | Yes. After calling free(p), the pointer still holds the address of a
                      region that has been returned to the heap allocator. Writing to that
                      address with strcpy("x")…
5 json True  | safe | analysis submitted
```

### 해석

**속성 순서도 기각됐다. 그리고 기각되는 방식이 결정적이다.**

시행 1·4에서 모델은 **완전하고 정확한 분석을 먼저 쓰고**(*"clear use-after-free"*, *"Yes.
After calling free(p)…"*) **그 직후에 `verdict: safe`를 낸다.** 「추론 전에 커밋한다」가
아니다 — **추론이 같은 객체 안 바로 앞에 있는데 enum 토큰이 그것과 모순된다.**

⚠ **부작용이 생겼다.** JSON 유효가 **5/5 → 3/5**로 떨어졌다. 무효 둘은 자유 텍스트 필드가
앞에 와서 길어지다 잘린 모양이다 — [D46](../../open-questions/D/D46.md) 공백/길이 폭주와
같은 계열로 **보이지만 그 귀속은 이 실험이 안 했다**(꼬리를 안 찍었다).

⚠ 시행 5의 `"analysis submitted"`는 **퇴화한 `reason`**이다. 모델이 그 필드를 내용이 아니라
**채널 표식**으로 쓴 경우이고, 이것도 한 건뿐이라 일반화하지 않는다.

## 7. 실험 5 — 백엔드 `outlines`

### 목적

09-09 §5의 미측정 마지막 항목. **처방이 갈리는 자리다** — 백엔드가 원인이면 서버 플래그
한 줄이고, 모델이면 코드 변경이다.

### 결과

| 팔 | HTTP 200 | 자유서술 |
| --- | --- | --- |
| `outlines-decision` | **0/5** | 3/3 |
| (라벨 `rparser-describe`, 실제 outlines) | **0/5** | 3/3 |

### 근거 로그

```
  HTTP 200        0/5
    ! HTTP 500: {"error": {"message": "Internal server error", "type": "InternalServerError", "param": null, "code": 500}}
    ! HTTP 500: (×5)
```

**서버는 정상 기동했고**(`/v1/models` 응답 OK) **자유서술은 3/3으로 돈다.**
500은 `response_format`을 실은 요청에서만 난다.

### 해석

**`outlines`는 이 모델·이 버전에서 쓸 수 없다.** 09-09의 `guidance`(5/5 HTTP 500)와 같은 자리다.

**그래서 구조화 출력으로 가는 백엔드 셋 중 둘이 막혔고, 남은 하나가 판정을 뒤집는다.**

⚠ **500의 사유는 안 갈랐다** — 서버 로그 레벨을 안 올렸다. 09-09도 `guidance` 500에 대해
같은 자리에서 멈췄다(§5). **두 번째 같은 모양이므로 다음에는 로그를 받는다.**

## 8. 아홉 팔이 공통으로 남긴 것

| 관측 | 값 |
| --- | --- |
| 자유서술 정답 | **27/27** (아홉 팔 × 3) |
| JSON 안의 `reason` | **맞는다** (실험 2·4의 원문) |
| enum 제약된 `verdict` | **85%가 「문제 없음」 쪽** |
| `reasoning_content` | **전 팔 비었다** |
| `completion_tokens` | 983~2,898 |
| `finish_reason` | `stop` (팔 7의 무효 둘 제외) |

**틀리는 것은 enum 토큰 하나다.** 자유 텍스트는 — 스키마 밖이든 스키마 안이든 — 맞는다.

⚠ **여기서 기제를 단정하지 않는다.** 「제약 디코딩이 enum 자리의 선택을 왜곡한다」가 살아
있는 설명이지만 **로짓도 마스크도 관측하지 않았다.** 본 것은 입력·출력 쌍뿐이다.

## 9. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 첫 요약 one-liner가 `KeyError: 'correct'` | `correct`는 팔 수준이 아니라 `structured_trials[*]`에 있다 | 시행을 순회해 세는 one-liner로 바꿨다(§11) |
| 세 어휘·두 순서를 재려면 프로브 수정이 필요 | `SCHEMAS`가 모듈 상수 | **고치지 않았다** — `importlib`로 불러와 키를 더하고 `main()`을 불렀다. 요청 조립 경로가 같아 팔 간 상수가 유지된다 |
| 마지막 팔의 라벨이 서버와 안 맞는다 | 같은 스크립트를 재사용하며 `--label`을 안 바꿨다 | **고치지 못했다** — 기록이 이미 났다. 메타에 적고 §1 표를 정본으로 삼는다. **다음 프로브는 `--label`을 서버 기동과 묶어야 한다** |

## 10. 미측정 항목과 사유

**못 잰 값을 0으로 채우지 않는다.**

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| **`enable_in_reasoning=True`** | **안 해봤다.** 09-09 §5가 남긴 셋 중 마지막 하나 | 그 값으로 재기동해 같은 팔을 돌린다 |
| **enum 제약을 뺀 `verdict`** | **안 해봤다 — 이 데이터가 새로 낸 항목이다** | `verdict`를 `enum` 없는 `string`으로 두고 사후 검증한다. **enum 제약 자체가 원인인지 가르는 가장 싼 실험** |
| **케이스 축** | 이 실험은 **UAF 한 케이스 ×5**다. #160은 서로 다른 3케이스 ×1 | 케이스를 바꿔 돌린다. **#160의 3/3을 설명할 남은 축이 이것 하나다** |
| **`outlines` 500의 사유** | 서버 로그 레벨을 안 올렸다 | 레벨을 올려 재현. 09-09의 `guidance` 500도 같은 자리에서 멈췄다 |
| **팔 7 무효 둘의 귀속** | 꼬리를 안 찍었다 | 그 시행의 `content` 꼬리를 본다. D67의 `disable_any_whitespace`가 듣는지도 함께 |
| **`reasoning_content`가 왜 비는가** | 안 갈랐다 | 로그 레벨을 올려 Harmony 채널 파싱을 본다 |
| **실제 Critic 계약에서의 정확도** | 프로브 프롬프트가 *"이 코드에 UAF가 있나"*인데 **실제 Critic은 Auditor의 주장·경로·근거를 받는다** | `prompts/critic/v1`의 렌더 결과로 재기 |
| Critic 반박 정확도(체크리스트 7번) | bench 범위 밖, 고정 반박 표본 필요 | 표본 확정 후([D4](../../open-questions/D/D4.md)) |

## 11. 반증 조건

- **「어휘가 원인이다」** → 이 실험이 기각했다. 네 어휘 전부 실패. 다시 서려면 **다섯째
  어휘**에서 뒤집혀야 한다.
- **「`reasoning_parser`가 원인이다」** → 기각. 시퀀스가 동일하다.
- **「enum 위치가 원인이다」** → 기각. 순서를 뒤집어도 의미를 따라갔다.
- **「속성 순서가 원인이다」** → 기각. `reason`을 먼저 쓰고도 반대 verdict를 냈다.
- **「`xgrammar` 구현이 원인이다」** → **이 실험으로는 못 가른다.** 다른 두 백엔드가 500이라
  **대조가 없다.** 이것이 남은 가장 큰 미확정이고, `enable_in_reasoning`이나 enum 제약 제거가
  간접적으로 답한다.
- **「모델은 멀쩡하다」가 틀렸다면**: 자유서술 27/27이 **한 케이스**에서 나왔다. 어려운
  표본에서 무너지면 이 문장을 좁혀야 한다.
- ⚠ **「#160이 틀렸다」로 읽지 않는다.** 3/3은 관측이고 이 실험이 뒤집지 않는다. 그 run이
  바꾼 축 넷 중 셋을 이 실험이 상수로 잡았고 어휘도 기각됐으므로 **남은 설명은 케이스다.**

## 12. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | **enum 제약을 뺀 `verdict`** | 미측정. **enum 제약 자체가 원인인지**를 가르는 가장 싼 실험이고, 이 데이터가 새로 낸 항목이다 |
| 2 | **(가) 2단 추출을 실제로 재기** | 미측정. **이 실험이 그 근거를 준다** — 자유 텍스트는 스키마 안에서도 맞는다. [ADR 0010](../../decisions/0010-harness-structure-and-model-roster.md) 「다시 열리는 조건」이 (나) 교체를 *"2단 추출로도 안 닫히면"*으로 걸었으므로 **(가)를 재기 전에는 (나)로 못 간다** |
| 3 | `enable_in_reasoning=True` | 미측정 (09-09 §5의 마지막 항목) |
| 4 | **케이스 축** | 미측정. #160의 3/3을 설명할 마지막 후보 |
| 5 | `outlines`·`guidance` 500의 사유 | 두 번째 같은 모양이다. 다음엔 로그를 받는다 |
| 6 | **실제 Critic 프롬프트로 재기** | 이 실험의 프롬프트는 Critic 계약이 아니다 |

## 13. 정리 상태

- **서빙 서버: 종료함.** `pgrep -fa "vllm serve"` → 0건, `GPU 반환됨` 확인
- 임시 설정·프로필: 없음 (프로필을 안 건드렸다)
- 저장소 변경: **ATOM 체크아웃은 읽기만** 했다. `/tmp/arm_describe.py`·`/tmp/arm_order.py`는
  임시 파일이고 그 내용은 §14에 원문으로 남긴다

## 14. 부록 — 재현 절차

### 서버 — 조건 넷

```bash
export PATH=/srv/tenants/user/venv-probe/bin:/usr/local/cuda/bin:$PATH

# (A) 팔 1·2 — xgrammar
VLLM_USE_DEEP_GEMM=0 VLLM_ATTENTION_BACKEND=TRITON_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 \
TIKTOKEN_ENCODINGS_BASE=$HOME/tiktoken_encodings \
nohup vllm serve openai/gpt-oss-20b --port 8006 --served-model-name gpt-oss-20b \
  --max-model-len 32768 --gpu-memory-utilization 0.30 --enforce-eager \
  --structured-outputs-config.backend xgrammar \
  > ~/vllm_logs/gptoss_8006_xgrammar.log 2>&1 &

# (B) 팔 3~7 — 위와 같고 한 줄만 다르다
#     --structured-outputs-config.reasoning_parser openai_gptoss

# (C) 팔 8·9 — 백엔드만 다르다
#     --structured-outputs-config.backend outlines

until curl -s -f http://127.0.0.1:8006/v1/models >/dev/null 2>&1; do sleep 10; done; echo "UP"
```

### 팔 1~4·8

```bash
cd /srv/tenants/user/whitehat-boss
python tools/probe/gpt_oss_structured.py --label xgrammar-report33 --schema report33 \
  --trials 5 --out /srv/tenants/user/gpt_oss_structured.jsonl
# (--label · --schema만 바꿔 반복)
```

### 팔 5·9 — 프로브를 고치지 않고 어휘만 더한다

```bash
cat > /tmp/arm_describe.py <<'PYEOF'
import importlib.util, sys
src = "/srv/tenants/user/whitehat-boss/tools/probe/gpt_oss_structured.py"
spec = importlib.util.spec_from_file_location("gos", src)
g = importlib.util.module_from_spec(spec); sys.modules["gos"] = g
spec.loader.exec_module(g)
g.SCHEMAS["describe"] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["vulnerable", "safe"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}
g.CORRECT["describe"] = "vulnerable"
sys.argv = ["probe", "--label", "rparser-describe", "--schema", "describe",
            "--trials", "5",
            "--serve-cmd", "same-server-as-rparser-report33 (schema name=Verdict, vocab=vulnerable/safe)",
            "--out", "/srv/tenants/user/gpt_oss_structured.jsonl"]
raise SystemExit(g.main())
PYEOF
python /tmp/arm_describe.py
```

⚠ **팔 9는 이 스크립트를 `outlines` 서버에서 그대로 재실행한 것이다** — 그래서 라벨과
`serve_cmd`가 팔 5의 값을 그대로 들고 있다. **다음에는 라벨을 서버와 묶는다.**

### 팔 6·7 — enum 순서 · 속성 순서

```bash
cat > /tmp/arm_order.py <<'PYEOF'
import importlib.util, sys
src = "/srv/tenants/user/whitehat-boss/tools/probe/gpt_oss_structured.py"
spec = importlib.util.spec_from_file_location("gos", src)
g = importlib.util.module_from_spec(spec); sys.modules["gos"] = g
spec.loader.exec_module(g)

g.SCHEMAS["flipped"] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["safe", "vulnerable"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}
g.CORRECT["flipped"] = "vulnerable"

g.SCHEMAS["reason_first"] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "verdict": {"type": "string", "enum": ["vulnerable", "safe"]},
    },
    "required": ["reason", "verdict"],
    "additionalProperties": False,
}
g.CORRECT["reason_first"] = "vulnerable"

OUT = "/srv/tenants/user/gpt_oss_structured.jsonl"
for key, label, note in (
    ("flipped", "rparser-flipped", "describe와 enum 순서만 다르다 (safe, vulnerable)"),
    ("reason_first", "rparser-reason-first", "describe와 속성 순서만 다르다 (reason 먼저)"),
):
    sys.argv = ["probe", "--label", label, "--schema", key, "--trials", "5",
                "--serve-cmd", "same-server-as-rparser-* : " + note, "--out", OUT]
    rc = g.main()
    if rc:
        raise SystemExit(rc)
PYEOF
python /tmp/arm_order.py
```

### 요약 (팔 수준이 아니라 시행을 센다)

```bash
python -c "
import json
for d in map(json.loads, open('/srv/tenants/user/gpt_oss_structured.jsonl')):
    t=d['structured_trials']; ok=sum(1 for x in t if x.get('correct'))
    print(d['label'].ljust(20), d['schema_key'].ljust(12), 'correct', ok, '/', len(t),
          '| verdicts', [x.get('verdict') for x in t])
"
```

### 한 팔의 `reason` 원문 (해석이 갈리는 자리)

```bash
python -c "
import json
d=[x for x in map(json.loads,open('/srv/tenants/user/gpt_oss_structured.jsonl')) if x['label']=='rparser-reason-first'][0]
for i,t in enumerate(d['structured_trials'],1):
    print(i, 'json', t['json_valid'], '|', t.get('verdict'), '|', (t.get('reason') or t.get('content',''))[:180]); print()
"
```

## 관련

- [2026-09-09 gpt-oss-20b 온보딩 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md) — §3.3이 이 실험의 기준선, §5·§6·§7이 이 실험이 답한 질문들
- [R8](../../open-questions/R/R8.md) — ⚠ **이 실험은 R8을 닫지 않는다.** 닫히는 조건 1은 `cli bench`이고 이것은 `curl`과 같은 자리다
- [ADR 0010](../../decisions/0010-harness-structure-and-model-roster.md) — 「다시 열리는 조건」 첫 줄이 Critic 자리의 재판단 조건을 걸고, **(나) 교체를 (가) 2단 추출 뒤로 둔다**
- [D46](../../open-questions/D/D46.md) — 팔 7의 무효 둘이 같은 계열로 보이나 **귀속은 안 했다**
- [D4](../../open-questions/D/D4.md) — Critic 반박 정확도의 표본·합격선
- `tools/probe/gpt_oss_structured.py` — 이 프로브. **09-09가 원문을 안 남겨 표뿐이던 것을 JSONL로 남긴다**
