"""戦略の呼び出しとリスク管理。バックテストとペーパートレードで共通。"""

from __future__ import annotations

from .broker import BaseBroker, SimBroker
from .models import Bar, Fill, Order, Side
from .strategies import Context, Strategy

DAY_MS = 86_400_000
JST_OFFSET_MS = 9 * 3_600_000


class Engine:
    """
    max_daily_loss: その日（日本時間）の開始時点の清算価値から、この割合を超えて減ったら
    その日は新規の買いを出さない（売りは通す）。
    """

    def __init__(self, strategy: Strategy, broker: BaseBroker, max_daily_loss: float = 0.03) -> None:
        self.strategy = strategy
        self.broker = broker
        self.max_daily_loss = max_daily_loss
        self.last_ts: int | None = None
        self.halted_day: int | None = None
        self._day: int | None = None
        self._day_start_value = 0.0

    def _check_order(self, bar: Bar) -> None:
        if self.last_ts is not None and bar.ts <= self.last_ts:
            raise ValueError(f"bars must be strictly increasing: {bar.ts} <= {self.last_ts}")
        self.last_ts = bar.ts

    def warmup(self, bar: Bar) -> None:
        """指標計算のために過去バーを流す。注文は捨てる（通信断からの復帰時もこれを使う）。"""
        self._check_order(bar)
        self.strategy.on_bar(bar, self._context())

    def notify_fills(self, fills: list[Fill]) -> None:
        for f in fills:
            self.strategy.on_fill(f)

    def decide(self, bar: Bar) -> list[Order]:
        """確定したバーで戦略を呼び、リスク管理を通った注文を返す（まだ発注しない）。"""
        self._check_order(bar)
        orders = self.strategy.on_bar(bar, self._context())
        if self._daily_loss_hit(bar):
            orders = [o for o in orders if o.side is Side.SELL]
        return orders

    def step(self, bar: Bar) -> list[Fill]:
        """バックテスト用: 前バーの注文をこのバーで約定判定し、次の注文を出す。"""
        assert isinstance(self.broker, SimBroker)
        fills = self.broker.process_bar(bar)
        self.notify_fills(fills)
        for order in self.decide(bar):
            self.broker.submit(order, ref_price=bar.close)
        return fills

    def _context(self) -> Context:
        return Context(
            cash=self.broker.cash,
            btc=self.broker.btc,
            available_cash=self.broker.available_cash,
            available_btc=self.broker.available_btc,
            min_amount=self.broker.min_amount,
        )

    def _daily_loss_hit(self, bar: Bar) -> bool:
        day = (bar.ts + JST_OFFSET_MS) // DAY_MS
        value = self.broker.liquidation_value(bar.close)
        if day != self._day:
            self._day, self._day_start_value = day, value
        if self.halted_day == day:
            return True
        if self._day_start_value > 0 and value < self._day_start_value * (1 - self.max_daily_loss):
            self.halted_day = day
            return True
        return False
