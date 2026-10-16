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
    """`prompts/`는 템플릿이다. 대상 코드가 섞이는 곳은 run 산출물 쪽이다 (D19).

    ⚠ **이 핀이 무는 것은 철자다.** 참인 명제는 *"이 글자로 되돌리면 죽는다"*이고
    *"run 산출물이 실제로 검사된다"*는 아래 `test_the_default_call_scans_...`와
    `test_check_scans_a_run_outside_the_repository`가 문다(CLAUDE.md §2.5 끝절).
    """
    assert "*/prompts/*.md" in leakage_main.RUN_GLOBS
    assert leakage_main.TRACE_GLOB in leakage_main.RUN_GLOBS


# --- 스캔 면 (R3) ------------------------------------------------------------
#
# R3가 적는 결함: *"`RUN_GLOBS`가 `REPO_ROOT.glob("artifacts/*/…")`이라 저장소 밖
# `--artifacts-root`로 돈 run은 기본 호출에서 한 건도 안 잡힌다."* ATOM run이 전부
# 그 모양이므로(`--artifacts-root /srv/tenants/user/artifacts`) 기본 호출은 **그 장비에
# 남은 잔여물**을 검사하고 방금 돈 run은 안 본다.


def test_default_artifacts_root_is_the_repository_one() -> None:
    """기본 호출이 저장소 안 run을 계속 본다 — R3 고침이 그것을 빼앗으면 안 된다."""
    assert leakage_main.DEFAULT_ARTIFACTS_ROOT == REPO_ROOT / "artifacts"


def test_the_default_call_scans_repository_run_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """산출물 프롬프트가 대상에 안 들어가면 렌더된 입력은 한 번도 검사되지 않는다 (D19)."""
    prompts = tmp_path / "run_20260901_0001" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "0001_scout.md").write_text(CLEAN + LEAKED_CVE, encoding="utf-8")
    monkeypatch.setattr(leakage_main, "DEFAULT_ARTIFACTS_ROOT", tmp_path)

    code = leakage_main.main(["--problem-names", str(tmp_path / "none.txt")])

    assert code == 1
    assert "cve_id" in capsys.readouterr().out


def test_check_scans_a_run_outside_the_repository(tmp_path: Path) -> None:
    """ATOM run은 `--artifacts-root ~/artifacts_*`라 **저장소 밖**에 있다 (R3)."""
    prompts = tmp_path / "run_20260901_0001" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "0001_scout.md").write_text(CLEAN + LEAKED_CVE, encoding="utf-8")

    report = leakage_main.check(
        scan=[],
        artifacts_root=tmp_path,
        kb=None,
        evalset=None,
        problem_names=tmp_path / "none.txt",
    )

    assert report.run_artifacts == 1
    assert [leak.pattern for leak in report.leaks] == ["cve_id"]


def test_check_ignores_runs_under_a_different_root(tmp_path: Path) -> None:
    """음성 핀 — 준 뿌리 **밖**은 안 들어온다.

    R3가 문제 삼는 것은 "0건"이 아니라 **무엇을 검사한 것인지 명령만 보고 알 수 없다**는
    것이다. 뿌리를 줬는데 다른 곳의 run이 섞여 들어오면 그 성질이 똑같이 깨진다.
    """
    elsewhere = tmp_path / "artifacts_다른곳" / "run_20260901_0001" / "prompts"
    elsewhere.mkdir(parents=True)
    (elsewhere / "0001_scout.md").write_text(CLEAN + LEAKED_CVE, encoding="utf-8")
    given = tmp_path / "artifacts_준것"
    given.mkdir()

    report = leakage_main.check(
        scan=[],
        artifacts_root=given,
        kb=None,
        evalset=None,
        problem_names=tmp_path / "none.txt",
    )

    assert report.run_artifacts == 0
    assert report.leaks == []


def test_the_report_states_which_surface_it_scanned(tmp_path: Path) -> None:
    """출력만 보고 무엇을 훑었는지 알 수 있어야 한다 — 그것이 R3가 남긴 문장이다."""
    scanned = tmp_path / "훑을곳"
    scanned.mkdir()
    (scanned / "clean.md").write_text(CLEAN, encoding="utf-8")
    root = tmp_path / "artifacts_atom"
    root.mkdir()

    report = leakage_main.check(
        scan=[scanned],
        artifacts_root=root,
        kb=None,
        evalset=None,
        problem_names=tmp_path / "none.txt",
    )
    rendered = report.render()

    assert str(scanned) in rendered
    assert str(root) in rendered
    # **「run 산출물 N건」이 `--scan` 경로를 같이 세면 안 된다.** 이 배치가 그것을 가르는
    # 유일한 자리다 — `--scan`은 있고 뿌리는 비었다. 변이 `len(found)` → `len(targets)`가
    # 나머지 검사 스물여섯을 **전부 통과했다**(실측). 수가 무엇을 센 것인지 모르면 이
    # 고침이 없애려던 성질이 그대로 남는다(§0).
    assert report.run_artifacts == 0
    assert report.scanned == 1


