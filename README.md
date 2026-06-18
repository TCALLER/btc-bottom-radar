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

## Notes & stubs

- The Telegram bot token currently configured resolves to **@flowgenius_bot** (it reaches the target
  chat). To use a different bot, set `TELEGRAM_BOT_TOKEN` in `.env` and the repo secret.
- SOPR's "sustained ~14d" rule is approximated on the spot value (no intraday history is persisted).
- BTC now runs on its **own** Supabase project (`ajunjsegdeyqjtjllnxg`); the dashboard/bundle carry
  no shared-project key. Originally prototyped inside a shared project, since migrated out and that
  project restored to baseline — see `DECISIONS.md` (migration section + before/after diffs).
