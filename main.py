"""
main.py
───────
Entry point. Sets up logging, creates the bot, loads all cogs, and starts.
No business logic lives here — it's wiring only.
"""

import asyncio
import logging
import logging.handlers

import discord
from discord.ext import commands

from config import BOT_TOKEN, LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES
from state import BotState

# ── Cog registry ──────────────────────────────────────────────────────────────
# Add or remove cog module paths here to enable/disable feature groups.
COGS: list[str] = [
    "cogs.scanner_cog",
    "cogs.watchlist_cog",
    "cogs.control_cog",
    "cogs.analysis_cog",
]


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """
    Configure root logger with:
      • Console output  (INFO and above)
      • Rotating file   (INFO and above, 5 MB × 7 files ≈ 35 MB max)

    Noisy third-party libraries are capped at WARNING so they don't flood
    the log with connection noise and HTTP request spam.
    """
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Suppress noisy third-party output
    for noisy in ("discord", "yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ── Bot setup ─────────────────────────────────────────────────────────────────

async def main() -> None:
    setup_logging()

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    bot.state = BotState()  # shared mutable state — accessed in all cogs via self.bot.state

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

    async with bot:
        for cog_path in COGS:
            await bot.load_extension(cog_path)
            logger.info("Loaded cog: %s", cog_path)
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
