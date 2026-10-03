"""Auditor `SOURCE`의 닫힘(`auditor_closure`) — 앵커 함수가 부르는 매크로·얇은 전달 함수를 실제
구현까지 따라간다(`src/orchestrator/source_closure.py`, 계획 WP-S2).

저장소·인덱스는 **손으로 적지 않는다** — C 조각을 파일로 쓰고 실제 인덱서(`indexer.build_index`)와
`GraphIndex`를 태운다. 줄 범위를 손으로 세면 인덱스가 실제로 내는 행과 갈라진 채 초록이 난다
(`test_fixture_index_matches_the_real_fixture`가 같은 이유로 있다).
"""

import importlib.util
import json
import re
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from slicer_source import assemble
from src.adapters.fake_retriever import read_symbol_source
from src.adapters.index_graph import GraphIndex
from src.adapters.slices import SliceRef, split_source
from src.orchestrator import runner, source_closure
from src.orchestrator.runner import _block
from src.orchestrator.source_closure import (
    BODY_CHARS,
    DROPPED_LISTED,
    FOLD_DEPTH,
    MACRO_CHAIN_DEPTH,
    MACRO_CHARS,
    WRAPPER_MAX_LINES,
    build_closure,
    classify_macro,
)
from src.schemas.common import Location
from tests.test_pipeline_e2e import _run, _with_index
from tests.test_slice_targets import _prompts, _slices_file, _source_events

_INDEXER_PATH = Path(__file__).resolve().parents[1] / "tools" / "harness-tools" / "indexer.py"
_spec = importlib.util.spec_from_file_location("harness_indexer_closure", _INDEXER_PATH)
indexer = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(indexer)


# --- 저장소 · 인덱스 · 호출 도우미 -------------------------------------------------------------


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, dict, GraphIndex]:
    """C 조각을 파일로 쓰고 실제 인덱서로 인덱스를 만든다. (루트, 인덱스 문서, GraphIndex)."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    document = indexer.build_index([root / name for name in files], root)
    path = tmp_path / "index.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return root, document, GraphIndex(path)


def _symbol(graph: GraphIndex, files: dict[str, str], name: str) -> Location:
    return next(s for f in files for s in graph.symbols_for(f) or [] if s.symbol == name)


def _close(tmp_path: Path, files: dict[str, str], anchor: str, **overrides):  # noqa: ANN202
    """앵커 함수의 닫힘. `blocks`에는 앵커 블록 하나만 있다 — 러너가 이 자리에서 부르는 모양이다."""
    root, _, graph = _repo(tmp_path, files)
    here = _symbol(graph, files, anchor)
    read = partial(read_symbol_source, root)
    text = read(here)
    kwargs = {
        "callables_of": graph.callables_of, "read": read, "block": _block,
        "blocks": [_block(here, text)], "measure": len, "room": 100_000,
    }
    kwargs.update(overrides)
    return build_closure(here, text, **kwargs), graph, root


def _labels(closure) -> list[str]:  # noqa: ANN001
    """블록마다 첫 줄 — `/* CLOSURE: … */`."""
    return [block.split("\n", 1)[0] for block in closure.blocks]


# 한 저장소에 여러 갈래를 다 둔다: rename 매크로 → 전달 함수 → NULL 검사가 있는 실제 구현 /
# 괄호 인자 전달 매크로 → 전달 함수 둘 → 외부(free) / 불투명 매크로 둘(하나는 다른 매크로를 쓴다).
_WRAP_H = r"""#ifndef WRAP_H
#define WRAP_H
#define xmalloc safe_alloc
#define xfree(p) safe_free((p))
#define LOG_ERR(fmt, ...) do { log_msg(2, __FILE__, fmt, ##__VA_ARGS__); } while (0)
#define CHECK_LEN(p, n) \
    if (!in_range((p), (n))) \
        goto trunc
#define in_range(p, n) (((const u_char *)(p) + (n)) <= end)
static inline void *safe_alloc(size_t n)
{
    return raw_alloc(n);
}
static inline void safe_free(void *p) { raw_free(p); }
#endif
"""
_MAIN_C = r"""#include "wrap.h"

void *raw_alloc(size_t n)
{
    void *p = malloc(n);
    if (p == NULL)
        abort();
    return p;
}

void raw_free(void *p)
{
    free(p);
}

