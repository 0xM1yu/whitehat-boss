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
| 낯선 선언자 형태의 이름 | 문법마다 다르다 — `unresolved_symbols`로 **센다** |

`#if`/`#else` 양쪽 가지는 **둘 다** 낸다. 구문만으로는 어느 쪽이 컴파일되는지 알 수
없고, 한쪽을 고르면 그 선택이 근거 없는 추측이 된다. 같은 이름이 두 번 나오는 것이
사실이므로 읽는 쪽이 `line`으로 구별한다.

**놓치는 것을 조용히 버리지 않고 센다.** 호출부의 누락은 `unresolved_calls`, 심볼의
누락은 `unresolved_symbols`다. 그 값이 크면 그 저장소에서는 이 그래프의 커버리지가
낮다는 뜻이고, 그 사실이 보여야 E2 등급을 얼마나 믿을지 판단할 수 있다.

**정의를 어디서 찾는가**는 `_iter_definitions()`에 적었다. 파일의 최상위만 보면
`#ifdef` 블록·C++ 클래스 본문·`namespace`·`extern "C"` 안의 정의가 통째로 빠진다 —
실측으로 zlib(.c) 28.3%, nlohmann/json(.cpp) 70.3%가 그렇게 사라졌다.

## index-v2 — NE resolver가 쓸 조회 표 (#63)

v1 필드(`symbols`·`files`·`skipped`·`unresolved_*`·`parse_errors`)는 그대로 두고 조회 표를
더한다. 모양은 실험팀 Notion 「Scout RAG baseline」 §1.2를 따른다 — 다음 단계(NE resolver)가
그 명세를 이식한다.

| 표 | 모양 |
| --- | --- |
| `functions` | 이름 → [{file, line_start, line_end, params}] — `symbols`와 같은 정의 |
| `macros` | 이름 → [{file, line_start, line_end}] |
| `types` | 이름 → [{file, line_start, line_end, kind}] — struct·union·enum·typedef |
| `file_vars` | 이름 → [{file, line_start, line_end}] — 파일 스코프 선언문 |
| `fields` | 필드 → [{type, file, line_start, line_end}] — 그 필드를 선언한 타입과 그 범위 |
| `locals` | 파일 → 함수 → 이름 → [선언 줄] — 파라미터 포함 |
| `assigns` | 파일 → 함수 → 이름 → [대입 줄] |
| `field_assigns` | 필드 → [{file, line, func, lhs, rhs, op}] |
| `calls` | callee → [{file, line, caller, args}] — **v1의 간선 목록을 callee로 묶은 것** |

**`calls`만 모양이 바뀌었다.** 간선은 v1과 같다 — 읽는 쪽(`index_graph`)이 두 모양에서 같은
간선을 낸다. **원문은 싣지 않는다** — RAG baseline의 `src` 표와 매크로 `value`가 없다. 하네스가
파일에서 직접 읽으므로 사본을 두면 어긋날 자리만 생긴다. `lhs`·`rhs`·`args`는 요약용 조각이라
공백을 하나로 접는다.

노드 이름은 **실파싱으로 확인했다**(tree-sitter 0.26.0 · tree-sitter-c 0.24.2, 호스트 —
`tests/test_indexer.py`의 v2 절이 핀으로 든다). 추측했으면 틀렸을 것 셋: `preproc_def`의 끝이
다음 줄 0열이다(`_last_line`) · `parenthesized_declarator`에는 `declarator` 필드가 없다 ·
프로토타입도 `declaration`이다(`_declared_name`).

v2 표가 못 잡는 것 — 구문만 보기 때문이다:

| 놓치는 것 | 왜 |
| --- | --- |
| 매크로가 선언하는 변수(`DECLARE_BITMAP(bits, 64);`) | 전처리를 하지 않아 호출식으로 파싱된다 |
| 원소·역참조 대입(`buf[i] = x`)·증감(`i++`) | 그 이름에 대입한 것이 아니다 |
| 지정 초기화(`.read = my_read`) | 대입식이 아니다 — `field_assigns`에 안 든다 |
| enum 상수 | `types`에는 enum 정의만 든다 |
| C++ 구성(class·namespace·template) | C 문법 노드만 본다 — class 필드는 안 든다 |
| 대상 트리 밖 헤더(`generated_headers` 등) | 인덱서는 대상 트리 안만 받는다 |

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

