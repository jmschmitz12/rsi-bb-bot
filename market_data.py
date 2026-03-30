"""
market_data.py
──────────────
All market-facing logic: data download, technical indicators, signal detection,
and chart generation. No Discord code lives here.

Two public entry points:
    scan_ticker(ticker)  → ScanAlert | None      (used by background scanner)
    check_ticker(ticker) → TickerData | None     (used by !check command)

Both delegate to _fetch_and_process(), which is the single place that
downloads data and computes RSI + Bollinger Bands.  Column names are
detected once here and carried in the return value — no second detection
pass in the chart function.
"""

import io
import logging
from datetime import datetime, time as dt_time
from typing import NamedTuple

import holidays
import mplfinance as mpf
import pandas as pd
import pandas_ta as ta
import pytz
import yfinance as yf

from config import BB_STD, RSI_LIMIT, TIMEZONE

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone(TIMEZONE)
_NYSE_HOLIDAYS = holidays.NYSE()


# ── Return types ──────────────────────────────────────────────────────────────

class TickerData(NamedTuple):
    """Full processed snapshot — used by the !check command."""
    price: float
    rsi: float
    bbl: float
    bbu: float
    bbm: float         # BB midline (SMA20) — mean reversion target
    bbl_col: str       # e.g. "BBL_20_2.0" — kept so the chart function never re-scans
    bbu_col: str
    bbm_col: str
    df: pd.DataFrame


class ScanAlert(NamedTuple):
    """A triggered scanner condition — used by the background loop and !scan."""
    signal: str        # "OVERSOLD" or "OVERBOUGHT"
    price: float
    rsi: float
    target_band: float # the band that was crossed (bbl for oversold, bbu for overbought)
    bbm: float         # BB midline (SMA20) — mean reversion target
    bbl_col: str
    bbu_col: str
    bbm_col: str
    df: pd.DataFrame


