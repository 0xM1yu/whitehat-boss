"""`--slices`를 주면 슬라이스 1개가 타깃 1개다 — 신원(Phase 3b)과 모델이 보는 것(Phase 3c).

ADR 0012 결정 1과 그 D78 ① 풀이: `Location`은 슬라이스의 **대표 위치**(entry의 file·line)이고,
어느 슬라이스인지는 **`Candidate.slice_id`**가 든다 — 하네스가 도장하고 모델은 쓰지 않는다.
Case B의 `entry.symbol`은 sink 호출 이름이라 `Location.symbol`은 후보의 `scopes`에서 감싸는
함수 이름을 가져온다.

**모델이 보는 것**(3c): Scout의 `SOURCE`는 슬라이스 렌더(gutter·배너)이고 FACTS에
`slice_criterion`이 붙는다. LIGHT 캡슐은 그 렌더를 센다. Auditor는 슬라이스가 아니라 슬라이스가
걸친 함수들의 원본을 본다 — entry 함수 먼저, 문자 수 상한 안에서(결정 1-b = 넓은 (i), 2026-09-25
팀 합의). 검사는 run이 남긴 프롬프트 파일(`run_dir/prompts/`)을 읽는다 — 모델이 실제로 받은 글자다.
"""

import json
import re
from pathlib import Path

import pytest

from slicer_source import assemble
from src.adapters.slices import SliceInputError, split_source
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.orchestrator.prompt_log import BODY_MARK
from src.orchestrator.runner import _AUDITOR_SOURCE_CHARS, _AUDITOR_WINDOW_LINES, RunSpec
from src.schemas.common import Location
from src.schemas.enums import Role
from src.tokens import estimate_tokens
from tests.conftest import ALL_PROFILES
from tests.test_pipeline_e2e import (
    _FIXTURE_INDEX,
    CONDITION,
    STARTED_AT,
    TARGET,
    _events,
    _of,
    _plan,
    _poolify_v3,
    _run,
    _with_index,
    _with_mode,
)

_PARSER = split_source((TARGET / "parser.c").read_bytes())
_UTIL = split_source((TARGET / "util.c").read_bytes())
_SOURCES = {"parser.c": _PARSER, "util.c": _UTIL}


@pytest.mark.parametrize("version", ["v4", "v5", "v6"])
def test_auditor_source_limits_match_the_runner_contract(version: str) -> None:
    """#328 2차 MEDIUM-3: 모델용 계약의 수치가 러너 상수와 조용히 갈리지 않는다.

    ⚠ 이 목록은 **판을 센다** — 올리면서 안 늘리면 서빙 판이 빠진다(2026-10-03 v6가 그랬다).
    판 목록 없이 **서빙 판**을 묻는 것은 `test_config.py`의
    `test_the_canonical_auditor_prompt_states_the_runner_source_contract`다.
    """
    prefix = (PROMPTS_DIR / "auditor" / version / "prefix.md").read_text(encoding="utf-8")

    assert f"{_AUDITOR_WINDOW_LINES} lines before and after" in prefix
    assert f"{_AUDITOR_SOURCE_CHARS:,}-character cap" in prefix
    assert "`(file, start line)` order" in prefix


def test_auditor_v4_t1_states_the_adopted_source_contract() -> None:
    """v4_t1(#349 채택 조합)의 입력 서술이 러너 상수·옵션과 갈리지 않는다 -- 창 줄 수 · 정의/피호출
    하나의 글자 상한 · (파일, 시작 줄) 순 · 20,000토큰 상한, 그리고 meta가 그 세 옵션을 선언한다."""
    from src.orchestrator.runner import _AUDITOR_CALLEE_CHARS, _AUDITOR_DEFINITION_CHARS

    prefix = (PROMPTS_DIR / "auditor" / "v4_t1" / "prefix.md").read_text(encoding="utf-8")
    assert f"{_AUDITOR_WINDOW_LINES} lines before and after" in prefix
    assert "`(file, start line)` order" in prefix
    assert "20,000-token cap" in prefix and "15,000-character" not in prefix
    # 하나의 상한은 배너 줄을 포함한 블록 길이다(`runner._auditor_source`의 `len(block)`) -- 본문만
    # 세는 것처럼 쓰면 본문 3,999자 + 배너가 빠지는 것을 모델이 모른다(#374 리뷰).
    flat = " ".join(prefix.split())
    assert (
        f"its block, counting its banner line, is at most {_AUDITOR_DEFINITION_CHARS:,} characters"
        in flat
    )
    assert (
        f"a callee block, counting its banner line, over {_AUDITOR_CALLEE_CHARS:,} characters"
        " is skipped" in flat
    )
    assert set(load_prompt(Role.AUDITOR, "v4_t1").source_options) == {
        "auditor_source_tokens", "auditor_definitions", "auditor_callee_hops"}


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_a_profile_on_auditor_v4_t1_sets_the_token_cap_the_prompt_states(profile: str) -> None:
    """v4_t1은 「20,000-token cap」을 모델에 말한다 -> 그 판을 고르는 프로필은 그 값을 켠다.
    지금은 아무 프로필도 안 고른다(미배선) -- 켜는 PR이 값을 다르게 두면 여기서 걸린다."""
    from src.config import load_config

    config = load_config(profile)
    if "auditor" not in config.profile.roles:
        return
    if config.model_for("auditor").prompt_template_version != "v4_t1":
        return
    thresholds = config.thresholds
    assert thresholds.auditor_source_tokens == 20_000
    assert (thresholds.auditor_definitions, thresholds.auditor_callee_hops) == (1, 1)


def _slice(sid: str, case: str, entry: tuple[str, int, str], scope: str, lines: set) -> dict:
    """`lines`의 정수는 entry 파일의 줄, `(파일, 줄)`은 다른 파일의 줄이다."""
    file, line, symbol = entry
    points = {(file, n) if isinstance(n, int) else n for n in lines}
    return {
        "id": sid,
        "candidate_key": f"key_{sid}",
        "case": case,
        "entry": {"file": file, "line": line, "symbol": symbol},
        "members": [f"key_{sid}"],
        "absorbed": [],
        "lines": [{"file": f, "line": n} for f, n in sorted(points)],
        "code": assemble(points, _SOURCES) if {f for f, _ in points} <= _SOURCES.keys() else "",
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
# `_CASE_B`의 렌더 — 픽스처의 원본 줄 앞에 원본 줄번호, 위에 파일 배너(v3가 선언한 모양).
_CASE_B_RENDER = "\n".join(
    ["/* ===== parser.c ===== */", f"21| {_PARSER[20]}", f"24| {_PARSER[23]}"]
)

# 헤더(.h)의 `static inline` 함수 — entry가 그 **본문** 줄일 때의 Auditor 앵커를 잰다
# (index-v2 보고서 2026-09-26-index-v2-atom.md §5 「말하지 않는 것」). 2026-09-26 표본 셋의 헤더
# entry는 전부 프로토타입 한 줄이었다 — 본문 줄인 경우는 이 픽스처가 처음이다. sink를
# `memcpy`(길이가 상수가 아닌 꼴)로 둔 것은 `_CASE_B`와 같은 이유다 — `fake_model.RISKY_CALLS`
# 가산점 없이는 점수(0.30)가 `scout_threshold`(0.5)를 못 넘어 Auditor까지 안 간다.
_HEADER_FN = (
    "static inline void copy_field(char *dst, const char *src, int n) {\n"
    "    if (dst == NULL) {\n"
    "        return;\n"
    "    }\n"
    "    memcpy(dst, src, n);\n"
    "}\n"
)
_HEADER_LINES = split_source(_HEADER_FN.encode("utf-8"))


def _prompts(trace_path: Path, role: str) -> list[tuple[str, dict]]:
    """run이 남긴 `role` 프롬프트마다 (본문, FACTS) — FACTS는 본문의 마지막 ```json 펜스다."""
    out = []
    for path in sorted((trace_path.parent / "prompts").glob(f"*_{role}.md")):
        body = path.read_text(encoding="utf-8").split(BODY_MARK, 1)[1]
        facts = re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1]
        out.append((body, json.loads(facts)))
    return out


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
    """슬라이스 없는 run은 그 이전 그대로다 — FACTS에 `slice_criterion` 키 자체가 없다(opt-in)."""
    result, trace_path = _run(tmp_path)

    assert result.record.slicer is None
    assert _plan(trace_path)["slice_targets"] == 0
    assert all(c.slice_id is None for c in result.candidates)
    scout_facts = [facts for _, facts in _prompts(trace_path, "scout")]
    assert scout_facts and all("slice_criterion" not in facts for facts in scout_facts)


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


def test_a_drifted_slice_stops_the_run_before_any_model_call(tmp_path: Path) -> None:
    """렌더는 run 전에 **전부** 한다. 슬라이스마다 그때그때 렌더하면 앞 슬라이스(`_CASE_B` —
    정적 신호 줄이라 먼저 돈다)의 모델 호출을 태운 뒤 죽는다.
    ⏵ 2026-10-02: 전 판은 *"… 태운 뒤 산출물 없이 죽는다"*였다. 러너의 최후 방어선이 생겨
    **매니페스트는 남는다** — 그래도 태운 호출은 안 돌아온다. 그것이 이유다."""
    drifted = _slice("slice_0005", "A", ("parser.c", 19, "parse_header"), "parse_header", {19})
    drifted["code"] = "/* ===== parser.c ===== */\nvoid parse_header(void)"
    path = _slices_file(tmp_path, [_slice(*_CASE_B), drifted])

    with pytest.raises(SliceInputError, match="slice_0005"):
        _run(tmp_path, slices_path=path)
    assert not _of(_events(tmp_path / "run" / "run_20260901_0001" / "trace.jsonl"), "model_call")


# --- 모델이 보는 것 (Phase 3c) ------------------------------------------------


def test_scout_sees_the_slice_render_and_the_end_it_was_given(tmp_path: Path) -> None:
    """Scout의 `SOURCE`는 슬라이스 렌더(gutter·배너)이고 FACTS가 `slice_criterion`을 싣는다.

    `given`은 슬라이서의 entry 그대로다 — Case B라 `symbol`이 sink 호출(`memcpy`)이고
    `FACTS.target.symbol`(감싸는 함수)과 다른 것이 정상이다.
    """
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    _, trace_path = _run(tmp_path, slices_path=path)

    ((body, facts),) = _prompts(trace_path, "scout")
    assert facts["source"] == _CASE_B_RENDER
    assert f"```c\n{_CASE_B_RENDER}\n```" in body
    assert facts["slice_criterion"] == {
        "given_end": "sink",
        "given": {"file": "parser.c", "line": 24, "symbol": "memcpy"},
        "degraded": False,
    }
    assert facts["target"]["symbol"] == "parse_header"


