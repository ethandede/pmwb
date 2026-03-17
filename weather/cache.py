"""Forecast cache — avoid redundant API calls to Open-Meteo and NOAA.

Caches forecast results by (lat, lon, temp_type, days_ahead) with configurable TTL.
Weather models update every 1-6 hours, so a 30-minute cache is conservative.
"""

import time
from typing import Any, Optional

# Cache TTL in seconds
FORECAST_TTL = 1800  # 30 minutes — weather models update every 1-6 hours
MARKET_TTL = 60      # 1 minute — market prices move faster
METAR_TTL = 300      # 5 minutes — METAR obs update roughly every hour

_cache: dict[str, dict] = {}


def _key(*parts) -> str:
    return "|".join(str(p) for p in parts)


def get(cache_type: str, *parts) -> Optional[Any]:
    """Get a cached value if it exists and hasn't expired."""
    key = _key(cache_type, *parts)
    entry = _cache.get(key)
    if entry is None:
        return None
    if cache_type == "market":
        ttl = MARKET_TTL
    elif cache_type == "metar":
        ttl = METAR_TTL
    else:
        ttl = FORECAST_TTL
    if time.time() - entry["ts"] > ttl:
        del _cache[key]
        return None
    return entry["val"]


def put(cache_type: str, *parts, value: Any):
    """Store a value in the cache."""
    key = _key(cache_type, *parts)
    _cache[key] = {"val": value, "ts": time.time()}


def clear():
    """Clear all cached data."""
    _cache.clear()


def stats() -> dict:
    """Return cache statistics."""
    now = time.time()
    total = len(_cache)
    expired = sum(1 for e in _cache.values() if now - e["ts"] > FORECAST_TTL)
    return {"total": total, "active": total - expired, "expired": expired}