# 출력 형식 버전. 읽는 쪽은 `src/adapters/index_graph.py`의 `SUPPORTED_INDEX_VERSIONS`이고,
# 거기 없는 값이면 그쪽이 `unsupported_version`으로 **거절한다** — 그래프 섹션이 통째로 사라지는데
# 그 모양은 "인덱서가 안 돌았다"와 같아서 캡슐만 봐서는 구별되지 않는다. 형식이 바뀌면
# 올리고, 두 값이 갈리지 않도록 `tests/test_indexer.py`가 대조한다.
INDEX_VERSION = "index-v2"

# 확장자 → tree-sitter 문법 모듈 이름. **`container_tools._INDEX_SUFFIXES`가 보내는
# 확장자를 전부 담아야 한다**(그쪽이 이 스크립트에 넘길 파일을 고른다). 여기가 더 넓은
# 것은 괜찮다 — `.hpp`는 알지만 아직 넘어오지 않는다(`.h`는 index-v2부터 넘어온다).
#
# **이 스크립트는 `src/`를 import 하지 않는다**(모듈 docstring). 그래서 목록이 양쪽에
# 따로 존재하고, 어긋나면 예외가 아니라 `skipped`에 이름만 남는다 — 즉 **조용히 빈
# 그래프가 나온다.** 그래서 `tests/test_indexer.py`와 이미지 빌드의 연막 검사
# (`Dockerfile`)가 양쪽을 각각 대조한다.
_GRAMMARS = {
    ".c": "tree_sitter_c",
    ".h": "tree_sitter_c",
    ".cc": "tree_sitter_cpp",
    ".cpp": "tree_sitter_cpp",
    ".hpp": "tree_sitter_cpp",
    # `.inc`에는 언어 힌트가 없다. C로 고정한 것은 **추측이다** — 실측 샘플
    # (`target.c` 28바이트 + `session_core.inc` 17KB)이 전부 C였다. C++ `.inc`가 오면
    # 파싱이 어긋나는데 그때도 조용하지는 않다: `has_parse_error`가 서고 이름을 못 뽑은
    # 정의는 `unresolved_symbols`가 센다. 그 값이 크면 이 매핑을 다시 봐야 한다는 뜻이다.
    ".inc": "tree_sitter_c",
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

# 선언자 사슬 끝의 이름 노드 — v2 표의 변수·파라미터·필드·typedef 이름. 실측(tree-sitter-c 0.24.2):
# 변수·파라미터 `identifier` · 구조체 필드 `field_identifier` · typedef 이름 `type_identifier`.
_DECLARED_NAMES = frozenset({"identifier", "field_identifier", "type_identifier"})

# 본문(`body`)이 있을 때 `types`에 드는 지정자와 그 `kind`.
_TYPE_SPECIFIERS = {
    "struct_specifier": "struct",
    "union_specifier": "union",
    "enum_specifier": "enum",
}

_MACROS = frozenset({"preproc_def", "preproc_function_def"})

# 이름 → 기록 목록 모양의 v2 표. `locals`·`assigns`는 파일 → 함수 → 이름 모양이라 따로 다룬다.
_NAMED_TABLES = ("functions", "macros", "types", "file_vars", "fields", "field_assigns")


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


def _text(node) -> str:
    return node.text.decode("utf-8", "replace")


def _squash(node) -> str:
    """요약용 원문 조각 — 공백·개행을 하나로 접는다. 원문 자체는 하네스가 파일에서 읽는다."""
    return " ".join(_text(node).split())


def _last_line(node) -> int:
    """노드의 마지막 줄(1부터).

    끝 지점이 **다음 줄 0열**이면 그 앞줄이다 — `preproc_def`는 끝나는 개행까지 품는다(실측:
    `#define LEN 16`이 1~2행). `end_point + 1`을 그대로 쓰면 매크로마다 무관한 다음 줄이 붙는다.
    """
    row, column = node.end_point
    return max(node.start_point[0] + 1, row + 1 if column else row)


def _span(node, relative: str) -> dict:
    return {"file": relative, "line_start": node.start_point[0] + 1, "line_end": _last_line(node)}


def _declared_name(declarator):
    """선언자 → `(이름 노드, 함수인가)`. 이름이 없으면 `None`.

    `declarator` 필드를 타고 이름 노드까지 내려가는데 두 가지를 실측으로 맞췄다:

    - **`parenthesized_declarator`에는 `declarator` 필드가 없다** — 안쪽 선언자가 첫 이름 있는
      자식이다. 필드만 따라가면 `void (*g_fp)(int)`의 이름을 못 찾는다.
    - **이름에 가장 가까운 선언자가 `function_declarator`면 함수다.** `int proto(int);`(함수)와
      `void (*fp)(int);`(함수를 가리키는 포인터 변수)가 둘 다 `declaration`이고 둘 다
      `function_declarator`를 품는데, 가르는 것은 이름 바로 바깥 겹이다.

    폭 0짜리 이름(자리 채움 비트필드 `int : 5;`의 `field_identifier`)은 이름이 아니다.
    """
    nearest = None
    node = declarator
    while node is not None:
        if node.type in _DECLARED_NAMES:
            return (node, nearest == "function_declarator") if node.text else None
        if node.type == "parenthesized_declarator":
            node = node.named_children[0] if node.named_children else None
            continue
        if node.type != "init_declarator":
            nearest = node.type
        node = node.child_by_field_name("declarator")
    return None


def _params(definition) -> list:
    """정의의 파라미터 이름 노드 — `_function_name`과 같은 사슬 위의 `function_declarator`의 것이다.

    그 사슬에는 `function_declarator`가 하나뿐이다: 함수 포인터를 돌려주는 정의
    (`int (*f(int))(int)`)는 사슬이 `parenthesized_declarator`에서 끊겨 `_function_name`이 이름을
    못 뽑고, 그런 정의는 여기 오지 않는다(`unresolved_symbols`가 센다). K&R 머리는 파라미터가
    `identifier`로 오고, `(void)`·가변 인자·이름 없는 파라미터는 이름이 없다.
    """
    node = definition.child_by_field_name("declarator")
    nearest = None
    while node is not None and node.type not in _NAME_NODES:
        if node.type == "function_declarator":
            nearest = node
        node = node.child_by_field_name("declarator")
    parameters = nearest.child_by_field_name("parameters") if nearest is not None else None
    names = []
    for param in parameters.named_children if parameters is not None else ():
        if param.type == "identifier":
            names.append(param)
        elif param.type == "parameter_declaration":
            found = _declared_name(param.child_by_field_name("declarator"))
            if found is not None:
                names.append(found[0])
    return names


def _args(call) -> list[str]:
    """호출 인자 — 인자 목록 안의 주석은 이름 있는 `comment` 노드라(실측) 거른다."""
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [_squash(arg) for arg in arguments.named_children if arg.type != "comment"]


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
                    "args": _args(node),
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
    `container_tools._INDEX_SUFFIXES`에 `.cc`/`.cpp`가 있으므로 그 파일들은 실제로
    여기 넘어온다.

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


def _add(table: dict, key: str, record: dict) -> None:
    table.setdefault(key, []).append(record)


def _add_line(scoped: dict, func: str, name: str, node) -> None:
    scoped.setdefault(func, {}).setdefault(name, set()).add(node.start_point[0] + 1)


def _lookup_tables(root, relative: str) -> dict:
    """파일 하나의 v2 조회 표 중 트리를 한 번 훑어 얻는 것 — `functions`·`calls`는 v1 심볼·호출과
    같은 자리(`index_file`)에서 만든다. 같은 정의를 두 곳에서 찾으면 두 목록이 어긋날 자리가 생긴다.

    스택에 `(노드, 함수, 필드 주인)`을 싣는다.

    - **함수** — `None`이면 파일 스코프, `""`이면 이름을 못 뽑은 정의 안이다. 그 안의 지역·대입은
      버린다(v1도 그 정의를 `unresolved_symbols`로 세고 호출을 버린다). 중첩 정의는 바깥 정의의
      것으로 센다 — `_walk_calls`와 같다.
    - **필드 주인** — `(이름, 범위)`. 이름 있는 struct·union이면 그것, 이름이 없으면 감싼 typedef,
      그것도 없으면 바깥 주인을 물려받는다(익명 union 멤버는 바깥 struct로 접근한다). 끝까지
      없으면 이름이 `None`이다.
    """
    tables: dict = {
        name: {} for name in ("macros", "types", "fields", "file_vars", "field_assigns")
    }
    scoped: dict = {"locals": {}, "assigns": {}}
    stack = [(root, None, None)]
    while stack:
        node, func, owner = stack.pop()
        kind = node.type
        if kind in _MACROS:
            name = node.child_by_field_name("name")
            if name is not None:
                _add(tables["macros"], _text(name), _span(node, relative))
            continue
        if kind == "function_definition" and func is None:
            func = _function_name(node) or ""
            for param in _params(node) if func else ():
                _add_line(scoped["locals"], func, _text(param), param)
        elif kind in _TYPE_SPECIFIERS and node.child_by_field_name("body") is not None:
            name = node.child_by_field_name("name")
            if name is not None:
                span = _span(node, relative)
                _add(tables["types"], _text(name), {**span, "kind": _TYPE_SPECIFIERS[kind]})
                owner = (_text(name), span)
            elif owner is None:
                owner = (None, _span(node, relative))
        elif kind == "type_definition":
            span = _span(node, relative)
            names = [
                found[0]
                for declarator in node.children_by_field_name("declarator")
                if (found := _declared_name(declarator)) is not None
            ]
            for name in names:
                _add(tables["types"], _text(name), {**span, "kind": "typedef"})
            if names:
                owner = (_text(names[0]), span)
        elif kind == "field_declaration" and owner is not None:
            for declarator in node.children_by_field_name("declarator"):
                found = _declared_name(declarator)
                if found is not None and not found[1]:
                    _add(tables["fields"], _text(found[0]), {"type": owner[0], **owner[1]})
        elif kind == "declaration":
            for declarator in node.children_by_field_name("declarator"):
                found = _declared_name(declarator)
                if found is None or found[1]:
                    continue  # 이름 없음 · 프로토타입
                if func is None:
                    _add(tables["file_vars"], _text(found[0]), _span(node, relative))
                elif func:
                    _add_line(scoped["locals"], func, _text(found[0]), found[0])
        elif kind == "assignment_expression" and func:
            _record_assignment(node, func, relative, tables["field_assigns"], scoped["assigns"])
        stack.extend((child, func, owner) for child in reversed(node.children))
    return {**tables, **scoped}


def _record_assignment(node, func: str, relative: str, field_assigns: dict, assigns: dict) -> None:
    """좌변이 이름이면 `assigns`, 필드면 `field_assigns`. 원소·역참조 대입은 어느 쪽도 아니다.

    연산자를 든다 — `b->x -= i`를 `lhs`·`rhs`만으로 적으면 「x에 i를 넣었다」로 읽힌다.
    """
    left = node.child_by_field_name("left")
    if left is None:
        return
    if left.type == "identifier":
        _add_line(assigns, func, _text(left), node)
        return
    field = left.child_by_field_name("field") if left.type == "field_expression" else None
    right = node.child_by_field_name("right")
    operator = node.child_by_field_name("operator")
    if field is None or right is None or operator is None:
        return
    _add(
        field_assigns,
        _text(field),
        {
            "file": relative,
            "line": node.start_point[0] + 1,
            "func": func,
            "lhs": _squash(left),
            "rhs": _squash(right),
            "op": _text(operator),
        },
    )


def index_file(path: Path, relative: str) -> dict:
    """파일 하나의 심볼·호출과 v2 조회 표.

    읽거나 파싱하지 못하면 `OSError`/`KeyError`가 올라간다.
    """
    source = path.read_bytes()
    tree = _parser_for(path.suffix).parse(source)

    symbols: list[dict] = []
    calls: list[dict] = []
    functions: dict[str, list[dict]] = {}
    unresolved = 0
    unnamed = 0
    for node in _iter_definitions(tree.root_node):
        name = _function_name(node)
        if name is None:
            # 정의는 분명히 있는데 이름을 못 뽑았다. 버리되 **센다** — 그냥 넘기면
            # "그 파일에 함수가 없다"와 구별되지 않는다(`unresolved_calls`와 같은 이유).
            unnamed += 1
            continue
        symbol = {
            "name": name,
            "file": relative,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
        }
        symbols.append(symbol)
        # `functions`는 **같은 정의·같은 범위**에 파라미터를 더한 것이다 — 따로 찾지 않는다.
        _add(
            functions,
            name,
            {
                "file": relative,
                "line_start": symbol["line_start"],
                "line_end": symbol["line_end"],
                "params": [_text(param) for param in _params(node)],
            },
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
        "functions": functions,
        **_lookup_tables(tree.root_node, relative),
    }


def _by_callee(calls: list[dict]) -> dict[str, list[dict]]:
    """v1의 간선 목록을 callee → 호출 지점으로 묶는다. **간선은 그대로다** — 모양만 바뀐다."""
    grouped: dict[str, list[dict]] = {}
    for call in sorted(
        calls, key=lambda c: (c["file"], c["line"], c["caller"], c["callee"], c["args"])
    ):
        site = {key: call[key] for key in ("file", "line", "caller", "args")}
        _add(grouped, call["callee"], site)
    return grouped


def _record_order(record: dict) -> tuple:
    """표 안 기록의 순서. 파일·줄이 먼저고, 같은 자리의 기록은 내용 전체로 가른다."""
    line = record.get("line_start", record.get("line", 0))
    return record["file"], line, json.dumps(record, sort_keys=True)


def build_index(paths: list[Path], root: Path) -> dict:
    """여러 파일의 결과를 합친다.

    **정렬이 계약이다.** 파일 순회도 결과 목록도 정렬하지 않으면 같은 입력에 다른
    index가 나오고, 그러면 캡슐이 달라져 재현성이 깨진다(engineering-guide.md §5).
    """
    symbols: list[dict] = []
    calls: list[dict] = []
    named: dict[str, dict] = {name: {} for name in _NAMED_TABLES}
    scoped: dict[str, dict] = {"locals": {}, "assigns": {}}
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
        for name, table in named.items():
            for key, records in result[name].items():
                table.setdefault(key, []).extend(records)
        for name, per_file in scoped.items():
            for func, names in result[name].items():
                for var, lines in names.items():
                    per_file.setdefault(relative, {}).setdefault(func, {}).setdefault(
                        var, set()
                    ).update(lines)
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
        # v1은 간선 목록이었다. v2는 callee로 묶고 인자를 더했다 — 간선은 같다(모듈 docstring).
        "calls": _by_callee(calls),
        "unresolved_calls": unresolved,
        # 정의는 찾았는데 이름을 못 뽑은 수. `unresolved_calls`가 호출부의 누락을
        # 세듯 이쪽은 **심볼의 누락**을 센다. 둘 다 0이 아니면 그 저장소에서 이
        # 그래프의 커버리지가 낮다는 뜻이고, E2를 얼마나 믿을지는 그것을 보고 판단한다.
        "unresolved_symbols": unnamed,
        "parse_errors": sorted(parse_errors),
        **{
            name: {key: sorted(records, key=_record_order) for key, records in table.items()}
            for name, table in named.items()
        },
        **{
            name: {
                file: {
                    func: {var: sorted(lines) for var, lines in names.items()}
                    for func, names in funcs.items()
                }
                for file, funcs in per_file.items()
            }
            for name, per_file in scoped.items()
        },
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
    # `calls`는 **간선 수**다 — v2의 `calls`는 callee로 묶였으므로 키 수가 아니다.
    edges = sum(len(sites) for sites in index["calls"].values())
    print(
        f"# index: {len(index['symbols'])} symbols, {edges} calls, "
        f"{index['unresolved_calls']} unresolved calls, "
        f"{index['unresolved_symbols']} unresolved symbols, {len(index['files'])} files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
