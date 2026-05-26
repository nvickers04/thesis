"""
Run mechanical rule theses via PortfolioManager — no Grok LLM call.

``run_portfolio_cycle`` evaluates all theses, aggregates signals, applies global
risk gates, and executes approved orders through one shared gateway.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from glue.executor import execute_decision
from glue.global_risk import GlobalRiskGate
from glue.portfolio_manager import PortfolioManager, PortfolioPlan
from glue.risk_check import evaluate_decision, make_safety_controller
from theses_rules.rules_based_theses import (
    RuleThesis,
    RulesContext,
    TradeSignal,
    signal_to_execution_decision,
)

logger = logging.getLogger(__name__)


async def account_snapshot(gateway: Any) -> dict:
    positions = []
    for item in gateway.get_cached_portfolio():
        contract = getattr(item, "contract", item)
        positions.append(
            {
                "symbol": getattr(contract, "symbol", getattr(item, "symbol", "")),
                "sec_type": getattr(contract, "secType", "STK"),
                "qty": getattr(item, "position", 0),
                "market_value": getattr(item, "marketValue", 0),
            }
        )
    return {
        "account_id": getattr(gateway, "account_id", "?"),
        "cash": getattr(gateway, "cash_value", 0),
        "net_liquidation": getattr(gateway, "net_liquidation", 0),
        "positions": positions,
    }


async def build_gateway(data_provider: Any) -> Any:
    from glue.bootstrap import is_local_paper_sim
    from glue.paper_broker import PaperBroker

    if is_local_paper_sim():
        import os

        cash = float(os.getenv("PAPER_STARTING_CASH", "100000"))
        broker = PaperBroker(initial_cash=cash, data_provider=data_provider)
        await broker.connect()
        return broker

    from data.broker_gateway import create_gateway

    return await create_gateway({"broker": {"adapter": "ibkr"}})


async def build_rules_context(
    gateway: Any,
    data_provider: Any,
    *,
    as_of: datetime | None = None,
) -> RulesContext:
    portfolio = gateway.get_cached_portfolio()
    await gateway.refresh_positions()
    acct = await account_snapshot(gateway)
    when = as_of or datetime.now(timezone.utc)
    return RulesContext(
        provider=data_provider,
        when=when,
        portfolio=portfolio,
        gateway=gateway,
        net_liquidation=float(acct.get("net_liquidation") or 0),
        cash=float(acct.get("cash") or 0),
    )


async def execute_portfolio_plan(
    plan: PortfolioPlan,
    *,
    manager: PortfolioManager,
    gateway: Any,
    data_provider: Any,
    cost_tracker: Any,
    trade_log: Any,
    execute: bool = True,
    thesis_by_id: dict[str, RuleThesis] | None = None,
) -> list[tuple[str, TradeSignal, dict[str, Any] | None]]:
    """Execute approved signals in plan order. Returns (thesis_id, signal, exec_result)."""
    thesis_map = thesis_by_id or {t.id: t for t in manager.theses}
    results: list[tuple[str, TradeSignal, dict[str, Any] | None]] = []

    if plan.global_risk is not None:
        trade_log.log_event(
            "global_risk",
            {
                "drawdown_pct": plan.global_risk.drawdown_pct,
                "cash_pct": plan.global_risk.cash_pct,
                "premium_exposure_pct": plan.global_risk.premium_exposure_pct,
                "block_new_premium": plan.global_risk.block_new_premium,
                "reason": plan.global_risk.reason,
            },
        )

    for thesis_id, sig in plan.raw_signals:
        trade_log.log_event("rules_signal", {"thesis_id": thesis_id, "signal": sig})

    for thesis_id, sig, reason in plan.blocked_signals:
        trade_log.log_event(
            "signal_blocked",
            {"thesis_id": thesis_id, "signal": sig, "reason": reason},
        )

    trade_log.log_event(
        "portfolio_plan",
        {
            "approved": len(plan.approved_signals),
            "blocked": len(plan.blocked_signals),
            "actionable": len(plan.actionable),
        },
    )

    if not execute:
        for thesis_id, sig in plan.actionable:
            results.append((thesis_id, sig, None))
        return results

    safety = make_safety_controller(gateway, cost_tracker)
    for thesis_id, sig in plan.actionable:
        thesis = thesis_map.get(thesis_id)
        if thesis is None:
            continue
        decision = manager.signal_to_decision(thesis_id, sig)
        trade_log.log_event("rules_decision", {"thesis_id": thesis_id, "decision": decision})

        verdict = evaluate_decision(
            decision,
            thesis=thesis,
            gateway=gateway,
            data_provider=data_provider,
            safety=safety,
        )
        trade_log.log_risk(thesis_id, verdict)
        if not verdict.approved:
            logger.warning("Risk REJECTED [%s]: %s", thesis_id, verdict.reason)
            results.append((thesis_id, sig, {"status": "rejected", "reason": verdict.reason}))
            continue

        qty = verdict.adjusted_quantity or int(decision.get("quantity") or 0)
        exec_result = await execute_decision(gateway, decision, qty)
        trade_log.log_execution(thesis_id, exec_result)
        logger.info("Executed [%s]: %s", thesis_id, exec_result)
        results.append((thesis_id, sig, exec_result))
        await gateway.refresh_positions()
        manager.global_risk.evaluate(gateway, data_provider=data_provider)

    return results


async def run_portfolio_cycle(
    theses: list[RuleThesis],
    *,
    data_provider: Any,
    cost_tracker: Any,
    trade_log: Any,
    gateway: Any | None = None,
    execute: bool = True,
    global_risk: GlobalRiskGate | None = None,
) -> PortfolioPlan:
    """
    Evaluate all rule theses, aggregate via PortfolioManager, apply global gates, execute.
    """
    logger.info("=" * 60)
    logger.info("Rules portfolio cycle — %d theses", len(theses))
    trade_log.log_event("rules_cycle_start", {"thesis_ids": [t.id for t in theses]})

    own_gateway = gateway is None
    if gateway is None:
        gateway = await build_gateway(data_provider)

    manager = PortfolioManager(theses, global_risk=global_risk or GlobalRiskGate())
    ctx = await build_rules_context(gateway, data_provider)
    plan = manager.build_plan(ctx, gateway, data_provider=data_provider)

    await execute_portfolio_plan(
        plan,
        manager=manager,
        gateway=gateway,
        data_provider=data_provider,
        cost_tracker=cost_tracker,
        trade_log=trade_log,
        execute=execute,
    )

    if own_gateway:
        await gateway.disconnect()

    return plan


async def run_rules_thesis_cycle(
    thesis: RuleThesis,
    *,
    data_provider: Any,
    cost_tracker: Any,
    trade_log: Any,
    gateway: Any | None = None,
    execute: bool = True,
) -> TradeSignal:
    """Evaluate a single thesis (legacy) — delegates to portfolio cycle."""
    plan = await run_portfolio_cycle(
        [thesis],
        data_provider=data_provider,
        cost_tracker=cost_tracker,
        trade_log=trade_log,
        gateway=gateway,
        execute=execute,
    )
    for tid, sig in plan.raw_signals:
        if tid == thesis.id:
            return sig
    return {"action": "hold", "symbol": "", "option_contract": None, "quantity": 0, "limit_price": None, "reason": "no signal"}
