# `gpt-oss-20b` 구조화 출력 — 어휘 × `reasoning_parser` 2×2 + 서술 어휘 — ATOM

> **한 줄:** 다섯 팔 전부 무너진다. **어휘도 플래그도 원인이 아니다.**
> 자유서술은 매번 3/3인데 스키마를 씌우면 1/5 또는 **0/5**다.

> **이 실험이 닫은 것.** [2026-09-09 온보딩 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md) §5가
> 미측정으로 남긴 *"같은 케이스·같은 스키마·같은 `max_tokens`로 **플래그만** 바꾼 A/B"* 와
> §7 후속 2가 *"셋 중 제일 싸다"*고 적은 **실제 `Decision` 스키마 반복**이다. 둘 다 답이 나왔고
> **둘 다 부정이다.**

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | 첫 팔 `2026-09-11 20:14:28 KST`. **나머지 팔의 시각은 JSONL의 `recorded_at`에 있다** — 여기 옮겨 적지 않는다(옮겨 적으면 두 곳이 갈린다) |
| 실제 서빙 모델 | `openai/gpt-oss-20b` (`--served-model-name gpt-oss-20b`는 별칭). ⚠ **revision을 이 세션에서 재확인하지 않았다** — registry(`config/models.yaml`)가 `@6cee5e81ee83917806bbde320786a8fb61efebee`를 들고 HF 캐시에서 해석됐다 |
| `repo_commit` | `089366c` (ATOM 체크아웃, 브랜치 `probe/d67-and-gpt-oss`) |
| `run_id` | **없다.** `cli run`을 안 돌렸다 — 이 모델은 아직 어느 프로필의 역할도 아니다 |
| run 산출물 경로·보존 여부 | 해당 없음 |
| `tool_runner` | 해당 없음 (모델 호출만) |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 |
| `--gpu-memory-utilization` | `0.30` (분모 = 물리 통합메모리 **119.7 GiB**) |
| 동시에 떠 있던 다른 서버 | **없다.** 8006 하나뿐이고 팔 사이에 한 번 재기동했다 |
| 원문 산출물 보존 위치 | **`/srv/tenants/ext01/gpt_oss_structured.jsonl` (5줄, 팔당 1줄).** 요청·응답 전문 포함 |
| 서빙 서버 stdout | `~/vllm_logs/gptoss_8006_xgrammar.log` · `~/vllm_logs/gptoss_8006_rparser.log` |

⚠ **`run_id`가 없으므로 이 결과는 비교표에 들어가지 않는다**([evaluation.md](../../evaluation.md) §6).
프로브는 `curl`과 같은 자리이고, [R8](../../open-questions/R/R8.md)의 닫히는 조건 1은
`cli bench`가 답한다.

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **`reasoning_parser` 명시가 판정을 바꾸는가** | **아니다.** verdict 시퀀스가 **바이트 단위로 동일**하다 |
| **어휘를 실제 `Decision`으로 바꾸면 뒤집히는가** | **아니다.** `report33`과 같은 1/5 |
| **서술 어휘(`vulnerable`/`safe`)는** | **더 나쁘다 — 0/5 만장일치 오답** |
| JSON 유효성 | **문제가 아니다. 25/25** |
| 자유서술 대조군 | **매 팔 3/3** |
| `reasoning_content` | **25/25 비었다.** 그런데 `completion_tokens`는 1,186~1,862 |

## 2. 실험 환경

