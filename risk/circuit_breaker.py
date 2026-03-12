"""Drawdown circuit breaker and daily P&L stop.

- Drawdown breaker: 15% drop from 7-day rolling peak -> halve sizes for 48h
- Daily stop: -5% realized+unrealized -> stop trading until next day
"""

from datetime import datetime, timezone, timedelta


class CircuitBreaker:
    def __init__(
        self,
        drawdown_threshold: float = 0.15,
        daily_stop_pct: float = 0.05,
        cooldown_hours: int = 48,
    ):
        self.drawdown_threshold = drawdown_threshold
        self.daily_stop_pct = daily_stop_pct
        self.cooldown_hours = cooldown_hours
        self._tripped = False
        self._trip_time: datetime | None = None
        self._trip_reason: str = ""

    def is_tripped(self, drawdown_pct: float, daily_pnl_pct: float) -> bool:
        """Check if either breaker condition is met.

        Args:
            drawdown_pct: Current drawdown from 7-day rolling peak (0.15 = 15%)
            daily_pnl_pct: Today's P&L as fraction of bankroll (negative = loss)
        """
        if drawdown_pct >= self.drawdown_threshold:
            self.trip("drawdown")
            return True
        if daily_pnl_pct <= -self.daily_stop_pct:
            self.trip("daily_stop")
            return True
        return self.is_in_cooldown()

    def trip(self, reason: str):
        """Manually trip the breaker."""
        self._tripped = True
        self._trip_time = datetime.now(timezone.utc)
        self._trip_reason = reason

    def is_in_cooldown(self) -> bool:
        """Check if breaker is still in cooldown period."""
        if not self._tripped or self._trip_time is None:
            return False
        elapsed = datetime.now(timezone.utc) - self._trip_time
        if elapsed > timedelta(hours=self.cooldown_hours):
            self._tripped = False
            return False
        return True

    def size_multiplier(self) -> float:
        """Return position size multiplier.

        - 1.0: normal operation
        - 0.5: drawdown breaker active (halve sizes for 48h)
        - 0.0: daily stop active (full stop until next day)
        """
        if self.is_in_cooldown():
            if self._trip_reason == "daily_stop":
                return 0.0
            return 0.5
        return 1.0

    def status(self) -> dict:
        """Return current breaker status for logging."""
        return {
            "tripped": self._tripped,
            "reason": self._trip_reason,
            "trip_time": self._trip_time.isoformat() if self._trip_time else None,
            "in_cooldown": self.is_in_cooldown(),
            "size_multiplier": self.size_multiplier(),
        }
