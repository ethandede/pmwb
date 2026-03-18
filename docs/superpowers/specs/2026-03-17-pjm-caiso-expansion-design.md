# PJM & CAISO Power Market Expansion

**Date:** 2026-03-17
**Status:** Draft
**Scope:** Add PJM and CAISO paper trading alongside existing ERCOT pipeline

---

## 1. Overview

Expand the power market trading bot from ERCOT-only to three ISOs: ERCOT, PJM, and CAISO. The same solar irradiance + load forecast signal model drives all three. Each region gets its own hub configs, data fetcher, paper trader, position manager, and pipeline config — copy-and-adapt from ERCOT, no shared refactor.

**Paper-only.** No live exchange integration yet.

---

## 2. Hub Configurations (in `config.py`)

### PJM_HUBS (5 hubs, 2 active)

```python
PJM_HUBS = {
    "Western":    {"city": "Pittsburgh",  "lat": 40.44, "lon": -80.00, "hub_name": "WESTERN",  "solar_sensitivity": 0.20, "active": True},
    "AEP-Dayton": {"city": "Columbus",    "lat": 39.96, "lon": -82.99, "hub_name": "AEP",      "solar_sensitivity": 0.15, "active": True},
    "NI":         {"city": "Chicago",     "lat": 41.88, "lon": -87.63, "hub_name": "NI",       "solar_sensitivity": 0.10, "active": False},
    "RTO":        {"city": "Philadelphia","lat": 39.95, "lon": -75.16, "hub_name": "RTO",      "solar_sensitivity": 0.12, "active": False},
    "PSEG":       {"city": "Newark",      "lat": 40.74, "lon": -74.17, "hub_name": "PSEG",     "solar_sensitivity": 0.08, "active": False},
}
```

### CAISO_HUBS (3 hubs, 1 active)

```python
CAISO_HUBS = {
    "SP15": {"city": "Los Angeles",  "lat": 34.05, "lon": -118.24, "hub_name": "SP15", "solar_sensitivity": 0.35, "active": True},
    "NP15": {"city": "Sacramento",   "lat": 38.58, "lon": -121.49, "hub_name": "NP15", "solar_sensitivity": 0.25, "active": False},
    "ZP26": {"city": "Fresno",       "lat": 36.74, "lon": -119.77, "hub_name": "ZP26", "solar_sensitivity": 0.30, "active": False},
}
```

### Solar Sensitivity Rationale

- CAISO sensitivities are highest (0.25-0.35) because California solar penetration is ~35% of capacity — price swings from solar ramps are massive (the "duck curve")
- PJM sensitivities are lower (0.08-0.20) because solar is only ~5% of PJM capacity — load drives prices more than solar
- Within PJM, Western Hub gets the highest sensitivity because the surrounding region (western PA/WV) has growing utility-scale solar and less grid-scale storage to buffer it

### Active Flag

The `"active"` field is new — ERCOT_HUBS doesn't have it (all 5 are always scanned). For PJM/CAISO, only active hubs are scanned. ERCOT won't get the field to avoid touching working code. The scanner functions for PJM/CAISO will filter on `active: True`.

---

## 3. Trading Parameters (in `config.py`)

Mirror ERCOT's parameter block for each region:

```python
# --- PJM Power Price Signal ---
PJM_PAPER_BANKROLL = 10_000.0
PJM_PAPER_MODE = True
PJM_MIN_EDGE = 0.03
PJM_MIN_CONFIDENCE = 50
PJM_FORTIFY_EDGE_INCREASE = 0.03
PJM_EXIT_EDGE_DECAY = 0.30
PJM_MAX_POSITIONS_PER_HUB = 3
PJM_MAX_POSITIONS_TOTAL = 8
PJM_POSITION_TTL_HOURS = 24
PJM_LOAD_SENSITIVITY = 0.20  # Higher than ERCOT — load matters more in PJM

# --- CAISO Power Price Signal ---
CAISO_PAPER_BANKROLL = 10_000.0
CAISO_PAPER_MODE = True
CAISO_MIN_EDGE = 0.03
CAISO_MIN_CONFIDENCE = 50
CAISO_FORTIFY_EDGE_INCREASE = 0.03
CAISO_EXIT_EDGE_DECAY = 0.30
CAISO_MAX_POSITIONS_PER_HUB = 3
CAISO_MAX_POSITIONS_TOTAL = 6
CAISO_POSITION_TTL_HOURS = 24
CAISO_LOAD_SENSITIVITY = 0.15
```

**Differences from ERCOT:**
- `PJM_LOAD_SENSITIVITY = 0.20` (vs ERCOT 0.15) — PJM prices are more load-driven than solar-driven
- `PJM_MAX_POSITIONS_TOTAL = 8` — 5 hubs but only 2 active initially
- `CAISO_MAX_POSITIONS_TOTAL = 6` — 3 hubs, 1 active initially

