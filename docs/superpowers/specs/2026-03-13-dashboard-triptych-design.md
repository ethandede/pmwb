# Dashboard Triptych Redesign

**Date:** 2026-03-13
**Status:** Approved

---

## Problem

The current dashboard buries portfolio data in a sidebar, shows two permanently empty charts (equity curve with 1 data point, model accuracy with 0), and puts the lowest-volume market type (precip) above the highest (temp). The result is a dashboard that feels like filler rather than a trading terminal.

## Design

### Layout: Three summary cards + two-column main area

No sidebar. Everything flows top-to-bottom.

**Top row — 3 equal cards spanning full width:**

| Card | Primary Value | Secondary | Visual |
|------|--------------|-----------|--------|
| Equity | `$290.14` | Cash / Positions breakdown | Sparkline from `equity_curve` (builds daily) |
| Record | `48W / 40L` | `55% hit rate` | Daily P&L bar chart (green/red) from `settled_daily` |
| P&L | `-$8.82` | Fees: `$5.60` | Health badge from `/api/health` (`HEALTHY` / `WARN` / `CRITICAL`) |

**Main area below — two columns:**

- **Left column (~75%)**: Open positions table, then temp markets section (table + edge chart + heatmap/scatter)
- **Right column (~25%)**: Precip markets section (table + edge chart), risk controls grid, signal summary counts

### Removed Elements

- **Sidebar layout** — replaced by full-width triptych + two-column flow
- **"How This Works" expander** — drop entirely (one-line footer at most)
- **Resting orders section** — not useful enough for screen space
- **Model accuracy scatter chart** — never populated, replaced by daily P&L bars
- **Empty equity curve taking 50% of performance row** — moved to sparkline in equity card

### New Elements

- **Daily P&L bar chart** in the Record card — one green/red bar per day from `settled_daily` API data. Shows `wins`, `losses`, `pnl` per day.
- **Health status badge** in the P&L card — fetches `/api/health`, shows `overall` status as a colored pill (green/amber/red). Clicking expands to show check details.
- **Temp markets moved above precip** — 168 markets vs 46, where all the action is.

### Data Flow

All data sources already exist and are populated:

| Card/Section | API Endpoint | Data Source |
|---|---|---|
| Equity card | `/api/portfolio` | Kalshi API (balance) |
| Record card | `/api/performance` → `settled_daily` | `data/trades.db` settled entries |
| P&L card | `/api/portfolio` → `settled` | Kalshi API (settled positions) |
| Health badge | `/api/health` | `health_check.py` |
| Open positions | `/api/portfolio` → `open_positions` | Kalshi API |
| Temp markets | `/api/markets/temp` | `data/scan_cache.db` |
| Precip markets | `/api/markets/precip` | `data/scan_cache.db` |
| Risk controls | `/api/config` | `config.py` |

### File Changes

| File | Change |
|------|--------|
| `dashboard/static/index.html` | Remove sidebar/aside, remove how-this-works, remove resting orders, remove performance section. Add triptych card row, restructure to two-column below. Swap temp above precip. |
| `dashboard/static/js/performance.js` | Replace `accuracyChart()` with `dailyPnlChart(data, containerId)` that renders green/red bars from `settled_daily`. Keep `equityChart()` but make it a compact sparkline variant for the card. Add `healthBadge(data, containerId)` that fetches `/api/health` and renders a status pill. |
| `dashboard/static/js/app.js` | Update `refreshAll()`: remove resting orders load, add `/api/health` fetch, wire new card renderers. Reorder: portfolio first, then performance cards, then markets. |
| `dashboard/static/js/portfolio.js` | Remove `ordersTable()` function and resting orders rendering. Remove resting count badge update. Simplify `renderPortfolio()`. |
| `dashboard/static/scss/_base.scss` | Remove `.we-sidebar`, `.we-layout` sidebar grid. Add `.triptych-row` (3-card flex grid), `.main-two-col` (75/25 split). Adjust existing `.chart-card` and `.metric-card` styles for new layout. |

### CSS Layout Structure

```
header
  triptych-row
    [equity-card] [record-card] [pnl-card]
  main-two-col
    main-wide (75%)
      [open-positions]
      [temp-markets: table + charts]
    main-narrow (25%)
      [precip-markets: table + charts]
      [risk-controls]
      [signal-summary]
footer (optional one-liner)
```

### Triptych Card HTML Structure

```html
<div class="triptych-row">
  <div class="triptych-card">
    <div class="triptych-value">$290.14</div>
    <div class="triptych-label">Total Equity</div>
    <div class="triptych-visual" id="equity-sparkline"></div>
    <div class="triptych-detail">$255 cash + $35 positions</div>
  </div>
  <!-- repeat for record, pnl -->
</div>
```

### Daily P&L Bar Chart

Input: `settled_daily` array from `/api/performance`:
```json
[{"date": "2026-03-12", "trades": 147, "wins": 48, "losses": 40, "pnl": -8.82}]
```

Render as horizontal Plotly bar chart: one bar per day, colored green (positive P&L) or red (negative). Hover shows wins/losses/pnl. Compact height (~80px) to fit inside the triptych card.

### Health Badge

Fetch `/api/health`, read `overall` field. Render as:
- `healthy` → green pill "HEALTHY"
- `warn` → amber pill "# WARNINGS"
- `critical` → red pill "# CRITICAL"

Click/hover expands to show failing check names.
