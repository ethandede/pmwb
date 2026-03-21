# dashboard/scan_cache.py
"""Read/write interface for data/scan_cache.db.

Tables: scan_results (cached market scans), model_outcomes (settled predictions).
"""
import os
import sqlite3
from datetime import datetime, timezone

SCAN_CACHE_DB = "data/scan_cache.db"


def _connect(db_path: str = SCAN_CACHE_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_scan_cache_db(db_path: str = SCAN_CACHE_DB):
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            market_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            city TEXT NOT NULL,
            model_prob REAL NOT NULL,
            market_price REAL NOT NULL,
            edge REAL NOT NULL,
            direction TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            method TEXT,
            threshold TEXT,
            days_left INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_scan_results_latest
            ON scan_results(market_type, scan_time);
        CREATE INDEX IF NOT EXISTS idx_scan_results_heatmap
            ON scan_results(market_type, city, scan_time);

        CREATE TABLE IF NOT EXISTS model_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            city TEXT NOT NULL,
            market_type TEXT NOT NULL,
            predicted_prob REAL NOT NULL,
            market_price REAL NOT NULL,
            actual_outcome INTEGER NOT NULL,
            settled_time TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS city_forecasts (
            city TEXT PRIMARY KEY,
            forecast_high_today REAL,
            forecast_high_tomorrow REAL,
            forecast_low_today REAL,
            forecast_low_tomorrow REAL,
            current_temp REAL,
            mtd_precip_inches REAL,
            forecast_precip_total REAL,
            unit TEXT DEFAULT 'f',
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def write_scan_results(rows: list, scan_time: str = None, db_path: str = SCAN_CACHE_DB):
    if scan_time is None:
        scan_time = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    for r in rows:
        conn.execute(
            """INSERT INTO scan_results
               (scan_time, market_type, ticker, city, model_prob, market_price,
                edge, direction, confidence, method, threshold, days_left)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_time, r["market_type"], r["ticker"], r["city"],
             r["model_prob"], r["market_price"], r["edge"], r["direction"],
             r["confidence"], r.get("method"), r.get("threshold"), r.get("days_left")),
        )
    conn.commit()
    conn.close()


def get_latest_scan(market_type: str, db_path: str = SCAN_CACHE_DB) -> dict:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT scan_time FROM scan_results WHERE market_type = ? ORDER BY scan_time DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    if not row:
        conn.close()
        return {"scan_time": None, "markets": []}
    scan_time = row["scan_time"]
    rows = conn.execute(
        "SELECT * FROM scan_results WHERE market_type = ? AND scan_time = ?",
        (market_type, scan_time),
    ).fetchall()
    conn.close()
    return {
        "scan_time": scan_time,
        "markets": [dict(r) for r in rows],
    }


def get_scan_history(market_type: str, days: int = 30, db_path: str = SCAN_CACHE_DB) -> list:
    """Get scan history for heatmap — one row per city per day (latest scan of each day)."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT city, DATE(scan_time) as scan_date, edge, confidence
           FROM scan_results s1
           WHERE market_type = ?
             AND scan_time >= datetime('now', ?)
             AND scan_time = (
                 SELECT MAX(scan_time) FROM scan_results s2
                 WHERE s2.market_type = s1.market_type
                   AND s2.city = s1.city
                   AND DATE(s2.scan_time) = DATE(s1.scan_time)
             )
           ORDER BY scan_date""",
        (market_type, f"-{days} days"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def write_model_outcome(ticker: str, city: str, market_type: str,
                        predicted_prob: float, market_price: float,
                        actual_outcome: int, db_path: str = SCAN_CACHE_DB):
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO model_outcomes
           (ticker, city, market_type, predicted_prob, market_price, actual_outcome, settled_time)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ticker) DO UPDATE SET
               actual_outcome = excluded.actual_outcome,
               settled_time = excluded.settled_time""",
        (ticker, city, market_type, predicted_prob, market_price,
         actual_outcome, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_model_outcomes(db_path: str = SCAN_CACHE_DB) -> list:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT ticker, city, market_type, predicted_prob as predicted, "
        "market_price as market, actual_outcome as actual, settled_time as settled "
        "FROM model_outcomes ORDER BY settled_time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_old_scans(days: int = 30, db_path: str = SCAN_CACHE_DB):
    conn = _connect(db_path)
    conn.execute(
        "DELETE FROM scan_results WHERE scan_time < datetime('now', ?)",
        (f"-{days} days",),
    )
    conn.commit()
    conn.close()


def write_city_forecasts(rows: list[dict], db_path: str = SCAN_CACHE_DB):
    """Upsert per-city forecast data. Called by daemon every cycle."""
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        conn.execute(
            """INSERT INTO city_forecasts
               (city, forecast_high_today, forecast_high_tomorrow,
                forecast_low_today, forecast_low_tomorrow, current_temp,
                mtd_precip_inches, forecast_precip_total, unit, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(city) DO UPDATE SET
                   forecast_high_today=excluded.forecast_high_today,
                   forecast_high_tomorrow=excluded.forecast_high_tomorrow,
                   forecast_low_today=excluded.forecast_low_today,
                   forecast_low_tomorrow=excluded.forecast_low_tomorrow,
                   current_temp=excluded.current_temp,
                   mtd_precip_inches=excluded.mtd_precip_inches,
                   forecast_precip_total=excluded.forecast_precip_total,
                   unit=excluded.unit,
                   updated_at=excluded.updated_at""",
            (r["city"], r.get("forecast_high_today"), r.get("forecast_high_tomorrow"),
             r.get("forecast_low_today"), r.get("forecast_low_tomorrow"),
             r.get("current_temp"), r.get("mtd_precip_inches"),
             r.get("forecast_precip_total"), r.get("unit", "f"), now),
        )
    conn.commit()
    conn.close()


def get_city_forecasts(db_path: str = SCAN_CACHE_DB) -> dict[str, dict]:
    """Read all city forecasts. Returns {city: {field: value}}."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM city_forecasts").fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {}  # table doesn't exist yet
    conn.close()
    return {r["city"]: dict(r) for r in rows}
