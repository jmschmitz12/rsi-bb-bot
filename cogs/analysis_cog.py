"""
cogs/analysis_cog.py
────────────────────
Commands: !check, !scan
Background: daily S&P 500 scan near the close (SP500_DAILY_* in config)
"""

import asyncio
import logging
from datetime import datetime

import discord
from discord.ext import commands

from alerts import format_day_change, send_alert
from config import (
    BB_STD,
    CHANNEL_ID,
    RSI_LIMIT,
    SP500_DAILY_SCAN_ENABLED,
    SP500_DAILY_SCAN_TIME,
)
from market_data import (
    EASTERN,
    alert_from_data,
    check_ticker,
    create_chart,
    fetch_batch,
    get_company_name,
    next_trading_time,
    scan_ticker,
)
from sp500 import get_sp500_names, get_sp500_tickers
from utils import is_bot_owner

SP500_CHUNK_SIZE = 50
SP500_CHUNK_DELAY_SECONDS = 2.0
ALERT_SEND_DELAY_SECONDS = 1.0

logger = logging.getLogger(__name__)


class AnalysisCog(commands.Cog, name="Analysis"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sp500_lock = asyncio.Lock()   # one S&P 500 scan at a time, manual or scheduled
        self._daily_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        if SP500_DAILY_SCAN_ENABLED:
            self._daily_task = asyncio.create_task(self._daily_sp500_loop())

    def cog_unload(self) -> None:
        if self._daily_task:
            self._daily_task.cancel()

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

        company_name = await asyncio.to_thread(get_company_name, ticker)
        title = f"{ticker} — {company_name}" if company_name else ticker
        change_str = format_day_change(data.day_change, data.day_change_pct)

        # ── Signal state — mirrors scan_ticker() dual-condition logic ────────
        if data.price < data.bbl and data.rsi < RSI_LIMIT:
            signal, color = "OVERSOLD", 0x2ECC71
        elif data.price > data.bbu and data.rsi > (100 - RSI_LIMIT):
            signal, color = "OVERBOUGHT", 0xE74C3C
        else:
            signal, color = "NEUTRAL", 0x95A5A6

        et_time = datetime.now(EASTERN).strftime("%-I:%M %p ET")

        if signal == "OVERSOLD":
            pct_outside = (data.bbl - data.price) / data.bbl * 100
            pct_to_mid  = (data.bbm - data.price) / data.price * 100
            field1 = ("RSI",         f"**{data.rsi:.2f}**")
            field2 = ("Lower Band",  f"${data.bbl:.2f} (−{pct_outside:.2f}%)")
            field3 = ("Midline",     f"${data.bbm:.2f} (+{pct_to_mid:.2f}%)")
        elif signal == "OVERBOUGHT":
            pct_outside = (data.price - data.bbu) / data.bbu * 100
            pct_to_mid  = (data.price - data.bbm) / data.price * 100
            field1 = ("RSI",         f"**{data.rsi:.2f}**")
            field2 = ("Upper Band",  f"${data.bbu:.2f} (+{pct_outside:.2f}%)")
            field3 = ("Midline",     f"${data.bbm:.2f} (−{pct_to_mid:.2f}%)")
        else:
            pct_to_upper = (data.bbu - data.price) / data.price * 100
            pct_to_lower = (data.price - data.bbl) / data.price * 100
            field1 = ("RSI",         f"**{data.rsi:.2f}**")
            field2 = ("Upper Band",  f"${data.bbu:.2f} (+{pct_to_upper:.2f}%)")
            field3 = ("Lower Band",  f"${data.bbl:.2f} (−{pct_to_lower:.2f}%)")

        # ── Build embed ───────────────────────────────────────────────────────
        chart = await asyncio.to_thread(
            create_chart, data.df, ticker, data.bbl_col, data.bbu_col, data.bbm_col
        )
        discord_file = discord.File(fp=chart, filename=f"{ticker}_check.png")

        embed = discord.Embed(color=color, timestamp=datetime.now())
        embed.set_author(name=f"{signal}  ·  Manual check")
        embed.title = title
        embed.description = f"**${data.price:.2f}**  ·  {change_str}"

        embed.add_field(name=field1[0], value=field1[1], inline=True)
        embed.add_field(name=field2[0], value=field2[1], inline=True)
        embed.add_field(name=field3[0], value=field3[1], inline=True)

        embed.set_image(url=f"attachment://{ticker}_check.png")
        embed.set_footer(text=f"{et_time}  ·  BB(20, {BB_STD})  ·  RSI(14)")

        await ctx.send(file=discord_file, embed=embed)
        logger.info("!check %s: $%.2f RSI=%.2f [%s]", ticker, data.price, data.rsi, signal)

    @commands.command()
    @is_bot_owner()
    async def scan(self, ctx: commands.Context, mode: str = None) -> None:
        """
        Manually triggers a scan and reports any signals.

        Usage:
            !scan          – scans your watchlist (fast)
            !scan sp500    – scans all S&P 500 tickers via batch download (slow)
        """
        if mode is None:
            await self._scan_watchlist(ctx)
        elif mode.lower() == "sp500":
            if self._sp500_lock.locked():
                await ctx.send("⏳ An S&P 500 scan is already running — try again in a few minutes.")
                return
            async with self._sp500_lock:
                await self._scan_sp500(ctx)
        else:
            await ctx.send(f"❌ Unknown scan mode: `{mode}`. Use `!scan` or `!scan sp500`.")

    async def _scan_watchlist(self, ctx: commands.Context) -> None:
        state = self.bot.state
        triggered = 0

        await ctx.send(f"🔍 Scanning {len(state.watchlist)} tickers...")

        for ticker in list(state.watchlist):
            try:
                alert = await asyncio.to_thread(scan_ticker, ticker)
                if alert:
                    company_name = await asyncio.to_thread(get_company_name, ticker)
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
                        company_name=company_name,
                        day_change=alert.day_change,
                        day_change_pct=alert.day_change_pct,
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

    async def _daily_sp500_loop(self) -> None:
        """Runs the S&P 500 scan once per trading day at SP500_DAILY_SCAN_TIME ET."""
        await self.bot.wait_until_ready()
        run_at = next_trading_time(*SP500_DAILY_SCAN_TIME)

        while True:
            logger.info(
                "Daily S&P 500 scan scheduled for %s",
                run_at.strftime("%Y-%m-%d %I:%M %p ET"),
            )
            await asyncio.sleep(max(0.0, (run_at - datetime.now(EASTERN)).total_seconds()))

            try:
                await self._run_daily_sp500()
            except Exception:
                logger.exception("Daily S&P 500 scan failed")

            # Schedule from the slot just used, so waking a moment early can
            # never run the same day twice.
            run_at = next_trading_time(
                *SP500_DAILY_SCAN_TIME, now=max(run_at, datetime.now(EASTERN))
            )

    async def _run_daily_sp500(self) -> None:
        state = self.bot.state

        if state.is_paused():
            logger.info("Daily S&P 500 scan skipped — scanner is paused")
            return
        if self._sp500_lock.locked():
            logger.info("Daily S&P 500 scan skipped — a manual scan is already running")
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            logger.error("Cannot resolve CHANNEL_ID %d — daily S&P 500 scan skipped", CHANNEL_ID)
            return

        logger.info("Daily S&P 500 scan starting")
        async with self._sp500_lock:
            await self._scan_sp500(
                channel,
                name="Daily S&P 500 scan",
                exclude=set(state.watchlist),   # the 5-minute scanner already covers these
                announce_start=False,
            )

    async def _scan_sp500(
        self,
        destination: discord.abc.Messageable,
        *,
        name: str = "S&P 500 scan",
        exclude: set[str] = frozenset(),
        announce_start: bool = True,
    ) -> None:
        """
        Batch-scan the S&P 500 and post hits to destination, most extreme first.

        exclude drops tickers before anything is downloaded. Every hit gets a
        full chart card.
        """
        try:
            tickers = await asyncio.to_thread(get_sp500_tickers)
        except Exception as e:
            logger.error("S&P 500 list load failed: %s", e)
            await destination.send(f"❌ Could not load S&P 500 list: {e}")
            return

        tickers = [t for t in tickers if t not in exclude]
        names = await asyncio.to_thread(get_sp500_names)

        chunks = [
            tickers[i : i + SP500_CHUNK_SIZE]
            for i in range(0, len(tickers), SP500_CHUNK_SIZE)
        ]
        if announce_start:
            await destination.send(
                f"🔍 Scanning S&P 500 — {len(tickers)} tickers in {len(chunks)} batches. "
                f"This will take a few minutes..."
            )

        triggered: list[tuple[str, object]] = []
        succeeded = 0

        for i, chunk in enumerate(chunks, 1):
            try:
                batch = await asyncio.to_thread(fetch_batch, chunk)
            except Exception as e:
                if "429" in str(e):
                    await destination.send(
                        f"⚠️ {name} rate limited at batch {i}/{len(chunks)} — aborted early."
                    )
                    logger.warning("%s aborted at chunk %d due to rate limit", name, i)
                    return
                logger.error("%s batch %d error: %s", name, i, e)
                continue

            succeeded += len(batch)
            for ticker, data in batch.items():
                alert = alert_from_data(data)
                if alert:
                    triggered.append((ticker, alert))

            await asyncio.sleep(SP500_CHUNK_DELAY_SECONDS)

        failed = len(tickers) - succeeded
        logger.info(
            "%s complete: %d signal(s), %d processed, %d failed",
            name, len(triggered), succeeded, failed,
        )

        if not triggered:
            await destination.send(
                f"✅ {name} complete — no signals triggered "
                f"({succeeded} processed, {failed} failed)."
            )
            return

        # Sort by signal magnitude — most extreme first.
        def magnitude(item: tuple[str, object]) -> float:
            _, a = item
            if a.signal == "OVERSOLD":
                return (a.target_band - a.price) / a.target_band
            return (a.price - a.target_band) / a.target_band

        triggered.sort(key=magnitude, reverse=True)

        oversold = sum(1 for _, a in triggered if a.signal == "OVERSOLD")
        overbought = len(triggered) - oversold
        summary = (
            f"✅ {name} complete — **{len(triggered)} signal(s)** "
            f"({oversold} oversold, {overbought} overbought)"
        )
        if failed:
            summary += f"  ·  {failed} tickers failed"
        await destination.send(summary)

        for ticker, alert in triggered:
            try:
                chart = await asyncio.to_thread(
                    create_chart, alert.df, ticker, alert.bbl_col, alert.bbu_col, alert.bbm_col
                )
                await send_alert(
                    destination,
                    ticker,
                    alert.signal,
                    alert.price,
                    alert.rsi,
                    alert.target_band,
                    alert.bbm,
                    chart,
                    company_name=names.get(ticker),
                    day_change=alert.day_change,
                    day_change_pct=alert.day_change_pct,
                )
            except Exception as e:
                logger.error("%s alert send failed for %s: %s", name, ticker, e)

            await asyncio.sleep(ALERT_SEND_DELAY_SECONDS)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalysisCog(bot))
