# ToolRunner gdb 배선 — TDD 증거

작성일: 2026-09-28

## 원천과 사용자 여정

별도 `*.plan.md`는 없었다. 사용자 요청의 앵커와 수용 조건에서 다음 여정을 뽑았다.

1. 하네스 개발자는 Fake와 컨테이너 ToolRunner에 `gdb`를 요청해 같은 `CRASH` 종류의
   Evidence를 받아야 한다.
2. 판정 코드는 빌드 실패, 정상 실행, 대상 크래시(신호), 대상 기동 실패(gdb·컨테이너 포함)를
   구분하고, `Evidence.exit_code`에는 gdb 프로세스의 종료 코드를 그대로 남긴다 — 대상이
   스스로 끝났으면 그 종료 코드와 같고, 신호로 멈춘 뒤 gdb가 죽이면 x86-64 Docker(gdb
   12.1·15.1)에서 255, ATOM Podman(aarch64, gdb 버전 미확인) 기록은 1이다(원인 미실측). 대상이 실제로
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

### 실컨테이너·전체 회귀 (2026-09-28)

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

### ⏵ 2026-10-01 — 독립 검토 R1 수정 (스레드 형태의 신호 정지)

독립 검토 R1: 스레드를 한 번이라도 만든 대상이 신호로 멈추면 gdb가 `Program received signal`이
아니라 `Thread N "이름" received signal`을 찍는다(gdb 12.1·15.1 같음). 위 HIGH-1 수정의 정규식이
그 형태를 몰라 진짜 크래시를 「띄우지 못했다」로 읽었다 — 수정이 만든 회귀다.

- 재녹음: 실제 gdb 12.1(v3 컨테이너)·15.1(WSL)로 30개 시나리오를 돌렸다 — 스레드 크래시·abort·
  trap·SIGTERM·SIGKILL·SIGALRM 기본 동작·스레드 안 `exit`·정상 종료, 스레드 이름에 공백·따옴표·
  빈 이름·UTF-8·깨진 UTF-8·개행·`received signal`·`[Inferior` 모양, fork·exec, 안 뜬 대상 셋.
  수정 전 정규식은 60줄 중 34줄을 오판했다(전부 스레드 형태의 신호 정지). `terminated with
  signal`은 스레드가 있어도 `Program`이었고, 개행이 든 이름은 개행 앞까지만 찍혔다. 그중 9개를
  `tests/fixtures/gdb/thread_and_stderr_recordings.json`에 원문 그대로 싣는다.
- RED: `5cbc4d9` — 새 파일 53개 중 스레드 신호 정지 12개가 빨갛다(**12 failed, 41 passed**).
- GREEN: `4c95568` — 정규식에 `Thread N "…" received signal` 갈래를 더했다. 이름 자리는 64자로
  묶었다: 검토가 제안한 묶지 않은 게으른형은 대상이 한 줄에 `Thread 1 "`을 20만 자 찍으면 20.5초,
  탐욕형은 20만 자 4.6초·40만 자 19.3초·80만 자 84.3초(제곱 시간)였고, 64자로 묶은 형은 80만 자
  0.023초다. 새 파일 **53 passed**.
- 교차 확인: 저장된 실제 gdb stdout 172건(시나리오 76종 — 이 재녹음과 검토의 녹음, 15.1은 WSL과
  `uaf-note-verify` 이미지 두 환경)에서 뜬 대상을 거절한 수가 수정 전 40 → 수정 뒤 0이다. 안 뜬
  대상을 받아들인 것은 검토의 가정 실측 한 건(실행 파일 경로를 수명주기 줄 모양으로 바꾼 것 —
  `plan()`이 경로를 고정해 도달 불가)뿐이고 두 정규식이 같다.
- 변이: 스레드 갈래 삭제 12 · 이름 자리를 묶지 않음(탐욕형 1·게으른형 1, 선형성 검사) · 이름에 따옴표
  금지 2 · 묶음 `{0,2}` 10 · stdout과 stderr를 합쳐 찾기(검토 J02) 3이 죽는다. 이름 자리 필수화·스레드
  번호를 정수로 한정은 산다 — 녹음에 이름 없는 형태·`1.2` 꼴 번호가 없어 녹음 위에서는 같은 판정이다.

### ⏵ 2026-10-01 — 2차 독립 검토 N1·N2 수정 (스레드 이름의 CR)

