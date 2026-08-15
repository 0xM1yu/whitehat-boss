#!/usr/bin/env python3
"""tree-sitter로 심볼 표와 호출 그래프를 뽑는다. 컨테이너 안에서 도는 독립 스크립트다.

파이프라인 1단계(`nodes/index.py`)가 `ContainerToolRunner`를 통해 이 파일을 부른다.
그전에는 `clang -fsyntax-only`였는데 **그것은 구문 검사일 뿐 그래프가 아니었다.**
include 경로 설정이 필요한 실제 저장소에서는 그 명령이 실패해 `Evidence(ok=False)`가
되고, `gate.py`가 `usable = [ev for ev in evidences if ev.ok]`로 거르므로 **모든
Finding이 E2를 넘지 못했다.** tree-sitter는 전처리도 링크도 하지 않아 include 경로가
없어도 파싱되므로 그 벽이 사라진다.

**`src/`를 import 하지 않는다.** 이 스크립트는 이미지 안에서 살고 하네스는 호스트에서
산다 — 둘은 파일 하나(`--out`이 쓰는 JSON)로만 만난다. 그래서 형식이 양쪽에 따로
존재하고, 어긋나면 조용히 빈 캡슐이 나온다. `tests/test_indexer.py`가 이 스크립트의
출력을 `INDEX_VERSION`과 함께 대조해 그 어긋남을 막는다.

## 이 그래프가 못 잡는 것

tree-sitter는 **구문만** 본다. 전처리도 타입 해석도 하지 않는다.

| 놓치는 것 | 왜 |
| --- | --- |
| 함수 포인터 호출 `(*fp)(x)` | 대상이 실행 시점에 정해진다 — `unresolved_calls`로 **센다** |
| 매크로로 감싼 호출 | 전처리를 하지 않는다 |
| 같은 이름의 `static` 함수 | 파일 스코프를 구분하지 않는다 |

**놓치는 것을 조용히 버리지 않고 센다.** `unresolved_calls`가 크면 그 저장소에서는
이 그래프의 커버리지가 낮다는 뜻이고, 그 사실이 보여야 E2 등급을 얼마나 믿을지
판단할 수 있다.

## 종료 코드

`container_tools.judge()`가 `index`를 "exit 0이면 ok"로 판정한다. 그래서 **인덱서
자신이 실패했을 때만** non-zero다.

심볼 0개는 실패가 아니다 — 헤더만 있는 대상이나 구조체 정의뿐인 파일이 정상적으로
그럴 수 있다. 다만 그 경우 **빈 그래프로 E2가 열린다**는 문제가 남는데, 그것은 이
스크립트가 아니라 게이트 쪽 판단이므로 open-questions D13에 적었다.
"""

import argparse
import json
import sys
from pathlib import Path

# 출력 형식 버전. 읽는 쪽(`HybridRetriever`)과 어긋나면 조용히 빈 캡슐이 나오므로
# 형식이 바뀌면 올리고, 읽는 쪽이 모르는 버전은 거절한다.
INDEX_VERSION = "index-v1"

# 확장자 → tree-sitter 문법 모듈 이름. `fake_tools._SOURCE_SUFFIXES`의 C/C++ 부분과
# 같아야 한다 — 그쪽이 `compile_sources`를 고르므로 여기 없는 확장자가 넘어오면
# 파싱하지 못하고 조용히 빠진다.
_GRAMMARS = {
    ".c": "tree_sitter_c",
    ".h": "tree_sitter_c",
    ".cc": "tree_sitter_cpp",
    ".cpp": "tree_sitter_cpp",
    ".hpp": "tree_sitter_cpp",
}


# `declarator` 사슬의 끝에 오는 이름 노드. **문법이 정해주는 값이라 실측했다**
# (tree-sitter-c 0.24.2 / tree-sitter-cpp 0.23.4):
#
#     void f(void){}                      identifier          'f'
#     char *g(void){}                     identifier          'g'
#     int ZEXPORT deflate(int a){}        identifier          'deflate'
#     class P{ void m(void){} };          field_identifier    'm'
#     void P::parse(char *s){}            qualified_identifier 'P::parse'
#     P::P(void){}                        qualified_identifier 'P::P'
#     class P{ ~P(void){} };              destructor_name     '~P'
#     class P{ bool operator==(...){} };  operator_name       'operator=='
#
# `identifier` 하나만 보면 C++ 쪽이 전부 `None`이 되고, 호출부가 그것을 `continue`로
# 넘겨 심볼과 그 함수의 호출 간선이 함께 사라진다.
_NAME_NODES = frozenset(
    {
        "identifier",
        "field_identifier",
        "qualified_identifier",
        "destructor_name",
        "operator_name",
    }
)


