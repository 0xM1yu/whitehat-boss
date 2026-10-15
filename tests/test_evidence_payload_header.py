"""모델로 가는 근거 본문에 하네스의 실행 기록 머리줄이 실리지 않는다 — 이슈 #256 항목 3.

`ContainerToolRunner._execute`가 쓰는 도구 로그의 앞머리는 **도구가 낸 것이 아니라 하네스가
실행을 기록한 것**이다 — `# node argv: …`(노드의 자리표시자 argv) · `# runtime: <런타임>` ·
명령마다 `$ <런타임> … -v <호스트 절대경로>:/src:ro …`(실제로 돈 명령). `evidence_summaries`는
근거를 증거당 800자(head 2/3 + tail 1/3)로 자르므로 그 머리가 남으면 head를 먹어 semgrep의
`check_id`가 잘려 나가고, 명령줄의 호스트 절대경로(사용자명 · `--target`으로 준 대상 디렉터리
이름)가 모델 입력에 실린다. 여기서 지키는 것은 **그 머리줄만** 빠진다는 것이다 — 발견 수 ·
종료 코드 · 도구가 낸 줄은 남는다(도구가 낸 줄이라도 줄 머리가 머리줄 접두면 빠진다 — 받아들인
오탐이고 `prompt_loader`의 머리줄 정규식 주석이 한계를 든다).

위쪽 검사의 하네스 로그는 **실제 `ContainerToolRunner.run`이 쓴다**(`subprocess.run`만 대역) —
로그 포맷을 손으로 짓지 않는다. semgrep·clang-tidy·build의 종료 코드 · stdout · stderr는 로컬 실
run `run_20260813_0002`(gitignore라 저장소에 없다)의 `semgrep/ev_0002.log`·`clang-tidy/ev_0003.log`
·`build/ev_0004.log`에서 그대로 옮겼다. 문자열로 짓는 입력은 머리말 없는 문자열 가드
(`test_a_payload_without_a_harness_header_is_untouched`)와 파일 끝의 단위 핀(걷은 뒤 기준의
`truncated`·합 예산 · 줄 머리 · 끝줄 개행 · 남기는 하네스 줄)뿐이다 — 그 머리줄은 `_execute`가
쓰는 모양 그대로다.
"""

import json
import re
from pathlib import Path

import pytest

from src.adapters.container_tools import (
    DEFAULT_IMAGE,
    UNENFORCED,
    make_payload_reader,
    sif_path,
)
from src.interfaces import tool_runner
from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import evidence_summaries
from src.schemas import Role
from src.schemas.evidence import Evidence
from tests.test_container_tools import ARVO, SANDBOX, _runner, _Spy, spy  # noqa: F401
from tests.test_nodes import VULN_TARGET, _triage, ctx  # noqa: F401

# 위 실 run의 semgrep이 낸 규칙 이름 — 모델이 무엇이 걸렸는지 알 수 있는 가장 짧은 문자열이다.
_RULE_ID = "opt.semgrep-rules.harness.harness-memcpy-nonconstant-length"

_REAL_SEMGREP_STDOUT = (
    '{"errors": [], "interfile_languages_used": [], "paths": {"sc'
    'anned": ["/src/parser.c"]}, "results": [{"check_id": "opt.se'
    'mgrep-rules.harness.harness-memcpy-nonconstant-length", "end'
    '": {"col": 26, "line": 24, "offset": 660}, "extra": {"engine'
    '_kind": "OSS", "fingerprint": "2f5f28f19181ea453426012790869'
    '84c2b0b8a35fddc67c0606df74f050ba14de703ca27bdb57ae10f8975577'
    '205ba82e60241963f53c113318938c9c250bce6_0", "is_ignored": fa'
    'lse, "lines": "    memcpy(buf, src, len);", "message": "\\uae'
    '38\\uc774\\uac00 \\uc0c1\\uc218\\uac00 \\uc544\\ub2cc memcpy \\u2014'
    ' \\ub300\\uc0c1 \\ubc84\\ud37c \\ud06c\\uae30\\uc640 \\ube44\\uad50\\u'
    'b418\\uc9c0 \\uc54a\\uc558\\uc744 \\uc218 \\uc788\\ub2e4", "metadat'
    'a": {}, "metavars": {"$DST": {"abstract_content": "buf", "en'
    'd": {"col": 15, "line": 24, "offset": 649}, "start": {"col":'
    ' 12, "line": 24, "offset": 646}}, "$LEN": {"abstract_content'
    '": "len", "end": {"col": 25, "line": 24, "offset": 659}, "st'
    'art": {"col": 22, "line": 24, "offset": 656}}, "$SRC": {"abs'
    'tract_content": "src", "end": {"col": 20, "line": 24, "offse'
    't": 654}, "start": {"col": 17, "line": 24, "offset": 651}}},'
    ' "severity": "WARNING", "validation_state": "NO_VALIDATOR"},'
    ' "path": "/src/parser.c", "start": {"col": 5, "line": 24, "o'
    'ffset": 639}}], "skipped_rules": [], "version": "1.86.0"}\n'
)

