import json
from rich.console import Console
from rich.table import Table
from config import CITIES, EDGE_THRESHOLD, SHOW_THRESHOLD, DUTCH_BOOK_THRESHOLD, ALERT_THRESHOLD, PAPER_MODE
from weather.forecast import get_ensemble_max_temps, get_bucket_prob
from polymarket.gamma import get_active_weather_markets, parse_bucket
from alerts.telegram_alert import send_signal_alert
from logging_utils import log_signal
from trading.trader import execute_signal
from kalshi.scanner import get_kalshi_weather_markets, parse_kalshi_bucket

console = Console()

def run_scanner():
    console.print("[bold cyan]Polymarket Weather Edge Scanner — Bidirectional Signals[/bold cyan]\n")

    console.print("Fetching weather markets from Gamma API...")
    markets = get_active_weather_markets()
    console.print(f"Found {len(markets)} weather-related markets\n")

    if not markets:
        console.print("[yellow]No weather markets found. Try again later or expand keywords.[/yellow]")
        return

    table = Table(title="Signal Opportunities (|edge| >= 7%)")
    table.add_column("Market", style="cyan", max_width=55)
    table.add_column("City", style="green")
    table.add_column("Model Prob", justify="right")
    table.add_column("Market Prob", justify="right")
    table.add_column("Edge", justify="right", style="bold")
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

            display_q = q[:52] + "..." if len(q) > 55 else q
            table.add_row(
                display_q,
                city_key.replace("_", " ").title(),
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                edge_str,
                arrow,
                note,
            )
            signals_found += 1

            # Log every signal to CSV
            log_signal(q, city_key, model_prob, yes_price, edge, direction, dutch, PAPER_MODE)

            # Send Telegram alert + execute on strong edges
            if abs(edge) >= ALERT_THRESHOLD:
                send_signal_alert(q, city_key, model_prob, yes_price, edge, direction)
                execute_signal(market, city_key, model_prob, yes_price, edge, direction)

    # --- Kalshi Markets ---
    console.print("\nFetching weather markets from Kalshi API...")
    kalshi_markets = get_kalshi_weather_markets()
    console.print(f"Found {len(kalshi_markets)} Kalshi weather markets\n")

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

        try:
            temps = get_ensemble_max_temps(market["_lat"], market["_lon"], days_ahead=1, unit=unit)
        except Exception as e:
            console.print(f"[red]Forecast error for {city_key}: {e}[/red]")
            continue

        if not temps:
            continue

        model_prob = get_bucket_prob(temps, low, high)
        edge = model_prob - yes_price

        dutch = False  # Not applicable for single Kalshi markets

        if abs(edge) >= SHOW_THRESHOLD:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            arrow = "[green]^ BUY YES[/green]" if edge > 0 else "[red]v SELL YES[/red]"
            color = "green" if edge > 0 else "red"
            edge_str = f"[{color}]{edge:+.1%}[/{color}]"

            display_q = title[:52] + "..." if len(title) > 55 else title
            table.add_row(
                display_q,
                f"{city_key.capitalize()} (K)",
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                edge_str,
                arrow,
                "Kalshi",
            )
            signals_found += 1

            # Log to CSV
            log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE)

            # Alert on strong edges
            if abs(edge) >= ALERT_THRESHOLD:
                send_signal_alert(title, city_key + " (Kalshi)", model_prob, yes_price, edge, direction)

    if signals_found:
        console.print(table)
    else:
        console.print("[yellow]No signals >= 7% edge this scan.[/yellow]")

    total_markets = len(markets) + len(kalshi_markets)
    console.print(f"\n[dim]Scanned {total_markets} markets (Polymarket: {len(markets)}, Kalshi: {len(kalshi_markets)}), {signals_found} signals found.[/dim]")
    console.print(f"[dim]Signals >= 10.5% are trade-worthy. Run every 15 min.[/dim]")
