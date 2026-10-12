#!/usr/bin/env python3
"""슬라이스 결과(slice-v2 JSON)에서 코드만 소스 파일로 뺀다.

- `<sample_id>.<direction>.sliced<확장자>` — 1단계: Joern 슬라이스 코드(`slices[].code`)
- `<sample_id>.<direction>.auditor-input<확장자>` — 2단계: 원본 함수·메타 정보를 붙인 코드
  (`slices[].attached.source`). 기존 tools 규칙 그대로다.
- `<sample_id>.<direction>.pov-input<확장자>` — 입력 파서 문맥을 더한 코드
  (`slices[].pov_attached.source`). POV 확장을 실행하지 않았으면 없다.

backward·forward 둘이면 표본 하나에 최대 여섯 파일이다. 확장자는 표본 언어로 정한다(C → `.c`,
C++ → `.cpp`). 언어를 모르면 `.c`다.

    python export_code.py results/netconsd      # 그 아래 모든 결과 JSON
    python export_code.py results/netconsd/vuln/hs-b445ce6f6bbea459.json
"""

import json
import sys
from pathlib import Path

EXTENSIONS = {"C": ".c", "C++": ".cpp"}


def export(document: dict, directory: Path, sample_id: str) -> list[Path]:
    """슬라이스마다 sliced·auditor-input·pov-input을 쓴다."""
    language = (document.get("sample") or {}).get("language")
    suffix = EXTENSIONS.get(language, ".c")
    written = []
    for item in document.get("slices", []):
        direction = item.get("direction") or item.get("id")
        outputs = [("sliced", item["code"])]
        if item.get("attached"):
            outputs.append(("auditor-input", item["attached"]["source"]))
        if item.get("pov_attached"):
            outputs.append(("pov-input", item["pov_attached"]["source"]))
        for stage, text in outputs:
            path = directory / f"{sample_id}.{direction}.{stage}{suffix}"
            path.write_text(text + "\n", encoding="utf-8")
            written.append(path)
    return written


def is_result(path: Path) -> bool:
    return path.suffix == ".json" and not path.name.endswith((".spec.json", ".slices.json"))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    total = 0
    for target in map(Path, argv):
        files = sorted(p for p in target.rglob("*.json") if is_result(p)) if target.is_dir() else [target]
        for path in files:
            document = json.loads(path.read_text(encoding="utf-8"))
            if "slices" not in document:
                continue
            sample_id = (document.get("sample") or {}).get("sample_id") or path.stem
            for written in export(document, path.parent, sample_id):
                print(written)
                total += 1
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