_REAL_CLANG_TIDY_STDOUT = (
    "/src/parser.c:24:5: error: Call to function 'memcpy' is inse"
    'cure as it does not provide security checks introduced in th'
    'e C11 standard. Replace with analogous functions that suppor'
    "t length arguments or provides boundary checks such as 'memc"
    "py_s' in case of C11 [clang-analyzer-security.insecureAPI.De"
    'precatedOrUnsafeBufferHandling,-warnings-as-errors]\n    memc'
    'py(buf, src, len);\n    ^~~~~~\n/src/parser.c:24:5: note: Call'
    " to function 'memcpy' is insecure as it does not provide sec"
    'urity checks introduced in the C11 standard. Replace with an'
    'alogous functions that support length arguments or provides '
    "boundary checks such as 'memcpy_s' in case of C11\n    memcpy"
    '(buf, src, len);\n    ^~~~~~\n'
)

_REAL_CLANG_TIDY_STDERR = "1 warning generated.\n"

# 인덱서가 stdout에 남기는 한 줄(`tools/harness-tools/indexer.py`의 `main`이 찍는 포맷).
# 수치는 지어냈다.
_INDEX_LINE = (
    "# index: 412 symbols, 1698 calls, 12 unresolved calls, 3 unresolved symbols, 140 files"
)

# 러너가 Auditor에 넘기는 `(graph, *found)`(`runner._artifact_signals`)와 같은 도구 · 순서 ·
# 노드 argv(`nodes/index.py` · `nodes/signals.py`). 종료 코드와 출력은 모듈 docstring의 실 run
# 그대로이고 index의 출력만 지어냈다(위 `_INDEX_LINE`).
_TOOL_CALLS = (
    ("index", ["index", "--callgraph", "."], ".", 0, _INDEX_LINE + "\n", ""),
    (
        "semgrep",
        ["semgrep", "--quiet", "src/mod_00.c"],
        "src/mod_00.c",
        1,
        _REAL_SEMGREP_STDOUT,
        "",
    ),
    (
        "clang-tidy",
        ["clang-tidy", "--quiet", "src/mod_00.c"],
        "src/mod_00.c",
        1,
        _REAL_CLANG_TIDY_STDOUT,
        _REAL_CLANG_TIDY_STDERR,
    ),
    ("build", ["build", "--quiet", "src/mod_00.c"], "src/mod_00.c", 0, "", ""),
)

# 대상 루트 — ATOM 평가 표본의 호스트 경로 모양이다
# (`docs/experiments/harness/2026-09-26-index-v2-atom.md` §7 — `sample_24`, `.c` 86 · `.h` 54).
# 명령줄의 바인드 인자가 이 경로를 그대로 싣는다.
_SAMPLE_ROOT = (
    "srv", "tenants", "ext01", "release-eval-slices-20260918", "samples", "sample_24", "repo"
)


def _sample_tree(tmp_path: Path, files: int) -> Path:
    root = tmp_path.joinpath(*_SAMPLE_ROOT)
    (root / "src").mkdir(parents=True)
    for number in range(files):
        (root / "src" / f"mod_{number:02d}.c").write_text("int f(void) { return 0; }\n", "utf-8")
    return root


def _real_evidences(tmp_path: Path, recorder: _Spy) -> tuple[Evidence, ...]:
    """실제 `ContainerToolRunner`로 네 도구의 로그를 쓴다.

    머리줄의 길이는 소스 수가 아니라 바인드 인자의 호스트 경로(`_SAMPLE_ROOT`)가 정한다 — 인덱서
    명령줄은 이제 소스 이름을 싣지 않고 목록 파일 경로만 든다(`INDEX_LIST_FILE`). 소스 40개는 표본
    트리를 실제 모양으로 두기 위한 것이다.
    """
    runner = _runner(tmp_path, root=_sample_tree(tmp_path, 40))
    evidences = []
    for tool, argv, workdir, code, stdout, stderr in _TOOL_CALLS:
        recorder.returncode, recorder.stdout, recorder.stderr = code, stdout, stderr
        evidences.append(runner.run(tool, ToolArgs(argv=argv, workdir=workdir), SANDBOX))
    assert all(evidence.ok for evidence in evidences), "도구 하나가 ok=False라 이 검사가 무의미하다"
    return tuple(evidences)


