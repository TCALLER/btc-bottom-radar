"""Flush-check verdict logic — pure, offline. Run: pytest -q"""
import json
from pathlib import Path

from collector import flush
from collector.derivatives import okx_liquidations_24h

CFG = json.loads((Path(__file__).parent.parent / "config" / "thresholds.json").read_text())


def snap(**kw):
    base = {"oi_change_24h_pct": None, "funding_rate": None, "liq_long_usd": None,
            "liq_short_usd": None, "liq_long_share_pct": None,
            "liq_covered_hours": None, "liq_truncated": False}
    base.update(kw)
    return base


def test_price_change_pct():
    assert flush.price_change_pct([100.0, 97.0]) == -3.0
    assert flush.price_change_pct([100.0]) is None


def test_no_drop_short_circuits():
    v = flush.classify(-1.0, snap(oi_change_24h_pct=-10, funding_rate=-0.001), CFG)
    assert v["verdict"] == "geen_daling"


def test_unknown_when_nothing_seen():
    v = flush.classify(-5.0, snap(), CFG)
    assert v["verdict"] == "onbekend"


def test_leverage_flush():
    # OI wiped, longs dominated the liquidations, funding reset → flush
    v = flush.classify(-3.1, snap(oi_change_24h_pct=-7.2, funding_rate=0.00002,
                                  liq_long_usd=400e6, liq_short_usd=50e6,
                                  liq_long_share_pct=88.9, liq_covered_hours=24), CFG)
    assert v["verdict"] == "hefboom_flush"
    assert any("open interest" in r for r in v["reasons"])


def test_flush_with_hot_funding_but_long_dominance():
    v = flush.classify(-4.0, snap(oi_change_24h_pct=-5.0, funding_rate=0.0003,
                                  liq_long_usd=300e6, liq_short_usd=20e6,
                                  liq_long_share_pct=93.8), CFG)
    assert v["verdict"] == "hefboom_flush"


def test_spot_selling():
    # OI intact, funding positive → spot-led, leverage not washed
    v = flush.classify(-2.8, snap(oi_change_24h_pct=+0.4, funding_rate=0.00012,
                                  liq_long_usd=30e6, liq_short_usd=25e6,
                                  liq_long_share_pct=54.5), CFG)
    assert v["verdict"] == "spot_verkoop"


def test_shorts_piling():
    v = flush.classify(-2.5, snap(oi_change_24h_pct=+3.0, funding_rate=-0.0002), CFG)
    assert v["verdict"] == "shorts_stapelen"


def test_mixed():
    # OI dropped a bit but funding still hot and shorts got liquidated → gemengd
    v = flush.classify(-2.5, snap(oi_change_24h_pct=-2.5, funding_rate=0.0004,
                                  liq_long_usd=40e6, liq_short_usd=60e6,
                                  liq_long_share_pct=40.0), CFG)
    assert v["verdict"] == "gemengd"


def test_every_verdict_has_text():
    for k in flush.VERDICT_NL:
        assert k in flush.VERDICT_EMOJI and k in flush.VERDICT_MEANING_NL


def test_liquidation_aggregation(monkeypatch):
    """Two OKX pages, one order older than the 24h cutoff, contract value 0.01 BTC."""
    now = 1_791_437_000_000
    h = 3_600_000
    pages = [
        {"code": "0", "data": [{"details": [
            {"ts": str(now - 1 * h), "bkPx": "84000", "sz": "10", "posSide": "long"},   # $8.400
            {"ts": str(now - 2 * h), "bkPx": "84100", "sz": "5", "posSide": "short"},   # $4.205
        ]}]},
        {"code": "0", "data": [{"details": [
            {"ts": str(now - 23 * h), "bkPx": "85000", "sz": "20", "posSide": "long"},  # $17.000
            {"ts": str(now - 30 * h), "bkPx": "86000", "sz": "99", "posSide": "long"},  # too old
        ]}]},
    ]
    calls = iter(pages)
    monkeypatch.setattr("collector.derivatives._get_json", lambda url, params=None: next(calls))
    monkeypatch.setattr("collector.derivatives.time.sleep", lambda s: None)
    out = okx_liquidations_24h(now, 0.01, max_pages=10)
    assert out["liq_long_usd"] == 25_400.0
    assert out["liq_short_usd"] == 4_205.0
    assert out["liq_long_count"] == 2 and out["liq_short_count"] == 1
    assert out["truncated"] is False
    assert out["pages"] == 2
    assert out["covered_hours"] == 23.0


def test_liquidation_source_down_is_none(monkeypatch):
    monkeypatch.setattr("collector.derivatives._get_json", lambda url, params=None: None)
    assert okx_liquidations_24h(0, 0.01, 5) is None
