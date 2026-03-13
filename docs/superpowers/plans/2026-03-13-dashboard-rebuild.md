# Dashboard Rebuild Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Streamlit dashboard with FastAPI + vanilla HTML/SCSS/JS frontend, adding equity curve and model accuracy views.

**Architecture:** FastAPI serves JSON endpoints and static files. Scanner daemon caches scan results to SQLite. Frontend is a single HTML page with ES module JS and Plotly.js charts.

**Tech Stack:** Python (FastAPI, Uvicorn, SQLite), HTML/SCSS/JS (vanilla, ES modules), Plotly.js (CDN), dart-sass

**Spec:** `docs/superpowers/specs/2026-03-13-dashboard-rebuild-design.md`

---

## File Map

### New files
- `dashboard/__init__.py` — Empty, makes dashboard a Python package
- `dashboard/api.py` — FastAPI app with all API endpoints
- `dashboard/scan_cache.py` — SQLite read/write for scan_cache.db (scan_results + model_outcomes tables)
- `dashboard/equity_db.py` — SQLite read/write for equity_history.db
- `dashboard/ticker_map.py` — `ticker_to_city()` utility
- `dashboard/static/index.html` — Single-page dashboard
- `dashboard/static/scss/_variables.scss` — Brand colors, fonts, spacing
- `dashboard/static/scss/_base.scss` — Reset, typography, global styles
- `dashboard/static/scss/_header.scss` — Header bar
- `dashboard/static/scss/_metrics.scss` — Metric cards
- `dashboard/static/scss/_charts.scss` — Chart containers
- `dashboard/static/scss/_tables.scss` — Data tables
- `dashboard/static/scss/_footer.scss` — Risk controls, footer
- `dashboard/static/scss/main.scss` — Import hub
- `dashboard/static/css/main.css` — Compiled output (committed)
- `dashboard/static/js/app.js` — Init, fetch orchestration, refresh
- `dashboard/static/js/portfolio.js` — Portfolio section rendering
- `dashboard/static/js/markets.js` — Market tables + charts (precip + temp)
- `dashboard/static/js/performance.js` — Equity curve + model accuracy charts
- `dashboard/static/assets/logo.svg` — EE logo
- `tests/test_ticker_map.py` — Tests for ticker_to_city
- `tests/test_scan_cache.py` — Tests for scan cache read/write
- `tests/test_equity_db.py` — Tests for equity DB read/write
- `tests/test_api.py` — Tests for API endpoints

### Modified files
- `scanner.py` — Add scan cache write after each cycle
- `weather/resolver.py` — Add model_outcomes write on settlement
- `scripts/daily_pnl_summary.py` — Add equity snapshot write
- `requirements.txt` — Add fastapi, uvicorn
- `run_dashboard.sh` — Create/overwrite: switch from streamlit to uvicorn

### Removed files
- `dashboard/app.py` — Old Streamlit dashboard
- `.streamlit/config.toml` — Streamlit theme config

---

## Chunk 1: Data Layer

### Task 1: Ticker-to-City Mapping Utility

**Files:**
- Create: `dashboard/ticker_map.py`
- Create: `tests/test_ticker_map.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ticker_map.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.ticker_map import ticker_to_city


def test_high_temp_ticker():
    assert ticker_to_city("KXHIGHNY-26MAR13-B55") == "NYC"


def test_high_temp_no_date():
    assert ticker_to_city("KXHIGHCHI") == "Chicago"


def test_precip_monthly_ticker():
    assert ticker_to_city("KXRAINNYCM-26MAR") == "NYC"


def test_precip_daily_ticker():
    """Daily rain tickers strip CM suffix — KXRAINNY should still resolve."""
    assert ticker_to_city("KXRAINNY-26MAR") == "NYC"


def test_low_temp_ticker():
    """KXLOWT prefix — not in WEATHER_SERIES directly but derivable."""
    result = ticker_to_city("KXLOWTNY-26MAR13")
    # Should resolve to NYC or fall back to KXLOWTNY
    assert result in ("NYC", "KXLOWTNY")


def test_unknown_ticker_falls_back():
    assert ticker_to_city("KXFOOBAR-26MAR") == "KXFOOBAR"


def test_city_name_formatting():
    """Cities should be title-cased display names, not slugs."""
    assert ticker_to_city("KXHIGHLAX-26MAR13") == "Los Angeles"
    assert ticker_to_city("KXHIGHTDC-26MAR13") == "Washington DC"
    assert ticker_to_city("KXHIGHTSEA-26MAR13") == "Seattle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_ticker_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.ticker_map'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/ticker_map.py
"""Ticker-to-city display name mapping.

Builds a reverse-lookup dict from WEATHER_SERIES and PRECIP_SERIES
at import time. Handles KXHIGH*, KXLOWT*, KXRAIN*, KXRAIN*CM prefixes.
"""
from kalshi.scanner import WEATHER_SERIES, PRECIP_SERIES

# Display name overrides for slug → human-readable
_CITY_DISPLAY = {
    "nyc": "NYC",
    "chicago": "Chicago",
    "miami": "Miami",
    "austin": "Austin",
    "los_angeles": "Los Angeles",
    "seattle": "Seattle",
    "houston": "Houston",
    "san_francisco": "San Francisco",
    "atlanta": "Atlanta",
    "washington_dc": "Washington DC",
    "boston": "Boston",
    "phoenix": "Phoenix",
    "san_antonio": "San Antonio",
    "las_vegas": "Las Vegas",
}

def _build_prefix_map() -> dict[str, str]:
    """Build ticker-prefix → display-city-name mapping."""
    m = {}
    for prefix, info in WEATHER_SERIES.items():
        city_slug = info["city"]
        display = _CITY_DISPLAY.get(city_slug, city_slug.replace("_", " ").title())
        m[prefix] = display
        # Also register KXLOWT variant: KXHIGHNY → KXLOWTNY
        if prefix.startswith("KXHIGH"):
            low_prefix = prefix.replace("KXHIGH", "KXLOWT", 1)
            m[low_prefix] = display
    for prefix, info in PRECIP_SERIES.items():
        city_slug = info["city"]
        display = _CITY_DISPLAY.get(city_slug, city_slug.replace("_", " ").title())
        m[prefix] = display
        # Also register without trailing CM (monthly → daily): KXRAINNYCM → KXRAINNY
        if prefix.endswith("CM"):
            m[prefix[:-2]] = display
        elif prefix.endswith("M"):
            m[prefix[:-1]] = display
    return m

_PREFIX_MAP = _build_prefix_map()


def ticker_to_city(ticker: str) -> str:
    """Convert a Kalshi ticker like KXHIGHNY-26MAR13-B55 to a display city name.

    Returns the raw prefix if no match is found.
    """
    prefix = ticker.split("-")[0] if "-" in ticker else ticker
    return _PREFIX_MAP.get(prefix, prefix)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_ticker_map.py -v`
