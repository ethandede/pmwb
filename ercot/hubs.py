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

    # Real-time LMP — fetch recent records, find hub prices
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np6-788-cd/lmp_node_zone_hub",
            headers=headers, params={"size": 200}, timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        # Records are arrays: [timestamp, repeatHourFlag, settlementPoint, LMP]
        for rec in records:
            sp = rec[2] if isinstance(rec, list) else rec.get("settlementPoint", "")
            if str(sp).startswith("HB_"):
                lmp = rec[3] if isinstance(rec, list) else rec.get("LMP", 0)
                result["price"] = float(lmp)
                break
    except Exception as e:
        print(f"  ERCOT price fetch error: {e}")

    # Solar generation — use SPP actual/forecast by geo
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np4-738-cd/spp_hrly_actual_fcast_geo",
            headers=headers, params={"size": 50}, timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        if records:
            # Try to extract solar MW from available fields
            for rec in records:
                val = rec[-1] if isinstance(rec, list) else rec.get("actual", 0)
                if val and float(val) > 0:
                    result["solar_mw"] = float(val)
                    break
    except Exception as e:
        print(f"  ERCOT solar gen fetch error: {e}")

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
