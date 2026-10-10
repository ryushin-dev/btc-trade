"""口座管理と約定シミュレータ。

`BaseBroker` が残高・資金拘束・約定の記帳を受け持ち、約定判定だけを派生クラスが実装する。
- `SimBroker`: バックテスト用。ローソク足で約定判定する。
- `PaperBroker`（paper.py）: ペーパートレード用。発注後の実際の板・約定履歴で判定する。

SimBroker の約定ルール（保守的）:
- 注文はバー t の確定後に出され、バー t+1 の中でだけ約定判定される（先読みなし）。
  1分足では、判断から約定まで最大1本ぶんの遅延を見込むことになる。
- 成行: 次バー始値にスリッページを不利な方向へ加えた価格で全量約定、Taker 手数料。
- 指値: 始値 + スリッページの時点で指値の内側なら、その価格で全量 Taker 約定（指値は超えない）。
  そうでなければ、価格が指値を「突き抜けた」バーでだけ Maker 約定する（タッチは不約定）。
  約定量はそのバーの出来高 × participation（既定 10%）が上限で、部分約定になり得る。
- 未約定・部分約定の残りは、そのバーの終わりで失効する（戦略が毎バー出し直す）。
- 発注時に利用可能残高を確認し、資金を拘束する。足りなければ注文は拒否される（数量は縮めない）。
  成行買いは約定時に実際の代金が残高を超えれば拒否される。
"""

from __future__ import annotations

from .fees import FeeSchedule
from .models import Bar, Fill, Order, OrderType, Side

MIN_AMOUNT_BTC = 0.0001
EPS = 1e-12


class BaseBroker:
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
        self.rejected: list[tuple[Order, str]] = []
        self._remaining: dict[int, float] = {}
        self._reserved_cash: dict[int, float] = {}
        self._reserved_btc: dict[int, float] = {}

    # --- 残高 ---
    @property
    def available_cash(self) -> float:
        return self.cash - sum(self._reserved_cash.values())

    @property
    def available_btc(self) -> float:
        return self.btc - sum(self._reserved_btc.values())

    def equity(self, price: float) -> float:
        """時価評価（手仕舞いコストを含まない）。"""
        return self.cash + self.btc * price

    def liquidation_value(self, price: float) -> float:
        """保有 BTC を成行で売った場合の価値（スリッページ・Taker 手数料込み）。"""
        return self.cash + self.btc * price * (1 - self.slippage) * (1 - self.fees.taker)

    # --- 注文 ---
    def submit(self, order: Order, ref_price: float) -> bool:
        """注文を受け付けて資金を拘束する。拒否したら False。

        ref_price は成行買いの拘束額の見積もりに使う直近価格。
        """
        amount = _floor(order.amount, 8)
        if amount < self.min_amount:
            return self._reject(order, "below_min_amount")
        if order.side is Side.BUY:
            price = order.price if order.type is OrderType.LIMIT else ref_price * (1 + self.slippage)
            need = amount * price * (1 + max(self.fees.taker, 0.0))
            if need > self.available_cash + EPS:
                return self._reject(order, "insufficient_jpy")
            self._reserved_cash[order.id] = need
        else:
            if amount > self.available_btc + EPS:
                return self._reject(order, "insufficient_btc")
            self._reserved_btc[order.id] = amount
        self._remaining[order.id] = amount
        self.open_orders.append(order)
        return True

    def cancel_all(self) -> None:
        for o in self.open_orders:
            self._release(o)
        self.open_orders = []

    def remaining(self, order: Order) -> float:
        return self._remaining.get(order.id, 0.0)

    def liquidate(self, ts: int, price: float) -> Fill | None:
        """保有 BTC を全量、成行で売ったものとして記帳する（期末評価・停止時用）。"""
        self.cancel_all()
        amount = _floor(self.btc, 8)
        if amount <= 0:
            return None
        px = price * (1 - self.slippage)
        fee = self.fees.fee(px * amount, "taker")
        self.cash += px * amount - fee
        self.btc -= amount
        fill = Fill(ts, 0, Side.SELL, px, amount, fee, "taker", "liquidation")
        self.fills.append(fill)
        return fill

    # --- 派生クラス用 ---
    def _fill(self, order: Order, ts: int, price: float, amount: float, liquidity: str) -> Fill | None:
        amount = _floor(min(amount, self._remaining[order.id]), 8)
        if amount <= 0:
            return None
        notional = price * amount
        fee = self.fees.fee(notional, liquidity)
        if order.side is Side.BUY:
            # 拘束分を解いてから実額で引き落とす
            reserved = self._reserved_cash.get(order.id, 0.0)
            if notional + fee > self.cash - (sum(self._reserved_cash.values()) - reserved) + EPS:
                self._reject(order, "insufficient_jpy_at_fill")
                self._drop(order)
                return None
            self.cash -= notional + fee
            self.btc += amount
            per_btc = reserved / self._remaining[order.id]
            self._reserved_cash[order.id] = max(reserved - per_btc * amount, 0.0)
        else:
            self.cash += notional - fee
            self.btc -= amount
            self._reserved_btc[order.id] = max(self._reserved_btc.get(order.id, 0.0) - amount, 0.0)
        self._remaining[order.id] -= amount
        fill = Fill(ts, order.id, order.side, price, amount, fee, liquidity, order.tag)
        self.fills.append(fill)
        if self._remaining[order.id] < self.min_amount:
            self._drop(order)
        return fill

    def _drop(self, order: Order) -> None:
        self._release(order)
        self.open_orders = [o for o in self.open_orders if o.id != order.id]

    def _release(self, order: Order) -> None:
        self._remaining.pop(order.id, None)
        self._reserved_cash.pop(order.id, None)
        self._reserved_btc.pop(order.id, None)

    def _reject(self, order: Order, reason: str) -> bool:
        self.rejected.append((order, reason))
        return False


class SimBroker(BaseBroker):
    def __init__(
        self,
        cash_jpy: float,
        fees: FeeSchedule,
        slippage: float = 0.0005,
        participation: float = 0.1,
        min_amount: float = MIN_AMOUNT_BTC,
    ) -> None:
        super().__init__(cash_jpy, fees, slippage, min_amount)
        self.participation = participation

    def process_bar(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        maker_capacity = bar.volume * self.participation
        for order in list(self.open_orders):
            fill = self._try_fill(order, bar, maker_capacity)
            if fill is not None:
                fills.append(fill)
                if fill.liquidity == "maker":
                    maker_capacity -= fill.amount
        self.cancel_all()  # 残りは失効
        return fills

    def _try_fill(self, order: Order, bar: Bar, maker_capacity: float) -> Fill | None:
        remaining = self.remaining(order)
        buy = order.side is Side.BUY
        taker_px = bar.open * (1 + self.slippage) if buy else bar.open * (1 - self.slippage)

        if order.type is OrderType.MARKET:
            return self._fill(order, bar.ts, taker_px, remaining, "taker")

        assert order.price is not None
        if (buy and taker_px <= order.price) or (not buy and taker_px >= order.price):
            return self._fill(order, bar.ts, taker_px, remaining, "taker")
        through = bar.low < order.price if buy else bar.high > order.price
        if through and maker_capacity > 0:
            return self._fill(order, bar.ts, order.price, min(remaining, maker_capacity), "maker")
        return None


def _floor(x: float, digits: int) -> float:
    m = 10**digits
    return int(x * m + 1e-9) / m