def test_the_v3_pool_gets_the_slice_contract_its_prompt_declares(tmp_path: Path) -> None:
    """D82 ①②의 과녁은 풀의 v3 Scout다 — 세 좌석이 같은 렌더와 `slice_criterion`을 받는다.

    위 검사는 `dev_fake`(scout v1)로 돈다. 노드 경로는 같지만 v3 prefix가 선언한 모양이 v3
    프롬프트의 `SOURCE`·FACTS에 실제로 실리는지는 v3로 돌려야 보인다.
    """
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    _, trace_path = _run(tmp_path, slices_path=path, configure=_poolify_v3())

    for role in ("scout_memory", "scout_boundary", "scout_injection"):
        ((body, facts),) = _prompts(trace_path, role)
        assert facts["source"] == _CASE_B_RENDER, role
        assert f"```c\n{_CASE_B_RENDER}\n```" in body, role
        assert facts["slice_criterion"] == {
            "given_end": "sink",
            "given": {"file": "parser.c", "line": 24, "symbol": "memcpy"},
            "degraded": False,
        }, role


def test_a_degraded_slice_tells_the_v3_scout_its_path_is_absent(tmp_path: Path) -> None:
    """#307 재리뷰(민규): FACTS가 degraded를 실어도 v3 prefix가 그 의미를 안 주면, 모델은
    "모든 slice가 도달 문장을 보존했다"는 prefix 단언과 충돌한다. degraded=true가 실제 v3
    프롬프트의 FACTS까지 가고, 세 prefix가 그 anchor-only 제한을 설명하는지 검사한다.
    """
    path = _slices_file(tmp_path, [{**_slice(*_CASE_B), "degraded": True}])

    _, trace_path = _run(tmp_path, slices_path=path, configure=_poolify_v3())

    for role in ("scout_memory", "scout_boundary", "scout_injection"):
        ((_body, facts),) = _prompts(trace_path, role)
        assert facts["slice_criterion"]["degraded"] is True, role  # marker가 모델 FACTS까지 간다
    # 세 v3 prefix가 degraded=true의 anchor-only 제한을 설명해야 FACTS 필드가 무력하지 않다.
    for role in ("scout_memory", "scout_boundary", "scout_injection"):
        prefix = (PROMPTS_DIR / role / "v3" / "prefix.md").read_text(encoding="utf-8")
        assert "`degraded: true`" in prefix, role


def test_each_slice_capsule_counts_the_render_its_prompt_carries(tmp_path: Path) -> None:
    """LIGHT 캡슐의 `target_function` 토큰은 그 슬라이스의 렌더에서 나온다 — 프롬프트가 싣는
    것을 센다.

    entry가 같은 두 슬라이스(쌍둥이)는 렌더가 다르다. Retriever의 캐시 키에 슬라이스 신원이 없으면
    둘째가 첫째의 캡슐을 받아 **자기가 안 싣는 코드**를 센다(#220 ②).
    """
    twin = ("slice_0003", "B", ("parser.c", 24, "memcpy"), "parse_header", {24})
    path = _slices_file(tmp_path, [_slice(*_CASE_B), _slice(*twin)])

    _, trace_path = _run(tmp_path, slices_path=path)

    prompts = _prompts(trace_path, "scout")
    assert len({facts["source"] for _, facts in prompts}) == 2
    for _, facts in prompts:
        (tokens,) = [
            section["tokens"]
            for section in facts["capsule_sections"]
            if section["kind"] == "target_function"
        ]
        assert tokens == estimate_tokens(facts["source"])


@pytest.mark.parametrize("mode", [None, "per_stage"], ids=["per_candidate", "per_stage"])
def test_the_auditor_reads_the_original_function_around_the_entry(
    tmp_path: Path, mode: str | None
) -> None:
    """Auditor는 슬라이스가 아니라 **원본**을 본다(ADR 0012 결정 1-b (i)) — entry 줄을 감싸는
    함수 전체다. entry만 읽으면 함수 머리·버퍼 선언이 빠진 한 줄(24)이 된다. 슬라이스가 그 함수
    하나 안이면 배너 없이 그 함수의 글자 그대로다 — 넓은 (i) 이전의 프롬프트와 바이트가 같다.

    후보의 위치는 그대로 entry다 — Auditor가 보는 범위를 넓혀도 후보의 신원은 안 바뀐다.
    단계 단위(`per_stage`)도 같은 `_TRIAGE_STEPS`를 도는지 같이 본다.
    """
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    result, trace_path = _run(
        tmp_path,
        slices_path=path,
        before=_with_index(),
        configure=_with_mode(mode) if mode is not None else None,
    )

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[18:26])
    assert facts["target"] == {
        "file": "parser.c", "symbol": "parse_header", "line_start": 19, "line_end": 26,
    }
    assert [c.location for c in result.candidates] == [
        Location(file="parser.c", line_start=24, symbol="parse_header")
    ]


def test_the_auditor_takes_the_narrowest_symbol_around_the_entry(tmp_path: Path) -> None:
    """entry를 품는 심볼이 여럿이면 가장 좁은 것이다 — 바깥 범위를 고르면 함수 하나가 아니라 그
    바깥 전체가 Auditor에 간다.

    ⚠ 인덱스를 손으로 적는다. 지금 인덱서는 찾은 정의 안으로 안 들어가
    (`tools/harness-tools/indexer.py`의 `_iter_definitions`) 겹치는 심볼을 내지 않는다 —
    index-v2도 `symbols`는 그대로다(2026-09-26). 이 규칙은 겹침을 내는 인덱스가 오는 날의
    것이고, 바깥 심볼을 **앞에** 둬
    「처음 찾은 것」으로 골라도 빨개지게 했다.
    """
    outer = {"name": "outer_scope", "file": "parser.c", "line_start": 17, "line_end": 31}
    index = {**_FIXTURE_INDEX, "symbols": [outer, *_FIXTURE_INDEX["symbols"]]}
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(index))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert (facts["target"]["symbol"], facts["target"]["line_start"]) == ("parse_header", 19)


def test_the_auditor_anchors_on_the_exact_range_of_a_header_function(tmp_path: Path) -> None:
    """entry가 헤더(`.h`)의 `static inline` 함수 **본문** 줄(프로토타입 한 줄이 아니라)이면 Auditor
    대상이 그 함수의 정확한 범위다 — `_AUDITOR_WINDOW_LINES`(50줄 창)가 아니다. index-v2 보고서
    (2026-09-26-index-v2-atom.md §5 「말하지 않는 것」)의 표본 셋은 헤더 entry가 전부 프로토타입
    한 줄이라 이 경로를 안 탔다 — 창 폴백 테스트
    (`test_without_an_enclosing_symbol_the_auditor_reads_a_window_not_the_whole_file`)의
    대칭 짝이다.
    """
    points = {("util.h", 1), ("util.h", 5)}
    header_slice = {
        "id": "slice_0100",
        "candidate_key": "key_slice_0100",
        "case": "B",
        "entry": {"file": "util.h", "line": 5, "symbol": "memcpy"},
        "members": ["key_slice_0100"],
        "absorbed": [],
        "lines": [{"file": f, "line": n} for f, n in sorted(points)],
        "code": assemble(points, {"util.h": _HEADER_LINES}),
        "_scope": "copy_field",
    }
    path = _slices_file(tmp_path, [header_slice])
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "util.h").write_text(_HEADER_FN, encoding="utf-8")
    index = {
        "version": "index-v1",
        "files": ["util.h"],
        "skipped": [],
        "symbols": [{"name": "copy_field", "file": "util.h", "line_start": 1, "line_end": 6}],
        "calls": [],
        "unresolved_calls": 0,
        "unresolved_symbols": 0,
        "parse_errors": [],
    }

    _, trace_path = _run(tmp_path, slices_path=path, target=repo, before=_with_index(index))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["target"] == {
        "file": "util.h", "symbol": "copy_field", "line_start": 1, "line_end": 6,
    }
    assert facts["source"] == "\n".join(_HEADER_LINES)


_NO_PARSER_SYMBOLS = {
    **_FIXTURE_INDEX,
    "symbols": [s for s in _FIXTURE_INDEX["symbols"] if s["file"] != "parser.c"],
}


@pytest.mark.parametrize(
    "index",
    [
        pytest.param(None, id="no-index"),
        pytest.param(_NO_PARSER_SYMBOLS, id="file-with-no-symbols"),
    ],
)
def test_without_an_enclosing_symbol_the_auditor_reads_a_window_not_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index: dict | None
) -> None:
    """entry를 품는 심볼을 모르면 entry 앞뒤 창이다 — 파일 전체가 아니다. 한 줄도 아니다.

    인덱스가 **있어도** 걸린다: entry가 어느 정의에도 안 드는 줄(헤더의 프로토타입·전역·
    매크로)이면 `symbols_for()`가 품는 심볼을 못 주고, 이름을 못 뽑은 정의도 인덱서가 버린다.
    파일 전체는 상한 없이 Auditor 프롬프트에 실려
    unzip.c 67,662자 → HTTP 400(`runner.py` 모듈 docstring)을 다시 연다 — W2는 같은 `[]`에서
    후보를 안 만들지만 슬라이스 타깃은 이미 서 있다.

    창을 2줄로 줄여 본다 — 픽스처 `parser.c`는 31줄이라 기본 창이면 파일 전체와 구별되지 않는다.
    """
    from src.orchestrator import runner

    monkeypatch.setattr(runner, "_AUDITOR_WINDOW_LINES", 2, raising=False)
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    _, trace_path = _run(
        tmp_path, slices_path=path, before=None if index is None else _with_index(index)
    )

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["target"] == {
        "file": "parser.c", "symbol": None, "line_start": 22, "line_end": 26,
    }
    assert facts["source"] == "\n".join(_PARSER[21:26])


# entry(28)는 `recv_request`(28–31) 안이고 24는 `parse_header`(19–26) 안이다. entry 함수가 줄
# 순서로는 **뒤**라 「entry 함수 먼저」와 「줄 순서」가 갈린다.
_SPANS_TWO = ("slice_0004", "A", ("parser.c", 28, "recv_request"), "recv_request", {24, 28, 30})


def _joined(lines: list[str]) -> str:
    """줄바꿈으로 잇는다 — 테스트가 기대하는 원문 모양."""
    return chr(10).join(lines)


def _banner(file: str, start: int, end: int) -> str:
    return f"/* ===== {file}:{start}-{end} ===== */"


def _source_events(trace_path: Path) -> list[dict]:
    return [event["data"] for event in _of(_events(trace_path), "auditor_source")]


