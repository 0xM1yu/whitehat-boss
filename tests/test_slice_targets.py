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
from src.orchestrator.runner import RunSpec
from src.schemas.common import Location
from src.schemas.enums import Role
from src.tokens import estimate_tokens
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
    """렌더는 run 전에 **전부** 한다. 러너는 렌더 오류를 안 잡으므로, 슬라이스마다 그때그때
    렌더하면 앞 슬라이스(`_CASE_B` — 정적 신호 줄이라 먼저 돈다)의 모델 호출을 태운 뒤 산출물
    없이 죽는다."""
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
        }, role


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
