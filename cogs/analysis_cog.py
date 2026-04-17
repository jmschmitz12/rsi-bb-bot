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
from config import BB_STD, RSI_LIMIT
from market_data import EASTERN, check_ticker, create_chart, scan_ticker
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

        # ── Signal state — mirrors scan_ticker() dual-condition logic ────────
        if data.price < data.bbl and data.rsi < RSI_LIMIT:
            signal, color = "OVERSOLD", 0x2ECC71
        elif data.price > data.bbu and data.rsi > (100 - RSI_LIMIT):
            signal, color = "OVERBOUGHT", 0xE74C3C
        else:
            signal, color = "NEUTRAL", 0x95A5A6

        # ── Percentage fields (mirror alert embed logic) ───────────────────────
        et_time = datetime.now(EASTERN).strftime("%-I:%M %p ET")

        if signal == "OVERSOLD":
            pct_outside = (data.bbl - data.price) / data.bbl * 100
            pct_to_mid  = (data.bbm - data.price) / data.price * 100
            field1 = ("Lower Band",    f"${data.bbl:.2f}")
            field2 = ("% Below Band",  f"−{pct_outside:.2f}%")
            field3 = ("% To Midline",  f"+{pct_to_mid:.2f}%")
        elif signal == "OVERBOUGHT":
            pct_outside = (data.price - data.bbu) / data.bbu * 100
            pct_to_mid  = (data.price - data.bbm) / data.price * 100
            field1 = ("Upper Band",    f"${data.bbu:.2f}")
            field2 = ("% Above Band",  f"+{pct_outside:.2f}%")
            field3 = ("% To Midline",  f"−{pct_to_mid:.2f}%")
        else:
            pct_to_upper = (data.bbu - data.price) / data.price * 100
            pct_to_lower = (data.price - data.bbl) / data.price * 100
            # Show which side of the midline price is on
            above_mid = data.price >= data.bbm
            pct_to_mid = abs(data.price - data.bbm) / data.price * 100
            mid_str = f"−{pct_to_mid:.2f}%" if above_mid else f"+{pct_to_mid:.2f}%"
            field1 = ("Upper Band",      f"${data.bbu:.2f}  (+{pct_to_upper:.2f}%)")
            field2 = ("Lower Band",      f"${data.bbl:.2f}  (−{pct_to_lower:.2f}%)")
            field3 = ("% To Midline",    mid_str)

        # ── Build embed ───────────────────────────────────────────────────────
        chart = await asyncio.to_thread(
            create_chart, data.df, ticker, data.bbl_col, data.bbu_col, data.bbm_col
        )
        discord_file = discord.File(fp=chart, filename=f"{ticker}_check.png")

        embed = discord.Embed(color=color, timestamp=datetime.now())
        embed.set_author(name=f"{signal}  ·  Manual check")
        embed.title = ticker
        embed.description = f"**${data.price:.2f}**  ·  RSI **{data.rsi:.2f}**  ·  6-month daily"

        embed.add_field(name=field1[0], value=field1[1], inline=True)
        embed.add_field(name=field2[0], value=field2[1], inline=True)
        embed.add_field(name=field3[0], value=field3[1], inline=True)

        embed.set_image(url=f"attachment://{ticker}_check.png")
        embed.set_footer(text=f"{et_time}  ·  BB(20, {BB_STD})  ·  RSI(14)")

        await ctx.send(file=discord_file, embed=embed)
        logger.info("!check %s: $%.2f RSI=%.2f [%s]", ticker, data.price, data.rsi, signal)

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
                        create_chart, alert.df, ticker, alert.bbl_col, alert.bbu_col, alert.bbm_col
                    )
                    await send_alert(
                        ctx,
                        ticker,
                        alert.signal,
                        alert.price,
                        alert.rsi,
                        alert.target_band,
                        alert.bbm,
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
