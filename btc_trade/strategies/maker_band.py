"""指値（Maker）中心の逆張りバンド。サンプル戦略。

- ノーポジ: SMA - k*σ に指値買いを置き、毎バー出し直す。
- 保有中: 取得価格 * (1 + take_profit) に指値売り。
- 終値が取得価格 * (1 - stop_loss) を割ったら成行で損切り。

Maker リベートを受け取りつつ小さな値幅を取る狙い。勝てるかどうかはバックテストで確認すること。
"""

from __future__ import annotations

import math
from collections import deque

from ..models import Bar, Fill, Order, OrderType, Side
from .base import Context, Strategy


class MakerBand(Strategy):
    name = "maker_band"

    def __init__(
        self,
        window: int = 60,
        k: float = 2.0,
        take_profit: float = 0.003,
        stop_loss: float = 0.01,
        fraction: float = 0.99,
    ) -> None:
        self.window, self.k = int(window), float(k)
        self.take_profit, self.stop_loss, self.fraction = float(take_profit), float(stop_loss), float(fraction)
        self.closes: deque[float] = deque(maxlen=self.window)
        self.entry_price: float | None = None

    def on_fill(self, fill: Fill) -> None:
        if fill.side is Side.BUY:
            self.entry_price = fill.price
        else:
            self.entry_price = None

    def on_bar(self, bar: Bar, ctx: Context) -> list[Order]:
        self.closes.append(bar.close)
        if ctx.btc > 0 and self.entry_price is not None:
            if bar.close <= self.entry_price * (1 - self.stop_loss):
                return [Order(Side.SELL, OrderType.MARKET, ctx.btc, tag="stop")]
            target = math.ceil(self.entry_price * (1 + self.take_profit))
            return [Order(Side.SELL, OrderType.LIMIT, ctx.btc, price=target, tag="take_profit")]

        if len(self.closes) < self.window:
            return []
        mean = sum(self.closes) / self.window
        std = math.sqrt(sum((c - mean) ** 2 for c in self.closes) / self.window)
        price = math.floor(mean - self.k * std)
        if price <= 0:
            return []
        amount = ctx.cash * self.fraction / price
        return [Order(Side.BUY, OrderType.LIMIT, amount, price=price, tag="entry")]
