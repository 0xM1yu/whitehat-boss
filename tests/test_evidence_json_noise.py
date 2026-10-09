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

**고친 뒤(계산값 — 실 로그가 저장소에 없어 그 로그로는 다시 못 쟀다)**: `errors`를 자리표시자로
바꾸면 `results`가 stdout 안 130자 지점에서 시작한다(= `{"errors": ` 11 + 자리표시자 23 +
errors와 results 사이 96 — 96은 42,508 − 42,401 − 11). 같은 구조의 픽스처를 이 함수로 재면
128이다. 픽스처의 본문(머리줄 합쳐)에서 `check_id`
(`opt.semgrep-rules.c.lang.security.insecure-use-memset`)는 189자, 그 이름의 끝은 254자,
줄번호 1087은 284자라 모두 head 530자 안이다.
위 수치는 범용 잡음 제거 경로의 값이다. semgrep은 이제 별도 접기 경로로 규칙·경로·줄 범위와
메시지 접두를 히트당 한 줄에 싣는다. 첫 히트를 열 번 반복한 축소판에서 800자 본문에
**5 of 10 shown**을 실측했다. 서로 다른 실제 열 히트의 가시성이나 모델 인용률 실측은 아니다.

이 파일의 픽스처는 **그 실 로그에서 왔다** — `errors[0]`의 `spans`를 148개 → 3개, `type`의
중첩 스팬 목록을 2개로 줄여 3,127자로 만들었고 `results[0]`은 **손대지 않았다**. 줄인 것은
크기뿐이고 키·값의 모양은 semgrep이 낸 그대로다(CLAUDE.md §2.5 — 핀 샘플은 지어내지 않는다).
그 축소판에서도 `errors`가 921자이고 `results`가 1,028자 지점에서 시작해 **같은 결함이 난다.**
"""

import json
from pathlib import Path

import pytest

from src.adapters.container_tools import PAYLOAD_READ_CAP, make_payload_reader
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


def _body(raw: str, *, source: str = "semgrep", cap: int = 800) -> dict[str, object]:
    """`evidence_summaries`를 통과한 모델 사본. 형제 파일과 같은 관용구다(`model_construct`)."""
    bare = Evidence.model_construct(evidence_id="evidence_0002", payload_ref="r2", source=source)
    return evidence_summaries([bare], lambda _ref: raw, per_cap=cap)["evidence_0002"]


def _ten_hit_log() -> str:
    """실 로그 축소판의 첫 히트를 열 번 반복한다 — 키·값은 그대로, 배열 크기만 바꾼다.

    출처: 이 파일 `_REAL_STDOUT`의 `results[0]` (`run_20261003_0003/semgrep/ev_0002.log`).
    서로 다른 히트를 실측한 픽스처가 아니다. 건수는 규칙·줄 범위를 함께 든 물리 줄로 센다.
    """
    document = json.loads(_REAL_STDOUT)
    document["results"] *= 10
    return _REAL_LOG.replace(_REAL_STDOUT, json.dumps(document))


def test_ten_hits_show_the_maximum_complete_rule_and_line_rows() -> None:
    """800자 안에서 완전한 규칙·경로·줄·메시지 접두를 가능한 많이 싣는다."""
    body = str(_body(_ten_hit_log())["body"])
    hit = json.loads(_REAL_STDOUT)["results"][0]
    identity = f'{hit["path"]}:{hit["start"]["line"]}-{hit["end"]["line"]} {_RULE_ID}: '
    rows = [line for line in body.splitlines() if line.startswith(identity)]
    assert len(rows) == 5  # 이 실 로그 유래 반복판으로 잰 표시 건수
    assert all(line[len(identity):].startswith(hit["extra"]["message"][:13]) for line in rows)
    assert f"{len(rows)} of 10 shown" in body
    assert len(body) == 800
    # 메시지 최소 16자와 현재 문맥/생략 표시를 유지하면 여섯 번째 완전한 줄은 못 들어간다.
    overhead = body.index(rows[0])
    assert overhead + 6 * (len(identity) + 16) + 5 > 800


def test_semgrep_folding_uses_the_evidence_source() -> None:
    """다른 도구가 같은 JSON을 내도 semgrep으로 추측하지 않는다."""
    assert "of 10 shown" not in str(_body(_ten_hit_log(), source="asan")["body"])


@pytest.mark.parametrize("cap", [20, 150, 350, 800, 2000])
def test_folded_rows_and_count_fit_the_available_budget(cap: int) -> None:
    """실 로그 유래 열 히트의 완전한 행 수와 표시 건수가 작은 잔여 예산에서도 같다."""
    body = str(_body(_ten_hit_log(), cap=cap)["body"])
    rows = [line for line in body.splitlines() if _RULE_ID in line]
    assert f"{len(rows)} of 10 shown" in body
    assert len(body) <= cap


def test_folding_charges_the_shared_budget_by_the_rendered_body() -> None:
    """첫 접기 본문을 청구하고 두 번째 증거는 남은 350자만 쓴다."""
    evidences = [
        Evidence.model_construct(evidence_id=f"evidence_{n:04d}", payload_ref="r", source="semgrep")
        for n in (1, 2)
    ]
    out = evidence_summaries(evidences, lambda _ref: _ten_hit_log(), total_cap=1150)
    assert len(out["evidence_0001"]["body"]) == 800
    assert len(out["evidence_0002"]["body"]) <= 350
    assert "1 of 10 shown" in out["evidence_0002"]["body"]


def test_a_malformed_hit_falls_back_without_inventing_a_row() -> None:
    """실 로그 히트의 필수 필드를 뺀 음성 판 — 손상된 결과 문서는 접지 않는다."""
    document = json.loads(_REAL_STDOUT)
    del document["results"][0]["start"]
    log = _REAL_LOG.replace(_REAL_STDOUT, json.dumps(document))
    assert "of 1 shown" not in str(_body(log)["body"])


def test_stderr_json_is_not_folded_as_stdout() -> None:
    """실 로그의 stdout/stderr 위치만 바꿔 stderr를 히트로 오인하는 회귀를 막는다."""
    log = _REAL_LOG.replace(f"{_REAL_STDOUT}\n--- stderr ---", f"\n--- stderr ---\n{_REAL_STDOUT}")
    assert "of 1 shown" not in str(_body(log)["body"])


# --- 결함 ------------------------------------------------------------------------


def test_the_model_sees_which_rule_hit_which_line() -> None:
    """🔴 이 PR의 명제다. 앞 판에서 모델이 받은 것은 `errors`의 구문 오류 스팬뿐이었다.

    Auditor prefix가 *"use evidence IDs only when their registered evidence content
    supports the claim"*을 요구하므로, 어느 줄인지 안 보이면 **인용을 거절하는 것이 맞다** →
    `evidence_refs` 빈 채로 E0 → 채점 불가(이슈 #475·#481). ⏵ **2026-10-09 — 그 사슬의 끝은
    틀렸다**: `run_20261007_0001`에서 E0 5/5인 채로도 PoV가 돌아 채점기에 7건을 냈다(#475의
    「PoV 0건 · 채점 불가」가 그 run에서 반증됐다 — `pov_attempt_top_n`이 양수면 `_pov_targets`가
    E0 Finding도 풀에 넣는다). 이 검사의 명제(줄번호가 모델에 닿는다)는 그대로다.
    """
    body = _body(_REAL_LOG)["body"]

    assert _RULE_ID in body
    assert _HIT_LINE in body


def test_the_findings_count_and_the_exit_code_survive() -> None:
    """하네스가 남기는 줄(발견 수·종료 코드·절 구분)은 접기 경로에서도 남는다.

    ⚠ **비회귀 핀 — 이 수정을 되돌려도 통과한다.** 그 줄들은 `stdout` 앞이라 head 안에 원래
    있다. 호출 자리를 빼든, `_drop_json_noise`를 입력 그대로 돌려주게 하든, `_JSON_NOISE_KEYS`를
    비우든 통과함을 되돌려 확인했다(PR #489 리뷰 반영). 막는 것은 이 수정이 그 줄을 **과하게**
    지우는 회귀이고 **결함의 해소는 증명하지 않는다** — 그것은
    `test_the_model_sees_which_rule_hit_which_line`이 핀한다.
    """
    body = _body(_REAL_LOG)["body"]

    assert "# semgrep: 10 findings" in body
    assert "(exit=1)" in body
    assert "--- stdout ---" in body


# --- 뺀 사실을 숨기지 않는다 (§0) -------------------------------------------------


def test_the_dropped_size_is_recorded_in_place() -> None:
    """뺀 자리에 **얼마를 뺐는지**가 남는다 - 조용히 사라지면 「없었다」와 구별되지 않는다.

    `truncated` 플래그는 절단을 말하지 이 제거를 말하지 않는다. semgrep이 파싱에 실패한
    사실 자체가 「10건 찾았다」의 신뢰도에 걸리는 정보라(그 이슈의 부수 발견) 크기는 남긴다.
    ⚠ 그 크기는 **재직렬화 길이**다 - 원문 글자 수가 아니다(바로 아래 테스트).
    """
    dropped = _drop_json_noise(_REAL_STDOUT)

    assert '"errors": "<921 chars dropped>"' in dropped
    assert "Syntax error at line" not in dropped


def test_the_dropped_size_counts_the_reserialized_value_not_the_source() -> None:
    """`<N chars dropped>`의 N은 **원문 글자 수가 아니라 재직렬화 길이**다(PR #489 리뷰).

    같은 문서를 compact 구분자로 쓴 원문에서는 `errors` 값이 원문 기준 921자보다 짧은데도
    자리표시자는 921이다 - `json.dumps` 기본 구분자(`", "`·`": "`)로 다시 쓴 길이라서다.
    이 저장소의 실 semgrep 로그는 구분자가 기본값이라(아래 첫 단언 - 그 로그의 왕복은 바이트
    동일이다) 실측 포맷에서는 둘이 같다. 다른 도구의 포맷은 미실측이다.
    """
    assert json.dumps(json.loads(_REAL_STDOUT)) == _REAL_STDOUT
    compact = json.dumps(json.loads(_REAL_STDOUT), separators=(",", ":"))
    source_size = len(json.dumps(json.loads(compact)["errors"], separators=(",", ":")))

    assert source_size < 921
    assert '"errors": "<921 chars dropped>"' in _drop_json_noise(compact)


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

    ⚠ **비회귀 핀이 아니라 계약의 서술이다 — `str`은 불변이라 어떤 구현에서도 통과한다**(이
    수정을 되돌려도, 함수를 입력 그대로 돌려주게 해도 통과함을 되돌려 확인했다 — PR #489 리뷰
    반영). 지우지 않고 남기는 것은 계약을 적어 두기 위해서다.
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

    ⚠ **읽기 상한(`PAYLOAD_READ_CAP`)이 중간에서 자른 JSON 줄도 이 갈래다** - 그때는 이 수정의
    효과가 꺼진다(`test_a_log_cut_by_the_read_cap_loses_the_effect`).
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

    ⚠ **그 `paths` 51자는 단일 파일 run 한 건의 값이다.** `paths.scanned`는 스캔한 파일 수만큼
    자라므로 디렉터리를 대상으로 돌면 `results`를 다시 head 밖으로 밀 수 있다 — 아래
    `test_a_wide_paths_list_pushes_the_results_out_again`이 그 모양을 실행으로 보인다. 현재
    호출부는 파일 경로만 넘기지만(코드 읽기) **그 불변식은 코드가 보장하지 않는다**(D16.md가
    같은 경고를 적는다). 디렉터리 대상 실행의 실측은 그 항목의 2파일 디렉터리 한 건뿐이고
    **운영 규모 디렉터리 run에서 `paths`가 head를 얼마나 먹는지는 미실측**이다.
    """
    assert _JSON_NOISE_KEYS == ("errors",)


# --- 절단과 같이 돈다 -------------------------------------------------------------


def test_the_body_still_obeys_the_per_evidence_cap() -> None:
    """접은 뒤에도 상한은 800자이고 생략한 JSON 필드는 truncated=True로 알린다.

    범용 절단으로 되돌려도 통과하는 비회귀 핀이다. 접기 가시성은 열 히트 핀이 검증한다.
    """
    summary = _body(_REAL_LOG)

    assert len(summary["body"]) <= EVIDENCE_BODY_CHAR_CAP
    assert summary["truncated"] is True


# --- 범용 경로의 한계와 semgrep 접기의 대조 (PR #489 이후) ---------------------------
#
# paths 핀은 semgrep 접기를 끄면 빨개지고, 다른 도구의 범용 경로에는 기존 한계가 남는다.
# 읽기 상한 핀은 여전히 읽은 범위 안에 results가 없음을 검증한다. 접기는 잘린 JSON을
# 복구하지 않는다. 어댑터가 상한 앞에서 잡음을 걷는 선택지는 이 변경 밖이다.


def test_a_wide_paths_list_pushes_the_results_out_again() -> None:
    """범용 경로의 한계는 남지만 semgrep 접기에서는 paths가 히트를 밀지 않는다.

    실 로그 경로 모양의 40파일 크기 변형이다. 기존 부재 기대값은 접기가 해결한 한계라 뒤집는다.
    `_drop_json_noise` 계약은 그대로 두고 두 경로의 결과를 함께 검증한다.
    """
    wide = json.loads(_REAL_STDOUT)
    wide["paths"]["scanned"] = [f"/src/src-vul/file/src/f{n:02d}.c" for n in range(40)]
    log = _REAL_LOG.replace(_REAL_STDOUT, json.dumps(wide))
    body = _body(log)["body"]

    assert "chars dropped" in body  # errors는 빠졌다
    assert _RULE_ID in body
    assert _HIT_LINE in body
    assert "1 of 1 shown" in body
    assert _RULE_ID not in _body(log, source="asan")["body"]  # 범용 경로의 한계는 남는다


def test_the_documented_read_cap_value_matches_the_adapter() -> None:
    """서술이 베낀 `PAYLOAD_READ_CAP` 값 사본이 어댑터 상수와 같다.

    `prompt_loader.py`는 어댑터를 import할 수 없어(engineering-guide.md §3) 이 상한을 **값으로**
    적고(`_drop_json_noise` docstring의 200,000자), `docs/agent-contracts.md`도 같은 값을 쓴다 -
    거기서 「스팬 약 630개 초과」(계산값)가 나온다. 아래 읽기 상한 핀은 상수에서 크기를 맞추므로
    **상수가 바뀌어도 초록이다** - 이 단언이 바뀐 것을 잡는다. 빨개지면 위 두 곳의 200,000자와
    그 값에서 환산한 630·633~636·695~698을 같이 다시 계산한다.
    """
    assert PAYLOAD_READ_CAP == 200_000


def test_a_log_cut_by_the_read_cap_loses_the_effect(tmp_path: Path) -> None:
    """한계 핀 - 읽기 상한이 JSON 줄을 중간에서 자르면 `errors`가 다시 head를 먹는다.

    `PAYLOAD_READ_CAP`(글자)이 stdout 줄을 자르면 `json.loads`가 실패해 `_drop_json_noise`가 그
    줄을 손대지 않는다. 같은 문서를 안 자르고 주면 효과가 난다(대조) - 원인은 크기가 아니라
    **자름**이다. 실 `make_payload_reader`로 읽는다. 상한을 넘는 로그는 픽스처 `errors[0].spans`를
    그 첫 스팬의 반복으로 늘려 만들었다 - 실 run 한 건은 스팬 148개 · 60,964자였고 이 모양의 실
    run은 미실측이다.

    ⚠ **이 핀은 어댑터 쪽 수정에만 빨개진다**(위 구역 머리말). 마지막 단언은 `_drop_json_noise`를
    어떻게 고쳐도 참이다 - 읽은 200,000자 안에 `results`가 **없어서**(이 입력에서 `results`는 로그
    203,709자 지점) 파싱이 되살릴 글자가 없다. 파싱 쪽 수정이 돕는 구간은 이 핀 밖이다.
    """
    wide = json.loads(_REAL_STDOUT)
    span = wide["errors"][0]["spans"][0]
    wide["errors"][0]["spans"] = [span] * (PAYLOAD_READ_CAP // len(json.dumps(span)) + 1)
    log = _REAL_LOG.replace(_REAL_STDOUT, json.dumps(wide))
    target = tmp_path / "run" / "semgrep" / "ev_0002.log"
    target.parent.mkdir(parents=True)
    target.write_bytes(log.encode("utf-8"))

    read = make_payload_reader(tmp_path)("artifacts/run/semgrep/ev_0002.log")

    assert len(log) > PAYLOAD_READ_CAP
    assert read is not None
    assert len(read) == PAYLOAD_READ_CAP  # 잘렸다
    assert _RULE_ID in _body(log)["body"]  # 대조 - 안 자르면 효과가 난다
    assert _RULE_ID not in _body(read)["body"]  # 잘리면 꺼진다
