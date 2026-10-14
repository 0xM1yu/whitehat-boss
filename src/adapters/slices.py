"""슬라이서 산출물(`slices.json`)을 읽어 Scout의 `SOURCE`로 렌더한다 (ADR 0012 결정 1).

⏵ 2026-10-13 — 렌더가 `SOURCE`가 되는 것은 프롬프트가 `input: slice`를 선언했을 때다.
`input: source` 프롬프트의 Scout는 entry를 품는 함수 본문을 받는다(#529).

슬라이서(`slicer.py`)는 하네스 run 밖에서 Joern과 함께 미리 돌고, 여기는 **읽는 쪽**이다.
`src/`는 `slicer*.py`를 import하지 않는다 — 인덱서(`index_graph.py`)와 같이 JSON 파일 하나로만
만난다. 그래서 슬라이서의 규칙 중 이쪽이 따라야 하는 둘(줄 세기 · 바이트 해석)을 여기 다시 적는다.

## 슬라이스 1개가 타깃 1개다

`slices[]`의 한 항목이 러너 타깃 하나가 된다(배선은 러너 쪽). `case`가 v3 프롬프트의
`FACTS.slice_criterion.given_end`를 정한다 — Case A는 진입점에서 앞으로 자른 것(`source`),
Case B는 위험 연산에서 뒤로 자른 것(`sink`), Case F는 대상 지정 모드
(`tools/slicer_v2/slicer_target.py`)가 source에서 앞으로 자른 것(`source` — A와 같은 끝)이다.
`case`에서 읽는 것은 `given_end`뿐이라 A와 F는 이 아래 어디서도 안 갈린다 — 가드 스캔이 안
훑는 사유를 `given_source`로 적는 것이 그 때문이다(`orchestrator/source_guards.py`).

## 로드에서 죽는다 — `index_graph`와 반대다

`index_graph`는 index.json이 없거나 깨져도 run을 살리고 사유만 센다. 여기는 거꾸로다 —
`--slices`는 조작자가 **명시적으로 준** 입력이고, 그것을 못 믿는데 조용히 빈 타깃 목록으로
돌면 「슬라이스가 0개였다」와 「파일을 못 읽었다」가 같은 run이 된다. 모르는 판·`failed`·
CPG 없이 끝난 `partial`은 run 시작 전에 이유를 대고 멈춘다.

## 렌더 — 슬라이서가 본 파일과 같을 때만

`code`(배너 + 맨 줄)는 슬라이서가 **그때 읽은** 파일에서 조립한 것이다. 하네스는 자기 사본을
읽어 같은 조립을 다시 하고, `code`와 한 글자라도 다르면 렌더하지 않는다 — 줄번호 gutter가
다른 파일의 줄을 가리키게 되기 때문이다(v3는 그 번호를 인용하라고 시킨다).

**렌더는 run을 시작하기 전에 전부 한다**(`render_slices` — `factory.build_deps`와 `cli.run`의
시작 전 검사가 부른다). 슬라이스마다 그때그때 렌더하면 앞 슬라이스의 **모델 호출을 태운 뒤**
죽는다 — 그 낭비가 이 설계의 이유다.
⏵ **2026-10-02 정정**: 전 판은 *"러너는 렌더 오류를 잡지 않으므로 … 리포트·매니페스트 없이
죽는다"*고 적었다. 러너에 최후 방어선(`run_pipeline`의 맨 `except Exception`)이 생겨 **매니페스트는
남는다** — 다만 사유가 `unforeseen_failure`로 적히고 `report.md`는 안 남는다. **결론은 그대로다**:
태운 모델 호출은 안 돌아온다.
"""

import codecs
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.paths import PathEscape, safe_join
from src.schemas.candidate import InputContext, InputContextFunction, SliceRef
from src.schemas.common import Location

SUPPORTED_SLICE_VERSION = "slice-v2"
# 읽는 `analysis.input_context.version`(`tools/slicer_v2/input_context.py`의 `VERSION`) - 모르는
# 판은 모양을 믿을 수 없어 로드에서 죽는다.
SUPPORTED_INPUT_CONTEXT_VERSION = 1

