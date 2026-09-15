# CONTEXT.md — 지금 무엇이 정본인가

> **이 파일이 로스터의 정본이다.** 다른 문서가 모델을 현재형으로 말하면 그 문서가 틀린 것이고,
> 이 파일과 어긋나면 **그 문서를 고친다.**
>
> **왜 이 파일이 있나.** 2026-09-09에 하루 동안 같은 사고가 네 번 났다 — 사람도 에이전트도
> 옛 로스터를 서술하는 계획 문서를 **살아 있는 지시로 읽고** 폐기 예정 구성으로 측정을 잡았다.
> 그때 저장소에는 「현재 구성」을 주장하는 소스가 셋(`config/`·`docs/` ADR·`build-plan §8`) 있었고
> **어느 쪽이 정본인지 기계가 읽을 수단이 없었다.** 이 파일이 그 수단이다.

---

## 1. 모델 로스터 — **정본**

**확정 2026-09-09 (@danielcho02).** 근거는 [ADR 0010](docs/decisions/0010-harness-structure-and-model-roster.md) §2와
[2026-09-08 로스터 조사](docs/research/2026-09-08-roster.md) §1.

| 자리 | registry 키 | registry 등재 |
| --- | --- | --- |
| 3단계 · 임베딩 | `qwen3-embedding-0.6b` | ✅ |
| 3단계 · 리랭커 | `qwen3-reranker-4b` | ✅ |
| 4단계 · `scout_memory` (CWE-415·416) | `qwen3-coder-30b-a3b` | ✅ |
| 4단계 · `scout_boundary` (CWE-119·125·787·190) | `devstral-small-2-24b` | ✅ |
| 4단계 · `scout_injection` (CWE-78·22·134) | `qwen3-8b` | ✅ |
| 5단계 · Auditor **A안** | `qwen3-coder-30b-a3b` | ✅ |
| 5단계 · Auditor **B안** | `nemotron-3-super-120b-a12b` | ❌ **미등재** |
| 7단계 · Critic | `gpt-oss-20b` (`reasoning_effort: high`) | ✅ |
| B1 기준(최강 단일 멤버) | `qwen3-coder-30b-a3b` | ✅ |

**Auditor는 A/B다.** 문헌이 정면으로 갈려 실측으로 가른다 — [D24](docs/open-questions/D/D24.md)가 *"A안·B안 둘 다 돌린 run 두 번. 한 번으로는 못 정한다"*고 적는다.

### 로스터에 없지만 registry에 남는 것

