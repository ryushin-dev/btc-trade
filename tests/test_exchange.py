import hashlib
import hmac
from datetime import date

import pytest

from btc_trade.exchange.private import BitbankPrivate, LiveTradingDisabled, sign
from btc_trade.exchange.public import BitbankPublic, parse_ohlcv
from btc_trade.paper import PaperTrader
from btc_trade.strategies import build_strategy


def test_parse_ohlcv():
    b = parse_ohlcv(["10000000", "10010000", "9990000", "10005000", "1.2345", 1700000000000])
    assert b.ts == 1700000000000 and b.close == 10_005_000.0


def test_sign_matches_hmac_sha256():
    assert sign("secret", "123/v1/user/assets") == hmac.new(
        b"secret", b"123/v1/user/assets", hashlib.sha256
    ).hexdigest()


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


class FakePublic(BitbankPublic):
    def __init__(self, bars):
        super().__init__()
        self.bars = bars

    def candles(self, pair, candle_type, day: date):
        return self.bars


def test_paper_trader_processes_only_closed_new_bars(tmp_path):
    from btc_trade.data import synthetic_bars

    bars = synthetic_bars(500, seed=3)
    client = FakePublic(bars[:400])
    t = PaperTrader(build_strategy("maker_band"), client, log_dir=tmp_path, warmup_bars=100)

    now = bars[399].ts + 60_000
    assert t.poll_once(now) == 0  # ウォームアップのみ
    client.bars = bars[:450]
    assert t.poll_once(bars[449].ts + 30_000) == 49  # 最後の1本は未確定
    assert t.poll_once(bars[449].ts + 60_000) == 1
    assert t.log_path.exists()
