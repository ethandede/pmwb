# tests/test_scan_cache.py
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.scan_cache import (
    init_scan_cache_db,
    write_scan_results,
    get_latest_scan,
    write_model_outcome,
    get_model_outcomes,
    cleanup_old_scans,
)


def test_init_creates_tables(tmp_path):
    db = str(tmp_path / "cache.db")
    init_scan_cache_db(db)
    import sqlite3
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "scan_results" in tables
    assert "model_outcomes" in tables


def test_write_and_read_scan_results(tmp_path):
    db = str(tmp_path / "cache.db")
    init_scan_cache_db(db)
    rows = [
        {"market_type": "precip", "ticker": "KXRAINNY-26MAR", "city": "NYC",
         "model_prob": 0.72, "market_price": 0.61, "edge": 0.11,
         "direction": "BUY YES", "confidence": 68, "method": "CDF",
         "threshold": ">2.5 in", "days_left": 18},
    ]
    write_scan_results(rows, scan_time="2026-03-13T14:15:02Z", db_path=db)
    result = get_latest_scan("precip", db_path=db)
    assert result["scan_time"] == "2026-03-13T14:15:02Z"
    assert len(result["markets"]) == 1
    assert result["markets"][0]["ticker"] == "KXRAINNY-26MAR"


def test_get_latest_scan_empty(tmp_path):
    db = str(tmp_path / "cache.db")
    init_scan_cache_db(db)
    result = get_latest_scan("temp", db_path=db)
    assert result["markets"] == []
    assert result["scan_time"] is None


def test_write_model_outcome(tmp_path):
    db = str(tmp_path / "cache.db")
    init_scan_cache_db(db)
    write_model_outcome(
        ticker="KXRAINNY-26MAR", city="NYC", market_type="precip",
        predicted_prob=0.72, market_price=0.61, actual_outcome=1,
        db_path=db,
    )
    outcomes = get_model_outcomes(db_path=db)
    assert len(outcomes) == 1
    assert outcomes[0]["actual"] == 1


def test_cleanup_old_scans(tmp_path):
    db = str(tmp_path / "cache.db")
    init_scan_cache_db(db)
    rows = [{"market_type": "temp", "ticker": "T1", "city": "NYC",
             "model_prob": 0.5, "market_price": 0.5, "edge": 0.0,
             "direction": "BUY YES", "confidence": 50, "method": "ENS",
             "threshold": "50-55", "days_left": 1}]
    write_scan_results(rows, scan_time="2025-01-01T00:00:00Z", db_path=db)
    write_scan_results(rows, scan_time="2026-03-13T00:00:00Z", db_path=db)
    cleanup_old_scans(days=30, db_path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
    conn.close()
    assert count == 1  # Only the recent one survives