# 슬라이서 `case` → v3 `slice_criterion.given_end`
# (`prompts/scout_*/v3/prefix.md` 「Slice criterion」: `source`면 앞으로, `sink`면 뒤로 자른 것).
# `F`는 `tools/slicer_v2/slicer_target.py`의 정방향 슬라이스다 — source에서 시작해 앞으로 자르므로
# 쥐여 준 끝은 `source`다(Case A와 같은 끝). 하네스가 `case`에서 읽는 것은 이 `given_end`뿐이다
# (`SliceRef.given_end` → Scout `slice_criterion`·가드 스캔·`NO_CALLER_IN_CPG` 판정).
_GIVEN_END = {"A": "source", "B": "sink", "F": "source"}


class SliceInputError(ValueError):
    """`slices.json`을 믿을 수 없다 — 읽기 실패·모르는 판·CPG 없음·파일 drift."""


def _decode_bad_bytes_as_latin1(exc: UnicodeDecodeError) -> tuple[str, int]:
    """무효 UTF-8 바이트만 Latin-1 글자로 — `slicer.py`의 같은 이름 처리기와 같은 규칙(#254)."""
    return bytes(exc.object[exc.start : exc.end]).decode("latin-1"), exc.end


codecs.register_error("harness_latin1_bad_bytes", _decode_bad_bytes_as_latin1)


def split_source(raw: bytes) -> list[str]:
    """슬라이서가 소스를 줄로 나누는 규칙 그대로(`slicer.py`의 소스 적재).

    CR을 접은 뒤 `\\n`으로만 자른다. `splitlines()`는 폼피드 같은 글자에서도 끊어 Joern의
    줄 번호와 어긋난다(#259).
    """
    text = raw.decode("utf-8", errors="harness_latin1_bad_bytes")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.removesuffix("\n").split("\n") if text else []


@dataclass(frozen=True)
class Slice:
    """`slices[]` 한 항목. 러너가 쓰는 필드만 든다 — 분석(`analysis`)은 절단 사유·싱크 cap·경계만
    싣고 줄 목록·카운터 같은 나머지 세부는 버린다. `candidate_manifest`의 `sink_arguments`(후보마다
    싱크 호출의 인자)는 아직 안 싣는다 — 미뤘다(#438)."""

    id: str
    candidate_key: str
    case: str
    # 슬라이스의 대표 위치 — `{file, line, symbol}`. Case B의 `symbol`은 감싸는 함수가 아니라
    # sink 호출 이름이다(ADR 0012 결정 1의 ⚠). 감싸는 함수는 `candidate_manifest`의 `scopes`에 있다.
    entry: dict
    members: tuple[str, ...]
    lines: tuple[tuple[str, int], ...]
    code: str
    # anchor-only 대체 슬라이스(#307): flow/backward 질의가 실패·건너뛴 후보라 dataflow 문맥이
    # 없다. 슬라이서가 `slices.json`에 이 marker를 남기는데, 러너가 안 실으면 consumer가 절단
    # 슬라이스를 full로 오인한다(@danielcho02 #307 리뷰). 기본 False라 정상 슬라이스는 영향 없다.
    degraded: bool = False
    # 줄별 출처 — slice-v2 `line_provenance`(`data_dependency`·`candidate_anchor`·`context` → 각
    # (file, line) 목록). Scout 창 트림이 어느 줄을 먼저 버릴지 정하는 데 쓴다(#375 HIGH-2 —
    # 전엔 하네스가 이 키를 안 읽었다). 없으면 빈 dict다(옛 슬라이스·degraded 범주 공백).
    provenance: dict[str, tuple[tuple[str, int], ...]] = field(default_factory=dict)
    # 슬라이서의 분석 사실 — `SliceRef`가 같은 이름으로 들고 가고 뜻은 거기 적었다. 없으면 옛
    # 슬라이스다.
    analysis_kind: str = ""
    truncated: bool = False
    truncation_reasons: tuple[str, ...] = ()
    sinks_capped: bool = False
    boundaries: tuple[tuple[str, str, int], ...] = ()
    # `analysis.input_context` - `None`은 키가 없다. `SliceRef`가 같은 이름으로 들고 간다.
    input_context: InputContext | None = None

    @property
    def given_end(self) -> str:
        return _GIVEN_END[self.case]


