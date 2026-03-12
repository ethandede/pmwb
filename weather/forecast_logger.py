"""Log per-model forecast temps to SQLite for later bias resolution."""

import os
import re
import sqlite3
from datetime import datetime, timezone
from config import BIAS_DB_PATH


def _get_db():
    os.makedirs(os.path.dirname(BIAS_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(BIAS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            city TEXT,
            target_date TEXT,
            model TEXT,
            forecast_temp REAL,
            logged_at TEXT,
            resolved INTEGER DEFAULT 0,
            PRIMARY KEY (city, target_date, model)
        )
    """)
    conn.commit()
    return conn


def log_forecast(city: str, target_date: str, model: str, temp: float, temp_type: str = "max"):
    """Log a model's forecast temp for a city/date. Skips if already logged.

    Args:
        city: City key (e.g. "nyc")
        target_date: YYYY-MM-DD format
        model: "ensemble", "noaa", or "hrrr" (suffixed with _min for low temps)
        temp: Forecast temp in the city's native unit
        temp_type: "max" or "min" — appended to model name for separate bias tracking
    """
    if temp_type == "min":
        model = f"{model}_min"
    conn = _get_db()
    conn.execute(
        """INSERT OR IGNORE INTO forecasts (city, target_date, model, forecast_temp, logged_at)
           VALUES (?, ?, ?, ?, ?)""",
        (city, target_date, model, round(temp, 1), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_unresolved_forecasts() -> list:
    """Return all unresolved forecasts grouped by (city, target_date)."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT city, target_date, model, forecast_temp
           FROM forecasts WHERE resolved = 0
           ORDER BY target_date, city, model"""
    ).fetchall()
    conn.close()
    return rows


def mark_resolved(city: str, target_date: str):
    """Mark all forecasts for a city/date as resolved."""
    conn = _get_db()
    conn.execute(
        "UPDATE forecasts SET resolved = 1 WHERE city = ? AND target_date = ?",
        (city, target_date),
    )
    conn.commit()
    conn.close()


def parse_ticker_date(ticker: str) -> str | None:
    """Extract target date from Kalshi ticker like KXHIGHNY-26MAR12-B55.

    Returns YYYY-MM-DD string or None.
    """
    match = re.search(r'(\d{2})([A-Z]{3})(\d{2})', ticker)
    if not match:
        return None
    year_short, month_str, day = match.groups()
    try:
        dt = datetime.strptime(f"{month_str}{day}20{year_short}", "%b%d%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None
