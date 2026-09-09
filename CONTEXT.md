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

**확정 2026-09-09 (조민규).** 근거는 [ADR 0010](docs/decisions/0010-harness-structure-and-model-roster.md) §2와
[2026-09-08 로스터 조사](docs/research/2026-09-08-roster.md) §1.

| 자리 | registry 키 | registry 등재 |
| --- | --- | --- |
| 1단계 · 쿼리 생성기 | `qwen3-8b` | ✅ |
| 3단계 · 임베딩 | `qwen3-embedding-0.6b` | ✅ |
| 3단계 · 리랭커 | `qwen3-reranker-4b` | ❌ **미등재** |
| 4단계 · `scout_memory` (CWE-415·416) | `qwen3-coder-30b-a3b` | ✅ |
| 4단계 · `scout_boundary` (CWE-119·125·787·190) | `devstral-small-2-24b` | ✅ |
| 4단계 · `scout_injection` (CWE-78·22·134) | `qwen3-8b` | ✅ |
| 5단계 · Auditor **A안** | `qwen3-coder-30b-a3b` | ✅ |
| 5단계 · Auditor **B안** | `nemotron-3-super-120b-a12b` | ❌ **미등재** |
| 7단계 · Critic | `gpt-oss-20b` (`reasoning_effort: high`) | ❌ **미등재** |
| B1 기준(최강 단일 멤버) | `qwen3-coder-30b-a3b` | ✅ |

**Auditor는 A/B다.** 문헌이 정면으로 갈려 실측으로 가른다 — [D24](docs/open-questions/D/D24.md)가 *"A안·B안 둘 다 돌린 run 두 번. 한 번으로는 못 정한다"*고 적는다.

### 로스터에 없지만 registry에 남는 것

| 키 | 왜 남나 |
| --- | --- |
| `fake` | 인프라. `dev_*` 다섯 프로필의 전 역할 |
| `qwen3-4b` | `single_gpu_12g` 전용(12GB 장비) |
| `qwen3-reranker-0.6b` | **지우지 않는다** — 4B와의 대조 기준. 서빙 역할에서만 내린다 |

> **registry 키는 지우지 않는다.** ADR 0010 「되돌리는 비용」 — *"모델은 registry 키를 지우지 않으므로 프로필 한 줄이다."*
> 로스터 교체가 **삭제가 아니라 프로필 재배선**인 이유이고, 그래서 되돌리기가 싸다.

---

## 2. 인덱서 — **정본**

**확정 2026-09-09 (조민규).** 발표안 아티팩트의 **Code Slicing** 박스를 이번 리스트럭처 범위에 넣는다.

| | |
| --- | --- |
| 슬라이서 | **Joern 기반** |
| 순서 | **`LLM4FPM`(신호 주도) → `LLMxCPG-Q`(쿼리 생성)** |
| 쿼리 생성기 모델 | `qwen3-8b` — `scout_injection`과 **같은 서버**(같은 모델) |
| 그때까지 | **tree-sitter 인덱서를 병행 유지.** 전환 시점은 슬라이서가 서는 때다 |

⚠ **실험 중이고 되면 적용한다.** 「확정」은 *이 구조로 간다*는 뜻이지 *이미 선다*는 뜻이 아니다.

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

⚠ **`config/profiles.yaml`의 `local_balanced`와 `local_balanced_hybrid`는 같은 diff에서 고친다.**
`tests/test_config.py`의 `test_the_hybrid_serving_profile_differs_from_local_balanced_only_in_the_retriever`가
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
| `gpt-oss-20b` structured output | **실패.** [2026-09-09 온보딩 보고서](docs/experiments/harness/2026-09-09-gpt-oss-20b-onboarding-atom.md) — `xgrammar` 5/5 오답 · `guidance` 5/5 HTTP 500 · 스키마 없이는 3/3 정답. 미측정 셋(`reasoning_parser` 명시 · `enable_in_reasoning=True` · `outlines`)이 남았고 **하나에서 뒤집히면 결론이 바뀐다** |
| `qwen3-reranker-4b` | 다운로드 전. [D63](docs/open-questions/D/D63.md)의 재측정 대상이 이 모델이다 |
| Auditor A/B | 실측 전. [D24](docs/open-questions/D/D24.md)·[D51](docs/open-questions/D/D51.md)이 여기 종속 |
| Joern 슬라이서 | 실험 중 |
| **`config/profiles.yaml` 재배선** | **안 됐다.** 프로필 전부가 아직 단일 `scout`이고 `critic`은 `devstral-small-2-24b`, 리랭커는 `qwen3-reranker-0.6b`다 — **§1의 배정이 아니다.** 신규 셋이 registry에 없어서 프로필에 못 올린다(위 두 줄이 그 선행 조건이다) |

**「로스터 확정」과 「그 모델이 선다」는 다른 문장이다.** 이 표가 그 차이를 남긴다.

⚠ **그래서 `config/profiles.yaml`을 열면 §1과 다른 배정이 보인다. 그것은 드리프트가 아니라
위 표의 마지막 줄이다.** §1이 정본이고 프로필은 아직 거기 못 따라왔다 — 반대로 읽지 마라.
`tests/test_docs_format.py`의 `test_context_declares_that_profiles_are_not_rewired_yet`이
그 어긋남과 이 선언을 묶는다: 재배선이 끝나면 그 검사가 실패해서 **이 줄을 지우게 만든다.**
