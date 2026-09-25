"""`src/adapters/slices.py` — 슬라이서 산출물(`slices.json`)을 읽고 Scout의 `SOURCE`로 렌더한다.

ADR 0012 결정 1(그 ADR 「후속 — Phase 번호」의 Phase 3). 이 파일은 **읽기·검증·렌더만** 본다 —
러너 배선(타깃·캡슐·Auditor)은 러너 쪽 테스트가 본다.

**기대값의 `code`는 슬라이서 자신이 만든다**(`slicer_source.assemble`). 하네스(`src/`)는 슬라이서를
import하지 않지만 테스트는 한다 — 렌더러의 줄 규칙이 슬라이서와 같다는 것을 지어낸 문자열이
아니라 슬라이서의 조립 함수로 대조하기 위해서다(CLAUDE.md §2.5 「핀 샘플은 저장소에서 온다」).
"""

import json
from pathlib import Path

import pytest

from slicer_source import assemble
from src.adapters.slices import SliceInputError, load_slices, render

_MAIN = (
    "int parse(char *buf, int n)\n{\n    char tmp[16];\n    int k = n;\n"
    "    memcpy(tmp, buf, k);\n    return 0;\n}\n"
)
_HELPER = "void helper(char *p)\n{\n    free(p);\n    free(p);\n}\n"


def _split(text: str) -> list[str]:
    """슬라이서의 줄 규칙(`slicer.py`의 소스 적재) — CR 접기 뒤 `\\n`으로만 자른다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.removesuffix("\n").split("\n") if text else []


def _slice(sid: str, case: str, points: set[tuple[str, int]], sources: dict, **extra) -> dict:
    entry_file, entry_line = min(points)
    return {
        "id": sid,
        "candidate_key": f"key_{sid}",
        "case": case,
        "entry": {"file": entry_file, "line": entry_line, "symbol": "memcpy"},
        "members": [f"key_{sid}"],
        "absorbed": [],
        "lines": [{"file": f, "line": n} for f, n in sorted(points)],
        "code": assemble(points, sources),
        **extra,
    }


def _document(slices: list[dict], **top) -> dict:
    return {
        "slice_version": "slice-v2",
        "repo": "demo",
        "status": "ok",
        "run_signature": "sig_0001",
        "joern_engine_config": {"maxCallDepth": 2},
        "slices": slices,
        "candidate_manifest": {s["candidate_key"]: {"scopes": []} for s in slices},
        **top,
    }


def _write(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "slices.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "repo"
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    return root


def _reader(root: Path):
    return lambda relative: (root / relative).read_bytes()


_SOURCES = {"main.c": _split(_MAIN), "helper.c": _split(_HELPER)}


# --- 적재 -------------------------------------------------------------------


def test_a_slice_v2_file_loads_with_its_status_signature_and_given_end(tmp_path: Path) -> None:
    """슬라이스 1개가 타깃 1개다. Case A는 source 끝을, B는 sink 끝을 준다(v3 「Slice criterion」).
    """
    document = _document([
        _slice("slice_0001", "A", {("main.c", 1), ("main.c", 5)}, _SOURCES),
        _slice("slice_0002", "B", {("helper.c", 3), ("helper.c", 4)}, _SOURCES),
    ])

    loaded = load_slices(_write(tmp_path, document))

    assert loaded.status == "ok"
    assert loaded.run_signature == "sig_0001"
    assert [s.id for s in loaded.slices] == ["slice_0001", "slice_0002"]
    assert [s.given_end for s in loaded.slices] == ["source", "sink"]
    assert loaded.slices[1].entry == {"file": "helper.c", "line": 3, "symbol": "memcpy"}


def test_a_partial_run_that_reached_the_cpg_loads_and_keeps_its_status(tmp_path: Path) -> None:
    """`partial`은 거부 사유가 아니다 — CPG가 섰으면 받고, 그 사실을 `status`로 들고 간다."""
    document = _document(
        [_slice("slice_0001", "A", {("main.c", 1)}, _SOURCES)], status="partial"
    )

    assert load_slices(_write(tmp_path, document)).status == "partial"


@pytest.mark.parametrize(
    ("top", "reason"),
    [
        ({"slice_version": "slice-v1"}, "slice-v1"),
        ({"status": "failed"}, "failed"),
        ({"status": "partial", "joern_engine_config": None}, "CPG"),
    ],
)
def test_a_slice_file_the_harness_cannot_trust_is_refused_at_load(
    tmp_path: Path, top: dict, reason: str
) -> None:
    """모르는 판·실패한 run·CPG 없이 끝난 run은 **로드에서** 죽는다.

    조용히 빈 타깃 목록이 되면 「슬라이스 0개」와 「못 읽었다」가 같은 run이 된다.
    """
    document = _document([_slice("slice_0001", "A", {("main.c", 1)}, _SOURCES)], **top)

    with pytest.raises(SliceInputError, match=reason):
        load_slices(_write(tmp_path, document))


def test_a_missing_or_broken_slice_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SliceInputError):
        load_slices(tmp_path / "nope.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(SliceInputError):
        load_slices(broken)


@pytest.mark.parametrize("field", ["id", "case", "entry", "lines", "code"])
def test_a_slice_missing_a_required_field_is_refused(tmp_path: Path, field: str) -> None:
    item = _slice("slice_0001", "A", {("main.c", 1)}, _SOURCES)
    del item[field]

    with pytest.raises(SliceInputError):
        load_slices(_write(tmp_path, _document([item])))


def test_duplicate_slice_ids_are_refused(tmp_path: Path) -> None:
    """ID가 타깃의 신원이다 — 둘이 같으면 후보·캐시 키가 서로를 덮는다."""
    item = _slice("slice_0001", "A", {("main.c", 1)}, _SOURCES)

    with pytest.raises(SliceInputError, match="slice_0001"):
        load_slices(_write(tmp_path, _document([item, dict(item)])))


@pytest.mark.parametrize(
    "slices",
    [pytest.param(..., id="missing"), pytest.param({"slice_0001": {}}, id="object")],
)
def test_a_file_without_a_slice_list_says_the_list_is_missing(
    tmp_path: Path, slices: object
) -> None:
    """목록이 통째로 없으면 「항목 모양」이 아니라 「목록이 없다」고 말한다 — 조작자가 엉뚱한
    항목을 찾지 않게(#283 리뷰 LOW)."""
    document = _document([])
    if slices is ...:
        del document["slices"]
    else:
        document["slices"] = slices

    with pytest.raises(SliceInputError, match="슬라이스 목록"):
        load_slices(_write(tmp_path, document))


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param([1], id="array"),
        pytest.param("ab", id="string"),
        pytest.param({"key_slice_0001": "oops"}, id="entry-not-object"),
        pytest.param({"key_slice_0001": {"scopes": "oops"}}, id="scopes-not-array"),
        pytest.param({"key_slice_0001": {"scopes": ["oops"]}}, id="scope-not-object"),
        pytest.param({"key_slice_0001": {"scopes": [{"symbol": 5}]}}, id="symbol-not-string"),
    ],
)
def test_a_malformed_candidate_manifest_is_refused_at_load(
    tmp_path: Path, manifest: object
) -> None:
    """manifest도 **로드에서** 죽는다 — 그 모양을 믿고 읽는 `slice_refs`는 run 디렉터리가 생긴
    뒤에 돈다(#283·#284 리뷰 MEDIUM).

    최상위가 객체가 아니면 `dict()`가 맨 `TypeError`·`ValueError`를 냈고, 안쪽 모양은 아무도 안 봐서
    `slice_refs`의 `.get()`이 `AttributeError`로 죽었다. 문형은 `slice_refs`가 읽는 길을 따라
    센다 — 항목 → `scopes` → 원소 → `symbol`.
    """
    document = _document(
        [_slice("slice_0001", "A", {("main.c", 1)}, _SOURCES)], candidate_manifest=manifest
    )

    with pytest.raises(SliceInputError, match="candidate_manifest"):
        load_slices(_write(tmp_path, document))


