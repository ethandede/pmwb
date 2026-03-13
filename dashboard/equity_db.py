# dashboard/equity_db.py
"""Read/write interface for data/equity_history.db.

Table: equity_snapshots — one row per day, appended by daily P&L script.
"""
import os
import sqlite3

EQUITY_DB = "data/equity_history.db"


def _connect(db_path: str = EQUITY_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_equity_db(db_path: str = EQUITY_DB):
    conn = _connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_equity REAL NOT NULL,
            cash REAL NOT NULL,
            portfolio_value REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            fees_paid REAL NOT NULL,
            win_count INTEGER NOT NULL DEFAULT 0,
            loss_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def record_equity_snapshot(date: str, total_equity: float, cash: float,
                           portfolio_value: float, realized_pnl: float,
                           fees_paid: float, win_count: int, loss_count: int,
                           db_path: str = EQUITY_DB):
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO equity_snapshots
           (date, total_equity, cash, portfolio_value, realized_pnl, fees_paid, win_count, loss_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
               total_equity = excluded.total_equity,
               cash = excluded.cash,
               portfolio_value = excluded.portfolio_value,
               realized_pnl = excluded.realized_pnl,
               fees_paid = excluded.fees_paid,
               win_count = excluded.win_count,
               loss_count = excluded.loss_count""",
        (date, total_equity, cash, portfolio_value, realized_pnl, fees_paid, win_count, loss_count),
    )
    conn.commit()
    conn.close()


def get_equity_curve(db_path: str = EQUITY_DB) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT date, total_equity as equity, realized_pnl, fees_paid as fees "
        "FROM equity_snapshots ORDER BY date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
