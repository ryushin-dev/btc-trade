import pytest

from btc_trade.broker import SimBroker
from btc_trade.fees import FeeSchedule
from btc_trade.models import Bar, Order, OrderType, Side

FEES = FeeSchedule(maker=-0.0002, taker=0.0012)
P = 10_000_000


def bar(o, h, l, c, ts=0, v=10.0):
    return Bar(ts, o, h, l, c, v)


def flat(ts=0, v=10.0):
    return bar(P, P, P, P, ts, v)


def test_market_buy_pays_slippage_and_taker_fee():
    b = SimBroker(1_000_000, FEES, slippage=0.001)
    assert b.submit(Order(Side.BUY, OrderType.MARKET, 0.01), ref_price=P)
    [f] = b.process_bar(bar(P, 10_100_000, 9_900_000, P))
    assert f.price == pytest.approx(10_010_000)
    assert f.liquidity == "taker"
    assert f.fee_jpy == pytest.approx(10_010_000 * 0.01 * 0.0012)
    assert b.cash == pytest.approx(1_000_000 - 100_100 - f.fee_jpy)
    assert b.btc == pytest.approx(0.01)
    assert b.available_cash == pytest.approx(b.cash)  # 拘束は解除済み


def test_limit_buy_needs_price_to_trade_through():
    b = SimBroker(1_000_000, FEES)
    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_900_000), ref_price=P)
    assert b.process_bar(bar(P, 10_050_000, 9_900_000, P)) == []  # タッチのみ
    assert b.open_orders == [] and b.available_cash == b.cash  # 1本で失効し拘束も解除

    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_900_000), ref_price=P)
    [f] = b.process_bar(bar(P, 10_050_000, 9_899_999, P))
    assert f.price == 9_900_000 and f.liquidity == "maker"
    assert f.fee_jpy < 0  # リベート


def test_maker_fill_is_capped_by_bar_volume():
    b = SimBroker(1_000_000, FEES, participation=0.1)
    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_900_000), ref_price=P)
    assert b.process_bar(bar(P, P, 9_800_000, P, v=0.0)) == []  # 出来高ゼロでは約定しない

    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_900_000), ref_price=P)
    [f] = b.process_bar(bar(P, P, 9_800_000, P, ts=1, v=0.05))
    assert f.amount == pytest.approx(0.005)  # 出来高 0.05 の 10% までの部分約定
    assert b.open_orders == []  # 残りは失効


def test_marketable_limit_is_taker_with_slippage_and_never_beyond_limit():
    b = SimBroker(1_000_000, FEES, slippage=0.001)
    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=P), ref_price=P)
    [f] = b.process_bar(bar(9_950_000, 9_960_000, 9_940_000, 9_950_000))
    assert f.liquidity == "taker" and f.price == pytest.approx(9_950_000 * 1.001)

    # 始値+スリッページが指値を超えるなら即時約定させない（突き抜けがあれば Maker）
    b.submit(Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_995_000), ref_price=P)
    [g] = b.process_bar(bar(9_990_000, 9_990_000, 9_980_000, 9_990_000, ts=1))
    assert g.liquidity == "maker" and g.price == 9_995_000


def test_orders_beyond_available_balance_are_rejected_not_shrunk():
    b = SimBroker(100_000, FEES, slippage=0)
    assert not b.submit(Order(Side.BUY, OrderType.MARKET, 1.0), ref_price=P)
    assert b.submit(Order(Side.BUY, OrderType.LIMIT, 0.007, price=9_000_000), ref_price=P)
    # 1本目で資金が拘束されているので、2本目は残高不足で拒否
    assert not b.submit(Order(Side.BUY, OrderType.LIMIT, 0.007, price=9_000_000), ref_price=P)
    assert [r for _, r in b.rejected] == ["insufficient_jpy", "insufficient_jpy"]
    assert not b.submit(Order(Side.SELL, OrderType.MARKET, 0.001), ref_price=P)
    assert b.rejected[-1][1] == "insufficient_btc"


def test_market_buy_rejected_when_price_jumps_past_cash():
    b = SimBroker(100_500, FEES, slippage=0)
    assert b.submit(Order(Side.BUY, OrderType.MARKET, 0.01), ref_price=P)
    assert b.process_bar(bar(P * 1.1, P * 1.1, P * 1.1, P * 1.1)) == []
    assert b.rejected[-1][1] == "insufficient_jpy_at_fill"
    assert b.cash == 100_500 and b.btc == 0


def test_round_trip_at_same_price_loses_fees():
    b = SimBroker(1_000_000, FEES, slippage=0)
    b.submit(Order(Side.BUY, OrderType.MARKET, 0.05), ref_price=P)
    b.process_bar(flat())
    b.submit(Order(Side.SELL, OrderType.MARKET, 0.05), ref_price=P)
    b.process_bar(flat(ts=1))
    assert b.cash == pytest.approx(1_000_000 - 2 * 500_000 * 0.0012)


def test_too_small_order_is_rejected():
    b = SimBroker(1_000_000, FEES)
    assert not b.submit(Order(Side.BUY, OrderType.MARKET, 0.00005), ref_price=P)
    assert b.open_orders == []


def test_liquidate_records_taker_fill_with_costs():
    b = SimBroker(1_000_000, FEES, slippage=0.001)
    b.submit(Order(Side.BUY, OrderType.MARKET, 0.01), ref_price=P)
    b.process_bar(flat())
    expected = b.liquidation_value(P)
    f = b.liquidate(1, P)
    assert f.tag == "liquidation" and f.liquidity == "taker" and f.fee_jpy > 0
    assert b.btc == 0 and b.cash == pytest.approx(expected)
