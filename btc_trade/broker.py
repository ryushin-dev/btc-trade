"""約定シミュレータ。バックテストとペーパートレードで同じものを使う。

約定ルール（保守的）:
- 注文はバー t の終値時点で出され、バー t+1 で初めて約定判定される（先読みなし）。
- 成行: 次バーの始値にスリッページを不利な方向へ加えた価格で約定、Taker 手数料。
- 指値買い P: 次バー始値 <= P なら発注時点で板を食う扱いとして始値で Taker 約定。
  そうでなければ安値が P を「下回った」とき（タッチだけでは不可）に P で Maker 約定。
  指値売りは対称。
- 未約定の注文は次のバー確定時に取り消される（戦略が毎バー出し直す）。
- 残高不足の分は約定量を減らす。最小数量未満になれば取り消し。
"""

from __future__ import annotations

from .fees import FeeSchedule
from .models import Bar, Fill, Order, OrderType, Side

MIN_AMOUNT_BTC = 0.0001


class SimBroker:
    def __init__(
        self,
        cash_jpy: float,
        fees: FeeSchedule,
        slippage: float = 0.0005,
        min_amount: float = MIN_AMOUNT_BTC,
    ) -> None:
        self.cash = cash_jpy
        self.btc = 0.0
        self.fees = fees
        self.slippage = slippage
        self.min_amount = min_amount
        self.open_orders: list[Order] = []
        self.fills: list[Fill] = []

    def submit(self, order: Order) -> None:
        if order.amount < self.min_amount:
            return
        self.open_orders.append(order)

    def cancel_all(self) -> None:
        self.open_orders.clear()

    def equity(self, price: float) -> float:
        return self.cash + self.btc * price

    def process_bar(self, bar: Bar) -> list[Fill]:
        new_fills: list[Fill] = []
        for order in self.open_orders:
            fill = self._try_fill(order, bar)
            if fill is not None:
                new_fills.append(fill)
        self.open_orders = []
        self.fills.extend(new_fills)
        return new_fills

    def _try_fill(self, order: Order, bar: Bar) -> Fill | None:
        if order.type is OrderType.MARKET:
            sign = 1 if order.side is Side.BUY else -1
            return self._execute(order, bar.ts, bar.open * (1 + sign * self.slippage), "taker")

        assert order.price is not None
        if order.side is Side.BUY:
            if bar.open <= order.price:
                return self._execute(order, bar.ts, bar.open, "taker")
            if bar.low < order.price:
                return self._execute(order, bar.ts, order.price, "maker")
        else:
            if bar.open >= order.price:
                return self._execute(order, bar.ts, bar.open, "taker")
            if bar.high > order.price:
                return self._execute(order, bar.ts, order.price, "maker")
        return None

    def _execute(self, order: Order, ts: int, price: float, liquidity: str) -> Fill | None:
        rate = self.fees.maker if liquidity == "maker" else self.fees.taker
        amount = order.amount
        if order.side is Side.BUY:
            max_amount = self.cash / (price * (1 + max(rate, 0.0)))
            amount = min(amount, max_amount)
        else:
            amount = min(amount, self.btc)
        amount = _floor(amount, 8)
        if amount < self.min_amount:
            return None

        notional = price * amount
        fee = self.fees.fee(notional, liquidity)
        if order.side is Side.BUY:
            self.cash -= notional + fee
            self.btc += amount
        else:
            self.cash += notional - fee
            self.btc -= amount
        return Fill(ts, order.id, order.side, price, amount, fee, liquidity, order.tag)


def _floor(x: float, digits: int) -> float:
    m = 10**digits
    return int(x * m) / m
