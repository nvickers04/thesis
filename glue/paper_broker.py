"""
Local paper broker — simulates stock and option fills using live quotes.

Used when EXECUTION_BACKEND=local_sim (default).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)


def _occ_key(symbol: str, expiration: str, strike: float, right: str) -> str:
    return f"{symbol.upper()}_{expiration}_{right.upper()}_{strike}"


@dataclass
class PaperBroker:
    initial_cash: float
    data_provider: Any
    cash_value: float = 0.0
    net_liquidation: float = 0.0
    _stock_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    _option_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
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

    def _stock_price(self, symbol: str) -> float:
        quote = self.data_provider.get_quote(symbol)
        if quote is None:
            return 0.0
        if isinstance(quote, dict):
            return float(quote.get("last") or quote.get("close") or 0)
        return float(getattr(quote, "last", 0) or getattr(quote, "mid", 0) or 0)

    def _option_premium(self, symbol: str, expiration: str, strike: float, right: str) -> float:
        chain = self.data_provider.get_option_chain(symbol, dte_range=(1, 120))
        if chain is None:
            return 2.0  # fallback for sim
        for c in getattr(chain, "contracts", []) or []:
            exp = getattr(c, "expiration", None) or (c.get("expiration") if isinstance(c, dict) else None)
            st = getattr(c, "strike", None) or (c.get("strike") if isinstance(c, dict) else None)
            rt = getattr(c, "right", None) or (c.get("right") if isinstance(c, dict) else None)
            exp_norm = str(exp or "").replace("-", "")
            if exp_norm.endswith(expiration[-8:]) or exp_norm == expiration:
                if float(st or 0) == float(strike) and str(rt or "").upper().startswith(right[0].upper()):
                    bid = getattr(c, "bid", None) or (c.get("bid") if isinstance(c, dict) else None)
                    ask = getattr(c, "ask", None) or (c.get("ask") if isinstance(c, dict) else None)
                    if bid and ask:
                        return (float(bid) + float(ask)) / 2
                    return float(ask or bid or 2.0)
        return 2.0

    def _update_nlv(self) -> None:
        mv = 0.0
        for sym, pos in self._stock_positions.items():
            mv += self._stock_price(sym) * int(pos["qty"])
        for key, pos in self._option_positions.items():
            parts = key.split("_")
            if len(parts) >= 4:
                sym, exp, right, strike = parts[0], parts[1], parts[2], float(parts[3])
                mv += self._option_premium(sym, exp, strike, right) * 100 * int(pos["qty"])
        self.net_liquidation = self.cash_value + mv

    def get_cached_portfolio(self) -> list[Any]:
        out = []
        for sym, pos in self._stock_positions.items():
            px = self._stock_price(sym)
            qty = int(pos["qty"])
            out.append(
                SimpleNamespace(
                    contract=SimpleNamespace(symbol=sym, secType="STK"),
                    symbol=sym,
                    position=qty,
                    marketPrice=px,
                    marketValue=px * qty,
                    averageCost=float(pos["avg_cost"]),
                )
            )
        for key, pos in self._option_positions.items():
            parts = key.split("_")
            sym = parts[0]
            px = self._option_premium(sym, parts[1], float(parts[3]), parts[2]) * 100
            qty = int(pos["qty"])
            out.append(
                SimpleNamespace(
                    contract=SimpleNamespace(symbol=sym, secType="OPT"),
                    symbol=sym,
                    position=qty,
                    marketPrice=px,
                    marketValue=px * qty,
                    averageCost=float(pos["avg_cost"]),
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
        pos = self._stock_positions.get(symbol.upper())
        if not pos:
            return None
        px = self._stock_price(symbol)
        qty = int(pos["qty"])
        return {"symbol": symbol.upper(), "quantity": qty, "avg_cost": pos["avg_cost"], "market_price": px}

    async def get_open_orders(self) -> list[Any]:
        return []

    async def cancel_stops(self, underlying: str) -> dict[str, Any]:
        return {"cancelled": 0}

    async def place_market_order(self, symbol: str, side: str, qty: int, **kwargs: Any) -> dict[str, Any]:
        sym = symbol.upper()
        side_u = side.upper()
        px = self._stock_price(sym)
        if px <= 0:
            return {"error": f"No quote for {sym}"}
        qty = int(qty)
        pos = self._stock_positions.get(sym, {"qty": 0, "avg_cost": 0.0})
        cur = int(pos["qty"])

        if side_u == "BUY":
            cost = px * qty
            if cost > self.cash_value:
                return {"error": f"Insufficient cash: need ${cost:,.2f}"}
            new_qty = cur + qty
            pos["avg_cost"] = (float(pos["avg_cost"]) * cur + px * qty) / new_qty if new_qty else px
            pos["qty"] = new_qty
            self.cash_value -= cost
            self._stock_positions[sym] = pos
        elif side_u == "SELL":
            if cur < qty:
                return {"error": f"Cannot sell {qty} {sym}"}
            self.cash_value += px * qty
            new_qty = cur - qty
            if new_qty == 0:
                self._stock_positions.pop(sym, None)
            else:
                pos["qty"] = new_qty
                self._stock_positions[sym] = pos
        else:
            return {"error": f"Unknown side {side_u}"}

        self._update_nlv()
        fill = {"simulated": True, "type": "stock", "symbol": sym, "side": side_u, "quantity": qty, "price": px}
        self.fills.append(fill)
        return fill

    async def buy_option(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str,
        quantity: int = 1,
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        key = _occ_key(symbol, expiration, strike, right)
        premium = float(limit_price) if limit_price is not None else self._option_premium(symbol, expiration, strike, right)
        cost = premium * 100 * int(quantity)
        if cost > self.cash_value:
            return {"error": f"Insufficient cash for option: need ${cost:,.2f}"}
        pos = self._option_positions.get(key, {"qty": 0, "avg_cost": 0.0})
        cur = int(pos["qty"])
        new_qty = cur + int(quantity)
        pos["avg_cost"] = (float(pos["avg_cost"]) * cur + premium * 100 * quantity) / new_qty if new_qty else premium * 100
        pos["qty"] = new_qty
        self._option_positions[key] = pos
        self.cash_value -= cost
        self._update_nlv()
        fill = {"simulated": True, "type": "option", "success": True, "key": key, "premium": premium, "contracts": quantity}
        self.fills.append(fill)
        return fill

    async def place_vertical_spread(
        self,
        symbol: str,
        expiration: str,
        long_strike: float,
        short_strike: float,
        right: str,
        quantity: int = 1,
        order_type: str = "LMT",
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        # Simplified sim: treat as defined-risk debit = limit * 100 * qty
        debit = float(limit_price or abs(long_strike - short_strike) * 0.4) * 100 * quantity
        if debit > self.cash_value:
            return {"error": "Insufficient cash for spread debit"}
        self.cash_value -= debit
        self._update_nlv()
        return {"simulated": True, "type": "vertical_spread", "success": True, "debit": debit}

    async def close_option_position(
        self,
        symbol: str,
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        quantity: int | None = None,
        limit_price: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        key = _occ_key(symbol, str(expiration), float(strike), str(right))
        pos = self._option_positions.get(key)
        if not pos:
            return {"error": f"No simulated option position {key}"}
        qty = int(quantity or pos["qty"])
        premium = float(limit_price) if limit_price is not None else self._option_premium(
            symbol, str(expiration), float(strike), str(right)
        )
        proceeds = premium * 100 * qty
        self.cash_value += proceeds
        pos["qty"] = int(pos["qty"]) - qty
        if pos["qty"] <= 0:
            self._option_positions.pop(key, None)
        else:
            self._option_positions[key] = pos
        self._update_nlv()
        return {"simulated": True, "type": "close_option", "success": True, "proceeds": proceeds}

    async def sell_covered_call(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str,
        quantity: int = 1,
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        sym = symbol.upper()
        shares = int(self._stock_positions.get(sym, {}).get("qty", 0))
        qty = int(quantity)
        if shares < qty * 100:
            return {"error": f"Need {qty * 100} shares of {sym} for covered call (hold {shares})"}
        key = _occ_key(sym, expiration, strike, right)
        premium = float(limit_price) if limit_price is not None else self._option_premium(
            sym, expiration, strike, right
        )
        credit = premium * 100 * qty
        pos = self._option_positions.get(key, {"qty": 0, "avg_cost": 0.0})
        pos["qty"] = int(pos["qty"]) - qty  # short option
        pos["avg_cost"] = premium * 100
        self._option_positions[key] = pos
        self.cash_value += credit
        self._update_nlv()
        fill = {
            "simulated": True,
            "type": "covered_call",
            "success": True,
            "symbol": sym,
            "premium": premium,
            "contracts": qty,
            "credit": credit,
        }
        self.fills.append(fill)
        return fill

    async def buy_to_close_call(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str,
        quantity: int = 1,
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        sym = symbol.upper()
        key = _occ_key(sym, expiration, strike, right)
        pos = self._option_positions.get(key)
        if not pos or int(pos.get("qty", 0)) >= 0:
            return {"error": f"No short call to close for {key}"}
        qty = min(int(quantity), abs(int(pos["qty"])))
        premium = float(limit_price) if limit_price is not None else self._option_premium(
            sym, expiration, strike, right
        )
        cost = premium * 100 * qty
        if cost > self.cash_value:
            return {"error": f"Insufficient cash to buy back call: need ${cost:,.2f}"}
        pos["qty"] = int(pos["qty"]) + qty
        if int(pos["qty"]) == 0:
            self._option_positions.pop(key, None)
        else:
            self._option_positions[key] = pos
        self.cash_value -= cost
        self._update_nlv()
        fill = {
            "simulated": True,
            "type": "close_short_call",
            "success": True,
            "symbol": sym,
            "premium": premium,
            "contracts": qty,
            "debit": cost,
        }
        self.fills.append(fill)
        return fill
