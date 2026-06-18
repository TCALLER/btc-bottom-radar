"""Mayer Multiple: price / SMA200(daily). Triggered < bottom_value (0.8)."""
from __future__ import annotations

from .. import ta
from .base import Context, IndicatorResult

KEY = "mayer_multiple"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    sma200 = ta.sma(ctx.daily_closes, 200)
    available = sma200 is not None and sma200 > 0 and ctx.price_usd is not None
    mayer = (ctx.price_usd / sma200) if available else None
    triggered = bool(mayer is not None and mayer < bottom)
    return IndicatorResult(
        key=KEY,
        value=mayer,
        threshold=f"< {bottom}",
        triggered=triggered,
        available=available,
        detail={"mayer_multiple": mayer, "sma_200d": sma200, "weight": cfg["weight"]},
    )
