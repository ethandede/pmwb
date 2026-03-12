# Phase 2: Kelly Sizing & Risk Management — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed $2/order, $10/scan caps with bankroll-aware Kelly sizing, layered position limits, and a drawdown circuit breaker.

**Architecture:** New `risk/` package with focused modules: `kelly.py` (math), `bankroll.py` (balance tracking), `position_limits.py` (layered caps), `circuit_breaker.py` (drawdown/daily stop), and `sizer.py` (orchestrator). Modify `kalshi/trader.py` to call `sizer.compute_size()` instead of hard-coded sizing. Update `backtesting/walk_forward.py` to support Kelly mode.

**Tech Stack:** Python 3.10, pytest (existing), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-12-weather-trading-system-design.md` Section 2

---

## File Structure

```
risk/
├── __init__.py          # Empty package init
├── kelly.py             # Kelly fraction for binary YES/NO bets
├── bankroll.py          # Effective bankroll = cash + mark-to-market
├── position_limits.py   # Layered limit checks (per-order, per-scan, per-city, total)
├── circuit_breaker.py   # Drawdown detection + daily P&L stop
└── sizer.py             # Orchestrator: (signal, bankroll, limits) → dollar amount
```

**Modifications:**
- `kalshi/trader.py:130-162` — replace hard-coded sizing with `sizer.compute_size()`
- `config.py` — add Kelly/risk config constants
- `backtesting/walk_forward.py:19-26` — add `kelly_mode` parameter

---

## Chunk 1: Kelly Calculator + Bankroll Tracker

### Task 1: Implement Kelly fraction calculator

**Files:**
- Create: `risk/__init__.py`
- Create: `risk/kelly.py`
- Create: `tests/test_kelly.py`

- [ ] **Step 1: Write failing test for Kelly fractions**

Create `tests/test_kelly.py`:
```python
import pytest
from risk.kelly import kelly_yes, kelly_no, kelly_fraction


def test_kelly_yes_positive_edge():
    """Model says 70% YES, market at 55% → positive Kelly."""
    f = kelly_yes(model_prob=0.70, market_prob=0.55)
    # f* = (0.70 - 0.55) / (1 - 0.55) = 0.15 / 0.45 ≈ 0.333
    assert f == pytest.approx(0.3333, abs=0.01)


def test_kelly_yes_no_edge():
    """Model agrees with market → Kelly = 0."""
    f = kelly_yes(model_prob=0.55, market_prob=0.55)
    assert f == pytest.approx(0.0)


def test_kelly_yes_negative_edge():
    """Model says 40% YES, market at 55% → negative (don't bet YES)."""
    f = kelly_yes(model_prob=0.40, market_prob=0.55)
    assert f < 0


def test_kelly_no_positive_edge():
    """Market YES = 0.70, model YES = 0.55 → buy NO."""
    f = kelly_no(model_prob=0.55, market_prob=0.70)
    # f* = (0.70 - 0.55) / 0.70 ≈ 0.214
    assert f == pytest.approx(0.2143, abs=0.01)


def test_kelly_no_spec_worked_example():
    """Spec worked example: market YES=0.70, model YES=0.55."""
    f = kelly_no(model_prob=0.55, market_prob=0.70)
    assert f == pytest.approx(0.214, abs=0.01)


def test_kelly_fraction_yes_bet():
    """Full pipeline: positive edge → YES bet with fractional Kelly."""
    result = kelly_fraction(
        model_prob=0.70, market_prob=0.55,
        fractional=0.25, confidence=85,
    )
    # raw = 0.333, adjusted = 0.333 * 0.25 * (85/100) = 0.0708
    assert result["side"] == "yes"
    assert result["fraction"] == pytest.approx(0.0708, abs=0.005)
    assert result["raw_kelly"] == pytest.approx(0.333, abs=0.01)


