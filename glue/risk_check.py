"""
Risk checks before any order is sent.

Uses core.risk_execution_config limits and SafetyController guardrails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.config import CASH_ONLY, MIN_RR_RATIO, RISK_PER_TRADE
from core.risk_execution_config import get_risk_execution_config
from core.runtime.safety import SafetyController
from glue.executor import is_option_decision
from glue.options import validate_option_decision
from theses import Thesis

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    adjusted_quantity: int | None = None


def _portfolio_qty(gateway: Any, symbol: str) -> int:
    sym = symbol.upper()
    for item in gateway.get_cached_portfolio() if gateway else []:
        contract = getattr(item, "contract", item)
        item_sym = getattr(contract, "symbol", getattr(item, "symbol", "")).upper()
        if item_sym == sym:
            return int(getattr(item, "position", 0))
    return 0


def _estimate_max_shares(gateway: Any, data_provider: Any, symbol: str) -> int:
    net_liq = float(getattr(gateway, "net_liquidation", 0) or 0)
    cash = float(getattr(gateway, "cash_value", 0) or 0)
    if net_liq <= 0:
        return 0

    quote = data_provider.get_quote(symbol)
    price = float(getattr(quote, "last", 0) or getattr(quote, "mid", 0) or 0) if quote else 0
    if price <= 0:
        return 0

    atr_result = data_provider.get_atr(symbol)
    atr_pct = (float(atr_result.value) / price * 100) if atr_result and atr_result.value else 5.0
    stop_distance_pct = round(min(atr_pct * 1.5, 15.0), 2)

    risk_dollars = net_liq * RISK_PER_TRADE
    stop_loss_per_share = price * (stop_distance_pct / 100)
    risk_shares = int(risk_dollars / stop_loss_per_share) if stop_loss_per_share > 0 else 0

    max_position_dollars = net_liq * 0.20
    existing = 0.0
    for item in gateway.get_cached_portfolio():
        contract = getattr(item, "contract", item)
        if getattr(contract, "symbol", "").upper() == symbol.upper():
            existing += abs(float(getattr(item, "marketValue", 0) or 0))
    remaining = max(0.0, max_position_dollars - existing)
    concentration_shares = int(remaining / price) if price > 0 else 0
    cash_shares = int(cash / price) if price > 0 else 0
    return max(0, min(risk_shares, concentration_shares, cash_shares))


def _estimate_max_option_contracts(gateway: Any, decision: dict[str, Any]) -> int:
    """Cap option size by cash (premium * 100 * contracts)."""
    cash = float(getattr(gateway, "cash_value", 0) or 0)
    opt = decision.get("option") or {}
    limit = opt.get("limit_price")
    # Conservative default premium estimate if Grok omits limit
    premium = float(limit) if limit is not None else 3.0
    cost_per_contract = premium * 100
    if cost_per_contract <= 0:
        return 0
    net_liq = float(getattr(gateway, "net_liquidation", 0) or 0)
    risk_budget = net_liq * RISK_PER_TRADE
    by_risk = int(risk_budget / cost_per_contract)
    by_cash = int(cash / cost_per_contract)
    return max(0, min(by_risk, by_cash, 10))  # hard cap 10 contracts in thesis mode


def evaluate_decision(
    decision: dict[str, Any],
    *,
    thesis: Thesis,
    gateway: Any,
    data_provider: Any,
    safety: SafetyController,
) -> RiskVerdict:
    action = str(decision.get("action", "hold")).lower()
    if action == "hold":
        return RiskVerdict(True, "hold — no order required")

    symbol = str(decision.get("symbol", "")).upper().strip()
    if not symbol:
        return RiskVerdict(False, "missing symbol")
    if symbol not in {s.upper() for s in thesis.watchlist}:
        return RiskVerdict(False, f"{symbol} not on thesis watchlist")

    safety.capture_start_of_day_cash()
    safety_verdict = safety.evaluate()
    if safety_verdict.triggered:
        return RiskVerdict(False, f"safety halt: {safety_verdict.reason}")

    try:
        stop_pct = float(decision.get("stop_loss_pct") or 0)
        target_pct = float(decision.get("take_profit_pct") or 0)
    except (TypeError, ValueError):
        stop_pct = target_pct = 0.0
    if stop_pct > 0 and target_pct > 0:
        rr = target_pct / stop_pct
        if rr < MIN_RR_RATIO:
            return RiskVerdict(False, f"reward:risk {rr:.2f} below min {MIN_RR_RATIO:.2f}")

    try:
        qty = int(decision.get("quantity") or 0)
    except (TypeError, ValueError):
        return RiskVerdict(False, "quantity must be an integer")
    if qty <= 0:
        return RiskVerdict(False, "quantity must be positive for buy/sell")

    # ── Options path ─────────────────────────────────────────────────────
    if is_option_decision(decision):
        if "option" not in thesis.instruments:
            return RiskVerdict(False, "this thesis does not allow options")
        ok, msg = validate_option_decision(decision, allowed_strategies=thesis.option_strategies)
        if not ok:
            return RiskVerdict(False, msg)
        opt = decision.get("option") or {}
        if str(opt.get("strategy")) == "close_option":
            if action != "sell":
                return RiskVerdict(False, "close_option requires action=sell")
        elif str(opt.get("strategy")) == "covered_call":
            if action != "sell":
                return RiskVerdict(False, "covered_call requires action=sell")
            shares = _portfolio_qty(gateway, symbol)
            if shares < qty * 100:
                return RiskVerdict(
                    False,
                    f"covered_call needs {qty * 100} shares of {symbol} (hold {shares})",
                )
        elif str(opt.get("strategy")) == "close_short_call":
            if action != "buy":
                return RiskVerdict(False, "close_short_call requires action=buy")
        elif action != "buy":
            return RiskVerdict(False, "opening option strategies require action=buy")
        max_c = _estimate_max_option_contracts(gateway, decision)
        if qty > max_c:
            if max_c <= 0:
                return RiskVerdict(False, "insufficient cash/risk budget for option premium")
            logger.warning("Capping option contracts %d -> %d", qty, max_c)
            qty = max_c
        return RiskVerdict(True, "approved", adjusted_quantity=qty)

    # ── Stock path ───────────────────────────────────────────────────────
    if "stock" not in thesis.instruments:
        return RiskVerdict(False, "this thesis does not allow stock orders")

    side = "BUY" if action == "buy" else "SELL" if action == "sell" else ""
    if not side:
        return RiskVerdict(False, f"unknown action {action!r}")

    if CASH_ONLY and side == "SELL" and _portfolio_qty(gateway, symbol) < qty:
        return RiskVerdict(
            False,
            f"cash-only: cannot sell {qty} {symbol} (hold {_portfolio_qty(gateway, symbol)})",
        )

    if side == "BUY":
        max_shares = _estimate_max_shares(gateway, data_provider, symbol)
        if qty > max_shares:
            if max_shares <= 0:
                return RiskVerdict(False, "sizing blocked under risk/cash limits")
            logger.warning("Capping shares %d -> %d", qty, max_shares)
            qty = max_shares

    risk = get_risk_execution_config()
    logger.info(
        "Risk OK: %s %d %s (mode=%s)",
        side,
        qty,
        symbol,
        risk.trading_mode,
    )
    return RiskVerdict(True, "approved", adjusted_quantity=qty)


def make_safety_controller(gateway: Any, cost_tracker: Any) -> SafetyController:
    risk = get_risk_execution_config()
    return SafetyController(
        gateway,
        cost_tracker,
        max_daily_loss_pct=float(risk.max_daily_loss_pct),
        intraday_drawdown_pct=float(risk.intraday_drawdown_pct),
        max_daily_llm_cost=float(risk.max_daily_llm_cost),
    )
