"""v9 프롬프트가 **v8에서 선언한 만큼만 달라졌는지** 문다 (`auditor_guards` 서술 · 2026-10-06).

v9는 v8에 `auditor_guards`(sink 앞 구문상 가드 목록, `GUARDS` 블록)의 입력 서술 **한 항목**만 더한
판이다. 막으려는 명제는 셋이다.

1. **v8의 지시가 v9에서 조용히 빠지지 않는다.** v9 prefix에서 그 한 항목을 빼면 v8 prefix와 **바이트
   동일**하고, body도 같다. ⚠ 이 검사는 v8이 움직일 때도 빨개진다 — 그것이 의도다(CLAUDE.md 2.3).
2. **그 한 항목이 러너가 실제로 내는 블록과 같은 말을 한다** — 머리줄·「없음」 줄·「더 있음」 줄·행
   상한이 `source_guards`의 상수와 같다. 서술이 코드와 갈리면 모델이 존재하지 않는 형식을 기대한다.
3. **켜진 옵션을 meta가 선언한다** — 선언이 빠지면 `cli`의 run·doctor가 막고 하네스가 아예 안 뜬다.
"""

from pathlib import Path

from src.cli import _undescribed_source_options
from src.config import load_config
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.orchestrator.source_guards import _MAX_ROWS, _MORE_ROWS, _NO_CONDITION, GUARDS_HEADER
from src.schemas.enums import Role

V8 = PROMPTS_DIR / "auditor" / "v8"
V9 = PROMPTS_DIR / "auditor" / "v9"

# v9가 v8에 더한 **한 항목**의 머리. 항목은 한 줄이라 줄 단위로 가른다.
_GUARDS_BULLET_HEAD = "- When the slice entry is a sink call and the anchor is a complete function"


def _read(directory: Path, name: str) -> str:
    return (directory / name).read_bytes().decode("utf-8")


def _guards_bullet() -> str:
    lines = [
        line
        for line in _read(V9, "prefix.md").split(chr(10))
        if line.startswith(_GUARDS_BULLET_HEAD)
    ]
    assert len(lines) == 1, "v9 prefix에 GUARDS 항목이 정확히 하나여야 한다"
    return lines[0]


def test_v9_is_v8_plus_exactly_the_guards_bullet() -> None:
    """항목 한 줄을 빼면 v8과 바이트 동일하다 - 그 밖의 어떤 줄도 더해지거나 빠지지 않았다."""
    bullet = _guards_bullet()
    without = _read(V9, "prefix.md").replace(bullet + chr(10), "", 1)

    assert without == _read(V8, "prefix.md")
    assert _read(V9, "body.md") == _read(V8, "body.md")


def test_the_guards_bullet_sits_right_after_the_anchor_blocks_and_before_other_functions() -> None:
    """러너는 GUARDS를 entry 함수 **바로 뒤**, 다른 함수보다 앞에 붙인다(`runner._auditor_source`) -
    서술도 그 순서의 자리에 있다."""
    lines = _read(V9, "prefix.md").split(chr(10))
    at = lines.index(_guards_bullet())

    assert lines[at - 1].startswith("- If no enclosing symbol is available")
    assert lines[at + 1].startswith("- It then finds the narrowest indexed symbol containing")


def test_the_guards_bullet_quotes_what_the_runner_emits() -> None:
    bullet = _guards_bullet()

    assert f"`{GUARDS_HEADER}`" in bullet
    assert _NO_CONDITION.replace("{names}", "<names>") in bullet
    assert _MORE_ROWS.replace("{count}", "N") in bullet
    assert f"At most {_MAX_ROWS} rows" in bullet


def test_the_v9_meta_declares_the_v8_options_plus_auditor_guards() -> None:
    v8, v9 = load_prompt(Role.AUDITOR, "v8"), load_prompt(Role.AUDITOR, "v9")

    assert v9.source_options == (*v8.source_options, "auditor_guards")
    assert (v9.output_schema, v9.tools, v9.input) == (v8.output_schema, v8.tools, v8.input)


def test_the_served_config_turns_guards_on_and_its_prompt_declares_it() -> None:
    """운영 좌석이 가드 목록을 켜고 그 판이 선언한다 - 하나만 되면 하네스가 안 뜬다.

    음성 통제: 같은 설정에서 좌석을 v8로 되돌리면 같은 검사가 그 옵션을 잡는다(v8은 선언하지 않는다)
    - 이 검사가 항상 빈 목록을 내는 공허한 검사가 아니라는 증거다."""
    config = load_config("harness_pool")
    assert config.thresholds.auditor_guards == 1
    assert _undescribed_source_options(config) == []

    entry = config.models["qwen3.8-27b-sft1-1"].transition(prompt_template_version="v8")
    on_v8 = config.transition(models={**config.models, "qwen3.8-27b-sft1-1": entry})
    # ⏵ 2026-10-14 - `auditor_write_window_marker`·`auditor_input_context`는 v16부터 서술하므로 v8을
    # 고르면 그것들도 짚힌다.
    assert _undescribed_source_options(on_v8) == [
        "auditor_guards(auditor/v8)",
        "auditor_write_window_marker(auditor/v8)",
        "auditor_input_context(auditor/v8)",
        # ⏵ 2026-10-17 - v19부터 서술하는 둘(#702)도 짚힌다.
        "auditor_desc_first(auditor/v8)",
        "auditor_omitted_functions(auditor/v8)",
    ]


def test_the_guards_bullet_keeps_each_claim_the_scan_makes() -> None:
    """문장마다 한 구절씩 문다 - **철자 pin이지 성질이 아니다**(구절이 있어도 뜻이 뒤집힌 문장은 못
    잡는다). 그래도 항목에서 구절째 빠지거나 철자가 바뀌면 여기서 빨개진다. 독립 리뷰가 항목 문장을
    고쳐도 초록이던 변이를 걸었고, 코드가 하는 일 중 서술이 빠뜨리거나 좁게 쓴 곳(삼항 · 탈출문 ·
    우선순위)을 채우면서 그 구절을 핀으로 둔다."""
    bullet = _guards_bullet()

    for phrase in (
        "harness-generated block (not source code)",
        "`[alias: lhs←rhs]`",
        "a ternary assigned to a sink argument",
        "`[exit: <that statement>]`",
        "not inside a nested block, an inner `if`, a loop, or an `else`",
        "directly are kept before alias rows",
        "no argument identifier can be found",
        "the file's line numbers cannot be aligned",
        "It is never omitted for size",
        "not as evidence that a range or null check protects the sink",
    ):
        assert phrase in bullet, phrase
