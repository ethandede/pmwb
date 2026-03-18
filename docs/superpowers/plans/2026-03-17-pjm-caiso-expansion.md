# PJM & CAISO Power Market Expansion — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PJM and CAISO paper trading pipelines alongside existing ERCOT, using public data endpoints and the same solar+load signal model.

**Architecture:** Copy-and-adapt from `ercot/`. Each ISO gets its own directory (`pjm/`, `caiso/`) with hubs, paper trader, and position manager. No shared base module — ERCOT code is untouched. New `MarketConfig` entries register them in the pipeline.

**Tech Stack:** Python 3.11+, SQLite, requests, pytest, Rich (console output)

**Spec:** `docs/superpowers/specs/2026-03-17-pjm-caiso-expansion-design.md`

---

## Task 1: Add PJM & CAISO config to `config.py`

**Files:**
- Modify: `config.py:147` (append after ERCOT_SEASONAL_NORMS)
- Test: `tests/test_pjm_config.py` (new)
- Test: `tests/test_caiso_config.py` (new)

- [ ] **Step 1: Write config validation tests for PJM**

Create `tests/test_pjm_config.py`:

```python
"""Tests for PJM hub config and seasonal norms in config.py."""


def test_pjm_hubs_exist():
    from config import PJM_HUBS
    assert len(PJM_HUBS) == 5


def test_pjm_hubs_have_required_fields():
    from config import PJM_HUBS
    required = {"city", "lat", "lon", "hub_name", "solar_sensitivity", "active"}
    for hub_key, info in PJM_HUBS.items():
        assert required.issubset(info.keys()), f"{hub_key} missing: {required - info.keys()}"


def test_pjm_active_hubs():
    from config import PJM_HUBS
    active = {k for k, v in PJM_HUBS.items() if v["active"]}
    assert active == {"Western", "AEP-Dayton"}


def test_pjm_seasonal_norms_all_months():
    from config import PJM_SEASONAL_NORMS
    assert set(PJM_SEASONAL_NORMS.keys()) == set(range(1, 13))
    for month, norms in PJM_SEASONAL_NORMS.items():
        assert "solar" in norms and "load" in norms
        assert norms["solar"] > 0
        assert norms["load"] > 0


def test_pjm_trading_params():
    from config import (
        PJM_PAPER_BANKROLL, PJM_PAPER_MODE, PJM_LOAD_SENSITIVITY,
        PJM_MAX_POSITIONS_PER_HUB, PJM_MAX_POSITIONS_TOTAL,
    )
    assert PJM_PAPER_BANKROLL == 10_000.0
    assert PJM_PAPER_MODE is True
    assert PJM_LOAD_SENSITIVITY == 0.20
    assert PJM_MAX_POSITIONS_PER_HUB == 3
    assert PJM_MAX_POSITIONS_TOTAL == 8
```

- [ ] **Step 2: Write config validation tests for CAISO**

Create `tests/test_caiso_config.py`:

```python
"""Tests for CAISO hub config and seasonal norms in config.py."""


def test_caiso_hubs_exist():
    from config import CAISO_HUBS
    assert len(CAISO_HUBS) == 3


def test_caiso_hubs_have_required_fields():
    from config import CAISO_HUBS
    required = {"city", "lat", "lon", "hub_name", "solar_sensitivity", "active"}
    for hub_key, info in CAISO_HUBS.items():
        assert required.issubset(info.keys()), f"{hub_key} missing: {required - info.keys()}"


def test_caiso_active_hubs():
    from config import CAISO_HUBS
    active = {k for k, v in CAISO_HUBS.items() if v["active"]}
    assert active == {"SP15"}


def test_caiso_seasonal_norms_all_months():
    from config import CAISO_SEASONAL_NORMS
    assert set(CAISO_SEASONAL_NORMS.keys()) == set(range(1, 13))
    for month, norms in CAISO_SEASONAL_NORMS.items():
        assert "solar" in norms and "load" in norms
        assert norms["solar"] > 0
        assert norms["load"] > 0


def test_caiso_trading_params():
    from config import (
        CAISO_PAPER_BANKROLL, CAISO_PAPER_MODE, CAISO_LOAD_SENSITIVITY,
        CAISO_MAX_POSITIONS_PER_HUB, CAISO_MAX_POSITIONS_TOTAL,
    )
    assert CAISO_PAPER_BANKROLL == 10_000.0
    assert CAISO_PAPER_MODE is True
    assert CAISO_LOAD_SENSITIVITY == 0.15
    assert CAISO_MAX_POSITIONS_PER_HUB == 3
    assert CAISO_MAX_POSITIONS_TOTAL == 6
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_pjm_config.py tests/test_caiso_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'PJM_HUBS' from 'config'`

- [ ] **Step 4: Add PJM config block to config.py**

Append after line 147 (end of `ERCOT_SEASONAL_NORMS`):

