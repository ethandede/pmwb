# ERCOT Solar / Power Price Signal Integration

**Date:** 2026-03-14
**Status:** Approved
**Approach:** Hybrid (Approach C) — new `ercot/` package, shared risk/logging utilities

## Overview

Integrate ERCOT power price signals driven by solar irradiance forecasts into the existing weather trading system. Paper-only until ElectronX options account is live. Covers 5 ERCOT hubs, full position management, and a dedicated dashboard tab.

## ERCOT Hubs

```python
ERCOT_HUBS = {
    "North":     {"city": "Dallas",      "lat": 32.78,  "lon": -96.80,  "hub_name": "HB_NORTH"},
    "Houston":   {"city": "Houston",     "lat": 29.76,  "lon": -95.37,  "hub_name": "HB_HOUSTON"},
    "South":     {"city": "San Antonio", "lat": 29.42,  "lon": -98.49,  "hub_name": "HB_SOUTH"},
    "West":      {"city": "Midland",     "lat": 31.99,  "lon": -102.08, "hub_name": "HB_WEST"},
    "Panhandle": {"city": "Amarillo",    "lat": 35.22,  "lon": -101.83, "hub_name": "HB_PAN"},
}
```

## ERCOT Public API Endpoints

All free, no auth, JSON, updated every 5 minutes:

- **Real-time LMPs:** `https://www.ercot.com/api/public-reports/np6788/rtmLmp` — system-wide $/MWh
- **Actual solar generation:** `https://www.ercot.com/api/public-reports/np4-738-cd/spp_actual_5min_avg_values` — MW currently being produced
- **7-day load forecast:** `https://www.ercot.com/api/public-reports/np3-566-cd/lf_by_model_study_area` — hourly demand forecast

Cache ERCOT API responses for 5 minutes in-memory to avoid redundant calls across the 5 hub scans per cycle.

## Architecture

### 1. Data Layer — `ercot/hubs.py`

- Imports `ERCOT_HUBS` from `config.py`
- `_fetch_ercot_market_data() -> dict` — single cached call to all 3 ERCOT endpoints, returns `{"price": float, "solar_mw": float, "load_forecast": float}`. Uses module-level `_ercot_cache` dict with `_ercot_cache_time` timestamp; refetches if `time.time() - _ercot_cache_time > 300` (5 min TTL). Returns fallback values `{"price": 40.0, "solar_mw": 12000.0, "load_forecast": 50000.0}` on failure and logs the error.
- `scan_all_hubs() -> list[dict]` — iterates all 5 hubs, calls `get_ercot_solar_signal()` from `weather/multi_model.py` for per-hub irradiance, merges with shared ERCOT market data. Returns enriched signal dicts:

```python
{"hub": "North", "hub_name": "HB_NORTH", "city": "Dallas",
 "signal": "SHORT", "edge": 1.69, "expected_solrad_mjm2": 21.8,
 "current_ercot_price": 40.0, "actual_solar_mw": 12500.0,
 "confidence": 70}
```

Note: ERCOT price is system-wide (same for all hubs). Hub differentiation comes from per-location solar irradiance forecasts — West Texas (Midland) and Panhandle (Amarillo) have very different solar profiles from Houston or San Antonio.

### 2. Paper Trading Engine — `ercot/paper_trader.py`

SQLite-backed at `data/ercot_paper.db`.

**Tables:**

`ercot_positions` (open paper positions):
- `id` INTEGER PRIMARY KEY
- `hub` TEXT — hub key (e.g., "North")
- `hub_name` TEXT — ERCOT hub name (e.g., "HB_NORTH")
- `signal` TEXT — "SHORT" or "LONG"
- `entry_price` REAL — ERCOT $/MWh at entry
- `size_dollars` REAL — Kelly-sized position
- `edge` REAL — edge at entry
- `confidence` INTEGER
- `opened_at` TEXT — ISO timestamp (UTC)
- `expires_at` TEXT — ISO timestamp (UTC), default 24h after opened_at

`ercot_trades` (closed trades / P&L log):
- `id` INTEGER PRIMARY KEY
- `hub` TEXT
- `hub_name` TEXT
- `signal` TEXT — direction at entry
- `exit_signal` TEXT — direction at exit (for post-hoc analysis)
- `entry_price` REAL
- `exit_price` REAL
- `size_dollars` REAL
- `pnl` REAL
- `edge_at_entry` REAL
- `confidence` INTEGER
- `opened_at` TEXT
- `closed_at` TEXT
- `exit_reason` TEXT

`ercot_scan_cache` (latest signals per hub for dashboard):
- `id` INTEGER PRIMARY KEY
- `hub` TEXT
- `hub_name` TEXT
- `signal` TEXT
- `edge` REAL
- `expected_solrad_mjm2` REAL
- `current_ercot_price` REAL
- `actual_solar_mw` REAL
- `confidence` INTEGER
- `scanned_at` TEXT

