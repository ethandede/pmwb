import os
import time
import base64
import requests
from datetime import datetime, timezone
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from kalshi.fill_tracker import init_trades_db, record_fill, update_fill_data
from kalshi.pricing import choose_price_strategy, kalshi_fee

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
    _load_credentials()
    timestamp_ms = str(int(time.time() * 1000))
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
    data = _get("/trade-api/v2/portfolio/positions", {
        "limit": limit,
        "settlement_status": settlement_status,
    })
    return data.get("market_positions", [])


def get_orders(limit: int = 50, status: str = "resting") -> list:
    data = _get("/trade-api/v2/portfolio/orders", {"limit": limit, "status": status})
    return data.get("orders", [])


def _post_order(ticker: str, action: str, side: str, price_cents: int, count: int) -> dict:
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
    return _post_order(ticker, "buy", side, price_cents, count)


def sell_order(ticker: str, side: str, price_cents: int, count: int) -> dict:
    return _post_order(ticker, "sell", side, price_cents, count)


_scan_spent = 0.0
_resting_buy_tickers: set[str] | None = None

from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker


def _init_bankroll() -> BankrollTracker:
    try:
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        portfolio = bal.get("portfolio_value", 0) / 100.0
        bt = BankrollTracker(initial_bankroll=cash + portfolio)
        bt.update_from_api(
            balance_cents=bal.get("balance", 0),
            portfolio_value_cents=bal.get("portfolio_value", 0),
        )
        print(f"  Bankroll synced: ${cash + portfolio:.2f} (cash ${cash:.2f} + positions ${portfolio:.2f})")
        return bt
    except Exception as e:
        print(f"  Bankroll API unavailable ({e}), using $500 default")
        return BankrollTracker(initial_bankroll=500.0)


_bankroll_tracker = _init_bankroll()
_circuit_breaker = CircuitBreaker()


def reset_scan_budget():
    global _scan_spent, _resting_buy_tickers
    _scan_spent = 0.0
    _resting_buy_tickers = None
    try:
        bal = get_balance()
        _bankroll_tracker.update_from_api(
            balance_cents=bal.get("balance", 0),
            portfolio_value_cents=bal.get("portfolio_value", 0),
        )
    except Exception as e:
        print(f"  Bankroll sync failed: {e} — using last known value")


