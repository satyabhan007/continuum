#!/usr/bin/env python3
"""Stock market data analyzer.

Analyzes OHLCV (Open/High/Low/Close/Volume) stock market data from CSV files,
or live data fetched from Stooq, and produces:

  * a human-readable report on stdout
  * optional machine-readable JSON       (--json PATH)
  * optional flat metrics CSV            (--csv PATH)
  * optional per-day indicator series CSV (--export-series PATH)

The analyzer runs on the Python standard library alone. The third-party
`requests` package is required only for the optional `--fetch` live-data mode.

Percentages are stored internally as fractions (0.12 means 12%). Annualized
figures use 252 trading days per year.

Exit codes:
  0  analysis produced without errors
  1  one or more inputs failed (analysis still produced for the rest)
  2  no input could be analyzed at all

Usage examples:
  python3 stock_analyzer.py AAPL.csv
  python3 stock_analyzer.py AAPL.csv MSFT.csv --csv summary.csv
  python3 stock_analyzer.py --fetch aapl --start 2023-01-01 --json out.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

VERSION = "1.0.0"
TRADING_DAYS_PER_YEAR = 252
STOOQ_URL = "https://stooq.com/q/d/l/"
STOOQ_BLOCK_HINTS = ("__verify", "requires javascript", "<!doctype html", "<html")

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y%m%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%Y-%m-%d %H:%M:%S",
)


class DataError(Exception):
    """Raised when a data source cannot be parsed or is unusable."""


class AnalysisError(Exception):
    """Raised when there is not enough data to analyze a symbol."""


class FetchError(Exception):
    """Raised when live data cannot be fetched."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    """One trading day of OHLCV data."""

    d: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Config:
    """Analyzer settings."""

    sma_windows: tuple = (20, 50, 200)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_window: int = 20
    bollinger_std: float = 2.0
    atr_period: int = 14
    risk_free: float = 0.0
    top: int = 3


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_date(text: str) -> Optional[date]:
    """Parse a date string using several common formats. Returns None on failure."""
    text = text.strip().strip('"')
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_float(text) -> Optional[float]:
    """Parse a number, tolerating thousands separators and common blanks."""
    if text is None:
        return None
    s = str(text).strip().strip('"')
    if s in ("", "-", "--", "n/a", "N/A", "null", "NULL", "NaN", "nan"):
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _find_key(row_keys: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    for cand in candidates:
        for key in row_keys:
            if key == cand:
                return key
    return None


def _normalize_row(row: dict) -> dict:
    """Map a CSV row to stripped/lowercase keys (first occurrence wins)."""
    norm: dict = {}
    for k, v in row.items():
        if isinstance(k, str):
            norm.setdefault(k.strip().lower(), v)
    return norm


def bars_from_dicts(rows: Iterable[dict], source: str, prefer_adj_close: bool = True):
    """Convert CSV rows (dicts) into Bar objects.

    Returns (bars, warnings, price_column). Column names are matched case-
    insensitively after stripping whitespace. Missing Open/High/Low are filled
    with Close. Missing Volume becomes 0. Rows without a usable Date + Close
    are skipped with a warning. `source` is used in warning messages.
    """
    normalized = [_normalize_row(r) for r in rows]
    if not normalized:
        raise DataError("no usable rows found")

    first = normalized[0]
    date_key = _find_key(list(first), ("date", "timestamp", "time", "datetime"))
    if date_key is None:
        raise DataError("no date column found")
    open_key = _find_key(list(first), ("open", "open price"))
    high_key = _find_key(list(first), ("high", "high price"))
    low_key = _find_key(list(first), ("low", "low price"))
    close_key = _find_key(list(first), ("close", "close/last", "close last", "price"))
    adj_key = _find_key(
        list(first), ("adj close", "adjusted close", "close_adj", "adj_close")
    )
    vol_key = _find_key(list(first), ("volume", "vol"))

    bars: list[Bar] = []
    warnings: list[str] = []
    price_col_used: Optional[str] = None
    use_key = None
    if prefer_adj_close and adj_key is not None:
        use_key = adj_key
        price_col_used = "adj close"
    if use_key is None:
        use_key = close_key
        price_col_used = "close"

    for rows_seen, row in enumerate(normalized, start=2):
        d = parse_date(str(row.get(date_key, "")))
        close = parse_float(row.get(use_key) if use_key else None)
        if close is None and use_key == adj_key and close_key is not None:
            close = parse_float(row.get(close_key))
        if d is None or close is None:
            warnings.append(f"{source}: skipped row {rows_seen} (bad date or price)")
            continue

        def _val(key: Optional[str], fallback: float) -> float:
            v = parse_float(row.get(key)) if key else None
            return fallback if v is None else v

        open_ = _val(open_key, close)
        high = max(_val(high_key, close), open_, close)
        low = min(_val(low_key, close), open_, close)
        volume = _val(vol_key, 0.0)

        bars.append(Bar(d=d, open=open_, high=high, low=low, close=close, volume=volume))

    if not bars:
        raise DataError("no usable rows found")
    return bars, warnings, (price_col_used or "close")


def finalize_bars(bars: list[Bar], source: str):
    """Sort bars by date ascending and drop duplicates (keeping the last seen).

    Returns (bars, warnings).
    """
    warnings: list[str] = []
    original = list(bars)
    bars = sorted(bars, key=lambda b: b.d)
    if [b.d for b in original] != [b.d for b in bars]:
        warnings.append(f"{source}: dates were not sorted, sorted ascending")
    seen: dict[date, Bar] = {}
    dupes = 0
    for b in bars:
        if b.d in seen:
            dupes += 1
        seen[b.d] = b
    if dupes:
        warnings.append(f"{source}: dropped {dupes} duplicate date row(s), kept last")
    return list(seen.values()), warnings


def load_csv(path: str, prefer_adj_close: bool = True):
    """Load an OHLCV CSV file.

    Returns (symbol, bars, warnings, price_column). Symbol defaults to the
    file stem. Accepts UTF-8 (with optional BOM) and UTF-16 encoded files.
    """
    p = Path(path)
    if not p.exists():
        raise DataError(f"file not found: {path}")
    if p.is_dir():
        raise DataError(f"not a file: {path}")
    if p.stat().st_size == 0:
        raise DataError("file is empty")
    text = None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = p.read_text(encoding=encoding)
            if text and "\x00" not in text:
                break
            text = None
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        raise DataError(f"could not decode {path} as UTF-8 or UTF-16")
    if not text.strip():
        raise DataError("file contains no data rows")
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except csv.Error as exc:
        raise DataError(f"CSV parse error: {exc}") from exc
    if not rows:
        raise DataError("file contains no data rows")
    if reader.fieldnames is None or all((f or "").strip() == "" for f in reader.fieldnames):
        raise DataError("file has no usable header row")
    bars, warnings, price_col = bars_from_dicts(rows, str(path), prefer_adj_close)
    bars, more = finalize_bars(bars, str(path))
    warnings.extend(more)
    return p.stem, bars, warnings, price_col


def filter_range(bars: list[Bar], start: Optional[date], end: Optional[date]) -> list[Bar]:
    """Keep bars within [start, end] inclusive."""
    return [
        b for b in bars if (start is None or b.d >= start) and (end is None or b.d <= end)
    ]


# ---------------------------------------------------------------------------
# Live data (Stooq key-free CSV endpoint, optional)
# ---------------------------------------------------------------------------


def _looks_like_block_page(text: str) -> bool:
    """Detect a JavaScript/anti-bot challenge page instead of CSV data."""
    head = (text or "").lstrip()[:512].lower()
    return any(hint in head for hint in STOOQ_BLOCK_HINTS)


def _parse_stooq_text(text: str) -> list[Bar]:
    text = (text or "").strip()
    if not text:
        return []
    first = text.splitlines()[0].strip().lower()
    if "no data" in first or "exceeded" in first or "limit" in first:
        return []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "Date" not in reader.fieldnames:
        return []
    bars, _, _ = bars_from_dicts(list(reader), "stooq", prefer_adj_close=False)
    return bars


def fetch_stooq(
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    http_get: Optional[Callable] = None,
    timeout: float = 15.0,
):
    """Fetch daily OHLCV bars for `symbol` from Stooq.

    Returns (symbol_used, bars). Raises FetchError on failure. `http_get` is
    injectable for testing.

    Stooq sometimes serves a JavaScript browser-verification page to
    non-interactive clients; that is detected and reported as an explicit,
    actionable error rather than retried.
    """
    sym = (symbol or "").strip().lower()
    if not sym:
        raise FetchError("empty symbol")
    if http_get is None:
        try:
            import requests  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only without requests
            raise FetchError(
                "the `requests` package is required for --fetch (pip install requests)"
            ) from exc
        http_get = requests.get

    candidates = [sym]
    if "." not in sym:
        candidates.append(sym + ".us")

    last_err: Exception = ValueError("no data")
    for cand in candidates:
        params = {"s": cand, "i": "d"}
        if start is not None:
            params["d1"] = start.strftime("%Y%m%d")
        if end is not None:
            params["d2"] = end.strftime("%Y%m%d")
        try:
            resp = http_get(STOOQ_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            text = getattr(resp, "text", "") or ""
        except Exception as exc:  # network/HTTP failures retry the next candidate
            last_err = exc
            continue
        if _looks_like_block_page(text):
            raise FetchError(
                f"could not fetch {symbol}: Stooq returned a browser-verification "
                "page instead of data (automated access blocked). Download the "
                "CSV from https://stooq.com in a browser and pass the file path "
                "to the analyzer instead."
            )
        try:
            bars = _parse_stooq_text(text)
        except Exception as exc:  # parse failures retry the next candidate
            last_err = exc
            continue
        if bars:
            return cand, bars
        last_err = ValueError(f"no data returned for {cand}")
    raise FetchError(f"could not fetch {symbol}: {last_err}")


# ---------------------------------------------------------------------------
# Indicator math (pure functions on lists)
# ---------------------------------------------------------------------------


def sma_series(values: Sequence[float], window: int) -> list[Optional[float]]:
    """Simple moving average. None until the window is full."""
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if window < 1 or n < window:
        return out
    s = math.fsum(values[:window])
    out[window - 1] = s / window
    for i in range(window, n):
        s += values[i] - values[i - window]
        out[i] = s / window
    return out


def ema_series(values: Sequence[float], period: int) -> list[Optional[float]]:
    """Exponential moving average seeded with the first SMA. None before seed."""
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if period < 1 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = math.fsum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def daily_returns(closes: Sequence[float]) -> list[float]:
    """Fractional close-to-close returns."""
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


def rsi_series(closes: Sequence[float], period: int) -> list[Optional[float]]:
    """RSI using Wilder smoothing. First value appears at index `period`."""
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if period < 1 or n <= period:
        return out

    def _rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_gain + avg_loss == 0.0:
            return 50.0
        return 100.0 * avg_gain / (avg_gain + avg_loss)

    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, period + 1)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, period + 1)]
    avg_gain = math.fsum(gains) / period
    avg_loss = math.fsum(losses) / period
    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def bollinger_series(closes: Sequence[float], window: int, num_std: float):
    """Bollinger bands. Returns (mid, upper, lower) series."""
    n = len(closes)
    mid = sma_series(closes, window)
    upper: list[Optional[float]] = [None] * n
    lower: list[Optional[float]] = [None] * n
    for i in range(window - 1, n):
        seg = closes[i - window + 1 : i + 1]
        m = mid[i]
        if m is None:
            continue
        sd = statistics.pstdev(seg)
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return mid, upper, lower


def macd_series(closes: Sequence[float], fast: int, slow: int, signal_period: int):
    """MACD, signal line, histogram. Returns (macd, signal, hist) series."""
    n = len(closes)
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd: list[Optional[float]] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd[i] = ema_fast[i] - ema_slow[i]
    first = slow - 1
    signal: list[Optional[float]] = [None] * n
    if n >= first + signal_period:
        seed_vals = [macd[i] for i in range(first, first + signal_period)]
        k = 2.0 / (signal_period + 1.0)
        prev = math.fsum(seed_vals) / signal_period
        signal[first + signal_period - 1] = prev
        for i in range(first + signal_period, n):
            prev = macd[i] * k + prev * (1.0 - k)
            signal[i] = prev
    hist: list[Optional[float]] = [
        (macd[i] - signal[i]) if (macd[i] is not None and signal[i] is not None) else None
        for i in range(n)
    ]
    return macd, signal, hist


def atr_series(bars: Sequence[Bar], period: int) -> list[Optional[float]]:
    """Average True Range using Wilder smoothing.

    The first ATR (at index `period`) is the mean of TR[1..period], where TR
    of day 0 (which has no previous close) is excluded.
    """
    n = len(bars)
    out: list[Optional[float]] = [None] * n
    if period < 1 or n <= period:
        return out
    trs: list[float] = []
    for i in range(n):
        b = bars[i]
        if i == 0:
            tr = b.high - b.low
        else:
            pc = bars[i - 1].close
            tr = max(b.high - b.low, abs(b.high - pc), abs(b.low - pc))
        trs.append(tr)
    atr = math.fsum(trs[1 : period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def drawdown_series(closes: Sequence[float]) -> list[float]:
    """Running drawdown from the running peak, as fractions."""
    out: list[float] = []
    peak = -math.inf
    for c in closes:
        peak = max(peak, c)
        out.append(c / peak - 1.0 if peak > 0 else 0.0)
    return out


def max_drawdown_info(closes: Sequence[float], dates: Sequence[date]) -> dict:
    """Max drawdown depth plus peak/trough/recovery dates."""
    dd = drawdown_series(closes)
    trough_i = min(range(len(dd)), key=lambda i: dd[i])
    peak_i = max(range(trough_i + 1), key=lambda i: closes[i])
    depth = dd[trough_i]
    recovery_date: Optional[str] = None
    peak_close = closes[peak_i]
    for j in range(trough_i + 1, len(closes)):
        if closes[j] >= peak_close:
            recovery_date = dates[j].isoformat()
            break
    return {
        "depth": depth,
        "peak_date": dates[peak_i].isoformat(),
        "trough_date": dates[trough_i].isoformat(),
        "recovery_date": recovery_date,
    }


def cagr(closes: Sequence[float]) -> Optional[float]:
    """Compound annual growth rate over trading days."""
    if len(closes) < 2 or closes[0] <= 0 or closes[-1] <= 0:
        return None
    n = len(closes) - 1
    return (closes[-1] / closes[0]) ** (TRADING_DAYS_PER_YEAR / n) - 1.0


def sharpe_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]:
    """Annualized Sharpe ratio using simple daily excess returns."""
    if len(returns) < 2:
        return None
    rf_daily = risk_free / TRADING_DAYS_PER_YEAR
    excess = [r - rf_daily for r in returns]
    sd = statistics.stdev(excess)
    if sd == 0.0:
        return None
    return statistics.fmean(excess) / sd * math.sqrt(TRADING_DAYS_PER_YEAR)


def sortino_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]:
    """Annualized Sortino ratio using downside deviation."""
    if len(returns) < 2:
        return None
    rf_daily = risk_free / TRADING_DAYS_PER_YEAR
    excess = [r - rf_daily for r in returns]
    downside = math.sqrt(statistics.fmean(min(r, 0.0) ** 2 for r in excess))
    if downside == 0.0:
        return None
    return statistics.fmean(excess) / downside * math.sqrt(TRADING_DAYS_PER_YEAR)


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation quantile of a sample (q in [0, 1])."""
    n = len(values)
    if n == 0:
        return None
    s = sorted(values)
    if n == 1 or q <= 0:
        return s[0]
    if q >= 1:
        return s[-1]
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def skewness(values: Sequence[float]) -> Optional[float]:
    """Sample skewness (adjusted Fisher-Pearson, G1)."""
    n = len(values)
    if n < 3:
        return None
    m = statistics.fmean(values)
    m2 = statistics.fmean((x - m) ** 2 for x in values)
    m3 = statistics.fmean((x - m) ** 3 for x in values)
    if m2 == 0.0:
        return None
    g1 = m3 / m2 ** 1.5
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def streaks(returns: Sequence[float]) -> dict:
    """Longest up/down streaks and the current streak."""
    best_up = best_down = 0
    cur_sign = 0
    cur_len = 0
    for r in returns:
        s = 1 if r > 0 else (-1 if r < 0 else 0)
        if s == 0 or s != cur_sign:
            cur_sign = s if s != 0 else 0
            cur_len = 0 if s == 0 else 1
        else:
            cur_len += 1
        if cur_sign == 1:
            best_up = max(best_up, cur_len)
        elif cur_sign == -1:
            best_down = max(best_down, cur_len)
    return {
        "max_up_streak": best_up,
        "max_down_streak": best_down,
        "current_streak": cur_len if cur_sign != 0 else 0,
        "current_direction": {1: "up", -1: "down", 0: "flat"}[cur_sign],
    }


