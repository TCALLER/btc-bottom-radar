"""Supply-in-profit % (optional on-chain). Triggered <= bottom_value (55)."""
from __future__ import annotations

from .base import Context, IndicatorResult

KEY = "supply_profit_pct"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    val = ctx.onchain.get("supply_profit_pct")
    available = val is not None
    triggered = bool(available and val <= bottom)
    return IndicatorResult(
        key=KEY,
        value=val,
        threshold=f"<= {bottom}%",
        triggered=triggered,
        available=available,
        detail={"supply_profit_pct": val, "weight": cfg["weight"]},
    )
