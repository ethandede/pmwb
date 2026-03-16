# Stale Order Management + Fee Dashboard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel stale maker orders automatically and surface fee data throughout the dashboard.

**Architecture:** Two independent features sharing a single deploy. Feature 1 adds a cancel_order method to the Kalshi adapter plus a new cleanup module called each daemon cycle. Feature 2 adds strategy/fee columns to trades.db, new API endpoints, and frontend charts/columns. Both converge on the existing pipeline: cleanup runs in the daemon loop, fee recording happens in execute_trade.

**Tech Stack:** Python 3.10, FastAPI, SQLite, Plotly.js, Kalshi REST API

**Spec:** `docs/superpowers/specs/2026-03-16-stale-orders-and-fee-dashboard-design.md`

---

## Chunk 1: Kalshi Cancel Order + Stale Cleanup

### Task 1: Add _delete and cancel_order to KalshiExchange

**Files:**
- Modify: `exchanges/kalshi.py:63-98`
- Test: `tests/test_exchanges.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_exchanges.py`:

```python
def test_kalshi_cancel_order():
    """cancel_order sends DELETE with order_id in path."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_delete') as mock_delete:
        mock_delete.return_value = {"order": {"order_id": "abc123", "status": "cancelled"}}
        result = exchange.cancel_order("abc123")
    mock_delete.assert_called_once_with("/trade-api/v2/portfolio/orders/abc123")
    assert result["order"]["status"] == "cancelled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_exchanges.py::test_kalshi_cancel_order -v`
Expected: FAIL with `AttributeError: 'KalshiExchange' object has no attribute '_delete'`

- [ ] **Step 3: Write _delete and cancel_order**

In `exchanges/kalshi.py`, after the `_get` method (line 63), add:

```python
    def _delete(self, path: str) -> dict:
        headers = self._sign_request("DELETE", path)
        resp = requests.delete(f"{_BASE_URL}{path}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
```

After `sell_order` (line 138), add:

```python
    def cancel_order(self, order_id: str) -> dict:
        """Cancel a resting order by ID."""
        return self._delete(f"/trade-api/v2/portfolio/orders/{order_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_exchanges.py -v`
Expected: All tests PASS including new `test_kalshi_cancel_order`

- [ ] **Step 5: Commit**

```bash
git add exchanges/kalshi.py tests/test_exchanges.py
git commit -m "feat: add cancel_order to KalshiExchange adapter"
```

---

### Task 2: Create stale order cleanup module

**Files:**
- Create: `kalshi/order_cleanup.py`
- Create: `tests/test_order_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_order_cleanup.py`:

```python
"""Tests for stale maker order cleanup."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from kalshi.order_cleanup import cleanup_stale_orders


def _make_order(order_id, ticker, created_time, action="buy"):
    """Helper to build a mock resting order dict."""
    return {
        "order_id": order_id,
        "ticker": ticker,
        "action": action,
        "side": "yes",
        "remaining_count_fp": "5",
        "created_time": created_time.isoformat(),
    }


class TestStaleOrderCleanup:
    def test_cancels_old_order(self):
        """Orders older than max_age_hours should be cancelled."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=7)
        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("old-1", "KXHIGHNY-26MAR20-T58", old_time),
        ]
        exchange.cancel_order.return_value = {"order": {"status": "cancelled"}}

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6)

        assert len(result) == 1
        assert result[0]["order_id"] == "old-1"
        assert result[0]["reason"] == "age"
        exchange.cancel_order.assert_called_once_with("old-1")

    def test_cancels_near_close_order(self):
        """Orders for events closing within close_proximity_hours should be cancelled."""
        now = datetime.now(timezone.utc)
        # Order is only 1h old but event is tomorrow — use a ticker date for tomorrow
        tomorrow = now + timedelta(hours=1)
        # Build a ticker whose date is today (event closes soon)
        today_str = now.strftime("%y%b%d").upper()  # e.g. "26MAR16"
        ticker = f"KXHIGHNY-{today_str}-T58"

        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("close-1", ticker, now - timedelta(hours=1)),
        ]
        exchange.cancel_order.return_value = {"order": {"status": "cancelled"}}

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6, close_proximity_hours=24)

        assert len(result) == 1
        assert result[0]["reason"] == "event_close"

    def test_keeps_fresh_order(self):
        """Recent order for a distant event should not be cancelled."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=5)
        future_str = future.strftime("%y%b%d").upper()
        ticker = f"KXHIGHNY-{future_str}-T58"

        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("fresh-1", ticker, now - timedelta(hours=2)),
        ]

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6)

        assert len(result) == 0
        exchange.cancel_order.assert_not_called()

    def test_cancel_failure_continues(self):
        """If one cancel fails, continue processing remaining orders."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=7)
        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("fail-1", "KXHIGHNY-26MAR20-T58", old_time),
            _make_order("ok-1", "KXHIGHCHI-26MAR20-T58", old_time),
        ]
        exchange.cancel_order.side_effect = [Exception("API error"), {"order": {"status": "cancelled"}}]

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6)

        # Should still return the successful cancellation
        assert len(result) == 1
        assert result[0]["order_id"] == "ok-1"
        assert exchange.cancel_order.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_order_cleanup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kalshi.order_cleanup'`

