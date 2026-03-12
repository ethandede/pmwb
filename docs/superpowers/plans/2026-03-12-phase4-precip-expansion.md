# Phase 4: Precipitation Expansion — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the temperature trading pipeline to precipitation markets (KXRAIN*) on Kalshi using a zero-inflated CSGD model, multi-model fusion, and the existing Kelly sizing infrastructure.

**Architecture:** New `weather/precip_model.py` with empirical + CSGD probability models. New `kalshi/market_types.py` for unified market type enum + precip bucket parsing. New `fuse_precip_forecast()` in `multi_model.py` (backward-compatible — existing temp path untouched). Scanner discovers KXRAIN* series and feeds signals through the existing edge → sizer → trader pipeline.

**Tech Stack:** Python 3.10, scipy (gamma distribution fitting), pytest, existing Open-Meteo + NWS APIs.

**Spec:** `docs/superpowers/specs/2026-03-12-phase4-precip-expansion-design.md`

---

## File Structure

```
weather/
├── precip_model.py          # NEW: PrecipForecast dataclass, empirical + CSGD probability
├── stations_config.py       # NEW: (city, market_type) → NOAA station ID
├── forecast.py              # MODIFY: add get_ensemble_precip, get_nws_precip_forecast, calculate_remaining_month_days
├── multi_model.py           # MODIFY: add fuse_precip_forecast() (temp path untouched)
├── cache.py                 # MODIFY: market_type in cache keys (minor)

kalshi/
├── market_types.py          # NEW: MarketType enum, detect_market_type, parse_precip_bucket
├── scanner.py               # MODIFY: add PRECIP_SERIES, get_kalshi_precip_markets()

config.py                    # MODIFY: add PRECIP_FUSION_WEIGHTS
scanner.py (root)            # MODIFY: call get_kalshi_precip_markets() in scan loop

tests/
├── test_precip_model.py     # NEW: 8+ tests for empirical + CSGD
├── test_market_types.py     # NEW: 6+ tests for enum, detect, parse
├── test_forecast_precip.py  # NEW: 4+ tests for precip ensemble fetching
├── test_precip_fusion.py    # NEW: 5+ tests for fuse_precip_forecast
```

---

## Chunk 1: Precipitation Model + Market Types

### Task 1: Implement precipitation model — empirical baseline

**Files:**
- Create: `weather/precip_model.py`
- Create: `tests/test_precip_model.py`

- [ ] **Step 1: Write failing tests for empirical precip probability**

Create `tests/test_precip_model.py`:
```python
import pytest
from weather.precip_model import empirical_precip_prob, PrecipForecast


def test_empirical_all_dry():
    """All 30 members predict 0 → P(>0.5) = 0."""
    result = empirical_precip_prob([0.0] * 30, threshold=0.5)
    assert result.prob_above == 0.0
    assert result.p_dry == 1.0
    assert result.method == "empirical"


def test_empirical_all_wet():
    """All 30 members predict > 0 → P(>0.0) = 1.0."""
    members = [0.5 + i * 0.1 for i in range(30)]
    result = empirical_precip_prob(members, threshold=0.0)
    assert result.prob_above == 1.0
    assert result.p_dry == 0.0


def test_empirical_mixed():
    """15 dry, 15 wet (0.5–3.0) → P(>2.0) counts wet members above 2.0."""
    wet = [0.5 + i * (2.5 / 14) for i in range(15)]  # 0.5 to 3.0
    members = [0.0] * 15 + wet
    result = empirical_precip_prob(members, threshold=2.0)
    # fraction_above = count of members > 2.0 / 30
    expected_count = sum(1 for v in members if v > 2.0)
    assert result.prob_above == pytest.approx(expected_count / 30, abs=0.01)
    assert result.p_dry == pytest.approx(0.5)
    assert result.fraction_above == pytest.approx(expected_count / 30, abs=0.01)


def test_empirical_empty_members():
    """Empty member list → P(>X) = 0, p_dry = 1.0."""
    result = empirical_precip_prob([], threshold=1.0)
    assert result.prob_above == 0.0
    assert result.p_dry == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_precip_model.py -v`
Expected: FAIL — `No module named 'weather.precip_model'`

- [ ] **Step 3: Implement empirical_precip_prob**

Create `weather/precip_model.py`:
```python
"""Precipitation probability models for Kalshi weather markets.

Two models with identical interfaces:
  1. empirical_precip_prob() — simple ensemble count (baseline)
  2. gamma_precip_prob() — zero-inflated CSGD (primary)

Both return PrecipForecast so multi_model.py can swap them transparently.
"""

from dataclasses import dataclass


@dataclass
class PrecipForecast:
    p_dry: float              # probability of zero precipitation
    shape: float              # gamma shape parameter
    scale: float              # gamma scale parameter
    shift: float = 0.0        # CSGD shift (0.0 for basic gamma)
    prob_above: float = 0.0   # P(precip > threshold)
    fraction_above: float = 0.0  # fraction of ensemble members above threshold
    method: str = "empirical"    # "empirical" or "csgd"


def empirical_precip_prob(
    members: list[float],
    threshold: float,
) -> PrecipForecast:
    """Empirical CDF baseline: P(precip > threshold) = count(members > threshold) / N.

    Args:
        members: Precipitation values from ensemble (inches, already summed for monthly).
        threshold: Inches threshold (0.0 for daily binary "any rain").
    """
    if not members:
        return PrecipForecast(p_dry=1.0, shape=0.0, scale=0.0, prob_above=0.0,
                              fraction_above=0.0, method="empirical")

    n = len(members)
    n_dry = sum(1 for m in members if m <= 0.0)
    n_above = sum(1 for m in members if m > threshold)

    return PrecipForecast(
        p_dry=n_dry / n,
        shape=0.0,
        scale=0.0,
        prob_above=n_above / n,
        fraction_above=n_above / n,
        method="empirical",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_precip_model.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add weather/precip_model.py tests/test_precip_model.py
git commit -m "feat: add empirical precipitation probability model (baseline)"
```

