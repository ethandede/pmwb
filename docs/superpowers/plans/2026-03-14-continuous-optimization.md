# Continuous Optimization System — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automated performance analytics engine that computes rolling stats from trades.db, surfaces insights on the dashboard, sends Telegram alerts, and powers a `/optimize` Claude Code skill.

**Architecture:** SQLite-based analytics (analytics.db) populated by a lightweight aggregation pass after each settler cycle. Dashboard reads via new API endpoints. Telegram alerts fire on threshold crossings. The `/optimize` skill reads analytics.db for conversational briefings.

**Tech Stack:** Python 3.12, SQLite, FastAPI, vanilla JS + Plotly (existing), Telegram Bot API (existing)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `analytics/__init__.py` | Create | Package init |
| `analytics/optimizer.py` | Create | Core engine: schema init, metric computation, recommendation generation |
| `analytics/alerts.py` | Create | Telegram alert logic for analytics thresholds |
| `dashboard/api.py` | Modify | Add 3 analytics endpoints |
| `dashboard/static/js/analytics.js` | Create | Scorecard, trends, parameter health rendering |
| `dashboard/static/index.html` | Modify | Add analytics sections |
| `dashboard/static/js/app.js` | Modify | Wire analytics JS |
| `daemon.py` | Modify | Add Phase 3.5 call |
| `config.py` | Modify | Add ANALYTICS_ENABLED, TELEGRAM_DAILY_SCORECARD |
| `tests/test_optimizer.py` | Create | Analytics engine tests |
| `.claude/skills/optimize.md` | Create | Claude Code skill definition |

---

## Chunk 1: Analytics Engine

### Task 1: Schema + Init

**Files:**
- Create: `analytics/__init__.py`
- Create: `analytics/optimizer.py`
- Test: `tests/test_optimizer.py`

- [ ] **Step 1: Create analytics package**

```bash
mkdir -p analytics
touch analytics/__init__.py
```

- [ ] **Step 2: Write failing test for schema init**

```python
# tests/test_optimizer.py
import os
import tempfile
import unittest
from analytics.optimizer import init_analytics_db

class TestAnalyticsSchema(unittest.TestCase):
    def test_init_creates_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_analytics_db(db_path)
            import sqlite3
            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
            self.assertIn("daily_stats", tables)
            self.assertIn("bucket_stats", tables)
            self.assertIn("recommendations", tables)
        finally:
            os.unlink(db_path)

if __name__ == "__main__":
    unittest.main()
```

Run: `python -m pytest tests/test_optimizer.py::TestAnalyticsSchema::test_init_creates_tables -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics'`

- [ ] **Step 3: Implement schema init**

```python
# analytics/optimizer.py
"""Continuous optimization engine — computes rolling stats and parameter recommendations."""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

ANALYTICS_DB = "data/analytics.db"
TRADES_DB = "data/trades.db"


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_analytics_db(db_path: str = ANALYTICS_DB):
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            net_pnl REAL,
            avg_win REAL,
            avg_loss REAL,
            hit_rate REAL
        );

        CREATE TABLE IF NOT EXISTS bucket_stats (
            date TEXT,
            bucket_type TEXT,
            bucket_value TEXT,
            trades INTEGER,
            wins INTEGER,
            hit_rate REAL,
            avg_pnl REAL,
            PRIMARY KEY (date, bucket_type, bucket_value)
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            param_name TEXT,
            current_value TEXT,
            suggested_value TEXT,
            reason TEXT,
            sample_size INTEGER,
            confidence TEXT DEFAULT 'low',
            status TEXT DEFAULT 'pending'
        );
    """)
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_optimizer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analytics/ tests/test_optimizer.py
git commit -m "feat: analytics engine schema + init"
```

---

### Task 2: Daily Stats Computation

**Files:**
- Modify: `analytics/optimizer.py`
- Test: `tests/test_optimizer.py`

- [ ] **Step 1: Write failing test for daily stats**

Add to `tests/test_optimizer.py`:

