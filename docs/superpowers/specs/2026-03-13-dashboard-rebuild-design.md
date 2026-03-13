# Dashboard Rebuild — FastAPI + Vanilla Frontend

## Goal

Replace the Streamlit dashboard with a FastAPI backend serving a vanilla HTML/SCSS/JS frontend. Eliminates CSS fighting, iframe limitations, and gives full DOM control while keeping the same branded trading terminal aesthetic.

## Architecture

FastAPI serves JSON API endpoints and static files. The frontend is a single HTML page with SCSS-compiled CSS and vanilla JS modules. Plotly.js (CDN) handles all charting. Data is cached in SQLite by the existing scanner daemon; the API reads from cache by default with an option to force a live rescan.

## Tech Stack

- **Backend**: FastAPI + Uvicorn
- **Frontend**: HTML, SCSS (compiled to CSS), vanilla JS (ES modules)
- **Charts**: Plotly.js via CDN
- **Fonts**: Google Fonts — Merriweather (headings), Roboto (body), JetBrains Mono (numbers/tickers)
- **Data**: SQLite (scan cache, equity history, model outcomes)

---

## API Endpoints

| Endpoint | Method | Query Params | Returns |
|----------|--------|--------------|---------|
| `/` | GET | — | Serves `index.html` |
| `/api/portfolio` | GET | — | Balance (cash, positions, equity, deployed %), settled P&L (gross, fees, net, hit rate, W/L), open positions list, resting orders list |
| `/api/markets/temp` | GET | `force=true` | Temperature market scan results. Default: from `scan_cache.db`. `force=true`: live rescan, updates cache, returns fresh data |
| `/api/markets/precip` | GET | `force=true` | Precipitation market scan results. Same caching behavior as temp |
| `/api/performance` | GET | — | Equity curve (date series) from `equity_history.db` + model accuracy data (predicted vs actual outcomes) from `scan_cache.db` |
| `/api/config` | GET | — | Risk control parameters (see response shape below) |

### API Response Shapes

**`/api/portfolio`**:
```json
{
  "balance": { "cash": 142.50, "positions": 82.54, "equity": 225.04, "deployed_pct": 36.7 },
  "settled": { "gross_pnl": 6.80, "fees": 2.60, "net_pnl": 4.20, "wins": 4, "losses": 2, "hit_rate": 66.7, "total_settled": 6 },
  "open_positions": [
    { "ticker": "KXRAINNY-26MAR", "city": "NYC", "side": "YES", "qty": 12, "entry": 0.45, "exposure": 12.00, "pnl": 1.20, "fees": 0.42 }
  ],
  "resting_orders": [
    { "ticker": "KXHIGHCHI-26MAR14", "action": "BUY", "side": "YES", "remaining": 5, "price": 0.38, "created": "2026-03-13 10:15" }
  ]
}
```

**Field derivation notes for `/api/portfolio`**:

- **`balance`**: From `get_balance()`. Kalshi returns `balance` and `portfolio_value` in cents — divide by 100. `deployed_pct = positions / equity * 100`.
- **`settled`**: From `get_positions(limit=200, settlement_status="settled")`. Iterate results: `gross_pnl = sum(realized_pnl_dollars)`, `fees = sum(fees_paid_dollars)`, `net_pnl = gross - fees`, `wins = count where realized_pnl_dollars > 0`, `losses = count where realized_pnl_dollars < 0`.
- **`open_positions`**: From `get_positions()` filtered to `position_fp != 0`. Derived fields:
  - `city`: looked up via a `ticker_to_city(ticker)` utility function (see Ticker-to-City Mapping section below)
  - `side`: `"YES"` if `position_fp > 0`, else `"NO"`
  - `qty`: `abs(position_fp)`
  - `entry`: `total_traded_dollars / abs(position_fp)`
  - `exposure`: `market_exposure_dollars`
  - `pnl`: `exposure - total_traded_dollars + realized_pnl_dollars`
  - `fees`: `fees_paid_dollars`
- **`resting_orders`**: From `get_orders(status="resting")`. The `price` field uses `yes_price_dollars` if `side == "yes"`, else `no_price_dollars`.

