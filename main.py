#!/usr/bin/env python3
"""
Thesis Trader — main entry point (read this file first).

Linear flow every cycle:
  1. Load theses from theses.py
  2. Fetch market data for the active thesis watchlist
  3. Build a focused Grok prompt from the thesis narrative + data
  4. Ask Grok for a JSON trade decision
  5. Run strict risk checks (ABC risk config + SafetyController)
  6. Execute on IBKR OR simulate locally (paper default)
  7. Log everything to logs/thesis_trader.jsonl

Usage:
  python main.py              # run all enabled theses (max 5)
  python main.py --thesis id   # run one thesis by id
  python main.py --list        # show configured theses
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from glue.bootstrap import assert_paper_mode_safe, bootstrap, execution_backend, is_local_paper_sim
from glue.fetch_data import fetch_for_thesis
from glue.paper_broker import PaperBroker
from glue.prompt_builder import build_system_prompt, build_user_prompt
from glue.risk_check import evaluate_decision, make_safety_controller
from glue.trade_logger import TradeLogger
from theses import Thesis, active_theses

logger = logging.getLogger(__name__)


# ── Step 4: Grok decision ────────────────────────────────────────────────────


async def ask_grok(
    grok,
    *,
    system_prompt: str,
    user_prompt: str,
    cost_tracker,
) -> tuple[str, dict]:
    """
    Single-turn Grok call (no ReAct loop — keeps this project easy to follow).

    Returns (raw_text, parsed_decision_dict).
    """
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
        cost_tracker.log_llm_usage(
            grok.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    objects = _parse_json_objects(raw)
    decision = objects[0] if objects else {"action": "hold", "rationale": "unparseable Grok output"}
    return raw, decision


# ── Step 6: Execution ───────────────────────────────────────────────────────


async def execute_decision(gateway, decision: dict, quantity: int) -> dict:
    """Route a approved buy/sell to the gateway (IBKR or local paper sim)."""
    action = str(decision.get("action", "hold")).lower()
    symbol = str(decision.get("symbol", "")).upper()
    if action == "hold":
        return {"status": "skipped", "reason": "hold"}
    side = "BUY" if action == "buy" else "SELL"
    result = await gateway.place_market_order(symbol, side, quantity)
    await gateway.refresh_positions()
    return {"status": "submitted", "side": side, "symbol": symbol, "quantity": quantity, "broker": result}


async def build_gateway(data_provider):
    """
    Step 0 (per cycle): connect execution backend.

    Default ``EXECUTION_BACKEND=local_sim`` needs no TWS.
    Set ``EXECUTION_BACKEND=ibkr`` to use copied IBKR execution stack.
    """
    if is_local_paper_sim():
        cash = float(__import__("os").getenv("PAPER_STARTING_CASH", "100000"))
        broker = PaperBroker(initial_cash=cash, data_provider=data_provider)
        await broker.connect()
        return broker

    from data.broker_gateway import create_gateway

    gateway = await create_gateway({"broker": {"adapter": "ibkr"}})
    return gateway


async def account_snapshot(gateway) -> dict:
    summary = await gateway.get_account_summary()
    positions = []
    for item in gateway.get_cached_portfolio():
        contract = getattr(item, "contract", item)
        positions.append(
            {
                "symbol": getattr(contract, "symbol", getattr(item, "symbol", "")),
                "qty": getattr(item, "position", 0),
                "market_value": getattr(item, "marketValue", 0),
            }
        )
    return {
        "account_id": getattr(gateway, "account_id", "?"),
        "cash": getattr(gateway, "cash_value", 0),
        "net_liquidation": getattr(gateway, "net_liquidation", 0),
        "backend": execution_backend(),
        "positions": positions,
    }


# ── One full thesis cycle ─────────────────────────────────────────────────────


async def run_thesis_cycle(
    thesis: Thesis,
    *,
    grok,
    data_provider,
    cost_tracker,
    trade_log: TradeLogger,
) -> None:
    """Run steps 2–7 for a single thesis."""
    logger.info("=" * 60)
    logger.info("Running thesis: %s (%s)", thesis.name, thesis.id)
    trade_log.log_cycle_start(thesis.id, thesis.watchlist)

    # 2) Fetch data
    market_data = fetch_for_thesis(thesis, data_provider)
    trade_log.log_market_data(thesis.id, market_data)

    # Connect broker / paper sim for account context + execution
    gateway = await build_gateway(data_provider)
    safety = make_safety_controller(gateway, cost_tracker)
    acct = await account_snapshot(gateway)

    # 3) Build prompt
    from core.risk_execution_config import get_risk_execution_config

    risk = get_risk_execution_config()
    system_prompt = build_system_prompt(trading_mode=risk.trading_mode, cash_only=risk.cash_only)
    user_prompt = build_user_prompt(thesis, market_data, acct)

    # 4) Grok decision
    raw, decision = await ask_grok(
        grok,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        cost_tracker=cost_tracker,
    )
    trade_log.log_grok_raw(thesis.id, raw)
    trade_log.log_decision(thesis.id, decision)
    logger.info("Grok decision: %s", decision)

    # 5) Risk check
    verdict = evaluate_decision(
        decision,
        thesis_watchlist=thesis.watchlist,
        gateway=gateway,
        data_provider=data_provider,
        safety=safety,
    )
    trade_log.log_risk(thesis.id, verdict)
    if not verdict.approved:
        logger.warning("Risk REJECTED: %s", verdict.reason)
        await gateway.disconnect()
        return
    if str(decision.get("action", "hold")).lower() == "hold":
        logger.info("Grok chose hold — cycle complete.")
        await gateway.disconnect()
        return

    qty = verdict.adjusted_quantity or int(decision.get("quantity") or 0)

    # 6) Execute
    exec_result = await execute_decision(gateway, decision, qty)
    trade_log.log_execution(thesis.id, exec_result)
    logger.info("Execution result: %s", exec_result)

    await gateway.disconnect()


# ── CLI entry ─────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thesis-driven Grok trader (paper by default)")
    parser.add_argument("--thesis", help="Run a single thesis id")
    parser.add_argument("--list", action="store_true", help="List configured theses")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args()


async def async_main() -> int:
    args = _parse_args()
    bootstrap(verbose=args.verbose)

    from core.grok_llm import get_grok_llm
    from core.log_context import get_logger
    from data.cost_tracker import get_cost_tracker
    from data.data_provider import get_data_provider

    global logger
    logger = get_logger(__name__)

    assert_paper_mode_safe()

    if args.list:
        for t in active_theses():
            print(f"  {t.id:20}  {t.name}  watchlist={','.join(t.watchlist)}")
        if not active_theses():
            print("  (no enabled theses — edit theses.py)")
        return 0

    theses = active_theses()
    if args.thesis:
        theses = [t for t in theses if t.id == args.thesis]
        if not theses:
            logger.error("No enabled thesis with id %r", args.thesis)
            return 1

    if not theses:
        logger.error(
            "No enabled theses. Open theses.py, add 1–5 strategies, and set enabled=True."
        )
        return 1
    if len(theses) > 5:
        logger.error("Too many enabled theses (%d). Disable extras in theses.py (max 5).", len(theses))
        return 1

    grok = get_grok_llm()
    data_provider = get_data_provider()
    cost_tracker = get_cost_tracker()
    trade_log = TradeLogger()

    # 1) Load theses — done above
    for thesis in theses:
        await run_thesis_cycle(
            thesis,
            grok=grok,
            data_provider=data_provider,
            cost_tracker=cost_tracker,
            trade_log=trade_log,
        )

    logger.info("All thesis cycles complete. See logs/thesis_trader.jsonl")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
