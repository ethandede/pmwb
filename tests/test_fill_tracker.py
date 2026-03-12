import os
import sqlite3
import pytest
from kalshi.fill_tracker import init_trades_db, record_fill, get_unresolved_trades


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_trades.db")
    return db_path


def test_init_creates_trades_table(tmp_db):
    init_trades_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_is_idempotent(tmp_db):
    init_trades_db(tmp_db)
    init_trades_db(tmp_db)  # should not raise


def test_record_fill_inserts_row(tmp_db):
    init_trades_db(tmp_db)
    record_fill(
        db_path=tmp_db,
        order_id="ord_123",
        ticker="KXHIGHNY-26MAR12-B65",
        side="yes",
        limit_price=45,
        fill_price=44,
        fill_qty=2,
        fill_time="2026-03-12T10:00:00Z",
        city="nyc",
    )
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT * FROM trades WHERE order_id='ord_123'").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "ord_123"  # order_id
    assert row[2] == "KXHIGHNY-26MAR12-B65"  # ticker
    assert row[3] == "nyc"  # city
    assert row[4] == "yes"  # side
    assert row[5] == 45  # limit_price
    assert row[6] == 44  # fill_price
    assert row[7] == 2   # fill_qty


def test_record_fill_deduplicates_on_order_id(tmp_db):
    init_trades_db(tmp_db)
    record_fill(tmp_db, "ord_123", "TICK", "yes", 50, 49, 1, "2026-03-12T10:00:00Z")
    record_fill(tmp_db, "ord_123", "TICK", "yes", 50, 49, 1, "2026-03-12T10:00:00Z")
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE order_id='ord_123'").fetchone()[0]
    conn.close()
    assert count == 1


def test_get_unresolved_trades(tmp_db):
    init_trades_db(tmp_db)
    record_fill(tmp_db, "ord_1", "TICK1", "yes", 50, 49, 1, "2026-03-12T10:00:00Z")
    record_fill(tmp_db, "ord_2", "TICK2", "no", 30, 31, 2, "2026-03-12T11:00:00Z")
    rows = get_unresolved_trades(tmp_db)
    assert len(rows) == 2
    assert all(r["settlement_outcome"] is None for r in rows)
