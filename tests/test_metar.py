"""Tests for METAR observation fetcher and forecast bust detection."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest
from weather import cache as fcache


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


METAR_RESPONSE = [
    {
        "icaoId": "KLGA",
        "obsTime": 1773708660,
        "temp": 28.3,  # Celsius
        "name": "New York/La Guardia Arpt, NY, US",
    }
]


class TestGetMetarObs:
    """get_metar_obs() fetches and converts METAR observations."""

    def setup_method(self):
        """Clear cache before each test to avoid cross-test contamination."""
        fcache.clear()

    @patch("weather.metar._http_get")
    def test_returns_temp_in_fahrenheit(self, mock_get):
        """API returns Celsius; function must convert to Fahrenheit."""
        mock_get.return_value = _mock_response(METAR_RESPONSE)

        from weather.metar import get_metar_obs
        result = get_metar_obs("KLGA")

        assert result is not None
        assert result["temp_f"] == pytest.approx(82.9, abs=0.1)
        assert result["station"] == "KLGA"

    @patch("weather.metar._http_get")
    def test_returns_none_on_empty_response(self, mock_get):
        """No observations → None (fail-open)."""
        mock_get.return_value = _mock_response([])

        from weather.metar import get_metar_obs
        assert get_metar_obs("KLGA") is None

    @patch("weather.metar._http_get")
    def test_returns_none_on_api_error(self, mock_get):
        """API failure → None (fail-open)."""
        mock_get.side_effect = Exception("timeout")

        from weather.metar import get_metar_obs
        assert get_metar_obs("KLGA") is None

    @patch("weather.metar._http_get")
    def test_uses_correct_api_endpoint(self, mock_get):
        """Must use the new Aviation Weather API, not the retired CGI endpoint."""
        mock_get.return_value = _mock_response(METAR_RESPONSE)

        from weather.metar import get_metar_obs
        get_metar_obs("KORD")

        call_url = mock_get.call_args[0][0]
        assert "aviationweather.gov/api/data/metar" in call_url
        assert "cgi-bin" not in call_url
        assert "KORD" in call_url


class TestCheckForecastBust:
    """check_forecast_bust() detects when reality diverges from forecast."""

    @patch("weather.metar._local_hour", return_value=14)  # 2pm local
    @patch("weather.metar.get_metar_obs")
    def test_bust_detected_when_obs_exceeds_forecast(self, mock_obs, mock_hour):
        """Current temp > forecast high → bust with floor and penalty."""
        mock_obs.return_value = {"temp_f": 83.0, "observed_at": datetime.now(), "station": "KLGA"}

        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=0, temp_type="max")

        assert result["active"] is True
        assert result["bust_detected"] is True
        assert result["floor"] == 83.0
        assert result["confidence_penalty"] == pytest.approx(0.25, abs=0.01)  # (83-78)/20 = 0.25

    @patch("weather.metar._local_hour", return_value=14)
    @patch("weather.metar.get_metar_obs")
    def test_no_bust_when_obs_below_forecast(self, mock_obs, mock_hour):
        """Current temp < forecast → no bust, no penalty."""
        mock_obs.return_value = {"temp_f": 72.0, "observed_at": datetime.now(), "station": "KLGA"}

        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=0, temp_type="max")

        assert result["active"] is True
        assert result["bust_detected"] is False
        assert result["floor"] is None
        assert result["confidence_penalty"] == 0.0

    @patch("weather.metar._local_hour", return_value=7)  # 7am — too early
    def test_inactive_before_10am_local(self, mock_hour):
        """Before 10am local, METAR check should not activate."""
        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=0, temp_type="max")

        assert result["active"] is False

    def test_inactive_for_day_ahead(self):
        """METAR only for same-day (days_ahead=0)."""
        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=1, temp_type="max")

        assert result["active"] is False

    def test_inactive_for_min_temp(self):
        """METAR bust check only for temp_type='max'."""
        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=0, temp_type="min")

        assert result["active"] is False

    @patch("weather.metar._local_hour", return_value=14)
    @patch("weather.metar.get_metar_obs")
    def test_penalty_capped_at_0_3(self, mock_obs, mock_hour):
        """Confidence penalty should not exceed 0.3 even on massive bust."""
        mock_obs.return_value = {"temp_f": 100.0, "observed_at": datetime.now(), "station": "KLGA"}

        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=0, temp_type="max")

        assert result["confidence_penalty"] == 0.3

    @patch("weather.metar._local_hour", return_value=14)
    @patch("weather.metar.get_metar_obs")
    def test_returns_inactive_when_metar_fails(self, mock_obs, mock_hour):
        """If METAR fetch fails, returns inactive (fail-open)."""
        mock_obs.return_value = None

        from weather.metar import check_forecast_bust
        result = check_forecast_bust("nyc", forecast_high=78.0, days_ahead=0, temp_type="max")

        assert result["active"] is False

    @patch("weather.metar._local_hour", return_value=14)
    @patch("weather.metar.get_metar_obs")
    def test_unknown_city_returns_inactive(self, mock_obs, mock_hour):
        """City not in METAR_STATIONS → inactive."""
        from weather.metar import check_forecast_bust
        result = check_forecast_bust("tokyo", forecast_high=78.0, days_ahead=0, temp_type="max")

        assert result["active"] is False
        mock_obs.assert_not_called()
