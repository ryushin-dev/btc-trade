"""bitbank Private API クライアント（APIキー必須）。

https://github.com/bitbankinc/bitbank-api-docs/blob/master/rest-api.md

安全装置: 発注・取消は `live=True` かつ環境変数 BTC_TRADE_ENABLE_LIVE=1 の両方がそろわない限り
LiveTradingDisabled を送出する。残高や注文の参照はキーさえあれば可能。
APIキーは「参照」と「取引」のみ許可し、「出金」は絶対に許可しないこと。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

from .public import BitbankError

PRIVATE_BASE = "https://api.bitbank.cc"


class LiveTradingDisabled(RuntimeError):
    pass


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
        self.api_key = api_key or os.environ.get("BITBANK_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BITBANK_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise ValueError("BITBANK_API_KEY / BITBANK_API_SECRET が未設定です")
        self.live = live and os.environ.get("BTC_TRADE_ENABLE_LIVE") == "1"
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _headers(self, message_suffix: str) -> dict[str, str]:
        nonce = str(int(time.time() * 1000))
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

    # --- 参照系 ---
    def assets(self) -> dict:
        return self._request("GET", "/v1/user/assets")

    def active_orders(self, pair: str = "btc_jpy") -> dict:
        return self._request("GET", "/v1/user/spot/active_orders", {"pair": pair})

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
        self._require_live()
        params: dict = {"pair": pair, "side": side, "type": order_type, "amount": amount}
        if price is not None:
            params["price"] = price
        if post_only:
            params["post_only"] = True
        return self._request("POST", "/v1/user/spot/order", params)

    def cancel_order(self, pair: str, order_id: int) -> dict:
        self._require_live()
        return self._request("POST", "/v1/user/spot/cancel_order", {"pair": pair, "order_id": order_id})
