#!/usr/bin/env python3
"""Offline unit and integration tests for stock_analyzer.py and sample_data.py.

Run from any directory:
  python3 test_stock_analyzer.py [-v]

No network access is required: live-fetch logic is tested via an injected
fake HTTP client.
"""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sample_data  # noqa: E402
import stock_analyzer as sa  # noqa: E402


def bars_from_rows(rows):
    """Build Bar lists from (date, open, high, low, close, volume) tuples."""
    return [
        sa.Bar(
            d=sa.parse_date(d), open=o, high=h, low=lo, close=c, volume=v
        )
        for d, o, h, lo, c, v in rows
    ]


def linear_bars(n=10, start=100.0, step=1.0, start_date="2024-01-01"):
    """n bars closing 100, 101, 102, ... with zero volume."""
    base = sa.parse_date(start_date)
    out = []
    for i in range(n):
        d = base.fromordinal(base.toordinal() + i)
        close = start + i * step
        out.append(sa.Bar(d=d, open=close, high=close, low=close, close=close, volume=0.0))
    return out

class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


STOOQ_SAMPLE = (
    "Date,Open,High,Low,Close,Volume\n"
    "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    "2024-01-03,10.2,10.6,10.0,10.5,1100\n"
    "2024-01-04,10.5,10.7,10.1,10.3,900\n"
)


def statistics_mean(xs):
    return sum(xs) / len(xs)


def statistics_stdev(xs):
    m = statistics_mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


class TestParsingHelpers(unittest.TestCase):
    def test_parse_date_common_formats(self):
        expected = date(2024, 3, 15)
        cases = [
            "2024-03-15",
            "2024/03/15",
            "20240315",
            "03/15/2024",
            "15-Mar-2024",
            "Mar 15, 2024",
            "March 15, 2024",
        ]
        for text in cases:
            self.assertEqual(sa.parse_date(text), expected, msg=text)
        self.assertEqual(sa.parse_date("2024-03-15 09:30:00"), expected)
        self.assertIsNone(sa.parse_date("not a date"))
        self.assertIsNone(sa.parse_date(""))
        self.assertIsNone(sa.parse_date("15/03/2024 25:99"))  # invalid time

    def test_parse_float_edge_cases(self):
        self.assertEqual(sa.parse_float("1,234.5"), 1234.5)
        self.assertEqual(sa.parse_float(" 42 "), 42.0)
        self.assertEqual(sa.parse_float("-3.25"), -3.25)
        self.assertIsNone(sa.parse_float(""))
        self.assertIsNone(sa.parse_float(None))
        self.assertIsNone(sa.parse_float("n/a"))
        self.assertIsNone(sa.parse_float("abc"))
        self.assertIsNone(sa.parse_float("--"))


