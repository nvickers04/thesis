"""Build Grok prompts — hybrid rule executor (default) and legacy LLM mode."""

from __future__ import annotations

import json
from typing import Any

from theses import Thesis

RULE_EXECUTOR_PREAMBLE = (
    "You are a precise rule executor with a good judgment window on sizing and allocation. "
    "Follow every entry, exit, timing, and filter rule exactly. "
    "You may use reasonable judgment to optimize position sizing and final allocation "
    "within the configurable base_risk_pct and max_allocation_pct. "
    "Try very hard to capitalize on high-conviction setups within the rules — be opportunistic "
    "and maximize edge capture safely without ever violating any guardrail. "
    "Return only valid JSON with your chosen allocation, sizing, and clear one-sentence reasoning."
)

HYBRID_DECISION_SCHEMA = """
Return ONE JSON object (no markdown fences):

{
  "decisions": [
    {
      "thesis_id": "<id from active theses>",
      "action": "buy" | "sell" | "hold",
      "instrument": "option",
      "symbol": "TICKER",
      "quantity": 0,
      "confidence": 0.0,
      "option": {
        "strategy": "long_call" | "vertical_spread" | "close_option" | "covered_call" | "close_short_call",
        "expiration": "YYYYMMDD",
        "right": "C",
        "strike": 0.0,
        "long_strike": 0.0,
        "short_strike": 0.0,
        "limit_price": null
      },
      "rationale": "One sentence."
    }
  ],
  "portfolio_rationale": "One to two sentences on overall allocation."
}

Rules:
- Include one entry per active thesis (use action=hold, quantity=0 when no trade).
- Pick symbols only from each thesis watchlist.
- Respect mechanical reference signals unless you have strong rule-compliant reason to differ.
- Never violate global risk gates or exceed base_risk_pct / max_allocation_pct per thesis.
- Defined-risk only: long calls, debit spreads, covered calls, closes — no naked shorts.
"""

OPTION_SCHEMA = """
Option decision JSON (instrument="option"):
{
  "action": "buy" | "sell" | "hold",
  "instrument": "option",
  "symbol": "UNDERLYING from watchlist",
  "quantity": 1,
  "confidence": 0.0,
  "stop_loss_pct": 50.0,
  "take_profit_pct": 100.0,
  "option": {
    "strategy": "long_call" | "long_put" | "vertical_spread" | "close_option",
    "expiration": "YYYYMMDD",
    "right": "C" | "P",
    "strike": 0.0,
    "long_strike": 0.0,
    "short_strike": 0.0,
    "limit_price": null
  },
  "rationale": "2-4 sentences"
}
"""


def _thesis_params_block(thesis: Thesis) -> str:
    dte_lo, dte_hi = thesis.dte_range
    delta_lo, delta_hi = thesis.delta_band
    spread_lo, spread_hi = thesis.spread_dte_range
    lines = [
        f"### {thesis.name} (`{thesis.id}`)",
        f"- Watchlist: {', '.join(thesis.watchlist)}",
        f"- Allowed strategies: {', '.join(thesis.option_strategies)}",
        f"- base_risk_pct: {thesis.base_risk_pct}% | max_allocation_pct: {thesis.max_allocation_pct}%",
        f"- dte_range: {dte_lo}–{dte_hi} | delta_band: {delta_lo}–{delta_hi} | target_delta: {thesis.target_delta}",
    ]
    if thesis.sma_period:
        lines.append(f"- sma_period: {thesis.sma_period}")
    if thesis.vix_max is not None:
        lines.append(f"- vix_max: {thesis.vix_max}")
    if thesis.take_profit_pct is not None:
        lines.append(f"- take_profit_pct: {thesis.take_profit_pct}")
    if thesis.min_otm_pct is not None and thesis.max_otm_pct is not None:
        lines.append(f"- otm_pct: {thesis.min_otm_pct}–{thesis.max_otm_pct}%")
    if thesis.leap_min_dte:
        lines.append(
            f"- leap_min_dte: {thesis.leap_min_dte} | spread_dte: {spread_lo}–{spread_hi}"
        )
    if thesis.timing_rule:
        lines.append(f"- Timing: {thesis.timing_rule}")

    if thesis.id == "overnight_drift":
        sym = thesis.watchlist[0]
        lines.extend([
            "#### Entry rules",
            f"- Instrument: {sym} long call, {dte_lo}–{dte_hi} DTE, delta {delta_lo}–{delta_hi} (target {thesis.target_delta}).",
            f"- Filter: prior close > SMA({thesis.sma_period}) and VIX < {thesis.vix_max}.",
            "- Timing: 15:55 ET Mon–Thu only.",
            "#### Exit rules",
            "- Close via close_option next session 9:30–9:45 ET.",
            "- Defined-risk only; max loss = premium paid.",
        ])
    elif thesis.id == "lunar_swing":
        lines.extend([
            "#### Entry rules",
            f"- Instrument: SPY or QQQ long call, {dte_lo}–{dte_hi} DTE, delta {delta_lo}–{delta_hi}.",
            "- Filter: new-moon window only (days 0–14 post new moon).",
            "#### Exit rules",
            "- Exit on full-moon window OR unrealized gain ≥ take_profit_pct.",
            "- Defined-risk only; max loss = premium paid.",
        ])
    elif thesis.id in ("defense_growth", "automation_boom"):
        lines.extend([
            "#### Entry rules",
            f"- Pick strongest watchlist symbol above SMA({thesis.sma_period}).",
            f"- Prefer LEAP long call: {thesis.leap_min_dte}+ DTE, delta {delta_lo}–{delta_hi}.",
            f"- Fallback: debit call spread {spread_lo}–{spread_hi} DTE (defined-risk).",
            "- Timing: quarterly rebalance Jan/Apr/Jul/Oct days 1–7 only.",
            "#### Exit rules",
            "- Close existing long calls during next quarterly rebalance window.",
            "- Hold between rebalance windows unless stop/roll rules apply.",
        ])
    elif thesis.id == "covered_call_overlay":
        lines.extend([
            "#### Entry rules",
            f"- Sell covered call against 100-share long stock lots on watchlist symbols.",
            f"- {dte_lo}–{dte_hi} DTE, {thesis.min_otm_pct}–{thesis.max_otm_pct}% OTM.",
            "- Monthly roll window: days 1–7 each month.",
            "#### Exit rules",
            "- close_short_call to roll or remove overlay.",
            "- No naked short calls; must own underlying shares.",
        ])

    lines.append(f"- Summary: {thesis.description.strip()}")
    return "\n".join(lines)


