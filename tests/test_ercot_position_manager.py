"""Tests for ercot/position_manager.py — evaluate/fortify/exit logic."""
import os
import pytest

TEST_DB = "data/test_ercot_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import ercot.paper_trader as pt
    pt.ERCOT_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": "North", "hub_name": "HB_NORTH", "city": "Dallas",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_ercot_price": price, "expected_solrad_mjm2": 20.0,
        "actual_solar_mw": 12000.0,
    }


def test_hold_when_signal_agrees():
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.2)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "hold"


def test_exit_when_signal_flips():
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="LONG", edge=1.0)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "exit"
    assert "flipped" in result["reason"].lower()


def test_exit_when_edge_decays():
    """Edge at 30% of entry = exit."""
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=2.0), bankroll=10000.0)
    # 30% of 2.0 = 0.6, so edge of 0.4 triggers exit
    current = _make_signal(signal="SHORT", edge=0.4)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()


def test_fortify_when_edge_increases():
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.0), bankroll=10000.0)
    # Entry edge 1.0 + FORTIFY_EDGE_INCREASE (0.5) = need 1.5+
    current = _make_signal(signal="SHORT", edge=1.6)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "fortify"


def test_exit_when_neutral_zero_edge():
    """NEUTRAL with zero edge is below 30% of entry edge 1.5, so exit."""
    from ercot.position_manager import evaluate_ercot_position
    from ercot.paper_trader import open_position

    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="NEUTRAL", edge=0.0)

    result = evaluate_ercot_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()
