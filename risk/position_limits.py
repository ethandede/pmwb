"""Layered position limits: per-order, per-scan, per-city/day, total exposure.

All limits are expressed as fractions of bankroll.
"""

from dataclasses import dataclass


# Default limit fractions (of bankroll)
PER_ORDER_FRAC = 0.03       # 3% of bankroll per order
PER_SCAN_FRAC = 0.10        # 10% of bankroll per scan cycle
PER_CITY_DAY_FRAC = 0.05    # 5% of bankroll per city/day
TOTAL_EXPOSURE_FRAC = 0.30  # 30% of bankroll total
MIN_ORDER_DOLLARS = 0.50     # Below this, skip the trade


@dataclass
class LimitResult:
    allowed_dollars: float
    blocked: bool
    reason: str


def check_limits(
    order_dollars: float,
    bankroll: float,
    scan_spent: float = 0.0,
    city_day_spent: float = 0.0,
    total_exposure: float = 0.0,
    per_order_frac: float = PER_ORDER_FRAC,
    per_scan_frac: float = PER_SCAN_FRAC,
    per_city_day_frac: float = PER_CITY_DAY_FRAC,
    total_exposure_frac: float = TOTAL_EXPOSURE_FRAC,
) -> LimitResult:
    """Apply layered limits, returning the effective allowed order size.

    Each limit is checked independently. The binding (tightest) limit wins.
    If the result falls below $0.50, the trade is blocked.
    """
    caps = []

    # Per-order cap
    per_order_cap = bankroll * per_order_frac
    caps.append(("per-order", per_order_cap))

    # Per-scan remaining
    per_scan_remaining = bankroll * per_scan_frac - scan_spent
    caps.append(("per-scan", per_scan_remaining))

    # Per-city/day remaining
    per_city_remaining = bankroll * per_city_day_frac - city_day_spent
    caps.append(("per-city/day", per_city_remaining))

    # Total exposure remaining
    total_remaining = bankroll * total_exposure_frac - total_exposure
    caps.append(("total exposure", total_remaining))

    # Find binding constraint
    binding_name, binding_cap = min(caps, key=lambda x: x[1])
    allowed = min(order_dollars, binding_cap)

    if allowed < MIN_ORDER_DOLLARS:
        return LimitResult(
            allowed_dollars=0.0,
            blocked=True,
            reason=f"{binding_name} limit reached (${binding_cap:.2f} remaining)",
        )

    return LimitResult(
        allowed_dollars=allowed,
        blocked=False,
        reason=f"OK (binding: {binding_name} at ${binding_cap:.2f})",
    )
