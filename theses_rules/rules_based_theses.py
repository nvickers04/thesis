"""
Mechanical rule theses for ``rules_mode`` — no LLM required.

Each :class:`RuleThesis` evaluates market state via :class:`data.data_provider.DataProvider`
and :mod:`data.MoonCalculator`, then emits an exact JSON signal:

    {
      "action": "buy" | "sell" | "hold",
      "symbol": "SPY",
      "option_contract": { ... } | null,
      "quantity": 1,
      "limit_price": 3.50 | null,
      "reason": "..."
    }

Core selection math is implemented as pure functions for unit tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence, TypedDict
from zoneinfo import ZoneInfo

from data.MoonCalculator import is_full_moon_window, is_new_moon_window
from data.technicals import price_vs_sma_pct, sma

ET = ZoneInfo("America/New_York")


class OptionContractSpec(TypedDict, total=False):
    strategy: str
    expiration: str
    right: str
    strike: float
    long_strike: float
    short_strike: float


class TradeSignal(TypedDict, total=False):
    action: str
    symbol: str
    option_contract: OptionContractSpec | None
    quantity: int
    limit_price: float | None
    reason: str
    take_profit_pct: float
    stop_loss_pct: float


def hold_signal(reason: str, *, symbol: str = "") -> TradeSignal:
    return {
        "action": "hold",
        "symbol": symbol,
        "option_contract": None,
        "quantity": 0,
        "limit_price": None,
        "reason": reason,
    }


def buy_call_signal(
    *,
    symbol: str,
    expiration: str,
    strike: float,
    quantity: int,
    limit_price: float | None,
    reason: str,
    strategy: str = "long_call",
) -> TradeSignal:
    return {
        "action": "buy",
        "symbol": symbol.upper(),
        "option_contract": {
            "strategy": strategy,
            "expiration": expiration,
            "right": "C",
            "strike": float(strike),
        },
        "quantity": max(1, int(quantity)),
        "limit_price": float(limit_price) if limit_price is not None else None,
        "reason": reason,
    }


def sell_call_signal(
    *,
    symbol: str,
    expiration: str,
    strike: float,
    quantity: int,
    limit_price: float | None,
    reason: str,
    strategy: str = "covered_call",
) -> TradeSignal:
    return {
        "action": "sell",
        "symbol": symbol.upper(),
        "option_contract": {
            "strategy": strategy,
            "expiration": expiration,
            "right": "C",
            "strike": float(strike),
        },
        "quantity": max(1, int(quantity)),
        "limit_price": float(limit_price) if limit_price is not None else None,
        "reason": reason,
    }


def close_call_signal(
    *,
    symbol: str,
    expiration: str,
    strike: float,
    quantity: int,
    limit_price: float | None,
    reason: str,
) -> TradeSignal:
    return {
        "action": "sell",
        "symbol": symbol.upper(),
        "option_contract": {
            "strategy": "close_option",
            "expiration": expiration,
            "right": "C",
            "strike": float(strike),
        },
        "quantity": max(1, int(quantity)),
        "limit_price": float(limit_price) if limit_price is not None else None,
        "reason": reason,
    }


def buy_to_close_call_signal(
    *,
    symbol: str,
    expiration: str,
    strike: float,
    quantity: int,
    limit_price: float | None,
    reason: str,
    strategy: str = "close_short_call",
) -> TradeSignal:
    return {
        "action": "buy",
        "symbol": symbol.upper(),
        "option_contract": {
            "strategy": strategy,
            "expiration": expiration,
            "right": "C",
            "strike": float(strike),
        },
        "quantity": max(1, int(quantity)),
        "limit_price": float(limit_price) if limit_price is not None else None,
        "reason": reason,
    }


def spread_signal(
    *,
    symbol: str,
    expiration: str,
    long_strike: float,
    short_strike: float,
    quantity: int,
    limit_price: float | None,
    reason: str,
    right: str = "C",
) -> TradeSignal:
    return {
        "action": "buy",
        "symbol": symbol.upper(),
        "option_contract": {
            "strategy": "vertical_spread",
            "expiration": expiration,
            "right": right,
            "long_strike": float(long_strike),
            "short_strike": float(short_strike),
        },
        "quantity": max(1, int(quantity)),
        "limit_price": float(limit_price) if limit_price is not None else None,
        "reason": reason,
    }


def signal_to_execution_decision(signal: TradeSignal) -> dict[str, Any]:
    """Map rules signal → Grok/executor decision shape."""
    action = str(signal.get("action", "hold")).lower()
    if action == "hold":
        return {
            "action": "hold",
            "instrument": "option",
            "symbol": signal.get("symbol") or "",
            "quantity": 0,
            "rationale": signal.get("reason", ""),
        }
    opt = dict(signal.get("option_contract") or {})
    if signal.get("limit_price") is not None:
        opt["limit_price"] = signal["limit_price"]
    decision: dict[str, Any] = {
        "action": action,
        "instrument": "option",
        "symbol": signal.get("symbol", ""),
        "quantity": int(signal.get("quantity") or 1),
        "option": opt,
        "rationale": signal.get("reason", ""),
    }
    if signal.get("take_profit_pct") is not None:
        decision["take_profit_pct"] = float(signal["take_profit_pct"])
    if signal.get("stop_loss_pct") is not None:
        decision["stop_loss_pct"] = float(signal["stop_loss_pct"])
    return decision


# ── Pure helpers (no I/O) ────────────────────────────────────────────────────


def is_weekday(dt: datetime) -> bool:
    local = dt.astimezone(ET)
    return local.weekday() < 5


def is_run_time_1555_et(dt: datetime, *, tolerance_minutes: int = 4) -> bool:
    """True at 15:55 ET (± tolerance) on a weekday."""
    local = dt.astimezone(ET)
    if local.weekday() >= 5:
        return False
    target = local.replace(hour=15, minute=55, second=0, microsecond=0)
    delta = abs((local - target).total_seconds()) / 60.0
    return delta <= tolerance_minutes


def is_market_open_exit_window(dt: datetime, *, tolerance_minutes: int = 15) -> bool:
    """True 9:30–9:45 ET on a weekday — overnight close-to-open exit window."""
    local = dt.astimezone(ET)
    if local.weekday() >= 5:
        return False
    open_time = local.replace(hour=9, minute=30, second=0, microsecond=0)
    end = open_time + timedelta(minutes=tolerance_minutes)
    return open_time <= local <= end


def is_near_market_close(dt: datetime, *, minutes_before_close: int = 30) -> bool:
    """True in the last ``minutes_before_close`` before 16:00 ET on a weekday."""
    local = dt.astimezone(ET)
    if local.weekday() >= 5:
        return False
    close = local.replace(hour=16, minute=0, second=0, microsecond=0)
    window_start = close - timedelta(minutes=minutes_before_close)
    return window_start <= local <= close


def _contract_field(contract: Any, name: str, default: Any = None) -> Any:
    if isinstance(contract, Mapping):
        return contract.get(name, default)
    return getattr(contract, name, default)


def normalize_contracts(contracts: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in contracts or []:
        side = str(_contract_field(c, "side", "") or "").lower()
        if side and side != "call":
            continue
        exp = str(_contract_field(c, "expiration", "") or "").replace("-", "")
        rows.append(
            {
                "expiration": exp,
                "strike": float(_contract_field(c, "strike", 0) or 0),
                "dte": _contract_field(c, "dte"),
                "delta": _contract_field(c, "delta"),
                "bid": _contract_field(c, "bid"),
                "ask": _contract_field(c, "ask"),
                "mid": _contract_field(c, "mid"),
                "last": _contract_field(c, "last"),
            }
        )
    return rows


def filter_calls_by_dte(
    contracts: Sequence[Any],
    *,
    min_dte: int,
    max_dte: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in normalize_contracts(contracts):
        dte = row.get("dte")
        if dte is None:
            continue
        if min_dte <= int(dte) <= max_dte:
            out.append(row)
    return out


def contract_mid(row: Mapping[str, Any]) -> float | None:
    mid = row.get("mid")
    if mid is not None:
        return float(mid)
    bid, ask = row.get("bid"), row.get("ask")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2.0
    if ask is not None:
        return float(ask)
    if bid is not None:
        return float(bid)
    last = row.get("last")
    return float(last) if last is not None else None


def pick_nearest_delta_call(
    contracts: Sequence[Any],
    *,
    min_dte: int,
    max_dte: int,
    target_delta: float = 0.50,
    min_delta: float | None = None,
    max_delta: float | None = None,
) -> dict[str, Any] | None:
    """Pick call closest to ``target_delta`` within DTE band (optional delta band)."""
    candidates = filter_calls_by_dte(contracts, min_dte=min_dte, max_dte=max_dte)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in candidates:
        delta = row.get("delta")
        if delta is None:
            continue
        ad = abs(float(delta))
        if min_delta is not None and ad < min_delta:
            continue
        if max_delta is not None and ad > max_delta:
            continue
        scored.append((abs(ad - target_delta), row))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def previous_close_above_sma(
    closes: Sequence[float],
    *,
    period: int = 50,
) -> tuple[bool, str, float | None, float | None]:
    """
    Prior **completed** session close vs SMA(period) through yesterday.

    Returns:
        (ok, reason, prior_close, sma_value)
    """
    if len(closes) < period + 1:
        return False, f"need {period + 1}+ daily bars", None, None
    prior_close = float(closes[-2])
    sma_val = sma(list(closes[:-1]), period)
    if sma_val is None:
        return False, f"SMA({period}) unavailable", prior_close, None
    if prior_close <= sma_val:
        return (
            False,
            f"prior close {prior_close:.2f} <= SMA({period}) {sma_val:.2f}",
            prior_close,
            sma_val,
        )
    return True, f"prior close {prior_close:.2f} > SMA({period}) {sma_val:.2f}", prior_close, sma_val


def vix_below_threshold(vix_last: float | None, threshold: float = 30.0) -> tuple[bool, str]:
    if vix_last is None:
        return False, "VIX unavailable"
    if vix_last >= threshold:
        return False, f"VIX {vix_last:.2f} >= {threshold:.0f}"
    return True, f"VIX {vix_last:.2f} < {threshold:.0f}"


def contracts_for_risk_budget(
    net_liquidation: float,
    premium_per_share: float,
    *,
    risk_pct: float = 2.0,
    cash: float | None = None,
    max_contracts: int = 10,
) -> int:
    """Size contracts so max outlay (premium or spread debit) ≤ ``risk_pct``% of NLV."""
    if net_liquidation <= 0 or premium_per_share <= 0:
        return 0
    risk_dollars = net_liquidation * (risk_pct / 100.0)
    cost_per_contract = premium_per_share * 100.0
    by_risk = int(risk_dollars / cost_per_contract)
    by_cash = int(cash / cost_per_contract) if cash is not None else by_risk
    return max(0, min(by_risk, by_cash, max_contracts))


def fetch_hybrid_chain(
    provider: Any,
    symbol: str,
    *,
    expiration_range_days: int,
    min_delta: float,
    max_delta: float,
) -> Any:
    """Hybrid Polygon → MDA chain fetch (``data_source='auto'``)."""
    return provider.get_options_chain(
        symbol.upper(),
        expiration_range_days=expiration_range_days,
        min_delta=min_delta,
        max_delta=max_delta,
        data_source="auto",
    )


def is_quarterly_rebalance_window(dt: datetime) -> tuple[bool, str]:
    """True during the first calendar week of Jan / Apr / Jul / Oct (weekdays)."""
    local = dt.astimezone(ET)
    if local.weekday() >= 5:
        return False, "weekend"
    if local.month not in (1, 4, 7, 10):
        return False, "outside quarterly rebalance months (Jan/Apr/Jul/Oct)"
    if local.day > 7:
        return False, "outside first-week quarterly rebalance window"
    quarter = (local.month - 1) // 3 + 1
    return True, f"Q{quarter} weekly rebalance window (month day {local.day})"


def is_monthly_overlay_window(dt: datetime) -> tuple[bool, str]:
    """True weekdays on calendar days 1–7 — monthly covered-call roll window."""
    local = dt.astimezone(ET)
    if local.weekday() >= 5:
        return False, "weekend"
    if local.day > 7:
        return False, "outside monthly overlay window (days 1–7)"
    return True, f"monthly overlay window (day {local.day})"


def spot_price_from_provider(provider: Any, symbol: str) -> float | None:
    quote = provider.get_quote(symbol.upper())
    if quote is None:
        return None
    if isinstance(quote, dict):
        px = quote.get("last") or quote.get("mid")
    else:
        px = getattr(quote, "last", None) or getattr(quote, "mid", None)
    return float(px) if px is not None else None


def pick_otm_call_by_strike_pct(
    contracts: Sequence[Any],
    spot: float,
    *,
    min_dte: int = 30,
    max_dte: int = 45,
    min_otm_pct: float = 3.0,
    max_otm_pct: float = 5.0,
) -> dict[str, Any] | None:
    """Pick call with strike ``min_otm_pct``–``max_otm_pct`` above ``spot``."""
    if spot <= 0:
        return None
    lo = spot * (1.0 + min_otm_pct / 100.0)
    hi = spot * (1.0 + max_otm_pct / 100.0)
    target = spot * (1.0 + (min_otm_pct + max_otm_pct) / 200.0)
    candidates = filter_calls_by_dte(contracts, min_dte=min_dte, max_dte=max_dte)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in candidates:
        strike = float(row["strike"])
        if lo <= strike <= hi:
            scored.append((abs(strike - target), row))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def adaptive_spread_width(spot: float, *, floor: float = 5.0, pct: float = 0.03) -> float:
    """Debit spread width — at least ``floor`` dollars or ``pct`` of spot."""
    return max(floor, round(spot * pct, 2))


def iter_short_call_positions(gateway: Any, symbols: Sequence[str]) -> list[dict[str, Any]]:
    """Short call legs (covered calls) for ``symbols``."""
    sym_set = {s.upper() for s in symbols}
    out: list[dict[str, Any]] = []

    internal = getattr(gateway, "_option_positions", None)
    if isinstance(internal, dict):
        for key, pos in internal.items():
            qty = int(pos.get("qty", 0) or 0)
            if qty >= 0:
                continue
            parts = str(key).split("_")
            if len(parts) < 4:
                continue
            sym, exp, right, strike_s = parts[0], parts[1], parts[2], parts[3]
            if sym not in sym_set or not str(right).upper().startswith("C"):
                continue
            out.append(
                {
                    "symbol": sym,
                    "expiration": exp,
                    "right": "C",
                    "strike": float(strike_s),
                    "qty": abs(qty),
                }
            )
        return out

    for item in getattr(gateway, "get_cached_portfolio", lambda: [])() or []:
        contract = getattr(item, "contract", item)
        sym = getattr(contract, "symbol", getattr(item, "symbol", "")).upper()
        sec_type = str(getattr(contract, "secType", getattr(item, "sec_type", "STK"))).upper()
        qty = int(getattr(item, "position", 0) or 0)
        if sym not in sym_set or qty >= 0 or sec_type not in ("OPT", "OPTION"):
            continue
        right = getattr(contract, "right", getattr(contract, "Right", "C"))
        if str(right).upper() not in ("C", "CALL"):
            continue
        exp = getattr(contract, "lastTradeDateOrContractMonth", getattr(contract, "expiration", ""))
        strike = getattr(contract, "strike", getattr(item, "strike", 0))
        out.append(
            {
                "symbol": sym,
                "expiration": str(exp).replace("-", ""),
                "right": "C",
                "strike": float(strike),
                "qty": abs(qty),
            }
        )
    return out


def option_pnl_pct(entry_premium: float, current_premium: float) -> float | None:
    if entry_premium <= 0:
        return None
    return ((current_premium - entry_premium) / entry_premium) * 100.0


def iter_long_call_positions(gateway: Any, symbols: Sequence[str]) -> list[dict[str, Any]]:
    """Long call legs for ``symbols`` from paper sim or gateway portfolio."""
    sym_set = {s.upper() for s in symbols}
    out: list[dict[str, Any]] = []

    internal = getattr(gateway, "_option_positions", None)
    if isinstance(internal, dict):
        for key, pos in internal.items():
            qty = int(pos.get("qty", 0) or 0)
            if qty <= 0:
                continue
            parts = str(key).split("_")
            if len(parts) < 4:
                continue
            sym, exp, right, strike_s = parts[0], parts[1], parts[2], parts[3]
            if sym not in sym_set or not str(right).upper().startswith("C"):
                continue
            avg_cost = float(pos.get("avg_cost", 0) or 0)
            out.append(
                {
                    "symbol": sym,
                    "expiration": exp,
                    "right": "C",
                    "strike": float(strike_s),
                    "qty": qty,
                    "entry_premium": avg_cost / 100.0 if avg_cost > 0 else None,
                }
            )
        return out

    for item in getattr(gateway, "get_cached_portfolio", lambda: [])() or []:
        contract = getattr(item, "contract", item)
        sym = getattr(contract, "symbol", getattr(item, "symbol", "")).upper()
        sec_type = str(getattr(contract, "secType", getattr(item, "sec_type", "STK"))).upper()
        qty = int(getattr(item, "position", 0) or 0)
        if sym not in sym_set or qty <= 0 or sec_type not in ("OPT", "OPTION"):
            continue
        right = getattr(contract, "right", getattr(contract, "Right", "C"))
        if str(right).upper() not in ("C", "CALL"):
            continue
        exp = getattr(contract, "lastTradeDateOrContractMonth", getattr(contract, "expiration", ""))
        strike = getattr(contract, "strike", getattr(item, "strike", 0))
        avg = float(getattr(item, "averageCost", getattr(item, "avg_cost", 0)) or 0)
        out.append(
            {
                "symbol": sym,
                "expiration": str(exp).replace("-", ""),
                "right": "C",
                "strike": float(strike),
                "qty": qty,
                "entry_premium": avg / 100.0 if avg > 0 else None,
            }
        )
    return out


def pick_otm_call_by_delta(
    contracts: Sequence[Any],
    *,
    min_dte: int,
    max_dte: int,
    min_delta: float = 0.20,
    max_delta: float = 0.35,
) -> dict[str, Any] | None:
    """Pick slightly OTM call (lower delta band) — covered-call overlay."""
    candidates = filter_calls_by_dte(contracts, min_dte=min_dte, max_dte=max_dte)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in candidates:
        delta = row.get("delta")
        if delta is None:
            continue
        ad = abs(float(delta))
        if min_delta <= ad <= max_delta:
            scored.append((abs(ad - 0.30), row))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def pick_debit_call_spread(
    contracts: Sequence[Any],
    *,
    min_dte: int,
    max_dte: int,
    target_delta: float = 0.45,
    width: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Long ATM-ish call + short call ``width`` points higher (same expiry)."""
    long_leg = pick_nearest_delta_call(
        contracts, min_dte=min_dte, max_dte=max_dte, target_delta=target_delta
    )
    if not long_leg:
        return None
    exp = long_leg["expiration"]
    long_strike = float(long_leg["strike"])
    short_target = long_strike + width
    shorts = [
        r
        for r in filter_calls_by_dte(contracts, min_dte=min_dte, max_dte=max_dte)
        if r["expiration"] == exp and float(r["strike"]) >= short_target
    ]
    if not shorts:
        return None
    short_leg = min(shorts, key=lambda r: abs(float(r["strike"]) - short_target))
    return long_leg, short_leg


