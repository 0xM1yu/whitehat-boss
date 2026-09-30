"""gdb 판정 — 실제로 녹음한 gdb 출력으로 「대상이 실제로 떴는가」를 묻는다 (PR #327 리뷰 HIGH-1).

**무엇을 고정하나.** gdb 경로에서 대상이 실제로 떴는지는 **종료 코드로 가릴 수
없다.** `asan`·`poc`는 마지막 명령이 **대상 바이너리**라 종료 코드 125·126·127이
곧 「대상을 못 띄움」이지만, gdb는 마지막 명령이 **gdb 자신**이다 — 대상이 한 번도
안 뜬 경우(실행 권한 없음 · 파일 없음 · ptrace 거부 · gdb가 스크립트를 못 읽음)에도
gdb는 정상적으로 떠서 종료 코드 1이나 255로 끝나고, 반대로 대상이 **자기 종료
코드**로 125~127을 돌려주면(`return 127;`) `--return-child-result`가 그 값을 gdb의
종료 코드에 실어 두 숫자 공간이 섞인다. 그래서 대상이 떴는지는 gdb가 **stdout**에
남긴 대상 수명주기 줄(`[Inferior … exited …]` · `Program … signal …`)로만 가른다.
이 파일은 그 판정을 녹음된 출력으로 고정한다 — 안 뜬 대상은 `ok=False`, 뜬 대상은
종료 코드가 무엇이든 `ok=True`다.

**이 파일의 문자열은 전부 녹음이다.** 지어낸 stdout이 아니다. 출처와 재현 방법은
`RECORDED_*` 머리에 적었다.

**한계.** 이 검사는 gdb 12.1(v3 이미지)과 15.1의 **출력 형태**를 고정한다. 다른 gdb
버전이나 로케일에서 같은 문구가 나온다는 보증은 아니다 — 이미지가 gdb 버전을
고정하고(ubuntu:22.04) 로케일 카탈로그가 없다는 것(이 이미지의 `dpkg -L gdb`에
`/locale/`이 0건)이 근거이고, 이미지를 올리면 이 녹음을 다시 뜬다.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.adapters import container_tools
from src.adapters.container_tools import ContainerToolRunner, Step, judge
from src.ids import IdFactory
from src.interfaces.tool_runner import SandboxSpec, ToolArgs

REPO_ROOT = Path(__file__).resolve().parent.parent
GDB_TARGET = Path(__file__).resolve().parent / "fixtures" / "gdb"
RUN_ID = "run_20260930_0327"
SANDBOX = SandboxSpec(
    image=container_tools.DEFAULT_IMAGE,
    network=False,
    read_only=True,
    cpu_limit=1.0,
    memory_mb=2048,
    timeout_s=300,
)


@dataclass(frozen=True)
class Recorded:
    """gdb 명령 하나(`plan("gdb", …)`의 둘째 명령)를 실제로 돌려 얻은 결과."""

    exit_code: int
    stdout: str
    stderr: str


# --- 녹음 -------------------------------------------------------------------------------
#
# 2026-09-30 이 저장소 밖 임시 디렉터리에서 돌린 실제 출력이다. PID·주소·LWP 번호도 녹음 그대로다.
#
# GDB_12 — 이 PR의 이미지 그대로:
#   `harness-tools:v3` (sha256:98b440ab23ba…, Ubuntu 22.04 · GNU gdb 12.1-0ubuntu1~22.04.2 ·
#   clang 14.0.0), Docker Desktop 29.5.3 · Linux/x86_64. /opt/gdb.batch는 저장소의
#   `tools/harness-tools/gdb.batch`와 내용이 같았다(diff 무차이).
#   컴파일 = `plan("gdb", …)[0]`, gdb = `plan("gdb", …)[1]` =
#   `gdb --nx -batch --return-child-result -x /opt/gdb.batch -- /work/gdb.bin`.
#   docker argv는 `container_argv("docker", …)`가 낸 그대로다(`--read-only --network=none …`).
#
# GDB_15 — WSL2 Ubuntu 24.04 · GNU gdb (Ubuntu 15.1-1ubuntu1~24.04.1) 15.1.
#   GDB_12를 만든 컨테이너에서 나온 같은 바이너리를 같은 gdb argv로 돌렸다
#   (경로만 /tmp/rv327). 컨테이너처럼 `env -u SHELL`로 기동해 대상은 /bin/sh를 거친다.
#   ptrace-denied는 이 장비에서 못 만들어 GDB_15에 없다.
#   소스가 /src에 없어 `warning: … No such file or directory`가 stderr에 더 붙는다.
#
# 재현: 조사 스크립트는 저장소에 없다 — 위 argv와 아래 각 항목의 조작(chmod -x · 삭제 ·
#   seccomp · 없는 -x 파일)만 다시 하면 된다.
GDB_12: dict[str, Recorded] = {
    # tests/fixtures/gdb/normal.c — `return 0;`
    "normal": Recorded(
        0,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 24) exited normally]\n'
        ),
        "",
    ),
    # tests/fixtures/gdb/crash.c — `*(volatile int *)0 = 1;` (SIGSEGV)
    "crash": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '\n'
            'Program received signal SIGSEGV, Segmentation fault.\n'
            '0x00005b0930f1313d in main () at /src/crash.c:2\n'
            '2\t    *(volatile int *)0 = 1;\n'
            '\n'
            'Thread 1 (Thread 0x7b27f09ac740 (LWP 24) "gdb.bin"):\n'
            '#0  0x00005b0930f1313d in main () at /src/crash.c:2\n'
            'No locals.\n'
        ),
        "",
    ),
    # `int main(void) { return 1; }`
    "ret1": Recorded(
        1,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 24) exited with code 01]\n'
        ),
        "",
    ),
    # `int main(void) { return 42; }`
    "ret42": Recorded(
        42,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 24) exited with code 052]\n'
        ),
        "",
    ),
    # `int main(void) { return 127; }` — 대상이 자기 종료 코드로 127을 돌려준다
    "ret127": Recorded(
        127,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 24) exited with code 0177]\n'
        ),
        "",
    ),
    # `abort();` (SIGABRT)
    "abort": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '\n'
            'Program received signal SIGABRT, Aborted.\n'
            '0x0000726f9aee19bc in pthread_kill () from /lib/x86_64-linux-gnu'
            '/libc.so.6\n'
            '\n'
            'Thread 1 (Thread 0x726f9ae48740 (LWP 24) "gdb.bin"):\n'
            '#0  0x0000726f9aee19bc in pthread_kill () from /lib/x86_64-linux'
            '-gnu/libc.so.6\n'
            'No symbol table info available.\n'
            '#1  0x0000726f9ae8d476 in raise () from /lib/x86_64-linux-gnu/li'
            'bc.so.6\n'
            'No symbol table info available.\n'
            '#2  0x0000726f9ae737f3 in abort () from /lib/x86_64-linux-gnu/li'
            'bc.so.6\n'
            'No symbol table info available.\n'
            '#3  0x00005c2437764154 in main () at /src/abort.c:2\n'
            'No locals.\n'
        ),
        "",
    ),
    # `kill(getpid(), SIGKILL);` — gdb가 가로채지 못하는 신호(「terminated」)
    "sigkill": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '\n'
            'Program terminated with signal SIGKILL, Killed.\n'
            'The program no longer exists.\n'
        ),
        "",
    ),
    # `fputs("done", stdout); return 0;` — 개행 없이 쓰고 끝난다. gdb의 수명주기 줄이 줄 머리가 아닌
    # 자리(`done[Inferior …`)에 붙는다
    "noeol": Recorded(
        0,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            'done[Inferior 1 (process 24) exited normally]\n'
        ),
        "",
    ),
    # `fputs("partial", stdout); fflush(stdout);` 뒤 SIGSEGV
    "noeol-crash": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            'partial\n'
            'Program received signal SIGSEGV, Segmentation fault.\n'
            '0x00005e5363ee8186 in main () at /src/noeol-crash.c:2\n'
            '2\tint main(void) { fputs("partial", stdout); fflush(stdout); *(v'
            'olatile int *)0 = 1; return 0; }\n'
            '\n'
            'Thread 1 (Thread 0x719ffa71e740 (LWP 24) "gdb.bin"):\n'
            '#0  0x00005e5363ee8186 in main () at /src/noeol-crash.c:2\n'
            'No locals.\n'
        ),
        "",
    ),
    # 컴파일 산출물 /work/gdb.bin에 `chmod -x`(별도 컨테이너) — 대상이 안 뜬다
    "noexec": Recorded(
        1,
        "",
        (
            '/bin/sh: 1: exec: /work/gdb.bin: Permission denied\n'
            '/opt/gdb.batch:10: Error in sourced command file:\n'
            'During startup program exited with code 126.\n'
        ),
    ),
    # 컴파일 산출물 /work/gdb.bin을 지웠다 — 대상이 없다
    "missing": Recorded(
        1,
        "",
        (
            '/work/gdb.bin: No such file or directory.\n'
            '/opt/gdb.batch:10: Error in sourced command file:\n'
            'No executable file specified.\n'
            'Use the "file" or "exec-file" command.\n'
        ),
    ),
    # Docker 기본 seccomp 위에 ptrace만 EPERM으로 막았다(`--security-opt seccomp=…`) — Singularity는
    # 아니다(그 런타임은 이 장비에 없다)
    "ptrace-denied": Recorded(
        1,
        "",
        (
            'warning: Could not trace the inferior process.\n'
            'warning: ptrace: Operation not permitted\n'
            '/opt/gdb.batch:10: Error in sourced command file:\n'
            'During startup program exited with code 127.\n'
        ),
    ),
    # gdb의 `-x`를 없는 파일(/opt/none.batch)로 — gdb가 스크립트를 못 읽는다
    "gdb-script-missing": Recorded(
        255,
        "",
        'warning: /opt/none.batch: No such file or directory\n',
    ),
}

GDB_15: dict[str, Recorded] = {
    # tests/fixtures/gdb/normal.c — `return 0;`
    "normal": Recorded(
        0,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 59) exited normally]\n'
        ),
        "",
    ),
    # tests/fixtures/gdb/crash.c — `*(volatile int *)0 = 1;` (SIGSEGV)
    "crash": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '\n'
            'Program received signal SIGSEGV, Segmentation fault.\n'
            '0x00005a3eb07fd13d in main () at /src/crash.c:2\n'
            '\n'
            'Thread 1 (Thread 0x75a60ccd7740 (LWP 73) "crash.bin"):\n'
            '#0  0x00005a3eb07fd13d in main () at /src/crash.c:2\n'
            'No locals.\n'
        ),
        'warning: 2\t/src/crash.c: No such file or directory\n',
    ),
    # `int main(void) { return 1; }`
    "ret1": Recorded(
        1,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 87) exited with code 01]\n'
        ),
        "",
    ),
    # `int main(void) { return 42; }`
    "ret42": Recorded(
        42,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 101) exited with code 052]\n'
        ),
        "",
    ),
    # `int main(void) { return 127; }` — 대상이 자기 종료 코드로 127을 돌려준다
    "ret127": Recorded(
        127,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '[Inferior 1 (process 115) exited with code 0177]\n'
        ),
        "",
    ),
    # `abort();` (SIGABRT)
    "abort": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '\n'
            'Program received signal SIGABRT, Aborted.\n'
            '__pthread_kill_implementation (no_tid=0, signo=6, threadid=<opti'
            'mized out>) at ./nptl/pthread_kill.c:44\n'
            '\n'
            'Thread 1 (Thread 0x7fc0c2fe7740 (LWP 129) "abort.bin"):\n'
            '#0  __pthread_kill_implementation (no_tid=0, signo=6, threadid=<'
            'optimized out>) at ./nptl/pthread_kill.c:44\n'
            '        tid = <optimized out>\n'
            '        ret = 0\n'
            '        pd = <optimized out>\n'
            '        old_mask = {__val = {1}}\n'
            '        ret = <optimized out>\n'
            '        pd = <optimized out>\n'
            '        old_mask = <optimized out>\n'
            '        ret = <optimized out>\n'
            '        tid = <optimized out>\n'
            '        ret = <optimized out>\n'
            '        resultvar = <optimized out>\n'
            '        resultvar = <optimized out>\n'
            '        __arg3 = <optimized out>\n'
            '        __arg2 = <optimized out>\n'
            '        __arg1 = <optimized out>\n'
            '        _a3 = <optimized out>\n'
            '        _a2 = <optimized out>\n'
            '        _a1 = <optimized out>\n'
            '        __futex = <optimized out>\n'
            '        resultvar = <optimized out>\n'
            '        __arg3 = <optimized out>\n'
            '        __arg2 = <optimized out>\n'
            '        __arg1 = <optimized out>\n'
            '        _a3 = <optimized out>\n'
            '        _a2 = <optimized out>\n'
            '        _a1 = <optimized out>\n'
            '        __futex = <optimized out>\n'
            '        __private = <optimized out>\n'
            '        __oldval = <optimized out>\n'
            '#1  __pthread_kill_internal (signo=6, threadid=<optimized out>) '
            'at ./nptl/pthread_kill.c:78\n'
            'No locals.\n'
            '#2  __GI___pthread_kill (threadid=<optimized out>, signo=signo@e'
            'ntry=6) at ./nptl/pthread_kill.c:89\n'
            'No locals.\n'
            '#3  0x00007fc0c2c4527e in __GI_raise (sig=sig@entry=6) at ../sys'
            'deps/posix/raise.c:26\n'
            '        ret = <optimized out>\n'
            '#4  0x00007fc0c2c288ff in __GI_abort () at ./stdlib/abort.c:79\n'
            '        save_stage = 1\n'
            '        act = {__sigaction_handler = {sa_handler = 0x20, sa_siga'
            'ction = 0x20}, sa_mask = {__val = {12, 18446744073709551615, 64,'
            ' 12, 5505024, 2048, 5505024, 49152, 22020096, 22020096, 3145728,'
            ' 49152, 140721967284632, 876173328390, 0, 0}}, sa_flags = 0, sa_'
            'restorer = 0x0}\n'
            '#5  0x00005e9a82318154 in main () at /src/abort.c:2\n'
            'No locals.\n'
        ),
        'warning: 44\t./nptl/pthread_kill.c: No such file or directory\n',
    ),
    # `kill(getpid(), SIGKILL);` — gdb가 가로채지 못하는 신호(「terminated」)
    "sigkill": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            '\n'
            'Program terminated with signal SIGKILL, Killed.\n'
            'The program no longer exists.\n'
        ),
        "",
    ),
    # `fputs("done", stdout); return 0;` — 개행 없이 쓰고 끝난다. gdb의 수명주기 줄이 줄 머리가 아닌
    # 자리(`done[Inferior …`)에 붙는다
    "noeol": Recorded(
        0,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            'done[Inferior 1 (process 157) exited normally]\n'
        ),
        "",
    ),
    # `fputs("partial", stdout); fflush(stdout);` 뒤 SIGSEGV
    "noeol-crash": Recorded(
        255,
        (
            '[Thread debugging using libthread_db enabled]\n'
            'Using host libthread_db library "/lib/x86_64-linux-gnu/libthread'
            '_db.so.1".\n'
            'partial\n'
            'Program received signal SIGSEGV, Segmentation fault.\n'
            '0x000058db85245186 in main () at /src/noeol-crash.c:2\n'
            '\n'
            'Thread 1 (Thread 0x7a8e6ec65740 (LWP 171) "noeol-crash.bin"):\n'
            '#0  0x000058db85245186 in main () at /src/noeol-crash.c:2\n'
            'No locals.\n'
        ),
        'warning: 2\t/src/noeol-crash.c: No such file or directory\n',
    ),
    # 컴파일 산출물 /work/gdb.bin에 `chmod -x`(별도 컨테이너) — 대상이 안 뜬다
    "noexec": Recorded(
        1,
        "",
        (
            '/bin/sh: 1: exec: /tmp/rv327/noexec.bin: Permission denied\n'
            '/tmp/rv327/gdb.batch:10: Error in sourced command file:\n'
            'During startup program exited with code 126.\n'
        ),
    ),
    # 컴파일 산출물 /work/gdb.bin을 지웠다 — 대상이 없다
    "missing": Recorded(
        1,
        "",
        (
            '/tmp/rv327/missing.bin: No such file or directory.\n'
            '/tmp/rv327/gdb.batch:10: Error in sourced command file:\n'
            'No executable file specified.\n'
            'Use the "file" or "exec-file" command.\n'
        ),
    ),
    # gdb의 `-x`를 없는 파일(/opt/none.batch)로 — gdb가 스크립트를 못 읽는다
    "gdb-script-missing": Recorded(
        255,
        "",
        'warning: /tmp/rv327/none.batch: No such file or directory\n',
    ),
}

RECORDED: dict[str, dict[str, Recorded]] = {"12.1": GDB_12, "15.1": GDB_15}

# 컨테이너·gdb 자체를 못 띄운 실제 docker 출력(GDB_12와 같은 docker 29.5.3). stdout은 전부 비었다.
RECORDED_DOCKER_FAILURES: dict[str, Recorded] = {
    # 같은 이름의 컨테이너가 이미 있다 — 병렬 pytest가 실제로 밟는 것
    "name-conflict": Recorded(
        125,
        "",
        (
            'docker: Error response from daemon: Conflict. The container name'
            ' "/rv327-conflict" is already in use by container "4339f7cdb16e8'
            'aa5e60c93cbb02a31bcfbed239b6db62aaacc1fef4e0a636486". You have t'
            'o remove (or rename) that container to be able to reuse that nam'
            'e.\n'
            '\n'
            "Run 'docker run --help' for more information\n"
        ),
    ),
    # gdb 자리에 이미지에 없는 실행 파일 이름
    "no-such-binary": Recorded(
        127,
        "",
        (
            'docker: Error response from daemon: failed to create task for co'
            'ntainer: failed to create shim task: OCI runtime create failed: '
            'runc create failed: unable to start container process: error dur'
            'ing container init: exec: "no-such-gdb": executable file not fou'
            'nd in $PATH\n'
            '\n'
            "Run 'docker run --help' for more information\n"
        ),
    ),
    # 이미지가 없다(`--pull=never` — 네트워크를 쓰지 않는다)
    "no-such-image": Recorded(
        125,
        "",
        (
            'docker: Error response from daemon: No such image: harness-tools'
            ':v0-nonexistent\n'
            '\n'
            "Run 'docker run --help' for more information\n"
        ),
    ),
}


# 컴파일 단계(`plan("gdb", …)`의 첫 명령)는 어느 경우에도 성공했다 — 대상이 뜨느냐는 그 다음이다.
_BUILD_OK = Step(0, "", "")

STARTED = ("normal", "crash", "ret1", "ret42", "ret127", "abort", "sigkill", "noeol", "noeol-crash")
NEVER_STARTED = ("noexec", "missing", "ptrace-denied", "gdb-script-missing")


def _cases(names: tuple[str, ...]) -> list:
    return [
        pytest.param(version, name, id=f"gdb{version}-{name}")
        for version, recorded in RECORDED.items()
        for name in names
        if name in recorded
    ]


def _judge_recorded(record: Recorded) -> tuple[bool, int, str | None]:
    return judge("gdb", [_BUILD_OK, Step(record.exit_code, record.stdout, record.stderr)], 2)


@pytest.mark.parametrize(("version", "name"), _cases(STARTED))
def test_a_target_that_started_is_ok_whatever_exit_code_gdb_returns(
    version: str, name: str
) -> None:
    """대상이 떴으면(정상 종료·자기 종료 코드·신호로 죽음) `ok=True`, 종료 코드는 그대로다.

    `ret127`은 현 판정을 빨갛게 만드는 한 건이다 — 대상이 `return 127;`로 끝났을 뿐
    멀쩡히 떴는데 종료 코드가 `_EXEC_FAILED`에 든다는 이유로 「실행 파일을 띄우지
    못했다」가 된다(현 판정은 종료 코드만 본다).

    `noeol`은 **고친 판정**을 겨눈다 — 대상이 개행 없이 stdout에 쓰면 gdb의 수명주기 줄이
    `done[Inferior 1 (process 24) exited normally]`처럼 줄 **중간**에 붙는다. 줄 머리에
    앵커(`^`)를 건 정규식은 여기서 멀쩡한 실행을 「안 떴다」로 읽는다(고치기 전에는 초록).
    """
    record = RECORDED[version][name]

    assert _judge_recorded(record) == (True, record.exit_code, None)


@pytest.mark.parametrize(("version", "name"), _cases(NEVER_STARTED))
def test_a_target_that_never_started_is_not_a_crash_evidence(version: str, name: str) -> None:
    """대상이 한 번도 안 떴으면 gdb가 exit 1(또는 255)로 끝나도 `ok=False`다.

    `container_tools.py` 모듈 docstring이 막겠다던 「한 번도 돌지 않은 프로그램이 재현
    완료로 기록된다」가 이 네 건에서 실제로 일어난다. 종료 코드는 gdb의 값이므로
    그대로 남긴다(합성하지 않는다).
    """
    record = RECORDED[version][name]

    ok, code, error = _judge_recorded(record)

    assert ok is False
    assert code == record.exit_code
    assert error


def test_exit_255_is_a_crash_or_a_failure_only_by_what_gdb_printed() -> None:
    """**같은 종료 코드 255**가 크래시일 수도, 「gdb가 스크립트를 못 읽음」일 수도 있다.

    `--return-child-result`는 「대상이 자기 힘으로 끝나지 않았다」를 -1(=255)로 낸다.
    SIGSEGV로 멈춘 대상을 gdb가 배치 끝에 죽일 때와 gdb가 아예 대상을 못 돌릴 때가
    같은 숫자다 — 종료 코드로는 못 가르고 gdb가 무엇을 찍었는지로만 가른다.
    """
    crash = RECORDED["12.1"]["crash"]
    unreadable_script = RECORDED["12.1"]["gdb-script-missing"]

    assert crash.exit_code == unreadable_script.exit_code == 255
    assert _judge_recorded(crash)[0] is True
    assert _judge_recorded(unreadable_script)[0] is False


@pytest.mark.parametrize("name", sorted(RECORDED_DOCKER_FAILURES))
def test_a_container_that_could_not_start_gdb_stays_a_failure(name: str) -> None:
    """현 `_EXEC_FAILED`가 잡으려던 갈래(컨테이너·gdb 기동 실패)는 고친 뒤에도 실패다.

    실제 docker 출력이다 — 이름 충돌(병렬 pytest가 실제로 밟는다)·실행 파일 없음·이미지
    없음. 셋 다 stdout이 비어 있다. **이 검사는 고치기 전에도 초록이다** — 고친 판정이
    너무 세게 조여 이 갈래를 놓치지 않는지 지킨다.
    """
    record = RECORDED_DOCKER_FAILURES[name]

    ok, code, error = _judge_recorded(record)

    assert (ok, code) == (False, record.exit_code)
    assert error


class _SequenceSpy:
    """`subprocess.run` 대역 — 호출 순서대로 미리 정한 결과를 돌려준다."""

    def __init__(self, results: list[Recorded]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        record = self.results[len(self.calls)]
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, record.exit_code, record.stdout, record.stderr)


def _run_gdb_through_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, debug_result: Recorded
):
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    spy = _SequenceSpy([Recorded(0, "", ""), debug_result])
    monkeypatch.setattr(container_tools.subprocess, "run", spy)
    runner = ContainerToolRunner(
        ids=IdFactory(RUN_ID),
        run_id=RUN_ID,
        root=GDB_TARGET,
        artifacts_root=tmp_path,
        runtime="docker",
        observed_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    evidence = runner.run("gdb", ToolArgs(argv=["gdb", "normal.c"], workdir="normal.c"), SANDBOX)
    assert len(spy.calls) == 2, "컴파일과 gdb, 두 컨테이너를 띄운다"
    return evidence


def test_the_runner_records_a_never_started_target_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """종단(`ContainerToolRunner.run`): 안 뜬 대상이 `Evidence(ok=True, kind=CRASH)`가 안 된다."""
    record = RECORDED["12.1"]["noexec"]

    evidence = _run_gdb_through_the_runner(tmp_path, monkeypatch, record)

    assert evidence.ok is False
    assert evidence.exit_code == record.exit_code
    assert evidence.error


def test_the_runner_still_records_a_real_crash_as_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """양성 대조 — 위 검사를 통과시키려 크래시까지 거절하는 고침을 여기서 잡는다.

    고치기 전에도 초록이다.
    """
    record = RECORDED["12.1"]["crash"]

    evidence = _run_gdb_through_the_runner(tmp_path, monkeypatch, record)

    assert evidence.ok is True
    assert evidence.exit_code == record.exit_code


# --- 아래 둘은 결함이 아니라 **검사의 구멍**이다 -------------------------------------------
# 고치기 전에도 초록이고, 변이(돌연변이)를 넣어야 빨개진다.


def test_dockerfile_installs_gdb_in_the_apt_list_and_bakes_the_script() -> None:
    """LOW-2: 기존 `assert "gdb" in dockerfile`은 헤더 주석만으로 참이다.

    apt 설치 목록의 `gdb \\` 줄이나 `COPY gdb.batch /opt/gdb.batch`를 지워도 통과했다
    (변이로 확인). **철자를 묻는다** — 설치가 실제로 되는지는 이미지를 빌드해야 안다
    (빌드 시점 연막 검사가 맡는다).
    """
    dockerfile = (REPO_ROOT / "tools" / "harness-tools" / "Dockerfile").read_text(encoding="utf-8")

    install = re.search(
        r"apt-get install(?P<body>.*?)&&\s*rm -rf /var/lib/apt/lists", dockerfile, re.DOTALL
    )
    assert install is not None, "apt-get install 블록을 찾지 못했다"
    assert re.search(r"(?m)^\s+gdb(?:\s|\\|$)", install["body"]), "apt 목록에 gdb가 없다"
    code_lines = [
        line.strip() for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    ]
    assert "COPY gdb.batch /opt/gdb.batch" in code_lines


def test_gdb_batch_script_runs_the_target_and_then_prints_backtraces() -> None:
    """MEDIUM(보류 리뷰 4): `gdb.batch`의 `run`을 주석으로 만들어도 테스트 전부가 초록이었다.

    실컨테이너 테스트는 **이미지에 구워진 사본**을 쓰므로 저장소의 스크립트를 바꿔도
    이미지를 다시 빌드하기 전에는 아무것도 안 바뀐다. **철자를 묻는다** — 스크립트의
    실제 동작(대상이 뜨고 백트레이스가 나온다)은 이미지를 다시 빌드한 뒤
    `test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability`만 본다.
    """
    script = (REPO_ROOT / "tools" / "harness-tools" / "gdb.batch").read_text(encoding="utf-8")

    commands = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "run" in commands
    assert "thread apply all backtrace full" in commands
    assert commands.index("thread apply all backtrace full") > commands.index("run")
