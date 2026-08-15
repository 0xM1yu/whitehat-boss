# 하드웨어 독립성

**이 하네스는 특정 장비를 알지 않는다.** 어느 GPU 장비에서든 같은 코드가 돌고, 장비마다
갈리는 것은 설정과 실행 방식뿐이다. 이 문서는 그 성질이 무엇을 뜻하고 무엇으로 확인되는지,
그리고 **아직 지켜지지 않는 항목**이 무엇인지를 적는다.

특정 장비의 접속·잡 실행 절차는 여기 없다. 그건 운용 사항이라
[tools/probe/README.md](../tools/probe/README.md)와 래퍼 스크립트에 있다.

## 1. 하네스가 장비에 요구하는 것

이 목록이 전부다. 여기 없는 것은 하네스가 신경 쓰지 않는다.

| 요구 | 없으면 | 하네스의 대응 |
| --- | --- | --- |
| Python 3.11+ | 아무것도 안 된다 | — |
| OpenAI 호환 엔드포인트 (실서빙 프로필만) | `fake://` 프로필은 그대로 돈다 | 스킴이 어댑터를 고른다. `models.yaml`의 `endpoint` |
| 컨테이너 런타임 (도구 프로필만) | `tool_runner: fake` 프로필은 그대로 돈다 | `doctor`가 PATH를 보고 WARN. `--tool-runner`로 갈아끼운다 |
| GPU | 실서빙·도구 프로필만 필요 | `dev_fake`·`dev_hybrid`는 GPU 없이 0~9단계를 완주한다 |
| `nvidia-smi` | `mem_gb_ctx8k`를 GB로 못 얻는다 | 사유를 남기고 0으로 둔다. 추정하지 않는다 |

**GPU 아키텍처·드라이버·CUDA 버전·스케줄러·호스트명은 목록에 없다.** 하네스 코드가 그
값들을 읽지 않기 때문이다.

## 2. 무엇으로 확인되는가

주장이 아니라 돌려서 확인한 것들이다.

| 확인 | 방법 | 현재 |
| --- | --- | --- |
| 실행 코드에 장비 고유명이 없다 | `src/**`를 AST로 파싱해 주석·docstring을 뺀 뒤 장비명·아키·스케줄러 이름을 찾는다 | 0건. 남는 것은 `docker`/`singularity`(config가 고르는 런타임 이름)와 `NVFP4`(registry의 양자화 값)뿐이고, 둘 다 장비가 아니라 설정을 가리킨다 |
| 전 프로필이 GPU·네트워크 없이 진단을 통과한다 | 모든 프로필에 `doctor` 실행 | 7/7, ERROR 0건 |
| GPU 없이 파이프라인이 완주한다 | `dev_fake`·`dev_hybrid`로 `run` | 완주 |
| 테스트가 GPU·네트워크 없이 돈다 | CI(`ubuntu-latest`, GPU 없음) | 전 테스트 통과 |
| 프로브가 `pip install` 없이 돈다 | import 그래프를 따라가며 `sys.stdlib_module_names` 밖을 찾는다(`tests/test_probe.py`) | stdlib 밖 0건. `src/__init__.py`까지 따라간다 |
| 측정 절차가 장비 사이에서 같다 | `tools/probe/run_probe.sh` 하나를 두 래퍼가 공유한다 | 래퍼는 잡 실행·접속만 감싼다 |

**주석에 남은 장비명은 의도된 것이다.** `2026-08-14 Seraph 실측`처럼 어떤 사실이 어디서
나왔는지를 밝히는 출처 표기이고, 지우면 수치의 근거가 사라진다. 코드가 그 값을 읽지 않는
한 종속이 아니다.

## 3. 장비에 따라 갈리는 것은 설정이지 코드가 아니다

| 갈리는 것 | 어디서 표현되는가 |
| --- | --- |
| endpoint | `config/models.yaml`의 `endpoint` |
| 컨테이너 런타임 | 프로필의 `adapters.tool_runner`, 또는 `--tool-runner`(ADR 0005) |
| 실행 가능한 양자화 | **별도 model key와 별도 프로필.** 오버레이로 `quantization`을 덮지 않는다 — 그건 같은 모델이 아니다 |
| 잡 실행·접속 | 하네스 밖. 래퍼 스크립트와 운용 문서 |

