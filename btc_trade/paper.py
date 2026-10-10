"""ペーパートレード（実注文は一切出さない）。

バックテストとの違い（実運用に近づけるための設計）:
- 戦略の判断は確定した1分足で行うが、注文は「判断した時刻」に置いたものとして扱う。
- 成行・即時約定する指値は、発注時点で取得した実際の板（depth）を上から食って約定させる。
  板が薄ければ部分約定し、指値の価格は超えない。
- 板に残った指値は、発注時刻より後の実際の約定履歴で、価格が指値を突き抜けたときだけ約定する。
  約定量は各約定の数量 × participation が上限。
- 通信断やプログラム停止から復帰したとき、その間の足は指標の更新にだけ使い、注文は出さない。
- 資産は清算価値（最良買い気配で成行売りした場合、Taker 手数料込み）で記録し、停止時にレポートを出す。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .broker import BaseBroker
from .engine import Engine
from .exchange.public import BitbankPublic
from .fees import BITBANK_BTC_JPY, FeeSchedule
from .metrics import Report, build_report
from .models import Bar, Fill, Order, OrderType, Side
from .strategies import Strategy

BAR_MS = 60_000


class PaperBroker(BaseBroker):
    def __init__(
        self,
        cash_jpy: float,
        fees: FeeSchedule,
        slippage: float = 0.0005,
        participation: float = 0.1,
    ) -> None:
        super().__init__(cash_jpy, fees, slippage)
        self.participation = participation
        self._placed_at: dict[int, int] = {}

    def place(self, order: Order, now_ms: int, depth: dict) -> list[Fill]:
        asks = [(float(p), float(a)) for p, a in depth.get("asks", [])]
        bids = [(float(p), float(a)) for p, a in depth.get("bids", [])]
        buy = order.side is Side.BUY
        book = asks if buy else bids
        if not book:
            self._reject(order, "empty_book")
            return []
        if not self.submit(order, ref_price=book[0][0]):
            return []
        self._placed_at[order.id] = now_ms

        fills: list[Fill] = []
        for price, size in book:
            if self.remaining(order) <= 0:
                break
            if order.type is OrderType.LIMIT:
                assert order.price is not None
                if (buy and price > order.price) or (not buy and price < order.price):
                    break
            f = self._fill(order, now_ms, price, size, "taker")
            if f is None:
                break
            fills.append(f)
        if order.type is OrderType.MARKET and self.remaining(order) > 0:
            self._drop(order)  # 板が尽きた成行の残りは取り消し
        return fills

    def on_trades(self, trades: list[dict]) -> list[Fill]:
        fills: list[Fill] = []
        for t in sorted(trades, key=lambda t: (t["executed_at"], t["transaction_id"])):
            price, ts = float(t["price"]), int(t["executed_at"])
            capacity = float(t["amount"]) * self.participation
            for order in list(self.open_orders):
                if capacity <= 0:
                    break
                if order.type is not OrderType.LIMIT or ts <= self._placed_at.get(order.id, ts):
                    continue
                assert order.price is not None
                through = price < order.price if order.side is Side.BUY else price > order.price
                if not through:
                    continue
                f = self._fill(order, ts, order.price, min(self.remaining(order), capacity), "maker")
                if f is not None:
                    fills.append(f)
                    capacity -= f.amount
        return fills

    def cancel_all(self) -> None:
        super().cancel_all()
        self._placed_at.clear()


class PaperTrader:
    def __init__(
        self,
        strategy: Strategy,
        client: BitbankPublic,
        cash_jpy: float = 100_000,
        fees: FeeSchedule = BITBANK_BTC_JPY,
        slippage: float = 0.0005,
        pair: str = "btc_jpy",
        log_dir: str | Path = "runs/paper",
        warmup_bars: int = 300,
        participation: float = 0.1,
        max_daily_loss: float = 0.03,
        max_bar_lag_ms: int = 90_000,
    ) -> None:
        self.client = client
        self.pair = pair
        self.cash_jpy = cash_jpy
        self.broker = PaperBroker(cash_jpy, fees, slippage, participation)
        self.engine = Engine(strategy, self.broker, max_daily_loss=max_daily_loss)
        self.warmup_bars = warmup_bars
        self.max_bar_lag_ms = max_bar_lag_ms
        self.started = False
        self.last_trade_id: int | None = None
        self.equity: list[float] = [cash_jpy]
        self.first_price: float | None = None
        self.last_bid: float | None = None
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.log_path = self.log_dir / f"{strategy.name}_{stamp}.jsonl"

    def _recent_closed_bars(self, now_ms: int) -> list[Bar]:
        now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        bars: dict[int, Bar] = {}
        for d in (now - timedelta(days=1), now):
            for b in self.client.candles(self.pair, "1min", d.date()):
                bars[b.ts] = b
        return [bars[k] for k in sorted(bars) if k + BAR_MS <= now_ms]

    def _new_trades(self) -> list[dict]:
        trades = self.client.transactions(self.pair)
        if self.last_trade_id is None:
            new = []  # 起動前の約定は使わない
        else:
            new = [t for t in trades if t["transaction_id"] > self.last_trade_id]
        if trades:
            self.last_trade_id = max(self.last_trade_id or 0, max(t["transaction_id"] for t in trades))
        return new

    def poll_once(self, now_ms: int | None = None) -> int:
        """1回分の処理。注文判断に使った足の本数（0 か 1）を返す。"""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

        # 1) 前回以降の実際の約定で、板に置いてある指値を約定判定
        fills = self.broker.on_trades(self._new_trades())
        self._record_fills(fills)

        # 2) 新しく確定した足
        closed = self._recent_closed_bars(now_ms)
        last = self.engine.last_ts
        new = [b for b in closed if last is None or b.ts > last]
        if not self.started:
            for b in new[-self.warmup_bars :]:
                self.engine.warmup(b)
            self.started = True
            self._log({"event": "start", "ts": now_ms, "warmup_bars": min(len(new), self.warmup_bars)})
            return 0
        if not new:
            return 0

        self.broker.cancel_all()  # 前の足で置いた注文は失効
        for b in new[:-1]:
            self.engine.warmup(b)
        latest = new[-1]
        lag = now_ms - (latest.ts + BAR_MS)
        if len(new) > 1 or lag > self.max_bar_lag_ms:
            self._log({"event": "gap", "ts": now_ms, "skipped_bars": len(new), "lag_ms": lag})
        if lag > self.max_bar_lag_ms:
            self.engine.warmup(latest)
            return 0

        # 3) 判断して、今の板に対して発注
        orders = self.engine.decide(latest)
        depth = self.client.depth(self.pair)
        for order in orders:
            fills = self.broker.place(order, now_ms, depth)
            self._record_fills(fills)
            self._log({"event": "order", "ts": now_ms, "side": order.side.value, "type": order.type.value,
                       "amount": order.amount, "price": order.price, "tag": order.tag})
        self._mark(latest, depth)
        return 1

    def _record_fills(self, fills: list[Fill]) -> None:
        self.engine.notify_fills(fills)
        for f in fills:
            self._log({"event": "fill", **_fill_dict(f)})

    def _mark(self, bar: Bar, depth: dict) -> None:
        bids = depth.get("bids") or []
        self.last_bid = float(bids[0][0]) if bids else bar.close
        if self.first_price is None:
            self.first_price = bar.close
        value = self.broker.liquidation_value(self.last_bid)
        self.equity.append(value)
        self._log({"event": "bar", "ts": bar.ts, "close": bar.close, "bid": self.last_bid,
                   "cash": self.broker.cash, "btc": self.broker.btc, "liquidation_value": value})

    def report(self) -> Report:
        """保有を最良買い気配で成行売りしたものとして清算し、成績を返す。"""
        price = self.last_bid or self.first_price or 0.0
        self.broker.liquidate(int(time.time() * 1000), price)
        equity = self.equity + [self.broker.cash]
        rep = build_report(equity, self.broker.fills, self.first_price or price, price, len(self.broker.rejected))
        self._log({"event": "report", **rep.to_dict()})
        return rep

    def run(self, poll_sec: float = 10.0) -> None:
        print(f"paper trading started. log: {self.log_path}")
        try:
            while True:
                try:
                    if self.poll_once():
                        print(
                            f"{datetime.now():%H:%M:%S} cash={self.broker.cash:,.0f} btc={self.broker.btc:.8f} "
                            f"清算価値={self.equity[-1]:,.0f} fills={len(self.broker.fills)}"
                        )
                except Exception as e:  # ネットワーク断などで止まらないようにする
                    self._log({"event": "error", "ts": int(time.time() * 1000), "error": repr(e)})
                    print(f"error: {e!r}")
                time.sleep(poll_sec)
        except KeyboardInterrupt:
            print("\n" + self.report().format())

    def _log(self, record: dict) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _fill_dict(f: Fill) -> dict:
    return {
        "ts": f.ts,
        "order_id": f.order_id,
        "side": f.side.value,
        "price": f.price,
        "amount": f.amount,
        "fee_jpy": f.fee_jpy,
        "liquidity": f.liquidity,
        "tag": f.tag,
    }
