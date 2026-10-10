"""카나리 보고서 게이트 — 채점 run 앞에서 무엇을 거부하나 (이슈 #383).

`tools/oracle_canary.py`가 쓴 JSON을 읽어 **이 run이 채점기에 제출해도 되는가**를 가른다.
도구 자체(네 조합·종료 코드 판정)는 `tests/test_oracle_canary.py`가 물고, 여기는 **그 산출물을
읽는 쪽**이다. CLI 배선(`--oracle-canary` → `_oracle_for`의 거절)은 `tests/test_oracle_cli.py`가
본다.

🔴 **왜 `verdict == "pass"`만으로 안 되나.** 도구는 `vm.mmap_rnd_bits`를 **경고로만** 다룬다 —
*"네 조합이 기대와 같으면 값이 32여도 `pass`다"*(그 모듈 docstring). 채점 run에는 그 값이
치명적이다: 32에서 판별력이 정답 ≈0.40 · 오답 ≈0.28로 **차가 ≈0.12**다(이슈 #383의 실측).
그래서 이 게이트는 `pass`에 더해 엔트로피 칸을 **따로** 본다.
"""

import hashlib
import json
from pathlib import Path

import pytest

from src.adapters.canary_report import (
    _MAX_REPORT_BYTES,
    MEASURED_REPEAT,
    CanaryReport,
    CanaryReportError,
    canary_refusal,
    read_canary_report,
)

_TASK = "arvo:1065"

# 통과하는 보고서의 모양. **합성값이다** - `tools/oracle_canary.py`가 쓰는 키와 같은 이름을 쓰되
# 값은 여기서 지었다. 참조 PoC 바이트는 보고서에 없고 이 모듈도 만들지 않는다.
_PASSING = {
    "task": _TASK,
    "verdict": "pass",
    "ok": True,
    "repeat": 10,
    "below_measured_repeat": False,
    "measured_repeat": 10,
    "mmap_rnd_bits": "28",
    "rnd_bits_above_28": False,
    "rnd_bits_unmeasured": False,
    "measured_at": "2026-10-03T17:42:07+00:00",
    "docker_version": "29.5.3",
    # 같은 파일의 **반증 칸**. 도구가 늘 쓰는 셋이고(그 모듈 `_report`), 게이트가 요약만 믿지
    # 않으려면 이것도 봐야 한다(§2.2 「요약이 원문보다 세면 안 된다」).
    "completed": True,
    "segv_count": 0,
    "problems": [],
}


