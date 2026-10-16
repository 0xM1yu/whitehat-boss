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
    assert rank(whole, "posix/other.c", "file_open") == 0  # 확장 조각 `file`은 심볼 조각과 안 겨룸
    assert rank(whole, "posix/regexec.c", "helper") == 1  # 파일도 그 조각으로
    assert rank(TOKENS, "posix/other.c", "regexe") == 0  # 부분 문자열은 일치가 아니다
    assert rank(TOKENS, "REGEXEC.C", None) == 1  # 대소문자 무시, 심볼 없음(폴백)
    assert rank(frozenset(), "regex.c", "regexec") == 0  # 설명 없음


def test_the_slicer_and_the_runner_agree_on_tokens() -> None:
    """`src`는 슬라이서를 import하지 않아 규칙이 두 벌이다 — 같은 토큰을 내야 한다."""
    assert slicer_description.STOPWORDS == description_match.STOPWORDS
    assert slicer_description.MIN_TOKEN == description_match.MIN_TOKEN
    samples = (
        ARVO_1065,
        "",
        "ndpi_search_tcp() leaks; see Foo::bar and x86_64 [A_B]",
        "한글 only",
        "return_value leaks in file_regexec",  # 불용어 조각 `return`은 둘 다 뺀다
    )
    for text in samples:
        theirs = description_match.description_tokens(text)
        assert slicer_description.description_tokens(text) == theirs
        assert slicer_description.expand(theirs) == description_match._expand(theirs)
    assert "regexec" in slicer_description.expand(frozenset({"file_regexec", "return_value"}))
    assert "return" not in slicer_description.expand(frozenset({"return_value"}))  # 불용어 조각
    assert "return" not in description_match._expand(frozenset({"return_value"}))


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


def _ctx(description: str | None, candidate_max: int, reserve: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        vuln_description=description,
        config=SimpleNamespace(
            thresholds=SimpleNamespace(candidate_max=candidate_max, candidate_desc_reserve=reserve)
        ),
        trace=_Trace(),
    )


def _plan_slices(monkeypatch: pytest.MonkeyPatch, ctx: SimpleNamespace, entries: list[Location]):
    """정적 신호를 비운(`_artifact_signals` 없음) 슬라이스 타깃을 세우고 자른다."""
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


# --- 러너: 설명 일치 예약(`candidate_desc_reserve`) ---


def _t(file: str, signal: int, desc: int, symbol: str = "f") -> runner._Target:
    return runner._Target(
        artifact=None,  # type: ignore[arg-type]
        location=Location(file=file, line_start=1, symbol=symbol),
        evidences=(),
        signal_rank=signal,
        desc_rank=desc,
    )


def _cut(
    items: list[runner._Target], limit: int, reserve: int
) -> tuple[SimpleNamespace, runner._TargetPlan, runner._Accumulator]:
    ctx = _ctx(ARVO_1065, limit, reserve)
    plan = runner._TargetPlan(collected=list(items))
    state = runner._Accumulator()
    runner._order_and_cut(ctx, plan, state)  # type: ignore[arg-type]
    return ctx, plan, state


def _files(items: list[runner._Target]) -> list[str]:
    return [t.location.file for t in items]


def _rehearsal() -> list[runner._Target]:
    """리허설 꼴 — 신호 후보 9개(`s1`..`s9`)와, 신호 없이 설명이 심볼을 부른 후보 하나."""
    return [_t(f"s{i}.c", 2, 0) for i in range(1, 10)] + [_t("funcs.c", 0, 2, "file_regexec")]


