"""Bias Resolver — fetch actual temps and update bias DB after markets settle.

Run daily (after midnight UTC) to resolve yesterday's forecasts.
Usage: python -m weather.resolver
"""

import requests
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from weather.forecast_logger import get_unresolved_forecasts, mark_resolved
from weather.multi_model import update_bias
from config import CITIES
from kalshi.scanner import WEATHER_SERIES


# Build unified city lookup: city_key -> {lat, lon, unit}
CITY_LOOKUP = {}
for key, data in CITIES.items():
    CITY_LOOKUP[key] = {"lat": data["lat"], "lon": data["lon"], "unit": data.get("unit", "f")}
for _ticker, info in WEATHER_SERIES.items():
    if info["city"] not in CITY_LOOKUP:
        CITY_LOOKUP[info["city"]] = {"lat": info["lat"], "lon": info["lon"], "unit": info["unit"]}


def get_actual_temp(lat: float, lon: float, date: str, unit: str = "f", temp_type: str = "max") -> float | None:
    """Fetch actual observed temp from Open-Meteo Historical Weather API.

    Args:
        lat, lon: Coordinates
        date: YYYY-MM-DD format
        unit: "f" or "c"
        temp_type: "max" or "min"

    Returns observed temp or None on failure.
    """
    try:
        unit_param = "fahrenheit" if unit == "f" else "celsius"
        daily_var = "temperature_2m_max" if temp_type == "max" else "temperature_2m_min"
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&daily={daily_var}"
            f"&temperature_unit={unit_param}"
            f"&timezone=auto"
            f"&start_date={date}&end_date={date}"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        temps = data.get("daily", {}).get(daily_var, [])
        if temps and temps[0] is not None:
            return round(float(temps[0]), 1)
        return None
    except Exception as e:
        print(f"  Historical API error for {date}: {e}")
        return None


def run_resolver():
    """Resolve all unresolved forecasts where the target date has passed."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = get_unresolved_forecasts()

    if not rows:
        print("No unresolved forecasts.")
        return

    # Group by (city, target_date)
    grouped = defaultdict(list)
    for city, target_date, model, forecast_temp in rows:
        grouped[(city, target_date)].append((model, forecast_temp))

    resolved_count = 0
    skipped_count = 0

    for (city, target_date), forecasts in sorted(grouped.items()):
        # Only resolve dates that have passed
        if target_date >= today:
            skipped_count += 1
            continue

        city_info = CITY_LOOKUP.get(city)
        if not city_info:
            print(f"  Unknown city {city} — skipping")
            continue

        # Determine if this group has min or max models (or both)
        has_min = any(m.endswith("_min") for m, _ in forecasts)
        has_max = any(not m.endswith("_min") for m, _ in forecasts)

        # Parse month from target date for bias table
        month = int(target_date.split("-")[1])

        # Fetch actual observed temps as needed
        actual_max = None
        actual_min = None
        if has_max:
            actual_max = get_actual_temp(
                city_info["lat"], city_info["lon"], target_date, city_info["unit"], "max"
            )
        if has_min:
            actual_min = get_actual_temp(
                city_info["lat"], city_info["lon"], target_date, city_info["unit"], "min"
            )

        if has_max and actual_max is None and has_min and actual_min is None:
            print(f"  No actual temps for {city} on {target_date} — skipping")
            continue

        print(f"  {city} {target_date}:", end="")
        if actual_max is not None:
            print(f" high={actual_max}°", end="")
        if actual_min is not None:
            print(f" low={actual_min}°", end="")
        print()

        for model, forecast_temp in forecasts:
            if model.endswith("_min"):
                actual = actual_min
            else:
                actual = actual_max
            if actual is None:
                continue
            bias = forecast_temp - actual
            print(f"    {model}: forecast={forecast_temp}° bias={bias:+.1f}°")
            update_bias(city, month, model, forecast_temp, actual)

        mark_resolved(city, target_date)
        resolved_count += 1
        time.sleep(0.2)  # Rate limit historical API

    print(f"\nResolved {resolved_count} city-dates, {skipped_count} still pending (future dates).")


if __name__ == "__main__":
    run_resolver()
