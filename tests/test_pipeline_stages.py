"""Tests for pipeline stage functions. All use mocks — no API/forecast calls."""
from unittest.mock import MagicMock
from pipeline.types import Signal, CycleState
from pipeline.stages import fetch_markets, score_signal


def test_fetch_markets_calls_config_fn():
    """fetch_markets delegates to config.fetch_fn."""
    config = MagicMock()
    config.fetch_fn.return_value = [{"ticker": "T1"}, {"ticker": "T2"}]
    exchange = MagicMock()

    markets = fetch_markets(config, exchange)

    config.fetch_fn.assert_called_once()
    assert len(markets) == 2


def test_score_signal_temp():
    """score_signal creates a Signal with correct fields for temp market."""
    config = MagicMock()
    config.name = "kalshi_temp"
    config.forecast_fn.return_value = (0.30, 72.0, {})
    config.fusion_weights = {"ensemble": 0.40, "noaa": 0.35, "hrrr": 0.25}
    config.bucket_parser = MagicMock(return_value=(56.0, None))

    market = {
        "ticker": "KXHIGHNY-26MAR15-T56",
        "_city": "nyc",
        "yes_ask": 55,
        "yes_bid": 50,
        "_lat": 40.7, "_lon": -74.0,
    }

    signal = score_signal(config, market)

    assert isinstance(signal, Signal)
    assert signal.ticker == "KXHIGHNY-26MAR15-T56"
    assert signal.market_type == "kalshi_temp"
    assert signal.model_prob == 0.30
    assert signal.city == "nyc"


def test_score_signal_ercot():
    """score_signal handles ERCOT markets (no bucket parser, different forecast shape)."""
    config = MagicMock()
    config.name = "ercot"
    config.bucket_parser = None
    config.forecast_fn.return_value = {
        "signal": "SHORT", "edge": 1.69, "confidence": 70,
        "expected_solrad_mjm2": 21.8,
    }
    config.fusion_weights = None

    market = {
        "hub": "North", "hub_name": "HB_NORTH", "city": "Dallas",
        "lat": 32.78, "lon": -96.80,
        "current_ercot_price": 40.0, "_ercot_data": {},
    }

    signal = score_signal(config, market)

    assert signal.market_type == "ercot"
    assert signal.city == "Dallas"
    assert signal.side == "no"  # SHORT maps to selling
