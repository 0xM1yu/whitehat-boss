"""`--slices`를 주면 슬라이스 1개가 타깃 1개다 — 신원(타깃·키·도장·기록) (Phase 3b).

ADR 0012 결정 1과 그 D78 ① 풀이: `Location`은 슬라이스의 **대표 위치**(entry의 file·line)이고,
어느 슬라이스인지는 **`Candidate.slice_id`**가 든다 — 하네스가 도장하고 모델은 쓰지 않는다.
Case B의 `entry.symbol`은 sink 호출 이름이라 `Location.symbol`은 후보의 `scopes`에서 감싸는
함수 이름을 가져온다. Scout가 **무엇을 보는가**(SOURCE 렌더·`slice_criterion`)는 다음 PR이다.
"""

import json
from pathlib import Path

import pytest

from slicer_source import assemble
from src.adapters.slices import SliceInputError, split_source
from src.orchestrator.runner import RunSpec
from src.schemas.common import Location
from tests.test_pipeline_e2e import CONDITION, STARTED_AT, TARGET, _events, _of, _plan, _run

_PARSER = split_source((TARGET / "parser.c").read_bytes())


def _slice(sid: str, case: str, entry: tuple[str, int, str], scope: str, lines: set[int]) -> dict:
    file, line, symbol = entry
    points = {(file, n) for n in lines}
    return {
        "id": sid,
        "candidate_key": f"key_{sid}",
        "case": case,
        "entry": {"file": file, "line": line, "symbol": symbol},
        "members": [f"key_{sid}"],
        "absorbed": [],
        "lines": [{"file": f, "line": n} for f, n in sorted(points)],
        "code": assemble(points, {"parser.c": _PARSER}) if file == "parser.c" else "",
        "_scope": scope,
    }


def _slices_file(tmp_path: Path, slices: list[dict]) -> Path:
    manifest = {
        s["candidate_key"]: {
            # 슬라이서가 싣는 모양 그대로 — `file`은 Joern 원경로(상대화 안 됨)다.
            "scopes": [{"file": f"/repo/{s['entry']['file']}", "symbol": s.pop("_scope"),
                        "full_name": "x", "line": 19}],
        }
        for s in slices
    }
    path = tmp_path / "slices.json"
    path.write_text(json.dumps({
        "slice_version": "slice-v2",
        "status": "ok",
        "run_signature": "sig_0001",
        "joern_engine_config": {"maxCallDepth": 2},
        "slices": slices,
        "candidate_manifest": manifest,
    }), encoding="utf-8")
    return path


_CASE_A = ("slice_0001", "A", ("parser.c", 19, "parse_header"), "parse_header", {19, 24})
_CASE_B = ("slice_0002", "B", ("parser.c", 24, "memcpy"), "parse_header", {21, 24})


def test_each_slice_becomes_one_candidate_stamped_with_its_slice_id(tmp_path: Path) -> None:
    """타깃 = 슬라이스. 후보마다 `slice_id`가 하네스 도장으로 붙는다.

    위치는 entry 줄 + 감싸는 함수 이름(Case B의 entry 이름 `memcpy`가 아니라)이다.
    """
    path = _slices_file(tmp_path, [_slice(*_CASE_A), _slice(*_CASE_B)])

    result, trace_path = _run(tmp_path, slices_path=path)

    got = {(c.slice_id, c.location) for c in result.candidates}
    assert got == {
        ("slice_0001", Location(file="parser.c", line_start=19, symbol="parse_header")),
        ("slice_0002", Location(file="parser.c", line_start=24, symbol="parse_header")),
    }
    assert _plan(trace_path)["slice_targets"] == 2


def test_two_slices_with_the_same_entry_are_both_analysed(tmp_path: Path) -> None:
    """entry가 같아도 다른 슬라이스다 — 타깃 키가 위치만이면 둘째가 첫째에 먹힌다."""
    twin = ("slice_0003", "B", ("parser.c", 24, "memcpy"), "parse_header", {24})
    path = _slices_file(tmp_path, [_slice(*_CASE_B), _slice(*twin)])

    result, _ = _run(tmp_path, slices_path=path)

    assert sorted(c.slice_id for c in result.candidates) == ["slice_0002", "slice_0003"]


