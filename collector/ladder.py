"""Buy-ladder engine — signal-driven tranche triggers, notify-only.

It NEVER trades and NEVER says "koop": it tells the human a tranche condition is
met and lets them decide. Rules are explicit Python predicates (no eval).

PRIVACY: the budget and plan are personal financial info. `btc.ladder_state` has
NO anon policy and is never exposed on the public dashboard — the ladder lives in
Telegram + this CLI only.

CLI:
  python -m collector.ladder --status
  python -m collector.ladder --simulate --score 76
  python -m collector.ladder --simulate --set mvrv_zscore=0.05 --set fear_greed=8
  python -m collector.ladder --simulate --score 62 --send-test
Simulation builds a synthetic row from the latest real row + overrides, recomputes
tiers from config, evaluates the rules and prints a table. It NEVER writes to the DB.
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

log = setup_logging()

LADDER_PATH = PROJECT_ROOT / "config" / "ladder.json"


# --------------------------------------------------------------------------- config
def load_ladder() -> dict:
    with open(LADDER_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def amount_eur(budget: float, pct: float) -> float:
    return round(budget * pct / 100.0, 2)


def fmt_eur(amount: float) -> str:
    """NL notation: thousands separated by '.', no decimals (e.g. 3036.3 -> '3.036')."""
    return f"{round(amount):,}".replace(",", ".")


# --------------------------------------------------------------------------- predicates
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


def _why(rule: str, row: dict) -> str:
    """Human-readable explanation of a rule's evaluation against a row."""
    score = row.get("bottom_score")
    tier = row.get("tier")
    if rule == "tier_naderend_and_ma200w":
        return f"tier={tier}, ma_200w in signalen={'ma_200w' in (row.get('signals_triggered') or [])}"
    if rule == "score_gte_60":
        return f"bottom_score={score} (>=60?)"
    if rule == "score_gte_75_or_capitulation":
        return (f"score={score} (>=75?), mvrv={row.get('mvrv_zscore')} (<=0.1?), "
                f"drawdown={row.get('drawdown_from_ath_pct')} (>=75?), fg={row.get('fear_greed')} (<=10?)")
    return rule


# --------------------------------------------------------------------------- decision (pure)
def decide(row: dict, state_by_id: dict, ladder: dict) -> list[dict]:
    """Pure: per tranche, evaluate its rule and whether it WOULD fire now.
    would_fire = rule true AND not already fired. No IO, no side effects."""
    budget = ladder["budget"]
    out = []
    for tr in ladder["tranches"]:
        rule = tr["rule"]
        rule_true = RULES[rule](row)
        st = state_by_id.get(tr["id"], {})
        already = (st.get("status") == "fired")
        out.append({
            "id": tr["id"],
            "label": tr["label"],
            "pct": tr["pct"],
            "amount": amount_eur(budget, tr["pct"]),
            "rule": rule,
            "rule_true": rule_true,
            "already_fired": already,
            "would_fire": bool(rule_true and not already),
            "why": _why(rule, row),
        })
    return out


# --------------------------------------------------------------------------- DB helpers
def fetch_state(client) -> dict:
    resp = client.table("ladder_state").select("*").execute()
    return {r["tranche_id"]: r for r in (resp.data or [])}


def seed_state(client, ladder: dict) -> dict:
    """Insert missing tranches as pending. Never resets a fired row (pct/budget
    unchanged → leave existing rows untouched)."""
    existing = fetch_state(client)
    budget = ladder["budget"]
    for tr in ladder["tranches"]:
        if tr["id"] not in existing:
            client.table("ladder_state").insert({
                "tranche_id": tr["id"], "label": tr["label"], "pct": tr["pct"],
                "amount_eur": amount_eur(budget, tr["pct"]), "status": "pending",
                "rule": tr["rule"],
            }).execute()
            log.info("seeded ladder tranche %s", tr["id"])
    return fetch_state(client)


# --------------------------------------------------------------------------- live evaluate
def evaluate(row: dict, *, dry_run: bool = False, client=None) -> list[dict]:
    """Evaluate the ladder against a real persisted row. Fires each pending
    tranche whose rule is now true exactly once (Telegram + DB write). Idempotent.
    When dry_run=True, performs no DB writes and sends nothing."""
    ladder = load_ladder()
    if dry_run:
        state = fetch_state(client) if client is not None else {}
        return decide(row, state, ladder)

    if client is None:
        client = db.get_client()
    state = seed_state(client, ladder)
    decisions = decide(row, state, ladder)

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for d in decisions:
        if not d["would_fire"]:
            continue
        amount = fmt_eur(d["amount"])
        msg = (f"🪜 <b>Ladder</b> — {d['label']} bereikt. "
               f"Overweeg ~€{amount} inzetten "
               f"(score {row.get('bottom_score')}, {row.get('tier')}). "
               f"Jouw beslissing — geen koopopdracht.")
        delivered = tg.send_message(msg)
        client.table("ladder_state").update({
            "status": "fired", "fired_at": now_iso, "fired_on_date": today,
            "fired_price_usd": row.get("price_usd"), "fired_score": row.get("bottom_score"),
        }).eq("tranche_id", d["id"]).execute()
        db.insert_alert(client, {
            "alert_type": "ladder", "tier": row.get("tier"),
            "bottom_score": row.get("bottom_score"), "message": msg,
            "payload": {"tranche_id": d["id"], "amount_eur": d["amount"]},
            "delivered": delivered,
        })
        log.info("ladder tranche %s FIRED (delivered=%s)", d["id"], delivered)
    return decisions