@dataclass(frozen=True)
class SliceSet:
    status: str
    run_signature: str | None
    slices: tuple[Slice, ...]
    candidate_manifest: dict


def load_slices(path: Path) -> SliceSet:
    """`slices.json`을 읽고 검증한다. 믿을 수 없으면 `SliceInputError`."""
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise SliceInputError(f"{path}: 슬라이스 파일을 읽지 못했다 — {exc}") from exc
    if not isinstance(document, dict):
        raise SliceInputError(f"{path}: 최상위가 객체가 아니다")
    version = document.get("slice_version")
    if version != SUPPORTED_SLICE_VERSION:
        raise SliceInputError(
            f"{path}: 슬라이스 판 {version!r}는 읽지 않는다 — {SUPPORTED_SLICE_VERSION}만 받는다"
        )
    if document.get("shard") is not None:
        # `slicer.py --shard I/N`의 조각은 후보의 1/N만 든다 — 넘기면 나머지를 조용히 떨군다.
        raise SliceInputError(
            f"{path}: 샤드 조각({document['shard']})이다 — tools/merge_slicer_shards.py로 합친 "
            "체크포인트의 --export-only 결과를 넘겨라"
        )
    if document.get("prepare_only"):
        # `slicer.py --prepare-only`은 CPG 저장·후보 발견만 하고 아무것도 안 자른다(#368) —
        # `partial`·`slices: []`라 넘기면 슬라이스 0건 run으로 읽힌다.
        raise SliceInputError(
            f"{path}: --prepare-only 준비 결과다 — 슬라이스가 없다. 샤드(--cpg)를 돌려 합친 "
            "체크포인트의 --export-only 결과를 넘겨라"
        )
    status = document.get("status")
    if status not in ("ok", "partial"):
        raise SliceInputError(f"{path}: 슬라이서 status가 {status!r}다 — 쓸 슬라이스가 없다")
    if status == "partial" and document.get("joern_engine_config") is None:
        raise SliceInputError(f"{path}: partial인데 CPG 설정이 없다 — CPG에 닿기 전에 끝난 run이다")
    items = document.get("slices")
    if not isinstance(items, list):
        raise SliceInputError(
            f"{path}: 슬라이스 목록(`slices`)이 배열이 아니다 — {type(items).__name__}"
        )
    try:
        slices = tuple(_slice(item) for item in items)
    except (KeyError, TypeError, ValueError) as exc:
        raise SliceInputError(f"{path}: 슬라이스 항목 모양이 slice-v2가 아니다 — {exc!r}") from exc
    seen: set[str] = set()
    for item in slices:
        if item.id in seen:
            raise SliceInputError(f"{path}: 슬라이스 id {item.id}가 둘이다")
        seen.add(item.id)
    try:
        manifest = _manifest(document.get("candidate_manifest"))
    except ValueError as exc:
        raise SliceInputError(
            f"{path}: candidate_manifest 모양이 slice-v2가 아니다 — {exc}"
        ) from exc
    return SliceSet(
        status=status,
        run_signature=document.get("run_signature"),
        slices=slices,
        candidate_manifest=manifest,
    )


