"""`src/adapters/slices.py` — 슬라이서 산출물(`slices.json`)을 읽고 Scout의 `SOURCE`로 렌더한다.

ADR 0012 결정 1(그 ADR 「후속 — Phase 번호」의 Phase 3). 이 파일은 **읽기·검증·렌더만** 본다 —
러너 배선(타깃·Scout가 보는 것·캡슐·Auditor)은 `tests/test_slice_targets.py`가 본다.

**기대값의 `code`는 슬라이서 자신이 만든다**(`slicer_source.assemble`). 하네스(`src/`)는 슬라이서를
import하지 않지만 테스트는 한다 — 렌더러의 줄 규칙이 슬라이서와 같다는 것을 지어낸 문자열이
아니라 슬라이서의 조립 함수로 대조하기 위해서다(CLAUDE.md §2.5 「핀 샘플은 저장소에서 온다」).
"""

import json
from pathlib import Path

import pytest

from slicer_source import assemble, cluster
from src.adapters.slices import SliceInputError, load_slices, render, render_slices, slice_refs
from src.schemas.common import Location

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


def test_a_degraded_slice_keeps_its_marker_through_load(tmp_path: Path) -> None:
    """#307 리뷰(@danielcho02): 슬라이서는 anchor-only 대체 슬라이스에 `degraded`를 남기지만
    consumer가
    그것을 버려서 절단 슬라이스가 러너에 full로 보였다. marker가 적재를 넘어 살아야 하고, 정상
    슬라이스는 False로 남는다.
    """
    document = _document([
        _slice("slice_0001", "A", {("main.c", 1), ("main.c", 5)}, _SOURCES, degraded=True),
        _slice("slice_0002", "B", {("helper.c", 3), ("helper.c", 4)}, _SOURCES),
    ])

    loaded = load_slices(_write(tmp_path, document))

    assert loaded.slices[0].degraded is True
    assert loaded.slices[1].degraded is False


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


def test_a_slice_ref_keeps_the_raw_entry_beside_the_enclosing_function(tmp_path: Path) -> None:
    """`entry`는 슬라이서가 준 그대로다 — v3 `FACTS.slice_criterion.given`이 이것에서 나온다.

    Case B의 entry `symbol`은 sink 호출 이름(`memcpy`)이고 `location.symbol`은 감싸는 함수다.
    한 필드로 합치면 둘 중 하나가 거짓이 된다.
    """
    document = _document([_slice("slice_0001", "B", {("main.c", 5)}, _SOURCES)])
    document["candidate_manifest"] = {"key_slice_0001": {"scopes": [{"symbol": "parse"}]}}

    (ref,) = slice_refs(load_slices(_write(tmp_path, document)))

    assert ref.entry == Location(file="main.c", line_start=5, symbol="memcpy")
    assert ref.location == Location(file="main.c", line_start=5, symbol="parse")


def test_a_degraded_slice_carries_its_marker_to_the_sliceref_boundary(tmp_path: Path) -> None:
    """#307 재리뷰(@danielcho02): marker가 Slice에서 멈추면 러너 소비 경계(SliceRef)와 FACTS·기록이
    절단 슬라이스를 full로 오인한다. SliceRef까지 살아야 하고, 정상 슬라이스는 False다.
    """
    document = _document([
        _slice("slice_0001", "A", {("main.c", 1), ("main.c", 5)}, _SOURCES, degraded=True),
        _slice("slice_0002", "B", {("helper.c", 3)}, _SOURCES),
    ])

    refs = slice_refs(load_slices(_write(tmp_path, document)))

    assert refs[0].degraded is True
    assert refs[1].degraded is False


# --- 분석 사실 — 슬라이서가 내는 절단·경계를 하네스가 버리지 않는다 --------------------------


def _backward(**extra) -> dict:
    """Case B `backward_ddg`의 `analysis` — 슬라이서(`slicer_backward.py`)가 쓰는 키 그대로다.
    `boundaries[]`는 `{reason, location: {file, line}}`이고 `file`은 저장소 상대 경로지만 Joern이
    보고한 그대로라 `lines`·`entry`와 달리 정리되지 않는다(아래 `_ENTRY_PARAMETER`)."""
    return {
        "flow_count": None,
        "truncated": False,
        "lines": [{"file": "main.c", "line": 5}],
        "node_count": 3,
        "state_count": 3,
        "dependency_count": 2,
        "boundaries": [],
        "truncation_reasons": [],
        **extra,
    }