```python
class TestDailyStats(unittest.TestCase):
    def setUp(self):
        self.analytics_db = tempfile.mktemp(suffix=".db")
        self.trades_db = tempfile.mktemp(suffix=".db")
        init_analytics_db(self.analytics_db)
        # Create trades.db with test data
        conn = sqlite3.connect(self.trades_db)
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, ticker TEXT,
            city TEXT, side TEXT, limit_price INTEGER, fill_price INTEGER,
            fill_qty INTEGER, fill_time TEXT, settlement_outcome TEXT, pnl REAL
        )""")
        # 3 wins, 2 losses
        trades = [
            ("o1","T1","nyc","buy_yes",50,50,10,"2026-03-14T10:00:00","win",4.0),
            ("o2","T2","nyc","buy_no",30,30,10,"2026-03-14T11:00:00","win",7.0),
            ("o3","T3","chi","buy_yes",60,60,10,"2026-03-14T12:00:00","loss",-6.0),
            ("o4","T4","chi","buy_no",40,40,10,"2026-03-14T13:00:00","win",6.0),
            ("o5","T5","mia","buy_yes",70,70,10,"2026-03-14T14:00:00","loss",-7.0),
        ]
        for t in trades:
            conn.execute("INSERT INTO trades (order_id,ticker,city,side,limit_price,fill_price,fill_qty,fill_time,settlement_outcome,pnl) VALUES (?,?,?,?,?,?,?,?,?,?)", t)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.analytics_db)
        os.unlink(self.trades_db)

    def test_compute_daily_stats(self):
        from analytics.optimizer import compute_daily_stats
        compute_daily_stats(self.trades_db, self.analytics_db)
        conn = sqlite3.connect(self.analytics_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM daily_stats WHERE date='2026-03-14'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["wins"], 3)
        self.assertEqual(row["losses"], 2)
        self.assertAlmostEqual(row["hit_rate"], 0.6)
        self.assertAlmostEqual(row["net_pnl"], 4.0)
```

Run: `python -m pytest tests/test_optimizer.py::TestDailyStats -v`
Expected: FAIL — `ImportError: cannot import name 'compute_daily_stats'`

- [ ] **Step 2: Implement compute_daily_stats**

Add to `analytics/optimizer.py`:

```python
def compute_daily_stats(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Aggregate settled trades into daily stats."""
    init_analytics_db(analytics_db)
    trades_conn = sqlite3.connect(trades_db)
    trades_conn.row_factory = sqlite3.Row

    rows = trades_conn.execute("""
        SELECT DATE(fill_time) as date,
               COUNT(*) as total,
               SUM(CASE WHEN settlement_outcome = 'win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN settlement_outcome = 'loss' THEN 1 ELSE 0 END) as losses,
               SUM(pnl) as net_pnl,
               AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
               AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
        FROM trades
        WHERE settlement_outcome IN ('win', 'loss')
          AND NOT side LIKE 'sell_%'
          AND fill_qty > 0
        GROUP BY DATE(fill_time)
    """).fetchall()
    trades_conn.close()

    a_conn = _connect(analytics_db)
    for r in rows:
        total = r["wins"] + r["losses"]
        hit_rate = r["wins"] / total if total > 0 else 0
        a_conn.execute("""
            INSERT OR REPLACE INTO daily_stats
            (date, total_trades, wins, losses, net_pnl, avg_win, avg_loss, hit_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (r["date"], total, r["wins"], r["losses"],
              round(r["net_pnl"] or 0, 2),
              round(r["avg_win"] or 0, 2),
              round(r["avg_loss"] or 0, 2),
              round(hit_rate, 4)))
    a_conn.commit()
    a_conn.close()
```

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/test_optimizer.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add analytics/optimizer.py tests/test_optimizer.py
git commit -m "feat: daily stats computation from trades.db"
```

---

### Task 3: Bucket Stats (confidence, edge, city breakdowns)

**Files:**
- Modify: `analytics/optimizer.py`
- Test: `tests/test_optimizer.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_optimizer.py`:

```python
    def test_compute_bucket_stats(self):
        from analytics.optimizer import compute_bucket_stats
        compute_bucket_stats(self.trades_db, self.analytics_db)
        conn = sqlite3.connect(self.analytics_db)
        conn.row_factory = sqlite3.Row
        # Check city bucket
        city_rows = conn.execute(
            "SELECT * FROM bucket_stats WHERE bucket_type='city'"
        ).fetchall()
        conn.close()
        cities = {r["bucket_value"]: r for r in city_rows}
        self.assertIn("nyc", cities)
        self.assertEqual(cities["nyc"]["wins"], 2)
