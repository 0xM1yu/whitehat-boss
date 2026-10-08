"""v8 프롬프트가 **v7에서 선언한 만큼만 달라졌는지** 문다 (이슈 #419).

v8은 v7을 대체하지 않고 **더한** 판이다. 그 주장이 참인지는 사람이 두 파일을 눈으로 비교하는
수밖에 없었다 — 그래서 여기서 기계로 센다. 막으려는 명제는 둘이다.

1. **v7의 지시가 v8에서 조용히 빠지지 않는다.** v7 prefix의 줄 중 아래 `_REPLACED_IN_V8` 열셋
   줄만 v8에 없고 나머지는 **전부 그대로 있다**. `SOURCE` 서술(「Original-source input contract」의
   목록 줄)이 그 보호를 받는 자리다 — `tests/test_config.py`의 서빙 판 철자 검사가 정본 판을
   고르므로 v8이 그 서술을 흘리면 거기서도 빨개지지만, **어느 줄이 빠졌는지는 여기서만 안다**.
   ⚠ **이 검사는 v7이 움직일 때도 빨개진다** — 그것이 의도다. v7에 줄이 더해지면 v8에도 같이
   더해야 하고, 안 더하면 두 판이 갈린 채로
   「v8 = v7 + 도구 루프」라는 meta·registry 주석이 거짓이 된다(CLAUDE.md 2.3).
   ⏵ 2026-10-08 — 위 「거기서도 빨개진다」는 v8이 서빙 판이던 동안의 말이다. v9·v10을 거쳐 서빙 판은
   `config/models.yaml`이 고르고, 그 검사는 이제 v8을 안 본다. v8 서술이 움직이면 빨개지는
   것은 이 파일과 `tests/test_auditor_prompt_v9.py`(v9 = v8 + 한 줄의 바이트 동일성)다.

2. **v8이 「이 호출은 한 방이다」라고 말하지 않는다.** v7의 그 단언들은 루프 판에서 거짓이다 —
   모델이 읽는 명령이라 거짓 한 줄이 그대로 행동이 된다.

그리고 meta 선언·광고 목록·호스트 문구가 코드와 같은 말을 하는지도 여기서 묶는다.
"""

from pathlib import Path

import pytest

from src.config import load_config
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.auditor import AUDITOR_TOOLS
from src.schemas.enums import Role

V7 = PROMPTS_DIR / "auditor" / "v7"
V8 = PROMPTS_DIR / "auditor" / "v8"


def _prefix(directory: Path) -> str:
    return (directory / "prefix.md").read_text(encoding="utf-8")


# v8이 v7에서 **다시 쓴** 줄의 머리. 이 열셋만 v8에 없다 — 열둘은 「이 호출은 한 방이고 내는
# 것은 Finding 하나다」를 단언하던 자리이고, **하나**(`When the capsule omits …` — 넷째)는 한
# 방 판에서는 맞는 지시(「`reachability_unknown`을 쓴다」)가 홉 절의 「먼저 `lookup`」과 따로
# 놀아 단서를 더한 자리다(PR #491 리뷰 LOW-2). 머리로 거는 것은 줄이 길어서이고, 아래 검사가
# **일대일**을 요구하므로 느슨해지지 않는다.
_REPLACED_IN_V8: tuple[str, ...] = (
    "# Auditor Prompt — Current Harness-Compatible Finding Audit (v6)",
    "Your reasoning depth may be controlled by the serving configuration",
    "Use only symbols, statements, callers, facts, and identifiers present in the supplied input.",
    "When the capsule omits a caller, guard, source-to-sink connection, or other material needed",
    "- evidence_refs: only FACTS.evidence[*].evidence_id",
    "This call has no Scout-style retrieval loop.",
    "it does not fetch that material or guarantee another audit.",
    "explicitly supported by the input. Do not invent build options",
    "The current Auditor call directly requests the Finding Pydantic model.",
    "object with the following fields in this order:",
    "The Finding model rejects unknown fields.",
    "Emit exactly one JSON object. Do not emit Markdown",
    "`<think>` content, `reasoning_content`, or additional explanation.",
)


