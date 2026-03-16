# ERCOT Signal Rework Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the threshold-based ERCOT signal with a per-hub fair-price model that uses solar sensitivity coefficients, load forecast, and per-hub settlement prices to generate differentiated trading signals.

**Architecture:** The fair-price model computes `edge = solar_impact + load_impact` where each hub has its own solar sensitivity coefficient and receives its own settlement price. Confidence is multi-factor (forecast agreement + edge magnitude). Both call paths (pipeline `score_signal` and direct `scan_all_hubs`) get updated.

**Tech Stack:** Python, pytest, unittest.mock, existing ERCOT API auth + weather APIs

**Spec:** `docs/superpowers/specs/2026-03-16-ercot-signal-rework-design.md`

---

## Chunk 1: Config + Fair Price Signal

### Task 1: Add seasonal norms and solar sensitivity to config.py

**Files:**
- Modify: `config.py:73-93`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ercot_config_norms.py`:

```python
"""Tests for ERCOT seasonal norms and per-hub solar sensitivity in config."""


def test_seasonal_norms_has_all_12_months():
    from config import ERCOT_SEASONAL_NORMS
    for month in range(1, 13):
        assert month in ERCOT_SEASONAL_NORMS, f"Missing month {month}"
        assert "solar" in ERCOT_SEASONAL_NORMS[month]
        assert "load" in ERCOT_SEASONAL_NORMS[month]


def test_seasonal_norms_values_are_reasonable():
    from config import ERCOT_SEASONAL_NORMS
    for month, norms in ERCOT_SEASONAL_NORMS.items():
        assert 5.0 <= norms["solar"] <= 30.0, f"Month {month} solar {norms['solar']} out of range"
        assert 30_000 <= norms["load"] <= 80_000, f"Month {month} load {norms['load']} out of range"


def test_load_sensitivity_exists():
    from config import ERCOT_LOAD_SENSITIVITY
    assert isinstance(ERCOT_LOAD_SENSITIVITY, float)
    assert 0.0 < ERCOT_LOAD_SENSITIVITY < 1.0


def test_each_hub_has_solar_sensitivity():
    from config import ERCOT_HUBS
    for hub_key, info in ERCOT_HUBS.items():
        assert "solar_sensitivity" in info, f"Hub {hub_key} missing solar_sensitivity"
        assert 0.0 < info["solar_sensitivity"] <= 1.0


