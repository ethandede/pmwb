"""End-to-end integration test for ERCOT solar signal pipeline."""
import os
import pytest

TEST_DB = "data/test_ercot_integration.db"


@pytest.fixture(autouse=True)
def clean_db():
    import ercot.paper_trader as pt
    pt.ERCOT_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_full_pipeline():
    """Scan hubs → open positions → evaluate → close."""
    from ercot.hubs import scan_all_hubs
    from ercot.paper_trader import open_position, get_open_positions, get_paper_summary
    from ercot.position_manager import evaluate_ercot_position

    # 1. Scan all hubs
    signals = scan_all_hubs()
    assert len(signals) == 5

    # 2. Open a position on the first tradeable signal
    tradeable = [s for s in signals if abs(s["edge"]) > 0]
    if not tradeable:
        pytest.skip("No tradeable signals right now")

    sig = tradeable[0]
    pos = open_position(sig, bankroll=10000.0)
    assert pos is not None
    assert pos["hub"] == sig["hub"]

    # 3. Evaluate the position
    result = evaluate_ercot_position(pos, sig)
    assert result["action"] in ("hold", "exit", "fortify")

    # 4. Verify summary
    summary = get_paper_summary()
    assert summary["open_count"] >= 1


def test_scan_cache_roundtrip():
    """Write scan results and read them back via dashboard API functions."""
    from ercot.hubs import scan_all_hubs
    from ercot.paper_trader import write_scan_cache, get_cached_signals

    signals = scan_all_hubs()
    write_scan_cache(signals)

    cached = get_cached_signals()
    assert len(cached) == 5
    assert {s["hub"] for s in cached} == {"North", "Houston", "South", "West", "Panhandle"}
