"""Tests for HRRR forecast fetcher — localhost + public API fallback."""
from unittest.mock import patch, MagicMock
import pytest


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


VALID_HRRR_RESPONSE = {
    "daily": {"temperature_2m_max": [None, 75.2, 76.0]}
}


class TestGetHrrrForecast:
    """get_hrrr_forecast() should use gfs_hrrr model with fallback."""

    @patch("weather.multi_model.http_get")
    def test_uses_gfs_hrrr_model_on_localhost(self, mock_get):
        """Must request models=gfs_hrrr, NOT gfs_seamless."""
        mock_get.return_value = _mock_response(VALID_HRRR_RESPONSE)

        from weather.multi_model import get_hrrr_forecast
        result = get_hrrr_forecast(40.79, -73.87, days_ahead=1, unit="f", temp_type="max")

        assert result == 75.2
        call_url = mock_get.call_args[0][0]
        assert "gfs_hrrr" in call_url
        assert "gfs_seamless" not in call_url

    @patch("weather.multi_model.http_get")
    def test_falls_back_to_public_api(self, mock_get):
        """When localhost fails, should try public Open-Meteo API."""
        mock_get.side_effect = [
            Exception("Connection refused"),  # localhost fails
            _mock_response(VALID_HRRR_RESPONSE),  # public succeeds
        ]

        from weather.multi_model import get_hrrr_forecast
        result = get_hrrr_forecast(40.79, -73.87, days_ahead=1, unit="f", temp_type="max")

        assert result == 75.2
        assert mock_get.call_count == 2
        public_url = mock_get.call_args_list[1][0][0]
        assert "api.open-meteo.com" in public_url
        assert "gfs_hrrr" in public_url

    @patch("weather.multi_model.http_get")
    def test_returns_none_when_both_fail(self, mock_get):
        """When both localhost and public fail, returns None (fail-open)."""
        mock_get.side_effect = Exception("Connection refused")

        from weather.multi_model import get_hrrr_forecast
        result = get_hrrr_forecast(40.79, -73.87, days_ahead=1, unit="f", temp_type="max")

        assert result is None

    @patch("weather.multi_model.http_get")
    def test_handles_missing_days_ahead_index(self, mock_get):
        """HRRR only covers 0-48h; days_ahead=3 may have no data."""
        short_response = {"daily": {"temperature_2m_max": [75.0]}}
        mock_get.return_value = _mock_response(short_response)

        from weather.multi_model import get_hrrr_forecast
        result = get_hrrr_forecast(40.79, -73.87, days_ahead=3, unit="f", temp_type="max")

        assert result is None
