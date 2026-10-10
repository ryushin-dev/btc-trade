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
    participation: float = 0.1,
    max_daily_loss: float = 0.03,
) -> tuple[Report, list[Fill], list[float]]:
    """equity は各バー終値時点の清算価値（手仕舞いコスト込み）。

    期末に残った保有は成行売却の Fill（tag="liquidation"）として記帳し、手数料・勝率にも含める。
    """
    bars = list(bars)
    if len(bars) < 2:
        raise ValueError("need at least 2 bars")
    broker = SimBroker(cash_jpy, fees, slippage=slippage, participation=participation)
    engine = Engine(strategy, broker, max_daily_loss=max_daily_loss)
    equity = [cash_jpy]
    for bar in bars:
        engine.step(bar)
        equity.append(broker.liquidation_value(bar.close))
    broker.liquidate(bars[-1].ts, bars[-1].close)
    equity[-1] = broker.cash
    report = build_report(equity, broker.fills, bars[0].open, bars[-1].close, len(broker.rejected))
    return report, broker.fills, equity
