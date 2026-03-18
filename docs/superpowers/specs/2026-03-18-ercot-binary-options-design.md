# ERCOT Binary Options — RT vs DAM Signal Model

**Date:** 2026-03-18
**Status:** Draft
**Target:** Electron Exchange ERCOT binary options (paper trading until API access)

## Overview

Redesign the ERCOT module to score P(RT >= DAM) per hub per hour for Electron Exchange binary options. Replace the current vague solar/load "directional edge" model with a concrete binary outcome: will the Real-Time Settlement Point Price (RT SPP) meet or exceed the Day-Ahead Market price (DAM SPP) for a given hub and hour?

## Contract Structure (Electron Exchange)

- **Symbol:** `BOPT-ERCOT-[hub_name]-[yymmdd]-[hh]` (e.g., `BOPT-ERCOT-HB_WEST-260318-14`)
- **Settlement:** $100 if RT >= DAM, $0 if RT < DAM
- **Strike:** Previous day's DAM SPP for that hub/hour
- **Floating:** Average of 4 fifteen-minute RT SPP intervals for the contract hour
- **Hour convention:** ERCOT uses Hour Ending (HE). HE14 = 1:00 PM - 2:00 PM CT. The contract labeled "hour 14" covers the 60 minutes ending at 2:00 PM CT.
- **Hubs:** HB_NORTH, HB_HOUSTON, HB_SOUTH, HB_WEST, HB_PAN (initial 5; BUSAVG and HUBAVG deferred)

## Scope Constraints (Initial Build)

These limits are deliberate. Expand after baseline validation (see expansion roadmap in memory).

| Constraint | Initial Value | Expand When |
|---|---|---|
| Hours traded | HE11-HE18 (10am-6pm CT, 8 hours) | Solar model shows >55% hit rate |
| Forecast horizon | Today + tomorrow only | Add wind/load models for longer-range signals |
| Signal inputs | Solar irradiance only | Add wind for Pan/West, then load |
| Hubs | 5 existing (HB_NORTH, HB_HOUSTON, HB_SOUTH, HB_WEST, HB_PAN) | Add BUSAVG/HUBAVG after per-hub model stabilizes |
| Market price | 50% assumed (no Electron feed) | Replace with Electron bid/ask when API access granted |

## Data Layer

### DAM SPP Fetch (new)

Pull hourly Day-Ahead prices from ERCOT's DAM SPP report.

- **Endpoint:** ERCOT public reports API (via existing `ercot/auth.py` token)
- **Report ID:** `np4-190-cd` — DAM Settlement Point Prices (or `np4-183-cd` if 190 is unavailable; implementer should verify against ERCOT API catalog)
- **Granularity:** One price per hub per hour for the next operating day (24 hours)
- **Cache:** Per calendar day — DAM prices are published once daily and don't change
- **Structure:** `{hub_name: {hour_ending: price_dollars}}` e.g., `{"HB_WEST": {11: 42.50, 12: 38.00}}`
- **Failure behavior:** Return `None` on fetch failure. All contracts for that day are skipped. Never fall back to default prices (per project convention — see `feedback_no_default_on_api_failure`).

### RT SPP Fetch (new, for settlement)

Pull actual Real-Time prices after each contract hour expires.

- **Endpoint:** Existing `spp_node_zone_hub` endpoint (already used), filtered to 15-minute interval level
- **Settlement calculation:** Average the 4 fifteen-minute intervals per Electron spec
  - Example: HE14 (1:00-2:00 PM CT) = avg of intervals 1:00-1:15, 1:15-1:30, 1:30-1:45, 1:45-2:00 PM CT
- **Timing:** Check after each hour completes (daemon cycle runs every 5 minutes, so settlement happens within 5 minutes of hour end)
- **Failure behavior:** Return `None`. Position stays open until RT data becomes available (retry next cycle).

### Solar Forecast (existing, reused)

- Visual Crossing + Open-Meteo solar irradiance — no changes
- Already cached at 30-min TTL via `weather/cache.py`
- Project daily solar forecast to hourly granularity using a solar curve (see Signal Model)
- Guard: `expected_solar = max(0, forecast_value)` — negative irradiance is physically impossible

## Signal Model

### Core Logic

For each contract (hub H, hour ending T):

1. **Get DAM price** — the strike for this hub/hour
2. **Get solar forecast** — expected irradiance for hour T's window
3. **Estimate RT price direction** — solar deviation from DAM's implied assumption drives the spread

### How Solar Drives RT-DAM Spread

- DAM prices are set day-ahead based on expected supply/demand balance
- Solar generation is a major supply-side variable in ERCOT (especially West, South, Panhandle)
- **Higher-than-expected solar** → excess supply → RT drops below DAM → P(RT >= DAM) low → bet NO
- **Lower-than-expected solar** (clouds, storms) → supply shortfall → RT rises above DAM → P(RT >= DAM) high → bet YES

### Probability Estimation