class TestBarsFromDicts(unittest.TestCase):
    def test_header_variants_and_case(self):
        rows = [
            {"DATE": "2024-01-02", "CLOSE LAST": "10.2", "Vol": "1000"},
            {"DATE": "2024-01-03", "CLOSE LAST": "10.5", "Vol": "1100"},
        ]
        bars, warns, col = sa.bars_from_dicts(rows, "src", prefer_adj_close=False)
        self.assertEqual(len(bars), 2)
        self.assertEqual(col, "close")
        self.assertEqual(bars[0].close, 10.2)
        # missing OHLC columns are filled from close
        self.assertEqual((bars[0].open, bars[0].high, bars[0].low), (10.2, 10.2, 10.2))
        self.assertEqual(bars[0].volume, 1000.0)

    def test_adj_close_preferred_and_fallback(self):
        rows = [
            {"Date": "2024-01-02", "Close": "10.0", "Adj Close": "9.8"},
            {"Date": "2024-01-03", "Close": "10.5", "Adj Close": ""},
        ]
        bars, _, col = sa.bars_from_dicts(rows, "src", prefer_adj_close=True)
        self.assertEqual(col, "adj close")
        self.assertEqual(bars[0].close, 9.8)
        self.assertEqual(bars[1].close, 10.5)  # blank adj falls back to close

    def test_raw_close_flag(self):
        rows = [{"Date": "2024-01-02", "Close": "10.0", "Adj Close": "9.8"}]
        bars, _, col = sa.bars_from_dicts(rows, "src", prefer_adj_close=False)
        self.assertEqual(col, "close")
        self.assertEqual(bars[0].close, 10.0)

    def test_bad_rows_skipped_with_warning(self):
        rows = [
            {"Date": "2024-01-02", "Close": "10.0"},
            {"Date": "garbage", "Close": "10.1"},
            {"Date": "2024-01-04", "Close": "oops"},
            {"Date": "2024-01-05", "Close": "10.4"},
        ]
        bars, warns, _ = sa.bars_from_dicts(rows, "src", prefer_adj_close=False)
        self.assertEqual(len(bars), 2)
        self.assertEqual(len(warns), 2)
        # Row numbers are physical CSV lines (row 1 is the header), so the
        # bad date is on line 3 and the bad price on line 4.
        self.assertIn("skipped row 3", warns[0])
        self.assertIn("skipped row 4", warns[1])

    def test_no_date_column_raises(self):
        with self.assertRaises(sa.DataError):
            sa.bars_from_dicts([{"Close": "10"}], "src")

    def test_high_low_consistency(self):
        rows = [
            {"Date": "2024-01-02", "Open": "10", "High": "5", "Low": "20", "Close": "11"},
        ]
        bars, _, _ = sa.bars_from_dicts(rows, "src", prefer_adj_close=False)
        b = bars[0]
        self.assertGreaterEqual(b.high, max(b.open, b.close))
        self.assertLessEqual(b.low, min(b.open, b.close))


class TestFinalizeBars(unittest.TestCase):
    def test_sort_and_dedupe_keeps_last(self):
        bars = bars_from_rows(
            [
                ("2024-01-03", 0, 0, 0, 13.0, 0),
                ("2024-01-02", 0, 0, 0, 11.0, 0),
                ("2024-01-03", 0, 0, 0, 12.0, 0),
                ("2024-01-01", 0, 0, 0, 10.0, 0),
            ]
        )
        out, warns = sa.finalize_bars(bars, "src")
        self.assertEqual([b.d.isoformat() for b in out], ["2024-01-01", "2024-01-02", "2024-01-03"])
        self.assertEqual(out[-1].close, 12.0)  # duplicate keeps the later row
        self.assertEqual(len(warns), 2)  # unsorted + duplicate


