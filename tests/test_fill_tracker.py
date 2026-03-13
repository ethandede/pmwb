import os
import sqlite3
import pytest
from kalshi.fill_tracker import init_trades_db, record_fill, update_fill_data, get_unresolved_trades


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


def test_record_fill_preserves_side_and_city_on_conflict(tmp_db):
    """Poller upsert must not overwrite side or city set by execute_kalshi_signal."""
    init_trades_db(tmp_db)
    # Initial insert from execute_kalshi_signal with canonical side and city
    record_fill(tmp_db, "ord_500", "TICK", "buy_yes", 45, 0, 0, "2026-03-12T10:00:00Z", city="Chicago")
    # Simulated poller upsert with Kalshi raw side and empty city
    record_fill(tmp_db, "ord_500", "TICK", "yes", 45, 44, 3, "2026-03-12T10:05:00Z", city="")
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT side, city, fill_price, fill_qty FROM trades WHERE order_id='ord_500'").fetchone()
    conn.close()
    assert row[0] == "buy_yes", f"side was overwritten to '{row[0]}'"
    assert row[1] == "Chicago", f"city was overwritten to '{row[1]}'"
    assert row[2] == 44   # fill_price updated
    assert row[3] == 3    # fill_qty updated


def test_update_fill_data_preserves_side_and_city(tmp_db):
    """update_fill_data only touches fill fields, never side or city."""
    init_trades_db(tmp_db)
    record_fill(tmp_db, "ord_600", "TICK", "buy_no", 55, 0, 0, "2026-03-12T10:00:00Z", city="Denver")
    update_fill_data(tmp_db, "ord_600", fill_price=53, fill_qty=5, fill_time="2026-03-12T10:10:00Z")
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT side, city, fill_price, fill_qty, fill_time FROM trades WHERE order_id='ord_600'").fetchone()
    conn.close()
    assert row[0] == "buy_no"
    assert row[1] == "Denver"
    assert row[2] == 53
    assert row[3] == 5
    assert row[4] == "2026-03-12T10:10:00Z"


def test_update_fill_data_noop_for_unknown_order(tmp_db):
    """update_fill_data does nothing if the order_id doesn't exist."""
    init_trades_db(tmp_db)
    update_fill_data(tmp_db, "ord_nonexistent", fill_price=50, fill_qty=1, fill_time="2026-03-12T10:00:00Z")
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    assert count == 0


def test_update_fill_data_skips_lower_qty(tmp_db):
    """update_fill_data won't downgrade fill_qty."""
    init_trades_db(tmp_db)
    record_fill(tmp_db, "ord_700", "TICK", "buy_yes", 40, 39, 10, "2026-03-12T10:00:00Z", city="NYC")
    update_fill_data(tmp_db, "ord_700", fill_price=38, fill_qty=5, fill_time="2026-03-12T10:05:00Z")
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT fill_price, fill_qty FROM trades WHERE order_id='ord_700'").fetchone()
    conn.close()
    assert row[0] == 39  # unchanged
    assert row[1] == 10  # unchanged