def test_v8_keeps_every_v7_line_except_the_thirteen_it_rewrites() -> None:
    """v7 prefix의 줄 중 **열세 줄만** v8에 없다 — 나머지는 바이트 그대로 살아 있다.

    오염: `prompts/auditor/v8/prefix.md`에서 아무 줄이나 지우면 빠진 줄이 열넷째로 잡혀 빨개진다.
    반대로 v7에 줄이 더해져도(= v8에 안 옮기면) 같은 자리에서 빨개진다 — **그쪽이 이 검사의
    주된 목적이다**(CLAUDE.md 2.3: 살아 있는 소스가 갈린 서술을 들지 않는다).

    ⏵ 실제로 그렇게 잡았다. PR #485가 v7의 `## Original-source input contract` 목록 줄에 enum
    절단 서술을 더했는데, 두 PR이 **각자 CI 초록**이라 순차 머지에서야 드러날 결함이었다 —
    4.1 합성에서 이 검사와 `test_config.py`의 서빙 판 철자 검사가 같이 빨개졌고, 그래서 그
    브랜치를 이쪽에 미리 머지하고 v8을 **그 v7에서 다시 생성**했다.
    """
    v7_lines = _prefix(V7).splitlines()
    v8_lines = set(_prefix(V8).splitlines())

    missing = [line for line in v7_lines if line not in v8_lines]

    assert len(missing) == len(_REPLACED_IN_V8), (
        "v7 줄이 v8에서 사라진 수가 선언과 다르다 — 선언한 열세 줄 밖이 빠졌거나 v7이 움직였다. "
        f"빠진 줄: {[line[:70] for line in missing]}"
    )
    for line, stem in zip(missing, _REPLACED_IN_V8, strict=True):
        assert line.startswith(stem), f"선언과 순서·내용이 다르다: {line[:80]!r} vs {stem!r}"


def test_v8_adds_the_recorded_number_of_lines_on_top_of_v7() -> None:
    """v8이 **더한** 줄 수도 센다 — 위 검사는 v7 줄만 묶으므로 v8이 더한 줄은 지워도 초록이었다.

    오염으로 확인했다: 홉 프로토콜 절의 `answer` 항목 한 줄을 지웠는데 위 검사가 안 잡았다.
    여기서 수를 박으면 v8 쪽 삭제·추가가 그대로 드러난다. 수가 움직이면 **의도한 변경인지 보고**
    이 수를 갱신한다(글자 수 핀과 같은 규율 — `test_the_v8_prefix_is_longer_than_v7...`).
    """
    v7_lines = set(_prefix(V7).splitlines())
    added = [line for line in _prefix(V8).splitlines() if line not in v7_lines]

    assert len(added) == 102, (
        f"v8이 더한 줄 수가 102에서 {len(added)}로 움직였다 — 의도한 변경이면 이 수를 갱신한다"
    )


def _section(text: str, title: str) -> list[str]:
    """`## title` 절의 줄 — 제목 줄부터 다음 `## ` 앞까지."""
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == title
        if inside:
            lines.append(line)
    return lines


_EVIDENCE_SECTION = "## Evidence body completeness"


