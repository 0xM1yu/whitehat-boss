"""1단계 인덱서 (`tools/harness-tools/indexer.py`).

이 스크립트는 이미지 안에서 살고 하네스는 호스트에서 산다 — 둘은 `--out`이 쓰는 JSON
하나로만 만난다. `src/`를 import 하지 않으므로 형식이 양쪽에 따로 존재하고, 어긋나면
캡슐이 조용히 빈다. 그 어긋남을 여기서 막는다.

**tree-sitter를 `requirements.txt`에 넣은 것은 이 테스트를 실제로 돌리기 위해서다.**
하네스 런타임은 이 패키지를 쓰지 않는다(인덱서는 컨테이너 안에서 돈다). `importorskip`으로
두면 CI에서 조용히 건너뛰고, 그러면 PR #7이 겪은 것과 같은 상태가 된다 — 초록불인데
아무것도 검증하지 않는 상태.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.adapters import index_graph
from src.adapters.container_tools import _INDEX_SUFFIXES, INDEX_FILE, plan

_INDEXER_PATH = Path(__file__).resolve().parents[1] / "tools" / "harness-tools" / "indexer.py"


def _load_indexer():
    """저장소 경로에서 직접 불러온다 — `tools/`는 패키지가 아니다."""
    spec = importlib.util.spec_from_file_location("harness_indexer", _INDEXER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


indexer = _load_indexer()


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    """호출 사슬이 있는 C 파일. `recv → parse → memcpy`가 캡슐이 필요로 하는 형태다."""
    (tmp_path / "parser.c").write_text(
        "#include <string.h>\n"
        "\n"
        "void handle_header(struct header *hdr);\n"   # 선언만 — 심볼이 아니다
        "\n"
        "static int helper(const char *s) {\n"
        "    return strlen(s);\n"
        "}\n"
        "\n"
        "void parse_header(const char *src, int len) {\n"
        "    char buf[64];\n"
        "    memcpy(buf, src, len);\n"
        "    helper(buf);\n"
        "}\n"
        "\n"
        "void recv_request(const char *packet) {\n"
        "    parse_header(packet, 128);\n"
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


def _index(root: Path, *names: str) -> dict:
    return indexer.build_index([root / name for name in names], root)


# --- 뽑아내는 것 ---------------------------------------------------------------


def test_definitions_become_symbols_but_declarations_do_not(sample: Path) -> None:
    """선언을 심볼로 세면 "그 함수가 여기 있다"가 거짓이 된다."""
    index = _index(sample, "parser.c")
    names = [s["name"] for s in index["symbols"]]

    assert names == ["helper", "parse_header", "recv_request"]
    assert "handle_header" not in names


def test_symbol_carries_the_line_range(sample: Path) -> None:
    """캡슐이 원문을 잘라 담으려면 범위가 있어야 한다."""
    index = _index(sample, "parser.c")
    parse = next(s for s in index["symbols"] if s["name"] == "parse_header")

    assert parse["file"] == "parser.c"
    assert parse["line_start"] < parse["line_end"]


def test_call_graph_links_caller_to_callee(sample: Path) -> None:
    """`callers`/`callees` 섹션이 이 간선에서 나온다(D13)."""
    index = _index(sample, "parser.c")
    edges = {(c["caller"], c["callee"]) for c in index["calls"]}

    assert ("recv_request", "parse_header") in edges
    assert ("parse_header", "memcpy") in edges
    assert ("parse_header", "helper") in edges


# --- 최상위에 없는 정의 ---------------------------------------------------------
#
# 이 절이 없어서 첫 구현이 `root_node.children`(최상위 한 겹)만 보고도 초록불이었다.
# 픽스처의 함수가 전부 최상위 평면이었기 때문이다 — 실측하니 zlib(.c)에서 28.3%,
# nlohmann/json(.cpp)에서 70.3%가 그렇게 사라지고 있었다.


def test_functions_inside_ifdef_are_found(tmp_path: Path) -> None:
    """`#ifdef`로 감싼 함수는 `preproc_ifdef` 아래 있다 — 최상위가 아니다.

    zlib에서 사라진 138개 중 57개가 이 경우였다.
    """
    (tmp_path / "guard.c").write_text(
        "#ifdef CONFIG_FEATURE\n"
        "void guarded(void) { helper(); }\n"
        "#endif\n"
        "void plain(void) { guarded(); }\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "guard.c")

    assert [s["name"] for s in index["symbols"]] == ["guarded", "plain"]
    assert ("guarded", "helper") in {(c["caller"], c["callee"]) for c in index["calls"]}


def test_both_branches_of_a_conditional_are_indexed(tmp_path: Path) -> None:
    """구문만 보는 인덱서는 어느 가지가 컴파일되는지 모른다.

    한쪽을 고르면 그 선택이 근거 없는 추측이다. 둘 다 낸다 — 같은 이름이 두 번
    나오는 것이 사실이고, 읽는 쪽이 line으로 구별한다.
    """
    (tmp_path / "plat.c").write_text(
        "#if defined(LINUX)\n"
        "void plat_init(void) { linux_setup(); }\n"
        "#else\n"
        "void plat_init(void) { other_setup(); }\n"
        "#endif\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "plat.c")

    assert [s["name"] for s in index["symbols"]] == ["plat_init", "plat_init"]
    assert {c["callee"] for c in index["calls"]} == {"linux_setup", "other_setup"}


def test_parse_error_does_not_suppress_the_symbols_in_that_file(tmp_path: Path) -> None:
    """전처리되지 않은 매크로가 끼면 tree-sitter가 그 파일을 `has_error`로 표시한다.

    `int ZEXPORT deflate(...)`·`charf *buf` 같은 형태가 그렇고, zlib에서는 43개 중
    39개(91%)가 이렇게 표시된다 — 평범한 C에서 예외가 아니라 기본값이다. 그러니
    `parse_errors`에 올랐다는 이유로 그 파일의 심볼을 버리면 안 된다.

    **이 테스트가 덮지 못하는 것**: 매크로가 정의를 `ERROR` 노드 **안으로** 밀어 넣는
    경우(zlib에서 66건). 그건 앞 문맥이 100줄 넘게 쌓여야 재현되므로 최소 예제로
    고정할 수 없다. 그쪽은 `_iter_definitions`가 트리 전체를 훑어 잡고, 근거는 실측이다
    (zlib .c 손실 28.3% → 0.4%).
    """
    (tmp_path / "zexport.c").write_text(
        "int ZEXPORT deflate(int level) {\n"
        "    return compress2(level);\n"
        "}\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "zexport.c")

    assert index["parse_errors"] == ["zexport.c"]
    assert [s["name"] for s in index["symbols"]] == ["deflate"]
    assert ("deflate", "compress2") in {(c["caller"], c["callee"]) for c in index["calls"]}


def test_cpp_class_namespace_and_extern_c_are_indexed(tmp_path: Path) -> None:
    """`_INDEX_SUFFIXES`에 `.cc`/`.cpp`가 있으므로 C++ 파일은 실제로 넘어온다.

    클래스 본문(`field_declaration_list`)·namespace(`declaration_list`)·
    `extern "C"`(`linkage_specification`) 전부 최상위가 아니다. 첫 구현은 이 파일에서
    심볼 0개·호출 0개를 내면서 exit 0이었다 — 그러면 `Evidence(ok=True)`가 남고
    E2가 빈 그래프 위에서 열린다.
    """
    (tmp_path / "net.cpp").write_text(
        "namespace net {\n"
        "class Parser {\n"
        "public:\n"
        "    void inline_method(void) { memcpy(a, b, c); }\n"
        "};\n"
        "}\n"
        'extern "C" {\n'
        "void c_api(void) { strcpy(a, b); }\n"
        "}\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "net.cpp")

    assert {s["name"] for s in index["symbols"]} == {"inline_method", "c_api"}
    assert {c["callee"] for c in index["calls"]} == {"memcpy", "strcpy"}


def test_cpp_qualified_names_are_kept_whole(tmp_path: Path) -> None:
    """`Parser::parse`를 `parse`로 줄이면 다른 클래스의 같은 이름과 구별되지 않는다.

    `identifier`만 이름으로 받으면 이 정의들이 전부 `None`이 되어 심볼과 호출 간선이
    함께 사라진다 — nlohmann/json에서 466건이 그 경로로 없어지고 있었다.
    """
    (tmp_path / "member.cpp").write_text(
        "void Parser::parse(const char *s) { memcpy(buf, s, 64); }\n"
        "Parser::Parser(void) { init(); }\n"
        "Parser::~Parser(void) { release(); }\n"
        "bool Parser::operator==(const Parser &o) { return cmp(o); }\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "member.cpp")

    assert [s["name"] for s in index["symbols"]] == [
        "Parser::parse",
        "Parser::Parser",
        "Parser::~Parser",
        "Parser::operator==",
    ]
    assert ("Parser::parse", "memcpy") in {(c["caller"], c["callee"]) for c in index["calls"]}


def test_a_definition_inside_a_body_is_not_a_separate_symbol(tmp_path: Path) -> None:
    """찾은 정의 안으로는 들어가지 않는다.

    중첩 정의까지 심볼로 올리면 그 본문의 호출이 바깥 함수 것으로도, 안쪽 함수 것으로도
    세어져 같은 간선이 두 번 잡힌다.
    """
    (tmp_path / "nested.c").write_text(
        "void outer(void) {\n"
        "    void inner(void) { target(); }\n"
        "    inner();\n"
        "}\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "nested.c")

    assert [s["name"] for s in index["symbols"]] == ["outer"]
    assert len([c for c in index["calls"] if c["callee"] == "target"]) == 1


# --- 못 잡는 것을 드러내는가 ----------------------------------------------------


def test_indirect_calls_are_counted_not_invented(tmp_path: Path) -> None:
    """함수 포인터 호출은 구문만으로 대상을 알 수 없다.

    이름을 지어내면 없는 간선이 생기고, 조용히 버리면 그래프가 완전한 것처럼 보인다.
    센다.
    """
    (tmp_path / "fp.c").write_text(
        "void run(void (*fp)(int)) {\n"
        "    (*fp)(1);\n"
        "    fp(2);\n"
        "}\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "fp.c")

    assert index["unresolved_calls"] >= 1
    # `fp(2)`는 identifier라 간선이 생기지만 그 대상은 매개변수다. 지금 구현의 한계이며
    # 없는 함수를 가리키는 간선이 나올 수 있다 — 읽는 쪽이 심볼 표와 대조해야 한다.
    assert all(c["caller"] == "run" for c in index["calls"])


def test_unnamed_definitions_are_counted_not_silently_dropped(tmp_path: Path) -> None:
    """정의는 찾았는데 이름을 못 뽑는 경우가 실제로 남아 있다.

    `unresolved_calls`가 호출부의 누락을 세듯 이쪽은 심볼의 누락을 센다. 세지 않으면
    "이 저장소에는 원래 함수가 그것뿐이다"와 구별되지 않는다.
    """
    (tmp_path / "ok.c").write_text("void a(void) { b(); }\n", encoding="utf-8")
    assert _index(tmp_path, "ok.c")["unresolved_symbols"] == 0

    # 정의 하나가 있는 파일. 심볼로 올라가든 이름을 못 뽑든, **둘 중 하나로는 세어져야**
    # 한다. 조용히 0/0이 되는 경우가 없다는 것이 이 계약이다.
    (tmp_path / "gnu.c").write_text(
        "__attribute__((noinline)) void tagged(void) { work(); }\n", encoding="utf-8"
    )
    gnu = _index(tmp_path, "gnu.c")
    assert len(gnu["symbols"]) + gnu["unresolved_symbols"] >= 1


def test_broken_source_is_recorded_not_fatal(tmp_path: Path) -> None:
    """부분 파싱 결과도 쓸모 있다. 깨졌다는 사실만 남기고 계속한다."""
    (tmp_path / "bad.c").write_text("void a(void) { if (", encoding="utf-8")
    index = _index(tmp_path, "bad.c")

    assert index["parse_errors"] == ["bad.c"]


def test_unparsable_suffix_is_listed_as_skipped(tmp_path: Path) -> None:
    """조용히 빠지면 "그 파일에 함수가 없다"와 구별되지 않는다."""
    (tmp_path / "notes.txt").write_text("void a(void) {}", encoding="utf-8")
    index = _index(tmp_path, "notes.txt")

    assert index["skipped"] == ["notes.txt"]
    assert index["files"] == []


def test_include_fragment_is_indexed_not_skipped(tmp_path: Path) -> None:
    """실측 샘플의 구조: `target.c`는 `#include` 한 줄이고 코드는 전부 `.inc`에 있다.

    tree-sitter는 **전처리를 하지 않는다.** `clang -fsyntax-only`를 쓰던 시절에는
    전처리가 `#include`를 펼쳐 `.inc`를 끌어왔지만 지금은 직접 주지 않으면 28바이트만
    보고 17KB를 못 본다 — 그런데 예외가 아니라 `skipped`라 "함수가 없다"와 구별되지
    않는다. 그래서 `skipped`가 비었는지와 `.inc` 쪽 심볼·호출을 함께 본다.
    """
    (tmp_path / "target.c").write_text('#include "session_core.inc"\n', encoding="utf-8")
    (tmp_path / "session_core.inc").write_text(
        "static int helper(const char *s) {\n"
        "    return strlen(s);\n"
        "}\n"
        "\n"
        "void session_read(const char *src, int len) {\n"
        "    char buf[64];\n"
        "    memcpy(buf, src, len);\n"
        "    helper(buf);\n"
        "}\n",
        encoding="utf-8",
    )

    index = _index(tmp_path, "target.c", "session_core.inc")

    assert index["skipped"] == []
    assert index["files"] == ["session_core.inc", "target.c"]
    assert [s["name"] for s in index["symbols"]] == ["helper", "session_read"]
    assert {(c["caller"], c["callee"]) for c in index["calls"]} == {
        ("helper", "strlen"),
        ("session_read", "memcpy"),
        ("session_read", "helper"),
    }


def test_every_suffix_the_harness_sends_has_a_grammar() -> None:
    """목록이 양쪽에 따로 있다 — 이 스크립트는 `src/`를 import 하지 않는다.

    어긋나면 예외가 아니라 `skipped`에 이름만 남아 조용히 빈 그래프가 나오고, 그
    그래프로 E2가 열린다. 하네스가 보내기로 한 확장자는 전부 파싱될 수 있어야 한다.
    """
    assert set(_INDEX_SUFFIXES) <= set(indexer._GRAMMARS)


def test_empty_source_is_not_an_error(tmp_path: Path) -> None:
    """헤더만 있는 대상이나 구조체 정의뿐인 파일이 정상적으로 심볼 0개다."""
    (tmp_path / "types.c").write_text("struct T { int x; };\n", encoding="utf-8")
    index = _index(tmp_path, "types.c")

    assert index["symbols"] == []
    assert index["parse_errors"] == []


# --- 재현성 --------------------------------------------------------------------


def test_two_runs_produce_identical_json(sample: Path, tmp_path: Path) -> None:
    """같은 입력에 다른 index가 나오면 캡슐이 달라져 재현성이 깨진다."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        assert indexer.main(["--out", str(out), "--root", str(sample),
                             str(sample / "parser.c")]) == 0

    assert first.read_bytes() == second.read_bytes()


