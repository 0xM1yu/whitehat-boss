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
| 함수 포인터 호출 `(*fp)(x)` | 실행 시점에 정해진다 — `unresolved_calls`로 **센다**(v3 아래) |
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
| `fields` | 필드 → [{type, file, line_start, line_end}] — 선언한 타입(없으면 `null`)과 범위 |
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

## index-v3 — 쓰기 지점과 함수 포인터 바인딩 (#304)

v2 표는 **한 글자도 안 바뀐다**(`assigns`는 여전히 이름 대입만이다). 표 셋을 더했을 뿐인데
버전을 올린 것은 읽는 쪽이 「이 표가 없다」(v2 산출물)와 「표는 있는데 0건」을 가르기 위해서다 —
`index_graph.table()`이 v2 문서에서 새 표를 `None`으로 낸다.

| 표 | 모양 |
| --- | --- |
| `writes` | 기저 이름 → [{file, line, func, lhs, rhs, op, form, base}] |
| `fn_bindings` | 필드 → [{file, line, func, fn, form}] — 함수 포인터 필드에 묶인 함수 이름 |
| `field_calls` | 필드 → [{file, line, caller, args, expr}] — `x->f(...)` 꼴 간접 호출 지점 |

- **`writes`의 `form`** — `element`(`buf[i] = x`) · `deref`(`*p = x`) · `init`(함수 안 선언
  초기화 `char *q = malloc(n)` — `locals`는 선언 줄만 들고 초기값 유무를 모른다). 이름 대입
  (`p = malloc(n)`)은 `assigns`에 있으므로 여기 다시 싣지 않는다.
- **`writes`의 `base`** — 기저가 이름이면 `name`, 필드면 `field`(`vv->buf[i] = x`의 키는 `buf`).
  둘을 한 키에 섞으므로 읽는 쪽이 가른다. 기저는 첨자·역참조·증감·괄호·캐스트·`+`/`-`의 왼쪽을
  벗겨 찾는다 — `*(p + 1) = 2`·`*p++ = c`는 `p`다(`+`의 왼쪽이 수 상수면 오른쪽 — `*(1 + p)`).
  못 찾으면(`*f() = x`) 싣지 않는다.
- **`fn_bindings`의 `form`** — `assign`(`o->alloc = my_alloc;` · `&my_alloc`) · `designated`
  (`.free_ctx = sshpam_free_ctx`). 값의 캐스트·괄호는 벗긴다(`(cb_t)my_alloc`).
  **`fn`이 `functions` 표에 정의로 있는 이름일 때만** 싣는다 — `o->len = n;`이 바인딩으로
  오인되지 않게. 그래서 대상 트리 밖에 정의된 함수(`.free = free`)는 안 든다.
  `func`는 파일 스코프 초기화면 `null`이다.
- **`field_calls`** — 호출식의 함수 자리가 필드(`vv->f`·`s.f`·`(*vv->f)`)인 호출. v1 이래
  `unresolved_calls`가 세던 것의 일부를 이름째 남긴다 — **그 수는 그대로 센다.**

v2·v3 표가 못 잡는 것 — 구문만 보기 때문이다:

| 놓치는 것 | 왜 |
| --- | --- |
| 매크로가 선언하는 변수(`DECLARE_BITMAP(bits, 64);`) | 전처리를 하지 않아 호출식으로 파싱된다 |
| 증감(`i++`·`buf[i]++`) | 대입식이 아니다 — 어느 표에도 안 든다(`*p++ = x`는 대입식이라 든다) |
| 삼항으로 고르는 바인딩(`o->f = c ? a : b`) | 값이 이름 하나가 아니다 — `fn_bindings`에 안 든다 |
| 위치 초기화(`{ my_init, my_free }`) | 필드 순서를 타입 정의에서 찾아야 한다 |
| 함수 포인터 변수·배열 호출(`fp(x)`) | 필드가 아니다 — `calls`에 `fp`로 들고 정의가 없다 |
| 같은 이름 필드의 주인 | 필드 이름만 키다 — 읽는 쪽이 `fields` 하나뿐일 때만 저장소 전체로 잇는다 |
| enum 상수 | `types`에는 enum 정의만 든다 — ⏵ index-v4가 `enum_constants`로 싣는다 |
| C++ 구성(class·namespace·template) | C 문법 노드만 본다 — class 필드는 안 든다 |
| 대상 트리 밖 헤더(`generated_headers` 등) | 인덱서는 대상 트리 안만 받는다 |

## index-v4 — include 그래프 · enum 상수 · 앞선 선언 표시

v2·v3 표의 행은 **하나만 빼고 그대로다** — `types`의 앞선 선언 typedef 행에 `forward`가 붙는다.
표 둘을 더했다. 버전을 올린 것은 v3 때와 같은 이유다 — 읽는 쪽이 「표가 없다」(v3 이하 산출물)와
「0건」을 가른다.

| 표 | 모양 |
| --- | --- |
| `includes` | **파일** → [{line, path, system}] — `#include` 지시문. `<x.h>`면 `system` 참 |
| `enum_constants` | 이름 → [{file, line_start, line_end, enum}] — 범위는 **그 enum 정의 전체** |

