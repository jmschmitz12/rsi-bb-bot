"""
cogs/scanner_cog.py
───────────────────
Background task: polls every POLL_SPEED_MINUTES and fires alerts when
a ticker crosses its Bollinger Bands with a confirming RSI reading.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from discord.ext import commands, tasks

from alerts import send_alert
from config import CHANNEL_ID, POLL_SPEED_MINUTES, RATE_LIMIT_COOLDOWN_MINUTES
from market_data import create_chart, is_market_open, scan_ticker

logger = logging.getLogger(__name__)


class ScannerCog(commands.Cog, name="Scanner"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scanner_loop.start()

    def cog_unload(self) -> None:
        self.scanner_loop.cancel()

    @tasks.loop(minutes=POLL_SPEED_MINUTES)
    async def scanner_loop(self) -> None:
        state = self.bot.state

        # ── Rate limit recovery ────────────────────────────────────────────────
        if state.rate_limit_cooldown:
            state.paused_until = datetime.now() + timedelta(minutes=RATE_LIMIT_COOLDOWN_MINUTES)
            state.rate_limit_cooldown = False
            logger.warning("Rate limit hit — pausing scanner for %d minutes", RATE_LIMIT_COOLDOWN_MINUTES)
            channel = self.bot.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(
                    f"⚠️ **Rate limit hit** — scanner paused for {RATE_LIMIT_COOLDOWN_MINUTES} minutes."
                )
            return

        # ── Manual pause ──────────────────────────────────────────────────────
        if state.is_paused():
            return

        # ── Market closed ─────────────────────────────────────────────────────
        if not is_market_open():
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            logger.error("Cannot resolve CHANNEL_ID %d — check bot permissions", CHANNEL_ID)
            return

        state.clean_mutes()

        for ticker in list(state.watchlist):  # copy so mutations during iteration are safe
            if state.rate_limit_cooldown:
                break
            if state.is_muted(ticker):
                continue

            try:
                alert = await asyncio.to_thread(scan_ticker, ticker)
                if alert:
                    chart = await asyncio.to_thread(
                        create_chart, alert.df, ticker, alert.bbl_col, alert.bbu_col
                    )
                    await send_alert(
                        channel,
                        ticker,
                        alert.signal,
                        alert.price,
                        alert.rsi,
                        alert.target_band,
                        chart,
                    )
            except Exception as e:
                if "429" in str(e):
                    state.rate_limit_cooldown = True
                    logger.error("Rate limit on %s — triggering cooldown", ticker)
                    break
                logger.error("Error scanning %s: %s", ticker, e)

            await asyncio.sleep(1.5)   # be courteous to the yfinance API

    @scanner_loop.before_loop
    async def before_scanner(self) -> None:
        await self.bot.wait_until_ready()
        # Brief delay on startup to avoid hammering the API during a Pi reboot
        await asyncio.sleep(30)
        logger.info("Scanner loop starting")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScannerCog(bot))