def test_kelly_fraction_no_bet():
    """Negative edge → NO bet with correct Kelly math."""
    result = kelly_fraction(
        model_prob=0.40, market_prob=0.55,
        fractional=0.25, confidence=90,
    )
    assert result["side"] == "no"
    # raw = (0.55 - 0.40) / 0.55 = 0.2727
    assert result["raw_kelly"] == pytest.approx(0.2727, abs=0.01)
    # adjusted = 0.2727 * 0.25 * (90/100) = 0.0614
    assert result["fraction"] == pytest.approx(0.0614, abs=0.005)


def test_kelly_yes_market_at_one():
    """Market price at 1.0 → no YES bet possible."""
    assert kelly_yes(0.95, 1.0) == 0.0


def test_kelly_no_market_at_zero():
    """Market price at 0.0 → no NO bet possible."""
    assert kelly_no(0.05, 0.0) == 0.0


def test_kelly_fraction_no_trade():
    """Tiny edge → fraction rounds to 0 → no trade."""
    result = kelly_fraction(
        model_prob=0.50, market_prob=0.50,
        fractional=0.25, confidence=50,
    )
    assert result["side"] is None
    assert result["fraction"] == 0.0


def test_kelly_fraction_clamps_extreme():
    """Even at huge edge, fraction never exceeds max_fraction."""
    result = kelly_fraction(
        model_prob=0.99, market_prob=0.10,
        fractional=0.5, confidence=95,
        max_fraction=0.03,
    )
    assert result["fraction"] <= 0.03
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_kelly.py -v`
Expected: FAIL — `No module named 'risk'`

- [ ] **Step 3: Implement kelly.py**

Create `risk/__init__.py` (empty file).

Create `risk/kelly.py`:
```python
"""Kelly criterion for binary contract markets (Kalshi YES/NO)."""


def kelly_yes(model_prob: float, market_prob: float) -> float:
    """Kelly fraction for buying YES.

    f* = (q - p) / (1 - p)
    where q = model probability of YES, p = market YES price.
    Positive = bet YES, negative = don't bet YES.
    """
    if market_prob >= 1.0:
        return 0.0
    return (model_prob - market_prob) / (1.0 - market_prob)


def kelly_no(model_prob: float, market_prob: float) -> float:
    """Kelly fraction for buying NO.

    f* = (p - q) / p
    where p = market YES price, q = model probability of YES.
    Positive = bet NO, negative = don't bet NO.
    """
    if market_prob <= 0.0:
        return 0.0
    return (market_prob - model_prob) / market_prob


def kelly_fraction(
    model_prob: float,
    market_prob: float,
    fractional: float = 0.25,
    confidence: float = 100.0,
    max_fraction: float = 0.03,
) -> dict:
    """Compute confidence-adjusted fractional Kelly.

    Returns dict with:
        side: "yes", "no", or None (no trade)
        fraction: bankroll fraction to bet (0 to max_fraction)
        raw_kelly: unadjusted Kelly fraction
        dollar_amount: None (caller multiplies by bankroll)
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

    # Confidence-adjusted fractional Kelly:
    # adjusted_f = f_kelly * fractional * (confidence / 100)
    adjusted = raw * fractional * (confidence / 100.0)

    # Clamp to max_fraction
    adjusted = min(adjusted, max_fraction)

    if adjusted < 0.001:  # less than 0.1% of bankroll → skip
        return {"side": None, "fraction": 0.0, "raw_kelly": raw}

    return {
        "side": side,
        "fraction": adjusted,
        "raw_kelly": raw,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_kelly.py -v`
Expected: 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add risk/__init__.py risk/kelly.py tests/test_kelly.py
git commit -m "feat: add Kelly criterion calculator for binary contract sizing"
```

---

### Task 2: Implement bankroll tracker

**Files:**
- Create: `risk/bankroll.py`
- Create: `tests/test_bankroll.py`

- [ ] **Step 1: Write failing test for bankroll tracker**

Create `tests/test_bankroll.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from risk.bankroll import BankrollTracker


def test_bankroll_from_manual():
    """Manual bankroll when API is unavailable."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    assert bt.effective_bankroll() == 1000.0


