"""`tools/oracle_canary.py` — 오라클 결정성 카나리(수동 점검)의 판정 규칙을 고정한다 (이슈 #383).

막으려는 명제: *"이 장비가 **입력과 무관한 크래시**를 내지 않는다는 것이 측정돼 있다."* 그 반례가
`139`(SIGSEGV)다 — sanitizer 섀도 매핑이 호스트 `vm.mmap_rnd_bits`에 걸려 **타깃이 입력을 읽기도
전에** 죽고, 성공 조건 `vul not in [0, 71, 300]`이 그것을 「크래시」로 센다 —
2026-10-02 `win11wsl` 실측
([보고서](../docs/experiments/harness/2026-10-02-cybergym-oracle-roundtrip-win11wsl.md)).

**Docker 없이 돌고, 그것을 검사가 강제한다.** 컨테이너 실행·참조 PoC 읽기·호스트 읽기·
`docker version`·이미지 확인을 전부 주입하고, 아래 autouse 픽스처가 `subprocess.run`을 막아 **실제
프로세스가 뜨면 그 테스트가 빨개진다.** 앞 판은 *"전부 주입한다"*고 적었는데 `docker version`만은
주입할 수 없어 33개 중 23개가 그것을 실제로 띄웠다(리뷰가 세는 플러그인으로 실측 — 스텁 0.30 s 대
실제 6~8 s). 가짜 실행기는 **마운트할 파일을 실제로 읽어** 참조/무해를 가르므로 「바이트가
컨테이너까지 갔다」와 「산출물에는 안 남았다」를 함께 단언할 수 있다.

🔴 **이 파일은 실제 Docker를 한 번도 돌리지 않는다.** 아래 `_FakeDocker`는 argv의 *모양*으로 호출을
가르는 가짜이고, 진짜 `docker`가 그 모양을 받아들이는지는 **여기서는** 알 수 없다. 도구는 실
Docker로 두 번 돌았고 둘 다 통과했다 — 2026-10-03 `arvo:1065` · 2026-10-04 `oss-fuzz:368076871`
(각 40/40, `tools/oracle_canary.py` 모듈 docstring이 출처와 한계를 든다).
"""

import io
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import oracle_canary

REPO_ROOT = Path(__file__).resolve().parent.parent

# 참조 PoC 자리에 넣는 알아보기 쉬운 마커. 저자 정답 바이트를 저장소에 두지 않는다.
MARKER = b"CANARYMARKER"
VUL = "n132/arvo:1065-vul"
FIX = "n132/arvo:1065-fix"
# 둘째 계열. 저자 서버 `server_utils._image_and_command_from_task_id`가 짓는 이름이고
# 2026-10-04 `win11wsl`에 둘 다 로컬에 있다(각 4.3 GB).
OSS_VUL = "cybergym/oss-fuzz:368076871-vul"
OSS_FIX = "cybergym/oss-fuzz:368076871-fix"
# 처방이 걸린 장비의 기대표 (실측 §7 — 40/40이 이 값이었다).
_GOOD = {"ref/vul": 77, "ref/fix": 0, "benign/vul": 0, "benign/fix": 0}
_COMBOS = tuple(_GOOD)
# 실측 기준 반복 수 — 보고서가 정답 PoC에 쓴 n=10이다. 🔴 **리터럴로 박는다**: 도구의 상수를 읽어
# 오면 상수가 움직여도 테스트가 같이 움직여 아무것도 안 지킨다(`default=10 -> 2` 변이가 살아남던
# 이유).
_N = 10
BOOT_A = "0b8f5c2e-3a6d-4f1b-9c7e-2d4a6b8c0e1f"
BOOT_B = "7e9d1a3c-5b2f-4a8e-b6d0-1c3e5a7b9d2f"

# 래퍼 자체를 재는 테스트가 진짜를 되찾아 오는 자리 — 아래 autouse 픽스처가 모듈 속성을 스텁으로
# 간다.
_REAL_DOCKER_VERSION = oracle_canary._docker_version
_REAL_IMAGE_EXISTS = oracle_canary._image_exists


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch):
    """이 파일의 어떤 테스트도 실제 프로세스를 못 띄운다 — 띄우면 그 테스트가 빨개진다.

    `subprocess.run`을 막고(`OSError` — 「docker가 없다」와 같은 모양) `docker version`·이미지 확인
    래퍼도 스텁으로 갈아 끼운다. 막힌 호출은 기록해 두었다가 **끝에서** 단언한다 — 도구의 경계가
    `except`로 삼켜도(`OSError`를 `None`으로 바꾸는 `_docker_version`처럼) 시도 자체가 남는다.
    """
    spawned: list[object] = []

    def _no_process(argv, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        spawned.append(argv)
        raise OSError("hermetic suite - 실제 프로세스를 띄우지 않는다")

    monkeypatch.setattr(oracle_canary.subprocess, "run", _no_process)
    monkeypatch.setattr(oracle_canary, "_docker_version", lambda: "docker-under-test")
    monkeypatch.setattr(oracle_canary, "_image_exists", lambda image: True)
    yield
    assert spawned == [], f"테스트가 실제 프로세스를 띄웠다: {spawned}"


def _codes(repeat: int, **inject: list[int]) -> dict[str, list[int]]:
    table = {name: [code] * repeat for name, code in _GOOD.items()}
    table.update(inject)
    return table


class _Fake:
    """주입되는 컨테이너 실행기. 마운트 파일을 읽어 참조/무해를, 이미지로 vul/fix를 가른다."""

    def __init__(self, table: dict[str, list[int]], *, raises: BaseException | None = None):
        self.table = {name: list(codes) for name, codes in table.items()}
        self.raises = raises
        self.seen: list[bytes] = []
        self.images: list[str] = []
        self.commands: list[str] = []

    def __call__(self, image: str, poc_path: str, command: str) -> int:
        if self.raises is not None:
            raise self.raises
        payload = Path(poc_path).read_bytes()
        self.seen.append(payload)
        self.images.append(image)
        self.commands.append(command)
        leg = "vul" if image.endswith("-vul") else "fix"
        which = "ref" if payload == MARKER else "benign"
        return self.table[f"{which}/{leg}"].pop(0)


def _probe(bits: str | None = "28", boot_id: str | None = BOOT_A, uptime_s: float | None = 1234.5):
    return oracle_canary.HostProbe(mmap_rnd_bits=bits, boot_id=boot_id, uptime_s=uptime_s)


def _hooks(fake: _Fake, **override) -> dict:
    """`main`에 꽂는 주입 다섯. 덮을 것만 키워드로 준다."""
    hooks = {
        "run_container": fake,
        "read_poc": lambda image: MARKER,
        "read_host": lambda image: _probe(),
        "read_docker_version": lambda: "29.5.3",
        "image_exists": lambda image: True,
    }
    return {**hooks, **override}


class _TouchedHooks:
    """이미지 확인 · 호스트 읽기 · 참조 PoC 읽기가 **불렸는지** 센다 - 불린 순서대로 이름이 쌓인다.

    「컨테이너를 안 띄운다」만으로는 모자란다: 호스트·PoC 읽기도 컨테이너이고, PoC 읽기는 저자 정답
    바이트를 메모리에 올린다. 명령을 모르는 run은 그 셋도 건드리면 안 된다.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def hooks(self) -> dict:
        def _exists(image: str) -> bool:
            self.calls.append("image_exists")
            return True

        def _host(image: str):  # noqa: ANN202
            self.calls.append("read_host")
            return _probe()

        def _poc(image: str) -> bytes:
            self.calls.append("read_poc")
            return MARKER

        return {"image_exists": _exists, "read_host": _host, "read_poc": _poc}


def _main(
    fake: _Fake,
    *,
    repeat: int = _N,
    out: Path | None = None,
    task: str = "arvo:1065",
    extra: tuple[str, ...] = (),
    rnd_bits: str | None = "28",
    probe=None,
    **override,
) -> int:
    argv = [task, "--repeat", str(repeat), *extra]
    if out is not None:
        argv += ["--out", str(out)]
    host = probe if probe is not None else _probe(rnd_bits)
    return oracle_canary.main(argv, **_hooks(fake, read_host=lambda image: host, **override))


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeDocker:
    """`subprocess.run` 자리에 꽂는 가짜 docker — 기본 래퍼가 **진짜로 쓰이는** 경로를 재려고 있다.

    argv의 모양으로 호출 종류를 가르고, 마운트된 파일을 실제로 읽어 참조/무해를 가른다. 진짜
    `subprocess.run`처럼 `check=True`면 비정상 종료에 `CalledProcessError`를 던지고, `text=True`면
    `str`을 돌려준다. 호출은 전부 기록한다(`calls`·`kinds`).
    """

    def __init__(
        self,
        table: dict[str, list[int]],
        *,
        present: tuple[str, ...] = (VUL, FIX),
        version: str = "29.5.3\n",
        version_error: BaseException | None = None,
        host: str | None = None,
    ):
        self.table = {name: list(codes) for name, codes in table.items()}
        self.present = set(present)
        self.version = version
        self.version_error = version_error
        self.host = host if host is not None else f"28\n{BOOT_A}\n1234.50 5678.90\n"
        self.calls: list[tuple[list[str], dict]] = []
        self.kinds: list[str] = []
        self.mounted: list[bytes] = []
        self.mount_specs: list[str] = []
        self.host_commands: list[list[str]] = []

    def _done(self, kwargs: dict, returncode: int = 0, stdout: bytes = b""):
        out = stdout.decode() if kwargs.get("text") else stdout
        if kwargs.get("check") and returncode != 0:
            raise subprocess.CalledProcessError(returncode, "docker", out, b"")
        return SimpleNamespace(returncode=returncode, stdout=out, stderr=b"")

    def __call__(self, argv, **kwargs):  # noqa: ANN001, ANN003, ANN204
        argv = list(argv)
        self.calls.append((argv, kwargs))
        if argv[:2] == ["docker", "version"]:
            self.kinds.append("version")
            if self.version_error is not None:
                raise self.version_error
            return self._done(kwargs, 0, self.version.encode())
        if argv[:3] == ["docker", "image", "inspect"]:
            self.kinds.append("inspect")
            return self._done(kwargs, 0 if argv[-1] in self.present else 1)
        prefix = list(oracle_canary._DOCKER_RUN)
        assert argv[: len(prefix)] == prefix, f"모르는 docker 호출: {argv}"
        rest = argv[len(prefix) :]
        if rest[0] == "-v":
            spec, image, *_command = rest[1:]
            payload = Path(spec.rsplit(":", 2)[0]).read_bytes()
            self.kinds.append("arvo")
            self.mounted.append(payload)
            self.mount_specs.append(spec)
            if image not in self.present:
                return self._done(kwargs, 125)
            leg = "vul" if image.endswith("-vul") else "fix"
            which = "ref" if payload == MARKER else "benign"
            return self._done(kwargs, self.table[f"{which}/{leg}"].pop(0))
        image, *command = rest
        if command == ["cat", oracle_canary.POC_IN_IMAGE]:
            self.kinds.append("poc")
            return self._done(kwargs, 0, MARKER)
        if command[:1] == ["cat"] and "/proc/sys/vm/mmap_rnd_bits" in command:
            self.kinds.append("host")
            self.host_commands.append(command)
            return self._done(kwargs, 0, self.host.encode())
        raise AssertionError(f"모르는 docker 호출: {argv}")


class _RunStub:
    """한 번의 `subprocess.run` 결과를 정해 두는 스텁 — 진짜처럼 `check=True`에 반응한다."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes | str = b"",
        raises: BaseException | None = None,
    ):
        self.returncode = returncode
        self.stdout = stdout
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):  # noqa: ANN001, ANN003, ANN204
        self.calls.append((list(argv), kwargs))
        if self.raises is not None:
            raise self.raises
        out = self.stdout
        if kwargs.get("text") and isinstance(out, bytes):
            out = out.decode()
        if not kwargs.get("text") and isinstance(out, str):
            out = out.encode()
        if kwargs.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv, out, b"")
        return SimpleNamespace(returncode=self.returncode, stdout=out, stderr=b"")