2차 검토 N1: gdb는 스레드 이름(comm)을 `fgets`로 읽고 LF만 지운다(gdb 12.1·15.1
`gdb/nat/linux-procfs.c`의 `linux_proc_tid_get_name`) — 이름 안의 CR은 정지 줄에 그대로 찍힌다.
`_execute`의 text 모드 캡처는 CR·CRLF를 LF로 바꾼다(`Lib/subprocess.py`의 `_translate_newlines`,
Windows는 `TextIOWrapper(newline=None)`). 그래서 이름 자리 `[^\n]{0,64}`가 못 넘어 진짜 크래시가
다시 「띄우지 못했다」가 됐다. 다른 구분 문자(탭·VT·FF·FS·GS·RS·U+0085·U+2028·U+2029)는 text 모드가
바꾸지 않아 문제가 없었다(실측).

- 재녹음: 실제 gdb 12.1·15.1로 22개 시나리오 — CR 이름 8종(가운데·끝·CR만·앞·CRLF·CR×15·15바이트
  끝 CR·main 스레드), 구분 문자 하나씩과 합친 것, 15자 이름, `Thread 13`, 11KB·4MB 출력 뒤 크래시,
  컨테이너 메모리 6MB(gdb 자체가 죽는다 — 한 번은 대상이 뜨기 전이라 stdout 0 → `ok=False`, 한 번은
  대상이 정상 종료한 뒤라 수명주기 줄 → `ok=True`이고 `exit_code` 137은 gdb의 값 — 경합이다). 수정
  전 정규식은 CR 8종 × 두 판 16줄을 오판했다. 그중 12개를 JSON에 두 판씩 더 싣는다(녹음 42건).
- RED: `76aec89` — 새 파일 78개 중 **17 failed, 61 passed**(CR 16 + CR 경로 검사 1).
- GREEN: `7232cfa` — 이름 자리 `[\s\S]{0,64}`(줄바꿈 포함, 64자 묶음 그대로). **78 passed**.
- 선형성(이 장비 실측, 별도 프로세스, 1천만 자 기준): 적대 입력 7족 0.02~0.24초(10배 길이에 약 10배
  시간), 줄바꿈이 섞인 적대 입력 0.15초, `judge` 종단 1억 자 1.66~1.86초. 묶지 않은 `[\s\S]*?`는
  20만 자에서 12.9~15.2초였다.
- 교차 확인: 저장된 실제 gdb stdout 434건(시나리오 163종 — 이 녹음들과 두 독립 검토의 녹음)에서 뜬
  대상 거절이 2차 수정 뒤 head 32 → 0이다. 안 뜬 대상 수용 10은 두 정규식이 같고, 전부 검토가 도달
  불가·선행으로 분류한 경우다(경로 위조 가정 실측·로더 실패·이 장비의 binfmt).
- 변이(새 파일): 이름 자리를 `[^\n]`·`[^\r\n]`로 되돌림 각 17 · 스레드 번호 한 자리(`[\d.]`·`\d`) 각 2 ·
  이름 상한 `{0,14}` 6·`{0,7}` 8 · stdout 앞 1000자만 검색 2 · 묶지 않음(탐욕·게으름, 선형성) 각 1 ·
  따옴표 금지 2 · 스레드 갈래 삭제 37 · `{0,2}` 31 · stdout과 stderr 합침 3이 죽는다.
  `(?s:.{0,64})`는 산다 — 같은 뜻이다.
- 옛 변이 재집계(최종 78개 기준): 앵커판 2 · 「exit 1이면 실패」 8 · stderr 음성 표지 6 · 리뷰의 양성
  표지 세 개 목록 41(`terminated with`도 스레드 형태도 없다 — `terminated with`만 뺀 형은 4) ·
  Dockerfile·`gdb.batch` 변이 각 1.

## 테스트 명세