# 실제 슬라이서 산출(`slices.json`)의 `entry_parameter` 경계에서 옮긴 이름 — 저장소 상대 경로인데
# Joern이 인클루드 디렉터리를 겹쳐 붙여 보고한 꼴이다.
_AVIO = "libavdevice/libavformat/libavformat/avio.h"
_ENTRY_PARAMETER = {"reason": "entry_parameter", "location": {"file": _AVIO, "line": 540}}
_DEPENDENCY_ROOT = {"reason": "dependency_root", "location": {"file": "main.c", "line": 5}}


def test_the_cut_and_the_boundaries_the_slicer_wrote_survive_load(tmp_path: Path) -> None:
    """슬라이서가 `truncated`·`analysis_kind`·`analysis.truncation_reasons`·`analysis.boundaries`를
    내는데 로더가 버렸다 — 하류가 「이 슬라이스는 잘렸다·역추적이 호출 간선 없는 파라미터에
    닿았다」를 못 알았다.
    사유는 정렬된 채(슬라이서도 정렬해 낸다), 경계는 낸 순서 그대로 (reason, file, line)이다."""
    item = _slice(
        "slice_0001", "B", {("main.c", 5)}, _SOURCES, truncated=True, analysis_kind="backward_ddg",
        analysis=_backward(
            truncated=True, truncation_reasons=["backward_depth", "backward_states", "call_depth"],
            boundaries=[_ENTRY_PARAMETER, _DEPENDENCY_ROOT],
        ),
    )

    (loaded,) = load_slices(_write(tmp_path, _document([item]))).slices

    assert loaded.analysis_kind == "backward_ddg"
    assert loaded.truncated is True
    assert loaded.truncation_reasons == ("backward_depth", "backward_states", "call_depth")
    assert loaded.boundaries == (
        ("entry_parameter", _AVIO, 540),
        ("dependency_root", "main.c", 5),
    )


def test_a_slice_without_the_analysis_facts_loads_with_defaults(tmp_path: Path) -> None:
    """옛 슬라이스(키가 없다)·`analysis`가 `null`인 슬라이스도 읽힌다 — 기본은 「절단 아님·경계
    없음」이 아니라 **모른다**를 뜻하는 빈 값이다(`analysis_kind`가 빈 문자열이면 슬라이서가
    안 적은 것이다)."""
    bare = _slice("slice_0001", "A", {("main.c", 1)}, _SOURCES)
    nulled = _slice("slice_0002", "B", {("main.c", 5)}, _SOURCES, analysis=None)

    for loaded in load_slices(_write(tmp_path, _document([bare, nulled]))).slices:
        assert loaded.analysis_kind == ""
        assert loaded.truncated is False
        assert loaded.truncation_reasons == ()
        assert loaded.boundaries == ()
        assert loaded.sinks_capped is False


def test_a_cut_case_a_slice_keeps_the_flag_though_it_has_no_reason_list(tmp_path: Path) -> None:
    """Case A(`parameter_paths`)의 `analysis`에는 `truncation_reasons`가 없다 — 슬라이서의 flows
    질의가 그 키를 안 낸다(`slicer_queries.flows_query`: `flow_count`·`truncated`·`sinks_capped`·
    `sinks_kept`·`lines`). 절단 Case A는 사유 없이 `truncated=True`이고 사유를 지어 채우지
    않는다 — 싱크 cap(`sinks_capped`)은 사유가 아니라 따로 싣는다."""
    item = _slice(
        "slice_0001", "A", {("main.c", 1)}, _SOURCES, truncated=True,
        analysis_kind="parameter_paths",
        analysis={"flow_count": 10, "truncated": True, "sinks_capped": True, "sinks_kept": 2063,
                  "lines": [{"file": "main.c", "line": 1}]},
    )

    (loaded,) = load_slices(_write(tmp_path, _document([item]))).slices

    assert (loaded.analysis_kind, loaded.truncated) == ("parameter_paths", True)
    assert loaded.truncation_reasons == () and loaded.boundaries == ()
    assert loaded.sinks_capped is True