def test_bankroll_from_api_response():
    """Bankroll from mocked API balance response."""
    bt = BankrollTracker(initial_bankroll=500.0)
    # Simulate Kalshi balance response (values in cents)
    bt.update_from_api(balance_cents=75000, portfolio_value_cents=25000)
    # $750 cash + $250 positions = $1000
    assert bt.effective_bankroll() == 1000.0


def test_bankroll_tracks_peak():
    """7-day rolling peak should update."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    bt.update_from_api(balance_cents=120000, portfolio_value_cents=0)
    assert bt.peak_bankroll() == 1200.0
    # Drop — peak stays
    bt.update_from_api(balance_cents=90000, portfolio_value_cents=0)
    assert bt.peak_bankroll() == 1200.0


def test_bankroll_drawdown_pct():
    """Drawdown should be calculated from peak."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    bt.update_from_api(balance_cents=120000, portfolio_value_cents=0)
    bt.update_from_api(balance_cents=102000, portfolio_value_cents=0)
    # Peak = 1200, current = 1020, drawdown = 180/1200 = 15%
    assert bt.drawdown_pct() == pytest.approx(0.15)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_bankroll.py -v`
Expected: FAIL — `No module named 'risk.bankroll'`

- [ ] **Step 3: Implement bankroll.py**

Create `risk/bankroll.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_bankroll.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add risk/bankroll.py tests/test_bankroll.py
git commit -m "feat: add bankroll tracker with 7-day rolling peak and drawdown calculation"
```

---

## Chunk 2: Position Limits + Circuit Breaker

### Task 3: Implement layered position limits

**Files:**
- Create: `risk/position_limits.py`
- Create: `tests/test_position_limits.py`

- [ ] **Step 1: Write failing test for position limits**

Create `tests/test_position_limits.py`:
```python
import pytest
from risk.position_limits import check_limits, LimitResult


def test_per_order_limit():
    """Order exceeding per-order cap gets clamped."""
    result = check_limits(
        order_dollars=50.0,
        bankroll=1000.0,
        scan_spent=0.0,
        city_day_spent=0.0,
        total_exposure=0.0,
    )
    # 3% of $1000 = $30 max per order
    assert result.allowed_dollars <= 30.0
    assert result.allowed_dollars > 0


def test_per_scan_limit():
    """Scan budget cap respected."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        scan_spent=95.0,  # 9.5% already spent, limit is 10%
    )
    # Only $5 remaining in scan budget (10% of 1000 = 100)
    assert result.allowed_dollars <= 5.0


def test_per_city_day_limit():
    """City/day exposure cap respected."""
    result = check_limits(
        order_dollars=40.0,
        bankroll=1000.0,
        city_day_spent=45.0,  # 4.5% already, limit is 5%
    )
    # Only $5 remaining (5% of 1000 = 50)
    assert result.allowed_dollars <= 5.0


def test_total_exposure_limit():
    """Total exposure cap respected."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        total_exposure=290.0,  # 29% already, limit is 30%
    )
    # Only $10 remaining (30% of 1000 = 300)
    assert result.allowed_dollars <= 10.0


def test_below_minimum():
    """When all limits collapse below $0.50, signal no-trade."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        scan_spent=99.8,  # only $0.20 remaining
    )
    assert result.allowed_dollars == 0.0
    assert result.blocked
    assert "scan" in result.reason.lower()


def test_all_limits_pass():
    """Fresh scan, no exposure — full order allowed (up to per-order cap)."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
    )
    assert result.allowed_dollars == 20.0
    assert not result.blocked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_position_limits.py -v`
Expected: FAIL — `No module named 'risk.position_limits'`

- [ ] **Step 3: Implement position_limits.py**

Create `risk/position_limits.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_position_limits.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add risk/position_limits.py tests/test_position_limits.py
git commit -m "feat: add layered position limits (per-order, per-scan, per-city, total)"
```

---

### Task 4: Implement circuit breaker

**Files:**
- Create: `risk/circuit_breaker.py`
- Create: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write failing test for circuit breaker**

Create `tests/test_circuit_breaker.py`:
```python
import pytest
from datetime import datetime, timezone, timedelta
from risk.circuit_breaker import CircuitBreaker