# --- 렌더 -------------------------------------------------------------------


def test_render_puts_the_original_line_number_in_a_gutter_under_a_file_banner(
    tmp_path: Path,
) -> None:
    """v3가 선언한 모양 — 파일마다 `/* ===== path ===== */` 배너, 줄마다 원본 줄번호 gutter.

    줄은 불연속으로 남는다(1 → 5). 파일은 슬라이서의 조립 순서(경로 사전순)를 따른다.
    """
    root = _repo(tmp_path, {"main.c": _MAIN.encode(), "helper.c": _HELPER.encode()})
    item = _slice(
        "slice_0001", "A", {("main.c", 1), ("main.c", 5), ("helper.c", 3)}, _SOURCES
    )
    loaded = load_slices(_write(tmp_path, _document([item])))

    assert render(loaded.slices[0], _reader(root)) == (
        "/* ===== helper.c ===== */\n"
        "3|     free(p);\n"
        "/* ===== main.c ===== */\n"
        "1| int parse(char *buf, int n)\n"
        "5|     memcpy(tmp, buf, k);"
    )


def test_render_refuses_a_file_that_drifted_from_what_the_slicer_saw(tmp_path: Path) -> None:
    """하네스가 읽은 파일이 슬라이서가 본 것과 다르면 렌더하지 않는다 — 줄번호가 거짓이 된다."""
    edited = _MAIN.replace("memcpy(tmp, buf, k);", "memcpy(tmp, buf, 16);")
    root = _repo(tmp_path, {"main.c": edited.encode()})
    item = _slice("slice_0001", "A", {("main.c", 5)}, _SOURCES)
    loaded = load_slices(_write(tmp_path, _document([item])))

    with pytest.raises(SliceInputError, match="main.c"):
        render(loaded.slices[0], _reader(root))


def test_render_counts_lines_like_the_slicer_across_a_form_feed(tmp_path: Path) -> None:
    """폼피드는 줄 경계가 아니다(#259). `splitlines()`로 세면 그 뒤가 한 줄씩 밀린다."""
    text = "int a;\f/* page */\nint b;\nint c;\n"
    root = _repo(tmp_path, {"ff.c": text.encode()})
    sources = {"ff.c": _split(text)}
    item = _slice("slice_0001", "B", {("ff.c", 2)}, sources)
    loaded = load_slices(_write(tmp_path, _document([item])))

    assert render(loaded.slices[0], _reader(root)) == "/* ===== ff.c ===== */\n2| int b;"


def test_render_folds_crlf_and_decodes_invalid_bytes_like_the_slicer(tmp_path: Path) -> None:
    """CRLF는 접고, 무효 UTF-8 바이트만 Latin-1로 읽는다(`slicer.py`의 소스 적재 — #254)."""
    raw = b"int a;\r\nchar *s = \"\xff\";\r\nint c;\r\n"
    root = _repo(tmp_path, {"w.c": raw})
    sources = {"w.c": _split(raw.decode("utf-8", errors="replace").replace("�", "\xff"))}
    item = _slice("slice_0001", "B", {("w.c", 2)}, sources)
    loaded = load_slices(_write(tmp_path, _document([item])))

    assert render(loaded.slices[0], _reader(root)) == '/* ===== w.c ===== */\n2| char *s = "\xff";'
