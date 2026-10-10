from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Bar, Fill, Order


@dataclass
class Context:
    """戦略に渡す口座状態（読み取り専用として扱う）。"""

    cash: float
    btc: float
    open_orders: list[Order] = field(default_factory=list)


class Strategy:
    """バー確定ごとに呼ばれ、次のバーで執行される注文を返す。

    on_bar で返した注文は、呼び出し時点で残っている未約定注文をすべて取り消したうえで発注される。
    """

    name = "base"

    def on_bar(self, bar: Bar, ctx: Context) -> list[Order]:
        raise NotImplementedError

    def on_fill(self, fill: Fill) -> None:
        pass
