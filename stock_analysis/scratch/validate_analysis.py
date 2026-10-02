#!/usr/bin/env python3
"""Second-opinion validator: recompute all major metrics from raw CSVs.

Independent of the analyzer module: no imports from stock_analyzer. The
indicator/risk formulas mirror the documented definitions (README +
docstrings) so that agreement is evidence the analyzer implements them
correctly.

Usage: python3 scratch/validate_analysis.py   (from stock_analysis/)
Exits non-zero on any mismatch.
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
# JSON export rounds floats to 6 decimals (_clean), so allow 1e-6 slack
TOL = 1e-6


def load(sym):
    with open(f"{sym}.csv") as fh:
        rows = list(csv.DictReader(fh))
    g = lambda key: [float(r[key]) for r in rows]
    dates = [date.fromisoformat(r["Date"]) for r in rows]
    return dates, g("Close"), g("High"), g("Low"), g("Open"), g("Volume")


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
    n = len(closes)
    out = [None] * n
    avg_gain = math.fsum(max(closes[i] - closes[i - 1], 0.0) for i in range(1, period + 1)) / period
    avg_loss = math.fsum(max(closes[i - 1] - closes[i], 0.0) for i in range(1, period + 1)) / period
    out[period] = 100.0 * avg_gain / (avg_gain + avg_loss) if avg_gain + avg_loss else 50.0
    for i in range(period + 1, n):
        ch = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(ch, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 * avg_gain / (avg_gain + avg_loss) if avg_gain + avg_loss else 50.0
    return out


def bollinger(closes, window=20, num_std=2.0):
    mid = sma(closes, window)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(window - 1, len(closes)):
        m = mid[i]
        sd = statistics.pstdev(closes[i - window + 1 : i + 1])
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return mid, upper, lower


def macd(closes, fast=12, slow=26, signal_period=9):
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [ef[i] - es[i] if ef[i] is not None and es[i] is not None else None
            for i in range(len(closes))]
    sig = [None] * len(closes)
    first = slow - 1
    if len(closes) >= first + signal_period:
        k = 2.0 / (signal_period + 1.0)
        prev = math.fsum(line[first : first + signal_period]) / signal_period
        sig[first + signal_period - 1] = prev
        for i in range(first + signal_period, len(closes)):
            prev = line[i] * k + prev * (1.0 - k)
            sig[i] = prev
    hist = [line[i] - sig[i] if line[i] is not None and sig[i] is not None else None
            for i in range(len(closes))]
    return line, sig, hist


def atr(highs, lows, closes, period=14):
    n = len(closes)
    out = [None] * n
    trs = []
    for i in range(n):
        if i == 0:
            tr = highs[i] - lows[i]
        else:
            pc = closes[i - 1]
            tr = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
        trs.append(tr)
    a = math.fsum(trs[1 : period + 1]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def drawdown(closes):
    out = []
    peak = -math.inf
    for c in closes:
        peak = max(peak, c)
        out.append(c / peak - 1.0)
    return out


def max_dd_info(closes, dates):
    dd = drawdown(closes)
    trough_i = min(range(len(dd)), key=lambda i: dd[i])
    peak_i = max(range(trough_i + 1), key=lambda i: closes[i])
    recovery = None
    for j in range(trough_i + 1, len(closes)):
        if closes[j] >= closes[peak_i]:
            recovery = dates[j].isoformat()
            break
    return {
        "depth": dd[trough_i],
        "peak_date": dates[peak_i].isoformat(),
        "trough_date": dates[trough_i].isoformat(),
        "recovery": recovery,
    }


def quantile(values, q):
    s = sorted(values)
    if q <= 0:
        return s[0]
    if q >= 1:
        return s[-1]
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def skewness(vals):
    n = len(vals)
    m = statistics.fmean(vals)
    m2 = statistics.fmean((x - m) ** 2 for x in vals)
    m3 = statistics.fmean((x - m) ** 3 for x in vals)
    return (m3 / m2 ** 1.5) * math.sqrt(n * (n - 1)) / (n - 2)


def classify_trend(close, sma_values):
    defined = [v for v in sma_values if v is not None]
    if not defined:
        return None
    if all(close > v for v in defined):
        return "bullish"
    if all(close < v for v in defined):
        return "bearish"
    if all(close == v for v in defined):
        return "neutral"
    return "mixed"


def streaks(returns):
    best_up = best_down = cur_len = 0
    cur_sign = 0
    for r in returns:
        s = 1 if r > 0 else (-1 if r < 0 else 0)
        if s == 0 or s != cur_sign:
            cur_sign, cur_len = s, (1 if s else 0)
        else:
            cur_len += 1
        if cur_sign == 1:
            best_up = max(best_up, cur_len)
        elif cur_sign == -1:
            best_down = max(best_down, cur_len)
    return best_up, best_down, (cur_len if cur_sign else 0), cur_sign


def main():
    # Never-crash rule: this validator is documented to run from
    # stock_analysis/, but a stray cwd must not crash it with a raw
    # traceback. Both scripts live in <analyzer_dir>/scratch/, so anchor
    # all relative paths (AAPL.csv, out/report.json) to the analyzer dir.
    analyzer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(analyzer_dir)

    # Self-heal for fresh clones: out/ is gitignored (regenerable), so if
    # the report is missing, produce it with the committed analyzer from
    # the committed sample CSVs - deterministic data, same tool.
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
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAILED: cannot read out/report.json ({exc}). Delete it "
              "to trigger self-heal, or regenerate with stock_analyzer.py")
        sys.exit(1)
    report = {s["symbol"]: s for s in raw["symbols"]}

    # settings echo must match the constants this validator assumes;
    # a report generated with different flags must fail loudly here,
    # not downstream in a confusing metric mismatch.
    settings = raw.get("settings", {})
    expected_settings = {
        "sma_windows": [20, 50, 200],
        "rsi_period": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "bollinger_window": 20,
        "bollinger_std": 2.0,
        "atr_period": 14,
        "risk_free": RISK_FREE,
        "top": 3,
        "trading_days_per_year": TRADING_DAYS,
    }
    if settings and settings != expected_settings:
        differing = {k: (settings.get(k), expected_settings[k])
                     for k in expected_settings
                     if settings.get(k) != expected_settings[k]}
        print(f"FAILED: report.json settings differ from validator "
              f"constants: {differing}")
        sys.exit(1)

    failures = []
    per_date_rets = {}

    def check(sym, name, mine, theirs):
        if mine is None and theirs is None:
            print(f"  ok {sym}.{name} (both None)")
            return
        if mine is None or theirs is None:
            failures.append(f"{sym}.{name}: mine={mine} theirs={theirs}")
            return
        if isinstance(mine, (str, list)) or isinstance(theirs, (str, list)):
            ok = mine == theirs
        else:
            ok = abs(mine - theirs) <= TOL
        if ok:
            print(f"  ok {sym}.{name}")
        else:
            failures.append(f"{sym}.{name}: mine={mine!r} theirs={theirs!r}")

    for sym in SYMBOLS:
        dates, closes, highs, lows, _opens, vols = load(sym)
        n = len(closes)
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, n)]
        per_date_rets[sym] = {dates[i + 1].isoformat(): r for i, r in enumerate(rets)}
        r = report[sym]

        # overview (dates, counts, endpoints)
        check(sym, "ov_start", dates[0].isoformat(), r["overview"]["start"])
        check(sym, "ov_end", dates[-1].isoformat(), r["overview"]["end"])
        check(sym, "ov_days", n, r["overview"]["trading_days"])
        check(sym, "ov_first", closes[0], r["overview"]["first_close"])
        check(sym, "ov_last", closes[-1], r["overview"]["last_close"])
        check(sym, "pr_min", min(closes), r["prices"]["min_close"])
        check(sym, "pr_min_date",
              dates[closes.index(min(closes))].isoformat(),
              r["prices"]["min_date"])
        check(sym, "pr_max", max(closes), r["prices"]["max_close"])
        check(sym, "pr_max_date",
              dates[closes.index(max(closes))].isoformat(),
              r["prices"]["max_date"])

        # returns
        check(sym, "total_return", closes[-1] / closes[0] - 1.0, r["returns"]["total_return"])
        check(sym, "cagr", (closes[-1] / closes[0]) ** (TRADING_DAYS / (n - 1)) - 1.0, r["returns"]["cagr"])
        best_i = max(range(len(rets)), key=lambda i: rets[i])
        worst_i = min(range(len(rets)), key=lambda i: rets[i])
        check(sym, "best_day", rets[best_i], r["returns"]["best_day"]["return"])
        check(sym, "best_day_date", dates[best_i + 1].isoformat(), r["returns"]["best_day"]["date"])
        check(sym, "worst_day", rets[worst_i], r["returns"]["worst_day"]["return"])
        check(sym, "worst_day_date", dates[worst_i + 1].isoformat(), r["returns"]["worst_day"]["date"])
        check(sym, "pos_ratio", sum(1 for x in rets if x > 0) / len(rets), r["returns"]["positive_days_ratio"])
        b_up, b_down, cur, cur_dir = streaks(rets)
        check(sym, "max_up_streak", b_up, r["returns"]["max_up_streak"])
        check(sym, "max_down_streak", b_down, r["returns"]["max_down_streak"])
        # The analyzer exports current_streak as {days, direction} (see
        # stock_analyzer.py ~line 805), so check both fields separately.
        cs = r["returns"]["current_streak"]
        check(sym, "current_streak.days", cur, cs["days"])
        check(sym, "current_streak.direction",
              {1: "up", -1: "down", 0: "flat"}[cur_dir], cs["direction"])

        # risk
        check(sym, "vol", statistics.stdev(rets) * math.sqrt(TRADING_DAYS), r["risk"]["ann_volatility"])
        excess = [x - RISK_FREE / TRADING_DAYS for x in rets]
        check(sym, "sharpe", statistics.fmean(excess) / statistics.stdev(excess) * math.sqrt(TRADING_DAYS), r["risk"]["sharpe"])
        downside = math.sqrt(statistics.fmean(min(x, 0.0) ** 2 for x in excess))
        check(sym, "sortino", statistics.fmean(excess) / downside * math.sqrt(TRADING_DAYS), r["risk"]["sortino"])
        check(sym, "var_95", quantile(rets, 0.05), r["risk"]["var_95"])
        check(sym, "skew", skewness(rets), r["risk"]["skew"])
        info = max_dd_info(closes, dates)
        check(sym, "maxdd_depth", info["depth"], r["risk"]["max_drawdown"]["depth"])
        check(sym, "maxdd_peak", info["peak_date"], r["risk"]["max_drawdown"]["peak_date"])
        check(sym, "maxdd_trough", info["trough_date"], r["risk"]["max_drawdown"]["trough_date"])
        check(sym, "maxdd_recovery", info["recovery"], r["risk"]["max_drawdown"]["recovery_date"])
        cagr_v = (closes[-1] / closes[0]) ** (TRADING_DAYS / (n - 1)) - 1.0
        check(sym, "calmar", cagr_v / abs(info["depth"]), r["risk"]["calmar"])

        # 52w window and current drawdown
        w52 = closes[-TRADING_DAYS:]
        check(sym, "high_52w", max(w52), r["prices"]["high_52w"])
        check(sym, "low_52w", min(w52), r["prices"]["low_52w"])
        check(sym, "pct_below_52w", closes[-1] / max(w52) - 1.0, r["prices"]["pct_below_52w_high"])
        check(sym, "current_dd", drawdown(closes)[-1], r["prices"]["current_dd"])

        # indicators
        sma20, sma50, sma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
        check(sym, "sma20", sma20[-1], r["indicators"]["sma"]["20"])
        check(sym, "sma50", sma50[-1], r["indicators"]["sma"]["50"])
        check(sym, "sma200", sma200[-1], r["indicators"]["sma"]["200"])
        check(sym, "rsi", rsi(closes)[-1], r["indicators"]["rsi"])
        _mid, upper, lower = bollinger(closes)
        check(sym, "bb_upper", upper[-1], r["indicators"]["bollinger"]["upper"])
        check(sym, "bb_lower", lower[-1], r["indicators"]["bollinger"]["lower"])
        check(sym, "bb_mid", _mid[-1], r["indicators"]["bollinger"]["mid"])
        pct_b = 50.0 if upper[-1] == lower[-1] else \
            (closes[-1] - lower[-1]) / (upper[-1] - lower[-1]) * 100.0
        check(sym, "bb_pct_b", pct_b, r["indicators"]["bollinger"]["percent_b"])
        line, sig, hist = macd(closes)
        check(sym, "macd", line[-1], r["indicators"]["macd"]["macd"])
        check(sym, "macd_signal", sig[-1], r["indicators"]["macd"]["signal"])
        check(sym, "macd_hist", hist[-1], r["indicators"]["macd"]["histogram"])
        check(sym, "atr", atr(highs, lows, closes)[-1], r["indicators"]["atr"])

        # signals
        check(sym, "trend", classify_trend(closes[-1], (sma20[-1], sma50[-1], sma200[-1])), r["signals"]["trend"])

        # cross events: sign-change crossings of fast over slow
        # (analyzer semantics: None points skipped, prev_sign preserved)
        def crosses(fast_s, slow_s):
            evs, prev_sign = [], 0
            for i in range(n):
                f, s = fast_s[i], slow_s[i]
                if f is None or s is None:
                    continue
                sgn = 1 if f > s else (-1 if f < s else 0)
                if sgn != 0:
                    if prev_sign != 0 and sgn != prev_sign:
                        evs.append((dates[i], "up" if sgn > 0 else "down"))
                    prev_sign = sgn
            return evs

        sma_cross = crosses(sma20, sma200)
        golden = [d for d, k in sma_cross if k == "up"]
        death = [d for d, k in sma_cross if k == "down"]
        check(sym, "golden_cross",
              [d.isoformat() for d in golden[-3:]],
              r["signals"]["golden_cross"])
        check(sym, "death_cross",
              [d.isoformat() for d in death[-3:]],
              r["signals"]["death_cross"])
        macd_cross = crosses(line, sig)
        check(sym, "macd_bullish",
              [d.isoformat() for d, k in macd_cross[-6:] if k == "up"][-3:],
              r["signals"]["macd_bullish_cross"])
        check(sym, "macd_bearish",
              [d.isoformat() for d, k in macd_cross[-6:] if k == "down"][-3:],
              r["signals"]["macd_bearish_cross"])

        # volume
        check(sym, "total_vol", math.fsum(vols), r["volume"]["total"])
        check(sym, "avg_vol", statistics.fmean(vols), r["volume"]["average"])
        check(sym, "latest_vol", vols[-1], r["volume"]["latest"])
        check(sym, "max_vol", max(vols), r["volume"]["max"]["volume"])
        check(sym, "rel_latest", vols[-1] / statistics.fmean(vols), r["volume"]["rel_latest"])

    # correlation matrix (top-level comparison section)
    comp = report.get("comparison", {}).get("correlation", {})
    if comp:
        for i, a in enumerate(SYMBOLS):
            for b in SYMBOLS[i + 1:]:
                common = sorted(set(per_date_rets[a]) & set(per_date_rets[b]))
                xs = [per_date_rets[a][d] for d in common]
                ys = [per_date_rets[b][d] for d in common]
                try:
                    corr = statistics.correlation(xs, ys)
                except statistics.StatisticsError:
                    corr = None
                check(a, f"corr|{b}", corr, comp.get(f"{a}|{b}"))

    print()
    if failures:
        print(f"FAILED ({len(failures)} mismatches):")
        for f in failures:
            print(" ", f)
        sys.exit(1)
    print("ALL CHECKS PASSED: every metric for all 5 symbols - returns, risk,",
          "prices, overview, indicators, signals, cross events, volume, and",
          "the pairwise correlation matrix - matches report.json")


if __name__ == "__main__":
    main()