def format_explicit_rule_sets(theses: list[Thesis]) -> str:
    """Full explicit rule sets for all active theses."""
    blocks = [_thesis_params_block(t) for t in theses]
    return "## Explicit rule sets (follow exactly)\n\n" + "\n\n".join(blocks)


def build_hybrid_system_prompt(
    *,
    theses: list[Thesis],
    trading_mode: str,
    global_risk: dict[str, Any] | None = None,
) -> str:
    mode_line = {
        "paper": "PAPER MODE — practice capital.",
        "aggressive_paper": "AGGRESSIVE PAPER — stay defined-risk.",
        "live": "LIVE MODE — real money.",
    }.get(trading_mode, "PAPER MODE.")

    parts = [
        RULE_EXECUTOR_PREAMBLE,
        "",
        mode_line,
        "",
        format_explicit_rule_sets(theses),
        "",
        "## Global portfolio guardrails",
        "- Peak drawdown >= 8% with cash < 50% NLV → no new premium risk.",
        "- Max option premium exposure: 50% of net liquidation.",
        "- Hybrid data: MarketData.app + Polygon auto-routing for option chains.",
    ]
    if global_risk:
        parts.append(f"- Current gates: ```json\n{json.dumps(global_risk, indent=2)}\n```")
    parts.append(HYBRID_DECISION_SCHEMA.strip())
    return "\n".join(parts)


def build_hybrid_user_prompt(
    *,
    theses: list[Thesis],
    market_data: dict[str, Any],
    mechanical_signals: list[dict[str, Any]],
    account_snapshot: dict[str, Any],
) -> str:
    return (
        f"# Active theses ({len(theses)})\n"
        f"{', '.join(t.id for t in theses)}\n\n"
        f"## Mechanical reference signals\n"
        f"```json\n{json.dumps(mechanical_signals, indent=2, default=str)}\n```\n\n"
        f"## Market data snapshot\n"
        f"```json\n{json.dumps(market_data, indent=2, default=str)}\n```\n\n"
        f"## Account snapshot\n"
        f"```json\n{json.dumps(account_snapshot, indent=2, default=str)}\n```\n\n"
        "Interpret the rules, reference signals, and data. Return your JSON allocation."
    )


# ── Legacy LLM-only prompts (hidden --legacy-llm flag) ───────────────────────


def build_system_prompt(
    *,
    thesis: Thesis,
    trading_mode: str,
    cash_only: bool,
) -> str:
    mode_line = {
        "paper": "PAPER MODE — practice capital. Size modestly.",
        "aggressive_paper": "AGGRESSIVE PAPER — explore setups but stay defined-risk.",
        "live": "LIVE MODE — real money. High conviction only.",
    }.get(trading_mode, "PAPER MODE.")

    parts = [
        "You are a disciplined execution assistant for a private thesis-driven trader.",
        mode_line,
    ]
    if cash_only:
        parts.append("CASH-ONLY: long stock/options only; no naked short options.")
    if "option" in thesis.instruments:
        allowed = ", ".join(thesis.option_strategies)
        parts.append(f"Allowed option strategies: {allowed}.")
        parts.append(OPTION_SCHEMA.strip())
    parts.append(
        "Rules: pick symbols only from the watchlist; use hold when unclear; "
        "one JSON object only, no markdown fences; quantity=0 for hold."
    )
    return "\n".join(parts)


def build_user_prompt(
    thesis: Thesis,
    market_data: dict[str, Any],
    account_snapshot: dict[str, Any],
) -> str:
    return (
        f"# Active thesis: {thesis.name}\n\n"
        f"## Strategy narrative\n{thesis.description.strip()}\n\n"
        f"## Watchlist\n{', '.join(thesis.watchlist)}\n\n"
        f"## Data fetched\n{', '.join(thesis.data_fields)}\n\n"
        f"## Market data snapshot\n```json\n{json.dumps(market_data, indent=2, default=str)}\n```\n\n"
        f"## Account snapshot\n```json\n{json.dumps(account_snapshot, indent=2, default=str)}\n```\n\n"
        "Return your JSON decision."
    )


def parse_hybrid_decisions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract decision list from Grok hybrid response."""
    if isinstance(raw.get("decisions"), list):
        return [d for d in raw["decisions"] if isinstance(d, dict)]
    if raw.get("action"):
        return [raw]
    return []