int use(const char *src, size_t len)
{
    char *buf = xmalloc(len);
    memcpy(buf, src, len);
    CHECK_LEN(src, len);
    LOG_ERR("bad %d", (int)len);
    xfree(buf);
    return 0;
trunc:
    return -1;
}
"""
_MAIN = {"wrap.h": _WRAP_H, "main.c": _MAIN_C}


# --- 매크로 분류: 정의 원문으로 가른다 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#define xmalloc safe_alloc", ("rename", "safe_alloc")),
        ("#define xmalloc safe_alloc /* tail */", ("rename", "safe_alloc")),
        ("#define xfree(p) safe_free(p)", ("forward", "safe_free")),
        ("#define xfree(p) safe_free((p))", ("forward", "safe_free")),
        ("#define xfree(p) (safe_free(p))", ("forward", "safe_free")),
        ("#define NEW(n) alloc_n((size_t)(n), 1)", ("forward", "alloc_n")),
        ("#define twice(x) twice_impl(x, x)", ("forward", "twice_impl")),
        ('#define say(x) say_impl(x, "hi")', ("forward", "say_impl")),
        ("#define LOG(fmt, ...) log_msg(fmt, __VA_ARGS__)", ("forward", "log_msg")),
        ("#define LOG(fmt, ...) log_msg(fmt, ##__VA_ARGS__)", ("opaque", None)),
        ("#define TRY(x) do { if (!(x)) goto fail; } while (0)", ("opaque", None)),
        ("#define MAX2(a, b) ((a) > (b) ? (a) : (b))", ("opaque", None)),
        ("#define SQR(x) sqr_impl(x * x)", ("opaque", None)),
        # 대문자 식별자(`__FILE__`·`FLAG_X`)는 상수다 — 접되 인자는 사슬 줄에 보인다
        ("#define DBG(n) dbg_alloc(n, __FILE__, __LINE__)", ("forward", "dbg_alloc")),
        ("#define LOG(m) log_at(__FILE__, __LINE__, m)", ("forward", "log_at")),
        # 소문자 전역은 상수가 아니다
        ("#define lower(x) lower_impl(x, g_flag)", ("opaque", None)),
        # `return`을 든 매크로는 **호출자**를 끝낸다 — 호출 하나로 접으면 그 제어 흐름이 사라진다
        ("#define FAIL_WITH(x) return real(x)", ("opaque", None)),
        ("#define FAIL_WITH(x) return real(x);", ("opaque", None)),
        # 호출 결과에 씌운 형변환은 값을 바꾼다(좁히기·부호) — `(void)`만 값을 안 바꾼다
        ("#define A(x) (int)B(x)", ("opaque", None)),
        ("#define A(x) (char *)B(x)", ("opaque", None)),
        ("#define A(x) (void)B(x)", ("forward", "B")),
        # 이름 있는 가변 인자(`x...`)는 `__VA_ARGS__`가 아니라 `x`다
        ("#define A(x...) B(x)", ("forward", "B")),
        # 줄 이음이 먼저다 — `//` 주석 끝의 `\`는 다음 줄을 주석으로 삼킨다
        ("#define A(x) B(x) // c \\\n   + 1", ("forward", "B")),
        ("#define STR(x) #x", ("opaque", None)),
        ("#define EMPTY(x)", ("opaque", None)),
        ("#define LIMIT 64", ("opaque", None)),
        ("#define fp(p) (*fp_ptr)(p)", ("opaque", None)),
        ("#define pick(a, b) (a)(b)", ("opaque", None)),
        # 인자의 `(f)(x)`는 형변환이 아니라 호출이다 — `f`가 파라미터이므로 벗기지 않는다
        ("#define call(f, x) apply((f)(x))", ("opaque", None)),
        ("typedef int x;", ("opaque", None)),
    ],
)
def test_a_macro_is_classified_by_its_definition_text(text: str, expected: tuple) -> None:
    """rename(`#define A B`) · 단일 호출 전달(`#define A(x) B(x, 1)` — 인자가 파라미터·형변환·
    상수뿐) · 불투명(그 밖 전부 — 문장 매크로·`##`·`#x`·계산한 인자·빈 정의·함수 포인터 호출)."""
    assert classify_macro(text) == expected


def test_a_continued_and_commented_macro_is_read_as_one_definition() -> None:
    """줄 이음(`\\`)과 주석은 정의 하나를 가르지 않는다 — 여러 줄 매크로가 불투명이 되는 것은 그
    본문이 문장이어서지 줄이 나뉘어서가 아니다."""
    rename = "#define A /* old name */ \\\n    B"
    forward = "#define F(x) \\\n    g(x, 1) // tail"
    assert classify_macro(rename) == ("rename", "B")
    assert classify_macro(forward) == ("forward", "g")


# --- 전달 함수를 접고 첫 실제 본문에서 멈춘다 ---------------------------------------------------


def test_a_rename_macro_and_a_forwarding_wrapper_fold_to_the_real_body(tmp_path: Path) -> None:
    """`xmalloc`(rename 매크로) → `safe_alloc`(헤더의 한 문장 전달 함수) → `raw_alloc`(NULL을
    검사하고 abort — 접지 않는다). 사슬 줄 하나와 `raw_alloc`의 본문만 싣는다 — 중간 두 단계는
    안 싣는다."""
    closure, graph, root = _close(tmp_path, _MAIN, "use")

    raw_alloc = _symbol(graph, _MAIN, "raw_alloc")
    first = closure.blocks[0]
    assert first == (
        "/* CLOSURE: xmalloc[macro] -> safe_alloc -> raw_alloc */\n"
        + _block(raw_alloc, read_symbol_source(root, raw_alloc))
    )
    assert "abort();" in first and "return raw_alloc(n);" not in first


def test_a_chain_that_ends_outside_the_repo_is_labelled_and_has_no_body(tmp_path: Path) -> None:
    """`xfree(buf)` → `safe_free((p))` → `raw_free`(`free(p)` 한 문장) → `free`(저장소에 정의 없음).
    끝의 본문은 없다 — 있었던 것처럼 쓰지 않고 그 사실을 적는다."""
    closure, _, _ = _close(tmp_path, _MAIN, "use")

    assert (
        "/* CLOSURE: xfree[macro] -> safe_free -> raw_free -> free"
        " (no definition in the repo index) */"
    ) in _labels(closure)
    chain = next(b for b in closure.blocks if b.startswith("/* CLOSURE: xfree"))
    assert "\n" not in chain


def test_an_opaque_macro_is_carried_verbatim_with_the_macros_its_body_uses(
    tmp_path: Path,
) -> None:
    """불투명 매크로는 호출 대상이 없다 — 전개가 구현이다. `CHECK_LEN`은 `goto trunc`을 숨긴 문장
    매크로이고 그 본문이 `in_range` 매크로를 쓴다 → 둘 다 원문 그대로(줄 이음 포함) 싣는다."""
    closure, graph, root = _close(tmp_path, _MAIN, "use")

    check = next(b for b in closure.blocks if b.startswith("/* CLOSURE: CHECK_LEN"))
    assert check.split("\n", 1)[0] == "/* CLOSURE: CHECK_LEN[macro] */"
    continued = "#define CHECK_LEN(p, n) \\\n    if (!in_range((p), (n))) \\\n        goto trunc"
    assert continued in check
    assert "#define in_range(p, n) (((const u_char *)(p) + (n)) <= end)" in check
    log = next(b for b in closure.blocks if b.startswith("/* CLOSURE: LOG_ERR"))
    assert "##__VA_ARGS__" in log


def test_units_follow_the_order_of_the_calls_in_the_anchor(tmp_path: Path) -> None:
    """단위는 앵커 본문에서 그 이름이 처음 나온 순서다 — 이름 순이 아니다."""
    closure, _, _ = _close(tmp_path, _MAIN, "use")

    heads = [label.split(" ")[2] for label in _labels(closure)]
    assert heads == ["xmalloc[macro]", "CHECK_LEN[macro]", "LOG_ERR[macro]", "xfree[macro]"]


def test_a_call_to_a_plain_function_adds_nothing(tmp_path: Path) -> None:
    """접을 단계가 없으면(`raw_alloc` 같은 실제 함수를 바로 부름) 닫힘이 보탤 것이 없다 — 피호출
    본문은 `auditor_callee_hops`의 일이다. 저장소에 없는 이름(`memcpy`)도 단위를 안 만든다."""
    files = {"a.c": "int real(int n)\n{\n    if (n < 0)\n        return -1;\n    return n;\n}\n"
             "int f(int n)\n{\n    memcpy(0, 0, n);\n    return real(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert closure.blocks == ()


@pytest.mark.parametrize(
    "wrapper",
    [
        # NULL 검사 — 계약이다
        "static int w(char *p)\n{\n    if (p == NULL)\n        return -1;\n    return real(p);\n}",
        # 오류 반환이 낀 삼항
        "static int w(char *p)\n{\n    return real(p) ? 0 : -1;\n}",
        # 단락 평가로 검사를 한다
        "static int w(char *p)\n{\n    return p && real(p);\n}",
        # 계산한 인자
        "static int w(int n)\n{\n    return real(n + 1);\n}",
        # 필드를 꺼내 넘긴다
        "static int w(struct s *q)\n{\n    return real(q->len);\n}",
        # 호출 둘
        "static int w(int n)\n{\n    log_it(n);\n    return real(n);\n}",
        # 지역 변수를 거친다
        "static int w(int n)\n{\n    int r = real(n);\n    return r;\n}",
        # 반환값을 바꾼다
        "static int w(int n)\n{\n    return -real(n);\n}",
        # 파라미터가 아닌 식별자를 넘긴다(전역)
        "static int w(int n)\n{\n    return real(g_limit);\n}",
        # 호출 결과를 좁힌다 — 값이 바뀐다
        "static int w(char *p)\n{\n    return (char)real(p);\n}",
        "static int w(char *p)\n{\n    return (unsigned short)real(p);\n}",
    ],
)
def test_a_wrapper_with_a_check_a_computation_or_a_second_call_is_not_folded(
    tmp_path: Path, wrapper: str
) -> None:
    """접는 것은 **엄격한 한 문장 전달자**뿐이다 — NULL 검사·오류 반환·계산한 인자는 Auditor가
    필요한 계약이다. 접히지 않은 `w`는 사슬의 끝이 되고(접은 단계가 0) 닫힘은 아무것도 싣지
    않는다."""
    files = {"a.c": f"int real(char *p) {{ return 1; }}\n{wrapper}\nint f(int n)\n{{\n"
                    "    return w(0);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert closure.blocks == ()


def test_a_wrapper_over_a_non_folding_one_ends_the_chain_at_that_one(tmp_path: Path) -> None:
    """`outer`는 접히고(한 문장 전달) `inner`는 NULL 검사 때문에 안 접힌다 → 사슬은 `outer`에서
    `inner`로 가 멈추고 `inner`의 본문(검사를 담은)을 싣는다 — 검사 뒤의 구현(`real`)까지 가지
    않는다."""
    files = {"a.c": "int real(char *p) { return 1; }\n"
                    "static int inner(char *p)\n{\n    if (p == NULL)\n        return -1;\n"
                    "    return real(p);\n}\n"
                    "static int outer(char *p) { return inner(p); }\n"
                    "int f(char *s)\n{\n    return outer(s);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    assert block.startswith("/* CLOSURE: outer -> inner */\n")
    assert "if (p == NULL)" in block and "int real" not in block


@pytest.mark.parametrize("noreturn", ["abort", "exit", "longjmp"])
def test_a_wrapper_that_never_returns_is_not_folded(tmp_path: Path, noreturn: str) -> None:
    """`die(c)`는 한 문장이고 인자도 파라미터지만 `abort`·`exit`·`longjmp` 호출은 계약이다 — 접지
    않는다. 그 앞의 전달 함수(`bail`)는 접혀 `bail -> die`가 되고 `die` 본문이 실린다."""
    files = {"a.c": f"static void die(int c) {{ {noreturn}(c); }}\n"
                    "static void bail(int c) { die(c); }\n"
                    "void f(int c)\n{\n    bail(c);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    assert block.startswith("/* CLOSURE: bail -> die */\n")
    assert f"{noreturn}(c);" in block


def test_a_wrapper_may_cast_and_pass_constants(tmp_path: Path) -> None:
    """한 문장 전달자의 인자는 파라미터·그 형변환·상수다 — `(int)` 같은 형변환과 숫자·문자열."""
    files = {"a.c": "int real(char *p, int a, int b, const char *s)\n"
                    "{\n    if (p)\n        return a;\n    return b;\n}\n"
                    "static int w(char *p, int n)\n{\n"
                    '    return real(p, (int)n, 0, "x");\n}\n'
                    "int f(char *s)\n{\n    return w(s, 1);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    # `0`·`"x"`가 얹혀 파라미터를 그대로 넘기는 것이 아니다 — 인자가 사슬 줄에 보인다
    assert block.startswith('/* CLOSURE: w -> real(p, (int)n, 0, "x") */\n')


def test_a_fold_that_adds_drops_or_reorders_arguments_shows_the_call(tmp_path: Path) -> None:
    """접으면서 값이 사라지면 안 된다 — 전달이 자기 파라미터를 같은 순서로 그대로 넘기지 않으면
    (상수를 얹거나 순서를 바꾸거나 뺀다) 받는 쪽 이름 뒤에 그 인자를 적는다. 그대로 넘기는 단계
    (`alloc_n` → `real`)는 아무것도 안 붙인다."""
    files = {
        "a.h": "#define NEW(n) alloc_n((size_t)(n), 1)\n#define SWAP(a, b) pair(b, a)\n",
        "a.c": '#include "a.h"\n'
               "int real(int a, int b) { if (a) return b; return a; }\n"
               "static int alloc_n(int n, int z) { return real(n, z); }\n"
               "static int pair(int x, int y) { return real(x); }\n"
               "int f(int n)\n{\n    NEW(n);\n    return SWAP(n, n);\n}\n",
    }

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == [
        "/* CLOSURE: NEW[macro] -> alloc_n((size_t)(n), 1) -> real */",
        "/* CLOSURE: SWAP[macro] -> pair(b, a) -> real(x) (body above) */",
    ]


def test_a_chain_that_ends_outside_the_repo_still_shows_the_constants_it_passes(
    tmp_path: Path,
) -> None:
    """`xcalloc(n)`은 `calloc(n, 1)`이다 — 끝이 저장소 밖(`calloc`)이어도 얹은 상수가 사슬 줄에
    보인다. 본문이 없다는 사실도 그대로 적는다."""
    files = {"a.c": "static void *xcalloc(size_t n) { return calloc(n, 1); }\n"
                    "int f(size_t n)\n{\n    return xcalloc(n) != 0;\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == [
        "/* CLOSURE: xcalloc -> calloc(n, 1) (no definition in the repo index) */"
    ]


def test_an_ambiguous_end_is_marked_and_an_ambiguous_start_is_counted(tmp_path: Path) -> None:
    """정의가 여럿(`#if` 양쪽 가지)인 이름은 고르지 않는다. 사슬 **끝**이면 그렇다고 적고(「정의
    없음」과 다르다), 호출 이름 **자체**가 모호하면 단위를 안 만들되 `closure_ambiguous`가 센다 —
    단위 0이 「닫을 것이 없다」로 읽히지 않게."""
    files = {
        "a.h": "#ifdef WIN\n#define PLAT(x) plat_win(x)\n#else\n#define PLAT(x) plat_unix(x)\n"
               "#endif\n#define VIA(x) PLAT(x)\n",
        "a.c": '#include "a.h"\nint f(int n)\n{\n    VIA(n);\n    return PLAT(n);\n}\n',
    }

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == [
        "/* CLOSURE: VIA[macro] -> PLAT (ambiguous: several definitions in the repo index) */"
    ]
    assert closure.event()["closure_ambiguous"] == 1  # 호출 이름 `PLAT` 자체


def test_a_cast_on_a_forwarded_argument_is_shown_not_hidden(tmp_path: Path) -> None:
    """파라미터에 씌운 형변환은 값을 좁히거나 부호를 바꾼다(`size_t` → `unsigned short`) — 접되
    사슬 줄이 그 형변환을 그대로 보인다. 겉으로 같은 `w -> alloc`이면 잘림이 사라진다. 바깥 괄호만
    다른 인자(`(n)`)는 그대로 넘기는 것이다."""
    files = {
        "a.h": "#define NEWBUF(n) alloc((unsigned char)(n))\n#define PLAIN(n) alloc2((n))\n",
        "a.c": '#include "a.h"\n'
               "void *alloc(unsigned short n) { if (n == 0) return 0; return malloc(n); }\n"
               "void *alloc2(size_t n) { if (n == 0) return 0; return malloc(n); }\n"
               "static void *w(size_t n) { return alloc((unsigned short)n); }\n"
               "void *f(size_t len)\n{\n    PLAIN(len);\n    NEWBUF(len);\n    return w(len);\n}\n",
    }

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == [
        "/* CLOSURE: PLAIN[macro] -> alloc2 */",
        "/* CLOSURE: NEWBUF[macro] -> alloc((unsigned char)(n)) */",
        "/* CLOSURE: w -> alloc((unsigned short)n) (body above) */",
    ]


def test_a_member_call_across_a_newline_is_not_a_chain_start(tmp_path: Path) -> None:
    """`p->`와 이름 사이에 줄바꿈이 끼어도(긴 체인) 멤버 호출이다 — 같은 이름의 전달 함수가 있어도
    닫힘을 안 낸다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w(int n) { return real(n); }\n"
                    "int f(struct ops *p, int n)\n{\n"
                    "    p->\n        w(1);\n    p .\n    w(2);\n    return n;\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert closure.blocks == ()


