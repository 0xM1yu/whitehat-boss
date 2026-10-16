"""디코더 스키마 컴파일 게이트의 명령줄 진입점 (#698). 로직은 `src/decoder_schemas.py`다.

`src/cli.py`는 `tools/`를 import하지 않는다(`test_the_report_gate_does_not_invoke_the_tool`) —
그래서 `doctor`와 이 스크립트가 같은 `src` 모듈을 부른다.

    python tools/check_decoder_schemas.py [프로필]     # xgrammar가 깔린 파이썬으로
"""

import argparse
import sys
from pathlib import Path

# 경로 실행(`python tools/check_decoder_schemas.py`)은 sys.path[0]이 tools/라 `src`를 못 찾는다.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config import load_config  # noqa: E402
from src.decoder_schemas import check_profile, format_line  # noqa: E402
from src.decoder_schemas import xgrammar_version as gate_version  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="디코더 스키마 컴파일 게이트 (#698)")
    parser.add_argument("profile", nargs="?", default="harness_pool")
    args = parser.parse_args(argv)
    try:
        import xgrammar  # noqa: F401
    except ImportError:
        print("xgrammar가 없다 — 게이트를 돌리지 못했다 (pip install 'xgrammar>=0.2.1,<1.0.0')")
        return 2
    results = check_profile(load_config(args.profile))
    for item, error in results:
        print(format_line(item, error))
    failed = sum(error is not None for _, error in results)
    print(f"{len(results)}개 중 FAIL {failed} (xgrammar {gate_version()})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
