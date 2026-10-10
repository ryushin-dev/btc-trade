"""共通データ型。"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

_order_ids = itertools.count(1)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class Bar:
    """ローソク足1本。ts はバー開始時刻（UNIXミリ秒）。"""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    side: Side
    type: OrderType
    amount: float  # BTC
    price: float | None = None  # 指値のときのみ（JPY）
    tag: str = ""
    id: int = field(default_factory=lambda: next(_order_ids))

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if self.type is OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("limit order requires a positive price")


@dataclass(frozen=True)
class Fill:
    ts: int
    order_id: int
    side: Side
    price: float
    amount: float
    fee_jpy: float  # 正なら支払い、負ならリベート（受け取り）
    liquidity: str  # "maker" | "taker"
    tag: str = ""
