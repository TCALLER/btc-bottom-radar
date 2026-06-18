-- BTC Bottom Radar — canonical schema for the dedicated Supabase project
-- (ref ajunjsegdeyqjtjllnxg, schema `btc`). Idempotent: safe to re-run.
--
-- This file is the source-of-truth. It was originally applied via the Supabase
-- MCP; keep it in sync with any future change. The live DB already matches it.
--
-- PRIVACY: btc.indicators / btc.latest are anon-readable (non-personal market
-- data). btc.alerts and btc.ladder_state are PRIVATE — no anon policy/grant —
-- because the ladder carries the owner's personal budget/plan.

create schema if not exists btc;

-- ---------------------------------------------------------------------------
-- Daily snapshot: bottom radar + symmetric top radar (one row per UTC date)
-- ---------------------------------------------------------------------------
create table if not exists btc.indicators (
  id bigint generated always as identity primary key,
  captured_date date not null unique,
  captured_at timestamptz not null default now(),
  price_usd numeric, all_time_high_usd numeric, drawdown_from_ath_pct numeric,
  ema_150d numeric, sma_471d numeric, sma_200d numeric, ma_200w numeric,
  mayer_multiple numeric, rsi_14d numeric, pi_cycle_bottom boolean,
  fear_greed integer, mvrv_zscore numeric, sopr numeric, supply_profit_pct numeric,
  triggered_count integer not null default 0, available_count integer not null default 0,
  bottom_score integer not null default 0, tier text not null default 'neutraal',
  signals_triggered jsonb not null default '[]'::jsonb,
  indicators_detail jsonb not null default '{}'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  -- top / sell radar (additive)
  rsi_14w numeric,
  pi_cycle_top boolean,
  nupl numeric,
  puell_multiple numeric,
  top_score integer not null default 0,
  top_tier text not null default 'neutraal',
  top_signals_triggered jsonb not null default '[]'::jsonb
);
create index if not exists btc_indicators_date_idx on btc.indicators (captured_date desc);

-- Backfill the top columns on a pre-existing table (no-op on a fresh create).
alter table btc.indicators
  add column if not exists rsi_14w numeric,
  add column if not exists pi_cycle_top boolean,
  add column if not exists nupl numeric,
  add column if not exists puell_multiple numeric,
  add column if not exists top_score integer not null default 0,
  add column if not exists top_tier text not null default 'neutraal',
  add column if not exists top_signals_triggered jsonb not null default '[]'::jsonb;

-- ---------------------------------------------------------------------------
-- Alert log (PRIVATE — no anon access)
-- ---------------------------------------------------------------------------
create table if not exists btc.alerts (
  id bigint generated always as identity primary key,
  sent_at timestamptz not null default now(),
  alert_type text not null, tier text, bottom_score integer,
  message text not null, payload jsonb not null default '{}'::jsonb,
  delivered boolean not null default false
);
create index if not exists btc_alerts_sent_idx on btc.alerts (sent_at desc);

-- ---------------------------------------------------------------------------
-- Buy-ladder state (PRIVATE — personal budget/plan; no anon policy/grant)
-- ---------------------------------------------------------------------------
create table if not exists btc.ladder_state (
  tranche_id int primary key, label text not null, pct numeric not null, amount_eur numeric not null,
  status text not null default 'pending', fired_at timestamptz, fired_on_date date,
  fired_price_usd numeric, fired_score int, rule text not null
);

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------
alter table btc.indicators   enable row level security;
alter table btc.alerts       enable row level security;
alter table btc.ladder_state enable row level security;

-- Only btc.indicators is anon-readable.
drop policy if exists "anon_read_btc_indicators" on btc.indicators;
create policy "anon_read_btc_indicators" on btc.indicators for select to anon using (true);
-- btc.alerts and btc.ladder_state intentionally have NO anon policy.

-- ---------------------------------------------------------------------------
-- Grants
-- ---------------------------------------------------------------------------
grant usage on schema btc to anon;
grant select on btc.indicators to anon;

-- service_role (collector writes) — full access inside the btc schema only.
grant usage on schema btc to service_role;
grant all privileges on all tables in schema btc to service_role;
grant all privileges on all sequences in schema btc to service_role;
alter default privileges in schema btc grant all on tables to service_role;
alter default privileges in schema btc grant all on sequences to service_role;

-- ---------------------------------------------------------------------------
-- Latest snapshot view.
--   * WITH (security_invoker = true): the view runs with the *querying* role's
--     privileges + RLS, not the view owner's. This keeps it from becoming a
--     SECURITY DEFINER bypass (Supabase advisor 0010) and matches the live DB.
--   * `select *` is deliberate, but a view created with `*` freezes its column
--     list at creation time — so this MUST be (re)created AFTER every column
--     added to btc.indicators. Always recreate the view from THIS file.
-- ---------------------------------------------------------------------------
create or replace view btc.latest
  with (security_invoker = true)
  as select * from btc.indicators order by captured_date desc limit 1;
grant select on btc.latest to anon;
