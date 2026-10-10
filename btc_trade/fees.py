"""取引手数料の定義。

bitbank の手数料は改定されることがあるため、運用前に必ず公式ページで確認すること。
https://bitbank.cc/docs/fees/
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSchedule:
    maker: float  # 約定代金に対する率。負ならリベート
    taker: float

    def fee(self, notional_jpy: float, liquidity: str) -> float:
        rate = self.maker if liquidity == "maker" else self.taker
        return notional_jpy * rate


# bitbank 現物 BTC/JPY（Maker -0.02% / Taker 0.12%）
BITBANK_BTC_JPY = FeeSchedule(maker=-0.0002, taker=0.0012)
