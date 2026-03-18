"""Tests for pjm/paper_trader.py — paper position management."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
import pytest

TEST_DB = "data/test_pjm_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import pjm.paper_trader as pt
    pt.PJM_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(hub="Western", hub_name="WESTERN", signal="SHORT", edge=1.5, confidence=70, price=35.0):
    return {
        "hub": hub, "hub_name": hub_name, "city": "Pittsburgh",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_pjm_price": price, "expected_solrad_mjm2": 12.0,
        "actual_solar_mw": 5000.0,
    }


def test_open_position():
    from pjm.paper_trader import open_position, get_open_positions
    sig = _make_signal()
    result = open_position(sig, bankroll=10000.0)
    assert result is not None
    assert result["hub"] == "Western"
    assert result["signal"] == "SHORT"
    assert result["size_dollars"] > 0
    positions = get_open_positions()
    assert len(positions) == 1


def test_close_position_pnl_short():
    from pjm.paper_trader import open_position, close_position, get_trade_history
    sig = _make_signal(price=35.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=25.0, exit_signal="LONG", reason="signal flipped")
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0


def test_close_position_pnl_long():
    from pjm.paper_trader import open_position, close_position, get_trade_history
    sig = _make_signal(signal="LONG", edge=1.2, price=35.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=45.0, exit_signal="NEUTRAL", reason="edge decay")
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0


def test_max_positions_per_hub():
    from pjm.paper_trader import open_position, get_open_positions
    import config
    original = config.PJM_MAX_POSITIONS_PER_HUB
    config.PJM_MAX_POSITIONS_PER_HUB = 2
    open_position(_make_signal(), bankroll=10000.0)
    open_position(_make_signal(), bankroll=10000.0)
    result = open_position(_make_signal(), bankroll=10000.0)
    assert result is None
    assert len(get_open_positions()) == 2
    config.PJM_MAX_POSITIONS_PER_HUB = original


def test_max_positions_total():
    from pjm.paper_trader import open_position, get_open_positions
    import config
    original = config.PJM_MAX_POSITIONS_TOTAL
    config.PJM_MAX_POSITIONS_TOTAL = 2
    open_position(_make_signal(hub="Western", hub_name="WESTERN"), bankroll=10000.0)
    open_position(_make_signal(hub="AEP-Dayton", hub_name="AEP"), bankroll=10000.0)
    result = open_position(_make_signal(hub="NI", hub_name="NI"), bankroll=10000.0)
    assert result is None
    assert len(get_open_positions()) == 2
    config.PJM_MAX_POSITIONS_TOTAL = original


def test_expire_positions():
    from pjm.paper_trader import open_position, expire_positions, get_open_positions, get_trade_history
    sig = _make_signal(price=35.0)
    open_position(sig, bankroll=10000.0)
    conn = sqlite3.connect(TEST_DB)
    conn.execute("UPDATE pjm_positions SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    conn.commit()
    conn.close()
    expire_positions(current_price=37.0)
    assert len(get_open_positions()) == 0
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "expired"


def test_paper_summary():
    from pjm.paper_trader import open_position, close_position, get_paper_summary
    sig = _make_signal(price=35.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=25.0, exit_signal="LONG", reason="test")
    summary = get_paper_summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
    assert summary["open_count"] == 0


def test_scan_cache_write_and_read():
    from pjm.paper_trader import write_scan_cache, get_cached_signals
    signals = [
        {"hub": "Western", "hub_name": "WESTERN", "signal": "SHORT",
         "edge": 1.5, "expected_solrad_mjm2": 12.0,
         "current_pjm_price": 35.0, "actual_solar_mw": 5000.0,
         "confidence": 70},
    ]
    write_scan_cache(signals)
    cached = get_cached_signals()
    assert len(cached) >= 1
    assert cached[0]["hub"] == "Western"