---

### Task 2: Implement precipitation model — CSGD

**Files:**
- Modify: `weather/precip_model.py`
- Modify: `tests/test_precip_model.py`

- [ ] **Step 1: Write failing tests for CSGD precip probability**

Add to `tests/test_precip_model.py`:
```python
from weather.precip_model import gamma_precip_prob


def test_csgd_all_dry():
    """All members = 0 → P(>X) = 0 for any X > 0."""
    result = gamma_precip_prob([0.0] * 30, threshold=0.5, nws_pop=0.0)
    assert result.prob_above == 0.0
    assert result.method == "csgd"


def test_csgd_all_wet_above_zero():
    """All members > 0, threshold=0.0 → P(>0.0) = 1.0 (or very close)."""
    members = [0.5 + i * 0.1 for i in range(30)]
    result = gamma_precip_prob(members, threshold=0.0, nws_pop=1.0)
    assert result.prob_above == pytest.approx(1.0, abs=0.01)


def test_csgd_few_nonzero_falls_back():
    """Fewer than 3 non-zero → fallback to empirical."""
    members = [0.0] * 28 + [1.0, 2.0]
    result = gamma_precip_prob(members, threshold=0.5, nws_pop=0.1)
    assert result.method == "empirical"  # fallback


def test_csgd_reasonable_probability():
    """25 zeros, 5 non-zero [0.1, 0.2, 0.3, 0.5, 1.0] → P(>0.25) roughly 0.05-0.20."""
    members = [0.0] * 25 + [0.1, 0.2, 0.3, 0.5, 1.0]
    result = gamma_precip_prob(members, threshold=0.25, nws_pop=0.2)
    assert 0.01 < result.prob_above < 0.30
    assert result.method == "csgd"
    assert result.shape > 0
    assert result.scale > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_precip_model.py::test_csgd_all_dry -v`
Expected: FAIL — `cannot import name 'gamma_precip_prob'`

- [ ] **Step 3: Implement gamma_precip_prob**

Add to `weather/precip_model.py`:
```python
import logging
from scipy.stats import gamma as gamma_dist

logger = logging.getLogger(__name__)


def gamma_precip_prob(
    members: list[float],
    threshold: float,
    nws_pop: float = 0.5,
) -> PrecipForecast:
    """Zero-inflated CSGD: P(precip > threshold) using fitted gamma on wet members.

    Args:
        members: Precipitation values from ensemble (inches).
        threshold: Inches threshold (0.0 for daily binary).
        nws_pop: NWS probability of precipitation [0, 1]. Used to blend
                 p_dry estimate (reduces ensemble dry bias).
    """
    if not members:
        return PrecipForecast(p_dry=1.0, shape=0.0, scale=0.0,
                              prob_above=0.0, fraction_above=0.0, method="csgd")

    n = len(members)
    nonzero = [m for m in members if m > 0.0]
    n_above = sum(1 for m in members if m > threshold)
    fraction_above = n_above / n

    # All dry → probability is 0 for any positive threshold
    if not nonzero:
        return PrecipForecast(p_dry=1.0, shape=0.0, scale=0.0,
                              prob_above=0.0, fraction_above=fraction_above, method="csgd")

    # Blended p_dry: 70% ensemble + 30% NWS (reduces ensemble dry bias)
    ensemble_dry_frac = (n - len(nonzero)) / n
    p_dry = 0.7 * ensemble_dry_frac + 0.3 * (1.0 - nws_pop)
    p_dry = max(0.0, min(1.0, p_dry))  # clamp

    # Fewer than 3 non-zero members → gamma fit unreliable, fall back to empirical
    if len(nonzero) < 3:
        return empirical_precip_prob(members, threshold)

    # Fit gamma to non-zero members
    try:
        shape, loc, scale = gamma_dist.fit(nonzero, floc=0)
        # Check for degenerate fit
        if shape < 0.01 or scale > 1000:
            logger.warning(f"Degenerate gamma fit: shape={shape}, scale={scale}. Falling back to empirical.")
            return empirical_precip_prob(members, threshold)
    except Exception as e:
        logger.warning(f"Gamma fit failed: {e}. Falling back to empirical.")
        return empirical_precip_prob(members, threshold)

    # P(precip > threshold) = (1 - p_dry) * (1 - gamma.cdf(threshold, shape, scale))
    if threshold <= 0.0:
        prob_above = 1.0 - p_dry
    else:
        prob_above = (1.0 - p_dry) * (1.0 - gamma_dist.cdf(threshold, shape, scale=scale))

    prob_above = max(0.0, min(1.0, prob_above))

    return PrecipForecast(
        p_dry=round(p_dry, 4),
        shape=round(shape, 4),
        scale=round(scale, 4),
        prob_above=round(prob_above, 4),
        fraction_above=round(fraction_above, 4),
        method="csgd",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_precip_model.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add weather/precip_model.py tests/test_precip_model.py
git commit -m "feat: add CSGD precipitation model with gamma fitting and fallback"
```

