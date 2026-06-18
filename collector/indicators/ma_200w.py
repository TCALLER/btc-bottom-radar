"""200-week MA: weekly SMA(200). Triggered when price <= MA200w."""
from __future__ import annotations

from .. import ta
from .base import Context, IndicatorResult

KEY = "ma_200w"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    ma200w = ta.sma(ctx.weekly_closes, 200)
    available = ma200w is not None and ctx.price_usd is not None
    triggered = bool(available and ctx.price_usd <= ma200w)
    return IndicatorResult(
        key=KEY,
        value=ma200w,
        threshold=f"price <= MA200w ({ma200w:.0f})" if ma200w else "price <= MA200w",
        triggered=triggered,
        available=available,
        detail={"ma_200w": ma200w, "price_usd": ctx.price_usd, "weight": cfg["weight"]},
    )
