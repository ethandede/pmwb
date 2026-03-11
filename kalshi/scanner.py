import requests
import re
from typing import List, Dict, Optional, Tuple

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Kalshi weather series tickers
WEATHER_SERIES = {
    "KXHIGHNY": {"city": "nyc", "lat": 40.7931, "lon": -73.8720, "unit": "f"},
    "KXHIGHCHI": {"city": "chicago", "lat": 41.9742, "lon": -87.9073, "unit": "f"},
    "KXHIGHMIA": {"city": "miami", "lat": 25.7617, "lon": -80.1918, "unit": "f"},
    "KXHIGHAUS": {"city": "austin", "lat": 30.2672, "lon": -97.7431, "unit": "f"},
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
