import discord
import os
import json  # <--- NEW: Needed for saving data
from discord.ext import tasks, commands
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import asyncio
import holidays
from datetime import datetime, timedelta, time as dt_time
from dotenv import load_dotenv

# ==========================================
# 1. CONFIGURATION
# ==========================================
load_dotenv()

BOT_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = 1462117751073013973
MY_USER_ID = int(os.getenv('USER_ID'))

# Settings
RSI_LIMIT = 30
BB_STD = 2.0
POLL_SPEED_MINUTES = 5
WATCHLIST_FILE = "watchlist.json"  # <--- NEW: File name to save data

# The "Backup" list if the file gets deleted or hasn't been created yet
DEFAULT_WATCHLIST = ['AMZN', 'NVDA', 'SPY', 'QQQ', 'META', 'MSFT', 'PM', 'DAL', 'AAL', 'GOOG', 'KO', 'AMD', 'AVGO', 'PLTR', 'TSLA']

# ==========================================
# 2. BOT SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# State Variables
paused_until = None
rate_limit_cooldown = False
ticker_mutes = {}

# --- NEW FUNCTION: LOAD WATCHLIST ---
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r') as f:
                data = json.load(f)
                print(f"Loaded watchlist from file: {len(data)} tickers.")
                return data
        except Exception as e:
            print(f"Error loading watchlist: {e}")
            return DEFAULT_WATCHLIST
    else:
        return list(DEFAULT_WATCHLIST) # Return a copy of defaults

# --- NEW FUNCTION: SAVE WATCHLIST ---
def save_watchlist():
    try:
        with open(WATCHLIST_FILE, 'w') as f:
            json.dump(WATCHLIST, f)
            print("Watchlist saved to disk.")
    except Exception as e:
        print(f"Error saving watchlist: {e}")

# Initialize Watchlist immediately on startup
WATCHLIST = load_watchlist()


# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5: return False
    nyse_holidays = holidays.NYSE()
    if now.date() in nyse_holidays: return False
    return dt_time(9, 30) <= now.time() <= dt_time(16, 0)


async def send_formatted_alert(ctx_or_channel, ticker, signal, price, rsi, band):
    alert_color = 0x2ecc71 if signal == "OVERSOLD" else 0xe74c3c
    banner_text = f"🚨 **{ticker} {signal}** at **${price:.2f}** (RSI: {rsi:.2f})"

    embed = discord.Embed(
        title=f"{ticker} - {signal} detected",
        description=f"Market price crossed the {'Lower' if signal == 'OVERSOLD' else 'Upper'} Bollinger Band.",
        color=alert_color,
        timestamp=datetime.now()
    )
    embed.add_field(name="Current Price", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="RSI (14)", value=f"**{rsi:.2f}**", inline=True)
    embed.add_field(name="Target Band", value=f"${band:.2f}", inline=True)
    embed.set_footer(text="Market Watchdog")

    await ctx_or_channel.send(content=banner_text, embed=embed)


def get_market_data(ticker):
    global rate_limit_cooldown
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty or len(df) < 20: return None

        df['RSI'] = ta.rsi(df['Close'], length=14)
        bb = ta.bbands(df['Close'], length=20, std=BB_STD)
        if bb is None: return None
        df = pd.concat([df, bb], axis=1)

        bbl_col = [c for c in df.columns if c.startswith('BBL')][0]
        bbu_col = [c for c in df.columns if c.startswith('BBU')][0]

        price, rsi = df['Close'].iloc[-1], df['RSI'].iloc[-1]
        bbl, bbu = df[bbl_col].iloc[-1], df[bbu_col].iloc[-1]

        if price < bbl and rsi < RSI_LIMIT:
            return ("OVERSOLD", price, rsi, bbl)
        elif price > bbu and rsi > (100 - RSI_LIMIT):
            return ("OVERBOUGHT", price, rsi, bbu)
        return None

    except Exception as e:
        if "429" in str(e):
            print(f"!!! RATE LIMIT HIT on {ticker} !!!")
            rate_limit_cooldown = True
        else:
            print(f"Error on {ticker}: {e}")
        return None


