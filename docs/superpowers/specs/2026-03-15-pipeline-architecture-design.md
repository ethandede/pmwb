# Pipeline Architecture Redesign

**Date:** 2026-03-15
**Status:** Approved
**Approach:** Data-driven pipeline with MarketConfig (Approach A)

## Problem

The trading system grew organically around temperature markets. Precipitation and ERCOT were bolted on with shared state and implicit branching. This caused:

- Temperature sanity checker comparing GFS temp data against precipitation thresholds (blocked all precip trades)
- Single scan budget shared across market types (temp starved precip every cycle)
- Circuit breaker treating order costs as realized losses (blocked all trading after ~$15 of buys)
- Dashboard reading live Kalshi API in paper mode instead of trades.db
- Deploy script overwriting live database files with stale local copies

Each bug stemmed from the same root: no separation between market types at the architectural level.

## Design

### Core Concept

One pipeline, multiple configs. Each market type is a `MarketConfig` that provides function pointers and thresholds to generic pipeline stages. The stages themselves are market-type-agnostic.

```
MarketConfig → PipelineRunner → [fetch → score → filter → sanity → size → execute]
                                         ↑ per-config function pointers
```

### MarketConfig

```python
@dataclass
class MarketConfig:
    name: str                          # "kalshi_temp", "kalshi_precip", "ercot"
    display_name: str                  # "Kalshi Temperature", etc.

    # Stage 1: Market discovery
    exchange: str                      # "kalshi" or "ercot"
    fetch_fn: Callable
    series: dict
    bucket_parser: Callable | None

    # Stage 2: Forecast & signal
    forecast_fn: Callable
    fusion_weights: dict | None

    # Stage 3: Signal filtering
    edge_gate: float
    confidence_gate: float
    sameday_overrides: dict | None     # {"edge", "confidence", "kelly_floor"} or None

    # Stage 4: Sanity check
    sanity_fn: Callable | None         # None = skip sanity check

    # Stage 5: Sizing
    scan_frac: float
    kelly_floor: float
    max_contracts_per_event: int

    # Stage 6: Execution
    execute_fn: Callable
    pricing_fn: Callable | None

    # Stage 7: Position management
    manage_fn: Callable
    exit_rules: dict

    # Stage 8: Settlement
    settlement_timeline: str           # "daily", "monthly", "hourly"
    settle_fn: Callable
```

Note: `budget_key` removed from earlier draft. CycleState is per-config, so budget isolation is structural — no key needed.

### Signal Dataclass

Typed data structure flowing between stages. Replaces raw dicts with `_market_type` flags.

```python
@dataclass
class Signal:
    ticker: str
    city: str
    market_type: str
    side: str                          # "yes" or "no"
    model_prob: float
    market_prob: float
    edge: float
    confidence: float
    price_cents: int
    days_ahead: int                    # 0 = same-day, used for sameday_overrides

    # Execution-relevant fields (promoted from raw dict for type safety)
    yes_bid: int | None = None
    yes_ask: int | None = None
    lat: float | None = None
    lon: float | None = None

    # Raw exchange data (fallback for exchange-specific fields)
    market: dict = field(default_factory=dict)

    # Enriched by later stages
    size: SizeResult | None = None
    trade_result: TradeResult | None = None
```

The `market` dict stays attached as a fallback, but pipeline stages use the typed fields. The score stage promotes critical fields from the raw dict into typed Signal fields at creation time.

### CycleState

Per-config, per-cycle mutable state. Created fresh each cycle, never leaks between market types.

```python
@dataclass
class CycleState:
    scan_spent: float = 0.0
    signals: list = field(default_factory=list)
    trades_attempted: int = 0
    errors: list = field(default_factory=list)
```

### PipelineRunner

Replaces the monolithic scanner + trader globals. Holds shared state (one Kalshi account, one risk policy), dispatches per-config.

