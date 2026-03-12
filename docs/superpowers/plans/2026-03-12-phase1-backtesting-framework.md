# Phase 1: Backtesting & Calibration Framework — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backtesting framework that measures model calibration (Brier score, log loss, calibration curves), tracks P&L by city/bucket, and logs trade fills — answering "is this system actually making money?"

**Architecture:** Signal-log-based analysis operating on the existing `logs/signals.csv` (1139 rows). New `backtesting/` package for scoring/reporting, new `kalshi/fill_tracker.py` for persisting order fills to SQLite, and modifications to `logging_utils.py` to capture missing fields (confidence, ticker). No replay mode (deferred to Phase 5).

**Tech Stack:** Python 3.13, pandas, numpy, matplotlib, scipy (new — for calibration curve fitting), rich (existing), sqlite3 (existing), pytest (new — no tests exist yet)

**Spec:** `docs/superpowers/specs/2026-03-12-weather-trading-system-design.md` Section 1

---

## Chunk 1: Test Infrastructure + Data Layer

### Task 1: Set up pytest and test infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`
- Modify: `requirements.txt`

- [ ] **Step 1: Add pytest and test deps to requirements.txt**

Add to end of `requirements.txt`:
```
pytest
matplotlib
scipy
numpy
```

- [ ] **Step 2: Install new dependencies**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && pip install pytest matplotlib scipy numpy`
Expected: Successful installation

- [ ] **Step 3: Create pytest.ini**

Create `pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

- [ ] **Step 4: Create tests package**

Create `tests/__init__.py` (empty file).

Create `tests/conftest.py`:
```python
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
```

- [ ] **Step 5: Verify pytest discovers tests directory**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest --collect-only`
Expected: "no tests ran" (but no errors)

- [ ] **Step 6: Commit**

```bash
git add pytest.ini requirements.txt tests/__init__.py tests/conftest.py
git commit -m "chore: add pytest infrastructure and test dependencies"
```

---

### Task 2: Create trades.db schema and fill_tracker module

**Files:**
- Create: `kalshi/fill_tracker.py`
- Create: `tests/test_fill_tracker.py`

- [ ] **Step 1: Write failing test for trades.db initialization**

Create `tests/test_fill_tracker.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_fill_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kalshi.fill_tracker'`

- [ ] **Step 3: Implement fill_tracker.py**

Create `kalshi/fill_tracker.py`:
```python
"""Track Kalshi order fills in SQLite for backtesting and P&L analysis."""

import os
import sqlite3
from typing import Optional


def init_trades_db(db_path: str = "data/trades.db"):
    """Create the trades table if it doesn't exist."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            ticker TEXT NOT NULL,
            city TEXT,
            side TEXT NOT NULL,
            limit_price INTEGER,
            fill_price INTEGER,
            fill_qty INTEGER,
            fill_time TEXT,
            settlement_outcome TEXT,
            pnl REAL
        )
    """)
    conn.commit()
    conn.close()


def record_fill(
    db_path: str,
    order_id: str,
    ticker: str,
    side: str,
    limit_price: int,
    fill_price: int,
    fill_qty: int,
    fill_time: str,
    city: str = "",
    settlement_outcome: Optional[str] = None,
    pnl: Optional[float] = None,
):
    """Record a fill. Deduplicates on order_id (INSERT OR IGNORE)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO trades
           (order_id, ticker, city, side, limit_price, fill_price, fill_qty, fill_time, settlement_outcome, pnl)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (order_id, ticker, city, side, limit_price, fill_price, fill_qty, fill_time, settlement_outcome, pnl),
    )
    conn.commit()
    conn.close()


def get_unresolved_trades(db_path: str = "data/trades.db") -> list[dict]:
    """Return all trades without a settlement outcome."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE settlement_outcome IS NULL ORDER BY fill_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_trade(db_path: str, order_id: str, settlement_outcome: str, pnl: float):
    """Update a trade with its settlement outcome and P&L."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE trades SET settlement_outcome=?, pnl=? WHERE order_id=?",
        (settlement_outcome, pnl, order_id),
    )
    conn.commit()
    conn.close()


def get_all_trades(db_path: str = "data/trades.db") -> list[dict]:
    """Return all trades for reporting."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades ORDER BY fill_time").fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_fill_tracker.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add kalshi/fill_tracker.py tests/test_fill_tracker.py
git commit -m "feat: add fill_tracker module with trades.db schema and dedup"
```

---

### Task 3: Wire fill logging into kalshi/trader.py