**Functions:**

- `open_position(hub_signal, bankroll)` — Simplified Kelly sizing for ERCOT: `size = min(edge * FRACTIONAL_KELLY * bankroll, MAX_BANKROLL_PCT_PER_TRADE * bankroll)`. Calls `check_limits(order_dollars=size, bankroll=bankroll, scan_spent=0.0, city_day_spent=0.0, total_exposure=current_ercot_exposure)` — only per-order and total-exposure limits are meaningful for ERCOT. Caps at `ERCOT_PAPER_BANKROLL`. Respects `ERCOT_MAX_POSITIONS_PER_HUB` and `ERCOT_MAX_POSITIONS_TOTAL`. Sets `expires_at` to `ERCOT_POSITION_TTL_HOURS` from now. Writes to `ercot_positions`.
- `close_position(position_id, exit_price, exit_signal, reason)` — moves from positions to trades. P&L formula: `direction * (exit_price - entry_price) / entry_price * size_dollars` where direction is +1 for LONG, -1 for SHORT.
- `expire_positions(current_price)` — auto-closes any positions past `expires_at` with reason "expired".
- `get_open_positions() -> list[dict]`
- `get_trade_history(limit=50) -> list[dict]`
- `get_paper_summary() -> dict` — total P&L, win rate, open count, open exposure
- `write_scan_cache(signals: list[dict])` — writes latest scan results for dashboard API
- `get_cached_signals() -> list[dict]` — reads latest cached signals

### 3. Position Manager — `ercot/position_manager.py`

**`evaluate_ercot_position(position, current_signal) -> dict`**

Takes an open paper position + fresh hub signal. Returns action dict with:
- `action`: "hold" | "exit" | "fortify"
- `reason`: human-readable string

Decision logic:
- **HOLD** — signal still agrees with position direction (default ~85% of positions)
- **FORTIFY** — edge strengthened: current edge > entry edge + `ERCOT_FORTIFY_EDGE_INCREASE`. Must not exceed `ERCOT_MAX_POSITIONS_PER_HUB`. Calls `open_position()` to add size. Fortify size capped at existing position size (no more than doubling).
- **EXIT** — signal flipped (was SHORT, now LONG) or edge decayed below threshold. Edge decay formula: exit when `current_edge < entry_edge * ERCOT_EXIT_EDGE_DECAY`. Example: entered SHORT at edge 2.0, exit when current edge < 0.6 (30% of 2.0). Calls `close_position()` with current ERCOT price.

