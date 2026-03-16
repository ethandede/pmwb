# Stale Maker Order Management + Dashboard Fee Surfacing

**Date**: 2026-03-16
**Status**: Approved

## Context

The bot places maker (resting limit) orders to avoid Kalshi's 7% taker fee. However, there is no mechanism to cancel stale orders that never fill — they sit indefinitely, tying up margin and risking fills at stale prices near settlement. Additionally, fees are tracked in equity snapshots but never surfaced in the dashboard, making it hard to evaluate whether the maker strategy is working.

## Design

### 1. Cancel Order Method

**File**: `exchanges/kalshi.py`

Add `_delete(path: str) -> dict` private method, following the same signing pattern as `_get`:
```python
def _delete(self, path: str) -> dict:
    headers = self._sign_request("DELETE", path)
    resp = requests.delete(f"{_BASE_URL}{path}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()
```

Add `cancel_order(order_id: str) -> dict`:
```python
def cancel_order(self, order_id: str) -> dict:
    return self._delete(f"/trade-api/v2/portfolio/orders/{order_id}")
```

### 2. Stale Order Cleanup Module

**File**: `kalshi/order_cleanup.py` (new)

```python
def cleanup_stale_orders(exchange, max_age_hours=6, close_proximity_hours=2) -> list[dict]:
```

Logic:
1. Fetch all resting orders via `exchange.get_orders(status="resting", limit=200)`
2. For each order, cancel if either condition is met:
   - `created_time` is older than `max_age_hours` (default 6h)
   - Market event closes within `close_proximity_hours` (default 2h) — parsed from ticker date (primary method, using existing date parsing patterns in the codebase). No per-market API calls — ticker date parsing covers all Kalshi weather tickers.
3. Return list of cancelled order summaries: `{order_id, ticker, reason, age_hours}`
4. Send single Telegram alert summarizing all cancellations (if any)

### 3. Daemon Integration

**File**: `daemon.py`

New Phase 1.5 after both position managers (Kalshi + ERCOT), before Phase 2 pipeline scan:
```python
# --- Phase 1.5: Stale order cleanup ---
try:
    from kalshi.order_cleanup import cleanup_stale_orders
    cancelled = cleanup_stale_orders(kalshi)
    if cancelled:
        console.print(f"  Cancelled {len(cancelled)} stale orders")
except Exception as e:
    console.print(f"  [red]Order cleanup error: {e}[/red]")
```

Runs every cycle (5 min). Most cycles will cancel nothing.

### 4. Fee Columns in trades.db

**File**: `kalshi/fill_tracker.py`

Schema migration (runs in `init_trades_db()`):
```sql
ALTER TABLE trades ADD COLUMN strategy TEXT;
ALTER TABLE trades ADD COLUMN fee REAL DEFAULT 0.0;
```

Wrapped in try/except (columns may already exist). Old rows get NULL/0.0.

Update `record_fill()` signature:
```python
def record_fill(db_path, order_id, ticker, side, limit_price, fill_price,
                fill_qty, fill_time, city=None, strategy=None, fee=0.0):
```

Update ON CONFLICT clause to preserve strategy/fee when provided:
```sql
ON CONFLICT(order_id) DO UPDATE SET
    fill_price=excluded.fill_price,
    fill_qty=excluded.fill_qty,
    fill_time=excluded.fill_time,
    strategy=COALESCE(excluded.strategy, trades.strategy),
    fee=CASE WHEN excluded.fee > 0 THEN excluded.fee ELSE trades.fee END
```

This ensures trade-time strategy/fee values are not overwritten by later poller updates that lack them, and that if `execute_trade` records first, a subsequent poller call won't erase the data.

**Note**: Existing callers (`backfill_trades.py`, `update_fill_data()`) continue to work with defaults (NULL strategy, 0.0 fee). Historical data will lack strategy/fee information — acceptable since the feature is forward-looking.

### 5. Recording Fees at Trade Time

**File**: `pipeline/stages.py` (`execute_trade()`)

