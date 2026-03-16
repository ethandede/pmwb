"""Climatological base rates for precipitation — used to fill blind days beyond ensemble horizon.

Fetches historical daily precip from Open-Meteo Archive API and caches results
in SQLite so we only hit the API once per city/month.
"""

import os
import sqlite3
import requests
import time
from datetime import datetime, timezone

DB_PATH = "data/climate.db"
MM_TO_INCHES = 1.0 / 25.4
LOOKBACK_YEARS = 5


def _get_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS precip_climate (
            city TEXT,
            month INTEGER,
            avg_daily_inches REAL,
            std_daily_inches REAL,
            sample_days INTEGER,
            last_updated TEXT,
            PRIMARY KEY (city, month)
        )
    """)
    conn.commit()
    return conn


def get_daily_precip_rate(city: str, month: int, lat: float, lon: float) -> tuple[float, float]:
    """Get climatological daily precip rate (mean, std) in inches for a city/month.

    Returns cached value if available, otherwise fetches from historical API.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT avg_daily_inches, std_daily_inches FROM precip_climate WHERE city=? AND month=?",
        (city, month),
    ).fetchone()

    if row is not None:
        conn.close()
        return row[0], row[1]

    # Fetch historical data
    avg, std, n = _fetch_historical_precip(lat, lon, month)

    conn.execute(
        """INSERT OR REPLACE INTO precip_climate
           (city, month, avg_daily_inches, std_daily_inches, sample_days, last_updated)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (city, month, avg, std, n, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return avg, std


def _fetch_historical_precip(lat: float, lon: float, month: int) -> tuple[float, float, int]:
    """Fetch daily precip from last N years for a given month. Returns (avg, std, n_days)."""
    import math

    current_year = datetime.now(timezone.utc).year
    all_daily = []

    for year in range(current_year - LOOKBACK_YEARS, current_year):
        # Get the last day of the month
        if month == 12:
            end_date = f"{year}-12-31"
        else:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            end_date = f"{year}-{month:02d}-{last_day:02d}"

        start_date = f"{year}-{month:02d}-01"

        try:
            url = (
                f"https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={lat}&longitude={lon}"
                f"&daily=precipitation_sum"
                f"&timezone=auto"
                f"&start_date={start_date}&end_date={end_date}"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            daily_mm = data.get("daily", {}).get("precipitation_sum", [])
            daily_inches = [d * MM_TO_INCHES for d in daily_mm if d is not None]
            all_daily.extend(daily_inches)
        except Exception:
            pass
        time.sleep(0.1)

    if not all_daily:
        return 0.05, 0.1, 0  # conservative fallback

    avg = sum(all_daily) / len(all_daily)
    variance = sum((d - avg) ** 2 for d in all_daily) / len(all_daily)
    std = math.sqrt(variance)

    return round(avg, 5), round(std, 5), len(all_daily)


def get_temp_std(city: str, month: int, lat: float, lon: float) -> float:
    """Get historical standard deviation of daily high temps (°F) for a city/month.

    Returns cached value if available, otherwise fetches from Open-Meteo archive.
    Used by _deterministic_bucket_prob as the spread parameter instead of the
    hardcoded 4.5°F.
    """
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS temp_climate (
            city TEXT,
            month INTEGER,
            avg_high_f REAL,
            std_high_f REAL,
            sample_days INTEGER,
            last_updated TEXT,
            PRIMARY KEY (city, month)
        )
    """)
    conn.commit()

    row = conn.execute(
        "SELECT std_high_f FROM temp_climate WHERE city=? AND month=?",
        (city, month),
    ).fetchone()

    if row is not None:
        conn.close()
        return row[0]

    # Fetch historical data
    avg, std, n = _fetch_historical_temps(lat, lon, month)

    conn.execute(
        """INSERT OR REPLACE INTO temp_climate
           (city, month, avg_high_f, std_high_f, sample_days, last_updated)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (city, month, avg, std, n, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return std


def _fetch_historical_temps(lat: float, lon: float, month: int) -> tuple[float, float, int]:
    """Fetch daily max temps (°F) from last N years for a given month."""
    import math

    current_year = datetime.now(timezone.utc).year
    all_temps = []

    for year in range(current_year - LOOKBACK_YEARS, current_year):
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        start_date = f"{year}-{month:02d}-01"
        end_date = f"{year}-{month:02d}-{last_day:02d}"

        try:
            url = (
                f"https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={lat}&longitude={lon}"
                f"&daily=temperature_2m_max"
                f"&temperature_unit=fahrenheit"
                f"&timezone=auto"
                f"&start_date={start_date}&end_date={end_date}"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            temps = r.json().get("daily", {}).get("temperature_2m_max", [])
            all_temps.extend([t for t in temps if t is not None])
        except Exception:
            pass
        time.sleep(0.1)

    if not all_temps:
        return 50.0, 8.0, 0  # conservative fallback

    avg = sum(all_temps) / len(all_temps)
    variance = sum((t - avg) ** 2 for t in all_temps) / len(all_temps)
    std = math.sqrt(variance)

    return round(avg, 2), round(std, 2), len(all_temps)


def estimate_blind_day_precip(city: str, month: int, lat: float, lon: float, blind_days: int) -> tuple[float, float]:
    """Estimate total precip for blind (unforecasted) days using climatology.

    Returns (expected_total_inches, std_total_inches).
    The std scales as sqrt(n) * daily_std (assumes daily precip is roughly independent).
    """
    import math

    if blind_days <= 0:
        return 0.0, 0.0

    avg_daily, std_daily = get_daily_precip_rate(city, month, lat, lon)
    expected = avg_daily * blind_days
    # Standard deviation of sum of independent daily values
    std_total = std_daily * math.sqrt(blind_days)

    return round(expected, 4), round(std_total, 4)
