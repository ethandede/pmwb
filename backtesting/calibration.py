"""Calibration analysis: calibration curves and Platt scaling."""

import numpy as np
from scipy.optimize import minimize


def calibration_curve(
    predicted_probs, outcomes, n_bins: int = 10
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute calibration curve data: bin means vs observed frequency.

    Returns:
        bin_means: Mean predicted probability in each bin
        bin_rates: Observed rate (fraction of positive outcomes) in each bin
        bin_counts: Number of samples in each bin
    """
    probs = np.array(predicted_probs, dtype=float)
    actual = np.array(outcomes, dtype=float)

    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_means = np.zeros(n_bins)
    bin_rates = np.zeros(n_bins)
    bin_counts = np.zeros(n_bins, dtype=int)

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == n_bins - 1:
            mask = (probs >= lo) & (probs <= hi)
        else:
            mask = (probs >= lo) & (probs < hi)

        count = mask.sum()
        bin_counts[i] = count
        if count > 0:
            bin_means[i] = probs[mask].mean()
            bin_rates[i] = actual[mask].mean()
        else:
            bin_means[i] = (lo + hi) / 2
            bin_rates[i] = np.nan

    return bin_means, bin_rates, bin_counts


def platt_scale(
    train_probs, train_outcomes, target_probs
) -> np.ndarray:
    """Apply Platt scaling to recalibrate probabilities.

    Fits a logistic regression on log-odds of train_probs vs train_outcomes,
    then applies the transformation to target_probs.

    Args:
        train_probs: Predicted probabilities used for fitting (training set)
        train_outcomes: Binary outcomes for training set
        target_probs: Probabilities to recalibrate

    Returns:
        Recalibrated probabilities for target_probs
    """
    train_p = np.clip(np.array(train_probs, dtype=float), 1e-6, 1 - 1e-6)
    train_y = np.array(train_outcomes, dtype=float)
    target_p = np.clip(np.array(target_probs, dtype=float), 1e-6, 1 - 1e-6)

    # Convert to log-odds
    train_logits = np.log(train_p / (1 - train_p))

    def _stable_sigmoid(z):
        """Numerically stable sigmoid."""
        z = np.clip(z, -500, 500)
        return np.where(z >= 0, 1 / (1 + np.exp(-z)), np.exp(z) / (1 + np.exp(z)))

    # Fit logistic regression: y = sigmoid(a * logit + b)
    def neg_log_likelihood(params):
        a, b = params
        z = a * train_logits + b
        p = np.clip(_stable_sigmoid(z), 1e-10, 1 - 1e-10)
        return -np.mean(train_y * np.log(p) + (1 - train_y) * np.log(1 - p))

    result = minimize(neg_log_likelihood, [1.0, 0.0], method="Nelder-Mead")
    a, b = result.x

    target_logits = np.log(target_p / (1 - target_p))
    z = a * target_logits + b
    return _stable_sigmoid(z)
