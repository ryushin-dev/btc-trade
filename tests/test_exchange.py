import hashlib
import hmac
import urllib.error
from datetime import date, datetime, timedelta, timezone

import pytest

from btc_trade.data import JST, fetch_range, synthetic_bars
from btc_trade.exchange.private import (
    BitbankPrivate,
    LiveTradingDisabled,
    OrderStateUnknown,
    load_env_file,
    next_nonce,
    sign,
)
from btc_trade.exchange.public import BitbankError, BitbankPublic, parse_ohlcv
from btc_trade.fees import FeeSchedule
from btc_trade.models import Bar, Order, OrderType, Side
from btc_trade.paper import PaperBroker, PaperTrader
from btc_trade.strategies import Strategy, build_strategy

FEES = FeeSchedule(maker=-0.0002, taker=0.0012)


def test_parse_ohlcv():
    b = parse_ohlcv(["10000000", "10010000", "9990000", "10005000", "1.2345", 1700000000000])
    assert b.ts == 1700000000000 and b.close == 10_005_000.0


def test_sign_matches_hmac_sha256():
    assert sign("secret", "123/v1/user/assets") == hmac.new(
        b"secret", b"123/v1/user/assets", hashlib.sha256
    ).hexdigest()


def test_nonce_strictly_increases_within_same_millisecond():
    values = [next_nonce() for _ in range(1000)]
    assert all(b > a for a, b in zip(values, values[1:]))


def test_orders_blocked_without_live_flag(monkeypatch):
    monkeypatch.delenv("BTC_TRADE_ENABLE_LIVE", raising=False)
    c = BitbankPrivate("k", "s", live=True)
    with pytest.raises(LiveTradingDisabled):
        c.create_order("btc_jpy", "buy", "limit", "0.0001", "1")


def test_orders_blocked_when_env_set_but_live_false(monkeypatch):
    monkeypatch.setenv("BTC_TRADE_ENABLE_LIVE", "1")
    c = BitbankPrivate("k", "s", live=False)
    with pytest.raises(LiveTradingDisabled):
        c.cancel_order("btc_jpy", 1)


def test_unknown_order_result_halts_until_reconciled(monkeypatch):
    monkeypatch.setenv("BTC_TRADE_ENABLE_LIVE", "1")
    c = BitbankPrivate("k", "s", live=True)
    calls = []

    def timeout(method, path, params=None):
        calls.append(path)
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(c, "_request", timeout)
    with pytest.raises(OrderStateUnknown):
        c.create_order("btc_jpy", "buy", "limit", "0.0001", "1")
    assert c.halted
    with pytest.raises(OrderStateUnknown):  # 停止中は送信すらしない
        c.create_order("btc_jpy", "buy", "limit", "0.0001", "1")
    assert len(calls) == 1

    monkeypatch.setattr(c, "_request", lambda m, p, params=None: {"ok": p})
    c.reconcile("btc_jpy")
    c.resume()
    assert c.create_order("btc_jpy", "buy", "limit", "0.0001", "1") == {"ok": "/v1/user/spot/order"}


def test_exchange_rejection_does_not_halt(monkeypatch):
    monkeypatch.setenv("BTC_TRADE_ENABLE_LIVE", "1")
    c = BitbankPrivate("k", "s", live=True)

    def rejected(method, path, params=None):
        raise BitbankError(60001, path)

    monkeypatch.setattr(c, "_request", rejected)
    with pytest.raises(BitbankError):
        c.create_order("btc_jpy", "buy", "limit", "0.0001", "1")
    assert not c.halted


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nBITBANK_API_KEY=from_file\nBITBANK_API_SECRET='sec'\n")
    monkeypatch.delenv("BITBANK_API_KEY", raising=False)
    monkeypatch.setenv("BITBANK_API_SECRET", "already")
    load_env_file(env)
    import os

    assert os.environ["BITBANK_API_KEY"] == "from_file"
    assert os.environ["BITBANK_API_SECRET"] == "already"


class FakePublic(BitbankPublic):
    def __init__(self, bars, trades=None, depth=None):
        super().__init__()
        self.bars = bars
        self.trades = trades or []
        self.book = depth or {"asks": [], "bids": []}
        self.requested = []

    def candles(self, pair, candle_type, day: date):
        self.requested.append(day)
        return self.bars

    def transactions(self, pair="btc_jpy", day=None):
        return self.trades

    def depth(self, pair="btc_jpy"):
        return self.book


