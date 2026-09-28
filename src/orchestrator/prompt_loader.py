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

    return PromptTemplate(
        role=role,
        version=version,
        prefix=prefix,
        body=body,
        output_schema=output_schema,
        input=input_kind,
    )


# Critic 근거 본문 절단(#237 HIGH-1). 결정론적 truncation — 모델 개입 없음, 재현 가능.
EVIDENCE_BODY_CHAR_CAP = 800  # evidence 1건당 상한
EVIDENCE_BODY_TOTAL_CAP = 4000  # 전 evidence 합 상한(프롬프트 토큰 예산)


_TRUNCATION_MARKER = "\n...\n"


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
        body, truncated = _bound_text(raw, min(per_cap, remaining))
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
) -> dict[str, object]:
    """마지막 ```json 펜스에 들어갈 사실 딕셔너리.

    실제 모델도 같은 블록을 읽으므로 Fake 전용 우회로가 아니다. `ids`는 노드가 미리
    발급한 식별자이며 모델은 그 값을 그대로 출력에 넣는다.

    `slice_criterion`은 준 경우에만 키가 생긴다(`FACT_KEYS` 주석) — `None`을 `null`로 싣지
    않는다. 실으면 슬라이스 없는 프롬프트까지 글자가 바뀐다.
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
