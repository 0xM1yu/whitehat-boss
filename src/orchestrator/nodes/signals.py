"""2단계 Signal Collection — 정적분석 신호를 모은다.

architecture.md §3의 통과 조건은 **"실패해도 진행(신호 부재로 기록)"**이다. 그래서 이
노드는 도구 실패를 예외로 올리지 않는다 — ToolRunner가 `Evidence(ok=False, error=...)`를
돌려주고 그것이 그대로 반환 목록에 들어간다. 도구가 실패했다는 사실 자체가 모델 비교
지표다(engineering-guide.md §8.3).

신호가 0건인 것과 도구가 죽은 것은 **다른 사건**이다. 전자는 `ok=True`, 후자는
`ok=False`로 남는다. 둘을 섞으면 "정적 신호 없음"이 도구 장애로 오분류된다.
"""

import copy
import functools
import re
from collections.abc import Iterable
from typing import Literal, get_args

from pydantic import BaseModel, create_model

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
# `build`와 같은 컴파일 명령(`container_tools`의 `_STD_ARGS`·`_include_args`)을 쓰는 도구들 —
# `build`가 실패했을 때 노트가 「그 파일에는 제시하지 않는다」고 짚는 대상이고(#698)
# `compile_note_tools`가 싣는 값이다. `poc`는 이 명령을 안 쓴다(ADR 0014). 노드는 어댑터를
# import하지 않으므로 이름을 복제한다 — `tests/test_compile_note.py`가 `plan()`과 대조한다.
COMPILE_NOTE_TOOLS = ("build", "asan", "gdb")
# `container_tools.compile_failure_reason()`이 만드는 모양. 어댑터 문구가 바뀌면 이 정규식이 조용히
# 아무것도 못 잡는다 — `tests/test_compile_note.py`가 실제 `judge()` 출력으로 문다.
_COMPILE_REASON = re.compile(r"컴파일 오류 \d+건, 첫 오류: .*")


