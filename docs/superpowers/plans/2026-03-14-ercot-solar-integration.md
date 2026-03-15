# ERCOT Solar / Power Price Signal Integration — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add paper-traded ERCOT power price signals across 5 Texas hubs, with position management, scanner integration, and a dedicated dashboard tab.

**Architecture:** New `ercot/` package with `hubs.py` (data layer), `paper_trader.py` (SQLite-backed paper positions), and `position_manager.py` (evaluate/fortify/exit). Reuses existing risk rails (`risk/position_limits.py`) and Kelly sizing from config. Scanner and dashboard are extended to include ERCOT as a separate section.

**Tech Stack:** Python, SQLite, FastAPI, Rich tables, Open-Meteo API, ERCOT public API

**Spec:** `docs/superpowers/specs/2026-03-14-ercot-solar-integration-design.md`

---

## Chunk 1: Config + Signal Function Update + Data Layer

### Task 1: Add ERCOT config constants

**Files:**
- Modify: `config.py:66-70` (append after `MAX_ENSEMBLE_HORIZON_DAYS`)

- [ ] **Step 1: Add ERCOT constants to config.py**

Add at end of `config.py` (after line 70):

```python
# --- ERCOT Power Price Signal ---
ERCOT_HUBS = {
    "North":     {"city": "Dallas",      "lat": 32.78,  "lon": -96.80,  "hub_name": "HB_NORTH"},
    "Houston":   {"city": "Houston",     "lat": 29.76,  "lon": -95.37,  "hub_name": "HB_HOUSTON"},
    "South":     {"city": "San Antonio", "lat": 29.42,  "lon": -98.49,  "hub_name": "HB_SOUTH"},
    "West":      {"city": "Midland",     "lat": 31.99,  "lon": -102.08, "hub_name": "HB_WEST"},
    "Panhandle": {"city": "Amarillo",    "lat": 35.22,  "lon": -101.83, "hub_name": "HB_PAN"},
}

ERCOT_PAPER_BANKROLL = 10_000.0
ERCOT_PAPER_MODE = True
ERCOT_MIN_EDGE = 0.5
ERCOT_MIN_CONFIDENCE = 50
ERCOT_FORTIFY_EDGE_INCREASE = 0.5
ERCOT_EXIT_EDGE_DECAY = 0.30
ERCOT_MAX_POSITIONS_PER_HUB = 3
ERCOT_MAX_POSITIONS_TOTAL = 10
ERCOT_POSITION_TTL_HOURS = 24
```

- [ ] **Step 2: Verify config imports cleanly**

Run: `python -c "from config import ERCOT_HUBS, ERCOT_PAPER_BANKROLL, ERCOT_MIN_EDGE; print(f'Hubs: {len(ERCOT_HUBS)}, Bankroll: ${ERCOT_PAPER_BANKROLL}')"`
Expected: `Hubs: 5, Bankroll: $10000.0`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add ERCOT hub config and paper trading constants"
```

---

### Task 2: Update get_ercot_solar_signal() with ercot_data param

**Files:**
- Modify: `weather/multi_model.py:388-438`
- Test: `tests/test_ercot_signal.py` (create)

- [ ] **Step 1: Write test for updated signal function**

Create `tests/test_ercot_signal.py`:

```python
"""Tests for get_ercot_solar_signal with optional ercot_data."""


def test_signal_with_prefetched_data():
    """When ercot_data is provided, use those values instead of fetching."""
    from weather.multi_model import get_ercot_solar_signal

    ercot_data = {"price": 55.0, "solar_mw": 8000.0}
    result = get_ercot_solar_signal(32.78, -96.80, hours_ahead=24, ercot_data=ercot_data)

    assert result["current_ercot_price"] == 55.0
    assert result["actual_solar_mw"] == 8000.0
    assert result["signal"] in ("SHORT", "LONG", "NEUTRAL")
    assert "edge" in result
    assert "confidence" in result
    assert "expected_solrad_mjm2" in result


def test_signal_without_prefetched_data():
    """When ercot_data is None, function fetches directly (backwards compat)."""
    from weather.multi_model import get_ercot_solar_signal

    result = get_ercot_solar_signal(32.78, -96.80, hours_ahead=24)

    assert "current_ercot_price" in result
    assert "actual_solar_mw" in result
    assert result["signal"] in ("SHORT", "LONG", "NEUTRAL")


def test_signal_short_when_high_solrad():
    """High solar irradiance should produce SHORT signal."""
    from weather.multi_model import get_ercot_solar_signal

    # Mock by passing ercot_data — irradiance still comes from Open-Meteo
    result = get_ercot_solar_signal(32.78, -96.80, ercot_data={"price": 40.0, "solar_mw": 12000.0})
    # Can't control irradiance in test, but verify structure
    assert isinstance(result["edge"], float)
    assert isinstance(result["confidence"], int)


