"""
IBKR real-time quote streams — connect and warm subscriptions for a watchlist.

When ``IBKR_QUOTES_ENABLED=true``, ``DataProvider.get_quote()`` reads live
NBBO from IBKR instead of MarketData.app REST. Streams work in paper and live
TWS sessions (requires your IBKR market data subscriptions).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def ensure_ibkr_streams(watchlist: list[str]) -> dict[str, Any]:
    """
    Connect to IBKR (if needed) and promote streaming quotes for each symbol.

    Safe to call even when streams are disabled — returns immediately.
    """
    from glue.bootstrap import ibkr_streams_enabled

    if not ibkr_streams_enabled():
        return {"enabled": False, "reason": "IBKR_QUOTES_ENABLED is false"}

    symbols = [s.upper() for s in watchlist if s]
    if not symbols:
        return {"enabled": True, "subscribed": []}

    try:
        from execution.ibkr_core import get_ibkr_connector

        connector = get_ibkr_connector()
        if not connector.is_connected():
            ok = await connector.connect()
            if not ok:
                logger.warning("IBKR connect failed — quote streams unavailable")
                return {"enabled": True, "connected": False, "subscribed": []}

        from data.ibkr_quote_source import get_ibkr_quote_source

        src = get_ibkr_quote_source()
        if src is None:
            return {"enabled": True, "connected": True, "source": None}

        subscribed: list[str] = []
        for sym in symbols:
            try:
                ok = await src.promote(sym)
                if ok:
                    subscribed.append(sym)
            except Exception as exc:
                logger.warning("Stream promote failed for %s: %s", sym, exc)

        logger.info(
            "IBKR streams warmed: %d/%d symbols (lines %s/%s)",
            len(subscribed),
            len(symbols),
            getattr(src, "lines_in_use", "?"),
            getattr(src, "_line_budget", "?"),
        )
        return {
            "enabled": True,
            "connected": True,
            "subscribed": subscribed,
            "lines_in_use": getattr(src, "lines_in_use", None),
            "line_budget": getattr(src, "_line_budget", None),
        }
    except Exception as exc:
        logger.warning("ensure_ibkr_streams failed: %s", exc)
        return {"enabled": True, "error": str(exc)}
