"""Pi-Cycle Bottom: daily EMA(150) vs SMA(471). Triggered when EMA150 < SMA471."""
from __future__ import annotations

from .. import ta
from .base import Context, IndicatorResult

KEY = "pi_cycle_bottom"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    ema150 = ta.ema(ctx.daily_closes, 150)
    sma471 = ta.sma(ctx.daily_closes, 471)
    available = ema150 is not None and sma471 is not None
    triggered = bool(available and ema150 < sma471)
    return IndicatorResult(
        key=KEY,
        value=triggered,
        threshold="EMA150 < SMA471",
        triggered=triggered,
        available=available,
        detail={"ema_150d": ema150, "sma_471d": sma471, "weight": cfg["weight"]},
    )
