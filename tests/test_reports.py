import csv
import os
import pytest
import sqlite3
from backtesting.reports import generate_report


@pytest.fixture
def sample_data(tmp_path):
    """Create sample signals CSV and trades.db for report testing."""
    # Signals CSV (new format)
    csv_path = str(tmp_path / "signals.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob",
                         "edge", "direction", "dutch_book", "paper_trade", "confidence", "ticker"])
        # 5 signals: mix of cities, directions, edges
        rows = [
            ("2026-03-01T10:00:00+00:00", "NYC High 65-66", "nyc (kalshi)", "0.70", "0.55", "0.15", "BUY YES", "False", "False", "85", "TICK1"),
            ("2026-03-01T10:05:00+00:00", "NYC High 67-68", "nyc (kalshi)", "0.30", "0.45", "-0.15", "SELL YES", "False", "False", "90", "TICK2"),
            ("2026-03-02T10:00:00+00:00", "Chicago High 50-52", "chicago (kalshi)", "0.60", "0.45", "0.15", "BUY YES", "False", "False", "75", "TICK3"),
            ("2026-03-02T10:05:00+00:00", "Miami High 84-85", "miami (kalshi)", "0.10", "0.25", "-0.15", "SELL YES", "False", "False", "80", "TICK4"),
            ("2026-03-03T10:00:00+00:00", "NYC High 60-62", "nyc (kalshi)", "0.50", "0.40", "0.10", "BUY YES", "False", "False", "70", "TICK5"),
        ]
        writer.writerows(rows)

    # Trades DB with settlement outcomes
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, ticker TEXT, city TEXT, side TEXT,
        limit_price INTEGER, fill_price INTEGER, fill_qty INTEGER,
        fill_time TEXT, settlement_outcome TEXT, pnl REAL
    )""")
    conn.executemany(
        "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "o1", "TICK1", "nyc", "yes", 55, 55, 1, "2026-03-01T10:00:00Z", "yes", 0.45),
            (2, "o2", "TICK2", "nyc", "no", 55, 55, 1, "2026-03-01T10:05:00Z", "no", 0.45),
            (3, "o3", "TICK3", "chicago", "yes", 45, 45, 1, "2026-03-02T10:00:00Z", "no", -0.45),
            (4, "o4", "TICK4", "miami", "no", 75, 75, 1, "2026-03-02T10:05:00Z", "no", 0.25),
        ],
    )
    conn.commit()
    conn.close()

    return {"csv_path": csv_path, "db_path": db_path}


def test_generate_report_returns_dict(sample_data):
    result = generate_report(
        signals_csv=sample_data["csv_path"],
        trades_db=sample_data["db_path"],
    )
    assert "brier_score" in result
    assert "log_loss" in result
    assert "total_pnl" in result
    assert "trade_count" in result


def test_generate_report_correct_pnl(sample_data):
    result = generate_report(
        signals_csv=sample_data["csv_path"],
        trades_db=sample_data["db_path"],
    )
    # 0.45 + 0.45 - 0.45 + 0.25 = 0.70
    assert result["total_pnl"] == pytest.approx(0.70)
    assert result["trade_count"] == 4
