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
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sp500.json")
_CACHE_TTL = timedelta(days=7)
_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Wikipedia rejects pandas' default urllib User-Agent with 403. Their policy
# asks for a descriptive UA identifying the app + contact, hence the repo URL.
_USER_AGENT = "rsi-bb-bot/1.0 (https://github.com/jmschmitz12/rsi-bb-bot)"


def _is_cache_stale() -> bool:
    if not os.path.exists(_CACHE_FILE):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(_CACHE_FILE))
    return datetime.now() - mtime > _CACHE_TTL


def _fetch_from_wikipedia() -> tuple[list[str], dict[str, str]]:
    response = requests.get(_WIKIPEDIA_URL, headers={"User-Agent": _USER_AGENT}, timeout=15)
    response.raise_for_status()
    # pandas 2.x rejects raw HTML strings as a path — wrap in StringIO.
    tables = pd.read_html(StringIO(response.text))
    table = tables[0]
    symbols = table["Symbol"].astype(str).str.strip().str.replace(".", "-", regex=False)
    names = table["Security"].astype(str).str.strip()
    tickers = symbols.tolist()
    name_map = dict(zip(symbols, names))
    return tickers, name_map


def _load_cache() -> tuple[list[str], dict[str, str]]:
    with open(_CACHE_FILE) as f:
        data = json.load(f)
    # Support old cache format (plain list) gracefully.
    if isinstance(data, list):
        return data, {}
    return data["tickers"], data.get("names", {})


def get_sp500_tickers(force_refresh: bool = False) -> list[str]:
    """
    Return the current S&P 500 ticker list.

    Refreshes from Wikipedia if the cache is missing or older than 7 days.
    On fetch failure, falls back to the cached list if one exists.
    """
    if force_refresh or _is_cache_stale():
        try:
            tickers, names = _fetch_from_wikipedia()
            with open(_CACHE_FILE, "w") as f:
                json.dump({"tickers": tickers, "names": names}, f, indent=2)
            logger.info("S&P 500 list refreshed from Wikipedia: %d tickers", len(tickers))
            return tickers
        except Exception as e:
            logger.warning("S&P 500 fetch failed (%s) — falling back to cache", e)

    if os.path.exists(_CACHE_FILE):
        tickers, _ = _load_cache()
        return tickers

    raise RuntimeError("No cached S&P 500 list available and Wikipedia fetch failed.")


def get_sp500_names() -> dict[str, str]:
    """Return a ticker → company name mapping from the cached S&P 500 list."""
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        _, names = _load_cache()
        return names
    except Exception:
        logger.warning("Could not load S&P 500 names from cache")
        return {}
