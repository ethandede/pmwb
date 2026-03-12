# Weather Prediction Market Trading System — Full Blueprint

**Date:** 2026-03-12
**Status:** Approved
**Approach:** Measure, Then Expand (Approach C)
**Working Style:** Pair programming, session by session
**Bankroll:** $500–$2,000 (moderate risk tolerance)

---

## Current System State

The project is a **production weather prediction market bot** trading live on Kalshi. Core capabilities:

- **Multi-model fusion forecasting:** Open-Meteo Ensemble (30-member), NOAA NWS, GFS/HRRR — bias-corrected via SQLite per city/month/model
- **Confidence-gated signal detection:** 10.5% edge threshold, 70% confidence threshold
- **Live Kalshi trading:** 20 high-temp + 7 low-temp markets, position sizing ($2/order, $10/scan caps), trailing stops, profit-taking, loss-cutting
- **5-minute daemon loop:** Position management + scanning + balance checks
- **Telegram alerts** for strong signals, **CSV logging** of all signals
- **Bias learning:** Daily resolver fetches actuals, updates per-model bias table

**Not yet built:** Backtesting framework, Kelly sizing, X/Twitter signals, precipitation/snowfall markets.

---

## Section 1: Backtesting & Calibration Framework

**Purpose:** Answer "is my model actually making money, and are my probability estimates well-calibrated?"

### Data Sources

- **Existing:** `logs/signals.csv` (all detected signals with timestamps, edges, model probs), `data/bias.db` (forecast vs actual per city/month/model)
- **New:** `data/trades.db` — SQLite table logging every Kalshi order fill (entry price, exit price, settlement, P&L). Trader already places orders; we capture fills.
- **Historical weather:** Open-Meteo Archive API (free, already used in resolver)
- **Historical market prices:** Start logging snapshots now and build forward. For older signals, use CSV log's `market_price` column.

### Core Metrics

| Metric | What it tells you |
|--------|-------------------|
| Brier Score | Overall probability calibration (lower = better, 0 = perfect) |
| Log Loss | Penalizes confident wrong predictions heavily |
| Calibration Curve | "When I say 70%, does it happen 70% of the time?" |
| P&L by city/bucket/model | Where is the edge coming from? |
| Edge decay | How does edge change from signal detection to settlement? |
| Hit rate by confidence tier | Are high-confidence signals actually better? |

### Architecture

```
backtesting/
├── __init__.py
├── data_loader.py      # Pulls from signals.csv, trades.db, bias.db
├── scorer.py           # Brier, log loss, calibration, P&L calculations
├── calibration.py      # Calibration curve fitting + Platt scaling
├── reports.py          # CLI + HTML report generation (matplotlib/rich)
└── walk_forward.py     # Time-series cross-validation (no lookahead)
```

### Walk-Forward Backtesting

- Expanding window: train bias model on days 1–N, test on day N+1, advance
- No lookahead contamination — bias corrections only use data available at forecast time
- Simulates what the system would have done historically using the signal log

### Key Design Decision

The backtester operates on the **existing signal log** for immediate results, but also supports **replay mode** where it re-runs the forecast pipeline against historical weather data for deeper analysis. Replay mode lets you test parameter changes (e.g., "what if edge threshold was 8% instead of 10.5%?").

---

## Section 2: Kelly Sizing & Risk Management

**Purpose:** Replace fixed $2/order, $10/scan caps with bankroll-aware sizing that grows with edge and shrinks with uncertainty.

### Kelly Criterion for Binary Markets

```
f* = (q - p) / (1 - p)    when betting YES
f* = (p - q) / p           when betting NO
```

Where `p` = market price, `q` = model probability. **Never bet full Kelly** — use fractional Kelly (0.25x–0.5x).

### Bankroll Tracking

- `risk/bankroll.py` tracks Kalshi account balance (via API) + mark-to-market of open positions
- **Effective bankroll** = cash balance + mark-to-market of open positions
- Kelly sizing uses effective bankroll as denominator
- Automatic recalculation each scan cycle

### Position Limits (Layered)

