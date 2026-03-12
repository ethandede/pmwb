import json
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from config import CITIES, EDGE_THRESHOLD, SHOW_THRESHOLD, DUTCH_BOOK_THRESHOLD, ALERT_THRESHOLD, PAPER_MODE, CONFIDENCE_THRESHOLD
from weather.forecast import get_ensemble_max_temps, get_bucket_prob
from weather.multi_model import fuse_forecast
from polymarket.gamma import get_active_weather_markets, parse_bucket
from alerts.telegram_alert import send_signal_alert
from logging_utils import log_signal
from trading.trader import execute_signal
from kalshi.scanner import get_kalshi_weather_markets, parse_kalshi_bucket, get_kalshi_precip_markets
from kalshi.trader import execute_kalshi_signal, reset_scan_budget
from weather.forecast_logger import log_forecast, parse_ticker_date
from weather.multi_model import fuse_precip_forecast
from weather.forecast import calculate_remaining_month_days
from config import MAX_ENSEMBLE_HORIZON_DAYS

console = Console()


def _buckets_overlap(lo1, hi1, lo2, hi2) -> bool:
    """Check if two temperature buckets overlap or are adjacent (within 2°)."""
    # Convert None bounds to extremes
    a_lo = lo1 if lo1 is not None else -999
    a_hi = hi1 if hi1 is not None else 999
    b_lo = lo2 if lo2 is not None else -999
    b_hi = hi2 if hi2 is not None else 999
    # Overlap or adjacent within 2 degrees
    return a_lo < b_hi + 2 and b_lo < a_hi + 2