def test_a_merged_slice_carries_the_analysis_of_every_member(tmp_path: Path) -> None:
    """클러스터링이 슬라이스를 합치면 `analysis`는 대표의 것이고 구성원별 분석은 `member_analysis`에
    있다(`slicer_source.cluster` — 대표 자신도 든다). 최상위 `truncated`는 구성원의 OR인데
    사유·경계를 대표 것만 읽으면 `truncated=True`인데 사유가 비거나, 흡수된 구성원이 닿은
    `entry_parameter`가 빠진다. 합친 항목은 슬라이서의 `cluster`가 만든다 — 모양을 손으로
    지어내지 않는다."""
    def member(sid: str, points: set, **analysis) -> dict:
        return _slice(
            sid, "B", points, _SOURCES, flow_count=None, truncated=analysis.get("truncated", False),
            analysis_kind="backward_ddg", analysis=_backward(**analysis),
            line_provenance={"data_dependency": [], "context": [],
                             "candidate_anchor": [list(p) for p in sorted(points)]},
        )

    representative = member(
        "slice_0001", {("main.c", n) for n in range(1, 8)}, truncated=True,
        truncation_reasons=["call_depth"], boundaries=[_DEPENDENCY_ROOT],
    )
    absorbed = member(
        "slice_0002", {("main.c", 5), ("main.c", 6)}, truncated=True,
        truncation_reasons=["backward_depth", "backward_states"],
        boundaries=[_ENTRY_PARAMETER, _DEPENDENCY_ROOT],
    )
    (merged,) = cluster([representative, absorbed], 0.95, _SOURCES)
    assert merged["absorbed"] == ["slice_0002"] and "member_analysis" in merged
    assert merged["analysis"]["truncation_reasons"] == ["call_depth"]  # 대표 자신의 분석뿐이다

    (loaded,) = load_slices(_write(tmp_path, _document([merged]))).slices

    assert loaded.truncated is True
    # 사유는 정렬(대표의 `call_depth`가 먼저 나왔어도), 경계는 처음 나온 순서에서 중복을 뺀다.
    assert loaded.truncation_reasons == ("backward_depth", "backward_states", "call_depth")
    assert loaded.boundaries == (
        ("dependency_root", "main.c", 5),
        ("entry_parameter", _AVIO, 540),
    )


@pytest.mark.parametrize(
    ("representative_capped", "absorbed_capped", "expected"),
    [(False, True, True), (True, False, True), (False, False, False)],
)
def test_a_merged_case_a_slice_keeps_the_sink_cap_of_any_member(
    tmp_path: Path, representative_capped: bool, absorbed_capped: bool, expected: bool
) -> None:
    """flows의 싱크 cap(`analysis.sinks_capped`)도 합친 슬라이스에서는 구성원 전부에서 OR로 모은다 —
    다른 필드(`truncated`·사유·경계)와 같은 길이고, 대표 것만 읽으면 흡수된 구성원이 낸 cap이
    빠지고 마지막 구성원 것만 읽으면 대표가 낸 cap이 빠진다(두 순서를 다 본다). 합친 항목은
    슬라이서의 `cluster`가 만든다."""
    def member(sid: str, points: set, capped: bool) -> dict:
        return _slice(
            sid, "A", points, _SOURCES, flow_count=1, truncated=False,
            analysis_kind="parameter_paths",
            analysis={"flow_count": 1, "truncated": False, "sinks_capped": capped,
                      "sinks_kept": 2063, "lines": []},
            line_provenance={"data_dependency": [], "context": [],
                             "candidate_anchor": [list(p) for p in sorted(points)]},
        )

    representative = member(
        "slice_0001", {("main.c", n) for n in range(1, 8)}, capped=representative_capped
    )
    absorbed = member("slice_0002", {("main.c", 5), ("main.c", 6)}, capped=absorbed_capped)
    (merged,) = cluster([representative, absorbed], 0.95, _SOURCES)
    assert merged["analysis"]["sinks_capped"] is representative_capped  # 대표 자신의 분석뿐이다

    (loaded,) = load_slices(_write(tmp_path, _document([merged]))).slices

    assert loaded.sinks_capped is expected


def test_the_analysis_facts_reach_the_sliceref_boundary(tmp_path: Path) -> None:
    """러너 소비 경계(`SliceRef`)까지 살아야 한다 — `Slice`에서 멈추면 게이트가 읽을 데가 없다
    (`degraded`·`provenance`가 그 경계를 넘는 것과 같은 길). 없는 슬라이스는 기본값이다."""
    cut = _slice(
        "slice_0001", "B", {("main.c", 5)}, _SOURCES, truncated=True, analysis_kind="backward_ddg",
        analysis=_backward(
            truncated=True, truncation_reasons=["call_depth"], boundaries=[_ENTRY_PARAMETER]
        ),
    )
    old = _slice("slice_0002", "A", {("main.c", 1)}, _SOURCES)
    capped = _slice(
        "slice_0003", "A", {("main.c", 1)}, _SOURCES, analysis_kind="parameter_paths",
        analysis={"flow_count": 1, "truncated": False, "sinks_capped": True,
                  "sinks_kept": 2063, "lines": []},
    )

    refs = slice_refs(load_slices(_write(tmp_path, _document([cut, old, capped]))))

    assert refs[0].analysis_kind == "backward_ddg"
    assert refs[0].truncated is True
    assert refs[0].truncation_reasons == ("call_depth",)
    assert refs[0].boundaries == (("entry_parameter", _AVIO, 540),)
    assert (refs[1].analysis_kind, refs[1].truncated) == ("", False)
    assert refs[1].truncation_reasons == () and refs[1].boundaries == ()
    assert [ref.sinks_capped for ref in refs] == [False, False, True]