def test_the_auditor_reads_every_function_the_slice_spans_entry_function_first(
    tmp_path: Path,
) -> None:
    """결정 1-b = 넓은 (i)(2026-09-25 팀 합의): Auditor는 슬라이스가 걸친 함수의 원본을 **전부**
    본다 — entry를 감싸는 함수 하나가 아니다. entry 함수가 맨 앞이고(줄 순서가 아니다) 함수마다
    `파일:시작-끝` 배너가 붙는다. 사실 블록과 `{{SOURCE}}` 자리가 같은 글자다.

    캡슐의 대상은 그대로 entry 함수다 — 호출자·피호출자 섹션이 그 함수의 것이다. 몇 함수를
    넣었는지는 `auditor_source` 이벤트가 든다.
    """
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((body, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(
        [_banner("parser.c", 28, 31), *_PARSER[27:31], _banner("parser.c", 19, 26), *_PARSER[18:26]]
    )
    assert facts["source"] in body
    assert (facts["target"]["symbol"], facts["target"]["line_start"]) == ("recv_request", 28)
    assert _source_events(trace_path) == [
        {"slice_id": "slice_0004", "functions": 2, "dropped": 0, "uncovered_lines": 0,
         "chars": len(facts["source"]),
         "anchor_chars": len(_joined(_PARSER[27:31]))},
    ]


def test_the_auditor_source_cap_drops_whole_functions_but_never_the_entry_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원문에는 문자 수 상한이 있다(`_AUDITOR_SOURCE_CHARS`). 함수는 통째로 넣거나 뺀다 — 중간을
    자르면 끊긴 함수를 본다. entry 함수는 상한보다 커도 넣는다 — 빼면 볼 것이 없다. 뺀 수는
    `auditor_source`가 든다 — 안 남기면 잘린 입력이 온전한 입력으로 읽힌다.

    상한을 1자로 줄인다 — entry 함수(4줄) 혼자서도 넘는다. 하나만 남으면 배너도 없다.
    """
    from src.orchestrator import runner

    monkeypatch.setattr(runner, "_AUDITOR_SOURCE_CHARS", 1, raising=False)
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[27:31])
    assert _source_events(trace_path) == [
        {"slice_id": "slice_0004", "functions": 1, "dropped": 1, "uncovered_lines": 0,
         "chars": len(facts["source"]),
         "anchor_chars": len(_joined(_PARSER[27:31]))},
    ]


def test_the_auditor_source_records_the_entry_function_size_the_cap_never_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상한은 **뒤에 붙는** 함수만 거른다 — entry 함수(`anchor`)는 상한보다 커도 늘 들어간다
    (바로 위 테스트). 그래서 `SOURCE` 하나가 상한을 넘을 수 있고, 그러면 서빙 입력 몫을
    넘겨 400으로 거절된다(#289 리뷰 LOW-2).

    `chars`만으로는 그것이 entry 함수 혼자 때문인지 합 때문인지 **함수가 둘 이상이면 못
    가른다.** `anchor_chars`가 그 자리를 든다 — 고치는 것이 아니라 **보이게 하는 것**이다.
    entry 함수에도 상한을 둘지는 이 이벤트가 실서빙에서 무엇을 내는지 보고 정한다.
    """
    from src.orchestrator import runner

    cap = 1  # entry 함수(4줄) 혼자서도 넘는다
    monkeypatch.setattr(runner, "_AUDITOR_SOURCE_CHARS", cap, raising=False)
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "auditor")
    (event,) = _source_events(trace_path)
    # entry 함수 혼자 남았고, 그 크기가 상한을 넘는다 — 이벤트가 그것을 말한다.
    assert event["anchor_chars"] == len(_joined(_PARSER[27:31]))
    assert event["anchor_chars"] > cap
    assert event["chars"] == len(facts["source"]) == event["anchor_chars"]


@pytest.mark.parametrize(("slack", "fits"), [(0, True), (-1, False)], ids=["exact", "one-short"])
def test_a_function_that_does_not_fit_is_skipped_and_a_later_one_that_fits_goes_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack: int, fits: bool
) -> None:
    """상한은 앞에서부터 **들어가는 것을 넣는다** — 안 들어가는 함수 하나에서 멈추지 않는다.
    나머지 함수의 순서는 (파일, 시작 줄)이라 `parser.c`의 `parse_header`(8줄)가 `util.c`의
    `name_length`(4줄)보다 먼저 오는데, 상한을 entry 함수 + `name_length`에 딱 맞춘다.

    상한은 모델이 받는 글자 그대로다 — 배너와 함수 사이 줄바꿈까지 센다. 딱 맞으면 들어가고
    1자 모자라면 빠진다.
    """
    from src.orchestrator import runner

    first = "\n".join([_banner("parser.c", 28, 31), *_PARSER[27:31]])
    last = "\n".join([_banner("util.c", 26, 29), *_UTIL[25:29]])
    cap = len(first) + 1 + len(last) + slack
    monkeypatch.setattr(runner, "_AUDITOR_SOURCE_CHARS", cap, raising=False)
    spans = ("slice_0006", "A", ("parser.c", 28, "recv_request"), "recv_request",
             {24, 28, 30, ("util.c", 28)})
    path = _slices_file(tmp_path, [_slice(*spans)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == (f"{first}\n{last}" if fits else "\n".join(_PARSER[27:31]))
    assert _source_events(trace_path) == [
        {"slice_id": "slice_0006", "functions": 2 if fits else 1, "dropped": 1 if fits else 2,
         "uncovered_lines": 0, "chars": len(facts["source"]),
         "anchor_chars": len(_joined(_PARSER[27:31]))},
    ]


def test_the_other_functions_follow_in_file_then_line_order(tmp_path: Path) -> None:
    """entry 함수 뒤의 순서는 (파일, 시작 줄)이다 — 줄이 먼저가 아니다. `util.c`의 `copy_name`(14)은
    `parser.c`의 `parse_header`(19)보다 줄이 앞이지만 파일이 뒤라 뒤에 온다. 상한 아래에서는 이
    순서가 어느 함수가 먼저 자리를 차지하는지를 정한다.
    """
    spans = ("slice_0007", "A", ("parser.c", 28, "recv_request"), "recv_request",
             {24, 28, 30, ("util.c", 16)})
    path = _slices_file(tmp_path, [_slice(*spans)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join([
        _banner("parser.c", 28, 31), *_PARSER[27:31],
        _banner("parser.c", 19, 26), *_PARSER[18:26],
        _banner("util.c", 14, 24), *_UTIL[13:24],
    ])


def test_the_auditor_source_is_read_inside_the_auditor_stage(tmp_path: Path) -> None:
    """원문을 모으는 읽기와 `auditor_source` 이벤트는 5단계 구간(`stage_start`~`stage_end`) 안이다.

    러너가 `auditor.run`의 인자로 원문을 미리 만들면 그 파일 읽기가 5단계 `stage_end.wall_ms`에서
    빠지고 이벤트가 5단계 `stage_start`보다 먼저 난다 — 구간으로 단계를 가르는 쪽이 그 이벤트를 어느
    단계에도 못 넣는다(적대 리뷰에서 재현).
    """
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    open_stage, seen = None, []
    for event in _events(trace_path):
        if event["kind"] == "stage_start":
            open_stage = event["stage"]
        elif event["kind"] == "stage_end":
            open_stage = None
        elif event["kind"] == "auditor_source":
            seen.append(open_stage)
    assert seen == [5]


def test_slice_lines_outside_every_known_function_are_counted_not_read(tmp_path: Path) -> None:
    """어느 인덱스 함수에도 안 드는 슬라이스 줄(구조체 필드·전역·프로토타입·매크로)은
    Auditor가 못 본다 — 창을 따로 세우지 않고 **센다**. 안 세면 「슬라이스가 한 함수 안이었다」와
    「슬라이스 일부를 못 봤다」가 같은 기록이 된다. 13은 `struct header`의 필드다.
    """
    field = ("slice_0005", "B", ("parser.c", 24, "memcpy"), "parse_header", {13, 24})
    path = _slices_file(tmp_path, [_slice(*field)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[18:26])
    assert _source_events(trace_path) == [
        {"slice_id": "slice_0005", "functions": 1, "dropped": 0, "uncovered_lines": 1,
         "chars": len(facts["source"]),
         "anchor_chars": len(_joined(_PARSER[18:26]))},
    ]


@pytest.mark.parametrize(
    "version", sorted(p.name for p in (PROMPTS_DIR / "auditor").iterdir() if p.is_dir())
)
def test_every_auditor_prompt_declares_the_original_source_the_runner_gives_it(
    version: str,
) -> None:
    """ADR 0012 검증 표의 1-b 행 — 고른 표현((i) 원본 소스)과 Auditor 프롬프트의 선언이 같다.

    러너는 Auditor에 늘 원본을 준다(슬라이스 렌더를 주는 길이 없다). 슬라이스를 선언한 Auditor
    프롬프트가 생기면 약속과 다른 입력을 받는다 — Scout의 D82가 Auditor에서 되풀이된다.
    """
    assert load_prompt(Role.AUDITOR, version).input == "source"


def test_a_slice_without_a_render_fails_by_name_rather_than_as_a_bare_key_error(
    tmp_path: Path,
) -> None:
    """렌더가 없는 슬라이스를 물으면 어느 슬라이스인지 대는 `SliceInputError`다(#285 리뷰 MEDIUM).

    타깃이 되는지(artifact 멤버십)와 렌더되는지(`is_file`)는 다른 술어다. 둘 다 `spec.target`
    한 트리를 run 시작 때 보므로 현실 입력으로는 안 갈린다 — 재현되는 길은 `build_deps`와 전처리
    사이에 트리가 바뀌는 경쟁뿐이었다. 그래도 갈리는 날 맨 `KeyError` 트레이스백이 아니라 이름을
    대고 멈춘다.
    """
    from src.adapters.factory import build_deps
    from src.config import load_config

    stray = ("slice_0009", "B", ("missing.c", 3, "strcpy"), "main", {3})
    path = _slices_file(tmp_path, [_slice(*_CASE_B), _slice(*stray)])
    deps = build_deps(
        load_config("dev_fake"),
        run_id="run_20260901_0001",
        source_root=TARGET,
        artifacts_root=tmp_path / "artifacts",
        slices_path=path,
    )

    assert deps.read_slice("slice_0002").startswith("/* ===== parser.c ===== */")
    with pytest.raises(SliceInputError, match="slice_0009"):
        deps.read_slice("slice_0009")


# --- 그래프 섹션의 「본문」은 슬라이스다 (#220 ①) ---------------------------------


@pytest.mark.parametrize(
    "case", [pytest.param(_CASE_A, id="case-a"), pytest.param(_CASE_B, id="case-b")]
)
def test_the_scout_graph_sections_describe_the_slice_it_shows(
    tmp_path: Path, case: tuple
) -> None:
    """Scout 캡슐의 callers/callees는 **슬라이스가 보여 주는 코드**의 간선이다(#220 ①).

    본문 = 슬라이스의 (file, line) 집합, 「본문 안에 정의」 = 정의 **범위**가 그 집합과 겹친다.
    예전에는 entry 한 줄이 본문이라 Case A(함수 머리 19)는 callees가, Case B(sink 줄 24)는 callers가
    비었다. Case B의 줄 {21, 24}는 `parse_header`(19–26)의 머리 줄을 안 품는다 — 정의 **시작 줄**로
    판정하면 callers가 다시 빈다(K&R 머리·손으로 만든 줄 집합이 그 모양이다).
    """
    path = _slices_file(tmp_path, [_slice(*case)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "scout")
    lines = {section["kind"]: section["lines"] for section in facts["capsule_sections"]}
    assert (lines["callers"], lines["callees"]) == (
        ["recv_request parser.c:30"],
        ["memcpy parser.c:24"],
    )


# --- 슬라이스는 Evidence가 아니다 (ADR 0012 결정 1 ③ · #220 ③) ----------------------


def _shape(evidence: object) -> dict:
    """run마다 달라지는 id·run·payload 경로를 뺀 Evidence의 모양."""
    return evidence.model_dump(exclude={"evidence_id", "run_id", "payload_ref"}, mode="json")


def test_the_gate_sees_only_evidence_the_tool_runner_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트가 보는 Evidence는 전부 도구 러너가 낸 것이다 — 슬라이스는 그 길에 없다(#220 ③).

    `static_ok`·`graph_ok`는 후보와 결합 없는 존재 검사라(`gate.py`) 슬라이스를 STATIC·GRAPH
    Evidence로 적는 순간 E1·E2가 부푼다. 철자(`Evidence(` 생성 지점 목록)가 아니라 **출처**로
    잠근다 —
    게이트 호출마다 받은 Evidence가 도구 러너 반환분의 부분집합인가(`evidences_for`가 Finding의
    인용으로 거르므로 같음이 아니라 부분집합이다). 같은 트리를 `--slices` 없이 한 번 더 돌려
    `--slices`가 도구 산출에 **새로 보탠 Evidence가 없는지**도 본다.

    ⚠ 한계: 슬라이서를 **두 run 모두에서 도는** 도구로 등록하면 둘 다 통과한다. `dev_fake`는
    `ContainerToolRunner`를 안 타 실 도구 러너의 경로는 못 본다.
    """
    from src.adapters.fake_tools import FakeToolRunner
    from src.orchestrator import gate

    def recorded(name: str, slices_path: Path | None) -> tuple[list, list, object, Path]:
        returned: list = []
        seen: list = []
        run_tool, evaluate = FakeToolRunner.run, gate.evaluate

        def recording_run(self, *args, **kwargs):
            evidence = run_tool(self, *args, **kwargs)
            returned.append(evidence)
            return evidence

        def recording_evaluate(finding, evidences, *args, **kwargs):
            seen.append(tuple(evidences))
            return evaluate(finding, evidences, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(FakeToolRunner, "run", recording_run)
            patch.setattr(gate, "evaluate", recording_evaluate)
            result, trace_path = _run(
                tmp_path, name, slices_path=slices_path, before=_with_index()
            )
        return returned, seen, result, trace_path

    path = _slices_file(tmp_path, [_slice(*_CASE_B)])
    returned, seen, result, trace_path = recorded("sliced", path)

    assert [candidate.slice_id for candidate in result.candidates] == ["slice_0002"]
    # 스파이가 trace에 남은 판정을 전부 봤다. 더 많은 것은 정상이다 — 러너가 보고서 등급을 순수
    # `gate.evaluate`로 다시 매기고 분모 이중 계상을 막으려 `gate_eval`을 안 남긴다(`runner.py`).
    assert len(seen) >= len(_of(_events(trace_path), "gate_eval")) > 0
    assert any(seen), "슬라이스 후보가 Evidence를 들고 게이트에 안 닿았다 — 빈 집합이면 늘 초록"
    for evidences in seen:
        assert [evidence for evidence in evidences if evidence not in returned] == []

    plain, _, _, _ = recorded("plain", None)
    plain_shapes = [_shape(evidence) for evidence in plain]
    assert [_shape(e) for e in returned if _shape(e) not in plain_shapes] == []


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


def test_cli_run_refuses_a_slice_that_drifted_from_the_target_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """형식은 맞는데 `code`가 대상 파일과 다르다(drift) — 같은 자리에서 막는다. 적재만 보는
    검사는 이것을 통과시키고, 그러면 gutter가 다른 파일의 줄을 가리키는 run이 선다."""
    from src import cli

    drifted = _slice(*_CASE_B)
    drifted["code"] = "/* ===== parser.c ===== */\n    char buf[8];\n    memcpy(buf, src, len);"
    path = _slices_file(tmp_path, [drifted])
    out = tmp_path / "artifacts"
    out.mkdir()

    code = cli.main([
        "run", "--profile", "dev_fake", "--target", str(TARGET),
        "--slices", str(path), "--artifacts-root", str(out),
    ])

    assert code == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "drift" in err
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


# #349 (a): entry 함수 `recv_request`(28–31)가 30줄에서 `parse_header`(19–26)를 부른다. 슬라이스는
# entry 함수 안에만 있다(28·30) — 결정 1-b 규칙으로는 `parse_header` 본문이 안 붙는다.
_CALLS_ONLY = ("slice_0005", "A", ("parser.c", 28, "recv_request"), "recv_request", {28, 30})


def _hops(value: int):  # noqa: ANN202
    def configure(config):  # noqa: ANN001, ANN202
        return config.transition(
            thresholds=config.thresholds.transition(auditor_callee_hops=value)
        )

    return configure


def test_callee_hops_off_keeps_the_decision_1b_source(tmp_path: Path) -> None:
    """기본(0)이면 피호출 함수 본문을 안 붙이고 이벤트도 옛 모양이다 — ADR 0012 결정 1-b 그대로."""
    path = _slices_file(tmp_path, [_slice(*_CALLS_ONLY)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[27:31])
    (event,) = _source_events(trace_path)
    assert "callees" not in event and "callees_dropped" not in event


def test_callee_hops_append_the_body_of_a_function_the_carried_code_calls(tmp_path: Path) -> None:
    """1이면 실린 함수의 본문 안 호출이 부르는 함수 본문을 entry 함수 뒤에 붙인다(#349 (a)).

    슬라이스 줄이 `parse_header` 안에 하나도 없어도 붙는다 — 그것이 결정 1-b와 갈리는 자리다.
    """
    path = _slices_file(tmp_path, [_slice(*_CALLS_ONLY)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_hops(1))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(
        [_banner("parser.c", 28, 31), *_PARSER[27:31], _banner("parser.c", 19, 26), *_PARSER[18:26]]
    )
    (event,) = _source_events(trace_path)
    assert (event["functions"], event["callees"], event["callees_dropped"]) == (2, 1, 0)


def test_a_callee_over_its_own_cap_is_not_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """피호출 함수 하나의 상한(`_AUDITOR_CALLEE_CHARS`)을 넘으면 안 붙이고
    `callees_dropped`로 센다."""
    from src.orchestrator import runner

    monkeypatch.setattr(runner, "_AUDITOR_CALLEE_CHARS", 1, raising=False)
    path = _slices_file(tmp_path, [_slice(*_CALLS_ONLY)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_hops(1))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[27:31])
    (event,) = _source_events(trace_path)
    assert (event["callees"], event["callees_dropped"]) == (0, 1)


def test_a_callee_already_carried_by_the_slice_is_not_appended_twice(tmp_path: Path) -> None:
    """슬라이스가 이미 `parse_header`를 지나면(24) 결정 1-b 규칙이 싣고, 피호출 단계는
    다시 안 싣는다."""
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_hops(1))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"].count(_banner("parser.c", 19, 26)) == 1
    (event,) = _source_events(trace_path)
    assert (event["functions"], event["callees"]) == (2, 0)


# #349 (f): `parse_header`(19–26)가 `struct header`(12–15)를 쓴다. index-v2여야 정의 표가 있다.
_FIXTURE_INDEX_V2 = {
    **{k: v for k, v in _FIXTURE_INDEX.items() if k != "calls"},
    "version": "index-v2",
    "calls": {
        "parse_header": [
            {"file": "parser.c", "line": 30, "caller": "recv_request", "args": ["packet + 2"]}
        ],
        "memcpy": [{"file": "parser.c", "line": 24, "caller": "parse_header", "args": ["buf"]}],
    },
    "functions": {
        s["name"]: [{"file": s["file"], "line_start": s["line_start"],
                     "line_end": s["line_end"], "params": []}]
        for s in _FIXTURE_INDEX["symbols"]
    },
    "macros": {},
    "types": {"header": [{"file": "parser.c", "line_start": 12, "line_end": 15, "kind": "struct"}]},
    "file_vars": {},
    "fields": {},
    "locals": {},
    "assigns": {},
    "field_assigns": {},
}


def _definitions(value: int):  # noqa: ANN202
    def configure(config):  # noqa: ANN001, ANN202
        return config.transition(
            thresholds=config.thresholds.transition(auditor_definitions=value)
        )

    return configure


def test_definitions_off_keeps_the_decision_1b_source(tmp_path: Path) -> None:
    """기본(0)이면 정의를 안 붙이고 이벤트에 `definitions` 필드도 없다."""
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(_FIXTURE_INDEX_V2))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert _banner("parser.c", 12, 15) not in facts["source"]
    (event,) = _source_events(trace_path)
    assert "definitions" not in event


def test_definitions_append_the_struct_the_carried_code_uses(tmp_path: Path) -> None:
    """1이면 실린 원문이 쓰는 `struct header`의 정의 줄을 함수들 뒤에 붙인다(#349 (f)).
    정의는 `functions`에 안 세고 `definitions`로 센다."""
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(
        tmp_path, slices_path=path, before=_with_index(_FIXTURE_INDEX_V2),
        configure=_definitions(1),
    )

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"].endswith("\n".join([_banner("parser.c", 12, 15), *_PARSER[11:15]]))
    (event,) = _source_events(trace_path)
    assert (event["functions"], event["definitions"], event["definitions_dropped"]) == (2, 1, 0)


def test_a_definition_over_its_own_cap_is_not_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정의 하나의 상한(`_AUDITOR_DEFINITION_CHARS`)을 넘으면 안 붙이고 센다."""
    from src.orchestrator import runner

    monkeypatch.setattr(runner, "_AUDITOR_DEFINITION_CHARS", 1, raising=False)
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(
        tmp_path, slices_path=path, before=_with_index(_FIXTURE_INDEX_V2),
        configure=_definitions(1),
    )

    (event,) = _source_events(trace_path)
    assert (event["definitions"], event["definitions_dropped"]) == (0, 1)


def _source_tokens(value: int):  # noqa: ANN202
    def configure(config):  # noqa: ANN001, ANN202
        return config.transition(
            thresholds=config.thresholds.transition(auditor_source_tokens=value)
        )

    return configure


def test_a_token_cap_without_a_tokenizer_counts_with_the_estimate(tmp_path: Path) -> None:
    """#349 (c): 토큰 상한이면 dev_fake(토크나이저 없음)는 글자 근사로 세고 그 사실을 남긴다.
    상한 1토큰이면 entry 함수만 남는다(entry는 상한 밖)."""
    from src.tokens import estimate_tokens

    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(
        tmp_path, slices_path=path, before=_with_index(), configure=_source_tokens(1)
    )

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[27:31])
    (event,) = _source_events(trace_path)
    assert (event["dropped"], event["token_counter"]) == (1, "estimate")
    assert event["tokens"] == estimate_tokens(facts["source"])


def test_a_token_cap_uses_the_auditor_model_counter_when_one_is_wired() -> None:
    """실 토크나이저가 주입되면(`counter_for_role("auditor")`) 그것으로 센다 — 여기선 단어 수.
    entry 함수(4줄)와 `parse_header`(8줄)의 단어 수 합에 상한을 딱 맞추면 둘 다 들어가고,
    하나 모자라면 `parse_header`가 빠진다."""
    import types

    from src.adapters.slices import SliceRef
    from src.orchestrator import runner

    words = lambda text: len(text.split())  # noqa: E731
    entry = Location(file="parser.c", symbol="recv_request", line_start=28, line_end=31)
    header = Location(file="parser.c", symbol="parse_header", line_start=19, line_end=26)
    texts = {28: "\n".join(_PARSER[27:31]), 19: "\n".join(_PARSER[18:26])}
    both = words(runner._block(entry, texts[28])) + 1 + words(runner._block(header, texts[19]))

    def source_for(cap: int) -> tuple[str, dict]:
        events: list[dict] = []
        ctx = types.SimpleNamespace(
            symbols_for=lambda _file: [entry, header],
            read_source=lambda loc: texts[loc.line_start],
            callees_of=lambda _loc: [], definitions_of=lambda _n, _f: [],
            counter_for_role=lambda role: words if role == "auditor" else None,
            config=types.SimpleNamespace(thresholds=types.SimpleNamespace(
                auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
                auditor_entry_trim=0, auditor_caller_lines=0, auditor_stores=0,
                auditor_closure=0)),
            trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
        )
        ref = SliceRef(slice_id="slice_0004", location=entry, given_end="source", entry=entry,
                       lines=(("parser.c", 24), ("parser.c", 28), ("parser.c", 30)),
                       degraded=False)
        return runner._auditor_source(ctx, ref, entry, "cand"), events[-1]

    _, event = source_for(both)
    assert (event["functions"], event["dropped"], event["token_counter"]) == (2, 0, "model")
    _, event = source_for(both - 1)
    assert (event["functions"], event["dropped"]) == (1, 1)


def _trim_source(
    trim: int, cap: int, helper: bool = False, at: tuple[int, ...] = (100,)
) -> tuple[str, dict]:
    """#349 entry 함수 줄이기: 200줄 entry 함수(머리 2줄 · 몸통 · 닫는 줄), 슬라이스 줄 100 하나.
    상한은 단어 수(모델 카운터 자리)로 센다."""
    import types

    from src.adapters.slices import SliceRef
    from src.orchestrator import runner

    body = ["int big(int n)", "{"] + [f"    step({i});" for i in range(3, 200)] + ["}"]
    entry = Location(file="big.c", symbol="big", line_start=1, line_end=200)
    small = Location(file="big.c", symbol="small", line_start=201, line_end=203)
    texts = {1: "\n".join(body), 201: "int small(void)\n{ return 0; }\n"}
    events: list[dict] = []
    ctx = types.SimpleNamespace(
        symbols_for=lambda _file: [entry, small],
        read_source=lambda loc: texts[loc.line_start],
        callees_of=lambda _loc: [], definitions_of=lambda _n, _f: [],
        counter_for_role=lambda role: (lambda text: len(text.split())),
        config=types.SimpleNamespace(thresholds=types.SimpleNamespace(
            auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
            auditor_entry_trim=trim, auditor_caller_lines=0, auditor_stores=0,
            auditor_closure=0)),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    ref = SliceRef(slice_id="slice_big", location=entry, given_end="source",
                   entry=Location(file="big.c", line_start=100),
                   lines=tuple(("big.c", n) for n in at) + ((("big.c", 202),) if helper else ()),
                   degraded=False)
    return runner._auditor_source(ctx, ref, entry, "cand"), events[-1]


def test_entry_trim_off_keeps_an_entry_function_over_the_cap_whole() -> None:
    """기본(0)이면 entry 함수는 상한 밖 — 200줄 그대로이고 이벤트에 `entry_omitted_lines`가 없다."""
    source, event = _trim_source(0, 10)
    assert source.count("\n") == 199
    assert "entry_omitted_lines" not in event


def test_entry_trim_keeps_the_head_the_slice_neighbourhood_and_the_closing_brace() -> None:
    """켜고 상한을 넘으면 머리(`{`까지 2줄) · 100번 줄 앞뒤 20줄 · 닫는 줄만 남고 뺀 구간마다
    표시가 한 줄씩 남는다. 함수가 하나여도 배너(줄이기 전 범위)를 단다 — 원래 줄 번호를
    되짚을 곳이 그것뿐이다(#351 리뷰)."""
    from src.orchestrator import runner

    source, event = _trim_source(1, 10)
    banner, *lines = source.split("\n")
    assert banner == "/* ===== big.c:1-200 ===== */"
    assert event["entry_over_cap"] is True
    w = runner._AUDITOR_ENTRY_CONTEXT_LINES
    kept = 2 + (2 * w + 1) + 1
    assert lines[:2] == ["int big(int n)", "{"]
    assert lines[-1] == "}"
    assert "    step(100);" in lines
    assert f"    step({100 - w});" in lines and f"    step({100 - w - 1});" not in lines
    assert f"    step({100 + w});" in lines and f"    step({100 + w + 1});" not in lines
    assert lines[2] == f"/* ... {100 - w - 3} lines omitted ... */"
    assert event["entry_omitted_lines"] == 200 - kept
    assert len(lines) == kept + 2
    assert event["anchor_chars"] > len(source)


def test_entry_trim_leaves_an_entry_function_under_the_cap_alone() -> None:
    """켜도 상한 안이면 줄이지 않는다 — `entry_omitted_lines`는 0."""
    source, event = _trim_source(1, 10_000)
    assert source.count("\n") == 199
    assert (event["entry_omitted_lines"], event["entry_over_cap"]) == (0, False)


def test_an_entry_over_the_cap_with_nothing_to_cut_says_so() -> None:
    """슬라이스 줄이 함수 전체에 퍼져 뺄 줄이 없으면 `entry_omitted_lines` 0이어도
    `entry_over_cap`이 참이다 — 「안 넘었다」와 갈린다(#351 리뷰). 배너도 안 붙는다(안 줄였다)."""
    source, event = _trim_source(1, 10, at=tuple(range(10, 200, 30)))
    assert (event["entry_omitted_lines"], event["entry_over_cap"]) == (0, True)
    assert source.startswith("int big(int n)\n")


def test_a_trimmed_entry_keeps_its_original_line_range_in_the_banner() -> None:
    """줄인 entry 함수 뒤에 함수가 붙으면 배너는 줄이기 전 범위(1-200)다 — 줄인 줄 수로 세면
    1-46이 되어 뒤 함수와 줄 번호가 어긋난다. 줄여서 남은 자리에 `small`이 들어간다."""
    source, event = _trim_source(1, 150, helper=True)
    assert source.startswith("/* ===== big.c:1-200 ===== */\n")
    assert "/* ===== big.c:201-203 ===== */" in source
    assert (event["functions"], event["dropped"], event["entry_omitted_lines"]) == (2, 0, 156)


def _caller_source(lines: int, carried_caller: bool = False, cap: int = 10_000):  # noqa: ANN202
    """#349 (b) 호출자 창: `copy`(30-34)를 `handle`(1-25)의 20번 줄이 부른다. 단어 수로 센다."""
    import types

    from src.adapters.slices import SliceRef
    from src.orchestrator import runner

    file = [f"line{i}" for i in range(1, 41)]
    entry = Location(file="x.c", symbol="copy", line_start=30, line_end=34)
    handle = Location(file="x.c", symbol="handle", line_start=1, line_end=25)

    def read(loc: Location) -> str:
        return "\n".join(file[(loc.line_start or 1) - 1 : loc.line_end])

    events: list[dict] = []
    ctx = types.SimpleNamespace(
        symbols_for=lambda _file: [handle, entry],
        read_source=read,
        callees_of=lambda _loc: [], definitions_of=lambda _n, _f: [],
        call_sites_of=lambda loc: (
            [Location(file="x.c", symbol="handle", line_start=20, line_end=20)]
            if loc == entry else []
        ),
        counter_for_role=lambda role: (lambda text: len(text.split())),
        config=types.SimpleNamespace(thresholds=types.SimpleNamespace(
            auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
            auditor_entry_trim=0, auditor_caller_lines=lines, auditor_stores=0,
            auditor_closure=0)),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    slice_lines = (("x.c", 31),) + ((("x.c", 3),) if carried_caller else ())
    ref = SliceRef(slice_id="slice_c", location=entry, given_end="source", entry=entry,
                   lines=slice_lines, degraded=False)
    return runner._auditor_source(ctx, ref, entry, "cand"), events[-1]


def test_caller_lines_off_adds_no_caller_window() -> None:
    """기본(0)이면 호출자 창이 없고 이벤트에 `callers`가 없다 — 결정 1-b 그대로."""
    source, event = _caller_source(0)
    assert source == "\n".join(f"line{i}" for i in range(30, 35))
    assert "callers" not in event


def test_a_caller_window_is_clipped_to_the_caller_function() -> None:
    """호출 지점(20) 앞뒤 8줄 = 12-28인데 호출자 `handle`이 25에서 끝나 12-25로 자른다. 창은
    `functions`에 안 든다."""
    source, event = _caller_source(8)
    assert "/* ===== x.c:12-25 ===== */\nline12\n" in source
    assert "line26" not in source and "line11" not in source
    assert (event["functions"], event["callers"], event["callers_dropped"]) == (1, 1, 0)


def test_a_caller_already_carried_by_the_slice_gets_no_window() -> None:
    """슬라이스가 `handle`을 지나면 그 함수 전체가 이미 실려 창을 붙이지 않는다."""
    source, event = _caller_source(8, carried_caller=True)
    assert source.count("/* ===== x.c:") == 2
    assert (event["functions"], event["callers"], event["callers_dropped"]) == (2, 0, 0)


def test_a_caller_window_over_the_cap_is_dropped() -> None:
    """창이 상한에 안 들면 빼고 `callers_dropped`가 센다."""
    _, event = _caller_source(8, cap=20)
    assert (event["callers"], event["callers_dropped"]) == (0, 1)



def _unit(files: dict[str, list[str]], symbols: list[Location], **opts):  # noqa: ANN202
    """`_auditor_source`를 직접 부르는 최소 ctx(#351 리뷰). 파일은 줄 목록, 상한은 단어 수(모델
    카운터 자리)다. `callees`·`sites`는 심볼 이름 → 위치 목록, `definitions`는 (이름들, 파일) →
    목록 함수, `locals`는 (파일, 함수 이름) → 그 함수의 지역·파라미터 이름 집합이다(없으면 `None` —
    「v2 표 없음」). `reads`가 읽은 위치를, `names`가 정의 조회에 넘어간 이름을 모은다.
    `ctx.lookup_kwargs`는 조회가 불린 순서대로 `(조회 이름, 받은 키워드)`를 든다 — 위생이 켜졌는지
    (`demote_nonproduct`)와 `locals_of`가 불렸는지를 본다."""
    import types

    thresholds = {
        "auditor_callee_hops": 0, "auditor_definitions": 0, "auditor_source_tokens": 0,
        "auditor_entry_trim": 0, "auditor_caller_lines": 0, "auditor_stores": 0,
        "auditor_closure": 0,
    }
    thresholds.update({k: v for k, v in opts.items() if k.startswith("auditor_")})
    reads: list[Location] = []
    names: list[set] = []
    lookup_kwargs: list[tuple[str, dict]] = []

    def read(loc: Location) -> str:
        reads.append(loc)
        lines = files[loc.file]
        return "\n".join(lines[(loc.line_start or 1) - 1 : loc.line_end or len(lines)])

    def definitions(found, file, **kw):  # noqa: ANN001, ANN003, ANN202
        names.append(set(found))
        lookup_kwargs.append(("definitions_of", kw))
        return opts.get("definitions", lambda _n, _f: [])(found, file)

    def callees(loc, **kw):  # noqa: ANN001, ANN003, ANN202
        lookup_kwargs.append(("callees_of", kw))
        return opts.get("callees", {}).get(loc.symbol, [])

    def sites(loc, **kw):  # noqa: ANN001, ANN003, ANN202
        lookup_kwargs.append(("call_sites_of", kw))
        return opts.get("sites", {}).get(loc.symbol, [])

    def locals_of(file, func):  # noqa: ANN001, ANN202
        lookup_kwargs.append(("locals_of", {}))
        return opts.get("locals", {}).get((file, func))

    events: list[dict] = []
    ctx = types.SimpleNamespace(
        symbols_for=lambda file: [s for s in symbols if s.file == file],
        read_source=read,
        callees_of=callees,
        definitions_of=definitions,
        call_sites_of=sites,
        locals_of=locals_of,
        lookup_kwargs=lookup_kwargs,
        stores_of=opts.get("stores_of", lambda _target: None),
        counter_for_role=lambda role: (lambda text: len(text.split())),
        config=types.SimpleNamespace(thresholds=types.SimpleNamespace(**thresholds)),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    return ctx, events, reads, names


def _unit_run(ctx, events, entry: Location, lines):  # noqa: ANN001, ANN202
    from src.adapters.slices import SliceRef
    from src.orchestrator import runner

    ref = SliceRef(slice_id="s", location=entry, given_end="source", entry=entry,
                   lines=tuple(lines), degraded=False)
    return runner._auditor_source(ctx, ref, entry, "cand"), events[-1]


_U_FILE = {"u.c": [f"w{i} x{i}" for i in range(1, 61)]}
_U_F = Location(file="u.c", symbol="f", line_start=1, line_end=3)
_U_H = Location(file="u.c", symbol="h", line_start=10, line_end=12)
_U_G = Location(file="u.c", symbol="g", line_start=20, line_end=29)
_U_K = Location(file="u.c", symbol="k", line_start=40, line_end=41)


def test_a_token_cap_also_bounds_the_callee_and_definition_steps() -> None:
    """토큰 상한은 함수 루프만이 아니라 피호출·정의 단계도 묶는다(#351 리뷰 — 그 둘을 글자
    상한으로 되돌려도 통과하던 변이). `g`(10줄 = 20단어+배너)는 글자로는 한참 작지만 토큰
    상한 20에 안 든다."""
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_F, _U_G], auditor_source_tokens=20, auditor_callee_hops=1,
        auditor_definitions=1, callees={"f": [_U_G]}, definitions=lambda _n, _f: [_U_G],
    )
    _, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert (event["definitions"], event["definitions_dropped"]) == (0, 1)
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_F, _U_G], auditor_source_tokens=20, auditor_callee_hops=1,
        callees={"f": [_U_G]},
    )
    _, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert (event["callees"], event["callees_dropped"]) == (0, 1)



@pytest.mark.parametrize("step", ["callee", "definition"])
def test_a_single_block_cap_counts_the_banner_and_admits_a_block_exactly_at_it(
    monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """피호출·정의 하나의 글자 상한은 **배너 줄을 포함한** 블록 길이에 걸고, 딱 그 길이면 싣는다
    (v4_t1 prefix 「counting its banner line」 · 「over N」/「at most N」 -- #374 리뷰). 상한을
    블록 길이로 두면 실리고(`>`를 `>=`로 바꾸면 죽음), 본문 길이로 두면 배너 때문에 빠진다(본문만
    세게 바꾸면 죽음)."""
    from src.orchestrator import runner

    constant = "_AUDITOR_CALLEE_CHARS" if step == "callee" else "_AUDITOR_DEFINITION_CHARS"
    body = "\n".join(_U_FILE["u.c"][_U_K.line_start - 1 : _U_K.line_end])
    block = runner._block(_U_K, body)
    opts = (
        {"auditor_callee_hops": 1, "callees": {"f": [_U_K]}}
        if step == "callee"
        else {"auditor_definitions": 1, "definitions": lambda _n, _f: [_U_K]}
    )
    for cap, carried in ((len(block), 1), (len(body), 0)):
        monkeypatch.setattr(runner, constant, cap)
        ctx, events, _, _ = _unit(_U_FILE, [_U_F, _U_K], **opts)
        _, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
        key = "callees" if step == "callee" else "definitions"
        assert (event[key], event[f"{key}_dropped"]) == (carried, 1 - carried), cap


def test_definitions_are_looked_up_against_the_anchor_file() -> None:
    """정의 조회는 anchor 파일을 넘긴다 -- 인덱스가 「anchor 파일의 정의가 이김」을 고를 수 있게
    (v4_t1 prefix 「a definition in the anchor's own file wins」 -- #374 리뷰: 넘기는 파일을 `""`로
    바꿔도 통과하던 변이). 조회는 그 파일일 때 같은 파일 정의를, 아니면 다른 파일 정의를 낸다."""
    files = {**_U_FILE, "v.c": ["#define K 9", "x"]}
    own = Location(file="u.c", symbol="K", line_start=40, line_end=41)
    other = Location(file="v.c", symbol="K", line_start=1, line_end=1)
    asked: list[str] = []

    def lookup(_names, file):  # noqa: ANN001, ANN202
        asked.append(file)
        return [own] if file == _U_F.file else [other]

    ctx, events, _, _ = _unit(files, [_U_F], auditor_definitions=1, definitions=lookup)
    source, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert asked == ["u.c"]
    assert "/* ===== u.c:40-41 ===== */" in source and "v.c" not in source
    assert event["definitions"] == 1

def test_callees_of_every_carried_function_are_added_not_only_the_entry() -> None:
    """entry가 아닌 실린 함수(`h` — 슬라이스가 지난다)의 피호출 함수도 붙는다(docstring 「그다음
    실린 순서대로」 — `carried[:1]` 변이가 살던 자리)."""
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_F, _U_H, _U_K], auditor_callee_hops=1, callees={"h": [_U_K]}
    )
    source, event = _unit_run(ctx, events, _U_F, [("u.c", 2), ("u.c", 11)])
    assert "/* ===== u.c:40-41 ===== */" in source
    assert event["callees"] == 1


def test_a_callee_two_carried_functions_share_is_added_once() -> None:
    """`f`와 `h`가 둘 다 `k`를 부르면 `k`는 한 번만 붙는다(`seen` 기억)."""
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_F, _U_H, _U_K], auditor_callee_hops=1, callees={"f": [_U_K], "h": [_U_K]}
    )
    source, event = _unit_run(ctx, events, _U_F, [("u.c", 2), ("u.c", 11)])
    assert source.count("/* ===== u.c:40-41 ===== */") == 1
    assert (event["callees"], event["callees_dropped"]) == (1, 0)


def test_a_function_the_cap_dropped_is_not_counted_again_as_a_callee() -> None:
    """총 상한에 걸려 뺀 함수(`g`)를 entry가 부르면 피호출 단계가 다시 세지 않는다 — 이벤트가
    `dropped:1, callees_dropped:1`로 같은 함수를 두 번 세던 것(#351 리뷰 LOW)."""
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_F, _U_G], auditor_source_tokens=20, auditor_callee_hops=1,
        callees={"f": [_U_G]},
    )
    _, event = _unit_run(ctx, events, _U_F, [("u.c", 2), ("u.c", 25)])
    assert (event["dropped"], event["callees_dropped"]) == (1, 0)


def test_definition_lookup_reads_code_not_banners_or_omission_marks() -> None:
    """정의 조회에 배너(`u.c` → `u`·`c`)와 뺀 구간 표시(`lines`·`omitted`)의 낱말이 안 간다
    (#351 리뷰 LOW — 코드가 안 쓰는 이름의 매크로가 붙던 것)."""
    files = {"u.c": ["int f(void)", "{"] + [f"    w{i};" for i in range(3, 80)] + ["}"]}
    entry = Location(file="u.c", symbol="f", line_start=1, line_end=80)
    ctx, events, _, names = _unit(
        files, [entry, _U_H], auditor_definitions=1, auditor_entry_trim=1,
        auditor_source_tokens=30,
    )
    source, _ = _unit_run(ctx, events, entry, [("u.c", 40)])
    assert "lines omitted" in source and "/* ===== u.c:" in source
    (used,) = names
    assert not used & {"u", "c", "lines", "omitted"}
    assert "w40" in used


def test_definition_lookup_skips_the_carried_functions_locals_params_and_fields() -> None:
    """정의 조회에 넘어가는 이름에서 실린 함수 **자신의** 지역·파라미터와 필드 전용 이름이 빠진다 --
    그 이름은 다른 파일의 매크로·타입·파일 변수와 철자가 같아도 그것을 가리키지 않는다(`za`·
    `->len`이 남의 파일 정의를 끌어오던 오염). 전역 `GLOBAL`·다른 함수가 부르는 이름은 남는다."""
    files = {
        "u.c": [
            "int f(int n)",
            "{",
            "    int za = p->len + n;",
            "    return GLOBAL + za;",
            "}",
        ]
    }
    f = Location(file="u.c", symbol="f", line_start=1, line_end=5)
    ctx, events, _, names = _unit(
        files, [f], auditor_definitions=1, locals={("u.c", "f"): {"n", "za"}}
    )

    _unit_run(ctx, events, f, [("u.c", 3)])

    (used,) = names
    assert used == {"f", "p", "GLOBAL"}


def test_definition_lookup_ignores_words_in_comments_and_string_literals() -> None:
    """정의 조회에 넘어가는 이름에서 주석·문자열 속 낱말(`FOO_MAX`·`BUF_SIZE`·`HIDDEN`)이 빠진다 --
    같은 철자의 매크로가 다른 파일에 하나뿐이면 끌려오던 오염. 주석에도 나오지만 **코드에도** 나온
    `LIMIT`은 남는다."""
    files = {
        "u.c": [
            "int f(int n)",
            "{",
            "    /* clamp to FOO_MAX and LIMIT first */",
            '    log("see BUF_SIZE");',
            "    if (n > LIMIT) return 0;",
            "    return 1; // HIDDEN",
            "}",
        ]
    }
    f = Location(file="u.c", symbol="f", line_start=1, line_end=7)
    ctx, events, _, names = _unit(
        files, [f], auditor_definitions=1, locals={("u.c", "f"): {"n"}}
    )

    _unit_run(ctx, events, f, [("u.c", 5)])

    (used,) = names
    assert used == {"f", "log", "LIMIT"}


def test_default_off_never_strips_anything_for_the_definition_lookup() -> None:
    """`auditor_definitions`가 0이면 정의 조회 자체가 안 돈다 -- 주석 거름도 이름 거름도
    `locals_of`도 안 불린다(`SOURCE`는 원문 그대로 실리고 이벤트에 `definitions` 키도 없다)."""
    files = {"u.c": ["int f(void)", "{", "    /* LIMIT */", "    return 0;", "}"]}
    f = Location(file="u.c", symbol="f", line_start=1, line_end=5)
    ctx, events, _, names = _unit(files, [f])

    source, event = _unit_run(ctx, events, f, [("u.c", 4)])

    assert source == "\n".join(files["u.c"])
    assert names == [] and "definitions" not in event
    assert ctx.lookup_kwargs == []


# 슬라이스 줄(4)에는 `BBB`만 있고 `AAA`는 같은 함수의 슬라이스 밖 줄(3)에만 있다. 정의는 (파일, 줄)
# 순으로 `AAA`가 먼저 온다 — 슬라이스 줄에 나온 `BBB`가 앞서야 한다.
_H_FILE = {
    "u.c": [
        "int f(void)",
        "{",
        "    use(AAA, BBB);",
        "    use(BBB);",
        "}",
        *[f"filler{i}" for i in range(6, 20)],
        "#define AAA 1",
        "#define BBB 2",
    ]
}
_H_F = Location(file="u.c", symbol="f", line_start=1, line_end=5)
_H_AAA = Location(file="u.c", symbol="AAA", line_start=20, line_end=20)
_H_BBB = Location(file="u.c", symbol="BBB", line_start=21, line_end=21)


def test_definitions_named_on_the_slice_lines_are_appended_first() -> None:
    """슬라이스 줄에 나온 이름의 정의가 먼저 붙는다(상한이 차면 뒤가 먼저 빠진다)."""
    ctx, events, _, _ = _unit(
        _H_FILE, [_H_F], auditor_definitions=1, definitions=lambda _n, _f: [_H_AAA, _H_BBB]
    )

    source, event = _unit_run(ctx, events, _H_F, [("u.c", 4)])

    assert source.index("#define BBB 2") < source.index("#define AAA 1")
    assert event["definitions"] == 2


def test_the_cap_keeps_the_definition_named_on_the_slice() -> None:
    """정의 하나만 들어갈 상한이면 슬라이스 줄에 나온 `BBB`가 남고 `AAA`가 빠진다 -- 순서를 안
    바꾸면 (파일, 줄) 순으로 `AAA`가 먼저 들어가 슬라이스와 무관한 정의가 자리를 먹는다."""
    from src.orchestrator import runner

    words = lambda text: len(text.split())  # noqa: E731
    anchor_block = runner._block(_H_F, "\n".join(_H_FILE["u.c"][0:5]))
    cap = words(anchor_block) + 1 + words(runner._block(_H_BBB, "#define BBB 2"))
    ctx, events, _, _ = _unit(
        _H_FILE, [_H_F], auditor_definitions=1, auditor_source_tokens=cap,
        definitions=lambda _n, _f: [_H_AAA, _H_BBB],
    )

    source, event = _unit_run(ctx, events, _H_F, [("u.c", 4)])

    assert (event["definitions"], event["definitions_dropped"]) == (1, 1)
    assert "#define BBB 2" in source and "#define AAA 1" not in source


def test_the_definition_hygiene_is_asked_for_only_with_definitions_on() -> None:
    """`auditor_definitions`가 1이면 정의·피호출·호출자 조회가 `demote_nonproduct=True`로 불리고
    `locals_of`도 불린다. 0이면(피호출·호출자만 켠 run) 옛 호출 그대로다 -- 키워드가 없고
    `locals_of`가 안 불린다(측정이 있는 옵션 조합의 출력이 안 바뀐다)."""
    site = Location(file="u.c", symbol="h", line_start=11, line_end=11)
    common = {
        "auditor_callee_hops": 1, "auditor_caller_lines": 1,
        "callees": {"f": [_U_K]}, "sites": {"f": [site]},
    }

    ctx, events, _, _ = _unit(_U_FILE, [_U_F, _U_H, _U_K], auditor_definitions=1, **common)
    _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert dict(ctx.lookup_kwargs) == {
        "definitions_of": {"demote_nonproduct": True},
        "callees_of": {"demote_nonproduct": True},
        "call_sites_of": {"demote_nonproduct": True},
        "locals_of": {},
    }

    ctx, events, _, _ = _unit(_U_FILE, [_U_F, _U_H, _U_K], **common)
    _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert dict(ctx.lookup_kwargs) == {"callees_of": {}, "call_sites_of": {}}


def test_a_parameter_named_like_a_foreign_macro_does_not_pull_it(tmp_path: Path) -> None:
    """`parse_header(const char *src, int len)`의 `len`은 파라미터다 -- 다른 파일(`util.c`)에
    `len`이라는 매크로가 **하나뿐**이어도 그것을 가리키지 않는다. 파이프라인 전체(index →
    `GraphIndex.locals_of` → factory → deps → ctx)를 태운다: `locals_of` 배선이 끊기면 이 테스트가
    먼저 안다. `struct header` 정의(태그 사용)는 그대로 붙는다."""
    index = {
        **_FIXTURE_INDEX_V2,
        "macros": {"len": [{"file": "util.c", "line_start": 12, "line_end": 12}]},
        "locals": {"parser.c": {"parse_header": {"src": [19], "len": [19], "buf": [21]}}},
    }
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(
        tmp_path, slices_path=path, before=_with_index(index), configure=_definitions(1)
    )

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert _banner("util.c", 12, 12) not in facts["source"]
    assert _banner("parser.c", 12, 15) in facts["source"]
    (event,) = _source_events(trace_path)
    assert (event["definitions"], event["definitions_dropped"]) == (1, 0)


def test_no_caller_window_without_an_entry_symbol() -> None:
    """entry를 품는 심볼을 몰라 창이 anchor면(`symbol` 없음) 호출자를 찾지 않는다 — 이름 없는
    대상의 호출 지점은 뜻이 없다(`anchor.symbol` 가드 변이가 살던 자리)."""
    window = Location(file="u.c", line_start=1, line_end=3)
    site = Location(file="u.c", symbol="h", line_start=11, line_end=11)
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_H], auditor_caller_lines=1, sites={None: [site]}
    )
    _, event = _unit_run(ctx, events, window, [("u.c", 2)])
    assert (event["callers"], event["call_sites"]) == (0, 0)