**Files:**
- Modify: `kalshi/trader.py:169-178` (the `execute_kalshi_signal` function's try block)

- [ ] **Step 1: Write failing test for order_id persistence**

Create `tests/test_trader_integration.py`:
```python
import pytest
from unittest.mock import patch, MagicMock


def test_execute_kalshi_signal_records_fill(tmp_path):
    """After a successful order, the fill should be recorded in trades.db."""
    db_path = str(tmp_path / "trades.db")

    # Mock the API call and config
    mock_response = {"order": {"order_id": "test_ord_456", "status": "resting"}}

    with patch("kalshi.trader.place_order", return_value=mock_response), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.MAX_ORDER_USD", 2.0), \
         patch("config.MAX_SCAN_BUDGET", 10.0), \
         patch("config.HIGH_CONFIDENCE_MULTIPLIER", 1.5), \
         patch("kalshi.trader.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db, get_all_trades
        init_trades_db(db_path)

        from kalshi.trader import execute_kalshi_signal, reset_scan_budget
        reset_scan_budget()

        market = {"ticker": "KXHIGHNY-26MAR12-B65", "title": "NYC High Temp"}
        execute_kalshi_signal(market, "nyc", 0.70, 0.55, 0.15, "BUY YES", confidence=80)

        trades = get_all_trades(db_path)
        assert len(trades) == 1
        assert trades[0]["order_id"] == "test_ord_456"
        assert trades[0]["ticker"] == "KXHIGHNY-26MAR12-B65"
        assert trades[0]["side"] == "yes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_trader_integration.py -v`
Expected: FAIL — `TRADES_DB_PATH` not found or trades not recorded

- [ ] **Step 3: Modify kalshi/trader.py to record fills**

In `kalshi/trader.py`, add at the top (after existing imports):
```python
from kalshi.fill_tracker import init_trades_db, record_fill

TRADES_DB_PATH = "data/trades.db"
```

Then modify the try block in `execute_kalshi_signal` (lines 169-178) to record the fill after a successful order:

Replace lines 169-180:
```python
    try:
        resp = place_order(ticker, side, price_cents, count)
        order_id = resp.get("order", {}).get("order_id", "unknown")
        status = resp.get("order", {}).get("status", "unknown")
        _scan_spent += order_cost
        print(f"  Order posted! ID: {order_id} Status: {status}")
        send_signal_alert(
            market.get("title", ticker), city + " (Kalshi)", model_prob, market_prob, edge,
            f"{direction} (LIVE order {order_id})"
        )
    except Exception as e:
        print(f"  Order failed: {e}")
```

With:
```python
    try:
        resp = place_order(ticker, side, price_cents, count)
        order_id = resp.get("order", {}).get("order_id", "unknown")
        status = resp.get("order", {}).get("status", "unknown")
        _scan_spent += order_cost
        print(f"  Order posted! ID: {order_id} Status: {status}")

        # Persist fill for backtesting
        from datetime import datetime, timezone
        init_trades_db(TRADES_DB_PATH)
        record_fill(
            db_path=TRADES_DB_PATH,
            order_id=order_id,
            ticker=ticker,
            side=side,
            limit_price=price_cents,
            fill_price=price_cents,  # Limit price as initial estimate; updated by fill_tracker poll
            fill_qty=count,
            fill_time=datetime.now(timezone.utc).isoformat(),
            city=city,
        )

        send_signal_alert(
            market.get("title", ticker), city + " (Kalshi)", model_prob, market_prob, edge,
            f"{direction} (LIVE order {order_id})"
        )
    except Exception as e:
        print(f"  Order failed: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_trader_integration.py tests/test_fill_tracker.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add kalshi/trader.py tests/test_trader_integration.py
git commit -m "feat: wire fill logging into kalshi trader for trade persistence"
```

---

### Task 4: Extend logging_utils.py with confidence and ticker columns

**Files:**
- Modify: `logging_utils.py`
- Create: `tests/test_logging_utils.py`

- [ ] **Step 1: Write failing test for new CSV columns**

Create `tests/test_logging_utils.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_logging_utils.py -v`
Expected: FAIL — `log_signal() got unexpected keyword argument 'confidence'`

- [ ] **Step 3: Update logging_utils.py**

Replace the entire `log_signal` function in `logging_utils.py`:

```python
def log_signal(market_question: str, city: str, model_prob: float, market_prob: float, edge: float, direction: str, dutch_book: bool, paper_trade: bool, confidence: float = None, ticker: str = None):
    """Append a signal to the CSV log."""
    file_exists = os.path.exists(SIGNALS_CSV)
    os.makedirs(os.path.dirname(SIGNALS_CSV), exist_ok=True)

    with open(SIGNALS_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob", "edge", "direction", "dutch_book", "paper_trade", "confidence", "ticker"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            market_question,
            city,
            f"{model_prob:.4f}",
            f"{market_prob:.4f}",
            f"{edge:.4f}",
            direction,
            dutch_book,
            paper_trade,
            f"{confidence}" if confidence is not None else "",
            ticker or "",
        ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_logging_utils.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Update scanner.py call sites to pass new params**

In `scanner.py`, two call sites for `log_signal` need updating:

Line 129 (Polymarket signals) — add `confidence=0` and `ticker=""`:
```python
log_signal(q, city_key, model_prob, yes_price, edge, direction, dutch, PAPER_MODE, confidence=0, ticker="")
```

Line 226 (Kalshi signals) — add `confidence=confidence` and `ticker=ticker`:
```python
log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE, confidence=confidence, ticker=ticker)
```

- [ ] **Step 6: Verify existing scanner still works (import check)**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -c "from logging_utils import log_signal; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add logging_utils.py scanner.py tests/test_logging_utils.py
git commit -m "feat: add confidence and ticker columns to signal CSV logging"
```

---

