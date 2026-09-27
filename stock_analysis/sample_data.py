#!/usr/bin/env python3
"""Generate deterministic synthetic OHLCV stock data for the analyzer.

Produces realistic-looking daily bars (open/high/low/close/volume) with a
seeded pseudorandom walk plus regime shifts, so the analyzer's outputs are
reproducible across runs. Runs entirely offline with the standard library.

Usage:
  python3 sample_data.py                    # writes AAPL.csv, MSFT.csv, ... in cwd
  python3 sample_data.py -o data            # writes data/AAPL.csv, data/MSFT.csv, ...
  python3 sample_data.py --days 300 --seed 7
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

SYMBOLS = {
    # symbol: (drift_bps, vol_bps, start_price)  bps = basis points per day
    "AAPL": (6, 120, 150.0),
    "MSFT": (5, 110, 220.0),
    "NVDA": (14, 320, 40.0),
    "GME": (-2, 450, 30.0),
    "KO": (2, 60, 55.0),
}

TRADING_START = date(2023, 1, 2)


def trading_days(count: int, start: date = TRADING_START) -> list[date]:
    """Generate `count` weekday dates (Mon-Fri) starting from `start`."""
    days: list[date] = []
    cur = start
    while len(days) < count:
        if cur.weekday() < 5:  # Mon=0 .. Fri=4
            days.append(cur)
        cur += timedelta(days=1)
    return days


def make_bars(symbol: str, days: int, seed: int):
    """Return rows of (date, open, high, low, close, volume) for one symbol."""
    drift_bps, vol_bps, start_price = SYMBOLS[symbol]
    rng = random.Random(f"{symbol}-{seed}")
    rows = []
    price = start_price
    regime = 1.0

    for i, d in enumerate(trading_days(days)):
        # regime shifts: occasional multi-week trend changes
        if rng.random() < 0.01:
            regime = rng.choice([-1.2, -0.7, 0.8, 1.3])
        # annual seasonality so long SMA crossings occur
        season = 0.5 * math.sin(2 * math.pi * i / 126)
        drift = drift_bps / 10_000 + season * 0.0006 + regime * 0.0004
        vol = vol_bps / 10_000
        ret = rng.gauss(drift, vol)
        close = max(round(price * (1 + ret), 2), 0.10)

        gap = rng.uniform(-0.004, 0.004)
        open_ = max(round(price * (1 + gap), 2), 0.10)
        spread = abs(close - open_) + price * rng.uniform(0.002, 0.012)
        high = round(max(open_, close) + spread * rng.uniform(0.3, 1.0), 2)
        low = round(max(min(open_, close) - spread * rng.uniform(0.3, 1.0), 0.05), 2)

        base = {
            "AAPL": 55_000_000,
            "MSFT": 22_000_000,
            "NVDA": 240_000_000,
            "GME": 4_000_000,
            "KO": 14_000_000,
        }[symbol]
        volume = int(base * rng.uniform(0.55, 1.7) * (1 + 3 * abs(ret)))

        rows.append((d.isoformat(), open_, high, low, close, volume))
        price = close
    return rows


def write_csv(path: Path, symbol: str, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writerows(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out-dir", default=".", help="output directory (default: cwd)")
    parser.add_argument("--days", type=int, default=504, help="trading days per symbol (default: 504)")
    parser.add_argument("--seed", type=int, default=42, help="PRNG seed (default: 42)")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=sorted(SYMBOLS),
        help=f"symbols to generate, subset of {sorted(SYMBOLS)}",
    )
    args = parser.parse_args(argv)
    if args.days < 30:
        parser.error("--days must be >= 30")
    for sym in args.symbols:
        if sym not in SYMBOLS:
            parser.error(f"unknown symbol {sym!r}, choose from {sorted(SYMBOLS)}")

    out = Path(args.out_dir)
    for sym in args.symbols:
        rows = make_bars(sym, args.days, args.seed)
        target = out / f"{sym}.csv"
        write_csv(target, sym, rows)
        print(f"wrote {target} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
