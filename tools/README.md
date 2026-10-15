# 개발 도구

저장소 유지보수용 일회성 스크립트를 둔다. 파이프라인이 실행 중에 의존하는 코드는
`src/`에, 평가에 쓰는 코드는 `eval/`에 둔다 — 여기 있는 것은 CI와 사람만 부른다.

| 디렉터리 | 내용 |
| --- | --- |
| `harness-tools/` | 1·2·8단계가 쓰는 컨테이너 이미지(clang·semgrep·gdb·인덱서). 아래 참조 |
| `slicer/` | 오프라인 Joern 슬라이서 모듈들 — 하네스가 파일(`slices.json`)로 받는 입력을 만든다. 하네스는 import하지 않는다. 사용법은 [slicer-README.md](../docs/slicer-README.md) |
| `slicer_v2/` | `slicer/` 위에 얹힌 확장 — spec에서 자르는 대상 지정 모드(`case` B·F)와 `analysis.input_context`. 사본이 아니라 `slicer/`를 import한다. [README](slicer_v2/README.md) |
| `model_checksum.py` | 모델 스냅샷의 checksum과 manifest를 낸다. registry의 `checksum`·`source`에 붙여넣을 값이고, 절차는 [model-serving.md](../docs/model-serving.md) §5 |
| `oracle_canary.py` | **수동 실행이다 — 하네스가 부르지 않는다.** 이 장비가 **입력과 무관한 크래시**(`139`)를 내는지 네 조합 × N회로 잰다 — 참조 PoC와 무해 입력을 `-vul`·`-fix`에 돌려 기대표와 대조한다(종료 코드: 통과 0 · 어긋남 1 · 못 돌림 2 · N이 작은 약한 통과 3). 🔴 **이 도구를 호출하는 코드가 없다**(문서 밖 호출자 0건) — 사람이 돌려 보고서를 만든다. ⏵ **2026-10-04 — 그 보고서가 채점 run을 막는다**: `--oracle-canary`로 준 JSON을 `src/adapters/canary_report.py`가 읽어 `verdict != pass` · 다른 과제 · `mmap_rnd_bits > 28` · 미측정이면 **제출 전에** 거부한다. 막는 것은 **보고서**이고 호출자는 여전히 0건이다. 🔴 **실 Docker 실행은 2건이고 둘 다 통과했다** — 2026-10-03 `arvo:1065`(MSan, 40/40) · 2026-10-04 `oss-fuzz:368076871`(ASan, 40/40 · `139` 0건). 둘째는 **보고서 파일이 없다**(출처: PR #463 본문 · 같은 PR 리뷰의 재실행 1회). **`mmap_rnd_bits = 32`에서 실패하는 경우는 여전히 0회 관측**이고(통과 쪽만 봤다) argv는 보고서 §13 재현 절차의 *모양*이지 저자 `run_container` 자체가 아니다. 과제 계열은 **둘**을 안다(`arvo` → `n132/arvo` + `/bin/arvo` · `oss-fuzz` → `cybergym/oss-fuzz` + 인자 없는 `/usr/local/bin/run_poc`) — 저자 `server_utils._image_and_command_from_task_id`가 접두사와 명령을 같이 고르므로 이 표도 둘을 같이 든다. 모르는 계열은 거부하고 `--vul-image`/`--fix-image`/`--run-cmd`로 셋 다 받는다. 근거는 [2026-10-02 왕복 보고서](../docs/experiments/harness/2026-10-02-cybergym-oracle-roundtrip-win11wsl.md) · [배관 보고서](../docs/experiments/harness/2026-10-03-cybergym-plumbing-proof-win11.md) §3(**`arvo:1065` 첫 실행만**) · 이슈 #383 |
| `cybergym_task.py` | CyberGym Level 1 과제 번들을 **받는다**(어느 노트북에서든 같은 바이트). 아래 [절](#cybergym_taskpy--과제-번들-받기) |
| `probe/` | **하네스를 안 거치고 서버에 직접 묻는 것들.** 아래 표 ([probe/README.md](probe/README.md)) |

### `probe/` 안의 것들

같은 디렉터리지만 **답하는 질문이 다르다.** 첫 행(metrics)은 서버가 값을 내주는가를 묻고,
나머지는 모델이 무엇을 내놓는가를 묻는다.

| 스크립트 | 답하는 질문 | 관련 |
| --- | --- | --- |
| `atom.sh`·`seraph.sh`·`run_probe.sh`·`probe_metrics.py` | vLLM `/metrics`가 우리가 읽는 이름으로 값을 내주는가. 산출물이 그대로 `tests/fixtures/metrics/`의 fixture가 된다 | R6 |
| `d67_required_schema.py` | 구조화 출력 스키마를 `required`로 올리면 Auditor가 `path`·`evidence_refs`를 **채우는가**. 보존된 run의 프롬프트를 원문 그대로 재생하고 **스키마만** 바꾼다 | [D67](../docs/open-questions/D/D67.md) · #172 |
| `gpt_oss_structured.py` | `gpt-oss-20b`의 structured output 정답률이 **serve 설정**으로 갈리는가. 프로브를 고정하고 `vllm serve` 인자만 바꿔 팔을 만든다 | [2026-09-09 보고서](../docs/experiments/harness/2026-09-09-gpt-oss-20b-onboarding-atom.md) §5 · R8 |
| `critic_output_budget.py` | 보존된 run의 Finding에서 Critic 프롬프트를 재구성해 **출력 토큰이 무엇을 먹는가**(추론 대 본문)를 가른다 | #79 · #172 |
| `d63_reranker_input_shape.py` | `qwen3-reranker-4b`가 **우리 입력 모양**(식별자 질의 × 코드 조각)에서 취약을 무관 위로 올리는가. `/rerank`에 Qwen3 양식을 씌워/안 씌워 판정 | [D63](../docs/open-questions/D/D63.md) · #152 |

⚠ **뒤의 둘은 `cli bench`·`cli run`을 대체하지 않는다.** 하네스가 실제로 보내는 요청이
아니기 때문이다 — R8의 닫히는 조건 **1**(`structured_output.valid_ratio` = **1.0**)은 `cli bench`에만
있다. ⚠ **조건 3(Critic 반박 정확도)은 `cli bench`에도 없다** — 고정 반박 표본이 선행이고
표본 크기·합격선은 [D4](../docs/open-questions/D/D4.md)가 정한다. 프로브가 내는 정답률은
그 표본이 아니다(팔을 가르는 A/B용이다).

## `cybergym_task.py` — 과제 번들 받기

```bash
python tools/cybergym_task.py arvo:1065 --out eval/datasets/arvo-1065 [--server http://127.0.0.1:8666]
```

- **받는 것은 두 파일뿐이다** — HF `sunblaze-ucb/cybergym`(공개·무인증)의 `repo-vul.tar.gz`·`description.txt`.
  같은 폴더의 `error.txt`·`patch.diff`·`repo-fix.tar.gz`는 정답 자료라 **요청하지 않는다**(Level 1).
- **revision은 커밋으로 고정**이다(`DEFAULT_REVISION`, `--revision`으로만 바꾼다). 받은 바이트는 같은
  revision의 트리 API가 주는 sha256·크기와 대조하고, 다르면 파일을 남기지 않는다(둘 다 받아 검증한 **뒤에**
  옮긴다). 응답이 트리의 크기를 넘으면 읽는 도중에 끊고, 크기 초과·해시 불일치는 **다시 걸지 않는다**
  (재시도는 5xx·연결 오류·중간 끊김만). 트리에 sha256(`lfs`)이 없는 파일은 받지 않는다. 출력에 sha256이 찍힌다.
- `--out`은 비어 있거나 **이 도구가 만든 같은 번들**일 때만 쓴다. 다른 파일이 있으면 거절한다(정답 자료
  섞임 방지). 끝에서 하네스 가드(`check_task_dir`)를 그대로 통과하는지 본다.
- `submit.sh`(채점기 신원)도 만든다. **agent_id는 run마다 새로 쓰는 것이 권장**이다(채점기가 같은 신원의
  PoC를 캐시하지 않게) — 이미 있으면 그대로 두고 `--force-submit`일 때만 새 agent_id로 다시 만든다.
  기존 `submit.sh`가 다른 과제의 것이거나 서버가 `--server`와 다르면 거절한다(조용히 무시하지 않는다).
- 종료 코드: 0 성공 · 1 다운로드·검증 실패 · 2 인자·입력 거절. 표준 라이브러리만 쓴다.

## harness-tools 이미지

파이프라인 2단계(Signal Collection)와 8단계(Tool Validation)가 `ContainerToolRunner`를 통해
이 이미지 안에서 도구를 돌린다. 이미지가 없으면 `dev_docker`/`dev_singularity` 프로필로
실행할 수 없다.

담긴 것: clang / clang++ (ASAN·UBSAN 런타임 포함), clang-tidy, gdb, semgrep, make, cmake,
tree-sitter 인덱서, 그리고 **이미지에 구워 넣은 semgrep 룰셋**.

### 룰셋을 왜 굽는가

샌드박스는 `--network=none`으로 뜬다(`SandboxSpec.network=False`). 그래서
`semgrep --config=p/c`처럼 레지스트리에서 룰을 내려받는 방식은 실행 시점에 실패한다.
우회할 수 없다 — `network=True`는 disclosure-policy.md 위반이라 ToolRunner가 거부하는 값이다.

빌드 시점에 받아 두 곳에 넣는다.

| 경로 | 내용 | 고정 방법 |
| --- | --- | --- |
| `/opt/semgrep-rules/c` | 공개 [semgrep-rules](https://github.com/semgrep/semgrep-rules)의 C 부분 | `--build-arg SEMGREP_RULES_COMMIT=<40 hex>`. 해석된 커밋은 이미지 안 `/opt/semgrep-rules/COMMIT`에 남는다 |
| `/opt/semgrep-rules/harness` | `tools/harness-tools/rules/*.yaml` (이 저장소가 버전 관리) | 이 저장소의 커밋 |

semgrep 자체 버전은 `--build-arg SEMGREP_VERSION=` (기본 1.86.0)로 고정한다.

**부수 효과가 요점이다**: 룰셋이 이미지 안에 있으므로 이미지 digest가 도구 버전과 룰셋
버전을 함께 고정한다. `RunRecord.tool_images`에 그 digest가 박히므로, 두 run이 같은
digest면 같은 도구·같은 룰로 돈 것이다.

### 로컬 빌드 (Docker)

```bash
docker build -t harness-tools:v3 tools/harness-tools/
```

**#304(index-v3) 전에 빌드한 v3 이미지는 다시 빌드한다** — 인덱서가 이미지에 구워지므로 낡은 이미지는
index-v2를 내고, `index_graph`의 v3 조회(`writes_of`·`fn_pointer_targets` 등)가 `None`이 된다.
태그는 그대로라 run끼리는 `tool_images` digest로 가른다(Dockerfile 머리 주석). sif도 같다.
⏵ 2026-10-04 -> 인덱서가 index-v4(`includes`·`enum_constants`)가 됐다. 그 전에 빌드한 이미지는 v3를 내고
정의 조회의 include 도달성·enum 상수가 안 붙는다(옛 규칙 그대로) -> 다시 빌드한다.

**v2가 있는 장비에서 v3를 만들 때는 룰셋 커밋을 v2와 맞춘다** — `SEMGREP_RULES_COMMIT`의 기본값이
브랜치(`develop`)라 그냥 빌드하면 gdb만 더하려던 이미지에서 정적 신호까지 움직인다. v1·v2 이미지는
지우지 않는다(지난 run 매니페스트의 `tool_images`가 그 digest를 적었다).

```bash
docker build -t harness-tools:v3 --build-arg SEMGREP_RULES_COMMIT="$(docker run --rm harness-tools:v2 cat /opt/semgrep-rules/COMMIT)" tools/harness-tools/
```

digest 확인:

```bash
docker image inspect harness-tools:v3 --format '{{index .RepoDigests 0}}|{{.Id}}'
```

**`RepoDigests`는 push된 적 없는 로컬 빌드 이미지에서 비어 있다.** 그 경우 `.Id`가 쓰인다 —
둘 다 그 이미지를 유일하게 지시하는 sha256이고, `ContainerToolRunner.images()`가 이 순서로
고른다. 어느 쪽도 못 읽으면 `tool_images`를 비워 두고 `run`이 경고한다. 값을 지어내지
않는다.

룰셋 커밋을 확인하려면:

```bash
docker run --rm harness-tools:v3 cat /opt/semgrep-rules/COMMIT
```

### ATOM 빌드·실행

> ⏵ 2026-10-08 — ATOM은 2026-09-29 사용이 끝났다(#340 · [portability.md](../docs/portability.md) §6). 이 절은
> 그때의 절차다. 그 뒤 도구까지 돈 완주는 하네스·도구가 로컬 Docker이고 서빙만 vast였다(`--device vastai-remote`) —
> Windows `run_20261003_0003`([2026-10-04 보고서](../docs/experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md)
> §2·§4 — 도구 4개 중 2개 ok)와 `run_20261007_0001`([2026-10-08 보고서](../docs/experiments/harness/2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md) 메타).
> 전부가 아니다: WSL `run_20261003_0002`는 Docker 소켓이 없어 도구 4개 전부 실패했고(10-04 보고서 §4),
> 2026-10-05 PoV 프로브는 `tool_runner: fake`다([2026-10-06 보고서](../docs/experiments/harness/2026-10-06-pov-seat-probe-vastai.md) 머리 표).

ATOM에는 **docker 29.2.1과 podman 4.9.3(rootless)이 함께 있고 singularity는 없다**
(2026-08-21 확인, 2026-08-28 `ssh atom 'which podman; which docker'` 재확인 —
[portability.md](../docs/portability.md) §6). aarch64라 x86 이미지를 옮겨 쓸 수 없으므로
**이 장비에서 직접 빌드한다.** 두 런타임 다 Dockerfile을 그대로 읽으므로 같은 파일로
빌드되고, 그 자리에서 tree-sitter·semgrep의 arm64 휠 유무까지 검증된다.

> **⚠ 이 절은 2026-09-09까지 "ATOM은 Docker 데몬 접근도 singularity도 없고 podman만
> 있다(2026-08-17 실측)"로 적고 있었다.** 그 실측은 그날의 관측으로 유효하지만 **4일 뒤
> docker 29.2.1이 확인되면서 전제가 반증됐다** —
> [ADR 0005](../docs/decisions/0005-tool-runner-override.md) 마지막 절 ①. 같은 반증이
> `config/profiles.yaml`·`src/config.py`·`src/adapters/factory.py`·[portability.md](../docs/portability.md) §6에는
> 2026-08-28에 반영됐는데(`eb0e30c`·`a69d69f`) **이 파일은 빠졌다**: [experiments/README.md](../docs/experiments/README.md)
> 확정 #6이 정정 대상을 앞의 둘로만 적었고 여기를 세지 않았다. CLAUDE.md §2.3(바꾼 값을
> 가리키던 곳을 전수로 찾는다)이 잡으려던 자리다.

```bash
# 둘 중 어느 쪽으로 빌드해도 된다. 다만 이미지 저장소가 갈려 있어 digest가 서로 다르다
# (2026-08 같은 harness-tools:v1에 podman RepoDigest sha256:3a63ba2e… / docker sha256:b5d7048b… —
#  experiments/README.md 확정 #6). 실행할 런타임 쪽에 빌드해 둔다. 룰셋 커밋은 위 절처럼 맞춘다.
docker build -t harness-tools:v3 tools/harness-tools/

# 실서빙 프로필은 `docker`를 선언하고 ATOM에 docker가 있다 — 플래그가 필요 없다 (2026-08-28)
# `--slices`는 런타임과 무관하게 필요하다 — Scout v3가 슬라이스 입력이라 없으면 시작 전에 거부한다
python -m src.cli run --profile harness_pool --target <경로> --slices <slices.json> --device atom --condition B3prime@pipeline

# `dev_docker`도 이 장비에서는 덮을 것이 없다(2026-08-21 이후). 덮어야 하는 것은 ATOM에
# 없는 런타임을 선언한 프로필뿐이고, 지금 그것은 `dev_singularity` 하나다
python -m src.cli run --profile dev_singularity --tool-runner docker --target <경로> --device atom
```

실서빙 프로필(`harness_pool`·`_hybrid`)이 `docker`인 것은 주 실서빙 장비가 ATOM이기 때문이다
(docs/portability.md §3 — 프로필은 주 실서빙 장비의 런타임을 선언하고 다른 장비가 덮는다).
**기준은 그대로이고 바뀐 것은 ATOM의 런타임이 무엇이냐다** — 2026-08-17~08-28 사이에는 같은
기준 아래 `podman`이었고, 되돌린 경위는
[ADR 0005](../docs/decisions/0005-tool-runner-override.md) 마지막 절이다. `--tool-runner`는
프로필이 선언한 런타임이 그 장비에 없을 때 쓴다(ADR 0005).
⏵ 2026-10-08 — 「주 실서빙 장비가 ATOM」은 낡았다(위 절 머리의 ⏵). 도구를 돌리는 로컬도 `docker`다
(2026-10-04 보고서 §2 — Docker 29.5.3).

podman은 docker CLI 호환이라 `container_argv`가 바이너리 이름만 갈고 격리 플래그는 같다.
**rootless인데도 `--cpus`·`--memory`를 강제하므로**(아래 표) SLURM 같은 외부 도구가 필요
없다. digest도 docker와 같은 `image inspect` 포맷으로 읽힌다 — podman은 로컬 빌드에도
`RepoDigests`를 채운다. **다만 적는 값의 종류가 달라 런타임 사이에서는 비교되지 않는다**
(docs/portability.md §5 #4).

825MB(arm64) 이미지가 `dev_docker --tool-runner podman`으로 완주했고(후보 2 / Finding 2,
`run_20260817_0001`), 이는 Seraph의 singularity 결과와 같다 — 세 런타임이 같은 표본에 같은
판정을 낸다. **그 run이 이 저장소의 유일한 podman 도구 실행 기록이다**(docs/portability.md §6).

### Seraph 이관 (Singularity)

moana-u3에는 `/usr/bin/docker`가 있지만 permission denied이고(사용자가 docker 그룹이 아니다)
`/usr/local/bin/singularity` 3.7.3만 쓸 수 있다. **무거운 작업은 반드시 컴퓨트 노드에서
한다 — 로그인 노드에서 빌드하지 않는다.**

**2026-08-14 실측: 레지스트리 없이 `docker-archive://`로 끝났다.** Singularity 3.7.3이
buildx가 만든 tar(manifest list + attestation manifest 포함)를 그대로 받아 8개 레이어를
풀었다 — `DOCKER_BUILDKIT=0`으로 다시 빌드할 필요가 없었다. 소요는 `docker save` 247MB,
`scp` 6초, SIF 변환 몇 분, 결과 SIF 241MB다. 아래 두 경로 중 **tar 경로를 먼저 시도해
보라** — 레지스트리 계정이 없어도 되고 한 번에 끝난다.

레지스트리를 쓸 수 있는 경우:

```bash
export SINGULARITY_CACHEDIR=/data/daniel3290/.cache/singularity
srun -p debug_ce_ugrad -w moana-u3 singularity build /data/daniel3290/sif/harness-tools_v3.sif docker://<레지스트리>/harness-tools:v3
```

레지스트리를 못 쓰는 경우 (로컬에서 tar로 내보내 옮긴다):

```bash
docker save harness-tools:v3 -o harness-tools_v3.tar
```

```bash
scp harness-tools_v3.tar <계정>@seraph:/data/daniel3290/
```

```bash
srun -p debug_ce_ugrad -w moana-u3 --mem=8G singularity build /data/daniel3290/sif/harness-tools_v3.sif docker-archive:///data/daniel3290/harness-tools_v3.tar
```

`--mem=8G`는 변환이 레이어를 푸는 동안 쓰는 메모리다. 실행 자체에는 그만큼 필요 없다 —
도구를 돌릴 때의 상한은 `srun --cpus-per-task=2 --mem=4G`로 줬다(아래 "런타임이 강제하지
못하는 항목").

SIF 경로 규칙은 `harness-tools:v3` → `{sif_dir}/harness-tools_v3.sif`이고, `sif_dir`은
환경변수 `HARNESS_SIF_DIR`, 없으면 `{artifacts_root}/sif`다.

```bash
export HARNESS_SIF_DIR=/data/daniel3290/sif
```

**SIF가 없어도 자동으로 받아오지 않는다.** 실행 중에 네트워크를 쓰면 `network=False` 정책과
충돌하므로, 없으면 `Evidence(ok=False, error="SIF가 없다: ...")`로 남기고 멈춘다.

### 런타임이 강제하지 못하는 항목

| 런타임 | `SandboxSpec` 필드 | 상태 |
| --- | --- | --- |
| docker | 전부 | `--network=none` / `--read-only` / `--cpus` / `--memory` / `timeout` 모두 적용 |
| podman | 전부 | docker와 같다 — rootless인데도 cgroup v2 위임으로 `--cpus`·`--memory`를 강제한다 (2026-08-17 ATOM 실측: `--memory=256m` → 컨테이너 안 `memory.max` 268435456) |
| singularity 3.7.3 | `cpu_limit`, `memory_mb` | **강제되지 않는다.** HPC에서는 SLURM(`--cpus-per-task`, `--mem`)이 담당한다 |

강제되지 않는 항목은 `Evidence.error`에 남기지 않는다 — 실행은 성공했고, 자원 상한이 안
걸린 것은 실행 실패가 아니다. 목록은 `src/adapters/container_tools.py`의 `UNENFORCED`가
들고 있다. Singularity로 돌릴 때 상한이 필요하면 `srun`에 붙인다.

gdb의 자식 실행형 배치 경로는 2026-09-28 Docker와 ATOM rootless Podman 4.9.3에서
`SYS_PTRACE` capability 없이 정상 종료·SIGSEGV 백트레이스를 둘 다 냈다. Podman은 권한
없음/추가 두 조건에서 정상·크래시를 각각 실행한 **4회 실측**이고 결과가 같았다.
Singularity/Apptainer만 접속 경로를 확보하지 못해 **0회/미실측**이며, 권한 필요 여부를
확정하지 않는다([D92](../docs/open-questions/D/D92.md)). attach 모드는 이 결과의 범위가 아니다.
Docker ptrace 측정 이미지의 gdb는 15.1(`uaf-note-verify:latest`)이고, v3 이미지(gdb 12.1)를
2026-09-30 재측정해도 SIGSEGV 대상의 gdb 종료 코드는 255로 같았다(자세한 값은 [D92](../docs/open-questions/D/D92.md)).

### 반대로, 끌 수 없는 항목

| 런타임 | 필드 | 상태 |
| --- | --- | --- |
| docker | — | `read_only`를 끄면 컨테이너 루트에 쓰기가 열린다 |
| podman | — | docker와 같다 — `read_only`를 끄면 루트 쓰기가 열린다 |
| singularity 3.7.3 | `read_only` | **끌 수 없다.** SIF가 immutable squashfs라 루트가 항상 읽기 전용이다 |

**그래서 `read_only=False`는 런타임에 따라 다르게 동작한다** — Docker·podman은 루트 쓰기가
열리고 Singularity는 그대로 막힌다(위 표). 가상의 위험이 아니다 — 첫 `dev_docker` 실행에서
semgrep이 `$HOME/.semgrep`에 쓰려다 죽은 것이 루트 쓰기 금지 때문이었고, `read_only=False`로
돌리면 Docker에서는 그 크래시가 사라지고 Singularity에서는 남는다. 같은 코드에 대한 정적
신호가 런타임에 따라 갈리므로 조건 간 비교가 성립하지 않는다.

`DEFAULT_SANDBOX.read_only`가 True 고정이라 지금은 드러나지 않는다. 끄는 호출부가 생기면
`ContainerToolRunner`가 `network=True`처럼 명시적으로 거절해야 한다.

### 컨테이너 안 경로

| 경로 | 마운트 | 용도 |
| --- | --- | --- |
| `/src` | 분석 대상 루트, **읽기 전용** | semgrep·clang-tidy·clang·gdb가 읽는다 |
| `/work` | `{artifacts_root}/{run_id}/scratch/{tool}`, 쓰기 가능 | 컴파일 산출물. `--read-only`가 걸린 루트에서 clang이 `.o`와 실행 파일을 둘 곳이 필요하다 |

`asan`과 `gdb`는 각각 컨테이너 **두 번**을 쓴다(컴파일 → 실행/디버그). 산출물이 `/work`에
남아야 두 번째 컨테이너가 그 바이너리를 실행할 수 있다.

**쓰기가 필요한 환경변수는 전부 `/work`를 가리킨다.** `--read-only`에서는 `/tmp`도 `$HOME`도
쓸 수 없다.

| 변수 | 값 | 이유 |
| --- | --- | --- |
| `TMPDIR` | `/work` | 링커와 일부 clang 경로가 임시 파일을 만든다 |
| `HOME` | `/work` | semgrep이 `$HOME/.semgrep/settings.yml`을 쓴다. 2026-08-14 첫 실행에서 이것 때문에 처리되지 않은 예외로 죽었고, 파이썬 크래시의 exit 1이 "발견함"으로 집계됐다 |

`SEMGREP_SETTINGS_FILE`로 좁게 막지 않은 이유: `TMPDIR`로 이미 한 번 겪은 **클래스 단위
결함**이라, 도구가 하나 늘 때마다 같은 사고를 반복하지 않으려면 쓰기 경로 자체를 옮기는
편이 낫다. 파이썬 계열 도구가 추가돼도 같은 방어가 적용된다.

**빌드 시점 연막 검사로는 이 결함을 잡을 수 없다** — 빌드 FS는 쓰기 가능하다. 회귀 그물은
`tests/test_container_tools.py::test_real_container_splits_vulnerable_from_benign`이고, 이
테스트는 이미지가 있을 때만 돈다. gdb의 정상·SIGSEGV 실컨테이너 경로는
`test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability`가 같은 조건으로 검증한다.

### semgrep 판정

`--json`을 붙여 **결과 문서의 유무로 "실제로 돌았는지"를 판정한다.** exit code만으로는
"발견함"과 "크래시"가 구별되지 않기 때문이다(위 사건). `exit_code`는 프로세스가 반환한 값을
그대로 기록한다 — 발견 여부는 `--error`가 표현하고(발견 0건 → 0, 발견 있음 → 1, 실측 확인),
결과 문서는 그 값이 믿을 만한지만 가른다. 로그 가독성은 각 로그 머리의
`# semgrep: N findings` 줄이 대신한다(대상을 반쪽만 파싱했으면 그 아래 둘째 줄이 그 사실을 싣는다).

### 인덱서 (1단계)

`index`는 `indexer.py`(tree-sitter)다. 심볼 표와 호출 그래프를 `/work/index.json`에 쓴다 —
대상 마운트가 읽기 전용이라 산출물은 scratch에만 떨어질 수 있다.

**`clang -fsyntax-only`를 걷어낸 이유**는 구문 검사가 그래프가 아니라서만이 아니다. 그 명령은
include 경로 설정이 필요한 실제 저장소에서 **실패**해 `Evidence(ok=False)`가 되고, 게이트가
`ok` 아닌 Evidence를 거르므로 **모든 Finding이 E2를 넘지 못했다.** tree-sitter는 전처리도
링크도 하지 않아 include 경로 없이 파싱된다.

| 뽑는 것 | 예 |
| --- | --- |
| `symbols` | `{"name": "parse_header", "file": "parser.c", "line_start": 19, "line_end": 26}` |
| `calls` | `{"memcpy": [{"file": "parser.c", "line": 24, "caller": "parse_header", "args": ["buf", "src", "len"]}]}` — callee → 호출 지점(index-v2. v1은 같은 간선의 목록이었다) |
| 조회 표(index-v2) | `functions`·`macros`·`types`·`file_vars`·`fields`·`locals`·`assigns`·`field_assigns` — 모양은 `indexer.py` 모듈 docstring. NE resolver 몫이고 캡슐로 가지 않는다 |
| 조회 표(index-v3, #304) | `writes`(원소·역참조 대입 · 함수 안 선언 초기화) · `fn_bindings`(함수 포인터 필드에 묶인 함수 — 필드 대입·지정 초기화) · `field_calls`(`x->f(...)` 호출 지점) — v2 표는 그대로다. 읽는 쪽은 `index_graph`의 `writes_of`·`field_writes_of`·`fn_pointer_targets`·`indirect_callees_of` ⏵ 2026-10-10 그리고 NE resolver — `writes`의 원소·역참조 행을 대입 라벨(3·4·13)에 싣는다(`table()`로 직접 · #304) |
| 조회 표(index-v4) | `includes`(파일 → `#include` 지시문 — 경로 원문 · 꺾쇠 여부) · `enum_constants`(enum 상수 → 그 enum 정의 전체 범위) · `types`의 앞선 선언 typedef 행에 `forward` — v3 표는 그대로다. 읽는 쪽은 `index_graph.definitions_of`(여러 정의 중 앵커가 include로 닿는 것이 하나뿐이면 그것) |
| `unresolved_calls` | 대상을 특정하지 못한 호출 **수** |
| `unresolved_symbols` | 정의를 찾았는데 이름을 못 뽑은 **수** |
| `files` | 실제로 인덱싱한 파일 — 분모다. `symbols`가 적을 때 "함수가 없다"와 "파일이 안 왔다"를 가른다 |
| `skipped` · `parse_errors` | 문법이 없어 건너뛴 파일 · 부분 파싱된 파일 |

정의는 `#ifdef` 블록·C++ 클래스 본문·`namespace`·`extern "C"` 안에도 있으므로 파일의
최상위만 보지 않고 **트리 전체**를 훑는다. 최상위만 보면 zlib(.c)에서 28.3%,
nlohmann/json(.cpp)에서 70.3%가 사라진다. `#if`/`#else` 양쪽 가지는 **둘 다** 낸다 —
구문만으로는 어느 쪽이 컴파일되는지 알 수 없고, 한쪽을 고르면 근거 없는 추측이 된다.

**이 그래프는 근사다.** 구문만 보므로 함수 포인터 호출(`(*fp)(x)`)·매크로로 감싼 호출·같은
이름의 파일 스코프 `static` 함수를 구분하지 못한다. 놓친 것을 조용히 버리거나 이름을
지어내지 않고 **센다** — 호출부의 누락은 `unresolved_calls`, 심볼의 누락은
`unresolved_symbols`다. index-v3부터 그중 필드 경유 호출(`vv->f(x)`)은 `field_calls`에 필드
이름이 남고 `fn_bindings`로 후보 함수를 이을 수 있다 — 수는 그대로 `unresolved_calls`에 센다. 그 값이 크면 그 저장소에서 커버리지가 낮다는 뜻이고, E2를 얼마나
믿을지는 그것을 보고 판단한다.

`parse_errors`는 드문 예외가 아니다. `ZEXPORT`·`FAR` 같은 전처리 안 된 매크로가 끼면
tree-sitter가 그 파일을 표시하는데, zlib에서는 43개 중 39개(91%)가 그렇다. **표시됐다고
그 파일의 심볼을 버리지 않는다** — 부분 파싱 결과가 여전히 쓸모 있다.

파서 버전은 `--build-arg TREE_SITTER_VERSION=` / `TREE_SITTER_C_VERSION=` /
`TREE_SITTER_CPP_VERSION=`으로 고정한다. 문법 모듈이 올라가면 같은 코드에서 다른 index가
나올 수 있으므로 이미지 digest가 이 버전을 함께 고정한다.

인덱서에 넘길 확장자는 clang에 넘길 확장자와 **따로 정한다**
(`container_tools._INDEX_SUFFIXES` vs `_COMPILE_SUFFIXES`). tree-sitter는 전처리를 하지
않으므로 `#include`로 끌려오는 파일을 **직접 주지 않으면 보지 못한다** — 실측 평가 샘플
3건이 `target.c` 28바이트(`#include "session_core.inc"` 한 줄) + `.inc` 17KB 구조라,
`.inc`를 넘기지 않으면 그 17KB가 통째로 `skipped`가 된다. 반대로 clang 계열은 `.inc`를
받으면 안 된다 — 함수 본문 조각이라 단독 컴파일이 되지 않아 **일어나지 않은 빌드 실패가
신호로 기록된다.** 그래서 두 목록을 합치지 않는다.

헤더(`.h`)도 인덱서에 넘긴다(index-v2, 2026-09-26) — 매크로·타입 정의와 `static inline` 함수가
대개 거기 있어서다. clang 계열에는 여전히 안 준다. **대상 트리 밖의 헤더는 아직 안 온다** — 인덱서는
대상 트리 안 파일만 받는데, 평가 표본의 `build_context/generated_headers`(설정 매크로)는 트리
밖이다(슬라이서는 그것을 `-I`로 본다).

### 컴파일 방언

컴파일하는 모든 clang 계열 도구(`build` / `clang-tidy` / `asan` / `gdb`)가 언어별 방언을
공유한다. C는 `-std=gnu17`, C++는 `-std=gnu++17`이며 값의 정본은
`container_tools._STD_ARGS`다. 같은 파일이 한 도구에서 통과하고 다른 도구에서 오류가 나면 그
차이가 코드의 성질인지 우리 플래그의 산물인지 구분할 수 없다. 기본값에 맡기지 않는 이유는
이미지의 clang을 올리면 기본 방언이 움직이기 때문이다(clang 18에서 gnu17→gnu23).
실제 저장소는 프로젝트마다 방언이 다르고 `compile_commands.json`이 있으면 그쪽을 따라야
한다 — 그 시점에 이 값은 config로 옮긴다.

### 확인

```bash
python -m src.cli doctor --profile dev_docker --artifacts-root artifacts
```

런타임이 PATH에 없으면 `[WARN ] docker 실행 파일이 PATH에 없다`가 나온다. 그 상태로 run하면
`build_deps()`에서 `SandboxUnavailable`로 즉시 끊긴다(도구 실행 시점까지 가지 않는다).
