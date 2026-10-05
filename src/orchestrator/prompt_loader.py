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
호출자 창이 뒤에 붙고, entry 함수 뒤에 sink 앞 가드 목록이 붙고, 상한이 토큰이 되고, entry 함수가
줄어든다) 프롬프트가 옛 모양을 말하면 모델이 받는 서술이 거짓이 된다. 켜진 옵션이 이 목록에 없으면
`cli`가 run을 막는다(#351 리뷰 MEDIUM-2). **모르는 키는 거절한다** — 오타 하나가 그 가드를 조용히
끈다(`input`과 같은 판단).

## meta의 `tools` — 그 프롬프트 판이 모델에게 광고하는 도구

`PROMPT_TOOL_NAMES` 중 그 프롬프트의 prefix가 「쓸 수 있는 도구」로 서술하는 이름의 목록이다
(없으면 빈 목록 — 도구를 안 쓰는 한 방 프롬프트). **스키마가 허용해도 프롬프트가 광고하지
않으면 모델은 그 도구를 영영 안 쓴다** — PR #327이 `gdb`를 `TOOL_KINDS`에 넣었는데 pov v2
prefix의 도구 목록은 `asan`·`build`·`poc` 셋 그대로였고, v3가 그 줄을 다시 쓸 때까지 모델이
`gdb`를 부를 길이 없었다(`prompts/pov/v3/meta.yaml`의 「v2와 다른 여섯」 ①).

**버전 이름으로 분기하지 않는 이유.** 노드마다 있는 `_CONTENT_AWARE_VERSIONS`가 이름 목록이라
판이 하나 늘 때마다 그 집합을 고쳐야 하고, 잊으면 새 판에서 그 기능이 조용히 꺼진다. 선언이
프롬프트 쪽에 있으면 v6→v7→v8이 코드 변경 없이 돈다. **모르는 이름은 거절한다** —
`input`·`source_options`와 같은 판단이고, 오타 하나가 그 가드를 조용히 끈다.

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
# **opt-in은 둘이다**(⏵ 2026-10-02 — 전 판은 *"`slice_criterion`만"*이라 적었다):
#   - `slice_criterion` — 슬라이스 타깃의 Scout 프롬프트에만(ADR 0012 결정 1, v3 prefix의
#     「Slice criterion」 절).
#   - `fuzz_entrypoint` — **PoV만**(아래 그 항목의 주석).
# 나머지 여덟은 늘 실린다. 안 주는 역할의 프롬프트는 그 키가 생기기 전과 **글자가 같다** — 그래서
# 이 튜플은 「허용」 목록이지 「늘 있는」 목록이 아니다.
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

# meta.yaml의 `tools`가 가질 수 있는 값(모듈 docstring 「meta의 `tools`」).
# **`adapters/fake_tools.TOOL_KINDS`의 키와 같은 집합을 여기 복제한다** — 그쪽은 어댑터라
# import 할 수 없다(engineering-guide.md §3 · CI의
# `test_orchestrator_source_has_no_adapter_import`).
# `nodes/auditor.CALLERS_SECTION` ↔ `index_graph.GRAPH_SECTION_KINDS`와 같은 이중화다.
# 갈리면 조용히 어긋난다 — 저쪽에만 있는 도구는 프롬프트가 광고할 길이 없고(위 docstring의
# pov v2 ↔ gdb), 여기에만 있는 이름은 로더가 통과시킨 뒤 실행 시점에 `tool_policy 밖 도구`로
# 떨어진다. 그래서 두 값의 대조는 테스트가 든다 — `tests/test_auditor_loop.py`의
# `test_prompt_tool_names_mirror_the_tool_whitelist`가 `TOOL_KINDS`와, 스키마
# Literal(`AuditorAction.tool`) 은 `TOOL_KINDS`에서 `index`를 뺀 집합과 대조한다.
# ⚠ **로더는 이 집합 전체를 통과시키지만 Auditor 좌석이 받는 것은 `index`를 뺀 여섯이다** —
# `schemas/auditor.AUDITOR_TOOLS`. 그 어긋남(`tools: [index]`로 켜면 광고하는 도구를 문법이
# 거절한다)은 로더가 아니라 `nodes/auditor.loop_problem`(cli run·doctor와 노드)이 막는다 — 로더는
# 역할을 모른다.
PROMPT_TOOL_NAMES = ("semgrep", "clang-tidy", "build", "asan", "gdb", "poc", "index")


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
    # 이 프롬프트 판이 모델에게 광고하는 도구(모듈 docstring 「meta의 `tools`」).
    # `PROMPT_TOOL_NAMES`의 부분집합이고, 비면 도구를 안 쓰는 판이다.
    tools: tuple[str, ...] = ()

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

    tools = meta.get("tools", [])
    if not isinstance(tools, list) or not all(t in PROMPT_TOOL_NAMES for t in tools):
        raise PromptError(
            f"{directory}/meta.yaml의 tools {tools!r}에 모르는 값이 있다 — "
            f"{list(PROMPT_TOOL_NAMES)} 중에서 고른다"
        )

    return PromptTemplate(
        role=role,
        version=version,
        prefix=prefix,
        body=body,
        output_schema=output_schema,
        input=input_kind,
        source_options=tuple(source_options),
        tools=tuple(tools),
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

# 구조화 출력의 **잡음 키** — `evidence_summaries`가 머리줄과 같은 자리에서, 같은 이유로
# 절단 **전에** 뺀다(이슈 #481). 절단은 head 2/3 + tail 1/3이므로 **앞에 오는 큰 키가 head를
# 먹으면 뒤에 오는 결과가 통째로 잘린다** — semgrep `--json`의 최상위 키는 알파벳 순이라
# `errors`(파싱 실패 목록)가 `results`(히트 — `check_id`·줄번호)보다 **먼저** 온다.
#
# 🔴 **취향이 아니라 실측으로 고른 목록이고 지금 하나다.** 2026-10-04 운영 run
# (`run_20261003_0003`의 `semgrep/ev_0002.log`, stdout 60,113자 · 물리 줄 1개)에서
# `errors`가 **42,401자(70.5%)**를 먹고 `results`는 **42,508자 지점**에서 시작했다 — head가
# 530자이니 `check_id`가 **0건** 보였다. 나머지 최상위 키는 `paths` 51자 ·
# `interfile_languages_used` 2자 · `skipped_rules` 2자 · `version` 8자로 head를 먹지 않아
# 넣지 않았다. 목록을 취향으로 늘리면 **도구가 낸 결과를 뺄 수 있다** — 이 도구에서 `errors`는
# 파싱 실패지만 다른 도구에서 그것이 결과일 수 있고, 그 경우는 **미실측**이다.
#
# ⚠ **그 51자는 단일 파일 run 한 건의 값이지 일반 서술이 아니다.** `paths.scanned`는 스캔한
# 파일 수만큼 자라고 알파벳 순으로 `results`보다 앞이다 — 픽스처 `_REAL_STDOUT`의 `scanned`를
# 27자 경로 40개(원소당 직렬화 폭 31자)로 늘려 돌리면 `errors`를 뺀 뒤에도 `results`가
# 1,330자 지점에서 시작해 head 530자 밖이다(실행한 값). 530은 증거당 800자 경로의 head이고
# Auditor `probe`(`_PROBE_BODY_CHARS` 2,000자 — head 1,330)는 문턱이 다르다 — 같은 27자
# 경로로 규칙 이름이 head에 보이는 최대 파일 수가 800자 경로에서 10개, probe에서 35개다(실행한
# 값). **운영 규모 디렉터리 run에서 `paths`가 head를 얼마나 먹는지는 미실측이다** — 디렉터리
# 대상 실행의 실측은 2파일 디렉터리 한 건뿐이다(`docs/open-questions/D/` 아래 `.inc` 건너뜀
# 항목 — 그 건은 `paths.scanned`에 `target.c`만 들었다). 현재 호출부는 파일 경로만 넘긴다(코드
# 읽기 — `preprocess.run`이 `is_file()`인 것만 artifact로 만들고 `runner._artifact_signals`·
# `auditor._probe`가 그 경로를 넘긴다) — **그 불변식은 코드가 지키지 않는다**(같은 항목의
# 경고): 호출이 배치 스캔으로 바뀌면 이 서술이 조용히 낡는다. 디렉터리를 대상으로 도는 경로가
# 생기면 `paths`를 이 목록에 넣을지 그때 잰다 — 판정(`judge_semgrep`)은 원본 stdout을 읽으니
# 이 목록의 영향을 받지 않는다.
#
# ⚠ **뺀 뒤에도 절단이지 요약이 아니다.** 같은 로그에서 `errors`를 빼면 `results`가 stdout 안
# 130자 지점에서 시작한다 — **계산값**이다: `{"errors": ` 11자 + 자리표시자 23자 + errors와
# results 사이 96자(= 42,508 − 42,401 − 11). 실 로그가 저장소에 없어 그 로그로는 다시 못 쟀고,
# 같은 구조의 픽스처를 이 함수로 재면 128이다(자리표시자 없이 값만 지우면 11 + 96 = 107).
# 픽스처의 본문(머리줄 합쳐)에서 첫 히트의 `check_id`는 189자, 규칙 이름의 끝은 254자,
# 줄번호 1087은 284자라 모두 head 530자 안이다 — **10건 중 1건**이고 나머지 아홉은 여전히
# 안 보인다 — **이 범용 제거 경로의 한계**다. semgrep Evidence는 별도 함수
# `_fold_semgrep_results`로 히트당 한 줄에 접는다. 이 함수의 JSON 계약은 그대로다.
#
# ⚠ **뺀 사실을 숨기지 않는다**(§0) — 자리에 뺀 값의 글자 수를 남긴다. 그 수는 **재직렬화
# 길이**(`json.dumps` 기본 구분자·`ensure_ascii=True`)이지 원문 글자 수가 아니다 — 구분자가
# 다른 원문이나 비ASCII를 담은 원문에서는 다르다. `truncated` 플래그는 절단을
# 말하지 이 제거를 말하지 않고, semgrep이 파싱에 실패했다는 사실 자체가 「10건 찾았다」의
# 신뢰도에 걸리는 정보다. 로그 파일과 `digest`는 그대로다 — 모델로 가는 사본에서만 뺀다.
_JSON_NOISE_KEYS = ("errors",)


def _drop_json_noise(text: str) -> str:
    """최상위 JSON 객체인 줄에서 `_JSON_NOISE_KEYS`의 값을 자리표시자로 바꿔 돌려준다.

    **뺄 것이 없으면 입력을 그대로 돌려준다** — 재직렬화는 실제로 뺄 때만 돈다. 그래야 빈
    `"errors": []`를 내는 run의 모델 입력 바이트가 이유 없이 바뀌지 않는다.

    도구 이름을 보지 않는다. 보는 것은 ① 줄이 `{`로 시작하는가(semgrep `--json`은 한 물리 줄)
    ② 그것이 JSON 객체로 읽히는가 ③ 그 **최상위**에 잡음 키가 비어 있지 않게 있는가다.
    셋 중 하나라도 아니면 그 줄은 손대지 않는다 — clang-tidy·build·ASan 로그가 그 경로다.

    ⚠ 뺄 때는 그 줄을 **다시 직렬화**하므로 공백·구분자가 `json.dumps` 기본값으로 정규화된다.
    실측(그 run의 로그)에서는 왕복이 **바이트 동일**이었다(semgrep이 `", "`·`": "` 구분자를
    쓴다) — 다른 도구의 포맷은 미실측이다. 정규화는 구분자만이 아니다 — 줄의 **나머지 값도**
    바뀐다: 비ASCII는 `\\uXXXX`로(`ensure_ascii` 기본값), 숫자는 `1.10`→`1.1`, 범위를 넘는
    `1e400`→`Infinity`(엄밀한 JSON이 아니다). `ensure_ascii`를 바꾸면 모델 입력 바이트가
    달라져 안 바꿨다. 자리표시자의 `<N chars dropped>`도 같은 재직렬화 길이다 — 원문 글자
    수가 아니라서 compact 구분자 원문에서는 원문보다 크게 나온다.

    ⚠ **읽기 상한이 줄 중간에서 자르면 이 제거는 꺼진다.** 로그 리더의 상한(어댑터의
    `container_tools.PAYLOAD_READ_CAP` 200,000자 — 이 모듈은 어댑터를 import할 수 없어 값으로
    적는다. 같은 값인지는 `tests/test_evidence_json_noise.py`가 건다)이 JSON 줄을 자르면
    `json.loads`가 실패해 아래 `except`로 가고, 그 줄은 종전처럼 `errors`가 head를 먹는다.
    이 경우만 가르는 표시는 없다 — `truncated`는 정상 경로에서도 True다. 문턱은 stdout 줄
    길이 + 그 앞 머리가 200,000자를 넘을 때다. 실측 로그 한 건(스팬 148개 · `errors` 42,401자
    · stdout 중 그 밖 17,712자 = 60,113 − 42,401 · 로그 중 stdout 밖 851자 = 60,964 − 60,113)에서
    `errors` 전부가 스팬에 비례한다고 놓고 스팬당 약 286.5자로 환산하면 스팬 **약 630개
    초과**다 — **계산값이고 실측이 아니다**(그 밖이 클수록 문턱은 내려간다).

    **고치는 길은 둘이고 닿는 구간이 다르다.** 이 함수에서 최상위 `"errors": [`의 괄호 짝을
    스캔해 자르는 수정은 `errors`의 끝이 읽기 상한 안에 있을 때만 돕는다 — 줄이 잘리기 시작하는
    문턱(스팬 약 633~636)에서 `results`의 시작이 상한 앞에 드는 한계(약 695~698)까지, 약 60스팬
    폭이다(같은 환산의 **계산값**). 그 위로는 `errors`가 상한을 넘어 이어져 `results`의 글자가
    아예 안 읽히므로 파싱으로 되살릴 것이 없다 — 어댑터가 읽기 상한 **앞에서** `errors`를
    빼야 닿는다(상한을 올리면 문턱이 옮겨질 뿐 한계는 남는다). 둘 다 아직 안 했다.
    """
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("{"):
            continue
        if not any(f'"{key}"' in line for key in _JSON_NOISE_KEYS):
            continue
        try:
            body = json.loads(line)
        except (ValueError, RecursionError):
            # 못 읽으면 손대지 않는다 — 지어내지 않고, 자르는 쪽이 종전 그대로 돈다. 읽기 상한이
            # 중간에서 자른 줄도 여기로 온다(위 docstring의 ⚠ — 그때 이 제거는 꺼진다).
            continue
        # `isinstance(body, dict)`를 따로 보지 않는다 — `{`로 시작하는 JSON이 읽히면 객체다.
        # (오염 검증에서 그 분기가 **닿지 않는 코드**로 드러났다: 배열은 위 `startswith`가 건다.)
        dropped = False
        for key in _JSON_NOISE_KEYS:
            value = body.get(key)
            if not value:  # 없거나 빈 것(`[]`·`{}`·`""`)은 head를 먹지 않는다
                continue
            body[key] = f"<{len(json.dumps(value))} chars dropped>"
            dropped = True
        if dropped:
            lines[index] = json.dumps(body)
    return "\n".join(lines)


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


def _fold_semgrep_results(text: str, cap: int) -> tuple[str, bool] | None:
    """semgrep 결과를 히트당 한 줄로 접는다 — 이슈 #481의 선택지 1.

    호출부가 Evidence.source로 도구를 가른다. 범용 `_drop_json_noise`와 독립이며 원본 stdout과
    판정은 바꾸지 않는다. 실 로그 유래 `_REAL_STDOUT`의 히트 반복판으로 표시 건수를 잰다.
    실서빙 모델의 인용 개선은 0회 관측(이 함수의 검증은 본문 가시성뿐이다).

    규칙·경로·시작/끝 줄은 온전히 남기고 메시지는 최소 16자(생략 표시 포함)를 예약한다.
    그 조건으로 원래 순서의 최대 접두 건수를 계산한 뒤 남은 예산을 메시지에 나눠 쓴다.
    stdout 밖 실행 기록은 앞 80자·뒤 20자까지 예약한다(설계 상수, 작은 cap이면 더 줄인다).
    JSON의 나머지 필드는 생략 표시하며 파싱 실패 목록은 건수와 재직렬화 길이를 남긴다.
    깨진 JSON·필수 필드가 없는 결과는 None으로 돌려 종전 절단 경로에 맡긴다.
    """
    lines = text.split("\n")
    # 하네스 로그에서는 stdout만 읽는다 — stderr의 JSON을 히트로 오인하지 않는다.
    start = lines.index("--- stdout ---") + 1 if "--- stdout ---" in lines else 0
    end = lines.index("--- stderr ---") if "--- stderr ---" in lines else len(lines)
    try:
        document = json.loads("\n".join(lines[start:end]))
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("results"), list):
        return None
    hits = document["results"]
    identities: list[str] = []
    messages: list[str] = []
    for hit in hits:
        try:
            path, rule = hit["path"], hit["check_id"]
            first, last = hit["start"]["line"], hit["end"]["line"]
            message = hit["extra"]["message"]
        except (KeyError, TypeError):
            return None
        if (
            not all(isinstance(value, str) for value in (path, rule, message))
            or not all(type(value) is int for value in (first, last))
        ):
            return None
        # 개행은 공백으로 접어 한 히트가 물리 줄 하나를 쓰도록 한다.
        identities.append(f"{' '.join(path.split())}:{first}-{last} {' '.join(rule.split())}: ")
        messages.append(" ".join(message.split()))
    errors = document.get("errors", [])
    if not isinstance(errors, list):
        return None
    notice = (
        f"errors: {len(errors)} ({len(json.dumps(errors))} chars dropped); "
        "other JSON fields omitted"
    )
    context, _ = _bound_text("\n".join(lines[:start]), min(80, cap // 5))
    tail, _ = _bound_text("\n".join(lines[end:]), min(20, cap // 20))
    suffix = f"\n{tail}" if tail else ""
    prefix = f"{context}\n{notice}\n" if context else f"{notice}\n"
    selected: list[str] = []
    for identity, message in zip(identities, messages, strict=True):
        preview = message if len(message) <= 16 else message[:13] + "..."
        candidate = selected + [identity + preview]
        count = f"{len(candidate)} of {len(hits)} shown"
        if len(prefix + count + "\n" + "\n".join(candidate) + suffix) > cap:
            break
        selected = candidate
    count = f"{len(selected)} of {len(hits)} shown"
    # 아주 작은 잔여 예산에서도 표시 건수를 먼저 둔다. 잘린 표시도 truncated로 알린다.
    if not selected:
        if len(count) >= cap:
            return count[:cap], True
        rest, _ = _bound_text(prefix + suffix, cap - len(count) - 1)
        return f"{count}\n{rest}".strip(), True
    spare = cap - len(prefix + count + "\n" + "\n".join(selected) + suffix)
    for index in range(len(selected)):
        old_width = len(selected[index]) - len(identities[index])
        width = old_width + spare // (len(selected) - index)
        message = messages[index]
        preview = message if len(message) <= width else message[: width - 3] + "..."
        selected[index] = identities[index] + preview
        spare -= len(preview) - old_width
    return prefix + count + "\n" + "\n".join(selected) + suffix, True


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
    주석). 로그 파일은 그대로다. semgrep은 `_fold_semgrep_results`로 먼저 접고, 그 밖의 도구나
    파싱 실패는 범용 잡음 제거·절단 경로로 간다.

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
        trimmed = _HARNESS_HEADER_LINE.sub("", raw)
        cap = min(per_cap, remaining)
        folded = (
            _fold_semgrep_results(trimmed, cap)
            if getattr(ev, "source", None) == "semgrep"
            else None
        )
        body, truncated = folded or _bound_text(_drop_json_noise(trimmed), cap)
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
    evidence_locations: bool = False,
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

    `evidence_locations`도 같은 opt-in이다 — 노드가 content-aware **판**일 때만 켠다. 켜면 각
    evidence에 `location`(없으면 `null`)이 실리고, 끄면 키가 없다(종전과 바이트 동일).

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
        # `evidence_bodies`가 주어진 evidence만 본문(body·truncated)을 싣는다. 안 주면
        # (Scout, 그리고 Auditor·Critic 중 노드의 `_CONTENT_AWARE_VERSIONS`에 없는 버전)
        # 종전과 바이트 동일하게 id·kind·ok만 나간다 — opt-in.
        #
        # **`location`은 본문과 따로 `evidence_locations`가 정한다** — 노드가 **판**(content-aware
        # 여부)으로 켠다. payload가 없어 본문이 안 실리는 Evidence에도 같은 판이면 키는 나간다
        # (`evidence_summaries`가 그 Evidence를 맵에서 뺀다) — 본문 유무에 묶으면 그때 키가 사라져
        # 같은 판의 Critic 줄(`critic._describe_evidence`)과 다른 값을 든다(적대적 리뷰 #480 LOW-1).
        # 위치가 없는 Evidence는 `null`로 싣는다 — 위 `target`과 같은 관례다. 키를 아예 빼면
        # 「도구가 위치를 안 줬다」와 「이 판은 위치를 안 싣는다」가 구별되지 않는다(CLAUDE.md §0).
        #
        # **위치는 판단 보조이고 등급 근거가 아니다.** 게이트 `p1 = static_ok or location_ok`에서
        # Evidence에 위치를 싣는 곳이 semgrep(STATIC) 하나라 인용하면 `static_ok`가 이미 참이다
        # (`container_tools.first_hit_location` · `gate.evaluate`). 그 위치는 semgrep
        # `results[0]`(첫 히트) 하나라 Finding이 주장한 줄과 다를 수 있다.
        #
        # **관측 —** 2026-10-04 첫 실모델·실도구 운영 run에서 finding 3건이 전부 `evidence_refs=[]`
        # (그래서 E0 -> 6단계 queue -> Critic 0회 -> PoV 0건 -> `oracle_final reason=no_poc`)였고,
        # semgrep 근거는 프롬프트에 본문까지 실려 있었는데 하네스가 아는 위치
        # `apprentice.c:1086-1087`은 FACTS에 안 나갔다(이슈 #475). 같은 run의 세 Finding은
        # `trigger_status=no_signal` 부정 결론(확신 0.05~0.10)이었고, semgrep 본문은
        # `truncated=true`로 절단돼 히트 10건의 줄이 안 보였다(#481) — 그 보고서(`docs/experiments/
        # harness/2026-10-04-cybergym-operational-run-vastai-remote.md`)는 이 인용 거절을 타당했다고
        # 적는다. 위치 미노출 외에 이 둘이 인용 부재의 후보다. **위치를 안 본 것이 인용을 막았다는
        # 것은 가설이다** — 커밋된 `docs/experiments/` 보고서 기준 위치를 싣고 돈 run이 0건이라
        # 인용이 늘지 어떨지는 미실측이다.
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
                **(
                    {"location": ev.location.model_dump(mode="json") if ev.location else None}
                    if evidence_locations
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