class TestLoadCsv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text, encoding="utf-8"):
        p = self.dir / name
        p.write_text(text, encoding=encoding)
        return str(p)

    def test_load_ok_with_bom(self):
        path = self.write("bom.csv", "Date,Close\n2024-01-02,10.0\n2024-01-03,10.5\n", "utf-8-sig")
        symbol, bars, warns, col = sa.load_csv(path)
        self.assertEqual(symbol, "bom")
        self.assertEqual(len(bars), 2)
        self.assertEqual(col, "close")

    def test_missing_file(self):
        with self.assertRaises(sa.DataError):
            sa.load_csv(str(self.dir / "nope.csv"))

    def test_empty_file(self):
        path = self.write("empty.csv", "")
        with self.assertRaises(sa.DataError):
            sa.load_csv(path)

    def test_whitespace_only_file(self):
        path = self.write("blank.csv", "   \n  \n")
        with self.assertRaises(sa.DataError):
            sa.load_csv(path)

    def test_no_data_rows(self):
        path = self.write("hdr.csv", "Date,Close\n")
        with self.assertRaises(sa.DataError):
            sa.load_csv(path)

    def test_directory_rejected(self):
        with self.assertRaises(sa.DataError):
            sa.load_csv(str(self.dir))

    def test_filter_range(self):
        path = self.write("f.csv", "Date,Close\n2024-01-01,1\n2024-01-02,2\n2024-01-03,3\n2024-01-04,4\n")
        _, bars, _, _ = sa.load_csv(path)
        kept = sa.filter_range(bars, sa.parse_date("2024-01-02"), sa.parse_date("2024-01-03"))
        self.assertEqual([b.d.isoformat() for b in kept], ["2024-01-02", "2024-01-03"])
        self.assertEqual(sa.filter_range(bars, None, None), bars)


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = sa.sma_series(vals, 3)
        self.assertEqual(out, [None, None, 2.0, 3.0, 4.0])
        self.assertEqual(sa.sma_series([], 3), [])
        self.assertEqual(sa.sma_series([1.0, 2.0], 5), [None, None])

    def test_ema_seed_and_recurrence(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        out = sa.ema_series(vals, 2)
        self.assertIsNone(out[0])
        self.assertEqual(out[1], 1.5)  # SMA seed
        self.assertAlmostEqual(out[2], 3.0 * (2 / 3) + 1.5 * (1 / 3))
        self.assertAlmostEqual(out[3], 4.0 * (2 / 3) + out[2] * (1 / 3))

    def test_daily_returns(self):
        out = sa.daily_returns([100.0, 110.0, 99.0])
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0], 0.1)
        self.assertAlmostEqual(out[1], -0.1)
        self.assertEqual(sa.daily_returns([0.0, 5.0]), [])  # non-positive base skipped
        self.assertEqual(sa.daily_returns([5.0]), [])

    def test_rsi_monotonic_up_is_100(self):
        closes = [float(i) for i in range(1, 25)]
        out = sa.rsi_series(closes, 14)
        self.assertIsNone(out[13])
        for v in out[14:]:
            self.assertAlmostEqual(v, 100.0)

    def test_rsi_monotonic_down_is_0(self):
        closes = [float(50 - i) for i in range(25)]
        out = sa.rsi_series(closes, 14)
        for v in out[14:]:
            self.assertAlmostEqual(v, 0.0)

    def test_rsi_flat_is_50(self):
        closes = [10.0] * 30
        out = sa.rsi_series(closes, 14)
        for v in out[14:]:
            self.assertAlmostEqual(v, 50.0)

    def test_rsi_known_value(self):
        # Wilder's classic worked example: the first RSI value uses simple
        # averages of the first 14 changes. Gains total +3.34 and losses 1.40,
        # so RSI = 100 * 3.34 / (3.34 + 1.40) ~= 70.46.
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        avg_gain = sum(c for c in changes if c > 0) / 14
        avg_loss = -sum(c for c in changes if c < 0) / 14
        expected = 100 * avg_gain / (avg_gain + avg_loss)
        out = sa.rsi_series(closes, 14)
        self.assertAlmostEqual(out[14], expected, places=6)
        self.assertAlmostEqual(out[14], 70.46, places=2)

    def test_bollinger_constant_series(self):
        closes = [5.0] * 25
        mid, up, lo = sa.bollinger_series(closes, 20, 2.0)
        self.assertEqual(up[19], 5.0)
        self.assertEqual(lo[19], 5.0)
        self.assertEqual(sa._percent_b(5.0, up[19], lo[19]), 50.0)

    def test_bollinger_matches_hand_computed(self):
        closes = [1.0, 2.0, 3.0, 4.0, 5.0]
        _, up, lo = sa.bollinger_series(closes, 4, 2.0)
        # window [1,2,3,4]: mean 2.5, pstdev sqrt(1.25)
        self.assertAlmostEqual(up[3], 2.5 + 2 * math.sqrt(1.25))
        self.assertAlmostEqual(lo[3], 2.5 - 2 * math.sqrt(1.25))

    def test_macd_lengths_and_none_alignment(self):
        closes = [float(i) for i in range(1, 41)]
        macd, sig, hist = sa.macd_series(closes, 12, 26, 9)
        self.assertEqual(len(macd), 40)
        self.assertIsNone(macd[24])
        self.assertIsNotNone(macd[25])
        first_signal = 25 + 9 - 1
        self.assertIsNone(sig[first_signal - 1])
        self.assertIsNotNone(sig[first_signal])
        for i in range(40):
            if sig[i] is not None:
                self.assertAlmostEqual(hist[i], macd[i] - sig[i])

    def test_atr_constant_range(self):
        bars = bars_from_rows(
            [(f"2024-01-{d:02d}", 9.0, 11.0, 9.0, 10.0, 0) for d in range(1, 22)]
        )
        out = sa.atr_series(bars, 14)
        # day 0 TR excluded: all TRs are 2.0 once previous close is 10
        self.assertAlmostEqual(out[14], 2.0)
        self.assertAlmostEqual(out[20], 2.0)
        self.assertIsNone(out[13])

    def test_drawdown_and_max_drawdown(self):
        closes = [100.0, 120.0, 60.0, 80.0, 130.0]
        dates = [sa.parse_date(f"2024-01-{i:02d}") for i in range(1, 6)]
        dd = sa.drawdown_series(closes)
        # at index 3 the running peak is 120, so drawdown is 80/120 - 1 = -1/3
        self.assertEqual(dd, [0.0, 0.0, -0.5, 80.0 / 120.0 - 1.0, 0.0])
        info = sa.max_drawdown_info(closes, dates)
        self.assertAlmostEqual(info["depth"], -0.5)
        self.assertEqual(info["peak_date"], "2024-01-02")
        self.assertEqual(info["trough_date"], "2024-01-03")
        self.assertEqual(info["recovery_date"], "2024-01-05")

    def test_max_drawdown_unrecovered(self):
        closes = [100.0, 120.0, 90.0]
        dates = [sa.parse_date(f"2024-01-{i:02d}") for i in range(1, 4)]
        info = sa.max_drawdown_info(closes, dates)
        self.assertIsNone(info["recovery_date"])

    def test_cagr(self):
        # 252 trading days doubling -> exactly 100% CAGR
        self.assertAlmostEqual(sa.cagr([100.0] + [100.0] * 251 + [200.0]), 1.0)
        self.assertIsNone(sa.cagr([5.0]))
        self.assertIsNone(sa.cagr([0.0, 5.0]))

    def test_sharpe_and_sortino(self):
        # constant positive returns: zero stdev -> Sharpe None
        self.assertIsNone(sa.sharpe_ratio([0.01, 0.01, 0.01]))
        self.assertIsNone(sa.sortino_ratio([0.01, 0.01, 0.01]))  # no downside
        rets = [0.02, -0.01, 0.015, -0.02, 0.01]
        s = sa.sharpe_ratio(rets)
        expected = (
            statistics_mean(rets) / statistics_stdev(rets) * math.sqrt(252)
        )
        self.assertAlmostEqual(s, expected)
        self.assertIsNotNone(sa.sortino_ratio(rets))

    def test_sharpe_with_risk_free(self):
        rets = [0.02, -0.01, 0.015, -0.02, 0.01]
        rf = sa.sharpe_ratio(rets, risk_free=0.02)
        daily_rf = 0.02 / 252
        excess = [r - daily_rf for r in rets]
        expected = statistics_mean(excess) / statistics_stdev(excess) * math.sqrt(252)
        self.assertAlmostEqual(rf, expected)

    def test_quantile(self):
        self.assertEqual(sa.quantile([], 0.5), None)
        self.assertEqual(sa.quantile([5.0], 0.5), 5.0)
        vals = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(sa.quantile(vals, 0.0), 1.0)
        self.assertEqual(sa.quantile(vals, 1.0), 4.0)
        self.assertEqual(sa.quantile(vals, 0.5), 2.5)
        self.assertAlmostEqual(sa.quantile(vals, 0.25), 1.75)

    def test_skewness(self):
        sym = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        self.assertAlmostEqual(sa.skewness(sym), 0.0)
        self.assertIsNone(sa.skewness([1.0, 2.0]))
        # heavy right tail -> positive skew
        right = [1.0, 1.1, 1.2, 1.3, 1.4, 9.0]
        self.assertGreater(sa.skewness(right), 0.5)

    def test_streaks(self):
        s = sa.streaks([0.01, 0.02, 0.03, -0.01, -0.02, 0.01, 0.02])
        self.assertEqual(s["max_up_streak"], 3)
        self.assertEqual(s["max_down_streak"], 2)
        self.assertEqual(s["current_streak"], 2)
        self.assertEqual(s["current_direction"], "up")
        flat = sa.streaks([0.0, 0.0])
        self.assertEqual(flat["current_direction"], "flat")
        self.assertEqual(flat["max_up_streak"], 0)

    def test_cross_events(self):
        fast = [1.0, 2.0, 3.0, 2.0, 1.0, 2.0]
        slow = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
        dates = [sa.parse_date(f"2024-01-{i:02d}") for i in range(1, 7)]
        events = sa.cross_events(fast, slow, dates)
        # index 5 has fast == slow, which is not a crossing
        self.assertEqual(events, [(dates[2], "up"), (dates[4], "down")])
        # leading Nones are tolerated
        events = sa.cross_events([None, 1.0, 3.0], [2.0, 2.0, 2.0], dates[:3])
        self.assertEqual(events, [(dates[2], "up")])
        # equality never counts as a crossing
        self.assertEqual(sa.cross_events([None, 1.0, 2.0], [2.0, 2.0, 2.0], dates[:3]), [])