```python

# --- PJM Power Price Signal ---
PJM_HUBS = {
    "Western":    {"city": "Pittsburgh",   "lat": 40.44, "lon": -80.00, "hub_name": "WESTERN", "solar_sensitivity": 0.20, "active": True},
    "AEP-Dayton": {"city": "Columbus",     "lat": 39.96, "lon": -82.99, "hub_name": "AEP",     "solar_sensitivity": 0.15, "active": True},
    "NI":         {"city": "Chicago",      "lat": 41.88, "lon": -87.63, "hub_name": "NI",      "solar_sensitivity": 0.10, "active": False},
    "RTO":        {"city": "Philadelphia", "lat": 39.95, "lon": -75.16, "hub_name": "RTO",     "solar_sensitivity": 0.12, "active": False},
    "PSEG":       {"city": "Newark",       "lat": 40.74, "lon": -74.17, "hub_name": "PSEG",    "solar_sensitivity": 0.08, "active": False},
}

PJM_PAPER_BANKROLL = 10_000.0
PJM_PAPER_MODE = True
PJM_MIN_EDGE = 0.03
PJM_MIN_CONFIDENCE = 50
PJM_FORTIFY_EDGE_INCREASE = 0.03
PJM_EXIT_EDGE_DECAY = 0.30
PJM_MAX_POSITIONS_PER_HUB = 3
PJM_MAX_POSITIONS_TOTAL = 8
PJM_POSITION_TTL_HOURS = 24
PJM_LOAD_SENSITIVITY = 0.20

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

- [ ] **Step 5: Add CAISO config block to config.py**

Append after PJM block:

```python

# --- CAISO Power Price Signal ---
CAISO_HUBS = {
    "SP15": {"city": "Los Angeles", "lat": 34.05, "lon": -118.24, "hub_name": "SP15", "solar_sensitivity": 0.35, "active": True},
    "NP15": {"city": "Sacramento",  "lat": 38.58, "lon": -121.49, "hub_name": "NP15", "solar_sensitivity": 0.25, "active": False},
    "ZP26": {"city": "Fresno",      "lat": 36.74, "lon": -119.77, "hub_name": "ZP26", "solar_sensitivity": 0.30, "active": False},
}

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

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_pjm_config.py tests/test_caiso_config.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add config.py tests/test_pjm_config.py tests/test_caiso_config.py
git commit -m "feat: add PJM and CAISO hub configs, trading params, and seasonal norms"
```

---

## Task 2: PJM signal function in `weather/multi_model.py`

**Files:**
- Modify: `weather/multi_model.py:687` (append after `get_ercot_solar_signal`)
- Test: `tests/test_pjm_signal.py` (new)

**Context:** Copy `get_ercot_solar_signal()` (lines 556-686) and adapt for PJM. The function uses `weather.http.get as http_get` for API calls (same VisualCrossing + Open-Meteo endpoints). Change only: config imports, data dict key names, return key names.

- [ ] **Step 1: Write PJM signal tests**

Create `tests/test_pjm_signal.py`:

```python
"""Tests for get_pjm_solar_signal — fair price model with PJM norms."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

MARCH_DATETIME = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)


def _make_pjm_data(hub_price=35.0, price=35.0, solar_mw=5000.0, load_forecast=80000.0):
    return {
        "hub_price": hub_price,
        "price": price,
        "solar_mw": solar_mw,
        "load_forecast": load_forecast,
    }


def _mock_both_solar(vc_solrad=12.5, om_solrad=12.5):
    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": vc_solrad}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [om_solrad]},
        "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
    }
    om_resp.raise_for_status = MagicMock()
    return [vc_resp, om_resp]


def _pjm_signal_test(func):
    @patch("config.VISUAL_CROSSING_API_KEY", "test-key-123")
    @patch("weather.multi_model.datetime", wraps=datetime,
           **{"now.return_value": MARCH_DATETIME})
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__qualname__ = func.__qualname__
    return wrapper


def test_pjm_signal_signature():
    from weather.multi_model import get_pjm_solar_signal
    import inspect
    sig = inspect.signature(get_pjm_solar_signal)
    assert "hub_key" in sig.parameters
    assert "solar_sensitivity" in sig.parameters
    assert "pjm_data" in sig.parameters


@_pjm_signal_test
def test_pjm_short_when_solar_above_norm(_mock_dt):
    """Above-norm solar -> SHORT. March norm = 12.5 MJ/m²."""
    from weather.multi_model import get_pjm_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=18.0, om_solrad=18.0)
        result = get_pjm_solar_signal(
            40.44, -80.00, hub_key="Western", solar_sensitivity=0.20,
            pjm_data=_make_pjm_data(hub_price=35.0, load_forecast=80000.0),
        )

    assert result["signal"] == "SHORT"
    assert result["edge"] < 0


@_pjm_signal_test
def test_pjm_long_when_solar_below_norm(_mock_dt):
    """Below-norm solar -> LONG. March norm = 12.5 MJ/m²."""
    from weather.multi_model import get_pjm_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=6.0, om_solrad=6.0)
        result = get_pjm_solar_signal(
            40.44, -80.00, hub_key="Western", solar_sensitivity=0.20,
            pjm_data=_make_pjm_data(hub_price=35.0, load_forecast=80000.0),
        )

    assert result["signal"] == "LONG"
    assert result["edge"] > 0


@_pjm_signal_test
def test_pjm_load_impact(_mock_dt):
    """High load above norm -> positive edge contribution."""
    from weather.multi_model import get_pjm_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        # Solar at norm (12.5) so solar_impact = 0
        mock_get.side_effect = _mock_both_solar(vc_solrad=12.5, om_solrad=12.5)
        result = get_pjm_solar_signal(
            40.44, -80.00, hub_key="Western", solar_sensitivity=0.20,
            pjm_data=_make_pjm_data(hub_price=35.0, load_forecast=100000.0),
        )

    # March norm_load = 80,000. load at 100,000 -> positive load_impact
    assert result["signal"] == "LONG"
    assert result["edge"] > 0


@_pjm_signal_test
def test_pjm_returns_pjm_price_key(_mock_dt):
    """Return dict should use 'current_pjm_price' not 'current_ercot_price'."""
    from weather.multi_model import get_pjm_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar()
        result = get_pjm_solar_signal(
            40.44, -80.00, hub_key="Western", solar_sensitivity=0.20,
            pjm_data=_make_pjm_data(hub_price=42.0),
        )

    assert "current_pjm_price" in result
    assert result["current_pjm_price"] == 42.0
    assert "current_ercot_price" not in result


@_pjm_signal_test
def test_pjm_confidence_agreement_bonus(_mock_dt):
    """VC and OM agree within 2.0 -> +20 confidence bonus."""
    from weather.multi_model import get_pjm_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=10.0, om_solrad=10.5)
        result = get_pjm_solar_signal(
            40.44, -80.00, hub_key="Western", solar_sensitivity=0.20,
            pjm_data=_make_pjm_data(),
        )

    assert result["confidence"] >= 50  # base 30 + agreement 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pjm_signal.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_pjm_solar_signal'`

- [ ] **Step 3: Implement `get_pjm_solar_signal()` in `weather/multi_model.py`**

Copy `get_ercot_solar_signal()` (lines 556-686) and paste after line 687. Make these changes:

1. Function name: `get_pjm_solar_signal`
2. Parameter: `ercot_data=None` → `pjm_data=None`
3. Import line inside function: `from config import VISUAL_CROSSING_API_KEY, PJM_SEASONAL_NORMS, PJM_LOAD_SENSITIVITY`
4. Norms lookup: `ERCOT_SEASONAL_NORMS` → `PJM_SEASONAL_NORMS`
5. Load sensitivity: `ERCOT_LOAD_SENSITIVITY` → `PJM_LOAD_SENSITIVITY`
6. Default norms fallback: `{"solar": 18.0, "load": 50_000}` → `{"solar": 12.0, "load": 85_000}`
7. Data dict: all `ercot_data` → `pjm_data`
8. Default hub_price: `40.0` → `35.0`
9. Default solar_mw: `12000.0` → `5000.0`
10. Default load: `norm_load` stays (same pattern)
11. Return key: `"current_ercot_price"` → `"current_pjm_price"`
12. Telegram alert: `"ERCOT Norms Missing"` → `"PJM Norms Missing"`, dedup key `"pjm_norms_missing"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pjm_signal.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add weather/multi_model.py tests/test_pjm_signal.py
git commit -m "feat: add get_pjm_solar_signal() — PJM fair-price model"
```

---

## Task 3: CAISO signal function in `weather/multi_model.py`

**Files:**
- Modify: `weather/multi_model.py` (append after PJM signal function)
- Test: `tests/test_caiso_signal.py` (new)

**Context:** Same copy-adapt as Task 2, but with CAISO constants. CAISO has higher solar norms and lower load norms than PJM.

- [ ] **Step 1: Write CAISO signal tests**

Create `tests/test_caiso_signal.py`:

```python
"""Tests for get_caiso_solar_signal — fair price model with CAISO norms."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

