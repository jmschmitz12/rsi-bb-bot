import discord
import os
import json
import asyncio
import holidays
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from discord.ext import tasks, commands
from datetime import datetime, timedelta, time as dt_time
from dotenv import load_dotenv
from zoneinfo import ZoneInfo  # <--- Added for Timezone Safety

# ==========================================
# 1. SETUP & CONFIG
# ==========================================
load_dotenv()

# Sanity Check: Ensure tokens exist
if not os.getenv('DISCORD_TOKEN') or not os.getenv('USER_ID'):
    print("❌ ERROR: Missing DISCORD_TOKEN or USER_ID in .env file")
    exit(1)

BOT_TOKEN = os.getenv('DISCORD_TOKEN')
USER_ID = int(os.getenv('USER_ID'))
CHANNEL_ID = 1462117751073013973
WATCHLIST_FILE = "watchlist.json"

# Trading Settings
RSI_LIMIT = 30
BB_STD = 2.0
POLL_SPEED_MINUTES = 5


# ==========================================
# 2. DATA MANAGER
# ==========================================
class DataManager:
    @staticmethod
    def load_watchlist():
        if os.path.exists(WATCHLIST_FILE):
            try:
                with open(WATCHLIST_FILE, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print("⚠️ Watchlist file corrupted, loading defaults.")

        return ['AMZN', 'NVDA', 'SPY', 'QQQ', 'META', 'MSFT', 'AMD', 'PLTR', 'TSLA']

    @staticmethod
    def save_watchlist(watchlist):
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f)


# ==========================================
# 3. TRADING LOGIC
# ==========================================
def fetch_ticker_data(ticker):
    """Blocking I/O - Run in executor."""
    try:
        df = yf.download(ticker, period="2y", interval="1d", progress=False)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty or len(df) < 50: return None

        df['RSI'] = ta.rsi(df['Close'], length=14)
        bb = ta.bbands(df['Close'], length=20, std=BB_STD)

        if bb is None: return None

        df = pd.concat([df, bb], axis=1)

        # Dynamic Column Finding
        try:
            bbl_col = [c for c in df.columns if c.startswith('BBL')][0]
            bbu_col = [c for c in df.columns if c.startswith('BBU')][0]
        except IndexError:
            return None

        return {
            "price": df['Close'].iloc[-1],
            "rsi": df['RSI'].iloc[-1],
            "bbl": df[bbl_col].iloc[-1],
            "bbu": df[bbu_col].iloc[-1]
        }
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None