No EV-vs-settlement gate (power markets don't settle like weather contracts). Uses edge-decay threshold instead.

**`run_ercot_manager()`**

Main loop:
1. Expire any positions past their 24h window via `expire_positions()`
2. Load remaining open positions from `ercot_paper.db`
3. Fetch fresh signal per hub via `scan_all_hubs()` (reuses cached ERCOT data)
4. Evaluate each position
5. Print rich table matching weather position manager style:

```
Ticker        Side    Size    Edge    Action    Reason
HB_NORTH      SHORT   $200   +1.69   HOLD      edge strong
HB_HOUSTON    SHORT   $150   -0.30   EXIT      signal flipped to LONG
```

6. Execute exits/fortifies against paper DB

### 4. Scanner Integration — `scanner.py`

Remove the existing `texas_cities` ERCOT block (iterates CITIES for dallas/houston/etc.) entirely and replace with a full hub-based section using `ERCOT_HUBS`. Order of operations: manage existing positions first, then scan for new entries.

1. Run `run_ercot_manager()` to evaluate/exit/fortify existing positions
2. Call `scan_all_hubs()` for all 5 hubs (reuses cached signals from step 1)
3. Write signals to `ercot_scan_cache` table for dashboard
4. Display dedicated rich table:

```
ERCOT Solar Signals (5 hubs)
Hub          City           Signal   Edge    Solrad    Solar MW   ERCOT$    Conf    Action
North        Dallas         SHORT    +1.69   21.8      12500      $40.1     70%     OPEN $200
Houston      Houston        SHORT    +1.02   19.1      12500      $38.5     70%     HOLD (existing)
South        San Antonio    NEUTRAL  +0.00   14.2      12500      $41.0     50%     --
West         Midland        LONG     +1.25   8.0       12500      $55.3     70%     OPEN $180
Panhandle    Amarillo       NEUTRAL  +0.00   15.1      12500      $39.8     50%     --
```

5. For signals with `|edge| > ERCOT_MIN_EDGE` and `confidence >= ERCOT_MIN_CONFIDENCE`:
   - Check if paper position already exists for that hub
   - Same direction → skip or fortify (if edge increased)
   - Opposite direction → exit existing + open new
   - No position → open new
6. Print paper P&L summary:

```
ERCOT Paper: 3 open ($530 exposure) | Closed: 12W/4L | Net P&L: +$287.40
```

### 5. Dashboard

**Backend: `dashboard/ercot_api.py`**

FastAPI router mounted in `dashboard/api.py`:

- `GET /api/ercot/signals` — latest signals from `ercot_scan_cache` table (no live API calls)
- `GET /api/ercot/positions` — open paper positions from `ercot_paper.db`
- `GET /api/ercot/trades?limit=50` — closed trade history with P&L
- `GET /api/ercot/summary` — aggregate stats (total P&L, win rate, open exposure, best/worst hub)

Router registration:
```python
from dashboard.ercot_api import ercot_router
app.include_router(ercot_router)
```

**Frontend: `dashboard/static/ercot.html`**

Separate page, same visual style as weather dashboard. Nav link added to `index.html`.

Sections:
1. **Hub Signal Cards** — 5 cards (one per hub), color-coded: green=LONG, red=SHORT, gray=NEUTRAL. Shows current signal, solrad, actual solar MW, ERCOT price, confidence.
2. **Open Positions Table** — hub, direction, size, entry price, current edge, unrealized P&L (computed by joining positions with latest `current_ercot_price` from `ercot_scan_cache`), time remaining (frontend computes from `expires_at`)
3. **Trade History** — recent closed trades with outcome
4. **Paper P&L Chart** — equity curve of simulated $10k bankroll over time

### 6. Config Additions — `config.py`

```python
# ERCOT Hubs
ERCOT_HUBS = {
    "North":     {"city": "Dallas",      "lat": 32.78,  "lon": -96.80,  "hub_name": "HB_NORTH"},
    "Houston":   {"city": "Houston",     "lat": 29.76,  "lon": -95.37,  "hub_name": "HB_HOUSTON"},
    "South":     {"city": "San Antonio", "lat": 29.42,  "lon": -98.49,  "hub_name": "HB_SOUTH"},
    "West":      {"city": "Midland",     "lat": 31.99,  "lon": -102.08, "hub_name": "HB_WEST"},
    "Panhandle": {"city": "Amarillo",    "lat": 35.22,  "lon": -101.83, "hub_name": "HB_PAN"},
}

# ERCOT Paper Trading
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

### 7. Signal Function Update — `weather/multi_model.py`

Update `get_ercot_solar_signal()` to also return `actual_solar_mw` from the ERCOT solar generation endpoint. The function accepts an optional `ercot_data` dict (pre-fetched by `_fetch_ercot_market_data()` in `ercot/hubs.py`) to avoid redundant API calls:

```python
def get_ercot_solar_signal(lat, lon, hours_ahead=24, ercot_data=None) -> dict:
    # ... existing irradiance forecast (always runs — per-hub lat/lon) ...
    # When ercot_data is provided, skip internal ERCOT price/solar fetch
    # and use ercot_data["price"] and ercot_data["solar_mw"] instead.
    # When ercot_data is None, fetch directly (backwards compatible).
    # Returns: signal, edge, expected_solrad_mjm2, current_ercot_price,
    #          actual_solar_mw, confidence, ticker_hint
```

## Shared Dependencies

Reused from existing codebase (not duplicated):
- `risk/position_limits.py` — `check_limits()` for sizing guard (pass `scan_spent=0.0`, `city_day_spent=0.0`)
- `config.py` — `FRACTIONAL_KELLY`, `MAX_BANKROLL_PCT_PER_TRADE`
- `weather/multi_model.py` — `get_ercot_solar_signal()`
- `alerts/telegram_alert.py` — signal alerts (parameterize header for ERCOT vs weather)

## File Changes Summary

| File | Change |
|------|--------|
| `config.py` | Add `ERCOT_HUBS` + paper trading constants |
| `weather/multi_model.py` | Update `get_ercot_solar_signal()` with `actual_solar_mw` + optional `ercot_data` param |
| `ercot/__init__.py` | New package |
| `ercot/hubs.py` | `_fetch_ercot_market_data()` cached fetcher + `scan_all_hubs()` |
| `ercot/paper_trader.py` | SQLite paper positions + Kelly sizing + scan cache |
| `ercot/position_manager.py` | Evaluate/fortify/exit for paper positions |
| `scanner.py` | Replace basic ERCOT print with full hub scan + paper trading |
| `dashboard/ercot_api.py` | FastAPI router for ERCOT endpoints |
| `dashboard/api.py` | Mount ERCOT router |
| `dashboard/static/ercot.html` | ERCOT dashboard page |
| `dashboard/static/index.html` | Add nav link to ERCOT page |

## Future: ElectronX Migration

When the ElectronX account is live:
1. Add `ercot/electronx_trader.py` — real order execution implementing same interface as paper_trader
2. Flip `ERCOT_PAPER_MODE = False`
3. Paper trader continues logging alongside for comparison (dual-track mode)
4. Add ElectronX API credentials to `.env`