MARCH_DATETIME = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)


def _make_caiso_data(hub_price=40.0, price=40.0, solar_mw=10000.0, load_forecast=23000.0):
    return {
        "hub_price": hub_price,
        "price": price,
        "solar_mw": solar_mw,
        "load_forecast": load_forecast,
    }


def _mock_both_solar(vc_solrad=19.0, om_solrad=19.0):
    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": vc_solrad}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [om_solrad]},
        "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
    }
    om_resp.raise_for_status = MagicMock()
    return [vc_resp, om_resp]


def _caiso_signal_test(func):
    @patch("config.VISUAL_CROSSING_API_KEY", "test-key-123")
    @patch("weather.multi_model.datetime", wraps=datetime,
           **{"now.return_value": MARCH_DATETIME})
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__qualname__ = func.__qualname__
    return wrapper


def test_caiso_signal_signature():
    from weather.multi_model import get_caiso_solar_signal
    import inspect
    sig = inspect.signature(get_caiso_solar_signal)
    assert "hub_key" in sig.parameters
    assert "caiso_data" in sig.parameters


@_caiso_signal_test
def test_caiso_short_when_solar_above_norm(_mock_dt):
    """Above-norm solar -> SHORT. March norm = 19.0 MJ/m²."""
    from weather.multi_model import get_caiso_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=27.0, om_solrad=27.0)
        result = get_caiso_solar_signal(
            34.05, -118.24, hub_key="SP15", solar_sensitivity=0.35,
            caiso_data=_make_caiso_data(hub_price=40.0, load_forecast=23000.0),
        )

    assert result["signal"] == "SHORT"
    assert result["edge"] < 0


@_caiso_signal_test
def test_caiso_long_when_solar_below_norm(_mock_dt):
    """Below-norm solar -> LONG. March norm = 19.0 MJ/m²."""
    from weather.multi_model import get_caiso_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=10.0, om_solrad=10.0)
        result = get_caiso_solar_signal(
            34.05, -118.24, hub_key="SP15", solar_sensitivity=0.35,
            caiso_data=_make_caiso_data(hub_price=40.0, load_forecast=23000.0),
        )

    assert result["signal"] == "LONG"
    assert result["edge"] > 0


@_caiso_signal_test
def test_caiso_returns_caiso_price_key(_mock_dt):
    """Return dict should use 'current_caiso_price'."""
    from weather.multi_model import get_caiso_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar()
        result = get_caiso_solar_signal(
            34.05, -118.24, hub_key="SP15", solar_sensitivity=0.35,
            caiso_data=_make_caiso_data(hub_price=55.0),
        )

    assert "current_caiso_price" in result
    assert result["current_caiso_price"] == 55.0
    assert "current_ercot_price" not in result


