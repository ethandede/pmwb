"""Track Kalshi order fills in SQLite for backtesting and P&L analysis."""

import os
import sqlite3
from typing import Optional


def init_trades_db(db_path: str = "data/trades.db"):
    """Create the trades table if it doesn't exist."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            ticker TEXT NOT NULL,
            city TEXT,
            side TEXT NOT NULL,
            limit_price INTEGER,
            fill_price INTEGER,
            fill_qty INTEGER,
            fill_time TEXT,
            settlement_outcome TEXT,
            pnl REAL
        )
    """)
    conn.commit()
    conn.close()


def record_fill(
    db_path: str,
    order_id: str,
    ticker: str,
    side: str,
    limit_price: int,
    fill_price: int,
    fill_qty: int,
    fill_time: str,
    city: str = "",
    settlement_outcome: Optional[str] = None,
    pnl: Optional[float] = None,
):
    """Record a fill. Updates fill data if order already exists (for resting order polling)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO trades
           (order_id, ticker, city, side, limit_price, fill_price, fill_qty, fill_time, settlement_outcome, pnl)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(order_id) DO UPDATE SET
               fill_price = excluded.fill_price,
               fill_qty = excluded.fill_qty,
               fill_time = excluded.fill_time
           WHERE excluded.fill_qty > trades.fill_qty""",
        (order_id, ticker, city, side, limit_price, fill_price, fill_qty, fill_time, settlement_outcome, pnl),
    )
    conn.commit()
    conn.close()


def get_unresolved_trades(db_path: str = "data/trades.db") -> list[dict]:
    """Return all trades without a settlement outcome."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE settlement_outcome IS NULL ORDER BY fill_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_trade(db_path: str, order_id: str, settlement_outcome: str, pnl: float):
    """Update a trade with its settlement outcome and P&L."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE trades SET settlement_outcome=?, pnl=? WHERE order_id=?",
        (settlement_outcome, pnl, order_id),
    )
    conn.commit()
    conn.close()


def get_all_trades(db_path: str = "data/trades.db") -> list[dict]:
    """Return all trades for reporting."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades ORDER BY fill_time").fetchall()
    conn.close()
    return [dict(r) for r in rows]