## Chunk 2: Backtesting Data Loader

### Task 5: Create backtesting package and data_loader

**Files:**
- Create: `backtesting/__init__.py`
- Create: `backtesting/data_loader.py`
- Create: `tests/test_data_loader.py`

- [ ] **Step 1: Write failing test for CSV loading with old and new formats**

Create `tests/test_data_loader.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_data_loader.py -v`
Expected: FAIL — `No module named 'backtesting'`

- [ ] **Step 3: Create backtesting package and data_loader**

Create `backtesting/__init__.py` (empty file).

Create `backtesting/data_loader.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_data_loader.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/__init__.py backtesting/data_loader.py tests/test_data_loader.py
git commit -m "feat: add backtesting data_loader with old/new CSV format support"
```

---

## Chunk 3: Scoring Engine

### Task 6: Implement Brier score and log loss calculator

**Files:**
- Create: `backtesting/scorer.py`
- Create: `tests/test_scorer.py`

- [ ] **Step 1: Write failing test for Brier score**

Create `tests/test_scorer.py`:
```python
import pytest
import pandas as pd
import numpy as np
from backtesting.scorer import brier_score, log_loss_score, hit_rate_by_confidence, pnl_by_city


def test_brier_score_perfect():
    """Perfect predictions should give Brier score of 0."""
    probs = [1.0, 0.0, 1.0]
    outcomes = [1, 0, 1]
    assert brier_score(probs, outcomes) == pytest.approx(0.0)


def test_brier_score_worst():
    """Completely wrong predictions should give Brier score of 1."""
    probs = [1.0, 0.0]
    outcomes = [0, 1]
    assert brier_score(probs, outcomes) == pytest.approx(1.0)


def test_brier_score_coin_flip():
    """50/50 predictions should give Brier score of 0.25."""
    probs = [0.5, 0.5, 0.5, 0.5]
    outcomes = [1, 0, 1, 0]
    assert brier_score(probs, outcomes) == pytest.approx(0.25)


def test_log_loss_perfect():
    """Near-perfect predictions should give very low log loss."""
    probs = [0.99, 0.01]
    outcomes = [1, 0]
    result = log_loss_score(probs, outcomes)
    assert result < 0.05


def test_log_loss_bad():
    """Confident wrong predictions should give high log loss."""
    probs = [0.99, 0.99]
    outcomes = [0, 0]
    result = log_loss_score(probs, outcomes)
    assert result > 2.0


def test_hit_rate_by_confidence():
    df = pd.DataFrame({
        "confidence": [90, 90, 90, 50, 50],
        "direction": ["BUY YES", "BUY YES", "BUY YES", "SELL YES", "SELL YES"],
        "edge": [0.15, 0.12, 0.10, -0.08, -0.09],
        "settlement_outcome": ["yes", "yes", "no", "no", "yes"],
    })
    result = hit_rate_by_confidence(df, bins=[0, 70, 100])
    assert len(result) == 2  # two bins
    assert "hit_rate" in result.columns


def test_pnl_by_city():
    df = pd.DataFrame({
        "city": ["nyc (kalshi)", "nyc (kalshi)", "chicago (kalshi)"],
        "pnl": [0.50, -0.30, 0.80],
    })
    result = pnl_by_city(df)
    assert result.loc["nyc (kalshi)", "total_pnl"] == pytest.approx(0.20)
    assert result.loc["chicago (kalshi)", "total_pnl"] == pytest.approx(0.80)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_scorer.py -v`
Expected: FAIL — `No module named 'backtesting.scorer'`

- [ ] **Step 3: Implement scorer.py**

