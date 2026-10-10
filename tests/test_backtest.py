import pytest

from btc_trade.backtest import run_backtest
from btc_trade.data import load_csv, save_csv, synthetic_bars
from btc_trade.models import Bar, Order, OrderType, Side
from btc_trade.strategies import Context, Strategy, build_strategy


class BuyOnceAtBar(Strategy):
    name = "probe"

    def __init__(self, at: int):
        self.at = at
        self.seen = 0

    def on_bar(self, bar, ctx: Context):
        self.seen += 1
        if self.seen == self.at:
            return [Order(Side.BUY, OrderType.MARKET, 0.001)]
        return []


def test_orders_fill_on_next_bar_open_no_lookahead():
    bars = [Bar(i, 100.0 * (i + 1), 100.0 * (i + 1), 100.0 * (i + 1), 100.0 * (i + 1), 1) for i in range(5)]
    bars = [Bar(b.ts, b.open * 1e5, b.high * 1e5, b.low * 1e5, b.close * 1e5, 1) for b in bars]
    report, fills, _ = run_backtest(bars, BuyOnceAtBar(at=2), cash_jpy=1_000_000, slippage=0)
    assert len(fills) == 1
    assert fills[0].ts == bars[2].ts  # 2本目の確定で注文 → 3本目で約定
    assert fills[0].price == bars[2].open


@pytest.mark.parametrize("name", ["sma_cross", "maker_band"])
def test_strategies_run_and_never_go_negative(name):
    bars = synthetic_bars(3000, seed=1)
    report, fills, equity = run_backtest(bars, build_strategy(name), cash_jpy=100_000)
    assert report.fills == len(fills)
    assert min(equity) > 0
    assert all(f.amount >= 0.0001 for f in fills)


def test_flat_market_round_trips_lose_money():
    """値動きゼロなら、Taker で売買するほど手数料ぶん必ず減る。"""
    bars = [Bar(i * 60_000, 1e7, 1e7, 1e7, 1e7, 1) for i in range(300)]

    class PingPong(Strategy):
        name = "pingpong"

        def on_bar(self, bar, ctx):
            if ctx.btc > 0:
                return [Order(Side.SELL, OrderType.MARKET, ctx.btc)]
            return [Order(Side.BUY, OrderType.MARKET, ctx.cash * 0.9 / bar.close)]

    report, _, _ = run_backtest(bars, PingPong(), cash_jpy=100_000)
    assert report.net_pnl < 0
    assert not report.profitable


def test_csv_round_trip(tmp_path):
    bars = synthetic_bars(10)
    path = tmp_path / "bars.csv"
    save_csv(bars, path)
    assert load_csv(path) == bars
