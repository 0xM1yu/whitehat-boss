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
from src.orchestrator.runner import (
    _AUDITOR_SOURCE_CHARS,
    _AUDITOR_WINDOW_LINES,
    RunSpec,
    _shown_ranges,
)
from src.schemas.common import Location
from src.schemas.enums import Role
from src.tokens import estimate_tokens
from tests.conftest import ALL_PROFILES, declare_scout_input
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


@pytest.fixture(autouse=True)
def _decision_1b_baseline(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """이 파일의 `_run`은 Auditor SOURCE 옵션(`AUDITOR_SOURCE_OPTIONS` — 지금 열, ⏵ 2026-10-14
    `auditor_input_context` 추가)을 **전부 끈**
    설정에서 돈다(결정 1-b 모양).

    2026-10-03부터 `budgets.yaml`이 그중 일곱을 켠다(entry 줄이기만 꺼짐 · #349 · ⏵ 2026-10-04
    래퍼 너머 · 간접 피호출 · 쓰기 지점 창 · ⏵ 2026-10-06 가드 목록 `auditor_guards`) -> 그대로
    두면 1-b 규칙을
    재는 검사마다 호출자 창·정의가 끼어 기대값이 흔들리고, 옵션 하나를 켜는 검사는 다른 것이 같이
    켜진 채로 돈다.
    옵션을 재는 검사는 `configure`로 그 하나만 켠다. 기본 설정(여덟 켬) 그대로의 모양은
    `test_default_*` 검사가 따로 본다 -> 그 이름은 이 픽스처를 건너뛴다.
    """
    if request.node.name.startswith("test_default_"):
        return
    import tests.test_pipeline_e2e as e2e
    from src.config import AUDITOR_SOURCE_OPTIONS

    real = e2e.load_config

    def load(profile: str, **kwargs: object):  # noqa: ANN202
        config = real(profile, **kwargs)
        # ⏵ 2026-10-05 — `auditor_source_gate`도 같이 끈다. 그것은 `AUDITOR_SOURCE_OPTIONS`가
        # 아니지만(입력을 안 넓히는 **관측 전용** 스위치라 그 집합 밖이다) `budgets.yaml`이
        # 1로 켜면서 `auditor_source` 이벤트에 `gate_reasons`가 붙어, 이 파일이 이벤트 dict를
        # 통째로 비교하는 검사 다섯이 전부 그 한 키 때문에 깨졌다. 이 픽스처의 명제는
        # 「결정 1-b 모양에서 재는 것」이고 이벤트 모양도 그 모양이어야 한다.
        off = config.thresholds.transition(
            **{key: 0 for key in AUDITOR_SOURCE_OPTIONS}, auditor_source_gate=0
        )
        return config.transition(thresholds=off)

    monkeypatch.setattr(e2e, "load_config", load)


@pytest.fixture(autouse=True)
def _scout_reads_the_slice_render(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """이 파일은 Scout가 **슬라이스 렌더**를 `SOURCE`로 보는 쪽(`input: slice`, v3)을 잰다.

    `dev_fake`의 Scout(`scout/v1`)는 meta에 `input` 키가 없어 기본값 `source`로 읽히고, 노드는
    함수 본문을 보낸다(#529) -> 선언을 `slice`로 바꿔 돈다.
    `test_source_*` 검사는 디스크 선언 그대로다.
    """
    if not request.node.name.startswith("test_source_"):
        declare_scout_input(monkeypatch, "slice")


_PARSER = split_source((TARGET / "parser.c").read_bytes())
_UTIL = split_source((TARGET / "util.c").read_bytes())
_SOURCES = {"parser.c": _PARSER, "util.c": _UTIL}


@pytest.mark.parametrize("version", ["v4", "v5", "v6"])
def test_auditor_source_limits_match_the_runner_contract(version: str) -> None:
    """#328 2차 MEDIUM-3: 모델용 계약의 수치가 러너 상수와 조용히 갈리지 않는다.

    ⚠ 이 목록은 **판을 센다** — 올리면서 안 늘리면 서빙 판이 빠진다(2026-10-03 v6가 그랬다).
    판 목록 없이 **서빙 판**을 묻는 것은 `test_config.py`의
    `test_the_canonical_auditor_prompt_states_the_runner_source_contract`다. ⏵ 2026-10-04 서빙 판
    v7(⏵ 같은 날 v8, ⏵ 2026-10-06부터 v9가 그 서술을 바이트 그대로 물려받았다)은 옵션 넷
    (20,000토큰 상한)을 서술해 15,000자 철자를 안 든다 -> 그쪽 검사가 서빙 판의 계약을 문다.
    v6는 #418이 잰 1-b 문장 그대로라 여기 남는다.

    ⏵ 2026-10-07 — 서빙 판은 v10이다. v10은 v7을 전면 재작성한 판이라 위 v8·v9처럼
    그 서술을 **바이트 그대로 물려받은 것이 아니다**(다시 썼다). v10이 그 옵션들을 서술하는지는
    `test_config.py`의 서빙 판 핀이 문다 — 판 번호가 아니라 `prompt_template_version`으로 서빙
    판을 골라 서술이 러너 상수와 같은지 본다. 이 목록의 v4~v6이 아니다.
    ⏵ 2026-10-09 — 서빙 판은 v11이다(v10 + `cwe` 서술 네 줄, 이슈 #525) — 옵션 서술은 v10과 바이트
    동일이고 같은 서빙 판 핀이 문다.
    ⏵ 2026-10-10 — 서빙 판은 v13이다(v11 + FACTS에서 `source` 사본을 뺀다, 이슈 #67) — 옵션 서술은
    v11과 바이트 동일이고 같은 서빙 판 핀이 문다.
    ⏵ 2026-10-11 — 서빙 판은 v14다(v12 + 같은 FACTS 변경) — 옵션 서술은 v12와 바이트 동일이고
    같은 서빙 판 핀이 문다.
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


def test_source_declaration_gets_the_function_body_through_the_whole_run(tmp_path: Path) -> None:
    """#529 — `input: source`로 읽히는 Scout(배선된 v4는 그렇게 선언하고, `dev_fake`의 scout/v1은
    키가 없어 기본값이 같은 쪽이다)는 `--slices` run에서도 **entry를 품는 함수 본문**을 받는다.
    거터도 배너도 `slice_criterion`도 없이, 첫 줄이 `FACTS.target.line_start`다. 후보의 위치는
    모델이 `FACTS.target`에서 복사하므로 그 함수 범위다.
    """
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    result, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((body, facts),) = _prompts(trace_path, "scout")
    assert facts["source"] == "\n".join(_PARSER[18:26])
    assert "slice_criterion" not in facts
    assert facts["target"] == {
        "file": "parser.c", "symbol": "parse_header", "line_start": 19, "line_end": 26,
    }
    assert _CASE_B_RENDER not in body
    assert [c.location for c in result.candidates] == [
        Location(file="parser.c", line_start=19, line_end=26, symbol="parse_header")
    ]


def test_source_declaration_does_not_report_a_finished_target_as_unanalyzed(
    tmp_path: Path,
) -> None:
    """#529 리뷰 HIGH — `source` 선언이면 `FACTS.target`이 함수 범위라 후보 `location`도 그 범위다.
    dispatched 키는 entry 위치라 위치까지 비교하면 정상 완주 run이 「모든 Scout 자리 실패」(1)로
    찍혔다. 슬라이스 run은 `slice_id`가 대상의 유일 키다."""
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    result, _ = _run(tmp_path, slices_path=path, before=_with_index())

    assert [c.slice_id for c in result.candidates] == [_CASE_B[0]]
    assert result.report.unanalyzed_targets == 0


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
    """#307 재리뷰(@danielcho02): FACTS가 degraded를 실어도 v3 prefix가 그 의미를 안 주면, 모델은
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


def test_source_declaration_keeps_the_slice_graph_sections_on_a_function_body_prompt(
    tmp_path: Path,
) -> None:
    """#529 — `source` 선언도 캡슐(callers/callees)은 슬라이스 집합의 간선이다. 바뀐 것은 `SOURCE`
    뿐이다(캡슐 조립은 프롬프트 선언을 안 본다) — 함수 본문 프롬프트에 슬라이스 기준 섹션이 붙는
    한계는 이 핀이 드러내 둔다(`retrieval.run`의 `slice_id`)."""
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    ((_, facts),) = _prompts(trace_path, "scout")
    lines = {section["kind"]: section["lines"] for section in facts["capsule_sections"]}
    assert (lines["callers"], lines["callees"]) == (
        ["recv_request parser.c:30"],
        ["memcpy parser.c:24"],
    )
    assert facts["source"] == chr(10).join(_PARSER[18:26])


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


def test_source_declaration_counts_a_failed_twin_slice_as_unanalysed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#529 리뷰 HIGH의 바로 그 경로 — `source` 선언이면 쌍둥이 둘의 후보 `location`이
    같은 함수 범위다. 위치로 맞추면 살아남은 쪽이 죽은 쪽을 「분석됨」으로 덮어 0이 된다.
    `slice_id`로 맞춰야 1이다."""
    from src.interfaces.model_client import SchemaParseError
    from tests.test_pipeline_e2e import _failing_model

    twin = ("slice_0003", "B", ("parser.c", 24, "memcpy"), "parse_header", {24})
    path = _slices_file(tmp_path, [_slice(*_CASE_B), _slice(*twin)])
    _failing_model(monkeypatch, [SchemaParseError("첫 슬라이스만 실패(테스트)")])

    result, _ = _run(tmp_path, slices_path=path, before=_with_index())

    assert [c.slice_id for c in result.candidates] == ["slice_0003"]
    assert result.candidates[0].location.line_start == 19  # 함수 범위 — 둘이 같은 위치다
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


def test_enum_constants_of_one_enum_are_carried_once(tmp_path: Path) -> None:
    """index-v4 `enum_constants`의 위치는 enum 정의 전체라 같은 enum의 상수 여럿이 같은 범위를
    낸다 — 겹치는 범위는 한 번만 싣는다(`_overlaps`). 여기선 본문 이름 `buf`·`len`을 같은 범위
    (12–15)의 상수로 적고 `struct header`도 그 범위다 -> 블록 하나, `definitions` 1, 뺀 수 0."""
    same = {"file": "parser.c", "line_start": 12, "line_end": 15, "enum": "e"}
    index = {
        **_FIXTURE_INDEX_V2,
        "version": "index-v4",
        "writes": {},
        "fn_bindings": {},
        "field_calls": {},
        "includes": {},
        "enum_constants": {"buf": [same], "len": [same]},
    }
    path = _slices_file(tmp_path, [_slice(*_SPANS_TWO)])

    _, trace_path = _run(
        tmp_path, slices_path=path, before=_with_index(index), configure=_definitions(1),
    )

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"].count(_banner("parser.c", 12, 15)) == 1
    (event,) = _source_events(trace_path)
    assert (event["definitions"], event["definitions_dropped"]) == (1, 0)


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
                auditor_source_gate=0, auditor_guards=0,
                auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
                auditor_entry_trim=0, auditor_caller_lines=0, auditor_callee_wrappers=0,
                auditor_indirect_callees=0, auditor_write_sites=0,
                auditor_input_context=0)),
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
            auditor_source_gate=0, auditor_guards=0,
            auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
            auditor_entry_trim=trim, auditor_caller_lines=0, auditor_callee_wrappers=0,
                auditor_indirect_callees=0, auditor_write_sites=0,
                auditor_input_context=0)),
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
            auditor_source_gate=0, auditor_guards=0,
            auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
            auditor_entry_trim=0, auditor_caller_lines=lines, auditor_callee_wrappers=0,
                auditor_indirect_callees=0, auditor_write_sites=0,
                auditor_input_context=0)),
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
    「v2 표 없음」). `indirect`는 심볼 이름 → `[(필드, 후보 위치 목록)]`이다(`indirect_calls_of`).
    `reads`가 읽은 위치를, `names`가 정의 조회에 넘어간 이름을 모은다.
    `ctx.lookup_kwargs`는 조회가 불린 순서대로 `(조회 이름, 받은 키워드)`를 든다 — 위생이 켜졌는지
    (`demote_nonproduct`)와 `locals_of`가 불렸는지를 본다."""
    import types

    thresholds = {
        "auditor_source_gate": 0, "auditor_guards": 0,
        "auditor_callee_hops": 0, "auditor_definitions": 0, "auditor_source_tokens": 0,
        "auditor_entry_trim": 0, "auditor_caller_lines": 0, "auditor_callee_wrappers": 0,
        "auditor_indirect_callees": 0, "auditor_write_sites": 0, "auditor_write_window_marker": 0,
        "auditor_input_context": 0,
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

    def indirect(loc, **kw):  # noqa: ANN001, ANN003, ANN202
        lookup_kwargs.append(("indirect_calls_of", kw))
        return opts.get("indirect", {}).get(loc.symbol, [])

    def functions_named(wanted, _file, **kw):  # noqa: ANN001, ANN003, ANN202
        lookup_kwargs.append(("functions_named", kw))
        return [loc for name in sorted(wanted) for loc in opts.get("functions", {}).get(name, [])]

    events: list[dict] = []
    ctx = types.SimpleNamespace(
        symbols_for=lambda file: [s for s in symbols if s.file == file],
        read_source=read,
        callees_of=callees,
        definitions_of=definitions,
        call_sites_of=sites,
        locals_of=locals_of,
        functions_named=functions_named,
        indirect_calls_of=indirect,
        writes_of=lambda name, _file: opts.get("writes", {}).get(name, []),
        field_writes_of=lambda name, _file: opts.get("field_writes", {}).get(name, []),
        lookup_kwargs=lookup_kwargs,
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


def test_default_budgets_carry_definitions_callees_and_caller_windows(tmp_path: Path) -> None:
    """기본 설정(`budgets.yaml` — 2026-10-03부터 옵션 넷 켬, #349)으로 돈 Auditor SOURCE 이벤트가
    네 옵션의 계수를 다 든다. 끄는 줄을 되돌리면(어느 하나라도 0) 그 키가 사라져 여기서 걸린다 --
    정본 프롬프트(v7 · ⏵ 2026-10-04 v8 · ⏵ 2026-10-06부터 v9 — 같은 서술 · ⏵ 2026-10-07부터 v10 —
    전면 재작성해 다시 쓴 서술)가 넷을 서술하므로 기본에서 빠지면 서술이 거짓이 된다."""
    path = _slices_file(tmp_path, [_slice(*_CALLS_ONLY)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index())

    (event,) = _source_events(trace_path)
    for key in ("callees", "callers", "call_sites", "definitions", "tokens"):
        assert key in event, key
    # 간접 피호출·쓰기 지점 창도 기본에서 켜져 있다(#304 · 2026-10-04) — v3 표가 없는 픽스처
    # 인덱스라 붙는 것은 0이어도 키는 있다.
    for key in ("indirect_callees", "indirect_skipped", "write_windows", "write_sites_found"):
        assert key in event, key


# --- 래퍼·별칭 너머(C4) -----------------------------------------------------------------------
# 팀 검수 83표본 중 18건이 「래퍼까지만 붙고 실제 구현이 없다」였다(`gf_strdup` → `gf_mem_strdup` ·
# `ND_TCHECK` → `ND_TCHECK2`). 붙인 피호출 함수가 짧고 정의를 아는 함수를 한둘만 부르면 래퍼로 보고
# 그 함수를 붙인다. 짧다 = `_AUDITOR_WRAPPER_LINES`줄 이하.
_W_FILE = {"w.c": [f"w{i} x{i}" for i in range(1, 121)]}
_W_F = Location(file="w.c", symbol="f", line_start=1, line_end=3)
_W_WRAP = Location(file="w.c", symbol="wrap", line_start=10, line_end=12)
_W_WRAP2 = Location(file="w.c", symbol="wrap2", line_start=14, line_end=16)
_W_WRAP3 = Location(file="w.c", symbol="wrap3", line_start=18, line_end=20)
_W_IMPL = Location(file="w.c", symbol="impl", line_start=30, line_end=45)
_W_LONG = Location(file="w.c", symbol="long_fn", line_start=50, line_end=70)
_W_A = Location(file="w.c", symbol="a", line_start=80, line_end=82)
_W_B = Location(file="w.c", symbol="b", line_start=84, line_end=86)
_W_C = Location(file="w.c", symbol="c", line_start=88, line_end=90)


def _wrapped(callees: dict, **opts):  # noqa: ANN202
    opts.setdefault("auditor_callee_wrappers", 1)
    ctx, events, _, _ = _unit(
        _W_FILE, [_W_F, _W_WRAP, _W_IMPL, _W_LONG], auditor_callee_hops=1, callees=callees, **opts
    )
    return _unit_run(ctx, events, _W_F, [("w.c", 2)])


def test_a_wrapper_callee_brings_the_function_it_forwards_to() -> None:
    """`f` → `wrap`(3줄, `impl` 하나만 부름) → `impl`이 붙는다. 1단만이던 옛 동작이면 `impl`이
    빠진다."""
    source, event = _wrapped({"f": [_W_WRAP], "wrap": [_W_IMPL]})
    assert "/* ===== w.c:30-45 ===== */" in source
    assert (event["callees"], event["wrapper_callees"]) == (2, 1)


def test_a_long_callee_is_not_a_wrapper() -> None:
    """`long_fn`(21줄)은 래퍼가 아니다 -> 그 피호출 `impl`은 안 붙는다(1단에서 멈춤)."""
    from src.orchestrator.runner import _AUDITOR_WRAPPER_LINES

    assert _W_LONG.line_end - _W_LONG.line_start + 1 > _AUDITOR_WRAPPER_LINES
    source, event = _wrapped({"f": [_W_LONG], "long_fn": [_W_IMPL]})
    assert "w.c:30-45" not in source
    assert (event["callees"], event["wrapper_callees"]) == (1, 0)


def test_a_short_callee_that_calls_many_functions_is_not_a_wrapper() -> None:
    """짧아도 정의를 아는 함수를 셋 부르면 래퍼가 아니다(`_AUDITOR_WRAPPER_CALLS` = 2)."""
    from src.orchestrator.runner import _AUDITOR_WRAPPER_CALLS

    assert _AUDITOR_WRAPPER_CALLS == 2
    _, event = _wrapped({"f": [_W_WRAP], "wrap": [_W_A, _W_B, _W_C]})
    assert (event["callees"], event["wrapper_callees"]) == (1, 0)
    _, event = _wrapped({"f": [_W_WRAP], "wrap": [_W_A, _W_B]})
    assert (event["callees"], event["wrapper_callees"]) == (3, 2)


def test_wrappers_are_followed_only_to_the_depth_limit() -> None:
    """`wrap` → `wrap2` → `wrap3` → `impl` — 래퍼 너머는 `_AUDITOR_WRAPPER_DEPTH`(2)단까지라
    `wrap2`·`wrap3`까지 붙고 `impl`은 안 붙는다."""
    from src.orchestrator.runner import _AUDITOR_WRAPPER_DEPTH

    assert _AUDITOR_WRAPPER_DEPTH == 2
    source, event = _wrapped(
        {"f": [_W_WRAP], "wrap": [_W_WRAP2], "wrap2": [_W_WRAP3], "wrap3": [_W_IMPL]}
    )
    assert "w.c:14-16" in source and "w.c:18-20" in source
    assert "w.c:30-45" not in source
    assert (event["callees"], event["wrapper_callees"]) == (3, 2)


def test_callee_hops_off_follows_no_wrapper() -> None:
    """피호출 단계가 꺼지면 래퍼도 안 따라간다 — 이벤트에 키도 없다."""
    ctx, events, _, _ = _unit(_W_FILE, [_W_F], callees={"f": [_W_WRAP], "wrap": [_W_IMPL]})
    source, event = _unit_run(ctx, events, _W_F, [("w.c", 2)])
    assert "w.c:10-12" not in source
    assert "wrapper_callees" not in event


def test_callee_hops_without_the_wrapper_option_stops_at_one_level() -> None:
    """`auditor_callee_hops`만 켜고 `auditor_callee_wrappers`가 0이면 C4 이전 모양이다 — 래퍼
    `wrap`은 붙고 그 너머 `impl`은 안 붙으며, 이벤트에 C4 계수 키가 없다(PR #452 리뷰 MEDIUM-2:
    전엔 피호출 단계만 켜져 있으면 늘 따라가 run 기록으로 전후가 안 갈렸다)."""
    callees = {"f": [_W_WRAP], "wrap": [_W_IMPL]}
    old, old_event = _wrapped(callees, auditor_callee_wrappers=0)
    assert "/* ===== w.c:10-12 ===== */" in old and "w.c:30-45" not in old
    assert old_event["callees"] == 1
    assert old_event.get("wrapper_callees", 0) == 0 and old_event.get("alias_callees", 0) == 0
    assert "wrapper_callees" not in old_event and "alias_callees" not in old_event
    new, new_event = _wrapped(callees, auditor_callee_wrappers=1)
    assert "w.c:30-45" in new and new_event["wrapper_callees"] == 1


_M_FILE = {
    "w.c": _W_FILE["w.c"],
    "m.h": [
        "#define M(x) M2(x, 1)",
        "#define M2(x, n) impl(x, n)",
        "#define K 64",
    ],
}
_M_M = Location(file="m.h", symbol="M", line_start=1, line_end=1)
_M_M2 = Location(file="m.h", symbol="M2", line_start=2, line_end=2)
_M_K = Location(file="m.h", symbol="K", line_start=3, line_end=3)


def _aliased(**opts):  # noqa: ANN202
    def definitions(names, _file):  # noqa: ANN001, ANN202
        # 첫 조회(실린 `f`의 이름)는 `M`·`K`를 낸다 — 본문이 그 둘을 쓴다고 본다. 별칭 라운드는
        # 넘어온 이름 그대로 찾는다.
        return [_M_M2] if "M2" in names else [_M_M, _M_K]

    opts.setdefault("auditor_callee_wrappers", 1)
    ctx, events, _, names = _unit(
        _M_FILE,
        [_W_F, _W_IMPL],
        auditor_definitions=1,
        definitions=definitions,
        functions={"impl": [_W_IMPL]},
        **opts,
    )
    source, event = _unit_run(ctx, events, _W_F, [("w.c", 2)])
    return source, event, names


def test_a_rename_macro_brings_the_macro_it_forwards_to() -> None:
    """실린 `M`이 `M2`로 이름만 바꿔 넘기면 `M2`의 정의를 한 번 더 찾는다(`ND_TCHECK` →
    `ND_TCHECK2`). 상수 매크로 `K`는 별칭이 아니라 다시 안 찾는다."""
    source, event, names = _aliased()
    assert "/* ===== m.h:2-2 ===== */" in source
    assert event["alias_definitions"] == 1
    # 별칭 라운드는 둘(`_AUDITOR_ALIAS_ROUNDS`) — `M` → `M2`, 그 `M2` → `impl`. `K`는 안 찾는다.
    assert names[1:] == [{"M2"}, {"impl"}]


def test_a_rename_macro_brings_the_function_it_forwards_to() -> None:
    """`M2`가 `impl`을 부르면 피호출 단계가 그 함수 본문을 이름으로 찾아 붙인다 — 매크로 본문 안의
    호출은 인덱스 호출 표에 없어 `callees_of`로는 안 닿는다(`FT_FRAME_EXTRACT` →
    `FT_Stream_ExtractFrame`)."""
    source, event, _ = _aliased(auditor_callee_hops=1)
    assert "/* ===== w.c:30-45 ===== */" in source
    assert event["alias_callees"] == 1


def test_the_wrapper_option_off_neither_follows_nor_counts_rename_macros() -> None:
    """`auditor_callee_wrappers`가 0이면 별칭 라운드도 없다 — `M2` 정의·`impl` 본문이 안 붙고
    정의 조회는 첫 조회 한 번이다(C4 이전 모양)."""
    source, event, names = _aliased(auditor_callee_hops=1, auditor_callee_wrappers=0)
    assert "m.h:2-2" not in source and "w.c:30-45" not in source
    assert len(names) == 1
    assert "alias_definitions" not in event and "alias_callees" not in event


_M3_FILE = {
    "w.c": _W_FILE["w.c"],
    "m.h": [
        "#define M(x) M2(x, 1)",
        "#define M2(x, n) M3(x, n)",
        "#define M3(x, n) impl(x, n)",
    ],
}
_M3_M = Location(file="m.h", symbol="M", line_start=1, line_end=1)
_M3_M2 = Location(file="m.h", symbol="M2", line_start=2, line_end=2)
_M3_M3 = Location(file="m.h", symbol="M3", line_start=3, line_end=3)


def test_rename_macros_are_followed_at_most_the_round_limit() -> None:
    """`M` → `M2` → `M3` → `impl`: 이름 바꾸기 세 번이다. `_AUDITOR_ALIAS_ROUNDS`(2)번이면 `M2`·`M3`
    정의까지 붙고 `impl`은 정의도 함수 본문도 안 찾는다 — 마지막 라운드가 낸 대상을 함수 조회에
    넘기면 세 번째 단계까지 간다(PR #452 리뷰 LOW-1 · v7 prefix 「up to 2 renaming steps」)."""
    from src.orchestrator.runner import _AUDITOR_ALIAS_ROUNDS

    assert _AUDITOR_ALIAS_ROUNDS == 2

    def definitions(names, _file):  # noqa: ANN001, ANN202
        table = {"M2": _M3_M2, "M3": _M3_M3}
        found = [table[name] for name in sorted(names) if name in table]
        return found if found or "impl" in names else [_M3_M]

    ctx, events, _, names = _unit(
        _M3_FILE,
        [_W_F, _W_IMPL],
        auditor_definitions=1,
        auditor_callee_hops=1,
        auditor_callee_wrappers=1,
        definitions=definitions,
        functions={"impl": [_W_IMPL]},
    )
    source, event = _unit_run(ctx, events, _W_F, [("w.c", 2)])
    assert "/* ===== m.h:2-2 ===== */" in source and "/* ===== m.h:3-3 ===== */" in source
    assert event["alias_definitions"] == 2
    assert names[1:] == [{"M2"}, {"M3"}]  # `impl`은 정의 조회에 안 넘어간다
    assert "w.c:30-45" not in source
    assert event["alias_callees"] == 0
    looked = [kw for name, kw in ctx.lookup_kwargs if name == "functions_named"]
    assert len(looked) == 1  # `M2`·`M3`로 한 번 찾았고 둘 다 함수가 아니다


def test_auditor_v4_t1_refuses_the_wrapper_option() -> None:
    """v4_t1은 피호출 본문을 「one call level deep」으로 서술하고 `auditor_callee_wrappers`를
    선언하지 않는다 -> 그 판에 그 옵션을 켜면 `cli`가 막는다(PR #452 리뷰 MEDIUM-1). 끄면 선다."""
    from src import cli
    from src.config import load_config

    prefix = (PROMPTS_DIR / "auditor" / "v4_t1" / "prefix.md").read_text(encoding="utf-8")
    assert "one call level deep" in " ".join(prefix.split())
    assert "auditor_callee_wrappers" not in load_prompt(Role.AUDITOR, "v4_t1").source_options

    config = load_config("harness_pool")
    key = config.model_key_for("auditor")
    entry = config.models[key].transition(prompt_template_version="v4_t1")
    # v4_t1이 서술하지 않는 다른 옵션(간접 피호출·쓰기 지점 창 #304 · 가드 목록)은 꺼서 이 옵션
    # 하나만 본다.
    on = config.thresholds.transition(
        auditor_caller_lines=0, auditor_callee_wrappers=1,
        auditor_indirect_callees=0, auditor_write_sites=0, auditor_guards=0,
        auditor_write_window_marker=0, auditor_input_context=0,
    )
    config = config.transition(models={**config.models, key: entry}, thresholds=on)
    assert cli._undescribed_source_options(config) == ["auditor_callee_wrappers(auditor/v4_t1)"]
    off = config.transition(thresholds=on.transition(auditor_callee_wrappers=0))
    assert cli._undescribed_source_options(off) == []


# --- 간접 피호출(C6) · 쓰기 지점 창(C5(a)) — index-v3 표(#304) ------------------------------------
_I_FILE = {"i.c": [f"i{i} y{i}" for i in range(1, 121)]}
_I_F = Location(file="i.c", symbol="f", line_start=1, line_end=3)
_I_A = Location(file="i.c", symbol="impl_a", line_start=30, line_end=40)
_I_B = Location(file="i.c", symbol="impl_b", line_start=50, line_end=60)
_I_C = Location(file="i.c", symbol="impl_c", line_start=70, line_end=80)
_I_D = Location(file="i.c", symbol="impl_d", line_start=90, line_end=100)


def _indirect(groups: list, symbols: tuple = (), lines: tuple = (("i.c", 2),), **opts):  # noqa: ANN202
    ctx, events, _, _ = _unit(
        _I_FILE, [_I_F, *symbols], auditor_callee_hops=1, auditor_indirect_callees=1,
        indirect={"f": groups}, **opts,
    )
    source, event = _unit_run(ctx, events, _I_F, list(lines))
    return source, event, ctx


def test_a_field_call_brings_the_functions_bound_to_that_field() -> None:
    """`sys->alloc(n)`의 후보(`fn_bindings`로 푼 `impl_a`)가 피호출처럼 붙는다."""
    source, event, _ = _indirect([("alloc", [_I_A])])
    assert "/* ===== i.c:30-40 ===== */" in source
    assert (event["callees"], event["indirect_callees"], event["indirect_skipped"]) == (1, 1, 0)


def test_too_many_field_call_candidates_skip_only_that_field() -> None:
    """후보가 `_AUDITOR_INDIRECT_MAX`(3)개를 넘는 **필드**는 어느 것인지 몰라 그 필드의 후보만 안
    붙는다 — 셋이면 붙고 넷이면 안 붙는다. 상한은 필드마다다(PR #468 리뷰 MEDIUM-2): `s->read`
    (후보 1)와 `s->write`(후보 3)를 같이 부르면 합이 4여도 둘 다 붙는다(함수 단위 합이던 때는 둘 다
    버려졌다). 넷인 필드 옆의 다른 필드는 그대로 붙는다."""
    from src.orchestrator.runner import _AUDITOR_INDIRECT_MAX

    assert _AUDITOR_INDIRECT_MAX == 3
    _, event, _ = _indirect([("alloc", [_I_A, _I_B, _I_C])])
    assert (event["indirect_callees"], event["indirect_skipped"]) == (3, 0)
    source, event, _ = _indirect([("alloc", [_I_A, _I_B, _I_C, _I_D])])
    assert "i.c:30-40" not in source
    assert (event["indirect_callees"], event["indirect_skipped"]) == (0, 1)
    _, event, _ = _indirect([("read", [_I_A]), ("write", [_I_B, _I_C, _I_D])])
    assert (event["indirect_callees"], event["indirect_skipped"]) == (4, 0)
    impl_e = Location(file="i.c", symbol="impl_e", line_start=105, line_end=110)
    source, event, _ = _indirect([("read", [_I_A]), ("write", [_I_B, _I_C, _I_D, impl_e])])
    assert "/* ===== i.c:30-40 ===== */" in source and "i.c:50-60" not in source
    assert (event["indirect_callees"], event["indirect_skipped"]) == (1, 1)


def test_already_carried_candidates_do_not_count_toward_the_field_cap() -> None:
    """이미 실린 후보는 상한에 안 센다 — 슬라이스가 `impl_a`를 지나 이미 실렸으면 후보 넷 중 새로
    붙일 것은 셋이라 붙는다(PR #468 리뷰 MEDIUM-2)."""
    source, event, _ = _indirect(
        [("alloc", [_I_A, _I_B, _I_C, _I_D])], symbols=(_I_A,), lines=(("i.c", 2), ("i.c", 31))
    )
    assert source.count("/* ===== i.c:30-40 ===== */") == 1
    assert "/* ===== i.c:90-100 ===== */" in source
    assert (event["indirect_callees"], event["indirect_skipped"]) == (3, 0)


def test_indirect_lookup_gets_the_nonproduct_demotion_with_definitions_on() -> None:
    """간접 피호출 조회도 정의·피호출·호출자 조회와 같이 `auditor_definitions`를 켠 run에서
    `demote_nonproduct=True`로 불린다(#444 · PR #468 리뷰 MEDIUM-2). 끄면 키워드가 없다."""
    _, _, ctx = _indirect([("alloc", [_I_A])], auditor_definitions=1)
    assert ("indirect_calls_of", {"demote_nonproduct": True}) in ctx.lookup_kwargs
    _, _, ctx = _indirect([("alloc", [_I_A])])
    assert ("indirect_calls_of", {}) in ctx.lookup_kwargs


def test_indirect_callees_off_adds_nothing_and_no_keys() -> None:
    ctx, events, _, _ = _unit(
        _I_FILE, [_I_F], auditor_callee_hops=1, indirect={"f": [("alloc", [_I_A])]}
    )
    source, event = _unit_run(ctx, events, _I_F, [("i.c", 2)])
    assert "i.c:30-40" not in source
    assert "indirect_callees" not in event
    assert all(name != "indirect_calls_of" for name, _ in ctx.lookup_kwargs)


_WS_FILE = {
    "w.c": [
        "int f(struct pkt *p) {",  # 1
        "  memcpy(dst, p->payload, p->len);",  # 2 — 슬라이스 줄: 파일 변수 dst · 필드 payload·len
        "}",  # 3
        *[f"filler{i}" for i in range(4, 20)],  # 4-19
        "void setup(struct pkt *p, int n) {",  # 20
        "  int k = n * 2;",  # 21
        "  p->len = k;",  # 22 — 필드 쓰기
        "  return;",  # 23
        "}",  # 24
        *[f"filler{i}" for i in range(25, 40)],  # 25-39
        "void init(void) {",  # 40
        "  dst = malloc(64);",  # 41 — 파일 변수 쓰기
        "}",  # 42
    ]
}
_WS_F = Location(file="w.c", symbol="f", line_start=1, line_end=3)
_WS_SETUP = Location(file="w.c", symbol="setup", line_start=20, line_end=24)
_WS_INIT = Location(file="w.c", symbol="init", line_start=40, line_end=42)


def _write_sites(sites: int, **opts):  # noqa: ANN202
    ctx, events, _, _ = _unit(
        _WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=sites,
        writes={"dst": [Location(file="w.c", symbol="init", line_start=41, line_end=41)]},
        field_writes={"len": [Location(file="w.c", symbol="setup", line_start=22, line_end=22)]},
        **{"auditor_write_window_marker": 1, **opts},
    )
    return _unit_run(ctx, events, _WS_F, [("w.c", 2)])


def test_write_site_windows_show_where_a_file_var_and_a_field_are_written() -> None:
    """슬라이스 줄의 파일 변수 `dst`와 필드 `len`에 쓰는 지점(다른 함수)이 그 줄 앞뒤 3줄 창으로
    붙는다 — 창은 그 함수 범위로 잘린다(`init` 40-42 · `setup` 20-24 안의 19-25가 20-24로)."""
    source, event = _write_sites(6)
    assert "/* ===== w.c:40-42 ===== */" in source and "  dst = malloc(64);" in source
    assert "/* ===== w.c:20-24 ===== */" in source and "  p->len = k;" in source
    assert (event["write_windows"], event["write_windows_dropped"]) == (2, 0)
    assert event["write_sites_found"] == 2


def test_write_site_windows_stop_at_the_limit_and_skip_locals() -> None:
    """상한 1이면 한 곳만 붙는다. 실린 함수의 지역·파라미터 이름은 다른 함수의 같은 이름을 잇지
    않는다 — `dst`가 `f`의 지역이면 그 쓰기 지점을 안 찾는다."""
    _, event = _write_sites(1)
    assert event["write_windows"] == 1
    source, event = _write_sites(6, locals={("w.c", "f"): frozenset({"dst"})})
    assert "w.c:40-42" not in source
    assert event["write_windows"] == 1


def test_write_sites_off_adds_no_window_and_no_keys() -> None:
    source, event = _write_sites(0)
    assert "w.c:40-42" not in source
    assert "write_windows" not in event


def test_a_write_to_a_same_named_local_in_another_function_is_not_attached() -> None:
    """파일 변수 `buf`를 찾을 때 다른 함수 `g`의 **지역** `buf`에 쓴 줄은 안 붙는다(PR #468 리뷰
    HIGH-1). `writes_of`의 실제 계약대로 가짜 조회가 near 파일 안의 두 지점을 다 낸다 — 파일
    변수에 쓴 `init`(41)과 `g`의 지역 선언 초기화(31). 걸러야 하는 것은 러너다(`locals_of`로 쓰는
    함수의 지역인지 본다). 테스트 `test_writes_of_does_not_split_functions_inside_the_near_file`이
    그 계약을 문다."""
    files = {
        "b.c": [
            "int f(const char *src, int n) {",  # 1
            "  memcpy(buf, src, n);",  # 2 — 슬라이스 줄: 파일 변수 buf
            "}",  # 3
            *[f"filler{i}" for i in range(4, 30)],  # 4-29
            "void g(void) {",  # 30
            "  char *buf = malloc(8);",  # 31 — g의 지역 buf
            "  use(buf);",  # 32
            "}",  # 33
            *[f"filler{i}" for i in range(34, 40)],  # 34-39
            "void init(void) {",  # 40
            "  buf = malloc(64);",  # 41 — 파일 변수 buf
            "}",  # 42
        ]
    }
    f = Location(file="b.c", symbol="f", line_start=1, line_end=3)
    g = Location(file="b.c", symbol="g", line_start=30, line_end=33)
    init = Location(file="b.c", symbol="init", line_start=40, line_end=42)
    ctx, events, _, _ = _unit(
        files, [f, g, init], auditor_write_sites=6,
        writes={"buf": [
            Location(file="b.c", symbol="g", line_start=31, line_end=31),
            Location(file="b.c", symbol="init", line_start=41, line_end=41),
        ]},
        locals={("b.c", "f"): frozenset({"src", "n"}), ("b.c", "g"): frozenset({"buf"})},
    )
    source, event = _unit_run(ctx, events, f, [("b.c", 2)])
    assert "malloc(8)" not in source
    assert "/* ===== b.c:40-42 ===== */" in source and "  buf = malloc(64);" in source
    assert (event["write_windows"], event["write_sites_found"]) == (1, 1)


_M1_FILE = {
    "m.c": [
        "int f(struct pkt *p) {",  # 1
        "  memcpy(dst, p->payload, p->len);",  # 2 — 슬라이스 줄
        "}",  # 3
        *[f"filler{i}" for i in range(4, 10)],  # 4-9
        "void setup(struct pkt *p, int k) {",  # 10
        *[f"  step{i};" for i in range(11, 18)],  # 11-17
        "  p->len = k - 2;",  # 18 — 필드 쓰기(호출자 창 밖)
        "  check(p);",  # 19
        "  if (p->len > 64) return;",  # 20
        "  step21;",  # 21
        "  p->len = k;",  # 22 — 필드 쓰기(호출자 창 안)
        *[f"  step{i};" for i in range(23, 30)],  # 23-29
        "  f(p);",  # 30 — entry 호출
        *[f"  tail{i};" for i in range(31, 40)],  # 31-39
        "}",  # 40
    ]
}
_M1_F = Location(file="m.c", symbol="f", line_start=1, line_end=3)
_M1_SETUP = Location(file="m.c", symbol="setup", line_start=10, line_end=40)


def _m1(field_lines: list[int]):  # noqa: ANN202
    ctx, events, _, _ = _unit(
        _M1_FILE, [_M1_F, _M1_SETUP], auditor_caller_lines=10, auditor_write_sites=6,
        sites={"f": [Location(file="m.c", symbol="setup", line_start=30, line_end=30)]},
        field_writes={"len": [
            Location(file="m.c", symbol="setup", line_start=n, line_end=n) for n in field_lines
        ]},
    )
    return _unit_run(ctx, events, _M1_F, [("m.c", 2)])


def test_a_write_window_does_not_push_out_the_caller_window() -> None:
    """**핀(PR #468 리뷰 MEDIUM-1)** — `setup`이 22행에서 `p->len`을 쓰고 30행에서 entry를 부른다.
    이 PR 전부터 실리던 호출자 창 20-40이 그대로 실린다(쓰기 창 19-25가 먼저 자리를 잡던 때는
    호출자 창이 겹쳐 통째로 빠져 26-40의 호출 직전 검사가 사라졌다). 22행은 호출자 창 안이라 쓰기
    창은 따로 안 붙는다."""
    source, event = _m1([22])
    assert "/* ===== m.c:20-40 ===== */" in source and "  f(p);" in source
    assert (event["callers"], event["write_windows"]) == (1, 0)


def test_a_write_window_overlapping_carried_code_is_cut_to_the_free_part() -> None:
    """쓰는 줄(18)이 실린 범위 밖이고 창(15-21)만 호출자 창(20-40)과 겹치면 겹치는 쪽을 깎은
    15-19가 붙는다 — 쓰는 줄은 보이고 같은 줄이 두 블록에 안 실린다."""
    source, event = _m1([18])
    assert "/* ===== m.c:20-40 ===== */" in source
    assert "/* ===== m.c:15-19 ===== */" in source and "  p->len = k - 2;" in source
    assert (event["callers"], event["write_windows"]) == (1, 1)


_ORDER_FILE = {
    "o.c": [
        "int f(struct pkt *p) {",  # 1
        "  copy(zbuf, p->len, abuf);",  # 2 — 처음 나온 순서: copy · zbuf · p · len(필드) · abuf
        "}",  # 3
        *[f"filler{i}" for i in range(4, 80)],  # 4-79
    ]
}
_O_F = Location(file="o.c", symbol="f", line_start=1, line_end=3)
_O_INIT = Location(file="o.c", symbol="init", line_start=10, line_end=30)
_O_SETUP = Location(file="o.c", symbol="setup", line_start=40, line_end=50)
_O_OTHER = Location(file="o.c", symbol="other", line_start=60, line_end=70)


def _ordered(limit: int, writes: dict):  # noqa: ANN202
    ctx, events, _, _ = _unit(
        _ORDER_FILE, [_O_F, _O_INIT, _O_SETUP, _O_OTHER], auditor_write_sites=limit,
        writes=writes,
        field_writes={"len": [Location(file="o.c", symbol="setup", line_start=45, line_end=45)]},
    )
    return _unit_run(ctx, events, _O_F, [("o.c", 2)])


def _site(symbol: str, line: int) -> Location:
    return Location(file="o.c", symbol=symbol, line_start=line, line_end=line)


def test_write_sites_follow_slice_order_not_the_alphabet() -> None:
    """**PR #468 리뷰 MEDIUM-3** — 이름·필드는 슬라이스 줄에 처음 나온 순서로 창을 받는다. 상한 2면
    `zbuf`(init)와 필드 `len`(setup)이 붙고 줄 끝의 `abuf`(other)는 빠진다. 알파벳 순이던 때는
    `abuf`가 먼저 먹고 필드는 이름이 다 끝난 뒤라 굶었다."""
    source, event = _ordered(2, {"zbuf": [_site("init", 12)], "abuf": [_site("other", 65)]})
    assert "/* ===== o.c:10-15 ===== */" in source  # zbuf — init 12 앞뒤 3줄을 init 머리에서 자름
    assert "/* ===== o.c:42-48 ===== */" in source  # len — setup 45
    assert "o.c:62-68" not in source
    assert (event["write_windows"], event["write_sites_found"]) == (2, 3)


def test_write_sites_prefer_a_different_function_before_a_second_site() -> None:
    """함수가 다른 지점이 먼저다 — `zbuf`를 `init`이 두 번(12·25) 써도 상한 2면 둘째 자리는
    `setup`의 `len` 쓰기가 받는다."""
    source, event = _ordered(2, {"zbuf": [_site("init", 12), _site("init", 25)]})
    assert "/* ===== o.c:10-15 ===== */" in source
    assert "/* ===== o.c:42-48 ===== */" in source
    assert "o.c:22-28" not in source
    assert event["write_windows"] == 2


def test_write_sites_found_counts_a_line_once() -> None:
    """한 줄이 두 이름을 쓰면 지점 하나·창 하나다(PR #468 리뷰 LOW-1)."""
    _, event = _ordered(6, {"zbuf": [_site("init", 12)], "abuf": [_site("init", 12)]})
    assert (event["write_windows"], event["write_sites_found"]) == (2, 2)


# --- 상한에 잘린 쓰기 지점 창은 SOURCE에 「더 있다」 줄을 남긴다 (#650) ---------------------------
# `write_sites_found`는 SOURCE에 든 코드 안의 지점도 세므로 `found - shown`이 뺀 창 수가 아니다.
_WS_MORE = "/* ... +1 more write windows not shown ... */"


def test_write_windows_cut_at_the_limit_say_so_in_source() -> None:
    """`_WS_*`의 지점은 둘(init 41 · setup 22) — 상한 1이면 하나가 빠지고 그 줄이 SOURCE 끝에
    선다."""
    source, event = _write_sites(1)
    assert source.endswith("\n" + _WS_MORE)
    assert event["write_windows_omitted"] == 1


def test_the_omitted_count_skips_sites_inside_code_already_carried() -> None:
    """찾은 지점 3곳 중 하나(w.c:2)는 SOURCE에 실린 entry 함수 안이다 — 상한 1이면 창 하나를 붙이고
    남는 둘 중 실제로 빠진 창은 하나뿐이다(`found - shown`은 2)."""
    ctx, events, _, _ = _unit(
        _WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=1,
        auditor_write_window_marker=1,
        writes={"dst": [Location(file="w.c", symbol="init", line_start=41, line_end=41)]},
        field_writes={"len": [
            Location(file="w.c", symbol="setup", line_start=22, line_end=22),
            Location(file="w.c", symbol="f", line_start=2, line_end=2),
        ]},
    )
    source, event = _unit_run(ctx, events, _WS_F, [("w.c", 2)])
    assert (event["write_windows"], event["write_sites_found"]) == (1, 3)
    assert event["write_windows_omitted"] == 1
    assert source.endswith(_WS_MORE)


def test_nothing_omitted_means_no_marker_and_a_zero_count() -> None:
    source, event = _write_sites(6)
    assert "more write windows" not in source
    # 표식 로직 이전과 같은 바이트 — entry 배너 블록과 창 둘(init · setup)을 줄바꿈으로 이은 것.
    lines = _WS_FILE["w.c"]
    assert source == "\n".join([
        "/* ===== w.c:1-3 ===== */", *lines[0:3],
        "/* ===== w.c:40-42 ===== */", *lines[39:42],
        "/* ===== w.c:20-24 ===== */", *lines[19:24],
    ])
    assert event["write_windows_omitted"] == 0
    # 꺼졌을 때는 키가 없다 — 0(안 빠짐)과 None(안 셈)은 다르다.
    assert "write_windows_omitted" not in _write_sites(0)[1]


# --- 큰 enum은 물은 상수 주변만 잘라 싣는다 -----------------------------------------------------
# index-v4가 enum 상수를 enum 정의 전체로 싣자 2,000자를 넘는 enum이 통째로 버려졌다(2026-10-04 로컬
# 274표본에서 「크기 초과로 버림」 29 -> 142, run ID 없음). 머리 · 물은 상수마다 그 앞뒤 · 닫는 줄만
# 남긴다.
_BIG_ENUM = ["typedef enum {"] + [f"    COLOR_{i:03d} = {i}," for i in range(120)] + ["} color_t;"]
_E_FILE = {"u.c": _U_FILE["u.c"], "e.h": _BIG_ENUM}
_E_ENUM = Location(file="e.h", symbol="COLOR_060", line_start=1, line_end=len(_BIG_ENUM))


def test_an_oversized_enum_is_carried_around_the_asked_constant() -> None:
    from src.orchestrator.runner import _AUDITOR_DEFINITION_CHARS, _AUDITOR_ENUM_CONTEXT_LINES

    assert len("\n".join(_BIG_ENUM)) > _AUDITOR_DEFINITION_CHARS  # 그대로면 버려진다
    ctx, events, _, _ = _unit(
        _E_FILE, [_U_F], auditor_definitions=1, definitions=lambda _n, _f: [_E_ENUM]
    )
    source, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert (event["definitions"], event["definitions_dropped"]) == (1, 0)
    assert event["definitions_trimmed"] == 1
    assert f"/* ===== e.h:1-{len(_BIG_ENUM)} ===== */\ntypedef enum {{" in source
    assert "    COLOR_060 = 60," in source and "} color_t;" in source
    width = _AUDITOR_ENUM_CONTEXT_LINES
    assert f"    COLOR_{60 - width:03d} = " in source
    assert f"    COLOR_{60 - width - 1:03d} = " not in source
    assert "/* ... 55 lines omitted ... */" in source  # COLOR_000..054 (55줄)


def test_a_small_enum_or_a_non_enum_is_not_trimmed() -> None:
    """상한 안의 enum은 그대로, 상한을 넘어도 enum이 아니면(큰 구조체) 지금처럼 버린다."""
    small = Location(file="e.h", symbol="COLOR_001", line_start=1, line_end=4)
    small_enum = ["typedef enum {", "    COLOR_000,", "    COLOR_001,", "} c;"]
    files = {"u.c": _U_FILE["u.c"], "e.h": small_enum}
    ctx, events, _, _ = _unit(
        files, [_U_F], auditor_definitions=1, definitions=lambda _n, _f: [small]
    )
    source, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    assert event["definitions_trimmed"] == 0 and "lines omitted" not in source
    big_struct = ["struct big {"] + [f"    int field_{i:03d};" for i in range(200)] + ["};"]
    loc = Location(file="s.h", symbol="field_100", line_start=1, line_end=len(big_struct))
    ctx, events, _, _ = _unit(
        {"u.c": _U_FILE["u.c"], "s.h": big_struct}, [_U_F], auditor_definitions=1,
        definitions=lambda _n, _f: [loc],
    )
    _, event = _unit_run(ctx, events, _U_F, [("u.c", 2)])
    counts = (event["definitions"], event["definitions_dropped"], event["definitions_trimmed"])
    assert counts == (0, 1, 0)


def _enum_constants(*names: str) -> list[Location]:
    """`GraphIndex.definitions_of`가 내는 모양 — enum 상수마다 **enum 전체** 범위에 상수 이름이
    `symbol`이다(그 docstring · `index_graph.py`의 `Location(... symbol=name ...)`)."""
    return [
        Location(file="e.h", symbol=name, line_start=1, line_end=len(_BIG_ENUM)) for name in names
    ]


def _carried_enum(*names: str, **opts):  # noqa: ANN202, ANN003
    ctx, events, _, _ = _unit(
        {**_E_FILE, **opts.pop("files", {})}, [_U_F], auditor_definitions=1,
        definitions=opts.pop("definitions", lambda _n, _f: _enum_constants(*names)), **opts,
    )
    return _unit_run(ctx, events, _U_F, [("u.c", 2)])


def _counts(event: dict) -> tuple:
    return (event["definitions"], event["definitions_dropped"], event["definitions_trimmed"])


def test_an_enum_the_total_cap_refuses_is_not_counted_as_trimmed() -> None:
    """`definitions_trimmed`는 **SOURCE에 실린** 잘린 enum만 센다 — 줄여 놓고 전체 상한에 걸려 못
    실은 것은 `definitions_dropped`다(PR #485 리뷰 P2-5: 예전엔 `definitions=0 · dropped=1 ·
    trimmed=1`로 실리지 않은 정의를 실린 것으로 셌다). 통과 경로는
    `test_an_oversized_enum_is_carried_around_the_asked_constant`가 든다."""
    source, event = _carried_enum("COLOR_060", auditor_source_tokens=30)
    assert _counts(event) == (0, 1, 0)
    assert "COLOR_" not in source


@pytest.mark.parametrize("order", [("COLOR_010", "COLOR_110"), ("COLOR_110", "COLOR_010")])
def test_an_oversized_enum_carries_every_asked_constant_in_one_block(order: tuple) -> None:
    """한 enum의 상수를 여럿 물으면 상수마다 앞뒤를 남긴 **블록 하나**다(PR #485 리뷰 P2-4: 첫
    상수 주변만 싣고 둘째는 enum 전체 범위와 겹친다며 사라졌다). 뺀 구간은 머리 앞 5줄
    (COLOR_000..004) · 두 창 사이 89줄(016..104) · 끝 4줄(116..119). 물은 순서와 상관없다."""
    source, event = _carried_enum(*order)
    assert "    COLOR_010 = 10," in source and "    COLOR_110 = 110," in source
    assert source.count("/* ===== e.h:") == 1
    # 넓힌 블록의 배너는 줄이기 전 끝 줄을 든다 — 모델이 줄 번호를 되짚는 곳은 배너뿐이다(F4:
    # 끝 줄을 빼면 `e.h:1-27`로 잘린 길이가 찍힌다)
    assert f"/* ===== e.h:1-{len(_BIG_ENUM)} ===== */" in source
    assert _counts(event) == (1, 0, 1)  # trimmed는 상수 수가 아니라 enum 수다
    assert event["functions"] == 1  # 블록 수는 그대로 — 넓히기는 새 블록이 아니다
    for gap in (5, 89, 4):
        assert f"/* ... {gap} lines omitted ... */" in source
    assert source.count("lines omitted") == 3


def test_three_asked_constants_far_apart_all_stay_in_the_enum_block() -> None:
    """상수 **셋**(010·060·110 — 창이 서로 안 겹친다)을 한 enum에 물으면 셋 다 실리고 뺀 구간 표시는
    넷(머리 앞·세 창 사이 둘·끝)이다. 셋째를 더할 때 블록을 줄이는 목록(`cut.names`)이 누적되지
    않으면(변이 `cut.names = names` 삭제) 둘째 창이 조용히 사라지는데 카운터는 `(1, 0, 1)` 그대로라
    상수 하나만 더 묻는 테스트로는 안 잡힌다(PR #485 3라운드 R2-3)."""
    source, event = _carried_enum("COLOR_010", "COLOR_060", "COLOR_110")
    for value in (10, 60, 110):
        assert f"    COLOR_{value:03d} = {value}," in source, value
    assert _counts(event) == (1, 0, 1)
    assert source.count("lines omitted") == 4


def test_asked_constants_whose_windows_touch_share_one_stretch() -> None:
    """창이 겹치는 두 상수(050·058 — 앞뒤 5줄이 053..055에서 겹친다)는 한 구간으로 합친다. 같은
    줄이 두 번 실리지 않고 뺀 구간 표시는 머리 앞(045..)·끝 둘뿐이다."""
    source, event = _carried_enum("COLOR_050", "COLOR_058")
    assert _counts(event) == (1, 0, 1)
    for i in range(45, 64):
        assert source.count(f"    COLOR_{i:03d} = ") == 1, i
    assert "    COLOR_044 = " not in source and "    COLOR_064 = " not in source
    assert source.count("lines omitted") == 2


def test_a_constant_asked_in_a_later_round_joins_the_enum_already_carried() -> None:
    """같은 enum의 상수가 **다른 조회 묶음**에서 오면(별칭 라운드: 실린 매크로 `MODE`가
    `COLOR_110`으로 바꿔 넘긴다) 이미 실린 잘린 블록에 합친다 — 블록을 새로 안 만들고 첫 상수만
    남기지도 않는다. 정의 수는 매크로 + enum = 2, 별칭 라운드가 붙인 수 1."""
    macro = Location(file="m.h", symbol="MODE", line_start=1, line_end=1)

    def defs(names, _file):  # noqa: ANN001, ANN202
        if "COLOR_110" in names:
            return _enum_constants("COLOR_110")
        return [macro, *_enum_constants("COLOR_010")]

    source, event = _carried_enum(
        definitions=defs, files={"m.h": ["#define MODE COLOR_110"]}, auditor_callee_wrappers=1
    )
    assert "    COLOR_010 = 10," in source and "    COLOR_110 = 110," in source
    assert source.count("/* ===== e.h:") == 1
    assert _counts(event) == (2, 0, 1)
    assert event["alias_definitions"] == 1


def test_a_constant_that_would_overflow_the_enum_block_is_dropped_and_the_block_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """둘째 상수를 합치면 블록이 정의 하나의 글자 상한을 넘을 때는 합치지 않는다 — 이미 실린 블록은
    그대로이고 못 합친 상수 하나를 `definitions_dropped`가 센다(`trimmed`는 enum 수라 1 그대로)."""
    from src.orchestrator import runner

    one = runner._block(_E_ENUM, runner._trim_enum("\n".join(_BIG_ENUM), ["COLOR_010"]),
                        end=len(_BIG_ENUM))
    monkeypatch.setattr(runner, "_AUDITOR_DEFINITION_CHARS", len(one))
    source, event = _carried_enum("COLOR_010", "COLOR_110")
    assert "    COLOR_010 = 10," in source and "    COLOR_110 = 110," not in source
    assert _counts(event) == (1, 1, 1)


def test_a_constant_the_enum_text_does_not_show_at_a_line_head_is_not_added() -> None:
    """줄 머리에 안 나오는 상수(`_trim_enum`이 못 찾음)는 합치지 않는다 — 블록은 그대로이고 그 상수
    하나를 `definitions_dropped`가 센다. 같은 상수를 두 번 물어도(`COLOR_010` 둘) 한 번이고 뺀 수에
    안 든다."""
    from src.orchestrator import runner

    assert runner._trim_enum("\n".join(_BIG_ENUM), ["COLOR_010", "COLOR_NOPE"]) is None
    source, event = _carried_enum("COLOR_010", "COLOR_NOPE")
    assert "    COLOR_010 = 10," in source and _counts(event) == (1, 1, 1)
    _, event = _carried_enum("COLOR_010", "COLOR_010")
    assert _counts(event) == (1, 0, 1)
    # 「보이는 이름」 검사는 배너 줄을 안 본다 — 파일 이름 `e.h`의 낱말 `e`는 정의가 아니다
    _, event = _carried_enum("COLOR_010", "e")
    assert _counts(event) == (1, 1, 1)


# PR #485 리뷰 P2-3의 4파일 — `a.c::f`와 `b.c::g`가 같은 이름 LIMIT에 다른 헤더를 가진다
_FOUR_FILES = {
    "a.c": '#include "a.h"\nint f(void) { return 0; }\n',
    "b.c": '#include "b.h"\nint g(void) { return LIMIT; }\n',
    "a.h": "#define LIMIT 1\n",
    "b.h": "#define LIMIT 2\n",
}


def test_names_of_a_body_are_asked_against_that_bodys_own_file() -> None:
    """실린 함수가 둘 이상의 파일에 걸치면 정의 조회는 **파일마다** 그 파일 본문이 쓰는 이름을 그
    파일 기준으로 부른다(PR #485 리뷰 P2-3: 이름을 합쳐 앵커 파일 하나로 부르면 `b.c::g`가 쓰는
    `LIMIT`이 `a.h`의 1로 풀렸다). 같은 이름이 파일마다 다르게 풀리면 둘 다 싣는다."""
    files = {name: text.splitlines() for name, text in _FOUR_FILES.items()}
    f = Location(file="a.c", symbol="f", line_start=2, line_end=2)
    g = Location(file="b.c", symbol="g", line_start=2, line_end=2)
    limit = {"a.c": Location(file="a.h", symbol="LIMIT", line_start=1, line_end=1),
             "b.c": Location(file="b.h", symbol="LIMIT", line_start=1, line_end=1)}
    calls: list[tuple[set, str]] = []

    def defs(names, file):  # noqa: ANN001, ANN202
        calls.append((set(names), file))
        return [limit[file]] if "LIMIT" in names else []

    ctx, events, _, _ = _unit(files, [f, g], auditor_definitions=1, definitions=defs)
    source, event = _unit_run(ctx, events, f, [("a.c", 2), ("b.c", 2)])
    assert calls == [({"f"}, "a.c"), ({"g", "LIMIT"}, "b.c")]
    assert "#define LIMIT 2" in source and "#define LIMIT 1" not in source
    assert (event["definitions"], event["definitions_dropped"]) == (1, 0)


def test_one_name_resolved_two_ways_by_two_files_carries_both_definitions() -> None:
    """두 파일 본문이 같은 이름을 쓰고 파일마다 다른 정의로 풀리면 **둘 다** 싣는다 — 위치가 블록
    배너에 있어 구별되고, 하나로 합쳐 보이면 한쪽 본문이 틀린 값을 본다. 같은 Location은 한 번."""
    files = {
        "a.c": ["int f(void) { return LIMIT; }"], "b.c": ["int g(void) { return LIMIT; }"],
        "a.h": ["#define LIMIT 1"], "b.h": ["#define LIMIT 2"], "c.h": ["#define SHARED 3"],
    }
    f = Location(file="a.c", symbol="f", line_start=1, line_end=1)
    g = Location(file="b.c", symbol="g", line_start=1, line_end=1)
    found = {
        "a.c": [Location(file="a.h", symbol="LIMIT", line_start=1, line_end=1)],
        "b.c": [Location(file="b.h", symbol="LIMIT", line_start=1, line_end=1)],
    }
    ctx, events, _, _ = _unit(files, [f, g], auditor_definitions=1,
                              definitions=lambda _n, file: found[file])
    source, event = _unit_run(ctx, events, f, [("a.c", 1), ("b.c", 1)])
    assert "#define LIMIT 1" in source and "#define LIMIT 2" in source
    assert (event["definitions"], event["definitions_dropped"]) == (2, 0)
    shared = [Location(file="c.h", symbol="LIMIT", line_start=1, line_end=1)]
    same = {file: shared for file in found}
    ctx, events, _, _ = _unit(files, [f, g], auditor_definitions=1,
                              definitions=lambda _n, file: same[file])
    _, event = _unit_run(ctx, events, f, [("a.c", 1), ("b.c", 1)])
    assert (event["definitions"], event["definitions_dropped"]) == (1, 0)  # 같은 위치는 한 번


def test_a_rename_macro_target_is_asked_against_the_file_that_used_the_macro() -> None:
    """별칭 라운드도 같다 — `b.c::g`가 쓴 매크로 `MODE`가 `T`로 바꿔 넘기면 `T`는 `b.c` 기준으로
    조회한다(앵커 `a.c`가 아니라). `a.c::f`가 `T`를 직접 써서 `a.c` 기준으로는 이미 조회했어도
    `b.c` 기준은 한 번 더 한다(이름이 아니라 (이름, 파일)로 센다)."""
    files = {
        "a.c": ["int f(void) { return A + T; }"], "b.c": ["int g(void) { return MODE; }"],
        "h.h": ["#define MODE T"],
    }
    f = Location(file="a.c", symbol="f", line_start=1, line_end=1)
    g = Location(file="b.c", symbol="g", line_start=1, line_end=1)
    calls: list[tuple[set, str]] = []

    def defs(names, file):  # noqa: ANN001, ANN202
        calls.append((set(names), file))
        mode = Location(file="h.h", symbol="MODE", line_start=1, line_end=1)
        return [mode] if "MODE" in names else []

    ctx, events, _, _ = _unit(
        files, [f, g], auditor_definitions=1, auditor_callee_wrappers=1, definitions=defs
    )
    _unit_run(ctx, events, f, [("a.c", 1), ("b.c", 1)])
    assert calls == [({"f", "A", "T"}, "a.c"), ({"g", "MODE"}, "b.c"), ({"T"}, "b.c")]


def test_bodies_in_the_anchor_file_make_one_definition_lookup_as_before() -> None:
    """실린 함수가 모두 앵커 파일이면 조회는 **한 번**, 이름은 전부 합친 집합, 파일은 앵커 —
    파일별로 갈라 부르게 된 뒤에도 흔한 경우의 호출과 SOURCE는 예전과 같다(바이트 동일)."""
    calls: list[tuple[set, str]] = []
    macro = Location(file="u.c", symbol="x10", line_start=30, line_end=30)

    def defs(names, file):  # noqa: ANN001, ANN202
        calls.append((set(names), file))
        return [macro]

    ctx, events, _, _ = _unit(_U_FILE, [_U_F, _U_H], auditor_definitions=1, definitions=defs)
    source, _ = _unit_run(ctx, events, _U_F, [("u.c", 2), ("u.c", 11)])
    ((names, file),) = calls
    assert file == "u.c" and {"w1", "x1", "w10", "x10"} <= names
    assert source.count("/* ===== u.c:") == 3  # f · h · 정의


def _load_indexer():  # noqa: ANN202
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "harness-tools" / "indexer.py"
    spec = importlib.util.spec_from_file_location("harness_indexer_slice_targets", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _real_source(tmp_path: Path, files: dict[str, str], entry: str, lines: list, **opts):  # noqa: ANN202, ANN003
    """**실제 인덱서 → `GraphIndex` → `_auditor_source`.** `files`(이름 → 원문)를 `indexer.main`으로
    색인하고 그 `definitions_of`(비제품 강등 켠 것 — 러너가 `auditor_definitions`를 켜면 쓰는 꼴)를
    조회로 꽂는다. mock이 내는 위치는 「잡히는 모양」만 고르게 되므로(CLAUDE.md §2.5) 실제 인덱스가
    같은 범위에 내는 typedef 이름·태그 행까지 이 경로로 핀한다. `entry`는 앵커 함수 이름이다."""
    from src.adapters import index_graph

    root = tmp_path / "src"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    out = tmp_path / "out" / "index.json"
    argv = ["--out", str(out), "--root", str(root), *(str(root / n) for n in sorted(files))]
    assert _load_indexer().main(argv) == 0
    graph = index_graph.GraphIndex(out)
    symbols = [s for n in sorted(files) if n.endswith(".c") for s in graph.symbols_for(n) or []]
    ctx, events, _, _ = _unit(
        {name: text.splitlines() for name, text in files.items()}, symbols,
        auditor_definitions=1,
        definitions=lambda names, file: graph.definitions_of(names, file, demote_nonproduct=True),
        **opts,
    )
    return _unit_run(ctx, events, next(s for s in symbols if s.symbol == entry), lines)


def test_a_body_from_another_file_gets_its_own_headers_constant(tmp_path: Path) -> None:
    """앵커 `a.c::f`, 슬라이스가 `b.c::g`도 지나 두 함수가 실리면 `g`가 쓰는 `LIMIT`은 `b.h`의 2다
    — 앵커 파일 `a.c`의 include 그래프로 풀어 `a.h`의 1이 붙던 것이 PR #485 리뷰 P2-3이다(두
    헤더가 같은 이름을 정의해 `_reachable`이 앵커의 헤더를 고른다)."""
    source, event = _real_source(tmp_path, _FOUR_FILES, "f", [("a.c", 2), ("b.c", 2)])
    assert "int g(void)" in source
    assert "#define LIMIT 2" in source and "#define LIMIT 1" not in source
    assert (event["definitions"], event["definitions_dropped"]) == (1, 0)


_ENUM_ROWS = "".join(f"    COLOR_{i:03d} = {i},\n" for i in range(120))
# 실제 인덱스는 같은 범위에 상수 행 말고 typedef 이름·태그 행도 낸다(PR #485 2라운드 F1)
_ENUM_HEADERS = {
    "typedef": "typedef enum {\n" + _ENUM_ROWS + "} color_t;\n",
    "tag": "enum color {\n" + _ENUM_ROWS + "};\n",
}
_ENUM_BODY = '#include "e.h"\nint f(void) {{\n    {decl}\n    return {expr};\n}}\n'


@pytest.mark.parametrize(
    ("kind", "decl", "expr", "lines", "wanted"),
    [
        # 상수 하나 + 타입 이름 — 잃은 것 0, 이벤트는 타입 이름이 없을 때와 같아야 한다
        ("typedef", "color_t c = COLOR_010;", "c", [3], (10,)),
        ("typedef", "color_t c = COLOR_010;", "c + COLOR_110", [3, 4], (10, 110)),
        ("tag", "enum color c = COLOR_010;", "c + COLOR_110", [3, 4], (10, 110)),
    ],
)
def test_a_type_or_tag_name_on_the_enums_range_is_not_a_constant_that_failed_to_join(
    tmp_path: Path, kind: str, decl: str, expr: str, lines: list, wanted: tuple
) -> None:
    """실제 인덱스는 큰 `typedef enum {…} color_t;`의 범위에 상수 행 말고 `color_t`(typedef 이름)
    행도 내고, `enum color {…}`는 태그 행을 낸다. 그 이름은 줄 머리 상수가 아니라 `_trim_enum`이
    못 찾지만 **정의는 이미 SOURCE에 보인다**(닫는 줄·머리) — 못 더했다고 `definitions_dropped`로
    세면 게이트 사유 `DEFINITION_DROPPED`까지 거짓 양성이다(PR #485 2라운드 F1)."""
    text = _ENUM_BODY.format(decl=decl, expr=expr)
    source, event = _real_source(
        tmp_path, {"u.c": text, "e.h": _ENUM_HEADERS[kind]}, "f", [("u.c", n) for n in lines]
    )
    for value in wanted:
        assert f"    COLOR_{value:03d} = {value}," in source, value
    assert _counts(event) == (1, 0, 1)


def test_several_constants_on_one_line_of_an_enum_are_one_visible_line(tmp_path: Path) -> None:
    """한 줄에 상수 셋씩 100줄(`K010_A = .., K010_B = .., K010_C = ..`) — 둘째 `K010_B`는 줄 머리가
    아니라 `_trim_enum`이 못 찾지만 첫 `K010_A`의 줄에 같이 실려 있다 -> 보이는 정의라 안 센다
    (F1)."""
    rows = "".join(
        f"    K{i:03d}_A = {i}, K{i:03d}_B = {i + 1}, K{i:03d}_C = {i + 2},\n" for i in range(100)
    )
    files = {
        "u.c": '#include "e.h"\nint f(void) {\n    return K010_A + K010_B;\n}\n',
        "e.h": "enum {\n" + rows + "};\n",
    }
    source, event = _real_source(tmp_path, files, "f", [("u.c", 3)])
    assert "K010_A = 10, K010_B = 11" in source
    assert _counts(event) == (1, 0, 1)


def test_a_name_seen_on_the_enums_block_does_not_stop_a_later_constant_from_joining() -> None:
    """보이는 이름(typedef 이름)을 「이미 처리한 이름」에 기억해 둘 뿐 **블록을 줄이는 이름 목록에는
    안 넣는다** — 넣으면 줄 머리 상수가 아닌 그 이름 때문에 `_trim_enum`이 `None`을 내 그 뒤의
    상수가 영영 못 합친다. 조회가 상수 · typedef 이름 · 상수 순으로 오는 mock(실제 인덱스의 정렬은
    `COLOR_…` < `color_t`라 이름이 뒤에 오지만, 이름이 앞서 오는 이름이면 이 순서다)."""
    type_row = Location(file="e.h", symbol="color_t", line_start=1, line_end=len(_BIG_ENUM))
    rows = [*_enum_constants("COLOR_010"), type_row, *_enum_constants("COLOR_110")]
    source, event = _carried_enum(definitions=lambda _n, _f: rows)
    assert "    COLOR_010 = 10," in source and "    COLOR_110 = 110," in source
    assert _counts(event) == (1, 0, 1)


def _size_of(source: str) -> int:
    """`_unit`의 토큰 카운터(단어 수)로 센 SOURCE의 총 크기 — 블록 크기의 합 + 블록 사이 1."""
    return len(source.split()) + source.count("/* ===== ") - 1


def test_widening_an_enum_block_is_refused_exactly_at_the_total_cap() -> None:
    """넓히기의 전체 상한 검사는 정확히 상한에서 수락·거절한다 — 넓힌 뒤 크기가 상한이면 싣고 1
    넘으면 못 더하고 `dropped`다(변이: 그 검사를 빼면 거절 쪽이 빨개진다 · PR #485 2라운드 F4)."""
    wide, _ = _carried_enum("COLOR_010", "COLOR_110", auditor_source_tokens=10_000)
    need = _size_of(wide)
    source, event = _carried_enum("COLOR_010", "COLOR_110", auditor_source_tokens=need)
    assert "    COLOR_110 = 110," in source and _counts(event) == (1, 0, 1)
    source, event = _carried_enum("COLOR_010", "COLOR_110", auditor_source_tokens=need - 1)
    assert "    COLOR_010 = 10," in source and "    COLOR_110 = 110," not in source
    assert _counts(event) == (1, 1, 1)


def test_widening_an_enum_block_keeps_the_size_account_exact_for_what_follows() -> None:
    """블록을 넓힌 만큼만 크기가 는다 — 옛 블록을 안 빼면(변이) 뒤따르는 정의가 상한 안인데도
    허위로 밀린다. 넓힌 enum 뒤에 작은 매크로 정의를 하나 두고 총 상한을 **딱 그 합**으로 잡으면
    둘 다 실리고, 1 모자라면 매크로만 `dropped`다(PR #485 2라운드 F4)."""
    macro = Location(file="m.h", symbol="LIMIT", line_start=1, line_end=1)
    opts = {
        "definitions": lambda _n, _f: [*_enum_constants("COLOR_010", "COLOR_110"), macro],
        "files": {"m.h": ["#define LIMIT 64"]},
    }
    full, event = _carried_enum(auditor_source_tokens=10_000, **opts)
    assert _counts(event) == (2, 0, 1)
    need = _size_of(full)
    source, event = _carried_enum(auditor_source_tokens=need, **opts)
    assert "#define LIMIT 64" in source and _counts(event) == (2, 0, 1)
    source, event = _carried_enum(auditor_source_tokens=need - 1, **opts)
    assert "#define LIMIT 64" not in source and _counts(event) == (1, 1, 1)


def test_a_constant_that_could_not_join_is_not_counted_dropped_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """못 더한 상수는 기억해 둔다 — `a.c`가 `COLOR_110`을 직접 쓰고 `b.c`의 매크로 `MODE`가 같은
    상수로 바꿔 넘기면 별칭 라운드가 같은 상수를 (이름, 파일) 쌍으로 한 번 더 묻는다. 둘째에서도
    넓히기가 안 되지만 한 상수라 `definitions_dropped`는 1이다(예전 `(2, 2, 1)` — PR #485 2라운드
    F7)."""
    from src.orchestrator import runner

    one = runner._block(_E_ENUM, runner._trim_enum("\n".join(_BIG_ENUM), ["COLOR_010"]),
                        end=len(_BIG_ENUM))
    monkeypatch.setattr(runner, "_AUDITOR_DEFINITION_CHARS", len(one))  # 상수 하나만 들어간다
    files = {
        "a.c": ["int f(void) { return COLOR_010 + COLOR_110; }"],
        "b.c": ["int g(void) { return MODE; }"], "e.h": _BIG_ENUM,
        "m.h": ["#define MODE COLOR_110"],
    }
    f = Location(file="a.c", symbol="f", line_start=1, line_end=1)
    g = Location(file="b.c", symbol="g", line_start=1, line_end=1)
    macro = Location(file="m.h", symbol="MODE", line_start=1, line_end=1)

    def defs(names, _file):  # noqa: ANN001, ANN202
        asked = sorted(name for name in names if name.startswith("COLOR_"))
        return [*([macro] if "MODE" in names else []), *_enum_constants(*asked)]

    ctx, events, _, _ = _unit(
        files, [f, g], auditor_definitions=1, auditor_callee_wrappers=1, definitions=defs
    )
    source, event = _unit_run(ctx, events, f, [("a.c", 1), ("b.c", 1)])
    assert "COLOR_110 =" not in source
    assert _counts(event) == (2, 1, 1)


# 같은 매크로 이름 MODE — a.h는 T로 바꿔 넘기고 b.h는 상수 5. T도 헤더마다 값이 다르다
_ALIAS_FILES = {
    "a.c": '#include "a.h"\nint f(void) { return MODE; }\n',
    "b.c": '#include "b.h"\nint g(void) { return MODE; }\n',
    "a.h": "#define T 1\n#define MODE T\n",
    "b.h": "#define T 99\n#define MODE 5\n",
}


def test_an_alias_target_is_asked_only_for_the_files_that_resolved_the_alias(
    tmp_path: Path,
) -> None:
    """별칭 대상은 **그 별칭 매크로 정의로 풀린 파일**의 기준으로만 조회한다 — 두 본문이 모두
    `MODE`를 써도 `b.c`의 `MODE`는 `b.h`의 5라 `T`와 무관하다. 그 매크로를 쓴 모든 파일로 귀속하면
    `b.h`의 `#define T 99`가 붙는다(틀린 정의가 붙는 쪽이 안 붙는 쪽보다 나쁘다 — PR #485 2라운드
    F3, 실제 인덱서 4파일)."""
    source, event = _real_source(
        tmp_path, _ALIAS_FILES, "f", [("a.c", 2), ("b.c", 2)], auditor_callee_wrappers=1
    )
    assert "#define T 1" in source and "#define MODE T" in source and "#define MODE 5" in source
    assert "#define T 99" not in source
    assert (event["definitions"], event["alias_definitions"]) == (3, 1)


def test_limitation_a_type_name_asked_first_drops_the_whole_enum_and_hides_its_constants(
    tmp_path: Path,
) -> None:
    """**이 동작은 한계이지 의도가 아니다**(수정 전과 같은 선행 결함 — 이슈 후보). 조건은 「줄 머리
    상수가 아닌 이름(typedef 이름·태그)이 enum의 줄 머리 상수보다 **먼저 처리된다**」이다 — 처리
    순서는 정의 조회의 이름 정렬 다음에 슬라이스 줄에 나온 이름이 앞으로 당겨진 순서(안정 정렬)다.
    이 테스트는 그중 「슬라이스 줄에는 타입 이름(`color_t c;`)만 있고 상수는 다른 줄」인 꼴이다(정렬
    사유의 꼴은 아래 짝 테스트). 타입 이름 행이 먼저 오면 `_trim_enum`이 `None`이라 큰 enum이
    **통째로** `dropped`되고, 그 범위가 `seen`에 올라 뒤따르는 `COLOR_010`·`COLOR_110`은 겹침으로
    조용히 안 실린다. 고치는 날 이 테스트가 빨개지면 기대값을 `(1, 0, 1)`과 두 상수의 실림으로
    뒤집는다."""
    text = (
        '#include "e.h"\nint f(void) {\n    color_t c;\n    c = COLOR_010;\n'
        "    return c + COLOR_110;\n}\n"
    )
    source, event = _real_source(
        tmp_path, {"u.c": text, "e.h": _ENUM_HEADERS["typedef"]}, "f", [("u.c", 3)]
    )
    assert "COLOR_010 =" not in source and "COLOR_110 =" not in source
    assert _counts(event) == (0, 1, 0)


def test_limitation_a_type_name_that_sorts_before_its_constants_hides_them_on_the_slice(
    tmp_path: Path,
) -> None:
    """**이 동작은 한계이지 의도가 아니다**(수정 전과 같은 선행 결함 — 이슈 후보). 위 한계가
    「슬라이스 줄에 이름만 있을 때」만이 아니라 **타입·태그 이름이 상수보다 정렬상 앞서면** 타입과
    상수가 모두 슬라이스 줄에 있어도 난다는 짝: FFmpeg 꼴 `enum AVPixelFormat { AV_PIX_FMT_…}`에서
    `AVPixelFormat`(`AVP`)이 `AV_PIX_FMT_X010`(`AV_`)보다 앞서 처리돼 enum이 통째로 `dropped`되고
    두 상수가 안 실린다 — **이 PR이 겨냥한 FFmpeg 꼴에서 상수 주변만 싣는 기능이 걸리지 않는다.**
    고치는 날 이 테스트가 빨개지면 기대값을 `(1, 0, 1)`과 두 상수의 실림으로 뒤집는다."""
    rows = "".join(f"    AV_PIX_FMT_X{i:03d} = {i},\n" for i in range(120))
    header = "enum AVPixelFormat {\n    AV_PIX_FMT_NONE = -1,\n" + rows + "    AV_PIX_FMT_NB\n};\n"
    files = {
        "pix.h": header,
        "u.c": (
            '#include "pix.h"\nint f(void) {\n    enum AVPixelFormat fmt = AV_PIX_FMT_X010;\n'
            "    return fmt == AV_PIX_FMT_X110;\n}\n"
        ),
    }
    source, event = _real_source(tmp_path, files, "f", [("u.c", 3), ("u.c", 4)])
    assert "AV_PIX_FMT_X010 =" not in source and "AV_PIX_FMT_X110 =" not in source
    assert _counts(event) == (0, 1, 0)


def test_limitation_a_constant_only_mentioned_in_the_block_counts_as_shown(
    tmp_path: Path,
) -> None:
    """**이 동작은 한계이지 의도가 아니다**(수정 전과 같은 침묵 — 이슈 후보). 줄 머리 상수가 아닌
    이름은 이미 실린 블록에 **낱말로** 있으면 보이는 것으로 읽는다(`widen_enum`). 낱말이 보이는 것과
    그 이름의 **정의가 보이는 것**은 다르다 — 끝줄 `AAA_ALIAS = OP005_B,`가 비머리 상수를 값으로
    언급하면 `OP005_B = 6`은 SOURCE에 없는데 `definitions_dropped`는 0이다(정직하면 1). 주석이
    언급해도 같다. 고치는 날 이 테스트가 빨개지면 기대값을 `(1, 1, 1)`로 뒤집는다."""
    rows = "".join(f"    OP{i:03d}_A = {i}, OP{i:03d}_B = {i + 1},\n" for i in range(100))
    files = {
        "e.h": "typedef enum {\n" + rows + "    AAA_ALIAS = OP005_B,\n} op_t;\n",
        "u.c": '#include "e.h"\nint f(void) {\n    return AAA_ALIAS + OP005_B;\n}\n',
    }
    source, event = _real_source(tmp_path, files, "f", [("u.c", 3)])
    assert "AAA_ALIAS = OP005_B" in source and "OP005_B = 6" not in source
    assert _counts(event) == (1, 0, 1)


def test_windows_dropped_for_the_size_cap_are_counted_in_the_marker_too() -> None:
    """상한 6 안이어도 SOURCE 크기 상한(여기서는 30단어)에 못 붙인 창은 모델이 못 본다 — 같은 줄이
    센다. 둘 중 하나(init)만 붙고 하나(setup)는 `write_windows_dropped`로 빠진다."""
    ctx, events, _, _ = _unit(
        _WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=6, auditor_source_tokens=30,
        auditor_write_window_marker=1,
        writes={"dst": [Location(file="w.c", symbol="init", line_start=41, line_end=41)]},
        field_writes={"len": [Location(file="w.c", symbol="setup", line_start=22, line_end=22)]},
    )
    source, event = _unit_run(ctx, events, _WS_F, [("w.c", 2)])
    assert (event["write_windows"], event["write_windows_dropped"]) == (1, 1)
    assert event["write_windows_omitted"] == 1
    assert source.endswith(_WS_MORE)


def test_omitted_sites_whose_windows_overlap_count_as_one_window() -> None:
    """상한 1에 init(41)이 붙고, 빠진 `setup`의 쓰기 둘(22 · 23)은 창이 겹친다(둘 다 setup 20-24로
    잘린다) — 붙였다면 창은 하나이므로 N은 1이다. 셈에서 앞서 센 창을 안 빼면 2가 된다."""
    ctx, events, _, _ = _unit(
        _WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=1,
        auditor_write_window_marker=1,
        writes={"dst": [Location(file="w.c", symbol="init", line_start=41, line_end=41)]},
        field_writes={"len": [
            Location(file="w.c", symbol="setup", line_start=22, line_end=22),
            Location(file="w.c", symbol="setup", line_start=23, line_end=23),
        ]},
    )
    source, event = _unit_run(ctx, events, _WS_F, [("w.c", 2)])
    assert (event["write_windows"], event["write_sites_found"]) == (1, 3)
    assert event["write_windows_omitted"] == 1
    assert source.endswith(_WS_MORE)


def test_marker_follows_a_bare_anchor_when_every_window_is_size_dropped() -> None:
    """창이 전부 크기 상한에 떨어지면 블록은 entry 하나뿐이라 SOURCE는 배너 없는 `anchor_text`다 —
    표식은 그 뒤에 붙는다."""
    ctx, events, _, _ = _unit(
        _WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=6, auditor_source_tokens=20,
        auditor_write_window_marker=1,
        writes={"dst": [Location(file="w.c", symbol="init", line_start=41, line_end=41)]},
        field_writes={"len": [Location(file="w.c", symbol="setup", line_start=22, line_end=22)]},
    )
    source, event = _unit_run(ctx, events, _WS_F, [("w.c", 2)])
    assert (event["write_windows"], event["write_windows_dropped"]) == (0, 2)
    marker = "/* ... +2 more write windows not shown ... */"
    assert source == "\n".join([*_WS_FILE["w.c"][0:3], marker])
    assert event["chars"] == len(source)  # 표식은 예산 밖이지만 `chars`에는 든다


# --- 크기 상한에 못 실린 범위 안의 쓰기 지점도 N에 든다 (#650 리뷰 MEDIUM-1) ---------------------
# 이전 판은 N을 `seen`(상한에 걸려 뺀 함수·창도 든다)으로 잘라, 모델이 못 보는 코드 안의 쓰기 지점이
# 안 실리고 안 세어졌다. 이제 센다 — 실제로 SOURCE에 든 범위(`shown`) 안만 뺀다.
_SD_FILE = {
    "s.c": [
        "int f(struct pkt *p) {",  # 1
        "  use(p->len);",  # 2 — 슬라이스 줄: 필드 len
        "}",  # 3
        *[f"filler{i}" for i in range(4, 20)],  # 4-19
        "void g(struct pkt *p, int n) {",  # 20
        "  int a = 0;",  # 21
        "  p->len = n * 2;",  # 22 — 필드 쓰기(g 안 · 슬라이스가 지나는 함수)
        "  a += 1;",  # 23
        "  a += 2;",  # 24
        "  sink(p, a);",  # 25 — 슬라이스 줄(g가 실릴 후보지만 크기 상한에 떨어진다)
        "  a += 3;", "  a += 4;", "  a += 5;", "  a += 6;",  # 26-29
        "}",  # 30
    ]
}
_SD_F = Location(file="s.c", symbol="f", line_start=1, line_end=3)
_SD_G = Location(file="s.c", symbol="g", line_start=20, line_end=30)


def _size_dropped(**opts):  # noqa: ANN202
    ctx, events, _, _ = _unit(
        _SD_FILE, [_SD_F, _SD_G], auditor_write_sites=6, auditor_source_tokens=20,
        field_writes={"len": [Location(file="s.c", symbol="g", line_start=22, line_end=22)]},
        **opts,
    )
    return _unit_run(ctx, events, _SD_F, [("s.c", 2), ("s.c", 25)])


def test_a_write_site_inside_a_size_dropped_function_is_counted() -> None:
    """`g`(20-30)는 슬라이스가 지나지만 상한 20에 못 실린다 — 그 안의 `p->len = n * 2;`(22)는
    SOURCE에 없다. `seen`으로 자르던 판은 그 지점을 이미 본 것으로 읽어 N이 0이고 줄도 없었다."""
    source, event = _size_dropped(auditor_write_window_marker=1)
    assert "p->len = n * 2;" not in source and event["dropped"] == 1
    assert event["write_windows_omitted"] >= 1
    assert source.endswith("/* ... +1 more write windows not shown ... */")


def test_marker_option_off_leaves_source_bytes_as_before_the_marker_existed() -> None:
    """`auditor_write_window_marker` 0(기본) — 못 실은 창이 있어도 줄이 없다. 켠 SOURCE는 꺼진
    SOURCE에 그 줄 하나를 줄바꿈으로 이은 것이다(= 꺼진 쪽이 #650 이전과 바이트 동일). 수는
    trace에 남는다."""
    off_source, off = _write_sites(1, auditor_write_window_marker=0)
    on_source, on = _write_sites(1)
    assert "more write windows" not in off_source
    assert on_source == off_source + "\n" + _WS_MORE
    assert off["write_windows_omitted"] == on["write_windows_omitted"] == 1
    assert off["chars"] + len("\n" + _WS_MORE) == on["chars"]


# --- 부분 겹침: 생략 창은 `_clip_around`로 깎아 센다 (#650 리뷰 LOW-1) ----------------------------
# 핀 없이는 「겹치면 버린다」·「안 깎은 창을 센다」는 변이가 살았다.
_PO_FILE = {
    "p.c": [
        "int f(struct pkt *p) {",  # 1
        "  memcpy(dst, p->payload, p->len);",  # 2 — 슬라이스 줄: dst · 필드 payload·len
        "}",  # 3
        *[f"filler{i}" for i in range(4, 20)],  # 4-19
        *[f"line{i}" for i in range(20, 53)],  # 20-52 — g 20-40 · m 20-50 · init 50-52가 겹쳐 쓴다
    ]
}
_PO_F = Location(file="p.c", symbol="f", line_start=1, line_end=3)


def _site_at(symbol: str, line: int) -> Location:
    return Location(file="p.c", symbol=symbol, line_start=line, line_end=line)


def _partial(symbols: list[Location], writes: dict, field: list[Location], cap: int):  # noqa: ANN202
    ctx, events, _, _ = _unit(
        _PO_FILE, [_PO_F, *symbols], auditor_write_sites=cap, auditor_write_window_marker=1,
        writes=writes, field_writes={"len": field},
    )
    return _unit_run(ctx, events, _PO_F, [("p.c", 2)])


def test_omitted_windows_that_partly_overlap_are_cut_not_dropped() -> None:
    """g(20-40)의 쓰기 22 · 28 — 창은 19-25 → 20-25, 25-31이고 둘은 25에서 겹친다. 둘째는 버리지
    않고 깎여(26-31) 따로 선다 — 상한을 3으로 올렸을 때 상한 1 뒤에 실제로 더 붙는 창 수와 같다.
    겹치면 버리는 셈이면 1이 된다."""
    g = Location(file="p.c", symbol="g", line_start=20, line_end=40)
    init = Location(file="p.c", symbol="init", line_start=50, line_end=52)
    args = ([g, init], {"dst": [_site_at("init", 51)]}, [_site_at("g", 22), _site_at("g", 28)])
    source, event = _partial(*args, cap=1)
    _, wide = _partial(*args, cap=3)
    assert wide["write_windows"] == 3
    assert event["write_windows_omitted"] == wide["write_windows"] - 1 == 2
    assert source.endswith("/* ... +2 more write windows not shown ... */")


def test_an_omitted_window_is_clipped_before_it_hides_a_later_site() -> None:
    """k(33-33)가 먼저 붙고 빠진 m(20-50)의 쓰기는 35 · 32다. 35의 창 32-38은 k에 깎여 34-38이다.
    깎인 채로 숨은 목록에 들어가야 32(창 29-32)가 따로 선다. 안 깎은 32-38을 넣으면 32가 그 안이라
    N이 1로 줄어든다."""
    k = Location(file="p.c", symbol="k", line_start=33, line_end=33)
    m = Location(file="p.c", symbol="m", line_start=20, line_end=50)
    args = ([k, m], {}, [_site_at("k", 33), _site_at("m", 35), _site_at("m", 32)])
    source, event = _partial(*args, cap=1)
    assert event["write_windows"] == 1
    assert event["write_windows_omitted"] == 2
    assert source.endswith("/* ... +2 more write windows not shown ... */")


def _trimmed_entry_write(at: int, **opts):  # noqa: ANN202
    """f(1-81) · 슬라이스 줄 2 · `len` 쓰기가 `at`행 · entry 줄이기 켬. 슬라이스 줄 앞뒤 20줄 + 닫는
    줄만 남아 22행 뒤는 뺀 구간이다(40행은 사라진다)."""
    lines = {
        "t.c": [
            "int f(struct pkt *p) {", "  use(p->len);",
            *[f"  w{i} x{i};" for i in range(3, 81)], "}",
        ]
    }
    entry = Location(file="t.c", symbol="f", line_start=1, line_end=81)
    ctx, events, _, _ = _unit(
        lines, [entry], auditor_write_sites=6, auditor_source_tokens=60, auditor_entry_trim=1,
        field_writes={"len": [Location(file="t.c", symbol="f", line_start=at, line_end=at)]},
        **{"auditor_write_window_marker": 1, **opts},
    )
    source, event = _unit_run(ctx, events, entry, [("t.c", 2)])
    assert event["entry_omitted_lines"] > 0 and "lines omitted" in source
    return source, event


def test_a_write_site_in_the_lines_a_trimmed_entry_dropped_is_counted_as_hidden() -> None:
    """뺀 구간 안의 쓰기 지점(40행)은 SOURCE에 없다 — 안 셌을 때는 `write_windows_omitted` 0에 줄도
    없어 모델이 그 쓰기를 「없다」로 읽었다(#650 2차 리뷰 MEDIUM). 옵션 0이면 수만 남는다."""
    source, event = _trimmed_entry_write(40)
    assert "w40 x40" not in source
    assert event["write_windows_omitted"] == 1
    assert source.endswith("/* ... +1 more write windows not shown ... */")
    off_source, off = _trimmed_entry_write(40, auditor_write_window_marker=0)
    assert off["write_windows_omitted"] == 1 and "more write windows" not in off_source


def test_the_kept_ranges_of_a_trimmed_entry_skip_every_omitted_gap() -> None:
    """뺀 구간이 둘이면 남은 범위는 셋이다 — 각 표시가 말하는 줄 수만큼 원래 번호를 건너뛴다.
    줄 끝 `\\r`이 붙은 표시도 표시로 읽는다(`read_source`는 지금 `\\r`을 접지만 기대지 않는다)."""
    anchor = Location(file="t.c", symbol="f", line_start=10, line_end=29)
    text = "\n".join([
        "a", "b", "/* ... 5 lines omitted ... */\r", "c", "/* ... 9 lines omitted ... */", "d",
    ])
    got = [(r.line_start, r.line_end) for r in _shown_ranges(anchor, text)]
    assert got == [(10, 11), (17, 17), (27, 27)]


def test_a_write_site_in_a_kept_line_of_a_trimmed_entry_is_not_counted_as_hidden() -> None:
    """줄인 entry 함수에 **남은** 줄의 지점(2행의 `p->len`)을 「못 본 창」으로 세면 보이는 줄을 두고
    「더 있다」고 말하게 된다 — 숨은 쓰기 창은 0이고 줄도 없다."""
    source, event = _trimmed_entry_write(2)
    assert "  use(p->len);" in source
    assert event["write_windows_omitted"] == 0 and "more write windows" not in source