def test_no_trip_normal_conditions():
    """Normal conditions — not tripped."""
    cb = CircuitBreaker()
    assert not cb.is_tripped(drawdown_pct=0.05, daily_pnl_pct=-0.02)


def test_drawdown_trips_breaker():
    """15% drawdown from 7-day peak triggers breaker."""
    cb = CircuitBreaker(drawdown_threshold=0.15)
    assert cb.is_tripped(drawdown_pct=0.16, daily_pnl_pct=0.0)
    assert cb.size_multiplier() == 0.5  # halve sizes


def test_daily_stop_trips_breaker():
    """-5% daily P&L triggers daily stop."""
    cb = CircuitBreaker(daily_stop_pct=0.05)
    assert cb.is_tripped(drawdown_pct=0.0, daily_pnl_pct=-0.06)


def test_cooldown_period():
    """After tripping, breaker stays active for cooldown period."""
    cb = CircuitBreaker(cooldown_hours=48)
    cb.trip("drawdown")
    assert cb.is_in_cooldown()
    assert cb.size_multiplier() == 0.5


def test_cooldown_expires():
    """After cooldown period, breaker resets."""
    cb = CircuitBreaker(cooldown_hours=48)
    # Trip with a time in the past
    cb._trip_time = datetime.now(timezone.utc) - timedelta(hours=49)
    cb._tripped = True
    assert not cb.is_in_cooldown()
    assert cb.size_multiplier() == 1.0


def test_daily_stop_full_stop():
    """Daily stop should return 0.0 multiplier (full stop), not 0.5."""
    cb = CircuitBreaker()
    cb.trip("daily_stop")
    assert cb.size_multiplier() == 0.0


def test_drawdown_halves_not_stops():
    """Drawdown breaker halves sizes (0.5x), does not fully stop."""
    cb = CircuitBreaker()
    cb.trip("drawdown")
    assert cb.size_multiplier() == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_circuit_breaker.py -v`
Expected: FAIL — `No module named 'risk.circuit_breaker'`

- [ ] **Step 3: Implement circuit_breaker.py**

Create `risk/circuit_breaker.py`:
```python
"""Drawdown circuit breaker and daily P&L stop.

- Drawdown breaker: 15% drop from 7-day rolling peak → halve sizes for 48h
- Daily stop: -5% realized+unrealized → stop trading until next day
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_circuit_breaker.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add risk/circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat: add circuit breaker with drawdown detection and daily stop"
```

---

## Chunk 3: Sizer Orchestrator + Trader Integration

### Task 5: Implement sizer orchestrator

**Files:**
- Create: `risk/sizer.py`
- Create: `tests/test_sizer.py`

- [ ] **Step 1: Write failing test for sizer**

Create `tests/test_sizer.py`:
```python
import pytest
from risk.sizer import compute_size, SizeResult
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker


def test_compute_size_basic():
    """Basic sizing: Kelly → limited → contract count."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    assert result.count > 0
    assert result.side == "yes"
    assert result.dollar_amount > 0
    assert result.dollar_amount <= 30.0  # 3% of $1000


def test_compute_size_no_bet():
    """No edge → no trade."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.55, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    assert result.count == 0
    assert result.side is None


def test_compute_size_drawdown_breaker_halves():
    """Drawdown circuit breaker → half the normal size."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    # Simulate drawdown: peak was 1200, now 1000 = 16.7% drawdown
    bt.update_from_api(balance_cents=120000, portfolio_value_cents=0)
    bt.update_from_api(balance_cents=100000, portfolio_value_cents=0)

    cb = CircuitBreaker(drawdown_threshold=0.15)

    result_tripped = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )

    cb2 = CircuitBreaker()
    result_normal = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb2,
    )

    # Tripped should be approximately half of normal
    assert result_tripped.dollar_amount < result_normal.dollar_amount
    assert result_tripped.dollar_amount == pytest.approx(
        result_normal.dollar_amount * 0.5, abs=1.0
    )


