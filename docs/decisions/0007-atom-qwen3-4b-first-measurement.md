# 0007. ATOM 첫 실측을 이 문서로 고정하고, `qwen3-4b`를 `VLLM_USE_DEEP_GEMM=0`으로 못박는다

- 상태: 제안
- 일자: 2026-08-17 (문서화 2026-08-18)
- 관련: [model-serving.md](../model-serving.md) §3·§5, [portability.md](../portability.md) §5 #5·§6, [open-questions.md](../open-questions.md) R1, [decisions/0006](0006-record-the-device-a-run-happened-on.md), [STATUS.md](../../STATUS.md) §5·§6

> ⏵ **2026-09-24** — `qwen3-4b` 키와 `config/checksums/qwen3-4b.txt`는 로스터 밖 정리로 지웠다. 이 문서가 드는
> registry 값·checksum 파일 경로는 그날의 기록이다(원문은 git 이력).

## 배경

`model-serving.md` §3은 **"이 장비에서 직접 검증하기 전까지 어떤 성능 수치도 계획에
반영하지 않는다"**로 시작하고, 같은 절이 "측정 결과는 `docs/decisions/`에 ADR로 남기고,
수치는 Model Registry에 `bench --apply`로 기록한다"고 절차를 정해 뒀다. 2026-08-17에
ATOM에서 첫 수치가 나왔고 registry는 채워졌는데 **ADR 쪽이 비어 있었다.** 그래서 지금
`config/models.yaml`의 다섯 줄이 그 측정의 유일한 기록이다.

측정까지 걸린 것은 두 가지였고 둘 다 그날 풀렸다.

| 막던 것 | 성격 | 해소 |
| --- | --- | --- |
| sm_121 커널 호환성 (R1) | 미검증 | vLLM 0.27.1 + torch 2.13.0+cu130이 `(12, 1)`·`NVIDIA GB10` 인식 |
| 테넌트의 로컬 TCP 차단 | 환경 제약 (하네스 밖) | 관리자 예외 |

그리고 **그 둘을 통과한 뒤에 세 번째가 나왔다.** `qwen3-4b`(fp8)가 sm_121에서 기동하지
않는다 — 가중치를 전부 읽은 **뒤** DeepGEMM이 fp8 scale-factor 레이아웃을 변환하지 못하고
죽는다.

```
RuntimeError: Assertion error (deepgemm-src/csrc/apis/layout.hpp:60):
              Unknown SF transformation
fp8.py:434 → deep_gemm.py:96 → fp8_utils.py:1137 → utils/deep_gemm.py:494
```

크래시 지점이 로딩 후처리라 **모델 파일이나 checksum을 의심하기 쉬운 모양**이다. 원인은
백엔드 커널이고, `VLLM_USE_DEEP_GEMM=0`을 주면 뜬다. 아래 수치는 전부 그 플래그를 준
상태의 값이다.

### 측정 대상과 환경

| 항목 | 값 | 출처 |
| --- | --- | --- |
| 모델 | `Qwen3-4B-Instruct-2507-FP8` @ `8591804019c8b22094c3b5b4454e0edc05dffc98` | `models.yaml` `source` |
| 스냅샷 무결성 | `sha256:ab78c8e5…ff72` (= `config/checksums/qwen3-4b.txt` 전체의 sha256) | `models.yaml` `checksum` |
| 장비 | `atom` — GB10, sm_121, CUDA 13.0, aarch64, DGX OS(Ubuntu 24.04), 통합메모리 128 GB 중 **팀 목표 34 GiB**(측정 시점 8/17엔 cgroup `memory.max`도 34였다; 이후 하드 한도 64로 상향 — [portability.md](../portability.md) §6), GPU 공유 | [portability.md](../portability.md) §6 |
| 서빙 | vLLM 0.27.1, `127.0.0.1:8001`, **`VLLM_USE_DEEP_GEMM=0` 필수** | 위 |
| 시각 | `2026-08-17T15:16:27Z` | `models.yaml` `measured_at` |

