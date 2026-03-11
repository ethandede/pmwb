import requests
from typing import List

def get_ensemble_max_temps_f(lat: float, lon: float, days_ahead: int = 1) -> List[float]:
    """Open-Meteo ensemble API — returns list of forecasted daily max temps (F) from all members."""
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&temperature_unit=fahrenheit"
        f"&timezone=auto&forecast_days={days_ahead + 1}"
    )
    data = requests.get(url, timeout=15).json()

    # The ensemble API returns multiple members in the daily data.
    # Each member has its own temperature_2m_max array.
    # The response structure has "daily" with "temperature_2m_max" as a list of values
    # across ensemble members for each day.
    #
    # Actually, the Open-Meteo ensemble API returns data per-member.
    # The daily.temperature_2m_max contains values for each day, but with
    # multiple ensemble members, the response includes member data.
    #
    # Let's handle both possible response formats:
    temps_raw = data["daily"]["temperature_2m_max"]

    if isinstance(temps_raw, list) and len(temps_raw) > 0:
        if isinstance(temps_raw[0], list):
            # Nested: each member is a sub-list, pick the target day from each
            temps_f = [member[days_ahead] for member in temps_raw if len(member) > days_ahead]
        else:
            # Flat list: the API may return one value per day (single model).
            # In this case, we just have one forecast — return it as a single-element list.
            # But the ensemble API typically returns ~50 members.
            # Check if we got the members format: daily has "temperature_2m_max_member01", etc.
            member_keys = [k for k in data["daily"].keys() if k.startswith("temperature_2m_max_member")]
            if member_keys:
                temps_f = []
                for key in sorted(member_keys):
                    vals = data["daily"][key]
                    if len(vals) > days_ahead and vals[days_ahead] is not None:
                        temps_f.append(vals[days_ahead])
            else:
                # Flat single forecast — just return the day's value
                val = temps_raw[days_ahead] if len(temps_raw) > days_ahead else temps_raw[-1]
                temps_f = [val]
    else:
        temps_f = []

    return [round(t, 1) for t in temps_f]


def get_bucket_prob(temps_f: List[float], low: float, high: float | None = None) -> float:
    """Empirical probability from ensemble members."""
    if not temps_f:
        return 0.0
    if high is None:  # >= low
        count = sum(1 for t in temps_f if t >= low)
    else:
        count = sum(1 for t in temps_f if low <= t < high)
    return round(count / len(temps_f), 4)