- [ ] **Step 3: Implement the cleanup module**

Create `kalshi/order_cleanup.py`:

```python
"""Stale maker order cleanup.

Cancels resting orders that are too old or too close to event settlement.
Runs each daemon cycle — most cycles cancel nothing.
"""

import re
from datetime import datetime, timezone, timedelta

# Ticker date pattern: "26MAR16" → 2026-03-16
_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})-")
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_event_date(ticker: str) -> datetime | None:
    """Extract event date from ticker like KXHIGHNY-26MAR16-T58."""
    m = _DATE_RE.search(ticker)
    if not m:
        return None
    year = 2000 + int(m.group(1))
    month = _MONTHS.get(m.group(2))
    day = int(m.group(3))
    if not month:
        return None
    try:
        # Event settles morning after the date — use 14:00 UTC as proxy close
        return datetime(year, month, day, 14, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


def _send_cleanup_alert(cancelled: list[dict]):
    """Send Telegram alert summarizing cancelled orders."""
    if not cancelled:
        return
    try:
        from alerts.telegram_alert import send_alert
        lines = [f"  {c['ticker']} — {c['reason']} ({c['age_hours']:.1f}h old)" for c in cancelled]
        send_alert(
            f"Cancelled {len(cancelled)} stale order(s)",
            "\n".join(lines),
            dedup_key="stale_order_cleanup",
        )
    except Exception:
        pass


def cleanup_stale_orders(
    exchange,
    max_age_hours: float = 6,
    close_proximity_hours: float = 2,
) -> list[dict]:
    """Cancel resting orders that are stale by age or event proximity.

    Returns list of successfully cancelled order summaries.
    """
    now = datetime.now(timezone.utc)
    age_cutoff = now - timedelta(hours=max_age_hours)

    resting = exchange.get_orders(status="resting", limit=200)
    cancelled = []

    for order in resting:
        order_id = order.get("order_id", "")
        ticker = order.get("ticker", "")
        created_str = order.get("created_time", "")

        if not order_id or not created_str:
            continue

        # Parse created time
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        age_hours = (now - created).total_seconds() / 3600
        reason = None

        # Check age
        if created < age_cutoff:
            reason = "age"

        # Check event proximity
        if not reason:
            event_date = _parse_event_date(ticker)
            if event_date:
                hours_to_close = (event_date - now).total_seconds() / 3600
                if hours_to_close < close_proximity_hours:
                    reason = "event_close"

        if not reason:
            continue

        # Cancel
        try:
            exchange.cancel_order(order_id)
            cancelled.append({
                "order_id": order_id,
                "ticker": ticker,
                "reason": reason,
                "age_hours": round(age_hours, 1),
            })
            print(f"  Cancelled stale order {order_id} ({ticker}): {reason}, {age_hours:.1f}h old")
        except Exception as e:
            print(f"  Failed to cancel {order_id}: {e}")

    _send_cleanup_alert(cancelled)
    return cancelled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_cleanup.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add kalshi/order_cleanup.py tests/test_order_cleanup.py
git commit -m "feat: stale maker order cleanup module"
```

---

### Task 3: Integrate cleanup into daemon loop

**Files:**
- Modify: `daemon.py:57-59`

- [ ] **Step 1: Add Phase 1.5 to daemon**

In `daemon.py`, after the ERCOT manager block (line 57) and before the Phase 2 comment (line 59), insert:

```python

    # --- Phase 1.5: Stale order cleanup ---
    kalshi = exchanges.get("kalshi")
    if kalshi:
        try:
            from kalshi.order_cleanup import cleanup_stale_orders
            cancelled = cleanup_stale_orders(kalshi)
            if cancelled:
                console.print(f"  Cancelled {len(cancelled)} stale order(s)")
        except Exception as e:
            console.print(f"  [red]Order cleanup error: {e}[/red]")

```

Note: `kalshi` is also used later in Phase 4. The variable assignment here is fine — it's a simple dict lookup, not a new instantiation.

- [ ] **Step 2: Verify daemon still starts**

Run: `python -c "from daemon import run_cycle; print('import OK')"`
Expected: `import OK`

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass (cleanup module is imported lazily, no import side effects)

- [ ] **Step 4: Commit**

```bash
git add daemon.py
git commit -m "feat: add stale order cleanup phase to daemon loop"
```

---

## Chunk 2: Fee Tracking in trades.db

### Task 4: Add strategy/fee columns to trades.db

**Files:**
- Modify: `kalshi/fill_tracker.py`
- Test: `tests/test_pricing.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pricing.py`:

```python
import os
import tempfile
from kalshi.fill_tracker import init_trades_db, record_fill
import sqlite3


class TestFeeRecording:
    def test_record_fill_with_strategy_and_fee(self):
        """record_fill should store strategy and fee columns."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "trades.db")
            init_trades_db(db)
            record_fill(
                db_path=db,
                order_id="test-1",
                ticker="KXHIGHNY-26MAR16-T58",
                side="buy_yes",
                limit_price=46,
                fill_price=46,
                fill_qty=5,
                fill_time="2026-03-16T10:00:00Z",
                city="new_york",
                strategy="maker",
                fee=0.0,
            )
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM trades WHERE order_id='test-1'").fetchone()
            conn.close()
            assert row["strategy"] == "maker"
            assert row["fee"] == 0.0

    def test_record_fill_taker_fee(self):
        """Taker trades should record non-zero fee."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "trades.db")
            init_trades_db(db)
            record_fill(
                db_path=db,
                order_id="test-2",
                ticker="KXHIGHNY-26MAR16-T58",
                side="buy_yes",
                limit_price=55,
                fill_price=55,
                fill_qty=5,
                fill_time="2026-03-16T10:00:00Z",
                city="new_york",
                strategy="taker",
                fee=0.175,
            )
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM trades WHERE order_id='test-2'").fetchone()
            conn.close()
            assert row["strategy"] == "taker"
            assert row["fee"] == pytest.approx(0.175)

    def test_upsert_preserves_strategy(self):
        """ON CONFLICT should preserve strategy/fee from first insert."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "trades.db")
            init_trades_db(db)
            # First: execute_trade records with strategy
            record_fill(db, "test-3", "T1", "buy_yes", 46, 46, 5,
                         "2026-03-16T10:00:00Z", strategy="maker", fee=0.0)
            # Second: poller updates without strategy (larger fill)
            record_fill(db, "test-3", "T1", "buy_yes", 46, 46, 8,
                         "2026-03-16T10:01:00Z")
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM trades WHERE order_id='test-3'").fetchone()
            conn.close()
            assert row["strategy"] == "maker"
            assert row["fee"] == 0.0
            assert row["fill_qty"] == 8  # updated by poller
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pricing.py::TestFeeRecording -v`
Expected: FAIL — `record_fill` doesn't accept strategy/fee params

- [ ] **Step 3: Update fill_tracker.py**

In `kalshi/fill_tracker.py`, update `init_trades_db` to add migration after the CREATE TABLE:

```python
def init_trades_db(db_path: str = "data/trades.db"):
    """Create the trades table if it doesn't exist."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            ticker TEXT NOT NULL,
            city TEXT,
            side TEXT NOT NULL,
            limit_price INTEGER,
            fill_price INTEGER,
            fill_qty INTEGER,
            fill_time TEXT,
            settlement_outcome TEXT,
            pnl REAL,
            strategy TEXT,
            fee REAL DEFAULT 0.0
        )
    """)
    # Migrate existing tables missing new columns
    for col, typedef in [("strategy", "TEXT"), ("fee", "REAL DEFAULT 0.0")]:
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()
```

Update `record_fill` signature and SQL:

```python
def record_fill(
    db_path: str,
    order_id: str,
    ticker: str,
    side: str,
    limit_price: int,
    fill_price: int,
    fill_qty: int,
    fill_time: str,
    city: str = "",
    settlement_outcome: Optional[str] = None,
    pnl: Optional[float] = None,
    strategy: Optional[str] = None,
    fee: float = 0.0,
):
    """Record a fill. Updates only fill data if order already exists.

    On conflict (same order_id), only fill_price, fill_qty, and fill_time are
    updated — side, city, strategy, and fee are preserved from the original
    insert so the poller cannot overwrite the canonical values set by
    execute_trade.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO trades
           (order_id, ticker, city, side, limit_price, fill_price, fill_qty,
            fill_time, settlement_outcome, pnl, strategy, fee)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(order_id) DO UPDATE SET
               fill_price = excluded.fill_price,
               fill_qty = excluded.fill_qty,
               fill_time = excluded.fill_time,
               strategy = COALESCE(excluded.strategy, trades.strategy),
               fee = CASE WHEN excluded.fee > 0 THEN excluded.fee ELSE trades.fee END
           WHERE excluded.fill_qty > trades.fill_qty""",
        (order_id, ticker, city, side, limit_price, fill_price, fill_qty,
         fill_time, settlement_outcome, pnl, strategy, fee),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pricing.py -v`
Expected: All tests PASS including new `TestFeeRecording` class

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass (existing callers use defaults for new params)

- [ ] **Step 6: Commit**

```bash
git add kalshi/fill_tracker.py tests/test_pricing.py
git commit -m "feat: add strategy/fee columns to trades.db with migration"
```

---

### Task 5: Record strategy/fee in execute_trade and position_manager

**Files:**
- Modify: `pipeline/stages.py:374-384,411-421`
- Modify: `kalshi/position_manager.py:420-430`

- [ ] **Step 1: Update paper trade recording in stages.py**

In `pipeline/stages.py`, update the paper trade `record_fill` call (lines 374-384) to add strategy and fee:

```python
            record_fill(
                db_path="data/trades.db",
                order_id=f"paper-{signal.ticker}-{int(datetime.now(timezone.utc).timestamp())}",
                ticker=signal.ticker,
                side=f"buy_{size.side or signal.side}",
                limit_price=price_cents,
                fill_price=price_cents,
                fill_qty=size.count,
                fill_time=datetime.now(timezone.utc).isoformat(),
                city=signal.city,
                strategy=strategy,
                fee=fee if config.exchange == "kalshi" else 0.0,
            )
```

Note: `strategy` and `fee` variables are already computed earlier in the function (lines 341-358). For non-Kalshi exchanges, fee defaults to 0.0.

- [ ] **Step 2: Update live trade recording in stages.py**

Update the live trade `record_fill` call (lines 411-421):

```python
    record_fill(
        db_path="data/trades.db",
        order_id=order_id,
        ticker=signal.ticker,
        side=f"buy_{size.side or signal.side}",
        limit_price=price_cents,
        fill_price=actual_price if fill_qty > 0 else 0,
        fill_qty=fill_qty,
        fill_time=datetime.now(timezone.utc).isoformat(),
        city=signal.city,
        strategy=strategy,
        fee=fee if config.exchange == "kalshi" else 0.0,
    )
```

- [ ] **Step 3: Update position_manager sell recording**

In `kalshi/position_manager.py`, update the sell `record_fill` call (lines 420-430):

```python
                record_fill(
                    db_path="data/trades.db",
                    order_id=order_id,
                    ticker=ticker,
                    side=f"sell_{side}",
                    limit_price=price,
                    fill_price=price,
                    fill_qty=qty,
                    fill_time=datetime.now(timezone.utc).isoformat(),
                    city=result.get("city", ""),
                    strategy="maker",
                    fee=0.0,
                )
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages.py kalshi/position_manager.py
git commit -m "feat: record strategy and fee on all trade fills"
```

---

## Chunk 3: Dashboard API Endpoints

### Task 6: Add fee data to activity and settled endpoints

**Files:**
- Modify: `dashboard/api.py:455-506` (activity endpoint)
- Modify: `dashboard/api.py:552-589` (settled endpoint)

- [ ] **Step 1: Update /api/activity**

In `dashboard/api.py`, update the SQL query in `get_activity` (line 462) to include strategy and fee:

```python
    rows = conn.execute(
        """SELECT ticker, city, side, fill_price, fill_qty, fill_time,
                  settlement_outcome, pnl, strategy, fee
           FROM trades WHERE fill_qty > 0
           ORDER BY fill_time DESC LIMIT ?""",
        (limit,),
    ).fetchall()
```

Add strategy and fee to the result dict (after the `"confidence"` line, around line 504):

```python
            "strategy": r["strategy"],
            "fee": round(r["fee"], 4) if r["fee"] else 0.0,
```

- [ ] **Step 2: Update /api/settled**

In `dashboard/api.py`, update the settled SQL (line 571) to include strategy and fee:

```python
    rows = conn.execute("""
        SELECT ticker, city, side, fill_price, fill_qty, fill_time,
               settlement_outcome, pnl, strategy, fee
        FROM trades WHERE settlement_outcome IN ('win', 'loss') AND fill_qty > 0
        ORDER BY fill_time DESC LIMIT ?
    """, (limit,)).fetchall()
```

Add to each trade dict (around line 586):

```python
            "strategy": r["strategy"],
            "fee": round(r["fee"], 4) if r["fee"] else 0.0,
```

Add fee aggregates to the summary query. After the existing summary block (line 567), add:

```python
    fee_row = conn.execute("""
        SELECT COALESCE(SUM(fee), 0) as total_fees,
               SUM(CASE WHEN strategy='maker' THEN 1 ELSE 0 END) as maker_count,
               SUM(CASE WHEN strategy='taker' THEN 1 ELSE 0 END) as taker_count
        FROM trades WHERE strategy IS NOT NULL AND settlement_outcome IN ('win', 'loss')
    """).fetchone()
    summary["total_fees"] = round(fee_row["total_fees"], 4)
    summary["maker_count"] = fee_row["maker_count"]
    summary["taker_count"] = fee_row["taker_count"]
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add dashboard/api.py
git commit -m "feat: add strategy/fee data to activity and settled endpoints"
```

---

### Task 7: Add /api/performance/fees and /api/fees/summary endpoints

**Files:**
- Modify: `dashboard/api.py` (add after settled endpoint, ~line 590)

- [ ] **Step 1: Add /api/performance/fees endpoint**

Add after the `get_settled` function in `dashboard/api.py`:

```python
@app.get("/api/performance/fees")
async def get_fee_chart():
    """Cumulative P&L vs fees time series for chart."""
    from dashboard.equity_db import EQUITY_DB
    equity_path = Path(EQUITY_DB)
    if not equity_path.exists():
        return []
    conn = sqlite3.connect(str(equity_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date, realized_pnl, fees_paid FROM equity_snapshots ORDER BY date"
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        # realized_pnl and fees_paid are already cumulative lifetime totals
        # from Kalshi settled positions (recorded daily in daemon Phase 4)
        result.append({
            "date": r["date"],
            "cumulative_pnl": round(r["realized_pnl"], 2),
            "cumulative_fees": round(r["fees_paid"], 2),
        })
    return result


@app.get("/api/fees/summary")
async def get_fee_summary():
    """Fee summary: total fees, maker/taker counts, savings estimate."""
    from kalshi.pricing import kalshi_fee
    from dashboard.equity_db import EQUITY_DB

    # Get maker/taker counts and savings estimate from trades.db
    maker_count = 0
    taker_count = 0
    estimated_taker_fees_for_makers = 0.0

    if TRADES_DB.exists():
        conn = sqlite3.connect(str(TRADES_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT strategy, fill_price, fill_qty FROM trades WHERE strategy IS NOT NULL"
        ).fetchall()
        conn.close()

        for r in rows:
            if r["strategy"] == "maker":
                maker_count += 1
                if r["fill_price"] and r["fill_qty"]:
                    estimated_taker_fees_for_makers += kalshi_fee(
                        r["fill_price"], r["fill_qty"], is_taker=True
                    )
            elif r["strategy"] == "taker":
                taker_count += 1

    # Get total fees from equity_db (recorded daily, avoids live API call)
    total_fees = 0.0
    equity_path = Path(EQUITY_DB)
    if equity_path.exists():
        conn = sqlite3.connect(str(equity_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT fees_paid FROM equity_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            total_fees = row["fees_paid"]

    return {
        "total_fees_paid": round(total_fees, 2),
        "maker_trades": maker_count,
        "taker_trades": taker_count,
        "fee_savings": round(estimated_taker_fees_for_makers, 2),
    }
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add dashboard/api.py
git commit -m "feat: add /api/performance/fees and /api/fees/summary endpoints"
```

