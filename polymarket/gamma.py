import requests
import re
from typing import List, Dict, Optional, Tuple
from config import GAMMA_BASE, SCAN_LIMIT

def get_active_weather_markets() -> List[Dict]:
    """Public Gamma API — fetch active markets and keep only weather/temp ones."""
    url = f"{GAMMA_BASE}/markets"
    params = {"active": "true", "closed": "false", "limit": SCAN_LIMIT, "order": "volume", "ascending": "false"}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    markets = resp.json()

    weather = []
    for m in markets:
        q = m.get("question", "").lower()
        if any(k in q for k in ["highest temperature", "high temp", "°f", "rain", "precipitation"]):
            weather.append(m)
    return weather


def parse_bucket(question: str) -> Optional[Tuple[float, float | None]]:
    """Parse common bucket formats: '52-53 F', '>= 60 F', etc."""
    # Range like 52-53
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)', question)
    if m:
        return float(m.group(1)), float(m.group(2))
    # >= X or ≥ X
    m = re.search(r'[≥>]=?\s*(\d+)', question)
    if m:
        return float(m.group(1)), None
    return None
