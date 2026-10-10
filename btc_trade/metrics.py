from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import Fill, Side


@dataclass
class Report:
    start_equity: float
    end_equity: float
    net_pnl: float
    return_pct: float
    buy_and_hold_pct: float
    max_drawdown_pct: float
    fills: int
    round_trips: int
    win_rate_pct: float
    fees_paid: float  # 正なら支払い超過、負ならリベート超過
    maker_fills: int
    taker_fills: int

    @property
    def profitable(self) -> bool:
        return self.net_pnl > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["profitable"] = self.profitable
        return d

    def format(self) -> str:
        verdict = "合格（手数料込みでプラス）" if self.profitable else "不合格（手数料込みでマイナス）"
        return "\n".join(
            [
                f"開始資産        {self.start_equity:,.0f} JPY",
                f"終了資産        {self.end_equity:,.0f} JPY",
                f"損益            {self.net_pnl:+,.0f} JPY ({self.return_pct:+.3f}%)",
                f"ガチホ比較      {self.buy_and_hold_pct:+.3f}%",
                f"最大ドローダウン {self.max_drawdown_pct:.3f}%",
                f"約定数          {self.fills} (maker {self.maker_fills} / taker {self.taker_fills})",
                f"往復取引        {self.round_trips} 回, 勝率 {self.win_rate_pct:.1f}%",
                f"手数料合計      {self.fees_paid:+,.0f} JPY",
                f"判定            {verdict}",
            ]
        )


def build_report(
    equity_curve: list[float], fills: list[Fill], first_price: float, last_price: float
) -> Report:
    start, end = equity_curve[0], equity_curve[-1]
    peak, max_dd = start, 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)

    wins, trips = _round_trips(fills)
    return Report(
        start_equity=start,
        end_equity=end,
        net_pnl=end - start,
        return_pct=(end / start - 1) * 100 if start else 0.0,
        buy_and_hold_pct=(last_price / first_price - 1) * 100 if first_price else 0.0,
        max_drawdown_pct=max_dd * 100,
        fills=len(fills),
        round_trips=trips,
        win_rate_pct=wins / trips * 100 if trips else 0.0,
        fees_paid=sum(f.fee_jpy for f in fills),
        maker_fills=sum(1 for f in fills if f.liquidity == "maker"),
        taker_fills=sum(1 for f in fills if f.liquidity == "taker"),
    )


def _round_trips(fills: list[Fill]) -> tuple[int, int]:
    """ポジションが 0 に戻るまでを1往復とし、手数料込み損益で勝ち負けを数える。"""
    wins = trips = 0
    pos = 0.0
    cash_flow = 0.0
    for f in fills:
        if f.side is Side.BUY:
            pos += f.amount
            cash_flow -= f.price * f.amount + f.fee_jpy
        else:
            pos -= f.amount
            cash_flow += f.price * f.amount - f.fee_jpy
        if pos <= 1e-12:
            trips += 1
            wins += cash_flow > 0
            pos, cash_flow = 0.0, 0.0
    return wins, trips
