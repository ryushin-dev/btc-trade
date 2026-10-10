"""ローソク足の取得・保存・読み込み。"""

from __future__ import annotations

import csv
import math
import random
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path

from .exchange.public import DAILY_CANDLE_TYPES, BitbankPublic
from .models import Bar

FIELDS = ["ts", "open", "high", "low", "close", "volume"]


JST = timezone(timedelta(hours=9))
CANDLE_MS = {
    "1min": 60_000,
    "5min": 300_000,
    "15min": 900_000,
    "30min": 1_800_000,
    "1hour": 3_600_000,
    "4hour": 14_400_000,
    "8hour": 28_800_000,
    "12hour": 43_200_000,
    "1day": 86_400_000,
    "1week": 604_800_000,
}


def fetch_range(
    client: BitbankPublic,
    pair: str,
    candle_type: str,
    start: date,
    end: date,
    pause: float = 0.2,
    now_ms: int | None = None,
) -> list[Bar]:
    """日本時間の start 0:00 〜 end 24:00 に始まり、かつ確定済みの足だけを返す。

    1hour 以下は日単位、4hour 以上は年単位のエンドポイントなので、必要な日・年だけを1回ずつ取得し、
    取得後に期間で絞り込む（期間外のデータが検証に混ざらないようにする）。
    """
    if candle_type not in CANDLE_MS:
        raise ValueError(f"unknown candle type {candle_type!r}")
    if candle_type in DAILY_CANDLE_TYPES:
        # API の日付境界に依存しないよう前後1日を余分に取得してから絞り込む
        keys, day = [], start - timedelta(days=1)
        while day <= end + timedelta(days=1):
            keys.append(day)
            day += timedelta(days=1)
    else:
        keys = [date(y, 1, 1) for y in range(start.year - 1, end.year + 1)]

    lo = int(datetime.combine(start, dt_time(0), JST).timestamp() * 1000)
    hi = int(datetime.combine(end + timedelta(days=1), dt_time(0), JST).timestamp() * 1000)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    span = CANDLE_MS[candle_type]

    bars: dict[int, Bar] = {}
    for key in keys:
        for b in client.candles(pair, candle_type, key):
            if lo <= b.ts < hi and b.ts + span <= now_ms:
                bars[b.ts] = b
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