@_caiso_signal_test
def test_caiso_high_solar_sensitivity_amplifies_edge(_mock_dt):
    """SP15 sensitivity 0.35 should produce larger edge than PJM 0.20 for same deviation."""
    from weather.multi_model import get_caiso_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        # Solar 5 MJ below norm (19 - 14 = 5)
        mock_get.side_effect = _mock_both_solar(vc_solrad=14.0, om_solrad=14.0)
        result = get_caiso_solar_signal(
            34.05, -118.24, hub_key="SP15", solar_sensitivity=0.35,
            caiso_data=_make_caiso_data(load_forecast=23000.0),
        )

    # solar_impact = 0.35 * (19 - 14) / 19 = 0.092
    assert result["edge"] > 0.08
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caiso_signal.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_caiso_solar_signal'`

- [ ] **Step 3: Implement `get_caiso_solar_signal()` in `weather/multi_model.py`**

Same copy-adapt pattern as Task 2. Changes from ERCOT:

1. Function name: `get_caiso_solar_signal`
2. Parameter: `caiso_data=None`
3. Import: `CAISO_SEASONAL_NORMS`, `CAISO_LOAD_SENSITIVITY`
4. Default norms fallback: `{"solar": 20.0, "load": 27_000}`
5. Default hub_price: `40.0`
6. Default solar_mw: `10000.0`
7. Return key: `"current_caiso_price"`
8. Telegram dedup: `"caiso_norms_missing"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caiso_signal.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add weather/multi_model.py tests/test_caiso_signal.py
git commit -m "feat: add get_caiso_solar_signal() — CAISO fair-price model"
```

---

## Task 4: PJM hub scanning — `pjm/hubs.py`

**Files:**
- Create: `pjm/__init__.py`
- Create: `pjm/hubs.py`
- Test: `tests/test_pjm_hubs.py` (new)

**Context:** Mirrors `ercot/hubs.py` but fetches from PJM public endpoints. Only scans hubs where `active: True`. No auth module needed — PJM public API doesn't require OAuth (unlike ERCOT).

- [ ] **Step 1: Write PJM hub tests**

Create `tests/test_pjm_hubs.py`:

```python
"""Tests for pjm/hubs.py — hub scanning and PJM data caching."""
from unittest.mock import patch


def test_scan_all_pjm_hubs_returns_active_only():
    """Only active hubs (Western, AEP-Dayton) should be scanned."""
    from pjm.hubs import scan_all_pjm_hubs
    signals = scan_all_pjm_hubs()
    assert len(signals) == 2
    hub_names = {s["hub"] for s in signals}
    assert hub_names == {"Western", "AEP-Dayton"}


def test_pjm_signal_dict_has_required_keys():
    from pjm.hubs import scan_all_pjm_hubs
    signals = scan_all_pjm_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_pjm_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_pjm_market_data_caching():
    from pjm.hubs import _fetch_pjm_market_data
    data1 = _fetch_pjm_market_data()
    data2 = _fetch_pjm_market_data()
    assert "price" in data1
    assert data1 == data2


def test_fetch_pjm_market_data_has_fallbacks():
    from pjm.hubs import _fetch_pjm_market_data
    data = _fetch_pjm_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)


def test_fetch_pjm_markets_includes_per_hub_fields():
    from pjm.hubs import fetch_pjm_markets
    markets = fetch_pjm_markets()
    for m in markets:
        assert "hub_key" in m
        assert "solar_sensitivity" in m
        assert isinstance(m["solar_sensitivity"], float)


def test_fetch_pjm_markets_only_active():
    """fetch_pjm_markets should only return active hubs."""
    from pjm.hubs import fetch_pjm_markets
    markets = fetch_pjm_markets()
    assert len(markets) == 2
    hubs = {m["hub"] for m in markets}
    assert hubs == {"Western", "AEP-Dayton"}


def test_fetch_pjm_markets_has_per_hub_price():
    from pjm.hubs import fetch_pjm_markets
    markets = fetch_pjm_markets()
    for m in markets:
        pjm_data = m.get("_pjm_data", {})
        assert "hub_price" in pjm_data


def test_per_hub_prices_are_differentiated():
    from pjm.hubs import fetch_pjm_markets

    fake_data = {
        "price": 35.0, "solar_mw": 5000.0, "load_forecast": 80000.0,
        "hub_prices": {"WESTERN": 30.0, "AEP": 45.0},
    }

    with patch("pjm.hubs._fetch_pjm_market_data", return_value=fake_data):
        markets = fetch_pjm_markets()

    prices = {m["hub_name"]: m["current_pjm_price"] for m in markets}
    assert prices["WESTERN"] == 30.0
    assert prices["AEP"] == 45.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pjm_hubs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pjm'`

- [ ] **Step 3: Create `pjm/__init__.py`**

Empty file.

- [ ] **Step 4: Create `pjm/hubs.py`**

Copy `ercot/hubs.py` and adapt:

1. Import `PJM_HUBS` from config (not `ERCOT_HUBS`)
2. Import `get_pjm_solar_signal` from `weather.multi_model` (not `get_ercot_solar_signal`)
3. Remove `from ercot.auth import get_ercot_headers` — PJM uses no auth
4. Cache vars: `_pjm_cache`, `_pjm_cache_time`
5. `_fetch_pjm_market_data()`: fetch from PJM public endpoints
   - LMPs: `https://api.pjm.com/api/v1/rt_hrl_lmps` (try JSON, fall back gracefully)
   - Load: `https://api.pjm.com/api/v1/load_frcstd_7_day`
   - No auth headers
   - Fallback defaults: `{"price": 35.0, "solar_mw": 5000.0, "load_forecast": 85_000.0}`
   - Telegram dedup: `"pjm_api_down"`