Expected: All tests PASS

- [ ] **Step 5: Create dashboard/__init__.py**

```bash
touch dashboard/__init__.py
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/__init__.py dashboard/ticker_map.py tests/test_ticker_map.py
git commit -m "feat: ticker-to-city display name mapping utility"
```

---

### Task 2: Scan Cache Database Module

**Files:**
- Create: `dashboard/scan_cache.py`
- Create: `tests/test_scan_cache.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_scan_cache.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/scan_cache.py
"""Read/write interface for data/scan_cache.db.

Tables: scan_results (cached market scans), model_outcomes (settled predictions).
"""
import os
import sqlite3
from datetime import datetime, timezone

SCAN_CACHE_DB = "data/scan_cache.db"


def _connect(db_path: str = SCAN_CACHE_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_scan_cache_db(db_path: str = SCAN_CACHE_DB):
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            market_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            city TEXT NOT NULL,
            model_prob REAL NOT NULL,
            market_price REAL NOT NULL,
            edge REAL NOT NULL,
            direction TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            method TEXT,
            threshold TEXT,
            days_left INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_scan_results_latest
            ON scan_results(market_type, scan_time);
        CREATE INDEX IF NOT EXISTS idx_scan_results_heatmap
            ON scan_results(market_type, city, scan_time);

        CREATE TABLE IF NOT EXISTS model_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            city TEXT NOT NULL,
            market_type TEXT NOT NULL,
            predicted_prob REAL NOT NULL,
            market_price REAL NOT NULL,
            actual_outcome INTEGER NOT NULL,
            settled_time TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def write_scan_results(rows: list[dict], scan_time: str = None, db_path: str = SCAN_CACHE_DB):
    if scan_time is None:
        scan_time = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    for r in rows:
        conn.execute(
            """INSERT INTO scan_results
               (scan_time, market_type, ticker, city, model_prob, market_price,
                edge, direction, confidence, method, threshold, days_left)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_time, r["market_type"], r["ticker"], r["city"],
             r["model_prob"], r["market_price"], r["edge"], r["direction"],
             r["confidence"], r.get("method"), r.get("threshold"), r.get("days_left")),
        )
    conn.commit()
    conn.close()


def get_latest_scan(market_type: str, db_path: str = SCAN_CACHE_DB) -> dict:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT scan_time FROM scan_results WHERE market_type = ? ORDER BY scan_time DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    if not row:
        conn.close()
        return {"scan_time": None, "markets": []}
    scan_time = row["scan_time"]
    rows = conn.execute(
        "SELECT * FROM scan_results WHERE market_type = ? AND scan_time = ?",
        (market_type, scan_time),
    ).fetchall()
    conn.close()
    return {
        "scan_time": scan_time,
        "markets": [dict(r) for r in rows],
    }


def get_scan_history(market_type: str, days: int = 30, db_path: str = SCAN_CACHE_DB) -> list[dict]:
    """Get scan history for heatmap — one row per city per day (latest scan of each day)."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT city, DATE(scan_time) as scan_date, edge, confidence
           FROM scan_results s1
           WHERE market_type = ?
             AND scan_time >= datetime('now', ?)
             AND scan_time = (
                 SELECT MAX(scan_time) FROM scan_results s2
                 WHERE s2.market_type = s1.market_type
                   AND s2.city = s1.city
                   AND DATE(s2.scan_time) = DATE(s1.scan_time)
             )
           ORDER BY scan_date""",
        (market_type, f"-{days} days"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def write_model_outcome(ticker: str, city: str, market_type: str,
                        predicted_prob: float, market_price: float,
                        actual_outcome: int, db_path: str = SCAN_CACHE_DB):
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO model_outcomes
           (ticker, city, market_type, predicted_prob, market_price, actual_outcome, settled_time)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ticker) DO UPDATE SET
               actual_outcome = excluded.actual_outcome,
               settled_time = excluded.settled_time""",
        (ticker, city, market_type, predicted_prob, market_price,
         actual_outcome, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_model_outcomes(db_path: str = SCAN_CACHE_DB) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT ticker, city, market_type, predicted_prob as predicted, "
        "market_price as market, actual_outcome as actual, settled_time as settled "
        "FROM model_outcomes ORDER BY settled_time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_old_scans(days: int = 30, db_path: str = SCAN_CACHE_DB):
    conn = _connect(db_path)
    conn.execute(
        "DELETE FROM scan_results WHERE scan_time < datetime('now', ?)",
        (f"-{days} days",),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_scan_cache.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/scan_cache.py tests/test_scan_cache.py
git commit -m "feat: scan cache database module for market scan results"
```

---

### Task 3: Equity History Database Module

