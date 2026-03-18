"""ERCOT hub scanning — fetches solar irradiance per hub + shared ERCOT market data.

Caches ERCOT API responses for 5 minutes to avoid redundant calls across hub scans.
"""

import time
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from config import ERCOT_HUBS, ERCOT_SOLAR_HOURS
from weather.multi_model import get_ercot_solar_signal
from ercot.auth import get_ercot_headers

# Module-level cache for ERCOT market data (shared across all 5 hub scans)
_ercot_cache: dict = {}
_ercot_cache_time: float = 0.0
_CACHE_TTL = 300  # 5 minutes

# DAM cache keyed by date string — DAM prices never change after publication
_dam_cache: dict[str, dict] = {}


def _get_ct_now() -> datetime:
    """Return current datetime in Central Time. Extracted for testability."""
    return datetime.now(ZoneInfo("America/Chicago"))


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


def fetch_dam_prices(date_str: str) -> "dict[str, dict[int, float]] | None":
    """Fetch Day-Ahead Market prices for all hubs for a given date.

    Returns {hub_name: {hour: price}} or None on failure.
    DAM prices are immutable after publication — cached per date string.
    """
    if date_str in _dam_cache:
        return _dam_cache[date_str]

    headers = get_ercot_headers()
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np4-190-cd/dam_stlmnt_pnt_prices",
            headers=headers,
            params={"size": 2000},
            timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])

        result: dict[str, dict[int, float]] = {}
        for rec in records:
            if isinstance(rec, list):
                # [deliveryDate, deliveryHour, settlementPoint, settlementPointType, settlementPointPrice]
                sp = rec[2]
                hour = int(rec[1])
                price = float(rec[4])
            else:
                sp = rec.get("settlementPoint", "")
                hour = int(rec.get("deliveryHour", 0))
                price = float(rec.get("settlementPointPrice", 0))

            if not str(sp).startswith("HB_") or sp in ("HB_BUSAVG", "HB_HUBAVG"):
                continue

            if sp not in result:
                result[sp] = {}
            result[sp][hour] = price

        _dam_cache[date_str] = result
        return result
    except Exception as e:
        print(f"  ERCOT DAM price fetch error: {e}")
        return None


def fetch_rt_settlement(hub_name: str, hour: int, date_str: str) -> "float | None":
    """Fetch Real-Time settlement price for a hub/hour/date.

    Averages all interval prices (should be 4 × 15-min intervals) for the hour.
    Returns None on failure or if no matching records found.
    """
    headers = get_ercot_headers()
    try:
        r = requests.get(
            "https://api.ercot.com/api/public-reports/np6-905-cd/spp_node_zone_hub",
            headers=headers,
            params={"size": 500},
            timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])

        prices = []
        for rec in records:
            if isinstance(rec, list):
                # [deliveryDate, deliveryHour, deliveryInterval, settlementPoint,
                #  settlementPointType, settlementPointPrice, DSTFlag]
                rec_date = str(rec[0])
                rec_hour = int(rec[1])
                sp = str(rec[3])
                price = float(rec[5])
            else:
                rec_date = str(rec.get("deliveryDate", ""))
                rec_hour = int(rec.get("deliveryHour", 0))
                sp = str(rec.get("settlementPoint", ""))
                price = float(rec.get("settlementPointPrice", 0))

            if sp == hub_name and rec_hour == hour and rec_date == date_str:
                prices.append(price)

        if not prices:
            return None
        return sum(prices) / len(prices)
    except Exception as e:
        print(f"  ERCOT RT settlement fetch error for {hub_name} HE{hour}: {e}")
        return None


def fetch_ercot_markets() -> list[dict]:
    """Return per-hour binary option contracts for all ERCOT hubs.

    When DAM prices are available, returns one dict per hub × hour with:
      ticker, hub_key, hub_name, contract_date, contract_hour, dam_price, etc.

    Falls back to one dict per hub (no hour dimension) when DAM is unavailable,
    preserving backward compatibility with existing pipeline consumers.
    """
    market_data = _fetch_ercot_market_data()
    hub_prices = market_data.get("hub_prices", {})
    grid_avg = market_data.get("price", 40.0)

    now_ct = _get_ct_now()
    tomorrow_str = (now_ct.date() + timedelta(days=1)).strftime("%Y-%m-%d")

    # Fetch DAM prices for tomorrow (forward-looking binary option contracts).
    # DAM prices are published the evening before; we trade them the next morning.
    dam_tomorrow = fetch_dam_prices(tomorrow_str)

    # If DAM fetch failed, fall back to legacy one-per-hub structure
    if dam_tomorrow is None:
        markets = []
        for hub_key, info in ERCOT_HUBS.items():
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
                "current_ercot_price": hub_price,
                "actual_solar_mw": market_data.get("solar_mw", 12000.0),
                "_ercot_data": per_hub_data,
            })
        return markets

    # Build per-hour contracts from tomorrow's DAM data
    markets = []
    date_compact = tomorrow_str.replace("-", "")[2:]  # e.g. "260319"

    for hub_key, info in ERCOT_HUBS.items():
        hub_name = info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        per_hub_data = dict(market_data)
        per_hub_data["hub_price"] = hub_price

        hub_dam = dam_tomorrow.get(hub_name, {})
        for hour, dam_price in hub_dam.items():
            ticker = f"BOPT-ERCOT-{hub_name}-{date_compact}-{hour:02d}"

            markets.append({
                "ticker": ticker,
                "hub": hub_key,
                "hub_key": hub_key,
                "hub_name": hub_name,
                "city": info["city"],
                "lat": info["lat"],
                "lon": info["lon"],
                "solar_sensitivity": info["solar_sensitivity"],
                "contract_date": tomorrow_str,
                "contract_hour": hour,
                "dam_price": dam_price,
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