# ==========================================
# 4. THE BOT
# ==========================================
class MarketScanner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.watchlist = DataManager.load_watchlist()
        self.paused_until = None
        self.ticker_mutes = {}
        self.scanner_task.start()

    def cog_unload(self):
        self.scanner_task.cancel()

    def is_market_open(self):
        # FORCE NY TIMEZONE
        now = datetime.now(ZoneInfo("America/New_York"))

        if now.weekday() >= 5: return False
        if now.date() in holidays.NYSE(): return False
        return dt_time(9, 30) <= now.time() <= dt_time(16, 0)

    async def send_alert(self, ticker, signal, data):
        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            print(f"❌ Error: Could not find channel {CHANNEL_ID}")
            return

        color = 0x2ecc71 if signal == "OVERSOLD" else 0xe74c3c
        target = data['bbl'] if signal == "OVERSOLD" else data['bbu']

        content_msg = f"<@{USER_ID}> **{ticker}** {signal}"
        embed = discord.Embed(title=f"🚨 {ticker} {signal}", color=color, timestamp=datetime.now())
        embed.add_field(name="Price", value=f"**${data['price']:.2f}**", inline=True)
        embed.add_field(name="RSI", value=f"**{data['rsi']:.1f}**", inline=True)
        embed.add_field(name="Target Band", value=f"${target:.2f}", inline=True)
        embed.set_footer(text="Daily Strategy • Market Watchdog")

        await channel.send(content=content_msg, embed=embed)

    @tasks.loop(minutes=POLL_SPEED_MINUTES)
    async def scanner_task(self):
        if self.paused_until and datetime.now() < self.paused_until: return
        if not self.is_market_open(): return

        now = datetime.now()
        self.ticker_mutes = {t: time for t, time in self.ticker_mutes.items() if time > now}

        print(f"[{now.strftime('%H:%M')}] Scanning {len(self.watchlist)} tickers...")

        for ticker in self.watchlist:
            if ticker in self.ticker_mutes: continue

            # Run blocking I/O in a separate thread
            data = await asyncio.to_thread(fetch_ticker_data, ticker)

            if not data: continue

            if data['price'] < data['bbl'] and data['rsi'] < RSI_LIMIT:
                await self.send_alert(ticker, "OVERSOLD", data)
            elif data['price'] > data['bbu'] and data['rsi'] > (100 - RSI_LIMIT):
                await self.send_alert(ticker, "OVERBOUGHT", data)

            await asyncio.sleep(1)

    @scanner_task.before_loop
    async def before_scanner(self):
        await self.bot.wait_until_ready()

    # --- COMMANDS ---
    @commands.command()
    async def check(self, ctx, *tickers: str):
        """⚡ Instant analysis. Usage: !check NVDA MSFT SPY"""

        if not tickers:
            await ctx.send("⚠️ Usage: `!check TICKER` or `!check TICKER1 TICKER2`")
            return

        # Loop through every ticker user typed
        for ticker in tickers:
            ticker = ticker.upper()

            # Fetch data (Background thread)
            data = await asyncio.to_thread(fetch_ticker_data, ticker)

            if not data:
                await ctx.send(f"❌ Could not fetch data for **{ticker}**.")
                continue  # Skip to the next ticker in the list

            # Determine Status
            if data['price'] < data['bbl']:
                status, color = "🚨 OVERSOLD", 0x2ecc71
            elif data['price'] > data['bbu']:
                status, color = "🚨 OVERBOUGHT", 0xe74c3c
            else:
                status, color = "☁️ NEUTRAL", 0x3498db

            # Build the card
            embed = discord.Embed(title=f"📊 Report: {ticker}", description=f"**Status:** {status}", color=color)
            embed.add_field(name="Price", value=f"${data['price']:.2f}", inline=True)
            embed.add_field(name="RSI", value=f"{data['rsi']:.1f}", inline=True)
            embed.add_field(name="Bands", value=f"L: ${data['bbl']:.2f} / U: ${data['bbu']:.2f}", inline=True)

            # Send immediately
            await ctx.send(embed=embed)

    @commands.command()
    async def add(self, ctx, ticker: str):
        ticker = ticker.upper()
        if ticker not in self.watchlist:
            self.watchlist.append(ticker)
            DataManager.save_watchlist(self.watchlist)
            await ctx.send(f"✅ **{ticker}** added.")
        else:
            await ctx.send(f"⚠️ **{ticker}** already tracked.")

    @commands.command()
    async def remove(self, ctx, ticker: str):
        ticker = ticker.upper()
        if ticker in self.watchlist:
            self.watchlist.remove(ticker)
            DataManager.save_watchlist(self.watchlist)
            await ctx.send(f"🗑️ **{ticker}** removed.")
        else:
            await ctx.send(f"⚠️ **{ticker}** not found.")

    @commands.command()
    async def watchlist(self, ctx):
        items = sorted(self.watchlist)
        # Handle empty list
        if not items:
            await ctx.send("Watchlist is empty.")
            return

        chunked = [items[i:i + 15] for i in range(0, len(items), 15)]
        for chunk in chunked:
            desc = ", ".join(f"`{t}`" for t in chunk)
            embed = discord.Embed(title="📋 Watchlist", description=desc, color=0x3498db)
            await ctx.send(embed=embed)

    @commands.command()
    async def status(self, ctx):
        now = datetime.now(ZoneInfo("America/New_York"))
        if self.paused_until and datetime.now() < self.paused_until:
            rem = int((self.paused_until - datetime.now()).total_seconds() / 60)
            await ctx.send(f"⏸️ **PAUSED** for {rem} mins.")
        elif not self.is_market_open():
            reason = "Market Closed"
            if now.date() in holidays.NYSE(): reason = f"Holiday: {holidays.NYSE().get(now.date())}"
            await ctx.send(f"💤 {reason}")
        else:
            await ctx.send("🟢 **ONLINE**")

    @commands.command()
    async def pause(self, ctx, minutes: int):
        self.paused_until = datetime.now() + timedelta(minutes=minutes)
        await ctx.send(f"⏸️ Paused {minutes}m.")

    @commands.command()
    async def resume(self, ctx):
        self.paused_until = None
        await ctx.send("▶️ Resumed.")


# ==========================================
# 5. RUN
# ==========================================
if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix='!', intents=intents)


    @bot.event
    async def on_ready():
        print(f"--- Logged in as {bot.user} ---")
        await bot.add_cog(MarketScanner(bot))


    bot.run(BOT_TOKEN)