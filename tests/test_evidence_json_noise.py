"""도구 로그 절단이 **의미 있는 부분**을 버리지 않는다 — 이슈 #481.

`evidence_summaries`는 근거 본문을 증거당 800자(head 2/3 + tail 1/3)로 자른다. semgrep
`--json`의 최상위 키는 **알파벳 순**이라 `errors`(파싱 실패 목록)가 `results`(실제 히트 —
`check_id`·줄번호)보다 **먼저** 온다. 그래서 head가 구문 오류로 차고 `results`가 정확히 절단
구간에 들어간다.

**2026-10-04 운영 run(`run_20261003_0003`)의 실측**(`semgrep/ev_0002.log`, 60,964자):

| | |
| --- | --- |
| stdout JSON | 60,113자 · **물리 줄 1개** |
| `errors` | **42,401자 (70.5%)** — 스팬 148개를 든 파싱 실패 하나 |
| `results` | 17,291자 · 시작 위치 **42,508자** (JSON의 70.7% 지점) |
| 그때 모델이 받은 본문 | 800자 — `check_id` **0건** · 줄번호는 꼬리 조각의 3093 하나 |

🔴 **절단 구간이 우연이 아니라 구조적이다.** head가 530자(= (800 − 마커 5) × 2/3)인데
`results`는 42,508자 뒤에 있다.

**고친 뒤의 실측(같은 로그)**: `errors`를 빼면 `results`가 108자 지점에서 시작해 head에
`check_id`(`opt.semgrep-rules.c.lang.security.insecure-use-memset`)와 줄번호 1087이 들어온다.
⚠ **10건 중 1건**이다 — 나머지 아홉은 여전히 안 보인다. 한 줄씩 접어 전부 보이게 하는 것은
그 이슈의 선택지 1이고 **도구를 아는 파서**가 필요해 이 PR의 범위 밖이다(`evidence_summaries`는
도구를 모르는 범용 절단기다).

이 파일의 픽스처는 **그 실 로그에서 왔다** — `errors[0]`의 `spans`를 148개 → 3개, `type`의
중첩 스팬 목록을 2개로 줄여 3,127자로 만들었고 `results[0]`은 **손대지 않았다**. 줄인 것은
크기뿐이고 키·값의 모양은 semgrep이 낸 그대로다(CLAUDE.md §2.5 — 핀 샘플은 지어내지 않는다).
그 축소판에서도 `errors`가 921자이고 `results`가 1,028자 지점에서 시작해 **같은 결함이 난다.**
"""

import json

from src.orchestrator.prompt_loader import (
    _JSON_NOISE_KEYS,
    EVIDENCE_BODY_CHAR_CAP,
    _drop_json_noise,
    evidence_summaries,
)
from src.schemas.evidence import Evidence

# 실 로그의 규칙 이름 — 모델이 「무엇이 어디서 걸렸나」를 알 수 있는 가장 짧은 문자열이다.
_RULE_ID = "opt.semgrep-rules.c.lang.security.insecure-use-memset"
_HIT_LINE = "1087"