```
expected_solar = max(0, forecast_irradiance_for_hour(hub, hour))
norm_solar = seasonal_norm_for_hour(month, hour)
solar_deviation = (norm_solar - expected_solar) / norm_solar

# Positive deviation = less solar than normal = RT likely > DAM
# Negative deviation = more solar than normal = RT likely < DAM

estimated_rt_shift = solar_deviation * hub_solar_sensitivity * dam_price
estimated_rt = dam_price + estimated_rt_shift

# Logistic function maps price difference to probability
# k calibrated so typical solar deviations produce 35%-65% probabilities
# With sensitivity=0.2, deviation=0.2, DAM=$40: shift=$1.60, k*1.60=0.56 → P≈64%
k = 0.35
P_rt_gte_dam = 1 / (1 + exp(-k * (estimated_rt - dam_price)))
```

- When `estimated_rt == dam_price`: P = 50% (no edge)
- When solar deficit predicts RT > DAM: P > 50% (buy YES)
- When solar surplus predicts RT < DAM: P < 50% (buy NO)

The `k` parameter controls signal sharpness. Starting at 0.35 produces a useful range (~35%-65% for typical deviations). Tune from paper trading data — if hit rate is good but edges are too thin, increase k; if edges are large but hit rate is poor, decrease k.

### Edge and Side

```
market_prob = 0.50  # no Electron market feed yet; assume fair value
edge = model_prob - market_prob
side = "yes" if edge > 0 else "no"
```

When Electron API access is granted, `market_prob` will come from actual bid/ask prices.

### Confidence

Same structure as current model:
```
base = 30
agreement_bonus = 20 if VC and OM solar forecasts agree within 2 MJ/m²
deviation_bonus = min(30, abs(solar_deviation) * 300)
confidence = min(90, max(30, base + agreement_bonus + deviation_bonus))
```

Gate: minimum 50 to trade.

### Hourly Solar Curve

Daily solar forecast needs to be distributed across hours. Use a cosine curve centered on solar noon (~1pm CT, HE14):

```
# Only compute for trading hours HE11-HE18
hour_weight(he) = max(0, cos((he - 13.5) * pi / 10))
hour_solar(he) = daily_solar * hour_weight(he) / sum(hour_weight(h) for h in range(11, 19))
```

Summation is across trading hours only (HE11-HE18). This normalizes the daily total into the 8-hour window, with peak weight at midday. Hours outside HE11-HE18 are not traded (deferred to wind/load expansion).

## Contract Lifecycle

### Scan Cycle (every 5 minutes)

1. Fetch DAM prices for today + tomorrow (cache per day)
2. For each hub (5) × each solar hour (HE11-HE18, 8 hours) = up to 40 contracts
3. Skip hours that have already passed (current CT HE > contract HE)
4. Score P(RT >= DAM), compute edge
5. Filter: `|edge| >= 0.03` and `confidence >= 50`
6. Dedup: one position per hub per hour
7. Size via Kelly against ERCOT paper bankroll ($10,000)
8. Open paper position with `entry_price = 0.50` (assumed market price, since no Electron feed)

### Hourly Auto-Settlement

Each scan cycle also checks for expired positions:

1. Find positions where `contract_hour` (HE) has passed
2. Fetch actual RT SPP for that hub/hour (average of 4 fifteen-minute intervals)
3. If RT fetch returns None, skip (retry next cycle)
4. Settle: `RT >= DAM → settlement_value = 100`, `RT < DAM → settlement_value = 0`
5. Record P&L: `(settlement_value / 100.0 - entry_price) * size_dollars`
   - `entry_price` is stored as a fraction (0.0-1.0), representing the cost per dollar of notional
   - With assumed market price of 0.50: a $100 win nets `(1.0 - 0.50) * size = +50%`, a $0 loss costs `(0.0 - 0.50) * size = -50%`
6. Move from `ercot_positions` to `ercot_trades`

### Position Limits

- 3 per hub (existing)
- 10 total (existing)
- 1 per hub per hour (new dedup)
- No exit/fortify logic — binary contracts settle automatically at hour end

## Schema Changes

**Migration strategy:** Drop and recreate all three ERCOT tables. This is paper trading with no real money — the 5 expired historical trades are backed up and not worth preserving over schema simplicity.

### ercot_positions (recreate)

```sql
CREATE TABLE ercot_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,           -- "North", "Houston", etc.
    hub_name TEXT NOT NULL,      -- "HB_NORTH", "HB_HOUSTON", etc.
    contract_date TEXT NOT NULL, -- "2026-03-18"
    contract_hour INTEGER NOT NULL, -- Hour Ending (11-18 for solar hours)
    side TEXT NOT NULL,          -- "yes" or "no"
    dam_price REAL NOT NULL,     -- strike price (DAM SPP)
    entry_price REAL NOT NULL,   -- cost fraction (0.50 assumed initially)
    size_dollars REAL NOT NULL,  -- position size from Kelly
    model_prob REAL NOT NULL,    -- P(RT >= DAM) at entry
    edge REAL NOT NULL,
    confidence INTEGER NOT NULL,
    opened_at TEXT NOT NULL
);
```

### ercot_trades (recreate)

