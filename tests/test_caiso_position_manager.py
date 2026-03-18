"""Tests for caiso/position_manager.py — evaluate/fortify/exit logic."""
import os
import pytest

TEST_DB = "data/test_caiso_paper.db"


@pytest.fixture(autouse=True)
def clean_db():
    import caiso.paper_trader as pt
    pt.CAISO_PAPER_DB = TEST_DB
    pt._init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_signal(signal="SHORT", edge=1.5, confidence=70, price=40.0):
    return {
        "hub": "SP15", "hub_name": "SP15", "city": "Los Angeles",
        "signal": signal, "edge": edge, "confidence": confidence,
        "current_caiso_price": price, "expected_solrad_mjm2": 19.0,
        "actual_solar_mw": 10000.0,
    }


def test_hold_when_signal_agrees():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.2)
    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "hold"


def test_exit_when_signal_flips():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=1.5), bankroll=10000.0)
    current = _make_signal(signal="LONG", edge=1.0)
    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "exit"
    assert "flipped" in result["reason"].lower()


def test_exit_when_edge_decays():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=2.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=0.4)
    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "exit"
    assert "decay" in result["reason"].lower()


def test_fortify_when_edge_increases():
    from caiso.position_manager import evaluate_caiso_position
    from caiso.paper_trader import open_position
    pos = open_position(_make_signal(signal="SHORT", edge=1.0), bankroll=10000.0)
    current = _make_signal(signal="SHORT", edge=1.6)
    result = evaluate_caiso_position(pos, current)
    assert result["action"] == "fortify"
