# ToolRunner gdb 배선 — TDD 증거

작성일: 2026-09-28

## 원천과 사용자 여정

별도 `*.plan.md`는 없었다. 사용자 요청의 앵커와 수용 조건에서 다음 여정을 뽑았다.

1. 하네스 개발자는 Fake와 컨테이너 ToolRunner에 `gdb`를 요청해 같은 `CRASH` 종류의
   Evidence를 받아야 한다.
2. 판정 코드는 빌드 실패, 정상 실행, 대상 크래시(신호), 대상 기동 실패(gdb·컨테이너 포함)를
   구분하고, `Evidence.exit_code`에는 gdb 프로세스의 종료 코드를 그대로 남긴다 — 대상이
   스스로 끝났으면 그 종료 코드와 같고, 신호로 멈춘 뒤 gdb가 죽이면 x86-64 Docker(gdb
   12.1·15.1)에서 255, ATOM Podman(aarch64·gdb 12.1) 기록은 1이다(원인 미실측). 대상이 실제로
   떴는지는 종료 코드가 아니라 gdb가 stdout에 남긴 대상 수명주기 줄로 가린다.
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
- `python -m pytest -q` → **2064 passed, 2 skipped**.
- `python -m ruff check .` → **All checks passed!**

### ⏵ 2026-09-30 — HIGH-1 수정 (gdb 대상 수명주기 판정)

적대적 리뷰 HIGH-1: gdb는 마지막 명령이 gdb 자신이라 종료 코드로는 대상이 떴는지 못 가른다
(안 뜬 대상도 exit 1·255, `return 127;` 대상은 127). 종료 코드만 보던 `judge()`의 gdb 갈래가
안 뜬 대상을 `ok=True, kind=CRASH`로 읽어 게이트가 E4로 받았다.

- RED: `e7f282f` — `tests/test_gdb_lifecycle_judgement.py`(33개, 녹음된 gdb 12.1·15.1 출력).
  종료 코드만 보는 head에서 **11 failed, 22 passed**.
- GREEN: `d64a2b1` — `judge()`가 gdb가 stdout에 남긴 대상 수명주기 줄(`_GDB_TARGET_LIFECYCLE`)로
  가른다. 안 뜬 대상은 `ok=False`, `exit_code`는 gdb 프로세스의 값 그대로. 같은 파일 **33 passed**.
  지어낸 stdout으로 얕게 고정하던 gdb judge 테스트 둘은 삭제(새 파일이 녹음으로 덮는다),
  Dockerfile 핀의 공허한 첫 단언과 `gdb.batch` `run` 무력화 생존(보류 리뷰 4)은 새 파일이 막는다.
- 변이 확인: 앵커판(noeol) 2 · 「exit 1이면 실패」 7 · stderr 음성 표지 5 · 리뷰의 양성 표지
  (SIGKILL 누락) 2 · apt gdb 삭제·`COPY` 삭제·`run` 주석·백트레이스 줄 삭제 각 1이 새 테스트로 죽는다.
- 병합·전체: `origin/main` 병합 뒤 기준선 2285 passed·46 skipped, 수정 뒤 `python -m pytest -q`
  → **2316 passed, 46 skipped**(+33 신규 −2 삭제), `ruff check .` 통과.

## 테스트 명세

| # | 보증하는 것 | 테스트 또는 명령 | 종류 | 결과 |
| --- | --- | --- | --- | --- |
| 1 | Fake gdb가 `CRASH` Evidence를 내고 존재하는 소스에서는 성공한다 | `tests/test_adapters.py::test_fake_gdb_uses_crash_evidence_and_requires_a_source` | 단위 | PASS |
| 2 | Fake gdb가 없는 소스를 성공으로 만들지 않는다 | `tests/test_adapters.py::test_fake_gdb_uses_crash_evidence_and_requires_a_source` | 단위 | PASS |
| 3 | gdb 계획은 디버그 심볼 컴파일 뒤 `--nx -batch --return-child-result -x` 실행을 낸다 | `tests/test_container_tools.py::test_gdb_plan_compiles_then_runs_a_noninteractive_debugger` | 단위 | PASS |
| 4 | C와 C++를 한 gdb 링크에 섞으면 명시적으로 거부한다 | `tests/test_container_tools.py::test_gdb_refuses_mixed_languages_instead_of_linking_half_a_program` | 단위 | PASS |
| 5 | 빌드 단계에서 끊기면 `ok=False`이며 실행으로 세지 않는다 | `tests/test_container_tools.py::test_gdb_build_failure_never_counts_as_a_debug_session` | 단위 | PASS |
| 6 | 대상이 떴으면(정상 종료·자기 종료 코드·신호) `ok=True`이고 종료 코드를 보존한다 | `tests/test_gdb_lifecycle_judgement.py::test_a_target_that_started_is_ok_whatever_exit_code_gdb_returns` | 단위 | PASS |
| 7 | 대상이 한 번도 안 떴으면(실행 권한 없음·파일 없음·ptrace 거부·스크립트 못 읽음) `ok=False`다 | `tests/test_gdb_lifecycle_judgement.py::test_a_target_that_never_started_is_not_a_crash_evidence` | 단위 | PASS |
| 8 | 같은 종료 코드 255가 크래시와 gdb 스크립트 실패로 갈린다 — gdb가 stdout에 무엇을 찍었는지로만 가른다 | `tests/test_gdb_lifecycle_judgement.py::test_exit_255_is_a_crash_or_a_failure_only_by_what_gdb_printed` | 단위 | PASS |
| 9 | v3 Dockerfile이 gdb를 설치하고 기본 배치 스크립트를 빌드 중 실행한다 | `tests/test_container_tools.py::test_the_tool_image_installs_and_smokes_gdb_batch_mode` | 정적 통합 | PASS |
| 10 | 실제 v3 이미지가 capability 없이 정상·SIGSEGV C 표본을 실행한다 | `tests/test_container_tools.py::test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability` | 실컨테이너 통합 | PASS |
| 11 | Fake·컨테이너 양 경로와 저장소 문서 규약에 회귀가 없다 | `python -m pytest -q` | 전체 회귀 | PASS |
| 12 | Dockerfile이 apt 목록에 gdb를, 주석 아닌 줄에 `COPY gdb.batch`를 둔다(공허한 문자열 단언 대체) | `tests/test_gdb_lifecycle_judgement.py::test_dockerfile_installs_gdb_in_the_apt_list_and_bakes_the_script` | 정적 통합 | PASS |
| 13 | `gdb.batch`가 `run` 뒤에 백트레이스를 낸다(스크립트 내용 핀) | `tests/test_gdb_lifecycle_judgement.py::test_gdb_batch_script_runs_the_target_and_then_prints_backtraces` | 정적 통합 | PASS |

