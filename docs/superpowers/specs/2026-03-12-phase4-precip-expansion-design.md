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
    shape: float          # gamma shape
    scale: float          # gamma scale
    shift: float = 0.0    # for CSGD
    prob_above: float
    spread: float         # fraction of members above threshold
    method: str
```

### 1. Empirical CDF baseline (always available, used for A/B testing)

- Takes the 30 ensemble `precipitation_sum` values (already summed over remaining days for monthly contracts).
- `P(precip > X) = (number of members > X) / 30`
- `spread = fraction of members above threshold` (high/low agreement -> confidence signal for fusion).
- Dead simple, no fitting issues.

### 2. Censored Shifted Gamma Distribution (CSGD) — primary model

Replaces plain zero-inflated gamma for better robustness on skewed monthly totals.

- `p_dry = 0.7 * (ensemble_zeros / 30) + 0.3 * NWS_PoP` (blended — reduces ensemble dry bias)
- Fit CSGD to the non-zero members only:
  - Use `scipy.stats.gamma.fit(data_nonzero, floc=0)` (forces location=0 for stability)
  - Or full CSGD with shift parameter (tiny extension)
- `P(precip > X) = (1 - p_dry) * (1 - gamma.cdf(X - shift, shape, scale))`
- Edge cases:
  - All members = 0 -> `P(>X) = 0` for any X > 0
  - Fewer than 3 non-zero members -> fallback to empirical CDF (fit unreliable)
- For daily binary (>0"): just pass `threshold=0.0` — becomes `1 - p_dry`.

### Ensemble spread feature (for fusion in multi_model.py)

- `spread = fraction of members above threshold`
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

**`parse_bucket(market: dict) -> (threshold: float, bucket_type: str)`**

- Handles both series-level and individual contracts.
- Primary: regex on ticker (e.g. `kxrainchim-26mar-4` or `-4.0`).
- Fallback: parse the market title / `yes_sub_title` ("Rain in Chicago this month above 4 inches" -> `(4.0, "above")`).
- Examples (real 2026 tickers):
  - `kxrainchim-26mar-4` or title "Above 4 inches" -> `(4.0, "above")`
  - `kxrainnyc-26mar11` (daily) -> `(0.0, "above")`
  - Later snow: `kxnycsnowm-26mar-3` -> `(3.0, "above")`

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

Introduce a lightweight dataclass:

```python
@dataclass
class ForecastRequest:
    lat: float
    lon: float
    city: str
    market_type: MarketType = MarketType.HIGH_TEMP
    threshold: float | None = None          # For "Above X" precip/snow
    forecast_days: int | None = None        # Remaining days for monthly cumulative
    # Legacy temp fields (low, high, etc.) stay as **kwargs or optional for backward compatibility
```

Update the main function:

```python
def fuse_forecast(request: ForecastRequest) -> FusionResult:
```

When `market_type == MarketType.PRECIP`:
- Call `get_ensemble_precip(request.lat, request.lon, request.forecast_days)`
- Call `get_nws_precip_forecast()`
- Route to `precip_model.gamma_precip_prob(threshold=request.threshold)` (or empirical baseline)
- Apply X signal layer with adaptive weighting (5-25%)
- Use ensemble spread + PoP agreement for confidence scoring
- Bias-correction key becomes `(city, month, "precip")`

When `market_type` is HIGH_TEMP / LOW_TEMP: original code path runs exactly as before (untouched).

### `backtesting/scorer.py` — Minor extension only

- Add `market_type` column awareness in reporting functions (`pnl_by_city()`, calibration plots, etc.) so precip and temperature results are grouped separately.
- Core Brier / log-loss scoring functions need zero changes (already probability-agnostic).

---

## Rollout Strategy

1. Add precip fields to existing Open-Meteo/NWS pipeline (minimal new API work)
2. Build `precip_model.py` with CSGD fitter + empirical baseline, test against historical
3. Map station IDs from Kalshi rules pages into `stations_config.py`
4. Extend scanner to discover KXRAIN* monthly + daily binary contracts
5. Run signal-only mode for 1-2 weeks to validate calibration
6. **Phase 4b:** Snowfall when validated — same pattern but add SLR model

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
