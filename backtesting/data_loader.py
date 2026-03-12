"""Load signals CSV and trades DB for backtesting analysis."""

import pandas as pd
import sqlite3
from typing import Optional

# Columns that must exist in the output DataFrame (old CSVs may lack some)
EXPECTED_COLUMNS = [
    "timestamp", "market_question", "city", "model_prob", "market_prob",
    "edge", "direction", "dutch_book", "paper_trade", "confidence", "ticker",
]


def load_signals(csv_path: str = "logs/signals.csv") -> pd.DataFrame:
    """Load signals CSV, handling both old (9-col) and new (11-col) formats.

    Missing columns (confidence, ticker) default to NaN/empty.
    Numeric columns are cast to float. Timestamps are parsed.
    """
    df = pd.read_csv(csv_path)

    # Add missing columns with NaN defaults
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    # Cast numeric columns
    for col in ["model_prob", "market_prob", "edge"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Confidence: empty strings → NaN, then to float
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")

    # Parse timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

    # Ticker: fill NaN with empty string
    df["ticker"] = df["ticker"].fillna("")

    return df


def load_trades(db_path: str = "data/trades.db") -> pd.DataFrame:
    """Load all trades from SQLite trades.db."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY fill_time", conn)
    conn.close()
    if not df.empty:
        df["fill_time"] = pd.to_datetime(df["fill_time"], utc=True, errors="coerce")
    return df


def load_bias_history(db_path: str = "data/bias.db") -> pd.DataFrame:
    """Load bias correction history from bias.db."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM bias ORDER BY city, month, model", conn)
    conn.close()
    return df
