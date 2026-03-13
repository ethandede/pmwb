# tests/test_equity_db.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.equity_db import init_equity_db, record_equity_snapshot, get_equity_curve


def test_init_creates_table(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    import sqlite3
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "equity_snapshots" in tables


def test_record_and_read(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    record_equity_snapshot(
        date="2026-03-13", total_equity=225.04, cash=142.50,
        portfolio_value=82.54, realized_pnl=4.20, fees_paid=2.60,
        win_count=4, loss_count=2, db_path=db,
    )
    curve = get_equity_curve(db_path=db)
    assert len(curve) == 1
    assert curve[0]["date"] == "2026-03-13"
    assert curve[0]["equity"] == 225.04


def test_upsert_same_date(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    record_equity_snapshot(
        date="2026-03-13", total_equity=200.0, cash=100.0,
        portfolio_value=100.0, realized_pnl=0.0, fees_paid=0.0,
        win_count=0, loss_count=0, db_path=db,
    )
    record_equity_snapshot(
        date="2026-03-13", total_equity=225.04, cash=142.50,
        portfolio_value=82.54, realized_pnl=4.20, fees_paid=2.60,
        win_count=4, loss_count=2, db_path=db,
    )
    curve = get_equity_curve(db_path=db)
    assert len(curve) == 1
    assert curve[0]["equity"] == 225.04  # Updated, not duplicated


def test_empty_curve(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    curve = get_equity_curve(db_path=db)
    assert curve == []
