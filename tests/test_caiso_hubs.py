"""Tests for caiso/hubs.py — hub scanning and CAISO data caching."""
from unittest.mock import patch


def test_scan_all_caiso_hubs_returns_active_only():
    from caiso.hubs import scan_all_caiso_hubs
    signals = scan_all_caiso_hubs()
    assert len(signals) == 1
    assert signals[0]["hub"] == "SP15"


def test_caiso_signal_dict_has_required_keys():
    from caiso.hubs import scan_all_caiso_hubs
    signals = scan_all_caiso_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_caiso_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_caiso_market_data_caching():
    from caiso.hubs import _fetch_caiso_market_data
    data1 = _fetch_caiso_market_data()
    data2 = _fetch_caiso_market_data()
    assert "price" in data1
    assert data1 == data2


def test_fetch_caiso_market_data_has_fallbacks():
    from caiso.hubs import _fetch_caiso_market_data
    data = _fetch_caiso_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)


def test_fetch_caiso_markets_only_active():
    from caiso.hubs import fetch_caiso_markets
    markets = fetch_caiso_markets()
    assert len(markets) == 1
    assert markets[0]["hub"] == "SP15"


def test_fetch_caiso_markets_has_per_hub_price():
    from caiso.hubs import fetch_caiso_markets
    markets = fetch_caiso_markets()
    for m in markets:
        caiso_data = m.get("_caiso_data", {})
        assert "hub_price" in caiso_data
