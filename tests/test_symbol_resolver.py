"""`src/adapters/symbol_resolver.py`의 순수 함수 테스트 — Phase 5 P3(PR-A resolver) A 단계.

명세 문자열은 Notion 「Scout RAG baseline」 스냅샷(바이트 대조 통과 —
`…\\444e006f-1c8b-4b5e-ac54-12900872bf08\\scratchpad\\PHASE5_sumin.md` §5, 원문 스냅샷
`…\\ddaa755d-b591-40f7-92b6-119955ecd128\\scratchpad\\notion_rag_baseline_2026-09-26.txt`)에서
그대로 옮겼다 — 의역하지 않는다. Q10(대입 주의문은 라벨 3·13에만)은
`PLAN_PHASE5_NE_LOOP.md` §2.0 council 판정을 따른다 — `PHASE5_sumin.md`의 「주의문을 넣은
판」 지시는 여기서는 폐기된 것으로 취급한다.

이 파일은 A 단계 범위만 본다 — 조회(`table()`)·파일 읽기·`resolve()` 조립은 B 단계다.
"""

import pytest

from src.adapters.symbol_resolver import (
    FIXABLE_REASONS,
    LABEL_ASSIGNMENT_SAME_FUNCTION,
    LABEL_ASSIGNMENT_TO_FIELD,
    LABEL_ASSIGNMENT_TO_GLOBAL,
    LABEL_DECLARATION_LOCAL,
    LABEL_MACRO_NAMED_IN_DECLARATION,
    REASON_CLOSEST_NAME,
    REASON_LIBC,
    REASON_NO_IDENTIFIER,
    REASON_NO_TARGET,
    REASON_NOT_FOUND,
    REASON_NOT_IDENTIFIER,
    REASON_OUTSIDE_REPO,
    REASON_PLACEHOLDER,
    Candidate,
    closest_name,
    is_duplicate_in_source,
    is_libc_symbol,
    is_rejected,
    needs_assignment_note,
    normalize_target,
    pick_best_candidate,
    render_already_in_source,
    render_followup,
    render_fragment,
    render_unfilled,
    score_candidate,
)

# --- normalize_target: §5.2 8단계 표 ----------------------------------------


@pytest.mark.parametrize(
    ("raw", "base", "chain"),
    [
        ("buf@src/a.c:L10-12", "buf", ()),  # 0단계(Q2) — @file:L 접미 제거, 점수엔 안 쓴다
        ("CNV_PARTS_SO_FAR()", "CNV_PARTS_SO_FAR", ()),  # 2단계 — 괄호 제거
        ("patches[i][1]", "patches", ()),  # 3단계 — 배열 첨자 제거
        ("struct ndp_msg", "ndp_msg", ()),  # 4단계 — struct/union 등 접두 제거
        ("st1->facet_start", "st1", ("facet_start",)),  # 5단계 — -> 분해
        ("*buf", "buf", ()),  # 6단계 — 앞쪽 * 제거
        ("st1_file initialization", "st1_file", ()),  # 7단계 — 공백 뒤 서술 제거
        # 전체 예(§5.2) — 3단계([facet] 제거) 다음 5단계(-> . 분해)까지 이어진다.
        ("stl->neighbors_start[facet].neighbor", "stl", ("neighbors_start", "neighbor")),
    ],
)
def test_normalize_target_applies_each_spec_step(raw, base, chain):
    assert normalize_target(raw) == (base, chain)


@pytest.mark.parametrize("raw", ["unknown", "n/a", "none", "null", "-", "tbd", "UNKNOWN"])
def test_normalize_target_rejects_placeholder_values(raw):
    result = normalize_target(raw)
    assert is_rejected(result)
    assert result[0] == REASON_PLACEHOLDER


def test_normalize_target_rejects_an_empty_target_as_no_target():
    result = normalize_target("")
    assert is_rejected(result)
    assert result[0] == REASON_NO_TARGET


