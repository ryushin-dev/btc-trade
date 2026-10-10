"""指値（Maker）中心の逆張りバンド。サンプル戦略。

- ノーポジ: SMA - k*σ に指値買いを置き、毎バー出し直す。
- 保有中: 加重平均取得価格 * (1 + take_profit) に指値売り。
- 終値が加重平均取得価格 * (1 - stop_loss) を割ったら成行で損切り。
  終値で判定して次バーで売るため、急落やギャップでは損失が stop_loss を超える。
  1日の損失上限は Engine の max_daily_loss で別に止める。

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
        fraction: float = 0.5,
    ) -> None:
        self.window, self.k = int(window), float(k)
        self.take_profit, self.stop_loss, self.fraction = float(take_profit), float(stop_loss), float(fraction)
        self.closes: deque[float] = deque(maxlen=self.window)
        self.position = 0.0
        self.avg_price = 0.0

    def on_fill(self, fill: Fill) -> None:
        if fill.side is Side.BUY:
            cost = self.avg_price * self.position + fill.price * fill.amount
            self.position += fill.amount
            self.avg_price = cost / self.position
        else:
            self.position = max(self.position - fill.amount, 0.0)
            if self.position <= 1e-12:
                self.position, self.avg_price = 0.0, 0.0

    def on_bar(self, bar: Bar, ctx: Context) -> list[Order]:
        self.closes.append(bar.close)
        if not ctx.flat and self.avg_price > 0:
            amount = ctx.available_btc
            if bar.close <= self.avg_price * (1 - self.stop_loss):
                return [Order(Side.SELL, OrderType.MARKET, amount, tag="stop")]
            target = math.ceil(self.avg_price * (1 + self.take_profit))
            return [Order(Side.SELL, OrderType.LIMIT, amount, price=target, tag="take_profit")]
        if not ctx.flat:
            return []  # 取得価格が分からない保有（手動で入れた BTC など）には触らない

        if len(self.closes) < self.window:
            return []
        mean = sum(self.closes) / self.window
        std = math.sqrt(sum((c - mean) ** 2 for c in self.closes) / self.window)
        price = math.floor(mean - self.k * std)
        if price <= 0:
            return []
        # 拘束額（Taker 手数料ぶんの余裕込み）が利用可能残高に収まるように数量を決める
        amount = ctx.available_cash * self.fraction / (price * 1.002)
        if amount < ctx.min_amount:
            return []
        return [Order(Side.BUY, OrderType.LIMIT, amount, price=price, tag="entry")]