def is_above_sma(closes: Sequence[float], period: int) -> bool:
    if len(closes) < period:
        return False
    ma_val = sma(closes, period)
    if ma_val is None:
        return False
    return float(closes[-1]) > ma_val


def pick_strongest_uptrend(
    symbols: Sequence[str],
    closes_by_symbol: Mapping[str, Sequence[float]],
    *,
    sma_period: int = 50,
) -> str | None:
    best_sym: str | None = None
    best_vs: float = float("-inf")
    for sym in symbols:
        closes = closes_by_symbol.get(sym.upper()) or closes_by_symbol.get(sym) or []
        if len(closes) < sma_period:
            continue
        ma_val = sma(closes, sma_period)
        if ma_val is None:
            continue
        vs = price_vs_sma_pct(float(closes[-1]), ma_val)
        if vs is None:
            continue
        if vs > best_vs:
            best_vs = vs
            best_sym = sym.upper()
    return best_sym


def stock_shares(portfolio: Sequence[Any], symbol: str) -> int:
    sym = symbol.upper()
    total = 0
    for item in portfolio or []:
        contract = getattr(item, "contract", item)
        item_sym = getattr(contract, "symbol", getattr(item, "symbol", "")).upper()
        sec_type = getattr(contract, "secType", getattr(item, "sec_type", "STK"))
        if item_sym == sym and str(sec_type).upper() in ("STK", "STOCK", ""):
            total += int(getattr(item, "position", 0) or 0)
    return total


