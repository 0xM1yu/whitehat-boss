"""4개 인터페이스의 구체 구현 (engineering-guide.md §3).

바깥에서는 `build_deps()`와 `Deps`만 쓴다. 개별 어댑터 클래스를 직접 import 하는 것은
`factory.py` 하나로 제한한다 — 오케스트레이터가 구현체 이름을 알게 되면 "설정만으로
교체"가 깨진다.
"""

from src.adapters.factory import Deps, build_deps

__all__ = ["Deps", "build_deps"]
