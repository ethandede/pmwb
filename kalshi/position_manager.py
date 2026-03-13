"""Intraday Position Manager — re-evaluate open positions and exit when edge evaporates.

Checks each open position against fresh model forecasts:
  - EXIT if edge has flipped (model now disagrees with our side)
  - EXIT if edge shrank below minimum threshold
  - TAKE PROFIT if market price moved strongly in our favor
  - HOLD otherwise

Usage: python -m kalshi.position_manager
"""

import re
import time
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple
from rich.console import Console
from rich.table import Table

from kalshi.trader import get_positions, get_balance, sell_order, _sign_request, _BASE_URL, _load_credentials
from kalshi.scanner import WEATHER_SERIES, parse_kalshi_bucket, PRECIP_SERIES
try:
    from kalshi.scanner import WEATHER_SERIES_LOW
except ImportError:
    WEATHER_SERIES_LOW = {}
from kalshi.trailing_stop import update_peak, check_trailing_stop, remove_position
from weather.multi_model import fuse_forecast
from config import CONFIDENCE_THRESHOLD, ALERT_THRESHOLD, PAPER_MODE

console = Console()

# --- Base thresholds (adjusted by time-to-settlement) ---
MIN_EDGE_TO_HOLD = 0.04       # exit if edge < 4% (was 7% to enter)
PROFIT_TAKE_PRICE = 0.92      # sell YES if market moved to 92¢+

# Same-day thresholds — forecasts are locked in, act decisively
SAMEDAY_MIN_EDGE = 0.02       # tighter: 2% edge is noise on a locked forecast
SAMEDAY_LOSS_CUT = -0.03      # cut at -3% — it's not coming back

# Fortify thresholds — add to winning positions
FORTIFY_MIN_EDGE = ALERT_THRESHOLD  # edge must still be above entry threshold
FORTIFY_MIN_CONFIDENCE = CONFIDENCE_THRESHOLD  # confidence must meet threshold


# --- Ticker/series lookup ---
_ALL_SERIES = {}
for ticker, info in WEATHER_SERIES.items():
    _ALL_SERIES[ticker] = {**info, "temp_type": "max"}
for ticker, info in WEATHER_SERIES_LOW.items():
    _ALL_SERIES[ticker] = {**info, "temp_type": "min"}
for ticker, info in PRECIP_SERIES.items():
    _ALL_SERIES[ticker] = {**info, "market_type": "precip"}


def _parse_position_ticker(ticker: str) -> Optional[dict]:
    """Extract series info from a position ticker.

    Returns dict with city, lat, lon, unit, and either temp_type or market_type.
    """
    for series_prefix, info in _ALL_SERIES.items():
        if ticker.upper().startswith(series_prefix.upper()):
            return dict(info)
    return None


