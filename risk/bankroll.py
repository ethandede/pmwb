"""Track effective bankroll for Kelly sizing.

Effective bankroll = cash balance + mark-to-market of open positions.
Tracks 7-day rolling peak for circuit breaker drawdown calculation.
"""

from datetime import datetime, timezone, timedelta


class BankrollTracker:
    def __init__(self, initial_bankroll: float = 1000.0):
        self._cash = initial_bankroll
        self._positions_value = 0.0
        self._peak = initial_bankroll
        self._peak_history: list[tuple[datetime, float]] = [
            (datetime.now(timezone.utc), initial_bankroll)
        ]
        self._daily_pnl = 0.0
        self._daily_pnl_reset: datetime | None = None

    def update_from_api(self, balance_cents: int, portfolio_value_cents: int):
        """Update from Kalshi API balance response (values in cents)."""
        self._cash = balance_cents / 100.0
        self._positions_value = portfolio_value_cents / 100.0
        current = self.effective_bankroll()

        # Update peak
        if current > self._peak:
            self._peak = current

        now = datetime.now(timezone.utc)
        self._peak_history.append((now, current))

        # Trim to 7-day window
        cutoff = now - timedelta(days=7)
        self._peak_history = [
            (t, v) for t, v in self._peak_history if t >= cutoff
        ]

        # Recalculate 7-day rolling peak
        if self._peak_history:
            self._peak = max(v for _, v in self._peak_history)

    def effective_bankroll(self) -> float:
        """Cash + mark-to-market of open positions."""
        return self._cash + self._positions_value

    def peak_bankroll(self) -> float:
        """7-day rolling peak bankroll."""
        return self._peak

    def drawdown_pct(self) -> float:
        """Current drawdown from 7-day rolling peak, as fraction (0.15 = 15%)."""
        if self._peak <= 0:
            return 0.0
        return (self._peak - self.effective_bankroll()) / self._peak

    def record_daily_pnl(self, pnl: float):
        """Record realized P&L for daily stop tracking."""
        now = datetime.now(timezone.utc)
        today = now.date()

        # Reset if new day
        if self._daily_pnl_reset is None or self._daily_pnl_reset.date() != today:
            self._daily_pnl = 0.0
            self._daily_pnl_reset = now

        self._daily_pnl += pnl

    def daily_pnl(self) -> float:
        """Today's cumulative realized P&L."""
        now = datetime.now(timezone.utc)
        if self._daily_pnl_reset and self._daily_pnl_reset.date() != now.date():
            return 0.0
        return self._daily_pnl