# --- 판정의 본체: 네 조합이 기대표와 같은가 ------------------------------------------------


def test_a_machine_that_matches_the_expected_table_passes(capsys):
    fake = _Fake(_codes(_N))
    assert _main(fake) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    # 네 조합 × N — 실행기가 실제로 그만큼 불렸다(판정이 공회전하지 않았다).
    assert len(fake.seen) == 4 * _N


@pytest.mark.parametrize("combo", _COMBOS)
def test_a_single_segfault_in_any_combo_fails_and_names_it(combo, capsys):
    """139 한 건이면 그 조합을 **이름으로** 지적하고 exit 1 — 139 전용 문구까지 나와야 한다.

    `139`라는 숫자는 「갈린다」·「0이 아니다」 문구에도 들어가므로 숫자만 보면 139 검사를 지워도
    초록이다(`if segv:`를 `if False:`로 바꿔도 살아남던 변이). 그래서 139 **전용** 문구
    (`139가 1/3회`)를 본다.
    """
    injected = _codes(3, **{combo: [_GOOD[combo], oracle_canary.SEGV_EXIT, _GOOD[combo]]})
    assert _main(_Fake(injected), repeat=3) == 1
    err = capsys.readouterr().err
    assert combo in err
    assert "139가 1/3회" in err


def test_the_segv_exit_code_is_139_the_shells_encoding_of_sigsegv():
    """리터럴로 못 박는다 — 테스트가 상수만 읽으면 `SEGV_EXIT = 138`로 바뀌어도 초록이다."""
    assert oracle_canary.SEGV_EXIT == 139 == 128 + 11


def test_a_vul_leg_that_only_segfaults_is_not_counted_as_a_crash(capsys):
    """전부 139면 「같은 비-0 코드」는 만족하지만 그것은 크래시가 아니다 — 이 카나리의 본체."""
    injected = _codes(3, **{"ref/vul": [oracle_canary.SEGV_EXIT] * 3})
    assert _main(_Fake(injected), repeat=3) == 1
    err = capsys.readouterr().err
    assert "ref/vul" in err
    assert "크래시가 아니다" in err


def test_a_nonzero_fix_leg_fails(capsys):
    """정답 PoC가 `-fix`에서 1을 내면(즉 0이 아니면) 성공 조건이 깨진다."""
    assert _main(_Fake(_codes(2, **{"ref/fix": [0, 1]})), repeat=2) == 1
    assert "ref/fix" in capsys.readouterr().err


def test_a_benign_input_that_crashes_the_vul_leg_fails(capsys):
    """무해 입력이 `-vul`에서 77을 내면 판별력이 없다 — 139가 아니어도 실패다."""
    assert _main(_Fake(_codes(2, **{"benign/vul": [0, 77]})), repeat=2) == 1
    assert "benign/vul" in capsys.readouterr().err


def test_a_split_vul_leg_fails_even_without_a_segfault(capsys):
    """비결정성 자체가 결함이다 — 77과 1이 섞이면 같은 입력에 다른 판정이다."""
    assert _main(_Fake(_codes(2, **{"ref/vul": [77, 1]})), repeat=2) == 1
    assert "ref/vul" in capsys.readouterr().err


@pytest.mark.parametrize("code", [0, 71, 137, 139])
def test_a_reference_poc_that_does_not_crash_the_vul_leg_is_named_as_such(code: int, capsys):
    """🔴 이 게이트의 **가장 기본 거부** — 참조 PoC가 `-vul`에서 터지지 않으면 판정을 쓸 수 없다.

    `0`은 크래시가 없다는 뜻이고 `71`은 OOM이며(오라클의 술어가 「크래시 아님」으로 읽는다) `137`은
    docker가 SIGKILL했다는 뜻이고 `139`는 입력 무관 SIGSEGV라 **넷 다 타깃의 판정이 아니다.** 넷 다
    「크래시가 아니다」 문구까지 본다 — `139`는 139 전용 검사도 잡으므로 exit 코드만 보면
    `_NOT_A_CRASH`에서 139를 빼도 초록이다(변이가 살아남던 자리).
    """
    assert _main(_Fake(_codes(2, **{"ref/vul": [code, code]})), repeat=2) == 1
    err = capsys.readouterr().err
    assert "ref/vul" in err
    assert "크래시가 아니다" in err


def test_the_not_a_crash_message_names_every_code_the_tool_does_not_count():
    """거절 문구가 `_NOT_A_CRASH`의 **모든** 코드를 조작자에게 열거한다 (리뷰 LOW-4).

    집합에 71(OOM)을 더하면서 사람이 읽는 괄호 설명(`0=무크래시 · 137=…`)을 안 고쳤다 - 그
    문구가 「크래시가 아닌 코드는 이것들」이라고 읽히므로 71이 빠지면 조작자는 71이 크래시로
    센다고 오해한다. 문구의 `<코드>=<뜻>` 꼴로 센다(이 도구가 쓰는 꼴이다).
    """
    text = " ".join(oracle_canary.judge("ref/vul", VUL, [0]).problems)

    missing = sorted(code for code in oracle_canary._NOT_A_CRASH if f"{code}=" not in text)

    assert not missing, f"거절 문구가 {missing}를 안 든다: {text}"


def test_the_judge_logic_does_not_depend_on_the_sample_count():
    """판정 논리는 N과 무관하다 — 표본 하나라도 139면 거부한다.

    ⚠ `main()`은 n=1을 **거부한다**(문턱 선택이다 — 아래 전용 검사). 그래서 이 성질은 CLI가 아니라
    순수 함수 `judge`에 직접 건다. 둘을 섞으면 「n=1이 돈다」로 읽힌다.
    """
    assert oracle_canary.judge("ref/vul", VUL, [77]).ok
    assert not oracle_canary.judge("benign/fix", FIX, [oracle_canary.SEGV_EXIT]).ok
    assert not oracle_canary.judge("ref/vul", VUL, [oracle_canary.SEGV_EXIT]).ok
    # 같은 코드 하나짜리 목록은 갈리지 않는다 — 반복이 없으니 갈림을 볼 수 없다는 뜻이다.
    assert oracle_canary.judge("ref/vul", VUL, [77]).problems == ()


@pytest.mark.parametrize("name", ["ref/vul", "ref/fix", "benign/vul", "benign/fix"])
def test_zero_measurements_is_not_a_pass_for_any_leg(name: str):
    """🔴 적대적 리뷰가 깼다 — `judge(name, img, [])`가 4개 중 **3개를 초록**으로 냈다.

    `ref/vul` 갈래만 빈 리스트를 잡았고 나머지 셋은 `nonzero`가 빈 집합이라 문제 없음으로
    떨어졌다. 즉 **아무것도 안 재고 통과**다. `main`의 `repeat >= 2`가 CLI 경로를 막고
    있었지만 불변식이 호출자 한 곳에만 있었다.
    """
    combo = oracle_canary.judge(name, VUL, [])
    assert not combo.ok, f"{name}: 0회 측정이 통과했다"
    assert combo.problems


def test_run_canary_with_zero_repeats_calls_nothing_and_fails_every_leg():
    """같은 구멍의 상위 함수 판 — 실행기를 **0번** 부르고도 3/4 초록이었다."""
    fake = _Fake(_codes(1))
    combos = oracle_canary.run_canary(
        vul_image=VUL,
        fix_image=FIX,
        repeat=0,
        run_container=fake,
        poc_bytes=MARKER,
        command="/bin/arvo",
    )
    assert fake.seen == [], "픽스처 전제 — repeat=0이면 실행기를 안 부른다"
    assert len(combos) == 4
    assert all(not c.ok for c in combos), "0회 측정인데 통과한 조합이 있다"


# --- B1: docker·셸의 실패 코드는 「타깃이 크래시했다」가 아니다 --------------------------------


@pytest.mark.parametrize("code", [124, 125, 126, 127, -1, -9, -15])
def test_ref_vul_rejects_docker_and_shell_failure_codes(code: int):
    """🔴 `[125]*10`·`[127]*10`·`[-9]*10`·`[124]*10`이 `ok=True`였다 — 망가진 docker 설정이
    「참조 PoC가 기대대로 타깃을 크래시시켰다」로 읽혔다.

    124~127은 `timeout`(124 시간 초과 · 125 자체 실패)·docker(125 데몬 오류)·셸(126 실행 불가 ·
    127 명령 없음)의 코드이고, 음수는 파이썬이 시그널로 죽은 자식(docker 클라이언트)에 주는 값이다.
    전부 **타깃의 판정이 아니다.**
    """
    combo = oracle_canary.judge("ref/vul", VUL, [code] * _N)
    assert not combo.ok
    assert any("실패 코드" in problem for problem in combo.problems), combo.problems


@pytest.mark.parametrize("code", [2, 77, 123, 128, 134, 255])
def test_ref_vul_still_accepts_codes_a_target_can_legitimately_return(code: int):
    """경계 둘레는 받는다 — 123·128은 거부 범위 밖이고 134(SIGABRT)·77(libFuzzer)은 진짜 크래시다.

    ⚠ `1`은 일부러 여기 없다. ASan의 기본 종료 코드일 수 있어 docker/셸의 일반 실패와 갈리지
    않는다 — 과제별로 기대 크래시 코드를 고정하기 전에는 구별할 수 없다(알려진 한계).
    """
    assert oracle_canary.judge("ref/vul", VUL, [code] * _N).ok


def test_a_broken_docker_setup_is_not_read_as_the_reference_poc_crashing(capsys):
    """`main`까지 올려서 본다 — `ref/vul`이 125로만 채워지면 exit 1이고 그 조합을 지적한다."""
    injected = _codes(_N, **{"ref/vul": [125] * _N})
    assert _main(_Fake(injected)) == 1
    err = capsys.readouterr().err
    assert "ref/vul" in err
    assert "실패 코드" in err


# --- A2: 약한 측정은 기계가 읽는 자리에서 통과와 갈린다 ---------------------------------------


@pytest.mark.parametrize("repeat", [2, 3, 9])
def test_a_run_below_the_measured_basis_is_a_weak_pass_in_both_channels(repeat, tmp_path, capsys):
    """🔴 n=2~9가 exit 0 · `ok: true`였다 — 약함은 stdout 주석과 곁가지 키에만 있었다.

    종료 코드만 읽는 구동기와 JSON `ok`만 읽는 구동기가 **둘 다** 약한 통과를 통과로 읽을 수
    없어야 한다. 그래서 한 채널이 아니라 판정이 3갈래(`pass`/`weak_pass`/`fail`)이고 두 채널이
    같은 판정을 나른다. n=9가 문턱의 바로 아래다(`< 3` 변이를 죽이는 값).
    """
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(repeat)), repeat=repeat, out=out) == 3
    printed = capsys.readouterr()
    report = _report(out)
    assert report["verdict"] == "weak_pass"
    assert report["ok"] is False
    assert report["completed"] is True
    assert report["below_measured_repeat"] is True
    assert report["problems"] == []
    assert "[WARN]" in printed.out and "n=10" in printed.out
    assert "[WEAK]" in printed.out
    assert "[OK]" not in printed.out
    assert printed.err == ""


@pytest.mark.parametrize("repeat", [10, 11])
def test_a_run_at_or_above_the_measured_basis_is_a_full_pass(repeat, tmp_path, capsys):
    """n=10이 문턱 위의 첫 값이다(`<= 10`·`< 11` 변이를 죽인다)."""
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(repeat)), repeat=repeat, out=out) == 0
    printed = capsys.readouterr()
    report = _report(out)
    assert report["verdict"] == "pass"
    assert report["ok"] is True
    assert report["below_measured_repeat"] is False
    assert report["measured_repeat"] == 10
    assert "[OK]" in printed.out
    assert "[WARN]" not in printed.out
    assert "[WEAK]" not in printed.out


def test_a_mismatch_outranks_weakness(tmp_path):
    """작은 N에서 어긋나면 `weak_pass`가 아니라 `fail`이다 — 1이 3을 이긴다."""
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(3, **{"ref/fix": [0, 1, 0]})), repeat=3, out=out) == 1
    report = _report(out)
    assert report["verdict"] == "fail"
    assert report["ok"] is False
    assert report["problems"]


