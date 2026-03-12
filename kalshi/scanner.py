import requests
import re
import time
from typing import List, Dict, Optional, Tuple

from kalshi.market_types import parse_precip_bucket

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Kalshi weather series tickers
WEATHER_SERIES = {
    "KXHIGHNY": {"city": "nyc", "lat": 40.7931, "lon": -73.8720, "unit": "f"},
    "KXHIGHCHI": {"city": "chicago", "lat": 41.9742, "lon": -87.9073, "unit": "f"},
    "KXHIGHMIA": {"city": "miami", "lat": 25.7617, "lon": -80.1918, "unit": "f"},
    "KXHIGHAUS": {"city": "austin", "lat": 30.2672, "lon": -97.7431, "unit": "f"},
}

# Precipitation series — monthly cumulative + daily binary
PRECIP_SERIES = {
    "kxrainnycm": {"city": "nyc", "lat": 40.7931, "lon": -73.8720, "unit": "in"},
    "kxrainchim": {"city": "chicago", "lat": 41.9742, "lon": -87.9073, "unit": "in"},
    "kxrainlaxm": {"city": "los_angeles", "lat": 33.9425, "lon": -118.4081, "unit": "in"},
    "kxrainseam": {"city": "seattle", "lat": 47.4502, "lon": -122.3088, "unit": "in"},
    "kxrainmiam": {"city": "miami", "lat": 25.7617, "lon": -80.1918, "unit": "in"},
    "kxrainhoum": {"city": "houston", "lat": 29.9902, "lon": -95.3368, "unit": "in"},
    "kxrainsfom": {"city": "san_francisco", "lat": 37.6213, "lon": -122.3790, "unit": "in"},
}


def get_kalshi_weather_markets() -> List[Dict]:
    """Fetch active weather markets from Kalshi public API."""
    all_markets = []

    for series_ticker, info in WEATHER_SERIES.items():
        try:
            url = f"{KALSHI_BASE}/events"
            params = {
                "series_ticker": series_ticker,
                "status": "open",
                "with_nested_markets": "true",
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            events = data.get("events", [])
            for event in events:
                nested_markets = event.get("markets", [])
                for market in nested_markets:
                    market["_city"] = info["city"]
                    market["_lat"] = info["lat"]
                    market["_lon"] = info["lon"]
                    market["_unit"] = info["unit"]
                    all_markets.append(market)
        except Exception as e:
            print(f"Kalshi API error for {series_ticker}: {e}")

    return all_markets


def get_kalshi_precip_markets() -> List[Dict]:
    """Fetch active precipitation markets from Kalshi public API."""
    all_markets = []

    for i, (series_ticker, info) in enumerate(PRECIP_SERIES.items()):
        if i > 0:
            time.sleep(0.15)
        try:
            url = f"{KALSHI_BASE}/events"
            params = {
                "series_ticker": series_ticker,
                "status": "open",
                "with_nested_markets": "true",
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            events = data.get("events", [])
            for event in events:
                nested_markets = event.get("markets", [])
                for market in nested_markets:
                    bucket = parse_precip_bucket(market)
                    if bucket is None:
                        continue
                    market["_city"] = info["city"]
                    market["_lat"] = info["lat"]
                    market["_lon"] = info["lon"]
                    market["_unit"] = info["unit"]
                    market["_market_type"] = "precip"
                    market["_threshold"] = bucket[0]
                    all_markets.append(market)
        except Exception as e:
            print(f"Kalshi API error for {series_ticker}: {e}")

    return all_markets


def parse_kalshi_bucket(market: dict) -> Optional[Tuple[float, float | None]]:
    """Parse temperature bucket from Kalshi market data."""
    strike_type = market.get("strike_type", "")
    floor_strike = market.get("floor_strike")
    cap_strike = market.get("cap_strike")

    if strike_type == "between" and floor_strike is not None and cap_strike is not None:
        return float(floor_strike), float(cap_strike)
    elif strike_type == "greater" and floor_strike is not None:
        return float(floor_strike), None
    elif strike_type == "less" and cap_strike is not None:
        return 0.0, float(cap_strike)

    # Fallback: parse from title/subtitle
    title = market.get("title", "") + " " + market.get("subtitle", "")
    m = re.search(r'(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*)', title)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r'[≥>]=?\s*(\d+\.?\d*)', title)
    if m:
        return float(m.group(1)), None

    return None
