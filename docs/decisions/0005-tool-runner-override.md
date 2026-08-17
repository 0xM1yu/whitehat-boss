# 0005. `--tool-runner`로 프로필의 도구 런타임을 덮어쓸 수 있게 한다

- 상태: 제안
- 일자: 2026-08-14
- 관련: [evaluation.md](../evaluation.md) §2.3, [model-serving.md](../model-serving.md) §3, [engineering-guide.md](../engineering-guide.md) §5, [STATUS.md](../../STATUS.md) §8·§10, [open-questions.md](../open-questions.md) R1

## 배경

`adapters.tool_runner`는 프로필이 정한다. `docker`와 `singularity`는 같은 구현
(`ContainerToolRunner`)의 런타임 선택이고, 계약과 판정은 하나이며 명령 조립만 갈린다
(`config.AdapterSelection` 주석).

그런데 **같은 프로필을 두 장비에서 돌릴 수 없었다.**

| 장비 | 쓸 수 있는 런타임 | 근거 |
| --- | --- | --- |
| ATOM (GB10) | Docker | R1 검증 대상 장비 |
| Seraph (moana-u3) | Singularity만 | `/usr/bin/docker`가 permission denied (2026-08-13 실측) |

R1은 2026-08-17 마감의 크리티컬 패스이고, ATOM에 올리기 전에 Seraph에서 리허설을 돌려
설정·프롬프트·배선 오류를 걸러내는 것이 유일한 예행 수단이다. 프로필이 런타임을 못박고
있으면 그 리허설을 하려고 **조건 프로필을 복제**해야 하는데, 그 순간 비교표의 조건 축에
장비 축이 섞인다.

같은 날 `local_balanced`·`b1_single`의 `tool_runner`가 `fake` → `docker`가 됐다.
`RunRecord.tool_images`가 비면 그 run은 비교표에서 제외되므로(STATUS.md §8), 그전에는 두
프로필로 완주시켜도 중간 발표 DoD를 만족시킬 수 없었다. 프로필이 실제 도구를 쓰기
시작하면서 "어느 런타임으로 돌릴 것인가"가 비로소 실제 문제가 됐다.

## 결정

`doctor` / `estimate` / `run` / `bench`의 공통 파서에
`--tool-runner {fake,docker,singularity}`를 둔다. 기본값은 `None`(프로필 존중)이고,
지정하면 `HarnessConfig` 로드 직후 `cli.load_profile()` **한 곳에서** 갈아 끼운다 —
`seed` 오버라이드와 같은 경로다.

**그 대가로 `RunRecord.tool_runner`를 필수 필드로 추가한다.** 이것이 이 결정의 핵심이다.
evaluation.md §2.3 규칙 2는 "조건 간 차이는 프로필과 파이프라인 단계 활성화만"이라고
정하는데, 오버라이드는 그 규칙에 예외를 하나 여는 일이다. 예외를 열되 **사후에 구분
가능하게** 만든다: 기록에 남지 않는 예외는 비교표를 조용히 오염시킨다.

`RunRecord.tool_runner`는 오버라이드 때문에만 있는 것이 아니다. STATUS.md §10이
"`tool_images` digest는 런타임 사이에서 비교할 수 없다"를 미해결로 남겨 뒀다 — Docker는
이미지 config의 sha256을, Singularity는 SIF 파일의 sha256을 적으므로 **같은 이미지가
다른 값으로 잡힌다**(2026-08-14 실측). 런타임이 함께 기록되면 최소한 digest를 런타임별로
묶어서 볼 수 있다. §10의 해법은 아니지만 **그 전제 조건**이고, 오버라이드가 없더라도
필요한 필드다.

## 대안과 기각 사유

