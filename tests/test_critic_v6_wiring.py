"""Critic v6의 실제 배선과 공유 SOURCE 계약 — 모델 판정 품질은 재지 않는다."""

from pathlib import Path

import pytest
from conftest import ALL_PROFILES
from test_nodes import (
    VULN_TARGET,
    _critic_prompt_at,
    _facts_of,
    _finding,
    _triage,
)
from test_nodes import (
    ctx as ctx,
)

from src.config import load_config
from src.orchestrator import runner
from src.orchestrator.nodes import critic, pov
from src.orchestrator.stages import Stage
from src.schemas import Role


def _v6(context):
    key = context.config.model_key_for("critic")
    entry = context.config.models[key].transition(prompt_template_version="v6")
    context.config = context.config.transition(
        models={**context.config.models, "fake_critic_v6": entry},
        profile=context.config.profile.transition(
            roles={**context.config.profile.roles, "critic": "fake_critic_v6"}
        ),
    )


def test_served_critic_is_wired_to_v6():
    assert load_config("harness_pool").model_for("critic").prompt_template_version == "v6"
    assert load_config("harness_pool_hybrid").model_for("critic").prompt_template_version == "v6"


def test_wired_served_critic_versions_are_content_aware():
    """배선된 실모델 판을 디스크·게이트와 묶는다. dev의 v1은 metadata 계약이다."""
    entries = [load_config(profile).model_for("critic") for profile in ALL_PROFILES]
    versions = {
        entry.prompt_template_version for entry in entries
        if not entry.endpoint.startswith("fake://")
    }
    assert versions <= {p.name for p in Path("prompts/critic").iterdir() if p.is_dir()}
    assert versions <= critic._CONTENT_AWARE_VERSIONS
    assert "v6" in critic._CONTENT_AWARE_VERSIONS


def test_v6_carries_evidence_body_and_location(ctx):
    """핀 출처: test_nodes의 실제 fake semgrep Evidence와 Critic 호출 경로."""
    prompt, own = _critic_prompt_at(ctx, "v6", "SANITIZER heap-buffer-overflow at parse_header")
    facts = {ev["evidence_id"]: ev for ev in _facts_of(prompt.body)["evidence"]}
    located = [ev for ev in own if ev.location is not None]
    assert located
    for ev in located:
        assert facts[ev.evidence_id]["location"] == ev.location.model_dump(mode="json")
        assert critic._location_text(ev.location) in prompt.body
        assert "heap-buffer-overflow" in facts[ev.evidence_id]["body"]
    assert "    SANITIZER heap-buffer-overflow" in prompt.body


def test_v6_source_survives_critic_then_pov(ctx):
    """같은 캐시를 Auditor→Critic→PoV가 읽어도 조회는 한 번이고 문자열은 같다."""
    _v6(ctx)
    _, capsule, _ = _triage(ctx, VULN_TARGET)
    calls = []

    def read():
        calls.append("read")
        return "/* parser.c:3-6 */\nvoid parse_header(char *p) { memcpy(p, p, 64); }"

    source = runner._Once(read)
    auditor_text = source()
    finding = _finding("finding_0001")
    critic.run(ctx, finding=finding, source=source)
    critic_facts = _facts_of(ctx.models.last_for(Role.CRITIC).body)
    assert critic_facts["source"] == auditor_text != ""
    prompt = pov._render_prompt(ctx, finding=finding, capsule=capsule, source=source)
    assert _facts_of(prompt.body)["source"] == auditor_text
    assert calls == ["read"]


def test_runner_passes_finding_source_to_critic(ctx, monkeypatch):
    """노드만 소스를 받는 척하고 러너 배선을 빼는 변이를 잡는다."""
    _v6(ctx)
    finding = _finding("finding_0001")
    state = runner._Accumulator()
    state.findings[finding.finding_id] = finding
    source = runner._Once(lambda: "int source_from_auditor;")
    state.finding_sources[finding.finding_id] = source
    monkeypatch.setattr(runner, "_plan_targets", lambda *a, **k: [])
    monkeypatch.setattr(runner, "_triage_all", lambda *a, **k: None)
    runner._analyze(ctx, [], state, Stage.CRITIC)
    assert _facts_of(ctx.models.last_for(Role.CRITIC).body)["source"] == source()


def test_legacy_critic_does_not_read_source(ctx):
    def forbidden():
        pytest.fail("v1은 새 SOURCE를 읽으면 안 된다")

    critic.run(ctx, finding=_finding("finding_0001"), source=forbidden)
    assert _facts_of(ctx.models.last_for(Role.CRITIC).body)["source"] == ""


def test_v6_source_is_present_in_facts(ctx):
    _v6(ctx)
    text = "void parse_header(char *src, int n) { memcpy(dst, src, n); }"
    critic.run(ctx, finding=_finding("finding_0001"), source=lambda: text)
    assert _facts_of(ctx.models.last_for(Role.CRITIC).body)["source"] == text


def test_v6_without_source_keeps_an_empty_source(ctx):
    _v6(ctx)
    critic.run(ctx, finding=_finding("finding_0001"))
    assert _facts_of(ctx.models.last_for(Role.CRITIC).body)["source"] == ""