def test_caller_windows_read_only_the_first_sites() -> None:
    """호출 지점이 많아도 앞 `_AUDITOR_CALLER_SITES`개만 읽는다 — 상한이 찬 뒤에도 지점마다
    파일을 읽고 `callers_dropped`가 지점 수가 되던 것(#351 리뷰). 찾은 수는 `call_sites`."""
    from src.orchestrator import runner

    files = {"u.c": [f"w{i}" for i in range(1, 401)]}
    entry = Location(file="u.c", symbol="f", line_start=1, line_end=3)
    sites = [Location(file="u.c", symbol=f"c{i}", line_start=10 * i, line_end=10 * i)
             for i in range(1, 31)]
    ctx, events, reads, _ = _unit(files, [entry], auditor_caller_lines=1, sites={"f": sites})
    _, event = _unit_run(ctx, events, entry, [("u.c", 2)])
    limit = runner._AUDITOR_CALLER_SITES
    assert len(reads) == 1 + limit
    assert event["call_sites"] == 30
    assert event["callers"] + event["callers_dropped"] == limit


def test_overlaps_treats_a_location_without_lines_as_the_whole_file() -> None:
    from src.orchestrator.runner import _overlaps

    whole = Location(file="u.c")
    assert _overlaps(whole, Location(file="u.c", line_start=5, line_end=6))
    assert not _overlaps(whole, Location(file="v.c", line_start=5, line_end=6))