# ==========================================
# 4. BACKGROUND TASK
# ==========================================
@tasks.loop(minutes=POLL_SPEED_MINUTES)
async def market_scanner():
    global paused_until, rate_limit_cooldown, ticker_mutes

    if rate_limit_cooldown:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send("⚠️ **RATE LIMIT ALERT**: Throttled. Cooling down for 30 mins...")
        paused_until = datetime.now() + timedelta(minutes=30)
        rate_limit_cooldown = False
        return

    if paused_until and datetime.now() < paused_until:
        return

    if not is_market_open():
        return

    channel = bot.get_channel(CHANNEL_ID)
    now = datetime.now()
    ticker_mutes = {t: time for t, time in ticker_mutes.items() if time > now}

    for ticker in WATCHLIST:
        if ticker in ticker_mutes:
            continue

        result = get_market_data(ticker)
        if result:
            await send_formatted_alert(channel, ticker, *result)
        await asyncio.sleep(1.5)


@market_scanner.before_loop
async def before_scanner():
    await bot.wait_until_ready()


# ==========================================
# 5. COMMANDS
# ==========================================
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    if not market_scanner.is_running():
        market_scanner.start()


@bot.command()
async def add(ctx, ticker: str):
    """Adds a ticker to the watchlist."""
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        WATCHLIST.append(ticker)
        save_watchlist()  # <--- NEW: Save changes
        await ctx.send(f"✅ Added **{ticker}** to watchlist.")
    else:
        await ctx.send(f"⚠️ **{ticker}** is already in the list.")


@bot.command()
async def remove(ctx, ticker: str):
    """Removes a ticker from the watchlist."""
    ticker = ticker.upper()
    if ticker in WATCHLIST:
        WATCHLIST.remove(ticker)
        save_watchlist()  # <--- NEW: Save changes
        await ctx.send(f"🗑️ Removed **{ticker}** from watchlist.")
    else:
        await ctx.send(f"⚠️ **{ticker}** not found.")


@bot.command()
async def watchlist(ctx):
    """Displays all active tickers."""
    now = datetime.now()
    msg = "**📊 CURRENT WATCHLIST**\n"
    for t in sorted(WATCHLIST):
        if t in ticker_mutes and ticker_mutes[t] > now:
            msg += f"• ~~{t}~~ (Muted)\n"
        else:
            msg += f"• {t}\n"
    await ctx.send(msg)


@bot.command()
async def mute(ctx, ticker: str, minutes: int):
    """Mutes a specific ticker for X minutes."""
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        await ctx.send(f"⚠️ **{ticker}** is not in your watchlist.")
        return

    unmute_time = datetime.now() + timedelta(minutes=minutes)
    ticker_mutes[ticker] = unmute_time
    await ctx.send(f"🔇 Muted **{ticker}** alerts for {minutes} minutes.")


@bot.command()
async def scan(ctx):
    """Silent manual scan."""
    for ticker in WATCHLIST:
        result = get_market_data(ticker)
        if result:
            await send_formatted_alert(ctx, ticker, *result)
        await asyncio.sleep(1)


@bot.command()
async def status(ctx):
    now = datetime.now()
    nyse_holidays = holidays.NYSE()
    if paused_until and now < paused_until:
        rem = int((paused_until - now).total_seconds() / 60)
        await ctx.send(f"⏸️ **SYSTEM PAUSED** for {rem} mins.")
    elif now.date() in nyse_holidays:
        await ctx.send(f"💤 **Holiday**: {nyse_holidays.get(now.date())}")
    elif not is_market_open():
        await ctx.send("💤 Market Closed.")
    else:
        await ctx.send(f"🟢 **ONLINE**.")


@bot.command()
async def pause(ctx, minutes: int):
    global paused_until
    paused_until = datetime.now() + timedelta(minutes=minutes)
    await ctx.send(f"⏸️ System paused for {minutes} mins.")


@bot.command()
async def resume(ctx):
    global paused_until
    paused_until = None
    await ctx.send("▶️ Resuming operations.")


if __name__ == "__main__":
    bot.run(BOT_TOKEN)