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
    if signal == "OVERSOLD":
        return (band - price) / band * 100
    return (price - band) / band * 100


def _pct_to_midline(price: float, bbm: float, signal: str) -> float:
    if signal == "OVERSOLD":
        return (bbm - price) / price * 100
    return (price - bbm) / price * 100


def format_day_change(day_change: float, day_change_pct: float) -> str:
    if day_change >= 0:
        return f"▲ ${day_change:.2f} (+{day_change_pct:.2f}%)"
    return f"▼ ${abs(day_change):.2f} (−{abs(day_change_pct):.2f}%)"


async def send_alert(
    destination: discord.abc.Messageable,
    ticker: str,
    signal: str,
    price: float,
    rsi: float,
    target_band: float,
    bbm: float,
    chart: io.BytesIO,
    company_name: str | None = None,
    day_change: float = 0.0,
    day_change_pct: float = 0.0,
) -> None:
    is_oversold = signal == "OVERSOLD"
    color = 0x2ECC71 if is_oversold else 0xE74C3C

    pct_outside = _pct_outside_band(price, target_band, signal)
    pct_to_mid = _pct_to_midline(price, bbm, signal)

    band_label = "Lower Band" if is_oversold else "Upper Band"
    pct_outside_str = f"−{pct_outside:.2f}%" if is_oversold else f"+{pct_outside:.2f}%"
    pct_mid_str = f"+{pct_to_mid:.2f}%" if is_oversold else f"−{pct_to_mid:.2f}%"

    change_str = format_day_change(day_change, day_change_pct)
    et_time = datetime.now(EASTERN).strftime("%-I:%M %p ET")

    title = f"{ticker} — {company_name}" if company_name else ticker
    banner = f"🚨 **{title}** is **{signal}** at **${price:.2f}**"

    discord_file = discord.File(fp=chart, filename="chart.png")

    embed = discord.Embed(color=color, timestamp=datetime.now())
    embed.set_author(name=f"{signal}  ·  BB + RSI Signal")
    embed.title = title
    embed.description = f"**${price:.2f}**  ·  {change_str}"

    embed.add_field(name="RSI",        value=f"**{rsi:.2f}**",                          inline=True)
    embed.add_field(name=band_label,   value=f"${target_band:.2f} ({pct_outside_str})", inline=True)
    embed.add_field(name="Midline",    value=f"${bbm:.2f} ({pct_mid_str})",             inline=True)

    embed.set_image(url="attachment://chart.png")
    embed.set_footer(text=f"{et_time}  ·  BB(20, {BB_STD})  ·  RSI(14)")

    await destination.send(content=banner, embed=embed, file=discord_file)
    logger.info(
        "Alert sent: %s %s @ $%.2f | RSI %.2f | %.2f%% outside band | %.2f%% to midline",
        ticker, signal, price, rsi, pct_outside, pct_to_mid,
    )
