"""Tests for caiso/paper_trader.py — paper position management."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
import pytest

TEST_DB = "data/test_caiso_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import caiso.paper_trader as pt
    pt.CAISO_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(hub="SP15", hub_name="SP15", signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": hub, "hub_name": hub_name, "city": "Los Angeles",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_caiso_price": price, "expected_solrad_mjm2": 19.0,
        "actual_solar_mw": 10000.0,
    }


def test_open_position():
    from caiso.paper_trader import open_position, get_open_positions
    sig = _make_signal()
    result = open_position(sig, bankroll=10000.0)
    assert result is not None
    assert result["hub"] == "SP15"
    assert result["signal"] == "SHORT"
    assert result["size_dollars"] > 0
    positions = get_open_positions()
    assert len(positions) == 1


def test_close_position_pnl_short():
    from caiso.paper_trader import open_position, close_position, get_trade_history
    sig = _make_signal(price=40.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=30.0, exit_signal="LONG", reason="signal flipped")
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0


def test_max_positions_total():
    from caiso.paper_trader import open_position, get_open_positions
    import config
    original = config.CAISO_MAX_POSITIONS_TOTAL
    config.CAISO_MAX_POSITIONS_TOTAL = 1
    open_position(_make_signal(hub="SP15", hub_name="SP15"), bankroll=10000.0)
    result = open_position(_make_signal(hub="NP15", hub_name="NP15"), bankroll=10000.0)
    assert result is None
    assert len(get_open_positions()) == 1
    config.CAISO_MAX_POSITIONS_TOTAL = original


def test_expire_positions():
    from caiso.paper_trader import open_position, expire_positions, get_open_positions, get_trade_history
    sig = _make_signal(price=40.0)
    open_position(sig, bankroll=10000.0)
    conn = sqlite3.connect(TEST_DB)
    conn.execute("UPDATE caiso_positions SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    conn.commit()
    conn.close()
    expire_positions(current_price=42.0)
    assert len(get_open_positions()) == 0
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "expired"


def test_paper_summary():
    from caiso.paper_trader import open_position, close_position, get_paper_summary
    sig = _make_signal(price=40.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=30.0, exit_signal="LONG", reason="test")
    summary = get_paper_summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
    assert summary["open_count"] == 0


def test_scan_cache_write_and_read():
    from caiso.paper_trader import write_scan_cache, get_cached_signals
    signals = [
        {"hub": "SP15", "hub_name": "SP15", "signal": "SHORT",
         "edge": 1.5, "expected_solrad_mjm2": 19.0,
         "current_caiso_price": 40.0, "actual_solar_mw": 10000.0,
         "confidence": 70},
    ]
    write_scan_cache(signals)
    cached = get_cached_signals()
    assert len(cached) >= 1
    assert cached[0]["hub"] == "SP15"
