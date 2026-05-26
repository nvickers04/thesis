#!/usr/bin/env python3
"""
Thesis Trader — main entry point.

Default (``python main.py``):
  Hybrid mode — mechanical reference signals + Grok rule interpretation
  for all five configured theses → global risk gates → execute → JSONL log.

Hidden fallback: ``--legacy-llm`` for original per-thesis Grok-only flow.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from glue.bootstrap import assert_paper_mode_safe, bootstrap, execution_backend, is_local_paper_sim
from glue.executor import execute_decision
from glue.fetch_data import fetch_for_thesis
from glue.ibkr_streams import ensure_ibkr_streams
from glue.paper_broker import PaperBroker
from glue.prompt_builder import build_system_prompt, build_user_prompt
from glue.risk_check import evaluate_decision, make_safety_controller
from glue.trade_logger import TradeLogger
from theses import Thesis, active_theses

logger = logging.getLogger(__name__)


async def ask_grok(grok, *, system_prompt: str, user_prompt: str, cost_tracker) -> tuple[str, dict]:
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


async def build_gateway(data_provider):
    if is_local_paper_sim():
        import os

        cash = float(os.getenv("PAPER_STARTING_CASH", "100000"))
        broker = PaperBroker(initial_cash=cash, data_provider=data_provider)
        await broker.connect()
        return broker

    from data.broker_gateway import create_gateway

    return await create_gateway({"broker": {"adapter": "ibkr"}})


async def account_snapshot(gateway) -> dict:
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
        "backend": execution_backend(),
        "positions": positions,
    }


async def run_legacy_thesis_cycle(
    thesis: Thesis,
    *,
    grok,
    data_provider,
    cost_tracker,
    trade_log: TradeLogger,
) -> None:
    """Hidden legacy LLM-only path (no mechanical reference layer)."""
    logger.info("Running legacy thesis: %s (%s)", thesis.name, thesis.id)
    trade_log.log_cycle_start(thesis.id, thesis.watchlist)

    stream_info = await ensure_ibkr_streams(thesis.watchlist)
    trade_log.log_event("ibkr_streams", {"thesis_id": thesis.id, **stream_info})

    market_data = fetch_for_thesis(thesis, data_provider)
    trade_log.log_market_data(thesis.id, market_data)

    gateway = await build_gateway(data_provider)
    safety = make_safety_controller(gateway, cost_tracker)
    acct = await account_snapshot(gateway)

    from core.risk_execution_config import get_risk_execution_config

    risk = get_risk_execution_config()
    system_prompt = build_system_prompt(
        thesis=thesis,
        trading_mode=risk.trading_mode,
        cash_only=risk.cash_only,
    )
    user_prompt = build_user_prompt(thesis, market_data, acct)

    raw, decision = await ask_grok(
        grok,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        cost_tracker=cost_tracker,
    )
    trade_log.log_grok_raw(thesis.id, raw)
    trade_log.log_decision(thesis.id, decision)

    verdict = evaluate_decision(
        decision,
        thesis=thesis,
        gateway=gateway,
        data_provider=data_provider,
        safety=safety,
    )
    trade_log.log_risk(thesis.id, verdict)
    if not verdict.approved:
        await gateway.disconnect()
        return
    if str(decision.get("action", "hold")).lower() == "hold":
        await gateway.disconnect()
        return

    qty = verdict.adjusted_quantity or int(decision.get("quantity") or 0)
    exec_result = await execute_decision(gateway, decision, qty)
    trade_log.log_execution(thesis.id, exec_result)
    await gateway.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thesis trader — hybrid rules + Grok (default)")
    parser.add_argument("--thesis", help="Run a single thesis id")
    parser.add_argument("--list", action="store_true", help="List configured theses")
    parser.add_argument(
        "--legacy-llm",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="2023-present mechanical backtest simulation",
    )
    parser.add_argument(
        "--backtest-daily",
        action="store_true",
        help="Backtest every business day (default: weekly)",
    )
    parser.add_argument(
        "--signals-only",
        action="store_true",
        help="Evaluate rules + Grok; log only (no execution)",
    )
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

    theses = active_theses()

    if args.list:
        for t in theses:
            print(
                f"  {t.id:22}  {t.name}  "
                f"risk={t.base_risk_pct}% alloc={t.max_allocation_pct}%  "
                f"watchlist={','.join(t.watchlist)}"
            )
        if not theses:
            print("  (no enabled theses)")
        return 0

    if args.thesis:
        theses = [t for t in theses if t.id == args.thesis]
        if not theses:
            logger.error("No enabled thesis with id %r", args.thesis)
            return 1

    if not theses:
        logger.error("No enabled theses in theses.py")
        return 1

    data_provider = get_data_provider()
    cost_tracker = get_cost_tracker()
    trade_log = TradeLogger()

    if args.backtest:
        from glue.backtest_runner import run_backtest
        from theses_rules.config_bridge import rules_from_theses

        import os

        summary = await run_backtest(
            rules_from_theses(theses),
            data_provider=data_provider,
            cost_tracker=cost_tracker,
            trade_log=trade_log,
            daily=args.backtest_daily,
            starting_cash=float(os.getenv("PAPER_STARTING_CASH", "100000")),
        )
        logger.info("Backtest complete: %s", summary)
        return 0

    if args.legacy_llm:
        grok = get_grok_llm()
        for thesis in theses:
            await run_legacy_thesis_cycle(
                thesis,
                grok=grok,
                data_provider=data_provider,
                cost_tracker=cost_tracker,
                trade_log=trade_log,
            )
        logger.info("Legacy LLM done. Audit log: logs/thesis_trader.jsonl")
        return 0

    from glue.hybrid_runner import run_hybrid_cycle

    grok = get_grok_llm()
    await run_hybrid_cycle(
        theses,
        grok=grok,
        data_provider=data_provider,
        cost_tracker=cost_tracker,
        trade_log=trade_log,
        execute=not args.signals_only,
    )
    logger.info("Done. Audit log: logs/thesis_trader.jsonl")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