def _body_of(evidences: tuple[Evidence, ...], tool: str, tmp_path: Path) -> str:
    bodies = evidence_summaries(evidences, make_payload_reader(tmp_path))
    return str(bodies[next(ev for ev in evidences if ev.source == tool).evidence_id]["body"])


def _host_paths_in(text: str, tmp_path: Path) -> list[str]:
    """`text`에 실린 호스트 경로. 바인드 인자는 `as_posix()`, singularity의 SIF 경로는 `str()`
    모양으로 실린다(`container_argv`) — POSIX에서는 둘이 같고 Windows에서만 갈린다."""
    resolved = tmp_path.resolve()
    return [form for form in sorted({resolved.as_posix(), str(resolved)}) if form in text]


def test_a_semgrep_body_keeps_its_rule_id_within_the_per_evidence_cap(
    tmp_path: Path, spy: _Spy  # noqa: F811
) -> None:
    """증거당 상한(800자) 안에 semgrep의 규칙 이름이 든다 — 머리줄이 head를 먹지 않는다."""
    evidences = _real_evidences(tmp_path, spy)

    body = _body_of(evidences, "semgrep", tmp_path)

    assert _RULE_ID in body, f"semgrep 규칙 이름이 근거 본문에 없다. 본문 앞 200자: {body[:200]!r}"


def test_a_semgrep_body_keeps_the_summary_line_and_the_exit_code(
    tmp_path: Path, spy: _Spy  # noqa: F811
) -> None:
    """하네스가 쓴 줄 중 발견 수(`# semgrep: N findings`)와 종료 코드(`(exit=N)`)는 남는다.

    둘 다 도구 결과를 말하는 줄이다. 머리를 통째로(첫 `--- stdout ---`까지) 걷거나 `(exit=`까지
    걷는 수정은 여기서 빨개진다.
    """
    evidences = _real_evidences(tmp_path, spy)

    body = _body_of(evidences, "semgrep", tmp_path)

    assert "# semgrep: 1 findings" in body
    assert "(exit=1)" in body


@pytest.mark.parametrize("tool", ["index", "semgrep", "clang-tidy", "build"])
def test_no_evidence_body_carries_the_harness_header_or_the_host_path(
    tmp_path: Path, spy: _Spy, tool: str  # noqa: F811
) -> None:
    """어느 도구의 본문에도 `# node argv:` · `# runtime:` · `$ docker run …` 명령줄 · 호스트
    경로가 없다.

    호스트 경로에는 사용자명과 `--target`으로 준 대상 디렉터리 이름(평가 표본 이름)이 든다.
    """
    evidences = _real_evidences(tmp_path, spy)

    body = _body_of(evidences, tool, tmp_path)

    for header in ("# node argv:", "# runtime:", "docker run"):
        assert header not in body, f"{tool} 본문에 하네스 머리줄이 실렸다: {header!r}"
    assert _host_paths_in(body, tmp_path) == [], f"{tool} 본문에 호스트 경로가 실렸다"


@pytest.mark.parametrize("runtime", sorted(UNENFORCED))
def test_the_host_path_is_dropped_whichever_runtime_wrote_the_log(
    tmp_path: Path, spy: _Spy, runtime: str  # noqa: F811
) -> None:
    """명령줄의 첫 낱말은 런타임 이름이다(`container_argv`). 어느 런타임이 썼든 호스트 경로가
    빠진다.

    런타임 목록은 `UNENFORCED`(생성자가 그 키로 런타임을 거른다)에서 가져온다. 걷는 쪽은
    오케스트레이터라 그 목록을 import하지 못하고 이름을 따로 든다(`prompt_loader`의 머리줄 정규식) —
    런타임이 늘면 이 검사가 그 런타임에서 빨개진다(놓친 명령줄은 호스트 경로를 남긴다).
    """
    root = _sample_tree(tmp_path, 1)
    sif_dir = tmp_path / "sif"
    sif_dir.mkdir()
    sif_path(sif_dir, DEFAULT_IMAGE).write_bytes(b"")  # singularity는 SIF가 있어야 명령을 띄운다
    spy.returncode, spy.stdout, spy.stderr = 1, _REAL_SEMGREP_STDOUT, ""
    runner = _runner(tmp_path, runtime=runtime, root=root, sif_dir=sif_dir)
    evidence = runner.run(
        "semgrep",
        ToolArgs(argv=["semgrep", "--quiet", "src/mod_00.c"], workdir="src/mod_00.c"),
        SANDBOX,
    )
    assert evidence.ok, "semgrep이 ok=False라 이 검사가 무의미하다"

    body = _body_of((evidence,), "semgrep", tmp_path)

    assert _host_paths_in(body, tmp_path) == [], f"{runtime}: 호스트 경로가 본문에 실렸다"
    assert _RULE_ID in body


