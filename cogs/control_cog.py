"""
cogs/control_cog.py
───────────────────
Commands: !pause, !resume, !mute, !status
"""

import asyncio
import logging
from datetime import datetime, timedelta

import holidays
from discord.ext import commands

from market_data import is_market_open
from utils import is_bot_owner

logger = logging.getLogger(__name__)


class ControlCog(commands.Cog, name="Control"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    @is_bot_owner()
    async def mute(self, ctx: commands.Context, ticker: str, minutes: int) -> None:
        """Silences alerts for a ticker for X minutes.  Usage: !mute TSLA 60"""
        state = self.bot.state
        ticker = ticker.upper()

        if ticker not in state.watchlist:
            await ctx.send(f"⚠️ **{ticker}** is not in the watchlist.")
            return

        await asyncio.to_thread(state.mute_ticker, ticker, minutes)
        logger.info("%s muted by %s for %d minutes", ticker, ctx.author, minutes)
        await ctx.send(f"🔇 **{ticker}** alerts muted for {minutes} minutes.")

    @commands.command()
    @is_bot_owner()
    async def pause(self, ctx: commands.Context, minutes: int) -> None:
        """Pauses the entire scanner for X minutes.  Usage: !pause 30"""
        state = self.bot.state
        state.paused_until = datetime.now() + timedelta(minutes=minutes)
        logger.info("Scanner paused for %d minutes by %s", minutes, ctx.author)
        await ctx.send(f"⏸️ Scanner paused for {minutes} minutes.")

    @commands.command()
    @is_bot_owner()
    async def resume(self, ctx: commands.Context) -> None:
        """Resumes the scanner immediately."""
        self.bot.state.paused_until = None
        logger.info("Scanner resumed by %s", ctx.author)
        await ctx.send("▶️ Scanner resumed.")

    @commands.command()
    async def status(self, ctx: commands.Context) -> None:
        """Reports the bot's current operating status."""
        state = self.bot.state
        nyse_holidays = holidays.NYSE()

        if state.is_paused():
            remaining = state.pause_minutes_remaining()
            await ctx.send(f"⏸️ **Paused** — {remaining} minute(s) remaining.")
        elif datetime.now().date() in nyse_holidays:
            holiday_name = nyse_holidays.get(datetime.now().date())
            await ctx.send(f"💤 **Market holiday:** {holiday_name}")
        elif not is_market_open():
            await ctx.send("💤 **Market closed.**")
        else:
            watchlist_size = len(state.watchlist)
            muted_count = sum(1 for t in state.watchlist if state.is_muted(t))
            await ctx.send(
                f"🟢 **Online** — scanning {watchlist_size} tickers"
                + (f" ({muted_count} muted)" if muted_count else "") + "."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ControlCog(bot))
