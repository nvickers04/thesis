"""
Project bootstrap — load .env, configure logging, IBKR stream defaults.

Import ``glue.bootstrap`` first (``main.py`` does this) so stream env vars are
set before ``core.config`` reads them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except ImportError:
        pass


def _apply_ibkr_stream_defaults() -> None:
    """
    Auto-enable IBKR quote streams when appropriate.

    IBKR_STREAMS=auto (default): enable when EXECUTION_BACKEND=ibkr or TRADING_MODE=live.
    IBKR_STREAMS=1|0: force on/off (overrides auto).
    """
    mode = os.getenv("IBKR_STREAMS", "auto").strip().lower()
    if mode in ("0", "false", "no", "off"):
        os.environ["IBKR_QUOTES_ENABLED"] = "0"
        return
    if mode in ("1", "true", "yes", "on"):
        os.environ["IBKR_QUOTES_ENABLED"] = "1"
        return

    # auto — only set if user has not explicitly configured IBKR_QUOTES_ENABLED
    if os.getenv("IBKR_QUOTES_ENABLED") is not None:
        return
    backend = os.getenv("EXECUTION_BACKEND", "local_sim").strip().lower()
    trading_mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    if backend == "ibkr" or trading_mode == "live":
        os.environ["IBKR_QUOTES_ENABLED"] = "1"


# Run before any core.config import (main.py imports bootstrap first).
_load_dotenv()
_apply_ibkr_stream_defaults()


def bootstrap(*, verbose: bool = False) -> None:
    """Configure structured logging (call once at startup)."""
    from core.log_setup import configure_root_logging

    configure_root_logging("thesis_trader.log", verbose=verbose)


def project_root() -> Path:
    return _ROOT


def logs_dir() -> Path:
    path = _ROOT / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def execution_backend() -> str:
    """local_sim (default) or ibkr."""
    return os.getenv("EXECUTION_BACKEND", "local_sim").strip().lower()


def is_local_paper_sim() -> bool:
    return execution_backend() != "ibkr"


def ibkr_streams_enabled() -> bool:
    from core.config import IBKR_QUOTES_ENABLED

    return bool(IBKR_QUOTES_ENABLED)


def assert_paper_mode_safe() -> None:
    from core.risk_execution_config import get_risk_execution_config

    risk = get_risk_execution_config()
    backend = execution_backend()
    streams = "on" if ibkr_streams_enabled() else "off"

    if risk.trading_mode == "live" and backend == "ibkr":
        print("\n*** LIVE TRADING ENABLED - REAL MONEY AT RISK ***\n")
        print(f"  IBKR streams: {streams}\n")
        return

    print("\n" + "=" * 60)
    print("  PAPER MODE (default)")
    print(f"  TRADING_MODE={risk.trading_mode!r}  EXECUTION_BACKEND={backend!r}")
    print(f"  IBKR quote streams: {streams}")
    if backend == "local_sim":
        print("  Orders are simulated locally - no broker order routing.")
    else:
        print("  Orders route to IBKR paper account (port 7497 by default).")
    print("=" * 60 + "\n")
