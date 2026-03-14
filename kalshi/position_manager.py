"""Intraday Position Manager — EV-gated active management.

Re-evaluates open positions against fresh forecasts. Every action (exit or fortify)
must pass a strict sell-now-vs-hold-to-settlement EV test + liquidity guard.

Default = HOLD (~85-90% of positions settle naturally).
Exit = only when selling now has clear EV advantage over holding.
Fortify = scale winners when edge strengthens and limits allow.

Usage: python -m kalshi.position_manager
"""

import re
import time
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple
from rich.console import Console
from rich.table import Table

from kalshi.trader import get_positions, get_balance, sell_order, get_orders, _sign_request, _BASE_URL, _load_credentials
from kalshi.scanner import WEATHER_SERIES, parse_kalshi_bucket, PRECIP_SERIES
try:
    from kalshi.scanner import WEATHER_SERIES_LOW
except ImportError:
    WEATHER_SERIES_LOW = {}
from kalshi.trailing_stop import update_peak, check_trailing_stop, remove_position
from kalshi.fill_tracker import record_fill
from weather.multi_model import fuse_forecast
from config import CONFIDENCE_THRESHOLD, ALERT_THRESHOLD, PAPER_MODE
from risk.position_limits import check_limits, MIN_ORDER_DOLLARS

console = Console()

# --- Thresholds ---
MIN_EDGE_TO_HOLD = 0.04
PROFIT_TAKE_PRICE = 0.92
SAMEDAY_MIN_EDGE = 0.02
SAMEDAY_LOSS_CUT = -0.03
MIN_EXIT_PROCEEDS = 1.00
MIN_SELL_CENTS = 12
FORTIFY_MIN_EDGE = ALERT_THRESHOLD
FORTIFY_MIN_CONFIDENCE = CONFIDENCE_THRESHOLD
MAX_SPREAD = 0.12

# --- Ticker/series lookup ---
_ALL_SERIES = {}
for ticker, info in WEATHER_SERIES.items():
    _ALL_SERIES[ticker] = {**info, "temp_type": "max"}
for ticker, info in WEATHER_SERIES_LOW.items():
    _ALL_SERIES[ticker] = {**info, "temp_type": "min"}
for ticker, info in PRECIP_SERIES.items():
    _ALL_SERIES[ticker] = {**info, "market_type": "precip"}


def _parse_position_ticker(ticker: str) -> Optional[dict]:
    for series_prefix, info in _ALL_SERIES.items():
        if ticker.upper().startswith(series_prefix.upper()):
            return dict(info)
    return None


def _get_market_data(ticker: str) -> Optional[dict]:
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


def _sell_ev_beats_hold(
    current_price: float, our_win_prob: float, qty: int, buffer_pct: float = 0.025
) -> tuple:
    """Strict EV gate: sell-now vs hold-to-settlement after fees.

    Returns (beats: bool, advantage_per_contract: float).
    """
    fee = 0.035 + (qty * 0.01)
    sell_ev = current_price * qty - fee
    hold_ev = our_win_prob * qty

    advantage = sell_ev - hold_ev
    beats = advantage > max(1.0, buffer_pct * qty)
    return beats, advantage / qty if qty > 0 else 0


