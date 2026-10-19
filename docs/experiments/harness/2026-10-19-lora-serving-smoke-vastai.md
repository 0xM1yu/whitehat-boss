# LoRA 서빙 feasibility 스모크 — sft_1-1 어댑터 on Qwen3.8-27B · vastai

> ## 🔴 배관 feasibility만 · 역량·점수·처리량 미측정
>
> 판정하는 것은 하나다 — **vLLM 0.27.1이 GDN-hybrid base `Qwen/Qwen3.8-27B` 위에 LoRA 어댑터를 얹어
> 서빙하는가.** Auditor 좌석을 FT(`qwen3.8-27b-sft1-1`)로 배선하는 approach A의 선행조건이다. 되면 A,
> 안 되면 어댑터를 base에 병합하는 B로 틀어야 했다. **결과: 된다.**
>
> 🔴 이 기록이 **보이지 않는 것** — FT 품질(base보다 나은지), 처리량, checksum. 하네스를 거치지 않았다
> (vLLM를 손으로 띄운 스모크). 비교표에 넣지 않는다.

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 시각 | `2026-10-19` (KST) | 분 단위 미기재 — 수동 스모크 |
| 실제 서빙 모델 | base `hf://Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` + LoRA 어댑터 `Melibee/sft-qwen27b-1-1` (`sft_1-1_epoch3`) | 어댑터는 레포 `models/sft_1-1/epoch3`와 같은 것(PR #710) · `adapter_model.safetensors` 318,835,672 B · r=16 · α=32 |
| `run_id` | **없음** | 하네스 run이 아니라 `vllm serve`를 직접 띄운 스모크 |
| `repo_commit` | 배선 브랜치 `feat/wire-sft1-1-auditor` | 스모크는 코드 실행이 아니라 서버 기동이라 commit과 독립 |
| `tool_runner` | **해당 없음** | 도구 단계 안 돎 |
| `--device` | `vastai` (직접) | 오케스트레이터 미사용 — box에서 vLLM만 |
| 서빙 런타임 | `vllm/vllm-openai:v0.27.1` | vast box `56688299` · RTX PRO 6000 Blackwell Server Edition 97,887 MiB |
| `--gpu-memory-utilization` | 0.92 · 분모 97,887 MiB | `--max-model-len 8192`(스모크값 · production은 262,144) · `--enforce-eager` |
| 원문 산출물 보존 | **보존하지 않았다** | 세션 중 `/v1/models`·`/v1/chat/completions` 응답을 눈으로 읽었다 |
| 비용 | vast 크레딧 $6.25 → $4.68 (~$1.57, 첫 디스크 실패 포함) | box destroy 완료 · 인스턴스 0 |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| base + LoRA 기동 | ✅ `Application startup complete` [실측] |
| `/v1/models` | ✅ `['qwen3.8-27b-base', 'sft1-1']` — base·LoRA 둘 다 [실측] |
| LoRA 추론 | ✅ `model=sft1-1` chat completion 1건 반환(생성됨 · `finish_reason: length`는 `max_tokens: 8`) [실측] |
| 품질·처리량·checksum | **미측정** |

## 2. 기동 명령 (원문)

```bash
vllm serve Qwen/Qwen3.8-27B \
  --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --served-model-name qwen3.8-27b-base \
  --enable-lora --max-lora-rank 16 \
  --lora-modules sft1-1=/root/sft/sft_1-1_epoch3 \
  --max-model-len 8192 --gpu-memory-utilization 0.92 \
  --enforce-eager --host 127.0.0.1 --port 8000
```

이 모양이 `tools/serve/swap.sh`가 LoRA 엔트리(`lora_base` 있음)에 대해 조립하는 인자와 같다 — base를
`--served-model-name <key>-base`로 띄우고 `--lora-modules <key>=<어댑터>`로 그 키를 얹는다.

## 3. 해석

- **따라 나오는 것:** vLLM 0.27.1이 이 base(GDN hybrid · V1 러너)에서 `--enable-lora`를 받고, 요청
  `model=sft1-1`이 어댑터 경로를 타 응답을 낸다. approach A(병합 없이 어댑터 로딩)가 **성립한다.**
- **따라 나오지 않는 것:** FT가 base보다 나은지 · 처리량 · 262,144 창에서의 메모리 · 장시간 안정성.
  스모크는 `max-model-len 8192`에 추론 1건이다.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| base 다운로드 중 `No space left on device` | 컨테이너 overlay가 52 GiB뿐인데 base 가중치가 ~52 GiB | HF 캐시를 `/dev/shm`(RAM, box RAM 1.5 TB)으로 돌림(`HF_HUB_CACHE=/dev/shm/hf`) |
| `huggingface-cli download`가 안 돎 | 신 CLI에서 제거된 별칭(→ `hf`) | `hf download`로 어댑터 받음 |

→ 서빙 배선(장비 파일)에 **base 캐시가 overlay에 안 들어갈 수 있다**는 경고를 달았다
(`config/serving/vastai.yaml` 등 · `HF_HUB_CACHE`를 큰 볼륨으로).

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| FT 품질(base 대비) | 스모크는 기동·응답만 봤다 | 하네스 실서빙 run(평가셋) |
| 처리량·메모리 | 추론 1건 · 작은 창 | `--max-model-len 262144`로 벤치 |
| checksum | 어댑터+base 묶음 해시 안 잼 | registry `checksum` 채우는 측정(§0) |

## 6. 반증 조건

vLLM가 이 arch에서 LoRA를 거부했다면 — 엔진 초기화 때 `--enable-lora` 관련 예외로 죽었을 것이다.
그렇지 않고 `/v1/models`에 LoRA가 뜨고 추론이 돌았으므로 그 경계는 배제된다. (첫 시도는 디스크 부족으로
죽었고, 그건 arch가 아니라 overlay 용량 — §4.)

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | FT Auditor 실서빙 run으로 품질·checksum 측정 | 미착수 (별도 PR) |
| 중간 | 서빙 box의 HF 캐시를 큰 볼륨으로 두는 배치 확정 | 장비 파일 주석에 경고만 |

## 8. 정리 상태

- vast box `56688299`: **destroy 완료 · 인스턴스 0** [실측].
- 임시 설정·프로필: 없음(스모크는 손으로 띄웠다).
- 저장소 변경: 없음(이 보고서와 배선 PR은 별개 기록).