def test_a_description_match_survives_when_signal_candidates_fill_the_cut() -> None:
    ctx, plan, state = _cut(_rehearsal(), 8, 2)
    # 신호 7개 + 설명 일치 1개. 설명 일치는 키 순서상 맨 뒤라 목록 끝이다(ID 발급 순서 = 키 순서).
    assert _files(plan.targets) == [f"s{i}.c" for i in range(1, 8)] + ["funcs.c"]
    assert state.deferred == 2  # s8·s9가 잘린다
    ((_, plan_event), *rest) = ctx.trace.events
    assert (plan_event["planned"], plan_event["deferred"]) == (8, 2)
    assert (plan_event["candidate_desc_reserve"], plan_event["desc_reserved"]) == (2, 1)
    assert plan_event["desc_matches_deferred"] == 0
    planned = [f for k, f in rest if k.value == "target_planned"]
    skipped = [f for k, f in rest if k.value == "target_skipped"]
    assert [f["file"] for f in skipped] == ["s8.c", "s9.c"]
    assert [(f["signal_rank"], f["desc_rank"], f["reason"]) for f in skipped] == [
        (2, 0, "over_candidate_max")
    ] * 2
    assert [f["file"] for f in planned if f["desc_reserved"]] == ["funcs.c"]  # 예약이 들인 것만
    assert {(f["signal_rank"], f["desc_rank"]) for f in planned} == {(2, 0), (0, 2)}
    # `target_plan.desc_reserved`는 `target_planned.desc_reserved`의 참 개수와 같은 정의다.
    assert plan_event["desc_reserved"] == sum(f["desc_reserved"] for f in planned)


def test_without_the_reserve_the_same_run_loses_the_match() -> None:
    _, plan, state = _cut(_rehearsal(), 8, 0)
    assert _files(plan.targets) == [f"s{i}.c" for i in range(1, 9)]
    assert state.deferred == 2


def test_only_the_first_reserve_description_matches_are_reserved_in_key_order() -> None:
    items = [_t(f"s{i}.c", 2, 0) for i in range(1, 9)] + [
        _t("zz.c", 0, 2, "part"),
        _t("aa.c", 0, 3, "exact"),  # -desc_rank가 먼저라 zz.c·bb.c보다 앞
        _t("bb.c", 0, 2, "part"),
    ]
    ctx, plan, _ = _cut(items, 8, 2)
    # 예약은 키 순서 앞 둘(aa.c 3, bb.c 2)이고 zz.c는 못 든다 — 나머지 6자리는 신호 후보 s1..s6.
    assert _files(plan.targets) == [f"s{i}.c" for i in range(1, 7)] + ["aa.c", "bb.c"]
    assert ctx.trace.events[0][1]["desc_reserved"] == 2
    assert "zz.c" in [f["file"] for k, f in ctx.trace.events if k.value == "target_skipped"]


def test_a_file_name_only_match_is_not_reserved() -> None:
    items = [_t(f"s{i}.c", 2, 0) for i in range(1, 9)] + [_t("regex.c", 0, 1, "helper")]
    _, plan, state = _cut(items, 8, 2)
    assert _files(plan.targets) == [f"s{i}.c" for i in range(1, 9)]
    assert state.deferred == 1


@pytest.mark.parametrize(
    ("items", "limit", "reserve", "expected"),
    [
        # 설명 일치 후보가 없다 - 종전 컷(키 앞 limit개).
        ([_t(f"s{i}.c", 2, 0) for i in (3, 1, 2)], 2, 2, ["s1.c", "s2.c"]),
        # 예약이 0이다 - 설명 일치가 있어도 종전 컷.
        ([_t("a.c", 2, 0), _t("b.c", 0, 3, "x"), _t("c.c", 0, 0)], 1, 0, ["a.c"]),
        # 후보가 limit 이하다 - 전부 든다.
        ([_t("b.c", 0, 3, "x"), _t("a.c", 2, 0)], 5, 2, ["a.c", "b.c"]),
        # 설명 일치 후보가 이미 컷 안이다 - 예약이 아무것도 바꾸지 않는다.
        ([_t("a.c", 2, 0), _t("b.c", 1, 3, "x"), _t("c.c", 0, 0)], 2, 2, ["a.c", "b.c"]),
    ],
)
def test_the_reserve_changes_nothing_when_it_has_nothing_to_do(
    items: list[runner._Target], limit: int, reserve: int, expected: list[str]
) -> None:
    ctx, plan, _ = _cut(items, limit, reserve)
    assert _files(plan.targets) == expected
    assert ctx.trace.events[0][1]["desc_reserved"] == 0


def test_the_cut_list_stays_in_key_order() -> None:
    _, plan, _ = _cut(_rehearsal(), 8, 2)
    assert plan.targets == sorted(plan.targets, key=runner._cut_key)


