# 개발 도구

저장소 유지보수용 일회성 스크립트를 둔다. 파이프라인이 실행 중에 의존하는 코드는
`src/`에, 평가에 쓰는 코드는 `eval/`에 둔다 — 여기 있는 것은 CI와 사람만 부른다.

| 디렉터리 | 내용 |
| --- | --- |
| `harness-tools/` | 2·8단계가 쓰는 컨테이너 이미지(clang·semgrep·인덱서). 아래 참조 |
| `probe/` | vLLM `/metrics` 원문을 두 장비에서 같은 절차로 받는다. 산출물이 그대로 `tests/fixtures/metrics/`의 fixture가 된다 ([probe/README.md](probe/README.md)) |

## harness-tools 이미지

파이프라인 2단계(Signal Collection)와 8단계(Tool Validation)가 `ContainerToolRunner`를 통해
이 이미지 안에서 도구를 돌린다. 이미지가 없으면 `dev_docker`/`dev_singularity` 프로필로
실행할 수 없다.

담긴 것: clang / clang++ (ASAN·UBSAN 런타임 포함), clang-tidy, semgrep, make, cmake,
그리고 **이미지에 구워 넣은 semgrep 룰셋**.

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
docker build -t harness-tools:v1 tools/harness-tools/
```

digest 확인:

```bash
docker image inspect harness-tools:v1 --format '{{index .RepoDigests 0}}|{{.Id}}'
```

**`RepoDigests`는 push된 적 없는 로컬 빌드 이미지에서 비어 있다.** 그 경우 `.Id`가 쓰인다 —
둘 다 그 이미지를 유일하게 지시하는 sha256이고, `ContainerToolRunner.images()`가 이 순서로
고른다. 어느 쪽도 못 읽으면 `tool_images`를 비워 두고 `run`이 경고한다. 값을 지어내지
않는다.

룰셋 커밋을 확인하려면:

```bash
docker run --rm harness-tools:v1 cat /opt/semgrep-rules/COMMIT
```

### ATOM 빌드·실행 (podman)

ATOM은 **Docker 데몬 접근도 singularity도 없고 podman만 있다**(2026-08-17 실측). aarch64라
x86 이미지를 옮겨 쓸 수도 없으므로 **이 장비에서 직접 빌드한다.** podman은 Dockerfile을 그대로
읽으므로 같은 파일로 빌드된다 — 그 자리에서 tree-sitter·semgrep의 arm64 휠 유무까지 검증된다.

```bash
podman build -t harness-tools:v1 tools/harness-tools/
python -m src.cli run --profile <프로필> --tool-runner podman --target <경로>
```

`--tool-runner podman`은 프로필의 런타임을 덮는다(ADR 0005). podman은 docker CLI 호환이라
`container_argv`가 바이너리 이름만 갈고 격리 플래그는 같다. **rootless인데도 `--cpus`·
`--memory`를 강제하므로**(아래 표) SLURM 같은 외부 도구가 필요 없다. digest도 docker와 같은
`image inspect` 포맷으로 읽힌다 — podman은 로컬 빌드에도 `RepoDigests`를 채운다.

825MB(arm64) 이미지가 `dev_docker --tool-runner podman`으로 완주했고(후보 2 / Finding 2),
이는 Seraph의 singularity 결과와 같다 — 세 런타임이 같은 표본에 같은 판정을 낸다.

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
srun -p debug_ce_ugrad -w moana-u3 singularity build /data/daniel3290/sif/harness-tools_v1.sif docker://<레지스트리>/harness-tools:v1
```

레지스트리를 못 쓰는 경우 (로컬에서 tar로 내보내 옮긴다):

```bash
docker save harness-tools:v1 -o harness-tools_v1.tar
```

```bash
scp harness-tools_v1.tar <계정>@seraph:/data/daniel3290/
```

```bash
srun -p debug_ce_ugrad -w moana-u3 --mem=8G singularity build /data/daniel3290/sif/harness-tools_v1.sif docker-archive:///data/daniel3290/harness-tools_v1.tar
```