**Files:**
- Create: `dashboard/equity_db.py`
- Create: `tests/test_equity_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_equity_db.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.equity_db import init_equity_db, record_equity_snapshot, get_equity_curve


def test_init_creates_table(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    import sqlite3
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "equity_snapshots" in tables


def test_record_and_read(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    record_equity_snapshot(
        date="2026-03-13", total_equity=225.04, cash=142.50,
        portfolio_value=82.54, realized_pnl=4.20, fees_paid=2.60,
        win_count=4, loss_count=2, db_path=db,
    )
    curve = get_equity_curve(db_path=db)
    assert len(curve) == 1
    assert curve[0]["date"] == "2026-03-13"
    assert curve[0]["equity"] == 225.04


def test_upsert_same_date(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    record_equity_snapshot(
        date="2026-03-13", total_equity=200.0, cash=100.0,
        portfolio_value=100.0, realized_pnl=0.0, fees_paid=0.0,
        win_count=0, loss_count=0, db_path=db,
    )
    record_equity_snapshot(
        date="2026-03-13", total_equity=225.04, cash=142.50,
        portfolio_value=82.54, realized_pnl=4.20, fees_paid=2.60,
        win_count=4, loss_count=2, db_path=db,
    )
    curve = get_equity_curve(db_path=db)
    assert len(curve) == 1
    assert curve[0]["equity"] == 225.04  # Updated, not duplicated


def test_empty_curve(tmp_path):
    db = str(tmp_path / "equity.db")
    init_equity_db(db)
    curve = get_equity_curve(db_path=db)
    assert curve == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_equity_db.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/equity_db.py
"""Read/write interface for data/equity_history.db.

Table: equity_snapshots — one row per day, appended by daily P&L script.
"""
import os
import sqlite3

EQUITY_DB = "data/equity_history.db"


def _connect(db_path: str = EQUITY_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_equity_db(db_path: str = EQUITY_DB):
    conn = _connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_equity REAL NOT NULL,
            cash REAL NOT NULL,
            portfolio_value REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            fees_paid REAL NOT NULL,
            win_count INTEGER NOT NULL DEFAULT 0,
            loss_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def record_equity_snapshot(date: str, total_equity: float, cash: float,
                           portfolio_value: float, realized_pnl: float,
                           fees_paid: float, win_count: int, loss_count: int,
                           db_path: str = EQUITY_DB):
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO equity_snapshots
           (date, total_equity, cash, portfolio_value, realized_pnl, fees_paid, win_count, loss_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
               total_equity = excluded.total_equity,
               cash = excluded.cash,
               portfolio_value = excluded.portfolio_value,
               realized_pnl = excluded.realized_pnl,
               fees_paid = excluded.fees_paid,
               win_count = excluded.win_count,
               loss_count = excluded.loss_count""",
        (date, total_equity, cash, portfolio_value, realized_pnl, fees_paid, win_count, loss_count),
    )
    conn.commit()
    conn.close()


def get_equity_curve(db_path: str = EQUITY_DB) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT date, total_equity as equity, realized_pnl, fees_paid as fees "
        "FROM equity_snapshots ORDER BY date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_equity_db.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/equity_db.py tests/test_equity_db.py
git commit -m "feat: equity history database module for daily snapshots"
```

---

### Task 4: Integrate Scan Cache into Scanner Daemon

**Files:**
- Modify: `scanner.py` (after precip signal execution, ~line 393)

- [ ] **Step 1: Read scanner.py to find exact insertion point**

Read `scanner.py` lines 380-410 to confirm the insertion point between precip signal execution and the fill poller.

- [ ] **Step 2: Add scan cache write**

At the top of `scanner.py`, add import:
```python
from dashboard.scan_cache import init_scan_cache_db, write_scan_results, cleanup_old_scans
```

After precip signal execution (after the `for sig in tradeable_precip_signals` loop), before the fill poller, add:

```python
# === CACHE SCAN RESULTS ===
try:
    init_scan_cache_db()
    cache_rows = []
    # IMPORTANT: Read scanner.py to confirm exact dict keys used in
    # tradeable_temp_signals and tradeable_precip_signals before implementing.
    # The keys below are best guesses — verify against the actual signal dicts.
    # Common keys to check: "ticker", "city"/"city_key", "model_prob",
    # "yes_price"/"market_prob", "edge", "direction", "confidence",
    # "low"/"high" (temp buckets), "threshold" (precip)
    from dashboard.ticker_map import ticker_to_city
    for sig in tradeable_temp_signals:
        ticker = sig.get("ticker", "")
        low = sig.get("low", 0)
        high = sig.get("high")
        bucket_str = f"{low:.0f}-{high:.0f}" if high else f">{low:.0f}"
        cache_rows.append({
            "market_type": "temp",
            "ticker": ticker,
            "city": ticker_to_city(ticker),
            "model_prob": sig.get("model_prob", 0),
            "market_price": sig.get("yes_price", sig.get("market_prob", 0)),
            "edge": sig.get("edge", 0),
            "direction": sig.get("direction", ""),
            "confidence": sig.get("confidence", 0),
            "method": "",
            "threshold": bucket_str,
            "days_left": 1,
        })
    for sig in tradeable_precip_signals:
        ticker = sig.get("ticker", "")
        cache_rows.append({
            "market_type": "precip",
            "ticker": ticker,
            "city": ticker_to_city(ticker),
            "model_prob": sig.get("model_prob", 0),
            "market_price": sig.get("yes_price", sig.get("market_prob", 0)),
            "edge": sig.get("edge", 0),
            "direction": sig.get("direction", ""),
            "confidence": sig.get("confidence", 0),
            "method": "",
            "threshold": sig.get("threshold", ""),
            "days_left": sig.get("remaining_days"),
        })
    if cache_rows:
        write_scan_results(cache_rows)
        cleanup_old_scans(days=30)
        print(f"  [Cache] Wrote {len(cache_rows)} scan results to cache")
except Exception as e:
    print(f"  [Cache] Error writing scan cache: {e}")
```

**IMPORTANT:** The exact dict keys in `tradeable_temp_signals` and `tradeable_precip_signals` MUST be verified by reading scanner.py before implementing. The code above uses `.get()` with fallbacks to handle key name uncertainty. The implementer must read `scanner.py` lines 260-390 and adjust the key names to match.

