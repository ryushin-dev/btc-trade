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
    assert [f.tag for f in fills] == ["", "liquidation"]  # 期末の清算売りも記録される
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


def test_report_counts_liquidation_fee_and_round_trip():
    bars = [Bar(i * 60_000, 1e7, 1e7, 1e7, 1e7, 1) for i in range(5)]
    report, fills, equity = run_backtest(bars, BuyOnceAtBar(at=1), cash_jpy=1_000_000, slippage=0)
    buy_fee, sell_fee = fills[0].fee_jpy, fills[1].fee_jpy
    assert report.fees_paid == pytest.approx(buy_fee + sell_fee)
    assert report.round_trips == 1 and report.win_rate_pct == 0
    assert report.net_pnl == pytest.approx(-(buy_fee + sell_fee))
    assert equity[-1] == pytest.approx(1_000_000 - buy_fee - sell_fee)


def test_daily_loss_limit_blocks_new_buys_for_the_rest_of_the_day():
    class BuyEveryBar(Strategy):
        name = "buyer"

        def on_bar(self, bar, ctx):
            orders = []
            if not ctx.flat:
                orders.append(Order(Side.SELL, OrderType.MARKET, ctx.available_btc))
            if ctx.flat:
                orders.append(Order(Side.BUY, OrderType.MARKET, ctx.available_cash * 0.9 / (bar.close * 1.01)))
            return orders

    # 価格が毎本 1% ずつ下がる
    bars = [Bar(i * 60_000, 1e7 * 0.99**i, 1e7 * 0.99**i, 1e7 * 0.99**i, 1e7 * 0.99**i, 1) for i in range(60)]
    report, fills, equity = run_backtest(bars, BuyEveryBar(), cash_jpy=100_000, max_daily_loss=0.03)
    assert min(equity) > 100_000 * (1 - 0.03 - 0.02)  # 3% を超えたあとは買わない
    buys = [f for f in fills if f.side is Side.BUY]
    assert len(buys) < 10


def test_maker_band_tracks_weighted_entry_and_clears_only_when_flat():
    from btc_trade.models import Fill
    from btc_trade.strategies.maker_band import MakerBand

    s = MakerBand()
    s.on_fill(Fill(0, 1, Side.BUY, 100.0, 1.0, 0, "maker"))
    s.on_fill(Fill(0, 2, Side.BUY, 130.0, 0.5, 0, "maker"))
    assert s.avg_price == pytest.approx(110.0)
    s.on_fill(Fill(0, 3, Side.SELL, 120.0, 1.0, 0, "maker"))  # 一部売却
    assert s.position == pytest.approx(0.5) and s.avg_price == pytest.approx(110.0)
    ctx = Context(cash=0, btc=0.5, available_cash=0, available_btc=0.5)
    [o] = s.on_bar(Bar(0, 111, 111, 111, 111, 1), ctx)
    assert o.side is Side.SELL  # 残りがあるうちは買いに進まない
    s.on_fill(Fill(0, 4, Side.SELL, 120.0, 0.5, 0, "maker"))
    assert s.position == 0 and s.avg_price == 0


def test_validate_reports_folds_and_stress():
    from btc_trade.validate import validate

    res = validate(synthetic_bars(4000, seed=2), lambda: build_strategy("maker_band"), folds=4)
    assert len(res.folds) == 4 and len(res.stress) == 5
    assert "判定" in res.format()