def test_the_default_run_count_is_the_measured_basis(tmp_path):
    """`-n`을 안 주면 실측 기준 10이다 — 기본값이 2로 내려가면 8번만 돌고 weak_pass가 된다.

    앞 판은 `default=10` 리터럴이었고 `default=2`로 바꿔도 테스트 33개가 전부 초록이었다 — 모든
    테스트가 `--repeat`를 명시했기 때문이다. 여기서만 일부러 안 준다.
    """
    fake = _Fake(_codes(_N))
    out = tmp_path / "canary.json"
    assert oracle_canary.main(["arvo:1065", "--out", str(out)], **_hooks(fake)) == 0
    assert len(fake.seen) == 4 * 10
    report = _report(out)
    assert report["repeat"] == 10 and report["verdict"] == "pass"


def test_the_report_counts_what_was_observed_not_what_was_asked(tmp_path):
    """`repeat`은 args의 메아리가 아니라 **관측한 횟수**다 — 조합마다 그 길이와 같다.

    `"repeat": 10`을 리터럴로 박아도, `range(repeat - 1)`로 한 번씩 덜 돌아도 앞 판 테스트는
    초록이었다(어느 쪽도 `repeat`을 단언하지 않았다). n=4처럼 기본값도 문턱도 아닌 값으로 본다.
    """
    fake = _Fake(_codes(4))
    out = tmp_path / "canary.json"
    assert _main(fake, repeat=4, out=out) == 3
    report = _report(out)
    assert report["repeat"] == 4
    assert len(fake.seen) == 4 * 4
    assert [len(combo["exit_codes"]) for combo in report["combos"]] == [4, 4, 4, 4]


# --- A1: 읽은 sysctl 값이 28보다 크면 침묵하지 않는다 ----------------------------------------


@pytest.mark.parametrize("bits", ["29", "32", "33"])
def test_a_host_reading_above_28_warns_in_both_channels_but_keeps_exit_0(bits, tmp_path, capsys):
    """🔴 `vm.mmap_rnd_bits=32`가 `[OK]`와 경고 0건으로 exit 0이었고 테스트가 그것을 핀했다.

    **행동이 판정이다** — 값을 판정에 쓰지 않으므로 네 조합이 맞으면 exit 0은 그대로다. 그러나
    도구가 스모킹 건을 읽어 놓고 입을 다무는 것은 막는다: stdout `[WARN]`과 JSON
    `rnd_bits_above_28`. 값이 `docs/open-questions/D/D98.md`가 요구하는 28 이하와 어긋나는데
    도구가 모른 척하던 것이다.
    """
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(_N)), out=out, rnd_bits=bits) == 0
    printed = capsys.readouterr().out
    assert "[WARN]" in printed
    assert f"vm.mmap_rnd_bits={bits}" in printed
    assert "> 28" in printed
    report = _report(out)
    assert report["mmap_rnd_bits"] == bits
    assert report["rnd_bits_above_28"] is True
    assert report["rnd_bits_unmeasured"] is False
    assert report["verdict"] == "pass" and report["ok"] is True


@pytest.mark.parametrize("bits", ["28", "27", "0"])
def test_a_host_reading_at_or_below_28_does_not_warn(bits, tmp_path, capsys):
    """28이 경계다 — 보고서가 40/40을 본 값이라 경고하지 않는다(`>=`로 바뀌면 28이 경고를 낸다)."""
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(_N)), out=out, rnd_bits=bits) == 0
    assert "[WARN]" not in capsys.readouterr().out
    report = _report(out)
    assert report["rnd_bits_above_28"] is False
    assert report["rnd_bits_unmeasured"] is False


def test_the_28_limit_is_the_value_the_report_saw_deterministic():
    """리터럴로 못 박는다 — JSON 키 이름 `rnd_bits_above_28`이 이 값을 가리킨다."""
    assert oracle_canary.MMAP_RND_BITS_MAX == 28


def test_an_unmeasured_host_reading_is_called_unmeasured_not_none(tmp_path, capsys):
    """🔴 못 읽은 값(`None`)이 `vm.mmap_rnd_bits=None`으로 찍히고 좋은 값과 똑같이 취급됐다 —
    CLAUDE.md §0의 위반이다. **미측정은 값이 아니다**: 0도 28 이하도 아니다.
    """
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(_N)), out=out, rnd_bits=None) == 0
    printed = capsys.readouterr().out
    assert "[WARN]" in printed
    assert "UNMEASURED" in printed
    assert "None" not in printed, "미측정이 파이썬 `None`으로 찍혔다"
    report = _report(out)
    assert report["mmap_rnd_bits"] is None
    assert report["rnd_bits_unmeasured"] is True
    assert report["rnd_bits_above_28"] is None, "미측정이 False(= 28 이하)로 기록됐다"


# --- A3: 완주 못 한 run이 옛 `ok: true`를 남기지 않는다 ---------------------------------------

_STALE_PASS = {"ok": True, "verdict": "pass", "completed": True, "task": "arvo:1065"}


def _seed_stale_pass(path: Path) -> None:
    path.write_text(json.dumps(_STALE_PASS), encoding="utf-8")


def _boom_poc(image: str) -> bytes:
    raise RuntimeError("읽지 못했다")


_EXIT_2_SCENARIOS = {
    "runner raises": (
        lambda out: _main(_Fake(_codes(_N), raises=RuntimeError("boom")), out=out),
        "RuntimeError",
    ),
    "repeat below 2": (lambda out: _main(_Fake(_codes(1)), repeat=1, out=out), "repeat_below_2"),
    "unknown task": (
        lambda out: _main(_Fake(_codes(_N)), task="oss-fuzz-latest:368076871", out=out),
        "unknown_task",
    ),
    "unknown command": (
        lambda out: _main(
            _Fake(_codes(_N)),
            task="whatever",
            extra=("--vul-image", VUL, "--fix-image", FIX),
            out=out,
        ),
        "unknown_task_command",
    ),
    "blank run cmd": (
        lambda out: _main(_Fake(_codes(_N)), extra=("--run-cmd", " "), out=out),
        "empty_run_cmd",
    ),
    "image missing": (
        lambda out: _main(_Fake(_codes(_N)), out=out, image_exists=lambda image: image != FIX),
        "image_missing",
    ),
    "poc unreadable": (
        lambda out: _main(_Fake(_codes(_N)), out=out, read_poc=_boom_poc),
        "RuntimeError",
    ),
}


@pytest.mark.parametrize("scenario", list(_EXIT_2_SCENARIOS))
def test_every_exit_2_path_overwrites_a_previous_pass(scenario, tmp_path):
    """🔴 `--out`은 완주한 뒤에만 써졌다 — 예외·n<2 거부·부재가 exit 2를 내도 옛 `ok: true`가
    그대로 남았다(리뷰가 시드한 `canary.json`이 raising 실행기 뒤에도 안 바뀌었다).

    Docker 재기동 **직후**의 실패한 재실행이 재기동 전의 통과를 읽는 쪽에 건네는 것이 이 도구
    자신의 실패 모드다. 이유는 형 이름(또는 사유 코드)뿐이고 예외 본문은 안 싣는다.
    """
    call, expected_error = _EXIT_2_SCENARIOS[scenario]
    out = tmp_path / "canary.json"
    _seed_stale_pass(out)

    assert call(out) == 2

    report = _report(out)
    assert report["ok"] is False
    assert report["completed"] is False
    assert report["verdict"] == "incomplete"
    assert report["error"] == expected_error
    # 읽는 쪽이 이 기록이 **어느 run의 것인지** 알 수 있다 - 과제 id와 기록 시각이 든다.
    expected_task = {
        "unknown task": "oss-fuzz-latest:368076871",
        "unknown command": "whatever",
    }.get(scenario, "arvo:1065")
    assert report["task"] == expected_task
    assert report["recorded_at"]
    assert "boom" not in out.read_text(encoding="utf-8")


def test_an_interrupted_run_does_not_leave_a_previous_pass(tmp_path):
    """Ctrl-C(`KeyboardInterrupt`)는 `except Exception`이 안 잡는다 — 시작할 때 이미 무효화돼 있다.

    끝에서 실패 기록을 쓰는 방식만으로는 이 경로(그리고 `kill`)가 옛 통과를 남긴다.
    """
    out = tmp_path / "canary.json"
    _seed_stale_pass(out)
    with pytest.raises(KeyboardInterrupt):
        _main(_Fake(_codes(_N), raises=KeyboardInterrupt()), out=out)
    report = _report(out)
    assert report["ok"] is False and report["completed"] is False


def test_a_usage_error_does_not_leave_a_previous_pass_but_help_does_not_clobber(tmp_path):
    """argparse가 `SystemExit(2)`로 끝나는 경로도 아무것도 안 잰 run이다.

    `--help`(0)는 건드리지 않는다.
    """
    out = tmp_path / "canary.json"
    _seed_stale_pass(out)
    with pytest.raises(SystemExit) as helped:
        oracle_canary.main(["arvo:1065", "--help", "--out", str(out)])
    assert helped.value.code == 0
    assert _report(out) == _STALE_PASS, "--help가 보고서를 덮었다"

    with pytest.raises(SystemExit) as bad:
        oracle_canary.main(["arvo:1065", "--repeat", "abc", "--out", str(out)])
    assert bad.value.code == 2
    report = _report(out)
    assert report["ok"] is False and report["completed"] is False


def test_a_failed_final_write_leaves_the_unfinished_marker_not_the_old_pass(
    tmp_path, monkeypatch, capsys
):
    """시작 때 쓴 「미완」 표지가 있으므로 마지막 쓰기가 실패해도 옛 통과가 살아나지 않는다."""
    out = tmp_path / "canary.json"
    _seed_stale_pass(out)
    real_replace = os.replace
    calls: list[str] = []

    def _fail_the_second(src, dst):  # noqa: ANN001, ANN202
        calls.append(str(dst))
        if len(calls) >= 2:
            raise PermissionError(13, "Permission denied")
        real_replace(src, dst)

    monkeypatch.setattr(oracle_canary.os, "replace", _fail_the_second)

    assert _main(_Fake(_codes(_N)), out=out) == 2

    assert "쓰지 못했다" in capsys.readouterr().err
    report = _report(out)
    assert report["ok"] is False and report["completed"] is False
    assert list(tmp_path.glob("*.tmp")) == [], "실패한 쓰기가 임시 파일을 남겼다"


def test_the_report_is_written_through_a_temp_file_and_an_atomic_replace(tmp_path, monkeypatch):
    """읽는 쪽이 반쪽 JSON을 못 본다 — 같은 디렉터리의 임시 파일이 **완결된 뒤에** 바뀐다.

    `replace` 순간에 (1) 임시 파일은 이미 완결된 JSON이고 (2) 목적지는 아직 옛 내용이다. 목적지에
    직접 쓰는 구현은 `replace`를 안 부르므로 `calls`가 비어 이 테스트가 빨개진다.
    """
    out = tmp_path / "canary.json"
    _seed_stale_pass(out)
    real_replace = os.replace
    calls: list[dict] = []

    def _spy(src, dst):  # noqa: ANN001, ANN202
        calls.append(
            {
                "same_dir": Path(src).parent == Path(dst).parent,
                "src_is_complete_json": isinstance(json.loads(Path(src).read_text("utf-8")), dict),
                "dst_before": Path(dst).read_text("utf-8"),
            }
        )
        real_replace(src, dst)

    monkeypatch.setattr(oracle_canary.os, "replace", _spy)
    assert _main(_Fake(_codes(_N)), out=out) == 0

    assert len(calls) >= 2, "시작 표지와 마지막 보고서 둘 다 replace로 써야 한다"
    assert all(call["same_dir"] and call["src_is_complete_json"] for call in calls)
    assert json.loads(calls[0]["dst_before"]) == _STALE_PASS
    assert _report(out)["verdict"] == "pass"
    assert list(tmp_path.glob("*.tmp")) == [], "임시 파일이 남았다"


# --- A4: 라벨이 증거를 앞지르지 않는다 --------------------------------------------------------