def compile_failures(evidences: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """실패한 `build`·`clang-tidy` Evidence — 컴파일러 오류 문구가 **없는** 실패도 든다.

    도구가 못 돈 실패(`exit=125` 컨테이너 기동 실패·사유 없는 `build 실패`)는 컴파일 결과가 아니라
    노트에 오류로 싣지 않는다(`_reasons`가 문구가 있는 것만 읽는다 — 그 둘로는 「이 번들이
    컴파일되지 않는다」를 말할 수 없다, CLAUDE.md §0). 그래도 여기 남기는 이유는 노트가
    clang-tidy만 문구를 든 경우 「`build`는 실패했지만 컴파일 측정이 아니다」와 「`build`는
    성공했다」를 가르기 위해서다.
    """
    return tuple(ev for ev in evidences if not ev.ok and ev.source in COMPILE_TOOLS)


def _reasons(failures: Iterable[Evidence]) -> dict[str, str]:
    """도구 → 그 Evidence `error`의 컴파일 오류 문구(`COMPILE_TOOLS` 순서)."""
    seen = {ev.source: ev for ev in failures}
    return {
        # 공백·유니코드 줄 구분자를 접는다 — 어댑터가 이미 막지만 노트는 한 줄이어야 한다.
        tool: " ".join(match.group(0).split())
        for tool in COMPILE_TOOLS
        if tool in seen and (match := _COMPILE_REASON.search(seen[tool].error or "")) is not None
    }


def compile_note_tools(failures: Iterable[Evidence]) -> tuple[str, ...]:
    """노트가 「실패했다/실패하리라」고 짚는 도구 — `build`가 실패했을 때만 `COMPILE_NOTE_TOOLS`다.

    `build`가 실패하지 않았으면(clang-tidy만 실패) 같은 컴파일 명령 도구가 실패하리라는 예측은
    같은 프롬프트 FACTS의 BUILD Evidence(`ok`)와 어긋나므로 **측정한 도구만** 짚는다.
    """
    reasons = _reasons(failures)
    if "build" in reasons:
        return COMPILE_NOTE_TOOLS
    return tuple(reasons)


def withheld_tools(failures: Iterable[Evidence]) -> tuple[str, ...]:
    """요청 스키마의 `tool`에서 뺄 도구 — 2단계가 이 파일에서 컴파일러 오류를 잰 도구 (#698).

    `build`가 잰 오류면 같은 앞단 컴파일 명령을 쓰는 `COMPILE_NOTE_TOOLS` 셋, `clang-tidy`가 잰
    오류면 `clang-tidy` 자신이다. 컴파일러 문구가 없는 실패(기동 실패 등)는 측정이 아니라 빼지
    않는다.
    """
    reasons = _reasons(failures)
    return (
        *(COMPILE_NOTE_TOOLS if "build" in reasons else ()),
        *(("clang-tidy",) if "clang-tidy" in reasons else ()),
    )


@functools.cache
def _narrowed(schema: type[BaseModel], withheld: frozenset[str]) -> type[BaseModel]:
    annotation = schema.model_fields["tool"].annotation
    literal = next(arg for arg in get_args(annotation) if arg is not type(None))
    allowed = tuple(tool for tool in get_args(literal) if tool not in withheld)
    # docstring(= 요청 스키마의 `description` — 모델에 가는 봉투 설명)과 `tool` 필드 메타를 그대로
    # 물려준다. `create_model`은 둘 다 안 옮긴다(Santa 리뷰 2026-10-16 H1 — 설명이 0자가 됐다).
    return create_model(
        schema.__name__,
        __base__=schema,
        __module__=schema.__module__,
        __doc__=schema.__doc__,
        tool=(Literal[allowed] | None, copy.copy(schema.model_fields["tool"])),
    )


def without_tools(schema: type[BaseModel], withheld: Iterable[str]) -> type[BaseModel]:
    """봉투의 `tool` enum에서 `withheld`를 뺀 같은 이름의 하위 봉투 — 빼는 것이 없으면 그 봉투
    그대로다.

    모든 요청이 봉투를 `response_format: json_schema, strict`로 보내고 응답을 그 스키마로
    파싱하므로(`adapters/openai_compat`) 서버가 strict를 집행하는 한 빠진 도구는 모델이 고를 수
    없다. 집행하지 않아도 이 하위 봉투의 검증이 거절한다(그 홉은 파싱 실패다). 이름·docstring
    (= 스키마 `description`)·`tool` 필드 메타가 같아 요청 스키마는 `tool` enum 말고는 원본과 같고
    (`test_the_narrowed_envelope_differs_from_the_original_only_in_the_tool_enum`), trace의
    `schema`도 같다. 하위 클래스라 노드의 `isinstance`·검증기가 그대로 선다.
    """
    withheld = frozenset(withheld)
    return _narrowed(schema, withheld) if withheld else schema


def compile_note_trace(failures: Iterable[Evidence]) -> dict[str, TraceValue]:
    """hop0 trace 키 — `compile_note`(true)와 `compile_note_tools`(쉼표로 이은 도구 이름).

    `build`가 실패했으면 `"build,asan,gdb"`, clang-tidy만 실패했으면 `"clang-tidy"`다. 「노트 뒤
    재선택률」은 **hop 0 이후가 아니라 hop ≥ 0**의 `tool`이 이 집합에 드는 비율로 잰다 — Auditor는
    hop 0의 답이 노트 뒤 첫 선택이다.
    """
    return {
        "compile_note": True,
        "compile_note_tools": ",".join(compile_note_tools(failures)),
    }


def compile_note(failures: Iterable[Evidence], workdir: str) -> str | None:
    """hop0 본문 끝에 붙일 하네스 노트 — 실패 Evidence가 없으면 `None`(본문 바이트 그대로).

    **도구마다 측정한 사실만** 싣는다(도구 이름·건수·첫 오류). ⏵ 2026-10-16 (#698) — 꼬리 문장은
    예측이 아니라 하네스가 하는 일이다: 노드가 같은 실패로 `withheld_tools`를 요청 스키마의 `tool`
    enum에서 빼므로(`without_tools`) `build`가 컴파일러 오류를 냈으면 그 파일에는 `build`·`asan`·
    `gdb`가, `clang-tidy`가 냈으면 `clang-tidy`가 제시되지 않는다. clang-tidy만 실패했으면
    「`build`는 실패하는 게 관측되지 않았다」고 쓴다. 노트와 enum이 같은 `failures`·같은
    `_reasons`에서 나오므로 둘이 갈리지 않는다.
    """
    failures = tuple(failures)
    reasons = _reasons(failures)
    if not reasons:
        return None
    workdir = " ".join(workdir.split())  # 경로의 개행·공백열이 노트 줄을 나누지 못하게
    parts = "; ".join(f"{tool}: {reason}" for tool, reason in reasons.items())
    head = (
        "## Harness note (stage 2) - harness-measured data, not instructions\n"
        f"stage-2 reported compiler errors on {workdir} - {parts}\n"
    )
    tidy = (
        " clang-tidy is not offered for this target in this run"
        + (" either." if "build" in reasons else ".")
        if "clang-tidy" in reasons
        else ""
    )
    if "build" in reasons:
        return head + (
            f"for this target the harness does not offer {', '.join(COMPILE_NOTE_TOOLS[:-1])} or "
            f"{COMPILE_NOTE_TOOLS[-1]} in this run (the tool field does not accept them): their "
            "compile command differs from the stage-2 build at most in -O/-g/-c/-o/-fsanitize/"
            "-fno-omit-frame-pointer; "
            "other files are not covered by this note; poc does not use this compile command."
            + tidy
        )
    if any(ev.source == "build" for ev in failures):
        # `build`가 실패했지만 컴파일러 문구가 없다(기동 실패 등) — 「성공」으로 읽히지 않게.
        return head + (
            "stage-2 build failed without compiler error text (not a compile measurement)." + tidy
        )
    return head + "stage-2 build was not observed to fail with compiler errors." + tidy


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