def test_overlapping_call_sites_do_not_use_up_the_read_limit() -> None:
    """같은 호출자 안의 이웃 호출·이미 실린 호출자와 겹치는 지점은 읽는 지점 수 상한을 안 먹는다
    — `h`가 `f`를 8번 불러도 다른 호출자 `k`(55번 줄)의 창이 붙는다(#351 자체 검토 MEDIUM-1:
    자르기가 겹침 검사보다 먼저라 `k`가 영영 안 닿던 것)."""
    entry = Location(file="u.c", symbol="f", line_start=40, line_end=42)
    h = Location(file="u.c", symbol="h", line_start=1, line_end=30)
    k = Location(file="u.c", symbol="k", line_start=50, line_end=60)
    sites = [Location(file="u.c", symbol="h", line_start=n, line_end=n) for n in range(2, 17, 2)]
    sites.append(Location(file="u.c", symbol="k", line_start=55, line_end=55))
    for slice_lines in ([("u.c", 41)], [("u.c", 41), ("u.c", 3)]):
        ctx, events, _, _ = _unit(
            _U_FILE, [entry, h, k], auditor_caller_lines=2, sites={"f": sites}
        )
        source, event = _unit_run(ctx, events, entry, slice_lines)
        assert "/* ===== u.c:53-57 ===== */" in source
        assert event["call_sites"] == 9


