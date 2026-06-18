"""Pure technical-analysis math. No I/O, no globals — unit-tested against a
committed fixture. All functions operate on lists of float closing prices
ordered oldest -> newest."""
from __future__ import annotations

from typing import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last `period` values."""
    if values is None or len(values) < period or period <= 0:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float | None:
    """Exponential moving average (last value of the EMA series).

    Seeded with the SMA of the first `period` values, then smoothed with
    multiplier 2/(period+1) — the standard EMA definition.
    """
    if values is None or len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    ema_val = seed
    for price in values[period:]:
        ema_val = price * k + ema_val * (1.0 - k)
    return ema_val


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI on closing prices. Returns 0..100 or None if too short."""
    if values is None or len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    # initial average gain/loss over the first `period` deltas
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    # Wilder smoothing across the remaining deltas
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def max_close(values: Sequence[float]) -> float | None:
    """Highest close in the series (used for ATH from data)."""
    if not values:
        return None
    return max(values)