지금은 endpoint가 갈리지 않는다. 서버와 클라이언트를 같은 노드에서 돌리므로 어느 장비에서든
`127.0.0.1`이다. 그래서 `config/`는 한 벌이고 장비별 오버레이가 없다. 이 전제가 깨지면
(원격에서 붙거나, 공용 계정이라 포트가 사용자별로 갈리거나) 다시 판단한다 —
[decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md)의 "후속" 참조.

## 4. 그래도 어디서 돌았는지는 기록한다

하네스가 장비를 모르는 것과, 그 결과가 어느 장비에서 나왔는지 아는 것은 다른 문제다.
**다른 하드웨어에서 잰 수치는 비교 대상이 아니다.**

| 기록 | 값 | 없으면 |
| --- | --- | --- |
| `RunRecord.device` | `--device`가 준 라벨. 없으면 `null` | 같은 프로필로 돈 두 run이 기록만으로 완전히 같아 보인다 |
| `Benchmark.measured_on` | `bench --apply`가 수치와 함께 쓴다 | registry 값이 어느 하드웨어 기준인지 사라진다 |

`--device`는 **명시 라벨 전용이다. 설정을 바꾸지 않고, 호스트명으로 자동으로 채우지도
않는다.** 근거는 [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md).
[evaluation.md](evaluation.md) §2.3 규칙 5가 이 기록 위에 서 있다.

```bash
python -m src.cli run    --profile local_balanced --target <경로> --device atom
python -m src.cli bench  --profile local_balanced --apply --device atom
python -m src.cli doctor --profile local_balanced --device seraph
```

## 5. 이식성 결함 목록

**어떤 환경에서 하네스가 완주하지 못하는 항목이다. 이 표가 비면 목표가 달성된 것이다.**

각 항목은 "어느 장비가 특별한가"가 아니라 **"하네스의 어느 부분이 아직 환경을 가린다"**로
읽는다.

| # | 항목 | 못 도는 환경 | 상태 |
| --- | --- | --- | --- |
| 1 | `local_balanced`·`b1_single`이 NVFP4 모델을 참조한다 (`qwen3-coder-30b-a3b`, `devstral-small-2-24b`) | Blackwell 이전 아키텍처 | 미해결. 해법: 같은 모델의 다른 양자화를 별도 model key + 별도 프로필로 둔다. **registry 문제이지 코드 문제가 아니다** |
| 2 | R1: 최신 vLLM × Blackwell 커널 호환성 미검증 | 검증 전 | [open-questions.md](open-questions.md) R1. `tools/probe/`가 1차 판정을 낸다 |
| 3 | `tool_images` digest를 런타임 사이에서 비교할 수 없다 | Docker↔Singularity 혼용 | [STATUS.md](../STATUS.md) §10. `RunRecord.tool_runner`가 드러내기만 한다 |

**양쪽 완주가 끝내 불가능하다고 판명되면 단일 장비로 좁히는 것이 옳은 결론이다** —
반쪽짜리 이중 지원이 최악이다. 지금 그 결정을 내리는 것이 아니라, 그 상황이 왔을 때
무엇이 옳은지를 미리 못박는다.

## 6. 현재 확인된 환경

이 표는 **하네스의 요구사항이 아니라 관측 기록이다.** 새 환경이 늘면 줄이 늘 뿐,
하네스가 이 값을 읽지는 않는다.

| 라벨 | 아키텍처 | 컨테이너 런타임 | 확인된 것 |
| --- | --- | --- | --- |
| `atom` | Blackwell (sm_121), CUDA 13.0 | Docker | R1 검증 대기 |
| `seraph` | Ampere (sm_86), CUDA 12.2 상한, vLLM 0.19.1까지 | Singularity 3.7.3 | 2026-08-11 구조화 출력·prefix cache, 2026-08-13 Singularity 도구 실행, 2026-08-14 `/metrics` 원문 |

접속·잡 실행·노드 선택 같은 운용 절차는 [tools/probe/README.md](../tools/probe/README.md)에
있다. 그 내용이 이 문서에 없는 것은 의도다 — 하네스가 알 필요가 없는 사실이기 때문이다.

## 7. 관련 문서

- [model-serving.md](model-serving.md) §3 배포 전 검증 체크리스트 · §5 Model Registry
- [evaluation.md](evaluation.md) §2.3 공정성 규칙
- [decisions/0005](decisions/0005-tool-runner-override.md) `--tool-runner` 오버라이드
- [decisions/0006](decisions/0006-record-the-device-a-run-happened-on.md) 장비 기록
- [tools/probe/README.md](../tools/probe/README.md) 같은 절차로 `/metrics` 받기, 장비별 운용
