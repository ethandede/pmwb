from rich.console import Console
from rich.table import Table
from config import CITIES, EDGE_THRESHOLD, DUTCH_BOOK_THRESHOLD
from weather.forecast import get_ensemble_max_temps_f, get_bucket_prob
from polymarket.gamma import get_active_weather_markets, parse_bucket

console = Console()

def run_scanner():
    console.print("[bold cyan]Polymarket Weather Edge Scanner[/bold cyan]\n")

    console.print("Fetching weather markets from Gamma API...")
    markets = get_active_weather_markets()
    console.print(f"Found {len(markets)} weather-related markets\n")

    if not markets:
        console.print("[yellow]No weather markets found. Try again later or expand keywords.[/yellow]")
        return

    table = Table(title="Strong Signals (Edge >= 10.5%)")
    table.add_column("Market", style="cyan", max_width=80)
    table.add_column("City", style="green")
    table.add_column("Model Prob", justify="right")
    table.add_column("Market Prob", justify="right")
    table.add_column("Edge", style="bold magenta", justify="right")
    table.add_column("Note", style="dim")

    signals_found = 0

    for market in markets:
        q = market["question"]
        outcome_prices = market.get("outcomePrices", "")

        # outcomePrices can be a JSON string like "[\"0.5\",\"0.5\"]" or a list
        if isinstance(outcome_prices, str):
            import json
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                continue

        if not outcome_prices:
            continue

        yes_price = float(outcome_prices[0])  # Yes = the bucket happening

        # Find city
        city_key = next(
            (k for k, v in CITIES.items() if any(kw in q.lower() for kw in v["keywords"])),
            None,
        )
        if not city_key:
            continue
        city = CITIES[city_key]

        # Parse bucket from question
        bucket = parse_bucket(q)
        if not bucket:
            continue
        low, high = bucket

        # Get ensemble forecast
        try:
            temps_f = get_ensemble_max_temps_f(city["lat"], city["lon"], days_ahead=1)
        except Exception as e:
            console.print(f"[red]Forecast error for {city_key}: {e}[/red]")
            continue

        if not temps_f:
            continue

        model_prob = get_bucket_prob(temps_f, low, high)
        edge = model_prob - yes_price

        # Dutch book quick check
        sum_prices = sum(float(p) for p in outcome_prices) if len(outcome_prices) > 1 else 1.0
        dutch = sum_prices < DUTCH_BOOK_THRESHOLD

        note = ""
        if dutch:
            note = f"DUTCH ({sum_prices:.3f})"

        if edge >= EDGE_THRESHOLD or dutch:
            edge_str = f"+{edge:.1%}" if edge > 0 else f"{edge:.1%}"
            display_q = q[:77] + "..." if len(q) > 80 else q
            table.add_row(
                display_q,
                city_key.capitalize(),
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                f"[bold]{edge_str}[/bold]",
                note,
            )
            signals_found += 1

    if signals_found:
        console.print(table)
    else:
        console.print("[yellow]No strong signals this scan. Run again in 15 min.[/yellow]")

    console.print(f"\n[dim]Scanned {len(markets)} markets, {signals_found} signals found.[/dim]")
    console.print(f"[dim]Ensemble members: forecasts use ~50 weather model runs for empirical probabilities.[/dim]")