def cross_events(fast: Sequence[Optional[float]], slow: Sequence[Optional[float]], dates):
    """Detect sign-change crossings of fast over slow.

    Returns a list of (date, kind) where kind is "up" or "down", in order.
    """
    events: list[tuple[date, str]] = []
    prev_sign = 0
    for i in range(len(fast)):
        f, s = fast[i], slow[i]
        if f is None or s is None:
            continue
        sign = 1 if f > s else (-1 if f < s else 0)
        if sign != 0:
            if prev_sign != 0 and sign != prev_sign:
                events.append((dates[i], "up" if sign > 0 else "down"))
            prev_sign = sign
    return events


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def classify_trend(close: float, smas: dict[int, Optional[float]]) -> Optional[str]:
    """Classify the latest trend from the close versus the SMA stack.

    Returns "bullish" when the close is strictly above every defined SMA,
    "bearish" when it is strictly below every defined SMA, "mixed" when it
    sits on both sides of the stack, "neutral" when it exactly equals every
    defined SMA (no strict relation), and None when no SMA is defined.

    The alignment of the SMA stack itself (shorter above longer) is reported
    separately via the golden/death-cross signals, so this classification
    focuses on where the price stands relative to its moving averages.
    """
    defined = [v for v in smas.values() if v is not None]
    if not defined:
        return None
    if all(close > v for v in defined):
        return "bullish"
    if all(close < v for v in defined):
        return "bearish"
    if all(close == v for v in defined):
        return "neutral"
    return "mixed"


