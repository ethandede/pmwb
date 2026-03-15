import math
from typing import Dict, Optional

def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Pure-Python normal CDF using erf (no scipy, no numpy). Accurate to ~1e-7."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))

def deterministic_bucket_prob(
    low: float, high: float, forecast_temp: float, spread: float = 4.5
) -> float:
    """
    P(actual temp in [low, high)) assuming Normal(forecast_temp, spread).
    This is how real weather ensembles work. Mathematically impossible to go negative.
    """
    if high <= low:
        return 0.01

    prob = normal_cdf(high, forecast_temp, spread) - normal_cdf(low, forecast_temp, spread)
    return round(max(0.01, min(0.99, prob)), 4)


def fuse_model_probs(
    model_probs: Dict[str, float],
    weights: Optional[Dict[str, float]] = None
) -> float:
    """Safe fusion with hard validation + logging."""
    if not model_probs:
        return 0.50

    # Defensive layer 1: log and clamp any bad model output (this will never trigger again)
    for name, p in list(model_probs.items()):
        if not (0.0 <= p <= 1.0):
            print(f"[BAD INPUT] {name} = {p} (clamped)")
            model_probs[name] = max(0.0, min(1.0, p))

    if weights is None:
        weights = {k: 1.0 / len(model_probs) for k in model_probs}

    fused = sum(model_probs[name] * weights.get(name, 0.0) for name in model_probs)
    return round(max(0.01, min(0.99, fused)), 4)