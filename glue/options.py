"""
Options helpers — validation and routing to execution/ibkr_options.py.

Option chains come from MarketData.app (REST). Order placement uses IBKR when
``EXECUTION_BACKEND=ibkr``, or local paper simulation otherwise.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Grok → our internal strategy names
SUPPORTED_STRATEGIES = frozenset(
    {"long_call", "long_put", "vertical_spread", "close_option", "covered_call", "close_short_call"}
)

_EXPIRY_RE = re.compile(r"^\d{8}$")


def normalize_expiration(raw: str) -> str:
    """Return YYYYMMDD (IBKR format). Accepts YYYY-MM-DD."""
    s = str(raw or "").strip().replace("-", "")
    if not _EXPIRY_RE.match(s):
        raise ValueError(f"expiration must be YYYYMMDD or YYYY-MM-DD, got {raw!r}")
    datetime.strptime(s, "%Y%m%d")  # validate calendar date
    return s


def normalize_right(raw: str) -> str:
    r = str(raw or "").strip().upper()
    if r in ("C", "CALL"):
        return "C"
    if r in ("P", "PUT"):
        return "P"
    raise ValueError(f"right must be C or P, got {raw!r}")


def option_block(decision: dict[str, Any]) -> dict[str, Any]:
    """Extract the nested option dict from a Grok decision."""
    opt = decision.get("option")
    return opt if isinstance(opt, dict) else {}


def validate_option_decision(decision: dict[str, Any], *, allowed_strategies: list[str]) -> tuple[bool, str]:
    """Validate option fields before risk / execution."""
    opt = option_block(decision)
    strategy = str(opt.get("strategy") or decision.get("option_strategy") or "").lower()
    if not strategy:
        return False, "option.strategy is required for instrument=option"
    if strategy not in SUPPORTED_STRATEGIES:
        return False, f"unsupported option strategy {strategy!r}"
    allowed = {s.lower() for s in allowed_strategies}
    if allowed and strategy not in allowed:
        return False, f"strategy {strategy!r} not allowed for this thesis"

    symbol = str(decision.get("symbol", "")).upper()
    if not symbol:
        return False, "symbol is required"

    if strategy == "close_option":
        if not opt.get("expiration") or opt.get("strike") is None:
            return False, "close_option requires expiration and strike"
        try:
            normalize_expiration(str(opt["expiration"]))
            normalize_right(str(opt.get("right", "C")))
            float(opt["strike"])
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        return True, "ok"

    try:
        normalize_expiration(str(opt.get("expiration", "")))
    except ValueError as exc:
        return False, str(exc)

    if strategy in ("long_call", "long_put"):
        try:
            normalize_right(str(opt.get("right", "C" if strategy == "long_call" else "P")))
            float(opt.get("strike", 0))
        except (TypeError, ValueError) as exc:
            return False, f"long option requires strike and right: {exc}"
        return True, "ok"

    if strategy == "vertical_spread":
        try:
            normalize_right(str(opt.get("right", "C")))
            float(opt["long_strike"])
            float(opt["short_strike"])
        except (KeyError, TypeError, ValueError) as exc:
            return False, f"vertical_spread requires long_strike, short_strike, right: {exc}"
        return True, "ok"

    if strategy == "covered_call":
        if action != "sell":
            return False, "covered_call requires action=sell"
        try:
            normalize_expiration(str(opt.get("expiration", "")))
            normalize_right(str(opt.get("right", "C")))
            float(opt.get("strike", 0))
        except (TypeError, ValueError) as exc:
            return False, f"covered_call requires expiration, strike, right: {exc}"
        return True, "ok"

    if strategy == "close_short_call":
        if action != "buy":
            return False, "close_short_call requires action=buy"
        try:
            normalize_expiration(str(opt.get("expiration", "")))
            normalize_right(str(opt.get("right", "C")))
            float(opt.get("strike", 0))
        except (TypeError, ValueError) as exc:
            return False, f"close_short_call requires expiration, strike, right: {exc}"
        return True, "ok"

    return False, f"unhandled strategy {strategy!r}"


def summarize_option_chain(chain: Any, *, limit: int = 12) -> dict[str, Any] | None:
    """Compact option chain for Grok prompts."""
    if chain is None:
        return None
    contracts = getattr(chain, "contracts", None) or []
    rows = []
    for c in contracts[:limit]:
        if isinstance(c, dict):
            rows.append(
                {
                    "expiration": c.get("expiration"),
                    "strike": c.get("strike"),
                    "right": c.get("right") or c.get("side"),
                    "bid": c.get("bid"),
                    "ask": c.get("ask"),
                    "delta": c.get("delta"),
                    "iv": c.get("iv"),
                }
            )
        else:
            rows.append(
                {
                    "expiration": getattr(c, "expiration", None),
                    "strike": getattr(c, "strike", None),
                    "right": getattr(c, "right", None),
                    "bid": getattr(c, "bid", None),
                    "ask": getattr(c, "ask", None),
                    "delta": getattr(c, "delta", None),
                    "iv": getattr(c, "iv", None),
                }
            )
    return {
        "underlying": getattr(chain, "symbol", None),
        "count": len(contracts),
        "sample": rows,
        "source": getattr(chain, "source", None),
    }


async def execute_option(gateway: Any, decision: dict[str, Any], quantity: int) -> dict[str, Any]:
    """Route an approved option decision to IBKR or paper sim."""
    opt = option_block(decision)
    strategy = str(opt.get("strategy") or "").lower()
    symbol = str(decision.get("symbol", "")).upper()
    qty = max(1, int(quantity))

    if strategy == "long_call":
        right = normalize_right(str(opt.get("right", "C")))
        exp = normalize_expiration(str(opt["expiration"]))
        strike = float(opt["strike"])
        limit = opt.get("limit_price")
        result = await gateway.buy_option(
            symbol, exp, strike, right, qty, float(limit) if limit is not None else None
        )
        return {"instrument": "option", "strategy": strategy, "broker": result}

    if strategy == "long_put":
        right = "P"
        exp = normalize_expiration(str(opt["expiration"]))
        strike = float(opt["strike"])
        limit = opt.get("limit_price")
        result = await gateway.buy_option(
            symbol, exp, strike, right, qty, float(limit) if limit is not None else None
        )
        return {"instrument": "option", "strategy": strategy, "broker": result}

    if strategy == "vertical_spread":
        exp = normalize_expiration(str(opt["expiration"]))
        right = normalize_right(str(opt.get("right", "C")))
        long_strike = float(opt["long_strike"])
        short_strike = float(opt["short_strike"])
        limit = opt.get("limit_price")
        result = await gateway.place_vertical_spread(
            symbol,
            exp,
            long_strike,
            short_strike,
            right,
            qty,
            limit_price=float(limit) if limit is not None else None,
        )
        return {"instrument": "option", "strategy": strategy, "broker": result}

    if strategy == "close_option":
        exp = normalize_expiration(str(opt["expiration"]))
        strike = float(opt["strike"])
        right = normalize_right(str(opt.get("right", "C")))
        limit = opt.get("limit_price")
        result = await gateway.close_option_position(
            symbol=symbol,
            expiration=exp,
            strike=strike,
            right=right,
            quantity=qty,
            limit_price=float(limit) if limit is not None else None,
        )
        return {"instrument": "option", "strategy": strategy, "broker": result}

    if strategy == "covered_call":
        exp = normalize_expiration(str(opt["expiration"]))
        strike = float(opt["strike"])
        right = normalize_right(str(opt.get("right", "C")))
        limit = opt.get("limit_price")
        sell_call = getattr(gateway, "sell_covered_call", None)
        if sell_call is None:
            return {"error": "gateway does not support covered_call"}
        result = await sell_call(
            symbol, exp, strike, right, qty, float(limit) if limit is not None else None
        )
        return {"instrument": "option", "strategy": strategy, "broker": result}

    if strategy == "close_short_call":
        exp = normalize_expiration(str(opt["expiration"]))
        strike = float(opt["strike"])
        right = normalize_right(str(opt.get("right", "C")))
        limit = opt.get("limit_price")
        buy_close = getattr(gateway, "buy_to_close_call", None)
        if buy_close is None:
            return {"error": "gateway does not support close_short_call"}
        result = await buy_close(
            symbol, exp, strike, right, qty, float(limit) if limit is not None else None
        )
        return {"instrument": "option", "strategy": strategy, "broker": result}

    return {"error": f"unsupported strategy {strategy!r}"}