@pytest.mark.parametrize(
    "analysis",
    [
        pytest.param([], id="analysis-is-a-list"),
        pytest.param({"boundaries": [{"reason": "entry_parameter"}]}, id="boundary-no-location"),
        pytest.param(
            {"boundaries": [{"reason": "x", "location": {"file": "a.c", "line": "n"}}]},
            id="boundary-line-not-a-number",
        ),
        # 강제 변환이 사실을 지어내던 모양 — 문자열 사유는 글자마다 사유 하나가 됐고(서로 다른
        # 글자 12개), 문자열 `"false"`는 `bool()`로 참이 됐다.
        pytest.param({"truncation_reasons": "backward_depth"}, id="reasons-is-a-string"),
        pytest.param({"truncation_reasons": [1]}, id="reason-not-a-string"),
        pytest.param({"sinks_capped": "false"}, id="cap-is-a-string"),
        pytest.param({"sinks_capped": 1}, id="cap-is-a-number"),
        pytest.param({"boundaries": _ENTRY_PARAMETER}, id="boundaries-is-an-object"),
    ],
)
def test_a_malformed_analysis_is_refused_at_load(tmp_path: Path, analysis: object) -> None:
    """읽는 필드의 모양이 틀리면 run이 시작되기 **전에** 이유를 대고 멈춘다(모듈 docstring
    「로드에서 죽는다」). 조용히 비워 두거나 강제 변환하면 「경계가 없다」·「잘렸다」와 「못
    읽었다」가 같은 입력이 된다."""
    item = _slice("slice_0001", "B", {("main.c", 5)}, _SOURCES, analysis=analysis)

    with pytest.raises(SliceInputError, match="slice-v2가 아니다"):
        load_slices(_write(tmp_path, _document([item])))


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"truncated": "false"}, id="truncated-is-a-string"),
        pytest.param({"truncated": 0}, id="truncated-is-a-number"),
        pytest.param({"analysis_kind": 1}, id="kind-not-a-string"),
    ],
)
def test_malformed_top_level_analysis_facts_are_refused_at_load(
    tmp_path: Path, fields: dict
) -> None:
    """최상위 `truncated`·`analysis_kind`도 같다 — `bool("false")`가 참이라 강제 변환하면 깨진
    입력이 「잘렸다」로 읽힌다."""
    item = _slice("slice_0001", "B", {("main.c", 5)}, _SOURCES, **fields)

    with pytest.raises(SliceInputError, match="slice-v2가 아니다"):
        load_slices(_write(tmp_path, _document([item])))


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


# --- run 전 일괄 렌더 ---------------------------------------------------------


def test_render_slices_renders_each_slice_whose_entry_file_is_in_the_tree(tmp_path: Path) -> None:
    """run 전에 전부 렌더해 id로 찾게 한다. entry 파일이 대상 트리에 없는 슬라이스는 뺀다 —
    러너가 그 슬라이스를 타깃으로 못 세우고 `unmatched_slices`로 센다."""
    root = _repo(tmp_path, {"main.c": _MAIN.encode()})
    kept = _slice("slice_0001", "A", {("main.c", 1), ("main.c", 5)}, _SOURCES)
    stray = _slice("slice_0002", "B", {("gone.c", 1)}, {"gone.c": ["int gone;"]})
    loaded = load_slices(_write(tmp_path, _document([kept, stray])))

    assert render_slices(loaded, root) == {
        "slice_0001": "/* ===== main.c ===== */\n"
        "1| int parse(char *buf, int n)\n"
        "5|     memcpy(tmp, buf, k);"
    }


def test_render_slices_refuses_drift_instead_of_skipping_it(tmp_path: Path) -> None:
    edited = _MAIN.replace("memcpy(tmp, buf, k);", "memcpy(tmp, buf, 16);")
    root = _repo(tmp_path, {"main.c": edited.encode()})
    loaded = load_slices(
        _write(tmp_path, _document([_slice("slice_0001", "A", {("main.c", 5)}, _SOURCES)]))
    )

    with pytest.raises(SliceInputError, match="drift"):
        render_slices(loaded, root)


