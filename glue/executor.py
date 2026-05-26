"""
Unified order execution — stocks and options through one entry point.
"""

from __future__ import annotations

import logging
from typing import Any

from glue.options import execute_option

logger = logging.getLogger(__name__)


def is_option_decision(decision: dict[str, Any]) -> bool:
    instrument = str(decision.get("instrument", "stock")).lower()
    return instrument in ("option", "opt", "options")


async def execute_decision(gateway: Any, decision: dict[str, Any], quantity: int) -> dict[str, Any]:
    """
    Step 6: send an approved Grok decision to the broker (IBKR or paper sim).

    Stocks: market order via ``place_market_order``.
    Options: routed through ``glue.options.execute_option``.
    """
    action = str(decision.get("action", "hold")).lower()
    if action == "hold":
        return {"status": "skipped", "reason": "hold"}

    if is_option_decision(decision):
        result = await execute_option(gateway, decision, quantity)
        await gateway.refresh_positions()
        return {"status": "submitted", "instrument": "option", **result}

    symbol = str(decision.get("symbol", "")).upper()
    side = "BUY" if action == "buy" else "SELL"
    result = await gateway.place_market_order(symbol, side, quantity)
    await gateway.refresh_positions()
    return {
        "status": "submitted",
        "instrument": "stock",
        "side": side,
        "symbol": symbol,
        "quantity": quantity,
        "broker": result,
    }
