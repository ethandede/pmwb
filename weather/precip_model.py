"""Precipitation probability models for Kalshi weather markets.

Two models with identical interfaces:
  1. empirical_precip_prob() — simple ensemble count (baseline)
  2. gamma_precip_prob() — zero-inflated CSGD (primary)

Both return PrecipForecast so multi_model.py can swap them transparently.
"""

import logging
from dataclasses import dataclass

from scipy.stats import gamma as gamma_dist

logger = logging.getLogger(__name__)


@dataclass
class PrecipForecast:
    p_dry: float              # probability of zero precipitation
    shape: float              # gamma shape parameter
    scale: float              # gamma scale parameter
    shift: float = 0.0        # CSGD shift (0.0 for basic gamma)
    prob_above: float = 0.0   # P(precip > threshold)
    fraction_above: float = 0.0  # fraction of ensemble members above threshold
    method: str = "empirical"    # "empirical" or "csgd"


def gamma_precip_prob(
    members: list[float],
    threshold: float,
    nws_pop: float = 0.5,
) -> PrecipForecast:
    """Zero-inflated CSGD: P(precip > threshold) using fitted gamma on wet members.

    Args:
        members: Precipitation values from ensemble (inches).
        threshold: Inches threshold (0.0 for daily binary).
        nws_pop: NWS probability of precipitation [0, 1]. Used to blend
                 p_dry estimate (reduces ensemble dry bias).
    """
    if not members:
        return PrecipForecast(p_dry=1.0, shape=0.0, scale=0.0,
                              prob_above=0.0, fraction_above=0.0, method="csgd")

    n = len(members)
    nonzero = [m for m in members if m > 0.0]
    n_above = sum(1 for m in members if m > threshold)
    fraction_above = n_above / n

    # All dry → probability is 0 for any positive threshold
    if not nonzero:
        return PrecipForecast(p_dry=1.0, shape=0.0, scale=0.0,
                              prob_above=0.0, fraction_above=fraction_above, method="csgd")

    # Blended p_dry: 70% ensemble + 30% NWS (reduces ensemble dry bias)
    ensemble_dry_frac = (n - len(nonzero)) / n
    p_dry = 0.7 * ensemble_dry_frac + 0.3 * (1.0 - nws_pop)
    p_dry = max(0.0, min(1.0, p_dry))  # clamp

    # Fewer than 3 non-zero members → gamma fit unreliable, fall back to empirical
    if len(nonzero) < 3:
        return empirical_precip_prob(members, threshold)

    # Fit gamma to non-zero members
    try:
        shape, loc, scale = gamma_dist.fit(nonzero, floc=0)
        # Check for degenerate fit
        if shape < 0.01 or scale > 1000:
            logger.warning(
                f"Degenerate gamma fit: shape={shape}, scale={scale}. Falling back to empirical."
            )
            return empirical_precip_prob(members, threshold)
    except Exception as e:
        logger.warning(f"Gamma fit failed: {e}. Falling back to empirical.")
        return empirical_precip_prob(members, threshold)

    # P(precip > threshold) = (1 - p_dry) * (1 - gamma.cdf(threshold, shape, scale))
    if threshold <= 0.0:
        prob_above = 1.0 - p_dry
    else:
        prob_above = (1.0 - p_dry) * (1.0 - gamma_dist.cdf(threshold, shape, scale=scale))

    prob_above = max(0.0, min(1.0, prob_above))

    return PrecipForecast(
        p_dry=round(p_dry, 4),
        shape=round(shape, 4),
        scale=round(scale, 4),
        prob_above=round(prob_above, 4),
        fraction_above=round(fraction_above, 4),
        method="csgd",
    )


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
