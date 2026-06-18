"""Runtime configuration loading. Thresholds live in config/thresholds.json —
no magic numbers in code."""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (parent of this package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

THRESHOLDS_PATH = PROJECT_ROOT / "config" / "thresholds.json"


@lru_cache(maxsize=1)
def load_thresholds() -> dict:
    """Load and cache the thresholds config."""
    with open(THRESHOLDS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def setup_logging() -> logging.Logger:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger("btc-bottom-radar")
