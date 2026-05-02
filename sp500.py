"""
sp500.py
────────
Fetches and caches the current S&P 500 ticker list from Wikipedia.
Used by the !scan sp500 command.

The list is cached to sp500.json for 7 days so we don't hit Wikipedia on
every scan. Symbols with dots (e.g. BRK.B) are normalized to dashes
(BRK-B) — that's the form yfinance expects.
"""

import json
import logging
import os
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sp500.json")
_CACHE_TTL = timedelta(days=7)
_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _is_cache_stale() -> bool:
    if not os.path.exists(_CACHE_FILE):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(_CACHE_FILE))
    return datetime.now() - mtime > _CACHE_TTL


def _fetch_from_wikipedia() -> list[str]:
    tables = pd.read_html(_WIKIPEDIA_URL)
    symbols = tables[0]["Symbol"].astype(str).tolist()
    return [s.strip().replace(".", "-") for s in symbols]


def get_sp500_tickers(force_refresh: bool = False) -> list[str]:
    """
    Return the current S&P 500 ticker list.

    Refreshes from Wikipedia if the cache is missing or older than 7 days.
    On fetch failure, falls back to the cached list if one exists.
    """
    if force_refresh or _is_cache_stale():
        try:
            tickers = _fetch_from_wikipedia()
            with open(_CACHE_FILE, "w") as f:
                json.dump(tickers, f, indent=2)
            logger.info("S&P 500 list refreshed from Wikipedia: %d tickers", len(tickers))
            return tickers
        except Exception as e:
            logger.warning("S&P 500 fetch failed (%s) — falling back to cache", e)

    if os.path.exists(_CACHE_FILE):
        with open(_CACHE_FILE) as f:
            return json.load(f)

    raise RuntimeError("No cached S&P 500 list available and Wikipedia fetch failed.")
