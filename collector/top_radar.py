"""Top / sell radar — the symmetric counterpart to the bottom radar. Measures
how many historically reliable *cycle-top* conditions are active. Reuses the
already-fetched price/on-chain data in Context. Honest framing: tops are only
confirmable in hindsight — this is tilt toward a top, never a sell command.

Each compute_* returns an IndicatorResult(key, value, threshold, triggered,
available, detail). Optional on-chain ones degrade to available=False when the
provider has no value, so they drop out of the score denominator."""
from __future__ import annotations

from . import ta
from .indicators.base import Context, IndicatorResult


def _tcfg(ctx: Context, key: str) -> dict:
    return ctx.cfg["top_indicators"][key]


def pi_cycle_top(ctx: Context) -> IndicatorResult:
    """111-day SMA >= factor * 350-day SMA (classic Pi-Cycle Top)."""
    cfg = _tcfg(ctx, "pi_cycle_top")
    factor = cfg.get("factor", 2.0)
    sma111 = ta.sma(ctx.daily_closes, 111)
    sma350 = ta.sma(ctx.daily_closes, 350)
    available = sma111 is not None and sma350 is not None
    triggered = bool(available and sma111 >= factor * sma350)
    return IndicatorResult(
        key="pi_cycle_top", value=triggered,
        threshold=f"SMA111 >= {factor}×SMA350",
        triggered=triggered, available=available,
        detail={"sma_111d": sma111, "sma_350d": sma350, "weight": cfg["weight"]},
    )


def mvrv_zscore_high(ctx: Context) -> IndicatorResult:
    cfg = _tcfg(ctx, "mvrv_zscore_high")
    top = cfg["top_value"]
    val = ctx.onchain.get("mvrv_zscore")
    available = val is not None
    triggered = bool(available and val >= top)
    return IndicatorResult(
        key="mvrv_zscore_high", value=val, threshold=f">= {top}",
        triggered=triggered, available=available,
        detail={"mvrv_zscore": val, "weight": cfg["weight"]},
    )


def mayer_high(ctx: Context) -> IndicatorResult:
    cfg = _tcfg(ctx, "mayer_high")
    top = cfg["top_value"]
    sma200 = ta.sma(ctx.daily_closes, 200)
    available = sma200 is not None and sma200 > 0 and ctx.price_usd is not None
    mayer = (ctx.price_usd / sma200) if available else None
    triggered = bool(mayer is not None and mayer > top)
    return IndicatorResult(
        key="mayer_high", value=mayer, threshold=f"> {top}",
        triggered=triggered, available=available,
        detail={"mayer_multiple": mayer, "sma_200d": sma200, "weight": cfg["weight"]},
    )


def rsi_14w_high(ctx: Context) -> IndicatorResult:
    cfg = _tcfg(ctx, "rsi_14w_high")
    top = cfg["top_value"]
    val = ctx.rsi_14_weekly
    available = val is not None
    triggered = bool(available and val > top)
    return IndicatorResult(
        key="rsi_14w_high", value=val, threshold=f"> {top}",
        triggered=triggered, available=available,
        detail={"rsi_14w": val, "weight": cfg["weight"]},
    )


def fear_greed_high(ctx: Context) -> IndicatorResult:
    cfg = _tcfg(ctx, "fear_greed_high")
    top = cfg["top_value"]
    fg = ctx.fear_greed
    available = fg is not None
    triggered = bool(available and fg >= top)
    return IndicatorResult(
        key="fear_greed_high", value=fg, threshold=f">= {top}",
        triggered=triggered, available=available,
        detail={"fear_greed": fg, "weight": cfg["weight"]},
    )


def nupl(ctx: Context) -> IndicatorResult:
    cfg = _tcfg(ctx, "nupl")
    top = cfg["top_value"]
    val = ctx.onchain.get("nupl")
    available = val is not None
    triggered = bool(available and val > top)
    return IndicatorResult(
        key="nupl", value=val, threshold=f"> {top}",
        triggered=triggered, available=available,
        detail={"nupl": val, "weight": cfg["weight"]},
    )


def puell_multiple(ctx: Context) -> IndicatorResult:
    cfg = _tcfg(ctx, "puell_multiple")
    top = cfg["top_value"]
    val = ctx.onchain.get("puell_multiple")
    available = val is not None
    triggered = bool(available and val > top)
    return IndicatorResult(
        key="puell_multiple", value=val, threshold=f"> {top}",
        triggered=triggered, available=available,
        detail={"puell_multiple": val, "weight": cfg["weight"]},
    )


# Order mirrors config; used by the collector and the dashboard table.
TOP_INDICATORS = [
    pi_cycle_top,
    mvrv_zscore_high,
    mayer_high,
    rsi_14w_high,
    fear_greed_high,
    nupl,
    puell_multiple,
]


def compute_top_results(ctx: Context) -> list[IndicatorResult]:
    return [fn(ctx) for fn in TOP_INDICATORS]
