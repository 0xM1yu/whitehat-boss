#!/usr/bin/env python3
"""run_gt 출력 폴더의 슬라이스를 정해진 이름으로 복사한다 (팀 요청 2026-10-04).

- source -> sink (forward):  ``A<번호>_<source>_<파일명>_<source 줄>.c``
- sink -> source (backward): ``B<번호>_<sink>_<파일명>_<sink 줄>.c``

예: ``A0001_sscanf_libncrx_256.c`` · ``B0001_nf_off_libncrx_260.c``.

- <번호>: vuln은 출력 폴더의 vuln 표본 id 순서로 1부터 4자리. 한 표본의 A와 B는 같은 번호다.
  patch는 ``--pairs``(vuln ↔ patch 짝 목록)를 주면 **짝 vuln의 번호**(vuln 쪽 파일과 짝이 맞게),
  없으면 vuln 다음 번호부터. normal(정상)은 짝이 없어 자체 번호(표본 id 순서로 1부터).
- <source>/<sink>: 슬라이스 시작 노드 — 실제 호출이면 호출 이름(`memcpy`), 연산자(`Data[a+1]`·
  `nf_off + msg->text_len`)면 그 식의 첫 식별자(`Data`·`nf_off`). `[A-Za-z0-9_]`만 남긴다.
- <파일명>: 시작 노드가 있는 파일의 확장자 뺀 이름(`ncrx/libncrx.c` → `libncrx`).
- <줄>: 시작 노드의 줄.

원래 파일(`<id>.<방향>.{sliced,auditor-input,pov-input}.c`)은 그대로 두고(평가 스크립트가 읽는다)
``<out>/<dest>/<종류>/``에 auditor-input과 있으면 pov-input을 쓰고 ``<out>/<dest>/index.csv``에 번호 ↔
표본·방향·원래 파일을 적는다. 1단계 sliced.c 사본(옛 ``stage1/``)은 만들지 않는다(사용자 2026-10-04) —
원래 파일 ``<id>.<방향>.sliced.c``에 있다.

사용: ``python name_outputs.py <run_gt --out 폴더> [--kinds vuln,patch] [--dest named] [--pairs pairs.jsonl]``
"""

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

PREFIX = {"forward": "A", "backward": "B"}
ROLE = {"forward": "source", "backward": "sink"}


def safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "x"


def start_name(entry: dict, nodes: list[dict]) -> str:
    """시작 노드 이름 — 호출 이름, 연산자면 식의 첫 식별자."""
    name = entry.get("symbol") or ""
    if name and not name.startswith("<operator>"):
        return safe(name)
    for node in nodes:
        found = re.findall(r"[A-Za-z_]\w*", node.get("code") or "")
        if found:
            return safe(found[0])
    found = re.findall(r"[A-Za-z_]\w*", name.replace("<operator>.", ""))
    return safe(found[0]) if found else "x"


def documents(out: Path, kind: str) -> list[Path]:
    return [p for p in sorted((out / kind).glob("*.json"))
            if not p.name.endswith(".spec.json") and not p.name.endswith(".slices.json")]


def has_slices(path: Path) -> tuple[dict, bool]:
    document = json.loads(path.read_text("utf-8"))
    return document, bool(document.get("slices"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--kinds", default="vuln,patch")
    parser.add_argument("--dest", default="named")
    parser.add_argument("--pairs", type=Path, help="pairs.jsonl — patch numbers follow their vuln")
    args = parser.parse_args(argv)
    out, kinds = args.out, [k for k in args.kinds.split(",") if k]
    # vuln numbering (always computed the same way, so vuln and patch runs agree)
    vuln_number: dict[str, int] = {}
    for path in documents(out, "vuln"):
        if has_slices(path)[1]:
            vuln_number[path.stem] = len(vuln_number) + 1
    vuln_of: dict[str, str] = {}
    if args.pairs:
        for line in args.pairs.read_text("utf-8").splitlines():
            if line.strip():
                pair = json.loads(line)
                vuln_of[pair["patch_sample_id"]] = pair["vuln_sample_id"]
    named = out / args.dest
    if named.exists():
        shutil.rmtree(named)
    rows = []
    extra = len(vuln_number)
    normal_number = 0
    for kind in ("vuln", "patch", "normal"):
        if kind not in kinds:
            continue
        for path in documents(out, kind):
            document, ok = has_slices(path)
            if not ok:
                continue
            sample_id = path.stem
            if kind == "vuln":
                number = vuln_number[sample_id]
            elif kind == "normal":
                normal_number += 1
                number = normal_number
            elif vuln_of.get(sample_id) in vuln_number:
                number = vuln_number[vuln_of[sample_id]]
            else:
                extra += 1
                number = extra
            located = (document.get("target") or {}).get("located") or {}
            for item in document["slices"]:
                direction = item["direction"]
                entry = item["entry"]
                nodes = (located.get(ROLE[direction]) or {}).get("nodes") or []
                name = (f"{PREFIX[direction]}{number:04d}_{start_name(entry, nodes)}_"
                        f"{safe(Path(entry['file']).stem)}_{entry['line']}.c")
                target = named / kind
                target.mkdir(parents=True, exist_ok=True)
                source = path.parent / f"{sample_id}.{direction}.auditor-input.c"
                if source.exists():
                    shutil.copyfile(source, target / name)
                pov_source = path.parent / f"{sample_id}.{direction}.pov-input.c"
                pov_name = f"{Path(name).stem}.pov-input.c"
                if pov_source.exists():
                    shutil.copyfile(pov_source, target / pov_name)
                rows.append({
                    "number": f"{number:04d}", "file": name, "kind": kind, "sample_id": sample_id,
                    "pair_vuln_id": vuln_of.get(sample_id, "") if kind == "patch" else "",
                    "direction": direction, "start_file": entry["file"], "start_line": entry["line"],
                    "auditor_input": source.name if source.exists() else "",
                    "pov_input": pov_source.name if pov_source.exists() else "",
                    "sliced": f"{sample_id}.{direction}.sliced.c",
                })
    named.mkdir(exist_ok=True)
    with (named / "index.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["number"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} slices -> {named}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
