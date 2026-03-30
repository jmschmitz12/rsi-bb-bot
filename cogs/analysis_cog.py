"""
cogs/analysis_cog.py
────────────────────
Commands: !check, !scan
"""

import asyncio
import logging
from datetime import datetime

import discord
from discord.ext import commands

from alerts import send_alert
from config import RSI_LIMIT
from market_data import check_ticker, create_chart, scan_ticker
from utils import is_bot_owner

logger = logging.getLogger(__name__)


class AnalysisCog(commands.Cog, name="Analysis"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    async def check(self, ctx: commands.Context, ticker: str) -> None:
        """Manually checks a ticker's current technicals.  Usage: !check AAPL"""
        ticker = ticker.upper()

        try:
            data = await asyncio.to_thread(check_ticker, ticker)
        except Exception as e:
            if "429" in str(e):
                await ctx.send("⚠️ Rate limited by Yahoo Finance — try again in a few minutes.")
            else:
                logger.error("!check %s failed: %s", ticker, e)
                await ctx.send(f"❌ Unexpected error fetching **{ticker}**.")
            return

        if data is None:
            await ctx.send(f"❌ Could not fetch data for **{ticker}**. The ticker may be invalid.")
            return

        # Determine signal status
        if data.rsi < RSI_LIMIT:
            status, color = "OVERSOLD (buy signal)", 0x2ECC71
        elif data.rsi > (100 - RSI_LIMIT):
            status, color = "OVERBOUGHT (sell signal)", 0xE74C3C
        else:
            status, color = "NEUTRAL", 0x95A5A6

        chart = await asyncio.to_thread(create_chart, data.df, ticker, data.bbl_col, data.bbu_col)
        discord_file = discord.File(fp=chart, filename=f"{ticker}_check.png")

        embed = discord.Embed(
            title=f"📊 Analysis: {ticker}",
            color=color,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Price", value=f"**${data.price:.2f}**", inline=True)
        embed.add_field(name="RSI (14)", value=f"**{data.rsi:.2f}**", inline=True)
        embed.add_field(name="Status", value=f"**{status}**", inline=False)
        embed.add_field(name="Bollinger Low", value=f"${data.bbl:.2f}", inline=True)
        embed.add_field(name="Bollinger High", value=f"${data.bbu:.2f}", inline=True)
        embed.set_image(url=f"attachment://{ticker}_check.png")

        await ctx.send(file=discord_file, embed=embed)
        logger.info("!check %s: $%.2f RSI=%.2f [%s]", ticker, data.price, data.rsi, status)

    @commands.command()
    @is_bot_owner()
    async def scan(self, ctx: commands.Context) -> None:
        """Manually triggers a full watchlist scan and reports any signals."""
        state = self.bot.state
        triggered = 0

        await ctx.send(f"🔍 Scanning {len(state.watchlist)} tickers...")

        for ticker in list(state.watchlist):
            try:
                alert = await asyncio.to_thread(scan_ticker, ticker)
                if alert:
                    chart = await asyncio.to_thread(
                        create_chart, alert.df, ticker, alert.bbl_col, alert.bbu_col
                    )
                    await send_alert(
                        ctx,
                        ticker,
                        alert.signal,
                        alert.price,
                        alert.rsi,
                        alert.target_band,
                        chart,
                    )
                    triggered += 1
            except Exception as e:
                if "429" in str(e):
                    await ctx.send("⚠️ Rate limited — scan aborted early.")
                    logger.warning("!scan aborted at %s due to rate limit", ticker)
                    return
                logger.error("!scan error on %s: %s", ticker, e)

            await asyncio.sleep(1.0)

        if triggered == 0:
            await ctx.send("✅ Scan complete — no signals triggered.")
        else:
            await ctx.send(f"✅ Scan complete — {triggered} signal(s) fired.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalysisCog(bot))
