"""semgrep C 룰을 C++ 파일에도 걸리게 넓힌다 — 이미지 빌드 때 한 번(#689).

공개 semgrep-rules에는 C++ 디렉터리가 없고, `languages: [c]`인 룰은 semgrep이 `.cc`·`.cpp`·`.hpp`를
아예 열지 않는다. 핀 커밋(a84ff9cc)에는 두 철자가 있다 — 인라인 `languages: [c]`와, 다음 줄에
`- c`를 쓰는 블록 리스트(`lang/security/use-after-free.yaml` 등 셋). 둘 다 바꾸고, 끝에서 **룰마다**
YAML로 읽어 `cpp`가 없는 룰이 하나라도 있으면 exit 1로 빌드를 멈춘다 — 철자가 또 바뀌어도
조용히 C만 남지 않는다.

    python3 widen_rules_to_cpp.py /opt/semgrep-rules/c
"""

import re
import sys
from pathlib import Path

import yaml

# 줄 안의 공백만 본다(`[ \t]`) — `\s`는 줄바꿈까지 먹어 빈 줄을 지운다.
_INLINE = re.compile(r"^([ \t]*languages:[ \t]*)\[c\][ \t]*$", re.MULTILINE)
_BLOCK = re.compile(r"^([ \t]*languages:[ \t]*\n)([ \t]*)- c[ \t]*$", re.MULTILINE)


def widen(text: str) -> str:
    """두 철자의 `[c]`를 `[c, cpp]`로. 이미 `cpp`가 있거나 다른 언어 목록이면 그대로."""
    text = _INLINE.sub(r"\1[c, cpp]", text)
    return _BLOCK.sub(r"\1\2- c\n\2- cpp", text)


def c_only_rules(root: Path) -> tuple[int, list[str]]:
    """(룰 수, `c`는 있는데 `cpp`가 없는 룰 `파일:id` 목록)."""
    count, missing = 0, []
    for path in sorted(root.rglob("*.yaml")):
        for rule in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("rules", []):
            count += 1
            languages = rule.get("languages") or []
            if "c" in languages and "cpp" not in languages:
                missing.append(f"{path.relative_to(root)}:{rule.get('id')}")
    return count, missing


def main(root: Path) -> int:
    for path in root.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        widened = widen(text)
        if widened != text:
            path.write_text(widened, encoding="utf-8")
    count, missing = c_only_rules(root)
    if count == 0 or missing:
        print(f"C++로 못 넓힌 룰이 있다 (룰 {count}개): {missing}", file=sys.stderr)
        return 1
    print(f"룰 {count}개를 [c, cpp]로 넓혔다")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