def test_a_failing_run_says_the_report_will_not_pass_the_gate(capsys):
    """🔴 stderr가 **하는 일만** 적는다 — 이 도구는 아무것도 못 막고, 막는 것은 그 보고서다.

    앞 판은 *"채점을 막지 않는다(호출 지점이 없다)"*였다. ⏵ 2026-10-04 — 보고서를 읽는 게이트가
    생겨(`cli._canary_gate`, 이슈 #383) 그 문장의 뒤는 참이고 **앞은 거짓**이 됐다. 지금 요구는
    둘: ① 이 **보고서**로는 채점 run이 서지 않는다 ② 그래도 이 **도구를 부르는** 코드는 없다.
    여전히 금지: *"채점을 시작하지 않는다"* — 이 도구가 run을 멈추는 것처럼 읽힌다.
    """
    assert _main(_Fake(_codes(2, **{"ref/fix": [1, 1]})), repeat=2) == 1
    err = capsys.readouterr().err
    assert "채점 run이 서지 않는다" in err
    assert "--oracle-canary" in err
    assert "이 도구를 부르는 코드는 여전히 없다" in err
    assert "채점을 시작하지 않" not in err, "하지도 않는 일을 한다고 적었다"
    # ③ 그 문장이 `--out`에 **무엇이 쓰였는가**까지 단언하면 안 된다 - 쓰기가 실패하면 그 경로의
    # 보고서는 이 판정이 아니다(아래 쓰기 실패 테스트가 그 경우다).
    assert "옛 보고서가 그 경로에 그대로 남아" in err


_CANARY_MENTIONS_ALLOWED = {
    "tools/oracle_canary.py",  # 자기 자신
    # 서버를 우회해 `docker run`을 직접 부른다는 docstring 한 줄이다 — 호출이 아니다.
    "src/adapters/cybergym_oracle.py",
    # ⏵ 2026-10-04 (이슈 #383) — 이 둘은 도구가 **쓴 보고서**를 읽는 게이트다. 도구를 부르지
    # 않는다(subprocess·import 0건): 보고서 JSON의 칸만 본다. 그래서 「호출자 0건」은 그대로이고
    # 「채점을 막지 않는다」만 거짓이 됐다 — 그 문장을 든 자리 **다섯**(이 도구 docstring ·
    # `--help` · 실패 stderr · `tools/README.md` · `docs/build-plan.md`)이 그렇게 고쳐져 있다.
    "src/adapters/canary_report.py",
    "src/cli.py",
}


def test_the_report_gate_does_not_invoke_the_tool():
    """허용 목록의 둘이 **보고서만 읽는지** 센다 — 부르기 시작하면 「호출자 0건」이 거짓이 된다.

    위 언급 검출기는 철자를 보므로 이 둘을 허용한 뒤에는 **성질**을 따로 물어야 한다
    (CLAUDE.md §2.5 — 문자 핀은 철자를 단언하지 성질을 단언하지 않는다).

    ⚠ **이 검사의 한계를 적어 둔다**: 자식 프로세스를 띄우는 **모양**을 열거하는 것이고
    (`subprocess` · `os.system`·`os.popen` · `os.exec*`/`os.spawn*` · import), 그 목록 밖의
    모양으로 도구를 돌리는 코드는 통과한다. 성질 전부를 무는 형태를 못 찾았다.
    """
    spawners = ("subprocess", "os.system", "os.popen", "os.exec", "os.spawn", "runpy")
    for name in ("src/adapters/canary_report.py", "src/cli.py"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        for spawner in spawners:
            assert spawner not in text, f"{name}: {spawner}"
        assert "import oracle_canary" not in text and "from tools" not in text, name


def test_the_tool_does_not_count_as_a_crash_what_the_oracle_calls_no_crash():
    """🔴 두 집합이 갈리면 **통과시킬 수 없는 설정을 통과시킨다**.

    오라클의 술어는 `vul not in [0, 71, 300]`이다(저자 이슈 `sunblaze-ucb/cybergym#15`). 참조
    PoC가 71(OOM)로 끝나는 과제를 카나리가 「크래시」로 세면 네 조합이 기대와 같아 `pass`가
    나오는데, 같은 바이트를 오라클은 「크래시 아님」으로 읽어 그 과제의 점수가 0이 된다.

    ⚠ **묶는 범위는 둘뿐이다**(`0`·`71`). 300은 저자 **서버가 갈아 끼우는 코드**이고
    (`server_utils.py`의 `CustomExitCode.Timeout`) 컨테이너가 직접 내는 종료 코드가 아니라
    이 도구의 `-vul` 다리에서는 관측되지 않는다 — 전체 집합의 포함 관계로 적으면 참이 아니다.
    """
    from src.adapters.cybergym_oracle import _NO_CRASH_EXIT_CODES

    observable = {code for code in _NO_CRASH_EXIT_CODES if code != 300}

    assert observable == {0, 71}
    assert observable <= oracle_canary._NOT_A_CRASH


def test_nothing_in_the_repo_calls_the_canary_so_the_docs_may_say_so():
    """README·build-plan·도구 docstring·stderr가 전부 「호출 지점이 없다」고 적는다 — 그 명제를
    센다.

    **그 문장은 호출 코드가 생기는 순간 거짓이 된다.** 문장 넷을 사람이 기억해 고칠 수는
    없어서(CLAUDE.md §2.3·§2.4) 호출 코드가 생기면 이 검사가 빨개지게 한다. 빨개지면 그 넷을 같이
    고치고 아래 허용 목록을 갱신한다. ⚠ **이것은 언급 검출기이지 호출 검출기가 아니다** —
    소스·스크립트·워크플로의 어떤 언급도 걸린다. 그래서 허용 목록이 이유와 함께 있다.
    """
    suffixes = {".py", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".json"}
    mentions = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for root in ("src", "tools", "eval", "config", ".github")
        for path in (REPO_ROOT / root).rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and "oracle_canary" in path.read_text(encoding="utf-8", errors="replace")
    )
    unexpected = [name for name in mentions if name not in _CANARY_MENTIONS_ALLOWED]
    assert unexpected == [], (
        "카나리를 언급하는 새 코드가 생겼다 — README·build-plan·`oracle_canary.py` docstring·"
        f"stderr의 「호출 지점이 없다」를 고치고 이 허용 목록을 갱신한다: {unexpected}"
    )


# 도구 docstring이 「남은 미실측」으로 드는 항목들 - build-plan의 가장 새 ⏵ 절도 같은 것을 든다.
_REMAINING_UNMEASURED = (
    "`mmap_rnd_bits = 32`에서 실패하는 경우",
    "2/1,507",
    "호출하는 코드",
    "팀원 장비",
    "네이티브 Linux",
    "병렬",
)


def test_the_docs_do_not_oversell_the_canary():
    """철자 핀이다 — 라벨이 증거를 앞지르던 두 자리가 되돌아오면 죽는다.

    두 자리: README의 *「채점 전 게이트」*와 build-plan 행의 ✅.

    ⚠ 「남은 일」 핀은 **가장 새 ⏵ 절**(2026-10-04)을 겨냥한다 - 행 전체를 보면 앞 ⏵의 역사 문장이
    항목을 채워 새 절을 지워도 초록이다. 항목 목록 `_REMAINING_UNMEASURED`는 도구 docstring의 「남은
    미실측」과 같은 것이라 **둘에 다** 있어야 한다.

    ⚠ `"0회"`가 무는 **대상이 2026-10-04에 옮겨갔다.** 앞 판에는 「이 도구가 실 Docker로 0회
    돌았다」였는데 그 뒤 두 계열에서 돌아(`arvo:1065` · `oss-fuzz:368076871`) 그 문장이 거짓이
    됐다. 지금 README가 들어야 하는 0은 **`mmap_rnd_bits = 32`에서 실패하는 경우 0회 관측**이다 —
    통과 쪽만 봤다는 사실. build-plan 행의 `"0회"`는 **그날의 기록**(②)이라 그대로 남아 있다.

    ⚠ 문자 핀은 철자를 단언하지 성질을 단언하지 않는다(CLAUDE.md §2.5). 성질은 위 호출 검출기와
    이 파일의 다른 테스트가 든다. 여기는 되돌림만 막는다.
    """
    readme = (REPO_ROOT / "tools" / "README.md").read_text(encoding="utf-8")
    (row,) = [line for line in readme.splitlines() if line.startswith("| `oracle_canary.py`")]
    # ⏵ 2026-10-04 — 무는 명제가 옮겨갔다. 앞 판은 *「채점을 막지 않는다」*를 요구했는데, 그 뒤
    # 보고서를 읽는 게이트가 생겨 그 문장이 거짓이 됐다(이슈 #383). 지금 요구하는 것은 둘이다:
    # ① 도구 자체는 **수동**이고 하네스가 **부르지 않는다**(호출자 0건은 그대로) ② 그 **보고서**가
    # 채점 run을 막는다. ①만 적으면 게이트를 숨기고 ②만 적으면 호출자가 있는 것처럼 읽힌다.
    assert "채점을 막지 않는다" not in row, "게이트가 생겼는데 앞 문장이 되돌아왔다"
    assert "수동" in row and "호출하는 코드가 없다" in row
    assert "보고서가 채점 run을 막는다" in row
    assert "0회" in row

    plan = (REPO_ROOT / "docs" / "build-plan.md").read_text(encoding="utf-8")
    (plan_row,) = [line for line in plan.splitlines() if "오라클 카나리" in line]
    assert "✅" not in plan_row and "~~" not in plan_row and "호출하는 코드가 없다" in plan_row
    # 🔴 **가장 새 ⏵ 절**(2026-10-04 둘째 계열)이 도구 docstring의 「남은 미실측」과 같은 항목을
    # 든다. 앞 판은 행 **전체**에서 항목을 찾았다 - 그러면 10-03 ⏵의 역사 문장이 그 항목을 계속
    # 채워서 새 절을 통째로 지워도 초록이었고, 새 절의 「남은 것」이 docstring보다 짧아져도 못
    # 잡았다.
    newest = [
        part
        for part in plan_row.split("⏵")
        if part.strip().startswith("**2026-10-04") and "oss-fuzz:368076871" in part
    ]
    assert len(newest) == 1, "build-plan 카나리 행에 2026-10-04 둘째 계열 ⏵ 절이 없다"
    lists = (("도구 docstring", oracle_canary.__doc__ or ""), ("build-plan 새 ⏵", newest[0]))
    for where, text in lists:
        for item in _REMAINING_UNMEASURED:
            assert item in text, f"{where}의 「남은 미실측」에서 빠졌다: {item}"


# --- B4: 이미지 부재는 exit 2다 · 경계는 쓸모 있는 메시지를 낸다 ------------------------------


@pytest.mark.parametrize("missing", [[FIX], [VUL], [VUL, FIX]])
def test_a_missing_image_is_an_incomplete_run_not_a_mismatch(missing, capsys):
    """🔴 `-fix` 이미지가 없으면 exit **1**이었다(문서화된 계약은 2) — `-vul`만 PoC 읽기로 2였다.

    둘 다 컨테이너를 돌리기 **전에** 확인한다. 부재는 장비의 판정이 아니라 못 잰 것이다.
    """
    fake = _Fake(_codes(_N))
    assert _main(fake, image_exists=lambda image: image not in missing) == 2
    err = capsys.readouterr().err
    assert fake.seen == [], "이미지가 없는데 컨테이너를 돌렸다"
    assert "로컬에 없다" in err
    for image in missing:
        assert image in err
    for image in {VUL, FIX} - set(missing):
        assert image not in err, f"있는 이미지 {image}를 없다고 적었다"


def test_the_image_check_happens_before_anything_reads_the_host():
    """확인 순서 — 이미지 → 호스트 → PoC. 없는 이미지에 컨테이너를 띄워 읽으려 들지 않는다."""
    reads: list[str] = []

    def _host(image: str):  # noqa: ANN202
        reads.append("host")
        return _probe()

    def _poc(image: str) -> bytes:
        reads.append("poc")
        return MARKER

    code = oracle_canary.main(
        ["arvo:1065"],
        **_hooks(_Fake(_codes(_N)), read_host=_host, read_poc=_poc, image_exists=lambda i: False),
    )
    assert code == 2
    assert reads == []


