"""서빙 전 디코더 스키마 컴파일 게이트 (#698).

모든 모델 요청은 `response_format: json_schema, strict: True`로 `schema.model_json_schema()`를
보낸다(`adapters/openai_compat`). 서빙 쪽 vLLM의 xgrammar 백엔드가 그 스키마를 문법으로 못 바꾸면
요청이 HTTP 400으로 거절되고, run은 몇 시간 뒤에야 그것을 안다. 스키마가 판별 합집합(`anyOf` ·
`const`)과 Auditor·PoV의 `tool` enum 축소(`signals.without_tools`)로 복잡해지므로, **모델을 부르지
않고** 한 run이 보낼 수 있는 스키마를 전부 문법으로 바꿔 본다.

## 무엇을 흉내 내나

vLLM 0.27.1 `v1/structured_output/backend_xgrammar.py`의 `validate_xgrammar_grammar`가 요청마다 하는
두 단계 — ① `has_xgrammar_unsupported_json_features`(아래에 그대로 옮겼다) ② 인자 없는
`xgr.Grammar.from_json_schema(schema)`(= `strict_mode=True`, `any_whitespace=True`) — 가 거절하면
`ValueError`가 400이 된다. 이 둘을 같은 순서로 돈다. **차이:** 그 뒤 서버는 실제 토크나이저로
`GrammarCompiler.compile_json_schema(any_whitespace=...)`를 한 번 더 한다. 여기서는 토크나이저가
없으므로 256개 단일 바이트 합성 어휘로 같은 호출을 한다 — 문법 변환·재귀 한도 오류는 같은 경로지만
**진짜 어휘의 토큰 마스크 컴파일 시간·메모리는 안 잰다.**

## 쓰는 법

    python tools/check_decoder_schemas.py [프로필]     # xgrammar가 깔린 파이썬으로 (진입점)

xgrammar는 **늦게 import한다** — 열거(`enumerate_schemas`)는 xgrammar 없이 돈다. 하나라도 FAIL이면
종료 코드 1이다. 이 게이트가 초록이어도 **모델이 그 문법으로 올바른 JSON을 낸다는 뜻은 아니다**
— 변환이 되는지만 본다(CLAUDE.md §0: 안 잰 것을 잰 것처럼 쓰지 않는다).
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from src.bench import ROLE_SPEC
from src.bench_report import schema_for
from src.config import PROMPTED_ROLES, HarnessConfig
from src.orchestrator.nodes import scout
from src.orchestrator.nodes.signals import COMPILE_NOTE_TOOLS, without_tools
from src.orchestrator.prompt_loader import load_prompt
from src.schemas.enums import Role

# `signals.withheld_tools`가 낼 수 있는 집합 전부 — build 오류·clang-tidy 오류 유무의 네 조합.
WITHHELD_SETS: tuple[tuple[str, ...], ...] = (
    (),
    COMPILE_NOTE_TOOLS,
    ("clang-tidy",),
    (*COMPILE_NOTE_TOOLS, "clang-tidy"),
)
# 요청 스키마의 `tool` enum을 줄이는 역할(`nodes/auditor.py`·`nodes/pov.py`).
_NARROWED_ROLES = ("auditor", "pov")

# vLLM 0.27.1 `STRING_SUPPORTED_FORMATS`와 같다(xgrammar json_schema_converter.cc 기준).
_STRING_FORMATS = frozenset(
    {
        "email", "date", "time", "date-time", "duration", "ipv4", "ipv6", "hostname", "uuid",
        "uri", "uri-reference", "uri-template", "json-pointer", "relative-json-pointer",
    }
)  # fmt: skip


@dataclass(frozen=True)
class DecoderSchema:
    role: str
    version: str
    schema: type[BaseModel]
    withheld: tuple[str, ...]


def enumerate_schemas(config: HarnessConfig) -> list[DecoderSchema]:
    """이 프로필의 한 run이 보낼 수 있는 요청 스키마 전부 — xgrammar가 필요 없다."""
    found: list[DecoderSchema] = []
    for role in PROMPTED_ROLES:
        if role not in config.profile.roles:
            continue
        version = config.model_for(role).prompt_template_version
        enum = Role(role)
        template = load_prompt(enum, version)
        schema = schema_for(enum, template, ROLE_SPEC[enum][0])
        variants = [schema]
        if enum in scout.SCOUT_ROLES:
            variants.append(scout.ScoutResponse)  # 되묻기 홉은 선언과 무관하게 이 래퍼를 보낸다
        for variant in dict.fromkeys(variants):
            narrowed = role in _NARROWED_ROLES and "tool" in variant.model_fields
            sets = WITHHELD_SETS if narrowed else ((),)
            found.extend(
                DecoderSchema(role, version, without_tools(variant, w), w) for w in sets
            )
    return found


def has_unsupported_features(schema: dict[str, Any]) -> bool:
    """vLLM 0.27.1 `has_xgrammar_unsupported_json_features`를 그대로 옮긴 것."""

    def check(obj: Any) -> bool:
        if not isinstance(obj, dict):
            return False
        kind = obj.get("type")
        if kind in ("integer", "number") and "multipleOf" in obj:
            return True
        if kind == "array" and any(
            k in obj for k in ("uniqueItems", "contains", "minContains", "maxContains")
        ):
            return True
        if kind == "string" and "format" in obj and obj["format"] not in _STRING_FORMATS:
            return True
        if kind == "object" and any(k in obj for k in ("patternProperties", "propertyNames")):
            return True
        for value in obj.values():
            if isinstance(value, dict) and check(value):
                return True
            if isinstance(value, list) and any(check(item) for item in value):
                return True
        return False

    return check(schema)


def compile_error(schema: type[BaseModel], xgr: Any, compiler: Any) -> str | None:
    """변환·컴파일이 되면 `None`, 아니면 사유. `xgr`는 호출부가 늦게 import한 모듈이다."""
    json_schema = schema.model_json_schema()
    if has_unsupported_features(json_schema):
        return "xgrammar가 모르는 JSON Schema 기능 (vLLM이 요청 단계에서 거절한다)"
    try:
        xgr.Grammar.from_json_schema(json_schema)  # vLLM validate_xgrammar_grammar와 같은 호출
        compiler.compile_json_schema(json.dumps(json_schema), any_whitespace=True)
    except Exception as exc:  # noqa: BLE001 - 어떤 변환 예외든 사유로 보고한다
        return f"{type(exc).__name__}: {exc}".replace("\n", " ")[:300]
    return None


def make_compiler(xgr: Any) -> Any:
    """토크나이저 없이 컴파일러를 세운다 — 256 단일 바이트 합성 어휘 + 종료 토큰."""
    vocab = [chr(i) for i in range(256)] + ["<eos>"]
    info = xgr.TokenizerInfo(vocab, stop_token_ids=[256])
    return xgr.GrammarCompiler(info, cache_enabled=False)


def xgrammar_version() -> str:
    """깔린 xgrammar 배포판 버전 — 게이트 결과를 어느 판으로 냈는지 적는다(`__version__`이 없다)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("xgrammar")
    except PackageNotFoundError:
        return "unknown"


