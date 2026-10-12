"""저장소 밖 상수·타입의 값 — 이 장비의 시스템 헤더를 C 전처리기로 읽는다.

- **매크로**: `gcc -E -dM`이 낸 `#define` 줄 그대로. 값이 다른 매크로를 쓰면 그것도 끝까지 붙인다
  (`PATH_MAX` → 값, `INT_MAX` → `__INT_MAX__` → 값).
- **타입**: `gcc -E -P` 결과를 tree-sitter로 읽어 typedef·태그 정의를 찾고, typedef 사슬을 기본
  타입까지 따라간다(`uint8_t` → `__uint8_t` → `unsigned char`) — 오염값 타입의 폭이 거기서 나온다.

어느 헤더를 읽나: 슬라이스 파일들이 include로 닿는 범위의 시스템 include(`closure.system_headers`)
에 `DEFAULT_HEADERS`를 더한다 — 저장소 헤더가 다른 경로로 끌어오는 것을 놓치지 않게.

⚠ **값은 이 장비의 플랫폼 것이다** — `PATH_MAX`는 Linux 4096, macOS 1024다. 블록 머리에 플랫폼과
컴파일러를 적는다(`describe`). 못 여는 헤더(다른 OS 전용 등)는 빼고 나머지로 읽는다.
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_HEADERS = (
    "stddef.h", "stdint.h", "limits.h", "stdio.h", "stdlib.h", "string.h", "sys/types.h",
)
DEFINE = re.compile(r"^#define ([A-Za-z_]\w*)(\([^)]*\))?(?:\s+(.*))?$")
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")


class SystemHeaders:
    def __init__(self, compiler: str = "gcc"):
        self.compiler = compiler
        self._cache: dict[tuple[str, ...], tuple[dict, dict, list[str]]] = {}
        self._platform: str | None = None

    def describe(self) -> str:
        if self._platform is None:
            def ask(flag: str) -> str:
                try:
                    return subprocess.run(
                        [self.compiler, flag], capture_output=True, text=True, timeout=30
                    ).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    return "unknown"

            version = ask("-dumpfullversion") or ask("-dumpversion")
            self._platform = f"{ask('-dumpmachine')}, {self.compiler} {version}".strip()
        return self._platform

    def _run(self, headers: list[str], flags: list[str]) -> str | None:
        source = "".join(f"#include <{h}>\n" for h in headers)
        try:
            done = subprocess.run(
                [self.compiler, *flags, "-x", "c", "-"],
                input=source, capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def _load(self, headers: list[str]) -> tuple[dict, dict, list[str]]:
        """(매크로 이름 → #define 줄, 타입 이름 → [(종류, 원문, 타입 참조들)], 읽은 헤더)."""
        key = tuple(headers)
        if key in self._cache:
            return self._cache[key]
        usable = headers if self._run(headers, ["-E", "-dM"]) is not None else [
            h for h in headers if self._run([h], ["-E", "-dM"]) is not None
        ]
        macros: dict[str, str] = {}
        for line in (self._run(usable, ["-E", "-dM"]) or "").splitlines():
            match = DEFINE.match(line)
            if match:
                macros[match.group(1)] = line
        types = self._types(self._run(usable, ["-E", "-P"]) or "")
        self._cache[key] = (macros, types, usable)
        return self._cache[key]

    @staticmethod
    def _types(text: str) -> dict[str, list[tuple[str, str, list[dict]]]]:
        import indexer

        found: dict[str, list[tuple[str, str, list[dict]]]] = {}
        if not text:
            return found
        tree = indexer._parser_for(".c").parse(text.encode("utf-8"))
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "type_definition":
                target = node.child_by_field_name("type")
                refs = indexer._type_refs(target)
                for name in indexer._typedef_names(node):
                    found.setdefault(indexer._text(name), []).append(
                        ("typedef", indexer._text(node), refs)
                    )
            elif node.type in indexer._TYPE_SPECIFIERS and node.child_by_field_name("body"):
                name = node.child_by_field_name("name")
                if name is not None:
                    found.setdefault(indexer._text(name), []).append(
                        (indexer._TYPE_SPECIFIERS[node.type], indexer._text(node) + ";", [])
                    )
            stack.extend(node.children)
        return found

    def lookup(
        self, headers: list[str], constants: list[str], types: list[str],
        members_used: set[str] | None = None, protected: set[str] | None = None,
    ) -> tuple[list[str], set[str], dict]:
        """(붙일 원문들, 찾은 이름들, 기록). 원문은 매크로 줄·typedef·태그 정의다.

        `members_used`가 있으면(근거 줄 정책) 그 이름을 멤버로 선언하지 않는 구조체·union 몸체는 접는다 —
        `FILE` 하나가 `struct _IO_FILE` 30줄을 끌어왔다(개선안 DEF-C7). typedef 사슬·매크로 값은 그대로.
        `protected`(sizeof·캐스트·메모리 복사 인자의 타입)와 그 typedef 사슬로 닿은 타입은 접지 않는다 —
        `sizeof(struct sockaddr_un)`의 크기가 사라졌다(리뷰 2026-10-10)."""
        chosen = sorted(set(headers) | set(DEFAULT_HEADERS))
        macros, typedefs, usable = self._load(chosen)
        out: list[str] = []
        resolved: set[str] = set()
        seen: set[str] = set()
        keep_whole = set(protected or ())
        queue = list(constants) + list(types)
        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            if name in macros:
                out.append(macros[name])
                resolved.add(name)
                value = DEFINE.match(macros[name]).group(3) or ""
                queue += [n for n in IDENT.findall(value) if n in macros or n in typedefs]
            elif name in typedefs:
                for _kind, text, refs in typedefs[name]:
                    whole = members_used is None or name in keep_whole
                    out.append(text if whole else _fold_body(text, members_used))
                    targets = [r["name"] for r in refs if r["kind"] != "builtin"]
                    if name in keep_whole:
                        keep_whole.update(targets)
                    queue += targets
                resolved.add(name)
        meta = {
            "platform": self.describe(),
            "headers": usable,
            "headers_unreadable": sorted(set(chosen) - set(usable)),
        }
        return list(dict.fromkeys(out)), resolved, meta


_MEMBER = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?[;,=]")


def _fold_body(text: str, members_used: set[str]) -> str:
    """구조체·union·enum 몸체를 멤버가 `members_used`와 겹치지 않으면 접는다 — 이름과 사슬만 남긴다."""
    if "{" not in text or "}" not in text:
        return text
    head, body = text[: text.find("{")], text[text.find("{") + 1: text.rfind("}")]
    # 끝에 `,`를 붙여 마지막 열거자(`enum { A, B, C }`의 C)도 센다
    if set(_MEMBER.findall(body + ",")) & members_used:
        return text
    return f"{head.rstrip()} {{ /* body omitted: no member used on evidence lines */ }}{text[text.rfind('}') + 1:]}"