Create `backtesting/scorer.py`:
```python
"""Scoring metrics for forecast calibration and P&L analysis."""

import numpy as np
import pandas as pd
from typing import Sequence


def brier_score(predicted_probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Brier score: mean squared error between predicted probability and binary outcome.

    Lower is better. 0 = perfect, 1 = worst possible.
    """
    probs = np.array(predicted_probs, dtype=float)
    actual = np.array(outcomes, dtype=float)
    return float(np.mean((probs - actual) ** 2))


def log_loss_score(predicted_probs: Sequence[float], outcomes: Sequence[int], eps: float = 1e-15) -> float:
    """Log loss (cross-entropy). Heavily penalizes confident wrong predictions.

    Lower is better. Clipped to avoid log(0).
    """
    probs = np.clip(np.array(predicted_probs, dtype=float), eps, 1 - eps)
    actual = np.array(outcomes, dtype=float)
    return float(-np.mean(actual * np.log(probs) + (1 - actual) * np.log(1 - probs)))


def hit_rate_by_confidence(df: pd.DataFrame, bins: list[int] = None) -> pd.DataFrame:
    """Calculate hit rate grouped by confidence bins.

    A 'hit' means the signal direction was correct:
    - BUY YES + settlement=yes → hit
    - SELL YES + settlement=no → hit

    Args:
        df: DataFrame with columns: confidence, direction, settlement_outcome
        bins: Confidence bin edges (default: [0, 50, 70, 85, 100])

    Returns:
        DataFrame with columns: bin_label, count, hits, hit_rate
    """
    if bins is None:
        bins = [0, 50, 70, 85, 100]

    df = df.copy()
    df["hit"] = (
        ((df["direction"] == "BUY YES") & (df["settlement_outcome"] == "yes")) |
        ((df["direction"] == "SELL YES") & (df["settlement_outcome"] == "no"))
    ).astype(int)

    df["conf_bin"] = pd.cut(df["confidence"], bins=bins, include_lowest=True)
    result = df.groupby("conf_bin", observed=False).agg(
        count=("hit", "size"),
        hits=("hit", "sum"),
    ).reset_index()
    result["hit_rate"] = (result["hits"] / result["count"]).fillna(0)
    return result


def pnl_by_city(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate P&L by city.

    Args:
        df: DataFrame with columns: city, pnl

    Returns:
        DataFrame indexed by city with columns: total_pnl, count, avg_pnl
    """
    result = df.groupby("city").agg(
        total_pnl=("pnl", "sum"),
        count=("pnl", "size"),
        avg_pnl=("pnl", "mean"),
    )
    return result


def edge_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze whether signals with larger edges are more profitable.

    Args:
        df: DataFrame with columns: edge, settlement_outcome, direction

    Returns:
        DataFrame grouped by edge magnitude bins with hit rates
    """
    df = df.copy()
    df["abs_edge"] = df["edge"].abs()
    df["hit"] = (
        ((df["direction"] == "BUY YES") & (df["settlement_outcome"] == "yes")) |
        ((df["direction"] == "SELL YES") & (df["settlement_outcome"] == "no"))
    ).astype(int)

    bins = [0, 0.07, 0.105, 0.15, 0.20, 1.0]
    labels = ["7-10.5%", "10.5-15%", "15-20%", "20%+"]
    df["edge_bin"] = pd.cut(df["abs_edge"], bins=bins, labels=labels[:len(bins)-1], include_lowest=True)
    result = df.groupby("edge_bin", observed=False).agg(
        count=("hit", "size"),
        hits=("hit", "sum"),
    ).reset_index()
    result["hit_rate"] = (result["hits"] / result["count"]).fillna(0)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_scorer.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/scorer.py tests/test_scorer.py
git commit -m "feat: add scoring engine — Brier score, log loss, hit rate, P&L by city"
```

---

### Task 7: Implement calibration curve fitting

**Files:**
- Create: `backtesting/calibration.py`
- Create: `tests/test_calibration.py`

- [ ] **Step 1: Write failing test for calibration curve**

Create `tests/test_calibration.py`:
```python
import pytest
import numpy as np
from backtesting.calibration import calibration_curve, platt_scale


def test_calibration_curve_well_calibrated():
    """A well-calibrated model should have bin means ≈ bin observed rates."""
    np.random.seed(42)
    n = 1000
    probs = np.random.uniform(0, 1, n)
    outcomes = (np.random.uniform(0, 1, n) < probs).astype(int)

    bin_means, bin_rates, bin_counts = calibration_curve(probs, outcomes, n_bins=5)

    assert len(bin_means) == 5
    # Each bin should be within ~0.15 of perfect calibration (generous for 1000 samples)
    for mean, rate in zip(bin_means, bin_rates):
        if not np.isnan(rate):
            assert abs(mean - rate) < 0.15, f"Bin mean {mean:.2f} vs rate {rate:.2f}"


def test_calibration_curve_returns_counts():
    probs = [0.1, 0.2, 0.3, 0.8, 0.9]
    outcomes = [0, 0, 1, 1, 1]
    _, _, counts = calibration_curve(probs, outcomes, n_bins=2)
    assert sum(counts) == 5


def test_platt_scale():
    """Platt scaling should recalibrate predictions."""
    probs = np.array([0.1, 0.3, 0.5, 0.7, 0.9] * 20)
    # Overconfident model: outcomes are closer to 50/50 than predictions suggest
    outcomes = np.array([0, 0, 1, 1, 1] * 20)

    scaled = platt_scale(probs, outcomes, probs)
    # Scaled predictions should be less extreme (closer to 0.5)
    assert scaled.min() > probs.min()  # low probs pushed up
    assert scaled.max() < probs.max()  # high probs pushed down
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_calibration.py -v`
Expected: FAIL — `No module named 'backtesting.calibration'`

- [ ] **Step 3: Implement calibration.py**