def test_v8_carries_the_v7_evidence_body_section_byte_for_byte_and_only_appends() -> None:
    """`## Evidence body completeness` 절은 v7의 줄을 **바이트 그대로 앞에 두고** v8의 단락이
    **뒤에** 붙는다 — 절 전체가 v7과 같은 것이 아니고, 줄 순서가 바뀐 것도 아니다.

    `nodes/auditor._CONTENT_AWARE_VERSIONS`에 v8이 드는 근거 둘 중 하나가 이 절이다
    (`FACTS.evidence[].body`가 생성 요약이 아니라 툴 로그의 유계 발췌라고 적는다). 위의 줄 단위
    검사는 v7 줄이 v8에 **있는지**만 보므로 절 안의 두 줄을 맞바꿔도 초록이었다(오염으로
    확인 — 글자 수·줄 수도 그대로다). 이 검사가 그 자리(절의 앞 열여섯 줄이 v7과 같다)를
    묶는다. 뒤에 붙은 단락은 도구 관측(`## Tool observation`)의 같은 절단 서술이다.
    """
    v7 = _section(_prefix(V7), _EVIDENCE_SECTION)
    v8 = _section(_prefix(V8), _EVIDENCE_SECTION)

    assert v7, "전제 — v7에 그 절이 있다"
    assert v8[: len(v7)] == v7, "v8 절의 앞부분이 v7의 그 절과 바이트 단위로 갈렸다"
    appended = " ".join(v8[len(v7) :])
    assert "## Tool observation" in appended and "(middle shortened)" in appended, (
        "v8이 그 절 뒤에 붙이는 단락은 도구 관측의 절단 서술이다"
    )


def test_v8_evidence_refs_line_is_the_v7_rule_with_a_clause_appended() -> None:
    """`evidence_refs:` 줄은 v7의 줄이 **머리로 남고** 뒤에 `probe` 관측의 `evidence_id=` 인용
    구절이 붙은 **다시 쓴 줄**이다 — 「바이트 그대로」가 아니다(`_REPLACED_IN_V8`의 한 줄).

    `_CONTENT_AWARE_VERSIONS` 자격의 둘째 근거(`evidence_refs: only
    FACTS.evidence[*].evidence_id`)가 이 줄의 머리다. 위 줄 검사는 **v7 쪽** 줄만 묶으므로 v8의
    이 줄을 같은 글자 수로 다시 써도 초록이었다 — 여기서 v8 쪽을 묶는다."""
    (v7_line,) = [x for x in _prefix(V7).splitlines() if x.startswith("- evidence_refs: only")]
    (v8_line,) = [x for x in _prefix(V8).splitlines() if x.startswith("- evidence_refs: only")]

    assert v8_line.startswith(v7_line) and v8_line != v7_line
    assert "`evidence_id=`" in v8_line, "덧붙인 구절이 `probe` 관측의 인용을 가리키지 않는다"


def test_v8_tells_the_model_to_look_a_missing_capsule_fact_up_before_giving_up() -> None:
    """캡슐이 빠뜨린 사실에 대한 **두 문장이 같은 순서**를 말한다 — 먼저 `lookup`, 그다음에야
    `reachability_unknown`.

    v7에서 물려받은 문장(「use `reachability_unknown` … when the contract permits it」)이 홉 절의
    *「`lookup`이 맞는 다음 홉, `reachability_unknown`은 물었는데 못 받았을 때」*와 따로 놀았다 —
    같은 프롬프트의 두 지시가 달랐다(PR #491 리뷰 LOW-2). 한 방 판 v7은 되물을 길이 없으므로 그
    문장이 맞고 **v7은 그대로다**(그래서 이 문장은 `_REPLACED_IN_V8` 열셋 중 넷째 줄이다).

    ⚠ **철자·순서 핀이다 — 성질 핀이 아니다.** 단서 문구가 있는지와 `lookup`이
    `reachability_unknown` 앞인지만 묻는다. 뜻을 뒤집는 **같은 길이**의 재작성
    (「only when」→「even when」)은 못 잡는다 — 복사본에서 그렇게 바꿔 v8 테스트 18개가 전부
    통과하는 것을 확인했다(독립 검토 L-4). 단서를 지우거나 순서를 뒤집는 변경은 잡는다. 모델이
    그 문장을 따르는지는 이 검사의 몫이 아니고 **미측정**이다(v8로 돈 run 0건 — CLAUDE.md §2.2)."""
    omitted = "When the capsule omits a caller, guard, source-to-sink connection"
    (v7_line,) = [x for x in _prefix(V7).splitlines() if x.startswith(omitted)]
    (v8_line,) = [x for x in _prefix(V8).splitlines() if x.startswith(omitted)]
    qualifier = "no hop remains or the lookup came back empty"

    assert qualifier not in v7_line, "v7은 한 방 판이다 — 되물을 길이 없어 단서가 없는 것이 맞다"
    assert qualifier in v8_line, "v8의 이 문장이 홉 절과 다른 지시를 한다"
    assert "`lookup`" in v8_line and "`reachability_unknown`" in v8_line
    assert v8_line.index("`lookup`") < v8_line.index("`reachability_unknown`"), (
        "순서가 뒤집혔다 — 조회가 먼저다"
    )
    # 홉 절의 문장 — 이 문장과 같은 말을 하는 짝이다(지워지면 위 단서가 홀로 남는다).
    assert "`reachability_unknown` is for material you asked for and" in " ".join(
        _prefix(V8).split()
    )