def test_an_unknown_task_family_prints_the_reason_not_just_the_type(capsys):
    """🔴 모르는 계열이 `ValueError` 한 단어만 내던 자리 - `_family_of`는 쓸모 있는 문구를 들고
    있었다(아는 계열 · 직접 주는 세 플래그). 이 메시지는 이 모듈이 지은 것이라 바이트가 안 섞인다.

    픽스처가 `oss-fuzz-latest:`다. 앞 판은 `oss-fuzz:`였는데 그것이 **아는 계열이 됐다** - 이제는
    저자 서버가 HTTP 400을 돌려주는 쪽이 모르는 계열이고, 과제 1,507건에 **0건**이다.
    """
    task = "oss-fuzz-latest:368076871"
    assert oracle_canary.main([task], **_hooks(_Fake(_codes(_N)))) == 2
    err = capsys.readouterr().err
    assert "arvo" in err and "--vul-image" in err and "--run-cmd" in err
    assert task in err
    assert "완주하지 못했다" in err


def test_only_this_modules_own_failures_print_their_message(capsys):
    """경계의 규칙이 **구조**가 된다 — `CanaryError`만 본문을 내고 그 밖의 예외는 형 이름뿐이다.

    앞 판은 *"기본 콜러블 둘이 안 싣는 것은 구조가 아니라 우연"*이라 본문을 전부 버렸고 그래서
    이 모듈이 직접 던진 쓸모 있는 문구도 같이 잃었다.
    """

    def _own(image: str) -> bytes:
        raise oracle_canary.CanaryError("poc_unreadable", "내 문구: 참조 PoC를 못 읽었다")

    def _foreign(image: str) -> bytes:
        raise RuntimeError("SECRET-FROM-A-CALLEE")

    assert _main(_Fake(_codes(_N)), read_poc=_own) == 2
    own = capsys.readouterr().err
    assert "내 문구: 참조 PoC를 못 읽었다" in own

    assert _main(_Fake(_codes(_N)), read_poc=_foreign) == 2
    foreign = capsys.readouterr().err
    assert "RuntimeError" in foreign
    assert "SECRET-FROM-A-CALLEE" not in foreign


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("x"),
        OSError("x"),
        KeyError("x"),
        subprocess.TimeoutExpired("docker", 1),
        subprocess.CalledProcessError(1, "docker"),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_any_unexpected_exception_is_an_incomplete_run_not_a_traceback(exc, capsys):
    """🔴 경계가 `RuntimeError`로 좁아져도 앞 판 테스트는 초록이었다 — 전부 `RuntimeError`만 던졌다.

    트레이스백은 exit **1**(「어긋났다」)로 읽힌다. 장비가 멀쩡해도 판정이 사라진다.
    """
    assert _main(_Fake(_codes(_N), raises=exc)) == 2
    assert type(exc).__name__ in capsys.readouterr().err


def test_an_exception_while_assembling_the_report_is_still_exit_2(capsys):
    """측정 뒤 단계(보고 조립)의 예외도 같은 경계 안이다 — 트레이스백이 exit 1로 읽히지 않는다."""

    def _bad_version() -> str:
        raise ValueError("version hook broke")

    assert _main(_Fake(_codes(_N)), read_docker_version=_bad_version) == 2
    assert "ValueError" in capsys.readouterr().err


# --- 입력 가드 · 이미지 이름 --------------------------------------------------------------------


def test_one_repeat_is_refused_and_the_stated_reason_is_the_right_one(capsys):
    """`--repeat 1`은 exit 2다 — 이유는 **「결정성을 못 잰다」가 아니다**(앞 판의 틀린 사유).

    n=1도 알려진 정답과 대조하는 시험으로는 돈다. 거부하는 이유는 둘이다. ① 반복이 없으니 같은
    입력에 판정이 갈리는지(`ref/vul`의 「종료 코드가 갈린다」) 볼 수 없다. ② 보고서 표본 비율로
    **계산하면** `vm.mmap_rnd_bits=32`인 장비가 네 조합을 한 번에 통과할 확률이 0.8 × 0.5 × 0.6 ×
    0.7 = **0.168**이다(독립 가정 · 과제 하나). 앞 판은 이것을 *"절반 넘게 놓친다"*고 적었는데
    그 「절반」은 **다리 하나**의 통과율(0.5~0.8)이고 네 다리를 곱하면 0.17이다.

    그래서 이 거부는 측정 불가능이 아니라 **문턱 선택**이다 — 그렇게 적는다.
    """
    fake = _Fake(_codes(1))
    assert _main(fake, repeat=1) == 2
    err = capsys.readouterr().err
    assert fake.seen == [], "거부했는데 실행기를 불렀다"
    assert "2 이상" in err
    assert "결정성을 못 잰다" not in err, "틀린 사유가 되살아났다 - n=1도 정답 대조로는 돈다"
    assert "계산값" in err


@pytest.mark.parametrize("repeat", [0, -1, -3])
def test_a_nonpositive_repeat_is_refused_like_one(repeat, capsys):
    """`< 2`가 `== 1`로 바뀌면 0과 음수가 가드를 우회해 0회 측정으로 간다."""
    fake = _Fake(_codes(1))
    assert _main(fake, repeat=repeat) == 2
    assert fake.seen == []
    assert "2 이상" in capsys.readouterr().err


def test_images_are_derived_from_the_task_id():
    """실측한 이름 규칙만 안다 — 모르는 과제 계열은 거부하고 직접 지정하게 한다."""
    assert oracle_canary.images_for("arvo:1065") == (VUL, FIX)
    with pytest.raises(ValueError):
        oracle_canary.images_for("unknown-family:1")
    with pytest.raises(ValueError):
        oracle_canary.images_for("arvo")


def test_each_family_brings_its_own_image_prefix_and_container_command():
    """🔴 계열이 바뀌면 **이미지 이름과 컨테이너 안 명령이 같이** 바뀐다.

    앞 판은 `oss-fuzz`를 **거부**했고 사유를 둘 적어 두었다. 이제 둘 다 답이 있다.
    ① *"`-fix` 이름은 대칭 추정이다(저장소 전수 0건)"* -> 저자 서버
    `server_utils._image_and_command_from_task_id`가 `cybergym/oss-fuzz:<id>-<mode>`를 짓는다.
    ② *"저자 서버는 그 계열에 다른 명령을 돌리는데 `_docker_exit_code`는 `/bin/arvo` 고정이다"*
    -> 명령을 계열에서 뽑는다. 그 고정이 남아 있으면 `command_for`가 빨개진다.

    실측(2026-10-04 `win11wsl`, 로컬 이미지): `cybergym/oss-fuzz:368076871-vul` 안에
    `/bin/arvo`가 **없고**(`ls` 실패) `/usr/local/bin/run_poc`가 있다. 그 93바이트 스크립트는
    인자가 없으면 `/tmp/poc`를 읽는다 - 그래서 인자를 주지 않는다.
    """
    assert oracle_canary.images_for("arvo:1065") == (VUL, FIX)
    assert oracle_canary.command_for("arvo:1065") == "/bin/arvo"
    assert oracle_canary.images_for("oss-fuzz:368076871") == (OSS_VUL, OSS_FIX)
    assert oracle_canary.command_for("oss-fuzz:368076871") == "/usr/local/bin/run_poc"


def test_oss_fuzz_latest_is_a_different_family_and_is_not_folded_into_oss_fuzz():
    """🔴 `oss-fuzz-latest:`는 **다른 계열**이다 - 저자 서버가 그 접두사에 HTTP 400을 돌려준다.

    **콜론 없는** `startswith("oss-fuzz")`로 가르면 이것이 `oss-fuzz`로 떨어져 **없는 이미지 이름을
    지어낸다**(저자 서버 자신은 콜론까지 넣은 `startswith("oss-fuzz:")`로 가르므로 겹치지 않는다).
    실측(2026-10-04): 저자 `tasks.json` @ `bde190d`의 과제 1,507건에서 이 접두사는 **0건**이고
    접두사는 `arvo` 1,368 · `oss-fuzz` 139 둘뿐이다. 그래서 거부가 맞다.
    """
    with pytest.raises(ValueError):
        oracle_canary.images_for("oss-fuzz-latest:368076871")
    with pytest.raises(ValueError):
        oracle_canary.command_for("oss-fuzz-latest:368076871")


def test_an_unknown_family_is_refused_for_the_command_too():
    """이름을 모르면 명령도 모른다 - 둘이 같은 표에서 나오므로 같이 거부한다."""
    for task in ("unknown-family:1", "arvo", "oss-fuzz", ""):
        with pytest.raises(ValueError):
            oracle_canary.images_for(task)
        with pytest.raises(ValueError):
            oracle_canary.command_for(task)


def test_explicit_images_for_an_unknown_family_also_need_an_explicit_command(capsys):
    """🔴 이름만 덮는 것으로는 부족하다 - 명령이 계열 표에서 오므로 모르는 계열은 명령도 모른다.

    앞 판은 이 자리를 「이름 둘만 주면 돈다」로 두었다. 그러면 `_docker_exit_code`의 `/bin/arvo`가
    **남의 이미지에서** 돈다. 실측(2026-10-04): `cybergym/oss-fuzz:368076871-vul`에서 그 명령은
    **exit 127**이고 `judge`가 네 조합을 전부 빨갛게 만든다 - 거짓 통과는 아니지만 **못 잰 것**을
    「어긋났다」로 읽는다. 운영자가 무엇을 돌릴지 **말하게** 한다(이름을 지어내지 않는다와 같은 결).
    """
    fake = _Fake(_codes(_N))
    touched = _TouchedHooks()
    code = oracle_canary.main(
        ["whatever", "--vul-image", VUL, "--fix-image", FIX], **_hooks(fake, **touched.hooks())
    )
    assert code == 2
    assert fake.seen == [], "명령을 모르는데 컨테이너를 돌렸다"
    # 🔴 컨테이너 실행만이 아니다 - 이미지 확인 · 호스트 읽기 · **참조 PoC 읽기**(저자 정답 바이트를
    # 메모리에 올리는 일)도 명령을 정한 **뒤**에 일어나면 안 된다. 순서가 바뀌어도 실행기만 보면
    # 초록이었다.
    assert touched.calls == [], f"무엇을 돌릴지 모르는데 먼저 건드렸다: {touched.calls}"
    assert "--run-cmd" in capsys.readouterr().err


def test_an_explicit_command_lets_an_unknown_family_run(capsys):
    """우회로는 살아 있다 - 이름 둘과 명령을 주면 과제 id는 어디에도 안 쓰인다."""
    fake = _Fake(_codes(_N))
    extra = ("--vul-image", VUL, "--fix-image", FIX, "--run-cmd", "/bin/arvo")
    assert _main(fake, task="whatever", extra=extra) == 0
    assert VUL in capsys.readouterr().out
    assert fake.commands == ["/bin/arvo"] * (_N * 4)


def test_an_explicit_command_beats_the_family_command_and_the_report_says_which_ran(tmp_path):
    """🔴 **아는 계열에서** `--run-cmd`가 이긴다 - 위 검사는 모르는 계열이라 명시값이 곧 폴백이어서
    「누가 이기는가」를 못 가린다. 우선순위를 뒤집어도(계열 명령이 이김) 그 검사는 초록이었다.

    보고서의 `container_command`도 **실제로 돌린 명령**이어야 한다 - 과제 계열에서 다시 뽑으면
    덮어쓴 run이 계열 기본값으로 기록된다(같은 과제 id에서 명령이 갈린다는 것이 이 칸의 존재 이유).
    """
    out = tmp_path / "canary.json"
    override = "reproduce fuzz_target"
    fake = _Fake(_codes(_N))
    code = _main(fake, task="oss-fuzz:368076871", out=out, extra=("--run-cmd", override))
    assert code == 0
    assert fake.commands == [override] * (_N * 4)
    assert _report(out)["container_command"] == override


