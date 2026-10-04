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

import json
from pathlib import Path

import pytest

from src.adapters.canary_report import CanaryReportError, canary_refusal, read_canary_report

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


def test_a_report_that_ran_fewer_times_than_measured_is_refused(tmp_path: Path) -> None:
    report = read_canary_report(_write(tmp_path, repeat=3, below_measured_repeat=True))

    refusal = canary_refusal(report, task_id=_TASK)

    assert refusal is not None and "3" in refusal


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