def _write(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "canary.json"
    path.write_text(json.dumps({**_PASSING, **overrides}), encoding="utf-8")
    return path


# --- 통과 ------------------------------------------------------------------------


def test_a_passing_report_for_this_task_is_not_refused(tmp_path: Path) -> None:
    report = read_canary_report(_write(tmp_path))

    assert canary_refusal(report, task_id=_TASK) is None


def test_the_report_keeps_the_fields_the_refusal_message_needs(tmp_path: Path) -> None:
    """거절 문구가 「언제·어느 장비에서 잰 것인가」를 들어야 한다 - 안 들면 조작자가 못 고친다."""
    report = read_canary_report(_write(tmp_path))

    assert (report.task, report.verdict, report.mmap_rnd_bits) == (_TASK, "pass", "28")
    assert (report.measured_at, report.docker_version) == ("2026-10-03T17:42:07+00:00", "29.5.3")


# --- 거절 ------------------------------------------------------------------------


def test_a_report_for_another_task_is_refused(tmp_path: Path) -> None:
    """다른 과제의 카나리로 이 과제를 채점하지 않는다 - 계열마다 이미지와 기대 코드가 다르다."""
    report = read_canary_report(_write(tmp_path, task="oss-fuzz:368076871"))

    refusal = canary_refusal(report, task_id=_TASK)

    assert refusal is not None and "oss-fuzz:368076871" in refusal


@pytest.mark.parametrize("verdict", ["fail", "incomplete", "weak_pass"])
def test_every_verdict_but_pass_is_refused(tmp_path: Path, verdict: str) -> None:
    """`weak_pass`도 거절한다 - 도구가 `ok=false`로 내는 값이고 관측 횟수가 모자란 것이다."""
    report = read_canary_report(_write(tmp_path, verdict=verdict, ok=False))

    refusal = canary_refusal(report, task_id=_TASK)

    assert refusal is not None and verdict in refusal


def test_an_entropy_above_28_is_refused_even_when_the_verdict_passed(tmp_path: Path) -> None:
    """🔴 이 파일의 존재 이유 - 도구는 32에서도 `pass`를 낸다(그 모듈 docstring)."""
    report = read_canary_report(_write(tmp_path, mmap_rnd_bits="32", rnd_bits_above_28=True))

    refusal = canary_refusal(report, task_id=_TASK)

    assert refusal is not None and "32" in refusal


def test_an_unmeasured_entropy_is_refused_not_read_as_28(tmp_path: Path) -> None:
    """미측정을 28 이하로 적지 않는다(CLAUDE.md §0) - 모르면 제출하지 않는다."""
    report = read_canary_report(
        _write(tmp_path, mmap_rnd_bits=None, rnd_bits_above_28=None, rnd_bits_unmeasured=True)
    )

    assert canary_refusal(report, task_id=_TASK) is not None


@pytest.mark.parametrize("repeat", [3, 9, MEASURED_REPEAT, MEASURED_REPEAT * 2])
def test_a_report_that_declares_too_few_runs_is_refused_by_its_declaration(
    tmp_path: Path, repeat: int
) -> None:
    """선언(`below_measured_repeat: true`)이 거절 사유일 때는 **선언을 사유로 말한다**.

    `repeat >= 10`인데 선언이 `true`인 보고서(도구가 쓰지 않는 모양)도 거절하고, 문구가
    「카나리를 20회만 돌렸다 - 실측한 횟수(10)보다 적어」처럼 **자기모순이 되지 않아야** 한다
    (독립 검토 LOW — 앞 판 문구는 선언 분기와 직접 견줌 분기가 같은 말이었다). 선언 분기를
    지우면 `repeat >= 10` 행이 통과해 빨개진다(그 행이 선언 피연산자를 무는 유일한 핀이다).
    """
    report = read_canary_report(_write(tmp_path, repeat=repeat, below_measured_repeat=True))

    refusal = canary_refusal(report, task_id=_TASK)

    assert refusal is not None
    prefix = f"카나리 보고서가 below_measured_repeat=true라 적었다(repeat={repeat},"
    assert refusal.startswith(prefix)
    assert "회만 돌렸다" not in refusal


@pytest.mark.parametrize("repeat", [3, 9])
def test_a_hand_edited_repeat_below_the_measured_count_is_refused(
    tmp_path: Path, repeat: int
) -> None:
    """`below_measured_repeat: false`라고 적혀 있어도 `repeat` 자신이 실측 횟수 아래면 거절한다.

    (리뷰 LOW-3) 앞 판은 횟수를 **자기 선언**(`below_measured_repeat`)으로만 봐서 손으로 `repeat`만
    고친 보고서(`repeat: 3`, 선언은 `false` 그대로)가 통과했다. 도구는 `repeat`와 선언을 같은
    값에서 같이 쓰므로(`tools/oracle_canary.py`의 `_report`) 둘이 어긋난 보고서는 도구가 쓴 것이
    아니다 - 여기서는 선언을 안 믿고 `repeat`를 본다.

    ⚠ 단언이 문구의 **어느 자리**인지 가른다: 거절 문구 끝의 `measured_at`·Docker 버전에 `3`·`9`가
    이미 있어 `str(repeat) in refusal`은 `repeat`를 문구가 안 들어도 통과한다(앞 판의 공허한 단언).
    """
    report = read_canary_report(_write(tmp_path, repeat=repeat, below_measured_repeat=False))

    refusal = canary_refusal(report, task_id=_TASK)

    assert refusal is not None
    assert refusal.startswith(f"카나리를 {repeat}회만 돌렸다 - 실측한 횟수({MEASURED_REPEAT})보다")
    assert "below_measured_repeat는 false" in refusal


def test_the_repeat_is_not_cross_checked_against_the_combos(tmp_path: Path) -> None:
    """⚠ **한계를 핀으로 둔다**(독립 검토 F1). 게이트는 `repeat`를 보고서가 적은 값 그대로 읽고
    `combos[*].exit_codes`의 실제 길이와 대조하지 않는다 - `repeat: 10`인데 `exit_codes`가 셋뿐인
    보고서가 통과한다. 도구는 둘을 같은 값에서 쓰지만(`_report`) 게이트는 `combos`를 안 읽고,
    대조하려면 `combos`를 필수 칸으로 올려야 해 기존 픽스처 넷(이 파일 · `test_oracle_cli` ·
    `test_oracle_wiring` · `test_pipeline_e2e`)이 깨진다. 대조가 들어오면 이 핀이 빨개져
    `canary_report.py`의 한계 서술을 같이 고치게 한다.
    """
    codes = [77, 77, 77]
    short = [{"name": "ref/vul", "image": "x", "exit_codes": codes, "ok": True, "problems": []}]
    report = read_canary_report(_write(tmp_path, repeat=MEASURED_REPEAT, combos=short))

    assert canary_refusal(report, task_id=_TASK) is None


@pytest.mark.parametrize("repeat", [MEASURED_REPEAT, MEASURED_REPEAT + 1])
def test_a_report_at_or_above_the_measured_repeat_passes_the_repeat_check(
    tmp_path: Path, repeat: int
) -> None:
    """경계: 실측 횟수 **이상**은 이 검사로 거절하지 않는다(`<`이지 `<=`가 아니다)."""
    report = read_canary_report(_write(tmp_path, repeat=repeat))

    assert canary_refusal(report, task_id=_TASK) is None


@pytest.mark.parametrize("bits", ["0", "00"])
def test_the_lower_bound_of_the_entropy_is_not_checked(tmp_path: Path, bits: str) -> None:
    """⚠ **한계를 문장이 아니라 핀으로 둔다.** `_DECIMAL_BITS` 주석과 `_above_limit` docstring이
    「`0`은 모양이 맞아 통과한다」고 적는다 - 하한 검사가 들어오면 이 핀이 빨개져 그 두 문장을
    같이 고치게 한다. `vm.mmap_rnd_bits`의 커널 최솟값은 이 저장소가 재지 않았고(아키텍처마다
    다르다) 도구의 `_BITS_LINE`(`[0-9]+`)도 `0`을 받는다 - 한쪽만 거절하면 둘이 갈린다.
    """
    report = read_canary_report(_write(tmp_path, mmap_rnd_bits=bits, rnd_bits_above_28=False))

    assert canary_refusal(report, task_id=_TASK) is None


def test_a_report_whose_ok_is_false_is_refused_whatever_the_verdict_says(tmp_path: Path) -> None:
    """`ok`와 `verdict`가 어긋나면 **거절한다** - 둘 중 관대한 쪽을 조용히 고르지 않는다."""
    report = read_canary_report(_write(tmp_path, ok=False))

    assert canary_refusal(report, task_id=_TASK) is not None


# --- 못 읽는 보고서 ---------------------------------------------------------------


def test_a_missing_file_is_an_error_not_a_pass(tmp_path: Path) -> None:
    with pytest.raises(CanaryReportError):
        read_canary_report(tmp_path / "nope.json")


@pytest.mark.parametrize("text", ["", "not json", "[]", '"scalar"', "123"])
def test_a_report_that_is_not_a_json_object_is_an_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "canary.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(CanaryReportError):
        read_canary_report(path)


@pytest.mark.parametrize("key", ["task", "verdict", "ok", "mmap_rnd_bits", "measured_at"])
def test_a_report_missing_a_field_the_gate_reads_is_an_error(tmp_path: Path, key: str) -> None:
    """빈 칸을 기본값으로 메우지 않는다 - 「없다」를 「통과」로 읽으면 게이트가 없는 것과 같다."""
    body = {k: v for k, v in _PASSING.items() if k != key}
    path = tmp_path / "canary.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(CanaryReportError):
        read_canary_report(path)


def test_a_report_with_the_wrong_type_in_a_field_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CanaryReportError):
        read_canary_report(_write(tmp_path, ok="yes"))


