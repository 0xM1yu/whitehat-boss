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
from src.schemas.common import Location

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


def _edges(index: dict) -> list[dict]:
    """index-v2의 `calls`(callee → 호출 지점)를 v1의 간선 행으로 편다.

    아래 테스트들의 **기대값은 index-v1 시절 그대로다** — 형식만 callee 키로 묶였고 간선은
    한 줄도 안 바뀌었다는 것이 그 대조로 선다.
    """
    return [
        {"caller": site["caller"], "callee": callee, "file": site["file"], "line": site["line"]}
        for callee, sites in index["calls"].items()
        for site in sites
    ]


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
    edges = {(c["caller"], c["callee"]) for c in _edges(index)}

    assert ("recv_request", "parse_header") in edges
    assert ("parse_header", "memcpy") in edges
    assert ("parse_header", "helper") in edges


def test_a_static_inline_header_function_is_indexed_with_the_header_as_its_file(
    tmp_path: Path,
) -> None:
    """`.h`도 인덱서에 간다(#63, index-v2) — 헤더의 `static inline` 함수가 v1 `symbols`와 v2
    `functions` 표 양쪽에 `file`이 그 헤더로 들어와야 NE resolver의 조회와 Auditor 앵커
    (`runner._auditor_target`)가 그 정의를 찾는다. `.c`만 줄 때는 이 심볼이 없다는 대조로,
    들어오는 이유가 헤더를 넘겼기 때문임을 보인다."""
    (tmp_path / "main.c").write_text(
        '#include "util.h"\nint f(void) { return clamp(0, 9, 3); }\n', encoding="utf-8"
    )
    (tmp_path / "util.h").write_text(
        "static inline int clamp(int lo, int hi, int v) {\n"
        "    if (v < lo) return lo;\n"
        "    if (v > hi) return hi;\n"
        "    return v;\n"
        "}\n",
        encoding="utf-8",
    )

    c_only = _index(tmp_path, "main.c")
    with_header = _index(tmp_path, "main.c", "util.h")

    assert "clamp" not in [s["name"] for s in c_only["symbols"]]
    clamp = next(s for s in with_header["symbols"] if s["name"] == "clamp")
    assert clamp == {"name": "clamp", "file": "util.h", "line_start": 1, "line_end": 5}
    assert with_header["functions"]["clamp"] == [
        {"file": "util.h", "line_start": 1, "line_end": 5, "params": ["lo", "hi", "v"]}
    ]


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
    assert ("guarded", "helper") in {(c["caller"], c["callee"]) for c in _edges(index)}


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
    assert {c["callee"] for c in _edges(index)} == {"linux_setup", "other_setup"}


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
    assert ("deflate", "compress2") in {(c["caller"], c["callee"]) for c in _edges(index)}


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
    assert {c["callee"] for c in _edges(index)} == {"memcpy", "strcpy"}


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
    assert ("Parser::parse", "memcpy") in {(c["caller"], c["callee"]) for c in _edges(index)}


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
    assert len([c for c in _edges(index) if c["callee"] == "target"]) == 1


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
    assert all(c["caller"] == "run" for c in _edges(index))


