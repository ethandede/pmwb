"""Maker/taker pricing strategy and Kalshi fee calculations.

Kalshi charges taker fees using: 0.07 * min(price, 100-price) / 100 per contract.
Maker (resting) orders are fee-exempt. This module decides whether to post as
maker or cross the spread as taker based on edge magnitude and urgency.
"""

TAKER_COEFFICIENT = 0.07
TAKER_EDGE_THRESHOLD = 0.15        # multi-day: need 15%+ edge to justify taker fees
TAKER_EDGE_THRESHOLD_SAMEDAY = 0.10  # same-day: lower bar (less time to fill)
MAKER_FALLBACK_OFFSET = 2           # cents below ask when no bid exists


def kalshi_fee(price_cents: int, count: int, is_taker: bool) -> float:
    """Calculate Kalshi fee for an order.

    Taker fee = TAKER_COEFFICIENT * min(price, 100-price) / 100 * count
    Maker fee = 0 (resting orders are fee-exempt)
    """
    if not is_taker:
        return 0.0
    return TAKER_COEFFICIENT * min(price_cents, 100 - price_cents) / 100.0 * count


def choose_price_strategy(
    side: str,
    yes_bid: int | None,
    yes_ask: int | None,
    edge: float,
    is_same_day: bool = False,
) -> tuple[int | None, str]:
    """Determine order price and maker/taker strategy.

    Args:
        side: "yes" or "no"
        yes_bid: best YES bid in cents (from Kalshi market data)
        yes_ask: best YES ask in cents
        edge: absolute edge as decimal (e.g. 0.12 for 12%)
        is_same_day: True for same-day events (more aggressive taker threshold)

    Returns:
        (price_cents, strategy) where strategy is "maker", "taker", or "unknown"
    """
    # Translate to our side's bid/ask
    if side == "yes":
        our_ask = yes_ask
        our_bid = yes_bid
    else:
        our_ask = (100 - yes_bid) if yes_bid is not None else None
        our_bid = (100 - yes_ask) if yes_ask is not None else None

    if our_ask is None:
        return None, "unknown"

    # Decide: taker or maker?
    threshold = TAKER_EDGE_THRESHOLD_SAMEDAY if is_same_day else TAKER_EDGE_THRESHOLD

    if edge >= threshold:
        return our_ask, "taker"

    # Maker: post inside the spread
    if our_bid is not None:
        maker_price = our_bid + 1
        # Don't cross the spread
        if maker_price >= our_ask:
            maker_price = our_ask - 1
    else:
        # No bid exists — post conservatively below the ask
        maker_price = our_ask - MAKER_FALLBACK_OFFSET

    maker_price = max(1, min(99, maker_price))
    return maker_price, "maker"