# ── Rule thesis base ─────────────────────────────────────────────────────────


@dataclass
class RulesContext:
    """Inputs for one evaluation pass."""

    provider: Any
    when: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    portfolio: list[Any] = field(default_factory=list)
    net_liquidation: float = 0.0
    cash: float = 0.0
    risk_per_trade_pct: float = 2.0
    default_quantity: int = 1
    gateway: Any = None


@dataclass
class RuleThesis(ABC):
    """Mechanical thesis — implements ``evaluate`` → :class:`TradeSignal`."""

    id: str
    name: str
    watchlist: list[str]
    enabled: bool = True
    instruments: list[str] = field(default_factory=lambda: ["option"])
    option_strategies: list[str] = field(default_factory=lambda: ["long_call"])
    data_fields: list[str] = field(default_factory=lambda: ["quote", "option_chain", "lunar_phase"])
    risk_per_trade_pct: float = 2.0

    @abstractmethod
    def evaluate(self, ctx: RulesContext) -> TradeSignal:
        ...

    def _size_contracts(self, ctx: RulesContext, premium: float | None) -> int:
        pct = self.risk_per_trade_pct or ctx.risk_per_trade_pct
        if premium is None or premium <= 0:
            return 0
        return contracts_for_risk_budget(
            ctx.net_liquidation,
            premium,
            risk_pct=pct,
            cash=ctx.cash,
        )


