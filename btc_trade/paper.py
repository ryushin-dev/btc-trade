"""ペーパートレード。

bitbank の確定済み1分足を定期的に取得し、バックテストと同じ Engine / SimBroker に流す。
実際の注文は一切出さない。約定・資産推移は log_dir に JSON Lines で記録する。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .broker import SimBroker
from .engine import Engine
from .exchange.public import BitbankPublic
from .fees import BITBANK_BTC_JPY, FeeSchedule
from .models import Bar
from .strategies import Strategy

BAR_MS = 60_000


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
    ) -> None:
        self.client = client
        self.pair = pair
        self.broker = SimBroker(cash_jpy, fees, slippage=slippage)
        self.engine = Engine(strategy, self.broker)
        self.warmup_bars = warmup_bars
        self.started = False
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

    def poll_once(self, now_ms: int | None = None) -> int:
        """新しく確定したバーを処理し、処理した本数を返す。"""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        closed = self._recent_closed_bars(now_ms)
        last = self.engine.last_ts
        new = [b for b in closed if last is None or b.ts > last]

        if not self.started:
            for b in new[-self.warmup_bars :]:
                self.engine.warmup(b)
            self.started = True
            self._log({"event": "start", "ts": now_ms, "warmup_bars": min(len(new), self.warmup_bars)})
            return 0

        for b in new:
            for f in self.engine.step(b):
                self._log({"event": "fill", **_fill_dict(f)})
            self._log(
                {
                    "event": "bar",
                    "ts": b.ts,
                    "close": b.close,
                    "cash": self.broker.cash,
                    "btc": self.broker.btc,
                    "equity": self.broker.equity(b.close),
                }
            )
        return len(new)

    def run(self, poll_sec: float = 10.0) -> None:
        print(f"paper trading started. log: {self.log_path}")
        while True:
            try:
                n = self.poll_once()
                if n:
                    print(
                        f"{datetime.now():%H:%M:%S} bars+{n} cash={self.broker.cash:,.0f} "
                        f"btc={self.broker.btc:.8f} fills={len(self.broker.fills)}"
                    )
            except KeyboardInterrupt:
                raise
            except Exception as e:  # ネットワーク断などで止まらないようにする
                self._log({"event": "error", "ts": int(time.time() * 1000), "error": repr(e)})
                print(f"error: {e!r}")
            time.sleep(poll_sec)

    def _log(self, record: dict) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _fill_dict(f) -> dict:
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
