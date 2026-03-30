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
import pytz

from config import BB_STD, TIMEZONE

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone(TIMEZONE)


def _pct_outside_band(price: float, band: float, signal: str) -> float:
    """
    How far price has moved through the band, as a percentage of the band value.
      Oversold:   (bbl - price) / bbl  → positive number, price is below bbl
      Overbought: (price - bbu) / bbu  → positive number, price is above bbu
    """
    if signal == "OVERSOLD":
        return (band - price) / band * 100
    return (price - band) / band * 100


def _pct_to_midline(price: float, bbm: float, signal: str) -> float:
    """
    Distance from current price to the BB midline (SMA20), as a percentage of price.
      Oversold:   midline > price → positive (upside to mean reversion)
      Overbought: price > midline → positive magnitude, shown as negative (downside)
    """
    if signal == "OVERSOLD":
        return (bbm - price) / price * 100
    return (price - bbm) / price * 100


async def send_alert(
    destination: discord.abc.Messageable,
    ticker: str,
    signal: str,
    price: float,
    rsi: float,
    target_band: float,
    bbm: float,
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

    # ── Computed stats ────────────────────────────────────────────────────────
    pct_outside = _pct_outside_band(price, target_band, signal)
    pct_to_mid = _pct_to_midline(price, bbm, signal)

    # ── Field labels and formatted values ────────────────────────────────────
    band_label = "Lower Band" if is_oversold else "Upper Band"
    outside_label = "% Below Band" if is_oversold else "% Above Band"
    pct_outside_str = f"−{pct_outside:.2f}%" if is_oversold else f"+{pct_outside:.2f}%"
    pct_mid_str = f"+{pct_to_mid:.2f}%" if is_oversold else f"−{pct_to_mid:.2f}%"

    # ── Footer timestamp in ET ────────────────────────────────────────────────
    et_time = datetime.now(EASTERN).strftime("%-I:%M %p ET")   # e.g. "10:34 AM ET"

    # ── Banner (plain message above the embed) ────────────────────────────────
    banner = f"🚨 **{ticker} {signal}** at **${price:.2f}** — RSI: {rsi:.2f}"

    discord_file = discord.File(fp=chart, filename="chart.png")

    embed = discord.Embed(color=color, timestamp=datetime.now())
    embed.set_author(name=f"{signal}  ·  BB + RSI signal")
    embed.title = ticker
    embed.description = f"**${price:.2f}**  ·  RSI **{rsi:.2f}**  ·  6-month daily"

    embed.add_field(name=band_label,     value=f"${target_band:.2f}", inline=True)
    embed.add_field(name=outside_label,  value=pct_outside_str,       inline=True)
    embed.add_field(name="% To Midline", value=pct_mid_str,           inline=True)

    embed.set_image(url="attachment://chart.png")
    embed.set_footer(text=f"{et_time}  ·  BB(20, {BB_STD})  ·  RSI(14)")

    await destination.send(content=banner, embed=embed, file=discord_file)
    logger.info(
        "Alert sent: %s %s @ $%.2f | RSI %.2f | %.2f%% outside band | %.2f%% to midline",
        ticker, signal, price, rsi, pct_outside, pct_to_mid,
    )