# ── 1. Leveraged Overnight Drift ───────────────────────────────────────────


@dataclass
class LeveragedOvernightDriftThesis(RuleThesis):
    """
    SPY 5–7 DTE ~0.50Δ call at 15:55 ET (Mon–Thu).

    Entry: prior close > SMA(50) and VIX < 30.
    Exit: next session open (9:30–9:45 ET) via close_option.
    """

    id: str = "overnight_drift"
    name: str = "Leveraged Overnight Drift"
    watchlist: list[str] = field(default_factory=lambda: ["SPY"])
    option_strategies: list[str] = field(default_factory=lambda: ["long_call", "close_option"])
    risk_per_trade_pct: float = 2.0
    symbol: str = "SPY"
    min_dte: int = 5
    max_dte: int = 7
    min_delta: float = 0.45
    max_delta: float = 0.55
    target_delta: float = 0.50
    sma_period: int = 50
    vix_max: float = 30.0

    @staticmethod
    def entry_allowed(*, when: datetime, at_1555: bool, weekday: bool) -> tuple[bool, str]:
        if not weekday:
            return False, "weekend — no overnight drift entry"
        if when.astimezone(ET).weekday() == 4:
            return False, "Friday — skip weekend gap risk"
        if not at_1555:
            return False, "outside 15:55 ET entry window"
        return True, "15:55 ET entry window"

    def _fetch_chain(self, provider: Any, sym: str):
        return provider.get_options_chain(
            sym,
            expiration_range_days=self.max_dte,
            min_delta=self.min_delta,
            max_delta=self.max_delta,
            data_source="auto",
        )

    def _exit_signal(self, ctx: RulesContext) -> TradeSignal | None:
        if not is_market_open_exit_window(ctx.when):
            return None
        gateway = ctx.gateway
        if gateway is None:
            return None
        for pos in iter_long_call_positions(gateway, [self.symbol]):
            limit = self._current_premium(ctx.provider, pos)
            if not self._is_overnight_contract(ctx.provider, pos):
                continue
            return close_call_signal(
                symbol=pos["symbol"],
                expiration=str(pos["expiration"]),
                strike=float(pos["strike"]),
                quantity=int(pos["qty"]),
                limit_price=limit,
                reason="overnight drift: close-to-open exit at market open",
            )
        return None

    def _current_premium(self, provider: Any, pos: dict[str, Any]) -> float | None:
        chain = self._fetch_chain(provider, pos["symbol"])
        if not chain:
            return None
        for row in normalize_contracts(getattr(chain, "contracts", []) or []):
            if (
                row["expiration"] == str(pos["expiration"]).replace("-", "")
                and float(row["strike"]) == float(pos["strike"])
            ):
                return contract_mid(row)
        return None

    def _is_overnight_contract(self, provider: Any, pos: dict[str, Any]) -> bool:
        chain = provider.get_option_chain(
            pos["symbol"],
            dte_range=(self.min_dte, self.max_dte),
            side="call",
        )
        if not chain:
            return True
        for c in getattr(chain, "contracts", []) or []:
            exp = str(getattr(c, "expiration", "") or "").replace("-", "")
            if exp == str(pos["expiration"]).replace("-", "") and float(getattr(c, "strike", 0)) == float(pos["strike"]):
                dte = getattr(c, "dte", None)
                if dte is None:
                    return True
                return self.min_dte <= int(dte) <= self.max_dte
        return False

    def evaluate(self, ctx: RulesContext) -> TradeSignal:
        sym = self.symbol.upper()
        exit_sig = self._exit_signal(ctx)
        if exit_sig is not None:
            return exit_sig

        allowed, why = self.entry_allowed(
            when=ctx.when,
            at_1555=is_run_time_1555_et(ctx.when),
            weekday=is_weekday(ctx.when),
        )
        if not allowed:
            return hold_signal(why, symbol=sym)

        if ctx.gateway and iter_long_call_positions(ctx.gateway, [sym]):
            return hold_signal("already holding overnight SPY call", symbol=sym)

        candles = ctx.provider.get_candles(sym, days_back=self.sma_period + 5)
        if not candles or not candles.close:
            return hold_signal("no SPY candle history for SMA filter", symbol=sym)
        trend_ok, trend_reason, _, _ = previous_close_above_sma(
            list(candles.close), period=self.sma_period
        )
        if not trend_ok:
            return hold_signal(trend_reason, symbol=sym)

        vix = ctx.provider.get_vix()
        vix_ok, vix_reason = vix_below_threshold(
            float(vix.last) if vix and vix.last is not None else None,
            self.vix_max,
        )
        if not vix_ok:
            return hold_signal(vix_reason, symbol=sym)

        chain = self._fetch_chain(ctx.provider, sym)
        contracts = getattr(chain, "contracts", None) if chain else None
        pick = pick_nearest_delta_call(
            contracts or [],
            min_dte=self.min_dte,
            max_dte=self.max_dte,
            target_delta=self.target_delta,
            min_delta=self.min_delta,
            max_delta=self.max_delta,
        )
        if not pick:
            return hold_signal("no SPY 5–7 DTE call in Δ 0.45–0.55 band", symbol=sym)

        limit = contract_mid(pick)
        qty = self._size_contracts(ctx, limit)
        if qty <= 0:
            return hold_signal("2% risk budget yields 0 contracts at this premium", symbol=sym)

        sig = buy_call_signal(
            symbol=sym,
            expiration=str(pick["expiration"]),
            strike=float(pick["strike"]),
            quantity=qty,
            limit_price=limit,
            reason=f"overnight drift: {why}; {trend_reason}; {vix_reason}",
        )
        return sig


