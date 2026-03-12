"""Backfill bias DB with historical forecast vs actual comparisons.

Uses Open-Meteo's past forecast data and archive API to compute
bias for the last N days without waiting for daily resolution.

Usage: python -m weather.backfill_bias [--days 14]
"""

import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from weather.multi_model import update_bias, get_bias
from config import CITIES
from kalshi.scanner import WEATHER_SERIES, WEATHER_SERIES_LOW


# Build unified city lookup
CITY_LOOKUP = {}
for key, data in CITIES.items():
    CITY_LOOKUP[key] = {"lat": data["lat"], "lon": data["lon"], "unit": data.get("unit", "f")}
for _ticker, info in WEATHER_SERIES.items():
    if info["city"] not in CITY_LOOKUP:
        CITY_LOOKUP[info["city"]] = {"lat": info["lat"], "lon": info["lon"], "unit": info["unit"]}
for _ticker, info in WEATHER_SERIES_LOW.items():
    if info["city"] not in CITY_LOOKUP:
        CITY_LOOKUP[info["city"]] = {"lat": info["lat"], "lon": info["lon"], "unit": info["unit"]}


def _fetch_open_meteo_history(lat, lon, start_date, end_date, unit, variables):
    """Fetch from Open-Meteo archive API. Returns dict of {variable: [values]}."""
    unit_param = "fahrenheit" if unit == "f" else "celsius"
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&daily={','.join(variables)}"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&start_date={start_date}&end_date={end_date}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    result = {"dates": dates}
    for var in variables:
        result[var] = daily.get(var, [])
    return result


def _fetch_gfs_history(lat, lon, start_date, end_date, unit, variables):
    """Fetch GFS model data for past dates via Open-Meteo forecast API with past_days."""
    unit_param = "fahrenheit" if unit == "f" else "celsius"
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily={','.join(variables)}"
        f"&models=gfs_seamless"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&start_date={start_date}&end_date={end_date}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    result = {"dates": dates}
    for var in variables:
        result[var] = daily.get(var, [])
    return result


def _fetch_ensemble_history(lat, lon, start_date, end_date, unit, variables):
    """Fetch ensemble model means for past dates."""
    unit_param = "fahrenheit" if unit == "f" else "celsius"
    # Ensemble API supports past_days for recent history
    today = datetime.now(timezone.utc).date()
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    past_days = (today - start).days
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily={','.join(variables)}"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&past_days={past_days}"
        f"&forecast_days=1"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    result = {"dates": dates}
    for var in variables:
        # Ensemble returns member columns — average them
        member_keys = sorted(k for k in daily if k.startswith(f"{var}_member"))
        if member_keys:
            n_days = len(daily[member_keys[0]])
            means = []
            for d in range(n_days):
                vals = [daily[k][d] for k in member_keys if daily[k][d] is not None]
                means.append(round(sum(vals) / len(vals), 1) if vals else None)
            result[var] = means
        else:
            result[var] = daily.get(var, [])
    return result


def backfill(days: int = 14):
    """Backfill bias data for the last N days across all tracked cities."""
    today = datetime.now(timezone.utc).date()
    # Don't include today or yesterday (data may not be finalized)
    end_date = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    start_date = (today - timedelta(days=days + 1)).strftime("%Y-%m-%d")

    print(f"Backfilling bias data from {start_date} to {end_date}")
    print(f"Cities: {len(CITY_LOOKUP)}\n")

    total_updates = 0

    for city, info in sorted(CITY_LOOKUP.items()):
        lat, lon, unit = info["lat"], info["lon"], info["unit"]
        variables = ["temperature_2m_max", "temperature_2m_min"]

        print(f"{city}:")

        # Fetch actual observed temps
        try:
            actuals = _fetch_open_meteo_history(lat, lon, start_date, end_date, unit, variables)
        except Exception as e:
            print(f"  Archive API error: {e}")
            continue
        time.sleep(0.2)

        # Fetch GFS/HRRR model data for same period
        try:
            gfs_data = _fetch_gfs_history(lat, lon, start_date, end_date, unit, variables)
        except Exception as e:
            print(f"  GFS API error: {e}")
            gfs_data = None
        time.sleep(0.2)

        # Fetch ensemble means for same period
        try:
            ens_data = _fetch_ensemble_history(lat, lon, start_date, end_date, unit, variables)
        except Exception as e:
            print(f"  Ensemble API error: {e}")
            ens_data = None
        time.sleep(0.2)

        # Compare and update bias for each date
        for i, date_str in enumerate(actuals["dates"]):
            month = int(date_str.split("-")[1])

            for var, temp_suffix in [("temperature_2m_max", ""), ("temperature_2m_min", "_min")]:
                actual_val = actuals[var][i] if i < len(actuals[var]) else None
                if actual_val is None:
                    continue

                # GFS/HRRR bias
                if gfs_data and i < len(gfs_data.get(var, [])):
                    gfs_val = gfs_data[var][i]
                    if gfs_val is not None:
                        model_name = f"hrrr{temp_suffix}"
                        update_bias(city, month, model_name, gfs_val, actual_val)
                        total_updates += 1

                # Ensemble bias
                if ens_data:
                    # Find matching date in ensemble data
                    ens_dates = ens_data.get("dates", [])
                    if date_str in ens_dates:
                        ens_idx = ens_dates.index(date_str)
                        ens_val = ens_data[var][ens_idx] if ens_idx < len(ens_data.get(var, [])) else None
                        if ens_val is not None:
                            model_name = f"ensemble{temp_suffix}"
                            update_bias(city, month, model_name, ens_val, actual_val)
                            total_updates += 1

        # Print current bias state for this city
        for model in ["ensemble", "hrrr", "ensemble_min", "hrrr_min"]:
            bias, n = get_bias(city, month, model)
            if n > 0:
                print(f"  {model}: bias={bias:+.1f}° (n={n})")

    print(f"\nBackfill complete: {total_updates} bias updates across {len(CITY_LOOKUP)} cities.")


if __name__ == "__main__":
    days = 14
    if len(sys.argv) > 1 and sys.argv[1] == "--days":
        days = int(sys.argv[2])
    backfill(days)
