"""ERCOT paper trading engine — SQLite-backed simulated positions.

Tracks paper positions with Kelly sizing, P&L, and expiry.
Uses shared risk limits from risk/position_limits.py.
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

import config as _config
from config import (
    FRACTIONAL_KELLY, MAX_BANKROLL_PCT_PER_TRADE,
    ERCOT_PAPER_BANKROLL, ERCOT_POSITION_TTL_HOURS,
)
from risk.position_limits import check_limits

ERCOT_PAPER_DB = "data/ercot_paper.db"


def _init_db():
    os.makedirs(os.path.dirname(ERCOT_PAPER_DB) or ".", exist_ok=True)
    conn = sqlite3.connect(ERCOT_PAPER_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ercot_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub TEXT NOT NULL,
            hub_name TEXT NOT NULL,
            signal TEXT NOT NULL,
            entry_price REAL NOT NULL,
            size_dollars REAL NOT NULL,
            edge REAL NOT NULL,
            confidence INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ercot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub TEXT NOT NULL,
            hub_name TEXT NOT NULL,
            signal TEXT NOT NULL,
            exit_signal TEXT,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            size_dollars REAL NOT NULL,
            pnl REAL NOT NULL,
            edge_at_entry REAL NOT NULL,
            confidence INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            exit_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS ercot_scan_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub TEXT NOT NULL,
            hub_name TEXT NOT NULL,
            signal TEXT NOT NULL,
            edge REAL NOT NULL,
            expected_solrad_mjm2 REAL,
            current_ercot_price REAL,
            actual_solar_mw REAL,
            confidence INTEGER NOT NULL,
            scanned_at TEXT NOT NULL
        );
    """)
    conn.close()


def _conn():
    _init_db()
    conn = sqlite3.connect(ERCOT_PAPER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def open_position(hub_signal: dict, bankroll: float, max_size: float = None) -> dict | None:
    """Open a paper position with Kelly sizing. Returns position dict or None if blocked.

    Args:
        max_size: optional cap on position size (used by fortify to prevent doubling).
    """
    conn = _conn()

    # Check per-hub limit
    hub = hub_signal["hub"]
    hub_count = conn.execute(
        "SELECT COUNT(*) FROM ercot_positions WHERE hub = ?", (hub,)
    ).fetchone()[0]
    if hub_count >= _config.ERCOT_MAX_POSITIONS_PER_HUB:
        conn.close()
        return None

    # Check total limit
    total_count = conn.execute("SELECT COUNT(*) FROM ercot_positions").fetchone()[0]
    if total_count >= _config.ERCOT_MAX_POSITIONS_TOTAL:
        conn.close()
        return None

    # Kelly sizing: size = min(edge * kelly * bankroll, max_pct * bankroll)
    edge = abs(hub_signal["edge"])
    effective_bankroll = min(bankroll, ERCOT_PAPER_BANKROLL)
    size = min(edge * FRACTIONAL_KELLY * effective_bankroll,
               MAX_BANKROLL_PCT_PER_TRADE * effective_bankroll)

    # Check risk limits
    total_exposure = sum(
        r[0] for r in conn.execute("SELECT size_dollars FROM ercot_positions").fetchall()
    )
    limit_result = check_limits(
        order_dollars=size,
        bankroll=effective_bankroll,
        scan_spent=0.0,
        city_day_spent=0.0,
        total_exposure=total_exposure,
    )
    if limit_result.blocked:
        conn.close()
        return None

    size = limit_result.allowed_dollars
    if max_size is not None:
        size = min(size, max_size)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ERCOT_POSITION_TTL_HOURS)

    conn.execute(
        """INSERT INTO ercot_positions
           (hub, hub_name, signal, entry_price, size_dollars, edge, confidence, opened_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (hub, hub_signal["hub_name"], hub_signal["signal"],
         hub_signal["current_ercot_price"], round(size, 2), edge,
         hub_signal["confidence"], now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    pos_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM ercot_positions WHERE id = ?", (pos_id,)).fetchone()
    conn.close()
    return dict(row)


def close_position(position_id: int, exit_price: float, exit_signal: str, reason: str):
    """Close a paper position and record trade with P&L."""
    conn = _conn()
    row = conn.execute("SELECT * FROM ercot_positions WHERE id = ?", (position_id,)).fetchone()
    if not row:
        conn.close()
        return

    direction = -1.0 if row["signal"] == "SHORT" else 1.0
    pnl = direction * (exit_price - row["entry_price"]) / row["entry_price"] * row["size_dollars"]

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO ercot_trades
           (hub, hub_name, signal, exit_signal, entry_price, exit_price, size_dollars,
            pnl, edge_at_entry, confidence, opened_at, closed_at, exit_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["hub"], row["hub_name"], row["signal"], exit_signal,
         row["entry_price"], exit_price, row["size_dollars"],
         round(pnl, 2), row["edge"], row["confidence"],
         row["opened_at"], now, reason),
    )
    conn.execute("DELETE FROM ercot_positions WHERE id = ?", (position_id,))
    conn.commit()
    conn.close()


def expire_positions(current_price: float):
    """Auto-close any positions past their expiry time."""
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    expired = conn.execute(
        "SELECT * FROM ercot_positions WHERE expires_at < ?", (now,)
    ).fetchall()

    for row in expired:
        direction = -1.0 if row["signal"] == "SHORT" else 1.0
        pnl = direction * (current_price - row["entry_price"]) / row["entry_price"] * row["size_dollars"]

        conn.execute(
            """INSERT INTO ercot_trades
               (hub, hub_name, signal, exit_signal, entry_price, exit_price, size_dollars,
                pnl, edge_at_entry, confidence, opened_at, closed_at, exit_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["hub"], row["hub_name"], row["signal"], "EXPIRED",
             row["entry_price"], current_price, row["size_dollars"],
             round(pnl, 2), row["edge"], row["confidence"],
             row["opened_at"], now, "expired"),
        )
        conn.execute("DELETE FROM ercot_positions WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()


def get_open_positions() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM ercot_positions ORDER BY opened_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_history(limit: int = 50) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM ercot_trades ORDER BY closed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paper_summary() -> dict:
    conn = _conn()
    positions = conn.execute("SELECT * FROM ercot_positions").fetchall()
    trades = conn.execute("SELECT pnl FROM ercot_trades").fetchall()
    conn.close()

    total_pnl = sum(t["pnl"] for t in trades) if trades else 0.0
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total_trades = len(trades)
    open_exposure = sum(p["size_dollars"] for p in positions)

    return {
        "open_count": len(positions),
        "open_exposure": round(open_exposure, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": total_trades - wins,
        "win_rate": round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
        "total_pnl": round(total_pnl, 2),
    }


def write_scan_cache(signals: list):
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM ercot_scan_cache")
    for sig in signals:
        conn.execute(
            """INSERT INTO ercot_scan_cache
               (hub, hub_name, signal, edge, expected_solrad_mjm2,
                current_ercot_price, actual_solar_mw, confidence, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sig["hub"], sig["hub_name"], sig["signal"], sig["edge"],
             sig.get("expected_solrad_mjm2", 0), sig.get("current_ercot_price", 0),
             sig.get("actual_solar_mw", 0), sig["confidence"], now),
        )
    conn.commit()
    conn.close()


def get_cached_signals() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM ercot_scan_cache ORDER BY hub").fetchall()
    conn.close()
    return [dict(r) for r in rows]