```

Run: `python -m pytest tests/test_optimizer.py::TestDailyStats::test_compute_bucket_stats -v`
Expected: FAIL

- [ ] **Step 2: Implement compute_bucket_stats**

Add to `analytics/optimizer.py`:

```python
def compute_bucket_stats(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Compute hit rate breakdowns by city, confidence, and edge buckets."""
    init_analytics_db(analytics_db)
    trades_conn = sqlite3.connect(trades_db)
    trades_conn.row_factory = sqlite3.Row
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # City breakdown
    city_rows = trades_conn.execute("""
        SELECT city, COUNT(*) as trades,
               SUM(CASE WHEN settlement_outcome='win' THEN 1 ELSE 0 END) as wins,
               AVG(pnl) as avg_pnl
        FROM trades
        WHERE settlement_outcome IN ('win','loss') AND NOT side LIKE 'sell_%' AND fill_qty > 0
        GROUP BY city
    """).fetchall()

    a_conn = _connect(analytics_db)
    for r in city_rows:
        total = r["trades"]
        hit_rate = r["wins"] / total if total > 0 else 0
        a_conn.execute("""
            INSERT OR REPLACE INTO bucket_stats
            (date, bucket_type, bucket_value, trades, wins, hit_rate, avg_pnl)
            VALUES (?, 'city', ?, ?, ?, ?, ?)
        """, (today, r["city"], total, r["wins"], round(hit_rate, 4), round(r["avg_pnl"] or 0, 2)))

    # Confidence breakdown (from scan_cache join)
    # Edge breakdown (from scan_cache join)
    # These require joining trades.db with scan_cache.db — implement as scan data becomes available
    # For now, city breakdown is the primary bucket stat

    a_conn.commit()
    a_conn.close()
    trades_conn.close()
```

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/test_optimizer.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add analytics/optimizer.py tests/test_optimizer.py
git commit -m "feat: bucket stats (city breakdowns)"
```

---

### Task 4: Parameter Recommendations

**Files:**
- Modify: `analytics/optimizer.py`
- Test: `tests/test_optimizer.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_optimizer.py`:

```python
class TestRecommendations(unittest.TestCase):
    def setUp(self):
        self.analytics_db = tempfile.mktemp(suffix=".db")
        self.trades_db = tempfile.mktemp(suffix=".db")
        init_analytics_db(self.analytics_db)
        conn = sqlite3.connect(self.trades_db)
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, ticker TEXT,
            city TEXT, side TEXT, limit_price INTEGER, fill_price INTEGER,
            fill_qty INTEGER, fill_time TEXT, settlement_outcome TEXT, pnl REAL
        )""")
        # Miami: 2 wins out of 25 trades → 8% hit rate → should recommend exclusion
        for i in range(25):
            outcome = "win" if i < 2 else "loss"
            pnl = 3.0 if outcome == "win" else -5.0
            conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (i, f"o{i}", f"T{i}", "miami", "buy_yes", 50, 50, 10,
                 f"2026-03-14T{10+i//4}:00:00", outcome, pnl))
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.analytics_db)
        os.unlink(self.trades_db)

    def test_generates_city_exclusion(self):
        from analytics.optimizer import generate_recommendations
        generate_recommendations(self.trades_db, self.analytics_db)
        conn = sqlite3.connect(self.analytics_db)
        conn.row_factory = sqlite3.Row
        recs = conn.execute("SELECT * FROM recommendations WHERE param_name='city_exclusion'").fetchall()
        conn.close()
        self.assertTrue(len(recs) > 0)
        self.assertIn("miami", recs[0]["suggested_value"])
