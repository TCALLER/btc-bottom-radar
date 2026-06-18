"""Indicator modules. Each exposes compute(ctx) -> IndicatorResult."""
from . import (
    drawdown,
    fear_greed,
    ma_200w,
    mayer,
    mvrv,
    pi_cycle,
    rsi,
    sopr,
    supply_profit,
)

# Ordered list used by the collector. Price/sentiment first, on-chain last.
ALL = [
    pi_cycle,
    ma_200w,
    mayer,
    rsi,
    drawdown,
    fear_greed,
    mvrv,
    sopr,
    supply_profit,
]
