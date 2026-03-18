"""Tests for PJM hub config and seasonal norms in config.py."""


def test_pjm_hubs_exist():
    from config import PJM_HUBS
    assert len(PJM_HUBS) == 5


def test_pjm_hubs_have_required_fields():
    from config import PJM_HUBS
    required = {"city", "lat", "lon", "hub_name", "solar_sensitivity", "active"}
    for hub_key, info in PJM_HUBS.items():
        assert required.issubset(info.keys()), f"{hub_key} missing: {required - info.keys()}"


def test_pjm_active_hubs():
    from config import PJM_HUBS
    active = {k for k, v in PJM_HUBS.items() if v["active"]}
    assert active == {"Western", "AEP-Dayton"}


def test_pjm_seasonal_norms_all_months():
    from config import PJM_SEASONAL_NORMS
    assert set(PJM_SEASONAL_NORMS.keys()) == set(range(1, 13))
    for month, norms in PJM_SEASONAL_NORMS.items():
        assert "solar" in norms and "load" in norms
        assert norms["solar"] > 0
        assert norms["load"] > 0


def test_pjm_trading_params():
    from config import (
        PJM_PAPER_BANKROLL, PJM_PAPER_MODE, PJM_LOAD_SENSITIVITY,
        PJM_MAX_POSITIONS_PER_HUB, PJM_MAX_POSITIONS_TOTAL,
    )
    assert PJM_PAPER_BANKROLL == 10_000.0
    assert PJM_PAPER_MODE is True
    assert PJM_LOAD_SENSITIVITY == 0.20
    assert PJM_MAX_POSITIONS_PER_HUB == 3
    assert PJM_MAX_POSITIONS_TOTAL == 8
