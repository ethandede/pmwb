import pytest
import pandas as pd
import numpy as np
from backtesting.scorer import brier_score, log_loss_score, hit_rate_by_confidence, pnl_by_city


def test_brier_score_perfect():
    """Perfect predictions should give Brier score of 0."""
    probs = [1.0, 0.0, 1.0]
    outcomes = [1, 0, 1]
    assert brier_score(probs, outcomes) == pytest.approx(0.0)


def test_brier_score_worst():
    """Completely wrong predictions should give Brier score of 1."""
    probs = [1.0, 0.0]
    outcomes = [0, 1]
    assert brier_score(probs, outcomes) == pytest.approx(1.0)


def test_brier_score_coin_flip():
    """50/50 predictions should give Brier score of 0.25."""
    probs = [0.5, 0.5, 0.5, 0.5]
    outcomes = [1, 0, 1, 0]
    assert brier_score(probs, outcomes) == pytest.approx(0.25)


def test_log_loss_perfect():
    """Near-perfect predictions should give very low log loss."""
    probs = [0.99, 0.01]
    outcomes = [1, 0]
    result = log_loss_score(probs, outcomes)
    assert result < 0.05


def test_log_loss_bad():
    """Confident wrong predictions should give high log loss."""
    probs = [0.99, 0.99]
    outcomes = [0, 0]
    result = log_loss_score(probs, outcomes)
    assert result > 2.0


def test_hit_rate_by_confidence():
    df = pd.DataFrame({
        "confidence": [90, 90, 90, 50, 50],
        "direction": ["BUY YES", "BUY YES", "BUY YES", "SELL YES", "SELL YES"],
        "edge": [0.15, 0.12, 0.10, -0.08, -0.09],
        "settlement_outcome": ["yes", "yes", "no", "no", "yes"],
    })
    result = hit_rate_by_confidence(df, bins=[0, 70, 100])
    assert len(result) == 2  # two bins
    assert "hit_rate" in result.columns


def test_pnl_by_city():
    df = pd.DataFrame({
        "city": ["nyc (kalshi)", "nyc (kalshi)", "chicago (kalshi)"],
        "pnl": [0.50, -0.30, 0.80],
    })
    result = pnl_by_city(df)
    assert result.loc["nyc (kalshi)", "total_pnl"] == pytest.approx(0.20)
    assert result.loc["chicago (kalshi)", "total_pnl"] == pytest.approx(0.80)
