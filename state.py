import json
import logging
import os
from datetime import datetime

from config import DEFAULT_WATCHLIST, WATCHLIST_FILE

logger = logging.getLogger(__name__)


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
        self.ticker_mutes: dict[str, datetime] = {}

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