def test_the_auditor_v3_prompt_carries_the_rule_id_but_no_host_path(
    ctx, spy: _Spy, tmp_path: Path  # noqa: ANN001, F811
) -> None:
    """노드를 거친 Auditor v3 프롬프트(FACTS `evidence[].body`)에서도 같다.

    `evidence_summaries` 단위가 아니라 **모델에 실제로 가는 문자열**을 본다. registry가 Auditor에
    v3를 고르는 구성은 `test_nodes`의
    `test_auditor_content_aware_version_renders_slice_source_and_evidence_body_through_the_node`
    와 같다.
    """
    fake_v3 = ctx.config.models["fake"].transition(prompt_template_version="v3")
    ctx.config = ctx.config.transition(
        models={**ctx.config.models, "fake_v3": fake_v3},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v3"}
        ),
    )
    ctx.read_payload = make_payload_reader(tmp_path)
    evidences = _real_evidences(tmp_path, spy)
    candidate, capsule, _ = _triage(ctx, VULN_TARGET)

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    prompt = ctx.models.last_for(Role.AUDITOR)
    assert prompt.prompt_version == "v3"
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", prompt.body, re.DOTALL)[-1])
    bodies = [ev["body"] for ev in facts["evidence"] if "body" in ev]
    assert len(bodies) == len(evidences), "근거 넷이 모두 본문을 받아야 이 검사가 의미가 있다"
    joined = "\n".join(bodies)
    assert _RULE_ID in joined
    assert "docker run" not in joined
    assert _host_paths_in(joined, tmp_path) == []
    assert tmp_path.resolve().as_posix() not in prompt.body


def test_the_index_summary_line_is_not_a_header(tmp_path: Path, spy: _Spy) -> None:  # noqa: F811
    """인덱서가 stdout에 남기는 `# index: …` 한 줄은 도구가 낸 것이라 남는다.

    `# `로 시작하는 줄을 통째로 걷는 수정은 그래프 통계(심볼 · 호출 · 미해석 수)를 지운다. 걷는 것은
    하네스가 쓴 `# node argv: `·`# runtime: `뿐이다.
    """
    evidences = _real_evidences(tmp_path, spy)

    body = _body_of(evidences, "index", tmp_path)

    assert _INDEX_LINE in body


def test_a_tool_output_line_that_starts_with_a_dollar_sign_is_kept(
    tmp_path: Path, spy: _Spy  # noqa: F811
) -> None:
    """도구 출력에서 `$ `로 시작하는 줄이라도 런타임 이름이 이어지지 않으면 머리줄이 아니다.

    하네스의 명령줄은 늘 `$ <런타임> `으로 시작한다(`container_argv`). `$ ` 접두를 통째로 걷는
    수정은 도구 출력을 지운다. `poc`는 명령이 대상 프로그램 하나라(`plan`) stdout이 곧 그
    프로그램이 찍은 것이다. ⚠ 도구 출력의 줄 머리가 `$ docker ` 같은 런타임 이름이면 그 줄은
    걷힌다 — 받아들인 오탐이다(`prompt_loader`의 머리줄 정규식 주석).

    ⏵ **2026-10-09 (ADR 0014)** — `poc`에 과제 이미지와 입력을 물려 준다. 안 물리면 그 도구가
    **계획 단계에서 거절**해(폴백을 지어내지 않는다) 로그에 도구 출력이 한 줄도 없고 이 검사가
    재려던 것이 사라진다. 입력을 싣는 것도 같은 이유다 — `needs_input`인 명령에 경로가 없으면
    `container_argv`가 거절한다.
    """
    spy.returncode, spy.stdout, spy.stderr = 0, "$ echo hello\nhello\n", ""
    runner = _runner(tmp_path, root=_sample_tree(tmp_path, 1), poc_target=ARVO)
    evidence = runner.run(
        "poc",
        ToolArgs(
            argv=["poc", tool_runner.INPUT_PLACEHOLDER],
            workdir="src/mod_00.c",
            input_bytes=b"AAAA",
        ),
        SANDBOX,
    )

    body = _body_of((evidence,), "poc", tmp_path)

    assert "$ echo hello\nhello" in body


