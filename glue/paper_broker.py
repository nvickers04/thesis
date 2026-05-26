"""
Local paper broker — simulates fills using live MarketData quotes.

Used when ``EXECUTION_BACKEND=local_sim`` (the default). Implements the small
subset of the IBKR gateway surface that ``main.py`` needs for account checks
and market orders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PaperBroker:
    """In-memory cash account with simple market-order fills."""

    initial_cash: float
    data_provider: Any
    cash_value: float = 0.0
    net_liquidation: float = 0.0
    _positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    fills: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash_value = float(self.initial_cash)
        self.net_liquidation = float(self.initial_cash)

    def __bool__(self) -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def account_id(self) -> str:
        return "PAPER-SIM"

    @property
    def day_trades_remaining(self) -> int:
        return 999

    def _mark_price(self, symbol: str) -> float:
        quote = self.data_provider.get_quote(symbol)
        if quote is None:
            return 0.0
        if isinstance(quote, dict):
            return float(quote.get("last") or quote.get("close") or 0)
        return float(getattr(quote, "last", 0) or getattr(quote, "mid", 0) or 0)

    def _update_nlv(self) -> None:
        mv = 0.0
        for sym, pos in self._positions.items():
            px = self._mark_price(sym)
            mv += px * int(pos["qty"])
        self.net_liquidation = self.cash_value + mv

    def get_cached_portfolio(self) -> list[Any]:
        out = []
        for sym, pos in self._positions.items():
            px = self._mark_price(sym)
            qty = int(pos["qty"])
            out.append(
                SimpleNamespace(
                    contract=SimpleNamespace(symbol=sym),
                    symbol=sym,
                    position=qty,
                    marketPrice=px,
                    marketValue=px * qty,
                    averageCost=float(pos["avg_cost"]),
                    unrealizedPNL=(px - float(pos["avg_cost"])) * qty,
                )
            )
        return out

    def get_cached_account_values(self) -> list[Any]:
        self._update_nlv()
        return [
            SimpleNamespace(tag="TotalCashValue", value=str(self.cash_value), currency="USD"),
            SimpleNamespace(tag="NetLiquidation", value=str(self.net_liquidation), currency="USD"),
        ]

    def get_cached_trades(self) -> list[Any]:
        return []

    async def connect(self) -> bool:
        logger.info("PaperBroker ready with $%s simulated cash", f"{self.initial_cash:,.2f}")
        return True

    async def disconnect(self) -> None:
        return None

    async def refresh_positions(self) -> None:
        self._update_nlv()

    async def get_account_summary(self) -> dict[str, float]:
        self._update_nlv()
        return {"totalcashvalue": self.cash_value, "netliquidation": self.net_liquidation}

    async def get_position(self, symbol: str) -> dict[str, Any] | None:
        pos = self._positions.get(symbol.upper())
        if not pos:
            return None
        px = self._mark_price(symbol)
        qty = int(pos["qty"])
        return {
            "symbol": symbol.upper(),
            "quantity": qty,
            "avg_cost": float(pos["avg_cost"]),
            "market_price": px,
            "unrealized_pnl": (px - float(pos["avg_cost"])) * qty,
        }

    async def get_open_orders(self) -> list[Any]:
        return []

    async def cancel_stops(self, underlying: str) -> dict[str, Any]:
        return {"cancelled": 0}

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        sym = symbol.upper()
        side_u = side.upper()
        px = self._mark_price(sym)
        if px <= 0:
            return {"error": f"No quote for {sym} — cannot simulate fill"}

        qty = int(qty)
        if qty <= 0:
            return {"error": "quantity must be positive"}

        pos = self._positions.get(sym, {"qty": 0, "avg_cost": 0.0})
        cur_qty = int(pos["qty"])
        pnl = 0.0

        if side_u == "BUY":
            cost = px * qty
            if cost > self.cash_value:
                return {
                    "error": f"Insufficient cash: need ${cost:,.2f}, have ${self.cash_value:,.2f}"
                }
            if cur_qty == 0:
                pos["entry_ts"] = datetime.now(timezone.utc).isoformat()
            new_qty = cur_qty + qty
            pos["avg_cost"] = (
                (float(pos["avg_cost"]) * cur_qty + px * qty) / new_qty if new_qty else px
            )
            pos["qty"] = new_qty
            self.cash_value -= cost
            self._positions[sym] = pos
        elif side_u == "SELL":
            if cur_qty < qty:
                return {"error": f"Cannot sell {qty} {sym}: only hold {cur_qty}"}
            proceeds = px * qty
            pnl = (px - float(pos["avg_cost"])) * qty
            self.cash_value += proceeds
            new_qty = cur_qty - qty
            if new_qty == 0:
                self._positions.pop(sym, None)
            else:
                pos["qty"] = new_qty
                self._positions[sym] = pos
        else:
            return {"error": f"Unknown side {side_u}"}

        self._update_nlv()
        fill = {
            "simulated": True,
            "symbol": sym,
            "side": side_u,
            "quantity": qty,
            "price": px,
            "pnl": pnl,
            "cash_after": self.cash_value,
            "nlv_after": self.net_liquidation,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.fills.append(fill)
        logger.info("PAPER SIM fill: %s", fill)
        return fill
