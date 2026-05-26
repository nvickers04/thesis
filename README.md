# Thesis Grok Trader

A minimal, thesis-driven trading runner powered by [Grok (xAI)](https://docs.x.ai/).

Define **strategies** in `theses.py`. Default run: mechanical reference signals + Grok rule interpretation → global risk gates → execute (paper sim or IBKR) → log. **Paper trading is the default.**

No ReAct agent loop, no research host, no Postgres, no signal engine.

---

## Risk warning

**Trading involves substantial risk of loss.** Defaults are paper-only (`EXECUTION_BACKEND=local_sim`). Live trading requires `TRADING_MODE=live` **and** `EXECUTION_BACKEND=ibkr`. Never risk money you cannot afford to lose.

---

## Quick start

```bash
git clone https://github.com/nvickers04/thesis.git
cd thesis
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.template .env
```

Set `GROK_API_KEY` and `MARKETDATA_TOKEN` (required for option chains). Edit `theses.py`, then:

```bash
python main.py --list
python main.py
```

Audit log: `logs/thesis_trader.jsonl`

---

## How a trade happens

Read `main.py` — default hybrid flow:

| Step | What |
|------|------|
| 1 | Load enabled theses from `theses.py` |
| 2 | Mechanical evaluators produce reference signals (all five theses) |
| 3 | Fetch market data + account snapshot |
| 4 | Grok receives full rule sets + signals → JSON allocation per thesis |
| 5 | Global risk gates + per-thesis risk check |
| 6 | Execute via `glue/executor.py` → IBKR or paper sim |
| 7 | Log to JSONL |

Hidden fallback: `python main.py --legacy-llm` for original per-thesis Grok-only flow (no mechanical layer).

---

## Execution modes

| `EXECUTION_BACKEND` | Orders |
|---------------------|--------|
| `local_sim` (default) | Simulated fills in `glue/paper_broker.py` |
| `ibkr` | Real orders via `execution/` (paper port 7497 by default) |

---

## Options support

Theses can set `instruments=["option"]` (or include both `"stock"` and `"option"`).

**Supported strategies** (Grok `option.strategy` field):

| Strategy | Action | Description |
|----------|--------|-------------|
| `long_call` | buy | Buy call single-leg |
| `long_put` | buy | Buy put single-leg |
| `vertical_spread` | buy | Debit/credit vertical (needs `long_strike`, `short_strike`, `right`) |
| `close_option` | sell | Close existing long option |

**Data:** add `"option_chain"` to `data_fields` — chains come from **MarketData.app** REST.

**Execution:**

- `EXECUTION_BACKEND=ibkr` → `execution/ibkr_options.py` (TWS required)
- `EXECUTION_BACKEND=local_sim` → simulated fills in `glue/paper_broker.py`

See the commented example in `theses.py`.

---

## IBKR real-time streams

Live stock quotes can stream from IBKR instead of MarketData.app REST.

| Variable | Default | Meaning |
|----------|---------|---------|
| `IBKR_STREAMS` | `auto` | `auto` enables streams when `EXECUTION_BACKEND=ibkr` or `TRADING_MODE=live` |
| `IBKR_QUOTES_ENABLED` | (auto) | Force `1` or `0` |
| `IBKR_QUOTE_LINE_BUDGET` | `90` | Max concurrent streaming lines |

**Requirements:** TWS or IB Gateway running, API enabled, and your **market data subscriptions** active (works on paper and live accounts).

When streams are on, `DataProvider.get_quote()` uses IBKR NBBO. Option chains still use MarketData.app.

You can use streams for data-only while simulating orders (`EXECUTION_BACKEND=local_sim` + TWS running).

Implementation: `glue/ibkr_streams.py` + `data/ibkr_quote_source.py`.

---

## Project layout

```
thesis/
├── main.py              # Linear flow — start here
├── theses.py            # Your strategies
├── glue/                # bootstrap, fetch, prompts, risk, executor, options, streams
├── core/                # Grok SDK, risk config, safety, JSON parse
├── data/                # MarketData.app + DataProvider + IBKR quote source
├── execution/           # IBKR orders (stocks + options)
└── memory/              # No-op stubs (no database)
```

---

## Adding a thesis

```python
Thesis(
    id="my_strategy",
    name="My strategy",
    description="What Grok should look for...",
    watchlist=["AAPL", "MSFT"],
    enabled=True,
    instruments=["stock"],
    data_fields=["quote", "candles", "atr"],
)
```

For options, see the commented template in `theses.py`.

---

## Hybrid mode (default)

Five rule-based theses are configured in `theses.py`. Parameters (`base_risk_pct`, `max_allocation_pct`, `dte_range`, `delta_band`, etc.) are fully configurable on each `Thesis` object.

```bash
python main.py --list              # show configured theses
python main.py                     # hybrid cycle (default)
python main.py --thesis overnight_drift
python main.py --signals-only      # rules + Grok; log only (no execution)
python main.py --backtest          # 2023-present mechanical sim (weekly)
python main.py --backtest --backtest-daily
```

**PortfolioManager** aggregates mechanical signals. **Global risk gates**: 8% peak drawdown → block new premium until cash ≥ 50% NLV; max 50% option premium exposure.

Mechanical evaluators live in `theses_rules/rules_based_theses.py` and are wired from `theses.py` via `theses_rules/config_bridge.py`.

---

## Requirements

- Python 3.11+
- Grok API key
- MarketData.app token (quotes, candles, ATR, MDA option fallback)
- Polygon API key (optional — delta-precise option chains)
- TWS/IB Gateway (optional for streams; required for IBKR execution)

---

## Hybrid data layer (MarketData.app + Polygon)

| Data type | Primary | Fallback |
|-----------|---------|----------|
| Quotes, candles, ATR | MarketData.app | IBKR streams |
| Options chain (delta filter) | Polygon | MarketData.app |
| Lunar phase | Astropy (`MoonCalculator`) | — |
| SMA / VIX | MarketData.app + pure math | yfinance (VIX fallback) |

```python
from data.data_provider import get_data_provider

p = get_data_provider()
p.get_options_chain("AAPL", min_delta=0.40, max_delta=0.60, data_source="auto")
p.get_lunar_phase()
p.is_new_moon_window()   # 14-day post-new-moon window
p.get_sma("SPY", period=20)
p.get_vix()
```

Pure functions for tests: `data.MoonCalculator.*`, `data.technicals.sma/ma/ema`.