6. `fetch_pjm_markets()`: filter `PJM_HUBS` on `active: True`, use `_pjm_data` key
7. `scan_all_pjm_hubs()`: filter `PJM_HUBS` on `active: True`, call `get_pjm_solar_signal`
8. Return dicts use `current_pjm_price` key (not `current_ercot_price`)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pjm_hubs.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pjm/__init__.py pjm/hubs.py tests/test_pjm_hubs.py
git commit -m "feat: add PJM hub scanning with public API data fetching"
```

---

## Task 5: PJM paper trader — `pjm/paper_trader.py`

**Files:**
- Create: `pjm/paper_trader.py`
- Test: `tests/test_pjm_paper_trader.py` (new)

**Context:** Copy `ercot/paper_trader.py` and change all `ERCOT_*` config references to `PJM_*`. DB path: `data/pjm_paper.db`. Table names: `pjm_positions`, `pjm_trades`, `pjm_scan_cache`. Price key in signals: `current_pjm_price`.

- [ ] **Step 1: Write PJM paper trader tests**

Create `tests/test_pjm_paper_trader.py`:

```python
"""Tests for pjm/paper_trader.py — paper position management."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
import pytest

TEST_DB = "data/test_pjm_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import pjm.paper_trader as pt
    pt.PJM_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(hub="Western", hub_name="WESTERN", signal="SHORT", edge=1.5, confidence=70, price=35.0):
    return {
        "hub": hub, "hub_name": hub_name, "city": "Pittsburgh",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_pjm_price": price, "expected_solrad_mjm2": 12.0,
        "actual_solar_mw": 5000.0,
    }


def test_open_position():
    from pjm.paper_trader import open_position, get_open_positions

    sig = _make_signal()
    result = open_position(sig, bankroll=10000.0)
    assert result is not None
    assert result["hub"] == "Western"
    assert result["signal"] == "SHORT"
    assert result["size_dollars"] > 0

    positions = get_open_positions()
    assert len(positions) == 1


def test_close_position_pnl_short():
    from pjm.paper_trader import open_position, close_position, get_trade_history

    sig = _make_signal(price=35.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=25.0, exit_signal="LONG", reason="signal flipped")

    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0


def test_close_position_pnl_long():
    from pjm.paper_trader import open_position, close_position, get_trade_history

    sig = _make_signal(signal="LONG", edge=1.2, price=35.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=45.0, exit_signal="NEUTRAL", reason="edge decay")

    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0


def test_max_positions_per_hub():
    from pjm.paper_trader import open_position, get_open_positions
    import config
    original = config.PJM_MAX_POSITIONS_PER_HUB
    config.PJM_MAX_POSITIONS_PER_HUB = 2

    open_position(_make_signal(), bankroll=10000.0)
    open_position(_make_signal(), bankroll=10000.0)
    result = open_position(_make_signal(), bankroll=10000.0)

    assert result is None
    assert len(get_open_positions()) == 2
    config.PJM_MAX_POSITIONS_PER_HUB = original


def test_max_positions_total():
    from pjm.paper_trader import open_position, get_open_positions
    import config
    original = config.PJM_MAX_POSITIONS_TOTAL
    config.PJM_MAX_POSITIONS_TOTAL = 2

    open_position(_make_signal(hub="Western", hub_name="WESTERN"), bankroll=10000.0)
    open_position(_make_signal(hub="AEP-Dayton", hub_name="AEP"), bankroll=10000.0)
    result = open_position(_make_signal(hub="NI", hub_name="NI"), bankroll=10000.0)

    assert result is None
    assert len(get_open_positions()) == 2
    config.PJM_MAX_POSITIONS_TOTAL = original


def test_expire_positions():
    from pjm.paper_trader import open_position, expire_positions, get_open_positions, get_trade_history

    sig = _make_signal(price=35.0)
    open_position(sig, bankroll=10000.0)

    conn = sqlite3.connect(TEST_DB)
    conn.execute("UPDATE pjm_positions SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    conn.commit()
    conn.close()

    expire_positions(current_price=37.0)

    assert len(get_open_positions()) == 0
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "expired"


def test_paper_summary():
    from pjm.paper_trader import open_position, close_position, get_paper_summary

    sig = _make_signal(price=35.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=25.0, exit_signal="LONG", reason="test")

    summary = get_paper_summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
    assert summary["open_count"] == 0


def test_scan_cache_write_and_read():
    from pjm.paper_trader import write_scan_cache, get_cached_signals

    signals = [
        {"hub": "Western", "hub_name": "WESTERN", "signal": "SHORT",
         "edge": 1.5, "expected_solrad_mjm2": 12.0,
         "current_pjm_price": 35.0, "actual_solar_mw": 5000.0,
         "confidence": 70},
    ]
    write_scan_cache(signals)
    cached = get_cached_signals()
    assert len(cached) >= 1
    assert cached[0]["hub"] == "Western"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pjm_paper_trader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pjm.paper_trader'`

- [ ] **Step 3: Create `pjm/paper_trader.py`**

Copy `ercot/paper_trader.py` and change:

1. DB path: `PJM_PAPER_DB = "data/pjm_paper.db"`
2. Imports: `PJM_PAPER_BANKROLL`, `PJM_POSITION_TTL_HOURS`, `PJM_MAX_POSITIONS_PER_HUB`, `PJM_MAX_POSITIONS_TOTAL`
3. Table names: `pjm_positions`, `pjm_trades`, `pjm_scan_cache`
4. Config references: `_config.PJM_MAX_POSITIONS_PER_HUB`, `_config.PJM_MAX_POSITIONS_TOTAL`
5. Price key in `open_position()`: `hub_signal["current_pjm_price"]`
6. Scan cache columns: `current_pjm_price` instead of `current_ercot_price`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pjm_paper_trader.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pjm/paper_trader.py tests/test_pjm_paper_trader.py
git commit -m "feat: add PJM paper trader with SQLite-backed positions"
```

---

## Task 6: PJM position manager — `pjm/position_manager.py`

**Files:**
- Create: `pjm/position_manager.py`
- Test: `tests/test_pjm_position_manager.py` (new)

**Context:** Copy `ercot/position_manager.py` and change config imports + function names.

- [ ] **Step 1: Write PJM position manager tests**

Create `tests/test_pjm_position_manager.py`:

```python
"""Tests for pjm/position_manager.py — evaluate/fortify/exit logic."""
import os
import pytest

TEST_DB = "data/test_pjm_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import pjm.paper_trader as pt
    pt.PJM_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(signal="SHORT", edge=1.5, confidence=70, price=35.0):
    return {
        "hub": "Western", "hub_name": "WESTERN", "city": "Pittsburgh",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_pjm_price": price, "expected_solrad_mjm2": 12.0,
        "actual_solar_mw": 5000.0,
    }


def test_hold_when_signal_agrees():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.2)

    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "hold"


def test_exit_when_signal_flips():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="LONG", edge=1.0)

    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "exit"
    assert "flipped" in result["reason"].lower()


def test_exit_when_edge_decays():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=2.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=0.4)

    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()


def test_fortify_when_edge_increases():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.6)

    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "fortify"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pjm_position_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Create `pjm/position_manager.py`**

Copy `ercot/position_manager.py` and change:

1. Imports: `PJM_FORTIFY_EDGE_INCREASE`, `PJM_EXIT_EDGE_DECAY`, `PJM_MAX_POSITIONS_PER_HUB`, `PJM_PAPER_BANKROLL`
2. Imports: from `pjm.paper_trader` and `pjm.hubs`
3. Function names: `evaluate_pjm_position`, `run_pjm_manager`
4. `scan_all_hubs` → `scan_all_pjm_hubs`
5. Price key: `current_pjm_price`
6. Rich table title: `"PJM Position Manager"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pjm_position_manager.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pjm/position_manager.py tests/test_pjm_position_manager.py
git commit -m "feat: add PJM position manager with evaluate/fortify/exit logic"
```

---

## Task 7: CAISO hub scanning — `caiso/hubs.py`

**Files:**
- Create: `caiso/__init__.py`
- Create: `caiso/hubs.py`
- Test: `tests/test_caiso_hubs.py` (new)

**Context:** Same pattern as Task 4 but for CAISO. CAISO public endpoints are fully open (no auth). Only SP15 is active initially.

- [ ] **Step 1: Write CAISO hub tests**

Create `tests/test_caiso_hubs.py`:

```python
"""Tests for caiso/hubs.py — hub scanning and CAISO data caching."""
from unittest.mock import patch


def test_scan_all_caiso_hubs_returns_active_only():
    from caiso.hubs import scan_all_caiso_hubs
    signals = scan_all_caiso_hubs()
    assert len(signals) == 1
    assert signals[0]["hub"] == "SP15"


def test_caiso_signal_dict_has_required_keys():
    from caiso.hubs import scan_all_caiso_hubs
    signals = scan_all_caiso_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_caiso_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_caiso_market_data_caching():
    from caiso.hubs import _fetch_caiso_market_data
    data1 = _fetch_caiso_market_data()
    data2 = _fetch_caiso_market_data()
    assert "price" in data1
    assert data1 == data2


def test_fetch_caiso_market_data_has_fallbacks():
    from caiso.hubs import _fetch_caiso_market_data
    data = _fetch_caiso_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)


def test_fetch_caiso_markets_only_active():
    from caiso.hubs import fetch_caiso_markets
    markets = fetch_caiso_markets()
    assert len(markets) == 1
    assert markets[0]["hub"] == "SP15"


def test_fetch_caiso_markets_has_per_hub_price():
    from caiso.hubs import fetch_caiso_markets
    markets = fetch_caiso_markets()
    for m in markets:
        caiso_data = m.get("_caiso_data", {})
        assert "hub_price" in caiso_data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caiso_hubs.py -v`
Expected: FAIL

- [ ] **Step 3: Create `caiso/__init__.py` and `caiso/hubs.py`**

`caiso/hubs.py` adapts PJM pattern with CAISO endpoints:

1. Import `CAISO_HUBS`, `get_caiso_solar_signal`
2. Cache vars: `_caiso_cache`, `_caiso_cache_time`
3. `_fetch_caiso_market_data()`:
   - Prices: `https://www.caiso.com/outlook/SP/fuels.json` (try JSON, fall back gracefully)
   - Load: `https://www.caiso.com/outlook/SP/demand.json`
   - Solar: from same fuels.json (CAISO publishes solar MW)
   - Fallback defaults: `{"price": 40.0, "solar_mw": 10000.0, "load_forecast": 27_000.0}`
   - Telegram dedup: `"caiso_api_down"`
4. `fetch_caiso_markets()`: filter on `active: True`, use `_caiso_data` key, `current_caiso_price`
5. `scan_all_caiso_hubs()`: filter on `active: True`, call `get_caiso_solar_signal`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caiso_hubs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add caiso/__init__.py caiso/hubs.py tests/test_caiso_hubs.py
git commit -m "feat: add CAISO hub scanning with public API data fetching"
```

---

## Task 8: CAISO paper trader — `caiso/paper_trader.py`

**Files:**
- Create: `caiso/paper_trader.py`
- Test: `tests/test_caiso_paper_trader.py` (new)

**Context:** Same pattern as Task 5 but with CAISO config. DB: `data/caiso_paper.db`. Tables: `caiso_positions`, `caiso_trades`, `caiso_scan_cache`. Price key: `current_caiso_price`.

- [ ] **Step 1: Write CAISO paper trader tests**

Create `tests/test_caiso_paper_trader.py`:

```python
"""Tests for caiso/paper_trader.py — paper position management."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
import pytest

