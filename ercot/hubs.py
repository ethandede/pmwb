"""ERCOT hub scanning — fetches solar irradiance per hub + shared ERCOT market data.

Caches ERCOT API responses for 5 minutes to avoid redundant calls across hub scans.
"""

import time
import requests
from config import ERCOT_HUBS
from weather.multi_model import get_ercot_solar_signal
from ercot.auth import get_ercot_headers

# Module-level cache for ERCOT market data (shared across all 5 hub scans)
_ercot_cache: dict = {}
_ercot_cache_time: float = 0.0
_CACHE_TTL = 300  # 5 minutes


def _fetch_ercot_market_data() -> dict:
    """Fetch ERCOT price, solar generation, and load forecast.

    Returns cached data if within TTL. Falls back to defaults on failure.
    """
    global _ercot_cache, _ercot_cache_time

    if _ercot_cache and (time.time() - _ercot_cache_time) < _CACHE_TTL:
        return _ercot_cache

    result = {"price": 40.0, "solar_mw": 12000.0, "load_forecast": 50000.0}

    headers = get_ercot_headers()

    # Settlement Point Prices — has all hub prices (HB_HOUSTON, HB_NORTH, etc.)
    # Fields: [deliveryDate, deliveryHour, deliveryInterval, settlementPoint,
    #          settlementPointType, settlementPointPrice, DSTFlag]
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np6-905-cd/spp_node_zone_hub",
            headers=headers, params={"size": 500}, timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        hub_prices = {}
        for rec in records:
            sp = rec[3] if isinstance(rec, list) else rec.get("settlementPoint", "")
            if str(sp).startswith("HB_") and sp not in ("HB_BUSAVG", "HB_HUBAVG"):
                price = rec[5] if isinstance(rec, list) else rec.get("settlementPointPrice", 0)
                hub_prices[sp] = float(price)
        if hub_prices:
            # Use HB_HOUSTON as default, or average of all hubs
            result["price"] = hub_prices.get("HB_HOUSTON", sum(hub_prices.values()) / len(hub_prices))
            result["hub_prices"] = hub_prices
    except Exception as e:
        print(f"  ERCOT price fetch error: {e}")

    # Load forecast
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np3-566-cd/lf_by_model_study_area",
            headers=headers, params={"size": 10}, timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        if records:
            rec = records[-1]
            val = rec[-1] if isinstance(rec, list) else rec.get("SystemTotal", 50000.0)
            result["load_forecast"] = float(val)
    except Exception as e:
        print(f"  ERCOT load forecast fetch error: {e}")

    # Alert if all 3 endpoints failed (using defaults for everything)
    if result["price"] == 40.0 and result["solar_mw"] == 12000.0 and result["load_forecast"] == 50000.0:
        try:
            from alerts.telegram_alert import send_alert
            send_alert(
                "ERCOT APIs Down",
                "All 3 ERCOT endpoints returned errors. Using fallback defaults.",
                dedup_key="ercot_api_down",
            )
        except Exception:
            pass

    _ercot_cache = result
    _ercot_cache_time = time.time()
    return result


def fetch_ercot_markets() -> list[dict]:
    """Return raw hub data + shared ERCOT market data for pipeline fetch stage."""
    market_data = _fetch_ercot_market_data()
    hub_prices = market_data.get("hub_prices", {})
    grid_avg = market_data.get("price", 40.0)
    markets = []
    for hub_key, info in ERCOT_HUBS.items():
        hub_name = info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        # Build per-hub ercot_data with hub_price injected
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
            "current_ercot_price": hub_price,
            "actual_solar_mw": market_data.get("solar_mw", 12000.0),
            "_ercot_data": per_hub_data,
        })
    return markets


def scan_all_hubs() -> list:
    """Scan all 5 ERCOT hubs. Returns list of enriched signal dicts."""
    ercot_data = _fetch_ercot_market_data()
    hub_prices = ercot_data.get("hub_prices", {})
    grid_avg = ercot_data.get("price", 40.0)
    signals = []

    for hub_key, hub_info in ERCOT_HUBS.items():
        hub_name = hub_info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        per_hub_data = dict(ercot_data)
        per_hub_data["hub_price"] = hub_price

        signal = get_ercot_solar_signal(
            hub_info["lat"], hub_info["lon"],
            hub_key=hub_key,
            solar_sensitivity=hub_info["solar_sensitivity"],
            hours_ahead=24,
            ercot_data=per_hub_data,
        )
        signal["hub"] = hub_key
        signal["hub_name"] = hub_name
        signal["city"] = hub_info["city"]
        signals.append(signal)

    return signals
