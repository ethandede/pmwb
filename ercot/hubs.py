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

    # Real-time LMP (Settlement Point Prices)
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np6-788-cd/lmp_node_zone_hub",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        records = data.get("data", [])
        if records:
            # Find a hub price (HB_HOUSTON or first available)
            for rec in records:
                if rec.get("SettlementPoint", "").startswith("HB_"):
                    result["price"] = float(rec.get("LMP", 40.0))
                    break
    except Exception as e:
        print(f"  ERCOT price fetch error: {e}")

    # Actual solar generation
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np4-738-cd/spp_hrly_actual_fcast_geo",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        records = data.get("data", [])
        if records:
            # Sum solar generation across all zones
            solar_vals = [float(rec.get("actual", 0) or 0) for rec in records
                          if "solar" in rec.get("fuelType", "").lower()]
            if solar_vals:
                result["solar_mw"] = sum(solar_vals)
    except Exception as e:
        print(f"  ERCOT solar gen fetch error: {e}")

    # Load forecast
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np3-566-cd/lf_by_model_study_area",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        records = data.get("data", [])
        if records:
            result["load_forecast"] = float(records[-1].get("SystemTotal", 50000.0))
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
    """Return raw hub data + shared ERCOT market data for pipeline fetch stage.

    Does NOT call solar signal — that happens in the score stage via forecast_fn.
    """
    market_data = _fetch_ercot_market_data()
    markets = []
    for hub_key, info in ERCOT_HUBS.items():
        markets.append({
            "hub": hub_key,
            "hub_name": info["hub_name"],
            "city": info["city"],
            "lat": info["lat"],
            "lon": info["lon"],
            "current_ercot_price": market_data.get("price", 40.0),
            "actual_solar_mw": market_data.get("solar_mw", 12000.0),
            "_ercot_data": market_data,
        })
    return markets


def scan_all_hubs() -> list:
    """Scan all 5 ERCOT hubs. Returns list of enriched signal dicts."""
    ercot_data = _fetch_ercot_market_data()
    signals = []

    for hub_key, hub_info in ERCOT_HUBS.items():
        signal = get_ercot_solar_signal(
            hub_info["lat"], hub_info["lon"],
            hours_ahead=24,
            ercot_data=ercot_data,
        )
        signal["hub"] = hub_key
        signal["hub_name"] = hub_info["hub_name"]
        signal["city"] = hub_info["city"]
        signals.append(signal)

    return signals
