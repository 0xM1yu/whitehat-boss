"""D97 / #377 (b) — 과제 설명이 슬라이서와 러너의 후보 컷 순서를 정한다. 순서만, 후보는 그대로.

설명 문자열은 arvo:1065의 실제 설명이다(2026-10-05 run 프롬프트의 `vuln_description`).
"""

from types import SimpleNamespace

import pytest

import slicer_description
from src.adapters.slices import SliceRef
from src.orchestrator import description_match, runner
from src.schemas.common import Location
from tests.test_pipeline_e2e import _FIXTURE_INDEX, _events, _of, _plan, _run, _with_index

ARVO_1065 = "A bug in glibc/regex/msan causes regexec to return 0 but not initialize pmatch."
TOKENS = description_match.description_tokens(ARVO_1065)


def test_the_arvo_1065_description_yields_its_identifiers() -> None:
    assert TOKENS == {"glibc", "regex", "msan", "regexec", "pmatch"}
    assert description_match.description_tokens("") == frozenset()
    assert description_match.description_tokens(None) == frozenset()


def test_rank_is_exact_over_part_over_file_over_nothing() -> None:
    rank = description_match.description_rank
    assert rank(TOKENS, "posix/other.c", "file_regexec") == 2  # `_` 조각 regexec
    assert rank(TOKENS, "posix/other.cc", "Matcher::regexec") == 2  # C++ 한정 이름의 `::` 조각
    assert rank(TOKENS, "posix/regex.c", "unrelated") == 1  # 파일 이름만
    assert rank(TOKENS, "posix/other.c", "regexec") == 3  # 심볼 전체 이름 일치
    assert rank(TOKENS, "posix/regex.c", "pmatch") == 3  # 둘 다면 심볼이 이긴다
    # 반대 방향: 설명이 `file_regexec`를 통째로 쓰고 심볼은 `regexec`다 — 조각이라 2, 3이 아니다
    whole = description_match.description_tokens("crash in file_regexec today")
    assert rank(whole, "posix/other.c", "regexec") == 2
    assert rank(whole, "posix/other.c", "file_regexec") == 3
    assert rank(whole, "posix/regexec.c", "helper") == 1  # 파일도 그 조각으로
    assert rank(TOKENS, "posix/other.c", "regexe") == 0  # 부분 문자열은 일치가 아니다
    assert rank(TOKENS, "REGEXEC.C", None) == 1  # 대소문자 무시, 심볼 없음(폴백)
    assert rank(frozenset(), "regex.c", "regexec") == 0  # 설명 없음


def test_the_slicer_and_the_runner_agree_on_tokens() -> None:
    """`src`는 슬라이서를 import하지 않아 규칙이 두 벌이다 — 같은 토큰을 내야 한다."""
    assert slicer_description.STOPWORDS == description_match.STOPWORDS
    assert slicer_description.MIN_TOKEN == description_match.MIN_TOKEN
    samples = (ARVO_1065, "", "ndpi_search_tcp() leaks; see Foo::bar and x86_64 [A_B]", "한글 only")
    for text in samples:
        theirs = description_match.description_tokens(text)
        assert slicer_description.description_tokens(text) == theirs
        assert slicer_description.expand(theirs) == description_match._expand(theirs)
    assert "regexec" in slicer_description.expand(frozenset({"file_regexec", "return_value"}))
    assert "return" not in slicer_description.expand(frozenset({"return_value"}))  # 불용어 조각


