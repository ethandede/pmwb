"""Scoring metrics for forecast calibration and P&L analysis."""

import numpy as np
import pandas as pd
from typing import Sequence


def brier_score(predicted_probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Brier score: mean squared error between predicted probability and binary outcome.

    Lower is better. 0 = perfect, 1 = worst possible.
    """
    probs = np.array(predicted_probs, dtype=float)
    actual = np.array(outcomes, dtype=float)
    return float(np.mean((probs - actual) ** 2))


def log_loss_score(predicted_probs: Sequence[float], outcomes: Sequence[int], eps: float = 1e-15) -> float:
    """Log loss (cross-entropy). Heavily penalizes confident wrong predictions.

    Lower is better. Clipped to avoid log(0).
    """
    probs = np.clip(np.array(predicted_probs, dtype=float), eps, 1 - eps)
    actual = np.array(outcomes, dtype=float)
    return float(-np.mean(actual * np.log(probs) + (1 - actual) * np.log(1 - probs)))


def hit_rate_by_confidence(df: pd.DataFrame, bins: list[int] = None) -> pd.DataFrame:
    """Calculate hit rate grouped by confidence bins.

    A 'hit' means the signal direction was correct:
    - BUY YES + settlement=yes -> hit
    - SELL YES + settlement=no -> hit

    Args:
        df: DataFrame with columns: confidence, direction, settlement_outcome
        bins: Confidence bin edges (default: [0, 50, 70, 85, 100])

    Returns:
        DataFrame with columns: bin_label, count, hits, hit_rate
    """
    if bins is None:
        bins = [0, 50, 70, 85, 100]

    df = df.copy()
    df["hit"] = (
        ((df["direction"] == "BUY YES") & (df["settlement_outcome"] == "yes")) |
        ((df["direction"] == "SELL YES") & (df["settlement_outcome"] == "no"))
    ).astype(int)

    df["conf_bin"] = pd.cut(df["confidence"], bins=bins, include_lowest=True)
    result = df.groupby("conf_bin", observed=False).agg(
        count=("hit", "size"),
        hits=("hit", "sum"),
    ).reset_index()
    result["hit_rate"] = (result["hits"] / result["count"]).fillna(0)
    return result


def pnl_by_city(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate P&L by city.

    Args:
        df: DataFrame with columns: city, pnl

    Returns:
        DataFrame indexed by city with columns: total_pnl, count, avg_pnl
    """
    result = df.groupby("city").agg(
        total_pnl=("pnl", "sum"),
        count=("pnl", "size"),
        avg_pnl=("pnl", "mean"),
    )
    return result


def edge_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze whether signals with larger edges are more profitable.

    Args:
        df: DataFrame with columns: edge, settlement_outcome, direction

    Returns:
        DataFrame grouped by edge magnitude bins with hit rates
    """
    df = df.copy()
    df["abs_edge"] = df["edge"].abs()
    df["hit"] = (
        ((df["direction"] == "BUY YES") & (df["settlement_outcome"] == "yes")) |
        ((df["direction"] == "SELL YES") & (df["settlement_outcome"] == "no"))
    ).astype(int)

    bins = [0, 0.07, 0.105, 0.15, 0.20, 1.0]
    labels = ["7-10.5%", "10.5-15%", "15-20%", "20%+"]
    df["edge_bin"] = pd.cut(df["abs_edge"], bins=bins, labels=labels[:len(bins)-1], include_lowest=True)
    result = df.groupby("edge_bin", observed=False).agg(
        count=("hit", "size"),
        hits=("hit", "sum"),
    ).reset_index()
    result["hit_rate"] = (result["hits"] / result["count"]).fillna(0)
    return result
