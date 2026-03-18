"""End-to-end integration test for ERCOT binary options pipeline."""
import os
import pytest
from unittest.mock import patch

TEST_DB = "data/test_ercot_integration.db"


@pytest.fixture(autouse=True)
def clean_db():
    import ercot.paper_trader as pt
    pt.ERCOT_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_binary_signal(hub="North", hub_name="HB_NORTH", side="yes",
                        contract_date="2026-03-18", contract_hour=14,
                        dam_price=35.0, model_prob=0.65, edge=0.15, confidence=70):
    return {
        "hub": hub, "hub_name": hub_name,
        "contract_date": contract_date, "contract_hour": contract_hour,
        "side": side, "dam_price": dam_price,
        "entry_price": 0.50,
        "model_prob": model_prob, "edge": edge, "confidence": confidence,
    }


def test_full_pipeline():
    """Open a binary position and verify it appears in the summary."""
    from ercot.paper_trader import open_position, get_open_positions, get_paper_summary

    sig = _make_binary_signal()
    pos = open_position(sig, bankroll=10000.0)
    assert pos is not None
    assert pos["hub"] == sig["hub"]
    assert pos["contract_hour"] == sig["contract_hour"]
    assert pos["side"] == sig["side"]

    summary = get_paper_summary()
    assert summary["open_count"] >= 1


def test_settlement_closes_expired_position():
    """settle_expired_hours should close positions past their expiry."""
    from ercot.paper_trader import open_position, settle_expired_hours, get_open_positions

    # Use a past date/hour so it is expired
    sig = _make_binary_signal(contract_date="2026-03-16", contract_hour=1)
    pos = open_position(sig, bankroll=10000.0)
    assert pos is not None

    def mock_rt(hub, hour, date_str):
        return 40.0  # RT above DAM (35.0) → settles YES=100

    settled = settle_expired_hours(fetch_rt_fn=mock_rt)
    assert len(settled) == 1
    assert settled[0]["settlement_value"] == 100

    remaining = get_open_positions()
    assert len(remaining) == 0


def test_scan_cache_roundtrip():
    """Write scan results and read them back."""
    from ercot.paper_trader import write_scan_cache, get_cached_signals

    signals = [
        {"hub": "North", "hub_name": "HB_NORTH", "contract_date": "2026-03-18",
         "contract_hour": 14, "side": "yes", "dam_price": 35.0,
         "model_prob": 0.65, "edge": 0.15, "expected_solrad_mjm2": 20.0, "confidence": 70},
        {"hub": "Houston", "hub_name": "HB_HOUSTON", "contract_date": "2026-03-18",
         "contract_hour": 14, "side": "no", "dam_price": 40.0,
         "model_prob": 0.38, "edge": 0.12, "expected_solrad_mjm2": 22.0, "confidence": 65},
    ]
    write_scan_cache(signals)

    cached = get_cached_signals()
    assert len(cached) == 2
    assert {s["hub"] for s in cached} == {"North", "Houston"}