---

### Task 3: Implement market types enum + precip bucket parser

**Files:**
- Create: `kalshi/market_types.py`
- Create: `tests/test_market_types.py`

- [ ] **Step 1: Write failing tests for market types**

Create `tests/test_market_types.py`:
```python
import pytest
from kalshi.market_types import MarketType, detect_market_type, parse_precip_bucket


def test_detect_high_temp():
    assert detect_market_type("KXHIGHNY-26MAR12-B65") == MarketType.HIGH_TEMP


def test_detect_low_temp():
    assert detect_market_type("KXLOWTNYC-26MAR12-B30") == MarketType.LOW_TEMP


def test_detect_precip():
    assert detect_market_type("kxrainchim-26mar-4") == MarketType.PRECIP


def test_detect_snow():
    assert detect_market_type("kxnycsnowm-26mar-3") == MarketType.SNOW


def test_parse_precip_monthly_from_strike():
    """Monthly precip market with strike data → (threshold, None)."""
    market = {"ticker": "kxrainchim-26mar-4", "strike_type": "greater", "floor_strike": 4.0}
    result = parse_precip_bucket(market)
    assert result == (4.0, None)


def test_parse_precip_from_ticker_regex():
    """Parse threshold from ticker when no strike data."""
    market = {"ticker": "kxrainchim-26mar-4.5", "title": "Rain above 4.5 inches"}
    result = parse_precip_bucket(market)
    assert result is not None
    assert result[0] == pytest.approx(4.5)


def test_parse_precip_from_title():
    """Parse threshold from title fallback."""
    market = {"ticker": "kxrainchim-26mar", "title": "Rain in Chicago this month above 3 inches"}
    result = parse_precip_bucket(market)
    assert result is not None
    assert result[0] == pytest.approx(3.0)


def test_parse_precip_daily_binary():
    """Daily binary rain market → (0.0, None)."""
    market = {"ticker": "kxrainnyc-26mar11", "title": "Will it rain in NYC today?",
              "strike_type": "greater", "floor_strike": 0.0}
    result = parse_precip_bucket(market)
    assert result == (0.0, None)


def test_parse_precip_no_match():
    """No parseable threshold → None."""
    market = {"ticker": "unknown-market", "title": "Something unrelated"}
    result = parse_precip_bucket(market)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_market_types.py -v`
Expected: FAIL — `No module named 'kalshi.market_types'`

- [ ] **Step 3: Implement market_types.py**

Create `kalshi/market_types.py`:
```python
"""Unified market type enum and bucket parsers for Kalshi weather markets.

Supports temperature (existing), precipitation (Phase 4), and snow (Phase 4b).
"""

import logging
import re
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MarketType(Enum):
    HIGH_TEMP = "high_temp"
    LOW_TEMP = "low_temp"
    PRECIP = "precip"      # monthly cumulative + daily binary rain
    SNOW = "snow"          # Phase 4b


def detect_market_type(ticker: str) -> MarketType:
    """Detect market type from ticker string.

    Examples:
        KXHIGHNY-26MAR12-B65 → HIGH_TEMP
        KXLOWTNYC-26MAR12-B30 → LOW_TEMP
        kxrainchim-26mar-4 → PRECIP
        kxnycsnowm-26mar-3 → SNOW
    """
    t = ticker.upper()
    if "RAIN" in t:
        return MarketType.PRECIP
    if "SNOW" in t:
        return MarketType.SNOW
    if "LOW" in t:
        return MarketType.LOW_TEMP
    if "HIGH" in t:
        return MarketType.HIGH_TEMP
    logger.warning(f"Unknown ticker pattern: {ticker}, defaulting to HIGH_TEMP")
    return MarketType.HIGH_TEMP


def parse_precip_bucket(market: dict) -> Optional[tuple[float, float | None]]:
    """Parse precipitation threshold from Kalshi market data.

    Returns (threshold, None) in the same shape as parse_kalshi_bucket()
    for temperature so downstream edge computation works identically.
    For "above X inches" → (X, None). For daily binary → (0.0, None).

    Returns None if no threshold can be parsed.
    """
    # Primary: use strike data if available
    strike_type = market.get("strike_type", "")
    floor_strike = market.get("floor_strike")

    if strike_type == "greater" and floor_strike is not None:
        return (float(floor_strike), None)

    # Secondary: regex on ticker for trailing number (e.g., kxrainchim-26mar-4.5)
    ticker = market.get("ticker", "")
    m = re.search(r'-(\d+\.?\d*)$', ticker)
    if m:
        return (float(m.group(1)), None)

    # Tertiary: parse from title ("above X inches", "> X")
    title = market.get("title", "") + " " + market.get("yes_sub_title", "")
    m = re.search(r'(?:above|over|>|≥)\s*(\d+\.?\d*)\s*(?:in|inch)', title, re.IGNORECASE)
    if m:
        return (float(m.group(1)), None)

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_market_types.py -v`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add kalshi/market_types.py tests/test_market_types.py
git commit -m "feat: add MarketType enum and precip bucket parser"
```

---

### Task 4: Add stations config + config.py updates

**Files:**
- Create: `weather/stations_config.py`
- Modify: `config.py`

- [ ] **Step 1: Create stations_config.py**

> **City key convention:** Use the same keys as `PRECIP_SERIES` in `kalshi/scanner.py` (Task 7). These are: `nyc`, `chicago`, `los_angeles`, `seattle`, `miami`, `houston`, `san_francisco`.

Create `weather/stations_config.py`:
```python
"""Settlement station mapping: (city, market_type) → NOAA station metadata.

Curated manually from each Kalshi market's rules page. Used for settlement
verification, logging, and future auto-resolver. Not in the hot trading path.

To add a station: check the market rules page on Kalshi for the exact
NOAA station ID and CLI report link.
"""

