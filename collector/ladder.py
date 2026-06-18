"""Buy-ladder engine — a 3-phase validation ladder, notify-only.

Per tranche:  pending --(value rule true)--> armed --(price rebounds
confirm_rebound_pct% above its low for confirm_days in a row)--> fired.
Plus an uptrend fallback: once price closes above the 200-day MA, the remaining
deep levels likely won't return this cycle, so the still-open tranches fire once
on that confirmed level. Anti-bull-trap: a fresh low while armed resets the
confirmation streak.

It NEVER trades and NEVER says 'koop' as an order — it notifies; the human decides.

PRIVACY: btc.ladder_state and btc.positions are personal (no anon policy). The
ladder lives in Telegram + this CLI only.

CLI:
  python -m collector.ladder --status
  python -m collector.ladder --preview arm|fire|uptrend [--trap N] [--send-test]
  python -m collector.ladder --backtest [--years N]
  python -m collector.ladder --mark-bought <trap_id|0> <eur> [--price USD] [--note "..."]
  python -m collector.ladder --positions
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .config import PROJECT_ROOT, load_thresholds, setup_logging
from . import notify_telegram as tg
from . import persist_supabase as db
from . import scoring
from . import ta

log = setup_logging()

LADDER_PATH = PROJECT_ROOT / "config" / "ladder.json"


# --------------------------------------------------------------------------- config
def load_ladder() -> dict:
    with open(LADDER_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def amount_eur(budget: float, pct: float) -> float:
    return round(budget * pct / 100.0, 2)


def fmt_eur(amount) -> str:
    try:
        return f"{round(float(amount)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "n.b."


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------- value rules (arm)
def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# Levels are multiples of sma_200d so they auto-track the moving average.
_NADEREND_PLUS = ("naderend", "sterke_bodem_confluentie")


def rule_ma200w_and_tier_naderend(row: dict) -> bool:
    """Trap 1: price <= 200w-MA AND tier >= naderend."""
    price = _f(row.get("price_usd"))
    ma = _f(row.get("ma_200w"))
    return bool(price is not None and ma is not None and price <= ma
                and row.get("tier") in _NADEREND_PLUS)


def rule_mayer070_or_score62(row: dict) -> bool:
    """Trap 2: price <= 0.70*sma_200d  OR  bottom_score >= 62."""
    price = _f(row.get("price_usd"))
    sma = _f(row.get("sma_200d"))
    by_price = price is not None and sma is not None and price <= 0.70 * sma
    return bool(by_price or (row.get("bottom_score") or 0) >= 62)


def rule_mayer050_or_mvrv_or_fg(row: dict) -> bool:
    """Trap 3: price <= 0.50*sma_200d  OR  mvrv_zscore <= 0.1  OR  fear_greed <= 10."""
    price = _f(row.get("price_usd"))
    sma = _f(row.get("sma_200d"))
    mvrv = _f(row.get("mvrv_zscore"))
    fg = row.get("fear_greed")
    return bool(
        (price is not None and sma is not None and price <= 0.50 * sma)
        or (mvrv is not None and mvrv <= 0.1)
        or (fg is not None and fg <= 10)
    )


RULES = {
    "ma200w_and_tier_naderend": rule_ma200w_and_tier_naderend,
    "mayer070_or_score62": rule_mayer070_or_score62,
    "mayer050_or_mvrv_or_fg": rule_mayer050_or_mvrv_or_fg,
}


# --------------------------------------------------------------------------- DB helpers
def fetch_state(client) -> dict:
    resp = client.table("ladder_state").select("*").execute()
    return {r["tranche_id"]: r for r in (resp.data or [])}


def seed_state(client, ladder: dict) -> dict:
    """Insert missing tranches as pending; refresh config-derived fields on existing
    rows (label/pct/amount/rule/confirm_*) WITHOUT touching runtime state
    (status/armed_*/fired_*)."""
    existing = fetch_state(client)
    budget = ladder["budget"]
    cdays = ladder.get("confirm_days", 2)
    for tr in ladder["tranches"]:
        cfg_fields = {
            "label": tr["label"], "pct": tr["pct"],
            "amount_eur": amount_eur(budget, tr["pct"]),
            "rule": tr["value_rule"],
            "confirm_rebound_pct": tr["confirm_rebound_pct"],
            "confirm_days": cdays,
        }
        if tr["id"] not in existing:
            client.table("ladder_state").insert(
                {**cfg_fields, "tranche_id": tr["id"], "status": "pending"}).execute()
            log.info("seeded ladder tranche %s", tr["id"])
        else:
            client.table("ladder_state").update(cfg_fields).eq("tranche_id", tr["id"]).execute()
    return fetch_state(client)


def positions_summary(client, budget: float) -> dict:
    rows = (client.table("positions").select("*").order("bought_on").execute().data) or []
    deployed = sum(_f(r.get("amount_eur")) or 0 for r in rows)
    priced = [(_f(r.get("amount_eur")) or 0, _f(r.get("price_usd")))
              for r in rows if _f(r.get("price_usd")) is not None]
    avg = (sum(a * p for a, p in priced) / sum(a for a, _ in priced)) if priced and sum(
        a for a, _ in priced) else None
    return {"deployed": deployed, "remaining": budget - deployed,
            "avg_price": avg, "count": len(rows), "rows": rows}


# --------------------------------------------------------------------------- messages
def build_armeer(label, armed_price, rebound_pct, confirm_days, amount) -> str:
    return (f"🟡 <b>Ladder — {label} BEWAPEND</b> op {tg._usd(armed_price)}\n"
            f"Niveau geraakt. Ik wacht nu op bevestiging: koers +{rebound_pct}% boven het "
            f"dieptepunt, {confirm_days} dagen op rij. Inzet zodra bevestigd: ~€{fmt_eur(amount)}. "
            f"Nog géén koop.")


def build_koopmoment(label, low, actual_rebound, confirm_days, amount,
                     remaining_pairs, rest_total) -> str:
    return (f"🪜🔔 <b>KOOPMOMENT — {label} BEVESTIGD</b>\n"
            f"Dieptepunt {tg._usd(low)}, nu +{actual_rebound:.1f}% hersteld over {confirm_days} "
            f"dagen → bevestigde instap.\n"
            f"👉 Overweeg ~€{fmt_eur(amount)} in te zetten. Jouw beslissing — geen koopopdracht, "
            f"niets wordt automatisch gekocht.\n"
            f"Nog open: {remaining_pairs}, totaal ~€{fmt_eur(rest_total)}.")


def build_vangnet(sma_200d, fired_pairs, total) -> str:
    return (f"🚀 <b>Ladder — bevestigde uptrend</b> (koers boven 200-daagse MA {tg._usd(sma_200d)})\n"
            f"De diepe niveaus komen deze cyclus waarschijnlijk niet meer. Overweeg je resterende "
            f"trappen nu op dit bevestigde niveau: {fired_pairs}, totaal ~€{fmt_eur(total)}. "
            f"Jouw beslissing — geen koopopdracht.")


# --------------------------------------------------------------------------- state machine
def evaluate(row: dict, *, client=None) -> dict:
    """Run the 3-phase state machine after the daily row is persisted. Returns
    {"armed":[ids], "fired":[ids], "uptrend":bool}. Idempotent; notify-only."""
    if client is None:
        client = db.get_client()
    ladder = load_ladder()
    budget = ladder["budget"]
    state = seed_state(client, ladder)
    events = {"armed": [], "fired": [], "uptrend": False}

    price = _f(row.get("price_usd"))
    sma200 = _f(row.get("sma_200d"))
    now_iso, today = _now_iso(), _today()
    statuses = {tid: dict(state[tid]) for tid in state}

    for tid in sorted(statuses):
        tr = statuses[tid]
        st = tr.get("status", "pending")
        rule = tr.get("rule")
        amount = _f(tr.get("amount_eur")) or 0.0

        if st == "pending":
            if rule in RULES and RULES[rule](row):
                upd = {"status": "armed", "armed_at": now_iso, "armed_on_date": today,
                       "armed_price_usd": price, "low_since_arm_usd": price, "confirm_streak": 0}
                client.table("ladder_state").update(upd).eq("tranche_id", tid).execute()
                tr.update(upd)
                events["armed"].append(tid)
                tg.send_message(build_armeer(tr["label"], price, tr.get("confirm_rebound_pct"),
                                             tr.get("confirm_days", 2), amount))
                log.info("ladder trap %s ARMED at %s", tid, price)

        elif st == "armed" and price is not None:
            low = _f(tr.get("low_since_arm_usd"))
            streak = int(tr.get("confirm_streak") or 0)
            rebound = _f(tr.get("confirm_rebound_pct")) or 0.0
            if low is None or price <= low:           # fresh low -> reset (anti-bull-trap)
                low = price
                streak = 0
            threshold = low * (1 + rebound / 100.0)
            if price >= threshold:
                streak += 1
            else:
                streak = 0
            client.table("ladder_state").update(
                {"low_since_arm_usd": low, "confirm_streak": streak}).eq("tranche_id", tid).execute()
            tr["low_since_arm_usd"], tr["confirm_streak"] = low, streak

            if streak >= int(tr.get("confirm_days", 2)):
                actual_rebound = (price / low - 1) * 100 if low else 0.0
                fired = {"status": "fired", "fire_reason": "confirmed", "fired_at": now_iso,
                         "fired_on_date": today, "fired_price_usd": price,
                         "fired_score": row.get("bottom_score")}
                client.table("ladder_state").update(fired).eq("tranche_id", tid).execute()
                tr.update(fired)
                events["fired"].append(tid)
                remaining_ids = [x for x in sorted(statuses)
                                 if statuses[x].get("status") != "fired"]
                pairs, total = tg.trap_pairs(statuses, remaining_ids)
                tg.send_message(build_koopmoment(tr["label"], low, actual_rebound,
                                                 tr.get("confirm_days", 2), amount, pairs, total))
                _log_alert(client, "ladder_fire", row, tr["label"])
                log.info("ladder trap %s FIRED (confirmed)", tid)

    # Uptrend fallback (fire-once): price above 200d-MA and >=1 non-fired trap.
    nonfired = [tid for tid in statuses if statuses[tid].get("status") != "fired"]
    if price is not None and sma200 is not None and price > sma200 and nonfired:
        pairs, total = tg.trap_pairs(statuses, nonfired)
        for tid in nonfired:
            client.table("ladder_state").update(
                {"status": "fired", "fire_reason": "uptrend", "fired_at": now_iso,
                 "fired_on_date": today, "fired_price_usd": price,
                 "fired_score": row.get("bottom_score")}).eq("tranche_id", tid).execute()
            statuses[tid]["status"] = "fired"
            events["fired"].append(tid)
        events["uptrend"] = True
        tg.send_message(build_vangnet(sma200, pairs, total))
        _log_alert(client, "ladder_uptrend", row, "uptrend-vangnet")
        log.info("ladder uptrend fallback fired traps %s", nonfired)

    return events


def _log_alert(client, atype, row, label):
    try:
        db.insert_alert(client, {
            "alert_type": atype, "tier": row.get("tier"),
            "bottom_score": row.get("bottom_score"),
            "message": f"{atype}: {label}", "payload": {"label": label}, "delivered": True})
    except Exception as exc:  # noqa: BLE001
        log.warning("alert log failed: %s", exc)


# --------------------------------------------------------------------------- status / positions
def show_status() -> int:
    client = db.get_client()
    ladder = load_ladder()
    state = seed_state(client, ladder)
    budget = ladder["budget"]
    ps = positions_summary(client, budget)
    print(f"\nBuy-ladder — {ladder['asset']} budget €{fmt_eur(budget)} ({ladder['currency']})")
    print(f"{'#':<3}{'trap':<26}{'€':>8}  {'status':<8} detail")
    print("-" * 78)
    for tid in sorted(state):
        tr = state[tid]
        detail = {
            "pending": "wacht op niveau",
            "armed": f"bewapend op ${_fmt0(tr.get('armed_price_usd'))}, "
                     f"koop > ${_fmt0(_threshold(tr))} (streak {tr.get('confirm_streak', 0)}"
                     f"/{tr.get('confirm_days', 2)})",
            "fired": f"KOOP {tr.get('fired_on_date')} ({tr.get('fire_reason')})",
        }.get(tr.get("status"), tr.get("status"))
        print(f"{tid:<3}{tr.get('label', '')[:24]:<26}{fmt_eur(tr.get('amount_eur')):>8}  "
              f"{tr.get('status', ''):<8} {detail}")
    avg = f"${_fmt0(ps['avg_price'])}" if ps["avg_price"] else "n.v.t."
    print(f"\nIngezet: €{fmt_eur(ps['deployed'])} · Droog kruit: €{fmt_eur(ps['remaining'])}"
          f" · Gem. instap {avg} · {ps['count']} aankoop(en)")
    return 0


def _threshold(tr) -> float | None:
    low = _f(tr.get("low_since_arm_usd"))
    pct = _f(tr.get("confirm_rebound_pct"))
    return low * (1 + pct / 100.0) if (low is not None and pct is not None) else None


def _fmt0(v) -> str:
    n = _f(v)
    return f"{n:,.0f}".replace(",", ".") if n is not None else "?"


def mark_bought(trap_id: int, eur: float, price: float | None, note: str | None) -> int:
    client = db.get_client()
    rec = {"tranche_id": (trap_id if trap_id > 0 else None), "bought_on": _today(),
           "amount_eur": eur, "price_usd": price, "btc_amount": None, "note": note}
    client.table("positions").insert(rec).execute()
    print(f"Aankoop geregistreerd: €{fmt_eur(eur)}"
          + (f" @ ${_fmt0(price)}" if price else "")
          + (f" (trap {trap_id})" if trap_id > 0 else " (los)"))
    if trap_id > 0:
        state = fetch_state(client)
        tr = state.get(trap_id)
        if tr and tr.get("status") != "fired":
            client.table("ladder_state").update(
                {"status": "fired", "fire_reason": "manual", "fired_at": _now_iso(),
                 "fired_on_date": _today(), "fired_price_usd": price}).eq("tranche_id", trap_id).execute()
            print(f"Trap {trap_id} gemarkeerd als gekocht (fire_reason=manual).")
    return 0


def list_positions() -> int:
    client = db.get_client()
    ladder = load_ladder()
    ps = positions_summary(client, ladder["budget"])
    print(f"\nPosities ({ps['count']}):")
    print(f"{'datum':<12}{'trap':<6}{'€':>8}{'prijs$':>10}  notitie")
    print("-" * 60)
    for r in ps["rows"]:
        print(f"{str(r.get('bought_on')):<12}{str(r.get('tranche_id') or '-'):<6}"
              f"{fmt_eur(r.get('amount_eur')):>8}{_fmt0(r.get('price_usd')):>10}  {r.get('note') or ''}")
    avg = f"${_fmt0(ps['avg_price'])}" if ps["avg_price"] else "n.v.t."
    print(f"\nIngezet: €{fmt_eur(ps['deployed'])} · Droog kruit: €{fmt_eur(ps['remaining'])}"
          f" · Gem. instap {avg}")
    return 0


# --------------------------------------------------------------------------- preview (no DB writes)
def preview(kind: str, trap_id: int, send_test: bool) -> int:
    cfg = load_thresholds()
    ladder = load_ladder()
    client = db.get_client()
    latest = db.fetch_last_snapshot(client) or {}
    price = _f(latest.get("price_usd")) or 65000.0
    sma200 = _f(latest.get("sma_200d")) or price
    by_id = {t["id"]: t for t in ladder["tranches"]}
    budget = ladder["budget"]

    def amt(tid):
        t = by_id.get(tid)
        return amount_eur(budget, t["pct"]) if t else 0.0

    cdays = ladder.get("confirm_days", 2)
    if kind == "arm":
        t = by_id[trap_id]
        msg = build_armeer(t["label"], price, t["confirm_rebound_pct"], cdays, amt(trap_id))
    elif kind == "fire":
        t = by_id[trap_id]
        rebound = t["confirm_rebound_pct"]
        low = price / (1 + rebound / 100.0)           # so actual rebound ≈ rebound%
        remaining_ids = [i for i in by_id if i != trap_id]
        pairs, total = tg.trap_pairs({i: {"amount_eur": amt(i)} for i in by_id}, remaining_ids)
        msg = build_koopmoment(t["label"], low, rebound, cdays, amt(trap_id), pairs, total)
    elif kind == "uptrend":
        all_ids = sorted(by_id)
        pairs, total = tg.trap_pairs({i: {"amount_eur": amt(i)} for i in by_id}, all_ids)
        msg = build_vangnet(sma200, pairs, total)
    else:
        print(f"onbekende preview: {kind}")
        return 2

    preview_msg = f"🧪 <b>PREVIEW</b> ({kind}) — geen echte trigger\n\n{msg}\n\n(test)"
    print("\n--- PREVIEW (zou verzonden worden) ---")
    print(preview_msg.replace("<b>", "").replace("</b>", "").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&amp;", "&"))
    if send_test:
        ok = tg.send_message(preview_msg)
        print(f"\n🧪 PREVIEW Telegram verzonden: {ok}")
    return 0


# --------------------------------------------------------------------------- backtest (price-only)
_BT_DISCLAIMER = (
    "⚠️ PRIJS-ONLY BACKTEST. Dit toetst enkel prijs-afgeleide signalen "
    "(koers, SMA200d, 200w-MA, Mayer, drawdown). Het KAN MVRV-Z, SOPR, NUPL, Puell, "
    "supply-in-profit, Fear&Greed of de Pi-Cycle-bodem NIET valideren — daar bestaat geen "
    "gratis historische reeks voor, dus die drempels blijven analytisch gekalibreerd "
    "(benaderend, niet bewezen), niet gebacktest.")


def _fetch_full_history():
    """Full daily price history. Kraken/CoinGecko free are range-capped (~720d / 365d),
    so blockchain.info's market-price chart (daily since 2010) is used for full history.
    Returns (closes, dates) oldest->newest; falls back to Kraken daily closes."""
    import requests
    try:
        r = requests.get("https://api.blockchain.info/charts/market-price",
                         params={"timespan": "all", "format": "json", "sampled": "false"},
                         timeout=60, headers={"User-Agent": "btc-bottom-radar/1.0"})
        vals = [v for v in r.json().get("values", []) if v.get("y", 0) > 0]
        if len(vals) > 1000:
            closes = [float(v["y"]) for v in vals]
            dates = [dt.datetime.utcfromtimestamp(v["x"]).date().isoformat() for v in vals]
            return closes, dates, "blockchain.info"
    except Exception as exc:  # noqa: BLE001
        log.warning("full history fetch failed: %s", exc)
    from .datasources import fetch_daily_closes
    closes, _ = fetch_daily_closes("kraken")
    return closes, [""] * len(closes), "kraken(720d-cap)"


def _roll_sma(c, p):
    out = [None] * len(c); s = 0.0
    for i, x in enumerate(c):
        s += x
        if i >= p:
            s -= c[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def _roll_ema(c, p):
    out = [None] * len(c); k = 2.0 / (p + 1); e = None
    for i, x in enumerate(c):
        if i == p - 1:
            e = sum(c[:p]) / p; out[i] = e
        elif i >= p:
            e = x * k + e * (1 - k); out[i] = e
    return out


def _roll_rsi(c, p=14):
    out = [None] * len(c)
    if len(c) < p + 1:
        return out
    g = sum(max(c[i] - c[i - 1], 0) for i in range(1, p + 1)) / p
    l = sum(max(c[i - 1] - c[i], 0) for i in range(1, p + 1)) / p
    out[p] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(p + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (p - 1) + max(d, 0)) / p
        l = (l * (p - 1) + max(-d, 0)) / p
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def _precompute(closes):
    n = len(closes)
    ath = [None] * n; m = 0.0
    for i, x in enumerate(closes):
        m = x if x > m else m
        ath[i] = m
    return {"sma200": _roll_sma(closes, 200), "sma471": _roll_sma(closes, 471),
            "ema150": _roll_ema(closes, 150), "rsi14": _roll_rsi(closes, 14),
            "ma200w": _roll_sma(closes, 1400), "ath": ath}


def _row_at(closes, pc, i, cfg) -> dict:
    """Price-only signal row + renormalized bottom_score (NEW weights/thresholds) for day i."""
    price = closes[i]
    sma200 = pc["sma200"][i]; sma471 = pc["sma471"][i]; ema150 = pc["ema150"][i]
    rsi14 = pc["rsi14"][i]; ma200w = pc["ma200w"][i]; ath = pc["ath"][i]
    dd = (1 - price / ath) * 100 if ath else None
    mayer = price / sma200 if sma200 else None
    ic = cfg["indicators"]
    at = {  # key -> (available, triggered) for the price-only universe
        "pi_cycle_bottom": (ema150 is not None and sma471 is not None,
                            bool(ema150 is not None and sma471 is not None and ema150 < sma471)),
        "ma_200w": (ma200w is not None, bool(ma200w is not None and price <= ma200w)),
        "mayer_multiple": (mayer is not None, bool(mayer is not None and mayer < ic["mayer_multiple"]["bottom_value"])),
        "rsi_14d": (rsi14 is not None, bool(rsi14 is not None and rsi14 < ic["rsi_14d"]["bottom_value"])),
        "drawdown_from_ath_pct": (dd is not None, bool(dd is not None and dd >= ic["drawdown_from_ath_pct"]["bottom_value"])),
    }
    possible = earned = 0; sig = []
    for key, (av, tr) in at.items():
        if av:
            possible += ic[key]["weight"]
            if tr:
                earned += ic[key]["weight"]; sig.append(key)
    score = round(100 * earned / possible) if possible else 0
    return {"price_usd": price, "sma_200d": sma200, "ma_200w": ma200w,
            "drawdown_from_ath_pct": dd, "mvrv_zscore": None, "fear_greed": None,
            "bottom_score": score, "tier": scoring._tier_for(score, cfg["score_tiers"]),
            "signals_triggered": sig, "mayer": mayer}


def _detect_cycles(closes, min_dd=50.0):
    """Return cycle dicts {peak_i,peak,bot_i,bot,rec_i,dd} for drawdowns >= min_dd%."""
    ath = closes[0]; ath_i = 0; emin = closes[0]; emin_i = 0
    cycles = []
    for i in range(1, len(closes)):
        p = closes[i]
        if p > ath:
            dd = (1 - emin / ath) * 100
            if dd >= min_dd and emin_i > ath_i:
                cycles.append({"peak_i": ath_i, "peak": ath, "bot_i": emin_i,
                               "bot": emin, "rec_i": i, "dd": dd})
            ath = p; ath_i = i; emin = p; emin_i = i
        elif p < emin:
            emin = p; emin_i = i
    dd = (1 - emin / ath) * 100
    if dd >= min_dd and emin_i > ath_i:
        cycles.append({"peak_i": ath_i, "peak": ath, "bot_i": emin_i,
                       "bot": emin, "rec_i": None, "dd": dd, "ongoing": True})
    return cycles


def _sim_cycle(closes, pc, cfg, ladder, cyc):
    """Run the new-spacing ladder over one cycle (price-only). Reset pending at bear
    entry (first close below SMA200d in the episode), run to recovery/end."""
    end = cyc["rec_i"] if cyc["rec_i"] is not None else len(closes) - 1
    bear = None
    for j in range(cyc["peak_i"], end + 1):
        s = pc["sma200"][j]
        if s is not None and closes[j] < s:
            bear = j; break
    if bear is None:
        return None
    budget = ladder["budget"]; cdays = ladder.get("confirm_days", 2)
    st = {t["id"]: {"status": "pending", "low": None, "streak": 0, "rule": t["value_rule"],
                    "rebound": t["confirm_rebound_pct"], "pct": t["pct"],
                    "fired_i": None, "fired_price": None, "reason": None}
          for t in ladder["tranches"]}
    for i in range(bear, end + 1):
        row = _row_at(closes, pc, i, cfg)
        price, sma200 = row["price_usd"], row["sma_200d"]
        for tid in sorted(st):
            tr = st[tid]
            if tr["status"] == "pending":
                if RULES[tr["rule"]](row):
                    tr.update(status="armed", low=price, streak=0)
            elif tr["status"] == "armed":
                if tr["low"] is None or price <= tr["low"]:
                    tr["low"], tr["streak"] = price, 0
                tr["streak"] = tr["streak"] + 1 if price >= tr["low"] * (1 + tr["rebound"] / 100.0) else 0
                if tr["streak"] >= cdays:
                    tr.update(status="fired", fired_i=i, fired_price=price, reason="confirmed")
        nonfired = [tid for tid in st if st[tid]["status"] != "fired"]
        if sma200 is not None and price > sma200 and nonfired:
            for tid in nonfired:
                st[tid].update(status="fired", fired_i=i, fired_price=price, reason="uptrend")
    fires = [(tid, st[tid]["fired_price"], st[tid]["reason"]) for tid in sorted(st)
             if st[tid]["status"] == "fired"]
    wsum = sum(amount_eur(budget, st[tid]["pct"]) for tid, _, _ in fires)
    avg = (sum(amount_eur(budget, st[tid]["pct"]) * pr for tid, pr, _ in fires) / wsum) if wsum else None
    return {"bear_price": closes[bear], "fires": fires, "avg": avg, "st": st}


def backtest(years: int) -> int:
    cfg = load_thresholds(); ladder = load_ladder()
    closes, dates, src = _fetch_full_history()
    print("\n" + _BT_DISCLAIMER + "\n")
    if not closes or len(closes) < 1500:
        print(f"Onvoldoende historische closes ({len(closes)}) — backtest afgebroken.")
        return 1
    pc = _precompute(closes)
    cycles = [c for c in _detect_cycles(closes, min_dd=50.0) if c["peak"] >= 100.0]
    print(f"Bron: {src} · {len(closes)} dagen ({dates[0]} → {dates[-1]}) · "
          f"{len(cycles)} drawdown-episodes ≥ 50% (piek ≥ $100; sommige zijn mid-cyclus "
          f"correcties, geen finale bodem).\n")

    # --- A) PER-CYCLE BOTTOM STATS (empirically checks the calibration) ---
    print("A) CYCLUS-BODEMS (prijs-afgeleid — toetst de kalibratie)")
    print(f"{'bodem-datum':<13}{'prijs$':>10}{'drawdown':>10}{'Mayer':>8}{'wkn<200wMA':>12}")
    print("-" * 56)
    for c in cycles:
        bi = c["bot_i"]
        mayer = closes[bi] / pc["sma200"][bi] if pc["sma200"][bi] else None
        end = c["rec_i"] if c["rec_i"] is not None else len(closes) - 1
        weeks_below = sum(1 for j in range(c["peak_i"], end + 1)
                          if pc["ma200w"][j] is not None and closes[j] < pc["ma200w"][j]) / 7.0
        tag = " (lopend)" if c.get("ongoing") else ""
        ddtxt = f"-{c['dd']:.0f}%"
        mtxt = f"{mayer:.2f}" if mayer else "n.b."
        print(f"{dates[bi]:<13}{_fmt0(closes[bi]):>10}{ddtxt:>10}{mtxt:>8}{weeks_below:>11.0f}{tag}")
    print("  (Mayer = prijs / SMA200d op de bodem. 'wkn<200wMA' = weken dat de koers in die "
          "cyclus onder de 200-weken-MA zat.)")

    # --- B) LADDER SIMULATION (new spacing, price-based arms) ---
    print("\nB) LADDER-SIMULATIE met de NIEUWE spacing (prijs-gebaseerde arming)")
    print("   per cyclus: gem. instap vs 'alles kopen bij bear-start (<200d-MA)' vs 'exacte bodem'")
    print("-" * 72)
    for c in cycles:
        res = _sim_cycle(closes, pc, cfg, ladder, c)
        label = dates[c["bot_i"]][:7] + (" (lopend)" if c.get("ongoing") else "")
        if not res:
            print(f"{label}: geen bear-fase gedetecteerd."); continue
        low = c["bot"]; bear = res["bear_price"]; avg = res["avg"]
        fired = ", ".join(f"T{tid}@{_fmt0(pr)}({rs[:4]})" for tid, pr, rs in res["fires"]) or "geen"
        if avg:
            vs_bear = (bear - avg) / bear * 100  # negative => ladder bought lower than bear-start
            vs_low = (avg / low - 1) * 100
            print(f"{label}: gem.instap ${_fmt0(avg)} · vs bear-start ${_fmt0(bear)} "
                  f"({vs_bear:+.0f}%) · vs bodem ${_fmt0(low)} (+{vs_low:.0f}%)")
        else:
            print(f"{label}: niets gevuurd · bodem ${_fmt0(low)}")
        print(f"        gevuurd: {fired}")

    print("\n" + _BT_DISCLAIMER)
    return 0


# --------------------------------------------------------------------------- CLI
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(description="BTC buy-ladder (notify-only, never trades)")
    p.add_argument("--status", action="store_true")
    p.add_argument("--preview", choices=["arm", "fire", "uptrend"])
    p.add_argument("--trap", type=int, default=1)
    p.add_argument("--send-test", action="store_true")
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--years", type=int, default=3)
    p.add_argument("--mark-bought", nargs=2, metavar=("TRAP_ID", "EUR"))
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--positions", action="store_true")
    args = p.parse_args()

    if args.preview:
        return preview(args.preview, args.trap, args.send_test)
    if args.backtest:
        return backtest(args.years)
    if args.mark_bought:
        return mark_bought(int(args.mark_bought[0]), float(args.mark_bought[1]),
                           args.price, args.note)
    if args.positions:
        return list_positions()
    return show_status()


if __name__ == "__main__":
    raise SystemExit(main())
