import pytest
from unittest.mock import patch


@patch("weather.forecast.get_observed_mtd_precip", return_value=0.5)
@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_returns_tuple(mock_nws, mock_ensemble, mock_mtd):
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
    assert details.get("effective_threshold") == 1.5  # 2.0 - 0.5 MTD


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
    """Default model should be CSGD (not empirical). No forecast_days = no MTD fetch."""
    mock_ensemble.return_value = [0.0] * 10 + [1.0 + i * 0.2 for i in range(20)]
    mock_nws.return_value = (0.8, 2.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=2.0,
    )
    assert details.get("ensemble", {}).get("method") == "csgd"


@patch("weather.forecast.get_observed_mtd_precip", return_value=3.5)
@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_mtd_exceeds_threshold(mock_nws, mock_ensemble, mock_mtd):
    """When MTD observed precip already exceeds threshold, probability is ~1.0."""
    mock_ensemble.return_value = [0.0] * 30
    mock_nws.return_value = (0.1, 0.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=2.0, forecast_days=16,
    )
    assert prob == 0.99
    assert details.get("short_circuit") == "mtd_exceeds_threshold"


@patch("weather.forecast.get_observed_mtd_precip", return_value=1.0)
@patch("weather.multi_model.get_ensemble_precip")
@patch("weather.multi_model.get_nws_precip_forecast")
def test_fuse_precip_mtd_adjusts_threshold(mock_nws, mock_ensemble, mock_mtd):
    """MTD observed precip reduces the effective threshold for the forecast."""
    mock_ensemble.return_value = [0.0] * 10 + [1.5 + i * 0.1 for i in range(20)]
    mock_nws.return_value = (0.7, 1.0)

    from weather.multi_model import fuse_precip_forecast
    prob, confidence, details = fuse_precip_forecast(
        lat=40.78, lon=-73.97, city="nyc", month=3,
        threshold=3.0, forecast_days=16,
    )
    # Effective threshold = 3.0 - 1.0 MTD = 2.0
    assert details.get("effective_threshold") == 2.0
    assert details.get("mtd_observed_inches") == 1.0
    assert 0.0 <= prob <= 1.0
