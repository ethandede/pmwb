import csv
import os
import pytest


def test_log_signal_includes_confidence_and_ticker(tmp_path):
    csv_path = str(tmp_path / "signals.csv")

    # Patch the CSV path
    import logging_utils
    original_path = logging_utils.SIGNALS_CSV
    logging_utils.SIGNALS_CSV = csv_path

    try:
        logging_utils.log_signal(
            market_question="Will high temp in NYC be 65-66?",
            city="nyc",
            model_prob=0.70,
            market_prob=0.55,
            edge=0.15,
            direction="BUY YES",
            dutch_book=False,
            paper_trade=True,
            confidence=85.0,
            ticker="KXHIGHNY-26MAR12-B65",
        )

        with open(csv_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["confidence"] == "85.0"
        assert rows[0]["ticker"] == "KXHIGHNY-26MAR12-B65"
    finally:
        logging_utils.SIGNALS_CSV = original_path


def test_log_signal_backwards_compatible(tmp_path):
    """Calling without new params should still work (defaults to None)."""
    csv_path = str(tmp_path / "signals.csv")

    import logging_utils
    original_path = logging_utils.SIGNALS_CSV
    logging_utils.SIGNALS_CSV = csv_path

    try:
        logging_utils.log_signal(
            market_question="Test",
            city="nyc",
            model_prob=0.5,
            market_prob=0.5,
            edge=0.0,
            direction="NONE",
            dutch_book=False,
            paper_trade=True,
        )

        with open(csv_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["confidence"] == ""
        assert rows[0]["ticker"] == ""
    finally:
        logging_utils.SIGNALS_CSV = original_path