| 대안 | 기각 사유 |
| --- | --- |
| 장비별 프로필을 추가한다 (`b1_single_singularity` 등) | 조건 축(B1/B3')에 장비 축이 섞인다. 조건이 늘 때마다 장비 수만큼 프로필이 곱해지고, 프로필 하나를 고치면 사본까지 같이 고쳐야 해서 "조건 간 차이는 프로필뿐"이라는 규칙이 오히려 먼저 깨진다 |
| 오버라이드를 열지 않는다 (프로필이 유일한 출처) | 규칙은 지켜지지만 ATOM 투입 전 리허설이 불가능해진다. R1이 마감 3일 전 크리티컬 패스인 상황에서 첫 실행이 곧 본 실행이 된다 |
| 런타임을 자동 감지한다 (PATH에 있는 것을 쓴다) | **가장 위험하다.** 같은 명령이 장비에 따라 다른 것을 실행하고, 그 사실이 기록에도 출력에도 남지 않는다. 명시적 오버라이드는 최소한 누가 무엇을 바꿨는지 커맨드라인에 남는다 |
| 환경변수로 받는다 (`HARNESS_TOOL_RUNNER`) | 실행 커맨드라인에 남지 않아 재현 시 놓치기 쉽다. 이 프로젝트의 다른 오버라이드(`--seed`)도 전부 플래그다 |
| `RunRecord`에 남기지 않고 ADR로만 규율한다 | 문서가 규율하는 것은 사람이고, 비교표를 만드는 것은 기록이다. 반년 뒤 run 30개를 놓고 "이 중 어느 것이 singularity였나"를 답할 수 없다 |

## 결과

**얻는 것**

- 같은 프로필로 Seraph 리허설 → ATOM 본 실행이 가능하다. 두 실행의 차이가
  `RunRecord.tool_runner` 한 필드로 드러난다.
- `doctor`가 **오버라이드된 값**을 검사한다. 프로필에 적힌 값을 검사하면 "docker 있음
  OK"라고 해놓고 실제 run이 singularity로 죽는다.
- `doctor`와 `run`이 오버라이드 사실을 각각 WARN 한 줄로 남긴다. 저장된 로그만으로
  구분되지 않으면 예외를 연 의미가 없다.

**잃는 것 / 위험**

- §2.3 규칙 2에 예외가 하나 생겼다. **런타임이 다른 run을 같은 비교 셀에 넣지 않는다**는
  판단은 사람이 해야 하고, 지금 그것을 강제하는 코드는 없다.
- `tool_images` digest는 여전히 런타임 사이에서 비교할 수 없다(§10). 이 결정은 그 사실을
  **드러나게** 만들 뿐 해소하지 않는다.

**되돌리는 비용: 낮다.** CLI 플래그 하나와 `load_profile()`의 세 줄을 지우면 된다.
`RunRecord.tool_runner`는 되돌리지 않는다 — 오버라이드와 무관하게 필요한 기록이고,
이미 쓰인 run 기록의 스키마를 되돌리는 것이 더 비싸다.

## 이후 드러난 것 (2026-08-15)

**이 플래그는 현재 도달 가능한 사용처가 없다.** 결정을 뒤집지는 않되 사실은 남긴다.

정당화는 "Docker가 없는 장비에서 `b1_single`을 리허설한다"였는데, 그 리허설이 성립하지
않는다:

| 프로필 | `tool_runner` | Singularity만 있는 장비(sm_86)에서 |
| --- | --- | --- |
| `local_balanced` | `docker` | **기동 불가.** 모델 5개 중 3개가 Blackwell 이상을 요구한다(`fp4` ×1, `nvfp4` ×2) |
| `b1_single` | `docker` | **기동 불가.** 세 역할이 전부 `nvfp4` 모델이다 |
| `dev_docker` | `docker` | 오버라이드하면 `dev_singularity`와 같아진다 |
| `dev_singularity` | `singularity` | **오버라이드가 필요 없다.** 프로필이 직접 선언한다 |

즉 오버라이드가 필요한 조합("Singularity만 있는 장비에서 그 프로필을 돌려야 한다")이 지금
registry·프로필 구성에서는 발생하지 않는다. 상세는
[portability.md](../portability.md) §5 #1·#2.

**그래도 `RunRecord.tool_runner`는 유효하다.** 위 "결정"이 적었듯 그 필드는 오버라이드
때문에만 있는 것이 아니다 — `tool_images` digest가 런타임 사이에서 비교되지 않는다는
사실([STATUS.md](../../STATUS.md) §10)의 전제 조건이고, 오버라이드가 하나도 안 쓰여도
필요하다.

플래그를 지울지는 **지금 정하지 않는다.** §5 #1의 해법(같은 모델의 다른 양자화를 별도
model key + 별도 프로필로 두기)이 들어오면 Singularity 장비에서 돌 수 있는 실서빙 프로필이
생기고, 그때 이 플래그의 사용처가 되살아난다. 그 시점에 다시 판단한다.