def test_artifacts_root_flag_reaches_the_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`parse_args`가 아니라 `main()`을 돌린다 — 배선이 끊기면 여기서만 드러난다."""
    prompts = tmp_path / "run_20260901_0001" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "0001_scout.md").write_text(CLEAN + LEAKED_CVE, encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text(CLEAN, encoding="utf-8")

    code = leakage_main.main(
        ["--scan", str(clean), "--artifacts-root", str(tmp_path), "--problem-names", str(clean)]
    )

    assert code == 1
    assert "CVE-2021-3156" in capsys.readouterr().out


@pytest.mark.parametrize("form", ["없는 경로", "빈 문자열", "디렉터리가 아닌 파일"])
def test_a_root_that_is_not_a_directory_is_an_error_not_a_green_light(
    form: str, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """뿌리를 **명시했는데** 디렉터리가 아니면 0건이 아니라 오류다.

    오타 하나가 "검사했고 깨끗함"을 만들어 내면 이 스캐너를 고친 의미가 없다 — 모듈
    docstring이 digest 교집합에 대해 이미 같은 말을 한다(`[생략]`과 `[통과]`를 가른다).

    **문형을 세서 넣었다.** 처음 판은 「없는 경로」 하나만 물었는데, 그 축이 **문자열**이
    아니라 *"준 값이 의도한 run 뿌리인가"*라 빈 문자열이 통과했다 — argparse의 `type=Path`가
    `""`를 `Path(".")`로 조용히 바꿔 `is_dir()`이 참이 되고 **현재 디렉터리**를 훑는다.
    `--artifacts-root "$ATOM_ARTIFACTS"`에서 변수가 비면 나는 모양이다. 실측한 문형:
    `''`→`.`(참) · `'   '`→그대로(거짓) · `'.'`·`'./'`→`.`(참, **정당한 값이다**) ·
    파일 경로(거짓).

    ⚠ **못 무는 것을 적는다.** 존재하는 **엉뚱한** 디렉터리(옆 뿌리를 가리킨 오타)는 이
    검사가 못 가른다. 그것에 답하는 것은 `render()`가 찍는 경로와 건수다.
    **기본 뿌리가 없는 것은 오류가 아니다** — 그 반대쪽은 아래
    `test_an_absent_default_root_is_not_an_error`가 문다.
    """
    a_file = tmp_path / "뿌리아님.txt"
    a_file.write_text(CLEAN, encoding="utf-8")
    value = {
        "없는 경로": str(tmp_path / "없는뿌리"),
        "빈 문자열": "",
        "디렉터리가 아닌 파일": str(a_file),
    }[form]

    code = leakage_main.main(["--artifacts-root", value])

    assert code == 2
    assert "[ERROR]" in capsys.readouterr().err


def test_an_absent_default_root_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """기본 뿌리가 **없는** 것은 오류가 아니다 — CI가 정확히 그 상태로 돈다.

    `artifacts/`는 gitignore라 갓 클론한 체크아웃에 없다. 위 검사가 명시한 뿌리를 exit 2로
    막으므로, 안 막는 쪽을 여기서 고정한다.

    **이 검사가 없던 동안 인용이 틀려 있었다(§2.1).** 위 docstring이 이 명제를
    `test_repository_passes_the_default_check`가 문다고 적었는데, 그것은 실제 저장소에 대고
    `main([])`을 돌리고 **이 체크아웃에는 `artifacts/`가 있다**(실측 run 산출물 7건). 즉 그
    검사가 문 것은 뿌리가 **있을 때**다. 게다가 `main([])`은 `args.artifacts_root`가 `None`
    이라 뿌리 검사 분기에 **아예 안 들어간다** — 부재를 만들어 주지 않으면 물 수가 없다.
    """
    monkeypatch.setattr(leakage_main, "DEFAULT_ARTIFACTS_ROOT", tmp_path / "존재안함")

    code = leakage_main.main(["--problem-names", str(tmp_path / "none.txt")])

    captured = capsys.readouterr()
    assert code == 0
    assert "[ERROR]" not in captured.err
    assert "run 산출물 0건" in captured.out


def test_repository_passes_the_default_check(capsys: pytest.CaptureFixture) -> None:
    assert leakage_main.main([]) == 0
    assert "[실패]" not in capsys.readouterr().out