# ── 2. Lunar Options Swing ───────────────────────────────────────────────────


@dataclass
class LunarOptionsSwingThesis(RuleThesis):
    """
    SPY/QQQ 21–45 DTE call (Δ 0.45–0.55) — entry in new-moon window only.

    Exit: full-moon window OR unrealized gain ≥ +60%.
    """

    id: str = "lunar_swing"
    name: str = "Lunar Options Swing"
    watchlist: list[str] = field(default_factory=lambda: ["SPY", "QQQ"])
    option_strategies: list[str] = field(default_factory=lambda: ["long_call", "close_option"])
    risk_per_trade_pct: float = 2.0
    min_dte: int = 21
    max_dte: int = 45
    min_delta: float = 0.45
    max_delta: float = 0.55
    target_delta: float = 0.50
    take_profit_pct: float = 60.0

    @staticmethod
    def lunar_entry_allowed(in_new_moon_window: bool) -> tuple[bool, str]:
        if not in_new_moon_window:
            return False, "outside new-moon entry window (days 0–14 post new moon)"
        return True, "new-moon entry window active"

    def _current_premium(self, provider: Any, pos: dict[str, Any]) -> float | None:
        chain = provider.get_options_chain(
            pos["symbol"],
            expiration_range_days=self.max_dte,
            min_delta=self.min_delta,
            max_delta=self.max_delta,
            data_source="auto",
        )
        if not chain:
            return None
        for row in normalize_contracts(getattr(chain, "contracts", []) or []):
            if (
                row["expiration"] == str(pos["expiration"]).replace("-", "")
                and float(row["strike"]) == float(pos["strike"])
            ):
                return contract_mid(row)
        return None

    def _exit_signal(self, ctx: RulesContext) -> TradeSignal | None:
        if ctx.gateway is None:
            return None
        full_moon = is_full_moon_window(ctx.when)
        for pos in iter_long_call_positions(ctx.gateway, self.watchlist):
            current = self._current_premium(ctx.provider, pos)
            entry = pos.get("entry_premium")
            pnl = option_pnl_pct(float(entry), float(current)) if entry and current else None
            if full_moon:
                reason = "lunar swing exit: full-moon window"
            elif pnl is not None and pnl >= self.take_profit_pct:
                reason = f"lunar swing exit: +{pnl:.1f}% gain (target +{self.take_profit_pct:.0f}%)"
            else:
                continue
            return close_call_signal(
                symbol=pos["symbol"],
                expiration=str(pos["expiration"]),
                strike=float(pos["strike"]),
                quantity=int(pos["qty"]),
                limit_price=current,
                reason=reason,
            )
        return None

    def evaluate(self, ctx: RulesContext) -> TradeSignal:
        exit_sig = self._exit_signal(ctx)
        if exit_sig is not None:
            return exit_sig

        in_window = is_new_moon_window(ctx.when)
        allowed, why = self.lunar_entry_allowed(in_window)
        if not allowed:
            return hold_signal(why)

        if ctx.gateway and iter_long_call_positions(ctx.gateway, self.watchlist):
            return hold_signal("lunar swing: already holding SPY/QQQ call — waiting for exit")

        for sym in self.watchlist:
            target = sym.upper()
            chain = ctx.provider.get_options_chain(
                target,
                expiration_range_days=self.max_dte,
                min_delta=self.min_delta,
                max_delta=self.max_delta,
                data_source="auto",
            )
            contracts = getattr(chain, "contracts", None) if chain else None
            pick = pick_nearest_delta_call(
                contracts or [],
                min_dte=self.min_dte,
                max_dte=self.max_dte,
                target_delta=self.target_delta,
                min_delta=self.min_delta,
                max_delta=self.max_delta,
            )
            if not pick:
                continue

            limit = contract_mid(pick)
            qty = self._size_contracts(ctx, limit)
            if qty <= 0:
                return hold_signal("2% risk budget yields 0 contracts at this premium", symbol=target)

            sig = buy_call_signal(
                symbol=target,
                expiration=str(pick["expiration"]),
                strike=float(pick["strike"]),
                quantity=qty,
                limit_price=limit,
                reason=f"lunar swing entry: {why}; {self.min_dte}-{self.max_dte} DTE Δ {self.min_delta}-{self.max_delta}",
            )
            sig["take_profit_pct"] = self.take_profit_pct
            return sig

        return hold_signal("no SPY/QQQ 21–45 DTE call in Δ 0.45–0.55 during new-moon window")


        return max(0, min(by_risk, by_cash, max_contracts))


