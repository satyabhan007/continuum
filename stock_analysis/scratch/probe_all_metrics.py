#!/usr/bin/env python3
"""One-off probe: test EVERY metric in out/report.json, not just 'major' ones.

Independent reimplementation from the documented definitions (README +
stock_analyzer.py docstrings). Prints PASS/FAIL per metric; exit 1 on any
FAIL. Run from stock_analysis/: python3 scratch/probe_all_metrics.py
"""
import csv
import json
import math
import os
import statistics
import subprocess
import sys
from datetime import date

TRADING_DAYS = 252
RISK_FREE = 0.04
SYMBOLS = ["AAPL", "MSFT", "NVDA", "GME", "KO"]
TOP = 3          # analyzer default --top 3
TOL = 1e-6      # JSON floats rounded to 6 decimals
SMA_WINDOWS = (20, 50, 200)
RSI_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_WINDOW, BB_STD = 20, 2.0

fails = []
npass = 0


def check(sym, name, mine, theirs):
    global npass
    if mine is None and theirs is None:
        npass += 1
        return
    if mine is None or theirs is None:
        fails.append(f"{sym}.{name}: mine={mine} theirs={theirs}")
        return
    if isinstance(mine, str) or isinstance(theirs, str):
        ok = mine == theirs
    elif isinstance(mine, list) or isinstance(theirs, list):
        ok = mine == theirs
    else:
        ok = abs(mine - theirs) <= TOL
    if ok:
        npass += 1
    else:
        fails.append(f"{sym}.{name}: mine={mine!r} theirs={theirs!r}")


def sma(values, window):
    out = [None] * len(values)
    if len(values) < window:
        return out
    s = math.fsum(values[:window])
    out[window - 1] = s / window
    for i in range(window, len(values)):
        s += values[i] - values[i - window]
        out[i] = s / window
    return out


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = math.fsum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(closes, period=14):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag = math.fsum(gains[:period]) / period
    al = math.fsum(losses[:period]) / period
    if al == 0:
        out[period] = 100.0
    else:
        rs = ag / al
        out[period] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(period + 1, len(closes)):
        g = gains[i - 1]
        l = losses[i - 1]
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
        if al == 0:
            out[i] = 100.0
        else:
            rs = ag / al
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def bollinger(closes, window=BB_WINDOW, num_std=BB_STD):
    n = len(closes)
    mid = sma(closes, window)
    upper, lower = [None] * n, [None] * n
    for i in range(window - 1, n):
        seg = closes[i - window + 1: i + 1]
        m = mid[i]
        sd = statistics.pstdev(seg)
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return mid, upper, lower


