"""RSI(14) daily. Triggered < bottom_value (30). Weekly RSI computed for display."""
from __future__ import annotations

from .base import Context, IndicatorResult

KEY = "rsi_14d"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    rsi_val = ctx.rsi_14d_daily
    available = rsi_val is not None
    triggered = bool(available and rsi_val < bottom)
    return IndicatorResult(
        key=KEY,
        value=rsi_val,
        threshold=f"< {bottom}",
        triggered=triggered,
        available=available,
        detail={
            "rsi_14d": rsi_val,
            "rsi_14_weekly": ctx.rsi_14_weekly,
            "weight": cfg["weight"],
        },
    )
