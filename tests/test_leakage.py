"""데이터 누출 검사 테스트 (evaluation.md §4).

이 검사가 통과하지 않으면 그 run의 비교 결과는 무효다. 따라서 검사 **자체가 실제로
잡는지**를 고정한다 — 누출이 있는데도 초록불이 뜨는 검사는 없는 것보다 나쁘다.
"""

import json
from pathlib import Path

import pytest

from eval.leakage import __main__ as leakage_main
from eval.leakage import digests, intersecting, load_problem_names, scan_paths, scan_text

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "prompts"

CLEAN = """\
# 역할: Scout

대상 함수에서 길이 검증 없는 복사를 찾는다. 사실 블록 밖의 내용은 추측하지 않는다.
"""

LEAKED_CVE = "이 문제는 CVE-2021-3156 재현이다.\n"
LEAKED_FLAG = "정답: flag{buffer_overflow_in_parser}\n"
LEAKED_DIFF = "--- a/src/parser/http.c\n+++ b/src/parser/http.c\n@@ -1 +1 @@\n"


# --- 마스킹 스캔 -------------------------------------------------------------


def test_clean_text_has_no_leak() -> None:
    assert scan_text(CLEAN, path=Path("prefix.md")) == []


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        (LEAKED_CVE, "cve_id"),
        (LEAKED_FLAG, "flag"),
        (LEAKED_DIFF, "patch_diff"),
    ],
)
def test_each_pattern_is_detected(text: str, pattern: str) -> None:
    leaks = scan_text(text, path=Path("body.md"))

    assert pattern in {leak.pattern for leak in leaks}
    assert leaks[0].line >= 1  # 고치려면 위치를 알아야 한다


def test_problem_name_is_matched_case_insensitively() -> None:
    leaks = scan_text("대상은 Baby Heap 문제다.", path=Path("x.md"), problem_names=["baby heap"])

    assert [leak.pattern for leak in leaks] == ["problem_name"]


def test_problem_names_file_ignores_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "names.txt"
    path.write_text("# 주석\n\nBaby Heap\n  Easy ROP  \n", encoding="utf-8")

    assert load_problem_names(path) == ["Baby Heap", "Easy ROP"]


def test_missing_problem_names_file_disables_only_that_check(tmp_path: Path) -> None:
    """목록이 없다고 CVE·flag 검사까지 꺼지면 안 된다."""
    assert load_problem_names(tmp_path / "없음.txt") == []
    assert scan_text(LEAKED_CVE, path=Path("x.md"), problem_names=[])


def test_scan_skips_binary_suffixes(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_text(LEAKED_CVE, encoding="utf-8")

    assert scan_paths([tmp_path]) == []


# --- digest 교집합 -----------------------------------------------------------


def test_intersection_is_empty_for_disjoint_sets() -> None:
    assert intersecting({"kb/a.c": "sha256:aa"}, {"eval/b.c": "sha256:bb"}) == []


def test_intersection_finds_the_same_content_under_different_paths() -> None:
    """경로가 달라도 내용이 같으면 누출이다 — 복사본이 가장 흔한 경로다."""
    overlaps = intersecting({"kb/copy.c": "sha256:aa"}, {"eval/orig.c": "sha256:aa"})

    assert [(o.kb_key, o.eval_key) for o in overlaps] == [("kb/copy.c", "eval/orig.c")]
    assert "kb/copy.c" in overlaps[0].render()


def test_digests_reads_a_directory_and_a_manifest(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.c").write_text("int a;", encoding="utf-8")
    from_tree = digests(tree)

    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(from_tree), encoding="utf-8")

    assert list(from_tree) == ["a.c"]
    assert from_tree["a.c"].startswith("sha256:")
    assert digests(manifest) == from_tree


def test_manifest_with_non_string_digest_fails_loudly(tmp_path: Path) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"a.c": 12}), encoding="utf-8")

    with pytest.raises(ValueError, match="digest"):
        digests(manifest)


# --- 진입점 (exit code 계약) --------------------------------------------------


def test_clean_fixture_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    (tmp_path / "prefix.md").write_text(CLEAN, encoding="utf-8")

    code = leakage_main.main(["--scan", str(tmp_path)])

    assert code == 0
    assert "[통과]" in capsys.readouterr().out


def test_leaked_fixture_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    (tmp_path / "body.md").write_text(CLEAN + LEAKED_CVE, encoding="utf-8")

    code = leakage_main.main(["--scan", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "CVE-2021-3156" in out and "[실패]" in out


def test_overlapping_manifests_exit_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    shared = {"parser.c": "sha256:" + "a" * 64}
    kb = tmp_path / "kb.json"
    evalset = tmp_path / "eval.json"
    kb.write_text(json.dumps(shared), encoding="utf-8")
    evalset.write_text(json.dumps(shared), encoding="utf-8")
    (tmp_path / "clean.md").write_text(CLEAN, encoding="utf-8")

    code = leakage_main.main(
        ["--scan", str(tmp_path / "clean.md"), "--kb", str(kb), "--evalset", str(evalset)]
    )

    assert code == 1
    assert "KB ∩ 평가셋" in capsys.readouterr().out


def test_missing_manifests_are_skipped_not_silently_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """"검사했고 깨끗함"과 "검사할 것이 없었음"을 같은 초록불로 뭉개지 않는다."""
    (tmp_path / "clean.md").write_text(CLEAN, encoding="utf-8")

    code = leakage_main.main(["--scan", str(tmp_path)])

    assert code == 0
    assert "[생략]" in capsys.readouterr().out


# --- 실제 저장소 -------------------------------------------------------------


def test_repository_prompts_are_clean() -> None:
    """C4에서 한 약속(프롬프트에 정답 단서를 넣지 않는다)을 스크립트가 지킨다."""
    assert scan_paths([PROMPTS]) == []


def test_default_scan_includes_prompts() -> None:
    """기본 대상에서 프롬프트가 빠지면 이 검사의 목적이 사라진다."""
    assert PROMPTS in leakage_main.DEFAULT_SCAN


def test_run_globs_include_run_prompt_artifacts() -> None:
    """`prompts/`는 템플릿이다. 대상 코드가 섞이는 곳은 run 산출물 쪽이다 (D19)."""
    assert "artifacts/*/prompts/*.md" in leakage_main.RUN_GLOBS
    assert leakage_main.TRACE_GLOB in leakage_main.RUN_GLOBS


def test_check_scans_run_prompt_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """산출물 프롬프트가 대상에 안 들어가면 렌더된 입력은 한 번도 검사되지 않는다."""
    prompts = tmp_path / "artifacts" / "run_20260901_0001" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "0001_scout.md").write_text(CLEAN + LEAKED_CVE, encoding="utf-8")
    monkeypatch.setattr(leakage_main, "REPO_ROOT", tmp_path)

    report = leakage_main.check(
        scan=[], kb=None, evalset=None, problem_names=tmp_path / "none.txt"
    )

    assert report.scanned == 1
    assert [leak.pattern for leak in report.leaks] == ["cve_id"]


def test_repository_passes_the_default_check(capsys: pytest.CaptureFixture) -> None:
    assert leakage_main.main([]) == 0
    assert "[실패]" not in capsys.readouterr().out
