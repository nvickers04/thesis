"""
Hybrid thesis definitions — default strategies for ``python main.py``.

Each thesis carries fully configurable rule parameters. Grok interprets the
explicit rule sets; mechanical evaluators supply reference signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Thesis:
    id: str
    name: str
    description: str
    watchlist: list[str]
    enabled: bool = True
    instruments: list[str] = field(default_factory=lambda: ["option"])
    data_fields: list[str] = field(
        default_factory=lambda: ["quote", "candles", "atr", "sma", "vix", "option_chain", "lunar_phase"]
    )
    option_strategies: list[str] = field(
        default_factory=lambda: [
            "long_call",
            "vertical_spread",
            "close_option",
            "covered_call",
            "close_short_call",
        ]
    )
    # Option chain fetch window (min_dte, max_dte)
    option_chain_dte: tuple[int, int] = (7, 45)
    # ── Configurable rule parameters ─────────────────────────────────────
    base_risk_pct: float = 2.0
    max_allocation_pct: float = 3.0
    dte_range: tuple[int, int] = (21, 45)
    delta_band: tuple[float, float] = (0.45, 0.55)
    target_delta: float = 0.50
    sma_period: int = 50
    vix_max: float | None = None
    take_profit_pct: float | None = None
    min_otm_pct: float | None = None
    max_otm_pct: float | None = None
    leap_min_dte: int = 365
    spread_dte_range: tuple[int, int] = (90, 180)
    timing_rule: str = ""


THESES: list[Thesis] = [
    Thesis(
        id="overnight_drift",
        name="Leveraged Overnight Drift",
        description="SPY close-to-open drift via short-dated calls.",
        watchlist=["SPY"],
        option_strategies=["long_call", "close_option"],
        option_chain_dte=(5, 7),
        base_risk_pct=2.0,
        max_allocation_pct=2.5,
        dte_range=(5, 7),
        delta_band=(0.45, 0.55),
        target_delta=0.50,
        sma_period=50,
        vix_max=30.0,
        timing_rule="Entry 15:55 ET Mon–Thu; exit 9:30–9:45 ET next session.",
    ),
    Thesis(
        id="lunar_swing",
        name="Lunar Options Swing",
        description="SPY/QQQ calls during post-new-moon window.",
        watchlist=["SPY", "QQQ"],
        option_strategies=["long_call", "close_option"],
        option_chain_dte=(21, 45),
        base_risk_pct=2.0,
        max_allocation_pct=3.0,
        dte_range=(21, 45),
        delta_band=(0.45, 0.55),
        target_delta=0.50,
        take_profit_pct=60.0,
        timing_rule="Entry: new-moon window (days 0–14). Exit: full-moon window OR +60%.",
    ),
    Thesis(
        id="defense_growth",
        name="Defense Growth Leveraged",
        description="ITA/XAR LEAP or debit spread on defense uptrend.",
        watchlist=["ITA", "XAR"],
        option_strategies=["long_call", "vertical_spread", "close_option"],
        option_chain_dte=(90, 540),
        base_risk_pct=3.0,
        max_allocation_pct=3.5,
        dte_range=(365, 540),
        delta_band=(0.40, 0.55),
        target_delta=0.45,
        leap_min_dte=365,
        spread_dte_range=(90, 180),
        sma_period=50,
        timing_rule="Quarterly rebalance Jan/Apr/Jul/Oct days 1–7; defined-risk only.",
    ),
    Thesis(
        id="automation_boom",
        name="Automation Boom Leveraged",
        description="BOTZ/ROBO LEAP or debit spread on robotics uptrend.",
        watchlist=["BOTZ", "ROBO"],
        option_strategies=["long_call", "vertical_spread", "close_option"],
        option_chain_dte=(90, 540),
        base_risk_pct=3.0,
        max_allocation_pct=3.5,
        dte_range=(365, 540),
        delta_band=(0.40, 0.55),
        target_delta=0.45,
        leap_min_dte=365,
        spread_dte_range=(90, 180),
        sma_period=50,
        timing_rule="Quarterly rebalance Jan/Apr/Jul/Oct days 1–7; defined-risk only.",
    ),
    Thesis(
        id="covered_call_overlay",
        name="Covered Call Overlay",
        description="Monthly OTM covered calls on core long holdings.",
        watchlist=["SPY", "QQQ", "ITA", "XAR", "BOTZ", "ROBO"],
        option_strategies=["covered_call", "close_short_call"],
        option_chain_dte=(30, 45),
        base_risk_pct=3.0,
        max_allocation_pct=4.0,
        dte_range=(30, 45),
        delta_band=(0.15, 0.40),
        min_otm_pct=3.0,
        max_otm_pct=5.0,
        timing_rule="Monthly roll window days 1–7; 100-share lots only.",
    ),
]


def active_theses() -> list[Thesis]:
    return [t for t in THESES if t.enabled]