## 사용처가 생겼다 (2026-08-17)

**위 절의 결론이 뒤집혔다. 다만 예상했던 경로가 아니다.**

2026-08-15의 판단은 그때의 사실 위에서 맞았다. 전제는 **"장비는 둘이고 런타임은 docker와
singularity 둘"**이었고, 그 안에서는 오버라이드가 필요한 조합이 정말로 없었다. 되살아나는
조건으로 §5 #1의 해법(양자화 분리)을 지목한 것도 그 전제를 따른 것이다.

**바뀐 것은 registry가 아니라 장비다.** ATOM 접속이 열려 확인해 보니 하네스가 알던 두
런타임이 **둘 다 없었다** — Docker 데몬은 접근 거부(사용자가 docker 그룹이 아니고 sudo도
없다), singularity는 미설치, podman 4.9.3만 있다. 세 번째 런타임이 생겼고, 그 장비가
**주 장비**다.

| 프로필 | `tool_runner` | podman만 있는 장비(ATOM)에서 |
| --- | --- | --- |
| `local_balanced` | `docker` | **오버라이드 필요.** 도구 실행 경로가 podman이어야 `tool_images`가 찬다 (모델 서빙 가능 여부는 별건 — R1) |
| `b1_single` | `docker` | 동일 |
| `dev_docker` | `docker` | **오버라이드 필요.** 2026-08-17 실측이 정확히 이 경로였다(`dev_docker --tool-runner podman`) |
| `dev_singularity` | `singularity` | 프로필대로는 못 돈다(singularity 부재). `--tool-runner podman`으로 덮으면 `dev_docker`와 같아진다 |

즉 위 절이 "발생하지 않는다"고 적은 조합이 발생했다. **기각 근거가 틀렸던 것이 아니라
전제가 바뀐 것이므로 위 절은 지우지 않는다** — 어떤 사실 위에서 그 판단이 나왔는지가
남아야 다음에 같은 종류의 전제를 의심할 수 있다.

**아직 정하지 않은 것: 프로필의 `tool_runner`를 `docker` → `podman`으로 바꿀 것인가.**
바꾸면 ATOM에서 오버라이드가 필요 없어지는 대신 Docker가 있는 장비에서 필요해진다
(`dev_singularity`가 런타임을 프로필에 직접 선언한 것과 같은 논법이다). **어느 쪽을 골라도
이 플래그의 사용처는 남는다** — 지원 장비가 셋인데 프로필은 런타임을 하나만 적을 수 있기
때문이다. 그래서 이 결정은 플래그의 존폐와 무관하고, 여기서 함께 처리하지 않는다.

## 정했다 — 프로필은 주 실서빙 장비를 선언한다 (2026-08-17, 같은 날 뒤)

위 절이 미정으로 남긴 것을 정했다. **`local_balanced`·`b1_single`의 `tool_runner`를
`docker` → `podman`으로 바꿨다.** 기준은 [portability.md](../portability.md) §3에 명문화했다:
**프로필은 주 실서빙 장비의 런타임을 선언하고 다른 장비가 덮는다.**

**위 절의 표는 이 변경 전 상태를 적은 것이다. 지우지 않는다** — 그 표가 없으면 왜
바꿨는지가 사라진다. 바뀐 뒤는 이렇다:

| 프로필 | `tool_runner` | podman만 있는 장비(ATOM)에서 | 바뀐 것 |
| --- | --- | --- | --- |
| `local_balanced` | **`podman`** | 오버라이드 불필요 | 위 표의 "오버라이드 필요"가 해소됐다 |
| `b1_single` | **`podman`** | 오버라이드 불필요 | 동일 |
| `dev_docker` | `docker` | **오버라이드 필요** (`--tool-runner podman`) | 그대로다 |
| `dev_singularity` | `singularity` | 프로필대로는 못 돈다 | 그대로다 |

