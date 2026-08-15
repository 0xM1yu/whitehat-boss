# 지원 대상 장비와 제약

이 하네스는 **두 장비에서 각각 파이프라인 전체가 완주해야 한다.** 역할을 나눠 맡는
관계가 아니라 같은 등급의 실행 대상이다.

| | 위치 | 왜 지원하는가 |
| --- | --- | --- |
| **ATOM** (GB10) | 주 장비 | 팀원 전원이 여기서 작업하고, registry `benchmark`도 이 기준이다 |
| **Seraph** (moana) | 이식성 요구사항 | 프로젝트 종료 후 경희대 학생들이 자기 클러스터에서 돌릴 수 있어야 한다 |

**한쪽이 반쪽만 도는 상태는 요구를 만족하지 못한다.** 양쪽 완주가 불가능하다고 판명되면
**ATOM 전용으로 좁히는 것이 옳은 결론이다** — 반쪽짜리 이중 지원이 최악이다. 지금 그
결정을 내리는 것이 아니라, 그 상황이 왔을 때 무엇이 옳은 결론인지를 미리 못박는다.

## 1. 제약과 그 결과

각 줄은 "그래서 그 장비에서 **무엇이 안 되는가**"로 끝난다. 제약만 적으면 표가 사실이지만
쓸모가 없다.

| 항목 | ATOM (GB10) | Seraph (moana) | 그래서 안 되는 것 |
| --- | --- | --- | --- |
| 아키텍처 | GB10, sm_121 (Blackwell) | A2000, sm_86 (Ampere) | **NVFP4가 Seraph에서 실행 불가.** `qwen3-coder-30b-a3b`·`devstral-small-2-24b`가 그 양자화라 `local_balanced`·`b1_single`을 Seraph에서 그대로 완주시킬 수 없다 → §3 이식성 결함 1 |
| 드라이버 / CUDA | 13.0 | 535 / CUDA 12.2 상한 | Seraph는 드라이버를 올릴 수 없다(공용 클러스터). CUDA 13 전용 커널을 요구하는 것은 무엇이든 Seraph에서 못 돈다 |
| vLLM | 최신 | **0.19.1까지** (torch cu12 세대) | 두 장비의 `/metrics` 이름·응답 봉투가 다를 수 있다. R6(`prompt_tokens_details: null`)이 정확히 이 차이로 갈리는 항목이다 → `tools/probe/`가 이것을 판정한다 |
| 메모리 | 통합메모리 | 12GB 전용 (u1은 6GB) | Seraph에 모델 5개를 동시에 못 띄운다. `single_gpu_12g`가 scout만 실서빙하는 이유다(`config/profiles.yaml`의 산술) |
| 도구 런타임 | Docker | **Singularity 3.7.3** (Docker 데몬 없음) | `cpu_limit`·`memory_mb`를 Singularity가 강제하지 못하고 `read_only=False`를 끌 수 없다. `tool_images` digest가 런타임 사이에서 비교되지 않는다(STATUS.md §10) → `--tool-runner`로 프로필을 덮는다(ADR 0005) |
| 잡 실행 | 직접 실행 | SLURM (`srun`/`sbatch`) | Seraph는 로그인 노드에서 무거운 작업이 금지다. 서버도 하네스도 컴퓨트 노드 안에서 돌아야 한다 |
| 접속 | Tailscale 경유 SSH | SSH (교외는 `-p 30080`) | SSH가 끊기면 `srun` job이 죽는다 → tmux 필수 |
| 계정 | **공용일 수 있다** | 개인 계정 + 공용 노드 | ATOM은 포트 충돌과 GPU 점유 조율이 필요하다 → `tools/probe/atom.sh`가 실행 전에 확인한다 |

### moana의 노드는 고정되지 않는다

**빈 노드를 그때그때 쓴다.** `sinfo -p debug_ce_ugrad`로 idle을 확인하고 들어간다.

| 노드 | 상태 |
| --- | --- |
| `moana-u1` | 6GB — 12GB 기준으로 잰 수치와 비교할 수 없다 |
| `moana-u3` · `moana-u4` | 12GB (A2000). 지금까지의 실측은 전부 이 둘에서 나왔다 |
| `moana-u8` | **사용 금지** — Prolog failure |

