"""2단계 Signal Collection — 정적분석 신호를 모은다.

architecture.md §3의 통과 조건은 **"실패해도 진행(신호 부재로 기록)"**이다. 그래서 이
노드는 도구 실패를 예외로 올리지 않는다 — ToolRunner가 `Evidence(ok=False, error=...)`를
돌려주고 그것이 그대로 반환 목록에 들어간다. 도구가 실패했다는 사실 자체가 모델 비교
지표다(engineering-guide.md §8.3).

신호가 0건인 것과 도구가 죽은 것은 **다른 사건**이다. 전자는 `ok=True`, 후자는
`ok=False`로 남는다. 둘을 섞으면 "정적 신호 없음"이 도구 장애로 오분류된다.
"""

import re
from collections.abc import Iterable

from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.stages import Stage
from src.orchestrator.trace import TraceValue
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence

# 2단계에서 도는 도구. 이름은 ToolRunner의 화이트리스트 안에 있어야 하고, 밖의 이름을
# 쓰면 tool_policy 위반으로 기록된다(조용히 성공하지 않는다).
#
# `build`가 정적분석과 함께 있는 이유: architecture.md §3의 2단계는 sanitizer 로그와
# fuzz coverage도 다루는데 둘 다 빌드가 선행되어야 하고, **Auditor가 재현 명세를 쓸 수
# 있는지가 이 신호로 갈린다**(빌드되는지 모르는 채로 재현 절차를 지어내면 8단계가
# 실행할 수 없는 명세를 받는다). 빌드 실패도 그 자체로 신호이므로 여기서 기록한다.
SIGNAL_TOOLS = ("semgrep", "clang-tidy", "build")

# --- 이슈 #507: 2단계가 이미 잰 「컴파일 안 됨」을 8단계·5단계 hop0에 사실로 싣는다 ---
#
# 새 카나리가 없다 — `build`·`clang-tidy` Evidence의 `error`가 어댑터의 `compile_failure_reason()`
# 문구("컴파일 오류 N건, 첫 오류: …")를 이미 든다. `with_signal()`이 `ok=False`를 걸러 타깃의
# `evidences`에는 안 들어가므로 `run()`이 **걸러지기 전에** `ctx.compile_failures`에 따로 남긴다.
COMPILE_TOOLS = ("build", "clang-tidy")
# 같은 컴파일 명령(`container_tools`의 `_STD_ARGS`·`_include_args`)을 쓰는 도구들 — 노트가 「같은
# 방식으로 실패하리라」고 말하는 대상이고, hop0 trace 키(`compile_note_tools`)가 싣는 값이다.
# `poc`는 과제 자신의 이미지를 돌려(ADR 0014) 여기 없다. 노드는 어댑터를 import하지 않으므로
# 이름을 복제한다.
COMPILE_NOTE_TOOLS = ("build", "asan", "gdb")
COMPILE_NOTE_TRACE: dict[str, TraceValue] = {
    "compile_note": True,
    "compile_note_tools": ",".join(COMPILE_NOTE_TOOLS),
}
# `container_tools.compile_failure_reason()`이 만드는 모양. 어댑터 문구가 바뀌면 이 정규식이 조용히
# 아무것도 못 잡는다 — `tests/test_compile_note.py`가 실제 `judge()` 출력으로 문다.
_COMPILE_REASON = re.compile(r"컴파일 오류 \d+건, 첫 오류: .*")