def test_a_callee_that_calls_the_entry_back_is_carried_whole() -> None:
    """피호출 함수 `h`가 entry를 되부르면 `h` 본문이 통째로 실리고 그 안의 호출자 창은 겹쳐
    빠진다 — 창이 먼저면 본문 대신 조각만 남고 `callees`에도 안 셌다(#351 자체 검토 LOW-1)."""
    h = Location(file="u.c", symbol="h", line_start=10, line_end=30)
    site = Location(file="u.c", symbol="h", line_start=20, line_end=20)
    ctx, events, _, _ = _unit(
        _U_FILE, [_U_F, h], auditor_callee_hops=1, auditor_caller_lines=2,
        callees={"f": [h]}, sites={"f": [site]},
    )
    source, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert "/* ===== u.c:10-30 ===== */" in source
    assert (event["callees"], event["callers"]) == (1, 0)


# --- `auditor_stores`: 쓰기 지점 블록(`source_stores`) ---------------------------------------

_S_LINES = [
    "int handle(struct ctx *c, const char *src, size_t n)",  # 1
    "{",  # 2
    "    memcpy(c->buf + c->len, src, n);",  # 3
    "    return 0;",  # 4
    "}",  # 5
    "",  # 6
    "void init(struct ctx *c)",  # 7
    "{",  # 8
    "    c->len = 16;",  # 9
    "}",  # 10
    "#define MAXLEN 64",  # 11
    "static int helper(int x)",  # 12
    "{ return x; }",  # 13
]
_S_FILE = {"s.c": _S_LINES}
_S_HANDLE = Location(file="s.c", symbol="handle", line_start=1, line_end=5)
_S_INIT = Location(file="s.c", symbol="init", line_start=7, line_end=10)
_S_HELPER = Location(file="s.c", symbol="helper", line_start=12, line_end=13)
_S_MACRO = Location(file="s.c", symbol="MAXLEN", line_start=11, line_end=11)
_S_ANCHOR = "\n".join(_S_LINES[:5])