# v8이 **말하지 않아야** 하는 것. 명제는 「이 호출은 한 방이고 루프가 없다」이고, 아래는 그
# 명제가 v7에서 취한 문형이다(문자열 하나가 아니라 형이다 — CLAUDE.md 2.5).
_SINGLE_SHOT_CLAIMS: tuple[str, ...] = (
    "no Scout-style retrieval loop",
    "directly requests the Finding Pydantic model",
    "Return only the final Finding JSON object",
    "only the Finding JSON object",
    "Emit exactly one JSON object. Do not emit",
    "(v6)",
)


@pytest.mark.parametrize("claim", _SINGLE_SHOT_CLAIMS)
def test_v8_never_tells_the_model_the_call_is_single_shot(claim: str) -> None:
    """루프 판이 「한 방」을 단언하면 모델이 그대로 믿는다 — 그 문형들이 v8에 없다.

    v7에는 **있어야 한다** — 그쪽은 실제로 한 방 판이다. 둘을 같이 거는 것은 이 검사가 「v8에서만
    없다」를 묻는다는 뜻이고, v7에서도 없으면 문형이 낡아 **아무것도 안 막는 핀**이 된다.
    """
    assert claim in _prefix(V7), f"v7에 없는 문형이면 이 핀은 아무것도 안 막는다: {claim!r}"
    assert claim not in _prefix(V8)


def test_v8_adds_the_hop_protocol_section_and_v7_does_not_have_it() -> None:
    """홉 프로토콜 절은 v8에만 있다."""
    assert "## Tool loop protocol" in _prefix(V8)
    assert "## Tool loop protocol" not in _prefix(V7)


def test_the_v8_meta_declares_the_loop_and_the_same_source_options_as_v7() -> None:
    """meta 선언 — 루프 스키마·도구 여섯·`source_options`는 v7과 **같은 일곱**.

    `source_options`가 v7과 같아야 하는 이유: v8의 `SOURCE` 서술이 v7과 바이트 동일이므로 선언이
    갈리면 `cli`의 `_undescribed_source_options`가 거짓으로 막거나 거짓으로 통과한다(#351).
    """
    v7_template = load_prompt(Role.AUDITOR, "v7")
    v8_template = load_prompt(Role.AUDITOR, "v8")

    assert v7_template.output_schema == "Finding"
    assert v8_template.output_schema == "AuditorAction"
    assert v8_template.tools == AUDITOR_TOOLS
    assert v8_template.source_options == v7_template.source_options
    # `input`은 `--slices` 없는 run을 막는 하드 게이트다(`src/cli.py`). 도구 루프와 무관하고 v8의
    # SOURCE 서술이 v7과 같으므로 v7과 같은 값(= 기본 `source`)을 쓴다.
    assert v8_template.input == v7_template.input == "source"