---

## 4. Seasonal Norms (in `config.py`)

### PJM

```python
PJM_SEASONAL_NORMS = {
    1:  {"solar":  7.0, "load": 92_000},
    2:  {"solar":  9.0, "load": 88_000},
    3:  {"solar": 12.5, "load": 80_000},
    4:  {"solar": 16.0, "load": 75_000},
    5:  {"solar": 18.5, "load": 78_000},
    6:  {"solar": 20.0, "load": 95_000},
    7:  {"solar": 20.5, "load": 105_000},
    8:  {"solar": 19.0, "load": 100_000},
    9:  {"solar": 15.0, "load": 85_000},
    10: {"solar": 11.5, "load": 76_000},
    11: {"solar":  8.0, "load": 80_000},
    12: {"solar":  6.5, "load": 90_000},
}
```

**Rationale:** PJM at ~40degN gets 20-25% less solar than ERCOT (~31degN). Winter is particularly weak (Ohio Valley cloudiness). Load is 30-50% higher than ERCOT because PJM serves ~65M people. Summer/winter spread is narrower than ERCOT because PJM has meaningful electric heating load.

### CAISO

```python
CAISO_SEASONAL_NORMS = {
    1:  {"solar": 12.0, "load": 24_000},
    2:  {"solar": 14.5, "load": 23_500},
    3:  {"solar": 19.0, "load": 23_000},
    4:  {"solar": 23.0, "load": 23_500},
    5:  {"solar": 26.0, "load": 25_000},
    6:  {"solar": 28.0, "load": 30_000},
    7:  {"solar": 27.5, "load": 35_000},
    8:  {"solar": 25.5, "load": 34_000},
    9:  {"solar": 22.0, "load": 30_000},
    10: {"solar": 17.0, "load": 26_000},
    11: {"solar": 13.0, "load": 24_000},
    12: {"solar": 11.0, "load": 24_500},
}
```

**Rationale:** CAISO has the highest solar irradiance (semi-arid California) but smallest load (~30M people, mild climate, aggressive efficiency programs). The duck curve effect is most pronounced here — solar ramps create massive intraday price swings.

### Cross-ISO Sanity Check

| Metric | PJM | ERCOT | CAISO |
|--------|-----|-------|-------|
| July solar (MJ/m2) | 20.5 | 26.0 | 27.5 |
| Dec solar (MJ/m2) | 6.5 | 10.0 | 11.0 |
| July load (MW) | 105,000 | 70,000 | 35,000 |
| Dec load (MW) | 90,000 | 48,000 | 24,500 |

---

## 5. Data Fetching

### PJM: `pjm/hubs.py` — `_fetch_pjm_market_data()`

**Source:** PJM public data feeds (no auth required for basic access).

**Endpoints:**
1. **Real-time LMPs** — `https://api.pjm.com/api/v1/rt_hrl_lmps` (JSON, public)
   - Filter by `pnode_name` matching hub names (WESTERN, AEP, NI, RTO, PSEG)
   - Returns: `total_lmp_rt` ($/MWh) per hub
   - Fallback: PJM Data Miner CSV endpoint at `https://dataminer2.pjm.com/feed/rt_hrl_lmps/definition`
2. **Load forecast** — `https://api.pjm.com/api/v1/load_frcstd_7_day`
   - Returns: `forecast_load_mw` (system total)