| Limit | Current | Proposed |
|-------|---------|----------|
| Per-order | $2 flat | Kelly-derived, capped at 3% of bankroll |
| Per-scan | $10 flat | 10% of bankroll |
| Per-city per-day | None | 5% of bankroll |
| Total exposure | None | 30% of bankroll |
| Per-market | None | Max 2 contracts on same event |

### Confidence-Adjusted Kelly

```
adjusted_f = f_kelly * fractional_kelly * (confidence / 100)
```

60% confidence → 60% of fractional Kelly. 95% confidence → near full fractional Kelly.

### Risk Controls

- **Drawdown circuit breaker:** Bankroll drops 15% from peak in rolling 7 days → halve all sizes for 48 hours
- **Correlation guard:** Total exposure to any single city/day capped regardless of bucket count
- **Minimum edge filter:** Don't trade if Kelly suggests < $0.50
- **Daily P&L stop:** Realized + unrealized hits -5% of bankroll → stop scanning until next day

### Architecture

```
risk/
├── __init__.py
├── bankroll.py         # Balance tracking, effective bankroll calculation
├── kelly.py            # Kelly fraction calculator for binary contracts
├── position_limits.py  # Layered limit checks (per-order, per-city, total)
├── circuit_breaker.py  # Drawdown detection, daily stop, cooldown logic
└── sizer.py            # Orchestrator: signal → dollar amount (or 0)
```

### Migration Path

- Start at 0.25x Kelly with current bankroll
- Graduate to 0.5x Kelly after backtester shows positive Brier score over 30+ days
- Old hard caps become absolute ceilings until trust is established

---

## Section 3: X/Twitter Signal Layer

**Purpose:** Extract hyper-local, real-time weather signals from X that official models miss — nowcasting corrections (2-6 hours before settlement), urban heat island effects, micro-climate anomalies, and ground-truth calibration.

### Why X Matters (2026 Context)

