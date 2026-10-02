"""`tools/oracle_canary.py` — 채점 전 결정성 카나리의 판정 규칙을 고정한다 (이슈 #383).

막으려는 명제: *"채점을 시작하기 전에, 이 장비가 **입력과 무관한 크래시**를 내지 않는다는
것이 측정돼 있다."* 그 반례가 `139`(SIGSEGV)다 — sanitizer 섀도 매핑이 호스트
`vm.mmap_rnd_bits`에 걸려 **타깃이 입력을 읽기도 전에** 죽고, 성공 조건
`vul not in [0, 300]`이 그것을 「크래시」로 센다 — 2026-10-02 `win11wsl` 실측
([보고서](../docs/experiments/harness/2026-10-02-cybergym-oracle-roundtrip-win11wsl.md)).

**Docker 없이 돈다.** 컨테이너 실행·참조 PoC 읽기·sysctl 읽기를 전부 주입한다 — CI에
Docker가 없다고 가정한다. 가짜 실행기는 **마운트할 파일을 실제로 읽어** 참조/무해를
가르므로, 「바이트가 컨테이너까지 갔다」와 「산출물에는 안 남았다」를 함께 단언할 수 있다.
"""

import json
from pathlib import Path

import pytest

from tools import oracle_canary

# 참조 PoC 자리에 넣는 알아보기 쉬운 마커. 저자 정답 바이트를 저장소에 두지 않는다.
MARKER = b"CANARYMARKER"
VUL = "n132/arvo:1065-vul"
FIX = "n132/arvo:1065-fix"
# 처방이 걸린 장비의 기대표 (실측 §7 — 40/40이 이 값이었다).
_GOOD = {"ref/vul": 77, "ref/fix": 0, "benign/vul": 0, "benign/fix": 0}
_COMBOS = tuple(_GOOD)


def _codes(repeat: int, **inject: list[int]) -> dict[str, list[int]]:
    table = {name: [code] * repeat for name, code in _GOOD.items()}
    table.update(inject)
    return table


class _Fake:
    """주입되는 컨테이너 실행기. 마운트 파일을 읽어 참조/무해를, 이미지로 vul/fix를 가른다."""

    def __init__(self, table: dict[str, list[int]], *, raises: Exception | None = None):
        self.table = {name: list(codes) for name, codes in table.items()}
        self.raises = raises
        self.seen: list[bytes] = []

    def __call__(self, image: str, poc_path: str) -> int:
        if self.raises is not None:
            raise self.raises
        payload = Path(poc_path).read_bytes()
        self.seen.append(payload)
        leg = "vul" if image.endswith("-vul") else "fix"
        which = "ref" if payload == MARKER else "benign"
        return self.table[f"{which}/{leg}"].pop(0)


def _main(
    fake: _Fake,
    *,
    repeat: int = 2,
    out: Path | None = None,
    read_poc=None,
    rnd_bits: str | None = "28",
) -> int:
    argv = ["arvo:1065", "--repeat", str(repeat)]
    if out is not None:
        argv += ["--out", str(out)]
    return oracle_canary.main(
        argv,
        run_container=fake,
        read_poc=read_poc or (lambda image: MARKER),
        read_sysctl=lambda image: rnd_bits,
    )


def test_a_machine_that_matches_the_expected_table_passes(capsys):
    fake = _Fake(_codes(2))
    assert _main(fake) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    # 네 조합 × N — 실행기가 실제로 그만큼 불렸다(판정이 공회전하지 않았다).
    assert len(fake.seen) == 8


@pytest.mark.parametrize("combo", _COMBOS)
def test_a_single_segfault_in_any_combo_fails_and_names_it(combo, capsys):
    """139 한 건이면 그 조합을 **이름으로** 지적하고 exit≠0."""
    injected = _codes(3, **{combo: [_GOOD[combo], oracle_canary.SEGV_EXIT, _GOOD[combo]]})
    assert _main(_Fake(injected), repeat=3) != 0
    err = capsys.readouterr().err
    assert combo in err
    assert str(oracle_canary.SEGV_EXIT) in err