def test_the_reserve_cannot_exceed_the_cut() -> None:
    items = [_t("a.c", 2, 0), _t("b.c", 0, 2, "x"), _t("c.c", 0, 3, "y")]
    targets, deferred, _, _ = runner._cut_with_reserve(
        sorted(items, key=runner._cut_key), 1, 5
    )
    assert _files(targets) == ["c.c"]
    assert len(deferred) == 2


def test_the_reserve_must_fit_inside_the_cut() -> None:
    from pydantic import ValidationError

    from src.config import load_config

    thresholds = load_config("dev_fake").thresholds
    assert thresholds.candidate_desc_reserve == 2  # budgets.yaml이 든다
    assert thresholds.flatten()["candidate_desc_reserve"] == 2.0
    with pytest.raises(ValidationError):
        thresholds.transition(candidate_desc_reserve=-1)
    with pytest.raises(ValidationError):
        thresholds.transition(candidate_desc_reserve=thresholds.candidate_max + 1)
    off = thresholds.transition(candidate_desc_reserve=0)
    assert "candidate_desc_reserve" not in off.flatten()  # 기본(0)은 키를 안 만든다
    assert thresholds.transition(candidate_desc_reserve=thresholds.candidate_max)


def test_the_symbol_index_path_reserves_a_description_match(tmp_path) -> None:  # noqa: ANN001
    """종단: `cli.execute` 배선 전체에서 세 이벤트가 새 값을 싣는다."""
    target, index = _no_signal_target(tmp_path)
    _, trace_path = _run(
        tmp_path,
        name="reserve",
        target=target,
        before=_with_index(index),
        candidate_max=1,
        vuln_description=ARVO_1065,
    )
    events = _events(trace_path)
    plan = _plan(trace_path)
    assert plan["candidate_desc_reserve"] == 1  # `_run`이 컷에 맞춰 min(2, 1)로 줄인다
    assert plan["desc_reserved"] == 0  # 예약 없이도 키 순서로 이미 든다(신호가 다 0이라)
    planned = _of(events, "target_planned")
    assert [e["data"]["symbol"] for e in planned] == ["file_regexec"]
    assert (planned[0]["data"]["signal_rank"], planned[0]["data"]["desc_rank"]) == (0, 2)
    assert planned[0]["data"]["desc_reserved"] is False
    cut = [e["data"] for e in _of(events, "target_skipped")]
    skipped = [d for d in cut if d["reason"] == "over_candidate_max"]
    assert [(d["symbol"], d["signal_rank"], d["desc_rank"]) for d in skipped] == [("quiet", 0, 0)]


def test_reserved_slots_go_to_distinct_functions() -> None:
    """슬라이스 둘이 같은 함수(entry)를 가리켜도 예약 두 자리를 다 먹지 않는다."""
    twin_a = _t("m.c", 0, 2, "fn")
    twin_b = _t("m.c", 0, 2, "fn")  # 같은 (file, symbol) - 다른 슬라이스라고 보면 된다
    other = _t("n.c", 0, 2, "other")
    items = [_t(f"s{i}.c", 2, 0) for i in range(1, 9)] + [twin_a, twin_b, other]
    ctx, plan, _ = _cut(items, 8, 2)
    assert _files(plan.targets) == [f"s{i}.c" for i in range(1, 7)] + ["m.c", "n.c"]
    assert any(t is twin_a for t in plan.targets)  # 첫째가 예약을 받는다
    assert not any(t is twin_b for t in plan.targets)  # 둘째는 자리가 없다
    assert ctx.trace.events[0][1]["desc_reserve_filled"] == 2
    # 나머지 경로로는 들어갈 수 있다 - 신호 후보가 자리를 안 채우면 둘째도 든다.
    _, plan, _ = _cut([twin_a, twin_b, other], 3, 2)
    assert len(plan.targets) == 3


@pytest.mark.parametrize(
    ("items", "limit", "reserve", "filled", "promoted"),
    [
        (_rehearsal(), 8, 2, 1, 1),  # 예약이 들였다
        ([_t("a.c", 2, 0), _t("b.c", 1, 3, "x"), _t("c.c", 0, 0)], 2, 2, 1, 0),  # 이미 앞 순위
        ([_t(f"s{i}.c", 2, 0) for i in (3, 1, 2)], 2, 2, 0, 0),  # 설명 일치 없음
        (_rehearsal(), 8, 0, 0, 0),  # 예약 0
    ],
)
def test_desc_reserve_filled_counts_the_pick_and_desc_reserved_counts_the_effect(
    items: list[runner._Target], limit: int, reserve: int, filled: int, promoted: int
) -> None:
    ctx, _, _ = _cut(items, limit, reserve)
    plan_event = ctx.trace.events[0][1]
    assert (plan_event["desc_reserve_filled"], plan_event["desc_reserved"]) == (filled, promoted)


