from __future__ import annotations

from collections.abc import Iterable

from .broker import SimBroker
from .engine import Engine
from .fees import BITBANK_BTC_JPY, FeeSchedule
from .metrics import Report, build_report
from .models import Bar, Fill
from .strategies import Strategy


def run_backtest(
    bars: Iterable[Bar],
    strategy: Strategy,
    cash_jpy: float = 100_000,
    fees: FeeSchedule = BITBANK_BTC_JPY,
    slippage: float = 0.0005,
) -> tuple[Report, list[Fill], list[float]]:
    bars = list(bars)
    if len(bars) < 2:
        raise ValueError("need at least 2 bars")
    broker = SimBroker(cash_jpy, fees, slippage=slippage)
    engine = Engine(strategy, broker)
    equity = [cash_jpy]
    for bar in bars:
        engine.step(bar)
        equity.append(broker.equity(bar.close))
    # 保有が残っていれば、成行で手仕舞った場合の価値（Taker 手数料・スリッページ込み）で評価する
    last = bars[-1].close * (1 - slippage)
    equity[-1] = broker.cash + broker.btc * last * (1 - fees.taker)
    report = build_report(equity, broker.fills, bars[0].open, bars[-1].close)
    return report, broker.fills, equity
