"""
Fetch market data for a thesis watchlist using ABC's DataProvider + MarketData.app.

Each field name in ``thesis.data_fields`` maps to a DataProvider call.
Unknown fields are skipped with a warning.
"""

from __future__ import annotations

import logging
from typing import Any

from theses import Thesis

logger = logging.getLogger(__name__)


def _quote_dict(quote: Any) -> dict[str, Any] | None:
    if quote is None:
        return None
    if isinstance(quote, dict):
        return quote
    return {
        "symbol": getattr(quote, "symbol", None),
        "last": getattr(quote, "last", None),
        "bid": getattr(quote, "bid", None),
        "ask": getattr(quote, "ask", None),
        "volume": getattr(quote, "volume", None),
        "change_pct": getattr(quote, "change_pct", None),
        "source": getattr(quote, "source", None),
    }


def _candles_summary(candles: Any, *, tail: int = 10) -> dict[str, Any] | None:
    if candles is None or len(candles) == 0:
        return None
    n = len(candles)
    start = max(0, n - tail)
    rows = []
    for i in range(start, n):
        bar = candles[i]
        rows.append(
            {
                "close": getattr(bar, "close", None),
                "volume": getattr(bar, "volume", None),
                "timestamp": getattr(bar, "timestamp", None),
            }
        )
    return {"bars": rows, "count": n, "source": getattr(candles, "source", None)}


def fetch_for_thesis(thesis: Thesis, data_provider: Any) -> dict[str, Any]:
    """Return ``{symbol: {field: payload}}`` for every symbol on the watchlist."""
    out: dict[str, Any] = {}
    for symbol in thesis.watchlist:
        sym = symbol.upper()
        sym_data: dict[str, Any] = {}
        for field in thesis.data_fields:
            key = field.lower().strip()
            try:
                if key == "quote":
                    sym_data["quote"] = _quote_dict(data_provider.get_quote(sym))
                elif key == "candles":
                    sym_data["candles"] = _candles_summary(data_provider.get_candles(sym, days=30))
                elif key == "atr":
                    atr = data_provider.get_atr(sym)
                    if atr:
                        quote = data_provider.get_quote(sym)
                        price = float(getattr(quote, "last", 0) or 0) if quote else 0
                        pct = (atr.value / price * 100) if price > 0 else None
                        sym_data["atr"] = {
                            "value": atr.value,
                            "pct": pct,
                            "source": atr.source,
                        }
                    else:
                        sym_data["atr"] = None
                elif key == "fundamentals":
                    sym_data["fundamentals"] = data_provider.get_fundamentals(sym)
                elif key == "news":
                    sym_data["news"] = data_provider.get_news(sym)
                else:
                    logger.warning("Unknown data field %r — add support in glue/fetch_data.py", key)
            except Exception as exc:
                logger.warning("Failed to fetch %s for %s: %s", key, sym, exc)
                sym_data[key] = {"error": str(exc)}
        out[sym] = sym_data
    return out
