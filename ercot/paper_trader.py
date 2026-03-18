"""ERCOT paper trading engine — binary options with hourly settlement.

Binary contracts: P(RT >= DAM) per hub per hour.
Settlement: $100 if RT >= DAM, $0 otherwise.
"""

import os
import shutil
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import config as _config
from config import (
    FRACTIONAL_KELLY, MAX_BANKROLL_PCT_PER_TRADE,
    ERCOT_PAPER_BANKROLL,
    ERCOT_MAX_POSITIONS_PER_HUB, ERCOT_MAX_POSITIONS_TOTAL,
)
from risk.position_limits import check_limits

ERCOT_PAPER_DB = "data/ercot_paper.db"

CT = ZoneInfo("America/Chicago")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ercot_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    contract_date TEXT NOT NULL,
    contract_hour INTEGER NOT NULL,
    side TEXT NOT NULL,
    dam_price REAL NOT NULL,
    entry_price REAL NOT NULL,
    size_dollars REAL NOT NULL,
    model_prob REAL NOT NULL,
    edge REAL NOT NULL,
    confidence INTEGER NOT NULL,
    opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ercot_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    contract_date TEXT NOT NULL,
    contract_hour INTEGER NOT NULL,
    side TEXT NOT NULL,
    dam_price REAL NOT NULL,
    rt_price REAL,
    entry_price REAL NOT NULL,
    size_dollars REAL NOT NULL,
    settlement_value INTEGER,
    pnl REAL NOT NULL,
    model_prob REAL NOT NULL,
    edge REAL NOT NULL,
    confidence INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS ercot_scan_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    contract_date TEXT NOT NULL,
    contract_hour INTEGER NOT NULL,
    side TEXT NOT NULL,
    dam_price REAL NOT NULL,
    model_prob REAL NOT NULL,
    edge REAL NOT NULL,
    expected_solrad_mjm2 REAL,
    confidence INTEGER NOT NULL,
    scanned_at TEXT NOT NULL
);
"""


def _init_db():
    os.makedirs(os.path.dirname(ERCOT_PAPER_DB) or ".", exist_ok=True)
    # Back up existing DB if present (schema migration)
    if os.path.exists(ERCOT_PAPER_DB):
        bak = ERCOT_PAPER_DB + ".bak"
        try:
            shutil.copy2(ERCOT_PAPER_DB, bak)
        except OSError:
            pass
    conn = sqlite3.connect(ERCOT_PAPER_DB)
    conn.executescript(_SCHEMA)
    conn.close()


def _conn():
    _init_db()
    conn = sqlite3.connect(ERCOT_PAPER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def open_position(hub_signal: dict, bankroll: float, max_size: float = None) -> dict | None:
    """Open a paper position with Kelly sizing. Returns position dict or None if blocked.

    hub_signal keys required:
        hub, hub_name, contract_date, contract_hour, side, dam_price,
        entry_price, model_prob, edge, confidence
    """
    conn = _conn()

    hub = hub_signal["hub"]
    contract_date = hub_signal["contract_date"]
    contract_hour = hub_signal["contract_hour"]

    # Dedup: one position per hub + contract_date + contract_hour
    existing = conn.execute(
        "SELECT COUNT(*) FROM ercot_positions WHERE hub = ? AND contract_date = ? AND contract_hour = ?",
        (hub, contract_date, contract_hour),
    ).fetchone()[0]
    if existing > 0:
        conn.close()
        return None

    # Check per-hub limit (across all hours for this hub)
    hub_count = conn.execute(
        "SELECT COUNT(*) FROM ercot_positions WHERE hub = ?", (hub,)
    ).fetchone()[0]
    if hub_count >= ERCOT_MAX_POSITIONS_PER_HUB:
        conn.close()
        return None

    # Check total limit
    total_count = conn.execute("SELECT COUNT(*) FROM ercot_positions").fetchone()[0]
    if total_count >= ERCOT_MAX_POSITIONS_TOTAL:
        conn.close()
        return None

    # Kelly sizing: size = min(|edge| * kelly * bankroll, max_pct * bankroll)
    edge = hub_signal["edge"]
    effective_bankroll = min(bankroll, ERCOT_PAPER_BANKROLL)
    size = min(
        abs(edge) * FRACTIONAL_KELLY * effective_bankroll,
        MAX_BANKROLL_PCT_PER_TRADE * effective_bankroll,
    )

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

    now = datetime.now(CT).isoformat()

    conn.execute(
        """INSERT INTO ercot_positions
           (hub, hub_name, contract_date, contract_hour, side, dam_price,
            entry_price, size_dollars, model_prob, edge, confidence, opened_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            hub, hub_signal["hub_name"],
            contract_date, contract_hour,
            hub_signal["side"], hub_signal["dam_price"],
            hub_signal["entry_price"], round(size, 2),
            hub_signal["model_prob"], edge,
            hub_signal["confidence"], now,
        ),
    )
    conn.commit()
    pos_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM ercot_positions WHERE id = ?", (pos_id,)).fetchone()
    conn.close()
    return dict(row)