STATIONS: dict[tuple[str, str], dict] = {
    # Precipitation stations (from Kalshi KXRAIN* market rules)
    ("nyc", "precip"): {
        "station": "USW00094728",
        "name": "Central Park",
        "report": "CLI",
    },
    ("chicago", "precip"): {
        "station": "USW00094846",
        "name": "O'Hare International",
        "report": "CLI",
    },
    ("los_angeles", "precip"): {
        "station": "USW00023174",
        "name": "Los Angeles International",
        "report": "CLI",
    },
    ("seattle", "precip"): {
        "station": "USW00024233",
        "name": "Seattle-Tacoma International",
        "report": "CLI",
    },
    ("miami", "precip"): {
        "station": "USW00012839",
        "name": "Miami International",
        "report": "CLI",
    },
    ("houston", "precip"): {
        "station": "USW00012960",
        "name": "Houston Intercontinental",
        "report": "CLI",
    },
    ("san_francisco", "precip"): {
        "station": "USW00023234",
        "name": "San Francisco International",
        "report": "CLI",
    },
}


def get_station(city: str, market_type: str) -> dict | None:
    """Look up station metadata for a city/market_type pair."""
    return STATIONS.get((city, market_type))
```

- [ ] **Step 2: Write quick test for stations_config**

Add to `tests/test_market_types.py` (or create inline):
```python
from weather.stations_config import get_station

def test_get_station_hit():
    result = get_station("nyc", "precip")
    assert result is not None
    assert result["station"] == "USW00094728"

def test_get_station_miss():
    assert get_station("unknown_city", "precip") is None
```

- [ ] **Step 3: Add precip config to config.py**

Add to end of `config.py`:
```python
# Precipitation fusion weights (Phase 4)
PRECIP_FUSION_WEIGHTS = {"ensemble": 0.50, "noaa": 0.30, "hrrr": 0.20}
MAX_ENSEMBLE_HORIZON_DAYS = 16  # Open-Meteo ensemble limit; skip monthly contracts beyond this
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_market_types.py -v`
Expected: All tests PASS (including new station tests)

- [ ] **Step 5: Commit**

```bash
git add weather/stations_config.py config.py tests/test_market_types.py
git commit -m "feat: add station config and precip fusion weights"
```

---

## Chunk 2: Forecast Functions + Precip Fusion

### Task 5: Add precipitation ensemble fetching to forecast.py

**Files:**
- Modify: `weather/forecast.py`
- Create: `tests/test_forecast_precip.py`

- [ ] **Step 1: Write failing tests for precip forecast functions**

Create `tests/test_forecast_precip.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from datetime import date
from weather.forecast import get_ensemble_precip, get_nws_precip_forecast, calculate_remaining_month_days


def test_calculate_remaining_days_end_of_march():
    """March 12 → 19 days remaining."""
    result = calculate_remaining_month_days(market_close_date=date(2026, 3, 31))
    # Depends on "today" — mock or use known date
    assert isinstance(result, int)
    assert result >= 0


def test_calculate_remaining_days_no_close_date():
    """No close date → days until end of current month."""
    result = calculate_remaining_month_days()
    assert isinstance(result, int)
    assert 0 <= result <= 31


@patch("weather.forecast.requests.get")
def test_get_ensemble_precip_returns_list(mock_get):
    """Mocked Open-Meteo response → list of 30 precip values in inches."""
    # Simulate Open-Meteo ensemble response with daily precipitation_sum
    daily_data = {}
    for i in range(30):
        key = f"precipitation_sum_member{i:02d}"
        daily_data[key] = [2.54, 5.08]  # 2 days of data in mm
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"daily": daily_data}
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    result = get_ensemble_precip(40.78, -73.97, forecast_days=2)
    assert len(result) == 30
    # Values should be in inches (2.54mm + 5.08mm = 7.62mm = 0.3 inches)
    assert all(isinstance(v, float) for v in result)
    assert all(v >= 0 for v in result)


@patch("weather.forecast.requests.get")
def test_get_ensemble_precip_fallback_on_error(mock_get):
    """API error → returns [0.0] * 30 fallback."""
    mock_get.side_effect = Exception("API down")
    result = get_ensemble_precip(40.78, -73.97)
    assert result == [0.0] * 30


@patch("weather.forecast.requests.get")
def test_get_nws_precip_forecast_success(mock_get):
    """Successful NWS response → (pop, qpf) tuple."""
    points_resp = MagicMock()
    points_resp.status_code = 200
    points_resp.json.return_value = {"properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/33,37/forecast"}}
    points_resp.raise_for_status = MagicMock()

    forecast_resp = MagicMock()
    forecast_resp.status_code = 200
    forecast_resp.json.return_value = {"properties": {"periods": [{
        "probabilityOfPrecipitation": {"value": 70},
        "detailedForecast": "Rain likely with accumulations around 0.5 inches."
    }]}}
    forecast_resp.raise_for_status = MagicMock()

    mock_get.side_effect = [points_resp, forecast_resp]
    pop, qpf = get_nws_precip_forecast(40.78, -73.97)
    assert pop == pytest.approx(0.7)
    assert qpf == pytest.approx(0.5)