def test_deeply_nested_code_does_not_hit_the_recursion_limit(tmp_path: Path) -> None:
    """긴 `else if` 사슬과 길게 이은 식은 트리가 수천 단으로 깊다 — 노드마다 재귀하던 호출 수집이
    `RecursionError`로 인덱스 전체를 잃었다(2026-10-04 GT 직접 모드 FT 데이터 생성 6,745표본에서
    인덱스 실패 147개 — 이슈 #492 본문이 밝힌 수이고 run ID가 없어 여기서는 재현할 수 없다). 두
    꼴 다 끝까지 돌고 호출을 하나도 안 잃는다. 호출 목록의 순서는 이 테스트가 고정하지 않는다."""
    chain = "".join(f" else if (x == {i}) g({i});" for i in range(1, 5000))
    (tmp_path / "chain.c").write_text(
        "void g(int v);\nint f(int x) { if (x == 0) g(0);" + chain + " return 0; }\n",
        encoding="utf-8",
    )
    terms = " + ".join(f"k({i})" for i in range(3000))
    (tmp_path / "sum.c").write_text(
        "int k(int v);\nint h(void) { return " + terms + "; }\n", encoding="utf-8"
    )
    assert sys.getrecursionlimit() < 3000  # 이 깊이는 재귀로는 못 간다

    index = _index(tmp_path, "chain.c", "sum.c")

    assert len(index["calls"]["g"]) == 5000
    assert len(index["calls"]["k"]) == 3000
    # 하나도 안 잃는다 — 인자가 0..2999 전부
    assert sorted(int(site["args"][0]) for site in index["calls"]["k"]) == list(range(3000))


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
    assert {(c["caller"], c["callee"]) for c in _edges(index)} == {
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
    assert indexer.INDEX_VERSION == "index-v3"


def test_the_reader_knows_the_version_this_script_writes() -> None:
    """형식이 양쪽에 따로 있다 — 이 스크립트는 `src/`를 import 하지 않는다.

    어긋나면 예외가 아니라 `unsupported_version`이 되고 **그래프 섹션이 통째로
    사라진다.** 그 모양은 "인덱서가 안 돌았다"와 같아서 캡슐만 봐서는 구별되지 않는다.
    `_INDEX_SUFFIXES` ↔ `_GRAMMARS`와 같은 종류의 이중화이고 같은 방식으로 막는다.
    """
    assert indexer.INDEX_VERSION in index_graph.SUPPORTED_INDEX_VERSIONS


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


# --- index-v2 조회 표 (NE resolver 몫, #63) ---------------------------------------
#
# 표의 모양은 실험팀 Notion 「Scout RAG baseline」 §1.2를 따른다 — 다음 단계(NE resolver)가 그
# 명세를 이식한다. 단 `src`(파일 전문) 표와 매크로 `value`는 없다: 원문은 하네스가 파일에서 읽는다.
#
# **노드 이름은 실파싱으로 확인했다**(호스트 · tree-sitter 0.26.0 · tree-sitter-c 0.24.2 ·
# Python 3.14.5, 2026-09-26 — Dockerfile 핀과 같은 판). 추측했으면 틀렸을 셋이 아래에 핀으로 있다:
# `preproc_def`의 끝이 다음 줄 0열이라는 것, `parenthesized_declarator`에 `declarator` 필드가
# 없다는 것, 프로토타입도 `declaration`이라는 것.
#
# 줄 번호가 단언에 들어가므로 이 픽스처는 **한 줄도 옮기지 않는다.** 오른쪽 숫자가 줄 번호다.
_TABLES_C = (
    "#define LEN 16\n"                              # 1
    "#define MAX(a, b) ((a) > (b) ? (a) : (b))\n"   # 2
    "#define MULTI \\\n"                            # 3
    "    1\n"                                       # 4
    "struct header {\n"                             # 5
    "    int len;\n"                                # 6
    "    char *buf;\n"                              # 7
    "};\n"                                          # 8
    "typedef struct {\n"                            # 9
    "    int x;\n"                                  # 10
    "    struct header *h;\n"                       # 11
    "} Box;\n"                                      # 12
    "typedef unsigned int u32;\n"                   # 13
    "typedef int (*cb_t)(int);\n"                   # 14
    "static char *g_buf = 0;\n"                     # 15
    "int g_count, g_arr[LEN];\n"                    # 16
    "extern int g_ext;\n"                           # 17
    "int proto(int a);\n"                           # 18
    "void (*g_fp)(int);\n"                          # 19
    "int doit(char *text, int n, ...) {\n"          # 20
    "    int i = 0, j;\n"                           # 21
    "    char buf[LEN];\n"                          # 22
    "    Box *b = 0;\n"                             # 23
    "    i = n;\n"                                  # 24
    "    j += 2;\n"                                 # 25
    "    b->h->len = n;\n"                          # 26
    "    b->x -= i;\n"                              # 27
    "    buf[0] = 1;\n"                             # 28
    "    *text = 0;\n"                              # 29
    "    i++;\n"                                    # 30
    "    memcpy(buf, text,\n"                       # 31
    "           n *\n"                              # 32
    "           2);\n"                              # 33
    '    helper(g(1), /* why */ "s");\n'            # 34
    "    return i;\n"                               # 35
    "}\n"                                           # 36
)


@pytest.fixture
def tables(tmp_path: Path) -> dict:
    (tmp_path / "t.c").write_text(_TABLES_C, encoding="utf-8")
    return _index(tmp_path, "t.c")


def _defs(*ranges: tuple[int, int]) -> list[dict]:
    return [{"file": "t.c", "line_start": start, "line_end": end} for start, end in ranges]


def test_macros_table_spans_each_define_to_its_own_last_line(tables: dict) -> None:
    """`#define`의 범위가 **그 줄에서 끝난다.**

    tree-sitter-c의 `preproc_def`·`preproc_function_def`는 끝나는 개행까지 품어서 끝 지점이
    **다음 줄 0열**이다 — `#define LEN 16`의 노드가 1~2행으로 나온다(실측). `end_point + 1`을
    그대로 쓰면 매크로마다 뒤의 무관한 줄이 하나씩 붙고, 그 줄이 조회 결과로 모델에게 간다.
    줄 잇기(`\\`)로 이어진 매크로는 이어진 줄까지다.
    """
    assert tables["macros"] == {
        "LEN": _defs((1, 1)),
        "MAX": _defs((2, 2)),
        "MULTI": _defs((3, 4)),
    }


def test_types_table_carries_struct_and_typedef_ranges(tables: dict) -> None:
    """이름 있는 struct는 그 정의, typedef는 선언문 전체 — 이름 없는 struct를 감싼 typedef면
    본문까지 든다(RAG baseline의 `stl_file` 예시가 그 모양이다). 함수 포인터 typedef의 이름도
    괄호 선언자 안에서 찾는다."""
    assert tables["types"] == {
        "header": [{**_defs((5, 8))[0], "kind": "struct"}],
        "Box": [{**_defs((9, 12))[0], "kind": "typedef"}],
        "u32": [{**_defs((13, 13))[0], "kind": "typedef"}],
        "cb_t": [{**_defs((14, 14))[0], "kind": "typedef"}],
    }


def test_fields_table_names_the_struct_that_declares_each_field(tables: dict) -> None:
    """`b->h->len`을 따라가려면 `len`을 선언한 타입과 그 범위를 알아야 한다. 이름 없는 struct는
    그것을 감싼 typedef 이름(`Box`)으로 든다."""
    header = [{"type": "header", **_defs((5, 8))[0]}]
    box = [{"type": "Box", **_defs((9, 12))[0]}]

    assert tables["fields"] == {"len": header, "buf": header, "x": box, "h": box}


def test_file_vars_table_keeps_globals_and_function_pointers_but_not_prototypes(
    tables: dict,
) -> None:
    """`int proto(int a);`도 tree-sitter에서는 `declaration`이다 — 걸러내지 않으면 함수가 전역
    변수로 올라간다. 반대로 `void (*g_fp)(int);`는 **변수**다(함수를 가리키는 포인터). 가르는
    것은 이름에 가장 가까운 선언자가 `function_declarator`인가다. `g_fp`의 이름은
    `parenthesized_declarator` 안에 있는데 그 노드에는 `declarator` 필드가 없다(실측)."""
    assert tables["file_vars"] == {
        "g_buf": _defs((15, 15)),
        "g_count": _defs((16, 16)),
        "g_arr": _defs((16, 16)),
        "g_ext": _defs((17, 17)),
        "g_fp": _defs((19, 19)),
    }


def test_functions_table_carries_the_symbol_range_and_params(tables: dict) -> None:
    """`symbols`(v1)와 **같은 정의·같은 범위**에 파라미터 이름을 더한 것이다. 가변 인자(`...`)는
    이름이 없다."""
    assert tables["functions"] == {"doit": [{**_defs((20, 36))[0], "params": ["text", "n"]}]}
    assert tables["symbols"] == [{"name": "doit", **_defs((20, 36))[0]}]


def test_locals_table_carries_params_and_every_declarator(tables: dict) -> None:
    """파일 → 함수 → 이름 → 선언 줄. 파라미터도 지역이다 — 선언 줄이 시그니처다(RAG
    baseline이 파라미터를 `declaration (local)`로 준다). 한 선언문의 선언자 여럿
    (`int i = 0, j;`)은 각각이다."""
    assert tables["locals"] == {
        "t.c": {
            "doit": {"text": [20], "n": [20], "i": [21], "j": [21], "buf": [22], "b": [23]}
        }
    }


def test_assigns_table_records_only_assignments_to_a_plain_name(tables: dict) -> None:
    """좌변이 이름 하나인 대입만 든다 — 복합 대입(`+=`)도 대입이다. 원소 대입(`buf[0] = 1`)·
    역참조 대입(`*text = 0`)·증감(`i++`)은 **그 이름에 대입한 것이 아니라서** 안 든다."""
    assert tables["assigns"] == {"t.c": {"doit": {"i": [24], "j": [25]}}}


def test_field_assigns_table_records_lhs_rhs_and_the_operator(tables: dict) -> None:
    """필드 이름이 키다 — `b->h->len`은 `len`에 대입한 것이다. 연산자를 든다: `b->x -= i`를
    `lhs`·`rhs`만으로 적으면 「x에 i를 넣었다」로 읽힌다."""
    assert tables["field_assigns"] == {
        "len": [
            {"file": "t.c", "line": 26, "func": "doit", "lhs": "b->h->len", "rhs": "n", "op": "="}
        ],
        "x": [{"file": "t.c", "line": 27, "func": "doit", "lhs": "b->x", "rhs": "i", "op": "-="}],
    }


def test_calls_table_groups_call_sites_by_callee_with_their_args(tables: dict) -> None:
    """index-v1의 간선 목록을 callee로 묶고 인자를 더한 것이다. 인자는 요약용이라 공백을 하나로
    접는다(여러 줄에 걸친 `n *\\n 2` → `n * 2`) — 원문은 하네스가 파일에서 읽는다. 인자 목록 안의
    주석은 tree-sitter에서 이름 있는 `comment` 노드라(실측) 거르지 않으면 인자로 셈해진다."""
    site = {"file": "t.c", "caller": "doit"}

    assert tables["calls"] == {
        "memcpy": [{**site, "line": 31, "args": ["buf", "text", "n * 2"]}],
        "helper": [{**site, "line": 34, "args": ["g(1)", '"s"']}],
        "g": [{**site, "line": 34, "args": ["1"]}],
    }


def test_the_index_carries_no_source_text_table(tables: dict) -> None:
    """RAG baseline의 `src`(파일 → 줄 목록)와 매크로 `value`를 싣지 않는다 — 원문은 하네스가
    파일에서 직접 읽는다. 인덱스에 소스를 복제하면 두 사본이 어긋날 자리가 생긴다."""
    assert "src" not in tables
    assert {key for records in tables["macros"].values() for r in records for key in r} == {
        "file",
        "line_start",
        "line_end",
    }


def test_params_are_read_through_each_declarator_shape(tmp_path: Path) -> None:
    """파라미터 이름을 선언자 모양마다 찾는다.

    K&R 머리는 파라미터가 `identifier`로 온다. 함수 포인터 파라미터의 이름은 괄호 선언자 안에
    있다. 포인터를 돌려주는 함수는 `pointer_declarator`가 한 겹 더 끼어 있다. `(void)`와 이름
    없는 파라미터는 이름이 없다. (함수 포인터를 **돌려주는** 정의 `int (*f(int))(int)`는 v1부터
    이름을 못 뽑아 `unresolved_symbols`로 세므로 여기 오지 않는다 — 2026-09-26 실파싱 확인.)
    """
    (tmp_path / "p.c").write_text(
        "int kr(a, b) int a; char *b; { return a; }\n"
        "void cb(int v[], int (*fp)(int)) { fp(1); }\n"
        "char *dup(const char *s) { return 0; }\n"
        "int none(void) { return 0; }\n",
        encoding="utf-8",
    )
    functions = _index(tmp_path, "p.c")["functions"]

    assert {name: records[0]["params"] for name, records in functions.items()} == {
        "kr": ["a", "b"],
        "cb": ["v", "fp"],
        "dup": ["s"],
        "none": [],
    }


def test_only_a_true_anonymous_member_lends_its_fields_to_the_outer_struct(
    tmp_path: Path,
) -> None:
    """C11 익명 멤버(`union { int b; };` — 선언자가 없다)의 필드는 바깥 struct로 접근하므로
    바깥 struct가 주인이다. **이름 있는 필드의 익명 타입**(`struct { int d; } named;`)은 다르다 —
    `d`는 `o.named.d`로만 닿으므로 주인은 그 익명 struct(이름 없음, 그 범위)다. 둘을 같은 모양으로
    적으면 「`d`를 선언한 타입은 `outer`」라는 거짓이 표에 든다(#63 코드리뷰 MEDIUM 재현)."""
    (tmp_path / "nest.c").write_text(
        "struct outer {\n"                      # 1
        "    int a;\n"                          # 2
        "    union { int b; float c; };\n"      # 3
        "    struct { int d; } named;\n"        # 4
        "};\n",                                 # 5
        encoding="utf-8",
    )
    outer = [{"type": "outer", "file": "nest.c", "line_start": 1, "line_end": 5}]

    assert _index(tmp_path, "nest.c")["fields"] == {
        "a": outer,
        "b": outer,
        "c": outer,
        "named": outer,
        "d": [{"type": None, "file": "nest.c", "line_start": 4, "line_end": 4}],
    }


def test_an_unnamed_bitfield_is_not_a_field(tmp_path: Path) -> None:
    """`int : 5;`(자리 채움 비트필드)의 선언자는 **폭 0짜리 `field_identifier`**다(실측 — 글자가
    빈 문자열). 거르지 않으면 이름이 `""`인 필드가 표에 든다."""
    (tmp_path / "bits.c").write_text("struct s { int a : 3; int : 5; };\n", encoding="utf-8")

    assert _index(tmp_path, "bits.c")["fields"] == {
        "a": [{"type": "s", "file": "bits.c", "line_start": 1, "line_end": 1}]
    }


def test_file_scope_is_everything_outside_a_function_even_under_ifdef(tmp_path: Path) -> None:
    """`#ifdef` 안의 전역·매크로는 `preproc_ifdef` 아래에 있다 — 최상위만 보면 빠진다(v1
    `_iter_definitions`가 겪은 것과 같은 구멍). 함수 안의 선언은 전역이 아니라 지역이다."""
    (tmp_path / "cfg.c").write_text(
        "#ifdef FEATURE\n"
        "static int g_feat = 1;\n"
        "#define FEAT_ON 1\n"
        "#endif\n"
        "void f(void) { int local; }\n",
        encoding="utf-8",
    )
    index = _index(tmp_path, "cfg.c")

    assert index["file_vars"] == {"g_feat": [{"file": "cfg.c", "line_start": 2, "line_end": 2}]}
    assert index["macros"] == {"FEAT_ON": [{"file": "cfg.c", "line_start": 3, "line_end": 3}]}
    assert index["locals"] == {"cfg.c": {"f": {"local": [5]}}}


def test_the_reader_builds_the_graph_from_this_scripts_v2_output(
    sample: Path, tmp_path: Path
) -> None:
    """**경계를 건너는 대조.** 이 스크립트가 쓴 index-v2를 하네스의 읽는 쪽이 받아 캡슐 간선을
    만든다. 기대값은 `sample`의 원문에서 손으로 뽑았다 — `recv_request`(16행)가 `parse_header`를
    부르고, `parse_header` 본문(9~13행)이 `memcpy`(11행)·`helper`(12행)를 부른다."""
    out = tmp_path / "out" / INDEX_FILE
    assert indexer.main(["--out", str(out), "--root", str(sample), str(sample / "parser.c")]) == 0
    graph = index_graph.GraphIndex(out)
    parse_header = Location(file="parser.c", symbol="parse_header", line_start=9, line_end=13)

    assert graph.names_for("callers", parse_header) == ["recv_request parser.c:16"]
    assert graph.names_for("callees", parse_header) == [
        "helper parser.c:12",
        "memcpy parser.c:11",
    ]
    assert graph.summary.calls == 4


# --- index-v3 (#304): 쓰기 지점 · 함수 포인터 바인딩 · 필드 경유 호출 ----------------------
#
# 노드 모양은 실파싱으로 확인했다(호스트 · tree-sitter 0.26.0 · tree-sitter-c 0.24.2 · Python 3.11,
# 2026-10-03). 추측했으면 틀렸을 것: `*p`와 `&p`가 같은 `pointer_expression`이고 `operator`로만
# 갈린다 · 괄호 식에는 필드가 없다 · 지정자 `.a.b`는 `designator` 필드가 둘이다 · `[0] = f`도
# `initializer_pair`다(`subscript_designator`).


def test_writes_table_records_element_deref_and_init_writes(tables: dict) -> None:
    """v2 픽스처(`_TABLES_C`)가 이미 든 원소 대입(28행 `buf[0] = 1`)·역참조 대입(29행 `*text = 0`)이
    `writes`에 든다. 함수 안 선언 초기화(21행 `i = 0` · 23행 `b = 0`)도 `init`으로 든다 — `locals`는
    그 줄이 쓰기인지 모른다. 초기값 없는 `j`·`buf`, 증감(30행 `i++`)은 안 든다. `assigns`는 그대로다
    (`test_assigns_table_records_only_assignments_to_a_plain_name`)."""
    row = {"file": "t.c", "func": "doit", "op": "=", "base": "name"}

    assert tables["writes"] == {
        "i": [{**row, "line": 21, "lhs": "i", "rhs": "0", "form": "init"}],
        "b": [{**row, "line": 23, "lhs": "*b", "rhs": "0", "form": "init"}],
        "buf": [{**row, "line": 28, "lhs": "buf[0]", "rhs": "1", "form": "element"}],
        "text": [{**row, "line": 29, "lhs": "*text", "rhs": "0", "form": "deref"}],
    }


# 줄 번호가 단언에 들어가므로 한 줄도 옮기지 않는다. 오른쪽 숫자가 줄 번호다.
_WRITES_C = (
    "void fill(char *p, struct s *vv, int n, int i, int j) {\n"  # 1
    "    char *q = malloc(n);\n"                                 # 2
    "    p = malloc(n);\n"                                       # 3
    "    *(p + 1) = 2;\n"                                        # 4
    "    ((char *)p)[2] = 5;\n"                                  # 5
    "    q[i][j] += 6;\n"                                        # 6
    "    vv->buf[i] = 3;\n"                                      # 7
    "    *vv->ptr = 4;\n"                                        # 8
    "    vv->payload_packet_len = n;\n"                          # 9
    "    if (p[i] == 0) { q[0]++; }\n"                           # 10
    "    /* p[9] = 1; */\n"                                      # 11
    '    puts("q[3] = 2");\n'                                    # 12
    "    *get() = 1;\n"                                          # 13
    "}\n"                                                        # 14
)


def test_writes_table_finds_the_base_through_arithmetic_casts_and_fields(tmp_path: Path) -> None:
    """기저는 괄호·캐스트·포인터 산술의 왼쪽·중첩 첨자를 벗겨 찾는다. 필드에서 멈추고 `base`가
    `field`다(`vv->buf[i]`의 키는 `buf`). 복합 대입은 연산자를 든다.

    음성: 이름 대입(3행)은 `assigns`에만 · 필드 대입(9행)은 `field_assigns`에만 든다. 비교 `==`·
    증감 `++`(10행), 주석(11행)·문자열(12행) 안의 대입 꼴은 대입식이 아니다. 기저가 이름이 아닌
    역참조(13행 `*get()`)는 이름을 지어내지 않는다."""
    (tmp_path / "w.c").write_text(_WRITES_C, encoding="utf-8")
    index = _index(tmp_path, "w.c")
    row = {"file": "w.c", "func": "fill"}

    assert index["writes"] == {
        "q": [
            {**row, "line": 2, "lhs": "*q", "rhs": "malloc(n)", "op": "=", "form": "init",
             "base": "name"},
            {**row, "line": 6, "lhs": "q[i][j]", "rhs": "6", "op": "+=", "form": "element",
             "base": "name"},
        ],
        "p": [
            {**row, "line": 4, "lhs": "*(p + 1)", "rhs": "2", "op": "=", "form": "deref",
             "base": "name"},
            {**row, "line": 5, "lhs": "((char *)p)[2]", "rhs": "5", "op": "=", "form": "element",
             "base": "name"},
        ],
        "buf": [
            {**row, "line": 7, "lhs": "vv->buf[i]", "rhs": "3", "op": "=", "form": "element",
             "base": "field"},
        ],
        "ptr": [
            {**row, "line": 8, "lhs": "*vv->ptr", "rhs": "4", "op": "=", "form": "deref",
             "base": "field"},
        ],
    }
    assert index["assigns"] == {"w.c": {"fill": {"p": [3]}}}
    assert list(index["field_assigns"]) == ["payload_packet_len"]


# #464 리뷰가 잡은 꼴. 줄 번호가 단언에 들어가므로 한 줄도 옮기지 않는다.
_WRITES_INCDEC_C = (
    "typedef int (*cb_t)(int);\n"                            # 1
    "struct ops { cb_t cb; cb_t alt; };\n"                   # 2
    "int my_cb(int x) { return x; }\n"                       # 3
    "long my_wide(long x) { return x; }\n"                   # 4
    "static struct ops g = { .alt = (cb_t)my_wide };\n"      # 5
    "void copy(char *d, const char *s, struct ops *o, int c) {\n"  # 6
    "    *d++ = *s++;\n"                                     # 7
    "    *--d = 0;\n"                                        # 8
    "    *(1 + d) = 2;\n"                                    # 9
    "    o->cb = (cb_t)my_wide;\n"                           # 10
    "    o->alt = (my_cb);\n"                                # 11
    "    o->cb = c ? my_cb : my_cb;\n"                       # 12
    "}\n"                                                    # 13
)


def test_writes_and_bindings_see_through_increments_literals_and_casts(tmp_path: Path) -> None:
    """복사 루프의 `*d++ = *s++`(7행)·`*--d = 0`(8행)은 역참조 쓰기다 — 증감은 기저를 감싼 겹일
    뿐이다. `+`의 왼쪽이 수 상수면 오른쪽이 기저다(9행). 바인딩 값의 캐스트(5·10행)·괄호(11행)는
    벗긴다. 이 넷은 #464 리뷰 전에는 0건이었다(head 81f94da에서 같은 꼴을 실파싱해 확인).

    음성: 삼항(12행)은 값이 이름 하나가 아니라 안 싣는다."""
    (tmp_path / "w.c").write_text(_WRITES_INCDEC_C, encoding="utf-8")
    index = _index(tmp_path, "w.c")
    row = {"file": "w.c", "func": "copy", "op": "=", "form": "deref", "base": "name"}

    assert index["writes"] == {
        "d": [
            {**row, "line": 7, "lhs": "*d++", "rhs": "*s++"},
            {**row, "line": 8, "lhs": "*--d", "rhs": "0"},
            {**row, "line": 9, "lhs": "*(1 + d)", "rhs": "2"},
        ],
    }
    assert index["fn_bindings"] == {
        "alt": [
            {"file": "w.c", "line": 5, "func": None, "fn": "my_wide", "form": "designated"},
            {"file": "w.c", "line": 11, "func": "copy", "fn": "my_cb", "form": "assign"},
        ],
        "cb": [{"file": "w.c", "line": 10, "func": "copy", "fn": "my_wide", "form": "assign"}],
    }


# 줄 번호가 단언에 들어가므로 한 줄도 옮기지 않는다. 함수 정의는 `impl.c`에 따로 있다 — 바인딩
# 필터가 파일 하나가 아니라 **저장소 전체**의 `functions`를 보는지를 같이 본다.
_OPS_C = (
    "struct ops { void (*free_ctx)(void *); int (*alloc)(int);"
    " int n; void (*ext)(void *); };\n"            # 1
    "static const struct ops dev = {\n"            # 2
    "    .free_ctx = sshpam_free_ctx,\n"           # 3
    "    .alloc = &my_alloc,\n"                    # 4
    "    .n = 3,\n"                                # 5
    "    .ext = free,\n"                           # 6
    "};\n"                                         # 7
    "struct outer o2 = { .in = { .cb = inner_cb }, [0] = my_alloc };\n"  # 8
    "void setup(struct ops *o, int n) {\n"         # 9
    "    o->alloc = my_alloc;\n"                   # 10
    "    o->free_ctx = &sshpam_free_ctx;\n"        # 11
    "    o->n = n;\n"                              # 12
    "    o->alloc = make_alloc(1);\n"              # 13
    "    o->ext += my_alloc;\n"                    # 14
    "    o->varint_get64s(n, 2);\n"                # 15
    "    dev.free_ctx(o);\n"                       # 16
    "    (*o->alloc)(n);\n"                        # 17
    "    helper(n);\n"                             # 18
    "}\n"                                          # 19
)
_IMPL_C = (
    "void sshpam_free_ctx(void *c) {}\n"
    "int my_alloc(int n) { return n; }\n"
    "void inner_cb(void) {}\n"
    "int make_alloc(int k) { return k; }\n"
    "void helper(int n) {}\n"
)


@pytest.fixture
def ops(tmp_path: Path) -> dict:
    (tmp_path / "ops.c").write_text(_OPS_C, encoding="utf-8")
    (tmp_path / "impl.c").write_text(_IMPL_C, encoding="utf-8")
    return _index(tmp_path, "impl.c", "ops.c")


def test_fn_bindings_table_records_designated_and_assigned_function_names(ops: dict) -> None:
    """함수 포인터 필드에 함수 이름을 묶는 두 꼴: 지정 초기화(3·4행, 중첩 8행)와 필드 대입(10·11행).
    `&f`도 `f`다. 파일 스코프 초기화의 `func`는 `None`이다.

    음성: 값이 함수가 아닌 것(5행 `3` · 12행 파라미터 `n`), 대상 트리에 정의가 없는 이름(6행
    `free`), 호출 결과(13행 `make_alloc(1)` — 정의는 있지만 이름 하나가 아니다), 첨자 지정자(8행
    `[0] = my_alloc`), `=`가 아닌 대입(14행 `+=`)."""
    ops_row = {"file": "ops.c"}

    assert ops["fn_bindings"] == {
        "free_ctx": [
            {**ops_row, "line": 3, "func": None, "fn": "sshpam_free_ctx", "form": "designated"},
            {**ops_row, "line": 11, "func": "setup", "fn": "sshpam_free_ctx", "form": "assign"},
        ],
        "alloc": [
            {**ops_row, "line": 4, "func": None, "fn": "my_alloc", "form": "designated"},
            {**ops_row, "line": 10, "func": "setup", "fn": "my_alloc", "form": "assign"},
        ],
        "cb": [{**ops_row, "line": 8, "func": None, "fn": "inner_cb", "form": "designated"}],
    }


def test_field_calls_table_names_the_field_of_each_indirect_call(ops: dict) -> None:
    """함수 자리가 필드인 호출(`->`·`.`·`(*o->f)`)을 필드 이름으로 싣는다. 직접 호출(18행
    `helper`)은 `calls`에만 든다. **`unresolved_calls`는 그대로 센다** — 필드 호출 셋이 여전히
    「대상을 구문만으로 특정 못 한 호출」이다."""
    site = {"file": "ops.c", "caller": "setup"}

    assert ops["field_calls"] == {
        "varint_get64s": [{**site, "line": 15, "args": ["n", "2"], "expr": "o->varint_get64s"}],
        "free_ctx": [{**site, "line": 16, "args": ["o"], "expr": "dev.free_ctx"}],
        "alloc": [{**site, "line": 17, "args": ["n"], "expr": "(*o->alloc)"}],
    }
    assert ops["unresolved_calls"] == 3
    assert "helper" in ops["calls"]


def test_the_reader_resolves_indirect_calls_from_this_scripts_v3_output(
    tmp_path: Path,
) -> None:
    """**경계를 건너는 대조(v3).** 이 스크립트가 쓴 index-v3를 읽는 쪽이 받아 `setup` 본문의 필드
    경유 호출을 정의로 푼다. `free_ctx`·`alloc`은 각각 struct `ops` 하나만 선언하므로 바인딩이
    다른 줄에 있어도 잇는다. `varint_get64s`는 바인딩이 없다. 기대값은 `_IMPL_C`에서 손으로 셌다
    (`sshpam_free_ctx` 1행 · `my_alloc` 2행)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "ops.c").write_text(_OPS_C, encoding="utf-8")
    (src / "impl.c").write_text(_IMPL_C, encoding="utf-8")
    out = tmp_path / "out" / INDEX_FILE
    argv = ["--out", str(out), "--root", str(src), str(src / "ops.c"), str(src / "impl.c")]
    assert indexer.main(argv) == 0
    graph = index_graph.GraphIndex(out)
    setup = Location(file="ops.c", symbol="setup", line_start=9, line_end=19)

    assert graph.indirect_callees_of(setup) == [
        Location(file="impl.c", symbol="sshpam_free_ctx", line_start=1, line_end=1),
        Location(file="impl.c", symbol="my_alloc", line_start=2, line_end=2),
    ]
    assert graph.field_writes_of("n", "impl.c") == [
        Location(file="ops.c", symbol="setup", line_start=12, line_end=12)
    ]