- [ ] **Step 3: Test manually**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python scanner.py 2>&1 | tail -20`
Expected: See `[Cache] Wrote N scan results to cache` in output. Verify `data/scan_cache.db` exists.

- [ ] **Step 4: Commit**

```bash
git add scanner.py
git commit -m "feat: write scan results to cache DB after each cycle"
```

---

### Task 5: Integrate Equity Snapshots into Daily P&L Script

**Files:**
- Modify: `scripts/daily_pnl_summary.py` (~line 89)

- [ ] **Step 1: Add equity snapshot write**

At the top of `scripts/daily_pnl_summary.py`, add import:
```python
from dashboard.equity_db import init_equity_db, record_equity_snapshot
```

After `total_pnl = round(realized + unrealized, 2)` (line 88), before the Telegram message, add:

```python
    # Record equity snapshot
    try:
        init_equity_db()
        settled_positions = get_positions(settlement_status="settled")
        wins = sum(1 for p in settled_positions if float(p.get("realized_pnl_dollars", "0")) > 0)
        losses = sum(1 for p in settled_positions if float(p.get("realized_pnl_dollars", "0")) < 0)
        settled_fees = sum(float(p.get("fees_paid_dollars", "0")) for p in settled_positions)
        record_equity_snapshot(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_equity=total_equity,
            cash=cash,
            portfolio_value=portfolio,
            realized_pnl=realized,
            fees_paid=settled_fees,
            win_count=wins,
            loss_count=losses,
        )
        print("Equity snapshot recorded.")
    except Exception as e:
        print(f"Equity snapshot failed: {e}")
```

- [ ] **Step 2: Test manually**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python scripts/daily_pnl_summary.py 2>&1 | tail -10`
Expected: See "Equity snapshot recorded." and verify `data/equity_history.db` exists.

- [ ] **Step 3: Commit**

```bash
git add scripts/daily_pnl_summary.py
git commit -m "feat: record equity snapshots to DB in daily P&L script"
```

---

### Task 5b: Integrate Model Outcomes into Resolver

**Files:**
- Modify: `weather/resolver.py`

- [ ] **Step 1: Read resolver.py to find exact insertion point**

Read `weather/resolver.py` to find where `update_bias()` is called after market settlement. The model_outcomes write goes right after.

- [ ] **Step 2: Add model outcomes write**

At the top of `weather/resolver.py`, add import:
```python
from dashboard.scan_cache import init_scan_cache_db, write_model_outcome
```

In `run_resolver()`, after the bias update loop for each (city, target_date) group, add:

```python
        # Record model outcome for dashboard accuracy chart
        try:
            init_scan_cache_db()
            # Use the ensemble model prediction as our "predicted_prob"
            # This requires knowing what the market ticker was — skip if not available
        except Exception as e:
            print(f"    Model outcome write failed: {e}")
```

**Note:** The resolver operates on temperature forecasts (bias correction), not market probabilities. The model_outcomes table needs `predicted_prob` and `market_price` — values that are only available at scan time, not settlement time. The better approach: record model outcomes in the **scanner** when it encounters a settled market, or have the scanner record predictions at scan time and the resolver marks them as settled later.

For now, the simplest path: have the scanner write predictions to `model_outcomes` at scan time with `actual_outcome = -1` (pending), then the resolver updates them when settled. But this adds complexity. An acceptable MVP: **skip resolver integration for now** — populate `model_outcomes` manually or via a future reconciliation script. The model accuracy chart will show "No data yet" until this is implemented fully.

- [ ] **Step 3: Commit (if changes were made)**

```bash
git add weather/resolver.py
git commit -m "feat: placeholder for model outcomes integration in resolver"
```

---

### Task 6: Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add FastAPI and Uvicorn**

Add to `requirements.txt`:
```
fastapi
uvicorn[standard]
```

- [ ] **Step 2: Install**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && pip install fastapi uvicorn[standard]`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add fastapi and uvicorn"
```

---

## Chunk 2: API Layer

### Task 7: FastAPI App with Portfolio Endpoint

