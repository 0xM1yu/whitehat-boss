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


# scout 자리만 실서빙인 합성 config의 scout 키. `prompts/scout`에 있는 버전(v1)을 고르는
# 로스터 키여야 프롬프트까지 열린다 — `qwen3.6-35b-a3b`가 v1이다.
SERVED_SCOUT_KEY = "qwen3.6-35b-a3b"


def served_scout_config():  # noqa: ANN201 - src.config를 모듈 머리에서 끌어오지 않으려고 반환형을 안 적는다
    """scout 자리만 실서빙이고 나머지는 fake인 config — 옛 `single_gpu_12g`의 모양.

    2026-09-24 로스터 밖 정리로 그 프로필을 지웠다. 실서빙과 fake가 한 프로필에 섞이는
    경로(`RoutingModelClient`·bench·토크나이저 카운터)를 재는 검사는 **모양**만 있으면 되므로
    커밋된 프로필 대신 `dev_fake`에서 조립한다. 상태 저장소도 그 프로필처럼 sqlite다.
    """
    from src.config import load_config

    base = load_config("dev_fake")
    profile = base.profile.transition(
        roles={**base.profile.roles, "scout": SERVED_SCOUT_KEY},
        adapters=base.profile.adapters.transition(state_store="sqlite"),
    )
    return base.transition(profile=profile)
