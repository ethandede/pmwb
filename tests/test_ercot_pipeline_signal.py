"""Test that score_signal produces valid ERCOT signals with new model."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


MARCH_DATETIME = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)


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

    with patch("weather.multi_model.datetime", wraps=datetime, **{"now.return_value": MARCH_DATETIME}):
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

            with patch("config.VISUAL_CROSSING_API_KEY", "test-key"):
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

    with patch("weather.multi_model.datetime", wraps=datetime, **{"now.return_value": MARCH_DATETIME}):
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

            with patch("config.VISUAL_CROSSING_API_KEY", "test-key"):
                signal = score_signal(ERCOT, market)

    # Low solar -> LONG -> positive edge -> model_prob > 0.5
    assert signal.model_prob > 0.5
    assert signal.model_prob < 0.99
