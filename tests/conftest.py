"""테스트 공용 상수.

프로필 이름을 테스트에 리터럴로 박지 않기 위한 단일 출처다. 박으면 프로필이
추가·개명될 때 그 테스트가 조용히 아무것도 지키지 않게 된다 — 이 PR이 고치는
결함("선언과 실제의 괴리")과 같은 종류다.
"""

import os
from pathlib import Path

import pytest
import yaml

from src.config import CONFIG_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent

# 저장소에 정의된 모든 프로필. 새 프로필이 추가되면 자동으로 검사 대상이 된다.
ALL_PROFILES: list[str] = sorted(
    yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text("utf-8"))["profiles"]
)


# scout 자리만 실서빙인 합성 config의 scout 키. `prompts/scout`에 있는 버전(v1)을 골라야
# 프롬프트까지 열린다. ⏵ 2026-09-29 — 이 키의 registry 버전이 Auditor v3
# (⏵ 2026-10-03 v6 · ⏵ 2026-10-04 v7 · ⏵ 2026-10-08 — 그 뒤 v8·v9·v10. 지금 판은
# `config/models.yaml`이 정본이다)가 되어
# (`prompts/scout/v3`는 없다) `served_scout_config`가 합성 엔트리에서 v1로 고정한다 —
# registry 버전에 기대지 않는다.
SERVED_SCOUT_KEY = "qwen3.8-27b"
_SERVED_SCOUT_PROMPT_VERSION = "v1"


def served_scout_config():  # noqa: ANN201 - src.config를 모듈 머리에서 끌어오지 않으려고 반환형을 안 적는다
    """scout 자리만 실서빙이고 나머지는 fake인 config — 옛 `single_gpu_12g`의 모양.

    2026-09-24 로스터 밖 정리로 그 프로필을 지웠다. 실서빙과 fake가 한 프로필에 섞이는
    경로(`RoutingModelClient`·bench·토크나이저 카운터)를 재는 검사는 **모양**만 있으면 되므로
    커밋된 프로필 대신 `dev_fake`에서 조립한다. 상태 저장소도 그 프로필처럼 sqlite다.
    """
    from src.config import load_config

    base = load_config("dev_fake")
    entry = base.models[SERVED_SCOUT_KEY].transition(
        prompt_template_version=_SERVED_SCOUT_PROMPT_VERSION
    )
    profile = base.profile.transition(
        roles={**base.profile.roles, "scout": SERVED_SCOUT_KEY},
        adapters=base.profile.adapters.transition(state_store="sqlite"),
    )
    return base.transition(models={**base.models, SERVED_SCOUT_KEY: entry}, profile=profile)


def plant_link(kind: str, link: Path, outside: Path) -> None:
    """`link` 자리에 `outside`를 가리키는 항목을 심는다 — `symlink`(살아 있는 링크)·`dangling`(없는
    곳을 가리키는 링크, 호출부가 `outside`를 안 만든다)·`hardlink`(같은 inode).

    못 만드는 환경이면 **사유를 적고 건너뛴다**(Windows는 심링크 권한이 없을 수 있다) — 안 만들어진
    링크 위에서 초록이 나면 공허하다.
    """
    try:
        if kind == "hardlink":
            os.link(outside, link)
        else:
            link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"{kind}를 만들 수 없는 환경이다: {type(exc).__name__}: {exc}")