def test_a_call_inside_a_spliced_line_comment_is_not_a_chain_start(tmp_path: Path) -> None:
    """`//` 주석 끝의 `\\`는 다음 줄을 주석으로 삼킨다 — 줄 이음이 주석 벗기기보다 먼저다. 그 줄의
    `w(1)`은 호출이 아니다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w(int n) { return real(n); }\n"
                    "int f(int n)\n{\n    // note \\\n    w(1);\n    return n;\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert closure.blocks == ()


def test_a_huge_nested_argument_is_not_a_forwarder_and_costs_nothing(tmp_path: Path) -> None:
    """한 문장이 `FORWARD_MAX_CHARS`를 넘으면 전달이 아니다 — 괄호가 수천 겹인 생성 코드가 제곱 시간
    (4,000겹에 1.7초 — 리뷰 프로브)을 먹지 않는다."""
    from src.orchestrator.source_closure import FORWARD_MAX_CHARS

    nest = "(" * 5_000 + "x" + ")" * 5_000
    assert source_closure._forward(f"g({nest})", ("x",)) is None
    assert len(f"g({nest})") > FORWARD_MAX_CHARS
    assert source_closure._forward("g(x)", ("x",)) == ("g", ["x"])


def test_a_named_variadic_macro_forwards_its_own_parameter_name() -> None:
    """`A(x...)`의 가변 인자 이름은 `x`다 — `__VA_ARGS__`가 아니므로 `B(x)`는 그대로 넘기는 것이다
    (인자 표시가 붙지 않는다). 이름 없는 `...`는 `__VA_ARGS__`다."""
    assert source_closure._classify("#define A(x...) B(x)") == ("forward", "B", "")
    assert source_closure._classify("#define A(f, ...) B(f, __VA_ARGS__)") == ("forward", "B", "")


def test_a_wrapper_may_pass_an_uppercase_constant_but_not_a_lowercase_global(
    tmp_path: Path,
) -> None:
    """대문자 식별자(`FLAG_ZERO`)는 상수다 — 접고 인자를 보인다. 소문자 전역은 접지 않는다."""
    files = {"a.c": "int real(int n, int z) { if (z) return 0; return n; }\n"
                    "static int w(int n) { return real(n, FLAG_ZERO); }\n"
                    "static int v(int n) { return real(n, g_flag); }\n"
                    "int f(int n)\n{\n    v(n);\n    return w(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == ["/* CLOSURE: w -> real(n, FLAG_ZERO) */"]


def test_a_long_argument_list_is_clipped_in_the_chain_line(tmp_path: Path) -> None:
    """사슬 줄은 한 줄이다 — 넘기는 인자 표시가 `ARGS_CHARS`를 넘으면 잘라 `...`을 붙인다."""
    long = "x" * 80
    files = {"a.h": f'#define LG(m) lg(m, "{long}")\n',
             "a.c": '#include "a.h"\n'
                    "int lg(int m, const char *s) { if (m) return 1; return 0; }\n"
                    "int f(int n)\n{\n    return LG(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    (label,) = _labels(closure)
    assert label.startswith("/* CLOSURE: LG[macro] -> lg(m, ")
    assert label.endswith("...) */")
    assert long not in label


def test_a_function_over_the_line_limit_is_not_even_read_as_a_wrapper(tmp_path: Path) -> None:
    """본문이 `WRAPPER_MAX_LINES`줄을 넘으면 한 문장이어도(주석이 길어도) 전달자로 안 본다 —
    읽지도 않는다(큰 함수를 읽는 비용을 안 낸다)."""
    pad = "\n".join(f"    /* pad {i} */" for i in range(WRAPPER_MAX_LINES))
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    f"static int w(int n)\n{{\n{pad}\n    return real(n);\n}}\n"
                    "int f(int n)\n{\n    return w(n);\n}\n"}
    reads: list[Location] = []
    root, _, graph = _repo(tmp_path, files)
    here = _symbol(graph, files, "f")

    def read(loc: Location) -> str:
        reads.append(loc)
        return read_symbol_source(root, loc)

    text = read(here)
    closure = build_closure(
        here, text, callables_of=graph.callables_of, read=read, block=_block,
        blocks=[_block(here, text)], measure=len, room=100_000,
    )

    assert closure.blocks == ()
    assert [loc.symbol for loc in reads] == ["f"]


def test_fold_depth_stops_the_chain_at_the_limit(tmp_path: Path) -> None:
    """전달 함수가 `FOLD_DEPTH`를 넘게 이어지면 그 단계에서 멈춘다 — 막힌 함수가 사슬의 끝이 되어
    본문이 실린다. 안전판이다 — 순환이 아니어도 끝없이 이어지는 것을 막는다."""
    n = FOLD_DEPTH + 3
    body = "".join(f"static int w{i}(int n) {{ return w{i + 1}(n); }}\n" for i in range(n))
    files = {"a.c": f"int w{n}(int n) {{ if (n) return 1; return 0; }}\n{body}"
                    "int f(int n)\n{\n    return w0(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    chain = " -> ".join(f"w{i}" for i in range(FOLD_DEPTH + 1))
    assert block.startswith(f"/* CLOSURE: {chain} */\n")
    assert f"w{FOLD_DEPTH + 1}(n)" in block  # 끝 함수(깊이 한도에 걸린 전달자)의 본문이다


def test_a_forwarding_cycle_is_cut_and_labelled(tmp_path: Path) -> None:
    """`c1 -> c2 -> c1` — 방문 집합이 끊고 사슬 줄이 순환이라고 적는다. 끝없이 돌지 않는다."""
    files = {"a.c": "static int c2(int n);\nstatic int c1(int n) { return c2(n); }\n"
                    "static int c2(int n) { return c1(n); }\n"
                    "int f(int n)\n{\n    return c1(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == ["/* CLOSURE: c1 -> c2 -> c1 (cycle) */"]
    assert closure.blocks[0].count("\n") == 0


def test_macro_chain_depth_is_limited(tmp_path: Path) -> None:
    """불투명 매크로가 다른 매크로를 쓰는 사슬은 `MACRO_CHAIN_DEPTH`단까지만 싣는다(루트 포함)."""
    n = MACRO_CHAIN_DEPTH + 2
    defines = "".join(f"#define M{i}(x) ((x) + M{i + 1}(x))\n" for i in range(1, n))
    files = {"a.h": defines + f"#define M{n}(x) ((x) + 1)\n",
             "a.c": '#include "a.h"\nint f(int n)\n{\n    return M1(n);\n}\n'}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    for i in range(1, MACRO_CHAIN_DEPTH + 1):
        assert f"#define M{i}(x)" in block
    assert f"#define M{MACRO_CHAIN_DEPTH + 1}(x)" not in block


def test_macros_that_use_each_other_are_carried_once_each(tmp_path: Path) -> None:
    """마름모(`A`가 `B`·`C`를 쓰고 둘 다 `D`를 쓴다)와 순환(`D`가 `A`를 되쓴다)에서도 정의는 한
    번씩이다 — 방문한 이름을 다시 안 센다."""
    files = {"a.h": "#define D(x) ((x) + A(x))\n#define B(x) ((x) + D(x))\n"
                    "#define C(x) ((x) * D(x))\n#define A(x) (B(x) + C(x))\n",
             "a.c": '#include "a.h"\nint f(int n)\n{\n    return A(n);\n}\n'}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    for name in "ABCD":
        assert block.count(f"#define {name}(x)") == 1, name


def test_calls_in_comments_strings_and_members_are_not_chain_starts(tmp_path: Path) -> None:
    """앵커 안의 `w(`가 주석·문자열 리터럴 안이거나 `p->w(`·`p.w(`(함수 포인터 멤버)면 호출이
    아니다 — 저장소에 같은 이름의 전달 함수가 있어도 닫힘을 안 낸다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w(int n) { return real(n); }\n"
                    "int f(struct ops *p, int n)\n{\n"
                    '    /* w(1) is not called */\n    // w(2)\n    puts("w(3)");\n'
                    "    p->w(4);\n    p.w(5);\n    return n;\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert closure.blocks == ()


