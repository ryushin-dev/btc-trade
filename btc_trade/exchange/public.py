"""bitbank Public API クライアント（認証不要）。

https://github.com/bitbankinc/bitbank-api-docs/blob/master/public-api.md
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date

from ..models import Bar

PUBLIC_BASE = "https://public.bitbank.cc"

# YYYYMMDD 指定のもの（それ以外は YYYY 指定）
DAILY_CANDLE_TYPES = {"1min", "5min", "15min", "30min", "1hour"}
YEARLY_CANDLE_TYPES = {"4hour", "8hour", "12hour", "1day", "1week", "1month"}


class BitbankError(RuntimeError):
    def __init__(self, code: int | None, path: str) -> None:
        super().__init__(f"bitbank API error code={code} path={path}")
        self.code = code


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "btc-trade/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class BitbankPublic:
    def __init__(self, base: str = PUBLIC_BASE, timeout: float = 10.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> dict:
        body = _get_json(self.base + path, self.timeout)
        if body.get("success") != 1:
            raise BitbankError(body.get("data", {}).get("code"), path)
        return body["data"]

    def ticker(self, pair: str = "btc_jpy") -> dict:
        return self._get(f"/{pair}/ticker")

    def depth(self, pair: str = "btc_jpy") -> dict:
        return self._get(f"/{pair}/depth")

    def transactions(self, pair: str = "btc_jpy", day: date | None = None) -> list[dict]:
        suffix = f"/{day:%Y%m%d}" if day else ""
        return self._get(f"/{pair}/transactions{suffix}")["transactions"]

    def candles(self, pair: str, candle_type: str, day: date) -> list[Bar]:
        if candle_type in DAILY_CANDLE_TYPES:
            key = f"{day:%Y%m%d}"
        elif candle_type in YEARLY_CANDLE_TYPES:
            key = f"{day:%Y}"
        else:
            raise ValueError(f"unknown candle type {candle_type!r}")
        data = self._get(f"/{pair}/candlestick/{candle_type}/{key}")
        rows = data["candlestick"][0]["ohlcv"]
        return [parse_ohlcv(r) for r in rows]


def parse_ohlcv(row: list) -> Bar:
    o, h, l, c, v, ts = row
    return Bar(ts=int(ts), open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v))
