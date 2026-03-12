import csv
import os
import pytest
from backtesting.data_loader import load_signals


@pytest.fixture
def old_format_csv(tmp_path):
    """CSV with old 9-column format (no confidence/ticker)."""
    path = str(tmp_path / "signals.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob", "edge", "direction", "dutch_book", "paper_trade"])
        writer.writerow(["2026-03-11T23:38:38+00:00", "High temp NYC 65-66?", "nyc (kalshi)", "0.7000", "0.5500", "0.1500", "BUY YES", "False", "True"])
        writer.writerow(["2026-03-11T23:40:00+00:00", "High temp Miami 84-85?", "miami", "0.0000", "0.1250", "-0.1250", "SELL YES", "False", "True"])
    return path


@pytest.fixture
def new_format_csv(tmp_path):
    """CSV with new 11-column format (has confidence/ticker)."""
    path = str(tmp_path / "signals.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob", "edge", "direction", "dutch_book", "paper_trade", "confidence", "ticker"])
        writer.writerow(["2026-03-12T10:00:00+00:00", "High temp NYC 65-66?", "nyc (kalshi)", "0.7000", "0.5500", "0.1500", "BUY YES", "False", "True", "85.0", "KXHIGHNY-26MAR12-B65"])
    return path


def test_load_old_format_signals(old_format_csv):
    df = load_signals(old_format_csv)
    assert len(df) == 2
    assert "confidence" in df.columns
    assert "ticker" in df.columns
    assert df["confidence"].isna().all()  # old rows have no confidence


def test_load_new_format_signals(new_format_csv):
    df = load_signals(new_format_csv)
    assert len(df) == 1
    assert df.iloc[0]["confidence"] == 85.0
    assert df.iloc[0]["ticker"] == "KXHIGHNY-26MAR12-B65"


def test_load_signals_numeric_types(new_format_csv):
    df = load_signals(new_format_csv)
    assert df["model_prob"].dtype == float
    assert df["market_prob"].dtype == float
    assert df["edge"].dtype == float


def test_load_signals_parses_timestamps(new_format_csv):
    df = load_signals(new_format_csv)
    assert hasattr(df["timestamp"].iloc[0], "hour")  # is datetime-like
