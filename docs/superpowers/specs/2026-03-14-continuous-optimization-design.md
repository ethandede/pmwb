# Continuous Optimization System — Design Spec

**Date:** 2026-03-14
**Status:** Approved
**Goal:** Automated performance analytics with human-approved parameter tuning

## Overview

The system continuously analyzes trading performance and surfaces actionable insights through the dashboard, Telegram alerts, and a callable Claude Code skill. Parameters only change with explicit user approval unless a bug-level issue is detected.

## 1. Analytics Engine

**File:** `analytics/optimizer.py`
**Database:** `data/analytics.db`
**Trigger:** Runs after every settler cycle in daemon.py (Phase 3.5)

### Computed Metrics

Stored as daily snapshots in `analytics.db`:

| Metric | Description | Granularity |
|--------|-------------|-------------|
| `hit_rate` | Wins / (Wins + Losses) | 1d, 7d, 30d rolling |
| `net_pnl` | Sum of settled P&L | 1d, 7d, 30d rolling |
| `avg_win` | Average win amount | 7d, 30d |
| `avg_loss` | Average loss amount | 7d, 30d |
| `win_loss_ratio` | avg_win / abs(avg_loss) | 7d, 30d |
| `edge_accuracy` | Predicted edge vs actual outcome | 7d rolling |
| `calibration` | Model prob vs actual settlement rate | By confidence bucket |
| `city_hit_rate` | Hit rate per city | 30d rolling |
| `confidence_hit_rate` | Hit rate by confidence bucket | 30d rolling |
| `edge_hit_rate` | Hit rate by edge bucket | 30d rolling |

### Parameter Recommendations

After computing metrics, the engine evaluates:

1. **Confidence threshold** — If trades at conf 55-59 win at 55%+, recommend lowering from 60 to 55
2. **Edge threshold** — If trades at 10-12% edge win profitably, recommend lowering from 12%
3. **City exclusions** — If a city has <40% hit rate over 30+ trades, recommend skipping
4. **Model weights** — If one model source is consistently more accurate, recommend weight adjustment
5. **Position sizing** — If win/loss ratio is skewed, recommend Kelly multiplier adjustment

Each recommendation includes:
- Current value
- Suggested value
- Supporting data (sample size, hit rate, P&L impact)
- Confidence level (low/medium/high based on sample size)

### Schema

```sql
CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,
    total_trades INTEGER,
    wins INTEGER,
    losses INTEGER,
    net_pnl REAL,
    avg_win REAL,
    avg_loss REAL,
    hit_rate REAL
);

CREATE TABLE bucket_stats (
    date TEXT,
    bucket_type TEXT,  -- 'confidence', 'edge', 'city'
    bucket_value TEXT, -- '60-70', '10-15%', 'nyc'
    trades INTEGER,
    wins INTEGER,
    hit_rate REAL,
    avg_pnl REAL,
    PRIMARY KEY (date, bucket_type, bucket_value)
);

CREATE TABLE recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    param_name TEXT,
    current_value TEXT,
    suggested_value TEXT,
    reason TEXT,
    sample_size INTEGER,
    confidence TEXT,  -- 'low', 'medium', 'high'
    status TEXT DEFAULT 'pending'  -- 'pending', 'applied', 'dismissed'
);
```

## 2. Dashboard Sections

### 2a. Daily Scorecard

**API:** `GET /api/analytics/scorecard`
**Position:** Below settled trades section

Displays:
- Today's/yesterday's settlements count, wins, losses
- Today's P&L
- Best and worst trade of the day
- Comparison to 7-day average

### 2b. Trends

**API:** `GET /api/analytics/trends`

Displays:
- 7-day rolling hit rate sparkline
- 7-day rolling P&L sparkline
- Edge accuracy trend (are we getting better or worse at predicting?)

### 2c. Parameter Health

**API:** `GET /api/analytics/recommendations`

Displays for each tunable parameter:
- Current value
- Data-suggested value
- Sample size backing the suggestion
- Color: green (optimal), amber (could improve), red (data says change it)

## 3. Alerts

### Telegram Alerts

Sent when the optimizer detects:
- Daily P&L crosses -5% (circuit breaker context)
- A city drops below 40% hit rate on 20+ trades
- A parameter recommendation reaches "high" confidence
- Daily scorecard summary (optional, configurable)

Uses existing `alerts/telegram_alert.py` infrastructure.

### Dashboard Banner

Top of page, dismissable. Shows the highest-priority pending recommendation.

## 4. Claude Code Skill

**Skill name:** `optimize`
**Invocation:** `/optimize`

Reads `analytics.db` and provides:
1. Performance briefing (rolling stats, trend direction)
2. Active recommendations with data
3. Proposed parameter changes (user approves each)
4. Applies approved changes to config.py and deploys

The skill file will live in the superpowers skill directory and be registered for invocation.

## 5. Integration Points

### daemon.py Changes

After Phase 3 (settler), add Phase 3.5:
```python
from analytics.optimizer import run_analytics
run_analytics()  # ~100ms, just SQL aggregations
```

### dashboard/api.py Changes

Three new endpoints:
- `GET /api/analytics/scorecard`
- `GET /api/analytics/trends`
- `GET /api/analytics/recommendations`

### Config Changes

Add to config.py:
```python
ANALYTICS_ENABLED = True
TELEGRAM_DAILY_SCORECARD = True  # send daily summary
```

## 6. Implementation Order

1. `analytics/optimizer.py` — core engine + schema
2. Dashboard API endpoints
3. Dashboard JS sections (scorecard, trends, parameter health)
4. Telegram alert integration
5. Claude Code skill
6. Tests

## 7. What This Does NOT Do

- Auto-apply parameter changes (requires user approval)
- Replace the existing forecast model (this is meta — it optimizes the parameters around the model)
- Require new external dependencies (pure Python + SQLite + existing Plotly)