def test_compute_size_daily_stop_blocks():
    """Daily stop → zero contracts (full stop, not halving)."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    bt.record_daily_pnl(-60.0)  # -6% of bankroll

    cb = CircuitBreaker(daily_stop_pct=0.05)
    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    assert result.count == 0
    assert "daily stop" in result.limit_reason


def test_compute_size_event_cap():
    """Per-event 2-contract cap limits output."""
    bt = BankrollTracker(initial_bankroll=10000.0)  # large bankroll
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.80, market_prob=0.55,
        confidence=95.0, price_cents=10,  # cheap contracts
        bankroll_tracker=bt, circuit_breaker=cb,
        event_contracts=1,  # already 1 contract on this event
    )
    # Should cap at 1 more contract (max 2 per event)
    assert result.count <= 1


def test_compute_size_respects_scan_budget():
    """Scan budget limits size."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
        scan_spent=95.0,  # 9.5% spent of 10% limit
    )
    # Only $5 remaining in scan budget
    assert result.dollar_amount <= 5.0


def test_compute_size_returns_contract_count():
    """Dollar amount → correct contract count at given price."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.80, market_prob=0.55,
        confidence=90.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    # count = dollar_amount * 100 / price_cents
    expected_count = int(result.dollar_amount * 100 / 55)
    assert result.count == expected_count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_sizer.py -v`
Expected: FAIL — `No module named 'risk.sizer'`

- [ ] **Step 3: Implement sizer.py**

Create `risk/sizer.py`:
```python
"""Orchestrator: combine Kelly, limits, and circuit breaker into a sizing decision.

Usage:
    result = compute_size(model_prob, market_prob, confidence, price_cents,
                          bankroll_tracker, circuit_breaker, ...)
    if result.count > 0:
        place_order(ticker, result.side, price_cents, result.count)
