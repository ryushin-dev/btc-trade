"""移動平均クロス（成行）。パイプライン確認用のベースライン。

Taker 手数料 0.12% を往復で払うため、手数料込みで勝つことは期待していない。
"""

from __future__ import annotations

from collections import deque

from ..models import Bar, Order, OrderType, Side
from .base import Context, Strategy


class SmaCross(Strategy):
    name = "sma_cross"

    def __init__(self, fast: int = 20, slow: int = 100, fraction: float = 0.99) -> None:
        if fast >= slow:
            raise ValueError("fast must be < slow")
        self.fast, self.slow, self.fraction = int(fast), int(slow), float(fraction)
        self.closes: deque[float] = deque(maxlen=self.slow + 1)

    def on_bar(self, bar: Bar, ctx: Context) -> list[Order]:
        self.closes.append(bar.close)
        if len(self.closes) <= self.slow:
            return []
        c = list(self.closes)
        fast_now, slow_now = _mean(c[-self.fast :]), _mean(c[-self.slow :])
        fast_prev, slow_prev = _mean(c[-self.fast - 1 : -1]), _mean(c[-self.slow - 1 : -1])

        if fast_prev <= slow_prev and fast_now > slow_now and ctx.btc == 0:
            amount = ctx.cash * self.fraction / bar.close
            return [Order(Side.BUY, OrderType.MARKET, amount, tag="entry")]
        if fast_prev >= slow_prev and fast_now < slow_now and ctx.btc > 0:
            return [Order(Side.SELL, OrderType.MARKET, ctx.btc, tag="exit")]
        return []


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)