def test_hub_sensitivities_are_differentiated():
    """West should be most sensitive, Houston least."""
    from config import ERCOT_HUBS
    assert ERCOT_HUBS["West"]["solar_sensitivity"] > ERCOT_HUBS["Houston"]["solar_sensitivity"]
    assert ERCOT_HUBS["West"]["solar_sensitivity"] > ERCOT_HUBS["North"]["solar_sensitivity"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ercot_config_norms.py -v`
Expected: FAIL — `ERCOT_SEASONAL_NORMS` not defined, `solar_sensitivity` not in hub dicts

- [ ] **Step 3: Add config constants**

In `config.py`, after `ERCOT_POSITION_TTL_HOURS = 24` (line 93), add:

```python
ERCOT_LOAD_SENSITIVITY = 0.15

ERCOT_SEASONAL_NORMS = {
    1:  {"solar": 10.0, "load": 50_000},
    2:  {"solar": 12.0, "load": 47_000},
    3:  {"solar": 16.0, "load": 45_000},
    4:  {"solar": 20.0, "load": 43_000},
    5:  {"solar": 22.0, "load": 48_000},
    6:  {"solar": 25.0, "load": 60_000},
    7:  {"solar": 26.0, "load": 70_000},
    8:  {"solar": 25.0, "load": 68_000},
    9:  {"solar": 20.0, "load": 55_000},
    10: {"solar": 16.0, "load": 45_000},
    11: {"solar": 12.0, "load": 43_000},
    12: {"solar": 10.0, "load": 48_000},
}
```

Also update `ERCOT_HUBS` to include `solar_sensitivity` per hub:

```python
ERCOT_HUBS = {
    "North":     {"city": "Dallas",      "lat": 32.78,  "lon": -96.80,  "hub_name": "HB_NORTH",   "solar_sensitivity": 0.15},
    "Houston":   {"city": "Houston",     "lat": 29.76,  "lon": -95.37,  "hub_name": "HB_HOUSTON", "solar_sensitivity": 0.10},
    "South":     {"city": "San Antonio", "lat": 29.42,  "lon": -98.49,  "hub_name": "HB_SOUTH",   "solar_sensitivity": 0.20},
    "West":      {"city": "Midland",     "lat": 31.99,  "lon": -102.08, "hub_name": "HB_WEST",    "solar_sensitivity": 0.35},
    "Panhandle": {"city": "Amarillo",    "lat": 35.22,  "lon": -101.83, "hub_name": "HB_PAN",     "solar_sensitivity": 0.25},
}
```

Also recalibrate position manager thresholds for the new edge range (0.03-0.10 vs old 0-0.99):

```python
ERCOT_MIN_EDGE = 0.03
ERCOT_FORTIFY_EDGE_INCREASE = 0.03  # was 0.5 — impossible with new edge range
```

Remove orphaned `ERCOT_MIN_EDGE` if not imported anywhere (it is dead code — the live gate is `pipeline/config.py` `edge_gate`). Keep it only as a reference constant. Add a comment: `# Reference only — live gate is pipeline/config.py edge_gate`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ercot_config_norms.py -v`
Expected: All 5 PASS

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_ercot_config_norms.py
git commit -m "feat(ercot): add seasonal norms, per-hub solar sensitivity, recalibrate edge gate"
```

---

### Task 2: Rewrite get_ercot_solar_signal() with fair price model

**Files:**
- Modify: `weather/multi_model.py:546-641` (ERCOT section comment through return dict)
- Test: `tests/test_ercot_signal.py` (rewrite)

- [ ] **Step 1: Write the failing tests**

Replace the contents of `tests/test_ercot_signal.py` with tests for the new signature and behavior:

```python
"""Tests for get_ercot_solar_signal — fair price model with per-hub sensitivity."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

# All tests pin to March so seasonal norms are deterministic.
MARCH_DATETIME = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)


def _make_ercot_data(hub_price=40.0, price=40.0, solar_mw=12000.0, load_forecast=45000.0):
    """Helper to build ercot_data dict."""
    return {
        "hub_price": hub_price,
        "price": price,
        "solar_mw": solar_mw,
        "load_forecast": load_forecast,
    }


def _mock_both_solar(vc_solrad=16.0, om_solrad=16.0):
    """Return a side_effect for http_get that returns VC then OM responses."""
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


# Decorator to pin datetime.now to March for all signal tests
_patch_march = patch("weather.multi_model.datetime",
                     wraps=datetime,
                     **{"now.return_value": MARCH_DATETIME})


# ============================================================================
# Core signal logic — fair price model
# ============================================================================

def test_new_signature_requires_hub_key_and_sensitivity():
    """New signature needs hub_key and solar_sensitivity params."""
    from weather.multi_model import get_ercot_solar_signal
    import inspect
    sig = inspect.signature(get_ercot_solar_signal)
    assert "hub_key" in sig.parameters
    assert "solar_sensitivity" in sig.parameters


@_patch_march
def test_short_signal_when_solar_above_norm(_mock_dt):
    """Above-norm solar → negative edge → SHORT."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=24.0, om_solrad=24.0)

        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    assert result["signal"] == "SHORT"
    assert result["edge"] < 0


@_patch_march
def test_long_signal_when_solar_below_norm(_mock_dt):
    """Below-norm solar → positive edge → LONG."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=8.0, om_solrad=8.0)

        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    assert result["signal"] == "LONG"
    assert result["edge"] > 0


@_patch_march
def test_neutral_when_solar_at_norm(_mock_dt):
    """Solar at seasonal norm → near-zero edge → could be NEUTRAL or tiny signal."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=16.0, om_solrad=16.0)

        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    # With solar at norm and load at norm, edge should be ~0
    assert abs(result["edge"]) < 0.01


# ============================================================================
# Per-hub differentiation
# ============================================================================

@_patch_march
def test_west_has_larger_edge_than_houston_same_solar(_mock_dt):
    """HB_WEST (sensitivity=0.35) should produce larger edge than HB_HOUSTON (0.10)."""
    from weather.multi_model import get_ercot_solar_signal

    solrad = 24.0  # well above March norm of 16

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=solrad, om_solrad=solrad)
        west = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=solrad, om_solrad=solrad)
        houston = get_ercot_solar_signal(
            29.76, -95.37, hub_key="Houston", solar_sensitivity=0.10,
            ercot_data=_make_ercot_data(hub_price=55.0, load_forecast=45000.0),
        )

    assert abs(west["edge"]) > abs(houston["edge"])