### 결과 — §3 체크리스트 7개 대비

| # | 항목 | 판정 필드 | 결과 |
| :-: | --- | --- | --- |
| 1 | 커널 호환성 = **기동 성공** | `roles.*.startup.ok` | **통과** (플래그를 준 상태에서) |
| 2 | 역할별 모델 로드 및 통합메모리 점유 | `mem_gb_ctx8k` | **미측정.** `nvidia-smi --query-gpu=memory.total,memory.used`가 이 장비에서 `[N/A], [N/A]`다 — 통합메모리라 보고할 별도 VRAM이 없다. registry의 `0`은 "0 GB"가 아니라 **안 쟀다**는 뜻이다 |
| 3 | context 8K/16K/32K KV cache·처리량 | `by_context.*.kv_cache_usage_perc_peak` | 미측정 |
| 4 | 단일 vs 배치 합산 tok/s | `tok_s_single` / `.by_concurrency.*` | **부분.** 단일 **39.65 tok/s**, batch32 **127.69 tok/s**. 8·16은 registry에 자리가 없어 남지 않았다 |
| 5 | prefix cache 적중률 | `prefix_cache.verdict` | 미측정 (R6 판정 지점) |
| 6 | structured output | `structured_output.valid_ratio` | 미측정 |
| 7 | NVFP4 Critic 정확도 | — | bench 범위 밖 (고정 반박 표본 대기) |

### 측정 원문 — **보존되지 않았다**

```
(비어 있음: bench stdout JSON 미보존)
```

**이것은 서식이 아니라 결함이다.** `python -m src.cli bench`는 JSON을 **stdout으로만**
낸다([cli.py](../../src/cli.py) `bench()`). `--apply`가 registry를 갈아 끼우므로 수치
다섯 개는 살아남았지만, `checklist` 배열·`measurement_order`·`nvidia_smi_mb_peak`·
`metrics_delta`처럼 **registry에 자리가 없는 필드는 터미널과 함께 사라졌다.** 위 표의
"미측정"과 "쟀는데 registry에 안 들어가는 값"을 지금 구분할 수 없는 이유가 이것이다.

원문이 복구되면 이 블록에 그대로 붙인다. 요약하거나 발췌하지 않는다 — 요약본은 다음
사람이 재현 대조에 쓸 수 없다.

## 결정

**하나. 이 문서가 ATOM 첫 실측의 정본이다.** `config/models.yaml`의 `benchmark:` 다섯
줄은 그 요약이고, 값의 뜻(무엇을 쟀고 무엇을 안 쟀는지)은 여기서 읽는다.

**둘. `qwen3-4b`를 ATOM에서 쓸 때 `VLLM_USE_DEEP_GEMM=0`은 선택이 아니다.** 이것은
성능 튜닝이 아니라 **기동 조건**이다. 플래그 없이 재측정하는 사람은 "수치가 안 나오는"
것이 아니라 "서버가 안 뜨는" 것을 만나고, 크래시 위치 때문에 모델 파일을 의심하게 된다.
그래서 이 사실은 세 곳에 중복해서 둔다 — `models.yaml`의 해당 필드 주석(바꾸려는 사람이
읽는 곳), portability.md §5 #5(환경 축), 이 ADR(측정 축).

**셋. 위 표의 "미측정"을 0으로 채우지 않는다.** `Benchmark.is_measured`가 0 여부로
판정하므로, 추정치를 넣으면 안 잰 모델이 production 프로필에 오르는 길이 열린다
(§3의 같은 규칙).

## 대안과 기각 사유