def execute_kalshi_signal(market: dict, city: str, model_prob: float, market_prob: float, edge: float, direction: str, confidence: float = 0, existing_contracts: int = 0, kelly_floor: float | None = None):
    global _scan_spent
    from config import PAPER_MODE, FRACTIONAL_KELLY, MAX_BANKROLL_PCT_PER_TRADE

    ticker = market.get("ticker", "")
    if not ticker:
        print("No ticker in market data — skipping")
        return

    global _resting_buy_tickers
    if _resting_buy_tickers is None:
        try:
            resting = get_orders(status="resting")
            _resting_buy_tickers = {o.get("ticker", "") for o in resting if o.get("action") == "buy"}
        except Exception:
            _resting_buy_tickers = set()
    if ticker in _resting_buy_tickers:
        print(f"\n  SKIP {ticker} — resting buy order already exists")
        return

    side = "yes" if edge > 0 else "no"

    # --- Sanity check: does the forecast actually support this trade? ---
    # Skip for precip markets — sanity check uses temperature data which is wrong for rain
    try:
        from kalshi.scanner import parse_kalshi_bucket
        market_type = market.get("_market_type", "temp")
        bucket = parse_kalshi_bucket(market)
        if bucket and market_type != "precip":
            low, high = bucket
            # Quick GFS forecast from self-hosted (instant, no rate limit)
            import requests as _req
            unit = market.get("_unit", "f")
            unit_param = "fahrenheit" if unit == "f" else "celsius"
            temp_type = market.get("_temp_type", "max")
            daily_var = f"temperature_2m_{temp_type}"
            lat, lon = market.get("_lat", 0), market.get("_lon", 0)
            r = _req.get(f"http://localhost:8080/v1/forecast?latitude={lat}&longitude={lon}&daily={daily_var}&models=gfs_seamless&temperature_unit={unit_param}&timezone=auto&forecast_days=2", timeout=5)
            gfs_temp = r.json().get("daily", {}).get(daily_var, [None])[0]
            if gfs_temp is not None:
                if high is None and low is not None:
                    # "above X" contract — buying YES means we think temp > threshold
                    if side == "yes" and gfs_temp < low - 3:
                        print(f"\n  SANITY BLOCK {ticker} — BUY YES 'above {low}' but GFS says {gfs_temp:.1f} ({low - gfs_temp:.1f}° below)")
                        return
                    # buying NO means we think temp < threshold
                    if side == "no" and gfs_temp > low + 3:
                        print(f"\n  SANITY BLOCK {ticker} — BUY NO 'above {low}' but GFS says {gfs_temp:.1f} ({gfs_temp - low:.1f}° above)")
                        return
                elif low is not None and high is not None:
                    # bucket contract — buying YES means we think temp lands in [low, high)
                    mid = (low + high) / 2
                    if side == "yes" and abs(gfs_temp - mid) > 15:
                        print(f"\n  SANITY BLOCK {ticker} — BUY YES bucket {low}-{high} but GFS says {gfs_temp:.1f} ({abs(gfs_temp - mid):.1f}° away)")
                        return
    except Exception:
        pass  # sanity check is advisory, never blocks on errors

    # --- Maker/taker pricing strategy ---
    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    if yes_bid is None:
        yes_bid_d = market.get("yes_bid_dollars")
        if yes_bid_d:
            yes_bid = int(float(yes_bid_d) * 100)
    if yes_ask is None:
        yes_ask_d = market.get("yes_ask_dollars")
        if yes_ask_d:
            yes_ask = int(float(yes_ask_d) * 100)

    price_cents, strategy = choose_price_strategy(
        side=side, yes_bid=yes_bid, yes_ask=yes_ask, edge=abs(edge),
    )
    if price_cents is None:
        # No market data — fall back to legacy formula
        if edge > 0:
            price_cents = int((market_prob + edge * 0.3) * 100)
        else:
            price_cents = int((1 - market_prob + abs(edge) * 0.3) * 100)
        strategy = "legacy"
    price_cents = max(1, min(99, price_cents))

    effective_kelly = kelly_floor if kelly_floor is not None else FRACTIONAL_KELLY

    from risk.sizer import compute_size
    size_result = compute_size(
        model_prob=model_prob,
        market_prob=market_prob,
        confidence=confidence,
        price_cents=price_cents,
        bankroll_tracker=_bankroll_tracker,
        circuit_breaker=_circuit_breaker,
        scan_spent=_scan_spent,
        fractional_kelly=effective_kelly,
        event_contracts=existing_contracts,
    )

    # HARD 2% BANKROLL CAP
    current_bankroll = _bankroll_tracker.effective_bankroll()
    max_dollars = current_bankroll * MAX_BANKROLL_PCT_PER_TRADE
    if size_result.dollar_amount > max_dollars:
        size_result.count = int(max_dollars / (price_cents / 100.0))
        print(f"  [SAFETY] Reduced size to respect 2% bankroll cap → {size_result.count} contracts")

    if size_result.count == 0:
        print(f"\n  SKIP {ticker} — {size_result.limit_reason}")
        return

    count = size_result.count
    order_cost = size_result.dollar_amount

    is_taker = strategy in ("taker", "legacy")
    fee_estimate = kalshi_fee(price_cents, count, is_taker=is_taker)
    expected_profit = (abs(edge) * count * 1.00) - fee_estimate
    if expected_profit < 0.12:
        print(f"\n  SKIP {ticker} — fee-adjusted profit ${expected_profit:.2f} < $0.12 (fee ~${fee_estimate:.2f}, {strategy})")
        return

    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    print(f"\n  [{mode_label}] {side.upper()} {count} contracts @ {price_cents}¢ (${order_cost:.2f})")
    print(f"  Ticker: {ticker} | Edge: {edge:+.1%} | Kelly: {size_result.raw_kelly:.1%} → {size_result.adjusted_kelly:.1%}")
    print(f"  Strategy: {strategy.upper()} | Fee est: ${fee_estimate:.3f}")
    print(f"  Scan budget: ${_scan_spent:.2f} spent | {size_result.limit_reason}")

    if PAPER_MODE:
        print(f"  PAPER MODE — no order sent.")
        _scan_spent += order_cost
        return

    try:
        resp = place_order(ticker, side, price_cents, count)
        order = resp.get("order", {})
        order_id = order.get("order_id", "unknown")
        status = order.get("status", "unknown")
        print(f"  Order posted! ID: {order_id} Status: {status}")

        _resting_buy_tickers.add(ticker)

        fill_qty = int(float(order.get("fill_count_fp", "0") or "0"))
        remaining = int(float(order.get("remaining_count_fp", "0") or "0"))

        if fill_qty > 0:
            taker_cost = float(order.get("taker_fill_cost_dollars", "0") or "0")
            maker_cost = float(order.get("maker_fill_cost_dollars", "0") or "0")
            actual_cost = taker_cost + maker_cost if (taker_cost + maker_cost) > 0 else fill_qty * price_cents / 100.0
            actual_price_cents = int(actual_cost / fill_qty * 100) if fill_qty > 0 else price_cents

            _scan_spent += actual_cost
            print(f"  Filled: {fill_qty}/{count} @ ~{actual_price_cents}¢ (${actual_cost:.2f})")

            init_trades_db(TRADES_DB_PATH)
            record_fill(
                db_path=TRADES_DB_PATH,
                order_id=order_id,
                ticker=ticker,
                side=f"buy_{side}",
                limit_price=price_cents,
                fill_price=actual_price_cents,
                fill_qty=fill_qty,
                fill_time=datetime.now(timezone.utc).isoformat(),
                city=city,
            )
        else:
            _scan_spent += order_cost
            print(f"  Resting: 0/{count} filled — limit order at {price_cents}¢")

            init_trades_db(TRADES_DB_PATH)
            record_fill(
                db_path=TRADES_DB_PATH,
                order_id=order_id,
                ticker=ticker,
                side=f"buy_{side}",
                limit_price=price_cents,
                fill_price=0,
                fill_qty=0,
                fill_time=datetime.now(timezone.utc).isoformat(),
                city=city,
            )

        if remaining > 0:
            print(f"  Partial fill: {remaining} contracts still resting")

        try:
            from alerts.telegram_alert import send_signal_alert
            send_signal_alert(
                market.get("title", ticker), city + " (Kalshi)", model_prob, market_prob, edge,
                f"{direction} (LIVE order {order_id}, filled {fill_qty}/{count})"
            )
        except Exception:
            pass
    except Exception as e:
        print(f"  Order failed: {e}")


