"""
Thesis definitions — edit this file to add your strategies.

Each thesis is a self-contained trading idea with:
  - a human-readable narrative (what you're looking for)
  - a watchlist of tickers Grok may choose from
  - which market-data fields to fetch before prompting Grok

Keep 2–5 active theses. Disable extras with ``enabled=False`` instead of deleting.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Thesis:
    """One user-defined strategy slot."""

    id: str
    name: str
    description: str
    watchlist: list[str]
    enabled: bool = True
    # Supported: quote, candles, atr, fundamentals, news
    data_fields: list[str] = field(default_factory=lambda: ["quote", "candles", "atr"])


# ── Add your theses below ───────────────────────────────────────────────────
#
# Example structure (copy, rename, and fill in — then set enabled=True):
#
# Thesis(
#     id="example_momentum",
#     name="Example — momentum continuation",
#     description=(
#         "Look for large-cap names holding above the 20-day range with rising "
#         "volume. Only consider entries when reward:risk is at least 2:1."
#     ),
#     watchlist=["AAPL", "MSFT", "NVDA"],
#     enabled=False,
#     data_fields=["quote", "candles", "atr"],
# ),
#
# Thesis(
#     id="example_mean_reversion",
#     name="Example — short-term mean reversion",
#     description=(
#         "Watch for oversold bounces in liquid ETFs after a 2–3 day pullback. "
#         "Prefer hold when the broad market trend is unclear."
#     ),
#     watchlist=["SPY", "QQQ", "IWM"],
#     enabled=False,
#     data_fields=["quote", "candles", "atr", "fundamentals"],
# ),


THESES: list[Thesis] = [
    # Your strategies go here. Leave empty until you define them.
]


def active_theses() -> list[Thesis]:
    """Return enabled theses (max 5 enforced in main.py)."""
    return [t for t in THESES if t.enabled]