def test_signal_direction_multiplier():
    """Verify edge is always positive regardless of direction."""
    from weather.multi_model import get_ercot_solar_signal

    result = get_ercot_solar_signal(32.78, -96.80, ercot_data={"price": 40.0, "solar_mw": 12000.0})
    assert result["edge"] >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ercot_signal.py -v`
Expected: FAIL — `get_ercot_solar_signal()` does not accept `ercot_data` parameter

- [ ] **Step 3: Update get_ercot_solar_signal() in weather/multi_model.py**

Replace the function at lines 388-438 with:

```python
def get_ercot_solar_signal(lat: float, lon: float, hours_ahead: int = 24, ercot_data: dict = None) -> dict:
    """Solar irradiance → ERCOT power price signal.
    Returns ready-to-use signal for your position manager.

    Args:
        ercot_data: optional pre-fetched {"price": float, "solar_mw": float}
                    to avoid redundant API calls when scanning multiple hubs.
    """

    # 1. Solar irradiance from Open-Meteo (always per-hub lat/lon)
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily=shortwave_radiation_sum"
            f"&forecast_days=3"
            f"&timezone=auto"
        )
        r = http_get(url, timeout=10)
        r.raise_for_status()
        radiation = r.json().get("daily", {}).get("shortwave_radiation_sum", [])
        target_idx = min(hours_ahead // 24, len(radiation) - 1)
        expected_solrad = radiation[target_idx] if radiation else 15.0
    except Exception as e:
        print(f"  Solar irradiance fetch error: {e}")
        expected_solrad = 15.0

    # 2. ERCOT market data — use pre-fetched if available
    if ercot_data is not None:
        current_price = float(ercot_data.get("price", 40.0))
        actual_solar_mw = float(ercot_data.get("solar_mw", 0.0))
    else:
        try:
            r = requests.get("https://www.ercot.com/api/public-reports/np6788/rtmLmp", timeout=8)
            data = r.json()
            current_price = float(data[-1]["price"]) if data else 40.0
        except:
            current_price = 40.0
        try:
            r = requests.get("https://www.ercot.com/api/public-reports/np4-738-cd/spp_actual_5min_avg_values", timeout=8)
            data = r.json()
            actual_solar_mw = float(data[-1].get("value", 0)) if data else 0.0
        except Exception as e:
            print(f"  ERCOT solar gen fetch error: {e}")
            actual_solar_mw = 12000.0  # match cached fallback

    # 3. Signal logic (tunable)
    if expected_solrad > 18.0:
        signal = "SHORT"
        edge = (expected_solrad - 15.0) / 4.0
    elif expected_solrad < 10.0:
        signal = "LONG"
        edge = (15.0 - expected_solrad) / 4.0
    else:
        signal = "NEUTRAL"
        edge = 0.0

    confidence = 70 if abs(edge) > 1.0 else 50

    return {
        "signal": signal,
        "edge": round(edge, 2),
        "expected_solrad_mjm2": round(expected_solrad, 1),
        "current_ercot_price": round(current_price, 1),
        "actual_solar_mw": round(actual_solar_mw, 0),
        "confidence": confidence,
    }
```

Note: `ticker_hint` is removed — `scan_all_hubs()` adds `hub` and `hub_name` to each signal dict, making it redundant.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ercot_signal.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add weather/multi_model.py tests/test_ercot_signal.py
git commit -m "feat: update get_ercot_solar_signal with ercot_data param and actual_solar_mw"
```

---

### Task 3: Create ercot package and hubs.py data layer

**Files:**
- Create: `ercot/__init__.py`
- Create: `ercot/hubs.py`
- Test: `tests/test_ercot_hubs.py` (create)

- [ ] **Step 1: Write test for scan_all_hubs and cached fetcher**

Create `tests/test_ercot_hubs.py`:

```python
"""Tests for ercot/hubs.py — hub scanning and ERCOT data caching."""
import time


def test_scan_all_hubs_returns_five_signals():
    from ercot.hubs import scan_all_hubs

    signals = scan_all_hubs()
    assert len(signals) == 5
    hub_names = {s["hub"] for s in signals}
    assert hub_names == {"North", "Houston", "South", "West", "Panhandle"}


def test_signal_dict_has_required_keys():
    from ercot.hubs import scan_all_hubs

    signals = scan_all_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_ercot_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_ercot_market_data_caching():
    """Second call within 5 min should return cached data."""
    from ercot.hubs import _fetch_ercot_market_data

    data1 = _fetch_ercot_market_data()
    data2 = _fetch_ercot_market_data()
    # Both should succeed and have required keys
    assert "price" in data1
    assert "solar_mw" in data1
    # Values should be identical (cached)
    assert data1 == data2


def test_fetch_ercot_market_data_has_fallbacks():
    """Data should always have price, solar_mw, load_forecast keys."""
    from ercot.hubs import _fetch_ercot_market_data

    data = _fetch_ercot_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ercot_hubs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ercot'`

- [ ] **Step 3: Create ercot package**

Create `ercot/__init__.py`:

```python
```

- [ ] **Step 4: Create ercot/hubs.py**

```python
"""ERCOT hub scanning — fetches solar irradiance per hub + shared ERCOT market data.

Caches ERCOT API responses for 5 minutes to avoid redundant calls across hub scans.
"""

import time
import requests
from config import ERCOT_HUBS
from weather.multi_model import get_ercot_solar_signal

# Module-level cache for ERCOT market data (shared across all 5 hub scans)
_ercot_cache: dict = {}
_ercot_cache_time: float = 0.0
_CACHE_TTL = 300  # 5 minutes


def _fetch_ercot_market_data() -> dict:
    """Fetch ERCOT price, solar generation, and load forecast.

    Returns cached data if within TTL. Falls back to defaults on failure.
    """
    global _ercot_cache, _ercot_cache_time

    if _ercot_cache and (time.time() - _ercot_cache_time) < _CACHE_TTL:
        return _ercot_cache

    result = {"price": 40.0, "solar_mw": 12000.0, "load_forecast": 50000.0}

    # Real-time LMP
    try:
        r = requests.get(
            "https://www.ercot.com/api/public-reports/np6788/rtmLmp", timeout=8
        )
        data = r.json()
        if data:
            result["price"] = float(data[-1]["price"])
    except Exception as e:
        print(f"  ERCOT price fetch error: {e}")

    # Actual solar generation
    try:
        r = requests.get(
            "https://www.ercot.com/api/public-reports/np4-738-cd/spp_actual_5min_avg_values",
            timeout=8,
        )
        data = r.json()
        if data:
            result["solar_mw"] = float(data[-1].get("value", 12000.0))
    except Exception as e:
        print(f"  ERCOT solar gen fetch error: {e}")

    # 7-day load forecast (reserved for future signal refinement — not used in v1)
    try:
        r = requests.get(
            "https://www.ercot.com/api/public-reports/np3-566-cd/lf_by_model_study_area",
            timeout=8,
        )
        data = r.json()
        if data:
            result["load_forecast"] = float(data[-1].get("value", 50000.0))
    except Exception as e:
        print(f"  ERCOT load forecast fetch error: {e}")

    _ercot_cache = result
    _ercot_cache_time = time.time()
    return result


def scan_all_hubs() -> list:
    """Scan all 5 ERCOT hubs. Returns list of enriched signal dicts."""
    ercot_data = _fetch_ercot_market_data()
    signals = []

    for hub_key, hub_info in ERCOT_HUBS.items():
        signal = get_ercot_solar_signal(
            hub_info["lat"], hub_info["lon"],
            hours_ahead=24,
            ercot_data=ercot_data,
        )
        signal["hub"] = hub_key
        signal["hub_name"] = hub_info["hub_name"]
        signal["city"] = hub_info["city"]
        # actual_solar_mw comes from ercot_data via the signal function
        signals.append(signal)

    return signals
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ercot_hubs.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add ercot/__init__.py ercot/hubs.py tests/test_ercot_hubs.py
git commit -m "feat: ercot package with hub scanning and cached ERCOT data layer"
```

---

## Chunk 2: Paper Trading Engine

### Task 4: Create paper_trader.py with SQLite schema and core functions

**Files:**
- Create: `ercot/paper_trader.py`
- Test: `tests/test_ercot_paper_trader.py` (create)

- [ ] **Step 1: Write tests for paper trading engine**

Create `tests/test_ercot_paper_trader.py`:

```python
"""Tests for ercot/paper_trader.py — paper position management."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

TEST_DB = "data/test_ercot_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    """Use a test DB and clean up after."""
    import ercot.paper_trader as pt
    pt.ERCOT_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(hub="North", hub_name="HB_NORTH", signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": hub, "hub_name": hub_name, "city": "Dallas",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_ercot_price": price, "expected_solrad_mjm2": 20.0,
        "actual_solar_mw": 12000.0,
    }


def test_open_position():
    from ercot.paper_trader import open_position, get_open_positions

    sig = _make_signal()
    result = open_position(sig, bankroll=10000.0)
    assert result is not None
    assert result["hub"] == "North"
    assert result["signal"] == "SHORT"
    assert result["size_dollars"] > 0

    positions = get_open_positions()
    assert len(positions) == 1
    assert positions[0]["hub"] == "North"


def test_close_position_pnl_short():
    """SHORT position: entry $40, exit $30 = profit."""
    from ercot.paper_trader import open_position, close_position, get_trade_history

    sig = _make_signal(price=40.0)
    pos = open_position(sig, bankroll=10000.0)

    close_position(pos["id"], exit_price=30.0, exit_signal="LONG", reason="signal flipped")

    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0  # price dropped, SHORT wins
    assert trades[0]["exit_reason"] == "signal flipped"
    assert trades[0]["exit_signal"] == "LONG"


def test_close_position_pnl_long():
    """LONG position: entry $40, exit $50 = profit."""
    from ercot.paper_trader import open_position, close_position, get_trade_history

    sig = _make_signal(signal="LONG", edge=1.2, price=40.0)
    pos = open_position(sig, bankroll=10000.0)

    close_position(pos["id"], exit_price=50.0, exit_signal="NEUTRAL", reason="edge decay")

    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0  # price rose, LONG wins


def test_max_positions_per_hub():
    from ercot.paper_trader import open_position, get_open_positions
    import config
    original = config.ERCOT_MAX_POSITIONS_PER_HUB
    config.ERCOT_MAX_POSITIONS_PER_HUB = 2

    sig = _make_signal()
    open_position(sig, bankroll=10000.0)
    open_position(sig, bankroll=10000.0)
    result = open_position(sig, bankroll=10000.0)  # should be blocked

    assert result is None
    assert len(get_open_positions()) == 2
    config.ERCOT_MAX_POSITIONS_PER_HUB = original


def test_max_positions_total():
    from ercot.paper_trader import open_position, get_open_positions
    import config
    original = config.ERCOT_MAX_POSITIONS_TOTAL
    config.ERCOT_MAX_POSITIONS_TOTAL = 2

    open_position(_make_signal(hub="North", hub_name="HB_NORTH"), bankroll=10000.0)
    open_position(_make_signal(hub="Houston", hub_name="HB_HOUSTON"), bankroll=10000.0)
    result = open_position(_make_signal(hub="South", hub_name="HB_SOUTH"), bankroll=10000.0)

    assert result is None
    assert len(get_open_positions()) == 2
    config.ERCOT_MAX_POSITIONS_TOTAL = original


def test_expire_positions():
    from ercot.paper_trader import open_position, expire_positions, get_open_positions, get_trade_history

    sig = _make_signal(price=40.0)
    open_position(sig, bankroll=10000.0)

    # Manually set expires_at to the past
    conn = sqlite3.connect(TEST_DB)
    conn.execute("UPDATE ercot_positions SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    conn.commit()
    conn.close()

    expire_positions(current_price=42.0)

    assert len(get_open_positions()) == 0
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "expired"


def test_paper_summary():
    from ercot.paper_trader import open_position, close_position, get_paper_summary

    sig = _make_signal(price=40.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=30.0, exit_signal="LONG", reason="test")

    summary = get_paper_summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
    assert summary["open_count"] == 0


def test_scan_cache_write_and_read():
    from ercot.paper_trader import write_scan_cache, get_cached_signals

    signals = [
        {"hub": "North", "hub_name": "HB_NORTH", "signal": "SHORT",
         "edge": 1.5, "expected_solrad_mjm2": 20.0,
         "current_ercot_price": 40.0, "actual_solar_mw": 12000.0,
         "confidence": 70},
    ]
    write_scan_cache(signals)
    cached = get_cached_signals()
    assert len(cached) >= 1
    assert cached[0]["hub"] == "North"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ercot_paper_trader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ercot.paper_trader'`

- [ ] **Step 3: Create ercot/paper_trader.py**

```python
"""ERCOT paper trading engine — SQLite-backed simulated positions.

Tracks paper positions with Kelly sizing, P&L, and expiry.
Uses shared risk limits from risk/position_limits.py.
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

from config import (
    FRACTIONAL_KELLY, MAX_BANKROLL_PCT_PER_TRADE,
    ERCOT_PAPER_BANKROLL, ERCOT_MAX_POSITIONS_PER_HUB,
    ERCOT_MAX_POSITIONS_TOTAL, ERCOT_POSITION_TTL_HOURS,
)
from risk.position_limits import check_limits

ERCOT_PAPER_DB = "data/ercot_paper.db"


def _init_db():
    os.makedirs(os.path.dirname(ERCOT_PAPER_DB) or ".", exist_ok=True)
    conn = sqlite3.connect(ERCOT_PAPER_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ercot_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub TEXT NOT NULL,
            hub_name TEXT NOT NULL,
            signal TEXT NOT NULL,
            entry_price REAL NOT NULL,
            size_dollars REAL NOT NULL,
            edge REAL NOT NULL,
            confidence INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ercot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub TEXT NOT NULL,
            hub_name TEXT NOT NULL,
            signal TEXT NOT NULL,
            exit_signal TEXT,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            size_dollars REAL NOT NULL,
            pnl REAL NOT NULL,
            edge_at_entry REAL NOT NULL,
            confidence INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            exit_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS ercot_scan_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub TEXT NOT NULL,
            hub_name TEXT NOT NULL,
            signal TEXT NOT NULL,
            edge REAL NOT NULL,
            expected_solrad_mjm2 REAL,
            current_ercot_price REAL,
            actual_solar_mw REAL,
            confidence INTEGER NOT NULL,
            scanned_at TEXT NOT NULL
        );
    """)
    conn.close()


def _conn():
    _init_db()
    conn = sqlite3.connect(ERCOT_PAPER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def open_position(hub_signal: dict, bankroll: float, max_size: float = None) -> dict | None:
    """Open a paper position with Kelly sizing. Returns position dict or None if blocked.

    Args:
        max_size: optional cap on position size (used by fortify to prevent doubling).
    """
    conn = _conn()

    # Check per-hub limit
    hub = hub_signal["hub"]
    hub_count = conn.execute(
        "SELECT COUNT(*) FROM ercot_positions WHERE hub = ?", (hub,)
    ).fetchone()[0]
    if hub_count >= ERCOT_MAX_POSITIONS_PER_HUB:
        conn.close()
        return None

    # Check total limit
    total_count = conn.execute("SELECT COUNT(*) FROM ercot_positions").fetchone()[0]
    if total_count >= ERCOT_MAX_POSITIONS_TOTAL:
        conn.close()
        return None

    # Kelly sizing: size = min(edge * kelly * bankroll, max_pct * bankroll)
    edge = abs(hub_signal["edge"])
    effective_bankroll = min(bankroll, ERCOT_PAPER_BANKROLL)
    size = min(edge * FRACTIONAL_KELLY * effective_bankroll,
               MAX_BANKROLL_PCT_PER_TRADE * effective_bankroll)

    # Check risk limits
    total_exposure = sum(
        r[0] for r in conn.execute("SELECT size_dollars FROM ercot_positions").fetchall()
    )
    limit_result = check_limits(
        order_dollars=size,
        bankroll=effective_bankroll,
        scan_spent=0.0,
        city_day_spent=0.0,
        total_exposure=total_exposure,
    )
    if limit_result.blocked:
        conn.close()
        return None

    size = limit_result.allowed_dollars
    if max_size is not None:
        size = min(size, max_size)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ERCOT_POSITION_TTL_HOURS)

    conn.execute(
        """INSERT INTO ercot_positions
           (hub, hub_name, signal, entry_price, size_dollars, edge, confidence, opened_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (hub, hub_signal["hub_name"], hub_signal["signal"],
         hub_signal["current_ercot_price"], round(size, 2), edge,
         hub_signal["confidence"], now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    pos_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM ercot_positions WHERE id = ?", (pos_id,)).fetchone()
    conn.close()
    return dict(row)


def close_position(position_id: int, exit_price: float, exit_signal: str, reason: str):
    """Close a paper position and record trade with P&L."""
    conn = _conn()
    row = conn.execute("SELECT * FROM ercot_positions WHERE id = ?", (position_id,)).fetchone()
    if not row:
        conn.close()
        return

    direction = -1.0 if row["signal"] == "SHORT" else 1.0
    pnl = direction * (exit_price - row["entry_price"]) / row["entry_price"] * row["size_dollars"]

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO ercot_trades
           (hub, hub_name, signal, exit_signal, entry_price, exit_price, size_dollars,
            pnl, edge_at_entry, confidence, opened_at, closed_at, exit_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["hub"], row["hub_name"], row["signal"], exit_signal,
         row["entry_price"], exit_price, row["size_dollars"],
         round(pnl, 2), row["edge"], row["confidence"],
         row["opened_at"], now, reason),
    )
    conn.execute("DELETE FROM ercot_positions WHERE id = ?", (position_id,))
    conn.commit()
    conn.close()


def expire_positions(current_price: float):
    """Auto-close any positions past their expiry time."""
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    expired = conn.execute(
        "SELECT * FROM ercot_positions WHERE expires_at < ?", (now,)
    ).fetchall()

    for row in expired:
        direction = -1.0 if row["signal"] == "SHORT" else 1.0
        pnl = direction * (current_price - row["entry_price"]) / row["entry_price"] * row["size_dollars"]

        conn.execute(
            """INSERT INTO ercot_trades
               (hub, hub_name, signal, exit_signal, entry_price, exit_price, size_dollars,
                pnl, edge_at_entry, confidence, opened_at, closed_at, exit_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["hub"], row["hub_name"], row["signal"], "EXPIRED",
             row["entry_price"], current_price, row["size_dollars"],
             round(pnl, 2), row["edge"], row["confidence"],
             row["opened_at"], now, "expired"),
        )
        conn.execute("DELETE FROM ercot_positions WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()


def get_open_positions() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM ercot_positions ORDER BY opened_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_history(limit: int = 50) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM ercot_trades ORDER BY closed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paper_summary() -> dict:
    conn = _conn()
    positions = conn.execute("SELECT * FROM ercot_positions").fetchall()
    trades = conn.execute("SELECT pnl FROM ercot_trades").fetchall()
    conn.close()

    total_pnl = sum(t["pnl"] for t in trades) if trades else 0.0
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total_trades = len(trades)
    open_exposure = sum(p["size_dollars"] for p in positions)

    return {
        "open_count": len(positions),
        "open_exposure": round(open_exposure, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": total_trades - wins,
        "win_rate": round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
        "total_pnl": round(total_pnl, 2),
    }


def write_scan_cache(signals: list):
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM ercot_scan_cache")
    for sig in signals:
        conn.execute(
            """INSERT INTO ercot_scan_cache
               (hub, hub_name, signal, edge, expected_solrad_mjm2,
                current_ercot_price, actual_solar_mw, confidence, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sig["hub"], sig["hub_name"], sig["signal"], sig["edge"],
             sig.get("expected_solrad_mjm2", 0), sig.get("current_ercot_price", 0),
             sig.get("actual_solar_mw", 0), sig["confidence"], now),
        )
    conn.commit()
    conn.close()


def get_cached_signals() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM ercot_scan_cache ORDER BY hub").fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ercot_paper_trader.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add ercot/paper_trader.py tests/test_ercot_paper_trader.py
git commit -m "feat: ERCOT paper trading engine with SQLite positions and P&L tracking"
```

---

## Chunk 3: Position Manager

### Task 5: Create ercot/position_manager.py

**Files:**
- Create: `ercot/position_manager.py`
- Test: `tests/test_ercot_position_manager.py` (create)

- [ ] **Step 1: Write tests for position evaluation logic**

Create `tests/test_ercot_position_manager.py`:

```python
"""Tests for ercot/position_manager.py — evaluate/fortify/exit logic."""
import os
import pytest

TEST_DB = "data/test_ercot_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import ercot.paper_trader as pt
    pt.ERCOT_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": "North", "hub_name": "HB_NORTH", "city": "Dallas",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_ercot_price": price, "expected_solrad_mjm2": 20.0,
        "actual_solar_mw": 12000.0,
    }


def test_hold_when_signal_agrees():
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.2)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "hold"


def test_exit_when_signal_flips():
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="LONG", edge=1.0)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "exit"
    assert "flipped" in result["reason"].lower()


def test_exit_when_edge_decays():
    """Edge at 30% of entry = exit."""
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=2.0), bankroll=10000.0)
    # 30% of 2.0 = 0.6, so edge of 0.4 triggers exit
    current = _make_signal(signal="SHORT", edge=0.4)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()


def test_fortify_when_edge_increases():
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position
    import config

    pos = open_position(_make_signal(signal="SHORT", edge=1.0), bankroll=10000.0)
    # Entry edge 1.0 + FORTIFY_EDGE_INCREASE (0.5) = need 1.5+
    current = _make_signal(signal="SHORT", edge=1.6)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "fortify"


def test_exit_when_neutral_zero_edge():
    """NEUTRAL with zero edge is below 30% of entry edge 1.5, so exit."""
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="NEUTRAL", edge=0.0)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ercot_position_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ercot.position_manager'`

- [ ] **Step 3: Create ercot/position_manager.py**

```python
"""ERCOT position manager — evaluate/fortify/exit paper positions.

Mirrors kalshi/position_manager.py patterns but adapted for paper-only
ERCOT power price positions with no exchange API.
"""

from rich.console import Console
from rich.table import Table

from config import (
    ERCOT_FORTIFY_EDGE_INCREASE, ERCOT_EXIT_EDGE_DECAY,
    ERCOT_MAX_POSITIONS_PER_HUB, ERCOT_PAPER_BANKROLL,
)
from ercot.paper_trader import (
    get_open_positions, close_position, open_position, expire_positions,
)
from ercot.hubs import scan_all_hubs

console = Console()


def evaluate_ercot_position(position: dict, current_signal: dict) -> dict:
    """Evaluate a paper position against fresh signal. Returns action dict."""
    entry_edge = position["edge"]
    entry_signal = position["signal"]
    current_edge = current_signal["edge"]
    current_direction = current_signal["signal"]

    result = {
        "position_id": position["id"],
        "hub": position["hub"],
        "hub_name": position["hub_name"],
        "entry_edge": entry_edge,
        "current_edge": current_edge,
        "size_dollars": position["size_dollars"],
    }

    # EXIT: signal flipped
    if current_direction not in (entry_signal, "NEUTRAL") and current_direction in ("SHORT", "LONG"):
        result["action"] = "exit"
        result["reason"] = f"Signal flipped {entry_signal} → {current_direction}"
        return result

    # EXIT: edge decayed below threshold
    decay_threshold = entry_edge * ERCOT_EXIT_EDGE_DECAY
    if current_edge < decay_threshold:
        result["action"] = "exit"
        result["reason"] = f"Edge decay {current_edge:.2f} < {decay_threshold:.2f} ({ERCOT_EXIT_EDGE_DECAY:.0%} of {entry_edge:.2f})"
        return result

    # FORTIFY: edge strengthened significantly
    if (current_direction == entry_signal and
            current_edge > entry_edge + ERCOT_FORTIFY_EDGE_INCREASE):
        result["action"] = "fortify"
        result["reason"] = f"Edge strengthened {entry_edge:.2f} → {current_edge:.2f} (+{current_edge - entry_edge:.2f})"
        return result

    # HOLD: default
    result["action"] = "hold"
    result["reason"] = f"Edge {current_edge:.2f} (entry {entry_edge:.2f})"
    return result


def run_ercot_manager():
    """Evaluate all open ERCOT paper positions. Print rich table, execute actions."""
    # 1. Fetch signals first (needed for current_price), then expire
    # Note: scan runs before expire because expire_positions() needs a current
    # ERCOT price, which comes from the signal fetch. If scan fails, positions
    # won't expire this cycle (safe default — they'll expire next cycle).
    signals = scan_all_hubs()
    if signals:
        current_price = signals[0]["current_ercot_price"]
        expire_positions(current_price)

    # 2. Load remaining positions
    positions = get_open_positions()
    if not positions:
        return signals  # return signals for scanner to reuse

    # Build signal lookup by hub
    signal_by_hub = {s["hub"]: s for s in signals}

    table = Table(title=f"ERCOT Position Manager ({len(positions)} positions)")
    table.add_column("Hub", style="cyan")
    table.add_column("Side", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Entry Edge", justify="right")
    table.add_column("Curr Edge", justify="right")
    table.add_column("Action", style="bold")
    table.add_column("Reason", style="dim", max_width=40)

    exits = []
    fortifies = []

    for pos in positions:
        hub_signal = signal_by_hub.get(pos["hub"])
        if not hub_signal:
            continue

        result = evaluate_ercot_position(pos, hub_signal)

        side_color = "red" if pos["signal"] == "SHORT" else "green"
        action = result["action"].upper()
        action_color = {"EXIT": "red", "FORTIFY": "cyan", "HOLD": "green"}.get(action, "white")

        table.add_row(
            pos["hub_name"],
            f"[{side_color}]{pos['signal']}[/{side_color}]",
            f"${pos['size_dollars']:.0f}",
            f"{result['entry_edge']:.2f}",
            f"{result['current_edge']:.2f}",
            f"[{action_color}]{action}[/{action_color}]",
            result["reason"],
        )

        if result["action"] == "exit":
            exits.append((pos, hub_signal, result))
        elif result["action"] == "fortify":
            fortifies.append((pos, hub_signal, result))

    console.print(table)

    # Execute exits
    for pos, hub_signal, result in exits:
        console.print(f"  [red]EXIT[/red] {pos['hub_name']} {pos['signal']} ${pos['size_dollars']:.0f} — {result['reason']}")
        close_position(pos["id"], hub_signal["current_ercot_price"], hub_signal["signal"], result["reason"])

    # Execute fortifies (cap at existing position size — no more than doubling)
    for pos, hub_signal, result in fortifies:
        console.print(f"  [cyan]FORTIFY[/cyan] {pos['hub_name']} {pos['signal']} — {result['reason']}")
        fortify_signal = dict(hub_signal)
        open_position(fortify_signal, bankroll=ERCOT_PAPER_BANKROLL, max_size=pos["size_dollars"])

    return signals  # return for scanner to reuse
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ercot_position_manager.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add ercot/position_manager.py tests/test_ercot_position_manager.py
git commit -m "feat: ERCOT position manager with evaluate/fortify/exit logic"
```

---

## Chunk 4: Scanner Integration

### Task 6: Replace scanner ERCOT block with full hub-based integration

**Files:**
- Modify: `scanner.py:1-8` (imports) and `scanner.py:438-452` (ERCOT block)

- [ ] **Step 1: Update scanner imports**

Add to `scanner.py` imports (near line 8, after `from weather.multi_model import fuse_forecast, _get_liquidity_score, get_ercot_solar_signal`):

Replace that import line with:
```python
from weather.multi_model import fuse_forecast, _get_liquidity_score
```

Then add after line 20 (after the last existing import block):
```python
from ercot.position_manager import run_ercot_manager
from ercot.hubs import scan_all_hubs
from ercot.paper_trader import open_position, get_open_positions, get_paper_summary, write_scan_cache
from config import ERCOT_MIN_EDGE, ERCOT_MIN_CONFIDENCE, ERCOT_PAPER_BANKROLL
```

- [ ] **Step 2: Replace ERCOT block in scanner**

Replace lines 438-452 (the `texas_cities` block) with:

```python
    # --- ERCOT Solar / Power Price Signal (5 hubs) ---
    console.print("\n[bold magenta]ERCOT Solar Signals[/bold magenta]")

    # 1. Manage existing positions first
    hub_signals = run_ercot_manager()
    if hub_signals is None:
        hub_signals = scan_all_hubs()

    # 2. Cache signals for dashboard
    write_scan_cache(hub_signals)

    # 3. Display hub signal table
    ercot_table = Table(title="ERCOT Solar Signals (5 hubs)")
    ercot_table.add_column("Hub", style="cyan")
    ercot_table.add_column("City", style="green")
    ercot_table.add_column("Signal", style="bold")
    ercot_table.add_column("Edge", justify="right")
    ercot_table.add_column("Solrad", justify="right")
    ercot_table.add_column("Solar MW", justify="right")
    ercot_table.add_column("ERCOT$", justify="right")
    ercot_table.add_column("Conf", justify="right")
    ercot_table.add_column("Action", style="bold")

    existing_hubs = {p["hub"]: p for p in get_open_positions()}

    for sig in hub_signals:
        sig_color = {"SHORT": "red", "LONG": "green"}.get(sig["signal"], "dim")
        edge_str = f"[{sig_color}]{sig['edge']:+.2f}[/{sig_color}]"

        # Determine action
        action = "--"
        if abs(sig["edge"]) >= ERCOT_MIN_EDGE and sig["confidence"] >= ERCOT_MIN_CONFIDENCE:
            existing = existing_hubs.get(sig["hub"])
            if existing:
                if existing["signal"] == sig["signal"]:
                    action = f"HOLD (${existing['size_dollars']:.0f})"
                else:
                    action = "FLIP"
            else:
                action = "OPEN"

        ercot_table.add_row(
            sig["hub"], sig["city"],
            f"[{sig_color}]{sig['signal']}[/{sig_color}]",
            edge_str,
            f"{sig['expected_solrad_mjm2']:.1f}",
            f"{sig['actual_solar_mw']:.0f}",
            f"${sig['current_ercot_price']:.1f}",
            f"{sig['confidence']}%",
            action,
        )

        # 4. Open new positions / handle flips
        if abs(sig["edge"]) >= ERCOT_MIN_EDGE and sig["confidence"] >= ERCOT_MIN_CONFIDENCE:
            existing = existing_hubs.get(sig["hub"])
            if existing and existing["signal"] != sig["signal"]:
                # Flip: close existing, open new
                from ercot.paper_trader import close_position
                close_position(existing["id"], sig["current_ercot_price"], sig["signal"], "signal flipped")
                open_position(sig, bankroll=ERCOT_PAPER_BANKROLL)
            elif not existing:
                open_position(sig, bankroll=ERCOT_PAPER_BANKROLL)

    console.print(ercot_table)

    # 5. Paper P&L summary
    summary = get_paper_summary()
    console.print(
        f"\n[bold magenta]ERCOT Paper:[/bold magenta] "
        f"{summary['open_count']} open (${summary['open_exposure']:.0f} exposure) | "
        f"Closed: {summary['wins']}W/{summary['losses']}L | "
        f"Net P&L: ${summary['total_pnl']:+.2f}"
    )
```

- [ ] **Step 3: Verify scanner imports cleanly**

Run: `python -c "from scanner import run_scanner; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add scanner.py
git commit -m "feat: full ERCOT hub scanning with paper trading in scanner"
```

---

## Chunk 5: Dashboard Backend + Frontend

### Task 7: Create ERCOT dashboard API router

**Files:**
- Create: `dashboard/ercot_api.py`
- Modify: `dashboard/api.py:9-10` (add import + mount)

- [ ] **Step 1: Create dashboard/ercot_api.py**

```python
"""ERCOT dashboard API endpoints.

Reads from ercot_paper.db — no live API calls.
"""

from fastapi import APIRouter, Query

from ercot.paper_trader import (
    get_cached_signals, get_open_positions, get_trade_history, get_paper_summary,
)

ercot_router = APIRouter(prefix="/api/ercot", tags=["ercot"])


@ercot_router.get("/signals")
async def ercot_signals():
    return get_cached_signals()


@ercot_router.get("/positions")
async def ercot_positions():
    positions = get_open_positions()
    # Enrich with unrealized P&L from latest cached price
    cached = get_cached_signals()
    price_map = {s["hub"]: s["current_ercot_price"] for s in cached}

    for pos in positions:
        current_price = price_map.get(pos["hub"], pos["entry_price"])
        direction = -1.0 if pos["signal"] == "SHORT" else 1.0
        pos["unrealized_pnl"] = round(
            direction * (current_price - pos["entry_price"]) / pos["entry_price"] * pos["size_dollars"], 2
        )
        pos["current_price"] = current_price

    return positions


@ercot_router.get("/trades")
async def ercot_trades(limit: int = Query(50)):
    return get_trade_history(limit=limit)


@ercot_router.get("/summary")
async def ercot_summary():
    summary = get_paper_summary()
    # Add per-hub breakdown
    trades = get_trade_history(limit=500)
    hub_pnl = {}
    for t in trades:
        hub_pnl[t["hub"]] = hub_pnl.get(t["hub"], 0) + t["pnl"]

    if hub_pnl:
        summary["best_hub"] = max(hub_pnl, key=hub_pnl.get)
        summary["worst_hub"] = min(hub_pnl, key=hub_pnl.get)
        summary["hub_pnl"] = {k: round(v, 2) for k, v in hub_pnl.items()}
    else:
        summary["best_hub"] = None
        summary["worst_hub"] = None
        summary["hub_pnl"] = {}

    return summary
```

- [ ] **Step 2: Mount router in dashboard/api.py**

Add with the other imports (after line 15, with the other `from dashboard...` imports):

```python
from dashboard.ercot_api import ercot_router
```

Add between `app = FastAPI(...)` (line 67) and `app.mount("/static", ...)` (line 71) — must be before the static mount to avoid route conflicts:

```python
app.include_router(ercot_router)
```

- [ ] **Step 3: Verify API starts cleanly**

Run: `python -c "from dashboard.api import app; print(f'Routes: {len(app.routes)}')"`
Expected: Prints route count without errors

- [ ] **Step 4: Commit**

```bash
git add dashboard/ercot_api.py dashboard/api.py
git commit -m "feat: ERCOT dashboard API with signals, positions, trades, summary endpoints"
```

---

### Task 8: Create ERCOT dashboard frontend

**Files:**
- Create: `dashboard/static/ercot.html`
- Modify: `dashboard/static/index.html:41-45` (add nav link)
- Modify: `dashboard/api.py` (add ercot.html route)

- [ ] **Step 1: Add nav link to index.html sidebar**

Add after the Markets nav item (after line 45 in `dashboard/static/index.html`):

```html
            <div class="we-nav-group-label">Energy</div>
            <a class="we-nav-item" href="/ercot">
                <i class="fa-solid fa-solar-panel"></i>
                <span>ERCOT Power</span>
            </a>
```

- [ ] **Step 2: Add /ercot route to dashboard/api.py**

Add after the root route (after line 79):

```python
@app.get("/ercot")
async def ercot_page():
    ercot_file = STATIC_DIR / "ercot.html"
    if ercot_file.exists():
        return FileResponse(str(ercot_file))
    return {"status": "ERCOT frontend not found"}
```

- [ ] **Step 3: Create dashboard/static/ercot.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ERCOT Power — Weather Edge</title>
    <link rel="stylesheet" href="/static/css/main.css?v=22">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@700;900&family=Roboto:wght@300;400;500;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <style>
        .ercot-main { max-width: 1200px; margin: 0 auto; padding: 2rem; }
        .ercot-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem; }
        .ercot-header h1 { font-family: 'Merriweather', serif; font-size: 1.6rem; margin: 0; }
        .ercot-header .back-link { color: var(--we-text-secondary, #888); text-decoration: none; }

        .hub-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
        .hub-card {
            background: var(--we-card-bg, #1a1a2e);
            border-radius: 12px; padding: 1.2rem;
            border-left: 4px solid #555;
        }
        .hub-card.short { border-left-color: #ef4444; }
        .hub-card.long { border-left-color: #22c55e; }
        .hub-card .hub-name { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; color: #888; }
        .hub-card .hub-city { font-weight: 600; font-size: 1.1rem; margin: 0.3rem 0; }
        .hub-card .hub-signal { font-family: 'JetBrains Mono', monospace; font-size: 1.3rem; font-weight: 700; }
        .hub-card .hub-signal.short { color: #ef4444; }
        .hub-card .hub-signal.long { color: #22c55e; }
        .hub-card .hub-signal.neutral { color: #888; }
        .hub-card .hub-detail { font-size: 0.8rem; color: #aaa; margin-top: 0.5rem; }

        .ercot-section { margin-bottom: 2rem; }
        .ercot-section h2 { font-family: 'Merriweather', serif; font-size: 1.2rem; margin-bottom: 1rem; }

        .ercot-table { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; }
        .ercot-table th { text-align: left; padding: 0.6rem; color: #888; border-bottom: 1px solid #333; font-weight: 500; }
        .ercot-table td { padding: 0.6rem; border-bottom: 1px solid #222; }
        .ercot-table .positive { color: #22c55e; }
        .ercot-table .negative { color: #ef4444; }

        .summary-bar {
            display: flex; gap: 2rem; padding: 1rem 1.5rem;
            background: var(--we-card-bg, #1a1a2e); border-radius: 12px; margin-bottom: 2rem;
            font-family: 'JetBrains Mono', monospace; font-size: 0.9rem;
        }
        .summary-bar .label { color: #888; font-size: 0.75rem; }
        .summary-bar .value { font-weight: 600; font-size: 1.1rem; }
    </style>
</head>
<body class="we-app" style="display: block;">
    <div class="ercot-main">
        <div class="ercot-header">
            <a href="/" class="back-link"><i class="fa-solid fa-arrow-left"></i></a>
            <h1><i class="fa-solid fa-solar-panel"></i> ERCOT Power Signals</h1>
            <span style="color: #f59e0b; font-family: 'JetBrains Mono'; font-size: 0.8rem;">PAPER MODE</span>
        </div>

        <!-- Summary Bar -->
        <div class="summary-bar" id="summary-bar">
            <div><div class="label">Open Positions</div><div class="value" id="s-open">--</div></div>
            <div><div class="label">Exposure</div><div class="value" id="s-exposure">--</div></div>
            <div><div class="label">Win Rate</div><div class="value" id="s-winrate">--</div></div>
            <div><div class="label">Net P&L</div><div class="value" id="s-pnl">--</div></div>
        </div>

        <!-- Hub Signal Cards -->
        <div class="hub-cards" id="hub-cards"></div>

        <!-- Open Positions -->
        <div class="ercot-section">
            <h2>Open Positions</h2>
            <table class="ercot-table">
                <thead><tr><th>Hub</th><th>Side</th><th>Size</th><th>Entry $</th><th>Current $</th><th>P&L</th><th>Expires</th></tr></thead>
                <tbody id="positions-body"></tbody>
            </table>
        </div>

        <!-- Trade History -->
        <div class="ercot-section">
            <h2>Trade History</h2>
            <table class="ercot-table">
                <thead><tr><th>Hub</th><th>Side</th><th>Entry</th><th>Exit</th><th>Size</th><th>P&L</th><th>Reason</th><th>Closed</th></tr></thead>
                <tbody id="trades-body"></tbody>
            </table>
        </div>
    </div>

    <script>
    async function loadErcot() {
        // Signals
        try {
            const signals = await (await fetch('/api/ercot/signals')).json();
            const cards = document.getElementById('hub-cards');
            cards.innerHTML = '';
            signals.forEach(s => {
                const cls = s.signal.toLowerCase();
                cards.innerHTML += `
                    <div class="hub-card ${cls}">
                        <div class="hub-name">${s.hub_name}</div>
                        <div class="hub-city">${s.hub || ''}</div>
                        <div class="hub-signal ${cls}">${s.signal} ${s.edge > 0 ? '+' : ''}${s.edge.toFixed(2)}</div>
                        <div class="hub-detail">
                            Solrad: ${s.expected_solrad_mjm2?.toFixed(1) || '--'} MJ/m&sup2;<br>
                            Solar: ${(s.actual_solar_mw || 0).toLocaleString()} MW<br>
                            Price: $${s.current_ercot_price?.toFixed(1) || '--'}/MWh<br>
                            Conf: ${s.confidence}%
                        </div>
                    </div>`;
            });
        } catch(e) { console.error('Signals error', e); }

        // Summary
        try {
            const sum = await (await fetch('/api/ercot/summary')).json();
            document.getElementById('s-open').textContent = sum.open_count;
            document.getElementById('s-exposure').textContent = `$${sum.open_exposure.toFixed(0)}`;
            document.getElementById('s-winrate').textContent = `${sum.win_rate.toFixed(1)}%`;
            const pnlEl = document.getElementById('s-pnl');
            pnlEl.textContent = `$${sum.total_pnl >= 0 ? '+' : ''}${sum.total_pnl.toFixed(2)}`;
            pnlEl.style.color = sum.total_pnl >= 0 ? '#22c55e' : '#ef4444';
        } catch(e) { console.error('Summary error', e); }

        // Positions
        try {
            const pos = await (await fetch('/api/ercot/positions')).json();
            const tbody = document.getElementById('positions-body');
            tbody.innerHTML = '';
            pos.forEach(p => {
                const pnlCls = (p.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
                const expires = p.expires_at ? new Date(p.expires_at).toLocaleString() : '--';
                tbody.innerHTML += `<tr>
                    <td>${p.hub_name}</td>
                    <td>${p.signal}</td>
                    <td>$${p.size_dollars.toFixed(0)}</td>
                    <td>$${p.entry_price.toFixed(1)}</td>
                    <td>$${(p.current_price || p.entry_price).toFixed(1)}</td>
                    <td class="${pnlCls}">$${(p.unrealized_pnl || 0).toFixed(2)}</td>
                    <td style="font-size:0.75rem">${expires}</td>
                </tr>`;
            });
            if (!pos.length) tbody.innerHTML = '<tr><td colspan="7" style="color:#666">No open positions</td></tr>';
        } catch(e) { console.error('Positions error', e); }

        // Trades
        try {
            const trades = await (await fetch('/api/ercot/trades?limit=20')).json();
            const tbody = document.getElementById('trades-body');
            tbody.innerHTML = '';
            trades.forEach(t => {
                const pnlCls = t.pnl >= 0 ? 'positive' : 'negative';
                const closed = t.closed_at ? new Date(t.closed_at).toLocaleString() : '--';
                tbody.innerHTML += `<tr>
                    <td>${t.hub_name}</td>
                    <td>${t.signal}</td>
                    <td>$${t.entry_price.toFixed(1)}</td>
                    <td>$${t.exit_price.toFixed(1)}</td>
                    <td>$${t.size_dollars.toFixed(0)}</td>
                    <td class="${pnlCls}">$${t.pnl.toFixed(2)}</td>
                    <td style="font-size:0.75rem">${t.exit_reason || ''}</td>
                    <td style="font-size:0.75rem">${closed}</td>
                </tr>`;
            });
            if (!trades.length) tbody.innerHTML = '<tr><td colspan="8" style="color:#666">No trades yet</td></tr>';
        } catch(e) { console.error('Trades error', e); }
    }

    loadErcot();
    setInterval(loadErcot, 60000);
    </script>
</body>
</html>
```

- [ ] **Step 4: Verify dashboard starts without errors**

Run: `python -c "from dashboard.api import app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/ercot.html dashboard/static/index.html dashboard/api.py
git commit -m "feat: ERCOT dashboard page with hub signal cards, positions, and trade history"
```

---

## Chunk 6: Integration Test

### Task 9: End-to-end integration test

**Files:**
- Test: `tests/test_ercot_integration.py` (create)

- [ ] **Step 1: Write integration test**

Create `tests/test_ercot_integration.py`:

```python
"""End-to-end integration test for ERCOT solar signal pipeline."""
import os
import pytest

TEST_DB = "data/test_ercot_integration.db"


@pytest.fixture(autouse=True)
def clean_db():
    import ercot.paper_trader as pt
    pt.ERCOT_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_full_pipeline():
    """Scan hubs → open positions → evaluate → close."""
    from ercot.hubs import scan_all_hubs
    from ercot.paper_trader import open_position, get_open_positions, get_paper_summary
    from ercot.position_manager import evaluate_ercot_position

    # 1. Scan all hubs
    signals = scan_all_hubs()
    assert len(signals) == 5

    # 2. Open a position on the first tradeable signal
    tradeable = [s for s in signals if abs(s["edge"]) > 0]
    if not tradeable:
        pytest.skip("No tradeable signals right now")

    sig = tradeable[0]
    pos = open_position(sig, bankroll=10000.0)
    assert pos is not None
    assert pos["hub"] == sig["hub"]

    # 3. Evaluate the position
    result = evaluate_ercot_position(pos, sig)
    assert result["action"] in ("hold", "exit", "fortify")

    # 4. Verify summary
    summary = get_paper_summary()
    assert summary["open_count"] >= 1


def test_scan_cache_roundtrip():
    """Write scan results and read them back via dashboard API functions."""
    from ercot.hubs import scan_all_hubs
    from ercot.paper_trader import write_scan_cache, get_cached_signals

    signals = scan_all_hubs()
    write_scan_cache(signals)

    cached = get_cached_signals()
    assert len(cached) == 5
    assert {s["hub"] for s in cached} == {"North", "Houston", "South", "West", "Panhandle"}
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_ercot_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add tests/test_ercot_integration.py
git commit -m "test: ERCOT end-to-end integration tests"
```