@_patch_march
def test_different_hub_prices_in_result(_mock_dt):
    """Each hub should report its own hub price, not the grid average."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=20.0, om_solrad=20.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=25.0, price=40.0),
        )

    assert result["current_ercot_price"] == 25.0  # hub price, not grid avg


# ============================================================================
# Load impact
# ============================================================================

@_patch_march
def test_high_load_increases_edge(_mock_dt):
    """Above-norm load should push edge positive (price up)."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=16.0, om_solrad=16.0)
        normal_load = get_ercot_solar_signal(
            32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
            ercot_data=_make_ercot_data(hub_price=40.0, load_forecast=45000.0),
        )

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=16.0, om_solrad=16.0)
        high_load = get_ercot_solar_signal(
            32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
            ercot_data=_make_ercot_data(hub_price=40.0, load_forecast=60000.0),
        )

    assert high_load["edge"] > normal_load["edge"]


# ============================================================================
# Confidence model — dual source agreement
# ============================================================================

@_patch_march
def test_confidence_higher_when_sources_agree(_mock_dt):
    """Confidence should be higher when VC and OM agree within 2 MJ/m²."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=24.0, om_solrad=24.5)
        agree = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=24.0, om_solrad=14.0)
        disagree = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    assert agree["confidence"] > disagree["confidence"]


@_patch_march
def test_confidence_capped_at_90_floored_at_30(_mock_dt):
    """Confidence must stay within [30, 90]."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=24.0, om_solrad=24.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    assert 30 <= result["confidence"] <= 90


# ============================================================================
# Unit validation for Open-Meteo
# ============================================================================

@_patch_march
def test_om_unit_conversion_kjm2(_mock_dt):
    """If Open-Meteo reports kJ/m², it should be converted to MJ/m²."""
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": 20.0}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [20000.0]},  # 20000 kJ = 20 MJ
        "daily_units": {"shortwave_radiation_sum": "kJ/m²"},
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    # Sources should agree (both ~20 MJ/m²) → agreement_bonus applied
    assert result["confidence"] >= 50  # base 30 + agreement 20


@_patch_march
def test_om_unit_conversion_whm2(_mock_dt):
    """If Open-Meteo reports Wh/m², it should be converted to MJ/m²."""
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": 20.0}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [5555.6]},  # 5555.6 Wh * 0.0036 = ~20 MJ
        "daily_units": {"shortwave_radiation_sum": "Wh/m²"},
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    # After conversion both should be ~20 MJ/m² → agreement_bonus
    assert result["confidence"] >= 50


@_patch_march
def test_om_unknown_unit_skips_agreement(_mock_dt):
    """If OM unit is unrecognized, skip agreement check per spec."""
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": 20.0}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [20.0]},
        "daily_units": {"shortwave_radiation_sum": "BTU/ft²"},  # unrecognized
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    # Unrecognized unit → no agreement bonus → confidence should be lower
    # base 30 + 0 (no agreement) + deviation bonus only
    assert result["confidence"] < 60