```python
class PipelineRunner:
    def __init__(self, configs: list[MarketConfig], exchanges: dict):
        self.configs = configs
        self.exchanges = exchanges     # {"kalshi": KalshiExchange(), "ercot": ErcotExchange()}
        self.bankroll: BankrollTracker  # shared
        self.circuit_breaker: CircuitBreaker  # shared

    def run_cycle(self, paper_mode: bool):
        """Called by daemon every 5 min."""
        self.bankroll.sync_from_api()

        # Pre-dispatch: fetch shared state once
        kalshi = self.exchanges["kalshi"]
        held_positions = kalshi.get_positions()
        resting_tickers = {o["ticker"] for o in kalshi.get_orders(status="resting")
                           if o.get("action") == "buy"}
        kalshi_position_count = sum(1 for p in held_positions
                                     if float(p.get("position_fp", 0)) != 0)

        for config in self.configs:
            # Cross-config position limit: skip Kalshi configs when maxed
            if config.exchange == "kalshi" and kalshi_position_count >= MAX_POSITIONS_TOTAL:
                continue

            state = CycleState()
            exchange = self.exchanges[config.exchange]

            try:
                markets  = fetch_markets(config, exchange)
                signals  = [score_signal(config, m) for m in markets]
                filtered = filter_signals(config, signals, held_positions,
                                          resting_tickers)
                for signal in filtered:
                    if sanity_check(config, signal):
                        signal.size = size_position(config, signal,
                                                    self.bankroll,
                                                    self.circuit_breaker,
                                                    state)
                        if signal.size and signal.size.count > 0:
                            execute_trade(config, signal, signal.size,
                                          exchange, paper_mode)
                            state.scan_spent += signal.size.dollar_amount
            except Exception as e:
                state.errors.append(str(e))
                print(f"  Pipeline error ({config.name}): {e}")

        # Post-cycle phases (run once, not per config)
        self._poll_fills(kalshi)       # update resting orders with fill data
        self._manage_positions()       # exits, fortifies, holds
        self._settle()                 # resolve settled markets
        self._snapshot_equity()        # daily equity record
```

Key design decisions:
- `held_positions` and `resting_tickers` are fetched once in pre-dispatch and shared across configs.
- `kalshi_position_count` gates all Kalshi configs when the global 50-position limit is hit.
- Errors in one config's pipeline do not abort other configs — caught, logged to `CycleState.errors`, and the next config runs.
- `_poll_fills()` runs once per cycle after all execution, since it's a Kalshi-global operation that checks for resting order fills and updates trades.db.
- `paper_mode` is passed as a parameter, not imported from config.py.

### Exchange Adapters

Thin wrappers around exchange-specific I/O. Stateless except credentials/connections.

```python
class KalshiExchange:
    def get_balance(self) -> dict
    def get_positions(self) -> list[dict]
    def get_orders(self, status="resting") -> list[dict]
    def place_order(self, ticker, side, price_cents, count) -> dict
    def get_market(self, ticker) -> dict    # used by settler + position manager
    def fetch_events(self, series_ticker) -> list[dict]
    def poll_fills(self) -> list[dict]      # check resting orders for fills

class ErcotExchange:
    def fetch_market_data(self) -> dict     # prices, solar, load (cached 5min)
    def get_positions(self) -> list[dict]   # from ercot_paper.db
    def open_position(self, hub, signal, size, edge, confidence) -> dict
    def close_position(self, position_id, exit_reason, exit_price) -> dict
```

`KalshiExchange.get_market(ticker)` replaces the private `_get` import that `kalshi/settler.py` currently uses. The settler will receive the exchange adapter and call `exchange.get_market(ticker)` instead.

The dashboard instantiates its own `KalshiExchange` at import time (stateless — just credentials + HTTP), same pattern as the current direct imports.

### Pipeline Stages

Plain functions. No classes, no globals. Each takes config + inputs, returns outputs.

```python
def fetch_markets(config, exchange) -> list[dict]
def score_signal(config, market) -> Signal
def filter_signals(config, signals, held_positions, resting_tickers) -> list[Signal]
def sanity_check(config, signal) -> bool
def size_position(config, signal, bankroll, circuit_breaker, cycle_state) -> SizeResult
def execute_trade(config, signal, size, exchange, paper_mode) -> TradeResult
```

Stage details:

**score_signal** — Calls `config.forecast_fn`, computes edge/confidence, promotes typed fields (ticker, yes_bid, yes_ask, lat, lon, days_ahead) from the raw market dict into the Signal dataclass.

**filter_signals** — Applies in order: (1) edge gate (using `config.sameday_overrides` when `signal.days_ahead == 0`), (2) confidence gate, (3) liquidity gate (volume/OI minimum), (4) re-entry cooldown (recently sold tickers from trades.db), (5) resting order dedup (skip if ticker in `resting_tickers`), (6) cross-contract consistency (temp only — sorts by rank_score, iterates greedily with accumulator to prevent contradictory bets on same city/date).

**sanity_check** — Returns True if `config.sanity_fn is None`. Otherwise calls `config.sanity_fn(signal)`.

**size_position** — Calls existing `risk/sizer.py:compute_size()` with config's `scan_frac` and `cycle_state.scan_spent`. Applies 2% bankroll hard cap.