def _insights(res: dict) -> list[str]:
    """Deterministic plain-language summary sentences for a symbol."""
    out: list[str] = []
    prices = res["prices"]
    returns = res["returns"]
    risk = res["risk"]
    ind = res["indicators"]

    trend = res["signals"].get("trend")
    if trend:
        out.append(f"Trend: {trend} (close vs SMA stack).")

    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi >= 70:
            out.append(f"RSI {rsi:.1f} is overbought (>= 70).")
        elif rsi <= 30:
            out.append(f"RSI {rsi:.1f} is oversold (<= 30).")
        else:
            out.append(f"RSI {rsi:.1f} is neutral (30-70).")

    below = prices.get("pct_below_52w_high")
    if below is not None:
        out.append(f"Last close is {below * 100:.1f}% below the 52-week high.")

    macd_v, sig_v = ind["macd"].get("macd"), ind["macd"].get("signal")
    if macd_v is not None and sig_v is not None:
        side = "above" if macd_v > sig_v else ("below" if macd_v < sig_v else "equal to")
        out.append(f"MACD is {side} its signal line.")

    vol = risk.get("ann_volatility")
    if vol is not None:
        char = "low" if vol < 0.15 else ("moderate" if vol < 0.30 else "high")
        out.append(f"Annualized volatility is {vol * 100:.1f}% ({char}).")

    mdd = risk["max_drawdown"]["depth"]
    cur_dd = prices.get("current_dd")
    if mdd is not None and mdd < 0:
        if cur_dd is not None and cur_dd < -0.05:
            out.append(
                f"Still {abs(cur_dd) * 100:.1f}% below the period peak"
                f" (max drawdown was {abs(mdd) * 100:.1f}%)."
            )
        else:
            out.append(f"Max drawdown was {abs(mdd) * 100:.1f}%, now fully recovered.")

    total = returns.get("total_return")
    if total is not None:
        cagr_v = returns.get("cagr")
        extra = f", CAGR {cagr_v * 100:+.1f}%" if cagr_v is not None else ""
        out.append(f"Total return over the period: {total * 100:+.1f}%{extra}.")
    return out


