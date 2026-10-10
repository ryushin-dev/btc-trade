from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .backtest import run_backtest
from .data import fetch_range, load_csv, save_csv, synthetic_bars
from .exchange.public import BitbankPublic
from .fees import BITBANK_BTC_JPY, FeeSchedule
from .paper import PaperTrader
from .strategies import STRATEGIES, build_strategy
from .validate import validate


def _params(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        k, _, v = item.partition("=")
        if not v:
            raise SystemExit(f"--param は key=value 形式で指定してください: {item}")
        out[k] = v
    return out


def _fees(args: argparse.Namespace) -> FeeSchedule:
    return FeeSchedule(
        maker=args.maker_fee if args.maker_fee is not None else BITBANK_BTC_JPY.maker,
        taker=args.taker_fee if args.taker_fee is not None else BITBANK_BTC_JPY.taker,
    )


def _add_trading_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    p.add_argument("--param", action="append", default=[], help="戦略パラメータ key=value（複数可）")
    p.add_argument("--cash", type=float, default=100_000, help="初期資金 JPY")
    p.add_argument("--slippage", type=float, default=0.0005, help="成行のスリッページ率")
    p.add_argument("--participation", type=float, default=0.1, help="指値が取れる出来高の割合")
    p.add_argument("--max-daily-loss", type=float, default=0.03, help="1日の損失がこの割合を超えたら新規買い停止")
    p.add_argument("--maker-fee", type=float, default=None)
    p.add_argument("--taker-fee", type=float, default=None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="btc_trade", description="bitbank BTC/JPY 自動売買ツール")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="ローソク足を取得して CSV 保存")
    f.add_argument("--pair", default="btc_jpy")
    f.add_argument("--candle", default="1min")
    f.add_argument("--start", type=date.fromisoformat, required=True)
    f.add_argument("--end", type=date.fromisoformat, required=True)
    f.add_argument("--out", required=True)

    b = sub.add_parser("backtest", help="手数料・スリッページ込みバックテスト")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="fetch で保存した CSV")
    src.add_argument("--synthetic", type=int, metavar="N", help="合成データ N 本で動作確認")
    b.add_argument("--json", action="store_true", help="結果を JSON で出力")
    _add_trading_args(b)

    v = sub.add_parser("validate", help="期間分割とコスト悪化で収益性を検証")
    v.add_argument("--data", required=True, help="fetch で保存した CSV")
    v.add_argument("--folds", type=int, default=4)
    v.add_argument("--min-trades", type=int, default=10, help="各期間で必要な最低往復回数")
    _add_trading_args(v)

    p = sub.add_parser("paper", help="ペーパートレード（実注文なし）")
    p.add_argument("--pair", default="btc_jpy")
    p.add_argument("--poll", type=float, default=10.0, help="ポーリング間隔（秒）")
    p.add_argument("--log-dir", default="runs/paper")
    _add_trading_args(p)

    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        bars = fetch_range(BitbankPublic(), args.pair, args.candle, args.start, args.end)
        save_csv(bars, args.out)
        print(f"saved {len(bars)} bars to {args.out}")
        return 0

    strategy = build_strategy(args.strategy, _params(args.param))

    if args.cmd == "backtest":
        bars = load_csv(args.data) if args.data else synthetic_bars(args.synthetic)
        report, _, _ = run_backtest(
            bars, strategy, args.cash, _fees(args), args.slippage, args.participation, args.max_daily_loss
        )
        if args.json:
            json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(f"戦略 {strategy.name} / {len(bars)} 本")
            print(report.format())
        return 0

    if args.cmd == "validate":
        params = _params(args.param)
        result = validate(
            load_csv(args.data),
            lambda: build_strategy(args.strategy, params),
            folds=args.folds,
            cash_jpy=args.cash,
            fees=_fees(args),
            slippage=args.slippage,
            participation=args.participation,
            min_trades=args.min_trades,
            max_daily_loss=args.max_daily_loss,
        )
        print(f"戦略 {args.strategy} {params}")
        print(result.format())
        return 0 if result.passed else 2

    if args.cmd == "paper":
        trader = PaperTrader(
            strategy,
            BitbankPublic(),
            args.cash,
            _fees(args),
            args.slippage,
            args.pair,
            args.log_dir,
            participation=args.participation,
            max_daily_loss=args.max_daily_loss,
        )
        trader.run(args.poll)
        return 0
    return 1
