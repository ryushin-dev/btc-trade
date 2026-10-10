"""bitbank Private API クライアント（APIキー必須）。

https://github.com/bitbankinc/bitbank-api-docs/blob/master/rest-api.md

安全装置:
- 発注・取消は `live=True` かつ環境変数 BTC_TRADE_ENABLE_LIVE=1 の両方がそろわない限り
  LiveTradingDisabled を送出する。残高や注文の参照はキーさえあれば可能。
- 発注・取消の結果が分からないまま失敗した場合（タイムアウト、通信断、5xx など）は
  OrderStateUnknown を送出してクライアントを停止状態にする。停止中は新規の発注・取消を受け付けない。
  reconcile() で取引所側の未約定注文と約定履歴を取得し、照合したうえで resume() を呼ぶまで再開しない。
  自動再試行はしない（受け付け済みの注文を二重に出さないため）。
- ACCESS-NONCE はプロセス内で必ず単調増加させる。同じキーを複数プロセスで同時に使わないこと。

APIキーは「参照」と「取引」のみ許可し、「出金」は絶対に許可しないこと。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .public import BitbankError

PRIVATE_BASE = "https://api.bitbank.cc"


class LiveTradingDisabled(RuntimeError):
    pass


class OrderStateUnknown(RuntimeError):
    """発注・取消が取引所に届いたか分からない。照合するまで新規の発注をしてはいけない。"""


_nonce_lock = threading.Lock()
_last_nonce = 0


def next_nonce() -> int:
    """ミリ秒時刻を基本に、同一ミリ秒や時計の巻き戻りでも必ず前回より大きい値を返す。"""
    global _last_nonce
    with _nonce_lock:
        _last_nonce = max(int(time.time() * 1000), _last_nonce + 1)
        return _last_nonce


def load_env_file(path: str | os.PathLike = ".env") -> None:
    """KEY=VALUE 形式の .env を読み、未設定の環境変数だけを埋める。"""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class BitbankPrivate:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        live: bool = False,
        base: str = PRIVATE_BASE,
        timeout: float = 10.0,
    ) -> None:
        load_env_file()
        self.api_key = api_key or os.environ.get("BITBANK_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BITBANK_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise ValueError("BITBANK_API_KEY / BITBANK_API_SECRET が未設定です")
        self.live = live and os.environ.get("BTC_TRADE_ENABLE_LIVE") == "1"
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.halted = False

    def _headers(self, message_suffix: str) -> dict[str, str]:
        nonce = str(next_nonce())
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-NONCE": nonce,
            "ACCESS-SIGNATURE": sign(self.api_secret, nonce + message_suffix),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        if method == "GET":
            query = "?" + urllib.parse.urlencode(params) if params else ""
            url = self.base + path + query
            headers = self._headers(path + query)
            data = None
        else:
            body = json.dumps(params or {})
            url = self.base + path
            headers = self._headers(body)
            data = body.encode()
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("success") != 1:
            raise BitbankError(payload.get("data", {}).get("code"), path)
        return payload["data"]

    def _mutate(self, path: str, params: dict) -> dict:
        """結果が分からない失敗では停止状態にする。取引所がエラーを返した場合（注文は不成立）は停止しない。"""
        self._require_live()
        if self.halted:
            raise OrderStateUnknown("前回の発注・取消の結果が未確認です。reconcile() で照合してから resume() してください。")
        try:
            return self._request("POST", path, params)
        except BitbankError:
            raise
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            self.halted = True
            raise OrderStateUnknown(f"{path} の結果が不明です（HTTP {e.code}）") from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            self.halted = True
            raise OrderStateUnknown(f"{path} の結果が不明です（{e!r}）") from e

    def reconcile(self, pair: str = "btc_jpy") -> dict:
        """取引所側の未約定注文と直近の約定履歴を返す。呼び出し側で照合してから resume() すること。"""
        return {
            "active_orders": self.active_orders(pair),
            "trade_history": self.trade_history(pair),
        }

    def resume(self) -> None:
        self.halted = False

    # --- 参照系 ---
    def assets(self) -> dict:
        return self._request("GET", "/v1/user/assets")

    def active_orders(self, pair: str = "btc_jpy") -> dict:
        return self._request("GET", "/v1/user/spot/active_orders", {"pair": pair})

    def trade_history(self, pair: str = "btc_jpy") -> dict:
        return self._request("GET", "/v1/user/spot/trade_history", {"pair": pair})

    # --- 発注系（安全装置つき） ---
    def _require_live(self) -> None:
        if not self.live:
            raise LiveTradingDisabled(
                "実弾取引は無効です。live=True かつ BTC_TRADE_ENABLE_LIVE=1 のときだけ発注できます。"
            )

    def create_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        amount: str,
        price: str | None = None,
        post_only: bool = False,
    ) -> dict:
        params: dict = {"pair": pair, "side": side, "type": order_type, "amount": amount}
        if price is not None:
            params["price"] = price
        if post_only:
            params["post_only"] = True
        return self._mutate("/v1/user/spot/order", params)

    def cancel_order(self, pair: str, order_id: int) -> dict:
        return self._mutate("/v1/user/spot/cancel_order", {"pair": pair, "order_id": order_id})