def _get_market_data(ticker: str) -> Optional[dict]:
    """Fetch market details from Kalshi API for a specific ticker."""
    try:
        _load_credentials()
        path = f"/trade-api/v2/markets/{ticker}"
        headers = _sign_request("GET", path)
        resp = requests.get(f"{_BASE_URL}{path}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("market", {})
    except Exception as e:
        print(f"  Market lookup failed for {ticker}: {e}")
        return None


def _sell_position(ticker: str, side: str, count: int, price_cents: int) -> Optional[dict]:
    """Place a sell order to close a position."""
    path = "/trade-api/v2/portfolio/orders"
    headers = _sign_request("POST", path)
    body = {
        "ticker": ticker,
        "action": "sell",
        "side": side,
        "type": "limit",
        "count": count,
    }
    if side == "yes":
        body["yes_price"] = price_cents
    else:
        body["no_price"] = price_cents

    resp = requests.post(f"{_BASE_URL}{path}", headers=headers, json=body, timeout=15)
    if resp.status_code >= 400:
        print(f"  Sell error: {resp.text}")
    resp.raise_for_status()
    return resp.json()


def evaluate_position(ticker: str, qty: float, market_data: dict) -> dict:
    """Re-evaluate a single position against fresh forecasts.

    Returns dict with action ("hold", "exit", "profit_take"), reason, and details.
    """
    series_info = _parse_position_ticker(ticker)
    if not series_info:
        return {"action": "hold", "reason": "unknown series"}

    side = "yes" if qty > 0 else "no"
    abs_qty = abs(int(qty))

    # Get current market price
    yes_ask = market_data.get("yes_ask")
    no_ask = market_data.get("no_ask")
    if yes_ask is None and no_ask is None:
        # Try dollar format
        yes_ask_d = market_data.get("yes_ask_dollars")
        no_ask_d = market_data.get("no_ask_dollars")
        if yes_ask_d:
            yes_ask = int(float(yes_ask_d) * 100)
        if no_ask_d:
            no_ask = int(float(no_ask_d) * 100)

    if yes_ask is None:
        return {"action": "hold", "reason": "no market price"}

    current_yes_price = yes_ask / 100.0 if yes_ask > 1 else yes_ask

    month = datetime.now(timezone.utc).month

    # Check if this is a precip position
    if series_info.get("market_type") == "precip":
        from weather.multi_model import fuse_precip_forecast
        from kalshi.market_types import parse_precip_bucket
        from weather.forecast import calculate_remaining_month_days

        bucket = parse_precip_bucket(market_data)
        if not bucket:
            return {"action": "hold", "reason": "can't parse precip bucket"}
        threshold = bucket[0]
        city = series_info["city"]
        remaining_days = calculate_remaining_month_days()

        try:
            model_prob, confidence, details = fuse_precip_forecast(
                series_info["lat"], series_info["lon"], city, month,
                threshold=threshold, forecast_days=remaining_days,
            )
        except Exception as e:
            return {"action": "hold", "reason": f"precip forecast error: {e}"}

        # For precip positions, set variables needed by decision logic below
        low, high = threshold, None
        temp_type = "precip"
        days_ahead = 0  # Monthly markets don't have a single target day

    else:
        # Existing temperature path — unchanged
        bucket = parse_kalshi_bucket(market_data)
        if not bucket:
            return {"action": "hold", "reason": "can't parse bucket"}

        low, high = bucket
        city = series_info["city"]
        temp_type = series_info["temp_type"]

        # Calculate days ahead from ticker
        from weather.forecast_logger import parse_ticker_date
        target_date = parse_ticker_date(ticker)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if target_date and target_date == today_str:
            days_ahead = 0
        elif target_date and target_date > today_str:
            target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
            today_dt = datetime.now(timezone.utc).date()
            days_ahead = (target_dt - today_dt).days
        else:
            days_ahead = 0

        # Run fresh forecast
        try:
            model_prob, confidence, details = fuse_forecast(
                series_info["lat"], series_info["lon"], city, month,
                low, high, days_ahead=days_ahead, unit=series_info["unit"], temp_type=temp_type,
            )
        except Exception as e:
            return {"action": "hold", "reason": f"forecast error: {e}"}

    # Calculate current edge from our side's perspective
    if side == "yes":
        edge = model_prob - current_yes_price
        our_price = current_yes_price
    else:
        # For NO: our probability of winning = 1 - yes_price
        # Model says YES prob is model_prob, so NO prob is 1 - model_prob
        no_prob = 1 - model_prob
        no_price = 1 - current_yes_price
        edge = no_prob - no_price
        our_price = no_price

    # Time-aware thresholds: same-day forecasts are locked in
    is_same_day = (days_ahead == 0)
    min_edge = SAMEDAY_MIN_EDGE if is_same_day else MIN_EDGE_TO_HOLD
    loss_threshold = SAMEDAY_LOSS_CUT if is_same_day else -0.05
    time_label = "SAMEDAY" if is_same_day else f"{days_ahead}d"

    result = {
        "side": side,
        "qty": abs_qty,
        "current_price": our_price,
        "model_prob": model_prob if side == "yes" else (1 - model_prob),
        "edge": edge,
        "confidence": confidence,
        "city": city,
        "temp_type": temp_type,
        "bucket": (low, high),
        "days_ahead": days_ahead,
    }

    # --- Update trailing stop tracker ---
    if side == "yes":
        our_price_cents = int(current_yes_price * 100)
    else:
        our_price_cents = int((1 - current_yes_price) * 100)
    update_peak(ticker, side, our_price_cents)

    # --- Decision logic ---

    # Profit take: market moved strongly in our favor
    if side == "yes" and current_yes_price >= PROFIT_TAKE_PRICE:
        result["action"] = "exit"
        result["reason"] = f"PROFIT TAKE — YES at {current_yes_price:.0%}"
        result["sell_price_cents"] = int(current_yes_price * 100) - 1  # sell 1¢ below ask
        return result
    if side == "no" and current_yes_price <= (1 - PROFIT_TAKE_PRICE):
        result["action"] = "exit"
        result["reason"] = f"PROFIT TAKE — NO near certain (YES at {current_yes_price:.0%})"
        result["sell_price_cents"] = int((1 - current_yes_price) * 100) - 1
        return result

    # Trailing stop: price peaked and is dropping back
    trail_reason = check_trailing_stop(ticker, side, our_price_cents, days_ahead)
    if trail_reason:
        result["action"] = "exit"
        result["reason"] = trail_reason
        # Sell at current price minus 1¢ to fill quickly
        result["sell_price_cents"] = max(1, our_price_cents - 1)
        return result

    # Loss cut: model disagrees with our position
    # Same-day: cut at -3% (forecast is locked, it's not coming back)
    # Multi-day: cut at -5% (forecast might still shift)
    if edge < loss_threshold:
        urgency = "LOCKED FORECAST" if is_same_day else "EDGE FLIPPED"
        result["action"] = "exit"
        result["reason"] = f"{urgency} [{time_label}] — {edge:+.1%} against us"
        if side == "yes":
            result["sell_price_cents"] = max(1, int(current_yes_price * 100) - 2)
        else:
            result["sell_price_cents"] = max(1, int((1 - current_yes_price) * 100) - 2)
        return result

    # Edge evaporated: not worth the capital tie-up
    # Same-day: tighter threshold (2%) — forecast won't improve
    # Multi-day: standard threshold (4%)
    if abs(edge) < min_edge:
        result["action"] = "exit"
        result["reason"] = f"EDGE GONE [{time_label}] — only {edge:+.1%} remaining"
        if side == "yes":
            result["sell_price_cents"] = max(1, int(current_yes_price * 100) - 1)
        else:
            result["sell_price_cents"] = max(1, int((1 - current_yes_price) * 100) - 1)
        return result

    # Confidence dropped below threshold (only for multi-day — same-day trusts the forecast)
    if not is_same_day and confidence < CONFIDENCE_THRESHOLD * 0.7:
        result["action"] = "exit"
        result["reason"] = f"LOW CONFIDENCE [{time_label}] — {confidence:.1f}% (need {CONFIDENCE_THRESHOLD * 0.7:.0f}%)"
        if side == "yes":
            result["sell_price_cents"] = max(1, int(current_yes_price * 100) - 1)
        else:
            result["sell_price_cents"] = max(1, int((1 - current_yes_price) * 100) - 1)
        return result

    # Fortify: edge is still strong — add to the position
    if edge >= FORTIFY_MIN_EDGE and confidence >= FORTIFY_MIN_CONFIDENCE and not is_same_day:
        result["action"] = "fortify"
        result["reason"] = f"FORTIFY [{time_label}] — edge {edge:+.1%}, conf {confidence:.1f}%"
        return result

    result["action"] = "hold"
    result["reason"] = f"HOLD [{time_label}] — edge {edge:+.1%}, conf {confidence:.1f}%"
    return result


def run_position_manager():
    """Main loop: evaluate all open positions and execute exits."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"\n[bold cyan]Position Manager — {now}[/bold cyan]\n")

    # Get balance
    bal = get_balance()
    cash = bal.get("balance", 0) / 100.0
    portfolio = bal.get("portfolio_value", 0) / 100.0
    console.print(f"  Cash: ${cash:.2f}  |  Positions: ${portfolio:.2f}  |  Total: ${cash + portfolio:.2f}\n")

    # Get open positions
    positions = get_positions()
    if not positions:
        console.print("[dim]No open positions to manage.[/dim]")
        return

    # Build results table
    table = Table(title=f"Position Evaluation ({len(positions)} positions)")
    table.add_column("Ticker", style="cyan", max_width=28)
    table.add_column("Side", style="bold")
    table.add_column("Qty", justify="right")
    table.add_column("Model", justify="right")
    table.add_column("Market", justify="right")
    table.add_column("Edge", justify="right")
    table.add_column("Action", style="bold")
    table.add_column("Reason", style="dim", max_width=35)

    exits = []
    fortifies = []
    holds = 0
    skips = 0

    for pos in positions:
        ticker = pos.get("ticker", "")
        qty = float(pos.get("position_fp", "0"))
        if qty == 0:
            continue

        # Fetch fresh market data
        market_data = _get_market_data(ticker)
        if not market_data:
            skips += 1
            continue

        # Check if market is still tradeable
        status = market_data.get("status", "")
        if status not in ("open", "active"):
            skips += 1
            continue

        time.sleep(0.15)  # Rate limit

        result = evaluate_position(ticker, qty, market_data)

        side = result.get("side", "?").upper()
        side_color = "green" if side == "YES" else "red"
        abs_qty = result.get("qty", 0)

        model_str = f"{result.get('model_prob', 0):.0%}" if result.get("model_prob") else "—"
        market_str = f"{result.get('current_price', 0):.0%}" if result.get("current_price") else "—"
        edge_val = result.get("edge", 0)
        edge_color = "green" if edge_val > 0 else "red"
        edge_str = f"[{edge_color}]{edge_val:+.1%}[/{edge_color}]" if edge_val else "—"

        action = result["action"].upper()
        action_color = {"EXIT": "red", "FORTIFY": "cyan", "HOLD": "green"}.get(action, "white")
        action_str = f"[{action_color}]{action}[/{action_color}]"

        table.add_row(
            ticker, f"[{side_color}]{side}[/{side_color}]",
            str(abs_qty), model_str, market_str, edge_str,
            action_str, result.get("reason", ""),
        )

        if result["action"] == "exit":
            exits.append((ticker, result))
        elif result["action"] == "fortify":
            fortifies.append((ticker, result, market_data))

    console.print(table)

    if not exits and not fortifies:
        console.print(f"\n[green]All positions healthy. {holds + len(positions) - skips} held, {skips} skipped.[/green]")
        return

    mode_label = "PAPER" if PAPER_MODE else "LIVE"

    # Execute exits
    if exits:
        console.print(f"\n[bold yellow]Executing {len(exits)} exits...[/bold yellow]\n")

        for ticker, result in exits:
            side = result["side"]
            qty = result["qty"]
            price = result.get("sell_price_cents", 1)
            reason = result["reason"]

            console.print(f"  [{mode_label}] SELL {side.upper()} {qty}x {ticker} @ {price}¢")
            console.print(f"    Reason: {reason}")

            if PAPER_MODE:
                console.print(f"    [dim]PAPER MODE — no order sent[/dim]")
                continue

            try:
                resp = sell_order(ticker, side, price, qty)
                order_id = resp.get("order", {}).get("order_id", "unknown")
                status = resp.get("order", {}).get("status", "unknown")
                console.print(f"    [green]Sell order posted: {order_id} ({status})[/green]")
                remove_position(ticker, side)  # Clean up trailing stop state
            except Exception as e:
                console.print(f"    [red]Sell failed: {e}[/red]")

            time.sleep(0.2)

    # Execute fortifications — add to winning positions
    if fortifies:
        from kalshi.trader import execute_kalshi_signal

        console.print(f"\n[bold cyan]Fortifying {len(fortifies)} positions...[/bold cyan]\n")

        for ticker, result, market_data in fortifies:
            city = result.get("city", "?")
            model_prob = result.get("model_prob", 0)
            current_price = result.get("current_price", 0)
            edge = result.get("edge", 0)
            confidence = result.get("confidence", 0)
            existing_qty = result.get("qty", 0)
            side = result["side"]
            direction = "BUY YES" if side == "yes" else "BUY NO"

            console.print(f"  [{mode_label}] FORTIFY {side.upper()} {ticker} (have {existing_qty}) — edge {edge:+.1%}, conf {confidence:.1f}%")

            execute_kalshi_signal(
                market_data, city, model_prob, current_price, edge,
                direction, confidence=confidence,
                existing_contracts=existing_qty,
            )

            time.sleep(0.2)

    console.print(f"\n[bold]Done. {len(exits)} exits attempted, {len(positions) - len(exits) - skips} held.[/bold]")


if __name__ == "__main__":
    run_position_manager()