def _stores_source(files=None, **opts):  # noqa: ANN001, ANN003, ANN202
    """Case B 슬라이스(sink `memcpy` = 3줄)로 `_auditor_source`를 부른다. `len` 필드의 쓰기는
    `init`(9줄)에 있다. `files`는 원문 파일 사전(기본 `_S_FILE`)이다."""
    from src.adapters.slices import SliceRef
    from src.orchestrator import runner
    from src.schemas.stores import StoreSite

    asked: list = []

    def stores_of(target):  # noqa: ANN001, ANN202
        asked.append(target)
        if (target.name, target.field) == ("len", True):
            return [StoreSite(file="s.c", line=9, func="init", name="len", field=True,
                              lhs="c->len")]
        return []

    opts.setdefault("stores_of", stores_of)
    ctx, events, _, _ = _unit(files or _S_FILE, [_S_HANDLE, _S_INIT, _S_HELPER], **opts)
    sink = Location(file="s.c", symbol="memcpy", line_start=3)
    ref = SliceRef(slice_id="s", location=_S_HANDLE, given_end="sink", entry=sink,
                   lines=(("s.c", 3),), degraded=False)
    return runner._auditor_source(ctx, ref, _S_HANDLE, "cand"), events[-1], asked


def test_stores_off_changes_neither_the_source_nor_the_event_and_never_asks_the_index() -> None:
    """기본(0) — 결정 1-b 그대로다: 함수 하나라 배너도 없고, 이벤트에 `stores*`도 없고, 인덱스에
    묻지도 않는다(`stores_of`가 불리면 실패한다)."""
    def never(_target):  # noqa: ANN001, ANN202
        raise AssertionError("auditor_stores가 꺼졌는데 쓰기 지점을 물었다")

    source, event, _ = _stores_source(stores_of=never)

    assert source == _S_ANCHOR
    assert not [key for key in event if key.startswith("stores")]
    assert event["functions"] == 1