def evaluate_position(ticker: str, qty: float, market_data: dict, bankroll: float) -> dict:
    """EV-gated active management — every exit must beat hold-to-settlement."""
    series_info = _parse_position_ticker(ticker)
    if not series_info:
        return {"action": "hold", "reason": "unknown series"}

    side = "yes" if qty > 0 else "no"
    abs_qty = abs(int(qty))

    # --- Price + liquidity guard ---
    yes_ask = market_data.get("yes_ask")
    no_ask = market_data.get("no_ask")
    if yes_ask is None and no_ask is None:
        yes_ask_d = market_data.get("yes_ask_dollars")
        no_ask_d = market_data.get("no_ask_dollars")
        if yes_ask_d:
            yes_ask = int(float(yes_ask_d) * 100)
        if no_ask_d:
            no_ask = int(float(no_ask_d) * 100)

    if yes_ask is None:
        return {"action": "hold", "reason": "no market price"}

    current_yes_price = yes_ask / 100.0 if yes_ask > 1 else yes_ask

    # Spread guard — don't trade into thin books
    if no_ask is not None and no_ask > 0:
        spread = abs((yes_ask or 0) - (no_ask or 0)) / 100.0
    else:
        spread = 0.15  # conservative fallback when no_ask unavailable
    if spread > MAX_SPREAD:
        return {"action": "hold", "reason": f"spread too wide ({spread:.0%})"}

    # --- Fresh forecast ---
    month = datetime.now(timezone.utc).month

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

        low, high = threshold, None
        temp_type = "precip"
        days_ahead = 0
    else:
        bucket = parse_kalshi_bucket(market_data)
        if not bucket:
            return {"action": "hold", "reason": "can't parse bucket"}

        low, high = bucket
        city = series_info["city"]
        temp_type = series_info["temp_type"]

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

        try:
            model_prob, confidence, details = fuse_forecast(
                series_info["lat"], series_info["lon"], city, month,
                low, high, days_ahead=days_ahead, unit=series_info["unit"], temp_type=temp_type,
            )
        except Exception as e:
            return {"action": "hold", "reason": f"forecast error: {e}"}

    # --- Edge calculation ---
    if side == "yes":
        edge = model_prob - current_yes_price
        our_price = current_yes_price
        our_win_prob = model_prob
    else:
        no_prob = 1 - model_prob
        no_price = 1 - current_yes_price
        edge = no_prob - no_price
        our_price = no_price
        our_win_prob = no_prob

    is_same_day = (days_ahead == 0)
    time_label = "SAMEDAY" if is_same_day else f"{days_ahead}d"

    result = {
        "side": side, "qty": abs_qty, "current_price": our_price,
        "model_prob": our_win_prob, "edge": edge, "confidence": confidence,
        "city": city, "temp_type": temp_type, "bucket": (low, high),
        "days_ahead": days_ahead,
    }

    # --- Trailing stop tracker ---
    our_price_cents = int(our_price * 100)
    update_peak(ticker, side, our_price_cents)

    # --- Trailing stop exit (EV-gated) ---
    if not is_same_day:
        trail_reason = check_trailing_stop(ticker, side, our_price_cents, days_ahead)
        if trail_reason:
            beats, adv = _sell_ev_beats_hold(our_price, our_win_prob, abs_qty)
            if beats:
                result["action"] = "exit"
                result["reason"] = f"TRAILING STOP — EV adv {adv:+.1%}"
                result["sell_price_cents"] = max(MIN_SELL_CENTS, our_price_cents - 1)
                return result

    # --- EV comparison (core gatekeeper) ---
    beats_ev, ev_adv = _sell_ev_beats_hold(our_price, our_win_prob, abs_qty)

    # --- Profit take (multi-day only, EV-gated) ---
    if not is_same_day:
        profit_threshold = 0.90 if days_ahead <= 3 else 0.88
        if ((side == "yes" and current_yes_price >= profit_threshold) or
            (side == "no" and current_yes_price <= (1 - profit_threshold))):
            if beats_ev and edge > 0.02:
                sell_price = max(MIN_SELL_CENTS, int(our_price * 100) - 1)
                result["action"] = "exit"
                result["reason"] = f"PROFIT TAKE — {side.upper()} @ {our_price:.0%} (EV+{ev_adv:.1%})"
                result["sell_price_cents"] = sell_price
                return result

    # --- Fortify: scale winners when edge strengthens ---
    if (edge > FORTIFY_MIN_EDGE + 0.02 and
        confidence >= FORTIFY_MIN_CONFIDENCE and
        not is_same_day and
        not beats_ev):

        limit_result = check_limits(
            order_dollars=abs_qty * 0.85,
            bankroll=bankroll,
            total_exposure=abs_qty * 0.85,
            scan_spent=0.0,
            city_day_spent=0.0,
        )
        if limit_result.allowed_dollars >= MIN_ORDER_DOLLARS * 2:
            result["action"] = "fortify"
            result["reason"] = f"FORTIFY — edge {edge:+.1%}, conf {confidence:.0f}% (+${limit_result.allowed_dollars:.0f})"
            return result

    # --- Defensive reversal (very high bar) ---
    reversal_threshold = -0.08 if not is_same_day else -0.15
    if edge < reversal_threshold and confidence > 70 and beats_ev:
        sell_price = max(MIN_SELL_CENTS, int(our_price * 100) - 1)
        result["action"] = "exit"
        result["reason"] = f"REVERSAL — edge {edge:+.1%} (EV+{ev_adv:.1%})"
        result["sell_price_cents"] = sell_price
        return result

    # --- Default: HOLD ---
    result["action"] = "hold"
    result["reason"] = f"HOLD [{time_label}] — edge {edge:+.1%}, conf {confidence:.1f}% (EV {ev_adv:+.1%})"
    return result


