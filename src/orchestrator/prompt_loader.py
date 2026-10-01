"""프롬프트 파일 로더 (agent-contracts.md §4).

**prefix/body 분리를 구조적으로 강제한다.** 합쳐진 문자열 하나를 받는 경로를 만들지
않는다 — `render()`는 언제나 `Prompt(prefix=..., body=...)`를 돌려주고, 노드는 그것을
그대로 `ctx.call_model()`에 넘긴다. 공통 system prompt + weakness checklist +
output schema는 prefix에 고정되고 후보별 가변부는 항상 body로 간다. 이 분리가 무너지면
vLLM automatic prefix caching이 빗나가고 cache hit ratio(핵심 지표)를 잴 수 없다.

## 파일 구조

    prompts/<role>/<version>/prefix.md    # 캐시되는 고정부
    prompts/<role>/<version>/body.md      # {{TOKEN}} 자리표시자를 가진 가변부
    prompts/<role>/<version>/meta.yaml    # role·version·output_schema·input

버전은 config에서 온다(`ModelEntry.prompt_template_version`). 노드가 문자열로 박지
않는다 — 버전이 바뀌면 이전 결과와 직접 비교하지 않는다는 규약(§4.1)이 성립하려면
그 값이 `RunRecord.prompt_versions`와 같은 곳에서 나와야 한다.

## meta의 `input` — 그 프롬프트의 `SOURCE`가 무엇인가

`source`(원본 소스 — 키가 없으면 이것)와 `slice`(슬라이서가 자른 슬라이스 — ADR 0012 결정 1)
둘뿐이다. v3 Scout가 `slice`다 — `SOURCE`를 원본 줄번호 gutter·파일 배너가 붙은 슬라이스로
정의한다. 로더는 값을 닫기만 하고, `slice` 프롬프트를 `--slices` 없이 부르는 run을 막는 것은
`cli.run`이다. **모르는 값은 기본값으로 읽지 않는다** — 오타 하나가 그 가드를 조용히 끈다.

## meta의 `source_options` — 그 프롬프트가 서술하는 Auditor `SOURCE` 옵션

`config.AUDITOR_SOURCE_OPTIONS` 중 그 프롬프트의 입력 서술이 다루는 키의 목록이다(없으면 빈
목록 — 결정 1-b의 SOURCE만 서술한다). 옵션을 켜면 SOURCE 모양이 바뀌는데(피호출 본문·정의·
호출자 창이 뒤에 붙고, 상한이 토큰이 되고, entry 함수가 줄어든다) 프롬프트가 옛 모양을 말하면
모델이 받는 서술이 거짓이 된다. 켜진 옵션이 이 목록에 없으면 `cli`가 run을 막는다(#351 리뷰
MEDIUM-2). **모르는 키는 거절한다** — 오타 하나가 그 가드를 조용히 끈다(`input`과 같은 판단).

## body의 마지막 ```json 펜스

**그 블록이 모델이 읽는 사실 전부다**(S1 계약). ID는 노드가 미리 발급해 여기 실어
보낸다 — 구조화 출력 스키마가 ID를 필수 필드로 갖기 때문에(`Candidate.candidate_id`)
모델이 ID를 지어내면 안 된다. `build_facts()`가 그 딕셔너리를 만든다.

## 누출 방지

프롬프트에 CVE ID·문제명·flag·patch diff를 넣지 않는다(evaluation.md §4).
C6의 leakage 스캐너가 `prompts/`도 검사한다.
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from src.config import AUDITOR_SOURCE_OPTIONS
from src.interfaces.model_client import Prompt
from src.schemas.capsule import ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import Role
from src.schemas.evidence import Evidence

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# body.md의 자리표시자. 대문자만 허용해 본문 텍스트와 섞이지 않게 한다.
_TOKEN = re.compile(r"\{\{(?P<name>[A-Z_]+)\}\}")

# 사실 블록 자리표시자. 다른 토큰과 달리 JSON으로 직렬화해 채운다.
FACTS_TOKEN = "FACTS"

# `build_facts()`가 만드는 키. S1이 굳힌 계약이며 어댑터의 `FakeContext`와 같은 집합이다
# (`extra="forbid"`이므로 여기서 키를 하나 더 넣으면 Fake가 로드 시점에 실패한다).
#
# **`slice_criterion`만 opt-in이다** — 슬라이스 타깃의 Scout 프롬프트에만 실린다(ADR 0012 결정 1,
# v3 prefix의 「Slice criterion」 절). 나머지 여덟은 늘 실린다. 슬라이스 없는 프롬프트는 이 키가
# 생기기 전과 글자가 같다 — 그래서 이 튜플은 「허용」 목록이지 「늘 있는」 목록이 아니다.
FACT_KEYS = (
    "run_id",
    "ids",
    "artifact_id",
    "target",
    "capsule_sections",
    "source",
    "evidence",
    "callers",
    "slice_criterion",
    # 퍼즈 진입점(`LLVMFuzzerTestOneInput`)과 그 본문. **PoV만 싣는다** — `slice_criterion`과 같은
    # opt-in이라 인자를 안 주는 역할의 프롬프트는 바이트가 그대로다(`docs/evaluation.md:69` —
    # 프롬프트가 바뀌면 이전 결과와 직접 비교하지 않는다).
    "fuzz_entrypoint",
)


class PromptError(Exception):
    """프롬프트 파일이 없거나 자리표시자가 맞지 않는다. 조용히 빈 값으로 채우지 않는다."""


# meta.yaml의 `input`이 가질 수 있는 값(모듈 docstring 「meta의 `input`」).
INPUT_KINDS = ("source", "slice")


@dataclass(frozen=True)
class PromptTemplate:
    """한 역할·버전의 프롬프트. prefix는 재사용되고 body만 매 호출 렌더링된다."""

    role: Role
    version: str
    prefix: str
    body: str
    output_schema: str
    # `INPUT_KINDS` 중 하나. meta에 키가 없으면 `source`다.
    input: str
    # 이 프롬프트가 서술하는 Auditor `SOURCE` 옵션(모듈 docstring 「meta의 `source_options`」).
    source_options: tuple[str, ...] = ()

    def render(self, *, facts: Mapping[str, object], **fields: str) -> Prompt:
        """가변부를 채운 `Prompt`. prefix는 손대지 않는다.

        자리표시자가 남거나 쓰이지 않은 인자가 있으면 실패한다 — 채워지지 않은
        `{{SOURCE}}`가 그대로 모델에 가면 "모델이 못 찾았다"로 집계된다.
        """
        values = {name.upper(): value for name, value in fields.items()}
        values[FACTS_TOKEN] = json.dumps(
            _validated_facts(facts), ensure_ascii=False, indent=2, sort_keys=True
        )
        required = {match.group("name") for match in _TOKEN.finditer(self.body)}
        missing = sorted(required - values.keys())
        unused = sorted(values.keys() - required)
        if missing or unused:
            raise PromptError(
                f"{self.role.value}/{self.version} body 자리표시자 불일치: "
                f"빠짐={missing}, 남음={unused}"
            )
        rendered = _TOKEN.sub(lambda m: values[m.group("name")], self.body)
        return Prompt(prefix=self.prefix, body=rendered, prompt_version=self.version)


def load_prompt(role: Role, version: str, *, root: Path = PROMPTS_DIR) -> PromptTemplate:
    """역할·버전에 해당하는 프롬프트를 읽는다. 같은 인자에는 같은 객체를 돌려준다."""
    return _load_cached(role, version, root)


@cache
def _load_cached(role: Role, version: str, root: Path) -> PromptTemplate:
    directory = root / role.value / version
    prefix = _read(directory / "prefix.md")
    body = _read(directory / "body.md")
    meta = _read_meta(directory / "meta.yaml")

    if meta.get("role") != role.value or str(meta.get("version")) != version:
        raise PromptError(
            f"{directory}의 meta.yaml이 디렉터리와 어긋난다: "
            f"role={meta.get('role')!r} version={meta.get('version')!r}"
        )
    if prefix.strip() and prefix.strip() in body:
        raise PromptError(
            f"{directory}: prefix가 body에 복사되어 있다. 고정부를 body에 넣으면 "
            "prefix cache가 빗나가고 cache hit ratio를 잴 수 없다"
        )
    if FACTS_TOKEN not in {m.group("name") for m in _TOKEN.finditer(body)}:
        raise PromptError(f"{directory}/body.md에 {{{{{FACTS_TOKEN}}}}} 블록이 없다")
    output_schema = meta.get("output_schema")
    if not isinstance(output_schema, str) or not output_schema:
        raise PromptError(f"{directory}/meta.yaml에 output_schema가 없다")
    input_kind = meta.get("input", "source")
    if input_kind not in INPUT_KINDS:
        raise PromptError(
            f"{directory}/meta.yaml의 input {input_kind!r}은 모르는 값이다 — "
            f"{list(INPUT_KINDS)} 중 하나다"
        )

    source_options = meta.get("source_options", [])
    if not isinstance(source_options, list) or not set(source_options) <= set(
        AUDITOR_SOURCE_OPTIONS
    ):
        raise PromptError(
            f"{directory}/meta.yaml의 source_options {source_options!r}에 모르는 값이 있다 — "
            f"{list(AUDITOR_SOURCE_OPTIONS)} 중에서 고른다"
        )

    return PromptTemplate(
        role=role,
        version=version,
        prefix=prefix,
        body=body,
        output_schema=output_schema,
        input=input_kind,
        source_options=tuple(source_options),
    )


# 근거 본문 절단 — Critic·Auditor의 content-aware 버전(각 노드의 `_CONTENT_AWARE_VERSIONS`)이
# 같은 상한을 쓴다(#237 HIGH-1 · Auditor는 2026-09-29부터). 결정론적 truncation — 모델 개입
# 없음, 재현 가능.
EVIDENCE_BODY_CHAR_CAP = 800  # evidence 1건당 상한
EVIDENCE_BODY_TOTAL_CAP = 4000  # 전 evidence 합 상한(프롬프트 토큰 예산)


_TRUNCATION_MARKER = "\n...\n"

# 하네스가 도구 로그에 쓰는 실행 기록 머리줄. `evidence_summaries`가 **절단 전에**
# 뺀다(#256 항목 3). 로그(`adapters/container_tools.py`의
# `ContainerToolRunner._execute`)는 도구 출력 앞에 노드의 자리표시자 argv(`# node argv: `) ·
# 런타임 이름(`# runtime: `) · 명령마다 실제로 돈 명령줄(`$ <런타임> …`)을 쓴다. 명령줄은
# 호스트 절대경로(바인드한 대상 루트 · scratch, singularity면 SIF 파일)를 싣는다 — 남겨 두면
# 그 경로가 모델 입력에 실리고, 증거당 상한의 head를 먹어 도구 출력(semgrep의 `check_id`
# 등)이 잘린다. 로그 파일과 `digest`는 그대로다 — 모델로 가는 사본에서만 뺀다.
#
# 나머지 하네스 줄 — 발견 수(`# semgrep: …`) · `(exit=N)`·`(timeout after …)` · 실행 예외
# (`(<예외형>: …)`) · 실행 전 거절 사유 · 절 구분(`--- stdout ---`·`--- stderr ---`) · 미실행
# 명령 수 — 은 결과·실패 사유를 말하거나 절을 갈라서 남긴다. 머리줄은 **줄 머리**에서만
# 보고, `$ `는 런타임 이름과 공백이 이어질 때만 머리줄이다(`$ echo …`는 남는다). 런타임
# 이름은 `container_tools.UNENFORCED`의 키와 맞아야 하는데 어댑터를 import할 수 없어
# (engineering-guide.md §3) 여기 따로 든다 — tests/test_evidence_payload_header.py가
# 런타임마다 실제 러너로 대조한다.
#
# ⚠ 뺀 뒤에도 **절단이지 요약이 아니다** — semgrep JSON의 `message` 같은 가운데는 잘릴
# 수 있다(구조 추출은 #256 항목 3에 남는다). 그리고:
# - 못 빼는 호스트 경로 — 실행 전 거절 사유(`# 실행하지 않았다: …` — 그 줄이 곧 실패
#   사유다), 런타임 CLI가 stderr에 쓴 문구(도구 stderr와 한 절이라 가를 표지가 없다),
#   줄바꿈이 쪼갠 명령줄의 둘째 줄: env 키·값에 줄바꿈이 있으면 그 뒤의 바인드 인자가
#   거기 가고(지금 env를 채우는 노드는 없다), singularity면 작업 디렉터리 이름의 줄바꿈
#   뒤에 SIF 경로가 간다. 호스트 경로(대상 루트 · artifacts 루트) 자체에 줄바꿈이 있어도 그
#   뒷부분이 남는다. 파일 이름의 줄바꿈은 컨테이너 경로 조각만 남긴다.
# - 오탐 — 도구 출력의 줄 머리가 `$ docker `·`$ podman `·`$ singularity `·`# runtime: `·
#   `# node argv: `이면 그 줄도 모델 사본에서 빠진다. 호스트 경로를 놓치는 것보다 싸서
#   받아들인 비용이다. Auditor가 받는 도구에서는 사실상 없다 — semgrep `--json` 출력은 한
#   물리 줄이라 구조적으로 0, clang-tidy·build는 인용한 소스 줄의 머리가 그 접두일 때만,
#   index의 stdout은 `# index: …` 한 줄뿐이다.
# - 읽기 상한(`PAYLOAD_READ_CAP`)이 머리줄 안에서 끝나면 그 뒤 도구 출력 없이 `truncated`가
#   False일 수 있다(본문이 빌 수도 있다) — 리더는 자른 사실을 알리지 않는다.
_HARNESS_HEADER_LINE = re.compile(
    r"^(?:# node argv: |# runtime: |\$ (?:docker|podman|singularity) )[^\n]*\n?", re.MULTILINE
)


def _bound_text(text: str, cap: int) -> tuple[str, bool]:
    """cap을 넘으면 head 2/3 + tail 1/3로 결정론 절단하고 truncated=True.

    **구분자(`\\n...\\n`)까지 cap 안에 든다** — 결과 길이는 `cap`을 넘지 않는다(예산 초과
    방지). cap이 구분자보다 작으면 tail 없이 head만 자른다 — `text[-0:]`가 원문 전체를
    돌려주던 작은 cap 버그를 막는다.
    """
    text = text.strip()
    if len(text) <= cap:
        return text, False
    budget = cap - len(_TRUNCATION_MARKER)
    if budget <= 0:
        return text[:cap], True
    head_len = (budget * 2) // 3
    tail_len = budget - head_len
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    return f"{head}{_TRUNCATION_MARKER}{tail}", True


def evidence_summaries(
    evidences: Iterable[Evidence],
    read_payload: Callable[[str], str | None],
    *,
    per_cap: int = EVIDENCE_BODY_CHAR_CAP,
    total_cap: int = EVIDENCE_BODY_TOTAL_CAP,
) -> dict[str, dict[str, object]]:
    """`payload_ref`에서 근거 본문을 읽어 결정론적으로 절단한 evidence_id → {body, truncated} 맵.

    **하네스가 쓴 실행 기록 머리줄(`_HARNESS_HEADER_LINE`)은 절단 전에 뺀다** — 절단 예산을 도구
    출력에 쓰고 명령줄의 호스트 경로를 모델에 싣지 않는다(못 빼는 형태와 오탐은 그 정규식의
    주석). 로그 파일은 그대로다.

    payload가 없으면(fake·미배선·파일 부재) 그 evidence는 맵에서 빠진다 → 호출부는 그
    evidence를 metadata만으로 싣는다. 총 예산을 넘으면 이후 evidence는 body를 비운다.
    """
    out: dict[str, dict[str, object]] = {}
    used = 0
    for ev in evidences:
        raw = read_payload(ev.payload_ref)
        if raw is None:
            continue
        remaining = total_cap - used
        if remaining <= 0:
            out[ev.evidence_id] = {"body": "", "truncated": True}
            continue
        body, truncated = _bound_text(_HARNESS_HEADER_LINE.sub("", raw), min(per_cap, remaining))
        used += len(body)
        out[ev.evidence_id] = {"body": body, "truncated": truncated}
    return out


def build_facts(
    *,
    run_id: str,
    ids: Mapping[str, str],
    artifact_id: str = "",
    target: Location | None = None,
    capsule: ContextCapsule | None = None,
    source: str = "",
    evidences: Iterable[Evidence] = (),
    callers: Iterable[str] = (),
    evidence_bodies: Mapping[str, Mapping[str, object]] | None = None,
    slice_criterion: Mapping[str, object] | None = None,
    fuzz_entrypoint: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """마지막 ```json 펜스에 들어갈 사실 딕셔너리.

    실제 모델도 같은 블록을 읽으므로 Fake 전용 우회로가 아니다. `ids`는 노드가 미리
    발급한 식별자다. Scout·Auditor는 그 값을 그대로 출력에 넣으라고 지시받고, 안 넣어도 노드가
    저장 전에 발급값으로 덮는다(`RunContext.stamp_issued`, 이슈 #279) — 단 `missing_id`는
    `missing_evidence`가 비어 있지 않을 때만 그 하나로 정규화하고 비어 있으면 그대로 둔다.
    Critic은 ID를 출력하지 않고 `DecisionDraft.to_decision()`이 발급값으로 조립한다.

    `slice_criterion`은 준 경우에만 키가 생긴다(`FACT_KEYS` 주석) — `None`을 `null`로 싣지
    않는다. 실으면 슬라이스 없는 프롬프트까지 글자가 바뀐다.

    `fuzz_entrypoint`도 같은 opt-in이다 — **PoV만** 준다. 단 PoV는 **못 찾았을 때도 준다**(빈
    `source` + `reason`): 키가 아예 없으면 "안 찾았다"와 "찾았는데 없다"가 구별되지 않는다
    (CLAUDE.md §0 — 안 잰 값은 0으로 두고 사유를 남긴다). 그 dict를 만드는 곳은
    `runner._fuzz_entrypoint`다.
    """
    return {
        "run_id": run_id,
        "ids": dict(ids),
        "artifact_id": artifact_id,
        "target": target.model_dump(mode="json") if target else None,
        # **종류 이름만이 아니라 본문까지 싣는다** (W1). body.md가 "아래 JSON이 참조
        # 가능한 사실 전부다"라고 못박으므로, 여기 없는 것은 모델이 무시해도 되는 값이
        # 된다 — `{{SOURCE}}`가 산문과 여기 양쪽에 실리는 것과 같은 이유다.
        "capsule_sections": [
            {
                "kind": section.kind,
                "tokens": section.tokens,
                "items": section.items,
                "lines": list(section.body),
            }
            for section in capsule.sections
        ]
        if capsule
        else [],
        "source": source,
        # `evidence_bodies`가 주어진 evidence만 본문(body·truncated)을 함께 싣는다. 안 주면
        # (Scout, 그리고 Auditor·Critic 중 노드의 `_CONTENT_AWARE_VERSIONS`에 없는 버전)
        # 종전과 바이트 동일하게 id·kind·ok만 나간다 — opt-in.
        "evidence": [
            {
                "evidence_id": ev.evidence_id,
                "kind": ev.kind.value,
                "ok": ev.ok,
                **(
                    {"body": body["body"], "truncated": body["truncated"]}
                    if (body := (evidence_bodies or {}).get(ev.evidence_id)) is not None
                    else {}
                ),
            }
            for ev in evidences
        ],
        "callers": list(callers),
        **({"slice_criterion": dict(slice_criterion)} if slice_criterion is not None else {}),
        **({"fuzz_entrypoint": dict(fuzz_entrypoint)} if fuzz_entrypoint is not None else {}),
    }


def _validated_facts(facts: Mapping[str, object]) -> dict[str, object]:
    extra = sorted(set(facts) - set(FACT_KEYS))
    if extra:
        raise PromptError(
            f"사실 블록에 계약 밖 키가 있다: {extra}. 허용: {list(FACT_KEYS)}. "
            "키를 늘리려면 어댑터의 FakeContext와 함께 고친다"
        )
    return dict(facts)


def _read(path: Path) -> str:
    if not path.is_file():
        raise PromptError(f"프롬프트 파일이 없다: {path}")
    return path.read_text(encoding="utf-8")


def _read_meta(path: Path) -> dict[str, object]:
    data = yaml.safe_load(_read(path))
    if not isinstance(data, dict):
        raise PromptError(f"{path}의 최상위는 매핑이어야 한다")
    return data