def test_normalize_target_rejects_a_target_with_no_identifier_left():
    # 괄호를 걷어내면 아무것도 안 남는 경우 — "정규화 후 식별자가 하나도 남지 않은 경우".
    result = normalize_target("()")
    assert is_rejected(result)
    assert result[0] == REASON_NO_IDENTIFIER


def test_normalize_target_rejects_a_bracket_only_value_as_no_identifier_too():
    result = normalize_target("[]")
    assert is_rejected(result)
    assert result[0] == REASON_NO_IDENTIFIER


def test_normalize_target_rejects_a_value_that_is_not_a_c_identifier():
    # 명세 거부 예 — 원문 오타(got 'stl:err')가 아니라 남은 값을 그대로 넣는 규칙으로 쓴다.
    result = normalize_target("stl:error")
    assert is_rejected(result)
    assert result == (REASON_NOT_IDENTIFIER, "stl:error")


def test_is_rejected_tells_success_from_failure_by_the_second_elements_type():
    assert is_rejected(normalize_target("stl:error")) is True
    assert is_rejected(normalize_target("buf")) is False


# --- render_unfilled: §5.6 UNFILLED 사유 여덜 --------------------------------


def test_render_unfilled_reproduces_the_placeholder_rejection_example():
    line = render_unfilled(kind="variable", target="unknown", reason=REASON_PLACEHOLDER)
    assert line == (
        "Not resolved: variable:unknown (the target was a placeholder, so there was "
        "nothing to look up; name the symbol you need)."
    )


def test_render_unfilled_reproduces_the_not_identifier_rejection_example():
    # 원문이 'stl:err'로 적혀 있으나(오타로 보임) 테스트는 남은 값을 그대로 넣는 규칙을 쓴다.
    line = render_unfilled(
        kind="variable", target="stl:error", reason=REASON_NOT_IDENTIFIER, detail="stl:error"
    )
    assert line == (
        "Not resolved: variable:stl:error (could not read a symbol name out of it "
        "(got 'stl:error'))."
    )


def test_render_unfilled_reproduces_the_hop1_libc_line():
    line = render_unfilled(kind="function", target="strdup", reason=REASON_LIBC)
    assert line == "Not resolved: function:strdup (C standard library symbol)."


@pytest.mark.parametrize(
    ("reason", "detail", "expected_phrase"),
    [
        (REASON_OUTSIDE_REPO, "", "called in this repository but defined outside it"),
        (REASON_NOT_FOUND, "", "no definition found in the repository"),
        (REASON_CLOSEST_NAME, "ndp_msg_free", "closest name in repository: ndp_msg_free"),
        (REASON_NO_TARGET, "", "no target was given"),
        (REASON_NO_IDENTIFIER, "", "the target held no identifier"),
    ],
)
def test_render_unfilled_covers_the_remaining_reasons(reason, detail, expected_phrase):
    line = render_unfilled(kind="function", target="doit", reason=reason, detail=detail)
    assert line == f"Not resolved: function:doit ({expected_phrase})."


def test_fixable_reasons_are_exactly_the_four_normalization_failures():
    assert FIXABLE_REASONS == {
        REASON_PLACEHOLDER,
        REASON_NO_TARGET,
        REASON_NO_IDENTIFIER,
        REASON_NOT_IDENTIFIER,
    }


# --- closest_name / is_libc_symbol ------------------------------------------


def test_closest_name_finds_a_similarly_spelled_symbol():
    names = ["ndp_msg_type", "ndp_msg_free", "other"]
    assert closest_name("ndp_msg_typ", names) == "ndp_msg_type"


def test_closest_name_returns_none_when_nothing_is_close():
    assert closest_name("zzzzzzzzzz", ["ndp_msg_type", "other"]) is None


def test_is_libc_symbol_recognizes_strdup_from_the_hop1_example():
    assert is_libc_symbol("strdup") is True


def test_is_libc_symbol_rejects_a_repository_local_name():
    assert is_libc_symbol("ndp_msg_type") is False


# --- render_fragment: 조각 틀 + 줄번호 폭 ------------------------------------


