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
        mock_get.side_effect = _mock_both_solar(vc_solrad=12.5, om_solrad=12.5)
        result = get_pjm_solar_signal(
            40.44, -80.00, hub_key="Western", solar_sensitivity=0.20,
            pjm_data=_make_pjm_data(hub_price=35.0, load_forecast=100000.0),
        )

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

    assert result["confidence"] >= 50
