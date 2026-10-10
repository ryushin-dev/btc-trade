# btc-trade

bitbank の BTC/JPY を対象にした自動売買ツールです。
目標は **手数料込みで、微小でもプラスを出すこと**。そのために「検証の仕組み」を先に固めています。

**取引は現物のみ**です。口座の JPY と BTC を売り買いして元手（JPY 換算）を増やします。信用取引（ショート・レバレッジ）は使いません。シミュレータは売りを保有 BTC まで、買いを手元 JPY までに制限し、実発注も現物注文 API（`/v1/user/spot/order`）だけを呼びます。

## 進め方（合格ライン）

1. **検証（validate）**: 実データ（1分足）をパラメータ固定のまま期間分割し、すべての期間で手数料込みプラス、
   かつコストを悪化させたシナリオ（スリッページ2倍、Makerリベートなし、Taker+0.05%、指値約定率1/5）でもプラス
2. **ペーパートレード**: 数週間、実時間で動かしてもプラス（バックテストとの差も確認）
3. **少額の実弾運用**: ユーザーが明示的に許可したときだけ。実弾接続の前に注文管理（照合・再開手順）を作り込む

損益はすべて清算価値（保有 BTC を成行で売った場合の、スリッページ・Taker 手数料込みの金額）で評価します。

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

# 3. 期間分割・コスト悪化の検証（合格なら終了コード 0、不合格なら 2）
python -m btc_trade validate --data data/btc_jpy_1min_202609.csv --strategy maker_band --folds 4

# 4. ペーパートレード（実注文は出ない。Ctrl+C で停止するとレポートを表示、ログは runs/paper/）
python -m btc_trade paper --strategy maker_band
```

## 約定モデル（保守的な前提）

### バックテスト

| 項目 | ルール |
|---|---|
| 発注タイミング | バー t の確定後に注文し、バー t+1 の中でだけ約定判定（先読みなし。判断から約定まで最大1本の遅延を見込む） |
| 成行 | 次バー始値 ± スリッページ（既定 0.05%）で全量、Taker 手数料 |
| 即時約定する指値 | 始値 ± スリッページが指値の内側なら、その価格で Taker 約定。指値は超えない |
| 板に置く指値 | 価格が指値を **突き抜けた** バーでだけ Maker 約定（タッチは不約定）。約定量はそのバーの出来高 × participation（既定 10%）まで。部分約定あり |
| 有効期限 | 未約定・部分約定の残りは1本で失効。戦略が毎バー出し直す |
| 残高 | 発注時に利用可能残高を確認して資金を拘束。足りなければ注文は拒否（数量を縮めて通すことはしない） |
| 1日の損失上限 | 日本時間のその日の開始時点から清算価値が 3% 超減ったら、その日は新規の買いを止める（`--max-daily-loss`） |
| 期末評価 | 残ポジションは成行売却の Fill として記帳し、手数料合計・勝率にも含める |
| 手数料 | Maker -0.02% / Taker 0.12%（`btc_trade/fees.py`。改定があるので運用前に [公式](https://bitbank.cc/docs/fees/) で確認） |

### ペーパートレード

確定した1分足で判断するところまではバックテストと同じですが、約定は実際の市場データで判定します。

- 成行・即時約定する指値は、発注した時点の実際の板を上から食って約定（板が薄ければ部分約定、指値は超えない）
- 板に置いた指値は、**発注時刻より後** の実際の約定履歴で価格が指値を突き抜けたときだけ、各約定数量 × participation まで約定
- 通信断や停止から復帰したとき、その間の足は指標の更新にだけ使い、注文は出さない
- 資産は最良買い気配での清算価値で記録し、停止時（Ctrl+C）に清算込みのレポートを出す

## 構成

```
btc_trade/
  exchange/public.py   bitbank Public API（ティッカー・板・約定履歴・ローソク足）
  exchange/private.py  bitbank Private API（残高・注文）。発注は二重の安全装置つき
  broker.py            残高・資金拘束と、バックテスト用の約定シミュレータ
  engine.py            戦略の呼び出しと1日の損失上限（バックテストとペーパーで共通）
  backtest.py          バックテスト
  paper.py             ペーパートレード
  metrics.py           成績レポート
  validate.py          期間分割・コスト悪化の検証
  strategies/          戦略（sma_cross, maker_band はサンプル）
```

## 戦略の追加

`btc_trade/strategies/base.py` の `Strategy` を継承し、`on_bar(bar, ctx)` で注文のリストを返します。
`strategies/__init__.py` の `STRATEGIES` に登録すると CLI から使えます。

## 実弾取引の安全装置

- `BitbankPrivate` の発注・取消は、コードで `live=True` を渡し、**かつ** 環境変数 `BTC_TRADE_ENABLE_LIVE=1` のときだけ動きます。
- 発注・取消の結果が分からない失敗（タイムアウト・通信断・5xx）では自動再試行せず、クライアントを停止状態にします。
  `reconcile()` で取引所側の未約定注文と約定履歴を取得・照合し、`resume()` するまで新規発注を受け付けません。
- `ACCESS-NONCE` はプロセス内で必ず単調増加します。同じ API キーを複数のプロセスで同時に使わないでください。
- API キーは「参照」「取引」のみ許可し、「出金」は付けないでください。キーは `.env`（git 管理外、`.env.example` を参照）に置けば自動で読み込まれます。

実弾接続の前に、注文を永続的に記録して起動時に照合する仕組み（注文管理）を別の PR で作ります。

## Claude と ChatGPT の相互レビュー

[docs/REVIEW_FLOW.md](docs/REVIEW_FLOW.md) を参照。

## 免責

このツールは利益を保証しません。暗号資産の売買は元本を失う可能性があります。