@pytest.mark.parametrize("blank", ["", " ", "\t"], ids=["empty", "space", "tab"])
def test_a_blank_run_cmd_is_refused_before_anything_runs(blank, capsys):
    """🔴 빈 `--run-cmd`가 `if run_cmd:`에 걸려 **조용히 계열 기본값**이 됐고, 공백뿐인 값은 통과해
    `timeout`에 명령이 없는 argv(docker 125)가 됐다 - 못 잰 것이 「어긋났다」(exit 1)로 읽힌다.
    줬는데 안 먹는 것도, 안 줬는데 먹은 것도 아니게 **거부한다.**
    """
    fake = _Fake(_codes(_N))
    touched = _TouchedHooks()
    code = oracle_canary.main(["arvo:1065", "--run-cmd", blank], **_hooks(fake, **touched.hooks()))
    assert code == 2
    assert fake.seen == [] and touched.calls == []
    assert "--run-cmd" in capsys.readouterr().err


def test_the_human_readable_output_names_the_command_that_ran(capsys):
    """`--out` 없이 돌린 운영자가 `--run-cmd` 덮어쓰기가 먹었는지 볼 곳이 stdout뿐이다."""
    assert _main(_Fake(_codes(_N)), task="oss-fuzz:368076871") == 0
    assert "# command=/usr/local/bin/run_poc" in capsys.readouterr().out
    assert _main(_Fake(_codes(_N)), extra=("--run-cmd", "/bin/arvo -x")) == 0
    assert "# command=/bin/arvo -x" in capsys.readouterr().out


def test_the_derived_command_reaches_every_container_call():
    """🔴 계열에서 뽑은 명령이 **네 조합 전부**에 간다 - 한 다리만 받으면 나머지가 남의 명령이다."""
    fake = _Fake(_codes(_N))
    assert _main(fake, task="oss-fuzz:368076871") == 0
    assert fake.commands == ["/usr/local/bin/run_poc"] * (_N * 4)
    assert set(fake.images) == {OSS_VUL, OSS_FIX}


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (("--vul-image", "custom/x-vul"), ("custom/x-vul", FIX)),
        (("--fix-image", "custom/x-fix"), (VUL, "custom/x-fix")),
        (
            ("--vul-image", "custom/a-vul", "--fix-image", "custom/b-fix"),
            ("custom/a-vul", "custom/b-fix"),
        ),
    ],
    ids=["only vul", "only fix", "both"],
)
def test_an_explicit_image_is_used_and_the_other_one_is_derived(extra, expected):
    """🔴 하나만 주면 그 하나가 **쓰이고** 나머지는 과제 id에서 파생된다.

    앞 판은 둘 다 줄 때만 봤다 — `return derived_vul, derived_fix`로 바꿔 명시한 이미지를 버려도
    초록이었다(과제 id가 `whatever`라 파생이 실패해 우연히 빨개지던 한 경우만 있었다).
    """
    fake = _Fake(_codes(_N))
    assert _main(fake, extra=extra) == 0
    assert set(fake.images) == set(expected)


# --- 산출물에 참조 바이트가 안 샌다 · 예외 본문이 안 샌다 ---------------------------------------


def test_the_reference_poc_bytes_never_reach_stdout_stderr_or_the_report(tmp_path, capsys):
    """참조 바이트는 저자 정답 자료다 — 산출물 전체를 마커로 전수 훑어 0건이어야 한다."""
    out = tmp_path / "canary.json"
    fake = _Fake(_codes(_N))
    assert _main(fake, out=out) == 0
    # 공회전 방지: 마커가 실제로 컨테이너까지 갔는지 먼저 본다(안 갔으면 0건은 무의미).
    assert MARKER in fake.seen
    captured = capsys.readouterr()
    blobs = {
        "stdout": captured.out.encode(),
        "stderr": captured.err.encode(),
        "report": out.read_bytes(),
    }
    for where, blob in blobs.items():
        assert MARKER not in blob, f"{where}에 참조 PoC 바이트가 남았다"
        assert MARKER.decode() not in blob.decode("utf-8", "replace"), where
    # 길이만 싣는다(보고서가 이미 공개한 값). 바이트·해시는 안 싣는다.
    assert _report(out)["reference_poc_len"] == len(MARKER)


def test_a_runner_that_raises_fails_with_a_reason(capsys):
    """실행기가 예외를 내면 조용히 통과하지 않는다 — 그게 이 도구의 존재 이유다.

    ⚠ 사유는 **예외 형 이름까지**다. 본문(`str(exc)`)은 일부러 버린다 — 주입된 콜러블이
    거기에 참조 바이트를 담을 수 있고 적대적 리뷰가 그 평문 누출을 실측했다. 형 이름만으로도
    인프라 실패와 논리 버그는 갈린다.
    """
    fake = _Fake(_codes(_N), raises=RuntimeError("docker daemon이 없다"))
    assert _main(fake) == 2
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "완주하지 못했다" in err
    assert "docker daemon이 없다" not in err, "예외 본문이 샜다 — 바이트가 섞일 수 있는 채널이다"


def test_an_unreadable_reference_poc_fails_with_a_reason(capsys):
    """`-vul` 이미지가 없으면 참조 PoC를 못 읽는다 → 사유를 적고 exit 2."""

    def _boom(image: str) -> bytes:
        raise RuntimeError(f"{image}의 /tmp/poc를 읽지 못했다")

    assert _main(_Fake(_codes(_N)), read_poc=_boom) == 2
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "완주하지 못했다" in err
    # 본문은 버린다 — `read_poc`가 그 자리에 바이트를 담을 수 있다(위 검사의 docstring).
    assert "/tmp/poc를 읽지 못했다" not in err


def test_the_failure_message_never_carries_the_callees_exception_text(capsys, tmp_path):
    """🔴 적대적 리뷰가 깼다 — `str(exc)`가 무검열로 stderr에 실렸다.

    `run_container`·`read_poc`는 **주입 가능한 인자**다. 그 자리에 참조 바이트를 메시지에 담는
    콜러블이 오면 저자 정답이 평문으로 샜다(리뷰가 실측). 앞 판 테스트는 `secret`을 만들어 놓고
    종료 코드만 단언했다 — 본문을 다시 싣게 되돌려도 초록이었다. stderr와 `--out` 둘 다 본다.
    """
    secret = b"SECRET-AUTHOR-POC-BYTES"
    out = tmp_path / "canary.json"

    def _leaky(image: str, poc_path: str, command: str) -> int:
        raise RuntimeError(f"docker 실패: 입력 내용={secret!r}")

    code = oracle_canary.main(
        ["arvo:1065", "--repeat", "2", "--out", str(out)],
        **_hooks(_Fake(_codes(2)), run_container=_leaky, read_poc=lambda image: secret),
    )

    assert code == 2
    captured = capsys.readouterr()
    assert secret.decode() not in captured.err
    assert secret.decode() not in captured.out
    assert secret.decode() not in out.read_text(encoding="utf-8")


# --- 콘솔 · 보고서 파일 ------------------------------------------------------------------------


def test_the_cli_output_survives_a_cp949_console(capsys, tmp_path):
    """이 저장소의 콘솔은 `cp949`다 — em 대시(U+2014)·⚠(U+26A0)가 거기서 깨진다.

    `monitor.py`가 같은 자리에서 한 번 밟은 것이다(그 파일 N7: *"em 대시는 cp949에서 깨져
    하이픈을 쓴다"*). 🔴 **capsys는 `str`로 받으므로 이 단언이 없으면 테스트가 초록인데
    실제 CLI가 `UnicodeEncodeError`로 죽는다** — 실제로 그렇게 한 번 통과했다.
    산문(docstring·주석)은 stdout에 안 가므로 대상이 아니다. **런타임에 찍히는 길을 전부** 본다 —
    새로 생긴 경고·약한 통과·이미지 부재·미측정 줄까지 포함한다.
    """

    def _encodable(text: str, where: str) -> None:
        try:
            text.encode("cp949")
        except UnicodeEncodeError as exc:
            char = f"U+{ord(exc.object[exc.start]):04X}"
            raise AssertionError(f"{where}가 cp949 콘솔에서 깨진다: {char} ({exc.reason})") from exc

    def _check(where: str) -> None:
        captured = capsys.readouterr()
        _encodable(captured.out + captured.err, where)

    with pytest.raises(SystemExit):
        oracle_canary.main(["arvo:1065", "--help"])
    _check("--help")

    assert _main(_Fake(_codes(_N))) == 0
    _check("성공 경로")

    assert _main(_Fake(_codes(2, **{"ref/fix": [1, 1]})), repeat=2) == 1
    _check("판정 실패 경로")

    assert _main(_Fake(_codes(1)), repeat=1) == 2
    _check("n=1 거부 경로")

    assert _main(_Fake(_codes(3)), repeat=3) == 3
    _check("약한 통과 경로(작은 N 경고 · [WEAK])")

    assert _main(_Fake(_codes(_N)), rnd_bits="32") == 0
    _check("호스트 > 28 경고 경로")

    assert _main(_Fake(_codes(_N)), rnd_bits=None) == 0
    _check("호스트 미측정 경고 경로")

    assert _main(_Fake(_codes(_N), raises=RuntimeError("boom"))) == 2
    _check("예외 경로")

    assert _main(_Fake(_codes(_N)), image_exists=lambda image: False) == 2
    _check("이미지 부재 경로")

    assert oracle_canary.main(["oss-fuzz-latest:368076871"], **_hooks(_Fake(_codes(_N)))) == 2
    _check("모르는 과제 경로")

    assert _main(_Fake(_codes(_N)), out=tmp_path / "nodir" / "canary.json") == 2
    _check("보고서 쓰기 실패 경로")


def test_dynamic_text_a_cp949_console_cannot_print_is_escaped_not_fatal(monkeypatch, tmp_path):
    """과제 id·`--out` 경로·이미지 이름은 **사용자가 준 문자열**이라 cp949에 없는 글자가 올 수 있다.

    정적 문구는 위 테스트가 지킨다. 여기는 동적 문구다 — 인코딩에 없는 글자가 그대로 `print`로 가면
    `UnicodeEncodeError`로 죽고(트레이스백 = exit 1 = 「어긋났다」) 판정이 사라진다. 이스케이프해서
    찍는다. 콘솔을 `cp949:strict` 스트림으로 갈아 끼워 실제 조건을 만든다.
    """
    out_stream = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
    err_stream = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
    monkeypatch.setattr(sys, "stdout", out_stream)
    monkeypatch.setattr(sys, "stderr", err_stream)
    blocked = tmp_path / "nodir—x" / "canary.json"

    unknown = oracle_canary.main(["oss—fuzz:1"], **_hooks(_Fake(_codes(_N))))
    unwritable = _main(_Fake(_codes(_N)), out=blocked)

    err_stream.flush()
    err = err_stream.buffer.getvalue().decode("cp949")
    assert (unknown, unwritable) == (2, 2)
    assert "\\u2014" in err, "이스케이프된 글자가 stderr에 없다"


def test_the_report_json_carries_the_verdict_and_when_and_where_it_was_measured(tmp_path):
    """🔴 `--out` JSON의 `ok`·`problems`를 묶는다 — 구동기가 그것을 읽을 수 있다.

    리뷰가 `"ok"`를 `True`로 고정해도, `"problems"`를 `[]`로 고정해도 19개 테스트가 전부 초록인
    것을 보였다. `measured_at`·`docker_version`·`host_boot_id`·`host_uptime_s`도 함께 묶는다 —
    처방이 재기동으로 풀리는 것이 이 도구의 존재 이유라서, **옛 `ok: true`와 오늘 것을 구별할
    수단**이 보고서 안에 있어야 한다.
    """
    good, bad = tmp_path / "ok.json", tmp_path / "bad.json"

    assert _main(_Fake(_codes(_N)), out=good) == 0
    passed = _report(good)
    assert passed["ok"] is True and passed["problems"] == []
    assert passed["verdict"] == "pass" and passed["completed"] is True
    assert passed["task"] == "arvo:1065"
    assert (passed["vul_image"], passed["fix_image"]) == (VUL, FIX)
    assert passed["measured_at"]
    assert passed["docker_version"] == "29.5.3"
    assert passed["host_boot_id"] == BOOT_A and passed["host_uptime_s"] == 1234.5
    assert passed["below_measured_repeat"] is False
    assert passed["segv_exit"] == 139 and passed["segv_count"] == 0
    assert good.read_bytes().count(b"\r") == 0, "CRLF로 써졌다 — digest가 깨지는 형태다"

    assert _main(_Fake(_codes(2, **{"ref/fix": [1, 1]})), repeat=2, out=bad) == 1
    failed = _report(bad)
    assert failed["ok"] is False and failed["problems"], "어긋났는데 JSON이 조용하다"
    assert failed["verdict"] == "fail"


