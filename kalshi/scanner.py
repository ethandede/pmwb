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
    "KXHIGHLAX": {"city": "los_angeles", "lat": 33.9425, "lon": -118.4081, "unit": "f"},
    "KXHIGHTSEA": {"city": "seattle", "lat": 47.4502, "lon": -122.3088, "unit": "f"},
    "KXHIGHTHOU": {"city": "houston", "lat": 29.9902, "lon": -95.3368, "unit": "f"},
    "KXHIGHTSFO": {"city": "san_francisco", "lat": 37.6213, "lon": -122.3790, "unit": "f"},
    "KXHIGHTATL": {"city": "atlanta", "lat": 33.6407, "lon": -84.4277, "unit": "f"},
    "KXHIGHTDC": {"city": "washington_dc", "lat": 38.8512, "lon": -77.0402, "unit": "f"},
    "KXHIGHTBOS": {"city": "boston", "lat": 42.3656, "lon": -71.0096, "unit": "f"},
    "KXHIGHTPHX": {"city": "phoenix", "lat": 33.4373, "lon": -112.0078, "unit": "f"},
    "KXHIGHTSATX": {"city": "san_antonio", "lat": 29.5312, "lon": -98.4690, "unit": "f"},
    "KXHIGHTLV": {"city": "las_vegas", "lat": 36.0840, "lon": -115.1537, "unit": "f"},
}

# Precipitation series — monthly cumulative + daily binary
PRECIP_SERIES = {
    "KXRAINNYCM": {"city": "nyc", "lat": 40.7931, "lon": -73.8720, "unit": "in"},
    "KXRAINCHIM": {"city": "chicago", "lat": 41.9742, "lon": -87.9073, "unit": "in"},
    "KXRAINLAXM": {"city": "los_angeles", "lat": 33.9425, "lon": -118.4081, "unit": "in"},
    "KXRAINSEAM": {"city": "seattle", "lat": 47.4502, "lon": -122.3088, "unit": "in"},
    "KXRAINMIAM": {"city": "miami", "lat": 25.7617, "lon": -80.1918, "unit": "in"},
    "KXRAINHOUM": {"city": "houston", "lat": 29.9902, "lon": -95.3368, "unit": "in"},
    "KXRAINSFOM": {"city": "san_francisco", "lat": 37.6213, "lon": -122.3790, "unit": "in"},
}


def get_kalshi_weather_markets() -> List[Dict]:
    """Fetch active weather markets from Kalshi public API."""
    all_markets = []

    for i, (series_ticker, info) in enumerate(WEATHER_SERIES.items()):
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


def get_kalshi_price(market: dict) -> Optional[float]:
    """Extract yes price from a Kalshi market dict.

    Kalshi returns prices in two formats:
      - yes_ask / last_price: integer cents (often None)
      - yes_ask_dollars / last_price_dollars: string dollars like "0.0700"

    Returns price as a float in [0, 1] or None if unavailable.
    """
    # Try cents format first
    cents = market.get("yes_ask") or market.get("last_price")
    if cents is not None:
        return cents / 100.0

    # Try dollar-string format
    dollars = market.get("yes_ask_dollars") or market.get("last_price_dollars")
    if dollars is not None:
        try:
            return float(dollars)
        except (ValueError, TypeError):
            return None

    return None


def parse_kalshi_bucket(market: dict) -> Optional[Tuple[float, float | None]]:
    """Parse temperature bucket from Kalshi market data."""
    strike_type = market.get("strike_type", "")
    floor_strike = market.get("floor_strike")
    cap_strike = market.get("cap_strike")

    if strike_type == "between" and floor_strike is not None and cap_strike is not None:
        return float(floor_strike), float(cap_strike)
    elif strike_type == "greater" and floor_strike is not None:
        # Kalshi "greater" contracts: YES = "above threshold".
        # Verified 2026-04-15 against live [DEBUG-T] logs:
        #   T91 → strike_type='greater', floor_strike=91, subtitle "92° or above"
        # The previous comment ("YES = below threshold") was the inversion bug
        # that produced phantom +0.8 edges on every greater-type contract once
        # the new edge gate dropped to 0.10. ensemble_signal._compute handles
        # the (low, None) shape as P(temp >= low), which is what we want.
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
