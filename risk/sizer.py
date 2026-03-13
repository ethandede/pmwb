"""Orchestrator: combine Kelly, limits, and circuit breaker into a sizing decision.

Usage:
    result = compute_size(model_prob, market_prob, confidence, price_cents,
                          bankroll_tracker, circuit_breaker, ...)
    if result.count > 0:
        place_order(ticker, result.side, price_cents, result.count)
"""

import math
from dataclasses import dataclass
from risk.kelly import kelly_fraction
from risk.position_limits import check_limits
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker
from config import FRACTIONAL_KELLY


def sigmoid_kelly(confidence: float, edge: float, floor: float = 0.25) -> float:
    """Sigmoid-scaled Kelly multiplier — smooth logistic ramp from floor to 0.50x.

    Inputs:
        confidence: model agreement score (0-100), gated at 55 minimum
        edge: absolute edge as a decimal (e.g. 0.12 for 12%)
        floor: minimum Kelly multiplier (0.25 normal, 0.35 same-day)

    The function normalizes both inputs to [0, 1], combines them into a single
    signal-strength score, passes it through a logistic sigmoid, and maps the
    output to a Kelly multiplier in [floor, 0.50].

    Behavior (floor=0.25):
        - Floor (55% conf, 7% edge):  ~0.25x Kelly
        - Mid   (70% conf, 12% edge): ~0.38x Kelly
        - High  (90% conf, 18% edge): ~0.50x Kelly
    """
    c_norm = max(0.0, (confidence - 55) / 45)
    e_norm = min(1.0, abs(edge) / 0.20)
    strength = 8.5 * c_norm * e_norm - 3.8
    sig = 1.0 / (1.0 + math.exp(-strength))
    # Scale from floor to 0.50: floor + (0.50 - floor) * sigmoid
    ramp = 0.50 - floor
    return min(0.50, floor + ramp * sig)


@dataclass
class SizeResult:
    side: str | None       # "yes", "no", or None
    count: int             # number of contracts
    dollar_amount: float   # total cost in dollars
    raw_kelly: float       # unadjusted Kelly fraction
    adjusted_kelly: float  # after fractional + confidence
    limit_reason: str      # which limit was binding


MAX_CONTRACTS_PER_EVENT = 10  # max contracts per city/day/type


def compute_size(
    model_prob: float,
    market_prob: float,
    confidence: float,
    price_cents: int,
    bankroll_tracker: BankrollTracker,
    circuit_breaker: CircuitBreaker,
    scan_spent: float = 0.0,
    city_day_spent: float = 0.0,
    total_exposure: float = 0.0,
    event_contracts: int = 0,
    fractional_kelly: float = FRACTIONAL_KELLY,
) -> SizeResult:
    """Compute position size for a signal.

    Pipeline: circuit breaker check → Kelly → layered limits → per-event cap → contract count.
    """
    bankroll = bankroll_tracker.effective_bankroll()

    # Step 0: Evaluate circuit breaker (drawdown + daily P&L)
    daily_pnl_pct = bankroll_tracker.daily_pnl() / bankroll if bankroll > 0 else 0
    circuit_breaker.is_tripped(
        drawdown_pct=bankroll_tracker.drawdown_pct(),
        daily_pnl_pct=daily_pnl_pct,
    )
    cb_mult = circuit_breaker.size_multiplier()
    if cb_mult == 0.0:
        return SizeResult(
            side=None, count=0, dollar_amount=0.0,
            raw_kelly=0.0, adjusted_kelly=0.0,
            limit_reason="daily stop active",
        )

    # Step 1: Kelly fraction with sigmoid-scaled multiplier
    edge = abs(model_prob - market_prob)
    scaled_kelly = sigmoid_kelly(confidence, edge, floor=fractional_kelly)
    kelly = kelly_fraction(
        model_prob=model_prob,
        market_prob=market_prob,
        fractional=scaled_kelly,
        confidence=confidence,
    )

    if kelly["side"] is None:
        return SizeResult(
            side=None, count=0, dollar_amount=0.0,
            raw_kelly=kelly["raw_kelly"], adjusted_kelly=0.0,
            limit_reason="no edge",
        )

    # Step 2: Dollar amount from Kelly fraction
    kelly_dollars = kelly["fraction"] * bankroll

    # Step 3: Circuit breaker multiplier (0.5x for drawdown)
    kelly_dollars *= cb_mult

    # Step 4: Layered limits
    limits = check_limits(
        order_dollars=kelly_dollars,
        bankroll=bankroll,
        scan_spent=scan_spent,
        city_day_spent=city_day_spent,
        total_exposure=total_exposure,
    )

    if limits.blocked:
        return SizeResult(
            side=kelly["side"], count=0, dollar_amount=0.0,
            raw_kelly=kelly["raw_kelly"], adjusted_kelly=kelly["fraction"],
            limit_reason=limits.reason,
        )

    # Step 5: Convert dollars → contract count
    final_dollars = limits.allowed_dollars
    count = int(final_dollars * 100 / max(price_cents, 1))
    if count <= 0:
        return SizeResult(
            side=kelly["side"], count=0, dollar_amount=0.0,
            raw_kelly=kelly["raw_kelly"], adjusted_kelly=kelly["fraction"],
            limit_reason="below minimum contract size",
        )

    # Step 6: Per-event contract cap (max 2 per city/day/type)
    remaining_event = MAX_CONTRACTS_PER_EVENT - event_contracts
    if remaining_event <= 0:
        return SizeResult(
            side=kelly["side"], count=0, dollar_amount=0.0,
            raw_kelly=kelly["raw_kelly"], adjusted_kelly=kelly["fraction"],
            limit_reason=f"event cap ({MAX_CONTRACTS_PER_EVENT} contracts)",
        )
    count = min(count, remaining_event)

    actual_cost = count * price_cents / 100.0

    return SizeResult(
        side=kelly["side"],
        count=count,
        dollar_amount=actual_cost,
        raw_kelly=kelly["raw_kelly"],
        adjusted_kelly=kelly["fraction"],
        limit_reason=limits.reason,
    )