def test_a_report_that_is_not_utf8_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """`UnicodeDecodeError`는 `ValueError`라 파싱 분기로 안 간다 - 읽기 분기가 `OSError`만 잡았다.

    호출부(`cli._canary_gate`)는 `CanaryReportError`만 잡으므로 흘리면 트레이스백과 exit 1이
    나온다. 제출은 안 나가니 fail-closed는 유지되지만 조작자가 받는 것이 문구가 아니다.
    형제인 `cli._query_task_id_from_mask_map`은 이미 `(OSError, ValueError)`를 잡는다.
    """
    path = tmp_path / "canary.json"
    path.write_bytes(json.dumps(_PASSING).encode("utf-16"))

    with pytest.raises(CanaryReportError):
        read_canary_report(path)


def test_a_report_nested_past_the_parser_limit_is_an_error(tmp_path: Path) -> None:
    """깊이 중첩된 JSON은 `RecursionError`로 샌다 - 그것도 「못 읽었다」다."""
    path = tmp_path / "canary.json"
    path.write_bytes(b"[" * 200_000)

    with pytest.raises(CanaryReportError):
        read_canary_report(path)


def test_a_report_far_larger_than_any_the_tool_writes_is_an_error(tmp_path: Path) -> None:
    """상한이 없으면 임의 크기 파일을 메모리로 읽는다. 상한은 **정한 값**이다(실측 아님) -
    도구가 쓴 실 보고서 둘이 1,652 B·1,742 B였다(2026-10-04).

    🔴 **통과하는 보고서를 채워서 넘긴다.** 공백 바이트로 채우면 JSON 파싱이 먼저 거절해 상한을
    지워도 초록이다 - 상한에 핀이 없는 것과 같다(처음 판이 그랬다).
    """
    path = tmp_path / "canary.json"
    padded = {**_PASSING, "_pad": "x" * _MAX_REPORT_BYTES}
    path.write_text(json.dumps(padded), encoding="utf-8")
    assert path.stat().st_size > _MAX_REPORT_BYTES

    with pytest.raises(CanaryReportError, match="너무 크다"):
        read_canary_report(path)


