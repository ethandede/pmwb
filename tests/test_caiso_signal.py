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
    """SP15 sensitivity 0.35 should produce larger edge for same deviation."""
    from weather.multi_model import get_caiso_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        mock_get.side_effect = _mock_both_solar(vc_solrad=14.0, om_solrad=14.0)
        result = get_caiso_solar_signal(
            34.05, -118.24, hub_key="SP15", solar_sensitivity=0.35,
            caiso_data=_make_caiso_data(load_forecast=23000.0),
        )

    # solar_impact = 0.35 * (19 - 14) / 19 = 0.092
    assert result["edge"] > 0.08