def test_a_slice_outside_the_analysed_artifacts_is_counted_not_dropped_silently(
    tmp_path: Path,
) -> None:
    """entry 파일이 이 run의 artifact가 아니면 타깃이 못 된다 — 그 수를 `target_plan`에 남긴다."""
    stray = ("slice_0009", "B", ("missing.c", 3, "strcpy"), "main", {3})
    path = _slices_file(tmp_path, [_slice(*_CASE_B), _slice(*stray)])

    result, trace_path = _run(tmp_path, slices_path=path)

    assert [c.slice_id for c in result.candidates] == ["slice_0002"]
    assert _plan(trace_path)["unmatched_slices"] == 1


def test_the_run_record_carries_the_slicer_status_and_signature(tmp_path: Path) -> None:
    """CPG가 못 본 줄은 모델도 못 본다 — 그 한계가 조용하지 않게 기록에 남는다(ADR 0012 결정 1)."""
    path = _slices_file(tmp_path, [_slice(*_CASE_A)])

    result, _ = _run(tmp_path, slices_path=path)

    assert result.record.slicer == {"status": "ok", "run_signature": "sig_0001", "slices": 1}


def test_without_slices_the_record_says_none_and_targets_come_from_files(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    assert result.record.slicer is None
    assert _plan(trace_path)["slice_targets"] == 0
    assert all(c.slice_id is None for c in result.candidates)


def test_an_untrustworthy_slice_file_stops_the_run_before_it_starts(tmp_path: Path) -> None:
    """못 믿는 `--slices`로 도는 run은 없다 — 모델을 부르기 전에 멈춘다."""
    bad = tmp_path / "slices.json"
    bad.write_text(json.dumps({"slice_version": "slice-v1"}), encoding="utf-8")

    with pytest.raises(SliceInputError):
        _run(tmp_path, slices_path=bad)
    assert not _of(_events(tmp_path / "run" / "run_20260901_0001" / "trace.jsonl"), "model_call")


def test_run_spec_keeps_the_slices_path() -> None:
    spec = RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT)
    assert spec.slices_path is None


# --- CLI `--slices` ---------------------------------------------------------