def _parser_for(suffix: str):
    """확장자에 맞는 파서. 문법 모듈은 이미지에 함께 설치돼 있다."""
    from importlib import import_module

    from tree_sitter import Language, Parser

    module = import_module(_GRAMMARS[suffix])
    return Parser(Language(module.language()))


def _function_name(definition) -> str | None:
    """`function_definition` → 함수 이름.

    `declarator` 필드를 타고 내려간다. 반환형이 포인터면 `pointer_declarator`가 한 겹
    더 끼므로 이름 노드가 나올 때까지 반복한다.

    끝에 오는 노드 타입은 문법이 정해주고 C와 C++이 다르다. 실측한 것이 `_NAME_NODES`다.
    """
    node = definition.child_by_field_name("declarator")
    while node is not None and node.type not in _NAME_NODES:
        node = node.child_by_field_name("declarator")
    return node.text.decode("utf-8", "replace") if node is not None else None


def _walk_calls(node, caller: str, relative: str, calls: list[dict]) -> int:
    """`node` 아래의 호출을 모은다. 반환값은 대상을 특정하지 못한 호출 수.

    `call_expression`의 `function` 필드가 `identifier`면 직접 호출이다. 그 외
    (`parenthesized_expression` 등)는 함수 포인터나 표현식 호출이라 구문만으로는
    대상을 알 수 없다 — 이름을 지어내지 않고 센다.
    """
    unresolved = 0
    if node.type == "call_expression":
        target = node.child_by_field_name("function")
        if target is not None and target.type == "identifier":
            calls.append(
                {
                    "caller": caller,
                    "callee": target.text.decode("utf-8", "replace"),
                    "file": relative,
                    "line": node.start_point[0] + 1,
                }
            )
        else:
            unresolved += 1
    for child in node.children:
        unresolved += _walk_calls(child, caller, relative, calls)
    return unresolved