NWS/GFS models update every 6-12 hours. Between runs, conditions drift. People tweet real-time observations ("it's way hotter than forecast in downtown Manhattan", "snow sticking in Brooklyn already"). These give edge on same-day Kalshi/Polymarket buckets (especially NYC Central Park, Chicago Midway/O'Hare, Miami International Airport settlements).

### Data Acquisition Strategy (2026 Optimized)

- **MVP (lowest cost/risk):** Third-party provider first — TwitterAPI.io (~$0.15/1k tweets) or Apify Twitter Scraper. Higher volume, better neighborhood/geo support, optional streaming.
- **Fallback / Production:** Official X API Basic tier (~$100-200/mo for 10k-15k reads) or Pay-Per-Use (~$0.005/tweet read).
- **Budget target:** Start ~$15-30/mo (one city).
- **Polling cadence:** Every 20-30 min during active hours (6am-10pm local), or 4 daily scans (6am/11am/3pm/6pm) if using official API only.

### Architecture

```
x_signals/
├── __init__.py
├── client.py           # TwitterAPI.io / Apify wrapper, quota tracker, rate limiter
├── queries.py          # City-specific query builders (keyword + neighborhood targeting)
├── classifier.py       # NLP: classify tweets as weather-observation vs noise
├── features.py         # Extract structured features from classified tweets
├── anomaly.py          # Volume spike detection, sentiment shift detection
└── fusion.py           # Interface to multi_model.py — X as fourth source
```

### Query Design (Keyword + Neighborhood, Not point_radius)

Per city, per scan:
```
("NYC" OR "New York" OR Manhattan OR Brooklyn OR Queens OR "Central Park" OR Williamsburg)
(hot OR hotter OR degrees OR thermometer OR "feels like" OR snowing OR raining OR accumulation)
("right now" OR currently OR "just hit")
-is:retweet lang:en
```

City-specific boosters:
- NYC: `"Central Park" temperature`, `"feels like" Manhattan`
- Chicago: `"wind chill" OR O'Hare OR Midway`
- Miami: `"heat index" OR "so hot" Florida`

Precip/snow queries: `"inches of snow" OR "snowing hard"` + neighborhood names.

### Tweet Classification

- **Tier 1 (Day 1):** Rule-based regex + keywords. Filter retweets, bots (<10 followers), non-English.
- **Tier 2 (Week 2):** TF-IDF + logistic regression on 500 labeled examples. Categories: `temperature_observation`, `weather_complaint`, `forecast_commentary`, `noise`.
- **Tier 3 (Future):** Batch ambiguous/high-value tweets to Claude/Grok API for structured extraction `{reported_temp, unit, location_detail, confidence}`.

### Feature Engineering

| Feature | How | Use |
|---------|-----|-----|
| Reported temps | Regex + "feels like" handling + C/F conversion | Direct bias correction |
| Volume anomaly | Z-score vs 7-day rolling mean | Flags unusual events |
| Sentiment shift | "hotter/warmer" vs "colder/cooler" ratio | Directional signal |
| Forecast disagreement | "forecast wrong", "supposed to be" | Model confidence discount |
| Extreme keywords | "record", "never seen", "hottest ever" | Tail event indicator |
| Photo presence | Vision extraction (thermometer/snow depth) | Numerical readings |
| Account credibility | Verified + local news/meteorologists weighted 2-3x | Signal quality weighting |

### Fusion with Existing Models

X as fourth source with adaptive weighting:
- No data: 0%
- Low confidence: 5-10%
- High confidence (3+ corroborating tweets + volume spike): 15-25%
- Same-day + strong signal: up to 30% (X's biggest strength)

Confidence scoring: number of independent observations, account quality, consistency, recency (<4 hours ideal).

### Rate Limit & Quota Management

- Monthly cap with daily target (leaves buffer for month-end)
- If daily usage exceeds target, skip non-essential scans
- Switch to third-party if monthly spend > $20 or quota tight
- Emergency mode: only scan cities with open positions

### Noise Filtering

- No retweets (duplicates inflate volume)
- Minimum followers (>10)
- Recency: only tweets from last 4 hours for same-day signals
- Hash-based dedup on normalized text
- Sarcasm/hyperbole: if extracted temp >20°F from model forecast, flag for review

---

## Section 4: Precipitation & Snowfall Expansion

**Purpose:** Extend the proven temperature pipeline to precipitation and snowfall markets. These are less efficient than daily temps, so edge (especially X nowcasting) can be largest.

### Market Landscape (Kalshi + Polymarket, March 2026)

| Type | Settlement Source | Bucket Examples | Notes |
|------|------------------|-----------------|-------|
| Daily high/low temp | Specific NOAA station (CLI report) | 80-84°F, Above 85°F | 20+ cities, highest daily liquidity |
| Daily rain (binary) | NOAA station precip gauge | >0.00 inches today | Several major cities |
| Monthly precipitation | NOAA GHCND Climate Data Online + CLI | Above 1in, Above 3in this month | LA, NYC, SF, Miami, Houston, Chicago |
| Snowfall (monthly/event) | NWS Climatological Report (specific station) | Above 0.1in, Above 3.0in, Above 6.0in | Northeast + Chicago (huge storm volume) |

Settlement always uses first complete report; revisions ignored. Always check exact market rules page for linked station/report.

### Key Differences from Temperature

| Dimension | Temperature | Precipitation | Snowfall |
|-----------|-------------|---------------|----------|
| Distribution | Roughly Gaussian | Highly skewed (many 0s) | Extremely skewed |
| Predictability | High (2-3 day) | Moderate (timing hard) | Low (rain/snow line sensitive) |
| Settlement precision | 1°F | 0.01 inch | 0.1 inch |
| Model agreement | Usually tight | Often divergent | Very divergent |
| X signal value | Moderate | High | Very high (photos) |

### Forecast Data Sources

**Precipitation (priority order):**
1. Open-Meteo Ensemble (`precipitation_sum` — direct distribution)
2. NOAA NWS (PoP + QPF)
3. HRRR (convective nowcasting)
4. MRMS radar (Iowa State archive — gold for same-day)

**Snowfall:**
1. Open-Meteo snowfall (water equivalent)
2. NOAA NWS snow forecasts
3. Temperature-profile SLR model (dynamic 5:1 to 20:1+ — biggest error source)

### Bucket Mapping

**Standard bucket probability:**
```python
P(precip == 0) = 1 - PoP          # discrete mass at zero
P(0 < precip <= X) = PoP * P(amount <= X | amount > 0)
```

**"Above X inches" monthly contracts (survival function):**
```python
P(precip > X) = 1 - CDF(X | amount > 0) * PoP
```

Trace amounts ("T") always resolve as 0.00. Implement zero-inflated gamma distribution fitter.

### Architecture Changes

```
weather/
├── forecast.py          # MODIFY: add precip/snow fields to Open-Meteo calls
├── multi_model.py       # MODIFY: extend fuse_forecast() with market_type param
├── precip_model.py      # NEW: zero-inflated gamma fitting, bucket probability calc
├── snow_model.py        # NEW: snowfall estimation with dynamic SLR conversion
├── stations_config.py   # NEW: city × market type → exact GHCND station ID, CLI report link

kalshi/
├── scanner.py           # MODIFY: discover precip/snow markets
├── market_types.py      # NEW: enum + bucket parsers for temp/precip/snow
```

### Settlement Station Mapping

Pull exact station and report link from each market's rules page. Examples:
- Chicago O'Hare ORD for snow markets
- Central Park KNYC for NYC
- LAX GHCND:USW00023174 for LA rain

Create dynamic loader in `stations_config.py` — this alone can be the biggest settlement arbitrage.

### X Signal Value for Precip/Snow (Highest Leverage)

- **Rain:** "pouring now / just stopped at 3pm" for daily totals
- **Snow:** Photos of rulers/snow depth → vision extraction (Claude API or LLaVA)
- **Micro-geography:** Airport gauge might read 0.5" while downtown got 1.2"

### Rollout Strategy

1. Add precip fields to existing Open-Meteo/NWS pipeline (minimal work)
2. Build `precip_model.py` (zero-inflated gamma + ensemble spread)
3. Extend scanner for monthly + binary contracts
4. Map station IDs from Kalshi rules pages
5. Run signal-only mode 1-2 weeks for calibration
6. Add snowfall when monthly/storm markets heat up (SLR model + vision)

---

## Section 5: Phased Implementation Roadmap

**Approach:** Dependency-ordered phases. Each produces a working, testable increment. Built together session by session.

### Phase 1: Backtesting Framework + Calibration
*Prerequisite: None*

| File | What it does |
|------|-------------|
| `backtesting/__init__.py` | Package init |
| `backtesting/data_loader.py` | Ingest signals.csv, bias.db, Kalshi fill history |
| `backtesting/scorer.py` | Brier score, log loss, P&L calculation per signal |
| `backtesting/calibration.py` | Calibration curve + Platt scaling |
| `backtesting/walk_forward.py` | Expanding-window backtester, no-lookahead |
| `backtesting/reports.py` | CLI tables (Rich) + matplotlib calibration plots |
| `kalshi/trader.py` | MODIFY: log every fill to data/trades.db |
| `data/trades.db` | NEW: SQLite — ticker, side, entry_price, fill_time, settlement, pnl |

**Success criteria:**
- `python -m backtesting.reports` shows Brier score, hit rate by confidence tier, P&L by city
- Calibration curve plotted
- Walk-forward backtest produces realistic simulated P&L

### Phase 2: Kelly Sizing + Risk Management
*Prerequisite: Phase 1 (need Brier scores to set initial Kelly fraction)*

| File | What it does |
|------|-------------|
| `risk/__init__.py` | Package init |
| `risk/kelly.py` | Kelly fraction for binary contracts |
| `risk/bankroll.py` | Kalshi balance via API + mark-to-market |
| `risk/position_limits.py` | Per-order (3%), per-city-day (5%), per-scan (10%), total (30%) |
| `risk/circuit_breaker.py` | 15% drawdown → halve 48hr, daily -5% → stop |
| `risk/sizer.py` | Orchestrator: signal → dollar amount (or 0) |
| `kalshi/trader.py` | MODIFY: replace fixed caps with sizer.py |
| `config.py` | MODIFY: add KELLY_FRACTION, MAX_DRAWDOWN_PCT, DAILY_STOP_PCT |

**Success criteria:**
- Sensible sizing: bigger bets on high-edge, tiny/zero on marginal
- Circuit breaker tested in backtest simulation
- Kelly-sized vs flat-sized historical P&L comparison

### Phase 3: X/Twitter Signal Layer
*Prerequisite: Phase 1 (need calibration to measure X impact)*

| File | What it does |
|------|-------------|
| `x_signals/__init__.py` | Package init |
| `x_signals/client.py` | TwitterAPI.io / Apify wrapper, quota tracker |
| `x_signals/queries.py` | City-specific keyword + neighborhood queries |
| `x_signals/classifier.py` | Tier 1 regex rules, Tier 2 TF-IDF + logistic regression |
| `x_signals/features.py` | Reported temps, volume anomaly, sentiment, credibility scoring |
| `x_signals/anomaly.py` | Z-score volume spike detection |
| `x_signals/fusion.py` | Interface to multi_model.py (0-30% adaptive weight) |
| `weather/multi_model.py` | MODIFY: accept X features with confidence-gated weighting |
| `config.py` | MODIFY: X API keys, quota limits, polling cadence |

**Rollout:**
1. Build client + queries, inspect tweet quality for NYC
2. Tier 1 classifier, log features to CSV (don't trade yet)
3. 1 week parallel: compare accuracy with vs without X
4. Enable fusion if Brier score improves
5. Tier 2 classifier after 500+ labeled examples

### Phase 4: Precipitation & Snowfall Expansion
*Prerequisite: Phase 1 (backtesting)*

| File | What it does |
|------|-------------|
| `weather/precip_model.py` | Zero-inflated gamma, survival function |
| `weather/snow_model.py` | Dynamic SLR snowfall estimation |
| `weather/stations_config.py` | City × market type → GHCND station, CLI report link |
| `weather/forecast.py` | MODIFY: add precip/snow to Open-Meteo calls |
| `weather/multi_model.py` | MODIFY: market_type parameter |
| `kalshi/market_types.py` | Enum + bucket parsers per type |
| `kalshi/scanner.py` | MODIFY: discover precip/snow markets |
| `backtesting/scorer.py` | MODIFY: precip/snow scoring |

**Rollout:**
1. Add precip fields to Open-Meteo/NWS (minimal)
2. Build precip_model.py, test against historical
3. Map station IDs from rules pages
4. Scanner discovers monthly + binary contracts
5. Signal-only 1-2 weeks
6. Snowfall when markets appear

### Phase 5: Production Hardening + Polymarket
*Prerequisite: Phases 1-4*

| File | What it does |
|------|-------------|
| `x_signals/vision.py` | Snow depth / thermometer photo extraction via Claude API |
| `x_signals/classifier.py` | MODIFY: Tier 3 LLM extraction |
| `polymarket/trader.py` | MODIFY: re-enable execution |
| `daemon.py` | MODIFY: unified loop, all market types + X polling |
| `alerts/telegram_alert.py` | MODIFY: richer alerts with Kelly size, X signal summary |
| `monitoring/dashboard.py` | NEW: live P&L, positions, model performance, X quota |
| `risk/circuit_breaker.py` | MODIFY: correlation-aware drawdown |

### Tech Stack & Monthly Cost

| Component | Tool | Cost |
|-----------|------|------|
| Weather data | Open-Meteo, NOAA NWS, MRMS | Free |
| X/Twitter data | TwitterAPI.io / Apify (MVP) | $15-30 |
| Exchange APIs | Kalshi, Polymarket (Gamma) | Free |
| ML | scikit-learn, numpy, pandas | Free |
| Vision (Phase 5) | Claude API (batch) | ~$5-10 |
| **Total** | | **~$25-40/mo** |

### Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| X API rate limits / quota exhaustion | Third-party fallback, emergency mode, quota tracker |
| Thin liquidity on precip/snow | Minimum liquidity filter, wider limits, skip illiquid |
| Model drift (seasonal bias changes) | Continuous bias learning, monthly recalibration alerts |
| Station methodology changes | Monitor rules pages, alert on station ID changes |
| Correlated weather events | Per-city-day caps, total exposure limit, correlation-aware breaker |
| Overfitting to historical signals | Walk-forward only, out-of-sample holdout |
