import json
import logging
import os
from datetime import datetime, timedelta

import pytz

from config import ALERT_PRICES_FILE, DEFAULT_WATCHLIST, MUTES_FILE, TIMEZONE, WATCHLIST_FILE

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone(TIMEZONE)

# Two prices that render identically in an alert card (2 decimal places) are
# treated as the same price.
_PRICE_EPSILON = 0.005


def _session_end() -> datetime:
    """
    Naive local datetime for today's 4:05 PM ET, matching the naive
    datetime.now() used everywhere else in this module.
    """
    now_et = datetime.now(EASTERN)
    close_et = now_et.replace(hour=16, minute=5, second=0, microsecond=0)
    if close_et <= now_et:
        close_et += timedelta(days=1)
    return close_et.astimezone().replace(tzinfo=None)


class BotState:
    """
    Single source of truth for all mutable bot state.

    Attach one instance to the bot object in main.py:
        bot.state = BotState()

    All cogs access it via self.bot.state.
    """

    def __init__(self) -> None:
        self.watchlist: list[str] = self._load_watchlist()
        self.paused_until: datetime | None = None
        self.rate_limit_cooldown: bool = False
        self.ticker_mutes: dict[str, datetime] = self._load_mutes()
        self.last_alert_price: dict[str, float] = self._load_alert_prices()

    # ── Watchlist persistence ──────────────────────────────────────────────────

    def _load_watchlist(self) -> list[str]:
        if os.path.exists(WATCHLIST_FILE):
            try:
                with open(WATCHLIST_FILE, "r") as f:
                    data: list[str] = json.load(f)
                logger.info("Loaded watchlist from disk (%d tickers)", len(data))
                return data
            except Exception:
                logger.exception("Failed to load watchlist — falling back to default")
        return list(DEFAULT_WATCHLIST)

    def save_watchlist(self) -> None:
        """Write the current watchlist to disk. Call via asyncio.to_thread()."""
        try:
            with open(WATCHLIST_FILE, "w") as f:
                json.dump(self.watchlist, f)
            logger.info("Watchlist saved (%d tickers)", len(self.watchlist))
        except Exception:
            logger.exception("Failed to save watchlist")

    # ── Mute persistence ──────────────────────────────────────────────────────

    def _load_mutes(self) -> dict[str, datetime]:
        if not os.path.exists(MUTES_FILE):
            return {}
        try:
            with open(MUTES_FILE, "r") as f:
                raw: dict[str, str] = json.load(f)
            now = datetime.now()
            return {
                ticker: exp
                for ticker, s in raw.items()
                if (exp := datetime.fromisoformat(s)) > now
            }
        except Exception:
            logger.exception("Failed to load mutes — starting with none")
            return {}

    def save_mutes(self) -> None:
        """Write active mutes to disk. Call via asyncio.to_thread()."""
        try:
            now = datetime.now()
            active = {t: exp.isoformat() for t, exp in self.ticker_mutes.items() if exp > now}
            with open(MUTES_FILE, "w") as f:
                json.dump(active, f)
        except Exception:
            logger.exception("Failed to save mutes")

    def mute_ticker(self, ticker: str, minutes: int, alert_price: float | None = None) -> None:
        """
        Set a mute and persist it to disk immediately.

        Pass alert_price when the mute follows a fired alert, so that a repeat
        signal at an unchanged price can be recognised later.
        """
        self.ticker_mutes[ticker] = datetime.now() + timedelta(minutes=minutes)
        self.save_mutes()
        if alert_price is not None:
            self.last_alert_price[ticker] = alert_price
            self.save_alert_prices()

    def mute_until_session_end(self, ticker: str) -> None:
        """Suppress a ticker for the remainder of the trading session."""
        self.ticker_mutes[ticker] = _session_end()
        self.save_mutes()

    # ── Last alerted price ────────────────────────────────────────────────────

    def _load_alert_prices(self) -> dict[str, float]:
        if not os.path.exists(ALERT_PRICES_FILE):
            return {}
        try:
            with open(ALERT_PRICES_FILE, "r") as f:
                return {t: float(p) for t, p in json.load(f).items()}
        except Exception:
            logger.exception("Failed to load alert prices — starting with none")
            return {}

    def save_alert_prices(self) -> None:
        """Write last alerted prices to disk. Call via asyncio.to_thread()."""
        try:
            with open(ALERT_PRICES_FILE, "w") as f:
                json.dump(self.last_alert_price, f)
        except Exception:
            logger.exception("Failed to save alert prices")

    def is_stale_repeat(self, ticker: str, price: float) -> bool:
        """
        True when this ticker last alerted at the same price.

        Mutual fund NAVs print once per trading day, so without this check the
        same signal re-fires every time the auto-mute expires, with identical
        numbers on the card.
        """
        previous = self.last_alert_price.get(ticker)
        return previous is not None and abs(previous - price) < _PRICE_EPSILON

    # ── Mute management ───────────────────────────────────────────────────────

    def clean_mutes(self) -> None:
        """Remove any mutes whose expiry has passed."""
        now = datetime.now()
        expired = [t for t, exp in self.ticker_mutes.items() if exp <= now]
        for t in expired:
            del self.ticker_mutes[t]
        if expired:
            logger.debug("Cleared expired mutes: %s", expired)

    def is_muted(self, ticker: str) -> bool:
        return ticker in self.ticker_mutes and self.ticker_mutes[ticker] > datetime.now()

    # ── Pause helpers ─────────────────────────────────────────────────────────

    def is_paused(self) -> bool:
        return self.paused_until is not None and datetime.now() < self.paused_until

    def pause_minutes_remaining(self) -> int:
        if self.paused_until is None:
            return 0
        return max(0, int((self.paused_until - datetime.now()).total_seconds() / 60))
