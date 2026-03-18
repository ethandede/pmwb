"""ERCOT position manager — binary contract settlement.

Binary contracts auto-settle at hour end. No exit/fortify logic needed.
"""
from ercot.paper_trader import settle_expired_hours
from ercot.hubs import fetch_rt_settlement


def run_ercot_manager():
    """Settle any expired binary option positions."""
    settled = settle_expired_hours(fetch_rt_fn=fetch_rt_settlement)
    if settled:
        print(f"  ERCOT settled {len(settled)} positions")
    return settled
