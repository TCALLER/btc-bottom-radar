# BTC Bottom Radar

A daily Bitcoin **cycle-bottom monitor**. It pulls historically reliable bottom indicators, scores how
many sit in their "bottom zone" (0–100 + tier), stores each day's snapshot in Supabase, alerts on
Telegram when the picture changes, and serves a small read-only React dashboard.

> ⚠️ **Monitoring tool, not financial advice.** It never places orders and never says "buy".
> Cycle bottoms can only be confirmed in hindsight — this measures *tilt toward a bottom*, nothing more.

**Live dashboard:** https://tcaller.github.io/btc-bottom-radar/ — data-driven, refreshes on page load.

## Architecture

```
Kraken / alternative.me / (optional on-chain)
        │  fetch (timeout + retry, degrade gracefully)
        ▼
collector/  ──► scoring ──► Supabase btc.indicators (service_role, upsert/day)
        │                         │
        ├──► Telegram (Dutch) ◄───┘  alert on change + daily digest, logged to btc.alerts
        ▼
dashboard/  ──► reads btc.latest + btc.indicators via anon key (RLS: select only)
```

- **Database**: schema `btc` in a **dedicated** Supabase project `btc-bottom-radar`
  (ref `ajunjsegdeyqjtjllnxg`). The dashboard's public anon key is isolated to this BTC-only project.
- **Schedule**: GitHub Actions, daily `30 5 * * *` UTC (~07:30 Europe/Brussels).

## Indicators (config/thresholds.json)

Price/sentiment (always on): Pi-Cycle Bottom (EMA150<SMA471), 200-week MA, Mayer Multiple (<0.8),
RSI(14) daily (<30), drawdown from ATH (≥75%), Fear & Greed (≤10). On-chain (now live via
bitcoin-data.com free tier; excluded from the score only if a fetch fails): MVRV Z-Score (≤0.1),
SOPR (<1.0), supply-in-profit % (≤55).

Score = `round(100 * Σ weight(available & triggered) / Σ weight(available))`. Tiers: `neutraal` ⚪,
`watch` 🟡, `naderend` 🟠, `sterke_bodem_confluentie` 🔴.

## Local usage

```bash
python -m venv venv && ./venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source venv/bin/activate                                                         # *nix
python -m pytest -q                 # unit tests for the TA math
python -m collector.main            # collect + persist + alert-on-change
python -m collector.main --digest   # also send a Telegram status digest
```

Environment lives in `.env` (see `.env.example`). On-chain is **enabled** via the free
bitcoin-data.com source (`ONCHAIN_PROVIDER=bitcoin_data`) — MVRV Z-Score, SOPR and supply-in-profit %
(the last derived as `supplyProfitBtc / circulating_supply × 100`). Set `none` to disable, or
`glassnode` + `GLASSNODE_API_KEY` for the paid provider. The free source allows ~10 requests/hour;
the daily run uses ~4 and degrades gracefully (on-chain marked unavailable) if rate-limited.

## Dashboard

```bash
cd dashboard
npm install
npm run build        # outputs dist/
npm run preview      # local preview server
```

Reads `dashboard/.env.local` (`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_DB_SCHEMA=btc`).
The dashboard shows **two gauges** — "Bodem" (bottom) and "Top" — plus per-radar indicator tables and
a dual score-over-time chart. The buy ladder and budget are **never** shown (privacy).

## Top / sell radar

A symmetric counterpart to the bottom radar (`collector/top_radar.py`, `top_indicators` in config):
Pi-Cycle Top (SMA111 ≥ 2×SMA350), MVRV Z-Score ≥ 7, Mayer Multiple > 2.4, weekly RSI > 80,
Fear & Greed ≥ 90, NUPL > 0.75, Puell Multiple > 4. `top_score = round(100·Σweight(available &
triggered)/Σweight(available))`; tiers `neutraal`/`watch`/`verhit`/`sterke_top_confluentie`. It
**alerts only** (tilt toward a top, honest framing, Belgian meerwaarde-timing in mind) — it never
builds a fixed-euro sell ladder and never says "verkoop".

## Buy ladder (notify-only — never trades)

A signal-driven buy ladder lives **in Telegram + CLI only** — its budget/plan are personal and have
**no anon policy** (never exposed on the public dashboard). Config in `config/ladder.json`
(budget + 3 tranches 30/30/40%). Rules are explicit predicates (no `eval`). When a tranche's rule
becomes true on a daily run it sends one Dutch "overweeg ~€X inzetten — jouw beslissing, geen
koopopdracht" alert and marks itself fired (idempotent).

```bash
python -m collector.ladder --status                                   # tranche state
python -m collector.ladder --simulate --score 76                      # which tranches WOULD fire (no DB write)
python -m collector.ladder --simulate --set mvrv_zscore=0.05 --set fear_greed=8
python -m collector.ladder --simulate --score 62 --send-test          # also sends a 🧪 SIMULATIE test ping
```

Simulation is **side-effect-free**: it builds a synthetic row from the latest real row + overrides,
recomputes tiers from config, prints a table, and never touches `btc.ladder_state`.

## Telegram messages (action-led)

Messages lead with **meaning + action**, not an indicator dump. `next_action()` derives the
plain-language bottom/top state, a "what to do" line, and the nearest *pending* ladder trap (with
its human condition, price level and distance). The **digest** has fixed sections — header
(date · price · % under ATH), "Stand van zaken", "Wat moet jij nu doen?", "Jouw ladder" (private —
budget shown only in Telegram), and the ✅/➖/⚪ signal lists last — plus a ⚠️ line when on-chain is
temporarily unavailable. The **change** message is a short header + the tier/score delta + one
"🎯 Actie" line. A fired ladder trap sends a "🪜🔔 KOOPMOMENT" message (condition met, amount,
remaining traps); the top radar sends "📈🔔 TOP-RADAR — let op". None of them is ever a buy/sell order.

## Database schema

`db/schema.sql` is the canonical, idempotent source-of-truth for the `btc` schema (tables, RLS,
grants, and the `btc.latest` view). The view is declared `WITH (security_invoker = true)` so it
runs with the caller's RLS rather than the owner's (no SECURITY DEFINER bypass). Because the view
uses `select *`, **always recreate it from this file after adding any column** to `btc.indicators`.

## Notes & stubs

- The Telegram bot token currently configured resolves to **@flowgenius_bot** (it reaches the target
  chat). To use a different bot, set `TELEGRAM_BOT_TOKEN` in `.env` and the repo secret.
- SOPR's "sustained ~14d" rule is approximated on the spot value (no intraday history is persisted).
- BTC now runs on its **own** Supabase project (`ajunjsegdeyqjtjllnxg`); the dashboard/bundle carry
  no shared-project key. Originally prototyped inside a shared project, since migrated out and that
  project restored to baseline — see `DECISIONS.md` (migration section + before/after diffs).