def grammar_accepts(schema: type[BaseModel], text: str, xgr: Any, compiler: Any) -> bool:
    """그 스키마의 문법이 `text` 전체를 완결된 출력으로 받는가 — 집행을 재는 검사용(#602).

    `accept_string`은 접두로 받을 수 있으면 참이라, 끝까지 받은 뒤 `is_completed`로 완결을 본다.
    합성 어휘라 **서버의 실제 집행이 아니라 같은 문법의 판정**이다.
    """
    compiled = compiler.compile_json_schema(
        json.dumps(schema.model_json_schema()), any_whitespace=True
    )
    matcher = xgr.GrammarMatcher(compiled)
    return bool(matcher.accept_string(text)) and matcher.is_completed()


def check_profile(config: HarnessConfig) -> list[tuple[DecoderSchema, str | None]]:
    import xgrammar as xgr  # 늦은 import — 없으면 ImportError를 호출부가 본다

    compiler = make_compiler(xgr)
    return [(item, compile_error(item.schema, xgr, compiler)) for item in enumerate_schemas(config)]


def format_line(item: DecoderSchema, error: str | None) -> str:
    withheld = ",".join(item.withheld) or "-"
    status = "OK" if error is None else f"FAIL {error}"
    name = f"{item.role:<16} {item.version:<4} {item.schema.__name__:<38}"
    return f"{name} withheld={withheld:<22} {status}"
