# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Discord bot that monitors a stock watchlist using RSI + Bollinger Bands and sends alerts to a Discord channel when oversold/overbought conditions are detected. Designed to run continuously (e.g., on a Raspberry Pi) during NYSE market hours.

## Running the Bot

```bash
pip install -r requirements.txt
python main.py
```

Requires a `.env` file in the project root:
```
DISCORD_TOKEN=<bot token>
CHANNEL_ID=<target channel ID>
USER_ID=<your Discord user ID>
```

There is no test suite. Manual testing uses Discord commands: `!check TICKER` (unconditional fetch + chart) and `!scan` (full watchlist scan respecting mutes).

## Architecture

### Entry Point and Shared State

`main.py` initializes the Discord bot, attaches a `BotState` instance to `bot.state`, and loads all four cogs. Every cog accesses shared state via `self.bot.state`.

`state.py` owns all mutable runtime state: watchlist (persisted to `watchlist.json`), per-ticker mutes (persisted to `mutes.json`), scanner pause, and rate-limit cooldown flag. All state changes write to disk immediately.

`config.py` is the single source of truth for thresholds (RSI_LIMIT=30, BB_STD=2.0, POLL_SPEED_MINUTES=5, AUTO_MUTE_HOURS=1) and file paths. It loads `.env` from its own directory.

### Data Flow

```
Discord command / background task
  → cog handler
  → market_data.py  (yfinance download → pandas-ta RSI+BB → ScanAlert or TickerData)
  → market_data.create_chart  (mplfinance candlestick + BB + RSI)
  → alerts.send_alert  (Discord embed with chart PNG)
  → state.mute_ticker  (auto-mute, persist)
```

### Cogs

| Cog | File | Purpose |
|-----|------|---------|
| Scanner | `cogs/scanner_cog.py` | Background task; polls watchlist every 5 min during market hours |
| Watchlist | `cogs/watchlist_cog.py` | `!add` / `!remove` / `!watchlist` commands |
| Control | `cogs/control_cog.py` | `!mute` / `!pause` / `!resume` / `!status` |
| Analysis | `cogs/analysis_cog.py` | `!check`, `!scan` (watchlist), `!scan sp500` (full S&P 500 batch scan) |

### Scanner Scheduling

The scanner cog uses a smart sleep loop: it sleeps until market open (9:30 AM ET), skips weekends and NYSE holidays (via `holidays` library), then polls every `POLL_SPEED_MINUTES` during the session. Between tickers, it waits 1.5 seconds to avoid rate limits.

### Signal Logic

**Dual condition** — both must be true simultaneously:
- Oversold: `price < lower_band AND RSI < 30`
- Overbought: `price > upper_band AND RSI > 70`

This prevents false signals from single-indicator crossings.

### Rate Limit Handling

HTTP 429 from yfinance is re-raised by `market_data.py`. The scanner catches it, sets a cooldown flag on `BotState`, and pauses for `RATE_LIMIT_COOLDOWN_MINUTES` (30). The `!scan` command stops early on 429 and reports the error.

### S&P 500 Scanning

`!scan sp500` uses a different code path from the watchlist scan. `sp500.py` fetches the constituent list from Wikipedia (cached to `sp500.json` for 7 days, with `BRK.B`-style symbols normalized to `BRK-B` for yfinance). `market_data.fetch_batch()` then downloads OHLCV for ~50 tickers in a single yfinance call (`group_by="ticker"`, `threads=True`) and runs RSI/BB locally on each sub-DataFrame. This is dramatically fewer HTTP requests than looping `scan_ticker()`. The cog processes chunks of 50 with a 2s delay between them; on hits, it sorts by signal magnitude (most extreme first) and sends individual alerts with charts.

### Long-running I/O

`market_data.py` functions (`scan_ticker`, `check_ticker`, `create_chart`) are run via `asyncio.to_thread()` in cogs because yfinance and mplfinance are synchronous and would block the event loop.
