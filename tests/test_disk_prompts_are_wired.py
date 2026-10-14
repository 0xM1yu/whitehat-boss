"""디스크에 판이 서면 노드의 입력 집합에도 선다.

Critic v7은 디스크에만 있는 동안 `_SOURCE_INPUT_VERSIONS`·`_CONTENT_AWARE_VERSIONS`에 없었다.
`_CONTENT_AWARE_VERSIONS`에서 빠지면 근거 본문·위치·홉 관측 절이 **조용히** 사라지고(서빙 판이면
`cli`의 `_check_prompts`가 모델 호출 전에 ERROR로 막는다),
`_SOURCE_INPUT_VERSIONS`에서 빠지면 body의 `{{SOURCE}}` 슬롯이 안 채워져 `render`가
`PromptError`로 선다.

한계: 집합의 최솟값이 올라가는 것·집합 안의 디스크에 없는 판·철자 변이는 이 핀 단독으로는 못
잡는다 - `test_nodes`의 집합 정확 일치 핀과 doctor의 내용 인식 검사가 그쪽을 막는다.
"""

import re

import pytest

from src.orchestrator.nodes import auditor, critic, pov
from src.orchestrator.prompt_loader import PROMPTS_DIR

# `v4_t1` 같은 실험 이름은 안 걸린다 - cli._check_prompt_version의 규약
_INT_VERSION = re.compile(r"v(\d+)")

AWARE_SETS = {
    "critic": critic._CONTENT_AWARE_VERSIONS,
    "auditor": auditor._CONTENT_AWARE_VERSIONS,
    "pov": pov._WIDE_INPUT_VERSIONS,
}


def _integer_versions(names) -> list[int]:
    return sorted(int(m.group(1)) for n in names if (m := _INT_VERSION.fullmatch(n)))


def _disk_versions(role: str) -> list[int]:
    """prefix.md가 있는 정수 판 디렉터리 - 로더(`body.md`·`meta.yaml`도 요구)보다 느슨하다."""
    return _integer_versions(
        p.name for p in (PROMPTS_DIR / role).iterdir() if (p / "prefix.md").is_file()
    )


@pytest.mark.parametrize("role", AWARE_SETS)
def test_every_disk_version_from_the_aware_floor_up_is_in_the_set(role):
    """집합의 최소 정수 판 이상인 디스크 판은 전부 든다. 새 판이 일부러 좁은 입력을 원하면
    이 검사를 고쳐 그 사실을 적는다(`test_pov_loop`의 v2 동결과 같은 규약)."""
    aware = AWARE_SETS[role]
    floor = _integer_versions(aware)[0]
    missing = [f"v{n}" for n in _disk_versions(role) if n >= floor and f"v{n}" not in aware]
    assert not missing, (
        f"prompts/{role}/{missing}이 디스크에 있는데 노드의 내용 인식 집합에 없다 - "
        "집합에 넣거나(조용히 좁은 입력을 받는다) 디스크 판을 지워라"
    )


def test_critic_prefix_describes_facts_source_exactly_for_source_input_versions():
    """양방향. 양성 v6·v7(prefix가 `FACTS.source`를 서술), 음성 v1~v5(안 서술) - 전부 저장소 실물.
    ⚠ 철자 pin이다: `FACTS.source`라는 글자가 prefix에 있는지만 묻고 계약의 뜻은 못 묻는다."""
    described = {
        f"v{n}"
        for n in _disk_versions("critic")
        if "FACTS.source" in (PROMPTS_DIR / "critic" / f"v{n}" / "prefix.md").read_text("utf-8")
    }
    assert {"v6", "v7"} <= described and described.isdisjoint({"v1", "v2", "v3", "v4", "v5"})
    assert described == critic._SOURCE_INPUT_VERSIONS, (
        f"prefix가 FACTS.source를 서술하는 판 {sorted(described)} != "
        f"_SOURCE_INPUT_VERSIONS {sorted(critic._SOURCE_INPUT_VERSIONS)} - "
        "집합을 고치거나 prefix를 고쳐라(어긋나면 렌더가 서거나 읽지 않는 판에 SOURCE가 실린다)"
    )
