"""Supabase persistence. Writes one row per UTC date into btc.indicators and
logs alerts into btc.alerts. The client is configured with schema='btc' and uses
the service_role key (writes bypass RLS; the dashboard reads with anon)."""
from __future__ import annotations

import logging
import os

from supabase import Client, create_client
from supabase.client import ClientOptions

log = logging.getLogger("btc-bottom-radar.persist")


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_SERVICE_KEY"].strip()
    schema = os.environ.get("SUPABASE_DB_SCHEMA", "btc").strip()
    options = ClientOptions(schema=schema)
    return create_client(url, key, options=options)


def fetch_last_snapshot(client: Client, before_date: str | None = None) -> dict | None:
    """Most recent indicators row, for alert diffing. When before_date is given,
    return the latest row strictly before that date (so a same-day re-run still
    compares against the previous day, not the row it just wrote)."""
    query = client.table("indicators").select("*")
    if before_date is not None:
        query = query.lt("captured_date", before_date)
    resp = query.order("captured_date", desc=True).limit(1).execute()
    rows = resp.data or []
    return rows[0] if rows else None


def upsert_indicators(client: Client, row: dict) -> dict:
    """Upsert one row keyed on captured_date (unique)."""
    resp = (
        client.table("indicators")
        .upsert(row, on_conflict="captured_date")
        .execute()
    )
    data = resp.data or []
    log.info("upserted indicators row for %s", row.get("captured_date"))
    return data[0] if data else {}


def insert_alert(client: Client, alert: dict) -> None:
    client.table("alerts").insert(alert).execute()
    log.info("logged alert: %s", alert.get("alert_type"))