def atr(highs, lows, closes, period=14):
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    a = math.fsum(trs[1:period + 1]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def macd_series(closes):
    """MACD line, signal (EMA of line), histogram."""
    line = [f - s if f is not None and s is not None else None
            for f, s in zip(ema(closes, MACD_FAST), ema(closes, MACD_SLOW))]
    first = MACD_SLOW - 1
    k = 2.0 / (MACD_SIGNAL + 1.0)
    if len(closes) < first + MACD_SIGNAL:
        return line, [None] * len(line), [None] * len(line)
    sig = [None] * len(line)
    prev = math.fsum(line[first:first + MACD_SIGNAL]) / MACD_SIGNAL
    sig[first + MACD_SIGNAL - 1] = prev
    for i in range(first + MACD_SIGNAL, len(line)):
        prev = line[i] * k + prev * (1.0 - k)
        sig[i] = prev
    hist = [None] * len(line)
    for i in range(len(line)):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def cross_events(fast, slow, dates):
    """Sign-change crossings of fast over slow (analyzer semantics: None
    points are skipped without updating prev_sign)."""
    events = []
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


def classify_trend(close, sma_vals):
    defined = [v for v in sma_vals if v is not None]
    if not defined:
        return None
    if all(close > v for v in defined):
        return "bullish"
    if all(close < v for v in defined):
        return "bearish"
    if all(close == v for v in defined):
        return "neutral"
    return "mixed"


def load(sym):
    with open(f"{sym}.csv") as fh:
        rows = list(csv.DictReader(fh))
    g = lambda key: [float(r[key]) for r in rows]
    dates = [date.fromisoformat(r["Date"]) for r in rows]
    return dates, g("Close"), g("High"), g("Low"), g("Open"), g("Volume")


def main():
    # Never-crash rule: this probe is documented to run from
    # stock_analysis/, but a stray cwd must not crash it with a raw
    # traceback. Both scripts live in <analyzer_dir>/scratch/, so anchor
    # all relative paths (AAPL.csv, out/report.json) to the analyzer dir.
    analyzer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(analyzer_dir)

    if not os.path.isfile("out/report.json"):
        print("[self-heal] out/report.json missing - generating with "
              "stock_analyzer.py from the sample CSVs")
        os.makedirs("out", exist_ok=True)
        cmd = [sys.executable, "stock_analyzer.py",
               *[f"{s}.csv" for s in SYMBOLS],
               "--risk-free", str(RISK_FREE),
               "--json", "out/report.json", "--csv", "out/metrics.csv",
               "--export-series", "out/series.csv"]
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True,
                                  text=True)
        except subprocess.CalledProcessError as exc:
            print(f"FAILED: self-heal regeneration failed "
                  f"(analyzer exit {exc.returncode})")
            if exc.stderr:
                print(exc.stderr.strip(), file=sys.stderr)
            sys.exit(1)
        if proc.stdout:
            print(proc.stdout)
    try:
        with open("out/report.json") as fh:
            rep = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAILED: cannot read out/report.json ({exc}). Delete it "
              "to trigger self-heal, or regenerate with stock_analyzer.py")
        sys.exit(1)

    # Settings echo must match the constants this probe assumes;
    # a report generated with different flags must fail loudly here
    # (parity with the validator's guard) - not downstream in a
    # confusing metric mismatch, and never a silent 90/90 PASS on a
    # wrong-settings report.
    settings = rep.get("settings", {})
    expected_settings = {
        "sma_windows": list(SMA_WINDOWS),
        "rsi_period": RSI_PERIOD,
        "macd_fast": MACD_FAST,
        "macd_slow": MACD_SLOW,
        "macd_signal": MACD_SIGNAL,
        "bollinger_window": BB_WINDOW,
        "bollinger_std": BB_STD,
        "atr_period": ATR_PERIOD,
        "risk_free": RISK_FREE,
        "top": TOP,
        "trading_days_per_year": TRADING_DAYS,
    }
    if settings and settings != expected_settings:
        differing = {k: (settings.get(k), expected_settings[k])
                     for k in expected_settings
                     if settings.get(k) != expected_settings[k]}
        print(f"FAILED: report.json settings differ from probe constants: "
              f"{differing}")
        sys.exit(1)

    report = {s["symbol"]: s for s in rep["symbols"]}
    per_date_rets = {}
    for sym in SYMBOLS:
        dates, closes, highs, lows, opens, vols = load(sym)
        n = len(closes)
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, n)]
        per_date_rets[sym] = {dates[i + 1].isoformat(): r for i, r in enumerate(rets)}
        r = report[sym]

        # ---- overview (7 unchecked metrics) ----
        check(sym, "ov_start", dates[0].isoformat(), r["overview"]["start"])
        check(sym, "ov_end", dates[-1].isoformat(), r["overview"]["end"])
        check(sym, "ov_days", n, r["overview"]["trading_days"])
        check(sym, "ov_first", closes[0], r["overview"]["first_close"])
        check(sym, "ov_last", closes[-1], r["overview"]["last_close"])
        check(sym, "pr_min", min(closes), r["prices"]["min_close"])
        check(sym, "pr_min_date", dates[closes.index(min(closes))].isoformat(), r["prices"]["min_date"])
        check(sym, "pr_max", max(closes), r["prices"]["max_close"])
        check(sym, "pr_max_date", dates[closes.index(max(closes))].isoformat(), r["prices"]["max_date"])

        # ---- bollinger extras ----
        mid, upper, lower = bollinger(closes)
        check(sym, "bb_mid", mid[-1], r["indicators"]["bollinger"]["mid"])
        pct_b = 50.0 if upper[-1] == lower[-1] else \
            (closes[-1] - lower[-1]) / (upper[-1] - lower[-1]) * 100.0
        check(sym, "bb_pct_b", pct_b, r["indicators"]["bollinger"]["percent_b"])

        # ---- volume.latest ----
        check(sym, "vol_latest", vols[-1], r["volume"]["latest"])

        # ---- cross event lists (top-3 recent) ----
        smas = {w: sma(closes, w) for w in SMA_WINDOWS}
        fast_s = smas[min(SMA_WINDOWS)]
        slow_s = smas[max(SMA_WINDOWS)]
        ev = cross_events(fast_s, slow_s, dates)
        golden = [d for d, k in ev if k == "up"]
        death = [d for d, k in ev if k == "down"]
        check(sym, "golden_cross",
              [d.isoformat() for d in golden[-TOP:]],
              r["signals"]["golden_cross"])
        check(sym, "death_cross",
              [d.isoformat() for d in death[-TOP:]],
              r["signals"]["death_cross"])

        # ---- MACD crosses (separate implementation from sma-cross) ----
        line, sig, hist = macd_series(closes)
        mev = cross_events(line, sig, dates)
        m_golden = [d for d, k in mev if k == "up"]
        m_death = [d for d, k in mev if k == "down"]
        # analyzer: [d for d, k in macd_crosses[-top*2:] if k=='up'][-top:]
        check(sym, "macd_bullish",
              [d.isoformat() for d, k in mev[-TOP * 2:] if k == "up"][-TOP:],
              r["signals"]["macd_bullish_cross"])
        check(sym, "macd_bearish",
              [d.isoformat() for d, k in mev[-TOP * 2:] if k == "down"][-TOP:],
              r["signals"]["macd_bearish_cross"])

    # ---- correlation matrix (top-level comparison) ----
    comp = rep["comparison"]["correlation"]
    for i, a in enumerate(SYMBOLS):
        for b in SYMBOLS[i + 1:]:
            common = sorted(set(per_date_rets[a]) & set(per_date_rets[b]))
            xs = [per_date_rets[a][d] for d in common]
            ys = [per_date_rets[b][d] for d in common]
            try:
                corr = statistics.correlation(xs, ys)
            except statistics.StatisticsError:
                corr = None
            check(a, f"corr|{b}", corr, comp[f"{a}|{b}"])

    print()
    print(f"probe: {npass} passed, {len(fails)} failed")
    if fails:
        print("FAILED metrics:")
        for f in fails:
            print(" ", f)
        sys.exit(1)
    print("ALL METRICS PASS: every field in report.json verified for all 5 symbols")


if __name__ == "__main__":
    main()
