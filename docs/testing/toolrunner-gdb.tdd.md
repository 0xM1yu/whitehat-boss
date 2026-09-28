# ToolRunner gdb 배선 — TDD 증거

작성일: 2026-09-28

## 원천과 사용자 여정

별도 `*.plan.md`는 없었다. 사용자 요청의 앵커와 수용 조건에서 다음 여정을 뽑았다.

1. 하네스 개발자는 Fake와 컨테이너 ToolRunner에 `gdb`를 요청해 같은 `CRASH` 종류의
   Evidence를 받아야 한다.
2. 판정 코드는 빌드 실패, 정상 실행, 대상 크래시, gdb 자체 기동 실패를 구분하고 대상의
   원 종료 코드를 보존해야 한다.
3. 이미지 빌더는 런타임이 시작된 뒤가 아니라 이미지 빌드 중에 비대화 gdb 배치 경로가
   실행 가능한지 알아야 한다.
4. 운영자는 ptrace 권한을 추측으로 넓히지 않고, 실측된 런타임과 미실측 런타임을 구분해서
   보아야 한다.

설계 선택과 런타임별 실측 범위는 `docs/open-questions/D/D92.md`, 추적 미러는 GitHub
[이슈 #325](https://github.com/0xM1yu/whitehat-boss/issues/325)가 들고 있다. 기존 ASAN
실컨테이너 E4 도달 기록 0건과 분리한 수리는
[이슈 #326](https://github.com/0xM1yu/whitehat-boss/issues/326)이 추적한다.

## RED → GREEN

### RED 체크포인트

- 명령: `python -m pytest -q tests/test_container_tools.py tests/test_adapters.py`
- 결과: **15 failed, 129 passed, 1 skipped**
- 실패 이유: `gdb` 도구·실행 계획·판정·Fake 매핑이 없었고 기본 이미지와 Dockerfile이
  여전히 v2였기 때문이다. 테스트 문법이나 환경 설정 실패가 아니었다.
- 커밋: `c25d5eb test: add RED coverage for gdb ToolRunner`

### GREEN 체크포인트

- 같은 명령: `python -m pytest -q tests/test_container_tools.py tests/test_adapters.py`
- 결과: **144 passed, 1 skipped**
- 커밋: `5bdfbfa feat: wire gdb into ToolRunner adapters`
- 두 체크포인트는 현재 브랜치 `HEAD`의 조상임을 `git merge-base --is-ancestor`로 확인했다.

### 실컨테이너·전체 회귀

- `docker build --progress=plain -t harness-tools:v3 tools/harness-tools/` → **exit 0**.
  gdb·ASAN·clang-tidy·semgrep·인덱서 빌드 연막이 같은 빌드에서 통과했다.
- `python -m pytest -q tests/test_container_tools.py::test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability`
  → **1 passed**. capability 추가 없이 정상 종료와 SIGSEGV 백트레이스를 확인했다.
- ATOM rootless Podman 4.9.3/aarch64에서 현재 v3 Dockerfile 빌드 → **exit 0**. 이어 capability
  없음/`SYS_PTRACE` 추가 조건에서 정상·SIGSEGV 대상을 각각 실행한 총 4회가 exit
  `0·1·0·1`과 같은 정상/백트레이스 출력을 냈다.
- `python -m pytest -q` → **2062 passed, 2 skipped**.
- `python -m ruff check .` → **All checks passed!**

## 테스트 명세

| # | 보증하는 것 | 테스트 또는 명령 | 종류 | 결과 |
| --- | --- | --- | --- | --- |
| 1 | Fake gdb가 `CRASH` Evidence를 내고 존재하는 소스에서는 성공한다 | `tests/test_adapters.py::test_fake_gdb_uses_crash_evidence_and_requires_a_source` | 단위 | PASS |
| 2 | Fake gdb가 없는 소스를 성공으로 만들지 않는다 | `tests/test_adapters.py::test_fake_gdb_uses_crash_evidence_and_requires_a_source` | 단위 | PASS |
| 3 | gdb 계획은 디버그 심볼 컴파일 뒤 `--nx -batch --return-child-result -x` 실행을 낸다 | `tests/test_container_tools.py::test_gdb_plan_compiles_then_runs_a_noninteractive_debugger` | 단위 | PASS |
| 4 | C와 C++를 한 gdb 링크에 섞으면 명시적으로 거부한다 | `tests/test_container_tools.py::test_gdb_refuses_mixed_languages_instead_of_linking_half_a_program` | 단위 | PASS |
| 5 | 빌드 단계에서 끊기면 `ok=False`이며 실행으로 세지 않는다 | `tests/test_container_tools.py::test_gdb_build_failure_never_counts_as_a_debug_session` | 단위 | PASS |
| 6 | 정상 실행은 `ok=True`, exit 0을 보존한다 | `tests/test_container_tools.py::test_gdb_clean_target_is_a_successful_debug_session` | 단위 | PASS |
| 7 | 대상 크래시는 도구 실패가 아니며 non-zero를 보존한다 | `tests/test_container_tools.py::test_gdb_target_crash_is_a_signal_not_a_tool_failure` | 단위 | PASS |
| 8 | exit 125·126·127은 대상 신호가 아니라 gdb 기동 실패다 | `tests/test_container_tools.py::test_gdb_exec_failure_is_not_a_target_crash` | 단위 | PASS |
| 9 | v3 Dockerfile이 gdb를 설치하고 기본 배치 스크립트를 빌드 중 실행한다 | `tests/test_container_tools.py::test_the_tool_image_installs_and_smokes_gdb_batch_mode` | 정적 통합 | PASS |
| 10 | 실제 v3 이미지가 capability 없이 정상·SIGSEGV C 표본을 실행한다 | `tests/test_container_tools.py::test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability` | 실컨테이너 통합 | PASS |
| 11 | Fake·컨테이너 양 경로와 저장소 문서 규약에 회귀가 없다 | `python -m pytest -q` | 전체 회귀 | PASS |

## 커버리지와 남은 공백

`python -m pytest --cov=src --cov-report=term-missing -q` 결과는 **전체 95%**였다.
변경 핵심인 `src/adapters/container_tools.py`는 92%, `src/adapters/fake_tools.py`는 97%다.
80% 기준을 넘는다.

- 전체 실행의 2 skip은 Windows에서 심볼릭 링크 생성이 불가능할 때 건너뛰는 기존
  `tests/test_model_checksum.py` 두 건이다. gdb 테스트는 skip되지 않았다.
- Podman은 ATOM에서 직접 실측했다. Singularity/Apptainer는 로컬 실행 파일이 없고 Slurm
  접속 경로도 열리지 않아 **0회/미실측**이다. D92는 이 조건 때문에 열림이다.
- Docker 실측은 gdb가 새 자식 프로세스를 실행하는 배치 모드만 보증한다. 기존 프로세스에
  attach하는 모드와 pwndbg는 범위 밖이다.
- `validate.tool_for()`는 gdb를 선택하지 않는다. 따라서 독립 ToolRunner 실측은 파이프라인
  E4 도달 실측이 아니며, Auditor/Critic의 gdb 요청 규칙도 이번 변경에 포함하지 않았다.
- gdb 연막을 ASAN 연막 뒤에 둔 Docker 빌드 2회는 해당 `RUN` 단계에서 exit 139였다. 순서를
  바꾼 뒤 전체 빌드는 통과했지만, 앞선 exit 139의 원인은 실측하지 않았다.

## 머지 증거

- RED: `c25d5eb` — 의도한 미구현 때문에 15개 실패.
- GREEN: `5bdfbfa` — 같은 표적 모음 144개 통과.
- 최종: 전체 2062개 통과, Ruff 통과, 전체 커버리지 95%, Docker v3 빌드와 실 gdb 통합
  테스트 통과.

스쿼시 머지를 한다면 위 RED/GREEN/최종 네 줄을 PR 본문 또는 스쿼시 커밋 본문에 보존한다.

## 살아 있는 참조 전수

명제는 **현재 운영 소스·문서·테스트가 기본 이미지 v3와 gdb의 실행류/컴파일 계약을 함께
가리켜야 하며, 과거 run 기록과 현재 노드의 `asan`·`poc` 선택 계약은 바꾸지 않는다**이다.

문형은 ① `harness-tools:vN`·`harness-tools_vN`·`DEFAULT_IMAGE`·`tool_images`,
② `asan`과 `poc`의 인접 열거·`_RUNTIME_TOOLS`·`_REPRO_TOOLS`·`TOOL_KINDS`,
③ clang 도구 열거·컴파일 방언 정본 이름이다. `docs/experiments/`는 과거 run 원문이므로
현재형 교체 대상에서 제외했다.

- 이미지 축: 넓은 `git grep` 후보 **99개**를 코드·운영 문서·테스트에서 대조했고, 느슨한
  손 훑기가 추가로 찾은 미커버 stale 참조는 **0개**였다. `tools/README.md`가 v2의 룰셋
  커밋을 읽는 한 곳은 v3 빌드 입력을 과거 이미지와 맞추는 의도된 참조다.
- 실행/컴파일 계약 축: 넓은 `git grep` 후보 **36개**를 대조했고, 느슨한 손 훑기가 추가로
  찾은 미커버 stale 참조는 **0개**였다. D18·D80의 `SIGNAL_TOOLS`가 `asan`·`poc`만 드는
  곳은 `validate.tool_for()`가 아직 gdb를 선택하지 않는 현재 계약이라 보존했다.