def test_render_fragment_reproduces_the_hop1_declaration_local_block():
    block = render_fragment(
        name="Buf",
        label=LABEL_DECLARATION_LOCAL,
        file="android-gif-drawable/src/main/c/giflib/dgif_lib.c",
        lines=[(44, "\tchar Buf[GIF_STAMP_LEN + 1];")],
    )
    assert block == (
        "/* --- retrieved: Buf (declaration (local)) --- */\n"
        "/* ===== android-gif-drawable/src/main/c/giflib/dgif_lib.c ===== */\n"
        "  44| \tchar Buf[GIF_STAMP_LEN + 1];"
    )


def test_render_fragment_right_justifies_line_numbers_to_four_columns():
    block = render_fragment(name="x", label="macro", file="f.c", lines=[(7, "a"), (123, "b")])
    lines = block.split("\n")
    assert lines[2] == "   7| a"
    assert lines[3] == " 123| b"


def test_render_fragment_omits_the_trailing_space_after_the_pipe_for_a_blank_line():
    block = render_fragment(name="x", label="macro", file="f.c", lines=[(127, "")])
    assert block.split("\n")[2] == " 127|"


# --- render_already_in_source / is_duplicate_in_source ----------------------


def test_render_already_in_source_reproduces_the_spec_example():
    line = render_already_in_source(
        kind="function", name="_compare", file="lib/tables.c", line_start=37, line_end=45
    )
    assert line == "Already in SOURCE: function:_compare at lib/tables.c:L37-45."


def test_is_duplicate_in_source_is_false_at_94_percent_overlap():
    block = frozenset(range(100))
    visible = frozenset(range(94))
    assert is_duplicate_in_source(block, visible) is False


def test_is_duplicate_in_source_is_true_at_95_percent_overlap():
    block = frozenset(range(100))
    visible = frozenset(range(95))
    assert is_duplicate_in_source(block, visible) is True


def test_is_duplicate_in_source_is_false_for_an_empty_block():
    assert is_duplicate_in_source(frozenset(), frozenset({1, 2, 3})) is False


# --- needs_assignment_note (Q10 — council 원문 근거) -------------------------


def test_needs_assignment_note_is_false_for_label_4_alone():
    assert needs_assignment_note([LABEL_ASSIGNMENT_SAME_FUNCTION]) is False


@pytest.mark.parametrize(
    "label",
    [
        LABEL_ASSIGNMENT_TO_FIELD.format(field="neighbor"),
        LABEL_ASSIGNMENT_TO_GLOBAL.format(global_name="g_counter"),
    ],
)
def test_needs_assignment_note_is_true_for_label_3_or_13(label):
    assert needs_assignment_note([LABEL_ASSIGNMENT_SAME_FUNCTION, label]) is True


def test_needs_assignment_note_is_false_with_no_labels():
    assert needs_assignment_note([]) is False


# --- render_followup: hop 1 예 바이트 일치 -----------------------------------

_HOP1_EXPECTED = (
    "## Retrieved context (hop 1)\n"
    "\n"
    "The host resolved the requests from your previous turn against the original "
    "repository. Each block\n"
    "below is verbatim source and carries its own file and line numbers.\n"
    "\n"
    "/* --- retrieved: Buf (declaration (local)) --- */\n"
    "/* ===== android-gif-drawable/src/main/c/giflib/dgif_lib.c ===== */\n"
    "  44| \tchar Buf[GIF_STAMP_LEN + 1];\n"
    "\n"
    "/* --- retrieved: Buf (assignment in the same function) --- */\n"
    "/* ===== android-gif-drawable/src/main/c/giflib/dgif_lib.c ===== */\n"
    "  88| \tBuf[GIF_STAMP_LEN] = '\\0';\n"
    "\n"
    "/* --- retrieved: GIF_STAMP_LEN (macro named in the declaration) --- */\n"
    "/* ===== android-gif-drawable/src/main/c/giflib/gif_lib.h ===== */\n"
    "  17| #define GIF_STAMP_LEN sizeof(GIF_STAMP) - 1\n"
    "\n"
    "Not resolved: function:strdup (C standard library symbol).\n"
    "\n"
    "Judge the same candidate again with `SOURCE` and the `RETRIEVED` blocks read "
    "together, and output\n"
    "one complete ScoutResponse JSON. Your signal may stay what it was or change; "
    "decide from the code\n"
    "in front of you."
)


