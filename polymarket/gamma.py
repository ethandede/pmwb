import requests
import re
from typing import List, Dict, Optional, Tuple
from config import GAMMA_BASE, SCAN_LIMIT

def get_active_weather_markets() -> List[Dict]:
    """Public Gamma API — fetch active markets and keep only weather/temp ones."""
    url = f"{GAMMA_BASE}/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": SCAN_LIMIT,
        "order": "volume",
        "ascending": "false"
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        print(f"Gamma API error: {e}")
        return []

    weather = []
    for m in markets:
        q = m.get("question", "").lower()
        if any(k in q for k in ["highest temperature", "high temp", "temperature in", "°f", "°c", "rain", "precipitation", "temp "]):
            weather.append(m)
    return weather

def parse_bucket(question: str) -> Optional[Tuple[float, float | None]]:
    """Improved parser — handles range buckets, single-temp, 'or higher', 'exactly', etc."""
    q = question.lower()

    # Range: "84-85", "84 - 85", "84–85"
    m = re.search(r'(\d{1,3})\s*[-–—]\s*(\d{1,3})', q)
    if m:
        return float(m.group(1)), float(m.group(2))

    # >= / or higher / above / 85+
    m = re.search(r'(?:≥|or higher|or above|above|or more|\+)\s*(\d{1,3})', q)
    if m:
        return float(m.group(1)), None

    # "X or higher" where number comes first
    m = re.search(r'(\d{1,3})\s*(?:or higher|or above|or more|\+)', q)
    if m:
        return float(m.group(1)), None

    # Single-temp / "be X°" / exactly X (treat as 1-degree bucket)
    m = re.search(r'(?:be |exactly |^|\s)(\d{1,3})\s*°[fc]', q)
    if m:
        x = float(m.group(1))
        return x, x + 1

    return None