**`/api/markets/temp`** and **`/api/markets/precip`**:
```json
{
  "scan_time": "2026-03-13T14:15:02Z",
  "markets": [
    { "city": "NYC", "ticker": "KXRAINNY-26MAR", "threshold": ">2.5 in", "model_prob": 0.721, "market_price": 0.61, "edge": 0.111, "direction": "BUY YES", "confidence": 68, "method": "CDF", "days_left": 18 }
  ]
}
```

**`/api/performance`**:
```json
{
  "equity_curve": [
    { "date": "2026-03-01", "equity": 200.00, "realized_pnl": 0.00, "fees": 0.00 }
  ],
  "model_accuracy": [
    { "ticker": "KXRAINNY-26MAR", "city": "NYC", "market_type": "precip", "predicted": 0.72, "market": 0.61, "actual": 1, "settled": "2026-03-13" }
  ]
}
```

**`/api/config`**:
```json
{
  "mode": "LIVE",
  "scan_interval_min": 15,
  "edge_gate": 0.07,
  "confidence_gate": 55,
  "kelly_range": [0.25, 0.50],
  "max_order_bankroll_pct": 0.03,
  "max_order_usd": 20.0,
  "scan_budget_usd": 60.0,
  "drawdown_threshold": 0.15,
  "daily_stop_pct": 0.05
}
```
Field sources: `mode` from `PAPER_MODE`, `edge_gate` from `ALERT_THRESHOLD`, `confidence_gate` from `CONFIDENCE_THRESHOLD`, `kelly_range[0]` from `FRACTIONAL_KELLY`, `kelly_range[1]` is display-only (sigmoid max output, hardcoded 0.50), `max_order_bankroll_pct` from `KELLY_MAX_FRACTION`, `max_order_usd` from `MAX_ORDER_USD`, `scan_budget_usd` from `MAX_SCAN_BUDGET`, `drawdown_threshold` from `DRAWDOWN_THRESHOLD`, `daily_stop_pct` from `DAILY_STOP_PCT`, `scan_interval_min` is hardcoded 15 (cron config, not a Python constant).

---

## Data Storage