def test_an_incomplete_report_says_what_the_tool_hit_not_which_field_is_missing(
    tmp_path: Path,
) -> None:
    """도구가 완주 못 하면 `_incomplete_record`를 쓴다 - 그 기록에는 `repeat`가 없다.

    앞 판은 필수 칸 루프가 먼저 돌아 「repeat가 없다」가 나왔다. 거절은 맞지만 조작자가 보는
    사유가 틀렸다 - 실제 원인(`verdict: incomplete` · `error`)은 파일에 적혀 있었다.
    """
    path = tmp_path / "canary.json"
    path.write_text(
        json.dumps(
            {
                "task": _TASK,
                "ok": False,
                "completed": False,
                "verdict": "incomplete",
                "error": "image_missing",
                "recorded_at": "2026-10-04T05:10:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CanaryReportError, match="image_missing"):
        read_canary_report(path)


# --- 자기 선언 요약이 같은 파일의 반증보다 세면 안 된다 (§2.2) -----------------------


def test_a_report_that_saw_a_segv_is_refused_even_when_it_calls_itself_pass(
    tmp_path: Path,
) -> None:
    """`139` 관측이 이 게이트의 표적이다 - `verdict`만 뒤집은 보고서를 받으면 게이트가 없다."""
    report = read_canary_report(_write(tmp_path, segv_count=7))

    problem = canary_refusal(report, task_id=_TASK)

    assert problem is not None and "139" in problem


def test_a_report_listing_problems_is_refused_even_when_it_calls_itself_pass(
    tmp_path: Path,
) -> None:
    """도구는 어긋남을 `problems`에 적는다. `pass`인데 그것이 비어 있지 않으면 자기모순이다."""
    report = read_canary_report(_write(tmp_path, problems=["benign_vul: exits=[139]"]))

    assert canary_refusal(report, task_id=_TASK) is not None


def test_a_report_whose_completed_is_false_is_unreadable_not_merely_refused(
    tmp_path: Path,
) -> None:
    """`completed: false`는 도구의 **미완주 기록**이라 읽기에서 멈춘다 - 칸이 다 있어도 그렇다.

    `canary_refusal`에도 같은 칸을 무는 핀이 있다(`test_a_report_that_did_not_complete_...`가
    아니라 그 함수 쪽). 둘 다 두는 이유: 읽기 분기는 `_incomplete_record`를 위해 있고, 거절
    분기는 그 분기를 누가 지워도 게이트가 남게 하려고 있다.
    """
    with pytest.raises(CanaryReportError, match="완주하지 못했다"):
        read_canary_report(_write(tmp_path, completed=False))


def test_the_refusal_also_guards_completed_so_the_gate_survives_one_deletion() -> None:
    """읽기 쪽 분기를 지워도 거절이 남는지 - 게이트를 두 자리에 둔다.

    `CanaryReport`를 직접 만든다 - 읽기를 거치면 위 분기가 먼저 걸려 이 분기에 못 닿는다.
    """
    report = CanaryReport(
        task=_TASK,
        verdict="pass",
        ok=True,
        repeat=10,
        below_measured_repeat=False,
        rnd_bits_unmeasured=False,
        mmap_rnd_bits="28",
        rnd_bits_above_28=False,
        measured_at="2026-10-03T17:42:07+00:00",
        completed=False,
        segv_count=0,
        problems=(),
    )

    assert canary_refusal(report, task_id=_TASK) is not None


@pytest.mark.parametrize("repeat", [0, -5])
def test_a_report_claiming_a_non_positive_repeat_is_refused(tmp_path: Path, repeat: int) -> None:
    """`below_measured_repeat`가 거짓이라고 적혀 있어도 관측 0회는 관측이 아니다."""
    report = read_canary_report(_write(tmp_path, repeat=repeat, below_measured_repeat=False))

    assert canary_refusal(report, task_id=_TASK) is not None


@pytest.mark.parametrize("bits", ["-1", "2_8", " 28 ", "٢٨", "+28", "28.0", ""])
def test_an_entropy_that_is_not_plain_digits_is_refused(tmp_path: Path, bits: str) -> None:
    """`int()`는 `2_8`·`٢٨`·`+28`을 받는다 - 모르는 모양을 28 이하로 읽지 않는다(미측정과 같은 결).

    ⚠ **값의 하한은 안 문다.** `"0"`은 이 핀에 없다 - `vm.mmap_rnd_bits`의 커널 최솟값을 이
    저장소가 **재지 않았다**(아키텍처마다 다르다). 모양만 보고 값의 범위는 상한만 본다 - 그 한계는
    `test_the_lower_bound_of_the_entropy_is_not_checked`가 핀으로 든다.
    """
    report = read_canary_report(_write(tmp_path, mmap_rnd_bits=bits, rnd_bits_above_28=False))

    assert canary_refusal(report, task_id=_TASK) is not None


def test_the_report_carries_the_sha256_of_the_bytes_it_parsed(tmp_path: Path) -> None:
    """이슈 #487 - 점수가 기댄 보고서를 사후에 변호하려면 **검사한 바이트의** 해시가 필요하다.

    파일을 한 번 더 읽어 해시하면 검사와 기록 사이에 바뀐 파일이 기록에 들어간다."""
    path = _write(tmp_path)

    report = read_canary_report(path)

    assert report.sha256 == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_sha256_is_of_the_raw_bytes_not_of_decoded_text(tmp_path: Path) -> None:
    """CRLF 보고서(Windows에서 손으로 고친 것)의 해시는 **디스크 바이트** 기준이다 - 텍스트로 풀었다
    다시 인코딩하면 개행이 번역돼 해시가 파일과 어긋난다(#387과 같은 갈래)."""
    path = tmp_path / "canary.json"
    crlf = chr(13) + chr(10)
    raw = json.dumps(_PASSING, indent=2).replace(chr(10), crlf).encode("utf-8")
    path.write_bytes(raw)

    report = read_canary_report(path)

    assert crlf.encode() in raw, "공허하다 - CRLF가 없다"
    assert report.sha256 == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_the_file_is_read_once_so_the_hash_cannot_describe_other_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 번째 읽기가 다른 바이트를 주는 파일 - 검사한 바이트와 해시한 바이트가 갈릴 틈이다."""
    path = _write(tmp_path)
    real_read_bytes = Path.read_bytes
    calls: list[Path] = []

    def flaky(self: Path) -> bytes:
        calls.append(self)
        data = real_read_bytes(self)
        return data if len(calls) == 1 else data + b" "

    monkeypatch.setattr(Path, "read_bytes", flaky)

    report = read_canary_report(path)

    assert len(calls) == 1, "보고서를 한 번만 읽어야 한다"
    assert report.sha256 == "sha256:" + hashlib.sha256(real_read_bytes(path)).hexdigest()
