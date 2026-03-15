"""ErcotExchange — wraps ERCOT data fetching and paper trading DB.

Delegates to ercot/hubs.py for market data and ercot/paper_trader.py for positions.
"""

from ercot.hubs import _fetch_ercot_market_data, ERCOT_HUBS
from ercot import paper_trader


class ErcotExchange:
    def fetch_market_data(self) -> dict:
        """Cached ERCOT prices, solar generation, load forecast."""
        return _fetch_ercot_market_data()

    def get_hubs(self) -> dict:
        """Return hub configuration."""
        return ERCOT_HUBS

    def get_positions(self) -> list:
        """Open paper positions from ercot_paper.db."""
        return paper_trader.get_open_positions()

    def open_position(self, hub_signal: dict, bankroll: float) -> dict | None:
        """Open a new paper position."""
        return paper_trader.open_position(hub_signal, bankroll)

    def close_position(self, position_id: int, exit_price: float,
                       exit_signal: str, reason: str):
        """Close a paper position."""
        return paper_trader.close_position(position_id, exit_price, exit_signal, reason)

    def expire_positions(self, current_price: float):
        """Auto-close positions past TTL."""
        return paper_trader.expire_positions(current_price)
