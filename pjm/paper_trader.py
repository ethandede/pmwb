"""PJM paper trading engine — directional solar-price signals.

Directional contracts: SHORT (expect price drop) or LONG (expect price rise).
Positions expire after PJM_POSITION_TTL_HOURS and are settled at current price.
"""

import os
import shutil
import sqlite3
from datetime import datetime, timezone, timedelta

import config as _config
from config import (
    FRACTIONAL_KELLY, MAX_BANKROLL_PCT_PER_TRADE,
    PJM_PAPER_BANKROLL,
    PJM_POSITION_TTL_HOURS,
)
from risk.position_limits import check_limits

PJM_PAPER_DB = "data/pjm_paper.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pjm_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    city TEXT NOT NULL,
    signal TEXT NOT NULL,
    edge REAL NOT NULL,
    confidence INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    size_dollars REAL NOT NULL,
    expected_solrad_mjm2 REAL,
    actual_solar_mw REAL,
    current_pjm_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pjm_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    city TEXT NOT NULL,
    signal TEXT NOT NULL,
    edge REAL NOT NULL,
    confidence INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    size_dollars REAL NOT NULL,
    pnl REAL NOT NULL,
    expected_solrad_mjm2 REAL,
    actual_solar_mw REAL,
    current_pjm_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    exit_signal TEXT,
    exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS pjm_scan_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    signal TEXT NOT NULL,
    edge REAL NOT NULL,
    expected_solrad_mjm2 REAL,
    current_pjm_price REAL NOT NULL,
    actual_solar_mw REAL,
    confidence INTEGER NOT NULL,
    scanned_at TEXT NOT NULL
);
"""


def _init_db():
    os.makedirs(os.path.dirname(PJM_PAPER_DB) or ".", exist_ok=True)
    if os.path.exists(PJM_PAPER_DB):
        bak = PJM_PAPER_DB + ".bak"
        try:
            shutil.copy2(PJM_PAPER_DB, bak)
        except OSError:
            pass
    conn = sqlite3.connect(PJM_PAPER_DB)
    conn.executescript(_SCHEMA)
    conn.close()


def _conn():
    _init_db()
    conn = sqlite3.connect(PJM_PAPER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def open_position(hub_signal: dict, bankroll: float, max_size: float = None) -> dict | None:
    """Open a directional paper position with Kelly sizing.

    hub_signal keys required:
        hub, hub_name, signal, edge, confidence, current_pjm_price
    """
    conn = _conn()

    hub = hub_signal["hub"]

    # Check per-hub limit
    hub_count = conn.execute(
        "SELECT COUNT(*) FROM pjm_positions WHERE hub = ?", (hub,)
    ).fetchone()[0]
    if hub_count >= _config.PJM_MAX_POSITIONS_PER_HUB:
        conn.close()
        return None

    # Check total limit
    total_count = conn.execute("SELECT COUNT(*) FROM pjm_positions").fetchone()[0]
    if total_count >= _config.PJM_MAX_POSITIONS_TOTAL:
        conn.close()
        return None

    # Kelly sizing
    edge = hub_signal["edge"]
    effective_bankroll = min(bankroll, PJM_PAPER_BANKROLL)
    size = min(
        abs(edge) * FRACTIONAL_KELLY * effective_bankroll,
        MAX_BANKROLL_PCT_PER_TRADE * effective_bankroll,
    )

    # Check risk limits
    total_exposure = sum(
        r[0] for r in conn.execute("SELECT size_dollars FROM pjm_positions").fetchall()
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
    expires_at = now + timedelta(hours=PJM_POSITION_TTL_HOURS)

    conn.execute(
        """INSERT INTO pjm_positions
           (hub, hub_name, city, signal, edge, confidence,
            entry_price, size_dollars, expected_solrad_mjm2, actual_solar_mw,
            current_pjm_price, opened_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            hub, hub_signal["hub_name"],
            hub_signal.get("city", ""),
            hub_signal["signal"], edge, hub_signal["confidence"],
            hub_signal["current_pjm_price"], round(size, 2),
            hub_signal.get("expected_solrad_mjm2"),
            hub_signal.get("actual_solar_mw"),
            hub_signal["current_pjm_price"],
            now.isoformat(), expires_at.isoformat(),
        ),
    )
    conn.commit()
    pos_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM pjm_positions WHERE id = ?", (pos_id,)).fetchone()
    conn.close()
    return dict(row)