def test_render_followup_reproduces_the_hop1_example_byte_for_byte():
    # 조각 셋의 원본 줄 + UNFILLED 한 줄을 입력으로 넣는다(계약 불필요 — 조회 없음).
    fragments = [
        render_fragment(
            name="Buf",
            label=LABEL_DECLARATION_LOCAL,
            file="android-gif-drawable/src/main/c/giflib/dgif_lib.c",
            lines=[(44, "\tchar Buf[GIF_STAMP_LEN + 1];")],
        ),
        render_fragment(
            name="Buf",
            label=LABEL_ASSIGNMENT_SAME_FUNCTION,
            file="android-gif-drawable/src/main/c/giflib/dgif_lib.c",
            lines=[(88, "\tBuf[GIF_STAMP_LEN] = '\\0';")],
        ),
        render_fragment(
            name="GIF_STAMP_LEN",
            label=LABEL_MACRO_NAMED_IN_DECLARATION,
            file="android-gif-drawable/src/main/c/giflib/gif_lib.h",
            lines=[(17, "#define GIF_STAMP_LEN sizeof(GIF_STAMP) - 1")],
        ),
    ]
    unfilled = [render_unfilled(kind="function", target="strdup", reason=REASON_LIBC)]

    followup = render_followup(
        hop=1, fragments=fragments, unfilled_lines=unfilled, include_assignment_note=False
    )

    assert followup == _HOP1_EXPECTED


def test_render_followup_omits_the_note_when_no_assignment_block_is_present():
    # hop 1 예는 라벨 4·10·11뿐이라(라벨 3·13이 없다) 주의문이 없다 — council Q10.
    assert "Note: assignment blocks" not in _HOP1_EXPECTED


def test_render_followup_inserts_the_note_between_retrieved_and_unfilled_when_requested():
    # 명세는 주의문이 "들어가는 경우"만 말하고 정확한 자리는 산문으로만 설명한다(모듈
    # docstring 참고) — 이 배치는 이 저장소의 추측이라 바이트 일치가 아니라 자리만 본다.
    followup = render_followup(
        hop=2, fragments=["FRAG"], unfilled_lines=["UNFILLED"], include_assignment_note=True
    )
    assert "FRAG\n\nNote: assignment blocks" in followup
    assert "code under review.\n\nUNFILLED" in followup


# --- score_candidate / pick_best_candidate: §5.3 ----------------------------


def test_pick_best_candidate_reproduces_the_spec_example_8_5_0():
    a = Candidate(definition_kind="function", file="src/main.c", line=10)
    b = Candidate(definition_kind="function", file="src/parser.c", line=20)
    c = Candidate(definition_kind="variable", file="src/other.c", line=30)
    visible = frozenset({"src/main.c", "src/parser.c"})

    def scored(candidate: Candidate) -> int:
        return score_candidate(
            candidate, requested_kind="function", target_file="src/main.c", visible_files=visible
        )

    assert scored(a) == 8
    assert scored(b) == 5
    assert scored(c) == 0

    winner = pick_best_candidate(
        [a, b, c], requested_kind="function", target_file="src/main.c", visible_files=visible
    )
    assert winner is a


def test_pick_best_candidate_breaks_ties_by_file_then_line():
    tied_a = Candidate(definition_kind="function", file="src/b.c", line=5)
    tied_b = Candidate(definition_kind="function", file="src/a.c", line=99)

    winner = pick_best_candidate(
        [tied_a, tied_b],
        requested_kind="function",
        target_file="nowhere.c",
        visible_files=frozenset(),
    )

    assert winner is tied_b  # 점수는 3:3 동점 — "src/a.c" < "src/b.c"라 tied_b가 이긴다.


def test_pick_best_candidate_returns_none_for_an_empty_list():
    winner = pick_best_candidate(
        [], requested_kind="function", target_file="x.c", visible_files=frozenset()
    )
    assert winner is None