TEST_DB = "data/test_caiso_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import caiso.paper_trader as pt
    pt.CAISO_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(hub="SP15", hub_name="SP15", signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": hub, "hub_name": hub_name, "city": "Los Angeles",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_caiso_price": price, "expected_solrad_mjm2": 19.0,
        "actual_solar_mw": 10000.0,
    }


def test_open_position():
    from caiso.paper_trader import open_position, get_open_positions

    sig = _make_signal()
    result = open_position(sig, bankroll=10000.0)
    assert result is not None
    assert result["hub"] == "SP15"
    assert result["signal"] == "SHORT"
    assert result["size_dollars"] > 0

    positions = get_open_positions()
    assert len(positions) == 1


def test_close_position_pnl_short():
    from caiso.paper_trader import open_position, close_position, get_trade_history

    sig = _make_signal(price=40.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=30.0, exit_signal="LONG", reason="signal flipped")

    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0


def test_max_positions_total():
    from caiso.paper_trader import open_position, get_open_positions
    import config
    original = config.CAISO_MAX_POSITIONS_TOTAL
    config.CAISO_MAX_POSITIONS_TOTAL = 1

    open_position(_make_signal(hub="SP15", hub_name="SP15"), bankroll=10000.0)
    result = open_position(_make_signal(hub="NP15", hub_name="NP15"), bankroll=10000.0)

    assert result is None
    assert len(get_open_positions()) == 1
    config.CAISO_MAX_POSITIONS_TOTAL = original


