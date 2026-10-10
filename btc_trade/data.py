"""ローソク足の取得・保存・読み込み。"""

from __future__ import annotations

import csv
import math
import random
import time
from datetime import date, timedelta
from pathlib import Path

from .exchange.public import BitbankPublic
from .models import Bar

FIELDS = ["ts", "open", "high", "low", "close", "volume"]


def fetch_range(
    client: BitbankPublic, pair: str, candle_type: str, start: date, end: date, pause: float = 0.2
) -> list[Bar]:
    """start〜end（両端含む）の日次エンドポイントを順に叩いて結合する。"""
    bars: dict[int, Bar] = {}
    day = start
    while day <= end:
        for b in client.candles(pair, candle_type, day):
            bars[b.ts] = b
        day += timedelta(days=1)
        time.sleep(pause)
    return [bars[k] for k in sorted(bars)]


def save_csv(bars: list[Bar], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for b in bars:
            w.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])


def load_csv(path: str | Path) -> list[Bar]:
    with Path(path).open(newline="") as f:
        rows = list(csv.DictReader(f))
    bars = [
        Bar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
        for r in rows
    ]
    bars.sort(key=lambda b: b.ts)
    return bars


def synthetic_bars(
    n: int, start_price: float = 10_000_000, vol_per_bar: float = 0.001, seed: int = 0, interval_ms: int = 60_000
) -> list[Bar]:
    """動作確認用の幾何ブラウン運動（ドリフトなし）。実データの代わりにはならない。"""
    rng = random.Random(seed)
    price = start_price
    bars = []
    for i in range(n):
        o = price
        path = [o]
        for _ in range(4):
            path.append(path[-1] * math.exp(rng.gauss(0, vol_per_bar / 2)))
        c = path[-1]
        bars.append(Bar(i * interval_ms, o, max(path), min(path), c, rng.uniform(0.1, 5)))
        price = c
    return bars