def _manifest(raw: object) -> dict:
    """`candidate_manifest` — **`slice_refs`가 읽는 길**(항목 → `scopes` → 원소 → `symbol`)만 본다.

    그 길의 모양을 로드에서 안 보면 `slice_refs`가 run 디렉터리가 생긴 뒤 `.get()`에서 죽는다
    (#284 리뷰). 없거나 `null`이면 빈 manifest다 — 슬라이스마다 `scopes`가 없는 것과 같다.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"객체가 아니다 — {type(raw).__name__}")
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{key}: 객체가 아니다")
        scopes = entry.get("scopes") or []
        if not isinstance(scopes, list) or not all(isinstance(s, dict) for s in scopes):
            raise ValueError(f"{key}: scopes가 객체 배열이 아니다")
        if not all(isinstance(s.get("symbol"), str | None) for s in scopes):
            raise ValueError(f"{key}: scopes[].symbol이 문자열이 아니다")
    return dict(raw)


def _provenance(raw: object) -> dict[str, tuple[tuple[str, int], ...]]:
    """slice-v2 `line_provenance`(범주 → `[[file, line], ...]`)를 읽는다. 없거나 `null`이면 `{}`다
    (옛 슬라이스). slice-v2가 내는 세 범주 밖 키는 **버린다** — Scout 트림(#375)이 아는 셋만
    쓴다."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"line_provenance: 객체가 아니다 — {type(raw).__name__}")
    out: dict[str, tuple[tuple[str, int], ...]] = {}
    for key in ("data_dependency", "candidate_anchor", "context"):
        points = raw.get(key) or []
        out[key] = tuple((str(p[0]), int(p[1])) for p in points)
    return out


def _object(raw: object, name: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"{name}: 객체가 아니다 — {type(raw).__name__}")
    return raw


def _kind(raw: object) -> str:
    """`analysis_kind` — 없거나 `null`이면 빈 문자열(슬라이서가 안 적었다 = 모른다)이다."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise TypeError(f"analysis_kind: 문자열이 아니다 — {type(raw).__name__}")
    return raw


def _flag(raw: object, name: str) -> bool:
    """JSON 불리언만 받는다 — 없거나 `null`이면 `False`다. `bool("false")`는 참이라 문자열·숫자를
    `bool()`로 바꾸면 깨진 입력이 「잘렸다」로 읽힌다."""
    if raw is None:
        return False
    if type(raw) is not bool:
        raise TypeError(f"{name}: 불리언이 아니다 — {type(raw).__name__}")
    return raw


def _array(raw: object, name: str) -> list:
    """JSON 배열만 받는다 — 없거나 `null`이면 빈 목록이다. 문자열을 그대로 돌면 글자마다 하나가
    된다(`"backward_depth"` → 서로 다른 글자 12개가 사유로)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"{name}: 배열이 아니다 — {type(raw).__name__}")
    return raw


def _int(raw: object, name: str) -> int:
    """JSON 정수만 받는다 - `bool`은 `int`의 서브클래스라 따로 거른다. 강제 변환하지 않는다."""
    if type(raw) is not int:
        raise TypeError(f"{name}: 정수가 아니다 - {type(raw).__name__}")
    return raw


def _listed(raw: object, name: str) -> list:
    """`_array`와 달리 `null`을 빈 목록으로 읽지 않는다 - 읽는 키가 `null`이면 모양이 틀린
    것이다."""
    if not isinstance(raw, list):
        raise TypeError(f"{name}: 배열이 아니다 - {type(raw).__name__}")
    return raw


