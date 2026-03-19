"""CAISO hub scanning — fetches solar irradiance per hub + shared CAISO market data.

Caches CAISO API responses for 5 minutes to avoid redundant calls across hub scans.
CAISO uses public CSV endpoints (no auth); falls back to defaults on failure.
"""

import csv
import io
import time
import requests
from config import CAISO_HUBS
from weather.multi_model import get_caiso_solar_signal

# Module-level cache for CAISO market data (shared across all hub scans)
_caiso_cache: dict = {}
_caiso_cache_time: float = 0.0
_CACHE_TTL = 300  # 5 minutes

_FUELSOURCE_URL = "https://www.caiso.com/outlook/current/fuelsource.csv"
_DEMAND_URL = "https://www.caiso.com/outlook/current/demand.csv"


def _parse_latest_row(csv_text: str) -> dict:
    """Parse a CAISO CSV and return the last non-empty row as a dict."""
    reader = csv.DictReader(io.StringIO(csv_text))
    last_row = None
    for row in reader:
        # Skip rows where all values are empty
        if any(v.strip() for k, v in row.items() if k != "Time"):
            last_row = row
    return last_row or {}


def _fetch_caiso_market_data() -> dict:
    """Fetch CAISO solar generation and load from public CSVs.

    Returns cached data if within TTL. Falls back to defaults on failure.
    """
    global _caiso_cache, _caiso_cache_time

    if _caiso_cache and (time.time() - _caiso_cache_time) < _CACHE_TTL:
        return _caiso_cache

    result = {"price": 40.0, "solar_mw": 10000.0, "load_forecast": 27_000.0}

    # Fuel source CSV — extract current solar generation
    try:
        r = requests.get(_FUELSOURCE_URL, timeout=15)
        r.raise_for_status()
        row = _parse_latest_row(r.text)
        solar_val = row.get("Solar", "").strip()
        if solar_val and solar_val != "":
            result["solar_mw"] = float(solar_val)
    except Exception as e:
        print(f"  CAISO fuels fetch error: {e}")

    # Demand CSV — extract current demand (or forecast fallback)
    try:
        r = requests.get(_DEMAND_URL, timeout=15)
        r.raise_for_status()
        row = _parse_latest_row(r.text)
        # Prefer current demand, fall back to hour-ahead then day-ahead forecast
        for col in ("Current demand", "Hour ahead forecast", "Day ahead forecast"):
            val = row.get(col, "").strip()
            if val:
                result["load_forecast"] = float(val)
                break
    except Exception as e:
        print(f"  CAISO demand fetch error: {e}")

    # Alert if all endpoints failed (using defaults for everything)
    if result["price"] == 40.0 and result["solar_mw"] == 10000.0 and result["load_forecast"] == 27_000.0:
        try:
            from alerts.telegram_alert import send_alert
            send_alert(
                "CAISO APIs Down",
                "All CAISO endpoints returned errors. Using fallback defaults.",
                dedup_key="caiso_api_down",
            )
        except Exception:
            pass

    _caiso_cache = result
    _caiso_cache_time = time.time()
    return result


def fetch_caiso_markets() -> list[dict]:
    """Return market data for all active CAISO hubs.

    One dict per active hub with current price, solar data, and hub metadata.
    """
    market_data = _fetch_caiso_market_data()
    hub_prices = market_data.get("hub_prices", {})
    grid_avg = market_data.get("price", 40.0)

    markets = []
    for hub_key, info in CAISO_HUBS.items():
        if not info.get("active", True):
            continue

        hub_name = info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        per_hub_data = dict(market_data)
        per_hub_data["hub_price"] = hub_price

        markets.append({
            "hub": hub_key,
            "hub_key": hub_key,
            "hub_name": hub_name,
            "city": info["city"],
            "lat": info["lat"],
            "lon": info["lon"],
            "solar_sensitivity": info["solar_sensitivity"],
            "current_caiso_price": hub_price,
            "actual_solar_mw": market_data.get("solar_mw", 10000.0),
            "_caiso_data": per_hub_data,
        })
    return markets


def scan_all_caiso_hubs() -> list:
    """Scan all active CAISO hubs. Returns list of enriched signal dicts."""
    caiso_data = _fetch_caiso_market_data()
    hub_prices = caiso_data.get("hub_prices", {})
    grid_avg = caiso_data.get("price", 40.0)
    signals = []

    for hub_key, hub_info in CAISO_HUBS.items():
        if not hub_info.get("active", True):
            continue

        hub_name = hub_info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        per_hub_data = dict(caiso_data)
        per_hub_data["hub_price"] = hub_price

        signal = get_caiso_solar_signal(
            hub_info["lat"], hub_info["lon"],
            hub_key=hub_key,
            solar_sensitivity=hub_info["solar_sensitivity"],
            hours_ahead=24,
            caiso_data=per_hub_data,
        )
        signal["hub"] = hub_key
        signal["hub_name"] = hub_name
        signal["city"] = hub_info["city"]
        signal["current_caiso_price"] = hub_price
        signals.append(signal)

    return signals
