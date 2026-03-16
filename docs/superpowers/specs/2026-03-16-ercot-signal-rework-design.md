# ERCOT Signal Rework: Per-Hub Fair Price Model

**Date:** 2026-03-16
**Status:** Approved

## Problem

The current ERCOT signal logic (`get_ercot_solar_signal()`) has three critical flaws:

1. **Signal ignores per-hub prices.** All 5 Texas hubs get the same `current_ercot_price` (HB_HOUSTON or grid average). The `hub_prices` dict is fetched but never piped per-hub.
2. **Edge is solrad-only, not price-based.** Fixed thresholds (`>18 MJ/m^2` = SHORT, `<10` = LONG) determine direction. Actual ERCOT price is fetched but unused in the signal logic.
3. **All hubs produce identical signals.** Texas hubs have nearly identical solar irradiance on any given day, so all 5 get the same signal, edge, and confidence. Only HB_WEST occasionally differs due to geographic distance.

Result: identical paper trades open on all hubs, sit static for 24 hours, and expire. No differentiation, no price-awareness.

## Solution: Solar-Adjusted Fair Price Model

### Core Formula

```
fair_price = current_hub_price × (1 + solar_impact + load_impact)
edge = (fair_price - current_hub_price) / current_hub_price
     = solar_impact + load_impact
```

Where:
- `solar_impact = solar_sensitivity[hub] × (seasonal_norm_solrad - expected_solrad) / seasonal_norm_solrad`
- `load_impact = load_sensitivity × (load_forecast - seasonal_norm_load) / seasonal_norm_load`

When solar is below seasonal norm, generation drops, prices should rise (positive impact). Above norm, prices drop (negative). Load works inversely: above-normal demand pushes prices up.

### Signal-to-Trade Mapping

- `edge > 0` → **LONG** → prices expected to rise → "yes" side in pipeline
- `edge < 0` → **SHORT** → prices expected to drop → "no" side in pipeline
- `edge == 0` → **NEUTRAL** → no trade

This aligns with the existing `pipeline/stages.py` line 46: `side = "no" if ercot_signal == "SHORT" else "yes"`.

### Per-Hub Solar Sensitivity

Each hub has a different sensitivity to solar deviation based on its generation mix:

| Hub | Sensitivity | Rationale |
|-----|------------|-----------|
| HB_WEST | 0.35 | Permian Basin, highest solar farm concentration |
| HB_PAN | 0.25 | Panhandle, significant wind+solar |
| HB_SOUTH | 0.20 | San Antonio region, growing solar |
| HB_NORTH | 0.15 | Dallas, large load center, gas-driven |
| HB_HOUSTON | 0.10 | Industrial load dominates, least solar-sensitive |

Load sensitivity: 0.15 (uniform across hubs initially — see Known Limitations).

### Seasonal Norms

Hardcoded by month, keyed as `ERCOT_SEASONAL_NORMS[month_number]`:

| Month | Solar (MJ/m^2) | Load (MW) | Source |
|-------|---------------|-----------|--------|
| 1 (Jan) | 10.0 | 50,000 | Winter low solar, heating load |
| 2 (Feb) | 12.0 | 47,000 | |
| 3 (Mar) | 16.0 | 45,000 | NREL typical, ERCOT historical |
| 4 (Apr) | 20.0 | 43,000 | |
| 5 (May) | 22.0 | 48,000 | |
| 6 (Jun) | 25.0 | 60,000 | Peak solar, AC ramp |
| 7 (Jul) | 26.0 | 70,000 | Peak everything |
| 8 (Aug) | 25.0 | 68,000 | |
| 9 (Sep) | 20.0 | 55,000 | |
| 10 (Oct) | 16.0 | 45,000 | |
| 11 (Nov) | 12.0 | 43,000 | |
| 12 (Dec) | 10.0 | 48,000 | Winter heating |

