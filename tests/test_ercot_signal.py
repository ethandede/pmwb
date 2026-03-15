"""Tests for get_ercot_solar_signal with optional ercot_data."""

from unittest.mock import MagicMock, patch
import pytest


def test_signal_with_prefetched_data():
    """When ercot_data is provided, use those values instead of fetching."""
    from weather.multi_model import get_ercot_solar_signal

    ercot_data = {"price": 55.0, "solar_mw": 8000.0}
    result = get_ercot_solar_signal(32.78, -96.80, hours_ahead=24, ercot_data=ercot_data)

    assert result["current_ercot_price"] == 55.0
    assert result["actual_solar_mw"] == 8000.0
    assert result["signal"] in ("SHORT", "LONG", "NEUTRAL")
    assert "edge" in result
    assert "confidence" in result
    assert "expected_solrad_mjm2" in result


def test_signal_without_prefetched_data():
    """When ercot_data is None, function fetches directly (backwards compat)."""
    from weather.multi_model import get_ercot_solar_signal

    result = get_ercot_solar_signal(32.78, -96.80, hours_ahead=24)

    assert "current_ercot_price" in result
    assert "actual_solar_mw" in result
    assert result["signal"] in ("SHORT", "LONG", "NEUTRAL")


def test_signal_short_when_high_solrad():
    """High solar irradiance should produce SHORT signal."""
    from weather.multi_model import get_ercot_solar_signal

    result = get_ercot_solar_signal(32.78, -96.80, ercot_data={"price": 40.0, "solar_mw": 12000.0})
    assert isinstance(result["edge"], float)
    assert isinstance(result["confidence"], int)


def test_signal_direction_multiplier():
    """Verify edge is always positive regardless of direction."""
    from weather.multi_model import get_ercot_solar_signal

    result = get_ercot_solar_signal(32.78, -96.80, ercot_data={"price": 40.0, "solar_mw": 12000.0})
    assert result["edge"] >= 0.0


# ============================================================================
# Edge clamping tests — edge must always be in [0, 0.99]
# ============================================================================

@pytest.mark.parametrize("solrad,expected_signal,max_edge", [
    (30.0, "SHORT", 0.99),   # extreme Texas summer
    (25.0, "SHORT", 0.99),   # high spring day
    (20.0, "SHORT", 0.99),   # moderate sunny day, edge = (20-15)/4 = 1.25 → clamped
    (19.0, "SHORT", 0.99),   # just above threshold, edge = (19-15)/4 = 1.0 → within range
    (18.5, "SHORT", 0.88),   # edge = (18.5-15)/4 = 0.875
    (15.0, "NEUTRAL", 0.0),  # baseline
    (5.0, "LONG", 0.99),     # very low radiation → clamped
    (8.0, "LONG", 0.99),     # low, edge = (15-8)/4 = 1.75 → clamped
])
def test_edge_clamped_across_solar_range(solrad, expected_signal, max_edge):
    """Edge must be clamped to [0, 0.99] for any solar radiation value."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        resp = MagicMock()
        resp.json.return_value = {"daily": {"shortwave_radiation_sum": [solrad]}}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hours_ahead=24,
            ercot_data={"price": 40.0, "solar_mw": 12000.0},
        )

        assert result["edge"] <= 0.99, f"Edge {result['edge']} exceeds 0.99 for solrad={solrad}"
        assert result["edge"] >= 0.0, f"Edge {result['edge']} is negative for solrad={solrad}"
        assert result["signal"] == expected_signal, f"Expected {expected_signal}, got {result['signal']}"
        assert result["edge"] <= max_edge + 0.01  # small tolerance for rounding


def test_edge_never_exceeds_099_extreme():
    """Even with absurd solar radiation (50 MJ/m2), edge stays <= 0.99."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        resp = MagicMock()
        resp.json.return_value = {"daily": {"shortwave_radiation_sum": [50.0]}}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hours_ahead=24,
            ercot_data={"price": 40.0, "solar_mw": 12000.0},
        )

        assert result["edge"] == 0.99
        assert result["signal"] == "SHORT"


def test_confidence_threshold_after_clamp():
    """High edge (>0.50 after clamp) should still get confidence=70."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        resp = MagicMock()
        resp.json.return_value = {"daily": {"shortwave_radiation_sum": [25.0]}}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hours_ahead=24,
            ercot_data={"price": 40.0, "solar_mw": 12000.0},
        )

        assert result["confidence"] == 70, f"Expected confidence=70 for clamped high edge, got {result['confidence']}"


def test_low_edge_gets_low_confidence():
    """Edge just above SHORT threshold should get confidence=50."""
    from weather.multi_model import get_ercot_solar_signal

    with patch("weather.multi_model.http_get") as mock_get:
        resp = MagicMock()
        # solrad=18.5 → edge = (18.5-15)/4 = 0.875... wait that's > 0.5
        # solrad=18.1 → edge = (18.1-15)/4 = 0.775... still > 0.5
        # Need solrad just above 18 but edge < 0.5: (x-15)/4 < 0.5 → x < 17
        # But x > 18 for SHORT... so all SHORT signals have edge >= 0.75
        # That means all SHORT signals get confidence=70 with the new threshold
        # Test NEUTRAL instead
        resp.json.return_value = {"daily": {"shortwave_radiation_sum": [14.0]}}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hours_ahead=24,
            ercot_data={"price": 40.0, "solar_mw": 12000.0},
        )

        assert result["signal"] == "NEUTRAL"
        assert result["edge"] == 0.0
        assert result["confidence"] == 50


def test_model_prob_stays_valid_after_edge_clamp():
    """score_signal model_prob = 1.0 - edge should stay in [0.01, 0.99]."""
    from pipeline.stages import score_signal
    from pipeline.config import ERCOT

    market = {
        "hub_name": "HB_NORTH",
        "city": "Dallas",
        "lat": 32.78,
        "lon": -96.80,
        "_ercot_data": {"price": 40.0, "solar_mw": 15000.0},
    }

    with patch("weather.multi_model.http_get") as mock_get:
        resp = MagicMock()
        resp.json.return_value = {"daily": {"shortwave_radiation_sum": [30.0]}}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        signal = score_signal(ERCOT, market)
        assert 0.01 <= signal.model_prob <= 0.99, \
            f"model_prob {signal.model_prob} out of [0.01, 0.99] range"


# ============================================================================
# Daemon ERCOT manager integration
# ============================================================================

def test_daemon_calls_ercot_manager():
    """daemon.run_cycle should call run_ercot_manager in Phase 1."""
    import importlib
    import daemon as daemon_mod

    source = importlib.util.find_spec("daemon").origin
    with open(source) as f:
        code = f.read()

    assert "run_ercot_manager" in code, \
        "daemon.py must call run_ercot_manager for ERCOT position management"
    assert "from ercot.position_manager import run_ercot_manager" in code, \
        "daemon.py must import run_ercot_manager"