**Files:**
- Create: `dashboard/api.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from dashboard.api import app

client = TestClient(app)


def test_root_serves_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_portfolio_endpoint():
    resp = client.get("/api/portfolio")
    # Should return 200 even if Kalshi is unreachable (graceful error)
    assert resp.status_code in (200, 502)
    data = resp.json()
    if resp.status_code == 200:
        assert "balance" in data
        assert "settled" in data
        assert "open_positions" in data
        assert "resting_orders" in data


def test_config_endpoint():
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert "edge_gate" in data
    assert "kelly_range" in data
    assert isinstance(data["kelly_range"], list)


def test_performance_endpoint():
    resp = client.get("/api/performance")
    assert resp.status_code == 200
    data = resp.json()
    assert "equity_curve" in data
    assert "model_accuracy" in data
    assert isinstance(data["equity_curve"], list)


def test_markets_temp_cached():
    resp = client.get("/api/markets/temp")
    assert resp.status_code == 200
    data = resp.json()
    assert "scan_time" in data
    assert "markets" in data


def test_markets_precip_cached():
    resp = client.get("/api/markets/precip")
    assert resp.status_code == 200
    data = resp.json()
    assert "scan_time" in data
    assert "markets" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the FastAPI app**

```python
# dashboard/api.py
"""FastAPI backend for Weather Edge dashboard.

Serves JSON API endpoints + static frontend files.
Run: uvicorn dashboard.api:app --host 127.0.0.1 --port 8501
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from datetime import datetime, timezone

from dashboard.ticker_map import ticker_to_city
from dashboard.scan_cache import (
    init_scan_cache_db, get_latest_scan, get_scan_history,
    get_model_outcomes, write_scan_results, cleanup_old_scans,
)
from dashboard.equity_db import init_equity_db, get_equity_curve

from config import (
    PAPER_MODE, ALERT_THRESHOLD, CONFIDENCE_THRESHOLD,
    MAX_ORDER_USD, MAX_SCAN_BUDGET, KELLY_MAX_FRACTION,
    DRAWDOWN_THRESHOLD, FRACTIONAL_KELLY, DAILY_STOP_PCT,
    MAX_ENSEMBLE_HORIZON_DAYS,
)

app = FastAPI(title="Weather Edge API")

# Serve static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup():
    init_scan_cache_db()
    init_equity_db()


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"error": "Frontend not built yet"}, status_code=404)


@app.get("/api/portfolio")
async def portfolio():
    try:
        from kalshi.trader import get_balance, get_positions, get_orders

        # Balance
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        positions_val = bal.get("portfolio_value", 0) / 100.0
        equity = cash + positions_val
        deployed_pct = round(positions_val / equity * 100, 1) if equity > 0 else 0

        # Settled P&L
        settled = get_positions(limit=200, settlement_status="settled")
        gross_pnl = sum(float(p.get("realized_pnl_dollars", "0")) for p in settled)
        fees = sum(float(p.get("fees_paid_dollars", "0")) for p in settled)
        net_pnl = gross_pnl - fees
        wins = sum(1 for p in settled if float(p.get("realized_pnl_dollars", "0")) > 0)
        losses = sum(1 for p in settled if float(p.get("realized_pnl_dollars", "0")) < 0)
        total_settled = wins + losses
        hit_rate = round(wins / total_settled * 100, 1) if total_settled > 0 else 0

        # Open positions
        all_pos = get_positions()
        open_pos = [p for p in all_pos if float(p.get("position_fp", "0")) != 0]
        open_positions = []
        for p in open_pos:
            qty = float(p.get("position_fp", "0"))
            abs_qty = abs(qty)
            cost = float(p.get("total_traded_dollars", "0"))
            exposure = float(p.get("market_exposure_dollars", "0"))
            realized = float(p.get("realized_pnl_dollars", "0"))
            ticker = p.get("ticker", "")
            open_positions.append({
                "ticker": ticker,
                "city": ticker_to_city(ticker),
                "side": "YES" if qty > 0 else "NO",
                "qty": int(abs_qty),
                "entry": round(cost / abs_qty, 2) if abs_qty > 0 else 0,
                "exposure": round(exposure, 2),
                "pnl": round(exposure - cost + realized, 2),
                "fees": round(float(p.get("fees_paid_dollars", "0")), 2),
            })

        # Resting orders
        resting = get_orders(status="resting")
        resting_orders = []
        for o in resting:
            side = o.get("side", "")
            price = o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars")
            resting_orders.append({
                "ticker": o.get("ticker", ""),
                "action": o.get("action", "").upper(),
                "side": side.upper(),
                "remaining": int(float(o.get("remaining_count_fp", "0"))),
                "price": float(price) if price else 0,
                "created": (o.get("created_time", "")[:16].replace("T", " ")),
            })

        return {
            "balance": {
                "cash": round(cash, 2),
                "positions": round(positions_val, 2),
                "equity": round(equity, 2),
                "deployed_pct": deployed_pct,
            },
            "settled": {
                "gross_pnl": round(gross_pnl, 2),
                "fees": round(fees, 2),
                "net_pnl": round(net_pnl, 2),
                "wins": wins,
                "losses": losses,
                "hit_rate": hit_rate,
                "total_settled": total_settled,
            },
            "open_positions": open_positions,
            "resting_orders": resting_orders,
        }
    except Exception as e:
        return JSONResponse({"error": str(e), "cached_at": None}, status_code=502)


@app.get("/api/markets/{market_type}")
async def markets(market_type: str, force: bool = Query(False)):
    if market_type not in ("temp", "precip"):
        return JSONResponse({"error": "market_type must be 'temp' or 'precip'"}, status_code=400)

    if force:
        try:
            from kalshi.scanner import (
                get_kalshi_weather_markets, get_kalshi_precip_markets,
                get_kalshi_price, parse_kalshi_bucket,
            )
            from weather.multi_model import fuse_forecast, fuse_precip_forecast
            from weather.forecast import calculate_remaining_month_days

            month = datetime.now(timezone.utc).month
            remaining_days = calculate_remaining_month_days()
            cache_rows = []

            if market_type == "temp":
                raw_markets = get_kalshi_weather_markets()
                for m in raw_markets:
                    try:
                        yes_price = get_kalshi_price(m)
                        if yes_price is None:
                            continue
                        bucket = parse_kalshi_bucket(m)
                        if not bucket:
                            continue
                        low, high = bucket
                        city = m.get("_city", "unknown")
                        ticker = m.get("ticker", "")
                        prob, confidence, details = fuse_forecast(
                            m["_lat"], m["_lon"], city, month,
                            low, high, days_ahead=1, unit=m.get("_unit", "f"),
                        )
                        edge = prob - yes_price
                        bucket_str = f"{low:.0f}-{high:.0f}" if high else f">{low:.0f}"
                        cache_rows.append({
                            "market_type": "temp",
                            "ticker": ticker,
                            "city": ticker_to_city(ticker),
                            "model_prob": round(prob, 4),
                            "market_price": round(yes_price, 4),
                            "edge": round(edge, 4),
                            "direction": "BUY YES" if edge > 0 else "SELL YES",
                            "confidence": confidence,
                            "method": details.get("ensemble", {}).get("method", ""),
                            "threshold": bucket_str,
                            "days_left": 1,
                        })
                    except Exception:
                        pass
            else:
                raw_markets = get_kalshi_precip_markets()
                forecast_window = min(remaining_days, MAX_ENSEMBLE_HORIZON_DAYS)
                for m in raw_markets:
                    try:
                        yes_price = get_kalshi_price(m)
                        if yes_price is None:
                            continue
                        threshold = m.get("_threshold", 0.0)
                        city = m.get("_city", "unknown")
                        ticker = m.get("ticker", "")
                        prob, confidence, details = fuse_precip_forecast(
                            m["_lat"], m["_lon"], city, month,
                            threshold=threshold, forecast_days=forecast_window,
                        )
                        edge = prob - yes_price
                        cache_rows.append({
                            "market_type": "precip",
                            "ticker": ticker,
                            "city": ticker_to_city(ticker),
                            "model_prob": round(prob, 4),
                            "market_price": round(yes_price, 4),
                            "edge": round(edge, 4),
                            "direction": "BUY YES" if edge > 0 else "SELL YES",
                            "confidence": confidence,
                            "method": details.get("ensemble", {}).get("method", ""),
                            "threshold": f">{threshold:.1f} in",
                            "days_left": remaining_days,
                        })
                    except Exception:
                        pass

            if cache_rows:
                write_scan_results(cache_rows)
                cleanup_old_scans(days=30)

            scan_time = datetime.now(timezone.utc).isoformat()
            return {"scan_time": scan_time, "markets": cache_rows}

        except Exception as e:
            # Fall back to cached data on error
            cached = get_latest_scan(market_type)
            cached["error"] = str(e)
            return cached

    # Default: return cached data
    return get_latest_scan(market_type)


@app.get("/api/markets/{market_type}/history")
async def market_history(market_type: str, days: int = Query(30)):
    if market_type not in ("temp", "precip"):
        return JSONResponse({"error": "market_type must be 'temp' or 'precip'"}, status_code=400)
    return get_scan_history(market_type, days=days)


@app.get("/api/performance")
async def performance():
    return {
        "equity_curve": get_equity_curve(),
        "model_accuracy": get_model_outcomes(),
    }


@app.get("/api/config")
async def config():
    return {
        "mode": "PAPER" if PAPER_MODE else "LIVE",
        "scan_interval_min": 15,
        "edge_gate": ALERT_THRESHOLD,
        "confidence_gate": CONFIDENCE_THRESHOLD,
        "kelly_range": [FRACTIONAL_KELLY, 0.50],
        "max_order_bankroll_pct": KELLY_MAX_FRACTION,
        "max_order_usd": MAX_ORDER_USD,
        "scan_budget_usd": MAX_SCAN_BUDGET,
        "drawdown_threshold": DRAWDOWN_THRESHOLD,
        "daily_stop_pct": DAILY_STOP_PCT,
    }
```

- [ ] **Step 4: Create placeholder index.html so root serves something**

```html
<!-- dashboard/static/index.html -->
<!DOCTYPE html>
<html><head><title>Weather Edge</title></head>
<body><h1>Weather Edge — frontend coming soon</h1></body>
</html>
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_api.py -v`
Expected: `test_root_serves_html`, `test_config_endpoint`, `test_performance_endpoint`, `test_markets_temp_cached`, `test_markets_precip_cached` PASS. `test_portfolio_endpoint` may return 502 if Kalshi API credentials aren't set — that's OK (graceful error).

- [ ] **Step 6: Commit**

```bash
git add dashboard/api.py tests/test_api.py dashboard/static/index.html
git commit -m "feat: FastAPI app with all API endpoints"
```

---

## Chunk 3: Frontend — Structure & Styles

### Task 8: SCSS Foundation

**Files:**
- Create: `dashboard/static/scss/_variables.scss`
- Create: `dashboard/static/scss/_base.scss`
- Create: `dashboard/static/scss/_header.scss`
- Create: `dashboard/static/scss/_metrics.scss`
- Create: `dashboard/static/scss/_charts.scss`
- Create: `dashboard/static/scss/_tables.scss`
- Create: `dashboard/static/scss/_footer.scss`
- Create: `dashboard/static/scss/main.scss`
- Create: `dashboard/static/css/main.css` (compiled)
- Create: `dashboard/static/assets/logo.svg`

This task creates all SCSS files and compiles them. The exact SCSS content should follow the brand constants from the spec (`_variables.scss`) and the visual design from the wireframe. Use the `frontend-design` skill for the final implementation of these files.

- [ ] **Step 1: Create _variables.scss**

All brand colors, font stacks, spacing, and breakpoints from the spec's Brand Constants section.

- [ ] **Step 2: Create _base.scss**

Global reset, font imports (Google Fonts), body background gradient, typography defaults, scrollbar styling.

- [ ] **Step 3: Create _header.scss**

Header bar layout: logo, title, subtitle, refresh button, mode badge, timestamp. Flexbox, right-aligned meta.

- [ ] **Step 4: Create _metrics.scss**

Metric cards: `.metric-card` with border-left accent colors, label/value/subtitle layout. `.metric-row` as a 4-column grid. Positive/negative/neutral/accent variants.

- [ ] **Step 5: Create _charts.scss**

Chart containers: `.chart-card` with background, border, padding. `.chart-row` as 2-column grid. Plotly theme overrides if needed.

- [ ] **Step 6: Create _tables.scss**

Data tables: `.data-table` with header row styling, row hover, P&L tint backgrounds. Monospace font for numbers.

- [ ] **Step 7: Create _footer.scss**

Risk controls grid, signal summary cards, section labels, dividers.

- [ ] **Step 8: Create main.scss**

```scss
@import 'variables';
@import 'base';
@import 'header';
@import 'metrics';
@import 'charts';
@import 'tables';
@import 'footer';
```

- [ ] **Step 9: Extract logo.svg**

Save the EE logo SVG from the current `dashboard/app.py` `LOGO_SVG` constant to `dashboard/static/assets/logo.svg`.

- [ ] **Step 10: Compile SCSS**

Run: `sass dashboard/static/scss/main.scss dashboard/static/css/main.css`

- [ ] **Step 11: Commit**

```bash
git add dashboard/static/scss/ dashboard/static/css/main.css dashboard/static/assets/logo.svg
git commit -m "feat: SCSS foundation with brand theme"
```

---

### Task 9: HTML Page Structure

**Files:**
- Modify: `dashboard/static/index.html`

- [ ] **Step 1: Write the full index.html**

Single page with all section containers. Key structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Weather Edge</title>
    <link rel="stylesheet" href="/static/css/main.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@700;900&family=Roboto:wght@300;400;500;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.plot.ly/plotly-2.35.0.min.js" charset="utf-8"></script>
</head>
<body>
    <!-- Header -->
    <header class="we-header">
        <img src="/static/assets/logo.svg" alt="WE" class="we-header-logo">
        <div class="we-header-text">
            <h1 class="we-header-title">Weather Edge</h1>
            <p class="we-header-subtitle">Automated Weather Market Trading Terminal</p>
        </div>
        <div class="we-header-right">
            <button class="we-refresh-btn" id="refresh-btn" title="Refresh data">↻</button>
            <div class="we-header-meta">
                <span id="mode-badge" class="we-mode-badge"></span>
                <span id="header-timestamp" class="we-timestamp"></span>
            </div>
        </div>
    </header>

    <!-- How This Works -->
    <details class="we-expander">
        <summary>How This Works</summary>
        <div class="we-expander-content">
            <!-- Pipeline explainer text -->
        </div>
    </details>

    <!-- Portfolio -->
    <section id="portfolio-section">
        <h2>Portfolio</h2>
        <div id="portfolio-balance" class="metric-row"></div>
        <div id="portfolio-settled-label" class="section-label">Settled Performance</div>
        <div id="portfolio-settled" class="metric-row"></div>
        <div id="open-positions-header"></div>
        <div id="open-positions-table"></div>
        <div id="open-positions-caption" class="caption"></div>
        <details class="we-expander" id="resting-orders-expander">
            <summary>Resting Orders (<span id="resting-count">0</span>)</summary>
            <div id="resting-orders-table"></div>
        </details>
    </section>

    <hr class="we-divider">

    <!-- Performance -->
    <section id="performance-section">
        <h2>Performance</h2>
        <div class="chart-row">
            <div class="chart-card">
                <div class="chart-label">Equity Curve</div>
                <div id="equity-chart"></div>
            </div>
            <div class="chart-card">
                <div class="chart-label">Model Accuracy — Predicted vs Actual</div>
                <div id="accuracy-chart"></div>
            </div>
        </div>
    </section>

    <hr class="we-divider">

    <!-- Precipitation Markets -->
    <section id="precip-section">
        <h2>Precipitation Markets</h2>
        <p class="section-desc">Monthly cumulative rainfall contracts. Settles at month-end via official station readings.</p>
        <div class="scan-meta">
            <span id="precip-scan-time" class="scan-time"></span>
            <button class="force-rescan-btn" id="precip-rescan">Force Rescan</button>
        </div>
        <div id="precip-table"></div>
        <div class="chart-row">
            <div class="chart-card">
                <div class="chart-label">Edge by City</div>
                <div id="precip-edge-chart"></div>
            </div>
            <div class="chart-card">
                <div class="chart-label" id="precip-chart2-label">Edge Heatmap — City x Date</div>
                <div id="precip-heatmap-chart"></div>
            </div>
        </div>
        <div id="precip-signal-badge"></div>
    </section>

    <hr class="we-divider">

    <!-- Temperature Markets -->
    <section id="temp-section">
        <h2>Temperature Markets</h2>
        <p class="section-desc">Daily high temperature contracts across US cities. Settles next day via official airport readings.</p>
        <div class="scan-meta">
            <span id="temp-scan-time" class="scan-time"></span>
            <button class="force-rescan-btn" id="temp-rescan">Force Rescan</button>
        </div>
        <div id="temp-table"></div>
        <div class="chart-row">
            <div class="chart-card">
                <div class="chart-label">Edge by City</div>
                <div id="temp-edge-chart"></div>
            </div>
            <div class="chart-card">
                <div class="chart-label" id="temp-chart2-label">Edge Heatmap — City x Date</div>
                <div id="temp-heatmap-chart"></div>
            </div>
        </div>
        <div id="temp-signal-badge"></div>
    </section>

    <hr class="we-divider">

    <!-- Footer -->
    <footer>
        <div id="signal-summary" class="metric-row metric-row-3"></div>
        <div id="risk-controls" class="risk-footer"></div>
    </footer>

    <!-- JS modules -->
    <script type="module" src="/static/js/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/index.html
git commit -m "feat: HTML page structure with all section containers"
```

---

## Chunk 4: Frontend — JavaScript

### Task 10: App.js — Orchestration & Refresh

**Files:**
- Create: `dashboard/static/js/app.js`

- [ ] **Step 1: Write app.js**

```javascript
// dashboard/static/js/app.js
import { renderPortfolio } from './portfolio.js';
import { renderMarkets } from './markets.js';
import { renderPerformance } from './performance.js';

async function fetchJSON(url, timeout = 30000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeout);
    try {
        const resp = await fetch(url, { signal: controller.signal });
        clearTimeout(id);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (e) {
        clearTimeout(id);
        throw e;
    }
}

async function loadSection(url, renderFn, containerId, timeout) {
    const container = document.getElementById(containerId);
    try {
        const data = await fetchJSON(url, timeout);
        renderFn(data);
    } catch (e) {
        if (container) {
            container.innerHTML = `<div class="error-msg">Failed to load: ${e.message}</div>`;
        }
    }
}

async function refreshAll() {
    const btn = document.getElementById('refresh-btn');
    btn.classList.add('spinning');

    await Promise.allSettled([
        loadSection('/api/portfolio', renderPortfolio, 'portfolio-balance'),
        loadSection('/api/markets/precip', (d) => renderMarkets(d, 'precip'), 'precip-table'),
        loadSection('/api/markets/temp', (d) => renderMarkets(d, 'temp'), 'temp-table'),
        loadSection('/api/performance', renderPerformance, 'equity-chart'),
        loadConfig(),
    ]);

    btn.classList.remove('spinning');
    document.getElementById('header-timestamp').textContent =
        new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

async function loadConfig() {
    try {
        const cfg = await fetchJSON('/api/config');
        const badge = document.getElementById('mode-badge');
        badge.textContent = cfg.mode;
        badge.className = `we-mode-badge we-mode-${cfg.mode.toLowerCase()}`;

        // Risk controls footer
        const rc = document.getElementById('risk-controls');
        rc.innerHTML = `
            <div class="risk-footer-title">Risk Controls</div>
            <div class="risk-grid">
                <div class="risk-item"><strong>Scan:</strong> ${cfg.scan_interval_min} min</div>
                <div class="risk-item"><strong>Edge:</strong> ${(cfg.edge_gate * 100).toFixed(0)}%</div>
                <div class="risk-item"><strong>Conf:</strong> ${cfg.confidence_gate}</div>
                <div class="risk-item"><strong>Kelly:</strong> ${cfg.kelly_range[0]}x-${cfg.kelly_range[1]}x</div>
                <div class="risk-item"><strong>Max/order:</strong> ${(cfg.max_order_bankroll_pct * 100).toFixed(0)}% bankroll</div>
                <div class="risk-item"><strong>$/order:</strong> $${cfg.max_order_usd.toFixed(0)}</div>
                <div class="risk-item"><strong>$/scan:</strong> $${cfg.scan_budget_usd.toFixed(0)}</div>
                <div class="risk-item"><strong>Drawdown:</strong> ${(cfg.drawdown_threshold * 100).toFixed(0)}%</div>
            </div>`;
    } catch (e) {
        console.error('Config load failed:', e);
    }
}

// Force rescan handlers
async function forceRescan(marketType) {
    const btn = document.getElementById(`${marketType}-rescan`);
    btn.textContent = 'Scanning...';
    btn.disabled = true;
    try {
        const data = await fetchJSON(`/api/markets/${marketType}?force=true`, 90000);
        renderMarkets(data, marketType);
    } catch (e) {
        console.error(`Rescan ${marketType} failed:`, e);
    } finally {
        btn.textContent = 'Force Rescan';
        btn.disabled = false;
    }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-btn').addEventListener('click', refreshAll);
    document.getElementById('precip-rescan').addEventListener('click', () => forceRescan('precip'));
    document.getElementById('temp-rescan').addEventListener('click', () => forceRescan('temp'));
    refreshAll();
});

export { fetchJSON };
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/js/app.js
git commit -m "feat: app.js — fetch orchestration and refresh logic"
```

---

### Task 11: Portfolio.js — Portfolio Section Rendering

**Files:**
- Create: `dashboard/static/js/portfolio.js`

- [ ] **Step 1: Write portfolio.js**

This module receives the `/api/portfolio` response and renders:
- 4 balance metric cards (Cash, Positions, Net P&L, Hit Rate — each with subtitle)
- 4 settled performance metric cards (Gross P&L, Fees, Net P&L, Hit Rate — each with delta line)
- Open positions table with P&L row tinting
- Resting orders table inside the `<details>` expander
- Summary caption line

Key functions:
- `renderPortfolio(data)` — main entry point
- `metricCard(label, value, subtitle, accentClass)` — returns HTML string for a metric card
- `positionsTable(positions)` — returns HTML string for the positions table
- `ordersTable(orders)` — returns HTML string for the resting orders table

All currency values formatted with `$` prefix, `+`/`-` signs, and 2 decimal places. Use JetBrains Mono for numbers via CSS class.

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/js/portfolio.js
git commit -m "feat: portfolio.js — balance, P&L, positions rendering"
```

---

### Task 12: Markets.js — Market Tables & Charts

**Files:**
- Create: `dashboard/static/js/markets.js`

- [ ] **Step 1: Write markets.js**

This module receives the `/api/markets/{type}` response and renders:
- Scan timestamp + updates the "Force Rescan" button area
- Data table (City, Ticker, Threshold, Model, Market, Edge, Direction, Confidence)
- Edge by City bar chart (Plotly)
- Edge Heatmap or Confidence-vs-Edge scatter (Plotly) — heatmap if 3+ days of scan history, scatter fallback otherwise
- Trade-worthy signal badge
- Signal count for footer summary

Key functions:
- `renderMarkets(data, type)` — main entry point. `type` is `"precip"` or `"temp"`.
- `marketsTable(markets)` — returns HTML string
- `edgeBarChart(markets, containerId)` — Plotly bar chart
- `edgeHeatmap(history, containerId)` — Plotly heatmap (fetches scan history from API)
- `confidenceScatter(markets, containerId)` — Plotly scatter fallback

Plotly layout uses the brand theme: `paper_bgcolor: 'rgba(0,0,0,0)'`, `plot_bgcolor: '#1a2332'`, font colors from variables, colorway from brand constants.

After rendering both market sections, update the footer signal counts.

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/js/markets.js
git commit -m "feat: markets.js — tables, bar charts, and heatmap/scatter"
```

---

### Task 13: Performance.js — Equity Curve & Model Accuracy

**Files:**
- Create: `dashboard/static/js/performance.js`

- [ ] **Step 1: Write performance.js**

This module receives the `/api/performance` response and renders:
- Equity Curve: Plotly line chart with area fill (green gradient), x=date, y=equity
- Model Accuracy: Plotly scatter, x=predicted probability, y=actual outcome (0 or 1), colored by correct/incorrect, with diagonal reference line

Key functions:
- `renderPerformance(data)` — main entry point
- `equityChart(curve, containerId)` — Plotly line+area chart
- `accuracyChart(outcomes, containerId)` — Plotly scatter chart

Handle empty data: show "No data yet — equity curve will appear after first daily snapshot" placeholder text.

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/js/performance.js
git commit -m "feat: performance.js — equity curve and model accuracy charts"
```

---

## Chunk 5: Deployment & Cleanup

### Task 14: Update run_dashboard.sh

**Files:**
- Modify: `run_dashboard.sh`

- [ ] **Step 1: Update to use uvicorn**

```bash
#!/bin/bash
cd /opt/weather-bot
source venv/bin/activate

# Kill existing dashboard
pkill -f "uvicorn dashboard.api" 2>/dev/null || true
sleep 1

# Start FastAPI dashboard
nohup uvicorn dashboard.api:app --host 127.0.0.1 --port 8501 >> logs/dashboard.log 2>&1 &
echo "Dashboard started (PID $!)"
```

- [ ] **Step 2: Commit**

```bash
git add run_dashboard.sh
git commit -m "feat: switch run_dashboard.sh from streamlit to uvicorn"
```

---

### Task 15: Remove Old Dashboard & Deploy

**Files:**
- Remove: `dashboard/app.py`
- Remove: `.streamlit/config.toml`

- [ ] **Step 1: Remove old files**

```bash
git rm dashboard/app.py .streamlit/config.toml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove old Streamlit dashboard"
```

- [ ] **Step 3: Deploy to Hetzner**

```bash
rsync -avz --exclude='venv/' --exclude='data/' --exclude='.env' --exclude='__pycache__/' \
    /Users/edede/Projects/polymarket-weather-bot/ hetzner:/opt/weather-bot/
```

- [ ] **Step 4: Install dependencies on server**

```bash
ssh hetzner "cd /opt/weather-bot && source venv/bin/activate && pip install fastapi uvicorn[standard]"
```

- [ ] **Step 5: Restart dashboard on server**

```bash
ssh hetzner "/opt/weather-bot/run_dashboard.sh"
```

- [ ] **Step 6: Update server crontab**

Update the streamlit watchdog to uvicorn:
```
*/5 * * * * pgrep -f "uvicorn dashboard.api" > /dev/null || /opt/weather-bot/run_dashboard.sh
```

- [ ] **Step 7: Verify**

Open `https://weather.ethanede.com` in browser. Verify:
- Page loads with branded dark theme
- Portfolio data populates
- Market tables show cached data (or "No data yet" if first run)
- Refresh button works
- Force Rescan works (may take 30-60s)

- [ ] **Step 8: Commit any deployment fixes**

```bash
git add -A && git commit -m "fix: deployment adjustments"
```
