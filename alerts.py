"""
alerts.py
─────────
Shared alert formatting and delivery. Used by both the background scanner
and the manual !scan command so embed layout is always consistent.
"""

import io
import logging
from datetime import datetime

import discord

logger = logging.getLogger(__name__)


async def send_alert(
    destination: discord.abc.Messageable,
    ticker: str,
    signal: str,
    price: float,
    rsi: float,
    target_band: float,
    chart: io.BytesIO,
) -> None:
    """
    Send a formatted signal alert to a channel or context.

    Both discord.TextChannel and commands.Context are Messageable, so this
    works identically for the background scanner (passes a channel) and the
    !scan command (passes ctx).
    """
    is_oversold = signal == "OVERSOLD"
    color = 0x2ECC71 if is_oversold else 0xE74C3C
    band_label = "Lower" if is_oversold else "Upper"
    banner = f"🚨 **{ticker} {signal}** at **${price:.2f}** (RSI: {rsi:.2f})"

    discord_file = discord.File(fp=chart, filename="chart.png")

    embed = discord.Embed(
        title=f"{ticker} — {signal} detected",
        description=f"Price crossed the {band_label} Bollinger Band.",
        color=color,
        timestamp=datetime.now(),
    )
    embed.add_field(name="Current Price", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="RSI (14)", value=f"**{rsi:.2f}**", inline=True)
    embed.add_field(name="Target Band", value=f"${target_band:.2f}", inline=True)
    embed.set_image(url="attachment://chart.png")

    await destination.send(content=banner, embed=embed, file=discord_file)
    logger.info("Alert sent: %s %s @ $%.2f (RSI: %.2f)", ticker, signal, price, rsi)
