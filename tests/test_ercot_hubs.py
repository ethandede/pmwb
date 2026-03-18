"""Tests for ercot/hubs.py — hub scanning and ERCOT data caching."""
import time
from unittest.mock import patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


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


def test_fetch_ercot_markets_includes_per_hub_fields():
    """Each market dict must include hub_key and solar_sensitivity."""
    from ercot.hubs import fetch_ercot_markets
    markets = fetch_ercot_markets()
    for m in markets:
        assert "hub_key" in m, f"Missing hub_key in market for {m.get('hub_name')}"
        assert "solar_sensitivity" in m, f"Missing solar_sensitivity in market for {m.get('hub_name')}"
        assert isinstance(m["solar_sensitivity"], float)


def test_fetch_ercot_markets_has_per_hub_price_in_ercot_data():
    """The _ercot_data dict should have a hub_price field for each market."""
    from ercot.hubs import fetch_ercot_markets
    markets = fetch_ercot_markets()
    for m in markets:
        ercot_data = m.get("_ercot_data", {})
        assert "hub_price" in ercot_data, f"Missing hub_price in _ercot_data for {m.get('hub_name')}"


def test_scan_all_hubs_uses_new_signature():
    """scan_all_hubs should pass hub_key and solar_sensitivity to signal function."""
    from ercot.hubs import scan_all_hubs
    signals = scan_all_hubs()
    for sig in signals:
        assert "signal" in sig
        assert "edge" in sig
        assert sig["signal"] in ("SHORT", "LONG", "NEUTRAL")


def test_per_hub_prices_are_differentiated():
    """When ERCOT returns different hub prices, each market should get its own."""
    from unittest.mock import patch
    from ercot.hubs import fetch_ercot_markets

    fake_data = {
        "price": 40.0,
        "solar_mw": 12000.0,
        "load_forecast": 45000.0,
        "hub_prices": {
            "HB_NORTH": 35.0,
            "HB_HOUSTON": 50.0,
            "HB_SOUTH": 42.0,
            "HB_WEST": 20.0,
            "HB_PAN": 28.0,
        },
    }

    with patch("ercot.hubs._fetch_ercot_market_data", return_value=fake_data):
        markets = fetch_ercot_markets()

    prices = {m["hub_name"]: m["current_ercot_price"] for m in markets}
    assert prices["HB_WEST"] == 20.0
    assert prices["HB_HOUSTON"] == 50.0
    assert prices["HB_NORTH"] == 35.0
    west_market = [m for m in markets if m["hub_name"] == "HB_WEST"][0]
    assert west_market["_ercot_data"]["hub_price"] == 20.0


class TestFetchDamPrices:
    def test_returns_hourly_prices_all_hubs(self):
        from ercot.hubs import fetch_dam_prices
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [
            ["2026-03-18", 11, "HB_WEST", "Hub", 42.50],
            ["2026-03-18", 12, "HB_WEST", "Hub", 38.00],
            ["2026-03-18", 14, "HB_WEST", "Hub", 45.00],
            ["2026-03-18", 11, "HB_NORTH", "Hub", 40.00],
            ["2026-03-18", 12, "HB_NORTH", "Hub", 36.00],
        ]}
        with patch("ercot.hubs.requests.get", return_value=mock_response):
            result = fetch_dam_prices("2026-03-18")
        assert result is not None
        assert result["HB_WEST"][11] == 42.50
        assert result["HB_WEST"][14] == 45.00
        assert result["HB_NORTH"][11] == 40.00

    def test_returns_none_on_failure(self):
        from ercot.hubs import fetch_dam_prices, _dam_cache
        _dam_cache.clear()  # ensure no stale cache from prior test
        with patch("ercot.hubs.requests.get", side_effect=Exception("timeout")):
            result = fetch_dam_prices("2026-03-18")
        assert result is None

    def test_caches_per_date(self):
        from ercot.hubs import fetch_dam_prices, _dam_cache
        _dam_cache.clear()  # reset module cache
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [
            ["2026-03-18", 11, "HB_WEST", "Hub", 42.50],
        ]}
        with patch("ercot.hubs.requests.get", return_value=mock_response) as mock_get:
            fetch_dam_prices("2026-03-18")
            fetch_dam_prices("2026-03-18")
            assert mock_get.call_count == 1


class TestFetchRtSettlement:
    def test_averages_four_intervals(self):
        from ercot.hubs import fetch_rt_settlement
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [
            ["2026-03-18", 14, 1, "HB_WEST", "Hub", 40.0, "N"],
            ["2026-03-18", 14, 2, "HB_WEST", "Hub", 42.0, "N"],
            ["2026-03-18", 14, 3, "HB_WEST", "Hub", 38.0, "N"],
            ["2026-03-18", 14, 4, "HB_WEST", "Hub", 44.0, "N"],
        ]}
        with patch("ercot.hubs.requests.get", return_value=mock_response):
            result = fetch_rt_settlement("HB_WEST", 14, "2026-03-18")
        assert result == 41.0

    def test_returns_none_on_failure(self):
        from ercot.hubs import fetch_rt_settlement
        with patch("ercot.hubs.requests.get", side_effect=Exception("timeout")):
            result = fetch_rt_settlement("HB_WEST", 14, "2026-03-18")
        assert result is None


class TestFetchErcotMarketsHourly:
    def test_returns_per_hour_contracts(self):
        from ercot.hubs import fetch_ercot_markets
        dam_all = {
            "HB_WEST": {11: 42.0, 12: 38.0, 14: 45.0},
            "HB_NORTH": {11: 40.0, 12: 36.0},
            "HB_HOUSTON": {11: 39.0},
            "HB_SOUTH": {11: 41.0},
            "HB_PAN": {11: 37.0},
        }
        mock_now = datetime(2026, 3, 18, 5, 0, tzinfo=CT)
        with patch("ercot.hubs.fetch_dam_prices", return_value=dam_all), \
             patch("ercot.hubs._fetch_ercot_market_data", return_value={
                 "price": 40.0, "solar_mw": 12000, "load_forecast": 45000,
                 "hub_prices": {"HB_WEST": 40.0, "HB_NORTH": 38.0,
                                "HB_HOUSTON": 39.0, "HB_SOUTH": 41.0, "HB_PAN": 37.0},
             }), \
             patch("ercot.hubs._get_ct_now", return_value=mock_now):
            markets = fetch_ercot_markets()
        assert len(markets) == 8
        west_markets = [m for m in markets if m["hub_name"] == "HB_WEST"]
        assert len(west_markets) == 3
        assert west_markets[0]["ticker"].startswith("BOPT-ERCOT-HB_WEST")
        assert "dam_price" in west_markets[0]
        assert "contract_hour" in west_markets[0]
