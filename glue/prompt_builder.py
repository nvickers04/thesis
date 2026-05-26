"""
Build the Grok prompt for a single thesis evaluation cycle.

Keeps prompt construction in one place so ``main.py`` stays easy to read.
"""

from __future__ import annotations

import json
from typing import Any

from theses import Thesis


DECISION_SCHEMA = """
Respond with ONE JSON object only (no markdown fences), shaped like:
{
  "action": "buy" | "sell" | "hold",
  "symbol": "TICKER from the watchlist",
  "quantity": 0,
  "confidence": 0.0,
  "stop_loss_pct": 5.0,
  "take_profit_pct": 10.0,
  "rationale": "2-4 sentences explaining the decision"
}

Rules:
- Only pick symbols from the watchlist.
- Use action \"hold\" when there is no clear edge.
- quantity must be a positive integer for buy/sell; use 0 for hold.
- stop_loss_pct and take_profit_pct express reward:risk for the idea.
- Be conservative — this is paper trading practice, not YOLO mode.
"""


def build_system_prompt(*, trading_mode: str, cash_only: bool) -> str:
    """Short system instructions for Grok."""
    mode_line = {
        "paper": "PAPER MODE — practice capital. Size modestly and explain your reasoning.",
        "aggressive_paper": "AGGRESSIVE PAPER — still simulated/practice, but you may explore more setups.",
        "live": "LIVE MODE — real money. Only high-conviction, well-sized entries.",
    }.get(trading_mode, "PAPER MODE — practice capital.")

    cash_line = (
        "CASH-ONLY account: long stock entries only (no short stock). "
        "Bearish views should use hold or closing existing longs."
        if cash_only
        else "Standard cash account rules apply."
    )

    return (
        "You are a disciplined execution assistant for a private thesis-driven trader.\n"
        f"{mode_line}\n"
        f"{cash_line}\n"
        f"{DECISION_SCHEMA.strip()}"
    )


def build_user_prompt(
    thesis: Thesis,
    market_data: dict[str, Any],
    account_snapshot: dict[str, Any],
) -> str:
    """Combine thesis narrative + fetched market data + account state."""
    return (
        f"# Active thesis: {thesis.name}\n\n"
        f"## Strategy narrative\n{thesis.description.strip()}\n\n"
        f"## Watchlist\n{', '.join(thesis.watchlist)}\n\n"
        f"## Data requested for this thesis\n{', '.join(thesis.data_fields)}\n\n"
        f"## Market data snapshot\n```json\n{json.dumps(market_data, indent=2, default=str)}\n```\n\n"
        f"## Account snapshot\n```json\n{json.dumps(account_snapshot, indent=2, default=str)}\n```\n\n"
        "Given ONLY the thesis above and the data snapshot, return your JSON decision."
    )
