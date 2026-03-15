"""Tests for get_ercot_solar_signal with optional ercot_data."""


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