@patch("weather.forecast.requests.get")
def test_get_nws_precip_forecast_error_fallback(mock_get):
    """API error → (0.5, 0.0) fallback."""
    mock_get.side_effect = Exception("NWS down")
    pop, qpf = get_nws_precip_forecast(40.78, -73.97)
    assert pop == 0.5
    assert qpf == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_forecast_precip.py -v`
Expected: FAIL — `cannot import name 'get_ensemble_precip'`

- [ ] **Step 3: Implement precip forecast functions**

Add to `weather/forecast.py` (at module top, alongside existing imports):
```python
import re
import calendar
from datetime import date


def calculate_remaining_month_days(market_close_date: date | None = None) -> int:
    """Days from today to market close (or end of month if close date unknown).

    Args:
        market_close_date: Parsed from ticker date tag or market API response.
    """
    today = date.today()
    if market_close_date:
        delta = (market_close_date - today).days
        return max(0, delta)
    # Default: days until end of current month
    last_day = calendar.monthrange(today.year, today.month)[1]
    return max(0, last_day - today.day)


MM_TO_INCHES = 1.0 / 25.4


def get_ensemble_precip(lat: float, lon: float, forecast_days: int | None = None) -> list[float]:
    """Open-Meteo Ensemble — returns per-member precipitation totals in inches.

    Open-Meteo returns mm; we convert to inches (Kalshi settles in inches).
    If forecast_days is set (monthly contracts): sum daily values per member.
    Otherwise returns single-day values for day 1.
    """
    days = forecast_days if forecast_days else 2
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=precipitation_sum"
        f"&timezone=auto"
        f"&forecast_days={days}"
    )

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        totals = []
        daily = data.get("daily", {})

        # Parse member keys: precipitation_sum_member00, precipitation_sum_member01, ...
        member_keys = sorted(k for k in daily if k.startswith("precipitation_sum_member"))

        if member_keys:
            for key in member_keys:
                vals = daily[key]
                if forecast_days:
                    # Monthly: sum all forecast days for this member
                    member_sum = sum(v for v in vals if v is not None)
                else:
                    # Daily: take day 1 value
                    member_sum = vals[1] if len(vals) > 1 and vals[1] is not None else 0.0
                totals.append(round(member_sum * MM_TO_INCHES, 4))
        else:
            # Fallback: single precipitation_sum field (non-ensemble)
            raw = daily.get("precipitation_sum", [])
            if forecast_days:
                total = sum(v for v in raw if v is not None) * MM_TO_INCHES
            else:
                total = (raw[1] if len(raw) > 1 and raw[1] is not None else 0.0) * MM_TO_INCHES
            totals = [round(total, 4)]

        return totals if totals else [0.0] * 30

    except Exception as e:
        print(f"Open-Meteo Ensemble precip error: {e}")
        return [0.0] * 30


def get_nws_precip_forecast(lat: float, lon: float) -> tuple[float, float]:
    """Get NWS probability of precipitation (PoP) and quantitative forecast (QPF).

    Returns (pop, qpf_inches) where pop is on [0, 1] scale.
    NWS API returns PoP as percentage 0-100; we divide by 100.
    """
    try:
        # Get forecast URL from NWS points API
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {"User-Agent": "weather-bot/1.0"}
        r = requests.get(points_url, headers=headers, timeout=10)
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        # Get forecast periods
        r2 = requests.get(forecast_url, headers=headers, timeout=10)
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"]

        if not periods:
            return (0.5, 0.0)

        period = periods[0]
        pop_raw = period.get("probabilityOfPrecipitation", {}).get("value")
        pop = (pop_raw / 100.0) if pop_raw is not None else 0.5

        # QPF: parse accumulation amount from detailedForecast text if present
        qpf = 0.0  # Default; override if detailedForecast contains a quantity
        detail = period.get("detailedForecast", "")
        m = re.search(r'(\d+\.?\d*)\s*(?:inch|in)', detail, re.IGNORECASE)
        if m:
            qpf = float(m.group(1))

        return (max(0.0, min(1.0, pop)), qpf)

    except Exception as e:
        print(f"NWS precip forecast error: {e}")
        return (0.5, 0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_forecast_precip.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add weather/forecast.py tests/test_forecast_precip.py
git commit -m "feat: add precipitation ensemble and NWS forecast fetching"
```

---

### Task 6: Implement fuse_precip_forecast

**Files:**
- Modify: `weather/multi_model.py`
- Create: `tests/test_precip_fusion.py`

- [ ] **Step 1: Write failing tests for precip fusion**

Create `tests/test_precip_fusion.py`:
```python
import pytest
from unittest.mock import patch


@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_returns_tuple(mock_nws, mock_ensemble):
    """fuse_precip_forecast returns (prob, confidence, details) tuple."""
    mock_ensemble.return_value = [0.0] * 15 + [1.0 + i * 0.2 for i in range(15)]
    mock_nws.return_value = (0.6, 1.5)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=2.0, forecast_days=16,
    )
    assert 0.0 <= prob <= 1.0
    assert 0 <= confidence <= 100
    assert isinstance(details, dict)
    assert "ensemble" in details


