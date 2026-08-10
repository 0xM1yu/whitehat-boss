"""테스트 공용 상수.

프로필 이름을 테스트에 리터럴로 박지 않기 위한 단일 출처다. 박으면 프로필이
추가·개명될 때 그 테스트가 조용히 아무것도 지키지 않게 된다 — 이 PR이 고치는
결함("선언과 실제의 괴리")과 같은 종류다.
"""

from pathlib import Path

import yaml

from src.config import CONFIG_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent

# 저장소에 정의된 모든 프로필. 새 프로필이 추가되면 자동으로 검사 대상이 된다.
ALL_PROFILES: list[str] = sorted(
    yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text("utf-8"))["profiles"]
)
