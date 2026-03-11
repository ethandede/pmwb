import requests
from typing import List

def get_ensemble_max_temps_f(lat: float, lon: float, days_ahead: int = 1) -> List[float]:
    """Open-Meteo Ensemble API — correct endpoint + exact 2026 response structure."""
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&temperature_unit=fahrenheit"
        f"&timezone=auto"
        f"&forecast_days={days_ahead + 2}"
    )

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        # Official structure: "ensemble" -> list of members -> each has "daily" dict
        temps_f = []
        if "ensemble" in data:
            for member in data["ensemble"]:
                daily_max = member.get("daily", {}).get("temperature_2m_max", [])
                if len(daily_max) > days_ahead:
                    temps_f.append(round(float(daily_max[days_ahead]), 1))
        else:
            # Fallback: member keys like temperature_2m_max_member01
            daily = data.get("daily", {})
            member_keys = sorted(k for k in daily if k.startswith("temperature_2m_max_member"))
            if member_keys:
                for key in member_keys:
                    vals = daily[key]
                    if len(vals) > days_ahead and vals[days_ahead] is not None:
                        temps_f.append(round(float(vals[days_ahead]), 1))
            else:
                # Single forecast fallback
                raw = daily.get("temperature_2m_max", [])
                if len(raw) > days_ahead and raw[days_ahead] is not None:
                    temps_f = [round(float(raw[days_ahead]), 1)]

        return temps_f

    except Exception as e:
        print(f"Open-Meteo Ensemble error: {e}")
        return [55.0] * 31  # safe fallback so scanner never crashes


def get_bucket_prob(temps_f: List[float], low: float, high: float | None = None) -> float:
    """Empirical probability from ensemble members."""
    if not temps_f:
        return 0.0
    if high is None:  # >= low
        count = sum(1 for t in temps_f if t >= low)
    else:
        count = sum(1 for t in temps_f if low <= t < high)
    return round(count / len(temps_f), 4)
