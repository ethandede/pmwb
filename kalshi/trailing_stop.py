"""Trailing stop tracker — tracks high-water marks for open positions.

Persists peak prices to disk so state survives daemon restarts.
Trailing distance adapts to:
  - Time to settlement (tighter same-day, looser multi-day)
  - Price level (tighter at extremes where conviction should be high)
  - How far the position has moved in our favor (only activates after meaningful gain)
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

STATE_FILE = "data/trailing_stops.json"

# Minimum gain before trailing stop activates (cents)
# Don't trail a position that hasn't moved meaningfully yet
ACTIVATION_GAIN = 5  # position must be 5¢+ above entry before trailing kicks in

# Trailing distances (in cents) by context
TRAIL_SAMEDAY = 4     # same-day: tight 4¢ trail — forecast is locked
TRAIL_MULTIDAY = 8    # multi-day: wider 8¢ trail — price can be noisy
TRAIL_HIGH_PRICE = 3  # positions above 85¢: very tight 3¢ — should be converging to $1
TRAIL_LOW_PRICE = 6   # positions below 20¢: wider trail — lottery tickets are volatile


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _trail_distance(current_price_cents: int, days_ahead: int) -> int:
    """Calculate trailing distance in cents based on context."""
    # High price: near certain, trail tight
    if current_price_cents >= 85:
        return TRAIL_HIGH_PRICE

    # Low price: lottery ticket, trail wide
    if current_price_cents <= 20:
        return TRAIL_LOW_PRICE

    # Time-based
    if days_ahead == 0:
        return TRAIL_SAMEDAY
    return TRAIL_MULTIDAY


def update_peak(ticker: str, side: str, current_price_cents: int, entry_price_cents: Optional[int] = None) -> dict:
    """Update the high-water mark for a position. Returns state for this ticker."""
    state = _load_state()
    key = f"{ticker}:{side}"

    if key not in state:
        state[key] = {
            "peak_cents": current_price_cents,
            "entry_cents": entry_price_cents or current_price_cents,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "peak_updated": datetime.now(timezone.utc).isoformat(),
        }
    else:
        if current_price_cents > state[key]["peak_cents"]:
            state[key]["peak_cents"] = current_price_cents
            state[key]["peak_updated"] = datetime.now(timezone.utc).isoformat()

    _save_state(state)
    return state[key]


def check_trailing_stop(ticker: str, side: str, current_price_cents: int, days_ahead: int) -> Optional[str]:
    """Check if trailing stop is triggered.

    Returns reason string if triggered, None if position should hold.
    """
    state = _load_state()
    key = f"{ticker}:{side}"

    if key not in state:
        return None

    entry = state[key]
    peak = entry["peak_cents"]
    entry_price = entry.get("entry_cents", peak)

    # Don't activate until position has gained meaningfully
    gain_from_entry = peak - entry_price
    if gain_from_entry < ACTIVATION_GAIN:
        return None

    # Calculate trail distance
    trail = _trail_distance(current_price_cents, days_ahead)

    # Check if price dropped from peak by more than trail
    drop = peak - current_price_cents
    if drop >= trail:
        return (
            f"TRAILING STOP — peaked at {peak}¢, now {current_price_cents}¢ "
            f"(dropped {drop}¢, trail={trail}¢)"
        )

    return None


def remove_position(ticker: str, side: str):
    """Remove a position from tracking (after exit or settlement)."""
    state = _load_state()
    key = f"{ticker}:{side}"
    if key in state:
        del state[key]
        _save_state(state)


def cleanup_settled():
    """Remove entries for tickers that are no longer in our portfolio."""
    # Called externally with list of active tickers
    pass


def get_all_stops() -> dict:
    """Return all tracked positions for display."""
    return _load_state()