---

## Chunk 4: Dashboard Frontend

### Task 8: Add fee summary card and P&L vs Fees chart

**Files:**
- Modify: `dashboard/static/index.html:202-210`
- Modify: `dashboard/static/js/performance.js`
- Modify: `dashboard/static/js/app.js`

- [ ] **Step 1: Add HTML containers**

In `dashboard/static/index.html`, in the History view (line 202-210), add a fee section before the settled section:

```html
            <!-- View: History -->
            <div class="we-view" id="view-history">
                <section id="fee-section">
                    <h2>Fee Analysis</h2>
                    <p class="section-desc">Maker strategy savings and cumulative fee impact on P&L.</p>
                    <div id="fee-summary"></div>
                    <div class="chart-card" style="margin-top: 1rem;">
                        <div class="chart-label">Cumulative P&L vs Fees</div>
                        <div id="fee-chart" style="height: 250px;"></div>
                    </div>
                </section>
                <hr class="we-divider">
                <section id="settled-section">
                    <h2>Settled Trades</h2>
                    <p class="section-desc">Completed trades with final outcomes. Win = contract settled in your favor.</p>
                    <div id="settled-summary"></div>
                    <div id="settled-table"></div>
                </section>
            </div>
```

Bump the cache-busting version on the script tag (line 217):

```html
    <script type="module" src="/static/js/app.js?v=28"></script>
```

- [ ] **Step 2: Add fee rendering to performance.js**

Add to `dashboard/static/js/performance.js`, before the `export` line:

```javascript
function renderFeeSummary(data) {
    const el = document.getElementById('fee-summary');
    if (!el || !data) return;

    const total = data.maker_trades + data.taker_trades;
    const makerPct = total > 0 ? (data.maker_trades / total * 100).toFixed(0) : '0';

    el.innerHTML = `
        <div class="metric-row">
            <div class="metric-card metric-negative">
                <div class="metric-label">Total Fees</div>
                <div class="metric-value mono">$${data.total_fees_paid.toFixed(2)}</div>
            </div>
            <div class="metric-card metric-positive">
                <div class="metric-label">Fee Savings</div>
                <div class="metric-value mono">$${data.fee_savings.toFixed(2)}</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">Maker Rate</div>
                <div class="metric-value mono">${data.maker_trades}/${total} (${makerPct}%)</div>
            </div>
        </div>`;
}

function renderFeeChart(data) {
    const el = document.getElementById('fee-chart');
    if (!el || !data || data.length === 0) return;

    const dates = data.map(d => d.date);
    const pnl = data.map(d => d.cumulative_pnl);
    const fees = data.map(d => d.cumulative_fees);

    const tracePnl = {
        x: dates, y: pnl, type: 'scatter', mode: 'lines',
        name: 'Realized P&L',
        line: { color: '#10b981', width: 2 },
    };
    const traceFees = {
        x: dates, y: fees, type: 'scatter', mode: 'lines',
        name: 'Cumulative Fees',
        line: { color: '#ef4444', width: 2, dash: 'dot' },
    };

    const layout = {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
        margin: { l: 50, r: 20, t: 10, b: 40 },
        xaxis: { showgrid: false, color: 'rgba(255,255,255,0.4)' },
        yaxis: {
            showgrid: true, gridcolor: 'rgba(255,255,255,0.08)',
            color: 'rgba(255,255,255,0.4)',
            tickprefix: '$',
        },
        legend: {
            orientation: 'h', y: -0.15,
            font: { size: 11 },
        },
        showlegend: true,
    };

    Plotly.newPlot(el, [tracePnl, traceFees], layout, PLOTLY_CONFIG);
}
```

Update the export to include the new functions:

```javascript
export { renderTriptych, renderFeeSummary, renderFeeChart };
```

- [ ] **Step 3: Wire up in app.js**

In `dashboard/static/js/app.js`, update the import (line 4):

```javascript
import { renderTriptych, renderFeeSummary, renderFeeChart } from './performance.js?v=28';
```

Add the fee data fetches to `refreshAll()`. In the second `Promise.allSettled` block (line 47), add two entries:

```javascript
        loadSection('/api/fees/summary', renderFeeSummary, 'fee-summary'),
        loadSection('/api/performance/fees', renderFeeChart, 'fee-chart'),
```

Update all other import version strings from `?v=27` to `?v=28`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -x -q`
Expected: All tests pass (frontend changes don't affect Python tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/index.html dashboard/static/js/performance.js dashboard/static/js/app.js
git commit -m "feat: add fee summary card and P&L vs fees chart"
```

---

### Task 9: Add strategy/fee columns to activity and settled tables

**Files:**
- Modify: `dashboard/static/js/activity.js`
- Modify: `dashboard/static/js/settled.js`

- [ ] **Step 1: Update activity.js columns**

In `dashboard/static/js/activity.js`, add two entries to the `COLUMNS` array (after the `confidence` entry, line 16):

```javascript
    { key: 'strategy', label: 'Type',    num: false },
    { key: 'fee',      label: 'Fee',     num: true  },
```

In the `activityGrid` row rendering (around line 90), add the two new cells before the closing `</div>`:

After the confidence `<span>` (line 97), add:

```javascript
          <span data-label="Type">${t.strategy === 'maker' ? '<span class="val-positive">MAKER</span>' : t.strategy === 'taker' ? '<span class="val-amber">TAKER</span>' : '\u2014'}</span>
          <span class="num mono" data-label="Fee">${t.fee > 0 ? '$' + t.fee.toFixed(3) : '$0'}</span>
```

- [ ] **Step 2: Update settled.js columns**

In `dashboard/static/js/settled.js`, add two entries to the `COLUMNS` array (after the `outcome` entry, line 15):

```javascript
    { key: 'strategy', label: 'Type',   num: false },
    { key: 'fee',      label: 'Fee',    num: true  },
```

In the `settledGrid` row rendering (around line 95), add two cells before the P&L span:

After the outcome `<span>` (line 101), add:

```javascript
          <span>${t.strategy === 'maker' ? '<span class="val-positive">MAKER</span>' : t.strategy === 'taker' ? '<span class="val-amber">TAKER</span>' : '\u2014'}</span>
          <span class="num mono">${t.fee > 0 ? '$' + t.fee.toFixed(3) : '$0'}</span>
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/static/js/activity.js dashboard/static/js/settled.js
git commit -m "feat: add strategy/fee columns to activity and settled tables"
```

---

## Chunk 5: Verification + Deploy

### Task 10: Full verification and deploy

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 2: Verify cleanup module in isolation**

Run:
```bash
python -c "
from kalshi.order_cleanup import _parse_event_date
d = _parse_event_date('KXHIGHNY-26MAR16-T58')
print(f'Parsed: {d}')
assert d is not None
assert d.year == 2026
assert d.month == 3
assert d.day == 16
print('OK')
"
```

- [ ] **Step 3: Verify schema migration**

Run:
```bash
python -c "
import tempfile, os, sqlite3
from kalshi.fill_tracker import init_trades_db, record_fill
db = os.path.join(tempfile.mkdtemp(), 'test.db')
init_trades_db(db)
record_fill(db, 'x1', 'T1', 'buy_yes', 50, 50, 1, '2026-01-01', strategy='maker', fee=0.0)
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
r = dict(conn.execute('SELECT * FROM trades').fetchone())
assert r['strategy'] == 'maker'
assert r['fee'] == 0.0
print('Schema OK:', list(r.keys()))
"
```

- [ ] **Step 4: Deploy**

Run: `bash deploy.sh`
Expected: rsync + services restarted

- [ ] **Step 5: Verify dashboard loads fee data**

SSH to server and check:
```bash
ssh -i ~/.ssh/hetzner_ed25519 edede@5.78.146.1 "curl -s http://localhost:8501/api/fees/summary | python3 -m json.tool"
```
Expected: JSON with `total_fees_paid`, `maker_trades`, `taker_trades`, `fee_savings`

- [ ] **Step 6: Verify daemon logs show cleanup phase**

```bash
ssh -i ~/.ssh/hetzner_ed25519 edede@5.78.146.1 "sudo journalctl -u weather-daemon --no-pager -n 20 --since '5 minutes ago' | grep -i 'cancel\|cleanup\|stale\|Phase 1.5'"
```

- [ ] **Step 7: Commit all changes if any stragglers**

Run `git status` and commit anything uncommitted.
