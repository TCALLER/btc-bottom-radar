"""Unit tests for the pure TA math, checked against hand-verifiable values and
the committed fixture. Run: pytest -q"""
import json
from pathlib import Path

from collector import ta

FIX = json.loads((Path(__file__).parent / "fixtures" / "prices.json").read_text())


def test_sma_basic():
    # last 5 of 1..10 = (6+7+8+9+10)/5 = 8.0
    assert ta.sma(FIX["ramp_1_to_10"], 5) == 8.0
    assert ta.sma(FIX["constant_100"], 10) == 100.0
    assert ta.sma([1, 2], 5) is None  # too short


def test_ema_recurrence():
    # EMA([1..6], period 3): seed=2, k=0.5 -> 3 -> 4 -> 5
    assert ta.ema([1, 2, 3, 4, 5, 6], 3) == 5.0
    # EMA of a constant series equals the constant
    assert ta.ema(FIX["constant_100"], 5) == 100.0
    assert ta.ema([1, 2], 5) is None


def test_rsi_extremes():
    # strictly increasing -> no losses -> RSI 100
    assert ta.rsi(FIX["ramp_1_to_10"], 9) == 100.0
    # strictly decreasing -> no gains -> RSI 0
    assert ta.rsi([10, 9, 8, 7, 6, 5, 4, 3, 2, 1], 9) == 0.0
    assert ta.rsi([1, 2, 3], 14) is None  # too short


def test_rsi_midrange_fixture():
    # The classic Wilder warm-up series should land in a sane mid/high band.
    val = ta.rsi(FIX["rsi_series"], 14)
    assert val is not None
    assert 60.0 <= val <= 80.0


def test_max_close():
    assert ta.max_close(FIX["drawdown_series"]) == 100
    assert ta.max_close([]) is None


def test_drawdown_formula():
    # price 25 vs ATH 100 => 75% drawdown
    series = FIX["drawdown_series"]
    price = series[-1]
    ath = ta.max_close(series)
    dd = (1.0 - (price / ath)) * 100.0
    assert dd == 75.0