def settle_expired_hours(fetch_rt_fn) -> list:
    """Settle all positions whose contract_hour has expired.

    For each open position where now_ct >= expiry (contract_date HE contract_hour),
    call fetch_rt_fn(hub, contract_hour, contract_date). If it returns None, skip.
    Otherwise determine settlement_value and record P&L.

    Args:
        fetch_rt_fn: callable(hub: str, hour: int, date: str) -> float | None
            Returns real-time price for the hub/hour/date, or None if unavailable.

    Returns:
        List of settled trade dicts.
    """
    conn = _conn()
    now_ct = datetime.now(CT)
    positions = conn.execute("SELECT * FROM ercot_positions ORDER BY opened_at").fetchall()

    settled = []
    for row in positions:
        # Parse expiry: contract_hour is HE, so the hour ending at contract_hour:00
        # e.g. HE14 ends at 14:00 (2:00 PM CT)
        try:
            expiry = datetime(
                *[int(x) for x in row["contract_date"].split("-")],
                row["contract_hour"], 0, 0,
                tzinfo=CT,
            )
        except (ValueError, TypeError):
            continue

        if now_ct < expiry:
            continue

        # Fetch real-time price
        rt_price = fetch_rt_fn(row["hub"], row["contract_hour"], row["contract_date"])
        if rt_price is None:
            continue

        # Binary settlement
        settlement_value = 100 if rt_price >= row["dam_price"] else 0

        # P&L calculation
        entry = row["entry_price"]
        size = row["size_dollars"]
        if row["side"] == "yes":
            pnl = (settlement_value / 100.0 - entry) * size
        else:
            pnl = ((100 - settlement_value) / 100.0 - entry) * size

        closed_at = now_ct.isoformat()
        conn.execute(
            """INSERT INTO ercot_trades
               (hub, hub_name, contract_date, contract_hour, side, dam_price,
                rt_price, entry_price, size_dollars, settlement_value, pnl,
                model_prob, edge, confidence, opened_at, closed_at, exit_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["hub"], row["hub_name"],
                row["contract_date"], row["contract_hour"],
                row["side"], row["dam_price"],
                rt_price, entry, size,
                settlement_value, round(pnl, 2),
                row["model_prob"], row["edge"], row["confidence"],
                row["opened_at"], closed_at, "settled",
            ),
        )
        conn.execute("DELETE FROM ercot_positions WHERE id = ?", (row["id"],))

        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        trade_row = conn.execute("SELECT * FROM ercot_trades WHERE id = ?", (trade_id,)).fetchone()
        settled.append(dict(trade_row))

    conn.commit()
    conn.close()
    return settled


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
    now = datetime.now(CT).isoformat()
    conn.execute("DELETE FROM ercot_scan_cache")
    for sig in signals:
        conn.execute(
            """INSERT INTO ercot_scan_cache
               (hub, hub_name, contract_date, contract_hour, side, dam_price,
                model_prob, edge, expected_solrad_mjm2, confidence, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig["hub"], sig["hub_name"],
                sig.get("contract_date", ""), sig.get("contract_hour", 0),
                sig.get("side", ""), sig.get("dam_price", 0.0),
                sig.get("model_prob", 0.0), sig["edge"],
                sig.get("expected_solrad_mjm2", None),
                sig["confidence"], now,
            ),
        )
    conn.commit()
    conn.close()


def get_cached_signals() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM ercot_scan_cache ORDER BY hub").fetchall()
    conn.close()
    return [dict(r) for r in rows]
