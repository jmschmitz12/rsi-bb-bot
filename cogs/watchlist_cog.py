"""
cogs/watchlist_cog.py
─────────────────────
Commands: !add, !remove, !watchlist
"""

import asyncio
import logging
from datetime import datetime

from discord.ext import commands

from utils import is_bot_owner

logger = logging.getLogger(__name__)


class WatchlistCog(commands.Cog, name="Watchlist"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    @is_bot_owner()
    async def add(self, ctx: commands.Context, *tickers: str) -> None:
        """Adds one or more tickers to the watchlist.  Usage: !add AAPL MSFT"""
        if not tickers:
            await ctx.send("⚠️ Usage: `!add TICKER1 TICKER2 ...`")
            return

        state = self.bot.state
        added: list[str] = []
        skipped: list[str] = []

        for raw in tickers:
            t = raw.upper().replace(",", "")
            if t not in state.watchlist:
                state.watchlist.append(t)
                added.append(t)
            else:
                skipped.append(t)

        if added:
            await asyncio.to_thread(state.save_watchlist)
            logger.info("Watchlist add by %s: %s", ctx.author, added)
            msg = f"✅ **Added:** {', '.join(added)}"
            if skipped:
                msg += f"\n⚠️ **Skipped (already in list):** {', '.join(skipped)}"
            await ctx.send(msg)
        else:
            await ctx.send("⚠️ All listed tickers are already in the watchlist.")

    @commands.command()
    @is_bot_owner()
    async def remove(self, ctx: commands.Context, *tickers: str) -> None:
        """Removes one or more tickers from the watchlist.  Usage: !remove AAPL MSFT"""
        if not tickers:
            await ctx.send("⚠️ Usage: `!remove TICKER1 TICKER2 ...`")
            return

        state = self.bot.state
        removed: list[str] = []
        not_found: list[str] = []

        for raw in tickers:
            t = raw.upper().replace(",", "")
            if t in state.watchlist:
                state.watchlist.remove(t)
                removed.append(t)
            else:
                not_found.append(t)

        if removed:
            await asyncio.to_thread(state.save_watchlist)
            logger.info("Watchlist remove by %s: %s", ctx.author, removed)
            msg = f"🗑️ **Removed:** {', '.join(removed)}"
            if not_found:
                msg += f"\n⚠️ **Not found:** {', '.join(not_found)}"
            await ctx.send(msg)
        else:
            await ctx.send("⚠️ None of those tickers were found in the watchlist.")

    @commands.command(name="watchlist")
    async def show_watchlist(self, ctx: commands.Context) -> None:
        """Displays all tickers currently being watched."""
        state = self.bot.state
        now = datetime.now()
        lines = ["**📊 Current Watchlist**"]

        for t in sorted(state.watchlist):
            if t in state.ticker_mutes and state.ticker_mutes[t] > now:
                lines.append(f"• ~~{t}~~ (muted)")
            else:
                lines.append(f"• {t}")

        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WatchlistCog(bot))
