# ATOM 검색 경로(Retrieval) 실서빙 실험 결과

> **기록 성격**: 2026-08-18 시점의 관측 기록이다. 서식(메타 표, §6~7)은
> [TEMPLATE.md](../TEMPLATE.md)에 맞춰 2026-08-19에 @danielcho02가 정리했다.
>
> **정정 이력**: 작성자(@sumin0218)가 2026-08-19에 본문에서 두 주장을 직접 정정했다 —
> §1(실모델 파이프라인 "첫 완주" → **검색 경로를 포함한** 첫 완주. 전체 첫 완주는 서빙 쪽)과
> §2.1(메모리 "34 GiB 커널 강제" → 34는 멘토 안내 **팀 목표**이고, 시스템 하드 한도는 cgroup
> **`memory.max`=64 GiB**였다(2026-08-18 시점 — 8/31에 80으로 확장). 8/17엔 max도 34였다가 상향됐다). 근거는 [README.md](../README.md) 확정 #1·#4.

**작성**: @sumin0218 (T4 Retrieval / 로컬 LLM 구축)
**실험 기간**: 2026-08-18 저녁
**대상 장비**: GIGABYTE AI TOP ATOM (NVIDIA DGX Spark 계열, GB10 Grace Blackwell)
**전제 조건**: TCP 예외 승인 이후 · vLLM 서빙 환경(venv-probe) 확보 이후

## 메타

**빈칸은 지어내지 않았다.** 특히 revision 3건의 공백이 [README.md](../README.md) 확정 #2가
지목한 오독의 원인이었다.

**그 세 칸은 2026-08-22에 채웠다 — 실험 시점에 적어둔 값이 아니라 ATOM 캐시의 스냅샷
디렉터리 이름에서 역추적한 것이다.** 근거와 그 한계는 README 미확정 #3에 있다.
나머지 빈칸은 그대로다.

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **미기재** — 원 보고서는 "2026-08-18 저녁"까지만 적었다. 분 단위가 없어 다른 실험과의 선후를 원문만으로는 정할 수 없다 |
| 실제 서빙 모델 (§3 scout) | `hf://Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca` (bf16). **별칭 `--served-model-name qwen3-4b`** — 별칭은 근거가 못 된다 |
| 실제 서빙 모델 (§4.2 embedding) | `hf://Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| 실제 서빙 모델 (§4.3 reranker) | `hf://Qwen/Qwen3-Reranker-0.6B@e61197ed45024b0ed8a2d74b80b4d909f1255473` |
| `repo_commit` | **미기재** |
| `run_id` | **미기재** |
| run 산출물 경로·보존 | **미기재.** §9는 "저장소 변경 없음"이므로 저장소 안에는 남지 않았다 |
| `tool_runner` | 프로필 `single_gpu_12g` 선언 **`fake`**([profiles.yaml](../../../config/profiles.yaml)) / 실제 **`podman`**. 오버라이드 경위는 [README.md](../README.md) 확정 #5 |
| `--device` | `atom` |
| `tool_images` digest | **미기재** |
| `--gpu-memory-utilization` | embedding·reranker 각 **0.1** (§10 부록) · 분모 **119.7 GiB**(물리 통합메모리). **scout(8001)은 미기재** |
| 동시에 떠 있던 다른 서버 | §4.4 기준 **8004(embedding) · 8005(reranker) 둘**. §3의 8001이 그때도 떠 있었는지는 **미기재** |
| 원문 산출물 보존 | **미기재** |

---

## 1. 요약

모델 서빙 환경이 마련된 위에서, **하네스의 검색(Retrieval) 파이프라인을 실모델로 처음부터 끝까지 완주**시켰습니다. 지금까지 검색 경로는 전부 Fake로만 돌았고, 실제 embedding·reranker 모델에 붙여 검증한 것은 이번이 처음입니다.

