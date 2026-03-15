"""Continuous optimization engine — computes rolling stats and parameter recommendations."""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

ANALYTICS_DB = "data/analytics.db"
TRADES_DB = "data/trades.db"


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_analytics_db(db_path: str = ANALYTICS_DB):
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            net_pnl REAL,
            avg_win REAL,
            avg_loss REAL,
            hit_rate REAL
        );

        CREATE TABLE IF NOT EXISTS bucket_stats (
            date TEXT,
            bucket_type TEXT,
            bucket_value TEXT,
            trades INTEGER,
            wins INTEGER,
            hit_rate REAL,
            avg_pnl REAL,
            PRIMARY KEY (date, bucket_type, bucket_value)
        );

        CREATE TABLE IF NOT EXISTS manager_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            city TEXT,
            action TEXT,
            reason TEXT,
            edge REAL,
            spread REAL
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            param_name TEXT,
            current_value TEXT,
            suggested_value TEXT,
            reason TEXT,
            sample_size INTEGER,
            confidence TEXT DEFAULT 'low',
            status TEXT DEFAULT 'pending'
        );
    """)
    conn.commit()
    conn.close()


def record_manager_action(ticker: str, city: str, action: str, reason: str,
                          edge: float = 0, spread: float = 0,
                          analytics_db: str = ANALYTICS_DB):
    """Record a position manager decision for analytics."""
    try:
        init_analytics_db(analytics_db)
        conn = _connect(analytics_db)
        conn.execute("""
            INSERT INTO manager_actions (timestamp, ticker, city, action, reason, edge, spread)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat(), ticker, city, action, reason, edge, spread))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_action_summary(analytics_db: str = ANALYTICS_DB, days: int = 7) -> dict:
    """Get summary of position manager actions over the last N days."""
    try:
        conn = _connect(analytics_db)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT action, COUNT(*) as count FROM manager_actions
            WHERE timestamp > ? GROUP BY action
        """, (cutoff,)).fetchall()

        spread_blocked = conn.execute("""
            SELECT COUNT(*) as count FROM manager_actions
            WHERE timestamp > ? AND reason LIKE '%spread%'
        """, (cutoff,)).fetchone()

        conn.close()
        summary = {r["action"]: r["count"] for r in rows}
        summary["spread_blocked"] = spread_blocked["count"] if spread_blocked else 0
        return summary
    except Exception:
        return {}


def compute_daily_stats(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Aggregate settled trades into daily stats."""
    init_analytics_db(analytics_db)
    trades_conn = sqlite3.connect(trades_db)
    trades_conn.row_factory = sqlite3.Row

    rows = trades_conn.execute("""
        SELECT DATE(fill_time) as date,
               COUNT(*) as total,
               SUM(CASE WHEN settlement_outcome = 'win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN settlement_outcome = 'loss' THEN 1 ELSE 0 END) as losses,
               SUM(pnl) as net_pnl,
               AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
               AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
        FROM trades
        WHERE settlement_outcome IN ('win', 'loss')
          AND NOT side LIKE 'sell_%'
          AND fill_qty > 0
        GROUP BY DATE(fill_time)
    """).fetchall()
    trades_conn.close()

    a_conn = _connect(analytics_db)
    for r in rows:
        total = r["wins"] + r["losses"]
        hit_rate = r["wins"] / total if total > 0 else 0
        a_conn.execute("""
            INSERT OR REPLACE INTO daily_stats
            (date, total_trades, wins, losses, net_pnl, avg_win, avg_loss, hit_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (r["date"], total, r["wins"], r["losses"],
              round(r["net_pnl"] or 0, 2),
              round(r["avg_win"] or 0, 2),
              round(r["avg_loss"] or 0, 2),
              round(hit_rate, 4)))
    a_conn.commit()
    a_conn.close()


def compute_bucket_stats(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Compute hit rate breakdowns by city."""
    init_analytics_db(analytics_db)
    trades_conn = sqlite3.connect(trades_db)
    trades_conn.row_factory = sqlite3.Row
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    city_rows = trades_conn.execute("""
        SELECT city, COUNT(*) as trades,
               SUM(CASE WHEN settlement_outcome='win' THEN 1 ELSE 0 END) as wins,
               AVG(pnl) as avg_pnl
        FROM trades
        WHERE settlement_outcome IN ('win','loss') AND NOT side LIKE 'sell_%' AND fill_qty > 0
        GROUP BY city
    """).fetchall()

    a_conn = _connect(analytics_db)
    for r in city_rows:
        total = r["trades"]
        hit_rate = r["wins"] / total if total > 0 else 0
        a_conn.execute("""
            INSERT OR REPLACE INTO bucket_stats
            (date, bucket_type, bucket_value, trades, wins, hit_rate, avg_pnl)
            VALUES (?, 'city', ?, ?, ?, ?, ?)
        """, (today, r["city"], total, r["wins"], round(hit_rate, 4), round(r["avg_pnl"] or 0, 2)))

    a_conn.commit()
    a_conn.close()
    trades_conn.close()


def generate_recommendations(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Analyze performance and generate parameter recommendations."""
    init_analytics_db(analytics_db)
    trades_conn = sqlite3.connect(trades_db)
    trades_conn.row_factory = sqlite3.Row
    a_conn = _connect(analytics_db)
    now = datetime.now(timezone.utc).isoformat()

    a_conn.execute("DELETE FROM recommendations WHERE status='pending'")

    # 1. City exclusion: any city with <40% hit rate on 20+ trades
    city_rows = trades_conn.execute("""
        SELECT city, COUNT(*) as trades,
               SUM(CASE WHEN settlement_outcome='win' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE settlement_outcome IN ('win','loss') AND NOT side LIKE 'sell_%' AND fill_qty > 0
        GROUP BY city HAVING trades >= 20
    """).fetchall()

    for r in city_rows:
        hit_rate = r["wins"] / r["trades"] if r["trades"] > 0 else 0
        if hit_rate < 0.40:
            confidence = "high" if r["trades"] >= 30 else "medium"
            a_conn.execute("""
                INSERT INTO recommendations (created_at, param_name, current_value, suggested_value, reason, sample_size, confidence)
                VALUES (?, 'city_exclusion', 'included', ?, ?, ?, ?)
            """, (now, r["city"],
                  f"{r['city']} has {hit_rate:.0%} hit rate over {r['trades']} trades",
                  r["trades"], confidence))

    # 2. Win/loss ratio check
    ratio_row = trades_conn.execute("""
        SELECT AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
               AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss,
               COUNT(*) as total
        FROM trades
        WHERE settlement_outcome IN ('win','loss') AND NOT side LIKE 'sell_%' AND fill_qty > 0
    """).fetchone()

    if ratio_row and ratio_row["avg_win"] and ratio_row["avg_loss"] and ratio_row["total"] >= 20:
        ratio = abs(ratio_row["avg_win"] / ratio_row["avg_loss"])
        if ratio < 0.5:
            a_conn.execute("""
                INSERT INTO recommendations (created_at, param_name, current_value, suggested_value, reason, sample_size, confidence)
                VALUES (?, 'kelly_multiplier', '0.25', '0.15', ?, ?, 'medium')
            """, (now,
                  f"Win/loss ratio is {ratio:.2f} (wins avg ${ratio_row['avg_win']:.2f}, losses avg ${ratio_row['avg_loss']:.2f}). Reduce position sizing.",
                  ratio_row["total"]))

    a_conn.commit()
    a_conn.close()
    trades_conn.close()


def run_analytics(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Main entry point — run all analytics passes."""
    try:
        compute_daily_stats(trades_db, analytics_db)
        compute_bucket_stats(trades_db, analytics_db)
        generate_recommendations(trades_db, analytics_db)

        try:
            from config import TELEGRAM_DAILY_SCORECARD
        except ImportError:
            TELEGRAM_DAILY_SCORECARD = False

        if TELEGRAM_DAILY_SCORECARD:
            try:
                from analytics.alerts import send_daily_scorecard, send_recommendation_alert
                conn = _connect(analytics_db)
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                today_stats = conn.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
                if today_stats:
                    send_daily_scorecard(dict(today_stats))

                high_recs = conn.execute(
                    "SELECT * FROM recommendations WHERE status='pending' AND confidence='high'"
                ).fetchall()
                for r in high_recs:
                    send_recommendation_alert(dict(r))
                conn.close()
            except Exception:
                pass
    except Exception as e:
        print(f"  [Analytics] Error: {e}")
