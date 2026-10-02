"""`src/adapters/cybergym_oracle.py` — 로컬 CyberGym 오라클 바이트-in 클라이언트 (S6b).

전부 `urlopen`을 갈아 끼운다(`tests/test_openai_compat.py:509-528`과 같은 선례) — 실서버·
docker 없이 돈다. 계약 근거는 저자 소스 고정
`sunblaze-ucb/cybergym@c6fe2027d39471375920b92cf1025e23a99ffda5`.
"""

import hashlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.adapters import cybergym_oracle as oracle

_SERVER = "http://127.0.0.1:8666"
_TASK_ID = "arvo:1065"
_AGENT_ID = "agent-s6b-test"
_POC_BYTES = b"\x00\x01\xff\xfe binary poc payload \x00\x00"
_POC_ID = "a" * 32


def _http_error(
    code: int, detail: str, url: str = f"{_SERVER}/submit-fix"
) -> urllib.error.HTTPError:
    body = json.dumps({"detail": detail}).encode("utf-8")
    return urllib.error.HTTPError(url, code, detail, {}, io.BytesIO(body))


def _submit_response(exit_code: int = 0) -> io.BytesIO:
    payload = {"task_id": _TASK_ID, "exit_code": exit_code, "output": "", "poc_id": _POC_ID}
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


# --- 1. multipart 본문 -------------------------------------------------------


def test_multipart_body_has_boundary_metadata_and_exact_file_bytes() -> None:
    metadata_json = json.dumps(
        {"task_id": _TASK_ID, "agent_id": _AGENT_ID, "checksum": "x" * 64, "require_flag": False}
    )
    body, boundary = oracle._multipart_body(metadata_json, _POC_BYTES)

    marker = ("--" + boundary).encode("ascii")
    closing = marker + b"--\r\n"
    # 파트가 2개면 경계 줄은 3번 나온다 — 여는 경계 · 파트 사이 구분 경계 · 닫는 경계
    # (RFC 7578: N개 파트 = N+1개 `--boundary` 줄).
    assert body.count(marker) == 3
    assert body.startswith(marker)
    assert body.endswith(closing)
    assert b'name="metadata"' in body
    assert b'name="file"' in body
    assert metadata_json.encode("utf-8") in body
    # 바이트가 그대로 실린다 — 길이가 아니라 내용으로 본다(길이만 맞으면 통과하는 테스트는
    # truncate+pad 같은 결함을 못 잡는다).
    assert _POC_BYTES in body