def test_render_slices_refuses_a_slice_file_the_tree_does_not_have(tmp_path: Path) -> None:
    """entry는 있는데 슬라이스가 걸친 다른 파일이 하네스 사본에 없다 — 슬라이서와 다른 트리다."""
    root = _repo(tmp_path, {"main.c": _MAIN.encode()})
    item = _slice("slice_0001", "A", {("main.c", 1), ("helper.c", 3)}, _SOURCES)
    item["entry"] = {"file": "main.c", "line": 1, "symbol": "parse"}
    loaded = load_slices(_write(tmp_path, _document([item])))

    with pytest.raises(SliceInputError, match="helper.c"):
        render_slices(loaded, root)


@pytest.mark.parametrize("entry_file", ["main.c", "../outside.c"])
def test_render_slices_never_reads_outside_the_target_root(tmp_path: Path, entry_file: str) -> None:
    """`slices.json`의 경로는 하네스가 만든 값이 아니다 — 루트 밖을 가리키면 못 믿는 입력이다.

    entry가 루트 밖인 경우와, entry는 안인데 슬라이스의 다른 줄이 루트 밖인 경우 둘 다다.
    """
    root = _repo(tmp_path, {"main.c": _MAIN.encode()})
    (tmp_path / "outside.c").write_text("int secret;\n", encoding="utf-8")
    sources = {**_SOURCES, "../outside.c": ["int secret;"]}
    item = _slice("slice_0001", "A", {("main.c", 1), ("../outside.c", 1)}, sources)
    item["entry"] = {"file": entry_file, "line": 1, "symbol": "parse"}
    loaded = load_slices(_write(tmp_path, _document([item])))

    with pytest.raises(SliceInputError, match="outside.c"):
        render_slices(loaded, root)


def test_a_forward_slice_f_loads_with_the_source_end_given(tmp_path: Path) -> None:
    """`tools/slicer_v2/slicer_target.py`의 정방향 슬라이스(`case: "F"`)는 source에서 시작해 앞으로
    자른 것이라 쥐여 준 끝이 `source`다. 하네스가 `case`에서 읽는 것은 이 `given_end`뿐이다 —
    그 밖의 case는 여전히 거부한다."""
    forward = _slice("forward", "F", {("main.c", 1), ("main.c", 5)}, _SOURCES, direction="forward")
    loaded = load_slices(_write(tmp_path, _document([forward])))
    assert [s.given_end for s in loaded.slices] == ["source"]
    refs = slice_refs(loaded)
    assert refs[0].given_end == "source"
    # Like Case B, an F slice does not take its entry's symbol for the enclosing function.
    assert refs[0].location.symbol is None

    with pytest.raises(SliceInputError):
        load_slices(_write(tmp_path, _document([_slice("x", "G", {("main.c", 1)}, _SOURCES)])))


def test_the_input_evidence_and_input_context_keys_do_not_stop_the_load(tmp_path: Path) -> None:
    """`analysis`는 읽는 키(절단 사유·경계·싱크 cap)만 쓰고 나머지는 둔다 — 슬라이서 v2가 더하는
    `input_evidence`(와 앞으로 더할 키)가 로드를 막지 않아야 한다. ⏵ 2026-10-14 — `input_context`는
    이제 **읽는다**(S20 Phase 13 · `tests/test_source_input_context.py`가 생산자 출력으로 문다):
    모양이 틀리면 로드가 죽으므로 여기서는 생산자의 `none_selected` 모양(읽지 않는 키 포함)을 든다.
    읽지 않는 키는 여전히 로드를 막지 않는다."""
    extra = {
        "input_evidence": {"version": 1, "status": "ok", "methods": [], "calls": []},
        "input_context": {
            "version": 1, "caps": {}, "caps_measured": False, "status": "none_selected",
            "basis": "syntax", "evidence_status": "unavailable", "cap_hit": False,
            "cap_reasons": [], "identical_to_auditor_input": True, "carried": [],
            "input_states": [], "linked_methods": None, "unreadable_files": [], "contract": {},
            "functions": [], "carried_decoders": [], "dispatches": [],
            "some_future_field": 1,
        },
        "some_future_key": {"anything": [1, 2, 3]},
    }
    item = _slice(
        "slice_0001", "B", {("main.c", 5)}, _SOURCES,
        analysis=_backward(truncation_reasons=["backward_depth"], **extra),
    )
    loaded = load_slices(_write(tmp_path, _document([item])))
    assert loaded.slices[0].truncation_reasons == ("backward_depth",)
