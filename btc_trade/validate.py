"""収益性の検証: パラメータ固定のまま期間を分けた成績と、コストを悪化させた成績を出す。

合格条件（すべて満たすこと）:
- 各期間で手数料込み損益がプラス
- すべてのコスト悪化シナリオ（全期間）でも手数料込み損益がプラス
- 各期間の往復取引が min_trades 回以上（取引が少なすぎる「たまたま」の利益を除くため）
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .backtest import run_backtest
from .fees import FeeSchedule
from .metrics import Report
from .models import Bar
from .strategies import Strategy


@dataclass(frozen=True)
class Scenario:
    name: str
    fees: FeeSchedule
    slippage: float
    participation: float


def stress_scenarios(fees: FeeSchedule, slippage: float, participation: float) -> list[Scenario]:
    return [
        Scenario("基準", fees, slippage, participation),
        Scenario("スリッページ2倍", fees, slippage * 2, participation),
        Scenario("Makerリベートなし", FeeSchedule(max(fees.maker, 0.0), fees.taker), slippage, participation),
        Scenario("Taker+0.05%", FeeSchedule(fees.maker, fees.taker + 0.0005), slippage, participation),
        Scenario("指値の約定率1/5", fees, slippage, participation / 5),
    ]


@dataclass
class ValidationResult:
    folds: list[tuple[str, Report]]
    stress: list[tuple[str, Report]]
    min_trades: int

    @property
    def passed(self) -> bool:
        return (
            all(r.profitable and r.round_trips >= self.min_trades for _, r in self.folds)
            and all(r.profitable for _, r in self.stress)
        )

    def format(self) -> str:
        lines = ["期間別（基準コスト）", _header()]
        lines += [_row(name, r) for name, r in self.folds]
        lines += ["", "コスト悪化（全期間）", _header()]
        lines += [_row(name, r) for name, r in self.stress]
        lines += ["", f"判定: {'合格' if self.passed else '不合格'}（各期間プラスかつ往復{self.min_trades}回以上、全シナリオでプラス）"]
        return "\n".join(lines)


def _header() -> str:
    return f"{'':16}{'損益(JPY)':>12}{'損益率':>10}{'最大DD':>9}{'往復':>6}{'勝率':>8}{'手数料':>10}"


def _row(name: str, r: Report) -> str:
    return (
        f"{name:16}{r.net_pnl:>+12,.0f}{r.return_pct:>+9.3f}%{r.max_drawdown_pct:>8.2f}%"
        f"{r.round_trips:>6}{r.win_rate_pct:>7.1f}%{r.fees_paid:>+10,.0f}"
    )


def validate(
    bars: list[Bar],
    make_strategy: Callable[[], Strategy],
    folds: int = 4,
    cash_jpy: float = 100_000,
    fees: FeeSchedule | None = None,
    slippage: float = 0.0005,
    participation: float = 0.1,
    min_trades: int = 10,
    max_daily_loss: float = 0.03,
) -> ValidationResult:
    from .fees import BITBANK_BTC_JPY

    fees = fees or BITBANK_BTC_JPY
    size = len(bars) // folds
    if size < 2:
        raise ValueError("データが少なすぎます")
    fold_results = []
    for i in range(folds):
        chunk = bars[i * size : (i + 1) * size if i < folds - 1 else len(bars)]
        rep, _, _ = run_backtest(chunk, make_strategy(), cash_jpy, fees, slippage, participation, max_daily_loss)
        fold_results.append((f"期間{i + 1}", rep))
    stress = []
    for sc in stress_scenarios(fees, slippage, participation):
        rep, _, _ = run_backtest(
            bars, make_strategy(), cash_jpy, sc.fees, sc.slippage, sc.participation, max_daily_loss
        )
        stress.append((sc.name, rep))
    return ValidationResult(fold_results, stress, min_trades)