def _ms(dt):
    return int(dt.timestamp() * 1000)


def test_fetch_range_filters_yearly_candles_to_requested_period():
    sep1 = datetime(2026, 9, 1, tzinfo=JST)
    bars = [Bar(_ms(sep1 + timedelta(hours=4 * i)), 1, 1, 1, 1, 1) for i in range(-30, 200)]
    client = FakePublic(bars)
    got = fetch_range(client, "btc_jpy", "4hour", date(2026, 9, 1), date(2026, 9, 10), pause=0,
                      now_ms=_ms(datetime(2026, 10, 1, tzinfo=JST)))
    assert got[0].ts == _ms(sep1)
    assert got[-1].ts == _ms(datetime(2026, 9, 10, 20, tzinfo=JST))
    assert len(client.requested) == len(set(d.year for d in client.requested))  # 同じ年は1回だけ


def test_fetch_range_drops_unclosed_bars():
    t0 = datetime(2026, 10, 10, 0, 0, tzinfo=JST)
    bars = [Bar(_ms(t0) + i * 60_000, 1, 1, 1, 1, 1) for i in range(10)]
    got = fetch_range(FakePublic(bars), "btc_jpy", "1min", date(2026, 10, 10), date(2026, 10, 10), pause=0,
                      now_ms=_ms(t0) + 5 * 60_000 + 30_000)
    assert len(got) == 5


# --- ペーパートレード ---

BOOK = {"asks": [["10001000", "0.002"], ["10002000", "0.01"]], "bids": [["9999000", "0.01"]]}


def test_paper_market_order_walks_the_book():
    b = PaperBroker(1_000_000, FEES)
    fills = b.place(Order(Side.BUY, OrderType.MARKET, 0.005), 1000, BOOK)
    assert [(f.price, f.amount) for f in fills] == [(10_001_000, 0.002), (10_002_000, 0.003)]
    assert all(f.liquidity == "taker" for f in fills)


def test_paper_limit_uses_only_trades_after_placement_and_caps_by_volume():
    b = PaperBroker(1_000_000, FEES, participation=0.1)
    o = Order(Side.BUY, OrderType.LIMIT, 0.01, price=9_990_000)
    assert b.place(o, 1000, BOOK) == []  # 板の売りは指値より上なので即時約定しない
    trades = [
        {"transaction_id": 1, "price": "9980000", "amount": "1.0", "executed_at": 999},  # 発注前
        {"transaction_id": 2, "price": "9990000", "amount": "1.0", "executed_at": 1500},  # タッチのみ
        {"transaction_id": 3, "price": "9989000", "amount": "0.03", "executed_at": 1600},
    ]
    [f] = b.on_trades(trades)
    assert f.ts == 1600 and f.price == 9_990_000 and f.liquidity == "maker"
    assert f.amount == pytest.approx(0.003)


class AlwaysBuy(Strategy):
    name = "always_buy"

    def on_bar(self, bar, ctx):
        if ctx.flat:
            return [Order(Side.BUY, OrderType.MARKET, 0.001)]
        return []


def test_paper_does_not_trade_on_bars_missed_during_outage(tmp_path):
    bars = synthetic_bars(500, seed=3)
    client = FakePublic(bars[:400], depth=BOOK)
    t = PaperTrader(AlwaysBuy(), client, log_dir=tmp_path, warmup_bars=100)
    assert t.poll_once(bars[399].ts + 60_000) == 0  # ウォームアップのみ

    # 50本ぶん通信が途絶えたあと、最新足が確定して5分後に復帰 → 注文しない
    client.bars = bars[:450]
    assert t.poll_once(bars[449].ts + 60_000 + 300_000) == 0
    assert t.broker.fills == []

    # 次の足を確定直後に処理 → ここで初めて発注し、今の板で約定
    client.bars = bars[:451]
    assert t.poll_once(bars[450].ts + 60_000 + 5_000) == 1
    [f] = t.broker.fills
    assert f.price == 10_001_000

    rep = t.report()
    assert t.broker.btc == 0 and t.broker.fills[-1].tag == "liquidation"
    assert rep.end_equity < 100_000  # 手仕舞いコスト込みで評価される
    assert t.log_path.exists()
