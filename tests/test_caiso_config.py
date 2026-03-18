"""Tests for CAISO hub config and seasonal norms in config.py."""


def test_caiso_hubs_exist():
    from config import CAISO_HUBS
    assert len(CAISO_HUBS) == 3


def test_caiso_hubs_have_required_fields():
    from config import CAISO_HUBS
    required = {"city", "lat", "lon", "hub_name", "solar_sensitivity", "active"}
    for hub_key, info in CAISO_HUBS.items():
        assert required.issubset(info.keys()), f"{hub_key} missing: {required - info.keys()}"


def test_caiso_active_hubs():
    from config import CAISO_HUBS
    active = {k for k, v in CAISO_HUBS.items() if v["active"]}
    assert active == {"SP15"}


def test_caiso_seasonal_norms_all_months():
    from config import CAISO_SEASONAL_NORMS
    assert set(CAISO_SEASONAL_NORMS.keys()) == set(range(1, 13))
    for month, norms in CAISO_SEASONAL_NORMS.items():
        assert "solar" in norms and "load" in norms
        assert norms["solar"] > 0
        assert norms["load"] > 0


def test_caiso_trading_params():
    from config import (
        CAISO_PAPER_BANKROLL, CAISO_PAPER_MODE, CAISO_LOAD_SENSITIVITY,
        CAISO_MAX_POSITIONS_PER_HUB, CAISO_MAX_POSITIONS_TOTAL,
    )
    assert CAISO_PAPER_BANKROLL == 10_000.0
    assert CAISO_PAPER_MODE is True
    assert CAISO_LOAD_SENSITIVITY == 0.15
    assert CAISO_MAX_POSITIONS_PER_HUB == 3
    assert CAISO_MAX_POSITIONS_TOTAL == 6
