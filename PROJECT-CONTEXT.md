# PROJECT-CONTEXT.md — 지금 무엇이 정본인가

> **이 파일이 로스터의 정본이다.** 다른 문서가 모델을 현재형으로 말하면 그 문서가 틀린 것이고,
> 이 파일과 어긋나면 **그 문서를 고친다.**
>
> **왜 이 파일이 있나.** 2026-09-09에 하루 동안 같은 사고가 네 번 났다 — 사람도 에이전트도
> 옛 로스터를 서술하는 계획 문서를 **살아 있는 지시로 읽고** 폐기 예정 구성으로 측정을 잡았다.
> 그때 저장소에는 「현재 구성」을 주장하는 소스가 셋(`config/`·`docs/` ADR·`build-plan §8`) 있었고
> **어느 쪽이 정본인지 기계가 읽을 수단이 없었다.** 이 파일이 그 수단이다.

---

## 1. 모델 로스터 — **정본**

**2026-09-09 확정(조민규) → 2026-09-21 회의에서 아래 로스터로 교체했다 → 2026-09-24 `scout_injection`을
`qwen3-14b`로 바꿨다**(2026-09-23 팀 피드백 「모델 크기를 내리자」 — ADR 0012(PR #260) 결정 4 · 조민규 배선 지시).
**→ 같은 날 조민규가 아래 표를 확정 로스터로 못박았다** — 가중치는 bf16 그대로(`gpt-oss-20b`만 원래 MXFP4)이고
양자화로 공동 상주하지 않는다(모델별 정밀도·추론은 아래 「모델별 정밀도 · 추론(사고)」). **→ 2026-09-25
`scout_memory`를 `qwen3-14b`로 바꿨다**(실험팀 최종 권고를 조민규가 받았다 — memory 자리는 파인튜닝한 Qwen3-14B를
쓸 계획이고 체크포인트가 오기 전까지 베이스 키다. boundary의 `seccoderx-8b`는 그대로다 — 팀 보고로는 파인튜닝
데이터와 비슷한 데이터로 학습됐고 후보 중 Recall이 가장 높다. run ID 없음). **→ 2026-09-26 Auditor를
`qwen3.8-27b`로 바꾸고 추론(사고)을 켰다**(조민규 — 옛 `qwen3.6-35b-a3b`는 로스터 밖이라 registry에서 지웠다.
고른 근거는 벤더 모델 카드뿐이다 `[출처주장]` — 아래 「모델별 정밀도 · 추론(사고)」). 앞 판(Scout
`qwen3-coder-30b-a3b`·`devstral-small-2-24b`·`qwen3-8b` · Auditor A/B)의 근거는
[ADR 0010](docs/decisions/0010-harness-structure-and-model-roster.md) §2와
[2026-09-08 로스터 조사](docs/research/2026-09-08-roster.md) §1이었다.
⚠ **교체판의 근거는 그 둘에 아직 없다** — 오늘 이 저장소가 드는 것은 실험팀 2026-09-20 결과와
`config/models.yaml`의 「확정 로스터」 주석이고, 선정을 가른 실측(GT 품질·처리량)은 아직 registry에
안 들어왔다([CLAUDE.md](CLAUDE.md) §0).

| 자리 | registry 키 | registry 등재 |
| --- | --- | --- |
| 3단계 · 임베딩 | `qwen3-embedding-0.6b` | ✅ |
| 3단계 · 리랭커 | `qwen3-reranker-4b` | ✅ |
| 4단계 · `scout_memory` (CWE-401·404·415·416·457·476·562·590·761·762·772·775·824·825) | `qwen3-14b` | ✅ |
| 4단계 · `scout_boundary` (CWE-119·120·121·122·124·125·126·127·129·131·170·190·191·193·194·195·197·369·680·681·787·805) | `seccoderx-8b` | ✅ |
| 4단계 · `scout_injection` (CWE-77·78·88·89·94·116·134) | `qwen3-14b` | ✅ |
| 5단계 · Auditor | `qwen3.8-27b` (`reasoning_effort: xhigh`) | ✅ |
| 7단계 · Critic | `gpt-oss-20b` (`reasoning_effort: high`) | ✅ |
| 8단계 · `pov` | `feyospace-v1-small` (`reasoning_effort: xhigh`) | ✅ |

⏵ **2026-10-07 — Scout 셋의 프롬프트가 v4로 배선됐다**(PR #524 — `qwen3-14b`·`seccoderx-8b` 두 엔트리를
함께 올렸다. 좌석 배치는 안 바꿨다). ⚠ **위 표의 CWE 열거보다 서빙 프롬프트가 두 칸 넓다** —
`scout_memory/v4/prefix.md`가 `CWE-822`를, `scout_injection/v4/prefix.md`가 `CWE-22`를 더 든다.
표를 안 고친 것은 4단계 CWE 분담이 팀이 정한 것이기 때문이다 — 확인은 실험팀 몫이다(이슈 #531, 2026-10-12 닫음 — 하네스 저장소가 정할 일이 아니다).
⚠ **boundary 좌석(`seccoderx-8b`)에서 v4를 돌린 실측은 없다** — 그 프롬프트의 원본 실험은 `qwen3-14b`로 쟀다.

⚠ **scout_memory·injection은 같은 키 `qwen3-14b` 한 벌이다**(2026-09-25부터 — 그 전에는
memory·boundary가 `seccoderx-8b` 한 벌이었다) — 프롬프트가 역할 폴더로 갈린다(`scout_memory/v3`=옛
실험 v14s · `scout_injection/v3`). 같은 weights라 별칭 키를 새로 두지 않는다 — 같은 서버(포트)를 두
자리가 함께 쓴다. ⚠ **그 결과 공동오답 행렬(ADR 0010 §3 · `candidate.py` `scout_member`)의
memory↔injection 축이 재는 것은 모델 차이가 아니라 프롬프트 차이다** — 그 축에서 "모델을 자리
사이에서 옮기는 근거"는 성립하지 않는다. boundary만 다른 키(`seccoderx-8b`)라 모델 대비가 산다.
⚠ **부수로 `prompt_template_version`이 모델 엔트리 단위이므로 memory·injection을 독립으로 버전 올릴
수 없다**(D67) — memory 프롬프트를 새 버전으로 올리면 injection에도 그 버전 폴더가 있어야 한다.
파인튜닝 체크포인트가 새 키로 들어오면 풀린다. ⚠ **프롬프트는 이 교체에서 안 바꿨다** — boundary를
SecCoderX가 파인튜닝에 쓴 전용 프롬프트로 바꾸는 것(출력 스키마가 크게 달라 하네스 수정이 따른다)과
memory 프롬프트 개선(스키마 재조정 가능)은 실험팀이 변경을 정리해 공유한 뒤의 일이다.

**Auditor의 A/B가 소멸했다.** 2026-09-21 교체가 5단계를 `qwen3.6-35b-a3b` 한 자리로 놓으면서,
문헌이 정면으로 갈린 둘을 실측으로 가르려던 갈래가 §1에서 없어졌다 — B안 `nemotron-3-super-120b-a12b`도
§1에서 내려왔다(애초에 registry 미등재라 온보딩 전이었다). ⚠ **갈래가 없어진 것이지 실측이 나온
것이 아니다** — 새 자리도 실측 전이고 그 상태는 §5가 든다([D24](docs/open-questions/D/D24.md)의 2026-09-21 절).
(⏵ 2026-09-24 — 새 자리를 처음 띄운 스모크가 있다. 기동·스키마·메모리를 쟀고 품질은 안 쟀다. 비교표 밖이다 — §5 Auditor 행.)
(⏵ 2026-09-26 — 자리의 모델이 다시 바뀌었다: `qwen3.8-27b`, 사고 켬 — 위 표. 이 문단의 `qwen3.6-35b-a3b`는 2026-09-21 교체 당시의 이름이다.)

⚠ **2026-09-24 — 「B1 기준(최강 단일 멤버)」 행을 뺐다.** 그 행의 키 `qwen3-coder-30b-a3b`와 프로필
`b1_single`을 로스터 밖 정리로 지웠다(아래 표 끝).

⚠ **2026-09-24 — 이 표를 다시 여는 로스터 제안이 [ADR 0012](docs/decisions/0012-harness-canon-v2.md)
결정 4에 있다.** ~~제안이라 위 표·`config/models.yaml`·`config/profiles.yaml`은
그대로이고 **정본은 여전히 위 표다.** 받아들여지면 §3의 넷을 같은 diff에서 고친다.~~ → **같은 날 조민규가
결정 4의 로스터를 확정했다.** 위 표·`config/models.yaml`·`config/profiles.yaml`·ADR 0010 §2가 이미 그 로스터다
(#266·#267 — injection `qwen3-14b`, 로스터 밖 키·프로필 삭제). **정본은 위 표다.** ADR 0012 **문서**는
2026-09-25에 손수민이 승인했다(구두) — 로스터 확정(2026-09-24)과 ADR 승인은 다른 일이었다.

### 모델별 정밀도 · 추론(사고) — 2026-09-24

값의 정본은 `config/models.yaml`(`quantization`·`reasoning_effort`)과 그 항목의 「기동 조건」 주석이다. 이 표는
한 화면 조망이고, 어긋나면 그쪽을 믿고 이 표를 고친다. 적재 크기는 vLLM 로그 실측의 **가중치+non-torch**다 —
[2026-09-24 스모크](docs/experiments/harness/2026-09-24-roster-smoke-atom.md) §2(Scout 둘·Auditor — Auditor는 두 번째 기동)와
[2026-09-21 메모리 보고서](docs/experiments/harness/2026-09-21-roster-memory-kv-atom.md) §1(검색 둘·Critic). 가중치만은 스모크 §2의
「가중치」 칸이다(15.27 · 27.52 · 65.53 GiB). (⏵ 2026-09-26 — 65.53은 옛 Auditor `qwen3.6-35b-a3b`의 값이다. 새 Auditor는 미실측이다.) non-torch 항은 util·기동마다 움직여 한 점이 아니다 — 리랭커가 두 판에서
8.12·8.15다(메모리 보고서 §1).

| 자리 | 모델 | 가중치 정밀도 | 적재 크기(가중치+non-torch) | 추론(사고) — 지금 설정에서 | 추론 손잡이(vLLM 0.27.1) |
| --- | --- | --- | --- | --- | --- |
| 3단계 · 임베딩 | `qwen3-embedding-0.6b` | fp16 | 1.76 GiB | 해당 없음 | — |
| 3단계 · 리랭커 | `qwen3-reranker-4b` | bf16 | 8.12~8.15 GiB | 해당 없음 | — |
| 4단계 · boundary | `seccoderx-8b`(SecCoderX RM, Qwen3-8B 기반) | bf16 | 16.05 GiB | 사고 모드로 돈다 — 출력의 약 2/3가 `<think>` 구간이다(스모크 §3 실험 1) | 등급이 없다. 길이 예산(`thinking_token_budget`)은 이 모델에 안 먹는다 — dense라 V2 러너로 돌고 V2가 그 필드를 거절한다 |
| 4단계 · memory·injection | `qwen3-14b` | bf16 | 28.27 GiB | 사고 모드로 돈다 — 출력의 약 2/3가 `<think>` 구간이다 | 위와 같다 |
| 5단계 · Auditor | `qwen3.8-27b`(dense 27.8B · GDN hybrid · VLM) | bf16 | **미실측** — 가중치만 51.75 GiB(계산값, HF safetensors 크기 합) | **사고를 켰다**(2026-09-26) — `reasoning_effort: xhigh` · ⏵ **2026-10-03 사고 상한 57,344 · 출력 상한 65,536**(그 전 28,672·32,768 — 조민규 승인 · 근거와 한계는 `config/models.yaml` 예산 절의 그 ⏵. 속도 근거가 외부 보고 4.5에서 vast 실측 16.2 tok/s로 바뀐 것이 올린 조건이다). 우리 장비에서 사고 켠 판을 띄운 적은 없다 | 등급이 있다 — `xhigh`(기본)·`medium`·`low`(`high`는 템플릿이 예외를 낸다). reasoning parser(`qwen3`)를 걸어야 사고가 살고 `thinking_token_budget`도 받는다(hybrid라 V1 러너 — vLLM 0.27.1 소스 판독, 미실측) |
| 7단계 · Critic | `gpt-oss-20b` | MXFP4(원래 형식) | 14.65 GiB | `reasoning_effort: high` | 등급 low·medium·high가 harmony system message에 실제로 들어간다. 길이 예산은 못 쓴다 |
| 8단계 · PoV | `feyospace-v1-small`(Qwen3.8-27B 사이버 post-train · Auditor와 같은 베이스) | bf16 | **미실측** — 가중치만 51.75 GiB(계산값 — 파라미터 BF16 27,781,427,952 × 2 B ÷ 2³⁰ = 51.747 GiB. 12개 파일의 크기 합은 안 쟀다 — Auditor 행의 51.75는 그쪽 18개 파일 합이다) | **사고를 켰다** — `reasoning_effort: xhigh` · 사고 상한 28,672 · 출력 상한 32,768(Auditor 손잡이 물려받음) · 우리 장비 미실측 | Auditor와 같다 — `xhigh`(기본)·`medium`·`low` · parser(`qwen3`) 걸어야 사고가 살고 `thinking_token_budget`을 받는다(hybrid V1 러너) |

⚠ 「추론 손잡이」 칸은 vLLM `v0.27.1` 태그의 소스에서 읽은 것이다(줄 번호는 2026-09-24에 그 태그와 다시 대조했다 ·
parser 이름 `qwen3`·`deepseek_r1`은 둘 다 `vllm/reasoning/__init__.py`에 등록돼 있다) — `vllm/v1/engine/input_processor.py:111-125`(V2 러너나
parser 없음이면 budget 거절) · `vllm/entrypoints/openai/chat_completion/protocol.py:569-573`(`reasoning_effort` →
`enable_thinking`) · `vllm/entrypoints/openai/parser/harmony_utils.py:104-139`(gpt-oss 등급). **우리 장비에서 budget을
걸어 본 적은 아직 없다.** 사고를 켜는 배선은 §5 「모델 배선」 행이 든다.

### 로스터에 없지만 registry에 남는 것

| 키 | 왜 남나 |
| --- | --- |
| `fake` | 인프라. `dev_*` 다섯 프로필의 전 역할 |
| `fake_v2` | 인프라. Scout 풀 팬아웃(v2 전용 풀 프롬프트)을 GPU 없이 테스트하는 fake 키. `test_pipeline_e2e`가 in-test로 조립한다 |

> ~~**registry 키는 지우지 않는다.**~~ ADR 0010 「되돌리는 비용」 — *"모델은 registry 키를 지우지 않으므로 프로필 한 줄이다."*
> 로스터 교체가 **삭제가 아니라 프로필 재배선**인 이유였고, 그래서 되돌리기가 쌌다.
>
> **2026-09-24 개정(조민규) — 확정 로스터와 인프라(`fake`·`fake_v2`) 밖의 키·프로필은 지운다.** 로스터가
> 확정됐고, 안 쓰는 키·프로필은 서빙·GPU 배선에 혼선만 만든다.
> **2026-09-30 — `feyospace-v1-small`은 §1로 배선·승격됐다**(8단계 pov 좌석 · `harness_pool`·`_hybrid`).
> **2026-10-08 — PoV 프롬프트는 v4이다**(#549/#515/#511). v4는 v3(2026-10-05, #354/#386①)에 넷을 더한
> 판이다(v4의 body는 v3와 바이트 동일, prefix만 다르다): 채점기 `vul` 종료 코드의 제외 코드(`0`·`71`·`300`)를
> 읽는 규칙 · 반복 판정의 실제 범위(지금까지 낸 모든 gen의 바이트) · 관측 `last_gen_*` 필드 설명과 도구 한계
> 문단 정정 · 고정 홉 예산(followup 첫 줄의 홉 줄 · 마지막 홉의 `submit`은 마지막 `gen`의 코드로 가른다 —
> 제외 코드 밖이면 하고 안이면 안 하며, `unavailable`은 진 것이 아니라 정보가 없는 것이므로 0바이트가 아니고 `rationale`에
> SOURCE·FACTS의 앞단 조건을 거쳐 sink에 이르는 사슬을 적을 때만 한다 - `submit` 원칙의 유일한 예외다. 코드를 읽는
> 줄은 마지막 `gen` 다음 턴의 것이다). 캐시된 코드(`cached=true`)로 `submit`하면 `rationale`에 그 사실을 적는다(막지는 않는다).
> v3가 켠 두 입력 변경은 v4가 그대로 물려받는다:
> Auditor 최초 읽기의 넓은 SOURCE와, 중간 gen PoC를 vul 다리에 낸 원시 종료 코드 되먹임이다.
> 되먹임은 `run --oracle-submit`일 때만 켜진다(꺼져 있으면 모든 gen이 `oracle_vul=unavailable`을 받는다).
> 모델이 submit한 PoC만 최종 지정 후보이고 fix 다리는 지정 뒤 한 번이다. **실모델 입력 토큰은 잰 값이 있다** —
> v3 prefix로 호출당 60,446~61,018(서버가 센 값, 한 finding의 네 호출 · n=1 · v4 prefix는 아직 안 쟀다,
> [2026-10-06 프로브](docs/experiments/harness/2026-10-06-pov-seat-probe-vastai.md)의 「호출당 입력」 행). ⚠ **전형값이
> 아니다** - 그 프로브는 `tool_runner: fake`라 도구 출력 꼬리가 없었고, 같은 run의 다른 finding hop0은 6,572토큰으로
> 약 열 배 작았다(n=2). 미실측은
> 셋이다: 되먹임이 PoC를 낫게 바꾸는 **효과**(대조가 없다) · **v4 prefix의 입력 토큰** · 제외 코드 규칙과 마지막
> 홉 규칙이 submit을 늘리는지 — **v4로 돈 run은 0건**이다.
> ⏵ 2026-10-11 — 「PoV 프롬프트는 v4이다」는 2026-10-08의 서술이고 pov 좌석은 2026-10-09 `60df731`부터 v5다(`config/models.yaml`). 위 마지막 홉 규칙의 「안이면 안 하며」는 v4의 서술이다. v5(이슈 #549 ②)는 앞선 gen이 제외 코드 밖을 받은 적이
> 있으면 `submit`의 `poc_sha256`으로 그 gen을 지명해 낸다 — 어느 gen도 못 이겼을 때만 안 낸다.
> 선정됐으나 아직 배선 전인 키가 다시 생기면 이 표에 「배선 전」으로 등재해 두고(「안 쓰는」과 사유가
> 다르다), 배선되면 §1로 올린다.
> 되돌리기는 git 이력으로 한다(항목·checksum manifest·프로필이 한 커밋에 있다). 그날 지운 것 —
> 키 9: `qwen3-4b` · `qwen3-8b`(+`-v2`) · `qwen3-coder-30b-a3b`(+`-v2`) · `devstral-small-2-24b`(+`-v2`) ·
> `deepseek-r1-distill-qwen-32b` · `qwen3-reranker-0.6b` / 프로필 4: `single_gpu_12g` · `local_balanced` ·
> `local_balanced_hybrid` · `b1_single`. 날짜가 붙은 과거 서술(ADR·D·실험 보고서)은 그때의 이름을 그대로 둔다.

---

## 2. 인덱서 — **정본**

**구조 확정 2026-09-09 · 방식 확정 2026-09-13 (조민규).** 발표안 아티팩트의 **Code Slicing** 박스를 이번 리스트럭처 범위에 넣는다.

| | |
| --- | --- |
| 슬라이서 | **Joern 기반** (CPG · CPGQL) |
| 방식 | **무모델 결정론.** Case A(source-first) · Case B(sink-first) **합집합** → 포함률 임계 클러스터링 |
| 모델 | **없다.** 슬라이싱 경로에 모델 호출이 없다 |
| tree-sitter 인덱서 | **계속 남는다.** 전환 대상이 아니라 **짝**이다 — 아래 |

⚠ **2026-09-13에 방식이 바뀌었다. 앞 판본을 인용한 문서는 낡은 것이다.** 그전 정본은
*"`LLM4FPM`(신호 주도) → `LLMxCPG-Q`(쿼리 생성) 순"*이었고 §1에 `qwen3-8b` 쿼리 생성기 자리를
들고 있었다. 실험 결과 **둘 다 쓰지 않는다** — LLM4FPM의 SAST 시작점도, LLMxCPG-Q의 모델 쿼리
생성도 없다. **§1의 「1단계 · 쿼리 생성기」 행이 그래서 사라졌고, 1단계에 모델 자리가 없다.**

**tree-sitter를 왜 계속 두는가 — 둘이 내는 것이 다르다.** Joern은 **슬라이스**(모델이 볼 코드)를,
tree-sitter는 **인덱스**(심볼 표·호출 그래프)를 낸다. 6단계 게이트 E2가 `GRAPH` Evidence를
요구하고([architecture.md](docs/architecture.md) §4), 캡슐의
`callers`/`callees`(`names_for()`)가 그 인덱스에서 나온다. **슬라이스는 그래프가 아니라 그래프의
투영이라 그 자리를 대신하지 못한다** — 「병행」은 전환기 조치가 아니다.

⚠ **2026-09-24 — [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 1: 두 산출물이 어디로
가는지를 다시 정한다.** 슬라이스가 Scout의 코드 입력이고 **후보 = 슬라이스 1개**다 — 위 「슬라이스(모델이
볼 코드)」가 결정이 되고, **위 문장의 「후보 열거(`symbols_for()`)」는 tree-sitter에서 슬라이스로
옮긴다**(승인됐다(2026-09-25) — 그 구절을 위 문장에서 뺐다). tree-sitter 인덱스는 남는 두 쓰임(`GRAPH` Evidence ·
`callers`/`callees`)에 더해 need_evidence resolver(결정 2)의 심볼 표가 된다.
**승인됐다(2026-09-25) — 코드는 아직(Phase 3)이다** — 지금 러너의 후보는 tree-sitter `symbols_for()`의 심볼이고(인덱스가 없으면
파일 하나, `src/orchestrator/runner.py`의 `_plan_targets`) Scout가 받는 코드는 `read_symbol_source()`가
자른 심볼 본문이다. `src/`·`config/`에 슬라이서·`slices.json`을 읽는 코드는 0건이다. 슬라이스 입력은
Phase 3, 심볼 인덱스 v2는 Phase 4, need_evidence 루프는 Phase 5의 몫이다.
(⏵ 2026-09-25 — 슬라이스 입력이 코드에 들어왔다: Phase 3a(PR #283) · 3b(PR #284) · 3c.
`run --slices <slices.json>`이면 슬라이스 1개가 타깃 1개이고(`runner._plan_slice_targets`), Scout의 `SOURCE`는
슬라이스 렌더(줄번호 gutter · 파일 배너), FACTS에 `slice_criterion`이 붙고, LIGHT 캡슐의 캐시 키에 slice id와
슬라이서 `run_signature`가 든다.
Auditor는 슬라이스가 걸친 함수들의 원본을 본다 — entry를 감싸는 함수(품는 함수를 모르면 entry 앞뒤 고정 창(`_AUDITOR_WINDOW_LINES`))가 먼저이고 나머지는 문자 수 상한(`_AUDITOR_SOURCE_CHARS`) 안에서 함수째 붙는다(결정 1-b = 넓은 (i), 2026-09-25 팀 합의). ⏵ 2026-10-03 — 그 결정을 넓혔다: 상한은 20,000토큰이고 함수 뒤에 정의 · 피호출 본문 1단 · 호출자 창 10줄이 붙는다(`config/budgets.yaml`의 #349 옵션 넷 · 서빙 판(⏵ 2026-10-04 — v7 = v6 + 그 서술 · 같은 날 **v8** = v7 + 도구 루프, 그 서술은 바이트 동일 · ⏵ 2026-10-06 v9 = v8 + `auditor_guards` 서술 · ⏵ 2026-10-07 **v10** = v7을 전면 재작성해 그 서술을 다시 썼다 — 바이트 동일이 아니다)이 서술 · 사용자 결정 — [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 1-b의 같은 날 노트). **`--slices` 없이 도는 run은 위 문장
그대로다** — 후보는 `symbols_for()`의 심볼이고 Scout는 심볼 본문을 받는다. 슬라이스 없이 v3 Scout를 부르는
run의 시작 전 거부(결정 1)와 need_evidence 루프(Phase 5)는 이 세 PR에 없다.)
(⏵ 2026-09-26 — need_evidence 루프가 배선됐다(별도 변경, Phase 5): `src/orchestrator/runner.py`의
`_run_ne_hops`가 (슬라이스, 좌석) 세션마다 `max_hops`까지 되묻고, `policy._scout_triage`가 그 신호를
점수보다 먼저 본다. 상세·한계는 [knowledge-base.md](docs/knowledge-base.md) §5.)

⚠ **되면 적용한다.** 「확정」은 *이 구조로 간다*는 뜻이지 *이미 선다*는 뜻이 아니다 — §5가 그
상태를 든다. **그리고 방식을 정한 실험 기록은 아직 이 저장소에 없다** — `docs/experiments/`의
전처리 표 넷은 `LLMxCPG` 행이 전부 `—`(미측정)다. 수치를 인용하려면 ADR 0012 결정 5의 인용 규칙을 따른다(아래 ⚠)
([CLAUDE.md](CLAUDE.md) §0).

⏵ **2026-09-26 — 그 결정의 범위가 좁아졌다**: 하네스를 돌린 run 기록은 `docs/experiments/`에, 하네스 결정·결함의 근거가 되는 조사는 `docs/research/`에 남는다. 아래 ⚠는 실험팀 실험에 대해 그대로 유효하다(그 결정의 ⏵ 표).

⚠ **2026-09-24 — [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 5: 실험팀 실험의 원천을 팀
Notion 워크스페이스로 두고 기록을 이 저장소로 들이지 않는다.** 승인(2026-09-25)으로 위 「그 기록을 먼저 들인다」를
그 결정의 인용 규칙으로 바꿨다 — 수치를 인용할 때 출처(Notion 페이지)와 판독 날짜를 같은 문장에 적고(페이지에
있으면 `run_id`·실제로 서빙한 모델·끝난 시각도) `run_id`가 없으면 「팀 보고, run ID 없음」을 붙이며,
하네스 결정의 근거로 쓰면 요약과 링크를 ADR·D 본문에 남긴다. [CLAUDE.md](CLAUDE.md) §0은 그대로다.
보관 배너(`STATUS.md`·`docs/experiments/README.md`·`docs/research/README.md`)는 이 노트와 같은 PR이 달았다 —
배너와 이 줄은 main에 함께 들어간다.

---

## 3. 로스터를 바꾸는 법 — **여기만 고친다**

팀은 계속 실험하며 로스터를 바꾼다. **바꾸는 비용을 낮게 유지하는 것이 이 파일의 존재 이유다.**

바꿀 때 손대는 곳은 **넷뿐이다**:

1. **이 파일 §1 표** — 정본 선언
2. **`config/models.yaml`** — 새 키 등재, 로스터에서 내려온 키는 지운다(2026-09-24 개정 — §1 끝)
3. **`config/profiles.yaml`** — `roles:` 한 줄씩
4. **[ADR 0010](docs/decisions/0010-harness-structure-and-model-roster.md) §2** — 결정 기록과 근거

**그 밖의 문서는 모델을 현재형으로 이름 대지 않는다.** 이름을 대야 하면 이 파일을 링크한다.
그렇게 해야 로스터가 바뀔 때 21개 문서를 쫓아다니지 않는다 — 2026-09-09에 실제로 그래야 했다.

### ⚠ 넷으로 안 되는 것 둘 — 2026-09-09에 확인했다

이 절은 **모델 키를 자리에 배정하는 것**만 넷으로 만든다. 아래 둘은 그 밖이다.

**(가) 모델에 붙는 실행 인자 — 2026-09-09에 배선했다.** §1의 Critic 행이
`reasoning_effort: high`를 드는데, 그 값이 들어갈 자리가 **스키마에 없었다**
(`ModelEntry`가 `extra="forbid"`이고 요청 조립이 고정 dict였다). 지금은 `ModelEntry`에
`reasoning_effort` 필드가 있고 registry가 선언하면 요청에 실리며 `RunRecord`에도 남는다
(`model_reasoning_effort`) — **선언 안 한 모델은 payload에도 기록에도 키가 안 생긴다.** 그러니 다음 모델에 다른 실행 인자가 붙으면 **그때 또 다섯째
자리를 연다.** 이 절의 넷은 여전히 「자리에 모델을 바꿔 끼우는 비용」이지 「새 손잡이를
여는 비용」이 아니다.

⚠ **기동 설정은 여기 안 들어온다.** `TRITON_ATTN`·flashinfer off·`--enforce-eager`·
`--structured-outputs-config.*`는 하네스가 보내는 값이 아니라 `vllm serve`를 치는 사람의
값이고, 정본 자리는 `config/models.yaml`의 그 항목 「기동 조건」 주석이다.

**(나) 슬라이서(§2).** registry 키도 프로필 칸도 없어서 위 넷 중 어느 것도 안 덮는다.
**2026-09-13에 그 사유가 바뀌었다** — 예전에는 *"고칠 자리가 이 규약에 정의돼 있지 않다"*였고,
지금은 **모델이 아니라서**다(§2 「모델 — 없다」). 슬라이서는 인덱서와 같은 종류다: 컨테이너 도구
하나와 `container_tools`의 도구 이름, 그리고 산출물 경로. 고칠 자리가 `config/models.yaml`이
아니라 그쪽이고, **그래서 로스터를 바꾸는 넷과는 여전히 무관하다.**
⚠ **2026-09-24 — 「컨테이너 도구 하나」는 지금 사실이 아니다.** 슬라이서는 하네스 밖에서 손으로 도는
오프라인 스크립트(`slicer.py` + Joern 서버 — ⏵ 2026-10-03 루트에서 `tools/slicer/`로 옮겼다)이고 `container_tools`에 그 이름이 없다 — `src/`·
`config/`에 슬라이서를 부르거나 `slices.json`을 읽는 코드가 0건이다. [ADR 0012](docs/decisions/0012-harness-canon-v2.md)
결정 1은 컨테이너 도구가 아니라 하네스 밖에서 미리 돈 `slices.json`을 파일로 받는 쪽이다 — 하네스는
슬라이서를 import하지 않는다(구현은 Phase 3). 로스터의 넷과 무관하다는 결론은 그대로다.
(⏵ 2026-09-25 — `slices.json`을 읽는 코드가 생겼다: `src/adapters/slices.py`가 읽고 렌더하며(PR #283)
`run --slices`가 그 파일을 넘긴다(PR #284 · 3c). 슬라이서를 **부르는** 코드는 여전히 0건이다 — 파일로만 받는다.)

**둘 다 「로스터를 바꾸는 비용」이 아니라 「로스터를 처음 세우는 비용」이다** — 자리에
모델을 바꿔 끼우는 일은 여전히 넷이다. 다만 이 파일만 읽고 "넷이면 끝난다"고 읽지 마라.

⚠ **roles를 잠근 대조 쌍은 같은 diff에서 고친다.** `harness_pool`↔`harness_pool_hybrid`다 —
`retriever` 하나만 달라야 검색 경로 효과가 갈리고, 그래서 검사가 roles·실행 단위 동일을
강제한다(그 강제가 없는 `dev_fake`/`dev_hybrid`는 이 규칙 밖이다 — ⏵ 2026-09-29부터 교대 선언은
`harness_pool`에만 있다. hybrid는 검색 역할 때문에 선언할 수 없다). `tests/test_config.py`의
`test_the_pool_hybrid_profile_differs_from_harness_pool_in_the_retriever_and_swap`가
`variant.roles == base.roles`를 단언한다 — 한쪽만 고치면 CI가 잡는다. (옛 쌍 `local_balanced`↔
`local_balanced_hybrid`와 그 검사는 2026-09-24에 지웠다.)

---

## 4. 이 파일이 정하지 **않는** 것

| | 어디가 정본인가 |
| --- | --- |
| 파이프라인 0~9단계 · Evidence Gate E0~E4 | [architecture.md](docs/architecture.md) §3·§4 — **이 리스트럭처가 안 바꾼다** |
| 실행 단위(후보 단위 ↔ 단계 단위) · 모델 교대 | [ADR 0009](docs/decisions/0009-stage-unit-execution-and-model-swap.md) |
| 결함의 상태 | `docs/open-questions/D/D<n>.md` — [ADR 0008](docs/decisions/0008-defect-source-of-truth.md) |
| 실측값 | `config/models.yaml`의 `checksum`·`benchmark`. **0은 미실측 표시다**([CLAUDE.md](CLAUDE.md) §0) |
| 무엇을 어느 순서로 짓는가 | [build-plan.md](docs/build-plan.md) |

---

## 5. 아직 안 선 것 — 로스터와 별개다

| 항목 | 상태 |
| --- | --- |
| `gpt-oss-20b` structured output | **실패.** [2026-09-09 온보딩 보고서](docs/experiments/harness/2026-09-09-gpt-oss-20b-onboarding-atom.md) — `xgrammar` 5/5 오답 · `guidance` 5/5 HTTP 500 · 스키마 없이는 3/3 정답. **2026-09-11에 아홉 팔로 축 다섯을 갈랐고 전부 기각이다**([9팔 보고서](docs/experiments/harness/2026-09-11-gpt-oss-structured-vocab-flag-atom.md)) — `reasoning_parser`·어휘 넷·**enum 순서**·**속성 순서**·**백엔드 `outlines`**(5/5 HTTP 500). **같은 JSON 안에서 `reason`이 "clear use-after-free"라 쓰고 `verdict`가 `safe`다.** 자유서술은 27/27 정답 — **틀리는 건 enum 토큰 하나다.** ⚠ **미측정은 `enable_in_reasoning`·enum 제약 제거·케이스 축이고, (가) 2단 추출은 아직 안 쟀다** — ADR 0010 「다시 열리는 조건」이 (나) 교체를 그 뒤로 뒀다. ⚠ **2026-09-20 — 이 중 「enum 제약 제거」는 낡았다.** `0148719`(#182, `2026-09-13 01:59`)가 그 축을 재서 **enum 강제 2/5 ↔ enum 제거(자유 문자열) 5/5**를 냈고 rationale은 양쪽 다 정답이었다 — 그 수치는 `src/schemas/enums.py`의 `Verdict.from_text` docstring에 실려 있다. 하네스도 이미 그대로 구현돼 있다(`DecisionDraft` + `Verdict.from_text`, `src/orchestrator/nodes/critic.py`). ⚠ **그래도 「고쳐졌다」로 읽지 않는다** — #182가 스스로 *"원리는 enum-제거 프로브(arm B 5/5)로 검증됨. 하네스 스키마 그대로를 gpt-oss에 보내는 **통합 확인은 프로필 배선(R8) 뒤 follow-up**"*이라 적었고 그 확인이 아직 없다. 이 줄(`6970fa8`, 같은 날 13:55)이 그 실측보다 **12시간 늦게 쓰였는데** 반영을 놓쳤다([CLAUDE.md](CLAUDE.md) §2.4) |
| `qwen3-reranker-4b` | 2026-09-11 등재(다운로드·checksum). ⚠ **2026-09-20 — 이 행이 적던 「서빙·점수 정확성 미검증 / 아직 프로필엔 안 올린다」가 둘 다 움직였다.** ① **기동은 2026-09-13에 됐다** — ATOM 8005에 `--hf-overrides`로 떠서 `/v1/rerank` 200이다([입력 모양 보고서](docs/experiments/harness/2026-09-13-d63-reranker4b-input-shape-atom.md) 메타 표. [R8](docs/open-questions/R/R8.md)의 그날 주가 그 사실을 장부에 옮겼다). **미검증으로 남은 것은 점수 정확성**이고 그것이 [D63](docs/open-questions/D/D63.md)(`닫힘` 2026-10-12 — 캡슐 related 경로 전제 소멸, 4b 실경로 순위는 미실측)가 재측정 대상이던 것이다. ② **두 풀 프로필**(`local_balanced_pool`·`local_balanced_pool_hybrid`)이 §1대로 이 모델을 세웠다. **`local_balanced`·`local_balanced_hybrid`·`b1_single` 셋은 그대로 `qwen3-reranker-0.6b`다** — `local_balanced` 쌍과 풀 쌍은 서로 안 묶여 있고(`tests/test_config.py`가 잠그는 것은 각 쌍의 **내부**다) `b1_single`은 짝이 없다. (⏵ 2026-09-24 — 그 셋과 `qwen3-reranker-0.6b` 키는 로스터 밖 정리로 지웠다(§1 끝). 두 풀 프로필은 `harness_pool`·`_hybrid`로 이름이 바뀌었고, 실모델 리랭커를 부르는 프로필은 `harness_pool_hybrid` 하나다 — `dev_hybrid`·`dev_hybrid_docker`는 `fake` 리랭커다.) ⚠ **「올렸다」를 「검증됐다」로 읽지 않는다** — D63 배제셋 둘(참조 구현 대조·리랭커를 끈 기준선)이 남았다(D63 닫힘 — 되살릴 때 새 이슈) `benchmark`는 여전히 전부 `0`(미실측 표시)이다. **그리고 D63 본문이 이 배선을 말렸다** — *"0.6b→4b로 지금 그대로 배선하는 것은 이르다"*. 그것을 알고 §1을 따른 것이고 근거는 D63의 2026-09-20 블록에 적었다 |
| Auditor(~~`qwen3.6-35b-a3b`~~ → `qwen3.8-27b`) | ~~Auditor A/B — 실측 전~~ → **2026-09-24 — A/B 갈래는 소멸했다**(§1 「Auditor의 A/B가 소멸했다」). 이 행이 2026-09-11부터 들던 「A/B」가 2026-09-22 #240이 §1을 바꿀 때 안 따라왔다. 지금 자리는 `qwen3.6-35b-a3b` 하나이고 **2026-09-21 회의의 교체다(근거는 실험팀 2026-09-20 선정 — Notion, 팀 보고). 품질 실측이 아니다** — registry의 `checksum`이 자리표시자(0)이고 `source`에 `@rev`가 없으며 `benchmark`가 전부 0/null이다(`config/models.yaml`). ⏵ **2026-09-26 — `@rev`는 박았다**(#294 — `qwen3.6-35b-a3b@995ad96`·`seccoderx-8b@25d7f38`, 2026-09-24 스모크 서빙 리비전 = HF 현재 main). 토크나이저가 실카운터로 뜨고(캐시·라이브러리 있으면) 선언 샘플링 기록도 켜진다; `checksum`·`benchmark`는 여전히 0이다. 프롬프트는 **v1**이다 — `prompts/auditor/v3`는 디스크에만 있고 어느 registry 엔트리도 그 버전을 안 고른다(그 `meta.yaml` 머리 주석). ⏵ **2026-09-29 — v3로 올렸다**(근거 본문 배선과 같은 변경 — `src/orchestrator/nodes/auditor.py`의 `_CONTENT_AWARE_VERSIONS` · `config/models.yaml`의 `qwen3.8-27b`). 이 칸의 v1 서술과 v1로 잰 것은 그 전의 기록이고, 그 뒤 run과 직접 비교하지 않는다([agent-contracts.md](docs/agent-contracts.md) §4 규칙 1). v3는 `trigger_status`를 묻는다 — D51이 기다리던 「그 필드를 묻는 프롬프트」다. v3로 돈 run은 아직 0건이다(`docs/experiments/` grep, 2026-09-29). ⏵ **2026-09-29 — 같은 날 v4로 올렸다**(#328 머지 뒤 — v4도 `_CONTENT_AWARE_VERSIONS`에 들어 근거 본문을 받고, `trigger_status`를 묻는다). v3로 돈 run은 스모크 1건뿐이었고([스테이지 교대 스모크](docs/experiments/harness/2026-09-29-stage-swap-smoke-atom.md) — Auditor 2호출, 비교표 밖), v4로 돈 run은 아직 0건이다. [D24](docs/open-questions/D/D24.md)·[D51](docs/open-questions/D/D51.md)이 여기 종속이고, 둘 다 2026-09-21 절에서 남은 입력을 「이 단일 모델의 run」으로 적는다. ⏵ **2026-09-24 — 처음 떴다**([스모크 보고서](docs/experiments/harness/2026-09-24-roster-smoke-atom.md) · 비교표 밖). parser 없이 `xgrammar`·`disable_any_whitespace`로 띄우면 `Finding` 36/36이다. 다만 구조화 출력 문법이 첫 토큰부터 걸려 **사고 없이** 답한다. parser를 걸면 사고가 `output_max` 4,096 안에 안 끝나 0/7이다. 기동 조건은 `config/models.yaml`의 이 항목에 짝 설정 PR #262가 적는다. **품질 실측이 아니라는 것은 그대로다.** D24·D51이 기다리던 입력으로는 두 D의 같은 날 절이 판정한다 — D24에는 역할별 비용의 첫 수, D51에는 입력이 아니다(v1은 `trigger_status`를 안 묻는다) ⏵ **2026-09-26 — Auditor가 `qwen3.8-27b`로 바뀌고 사고를 켰다**(§1). 이 칸의 서술(2026-09-24 스모크 포함)은 `qwen3.6-35b-a3b`의 기록이다 — 새 모델은 우리 장비 실측이 0건이고 `checksum` 0이다. 고른 근거는 벤더 모델 카드뿐이다 `[출처주장]`. ⏵ **2026-10-03 — 프롬프트를 v4에서 v6로 올렸다**(프롬프트 #418 · 배선 #421 · 사용자 결정). **v5는 건너뛰었다** — v5 배선 PR #390이 대여 세션 측정까지 주차돼 있었고 그 PR은 닫혔다. v6도 `_CONTENT_AWARE_VERSIONS`에 들어 근거 본문을 받고 `trigger_status`를 묻는다. **v4로 돈 run은 끝까지 0건이었다**(`docs/experiments/` grep, 2026-10-03). ⚠ **v6를 「v5 + 추가」로 읽지 않는다** — v5의 범위 제약 둘(`Do not invent unrelated issues` · `Do not expand the analysis to unrelated vulnerabilities …`)을 **삭제**해 Scout 가설과 무관한 독립 판정으로 돌린 것이 이 판의 핵이다. #418이 든 수는 **Recall 0.637→0.966 · Precision 0.939→0.821 · FP 6→31**이고 **원천은 그 PR 본문의 비교표다 — 이 저장소의 run 기록이 아니다**(실험팀 프롬프트 A/B · 원천은 Notion, [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 5). **v6로 돈 run도 0건이다.** ⏵ **2026-10-04 — v7로 올렸다**(#349 · PR #443 리뷰 HIGH). v7 = v6 + SOURCE 옵션 넷(`config/budgets.yaml`)의 입력 서술 + meta `source_options`(⏵ 같은 날 래퍼·별칭 너머 · 간접 피호출 · 쓰기 지점 창 서술을 더해 지금 v7이 서술하는 옵션은 일곱 · #451 · #304 — `prompts/auditor/v7/meta.yaml`) — 나머지 지시·body는 v6와 같고 `_CONTENT_AWARE_VERSIONS`에 든다. v6를 그 자리에서 고치지 않은 것은 #418의 수가 v6 문장에서 나왔기 때문이다(agent-contracts §4 규칙 1) — **그 수를 v7에 옮겨 읽지 않는다.** v7로 돈 run도 0건이다. ⏵ **같은 날 — v8로 올렸다**(도구 루프, #419). v8 = v7 + 홉 프로토콜 절이고, v7이 「이 호출은 한 방이다」라고 단언하던 열두 줄과 캡슐이 빠뜨린 사실에 `reachability_unknown`을 쓰라던 문장 하나(홉 절의 「먼저 `lookup`」과 맞추는 단서)만 다시 썼다 — 열세 줄이다. `SOURCE` 서술과 trigger rule은 **바이트 동일**이다(`tests/test_auditor_prompt_v8.py`가 그 동일성을 센다). 매 홉 `AuditorAction`(`lookup`·`probe`·`answer`)을 내고 `config/budgets.yaml`의 `thresholds.auditor_tool_hops`가 16이다 — **그 16은 실험팀·사용자 판단이고 이 하네스에서 안 쟀다**(홉당 usage 0 관측 · 창이 드는지도 미실측). **v8로 돈 run도 0건이다.** ⚠ v6·v7의 수를 v8에 옮겨 읽지 않는다. ⏵ **2026-10-06 — v9로 올렸다**(v8 + `auditor_guards` 서술 한 항목 — 그 밖은 바이트 동일, `tests/test_auditor_prompt_v9.py`가 센다). **v9로 돈 run도 0건이다.** ⏵ **2026-10-07 — v10으로 올렸다**(배선 `21b9513` 01:58 KST · PR #521 — ⏵ 2026-10-09 정정: 전에는 「같은 날(10-06)」로 적었다, #557)(v7을 전면 재작성한 판이다 — v8·v9처럼 앞 판에 한 항목을 더한 것이 **아니고 바이트 동일도 아니다**. 분석 절차(점검 다섯 · 우선순위 · 트리거 규칙)와 출력 계약을 새로 썼고 `body`만 같다). 도구는 v8·v9와 같은 홉 구조이지만 **출력 계약이 다르다** — 매 홉 봉투 `AuditorProcedureAction`(`action`·`requests`·`tool`·`spec`·`procedure`·`rationale`)을 내고, 최종 답은 `Finding`이 아니라 그 `procedure`에 드는 `AuditProcedure`(여덟 필드 전부 required · 새 `audit` 블록 하나)다. 하네스가 그것을 `Finding`으로 옮기고 발급 ID 넷(`finding_id`·`run_id`·`candidate_id`·`state`)·`missing_evidence`·`produced_by`를 **모델에게 묻지 않고** 채운다. 그래서 `output_schema` 허용집합은 `Finding`·`AuditorAction`(v8·v9)·`AuditorProcedureAction`(v10) 셋이고 `AuditProcedure`는 payload라 그 집합에 없다. v10도 `_CONTENT_AWARE_VERSIONS`에 들어 근거 본문·위치를 받고, meta `source_options`는 여덟이며 켜진 옵션 여덟과 같다(`auditor_guards` 포함). prefix 글자 수는 v7 25,785 · v8 33,209 · v9 35,501 · **v10 56,906**(`load_prompt(...).prefix`의 길이를 2026-10-06 이 저장소에서 쟀다 — body는 네 판 모두 282)라 v10은 v9보다 **+21,405자**다(그 수는 `tests/test_auditor_prompt_v10.py`가 핀으로 든다). ⚠ **토큰으로 환산한 수는 안 쟀다.** ⚠ v6·v7·v8·v9의 수를 v10에 옮겨 읽지 않는다(agent-contracts §4 규칙 1 — 문장이 다르다). **v10으로 돈 run은 0건이다**(`git grep -i v10 -- docs/experiments` 0건, 2026-10-06) ⏵ **2026-10-08 — v10으로 돈 run이 1건이 됐다**([10-08 run](docs/experiments/harness/2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md) — `checksum` 0이라 비교표 밖). `Finding`에 `audit` 필드가 생겼지만 `SkipJsonSchema`라 요청 스키마에 안 실린다 — `Finding`·`AuditorAction`의 `model_json_schema()`는 변경 전후 sha256이 같아 **v1~v9의 요청 바이트는 안 바뀌었다**(2026-10-06 스냅샷 — 대조 방법은 agent-contracts 「AuditProcedure」 절). ⏵ **2026-10-09 — v11로 올렸다**(이슈 #525 선택지 1 · 사용자 결정 · [D107](docs/open-questions/D/D107.md)). v11 = v10 + `no_signal`의 `cwe`를 null로 내라는 서술(prefix `cwe` 서술 네 줄 — 그 밖의 prefix·body는 v10과 바이트 동일, `tests/test_nullable_cwe.py`). 매 홉 봉투는 형제 `AuditorProcedureNullableCweAction`이고 최종 답 `AuditProcedureNullableCwe`의 `cwe`가 `no_signal`에서만 null일 수 있다 — 그래서 `output_schema` 허용집합은 넷이다. v10을 그 자리에서 고치지 않은 것은 v10의 요청 바이트가 `AuditorProcedureAction`의 JSON 스키마라서다(agent-contracts §4 규칙 1). `Finding.cwe`도 `no_signal`에서만 `None`을 받되 요청 스키마는 그대로라 `Finding`·`AuditorAction`·`AuditorProcedureAction`의 `model_json_schema()`가 넓히기 전 `origin/main`(a78b09f)과 sha256이 같다. v11도 `_CONTENT_AWARE_VERSIONS`에 든다. **v11로 돈 run은 0건이다.** ⏵ **2026-10-10 — v12가 디스크에 섰다. 서빙 판은 그대로 v11이다**(이슈 #578 · 팀 결정 2026-10-10: `no_signal`도 `audit`를 채운다). v12 = v11 + `audit`를 모든 상태에서 채우라는 서술 — FT 데이터 Teacher v4의 계약이고 FT Auditor를 서빙할 때 고를 판이다(그 전환은 그때의 결정). 봉투는 형제 `AuditorProcedureAuditAlwaysAction`이라 허용집합은 다섯이다. `no_signal`이면 `category` `none` · `defense.status` `not_applicable` · `refutation.result` `no_candidate` 셋 다이고 다른 상태에는 셋 다 못 쓴다(하네스가 검증). v10·v11의 요청 바이트는 안 바뀌었다(`tests/test_auditor_v12.py`가 sha256으로 문다). **v12로 돈 run은 0건이다.** ⏵ **2026-10-10 — v13으로 올렸다**(이슈 #67 · 저장소 소유자 결정 · [D17](docs/open-questions/D/D17.md)). v13 = v11 + FACTS에서 `source` 사본을 뺀다 — 원문은 산문 SOURCE 블록 하나로 가고 FACTS는 식별자·근거를 든다(meta `facts_source: false`). 산문은 v11에서 두 줄만 다르다(body의 Facts 머리 문장 · prefix의 `FACTS.source` 한 자리 — `tests/test_auditor_v13_facts.py`). 봉투·도구·SOURCE 옵션은 v11과 같다. v12는 열린 PR #580이 쓰는 번호라 건너뛰었다. ~~**v13으로 돈 run은 0건이다.**~~ ⏵ **2026-10-11 — v13 Auditor 호출이 있는 run은 넷이 됐다**(`run_20261010_0002`·`0003`·`0004` · `run_20261011_0001` — Auditor `model_call` 1·14·8·20건 · [10-11 run](docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md) §1 · `checksum` 0이라 전부 비교표 밖). **0~9단계를 완주한 것은 `run_20261011_0001` 하나**이고 Auditor가 Finding 5건을 냈다. 서빙 판이 v11에서 v13으로 올라가 v11과 v12는 둘 다 디스크에만 있다(미선택). ⏵ **2026-10-11 — v14로 올렸다**(이슈 #67 · #578). v13은 같은 변경을 v11 위에 얹어 봉투가 v11 것(`no_signal`이면 `audit` null)이라 v12 계약(`no_signal`에도 `audit`)인 FT 정답과 어긋났다. v14 = v12 + FACTS에서 `source` 사본을 뺀다 — body는 v13과 바이트 동일, prefix는 v12와 한 자리만 다르다(`tests/test_auditor_v14_facts.py`). 봉투는 v12의 `AuditorProcedureAuditAlwaysAction`. v13의 요청 바이트·봉투는 그대로다(meta 주석만 바뀌었다 · 규칙 1). **v14로 돈 run은 0건이다**(`docs/experiments/` grep, 2026-10-11). v11·v12·v13은 디스크에만 있다(미선택). ⏵ 2026-10-10 — 위에서 이 칸에 종속이라 적은 [D51](docs/open-questions/D/D51.md)은 닫혔다: 열거 강제는 서빙 판(v10 이상)의 응답 스키마가 맡고 `Finding.trigger_status`는 `str` 그대로다. |
| **모델 배선(교대 · 사고 · 동시성)** | **교대는 배선됐다 — 실장비 종단은 미실측이다**(ATOM 먼저 · vast.ai는 도구 단계를 어디서 돌릴지 미정, 2026-09-29 — 이 칸 끝). ⏵ **2026-09-29 — ATOM에서 한 번 돌았다**([스모크 보고서](docs/experiments/harness/2026-09-29-stage-swap-smoke-atom.md)) — 교대 네 번 전부 `ok=true`로 완주했다. 슬라이스 1개·좌석 3개(n=1)이고 비교표 밖이다. 위 「미실측」은 ATOM에는 이제 낡았다 — vast는 여전히 미실측이다. ⏵ 2026-10-12 — 「vast는 여전히 미실측」도 낡았다: vast 교대 종단은 2026-10-03에 처음 성립했고(`config/profiles.yaml`의 ⏵ 2026-10-08), 기동 콜드 ↔ 따뜻한 쌍은 [10-11 보고서](docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md) §2가 든다. 단계 모델에 메모리 몰아주기는 장비 파일의 util로 들어왔다(계획값). 단계 안 동시성·로더는 아직이다. ⏵ 아래는 2026-09-24의 서술이다 — **방식은 정했고 배선은 아직이다.** 2026-09-24 조민규 — 로딩·배선 조사가 권한 방식으로 간다. 단계 단위로 서버를 완전히 끄고 켜는 **콜드 재기동**이다(sleep/wake가 아니다). 여기에 다음을 더한다 — Auditor 사고 켜기(parser `qwen3` + `thinking_token_budget`) · 단계 안 동시 요청 · 단계 모델에 메모리 몰아주기(Auditor util 0.80) · 로더 `fastsafetensors` · 교대 기록(`model_swap`에 실제 기동 인자)·장비 배타·마감 일원화. **착수는 지금 열린 PR을 모두 머지한 뒤다.** 지금 저장소는 셋이다. `src/`에 교대 호출이 0건이다(단계 단위 루프만 있다 — [ADR 0009](docs/decisions/0009-stage-unit-execution-and-model-swap.md) 결과 3). 단계 안 요청은 순차다(`runner._run_stage_for_all`). Auditor 기동 조건은 사고 없이 답하는 판이다(§1 「모델별 정밀도 · 추론(사고)」). 모델·추론·배선 요약은 팀 Notion 「로스터 현황」(https://app.notion.com/p/3e4bec1a15bc802ca8b2f50cc7f0c9e3)에 둔다 ⏵ **2026-09-26 — 사고 켜기의 요청 쪽이 들어왔다.** Auditor가 `qwen3.8-27b`로 바뀌며 registry가 `reasoning_effort: xhigh`·`thinking_token_budget` 28,672·`request_timeout_s` 7,500을 보내고 역할 출력 상한이 32,768이다(계산값 — 미실측). ⏵ **2026-10-03 — 그 둘이 올랐다**: 사고 상한 57,344 · 출력 상한 65,536이고 타임아웃 7,500은 그대로다(위 §1 Auditor 행). 기동 조건은 parser `qwen3`를 건 계획값이다(`config/models.yaml`의 그 항목). 위 「Auditor 기동 조건은 사고 없이 답하는 판이다」는 옛 `qwen3.6-35b-a3b`의 서술이다. 교대·단계 안 동시성·메모리 몰아주기·로더는 그대로 아직이다. ⏵ **2026-09-29 — 교대의 하네스 쪽이 들어왔다** — 러너가 단계 경계에서 교대 콜러블을 부르고 run 끝에 내린다(`runner._swap_for`) · 교대 어댑터(`src/adapters/model_swap.py`) · 장비 파일 스키마(`config/serving/<device>.yaml`) · `model_swap` 기록. 모양은 [ADR 0009](docs/decisions/0009-stage-unit-execution-and-model-swap.md) 「2026-09-29 제안 — 단계 단위 콜드 재기동」 절이고 **제안이다(손수민 사인오프 필요).** 교대 명령(`tools/serve/swap.sh` — 장비 무관)·장비 파일(`config/serving/atom.yaml`·`vastai.yaml`)·`harness_pool`의 `swap` 선언도 같은 날 들어왔다 — **실장비 종단은 미실측이다**(ATOM 먼저 · 장비 파일 값은 계획값). 위 「`src/`에 교대 호출이 0건이다」는 그래서 낡았다. 단계 안 동시성·메모리 몰아주기·로더는 그대로 아직이다. |
| **Critic v6 · 근거 본문·SOURCE** | **2026-10-05 — 실 Critic `gpt-oss-20b`은 v6이다**(#497). `config/models.yaml`의 판과 `nodes/critic._CONTENT_AWARE_VERSIONS`가 근거 본문·위치를, `_SOURCE_INPUT_VERSIONS`가 SOURCE를 정한다. `config/profiles.yaml`에서 직접 센 critic 좌석은 `fake`를 공유하는 `dev_*` 다섯(v1)과 `gpt-oss-20b`을 쓰는 두 풀 프로필(v6)이다. v3는 디스크 전용이다. 이전 v1 해제의 확인 run 선행은 `prompts/critic/v3/meta.yaml`의 2026-10-05 교정대로 유지하지 않는다. v6은 Auditor 최초 읽기를 캐시한 SOURCE를 원문 블록과 FACTS에 싣고 Critic·PoV가 공유한다. 실모델 v6 입력 토큰·판정 품질은 0(미실측)이며 배선으로 판정 결과를 보장하지 않는다. |
| Joern 슬라이서 | [독립 오프라인 구현](docs/slicer-README.md)을 추가했다. **실서버 질의·실대상 커버리지는 2026-09-15에 실측됐다** — ATOM Joern 4.0.605에서 fixture 재개 동등성과 표본 3종 완주를 확인했다([보고서](docs/experiments/harness/2026-09-15-joern-slicer-v2-atom.md), [D77](docs/open-questions/D/D77.md) 닫힘). ⚠ **재개의 바이트 동등성은 fixture 규모에서만** 쟀고, 절단된 Case A의 `analysis`는 CPG를 다시 만들면 같지 않다(보고서 §4.5). **하네스 소비 경로는 2026-09-18에 (a) 후보 열거에만으로 확정됐다**(→ **2026-09-24 — ADR 0012 결정 1이 이것을 대체한다** — 이 칸 끝) — ~~캡슐 본문·확장 루프·`Location`·게이트를 하나도 안 건드린다~~([D74](docs/open-questions/D/D74.md) 닫힘). ⚠ **확정이지 배선이 아니다** — 평가 데이터셋 40샘플 슬라이싱이 ATOM 서버 drain으로 중단돼 `slices.json`이 없다(**팀 구두 보고 · `run_id` 없음** — 실측으로 인용하지 않는다). 캡슐 경로((b)~(d))의 선행조건은 [D78](docs/open-questions/D/D78.md)이고, 그중 E1 STATIC 하나는 **(a)의 배선 PR도 슬라이스를 Evidence로 적으면 밟을 수 있다** — D74가 그것을 「배선 PR이 지킬 것」으로 금지한다. ⚠ **2026-09-24 — [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 1: 이 (a)를 대체한다** — 조민규 결정(2026-09-23)으로 슬라이스가 **Scout의 코드 입력**이 되고 후보 = 슬라이스 1개다. 승인(2026-09-25)으로 위 「캡슐 본문·`Location`을 안 건드린다」를 계획에서 뺐다(취소선) — [D78](docs/open-questions/D/D78.md) ①②③의 처리 방향을 그 결정이 든다: `Location`은 대표 위치(entry)에 `Candidate.slice_id`를 더하고, 캡슐 캐시 키에 slice id와 슬라이서의 `run_signature`를 넣고, 슬라이스·RETRIEVED는 Evidence로 적지 않는다(위 E1 STATIC 금지는 그대로 선다). Auditor가 받을 표현(원본 소스 ↔ 슬라이스)은 열려 있다(결정 1-b). **승인됐다(2026-09-25) — 배선은 아직이다** — `src/`·`config/`에 슬라이스를 읽는 코드가 0건이고 구현은 Phase 3이다. 위 「`slices.json`이 없다」는 2026-09-18 팀 구두 보고이고, 그 뒤 슬라이싱 **진행 보고**의 원천은 Notion이다(결정 5 — 실험 기록의 원천. `slices.json` 자체는 결정 1이 하네스가 파일로 받는 입력이라 이 규칙 밖이다). ⏵ 2026-09-25 — 배선이 들어왔다(Phase 3a PR #283 · 3b PR #284 · 3c): `run --slices`면 슬라이스 1개가 타깃 1개이고, Scout는 그 렌더(줄번호 gutter · 파일 배너)와 `slice_criterion`을, Auditor는 entry를 감싸는 함수 하나의 원본(품는 함수를 모르면 entry 앞뒤 고정 창(`_AUDITOR_WINDOW_LINES`))을 받는다. 슬라이스는 Evidence로 적지 않는다. `--slices` 없는 run의 후보는 그대로 tree-sitter 심볼이다. ⏵ 같은 날 #288 — 슬라이스 타깃 캡슐의 그래프 섹션이 슬라이스의 줄 집합을 본문으로 세고 「슬라이스는 Evidence가 아니다」가 출처 테스트로 잠겨 [D78](docs/open-questions/D/D78.md)이 닫혔다. ⏵ 같은 날 결정 1-b가 넓은 (i)로 정해졌다(팀 합의) — Auditor는 슬라이스가 걸친 함수들의 원본을 entry 함수부터 문자 수 상한 안에서 받는다(`runner._auditor_source`) |
| **`reasoning_effort: high` 배선** | **배선(2026-09-09) + registry 등재(2026-09-11) 둘 다 됐다.** `ModelEntry.reasoning_effort` → 요청 payload → `RunRecord.model_reasoning_effort`이고, `gpt-oss-20b`가 §1 표대로 등재돼 `reasoning_effort: "high"`를 선언한다. ⚠ **그래도 이 자리는 안 선다** — **앞에 이 표 첫 줄(structured output)**이 남았고, ~~프로필의 `critic`도 아직 `devstral-small-2-24b`다(아래 「실서빙 프로필 ↔ §1」 행).~~ (⏵ 2026-09-24 — 그 프로필들은 로스터 밖 정리로 지웠다. 실모델 프로필 `harness_pool`·`_hybrid`의 critic은 `gpt-oss-20b`다.) 배선과 등재가 여는 것은 「선언하면 값이 실제로 간다」까지이지 「Critic이 뜬다」가 아니다. ⚠ **2026-09-20 — 「프로필의 `critic`도 아직 devstral」은 실서빙 프로필에만 맞는다.** `config/profiles.yaml`의 `critic:`을 전수로 세면 `fake`가 여섯(`dev_*` 다섯 + `single_gpu_12g`), `local_balanced`·`local_balanced_hybrid`가 `devstral-small-2-24b`, `b1_single`이 `qwen3-coder-30b-a3b`, 그리고 **`local_balanced_pool`·`local_balanced_pool_hybrid`는 이미 `gpt-oss-20b`**다(`4681efe`, #190, `2026-09-13 17:08`). 아래 「실서빙 프로필 ↔ §1」 행이 그 provisional 배선을 이미 적는다. (⏵ 2026-09-24 — 이 셈의 `single_gpu_12g`·`local_balanced`·`local_balanced_hybrid`·`b1_single`은 로스터 밖 정리로 지웠고, 풀 둘은 `harness_pool`·`_hybrid`로 이름이 바뀌었다.) |
| **실서빙 프로필 ↔ §1** | ~~**실서빙 프로필(`local_balanced` 등)은 아직 §1과 다르다** — 단일 `scout`·`critic: devstral-small-2-24b`·리랭커 `qwen3-reranker-0.6b`.~~ → ⏵ **2026-09-24 — 그 프로필들은 지웠다**(로스터 밖 정리 — §1 끝). 실모델 프로필은 `harness_pool`·`_hybrid` 둘이고 §1과 일치한다(표 아래 문단). 이 행의 나머지는 그 전의 경과다 — 그 안의 「실서빙 `local_balanced` …」 현재형은 전부 정리 전 상태다. ⚠ **2026-09-11부터 이유가 자리마다 다르다** — Critic(`gpt-oss-20b`)·리랭커(`qwen3-reranker-4b`)는 **등재됐으니** 올릴 수는 있고 남은 선행이 각각 이 표 첫 줄(structured output)·리랭커 행(서빙·점수 검증)이다. ~~Auditor B(`nemotron-3-super-120b-a12b`)만 **미등재**라 못 올린다.~~ → **2026-09-24 — 이 사유는 없어졌다.** 2026-09-21 교체로 Auditor가 `qwen3.6-35b-a3b` 한 자리가 되며 B안이 §1에서 내려왔다(§1) — 2026-09-22 #240이 이 행 끝에 교체 노트를 달면서 이 문장은 안 고쳤다. **단 2026-09-13에 실험용 `local_balanced_pool` 하나가 §1 목표를 provisional 배선했다 — Scout 풀(v2 별칭) + Critic `gpt-oss-20b`.** #178 팬아웃 플러밍을 실모델로 처음 채우고 Critic도 §1의 gpt-oss로 올렸다. **선정·검증은 미확정**(풀 도달성 #103 · Critic 반박 정확도·DecisionDraft→gpt-oss 통합 #182). `production: false`이고 실서빙 `local_balanced`(critic devstral)는 안 건드렸다. ⚠ **2026-09-20 — 이 행에서 넷이 움직였다.** ⓪ **리랭커의 「남은 선행」이 둘 중 하나로 줄었다** — 서빙은 2026-09-13에 확인됐고(위 리랭커 행) **점수 검증만 남았다.** ① **「리랭커 `qwen3-reranker-0.6b`」가 더 이상 균일하지 않다.** `reranker:`를 전수로 세면 **11 프로필 전부가 그 자리를 선언하고** `fake`가 여섯(`dev_*` 다섯 + `single_gpu_12g`), `local_balanced`·`local_balanced_hybrid`·`b1_single`이 `qwen3-reranker-0.6b`, **`local_balanced_pool`·`local_balanced_pool_hybrid`가 `qwen3-reranker-4b`**다. 실제로 리랭커를 **부르는**(`retriever: hybrid`) 실모델 프로필은 **둘**이고 각각 0.6b·4b다. ② **리랭커의 남은 선행은 풀 프로필에는 더 이상 블로커가 아니다** — 위 리랭커 행이 그 사정을 든다(검증은 그대로 열림이고, 막는 것이 아니라 **알고 넘은** 것이다). 실서빙 `local_balanced` 쌍에는 그대로 선행이다. ③ **provisional 배선 열거가 한 자리 늘었다** — 「Scout 풀 + Critic `gpt-oss-20b`」에 **리랭커 `qwen3-reranker-4b`**가 더해진다. 그리고 *"`local_balanced_pool` **하나가**"*도 2026-09-13 시점의 수다 — 그날 `local_balanced_pool_hybrid`가 같이 들어와 **둘**이다. ⚠ **2026-09-21 — §1 로스터가 교체됐고 두 풀 프로필이 그 §1을 그대로 배선했다(#233).** §1의 Scout 셋이 `seccoderx-8b`(memory·boundary 한 키)·`deepseek-r1-distill-qwen-32b`(injection, 모두 v3)이 되고 Auditor가 `qwen3.6-35b-a3b`가 되면서, 두 풀 프로필의 전 역할이 §1과 **일치한다**(auditor는 프롬프트 미확정이라 디스크의 v1을 고른다 — ⏵ 2026-09-29 v4가 됐고(같은 날 v3에서 올렸다) ⏵ **2026-10-03 v6**(#418·#421) ⏵ 2026-10-04 v7(v6 + SOURCE 서술, #349) ⏵ **같은 날 v8이었고**(v7 + 도구 루프, #419) ⏵ 2026-10-06부터 **지금은 v9다**(v8 + `auditor_guards` 서술) ⏵ **2026-10-07부터 v10이었다**(v7 전면 재작성 · 매 홉 봉투가 `AuditorProcedureAction`) ⏵ **2026-10-09부터 지금은 v11이다**(v10 + `cwe` 서술 네 줄 · 매 홉 봉투가 `AuditorProcedureNullableCweAction`, 이슈 #525) ⏵ **2026-10-10부터 지금은 v13이다**(v11 + FACTS에서 `source` 사본을 뺀다, 이슈 #67) ⏵ **2026-10-11부터 지금은 v14다**(v12 + FACTS에서 `source` 사본을 뺀다, 이슈 #67 · #578 · PR #601) ⏵ **2026-10-13부터 지금은 v15다**(v14 + 계약 문장 여섯, `docs/agent-contracts.md` §2.2 「v14 → v15」 · 봉투는 v12의 서브클래스 `AuditorProcedureProbeInputAction`), §5 Auditor 행). 즉 풀은 더는 「§1을 앞선 provisional 배선」이 아니라 **§1 그 자체다.** (⏵ 2026-09-25 — §1이 다시 바뀌었다: `scout_memory`가 `qwen3-14b`로 옮겨 지금은 `qwen3-14b`(memory·injection 한 키)·`seccoderx-8b`(boundary)다. 두 풀 프로필은 그 새 배선도 그대로 따른다 — 위 §1.) ⚠ **그래도 검증 전이다** — 새 넷의 `checksum`이 placeholder(0)이고 `benchmark`가 전부 0/null이라 `production: false`이고 손으로 안 돌린다(위 리랭커·structured output 선행도 그대로). **실서빙 `local_balanced`(단일 scout `qwen3-8b`·critic `devstral-small-2-24b`·리랭커 `qwen3-reranker-0.6b`)는 여전히 §1과 다르다(behind)** — 그 자리는 이번 교체로 안 움직였다. ⚠ **2026-09-24 — 두 풀 프로필이 `harness_pool`·`harness_pool_hybrid`로 이름을 바꿨다.** 같은 diff에서 injection을 §1대로 `qwen3-14b`로 옮기고 단계 단위 실행(`execution.mode: per_stage`)을 선언했다 — 교대(`swap`)는 배선 전이라 안 적었다(`config/profiles.yaml`의 그 주석) |
| **⑤ 슬라이스 판별기** | **§1에 자리가 없다.** [build-plan.md](docs/build-plan.md) §2가 4단계 모델 자리로 ⑤를 들고 §8 로드맵 8이 *"슬라이스 판별기 추가 · 임계값 보정"*을 두는데, 위 §1 표에 대응 행이 없고 registry 키도 없다(②③④⑥⑦은 있다). ⚠ **1단계 무모델 전환과 별개다** — 그것은 0·1단계이고 ⑤는 **4단계**다. 로드맵 8 행은 2026-09-02 커밋으로 들어와 슬라이서 결정(2026-09-13)보다 앞서고, *"입력 표현이 다른 네 번째 채널"*이라 Case A/B 슬라이스 여부와 무관하게 설 수 있다. **등재·본문 서술이 없는 것이 남은 일이고, 로스터를 바꾸는 것은 §3의 넷뿐이다.** ⚠ **2026-09-24 — 「입력 표현이 다른」이 [ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 1과 부딪힌다** — 그 결정대로면 Scout 세 좌석이 이미 슬라이스를 받으므로 ⑤만의 입력 표현이 따로 남지 않는다. ⑤를 어떻게 할지는 이 파일이 정하지 않는다 |

**「로스터 확정」과 「그 모델이 선다」는 다른 문장이다.** 이 표가 그 차이를 남긴다.

⚠ **그래서 `config/profiles.yaml`을 열면 §1과 다른 배정이 아직 보인다 — 이제 자리가 갈린다.**
**canonical `harness_pool`·`_hybrid`는 §1과 일치한다**(위 표 「실서빙 프로필 ↔ §1」 행 — 2026-09-24 `local_balanced_pool`에서
이름을 바꾸고 injection을 `qwen3-14b`로 옮겼다).
~~**실서빙 `local_balanced`·`_hybrid`·`b1_single`은 여전히 §1과 다르다**(단일 scout·critic devstral 등) —
그것은 드리프트가 아니라 위 표 「실서빙 프로필 ↔ §1」 행이 든 「아직 안 따라온」 상태다.~~ **2026-09-24 — 그 셋(과
`single_gpu_12g`)을 지웠다**(§1 끝). 이제 실모델을 쓰는 프로필은 `harness_pool`·`_hybrid` 둘이고 §1과 일치한다.
`tests/test_docs_format.py`의 `test_context_declares_that_profiles_are_not_rewired_yet`이
그 어긋남과 이 선언을 묶는다: **어느 프로필이든 Scout 풀 역할을 쓰면 「아직 안 됐다」 마커를
지우게 만든다** — 2026-09-13에 `local_balanced_pool`이 풀을 배선하며 그 마커를 지웠다. 이제 그
줄이 도로 들어오면(실서빙 프로필이 §1을 못 따라온 채 「재배선 안 됨」을 다시 주장하면) 검사가
막는다.