**바뀐 것은 사용처의 유무가 아니라 정체다.** 위 절은 이 플래그의 사용처를 "**실서빙
프로필**을 ATOM에서 돌리는 것"으로 지목했는데, 그 근거는 이 변경으로 사라졌다. 대신
**`dev_docker`를 ATOM에서 돌리는 것**이 사용처로 남고, 그것이 2026-08-17 실측이 실제로
지나간 경로다(`dev_docker --tool-runner podman`,
[tools/README.md](../../tools/README.md)). GPU 없는 팀원 노트북은 대부분 Docker이므로
`dev_docker`는 `docker`로 둔다.

**왜 이쪽 방향인가.** 위 절이 적었듯 어느 쪽을 골라도 오버라이드는 남는다. 그러면 남는
질문은 **어느 장비를 예외로 둘 것인가**뿐인데, 오버라이드는 [evaluation.md](../evaluation.md)
§2.3 규칙 2의 예외이고 `RunRecord.tool_runner`에 기록된다 — **주 경로가 상시 예외 상태면
그 기록이 "이 run은 특별했다"를 뜻하지 못한다.** 예외는 드물어야 기록으로 값이 있다.

**반대 방향의 대가가 없다는 것이 결정적이었다.** "바꾸면 Docker가 있는 장비에서 필요해진다"는
위 절의 서술은 실서빙 프로필에 대해서는 성립하지 않는다. `local_balanced`는 모델 5개 중
3개가(`qwen3-8b`=`fp4`, `qwen3-coder-30b-a3b`·`devstral-small-2-24b`=`nvfp4`), `b1_single`은
세 역할이 전부 `nvfp4`라 **Blackwell 미만 장비에서는 애초에 기동하지 못하기 때문이다**
([portability.md](../portability.md) §5 #1). 즉 "Docker는 있는데 이 프로필을 돌려야 하는
장비"가 지금 존재하지 않는다. 그런 장비(GPU + Docker)가 생기면 그때 `--tool-runner docker`를
쓰면 되고, 그것이 이 플래그가 있는 이유다.

**되돌리는 비용: 낮다.** `config/profiles.yaml`의 값 두 줄이다.

## 후속

- 비교표를 만들 때 `tool_runner`가 다른 run을 같은 셀에 넣지 않는지 확인하는 검사를
  `eval/`에 넣을지 정한다. 지금은 규율이 사람에게 있다.
- STATUS.md §10의 "양쪽에서 같은 값이 나오는 이미지 식별자"가 정해지면 이 필드와 함께
  digest 비교 규칙을 다시 쓴다.
- **상태 갱신 조건 (2026-08-17 재작성).** 런타임이 셋이 됐으므로 "두 런타임"을 다시 쓴다:
  **지원 런타임 전부가 같은 표본에서 같은 판정을 내는 것**이 확인되면 상태를 갱신한다.
  판정의 기준은 **semgrep fingerprint 일치**다 — 후보·Finding 개수 일치는 룰이나 줄 번호가
  달라도 우연히 같을 수 있어 근거가 못 된다.

  현재 상태로는 **충족되지 않았다.**

  | 대조 | 확인된 수준 | 근거 |
  | --- | --- | --- |
  | docker ↔ singularity (Seraph) | **fingerprint 일치** | STATUS.md §5 |
  | podman (ATOM) | **후보 2/Finding 2 개수 일치까지만** | tools/README.md, 2026-08-17 |

  ATOM에는 런타임이 podman 하나뿐이라 **그 장비 안에서는 두 런타임을 대조할 수 없다** —
  대조가 장비를 건너뛴다. 그래서 이 조건을 채우려면 ATOM run의 semgrep fingerprint를 다른
  런타임의 것과 직접 맞춰 봐야 한다. 그전까지 상태는 `제안`이다.
