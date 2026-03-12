# Phase 4: Precipitation Expansion — Design Spec

**Goal:** Extend the proven temperature trading pipeline to precipitation markets on Kalshi (monthly cumulative KXRAIN* series + daily binary), using a zero-inflated CSGD model with multi-model fusion and the existing Kelly sizing infrastructure.

**Scope:** Precipitation only. Snowfall (Phase 4b) deferred until precip is validated for 1-2 weeks.

**Prerequisite:** Phase 2 (Kelly sizing & risk management) — completed.

---

## Section 1: Architecture & Data Flow

The precip pipeline mirrors the temperature pipeline but with a distribution model tailored to skewed, zero-inflated data:

```
Open-Meteo Ensemble (30 members, precipitation_sum)
          |
GFS/HRRR (short-range) + NWS PoP + QPF
          | (parallel)
     precip_model.py
     +-- Empirical CDF baseline (count members > threshold)
     +-- Zero-inflated gamma / CSGD fit
          | (returns clean P(precip > threshold))
     multi_model.py (market_type="precip")
          +-- weighted fusion (Open-Meteo + NWS + optional HRRR)
          +-- ensemble spread as confidence feature
          +-- X signal layer (nowcasting correction, 5-25% adaptive weight)
          +-- per-city/month bias correction
          |
     market_types.py (parse KXRAIN* bucket -> exact threshold)
          |
     stations_config.py (city + market_type -> NOAA CLI station ID)
          |
     scanner.py (discover live KXRAIN* + daily binary markets, compute edge)
          |
     sizer.py -> trader.py (existing Kelly pipeline)
```

### Key Design Decisions

- **`market_type` parameter** threads through the entire pipeline: `"high_temp"`, `"low_temp"`, `"precip"`, (later `"snow"`).
- **`precip_model.py`** is standalone with two public methods:
  - `empirical_precip_prob(threshold)` — simple ensemble count (baseline for comparison)
  - `gamma_precip_prob(threshold)` — zero-inflated CSGD returning `P(precip > threshold)`