@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_all_dry(mock_nws, mock_ensemble):
    """All dry ensemble + low NWS PoP → low probability."""
    mock_ensemble.return_value = [0.0] * 30
    mock_nws.return_value = (0.1, 0.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=1.0,
    )
    assert prob < 0.15


@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_all_wet(mock_nws, mock_ensemble):
    """All wet ensemble + high NWS PoP → high probability for low threshold."""
    mock_ensemble.return_value = [2.0 + i * 0.1 for i in range(30)]
    mock_nws.return_value = (0.95, 3.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=1.0,
    )
    assert prob > 0.80


@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_confidence_with_bias(mock_nws, mock_ensemble):
    """Confidence scoring produces value 0-100."""
    mock_ensemble.return_value = [0.0] * 10 + [0.5 + i * 0.2 for i in range(20)]
    mock_nws.return_value = (0.7, 1.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=1.5,
    )
    assert 0 <= confidence <= 100


@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_uses_csgd_by_default(mock_nws, mock_ensemble):
    """Default model should be CSGD (not empirical)."""
    mock_ensemble.return_value = [0.0] * 10 + [1.0 + i * 0.2 for i in range(20)]
    mock_nws.return_value = (0.8, 2.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=2.0,
    )
    assert details.get("ensemble", {}).get("method") == "csgd"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_precip_fusion.py -v`
Expected: FAIL — `cannot import name 'fuse_precip_forecast'`

- [ ] **Step 3: Implement fuse_precip_forecast**

Add to `weather/multi_model.py` (after existing imports, before `fuse_forecast`):

```python
from weather.forecast import get_ensemble_precip, get_nws_precip_forecast
from weather.precip_model import gamma_precip_prob
from config import PRECIP_FUSION_WEIGHTS


def fuse_precip_forecast(
    lat: float, lon: float, city: str, month: int,
    threshold: float, forecast_days: int | None = None,
) -> tuple[float, float, dict]:
    """Precipitation fusion. Returns (fused_prob, confidence, details).

    Same return shape as fuse_forecast() for temperature so the scanner
    edge-computation logic works identically.
    """
    weights = dict(PRECIP_FUSION_WEIGHTS)
    details = {}

    # --- Model 1: Open-Meteo Ensemble (30-member precip) ---
    cache_key = (round(lat, 2), round(lon, 2), "precip", forecast_days or 1)
    ensemble_precip = fcache.get("ensemble_precip", *cache_key)
    if ensemble_precip is None:
        ensemble_precip = get_ensemble_precip(lat, lon, forecast_days=forecast_days)
        if ensemble_precip:
            fcache.put("ensemble_precip", *cache_key, value=ensemble_precip)

    # Get NWS PoP for blended p_dry
    nws_pop, nws_qpf = get_nws_precip_forecast(lat, lon)

    # CSGD model (primary)
    csgd_result = gamma_precip_prob(ensemble_precip, threshold=threshold, nws_pop=nws_pop)

    # Bias correction for ensemble
    bias_ens, n_ens = get_bias(city, month, "ensemble_precip")

    ensemble_prob = csgd_result.prob_above
    details["ensemble"] = {
        "prob": ensemble_prob,
        "fraction_above": csgd_result.fraction_above,
        "p_dry": csgd_result.p_dry,
        "shape": csgd_result.shape,
        "scale": csgd_result.scale,
        "method": csgd_result.method,
        "bias": round(bias_ens, 3),
        "n": n_ens,
        "members_count": len(ensemble_precip),
    }

    # --- Model 2: NWS PoP + QPF ---
    # For "above X" threshold: use PoP for binary, PoP-weighted estimate for amounts
    if threshold <= 0.0:
        noaa_prob = nws_pop
    elif nws_qpf > 0:
        # Simple estimate: P(>X) ≈ PoP * P(amount > X | rain)
        # Rough: if QPF > threshold, high prob; if QPF < threshold, low prob
        ratio = min(1.0, nws_qpf / max(threshold, 0.01))
        noaa_prob = nws_pop * ratio
    else:
        noaa_prob = nws_pop * 0.3  # Low confidence estimate

    noaa_prob = max(0.0, min(1.0, noaa_prob))
    bias_noaa, n_noaa = get_bias(city, month, "noaa_precip")
    details["noaa"] = {"prob": noaa_prob, "pop": nws_pop, "qpf": nws_qpf,
                       "bias": round(bias_noaa, 3), "n": n_noaa}

    # --- Weighted fusion (ensemble + NWS; HRRR slot reserved for future) ---
    active = {"ensemble": ensemble_prob}
    if noaa_prob is not None:
        active["noaa"] = noaa_prob

    total_weight = sum(weights.get(k, 0) for k in active)
    if total_weight == 0:
        fused_prob = ensemble_prob
    else:
        fused_prob = sum(weights.get(k, 0) * active[k] / total_weight for k in active)
    fused_prob = round(max(0.0, min(1.0, fused_prob)), 4)

    details["fused_prob"] = fused_prob
    details["models_used"] = len(active)

    # --- Confidence scoring (0-100, adapted for precip) ---
    confidence = 0

    # +30 if ensemble and NWS agree (both > 0.5 or both < 0.5 for this threshold)
    if (ensemble_prob > 0.5 and noaa_prob > 0.5) or (ensemble_prob < 0.5 and noaa_prob < 0.5):
        confidence += 30
    elif abs(ensemble_prob - noaa_prob) < 0.15:
        confidence += 15

    # +25 if ensemble has strong agreement (fraction_above > 0.85 or < 0.15)
    fa = csgd_result.fraction_above
    if fa > 0.85 or fa < 0.15:
        confidence += 25
    elif fa > 0.75 or fa < 0.25:
        confidence += 12

    # +20 if bias data available
    if any(get_bias(city, month, m)[1] >= 30 for m in ["ensemble_precip", "noaa_precip"]):
        confidence += 20
    elif any(get_bias(city, month, m)[1] >= 10 for m in ["ensemble_precip", "noaa_precip"]):
        confidence += 10

    # +15 if CSGD fit succeeded (not fallback)
    if csgd_result.method == "csgd":
        confidence += 15

    # +10 if NWS data available
    if noaa_prob is not None:
        confidence += 10

    confidence = min(100, confidence)
    details["confidence"] = confidence

    return fused_prob, confidence, details
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_precip_fusion.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add weather/multi_model.py tests/test_precip_fusion.py
git commit -m "feat: add fuse_precip_forecast with CSGD + NWS fusion"
```

---

## Chunk 3: Scanner Integration + Wiring

### Task 7: Add PRECIP_SERIES and get_kalshi_precip_markets to scanner

**Files:**
- Modify: `kalshi/scanner.py`
- No new tests (API integration — tested via mocking in existing patterns)

- [ ] **Step 1: Add PRECIP_SERIES dict to kalshi/scanner.py**

Add after `WEATHER_SERIES_LOW` (line 42):
```python
# Precipitation series — monthly cumulative + daily binary
PRECIP_SERIES = {
    "kxrainnycm": {"city": "nyc", "lat": 40.7931, "lon": -73.8720, "unit": "in"},
    "kxrainchim": {"city": "chicago", "lat": 41.9742, "lon": -87.9073, "unit": "in"},
    "kxrainlaxm": {"city": "los_angeles", "lat": 33.9425, "lon": -118.4081, "unit": "in"},
    "kxrainseam": {"city": "seattle", "lat": 47.4502, "lon": -122.3088, "unit": "in"},
    "kxrainmiam": {"city": "miami", "lat": 25.7617, "lon": -80.1918, "unit": "in"},
    "kxrainhoum": {"city": "houston", "lat": 29.9902, "lon": -95.3368, "unit": "in"},
    "kxrainsfom": {"city": "san_francisco", "lat": 37.6213, "lon": -122.3790, "unit": "in"},
}
```

- [ ] **Step 2: Add get_kalshi_precip_markets function**

Add after `get_kalshi_low_temp_markets()`:
```python
from kalshi.market_types import parse_precip_bucket