| # | 보증하는 것 | 테스트 또는 명령 | 종류 | 결과 |
| --- | --- | --- | --- | --- |
| 1 | Fake gdb가 `CRASH` Evidence를 내고 존재하는 소스에서는 성공한다 | `tests/test_adapters.py::test_fake_gdb_uses_crash_evidence_and_requires_a_source` | 단위 | PASS |
| 2 | Fake gdb가 없는 소스를 성공으로 만들지 않는다 | `tests/test_adapters.py::test_fake_gdb_uses_crash_evidence_and_requires_a_source` | 단위 | PASS |
| 3 | gdb 계획은 디버그 심볼 컴파일 뒤 `--nx -batch --return-child-result -x` 실행을 낸다 | `tests/test_container_tools.py::test_gdb_plan_compiles_then_runs_a_noninteractive_debugger` | 단위 | PASS |
| 4 | C와 C++를 한 gdb 링크에 섞으면 명시적으로 거부한다 | `tests/test_container_tools.py::test_gdb_refuses_mixed_languages_instead_of_linking_half_a_program` | 단위 | PASS |
| 5 | 빌드 단계에서 끊기면 `ok=False`이며 실행으로 세지 않는다 | `tests/test_container_tools.py::test_gdb_build_failure_never_counts_as_a_debug_session` | 단위 | PASS |
| 6 | 대상이 떴으면(정상 종료·자기 종료 코드·신호) `ok=True`이고 gdb의 종료 코드를 그대로 남긴다 | `tests/test_gdb_lifecycle_judgement.py::test_a_target_that_started_is_ok_whatever_exit_code_gdb_returns` | 단위 | PASS |
| 7 | 대상이 한 번도 안 떴으면(실행 권한 없음·파일 없음·ptrace 거부·스크립트 못 읽음) `ok=False`다 | `tests/test_gdb_lifecycle_judgement.py::test_a_target_that_never_started_is_not_a_crash_evidence` | 단위 | PASS |
| 8 | 같은 종료 코드 255가 크래시와 gdb 스크립트 실패로 갈린다 — gdb가 stdout에 무엇을 찍었는지로만 가른다 | `tests/test_gdb_lifecycle_judgement.py::test_exit_255_is_a_crash_or_a_failure_only_by_what_gdb_printed` | 단위 | PASS |
| 9 | v3 Dockerfile이 gdb를 설치하고 기본 배치 스크립트를 빌드 중 실행한다 | `tests/test_container_tools.py::test_the_tool_image_installs_and_smokes_gdb_batch_mode` | 정적 통합 | PASS |
| 10 | 실제 v3 이미지가 capability 없이 정상·SIGSEGV C 표본을 실행한다 | `tests/test_container_tools.py::test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability` | 실컨테이너 통합 | PASS |
| 11 | Fake·컨테이너 양 경로와 저장소 문서 규약에 회귀가 없다 | `python -m pytest -q` | 전체 회귀 | PASS |
| 12 | Dockerfile이 apt 목록에 gdb를, 주석 아닌 줄에 `COPY gdb.batch`를 둔다(공허한 문자열 단언 대체) | `tests/test_gdb_lifecycle_judgement.py::test_dockerfile_installs_gdb_in_the_apt_list_and_bakes_the_script` | 정적 통합 | PASS |
| 13 | `gdb.batch`가 `run` 뒤에 백트레이스를 낸다(스크립트 내용 핀) | `tests/test_gdb_lifecycle_judgement.py::test_gdb_batch_script_runs_the_target_and_then_prints_backtraces` | 정적 통합 | PASS |
| 14 | 스레드를 만든 대상의 신호 정지(`Thread N "이름" received signal`)도 떴다고 읽는다 — 따옴표·빈 이름·깨진 UTF-8·CR·다른 구분 문자가 든 이름, 15자 이름, 두 자리 번호, 긴 출력 뒤 정지 포함 | `tests/test_gdb_lifecycle_judgement.py::test_a_threaded_target_stopped_by_a_signal_is_reported_per_thread` | 단위 | PASS |
| 15 | 판정은 stdout만 본다 — 안 뜬 대상의 경로 에코로 stderr에 든 수명주기 줄 모양 문자열은 세지 않는다 | `tests/test_gdb_lifecycle_judgement.py::test_only_stdout_counts_even_when_stderr_carries_a_lifecycle_line` | 단위 | PASS |
| 16 | 대상이 찍은 적대적 긴 줄에서도 판정이 선형 시간이다(합성 입력) | `tests/test_gdb_lifecycle_judgement.py::test_the_lifecycle_search_stays_linear_on_a_hostile_stdout` | 단위 | PASS |
| 17 | 이름 안의 CR을 text 모드 캡처가 LF로 바꿔도 떴다고 읽는다 | `tests/test_gdb_lifecycle_judgement.py::test_a_carriage_return_in_a_thread_name_reaches_judge_as_a_newline` | 단위 | PASS |

## 커버리지와 남은 공백

2026-09-28 시점 `python -m pytest --cov=src --cov-report=term-missing -q` 결과는 **전체 95%**였다.
변경 핵심인 `src/adapters/container_tools.py`는 92%, `src/adapters/fake_tools.py`는 97%다.
80% 기준을 넘는다. (그 뒤 커버리지는 다시 재지 않았다.)