def close_position(position_id: int, exit_price: float, exit_signal: str, reason: str):
    """Close a position and record trade with P&L.

    P&L logic:
        SHORT: profit when price drops  -> pnl = (entry - exit) / entry * size
        LONG:  profit when price rises   -> pnl = (exit - entry) / entry * size
    """
    conn = _conn()
    row = conn.execute("SELECT * FROM pjm_positions WHERE id = ?", (position_id,)).fetchone()
    if row is None:
        conn.close()
        return

    entry = row["entry_price"]
    size = row["size_dollars"]

    if row["signal"] == "SHORT":
        pnl = (entry - exit_price) / entry * size if entry != 0 else 0.0
    else:  # LONG
        pnl = (exit_price - entry) / entry * size if entry != 0 else 0.0

    closed_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO pjm_trades
           (hub, hub_name, city, signal, edge, confidence,
            entry_price, exit_price, size_dollars, pnl,
            expected_solrad_mjm2, actual_solar_mw, current_pjm_price,
            opened_at, closed_at, exit_signal, exit_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["hub"], row["hub_name"], row["city"],
            row["signal"], row["edge"], row["confidence"],
            entry, exit_price, size, round(pnl, 2),
            row["expected_solrad_mjm2"], row["actual_solar_mw"],
            row["current_pjm_price"],
            row["opened_at"], closed_at, exit_signal, reason,
        ),
    )
    conn.execute("DELETE FROM pjm_positions WHERE id = ?", (position_id,))
    conn.commit()
    conn.close()


def expire_positions(current_price: float):
    """Auto-close positions past their TTL."""
    conn = _conn()
    now = datetime.now(timezone.utc)
    positions = conn.execute("SELECT * FROM pjm_positions").fetchall()

    for row in positions:
        try:
            expires_at_str = row["expires_at"]
            # Handle both offset-aware and naive ISO strings
            if expires_at_str.endswith("+00:00") or expires_at_str.endswith("Z"):
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            else:
                expires_at = datetime.fromisoformat(expires_at_str).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        if now >= expires_at:
            entry = row["entry_price"]
            size = row["size_dollars"]
            if row["signal"] == "SHORT":
                pnl = (entry - current_price) / entry * size if entry != 0 else 0.0
            else:
                pnl = (current_price - entry) / entry * size if entry != 0 else 0.0

            closed_at = now.isoformat()
            conn.execute(
                """INSERT INTO pjm_trades
                   (hub, hub_name, city, signal, edge, confidence,
                    entry_price, exit_price, size_dollars, pnl,
                    expected_solrad_mjm2, actual_solar_mw, current_pjm_price,
                    opened_at, closed_at, exit_signal, exit_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hub"], row["hub_name"], row["city"],
                    row["signal"], row["edge"], row["confidence"],
                    entry, current_price, size, round(pnl, 2),
                    row["expected_solrad_mjm2"], row["actual_solar_mw"],
                    row["current_pjm_price"],
                    row["opened_at"], closed_at, "", "expired",
                ),
            )
            conn.execute("DELETE FROM pjm_positions WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()


def get_open_positions() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM pjm_positions ORDER BY opened_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_history(limit: int = 50) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM pjm_trades ORDER BY closed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paper_summary() -> dict:
    conn = _conn()
    positions = conn.execute("SELECT * FROM pjm_positions").fetchall()
    trades = conn.execute("SELECT pnl FROM pjm_trades").fetchall()
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
    conn.execute("DELETE FROM pjm_scan_cache")
    for sig in signals:
        conn.execute(
            """INSERT INTO pjm_scan_cache
               (hub, hub_name, signal, edge, expected_solrad_mjm2,
                current_pjm_price, actual_solar_mw, confidence, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig["hub"], sig["hub_name"],
                sig.get("signal", ""), sig["edge"],
                sig.get("expected_solrad_mjm2"),
                sig.get("current_pjm_price", 0.0),
                sig.get("actual_solar_mw"),
                sig["confidence"], now,
            ),
        )
    conn.commit()
    conn.close()


def get_cached_signals() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM pjm_scan_cache ORDER BY hub").fetchall()
    conn.close()
    return [dict(r) for r in rows]