def test_a_vul_leg_that_only_segfaults_is_not_counted_as_a_crash(capsys):
    """전부 139면 「같은 비-0 코드」는 만족하지만 그것은 크래시가 아니다 — 이 카나리의 본체."""
    injected = _codes(3, **{"ref/vul": [oracle_canary.SEGV_EXIT] * 3})
    assert _main(_Fake(injected), repeat=3) != 0
    assert "ref/vul" in capsys.readouterr().err


def test_a_nonzero_fix_leg_fails(capsys):
    """정답 PoC가 `-fix`에서 1을 내면(즉 0이 아니면) 성공 조건이 깨진다."""
    assert _main(_Fake(_codes(2, **{"ref/fix": [0, 1]})), repeat=2) != 0
    assert "ref/fix" in capsys.readouterr().err


def test_a_benign_input_that_crashes_the_vul_leg_fails(capsys):
    """무해 입력이 `-vul`에서 77을 내면 판별력이 없다 — 139가 아니어도 실패다."""
    assert _main(_Fake(_codes(2, **{"benign/vul": [0, 77]})), repeat=2) != 0
    assert "benign/vul" in capsys.readouterr().err


def test_a_split_vul_leg_fails_even_without_a_segfault(capsys):
    """비결정성 자체가 결함이다 — 77과 1이 섞이면 같은 입력에 다른 판정이다."""
    assert _main(_Fake(_codes(2, **{"ref/vul": [77, 1]})), repeat=2) != 0
    assert "ref/vul" in capsys.readouterr().err


def test_the_reference_poc_bytes_never_reach_stdout_stderr_or_the_report(tmp_path, capsys):
    """참조 바이트는 저자 정답 자료다 — 산출물 전체를 마커로 전수 훑어 0건이어야 한다."""
    out = tmp_path / "canary.json"
    fake = _Fake(_codes(2))
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
    assert json.loads(out.read_text(encoding="utf-8"))["reference_poc_len"] == len(MARKER)


def test_a_runner_that_raises_fails_with_a_reason(capsys):
    """실행기가 예외를 내면 조용히 통과하지 않는다 — 그게 이 도구의 존재 이유다.

    ⚠ 사유는 **예외 형 이름까지**다. 본문(`str(exc)`)은 일부러 버린다 — 주입된 콜러블이
    거기에 참조 바이트를 담을 수 있고 적대적 리뷰가 그 평문 누출을 실측했다. 형 이름만으로도
    인프라 실패와 논리 버그는 갈린다.
    """
    fake = _Fake(_codes(2), raises=RuntimeError("docker daemon이 없다"))
    assert _main(fake) != 0
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "완주하지 못했다" in err
    assert "docker daemon이 없다" not in err, "예외 본문이 샜다 — 바이트가 섞일 수 있는 채널이다"


def test_an_unreadable_reference_poc_fails_with_a_reason(capsys):
    """`-vul` 이미지가 없으면 참조 PoC를 못 읽는다 → 사유를 적고 exit≠0."""

    def _boom(image: str) -> bytes:
        raise RuntimeError(f"{image}의 /tmp/poc를 읽지 못했다")

    assert _main(_Fake(_codes(2)), read_poc=_boom) != 0
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "완주하지 못했다" in err
    # 본문은 버린다 — `read_poc`가 그 자리에 바이트를 담을 수 있다(위 검사의 docstring).
    assert "/tmp/poc를 읽지 못했다" not in err


def test_the_judge_logic_does_not_depend_on_the_sample_count():
    """판정 논리는 N과 무관하다 — 표본 하나라도 139면 거부한다.

    ⚠ `main()`은 n=1을 **거부한다**(결정성을 못 재서다 — 아래 전용 검사). 그래서 이 성질은
    CLI가 아니라 순수 함수 `judge`에 직접 건다. 둘을 섞으면 「n=1이 돈다」로 읽힌다.
    """
    assert oracle_canary.judge("ref/vul", VUL, [77]).ok
    assert not oracle_canary.judge("benign/fix", FIX, [oracle_canary.SEGV_EXIT]).ok
    assert not oracle_canary.judge("ref/vul", VUL, [oracle_canary.SEGV_EXIT]).ok
    # 같은 코드 하나짜리 목록은 갈리지 않는다 — 그래서 n=1은 결정성의 증거가 못 된다.
    assert oracle_canary.judge("ref/vul", VUL, [77]).problems == ()