**execute_trade** — Determines maker/taker price via `config.pricing_fn`. Checks fee-adjusted profit gate ($0.12 minimum). If `paper_mode`, logs to trades.db without API call. Otherwise calls `config.execute_fn` via exchange adapter.

Cross-cutting concerns handled in stages:
- Cross-contract consistency check: filter stage (temp only — config drives whether this runs)
- Re-entry cooldown: filter stage (queries recently sold tickers from trades.db)
- Liquidity gate: filter stage (volume/OI minimum)
- Resting order dedup: filter stage (skip tickers with existing resting buy orders)
- Fee-adjusted profit gate: execute stage ($0.12 minimum expected profit)
- 2% bankroll hard cap: size stage
- Trailing stops: manage stage

## Config Instances

### KALSHI_TEMP

```python
KALSHI_TEMP = MarketConfig(
    name="kalshi_temp",
    display_name="Kalshi Temperature",
    exchange="kalshi",
    fetch_fn=get_kalshi_weather_markets,
    series=WEATHER_SERIES,
    bucket_parser=parse_kalshi_bucket,
    forecast_fn=fuse_forecast,
    fusion_weights={"ensemble": 0.40, "noaa": 0.35, "hrrr": 0.25},
    edge_gate=0.12,
    confidence_gate=60,
    sameday_overrides={"edge": 0.05, "confidence": 45, "kelly_floor": 0.35},
    sanity_fn=gfs_temp_sanity,
    scan_frac=0.10,
    kelly_floor=0.25,
    max_contracts_per_event=10,
    execute_fn=execute_kalshi_order,
    pricing_fn=choose_price_strategy,
    manage_fn=evaluate_kalshi_position,
    exit_rules={"reversal_edge": -0.08, "sameday_reversal": -0.15,
                "profit_take_pct": 0.88, "min_confidence": 70},
    settlement_timeline="daily",
    settle_fn=resolve_kalshi_trade,
)
```

### KALSHI_PRECIP

```python
KALSHI_PRECIP = MarketConfig(
    name="kalshi_precip",
    display_name="Kalshi Precipitation",
    exchange="kalshi",
    fetch_fn=get_kalshi_precip_markets,
    series=PRECIP_SERIES,
    bucket_parser=parse_precip_bucket,
    forecast_fn=fuse_precip_forecast,
    fusion_weights={"ensemble": 0.50, "noaa": 0.30},
    edge_gate=0.07,
    confidence_gate=60,
    sameday_overrides=None,
    sanity_fn=None,
    scan_frac=0.10,
    kelly_floor=0.25,
    max_contracts_per_event=10,
    execute_fn=execute_kalshi_order,
    pricing_fn=choose_price_strategy,
    manage_fn=evaluate_kalshi_position,
    exit_rules={"reversal_edge": -0.10, "profit_take_pct": 0.90,
                "min_confidence": 60},
    settlement_timeline="monthly",
    settle_fn=resolve_kalshi_trade,
)
```

### ERCOT

```python
ERCOT = MarketConfig(
    name="ercot",
    display_name="ERCOT Solar",
    exchange="ercot",
    fetch_fn=fetch_ercot_markets,       # split from scan_all_hubs (raw hub data only)
    series=ERCOT_HUBS,
    bucket_parser=None,
    forecast_fn=get_ercot_solar_signal,  # scoring happens in score_signal stage
    fusion_weights=None,
    edge_gate=0.005,
    confidence_gate=50,
    sameday_overrides=None,
    sanity_fn=None,
    scan_frac=0.10,
    kelly_floor=0.25,
    max_contracts_per_event=3,
    execute_fn=execute_ercot_paper,
    pricing_fn=None,
    manage_fn=evaluate_ercot_position,
    exit_rules={"edge_decay_pct": 0.30, "signal_flip": True,
                "ttl_hours": 24},
    settlement_timeline="hourly",
    settle_fn=expire_ercot_position,
)
```

Note: `scan_all_hubs` is split into `fetch_ercot_markets` (returns raw hub data + ERCOT market data) and `get_ercot_solar_signal` (used as `forecast_fn` in the score stage). This preserves the 5-minute cache for ERCOT market data while fitting the two-stage fetch/score pipeline.

## Migration Plan

### New Files