3. **Solar generation** — currently stubbed (PJM doesn't expose solar generation in a simple public feed yet)

**Cache:** 5-minute TTL, same module-level pattern as ERCOT.

**Fallback defaults:** `{"price": 35.0, "solar_mw": 5000.0, "load_forecast": 85_000.0}`

**Important:** PJM's public API may require a subscription key header (`Ocp-Apim-Subscription-Key`). If the free tier doesn't work, we fall back to scraping the Data Miner 2 CSV feeds which are fully public. The code will try the JSON API first, then fall back to CSV parsing.

### CAISO: `caiso/hubs.py` — `_fetch_caiso_market_data()`

**Source:** CAISO public endpoints (fully public, no auth).

**Endpoints:**
1. **Real-time prices** — `https://www.caiso.com/outlook/SP/fuels.json` or OASIS XML
   - CAISO Today's Outlook provides JSON feeds per zone
   - Alternative: OASIS `http://oasis.caiso.com/oasisapi/SingleZip` with `queryname=PRC_LMP`
   - Returns: LMP per trading hub (SP15, NP15, ZP26)
2. **Load forecast** — `https://www.caiso.com/outlook/SP/demand.json`
   - Returns: current system demand in MW
3. **Solar generation** — `https://www.caiso.com/outlook/SP/fuels.json`
   - CAISO publishes real-time solar generation (MW) in their renewables outlook
   - This is a significant advantage over PJM — we can directly see solar ramp effects

**Cache:** 5-minute TTL, same pattern.

**Fallback defaults:** `{"price": 40.0, "solar_mw": 10000.0, "load_forecast": 27_000.0}`

### Shared Pattern

Both follow the ERCOT pattern exactly:
- Module-level `_cache` dict and `_cache_time` float
- `_CACHE_TTL = 300`
- Try each endpoint independently, accumulate into result dict
- Telegram alert if all endpoints fail
- `fetch_<iso>_markets()` wraps the raw data into per-hub market dicts for the pipeline

---

## 6. Signal Model

### Reuse Strategy

The fair-price model is identical across all three ISOs — the only differences are:
- Different seasonal norms (section 4)
- Different load sensitivity constants
- Different solar sensitivity per hub

Rather than modifying `get_ercot_solar_signal()`, we create two new functions in `weather/multi_model.py`:
- `get_pjm_solar_signal(lat, lon, hub_key, solar_sensitivity, hours_ahead, pjm_data)`
- `get_caiso_solar_signal(lat, lon, hub_key, solar_sensitivity, hours_ahead, caiso_data)`

Each is a copy of `get_ercot_solar_signal()` with:
1. Different config imports (`PJM_SEASONAL_NORMS` / `CAISO_SEASONAL_NORMS`)
2. Different load sensitivity constant (`PJM_LOAD_SENSITIVITY` / `CAISO_LOAD_SENSITIVITY`)
3. Different key names in the data dict (`pjm_data` / `caiso_data` instead of `ercot_data`)
4. Different return key names (`current_pjm_price` / `current_caiso_price` instead of `current_ercot_price`)

The solar irradiance fetching (VisualCrossing + Open-Meteo) is identical — same lat/lon based API calls. The confidence model is identical.

---

## 7. Directory Structure (copy-and-adapt from `ercot/`)

```
pjm/
    __init__.py
    hubs.py              # _fetch_pjm_market_data(), fetch_pjm_markets(), scan_all_pjm_hubs()
    paper_trader.py      # SQLite -> data/pjm_paper.db (same schema as ercot_paper.db)
    position_manager.py  # evaluate_pjm_position(), run_pjm_manager()

caiso/
    __init__.py
    hubs.py              # _fetch_caiso_market_data(), fetch_caiso_markets(), scan_all_caiso_hubs()
    paper_trader.py      # SQLite -> data/caiso_paper.db
    position_manager.py  # evaluate_caiso_position(), run_caiso_manager()
```

No `auth.py` needed for either — both use public endpoints (unlike ERCOT which has OAuth).

---

## 8. Paper Trader

Each region gets its own SQLite database with identical schema to ERCOT:

- `data/pjm_paper.db` — tables: `pjm_positions`, `pjm_trades`, `pjm_scan_cache`
- `data/caiso_paper.db` — tables: `caiso_positions`, `caiso_trades`, `caiso_scan_cache`

Same functions as `ercot/paper_trader.py`:
- `open_position(hub_signal, bankroll, max_size)` — Kelly sizing with region-specific config
- `close_position(position_id, exit_price, exit_signal, reason)` — P&L recording
- `expire_positions(current_price)` — TTL-based auto-close
- `get_open_positions()`, `get_trade_history()`, `get_paper_summary()`
- `write_scan_cache(signals)`, `get_cached_signals()`

Only difference: imports pull from `PJM_*` / `CAISO_*` config constants instead of `ERCOT_*`.

---

## 9. Position Manager

Each region gets its own position manager, identical logic to `ercot/position_manager.py`:
- `evaluate_<iso>_position(position, current_signal)` — exit/fortify/hold
- `run_<iso>_manager()` — scan, expire, evaluate, execute

Uses region-specific config constants for:
- `FORTIFY_EDGE_INCREASE`
- `EXIT_EDGE_DECAY`
- `MAX_POSITIONS_PER_HUB`
- `PAPER_BANKROLL`

---

## 10. Pipeline Integration

Add two new `MarketConfig` entries in `pipeline/config.py`:

```python
pjm = MarketConfig(
    name="pjm",
    display_name="PJM Solar",
    exchange="pjm",
    fetch_fn=fetch_pjm_markets,
    series=PJM_HUBS,
    bucket_parser=None,
    forecast_fn=get_pjm_solar_signal,
    fusion_weights=None,
    edge_gate=0.03,
    confidence_gate=50,
    sameday_overrides=None,
    sanity_fn=None,
    scan_frac=0.10,
    kelly_floor=0.25,
    max_contracts_per_event=3,
    execute_fn=execute_trade,
    pricing_fn=None,
    manage_fn=run_pjm_manager,
    exit_rules={"edge_decay_pct": 0.30, "signal_flip": True, "ttl_hours": 24},
    settlement_timeline="hourly",
    settle_fn=run_pjm_manager,
)

caiso = MarketConfig(
    name="caiso",
    display_name="CAISO Solar",
    exchange="caiso",
    fetch_fn=fetch_caiso_markets,
    series=CAISO_HUBS,
    bucket_parser=None,
    forecast_fn=get_caiso_solar_signal,
    fusion_weights=None,
    edge_gate=0.03,
    confidence_gate=50,
    sameday_overrides=None,
    sanity_fn=None,
    scan_frac=0.10,
    kelly_floor=0.25,
    max_contracts_per_event=3,
    execute_fn=execute_trade,
    pricing_fn=None,
    manage_fn=run_caiso_manager,
    exit_rules={"edge_decay_pct": 0.30, "signal_flip": True, "ttl_hours": 24},
    settlement_timeline="hourly",
    settle_fn=run_caiso_manager,
)
```

Update `_build_configs()` to import and return these, and add them to `ALL_CONFIGS`.

---

## 11. Dashboard Integration

The dashboard scan cache (`data/scan_cache.db`) already supports multiple market types via the `market_type` column. PJM and CAISO signals will be cached with `market_type="pjm"` and `market_type="caiso"` respectively.

The dashboard frontend (`dashboard/static/js/app.js`) will need:
- New tabs or sections for PJM and CAISO scan results
- This is a minor UI addition — same table format as ERCOT

---

## 12. Tests

Create test files mirroring ERCOT's test suite:

```
tests/
    test_pjm_signal.py          # Fair-price model with PJM norms
    test_pjm_paper_trader.py    # Kelly sizing, position limits, TTL
    test_pjm_hubs.py            # Hub config, fetch, cache
    test_pjm_position_manager.py # Evaluate/exit/fortify
    test_caiso_signal.py
    test_caiso_paper_trader.py
    test_caiso_hubs.py
    test_caiso_position_manager.py
```

Each mirrors the corresponding ERCOT test with region-specific values.

---

## 13. Accounts Needed (Future Live Trading)

For paper trading: **no accounts needed**. All data is from free public endpoints.

For future live trading:

| Platform | What | Status | Notes |
|----------|------|--------|-------|
| **PJM Data Miner 2** | Free API key | Optional | `apiportal.pjm.com` — gives faster, more reliable LMP access. Not needed for paper trading. |
| **Nodal Exchange** | Trading account | Not yet | Main venue for PJM + CAISO daily power futures (on-peak/off-peak). Requires institutional-grade onboarding — clearing member or FCM relationship. |
| **ElectronX** | Trading account | Pending (ERCOT) | Emerging retail platform. If they add PJM/CAISO products, same account should work. |
| **ICE (Intercontinental Exchange)** | Trading account | Not yet | Alternative to Nodal for power futures. Higher minimums. |
| **CME Group** | Trading account | Not yet | Offers CAISO peak/off-peak futures. Institutional access. |

**Recommended first step for live:** Wait for ElectronX to add PJM/CAISO products (retail-friendly). Nodal Exchange is the primary institutional venue but requires an FCM relationship.

---

## 14. What's NOT in Scope

- No refactoring of existing ERCOT code (copy-and-adapt strategy)
- No live exchange integration (paper-only)
- No backtesting framework for PJM/CAISO (build later from paper trade data)
- No ERCOT `active` flag retrofit (all 5 ERCOT hubs remain always-on)
- No wind signal integration (solar + load only for now)
- No cross-ISO arbitrage signals (each ISO trades independently)

---

## 15. File Change Summary

| File | Change |
|------|--------|
| `config.py` | Add PJM_HUBS, CAISO_HUBS, PJM_*, CAISO_* constants, seasonal norms |
| `weather/multi_model.py` | Add `get_pjm_solar_signal()`, `get_caiso_solar_signal()` |
| `pjm/__init__.py` | New |
| `pjm/hubs.py` | New — fetch + scan |
| `pjm/paper_trader.py` | New — SQLite paper trading |
| `pjm/position_manager.py` | New — evaluate/manage |
| `caiso/__init__.py` | New |
| `caiso/hubs.py` | New — fetch + scan |
| `caiso/paper_trader.py` | New — SQLite paper trading |
| `caiso/position_manager.py` | New — evaluate/manage |
| `pipeline/config.py` | Add PJM + CAISO MarketConfig entries to ALL_CONFIGS |
| `tests/test_pjm_*.py` | New (4 files) |
| `tests/test_caiso_*.py` | New (4 files) |
