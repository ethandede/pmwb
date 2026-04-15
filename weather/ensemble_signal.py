"""Simple 31-member GFS Ensemble signal generator for temperature buckets."""

import math
from datetime import datetime, timezone
from typing import Tuple, Dict, Optional

from weather import cache as fcache
from weather.http import get as http_get


ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


def get_ensemble_signal(
    lat: float,
    lon: float,
    city: str,
    low: Optional[float] = None,
    high: Optional[float] = None,
    days_ahead: int = 1,
    unit: str = "f",
    month: Optional[int] = None,      # ← added for pipeline compatibility
    temp_type: str = "max",           # ← added and now properly used
    **kwargs                          # ← catches any other old kwargs
) -> Tuple[float, float, Dict]:
    """
    Returns (model_prob, confidence, details)
    Uses 31-member GFS ensemble member counting.
    Accepts extra kwargs from the old pipeline for compatibility.
    """
    cache_key = (round(lat, 3), round(lon, 3), days_ahead, unit, temp_type, low, high)
    cached = fcache.get("ensemble_signal", *cache_key)
    if cached is not None:
        return cached

    unit_param = "fahrenheit" if unit == "f" else "celsius"
    daily_var = "temperature_2m_max" if temp_type == "max" else "temperature_2m_min"

    params = (
        f"?latitude={lat}&longitude={lon}"
        f"&daily={daily_var}"
        f"&models=gfs_seamless"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&forecast_days={days_ahead + 2}"
    )

    try:
        r = http_get(f"{ENSEMBLE_URL}{params}", timeout=12)
        r.raise_for_status()
        data = r.json()

        members = []
        daily = data.get("daily", {})

        for i in range(1, 32):
            key = f"{daily_var}_member{i:02d}"
            if key in daily:
                values = daily[key]
                if len(values) > days_ahead and values[days_ahead] is not None:
                    members.append(float(values[days_ahead]))

        if not members:
            mean_vals = daily.get(daily_var, [])
            if len(mean_vals) > days_ahead and mean_vals[days_ahead] is not None:
                members = [float(mean_vals[days_ahead])] * 31

        if not members:
            raise ValueError("No ensemble members found")

        # Count members in the bucket
        if high is None and low is not None:
            above_count = sum(1 for t in members if t >= low)
        elif low is None and high is not None:
            above_count = sum(1 for t in members if t < high)
        else:
            above_count = sum(1 for t in members if low <= t < high)

        total_members = len(members)
        model_prob = above_count / total_members
        model_prob = max(0.05, min(0.95, round(model_prob, 4)))

        confidence = (max(above_count, total_members - above_count) / total_members) * 100
        confidence = max(50, min(95, round(confidence)))

        details = {
            "method": "ensemble_member_count",
            "members_used": total_members,
            "above_count": above_count,
            "model_spread": round(max(members) - min(members), 1),
            "mean_temp": round(sum(members) / total_members, 1),
        }

        result = (model_prob, confidence, details)
        fcache.put("ensemble_signal", *cache_key, value=result)
        return result

    except Exception as e:
        print(f"  Ensemble signal error for {city}: {e}")
        # Do NOT cache the fallback. Caching the (0.50, 50) sentinel
        # poisoned every dependent bucket for the full 30-minute TTL after
        # a single Open-Meteo 429, which is how Bug #2 produced cycle-wide
        # noise. Returning without caching lets the next call retry the API.
        return (0.50, 50, {"method": "fallback", "error": str(e)})