def test_request_content_type_boundary_matches_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        captured["request"] = request
        return _submit_response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    oracle.submit_vul(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES)

    request = captured["request"]
    headers = {k.lower(): v for k, v in request.header_items()}
    content_type = headers["content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1]
    assert ("--" + boundary).encode("ascii") in request.data
    assert _POC_BYTES in request.data


# --- 2. checksum --------------------------------------------------------------


def test_checksum_matches_an_independently_derived_sha256() -> None:
    expected = hashlib.sha256(f"{_TASK_ID}{_AGENT_ID}CyberGym".encode()).hexdigest()
    assert oracle.checksum(_TASK_ID, _AGENT_ID) == expected
    assert len(expected) == 64

    other = hashlib.sha256(f"{_TASK_ID}{_AGENT_ID}other-salt".encode()).hexdigest()
    assert oracle.checksum(_TASK_ID, _AGENT_ID, salt="other-salt") == other
    assert other != expected


# --- 3. 예외 사슬 순서 ---------------------------------------------------------


def test_http_error_is_not_swallowed_by_the_url_error_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`HTTPError`가 `URLError`의 하위형이라, 순서를 바꾸면(`URLError`를 먼저 잡으면)
    상태 코드·서버 사유가 통째로 사라진다(openai_compat.py `_post` 주석 340-349와 같은 사고).
    """

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise _http_error(400, "Invalid checksum", url=f"{_SERVER}/submit-vul")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(oracle.CyberGymOracleError) as caught:
        oracle.submit_vul(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES)

    assert caught.value.status == 400
    assert "Invalid checksum" in caught.value.detail


def test_timeout_error_is_mapped_without_a_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(oracle.CyberGymOracleError) as caught:
        oracle.submit_vul(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES)

    assert caught.value.status is None


def test_connection_failure_falls_into_the_url_error_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(oracle.CyberGymOracleError) as caught:
        oracle.submit_vul(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES)

    assert caught.value.status is None


# --- 4. 404 → 키 오류(task not found 아님) ------------------------------------


def test_404_from_a_keyed_endpoint_is_reported_as_an_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """서버 `get_api_key`(저자 `__main__.py:112-119`)는 키 없음/오류를 401/403이 아니라
    `404 detail="Not found"`로 낸다(측정). "그 task가 없다"로 잘못 읽으면 안 된다.
    """

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise _http_error(404, "Not found", url=f"{_SERVER}/submit-fix")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(oracle.CyberGymAuthError) as caught:
        oracle.submit_fix(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES, api_key="wrong")

    assert "key" in str(caught.value).lower() or "키" in str(caught.value)


def test_404_with_a_different_detail_is_not_misread_as_an_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 404라도 본문이 다르면(레코드가 그냥 없다) 키 오류로 덮지 않는다 — `get_api_key`의
    리터럴 `"Not found"`와 **정확히** 같을 때만 키 오류로 읽는다.
    """

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise _http_error(404, "Record not found", url=f"{_SERVER}/query-poc")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(oracle.CyberGymOracleError) as caught:
        oracle.query_verdict(_SERVER, _AGENT_ID, _TASK_ID, api_key="fine")

    assert not isinstance(caught.value, oracle.CyberGymAuthError)


# --- 5. 판정은 query 경로에서 — submit 응답이 아니라 ---------------------------


def test_verdict_comes_from_the_query_route_not_the_submit_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 `_post_process_result`가 타임아웃(300)을 **응답에서만** 0으로 되쓴다(이슈 #384) —
    DB는 300을 그대로 든다. 제출 응답의 `exit_code`를 믿으면 타임아웃을 "안 터졌다"로
    오판한다. 진짜 값은 `/query-poc`(= `PoCRecord.to_dict()`)에서 읽는다.
    """

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        if request.full_url.endswith("/query-poc"):
            record = {
                "agent_id": _AGENT_ID,
                "task_id": _TASK_ID,
                "poc_id": _POC_ID,
                "poc_hash": "b" * 64,
                "poc_length": len(_POC_BYTES),
                "vul_exit_code": 300,
                "fix_exit_code": 0,
                "created_at": "2026-10-02T00:00:00Z",
                "updated_at": "2026-10-02T00:00:00Z",
            }
            return io.BytesIO(json.dumps([record]).encode("utf-8"))
        # submit-vul/submit-fix 응답은 거짓으로 "안 터졌다"(exit_code 0)고 주장한다.
        return _submit_response(exit_code=0)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    verdict = oracle.run_round_trip(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES, api_key="k")

    assert verdict.vul_exit_code == 300  # 응답이 주장한 0이 아니다
    assert verdict.fix_exit_code == 0


# --- 6. fix 이미지 없음 → 500 + 서버 사유 -------------------------------------


def test_missing_fix_image_surfaces_as_500_with_the_servers_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        if request.full_url.endswith("/submit-fix"):
            raise _http_error(
                500,
                'Running error: 404 Client Error: Not Found ("No such image: n132/arvo:1065-fix")',
                url=f"{_SERVER}/submit-fix",
            )
        return _submit_response(exit_code=77)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(oracle.CyberGymOracleError) as caught:
        oracle.run_round_trip(_SERVER, _TASK_ID, _AGENT_ID, _POC_BYTES, api_key="k")

    assert caught.value.status == 500
    assert "No such image" in caught.value.detail


# --- 7. 와일드카드 bind 전수 ---------------------------------------------------

_ORACLE_MODULE = Path(__file__).resolve().parents[1] / "src" / "adapters" / "cybergym_oracle.py"


def test_no_wildcard_bind_in_the_new_module_or_this_test_file() -> None:
    """🔴 all-interfaces 와일드카드 bind 주소는 서버를 공개 인터넷에 노출한다(S6b 가드 §⑫) —
    새 코드에 0건이어야 한다. (이 검사 자체가 그 리터럴을 말하면 자기 자신에서 걸린다 —
    그래서 이 docstring도 그 글자를 안 쓴다.)
    """
    wildcard = "0" + ".0" * 3
    assert wildcard not in _ORACLE_MODULE.read_text(encoding="utf-8")
    assert wildcard not in Path(__file__).read_text(encoding="utf-8")
