import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ────────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
CHANNEL_ID: int = int(os.getenv("CHANNEL_ID", "0"))
MY_USER_ID: int = int(os.getenv("USER_ID", "0"))

# ── Scanner settings ───────────────────────────────────────────────────────────
RSI_LIMIT: float = 30.0          # oversold threshold; 100 - RSI_LIMIT = overbought
BB_STD: float = 2.0              # Bollinger Band standard deviation multiplier
POLL_SPEED_MINUTES: int = 5      # how often the background scanner runs
RATE_LIMIT_COOLDOWN_MINUTES: int = 30

# ── Persistence ────────────────────────────────────────────────────────────────
WATCHLIST_FILE: str = "watchlist.json"
LOG_FILE: str = "bot.log"
LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB per log file
LOG_BACKUP_COUNT: int = 7             # keep 7 rotated files (~35 MB total)

# ── Market hours ───────────────────────────────────────────────────────────────
TIMEZONE: str = "America/New_York"   # NYSE timezone — independent of Pi's locale

# ── Default watchlist (used only when watchlist.json does not exist) ───────────
DEFAULT_WATCHLIST: list[str] = [
    "AMZN", "NVDA", "SPY", "QQQ", "META", "MSFT", "PM", "DAL", "AAL",
    "GOOG", "KO", "AMD", "AVGO", "PLTR", "TSLA",
]