def analyze(symbol: str, bars: list[Bar], cfg: Config) -> dict:
    """Compute the full analysis for one symbol. Raises AnalysisError if unusable."""
    if len(bars) < 2:
        raise AnalysisError(f"need at least 2 bars, got {len(bars)}")
    closes = [b.close for b in bars]
    if any(c <= 0 for c in closes):
        raise AnalysisError("non-positive close price found; data is unusable")
    dates = [b.d for b in bars]
    n = len(bars)
    rets = daily_returns(closes)

    smas = {w: sma_series(closes, w) for w in cfg.sma_windows}
    rsi = rsi_series(closes, cfg.rsi_period)
    macd, macd_sig, macd_hist = macd_series(
        closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal
    )
    bb_mid, bb_up, bb_lo = bollinger_series(closes, cfg.bollinger_window, cfg.bollinger_std)
    atr = atr_series(bars, cfg.atr_period)
    dd = drawdown_series(closes)

    def last_defined(series):
        for v in reversed(series):
            if v is not None:
                return v
        return None

    # 52-week window (or whole period if shorter)
    w52 = min(TRADING_DAYS_PER_YEAR, n)
    hi52 = max(closes[-w52:])
    lo52 = min(closes[-w52:])

    best_i = max(range(len(rets)), key=lambda i: rets[i])
    worst_i = min(range(len(rets)), key=lambda i: rets[i])
    stk = streaks(rets)

    total_return = closes[-1] / closes[0] - 1.0
    ann_vol = statistics.stdev(rets) * math.sqrt(TRADING_DAYS_PER_YEAR) if len(rets) >= 2 else None
    sharpe = sharpe_ratio(rets, cfg.risk_free)
    sortino = sortino_ratio(rets, cfg.risk_free)
    cagr_v = cagr(closes)
    mdd = max_drawdown_info(closes, dates)
    calmar = (cagr_v / abs(mdd["depth"])) if (cagr_v is not None and mdd["depth"] < 0) else None

    pos_days = sum(1 for r in rets if r > 0)
    avg_vol = statistics.fmean(b.volume for b in bars)

    sma_latest = {w: last_defined(smas[w]) for w in cfg.sma_windows}
    golden = [d for d, k in cross_events(smas[min(cfg.sma_windows)], smas[max(cfg.sma_windows)], dates) if k == "up"]
    death = [d for d, k in cross_events(smas[min(cfg.sma_windows)], smas[max(cfg.sma_windows)], dates) if k == "down"]
    macd_crosses = cross_events(macd, macd_sig, dates)

    res = {
        "symbol": symbol,
        "overview": {
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "trading_days": n,
            "first_close": closes[0],
            "last_close": closes[-1],
        },
        "prices": {
            "min_close": min(closes),
            "min_date": dates[closes.index(min(closes))].isoformat(),
            "max_close": max(closes),
            "max_date": dates[closes.index(max(closes))].isoformat(),
            "high_52w": hi52,
            "low_52w": lo52,
            "pct_below_52w_high": closes[-1] / hi52 - 1.0,
            "current_dd": dd[-1],
        },
        "returns": {
            "total_return": total_return,
            "cagr": cagr_v,
            "best_day": {"date": dates[best_i + 1].isoformat(), "return": rets[best_i]},
            "worst_day": {"date": dates[worst_i + 1].isoformat(), "return": rets[worst_i]},
            "positive_days": pos_days,
            "positive_days_ratio": pos_days / len(rets),
            "max_up_streak": stk["max_up_streak"],
            "max_down_streak": stk["max_down_streak"],
            "current_streak": {
                "days": stk["current_streak"],
                "direction": stk["current_direction"],
            },
        },
        "risk": {
            "ann_volatility": ann_vol,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "max_drawdown": mdd,
            "var_95": quantile(rets, 0.05),
            "skew": skewness(rets),
        },
        "indicators": {
            "rsi": last_defined(rsi),
            "atr": last_defined(atr),
            "sma": sma_latest,
            "bollinger": {
                "upper": last_defined(bb_up),
                "mid": last_defined(bb_mid),
                "lower": last_defined(bb_lo),
                "percent_b": _percent_b(closes[-1], last_defined(bb_up), last_defined(bb_lo)),
            },
            "macd": {
                "macd": last_defined(macd),
                "signal": last_defined(macd_sig),
                "histogram": last_defined(macd_hist),
            },
        },
        "signals": {
            "trend": classify_trend(closes[-1], sma_latest),
            "golden_cross": [d.isoformat() for d in golden[-cfg.top :]],
            "death_cross": [d.isoformat() for d in death[-cfg.top :]],
            "macd_bullish_cross": [
                d.isoformat() for d, k in macd_crosses[-cfg.top * 2 :] if k == "up"
            ][-cfg.top :],
            "macd_bearish_cross": [
                d.isoformat() for d, k in macd_crosses[-cfg.top * 2 :] if k == "down"
            ][-cfg.top :],
        },
        "volume": {
            "total": math.fsum(b.volume for b in bars),
            "average": avg_vol,
            "latest": bars[-1].volume,
            "max": {
                "date": max(bars, key=lambda b: b.volume).d.isoformat(),
                "volume": max(b.volume for b in bars),
            },
            "rel_latest": (bars[-1].volume / avg_vol) if avg_vol > 0 else None,
        },
        # Per-day series used by --export-series (dropped from JSON export)
        "series": {
            "dates": [d.isoformat() for d in dates],
            "close": closes,
            "daily_return": [None] + rets,
            "drawdown": dd,
            "rsi": rsi,
            "macd": macd,
            "macd_signal": macd_sig,
            "macd_hist": macd_hist,
            "bb_upper": bb_up,
            "bb_lower": bb_lo,
            "sma": {str(w): smas[w] for w in cfg.sma_windows},
            "atr": atr,
        },
    }
    res["insights"] = _insights(res)
    return res