# --- 슬라이스 경로: 걸친 함수로 매긴다 (#693) ---
#
# 실형태 출처: run_20261015_0001(commit 856528d)의 `slices.json` `slice_0524`와 그 run의
# `scratch/index/index.json`(@sumin0218 handoff, 저장소 밖). entry는 `funcs.c:76`(감싸는 함수
# `file_vprintf` 56-81)이고 걸친 줄 `funcs.c:508`이 `file_regexec`(507-513) 안이다. 그 run은 이
# 슬라이스를 `desc_rank` 0으로 잘랐다(`target_skipped` · `target_plan.desc_part_matches` 0).
_FUNCS = "src-vul/file/src/funcs.c"
_SOFT = "src-vul/file/src/softmagic.c"
_APPR = "src-vul/file/src/apprentice.c"
_INDEX = {
    _FUNCS: [
        Location(file=_FUNCS, symbol="file_vprintf", line_start=56, line_end=81),
        Location(file=_FUNCS, symbol="file_regcomp", line_start=491, line_end=505),
        Location(file=_FUNCS, symbol="file_regexec", line_start=507, line_end=513),
    ],
    _SOFT: [Location(file=_SOFT, symbol="match", line_start=162, line_end=402)],
    # 같은 run에서 `FILE_REGEX`·`REGEX_OFFSET_START`가 본문에 든 함수(apprentice.c `getvalue`).
    _APPR: [Location(file=_APPR, symbol="getvalue", line_start=2564, line_end=2634)],
}


def _ref(slice_id: str, entry: Location, lines: tuple[tuple[str, int], ...]) -> SliceRef:
    return SliceRef(
        slice_id=slice_id, location=entry, given_end="source", entry=entry, lines=lines,
        degraded=False,
    )


_GT = _ref(
    "slice_0524",
    Location(file=_FUNCS, line_start=76, symbol="file_vprintf"),
    ((_FUNCS, 76), (_FUNCS, 508), (_SOFT, 365)),
)
_MACRO_ONLY = _ref(
    "slice_0029", Location(file=_APPR, line_start=2564, symbol="getvalue"), ((_APPR, 2600),)
)


def _plan_refs(
    monkeypatch: pytest.MonkeyPatch, refs: list[SliceRef], *, index: dict | None = _INDEX,
    limit: int = 8, reserve: int = 2,
) -> tuple[SimpleNamespace, runner._TargetPlan]:
    # 그 run처럼 `apprentice.c`에는 정적 신호가 있다(planned 8개가 signal_rank 1·2) — 신호 없는
    # 설명과 맞는 슬라이스는 키 순서로는 못 들고 예약으로만 든다.
    monkeypatch.setattr(
        runner, "_artifact_signals", lambda _c, artifact, _g: ((), [], artifact.path == _APPR)
    )
    ctx = _ctx(ARVO_1065, limit, reserve)
    ctx.symbols_for = lambda file: None if index is None else index.get(file, [])
    files = {r.location.file for r in refs}
    plan = runner._TargetPlan(analyzable_artifacts=len(files))
    artifacts = [SimpleNamespace(path=f) for f in sorted(files)]
    runner._plan_slice_targets(ctx, plan, artifacts, None, tuple(refs))  # type: ignore[arg-type]
    runner._order_and_cut(ctx, plan, runner._Accumulator())  # type: ignore[arg-type]
    return ctx, plan


def test_a_slice_is_ranked_by_a_function_it_spans_not_only_its_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, plan = _plan_refs(monkeypatch, [_GT])
    (target,) = plan.collected
    assert target.desc_rank == 2  # `file_regexec`의 조각 `regexec`가 설명 토큰이다
    assert target.desc_symbol == Location(
        file=_FUNCS, symbol="file_regexec", line_start=507, line_end=513
    )
    assert target.location.symbol == "file_vprintf"  # 타깃 위치는 그대로 entry다


