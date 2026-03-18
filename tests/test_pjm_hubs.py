"""Tests for pjm/hubs.py — hub scanning and PJM data caching."""
from unittest.mock import patch


def test_scan_all_pjm_hubs_returns_active_only():
    from pjm.hubs import scan_all_pjm_hubs
    signals = scan_all_pjm_hubs()
    assert len(signals) == 2
    hub_names = {s["hub"] for s in signals}
    assert hub_names == {"Western", "AEP-Dayton"}


def test_pjm_signal_dict_has_required_keys():
    from pjm.hubs import scan_all_pjm_hubs
    signals = scan_all_pjm_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_pjm_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_pjm_market_data_caching():
    from pjm.hubs import _fetch_pjm_market_data
    data1 = _fetch_pjm_market_data()
    data2 = _fetch_pjm_market_data()
    assert "price" in data1
    assert data1 == data2


def test_fetch_pjm_market_data_has_fallbacks():
    from pjm.hubs import _fetch_pjm_market_data
    data = _fetch_pjm_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)


def test_fetch_pjm_markets_includes_per_hub_fields():
    from pjm.hubs import fetch_pjm_markets
    markets = fetch_pjm_markets()
    for m in markets:
        assert "hub_key" in m
        assert "solar_sensitivity" in m
        assert isinstance(m["solar_sensitivity"], float)


def test_fetch_pjm_markets_only_active():
    from pjm.hubs import fetch_pjm_markets
    markets = fetch_pjm_markets()
    assert len(markets) == 2
    hubs = {m["hub"] for m in markets}
    assert hubs == {"Western", "AEP-Dayton"}


def test_fetch_pjm_markets_has_per_hub_price():
    from pjm.hubs import fetch_pjm_markets
    markets = fetch_pjm_markets()
    for m in markets:
        pjm_data = m.get("_pjm_data", {})
        assert "hub_price" in pjm_data


def test_per_hub_prices_are_differentiated():
    from pjm.hubs import fetch_pjm_markets

    fake_data = {
        "price": 35.0, "solar_mw": 5000.0, "load_forecast": 80000.0,
        "hub_prices": {"WESTERN": 30.0, "AEP": 45.0},
    }

    with patch("pjm.hubs._fetch_pjm_market_data", return_value=fake_data):
        markets = fetch_pjm_markets()

    prices = {m["hub_name"]: m["current_pjm_price"] for m in markets}
    assert prices["WESTERN"] == 30.0
    assert prices["AEP"] == 45.0