def run_position_manager():
    """Main loop: evaluate all open positions and execute exits/fortifications."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"\n[bold cyan]Position Manager — {now}[/bold cyan]\n")

    bal = get_balance()
    cash = bal.get("balance", 0) / 100.0
    portfolio = bal.get("portfolio_value", 0) / 100.0
    bankroll = cash + portfolio
    console.print(f"  Cash: ${cash:.2f}  |  Positions: ${portfolio:.2f}  |  Total: ${bankroll:.2f}\n")

    positions = get_positions()
    if not positions:
        console.print("[dim]No open positions to manage.[/dim]")
        return

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
    skips = 0

    for pos in positions:
        ticker = pos.get("ticker", "")
        qty = float(pos.get("position_fp", "0"))
        if qty == 0:
            continue

        market_data = _get_market_data(ticker)
        if not market_data:
            skips += 1
            continue

        status = market_data.get("status", "")
        if status not in ("open", "active"):
            skips += 1
            continue

        time.sleep(0.15)

        result = evaluate_position(ticker, qty, market_data, bankroll)

        side = result.get("side", "?").upper()
        side_color = "green" if side == "YES" else "red"
        abs_qty = result.get("qty", 0)

        model_str = f"{result.get('model_prob', 0):.0%}" if result.get("model_prob") else "\u2014"
        market_str = f"{result.get('current_price', 0):.0%}" if result.get("current_price") else "\u2014"
        edge_val = result.get("edge", 0)
        edge_color = "green" if edge_val > 0 else "red"
        edge_str = f"[{edge_color}]{edge_val:+.1%}[/{edge_color}]" if edge_val else "\u2014"

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
        console.print(f"\n[green]All positions holding. {len(positions) - skips} evaluated, {skips} skipped.[/green]")
        return

    mode_label = "PAPER" if PAPER_MODE else "LIVE"

    if exits:
        try:
            resting = get_orders(status="resting")
            resting_tickers = {o.get("ticker", "") for o in resting if o.get("action") == "sell"}
        except Exception:
            resting_tickers = set()

        console.print(f"\n[bold yellow]Executing {len(exits)} exits...[/bold yellow]\n")

        for ticker, result in exits:
            side = result["side"]
            qty = result["qty"]
            price = result.get("sell_price_cents", MIN_SELL_CENTS)
            reason = result["reason"]

            if ticker in resting_tickers:
                console.print(f"  [dim]SKIP {ticker} \u2014 resting sell order already exists[/dim]")
                continue

            console.print(f"  [{mode_label}] SELL {side.upper()} {qty}x {ticker} @ {price}\u00a2")
            console.print(f"    Reason: {reason}")

            if PAPER_MODE:
                console.print(f"    [dim]PAPER MODE \u2014 no order sent[/dim]")
                continue

            try:
                resp = sell_order(ticker, side, price, qty)
                order_id = resp.get("order", {}).get("order_id", "unknown")
                status = resp.get("order", {}).get("status", "unknown")
                console.print(f"    [green]Sell order posted: {order_id} ({status})[/green]")
                remove_position(ticker, side)

                record_fill(
                    db_path="data/trades.db",
                    order_id=order_id,
                    ticker=ticker,
                    side=f"sell_{side}",
                    limit_price=price,
                    fill_price=price,
                    fill_qty=qty,
                    fill_time=datetime.now(timezone.utc).isoformat(),
                    city=result.get("city", ""),
                )
            except Exception as e:
                console.print(f"    [red]Sell failed: {e}[/red]")

            time.sleep(0.2)

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

            console.print(f"  [{mode_label}] FORTIFY {side.upper()} {ticker} (have {existing_qty}) \u2014 edge {edge:+.1%}, conf {confidence:.1f}%")

            execute_kalshi_signal(
                market_data, city, model_prob, current_price, edge,
                direction, confidence=confidence,
                existing_contracts=existing_qty,
            )

            time.sleep(0.2)

    console.print(f"\n[bold]Done. {len(exits)} exits, {len(fortifies)} fortifies, {len(positions) - len(exits) - len(fortifies) - skips} held.[/bold]")


if __name__ == "__main__":
    run_position_manager()