"""

from dataclasses import dataclass
from risk.kelly import kelly_fraction
from risk.position_limits import check_limits
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker


# Default fractional Kelly multiplier (start conservative)
DEFAULT_FRACTIONAL_KELLY = 0.25


@dataclass
class SizeResult:
    side: str | None       # "yes", "no", or None
    count: int             # number of contracts
    dollar_amount: float   # total cost in dollars
    raw_kelly: float       # unadjusted Kelly fraction
    adjusted_kelly: float  # after fractional + confidence
    limit_reason: str      # which limit was binding


MAX_CONTRACTS_PER_EVENT = 2  # spec: max 2 contracts per city/day/type


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
    fractional_kelly: float = DEFAULT_FRACTIONAL_KELLY,
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

    # Step 1: Kelly fraction
    kelly = kelly_fraction(
        model_prob=model_prob,
        market_prob=market_prob,
        fractional=fractional_kelly,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_sizer.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add risk/sizer.py tests/test_sizer.py
git commit -m "feat: add sizer orchestrator combining Kelly, limits, and circuit breaker"
```

---

### Task 6: Wire sizer into kalshi/trader.py

**Files:**
- Modify: `kalshi/trader.py:130-167`
- Modify: `config.py`
- Modify: `tests/test_trader_integration.py`

- [ ] **Step 1: Add risk config to config.py**

Add to end of `config.py`:
```python
# Risk / Kelly sizing (Phase 2)
FRACTIONAL_KELLY = 0.25       # start at 25% Kelly
KELLY_MAX_FRACTION = 0.03    # max 3% of bankroll per order
DRAWDOWN_THRESHOLD = 0.15    # 15% drawdown triggers circuit breaker
DAILY_STOP_PCT = 0.05        # -5% daily P&L stops trading
CIRCUIT_BREAKER_COOLDOWN_HOURS = 48
```

- [ ] **Step 2: Update execute_kalshi_signal to use sizer**

Replace the sizing block in `kalshi/trader.py` (lines 130-167) with:

```python
def execute_kalshi_signal(market: dict, city: str, model_prob: float, market_prob: float, edge: float, direction: str, confidence: float = 0):
    """Execute a trade on Kalshi based on a signal."""
    global _scan_spent
    from config import PAPER_MODE, MAX_SCAN_BUDGET, FRACTIONAL_KELLY
    from alerts.telegram_alert import send_signal_alert
    from risk.sizer import compute_size
    from risk.bankroll import BankrollTracker
    from risk.circuit_breaker import CircuitBreaker

    ticker = market.get("ticker", "")
    if not ticker:
        print("No ticker in market data — skipping")
        return

    # Determine side: positive edge = buy YES, negative = buy NO
    side = "yes" if edge > 0 else "no"

    # Price in cents
    if edge > 0:
        price_cents = int((market_prob + edge * 0.3) * 100)
    else:
        price_cents = int((1 - market_prob + abs(edge) * 0.3) * 100)
    price_cents = max(1, min(99, price_cents))

    # Kelly-based position sizing
    size_result = compute_size(
        model_prob=model_prob,
        market_prob=market_prob,
        confidence=confidence,
        price_cents=price_cents,
        bankroll_tracker=_bankroll_tracker,
        circuit_breaker=_circuit_breaker,
        scan_spent=_scan_spent,
        fractional_kelly=FRACTIONAL_KELLY,
    )

    if size_result.count == 0:
        print(f"\n  SKIP {ticker} — {size_result.limit_reason}")
        return

    count = size_result.count
    order_cost = size_result.dollar_amount

    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    print(f"\n  [{mode_label}] {side.upper()} {count} contracts @ {price_cents}¢ (${order_cost:.2f})")
    print(f"  Ticker: {ticker} | Edge: {edge:+.1%} | Kelly: {size_result.raw_kelly:.1%} → {size_result.adjusted_kelly:.1%}")
    print(f"  Scan budget: ${_scan_spent:.2f} spent | {size_result.limit_reason}")

    if PAPER_MODE:
        print(f"  PAPER MODE — no order sent.")
        _scan_spent += order_cost
        return
```

The existing live-trading `try/except` block (lines 173-199) stays as-is, but add this line after `_scan_spent += order_cost` (line 177):
```python
        _bankroll_tracker.record_daily_pnl(-order_cost)  # Track spend for daily stop
```

- [ ] **Step 3: Add module-level bankroll and circuit breaker instances**

Add after `_scan_spent = 0.0` (line 121) in `kalshi/trader.py`:
```python
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker

_bankroll_tracker = BankrollTracker(initial_bankroll=500.0)
_circuit_breaker = CircuitBreaker()
```

And update `reset_scan_budget` to also refresh bankroll:
```python
def reset_scan_budget():
    """Reset per-scan spending tracker. Call at start of each scan."""
    global _scan_spent
    _scan_spent = 0.0
    # Try to refresh bankroll from API (non-fatal if offline)
    try:
        bal = get_balance()
        _bankroll_tracker.update_from_api(
            balance_cents=bal.get("balance", 0),
            portfolio_value_cents=bal.get("portfolio_value", 0),
        )
    except Exception:
        pass  # Use last known or initial bankroll
```

- [ ] **Step 4: Update test_trader_integration.py**

Replace `tests/test_trader_integration.py`:
```python
import pytest
from unittest.mock import patch, MagicMock


def test_execute_kalshi_signal_records_fill(tmp_path):
    """After a successful order, the fill should be recorded in trades.db."""
    db_path = str(tmp_path / "trades.db")

    mock_response = {"order": {"order_id": "test_ord_456", "status": "resting"}}

    with patch("kalshi.trader.place_order", return_value=mock_response), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.FRACTIONAL_KELLY", 0.25), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db, get_all_trades
        init_trades_db(db_path)

        from kalshi.trader import execute_kalshi_signal, reset_scan_budget, _bankroll_tracker
        _bankroll_tracker._cash = 1000.0  # Set known bankroll
        reset_scan_budget.__wrapped__ = reset_scan_budget  # avoid API call in test

        import kalshi.trader as trader_mod
        trader_mod._scan_spent = 0.0

        market = {"ticker": "KXHIGHNY-26MAR12-B65", "title": "NYC High Temp"}
        execute_kalshi_signal(market, "nyc", 0.70, 0.55, 0.15, "BUY YES", confidence=80)

        trades = get_all_trades(db_path)
        assert len(trades) == 1
        assert trades[0]["order_id"] == "test_ord_456"
        assert trades[0]["ticker"] == "KXHIGHNY-26MAR12-B65"
        assert trades[0]["side"] == "yes"


def test_execute_kalshi_signal_kelly_sizing(tmp_path):
    """Kelly sizing should produce reasonable contract counts."""
    from risk.bankroll import BankrollTracker
    from risk.circuit_breaker import CircuitBreaker
    from risk.sizer import compute_size

    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()

    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
        fractional_kelly=0.25,
    )

    # Should size based on Kelly, not flat $2
    assert result.count > 0
    assert result.side == "yes"
    # At 0.25x Kelly with 85% conf: ~7% * $1000 = ~$70.80 → capped at 3% = $30
    assert result.dollar_amount <= 30.0
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_trader_integration.py tests/test_sizer.py tests/test_kelly.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add config.py kalshi/trader.py tests/test_trader_integration.py
git commit -m "feat: wire Kelly sizer into live trader, replacing fixed sizing"
```

---

## Chunk 4: Walk-Forward Kelly Mode + Final Integration

### Task 7: Add Kelly mode to walk-forward simulator

**Files:**
- Modify: `backtesting/walk_forward.py`
- Modify: `tests/test_walk_forward.py`

- [ ] **Step 1: Add Kelly mode test**

Add to `tests/test_walk_forward.py`:
```python
def test_walk_forward_kelly_mode(signals_csv):
    """Kelly mode should produce different results than fixed-fraction."""
    results_fixed = walk_forward_simulate(
        signals_csv=signals_csv,
        edge_threshold=0.07,
        initial_bankroll=1000.0,
        bet_fraction=0.02,
    )
    results_kelly = walk_forward_simulate(
        signals_csv=signals_csv,
        edge_threshold=0.07,
        initial_bankroll=1000.0,
        kelly_mode=True,
        fractional_kelly=0.25,
    )
    # Both should trade the same signals, but sizing differs
    assert results_kelly["signals_traded"] == results_fixed["signals_traded"]
    # Returns will differ because sizing differs
    assert results_kelly["total_return"] != results_fixed["total_return"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_walk_forward.py::test_walk_forward_kelly_mode -v`
Expected: FAIL — `unexpected keyword argument 'kelly_mode'`

- [ ] **Step 3: Update walk_forward.py to support Kelly mode**

Add `kelly_mode` and `fractional_kelly` parameters to `walk_forward_simulate`. When `kelly_mode=True`, use `kelly_fraction()` instead of fixed `bet_fraction`:

In `backtesting/walk_forward.py`, update the function signature:
```python
def walk_forward_simulate(
    signals_csv: str = "logs/signals.csv",
    edge_threshold: float = 0.105,
    confidence_threshold: float = 70.0,
    initial_bankroll: float = 1000.0,
    bet_fraction: float = 0.02,
    max_bet_pct: float = 0.03,
    kelly_mode: bool = False,
    fractional_kelly: float = 0.25,
) -> dict:
```

And update the position sizing block inside the loop:
```python
            if kelly_mode:
                from risk.kelly import kelly_fraction as kf
                confidence = sig.get("confidence", 80.0)
                if pd.isna(confidence):
                    confidence = 80.0
                kelly = kf(
                    model_prob=model_prob,
                    market_prob=market_prob,
                    fractional=fractional_kelly,
                    confidence=confidence,
                )
                if kelly["side"] is None:
                    continue
                bet_size = min(bankroll * kelly["fraction"], bankroll * max_bet_pct)
            else:
                bet_size = min(bankroll * bet_fraction, bankroll * max_bet_pct)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/test_walk_forward.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtesting/walk_forward.py tests/test_walk_forward.py
git commit -m "feat: add Kelly mode to walk-forward simulator for sizing comparison"
```

---

### Task 8: Update CLI to expose Kelly options

**Files:**
- Modify: `backtesting/__main__.py`

- [ ] **Step 1: Add Kelly arguments to CLI**

Add to the argparse section in `backtesting/__main__.py`:
```python
    parser.add_argument("--kelly", action="store_true", help="Use Kelly sizing in walk-forward")
    parser.add_argument("--fractional-kelly", type=float, default=0.25, help="Fractional Kelly multiplier (default 0.25)")
```

And update the walk-forward call:
```python
        wf = walk_forward_simulate(
            signals_csv=args.signals,
            edge_threshold=args.edge_threshold,
            initial_bankroll=args.bankroll,
            kelly_mode=args.kelly,
            fractional_kelly=args.fractional_kelly,
        )
```

Add a label in the output:
```python
        sizing_label = f"Kelly {args.fractional_kelly:.0%}x" if args.kelly else "Fixed 2%"
        console.print(f"Sizing mode: {sizing_label}")
```

- [ ] **Step 2: Test CLI**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m backtesting --walk-forward --kelly --fractional-kelly 0.25`
Expected: Walk-forward results with Kelly sizing label

- [ ] **Step 3: Commit**

```bash
git add backtesting/__main__.py
git commit -m "feat: add --kelly and --fractional-kelly CLI options for walk-forward"
```

---

### Task 9: Final integration test

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (35+ tests)

- [ ] **Step 2: Run walk-forward comparison (fixed vs Kelly)**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -m backtesting --walk-forward --edge-threshold 0.105`
Then: `python -m backtesting --walk-forward --kelly --edge-threshold 0.105`
Expected: Different sizing results between the two modes

- [ ] **Step 3: Verify import chain works**

Run: `cd /Users/edede/Projects/polymarket-weather-bot && python -c "from risk.sizer import compute_size; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: Phase 2 complete — Kelly sizing with circuit breaker and layered limits"
```

---

## Spec Deviations & Notes

**Bankroll API integration:** The spec says `risk/bankroll.py` tracks Kalshi account balance via API. This plan implements the `BankrollTracker` class and calls `get_balance()` in `reset_scan_budget()`, but does not add mark-to-market of individual positions (which would require iterating positions and looking up current prices). The `portfolio_value` field from the Kalshi balance API is used as the positions value.

**Per-market limit (2 contracts per event):** Implemented in `sizer.py` as `MAX_CONTRACTS_PER_EVENT = 2`. The `event_contracts` parameter must be passed by the caller (scanner.py tracks this via `city_date_positions`). Combined with the existing anti-correlation logic in `scanner.py` and the per-city/day dollar limit.

**Daily stop vs drawdown distinction:** The spec distinguishes: drawdown = halve sizes for 48h, daily stop = full stop until next day. The `CircuitBreaker` implementation returns `size_multiplier() == 0.0` for daily stop and `0.5` for drawdown. Both conditions are evaluated in `compute_size()` via `is_tripped()`.

**Migration path:** The spec says "Start at 0.25x Kelly, graduate to 0.5x after 30+ days positive Brier score." The config constant `FRACTIONAL_KELLY = 0.25` implements the starting point. Graduation is manual (change the config). Automated graduation based on Brier score could be added later but is not in scope for this phase.

**Daily P&L tracking:** `BankrollTracker.record_daily_pnl()` is called in `execute_kalshi_signal()` after each successful fill. The `compute_size()` function reads `bankroll_tracker.daily_pnl()` to check the daily stop condition.
