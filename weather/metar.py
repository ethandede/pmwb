"""METAR real-time airport observations for forecast bust detection.

Fetches current conditions from the Aviation Weather Center API.
Used as a pre-fusion reality check — NOT a forecast model.
"""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from weather.http import get as _http_get
from weather import cache as fcache
from config import METAR_STATIONS, CITY_TIMEZONES

_METAR_API = "https://aviationweather.gov/api/data/metar"


def get_metar_obs(station_id: str) -> Optional[dict]:
    """Fetch latest METAR observation for a station.

    Returns {"temp_f": float, "observed_at": datetime, "station": str}
    or None on failure (fail-open).
    """
    # Check cache first
    cached = fcache.get("metar", station_id)
    if cached is not None:
        return cached

    try:
        url = f"{_METAR_API}?ids={station_id}&format=json&hours=2"
        r = _http_get(url, timeout=10)
        r.raise_for_status()
        data = r.json()

        if not data or not isinstance(data, list):
            return None

        obs = data[0]  # most recent observation
        temp_c = obs.get("temp")
        if temp_c is None:
            return None

        result = {
            "temp_f": round(temp_c * 9 / 5 + 32, 1),
            "observed_at": datetime.fromtimestamp(obs.get("obsTime", 0), tz=timezone.utc),
            "station": station_id,
        }

        fcache.put("metar", station_id, value=result)
        return result

    except Exception as e:
        print(f"  METAR obs error ({station_id}): {e}")
        return None


def _local_hour(city: str) -> int:
    """Get current local hour (0-23) for a city."""
    tz_name = CITY_TIMEZONES.get(city)
    if not tz_name:
        return -1
    now = datetime.now(ZoneInfo(tz_name))
    return now.hour


def check_forecast_bust(
    city: str,
    forecast_high: float,
    days_ahead: int,
    temp_type: str,
) -> dict:
    """Check if METAR observations indicate a forecast bust.

    Only activates for same-day max-temp contracts after 10am local.
    Returns constraints to apply after fusion (floor, confidence penalty).
    """
    inactive = {"active": False}

    # Gate: same-day max-temp only
    if days_ahead != 0 or temp_type != "max":
        return inactive

    # Gate: city must have a METAR station
    station = METAR_STATIONS.get(city)
    if not station:
        return inactive

    # Gate: must be after 10am local (before that, temp is still climbing)
    if _local_hour(city) < 10:
        return inactive

    # Fetch current observation
    obs = get_metar_obs(station)
    if obs is None:
        return inactive

    obs_temp = obs["temp_f"]
    overshoot = obs_temp - forecast_high

    if overshoot > 0:
        return {
            "active": True,
            "bust_detected": True,
            "floor": obs_temp,
            "obs_temp": obs_temp,
            "confidence_penalty": min(0.3, round(overshoot / 20.0, 2)),
        }

    return {
        "active": True,
        "bust_detected": False,
        "floor": None,
        "obs_temp": obs_temp,
        "confidence_penalty": 0.0,
    }