def test_cli_run_takes_slices_and_records_them_in_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`main()`을 끝까지 돈다 — 파서만 보면 `RunSpec`까지의 배선이 끊겨도 초록이다."""
    from src import cli

    path = _slices_file(tmp_path, [_slice(*_CASE_A)])
    out = tmp_path / "artifacts"

    code = cli.main([
        "run", "--profile", "dev_fake", "--target", str(TARGET),
        "--slices", str(path), "--artifacts-root", str(out),
    ])

    assert code == 0, capsys.readouterr().err
    (manifest,) = out.glob("*/run_manifest.json")
    assert json.loads(manifest.read_text("utf-8"))["slicer"]["run_signature"] == "sig_0001"


def test_cli_run_refuses_an_untrustworthy_slice_file_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """형식 라벨과 같은 자리(`_claim_run_dir` 앞)에서 막는다 — 번호도 디렉터리도 안 남긴다."""
    from src import cli

    bad = tmp_path / "slices.json"
    bad.write_text(json.dumps({"slice_version": "slice-v1"}), encoding="utf-8")
    out = tmp_path / "artifacts"
    out.mkdir()

    code = cli.main([
        "run", "--profile", "dev_fake", "--target", str(TARGET),
        "--slices", str(bad), "--artifacts-root", str(out),
    ])

    assert code == 2
    assert "[ERROR]" in capsys.readouterr().err
    assert not list(out.iterdir()), "거부된 run이 디렉터리를 남겼다"


@pytest.mark.parametrize(
    "entry", [pytest.param("oops", id="entry"), pytest.param({"scopes": ["oops"]}, id="scope")]
)
def test_cli_run_refuses_a_broken_candidate_manifest_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture, entry: object
) -> None:
    """manifest가 깨져도 같은 자리에서 막는다 — `slice_refs`가 run 디렉터리를 만든 뒤
    `AttributeError`로 죽던 자리다(#284 리뷰 MEDIUM, 재현: `run_20260925_0001`이 남았다)."""
    from src import cli

    path = _slices_file(tmp_path, [_slice(*_CASE_B)])
    document = json.loads(path.read_text("utf-8"))
    document["candidate_manifest"]["key_slice_0002"] = entry
    path.write_text(json.dumps(document), encoding="utf-8")
    out = tmp_path / "artifacts"
    out.mkdir()

    code = cli.main([
        "run", "--profile", "dev_fake", "--target", str(TARGET),
        "--slices", str(path), "--artifacts-root", str(out),
    ])

    assert code == 2
    assert "candidate_manifest" in capsys.readouterr().err
    assert not list(out.iterdir()), "거부된 run이 디렉터리를 남겼다"


def test_a_case_b_slice_without_scopes_names_no_function_rather_than_the_sink(
    tmp_path: Path,
) -> None:
    """Case B의 entry 이름은 sink 호출(`memcpy`)이라 함수 이름이 아니다 — scopes가 없으면
    `symbol`을 비운다. Case A의 entry는 메서드 자신이라 그 이름을 쓴다(#284 리뷰 LOW)."""
    from src.adapters.slices import load_slices, slice_refs

    path = _slices_file(tmp_path, [_slice(*_CASE_A), _slice(*_CASE_B)])
    document = json.loads(path.read_text("utf-8"))
    for entry in document["candidate_manifest"].values():
        entry["scopes"] = []
    path.write_text(json.dumps(document), encoding="utf-8")

    symbols = {ref.slice_id: ref.location.symbol for ref in slice_refs(load_slices(path))}

    assert symbols == {"slice_0001": "parse_header", "slice_0002": None}


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param(
            Location(file="parser.c", symbol="parse_header", line_start=19, line_end=26),
            "`parser.c` 의 `parse_header` (줄 19-26)",
            id="symbol",
        ),
        pytest.param(Location(file="parser.c"), "`parser.c` 의 `(파일 전체)`", id="whole-file"),
        pytest.param(Location(file="parser.c", line_start=24), "`parser.c` (줄 24-24)", id="lines"),
    ],
)
def test_the_prompt_never_calls_a_line_range_the_whole_file(
    target: Location, expected: str
) -> None:
    """이름 없이 줄만 든 대상이 생겼다 — scopes 없는 Case B(바로 위 테스트)가 그렇다. 「이름이
    없으면 파일 전체」라는 옛 등식으로 그리면 프롬프트가 「`(파일 전체)` (줄 24-24)」로 자기
    FACTS와 어긋난다(코드 리뷰 MEDIUM, 재현)."""
    from src.orchestrator.nodes.scout import describe_target

    assert describe_target(target) == expected


def test_a_failed_twin_slice_counts_as_unanalysed_even_when_its_twin_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entry가 같은 두 슬라이스 중 하나만 실패하면 「분석되지 않은 대상」은 1이다.

    타깃 키가 위치만이면 두 슬라이스가 한 키가 되어, 살아남은 쪽이 죽은 쪽을 「분석됨」으로
    덮는다 — 리포트가 0을 말한다. 위 검사(후보 둘)는 그 합쳐짐을 못 본다.
    """
    from src.interfaces.model_client import SchemaParseError
    from tests.test_pipeline_e2e import _failing_model

    twin = ("slice_0003", "B", ("parser.c", 24, "memcpy"), "parse_header", {24})
    path = _slices_file(tmp_path, [_slice(*_CASE_B), _slice(*twin)])
    _failing_model(monkeypatch, [SchemaParseError("첫 슬라이스만 실패(테스트)")])

    result, _ = _run(tmp_path, slices_path=path)

    assert [c.slice_id for c in result.candidates] == ["slice_0003"]
    assert result.report.unanalyzed_targets == 1