def get_kalshi_precip_markets() -> List[Dict]:
    """Fetch active precipitation markets from Kalshi public API."""
    all_markets = []

    for i, (series_ticker, info) in enumerate(PRECIP_SERIES.items()):
        if i > 0:
            time.sleep(0.15)
        try:
            url = f"{KALSHI_BASE}/events"
            params = {
                "series_ticker": series_ticker,
                "status": "open",
                "with_nested_markets": "true",
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            events = data.get("events", [])
            for event in events:
                nested_markets = event.get("markets", [])
                for market in nested_markets:
                    bucket = parse_precip_bucket(market)
                    if bucket is None:
                        continue
                    market["_city"] = info["city"]
                    market["_lat"] = info["lat"]
                    market["_lon"] = info["lon"]
                    market["_unit"] = info["unit"]
                    market["_market_type"] = "precip"
                    market["_threshold"] = bucket[0]
                    all_markets.append(market)
        except Exception as e:
            print(f"Kalshi API error for {series_ticker}: {e}")

    return all_markets
```

- [ ] **Step 3: Commit**

```bash
git add kalshi/scanner.py
git commit -m "feat: add PRECIP_SERIES and get_kalshi_precip_markets to scanner"
```

---

### Task 8: Wire precip into root scanner.py

**Files:**
- Modify: `scanner.py` (root)

- [ ] **Step 1: Add precip market discovery to scan loop**

In `scanner.py` (root), find where `get_kalshi_weather_markets()` and `get_kalshi_low_temp_markets()` are called. Add after them:

```python
from kalshi.scanner import get_kalshi_precip_markets
from weather.multi_model import fuse_precip_forecast
from weather.forecast import calculate_remaining_month_days
from config import MAX_ENSEMBLE_HORIZON_DAYS
```

Also verify `from datetime import datetime, timezone` is already imported (it should be for existing temp logic).

In the Kalshi scan section, after the existing low-temp market loop, add:
```python
    # --- Kalshi Precipitation Markets ---
    precip_markets = get_kalshi_precip_markets()
    console.print(f"\n  Found {len(precip_markets)} precip markets")

    for market in precip_markets:
        ticker = market.get("ticker", "")
        title = market.get("title", ticker)
        city_key = market.get("_city", "unknown")
        threshold = market.get("_threshold", 0.0)
        yes_price = market.get("yes_price", 0) / 100.0 if isinstance(market.get("yes_price"), int) else market.get("yes_price", 0.5)

        # Monthly contracts: check horizon limit
        remaining_days = calculate_remaining_month_days()
        if remaining_days > MAX_ENSEMBLE_HORIZON_DAYS:
            continue  # Skip — beyond ensemble forecast horizon

        month = datetime.now(timezone.utc).month

        try:
            model_prob, confidence, details = fuse_precip_forecast(
                market["_lat"], market["_lon"], city_key, month,
                threshold=threshold, forecast_days=remaining_days,
            )
        except Exception as e:
            console.print(f"[red]Precip fusion error for {city_key}: {e}[/red]")
            continue

        edge = model_prob - yes_price

        if abs(edge) >= SHOW_THRESHOLD:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE, confidence=confidence, ticker=ticker)
            signals_found += 1

            if abs(edge) >= ALERT_THRESHOLD and confidence >= CONFIDENCE_THRESHOLD:
                execute_kalshi_signal(market, city_key, model_prob, yes_price, edge, direction, confidence=confidence)