**실측 기록의 노드 이름은 그 날 실제로 쓴 노드다. 통일하지 않는다.** ADR 0005와 STATUS §5의
Singularity 검증은 `moana-u3`에서, 2026-08-14 저녁의 `/metrics` 프로브는 `moana-u4`에서
돌았다. 통일하면 실측 기록이 거짓이 된다. **값을 비교할 때 노드가 같은지 확인한다.**

래퍼(`tools/probe/seraph.sh`)는 `--node`에 기본값을 두지 않는다. 기본값을 두면 남이 쓰는
노드에 얹히거나 6GB 카드에서 재고도 그 사실이 산출물에 안 남는다.

## 2. endpoint는 두 장비에서 같다

**서버와 클라이언트를 같은 노드에서 돌린다.** Seraph는 로그인 노드에서 무거운 작업이
금지되므로 `srun` 하나가 컴퓨트 노드에서 vLLM과 클라이언트를 함께 띄우는 것이 규정에 맞는
유일한 방식이고, ATOM은 스케줄러가 없어 그냥 같은 기계다.

→ **endpoint는 양쪽 다 `127.0.0.1`이다.** 그래서 `config/`는 한 벌로 충분하고, 장비별 설정
오버레이가 지금은 없다. 이 전제가 깨지면(원격에서 붙거나, 공용 계정이라 포트가 사용자별로
갈리거나) 그때 다시 판단한다 — [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md)의
"후속"에 확인 항목이 있다.

## 3. 이식성 결함 목록

두 장비 중 한쪽에서 **아직 완주하지 못하는** 항목이다. 이 표가 비면 이식성 요구가
충족된 것이다.

| # | 항목 | 막힌 장비 | 상태 |
| --- | --- | --- | --- |
| 1 | `local_balanced`·`b1_single`이 NVFP4 모델을 쓴다 (`qwen3-coder-30b-a3b`, `devstral-small-2-24b`) | **Seraph** (sm_86) | 미해결. 해법 후보: 같은 모델의 다른 양자화를 **별도 model key + 별도 프로필**로 둔다. 오버레이로 `quantization`을 덮으면 안 된다 — 그건 같은 모델이 아니다 |
| 2 | R1: ATOM(sm_121)에서 vLLM 커널 호환성 미검증 | **ATOM** | [open-questions.md](open-questions.md) R1. `tools/probe/`가 1차 판정을 낸다 |

새 항목이 생기면 여기에 적는다. STATUS.md가 아니라 이 문서인 이유는 수명이 다르기
때문이다 — STATUS는 시점 스냅샷이고, 이식성 결함은 해소될 때까지 남는 대장이다.

## 4. 결과를 섞지 않는다

**두 장비의 결과를 같은 비교 셀에 넣지 않는다.** 다른 하드웨어에서 잰 수치는 비교 대상이
아니다. 이건 이식성 전제에서도 그대로 맞다 — 두 장비가 같은 등급의 실행 대상이라는 것과
그 수치가 서로 비교 가능하다는 것은 다른 이야기다.

그 판단을 하려면 **어느 장비에서 나왔는지가 기록에 있어야 한다.**

| 기록 | 값 | 없으면 |
| --- | --- | --- |
| `RunRecord.device` | `--device`가 준 라벨. 없으면 `null` | 같은 프로필로 돈 두 run이 기록만으로 완전히 같아 보인다 |
| `Benchmark.measured_on` | `bench --apply`가 수치와 함께 쓴다 | registry 값이 어느 하드웨어 기준인지 사라진다 |

`--device`는 **명시 라벨 전용이다. 설정을 바꾸지 않고, 호스트명으로 자동으로 채우지도
않는다.** 근거는 [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md).

```bash
python -m src.cli run --profile local_balanced --target <경로> --device atom
python -m src.cli bench --profile local_balanced --apply --device atom
python -m src.cli doctor --profile local_balanced --device seraph   # registry 불일치 경고
```

라벨은 위 §1 표의 이름을 쓴다: `atom`, `seraph`.

## 5. 관련 문서

- [model-serving.md](model-serving.md) §3 배포 전 검증 체크리스트 · §5 Model Registry
- [evaluation.md](evaluation.md) §2.3 공정성 규칙
- [decisions/0005](decisions/0005-tool-runner-override.md) `--tool-runner` 오버라이드
- [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md) 장비 기록
- [tools/probe/README.md](../tools/probe/README.md) 두 장비에서 같은 절차로 `/metrics` 받기
- [STATUS.md](../STATUS.md) §10 해석 시 주의
