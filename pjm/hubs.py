"""PJM hub scanning — fetches solar irradiance per hub + shared PJM market data.

Caches PJM API responses for 5 minutes to avoid redundant calls across hub scans.
PJM uses public (no-auth) endpoints; falls back to defaults on failure.
"""

import time
import requests
from config import PJM_HUBS
from weather.multi_model import get_pjm_solar_signal

# Module-level cache for PJM market data (shared across all hub scans)
_pjm_cache: dict = {}
_pjm_cache_time: float = 0.0
_CACHE_TTL = 300  # 5 minutes


def _fetch_pjm_market_data() -> dict:
    """Fetch PJM price, solar generation, and load forecast.

    Returns cached data if within TTL. Falls back to defaults on failure.
    PJM endpoints are public — no auth headers required.
    """
    global _pjm_cache, _pjm_cache_time

    if _pjm_cache and (time.time() - _pjm_cache_time) < _CACHE_TTL:
        return _pjm_cache

    result = {"price": 35.0, "solar_mw": 5000.0, "load_forecast": 85_000.0}

    # LMPs — Real-Time Hourly LMPs for PJM hubs
    try:
        r = requests.get(
            "https://api.pjm.com/api/v1/rt_hrl_lmps",
            params={"rowCount": 500}, timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("items", r.json().get("data", []))
        hub_prices = {}
        pjm_hub_names = {info["hub_name"] for info in PJM_HUBS.values()}
        for rec in records:
            if isinstance(rec, dict):
                pnode = rec.get("pnode_name", rec.get("pnodeName", ""))
            else:
                pnode = str(rec[0]) if rec else ""
            if pnode in pjm_hub_names:
                price_val = rec.get("total_lmp_rt", rec.get("totalLmpRt", 0)) if isinstance(rec, dict) else float(rec[-1])
                hub_prices[pnode] = float(price_val)
        if hub_prices:
            result["price"] = hub_prices.get("WESTERN", sum(hub_prices.values()) / len(hub_prices))
            result["hub_prices"] = hub_prices
    except Exception as e:
        print(f"  PJM LMP fetch error: {e}")

    # Load forecast
    try:
        r = requests.get(
            "https://api.pjm.com/api/v1/load_frcstd_7_day",
            params={"rowCount": 10}, timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("items", r.json().get("data", []))
        if records:
            rec = records[-1]
            if isinstance(rec, dict):
                val = rec.get("forecast_load_mw", rec.get("forecastLoadMw", 85_000.0))
            else:
                val = rec[-1]
            result["load_forecast"] = float(val)
    except Exception as e:
        print(f"  PJM load forecast fetch error: {e}")

    # Alert if all endpoints failed (using defaults for everything)
    if result["price"] == 35.0 and result["solar_mw"] == 5000.0 and result["load_forecast"] == 85_000.0:
        try:
            from alerts.telegram_alert import send_alert
            send_alert(
                "PJM APIs Down",
                "All PJM endpoints returned errors. Using fallback defaults.",
                dedup_key="pjm_api_down",
            )
        except Exception:
            pass

    _pjm_cache = result
    _pjm_cache_time = time.time()
    return result


def fetch_pjm_markets() -> list[dict]:
    """Return market data for all active PJM hubs.

    One dict per active hub with current price, solar data, and hub metadata.
    """
    market_data = _fetch_pjm_market_data()
    hub_prices = market_data.get("hub_prices", {})
    grid_avg = market_data.get("price", 35.0)

    markets = []
    for hub_key, info in PJM_HUBS.items():
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
            "current_pjm_price": hub_price,
            "actual_solar_mw": market_data.get("solar_mw", 5000.0),
            "_pjm_data": per_hub_data,
        })
    return markets


def scan_all_pjm_hubs() -> list:
    """Scan all active PJM hubs. Returns list of enriched signal dicts."""
    pjm_data = _fetch_pjm_market_data()
    hub_prices = pjm_data.get("hub_prices", {})
    grid_avg = pjm_data.get("price", 35.0)
    signals = []

    for hub_key, hub_info in PJM_HUBS.items():
        if not hub_info.get("active", True):
            continue

        hub_name = hub_info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        per_hub_data = dict(pjm_data)
        per_hub_data["hub_price"] = hub_price

        signal = get_pjm_solar_signal(
            hub_info["lat"], hub_info["lon"],
            hub_key=hub_key,
            solar_sensitivity=hub_info["solar_sensitivity"],
            hours_ahead=24,
            pjm_data=per_hub_data,
        )
        signal["hub"] = hub_key
        signal["hub_name"] = hub_name
        signal["city"] = hub_info["city"]
        signal["current_pjm_price"] = hub_price
        signals.append(signal)

    return signals