# ── Quarterly leveraged base (Defense / Automation) ──────────────────────────


@dataclass
class QuarterlyLeveragedTrendThesis(RuleThesis):
    """
    LEAP or debit vertical on strongest watchlist name above SMA — quarterly rebalance.

    Defined-risk only: LEAP (max loss = premium) or debit call spread fallback.
    """

    sma_period: int = 50
    leap_min_dte: int = 365
    leap_max_dte: int = 540
    leap_min_delta: float = 0.40
    leap_max_delta: float = 0.55
    leap_target_delta: float = 0.45
    spread_min_dte: int = 90
    spread_max_dte: int = 180
    spread_target_delta: float = 0.45
    risk_per_trade_pct: float = 3.0
    option_strategies: list[str] = field(
        default_factory=lambda: ["long_call", "vertical_spread", "close_option"]
    )

    def _trend_leader(self, ctx: RulesContext) -> tuple[str | None, str]:
        closes_by_sym: dict[str, list[float]] = {}
        for sym in self.watchlist:
            candles = ctx.provider.get_candles(sym, days_back=max(self.sma_period + 10, 260))
            if candles and candles.close:
                closes_by_sym[sym.upper()] = list(candles.close)
        target = pick_strongest_uptrend(self.watchlist, closes_by_sym, sma_period=self.sma_period)
        if not target:
            return None, f"no {','.join(self.watchlist)} above SMA({self.sma_period})"
        return target, f"{target} above SMA({self.sma_period})"

    def _rebalance_close_signal(self, ctx: RulesContext) -> TradeSignal | None:
        in_reb, _ = is_quarterly_rebalance_window(ctx.when)
        if not in_reb or ctx.gateway is None:
            return None
        for pos in iter_long_call_positions(ctx.gateway, self.watchlist):
            limit = None
            chain = fetch_hybrid_chain(
                ctx.provider,
                pos["symbol"],
                expiration_range_days=max(self.leap_max_dte, self.spread_max_dte),
                min_delta=self.leap_min_delta,
                max_delta=self.leap_max_delta,
            )
            if chain:
                for row in normalize_contracts(getattr(chain, "contracts", []) or []):
                    if (
                        row["expiration"] == str(pos["expiration"]).replace("-", "")
                        and float(row["strike"]) == float(pos["strike"])
                    ):
                        limit = contract_mid(row)
                        break
            return close_call_signal(
                symbol=pos["symbol"],
                expiration=str(pos["expiration"]),
                strike=float(pos["strike"]),
                quantity=int(pos["qty"]),
                limit_price=limit,
                reason=f"{self.name}: quarterly rebalance close-out (defined-risk roll)",
            )
        return None

    def _leap_entry(
        self,
        ctx: RulesContext,
        sym: str,
        trend_reason: str,
    ) -> TradeSignal | None:
        chain = fetch_hybrid_chain(
            ctx.provider,
            sym,
            expiration_range_days=self.leap_max_dte,
            min_delta=self.leap_min_delta,
            max_delta=self.leap_max_delta,
        )
        contracts = getattr(chain, "contracts", None) if chain else None
        leap = pick_nearest_delta_call(
            contracts or [],
            min_dte=self.leap_min_dte,
            max_dte=self.leap_max_dte,
            target_delta=self.leap_target_delta,
            min_delta=self.leap_min_delta,
            max_delta=self.leap_max_delta,
        )
        if not leap:
            return None
        limit = contract_mid(leap)
        qty = self._size_contracts(ctx, limit)
        if qty <= 0:
            return hold_signal("3% risk budget yields 0 LEAP contracts", symbol=sym)
        return buy_call_signal(
            symbol=sym,
            expiration=str(leap["expiration"]),
            strike=float(leap["strike"]),
            quantity=qty,
            limit_price=limit,
            reason=f"{self.name}: {trend_reason}; LEAP {self.leap_min_dte}+ DTE (defined risk = premium)",
        )

    def _spread_entry(
        self,
        ctx: RulesContext,
        sym: str,
        trend_reason: str,
    ) -> TradeSignal | None:
        spot = spot_price_from_provider(ctx.provider, sym)
        width = adaptive_spread_width(spot or 100.0)
        chain = fetch_hybrid_chain(
            ctx.provider,
            sym,
            expiration_range_days=self.spread_max_dte,
            min_delta=0.30,
            max_delta=0.60,
        )
        contracts = getattr(chain, "contracts", None) if chain else None
        legs = pick_debit_call_spread(
            contracts or [],
            min_dte=self.spread_min_dte,
            max_dte=self.spread_max_dte,
            target_delta=self.spread_target_delta,
            width=width,
        )
        if not legs:
            return None
        long_leg, short_leg = legs
        long_mid = contract_mid(long_leg) or 0.0
        short_mid = contract_mid(short_leg) or 0.0
        debit = max(0.05, long_mid - short_mid)
        qty = self._size_contracts(ctx, debit)
        if qty <= 0:
            return hold_signal("3% risk budget yields 0 spread contracts", symbol=sym)
        return spread_signal(
            symbol=sym,
            expiration=str(long_leg["expiration"]),
            long_strike=float(long_leg["strike"]),
            short_strike=float(short_leg["strike"]),
            quantity=qty,
            limit_price=round(debit, 2),
            reason=(
                f"{self.name}: {trend_reason}; debit call spread "
                f"{self.spread_min_dte}-{self.spread_max_dte} DTE (max loss = debit)"
            ),
        )

    def evaluate(self, ctx: RulesContext) -> TradeSignal:
        close_sig = self._rebalance_close_signal(ctx)
        if close_sig is not None:
            return close_sig

        in_reb, reb_reason = is_quarterly_rebalance_window(ctx.when)
        if ctx.gateway and iter_long_call_positions(ctx.gateway, self.watchlist):
            if in_reb:
                return hold_signal("awaiting rebalance close before re-entry")
            return hold_signal("holding defined-risk position until quarterly rebalance")

        if not in_reb:
            return hold_signal("outside quarterly rebalance window (Jan/Apr/Jul/Oct, days 1–7)")

        sym, trend_reason = self._trend_leader(ctx)
        if not sym:
            return hold_signal(trend_reason)

        leap_sig = self._leap_entry(ctx, sym, f"{reb_reason}; {trend_reason}")
        if leap_sig is not None:
            return leap_sig

        spread_sig = self._spread_entry(ctx, sym, f"{reb_reason}; {trend_reason}")
        if spread_sig is not None:
            return spread_sig

        return hold_signal(f"no LEAP or debit spread for {sym} during rebalance", symbol=sym)