class TestTrend(unittest.TestCase):
    def test_classification(self):
        smas = {20: 90.0, 50: 80.0, 200: 70.0}
        self.assertEqual(sa.classify_trend(100.0, smas), "bullish")
        self.assertEqual(sa.classify_trend(60.0, smas), "bearish")
        self.assertEqual(sa.classify_trend(85.0, smas), "mixed")  # 1 of 3 pass
        flat = {20: 100.0, 50: 100.0, 200: 100.0}
        self.assertEqual(sa.classify_trend(100.0, flat), "neutral")
        self.assertIsNone(sa.classify_trend(100.0, {20: None, 50: None}))
        # partial definition still works
        self.assertEqual(sa.classify_trend(100.0, {20: 90.0, 50: None}), "bullish")


class TestAnalyze(unittest.TestCase):
    def test_analyze_linear_series(self):
        bars = linear_bars(60)
        res = sa.analyze("LIN", bars, sa.Config(sma_windows=(5, 10)))
        self.assertEqual(res["overview"]["trading_days"], 60)
        self.assertAlmostEqual(res["returns"]["total_return"], 59.0 / 100.0)
        self.assertAlmostEqual(res["returns"]["best_day"]["return"], 0.01)
        self.assertAlmostEqual(res["returns"]["positive_days_ratio"], 1.0)
        self.assertEqual(res["returns"]["current_streak"]["direction"], "up")
        self.assertAlmostEqual(res["prices"]["current_dd"], 0.0)
        self.assertAlmostEqual(res["risk"]["max_drawdown"]["depth"], 0.0)
        # A linear price ramp has slightly *declining* daily returns (1/100,
        # 1/101, ...), not constant ones, so Sharpe is defined and very large.
        sharpe = res["risk"]["sharpe"]
        self.assertIsNotNone(sharpe)
        self.assertGreater(sharpe, 50.0)
        self.assertAlmostEqual(res["indicators"]["rsi"], 100.0)
        self.assertEqual(res["signals"]["trend"], "bullish")
        self.assertEqual(len(res["series"]["dates"]), 60)
        self.assertTrue(res["insights"])

    def test_analyze_requires_two_bars_and_positive(self):
        with self.assertRaises(sa.AnalysisError):
            sa.analyze("X", linear_bars(1), sa.Config())
        bad = linear_bars(3)
        bad = [sa.Bar(b.d, b.open, b.high, b.low, -1.0, b.volume) for b in bad]
        with self.assertRaises(sa.AnalysisError):
            sa.analyze("X", bad, sa.Config())

    def test_insight_sentences(self):
        bars = linear_bars(60)
        res = sa.analyze("LIN", bars, sa.Config(sma_windows=(5, 10)))
        text = " ".join(res["insights"])
        self.assertIn("Trend: bullish", text)
        self.assertIn("RSI", text)
        self.assertIn("Total return", text)