def test_file_order_does_not_change_the_output(sample: Path) -> None:
    """호출부가 넘기는 순서가 결과를 바꾸면 안 된다."""
    (sample / "util.c").write_text("void other(void) { }\n", encoding="utf-8")

    forward = _index(sample, "parser.c", "util.c")
    reverse = _index(sample, "util.c", "parser.c")

    assert forward == reverse


def test_paths_are_relative_to_the_mount_root(sample: Path) -> None:
    """절대경로를 남기면 실행마다 값이 달라지고 컨테이너 밖 경로가 기록에 샌다."""
    index = _index(sample, "parser.c")

    assert all(not Path(s["file"]).is_absolute() for s in index["symbols"])
    assert index["files"] == ["parser.c"]


# --- 하네스와의 계약 ------------------------------------------------------------


def test_plan_calls_the_indexer_and_writes_into_scratch() -> None:
    """대상 마운트는 읽기 전용이므로 산출물은 scratch에만 떨어질 수 있다."""
    commands = plan("index", target="/src", compile_sources=["/src/parser.c"], cwd="/src")

    assert len(commands) == 1
    argv = commands[0].argv
    assert argv[0] == "python3"
    assert argv[argv.index("--out") + 1] == f"/work/{INDEX_FILE}"
    assert argv[argv.index("--root") + 1] == "/src"
    assert argv[-1] == "/src/parser.c"