| File | Purpose |
|------|---------|
| `pipeline/__init__.py` | Package init |
| `pipeline/config.py` | MarketConfig dataclass + 3 config instances |
| `pipeline/runner.py` | PipelineRunner — cycle orchestrator |
| `pipeline/stages.py` | 6 stage functions |
| `pipeline/types.py` | Signal, CycleState, TradeResult dataclasses |
| `exchanges/__init__.py` | Package init |
| `exchanges/kalshi.py` | KalshiExchange — API auth, orders, positions, balance |
| `exchanges/ercot.py` | ErcotExchange — paper DB, positions, market data |

### Replaced Files

| File | Replaced by |
|------|-------------|
| `kalshi/trader.py` | `exchanges/kalshi.py` + `pipeline/stages.py` + `pipeline/runner.py` |
| `scanner.py` | `pipeline/runner.py` + `pipeline/stages.py` |

### Modified Files

| File | Change |
|------|--------|
| `daemon.py` | Instantiate PipelineRunner, call `runner.run_cycle(paper_mode)` |
| `config.py` | Remove market-type constants that moved into MarketConfig. Keep shared values (PAPER_MODE, FRACTIONAL_KELLY, MAX_POSITIONS_TOTAL, etc.) |
| `kalshi/position_manager.py` | Receive exchange adapter instead of importing from `kalshi/trader.py` |
| `kalshi/settler.py` | Receive exchange adapter. Replace `_get` import with `exchange.get_market(ticker)` |
| `dashboard/api.py` | Instantiate own `KalshiExchange` (stateless). Query via adapter instead of importing from `kalshi/trader.py` |
| `ercot/hubs.py` | Split `scan_all_hubs` into `fetch_ercot_markets` (raw data) + keep solar signal as separate function |

### Unchanged Files

| File | Why |
|------|-----|
| `risk/sizer.py` | Already takes explicit inputs |
| `risk/kelly.py` | Pure function |
| `risk/circuit_breaker.py` | Clean class |
| `risk/bankroll.py` | Clean class |
| `risk/position_limits.py` | Pure function |
| `weather/multi_model.py` | Forecast functions stay, configs point to them |
| `weather/forecast.py` | Data fetching layer |
| `weather/precip_model.py` | Gamma distribution model |
| `kalshi/scanner.py` | Market fetching functions stay, configs point to them |
| `kalshi/pricing.py` | Pricing strategy stays, configs point to it |
| `ercot/position_manager.py` | Logic stays, gets exchange adapter passed in |

### Out of Scope

- **Polymarket** — Current Polymarket scanning (scanner.py lines 108-197) is effectively deprecated and removed in this migration. Will be added as a fourth MarketConfig when ready.
- **Structured logging** — Current print-based logging is carried over. Structured logging can be added later as a pipeline-level concern.

## Testing Strategy

### Unit Tests (per stage)

```
test_stages.py:
  test_score_signal_temp()
  test_score_signal_precip()
  test_filter_edge_gate()
  test_filter_sameday_override()
  test_filter_cross_contract()
  test_filter_reentry_cooldown()
  test_filter_resting_order_dedup()
  test_sanity_check_temp()
  test_sanity_check_precip_skipped()
  test_size_respects_budget()
  test_size_circuit_breaker()
  test_size_2pct_hard_cap()
  test_execute_paper_mode()
  test_execute_fee_gate()
```

### Integration Tests (per config, mock exchange)

```
test_pipeline_temp.py:
  test_full_cycle_temp()
  test_budget_isolation()

test_pipeline_precip.py:
  test_full_cycle_precip()

test_pipeline_ercot.py:
  test_full_cycle_ercot()

test_pipeline_runner.py:
  test_cross_config_position_limit()
  test_error_in_one_config_doesnt_abort_others()
  test_fill_polling_runs_once_per_cycle()
```

### What stays untested (already covered)

The `risk/` and `weather/` modules have existing tests and are not changing.

## Error Handling

- Errors in `score_signal` or `sanity_check` for one signal are caught, logged to `CycleState.errors`, and the signal is skipped. Processing continues.
- Errors in one config's entire pipeline are caught at the config level. Other configs still run.
- Exchange adapter errors (API failures, timeouts) are caught in the adapter and returned as error results, not raised.
- The daemon loop's existing error handling around `run_cycle` remains as a final safety net.

## Success Criteria

1. Adding a new market type requires only a MarketConfig + adapter, no pipeline changes
2. Bugs in one market type cannot affect another (no shared mutable state between configs)
3. Each pipeline stage is testable in isolation with mock inputs
4. Dashboard and analytics work uniformly across market types
5. ERCOT stays connected to pipeline improvements
6. All existing tests continue to pass
7. Paper trading produces the same signals as the current system (regression check)
