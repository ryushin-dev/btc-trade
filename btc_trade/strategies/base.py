from __future__ import annotations

from dataclasses import dataclass

from ..models import Bar, Fill, Order


@dataclass(frozen=True)
class Context:
    """戦略に渡す口座状態。"""

    cash: float
    btc: float
    available_cash: float
    available_btc: float
    min_amount: float = 0.0001

    @property
    def flat(self) -> bool:
        """売れる最小数量未満しか持っていなければノーポジ扱い。"""
        return self.btc < self.min_amount


class Strategy:
    """バー確定ごとに呼ばれ、次のバーで執行される注文を返す。

    未約定・部分約定の残りはそのバーの終わりで失効するので、指値は毎バー出し直す。
    """

    name = "base"

    def on_bar(self, bar: Bar, ctx: Context) -> list[Order]:
        raise NotImplementedError

    def on_fill(self, fill: Fill) -> None:
        pass