# ── 3. Defense Growth Leveraged ──────────────────────────────────────────────


@dataclass
class DefenseGrowthLeveragedThesis(QuarterlyLeveragedTrendThesis):
    """ITA/XAR LEAP or debit spread — trend filter, quarterly rebalance, 3% risk."""

    id: str = "defense_growth"
    name: str = "Defense Growth Leveraged"
    watchlist: list[str] = field(default_factory=lambda: ["ITA", "XAR"])


# ── 4. Automation Boom Leveraged ─────────────────────────────────────────────


@dataclass
class AutomationBoomLeveragedThesis(QuarterlyLeveragedTrendThesis):
    """BOTZ/ROBO LEAP or debit spread — trend filter, quarterly rebalance, 3% risk."""

    id: str = "automation_boom"
    name: str = "Automation Boom Leveraged"
    watchlist: list[str] = field(default_factory=lambda: ["BOTZ", "ROBO"])


# ── 5. Covered Call Overlay ──────────────────────────────────────────────────


@dataclass
class CoveredCallOverlayThesis(RuleThesis):
    """
    Monthly 30–45 DTE calls 3–5% OTM against core long stock (100-share lots).

    Defined-risk: covered calls only (no naked short). Roll window: days 1–7 each month.
    """

    id: str = "covered_call_overlay"
    name: str = "Covered Call Overlay"
    watchlist: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "ITA", "XAR", "BOTZ", "ROBO"])
    option_strategies: list[str] = field(default_factory=lambda: ["covered_call", "close_short_call"])
    risk_per_trade_pct: float = 3.0
    core_symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "ITA", "XAR", "BOTZ", "ROBO"])
    min_dte: int = 30
    max_dte: int = 45
    min_otm_pct: float = 3.0
    max_otm_pct: float = 5.0

    def _has_active_overlay(self, ctx: RulesContext, sym: str) -> bool:
        if ctx.gateway is None:
            return False
        for pos in iter_short_call_positions(ctx.gateway, [sym]):
            chain = fetch_hybrid_chain(
                ctx.provider,
                sym,
                expiration_range_days=self.max_dte,
                min_delta=0.10,
                max_delta=0.45,
            )
            if not chain:
                return True
            for row in normalize_contracts(getattr(chain, "contracts", []) or []):
                if (
                    row["expiration"] == str(pos["expiration"]).replace("-", "")
                    and float(row["strike"]) == float(pos["strike"])
                ):
                    dte = row.get("dte")
                    if dte is None:
                        return True
                    return self.min_dte <= int(dte) <= self.max_dte
        return False

    def _monthly_roll_close(self, ctx: RulesContext) -> TradeSignal | None:
        in_window, _ = is_monthly_overlay_window(ctx.when)
        if not in_window or ctx.gateway is None:
            return None
        for sym in self.core_symbols:
            for pos in iter_short_call_positions(ctx.gateway, [sym]):
                limit = None
                chain = fetch_hybrid_chain(
                    ctx.provider,
                    sym,
                    expiration_range_days=self.max_dte,
                    min_delta=0.10,
                    max_delta=0.45,
                )
                if chain:
                    for row in normalize_contracts(getattr(chain, "contracts", []) or []):
                        if (
                            row["expiration"] == str(pos["expiration"]).replace("-", "")
                            and float(row["strike"]) == float(pos["strike"])
                        ):
                            limit = contract_mid(row)
                            break
                return buy_to_close_call_signal(
                    symbol=pos["symbol"],
                    expiration=str(pos["expiration"]),
                    strike=float(pos["strike"]),
                    quantity=int(pos["qty"]),
                    limit_price=limit,
                    reason=f"covered call overlay: monthly roll buy-to-close on {sym}",
                )
        return None

    def evaluate(self, ctx: RulesContext) -> TradeSignal:
        roll_close = self._monthly_roll_close(ctx)
        if roll_close is not None:
            return roll_close

        in_window, window_reason = is_monthly_overlay_window(ctx.when)
        if not in_window:
            return hold_signal("outside monthly covered-call overlay window (days 1–7)")

        best: tuple[str, int, dict[str, Any], float] | None = None
        for sym in self.core_symbols:
            sym_u = sym.upper()
            shares = stock_shares(ctx.portfolio, sym_u)
            contracts_available = shares // 100
            if contracts_available < 1:
                continue
            if self._has_active_overlay(ctx, sym_u):
                continue

            spot = spot_price_from_provider(ctx.provider, sym_u)
            if spot is None:
                continue

            chain = fetch_hybrid_chain(
                ctx.provider,
                sym_u,
                expiration_range_days=self.max_dte,
                min_delta=0.15,
                max_delta=0.40,
            )
            option_contracts = getattr(chain, "contracts", None) if chain else None
            pick = pick_otm_call_by_strike_pct(
                option_contracts or [],
                spot,
                min_dte=self.min_dte,
                max_dte=self.max_dte,
                min_otm_pct=self.min_otm_pct,
                max_otm_pct=self.max_otm_pct,
            )
            if not pick:
                continue

            limit = contract_mid(pick) or 0.0
            notional_cap = int((ctx.net_liquidation * (self.risk_per_trade_pct / 100.0)) / (spot * 100.0))
            qty = min(contracts_available, max(1, notional_cap)) if notional_cap > 0 else contracts_available
            otm_pct = (float(pick["strike"]) / spot - 1.0) * 100.0
            if best is None or qty > best[1]:
                best = (sym_u, qty, pick, otm_pct)

        if not best:
            return hold_signal("no core longs eligible for 30–45 DTE 3–5% OTM covered call")

        sym, qty, pick, otm_pct = best
        limit = contract_mid(pick)
        return sell_call_signal(
            symbol=sym,
            expiration=str(pick["expiration"]),
            strike=float(pick["strike"]),
            quantity=qty,
            limit_price=limit,
            reason=(
                f"covered call overlay: {window_reason}; sell {qty}x "
                f"{self.min_dte}-{self.max_dte} DTE ~{otm_pct:.1f}% OTM vs {sym} core"
            ),
        )


# ── Registry ─────────────────────────────────────────────────────────────────

RULES_THESES: list[RuleThesis] = [
    LeveragedOvernightDriftThesis(),
    LunarOptionsSwingThesis(),
    DefenseGrowthLeveragedThesis(),
    AutomationBoomLeveragedThesis(),
    CoveredCallOverlayThesis(),
]


def active_rules_theses() -> list[RuleThesis]:
    return [t for t in RULES_THESES if t.enabled]