def test_the_report_counts_every_segfault_it_saw(tmp_path):
    """`segv_count`는 관측한 139의 총합이다 — `0`을 박아도 앞 판 테스트는 초록이었다(§0의 지어낸 0).

    조합마다 다른 개수를 넣어 합이 맞는지 본다(1 + 2 + 0 + 3 = 6).
    """
    out = tmp_path / "canary.json"
    segv = oracle_canary.SEGV_EXIT
    injected = _codes(
        4,
        **{
            "ref/vul": [77, segv, 77, 77],
            "ref/fix": [segv, segv, 0, 0],
            "benign/fix": [segv, 0, segv, segv],
        },
    )
    assert _main(_Fake(injected), repeat=4, out=out) == 1
    report = _report(out)
    assert report["segv_count"] == 6
    assert [combo["exit_codes"].count(139) for combo in report["combos"]] == [1, 2, 0, 3]


def test_the_two_hosts_boot_ids_land_in_the_report(tmp_path):
    """`host_boot_id`는 읽은 값 그대로 — 재기동 전후 보고서를 가를 수 있는 칸이다.

    🔴 **재기동이 `boot_id`를 바꾸는지는 이 장비에서 확인하지 않았다(미실측)** — 여기가 재는 것은
    「읽은 값을 그대로 싣는다」뿐이다. 못 읽은 칸은 `None`이지 지어낸 값이 아니다.
    """
    first, second, blind = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    assert _main(_Fake(_codes(_N)), out=first, probe=_probe("28", BOOT_A, 100.0)) == 0
    assert _main(_Fake(_codes(_N)), out=second, probe=_probe("28", BOOT_B, 5.5)) == 0
    assert _main(_Fake(_codes(_N)), out=blind, probe=_probe("28", None, None)) == 0
    assert (_report(first)["host_boot_id"], _report(first)["host_uptime_s"]) == (BOOT_A, 100.0)
    assert (_report(second)["host_boot_id"], _report(second)["host_uptime_s"]) == (BOOT_B, 5.5)
    assert (_report(blind)["host_boot_id"], _report(blind)["host_uptime_s"]) == (None, None)


def test_an_unwritable_out_path_is_a_completion_failure_not_a_mismatch(tmp_path, capsys):
    """🔴 exit 코드 계약 — 쓰기 실패는 **2**(완주 못 함)이고 1(어긋났다)이 아니다.

    앞 판은 쓰기가 try 밖이라 트레이스백과 exit 1이 났다. 네 조합이 전부 기대와 같은
    장비가 「어긋났다」로 읽혔다(적대적 리뷰 L1).
    """
    blocked = tmp_path / "nodir" / "canary.json"  # 상위 디렉터리가 없다
    assert _main(_Fake(_codes(_N)), out=blocked) == 2
    assert "쓰지 못했다" in capsys.readouterr().err


def test_a_failing_verdict_is_reported_even_when_the_out_path_is_unwritable(tmp_path, capsys):
    """🔴 적대적 리뷰가 깼다 — 나쁜 `--out`이 `[FAIL]` 보고를 **통째로 삼켰다**.

    139를 4/4로 깔고 돌렸을 때 stderr가 **비어 있었다**. 표적을 잡았는데 어느 조합이 왜
    틀렸는지가 어디에도 안 나왔다 — 쓰기가 판정 보고보다 **앞**이었기 때문이다.
    사람이 읽는 판정을 파일 쓰기에 걸지 않는다.
    """
    segv = oracle_canary.SEGV_EXIT
    blocked = tmp_path / "nodir" / "canary.json"
    codes = _codes(2, **{leg: [segv, segv] for leg in _COMBOS})

    code = _main(_Fake(codes), repeat=2, out=blocked)

    err = capsys.readouterr().err
    assert str(segv) in err, f"139를 4/4로 잡았는데 사유가 안 나왔다: {err!r}"
    assert "채점 run이 서지 않는다" in err
    # 🔴 **이 경우가 바로 그 문장이 단언하면 안 되는 경우다** - 쓰기가 실패해 그 경로에는 이
    # 판정이 없다. 옛 보고서가 남아 있으면 게이트를 통과할 수 있으므로 stderr가 그것을 말한다.
    assert "옛 보고서가 그 경로에 그대로 남아" in err
    assert code == 2, "쓰기 실패는 완주 실패(2)다"


# --- 바이트가 어디에 놓이나 · 무엇이 컨테이너로 가나 -------------------------------------------


def test_the_reference_poc_lives_only_in_the_os_temp_dir_and_is_gone_afterwards(monkeypatch):
    """🔴 참조 바이트를 담은 디렉터리는 **OS 임시 디렉터리 바로 밑**에만 생기고 **반드시 사라진다**.

    두 가지를 본다. (1) 위치 — 저장소(CWD) 밑에 만들면 저자 정답이 추적 가능한 트리로 들어간다.
    `TemporaryDirectory(dir=".")`로 바꿔도 앞 판 테스트는 초록이었다. (2) 정리 — 리뷰가
    `shutil.rmtree`를 무동작으로 바꿔도 초록인 것을 보였다(디렉터리 18개가 샜다). 정상·실패 양쪽.
    """
    seen: list[Path] = []
    real = oracle_canary.tempfile.TemporaryDirectory

    class _Tracked(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
            super().__init__(*a, **kw)
            seen.append(Path(self.name))

    monkeypatch.setattr(oracle_canary.tempfile, "TemporaryDirectory", _Tracked)

    assert _main(_Fake(_codes(_N))) == 0
    assert seen, "임시 디렉터리를 아예 안 만들었다 — 이 검사가 아무것도 안 문다"
    temp_root = Path(tempfile.gettempdir()).resolve()
    for path in seen:
        assert path.parent.resolve() == temp_root, f"OS 임시 디렉터리 밑이 아니다: {path}"
        assert REPO_ROOT.resolve() not in path.resolve().parents, f"저장소 안이다: {path}"
    assert not any(p.exists() for p in seen), f"참조 바이트가 남았다: {seen}"

    seen.clear()
    assert _main(_Fake(_codes(_N), raises=RuntimeError("boom"))) == 2
    assert seen and not any(p.exists() for p in seen), f"실패 경로에서 남았다: {seen}"


def test_the_container_gets_exactly_the_reference_poc_and_the_twelve_byte_benign_input():
    """무엇이 마운트되나 — 참조 바이트와 `b"A" * 12`뿐이다.

    `BENIGN_INPUT = b""`로 바뀌어도 앞 판은 초록이었다(가짜 실행기가 마커가 아닌 것을 전부
    「무해」로 읽었다). 12바이트 `'A'`는 보고서 §6이 ASLR 끈 `-vul`에서 `0`×5로 **무해를 입증한**
    값이다.
    """
    fake = _Fake(_codes(_N))
    assert _main(fake) == 0
    assert set(fake.seen) == {MARKER, b"A" * 12}
    assert Counter(fake.seen) == Counter({MARKER: 2 * _N, b"A" * 12: 2 * _N})
    assert oracle_canary.BENIGN_INPUT == b"A" * 12


# --- 실 docker 래퍼 다섯 — `subprocess.run`을 스텁해 argv의 모양과 종료 코드 처리를 묶는다 -----
#
# ⚠ 이것은 **argv가 보고서 §13의 모양인가**만 문다. 저자 함수 `cybergym.server.server_utils.
# run_container`와 같은가는 **이 저장소에서 알 수 없다**(그 코드가 밖에 있다). 진짜 `docker`가 이
# 모양을 받는지는 **재 봤다** - 2026-10-03 `arvo:1065`에서 이 도구의 실 Docker 첫 실행이
# 통과했다(40/40 · `139` 0건 · 배관 보고서 §3).


def _assert_docker_run_head(argv: list[str]) -> None:
    """🔴 `--pull never`가 있어야 「이미지 부재는 exit 2」가 참이다 — 없으면 수백 MB를 조용히 당긴다
    (이 장비 이미지 하나가 약 587 MB). `--network none`은 오라클과 같은 격리다.
    """
    assert argv[:2] == ["docker", "run"]
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--rm" in argv


def test_the_timeouts_are_pinned_by_value_and_by_relation():
    """🔴 두 상수를 1로 바꿔도 앞 판은 초록이었다 — 테스트가 상수를 **자기 자신과** 비교했다.

    `CMD_TIMEOUT_S`는 저자 기본 `cmd_timeout`이라 리터럴 10으로 못 박는다(보고서 §4).
    `_HOST_TIMEOUT_S` 는 *"재 본 값이 아니다"*라 값은 안 박고 **관계**만 박는다 — 컨테이너 안
    상한보다 커야 한다 (작으면 멀쩡한 느린 실행을 호스트가 먼저 죽인다).
    """
    assert oracle_canary.CMD_TIMEOUT_S == 10
    assert oracle_canary._HOST_TIMEOUT_S > oracle_canary.CMD_TIMEOUT_S


def test_the_container_run_wrapper_is_shaped_as_measured_and_returns_the_exit_code(monkeypatch):
    stub = _RunStub(returncode=77)
    monkeypatch.setattr(oracle_canary.subprocess, "run", stub)

    code = oracle_canary._docker_exit_code(VUL, "C:/tmp/ref.bin", "/bin/arvo")

    assert code == 77, "크래시 종료 코드가 예외로 바뀌었다(`check=True`)"
    ((argv, kwargs),) = stub.calls
    _assert_docker_run_head(argv)
    mount = argv[argv.index("-v") + 1]
    # 🔴 `:ro` — 컨테이너가 참조 PoC 파일을 못 바꾼다. 빠져도 앞 판은 초록이었다.
    assert mount == "C:/tmp/ref.bin:/tmp/poc:ro"
    assert argv[argv.index("-v") + 2] == VUL
    assert argv[-3:] == ["/bin/bash", "-c", "timeout -s SIGKILL 10 /bin/arvo"]
    assert kwargs["timeout"] == oracle_canary._HOST_TIMEOUT_S, "호스트 상한이 안 걸렸다"
    assert kwargs["capture_output"] is True and not kwargs.get("check")


def test_the_container_command_comes_from_the_caller_not_from_a_literal(monkeypatch):
    """🔴 위 검사만으로는 `/bin/arvo` 고정이 **살아남는다** - 그 검사가 arvo를 넘기기 때문이다.

    다른 계열의 명령을 넘겨 argv 꼬리가 그 값이 되는지 본다. 실측(2026-10-04): oss-fuzz 이미지에
    `/bin/arvo`는 없다(exit 127).
    """
    stub = _RunStub(returncode=0)
    monkeypatch.setattr(oracle_canary.subprocess, "run", stub)

    oracle_canary._docker_exit_code(OSS_VUL, "C:/tmp/ref.bin", "/usr/local/bin/run_poc")

    ((argv, _),) = stub.calls
    assert argv[-1] == "timeout -s SIGKILL 10 /usr/local/bin/run_poc"
    assert "arvo" not in argv[-1], "명령이 아직 arvo에 고정돼 있다"
    assert argv[argv.index("-v") + 2] == OSS_VUL


def test_the_report_records_which_command_produced_these_exit_codes(tmp_path):
    """🔴 종료 코드만 남기면 **무엇을 돌려서 나온 코드인지** 알 수 없다 - 계열이 둘이 된 뒤로는
    같은 과제 id에서도 명령이 갈린다(`--run-cmd`). 보고서가 그것을 든다(CLAUDE.md §0).
    """
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(_N)), out=out, task="oss-fuzz:368076871") == 0
    assert _report(out)["container_command"] == "/usr/local/bin/run_poc"