Create `backtesting/calibration.py`:
```python
"""Calibration analysis: calibration curves and Platt scaling."""

import numpy as np
from scipy.optimize import minimize


def calibration_curve(
    predicted_probs, outcomes, n_bins: int = 10
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute calibration curve data: bin means vs observed frequency.

    Returns:
        bin_means: Mean predicted probability in each bin
        bin_rates: Observed rate (fraction of positive outcomes) in each bin
        bin_counts: Number of samples in each bin
    """
    probs = np.array(predicted_probs, dtype=float)
    actual = np.array(outcomes, dtype=float)

    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_means = np.zeros(n_bins)
    bin_rates = np.zeros(n_bins)
    bin_counts = np.zeros(n_bins, dtype=int)

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == n_bins - 1:
            mask = (probs >= lo) & (probs <= hi)
        else:
            mask = (probs >= lo) & (probs < hi)

        count = mask.sum()
        bin_counts[i] = count
        if count > 0:
            bin_means[i] = probs[mask].mean()
            bin_rates[i] = actual[mask].mean()
        else:
            bin_means[i] = (lo + hi) / 2
            bin_rates[i] = np.nan

    return bin_means, bin_rates, bin_counts


def platt_scale(
    train_probs, train_outcomes, target_probs
) -> np.ndarray:
    """Apply Platt scaling to recalibrate probabilities.

    Fits a logistic regression on log-odds of train_probs vs train_outcomes,
    then applies the transformation to target_probs.

    Args:
        train_probs: Predicted probabilities used for fitting (training set)
        train_outcomes: Binary outcomes for training set
        target_probs: Probabilities to recalibrate

    Returns:
        Recalibrated probabilities for target_probs
    """
    train_p = np.clip(np.array(train_probs, dtype=float), 1e-6, 1 - 1e-6)
    train_y = np.array(train_outcomes, dtype=float)
    target_p = np.clip(np.array(target_probs, dtype=float), 1e-6, 1 - 1e-6)

    # Convert to log-odds
    train_logits = np.log(train_p / (1 - train_p))

    # Fit logistic regression: y = sigmoid(a * logit + b)
    def neg_log_likelihood(params):
        a, b = params
        z = a * train_logits + b
        p = 1 / (1 + np.exp(-z))
        p = np.clip(p, 1e-10, 1 - 1e-10)
        return -np.mean(train_y * np.log(p) + (1 - train_y) * np.log(1 - p))

    result = minimize(neg_log_likelihood, [1.0, 0.0], method="Nelder-Mead")
    a, b = result.x

    target_logits = np.log(target_p / (1 - target_p))
    z = a * target_logits + b
    return 1 / (1 + np.exp(-z))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_calibration.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/calibration.py tests/test_calibration.py
git commit -m "feat: add calibration curve and Platt scaling for probability recalibration"
```

---

## Chunk 4: Reports and Walk-Forward

### Task 8: Implement CLI reports with Rich tables and matplotlib plots

**Files:**
- Create: `backtesting/reports.py`
- Create: `tests/test_reports.py`

- [ ] **Step 1: Write failing test for report generation**

Create `tests/test_reports.py`:
```python
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
        # 10 signals: mix of cities, directions, edges
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_reports.py -v`
Expected: FAIL — `No module named 'backtesting.reports'`

- [ ] **Step 3: Implement reports.py**

