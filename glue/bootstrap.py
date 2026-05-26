"""
Project bootstrap — load .env, configure logging, validate mode flags.

Call ``bootstrap()`` once at the top of ``main.py`` before anything else runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the thesis project root is on sys.path when running `python main.py`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def bootstrap(*, verbose: bool = False) -> None:
    """Load environment variables and configure structured logging."""
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except ImportError:
        pass

    from core.log_setup import configure_root_logging

    configure_root_logging("thesis_trader.log", verbose=verbose)


def project_root() -> Path:
    return _ROOT


def logs_dir() -> Path:
    path = _ROOT / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def execution_backend() -> str:
    """
    How orders are routed.

    * ``local_sim`` (default) — no IBKR required; fills are simulated locally.
    * ``ibkr`` — connect to TWS / IB Gateway (paper or live per TRADING_MODE).
    """
    return os.getenv("EXECUTION_BACKEND", "local_sim").strip().lower()


def is_local_paper_sim() -> bool:
    """True when we should NOT touch a real broker."""
    return execution_backend() != "ibkr"


def assert_paper_mode_safe() -> None:
    """
    Loud startup banner so paper mode is impossible to miss.

    Live trading requires BOTH ``TRADING_MODE=live`` AND ``EXECUTION_BACKEND=ibkr``.
    """
    from core.risk_execution_config import get_risk_execution_config

    risk = get_risk_execution_config()
    backend = execution_backend()

    if risk.trading_mode == "live" and backend == "ibkr":
        print("\n*** LIVE TRADING ENABLED - REAL MONEY AT RISK ***\n")
        return

    print("\n" + "=" * 60)
    print("  PAPER MODE (default)")
    print(f"  TRADING_MODE={risk.trading_mode!r}  EXECUTION_BACKEND={backend!r}")
    if backend == "local_sim":
        print("  Orders are simulated locally - no broker connection.")
    else:
        print("  Orders route to IBKR paper account (port 7497 by default).")
    print("=" * 60 + "\n")
