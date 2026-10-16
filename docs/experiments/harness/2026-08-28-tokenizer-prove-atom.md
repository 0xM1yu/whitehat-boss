# 토크나이저 정확도 Prove — ATOM

`run_20260827_0004`(30B `b1_single`, PWN-003, 실서빙 33 `model_call`) **재분석**이다. 코드 변경 0.
run 산출물(`trace.jsonl`·`prompts/`)과 서빙 모델 `tokenizer.json`에서 다시 읽은 값만 적었다.

## 메타

이 문서는 `run_20260827_0004`의 **재분석**이라 그 run의 세션 메타(기동 명령·prefix cache·샘플링
등)를 복제하지 않고 [track_a 보고서](2026-08-28-pipeline-track-a-pwn003-atom.md)를 링크한다. 재분석에
필요한 다섯 값만 여기 둔다(TEMPLATE.md).

| 항목 | 값 |
| --- | --- |
| 대상 run | `run_20260827_0004` (30B `b1_single`, PWN-003). run 자체의 세션 메타·`repo_commit`은 [track_a 보고서](2026-08-28-pipeline-track-a-pwn003-atom.md) §메타 |
| run 산출물·보존 | `/srv/tenants/user/artifacts/run_20260827_0004` — **ATOM 보존.** `trace.jsonl`·`prompts/`(33건)를 재분석 입력으로 받았다 |
| `repo_commit` (재분석 시점) | **해당 없음** — 이 재분석은 하네스를 돌리지 않고 산출물만 읽었다(§5가 쓰는 것은 `tokenizers`와 표준 라이브러리뿐이다). run 자체의 `repo_commit`은 [track_a 보고서](2026-08-28-pipeline-track-a-pwn003-atom.md) §메타 |
| `--device` | `atom` |
| 서빙 스냅샷 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` — Prove가 이 `tokenizer.json`으로 셌다. **이 40 hex가 이 문서 전체의 근거다**(무엇으로 셌나). revision은 [NVFP4 검증 보고서](2026-08-25-serving-nvfp4-atom.md)와 같다 |

`--device`가 `atom`인 근거는 입력이 ATOM `run_20260827_0004`의 산출물이라는 것이다 — 이 재분석
자체는 `--device`를 인자로 받지 않는다. **그 설명을 값 칸에 적지 않는다**:
`tests/test_docs_format.py`의 `_DEVICE_ROW`가 `` | `--device` | `atom` | `` 꼴만 읽어 파일명의
장비와 대조하므로, 칸에 산문을 넣으면 그 대조가 조용히 건너뛰어진다.

## 1. 요약

| 축 | 결과 |
| --- | --- |
| **토크나이저 Prove (D21 (b), 로드맵 4단계)** | 서버 `input_tokens` − 우리 카운트 = **정확히 +13** (33/33). 비율 0.990(상수 보정 시 1.000). 옛 `len//4`는 0.629(≈1.6x 과소) |
| 부수 — critic 실서빙 확인 | critic 7콜이 `qwen3-coder-30b-a3b`로 real 서빙됨(`gpu_ms` 있음). 단 `b1_single`이라 scout·auditor와 **같은 30B**를 쓴다 |
| 의제 — `critic_top_n` | D26(2026-08-26 해소)이 이제 **개수**를 다음 병목으로 지목 — "상한만 올린 대조 run 1회"가 답(단 D26의 그 숫자는 Fake critic run이다 — §4). 이 문서의 per-call 비용은 confound가 커 그 대조 run의 비용 예산 입력까지다 |

## 2. 토크나이저 Prove (메인)

**목적.** D21 (b)로 넣은 실 토크나이저가 실서빙 `input_tokens`와 얼마나 맞는지 증명한다. 그동안
보고서들은 `token_counter` 이벤트로 "어느 카운터를 썼나(source·real)"까지만 남겼고, 그 카운터가
서버 실측과 **얼마나 정확한가**는 숫자로 대조된 적이 없다.

**방법.** 33개 `model_call`의 `input_tokens`(서버 실측)와, 각 `prompt_ref` 파일에서 harness
주석(`<!-- … -->` 줄)을 걷어낸 실제 전송 텍스트를 서빙 스냅샷
(`NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048`)의
`tokenizer.json`으로 센 값을 대조했다.

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
대규모 검증한다. 우리는 "소수 역할·소형 모델로 축소"([prd.md](../../prd.md))했으므로 cap이 필요하지만 그
값은 우리 비용 예산이 정할 몫이다.

**D26이 이제 `critic_top_n`을 다음 병목으로 지목한다.** 이 항목은 2026-08-26에 해소됐다
(`docs/open-questions.md`의 D26 제목 취소선). build `-I` 누락이 고쳐지자 `has_repro_spec`과
`static_ok`의 배타가 풀렸고 8단계 `gate_eval`이 8건 나왔다. 그 뒤 D26의 결론은 "다음 병목은
이제 `critic_top_n`이 맞다"이다 — 6단계 통과 59건 중 8건만 7단계에 갔고(=상한 8) 그 8건이 전부
upheld라 **E3 8건이 상한값 그 자체**이며, 잘린 51건 중 50건이 `repro_spec`을 갖고 있다. D26이 그
질문을 닫을 입력으로 든 것은 **"상한만 바꾼 대조 run 1회"**다.