Create `backtesting/reports.py`:
```python
"""Generate backtesting reports: CLI tables and calibration plots."""

import os
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from backtesting.data_loader import load_signals, load_trades
from backtesting.scorer import brier_score, log_loss_score, hit_rate_by_confidence, pnl_by_city
from backtesting.calibration import calibration_curve

console = Console()


def generate_report(
    signals_csv: str = "logs/signals.csv",
    trades_db: str = "data/trades.db",
    plot: bool = False,
) -> dict:
    """Generate a comprehensive backtesting report.

    Returns a dict of key metrics for programmatic use.
    Prints Rich tables to console for human consumption.
    Optionally generates calibration plot.
    """
    signals = load_signals(signals_csv)
    trades = load_trades(trades_db) if os.path.exists(trades_db) else pd.DataFrame()

    result = {}

    # --- P&L Summary ---
    if not trades.empty and "pnl" in trades.columns:
        resolved = trades.dropna(subset=["settlement_outcome"])
        result["total_pnl"] = float(resolved["pnl"].sum()) if not resolved.empty else 0.0
        result["trade_count"] = len(resolved)
        result["win_count"] = int((resolved["pnl"] > 0).sum())
        result["loss_count"] = int((resolved["pnl"] < 0).sum())
        result["win_rate"] = result["win_count"] / max(result["trade_count"], 1)
        result["avg_win"] = float(resolved.loc[resolved["pnl"] > 0, "pnl"].mean()) if result["win_count"] > 0 else 0.0
        result["avg_loss"] = float(resolved.loc[resolved["pnl"] < 0, "pnl"].mean()) if result["loss_count"] > 0 else 0.0
    else:
        result["total_pnl"] = 0.0
        result["trade_count"] = 0
        result["win_count"] = 0
        result["loss_count"] = 0
        result["win_rate"] = 0.0
        result["avg_win"] = 0.0
        result["avg_loss"] = 0.0

    # --- Calibration (Brier/Log Loss) ---
    # Match signals to trade outcomes via ticker
    if not trades.empty and not signals.empty:
        merged = signals.merge(
            trades[["ticker", "settlement_outcome"]].drop_duplicates(subset=["ticker"]),
            on="ticker",
            how="inner",
        )
        if not merged.empty:
            # Convert settlement to binary: model predicted YES probability,
            # outcome is 1 if settled YES, 0 if settled NO
            merged["outcome_binary"] = (merged["settlement_outcome"] == "yes").astype(int)
            probs = merged["model_prob"].values
            outcomes = merged["outcome_binary"].values

            result["brier_score"] = brier_score(probs, outcomes)
            result["log_loss"] = log_loss_score(probs, outcomes)
            result["calibration_n"] = len(merged)
        else:
            result["brier_score"] = None
            result["log_loss"] = None
            result["calibration_n"] = 0
    else:
        result["brier_score"] = None
        result["log_loss"] = None
        result["calibration_n"] = 0

    # --- Print Report ---
    _print_pnl_table(result)

    if not trades.empty:
        _print_pnl_by_city(trades)

    if result["brier_score"] is not None:
        _print_calibration_table(result)

    # --- Calibration Plot ---
    if plot and result["brier_score"] is not None:
        _plot_calibration(merged["model_prob"].values, merged["outcome_binary"].values)

    # --- Signal Summary ---
    result["total_signals"] = len(signals)
    result["kalshi_signals"] = len(signals[signals["city"].str.contains("kalshi", case=False, na=False)])

    return result


def _print_pnl_table(result: dict):
    table = Table(title="P&L Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total P&L", f"${result['total_pnl']:.2f}")
    table.add_row("Trades", str(result["trade_count"]))
    table.add_row("Wins / Losses", f"{result['win_count']} / {result['loss_count']}")
    table.add_row("Win Rate", f"{result['win_rate']:.1%}")
    table.add_row("Avg Win", f"${result['avg_win']:.2f}")
    table.add_row("Avg Loss", f"${result['avg_loss']:.2f}")
    console.print(table)


def _print_pnl_by_city(trades: pd.DataFrame):
    resolved = trades.dropna(subset=["settlement_outcome"])
    if resolved.empty:
        return

    # Extract city from ticker (e.g., KXHIGHNY → nyc)
    if "city" not in resolved.columns:
        return

    city_pnl = pnl_by_city(resolved)
    table = Table(title="P&L by City")
    table.add_column("City", style="cyan")
    table.add_column("Total P&L", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Avg P&L", justify="right")

    for city, row in city_pnl.iterrows():
        color = "green" if row["total_pnl"] > 0 else "red"
        table.add_row(
            str(city),
            f"[{color}]${row['total_pnl']:.2f}[/{color}]",
            str(int(row["count"])),
            f"${row['avg_pnl']:.2f}",
        )
    console.print(table)


def _print_calibration_table(result: dict):
    table = Table(title="Calibration Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    brier = result["brier_score"]
    table.add_row("Brier Score", f"{brier:.4f}" if brier is not None else "N/A")
    table.add_row("Log Loss", f"{result['log_loss']:.4f}" if result["log_loss"] is not None else "N/A")
    table.add_row("Calibration Samples", str(result["calibration_n"]))

    # Brier score interpretation
    if brier is not None:
        if brier < 0.1:
            table.add_row("Rating", "[green]Excellent[/green]")
        elif brier < 0.2:
            table.add_row("Rating", "[yellow]Good[/yellow]")
        elif brier < 0.25:
            table.add_row("Rating", "[yellow]Fair (coin-flip territory)[/yellow]")
        else:
            table.add_row("Rating", "[red]Poor[/red]")

    console.print(table)


def _plot_calibration(probs, outcomes, save_path: str = "logs/calibration.png"):
    """Generate and save calibration curve plot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bin_means, bin_rates, bin_counts = calibration_curve(probs, outcomes, n_bins=10)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Calibration curve
    ax1.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
    valid = ~np.isnan(bin_rates)
    ax1.plot(bin_means[valid], bin_rates[valid], "bo-", label="Model")
    ax1.set_xlabel("Predicted probability")
    ax1.set_ylabel("Observed frequency")
    ax1.set_title("Calibration Curve")
    ax1.legend()
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)

    # Histogram of predictions
    ax2.bar(bin_means, bin_counts, width=0.08, alpha=0.7)
    ax2.set_xlabel("Predicted probability")
    ax2.set_ylabel("Count")
    ax2.set_title("Prediction Distribution")

    plt.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, dpi=100)
    plt.close()
    console.print(f"[dim]Calibration plot saved to {save_path}[/dim]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_reports.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/reports.py tests/test_reports.py
git commit -m "feat: add backtesting reports — Rich tables, calibration plots, P&L summary"
```

---

### Task 9: Implement walk-forward backtester

**Files:**
- Create: `backtesting/walk_forward.py`
- Create: `tests/test_walk_forward.py`

- [ ] **Step 1: Write failing test for walk-forward simulation**

Create `tests/test_walk_forward.py`:
```python
import csv
import pytest
from backtesting.walk_forward import walk_forward_simulate


@pytest.fixture
def signals_csv(tmp_path):
    """Create a signal log spanning 5 days for walk-forward testing."""
    path = str(tmp_path / "signals.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob",
                         "edge", "direction", "dutch_book", "paper_trade", "confidence", "ticker"])
        # Day 1-5: some BUY YES, some SELL YES with varying edges
        for day in range(1, 6):
            writer.writerow([
                f"2026-03-{day:02d}T10:00:00+00:00", f"NYC High {60+day}-{61+day}",
                "nyc (kalshi)", f"{0.5 + day*0.05:.2f}", "0.45",
                f"{0.05 + day*0.05:.2f}", "BUY YES", "False", "False", "80", f"TICK{day}",
            ])
    return path


def test_walk_forward_returns_results(signals_csv):
    results = walk_forward_simulate(
        signals_csv=signals_csv,
        edge_threshold=0.07,
        initial_bankroll=500.0,
        bet_fraction=0.02,
    )
    assert "daily_pnl" in results
    assert "total_return" in results
    assert "signals_traded" in results
    assert results["signals_traded"] > 0


def test_walk_forward_respects_edge_threshold(signals_csv):
    results_low = walk_forward_simulate(signals_csv=signals_csv, edge_threshold=0.05)
    results_high = walk_forward_simulate(signals_csv=signals_csv, edge_threshold=0.20)
    assert results_low["signals_traded"] >= results_high["signals_traded"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_walk_forward.py -v`