# --------------------------------------------------------------------------- simulation
def _coerce(v: str):
    low = v.strip().lower()
    if low in ("none", "null", ""):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def build_synthetic_row(cfg: dict, score: int | None, sets: list[str], client) -> dict:
    """Latest real row + overrides. Recomputes bottom/top tiers from config so
    the synthetic row is internally consistent."""
    base = {}
    try:
        latest = db.fetch_last_snapshot(client)
        if latest:
            base = dict(latest)
    except Exception as exc:  # noqa: BLE001 - simulation must work offline-ish
        log.warning("could not load latest row for simulation: %s", exc)

    row = dict(base)
    if score is not None:
        row["bottom_score"] = score
    for pair in sets or []:
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        row[k.strip()] = _coerce(v)

    # keep tiers consistent with (possibly overridden) scores
    if row.get("bottom_score") is not None:
        row["tier"] = scoring._tier_for(int(row["bottom_score"]), cfg["score_tiers"])
    if row.get("top_score") is not None:
        row["top_tier"] = scoring._tier_for(int(row["top_score"]), cfg["top_score_tiers"])
    return row


def print_decision_table(decisions: list[dict]) -> None:
    print("\nLadder-simulatie — welke trappen zouden NU vuren?")
    print(f"{'#':<3}{'trap':<46}{'€':>8}  rule_true  fired  WOULD_FIRE")
    print("-" * 90)
    for d in decisions:
        print(f"{d['id']:<3}{d['label'][:44]:<46}{fmt_eur(d['amount']):>8}  "
              f"{str(d['rule_true']):<9}  {str(d['already_fired']):<5}  "
              f"{'JA' if d['would_fire'] else 'nee'}")
        print(f"     -> {d['why']}")


def simulate(score: int | None, sets: list[str], send_test: bool) -> int:
    cfg = load_thresholds()
    client = db.get_client()  # read-only use here (SELECT only)
    row = build_synthetic_row(cfg, score, sets, client)

    state = fetch_state(client)  # read-only
    decisions = decide(row, state, load_ladder())
    print_decision_table(decisions)

    # top-radar view of the synthetic row (driven by overridden top_score/top_tier)
    top_score = row.get("top_score")
    top_tier = row.get("top_tier")
    print(f"\nTop-radar (synthetisch): top_score={top_score} tier={top_tier}")

    if send_test:
        would = [d for d in decisions if d["would_fire"]]
        lines = ["🧪 <b>SIMULATIE</b> — geen echte trigger",
                 f"Bodem: score {row.get('bottom_score')} ({row.get('tier')})"]
        if would:
            for d in would:
                lines.append(f"🪜 Zou vuren: {d['label']} (~€{fmt_eur(d['amount'])})")
        else:
            lines.append("🪜 Geen ladder-trap zou nu vuren.")
        if top_score is not None:
            tier_emoji = cfg["top_score_tiers"].get(top_tier, {}).get("emoji", "")
            lines.append(f"📈 Top-radar: {tier_emoji} {top_score}/100 ({top_tier})")
        lines.append("(test, geen echte trigger)")
        ok = tg.send_message("\n".join(lines))
        print(f"\n🧪 SIMULATIE Telegram verzonden: {ok}")
    return 0


def show_status() -> int:
    client = db.get_client()
    ladder = load_ladder()
    state = seed_state(client, ladder)
    print(f"\nBuy-ladder — {ladder['asset']} budget €{fmt_eur(ladder['budget'])} ({ladder['currency']})")
    print(f"{'#':<3}{'trap':<46}{'€':>8}  {'status':<8} fired_on")
    print("-" * 80)
    for tr in ladder["tranches"]:
        st = state.get(tr["id"], {})
        print(f"{tr['id']:<3}{tr['label'][:44]:<46}"
              f"{fmt_eur(amount_eur(ladder['budget'], tr['pct'])):>8}  "
              f"{st.get('status', 'pending'):<8} {st.get('fired_on_date') or '-'}")
    return 0


def main() -> int:
    # Emit UTF-8 regardless of console codepage (Windows cp1252 would choke on €/emoji).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(description="BTC buy-ladder (notify-only, never trades)")
    p.add_argument("--status", action="store_true", help="show ladder tranche state")
    p.add_argument("--simulate", action="store_true", help="dry-run: which tranches WOULD fire")
    p.add_argument("--score", type=int, default=None, help="override bottom_score for simulation")
    p.add_argument("--set", action="append", default=[], dest="sets",
                   help="override a row field, e.g. --set mvrv_zscore=0.05 (repeatable)")
    p.add_argument("--send-test", action="store_true",
                   help="also send a labelled 🧪 SIMULATIE Telegram (test only)")
    args = p.parse_args()

    if args.simulate:
        return simulate(args.score, args.sets, args.send_test)
    if args.status:
        return show_status()
    # default: show status
    return show_status()


if __name__ == "__main__":
    raise SystemExit(main())