`--mem=8G`는 변환이 레이어를 푸는 동안 쓰는 메모리다. 실행 자체에는 그만큼 필요 없다 —
도구를 돌릴 때의 상한은 `srun --cpus-per-task=2 --mem=4G`로 줬다(아래 "런타임이 강제하지
못하는 항목").

SIF 경로 규칙은 `harness-tools:v1` → `{sif_dir}/harness-tools_v1.sif`이고, `sif_dir`은
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

### 반대로, 끌 수 없는 항목

| 런타임 | 필드 | 상태 |
| --- | --- | --- |
| docker | — | `read_only`를 끄면 컨테이너 루트에 쓰기가 열린다 |
| podman | — | docker와 같다 — `read_only`를 끄면 루트 쓰기가 열린다 |
| singularity 3.7.3 | `read_only` | **끌 수 없다.** SIF가 immutable squashfs라 루트가 항상 읽기 전용이다 |

**그래서 `read_only=False`는 두 런타임에서 다르게 동작한다.** 가상의 위험이 아니다 — 첫
`dev_docker` 실행에서 semgrep이 `$HOME/.semgrep`에 쓰려다 죽은 것이 루트 쓰기 금지 때문이었고,
`read_only=False`로 돌리면 Docker에서는 그 크래시가 사라지고 Singularity에서는 남는다. 같은
코드에 대한 정적 신호가 런타임에 따라 갈리므로 조건 간 비교가 성립하지 않는다.

`DEFAULT_SANDBOX.read_only`가 True 고정이라 지금은 드러나지 않는다. 끄는 호출부가 생기면
`ContainerToolRunner`가 `network=True`처럼 명시적으로 거절해야 한다.

### 컨테이너 안 경로

| 경로 | 마운트 | 용도 |
| --- | --- | --- |
| `/src` | 분석 대상 루트, **읽기 전용** | semgrep·clang-tidy·clang이 읽는다 |
| `/work` | `{artifacts_root}/{run_id}/scratch/{tool}`, 쓰기 가능 | 컴파일 산출물. `--read-only`가 걸린 루트에서 clang이 `.o`와 실행 파일을 둘 곳이 필요하다 |

`asan`은 컨테이너 **두 번**을 쓴다(컴파일 → 실행). 산출물이 `/work`에 남아야 두 번째
컨테이너가 그 바이너리를 실행할 수 있다.

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
테스트는 이미지가 있을 때만 돈다.

### semgrep 판정

`--json`을 붙여 **결과 문서의 유무로 "실제로 돌았는지"를 판정한다.** exit code만으로는
"발견함"과 "크래시"가 구별되지 않기 때문이다(위 사건). `exit_code`는 프로세스가 반환한 값을
그대로 기록한다 — 발견 여부는 `--error`가 표현하고(발견 0건 → 0, 발견 있음 → 1, 실측 확인),
결과 문서는 그 값이 믿을 만한지만 가른다. 로그 가독성은 각 로그 머리의
`# semgrep: N findings` 한 줄이 대신한다.

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
| `calls` | `{"caller": "parse_header", "callee": "memcpy", "file": "parser.c", "line": 24}` |
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
`unresolved_symbols`다. 그 값이 크면 그 저장소에서 커버리지가 낮다는 뜻이고, E2를 얼마나
믿을지는 그것을 보고 판단한다.

`parse_errors`는 드문 예외가 아니다. `ZEXPORT`·`FAR` 같은 전처리 안 된 매크로가 끼면
tree-sitter가 그 파일을 표시하는데, zlib에서는 43개 중 39개(91%)가 그렇다. **표시됐다고
그 파일의 심볼을 버리지 않는다** — 부분 파싱 결과가 여전히 쓸모 있다.

파서 버전은 `--build-arg TREE_SITTER_VERSION=` / `TREE_SITTER_C_VERSION=` /
`TREE_SITTER_CPP_VERSION=`으로 고정한다. 문법 모듈이 올라가면 같은 코드에서 다른 index가
나올 수 있으므로 이미지 digest가 이 버전을 함께 고정한다.

헤더(`.h`)는 아직 인덱싱하지 않는다 — `compile_sources`가 `_COMPILE_SUFFIXES`로 거르기
때문이고, 그래서 헤더에 정의된 `static inline` 함수가 심볼에 없다.

### 컴파일 방언

clang을 쓰는 모든 도구(`index` / `build` / `clang-tidy` / `asan`)가 `-std=gnu17`을 공유한다
(`container_tools._STD_ARG`). 같은 파일이 한 도구에서 통과하고 다른 도구에서 오류가 나면 그
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