def _percent_b(close: float, upper: Optional[float], lower: Optional[float]) -> Optional[float]:
    """Bollinger %B (0-100). Returns 50.0 for flat bands."""
    if upper is None or lower is None:
        return None
    if upper == lower:
        return 50.0
    return (close - lower) / (upper - lower) * 100.0


# ---------------------------------------------------------------------------
# Multi-symbol comparison
# ---------------------------------------------------------------------------


def returns_by_date(bars: Sequence[Bar]) -> dict[str, float]:
    """Map ISO date -> that day's fractional return (keyed by the later date)."""
    out: dict[str, float] = {}
    for i in range(1, len(bars)):
        if bars[i - 1].close > 0:
            out[bars[i].d.isoformat()] = bars[i].close / bars[i - 1].close - 1.0
    return out


def correlation_matrix(results: Sequence[dict], series_bars: dict[str, list[Bar]]) -> dict:
    """Pearson correlation of daily returns across symbols (pairwise)."""
    per_symbol = {r["symbol"]: returns_by_date(series_bars[r["symbol"]]) for r in results}
    symbols = [r["symbol"] for r in results]
    out: dict[str, Optional[float]] = {}
    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            common = sorted(set(per_symbol[a]) & set(per_symbol[b]))
            if len(common) < 3:
                out[f"{a}|{b}"] = None
                continue
            xs = [per_symbol[a][d] for d in common]
            ys = [per_symbol[b][d] for d in common]
            try:
                out[f"{a}|{b}"] = statistics.correlation(xs, ys)
            except statistics.StatisticsError:
                out[f"{a}|{b}"] = None
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def fmt_pct(v: Optional[float], nd: int = 2, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    if signed:
        return f"{v * 100:+.{nd}f}%"
    return f"{v * 100:.{nd}f}%"


def fmt_num(v: Optional[float], nd: int = 2) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.{nd}f}"


def fmt_int(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.0f}"


def _kv(rows: list[tuple[str, str]]) -> list[str]:
    width = max(len(label) for label, _ in rows)
    return [f"  {label:<{width}}  {value}" for label, value in rows]