def test_the_anchor_itself_is_not_a_chain_start(tmp_path: Path) -> None:
    """앵커가 얇은 전달 함수여도 자기 이름(시그니처의 `name(`)에서 사슬을 시작하지 않는다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "int anchor(int n) { return real(n); }\n"}

    closure, _, _ = _close(tmp_path, files, "anchor")

    assert closure.blocks == ()


def test_a_hop_resolves_names_against_the_file_it_was_found_in(tmp_path: Path) -> None:
    """같은 이름의 `static` 함수가 두 파일에 있으면 전달자 `wrap`(b.c)이 부르는 `helper`는 `wrap`의
    파일의 것이다 — 앵커 파일이 아니라 단계마다 그 단계가 있는 파일을 가까운 파일로 넘긴다."""
    files = {"a.c": "static int helper(int n) { if (n) return 1; return 0; }\n",
             "b.c": "static int helper(int n) { if (n > 5) return 2; return 3; }\n"
                    "static int wrap(int n) { return helper(n); }\n"
                    "int f(int n)\n{\n    return wrap(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    (block,) = closure.blocks
    assert block.startswith("/* CLOSURE: wrap -> helper */\n/* ===== b.c:1-1 ===== */\n")
    assert "n > 5" in block


def test_the_same_wrapper_called_twice_is_one_unit(tmp_path: Path) -> None:
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w(int n) { return real(n); }\n"
                    "int f(int n)\n{\n    w(1);\n    return w(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == ["/* CLOSURE: w -> real */"]


def test_two_wrappers_to_one_implementation_carry_it_once(tmp_path: Path) -> None:
    """둘째 단위는 사슬 줄만 싣고 본문은 **위**라고 적는다 — 같은 본문을 두 번 안 싣는다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w1(int n) { return real(n); }\n"
                    "static int w2(int n) { return real(n); }\n"
                    "int f(int n)\n{\n    w1(n);\n    return w2(n);\n}\n"}

    closure, _, _ = _close(tmp_path, files, "f")

    assert _labels(closure) == [
        "/* CLOSURE: w1 -> real */", "/* CLOSURE: w2 -> real (body above) */"
    ]
    assert closure.blocks[1].count("\n") == 0
    assert sum(block.count("int real(int n)") for block in closure.blocks) == 1