Expected: FAIL — `No module named 'backtesting.walk_forward'`

- [ ] **Step 3: Implement walk_forward.py**

Create `backtesting/walk_forward.py`:
```python
"""Monte Carlo walk-forward simulator: simulates trading on signal log.

IMPORTANT: This is a Monte Carlo simulation, NOT a historical backtest.
Outcomes are simulated using model_prob as the true probability (self-reinforcing).
For real historical performance, use generate_report() with actual settlement
data from trades.db. This simulator is useful for:
- Stress-testing position sizing parameters
- Estimating drawdown characteristics
- Comparing different edge thresholds

When trades.db has resolved trades, prefer reports.py for actual performance.
"""

import pandas as pd
import numpy as np
from backtesting.data_loader import load_signals


def walk_forward_simulate(
    signals_csv: str = "logs/signals.csv",
    edge_threshold: float = 0.105,
    confidence_threshold: float = 70.0,
    initial_bankroll: float = 1000.0,
    bet_fraction: float = 0.02,
    max_bet_pct: float = 0.03,
) -> dict:
    """Simulate historical trading using signal log.

    Walks forward day by day, "betting" on signals that meet thresholds.
    Uses a simple fixed-fraction sizing (placeholder for Kelly in Phase 2).

    Settlement is simulated: if model_prob > 0.5 for BUY YES, we assume
    the signal was correct with probability = model_prob. This is an
    approximation — real settlement data from trades.db is better.

    Args:
        signals_csv: Path to signals CSV
        edge_threshold: Minimum |edge| to trade
        confidence_threshold: Minimum confidence to trade (NaN treated as meeting threshold)
        initial_bankroll: Starting capital
        bet_fraction: Fraction of bankroll per trade
        max_bet_pct: Maximum fraction of bankroll per trade

    Returns:
        Dict with daily_pnl (Series), total_return, signals_traded, etc.
    """
    signals = load_signals(signals_csv)
    if signals.empty:
        return {"daily_pnl": pd.Series(), "total_return": 0.0, "signals_traded": 0}

    # Filter to tradeable signals
    signals = signals[signals["edge"].abs() >= edge_threshold].copy()
    conf_mask = signals["confidence"].isna() | (signals["confidence"] >= confidence_threshold)
    signals = signals[conf_mask]

    if signals.empty:
        return {"daily_pnl": pd.Series(), "total_return": 0.0, "signals_traded": 0}

    signals["date"] = signals["timestamp"].dt.date

    bankroll = initial_bankroll
    daily_pnl = {}
    trades_taken = 0

    for date, day_signals in signals.groupby("date"):
        day_pnl = 0.0

        for _, sig in day_signals.iterrows():
            # Position size
            bet_size = min(bankroll * bet_fraction, bankroll * max_bet_pct)
            if bet_size < 0.50:
                continue

            edge = sig["edge"]
            market_prob = sig["market_prob"]
            model_prob = sig["model_prob"]

            # Simulate outcome using model_prob as true probability
            # In a real backtest with settlement data, use actual outcomes instead
            np.random.seed(hash((str(date), sig.get("ticker", trades_taken))) % (2**31))
            outcome_yes = np.random.random() < model_prob

            if sig["direction"] == "BUY YES":
                # Bought YES at market_prob, settles to $1 if yes, $0 if no
                if outcome_yes:
                    trade_pnl = bet_size * (1.0 - market_prob) / market_prob
                else:
                    trade_pnl = -bet_size
            else:
                # Sold YES (bought NO) at (1 - market_prob)
                if not outcome_yes:
                    trade_pnl = bet_size * market_prob / (1.0 - market_prob)
                else:
                    trade_pnl = -bet_size

            day_pnl += trade_pnl
            trades_taken += 1

        bankroll += day_pnl
        daily_pnl[date] = day_pnl

    daily_series = pd.Series(daily_pnl)
    total_return = (bankroll - initial_bankroll) / initial_bankroll

    return {
        "daily_pnl": daily_series,
        "total_return": total_return,
        "final_bankroll": bankroll,
        "signals_traded": trades_taken,
        "max_drawdown": _max_drawdown(daily_series, initial_bankroll),
        "sharpe_ratio": _sharpe_ratio(daily_series) if len(daily_series) > 1 else 0.0,
    }


def _max_drawdown(daily_pnl: pd.Series, initial_bankroll: float) -> float:
    """Calculate maximum drawdown as a fraction of peak bankroll."""
    cumulative = daily_pnl.cumsum() + initial_bankroll
    peak = cumulative.cummax()
    drawdown = (peak - cumulative) / peak
    return float(drawdown.max()) if not drawdown.empty else 0.0


def _sharpe_ratio(daily_pnl: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualized Sharpe ratio from daily P&L."""
    if daily_pnl.std() == 0:
        return 0.0
    daily_mean = daily_pnl.mean() - risk_free_rate / 252
    return float(daily_mean / daily_pnl.std() * np.sqrt(252))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_walk_forward.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/walk_forward.py tests/test_walk_forward.py
git commit -m "feat: add walk-forward backtester with simulated P&L and drawdown analysis"
```