- ATOM (NVIDIA DGX Spark, aarch64, sm_121), vLLM 0.27.1
- venv `/srv/tenants/ext01/venv-probe`, 저장소 `/srv/tenants/ext01/whitehat-boss`
- 프로브 `tools/probe/gpt_oss_structured.py` (PR #175). **이 실험이 그 프로브의 첫 실행이다**
- 기동 인자는 [§9](#9-부록--재현-절차)에 원문 그대로

## 3. 실험 1 — 2×2 (스키마 어휘 × `reasoning_parser`)

### 목적

**경로 성립 판정이다. 성능 측정이 아니다.** 09-09 §3.3의 「구조화 출력 실패」가
① 스키마 어휘 때문인지 ② `reasoning_parser`를 CLI로 안 줘서인지를 가른다.

### 조건

프로브가 나머지를 **전부 상수로 잡는다** — 같은 프롬프트(UAF 한 케이스), `max_tokens=3000`,
`reasoning_effort=high`, `trials=5`, `backend=xgrammar`, 같은 기동 환경변수 셋.
갈리는 것은 **두 축뿐**이다.

| 팔 | `--structured-outputs-config.reasoning_parser` | `--schema` | verdict enum |
| --- | --- | --- | --- |
| `xgrammar-report33` | (안 줌 — EngineCore가 채운다) | `report33` | `upheld` / `rejected` |
| `xgrammar-decision` | (안 줌) | `decision` | `uphold` / `rebut` / `need_evidence` |
| `rparser-report33` | **`openai_gptoss` 명시** | `report33` | 위와 같음 |
| `rparser-decision` | **`openai_gptoss` 명시** | `decision` | 위와 같음 |

### 결과

| 팔 | HTTP 200 | JSON 유효 | **verdict 정답** | verdict 시퀀스 |
| --- | --- | --- | --- | --- |
| `xgrammar-report33` | 5/5 | 5/5 | **1/5** | `rejected, rejected, rejected, rejected, upheld` |
| `rparser-report33` | 5/5 | 5/5 | **1/5** | `rejected, rejected, rejected, rejected, upheld` |
| `xgrammar-decision` | 5/5 | 5/5 | **1/5** | `rebut, rebut, rebut, uphold, rebut` |
| `rparser-decision` | 5/5 | 5/5 | **1/5** | `rebut, rebut, rebut, uphold, rebut` |

자유서술 대조군은 네 팔 모두 **3/3**.

### 근거 로그

```
[팔]    xgrammar-report33
  verdict 정답    1/5
  verdict들       ['rejected', 'rejected', 'rejected', 'rejected', 'upheld']

[팔]    rparser-report33
  verdict 정답    1/5
  verdict들       ['rejected', 'rejected', 'rejected', 'rejected', 'upheld']
```

```
[팔]    xgrammar-decision
  verdict들       ['rebut', 'rebut', 'rebut', 'uphold', 'rebut']

[팔]    rparser-decision
  verdict들       ['rebut', 'rebut', 'rebut', 'uphold', 'rebut']
```

### 해석

**`reasoning_parser` 가설이 기각됐다.** 09-09 §6이 *"검토된 가설 — `reasoning_parser`를
CLI로 안 줘서 채널 경계를 몰랐다"*로 남기고 *"확인법: 그 플래그를 명시하고 §3.3을 반복해
verdict가 0/5에서 움직이는지 본다"*고 적었다. **안 움직였다.** 같은 §2가 관측한
*"EngineCore가 `openai_gptoss`로 채웠다"*가 맞았다는 뜻이다.

**어휘 가설도 기각됐다.** §7 후속 2가 *"어휘가 원인이면 serve 설정을 안 건드리고도
뒤집힌다"*고 적었는데 안 뒤집혔다.

⚠ **시퀀스가 팔 사이에서 같다는 것은 따로 읽는다.** 플래그를 바꿔도 다섯 시행의 답이
순서까지 같다 — 이 조건에서 **재현성이 있다**는 뜻이고, 「우연히 1/5이 나왔다」가 아니다.
**다만 그 재현성의 기제(샘플링 seed·캐시·결정성 중 무엇인지)는 이 실험이 안 갈랐다.**

## 4. 실험 2 — 서술 어휘

### 목적

실험 1이 가른 두 어휘는 **둘 다 「주장에 대한 판정」**(`upheld`/`rejected`,
`uphold`/`rebut`)이다. **#160이 3/3을 낸 `vulnerable`/`safe`는 「코드에 대한 서술」**이라
종류가 다르다. 그 축을 가른다.

### 조건

**실험 1의 `rparser-*` 팔과 서버·프롬프트·`max_tokens`·`reasoning_effort`·JSON 스키마
이름(`Verdict`)까지 전부 같고, `enum` 두 값만 다르다.** 프로브를 고치지 않고 모듈을 불러와
스키마를 더했다([§9](#9-부록--재현-절차)).

### 결과

| 팔 | HTTP 200 | JSON 유효 | **verdict 정답** | verdict 시퀀스 |
| --- | --- | --- | --- | --- |
| `rparser-describe` | 5/5 | 5/5 | **0/5** | `safe, safe, safe, safe, safe` |

자유서술 대조군 **3/3**.

### 근거 로그

```
[팔]    rparser-describe
[스키마] describe  (정답 = vulnerable)
  HTTP 200        5/5
  JSON 유효       5/5
  verdict 정답    0/5
  reasoning 빔    5/5
  verdict들       ['safe', 'safe', 'safe', 'safe', 'safe']
  자유서술 200    3/3  (§3.3 대조군)
```

### 해석

**어휘 가설이 완전히 기각된다.** 세 어휘 전부 실패했고, **서술 어휘가 제일 나쁘다.**

**2개 값 enum에서 0/5는 우연보다 나쁘다.** 5회 독립 균등이면 전부 틀릴 확률이
`(1/2)^5 = 3.1%` `[계산]`이다. **「헷갈린다」로는 설명되지 않는다** — 체계적으로 한쪽에
붙었다는 뜻이고, 그 한쪽이 하필 오답이다.

⚠ **「#160의 3/3이 어휘 덕이 아니었다」까지만 따라 나온다.** #160이 바꾼 축 넷 중 셋
(플래그·기동 인자·`max_tokens`)을 이 실험이 상수로 잡았고 어휘도 기각됐으므로,
**남은 설명은 「케이스」 하나다.** 그것은 이 실험이 안 쟀다(§5).

## 5. 실험 1·2가 공통으로 남긴 관측

**이 절이 다음 실험의 입력이다.**

| 관측 | 값 |
| --- | --- |
| 자유서술 정답 | **매 팔 3/3** — 모델은 답을 안다 |
| `reasoning_content` | **25/25 비었다** |
| `completion_tokens` | **1,186~1,862** (구조화 시행) |
| `finish_reason` | `stop` — **잘린 것이 아니다** |
| JSON 유효 | 25/25 — D67의 공백 폭주는 **여기 없다** |

**250자 안팎의 JSON에 1,000토큰이 넘게 들지 않는다.** 모델이 그만큼 생성하는데
그 결과가 `content`에도 `reasoning_content`에도 안 나온다.

⚠ **여기서 기제를 단정하지 않는다.** 살아 있는 설명은 「Harmony 채널 × 제약 디코딩의
상호작용」이지만 **이 실험은 채널을 직접 관측하지 않았다** — 본 것은 `reasoning_content`가
비었다는 것과 토큰 수뿐이다. 그 기제가 참이면 처방은 [2026-09-09 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md)
§7 후속 1의 **(가) 자유서술 → 별도 추출**인데, **기제가 안 갈린 상태에서 처방을 고르지 않는다.**

## 6. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 첫 요약 one-liner가 `KeyError: 'correct'` | `correct`는 팔 수준이 아니라 `structured_trials[*]`에 있다 | 시행을 순회해 세는 one-liner로 바꿨다(§9) |
| 세 번째 어휘를 재려면 프로브 수정이 필요 | `SCHEMAS`가 모듈 상수 | **고치지 않았다** — `importlib`로 모듈을 불러와 키를 더하고 `main()`을 불렀다. 요청 조립 경로가 같으므로 팔 간 상수가 유지된다 |

## 7. 미측정 항목과 사유

**못 잰 값을 0으로 채우지 않는다.**

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| **`outlines` 백엔드** | **안 해봤다.** 09-09 §5가 남긴 셋 중 마지막 |  `--structured-outputs-config.backend outlines`로 재기동해 같은 팔을 돌린다. **처방이 갈리는 자리다** — xgrammar가 원인이면 서버 플래그 한 줄이고, 모델이면 2단 추출(코드 변경)이다 |
| **`enable_in_reasoning=True`** | **안 해봤다** | 그 값으로 재기동해 §3.3 반복 |
| **케이스 축** | 이 실험은 **UAF 한 케이스 ×5**다. #160은 서로 다른 3케이스 ×1 | 같은 서버·같은 스키마로 케이스를 바꿔 돌린다. **#160의 3/3을 설명할 남은 축이 이것 하나다** |
| **`reasoning_content`가 왜 비는가** | 안 갈랐다. 서버 로그 레벨을 안 올렸다 | 로그 레벨을 올려 Harmony 채널 파싱을 본다 |
| **`report33` 밖 어휘의 오답 내용** | `describe` 팔의 `reason` 원문을 이 보고서 작성 시점에 못 읽었다 | JSONL에 **보존돼 있다.** `rparser-describe`의 `structured_trials[*].reason`을 읽는다 |
| **실제 Critic 계약에서의 정확도** | 프로브 프롬프트가 *"이 코드에 UAF가 있나"*인데 **실제 Critic은 Auditor의 주장·경로·근거를 받는다** | `prompts/critic/v1`의 렌더 결과로 재기. **이 실험은 그 계약이 아니다** |
| Critic 반박 정확도(체크리스트 7번) | bench 범위 밖, 고정 반박 표본 필요 | 표본 확정 후([D4](../../open-questions/D/D4.md)) |

## 8. 반증 조건

- **「어휘가 원인이다」가 틀렸다면**: 이 실험이 그것을 보였다. 세 어휘 전부 실패했고
  서술 어휘가 제일 나빴다. **이 결론이 다시 틀리려면** 네 번째 어휘에서 뒤집혀야 한다.
- **「`reasoning_parser` 플래그가 원인이다」가 틀렸다면**: 이 실험이 그것을 보였다.
  시퀀스가 동일하다.
- **「구조화 출력이 이 모델을 망가뜨린다」가 틀렸다면**: `outlines`나
  `enable_in_reasoning=True`에서 정답률이 오른다. **둘 다 안 해봤으므로 이 결론은
  `xgrammar` 범위다.** 09-09 §6이 같은 경계를 그었고 그 경계가 아직 안 넓어졌다.
- **「모델은 멀쩡하다」가 틀렸다면**: 자유서술 3/3이 **한 케이스**에서 나왔다. 어려운
  표본에서 무너지면 이 문장을 좁혀야 한다.
- ⚠ **「#160이 틀렸다」로 읽지 않는다.** #160의 3/3은 관측이고 이 실험이 뒤집지 않는다.
  갈린 것은 **그 3/3의 원인이 무엇이 아닌가**이고, 남은 후보는 케이스다.

## 9. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | **`outlines` 백엔드 A/B** | 미측정. **처방(서버 플래그 ↔ 2단 추출)이 여기서 갈린다** |
| 2 | **케이스 축** — #160의 3케이스로 반복 | 미측정. #160의 3/3을 설명할 마지막 후보 |
| 3 | `enable_in_reasoning=True` | 미측정 (09-09 §5) |
| 4 | **실제 Critic 프롬프트로 재기** | 이 실험의 프롬프트는 Critic 계약이 아니다 |
| 5 | **Critic 자리 판정** — (가) 2단 추출 / (나) 모델 교체 | **아직 못 정한다.** 1·2가 답해야 한다. [ADR 0010](../../decisions/0010-harness-structure-and-model-roster.md) 「다시 열리는 조건」 첫 줄이 (나)의 조건을 *"2단 추출로도 안 닫히면"*으로 걸어 뒀으므로 **(가)를 재기 전에는 (나)로 못 간다** |

## 10. 정리 상태

- **서빙 서버: 종료함.** `pgrep -fa "vllm serve"` → 0건, `GPU 반환됨` 확인
- 임시 설정·프로필: 없음 (프로필을 안 건드렸다)
- 저장소 변경: **ATOM 체크아웃은 읽기만** 했다. `/tmp/arm_describe.py`는 임시 파일이고
  그 내용은 §9에 원문으로 남긴다

## 11. 부록 — 재현 절차

### 서버 (팔 1·2)

```bash
export PATH=/srv/tenants/ext01/venv-probe/bin:/usr/local/cuda/bin:$PATH
VLLM_USE_DEEP_GEMM=0 VLLM_ATTENTION_BACKEND=TRITON_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 \
TIKTOKEN_ENCODINGS_BASE=$HOME/tiktoken_encodings \
nohup vllm serve openai/gpt-oss-20b --port 8006 --served-model-name gpt-oss-20b \
  --max-model-len 32768 --gpu-memory-utilization 0.30 --enforce-eager \
  --structured-outputs-config.backend xgrammar \
  > ~/vllm_logs/gptoss_8006_xgrammar.log 2>&1 &
until curl -s -f http://127.0.0.1:8006/v1/models >/dev/null 2>&1; do sleep 10; done; echo "UP"
```

### 서버 (팔 3·4·5) — **`--structured-outputs-config.reasoning_parser openai_gptoss` 한 줄만 다르다**

```bash
VLLM_USE_DEEP_GEMM=0 VLLM_ATTENTION_BACKEND=TRITON_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 \
TIKTOKEN_ENCODINGS_BASE=$HOME/tiktoken_encodings \
nohup vllm serve openai/gpt-oss-20b --port 8006 --served-model-name gpt-oss-20b \
  --max-model-len 32768 --gpu-memory-utilization 0.30 --enforce-eager \
  --structured-outputs-config.backend xgrammar \
  --structured-outputs-config.reasoning_parser openai_gptoss \
  > ~/vllm_logs/gptoss_8006_rparser.log 2>&1 &
```

### 팔 1~4

```bash
cd /srv/tenants/ext01/whitehat-boss
python tools/probe/gpt_oss_structured.py --label xgrammar-report33 --schema report33 \
  --trials 5 --out /srv/tenants/ext01/gpt_oss_structured.jsonl
python tools/probe/gpt_oss_structured.py --label xgrammar-decision --schema decision \
  --trials 5 --out /srv/tenants/ext01/gpt_oss_structured.jsonl
# (서버 재기동 후)
python tools/probe/gpt_oss_structured.py --label rparser-report33 --schema report33 \
  --trials 5 --out /srv/tenants/ext01/gpt_oss_structured.jsonl
python tools/probe/gpt_oss_structured.py --label rparser-decision --schema decision \
  --trials 5 --out /srv/tenants/ext01/gpt_oss_structured.jsonl
```

### 팔 5 — 프로브를 고치지 않고 어휘만 더한다

```bash
cat > /tmp/arm_describe.py <<'PYEOF'
import importlib.util, sys
src = "/srv/tenants/ext01/whitehat-boss/tools/probe/gpt_oss_structured.py"
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
            "--out", "/srv/tenants/ext01/gpt_oss_structured.jsonl"]
raise SystemExit(g.main())
PYEOF
python /tmp/arm_describe.py
```

### 요약 (팔 수준이 아니라 시행을 센다)

```bash
python -c "
import json
for d in map(json.loads, open('/srv/tenants/ext01/gpt_oss_structured.jsonl')):
    t=d['structured_trials']; ok=sum(1 for x in t if x.get('correct'))
    print(d['label'].ljust(20), d['schema_key'].ljust(10), 'correct', ok, '/', len(t),
          '| verdicts', [x.get('verdict') for x in t])
"
```

## 관련

- [2026-09-09 gpt-oss-20b 온보딩 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md) — §3.3이 이 실험의 기준선, §5·§6·§7이 이 실험이 답한 질문들
- [R8](../../open-questions/R/R8.md) — ⚠ **이 실험은 R8을 닫지 않는다.** 닫히는 조건 1은 `cli bench`이고 이것은 `curl`과 같은 자리다
- [ADR 0010](../../decisions/0010-harness-structure-and-model-roster.md) — 「다시 열리는 조건」 첫 줄이 Critic 자리의 재판단 조건을 건다
- [D4](../../open-questions/D/D4.md) — Critic 반박 정확도의 표본·합격선
- `tools/probe/gpt_oss_structured.py` — 이 프로브. **09-09가 원문을 안 남겨 표뿐이던 것을 JSONL로 남긴다**