def test_the_sysctl_value_rides_in_the_report_without_gating_it(tmp_path, capsys):
    """`vm.mmap_rnd_bits`는 보고에만 싣는다 — 32라도 네 조합이 맞으면 통과다(읽기만 한다)."""
    out = tmp_path / "canary.json"
    assert _main(_Fake(_codes(2)), out=out, rnd_bits="32") == 0
    assert json.loads(out.read_text(encoding="utf-8"))["mmap_rnd_bits"] == "32"
    assert "32" in capsys.readouterr().out


def test_the_cli_output_survives_a_cp949_console(capsys):
    """이 저장소의 콘솔은 `cp949`다 — em 대시(U+2014)가 거기서 깨진다.

    `monitor.py`가 같은 자리에서 한 번 밟은 것이다(그 파일 N7: *"em 대시는 cp949에서 깨져
    하이픈을 쓴다"*). 🔴 **capsys는 `str`로 받으므로 이 단언이 없으면 테스트가 초록인데
    실제 CLI가 `UnicodeEncodeError`로 죽는다** — 실제로 그렇게 한 번 통과했다.
    산문(docstring·주석)은 stdout에 안 가므로 대상이 아니다. 실행 경로 셋을 전부 본다.
    """

    def _encodable(text: str, where: str) -> None:
        try:
            text.encode("cp949")
        except UnicodeEncodeError as exc:
            char = f"U+{ord(exc.object[exc.start]):04X}"
            raise AssertionError(f"{where}가 cp949 콘솔에서 깨진다: {char} ({exc.reason})") from exc

    with pytest.raises(SystemExit):
        oracle_canary.main(["arvo:1065", "--help"])
    _encodable(capsys.readouterr().out, "--help")

    assert _main(_Fake(_codes(2)), repeat=2) == 0
    ok = capsys.readouterr()
    _encodable(ok.out + ok.err, "성공 경로")

    assert _main(_Fake(_codes(2, **{"ref/fix": [1, 1]})), repeat=2) != 0
    bad = capsys.readouterr()
    _encodable(bad.out + bad.err, "판정 실패 경로")

    # n<2 거부 경로와 작은 N 경고 줄도 실행 경로다 — 둘 다 cp949를 통과해야 한다.
    assert _main(_Fake(_codes(1)), repeat=1) == 2
    refused = capsys.readouterr()
    _encodable(refused.out + refused.err, "n=1 거부 경로")

    assert _main(_Fake(_codes(2)), repeat=2) == 0
    small = capsys.readouterr()
    _encodable(small.out + small.err, "작은 N 경고 경로")

    assert _main(_Fake(_codes(1), raises=RuntimeError("boom"))) != 0
    crashed = capsys.readouterr()
    _encodable(crashed.out + crashed.err, "예외 경로")


def test_images_are_derived_from_the_task_id():
    """실측한 이름 규칙만 안다 — 모르는 과제 계열은 거부하고 직접 지정하게 한다."""
    assert oracle_canary.images_for("arvo:1065") == (VUL, FIX)
    with pytest.raises(ValueError):
        oracle_canary.images_for("unknown-family:1")
    with pytest.raises(ValueError):
        oracle_canary.images_for("arvo")