After computing strategy and fee (already done for the fee gate), pass to `record_fill()`:
```python
record_fill(
    ...,
    strategy=strategy,
    fee=fee,
)
```

**File**: `kalshi/position_manager.py`

Sell order records: `strategy="maker"`, `fee=0.0` (resting limit sells are fee-exempt). Note: if a sell fills as taker, the actual fee differs — this is an acceptable approximation. True fees come from Kalshi settlement data in equity snapshots.

### 6. Dashboard API Changes

**File**: `dashboard/api.py`

**Modified endpoints:**

`GET /api/activity` — Add `strategy` and `fee` fields from trades.db to each trade object.

`GET /api/settled` — Add `strategy` and `fee` fields. Add to summary: `total_fees`, `maker_count`, `taker_count`.

**New endpoints:**

`GET /api/performance/fees` — Returns cumulative daily time series:
```json
[
  {"date": "2026-03-10", "cumulative_pnl": 12.50, "cumulative_fees": 1.80},
  {"date": "2026-03-11", "cumulative_pnl": 15.20, "cumulative_fees": 2.10}
]
```

Source: equity_db `fees_paid` column (already recorded daily) + realized_pnl. Accumulate across dates. Note: fee data lags by settlement date — fees on open positions appear only after settlement.

`GET /api/fees/summary` — Dedicated endpoint for fee summary card:
- `total_fees_paid`: lifetime from Kalshi settled positions
- `maker_trades`: count from trades.db WHERE strategy='maker'
- `taker_trades`: count from trades.db WHERE strategy='taker'
- `fee_savings`: estimated taker fees for maker trades (what we would have paid)

### 7. Dashboard Frontend

**File**: `dashboard/static/index.html`
- Fee summary card in performance section: total fees, maker/taker split, savings estimate

**File**: `dashboard/static/js/performance.js`
- New chart: "P&L vs Fees" — two cumulative lines using Plotly.js (matching existing equity curve)
  - Line 1: Cumulative realized P&L (existing color scheme)
  - Line 2: Cumulative fees paid (red/orange)
  - Gap = net fee drag
- Placed below existing equity curve

**File**: `dashboard/static/js/activity.js`
- Add "Strategy" column: green "MAKER" / orange "TAKER" badge
- Add "Fee" column: dollar amount (e.g. "$0.00", "$0.04")

**File**: `dashboard/static/js/settled.js`
- Same strategy + fee columns as activity

### 8. Tests

**File**: `tests/test_order_cleanup.py` (new)
- Test age-based cancellation (mock order older than 6h → cancelled)
- Test event-proximity cancellation (event closing in 1h → cancelled)
- Test fresh order not cancelled (2h old, event in 12h → kept)
- Test cancellation summary format

**File**: `tests/test_exchanges.py` (extend)
- Test `cancel_order` method (mock DELETE response)

**File**: `tests/test_pricing.py` (extend)
- Test fee recording: verify record_fill receives strategy + fee
- Test maker fee = 0.0, taker fee = calculated value

## Critical Files

| File | Change Type |
|------|-------------|
| `exchanges/kalshi.py` | Add `_delete` helper + `cancel_order` method |
| `kalshi/order_cleanup.py` | New module |
| `daemon.py` | Add Phase 1.5 |
| `kalshi/fill_tracker.py` | Schema migration + record_fill update + ON CONFLICT fix |
| `pipeline/stages.py` | Pass strategy/fee to record_fill |
| `kalshi/position_manager.py` | Pass strategy/fee on sell records |
| `dashboard/api.py` | New endpoints + modify activity/settled |
| `dashboard/static/index.html` | Fee card + chart container |
| `dashboard/static/js/performance.js` | Cumulative P&L vs fees chart (Plotly.js) |
| `dashboard/static/js/activity.js` | Strategy/fee columns |
| `dashboard/static/js/settled.js` | Strategy/fee columns |
| `tests/test_order_cleanup.py` | New test file |
| `tests/test_exchanges.py` | Extend with cancel_order test |
| `tests/test_pricing.py` | Extend with fee recording tests |