- 2026-09-28 전체 실행의 2 skip은 Windows에서 심볼릭 링크 생성이 불가능할 때 건너뛰는 기존
  `tests/test_model_checksum.py` 두 건이다. ⏵ `origin/main` 병합 뒤 전체 실행의 skip은 46이다 —
  `tests/test_serve_scripts.py` 40·`tests/test_run_signals.py` 4·`tests/test_model_checksum.py` 2
  (2026-10-01 `pytest -rs`로 확인). 어느 경우에도 gdb 테스트는 skip되지 않았다.
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
- 2026-09-28 최종: 전체 2064개 통과, Ruff 통과, 전체 커버리지 95%, Docker v3 빌드와 실 gdb
  통합 테스트 통과.
- ⏵ 2026-09-30 HIGH-1 RED: `e7f282f` — gdb 대상 수명주기 판정 33개 중 11개 실패(종료 코드만 보는 head).
- ⏵ 2026-09-30 HIGH-1 GREEN: `d64a2b1` — stdout 수명주기 줄 판정으로 33개 통과. `origin/main`
  병합 뒤 전체 2316개 통과·46 skip, Ruff 통과.
- ⏵ 2026-10-01 R1 RED: `5cbc4d9` — 스레드 신호 정지 녹음 12개가 실패(새 파일 53개 중).
- ⏵ 2026-10-01 R1 GREEN: `4c95568` — 스레드 갈래(이름 자리 64자로 묶음)로 새 파일 53개 통과. 전체
  2336개 통과·46 skip, Ruff 통과.
- ⏵ 2026-10-01 N1 RED: `76aec89` — 스레드 이름 CR 녹음 16개와 CR 경로 검사 1개가 실패(새 파일 78개 중).
- ⏵ 2026-10-01 N1 GREEN: `7232cfa` — 이름 자리가 줄바꿈도 받게(64자 묶음 그대로) 새 파일 78개 통과.
  전체 2361개 통과·46 skip, Ruff 통과.

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

### ⏵ 2026-10-01 — 위 「미정정 0 · 델타 0」은 사실이 아니었다 (독립 검토 R4)

위 명제 축의 검출기는 **줄 단위·gdb 문맥**만 봤다. 독립 검토가 검출기를 넓혀(줄바꿈을 무시한 창·
gdb 이름 없는 도구 묶음·영어·열거 문구) 다시 세자 미정정이 남아 있었다 — P1형 3곳(`D80.md` ②표
컨테이너 행 2열이 판정 기준을 종료 코드로만 적은 것, `D92.md` 실행 판정 (a) 제목이 판정 방법까지
ASAN과 같다고 읽히던 것, `container_tools.py`의 `_EXEC_FAILED` 주석이 그 상수를 쓰는 도구를
asan·poc로만 적은 것 — clang-tidy도 쓴다)과, 새 테스트 파일에서 수정 전을 현재형으로 말하던
docstring 8곳이다. 이번 재전수에서 둘을 더 고쳤다: `D92.md` 실행 판정 (a) 본문이 빌드 실패까지
수명주기 줄로 가른다고 적은 것(빌드 실패는 컴파일 단계 끊김으로 가른다 — 원문 대조로 찾았다)과, 이번
R3 정정이 새로 만든 P1형 괄호(docker 실패 검사 docstring이 「못 띄움」을 종료 코드 둘과 짝지은 꼴 —
손 훑기로 찾았다).

- 검출기(검토의 A 줄 단위 · B 줄바꿈 무시 ±450자 창 · C gdb 이름 없는 문형 · D 영어, 그대로)를 이
  절까지 쓴 트리에 돌린 후보 **220줄**(수명주기 언급 표지 103 · asan·poc 한정 14 · 날짜 스냅샷 29 ·
  과거 기록 9 · 표지 없음 65). 과거 기록(`docs/experiments/`·`docs/research/`)을 뺀 211줄을 손으로
  전부 분류했다 — gdb에 P1·P2를 그대로 단언한 미정정은 **0**이다(고친 곳 · asan·poc/clang-tidy/정적
  도구로 한정된 곳 · 날짜 붙은 스냅샷 · argv 문자열 · 명제를 설명하는 메타 문장).
- 손 훑기(gdb·디버거를 말하면서 종료 코드·판정·기동·보존 어휘가 든 줄 가운데 검출기가 안 낸 것)
  마지막 실행에서 **28줄**을 읽었다 — 그 전 실행에서 미정정 1곳(위 docker 실패 검사 docstring, 델타 1)이
  나와 고쳤고, 마지막 실행에서는 0이다(델타 0).
- 한계: 검출기와 손 훑기 모두 문형을 적어 넣은 그물이다 — 목록 밖 문형의 서술은 이 수에 안 든다.