def run_scanner():
    console.print("[bold cyan]Weather Edge Scanner — Bidirectional Signals[/bold cyan]\n")
    reset_scan_budget()

    console.print("Fetching weather markets from Gamma API...")
    markets = get_active_weather_markets()
    console.print(f"Found {len(markets)} weather-related markets\n")

    if not markets:
        console.print("[yellow]No weather markets found. Try again later or expand keywords.[/yellow]")
        return

    table = Table(title="Signal Opportunities (|edge| >= 7%)")
    table.add_column("Market", style="cyan", max_width=45)
    table.add_column("City", style="green")
    table.add_column("Model", justify="right")
    table.add_column("Market", justify="right")
    table.add_column("Edge", justify="right", style="bold")
    table.add_column("Conf", justify="right")
    table.add_column("Signal", style="bold")
    table.add_column("Note", style="dim")

    signals_found = 0

    for market in markets:
        q = market["question"]
        outcome_prices = market.get("outcomePrices", "")

        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                continue

        if not outcome_prices:
            continue

        yes_price = float(outcome_prices[0])

        # Find city with unit support
        q_lower = q.lower()
        city_key = None
        for key, data in CITIES.items():
            if any(kw in q_lower for kw in data["keywords"]):
                city_key = key
                break
        if not city_key:
            continue
        city = CITIES[city_key]
        unit = city.get("unit", "f")

        # Parse bucket from question
        bucket = parse_bucket(q)
        if not bucket:
            console.print(f"[dim]Skipped (no bucket parse): {q}[/dim]")
            continue
        low, high = bucket

        # Get ensemble forecast with correct unit
        try:
            temps = get_ensemble_max_temps(city["lat"], city["lon"], days_ahead=1, unit=unit)
        except Exception as e:
            console.print(f"[red]Forecast error for {city_key}: {e}[/red]")
            continue

        if not temps:
            continue

        model_prob = get_bucket_prob(temps, low, high)
        edge = model_prob - yes_price

        # Dutch book quick check
        sum_prices = sum(float(p) for p in outcome_prices) if len(outcome_prices) > 1 else 1.0
        dutch = sum_prices < DUTCH_BOOK_THRESHOLD

        note = ""
        if dutch:
            note = f"DUTCH ({sum_prices:.3f})"

        if abs(edge) >= SHOW_THRESHOLD or dutch:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            arrow = "[green]^ BUY YES[/green]" if edge > 0 else "[red]v SELL YES[/red]"
            color = "green" if edge > 0 else "red"
            edge_str = f"[{color}]{edge:+.1%}[/{color}]"

            display_q = q[:42] + "..." if len(q) > 45 else q
            table.add_row(
                display_q,
                city_key.replace("_", " ").title(),
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                edge_str,
                "[dim]—[/dim]",
                arrow,
                note,
            )
            signals_found += 1

            # Log every signal to CSV
            log_signal(q, city_key, model_prob, yes_price, edge, direction, dutch, PAPER_MODE, confidence=0, ticker="")

            # Polymarket trading disabled (account geo-blocked)
            # Signals still logged to CSV for analysis

    # --- Kalshi Markets ---
    console.print("\nFetching weather markets from Kalshi API...")
    kalshi_markets = get_kalshi_weather_markets()
    console.print(f"Found {len(kalshi_markets)} Kalshi temp markets\n")

    month = datetime.now(timezone.utc).month
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Track positions per city/date to avoid correlated bets
    city_date_positions = {}  # (city, date, temp_type) -> list of (side, bucket)

    for market in kalshi_markets:
        title = market.get("title", "") + " " + market.get("subtitle", "")
        city_key = market["_city"]
        unit = market["_unit"]

        yes_ask = market.get("yes_ask")
        if yes_ask is None:
            continue
        yes_price = yes_ask / 100.0  # Kalshi prices are in cents

        bucket = parse_kalshi_bucket(market)
        if not bucket:
            continue
        low, high = bucket

        # Calculate days ahead from ticker date
        ticker = market.get("ticker", "")
        target_date = parse_ticker_date(ticker)
        if target_date and target_date == today_str:
            days_ahead = 0  # Same-day market — use current observations
        elif target_date and target_date > today_str:
            from datetime import timedelta
            target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
            today_dt = datetime.now(timezone.utc).date()
            days_ahead = (target_dt - today_dt).days
        else:
            days_ahead = 1  # Fallback

        # Multi-model fusion
        temp_type = market.get("_temp_type", "max")
        try:
            model_prob, confidence, details = fuse_forecast(
                market["_lat"], market["_lon"], city_key, month,
                low, high, days_ahead=days_ahead, unit=unit, temp_type=temp_type,
            )
        except Exception as e:
            console.print(f"[red]Fusion error for {city_key}: {e}[/red]")
            continue

        # Log per-model forecast temps for bias resolution
        ticker = market.get("ticker", "")
        target_date = parse_ticker_date(ticker)
        if target_date:
            for model_name in ["ensemble", "noaa", "hrrr"]:
                temp_val = details.get(model_name, {}).get("temp")
                if temp_val is not None:
                    log_forecast(city_key, target_date, model_name, temp_val, temp_type)

        edge = model_prob - yes_price

        if abs(edge) >= SHOW_THRESHOLD:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            arrow = "[green]^ BUY YES[/green]" if edge > 0 else "[red]v SELL YES[/red]"
            color = "green" if edge > 0 else "red"
            edge_str = f"[{color}]{edge:+.1%}[/{color}]"

            n_models = details.get("models_used", 1)
            conf_color = "green" if confidence >= CONFIDENCE_THRESHOLD else "yellow" if confidence >= 50 else "red"
            conf_str = f"[{conf_color}]{confidence}% ({n_models}m)[/{conf_color}]"

            display_q = title[:42] + "..." if len(title) > 45 else title
            type_label = "Lo" if temp_type == "min" else "Hi"
            table.add_row(
                display_q,
                f"{city_key.replace('_', ' ').title()} (K)",
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                edge_str,
                conf_str,
                arrow,
                f"K-{type_label}",
            )
            signals_found += 1

            # Log to CSV
            log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE, confidence=confidence, ticker=ticker)

            # Only trade when confidence meets threshold
            if abs(edge) >= ALERT_THRESHOLD and confidence >= CONFIDENCE_THRESHOLD:
                # Anti-correlation: don't bet YES and NO on overlapping buckets
                pos_key = (city_key, target_date or "unknown", temp_type)
                side = "yes" if edge > 0 else "no"
                existing = city_date_positions.get(pos_key, [])

                # Only skip if opposite side on an overlapping/adjacent bucket
                skip = False
                for prev_side, prev_bucket in existing:
                    if prev_side != side:
                        p_lo, p_hi = prev_bucket
                        # Check for overlap between buckets
                        if _buckets_overlap(low, high, p_lo, p_hi):
                            skip = True
                            console.print(f"[dim]  Skipping {ticker} — conflicts with existing {prev_side.upper()} on {city_key}[/dim]")
                            break

                if not skip:
                    city_date_positions.setdefault(pos_key, []).append((side, (low, high)))
                    send_signal_alert(
                        title, city_key + " (Kalshi)", model_prob, yes_price, edge,
                        f"{direction} (Conf {confidence}%, {n_models} models)"
                    )
                    execute_kalshi_signal(market, city_key, model_prob, yes_price, edge, direction, confidence)

    # --- Kalshi Precipitation Markets ---
    precip_markets = get_kalshi_precip_markets()
    console.print(f"\n  Found {len(precip_markets)} precip markets")

    for market in precip_markets:
        ticker = market.get("ticker", "")
        title = market.get("title", ticker)
        city_key = market.get("_city", "unknown")
        threshold = market.get("_threshold", 0.0)

        yes_ask = market.get("yes_ask")
        if yes_ask is None:
            continue
        yes_price = yes_ask / 100.0  # Kalshi prices are in cents

        # Monthly contracts: check horizon limit
        remaining_days = calculate_remaining_month_days()
        if remaining_days > MAX_ENSEMBLE_HORIZON_DAYS:
            continue  # Skip — beyond ensemble forecast horizon

        month = datetime.now(timezone.utc).month

        try:
            model_prob, confidence, details = fuse_precip_forecast(
                market["_lat"], market["_lon"], city_key, month,
                threshold=threshold, forecast_days=remaining_days,
            )
        except Exception as e:
            console.print(f"[red]Precip fusion error for {city_key}: {e}[/red]")
            continue

        edge = model_prob - yes_price

        if abs(edge) >= SHOW_THRESHOLD:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            edge_color = "green" if edge > 0 else "red"
            table.add_row(
                title[:40], city_key, f"{model_prob:.0%}", f"{yes_price:.0%}",
                f"[{edge_color}]{edge:+.1%}[/{edge_color}]",
                f"[bold]{direction}[/bold]", f"{confidence}%", ticker,
            )
            log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE, confidence=confidence, ticker=ticker)
            signals_found += 1

            if abs(edge) >= ALERT_THRESHOLD and confidence >= CONFIDENCE_THRESHOLD:
                execute_kalshi_signal(market, city_key, model_prob, yes_price, edge, direction, confidence=confidence)

    if signals_found:
        console.print(table)
    else:
        console.print("[yellow]No signals >= 7% edge this scan.[/yellow]")

    total_markets = len(markets) + len(kalshi_markets) + len(precip_markets)
    console.print(f"\n[dim]Scanned {total_markets} markets (Polymarket: {len(markets)}, Kalshi temp: {len(kalshi_markets)}, Kalshi precip: {len(precip_markets)}), {signals_found} signals found.[/dim]")
    console.print(f"[dim]Signals >= 10.5% are trade-worthy. Run every 15 min.[/dim]")