---

### Task 10: Create CLI entry point for backtesting reports

**Files:**
- Create: `backtesting/__main__.py`

- [ ] **Step 1: Create __main__.py for `python -m backtesting.reports` usage**

Create `backtesting/__main__.py`:
```python
"""CLI entry point: python -m backtesting"""

import argparse
from backtesting.reports import generate_report
from backtesting.walk_forward import walk_forward_simulate
from rich.console import Console

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Weather Bot Backtesting Reports")
    parser.add_argument("--signals", default="logs/signals.csv", help="Path to signals CSV")
    parser.add_argument("--trades", default="data/trades.db", help="Path to trades SQLite DB")
    parser.add_argument("--plot", action="store_true", help="Generate calibration plot")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward simulation")
    parser.add_argument("--edge-threshold", type=float, default=0.105, help="Edge threshold for walk-forward")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Initial bankroll for walk-forward")
    args = parser.parse_args()

    console.print("[bold cyan]Weather Bot — Backtesting Report[/bold cyan]\n")

    result = generate_report(
        signals_csv=args.signals,
        trades_db=args.trades,
        plot=args.plot,
    )

    console.print(f"\n[dim]Total signals logged: {result['total_signals']}[/dim]")
    console.print(f"[dim]Kalshi signals: {result['kalshi_signals']}[/dim]")

    if args.walk_forward:
        console.print("\n[bold cyan]Walk-Forward Monte Carlo Simulation[/bold cyan]")
        console.print("[dim](Outcomes simulated from model probs — use P&L Summary above for actual performance)[/dim]\n")
        wf = walk_forward_simulate(
            signals_csv=args.signals,
            edge_threshold=args.edge_threshold,
            initial_bankroll=args.bankroll,
        )
        console.print(f"Signals traded: {wf['signals_traded']}")
        console.print(f"Final bankroll: ${wf['final_bankroll']:.2f} (started ${args.bankroll:.2f})")
        console.print(f"Total return: {wf['total_return']:.1%}")
        console.print(f"Max drawdown: {wf['max_drawdown']:.1%}")
        console.print(f"Sharpe ratio: {wf['sharpe_ratio']:.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test the CLI on real data**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m backtesting --signals logs/signals.csv`
Expected: Rich table output with signal counts (calibration metrics will be N/A until trades are resolved)

- [ ] **Step 3: Test walk-forward simulation**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m backtesting --signals logs/signals.csv --walk-forward --edge-threshold 0.105`
Expected: Walk-forward results printed — signals traded, P&L, Sharpe ratio

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/__main__.py
git commit -m "feat: add CLI entry point for backtesting reports and walk-forward simulation"
```

---

### Task 11: Final integration test and cleanup

- [ ] **Step 1: Run the full backtesting report on real data**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m backtesting --walk-forward --plot`
Expected: Full report with P&L table, walk-forward simulation, and calibration plot saved to `logs/calibration.png`

- [ ] **Step 2: Verify trades.db initialization works with live system**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -c "from kalshi.fill_tracker import init_trades_db; init_trades_db(); print('trades.db initialized')"`
Expected: `trades.db initialized` and `data/trades.db` file created

- [ ] **Step 3: Run full test suite one final time**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (14+ tests)

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: Phase 1 complete — backtesting framework with scoring, calibration, walk-forward"
```

---

## Spec Deviations & Notes

**settlement_outcome in signals CSV:** The spec says to add `settlement_outcome` to the CSV. This plan instead joins signals to trades via the `ticker` column to get outcomes. Architecturally equivalent, avoids double-storage. The 1139 existing signals have no `ticker` — they cannot be calibration-scored retroactively. Calibration metrics start accumulating from now.

**Edge decay metric:** The spec lists "Edge decay — how does edge change from signal detection to settlement?" as a core metric. This is deferred — it requires time-series market price snapshots between signal and settlement, which aren't currently logged. Can be added once market price logging is in place.

**Walk-forward vs historical backtest:** The walk-forward simulator uses Monte Carlo (model_prob as ground truth), not actual settlement outcomes. This is clearly labeled in the CLI output. Real performance analysis uses `generate_report()` with trades.db. A true historical backtest (replay mode) is deferred to Phase 5.

**Backfilling Kalshi fills:** The spec mentions "Backfilling recent fills from Kalshi order history API on first run." This is not in the plan — it requires paginating the Kalshi orders API with status=executed, which is straightforward but adds scope. Can be done as a follow-up task if historical fill data is valuable.
