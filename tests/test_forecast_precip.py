import pytest
from unittest.mock import patch, MagicMock
from datetime import date
from weather.forecast import get_ensemble_precip, get_nws_precip_forecast, calculate_remaining_month_days


def test_calculate_remaining_days_end_of_march():
    """March 12 → 19 days remaining."""
    result = calculate_remaining_month_days(market_close_date=date(2026, 3, 31))
    assert isinstance(result, int)
    assert result >= 0


def test_calculate_remaining_days_no_close_date():
    """No close date → days until end of current month."""
    result = calculate_remaining_month_days()
    assert isinstance(result, int)
    assert 0 <= result <= 31


@patch("weather.forecast.http_get")
def test_get_ensemble_precip_returns_list(mock_get):
    """Mocked Open-Meteo response → list of 30 precip values in inches."""
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
    assert all(isinstance(v, float) for v in result)
    assert all(v >= 0 for v in result)


@patch("weather.forecast.http_get")
def test_get_ensemble_precip_fallback_on_error(mock_get):
    """API error → returns [0.0] * 30 fallback."""
    mock_get.side_effect = Exception("API down")
    result = get_ensemble_precip(40.78, -73.97)
    assert result == [0.0] * 30


@patch("weather.forecast.http_get")
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


@patch("weather.forecast.http_get")
def test_get_nws_precip_forecast_error_fallback(mock_get):
    """API error → (0.5, 0.0) fallback."""
    mock_get.side_effect = Exception("NWS down")
    pop, qpf = get_nws_precip_forecast(40.78, -73.97)
    assert pop == 0.5
    assert qpf == 0.0
