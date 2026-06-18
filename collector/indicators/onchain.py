"""On-chain data provider for the optional MVRV / SOPR / supply-in-profit signals.

FREE source: bitcoin-data.com (bgeometrics). Verified live JSON shapes:
  GET /v1/mvrv-zscore/last  -> {"d":"YYYY-MM-DD","unixTs":..,"mvrvZscore":0.4234}
  GET /v1/sopr/last         -> {"d":..,"unixTs":..,"sopr":0.9948}
  GET /v1/supply-profit/last-> {"d":..,"unixTs":..,"supplyProfitBtc":1.062e7}

The free tier exposes supply-in-profit as an ABSOLUTE BTC amount, not a percent.
"Percent supply in profit" is, by definition, that amount divided by the
circulating supply — so we compute it from the same real reading plus a free
circulating-supply source (blockchain.info, CoinGecko fallback). No value is
invented.

Free tier is ~10 requests/hour per IP; a daily run makes at most 4 calls. Every
fetch is timeout+retry and degrades to None (available=false) on any failure,
including HTTP 429 — the system then scores on the available indicators only and
never raises. System behaves identically when provider == "none".
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("btc-bottom-radar.onchain")

_TIMEOUT = 20
_RETRIES = 3
_HEADERS = {"User-Agent": "btc-bottom-radar/1.0 (monitoring)"}
_BASE = "https://bitcoin-data.com/v1"

# Mid-2026 sanity bands; outside these we warn (likely a units/parser bug) but
# still store the value rather than dropping it.
_SANITY = {
    "mvrv_zscore": (-1.0, 6.0),
    "sopr": (0.8, 1.3),
    "supply_profit_pct": (30.0, 100.0),
    "nupl": (-1.0, 1.0),
    "puell_multiple": (0.0, 15.0),
}

_EMPTY = {"mvrv_zscore": None, "sopr": None, "supply_profit_pct": None,
          "nupl": None, "puell_multiple": None}


def _get_json(url: str, params: dict | None = None):
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT, headers=_HEADERS)
            if resp.status_code == 429:
                log.warning("rate limited (429) %s — on-chain unavailable this run", url)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            log.warning("onchain fetch failed (%s/%s) %s: %s", attempt, _RETRIES, url, exc)
            if attempt < _RETRIES:
                time.sleep(1.5 * attempt)
    return None


def _sanity_check(key: str, value: float | None) -> None:
    if value is None:
        return
    lo, hi = _SANITY.get(key, (float("-inf"), float("inf")))
    if not (lo <= value <= hi):
        log.warning("on-chain %s=%s outside expected [%s,%s] — possible units/parser issue",
                    key, value, lo, hi)


class OnchainProvider:
    """fetch() -> {"mvrv_zscore","sopr","supply_profit_pct"} (Nones allowed)."""

    def __init__(self, provider: str = "none", glassnode_key: str | None = None):
        # accept both "bitcoin_data" (spec) and "bitcoin-data" spellings
        self.provider = (provider or "none").strip().lower().replace("-", "_")
        self.glassnode_key = glassnode_key

    def fetch(self) -> dict:
        if self.provider == "none":
            return dict(_EMPTY)
        if self.provider == "bitcoin_data":
            return self._bitcoin_data()
        if self.provider == "glassnode" and self.glassnode_key:
            return self._glassnode()
        log.info("onchain provider '%s' not configured -> none", self.provider)
        return dict(_EMPTY)

    # ------------------------------------------------------------------ free
    def _bitcoin_data(self) -> dict:
        out = dict(_EMPTY)

        mvrv = _get_json(f"{_BASE}/mvrv-zscore/last")
        if isinstance(mvrv, dict) and mvrv.get("mvrvZscore") is not None:
            out["mvrv_zscore"] = _to_float(mvrv["mvrvZscore"])

        sopr = _get_json(f"{_BASE}/sopr/last")
        if isinstance(sopr, dict) and sopr.get("sopr") is not None:
            out["sopr"] = _to_float(sopr["sopr"])

        sip = _get_json(f"{_BASE}/supply-profit/last")
        profit_btc = _to_float(sip.get("supplyProfitBtc")) if isinstance(sip, dict) else None
        if profit_btc is not None:
            circ = _circulating_supply_btc()
            if circ and circ > 0:
                out["supply_profit_pct"] = round(profit_btc / circ * 100.0, 2)
            else:
                log.warning("circulating supply unavailable — cannot derive supply_profit_pct")

        # Top-radar on-chain series (optional; same graceful degradation).
        nupl = _get_json(f"{_BASE}/nupl/last")
        if isinstance(nupl, dict) and nupl.get("nupl") is not None:
            out["nupl"] = _to_float(nupl["nupl"])

        puell = _get_json(f"{_BASE}/puell-multiple/last")
        if isinstance(puell, dict) and puell.get("puellMultiple") is not None:
            out["puell_multiple"] = _to_float(puell["puellMultiple"])

        for k, v in out.items():
            _sanity_check(k, v)
        log.info("on-chain (bitcoin_data): mvrv=%s sopr=%s supply_profit_pct=%s nupl=%s puell=%s",
                 out["mvrv_zscore"], out["sopr"], out["supply_profit_pct"],
                 out["nupl"], out["puell_multiple"])
        return out

    # -------------------------------------------------------------- glassnode
    def _glassnode(self) -> dict:
        out = dict(_EMPTY)
        base = "https://api.glassnode.com/v1/metrics"
        params = {"a": "BTC", "api_key": self.glassnode_key, "i": "24h"}

        def last(path):
            data = _get_json(f"{base}/{path}", params)
            if isinstance(data, list) and data:
                return _to_float(data[-1].get("v"))
            return None

        out["mvrv_zscore"] = last("market/mvrv_z_score")
        out["sopr"] = last("indicators/sopr_adjusted")
        sip = last("supply/profit_relative")
        out["supply_profit_pct"] = sip * 100 if sip is not None and sip <= 1 else sip
        for k, v in out.items():
            _sanity_check(k, v)
        return out


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _circulating_supply_btc() -> float | None:
    """Free circulating-supply sources. blockchain.info returns satoshis."""
    sats = _get_json("https://blockchain.info/q/totalbc")
    # this endpoint returns a bare integer (satoshis) as text/json
    if isinstance(sats, (int, float)) and sats > 0:
        return float(sats) / 1e8
    # fallback: CoinGecko circulating_supply (already in BTC)
    cg = _get_json("https://api.coingecko.com/api/v3/coins/bitcoin",
                   {"localization": "false", "tickers": "false", "market_data": "true",
                    "community_data": "false", "developer_data": "false", "sparkline": "false"})
    if isinstance(cg, dict):
        cs = cg.get("market_data", {}).get("circulating_supply")
        return _to_float(cs)
    return None