# `run_20261003_0003`의 semgrep stdout 축소판(위 docstring). **한 물리 줄이다** — semgrep
# `--json`이 그렇게 낸다.
_REAL_STDOUT = (
    '{"errors": [{"code": 3, "level": "warn", "message": "Syntax er'
    'ror at line /src/src-vul/file/src/apprentice.c:118:\\n `int` wa'
    's unexpected", "path": "/src/src-vul/file/src/apprentice.c", "'
    'spans": [{"end": {"col": 12, "line": 118, "offset": 3}, "file"'
    ': "/src/src-vul/file/src/apprentice.c", "start": {"col": 9, "l'
    'ine": 118, "offset": 0}}, {"end": {"col": 12, "line": 119, "of'
    'fset": 3}, "file": "/src/src-vul/file/src/apprentice.c", "star'
    't": {"col": 9, "line": 119, "offset": 0}}, {"end": {"col": 19,'
    ' "line": 120, "offset": 4}, "file": "/src/src-vul/file/src/app'
    'rentice.c", "start": {"col": 15, "line": 120, "offset": 0}}], '
    '"type": ["PartialParsing", [{"end": {"col": 12, "line": 118, "'
    'offset": 3}, "path": "/src/src-vul/file/src/apprentice.c", "st'
    'art": {"col": 9, "line": 118, "offset": 0}}, {"end": {"col": 1'
    '2, "line": 119, "offset": 3}, "path": "/src/src-vul/file/src/a'
    'pprentice.c", "start": {"col": 9, "line": 119, "offset": 0}}]]'
    '}], "interfile_languages_used": [], "paths": {"scanned": ["/sr'
    'c/src-vul/file/src/apprentice.c"]}, "results": [{"check_id": "'
    'opt.semgrep-rules.c.lang.security.insecure-use-memset", "end":'
    ' {"col": 18, "line": 1087, "offset": 25608}, "extra": {"engine'
    '_kind": "OSS", "fingerprint": "07f4faa310274079ec86069798c80f3'
    '4d59ffb50f1c36ab0e1db88917c33aebb4ff22ffd8430d09bf8e3b3c58ec4f'
    '625a6a87466334522e357d46beffaf03dca_0", "fix": "memset_s(&mp[m'
    'set[i].count], 0, sizeof(*mp) *\\n        \\t\\t    ALLOC_INCR)",'
    ' "is_ignored": false, "lines": "\\t\\t(void)memset(&mp[mset[i].c'
    'ount], 0, sizeof(*mp) *\\n\\t\\t    ALLOC_INCR);", "message": "Wh'
    "en handling sensitive information in a buffer, it's important "
    'to ensure  that the data is securely erased before the buffer '
    'is deleted or reused.  While `memset()` is commonly used for t'
    'his purpose, it can leave sensitive  information behind due to'
    ' compiler optimizations or other factors.  To avoid this poten'
    "tial vulnerability, it's recommended to use the  `memset_s()` "
    'function instead. `memset_s()` is a standardized function  tha'
    't securely overwrites the memory with a specified value, makin'
    'g it more  difficult for an attacker to recover any sensitive '
    'data that was stored in  the buffer. By using `memset_s()` ins'
    'tead of `memset()`, you can help to  ensure that your applicat'
    'ion is more secure and less vulnerable to exploits  that rely '
    'on residual data in memory.", "metadata": {"category": "securi'
    'ty", "confidence": "LOW", "cwe": ["CWE-14: Compiler Removal of'
    ' Code to Clear Buffers"], "impact": "MEDIUM", "likelihood": "L'
    'OW", "owasp": ["A04:2021 - Insecure Design", "A06:2025 - Insec'
    'ure Design"], "references": ["https://cwe.mitre.org/data/defin'
    'itions/14.html", "https://owasp.org/Top10/A02_2021-Cryptograph'
    'ic_Failures/"], "subcategory": ["audit"], "technology": ["c"]}'
    ', "metavars": {"$...VARS": {"abstract_content": "&mp[mset[i].c'
    'ount]0 sizeof*mp*ALLOC_INCR", "end": {"col": 17, "line": 1087,'
    ' "offset": 25607}, "start": {"col": 16, "line": 1086, "offset"'
    ': 25554}}}, "severity": "WARNING", "validation_state": "NO_VAL'
    'IDATOR"}, "path": "/src/src-vul/file/src/apprentice.c", "start'
    '": {"col": 9, "line": 1086, "offset": 25547}}], "skipped_rules'
    '": [], "version": "1.86.0"}'
)

# 하네스가 그 로그에 쓴 모양 그대로(`container_tools.ContainerToolRunner._execute`). 머리줄은
# `_HARNESS_HEADER_LINE`이 따로 뺀다 — 여기서는 그 둘이 **같이** 돈다는 것도 본다.
# ⚠ `-v` 뒤의 호스트 경로는 **합성값**이다(실 로그의 그 자리에는 사용자명이 든다).
_REAL_LOG = (
    "# node argv: semgrep --quiet src-vul/file/src/apprentice.c\n"
    "# runtime: docker\n"
    "# semgrep: 10 findings\n"
    "$ docker run --rm --name h-ev_0002-0 --network=none -v C:\\w\\t:/src:ro img semgrep\n"
    "(exit=1)\n"
    "--- stdout ---\n"
    f"{_REAL_STDOUT}\n"
    "--- stderr ---\n"
)


def _body(raw: str) -> dict[str, object]:
    """`evidence_summaries`를 통과한 모델 사본. 형제 파일과 같은 관용구다(`model_construct`)."""
    bare = Evidence.model_construct(evidence_id="evidence_0002", payload_ref="r2")
    return evidence_summaries([bare], lambda _ref: raw)["evidence_0002"]


# --- 결함 ------------------------------------------------------------------------


def test_the_model_sees_which_rule_hit_which_line() -> None:
    """🔴 이 PR의 명제다. 앞 판에서 모델이 받은 것은 `errors`의 구문 오류 스팬뿐이었다.

    Auditor prefix가 *"use evidence IDs only when their registered evidence content
    supports the claim"*을 요구하므로, 어느 줄인지 안 보이면 **인용을 거절하는 것이 맞다** →
    `evidence_refs` 빈 채로 E0 → 채점 불가(이슈 #475·#481).
    """
    body = _body(_REAL_LOG)["body"]

    assert _RULE_ID in body
    assert _HIT_LINE in body


def test_the_findings_count_and_the_exit_code_survive() -> None:
    """하네스가 남기는 줄(발견 수·종료 코드·절 구분)은 그대로다 — 뺀 것은 잡음 키 하나다."""
    body = _body(_REAL_LOG)["body"]

    assert "# semgrep: 10 findings" in body
    assert "(exit=1)" in body
    assert "--- stdout ---" in body