## 커버리지와 남은 공백

`python -m pytest --cov=src --cov-report=term-missing -q` 결과는 **전체 95%**였다.
변경 핵심인 `src/adapters/container_tools.py`는 92%, `src/adapters/fake_tools.py`는 97%다.
80% 기준을 넘는다.

- 전체 실행의 2 skip은 Windows에서 심볼릭 링크 생성이 불가능할 때 건너뛰는 기존
  `tests/test_model_checksum.py` 두 건이다. gdb 테스트는 skip되지 않았다.
- Podman은 ATOM에서 직접 실측했다. Singularity/Apptainer는 로컬 실행 파일이 없고 Seraph
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
- 2026-09-28 최종: 전체 2064개 통과, Ruff 통과, 전체 커버리지 95%, Docker v3 빌드와 실 gdb
  통합 테스트 통과.
- ⏵ 2026-09-30 HIGH-1 RED: `e7f282f` — gdb 대상 수명주기 판정 33개 중 11개 실패(종료 코드만 보는 head).
- ⏵ 2026-09-30 HIGH-1 GREEN: `d64a2b1` — stdout 수명주기 줄 판정으로 33개 통과. `origin/main`
  병합 뒤 전체 2316개 통과·46 skip, Ruff 통과.

스쿼시 머지를 한다면 위 RED/GREEN/최종 줄들을 PR 본문 또는 스쿼시 커밋 본문에 보존한다.

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

### ⏵ 2026-09-30 — 위 두 축이 안 센 명제 축 (#327 HIGH-1)

위 두 축은 **이미지 태그**와 **도구 열거/컴파일 계약**만 셌고, 「종료 코드로 대상이 떴는지
안다」라는 **명제**를 문형으로 세지 않았다. 그 「0개」는 그래서 반쪽이었다 — 그 명제가 gdb에서
거짓이라는 것이 HIGH-1이고, 그것을 서술한 자리들이 미커버였다.

- **명제 P1**: 「`_RUNTIME_TOOLS` 판정에서 마지막 종료 코드가 125·126·127이 아니면 대상이
  떴다」가 **gdb에도** 참이라고 서술한다. gdb에서 거짓(안 뜬 대상도 exit 1·255, `return 127;`이면 127).
- **명제 P2**: 「gdb 경로의 `Evidence.exit_code`는 대상의 **원** 종료 코드다」. 거짓 — 신호로
  멈춘 대상에는 원 종료 코드가 없고 gdb 프로세스의 값이 남는다.

문형: (i) 직접 단언(「여기까지 오면 떴다」·「뜨기만 하면」), (ii) 열거형(`_EXEC_FAILED`·
125·126·127), (iii) 도구 묶음(`asan·gdb·poc`·`_RUNTIME_TOOLS`), (iv) 결과 서술(「네 갈래」·
「기동 실패」·「실행류 공통 규약」·「실행까지 성공」), (v) P2형(「원 종료 코드」·「종료 코드를 보존/그대로 남긴다」).

- 명제 축: 위 문형의 정규식이 gdb·exit·종료 코드 문맥에서 낸 후보 **46줄**을 손으로 분류했다 —
  gdb 갈래를 갈라 적어 고친 곳, `asan`·`poc`/clang-tidy/semgrep로 이미 한정된 곳(참), 날짜 붙은
  과거 스냅샷(D92 §결정 직전 계약·D80 ②)으로 갈렸고, **gdb에 P1·P2를 그대로 단언한 미정정 자리는
  0**이다. 손 훑기가 검출기 밖에서 더 찾은 것도 **0(델타 0)**. 정정한 곳: 코드는
  `container_tools.py`의 모듈 docstring·`_EXEC_FAILED`·`_RUNTIME_TOOLS` 주석·`judge()` 갈래와
  docstring, 문서는 `architecture.md`·`STATUS.md`·`D80`·`D92`·이 파일·`tools/README.md`·
  `tests/test_adapters.py` docstring이다.