def test_a_payload_without_a_harness_header_is_untouched() -> None:
    """머리말이 없는 본문(단위 테스트의 문자열 · 다른 모양의 payload)은 글자 그대로 나간다."""
    text = "SANITIZER heap-buffer-overflow at parse_header"
    evidence = Evidence.model_construct(evidence_id="evidence_0001", payload_ref="r1")

    out = evidence_summaries([evidence], lambda _ref: text)

    assert out["evidence_0001"] == {"body": text, "truncated": False}


# --- 걷은 뒤 기준 — `evidence_summaries` 단위 핀 -----------------------------------------
#
# 머리 세 줄은 `_execute`가 쓰는 모양 그대로이고 명령줄 길이만 늘렸다 — 원문은 증거당 상한
# (800자)을 넘고 걷은 뒤에는 안 넘게 해서 「걷기 전 길이」와 「걷은 뒤 길이」가 갈리게 한다.
_LONG_HEADER = (
    "# node argv: index --callgraph .\n# runtime: docker\n$ docker run " + "x" * 970 + "\n"
)


def _bare(number: int) -> Evidence:
    return Evidence.model_construct(evidence_id=f"evidence_{number:04d}", payload_ref=f"r{number}")


def _summary_of(text: str) -> dict[str, object]:
    return evidence_summaries([_bare(1)], lambda _ref: text)["evidence_0001"]


def test_truncated_and_the_shared_budget_are_measured_after_the_header_strip() -> None:
    """`truncated`와 합 예산은 **걷은 뒤** 길이로 잰다.

    원문이 증거당 상한을 넘어도 걷은 뒤 길이가 상한 안이면 `truncated`는 False이고, 합 예산에는
    걷은 뒤 길이만 청구된다. `truncated`를 원문 길이로 판정하거나 `used`를 걷기 전 길이로
    청구하는 수정은 여기서 빨개진다.
    """
    raws = {"r1": _LONG_HEADER + "c" * 300, "r2": _LONG_HEADER + "d" * 900}

    out = evidence_summaries(
        [_bare(1), _bare(2)], raws.__getitem__, per_cap=800, total_cap=1000
    )

    assert out["evidence_0001"] == {"body": "c" * 300, "truncated": False}
    assert len(str(out["evidence_0002"]["body"])) == 700  # 합 1,000 − 첫 증거 300
    assert out["evidence_0002"]["truncated"] is True


def test_only_a_line_that_starts_with_a_header_prefix_is_dropped() -> None:
    """줄 머리가 아니면(줄 가운데 · 들여쓴 줄 · 한 줄 JSON 안) 머리줄 모양이어도 남는다."""
    text = 'x# runtime: y  $ docker run z\n  $ docker run indented\n{"m": "# node argv: k"}'

    assert _summary_of(text)["body"] == text


def test_a_command_word_that_only_begins_with_a_runtime_name_is_kept() -> None:
    """`$ ` 뒤 낱말이 런타임 이름으로 **시작만** 하면 머리줄이 아니다 — 이름 뒤 공백까지 본다."""
    assert _summary_of("$ dockerfile-lint a\n")["body"] == "$ dockerfile-lint a"


@pytest.mark.parametrize(
    "line",
    [
        "--- stdout ---",
        "--- stderr ---",
        "(timeout after 300s)",
        "(FileNotFoundError: [Errno 2] No such file or directory: 'docker')",
        "# 실행하지 않았다: x",
        "# 실행되지 않은 명령 1개 — 앞 명령이 non-zero로 끝나 break됨",
    ],
)
def test_the_harness_lines_outside_the_three_headers_are_kept(line: str) -> None:
    """머리줄 셋 밖의 하네스 줄(절 구분 · timeout · 실행 예외 · 실행 전 거절 사유 · 미실행
    명령 수)은 남는다. 발견 수와 `(exit=N)`은 위 semgrep 검사가 본다."""
    body = str(_summary_of(f"{_LONG_HEADER}(exit=1)\n{line}\ntool output\n")["body"])

    assert line in body


def test_a_header_line_without_a_final_newline_is_dropped_too() -> None:
    """읽기 상한이 로그를 머리줄 한가운데서 자르면 개행 없는 끝줄이 남는다 — 그 줄도 걷는다."""
    assert _summary_of("keep\n$ docker run " + "y" * 50) == {"body": "keep", "truncated": False}