def _iter_definitions(root):
    """트리 전체에서 `function_definition`을 **원문 순서로** 낸다.

    최상위(`root_node.children`)만 보면 실제 저장소의 함수 상당수가 통째로 빠진다.
    zlib(.c 43파일)에서 488개 중 138개(28.3%)가 그랬고, 그 138개가 있던 자리는:

    | 부모 노드 | 개수 | 무엇인가 |
    | --- | --- | --- |
    | `ERROR` | 66 | `int ZEXPORT deflate(...)` — 전처리되지 않은 매크로가 낀 시그니처 |
    | `preproc_ifdef` | 57 | `#ifdef`로 감싼 함수 |
    | `preproc_else`·`preproc_if`·`preproc_elif` | 15 | 조건부 컴파일의 나머지 가지 |

    C++은 더 심하다. nlohmann/json(.cpp 423파일)에서 1247개 중 877개(70.3%)가 빠졌고
    자리는 `field_declaration_list`(클래스 본문)·`declaration_list`(namespace)·
    `linkage_specification`(`extern "C"`)·`template_declaration`이다.
    `_COMPILE_SUFFIXES`에 `.cc`/`.cpp`가 있으므로 그 파일들은 실제로 여기 넘어온다.

    **찾은 정의 안으로는 들어가지 않는다.** 본문 안의 호출은 `_walk_calls`가 그 정의의
    것으로 세므로, 중첩 정의까지 별도 심볼로 올리면 같은 호출이 두 번 잡힌다.

    재귀 대신 스택을 쓴다 — 파스 트리 깊이가 파이썬 재귀 한계에 걸리는 자리를 하나
    줄인다(실측 최대 깊이는 zlib 31, json 128로 여유가 있지만 공짜로 없앨 수 있다).
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            yield node
            continue
        # pop()이 끝에서 꺼내므로 역순으로 넣어야 원문 순서가 된다.
        stack.extend(reversed(node.children))


def index_file(path: Path, relative: str) -> dict:
    """파일 하나의 심볼·호출. 읽거나 파싱하지 못하면 `OSError`/`KeyError`가 올라간다."""
    source = path.read_bytes()
    tree = _parser_for(path.suffix).parse(source)

    symbols: list[dict] = []
    calls: list[dict] = []
    unresolved = 0
    unnamed = 0
    for node in _iter_definitions(tree.root_node):
        name = _function_name(node)
        if name is None:
            # 정의는 분명히 있는데 이름을 못 뽑았다. 버리되 **센다** — 그냥 넘기면
            # "그 파일에 함수가 없다"와 구별되지 않는다(`unresolved_calls`와 같은 이유).
            unnamed += 1
            continue
        symbols.append(
            {
                "name": name,
                "file": relative,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
            }
        )
        body = node.child_by_field_name("body")
        if body is not None:
            unresolved += _walk_calls(body, name, relative, calls)

    return {
        "symbols": symbols,
        "calls": calls,
        "unresolved_calls": unresolved,
        "unresolved_symbols": unnamed,
        # 깨진 코드도 tree-sitter는 ERROR 노드를 끼워 넣고 계속 파싱한다. 부분 결과가
        # 여전히 쓸모 있으므로 실패로 만들지 않고 사실만 남긴다.
        "has_parse_error": tree.root_node.has_error,
    }


def build_index(paths: list[Path], root: Path) -> dict:
    """여러 파일의 결과를 합친다.

    **정렬이 계약이다.** 파일 순회도 결과 목록도 정렬하지 않으면 같은 입력에 다른
    index가 나오고, 그러면 캡슐이 달라져 재현성이 깨진다(engineering-guide.md §5).
    """
    symbols: list[dict] = []
    calls: list[dict] = []
    unresolved = 0
    unnamed = 0
    parse_errors: list[str] = []
    indexed: list[str] = []
    skipped: list[str] = []

    for path in sorted(paths):
        relative = _relative(path, root)
        if path.suffix not in _GRAMMARS:
            skipped.append(relative)
            continue
        result = index_file(path, relative)
        symbols += result["symbols"]
        calls += result["calls"]
        unresolved += result["unresolved_calls"]
        unnamed += result["unresolved_symbols"]
        if result["has_parse_error"]:
            parse_errors.append(relative)
        indexed.append(relative)

    return {
        "version": INDEX_VERSION,
        "files": sorted(indexed),
        # 파싱할 문법이 없어 건너뛴 파일. 조용히 빠지면 "그 파일에 함수가 없다"와
        # 구별되지 않는다.
        "skipped": sorted(skipped),
        "symbols": sorted(symbols, key=lambda s: (s["file"], s["line_start"], s["name"])),
        "calls": sorted(calls, key=lambda c: (c["file"], c["line"], c["caller"], c["callee"])),
        "unresolved_calls": unresolved,
        # 정의는 찾았는데 이름을 못 뽑은 수. `unresolved_calls`가 호출부의 누락을
        # 세듯 이쪽은 **심볼의 누락**을 센다. 둘 다 0이 아니면 그 저장소에서 이
        # 그래프의 커버리지가 낮다는 뜻이고, E2를 얼마나 믿을지는 그것을 보고 판단한다.
        "unresolved_symbols": unnamed,
        "parse_errors": sorted(parse_errors),
    }


def _relative(path: Path, root: Path) -> str:
    """컨테이너 마운트 기준 상대경로. 절대경로를 남기면 실행마다 값이 달라진다."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path("/src"))
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args(argv)

    try:
        index = build_index(args.sources, args.root)
    except (OSError, ImportError) as exc:
        # 인덱서 자신이 실패한 경우만 non-zero다(모듈 docstring). judge()가 exit 0을
        # ok로 보므로, 여기서 0을 내면 빈 그래프가 성공으로 기록된다.
        print(f"indexer: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # 로그에 남는 한 줄. `Evidence.digest`가 로그의 sha256이므로 이 요약이 기록에 들어간다.
    print(
        f"# index: {len(index['symbols'])} symbols, {len(index['calls'])} calls, "
        f"{index['unresolved_calls']} unresolved calls, "
        f"{index['unresolved_symbols']} unresolved symbols, {len(index['files'])} files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
