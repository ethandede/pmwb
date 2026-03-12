"""Precipitation probability models for Kalshi weather markets.

Two models with identical interfaces:
  1. empirical_precip_prob() — simple ensemble count (baseline)
  2. gamma_precip_prob() — zero-inflated CSGD (primary)

Both return PrecipForecast so multi_model.py can swap them transparently.
"""

from dataclasses import dataclass


@dataclass
class PrecipForecast:
    p_dry: float              # probability of zero precipitation
    shape: float              # gamma shape parameter
    scale: float              # gamma scale parameter
    shift: float = 0.0        # CSGD shift (0.0 for basic gamma)
    prob_above: float = 0.0   # P(precip > threshold)
    fraction_above: float = 0.0  # fraction of ensemble members above threshold
    method: str = "empirical"    # "empirical" or "csgd"


def empirical_precip_prob(
    members: list[float],
    threshold: float,
) -> PrecipForecast:
    """Empirical CDF baseline: P(precip > threshold) = count(members > threshold) / N.

    Args:
        members: Precipitation values from ensemble (inches, already summed for monthly).
        threshold: Inches threshold (0.0 for daily binary "any rain").
    """
    if not members:
        return PrecipForecast(p_dry=1.0, shape=0.0, scale=0.0, prob_above=0.0,
                              fraction_above=0.0, method="empirical")

    n = len(members)
    n_dry = sum(1 for m in members if m <= 0.0)
    n_above = sum(1 for m in members if m > threshold)

    return PrecipForecast(
        p_dry=n_dry / n,
        shape=0.0,
        scale=0.0,
        prob_above=n_above / n,
        fraction_above=n_above / n,
        method="empirical",
    )
