"""Tests for ercot/hubs.py — hub scanning and ERCOT data caching."""
import time


def test_scan_all_hubs_returns_five_signals():
    from ercot.hubs import scan_all_hubs

    signals = scan_all_hubs()
    assert len(signals) == 5
    hub_names = {s["hub"] for s in signals}
    assert hub_names == {"North", "Houston", "South", "West", "Panhandle"}


def test_signal_dict_has_required_keys():
    from ercot.hubs import scan_all_hubs

    signals = scan_all_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_ercot_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_ercot_market_data_caching():
    """Second call within 5 min should return cached data."""
    from ercot.hubs import _fetch_ercot_market_data

    data1 = _fetch_ercot_market_data()
    data2 = _fetch_ercot_market_data()
    assert "price" in data1
    assert "solar_mw" in data1
    assert data1 == data2


def test_fetch_ercot_market_data_has_fallbacks():
    """Data should always have price, solar_mw, load_forecast keys."""
    from ercot.hubs import _fetch_ercot_market_data

    data = _fetch_ercot_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)