| 대안 | 기각 사유 |
| --- | --- |
| ADR 없이 registry 다섯 줄로 끝낸다 | **"39.65"가 무엇의 39.65인지 파일 안에 없다.** 플래그 조건도, `mem_gb_ctx8k: 0`이 미측정이라는 것도, 8·16을 안 쟀다는 것도 registry 스키마에 자리가 없다. §3이 ADR을 요구하는 이유가 이것이다 |
| 원문이 복구될 때까지 ADR을 미룬다 | 그 사이 registry 수치는 **출처 없이** 계속 쓰인다. 그리고 미루는 동안 없어진 것은 원문만이 아니라 "무엇이 없는지"에 대한 기억이다 — 지금 적지 않으면 반년 뒤엔 미측정과 미보존을 구분할 사람이 없다 |
| 원문 자리를 registry 값으로 재구성해 채운다 | **지어낸 JSON이 된다.** 이 PR이 못박으려던 오진("재지 않은 값이 통과했다")과 같은 종류의 구멍이고, 재현 대조에 쓰이면 더 나쁘다 |
| `bench`가 결과를 파일로 쓰게 코드를 고친다 | 옳은 방향이지만 이 ADR의 범위가 아니다. 지금 필요한 것은 이미 일어난 측정의 기록이고, 절차(아래 `tee`)로 같은 손실을 막을 수 있다. 반복되면 그때 코드로 옮긴다 |

## 결과

**얻는 것**

- registry 수치에 출처가 붙는다. `doctor`의 `measured_on` 경고(0006)가 "다른 장비"를
  잡아 준다면, 이 문서는 "같은 장비인데 다른 조건"을 잡아 준다.
- R1이 "환경이 막는다"에서 "아직 안 쟀다"로 성격이 바뀐 것이 한 곳에 기록된다. 해소
  주체가 장비 관리자에서 다시 우리로 돌아왔다.
- 다음 측정이 대조군을 갖는다.

**잃는 것 / 위험**

- **원문이 없는 채로 정본이 된다.** 위 표의 판정은 registry 다섯 줄과 그날의 크래시
  로그에서 재구성한 것이고, `bench`의 `checklist` 배열이 실제로 무엇을 뱉었는지는
  확인할 수 없다. 그래서 상태가 `제안`이다 — 원문이 붙거나 재측정이 되면 올린다.
- 수치가 **GPU 공유 상태**의 값이다. 팀 목표 34 GiB 안에서 GPU를 공유하므로 같은 명령이
  다른 시각에 다른 값을 낼 수 있다. 재현 시 대조할 기준선으로는 쓰되, 절대값으로
  계획에 넣지 않는다(§3의 첫 문장).

**되돌리는 비용: 없다.** 관측 기록이라 되돌릴 결정이 없다. 틀린 것이 드러나면 고쳐 적는다.

## 후속

- **원문을 찾으면 위 블록에 붙이고 상태를 `승인`으로 올린다.** 못 찾으면 재측정할 때
  받는다. 그때는 파일로 받는다:

  ```bash
  VLLM_USE_DEEP_GEMM=0 vllm serve <스냅샷> --served-model-name qwen3-4b &
  python -m src.cli bench --device atom --apply | tee docs/decisions/0007-bench-atom-20260817.json
  ```

- 체크리스트 2~6을 채운다. 이제 막는 것이 없다. 2번은 `nvidia-smi`가 `[N/A]`라 **소스를
  먼저 정해야** 한다 — 통합메모리에서 무엇을 "점유"로 셀지가 미정이다.
- **`bench` 결과를 파일로 남길지 정한다.** 이번 손실이 절차로 막을 수 있는 종류였는지
  (사람이 `tee`를 안 쓴 것), 아니면 구조적인지(stdout만 내는 것이 문제)를 두 번째 측정에서
  본다. 반복되면 코드로 옮긴다.
- `nvfp4` 모델(`qwen3-coder-30b-a3b`, `devstral-small-2-24b`)이 같은 종류의 백엔드 문제를
  따로 만날 수 있다. fp8에서 일어난 일이 nvfp4에서 안 일어난다는 근거는 없다.
