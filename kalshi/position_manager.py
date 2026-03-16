"""Intraday Position Manager — EV-gated active management.

Re-evaluates open positions against fresh forecasts. Every action (exit or fortify)
must pass a strict sell-now-vs-hold-to-settlement EV test + liquidity guard.

Default = HOLD (~85-90% of positions settle naturally).
Exit = only when selling now has clear EV advantage over holding.
Fortify = scale winners when edge strengthens and limits allow.

Usage: python -m kalshi.position_manager
"""

import time
from datetime import datetime, timezone
from typing import Optional
from rich.console import Console
from rich.table import Table

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
MAX_SPREAD = 0.22              # relaxed temporarily so we can see FORTIFY / PROFIT TAKE fire

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


def _get_market_data(ticker: str, exchange=None) -> Optional[dict]:
    if exchange is None:
        from exchanges.kalshi import KalshiExchange
        exchange = KalshiExchange()
    try:
        return exchange.get_market(ticker)
    except Exception as e:
        print(f"  Market lookup failed for {ticker}: {e}")
        return None


def _sell_position(ticker: str, side: str, count: int, price_cents: int, exchange=None) -> Optional[dict]:
    if exchange is None:
        from exchanges.kalshi import KalshiExchange
        exchange = KalshiExchange()
    try:
        return exchange.sell_order(ticker, side, price_cents, count)
    except Exception as e:
        if hasattr(e, 'response'):
            print(f"  Sell error: {e.response.text}")
        raise


def _sell_ev_beats_hold(
    current_price: float, our_win_prob: float, qty: int, buffer_pct: float = 0.025
) -> tuple:
    """Strict EV gate: sell-now vs hold-to-settlement after fees.

    Returns (beats: bool, advantage_per_contract: float).
    Sells are posted as maker (resting limit orders), so fee = 0.
    """
    from kalshi.pricing import kalshi_fee
    price_cents = int(current_price * 100)
    fee = kalshi_fee(price_cents, qty, is_taker=False)
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


def run_position_manager(exchange=None):
    """Main loop: evaluate all open positions and execute exits/fortifications."""
    if exchange is None:
        from exchanges.kalshi import KalshiExchange
        exchange = KalshiExchange()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"\n[bold cyan]Position Manager — {now}[/bold cyan]\n")

    bal = exchange.get_balance()
    cash = bal.get("balance", 0) / 100.0
    portfolio = bal.get("portfolio_value", 0) / 100.0
    bankroll = cash + portfolio
    console.print(f"  Cash: ${cash:.2f}  |  Positions: ${portfolio:.2f}  |  Total: ${bankroll:.2f}\n")

    positions = exchange.get_positions()
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

        market_data = _get_market_data(ticker, exchange)
        if not market_data:
            skips += 1
            continue

        status = market_data.get("status", "")
        if status not in ("open", "active"):
            skips += 1
            continue

        time.sleep(0.15)

        result = evaluate_position(ticker, qty, market_data, bankroll)

        # Record decision for analytics
        try:
            from analytics.optimizer import record_manager_action
            record_manager_action(
                ticker=ticker,
                city=result.get("city", ""),
                action=result["action"],
                reason=result.get("reason", ""),
                edge=result.get("edge", 0),
                spread=0,
            )
        except Exception:
            pass

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
        # Build map of resting sell qty per ticker to prevent overselling
        resting_sell_qty = {}
        try:
            resting = exchange.get_orders(status="resting")
            for o in resting:
                if o.get("action") == "sell":
                    t = o.get("ticker", "")
                    remaining = int(float(o.get("remaining_count_fp", "0") or "0"))
                    resting_sell_qty[t] = resting_sell_qty.get(t, 0) + remaining
        except Exception:
            pass

        console.print(f"\n[bold yellow]Executing {len(exits)} exits...[/bold yellow]\n")

        for ticker, result in exits:
            side = result["side"]
            qty = result["qty"]
            price = result.get("sell_price_cents", MIN_SELL_CENTS)
            reason = result["reason"]

            # Subtract resting sells to avoid overselling
            already_selling = resting_sell_qty.get(ticker, 0)
            if already_selling >= qty:
                console.print(f"  [dim]SKIP {ticker} \u2014 {already_selling}x already resting to sell[/dim]")
                continue
            qty = qty - already_selling
            if already_selling > 0:
                console.print(f"  [dim]{ticker}: reducing sell from {result['qty']}x to {qty}x ({already_selling}x already resting)[/dim]")

            console.print(f"  [{mode_label}] SELL {side.upper()} {qty}x {ticker} @ {price}\u00a2")
            console.print(f"    Reason: {reason}")

            if PAPER_MODE:
                console.print(f"    [dim]PAPER MODE \u2014 no order sent[/dim]")
                continue

            try:
                resp = exchange.sell_order(ticker, side, price, qty)
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
        from pipeline.types import Signal, CycleState
        from pipeline.stages import size_position, execute_trade
        from risk.bankroll import BankrollTracker
        from risk.circuit_breaker import CircuitBreaker

        bt = BankrollTracker(initial_bankroll=bankroll)
        cb = CircuitBreaker()

        console.print(f"\n[bold cyan]Fortifying {len(fortifies)} positions...[/bold cyan]\n")

        for ticker, result, market_data in fortifies:
            city = result.get("city", "?")
            model_prob = result.get("model_prob", 0)
            current_price = result.get("current_price", 0)
            edge = result.get("edge", 0)
            confidence = result.get("confidence", 0)
            existing_qty = result.get("qty", 0)
            side = result["side"]

            console.print(f"  [{mode_label}] FORTIFY {side.upper()} {ticker} (have {existing_qty}) \u2014 edge {edge:+.1%}, conf {confidence:.1f}%")

            price_cents = market_data.get("yes_ask") or market_data.get("last_price") or 50
            signal = Signal(
                ticker=ticker, city=city, market_type="kalshi_temp",
                side=side, model_prob=model_prob, market_prob=current_price,
                edge=edge, confidence=confidence,
                price_cents=int(price_cents), days_ahead=result.get("days_ahead", 1),
                yes_bid=market_data.get("yes_bid"), yes_ask=market_data.get("yes_ask"),
                market=market_data,
            )

            from pipeline.config import KALSHI_TEMP
            state = CycleState()
            size = size_position(KALSHI_TEMP, signal, bt, cb, state)
            if size.count > 0:
                trade = execute_trade(KALSHI_TEMP, signal, size, exchange, PAPER_MODE)
                if trade and trade.count > 0:
                    console.print(f"    Fortified: {trade.count}x @ {trade.price_cents}\u00a2 (${trade.cost:.2f})")
            else:
                console.print(f"    [dim]Fortify skipped: {size.limit_reason}[/dim]")

            time.sleep(0.2)

    console.print(f"\n[bold]Done. {len(exits)} exits, {len(fortifies)} fortifies, {len(positions) - len(exits) - len(fortifies) - skips} held.[/bold]")


if __name__ == "__main__":
    run_position_manager()
