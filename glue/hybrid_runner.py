"""
Hybrid cycle — mechanical reference signals + Grok rule interpretation (default).

``python main.py`` runs this path automatically.
"""

from __future__ import annotations

import logging
from typing import Any

from glue.executor import execute_decision
from glue.fetch_data import fetch_for_portfolio
from glue.global_risk import GlobalRiskGate
from glue.portfolio_manager import PortfolioManager
from glue.prompt_builder import (
    build_hybrid_system_prompt,
    build_hybrid_user_prompt,
    parse_hybrid_decisions,
)
from glue.risk_check import evaluate_decision, make_safety_controller
from glue.rules_runner import account_snapshot, build_gateway, build_rules_context
from theses import Thesis
from theses_rules.config_bridge import rules_from_theses

logger = logging.getLogger(__name__)


async def ask_grok_hybrid(grok, *, system_prompt: str, user_prompt: str, cost_tracker) -> tuple[str, dict]:
    from xai_sdk.chat import system as sdk_system
    from xai_sdk.chat import user as sdk_user

    from core.json_parse import _parse_json_objects
    from core.prompt_config import get_prompt_config

    pc = get_prompt_config()
    chat = grok.client.chat.create(
        model=grok.model,
        messages=[sdk_system(system_prompt), sdk_user(user_prompt)],
        temperature=pc.llm_temperature,
        max_tokens=pc.llm_max_tokens,
        seed=pc.llm_seed,
    )
    response = await chat.sample()
    raw = response.content or ""

    usage = getattr(response, "usage", None)
    if usage is not None:
        cost_tracker.log_llm_usage(grok.model, usage=usage)

    objects = _parse_json_objects(raw)
    parsed = objects[0] if objects else {"decisions": [], "portfolio_rationale": "unparseable Grok output"}
    return raw, parsed


def _thesis_map(theses: list[Thesis]) -> dict[str, Thesis]:
    return {t.id: t for t in theses}


def _cap_quantity_by_allocation(
    decision: dict[str, Any],
    thesis: Thesis,
    gateway: Any,
) -> dict[str, Any]:
    """Cap Grok quantity by thesis max_allocation_pct vs NLV."""
    nlv = float(getattr(gateway, "net_liquidation", 0) or 0)
    if nlv <= 0:
        return decision
    opt = decision.get("option") or {}
    premium = opt.get("limit_price")
    if premium is None:
        return decision
    try:
        premium = float(premium)
    except (TypeError, ValueError):
        return decision
    if premium <= 0:
        return decision
    budget = nlv * (thesis.max_allocation_pct / 100.0)
    max_qty = max(0, int(budget / (premium * 100.0)))
    try:
        qty = int(decision.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty > max_qty:
        decision = dict(decision)
        decision["quantity"] = max_qty
    return decision


async def run_hybrid_cycle(
    theses: list[Thesis],
    *,
    grok: Any,
    data_provider: Any,
    cost_tracker: Any,
    trade_log: Any,
    execute: bool = True,
) -> dict[str, Any]:
    """
    Default thesis trader cycle:
      1. Mechanical reference signals (all theses)
      2. Global risk gates
      3. Grok interprets rules → allocation JSON
      4. Risk check + execute each decision
    """
    logger.info("=" * 60)
    logger.info("Hybrid cycle — %d theses", len(theses))
    trade_log.log_event("hybrid_cycle_start", {"thesis_ids": [t.id for t in theses]})

    rule_objs = rules_from_theses(theses)
    tmap = _thesis_map(theses)
    gateway = await build_gateway(data_provider)
    manager = PortfolioManager(rule_objs, global_risk=GlobalRiskGate())

    ctx = await build_rules_context(gateway, data_provider)
    plan = manager.build_plan(ctx, gateway, data_provider=data_provider)

    for thesis_id, sig in plan.raw_signals:
        trade_log.log_event("rules_signal", {"thesis_id": thesis_id, "signal": sig})

    mechanical_signals = [
        {"thesis_id": tid, "signal": sig} for tid, sig in plan.raw_signals
    ]
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

    market_data = fetch_for_portfolio(theses, data_provider)
    trade_log.log_event("market_data", {"data": market_data})
    acct = await account_snapshot(gateway)

    from core.risk_execution_config import get_risk_execution_config

    risk_cfg = get_risk_execution_config()
    global_risk_dict = None
    if plan.global_risk is not None:
        global_risk_dict = {
            "drawdown_pct": plan.global_risk.drawdown_pct,
            "cash_pct": plan.global_risk.cash_pct,
            "premium_exposure_pct": plan.global_risk.premium_exposure_pct,
            "block_new_premium": plan.global_risk.block_new_premium,
            "reason": plan.global_risk.reason,
        }

    system_prompt = build_hybrid_system_prompt(
        theses=theses,
        trading_mode=risk_cfg.trading_mode,
        global_risk=global_risk_dict,
    )
    user_prompt = build_hybrid_user_prompt(
        theses=theses,
        market_data=market_data,
        mechanical_signals=mechanical_signals,
        account_snapshot=acct,
    )

    raw, parsed = await ask_grok_hybrid(
        grok,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        cost_tracker=cost_tracker,
    )
    trade_log.log_event("grok_raw", {"raw": raw})
    trade_log.log_event("grok_hybrid", {"parsed": parsed})

    decisions = parse_hybrid_decisions(parsed)
    logger.info("Grok hybrid decisions: %d", len(decisions))

    if not execute:
        await gateway.disconnect()
        return {"decisions": decisions, "mechanical": mechanical_signals, "raw": raw}

    safety = make_safety_controller(gateway, cost_tracker)
    executed = 0
    for decision in decisions:
        thesis_id = str(decision.get("thesis_id", "")).strip()
        thesis = tmap.get(thesis_id)
        if thesis is None:
            logger.warning("Unknown thesis_id in Grok decision: %r", thesis_id)
            continue

        action = str(decision.get("action", "hold")).lower()
        trade_log.log_event("grok_decision", {"thesis_id": thesis_id, "decision": decision})
        if action == "hold":
            continue

        if plan.global_risk and plan.global_risk.block_new_premium:
            opt = decision.get("option") or {}
            strategy = str(opt.get("strategy", "")).lower()
            if action == "buy" and strategy in ("long_call", "long_put", "vertical_spread", ""):
                trade_log.log_event(
                    "signal_blocked",
                    {"thesis_id": thesis_id, "decision": decision, "reason": plan.global_risk.reason},
                )
                logger.warning("Global gate blocked %s: %s", thesis_id, plan.global_risk.reason)
                continue

        decision = _cap_quantity_by_allocation(decision, thesis, gateway)
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
            continue

        qty = verdict.adjusted_quantity or int(decision.get("quantity") or 0)
        if qty <= 0:
            continue
        exec_result = await execute_decision(gateway, decision, qty)
        trade_log.log_execution(thesis_id, exec_result)
        logger.info("Executed [%s]: %s", thesis_id, exec_result)
        executed += 1
        await gateway.refresh_positions()
        manager.global_risk.evaluate(gateway, data_provider=data_provider)

    await gateway.disconnect()
    trade_log.log_event("hybrid_cycle_complete", {"executed": executed, "decisions": len(decisions)})
    return {"decisions": decisions, "executed": executed, "raw": raw}