def test_the_spanned_match_takes_a_reserved_seat_from_path_ordered_slices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """그 run의 꼴 — 경로 사전순 앞(`apprentice.c`) 슬라이스가 컷을 다 채운다."""
    filler = [
        _ref(f"slice_{i:04d}", Location(file=_APPR, line_start=i, symbol=f"f{i}"), ())
        for i in range(1, 10)
    ]
    ctx, plan = _plan_refs(monkeypatch, [*filler, _GT])
    assert "slice_0524" in [t.slice_id for t in plan.targets]
    plan_event = ctx.trace.events[0][1]
    assert (plan_event["desc_part_matches"], plan_event["desc_reserved"]) == (1, 1)
    planned = [f for k, f in ctx.trace.events if k.value == "target_planned"]
    (gt,) = [f for f in planned if f["slice_id"] == "slice_0524"]
    assert (gt["desc_symbol"], gt["desc_symbol_file"]) == ("file_regexec", _FUNCS)


def test_macros_in_a_spanned_function_do_not_rank_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """함수 **이름**만 본다 — 본문의 `FILE_REGEX` 같은 식별자는 대상이 아니다."""
    _, plan = _plan_refs(monkeypatch, [_MACRO_ONLY])
    assert plan.collected[0].desc_rank == 0
    assert plan.collected[0].desc_symbol is None


def test_without_an_index_only_the_entry_is_ranked_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, plan = _plan_refs(monkeypatch, [_GT], index=None)
    assert plan.collected[0].desc_rank == 0
    assert ctx.trace.events[0][1]["desc_unindexed_slices"] == 1


def test_two_slices_matching_the_same_spanned_function_reserve_one_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    twin = _ref(
        "slice_0600", Location(file=_SOFT, line_start=365, symbol="match"), ((_FUNCS, 510),)
    )
    filler = [
        _ref(f"slice_{i:04d}", Location(file=_APPR, line_start=i, symbol=f"f{i}"), ())
        for i in range(1, 10)
    ]
    ctx, plan = _plan_refs(monkeypatch, [*filler, _GT, twin])
    assert ctx.trace.events[0][1]["desc_reserve_filled"] == 1
    assert sum(t.slice_id in {"slice_0524", "slice_0600"} for t in plan.targets) == 1


def test_the_entry_keeps_a_tie_and_a_higher_entry_is_not_displaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # entry가 이미 3(전체 이름 `regexec`)이면 걸친 2(`file_regexec`)가 안 이긴다 — 엄격 부등호.
    entry = Location(file=_FUNCS, line_start=508, symbol="regexec")
    _, plan = _plan_refs(monkeypatch, [_ref("slice_0001", entry, ((_FUNCS, 508),))])
    assert (plan.collected[0].desc_rank, plan.collected[0].desc_symbol) == (3, None)
    # 같은 2끼리면 entry가 남는다.
    entry = Location(file=_FUNCS, line_start=76, symbol="pmatch_init")
    _, plan = _plan_refs(monkeypatch, [_ref("slice_0002", entry, ((_FUNCS, 508),))])
    assert (plan.collected[0].desc_rank, plan.collected[0].desc_symbol) == (2, None)


def test_a_spanned_file_name_match_alone_does_not_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    """걸친 함수는 이름 일치(≥ 2)만 센다 — 파일 이름(`regex.c`)만 맞는 함수는 0으로 둔다."""
    regex_c = "src/regex.c"
    index = {**_INDEX, regex_c: [Location(file=regex_c, symbol="helper", line_start=1, line_end=9)]}
    entry = Location(file=_APPR, line_start=2564, symbol="getvalue")
    _, plan = _plan_refs(monkeypatch, [_ref("slice_0003", entry, ((regex_c, 5),))], index=index)
    assert (plan.collected[0].desc_rank, plan.collected[0].desc_symbol) == (0, None)


def test_an_index_without_symbols_for_a_file_is_not_counted_as_unindexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, plan = _plan_refs(monkeypatch, [_GT], index={_FUNCS: [], _SOFT: []})
    assert plan.collected[0].desc_rank == 0
    assert ctx.trace.events[0][1]["desc_unindexed_slices"] == 0  # `[]`은 「색인 있음 · 심볼 0건」