```sql
CREATE TABLE ercot_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    contract_date TEXT NOT NULL,
    contract_hour INTEGER NOT NULL,
    side TEXT NOT NULL,
    dam_price REAL NOT NULL,
    rt_price REAL,               -- actual RT SPP (avg of 4 intervals)
    entry_price REAL NOT NULL,
    size_dollars REAL NOT NULL,
    settlement_value INTEGER,    -- 0 or 100
    pnl REAL NOT NULL,
    model_prob REAL NOT NULL,
    edge REAL NOT NULL,
    confidence INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    exit_reason TEXT              -- "settled" or "expired_no_rt_data"
);
```

### ercot_scan_cache (recreate)

```sql
CREATE TABLE ercot_scan_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub TEXT NOT NULL,
    hub_name TEXT NOT NULL,
    contract_date TEXT NOT NULL,
    contract_hour INTEGER NOT NULL,
    side TEXT NOT NULL,
    dam_price REAL NOT NULL,
    model_prob REAL NOT NULL,
    edge REAL NOT NULL,
    expected_solrad_mjm2 REAL,
    confidence INTEGER NOT NULL,
    scanned_at TEXT NOT NULL
);
```

## Per-Hour Market Dict Schema

`fetch_ercot_markets()` returns a list of dicts, one per hub × hour (up to 40):

```python
{
    "ticker": "BOPT-ERCOT-HB_WEST-260318-14",  # synthetic symbol
    "hub_key": "West",
    "hub_name": "HB_WEST",
    "city": "Midland",
    "lat": 31.99,
    "lon": -102.08,
    "solar_sensitivity": 0.35,
    "contract_date": "2026-03-18",
    "contract_hour": 14,          # Hour Ending
    "dam_price": 42.50,
    "_ercot_data": {              # passed through to signal model
        "hub_price": 42.50,
        "load_forecast": 45000,
        "solar_mw": 12000,
    },
}
```

## File Changes

### Keep as-is
- `ercot/auth.py` — token management
- `exchanges/ercot.py` — thin wrapper
- `dashboard/ercot_api.py` — endpoints same, data richer
- `dashboard/static/ercot.html` — works with existing endpoints

### Modify

**`config.py`**
- Add `ERCOT_SOLAR_HOURS = range(11, 19)` — HE11 through HE18 (10am-6pm CT)
- Existing hub definitions, seasonal norms, risk limits unchanged

**`pipeline/config.py`**
- Update ERCOT config: `settlement_timeline="hourly_binary"`, remove stale `exit_rules` dict, simplify `manage_fn` and `settle_fn` to point to `settle_expired_hours`

**`ercot/hubs.py`**
- New `fetch_dam_prices(hub_name: str) -> dict[int, float] | None` — hourly DAM prices per hub, returns None on failure
- New `fetch_rt_settlement(hub_name: str, hour: int, date: str) -> float | None` — avg of 4 RT intervals
- Refactor `fetch_ercot_markets()` to return per-hour contracts (list of dicts per schema above)

**`weather/multi_model.py`**
- Refactor `get_ercot_solar_signal()` to accept `contract_hour` and `dam_price` parameters
- Return `{"model_prob": float, "edge": float, "signal": str, "confidence": int, ...}` with P(RT >= DAM) semantics
- Add `_hourly_solar_curve(daily_solar: float, hour_ending: int, month: int) -> float` helper

**`pipeline/stages.py`**
- ERCOT scoring branch passes `contract_hour` and `dam_price` from market dict to forecast function
- Maps P(RT >= DAM) to standard Signal with `model_prob` and `side`

**`ercot/paper_trader.py`**
- Drop and recreate tables with new schema
- New `settle_expired_hours()` — binary settlement using RT SPP
- Update `open_position()` signature: takes `contract_hour`, `contract_date`, `dam_price`, `side`, `entry_price`, `model_prob`
- Update `write_scan_cache()` for new schema

**`ercot/position_manager.py`**
- Simplify: remove exit/fortify evaluation. Binary contracts auto-settle.
- `run_ercot_manager()` delegates to `settle_expired_hours()` from paper_trader

**`pipeline/runner.py`**
- Update ERCOT expiry call to use `settle_expired_hours()` instead of `expire_positions()`

### New files
None.

### Test updates

- `test_ercot_signal.py` — P(RT >= DAM) output format, hourly solar curve, logistic function, k parameter behavior
- `test_ercot_hubs.py` — DAM price fetch, RT settlement fetch, per-hour market discovery, None on failure
- `test_ercot_paper_trader.py` — binary settlement (RT >= DAM → $100, RT < DAM → $0), new schema
- `test_ercot_pipeline_signal.py` — hourly signal scoring through pipeline

## Success Criteria

1. DAM prices fetched and cached daily per hub (None on failure, no defaults)
2. Solar-hours contracts (HE11-HE18) scored with P(RT >= DAM) each cycle
3. Paper positions opened with hub/hour/DAM strike/side
4. Hourly auto-settlement against actual RT SPP
5. Dashboard shows hourly signals, positions, and settled P&L
6. After 2 weeks: hit rate > 50% on solar hours indicates signal has value

## Estimated Scope

~8 files modified, 0 new files, ~400-500 lines changed. Architecture unchanged — signal semantics and data granularity are the only shifts.