def test_the_slicer_picks_repo_files_by_name_without_reading_them(tmp_path) -> None:
    for name in ("regex.c", "sub/regexec.h", "sub/other.c", "notes/regex.txt", ".git/pmatch.c"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int x;\n", encoding="utf-8")
    assert slicer_description.matching_files(TOKENS, tmp_path) == {"regex.c", "sub/regexec.h"}
    assert slicer_description.matching_files(frozenset(), tmp_path) == set()
    # 설명이 `file_regexec`를 통째로 써도 `regexec.h`가 닿는다
    whole = slicer_description.description_tokens("crash in file_regexec")
    assert slicer_description.matching_files(whole, tmp_path) == {"sub/regexec.h"}


# --- 러너: `_order_and_cut` ---


class _Trace:
    def __init__(self) -> None:
        self.events: list[tuple[object, dict]] = []

    def emit(self, kind: object, **fields: object) -> None:
        self.events.append((kind, fields))


def _ctx(description: str | None, candidate_max: int) -> SimpleNamespace:
    return SimpleNamespace(
        vuln_description=description,
        config=SimpleNamespace(thresholds=SimpleNamespace(candidate_max=candidate_max)),
        trace=_Trace(),
    )


def _plan_slices(monkeypatch: pytest.MonkeyPatch, ctx: SimpleNamespace, entries: list[Location]):
    """신호가 없는(Level 1) 슬라이스 타깃을 세우고 자른다."""
    monkeypatch.setattr(runner, "_artifact_signals", lambda *_a: ((), [], False))
    artifacts = [SimpleNamespace(path=e.file) for e in entries]
    refs = tuple(
        SliceRef(
            slice_id=f"slice_{i:04d}",
            location=e,
            given_end="source",
            entry=e,
            lines=(),
            degraded=False,
        )
        for i, e in enumerate(entries, 1)
    )
    plan = runner._TargetPlan(analyzable_artifacts=len(artifacts))
    state = runner._Accumulator()
    runner._plan_slice_targets(ctx, plan, artifacts, None, refs)  # type: ignore[arg-type]
    runner._order_and_cut(ctx, plan, state)  # type: ignore[arg-type]
    return plan, state


_ENTRIES = [
    Location(file="a.c", line_start=1, symbol="quiet"),
    Location(file="m.c", line_start=1, symbol="file_regexec"),
    Location(file="regex.c", line_start=1, symbol="helper"),
    Location(file="z.c", line_start=1, symbol="zzz"),
]


def test_a_description_match_survives_the_cut_that_path_order_would_make(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain, _ = _plan_slices(monkeypatch, _ctx(None, 2), _ENTRIES)
    assert [t.location.file for t in plain.targets] == ["a.c", "m.c"]  # 경로순 — 종전

    ctx = _ctx(ARVO_1065, 2)
    plan, state = _plan_slices(monkeypatch, ctx, _ENTRIES)
    # 심볼 조각 일치(2) m.c가 파일 일치(1) regex.c 앞이고, 둘 다 경로순 a.c를 밀어낸다.
    assert [t.location.file for t in plan.targets] == ["m.c", "regex.c"]
    assert state.deferred == 2
    ((_, fields),) = ctx.trace.events[:1]
    assert fields["desc_exact_matches"] == 0
    assert (fields["desc_part_matches"], fields["desc_file_matches"]) == (1, 1)
    assert fields["desc_matches_deferred"] == 0


def test_matches_cut_by_candidate_max_are_counted_as_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(ARVO_1065, 1)
    plan, _ = _plan_slices(monkeypatch, ctx, _ENTRIES)
    assert [t.location.file for t in plan.targets] == ["m.c"]
    ((_, fields),) = ctx.trace.events[:1]
    assert fields["desc_matches_deferred"] == 1  # regex.c(파일 일치)가 컷 밖


def test_a_description_that_matches_nothing_keeps_the_same_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def order(description: str | None) -> list[tuple]:
        plan, _ = _plan_slices(monkeypatch, _ctx(description, 3), _ENTRIES)
        return [(t.location, t.slice_id) for t in plan.targets]

    assert order("Nothing here names a thing.") == order(None) == order("")


def test_the_static_signal_still_comes_before_the_description() -> None:
    hit = runner._Target(
        artifact=None,
        location=Location(file="z.c", symbol="zzz"),
        evidences=(),  # type: ignore[arg-type]
        signal_rank=2,
        desc_rank=0,
    )
    named = runner._Target(
        artifact=None,
        location=Location(file="a.c", symbol="regexec"),
        evidences=(),  # type: ignore[arg-type]
        signal_rank=1,
        desc_rank=2,
    )
    plan = runner._TargetPlan(collected=[named, hit])
    runner._order_and_cut(_ctx(ARVO_1065, 1), plan, runner._Accumulator())  # type: ignore[arg-type]
    assert plan.targets == [hit]


# --- 러너: 심볼 인덱스 경로 전체(`cli.execute`) ---


def _no_signal_target(tmp_path):  # noqa: ANN001, ANN202
    target = tmp_path / "src"
    target.mkdir()
    for name, body in (
        ("aaa.c", "int quiet(void)\n{\n    return 0;\n}\n"),
        ("zzz.c", "int file_regexec(void)\n{\n    return 1;\n}\n"),
    ):
        (target / name).write_text(body, encoding="utf-8")
    index = {
        **_FIXTURE_INDEX,
        "files": ["aaa.c", "zzz.c"],
        "symbols": [
            {"name": "quiet", "file": "aaa.c", "line_start": 1, "line_end": 4},
            {"name": "file_regexec", "file": "zzz.c", "line_start": 1, "line_end": 4},
        ],
    }
    return target, index


def test_the_symbol_index_path_ranks_by_the_run_description(tmp_path) -> None:  # noqa: ANN001
    target, index = _no_signal_target(tmp_path)

    def survivors(name: str, description: str | None) -> list[str]:
        result, trace_path = _run(
            tmp_path,
            name=name,
            target=target,
            before=_with_index(index),
            candidate_max=1,
            vuln_description=description,
        )
        assert _plan(trace_path)["signal_artifacts"] == 0
        return [e["data"]["symbol"] for e in _of(_events(trace_path), "target_planned")]

    assert survivors("none", None) == ["quiet"]
    assert survivors("miss", "An unrelated overflow.") == ["quiet"]
    assert survivors("hit", ARVO_1065) == ["file_regexec"]
    _, trace_path = _run(
        tmp_path,
        name="count",
        target=target,
        before=_with_index(index),
        candidate_max=1,
        vuln_description=ARVO_1065,
    )
    plan = _plan(trace_path)
    assert (plan["desc_exact_matches"], plan["desc_part_matches"]) == (0, 1)
    assert plan["desc_file_matches"] == 0
    assert plan["desc_matches_deferred"] == 0