def render_symbol(res: dict, cfg: Config) -> list[str]:
    o, p, r, k, ind, sig, vol = (
        res["overview"],
        res["prices"],
        res["returns"],
        res["risk"],
        res["indicators"],
        res["signals"],
        res["volume"],
    )
    mdd = k["max_drawdown"]
    lines: list[str] = []
    title = f"Symbol: {res['symbol']}  ({o['trading_days']} trading days, {o['start']} to {o['end']})"
    lines.append(title)
    lines.append("=" * max(len(title), 40))
    lines.append("")

    lines.append("Price action")
    lines += _kv(
        [
            ("First / last close", f"{fmt_num(o['first_close'])} -> {fmt_num(o['last_close'])}"),
            ("Min close", f"{fmt_num(p['min_close'])} on {p['min_date']}"),
            ("Max close", f"{fmt_num(p['max_close'])} on {p['max_date']}"),
            ("52w high / low", f"{fmt_num(p['high_52w'])} / {fmt_num(p['low_52w'])}"),
            ("Distance from 52w high", fmt_pct(p["pct_below_52w_high"], 2, signed=True)),
            ("Current drawdown from peak", fmt_pct(p["current_dd"], 2, signed=True)),
        ]
    )
    lines.append("")

    lines.append("Returns")
    lines += _kv(
        [
            ("Total return", fmt_pct(r["total_return"], 2, signed=True)),
            ("CAGR (annualized)", fmt_pct(r["cagr"], 2, signed=True)),
            (
                "Best day",
                f"{fmt_pct(r['best_day']['return'], 2, signed=True)} on {r['best_day']['date']}",
            ),
            (
                "Worst day",
                f"{fmt_pct(r['worst_day']['return'], 2, signed=True)} on {r['worst_day']['date']}",
            ),
            (
                "Positive days",
                f"{r['positive_days']} of {o['trading_days'] - 1} "
                f"({fmt_pct(r['positive_days_ratio'], 1)})",
            ),
            ("Max up / down streak", f"{r['max_up_streak']} / {r['max_down_streak']} days"),
            (
                "Current streak",
                f"{r['current_streak']['days']} day(s) {r['current_streak']['direction']}",
            ),
        ]
    )
    lines.append("")

    lines.append("Risk")
    lines += _kv(
        [
            ("Annualized volatility", fmt_pct(k["ann_volatility"], 2)),
            ("Sharpe ratio", fmt_num(k["sharpe"], 3)),
            ("Sortino ratio", fmt_num(k["sortino"], 3)),
            ("Calmar ratio", fmt_num(k["calmar"], 3)),
            ("Max drawdown", fmt_pct(mdd["depth"], 2, signed=True)),
            ("  peak / trough", f"{mdd['peak_date']} -> {mdd['trough_date']}"),
            ("  recovered on", mdd["recovery_date"] or "not yet"),
            ("Historical VaR 95% (1-day)", fmt_pct(k["var_95"], 2, signed=True)),
            ("Return skewness", fmt_num(k["skew"], 3)),
        ]
    )
    lines.append("")

    lines.append("Indicators (latest values)")
    sma_rows = [(f"SMA {w}", fmt_num(v)) for w, v in sorted(ind["sma"].items())]
    bb = ind["bollinger"]
    lines += _kv(
        [
            (f"RSI ({cfg.rsi_period})", fmt_num(ind["rsi"], 1)),
            (f"ATR ({cfg.atr_period})", fmt_num(ind["atr"], 2)),
        ]
        + sma_rows
        + [
            ("Bollinger upper / mid / lower",
             f"{fmt_num(bb['upper'])} / {fmt_num(bb['mid'])} / {fmt_num(bb['lower'])}"),
            ("Bollinger %B", fmt_num(bb["percent_b"], 1)),
            (
                f"MACD ({cfg.macd_fast},{cfg.macd_slow},{cfg.macd_signal})",
                f"{fmt_num(ind['macd']['macd'], 3)} (signal {fmt_num(ind['macd']['signal'], 3)}, "
                f"hist {fmt_num(ind['macd']['histogram'], 3)})",
            ),
        ]
    )
    lines.append("")

    lines.append("Signals")
    lines += _kv(
        [
            ("Trend classification", sig["trend"] or "n/a (not enough data)"),
            ("Golden cross (recent)", ", ".join(sig["golden_cross"]) or "none"),
            ("Death cross (recent)", ", ".join(sig["death_cross"]) or "none"),
            ("MACD bullish cross", ", ".join(sig["macd_bullish_cross"]) or "none"),
            ("MACD bearish cross", ", ".join(sig["macd_bearish_cross"]) or "none"),
        ]
    )
    lines.append("")

    lines.append("Volume")
    lines += _kv(
        [
            ("Total / average", f"{fmt_int(vol['total'])} / {fmt_int(vol['average'])}"),
            (
                "Max volume day",
                f"{fmt_int(vol['max']['volume'])} on {vol['max']['date']}",
            ),
            ("Latest vs average", f"{fmt_num(vol['rel_latest'], 2)}x"),
        ]
    )
    lines.append("")

    lines.append("Summary")
    for line in res["insights"]:
        lines.append(f"  * {line}")
    lines.append("")
    return lines


