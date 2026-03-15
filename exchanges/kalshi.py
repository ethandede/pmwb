"""KalshiExchange — thin wrapper around Kalshi REST API.

Handles authentication, request signing, and order management.
Stateless except for lazily-loaded credentials.
"""

import os
import time
import base64
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


_BASE_URL = "https://api.elections.kalshi.com"


class KalshiExchange:
    def __init__(self):
        self._key_id: str | None = None
        self._private_key = None

    def _load_credentials(self):
        if self._key_id is not None:
            return
        from dotenv import load_dotenv
        load_dotenv()
        self._key_id = os.environ.get("KALSHI_API_KEY", "")
        pk_path = os.environ.get("KALSHI_KEY_FILE", "kalshi/kalshi_key.pem")
        if pk_path and os.path.exists(pk_path):
            with open(pk_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(f.read(), password=None)

    def _sign_request(self, method: str, path: str) -> dict:
        self._load_credentials()
        timestamp_ms = str(int(time.time() * 1000))
        sign_path = path.split("?")[0]
        message = f"{timestamp_ms}{method.upper()}{sign_path}"
        signature = self._private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256.digest_size,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            full_path = f"{path}?{qs}"
        else:
            full_path = path
        headers = self._sign_request("GET", full_path)
        resp = requests.get(f"{_BASE_URL}{full_path}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post_order(self, ticker: str, action: str, side: str, price_cents: int, count: int) -> dict:
        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_request("POST", path)
        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "type": "limit",
            "yes_price": price_cents if side == "yes" else None,
            "no_price": price_cents if side == "no" else None,
            "count": count,
        }
        body = {k: v for k, v in body.items() if v is not None}
        resp = requests.post(f"{_BASE_URL}{path}", headers=headers, json=body, timeout=15)
        if resp.status_code >= 400:
            print(f"  Kalshi API error detail: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    # --- Public API ---

    def get_balance(self) -> dict:
        return self._get("/trade-api/v2/portfolio/balance")

    def get_positions(self, limit: int = 100, settlement_status: str = "unsettled") -> list:
        data = self._get("/trade-api/v2/portfolio/positions", {
            "limit": limit,
            "settlement_status": settlement_status,
        })
        return data.get("market_positions", [])

    def get_orders(self, limit: int = 50, status: str = "resting") -> list:
        data = self._get("/trade-api/v2/portfolio/orders", {"limit": limit, "status": status})
        return data.get("orders", [])

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market's data (for settler + position manager)."""
        data = self._get(f"/trade-api/v2/markets/{ticker}")
        return data.get("market", {})

    def place_order(self, ticker: str, action: str, side: str, price_cents: int, count: int) -> dict:
        return self._post_order(ticker, action, side, price_cents, count)

    def sell_order(self, ticker: str, side: str, price_cents: int, count: int) -> dict:
        """Convenience for selling — delegates to place_order with action='sell'."""
        return self._post_order(ticker, "sell", side, price_cents, count)

    def fetch_events(self, series_ticker: str) -> list:
        """Fetch all open events for a series (for market discovery)."""
        data = self._get("/trade-api/v2/events", {
            "series_ticker": series_ticker,
            "status": "open",
            "with_nested_markets": "true",
        })
        return data.get("events", [])