# ============================================================================
# Fallback paths
# ============================================================================

@_patch_march
def test_ercot_data_none_uses_defaults(_mock_dt):
    """When ercot_data is None, use fallback defaults."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=20.0, om_solrad=20.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
        )

    assert result["current_ercot_price"] == 40.0  # default
    assert result["signal"] in ("SHORT", "LONG", "NEUTRAL")


@_patch_march
def test_vc_failure_falls_back_to_om(_mock_dt):
    """When VC fails, use OM as primary solrad."""
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.raise_for_status.side_effect = Exception("VC down")

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [8.0]},
        "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    assert result["expected_solrad_mjm2"] == 8.0  # fell back to OM
    assert result["signal"] == "LONG"  # below norm


# ============================================================================
# Return dict shape
# ============================================================================

@_patch_march
def test_return_dict_has_required_keys(_mock_dt):
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=20.0, om_solrad=20.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    required = {"signal", "edge", "expected_solrad_mjm2", "current_ercot_price",
                "actual_solar_mw", "confidence"}
    assert required.issubset(result.keys()), f"Missing: {required - result.keys()}"


# ============================================================================
# Daemon integration (unchanged)
# ============================================================================

def test_daemon_calls_ercot_manager():
    """daemon.run_cycle should call run_ercot_manager in Phase 1."""
    import importlib
    import daemon as daemon_mod

    source = importlib.util.find_spec("daemon").origin
    with open(source) as f:
        code = f.read()

    assert "run_ercot_manager" in code
    assert "from ercot.position_manager import run_ercot_manager" in code
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ercot_signal.py -v`
Expected: Most FAIL — `hub_key` param doesn't exist yet, wrong signal logic

- [ ] **Step 3: Rewrite get_ercot_solar_signal()**

Replace lines 546-641 of `weather/multi_model.py` with the new implementation.

**Important:** Add `from datetime import datetime, timezone` at the module level (near existing imports) so tests can mock `weather.multi_model.datetime`. Do NOT import datetime inside the function.

The function should:

1. Accept new params: `hub_key: str`, `solar_sensitivity: float`
2. Fetch VC solrad (primary) and OM solrad (cross-reference) — both fetched, not try/fallback
3. Check OM units via `daily_units` field; convert kJ/m² → MJ/m² or Wh/m² → MJ/m²
4. Extract hub_price from `ercot_data["hub_price"]` (fallback to `ercot_data["price"]`, then 40.0)
5. Extract load_forecast from `ercot_data["load_forecast"]` (fallback to seasonal norm)
6. Look up seasonal norms for current month from `ERCOT_SEASONAL_NORMS`
7. Compute: `solar_impact = solar_sensitivity * (norm_solar - expected_solrad) / norm_solar`
8. Compute: `load_impact = ERCOT_LOAD_SENSITIVITY * (load_forecast - norm_load) / norm_load`
9. `edge = solar_impact + load_impact`
10. Signal: `LONG` if edge > 0, `SHORT` if edge < 0, else `NEUTRAL`
11. Confidence: `base(30) + agreement(20 if VC/OM within 2 MJ/m²) + deviation(min(30, abs(edge)*300))`, capped [30, 90]
12. Return dict with same keys as before (backwards compat for scan_cache)

```python
def get_ercot_solar_signal(
    lat: float, lon: float,
    hub_key: str = "",
    solar_sensitivity: float = 0.15,
    hours_ahead: int = 24,
    ercot_data: dict = None,
) -> dict:
    """Solar irradiance + ERCOT price → fair-price trading signal.

    Uses per-hub solar sensitivity and load forecast to estimate
    whether the current hub price is above or below fair value.
    """
    from config import (
        VISUAL_CROSSING_API_KEY, ERCOT_SEASONAL_NORMS,
        ERCOT_LOAD_SENSITIVITY,
    )

    # 1. Seasonal norms for current month
    # Note: uses module-level datetime import (not local) so tests can mock it
    month = datetime.now(timezone.utc).month
    norms = ERCOT_SEASONAL_NORMS.get(month)
    if norms is None:
        try:
            from alerts.telegram_alert import send_alert
            send_alert("ERCOT Norms Missing", f"No seasonal norms for month {month}",
                       dedup_key="ercot_norms_missing")
        except Exception:
            pass
        norms = {"solar": 18.0, "load": 50_000}
    norm_solar = norms["solar"]
    norm_load = norms["load"]

    # 2a. Visual Crossing solrad (primary)
    vc_solrad = None
    if VISUAL_CROSSING_API_KEY:
        try:
            vc_r = http_get(
                f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
                f"/{lat},{lon}/next3days",
                params={"key": VISUAL_CROSSING_API_KEY, "unitGroup": "metric",
                        "include": "days", "elements": "solarenergy"},
                timeout=10,
            )
            vc_r.raise_for_status()
            vc_days = vc_r.json().get("days", [])
            target_idx = min(hours_ahead // 24, len(vc_days) - 1)
            if vc_days and vc_days[target_idx].get("solarenergy") is not None:
                vc_solrad = float(vc_days[target_idx]["solarenergy"])
        except Exception as e:
            print(f"  Visual Crossing solar error: {e}")

    # 2b. Open-Meteo solrad (cross-reference for confidence)
    om_solrad = None
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
        data = r.json()
        radiation = data.get("daily", {}).get("shortwave_radiation_sum", [])
        target_idx = min(hours_ahead // 24, len(radiation) - 1)
        raw_om = radiation[target_idx] if radiation else None

        if raw_om is not None:
            # Unit validation — skip agreement check if unit unrecognized
            unit = data.get("daily_units", {}).get("shortwave_radiation_sum", "")
            if "kJ" in unit:
                om_solrad = raw_om / 1000.0
            elif "Wh" in unit:
                om_solrad = raw_om * 0.0036
            elif "MJ" in unit:
                om_solrad = raw_om
            else:
                # Unrecognized unit — log warning, skip agreement check
                print(f"  Open-Meteo unknown solar unit: {unit!r}, skipping agreement")
                om_solrad = None  # don't use for agreement
    except Exception as e:
        print(f"  Open-Meteo solar error: {e}")

    # 2c. Pick primary solrad: VC preferred, OM fallback, then seasonal norm
    if vc_solrad is not None:
        expected_solrad = vc_solrad
    elif om_solrad is not None:
        expected_solrad = om_solrad
    else:
        expected_solrad = norm_solar  # no data = no solar signal

    # 3. ERCOT market data
    if ercot_data is not None:
        hub_price = float(ercot_data.get("hub_price", ercot_data.get("price", 40.0)))
        actual_solar_mw = float(ercot_data.get("solar_mw", 0.0))
        load_forecast = float(ercot_data.get("load_forecast", norm_load))
    else:
        hub_price = 40.0
        actual_solar_mw = 12000.0
        load_forecast = norm_load

    # 4. Fair price model
    solar_impact = solar_sensitivity * (norm_solar - expected_solrad) / norm_solar
    load_impact = ERCOT_LOAD_SENSITIVITY * (load_forecast - norm_load) / norm_load
    edge = round(solar_impact + load_impact, 4)

    # 5. Signal direction
    if edge > 0:
        signal = "LONG"
    elif edge < 0:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    # 6. Confidence: base + agreement + deviation
    base_conf = 30
    agreement_bonus = 0
    if vc_solrad is not None and om_solrad is not None:
        if abs(vc_solrad - om_solrad) <= 2.0:
            agreement_bonus = 20
    price_deviation_bonus = min(30, abs(edge) * 300)
    confidence = int(min(90, max(30, base_conf + agreement_bonus + price_deviation_bonus)))

    return {
        "signal": signal,
        "edge": round(edge, 4),
        "expected_solrad_mjm2": round(expected_solrad, 1),
        "current_ercot_price": round(hub_price, 1),
        "actual_solar_mw": round(actual_solar_mw, 0),
        "confidence": confidence,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ercot_signal.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add weather/multi_model.py tests/test_ercot_signal.py
git commit -m "feat(ercot): rewrite signal with fair-price model, dual-source solar, per-hub sensitivity"
```

---

## Chunk 2: Hub Data Routing + Pipeline Integration

### Task 3: Update fetch_ercot_markets() and scan_all_hubs() to pass per-hub data

**Files:**
- Modify: `ercot/hubs.py:87-124`
- Test: `tests/test_ercot_hubs.py` (update)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ercot_hubs.py`:

```python
def test_fetch_ercot_markets_includes_per_hub_fields():
    """Each market dict must include hub_key and solar_sensitivity."""
    from ercot.hubs import fetch_ercot_markets
    markets = fetch_ercot_markets()
    for m in markets:
        assert "hub_key" in m, f"Missing hub_key in market for {m.get('hub_name')}"
        assert "solar_sensitivity" in m, f"Missing solar_sensitivity in market for {m.get('hub_name')}"
        assert isinstance(m["solar_sensitivity"], float)


def test_fetch_ercot_markets_has_per_hub_price_in_ercot_data():
    """The _ercot_data dict should have a hub_price field for each market."""
    from ercot.hubs import fetch_ercot_markets
    markets = fetch_ercot_markets()
    for m in markets:
        ercot_data = m.get("_ercot_data", {})
        assert "hub_price" in ercot_data, f"Missing hub_price in _ercot_data for {m.get('hub_name')}"


def test_scan_all_hubs_uses_new_signature():
    """scan_all_hubs should pass hub_key and solar_sensitivity to signal function."""
    from ercot.hubs import scan_all_hubs
    signals = scan_all_hubs()
    # All signals should still have required keys
    for sig in signals:
        assert "signal" in sig
        assert "edge" in sig
        assert sig["signal"] in ("SHORT", "LONG", "NEUTRAL")


def test_per_hub_prices_are_differentiated():
    """When ERCOT returns different hub prices, each market should get its own."""
    from unittest.mock import patch, MagicMock
    from ercot.hubs import fetch_ercot_markets, _fetch_ercot_market_data

    fake_data = {
        "price": 40.0,
        "solar_mw": 12000.0,
        "load_forecast": 45000.0,
        "hub_prices": {
            "HB_NORTH": 35.0,
            "HB_HOUSTON": 50.0,
            "HB_SOUTH": 42.0,
            "HB_WEST": 20.0,
            "HB_PAN": 28.0,
        },
    }

    with patch("ercot.hubs._fetch_ercot_market_data", return_value=fake_data):
        markets = fetch_ercot_markets()

    prices = {m["hub_name"]: m["current_ercot_price"] for m in markets}
    assert prices["HB_WEST"] == 20.0
    assert prices["HB_HOUSTON"] == 50.0
    assert prices["HB_NORTH"] == 35.0
    # Verify _ercot_data also has correct hub_price
    west_market = [m for m in markets if m["hub_name"] == "HB_WEST"][0]
    assert west_market["_ercot_data"]["hub_price"] == 20.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ercot_hubs.py -v`
Expected: FAIL — `hub_key` and `solar_sensitivity` not in market dicts

- [ ] **Step 3: Update ercot/hubs.py**

Modify `fetch_ercot_markets()` to include per-hub price and new fields:

```python
def fetch_ercot_markets() -> list[dict]:
    """Return raw hub data + shared ERCOT market data for pipeline fetch stage."""
    market_data = _fetch_ercot_market_data()
    hub_prices = market_data.get("hub_prices", {})
    grid_avg = market_data.get("price", 40.0)
    markets = []
    for hub_key, info in ERCOT_HUBS.items():
        hub_name = info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        # Build per-hub ercot_data with hub_price injected
        per_hub_data = dict(market_data)
        per_hub_data["hub_price"] = hub_price

        markets.append({
            "hub": hub_key,
            "hub_key": hub_key,
            "hub_name": hub_name,
            "city": info["city"],
            "lat": info["lat"],
            "lon": info["lon"],
            "solar_sensitivity": info["solar_sensitivity"],
            "current_ercot_price": hub_price,
            "actual_solar_mw": market_data.get("solar_mw", 12000.0),
            "_ercot_data": per_hub_data,
        })
    return markets
```

Modify `scan_all_hubs()` to pass hub_key and solar_sensitivity:

```python
def scan_all_hubs() -> list:
    """Scan all 5 ERCOT hubs. Returns list of enriched signal dicts."""
    ercot_data = _fetch_ercot_market_data()
    hub_prices = ercot_data.get("hub_prices", {})
    grid_avg = ercot_data.get("price", 40.0)
    signals = []

    for hub_key, hub_info in ERCOT_HUBS.items():
        hub_name = hub_info["hub_name"]
        hub_price = hub_prices.get(hub_name, grid_avg)

        per_hub_data = dict(ercot_data)
        per_hub_data["hub_price"] = hub_price

        signal = get_ercot_solar_signal(
            hub_info["lat"], hub_info["lon"],
            hub_key=hub_key,
            solar_sensitivity=hub_info["solar_sensitivity"],
            hours_ahead=24,
            ercot_data=per_hub_data,
        )
        signal["hub"] = hub_key
        signal["hub_name"] = hub_name
        signal["city"] = hub_info["city"]
        signals.append(signal)

    return signals
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ercot_hubs.py -v`
Expected: All PASS (including original tests)

- [ ] **Step 5: Commit**

```bash
git add ercot/hubs.py tests/test_ercot_hubs.py
git commit -m "feat(ercot): route per-hub price and sensitivity through fetch and scan"
```

---

### Task 4: Update pipeline score_signal() and ERCOT config

**Files:**
- Modify: `pipeline/stages.py:36-47`
- Modify: `pipeline/config.py:178`
- Test: `tests/test_pipeline_config.py` (update)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline_config.py`:

```python
def test_ercot_edge_gate_recalibrated():
    """ERCOT edge gate should be 0.03 for the new fair-price model."""
    assert ERCOT.edge_gate == 0.03
```

And create `tests/test_ercot_pipeline_signal.py`:

```python
"""Test that score_signal produces valid ERCOT signals with new model."""

from unittest.mock import MagicMock, patch


def test_score_signal_ercot_passes_hub_params():
    """score_signal should pass hub_key and solar_sensitivity to forecast_fn."""
    from pipeline.stages import score_signal
    from pipeline.config import ERCOT

    market = {
        "hub_name": "HB_WEST",
        "hub_key": "West",
        "solar_sensitivity": 0.35,
        "city": "Midland",
        "lat": 31.99,
        "lon": -102.08,
        "_ercot_data": {"hub_price": 25.0, "price": 40.0, "solar_mw": 12000.0, "load_forecast": 45000.0},
    }

    with patch("weather.multi_model.http_get") as mock_get:
        vc_resp = MagicMock()
        vc_resp.json.return_value = {"days": [{"solarenergy": 24.0}]}
        vc_resp.raise_for_status = MagicMock()
        om_resp = MagicMock()
        om_resp.json.return_value = {
            "daily": {"shortwave_radiation_sum": [24.0]},
            "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
        }
        om_resp.raise_for_status = MagicMock()
        mock_get.side_effect = [vc_resp, om_resp]

        signal = score_signal(ERCOT, market)

    assert signal.ticker == "HB_WEST"
    assert signal.side in ("yes", "no")
    assert 0.01 <= signal.model_prob <= 0.99


def test_score_signal_ercot_model_prob_is_meaningful():
    """model_prob should be 0.5 + edge, not always ~1.0."""
    from pipeline.stages import score_signal
    from pipeline.config import ERCOT

    market = {
        "hub_name": "HB_WEST",
        "hub_key": "West",
        "solar_sensitivity": 0.35,
        "city": "Midland",
        "lat": 31.99,
        "lon": -102.08,
        "_ercot_data": {"hub_price": 25.0, "price": 40.0, "solar_mw": 12000.0, "load_forecast": 45000.0},
    }

    with patch("weather.multi_model.http_get") as mock_get:
        vc_resp = MagicMock()
        vc_resp.json.return_value = {"days": [{"solarenergy": 8.0}]}
        vc_resp.raise_for_status = MagicMock()
        om_resp = MagicMock()
        om_resp.json.return_value = {
            "daily": {"shortwave_radiation_sum": [8.0]},
            "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
        }
        om_resp.raise_for_status = MagicMock()
        mock_get.side_effect = [vc_resp, om_resp]

        signal = score_signal(ERCOT, market)

    # Low solar → LONG → positive edge → model_prob > 0.5
    assert signal.model_prob > 0.5
    assert signal.model_prob < 0.99  # shouldn't be clamped to max
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_config.py tests/test_ercot_pipeline_signal.py -v`
Expected: FAIL — edge_gate is still 0.005, hub_key not passed

- [ ] **Step 3: Update pipeline/stages.py ERCOT branch**

Replace lines 36-47 of `pipeline/stages.py`:

```python
    if config.name == "ercot":
        forecast_result = config.forecast_fn(
            market.get("lat", 0), market.get("lon", 0),
            hub_key=market.get("hub_key", ""),
            solar_sensitivity=market.get("solar_sensitivity", 0.15),
            hours_ahead=24,
            ercot_data=market.get("_ercot_data"),
        )
        edge = forecast_result.get("edge", 0)
        model_prob = max(0.01, min(0.99, 0.5 + edge))
        confidence = forecast_result.get("confidence", 50)
        ercot_signal = forecast_result.get("signal", "NEUTRAL")
        side = "no" if ercot_signal == "SHORT" else "yes"
        days_ahead = 0
```

- [ ] **Step 4: Update pipeline/config.py edge_gate**

Change line 178: `edge_gate=0.005` → `edge_gate=0.03`

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_config.py tests/test_ercot_pipeline_signal.py tests/test_ercot_signal.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add pipeline/stages.py pipeline/config.py tests/test_pipeline_config.py tests/test_ercot_pipeline_signal.py
git commit -m "feat(ercot): update pipeline to pass per-hub params, fix model_prob, recalibrate edge gate"
```

---

### Task 5: Run full test suite and verify no regressions

**Files:** None (verification only)

- [ ] **Step 1: Run all ERCOT tests**

Run: `pytest tests/test_ercot_signal.py tests/test_ercot_hubs.py tests/test_ercot_config_norms.py tests/test_ercot_pipeline_signal.py tests/test_pipeline_config.py tests/test_ercot_paper_trader.py tests/test_ercot_position_manager.py tests/test_ercot_integration.py -v`
Expected: All PASS

- [ ] **Step 2: Run full test suite for regressions**

Run: `pytest --tb=short -q`
Expected: No new failures. Any pre-existing failures should be noted but not fixed in this PR.

- [ ] **Step 3: Spot-check signal differentiation manually**

Run a quick Python script to verify hubs produce different signals:

```bash
python -c "
from ercot.hubs import scan_all_hubs
signals = scan_all_hubs()
for s in signals:
    print(f\"{s['hub_name']:12s} signal={s['signal']:7s} edge={s['edge']:+.4f} conf={s['confidence']} price=\${s['current_ercot_price']:.1f} solrad={s['expected_solrad_mjm2']}\")
"
```

Verify: edges differ across hubs (especially HB_WEST vs HB_HOUSTON), prices are per-hub (not all the same).

- [ ] **Step 4: Commit any test fixups if needed**

Only if step 1/2 revealed fixable issues.