def render_comparison(results: Sequence[dict]) -> list[str]:
    headers = [
        "Symbol", "Days", "Total %", "CAGR %", "Vol %", "Sharpe", "MaxDD %", "RSI", "Trend"
    ]
    def sort_key(res):
        tr = res["returns"]["total_return"]
        return -math.inf if tr is None else tr

    ordered = sorted(results, key=sort_key, reverse=True)
    rows = []
    for res in ordered:
        r, k, ind, sig = res["returns"], res["risk"], res["indicators"], res["signals"]
        rows.append(
            [
                res["symbol"],
                str(res["overview"]["trading_days"]),
                fmt_pct(r["total_return"], 1, signed=True),
                fmt_pct(r["cagr"], 1, signed=True),
                fmt_pct(k["ann_volatility"], 1),
                fmt_num(k["sharpe"], 2),
                fmt_pct(k["max_drawdown"]["depth"], 1, signed=True),
                fmt_num(ind["rsi"], 1),
                sig["trend"] or "n/a",
            ]
        )
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))
    ]
    lines = ["Comparison (sorted by total return)"]
    lines.append(
        "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    )
    lines.append("  " + "-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        lines.append("  " + "  ".join(row[i].ljust(widths[i]) for i in range(len(row))))
    lines.append("")
    return lines


def render_correlation(corr: dict) -> list[str]:
    if not corr:
        return []
    lines = ["Daily-return correlation (Pearson)"]
    for pair, value in corr.items():
        lines.append(f"  {pair}: {fmt_num(value, 3)}")
    lines.append("")
    return lines


def render_report(results: Sequence[dict], cfg: Config, corr: Optional[dict] = None) -> str:
    lines: list[str] = []
    for res in results:
        lines += render_symbol(res, cfg)
    if len(results) > 1:
        lines += render_comparison(results)
        lines += render_correlation(corr or {})
    lines.append(
        f"Settings: SMA {'/'.join(str(w) for w in cfg.sma_windows)} | RSI {cfg.rsi_period} | "
        f"MACD {cfg.macd_fast}/{cfg.macd_slow}/{cfg.macd_signal} | "
        f"BBands {cfg.bollinger_window},{cfg.bollinger_std}x | risk-free {cfg.risk_free:.2%} | "
        f"annualization {TRADING_DAYS_PER_YEAR}d | analyzer v{VERSION}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def _clean(obj, nd: int = 6):
    """Recursively round floats and drop the bulky per-day series."""
    if isinstance(obj, dict):
        return {k: _clean(v, nd) for k, v in obj.items() if k != "series"}
    if isinstance(obj, (list, tuple)):
        return [_clean(v, nd) for v in obj]
    if isinstance(obj, float):
        return round(obj, nd)
    return obj


def export_json(results, cfg: Config, corr: Optional[dict], path: str) -> None:
    payload = {
        "analyzer": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            **{k: list(v) if isinstance(v, tuple) else v for k, v in vars(cfg).items()},
            "trading_days_per_year": TRADING_DAYS_PER_YEAR,
        },
        "symbols": [_clean(r) for r in results],
    }
    if corr is not None:
        payload["comparison"] = {"correlation": _clean(corr)}
    text = json.dumps(payload, indent=2, allow_nan=False)
    if path == "-":
        print(text)
    else:
        Path(path).write_text(text + "\n", encoding="utf-8")


def _flatten(res: dict) -> dict:
    o, p, r, k, ind, sig, vol = (
        res["overview"],
        res["prices"],
        res["returns"],
        res["risk"],
        res["indicators"],
        res["signals"],
        res["volume"],
    )
    mdd = k["max_drawdown"]
    flat = {
        "symbol": res["symbol"],
        "start": o["start"],
        "end": o["end"],
        "trading_days": o["trading_days"],
        "first_close": o["first_close"],
        "last_close": o["last_close"],
        "min_close": p["min_close"],
        "max_close": p["max_close"],
        "high_52w": p["high_52w"],
        "low_52w": p["low_52w"],
        "pct_below_52w_high": p["pct_below_52w_high"],
        "current_dd": p["current_dd"],
        "total_return": r["total_return"],
        "cagr": r["cagr"],
        "best_day_return": r["best_day"]["return"],
        "best_day_date": r["best_day"]["date"],
        "worst_day_return": r["worst_day"]["return"],
        "worst_day_date": r["worst_day"]["date"],
        "positive_days_ratio": r["positive_days_ratio"],
        "max_up_streak": r["max_up_streak"],
        "max_down_streak": r["max_down_streak"],
        "ann_volatility": k["ann_volatility"],
        "sharpe": k["sharpe"],
        "sortino": k["sortino"],
        "calmar": k["calmar"],
        "max_drawdown": mdd["depth"],
        "dd_peak_date": mdd["peak_date"],
        "dd_trough_date": mdd["trough_date"],
        "dd_recovery_date": mdd["recovery_date"],
        "var_95": k["var_95"],
        "skew": k["skew"],
        "rsi": ind["rsi"],
        "atr": ind["atr"],
    }
    for w, v in sorted(ind["sma"].items()):
        flat[f"sma_{w}"] = v
    flat.update(
        {
            "bb_upper": ind["bollinger"]["upper"],
            "bb_lower": ind["bollinger"]["lower"],
            "percent_b": ind["bollinger"]["percent_b"],
            "macd": ind["macd"]["macd"],
            "macd_signal": ind["macd"]["signal"],
            "macd_hist": ind["macd"]["histogram"],
            "avg_volume": vol["average"],
            "total_volume": vol["total"],
            "rel_volume_latest": vol["rel_latest"],
            "trend": sig["trend"],
        }
    )
    return flat


def export_metrics_csv(results, path: str) -> None:
    flat_rows = [_flatten(r) for r in results]
    fieldnames: list[str] = []
    for row in flat_rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    handle = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in flat_rows:
            writer.writerow(
                {
                    key: ("" if row.get(key) is None else row.get(key))
                    for key in fieldnames
                }
            )
    finally:
        if handle is not sys.stdout:
            handle.close()


def export_series_csv(results, path: str, cfg: Config) -> None:
    handle = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")
    try:
        windows = sorted(cfg.sma_windows)
        header = (
            ["Date", "Symbol", "Close"]
            + [f"SMA{w}" for w in windows]
            + [
                "RSI",
                "MACD",
                "MACD_Signal",
                "MACD_Hist",
                "BB_Upper",
                "BB_Lower",
                "ATR",
                "Daily_Return",
                "Drawdown",
            ]
        )
        writer = csv.writer(handle)
        writer.writerow(header)

        def cell(v) -> str:
            if v is None:
                return ""
            if isinstance(v, float):
                return f"{v:.6f}"
            return str(v)

        for res in results:
            s = res["series"]
            n = len(s["dates"])
            for i in range(n):
                row = [s["dates"][i], res["symbol"], cell(s["close"][i])]
                row += [cell(s["sma"][str(w)][i]) for w in windows]
                row += [
                    cell(s["rsi"][i]),
                    cell(s["macd"][i]),
                    cell(s["macd_signal"][i]),
                    cell(s["macd_hist"][i]),
                    cell(s["bb_upper"][i]),
                    cell(s["bb_lower"][i]),
                    cell(s["atr"][i]),
                    cell(s["daily_return"][i]),
                    cell(s["drawdown"][i]),
                ]
                writer.writerow(row)
    finally:
        if handle is not sys.stdout:
            handle.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _iso_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {text!r}, expected YYYY-MM-DD")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock_analyzer.py",
        description="Analyze stock market OHLCV data from CSV files or Stooq.",
        epilog=(
            "examples:\n"
            "  %(prog)s AAPL.csv\n"
            "  %(prog)s AAPL.csv MSFT.csv --csv summary.csv --json report.json\n"
            "  %(prog)s --fetch aapl --start 2024-01-01 --export-series series.csv\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="*", metavar="CSV", help="OHLCV CSV file(s) to analyze")
    parser.add_argument(
        "-f",
        "--fetch",
        action="append",
        default=[],
        metavar="SYMBOL",
        help="fetch live daily data from Stooq (repeatable, e.g. -f aapl -f msft)",
    )
    parser.add_argument("--start", type=_iso_date, help="only use data on/after this date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_iso_date, help="only use data on/before this date (YYYY-MM-DD)")
    parser.add_argument(
        "--raw-close",
        action="store_true",
        help="use the Close column even when an Adj Close column exists",
    )
    parser.add_argument(
        "--sma",
        nargs="+",
        type=int,
        default=[20, 50, 200],
        metavar="N",
        help="SMA windows (default: 20 50 200)",
    )
    parser.add_argument("--rsi-period", type=int, default=14, help="RSI period (default: 14)")
    parser.add_argument(
        "--macd",
        nargs=3,
        type=int,
        default=[12, 26, 9],
        metavar=("FAST", "SLOW", "SIGNAL"),
        help="MACD parameters (default: 12 26 9)",
    )
    parser.add_argument(
        "--bollinger",
        nargs=2,
        default=[20, 2.0],
        metavar=("WINDOW", "STD"),
        help="Bollinger window and std multiple (default: 20 2)",
    )
    parser.add_argument("--atr-period", type=int, default=14, help="ATR period (default: 14)")
    parser.add_argument(
        "--risk-free",
        type=float,
        default=0.0,
        help="annual risk-free rate as a fraction, e.g. 0.04 (default: 0)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="number of recent signal events to show (default: 3)",
    )
    parser.add_argument("--json", metavar="PATH", help="write full results as JSON ('-' for stdout)")
    parser.add_argument("--csv", metavar="PATH", help="write per-symbol metrics as CSV ('-' for stdout)")
    parser.add_argument(
        "--export-series",
        metavar="PATH",
        help="write per-day indicator series as CSV ('-' for stdout)",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="suppress the stdout report (useful with --json)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _validate(parser: argparse.ArgumentParser, args) -> Config:
    if not args.files and not args.fetch:
        parser.error("provide at least one CSV file or --fetch SYMBOL")
    if any(w < 1 for w in args.sma):
        parser.error("--sma windows must be >= 1")
    if len(set(args.sma)) != len(args.sma):
        parser.error("--sma windows must be unique")
    if len(args.sma) < 2:
        parser.error("provide at least two SMA windows for cross signals")
    fast, slow, signal = args.macd
    if not (1 <= fast < slow) or signal < 1:
        parser.error("--macd requires 1 <= FAST < SLOW and SIGNAL >= 1")
    try:
        bw = int(float(args.bollinger[0]))
        bstd = float(args.bollinger[1])
    except ValueError:
        parser.error("--bollinger expects numeric WINDOW and STD")
    if bw < 2 or bstd <= 0:
        parser.error("--bollinger requires WINDOW >= 2 and STD > 0")
    if args.rsi_period < 1 or args.atr_period < 1:
        parser.error("--rsi-period and --atr-period must be >= 1")
    if args.top < 1:
        parser.error("--top must be >= 1")
    if args.risk_free < 0:
        parser.error("--risk-free must be >= 0")
    return Config(
        sma_windows=tuple(sorted(args.sma)),
        rsi_period=args.rsi_period,
        macd_fast=fast,
        macd_slow=slow,
        macd_signal=signal,
        bollinger_window=int(bw),
        bollinger_std=float(bstd),
        atr_period=args.atr_period,
        risk_free=args.risk_free,
        top=args.top,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = _validate(parser, args)

    results: list[dict] = []
    all_bars: dict[str, list[Bar]] = {}
    errors: list[str] = []
    warnings: list[str] = []

    for path in args.files:
        try:
            symbol, bars, warns, _price_col = load_csv(path, prefer_adj_close=not args.raw_close)
        except (DataError, OSError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        warnings.extend(warns)
        bars = filter_range(bars, args.start, args.end)
        try:
            res = analyze(symbol, bars, cfg)
        except AnalysisError as exc:
            errors.append(f"{path}: {exc}")
            continue
        results.append(res)
        all_bars[res["symbol"]] = bars

    for sym in args.fetch:
        try:
            _used, bars = fetch_stooq(sym, args.start, args.end)
        except FetchError as exc:
            errors.append(f"fetch {sym}: {exc}")
            continue
        label = sym.strip().upper()
        try:
            res = analyze(label, bars, cfg)
        except AnalysisError as exc:
            errors.append(f"fetch {sym}: {exc}")
            continue
        results.append(res)
        all_bars[label] = bars

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)

    if not results:
        print("error: nothing to analyze", file=sys.stderr)
        return 2

    corr = correlation_matrix(results, all_bars) if len(results) > 1 else None

    if not args.no_report:
        print(render_report(results, cfg, corr))

    if args.json:
        export_json(results, cfg, corr, args.json)
    if args.csv:
        export_metrics_csv(results, args.csv)
    if args.export_series:
        export_series_csv(results, args.export_series, cfg)

    if errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
