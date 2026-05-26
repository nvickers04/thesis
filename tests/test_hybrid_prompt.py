"""Tests for hybrid prompt builder and thesis config bridge."""

from __future__ import annotations

from glue.prompt_builder import (
    RULE_EXECUTOR_PREAMBLE,
    build_hybrid_system_prompt,
    parse_hybrid_decisions,
)
from theses import active_theses
from theses_rules.config_bridge import rules_from_theses, thesis_to_rule


def test_hybrid_system_prompt_starts_with_preamble():
    prompt = build_hybrid_system_prompt(
        theses=active_theses(),
        trading_mode="paper",
    )
    assert prompt.startswith(RULE_EXECUTOR_PREAMBLE)
    assert "overnight_drift" in prompt
    assert "covered_call_overlay" in prompt
    assert "Entry rules" in prompt


def test_parse_hybrid_decisions_list():
    raw = {"decisions": [{"thesis_id": "overnight_drift", "action": "hold"}]}
    assert len(parse_hybrid_decisions(raw)) == 1


def test_parse_hybrid_decisions_legacy_single():
    raw = {"action": "hold", "thesis_id": "lunar_swing"}
    assert parse_hybrid_decisions(raw)[0]["thesis_id"] == "lunar_swing"


def test_thesis_to_rule_maps_all_five():
    theses = active_theses()
    rules = rules_from_theses(theses)
    assert len(rules) == 5
    for t in theses:
        r = thesis_to_rule(t)
        assert r.id == t.id
        assert r.risk_per_trade_pct == t.base_risk_pct