class TestCorrelation(unittest.TestCase):
    def test_perfect_and_inverse(self):
        # Correlation runs on daily RETURNS, so build the price series from an
        # explicit returns pattern: A and B share it, C mirrors it exactly.
        pattern = [0.05, -0.02, 0.08, -0.04, 0.03, -0.01, 0.06, -0.03,
                   0.04, -0.02, 0.07, -0.05, 0.02, -0.01, 0.09, -0.04,
                   0.05, -0.02, 0.06, -0.03]

        def build(start: float, sign: float):
            closes = [start]
            for r in pattern:
                closes.append(closes[-1] * (1.0 + sign * r))
            days = [f"2024-01-{i:02d}" for i in range(1, len(closes) + 1)]
            return bars_from_rows([(d, 0, 0, 0, c, 0) for d, c in zip(days, closes)])

        a, b, c = build(100.0, 1.0), build(50.0, 1.0), build(200.0, -1.0)
        res_a, res_b, res_c = {"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}
        corr = sa.correlation_matrix([res_a, res_b, res_c], {"A": a, "B": b, "C": c})
        self.assertAlmostEqual(corr["A|B"], 1.0, places=6)
        self.assertAlmostEqual(corr["A|C"], -1.0, places=6)
        self.assertAlmostEqual(corr["B|C"], -1.0, places=6)

    def test_insufficient_overlap(self):
        a = bars_from_rows([("2024-01-02", 0, 0, 0, 10, 0), ("2024-01-03", 0, 0, 0, 11, 0)])
        b = bars_from_rows([("2024-02-02", 0, 0, 0, 10, 0), ("2024-02-03", 0, 0, 0, 11, 0)])
        corr = sa.correlation_matrix([{"symbol": "A"}, {"symbol": "B"}], {"A": a, "B": b})
        self.assertIsNone(corr["A|B"])


class TestFetchStooq(unittest.TestCase):
    def test_success_plain_symbol(self):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen["params"] = params
            return FakeResponse(STOOQ_SAMPLE)

        name, bars = sa.fetch_stooq("AAPL", http_get=fake_get)
        self.assertEqual(name, "aapl")
        self.assertEqual(len(bars), 3)
        self.assertEqual(seen["params"]["s"], "aapl")
        self.assertEqual(seen["params"]["i"], "d")

    def test_us_suffix_fallback(self):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params["s"])
            if params["s"] == "aapl":
                return FakeResponse("No data")  # body indicates failure
            return FakeResponse(STOOQ_SAMPLE)

        name, bars = sa.fetch_stooq("aapl", http_get=fake_get)
        self.assertEqual(name, "aapl.us")
        self.assertEqual(calls, ["aapl", "aapl.us"])

    def test_date_params_forwarded(self):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen.update(params)
            return FakeResponse(STOOQ_SAMPLE)

        sa.fetch_stooq(
            "aapl",
            start=sa.parse_date("2024-01-01"),
            end=sa.parse_date("2024-06-30"),
            http_get=fake_get,
        )
        self.assertEqual(seen["d1"], "20240101")
        self.assertEqual(seen["d2"], "20240630")

    def test_http_error_raises_fetch_error(self):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse("boom", status=500)

        with self.assertRaises(sa.FetchError):
            sa.fetch_stooq("aapl", http_get=fake_get)

    def test_empty_symbol(self):
        with self.assertRaises(sa.FetchError):
            sa.fetch_stooq("  ", http_get=lambda *a, **k: FakeResponse(STOOQ_SAMPLE))

    def test_block_page_raises_actionable_error(self):
        # Stooq serves a JS browser-verification page to plain HTTP clients.
        block_html = (
            "<!DOCTYPE html><html><body><noscript>This site requires JavaScript "
            "to verify your browser.</noscript><script>fetch(\"/__verify\")</script>"
        )

        def fake_get(url, params=None, timeout=None):
            return FakeResponse(block_html)

        with self.assertRaises(sa.FetchError) as ctx:
            sa.fetch_stooq("aapl", http_get=fake_get)
        self.assertIn("browser-verification", str(ctx.exception))
        self.assertIn("Download the CSV", str(ctx.exception))

    def test_block_detector_direct(self):
        self.assertTrue(sa._looks_like_block_page("<!DOCTYPE html><html>challenge"))
        self.assertTrue(sa._looks_like_block_page("  <html><script>__verify</script>"))
        self.assertFalse(sa._looks_like_block_page(STOOQ_SAMPLE))
        self.assertFalse(sa._looks_like_block_page(""))
        self.assertFalse(sa._looks_like_block_page("Date,Open,High,Low,Close\n2024-01-02,10,11,9,10.5\n"))

    def test_explicit_suffix_not_duplicated(self):
        def fake_get(url, params=None, timeout=None):
            self.assertTrue(params["s"].endswith(".us"))
            return FakeResponse(STOOQ_SAMPLE)

        name, _ = sa.fetch_stooq("MSFT.US", http_get=fake_get)
        self.assertEqual(name, "msft.us")


