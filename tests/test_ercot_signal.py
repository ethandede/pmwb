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


def _ercot_signal_test(func):
    """Combined decorator: pin datetime to March + ensure VC API key is truthy."""
    @patch("config.VISUAL_CROSSING_API_KEY", "test-key-123")
    @patch("weather.multi_model.datetime", wraps=datetime,
           **{"now.return_value": MARCH_DATETIME})
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__qualname__ = func.__qualname__
    return wrapper


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


@_ercot_signal_test
def test_short_signal_when_solar_above_norm(_mock_dt):
    """Above-norm solar -> negative edge -> SHORT."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=24.0, om_solrad=24.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    assert result["signal"] == "SHORT"
    assert result["edge"] < 0


@_ercot_signal_test
def test_long_signal_when_solar_below_norm(_mock_dt):
    """Below-norm solar -> positive edge -> LONG."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=8.0, om_solrad=8.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    assert result["signal"] == "LONG"
    assert result["edge"] > 0


@_ercot_signal_test
def test_neutral_when_solar_at_norm(_mock_dt):
    """Solar at seasonal norm with normal load -> near-zero edge."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=16.0, om_solrad=16.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0, load_forecast=45000.0),
        )

    assert abs(result["edge"]) < 0.01


# ============================================================================
# Per-hub differentiation
# ============================================================================

@_ercot_signal_test
def test_west_has_larger_edge_than_houston_same_solar(_mock_dt):
    """HB_WEST (sensitivity=0.35) should produce larger edge than HB_HOUSTON (0.10)."""
    from weather.multi_model import get_ercot_solar_signal

    solrad = 24.0

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


@_ercot_signal_test
def test_different_hub_prices_in_result(_mock_dt):
    """Each hub should report its own hub price, not the grid average."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=20.0, om_solrad=20.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=25.0, price=40.0),
        )

    assert result["current_ercot_price"] == 25.0


# ============================================================================
# Load impact
# ============================================================================

@_ercot_signal_test
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

@_ercot_signal_test
def test_confidence_higher_when_sources_agree(_mock_dt):
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


@_ercot_signal_test
def test_confidence_capped_at_90_floored_at_30(_mock_dt):
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

@_ercot_signal_test
def test_om_unit_conversion_kjm2(_mock_dt):
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": 20.0}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [20000.0]},
        "daily_units": {"shortwave_radiation_sum": "kJ/m²"},
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    assert result["confidence"] >= 50  # base 30 + agreement 20


@_ercot_signal_test
def test_om_unit_conversion_whm2(_mock_dt):
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": 20.0}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [5555.6]},
        "daily_units": {"shortwave_radiation_sum": "Wh/m²"},
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    assert result["confidence"] >= 50


@_ercot_signal_test
def test_om_unknown_unit_skips_agreement(_mock_dt):
    from weather.multi_model import get_ercot_solar_signal

    vc_resp = MagicMock()
    vc_resp.json.return_value = {"days": [{"solarenergy": 20.0}]}
    vc_resp.raise_for_status = MagicMock()

    om_resp = MagicMock()
    om_resp.json.return_value = {
        "daily": {"shortwave_radiation_sum": [20.0]},
        "daily_units": {"shortwave_radiation_sum": "BTU/ft²"},
    }
    om_resp.raise_for_status = MagicMock()

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = [vc_resp, om_resp]
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
            ercot_data=_make_ercot_data(hub_price=30.0),
        )

    assert result["confidence"] < 60


# ============================================================================
# Fallback paths
# ============================================================================

@_ercot_signal_test
def test_ercot_data_none_uses_defaults(_mock_dt):
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=20.0, om_solrad=20.0)
        result = get_ercot_solar_signal(
            31.99, -102.08, hub_key="West", solar_sensitivity=0.35,
        )

    assert result["current_ercot_price"] == 40.0
    assert result["signal"] in ("SHORT", "LONG", "NEUTRAL")


@_ercot_signal_test
def test_vc_failure_falls_back_to_om(_mock_dt):
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

    assert result["expected_solrad_mjm2"] == 8.0
    assert result["signal"] == "LONG"


# ============================================================================
# Return dict shape
# ============================================================================

@_ercot_signal_test
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
    import importlib
    import daemon as daemon_mod

    source = importlib.util.find_spec("daemon").origin
    with open(source) as f:
        code = f.read()

    assert "run_ercot_manager" in code
    assert "from ercot.position_manager import run_ercot_manager" in code
