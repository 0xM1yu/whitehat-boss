"""v10 프롬프트의 산문이 **코드가 쥔 값·문구와 같은 말을 하는지** 문다 (Auditor 서빙 판 ·
2026-10-06).

⏵ 2026-10-10 — **서빙 판은 v11이다**(이슈 #525). ⏵ 같은 날 — **v13이 됐다**(이슈 #67 — v11 +
FACTS에서 `source` 사본을 뺀다. 아래 「서빙 판」은 config에서 읽으므로 v13을 문다). v10 파일은
동결이라 그것만 읽는 핀은 다시 빨개질 수
없다(적대적 리뷰 H1). 그래서 아래 검사는 `_V10_ONLY` 넷을 빼고 **v10과 config의 서빙 판** 둘에서
돈다(`pytest_generate_tests` · `tests/test_pov_prompt_v4.py`의 `VERSION` = config와 같은 장치). v10
전용 넷(봉투 이름 · `cwe` null 금지 · 글자 수 둘)의 서빙 판 쪽은 `tests/test_nullable_cwe.py`가
문다.

v8·v9는 「앞 판과 바이트 동일」을 세는 핀 파일(`test_auditor_prompt_v8.py` · `_v9.py`)이 있고, 그
핀의 절반은 노드 상수·러너 문구와 산문을 묶는 것이다. **그 둘은 자기 판 파일만 읽는다** — v10은
v7에서 전면 재작성한 판이라 바이트 동일성 핀이 성립하지 않고, 산문을 코드에서 베낀
곳(`_LAST_HOP_NOTE` 인용 · `first 3 requests` · GUARDS 줄 · 관측 머리줄)은 **어느 검사도 v10을 안
읽어서** 한 줄만 고쳐도 전량이 초록이었다. 막으려는 명제는 하나다 — **v10 산문이 코드와 다른 값을
단언하지 않는다.**

⚠ **기대값을 산문에서 베끼지 않는다.** 산문이 움직여도(다른 작업이 산문을 고친다) 핀이 같이 낡지
않게 기대값은 코드 상수·리터럴, 또는 **코드를 실제로 돌려 얻은 출력**(`scan_function` ·
`_probe_observation` · `evidence_summaries`)에서 뽑고, 산문에서는 「그 값을 말하는 자리」만 읽는다.

⚠ **이 파일의 한계 — 철자 핀이지 성질 핀이 아니다.** 산문이 인용하는 문구·수·필드 목록이 코드와
같은지를 묻는다. 모델이 그 산문을 따르는지는 이 파일의 몫이 아니고 미측정이다. 키워드 정규식(GUARDS
사실 셋)은 뜻이 뒤집힌 같은 길이의 재작성(「kept before」→「kept after」)을 못 잡는다.

겹치지 않는 것 —
`test_config.py::test_the_canonical_auditor_prompt_states_the_runner_source_contract`가 SOURCE 조립
수치·근거 본문 철자를, `test_auditor_procedure_schema.py`가 출력 계약의 값 집합·필드 순서를,
`test_nodes.py`·`test_config.py`·`test_auditor_loop.py`가 `_CONTENT_AWARE_VERSIONS`의 v10 포함을
이미 묶는다."""

import inspect
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

from src.config import CONFIG_DIR, load_config
from src.orchestrator import prompt_loader
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, PromptError, load_prompt
from src.orchestrator.source_guards import (
    _MAX_ROWS,
    _MORE_ROWS,
    _NO_CONDITION,
    GUARDS_HEADER,
    scan_function,
)
from src.schemas import AuditRefutation, ProducedBy
from src.schemas.auditor import (
    _FIELDS_BY_ACTION,
    _REQUIRED_BY_ACTION,
    AUDITOR_TOOLS,
    AuditorProcedureAction,
    AuditProcedure,
)
from src.schemas.enums import AuditorStopReason, EvidenceKind, ReproOrigin, Role
from src.schemas.evidence import Evidence
from src.schemas.need_evidence import Resolution
from src.schemas.pov import PovSegment, PovSpec
from src.schemas.scout_response import RequestedContext

SRC_DIR = Path(auditor.__file__).resolve().parents[2]


def _served_auditor_version() -> str:
    """서빙 프로필 둘의 Auditor 좌석이 **실제로 고르는** 판 — 살아 있는 config에서 읽는다
    (`tests/test_pov_loop.py`의 `_served_pov_version`과 같은 장치). 판 이름을 박으면 판이 올라갈 때
    핀이 낡은 판을 계속 문다 — ⏵ 2026-10-09 이 파일이 정확히 그랬다(서빙 판 v11 · 이슈 #525 ·
    적대적 리뷰 H1: v10 파일은 동결이라 그 판만 읽는 핀은 다시 빨개질 수 없다)."""
    versions = {
        load_config(profile).model_for(Role.AUDITOR.value).prompt_template_version
        for profile in ("harness_pool", "harness_pool_hybrid")
    }
    assert len(versions) == 1, f"서빙 프로필의 Auditor 좌석 판이 갈린다: {sorted(versions)}"
    return versions.pop()


# 이 파일의 핀이 읽는 판 — v10(동결 기록)과 **서빙 판**(config). 같으면 하나다.
# 아래 `_V10_ONLY`에 없는 검사는 전부 두 판에서 돈다(`pytest_generate_tests`). 서빙 판이 v10과
# 다른 문장을 의도로 든 검사(출력 봉투 이름 · `cwe` null 규칙 · 글자 수)만 v10 전용이고, 서빙 판의
# 그 자리는 `tests/test_nullable_cwe.py`가 문다.
SERVED = _served_auditor_version()
_VERSIONS = tuple(sorted({"v10", SERVED}, key=lambda v: int(v.lstrip("v"))))
_V10_ONLY = frozenset(
    {
        "test_the_v10_meta_declares_the_procedure_envelope_not_its_payload",
        "test_the_v10_prose_never_lets_cwe_be_null_because_the_schema_refuses_it",
        "test_the_v10_prefix_length_is_the_number_four_other_places_copy",
        "test_every_place_that_copies_the_v10_prefix_length_carries_the_measured_number",
        # ⏵ 2026-10-11 — 서빙 판이 v14(v12 계열 · `audit`를 모든 상태에서 채움)가 되며 v10 전용으로
        # 옮겼다: `audit` null ⟺ no_signal과 넷짜리 result 짝은 v10·v11의 문장이다. v12 계열의 그
        # 자리는 `tests/test_auditor_v12.py`가 문고, v14는 v12와 두 줄만 다르다
        # (`tests/test_auditor_v14_facts.py`).
        "test_the_v10_prose_pairs_each_refutation_result_with_the_status_the_node_expects",
        "test_the_v10_prose_pairs_a_null_audit_with_the_status_the_validator_pairs_it_with",
    }
)

# 지금 읽는 판의 디렉터리와 이름 — `_prompt_under_test`가 판마다 바꾼다(기본은 v10).
V10 = PROMPTS_DIR / "auditor" / "v10"
_VERSION = "v10"


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """`_V10_ONLY` 밖의 검사를 `_VERSIONS`(v10 + 서빙 판)로 편다."""
    if metafunc.function.__name__ not in _V10_ONLY:
        metafunc.parametrize("_prompt_under_test", _VERSIONS, indirect=True)