**Fallback:** If the current month has no entry (shouldn't happen with all 12 defined, but as a safety rail), send a Telegram alert and use annual averages: solar 18.0 MJ/m^2, load 50,000 MW.

### Per-Hub Price Routing

`fetch_ercot_markets()` already fetches all hub prices into `hub_prices` dict. Change: pass each hub's own price from `hub_prices[hub_name]` (e.g., `hub_prices["HB_WEST"]`) instead of the global average. Fallback chain: hub-specific price → grid average → $40 default.

### New Function Signature

```python
def get_ercot_solar_signal(
    lat: float, lon: float,
    hub_key: str,                    # NEW — e.g., "West"
    solar_sensitivity: float,        # NEW — e.g., 0.35
    hours_ahead: int = 24,
    ercot_data: dict | None = None,  # now must include "hub_price" for this hub
) -> dict:
```

`ercot_data` dict gains a `hub_price` field (the specific hub's settlement price) alongside the existing `price` (grid avg), `solar_mw`, and `load_forecast` fields.

### Dual Call Paths

Both paths must pass per-hub parameters:

1. **Pipeline path** (`pipeline/stages.py` `score_signal()`): The ERCOT branch at line 36 must pass `hub_key` and `solar_sensitivity` from the market dict. `fetch_ercot_markets()` will include these in each market dict.

2. **Direct path** (`ercot/hubs.py` `scan_all_hubs()`): Already iterates per hub. Must pass `hub_key` and `solar_sensitivity` from `ERCOT_HUBS[hub_key]` config.

### Dual-Source Solar with Unit Validation

Fetch BOTH Visual Crossing and Open-Meteo solrad (not try/fallback). Use VC as primary forecast value, OM as cross-reference for confidence.

**Unit validation:** Open-Meteo's `shortwave_radiation_sum` should be in MJ/m^2. After fetching, check the response's `daily_units.shortwave_radiation_sum` field. If it reports `kJ/m^2`, divide by 1000. If `Wh/m^2`, multiply by 0.0036. If the unit is missing or unrecognized, log a warning and skip the agreement check (still use VC value as primary).

The agreement threshold of 2 MJ/m^2 only applies after unit normalization.

### Confidence Model

Replace the binary `70 if edge > 0.50 else 50` with multi-factor:

```
confidence = base_conf + agreement_bonus + price_deviation_bonus

base_conf             = 30
agreement_bonus       = 20   (if VC and Open-Meteo solrad agree within 2 MJ/m^2)
price_deviation_bonus = min(30, abs(edge) * 300)
```

Capped at 90, floored at 30.

Range: minimum = 30 (no agreement, zero edge), maximum = 80 typical (30 + 20 + 30). Cap of 90 is a safety rail for future formula additions.

### Signal Direction

Derived from edge sign (no more magic thresholds):

```python
if edge > 0:    signal = "LONG"
elif edge < 0:  signal = "SHORT"
else:           signal = "NEUTRAL"
```

### Edge Gate Recalibration

The old `edge_gate=0.005` was tuned for the previous 0.0-0.99 edge range. With the new model, edges will typically be -0.10 to +0.10. New gate: **`edge_gate=0.03`** — requires ~8.5% solar deviation on HB_WEST or ~20% on HB_HOUSTON (or combined solar+load deviations) to trigger. This filters out noise while still catching meaningful mispricings.

Update in `pipeline/config.py` ERCOT config and `config.py` `ERCOT_MIN_EDGE`.

### Pipeline `model_prob` Handling

The current `score_signal()` computes `model_prob = 1.0 - edge` which made sense when edge was 0-1. With the new small-range edge, this always produces ~1.0. Fix: for ERCOT signals, set `model_prob = 0.5 + edge` (centered at 50%, adjusted by edge). This gives a meaningful probability-like value that the rest of the pipeline can work with, and preserves the `edge = model_prob - market_prob` relationship when `market_prob = 0.5` (which is what `_extract_market_prob` already returns for ERCOT at line 476 of stages.py).

## Files Changed

| File | Change |
|------|--------|
| `weather/multi_model.py` | Rewrite `get_ercot_solar_signal()` — new signature, fair price model, dual-source solrad with unit validation, per-hub sensitivity |
| `ercot/hubs.py` | `fetch_ercot_markets()` passes per-hub price + hub_key + solar_sensitivity in market dict; `scan_all_hubs()` passes hub_key and solar_sensitivity |
| `config.py` | Add `ERCOT_SEASONAL_NORMS` (all 12 months), `ERCOT_LOAD_SENSITIVITY`, add `solar_sensitivity` to each `ERCOT_HUBS` entry |
| `pipeline/stages.py` | Update ERCOT branch: pass hub_key/solar_sensitivity, fix model_prob to `0.5 + edge` |
| `pipeline/config.py` | Update `edge_gate` from 0.005 to 0.03 |

## Files NOT Changed

- `ercot/paper_trader.py` — consumes whatever signal provides
- `ercot/position_manager.py` — evaluates based on signal edge, unchanged
- Dashboard endpoints — read from scan_cache, shape unchanged
- Kalshi pipeline — completely separate
- DB schema — existing columns sufficient (`expected_solrad_mjm2`, `current_ercot_price`, `confidence`)

## Known Limitations / Future Work

1. **Uniform load sensitivity.** HB_HOUSTON and HB_NORTH are major load centers and experience disproportionate congestion pricing during high-load periods. Per-hub load sensitivity should be calibrated from paper trade results.
2. **Seasonal norms are estimates.** Initial values are from public references. After accumulating paper trade data across months, these should be refined with observed data.
3. **Solar sensitivity coefficients are estimates.** These reflect the relative solar generation mix per hub but will need tuning from paper trading P&L. The paper trading period is specifically for calibrating these.
4. **No wind signal yet.** HB_PAN and HB_WEST have significant wind generation. A future iteration could add wind forecast as a third impact term.

## Expected Outcomes

1. Each hub generates a different edge based on its own price and solar sensitivity
2. HB_WEST (high solar sensitivity, often low price) will trade very differently from HB_HOUSTON (low sensitivity, higher price)
3. Positions that open will have price-justified edge, not just weather-justified
4. Confidence varies based on forecast agreement, not just edge magnitude
5. Edge gate filters out noise signals while still catching real mispricings
