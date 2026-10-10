# btc-trade

bitbank の BTC/JPY を対象にした自動売買ツールです。
目標は **手数料込みで、微小でもプラスを出すこと**。そのために「検証の仕組み」を先に固めています。

**取引は現物のみ**です。口座の JPY と BTC を売り買いして元手（JPY 換算）を増やします。信用取引（ショート・レバレッジ）は使いません。シミュレータは売りを保有 BTC まで、買いを手元 JPY までに制限し、実発注も現物注文 API（`/v1/user/spot/order`）だけを呼びます。

## 進め方（合格ライン）

1. **バックテスト**: 実データ（1分足）で手数料・スリッページ込みの損益がプラス
2. **ペーパートレード**: 数週間、実時間で動かしてもプラス（バックテストとの差も確認）
3. **少額の実弾運用**: ユーザーが明示的に許可したときだけ

どの段階でも、判定は `net_pnl > 0`（手数料・スリッページ・期末の手仕舞いコスト込み）です。

## セットアップ

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

外部ライブラリには依存していません（Python 3.10 以上）。

## 使い方

```bash
# 1. 1分足を取得（認証不要）
python -m btc_trade fetch --start 2026-09-01 --end 2026-09-30 --out data/btc_jpy_1min_202609.csv

# 2. バックテスト
python -m btc_trade backtest --data data/btc_jpy_1min_202609.csv --strategy maker_band
python -m btc_trade backtest --data data/btc_jpy_1min_202609.csv --strategy sma_cross --param fast=10 --param slow=60

# 動作確認だけなら合成データでも可
python -m btc_trade backtest --synthetic 20000 --strategy maker_band

# 3. ペーパートレード（実注文は出ない。Ctrl+C で停止、ログは runs/paper/）
python -m btc_trade paper --strategy maker_band
```

## 約定モデル（保守的な前提）

| 項目 | ルール |
|---|---|
| 発注タイミング | バー t の確定後に注文し、バー t+1 で初めて約定判定（先読みなし） |
| 成行 | 次バー始値 ± スリッページ（既定 0.05%）、Taker 手数料 |
| 指値 | 価格が指値を **下回った/上回った** ときだけ Maker 約定（タッチは不約定）。始値の時点で約定可能なら Taker 扱い |
| 有効期限 | 未約定の注文は1本で失効。戦略が毎バー出し直す |
| 期末評価 | 残ポジションは成行で手仕舞った価値（Taker 手数料・スリッページ込み）で評価 |
| 手数料 | Maker -0.02% / Taker 0.12%（`btc_trade/fees.py`。改定があるので運用前に [公式](https://bitbank.cc/docs/fees/) で確認） |

## 構成

```
btc_trade/
  exchange/public.py   bitbank Public API（ティッカー・板・約定履歴・ローソク足）
  exchange/private.py  bitbank Private API（残高・注文）。発注は二重の安全装置つき
  broker.py            約定シミュレータ（バックテストとペーパーで共通）
  engine.py            バー1本の処理（同上）
  backtest.py          バックテスト
  paper.py             ペーパートレード
  metrics.py           成績レポート
  strategies/          戦略（sma_cross, maker_band はサンプル）
```

## 戦略の追加

`btc_trade/strategies/base.py` の `Strategy` を継承し、`on_bar(bar, ctx)` で注文のリストを返します。
`strategies/__init__.py` の `STRATEGIES` に登録すると CLI から使えます。

## 実弾取引の安全装置

`BitbankPrivate` の発注・取消は、コードで `live=True` を渡し、**かつ** 環境変数 `BTC_TRADE_ENABLE_LIVE=1` のときだけ動きます。
APIキーは「参照」「取引」のみ許可し、「出金」は付けないでください。キーは `.env`（git 管理外）に置きます。

## Claude と ChatGPT の相互レビュー

[docs/REVIEW_FLOW.md](docs/REVIEW_FLOW.md) を参照。

## 免責

このツールは利益を保証しません。暗号資産の売買は元本を失う可能性があります。
