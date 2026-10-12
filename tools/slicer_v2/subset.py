#!/usr/bin/env python3
"""취약점 주변의 「서브 기능」 파일 집합 — 큰 저장소에서 Joern에 올릴 파일만 고른다.

저장소 전체를 Joern에 올리면 파일 사이 연결이 다 살지만 linux·tensorflow 같은 저장소는 CPG가
너무 크다. 그래서 tree-sitter 인덱스(`indexer.py`의 index.json)의 호출 그래프에서 GT 함수(sink·
source를 품은 함수)로부터

- 호출자 쪽으로 `hops`단 (호출자의 호출자 …)
- 피호출자 쪽으로 `hops`단 (피호출자의 피호출자 …)

닿는 함수가 정의된 파일과, 그 파일들이 `#include`로 끌어오는 저장소 안 헤더(전이)를 모은다.
가까운 단부터 담고 `max_files`에서 멈춘다(멈췄으면 `truncated`).

⚠ **이 집합이 「필요한 연결을 다 담는가」는 이 모듈이 보장하지 않는다.** 이름 기반 호출
그래프라 함수 포인터·매크로 안 호출은 못 따라간다. 같은 표본을 전체 CPG와 이 집합으로 각각
잘라 비교하는 것이 확인 방법이다(`run_gt.py --scope full` vs `--scope subset`).
"""

import json
import os
import re
import shutil
from collections import deque
from pathlib import Path

INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def _tables(index: dict) -> tuple[dict, dict, dict]:
    """(함수 → 정의 파일들, 함수 → 호출자들, 함수 → 피호출자들) — 이름 기준."""
    defined: dict[str, set[str]] = {}
    for name, records in (index.get("functions") or {}).items():
        defined.setdefault(name, set()).update(r["file"] for r in records)
    if not defined:
        for symbol in index.get("symbols", []):
            defined.setdefault(symbol["name"], set()).add(symbol["file"])
    callers: dict[str, set[str]] = {}
    callees: dict[str, set[str]] = {}
    calls = index.get("calls") or {}
    edges = (
        ((callee, site) for callee, sites in calls.items() for site in sites)
        if isinstance(calls, dict)
        else ((edge["callee"], edge) for edge in calls)
    )
    for callee, site in edges:
        caller = site.get("caller")
        if not caller:
            continue
        callers.setdefault(callee, set()).add(caller)
        callees.setdefault(caller, set()).add(callee)
    return defined, callers, callees


def _walk(start: set[str], graph: dict[str, set[str]], hops: int) -> list[tuple[int, str]]:
    """BFS — (단, 함수). 시작 함수는 0단."""
    order = [(0, name) for name in sorted(start)]
    seen = set(start)
    queue = deque(order)
    while queue:
        depth, name = queue.popleft()
        if depth == hops:
            continue
        for other in sorted(graph.get(name, ())):
            if other not in seen:
                seen.add(other)
                order.append((depth + 1, other))
                queue.append((depth + 1, other))
    return order


def _resolve_include(target: str, including: str, by_suffix: dict[str, list[str]]) -> list[str]:
    """include 문자열을 저장소 파일로. 같은 디렉터리 상대경로 → 경로 끝이 같은 파일."""
    local = os.path.normpath(os.path.join(os.path.dirname(including), target)).replace("\\", "/")
    candidates = by_suffix.get(Path(target).name, [])
    if local in candidates:
        return [local]
    return [c for c in candidates if c == target or c.endswith("/" + target)]


def select_files(
    repo: Path,
    index: dict,
    seeds: list[tuple[str, str | None]],
    *,
    hops: int = 2,
    max_files: int = 400,
) -> dict:
    """GT 함수 주변 파일 집합. `seeds`는 (파일, 함수 이름) — 함수를 모르면 파일만 넣는다."""
    defined, callers, callees = _tables(index)
    files: dict[str, str] = {}  # 파일 → 들어온 이유

    def add(file: str, reason: str) -> bool:
        if file in files:
            return True
        if len(files) >= max_files:
            return False
        if (repo / file).is_file():
            files[file] = reason
        return True

    names = set()
    for file, function in seeds:
        add(file, "seed")
        if function:
            names.add(function)
    truncated = False
    up = _walk(names, callers, hops)
    down = _walk(names, callees, hops)
    # 가까운 단부터 — 같은 단에서는 호출자, 피호출자 순.
    layered = sorted(
        [(d, 0, n) for d, n in up] + [(d, 1, n) for d, n in down], key=lambda t: (t[0], t[1], t[2])
    )
    for depth, side, name in layered:
        for file in sorted(defined.get(name, ())):
            if not add(file, f"{'caller' if side == 0 else 'callee'}_hop{depth}"):
                truncated = True
    # 헤더 — 고른 파일들이 include하는 저장소 안 파일(전이).
    all_files = [f for f in index.get("files", [])]
    by_suffix: dict[str, list[str]] = {}
    for file in all_files:
        by_suffix.setdefault(Path(file).name, []).append(file)
    queue = deque(sorted(files))
    while queue:
        file = queue.popleft()
        try:
            text = (repo / file).read_text("utf-8", errors="replace")
        except OSError:
            continue
        for target in INCLUDE.findall(text):
            for header in _resolve_include(target, file, by_suffix):
                if header not in files:
                    if not add(header, "include"):
                        truncated = True
                        continue
                    queue.append(header)
    reasons: dict[str, int] = {}
    for reason in files.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "files": sorted(files),
        "reasons": dict(sorted(reasons.items())),
        "functions_up": len(up),
        "functions_down": len(down),
        "hops": hops,
        "max_files": max_files,
        "truncated": truncated,
    }


def materialize(repo: Path, files: list[str], out: Path) -> Path:
    """`files`만 든 디렉터리를 같은 상대경로로 만든다 — 하드링크, 안 되면 복사.

    심볼릭 링크는 안 쓴다 — 슬라이서가 저장소 안 심링크를 읽지 않는다(`slicer.load_sources`).
    """
    if out.exists():
        shutil.rmtree(out)
    for file in files:
        target = out / file
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(repo / file, target)
        except OSError:
            shutil.copy2(repo / file, target)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--seed", action="append", required=True, help="file[:function]")
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--max-files", type=int, default=400)
    parser.add_argument("--out", type=Path, help="이 디렉터리에 파일 집합을 만든다")
    args = parser.parse_args(argv)
    index = json.loads(args.index.read_text(encoding="utf-8"))
    seeds = [(s.split(":", 1)[0], (s.split(":", 1) + [None])[1]) for s in args.seed]
    chosen = select_files(args.repo, index, seeds, hops=args.hops, max_files=args.max_files)
    if args.out:
        materialize(args.repo, chosen["files"], args.out)
    print(json.dumps({k: v for k, v in chosen.items() if k != "files"} | {
        "file_count": len(chosen["files"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