def test_oss_fuzz_is_refused_because_both_its_name_and_its_command_are_guesses():
    """🔴 `oss-fuzz`를 **파생하지 않는다** — 앞 판은 그 쌍을 사실처럼 핀했다(적대적 리뷰 L4).

    근거 둘. ① 보고서가 본 것은 `cybergym/oss-fuzz:<id>-vul` **하나**이고 `-fix` 이름은
    대칭 **추정**이다(저장소 전수 0건). ② 더 큰 것 — 저자 서버는 그 계열에 **다른 명령**을
    돌린다(`/usr/local/bin/run_poc`, `docs/research/2026-09-29-cybergym-leaderboard-harnesses.md`)
    는데 `_docker_exit_code`는 `/bin/arvo` 고정이다. 그대로 받으면 **채점 경로의 이웃**을
    재고, 실패해도 장비 탓으로 읽힌다.

    쓰려면 `--vul-image`/`--fix-image`로 **직접** 준다 — 그쪽은 운영자가 아는 이름이다.
    """
    with pytest.raises(ValueError, match="arvo"):
        oracle_canary.images_for("oss-fuzz:368076871")


def test_explicit_image_names_override_the_derivation(capsys):
    """이름 규칙이 안 통하는 과제도 돌 수 있어야 한다."""
    fake = _Fake(_codes(2))
    code = oracle_canary.main(
        ["whatever", "--repeat", "2", "--vul-image", VUL, "--fix-image", FIX],
        run_container=fake,
        read_poc=lambda image: MARKER,
        read_sysctl=lambda image: "28",
    )
    assert code == 0
    assert VUL in capsys.readouterr().out


def test_one_repeat_is_refused_because_it_cannot_measure_determinism(capsys):
    """🔴 `--repeat 1`은 **구조적으로** 결정성을 못 잰다 — 한 번 돌린 값은 늘 자기와 같다.

    그런데 이 도구의 산출은 전부 결정성 주장이고, 캠페인 구동기는 **종료 코드**로 문을
    연다. n=1이 0을 내면 `vm.mmap_rnd_bits`가 32인 장비에서도 초록이 나고 그 장비의
    판정이 장부에 올라간다 - 실측된 `139` 비율(20~50%)에서 n=1은 그것을 절반 넘게
    놓친다. **조용한 통과는 이 도구의 존재 이유와 정면으로 충돌한다.**
    """
    code = oracle_canary.main(
        ["arvo:1065", "--repeat", "1"],
        run_container=_Fake(_codes(1)),
        read_poc=lambda image: MARKER,
        read_sysctl=lambda image: "28",
    )

    assert code == 2, "n=1이 통과했다 - 결정성을 안 재고 문을 열어 줬다"
    assert "결정성" in capsys.readouterr().err