def _str(raw: object, name: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{name}: 문자열이 아니다 - {type(raw).__name__}")
    return raw


def _input_context(item: dict) -> InputContext | None:
    """`analysis.input_context`에서 **하네스가 쓰는 키만** 엄격히 읽는다. 키가 없으면 `None`
    (`--index` 없이 돈 슬라이스)이고 `none_selected`와 다르다 - `null`은 키가 없는 것이 아니라
    모양이 틀린 것이다. 모양이 틀리면(모르는 판 · 모르는 `status`/`basis` · 불리언이 아닌
    `cap_hit` · 함수 위치가 정수가 아니다 · 끝 줄이 시작 줄 앞) `KeyError`/`TypeError`/
    `ValueError`로 로드를 멈춘다 - 강제 변환하면 깨진 입력이 사실처럼 읽힌다.

    클러스터가 합친 슬라이스는 `analysis`가 대표 후보의 것이다 - 그 대표의 선택을 읽는다
    (구성원별 분석은 `member_analysis`에 있고 여기서는 안 읽는다). `carried_decoders`·
    `dispatches`는 싣지 않는 것들이라 **개수만** 센다."""
    analysis = _object(item.get("analysis"), "analysis")
    if "input_context" not in analysis:
        return None
    raw = analysis["input_context"]
    if not isinstance(raw, dict):
        raise TypeError(f"input_context: 객체가 아니다 - {type(raw).__name__}")
    # 읽는 키는 **전부 있어야 한다**(`[]` 색인 → 없으면 `KeyError`로 로드가 선다). 없는 `cap_hit`을
    # `False`로 읽으면 「상한에 안 닿았다」는 안 잰 사실이 된다.
    context = raw
    version = _int(context["version"], "input_context.version")
    if version != SUPPORTED_INPUT_CONTEXT_VERSION:
        raise ValueError(
            f"input_context.version {version}는 읽지 않는다 - "
            f"{SUPPORTED_INPUT_CONTEXT_VERSION}만 받는다"
        )
    status, basis = context["status"], context["basis"]
    if status not in ("selected", "none_selected"):
        raise ValueError(f"input_context.status {status!r}")
    if basis not in ("cpg", "syntax"):
        raise ValueError(f"input_context.basis {basis!r}")
    functions = []
    for entry in _listed(context["functions"], "input_context.functions"):
        if not isinstance(entry, dict):
            raise TypeError(f"functions[]: 객체가 아니다 - {type(entry).__name__}")
        start = _int(entry["line_start"], "functions[].line_start")
        end = _int(entry["line_end"], "functions[].line_end")
        if not 1 <= start <= end:
            raise ValueError(f"functions[]: 줄 범위 {start}-{end}")
        symbol = entry["symbol"]
        functions.append(
            InputContextFunction(
                file=_str(entry["file"], "functions[].file"),
                symbol=None if symbol is None else _str(symbol, "functions[].symbol"),
                line_start=start,
                line_end=end,
            )
        )
    if type(context["cap_hit"]) is not bool:
        got = type(context["cap_hit"]).__name__
        raise TypeError(f"input_context.cap_hit: 불리언이 아니다 - {got}")
    return InputContext(
        version=version,
        status=status,
        basis=basis,
        cap_hit=context["cap_hit"],
        cap_reasons=tuple(
            _str(reason, "cap_reasons[]")
            for reason in _listed(context["cap_reasons"], "cap_reasons")
        ),
        functions=tuple(functions),
        carried_decoders=len(_listed(context["carried_decoders"], "carried_decoders")),
        dispatches=len(_listed(context["dispatches"], "dispatches")),
    )


def _analysis_facts(
    item: dict,
) -> tuple[tuple[str, ...], tuple[tuple[str, str, int], ...], bool]:
    """`analysis`의 절단 사유(`truncation_reasons`) · 역추적이 멈춘 자리(`boundaries`) · flows의
    싱크 cap(`sinks_capped`).

    클러스터링이 합친 슬라이스의 `analysis`는 **대표 후보의 것**이고 구성원별 분석은
    `member_analysis`에 있다(대표 자신도 든다 — `slicer_source.cluster`). 최상위 `truncated`가
    구성원의 OR이라 셋 다 구성원 전부에서 모은다(cap은 OR). 키가 없으면(옛 슬라이스 · 사유·경계를
    안 내는 Case A · cap을 안 내는 Case B) 빈 튜플·`False`다. 사유는 정렬하고 경계는 처음 나온
    순서로 중복을 뺀다. 모양이 틀리면(사유·경계가 배열이 아니다 · 사유가 문자열이 아니다 · cap이
    불리언이 아니다) `TypeError`로 로드를 멈춘다 — 강제 변환하면 깨진 입력이 사실처럼 읽힌다.
    """
    members = _object(item.get("member_analysis"), "member_analysis")
    reasons: dict[str, None] = {}
    boundaries: dict[tuple[str, str, int], None] = {}
    sinks_capped = False
    for raw in list(members.values()) or [item.get("analysis")]:
        analysis = _object(raw, "analysis")
        for reason in _array(analysis.get("truncation_reasons"), "truncation_reasons"):
            if not isinstance(reason, str):
                raise TypeError(f"truncation_reasons: 문자열이 아니다 — {type(reason).__name__}")
            reasons[reason] = None
        sinks_capped = _flag(analysis.get("sinks_capped"), "sinks_capped") or sinks_capped
        for boundary in _array(analysis.get("boundaries"), "boundaries"):
            where = boundary["location"]
            boundaries[(str(boundary["reason"]), str(where["file"]), int(where["line"]))] = None
    return tuple(sorted(reasons)), tuple(boundaries), sinks_capped


def _slice(item: dict) -> Slice:
    case = item["case"]
    if case not in _GIVEN_END:
        raise ValueError(f"case {case!r}")
    entry = item["entry"]
    entry = {"file": str(entry["file"]), "line": int(entry["line"]), "symbol": entry["symbol"]}
    code = item["code"]
    if not isinstance(code, str):
        raise TypeError("code")
    truncation_reasons, boundaries, sinks_capped = _analysis_facts(item)
    return Slice(
        id=str(item["id"]),
        candidate_key=str(item["candidate_key"]),
        case=case,
        entry=entry,
        members=tuple(str(m) for m in item["members"]),
        lines=tuple((str(p["file"]), int(p["line"])) for p in item["lines"]),
        code=code,
        degraded=bool(item.get("degraded", False)),
        provenance=_provenance(item.get("line_provenance")),
        analysis_kind=_kind(item.get("analysis_kind")),
        truncated=_flag(item.get("truncated"), "truncated"),
        truncation_reasons=truncation_reasons,
        sinks_capped=sinks_capped,
        boundaries=boundaries,
        input_context=_input_context(item),
    )


def slice_refs(loaded: SliceSet) -> tuple[SliceRef, ...]:
    """러너 타깃으로 넘길 모양. `Location`은 entry의 file·line + **감싸는 함수 이름**이다.

    Case B의 `entry.symbol`은 sink 호출 이름(`memcpy` 등)이라 심볼 이름으로 못 쓴다 —
    대표 후보의 `scopes[0].symbol`(Joern이 준 메서드 이름)을 쓴다. `scopes`가 없으면 Case A는
    entry의 이름으로 떨어지고(entry가 메서드 자신이다) **Case B는 비운다** — sink 이름을 함수
    이름 자리에 조용히 적지 않는다(#284 리뷰). `scopes[].file`은 Joern 원경로라 쓰지 않는다 —
    파일은 이미 상대화된 entry의 것이다. entry는 `SliceRef.entry`로 **그대로** 따로 싣는다 —
    v3 `slice_criterion.given`이 그것이다. 줄은 `SliceRef.lines`로 싣는다 — Auditor가 슬라이스가
    걸친 함수들을 찾는다(결정 1-b).
    """
    refs = []
    for item in loaded.slices:
        scopes = (loaded.candidate_manifest.get(item.candidate_key) or {}).get("scopes") or []
        symbol = scopes[0].get("symbol") if scopes else None
        entry = Location(
            file=item.entry["file"], line_start=item.entry["line"], symbol=item.entry["symbol"]
        )
        fallback = entry.symbol if item.case == "A" else None
        location = Location(
            file=entry.file, line_start=entry.line_start, symbol=symbol or fallback
        )
        refs.append(
            SliceRef(
                slice_id=item.id,
                location=location,
                given_end=item.given_end,
                entry=entry,
                lines=item.lines,
                degraded=item.degraded,
                # 줄별 출처 — Scout 트림이 anchor > dependency > context 순으로 버린다(#375 HIGH-2).
                provenance=item.provenance,
                analysis_kind=item.analysis_kind,
                truncated=item.truncated,
                truncation_reasons=item.truncation_reasons,
                sinks_capped=item.sinks_capped,
                boundaries=item.boundaries,
                input_context=item.input_context,
            )
        )
    return tuple(refs)


def slicer_summary(loaded: SliceSet) -> dict[str, str | int | None]:
    """`RunRecord.slicer`가 될 값 — CPG가 못 본 줄의 한계가 기록에 남는 자리(ADR 0012 결정 1)."""
    return {
        "status": loaded.status,
        "run_signature": loaded.run_signature,
        "slices": len(loaded.slices),
    }


def render(item: Slice, read_bytes: Callable[[str], bytes]) -> str:
    """배너 + `<원본줄>| <코드>` gutter. 하네스 사본이 슬라이서가 본 것과 다르면 `SliceInputError`.

    `read_bytes`는 저장소 상대 경로를 받아 원 바이트를 돌려준다 — 루트 탈출 검사는 부르는
    쪽 몫이다(`render_slices` — `src/paths.py`).
    """
    files = sorted({file for file, _ in item.lines})
    sources = {file: split_source(read_bytes(file)) for file in files}
    plain: list[str] = []
    gutter: list[str] = []
    for file in files:
        banner = f"/* ===== {file} ===== */"
        plain.append(banner)
        gutter.append(banner)
        for _, line in sorted(p for p in item.lines if p[0] == file):
            text = sources[file][line - 1] if 0 < line <= len(sources[file]) else None
            if text is None:
                raise SliceInputError(f"{item.id}: {file}:{line}이 하네스 사본에 없다 — drift")
            plain.append(text)
            gutter.append(f"{line}| {text}")
    if "\n".join(plain) != item.code:
        raise SliceInputError(
            f"{item.id}: {', '.join(files)}이 슬라이서가 본 것과 다르다 — drift라 렌더하지 않는다"
        )
    return "\n".join(gutter)


def render_slices(loaded: SliceSet, root: Path) -> dict[str, str]:
    """슬라이스 id → 렌더. run을 시작하기 **전에** 전부 한다(모듈 docstring 「렌더」 절).

    entry 파일이 대상 트리(`root`)에 없는 슬라이스는 렌더하지 않는다 — 러너가 그 슬라이스를
    타깃으로 못 세우고 `target_plan.unmatched_slices`로 센다. drift는 빼지 않고
    `SliceInputError`로 올린다 — 조용히 빼면 「슬라이스가 적었다」로 읽힌다.

    읽기는 `root` 아래로만 간다(`src/paths.py`). `slices.json`의 경로는 하네스가 만든 값이
    아니라서 루트 밖을 가리키면 못 믿는 입력으로 멈춘다.
    """

    def read_bytes(relative: str) -> bytes:
        try:
            return _under(root, relative).read_bytes()
        except OSError as exc:
            raise SliceInputError(f"{relative}: 하네스 사본에 없다 — drift ({exc})") from exc

    renderable = [item for item in loaded.slices if _under(root, item.entry["file"]).is_file()]
    # `analysis.input_context.functions[].file`도 같은 못 믿는 경로다 — 5단계의 `read_source`가
    # 읽는데 거기서 `PathEscape`가 나면 run 전체가 죽는다(Scout가 GPU를 쓴 뒤). 그래서 여기서 먼저
    # 막는다. 읽히는 것만 본다 — `carry_input_context`가 읽는 것은 렌더되는 슬라이스의 `selected`
    # + `cpg`뿐이라, 안 읽는 슬라이스의 경로까지 막으면 쓰지도 않을 입력으로 run이 멈춘다.
    for item in renderable:
        context = item.input_context
        if context is not None and context.status == "selected" and context.basis == "cpg":
            for function in context.functions:
                _under(root, function.file)
    return {item.id: render(item, read_bytes) for item in renderable}


def _under(root: Path, relative: str) -> Path:
    try:
        return safe_join(root, relative)
    except PathEscape as exc:
        raise SliceInputError(str(exc)) from exc
