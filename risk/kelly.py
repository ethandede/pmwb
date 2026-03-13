"""Kelly criterion for binary contract markets (Kalshi YES/NO)."""


def kelly_yes(model_prob: float, market_prob: float) -> float:
    """Kelly fraction for buying YES.

    f* = (q - p) / (1 - p)
    where p = market_prob (market YES price), q = model_prob (model probability of YES).
    Positive = bet YES, negative = don't bet YES.
    """
    if market_prob >= 1.0:
        return 0.0
    return (model_prob - market_prob) / (1.0 - market_prob)


def kelly_no(model_prob: float, market_prob: float) -> float:
    """Kelly fraction for buying NO.

    f* = (p - q) / p
    where p = market_prob (market YES price), q = model_prob (model probability of YES).
    Positive = bet NO, negative = don't bet NO.
    """
    if market_prob <= 0.0:
        return 0.0
    return (market_prob - model_prob) / market_prob


def kelly_fraction(
    model_prob: float,
    market_prob: float,
    fractional: float = 0.25,   # this is now the sigmoid output
    confidence: float = 100.0,
    max_fraction: float = 0.03,
) -> dict:
    """Compute fractional Kelly sized by the sigmoid multiplier.

    Returns dict with:
        side: "yes", "no", or None (no trade)
        fraction: bankroll fraction to bet (0 to max_fraction)
        raw_kelly: unadjusted Kelly fraction
    """
    f_yes = kelly_yes(model_prob, market_prob)
    f_no = kelly_no(model_prob, market_prob)

    if f_yes > 0:
        side = "yes"
        raw = f_yes
    elif f_no > 0:
        side = "no"
        raw = f_no
    else:
        return {"side": None, "fraction": 0.0, "raw_kelly": 0.0}

    # No more (confidence / 100) multiplier — sigmoid already handled it
    adjusted = raw * fractional
    adjusted = min(adjusted, max_fraction)

    if adjusted < 0.001:
        return {"side": None, "fraction": 0.0, "raw_kelly": raw}

    return {
        "side": side,
        "fraction": adjusted,
        "raw_kelly": raw,
    }