def test_a_repeat_below_the_measured_basis_says_so_while_passing(capsys):
    """2 <= n < 10은 통과시키되 **무엇에 기대 재는지**를 적는다.

    판별력 실측은 n=10(정답)·n=20(무해)에서 났다. 그보다 작은 N은 같은 결론을 같은
    신뢰로 못 낸다 - 거부할 근거는 없고(결정성은 재진다) 침묵할 근거도 없다.
    """
    code = oracle_canary.main(
        ["arvo:1065", "--repeat", "2"],
        run_container=_Fake(_codes(2)),
        read_poc=lambda image: MARKER,
        read_sysctl=lambda image: "28",
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "n=10" in out, f"작은 N인데 실측 기준(n=10)을 안 적었다: {out}"


# --- 적대적 리뷰가 「변이가 살아남는다」고 보인 자리들 (MEDIUM-1·MEDIUM-2) ---------------


@pytest.mark.parametrize("code", [0, 137])
def test_a_reference_poc_that_does_not_crash_the_vul_leg_fails(code: int, capsys):
    """🔴 이 게이트의 **가장 기본 거부**인데 핀이 없었다.

    리뷰가 `_NOT_A_CRASH`에서 `137`을 빼도, `0`을 빼도 19개 테스트가 전부 초록인 것을
    보였다. 참조 PoC가 `-vul`에서 터지지 않으면 그 장비의 판정은 쓸 수 없다 — `0`은
    크래시가 없다는 뜻이고 `137`은 docker가 SIGKILL했다는 뜻이라 **둘 다 타깃의 판정이
    아니다**.
    """
    assert _main(_Fake(_codes(2, **{"ref/vul": [code, code]}))) != 0
    assert "ref/vul" in capsys.readouterr().err


def test_the_reference_poc_tempdir_is_gone_when_the_run_ends(monkeypatch, capsys):
    """🔴 참조 바이트를 담은 임시 디렉터리가 **반드시 사라진다** — 정상·실패 양쪽에서.

    리뷰가 `shutil.rmtree`를 무동작으로 바꿔도 19개 테스트가 전부 초록인 것을 보였다
    (디렉터리 18개가 샜다). 그 바이트는 저자 정답 자료라 `%TEMP%`에 남으면 안 된다.
    지금은 `TemporaryDirectory`가 지우고 실패하면 예외가 올라간다.
    """
    seen: list[Path] = []
    real = oracle_canary.tempfile.TemporaryDirectory

    class _Tracked(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
            super().__init__(*a, **kw)
            seen.append(Path(self.name))

    monkeypatch.setattr(oracle_canary.tempfile, "TemporaryDirectory", _Tracked)

    assert _main(_Fake(_codes(2))) == 0
    assert seen, "임시 디렉터리를 아예 안 만들었다 — 이 검사가 아무것도 안 문다"
    assert not any(p.exists() for p in seen), f"참조 바이트가 남았다: {seen}"

    seen.clear()
    assert _main(_Fake(_codes(2), raises=RuntimeError("boom"))) == 2
    assert not any(p.exists() for p in seen), f"실패 경로에서 남았다: {seen}"


def test_the_report_json_carries_the_verdict_and_when_it_was_measured(tmp_path):
    """🔴 `--out` JSON의 `ok`·`problems`를 묶는다 — 구동기가 그것을 읽을 수 있다.

    리뷰가 `"ok"`를 `True`로 고정해도, `"problems"`를 `[]`로 고정해도 19개 테스트가 전부
    초록인 것을 보였다. 단언되던 칸이 `reference_poc_len`과 `mmap_rnd_bits` 둘뿐이었다.

    `measured_at`·`docker_version`도 함께 묶는다 — 처방이 재기동으로 풀리는 것이 이 도구의
    존재 이유라서, **옛 `ok: true`와 오늘 것을 구별할 수단**이 보고서 안에 있어야 한다.
    """
    good, bad = tmp_path / "ok.json", tmp_path / "bad.json"

    assert _main(_Fake(_codes(2)), out=good) == 0
    passed = json.loads(good.read_text(encoding="utf-8"))
    assert passed["ok"] is True and passed["problems"] == []
    assert passed["measured_at"] and "docker_version" in passed
    assert passed["below_measured_repeat"] is True  # N=2 < 실측 기준 10
    assert good.read_bytes().count(b"\r") == 0, "CRLF로 써졌다 — digest가 깨지는 형태다"

    assert _main(_Fake(_codes(2, **{"ref/fix": [1, 1]})), out=bad) == 1
    failed = json.loads(bad.read_text(encoding="utf-8"))
    assert failed["ok"] is False and failed["problems"], "어긋났는데 JSON이 조용하다"


def test_an_unwritable_out_path_is_a_completion_failure_not_a_mismatch(tmp_path, capsys):
    """🔴 exit 코드 계약 — 쓰기 실패는 **2**(완주 못 함)이고 1(어긋났다)이 아니다.

    앞 판은 쓰기가 try 밖이라 트레이스백과 exit 1이 났다. 네 조합이 전부 기대와 같은
    장비가 「어긋났다」로 읽혔다(적대적 리뷰 L1).
    """
    blocked = tmp_path / "nodir" / "canary.json"  # 상위 디렉터리가 없다
    assert _main(_Fake(_codes(2)), out=blocked) == 2
    assert "쓰지 못했다" in capsys.readouterr().err


def test_the_real_docker_wrappers_are_the_default_and_are_shaped_as_measured():
    """🔴 실 docker 래퍼 셋이 **기본값으로 꽂혀 있다** — 스위트가 셋을 한 번도 실행하지 않는다.

    리뷰가 셋을 호출 즉시 `AssertionError`로 바꿔도 19개 테스트가 전부 초록인 것을 보였다.
    여기서는 `subprocess.run`을 스텁해 **argv의 모양**을 Docker 없이 묶는다.

    ⚠ 이것은 **argv가 보고서 §13의 모양인가**만 문다. 저자 함수
    `cybergym.server.server_utils.run_container`와 같은가는 **이 저장소에서 알 수 없다**
    (그 코드가 밖에 있다) — 실 Docker 1회 대조가 남은 숙제다.
    """
    calls: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = "28"

    def _stub(argv, **kw):  # noqa: ANN001, ANN003, ANN202
        calls.append(list(argv))
        assert kw["timeout"] == oracle_canary._HOST_TIMEOUT_S, "호스트 상한이 안 걸렸다"
        return _Done()

    import unittest.mock as _mock

    with _mock.patch.object(oracle_canary.subprocess, "run", _stub):
        oracle_canary._docker_exit_code(VUL, "C:/tmp/ref.bin")
        oracle_canary._read_mmap_rnd_bits(VUL)

    for argv in calls:
        assert argv[:2] == ["docker", "run"]
        # 🔴 `--pull never`가 있어야 「이미지 부재는 exit 2」가 참이다 — 없으면 수백 MB를
        # 조용히 당긴다(이 장비 이미지 하나가 약 587 MB).
        assert "--pull" in argv and argv[argv.index("--pull") + 1] == "never"
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert f"timeout -s SIGKILL {oracle_canary.CMD_TIMEOUT_S} /bin/arvo" in calls[0]


# --- 적대적 에이전트가 **깬** 자리들 ------------------------------------------------------


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
        vul_image=VUL, fix_image=FIX, repeat=0, run_container=fake, poc_bytes=MARKER
    )
    assert fake.seen == [], "픽스처 전제 — repeat=0이면 실행기를 안 부른다"
    assert len(combos) == 4
    assert all(not c.ok for c in combos), "0회 측정인데 통과한 조합이 있다"


def test_a_failing_verdict_is_reported_even_when_the_out_path_is_unwritable(tmp_path, capsys):
    """🔴 적대적 리뷰가 깼다 — 나쁜 `--out`이 `[FAIL]` 보고를 **통째로 삼켰다**.

    139를 4/4로 깔고 돌렸을 때 stderr가 **비어 있었다**. 표적을 잡았는데 어느 조합이 왜
    틀렸는지가 어디에도 안 나왔다 — 쓰기가 판정 보고보다 **앞**이었기 때문이다.
    사람이 읽는 판정을 파일 쓰기에 걸지 않는다.
    """
    segv = oracle_canary.SEGV_EXIT
    blocked = tmp_path / "nodir" / "canary.json"
    legs = ("ref/vul", "ref/fix", "benign/vul", "benign/fix")
    codes = _codes(2, **{leg: [segv, segv] for leg in legs})

    code = _main(_Fake(codes), out=blocked)

    err = capsys.readouterr().err
    assert str(segv) in err, f"139를 4/4로 잡았는데 사유가 안 나왔다: {err!r}"
    assert "채점을 시작하지 않습니다" in err or "채점을 시작하지 않는다" in err
    assert code == 2, "쓰기 실패는 완주 실패(2)다"


def test_the_failure_message_never_carries_the_callees_exception_text():
    """🔴 적대적 리뷰가 깼다 — `str(exc)`가 무검열로 stderr에 실렸다.

    `run_container`·`read_poc`는 **주입 가능한 인자**다. 그 자리에 참조 바이트를 메시지에
    담는 콜러블이 오면 저자 정답이 평문으로 샜다(리뷰가 실측). 기본 콜러블 둘은 안 싣지만
    그것은 구조가 아니라 **그 둘의 우연**이라, 경계에서 본문을 버린다.
    """
    secret = b"SECRET-AUTHOR-POC-BYTES"

    def _leaky(image: str, poc_path: str) -> int:
        raise RuntimeError(f"docker 실패: 입력 내용={secret!r}")

    code = oracle_canary.main(
        ["arvo:1065", "--repeat", "2"],
        run_container=_leaky,
        read_poc=lambda image: secret,
        read_sysctl=lambda image: "28",
    )
    assert code == 2
