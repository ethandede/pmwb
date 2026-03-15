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
    budget_key: str
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
    market: dict                       # raw exchange data for execution

    size: SizeResult | None = None
    trade_result: TradeResult | None = None
```

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

    def run_cycle(self):
        self.bankroll.sync_from_api()

        for config in self.configs:
            state = CycleState()
            exchange = self.exchanges[config.exchange]
            markets  = fetch_markets(config, exchange)
            signals  = [score_signal(config, m) for m in markets]
            filtered = filter_signals(config, signals, held_positions)
            for signal in filtered:
                if sanity_check(config, signal):
                    signal.size = size_position(config, signal, self.bankroll,
                                                self.circuit_breaker, state)
                    if signal.size and signal.size.count > 0:
                        execute_trade(config, signal, signal.size, exchange, paper_mode)
                        state.scan_spent += signal.size.dollar_amount

        self._manage_positions()
        self._settle()
        self._snapshot_equity()
```

Cross-config constraints (total Kalshi position limit of 50) are checked in the runner before dispatching configs.

### Exchange Adapters

Thin wrappers around exchange-specific I/O. Stateless except credentials/connections.

```python
class KalshiExchange:
    def get_balance(self) -> dict
    def get_positions(self) -> list[dict]
    def get_orders(self, status="resting") -> list[dict]
    def place_order(self, ticker, side, price_cents, count) -> dict
    def get_market(self, ticker) -> dict
    def fetch_events(self, series_ticker) -> list[dict]

class ErcotExchange:
    def fetch_market_data(self) -> dict
    def get_positions(self) -> list[dict]
    def open_position(self, hub, signal, size, edge, confidence) -> dict
    def close_position(self, position_id, exit_reason, exit_price) -> dict
```

### Pipeline Stages

Plain functions. No classes, no globals. Each takes config + inputs, returns outputs.

```python
def fetch_markets(config, exchange) -> list[dict]
def score_signal(config, market) -> Signal
def filter_signals(config, signals, held_positions) -> list[Signal]
def sanity_check(config, signal) -> bool
def size_position(config, signal, bankroll, circuit_breaker, cycle_state) -> SizeResult
def execute_trade(config, signal, size, exchange, paper_mode) -> TradeResult
```

Cross-cutting concerns handled in stages:
- Cross-contract consistency check: filter stage (temp only, driven by config)
- Re-entry cooldown: filter stage (queries recently sold tickers)
- Liquidity gate: filter stage (volume/OI minimum)
- Fee-adjusted profit gate: execute stage ($0.12 minimum)
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
    budget_key="temp",
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
    budget_key="precip",
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
    fetch_fn=scan_all_hubs,
    series=ERCOT_HUBS,
    bucket_parser=None,
    forecast_fn=get_ercot_solar_signal,
    fusion_weights=None,
    edge_gate=0.005,
    confidence_gate=50,
    sameday_overrides=None,
    sanity_fn=None,
    budget_key="ercot",
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

## Migration Plan

### New Files

| File | Purpose |
|------|---------|
| `pipeline/config.py` | MarketConfig dataclass + 3 config instances |
| `pipeline/runner.py` | PipelineRunner — cycle orchestrator |
| `pipeline/stages.py` | 6 stage functions |
| `pipeline/types.py` | Signal, CycleState, TradeResult dataclasses |
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
| `daemon.py` | Instantiate PipelineRunner, call `runner.run_cycle()` |
| `config.py` | Remove market-type constants that moved into MarketConfig |
| `kalshi/position_manager.py` | Receive exchange adapter instead of importing from trader |
| `kalshi/settler.py` | Receive exchange adapter |
| `dashboard/api.py` | Query exchange adapters instead of importing from trader |

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
| `ercot/hubs.py` | Hub scanning stays, config points to scan_all_hubs |
| `ercot/position_manager.py` | Logic stays, gets exchange adapter passed in |

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
  test_sanity_check_temp()
  test_sanity_check_precip_skipped()
  test_size_respects_budget()
  test_size_circuit_breaker()
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
```

### What stays untested (already covered)

The `risk/` and `weather/` modules have existing tests and are not changing.

## Success Criteria

1. Adding a new market type requires only a MarketConfig + adapter, no pipeline changes
2. Bugs in one market type cannot affect another (no shared mutable state)
3. Each pipeline stage is testable in isolation with mock inputs
4. Dashboard and analytics work uniformly across market types
5. ERCOT stays connected to pipeline improvements
6. All existing tests continue to pass
7. Paper trading produces the same signals as the current system (regression check)
