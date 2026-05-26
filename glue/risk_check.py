"""
Risk checks before any order is sent.

Reuses ABC's :mod:`core.risk_execution_config` limits and
:class:`core.runtime.safety.SafetyController` guardrails. Sizing math mirrors
``tools.tools_sizing`` (risk-per-trade, concentration, cash-only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.config import CASH_ONLY, MIN_RR_RATIO, RISK_PER_TRADE
from core.risk_execution_config import get_risk_execution_config
from core.runtime.safety import SafetyController

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
    """Risk-based sizing (simplified from tools_sizing.handle_calculate_size)."""
    net_liq = float(getattr(gateway, "net_liquidation", 0) or 0)
    cash = float(getattr(gateway, "cash_value", 0) or 0)
    if net_liq <= 0:
        return 0

    quote = data_provider.get_quote(symbol)
    if quote is None:
        return 0
    price = float(getattr(quote, "last", 0) or getattr(quote, "mid", 0) or 0)
    if price <= 0:
        return 0

    atr_result = data_provider.get_atr(symbol)
    atr_pct = None
    if atr_result and getattr(atr_result, "value", None):
        atr_pct = float(atr_result.value) / price * 100

    stop_distance_pct = round(min((atr_pct or 5.0) * 1.5, 15.0), 2)
    risk_per_trade_pct = RISK_PER_TRADE * 100
    risk_dollars = net_liq * (risk_per_trade_pct / 100)
    stop_loss_per_share = price * (stop_distance_pct / 100)
    risk_shares = int(risk_dollars / stop_loss_per_share) if stop_loss_per_share > 0 else 0

    max_position_pct = 20.0
    max_position_dollars = net_liq * (max_position_pct / 100)
    existing = 0.0
    for item in gateway.get_cached_portfolio():
        contract = getattr(item, "contract", item)
        item_sym = getattr(contract, "symbol", getattr(item, "symbol", "")).upper()
        if item_sym == symbol.upper():
            existing += abs(float(getattr(item, "marketValue", 0) or 0))
    remaining = max(0.0, max_position_dollars - existing)
    concentration_shares = int(remaining / price) if price > 0 else 0

    cash_shares = int(cash / price) if price > 0 else 0
    return max(0, min(risk_shares, concentration_shares, cash_shares))


def evaluate_decision(
    decision: dict[str, Any],
    *,
    thesis_watchlist: list[str],
    gateway: Any,
    data_provider: Any,
    safety: SafetyController,
) -> RiskVerdict:
    """
    Run safety rails + thesis constraints + sizing on a parsed Grok decision.

    Returns ``RiskVerdict(approved=False, ...)`` with a human-readable reason
    when anything fails.
    """
    action = str(decision.get("action", "hold")).lower()
    if action == "hold":
        return RiskVerdict(True, "hold — no order required")

    symbol = str(decision.get("symbol", "")).upper().strip()
    if not symbol:
        return RiskVerdict(False, "missing symbol")
    if symbol not in {s.upper() for s in thesis_watchlist}:
        return RiskVerdict(False, f"{symbol} is not on this thesis watchlist")

    side = "BUY" if action == "buy" else "SELL" if action == "sell" else ""
    if not side:
        return RiskVerdict(False, f"unknown action {action!r}")

    try:
        qty = int(decision.get("quantity") or 0)
    except (TypeError, ValueError):
        return RiskVerdict(False, "quantity must be an integer")
    if qty <= 0:
        return RiskVerdict(False, "quantity must be positive for buy/sell")

    # ── SafetyController (daily loss, drawdown, LLM spend) ─────────────
    safety.capture_start_of_day_cash()
    verdict = safety.evaluate()
    if verdict.triggered:
        return RiskVerdict(False, f"safety halt: {verdict.reason}")

    # ── Reward:risk from Grok's stop/target hints ────────────────────────
    try:
        stop_pct = float(decision.get("stop_loss_pct") or 0)
        target_pct = float(decision.get("take_profit_pct") or 0)
    except (TypeError, ValueError):
        stop_pct = target_pct = 0.0
    if stop_pct > 0 and target_pct > 0:
        rr = target_pct / stop_pct
        if rr < MIN_RR_RATIO:
            return RiskVerdict(
                False,
                f"reward:risk {rr:.2f} below minimum {MIN_RR_RATIO:.2f}",
            )

    # ── Cash-only guards ─────────────────────────────────────────────────
    if CASH_ONLY and side == "SELL" and _portfolio_qty(gateway, symbol) < qty:
        return RiskVerdict(
            False,
            f"cash-only: cannot sell {qty} {symbol} — only hold {_portfolio_qty(gateway, symbol)}",
        )

    max_shares = _estimate_max_shares(gateway, data_provider, symbol)
    if side == "BUY" and qty > max_shares:
        if max_shares <= 0:
            return RiskVerdict(False, "sizing blocked this entry (no room under risk/cash limits)")
        logger.warning("Capping quantity %d → %d under risk limits", qty, max_shares)
        qty = max_shares

    risk = get_risk_execution_config()
    logger.info(
        "Risk OK: %s %d %s (mode=%s, risk/trade=%.2f%%, min_rr=%.2f)",
        side,
        qty,
        symbol,
        risk.trading_mode,
        RISK_PER_TRADE * 100,
        MIN_RR_RATIO,
    )
    return RiskVerdict(True, "approved", adjusted_quantity=qty)


def make_safety_controller(gateway: Any, cost_tracker: Any) -> SafetyController:
    """Build SafetyController from risk config (no Postgres / profit profiles)."""
    risk = get_risk_execution_config()
    return SafetyController(
        gateway,
        cost_tracker,
        max_daily_loss_pct=float(risk.max_daily_loss_pct),
        intraday_drawdown_pct=float(risk.intraday_drawdown_pct),
        max_daily_llm_cost=float(risk.max_daily_llm_cost),
    )