Both databases are **new** and must be created by the implementation. Each module that writes to them should call a `CREATE TABLE IF NOT EXISTS` init function on startup (same pattern as `kalshi/fill_tracker.py`'s `init_trades_db()`). All SQLite connections should enable **WAL mode** (`PRAGMA journal_mode=WAL`) for safe concurrent read/write between the scanner daemon and FastAPI server.

### `data/scan_cache.db`

Written by the scanner daemon after each 15-min cycle. Read by the API for cached market data.

```sql
CREATE TABLE scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time TEXT NOT NULL,
    market_type TEXT NOT NULL,  -- 'temp' or 'precip'
    ticker TEXT NOT NULL,
    city TEXT NOT NULL,
    model_prob REAL NOT NULL,
    market_price REAL NOT NULL,
    edge REAL NOT NULL,
    direction TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    method TEXT,
    threshold TEXT,            -- '>2.5 in' or '45-50'
    days_left INTEGER
);

CREATE INDEX idx_scan_results_latest ON scan_results(market_type, scan_time);
CREATE INDEX idx_scan_results_heatmap ON scan_results(market_type, city, scan_time);

CREATE TABLE model_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    market_type TEXT NOT NULL,
    predicted_prob REAL NOT NULL,
    market_price REAL NOT NULL,
    actual_outcome INTEGER NOT NULL,  -- 1=YES won, 0=NO won
    settled_time TEXT NOT NULL
);
```

**Retention policy**: The scanner daemon deletes `scan_results` rows older than 30 days after each write cycle (`DELETE FROM scan_results WHERE scan_time < datetime('now', '-30 days')`). At ~62 markets x 96 scans/day, this caps the table at ~180k rows.

**Heatmap query**: The edge heatmap aggregates scan history by city and date. When fewer than 3 days of data exist, the frontend falls back to the confidence-vs-edge scatter plot (same as the current dashboard).

### `data/equity_history.db`

Appended by the daily P&L script (runs at 8am PT / 15:00 UTC via cron).

```sql
CREATE TABLE equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    total_equity REAL NOT NULL,
    cash REAL NOT NULL,
    portfolio_value REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    fees_paid REAL NOT NULL,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0
);
```

**New code required in existing files**:

1. **`scanner.py`**: After each scan cycle completes, write all scan results to `data/scan_cache.db` `scan_results` table. Init the DB on first run.
2. **`weather/resolver.py`**: When a market settles, write to `data/scan_cache.db` `model_outcomes` table. This is new behavior — the resolver currently only updates `bias.db`.
3. **`scripts/daily_pnl_summary.py`**: After computing the daily summary, append a row to `data/equity_history.db` `equity_snapshots`. The `win_count` and `loss_count` come from the same `get_positions(settlement_status="settled")` call already in the script.

---

## Frontend Structure

```
dashboard/
  api.py
  static/
    index.html
    scss/
      main.scss
      _variables.scss
      _base.scss
      _header.scss
      _metrics.scss
      _charts.scss
      _tables.scss
      _footer.scss
    css/
      main.css           # compiled output
    js/
      app.js             # init, fetch orchestration, refresh
      portfolio.js       # portfolio section rendering
      markets.js         # market tables + charts
      performance.js     # equity curve + model accuracy
    assets/
      logo.svg
```

- SCSS compiled locally with `dart-sass`: `sass dashboard/static/scss/main.scss dashboard/static/css/main.css`. The compiled `main.css` is committed — no build step in production
- JS uses ES modules (`type="module"`) — no bundler
- Plotly.js loaded from CDN
- Google Fonts loaded in `<head>`

---

## Page Sections (top to bottom)

### 1. Header
- EE logo SVG + "Weather Edge" title + subtitle
- Refresh button (triggers all API fetches)
- Mode badge (LIVE/PAPER) + timestamp

### 2. How This Works
- Collapsible `<details>` element
- Same pipeline explainer text as current

### 3. Portfolio
- **Top row**: 4 metric cards — Cash, Positions, Net P&L, Hit Rate (each with subtitle line for consistent height)
- **Settled Performance row**: 4 metric cards — Gross P&L (X settled), Fees Paid (X% of gross), Net P&L (+$X.XX), Hit Rate (XW / YL)
- **Open Positions table**: Ticker, City, Side, Qty, Entry, Exposure, P&L, Fees. Row backgrounds tinted green/red by P&L
- **Resting Orders**: Collapsible `<details>`, shows table when expanded
- **Summary caption**: Cost basis, exposure, unrealized, fees

### 4. Performance (new section)
- Two side-by-side chart cards:
  - **Equity Curve**: Plotly line chart with area fill, from `equity_snapshots` table
  - **Model Accuracy**: Plotly scatter — predicted probability vs actual outcome, colored by correct/incorrect, with diagonal reference line for perfect calibration

### 5. Precipitation Markets
- Section header + description
- Last scan timestamp + "Force Rescan" button
- Data table: City, Ticker, Threshold, Model, Market, Edge, Direction, Confidence
- Two side-by-side charts:
  - **Edge by City**: bar chart
  - **Edge Heatmap**: city x date heatmap (built from scan history in `scan_results` table)
- Trade-worthy signal badge

### 6. Temperature Markets
- Same structure as Precipitation Markets
- Threshold column shows bucket range instead of rainfall inches

### 7. Footer
- 3 signal count metric cards: Precip Signals, Temp Signals, Trade-Worthy
- Risk Controls grid: scan interval, edge gate, confidence gate, Kelly, max order, scan budget, drawdown, daily stop

---

## Brand Constants

Carried over from current dashboard, defined in `_variables.scss`:

```scss
// Brand
$brand-primary: #45748C;
$brand-secondary: #BF3978;
$brand-teal: #10b981;
$brand-amber: #f59e0b;
$brand-red: #ef4444;

// Backgrounds
$bg-deep: #0a0e1a;
$bg-surface: #111827;
$bg-card: #1a2332;
$bg-elevated: #1f2b3d;
$border-subtle: #2a3a4e;

// Text
$text-primary: rgba(255, 255, 255, 0.92);
$text-secondary: rgba(255, 255, 255, 0.60);
$text-muted: rgba(255, 255, 255, 0.45);
```

---

## Deployment

- FastAPI + Uvicorn on `127.0.0.1:8501` (same port as Streamlit — zero nginx changes)
- `run_dashboard.sh` updated to launch uvicorn instead of streamlit
- Cron watchdog: `pgrep -f uvicorn` instead of `pgrep -f streamlit`
- Same nginx basic auth proxy, same Cloudflare SSL

### Dependencies

**Added**: `fastapi`, `uvicorn`
**Removed**: `streamlit`, `plotly` (Python — replaced by Plotly.js CDN)

### Files Removed

- `dashboard/app.py` (old Streamlit dashboard)
- `.streamlit/config.toml`

---

## Refresh Behavior

- **Manual refresh**: clicking the refresh button in the header calls all API endpoints and re-renders all sections
- **Market rescan**: each market section has a "Force Rescan" button that calls the endpoint with `?force=true`, triggering a live scan. This is independent of the global refresh
- **No auto-refresh**: data is static until the user clicks refresh

---

## Error Handling

**API errors**: All endpoints return `{ "error": "message", "cached_at": "ISO timestamp or null" }` with appropriate HTTP status codes on failure. If the Kalshi API is unreachable, `/api/portfolio` returns the error with status 502. Market endpoints fall back to cached data if available and include the cache timestamp.

**Empty databases**: If `scan_cache.db` or `equity_history.db` don't exist or have no rows, the API returns empty arrays (not errors). The frontend renders "No data yet" placeholder text in the relevant sections.

**Force rescan timeout**: A live rescan can take 30-60 seconds (14+ series with rate limiting). The frontend sets a 90-second fetch timeout on `?force=true` requests and shows a spinner with "Scanning... this may take up to a minute" text. The API runs the scan synchronously (no background task — keeps it simple).

**Frontend loading**: On page load, all sections show skeleton placeholders while API calls are in flight. Each section loads independently — a failure in `/api/portfolio` doesn't block `/api/markets/temp`.

---

## Ticker-to-City Mapping

Create a `ticker_to_city(ticker: str) -> str` utility function in a shared location (e.g. `kalshi/scanner.py` or a new `kalshi/utils.py`). This function:

1. Strips the date suffix from the ticker (everything after the first `-`): `KXRAINNY-26MAR` → `KXRAINNY`
2. Looks up the prefix in a reverse-lookup dict built at import time from `WEATHER_SERIES` and `PRECIP_SERIES` in `kalshi/scanner.py`
3. The reverse-lookup must handle all ticker prefix variants:
   - `KXHIGHNY` → NYC (from `WEATHER_SERIES["KXHIGHNY"]`)
   - `KXLOWTNY` → NYC (low-temp markets use `KXLOWT` + city suffix)
   - `KXRAINNY` → NYC (daily rain — stripped from `KXRAINNYCM` in `PRECIP_SERIES`)
   - `KXRAINNYCM` → NYC (monthly cumulative rain)
4. Falls back to the raw prefix (e.g. `KXHIGHDEN`) if no match — human-readable enough

Build the mapping once at module level, not on every call.

---

## Integration Points

### Scanner daemon (`scanner.py`)
After each scan cycle, write results to `data/scan_cache.db` `scan_results` table. **This is new code** — add an `init_scan_cache_db()` function and a `write_scan_results()` function. Existing scanning logic unchanged. Also run the 30-day retention `DELETE` after each write.

### Resolver (`weather/resolver.py`)
When a market settles, write to `data/scan_cache.db` `model_outcomes` table with our predicted probability and the actual outcome. **This is new code** — the resolver currently only updates `bias.db`.

### Daily P&L script (`scripts/daily_pnl_summary.py`)
After computing daily summary, append a row to `data/equity_history.db` `equity_snapshots` table. **This is new code** — add an `init_equity_db()` function and a `record_equity_snapshot()` call after the existing summary logic.

### Existing functions called by API
- `kalshi/trader.py`: `get_balance()`, `get_positions(settlement_status=...)`, `get_orders(status="resting")`
- `kalshi/scanner.py`: `get_kalshi_weather_markets()`, `get_kalshi_precip_markets()`, `get_kalshi_price()`, `parse_kalshi_bucket()`, `WEATHER_SERIES`, `PRECIP_SERIES`
- `weather/multi_model.py`: `fuse_forecast()`, `fuse_precip_forecast()`
- `weather/forecast.py`: `calculate_remaining_month_days()`
- `config.py`: all risk control constants + `PAPER_MODE`