| 키 | 왜 남나 |
| --- | --- |
| `fake` | 인프라. `dev_*` 다섯 프로필의 전 역할 |
| `fake_v2` | 인프라. Scout 풀 팬아웃(v2 전용 풀 프롬프트)을 GPU 없이 테스트하는 fake 키. `test_pipeline_e2e`가 in-test로 조립한다 |
| `qwen3-coder-30b-a3b-v2` | **임시 v2 별칭.** base와 같은 무게·서버·checksum·fallback이고 `prompt_template_version`만 v2 — 풀 프롬프트(v2뿐) 도달용(#103 — registry 별칭, D54 (b)가 아니라 별개 선택지). `local_balanced_pool`의 `scout_memory`. **모델 선정 미확정** — §1 자리는 base(v1)가 정본 |
| `devstral-small-2-24b-v2` | **임시 v2 별칭.** base와 같은 무게·서버·checksum·fallback, `prompt_template_version`만 v2. `local_balanced_pool`의 `scout_boundary`. 근거는 위 별칭과 같다(#103) |
| `qwen3-8b-v2` | **임시 v2 별칭.** base와 같은 무게·서버·checksum·fallback(`qwen3-4b`), `prompt_template_version`만 v2. `local_balanced_pool`의 `scout_injection`. 근거는 위 별칭과 같다(#103) |
| `qwen3-4b` | `single_gpu_12g` 전용(12GB 장비) |
| `qwen3-reranker-0.6b` | **지우지 않는다** — 4B와의 대조 기준. 서빙 역할에서만 내린다 |

> **registry 키는 지우지 않는다.** ADR 0010 「되돌리는 비용」 — *"모델은 registry 키를 지우지 않으므로 프로필 한 줄이다."*
> 로스터 교체가 **삭제가 아니라 프로필 재배선**인 이유이고, 그래서 되돌리기가 싸다.

---

## 2. 인덱서 — **정본**

**구조 확정 2026-09-09 · 방식 확정 2026-09-13 (@danielcho02).** 발표안 아티팩트의 **Code Slicing** 박스를 이번 리스트럭처 범위에 넣는다.

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
요구하고([architecture.md](docs/architecture.md) §4), 후보 열거(`symbols_for()`)와 캡슐의
`callers`/`callees`(`names_for()`)가 그 인덱스에서 나온다. **슬라이스는 그래프가 아니라 그래프의
투영이라 그 자리를 대신하지 못한다** — 「병행」은 전환기 조치가 아니다.

⚠ **되면 적용한다.** 「확정」은 *이 구조로 간다*는 뜻이지 *이미 선다*는 뜻이 아니다 — §5가 그
상태를 든다. **그리고 방식을 정한 실험 기록은 아직 이 저장소에 없다** — `docs/experiments/`의
전처리 표 넷은 `LLMxCPG` 행이 전부 `—`(미측정)다. 수치를 인용하려면 그 기록을 먼저 들인다
([CLAUDE.md](CLAUDE.md) §0).

---

## 3. 로스터를 바꾸는 법 — **여기만 고친다**

팀은 계속 실험하며 로스터를 바꾼다. **바꾸는 비용을 낮게 유지하는 것이 이 파일의 존재 이유다.**

바꿀 때 손대는 곳은 **넷뿐이다**:

1. **이 파일 §1 표** — 정본 선언
2. **`config/models.yaml`** — 새 키 등재(기존 키는 안 지운다)
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

**둘 다 「로스터를 바꾸는 비용」이 아니라 「로스터를 처음 세우는 비용」이다** — 자리에
모델을 바꿔 끼우는 일은 여전히 넷이다. 다만 이 파일만 읽고 "넷이면 끝난다"고 읽지 마라.

⚠ **roles를 잠근 대조 쌍은 같은 diff에서 고친다.** `local_balanced`↔`local_balanced_hybrid`,
그리고 `local_balanced_pool`↔`local_balanced_pool_hybrid` 둘이다 — 각 쌍은 `retriever`
하나만 달라야 검색 경로 효과가 갈리고, 그래서 아래 두 검사가 roles 동일을 강제한다(그
강제가 없는 `dev_fake`/`dev_hybrid`는 이 규칙 밖이다). `tests/test_config.py`의
`test_the_hybrid_serving_profile_differs_from_local_balanced_only_in_the_retriever`와
`test_the_pool_hybrid_profile_differs_from_local_balanced_pool_only_in_the_retriever`가
`variant.roles == base.roles`를 단언한다 — 한쪽만 고치면 CI가 잡는다.

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
| `gpt-oss-20b` structured output | **실패.** [2026-09-09 온보딩 보고서](docs/experiments/harness/2026-09-09-gpt-oss-20b-onboarding-atom.md) — `xgrammar` 5/5 오답 · `guidance` 5/5 HTTP 500 · 스키마 없이는 3/3 정답. **2026-09-11에 아홉 팔로 축 다섯을 갈랐고 전부 기각이다**([9팔 보고서](docs/experiments/harness/2026-09-11-gpt-oss-structured-vocab-flag-atom.md)) — `reasoning_parser`·어휘 넷·**enum 순서**·**속성 순서**·**백엔드 `outlines`**(5/5 HTTP 500). **같은 JSON 안에서 `reason`이 "clear use-after-free"라 쓰고 `verdict`가 `safe`다.** 자유서술은 27/27 정답 — **틀리는 건 enum 토큰 하나다.** ⚠ **미측정은 `enable_in_reasoning`·enum 제약 제거·케이스 축이고, (가) 2단 추출은 아직 안 쟀다** — ADR 0010 「다시 열리는 조건」이 (나) 교체를 그 뒤로 뒀다 |
| `qwen3-reranker-4b` | 2026-09-11 등재(다운로드·checksum). 서빙·점수 정확성은 [R8](docs/open-questions/R/R8.md) 미검증이라 아직 프로필엔 안 올린다. [D63](docs/open-questions/D/D63.md)의 재측정 대상이 이 모델이다 |
| Auditor A/B | 실측 전. [D24](docs/open-questions/D/D24.md)·[D51](docs/open-questions/D/D51.md)이 여기 종속 |
| Joern 슬라이서 | [독립 오프라인 구현](docs/slicer-README.md)을 추가했다. `slices.json` 생성·mock 회귀 테스트까지이며, 실서버 질의·실대상 커버리지와 하네스 소비 경로는 미검증·미연결이다 |
| **`reasoning_effort: high` 배선** | **배선(2026-09-09) + registry 등재(2026-09-11) 둘 다 됐다.** `ModelEntry.reasoning_effort` → 요청 payload → `RunRecord.model_reasoning_effort`이고, `gpt-oss-20b`가 §1 표대로 등재돼 `reasoning_effort: "high"`를 선언한다. ⚠ **그래도 이 자리는 안 선다** — **앞에 이 표 첫 줄(structured output)**이 남았고, 프로필의 `critic`도 아직 `devstral-small-2-24b`다(아래 마지막 줄). 배선과 등재가 여는 것은 「선언하면 값이 실제로 간다」까지이지 「Critic이 뜬다」가 아니다 |
| **실서빙 프로필 ↔ §1** | **실서빙 프로필(`local_balanced` 등)은 아직 §1과 다르다** — 단일 `scout`·`critic: devstral-small-2-24b`·리랭커 `qwen3-reranker-0.6b`. ⚠ **2026-09-11부터 이유가 자리마다 다르다** — Critic(`gpt-oss-20b`)·리랭커(`qwen3-reranker-4b`)는 **등재됐으니** 올릴 수는 있고 남은 선행이 각각 이 표 첫 줄(structured output)·리랭커 행(서빙·점수 검증)이다. Auditor B(`nemotron-3-super-120b-a12b`)만 **미등재**라 못 올린다. **단 2026-09-13에 실험용 `local_balanced_pool` 하나가 §1 목표를 provisional 배선했다 — Scout 풀(v2 별칭) + Critic `gpt-oss-20b`.** #178 팬아웃 플러밍을 실모델로 처음 채우고 Critic도 §1의 gpt-oss로 올렸다. **선정·검증은 미확정**(풀 도달성 #103·2×2 · Critic 반박 정확도·DecisionDraft→gpt-oss 통합 #182). `production: false`이고 실서빙 `local_balanced`(critic devstral)는 안 건드렸다 |
| **⑤ 슬라이스 판별기** | **§1에 자리가 없다.** [build-plan.md](docs/build-plan.md) §2가 4단계 모델 자리로 ⑤를 들고 §8 로드맵 8이 *"슬라이스 판별기 추가 · 임계값 보정"*을 두는데, 위 §1 표에 대응 행이 없고 registry 키도 없다(②③④⑥⑦은 있다). ⚠ **1단계 무모델 전환과 별개다** — 그것은 0·1단계이고 ⑤는 **4단계**다. 로드맵 8 행은 2026-09-02 커밋으로 들어와 슬라이서 결정(2026-09-13)보다 앞서고, *"입력 표현이 다른 네 번째 채널"*이라 Case A/B 슬라이스 여부와 무관하게 설 수 있다. **등재·본문 서술이 없는 것이 남은 일이고, 로스터를 바꾸는 것은 §3의 넷뿐이다** |

**「로스터 확정」과 「그 모델이 선다」는 다른 문장이다.** 이 표가 그 차이를 남긴다.

⚠ **그래서 `config/profiles.yaml`을 열면 §1과 다른 배정이 보인다. 그것은 드리프트가 아니라
위 표의 마지막 줄이다.** §1이 정본이고 실서빙 프로필은 아직 거기 못 따라왔다 — 반대로 읽지 마라.
`tests/test_docs_format.py`의 `test_context_declares_that_profiles_are_not_rewired_yet`이
그 어긋남과 이 선언을 묶는다: **어느 프로필이든 Scout 풀 역할을 쓰면 「아직 안 됐다」 마커를
지우게 만든다** — 2026-09-13에 `local_balanced_pool`이 풀을 배선하며 그 마커를 지웠다. 이제 그
줄이 도로 들어오면(실서빙 프로필이 §1을 못 따라온 채 「재배선 안 됨」을 다시 주장하면) 검사가
막는다. **실서빙 프로필은 여전히 §1과 다르다** — 위 표 마지막 줄이 그 상태다.