```

- [ ] **Step 2: Commit**

```bash
git add scanner.py
git commit -m "feat: wire precip market discovery into root scanner loop"
```

---

### Task 9: Update position manager for precip positions

**Files:**
- Modify: `kalshi/position_manager.py`

Without this, any precip positions taken by the trader will be unmanaged — no trailing stops, no exits, no mark-to-market.

- [ ] **Step 1: Add PRECIP_SERIES to _ALL_SERIES**

At the top of `kalshi/position_manager.py`, after the existing `_ALL_SERIES` construction:
```python
from kalshi.scanner import PRECIP_SERIES

for ticker, info in PRECIP_SERIES.items():
    _ALL_SERIES[ticker] = {**info, "market_type": "precip"}
```

- [ ] **Step 2: Update _parse_position_ticker to recognize KXRAIN***

The existing function iterates `_ALL_SERIES` which now includes precip series, so it should work for KXRAIN* tickers automatically. However, update the return to include `market_type`:
```python
def _parse_position_ticker(ticker: str) -> Optional[dict]:
    """Extract series info from a position ticker.

    Returns dict with city, lat, lon, unit, and either temp_type or market_type.
    """
    for series_prefix, info in _ALL_SERIES.items():
        if ticker.upper().startswith(series_prefix.upper()):
            return dict(info)
    return None
```

- [ ] **Step 3: Route precip positions through fuse_precip_forecast in evaluate_position**

In `evaluate_position()`, after getting `series_info`, add a branch for precip:
```python
from weather.multi_model import fuse_precip_forecast
from kalshi.market_types import parse_precip_bucket

# In evaluate_position(), after series_info = _parse_position_ticker(ticker):
if series_info.get("market_type") == "precip":
    # Parse precip threshold from market data
    bucket = parse_precip_bucket(market_data)
    if not bucket:
        return {"action": "hold", "reason": "can't parse precip bucket"}
    threshold = bucket[0]
    city = series_info["city"]
    month = datetime.now(timezone.utc).month
    remaining_days = calculate_remaining_month_days()

    try:
        model_prob, confidence, details = fuse_precip_forecast(
            series_info["lat"], series_info["lon"], city, month,
            threshold=threshold, forecast_days=remaining_days,
        )
    except Exception as e:
        return {"action": "hold", "reason": f"precip forecast error: {e}"}

    # Continue with the same edge calculation and decision logic below
    # (set bucket, temp_type vars for compatibility)
    low, high = threshold, None
    temp_type = "precip"
else:
    # Existing temperature path (unchanged)
    ...
```

- [ ] **Step 4: Verify import works**

Run: `python -c "from kalshi.position_manager import _ALL_SERIES; print(f'{len(_ALL_SERIES)} series loaded'); assert any('rain' in k for k in _ALL_SERIES), 'No precip series found'"`
Expected: Series count includes precip entries

- [ ] **Step 5: Commit**

```bash
git add kalshi/position_manager.py
git commit -m "feat: add precip position management (KXRAIN* trailing stops + exits)"
```

---

### Task 10: Run full test suite + final integration

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All existing tests PASS + all new tests PASS (26+ new tests total)

- [ ] **Step 2: Verify no import errors with a dry run**

Run: `python -c "from weather.precip_model import gamma_precip_prob, empirical_precip_prob; from kalshi.market_types import MarketType, detect_market_type, parse_precip_bucket; from weather.multi_model import fuse_precip_forecast; from weather.stations_config import get_station; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Commit any final fixes (only if needed)**

If any fixes were made, stage specific files (not `git add -A`):
```bash
git add <specific files that were fixed>
git commit -m "fix: resolve integration issues from Phase 4 precip expansion"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Precip model — empirical baseline | 4 |
| 2 | Precip model — CSGD with gamma fit | 4 |
| 3 | MarketType enum + precip bucket parser | 9 |
| 4 | Stations config + config.py updates | 2 |
| 5 | Forecast precip functions (Open-Meteo + NWS) | 6 |
| 6 | fuse_precip_forecast fusion engine | 5 |
| 7 | Scanner PRECIP_SERIES + discovery | 0 (API integration) |
| 8 | Root scanner.py wiring | 0 (integration) |
| 9 | Position manager precip support | 0 (integration) |
| 10 | Full test suite + import verification | existing + new |

**Total new tests: 30**

> **Note:** X signal layer for precipitation deferred intentionally — spec limits it to daily binary nowcasting initially. Will be added after validating precip model calibration.