```

Run: `python -m pytest tests/test_optimizer.py::TestRecommendations -v`
Expected: FAIL

- [ ] **Step 2: Implement generate_recommendations**

Add to `analytics/optimizer.py`:

```python
def generate_recommendations(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Analyze performance and generate parameter recommendations."""
    init_analytics_db(analytics_db)
    trades_conn = sqlite3.connect(trades_db)
    trades_conn.row_factory = sqlite3.Row
    a_conn = _connect(analytics_db)
    now = datetime.now(timezone.utc).isoformat()

    # Clear old pending recommendations
    a_conn.execute("DELETE FROM recommendations WHERE status='pending'")

    # 1. City exclusion: any city with <40% hit rate on 20+ trades
    city_rows = trades_conn.execute("""
        SELECT city, COUNT(*) as trades,
               SUM(CASE WHEN settlement_outcome='win' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE settlement_outcome IN ('win','loss') AND NOT side LIKE 'sell_%' AND fill_qty > 0
        GROUP BY city HAVING trades >= 20
    """).fetchall()

    for r in city_rows:
        hit_rate = r["wins"] / r["trades"] if r["trades"] > 0 else 0
        if hit_rate < 0.40:
            confidence = "high" if r["trades"] >= 30 else "medium"
            a_conn.execute("""
                INSERT INTO recommendations (created_at, param_name, current_value, suggested_value, reason, sample_size, confidence)
                VALUES (?, 'city_exclusion', 'included', ?, ?, ?, ?)
            """, (now, r["city"],
                  f"{r['city']} has {hit_rate:.0%} hit rate over {r['trades']} trades",
                  r["trades"], confidence))

    # 2. Win/loss ratio check
    ratio_row = trades_conn.execute("""
        SELECT AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
               AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss,
               COUNT(*) as total
        FROM trades
        WHERE settlement_outcome IN ('win','loss') AND NOT side LIKE 'sell_%' AND fill_qty > 0
    """).fetchone()

    if ratio_row and ratio_row["avg_win"] and ratio_row["avg_loss"] and ratio_row["total"] >= 20:
        ratio = abs(ratio_row["avg_win"] / ratio_row["avg_loss"])
        if ratio < 0.5:
            a_conn.execute("""
                INSERT INTO recommendations (created_at, param_name, current_value, suggested_value, reason, sample_size, confidence)
                VALUES (?, 'kelly_multiplier', '0.25', '0.15', ?, ?, 'medium')
            """, (now,
                  f"Win/loss ratio is {ratio:.2f} (wins avg ${ratio_row['avg_win']:.2f}, losses avg ${ratio_row['avg_loss']:.2f}). Reduce position sizing.",
                  ratio_row["total"]))

    a_conn.commit()
    a_conn.close()
    trades_conn.close()
```

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/test_optimizer.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add analytics/optimizer.py tests/test_optimizer.py
git commit -m "feat: parameter recommendation engine"
```

---

### Task 5: Main run_analytics entry point + daemon integration

**Files:**
- Modify: `analytics/optimizer.py`
- Modify: `daemon.py`
- Modify: `config.py`

- [ ] **Step 1: Add run_analytics and config**

Add to `analytics/optimizer.py`:

```python
def run_analytics(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    """Main entry point — run all analytics passes."""
    try:
        compute_daily_stats(trades_db, analytics_db)
        compute_bucket_stats(trades_db, analytics_db)
        generate_recommendations(trades_db, analytics_db)
    except Exception as e:
        print(f"  [Analytics] Error: {e}")
```

Add to `config.py`:

```python
ANALYTICS_ENABLED = True
TELEGRAM_DAILY_SCORECARD = True
```

- [ ] **Step 2: Add Phase 3.5 to daemon.py**

After the Phase 3 settler block, add:

```python
    # --- Phase 3.5: Analytics ---
    from config import ANALYTICS_ENABLED
    if ANALYTICS_ENABLED:
        console.print(f"\n[bold]Phase 3.5: Analytics[/bold]")
        try:
            from analytics.optimizer import run_analytics
            run_analytics()
        except Exception as e:
            console.print(f"  [red]Analytics error: {e}[/red]")
```

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add analytics/optimizer.py daemon.py config.py
git commit -m "feat: wire analytics into daemon Phase 3.5"
```

---

## Chunk 2: Dashboard API + Frontend

### Task 6: Analytics API endpoints

**Files:**
- Modify: `dashboard/api.py`

- [ ] **Step 1: Add scorecard endpoint**

Add to `dashboard/api.py`:

```python
ANALYTICS_DB = Path(__file__).resolve().parent.parent / "data" / "analytics.db"

@app.get("/api/analytics/scorecard")
async def analytics_scorecard():
    if not ANALYTICS_DB.exists():
        return {"today": None, "rolling_7d": None}
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")

    today_row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
    yesterday_row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (yesterday,)).fetchone()
    rolling = conn.execute("""
        SELECT SUM(wins) as wins, SUM(losses) as losses, SUM(net_pnl) as pnl,
               AVG(avg_win) as avg_win, AVG(avg_loss) as avg_loss
        FROM daily_stats WHERE date >= date('now', '-7 days')
    """).fetchone()
    conn.close()

    return {
        "today": dict(today_row) if today_row else None,
        "yesterday": dict(yesterday_row) if yesterday_row else None,
        "rolling_7d": dict(rolling) if rolling else None,
    }


@app.get("/api/analytics/trends")
async def analytics_trends():
    if not ANALYTICS_DB.exists():
        return {"daily": []}
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, hit_rate, net_pnl, wins, losses
        FROM daily_stats ORDER BY date DESC LIMIT 30
    """).fetchall()
    conn.close()
    return {"daily": [dict(r) for r in reversed(rows)]}


@app.get("/api/analytics/recommendations")
async def analytics_recommendations():
    if not ANALYTICS_DB.exists():
        return []
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM recommendations WHERE status='pending'
        ORDER BY CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 2: Test endpoints manually**

Run: `python -m pytest tests/test_api.py -v`
Expected: PASS (existing tests still work)

- [ ] **Step 3: Commit**

```bash
git add dashboard/api.py
git commit -m "feat: analytics API endpoints (scorecard, trends, recommendations)"
```

---

### Task 7: Analytics JS + dashboard sections

**Files:**
- Create: `dashboard/static/js/analytics.js`
- Modify: `dashboard/static/index.html`
- Modify: `dashboard/static/js/app.js`

- [ ] **Step 1: Create analytics.js**

```javascript
// dashboard/static/js/analytics.js
// Scorecard, trends, and parameter health rendering

function fmtPnl(n) {
    if (n === null || n === undefined) return '\u2014';
    const sign = n >= 0 ? '+' : '';
    return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function renderScorecard(data) {
    const el = document.getElementById('scorecard-content');
    if (!el) return;

    const today = data.today || data.yesterday;
    const label = data.today ? 'Today' : 'Yesterday';
    const r7 = data.rolling_7d;

    if (!today && !r7) {
        el.innerHTML = '<p class="section-desc">No settlement data yet. Analytics populate after trades settle.</p>';
        return;
    }

    let html = '<div class="metric-row" style="grid-template-columns: repeat(4, 1fr);">';

    if (today) {
        const pnlClass = today.net_pnl >= 0 ? 'metric-positive' : 'metric-negative';
        html += `
            <div class="metric-card ${pnlClass}">
                <div class="metric-label">${label} P&L</div>
                <div class="metric-value mono">${fmtPnl(today.net_pnl)}</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">${label} Record</div>
                <div class="metric-value mono">${today.wins}W / ${today.losses}L</div>
            </div>`;
    }

    if (r7) {
        const total7 = (r7.wins || 0) + (r7.losses || 0);
        const hr7 = total7 > 0 ? ((r7.wins || 0) / total7 * 100).toFixed(1) : '0.0';
        html += `
            <div class="metric-card metric-neutral">
                <div class="metric-label">7-Day Hit Rate</div>
                <div class="metric-value mono">${hr7}%</div>
                <div class="metric-subtitle">${r7.wins || 0}W / ${r7.losses || 0}L</div>
            </div>
            <div class="metric-card ${(r7.pnl || 0) >= 0 ? 'metric-positive' : 'metric-negative'}">
                <div class="metric-label">7-Day P&L</div>
                <div class="metric-value mono">${fmtPnl(r7.pnl)}</div>
            </div>`;
    }

    html += '</div>';
    el.innerHTML = html;
}

function renderTrends(data) {
    const el = document.getElementById('trends-content');
    if (!el) return;

    const daily = data.daily || [];
    if (daily.length < 2) {
        el.innerHTML = '<p class="section-desc">Need at least 2 days of data for trends.</p>';
        return;
    }

    const dates = daily.map(d => d.date);
    const hitRates = daily.map(d => d.hit_rate !== null ? (d.hit_rate * 100) : null);
    const pnls = daily.map(d => d.net_pnl);

    const layout = {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: '#1a2332',
        font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
        margin: { l: 50, r: 20, t: 10, b: 30 },
        xaxis: { gridcolor: '#2a3a4e' }, yaxis: { gridcolor: '#2a3a4e' },
    };
    const config = { displayModeBar: false, responsive: true };

    el.innerHTML = '<div class="chart-row"><div class="chart-card"><div class="chart-label">Hit Rate %</div><div id="trend-hitrate"></div></div><div class="chart-card"><div class="chart-label">Daily P&L</div><div id="trend-pnl"></div></div></div>';

    Plotly.newPlot('trend-hitrate', [{
        x: dates, y: hitRates, type: 'scatter', mode: 'lines+markers',
        line: { color: '#10b981', width: 2 }, marker: { size: 5 },
    }], { ...layout, yaxis: { ...layout.yaxis, title: { text: '%' } } }, config);

    const pnlColors = pnls.map(p => p >= 0 ? '#10b981' : '#ef4444');
    Plotly.newPlot('trend-pnl', [{
        x: dates, y: pnls, type: 'bar', marker: { color: pnlColors },
    }], { ...layout, yaxis: { ...layout.yaxis, title: { text: '$' } } }, config);
}

function renderRecommendations(data) {
    const el = document.getElementById('recommendations-content');
    if (!el) return;

    if (!data || data.length === 0) {
        el.innerHTML = '<p class="section-desc">No parameter recommendations at this time. System is operating within expected ranges.</p>';
        return;
    }

    const rows = data.map(r => {
        const confClass = r.confidence === 'high' ? 'val-negative' : r.confidence === 'medium' ? 'val-amber' : 'val-neutral';
        return `<tr>
            <td>${r.param_name}</td>
            <td class="mono">${r.current_value}</td>
            <td class="mono val-positive">${r.suggested_value}</td>
            <td>${r.reason}</td>
            <td class="num">${r.sample_size}</td>
            <td class="${confClass}">${r.confidence.toUpperCase()}</td>
        </tr>`;
    }).join('');

    el.innerHTML = `
        <table class="data-table">
            <thead><tr><th>Parameter</th><th>Current</th><th>Suggested</th><th>Reason</th><th class="num">Trades</th><th>Priority</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

export { renderScorecard, renderTrends, renderRecommendations };
```

- [ ] **Step 2: Add HTML sections**

Add to `dashboard/static/index.html` after the settled trades section:

```html
<hr class="we-divider">

<!-- Analytics: Scorecard -->
<section id="scorecard-section">
    <h2>Daily Scorecard</h2>
    <p class="section-desc">Settlement results and comparison to recent performance.</p>
    <div id="scorecard-content"></div>
</section>

<hr class="we-divider">

<!-- Analytics: Trends -->
<section id="trends-section">
    <h2>Trends</h2>
    <div id="trends-content"></div>
</section>

<hr class="we-divider">

<!-- Analytics: Parameter Health -->
<section id="recommendations-section">
    <h2>Parameter Health</h2>
    <p class="section-desc">Data-driven suggestions. Changes require your approval.</p>
    <div id="recommendations-content"></div>
</section>
```

- [ ] **Step 3: Wire into app.js**

Add import:
```javascript
import { renderScorecard, renderTrends, renderRecommendations } from './analytics.js?v=N';
```

Add to the `Promise.allSettled` block:
```javascript
loadSection('/api/analytics/scorecard', renderScorecard, 'scorecard-content'),
loadSection('/api/analytics/trends', renderTrends, 'trends-content'),
loadSection('/api/analytics/recommendations', renderRecommendations, 'recommendations-content'),
```

- [ ] **Step 4: Bump cache version, deploy, verify**

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/js/analytics.js dashboard/static/index.html dashboard/static/js/app.js
git commit -m "feat: analytics dashboard sections (scorecard, trends, parameter health)"
```

---

## Chunk 3: Alerts + Skill

### Task 8: Telegram analytics alerts

**Files:**
- Create: `analytics/alerts.py`
- Modify: `analytics/optimizer.py`

- [ ] **Step 1: Create alerts module**

```python
# analytics/alerts.py
"""Telegram alerts for analytics thresholds."""

from alerts.telegram_alert import send_signal_alert


def send_daily_scorecard(stats: dict):
    """Send daily scorecard summary via Telegram."""
    try:
        if not stats:
            return
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        pnl = stats.get("net_pnl", 0)
        hit_rate = stats.get("hit_rate", 0)

        msg = (
            f"Daily Scorecard\n"
            f"Record: {wins}W / {losses}L ({hit_rate:.0%})\n"
            f"P&L: {'+'if pnl>=0 else ''}${pnl:.2f}"
        )
        send_signal_alert(msg, "", 0, 0, 0, "")
    except Exception:
        pass


def send_recommendation_alert(rec: dict):
    """Send high-confidence recommendation via Telegram."""
    try:
        msg = (
            f"Parameter Alert: {rec['param_name']}\n"
            f"Current: {rec['current_value']} → Suggested: {rec['suggested_value']}\n"
            f"Reason: {rec['reason']}\n"
            f"Confidence: {rec['confidence'].upper()} ({rec['sample_size']} trades)"
        )
        send_signal_alert(msg, "", 0, 0, 0, "")
    except Exception:
        pass
```

- [ ] **Step 2: Wire alerts into run_analytics**

Add to `analytics/optimizer.py` `run_analytics()`:

```python
def run_analytics(trades_db: str = TRADES_DB, analytics_db: str = ANALYTICS_DB):
    try:
        compute_daily_stats(trades_db, analytics_db)
        compute_bucket_stats(trades_db, analytics_db)
        generate_recommendations(trades_db, analytics_db)

        # Send alerts
        from config import TELEGRAM_DAILY_SCORECARD
        if TELEGRAM_DAILY_SCORECARD:
            from analytics.alerts import send_daily_scorecard, send_recommendation_alert
            conn = _connect(analytics_db)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_stats = conn.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
            if today_stats:
                send_daily_scorecard(dict(today_stats))

            high_recs = conn.execute(
                "SELECT * FROM recommendations WHERE status='pending' AND confidence='high'"
            ).fetchall()
            for r in high_recs:
                send_recommendation_alert(dict(r))
            conn.close()
    except Exception as e:
        print(f"  [Analytics] Error: {e}")
```

- [ ] **Step 3: Commit**

```bash
git add analytics/alerts.py analytics/optimizer.py
git commit -m "feat: Telegram alerts for daily scorecard + high-priority recommendations"
```

---

### Task 9: Claude Code `/optimize` skill

**Files:**
- Create: `.claude/skills/optimize.md`

- [ ] **Step 1: Create skill file**

```markdown
---
name: optimize
description: Performance briefing and parameter optimization for the weather trading bot. Reads analytics.db, shows rolling stats, active recommendations, and proposes parameter changes.
user_invocable: true
---

# Weather Bot Optimizer

Read the analytics database and provide a performance briefing with parameter recommendations.

## Steps

1. Read `data/analytics.db` on the Hetzner server via SSH
2. Query daily_stats for rolling 7-day and 30-day performance
3. Query bucket_stats for city/confidence/edge breakdowns
4. Query recommendations for pending suggestions
5. Present a conversational briefing with:
   - Rolling hit rate and P&L trend
   - Best/worst performing cities
   - Active parameter recommendations with supporting data
   - Specific change proposals (user approves each)
6. If user approves a change:
   - Update config.py locally
   - Deploy to server via rsync
   - Clear cache and restart dashboard
   - Mark recommendation as 'applied' in analytics.db

## Key files
- Analytics DB: `data/analytics.db` (on server at `/home/edede/polymarket-weather-bot/data/analytics.db`)
- Config: `config.py`
- Trades: `data/trades.db`
- SSH: `ssh -i ~/.ssh/hetzner_ed25519 root@5.78.146.1`
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/optimize.md
git commit -m "feat: /optimize Claude Code skill for performance briefing"
```

---

### Task 10: Deploy everything to server

- [ ] **Step 1: Deploy all new files**

```bash
rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519' \
  analytics/ edede@5.78.146.1:~/polymarket-weather-bot/analytics/
rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519' \
  dashboard/api.py edede@5.78.146.1:~/polymarket-weather-bot/dashboard/api.py
rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519' \
  dashboard/static/ edede@5.78.146.1:~/polymarket-weather-bot/dashboard/static/
rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519' \
  daemon.py edede@5.78.146.1:~/polymarket-weather-bot/daemon.py
rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519' \
  config.py edede@5.78.146.1:~/polymarket-weather-bot/config.py
```

- [ ] **Step 2: Clear cache and restart**

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@5.78.146.1 'rm -rf /home/edede/polymarket-weather-bot/dashboard/__pycache__ /home/edede/polymarket-weather-bot/analytics/__pycache__ /home/edede/polymarket-weather-bot/__pycache__ && systemctl restart weather-dashboard'
```

- [ ] **Step 3: Run analytics manually to seed initial data**

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@5.78.146.1 'cd /home/edede/polymarket-weather-bot && PYTHONPATH=. .venv/bin/python3 -c "from analytics.optimizer import run_analytics; run_analytics()"'
```

- [ ] **Step 4: Verify dashboard shows analytics sections**

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: continuous optimization system — analytics engine, dashboard, alerts, skill"
```