# --- 뺀 사실을 숨기지 않는다 (§0) -------------------------------------------------


def test_the_dropped_size_is_recorded_in_place() -> None:
    """뺀 자리에 **얼마를 뺐는지**가 남는다 - 조용히 사라지면 「없었다」와 구별되지 않는다.

    `truncated` 플래그는 절단을 말하지 이 제거를 말하지 않는다. semgrep이 파싱에 실패한
    사실 자체가 「10건 찾았다」의 신뢰도에 걸리는 정보라(그 이슈의 부수 발견) 크기는 남긴다.
    """
    dropped = _drop_json_noise(_REAL_STDOUT)

    assert '"errors": "<921 chars dropped>"' in dropped
    assert "Syntax error at line" not in dropped


def test_the_result_is_still_valid_json() -> None:
    """뺀 뒤에도 JSON으로 읽힌다 - 값만 자리표시자 문자열로 바뀐다."""
    parsed = json.loads(_drop_json_noise(_REAL_STDOUT))

    assert isinstance(parsed["errors"], str)
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["check_id"] == _RULE_ID
    assert parsed["version"] == "1.86.0"


def test_the_input_string_is_not_mutated() -> None:
    """모델로 가는 **사본**에서만 뺀다 - `_HARNESS_HEADER_LINE`과 같은 계약이다.

    순수 함수라 입력을 바꾸지 않고, 로그 파일은 리더가 읽기만 한다.
    """
    before = _REAL_LOG

    _drop_json_noise(before)

    assert before == _REAL_LOG
    assert "Syntax error at line" in before


# --- 건드리지 않는 것 -------------------------------------------------------------


def test_an_empty_noise_key_leaves_the_payload_byte_identical() -> None:
    """뺄 것이 없으면 **한 바이트도 안 바꾼다** - 재직렬화는 뺄 때만 돈다.

    실 run의 다른 semgrep 로그는 `"errors": []`다(`tests/test_evidence_payload_header.py`의
    `_REAL_SEMGREP_STDOUT`). 그 경우를 다시 포맷하면 모델 입력 바이트가 이유 없이 바뀐다.
    """
    clean = '{"errors": [], "results": [{"check_id": "x"}], "version": "1.86.0"}'

    assert _drop_json_noise(clean) == clean


def test_a_payload_that_is_not_json_is_untouched() -> None:
    """clang-tidy·build·ASan 로그는 JSON이 아니다 - 그 경로는 종전 그대로다."""
    plain = "/src/parser.c:24:5: warning: memcpy with nonconstant length\n1 warning generated.\n"

    assert _drop_json_noise(plain) == plain


def test_a_json_array_or_a_broken_object_is_untouched() -> None:
    """최상위가 객체가 아니거나 못 읽는 JSON이면 손대지 않는다 - 지어내지 않는다.

    ⚠ **두 경우를 거는 가드가 다르다**: 배열은 `{`로 시작하지 않아 줄 자체가 건너뛰어지고,
    깨진 객체는 `json.loads`의 `ValueError`가 건다. `isinstance(body, dict)` 검사는 **닿지
    않는다**(읽히는 `{`-시작 JSON은 객체다) — 오염 검증이 그것을 드러내 그 분기를 지웠다.
    """
    array = '[{"errors": [1, 2, 3]}]'
    broken = '{"errors": [1, 2, '

    assert _drop_json_noise(array) == array
    assert _drop_json_noise(broken) == broken


def test_a_noise_key_that_is_not_at_the_top_level_is_untouched() -> None:
    """`results[*].extra` 안의 같은 이름은 안 건드린다 - 최상위 키만 본다."""
    nested = '{"results": [{"check_id": "x", "extra": {"errors": ["keep", "me"]}}]}'

    assert _drop_json_noise(nested) == nested


def test_the_noise_list_stays_short_and_declared() -> None:
    """🔴 잡음 키는 **실측으로 고른다.** 지금 하나다.

    그 로그에서 `errors`가 70.5%를 먹었고 나머지 최상위 키는 `paths` 51자 ·
    `interfile_languages_used` 2자 · `skipped_rules` 2자 · `version` 8자로 head를 먹지
    않는다. 취향으로 목록을 늘리면 **도구가 낸 결과를 뺄 수 있다** - 이 도구에서 `errors`는
    파싱 실패지만 다른 도구에서는 그것이 결과일 수 있다(그 경우는 미실측이다).
    """
    assert _JSON_NOISE_KEYS == ("errors",)


# --- 절단과 같이 돈다 -------------------------------------------------------------


def test_the_body_still_obeys_the_per_evidence_cap() -> None:
    """뺀 뒤에도 상한은 상한이다 - 예산을 늘리는 수정이 아니다(그 이슈의 선택지 4를 안 골랐다)."""
    summary = _body(_REAL_LOG)

    assert len(summary["body"]) <= EVIDENCE_BODY_CHAR_CAP
    assert summary["truncated"] is True