# ── Market hours ──────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """
    Return True if the NYSE is currently open.

    Uses pytz to convert to America/New_York regardless of the host machine's
    locale — critical for a Raspberry Pi that may be running in any timezone.
    """
    now = datetime.now(EASTERN)

    if now.weekday() >= 5:          # Saturday = 5, Sunday = 6
        return False
    if now.date() in _NYSE_HOLIDAYS:
        return False

    market_open = dt_time(9, 30)
    market_close = dt_time(16, 0)
    return market_open <= now.time() <= market_close


# ── Core data pipeline ────────────────────────────────────────────────────────

def _fetch_and_process(ticker: str) -> TickerData | None:
    """
    Download 6 months of daily OHLCV data and compute RSI(14) + BB(20).

    Returns a TickerData on success, None on bad data.
    Re-raises on HTTP 429 so callers can trigger a rate-limit cooldown.
    """
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)

        # Newer yfinance versions return a MultiIndex — flatten it
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty or len(df) < 20:
            logger.warning("%s: insufficient data (%d rows)", ticker, len(df))
            return None

        df["RSI"] = ta.rsi(df["Close"], length=14)

        bb = ta.bbands(df["Close"], length=20, std=BB_STD)
        if bb is None:
            logger.warning("%s: Bollinger Band calculation returned None", ticker)
            return None

        df = pd.concat([df, bb], axis=1)

        bbl_col = next((c for c in df.columns if c.startswith("BBL")), None)
        bbu_col = next((c for c in df.columns if c.startswith("BBU")), None)
        bbm_col = next((c for c in df.columns if c.startswith("BBM")), None)

        if not bbl_col or not bbu_col or not bbm_col:
            logger.warning("%s: could not locate BB columns in %s", ticker, df.columns.tolist())
            return None

        return TickerData(
            price=float(df["Close"].iloc[-1]),
            rsi=float(df["RSI"].iloc[-1]),
            bbl=float(df[bbl_col].iloc[-1]),
            bbu=float(df[bbu_col].iloc[-1]),
            bbm=float(df[bbm_col].iloc[-1]),
            bbl_col=bbl_col,
            bbu_col=bbu_col,
            bbm_col=bbm_col,
            df=df,
        )

    except Exception as e:
        if "429" in str(e):
            logger.error("%s: rate limit hit (429) — re-raising", ticker)
            raise
        logger.error("%s: unexpected error — %s", ticker, e)
        return None


# ── Public entry points ───────────────────────────────────────────────────────

def scan_ticker(ticker: str) -> ScanAlert | None:
    """
    Check whether a ticker has triggered an oversold or overbought signal.

    Returns a ScanAlert if conditions are met, None otherwise.
    Propagates HTTP 429 exceptions so the scanner loop can handle cooldown.
    """
    data = _fetch_and_process(ticker)
    if data is None:
        return None

    if data.price < data.bbl and data.rsi < RSI_LIMIT:
        return ScanAlert(
            signal="OVERSOLD",
            price=data.price,
            rsi=data.rsi,
            target_band=data.bbl,
            bbm=data.bbm,
            bbl_col=data.bbl_col,
            bbu_col=data.bbu_col,
            bbm_col=data.bbm_col,
            df=data.df,
        )

    if data.price > data.bbu and data.rsi > (100 - RSI_LIMIT):
        return ScanAlert(
            signal="OVERBOUGHT",
            price=data.price,
            rsi=data.rsi,
            target_band=data.bbu,
            bbm=data.bbm,
            bbl_col=data.bbl_col,
            bbu_col=data.bbu_col,
            bbm_col=data.bbm_col,
            df=data.df,
        )

    return None


def check_ticker(ticker: str) -> TickerData | None:
    """
    Fetch full TickerData unconditionally — used by the !check command.
    Returns None on failure; propagates HTTP 429.
    """
    return _fetch_and_process(ticker)


# ── Custom chart style ────────────────────────────────────────────────────────

_MARKET_COLORS = mpf.make_marketcolors(
    up="#2ecc71",       # green candles
    down="#e74c3c",     # red candles
    edge="inherit",
    wick="inherit",
    ohlc="inherit",
)

_CHART_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=_MARKET_COLORS,
    figcolor="#1e1f22",         # outer figure background
    facecolor="#2b2d31",        # chart panel background
    gridcolor="#3a3b3c",
    gridstyle="--",
    gridaxis="both",
    y_on_right=True,
    rc={
        "axes.labelcolor":  "#b5bac1",
        "axes.edgecolor":   "#3a3b3c",
        "xtick.color":      "#87898c",
        "ytick.color":      "#87898c",
        "font.size":        9,
    },
)


# ── Charting ──────────────────────────────────────────────────────────────────

def create_chart(
    df: pd.DataFrame,
    ticker: str,
    bbl_col: str,
    bbu_col: str,
    bbm_col: str,
) -> io.BytesIO:
    """
    Render a 50-candle candlestick chart with Bollinger Bands, midline, and RSI
    using a custom dark theme.

    Column names are passed in from the caller (detected once in
    _fetch_and_process) rather than re-scanned here.

    Returns a BytesIO PNG ready to pass to discord.File().
    """
    plot_df = df.tail(50)
    image_stream = io.BytesIO()

    extra_plots = [
        mpf.make_addplot(plot_df[bbu_col], color="#f39c12", width=1.0, panel=0),
        mpf.make_addplot(plot_df[bbm_col], color="#f39c12", width=0.6, linestyle="--", panel=0),
        mpf.make_addplot(plot_df[bbl_col], color="#f39c12", width=1.0, panel=0),
        mpf.make_addplot(
            plot_df["RSI"],
            color="#9b59b6",
            width=1.8,
            panel=1,
            ylabel="RSI",
            ylim=(0, 100),
        ),
    ]

    mpf.plot(
        plot_df,
        type="candle",
        style=_CHART_STYLE,
        addplot=extra_plots,
        title=f"\n{ticker}  —  BB(20, {BB_STD})  ·  RSI(14)",
        volume=False,
        ylabel="Price",
        panel_ratios=(3, 1),
        figratio=(10, 5),
        figscale=1.3,
        tight_layout=True,
        savefig=dict(fname=image_stream, format="png", bbox_inches="tight", dpi=130),
    )

    image_stream.seek(0)
    return image_stream
