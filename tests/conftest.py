"""테스트 공용 상수.

프로필 이름을 테스트에 리터럴로 박지 않기 위한 단일 출처다. 박으면 프로필이
추가·개명될 때 그 테스트가 조용히 아무것도 지키지 않게 된다 — 이 PR이 고치는
결함("선언과 실제의 괴리")과 같은 종류다.
"""

import os
import subprocess
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


def tracked_files(*patterns: str, root: Path | None = None) -> list[Path]:
    """`git ls-files`가 내놓는 추적 파일. git이 없으면 검사를 건너뛴다.

    **워킹트리는 저장소가 아니다.** 전수 검사가 `rglob`로 걸으면 체크아웃에 같이 있는
    미추적 트리까지 읽는다 — `.claude/worktrees/<이름>/`(다른 세션의 linked worktree로,
    그 안에는 **낡은 판의 문서가 그대로** 있다)·`_workspace/`·`artifacts/`.
    CLAUDE.md §1이 검사 대상을 *"추적되는 `*.md`는 전부"*로 정한 것이 이 함수다.

    **사고 —** 2026-10-12, 체크아웃에 `.claude/worktrees/` 아래 worktree가 5개 있는
    상태에서 전수 검사 셋(`test_no_live_source_says_pov_v3_is_unwired` ·
    `test_no_live_source_names_v3_alone_as_the_pov_prompt` ·
    `test_no_live_source_puts_last_gen_on_the_first_line_of_the_observation`)이
    offender 목록이 **전부 `.claude/worktrees/...`** 인 채로 깨졌다(#631). 이 저장소의 산문은
    한 줄도 틀리지 않았는데 빨개졌고, 읽는 쪽에는 고칠 곳이 없다 — 남의 체크아웃이다.

    `root`는 오염 핀 전용이다(임시 저장소를 가리킨다). 기본값은 `REPO_ROOT`다.

    **`-z`가 필수다.** 기본 출력은 비ASCII 경로를 큰따옴표로 감싸고 바이트를 8진
    이스케이프로 바꾼다(`.claude/노트.md` → `".claude/\\353\\205\\270...md"`). 그대로
    `Path`에 넣으면 없는 파일을 가리켜 `OSError`가 나고, 검사기가 **문서의 결함처럼 보이는
    실패**를 낸다 — 원인이 문서가 아니라 여기라서 읽는 사람이 엉뚱한 곳을 고친다.
    `-z`는 이스케이프를 끄고 NUL로 가른다.

    **디스크에 없는 추적 파일은 뺀다.** `git ls-files`는 인덱스를 읽으므로 `git rm` 없이 지운
    파일도 낸다 — 그대로 열면 전수 검사 셋이 `FileNotFoundError`로 죽고, 위와 같이 **문서의
    결함처럼 보이는 실패**가 된다(`rglob` 판은 없는 파일을 못 봐서 통과했다). 지움 자체는
    `git status`가 보이는 일이지 이 수집기의 몫이 아니다. 서브모듈(gitlink)도 디렉터리라
    같이 빠진다.
    """
    base = REPO_ROOT if root is None else root
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", *patterns],
            cwd=base,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")
    if result.returncode != 0:  # pragma: no cover - 저장소 밖
        pytest.skip("저장소가 아니다")
    paths = (base / name for name in result.stdout.split("\0") if name)
    return [path for path in paths if path.is_file()]


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


def declare_scout_input(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """Scout 노드가 읽는 프롬프트 meta의 `input` 선언을 `kind`로 바꾼다(디스크 프롬프트는 그대로).

    노드가 `SOURCE`의 모양을 이 선언으로 고른다(#529). `dev_fake`의 Scout(`scout/v2`)는 선언이
    `source`라, 슬라이스 렌더를 보는 Scout(v3)를 재는 검사가 `slice`로 바꿔 돌린다.
    """
    import dataclasses

    from src.orchestrator.nodes import scout

    real = scout.load_prompt
    monkeypatch.setattr(
        scout,
        "load_prompt",
        lambda role, version, **kw: dataclasses.replace(real(role, version, **kw), input=kind),
    )