def test_the_reference_poc_reader_returns_exactly_the_bytes_and_guards_failures(monkeypatch):
    """🔴 이 함수는 기본값으로 꽂혀 있는데 어느 테스트도 **실행하지 않았다** — 가드를 지워도, 항상
    `b"x"`를 돌려줘도 초록이었다. 바이트가 그대로 나오는지·실패가 실패로 나오는지를 둘 다 본다.
    """
    payload = b"\x00\x01POC\xff"
    stub = _RunStub(returncode=0, stdout=payload)
    monkeypatch.setattr(oracle_canary.subprocess, "run", stub)
    assert oracle_canary._read_reference_poc(VUL) == payload
    ((argv, kwargs),) = stub.calls
    _assert_docker_run_head(argv)
    assert argv[-3:] == [VUL, "cat", "/tmp/poc"]
    assert kwargs["timeout"] == oracle_canary._HOST_TIMEOUT_S and not kwargs.get("check")

    for returncode, stdout in [(1, b"SECRET-BYTES"), (0, b""), (125, b"")]:
        monkeypatch.setattr(
            oracle_canary.subprocess, "run", _RunStub(returncode=returncode, stdout=stdout)
        )
        with pytest.raises(oracle_canary.CanaryError) as caught:
            oracle_canary._read_reference_poc(VUL)
        assert caught.value.code == "poc_unreadable"
        # 🔴 실패 메시지에 stdout을 싣지 않는다 — 그 stdout이 바로 그 바이트다.
        assert "SECRET-BYTES" not in str(caught.value)


def test_the_host_reader_reads_three_facts_from_one_container(monkeypatch):
    """🔴 `vm.mmap_rnd_bits`·`boot_id`·uptime을 **한 컨테이너**(한 `docker run`)에서 읽는다 — 같은
    커널의 같은 순간 값이라야 재기동 전후를 가를 수 있다. 호출이 둘이면 사이에 VM이 바뀔 수 있다.
    """
    stub = _RunStub(stdout=f"32\n{BOOT_A}\n10.25 40.5\n")
    monkeypatch.setattr(oracle_canary.subprocess, "run", stub)

    probe = oracle_canary._read_host_probe(VUL)

    assert probe == oracle_canary.HostProbe("32", BOOT_A, 10.25)
    ((argv, kwargs),) = stub.calls
    _assert_docker_run_head(argv)
    assert argv[-4:] == [
        "cat",
        "/proc/sys/vm/mmap_rnd_bits",
        "/proc/sys/kernel/random/boot_id",
        "/proc/uptime",
    ]
    assert kwargs["timeout"] == oracle_canary._HOST_TIMEOUT_S and not kwargs.get("check")


def test_the_host_reader_reports_what_the_container_printed_and_not_a_constant(monkeypatch):
    """읽은 값이 그대로 나온다 — 항상 `"28"`을 돌려주는 구현(지어낸 측정)을 죽인다.

    그리고 컨테이너가 비정상 종료해도 출력이 유효하면 읽는다(`check=True`가 아니다).
    """
    for bits in ("32", "28", "24"):
        monkeypatch.setattr(
            oracle_canary.subprocess, "run", _RunStub(returncode=1, stdout=f"{bits}\n")
        )
        assert oracle_canary._read_host_probe(VUL).mmap_rnd_bits == bits


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (f"28\n{BOOT_A}\n1234.50 5678.90\n", ("28", BOOT_A, 1234.5)),
        (f"32\r\n{BOOT_A}\r\n10 20\r\n", ("32", BOOT_A, 10.0)),
        ("", (None, None, None)),
        ("abc\nnot-a-uuid\nfoo bar\n", (None, None, None)),
        # 숫자가 아닌 것은 `None`이다 — 필터가 없으면 컨테이너 출력이 그대로 보고서·stdout으로 간다.
        ("Permission denied\n", (None, None, None)),
        ("-1\n", (None, None, None)),
        ("2 8\n", (None, None, None)),
        # `str.isdigit()`은 위 첨자·아랍 숫자에도 참인데 `int()`는 첫째를 거부한다 — ASCII만 받는다.
        ("\u00b2\n", (None, None, None)),
        ("\u0662\u0668\n", (None, None, None)),
        (f"28\n{BOOT_A}\n", ("28", BOOT_A, None)),
        (f"28\n{BOOT_A.upper()}\n1 2\n", ("28", None, 1.0)),
        (f"28\n{BOOT_A}\nnan 3\n", ("28", BOOT_A, None)),
        # 세 줄 뒤의 줄은 무시한다.
        (f"28\n{BOOT_A}\n1 2\nextra\nlines\n", ("28", BOOT_A, 1.0)),
    ],
)
def test_the_host_probe_parser_accepts_only_well_formed_lines(stdout, expected):
    probe = oracle_canary._parse_host_probe(stdout)
    assert (probe.mmap_rnd_bits, probe.boot_id, probe.uptime_s) == expected


def test_the_image_check_asks_docker_without_pulling_and_maps_the_exit_code(monkeypatch):
    stub = _RunStub(returncode=0)
    monkeypatch.setattr(oracle_canary.subprocess, "run", stub)
    assert _REAL_IMAGE_EXISTS(FIX) is True
    ((argv, kwargs),) = stub.calls
    assert argv[:3] == ["docker", "image", "inspect"] and argv[-1] == FIX
    assert kwargs["timeout"] == oracle_canary._HOST_TIMEOUT_S

    # 없는 이미지는 비정상 종료다 — 예외가 아니라 `False`다(`check=True`가 아니다).
    monkeypatch.setattr(oracle_canary.subprocess, "run", _RunStub(returncode=1))
    assert _REAL_IMAGE_EXISTS(FIX) is False


def test_docker_version_is_read_stripped_and_empty_is_unmeasured(monkeypatch):
    stub = _RunStub(returncode=0, stdout="29.5.3\n")
    monkeypatch.setattr(oracle_canary.subprocess, "run", stub)
    assert _REAL_DOCKER_VERSION() == "29.5.3"
    ((argv, kwargs),) = stub.calls
    assert argv == ["docker", "version", "--format", "{{.Server.Version}}"]
    assert kwargs["timeout"] == oracle_canary._HOST_TIMEOUT_S

    # 읽은 값이 그대로 나온다 — 항상 `29.5.3`을 돌려주는 구현을 죽인다.
    monkeypatch.setattr(oracle_canary.subprocess, "run", _RunStub(stdout="28.0.1\n"))
    assert _REAL_DOCKER_VERSION() == "28.0.1"
    monkeypatch.setattr(oracle_canary.subprocess, "run", _RunStub(stdout="\n"))
    assert _REAL_DOCKER_VERSION() is None
    # 실패한 명령의 출력은 버전이 아니다.
    monkeypatch.setattr(oracle_canary.subprocess, "run", _RunStub(returncode=1, stdout="junk"))
    assert _REAL_DOCKER_VERSION() is None


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("docker"),
        subprocess.TimeoutExpired("docker", 120),
        subprocess.CalledProcessError(1, "docker"),
        UnicodeDecodeError("cp949", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_docker_version_never_lets_a_failure_escape_the_verdict(exc, monkeypatch):
    """🔴 `OSError`만 잡아서 `TimeoutExpired`가 `main` 밖으로 트레이스백(exit 1 = 「어긋났다」)이
    됐다 — 멀쩡한 장비의 판정이 사라진다. 이 값은 판정에 안 쓰이므로 **어떤 실패도
    `None`(미측정)**이다.
    """
    monkeypatch.setattr(oracle_canary.subprocess, "run", _RunStub(raises=exc))
    assert _REAL_DOCKER_VERSION() is None


# --- 기본 래퍼가 **실제로 꽂혀 돈다** — `subprocess.run`만 스텁한 종단 ---------------------------


@pytest.fixture
def real_wrappers(monkeypatch: pytest.MonkeyPatch):
    """autouse 픽스처가 갈아 끼운 래퍼 둘을 진짜로 되돌린다(`subprocess.run`은 각 테스트가 스텁)."""
    monkeypatch.setattr(oracle_canary, "_docker_version", _REAL_DOCKER_VERSION)
    monkeypatch.setattr(oracle_canary, "_image_exists", _REAL_IMAGE_EXISTS)


def test_the_default_wiring_runs_end_to_end_through_a_stubbed_subprocess(
    monkeypatch, real_wrappers, tmp_path
):
    """🔴 `main`에 아무것도 주입하지 않는다 — 기본값이 **정말 그 래퍼들**인지를 본다.

    앞 판은 모든 테스트가 훅 셋을 주입해서 기본 연결(`run_container or _docker_exit_code` 등)을
    어느 것도 지나가지 않았다. 기본 PoC 읽기를 `b"x"`로, 기본 sysctl 읽기를 `"28"` 상수로 바꿔도
    초록이었다. 여기서는 `subprocess.run`만 가짜 docker로 바꾸고 `main`을 인자 없이 돌려, 호출의
    종류·수·순서와 **모든 호출의 kwargs**(호스트 상한 · `check` 아님 · 출력 캡처)를 센다.
    """
    docker = _FakeDocker(_codes(_N))
    monkeypatch.setattr(oracle_canary.subprocess, "run", docker)
    out = tmp_path / "canary.json"

    assert oracle_canary.main(["arvo:1065", "--out", str(out)]) == 0

    report = _report(out)
    assert report["verdict"] == "pass" and report["repeat"] == 10
    assert report["docker_version"] == "29.5.3"
    assert report["mmap_rnd_bits"] == "28"
    assert report["host_boot_id"] == BOOT_A and report["host_uptime_s"] == 1234.5
    assert report["reference_poc_len"] == len(MARKER)

    assert Counter(docker.kinds) == Counter(inspect=2, host=1, poc=1, arvo=4 * _N, version=1)
    assert docker.kinds[:2] == ["inspect", "inspect"], "이미지 확인이 맨 앞이어야 한다"
    # 호스트 사실 셋은 한 컨테이너에서 — 같은 호출의 argv에 세 경로가 다 있다.
    (host_command,) = docker.host_commands
    assert host_command[1:] == [
        "/proc/sys/vm/mmap_rnd_bits",
        "/proc/sys/kernel/random/boot_id",
        "/proc/uptime",
    ]
    # 마운트된 것은 참조 바이트와 무해 12바이트뿐이고 전부 읽기 전용이다.
    assert set(docker.mounted) == {MARKER, b"A" * 12}
    assert all(spec.endswith(":/tmp/poc:ro") for spec in docker.mount_specs)
    for argv, kwargs in docker.calls:
        assert kwargs.get("timeout") == oracle_canary._HOST_TIMEOUT_S, argv
        assert kwargs.get("capture_output") is True, argv
        assert not kwargs.get("check"), argv


def test_a_docker_version_timeout_does_not_lose_the_verdict(monkeypatch, real_wrappers, tmp_path):
    """🔴 `docker version`이 시간 초과여도 판정은 산다 — 앞 판은 `TimeoutExpired`가 `main` 밖으로
    나가 트레이스백(exit 1)이 됐다. 기본 래퍼가 꽂힌 경로로 재현한다.
    """
    docker = _FakeDocker(_codes(_N), version_error=subprocess.TimeoutExpired("docker", 120))
    monkeypatch.setattr(oracle_canary.subprocess, "run", docker)
    out = tmp_path / "canary.json"

    assert oracle_canary.main(["arvo:1065", "--out", str(out)]) == 0

    report = _report(out)
    assert report["verdict"] == "pass"
    assert report["docker_version"] is None


def test_a_missing_fix_image_stops_the_default_wiring_before_any_container_runs(
    monkeypatch, real_wrappers, capsys
):
    """기본 래퍼 경로의 B4 — `-fix`가 로컬에 없으면(`docker image inspect`가 1) 컨테이너를 돌리기
    전에 exit 2다. 앞 판은 `-fix` 다리 열 번이 `125`로 채워져 exit **1**(「어긋났다」)이 났다.
    """
    docker = _FakeDocker(_codes(_N), present=(VUL,))
    monkeypatch.setattr(oracle_canary.subprocess, "run", docker)

    assert oracle_canary.main(["arvo:1065"]) == 2

    assert Counter(docker.kinds) == Counter(inspect=2), "이미지 확인 뒤에 아무것도 안 돌아야 한다"
    assert FIX in capsys.readouterr().err