**단, 그 숫자들은 auditor·critic이 Fake인 run의 것이다.** D26이 같은 자리에 경고 둘을 함께
달아 뒀다 — "**E3 8건을 성능으로 읽으면 안 된다** — auditor·critic이 Fake다", 그리고 "그 51건이
전부 upheld된다는 근거는 없으므로(confidence 0.6이 23건 / 0.3이 28건)". 이 문서가 바로 위에서
자기 per-call을 confound 셋으로 낮춘 것과 **같은 기준이 이 인용 숫자에도 걸린다.**

> ⚠ **정정:** 이 §의 초안은 "개수보다 선별 기준(confidence vs `repro_spec`)이 먼저"라고 적었는데,
> 그것은 `-I` 수정 **전** D26의 배타성에 기댄 서술이라 지금은 서지 않는다. 그리고 "8→44로 늘려도
> 8단계 0건"은 **실측이 아니라** 그 배타성에서 나온 구조적 추론이고, 근거 run(`run_20260825_0001`)은
> `critic_top_n: 8`로 돌았다 — 실측으로 적으면 안 된다.

**회의에서 정할 것은 `critic_top_n`을 얼마로 올릴지다.** D26이 든 대조 run(상한만 올린 1회)이 그
답의 주 입력이고, 이 문서의 per-call 관찰은 그 대조 run의 **비용 예산**을 잡는 데만 쓰인다.

| 필요한 미측정 입력 | 무엇을 답하나 |
| --- | --- |
| **상한만 올린 대조 run 1회** | 잘린 51건(50건 `repro_spec` 보유)이 critic에 가면 E3가 몇으로 느는가 — D26이 든 그 run |
| 8B critic 비용 | B1'/B3'가 실제 쓰는 `qwen3-8b-bf16`(30B 아님) · 순수 GPU 또는 격리 요청 · 캡이 실제로 자를 때의 손실 |

## 5. 재현

입력 셋: `run_20260827_0004/trace.jsonl`(서버 `input_tokens`) · `run_20260827_0004/prompts/NNNN_<role>.md`
(전송 프롬프트) · 위 메타의 40 hex 스냅샷 `tokenizer.json`.

**전송 텍스트는 프롬프트 파일에서 harness 주석 줄만 걷어낸 것이다 — 그 규칙이 +13의 전제라 정규식과
대조식을 원문으로 박는다** (걷어내는 줄이 하나 달라지면 +13이 +12/+14가 된다).

```python
import re, json
from tokenizers import Tokenizer

COMMENT = re.compile(r'^\s*<!--.*-->\s*$')            # 이 패턴에 맞는 '줄 전체'만 제거
tok = Tokenizer.from_file("<40hex 스냅샷>/tokenizer.json")

for e in map(json.loads, open(".../run_20260827_0004/trace.jsonl", encoding="utf-8")):
    if e.get("kind") != "model_call":            continue
    d = e["data"]
    if not d.get("ok") or "prompt_ref" not in d: continue   # 이 run은 33건 전부 ok=True다
    # 줄에 개행이 이미 붙어 있다 — `"\n".join`을 쓰면 줄마다 빈 줄이 끼어 수가 달라진다.
    text = "".join(ln for ln in open(f".../{d['prompt_ref']}", encoding="utf-8")
                   if not COMMENT.match(ln))
    ours   = len(tok.encode(text).ids)           # 실 토크나이저
    server = d["input_tokens"]                    # 서버 실측
    assert server - ours == 13                    # 33/33 성립
```

`prompt_log.render()`가 파일에 `<!-- harness-prompt … -->` · `<!-- prefix -->` · `<!-- body -->` 세 줄을
넣으므로 위 정규식이 걷어내는 것은 그 셋이고, 남는 것은 `prefix` + `\n` + `body`다. **서버는 그 둘을
이어 붙인 한 덩어리가 아니라 두 메시지로 받는다**(`src/adapters/openai_compat.py` — `system=prefix`,
`user=body`). 따라서 위 대조는 "이어 붙인 텍스트 대 서버 실측"이고, +13에는 채팅 템플릿 래핑과
이 이어 붙이기의 차이가 **함께** 들어 있다 — 둘로 갈라 세지 않았으므로 §6의 "+13 상수의
구성"은 여전히 미측정이다.

critic real 판정과 per-call 관찰은 같은 `model_call`의 `model`·`gpu_ms`·`gpu_ms_source`에서 읽었다.
전체 스크립트를 `tools/`에 두는 것이 정본이나(TEMPLATE.md §9), 지금은 위 정규식·입력 경로·대조식이
재현의 전부다.

## 6. 안 잰 것

- **8B(B1'/B3' 실제 모델)의 토크나이저 정확도.** 이 Prove는 30B 스냅샷 하나에 대한 것이다 — 배선·기법이
  정확하다는 증명이고, 8B 토크나이저도 같은 경로를 타지만 그 숫자는 따로 재지 않았다.
- **정식 critic 비용.** 위 §4의 confound 셋 때문에 per-call은 관찰까지다.
- **+13 상수의 구성.** 채팅 템플릿 래핑으로 추정하나 템플릿을 토큰 단위로 분해해 확인하지는 않았다.
