"""Module-level trading constants loaded from .env via risk_execution_config."""

from __future__ import annotations

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.risk_execution_config import get_risk_execution_config

_risk = get_risk_execution_config()

# Risk
TRADING_MODE = _risk.trading_mode
IBKR_ACCOUNT_TYPE = _risk.ibkr_account_type
RISK_PER_TRADE = _risk.risk_per_trade_fraction
MIN_RR_RATIO = float(_risk.min_rr_ratio or 2.0)
CASH_ONLY = _risk.cash_only

# Safety rails
MAX_DAILY_LOSS_PCT = _risk.max_daily_loss_pct
INTRADAY_DRAWDOWN_PCT = _risk.intraday_drawdown_pct
MAX_DAILY_LLM_COST = _risk.max_daily_llm_cost

# IBKR connection
IBKR_HOST = _risk.ibkr_host
IBKR_PORT = _risk.resolve_ibkr_port()
IBKR_CLIENT_ID = _risk.ibkr_client_id
IBKR_CONNECT_MAX_ATTEMPTS = _risk.ibkr_connect_max_attempts
IBKR_ACCOUNT_ID = _risk.ibkr_account_id

# IBKR live quote streams (see glue/ibkr_streams.py)
IBKR_QUOTES_ENABLED = _risk.ibkr_quotes_enabled
IBKR_QUOTE_LINE_BUDGET = _risk.ibkr_quote_line_budget

# LLM token ceilings (optional; cost_tracker reads these)
MAX_DAILY_LLM_NONCACHED_PROMPT_TEXT_TOKENS = _risk.max_daily_llm_noncached_prompt_text_tokens
MAX_DAILY_LLM_CACHED_PROMPT_TEXT_TOKENS = _risk.max_daily_llm_cached_prompt_text_tokens
MAX_DAILY_LLM_PROMPT_IMAGE_TOKENS = _risk.max_daily_llm_prompt_image_tokens
MAX_DAILY_LLM_COMPLETION_TOKENS = _risk.max_daily_llm_completion_tokens
MAX_DAILY_LLM_REASONING_TOKENS = _risk.max_daily_llm_reasoning_tokens
MAX_DAILY_LLM_OUTPUT_PRICED_TOKENS = _risk.max_daily_llm_output_priced_tokens


def get_effective_risk_per_trade() -> float:
    """Fraction of NLV risked per trade (used by execution risk guards)."""
    return RISK_PER_TRADE


def refresh_trading_identity_from_environ() -> None:
    """Reload risk config after .env / CLI changes."""
    from core.risk_execution_config import reload_risk_execution_config

    reload_risk_execution_config()
    global _risk
    _risk = get_risk_execution_config()


__all__ = [
    "CASH_ONLY",
    "IBKR_ACCOUNT_ID",
    "IBKR_CLIENT_ID",
    "IBKR_CONNECT_MAX_ATTEMPTS",
    "IBKR_HOST",
    "IBKR_PORT",
    "IBKR_QUOTE_LINE_BUDGET",
    "IBKR_QUOTES_ENABLED",
    "MAX_DAILY_LLM_CACHED_PROMPT_TEXT_TOKENS",
    "MAX_DAILY_LLM_COMPLETION_TOKENS",
    "MAX_DAILY_LLM_COST",
    "MAX_DAILY_LLM_NONCACHED_PROMPT_TEXT_TOKENS",
    "MAX_DAILY_LLM_OUTPUT_PRICED_TOKENS",
    "MAX_DAILY_LLM_PROMPT_IMAGE_TOKENS",
    "MAX_DAILY_LLM_REASONING_TOKENS",
    "MAX_DAILY_LOSS_PCT",
    "MIN_RR_RATIO",
    "RISK_PER_TRADE",
    "TRADING_MODE",
    "get_effective_risk_per_trade",
    "refresh_trading_identity_from_environ",
]