def test_the_v8_prompt_advertises_exactly_the_tools_the_schema_accepts() -> None:
    """광고 목록과 **본문 문장**이 같은 여섯을 든다 — meta만 맞으면 모델은 그 도구를 못 본다.

    프롬프트가 이름을 안 적으면 모델은 그 도구를 영영 안 쓴다(PoV v2와 gdb가 그 선례다). 그리고
    `index`는 **본문에도 없어야** 한다 — 스키마가 거절하는 이름을 광고하면 그 홉이 파싱 실패다.
    """
    prefix = _prefix(V8)

    for tool in AUDITOR_TOOLS:
        assert f"`{tool}`" in prefix, f"meta는 {tool}을 광고하는데 본문이 그 이름을 안 적는다"
    assert "`index`" not in prefix, (
        "`index`는 AuditorAction.tool이 안 받는다 — 코드 열람은 lookup이 담당한다"
    )


def test_the_v8_prompt_quotes_the_hosts_last_hop_notice_byte_for_byte() -> None:
    """마지막 홉 안내 문구를 프롬프트가 **그 바이트로** 인용한다.

    호스트는 마지막 허용 홉 앞에 `_LAST_HOP_NOTE`를 붙인다. 프롬프트가 「그런 줄이 온다」고 적어
    두어야 모델이 그것을 지시로 읽는다 — 문구가 갈리면 프롬프트의 안내가 오지 않는 줄을 가리킨다.
    """
    notice = "This is the last hop the host allows"

    assert notice in auditor._LAST_HOP_NOTE, "호스트 문구가 바뀌었다 — 프롬프트도 같이 고친다"
    assert notice in _prefix(V8)


def test_the_v8_prompt_states_the_lookup_request_cap_the_node_enforces() -> None:
    """「앞 3개만 처리한다」의 3이 노드 상수와 같은 수다 — 다르면 모델이 안 돌 요청을 더 낸다."""
    assert f"first {auditor._LOOKUP_REQUESTS_MAX} requests" in _prefix(V8)


def test_the_v8_prompt_and_the_repository_threshold_agree() -> None:
    """저장소에 커밋된 상한과 v8 선언이 `loop_problem`을 통과한다 — 실서빙 좌석 기준이다.

    여기서 `is_fake=False`로 묻는 것은 그 면제가 **Fake 좌석 전용**이기 때문이다
    (`auditor.loop_problem` docstring). 실서빙에서 통과해야 비로소 배선이 맞는 것이다.

    ⏵ 2026-10-08 — 고르는 판은 이름의 v8이 아니라 registry의 서빙 판이다(v9·v10을 거쳐 지금 판은
    `config/models.yaml`이 정본이다). 「v8 선언」은 이 검사가 들어온 날의 서빙 판이다.
    """
    config = load_config("harness_pool")
    template = load_prompt(Role.AUDITOR, config.model_for("auditor").prompt_template_version)

    assert config.thresholds.auditor_tool_hops > 0
    assert (
        auditor.loop_problem(template, config.thresholds.auditor_tool_hops, is_fake=False) is None
    )


def test_the_v8_prefix_is_longer_than_v7_and_the_delta_is_recorded() -> None:
    """글자 수를 **여기서** 센다 — meta 주석에 적어 둔 수가 수정마다 낡는 것을 피한다.

    이 수는 창 계산의 입력이다(`config/budgets.yaml`의 `auditor_tool_hops` 주석). 🔴 **창이 드는지는
    미실측이고** 이 검사는 그것을 묻지 않는다 — 수가 **움직였다는 사실**만 드러낸다. prefix를
    고치면 여기서 빨개지고, 그때 그 주석의 계산을 같이 다시 본다.
    """
    v7_chars, v8_chars = len(_prefix(V7)), len(_prefix(V8))

    assert (v7_chars, v8_chars) == (25_785, 33_209), (
        f"prefix 글자 수가 움직였다 — v7 {v7_chars} · v8 {v8_chars}. 창 계산"
        "(`config/budgets.yaml`의 auditor_tool_hops 주석)을 다시 보고 이 수를 갱신한다"
    )
    assert (V8 / "body.md").read_bytes() == (V7 / "body.md").read_bytes(), (
        "body는 v7과 바이트 동일이다 — 홉 1 이후 user 턴은 노드가 만들므로 갈릴 이유가 없다"
    )
