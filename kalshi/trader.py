import os
import time
import base64
import requests
from datetime import datetime, timezone
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from kalshi.fill_tracker import init_trades_db, record_fill

TRADES_DB_PATH = "data/trades.db"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_BASE_URL = KALSHI_BASE.replace("/trade-api/v2", "")

_key_id = None
_private_key = None


def _load_credentials():
    global _key_id, _private_key
    if _key_id is None:
        from dotenv import load_dotenv
        load_dotenv()
        _key_id = os.getenv("KALSHI_API_KEY")
        key_file = os.getenv("KALSHI_KEY_FILE", "kalshi/kalshi_key.pem")
        with open(key_file, "rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None)


def _sign_request(method: str, path: str) -> dict:
    """Generate auth headers for a Kalshi API request."""
    _load_credentials()
    timestamp_ms = str(int(time.time() * 1000))
    # Strip query params from path for signing
    sign_path = path.split("?")[0]
    message = f"{timestamp_ms}{method.upper()}{sign_path}"
    signature = _private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256.digest_size,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": _key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "Content-Type": "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET request to Kalshi API."""
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{qs}"
    else:
        full_path = path
    headers = _sign_request("GET", full_path)
    resp = requests.get(f"{_BASE_URL}{full_path}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_balance() -> dict:
    return _get("/trade-api/v2/portfolio/balance")


def get_positions(limit: int = 100, settlement_status: str = "unsettled") -> list:
    """Get current portfolio positions."""
    data = _get("/trade-api/v2/portfolio/positions", {
        "limit": limit,
        "settlement_status": settlement_status,
    })
    return data.get("market_positions", [])


def get_orders(limit: int = 50, status: str = "resting") -> list:
    """Get orders by status (resting, canceled, executed)."""
    data = _get("/trade-api/v2/portfolio/orders", {"limit": limit, "status": status})
    return data.get("orders", [])


def _post_order(ticker: str, action: str, side: str, price_cents: int, count: int) -> dict:
    """Place an order (buy or sell) on Kalshi."""
    path = "/trade-api/v2/portfolio/orders"
    headers = _sign_request("POST", path)
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
    resp = requests.post(
        f"{_BASE_URL}{path}",
        headers=headers,
        json=body,
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"  Kalshi API error detail: {resp.text}")
    resp.raise_for_status()
    return resp.json()


def place_order(ticker: str, side: str, price_cents: int, count: int) -> dict:
    """Place a BUY limit order on Kalshi."""
    return _post_order(ticker, "buy", side, price_cents, count)


def sell_order(ticker: str, side: str, price_cents: int, count: int) -> dict:
    """Place a SELL limit order to close a position."""
    return _post_order(ticker, "sell", side, price_cents, count)


_scan_spent = 0.0  # tracks dollars spent in current scan cycle


def reset_scan_budget():
    """Reset per-scan spending tracker. Call at start of each scan."""
    global _scan_spent
    _scan_spent = 0.0


def execute_kalshi_signal(market: dict, city: str, model_prob: float, market_prob: float, edge: float, direction: str, confidence: float = 0):
    """Execute a trade on Kalshi based on a signal."""
    global _scan_spent
    from config import PAPER_MODE, MAX_ORDER_USD, MAX_SCAN_BUDGET, HIGH_CONFIDENCE_MULTIPLIER
    from alerts.telegram_alert import send_signal_alert

    ticker = market.get("ticker", "")
    if not ticker:
        print("No ticker in market data — skipping")
        return

    # Check scan budget
    if _scan_spent >= MAX_SCAN_BUDGET:
        print(f"\n  BUDGET CAP — ${MAX_SCAN_BUDGET:.0f}/scan reached, skipping {ticker}")
        return

    # Determine side: positive edge = buy YES, negative = buy NO
    side = "yes" if edge > 0 else "no"

    # Price in cents
    if edge > 0:
        price_cents = int((market_prob + edge * 0.3) * 100)
    else:
        price_cents = int((1 - market_prob + abs(edge) * 0.3) * 100)
    price_cents = max(1, min(99, price_cents))

    # Position sizing: cap per order and respect remaining scan budget
    # Boost size for high-confidence signals
    size_mult = HIGH_CONFIDENCE_MULTIPLIER if confidence >= 85 else 1.0
    remaining = MAX_SCAN_BUDGET - _scan_spent
    order_budget = min(MAX_ORDER_USD * size_mult, remaining)
    count = max(1, int(order_budget * 100 / max(price_cents, 1)))
    order_cost = count * price_cents / 100.0

    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    print(f"\n  [{mode_label}] {side.upper()} {count} contracts @ {price_cents}¢ (${order_cost:.2f})")
    print(f"  Ticker: {ticker} | Edge: {edge:+.1%} | City: {city}")
    print(f"  Scan budget: ${_scan_spent:.2f}/${MAX_SCAN_BUDGET:.0f} spent")

    if PAPER_MODE:
        print(f"  PAPER MODE — no order sent.")
        return

    try:
        resp = place_order(ticker, side, price_cents, count)
        order_id = resp.get("order", {}).get("order_id", "unknown")
        status = resp.get("order", {}).get("status", "unknown")
        _scan_spent += order_cost
        print(f"  Order posted! ID: {order_id} Status: {status}")

        # Persist fill for backtesting
        init_trades_db(TRADES_DB_PATH)
        record_fill(
            db_path=TRADES_DB_PATH,
            order_id=order_id,
            ticker=ticker,
            side=side,
            limit_price=price_cents,
            fill_price=price_cents,
            fill_qty=count,
            fill_time=datetime.now(timezone.utc).isoformat(),
            city=city,
        )

        send_signal_alert(
            market.get("title", ticker), city + " (Kalshi)", model_prob, market_prob, edge,
            f"{direction} (LIVE order {order_id})"
        )
    except Exception as e:
        print(f"  Order failed: {e}")