def test_index_no_longer_shells_out_to_clang() -> None:
    """`clang -fsyntax-only`는 구문 검사일 뿐이고, include 경로가 필요한 실제 저장소에서
    실패해 GRAPH Evidence가 아예 생기지 않았다 — 그래서 모든 Finding이 E2를 못 넘었다."""
    argv = plan("index", target="/src", compile_sources=["/src/a.c"], cwd="/src")[0].argv

    assert "clang" not in argv
    assert "-fsyntax-only" not in argv


def test_version_is_stamped_so_readers_can_reject_unknown_formats() -> None:
    assert indexer.INDEX_VERSION == "index-v1"


def test_the_reader_knows_the_version_this_script_writes() -> None:
    """형식이 양쪽에 따로 있다 — 이 스크립트는 `src/`를 import 하지 않는다.

    어긋나면 예외가 아니라 `unsupported_version`이 되고 **그래프 섹션이 통째로
    사라진다.** 그 모양은 "인덱서가 안 돌았다"와 같아서 캡슐만 봐서는 구별되지 않는다.
    `_INDEX_SUFFIXES` ↔ `_GRAMMARS`와 같은 종류의 이중화이고 같은 방식으로 막는다.
    """
    assert index_graph.SUPPORTED_INDEX_VERSION == indexer.INDEX_VERSION


def test_script_runs_as_a_subprocess_and_exits_zero(sample: Path, tmp_path: Path) -> None:
    """컨테이너 안에서는 `python3 /opt/indexer.py`로 불린다. import 경로가 아니라
    실행 경로가 도는지 본다 — `judge()`가 exit 0만 ok로 본다."""
    out = tmp_path / INDEX_FILE
    result = subprocess.run(
        [sys.executable, str(_INDEXER_PATH), "--out", str(out),
         "--root", str(sample), str(sample / "parser.c")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "# index:" in result.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == indexer.INDEX_VERSION


def test_missing_source_exits_nonzero(tmp_path: Path) -> None:
    """인덱서 자신이 실패한 경우다. 0을 내면 빈 그래프가 성공으로 기록되고 E2가 열린다."""
    assert indexer.main(
        ["--out", str(tmp_path / "x.json"), "--root", str(tmp_path), str(tmp_path / "없다.c")]
    ) == 2
