"""Simple 31-member GFS Ensemble signal generator for temperature buckets.

Architecture (post-2026-04-15 dedupe refactor):
  1. _fetch_ensemble_members(lat, lon, days_ahead, unit, temp_type)
       Fetches the raw 31-member list from Open-Meteo ONCE per
       (city, day, temp_type). Cached on success in fcache for 30 min so
       all 8-12 bucket queries for that city share a single API call —
       a ~10x reduction in API volume vs. the previous (low, high)-keyed
       cache. On failure, the result is held in a 2-minute in-process
       negative cache to prevent within-cycle retry storms while still
       letting the next 5-minute cycle try fresh.

  2. get_ensemble_signal(..., low, high, ...)
       Cheap wrapper. Calls _fetch_ensemble_members, then runs the bucket
       math fresh for the requested [low, high) window. No API call.

This is the production kalshi_temp scoring path (pipeline/config.py:132).
"""

import time
from typing import Tuple, Dict, List, Optional

from weather import cache as fcache
from weather.http import get as http_get


ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# In-process negative cache: prevents within-cycle retry storms when
# Open-Meteo is rate-limiting us. Short TTL (2 min) so the next 5-min
# daemon cycle gets a fresh attempt. Process-local on purpose — we want
# this to reset on daemon restart.
_NEGATIVE_CACHE: Dict[Tuple, float] = {}
_NEGATIVE_TTL_SEC = 120

# Consecutive 429 counter per city, for loud alerting after sustained
# rate limiting.
_CONSECUTIVE_429S: Dict[str, int] = {}


def _fetch_ensemble_members(
    lat: float,
    lon: float,
    city: str,
    days_ahead: int,
    unit: str,
    temp_type: str,
) -> Optional[List[float]]:
    """Fetch GFS ensemble members for a single (city, day, temp_type).

    Returns a list of member temperatures (one per ensemble member) for
    the target day, or None if the fetch failed.

    Cache key intentionally OMITS bucket bounds so all bucket queries
    for the same (city, day) share one upstream call.
    """
    cache_key = (round(lat, 3), round(lon, 3), days_ahead, unit, temp_type)

    # Success cache (persistent fcache, 30-min TTL set at module level)
    cached = fcache.get("ensemble_members", *cache_key)
    if cached is not None:
        return cached

    # Negative cache (in-process, 2-min TTL) — prevents within-cycle
    # retry storms after a 429 without locking us out of the next cycle.
    neg_ts = _NEGATIVE_CACHE.get(cache_key)
    if neg_ts is not None:
        if time.time() - neg_ts < _NEGATIVE_TTL_SEC:
            return None
        del _NEGATIVE_CACHE[cache_key]

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

        members: List[float] = []
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

        fcache.put("ensemble_members", *cache_key, value=members)
        _CONSECUTIVE_429S[city] = 0  # reset on success
        return members

    except Exception as e:
        # Detect HTTP 429 specifically so we can shout about it.
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        is_rate_limit = status_code == 429 or "429" in str(e)

        # Mark this cache key as recently-failed so subsequent buckets
        # in the same cycle don't re-hit the API.
        _NEGATIVE_CACHE[cache_key] = time.time()

        if is_rate_limit:
            count = _CONSECUTIVE_429S.get(city, 0) + 1
            _CONSECUTIVE_429S[city] = count
            print(
                f"  [429-RATE-LIMIT] {city} ensemble — Open-Meteo is "
                f"throttling us (429). Using fallback sentinel."
            )
            if count >= 3:
                print(
                    f"  [429-RATE-LIMIT] {city} — {count} consecutive rate "
                    f"limits. Bot is running on fallback data."
                )
        else:
            print(f"  Ensemble signal error for {city}: {e}")
        return None


def get_ensemble_signal(
    lat: float,
    lon: float,
    city: str,
    low: Optional[float] = None,
    high: Optional[float] = None,
    days_ahead: int = 1,
    unit: str = "f",
    month: Optional[int] = None,      # pipeline compatibility
    temp_type: str = "max",
    **kwargs                          # absorb any other legacy kwargs
) -> Tuple[float, float, Dict]:
    """Compute (model_prob, confidence, details) for a temp bucket.

    Fetches members via _fetch_ensemble_members (cached per city/day) and
    runs the bucket math fresh per call. Returns the (0.50, 50, fallback)
    sentinel if member fetch fails.
    """
    members = _fetch_ensemble_members(lat, lon, city, days_ahead, unit, temp_type)

    if members is None:
        return (0.50, 50, {"method": "fallback", "error": "member fetch failed"})

    # Bucket math (unchanged from previous version)
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

    return (model_prob, confidence, details)