- **Monthly cumulative KXRAIN contracts:** sum remaining days of the month in the forecast call (Open-Meteo `precipitation_sum` handles this natively per ensemble member).
- **Daily binary (>0"):** handled identically — just pass `threshold=0.0`, result is `1 - p_dry`.
- **X signal layer** plugs directly into `multi_model.py` fusion (same adaptive weighting architecture built for temperature).
- **Station config** maps `(city, market_type)` -> exact NOAA station ID + CLI report link (curated once from Kalshi rules pages — high-edge settlement accuracy).

---

## Section 2: Precipitation Model (`weather/precip_model.py`)

Two public methods with the exact same interface so `multi_model.py` can swap them transparently:

```python
@dataclass
class PrecipForecast:
    p_dry: float
    shape: float              # gamma shape
    scale: float              # gamma scale
    shift: float = 0.0        # for CSGD
    prob_above: float
    fraction_above: float     # fraction of ensemble members above threshold (confidence signal)
    method: str               # "empirical" or "csgd"
```

> **Naming note:** `fraction_above` (not `spread`) to avoid collision with temperature ensemble spread (which is a degree range). In multi_model.py confidence scoring, this plays the same role as ensemble spread for temp — high agreement (>85% or <15%) = higher fusion weight.

### 1. Empirical CDF baseline (always available, used for A/B testing)

- Takes the 30 ensemble `precipitation_sum` values (already summed over remaining days for monthly contracts).
- `P(precip > X) = (number of members > X) / 30`
- `spread = fraction of members above threshold` (high/low agreement -> confidence signal for fusion).
- Dead simple, no fitting issues.

### 2. Censored Shifted Gamma Distribution (CSGD) — primary model

Replaces plain zero-inflated gamma for better robustness on skewed monthly totals.

- `p_dry = 0.7 * (ensemble_zeros / 30) + 0.3 * (1 - nws_pop)` (blended — reduces ensemble dry bias). **Note:** `nws_pop` from `get_nws_precip_forecast()` is normalized to [0, 1] scale before blending (NWS API returns percentage 0-100, divide by 100).
- Fit CSGD to the non-zero members only:
  - Use `scipy.stats.gamma.fit(data_nonzero, floc=0)` (forces location=0 for stability)
  - Or full CSGD with shift parameter (tiny extension)
- `P(precip > X) = (1 - p_dry) * (1 - gamma.cdf(X - shift, shape, scale))`
- Edge cases:
  - All members = 0 -> `P(>X) = 0` for any X > 0
  - Fewer than 3 non-zero members -> fallback to empirical CDF (fit unreliable)
  - `gamma.fit` raises or produces degenerate params (shape < 0.01 or scale > 1000) -> fallback to empirical CDF + log warning
- For daily binary (>0"): just pass `threshold=0.0` — becomes `1 - p_dry`.

### Test Vectors

- 30 members: 15 zeros, 15 values uniform in [0.5, 3.0], threshold=2.0 -> `P(>2.0)` should be roughly 0.15-0.25 (half the wet members above 2.0, weighted by p_dry)
- 30 members: all 0.0, threshold=0.5 -> `P(>0.5) = 0.0`
- 30 members: all > 0, threshold=0.0 -> `P(>0.0) = 1.0`
- 30 members: 25 zeros, 5 non-zero [0.1, 0.2, 0.3, 0.5, 1.0], threshold=0.25 -> `P(>0.25)` should be roughly 0.10 (3/5 of wet members above 0.25, times p_wet)

### Ensemble spread feature (for fusion in multi_model.py)

- `fraction_above = count of members above threshold / 30`
- High agreement (>85% or <15%) = higher weight in fusion (strong signal).

### Monthly cumulative handling

- Caller passes the exact remaining forecast window (Open-Meteo returns daily values per member).
- Sum across days per ensemble member first, then fit CSGD to the summed totals.
- This naturally captures accumulation uncertainty over the month.

The module returns a `PrecipForecast` dataclass so downstream code (fusion, logging, backtesting) can inspect everything.

---

## Section 3: Market Types & Scanner Expansion

### `kalshi/market_types.py` — New file

An enum + parser that unifies bucket handling across all weather types:

```python
from enum import Enum

class MarketType(Enum):
    HIGH_TEMP = "high_temp"
    LOW_TEMP = "low_temp"
    PRECIP = "precip"      # monthly cumulative + daily binary
    SNOW = "snow"          # Phase 4b
```

**`parse_precip_bucket(market: dict) -> tuple[float, float | None] | None`**

Returns `(low, high)` in the same shape as the existing `parse_kalshi_bucket()` for temperature. This lets precip markets flow through the same edge-computation pipeline. For precip "above X" contracts: returns `(X, None)` meaning ">= X inches". For daily binary (>0"): returns `(0.0, None)`.

- Primary: regex on ticker (e.g. `kxrainchim-26mar-4` or `-4.0`).
- Fallback: parse the market title / `yes_sub_title` ("Rain in Chicago this month above 4 inches" -> `(4.0, None)`).
- Examples (real 2026 tickers):
  - `kxrainchim-26mar-4` or title "Above 4 inches" -> `(4.0, None)`
  - `kxrainnyc-26mar11` (daily) -> `(0.0, None)`
  - Later snow: `kxnycsnowm-26mar-3` -> `(3.0, None)`

> **Note:** The existing `parse_kalshi_bucket()` in `kalshi/scanner.py` remains for temperature. `parse_precip_bucket()` is a separate function in `market_types.py`. Both return `Optional[tuple[float, float | None]]`.

**`detect_market_type(ticker: str) -> MarketType`**

- `kxrain*` or `KXRAIN*` -> PRECIP
- `kx*snow*` or `KX*SNOW*` -> SNOW
- Existing temp patterns unchanged.

### `kalshi/scanner.py` — Modifications

Add alongside existing temp series:

```python
PRECIP_SERIES = {
    "kxrainnycm": {"city": "nyc", "lat": 40.78, "lon": -73.97, "unit": "in"},
    "kxrainchim": {"city": "chicago", "lat": 41.98, "lon": -87.90, "unit": "in"},
    "kxrainlaxm": {"city": "la", ...},
    "kxrainseam": {"city": "seattle", ...},
    # Add more as discovered — or make fully dynamic via Kalshi /markets search
}
```

New function `get_kalshi_precip_markets()` (mirrors `get_kalshi_weather_markets()`):

- Calls Kalshi API filtered to precip series.
- For each market: `market_type = detect_market_type(...)`
- `threshold, bucket_type = parse_bucket(market)`
- Enriches with `_market_type="precip"`
- Calls `fuse_forecast(..., market_type="precip")`
- Feeds into the same edge -> sizer -> trader pipeline as temperature markets.

### `weather/stations_config.py` — New file (confirmed accurate March 2026)

```python
STATIONS = {
    ("nyc", "precip"): {"station": "USW00094728", "name": "Central Park", "report": "CLI"},
    ("chicago", "precip"): {"station": "USW00094846", "name": "O'Hare", "report": "CLI"},
    ("la", "precip"): {"station": "USW00023174", "name": "LAX", "report": "CLI"},
    # ... curated once from each market's rules page (~15 min)
}
```

Used for logging, settlement verification, and future auto-resolver. Not in the hot trading path.

---

## Section 4: Existing File Modifications

### `weather/forecast.py` — Add precipitation ensemble fetching

Add two new functions alongside the existing temperature ones:

```python
def get_ensemble_precip(lat: float, lon: float, forecast_days: int | None = None) -> list[float]:
    """Returns list of 30 ensemble precipitation_sum values in inches.
    Open-Meteo returns mm; convert to inches by dividing by 25.4. Kalshi markets settle in inches.
    If forecast_days set (monthly contracts): sum daily values across the window PER ensemble member.
    Fallback: return [0.0] * 30 on any error (matches existing temp functions).
    """
```

```python
def get_nws_precip_forecast(lat: float, lon: float) -> tuple[float, float]:
    """Returns (pop: float, qpf_inches: float).
    NWS is short-range (<7-10 days reliable); mainly for bias correction on monthly contracts.
    Parse probabilityOfPrecipitation and quantitativePrecipitation from existing NWS API response.
    """
```

Add a helper:

```python
def calculate_remaining_month_days(target_date=None) -> int:
    """Returns days left in current month (or until market close) for clean monthly logic."""
```

### `weather/multi_model.py` — The main routing change

**Migration strategy (backward-compatible):** Keep the existing `fuse_forecast()` signature unchanged for temperature callers. Add a new `fuse_precip_forecast()` function for the precipitation path. This avoids breaking the 2 existing call sites (`scanner.py` line 182, `kalshi/position_manager.py` line 147) while keeping the code clean. A unified `ForecastRequest` wrapper can be added later when all callers are migrated.

New function (does NOT replace existing `fuse_forecast`):

```python
@dataclass
class FusionResult:
    fused_prob: float       # P(precip > threshold)
    confidence: int         # 0-100
    details: dict           # per-model probs, spreads, biases (same structure as temp)

def fuse_precip_forecast(
    lat: float, lon: float, city: str, month: int,
    threshold: float, forecast_days: int | None = None,
) -> tuple[float, float, dict]:
    """Precipitation fusion. Returns (fused_prob, confidence, details) — same shape as fuse_forecast for temp."""
```

This returns the same `tuple[float, float, dict]` shape as the existing `fuse_forecast()` so the scanner edge-computation logic works identically.

When called:
- Calls `get_ensemble_precip(lat, lon, forecast_days)`
- Calls `get_nws_precip_forecast(lat, lon)` for PoP/QPF
- Routes to `precip_model.gamma_precip_prob(threshold=threshold)` (or empirical baseline)
- Apply X signal layer with adaptive weighting (5-25%) — limited to daily binary nowcasting initially; monthly contracts use model-only signals
- Use `fraction_above` + PoP agreement for confidence scoring
- Bias-correction key: `(city, month, "ensemble_precip")` and `(city, month, "noaa_precip")` — separate per-source tracking, not just `"precip"`
- Fusion weights from `config.py`: new `PRECIP_FUSION_WEIGHTS` entry (default: `{"ensemble": 0.50, "noaa": 0.30, "hrrr": 0.20}` — ensemble weighted higher since NWS PoP is less informative for monthly cumulative)

Existing `fuse_forecast()` for HIGH_TEMP / LOW_TEMP: **completely untouched**.

### `weather/cache.py` — Minor update

Cache keys must include market type to avoid collisions. When caching precip ensemble data, use key `f"ensemble_precip_{city}_{days_ahead}"` (distinct from temperature cache keys).

### `config.py` — Add precip fusion weights

```python
# Precipitation fusion weights (Phase 4)
PRECIP_FUSION_WEIGHTS = {"ensemble": 0.50, "noaa": 0.30, "hrrr": 0.20}
```

### `backtesting/scorer.py` — Minor extension only

- Add `market_type` column awareness in reporting functions (`pnl_by_city()`, calibration plots, etc.) so precip and temperature results are grouped separately.
- Core Brier / log-loss scoring functions need zero changes (already probability-agnostic).

### `scanner.py` (root) — Add precip market discovery

The root-level `scanner.py` orchestrates both Polymarket and Kalshi scanning. Currently calls `get_kalshi_weather_markets()` and `get_kalshi_low_temp_markets()`. Must also call `get_kalshi_precip_markets()` and merge results into the Kalshi market list for edge detection.

### `kalshi/position_manager.py` — Recognize precip tickers

The position manager uses `_ALL_SERIES` and `_parse_position_ticker` to look up cities for open positions. Must:
- Add `PRECIP_SERIES` to `_ALL_SERIES`
- Update `_parse_position_ticker` to recognize `KXRAIN*` tickers
- Route precip positions through `fuse_precip_forecast()` for mark-to-market in `evaluate_position()`
- Without this, precip positions will be unmanaged (no trailing stops, no exits)

### Monthly Cumulative Forecast Horizon Limitation

Open-Meteo ensemble covers ~16 days ahead. For monthly contracts queried early in the month (e.g., March 5 for a March 31 contract), the remaining 26 days exceed the ensemble horizon. **Strategy:** Only trade monthly contracts when remaining days <= 16 (ensemble horizon). For days beyond the horizon, accept reduced accuracy and note the limitation in signal logs. This is conservative but avoids introducing a climatology fallback before we have the data to calibrate it.

### `calculate_remaining_month_days` Clarification

```python
def calculate_remaining_month_days(market_close_date: date | None = None) -> int:
    """Days from today to market close (or end of month if close date unknown).
    market_close_date: parsed from ticker date tag or market API response.
    """
```

---

## Rollout Strategy

1. Add precip fields to existing Open-Meteo/NWS pipeline (minimal new API work)
2. Build `precip_model.py` with CSGD fitter + empirical baseline, test against historical
3. Map station IDs from Kalshi rules pages into `stations_config.py`
4. Extend scanner to discover KXRAIN* monthly + daily binary contracts
5. Run signal-only mode for 1-2 weeks to validate calibration
6. **Phase 4b:** Snowfall when validated — covered by a separate design spec. Same architecture but adds SLR (snow-to-liquid ratio) model.

**X signal note for precip:** X/Twitter integration for precipitation is limited to daily binary market nowcasting initially ("it's raining right now" = same-day correction). Monthly cumulative contracts rely on model-only signals until X signal quality is validated for multi-day accumulation estimates.

---

## Market Landscape Reference (Kalshi, March 2026)

| Type | Settlement Source | Bucket Examples | Active Cities |
|------|------------------|-----------------|---------------|
| Daily high/low temp | NOAA station (CLI) | "80-84F", "Above 85F" | 20+ cities |
| Daily rain (binary) | NOAA station precip gauge | >0.00 inches today | Several major cities |
| Monthly precipitation | NOAA GHCND + CLI | Above 1in, Above 3in | LA, NYC, SF, Miami, Houston, Chicago |
| Snowfall (monthly/event) | NWS CLI (specific station) | Above 0.1in, Above 3.0in | Northeast + Chicago (Phase 4b) |

Settlement always uses first complete report; revisions ignored. Always check exact market rules page for linked station/report.

**Scope note:** Precipitation expansion targets US cities only (where Kalshi operates). International cities in `config.py` CITIES list (Buenos Aires, London, Paris, Seoul, Tokyo) are Polymarket-only and remain temperature-only.
