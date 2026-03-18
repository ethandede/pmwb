"""Tests for pjm/position_manager.py — evaluate/fortify/exit logic."""
import os
import pytest

TEST_DB = "data/test_pjm_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import pjm.paper_trader as pt
    pt.PJM_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(signal="SHORT", edge=1.5, confidence=70, price=35.0):
    return {
        "hub": "Western", "hub_name": "WESTERN", "city": "Pittsburgh",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_pjm_price": price, "expected_solrad_mjm2": 12.0,
        "actual_solar_mw": 5000.0,
    }


def test_hold_when_signal_agrees():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.2)
    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "hold"


def test_exit_when_signal_flips():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="LONG", edge=1.0)
    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "exit"
    assert "flipped" in result["reason"].lower()


def test_exit_when_edge_decays():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=2.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=0.4)
    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()


def test_fortify_when_edge_increases():
    from pjm.position_manager import evaluate_pjm_position
    from pjm.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=1.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.6)
    result = evaluate_pjm_position(pos, current)
    assert result["action"] == "fortify"
