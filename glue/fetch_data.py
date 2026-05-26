"""
Fetch market data for a thesis watchlist.

Hybrid data layer:
- Quotes / candles / ATR → MarketData.app (or IBKR streams when enabled).
- Option chains → auto-routing (Polygon preferred, MarketData.app fallback).
"""

from __future__ import annotations

import logging
from typing import Any

from glue.options import summarize_option_chain
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


def fetch_for_portfolio(
    theses: list[Thesis],
    data_provider: Any,
) -> dict[str, Any]:
    """Fetch and merge market data for all thesis watchlists."""
    merged: dict[str, Any] = {}
    lunar_attached = False
    for thesis in theses:
        chunk = fetch_for_thesis(thesis, data_provider)
        for sym, sym_data in chunk.items():
            if sym not in merged:
                merged[sym] = sym_data
            else:
                merged[sym].update(sym_data)
        if not lunar_attached and any(
            f.lower().strip() in ("lunar_phase", "moon") for f in thesis.data_fields
        ):
            lunar_attached = True
    merged["_meta"] = {"thesis_ids": [t.id for t in theses]}
    return merged


def fetch_for_thesis(
    thesis: Thesis,
    data_provider: Any,
) -> dict[str, Any]:
    """
    Return ``{symbol: {field: payload}}`` for every symbol on the watchlist.

    Option chains always use hybrid auto-routing (Polygon → MarketData.app).
    """
    out: dict[str, Any] = {}
    dte_min, dte_max = thesis.option_chain_dte
    delta_lo, delta_hi = thesis.delta_band

    lunar_payload = None
    if any(f.lower().strip() in ("lunar_phase", "moon") for f in thesis.data_fields):
        if hasattr(data_provider, "get_lunar_phase"):
            lp = data_provider.get_lunar_phase()
            lunar_payload = lp.__dict__ if hasattr(lp, "__dict__") else lp

    for symbol in thesis.watchlist:
        sym = symbol.upper()
        sym_data: dict[str, Any] = {}
        if lunar_payload is not None:
            sym_data["lunar_phase"] = lunar_payload

        for field in thesis.data_fields:
            key = field.lower().strip()
            if key in ("lunar_phase", "moon"):
                continue
            try:
                if key == "quote":
                    sym_data["quote"] = _quote_dict(data_provider.get_quote(sym))
                elif key == "candles":
                    sym_data["candles"] = _candles_summary(data_provider.get_candles(sym, days_back=30))
                elif key == "atr":
                    atr = data_provider.get_atr(sym)
                    if atr:
                        quote = data_provider.get_quote(sym)
                        price = float(getattr(quote, "last", 0) or 0) if quote else 0
                        pct = (atr.value / price * 100) if price > 0 else None
                        sym_data["atr"] = {"value": atr.value, "pct": pct, "source": atr.source}
                    else:
                        sym_data["atr"] = None
                elif key == "fundamentals":
                    f = data_provider.get_fundamentals(sym)
                    sym_data["fundamentals"] = f.__dict__ if f and hasattr(f, "__dict__") else f
                elif key == "news":
                    n = data_provider.get_news(sym)
                    sym_data["news"] = n.__dict__ if n and hasattr(n, "__dict__") else n
                elif key in ("option_chain", "options"):
                    chain = _fetch_options_chain(
                        data_provider,
                        sym,
                        dte_min=dte_min,
                        dte_max=dte_max,
                        delta_lo=delta_lo,
                        delta_hi=delta_hi,
                    )
                    sym_data["option_chain"] = summarize_option_chain(chain)
                elif key in ("sma", "ma"):
                    period = thesis.sma_period
                    if hasattr(data_provider, "get_sma_context"):
                        sym_data[key] = data_provider.get_sma_context(sym, period=period)
                    elif hasattr(data_provider, "get_sma"):
                        sym_data[key] = {"period": period, "sma": data_provider.get_sma(sym, period)}
                elif key == "vix":
                    if hasattr(data_provider, "get_vix"):
                        v = data_provider.get_vix()
                        sym_data["vix"] = v.__dict__ if v and hasattr(v, "__dict__") else v
                elif key == "iv_info":
                    sym_data["iv_info"] = (
                        data_provider.get_iv_info(sym) if hasattr(data_provider, "get_iv_info") else None
                    )
                else:
                    logger.warning("Unknown data field %r", key)
            except Exception as exc:
                logger.warning("Failed to fetch %s for %s: %s", key, sym, exc)
                sym_data[key] = {"error": str(exc)}
        out[sym] = sym_data
    return out


def _fetch_options_chain(
    data_provider: Any,
    symbol: str,
    *,
    dte_min: int,
    dte_max: int,
    delta_lo: float,
    delta_hi: float,
) -> Any:
    """Hybrid chain fetch — always auto-routes Polygon → MDA."""
    if hasattr(data_provider, "get_options_chain"):
        return data_provider.get_options_chain(
            symbol,
            expiration_range_days=dte_max,
            min_delta=delta_lo,
            max_delta=delta_hi,
            data_source="auto",
        )
    return data_provider.get_option_chain(symbol, dte_range=(dte_min, dte_max))
