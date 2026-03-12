import pytest
import numpy as np
from backtesting.calibration import calibration_curve, platt_scale


def test_calibration_curve_well_calibrated():
    """A well-calibrated model should have bin means ~ bin observed rates."""
    np.random.seed(42)
    n = 1000
    probs = np.random.uniform(0, 1, n)
    outcomes = (np.random.uniform(0, 1, n) < probs).astype(int)

    bin_means, bin_rates, bin_counts = calibration_curve(probs, outcomes, n_bins=5)

    assert len(bin_means) == 5
    # Each bin should be within ~0.15 of perfect calibration (generous for 1000 samples)
    for mean, rate in zip(bin_means, bin_rates):
        if not np.isnan(rate):
            assert abs(mean - rate) < 0.15, f"Bin mean {mean:.2f} vs rate {rate:.2f}"


def test_calibration_curve_returns_counts():
    probs = [0.1, 0.2, 0.3, 0.8, 0.9]
    outcomes = [0, 0, 1, 1, 1]
    _, _, counts = calibration_curve(probs, outcomes, n_bins=2)
    assert sum(counts) == 5


def test_platt_scale():
    """Platt scaling should recalibrate overconfident predictions."""
    # Overconfident model: predicts extreme probs but outcomes are closer to 50/50
    np.random.seed(42)
    n = 200
    # Generate overconfident predictions (extreme values)
    probs = np.concatenate([np.full(n // 2, 0.1), np.full(n // 2, 0.9)])
    # But real outcomes are less extreme (30% and 70%)
    outcomes = np.concatenate([
        (np.random.random(n // 2) < 0.30).astype(int),
        (np.random.random(n // 2) < 0.70).astype(int),
    ])

    scaled = platt_scale(probs, outcomes, probs)
    # Scaled low predictions should be pushed toward 0.30 (up from 0.10)
    assert scaled[0] > probs[0] + 0.05
    # Scaled high predictions should be pushed toward 0.70 (down from 0.90)
    assert scaled[-1] < probs[-1] - 0.05
