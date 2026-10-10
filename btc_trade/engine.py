"""バー1本ぶんの処理。バックテストとペーパートレードで共通。"""

from __future__ import annotations

from .broker import SimBroker
from .models import Bar, Fill
from .strategies import Context, Strategy


class Engine:
    def __init__(self, strategy: Strategy, broker: SimBroker) -> None:
        self.strategy = strategy
        self.broker = broker
        self.last_ts: int | None = None

    def step(self, bar: Bar) -> list[Fill]:
        if self.last_ts is not None and bar.ts <= self.last_ts:
            raise ValueError(f"bars must be strictly increasing: {bar.ts} <= {self.last_ts}")
        self.last_ts = bar.ts

        # 1) 前のバーで出した注文をこのバーで約定判定
        fills = self.broker.process_bar(bar)
        for f in fills:
            self.strategy.on_fill(f)
        # 2) このバーの確定値で次の注文を決める（約定判定は次のバー）
        ctx = Context(cash=self.broker.cash, btc=self.broker.btc)
        for order in self.strategy.on_bar(bar, ctx):
            self.broker.submit(order)
        return fills

    def warmup(self, bar: Bar) -> None:
        """指標の計算用に過去バーを流す。注文は捨てる。"""
        self.last_ts = bar.ts
        ctx = Context(cash=self.broker.cash, btc=self.broker.btc)
        self.strategy.on_bar(bar, ctx)