- **`includes`** — 경로는 따옴표·꺾쇠 안 원문 그대로다(정규화하지 않는다 — 파일로 푸는 것은 읽는 쪽
  `index_graph` 몫). `#if` 양쪽 가지의 지시문을 **둘 다** 싣는다(심볼과 같은 원칙). 지시문이 없는
  파일은 키가 없다. 매크로로 계산하는 경로(`#include MACRO_H`)는 경로가 없어 안 싣는다.
- **`enum_constants`의 범위** — `typedef enum {…} T;`면 그 `typedef` 문 전체, 아니면 enum 지정자다.
  같은 enum의 상수는 범위가 같다 — 읽는 쪽이 겹치는 범위를 한 번만 싣는다(`runner._overlaps`).
  `enum`은 태그 이름, 익명이면 typedef 이름, 둘 다 없으면 `null`이다.
- **함수 본문 안 enum의 상수는 안 싣는다** — `int f(void) { enum { A }; … }`의 `A`는 그 함수의
  지역이다. 싣고 읽는 쪽이 같은 파일 우선으로 고르면 다른 함수가 헤더의 같은 이름 `A`를 쓰는
  자리에 그 지역 값이 붙는다(#485 리뷰). **지역 판별은 구문 성질이다**: 선언자 사슬에
  `function_declarator`가 있고 `type`이 `class`·`namespace` 식별자가 아닌
  `function_definition`의 본문(함수 try 블록 포함)이거나 람다 안이다. 파일·`namespace`·클래스·
  `extern "C"` 본문과 반환형의 enum은 싣는다. `compound_statement`를 지나는지만 보면 안 된다 —
  tree-sitter가 `namespace n {…}`·`class K {…}`를 `function_definition`(`compound_statement`
  본문)으로 읽기도 한다: `.h`는 C 문법이라 C++ 헤더가 전부 그렇고 `.cpp`도 `class API K {`처럼
  매크로가 낀 머리가 그렇다(실파싱 — 선언자가 이름 하나). 머리에 **함수형 매크로 호출**이 끼면
  (`class ALIGNAS(16) K {` · `namespace std _GLIBCXX_VISIBILITY(default) {` — `.h`·`.c`) 선언자가
  `function_declarator`가 되므로 선언자만 봐서는 안 되고 `type` 식별자(`class`·`namespace`)를
  먼저 본다. **못 빼는 지역(미탐 — 의심스러우면 행을 남긴다, 예전 동작):** 함수로 안 읽히거나
  사슬을 못 따라가는 머리의 본문 — `.c`·`.h`의 `TEST(a, b) { enum { L }; }`(C 문법은 `ERROR`
  뒤의 떠도는 블록으로 읽는다 · `.cpp`는 함수로 읽어 뺀다) · 함수 밖 초기화식의 GNU 문 표현식
  `int v = ({ enum { S }; S; });` · 참조 반환(`int& f()` · `S& S::operator=(…)` —
  `reference_declarator`에 `declarator` 필드가 없어 사슬이 끊긴다) · 변환 연산자
  `operator int() const` · 초기화 목록이 딸린 함수 try 블록 `S::S() try : x(1) {…}`.
- **`types`의 `forward`** — `typedef struct X X;`처럼 **본문 없는** struct·union·enum을 가리키고
  **이름이 그 태그와 같은** 맨 별칭 typedef 행 중, **같은 파일에** 그 이름의 struct·union·enum
  본문 행이 있는 것에만 `"forward": true`가 붙는다(없으면 거짓). 그 행은 정의가 아니라 같은 파일의
  본문이 이미 말한 앞선 선언이다 — 읽는 쪽(`index_graph._definition_rows`)이 같은 전제를 다시 보고
  본문 행만 고른다. 그 밖은 안 붙고 **행이 그대로 남는다**: 태그와 이름이 다른 별칭
  (`typedef struct Private Public;`) · 포인터·배열 선언자(`typedef struct X *X;`) · `const` 등
  한정자 · 본문이 다른 파일에 있는 불투명 타입. 이 행들은 본문 행이 대신하지 못하는 사실을 든다 —
  지우면 엉뚱한 정의가 붙는다. 불투명 타입(`api.h`의 `typedef struct Y Y;` + `priv.h`의 본문)은
  두 행이 남아 읽는 쪽이 앵커 파일의 행, 아니면 include 닫힘에 든 행이 하나뿐일 때 그것을
  싣는다 — 둘 다 닿거나 둘 다 안 닿으면 안 붙는다(index-v3와 같다). 본문 없는 `struct X;`와
  프로토타입은 v2부터 어느 표에도 안 든다(실파싱 확인).

노드 모양은 **실파싱으로 확인했다**(tree-sitter 0.26.0 · tree-sitter-c 0.24.2 · tree-sitter-cpp
0.23.4, 호스트). 추측했으면 틀렸을 것 셋:

- `preproc_include`의 경로는 `path` 필드이고 `string_literal`·`system_lib_string`·
  `identifier`(매크로) 중 하나다. **`string_literal`의 `string_content`를 이어 붙이면 안 된다** —
  역슬래시가 `escape_sequence`로 쪼개진다(`"a\\b.h"`). include 경로는 이스케이프를 안 푸므로
  따옴표 안 원문을 쓴다.
- enum 상수는 `enumerator_list` 바로 아래만이 아니라 **`#ifdef` 안에도** 있다(`preproc_ifdef` 자식).
- `typedef struct Z {…} Z;`는 `types`에 **같은 범위의 행 둘**(struct `Z` · typedef `Z`)을 낸다. 같은
  사실이므로 읽는 쪽이 범위로 합친다(인덱서는 v2 모양대로 둘 다 낸다).

v4가 못 잡는 것:

| 놓치는 것 | 왜 |
| --- | --- |
| 매크로로 계산한 include 경로 | 전처리를 하지 않는다 — 지시문 자체를 안 싣는다 |
| `-I`로 바뀌는 include 해석 | 빌드 설정을 안 본다 — 읽는 쪽이 경로 접미로 근사한다 |
| 대상 트리 밖 헤더(libc 등) | 인덱서는 대상 트리 안만 받는다 — 푼 파일이 없다 |
| `typedef struct _X X;`에서 `X` → `struct _X` 본문 | 태그와 이름이 다르면 안 잇는다 |
| 함수 안 `struct`·`union`·`enum` 태그·`typedef`의 지역성 | v2 이래 `types`에 든다 — 아래 문단 |
| 파일 범위 람다 본문의 변수 | v2 이래 `file_vars`에 든다 — 아래 문단 |
| 지역 판별이 못 보는 함수의 enum 상수 | 지역인데 든다 — 위 불릿 |

함수 안 타입 정의를 `types`에서 **안 뺀 것은 한계다.** NE resolver가 `struct pkt { … } p;` 같은
지역 변수의 타입을 `types`에서 찾아 체인을 잇는다(`symbol_resolver._type_name_from_line`) — 빼면
그 경로가 끊긴다. 그래서 정의 조회(`index_graph.definitions_of`)가 다른 함수의 같은 이름 사용에
그 지역 정의를 붙일 수 있다. 같은 태그를 파일 범위 불투명 선언(`typedef struct X X;`)과 함수 안
정의(`void f(void) { struct X {…} x; }`)에 둘 다 쓰면 함수 안 `struct`도 `types`의 `struct`
행이라 `forward`가 달리고, 읽는 쪽이 올바른 typedef 행을 지워 지역 struct가 돌아온다(수정 전과
같다 · 미해결). enum 상수는 v4가 새로 더한 표라 처음부터 함수 본문 안을 안 싣는다.

`file_vars`의 지역 변수도 같은 줄의 한계다: 파일 범위 람다 본문의 변수가 든다(HEAD 동작). 지역을
빼는 가드는 시험해서 되돌렸다 — 쪼개진 함수로 읽힌 파일의 실제 파일 범위 변수까지 지웠고(로컬
코퍼스 — D103) `symbol_resolver`가 같은 표를 전역 변수 폴백으로 읽는다. 이 PR 범위 밖이다.

## 종료 코드

`container_tools.judge()`가 `index`를 "exit 0이면 ok"로 판정한다. 그래서 **인덱서
자신이 실패했을 때만** non-zero다.

심볼 0개는 실패가 아니다 — 헤더만 있는 대상이나 구조체 정의뿐인 파일이 정상적으로
그럴 수 있다. 다만 그 경우 **빈 그래프로 E2가 열린다**는 문제가 남는데, 그것은 이
스크립트가 아니라 게이트 쪽 판단이므로 open-questions D13에 적었다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 출력 형식 버전. 읽는 쪽은 `src/adapters/index_graph.py`의 `SUPPORTED_INDEX_VERSIONS`이고,
# 거기 없는 값이면 그쪽이 `unsupported_version`으로 **거절한다** — 그래프 섹션이 통째로 사라지는데
# 그 모양은 "인덱서가 안 돌았다"와 같아서 캡슐만 봐서는 구별되지 않는다. 형식이 바뀌면
# 올리고, 두 값이 갈리지 않도록 `tests/test_indexer.py`가 대조한다.
INDEX_VERSION = "index-v4"

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

# C 문법이 `class K {…}`·`namespace n {…}`를 `function_definition`으로 읽을 때 `type` 자리에 오는
# 식별자(`_is_function`). `struct`·`union`은 지정자 노드로 읽혀 여기 안 온다.
_SCOPE_KEYWORDS = frozenset({"class", "namespace"})

# `types`에서 본문 있는 태그 행의 `kind` — 앞선 선언 typedef 행(`kind: "typedef"`)을 뺄 근거다.
_TAG_KINDS = frozenset(_TYPE_SPECIFIERS.values())

# 정의 기록·필드 owner 추적에 쓰는 지정자. C++ `class`(`class_specifier`)는 멤버를 소유하고
# `struct`처럼 타입 이름으로도 쓰이지만 tree-sitter가 따로 태그해서 지금까지 전혀 기록되지
# 않았다 — 클래스 멤버가 owner 없이 `fields`에서 누락됐다(C++로 파싱되는 `.cc`/`.cpp`/`.hpp`,
# 그리고 `.h`를 C++로 올린 뒤의 헤더). `struct`로 기록하면 그 멤버가 잡히고 클래스가 타입으로
# 풀린다. `_TYPE_SPECIFIERS`의 다른 용도(`_TAG_KINDS`·`_forward_tag`)는 건드리지 않는다.
_RECORD_SPECIFIERS = {**_TYPE_SPECIFIERS, "class_specifier": "struct"}

_MACROS = frozenset({"preproc_def", "preproc_function_def"})

# 이름 → 기록 목록 모양의 v2~v4 표. `locals`·`assigns`는 파일 → 함수 → 이름 모양이라 따로 다룬다.
_NAMED_TABLES = (
    "functions",
    "macros",
    "types",
    "file_vars",
    "fields",
    "field_assigns",
    "writes",
    "fn_bindings",
    "field_calls",
    "enum_constants",
)

# 원소·역참조 좌변의 바깥 겹 → `writes`의 `form`(index-v3, #304).
_WRITE_FORMS = {"subscript_expression": "element", "pointer_expression": "deref"}


# `.h`는 C 문법에 고정돼 있지만 C++ 프로젝트는 클래스·네임스페이스·템플릿을 `.h` 헤더에 둔다.
# C 문법은 그것들을 ERROR로 만들어 클래스 본문과 그 안의 멤버·타입이 통째로 유실된다. 강한
# C++ 구조 마커가 있을 때만 그 헤더를 C++ 문법으로 올린다 — 마커가 없는 순수 C 헤더는 C 그대로라
# C 프로젝트 회귀가 없다.
_CPP_HEADER_MARKERS = re.compile(
    rb"\bnamespace\s+[\w:]+\s*\{|\bclass\s+\w+\s*[:{]|\btemplate\s*<|^\s*(?:public|private|protected)\s*:",
    re.MULTILINE,
)


def _grammar_module(suffix: str, source: bytes) -> str:
    """파싱에 쓸 tree-sitter 문법 모듈 이름. `.h`에 C++ 구조 마커가 있으면 C++로 올린다."""
    module = _GRAMMARS[suffix]
    if module == "tree_sitter_c" and suffix == ".h" and _CPP_HEADER_MARKERS.search(source):
        return "tree_sitter_cpp"
    return module


def _parser_for(module_or_suffix: str):
    """파서. 인자는 문법 모듈 이름(`tree_sitter_c`)이거나, 하위호환으로 확장자(`.c`)다 —
    확장자면 `_GRAMMARS`로 모듈 이름을 찾는다. 문법 모듈은 이미지에 함께 설치돼 있다."""
    from importlib import import_module

    from tree_sitter import Language, Parser

    module = import_module(_GRAMMARS.get(module_or_suffix, module_or_suffix))
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

    **재귀하지 않는다.** 노드마다 자기를 부르던 판은 깊게 중첩된 코드(긴 `else if` 사슬 ·
    길게 이은 식)에서 Python 재귀 한도를 넘어 `RecursionError`로 인덱스 전체를 잃었다 —
    2026-10-04 GT 직접 모드 FT 데이터 생성(6,745표본)에서 인덱스 실패 147개(이슈 #492 본문이
    밝힌 수 — run ID가 없고 원자료가 저장소 밖이라 여기서는 재현할 수 없다). 명시적 스택으로 돈다.

    **방문 순서는 출력에 영향이 없다.** 스택은 예전 재귀와 같은 순서(부모 먼저, 자식은 앞쪽부터)로
    돌지만 그 순서는 약속이 아니다(2026-10-05 `git grep` 기준 `index_file`을 부르는 곳은
    `build_index` 하나이고, 그것이 `_by_callee`에서 `calls`를 (파일 · 줄 · caller · callee ·
    인자)로 정렬해 내보낸다). `index_graph`의 「첫 호출 줄 순」도 줄의 최솟값으로 스스로 정렬한다.
    """
    unresolved = 0
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            target = current.child_by_field_name("function")
            if target is not None and target.type == "identifier":
                calls.append(
                    {
                        "caller": caller,
                        "callee": target.text.decode("utf-8", "replace"),
                        "file": relative,
                        "line": current.start_point[0] + 1,
                        "args": _args(current),
                    }
                )
            else:
                unresolved += 1
        stack.extend(reversed(current.children))
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
    줄인다(실측 최대 깊이는 zlib 31, json 128로 여유가 있었다). 그러나 재귀 한도(기본 1,000)를
    넘는 코드가 실제로 있다 — 이슈 #492가 밝힌 인덱스 실패 147개이고, 로컬 재현은 5,000단 `else if`
    사슬 · 3,000항 덧셈식이 재귀 순회(`_walk_calls`)를 `RecursionError`로 죽인 것이다(합성 입력이라
    실제 깊이를 말하지는 않는다). 이 순회와 `_lookup_tables`는 처음부터 스택이라 같은 입력에서도
    안 죽는다.
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

    스택에 `(노드, 함수, 필드 주인, 함수 본문 안인가)`를 싣는다.

    - **함수 본문 안인가** — 선언자 사슬에 `function_declarator`가 있고 `type`이
      `class`·`namespace` 식별자가 아닌 `function_definition`의 `body` 필드 아래이거나 람다 안이면
      참이다(`_is_function`). 함수 본문 안 enum의 상수는 `enum_constants`에 싣지 않는다.
      반환형·파라미터는 본문이 아니고, `namespace`·클래스 본문도 아니다 — tree-sitter가 그것들을
      `function_definition`으로 읽어도 선언자에 `function_declarator`가 없거나(`class K {`), 있어도
      `type`이 그 키워드다(`class ALIGNAS(16) K {`). `compound_statement`를 지났는지만 보면 그
      상수가 샌다.
    - **함수** — `None`이면 파일 스코프, `""`이면 이름을 못 뽑은 정의 안이다. 그 안의 지역·대입은
      버린다(v1도 그 정의를 `unresolved_symbols`로 세고 호출을 버린다). 중첩 정의는 바깥 정의의
      것으로 센다 — `_walk_calls`와 같다.
    - **필드 주인** — `(이름, 범위)`. 이름 있는 struct·union이면 그것이다. 이름이 없으면
      typedef의 타입일 때 그 typedef, C11 익명 멤버일 때 바깥 주인을 물려받고(`_lends_owner`),
      그 밖에는 이름이 `None`이고 범위가 그 지정자다.
    """
    tables: dict = {
        name: {}
        for name in (
            "macros",
            "types",
            "fields",
            "file_vars",
            "field_assigns",
            "writes",
            "fn_bindings",
            "field_calls",
            "enum_constants",
        )
    }
    scoped: dict = {"locals": {}, "assigns": {}}
    includes: list[dict] = []
    # 앞선 선언 후보 — 본문이 이 파일에 있는지는 트리를 다 훑어야 안다(아래).
    forward_candidates: list[tuple[str, dict]] = []
    stack = [(root, None, None, False)]
    while stack:
        node, func, owner, local = stack.pop()
        kind = node.type
        if kind == "lambda_expression":
            local = True
        if kind in _MACROS:
            name = node.child_by_field_name("name")
            if name is not None:
                _add(tables["macros"], _text(name), _span(node, relative))
            continue
        if kind == "preproc_include":
            _record_include(node, includes)
            continue
        if kind == "function_definition" and func is None:
            func = _function_name(node) or ""
            for param in _params(node) if func else ():
                _add_line(scoped["locals"], func, _text(param), param)
        elif kind in _RECORD_SPECIFIERS and node.child_by_field_name("body") is not None:
            name = node.child_by_field_name("name")
            span = _span(node, relative)
            if name is not None:
                _add(tables["types"], _text(name), {**span, "kind": _RECORD_SPECIFIERS[kind]})
                owner = (_text(name), span)
            elif not _lends_owner(node):
                owner = (None, span)
            # 함수 본문 안 enum의 상수는 그 함수의 지역이다 — 파일 범위 표에 넣으면 다른 함수의 같은
            # 이름 사용에 그 값이 붙는다(#485 리뷰). 파일·namespace·클래스 본문의 상수는 싣는다.
            if kind == "enum_specifier" and not local:
                _record_enumerators(node, relative, tables["enum_constants"])
        elif kind == "type_definition":
            span = _span(node, relative)
            declared = [
                (found[0], declarator.type == "type_identifier")
                for declarator in node.children_by_field_name("declarator")
                if (found := _declared_name(declarator)) is not None
            ]
            tag = _forward_tag(node)
            for name, bare in declared:
                row = {**span, "kind": "typedef"}
                if bare and _text(name) == tag:
                    forward_candidates.append((_text(name), row))
                else:
                    _add(tables["types"], _text(name), row)
            if declared:
                owner = (_text(declared[0][0]), span)
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
                    _record_init(declarator, found[0], func, relative, tables["writes"])
        elif kind == "assignment_expression" and func:
            _record_assignment(node, func, relative, tables, scoped["assigns"])
        elif kind == "initializer_pair" and func != "":
            _record_designated(node, func, relative, tables["fn_bindings"])
        elif kind == "call_expression" and func:
            _record_field_call(node, func, relative, tables["field_calls"])
        # 진짜 함수(`_is_function` — `class`·`namespace` 머리가 아니다)의 본문은 지역이다. `body`
        # 필드째 — 함수 try 블록의 `try_statement`도 본문이다. 반환형·파라미터는 파일 범위다.
        body = node.child_by_field_name("body") if _is_function(node) else None
        stack.extend(
            (child, func, owner, local or (body is not None and child == body))
            for child in reversed(node.children)
        )
    # 앞선 선언 표시(index-v4 `forward`) — 같은 파일에 그 태그의 본문 행이 있을 때만 단다. 읽는 쪽
    # (`index_graph._definition_rows`)이 같은 전제로 그 행을 뺀다: 본문이 없거나 다른 파일에 있으면
    # 이 행이 그 이름의 유일한 사실일 수 있다.
    for name, row in forward_candidates:
        tagged = any(other["kind"] in _TAG_KINDS for other in tables["types"].get(name, ()))
        _add(tables["types"], name, {**row, "forward": True} if tagged else row)
    return {**tables, **scoped, "includes": sorted(includes, key=lambda row: row["line"])}


def _is_function(node) -> bool:
    """`node`가 진짜 함수 정의인가 — `function_definition`이고 선언자 사슬에 `function_declarator`가
    있고, `type`이 C++ 범위 키워드(`class`·`namespace`)로 읽힌 식별자가 아니다.

    tree-sitter는 `namespace n {…}`·`class K {…}`를 `function_definition`(`compound_statement`
    본문)으로 읽기도 한다 — `.h`는 C 문법이라 C++ 헤더가 전부 그렇고, `.cpp`도 `class API K {`처럼
    매크로가 낀 머리가 그렇다(실파싱 — 선언자가 `function_declarator` 없이 이름 하나다). 그 본문의
    상수는 파일·namespace·클래스 범위라 지역이 아니다.

    **머리에 함수형 매크로 호출이 끼면 선언자가 `function_declarator`가 된다** —
    `class ALIGNAS(16) K {` · `class _GLIBCXX_VISIBILITY(default) K {` ·
    `namespace std _GLIBCXX_VISIBILITY(default) {`(`.h`·`.c`, C 문법; 실파싱). 선언자만 보면
    함수로 오판하므로 `type` 필드가 `class`·`namespace` 식별자인지 먼저 본다 — 키워드가 반환형
    이름일 수 없다. `struct`·`union`은 안 본다: C·C++ 문법 모두 `struct_specifier`·
    `union_specifier`로 읽어 식별자가 되지 않는다(실파싱).

    사슬은 `_declared_name`과 같이 `declarator` 필드를 따르고, 괄호 선언자는 첫 이름 있는 자식으로
    건넌다 — 최외곽이 괄호인 `int (f(void)) {…}`에서만 필요하다(`int (*f(int))(int)`는 최외곽이
    `function_declarator`다).
    """
    if node.type != "function_definition":
        return False
    returned = node.child_by_field_name("type")
    if returned is not None and returned.type == "type_identifier":
        if _text(returned) in _SCOPE_KEYWORDS:
            return False
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type == "function_declarator":
            return True
        if declarator.type == "parenthesized_declarator":
            declarator = declarator.named_children[0] if declarator.named_children else None
        else:
            declarator = declarator.child_by_field_name("declarator")
    return False


def _forward_tag(definition) -> str | None:
    """`typedef struct X …;`가 가리키는 태그 이름 `X` — 앞선 선언 후보의 조건 둘을 본다.

    태그가 있고 **본문이 없는** struct·union·enum 지정자여야 하고, `const` 같은 한정자가 안
    붙어야 한다(`typedef const struct X X;`는 본문이 말하지 않는 것을 더한다). 선언자가 맨
    이름인지·이름이 태그와 같은지는 부르는 쪽이 이름마다 본다.
    """
    aliased = definition.child_by_field_name("type")
    if (
        aliased is None
        or aliased.type not in _TYPE_SPECIFIERS
        or aliased.child_by_field_name("body") is not None
        or any(child.type == "type_qualifier" for child in definition.named_children)
    ):
        return None
    tag = aliased.child_by_field_name("name")
    return _text(tag) if tag is not None else None


def _record_include(node, includes: list[dict]) -> None:
    """`#include "a/b.h"`·`#include <b.h>`를 싣는다(index-v4). 경로는 구분자 안 원문 그대로다.

    `string_literal`의 `string_content`를 이으면 역슬래시가 `escape_sequence`로 빠진다(실파싱) —
    include 경로는 이스케이프를 안 푸므로 따옴표 안을 그대로 쓴다. 매크로 경로(`identifier`·
    호출식)와 빈 경로는 안 싣는다.
    """
    path = node.child_by_field_name("path")
    if path is None or path.type not in ("string_literal", "system_lib_string"):
        return
    inner = _text(path)[1:-1]
    if inner:
        includes.append(
            {
                "line": node.start_point[0] + 1,
                "path": inner,
                "system": path.type == "system_lib_string",
            }
        )


def _record_enumerators(specifier, relative: str, enum_constants: dict) -> None:
    """본문 있는 enum 지정자의 상수를 `enum_constants`에 싣는다(index-v4).

    범위는 그 enum 정의 전체다 — `typedef enum {…} T;`면 typedef 문, 아니면 지정자. 상수 하나만
    보여 주면 값(`= 3`)과 이웃 상수가 빠진다. `#ifdef` 안 상수도 enum 본문의 자손이라 같이 훑는다
    (실파싱 — `preproc_ifdef` 자식으로 온다). enum 안에 enum은 없으므로 깊이를 막지 않는다.
    """
    body = specifier.child_by_field_name("body")
    if body is None:
        return
    parent = specifier.parent
    whole = parent if parent is not None and parent.type == "type_definition" else specifier
    tag = specifier.child_by_field_name("name")
    enum = None
    if tag is not None:
        enum = _text(tag)
    elif whole is not specifier:
        for declarator in whole.children_by_field_name("declarator"):
            found = _declared_name(declarator)
            if found is not None:
                enum = _text(found[0])
                break
    record = {**_span(whole, relative), "enum": enum}
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == "enumerator":
            name = node.child_by_field_name("name")
            if name is not None:
                _add(enum_constants, _text(name), dict(record))
            continue
        stack.extend(reversed(node.children))


def _lends_owner(specifier) -> bool:
    """이름 없는 struct·union이 바깥 주인을 그대로 물려받는가.

    둘뿐이다: typedef의 타입일 때(주인은 그 typedef 이름 — `type_definition` 갈래가 먼저 정했다)와
    **선언자 없는 필드**일 때(C11 익명 멤버 `union { int b; };` — `o.b`로 바깥에서 닿는다).
    이름 있는 필드의 익명 타입(`struct { int d; } named;`)은 아니다 — `d`는 `o.named.d`로만 닿으므로
    주인이 바깥 struct가 아니다(#63 코드리뷰가 잡았다).
    """
    parent = specifier.parent
    if parent is None:
        return False
    if parent.type == "type_definition":
        return True
    return parent.type == "field_declaration" and not parent.children_by_field_name("declarator")


def _record_assignment(node, func: str, relative: str, tables: dict, assigns: dict) -> None:
    """좌변이 이름이면 `assigns`, 필드면 `field_assigns`, 원소·역참조면 `writes`(index-v3, #304).

    연산자를 든다 — `b->x -= i`를 `lhs`·`rhs`만으로 적으면 「x에 i를 넣었다」로 읽힌다.
    필드에 `=`로 함수 이름을 넣으면 `fn_bindings`에도 든다(`_bound_function`).
    """
    left = node.child_by_field_name("left")
    if left is None:
        return
    if left.type == "identifier":
        _add_line(assigns, func, _text(left), node)
        return
    right = node.child_by_field_name("right")
    operator = node.child_by_field_name("operator")
    if right is None or operator is None:
        return
    record = {
        "file": relative,
        "line": node.start_point[0] + 1,
        "func": func,
        "lhs": _squash(left),
        "rhs": _squash(right),
        "op": _text(operator),
    }
    if left.type in _WRITE_FORMS:
        base = _write_base(left)
        if base is not None:
            _add(
                tables["writes"],
                _text(base),
                {
                    **record,
                    "form": _WRITE_FORMS[left.type],
                    "base": "field" if base.type == "field_identifier" else "name",
                },
            )
        return
    field = left.child_by_field_name("field") if left.type == "field_expression" else None
    if field is None:
        return
    _add(tables["field_assigns"], _text(field), record)
    bound = _bound_function(right)
    if bound is not None and record["op"] == "=":
        _add(
            tables["fn_bindings"],
            _text(field),
            {
                **{key: record[key] for key in ("file", "line", "func")},
                "fn": bound,
                "form": "assign",
            },
        )


def _is_deref(node) -> bool:
    """`pointer_expression`이 역참조(`*`)인가 — 주소 연산(`&`)도 같은 노드다(실측)."""
    operator = node.child_by_field_name("operator")
    return operator is not None and operator.type == "*"


def _write_base(node):
    """원소·역참조 좌변의 기저 이름 노드(`identifier`·필드의 `field_identifier`). 없으면 `None`.

    벗기는 겹(실파싱 — tree-sitter-c 0.24.2): 첨자(`buf[i][j]`)·역참조(`**pp`)·증감
    (`*p++`·`*--p` — `update_expression`)은 `argument` 필드, 캐스트(`((char *)p)[2]`)는
    `value` 필드, 괄호는 필드가 없어 첫 이름 있는 자식, 포인터 산술(`*(p + 1)`)은 `+`·`-`의
    왼쪽 — 단 `+`의 왼쪽이 수 상수면 오른쪽이다(`*(1 + p)`). 타입을 안 보므로 `*(i + p)`는 `i`로
    적는다. 필드에서 멈춘다 — `vv->buf[i]`의 기저는 필드 `buf`다. 그 밖(`*f() = x` · `&x`)은
    이름을 지어내지 않는다.
    """
    while node is not None:
        kind = node.type
        if kind == "identifier":
            return node
        if kind == "field_expression":
            return node.child_by_field_name("field")
        if kind in ("subscript_expression", "update_expression") or (
            kind == "pointer_expression" and _is_deref(node)
        ):
            node = node.child_by_field_name("argument")
        elif kind == "parenthesized_expression":
            node = node.named_children[0] if node.named_children else None
        elif kind == "cast_expression":
            node = node.child_by_field_name("value")
        elif kind == "binary_expression" and _operator_is(node, ("+", "-")):
            left = node.child_by_field_name("left")
            if left is not None and left.type == "number_literal" and _operator_is(node, ("+",)):
                node = node.child_by_field_name("right")
            else:
                node = left
        else:
            return None
    return None


def _operator_is(node, operators: tuple[str, ...]) -> bool:
    operator = node.child_by_field_name("operator")
    return operator is not None and operator.type in operators


def _bound_function(value) -> str | None:
    """대입·초기화 값이 함수 이름 하나(`f`·`&f`)면 그 이름. 함수인지는 `build_index`가 본다.

    캐스트(`(cb_t)f`)·괄호(`(f)`)는 벗긴다 — 시그니처가 다른 콜백을 캐스트해 묶는 꼴이 흔하다.
    삼항(`c ? f : g`)·호출 결과는 이름 하나가 아니라 안 싣는다.
    """
    while value is not None:
        if value.type == "cast_expression":
            value = value.child_by_field_name("value")
        elif value.type == "parenthesized_expression":
            value = value.named_children[0] if value.named_children else None
        elif value.type == "pointer_expression" and not _is_deref(value):
            value = value.child_by_field_name("argument")
        else:
            break
    return _text(value) if value is not None and value.type == "identifier" else None


def _record_init(declarator, name, func: str, relative: str, writes: dict) -> None:
    """함수 안 선언 초기화(`char *q = malloc(n);`)를 `writes`에 `init`으로 싣는다.

    `locals`는 선언 줄만 든다 — 할당 지점이 선언문에 붙어 있으면 그 줄이 쓰기인지 표만 봐서는
    모른다. 초기값 없는 선언자(`int j;`)는 안 싣는다.
    """
    if declarator.type != "init_declarator":
        return
    value = declarator.child_by_field_name("value")
    if value is None:
        return
    _add(
        writes,
        _text(name),
        {
            "file": relative,
            "line": declarator.start_point[0] + 1,
            "func": func,
            "lhs": _squash(declarator.child_by_field_name("declarator")),
            "rhs": _squash(value),
            "op": "=",
            "form": "init",
            "base": "name",
        },
    )


def _record_designated(node, func: str | None, relative: str, fn_bindings: dict) -> None:
    """지정 초기화 `.free_ctx = sshpam_free_ctx`의 값이 함수 이름이면 `fn_bindings`에 싣는다.

    지정자가 여럿(`.a.b = f`)이면 마지막 필드다. 첨자 지정자(`[0] = f`)는 필드가 아니라 안 싣는다.
    중첩 목록(`.in = { .cb = f }`)은 스택이 안쪽 `initializer_pair`를 따로 만난다.
    """
    designators = node.children_by_field_name("designator")
    value = node.child_by_field_name("value")
    if not designators or value is None or designators[-1].type != "field_designator":
        return
    field = next(
        (child for child in designators[-1].named_children if child.type == "field_identifier"),
        None,
    )
    bound = _bound_function(value)
    if field is None or bound is None:
        return
    _add(
        fn_bindings,
        _text(field),
        {
            "file": relative,
            "line": node.start_point[0] + 1,
            "func": func,
            "fn": bound,
            "form": "designated",
        },
    )


def _record_field_call(node, func: str, relative: str, field_calls: dict) -> None:
    """함수 자리가 필드인 호출(`vv->f(…)`·`s.f(…)`·`(*vv->f)(…)`)을 필드 이름으로 싣는다.

    `_walk_calls`는 이 호출을 `unresolved_calls`로 센다 — 그 수는 그대로 두고 이름만 더 남긴다.
    """
    expr = node.child_by_field_name("function")
    target = expr
    while target is not None and target.type in ("parenthesized_expression", "pointer_expression"):
        if target.type == "pointer_expression":
            target = target.child_by_field_name("argument") if _is_deref(target) else None
        else:
            target = target.named_children[0] if target.named_children else None
    if target is None or target.type != "field_expression":
        return
    field = target.child_by_field_name("field")
    if field is None:
        return
    _add(
        field_calls,
        _text(field),
        {
            "file": relative,
            "line": node.start_point[0] + 1,
            "caller": func,
            "args": _args(node),
            "expr": _squash(expr),
        },
    )


def index_file(path: Path, relative: str) -> dict:
    """파일 하나의 심볼·호출과 v2 조회 표.

    읽거나 파싱하지 못하면 `OSError`/`KeyError`가 올라간다.
    """
    source = path.read_bytes()
    tree = _parser_for(_grammar_module(path.suffix, source)).parse(source)

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
    includes: dict[str, list[dict]] = {}
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
        if result["includes"]:
            includes[relative] = result["includes"]
        unresolved += result["unresolved_calls"]
        unnamed += result["unresolved_symbols"]
        if result["has_parse_error"]:
            parse_errors.append(relative)
        indexed.append(relative)

    # 함수 이름이 아닌 값(`o->len = n`)은 바인딩이 아니다 — 정의가 저장소에 있는 이름만 남긴다.
    named["fn_bindings"] = {
        field: kept
        for field, records in named["fn_bindings"].items()
        if (kept := [record for record in records if record["fn"] in named["functions"]])
    }

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
        # 파일 → `#include` 지시문(index-v4). 지시문이 없는 파일은 키가 없다.
        "includes": dict(sorted(includes.items())),
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
