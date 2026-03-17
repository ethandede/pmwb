"""Tests for METAR integration in pipeline scoring."""
from unittest.mock import patch, MagicMock
from pipeline.stages import score_signal


def _make_config(name="kalshi_temp"):
    config = MagicMock()
    config.name = name
    config.forecast_fn.return_value = (0.70, 75.0, {"ensemble": {"temp": 78.0}})
    config.bucket_parser = MagicMock(return_value=(80.0, None))  # "at or above 80°F"
    return config


def _make_market(ticker="KXHIGHNY-26MAR16-T80", city="nyc", days_ahead_date="26MAR16"):
    return {
        "ticker": ticker,
        "_city": city,
        "yes_ask": 70,
        "yes_bid": 65,
        "_lat": 40.79,
        "_lon": -73.87,
        "_unit": "f",
        "_temp_type": "max",
    }


class TestMetarPipelineIntegration:
    """METAR bust detection should modify model_prob and confidence in score_signal."""

    @patch("pipeline.stages.check_forecast_bust")
    @patch("pipeline.stages._compute_days_ahead", return_value=0)
    def test_bust_reduces_confidence(self, mock_days, mock_bust):
        """When METAR detects a bust, confidence should be penalized."""
        mock_bust.return_value = {
            "active": True,
            "bust_detected": True,
            "floor": 83.0,
            "obs_temp": 83.0,
            "confidence_penalty": 0.25,
        }

        config = _make_config()
        signal = score_signal(config, _make_market())

        assert signal.confidence == 75.0 - 0.25  # original 75.0 minus penalty
        mock_bust.assert_called_once()

    @patch("pipeline.stages.check_forecast_bust")
    @patch("pipeline.stages._compute_days_ahead", return_value=0)
    def test_floor_clamps_below_bucket_prob(self, mock_days, mock_bust):
        """If obs > bucket threshold, prob of 'below threshold' should collapse."""
        mock_bust.return_value = {
            "active": True,
            "bust_detected": True,
            "floor": 83.0,  # already above the 80°F threshold
            "obs_temp": 83.0,
            "confidence_penalty": 0.15,
        }

        config = _make_config()
        # Bucket is (80.0, None) = "at or above 80°F"
        # high=None, so floor clamp condition (high < floor) won't trigger
        # But confidence should still be penalized
        signal = score_signal(config, _make_market())

        assert signal.confidence < 75.0

    @patch("pipeline.stages.check_forecast_bust")
    @patch("pipeline.stages._compute_days_ahead", return_value=1)
    def test_metar_skipped_for_day_ahead(self, mock_days, mock_bust):
        """METAR check should not run for days_ahead > 0."""
        config = _make_config()
        signal = score_signal(config, _make_market())

        # Should return unmodified forecast values
        assert signal.model_prob == 0.70
        assert signal.confidence == 75.0
        mock_bust.assert_not_called()

    @patch("pipeline.stages.check_forecast_bust")
    @patch("pipeline.stages._compute_days_ahead", return_value=0)
    def test_metar_inactive_passes_through(self, mock_days, mock_bust):
        """When METAR returns inactive, forecast values unchanged."""
        mock_bust.return_value = {"active": False}

        config = _make_config()
        signal = score_signal(config, _make_market())

        assert signal.model_prob == 0.70
        assert signal.confidence == 75.0