class TestExports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = sa.Config(sma_windows=(5, 10))
        self.results = [sa.analyze("LIN", linear_bars(30), self.cfg)]

    def test_json_round_trip(self):
        path = self.dir / "out.json"
        sa.export_json(self.results, self.cfg, None, str(path))
        data = json.loads(path.read_text())
        self.assertEqual(data["analyzer"], sa.VERSION)
        self.assertIn("generated_at", data)
        self.assertEqual(data["symbols"][0]["symbol"], "LIN")
        self.assertNotIn("series", data["symbols"][0])  # bulky series excluded
        self.assertEqual(data["settings"]["sma_windows"], [5, 10])
        # values were rounded
        self.assertLessEqual(
            len(str(data["symbols"][0]["returns"]["total_return"]).split(".")[-1]), 6
        )

    def test_json_allow_nan_false(self):
        # json.dumps runs with allow_nan=False; normal results must serialize
        # without raising. NaN in a nested value would raise ValueError.
        path = self.dir / "ok.json"
        sa.export_json(self.results, self.cfg, None, str(path))
        self.assertTrue(path.exists())
        # a NaN injected into the result must be rejected, not written
        bad = [dict(self.results[0])]
        bad[0]["risk"] = dict(bad[0]["risk"], sharpe=float("nan"))
        with self.assertRaises(ValueError):
            sa.export_json(bad, self.cfg, None, str(self.dir / "nan.json"))

    def test_metrics_csv(self):
        path = self.dir / "metrics.csv"
        sa.export_metrics_csv(self.results, str(path))
        lines = path.read_text().strip().splitlines()
        self.assertEqual(lines[0].split(",")[0], "symbol")
        self.assertEqual(lines[1].split(",")[0], "LIN")
        header = lines[0].split(",")
        for must in ("total_return", "sharpe", "rsi", "trend", "dd_recovery_date"):
            self.assertIn(must, header)

    def test_series_csv_rows(self):
        path = self.dir / "series.csv"
        sa.export_series_csv(self.results, str(path), self.cfg)
        lines = path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1 + 30)  # header + one row per day
        header = lines[0].split(",")
        for must in ("Date", "Symbol", "Close", "SMA5", "SMA10", "RSI", "Drawdown"):
            self.assertIn(must, header)
        first_data = lines[1].split(",")
        self.assertEqual(first_data[1], "LIN")
        # SMA not yet defined on day 1 -> empty cell
        self.assertEqual(first_data[3], "")

    def test_stdout_dash_variants(self):
        for fn, name in (
            (sa.export_metrics_csv, "csv"),
            (sa.export_series_csv, "series"),
        ):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                if name == "csv":
                    fn(self.results, "-")
                else:
                    fn(self.results, "-", self.cfg)
            self.assertIn("LIN", buf.getvalue())
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            sa.export_json(self.results, self.cfg, None, "-")
        data = json.loads(buf.getvalue())
        self.assertEqual(data["symbols"][0]["symbol"], "LIN")


class TestCliMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.good = self.dir / "good.csv"
        self.good.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            + "".join(
                f"2024-01-{d:02d},{100+i},{101+i},{99+i},{100+i},{1000}\n"
                for i, d in enumerate(range(1, 11), start=1)
            )
        )
        self.bad = self.dir / "bad.csv"
        self.bad.write_text("Foo,Bar\n1,2\n")

    def run_main(self, *argv):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = sa.main(list(argv))
        return code, stdout.getvalue()

    def test_success_exit_zero(self):
        code, out = self.run_main(str(self.good))
        self.assertEqual(code, 0)
        self.assertIn("Symbol: good", out)
        self.assertIn("Settings:", out)

    def test_argparse_rejects_bad_date(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_main(str(self.good), "--start", "01-2024-04")
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_file_exit_two(self):
        code, _ = self.run_main(str(self.dir / "missing.csv"))
        self.assertEqual(code, 2)

    def test_partial_failure_exit_one_with_report(self):
        code, out = self.run_main(str(self.good), str(self.bad))
        self.assertEqual(code, 1)
        self.assertIn("Symbol: good", out)

    def test_no_report_with_json_stdout(self):
        json_path = self.dir / "out.json"
        code, out = self.run_main(
            str(self.good), "--no-report", "--json", str(json_path)
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        data = json.loads(json_path.read_text())
        self.assertEqual(data["symbols"][0]["symbol"], "good")

    def test_date_filtering(self):
        code, out = self.run_main(
            str(self.good), "--start", "2024-01-05", "--end", "2024-01-08"
        )
        self.assertEqual(code, 0)
        self.assertIn("2024-01-05 to 2024-01-08", out)

    def test_all_filtered_out_is_analysis_error(self):
        code, out = self.run_main(
            str(self.good), "--start", "2030-01-01", "--end", "2030-12-31"
        )
        self.assertEqual(code, 2)

    def test_fetch_failure_still_exit_two_when_only_source(self):
        def fake_fetch(sym, start=None, end=None, http_get=None, timeout=15.0):
            raise sa.FetchError("network down")

        with mock.patch.object(sa, "fetch_stooq", fake_fetch):
            code, out = self.run_main("--fetch", "aapl")
        self.assertEqual(code, 2)

    def test_fetch_success_path(self):
        def fake_fetch(sym, start=None, end=None, http_get=None, timeout=15.0):
            return sym, bars_from_rows(
                [(f"2024-01-{d:02d}", 0, 0, 0, 10 + i, 0) for i, d in enumerate(range(1, 21))]
            )

        with mock.patch.object(sa, "fetch_stooq", fake_fetch):
            code, out = self.run_main("--fetch", "aapl")
        self.assertEqual(code, 0)
        self.assertIn("Symbol: AAPL", out)

    def test_validation_errors_exit_two(self):
        for bad_args in (
            ["--sma", "20", "50", "50"],  # duplicate windows
            ["--sma", "0", "50"],  # window < 1
            ["--macd", "26", "12", "9"],  # fast >= slow
            ["--bollinger", "1", "2"],  # window < 2
            ["--bollinger", "20", "0"],  # std <= 0
            ["--rsi-period", "0"],
            ["--top", "0"],
            ["--risk-free", "-0.01"],
        ):
            with self.assertRaises(SystemExit) as ctx:
                self.run_main(str(self.good), *bad_args)
            self.assertEqual(ctx.exception.code, 2, msg=str(bad_args))
        # Providing no input at all is also a usage error.
        with self.assertRaises(SystemExit) as ctx:
            self.run_main()
        self.assertEqual(ctx.exception.code, 2)

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_main("--version")
        self.assertEqual(ctx.exception.code, 0)


class TestSampleData(unittest.TestCase):
    def test_deterministic_output(self):
        rows1 = sample_data.make_bars("AAPL", 60, seed=42)
        rows2 = sample_data.make_bars("AAPL", 60, seed=42)
        self.assertEqual(rows1, rows2)
        rows_other = sample_data.make_bars("AAPL", 60, seed=7)
        self.assertNotEqual(rows1, rows_other)

    def test_symbols_differ(self):
        a = sample_data.make_bars("AAPL", 60, 42)
        k = sample_data.make_bars("KO", 60, 42)
        self.assertNotEqual([r[4] for r in a], [r[4] for r in k])

    def test_trading_days_weekdays_only(self):
        days = sample_data.trading_days(20, start=sa.parse_date("2024-01-01"))
        self.assertEqual(len(days), 20)
        self.assertTrue(all(d.weekday() < 5 for d in days))

    def test_high_low_bracket_close(self):
        rows = sample_data.make_bars("GME", 60, 42)
        for d, o, h, lo, c, v in rows:
            self.assertGreaterEqual(h, max(o, c))
            self.assertLessEqual(lo, min(o, c))
            self.assertGreater(v, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
