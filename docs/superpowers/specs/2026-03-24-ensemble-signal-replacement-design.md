# Ensemble Signal Replacement — Design Spec

## Problem

The current 5-model fusion engine (`weather/multi_model.py`, 1400+ lines) produces unreliable probability estimates for temperature bucket contracts. It requires bias correction that is fragile (corrupted by repeated backfills), produces phantom 40-50% edges that are actually coin flips, and bets on 2°F buckets that weather models cannot reliably predict. Result: 50% win rate, 100% taker orders, guaranteed loss after fees.

## Solution

Replace the scoring model with 31-member GFS ensemble member counting from Open-Meteo. Trade only threshold (T-type) and precipitation contracts where forecast precision matches contract structure. Maker orders only.

## Signal Generation

New file: `weather/ensemble_signal.py` (~150 lines)

For each contract:
1. Fetch 31-member GFS ensemble from `https://ensemble-api.open-meteo.com/v1/ensemble` for the target city/date
2. Count how many members exceed the contract threshold
3. `model_prob = members_above / total_members` (clipped to 0.05-0.95)
4. `edge = model_prob - market_price`
5. Signal passes if `abs(edge) >= 0.08` (8%)

No bias correction. No multi-model fusion. Single data source, single API call per city/date.

The function signature must match what `pipeline/stages.py:score_signal` expects from `config.forecast_fn`. It receives market metadata (lat, lon, city, threshold, date info) and returns `(model_prob, confidence, details_dict)` — same shape as `fuse_forecast`.

Confidence = ensemble agreement: `max(above_count, below_count) / total_members`, clipped to 0.90.

### Ensemble Data

- Source: Open-Meteo Ensemble API (free, no key needed)
- Model: `gfs_seamless` (31 members)
- Data: `temperature_2m_max` and `temperature_2m_min` per member
- Returns member-level daily values, not just the mean
- Cache per city/date with 15-minute TTL (same forecast doesn't change faster than that)

## Contract Filtering

In `pipeline/stages.py:filter_signals`, add a filter:
- **Skip B-type tickers** — any ticker with `-B` in the strike portion
- **Pass T-type tickers** — threshold contracts ("above/below X°F")
- **Pass precip tickers** — `RAIN` series (unchanged)

This is a 2-line addition to the existing filter function.

## Pipeline Integration

### `pipeline/config.py`
- Change `kalshi_temp` config's `forecast_fn` from `fuse_forecast` to the new ensemble function
- Change `kelly_floor` from 0.25 to 0.15 (more conservative, matches reference bot)

### `pipeline/stages.py`
- `score_signal`: no changes needed if the new function returns the same `(prob, confidence, details)` tuple
- `execute_trade`: remove `choose_price_strategy` call, always use maker pricing
- `filter_signals`: add B-type ticker skip

### `kalshi/pricing.py`
- Add `maker_price(side, yes_bid, yes_ask)` function that returns bid+1 (for YES) or 100-ask+1 (for NO), with fallback to ask-2 if no bid. No taker path.

## Pricing — Maker Only

Every order is a resting limit order:
- YES side: post at `yes_bid + 1` (one tick inside the spread)
- NO side: post at `(100 - yes_ask) + 1`
- If no bid exists: post at `yes_ask - 2` (conservative)
- All orders are maker = zero Kalshi fees

Remove the `choose_price_strategy` function from the trading path. The `_compress_edge_for_pricing` function becomes dead code.

## Position Management

- **Multi-day contracts (days_ahead >= 1):** Hold to settlement. No intraday exits.
- **Same-day contracts (days_ahead == 0):** After 4pm local time for the contract's city, if the position is profitable based on current market price, exit at market (sell at bid). This prevents the "ITM at 3pm, OTM at settlement" pattern.

This logic goes in `kalshi/position_manager.py:evaluate_position`.

## Kelly Sizing

- Fractional Kelly: 0.15 (was 0.25)
- Max position: 5% of bankroll per trade
- Max trade size: $100 (from reference bot config)

Change in `pipeline/config.py` kalshi_temp config: `kelly_floor=0.15`

## What Stays the Same

- All 14 cities (WEATHER_SERIES in kalshi/scanner.py)
- Dashboard, health check, settler, fill tracker, trailing stops
- trades.db schema (including new edge + confidence columns)
- Precip scoring (fuse_precip_forecast — already uses ensemble, works)
- Sanity checks (weather/sanity.py)
- ERCOT/PJM/CAISO configs (unchanged, broken ones stay broken)

## What Gets Bypassed (Not Deleted)

- `weather/multi_model.py:fuse_forecast` — no longer called for temp, kept for reference
- Bias correction (`data/bias.db`) — not needed for raw member counting
- Edge compression (`_compress_edge_for_pricing`) — not needed with realistic edges
- 4-model minimum gate — only one model now
- `choose_price_strategy` — replaced by maker-only pricing

## Files Changed

| File | Change |
|------|--------|
| `weather/ensemble_signal.py` | **New** — ensemble member counting signal generator |
| `pipeline/config.py` | Point `forecast_fn` at new function, kelly_floor=0.15 |
| `pipeline/stages.py` | Skip B-type tickers in filter, maker-only in execute |
| `kalshi/pricing.py` | Add `maker_price()`, keep old functions for reference |
| `kalshi/position_manager.py` | Add same-day afternoon exit logic |

## Testing

- Unit test for ensemble_signal: mock API response, verify probability calculation
- Unit test for B-type filter: verify B-type tickers are skipped, T-type pass
- Unit test for maker pricing: verify bid+1 logic
- Integration: deploy to server, verify daemon produces trades with strategy="maker" and realistic edges (8-20%)
- Smoke test: existing test_import_smoke.py passes

## Success Criteria

- Edges in 8-20% range (not 40-50%)
- Strategy column shows "maker" (not "taker")
- No B-type bucket trades
- Win rate on threshold contracts > 55% over 30+ trades
- Net P&L positive (zero fees helps significantly)
