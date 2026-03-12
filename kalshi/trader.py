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

from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker

_bankroll_tracker = BankrollTracker(initial_bankroll=500.0)
_circuit_breaker = CircuitBreaker()


def reset_scan_budget():
    """Reset per-scan spending tracker. Call at start of each scan."""
    global _scan_spent
    _scan_spent = 0.0
    # Try to refresh bankroll from API (non-fatal if offline)
    try:
        bal = get_balance()
        _bankroll_tracker.update_from_api(
            balance_cents=bal.get("balance", 0),
            portfolio_value_cents=bal.get("portfolio_value", 0),
        )
    except Exception:
        pass  # Use last known or initial bankroll


def execute_kalshi_signal(market: dict, city: str, model_prob: float, market_prob: float, edge: float, direction: str, confidence: float = 0):
    """Execute a trade on Kalshi based on a signal."""
    global _scan_spent
    from config import PAPER_MODE, FRACTIONAL_KELLY
    from alerts.telegram_alert import send_signal_alert
    from risk.sizer import compute_size

    ticker = market.get("ticker", "")
    if not ticker:
        print("No ticker in market data — skipping")
        return

    # Determine side: positive edge = buy YES, negative = buy NO
    side = "yes" if edge > 0 else "no"

    # Price in cents
    if edge > 0:
        price_cents = int((market_prob + edge * 0.3) * 100)
    else:
        price_cents = int((1 - market_prob + abs(edge) * 0.3) * 100)
    price_cents = max(1, min(99, price_cents))

    # Kelly-based position sizing
    size_result = compute_size(
        model_prob=model_prob,
        market_prob=market_prob,
        confidence=confidence,
        price_cents=price_cents,
        bankroll_tracker=_bankroll_tracker,
        circuit_breaker=_circuit_breaker,
        scan_spent=_scan_spent,
        fractional_kelly=FRACTIONAL_KELLY,
    )

    if size_result.count == 0:
        print(f"\n  SKIP {ticker} — {size_result.limit_reason}")
        return

    count = size_result.count
    order_cost = size_result.dollar_amount

    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    print(f"\n  [{mode_label}] {side.upper()} {count} contracts @ {price_cents}¢ (${order_cost:.2f})")
    print(f"  Ticker: {ticker} | Edge: {edge:+.1%} | Kelly: {size_result.raw_kelly:.1%} → {size_result.adjusted_kelly:.1%}")
    print(f"  Scan budget: ${_scan_spent:.2f} spent | {size_result.limit_reason}")

    if PAPER_MODE:
        print(f"  PAPER MODE — no order sent.")
        _scan_spent += order_cost
        return

    try:
        resp = place_order(ticker, side, price_cents, count)
        order_id = resp.get("order", {}).get("order_id", "unknown")
        status = resp.get("order", {}).get("status", "unknown")
        _scan_spent += order_cost
        _bankroll_tracker.record_daily_pnl(-order_cost)  # Track spend for daily stop
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