def test_expire_positions():
    from caiso.paper_trader import open_position, expire_positions, get_open_positions, get_trade_history

    sig = _make_signal(price=40.0)
    open_position(sig, bankroll=10000.0)

    conn = sqlite3.connect(TEST_DB)
    conn.execute("UPDATE caiso_positions SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    conn.commit()
    conn.close()

    expire_positions(current_price=42.0)

    assert len(get_open_positions()) == 0
    trades = get_trade_history()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "expired"


def test_paper_summary():
    from caiso.paper_trader import open_position, close_position, get_paper_summary

    sig = _make_signal(price=40.0)
    pos = open_position(sig, bankroll=10000.0)
    close_position(pos["id"], exit_price=30.0, exit_signal="LONG", reason="test")

    summary = get_paper_summary()
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] > 0
    assert summary["open_count"] == 0


def test_scan_cache_write_and_read():
    from caiso.paper_trader import write_scan_cache, get_cached_signals

    signals = [
        {"hub": "SP15", "hub_name": "SP15", "signal": "SHORT",
         "edge": 1.5, "expected_solrad_mjm2": 19.0,
         "current_caiso_price": 40.0, "actual_solar_mw": 10000.0,
         "confidence": 70},
    ]
    write_scan_cache(signals)
    cached = get_cached_signals()
    assert len(cached) >= 1
    assert cached[0]["hub"] == "SP15"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caiso_paper_trader.py -v`
Expected: FAIL

- [ ] **Step 3: Create `caiso/paper_trader.py`**

Copy `pjm/paper_trader.py` (already adapted from ERCOT) and change:

1. DB path: `CAISO_PAPER_DB = "data/caiso_paper.db"`
2. Imports: `CAISO_PAPER_BANKROLL`, `CAISO_POSITION_TTL_HOURS`, etc.
3. Table names: `caiso_positions`, `caiso_trades`, `caiso_scan_cache`
4. Config refs: `_config.CAISO_MAX_POSITIONS_PER_HUB`, `_config.CAISO_MAX_POSITIONS_TOTAL`
5. Price key: `hub_signal["current_caiso_price"]`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caiso_paper_trader.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add caiso/paper_trader.py tests/test_caiso_paper_trader.py
git commit -m "feat: add CAISO paper trader with SQLite-backed positions"
```

---

## Task 9: CAISO position manager — `caiso/position_manager.py`

**Files:**
- Create: `caiso/position_manager.py`
- Test: `tests/test_caiso_position_manager.py` (new)

- [ ] **Step 1: Write CAISO position manager tests**

Create `tests/test_caiso_position_manager.py`:

```python
"""Tests for caiso/position_manager.py — evaluate/fortify/exit logic."""
import os
import pytest

TEST_DB = "data/test_caiso_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import caiso.paper_trader as pt
    pt.CAISO_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": "SP15", "hub_name": "SP15", "city": "Los Angeles",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_caiso_price": price, "expected_solrad_mjm2": 19.0,
        "actual_solar_mw": 10000.0,
    }


def test_hold_when_signal_agrees():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.2)

    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "hold"


def test_exit_when_signal_flips():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="LONG", edge=1.0)

    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "exit"
    assert "flipped" in result["reason"].lower()


def test_exit_when_edge_decays():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=2.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=0.4)

    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()


def test_fortify_when_edge_increases():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.6)

    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "fortify"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caiso_position_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Create `caiso/position_manager.py`**

Copy `pjm/position_manager.py` and change:

1. Imports: `CAISO_FORTIFY_EDGE_INCREASE`, `CAISO_EXIT_EDGE_DECAY`, `CAISO_MAX_POSITIONS_PER_HUB`, `CAISO_PAPER_BANKROLL`
2. Imports: from `caiso.paper_trader` and `caiso.hubs`
3. Function names: `evaluate_caiso_position`, `run_caiso_manager`
4. `scan_all_pjm_hubs` → `scan_all_caiso_hubs`
5. Price key: `current_caiso_price`
6. Rich table title: `"CAISO Position Manager"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caiso_position_manager.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add caiso/position_manager.py tests/test_caiso_position_manager.py
git commit -m "feat: add CAISO position manager with evaluate/fortify/exit logic"
```

---

## Task 10: Pipeline integration — register PJM & CAISO in `pipeline/config.py`

**Files:**
- Modify: `pipeline/config.py`
- Test: `tests/test_pipeline_config.py` (verify existing tests still pass + new configs)

- [ ] **Step 1: Write pipeline config tests for new ISOs**

Add to existing `tests/test_pipeline_config.py` (or create `tests/test_pjm_caiso_pipeline.py` if simpler):

```python
"""Tests for PJM and CAISO pipeline config entries."""