def test_stores_on_appends_the_block_and_reports_it_apart_from_the_functions() -> None:
    """켜면 함수들 뒤에 `STORES` 블록이 붙고(함수가 하나라도 배너가 붙는다 — 블록이 둘이라서),
    이벤트에 `stores*`가 실린다. 블록은 함수가 아니라 `functions`에 안 센다."""
    from src.orchestrator.source_stores import HEADER

    source, event, asked = _stores_source(auditor_stores=1)

    assert source == "\n".join(
        ["/* ===== s.c:1-5 ===== */", _S_ANCHOR, HEADER, "s.c:9 | c->len = 16; | writes ->len"]
    )
    assert event["functions"] == 1
    assert (event["stores"], event["stores_dropped"], event["stores_in_source"]) == (1, 0, 0)
    assert event["stores_dropped_rows"] == "[]"
    assert {(t.name, t.field) for t in asked} >= {("buf", True), ("len", True), ("src", False)}


def test_the_stores_block_sits_after_definitions_and_before_the_callees() -> None:
    """작고 판단 가치가 큰 쪽이 먼저다 — 정의 → 쓰기 지점 → 피호출 본문. 뒤에 붙는 큰 본문이 이 작은
    블록의 몫을 먼저 먹지 않는다."""
    source, event, _ = _stores_source(
        auditor_stores=1, auditor_definitions=1, auditor_callee_hops=1,
        definitions=lambda _names, _file: [_S_MACRO], callees={"handle": [_S_HELPER]},
    )

    positions = [
        source.index(marker)
        for marker in ("s.c:1-5", "s.c:11-11", "STORES", "s.c:12-13")
    ]
    assert positions == sorted(positions)
    assert (event["functions"], event["definitions"], event["callees"]) == (2, 1, 1)


def test_the_stores_block_counts_against_the_source_cap_and_its_rows_are_listed() -> None:
    """블록이 남은 몫에 안 들면 **통째로** 빠지고, 못 실은 행은 목록으로 남는다. 단어 수로 센다."""
    from src.orchestrator import runner

    anchor_words = len(runner._block(_S_HANDLE, _S_ANCHOR).split())

    source, event, _ = _stores_source(auditor_stores=1, auditor_source_tokens=anchor_words + 3)

    assert "STORES" not in source
    assert (event["stores"], event["stores_dropped"]) == (0, 1)
    assert event["stores_dropped_rows"] == '["s.c:9 len"]'
    assert event["tokens"] <= anchor_words + 3


def test_a_flood_of_same_named_writes_is_capped_per_name_and_listed_in_the_event() -> None:
    """같은 이름의 필드 쓰기가 열 곳(8행 초과, 인덱스가 전부 모호하다고 표시)이다 — 블록에는
    이름마다 넷만 실리고 못 실은 여섯은 이벤트에 `file:line 이름` 목록으로 남는다."""
    import json

    from src.schemas.stores import StoreSite

    files = {**_S_FILE, "t.c": [f"    t->len = {n};" for n in range(1, 11)]}
    flood = [
        StoreSite(file="t.c", line=n, func="f", name="len", field=True, lhs="t->len",
                  ambiguous=True)
        for n in range(1, 11)
    ]

    def stores_of(target):  # noqa: ANN001, ANN202
        return flood if (target.name, target.field) == ("len", True) else []

    source, event, _ = _stores_source(files, stores_of=stores_of, auditor_stores=1)

    assert source.count("| writes ->len [ambiguous]") == 4
    assert (event["stores"], event["stores_dropped"]) == (4, 6)
    assert json.loads(event["stores_dropped_rows"]) == [f"t.c:{n} len" for n in range(5, 11)]


def _stores_trim_source(trim: int):  # noqa: ANN202
    """200줄 entry 함수 `big`(100줄이 sink 호출 `memcpy(d, s, p->len)`)에서 `len` 필드의 쓰기가 같은
    함수 안 세 곳(50 · 99 · 150줄)에 있다. `auditor_entry_trim`이 1이고 상한(단어 150 — 줄인
    토막에 `STORES` 머리와 행 둘이 들 만큼)을 넘으면 머리(1-2줄) · 100줄 앞뒤 20줄(80-120) · 닫는
    줄(200)만 남는다 — 99줄은 남고 50·150줄은 빠진다."""
    from src.adapters.slices import SliceRef
    from src.orchestrator import runner
    from src.schemas.stores import StoreSite

    lines = ["int big(int n)", "{"] + [f"    step({i});" for i in range(3, 200)] + ["}"]
    lines[49], lines[98], lines[149] = "    p->len = 7;", "    p->len = 3;", "    p->len = 9;"
    lines[99] = "    memcpy(d, s, p->len);"
    big = Location(file="big.c", symbol="big", line_start=1, line_end=200)
    writes = [
        StoreSite(file="big.c", line=n, func="big", name="len", field=True, lhs="p->len")
        for n in (50, 99, 150)
    ]

    def stores_of(target):  # noqa: ANN001, ANN202
        return writes if (target.name, target.field) == ("len", True) else []

    ctx, events, _, _ = _unit(
        {"big.c": lines}, [big], auditor_entry_trim=trim, auditor_stores=1,
        auditor_source_tokens=150, stores_of=stores_of,
    )
    ref = SliceRef(
        slice_id="s", location=big, given_end="sink",
        entry=Location(file="big.c", symbol="memcpy", line_start=100),
        lines=(("big.c", 100),), degraded=False,
    )
    return runner._auditor_source(ctx, ref, big, "cand"), events[-1]


def test_stores_treats_only_the_lines_that_remain_after_an_entry_trim_as_visible() -> None:
    """`auditor_entry_trim`이 뺀 줄의 쓰기는 `SOURCE`에 **안 보인다** — 「이미 보인다」로 세어
    빠지면 그 쓰기가 어디에도 없다. 남은 토막 안의 99줄만 건너뛰고(`stores_in_source` 1) 뺀
    50·150줄은 행이다."""
    source, event = _stores_trim_source(1)

    assert event["entry_omitted_lines"] > 0 and event["entry_over_cap"] is True
    assert "    p->len = 3;" in source  # 99줄은 남아 있다
    assert "    p->len = 7;" not in source and "    p->len = 9;" not in source
    assert source.count("| writes ->len") == 2
    assert "big.c:50 | p->len = 7; | writes ->len" in source
    assert "big.c:150 | p->len = 9; | writes ->len" in source
    assert (event["stores"], event["stores_in_source"], event["stores_dropped"]) == (2, 1, 0)


def test_stores_without_an_entry_trim_sees_the_whole_function_as_visible() -> None:
    """줄이지 않으면 함수 전체가 실린다 — 세 쓰기가 다 이미 보여 행이 없고 `stores_in_source`가
    셋이다."""
    source, event = _stores_trim_source(0)

    assert "STORES" not in source and "    p->len = 7;" in source
    assert (event["stores"], event["stores_in_source"]) == (0, 3)


def test_trim_entry_reports_the_remaining_lines_as_merged_ranges() -> None:
    """남은 줄은 **이어진 구간**으로 돌려준다 — 머리(1-2) · 슬라이스 줄 앞뒤(80-120) · 닫는 줄(200).
    줄마다 구간 하나씩이면 같은 줄을 덮어도 `STORES`가 훑을 구간이 41개로 는다."""
    from src.orchestrator import runner

    lines = ["int big(int n)", "{"] + [f"    step({i});" for i in range(3, 200)] + ["}"]
    big = Location(file="big.c", symbol="big", line_start=1, line_end=200)

    text, omitted, visible = runner._trim_entry(big, "\n".join(lines), [100, 100])

    assert [(v.line_start, v.line_end) for v in visible] == [(1, 2), (80, 120), (200, 200)]
    assert omitted == 200 - (2 + 41 + 1) and text.count("lines omitted") == 2
    assert {(v.file, v.symbol) for v in visible} == {("big.c", "big")}