def test_a_body_the_source_already_shows_is_not_carried_again(tmp_path: Path) -> None:
    """`blocks`(지금까지 SOURCE에 실린 것)에 그 본문이 이미 있으면 사슬 줄만 싣는다 — 배너의 줄
    범위로 안다. 실리지 **않은** 것을 위에 있다고 적지 않는 것이 요점이다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w(int n) { return real(n); }\n"
                    "int f(int n)\n{\n    return w(n);\n}\n"}
    root, _, graph = _repo(tmp_path, files)
    here = _symbol(graph, files, "f")
    real = _symbol(graph, files, "real")
    read = partial(read_symbol_source, root)
    shown = [_block(here, read(here)), _block(real, read(real))]

    closure = build_closure(
        here, read(here), callables_of=graph.callables_of, read=read, block=_block,
        blocks=shown, measure=len, room=100_000,
    )

    assert closure.blocks == ("/* CLOSURE: w -> real (body above) */",)
    assert closure.carried == ()


def test_an_opaque_macro_already_fully_carried_adds_no_unit_but_a_chain_to_it_does(
    tmp_path: Path,
) -> None:
    """뿌리 정의가 이미 SOURCE에 있고 그 본문이 다른 매크로를 안 쓰면 보탤 것이 없다 — 단위를 안
    만들고 예산에 밀려 빠진 것으로 세지도 않는다(「위에 있다」만 적은 줄은 소음이다). 그 매크로로
    **접히는** 사슬(`ALIAS` → `ONE`)은 관계를 알려 주므로 줄만 싣는다."""
    files = {
        "a.h": "#define ONE(x) ((x) + 1)\n#define ALIAS(x) ONE(x)\n",
        "a.c": '#include "a.h"\nint f(int n)\n{\n    return ONE(n);\n}\n'
               "int g(int n)\n{\n    return ALIAS(n);\n}\n",
    }
    root, _, graph = _repo(tmp_path, files)
    read = partial(read_symbol_source, root)
    one = graph.callables_of(["ONE"], "a.c")["ONE"][1]

    def close(anchor: str):  # noqa: ANN202
        here = _symbol(graph, files, anchor)
        return build_closure(
            here, read(here), callables_of=graph.callables_of, read=read, block=_block,
            blocks=[_block(here, read(here)), _block(one, read(one))], measure=len,
            room=100_000,
        )

    direct, folded = close("f"), close("g")

    assert direct.blocks == () and direct.dropped == ()
    assert folded.blocks == ("/* CLOSURE: ALIAS[macro] -> ONE[macro] (definition above) */",)


def test_a_unit_over_the_room_is_dropped_and_listed(tmp_path: Path) -> None:
    """남은 몫을 넘는 단위는 빠지고 이벤트가 목록으로 든다 — 개수만이 아니다(§0 「0을 없음으로
    쓰지 않는다」). 몫은 한 단위의 글 길이 + 이음 줄바꿈 1이다."""
    closure, _, _ = _close(tmp_path, _MAIN, "use", room=0)
    assert closure.blocks == () and closure.size == 0
    listed = json.loads(closure.event()["closure_dropped_units"])
    assert closure.event()["closure_dropped"] == len(listed) == 4
    assert listed[0]["chain"] == "xmalloc[macro] -> safe_alloc -> raw_alloc"
    assert {entry["reason"] for entry in listed} == {"cap"}

    full, _, _ = _close(tmp_path / "again", _MAIN, "use")
    first = full.blocks[0]
    just_fits, _, _ = _close(tmp_path / "fits", _MAIN, "use", room=1 + len(first))
    assert just_fits.blocks == (first,)  # 딱 맞으면 싣는다
    assert len(just_fits.dropped) == 3 and just_fits.size == 1 + len(first)
    short, _, _ = _close(tmp_path / "short", _MAIN, "use", room=len(first))
    assert first not in short.blocks  # 하나 모자라면 뺀다(이음 줄바꿈 1을 센다)


def test_the_room_is_counted_with_the_measure_the_runner_passes(tmp_path: Path) -> None:
    """토큰 상한이면 러너가 모델 카운터(`measure`)를 넘긴다 — 글자가 아니라 그것으로 센다. 같은
    몫 40이 글자로는 아무것도 못 싣고 단어로는 싣는다."""

    def words(text: str) -> int:
        return len(text.split())

    by_chars, _, _ = _close(tmp_path / "chars", _MAIN, "use", room=40)
    by_words, _, _ = _close(tmp_path / "words", _MAIN, "use", measure=words, room=40)

    assert by_chars.blocks == ()
    assert by_words.blocks and by_words.dropped
    assert by_words.size == sum(1 + words(block) for block in by_words.blocks) <= 40


def test_a_block_over_its_own_cap_is_dropped_and_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """함수 본문 하나가 `BODY_CHARS`를, 매크로 정의 하나가 `MACRO_CHARS`를 넘으면 단위가 빠지고
    사유 `block_cap`으로 적힌다 — 큰 것 하나가 전체 상한을 먹는 것을 막는 `callee`·정의 창과 같은
    장치다."""
    monkeypatch.setattr(source_closure, "BODY_CHARS", 10)
    monkeypatch.setattr(source_closure, "MACRO_CHARS", 10)

    closure, _, _ = _close(tmp_path, _MAIN, "use")

    assert closure.blocks == (
        "/* CLOSURE: xfree[macro] -> safe_free -> raw_free -> free"
        " (no definition in the repo index) */",
    )
    reasons = {e["chain"].split(" ")[0]: e["reason"] for e in closure.dropped}
    assert reasons == {"xmalloc[macro]": "block_cap", "CHECK_LEN[macro]": "block_cap",
                       "LOG_ERR[macro]": "block_cap"}


def test_the_event_lists_a_bounded_number_of_dropped_units_but_counts_all(
    tmp_path: Path,
) -> None:
    n = DROPPED_LISTED + 10
    defs = "".join(
        f"int impl{i}(int n) {{ if (n) return {i}; return 0; }}\n"
        f"static int w{i}(int n) {{ return impl{i}(n); }}\n" for i in range(n)
    )
    calls = "".join(f"    w{i}(n);\n" for i in range(n))
    files = {"a.c": f"{defs}int f(int n)\n{{\n{calls}    return 0;\n}}\n"}

    closure, _, _ = _close(tmp_path, files, "f", room=0)

    event = closure.event()
    assert event["closure_dropped"] == n
    assert len(json.loads(event["closure_dropped_units"])) == DROPPED_LISTED
    assert event["closure"] == 0


def test_without_a_v2_index_the_closure_says_it_did_not_look(tmp_path: Path) -> None:
    """조회가 `None`(인덱스 없음·v1)이면 단위 0건이고 이벤트가 그것을 밝힌다 — 「본 결과 0건」과
    「못 봤다」를 섞지 않는다."""
    root, _, graph = _repo(tmp_path, _MAIN)
    here = _symbol(graph, _MAIN, "use")
    read = partial(read_symbol_source, root)

    closure = build_closure(
        here, read(here), callables_of=lambda _names, _file: None, read=read, block=_block,
        blocks=[_block(here, read(here))], measure=len, room=100_000,
    )

    assert closure.blocks == () and closure.event()["closure_lookup"] == "none"
    seen_but_empty, _, _ = _close(tmp_path / "ok", {"a.c": "int f(void) { return 0; }\n"}, "f")
    assert seen_but_empty.event()["closure_lookup"] == "index"


def test_an_unreadable_definition_is_not_presented_as_a_body(tmp_path: Path) -> None:
    """인덱스는 가리키는데 원문이 비어 있으면(파일이 사라짐) 본문 없는 배너를 싣지 않는다 —
    사슬은 저장소에 정의가 없는 것으로 끝난다."""
    files = {"a.c": "int real(int n) { if (n) return 1; return 0; }\n"
                    "static int w(int n) { return real(n); }\n"
                    "int f(int n)\n{\n    return w(n);\n}\n"}
    root, _, graph = _repo(tmp_path, files)
    here = _symbol(graph, files, "f")
    real = _symbol(graph, files, "real")
    real_read = partial(read_symbol_source, root)

    def read(loc: Location) -> str:
        return "" if loc == real else real_read(loc)

    closure = build_closure(
        here, read(here), callables_of=graph.callables_of, read=read, block=_block,
        blocks=[_block(here, read(here))], measure=len, room=100_000,
    )

    assert _labels(closure) == ["/* CLOSURE: w -> real (no definition in the repo index) */"]


# --- 인덱스 조회 `GraphIndex.callables_of` -----------------------------------------------------


def test_callables_of_gives_macros_and_functions_with_their_parameters(tmp_path: Path) -> None:
    """이름 → (종류, 정의 위치, 파라미터). 매크로의 파라미터는 원문에서 읽으므로 비어 있다."""
    _, _, graph = _repo(tmp_path, _MAIN)

    found = graph.callables_of(["xmalloc", "safe_alloc", "raw_free", "memcpy"], "main.c")

    assert set(found) == {"xmalloc", "safe_alloc", "raw_free"}  # memcpy는 인덱스에 없다
    kind, where, params = found["safe_alloc"]
    assert (kind, where.symbol, where.file, params) == ("function", "safe_alloc", "wrap.h", ("n",))
    assert found["xmalloc"][0] == "macro" and found["xmalloc"][2] == ()
    assert found["xmalloc"][1] == Location(file="wrap.h", symbol="xmalloc", line_start=3,
                                           line_end=3)


def test_a_macro_shadows_a_function_of_the_same_name(tmp_path: Path) -> None:
    """전처리가 먼저라 같은 이름이 매크로이자 함수면 매크로다."""
    files = {"a.c": "static int dup(int n) { return n; }\n#define dup(n) other(n)\n"}
    _, _, graph = _repo(tmp_path, files)

    assert graph.callables_of(["dup"], "a.c")["dup"][0] == "macro"


def test_callables_of_prefers_the_near_file_and_marks_an_ambiguous_name(tmp_path: Path) -> None:
    """`definitions_of`·`callees_of`와 같은 규칙: 가까운 파일에 정의가 있으면 그것, 없으면
    저장소에서 **하나뿐일 때만** — 여럿이면 이름만으로 못 가리므로 고르지 않고 `ambiguous`로
    표시한다(결과에서 빼면 「정의가 없다」와 같아 보인다)."""
    files = {
        "a.c": "static int dup(int n) { return n; }\nstatic int only_a(void) { return 0; }\n",
        "b.c": "static int dup(int n) { return n + 1; }\nstatic int alone(void) { return 1; }\n",
        "c.c": "static int dup(int n) { return n + 2; }\n",
        "d.h": "#define MAC 1\n",
        "e.h": "#define MAC 2\n",
    }
    _, _, graph = _repo(tmp_path, files)

    assert graph.callables_of(["dup"], "b.c")["dup"][1].file == "b.c"
    assert graph.callables_of(["dup"], "z.c")["dup"][0] == "ambiguous"  # 셋 → 모호
    assert graph.callables_of(["MAC"], "a.c")["MAC"][0] == "ambiguous"  # 매크로도 같다
    assert graph.callables_of(["alone"], "a.c")["alone"][1].file == "b.c"  # 하나뿐이면 어디서든
    assert "nope" not in graph.callables_of(["nope"], "a.c")  # 없는 이름은 결과에 없다


def test_callables_of_is_none_without_v2_tables(tmp_path: Path) -> None:
    """index-v1(표 없음)과 인덱스 없음은 `None`이다 — 빈 `{}`(「봤는데 없다」)와 섞지 않는다."""
    v1 = {
        "version": "index-v1", "files": ["a.c"], "skipped": [], "symbols": [],
        "calls": [], "unresolved_calls": 0, "unresolved_symbols": 0, "parse_errors": [],
    }
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(v1), encoding="utf-8")

    assert GraphIndex(path).callables_of(["x"], "a.c") is None
    assert GraphIndex(tmp_path / "missing.json").callables_of(["x"], "a.c") is None
    _, _, graph = _repo(tmp_path / "v2", {"a.c": "int f(void) { return 0; }\n"})
    assert graph.callables_of(["x"], "a.c") == {}


# --- 러너 연결: 꺼짐은 바이트 동일, 켜짐은 블록을 더한다 ------------------------------------------

_THRESHOLDS = {
    "auditor_callee_hops": 0, "auditor_definitions": 0, "auditor_source_tokens": 0,
    "auditor_entry_trim": 0, "auditor_caller_lines": 0, "auditor_closure": 0,
}


def _auditor_source(tmp_path: Path, files: dict[str, str], anchor: str, **opts):  # noqa: ANN202
    """`_auditor_source`를 직접 부르는 최소 ctx — 실제 `GraphIndex`와 원문 읽기를 쓴다.
    (소스, 이벤트, 읽은 위치들)을 돌려준다."""
    root, _, graph = _repo(tmp_path, files)
    here = _symbol(graph, files, anchor)
    events: list[dict] = []
    reads: list[Location] = []

    def read(loc: Location) -> str:
        reads.append(loc)
        return read_symbol_source(root, loc)

    ctx = SimpleNamespace(
        symbols_for=graph.symbols_for,
        read_source=read,
        callees_of=opts.pop("callees_of", graph.callees_of),
        definitions_of=graph.definitions_of,
        call_sites_of=graph.call_sites_of,
        callables_of=opts.pop("callables_of", graph.callables_of),
        counter_for_role=lambda role: (lambda text: len(text.split())),
        config=SimpleNamespace(thresholds=SimpleNamespace(**{**_THRESHOLDS, **opts})),
        trace=SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    ref = SliceRef(slice_id="s", location=here, given_end="source", entry=here,
                   lines=((here.file, here.line_start + 1),), degraded=False)
    return runner._auditor_source(ctx, ref, here, "cand"), events[-1], reads


def test_option_off_never_asks_for_a_closure_and_keeps_the_event_shape(tmp_path: Path) -> None:
    """꺼짐(기본 0)이면 닫힘 조회를 **부르지 않고**(부르면 이 대역이 터진다) SOURCE는 앵커 함수
    그대로, 이벤트에 `closure*` 키가 없다 — 옵션 이전과 바이트 동일."""

    def boom(*_: object) -> None:
        raise AssertionError("꺼진 옵션이 닫힘 조회를 불렀다")

    source, event, _ = _auditor_source(tmp_path, _MAIN, "use", callables_of=boom)

    assert source == read_symbol_source(
        tmp_path / "repo", Location(file="main.c", line_start=16, line_end=26)
    )
    assert set(event) == {"slice_id", "functions", "dropped", "uncovered_lines", "chars",
                          "anchor_chars", "subject_kind", "subject_id", "stage"}


def test_option_on_appends_closure_blocks_and_reports_them(tmp_path: Path) -> None:
    """켜면 앵커 함수 뒤에 닫힘 단위가 붙고 이벤트에 `closure`·`closure_dropped`·
    `closure_dropped_units`·`closure_lookup`이 실린다. `functions`에는 안 든다(함수가 아니다)."""
    source, event, _ = _auditor_source(tmp_path, _MAIN, "use", auditor_closure=1)

    head, *_ = source.split("\n/* CLOSURE: ")
    assert head.startswith("/* ===== main.c:16-26 ===== */\n")
    assert re.findall(r"^/\* CLOSURE: (.*) \*/$", source, re.M) == [
        "xmalloc[macro] -> safe_alloc -> raw_alloc",
        "CHECK_LEN[macro]",
        "LOG_ERR[macro]",
        "xfree[macro] -> safe_free -> raw_free -> free (no definition in the repo index)",
    ]
    assert (event["functions"], event["closure"], event["closure_dropped"]) == (1, 4, 0)
    assert event["closure_dropped_units"] == "[]" and event["closure_lookup"] == "index"
    assert event["chars"] == len(source)


def test_the_closure_shares_the_overall_cap(tmp_path: Path) -> None:
    """같은 총 상한을 쓴다 — 몫은 `상한 − 지금까지 실린 것`이고 단위 하나는 이음 줄바꿈 1을 더해
    센다. 상한을 첫 단위가 딱 들어가는 값으로 잡으면 하나만 실리고, 하나 줄이면 첫 단위가 안
    실린다(러너 쪽 몫 계산이 하나라도 틀리면 여기서 걸린다). 상한은 모델 카운터(단어 수)로
    센다."""
    full, event, _ = _auditor_source(tmp_path / "full", _MAIN, "use", auditor_closure=1)
    anchor, first, *_ = re.split(r"\n(?=/\* CLOSURE: )", full)
    cap = len(anchor.split()) + 1 + len(first.split())

    carried, fits, _ = _auditor_source(
        tmp_path / "fits", _MAIN, "use", auditor_closure=1, auditor_source_tokens=cap
    )
    short_of, short, _ = _auditor_source(
        tmp_path / "short", _MAIN, "use", auditor_closure=1, auditor_source_tokens=cap - 1
    )

    assert event["closure"] == 4
    assert (fits["closure"], fits["closure_dropped"]) == (1, 3)
    assert carried.endswith(first)
    assert "/* CLOSURE: xmalloc" not in short_of and short["closure_dropped"] >= 1


def _direct(files: dict[str, str]) -> dict[str, str]:
    """앵커가 `raw_alloc`을 **직접도** 부르는 변형 — 피호출 창(`auditor_callee_hops`)이 그 본문을
    후보로 삼는다(`xmalloc` 사슬이 닿는 구현과 같은 함수다)."""
    return {**files, "main.c": files["main.c"].replace(
        "    memcpy(buf, src, len);", "    memcpy(buf, src, len);\n    raw_alloc(1);"
    )}


def test_the_closure_comes_before_the_caller_windows_and_after_the_callees(
    tmp_path: Path,
) -> None:
    """순서는 앵커 → (정의) → 피호출 → **닫힘** → 호출자 창이다 — 닫힘은 피호출 본문의 연장이라
    요청 빈도가 높고(호출부 창보다) 같은 상한 안에서 먼저 몫을 받는다."""
    files = {**_direct(_MAIN), "caller.c": "void up(void)\n{\n    use(\"x\", 1);\n}\n"}

    source, _, _ = _auditor_source(
        tmp_path, files, "use", auditor_closure=1, auditor_callee_hops=1, auditor_caller_lines=1
    )

    positions = [source.index(mark) for mark in
                 ("/* ===== main.c:16-", "/* ===== main.c:3-9", "/* CLOSURE: xmalloc",
                  "/* ===== caller.c:")]
    assert positions == sorted(positions)


def test_the_closure_counts_against_the_budget_the_caller_windows_get(tmp_path: Path) -> None:
    """닫힘이 상한을 다 먹으면 호출자 창이 그 뒤 몫으로 빠진다 — 러너가 닫힘 크기를 `size`에 안
    더하면 창이 상한을 넘어 실린다. 상한을 앵커 + 닫힘 네 단위에 딱 맞춘다(창은 못 든다)."""
    files = {**_MAIN, "caller.c": 'void up(void)\n{\n    use("x", 1);\n}\n'}
    full, _, _ = _auditor_source(tmp_path / "full", files, "use", auditor_closure=1)
    anchor, *units = re.split(r"\n(?=/\* CLOSURE: )", full)
    cap = len(anchor.split()) + sum(1 + len(unit.split()) for unit in units)

    _, event, _ = _auditor_source(
        tmp_path / "capped", files, "use", auditor_closure=1, auditor_caller_lines=1,
        auditor_source_tokens=cap,
    )

    assert event["closure"] == 4
    assert (event["callers"], event["callers_dropped"]) == (0, 1)


def test_a_caller_window_over_a_body_the_closure_carried_is_skipped(tmp_path: Path) -> None:
    """닫힘이 실은 `raw_alloc` 본문 안에 앵커(`use`)의 호출 지점이 있으면(상호 재귀) 그 호출자 창은
    이미 실린 범위와 겹쳐 빠진다 — 러너가 닫힘이 실은 위치를 `seen`에 안 더하면 같은 줄이 한 번 더
    실린다."""
    files = {**_MAIN}
    files["main.c"] = _MAIN_C.replace("    void *p = malloc(n);",
                                      "    void *p = malloc(n);\n    use(0, 0);")

    source, event, _ = _auditor_source(
        tmp_path, files, "use", auditor_closure=1, auditor_caller_lines=1
    )

    assert event["call_sites"] == 1 and event["callers"] == 0
    assert source.count("use(0, 0);") == 1


def test_a_macro_the_definitions_option_already_carried_is_not_repeated(tmp_path: Path) -> None:
    """`auditor_definitions`가 이미 실은 매크로 정의는 닫힘이 다시 안 싣는다. 전부 실렸고 따라갈
    사슬도 없으면(`LOG_ERR`) 단위 자체가 없고, 뿌리만 실렸으면(`CHECK_LEN`) 안 실린 중첩
    정의(`in_range`)만 붙는다 — 사슬이 2단 이상인 매크로가 닫힘이 보태는 몫이다."""
    source, event, _ = _auditor_source(
        tmp_path, _MAIN, "use", auditor_closure=1, auditor_definitions=1
    )

    assert "/* CLOSURE: LOG_ERR" not in source
    assert (
        "/* CLOSURE: CHECK_LEN[macro] (definition above) */\n/* ===== wrap.h:9-9 ===== */"
    ) in source
    assert source.count("#define LOG_ERR") == 1 and source.count("#define CHECK_LEN") == 1
    assert source.count("#define in_range") == 1
    assert event["closure_dropped"] == 0


def test_a_callee_body_the_cap_dropped_is_not_reported_as_shown_above(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """피호출 단계가 상한(`_AUDITOR_CALLEE_CHARS`)에 걸려 **뺀** `raw_alloc`을 닫힘이 「위에
    있다」고 적으면 거짓이다(러너의 `seen`은 뺀 것도 담는다 — 그래서 SOURCE의 배너로 판다).
    닫힘이 그 본문을 싣는다."""
    monkeypatch.setattr(runner, "_AUDITOR_CALLEE_CHARS", 1)

    source, event, _ = _auditor_source(
        tmp_path, _direct(_MAIN), "use", auditor_closure=1, auditor_callee_hops=1
    )

    assert event["callees_dropped"] >= 1
    assert "(body above)" not in source
    assert source.count("void *raw_alloc(size_t n)") == 1


def test_a_callee_body_already_carried_is_labelled_not_repeated(tmp_path: Path) -> None:
    """피호출 단계가 `raw_alloc`을 이미 실었으면(entry가 직접 부름) 닫힘은 사슬 줄만 싣고 위에
    있다고 적는다."""
    source, _, _ = _auditor_source(
        tmp_path, _direct(_MAIN), "use", auditor_closure=1, auditor_callee_hops=1
    )

    assert "/* CLOSURE: xmalloc[macro] -> safe_alloc -> raw_alloc (body above) */" in source
    assert source.count("void *raw_alloc(size_t n)") == 1


# --- 파이프라인 전체: 공장 → Deps → RunContext → 노드 --------------------------------------------


def _closure_on(config):  # noqa: ANN001, ANN202
    return config.transition(thresholds=config.thresholds.transition(auditor_closure=1))


def _closure_run(tmp_path: Path, **overrides):  # noqa: ANN202
    """실제 `cli.execute` 배선으로 슬라이스 후보 하나를 Auditor까지 보낸다 — `callables_of`를
    `RunContext`에 안 꽂으면 단위 대역은 초록이어도 여기서 빨개진다."""
    repo, document, _ = _repo(tmp_path / "pipeline", _MAIN)
    lines = {name: split_source(text.encode("utf-8")) for name, text in _MAIN.items()}
    rows = _MAIN_C.splitlines()
    head = next(n for n, row in enumerate(rows, 1) if row.startswith("int use("))
    sink = next(n for n, row in enumerate(rows, 1) if "memcpy(" in row)
    points = {("main.c", head), ("main.c", sink)}
    sliced = {
        "id": "slice_0200", "candidate_key": "key_slice_0200", "case": "B",
        "entry": {"file": "main.c", "line": sink, "symbol": "memcpy"},
        "members": ["key_slice_0200"], "absorbed": [],
        "lines": [{"file": f, "line": n} for f, n in sorted(points)],
        "code": assemble(points, lines), "_scope": "use",
    }
    path = _slices_file(tmp_path, [sliced])
    return _run(tmp_path, slices_path=path, target=repo, before=_with_index(document), **overrides)


def test_the_closure_reaches_the_auditor_prompt_through_the_real_wiring(tmp_path: Path) -> None:
    _, trace_path = _closure_run(tmp_path, configure=_closure_on)

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert "/* CLOSURE: xmalloc[macro] -> safe_alloc -> raw_alloc */" in facts["source"]
    assert "abort();" in facts["source"]
    (event,) = _source_events(trace_path)
    assert event["closure"] == 4 and event["closure_lookup"] == "index"


def test_the_default_run_carries_no_closure_through_the_same_wiring(tmp_path: Path) -> None:
    """같은 배선·같은 슬라이스를 옵션 없이 돌리면 닫힘이 없고 이벤트 모양이 옛 그대로다."""
    _, trace_path = _closure_run(tmp_path)

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert "CLOSURE" not in facts["source"]
    (event,) = _source_events(trace_path)
    assert set(event) == {"slice_id", "functions", "dropped", "uncovered_lines", "chars",
                          "anchor_chars"}


# --- 설정 · 가드 · 상수 -----------------------------------------------------------------------


def test_the_closure_option_is_off_by_default_and_a_switch() -> None:
    from pydantic import ValidationError

    from src.config import AUDITOR_SOURCE_OPTIONS, load_config

    thresholds = load_config("dev_fake").thresholds
    assert thresholds.auditor_closure == 0
    assert "auditor_closure" not in thresholds.flatten()  # 꺼짐이면 manifest 모양이 옛 그대로
    assert thresholds.transition(auditor_closure=1).flatten()["auditor_closure"] == 1.0
    assert "auditor_closure" in AUDITOR_SOURCE_OPTIONS
    with pytest.raises(ValidationError):
        thresholds.transition(auditor_closure=2)


def test_a_run_with_the_closure_on_is_held_by_a_prompt_that_does_not_describe_it() -> None:
    """프롬프트 판 meta의 `source_options`가 이 키를 들기 전에는 `cli`의 run·doctor가 막는다 —
    프롬프트 v7이 오기 전의 기대된 상태다(`_undescribed_source_options`가 그 판단이다)."""
    from src import cli
    from src.config import load_config

    config = load_config("dev_fake")
    on = config.transition(thresholds=config.thresholds.transition(auditor_closure=1))

    assert cli._undescribed_source_options(config) == []
    undescribed = cli._undescribed_source_options(on)
    assert [entry.split("(")[0] for entry in undescribed] == ["auditor_closure"]


def test_the_closure_block_caps_equal_the_callee_and_definition_caps() -> None:
    """닫힘의 블록 상한은 러너의 피호출·정의 상한과 **같은 값**이다 — 모듈이 러너를 import 할 수
    없어(순환) 숫자를 따로 들고 있으므로 갈리면 여기서 걸린다."""
    assert BODY_CHARS == runner._AUDITOR_CALLEE_CHARS
    assert MACRO_CHARS == runner._AUDITOR_DEFINITION_CHARS


def test_the_banner_a_block_starts_with_is_the_one_the_closure_reads_back() -> None:
    """닫힘은 SOURCE 블록의 첫 줄 배너(`file:start-end`)로 무엇이 이미 실렸는지 안다 — 러너의
    `_block` 형식이 바뀌면 여기서 걸린다."""
    where = Location(file="dir/a.c", symbol="f", line_start=7, line_end=9)

    assert source_closure.shown_ranges([_block(where, "a\nb\nc")]) == [
        Location(file="dir/a.c", line_start=7, line_end=9)
    ]
    assert source_closure.shown_ranges(["no banner here", "/* CLOSURE: a -> b */"]) == []
