"""
cogs/scanner_cog.py
───────────────────
Background task: sleeps until market open, then polls every POLL_SPEED_MINUTES
during market hours. Never wakes up unnecessarily outside trading hours.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import holidays
import pytz

from discord.ext import commands

from alerts import send_alert
from config import CHANNEL_ID, POLL_SPEED_MINUTES, RATE_LIMIT_COOLDOWN_MINUTES, TIMEZONE
from market_data import create_chart, is_market_open, scan_ticker

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone(TIMEZONE)
_NYSE_HOLIDAYS = holidays.NYSE()


def _seconds_until_market_open() -> float:
    """
    Calculate seconds until the next NYSE market open (9:30 AM ET).
    Skips weekends and NYSE holidays automatically.
    """
    now = datetime.now(EASTERN)
    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)

    # If 9:30 AM today has already passed, start from tomorrow
    if now >= candidate:
        candidate += timedelta(days=1)

    # Skip weekends and holidays
    while candidate.weekday() >= 5 or candidate.date() in _NYSE_HOLIDAYS:
        candidate += timedelta(days=1)

    delta = (candidate - now).total_seconds()
    logger.info(
        "Market opens at %s — sleeping for %.0f seconds (%.1f hours)",
        candidate.strftime("%Y-%m-%d %I:%M %p ET"),
        delta,
        delta / 3600,
    )
    return delta


class ScannerCog(commands.Cog, name="Scanner"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._scan_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._scan_task = asyncio.create_task(self._run())

    def cog_unload(self) -> None:
        if self._scan_task:
            self._scan_task.cancel()

    async def _run(self) -> None:
        """
        Outer loop: sleeps until market open, then hands off to _scan_session()
        for the duration of the trading day. Repeats forever.
        """
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)
        logger.info("Scanner task started")

        while True:
            if not is_market_open():
                await asyncio.sleep(_seconds_until_market_open())

            await self._scan_session()

    async def _scan_session(self) -> None:
        """
        Inner loop: runs during market hours, polling every POLL_SPEED_MINUTES.
        Exits cleanly when the market closes.
        """
        logger.info("Market open — scan session starting")
        state = self.bot.state

        while is_market_open():
            logger.info("Scanner loop fired")

            # ── Rate limit recovery ───────────────────────────────────────────
            if state.rate_limit_cooldown:
                state.paused_until = datetime.now() + timedelta(minutes=RATE_LIMIT_COOLDOWN_MINUTES)
                state.rate_limit_cooldown = False
                logger.warning("Rate limit hit — pausing for %d minutes", RATE_LIMIT_COOLDOWN_MINUTES)
                channel = self.bot.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(
                        f"⚠️ **Rate limit hit** — scanner paused for {RATE_LIMIT_COOLDOWN_MINUTES} minutes."
                    )
                await asyncio.sleep(RATE_LIMIT_COOLDOWN_MINUTES * 60)
                continue

            # ── Manual pause ──────────────────────────────────────────────────
            if state.is_paused():
                await asyncio.sleep(POLL_SPEED_MINUTES * 60)
                continue

            channel = self.bot.get_channel(CHANNEL_ID)
            if not channel:
                logger.error("Cannot resolve CHANNEL_ID %d — check bot permissions", CHANNEL_ID)
                await asyncio.sleep(POLL_SPEED_MINUTES * 60)
                continue

            state.clean_mutes()

            # ── Scan each ticker ──────────────────────────────────────────────
            for ticker in list(state.watchlist):
                if state.rate_limit_cooldown:
                    break
                if state.is_muted(ticker):
                    continue

                try:
                    alert = await asyncio.to_thread(scan_ticker, ticker)
                    if alert:
                        chart = await asyncio.to_thread(
                            create_chart, alert.df, ticker, alert.bbl_col, alert.bbu_col, alert.bbm_col
                        )
                        await send_alert(
                            channel,
                            ticker,
                            alert.signal,
                            alert.price,
                            alert.rsi,
                            alert.target_band,
                            alert.bbm,
                            chart,
                        )
                except Exception as e:
                    if "429" in str(e):
                        state.rate_limit_cooldown = True
                        logger.error("Rate limit on %s — triggering cooldown", ticker)
                        break
                    logger.error("Error scanning %s: %s", ticker, e)

                await asyncio.sleep(1.5)

            await asyncio.sleep(POLL_SPEED_MINUTES * 60)

        logger.info("Market closed — scan session ending")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScannerCog(bot))
