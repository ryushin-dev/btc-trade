import pytest

from btc_trade.broker import SimBroker
from btc_trade.fees import FeeSchedule
from btc_trade.models import Bar, Order, OrderType, Side

FEES = FeeSchedule(maker=-0.0002, taker=0.0012)


def bar(o, h, l, c, ts=0):
    return Bar(ts, o, h, l, c, 1.0)


def test_market_buy_pays_slippage_and_taker_fee():
    b = SimBroker(1_000_000, FEES, slippage=0.001)
    b.submit(Order(Side.BUY, OrderType.MARKET, 0.01))
    [f] = b.process_bar(bar(10_000_000, 10_100_000, 9_900_000, 10_000_000))
    assert f.price == pytest.approx(10_010_000)
    assert f.liquidity == "taker"
    assert f.fee_jpy == pytest.approx(10_010_000 * 0.01 * 0.0012)
    assert b.cash == pytest.approx(1_000_000 - 100_100 - f.fee_jpy)
    assert b.btc == pytest.approx(0.01)


def test_limit_buy_needs_price_to_trade_through():
    b = SimBroker(1_000_000, FEES)
    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_900_000))
    assert b.process_bar(bar(10_000_000, 10_050_000, 9_900_000, 10_000_000)) == []  # タッチのみ
    assert b.open_orders == []  # 1本で失効

    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_900_000))
    [f] = b.process_bar(bar(10_000_000, 10_050_000, 9_899_999, 10_000_000))
    assert f.price == 9_900_000 and f.liquidity == "maker"
    assert f.fee_jpy < 0  # リベート


def test_limit_buy_marketable_at_open_is_taker():
    b = SimBroker(1_000_000, FEES)
    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=10_000_000))
    [f] = b.process_bar(bar(9_950_000, 9_960_000, 9_900_000, 9_950_000))
    assert f.liquidity == "taker" and f.price == 9_950_000


def test_buy_is_capped_by_cash_and_sell_by_holdings():
    b = SimBroker(100_000, FEES, slippage=0)
    b.submit(Order(Side.BUY, OrderType.MARKET, 1.0))
    [f] = b.process_bar(bar(10_000_000, 10_000_000, 10_000_000, 10_000_000))
    assert b.cash >= 0
    assert f.amount < 0.01
    b.submit(Order(Side.SELL, OrderType.MARKET, 5.0))
    [s] = b.process_bar(bar(10_000_000, 10_000_000, 10_000_000, 10_000_000, ts=1))
    assert s.amount == pytest.approx(f.amount)
    assert b.btc == pytest.approx(0)


def test_round_trip_at_same_price_loses_fees():
    b = SimBroker(1_000_000, FEES, slippage=0)
    flat = bar(10_000_000, 10_000_000, 10_000_000, 10_000_000)
    b.submit(Order(Side.BUY, OrderType.MARKET, 0.05))
    b.process_bar(flat)
    b.submit(Order(Side.SELL, OrderType.MARKET, 0.05))
    b.process_bar(flat)
    assert b.cash == pytest.approx(1_000_000 - 2 * 500_000 * 0.0012)


def test_too_small_order_is_ignored():
    b = SimBroker(1_000_000, FEES)
    b.submit(Order(Side.BUY, OrderType.MARKET, 0.00005))
    assert b.open_orders == []