def poll_and_update_fills():
    try:
        orders = get_orders(status="resting") + get_orders(status="executed")

        updated = 0
        for order in orders:
            order_id = order.get("order_id")
            if not order_id:
                continue

            fill_qty = int(float(order.get("fill_count_fp", "0") or "0"))

            if fill_qty > 0:
                taker_cost = float(order.get("taker_fill_cost_dollars", "0") or "0")
                maker_cost = float(order.get("maker_fill_cost_dollars", "0") or "0")
                total_cost = taker_cost + maker_cost
                actual_price_cents = int((total_cost / fill_qty) * 100) if fill_qty > 0 else 0

                init_trades_db(TRADES_DB_PATH)
                update_fill_data(
                    db_path=TRADES_DB_PATH,
                    order_id=order_id,
                    fill_price=actual_price_cents,
                    fill_qty=fill_qty,
                    fill_time=order.get("last_update_time", datetime.now(timezone.utc).isoformat()),
                )
                updated += 1
                print(f"  [Poller] Updated fill for {order_id}: {fill_qty} @ {actual_price_cents}¢")

        if updated:
            print(f"  [Poller] Updated {updated} orders with actual fills")
        else:
            print("  [Poller] No new fills this cycle")

    except Exception as e:
        print(f"  [Poller] Error: {e}")