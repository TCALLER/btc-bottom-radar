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


def rule_tier_naderend_and_ma200w(row: dict) -> bool:
    return row.get("tier") == "naderend" and "ma_200w" in (row.get("signals_triggered") or [])


def rule_score_gte_60(row: dict) -> bool:
    return (row.get("bottom_score") or 0) >= 60


def rule_score_gte_75_or_capitulation(row: dict) -> bool:
    score = row.get("bottom_score") or 0
    mvrv = _f(row.get("mvrv_zscore"))
    dd = _f(row.get("drawdown_from_ath_pct"))
    fg = row.get("fear_greed")
    return bool(
        score >= 75
        or (mvrv is not None and mvrv <= 0.1)
        or (dd is not None and dd >= 75)
        or (fg is not None and fg <= 10)
    )


RULES = {
    "tier_naderend_and_ma200w": rule_tier_naderend_and_ma200w,
    "score_gte_60": rule_score_gte_60,
    "score_gte_75_or_capitulation": rule_score_gte_75_or_capitulation,
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
_BT_TOP = ("⚠️ Backtest gebruikt enkel prijs-gebaseerde signalen (geen historische on-chain/"
           "sentiment), dus Trap 2/3-arming is benaderend; het bevestigings-mechanisme (puur "
           "prijs) wordt wel getrouw getest.")


def _price_only_row(closes: list[float], i: int, cfg: dict) -> dict:
    """Compute the price-only signals + renormalized bottom_score for day i."""
    window = closes[: i + 1]
    price = closes[i]
    sma200 = ta.sma(window, 200)
    sma471 = ta.sma(window, 471)
    ema150 = ta.ema(window, 150)
    rsi14 = ta.rsi(window, 14)
    ath = max(window)
    dd = (1 - price / ath) * 100 if ath else None
    mayer = price / sma200 if sma200 else None

    icfg = cfg["indicators"]
    # availability + trigger for the price-only universe
    avail_trig = {}  # key -> (available, triggered)
    avail_trig["pi_cycle_bottom"] = (ema150 is not None and sma471 is not None,
                                     bool(ema150 is not None and sma471 is not None and ema150 < sma471))
    avail_trig["mayer_multiple"] = (mayer is not None, bool(mayer is not None and mayer < icfg["mayer_multiple"]["bottom_value"]))
    avail_trig["rsi_14d"] = (rsi14 is not None, bool(rsi14 is not None and rsi14 < icfg["rsi_14d"]["bottom_value"]))
    avail_trig["drawdown_from_ath_pct"] = (dd is not None, bool(dd is not None and dd >= icfg["drawdown_from_ath_pct"]["bottom_value"]))
    # ma_200w / fear_greed / on-chain treated as UNAVAILABLE (no history here)

    possible = earned = 0
    signals = []
    for key, (av, tr) in avail_trig.items():
        if av:
            w = icfg[key]["weight"]
            possible += w
            if tr:
                earned += w
                signals.append(key)
    score = round(100 * earned / possible) if possible else 0
    tier = scoring._tier_for(score, cfg["score_tiers"])
    return {"price_usd": price, "sma_200d": sma200, "ma_200w": None,
            "drawdown_from_ath_pct": dd, "mvrv_zscore": None, "fear_greed": None,
            "bottom_score": score, "tier": tier, "signals_triggered": signals}


def backtest(years: int) -> int:
    from .datasources import fetch_daily_closes
    cfg = load_thresholds()
    ladder = load_ladder()
    budget = ladder["budget"]
    closes, src = fetch_daily_closes("kraken")
    print("\n" + _BT_TOP + "\n")
    if not closes or len(closes) < 220:
        print(f"Onvoldoende historische closes ({len(closes)}) — backtest afgebroken.")
        return 1

    start = max(200, len(closes) - years * 365)
    window_closes = closes[start:]
    win_low = min(window_closes)
    day1_price = closes[start]

    # in-memory ladder state
    st = {t["id"]: {"status": "pending", "low": None, "streak": 0,
                    "rule": t["value_rule"], "rebound": t["confirm_rebound_pct"],
                    "pct": t["pct"], "label": t["label"],
                    "armed_i": None, "fired_i": None, "fire_reason": None,
                    "armed_price": None, "fired_price": None}
          for t in ladder["tranches"]}
    cdays = ladder.get("confirm_days", 2)
    buys = []  # (tranche_id, price, reason)

    for i in range(start, len(closes)):
        row = _price_only_row(closes, i, cfg)
        price, sma200 = row["price_usd"], row["sma_200d"]
        for tid in sorted(st):
            tr = st[tid]
            if tr["status"] == "pending":
                if RULES[tr["rule"]](row):
                    tr.update(status="armed", low=price, streak=0, armed_i=i, armed_price=price)
            elif tr["status"] == "armed":
                if tr["low"] is None or price <= tr["low"]:
                    tr["low"], tr["streak"] = price, 0
                threshold = tr["low"] * (1 + tr["rebound"] / 100.0)
                tr["streak"] = tr["streak"] + 1 if price >= threshold else 0
                if tr["streak"] >= cdays:
                    tr.update(status="fired", fired_i=i, fired_price=price, fire_reason="confirmed")
                    buys.append((tid, price, "confirmed"))
        nonfired = [tid for tid in st if st[tid]["status"] != "fired"]
        if sma200 is not None and price > sma200 and nonfired:
            for tid in nonfired:
                st[tid].update(status="fired", fired_i=i, fired_price=price, fire_reason="uptrend")
                buys.append((tid, price, "uptrend"))

    def dstr(i):
        return f"day+{i - start}" if i is not None else "—"

    print(f"Window: laatste {years}j → {len(window_closes)} dagen (van index {start}).")
    print(f"{'trap':<22}{'armed':>10}{'@armed$':>10}{'fired':>10}{'@fired$':>10}  reason")
    print("-" * 78)
    for tid in sorted(st):
        tr = st[tid]
        print(f"{tr['label'][:20]:<22}{dstr(tr['armed_i']):>10}{_fmt0(tr['armed_price']):>10}"
              f"{dstr(tr['fired_i']):>10}{_fmt0(tr['fired_price']):>10}  {tr['fire_reason'] or '-'}")

    # SUMMARY
    deployed = sum(amount_eur(budget, st[tid]["pct"]) for tid, _, _ in buys)
    if buys:
        wsum = sum(amount_eur(budget, st[tid]["pct"]) for tid, _, _ in buys)
        avg = sum(amount_eur(budget, st[tid]["pct"]) * p for tid, p, _ in buys) / wsum
    else:
        avg = None
    print("\nSAMENVATTING")
    print(f"  Aankopen: {len(buys)}  ·  Ingezet: €{fmt_eur(deployed)}  ·  "
          f"Gem. instap: {('$' + _fmt0(avg)) if avg else 'n.v.t.'}")
    print(f"  Vergelijk: hele budget op dag 1 → ${_fmt0(day1_price)};  "
          f"laagste close in window → ${_fmt0(win_low)}")
    if avg:
        vs_day1 = (day1_price / avg - 1) * 100
        vs_low = (avg / win_low - 1) * 100
        print(f"  Gem. instap vs dag-1: {vs_day1:+.1f}%  ·  vs laagste close: {vs_low:+.1f}% hoger")
    print("\n" + _BT_TOP)
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