def test_pjm_config_in_all_configs():
    from pipeline.config import ALL_CONFIGS
    names = {c.name for c in ALL_CONFIGS}
    assert "pjm" in names


def test_caiso_config_in_all_configs():
    from pipeline.config import ALL_CONFIGS
    names = {c.name for c in ALL_CONFIGS}
    assert "caiso" in names


def test_pjm_config_fields():
    from pipeline.config import ALL_CONFIGS
    pjm = [c for c in ALL_CONFIGS if c.name == "pjm"][0]
    assert pjm.display_name == "PJM Solar"
    assert pjm.exchange == "pjm"
    assert pjm.edge_gate == 0.03
    assert pjm.confidence_gate == 50
    assert callable(pjm.fetch_fn)
    assert callable(pjm.forecast_fn)
    assert callable(pjm.manage_fn)


def test_caiso_config_fields():
    from pipeline.config import ALL_CONFIGS
    caiso = [c for c in ALL_CONFIGS if c.name == "caiso"][0]
    assert caiso.display_name == "CAISO Solar"
    assert caiso.exchange == "caiso"
    assert callable(caiso.fetch_fn)
    assert callable(caiso.forecast_fn)
    assert callable(caiso.manage_fn)


def test_all_configs_count():
    """Should now have 5 configs: kalshi_temp, kalshi_precip, ercot, pjm, caiso."""
    from pipeline.config import ALL_CONFIGS
    assert len(ALL_CONFIGS) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pjm_caiso_pipeline.py -v`
Expected: FAIL — "pjm" not in ALL_CONFIGS

- [ ] **Step 3: Add PJM and CAISO to `pipeline/config.py`**

In `_build_configs()`:

1. Add imports:
```python
from pjm.hubs import fetch_pjm_markets
from caiso.hubs import fetch_caiso_markets
from weather.multi_model import get_pjm_solar_signal, get_caiso_solar_signal
from config import PJM_HUBS, CAISO_HUBS
from pjm.position_manager import run_pjm_manager
from caiso.position_manager import run_caiso_manager
```

2. Add MarketConfig entries (see spec section 10 for exact values)

3. Update return: `return kalshi_temp, kalshi_precip, ercot, pjm, caiso`

4. Update module-level: `KALSHI_TEMP, KALSHI_PRECIP, ERCOT, PJM, CAISO = _build_configs()`

5. Update: `ALL_CONFIGS = [KALSHI_TEMP, KALSHI_PRECIP, ERCOT, PJM, CAISO]`

- [ ] **Step 4: Run all tests to verify nothing broke**

Run: `pytest tests/test_pjm_caiso_pipeline.py -v && pytest tests/test_pipeline_config.py -v`
Expected: all PASS

- [ ] **Step 5: Run full existing test suite to verify no ERCOT regressions**

Run: `pytest tests/ -v --timeout=60`
Expected: all existing tests PASS, all new tests PASS

- [ ] **Step 6: Commit**

```bash
git add pipeline/config.py tests/test_pjm_caiso_pipeline.py
git commit -m "feat: register PJM and CAISO in pipeline config (ALL_CONFIGS now 5 markets)"
```

---

## Task 11: Verify full system — integration smoke test

**Files:**
- No new files — verification only

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: all tests PASS

- [ ] **Step 2: Smoke test PJM scanning**

Run: `python -c "from pjm.hubs import scan_all_pjm_hubs; signals = scan_all_pjm_hubs(); print(f'PJM: {len(signals)} hubs scanned'); [print(f'  {s[\"hub\"]}: {s[\"signal\"]} edge={s[\"edge\"]:.4f} conf={s[\"confidence\"]}') for s in signals]"`

Expected: 2 hubs (Western, AEP-Dayton) with LONG/SHORT/NEUTRAL signals

- [ ] **Step 3: Smoke test CAISO scanning**

Run: `python -c "from caiso.hubs import scan_all_caiso_hubs; signals = scan_all_caiso_hubs(); print(f'CAISO: {len(signals)} hubs scanned'); [print(f'  {s[\"hub\"]}: {s[\"signal\"]} edge={s[\"edge\"]:.4f} conf={s[\"confidence\"]}') for s in signals]"`

Expected: 1 hub (SP15) with LONG/SHORT/NEUTRAL signal

- [ ] **Step 4: Verify ERCOT still works unchanged**

Run: `python -c "from ercot.hubs import scan_all_hubs; signals = scan_all_hubs(); print(f'ERCOT: {len(signals)} hubs scanned')"`

Expected: 5 hubs — proves zero regression

- [ ] **Step 5: Final commit with all changes**

```bash
git add -A
git status  # verify no unexpected files
git commit -m "feat: PJM and CAISO power market expansion — paper trading ready"
```
