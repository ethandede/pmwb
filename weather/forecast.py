import requests
import re
import calendar
from datetime import date
from typing import List
from weather.http import get as http_get

def get_ensemble_max_temps(lat: float, lon: float, days_ahead: int = 1, unit: str = "f") -> List[float]:
    """Open-Meteo Ensemble — returns daily max temps in the correct unit (F or C)."""
    unit_param = "fahrenheit" if unit == "f" else "celsius"
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&forecast_days={days_ahead + 2}"
    )

    try:
        r = http_get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        temps = []
        if "ensemble" in data:
            for member in data["ensemble"]:
                daily_max = member.get("daily", {}).get("temperature_2m_max", [])
                if len(daily_max) > days_ahead:
                    temps.append(round(float(daily_max[days_ahead]), 1))
        else:
            # Fallback: member keys like temperature_2m_max_member01
            daily = data.get("daily", {})
            member_keys = sorted(k for k in daily if k.startswith("temperature_2m_max_member"))
            if member_keys:
                for key in member_keys:
                    vals = daily[key]
                    if len(vals) > days_ahead and vals[days_ahead] is not None:
                        temps.append(round(float(vals[days_ahead]), 1))
            else:
                raw = daily.get("temperature_2m_max", [])
                if len(raw) > days_ahead and raw[days_ahead] is not None:
                    temps = [round(float(raw[days_ahead]), 1)]

        return temps

    except Exception as e:
        print(f"Open-Meteo Ensemble error: {e}")
        return [20.0] * 31  # safe fallback


def get_ensemble_min_temps(lat: float, lon: float, days_ahead: int = 1, unit: str = "f") -> List[float]:
    """Open-Meteo Ensemble — returns daily min temps in the correct unit (F or C)."""
    unit_param = "fahrenheit" if unit == "f" else "celsius"
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_min"
        f"&temperature_unit={unit_param}"
        f"&timezone=auto"
        f"&forecast_days={days_ahead + 2}"
    )

    try:
        r = http_get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        temps = []
        if "ensemble" in data:
            for member in data["ensemble"]:
                daily_min = member.get("daily", {}).get("temperature_2m_min", [])
                if len(daily_min) > days_ahead:
                    temps.append(round(float(daily_min[days_ahead]), 1))
        else:
            # Fallback: member keys like temperature_2m_min_member01
            daily = data.get("daily", {})
            member_keys = sorted(k for k in daily if k.startswith("temperature_2m_min_member"))
            if member_keys:
                for key in member_keys:
                    vals = daily[key]
                    if len(vals) > days_ahead and vals[days_ahead] is not None:
                        temps.append(round(float(vals[days_ahead]), 1))
            else:
                raw = daily.get("temperature_2m_min", [])
                if len(raw) > days_ahead and raw[days_ahead] is not None:
                    temps = [round(float(raw[days_ahead]), 1)]

        return temps

    except Exception as e:
        print(f"Open-Meteo Ensemble min temp error: {e}")
        return [20.0] * 31  # safe fallback


def get_bucket_prob(temps: List[float], low: float, high: float | None = None) -> float:
    """Empirical probability from ensemble members (works for both F and C)."""
    if not temps:
        return 0.0
    if high is None:  # >= low
        count = sum(1 for t in temps if t >= low)
    else:
        count = sum(1 for t in temps if low <= t < high)
    return round(count / len(temps), 4)


def calculate_remaining_month_days(market_close_date: date | None = None) -> int:
    """Days from today to market close (or end of month if close date unknown).

    Args:
        market_close_date: Parsed from ticker date tag or market API response.
    """
    today = date.today()
    if market_close_date:
        delta = (market_close_date - today).days
        return max(0, delta)
    last_day = calendar.monthrange(today.year, today.month)[1]
    return max(0, last_day - today.day)


MM_TO_INCHES = 1.0 / 25.4


def get_ensemble_precip(lat: float, lon: float, forecast_days: int | None = None) -> list[float]:
    """Open-Meteo Ensemble — returns per-member precipitation totals in inches.

    Open-Meteo returns mm; we convert to inches (Kalshi settles in inches).
    If forecast_days is set (monthly contracts): sum daily values per member.
    Otherwise returns single-day values for day 1.
    """
    days = forecast_days if forecast_days is not None else 2
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=precipitation_sum"
        f"&timezone=auto"
        f"&forecast_days={days}"
    )

    try:
        r = http_get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        totals = []
        daily = data.get("daily", {})

        member_keys = sorted(k for k in daily if k.startswith("precipitation_sum_member"))

        if member_keys:
            for key in member_keys:
                vals = daily[key]
                if forecast_days:
                    member_sum = sum(v for v in vals if v is not None)
                else:
                    member_sum = vals[1] if len(vals) > 1 and vals[1] is not None else 0.0
                totals.append(round(member_sum * MM_TO_INCHES, 4))
        else:
            raw = daily.get("precipitation_sum", [])
            if forecast_days:
                total = sum(v for v in raw if v is not None) * MM_TO_INCHES
            else:
                total = (raw[1] if len(raw) > 1 and raw[1] is not None else 0.0) * MM_TO_INCHES
            totals = [round(total, 4)]

        return totals if totals else [0.0] * 30

    except Exception as e:
        print(f"Open-Meteo Ensemble precip error: {e}")
        return [0.0] * 30


def get_nws_precip_forecast(lat: float, lon: float) -> tuple[float, float]:
    """Get NWS probability of precipitation (PoP) and quantitative forecast (QPF).

    Returns (pop, qpf_inches) where pop is on [0, 1] scale.
    NWS API returns PoP as percentage 0-100; we divide by 100.
    """
    try:
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {"User-Agent": "weather-bot/1.0"}
        r = http_get(points_url, headers=headers, timeout=10)
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        r2 = http_get(forecast_url, headers=headers, timeout=10)
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"]

        if not periods:
            return (0.5, 0.0)

        period = periods[0]
        pop_raw = period.get("probabilityOfPrecipitation", {}).get("value")
        pop = (pop_raw / 100.0) if pop_raw is not None else 0.5

        # QPF: parse accumulation amount from detailedForecast text if present
        qpf = 0.0  # Default; override if detailedForecast contains a quantity
        detail = period.get("detailedForecast", "")
        m = re.search(r'(\d+\.?\d*)\s*(?:inch|in)', detail, re.IGNORECASE)
        if m:
            qpf = float(m.group(1))

        return (max(0.0, min(1.0, pop)), qpf)

    except Exception as e:
        print(f"NWS precip forecast error: {e}")
        return (0.5, 0.0)
