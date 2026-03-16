"""Tests for ERCOT seasonal norms and per-hub solar sensitivity in config."""


def test_seasonal_norms_has_all_12_months():
    from config import ERCOT_SEASONAL_NORMS
    for month in range(1, 13):
        assert month in ERCOT_SEASONAL_NORMS, f"Missing month {month}"
        assert "solar" in ERCOT_SEASONAL_NORMS[month]
        assert "load" in ERCOT_SEASONAL_NORMS[month]


def test_seasonal_norms_values_are_reasonable():
    from config import ERCOT_SEASONAL_NORMS
    for month, norms in ERCOT_SEASONAL_NORMS.items():
        assert 5.0 <= norms["solar"] <= 30.0, f"Month {month} solar {norms['solar']} out of range"
        assert 30_000 <= norms["load"] <= 80_000, f"Month {month} load {norms['load']} out of range"


def test_load_sensitivity_exists():
    from config import ERCOT_LOAD_SENSITIVITY
    assert isinstance(ERCOT_LOAD_SENSITIVITY, float)
    assert 0.0 < ERCOT_LOAD_SENSITIVITY < 1.0


def test_each_hub_has_solar_sensitivity():
    from config import ERCOT_HUBS
    for hub_key, info in ERCOT_HUBS.items():
        assert "solar_sensitivity" in info, f"Hub {hub_key} missing solar_sensitivity"
        assert 0.0 < info["solar_sensitivity"] <= 1.0


def test_hub_sensitivities_are_differentiated():
    """West should be most sensitive, Houston least."""
    from config import ERCOT_HUBS
    assert ERCOT_HUBS["West"]["solar_sensitivity"] > ERCOT_HUBS["Houston"]["solar_sensitivity"]
    assert ERCOT_HUBS["West"]["solar_sensitivity"] > ERCOT_HUBS["North"]["solar_sensitivity"]