| 항목                         | 결과                                                         |
| ---------------------------- | ------------------------------------------------------------ |
| 하네스 ↔ 실모델 배선 (scout) | **성공** — 검색 경로를 포함한 실모델 첫 완주(전체 첫 완주는 모델 서빙 쪽, [README.md](../README.md) 확정 #1), structured output 정상 |
| embedding 실서빙             | **성공** — `/v1/embeddings`, 1024차원 벡터                   |
| reranker 실서빙              | **성공** — `/v1/rerank`, 순위 의미상 정확                    |
| HybridRetriever 실경로 완주  | **성공** — embedding·reranker 둘 다 실호출, 파이프라인 완주  |
| sm_121 커널 우회 확립        | **완료** — 서빙 시 재현 가능한 기동 조합 확정                |

특히 세 가지를 공유드립니다.

1. **검색이 실제로 결과를 바꿉니다.** Fake retriever일 때와 실서빙일 때 Finding 수가 달랐습니다 — 실제 embedding·reranker가 어떤 코드 조각을 캡슐에 넣느냐가 후보 판정에 영향을 준다는 뜻입니다. 검색 품질이 파이프라인 성능의 실제 변수임을 확인했습니다.
2. **reranker 서빙 레시피가 코드에 이미 있었습니다.** `src/adapters/vllm_retrieval.py`의 docstring에 2026-08-11 실측으로 남긴 `--hf_overrides` 설정이 있었고, 그대로 적용해 한 번에 열렸습니다. 문서화의 값을 실감한 사례입니다.
3. **작은 검색 모델은 FP8 커널 문제를 겪지 않습니다.** embedding·reranker는 0.6B fp16이라, 모델 서빙 쪽에서 다룬 양자화 커널 이슈와 무관하게 가볍게 떴습니다(각 `gpu_memory_utilization 0.1`).

---

## 2. 실험 환경

### 2.1 하드웨어 · 소프트웨어

| 항목      | 값                                                                                        |
| --------- | ----------------------------------------------------------------------------------------- |
| GPU       | NVIDIA GB10 Grace Blackwell, sm_121, aarch64                                              |
| 메모리    | 팀 목표 34 GiB(멘토 안내) / 하드 한도 cgroup `memory.max` 64 GiB (`memory.high` 58 · 값은 바뀜, 실행 시점에 읽음)                                                  |
| vLLM      | 0.27.1 (`/srv/tenants/user/venv-probe`, Python 3.12)                                     |
| 사용 모델 | Qwen3-0.6B, Qwen3-4B-Instruct-2507-FP8, **Qwen3-Embedding-0.6B**, **Qwen3-Reranker-0.6B** |

### 2.2 착수 전 정리한 sm_121 기동 조건

sm_121에서 vLLM이 커널을 실행 시점에 JIT 컴파일하는데, 빌드 도구(nvcc·파이썬 헤더)가 사용자 권한 밖이라 그대로는 실패합니다. 서빙 담당 쪽에서 확립한 방식(파이썬 헤더를 사용자 영역에 전개 + `CPATH` 지정)을 재사용하고, 어텐션 백엔드를 JIT가 필요 없는 것으로 고정했습니다.

```
CPATH=$HOME/pyhdr/usr/include/python3.12:$HOME/pyhdr/usr/include:$CPATH
VLLM_ATTENTION_BACKEND=TORCH_SDPA
--enforce-eager
VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo   # loopback 고정
```

---

## 3. 실험 1 — 하네스 ↔ 실모델 배선 (Phase 1)

### 3.1 목적

지금까지 파이프라인 완주는 전부 Fake 모델이었습니다. **하네스의 `OpenAICompatModelClient`가 실제 vLLM 서버와 통신하고, structured output(JSON schema 강제)이 작동하며, 엔드포인트 배선이 맞는가**를 확인합니다. 성능이 아니라 **경로의 성립**을 판정하는 실험입니다.

### 3.2 조건

- 모델: Qwen3-0.6B(bf16)를 `--served-model-name qwen3-4b`로 8001에 서빙 — 배선 검증이 목적이라 모델 크기는 무관
- 프로필: `single_gpu_12g`(scout 실서빙, 나머지 Fake) `--tool-runner podman`

### 3.3 결과

| 항목                   | 결과                                                    |
| ---------------------- | ------------------------------------------------------- |
| `/v1/chat/completions` | **2회 200 OK** — 하네스가 실제로 추론 요청              |
| structured output      | **정상** — SchemaParseError 없이 완주(가드 디코딩 작동) |
| 파이프라인             | 완주 (후보 2 / Finding 0)                               |
| 기록                   | `tool_runner=podman`, `device=atom`                     |

Finding 0건은 결함이 아니라 **0.6B 실모델이 두 후보를 임계값 아래로 판정**한 결과입니다(Fake 휴리스틱은 2건이었음). 실모델이 실제로 판단을 내렸다는 방증입니다.

---

## 4. 실험 2 — 검색 경로 실서빙 (①)

### 4.1 목적

`HybridRetriever`의 전체 경로(넓게 수집 → embedding으로 좁힘 → reranker로 재선별 → 캡슐)를 **실제 embedding·reranker 모델**에 붙여 검증합니다.

### 4.2 embedding (Qwen3-Embedding-0.6B, 8004)

sentence-transformers 형식이라 vLLM이 자동으로 pooling 모델로 인식했습니다(`--task` 인자 불필요 — 0.27.1에서 폐기됨).

```
POST /v1/embeddings  →  1024차원 벡터, usage 토큰 회계 정상
```

### 4.3 reranker (Qwen3-Reranker-0.6B, 8005)

원본이 causal LM이라 그냥 띄우면 `/rerank`가 404입니다. 어댑터 docstring의 실측 레시피대로 `--hf_overrides`로 sequence-classification으로 변환해 score API를 열었습니다.

```
--hf_overrides {"architectures":["Qwen3ForSequenceClassification"],
                "classifier_from_token":["no","yes"],
                "is_original_qwen3_reranker":true}
```

**순위가 의미상 정확합니다** (query="buffer overflow vulnerability"):

| 문서                                   | 점수                         |
| -------------------------------------- | ---------------------------- |
| parse_header가 경계 검사 없이 복사한다 | **0.2447** (최고)            |
| memcpy에 공격자 길이                   | 0.0911                       |
| 무관한 로깅 함수                       | 0.0242 (최저, 정확히 걸러냄) |

### 4.4 통합 — 전체 검색 경로

embedding·reranker 둘 다 실서빙으로 두고 파이프라인을 돌렸습니다(scout/auditor/critic은 Fake로 격리 — 검색만 검증).

| 항목           | 결과                          |
| -------------- | ----------------------------- |
| embedding 호출 | run 중 실제 발생 (+1)         |
| reranker 호출  | run 중 실제 발생 (+1)         |
| 파이프라인     | 완주 (후보 2 / **Finding 1**) |

Finding이 Fake retriever(dev_fake)와 다르게 나온 것이 요점입니다 — **실제 검색이 캡슐 내용을 바꾸고, 그것이 후보 판정을 바꿉니다.** 검색 품질이 실제 변수임을 확인했습니다.

---

## 5. 막힌 것과 해결

| 벽                            | 원인                                  | 해결                                              |
| ----------------------------- | ------------------------------------- | ------------------------------------------------- |
| torch.compile `Python.h 없음` | python3-dev 미설치(sudo 필요)         | CPATH로 사용자 영역 헤더 지정 / `--enforce-eager` |
| flashinfer `ninja 없음`       | sm_121 커널 JIT에 nvcc 필요           | `VLLM_ATTENTION_BACKEND=TORCH_SDPA`               |
| `--task embed` 미인식         | vLLM 0.27.1에서 폐기                  | sentence-transformers config로 자동 인식          |
| reranker `/rerank` 404        | causal LM은 score API 없음            | `--hf_overrides`로 SequenceClassification 변환    |
| `pkill` 실행 시 SSH 끊김      | 패턴이 자기 명령줄과 일치 → 자기 종료 | 종료는 pid로                                      |

---

## 6. 미측정 항목과 사유

**2026-08-19 서식 정리에서 신설한 절이다.** 원 보고서 §"남은 것"에 있던 항목을 옮겼고,
내용은 그대로다.

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 단계별 서빙 34 GiB 실측 | **미착수** | 각 서버를 띄운 상태에서 `memory.current`를 읽는다 — 상한이 세 겹이라는 것이 [README.md](../README.md) 확정 #4에 있고, 미확정 #1이 정확히 이 측정을 기다린다 |
| 실험 종료 시각 (분 단위) | 필드가 없었다 | 다음 실험부터 [TEMPLATE.md](../TEMPLATE.md) 메타 표가 강제한다 |
| 서빙 모델 revision 3건 | 필드가 없었다 | **해소(스냅샷 해시로, 2026-08-22).** 위 「메타」 표에 기재했다 — 지운 기록이 아니라 남긴 기록으로 이 행을 둔다 |

---

## 7. 반증 조건

**미기재.** 원 보고서는 §3.1에서 "성능이 아니라 **경로의 성립**을 판정하는 실험"이라고
판정 대상을 밝히고 있으나, **실패했다면 무엇이 틀린 것이었는지는 적혀 있지 않다.**
이 절은 2026-08-19 서식 정리에서 신설했고, 남의 실험의 반증 조건을 대신 쓰지 않는다 —
작성자 확인 대기.

---

## 8. 의의와 후속 과제

### 의의

- **T4(Retrieval) 실서빙 경로가 처음으로 검증됐습니다.** 검색은 B1·B3' 모든 조건에 필요한 공통 경로라, 이 검증이 조건 비교의 전제 하나를 채웁니다.
- 모델 서빙(FP8/30B) 작업과 **상보적**입니다 — 이쪽은 검색 서브시스템, 저쪽은 추론 모델. 겹치지 않습니다.

### 남은 것

| 항목                       | 상태                                                  |
| -------------------------- | ----------------------------------------------------- |
| B1 조건 완주 (30B 실서빙)  | 모델 서빙 레시피(FP8) 확보됨 → 진행 가능              |
| 상시 검증 프로필 신설 여부 | 이번엔 임시 프로필(커밋 안 함)로 검증 — 상시화는 논의 |
| 단계별 서빙 34 GiB 실측    | 미착수                                                |

---

## 9. 정리 상태

서빙 서버 전부 종료(메모리 반납), 임시 프로필 되돌림, 저장소 변경 없음.

---

## 10. 부록 — 검증된 서빙 명령

```bash
# embedding
CPATH=$HOME/pyhdr/usr/include/python3.12:$HOME/pyhdr/usr/include \
VLLM_ATTENTION_BACKEND=TORCH_SDPA VLLM_HOST_IP=127.0.0.1 \
NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
vllm serve <Qwen3-Embedding-0.6B snap> --served-model-name qwen3-embedding-0.6b \
  --host 127.0.0.1 --port 8004 --gpu-memory-utilization 0.1 --enforce-eager

# reranker
CPATH=... VLLM_ATTENTION_BACKEND=TORCH_SDPA ... \
vllm serve <Qwen3-Reranker-0.6B snap> --served-model-name qwen3-reranker-0.6b \
  --host 127.0.0.1 --port 8005 --gpu-memory-utilization 0.1 --enforce-eager \
  --hf_overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'
```

**§3의 scout(8001) 기동 명령은 원 보고서에 없다 — 미기재.**