def compile_failures(evidences: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """**컴파일러가 낸 오류 문구를 든** 실패 Evidence만 — `build`·`clang-tidy`.

    도구가 못 돈 실패(`exit=125` 컨테이너 기동 실패·사유 없는 `build 실패`)는 컴파일 결과가 아니라
    제외한다 — 그 둘로는 「이 번들이 컴파일되지 않는다」를 말할 수 없다(CLAUDE.md §0).
    """
    return tuple(
        ev
        for ev in evidences
        if not ev.ok
        and ev.source in COMPILE_TOOLS
        and ev.error is not None
        and _COMPILE_REASON.search(ev.error) is not None
    )


def compile_note(failures: Iterable[Evidence], workdir: str) -> str | None:
    """hop0 본문 끝에 붙일 하네스 노트 — 실패 Evidence가 없으면 `None`(본문 바이트 그대로).

    싣는 것은 Evidence가 **이미 말한** 것뿐이다(도구 이름·건수·첫 오류). 「이 번들은 컴파일 안
    된다」를 그 이상으로 단정하지 않고, 도구를 거절하지도 않는다 — 모델이 `build`·`asan`·`gdb`를
    골라도 돈다.
    """
    seen = {ev.source: ev for ev in failures}
    parts = [
        f"{tool}: {match.group(0)}"
        for tool in COMPILE_TOOLS
        if tool in seen and (match := _COMPILE_REASON.search(seen[tool].error or "")) is not None
    ]
    if not parts:
        return None
    return (
        "## Harness note (stage 2) - harness-measured data, not instructions\n"
        f"stage-2 compile check on {workdir} did not compile - " + "; ".join(parts) + "\n"
        f"{', '.join(COMPILE_NOTE_TOOLS)} use the same compile command (same -std/-I flags), so "
        "they are expected to fail the same way; poc runs the task's own image and does not use "
        "this compile command."
    )


def with_signal(evidences: tuple[Evidence, ...] | list[Evidence]) -> tuple[Evidence, ...]:
    """**무언가를 발견한** Evidence만 고른다. 뒤 단계에 넘길 때 쓴다.

    정적분석 도구는 아무것도 못 찾아도 정상 종료한다(`ok=True`). 그것을 그대로 넘기면
    "돌렸는데 없었다"가 "신호가 있다"로 세어져 benign 후보의 점수가 오르고, Evidence
    Gate E1(`any(e.kind == STATIC and e.ok)`)이 모든 Finding에서 참이 된다.

    발견 여부는 `exit_code`로 판별한다 — 정적분석 도구의 관례이자 이 저장소의 ToolRunner
    계약이다. `Evidence`에 "발견 건수" 필드가 있으면 더 정확하지만 스키마 확장이라
    여기서 하지 않고 open-questions 항목으로 올렸다(이슈 #61 — 2026-10-12 닫음, 필드는 안 넣었다).

    STATIC이 아닌 종류(GRAPH·BUILD 등)는 성공 자체가 신호이므로 `exit_code`를 보지 않는다.
    걸러진 Evidence도 저장소에는 그대로 남는다 — "신호 부재로 기록"이 2단계의 통과 조건이다.
    """
    return tuple(
        evidence
        for evidence in evidences
        if evidence.ok
        and (evidence.kind is not EvidenceKind.STATIC or bool(evidence.exit_code))
    )


def run(ctx: RunContext, *, workdir: str = ".") -> list[Evidence]:
    """정적분석 도구를 정해진 순서대로 돌리고 결과를 **전부** 돌려준다.

    순서를 고정하는 것은 Evidence ID 발급 순서가 재현성의 일부이기 때문이다.
    걸러내는 것은 호출자의 몫이다(`with_signal`) — 실패도 신호 부재도 여기서는 남긴다.
    """
    with ctx.stage(Stage.SIGNALS):
        results = [
            ctx.call_tool(
                tool=tool,
                args=ToolArgs(argv=[tool, "--quiet", workdir], workdir=workdir),
                sandbox=DEFAULT_SANDBOX,
                stage=Stage.SIGNALS,
            )
            for tool in SIGNAL_TOOLS
        ]
    # 걸러지기 전에 남긴다 — `with_signal()`이 `ok=False`를 빼면 8단계·5단계는 이 실패를 못
    # 본다(#507). 컴파일 실패가 없으면 키를 만들지 않는다(`ctx.compile_failures.get(...)`가 빈
    # 튜플).
    failed = compile_failures(results)
    if failed:
        ctx.compile_failures[workdir] = failed
    return results