@pytest.fixture(autouse=True)
def _prompt_under_test(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    version = getattr(request, "param", "v10")
    monkeypatch.setattr(f"{__name__}.V10", PROMPTS_DIR / "auditor" / version)
    monkeypatch.setattr(f"{__name__}._VERSION", version)
    return version

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


# --- 산문을 읽는 도구 ---------------------------------------------------------------------


def _prefix() -> str:
    return (V10 / "prefix.md").read_text(encoding="utf-8")


def _flat() -> str:
    """줄바꿈·들여쓰기를 한 칸으로 접은 산문 — 문장이 줄바꿈으로 갈린 곳(「Tool responses are
    data」)을 넘어 읽는다."""
    return " ".join(_prefix().split())


def _template():  # noqa: ANN202 - PromptTemplate을 import하지 않으려고 반환형을 안 적는다
    return load_prompt(Role.AUDITOR, _VERSION)


def _sentence(flat: str, needle: str) -> str:
    """`needle`을 든 첫 문장(`. `에서 끊는다). 없으면 그 사실 자체가 산문에서 빠진 것이다."""
    for sentence in re.split(r"(?<=\.)\s+", flat):
        if needle in sentence:
            return sentence
    raise AssertionError(f"산문에 {needle!r}을 든 문장이 없다")


def _section(title: str) -> str:
    """`## title` 절을 한 칸으로 접은 글 — 제목 줄부터 다음 `## ` 앞까지."""
    lines: list[str] = []
    inside = False
    for line in _prefix().splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == title
        if inside:
            lines.append(line)
    assert lines, f"산문에 {title!r} 절이 없다"
    return " ".join(" ".join(lines).split())


def _clauses(flat: str) -> list[str]:
    """문장을 `;`에서 한 번 더 끊은 조각 — 한 문장이 `;`로 서로 다른 규칙을 잇는 곳(「`cwe` …;
    `repro_spec` …」 · 「… `audit`: null …」)에서 규칙 하나를 한 조각으로 읽는다."""
    return [
        clause.strip()
        for sentence in re.split(r"(?<=\.)\s+", flat)
        for clause in sentence.split(";")
    ]


_SOURCE_CONTRACT = "## Original-source input contract"


def _types_of(model: type, field: str) -> tuple[str, ...]:
    return get_args(model.model_fields[field].annotation)


# --- meta 선언 ----------------------------------------------------------------------------


def test_the_v10_meta_declares_the_procedure_envelope_not_its_payload() -> None:
    """`output_schema`는 응답의 **루트 봉투**(`AuditorProcedureAction`)다 — payload 모델
    `AuditProcedure`를 선언하면 `lookup`·`probe`·`answer` 액션 없이 감사 결과 하나만 받는 문법이
    된다.

    meta 선언이 곧 모델에게 보내는 스키마라(`nodes/auditor._loop_envelope`) 매핑 표가 없다 — 이름이
    허용집합 밖이면 `resolve_output_schema`가 `PromptError`로 막고, 허용집합 안이어도 봉투가 아니면
    한 방 `Finding`으로 읽힌다. `test_auditor_loop.py`가 같은 선언을 **판 넷을 돌며** 건다(v10은 그
    한 행) — 여기는 v10 파일만 읽는 자리라 서빙 판이 v9로 되돌아가도 v10의 선언이 안 풀리지 않는다.
    """
    template = _template()
    allowed = {schema.__name__ for schema in auditor.ALLOWED_OUTPUT_SCHEMAS}

    assert template.output_schema == AuditorProcedureAction.__name__
    assert template.output_schema in allowed
    assert AuditProcedure.__name__ not in allowed
    assert auditor.resolve_output_schema(template.output_schema) is AuditorProcedureAction
    assert auditor._loop_envelope(template) is AuditorProcedureAction
    with pytest.raises(PromptError):
        auditor.resolve_output_schema(AuditProcedure.__name__)


def test_the_v10_meta_advertises_exactly_the_tools_the_schema_accepts() -> None:
    """meta가 광고하는 도구 = 봉투의 `tool` Literal(`AUDITOR_TOOLS`) — 스키마가 거절하는
    이름(`index`)을 광고하면 모델이 그것을 고른 홉이 파싱 실패이고, 하나를 빼먹으면 모델이 그 도구를
    영영 안 쓴다(PoV v2 ↔ gdb가 그 선례다). 그리고 서빙 설정의 홉 상한으로 `loop_problem`이 v10을
    통과시킨다 — 이 판은 루프 판이라 상한 0이면 안 선다. ⏵ 2026-10-10 — 이 검사는 서빙 판에서도
    돈다(모듈 머리의 ⏵)."""
    tools = _template().tools

    assert len(tools) == len(set(tools)), "meta tools에 중복이 있다"
    assert set(tools) == set(AUDITOR_TOOLS)
    hops = load_config("harness_pool").thresholds.auditor_tool_hops
    assert hops > 0
    assert auditor.loop_problem(_template(), hops, is_fake=False) is None


# --- 도구 · 홉 · 요청 ---------------------------------------------------------------------


def _prose_tool_lists() -> dict[str, list[str]]:
    """산문이 도구 이름을 나열하는 두 자리 — 봉투 예시의 `probe` 줄과 「Tool use」의 `probe`
    항목."""
    prefix = _prefix()
    envelope = re.search(r'"action": "probe", "tool": "([^"]+)"', prefix)
    assert envelope, "산문에 `probe` 봉투 예시 줄이 없다"
    bullets = [x for x in prefix.splitlines() if x.startswith("- `probe` runs one diagnostic:")]
    assert len(bullets) == 1, "산문에 `probe` 도구 항목이 정확히 하나여야 한다"
    listing = bullets[0].split(":", 1)[1].split(".", 1)[0]
    return {
        "envelope example": envelope.group(1).split("|"),
        "Tool use bullet": re.findall(r"`([a-z-]+)`", listing),
    }


def test_the_v10_prose_lists_the_six_tools_the_schema_accepts_everywhere_it_lists_them() -> None:
    """산문이 도구를 나열하는 **두 자리**가 각각 `AUDITOR_TOOLS`와 같다 — 한 자리만 맞춰서는 모델이
    두 말을 다 읽는다. `index`는 본문 어디에도 없어야 한다(스키마가 안 받는 이름이다 — 코드 열람은
    `lookup`이 담당한다).

    ⚠ 철자 핀이다 — 산문이 도구를 **세 번째 자리**에서 나열하면 그 자리는 못 본다."""
    for place, names in _prose_tool_lists().items():
        assert names, f"{place}에서 읽은 목록이 비었다 — 이 검사가 아무것도 안 묻는 상태다"
        assert sorted(names) == sorted(AUDITOR_TOOLS), place
    prefix = _prefix()
    for tool in AUDITOR_TOOLS:
        assert f"`{tool}`" in prefix, f"산문이 {tool}을 백틱 이름으로 적지 않는다"
    assert "`index`" not in prefix, "`index`는 봉투의 tool이 안 받는다 — 광고하면 홉이 파싱 실패다"


def test_the_v10_prose_quotes_the_hosts_last_hop_notice_and_reconciles_its_tail() -> None:
    """마지막 홉 안내를 산문이 **호스트가 붙이는 글자**로 인용한다 — 산문이 「이런 줄이 온다」고
    적어 두어야 모델이 그것을 지시로 읽고, 인용이 갈리면 산문의 안내가 오지 않는 줄을 가리킨다.

    산문에서 읽는 것은 인용의 **머리**(「beginning `…`」)와 **꼬리**(`ends by asking for …`)이고,
    대조 대상은 `_LAST_HOP_NOTE`다. ⚠ 그 상수의 꼬리는 *「final Finding」*인데 v10의 결과 이름은
    `procedure`다 — 그 차이를 산문이 **같은 문장에서** 풀어 주지 않으면(`procedure`를 안 부르면)
    모델이 마지막 홉에 `Finding`을 내려 한다. 상수가 `Finding`을 부르는 한 이 조건이 건다(상수 쪽이
    바뀌면 조건이 풀린다)."""
    flat = _flat()
    note = auditor._LAST_HOP_NOTE.strip()
    head = re.search(r"beginning `([^`]+)`", flat)
    tail = re.search(r"ends by asking for ([^;.]+)", flat)
    assert head and tail, "산문이 마지막 홉 안내의 머리·꼬리를 인용하지 않는다"

    assert note.startswith(head.group(1)), "산문이 인용한 머리가 호스트 문구와 갈렸다"
    assert note.rstrip(".").endswith(tail.group(1)), "산문이 말하는 꼬리가 호스트 문구와 갈렸다"
    if "Finding" in note:
        quoting = _sentence(flat, head.group(1))
        payload = f"`{AuditorProcedureAction._PAYLOAD_FIELD}`"
        assert payload in quoting, (
            f"호스트 안내가 `Finding`을 부르는데 그 문장이 {payload}과의 관계를 안 말한다"
        )


def test_the_v10_prose_states_the_lookup_request_cap_the_node_enforces() -> None:
    """「first N requests」의 N이 노드 상수(`_LOOKUP_REQUESTS_MAX`)와 같은 수다 — 다르면 모델이 안
    돌 요청을 더 낸다. 그리고 산문이 약속하는 「응답이 몇 개를 버렸는지 말한다」가 사실이다 — 넘친
    요청이 있으면 관측의 상태 줄에 그 수가 실린다(`_lookup_observation`).

    ⚠ `_LOOKUP_REQUESTS_MAX == symbol_resolver._MAX_REQUESTS`는 `test_auditor_loop.py`가 문다 —
    여기서 복제하지 않는다."""
    flat = _flat()
    caps = re.findall(r"first (\d+) requests", flat)
    assert caps, "산문에 요청 상한(`first N requests`)이 없다"
    assert set(caps) == {str(auditor._LOOKUP_REQUESTS_MAX)}

    assert re.search(r"(?i)how many[^.;]*(?:dropped|ignored|skipped)", flat), (
        "산문이 「응답이 버린 수를 말한다」를 안 적는다"
    )
    empty = Resolution(followup_text="", blocks=(), unfilled=(), already=(), truncated=())
    shown = auditor._lookup_observation(2, 5, 0, empty, ignored=2)
    assert "ignored=2" in shown and f"first {auditor._LOOKUP_REQUESTS_MAX} requests" in shown


def _spec() -> PovSpec:
    return PovSpec(segments=[PovSegment(kind="repeat", byte=0x41, count=8)], rationale="padding")


def _lookup(pairs: list[tuple[str, str]], **fields: str) -> AuditorProcedureAction:
    values = {"purpose": "need the size", "request_code": "SIZE", "rationale": "look it up"}
    values.update(fields)
    return AuditorProcedureAction(
        action="lookup",
        requests=[
            RequestedContext(
                kind=kind,
                target=target,
                purpose=values["purpose"],
                request_code=values["request_code"],
            )
            for kind, target in pairs
        ],
        rationale=values["rationale"],
    )


def _probe(tool: str = "asan", **fields: object) -> AuditorProcedureAction:
    return AuditorProcedureAction(
        action="probe", tool=tool, rationale=str(fields.pop("rationale", "run it")), **fields
    )


_KINDS = ("function", "type", "macro", "variable", "caller")
_PAIRS = [(_KINDS[i % len(_KINDS)], f"name_{i}") for i in range(auditor._LOOKUP_REQUESTS_MAX)]


def test_the_v10_repeat_rule_lists_exactly_the_fields_the_request_key_ignores() -> None:
    """산문이 「무엇을 바꿔도 다른 요청이 아니다」라고 드는 필드 = `_request_key`가 **실제로
    무시하는** 필드다. 산문에서 목록을 읽어 필드마다 **변이를 걸어** 키가 그대로인지 보고, 코드
    쪽에서 센 무시되는 필드(요청 객체의 `kind`·`target` 밖 + 봉투의 `spec`·`rationale`)와도 같은지
    본다.

    왜 둘 다 보나. 산문 목록만 변이로 보면 「코드는 무시하는데 산문이 안 적은」 필드(예: 요청 객체에
    필드가 하나 늘었다)가 안 드러나고, 코드 쪽만 세면 산문이 **무시 안 하는** 필드를 무시한다고
    적어도 안 드러난다. 모델이 그 문장을 믿고 `purpose`만 바꿔 되묻는 것이 반복
    판정(`REPEAT_REQUEST`)이다. 그 밖에 산문이 말하는 세 가지 — 요청 **순서**·「호스트가 처리하는
    요청 가운데」(앞 N개)·`probe`는 **도구 이름**만 — 도 같이 건다. 음성 통제로
    `kind`·`target`·`tool`이 키를 바꾼다는 것도 본다(키가 상수면 위 변이는 전부 초록이다)."""
    sentence = _sentence(_flat(), "does not make a tool call different")
    prose_fields = set(re.findall(r"`(\w+)`", sentence))
    base_lookup, base_probe = _lookup(_PAIRS), _probe()
    ignored_by_code = (set(RequestedContext.model_fields) - {"kind", "target"}) | (
        set(AuditorProcedureAction.model_fields) - {"action", "requests", "tool", "procedure"}
    )
    mutated: dict[str, tuple[AuditorProcedureAction, AuditorProcedureAction]] = {
        "purpose": (base_lookup, _lookup(_PAIRS, purpose="another reason")),
        "request_code": (base_lookup, _lookup(_PAIRS, request_code="OTHER")),
        "rationale": (base_lookup, _lookup(_PAIRS, rationale="another rationale")),
        "spec": (base_probe, _probe(spec=_spec())),
    }

    assert prose_fields == ignored_by_code, (
        f"산문이 드는 필드 {sorted(prose_fields)}가 코드가 무시하는 필드 "
        f"{sorted(ignored_by_code)}와 다르다"
    )
    assert set(mutated) == prose_fields, "이 검사에 변이가 없는 필드가 있다 — 변이를 더한다"
    for name, (before, after) in mutated.items():
        assert auditor._request_key(before) == auditor._request_key(after), name
    assert auditor._request_key(_probe(rationale="again")) == auditor._request_key(base_probe)

    key = auditor._request_key
    assert key(_lookup(list(reversed(_PAIRS)))) == key(base_lookup), "요청 순서가 키를 바꾼다"
    assert key(_lookup([*_PAIRS, ("guard", "extra")])) == key(base_lookup), (
        f"앞 {auditor._LOOKUP_REQUESTS_MAX}개 밖의 요청이 키를 바꾼다"
    )
    assert key(_lookup([_PAIRS[0], _PAIRS[0]])) == key(_lookup([_PAIRS[0]])), "집합이 아니다"
    # 음성 통제 — 키가 상수가 아니다.
    assert key(_lookup([(_PAIRS[0][0], "other"), *_PAIRS[1:]])) != key(base_lookup)
    assert key(_lookup([("other_kind", _PAIRS[0][1]), *_PAIRS[1:]])) != key(base_lookup)
    assert key(_probe("gdb")) != key(base_probe)
    assert key(base_probe) != key(base_lookup)


def _key_roles() -> dict[str, set[str]]:
    """`_request_key`를 **돌려서** 어떤 필드가 키를 바꾸는지 가른다 — 산문이 아니라 코드가 정한
    것이다. 필드마다 값을 바꾼 사본의 키를 원본과 비교한다(`model_copy`는 검증을 안 거친다 — 키가
    보는 것만 묻는다).

    요청 객체(`RequestedContext`)의 필드는 `lookup` 키로, 봉투의 조사 필드(`action`·`requests`·
    `answer`의 payload 말고)는 `probe` 키로 잰다. 새 필드가 생기면 `samples`에 값이 없어 KeyError로
    **시끄럽게** 깨진다 — 그 필드를 키가 보는지 안 보는지 여기서 정하라는 신호다."""
    key = auditor._request_key
    lookup, probe = _lookup(_PAIRS), _probe()
    used = set()
    for name in RequestedContext.model_fields:
        first = lookup.requests[0].model_copy(update={name: f"changed-{name}"})
        changed = lookup.model_copy(update={"requests": [first, *lookup.requests[1:]]})
        if key(changed) != key(lookup):
            used.add(name)
    samples = {"tool": "gdb", "spec": _spec(), "rationale": "changed"}
    fields = set(AuditorProcedureAction.model_fields) - {
        "action",
        "requests",
        AuditorProcedureAction._PAYLOAD_FIELD,
    }
    used_by_probe = {
        n for n in fields if key(probe.model_copy(update={n: samples[n]})) != key(probe)
    }
    return {
        "request used": used,
        "request ignored": set(RequestedContext.model_fields) - used,
        "probe used": used_by_probe,
        "probe ignored": fields - used_by_probe,
    }


def test_the_v10_repeat_rule_states_both_what_the_request_key_uses_and_what_it_ignores() -> None:
    """반복 판정의 **두 문장 다** 코드가 센 것과 같다 — 「무엇이 같으면 반복인가」(앞문장)와
    「무엇을 바꿔도 다른 요청이 아니다」(뒷문장). 기대는 `_request_key`를 필드마다 돌려 얻는다
    (`_key_roles`).

    **사고 형태 —** 독립 검토의 변이 실험: 위 검사는 뒷문장만 읽어서 **앞문장의 정의에 `purpose`를
    끼워 넣어도**(「same set of `kind`, `target`, and `purpose`」) 전량이 초록이었다. 모델이 그
    문장을 믿으면 `purpose`를 바꾼 되묻기가 반복이 아니라고 읽는데 호스트는 반복으로 끝낸다
    (`REPEAT_REQUEST`).

    산문에서 읽는 것: 앞문장 — `lookup`의 「set of … pairs」 속 백틱 필드가 키가 보는 요청 필드와
    같고(`kind`·`target`), 그 짝이 「호스트가 처리하는 요청」의 것이며(앞 N개 — 키의 앞 N개 한정은
    위 검사가 코드로 건다), `probe`는 키가 보는 조사 필드(`tool`) 하나이고 안 보는 필드를 안
    부른다. 뒷문장 — 백틱 필드가 키가 안 보는 필드 전부이고 「요청 순서」를 든다. ⚠ 철자 핀이다."""
    roles = _key_roles()
    assert all(roles.values()), f"전제 — 키가 보는 필드와 안 보는 필드가 다 있다: {roles}"
    flat = _flat()
    defining = _sentence(flat, "means the same set of")
    ignoring = _sentence(flat, "does not make a tool call different")
    lookup_part, probe_part = defining.split(";", 1)

    pairs = set(re.findall(r"`(\w+)`", lookup_part.split("same set of", 1)[1]))
    assert pairs == roles["request used"], f"앞문장이 `lookup`의 정의로 {sorted(pairs)}를 든다"
    assert "the requests the host processes" in lookup_part, "「호스트가 처리하는 요청」이 없다"
    for name in sorted(roles["probe used"]):
        assert f"the same {name}" in probe_part, f"`probe`의 정의에 「the same {name}」이 없다"
    for name in sorted(roles["probe ignored"]):
        assert not re.search(rf"\b{name}\b", probe_part), f"`probe` 정의가 `{name}`을 부른다"
    assert set(re.findall(r"`(\w+)`", probe_part)) == {"probe"}

    listed = set(re.findall(r"`(\w+)`", ignoring))
    assert listed == roles["request ignored"] | roles["probe ignored"], sorted(listed)
    key = auditor._request_key
    assert key(_lookup(list(reversed(_PAIRS)))) == key(_lookup(_PAIRS)), "전제 — 순서는 안 본다"
    assert "the order of the requests" in ignoring, "뒷문장이 「요청 순서」를 안 든다"


def _hop_count_claims(text: str) -> list[str]:
    """`hop`·`tool call` 곁(앞뒤 네 토큰)에 숫자가 든 자리 — 「N hops」 · 「at most N tool calls」 ·
    「hop limit is N」 같은 홉 수 단언. ⚠ **숫자(digit)만 본다** — 이 산문은 셋 넘는 수를 숫자로
    적는다 (`At most 12 rows` · `first 3 requests` · `20,000-token`). 「sixteen hops」 같은 영어
    단어 수는 못 잡는다."""
    tokens = re.findall(r"[A-Za-z]+|\d+(?:,\d+)*", text)
    found: list[str] = []
    for at, token in enumerate(tokens):
        word = token.lower()
        is_hop = word in {"hop", "hops"} or (
            word in {"call", "calls"} and at > 0 and tokens[at - 1].lower() == "tool"
        )
        if is_hop and any(near[0].isdigit() for near in tokens[max(0, at - 4) : at + 5]):
            found.append(" ".join(tokens[max(0, at - 4) : at + 5]))
    return found


@pytest.mark.parametrize(
    "claim",
    [
        "You may make up to 16 hops.",
        "At most 16 hops are allowed.",
        "Use at most 16 tool calls.",
        "The hop limit is 16.",
        "Hops are limited to 16 per audit.",
    ],
)
def test_the_hop_count_detector_catches_the_forms_a_count_claim_would_take(claim: str) -> None:
    """위 검출기가 **아무것도 안 잡는 검출기가 아니다**(CLAUDE.md 2.5 — 핀이 없으면 CI는 영영
    초록이다).

    ⚠ 양성 샘플의 출처: 저장소에는 **홉 수를 산문에 적은 곳이 없다**(`git grep`으로 확인했다 —
    `prompts/`와 `src/`에 「N hops」 꼴이 0건). 그래서 이 문형들은 **이 산문이 다른 수를 적는 방식**
    (`At most 12 rows` · `first 3 requests`, `prefix.md`의 Original-source input contract와 Tool
    use)을 홉에 옮긴 것이다 — 실제로 나타날 수 있는 문형이라는 근거는 그것뿐이다."""
    assert _hop_count_claims(claim), claim


def test_the_v10_prose_states_no_hop_count() -> None:
    """산문이 **홉 수를 적지 않는다** — 상한은 `thresholds.auditor_tool_hops`(프로필 공통 설정)가
    정하고 산문이 숫자를 들면 설정과 갈리는 순간 모델이 거짓 상한을 믿는다. 산문은 상한을 *「마지막
    허용 홉」과 호스트가 보내는 안내*로만 말하고(위 마지막 홉 검사), 그 안내가 오는 시점은 코드가
    정한다.

    음성 핀 — 지금 산문에서 이 검출기가 잡는 것은 0건이다(`it is one hop`처럼 digit이 없는 문장과
    `first 3 requests`는 hop·tool call 곁이 아니다). 산문이 홉 수를 적기로 하면 **여기가 아니라
    `thresholds`와 대조하는 핀으로 바꾼다.**"""
    assert _hop_count_claims(_flat()) == []


def test_the_v10_prose_counts_the_ways_a_hop_loop_ends_without_an_answer() -> None:
    """「Two things end the audit with no audit result」의 둘이 `AuditorStopReason`에서 `answered`를
    뺀 수와 같고 번호 항목도 그 수다 — 사유가 하나 늘면(예: `nothing_resolved`) 산문이 거짓이 된다.

    ⚠ 열거형 독스트링이 「셋이다」를 적는 이유와 같은 규율이다 — 그 수가 움직이면 두 군데가 같이
    움직인다."""
    prefix = _prefix()
    word = re.search(r"(\w+) things end the audit", prefix)
    assert word, "산문에 「N things end the audit」이 없다"
    without_finding = set(AuditorStopReason) - {AuditorStopReason.ANSWERED}

    assert _NUMBER_WORDS[word.group(1).lower()] == len(without_finding)
    lines = prefix.splitlines()
    after = next(i for i, line in enumerate(lines) if "things end the audit" in line) + 1
    items = []
    for line in lines[after:]:
        if re.match(r"\d+\. ", line):
            items.append(line)
        elif line.strip() and items:
            break
    assert len(items) == len(without_finding)


# --- 출력 계약 ----------------------------------------------------------------------------


def test_the_v10_prose_pairs_each_refutation_result_with_the_status_the_node_expects() -> None:
    """「`refutation.result` ↔ trigger_status」의 짝이 노드의 `_STATUS_BY_RESULT`와 같다 — 그 표는
    스키마가 안 싣는 규칙이라(`AuditProcedure`의 검증기는 `audit is None ⟺ no_signal` 하나) 어긴
    출력이 그대로 통과하고, 노드가 **어긴 비율을 세는 기준**이 이 표다(`status_result_mismatch`).
    산문과 표가 갈리면 그 비율이 산문이 시킨 것과 다른 규칙으로 센다.

    그리고 표의 키가 스키마의 `result` Literal과 같다 — 값이 하나 늘었는데 표에 없으면
    `_answer_observed`가 그 답에서 `KeyError`로 죽는다. 값은 `trigger_status` Literal 안이다."""
    (line,) = [x for x in _prefix().splitlines() if "↔ trigger_status:" in x]
    pairs = dict(re.findall(r"(\w+)→(\w+)", line))

    assert pairs, "산문에서 읽은 짝이 비었다"
    # ⏵ 2026-10-10 — 노드의 표는 v12(이슈 #578)의 `no_candidate→no_signal`을 더 든다. v10 산문과
    # 스키마가 대는 것은 그 밖의 넷이다(v12의 짝은 `tests/test_auditor_v12.py`가 문다).
    v10_map = {k: v for k, v in auditor._STATUS_BY_RESULT.items() if k != "no_candidate"}
    assert pairs == v10_map
    assert set(v10_map) == set(_types_of(AuditRefutation, "result"))
    statuses = set(_types_of(AuditProcedure, "trigger_status"))
    assert set(auditor._STATUS_BY_RESULT.values()) <= statuses


def test_the_v10_prose_names_the_statuses_whose_path_must_not_be_empty() -> None:
    """「`audit.path` may be `[]` for X and is non-empty for A/B/C」가 노드의
    `_PATH_REQUIRED_STATUSES`와 같다 — 산문이 경로를 비워도 되는 상태와 노드가
    `path_without_endpoints`로 세는 상태가 갈리면 합법적인 빈 경로가 위반으로 세어지거나 그 반대다.
    빈 경로가 허용되는 상태는 (전체 − 경로 필요 − `no_signal`)이다 — `no_signal`은 `audit`가
    null이라 경로 자체가 없다."""
    flat = _flat()
    required = re.search(r"non-empty for ([a-z_/]+)", flat)
    empty_ok = re.search(r"may be `\[\]` for (\w+)", flat)
    assert required and empty_ok, "산문이 경로의 비움 규칙을 안 적는다"
    statuses = set(_types_of(AuditProcedure, "trigger_status"))

    assert set(required.group(1).split("/")) == set(auditor._PATH_REQUIRED_STATUSES)
    assert {empty_ok.group(1)} == statuses - set(auditor._PATH_REQUIRED_STATUSES) - {"no_signal"}


def test_the_v10_prose_envelope_examples_use_the_fields_each_action_uses() -> None:
    """산문의 봉투 예시 세 줄이 든 키 = 스키마가 그 액션에 허용하는 필드(+ `action` ·
    `rationale`)다. 필드 규율이 `_check_action`이라 액션에 안 맞는 필드를 실으면 그 홉이 파싱 실패다
    — 예시가 틀리면 모델이 그대로 따라 쓴다. `probe`의 `spec`은 예시에 없는 것이 **맞다**(「Do not
    send `spec`」 — 도구가 그 값을 안 받는다고 필드 설명이 적는다)."""
    lines = {
        match.group(1): re.findall(r'"(\w+)":', line)
        for line in _prefix().splitlines()
        if (match := re.match(r'\s*\{"action": "(lookup|probe|answer)"', line))
    }
    payload = AuditorProcedureAction._PAYLOAD_FIELD
    shown = {
        "lookup": set(_FIELDS_BY_ACTION["lookup"]),
        "probe": set(_FIELDS_BY_ACTION["probe"]) - {"spec"},
        "answer": {payload},
    }

    assert set(lines) == set(shown), "봉투 예시 세 줄이 다 있어야 한다"
    for action, keys in lines.items():
        assert set(keys) == {"action", "rationale", *shown[action]}, action
    assert "spec" in _FIELDS_BY_ACTION["probe"]
    assert "Do not send `spec`" in _flat()


def test_the_v10_prose_request_object_has_the_fields_of_requested_context() -> None:
    """「each request object has four string fields: …」의 목록·수가 `RequestedContext`의 필드와
    같고 전부 `str`이다 — 필드가 하나 늘면 모델은 그것 없이 요청을 내 파싱 실패이고, 하나 줄면 안
    쓰이는 필드를 채운다. 순서도 같다(스키마의 property 순서가 문법이다)."""
    sentence = _sentence(_flat(), "string fields")
    names = re.findall(r"`(\w+)`", sentence.split("string fields", 1)[1])
    count = re.search(r"has (\w+) string fields", sentence)

    assert count and names
    assert names == list(RequestedContext.model_fields)
    assert _NUMBER_WORDS[count.group(1).lower()] == len(RequestedContext.model_fields)
    assert {field.annotation for field in RequestedContext.model_fields.values()} == {str}


# 최소 유효 `audit` 블록 — 아래 핀이 `AuditProcedure` 검증기를 **돌려** 거절·수락을 가를 때 쓴다.
# 값은 어느 것이든 스키마가 받는 값이면 된다(기대값이 아니라 입력이다).
_AUDIT_BLOCK = {
    "category": "spatial_bounds",
    "point": {"symbol": "f", "expression": "memcpy(d, s, n)"},
    "origin": {"kind": "param", "fact": "n is a parameter"},
    "path": [{"step": "sink", "symbol": "f", "note": "copies n bytes"}],
    "defense": {"expression": "none on visible path", "status": "absent", "gap": None, "fact": "-"},
    "refutation": {"result": "defeated", "witness": "n=65", "missing_information": []},
}


def _procedure_payload(status: str, **override: object) -> dict[str, object]:
    """`trigger_status`에 맞는 `audit`(no_signal이면 null)를 든 최소 `AuditProcedure` payload."""
    base: dict[str, object] = {
        "audit": None if status == "no_signal" else _AUDIT_BLOCK,
        "trigger_status": status,
        "confidence": 0.5,
        "cwe": "CWE-787",
        "title": "t",
        "rca_draft": "r",
        "evidence_refs": [],
        "repro_spec": None,
    }
    return {**base, **override}


def _statuses_accepted_with(**override: object) -> set[str]:
    """`override`를 얹은 payload를 **검증기가 실제로 받는** `trigger_status`들."""
    accepted = set()
    for status in _types_of(AuditProcedure, "trigger_status"):
        try:
            AuditProcedure.model_validate(_procedure_payload(status, **override))
        except ValidationError:
            continue
        accepted.add(status)
    return accepted


def test_the_v10_prose_pairs_a_null_audit_with_the_status_the_validator_pairs_it_with() -> None:
    """「`audit` is null exactly when no_signal」가 말하는 상태 집합 = **검증기가 null `audit`를
    받는 상태 집합**이다. 기대는 `AuditProcedure`를 상태마다 돌려 얻는다(산문에서 베끼지 않는다).

    **사고 형태 —** 독립 검토의 변이 실험: 그 문장에 「or reachability_unknown」을 붙여도 전량이
    초록이었다. 그 문장은 검증기와 정면으로 어긋난다(`reachability_unknown`에 null `audit`를 내면
    검증기가 그 답을 통째로 거절해 재생성한다). 동치의 **양쪽**을 코드가 집행하므로 둘 다 본다 —
    null이 되는 상태가 `no_signal` 하나이고 블록이 있어야 하는 상태가 나머지 전부다.

    산문에서 읽는 자리는 둘이다 — 「4. Keep fields consistent」의 동치 문장과 출력 계약의 「For X,
    output `"audit": null`」. ⚠ 철자 핀이다 — 「exactly when」을 빼거나 상태를 끼워 넣으면 빨개지고,
    같은 뜻의 다른 문형은 못 잡는다."""
    everything = set(_types_of(AuditProcedure, "trigger_status"))
    null_ok = _statuses_accepted_with(audit=None)
    block_ok = _statuses_accepted_with(audit=_AUDIT_BLOCK)
    assert null_ok and block_ok, "전제 — 검증기가 양쪽을 다 받는 상태가 있다"
    assert not null_ok & block_ok and null_ok | block_ok == everything, "전제 — 동치를 집행한다"

    flat = _flat()
    equivalence = re.search(r"`audit` is null exactly when ([^;]+);", flat)
    exit_rule = re.search(r'For ([^,]+), output `"audit": null`', flat)
    assert equivalence, "산문이 「`audit` is null exactly when …」를 안 적는다 — 동치가 아니다"
    assert exit_rule, '산문이 「For …, output `"audit": null`」를 안 적는다'
    for place, found in (("동치 문장", equivalence), ("출력 계약", exit_rule)):
        named = {status for status in everything if re.search(rf"\b{status}\b", found.group(1))}
        assert named == null_ok, f"{place}이 null `audit`의 상태를 {sorted(named)}로 적는다"


def _cwe_null_clauses(flat: str) -> list[str]:
    """`cwe`를 **백틱으로** 말하는 조각 가운데 **null을 부정 없이** 드는 것 — 산문이 `cwe`를 null로
    둘 수 있다고 말하는 자리다. (백틱을 요구하는 것은 계약 예시 JSON의 `"cwe": "CWE-<number>"`가
    같은 조각에 `"repro_spec": null`을 들고 있어서다 — 그것은 `cwe`의 null이 아니다. 계약 예시의
    `cwe` 줄은 아래 검사가 따로 본다.) 「never null」·「non-null」·「not null」은 부정이라
    세지 않는다.

    ⚠ `null`·`nullable`만 본다 — 「omit」「optional」 같은 다른 표현은 이 검출기가 못 잡는다(그
    문형은 저장소에 없어 핀을 못 달았다)."""
    return [
        clause
        for clause in _clauses(flat)
        if re.search(r'`cwe`|`"cwe"|`CWE-<number>`', clause)
        and any(
            not re.search(r"(?:never|not|non-)\s?$", clause[: found.start()])
            for found in re.finditer(r"\bnull(?:able)?\b", clause)
        )
    ]


@pytest.mark.parametrize(
    "clause",
    [
        # 양성 — 이 파일이 막는 사고 그 자체다. 출처: 작업 트리가 고치기 전 HEAD의
        # `prompts/auditor/v10/prefix.md`의 211 · 278 · 282줄(`git show HEAD:<경로>`).
        'whether or not the Scout suggested it (no_signal → `"cwe": null`)',
        'For no_signal, output `"audit": null` and `"cwe": null`',
        "`cwe` matches `^CWE-\\d+$` or is null when trigger_status is no_signal.",
    ],
)
def test_the_cwe_null_detector_catches_the_sentences_that_once_said_it(clause: str) -> None:
    """위 검출기가 **아무것도 안 잡는 검출기가 아니다**(CLAUDE.md 2.5)."""
    assert _cwe_null_clauses(clause), clause


@pytest.mark.parametrize(
    "clause",
    [
        # 음성 — `cwe`를 null이 아니라고 말하는 문형. 출처: `prompts/auditor/v2/prefix.md`의
        # 57줄(굵게 `**non-null**`)·107줄(`remains non-null`)과 v10 「Exact output contract」의
        # `cwe` stays … non-null one — 이 저장소가 같은 사실을 이미 이렇게 적었다. 마지막은 null을
        # 들되 `cwe`를 말하지 않는 조각(`repro_spec`) — v10 같은 절에 실제로 있다.
        "The current harness requires `cwe` to be **non-null**.",
        "`cwe` remains non-null in the current experiment.",
        "`cwe` stays a `CWE-<number>` string, because the harness requires a non-null one",
        "`repro_spec` is either null or an object with non-empty `steps` and `expected_signal`",
    ],
)
def test_the_cwe_null_detector_lets_the_statements_that_cwe_is_never_null_through(
    clause: str,
) -> None:
    assert _cwe_null_clauses(clause) == [], clause


def test_the_v10_prose_never_lets_cwe_be_null_because_the_schema_refuses_it() -> None:
    """스키마는 `cwe: null`을 **어떤 상태에서도** 거절한다 — 그래서 산문이 null을 허용하는 문장을
    들면 모델이 이 스키마가 거절하는 출력을 낸다(`AuditProcedure.cwe`의 주석이 바로 그 사고를
    경고한다). 거절은 상태마다 `AuditProcedure`를 돌려 확인하고, 그 위에서 산문이 셋을 말한다.

    ① 패턴을 스키마의 것으로 인용하고 「it is never null」을 적는다 ② 어느 조각도 `cwe`를 null로
    두게 하지 않는다(`_cwe_null_clauses`) ③ 계약 예시의 `"cwe"` 줄이 null을 안 든다.

    **사고 형태 —** 독립 검토의 변이 실험: 「`cwe` never null」을 「no_signal이면 null 가능」으로
    바꿔도 전량이 초록이었다."""
    pattern = AuditProcedure.model_json_schema()["properties"]["cwe"]["pattern"]
    for status in _types_of(AuditProcedure, "trigger_status"):
        AuditProcedure.model_validate(_procedure_payload(status))  # 통제 — cwe만 다르다
        with pytest.raises(ValidationError):
            AuditProcedure.model_validate(_procedure_payload(status, cwe=None))

    flat = _flat()
    stated = re.search(r"`cwe` always matches `([^`]+)`; it is never null", flat)
    assert stated, "산문이 「`cwe` always matches `…`; it is never null」를 안 적는다"
    assert stated.group(1) == pattern, "산문이 인용한 패턴이 스키마의 것과 다르다"
    offenders = _cwe_null_clauses(flat)
    assert offenders == [], f"산문이 `cwe`를 null로 둘 수 있다고 말한다: {offenders}"
    skeleton = re.search(r'"cwe": ([^,\n]+)', _prefix())
    assert skeleton and "null" not in skeleton.group(1), "계약 예시의 `cwe` 줄이 null을 든다"


def _minimal_action(action: str) -> dict[str, object]:
    """액션이 **필수로 드는 필드만** 채운 봉투 — 필수 필드의 이름은 스키마가 정한다."""
    payload = AuditorProcedureAction._PAYLOAD_FIELD
    needed = {**_REQUIRED_BY_ACTION, "answer": payload}[action]
    values: dict[str, object] = {
        "requests": _lookup(_PAIRS[:1]).requests,
        "tool": AUDITOR_TOOLS[0],
        payload: _procedure_payload("triggerable"),
    }
    return {"action": action, needed: values[needed]}


def test_the_v10_prose_says_every_field_the_envelope_requires_on_all_actions_is_required() -> None:
    """봉투 스키마의 `required`에서 **액션 말고 필수인 필드**를 뽑아 산문이 그 각각을 「required on
    all <액션 수>」로 적는지 본다. 그리고 그 말이 사실이다 — 액션마다 그 필드를 빼면 거절된다.

    **사고 형태 —** 독립 검토의 변이 실험: 「`rationale` is required」를 「optional」로 바꿔도
    전량이 초록이었다. 모델이 그 문장을 믿고 `rationale`을 빼면 그 홉이 파싱 실패다.

    ⚠ 철자 핀이다 — 「required on all three」의 세 낱말과 수를 묻는다."""
    actions = get_args(AuditorProcedureAction.model_fields["action"].annotation)
    common = set(AuditorProcedureAction.model_json_schema()["required"]) - {"action"}
    assert common, "전제 — 모든 액션이 필수로 드는 필드가 있다"

    flat = _flat()
    for name in sorted(common):
        for action in actions:
            body = {**_minimal_action(action), **{n: "stated" for n in common}}
            AuditorProcedureAction.model_validate(body)  # 통제 — 다 실으면 받는다
            with pytest.raises(ValidationError):
                AuditorProcedureAction.model_validate({k: v for k, v in body.items() if k != name})
        said = re.search(rf"`{name}` is required on all (\w+)", flat)
        assert said, f"산문이 「`{name}` is required on all …」를 안 적는다"
        assert _NUMBER_WORDS[said.group(1).lower()] == len(actions), "수가 액션 수와 다르다"


# --- GUARDS ------------------------------------------------------------------------------


def _scan(lines: list[str], sink: str = "memcpy"):  # noqa: ANN202
    """C 조각을 `scan_function`에 넣는다 — sink는 `sink(`이 처음 든 줄이다. 산문이 말하는 사실을
    **코드를 실제로 돌려** 확인하는 자리다(산문의 기대값을 베끼지 않는다)."""
    sink_line = next(i for i, line in enumerate(lines, 1) if f"{sink}(" in line)
    scan = scan_function(
        "\n".join(lines) + "\n", file="a.c", first_line=1, sink_line=sink_line, sink_name=sink
    )
    assert scan is not None, "전제 — 조각에서 sink를 찾는다"
    return scan


def test_the_v10_guards_description_quotes_what_the_runner_emits() -> None:
    """GUARDS 서술이 러너가 실제로 쓰는 글자를 인용한다 — 머리줄 · 「없음」 줄(`<names>` 자리) ·
    「더 있음」 줄 (`N` 자리) · 행 상한. 서술이 코드와 갈리면 모델이 존재하지 않는 형식을 기대한다.

    기대값은 전부 `source_guards`의 상수에서 뽑는다 — **v9의 같은 검사
    (`test_the_guards_bullet_quotes_what_the_runner_emits`)는 v9 파일만 읽는다**."""
    section = _section(_SOURCE_CONTRACT)

    assert f"`{GUARDS_HEADER}`" in section
    head, tail = _NO_CONDITION.split("{names}")
    assert re.search(re.escape(head) + r"<[^<>]+>" + re.escape(tail), section), (
        "「없음」 줄의 인용이 `<…>` 자리를 둔 코드 문구와 다르다"
    )
    head, tail = _MORE_ROWS.split("{count}")
    assert re.search(re.escape(head) + r"\w+" + re.escape(tail), section)
    caps = re.findall(r"(?i)at most (\d+) rows", section)
    assert caps and set(caps) == {str(_MAX_ROWS)}


def test_the_v10_guards_description_names_the_row_tags_and_the_top_level_exit_rule() -> None:
    """행 꼬리표 `[alias: …]`·`[exit: …]`와 「탈출은 몸통의 최상위 문장일 때만」이 코드의 동작이다.

    코드를 돌려 얻는다 — 별칭으로만 이어진 행에 `[alias: m←n]`, `if`의 몸통에서 곧바로 탈출하는 행에
    `[exit: return]`이 붙고, **안쪽 `if` 몸통의 탈출은 바깥 행에 안 붙는다.** 산문은 두 꼬리표의
    머리와 그 최상위 한정을 적어야 한다(`top-level`)."""
    scan = _scan(
        [
            "void f(char *dst, char *src, int n) {",
            "    int m = n + 1;",
            "    if (m > 8) { }",
            "    if (n > 8) { return; }",
            "    if (n > 4) { if (n > 5) { return; } }",
            "    memcpy(dst, src, n);",
            "}",
        ]
    )
    tags = {tag for row in scan.rows for tag in re.findall(r"\[(\w+):", row)}
    (alias_row,) = [row for row in scan.rows if "if (m > 8)" in row]
    (outer_row,) = [row for row in scan.rows if "if (n > 4)" in row]
    section = _section(_SOURCE_CONTRACT)

    assert tags == {"alias", "exit"}
    assert alias_row.endswith("[alias: m←n]")
    assert any(row.endswith("[exit: return]") for row in scan.rows)
    assert "[exit:" not in outer_row, "전제 — 안쪽 `if`의 탈출은 바깥 행의 것이 아니다"
    for tag in sorted(tags):
        assert f"[{tag}:" in section, f"산문이 `[{tag}: …]` 꼬리표를 안 적는다"
    assert re.search(r"(?i)top[- ]level", section), "산문이 탈출을 몸통의 최상위로 한정하지 않는다"


def test_the_v10_guards_description_says_a_ternary_assigned_to_a_sink_argument_is_listed() -> None:
    """삼항 대입 행 — `n = flag ? 8 : 16;`처럼 **sink 인자에 대입되는 삼항**은 그 조건(`flag`)이
    인자를 말하지 않아도 행으로 싣는다(좌변 `n`이 인자라서다). v9 서술은 이것을 적었고 **v10은 구성
    목록에 「ternary」만 남겼다** — 독립 검토가 찾은 줄어든 사실의 하나다.

    코드를 돌려 확인한다 — ① 행이 실제로 생기고 별칭 꼬리표 없이 직접 행이다 ② **방향의 반대편
    통제**: 같은 모양의 삼항이어도 **sink 인자가 아닌 변수**(`k = flag ? 8 : 16;`)에 대입되면 조건이
    인자를 안 말하는 한 행이 안 난다. 그래서 「조건이 인자를 말하지 않아도 싣는다」가 **인자에
    대입될 때만**의 규칙이다(`_ternaries`가 대입 왼쪽을 sink 이름과 맞춰 본다).

    산문은 그 조각(`;`로 끊은 한 구절)에서 셋을 적어야 한다 — 「ternary assigned to a sink
    argument」·「is listed even when `…` does not mention」(싣는 쪽 — **방향**)·「assignment
    target」(까닭). 독립 검토의 변이 실험: 이 문장을 「조건이 인자를 말할 때**만** 싣는다」로
    뒤집어도 예전 검사(「ternary」와 「assign」이 가까이 있는가)는 초록이었다.
    ⚠ 철자 핀이다 — 뒤집는 한 단어(`only`·`not`)에는 빨개지지만 같은 뜻의 다른 문형은 못 잡는다."""
    scan = _scan(
        [
            "void f(int flag, char *dst, char *src) {",
            "    int n;",
            "    n = flag ? 8 : 16;",
            "    memcpy(dst, src, n);",
            "}",
        ]
    )
    elsewhere = _scan(
        [
            "void f(int flag, char *dst, char *src, int n) {",
            "    int k;",
            "    k = flag ? 8 : 16;",
            "    memcpy(dst, src, n);",
            "}",
        ]
    )

    assert len(scan.rows) == 1, f"삼항 대입 행이 안 생겼다: {scan.rows}"
    row = scan.rows[0]
    assert "flag ? ... : ..." in row and "[alias:" not in row
    assert elsewhere.rows == (), f"인자가 아닌 변수에 대입된 삼항이 행을 냈다: {elsewhere.rows}"
    (clause,) = [x for x in _clauses(_section(_SOURCE_CONTRACT)) if "ternary assigned" in x]
    assert "ternary assigned to a sink argument" in clause, (
        "산문이 대입 대상을 sink 인자로 한정 안 한다"
    )
    assert re.search(
        r"\bis listed even (?:when|if) `?\w+`? (?:does not|doesn't) mention\b", clause
    ), "산문이 「조건이 인자를 안 말해도 싣는다」를 안 적는다 — 코드는 그 행을 싣는다"
    assert "assignment target" in clause, "산문이 그 행을 싣는 까닭(대입 왼쪽)을 안 적는다"


def test_the_v10_guards_description_says_direct_rows_outlast_alias_rows_at_the_cap() -> None:
    """행 상한을 넘으면 **직접 이름 행이 별칭 행보다 먼저 남는다** — 별칭 조건이 상한을 채워 인자
    자신의 가드를 밀어내지 않게 하는 규칙이다(`_pick`). v10 서술은 「최대 12행 · 소스 순서 · 뺀
    수」만 적고 **무엇이 먼저 남는지**를 안 적는다 — 독립 검토가 찾은 줄어든 사실의 하나다.

    코드를 돌려 확인한다 — 별칭 행 하나가 맨 앞에 있고 직접 행이 열둘이면 보이는 열둘은 전부 직접
    행이고 **빠지는 하나가 그 별칭 행**이다. 산문은 「직접」이 「별칭」보다 먼저(`before`·`ahead
    of`·`over`) 남는다고 적어야 한다(키워드 정규식)."""
    scan = _scan(
        [
            "void f(char *dst, char *src, int n) {",
            "    int m = n + 1;",
            "    if (m > 3) { }",
            *(f"    if (n > {k}) {{ }}" for k in range(1, _MAX_ROWS + 1)),
            "    memcpy(dst, src, n);",
            "}",
        ]
    )

    assert len(scan.rows) == _MAX_ROWS and len(scan.dropped) == 1
    assert all("[alias:" not in row for row in scan.rows)
    assert "[alias:" in scan.dropped[0]
    assert re.search(
        r"(?is)\bdirect(?:ly)?\b[^;]{0,120}\b(?:before|ahead of|over|first)\b[^;]{0,60}\balias"
        r"|\balias\b[^;]{0,80}\b(?:after|last|dropped first|cut first)\b",
        _section(_SOURCE_CONTRACT),
    ), "산문이 상한을 넘을 때 직접 행이 별칭 행보다 먼저 남는다고 적지 않는다"


def test_the_v10_guards_description_says_the_no_condition_line_lists_the_names_checked() -> None:
    """「없음」 줄은 **맞춰 본 이름**을 싣는다 — 맨 「none found」는 거짓이었다(독립 검토가 실제
    sink 82개를 훑어 「없음」 12개 중 3개가 틀린 것을 찾았다). 산문이 그 줄을 `<names>` 자리로
    인용하는 것은 위 인용 검사가 묻고, 여기는 **그 자리가 무엇인지**(맞춰 본 이름)를 산문이
    말하는지를 묻는다.

    코드를 돌려 확인한다 — 행이 없는 조각의 블록이 sink 인자 이름 전부를 싣는다."""
    scan = _scan(
        [
            "void f(char *dst, char *src, int n) {",
            "    memcpy(dst, src, n);",
            "}",
        ]
    )

    assert scan.rows == ()
    assert all(name in scan.block for name in ("dst", "src", "n"))
    assert re.search(
        r"(?i)\bnames\b[^.;]{0,40}\b(?:checked|matched|scanned|compared)\b"
        r"|\b(?:checked|matched|scanned|compared)\b[^.;]{0,40}\bnames\b",
        _section(_SOURCE_CONTRACT),
    ), "산문이 「없음」 줄의 이름이 맞춰 본 이름이라고 적지 않는다"


def _scan_line(lines: list[str], marker: str, name: str | None):  # noqa: ANN202
    """`marker`가 처음 든 줄을 sink 줄로 삼아 `scan_function`에 넣는다 — 연산자 sink나 매크로로
    감싼 호출처럼 `이름(` 꼴이 아닌 줄을 겨누는 자리다(`_scan`은 `이름(`이 든 줄만 찾는다)."""
    line = next(i for i, text in enumerate(lines, 1) if marker in text)
    scan = scan_function(
        "\n".join(lines) + "\n", file="a.c", first_line=1, sink_line=line, sink_name=name
    )
    assert scan is not None, "전제 — 조각에서 sink를 찾는다"
    return scan


def test_the_v10_guards_description_states_the_sink_shapes_and_the_cuts_the_scan_applies() -> None:
    """GUARDS 서술이 코드보다 **좁지 않다** — 블록이 붙는 sink의 모양과 행에 걸리는 절단 셋.

    **사고 형태 —** 독립 검토가 `scan_function`을 돌려 확인했다. 서술은 블록이 「sink 호출」에만
    붙는다고 적었는데 코드는 연산자 sink(`a[i] = x`)와 매크로로 감싼 호출에도 붙인다(`_sink`의 줄
    단위 대체 경로). 그리고 서술에 없던 것 셋 — 행 문구 절단 · `[alias: …]`의 대입 상한 · sink를
    품은 조건은 행이 아니다 — 이 있었다(`[exit: …]` 문구의 절단은 검토가 못 짚은 넷째다).
    모델이 읽는 서술이 코드보다 좁으면 모델은 붙는 블록을 못 믿거나 잘린 행을 온전한 조건으로
    읽는다.

    기대는 **코드를 실제로 돌려** 얻는다 — 수(절단 길이 · 대입 상한)는 돌려서 센 값이고 산문의 수와
    같아야 한다(`_ROW_CHARS`·`_EXIT_CHARS`·`_ALIAS_SHOWN` 상수를 산문에서 베끼지 않고 코드를 센다).
    방향의 반대편 통제도 건다: 이름이 안 겹치면 행이 안 나고, 대입이 상한 이하면 `, ...`이 안 붙고,
    sink를 **본문에** 품은 `if`는 행이다(조건식 안에 품은 것과 갈린다).

    ⚠ 철자 핀이다 — 산문이 같은 사실을 다른 문형으로 적으면(`contains` → `includes`) 빨개진다. 그
    경우 이 검사의 문구를 새 문형에 맞춘다(수와 동작은 코드에서 오므로 안 바뀐다)."""
    section = _section(_SOURCE_CONTRACT)
    head = "void f(int *a, int i, int x) {"
    to_a = "<operator>.assignment"

    operator = _scan_line(
        [head, "    if (i > 8) { return; }", "    a[i] = x;", "}"], "a[i] = x", to_a
    )
    wrapped = _scan_line(
        ["void f(char *d, char *s) {", "    if (!d) { return; }", "    STRCPY(d, s);", "}"],
        "STRCPY(",
        "strcpy",
    )
    at_head = _scan_line([head, "    if (i > 8) a[i] = x;", "}"], "a[i] = x", to_a)
    mid_line = _scan_line([head, "    else if (i > 8) a[i] = x;", "}"], "a[i] = x", to_a)
    for found in (operator, wrapped):
        assert found.skipped is None and found.rows, "호출이 없는 sink에도 블록이 붙는다"
        assert found.block.startswith(GUARDS_HEADER)
    assert len(at_head.rows) == 1 and mid_line.rows == (), "줄 머리의 조건만 sink 앞으로 센다"
    for phrase in (
        "When the entry is a sink (a call, a call wrapped in a macro, or an operator such as "
        "`a[i] = x`)",
        "or a call a macro wraps",
        "every identifier on that line, and of the conditions on that line only one that starts it",
        "The block is absent when the entry is not a sink (or no identifier can be found for it)",
    ):
        assert phrase in section, f"산문에 {phrase!r}가 없다"

    inside = _scan_line(
        ["void f(char *d, char *s, int n) {", "    if (n < 8 && copy(d, s, n)) { }", "}"],
        "copy(",
        "copy",
    )
    in_body = _scan_line(
        ["void f(char *d, char *s, int n) {", "    if (n < 8) { copy(d, s, n); }", "}"],
        "copy(",
        "copy",
    )
    assert inside.rows == () and len(in_body.rows) == 1, "전제 — 조건식 안은 행이 아니고 본문은 행"
    assert "A condition that contains the sink call itself is not a row" in section

    def row_for(condition: str, body: str = "{ }") -> str:
        scan = _scan(
            [
                "void f(char *dst, char *src, int n) {",
                *(f"    int a{k} = n + {k};" for k in range(1, 6)),
                f"    if ({condition}) {body}",
                "    memcpy(dst, src, n);",
                "}",
            ]
        )
        assert len(scan.rows) == 1, scan.rows
        return scan.rows[0]

    wide = " && ".join(f"n > {k}" for k in range(1, 60))
    text = row_for(wide).split(" | ", 1)[1]
    statement = row_for("n > 8", "{ return very_long_error_identifier_" + "x" * 80 + "; }")
    exit_text = statement.split("[exit: ", 1)[1].rsplit("]", 1)[0]
    cut = re.search(r"cut to (\d+) characters", section)
    exit_cut = re.search(r"statement to (\d+),", section)
    assert text.endswith("...") and exit_text.endswith("...")
    assert cut and int(cut.group(1)) == len(text), "산문의 행 절단 길이가 코드가 센 값과 다르다"
    assert exit_cut and int(exit_cut.group(1)) == len(exit_text), "탈출문 절단 길이가 다르다"
    assert "each ending in `...`" in section

    def alias_tag(names: list[str]) -> str:
        condition = " && ".join(f"{name} > 0" for name in names)
        return re.search(r"\[alias: ([^\]]*)\]", row_for(condition)).group(1)

    over, exact = alias_tag(["a1", "a2", "a3", "a4", "a5"]), alias_tag(["a1", "a2", "a3"])
    cap = re.search(r"for at most (\d+) assignments and then `, \.\.\.`", section)
    assert cap, "산문이 `[alias: …]`의 대입 상한과 그 뒤 `, ...`을 안 적는다"
    assert over.count("←") == int(cap.group(1)) and over.endswith(", ...")
    assert exact.count("←") == int(cap.group(1)) and "..." not in exact, "상한 이하는 안 붙는다"


# --- 관측 형식 문자열 ----------------------------------------------------------------------


def _evidence(source: str = "asan") -> Evidence:
    return Evidence(
        evidence_id="ev-v10",
        run_id="run-v10",
        kind=EvidenceKind.STATIC if source == "semgrep" else EvidenceKind.SANITIZER,
        source=source,
        artifact_id="art-v10",
        payload_ref="logs/v10.log",
        digest="sha256:" + "0" * 64,
        observed_at=datetime.now(UTC),
    )


def _probe_lines(payload: str | None, source: str = "asan") -> list[str]:
    """`probe` 관측(`_probe_observation`)을 실제로 만들어 줄로 돌려준다."""
    ctx = SimpleNamespace(read_payload=lambda _ref: payload)
    return auditor._probe_observation(ctx, 1, _evidence(source), spec_ignored=False).splitlines()


def _backticked(flat: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", flat))


def test_the_v10_prose_quotes_the_clip_marker_the_host_writes() -> None:
    """절단 표식 `\\n...\\n`(줄 하나가 `...`뿐) — 산문이 그것을 **글자 그대로** 적는다. 표식은
    `prompt_loader._TRUNCATION_MARKER`가 정하고 `FACTS.evidence[].body`와 `probe` 관측이 같이 쓴다.
    그리고 실제로 잘린 본문이 그 표식을 든다(상수를 바꿔도 안 쓰는 낡은 상수가 아니다).

    산문에서 읽는 것은 **한 문장**이다 — 줄 모양(`...`)과 JSON 표기(`\\n...\\n`)를 같은 문장이 든다.
    `...`는 산문의 다른 자리(「A message may end in `...`」)에도 백틱으로 있어 그것만 보면
    이 문장에서 빠져도 초록이다(오염으로 확인했다)."""
    marker = prompt_loader._TRUNCATION_MARKER
    clipped, truncated = prompt_loader._bound_text("x" * 100, 40)
    escaped = marker.replace(chr(10), chr(92) + "n")

    assert truncated and marker in clipped
    sentence = _sentence(_flat(), f"`{escaped}`")
    assert f"`{marker.strip()}`" in sentence, "산문이 `...` 줄을 같은 문장에서 안 적는다"


def test_the_v10_prose_quotes_the_probe_log_lines_the_host_writes() -> None:
    """`probe` 관측이 로그 앞에 붙이는 줄 — `--- <tool> output ---` · 잘렸을 때의 `(middle
    shortened)` · 도구가 아무것도 안 쓴 것 · 로그를 못 읽은 것 — 을 산문이 **글자 그대로** 인용한다.
    이 줄들은 모델이 「깨끗한 실행」과 「안 잰 것」을 가르는 유일한 단서라(`_probe_observation`
    독스트링) 철자가 갈리면 모델이 오지 않는 표식을 찾는다.

    기대값은 코드를 **실제로 돌려** 얻는다(도구 이름만 `<tool>`로 바꾼다). 코드 쪽 철자는
    `test_auditor_loop.py`가 부분 문자열로 문다 — 산문과의 연결이 이 검사다."""
    quoted = _backticked(_flat())
    short = _probe_lines("one line")
    cut = _probe_lines("line\n" * 1000)
    quiet = _probe_lines("")
    missing = _probe_lines(None)
    headers = {
        line.replace("asan", "<tool>")
        for line in (*short, *cut)
        if line.startswith("--- asan output")
    }

    assert headers == {"--- <tool> output ---", "--- <tool> output (middle shortened) ---"}
    assert headers <= quoted
    assert quiet[-1] in quoted, "도구가 아무것도 안 쓴 줄을 산문이 인용하지 않는다"
    assert missing[-1] in quoted, "로그를 못 읽은 줄을 산문이 인용하지 않는다"
    assert quiet[-1] != missing[-1]


def _semgrep_payload() -> str:
    hits = [
        {
            "path": "a.c",
            "check_id": f"rule.r{n}",
            "start": {"line": 3 + n},
            "end": {"line": 4 + n},
            "extra": {"message": "a message that is long enough to be previewed"},
        }
        for n in range(3)
    ]
    return json.dumps(
        {"errors": [{"code": 3, "message": "e" * 20}], "results": hits, "version": "1.0"}
    )


def test_the_v10_prose_quotes_the_semgrep_fold_lines_the_host_writes() -> None:
    """semgrep 로그를 접은 본문의 줄 — `errors: N (M chars dropped); other JSON fields omitted` · `K
    of N shown` · 히트 줄 `path:start-end rule: message` — 을 산문이 인용한다. 접힌 로그는 가운데를
    자른 것이 아니라 접은 것이라 절단 표식(`...` 줄)이 **없고**, 그래서 모델이 이 줄들을 읽는 법을
    산문이 알려야 한다.

    기대값은 `_fold_semgrep_results`가 **실제로 낸 줄**에서 뽑는다 — 수는 자리표시(`#`)로 접어
    산문의 `N`·`M`·`K`와 같은 꼴로 맞춘다(산문의 자리표시 철자는 안 문다). 히트 줄은
    **꼴**(`토큰:수-수 토큰: …`)만 문다 — 철자 핀이다. 그리고 접힌 로그는 **항상 `truncated`**이고
    `probe` 관측 머리줄도 그때 잘렸다고 적는다(산문의 「절단 표시가 선다」가 기대는 코드 사실)."""
    flat = _flat()
    ev = _evidence("semgrep")
    summaries = prompt_loader.evidence_summaries((ev,), lambda _ref: _semgrep_payload())
    summary = summaries[ev.evidence_id]
    body = str(summary["body"]).splitlines()
    notices = [line for line in body if line.startswith("errors:")]
    counts = [line for line in body if re.fullmatch(r"\d+ of \d+ shown", line)]
    hits = [line for line in body if re.match(r"\S+:\d+-\d+ \S+: ", line)]
    assert len(notices) == len(counts) == 1 and hits, f"접힌 본문의 꼴이 달라졌다: {body}"
    notice, count = notices[0], counts[0]

    def fold(text: str) -> str:
        return re.sub(r"\d+", "#", text)

    prose = re.sub(r"(?<![A-Za-z])[NMK](?![A-Za-z])", "#", flat)
    assert fold(notice) in prose, f"산문에 접힌 로그의 오류 줄({fold(notice)!r})이 없다"
    assert fold(count) in prose, f"산문에 접힌 로그의 건수 줄({fold(count)!r})이 없다"
    assert hits and re.search(r"`\w+:\w+-\w+ \w+: \w+`", flat), "히트 줄의 꼴을 산문이 안 적는다"
    assert body.index(notice) < body.index(count) < body.index(hits[0])
    assert not any(line == "..." for line in body), "접힌 본문에 `...` 줄이 있다"
    assert summary["truncated"] is True
    assert "(middle shortened)" in " ".join(_probe_lines(_semgrep_payload(), "semgrep"))


# --- 산문이 없는 입력을 서술하지 않는다 -----------------------------------------------------


def _repo_texts() -> Iterator[tuple[Path, str]]:
    """`src/`와 `config/`의 텍스트 파일 — 「그 문구를 내는 코드가 없다」를 **넓게** 본다."""
    for root in (SRC_DIR, CONFIG_DIR):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".json", ".md", ".toml"}:
                yield path, path.read_text(encoding="utf-8", errors="ignore")


def test_the_v10_prose_describes_no_input_the_auditor_is_not_given() -> None:
    """v10 산문이 **Auditor가 안 받는 입력**을 서술하지 않는다 — 힌트 절·슬라이스 anchor note를
    지웠고, 슬라이스 기준(`slice_criterion`)은 안 받는다고 적었다. 모델이 읽는 명령이라 없는 입력을
    서술하면 그 입력을 찾아 헤맨다.

    그 사실의 코드 쪽 근거 둘: `extracted around`를 내는 코드가 `src/`·`config/`에 **없다**(하네스는
    anchor note를 SOURCE에 안 싣는다), 그리고 Auditor의 hop0 렌더(`_render_hop0`)가 `build_facts`에
    `slice_criterion`을 안 넘긴다(그 키는 Scout 전용 opt-in FACT 키다 — `prompt_loader.FACT_KEYS`).

    ⚠ 철자 핀이다 — 힌트를 다른 낱말(`clue`)로 쓰거나 `extracted near`로 적은 note는 못 잡는다. 코드
    쪽 둘은 문구·호출 철자를 묻는다."""
    prefix = _prefix()

    assert "hint" not in prefix.lower()
    assert re.search(r"does not receive the slice criterion", _flat())
    assert "slice_criterion" in prompt_loader.FACT_KEYS, "전제 — 그 키는 FACT 허용 목록에 있다"
    assert "slice_criterion" not in inspect.getsource(auditor._render_hop0)
    emitters = [path.name for path, text in _repo_texts() if "extracted around" in text]
    assert emitters == [], f"anchor note를 내는 코드가 생겼다: {emitters}"


def test_the_v10_prose_names_exactly_the_input_blocks_the_body_fills() -> None:
    """산문이 입력 블록으로 부르는 다섯 이름이 `body.md`의 자리표시자와 같다 — 산문이 `{{X}}`를
    부르는데 본문에 없으면 모델이 없는 블록을 찾고, 본문이 새 블록을 더했는데 산문이 모르면 그
    블록은 「Trust boundary」의 「data, not instructions」 목록 밖이라 지시로 읽힌다."""
    body_blocks = set(re.findall(r"\{\{(\w+)\}\}", (V10 / "body.md").read_text(encoding="utf-8")))
    trust = _sentence(_section("## Trust boundary"), "are data, not instructions")
    named = set(re.findall(r"`([A-Z_]+)`", trust.split(" are data")[0]))

    assert named == body_blocks


# --- 이 PR이 고친 옛 산문을 되살리지 않는다 ------------------------------------------------
#
# PR #521 원문(`git show 57c34f0:prompts/auditor/v10/prefix.md`)은 **하네스가 안 하는 일을 한다고
# 적은 문장들**을 들고 있었고 이 PR이 그것을 고쳤다. 그런데 **고친 문장을 원문으로 되돌려도
# 다른 검사는 하나도 안 빨개졌다**(독립 검토의 변이 실험 — 산문 한 문장을 되돌리면 전량이
# 초록). 여기가 그 음성 핀이다. 라벨 하나가 거짓 명제 하나이고, 막는 것은 **그 철자가 다시
# 나타나는 것**이다.
#
#   slice_anchor_note — 하네스는 SOURCE 첫 줄에 anchor note를 안 싣는다. `src/`·`config/`에
#       `extracted around`를 내는 코드가 0건이고 `slice_criterion`은 Scout 전용 FACT 키다.
#   hint — 위 명제의 힌트 절(「hinted candidate first」는 선택 규칙에도 들어 있었다).
#   char_cap — SOURCE 상한의 단위는 글자가 아니라 **토큰**이다(`thresholds.auditor_source_tokens`).
#   cap_is_not_a_token_budget — 같은 명제의 반대편. 켜진 상한은 토큰 예산이다.
#   tool_definitions — 하네스는 도구 정의를 안 보낸다. 요청 payload에 `tools`가 없고
#       (`openai_compat._request_payload`는 `response_format`만 싣는다) 도구는 매 홉 구조화 출력
#       JSON의 `action`이다.
#   final_output_without_a_tool_call — 위 명제의 최종 출력 쪽. 최종 출력은 도구 호출 밖의 JSON이
#       아니라 `action: "answer"` 봉투다.
#   same_tool_response — `probe`를 다시 돌리면 `evidence_id`가 **새로 난다**(두 ToolRunner가
#       호출마다 `ids.next("evidence")`를 쓴다). 로그가 같은지는 안 쟀다.
#
# (`"cwe": null`은 여기 없다 — `_cwe_null_clauses`가 양성·음성 핀과 함께 이미 막는다.)
_STALE_SPELLINGS = {
    "slice_anchor_note": re.compile(r"extracted around|slice-anchor|\banchor note\b|-anchored\b"),
    "hint": re.compile(r"\bhint", re.IGNORECASE),
    "char_cap": re.compile(r"\d-char(?:acter)?s?\b[^.;]{0,30}\bcap\b"),
    "cap_is_not_a_token_budget": re.compile(r"\bbounds construction only\b|\bnot a token budget\b"),
    "tool_definitions": re.compile(
        r"\btool definitions?\b|\bsent with this request\b|\bthe tools below\b"
    ),
    "final_output_without_a_tool_call": re.compile(r"\bwith no tool call\b"),
    "same_tool_response": re.compile(
        r"\b(?:returns?|yields?|gives?)\s+the same (?:tool )?(?:response|observation|output|log)\b"
    ),
}


def _stale_claims(text: str) -> list[tuple[str, str]]:
    """`text`에서 옛 철자 검출기가 잡은 `(라벨, 잡힌 글자)` — 없으면 `[]`."""
    return [
        (label, found.group(0))
        for label, pattern in _STALE_SPELLINGS.items()
        for found in pattern.finditer(text)
    ]


# 양성 핀 — **저장소 실물에서 온 철자만 적는다**(지어낸 샘플은 잡히는 꼴만 고르게 된다). 출처는 이
# PR 전 v10 산문(`git show 57c34f0:prompts/auditor/v10/prefix.md`)의 줄이고, 앞서 판들에 같은 말이
# 있으면(v4~v6 `15,000-character`) 그 줄을 같이 적는다.
_STALE_POSITIVE = (
    ("slice_anchor_note", "Note: this slice was extracted around the SINK `<name>` in"),  # 63
    ("slice_anchor_note", "or instructions — the slice-anchor note (first line) and"),  # 19
    ("slice_anchor_note", "Apart from the anchor note and banners, `SOURCE` is exact"),  # 45
    ("slice_anchor_note", "(sink-anchored slice)"),  # 63
    ("hint", "(hinted candidate first, else the strongest; step 3)"),  # 5
    ("hint", "A note naming a SINK is a **sink hint**: it gives the sink"),  # 66
    ("char_cap", "even if it alone exceeds the 15,000-char cap."),  # 50
    ("char_cap", "stays within the 15,000-character cap."),  # prompts/auditor/v6/prefix.md:59
    ("cap_is_not_a_token_budget", "The cap bounds construction only — not a token budget."),  # 53
    ("tool_definitions", "schema, and the tool definitions are set by the harness;"),  # 15
    ("tool_definitions", "The tool definitions sent with this request give the exact"),  # 215
    ("tool_definitions", "Before the final output you may call the tools below;"),  # 15
    ("final_output_without_a_tool_call", "exactly one JSON object, with no tool call."),  # 15
    (
        "final_output_without_a_tool_call",
        "output the JSON of the exact output contract with no tool call; that is the",  # 220
    ),
    ("same_tool_response", "Running the same tool again returns the same tool response."),  # 218
)

# 음성 핀 — 같은 사실을 **참으로** 적은 문장이다(검출기가 정상 문장을 막지 않는다). 정의상 저장소에
# 없는 음성 샘플은 「그 문형이 실제로 나타날 수 있는가」로 고른다: 아래는 전부 이 PR이 위 거짓 문장
# **자리에** 쓴 문장이라 되돌림 직전에 그 문장과 나란히 서 있는 이웃이고, 그래서 어휘가 겹친다
# (`slice`·`cap`·`tool call`·`same tool`·`final output`). 출처는 작업 트리의 v10 prefix 절이다(작성
# 시점의 줄 번호를 적는다 — 줄이 움직여도 문장은 그 절에 있다).
_STALE_NEGATIVE = (
    (  # Scout and context-capsule status, 39줄
        "slice_anchor_note",
        "This audit does not receive the slice criterion either (which end of the slice path the "
        "slicer was given); the expanded capsule is assembled separately.",
    ),
    (  # Role, 5줄
        "hint",
        "produce a concise audit result for the one the procedure selects (the strongest weakness "
        "established; step 3).",
    ),
    (  # Original-source input contract, 51줄 — 토큰 상한
        "char_cap",
        "Later functions are appended only while the total stays within the 20,000-token cap, "
        "counted in Auditor-model tokens (approximated when that tokenizer is unavailable);",
    ),
    (  # Original-source input contract, 52줄 — 글자로 세는 것은 정의 블록의 한도(`cap`이 곁에 선다)
        "char_cap",
        "its block, counting its banner line, is at most 2,000 characters, and the same cap "
        "allows it.",
    ),
    (  # Original-source input contract, 59줄
        "cap_is_not_a_token_budget",
        "The cap bounds what `SOURCE` supplies; it does not mean every relevant function, "
        "definition, callee, or caller fits within it.",
    ),
    (  # Role, 6줄
        "tool_definitions",
        "A tool call is not a native function call: on every hop you output one JSON object whose "
        "`action` is `lookup`, `probe`, or `answer` (Exact output contract), and the host carries "
        "out the first two.",
    ),
    (  # Tool use, 210줄
        "final_output_without_a_tool_call",
        "To end the audit, output an `answer` action whose `procedure` is the JSON of the exact "
        "output contract; that is the only way a result is recorded.",
    ),
    (  # Tool use, 208줄
        "same_tool_response",
        "Since you send no input, running the same tool again puts the same question to it, and "
        "doing so straight after the previous call is a repeat (below). Whether a later re-run "
        "would log anything different has not been measured; each run reports its own "
        "`evidence_id`.",
    ),
)


@pytest.mark.parametrize(("label", "quote"), _STALE_POSITIVE)
def test_the_stale_detectors_catch_the_spellings_the_original_v10_prose_used(
    label: str, quote: str
) -> None:
    """검출기가 **아무것도 안 잡는 검출기가 아니다**(CLAUDE.md 2.5) — 이 PR 전 v10 산문의 철자
    그대로를 그 라벨이 잡는다. 정규식을 지우거나 좁혀도 아래 전수(`..._does_not_revive_...`)는
    `[] == []`로 초록이라, 이 핀이 없으면 검출기가 죽어도 CI는 영영 초록이다."""
    assert label in {found for found, _ in _stale_claims(" ".join(quote.split()))}


@pytest.mark.parametrize(("label", "sentence"), _STALE_NEGATIVE)
def test_the_stale_detectors_let_the_truthful_replacements_through(
    label: str, sentence: str
) -> None:
    """같은 사실을 참으로 적은 문장은 **어느 라벨도** 안 잡는다 — 검출기가 넓어져 정상 산문을 막는
    상태(양성 핀만 있으면 못 잡는다)를 막는다. `label`은 이 문장이 대신하는 거짓 명제다."""
    assert label in _STALE_SPELLINGS
    assert _stale_claims(sentence) == []


def test_every_stale_detector_carries_both_pins() -> None:
    """라벨마다 양성 핀과 음성 핀이 **하나 이상** 있다 — 새 라벨을 더하고 핀을 안 달면 빨개진다
    (`tests/test_docs_format.py`의 같은 규율이 모듈 수준 정규식에 거는 것을 이 표에 건다)."""
    assert {label for label, _ in _STALE_POSITIVE} == set(_STALE_SPELLINGS)
    assert {label for label, _ in _STALE_NEGATIVE} == set(_STALE_SPELLINGS)


def test_the_v10_prose_does_not_revive_what_the_original_v10_prose_got_wrong() -> None:
    """위 라벨들의 철자가 v10 산문에 **다시 나타나지 않는다** — 이 PR이 고친 PR #521 원문의 결함을
    되돌리면 빨개진다.

    **사고 형태 —** 독립 검토의 변이 실험: 산문 한 문장을 원문으로 되돌려도(예: 「The cap bounds
    construction only — not a token budget.」) 전량이 초록이었다. 그 문장들은 하네스가 안 하는 일을
    한다고 모델에게 말하는 거짓이다(위 표 머리의 라벨별 사실).

    ⚠ **철자를 단언하지 성질을 단언하지 않는다.** 「이 철자로 되돌리면 죽는다」가 참인 명제이고,
    같은 거짓을 **다른 낱말로** 쓰면(`tool schemas` · `hint` 대신 `clue`) 못 잡는다. 명제 쪽의
    대조는 그 사실의 코드 쪽 핀들이 맡는다(`..._describes_no_input_the_auditor_is_not_given`의
    코드 둘 · `test_config.py`의 `-token cap` · `_cwe_null_clauses`). `hint`는 그 첫 검사가 맨
    부분 문자열로도 막는다(겹치지만 그쪽은 철자 하나, 이쪽은 어형까지)."""
    assert _stale_claims(_flat()) == []


# --- 산문이 약속하는 재현 명세 ↔ 그것을 채우는 설정 · 코드 -------------------------------------


# v10의 `repro_spec` 약속을 바이트 그대로 든 판 — ⏵ 2026-10-09 v11(이슈 #525)은 v10에서 `cwe` 서술
# 네
# 줄만 바꿨고 그 약속 문장은 같다(`tests/test_nullable_cwe.py`가 차이를 그 네 줄로 묶는다). 서빙
# 판이
# v11로 올라가도 이 약속의 「스위치가 켜져 있다」는 그대로 걸려야 한다.
# ⏵ 2026-10-10 — v12(이슈 #578)도 그 약속 문장(§5 CWE and reproduction의 `repro_spec` 서술)이 v11과
# 같다(`tests/test_auditor_v12.py`가 그 줄이 안 바뀐 것을 문다). 서빙 판이 아니라 지금은 안 고른다.
# ⏵ 같은 날 v13(이슈 #67)도 같다 — v11에서 FACTS `source` 서술 두 줄만 바꿨다
# (`tests/test_auditor_v13_facts.py`가 차이를 그 두 줄로 묶는다).
# ⏵ 2026-10-11 v14(이슈 #67 · #578)도 같다 — v12에서 FACTS `source` 서술 두 줄만 바꿨다
# (`tests/test_auditor_v14_facts.py`).
_V10_REPRO_PROMISE_VERSIONS = frozenset({"v10", "v11", "v12", "v13", "v14"})


def _profiles_serving_v10() -> list[str]:
    """`config/profiles.yaml`에서 Auditor 좌석이 v10(또는 그 약속을 그대로 든 v11·v13)을 고르는
    프로필 — 판 이름을 이 검사가 쥐지 않고 **설정에서 읽는다**(배선한 프로필이 늘면 같이
    따라온다)."""
    text = (CONFIG_DIR / "profiles.yaml").read_text(encoding="utf-8")
    return [
        name
        for name in sorted(yaml.safe_load(text)["profiles"])
        if load_config(name).model_for("auditor").prompt_template_version
        in _V10_REPRO_PROMISE_VERSIONS
    ]


def _stub_context(synthesize: int) -> SimpleNamespace:
    """`auditor._ensure_repro_origin`이 읽는 것(`config.thresholds` · `trace.emit`)만 든 문맥."""
    config = load_config("harness_pool")
    thresholds = config.thresholds.transition(synthesize_repro_spec=synthesize)
    return SimpleNamespace(
        config=config.transition(thresholds=thresholds),
        trace=SimpleNamespace(emit=lambda *_args, **_fields: None),
    )


def test_the_v10_repro_spec_promise_matches_the_switch_and_the_node() -> None:
    """「`repro_spec`을 안 보내면 하네스가 채운다」는 약속이 **그것을 켜는 설정과 노드의
    동작**과 같다.

    **사고 형태 —** 독립 검토: `config/budgets.yaml`의 `synthesize_repro_spec`을 0으로 두면 그
    약속이 거짓이 되는데 아무 검사도 안 빨개졌다. 서빙판 핀이 켜진 SOURCE 옵션을 묶는 것과
    같은 결이다(`test_config.py`의 `-token cap` 핀). 산문이 약속하는 다섯을 코드·설정으로
    대조한다.

    ① v10을 고르는 모든 프로필에서 스위치가 켜져 있다 — 꺼지면 약속이 거짓이다(아래 ②의 꺼진 쪽이 그
    거짓의 실물이다) ② 켜면 모델이 안 낸 명세를 **모든 `trigger_status`에서** 채운다(`no_signal`
    포함 — 「whatever the `trigger_status`」) ③ 채운 명세가 **후보 위치**를 가리킨다 ④ 하네스가 채운
    것은 `origin=harness`(「marks it as its own」) ⑤ 모델이 낸 명세는 내용이 그대로고 `origin=model`
    (「kept and marked as yours」 — 모델이 `harness`를 참칭해도 덮인다).

    ⚠ 철자 핀이다 — 약속의 문형을 바꾸면 이 검사의 문구를 새 문형에 맞춘다. 스위치의 **기본값**이
    어느 쪽인가는 묻지 않는다(`test_nodes.py`가 꺼짐·켜짐을 각각 주입해 문다)."""
    flat = _flat()
    for phrase in (
        "when you send none, the harness fills in a generic reproduction spec",
        "for the candidate's location, whatever the `trigger_status`,",
        "and marks it as its own; a spec you send is kept and marked as yours",
    ):
        assert phrase in flat, f"산문에 {phrase!r}가 없다"

    serving = _profiles_serving_v10()
    assert serving, "전제 — v10·v11·v13을 고르는 프로필이 없으면 이 검사가 아무것도 안 묻는다"
    for profile in serving:
        assert load_config(profile).thresholds.synthesize_repro_spec == 1, profile

    candidate = SimpleNamespace(location=SimpleNamespace(symbol="parse_header", file="parser.c"))
    ids = {"finding_id": "find_0001", "candidate_id": "cand_0001", "missing_id": "mev_0001"}
    stamped = ProducedBy(role=Role.AUDITOR, model="m", prompt_version="v10")

    def finding(status: str, **override: object):  # noqa: ANN202
        procedure = AuditProcedure.model_validate(_procedure_payload(status, **override))
        return auditor._finding_from_procedure(
            procedure, ids=ids, run_id="run_0001", produced_by=stamped
        )

    def ensured(synthesize: int, status: str, **override: object):  # noqa: ANN202
        return auditor._ensure_repro_origin(
            _stub_context(synthesize),
            finding(status, **override),
            candidate,
            finding_id="find_0001",
        ).repro_spec

    for status in _types_of(AuditProcedure, "trigger_status"):
        assert ensured(0, status) is None, f"{status}: 꺼졌는데 채웠다 — 약속은 스위치에 달렸다"
        spec = ensured(1, status)
        assert spec is not None, f"{status}: 켜졌는데 안 채웠다"
        assert spec.origin is ReproOrigin.HARNESS
        assert "parse_header" in spec.expected_signal and "parse_header" in spec.steps[0]

    own = {"steps": ["./run.sh"], "expected_signal": "ASAN heap-buffer-overflow"}
    for claimed in ("model", "harness"):
        kept = ensured(1, "triggerable", repro_spec={**own, "origin": claimed})
        assert kept is not None and kept.origin is ReproOrigin.MODEL
        assert (kept.steps, kept.expected_signal) == (own["steps"], own["expected_signal"])


# v10 prefix의 글자 수. 🔴 **이 수를 베껴 든 자리가 넷이다** — `config/budgets.yaml`(hop0 계산) ·
# `src/config.py` · `src/orchestrator/nodes/auditor.py` · `PROJECT-CONTEXT.md` §5 Auditor 행.
# 아래 검사가 그 넷의 **강제 장치**다: 산문을 고치면 빨개지고, 그때 넷을 같이 고친다.
_PREFIX_CHARS = 56_906


def test_the_v10_prefix_length_is_the_number_four_other_places_copy() -> None:
    """산문 길이가 그것을 베낀 자리들과 같다.

    🔴 **핀을 둔 이유는 사고다.** 이 수가 **한 세션에서 두 번 낡았다** — 산문을 고친 뒤 다시 재지
    않아서다. 처음엔 「v8보다 1,815자」가 틀린 수로 남아 있었고, 고쳐 적은 53,264도 그 뒤 산문
    수정으로 낡았다(적대적 검토가 둘 다 잡았다). 글자 수는 **실측**이라 적히므로 낡으면 CLAUDE.md
    §0을 어긴다 — 실측이라 적힌 값이 실측이 아니게 된다.

    ⚠ **이 검사는 철자가 아니라 길이를 문다** — 산문을 어떻게 고쳐도 길이가 바뀌면 빨개진다.
    그것이 목적이다: 빨개지면 위 네 자리를 다시 재서 고치라는 신호다. v8·v7은
    `tests/test_auditor_prompt_v8.py`가 같은 식으로 든다(그 선례를 따랐다).

    `body`는 v6~v10이 같은 282자다 — 그 동일성은 다른 검사가 든다.
    """
    assert len(load_prompt(Role.AUDITOR, "v10").prefix) == _PREFIX_CHARS, (
        "v10 산문의 길이가 바뀌었다 — `config/budgets.yaml`·`src/config.py`·"
        "`src/orchestrator/nodes/auditor.py`·`PROJECT-CONTEXT.md`의 글자 수와 그 파생값"
        "(v8·v9 대비 차 · hop0 계산값 · 비(比))을 다시 재서 고치고 이 상수를 갱신한다"
    )


# 그 수를 베낀 자리 넷. 🔴 **위 길이 핀은 「다시 재라」고 깨울 뿐 이 넷이 맞는지는 안 본다** —
# 적대적 검토가 이 넷의 숫자를 한 글자씩 바꾸는 변이 열 개를 돌려 **열 개가 전부 살아남는** 것을
# 확인했다. 아래 검사가 그 구멍이다.
_PREFIX_COPY_SITES = (
    "config/budgets.yaml",
    "src/config.py",
    "src/orchestrator/nodes/auditor.py",
    "PROJECT-CONTEXT.md",
)


def test_every_place_that_copies_the_v10_prefix_length_carries_the_measured_number() -> None:
    """글자 수를 베낀 네 자리가 **실측값과 같은 수**를 든다.

    🔴 **이 수는 한 세션에서 세 번 낡았다** — 산문을 고친 뒤 다시 재지 않아서다. 「실측」이라
    적힌 값이 실측이 아니게 되는 것은 CLAUDE.md §0을 정면으로 어긴다. 위 길이 핀은 **고치라는
    신호**를 줄 뿐이고, 신호를 받고 네 자리 중 셋만 고쳐도 아무도 모른다.

    ⚠ **포맷을 단언한다** — `56,906`처럼 천 단위 쉼표를 쓴 꼴이다. 다른 표기(`56906`)로 적으면
    여기서 빨개지고, 그것이 의도다: 네 자리가 **같은 꼴로** 같은 수를 들어야 사람이 대조한다.
    v8·v9 대비 차도 같이 묶는다 — 길이만 고치고 차를 안 고치는 것이 실제로 났던 일이다.
    """
    prefix_chars = len(load_prompt(Role.AUDITOR, "v10").prefix)
    repo = PROMPTS_DIR.parent
    texts = {name: (repo / name).read_text(encoding="utf-8") for name in _PREFIX_COPY_SITES}

    assert prefix_chars == _PREFIX_CHARS, "위 길이 핀이 먼저 빨개진다 — 그쪽 메시지를 읽어라"
    assert [name for name, text in texts.items() if f"{prefix_chars:,}" not in text] == []
    assert f"+{prefix_chars - 33_209:,}자" in texts["config/budgets.yaml"], "v8 대비 차"
    for name in ("config/budgets.yaml", "PROJECT-CONTEXT.md"):
        assert f"+{prefix_chars - 35_501:,}자" in texts[name], f"{name}의 v9 대비 차"

