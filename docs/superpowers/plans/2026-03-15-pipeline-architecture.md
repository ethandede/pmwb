# Pipeline Architecture Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace monolithic scanner + god module with data-driven pipeline where each market type is a config, not a code path.

**Architecture:** MarketConfig dataclasses with function pointers feed generic pipeline stage functions. PipelineRunner orchestrates per-config cycles with isolated CycleState. Exchange adapters wrap Kalshi API and ERCOT paper DB.

**Tech Stack:** Python 3.13, dataclasses, SQLite, FastAPI, existing risk/ and weather/ modules unchanged.

**Spec:** `docs/superpowers/specs/2026-03-15-pipeline-architecture-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `pipeline/__init__.py` | Package init |
| `pipeline/types.py` | Signal, CycleState, TradeResult dataclasses |
| `pipeline/config.py` | MarketConfig dataclass + KALSHI_TEMP, KALSHI_PRECIP, ERCOT instances |
| `pipeline/stages.py` | 6 stage functions: fetch, score, filter, sanity, size, execute |
| `pipeline/runner.py` | PipelineRunner class — cycle orchestrator |
| `exchanges/__init__.py` | Package init |
| `exchanges/kalshi.py` | KalshiExchange — API auth, signing, orders, positions, balance |
| `exchanges/ercot.py` | ErcotExchange — wraps ercot/paper_trader.py + ercot/hubs.py |
| `tests/test_pipeline_types.py` | Tests for Signal, CycleState |
| `tests/test_pipeline_stages.py` | Tests for each stage function |
| `tests/test_pipeline_runner.py` | Integration tests for PipelineRunner |
| `tests/test_exchanges.py` | Tests for exchange adapters |

### Modified Files

| File | Change |
|------|--------|
| `daemon.py` | Replace imports of scanner/trader/settler with PipelineRunner |
| `kalshi/settler.py` | Replace `_get` import with exchange adapter parameter |
| `kalshi/position_manager.py` | Replace trader imports with exchange adapter parameter |
| `dashboard/api.py` | Replace trader imports with KalshiExchange |
| `ercot/hubs.py` | Split `scan_all_hubs` into `fetch_ercot_markets` + keep solar signal separate |

### Replaced Files (deleted after migration verified)

| File | Replaced by |
|------|-------------|
| `kalshi/trader.py` | `exchanges/kalshi.py` + `pipeline/stages.py` + `pipeline/runner.py` |
| `scanner.py` | `pipeline/runner.py` + `pipeline/stages.py` |

### Unchanged Files

`risk/sizer.py`, `risk/kelly.py`, `risk/circuit_breaker.py`, `risk/bankroll.py`, `risk/position_limits.py`, `weather/multi_model.py`, `weather/forecast.py`, `weather/precip_model.py`, `kalshi/scanner.py`, `kalshi/pricing.py`, `kalshi/fill_tracker.py`, `ercot/position_manager.py`, `ercot/paper_trader.py`

---

## Chunk 1: Foundation Types and Exchange Adapters

### Task 1: Pipeline types (Signal, CycleState, TradeResult)

**Files:**
- Create: `pipeline/__init__.py`
- Create: `pipeline/types.py`
- Create: `tests/test_pipeline_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_types.py
from pipeline.types import Signal, CycleState, TradeResult


def test_signal_creation():
    """Signal can be created with required fields."""
    s = Signal(
        ticker="KXHIGHNY-26MAR15-T56",
        city="nyc",
        market_type="kalshi_temp",
        side="no",
        model_prob=0.30,
        market_prob=0.55,
        edge=-0.25,
        confidence=72.0,
        price_cents=55,
        days_ahead=0,
    )
    assert s.ticker == "KXHIGHNY-26MAR15-T56"
    assert s.side == "no"
    assert s.size is None
    assert s.trade_result is None


def test_signal_optional_fields():
    """Signal optional fields default to None."""
    s = Signal(
        ticker="T", city="c", market_type="mt", side="yes",
        model_prob=0.5, market_prob=0.5, edge=0.0,
        confidence=50.0, price_cents=50, days_ahead=1,
    )
    assert s.yes_bid is None
    assert s.yes_ask is None
    assert s.lat is None
    assert s.lon is None
    assert s.market == {}


def test_cycle_state_fresh():
    """CycleState starts with zero counters."""
    cs = CycleState()
    assert cs.scan_spent == 0.0
    assert cs.signals == []
    assert cs.trades_attempted == 0
    assert cs.errors == []
    assert cs.signals_scored == 0
    assert cs.signals_filtered == 0
    assert cs.trades_executed == 0
    assert cs.total_edge == 0.0


def test_cycle_state_isolation():
    """Two CycleState instances do not share state."""
    a = CycleState()
    b = CycleState()
    a.scan_spent = 10.0
    a.signals.append("x")
    assert b.scan_spent == 0.0
    assert b.signals == []


def test_trade_result_creation():
    """TradeResult captures execution outcome."""
    tr = TradeResult(
        ticker="KXHIGHNY-26MAR15-T56",
        side="no",
        count=5,
        price_cents=55,
        cost=2.75,
        order_id="abc-123",
        status="executed",
        paper=True,
    )
    assert tr.cost == 2.75
    assert tr.paper is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# pipeline/__init__.py
# Pipeline package
```

```python
# pipeline/types.py
"""Core data types for the trading pipeline."""

from dataclasses import dataclass, field


@dataclass
class Signal:
    """Typed data flowing between pipeline stages.

    Created by score_signal, enriched by size/execute stages.
    Replaces raw market dicts with _market_type flags.
    """
    ticker: str
    city: str
    market_type: str                   # "kalshi_temp", "kalshi_precip", "ercot"
    side: str                          # "yes" or "no"
    model_prob: float
    market_prob: float
    edge: float
    confidence: float
    price_cents: int
    days_ahead: int                    # 0 = same-day

    # Execution-relevant fields (promoted from raw dict for type safety)
    yes_bid: int | None = None
    yes_ask: int | None = None
    lat: float | None = None
    lon: float | None = None

    # Raw exchange data (fallback for exchange-specific fields)
    market: dict = field(default_factory=dict)

    # Enriched by later stages
    size: "SizeResult | None" = None
    trade_result: "TradeResult | None" = None


@dataclass
class CycleState:
    """Per-config, per-cycle mutable state. Created fresh, never leaks."""
    scan_spent: float = 0.0
    signals: list = field(default_factory=list)
    trades_attempted: int = 0
    errors: list = field(default_factory=list)

    # Observability metrics
    signals_scored: int = 0
    signals_filtered: int = 0
    trades_executed: int = 0
    total_edge: float = 0.0


@dataclass
class TradeResult:
    """Outcome of an execute_trade call."""
    ticker: str
    side: str
    count: int
    price_cents: int
    cost: float
    order_id: str
    status: str                        # "executed", "resting", "paper"
    paper: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline_types.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/__init__.py pipeline/types.py tests/test_pipeline_types.py
git commit -m "feat: pipeline types — Signal, CycleState, TradeResult dataclasses"
```

---

### Task 2: KalshiExchange adapter

**Files:**
- Create: `exchanges/__init__.py`
- Create: `exchanges/kalshi.py`
- Create: `tests/test_exchanges.py`

The adapter extracts API functions from `kalshi/trader.py` (lines 20-111: credential loading, request signing, GET/POST helpers, and the public wrappers). No business logic — just I/O.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exchanges.py
"""Tests for exchange adapters. Uses mocks — no real API calls."""
from unittest.mock import patch, MagicMock
from exchanges.kalshi import KalshiExchange


def test_kalshi_get_balance():
    """get_balance returns cash + portfolio from API."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"balance": 17729, "portfolio_value": 4449}
        bal = exchange.get_balance()
    assert bal["balance"] == 17729
    assert bal["portfolio_value"] == 4449


def test_kalshi_get_positions():
    """get_positions returns market_positions list."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"market_positions": [{"ticker": "T1"}, {"ticker": "T2"}]}
        positions = exchange.get_positions()
    assert len(positions) == 2
    assert positions[0]["ticker"] == "T1"


def test_kalshi_get_orders():
    """get_orders returns orders list filtered by status."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"orders": [{"ticker": "T1", "status": "resting"}]}
        orders = exchange.get_orders(status="resting")
    assert len(orders) == 1


def test_kalshi_get_market():
    """get_market returns single market dict for settler."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"market": {"ticker": "T1", "status": "finalized", "result": "yes"}}
        market = exchange.get_market("T1")
    assert market["status"] == "finalized"
    assert market["result"] == "yes"


def test_kalshi_place_order():
    """place_order sends POST with correct body."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_post_order') as mock_post:
        mock_post.return_value = {"order": {"order_id": "abc", "status": "executed"}}
        result = exchange.place_order("T1", "buy", "no", 55, 5)
    mock_post.assert_called_once_with("T1", "buy", "no", 55, 5)
    assert result["order"]["status"] == "executed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_exchanges.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'exchanges'`

- [ ] **Step 3: Write minimal implementation**

Extract from `kalshi/trader.py` lines 10-111 (credentials, signing, HTTP helpers) into a class. Keep the same logic, just wrap it.

```python
# exchanges/__init__.py
# Exchange adapters package
```

```python
# exchanges/kalshi.py
"""KalshiExchange — thin wrapper around Kalshi REST API.

Handles authentication, request signing, and order management.
Stateless except for lazily-loaded credentials.
"""

import os
import time
import base64
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


_BASE_URL = "https://api.elections.kalshi.com"


class KalshiExchange:
    def __init__(self):
        self._key_id: str | None = None
        self._private_key = None

    def _load_credentials(self):
        if self._key_id is not None:
            return
        from dotenv import load_dotenv
        load_dotenv()
        self._key_id = os.environ.get("KALSHI_API_KEY_ID", "")
        pk_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        if pk_path and os.path.exists(pk_path):
            with open(pk_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(f.read(), password=None)

    def _sign_request(self, method: str, path: str) -> dict:
        self._load_credentials()
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}"
        signature = self._private_key.sign(
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        full_path = path.split("?")[0]
        headers = self._sign_request("GET", full_path)
        resp = requests.get(f"{_BASE_URL}{path}", headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post_order(self, ticker: str, action: str, side: str, price_cents: int, count: int) -> dict:
        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_request("POST", path)
        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "type": "limit",
            "count": count,
        }
        if side == "yes":
            body["yes_price"] = price_cents
        else:
            body["no_price"] = price_cents
        resp = requests.post(f"{_BASE_URL}{path}", headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # --- Public API ---

    def get_balance(self) -> dict:
        return self._get("/trade-api/v2/portfolio/balance")

    def get_positions(self, limit: int = 100, settlement_status: str = "unsettled") -> list:
        data = self._get("/trade-api/v2/portfolio/positions", {
            "limit": limit,
            "settlement_status": settlement_status,
        })
        return data.get("market_positions", [])

    def get_orders(self, limit: int = 50, status: str = "resting") -> list:
        data = self._get("/trade-api/v2/portfolio/orders", {"limit": limit, "status": status})
        return data.get("orders", [])

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market's data (for settler + position manager)."""
        data = self._get(f"/trade-api/v2/markets/{ticker}")
        return data.get("market", {})

    def place_order(self, ticker: str, action: str, side: str, price_cents: int, count: int) -> dict:
        return self._post_order(ticker, action, side, price_cents, count)

    def fetch_events(self, series_ticker: str) -> list:
        """Fetch all open events for a series (for market discovery)."""
        data = self._get("/trade-api/v2/events", {
            "series_ticker": series_ticker,
            "status": "open",
            "with_nested_markets": "true",
        })
        return data.get("events", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_exchanges.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add exchanges/__init__.py exchanges/kalshi.py tests/test_exchanges.py
git commit -m "feat: KalshiExchange adapter — extracted API layer from kalshi/trader.py"
```

---

### Task 3: ErcotExchange adapter

**Files:**
- Modify: `ercot/hubs.py` — split `scan_all_hubs` into `fetch_ercot_markets` + scoring
- Create: `exchanges/ercot.py`
- Modify: `tests/test_exchanges.py` — add ERCOT tests

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exchanges.py`:

```python
from unittest.mock import patch
from exchanges.ercot import ErcotExchange


def test_ercot_fetch_market_data():
    """fetch_market_data returns price + solar + load."""
    exchange = ErcotExchange()
    with patch('exchanges.ercot._fetch_ercot_market_data') as mock_fetch:
        mock_fetch.return_value = {"price": 42.0, "solar_mw": 13000.0, "load_forecast": 51000.0}
        data = exchange.fetch_market_data()
    assert data["price"] == 42.0
    assert data["solar_mw"] == 13000.0


def test_ercot_get_positions():
    """get_positions returns open paper positions."""
    exchange = ErcotExchange()
    with patch('ercot.paper_trader.get_open_positions') as mock_pos:
        mock_pos.return_value = [{"hub": "North", "signal": "SHORT"}]
        positions = exchange.get_positions()
    assert len(positions) == 1
    assert positions[0]["hub"] == "North"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_exchanges.py::test_ercot_fetch_market_data -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'exchanges.ercot'`

- [ ] **Step 3: Write minimal implementation**

```python
# exchanges/ercot.py
"""ErcotExchange — wraps ERCOT data fetching and paper trading DB.

Delegates to ercot/hubs.py for market data and ercot/paper_trader.py for positions.
"""

from ercot.hubs import _fetch_ercot_market_data, ERCOT_HUBS
from ercot import paper_trader


class ErcotExchange:
    def fetch_market_data(self) -> dict:
        """Cached ERCOT prices, solar generation, load forecast."""
        return _fetch_ercot_market_data()

    def get_hubs(self) -> dict:
        """Return hub configuration."""
        return ERCOT_HUBS

    def get_positions(self) -> list:
        """Open paper positions from ercot_paper.db."""
        return paper_trader.get_open_positions()

    def open_position(self, hub_signal: dict, bankroll: float) -> dict | None:
        """Open a new paper position."""
        return paper_trader.open_position(hub_signal, bankroll)

    def close_position(self, position_id: int, exit_price: float,
                       exit_signal: str, reason: str):
        """Close a paper position."""
        return paper_trader.close_position(position_id, exit_price, exit_signal, reason)

    def expire_positions(self, current_price: float):
        """Auto-close positions past TTL."""
        return paper_trader.expire_positions(current_price)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_exchanges.py -v`
Expected: All 7 tests PASS (5 Kalshi + 2 ERCOT)

- [ ] **Step 5: Split scan_all_hubs in ercot/hubs.py**

Add `fetch_ercot_markets()` that returns raw hub data without scoring. Keep `scan_all_hubs()` for backward compatibility (it can call `fetch_ercot_markets` + scoring internally).

In `ercot/hubs.py`, add above `scan_all_hubs`:

```python
def fetch_ercot_markets() -> list[dict]:
    """Return raw hub data + shared ERCOT market data for pipeline fetch stage.

    Does NOT call solar signal — that happens in the score stage via forecast_fn.
    """
    market_data = _fetch_ercot_market_data()
    markets = []
    for hub_key, info in ERCOT_HUBS.items():
        markets.append({
            "hub": hub_key,
            "hub_name": info["hub_name"],
            "city": info["city"],
            "lat": info["lat"],
            "lon": info["lon"],
            "current_ercot_price": market_data.get("price", 40.0),
            "actual_solar_mw": market_data.get("solar_mw", 12000.0),
            "_ercot_data": market_data,
        })
    return markets
```

- [ ] **Step 6: Commit**

```bash
git add exchanges/ercot.py ercot/hubs.py tests/test_exchanges.py
git commit -m "feat: ErcotExchange adapter + fetch_ercot_markets split"
```

---

### Task 4: MarketConfig dataclass

**Files:**
- Create: `pipeline/config.py`
- Create: `tests/test_pipeline_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_config.py
from pipeline.config import MarketConfig, KALSHI_TEMP, KALSHI_PRECIP, ERCOT


def test_market_config_fields():
    """MarketConfig has all required fields."""
    cfg = KALSHI_TEMP
    assert cfg.name == "kalshi_temp"
    assert cfg.exchange == "kalshi"
    assert callable(cfg.fetch_fn)
    assert callable(cfg.forecast_fn)
    assert cfg.sanity_fn is not None  # temp has sanity check
    assert cfg.edge_gate == 0.12
    assert cfg.scan_frac == 0.10


def test_precip_no_sanity():
    """Precip config has no sanity function."""
    assert KALSHI_PRECIP.sanity_fn is None
    assert KALSHI_PRECIP.sameday_overrides is None


def test_ercot_config():
    """ERCOT config has paper-specific settings."""
    assert ERCOT.exchange == "ercot"
    assert ERCOT.pricing_fn is None
    assert ERCOT.bucket_parser is None
    assert ERCOT.settlement_timeline == "hourly"


def test_all_configs_have_required_callables():
    """Every config must have fetch_fn, forecast_fn, execute_fn, manage_fn, settle_fn."""
    for cfg in [KALSHI_TEMP, KALSHI_PRECIP, ERCOT]:
        assert callable(cfg.fetch_fn), f"{cfg.name} missing fetch_fn"
        assert callable(cfg.forecast_fn), f"{cfg.name} missing forecast_fn"
        assert callable(cfg.execute_fn), f"{cfg.name} missing execute_fn"
        assert callable(cfg.manage_fn), f"{cfg.name} missing manage_fn"
        assert callable(cfg.settle_fn), f"{cfg.name} missing settle_fn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline_config.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

```python
# pipeline/config.py
"""MarketConfig dataclass and concrete config instances.

Each market type is fully described by its config — no branching in pipeline code.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class MarketConfig:
    name: str
    display_name: str

    # Stage 1: Market discovery
    exchange: str
    fetch_fn: Callable
    series: dict
    bucket_parser: Callable | None

    # Stage 2: Forecast & signal
    forecast_fn: Callable
    fusion_weights: dict | None

    # Stage 3: Signal filtering
    edge_gate: float
    confidence_gate: float
    sameday_overrides: dict | None

    # Stage 4: Sanity check
    sanity_fn: Callable | None

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
    settlement_timeline: str
    settle_fn: Callable


# --- Concrete configs ---
# Import the actual functions each config needs.
# These imports are deferred to avoid circular imports at module load.

def _build_configs() -> tuple:
    """Build all 3 configs. Called once at import time."""
    from kalshi.scanner import (
        get_kalshi_weather_markets, get_kalshi_precip_markets,
        parse_kalshi_bucket, WEATHER_SERIES, PRECIP_SERIES,
    )
    from kalshi.market_types import parse_precip_bucket
    from weather.multi_model import fuse_forecast, fuse_precip_forecast, get_ercot_solar_signal
    from kalshi.pricing import choose_price_strategy
    from ercot.hubs import fetch_ercot_markets
    from config import ERCOT_HUBS

    # Placeholder functions — will be implemented in pipeline/stages.py
    # These are the config-specific execution/management/settlement functions
    # that the pipeline stages will call.
    def _placeholder(*args, **kwargs):
        raise NotImplementedError("Pipeline stage function not yet implemented")

    # Sanity check for temp markets (GFS cross-reference)
    def gfs_temp_sanity(signal) -> bool:
        """Placeholder — will be extracted from kalshi/trader.py sanity check logic."""
        return True

    kalshi_temp = MarketConfig(
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
        execute_fn=_placeholder,
        pricing_fn=choose_price_strategy,
        manage_fn=_placeholder,
        exit_rules={"reversal_edge": -0.08, "sameday_reversal": -0.15,
                    "profit_take_pct": 0.88, "min_confidence": 70},
        settlement_timeline="daily",
        settle_fn=_placeholder,
    )

    kalshi_precip = MarketConfig(
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
        execute_fn=_placeholder,
        pricing_fn=choose_price_strategy,
        manage_fn=_placeholder,
        exit_rules={"reversal_edge": -0.10, "profit_take_pct": 0.90,
                    "min_confidence": 60},
        settlement_timeline="monthly",
        settle_fn=_placeholder,
    )

    ercot = MarketConfig(
        name="ercot",
        display_name="ERCOT Solar",
        exchange="ercot",
        fetch_fn=fetch_ercot_markets,
        series=ERCOT_HUBS,
        bucket_parser=None,
        forecast_fn=get_ercot_solar_signal,
        fusion_weights=None,
        edge_gate=0.005,
        confidence_gate=50,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_contracts_per_event=3,
        execute_fn=_placeholder,
        pricing_fn=None,
        manage_fn=_placeholder,
        exit_rules={"edge_decay_pct": 0.30, "signal_flip": True,
                    "ttl_hours": 24},
        settlement_timeline="hourly",
        settle_fn=_placeholder,
    )

    return kalshi_temp, kalshi_precip, ercot


KALSHI_TEMP, KALSHI_PRECIP, ERCOT = _build_configs()
ALL_CONFIGS = [KALSHI_TEMP, KALSHI_PRECIP, ERCOT]
```

Note: `execute_fn`, `manage_fn`, and `settle_fn` are placeholders (`_placeholder`) that will be replaced in Tasks 5-8 when we implement the pipeline stages. The configs are `frozen=True` so they can't be accidentally mutated. The placeholder functions raise `NotImplementedError` to fail fast if called before implementation.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline_config.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/config.py tests/test_pipeline_config.py
git commit -m "feat: MarketConfig dataclass + 3 config instances (placeholders for stages)"
```

---

## Chunk 2: Pipeline Stages

### Task 5: fetch_markets and score_signal stages

**Files:**
- Create: `pipeline/stages.py`
- Create: `tests/test_pipeline_stages.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_stages.py
"""Tests for pipeline stage functions. All use mocks — no API/forecast calls."""
from unittest.mock import MagicMock
from pipeline.types import Signal, CycleState
from pipeline.stages import fetch_markets, score_signal


def test_fetch_markets_calls_config_fn():
    """fetch_markets delegates to config.fetch_fn."""
    config = MagicMock()
    config.fetch_fn.return_value = [{"ticker": "T1"}, {"ticker": "T2"}]
    exchange = MagicMock()

    markets = fetch_markets(config, exchange)

    config.fetch_fn.assert_called_once_with(exchange, config.series)
    assert len(markets) == 2


def test_score_signal_temp():
    """score_signal creates a Signal with correct fields for temp market."""
    config = MagicMock()
    config.name = "kalshi_temp"
    config.forecast_fn.return_value = MagicMock(prob=0.30, confidence=72.0, details={})
    config.fusion_weights = {"ensemble": 0.40, "noaa": 0.35, "hrrr": 0.25}
    config.bucket_parser = MagicMock(return_value=(56.0, None))

    market = {
        "ticker": "KXHIGHNY-26MAR15-T56",
        "_city": "nyc",
        "yes_ask": 55,
        "yes_bid": 50,
        "_lat": 40.7, "_lon": -74.0,
    }

    signal = score_signal(config, market)

    assert isinstance(signal, Signal)
    assert signal.ticker == "KXHIGHNY-26MAR15-T56"
    assert signal.market_type == "kalshi_temp"
    assert signal.model_prob == 0.30
    assert signal.city == "nyc"


def test_score_signal_ercot():
    """score_signal handles ERCOT markets (no bucket parser, different forecast shape)."""
    config = MagicMock()
    config.name = "ercot"
    config.bucket_parser = None
    config.forecast_fn.return_value = {
        "signal": "SHORT", "edge": 1.69, "confidence": 70,
        "expected_solrad_mjm2": 21.8,
    }
    config.fusion_weights = None

    market = {
        "hub": "North", "hub_name": "HB_NORTH", "city": "Dallas",
        "lat": 32.78, "lon": -96.80,
        "current_ercot_price": 40.0, "_ercot_data": {},
    }

    signal = score_signal(config, market)

    assert signal.market_type == "ercot"
    assert signal.city == "Dallas"
    assert signal.side == "no"  # SHORT maps to selling
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline_stages.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write implementation**

```python
# pipeline/stages.py
"""Generic pipeline stage functions.

Each stage takes a MarketConfig + inputs, returns outputs.
No globals, no module state — everything through parameters.
"""

from pipeline.types import Signal, CycleState, TradeResult


def fetch_markets(config, exchange) -> list[dict]:
    """Stage 1: Call config's fetch function to discover markets."""
    return config.fetch_fn(exchange, config.series)


def score_signal(config, market: dict) -> Signal:
    """Stage 2: Generate forecast and create typed Signal.

    Calls config.forecast_fn, extracts market price, computes edge.
    Promotes key fields from raw market dict into typed Signal fields.
    """
    ticker = market.get("ticker") or market.get("hub_name", "")
    city = market.get("_city") or market.get("city", "")

    # Get market price
    market_prob = _extract_market_prob(market)

    # Call forecast function (different signature per market type)
    if config.name == "ercot":
        forecast_result = config.forecast_fn(
            market.get("lat", 0), market.get("lon", 0),
            hours_ahead=24,
            ercot_data=market.get("_ercot_data"),
        )
        model_prob = 1.0 - forecast_result.get("edge", 0)  # ERCOT edge is direct
        confidence = forecast_result.get("confidence", 50)
        edge = forecast_result.get("edge", 0)
        ercot_signal = forecast_result.get("signal", "NEUTRAL")
        side = "no" if ercot_signal == "SHORT" else "yes" if ercot_signal == "LONG" else "yes"
    else:
        # Kalshi temp/precip: parse bucket, call forecast fusion
        bucket = config.bucket_parser(market) if config.bucket_parser else None
        low = bucket[0] if bucket else 0
        high = bucket[1] if bucket else None

        # Extract forecast parameters from market metadata
        lat = market.get("_lat", 0)
        lon = market.get("_lon", 0)
        unit = market.get("_unit", "f")
        temp_type = market.get("_temp_type", "max")
        days_ahead = _compute_days_ahead(ticker)

        if config.name == "kalshi_precip":
            threshold = market.get("_threshold", low)
            forecast_days = _compute_forecast_days(ticker)
            forecast_result = config.forecast_fn(
                lat=lat, lon=lon, city=city,
                month=_extract_month(ticker),
                threshold=threshold,
                forecast_days=forecast_days,
            )
        else:
            forecast_result = config.forecast_fn(
                lat=lat, lon=lon, city=city,
                month=_extract_month(ticker),
                low=low, high=high,
                days_ahead=days_ahead,
                unit=unit, temp_type=temp_type,
                weights=config.fusion_weights,
            )

        if hasattr(forecast_result, 'prob'):
            model_prob = forecast_result.prob
            confidence = forecast_result.confidence
        elif isinstance(forecast_result, tuple):
            model_prob, confidence = forecast_result[0], forecast_result[1]
        else:
            model_prob = forecast_result
            confidence = 50.0

        edge = model_prob - market_prob
        side = "yes" if edge > 0 else "no"
        days_ahead = days_ahead if config.name != "kalshi_precip" else 99

    price_cents = market.get("yes_ask") or market.get("last_price") or 50

    return Signal(
        ticker=ticker,
        city=city,
        market_type=config.name,
        side=side,
        model_prob=model_prob,
        market_prob=market_prob,
        edge=edge,
        confidence=confidence,
        price_cents=int(price_cents) if isinstance(price_cents, (int, float)) else 50,
        days_ahead=days_ahead if 'days_ahead' in dir() else 0,
        yes_bid=market.get("yes_bid"),
        yes_ask=market.get("yes_ask"),
        lat=market.get("_lat") or market.get("lat"),
        lon=market.get("_lon") or market.get("lon"),
        market=market,
    )


def _extract_market_prob(market: dict) -> float:
    """Extract YES probability from market data."""
    # Try cents format first
    yes_ask = market.get("yes_ask")
    if yes_ask and isinstance(yes_ask, (int, float)) and yes_ask > 1:
        return yes_ask / 100.0
    # Try dollar format
    yes_ask_d = market.get("yes_ask_dollars")
    if yes_ask_d:
        return float(yes_ask_d)
    # Try last_price
    last = market.get("last_price")
    if last and isinstance(last, (int, float)) and last > 1:
        return last / 100.0
    # ERCOT has no market_prob concept
    return 0.50


def _compute_days_ahead(ticker: str) -> int:
    """Parse settlement date from ticker and compute days ahead."""
    import re
    from datetime import date
    parts = ticker.split("-")
    if len(parts) >= 2:
        m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", parts[1])
        if m:
            yr, mon_str, day = m.groups()
            months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                      "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
            mon = months.get(mon_str, 1)
            target = date(2000 + int(yr), mon, int(day))
            return max(0, (target - date.today()).days)
    return 0


def _compute_forecast_days(ticker: str) -> int:
    """For monthly precip contracts, compute remaining days in the month."""
    from datetime import date
    today = date.today()
    import calendar
    last_day = calendar.monthrange(today.year, today.month)[1]
    return last_day - today.day + 1


def _extract_month(ticker: str) -> int:
    """Extract month number from ticker like KXHIGHNY-26MAR15-T56."""
    import re
    parts = ticker.split("-")
    if len(parts) >= 2:
        m = re.match(r"\d{2}([A-Z]{3})", parts[1])
        if m:
            months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                      "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
            return months.get(m.group(1), 3)
    from datetime import date
    return date.today().month
```

Note: `score_signal` is the most complex stage because each market type's forecast function has a different signature. The `config.name` check for "ercot" vs Kalshi is the ONE place where market-type branching exists in the pipeline — and it's only because the forecast function signatures genuinely differ (lat/lon/hours_ahead vs lat/lon/city/month/bucket). All other stages are truly generic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline_stages.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages.py tests/test_pipeline_stages.py
git commit -m "feat: fetch_markets and score_signal pipeline stages"
```

---

### Task 6: filter_signals stage

**Files:**
- Modify: `pipeline/stages.py` — add `filter_signals`
- Modify: `tests/test_pipeline_stages.py` — add filter tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_stages.py`:

```python
from pipeline.stages import filter_signals


def _make_signal(**overrides) -> Signal:
    """Helper to create test signals with defaults."""
    defaults = dict(
        ticker="KXHIGHNY-26MAR15-T56", city="nyc", market_type="kalshi_temp",
        side="no", model_prob=0.30, market_prob=0.55, edge=-0.25,
        confidence=72.0, price_cents=55, days_ahead=0,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def test_filter_edge_gate():
    """Signals below edge gate are filtered out."""
    config = MagicMock()
    config.edge_gate = 0.12
    config.confidence_gate = 60
    config.sameday_overrides = None

    signals = [
        _make_signal(edge=0.15, confidence=70),   # passes
        _make_signal(edge=0.05, confidence=70),   # filtered (edge too low)
        _make_signal(edge=-0.20, confidence=70),  # passes (abs edge)
    ]

    filtered = filter_signals(config, signals, held_positions=[], resting_tickers=set())
    assert len(filtered) == 2


def test_filter_sameday_override():
    """Same-day signals use looser thresholds from config."""
    config = MagicMock()
    config.edge_gate = 0.12
    config.confidence_gate = 60
    config.sameday_overrides = {"edge": 0.05, "confidence": 45}

    signal = _make_signal(edge=0.08, confidence=50, days_ahead=0)
    filtered = filter_signals(config, [signal], held_positions=[], resting_tickers=set())
    assert len(filtered) == 1  # passes with sameday override


def test_filter_confidence_gate():
    """Signals below confidence gate are filtered out."""
    config = MagicMock()
    config.edge_gate = 0.05
    config.confidence_gate = 60
    config.sameday_overrides = None

    signal = _make_signal(edge=0.15, confidence=40)
    filtered = filter_signals(config, [signal], held_positions=[], resting_tickers=set())
    assert len(filtered) == 0


def test_filter_resting_order_dedup():
    """Signals for tickers with resting buy orders are filtered."""
    config = MagicMock()
    config.edge_gate = 0.05
    config.confidence_gate = 40
    config.sameday_overrides = None

    signal = _make_signal(ticker="KXHIGHNY-26MAR15-T56", edge=0.20, confidence=80)
    resting = {"KXHIGHNY-26MAR15-T56"}
    filtered = filter_signals(config, [signal], held_positions=[], resting_tickers=resting)
    assert len(filtered) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline_stages.py::test_filter_edge_gate -v`
Expected: FAIL with `ImportError: cannot import name 'filter_signals'`

- [ ] **Step 3: Write implementation**

Add to `pipeline/stages.py`:

```python
def filter_signals(config, signals: list[Signal], held_positions: list,
                   resting_tickers: set[str]) -> list[Signal]:
    """Stage 3: Apply edge gate, confidence gate, liquidity, dedup, cross-contract.

    Returns filtered and de-conflicted signal list, sorted by absolute edge descending.
    """
    # Determine thresholds (same-day overrides if applicable)
    results = []

    # Sort by absolute edge descending (strongest signals first for cross-contract)
    ranked = sorted(signals, key=lambda s: abs(s.edge), reverse=True)

    # Track committed positions for cross-contract consistency
    committed = {}  # (city, days_ahead) -> set of (side, bucket_low)

    held_tickers = {p.get("ticker", "") for p in held_positions
                    if float(p.get("position_fp", 0)) != 0}

    for signal in ranked:
        # Determine effective thresholds
        edge_gate = config.edge_gate
        conf_gate = config.confidence_gate
        if config.sameday_overrides and signal.days_ahead == 0:
            edge_gate = config.sameday_overrides.get("edge", edge_gate)
            conf_gate = config.sameday_overrides.get("confidence", conf_gate)

        # Edge gate
        if abs(signal.edge) < edge_gate:
            continue

        # Confidence gate
        if signal.confidence < conf_gate:
            continue

        # Already holding this ticker
        if signal.ticker in held_tickers:
            continue

        # Resting order dedup
        if signal.ticker in resting_tickers:
            continue

        # Liquidity gate (for Kalshi markets)
        if signal.market:
            volume = float(signal.market.get("volume_24h_fp", 0) or 0)
            oi = float(signal.market.get("open_interest_fp", 0) or 0)
            if volume < 500 and oi < 500 and config.exchange == "kalshi":
                continue

        results.append(signal)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline_stages.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages.py tests/test_pipeline_stages.py
git commit -m "feat: filter_signals pipeline stage — edge/confidence gates, dedup, liquidity"
```

---

### Task 7: sanity_check, size_position, execute_trade stages

**Files:**
- Modify: `pipeline/stages.py` — add remaining stages
- Modify: `tests/test_pipeline_stages.py` — add tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_stages.py`:

```python
from pipeline.stages import sanity_check, size_position, execute_trade
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker


def test_sanity_check_none_passes():
    """When config.sanity_fn is None, signal always passes."""
    config = MagicMock()
    config.sanity_fn = None
    signal = _make_signal()
    assert sanity_check(config, signal) is True


def test_sanity_check_calls_fn():
    """When config.sanity_fn exists, it's called with the signal."""
    config = MagicMock()
    config.sanity_fn.return_value = False
    signal = _make_signal()
    assert sanity_check(config, signal) is False
    config.sanity_fn.assert_called_once_with(signal)


def test_size_position_basic():
    """size_position returns a SizeResult with nonzero count for good signal."""
    config = MagicMock()
    config.scan_frac = 0.10
    config.kelly_floor = 0.25
    config.max_contracts_per_event = 10

    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    state = CycleState()

    signal = _make_signal(model_prob=0.70, market_prob=0.55, edge=0.15,
                          confidence=85.0, price_cents=55)

    result = size_position(config, signal, bt, cb, state)
    assert result.count > 0
    assert result.dollar_amount > 0


def test_size_position_budget_exhausted():
    """size_position returns 0 when scan budget is exhausted."""
    config = MagicMock()
    config.scan_frac = 0.10
    config.kelly_floor = 0.25
    config.max_contracts_per_event = 10

    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    state = CycleState()
    state.scan_spent = 999.0  # way over budget

    signal = _make_signal(model_prob=0.70, market_prob=0.55, edge=0.15,
                          confidence=85.0, price_cents=55)

    result = size_position(config, signal, bt, cb, state)
    assert result.count == 0


def test_execute_trade_paper_mode():
    """In paper mode, execute_trade logs but doesn't call exchange."""
    config = MagicMock()
    config.pricing_fn = None
    exchange = MagicMock()

    signal = _make_signal()
    from risk.sizer import SizeResult
    size = SizeResult(side="no", count=5, dollar_amount=2.75,
                      raw_kelly=0.03, adjusted_kelly=0.02,
                      limit_reason="OK")

    result = execute_trade(config, signal, size, exchange, paper_mode=True)
    assert result.paper is True
    assert result.status == "paper"
    exchange.place_order.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline_stages.py::test_sanity_check_none_passes -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

Add to `pipeline/stages.py`:

```python
from risk.sizer import compute_size, SizeResult


def sanity_check(config, signal: Signal) -> bool:
    """Stage 4: Validate signal against reference forecast.

    Returns True if signal passes (or if no sanity function configured).
    """
    if config.sanity_fn is None:
        return True
    try:
        return config.sanity_fn(signal)
    except Exception:
        return True  # sanity check is advisory, never blocks on errors


def size_position(config, signal: Signal, bankroll: "BankrollTracker",
                  circuit_breaker: "CircuitBreaker",
                  cycle_state: CycleState) -> SizeResult:
    """Stage 5: Kelly sizing with config's budget and limits.

    Uses the shared risk/sizer.py:compute_size() with config-specific parameters.
    """
    effective_kelly = config.kelly_floor
    if config.sameday_overrides and signal.days_ahead == 0:
        effective_kelly = config.sameday_overrides.get("kelly_floor", config.kelly_floor)

    result = compute_size(
        model_prob=signal.model_prob,
        market_prob=signal.market_prob,
        confidence=signal.confidence,
        price_cents=signal.price_cents,
        bankroll_tracker=bankroll,
        circuit_breaker=circuit_breaker,
        scan_spent=cycle_state.scan_spent,
        event_contracts=0,
        fractional_kelly=effective_kelly,
    )

    # Hard 2% bankroll cap
    current_bankroll = bankroll.effective_bankroll()
    max_dollars = current_bankroll * 0.02
    if result.dollar_amount > max_dollars and result.count > 0:
        result.count = max(1, int(max_dollars / (signal.price_cents / 100.0)))
        result.dollar_amount = result.count * signal.price_cents / 100.0

    return result


def execute_trade(config, signal: Signal, size: SizeResult,
                  exchange, paper_mode: bool) -> TradeResult:
    """Stage 6: Place order or log paper trade.

    Determines price via config.pricing_fn, checks fee profitability,
    then either logs (paper) or calls exchange adapter (live).
    """
    from kalshi.pricing import kalshi_fee

    # Determine price
    price_cents = signal.price_cents
    strategy = "taker"
    if config.pricing_fn and signal.yes_bid is not None:
        is_sameday = signal.days_ahead == 0
        price_result = config.pricing_fn(
            size.side, signal.yes_bid, signal.yes_ask,
            signal.edge, is_sameday,
        )
        if price_result and price_result[0] is not None:
            price_cents = price_result[0]
            strategy = price_result[1]

    # Fee gate
    is_taker = strategy in ("taker", "legacy")
    fee = kalshi_fee(price_cents, size.count, is_taker=is_taker)
    expected_profit = abs(signal.edge) * size.count - fee
    if expected_profit < 0.12 and config.exchange == "kalshi":
        return TradeResult(
            ticker=signal.ticker, side=size.side or signal.side,
            count=0, price_cents=price_cents, cost=0,
            order_id="", status="fee_blocked", paper=paper_mode,
        )

    cost = size.count * price_cents / 100.0

    if paper_mode:
        # Log paper trade to trades.db
        from kalshi.fill_tracker import init_trades_db, record_fill
        from datetime import datetime, timezone
        init_trades_db()
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
        )
        return TradeResult(
            ticker=signal.ticker, side=size.side or signal.side,
            count=size.count, price_cents=price_cents, cost=cost,
            order_id="paper", status="paper", paper=True,
        )

    # Live order
    resp = exchange.place_order(
        signal.ticker, "buy", size.side or signal.side, price_cents, size.count,
    )
    order = resp.get("order", {})
    order_id = order.get("order_id", "unknown")
    status = order.get("status", "unknown")

    fill_qty = int(float(order.get("fill_count_fp", "0") or "0"))
    if fill_qty > 0:
        taker_cost = float(order.get("taker_fill_cost_dollars", "0") or "0")
        maker_cost = float(order.get("maker_fill_cost_dollars", "0") or "0")
        actual_cost = taker_cost + maker_cost if (taker_cost + maker_cost) > 0 else fill_qty * price_cents / 100.0
    else:
        actual_cost = cost

    # Record fill
    from kalshi.fill_tracker import init_trades_db, record_fill
    from datetime import datetime, timezone
    init_trades_db()
    actual_price = int(actual_cost / fill_qty * 100) if fill_qty > 0 else price_cents
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
    )

    return TradeResult(
        ticker=signal.ticker, side=size.side or signal.side,
        count=fill_qty if fill_qty > 0 else size.count,
        price_cents=price_cents, cost=actual_cost,
        order_id=order_id, status=status, paper=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline_stages.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages.py tests/test_pipeline_stages.py
git commit -m "feat: sanity_check, size_position, execute_trade pipeline stages"
```

---

## Chunk 3: PipelineRunner and Integration

### Task 8: PipelineRunner

**Files:**
- Create: `pipeline/runner.py`
- Create: `tests/test_pipeline_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_runner.py
"""Integration tests for PipelineRunner with mock exchanges."""
from unittest.mock import MagicMock, patch
from pipeline.runner import PipelineRunner
from pipeline.types import CycleState


def test_runner_creates_cycle_state_per_config():
    """Each config gets its own CycleState (budget isolation)."""
    configs = [MagicMock(name="cfg_a", exchange="kalshi"),
               MagicMock(name="cfg_b", exchange="kalshi")]
    exchanges = {"kalshi": MagicMock(), "ercot": MagicMock()}

    runner = PipelineRunner(configs, exchanges)

    # Mock the exchange to return empty positions
    exchanges["kalshi"].get_positions.return_value = []
    exchanges["kalshi"].get_orders.return_value = []
    exchanges["kalshi"].get_balance.return_value = {"balance": 10000, "portfolio_value": 0}

    with patch('pipeline.runner.fetch_markets', return_value=[]):
        runner.run_cycle(paper_mode=True)

    # Each config was processed (fetch_markets called twice)
    assert exchanges["kalshi"].get_positions.call_count == 1  # fetched once


def test_runner_skips_kalshi_when_maxed():
    """Runner skips Kalshi configs when position limit is hit."""
    config = MagicMock(name="kalshi_temp", exchange="kalshi")
    exchanges = {"kalshi": MagicMock(), "ercot": MagicMock()}

    runner = PipelineRunner([config], exchanges)

    # Simulate 50 positions (maxed)
    exchanges["kalshi"].get_positions.return_value = [
        {"ticker": f"T{i}", "position_fp": "1.0"} for i in range(50)
    ]
    exchanges["kalshi"].get_orders.return_value = []
    exchanges["kalshi"].get_balance.return_value = {"balance": 10000, "portfolio_value": 5000}

    with patch('pipeline.runner.fetch_markets') as mock_fetch:
        runner.run_cycle(paper_mode=True)
        mock_fetch.assert_not_called()  # skipped because maxed


def test_runner_error_in_one_config_doesnt_abort():
    """Error in config A doesn't prevent config B from running."""
    config_a = MagicMock(name="bad_config", exchange="kalshi")
    config_b = MagicMock(name="good_config", exchange="kalshi")
    exchanges = {"kalshi": MagicMock(), "ercot": MagicMock()}

    runner = PipelineRunner([config_a, config_b], exchanges)

    exchanges["kalshi"].get_positions.return_value = []
    exchanges["kalshi"].get_orders.return_value = []
    exchanges["kalshi"].get_balance.return_value = {"balance": 10000, "portfolio_value": 0}

    call_count = [0]

    def fetch_side_effect(config, exchange):
        call_count[0] += 1
        if config.name == "bad_config":
            raise RuntimeError("API down")
        return []

    with patch('pipeline.runner.fetch_markets', side_effect=fetch_side_effect):
        runner.run_cycle(paper_mode=True)

    assert call_count[0] == 2  # both configs attempted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline_runner.py -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

```python
# pipeline/runner.py
"""PipelineRunner — orchestrates market scanning cycles.

Replaces scanner.py + kalshi/trader.py globals.
Holds shared state (bankroll, circuit breaker).
Dispatches per-config with isolated CycleState.
"""

from rich.console import Console

from pipeline.types import CycleState
from pipeline.stages import (
    fetch_markets, score_signal, filter_signals,
    sanity_check, size_position, execute_trade,
)
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker
from config import MAX_POSITIONS_TOTAL

console = Console()

MAX_CONSECUTIVE_ERRORS = 5


class PipelineRunner:
    def __init__(self, configs: list, exchanges: dict):
        self.configs = configs
        self.exchanges = exchanges
        self.bankroll = BankrollTracker(initial_bankroll=500.0)
        self.circuit_breaker = CircuitBreaker()
        self._consecutive_errors: dict[str, int] = {}

    def run_cycle(self, paper_mode: bool):
        """Run one full scan cycle across all configs."""
        # Sync bankroll from Kalshi API
        kalshi = self.exchanges.get("kalshi")
        if kalshi:
            try:
                bal = kalshi.get_balance()
                self.bankroll.update_from_api(
                    balance_cents=bal.get("balance", 0),
                    portfolio_value_cents=bal.get("portfolio_value", 0),
                )
            except Exception as e:
                print(f"  Bankroll sync failed: {e}")

        # Pre-dispatch: fetch shared state once
        held_positions = []
        resting_tickers = set()
        kalshi_position_count = 0

        if kalshi:
            try:
                held_positions = kalshi.get_positions()
                resting_orders = kalshi.get_orders(status="resting")
                resting_tickers = {o.get("ticker", "") for o in resting_orders
                                   if o.get("action") == "buy"}
                kalshi_position_count = sum(
                    1 for p in held_positions
                    if float(p.get("position_fp", 0)) != 0
                )
            except Exception as e:
                print(f"  Position fetch failed: {e}")

        # Process each config
        for config in self.configs:
            # Skip configs with too many consecutive errors
            if self._consecutive_errors.get(config.name, 0) >= MAX_CONSECUTIVE_ERRORS:
                print(f"  SKIP {config.name} — {MAX_CONSECUTIVE_ERRORS} consecutive errors")
                continue

            # Cross-config position limit
            if config.exchange == "kalshi" and kalshi_position_count >= MAX_POSITIONS_TOTAL:
                continue

            state = CycleState()
            exchange = self.exchanges.get(config.exchange)

            try:
                markets = fetch_markets(config, exchange)
                state.signals_scored = len(markets)

                signals = []
                for m in markets:
                    try:
                        signals.append(score_signal(config, m))
                    except Exception as e:
                        state.errors.append(f"score: {e}")

                filtered = filter_signals(config, signals, held_positions, resting_tickers)
                state.signals_filtered = len(filtered)

                for signal in filtered:
                    if not sanity_check(config, signal):
                        continue

                    signal.size = size_position(
                        config, signal, self.bankroll,
                        self.circuit_breaker, state,
                    )

                    if signal.size and signal.size.count > 0:
                        signal.trade_result = execute_trade(
                            config, signal, signal.size, exchange, paper_mode,
                        )
                        if signal.trade_result and signal.trade_result.count > 0:
                            state.scan_spent += signal.trade_result.cost
                            state.trades_executed += 1
                            state.total_edge += abs(signal.edge)

                # Reset error counter on success
                self._consecutive_errors[config.name] = 0

            except Exception as e:
                state.errors.append(str(e))
                self._consecutive_errors[config.name] = \
                    self._consecutive_errors.get(config.name, 0) + 1
                print(f"  Pipeline error ({config.name}): {e}")

            # Log cycle stats
            if state.trades_executed > 0 or state.errors:
                print(f"  {config.display_name}: {state.signals_scored} scored, "
                      f"{state.signals_filtered} filtered, {state.trades_executed} traded, "
                      f"${state.scan_spent:.2f} spent"
                      + (f", {len(state.errors)} errors" if state.errors else ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline_runner.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_pipeline_runner.py
git commit -m "feat: PipelineRunner — cycle orchestrator with per-config isolation"
```

---

### Task 9: Wire daemon.py to PipelineRunner

**Files:**
- Modify: `daemon.py`

- [ ] **Step 1: Read current daemon.py**

Review the current `run_cycle` and `main` functions to understand the exact call sequence.

- [ ] **Step 2: Modify daemon.py**

Replace the phase-by-phase imports with PipelineRunner:

```python
# daemon.py — key changes:

# Old imports (remove):
# from scanner import run_scanner
# from kalshi.trader import get_balance, get_positions

# New imports:
from pipeline.runner import PipelineRunner
from pipeline.config import ALL_CONFIGS
from exchanges.kalshi import KalshiExchange
from exchanges.ercot import ErcotExchange

# In main(), create runner once:
exchanges = {
    "kalshi": KalshiExchange(),
    "ercot": ErcotExchange(),
}
runner = PipelineRunner(ALL_CONFIGS, exchanges)

# In run_cycle(), replace Phase 1 + Phase 2:
# Old:
#   run_position_manager()
#   run_scanner()
# New:
runner.run_cycle(paper_mode=PAPER_MODE)

# Phase 3 (settler) stays but receives exchange adapter:
from kalshi.settler import run_settler
run_settler(exchange=exchanges["kalshi"])

# Balance check uses exchange directly:
bal = exchanges["kalshi"].get_balance()
```

The exact implementation will need to carefully preserve the cycle numbering, timing, logging, and error handling from the current daemon. Read the full file and modify surgically.

- [ ] **Step 3: Verify daemon starts**

Run: `.venv/bin/python -m daemon` (Ctrl+C after first cycle)
Expected: Cycle runs, signals are scored, paper trades are logged. No crashes.

- [ ] **Step 4: Commit**

```bash
git add daemon.py
git commit -m "feat: wire daemon.py to PipelineRunner — replace scanner + trader"
```

---

### Task 10: Migrate settler and position manager to exchange adapter

**Files:**
- Modify: `kalshi/settler.py` — replace `_get` import with exchange adapter parameter
- Modify: `kalshi/position_manager.py` — replace trader imports with exchange adapter parameter

- [ ] **Step 1: Modify kalshi/settler.py**

```python
# Change:
#   from kalshi.trader import _get
# To: receive exchange as parameter

def run_settler(exchange=None):
    """Resolve all unresolved trades against settled markets."""
    if exchange is None:
        from exchanges.kalshi import KalshiExchange
        exchange = KalshiExchange()

    # Replace _get calls:
    # Old: data = _get(f"/trade-api/v2/markets/{ticker}")
    # New: market = exchange.get_market(ticker)
```

- [ ] **Step 2: Modify kalshi/position_manager.py**

```python
# Change:
#   from kalshi.trader import get_positions, get_balance, sell_order, ...
# To: receive exchange as parameter

def run_position_manager(exchange=None):
    if exchange is None:
        from exchanges.kalshi import KalshiExchange
        exchange = KalshiExchange()

    # Replace all trader calls with exchange.method() calls
```

- [ ] **Step 3: Verify both still work**

Run daemon for one cycle and check logs for position management and settlement.

- [ ] **Step 4: Commit**

```bash
git add kalshi/settler.py kalshi/position_manager.py
git commit -m "refactor: settler + position manager use exchange adapter instead of trader imports"
```

---

### Task 11: Migrate dashboard to exchange adapter

**Files:**
- Modify: `dashboard/api.py`

- [ ] **Step 1: Replace trader imports**

```python
# Old:
# from kalshi.trader import get_balance, get_positions
# New:
from exchanges.kalshi import KalshiExchange
_kalshi = KalshiExchange()

# Replace all get_balance() calls with _kalshi.get_balance()
# Replace all get_positions() calls with _kalshi.get_positions()
# Replace get_orders() import with _kalshi.get_orders()
```

- [ ] **Step 2: Verify dashboard still works**

Run: `ssh server "curl -s http://localhost:8501/api/portfolio | python3 -m json.tool"`
Expected: Same response as before.

- [ ] **Step 3: Commit**

```bash
git add dashboard/api.py
git commit -m "refactor: dashboard uses KalshiExchange adapter instead of trader imports"
```

---

### Task 12: Update MarketConfig with real stage functions (remove placeholders)

**Files:**
- Modify: `pipeline/config.py` — replace `_placeholder` with actual stage functions

- [ ] **Step 1: Replace placeholders**

Now that `pipeline/stages.py` has `execute_trade`, the configs can reference the real functions. The `execute_fn`, `manage_fn`, and `settle_fn` in the configs should point to the appropriate functions:

- `execute_fn` for Kalshi → the Kalshi-specific order logic in `execute_trade`
- `manage_fn` → `evaluate_kalshi_position` / `evaluate_ercot_position`
- `settle_fn` → `resolve_kalshi_trade` / `expire_ercot_position`

Since `execute_trade` in `pipeline/stages.py` handles both Kalshi and ERCOT through the exchange adapter, the `execute_fn` on the config may become unnecessary (the stage function handles it). Evaluate whether `execute_fn` is still needed or if it's absorbed by the stage.

- [ ] **Step 2: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add pipeline/config.py
git commit -m "feat: replace MarketConfig placeholder functions with real implementations"
```

---

### Task 13: Delete replaced files and final verification

**Files:**
- Delete: `kalshi/trader.py` (replaced by `exchanges/kalshi.py` + `pipeline/`)
- Delete: `scanner.py` (replaced by `pipeline/runner.py` + `pipeline/stages.py`)

- [ ] **Step 1: Verify no remaining imports of old modules**

Search for any remaining `from kalshi.trader import` or `from scanner import` across the codebase. Fix any stragglers.

- [ ] **Step 2: Delete old files**

```bash
git rm kalshi/trader.py scanner.py
```

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Deploy to server and verify one full cycle**

```bash
git push && bash deploy.sh
ssh server "sudo systemctl restart weather-daemon && sudo systemctl restart weather-dashboard"
# Watch first cycle:
ssh server "sudo journalctl -u weather-daemon --since '5 seconds ago' --no-pager -f"
```

Expected: Cycle runs with all 3 configs, paper trades logged, no errors.

- [ ] **Step 5: Final commit**

```bash
git commit -m "chore: remove kalshi/trader.py and scanner.py — replaced by pipeline architecture"
```

---

## Summary

| Chunk | Tasks | What it delivers |
|-------|-------|-----------------|
| **Chunk 1** | Tasks 1-4 | Foundation: types, exchange adapters, MarketConfig |
| **Chunk 2** | Tasks 5-7 | Pipeline stages: fetch, score, filter, sanity, size, execute |
| **Chunk 3** | Tasks 8-13 | Runner, daemon wiring, migration of settler/PM/dashboard, cleanup |

Total: 13 tasks. Each task has a test-first step, implementation, verification, and commit. The pipeline is functional after Chunk 2 (can be tested standalone). Chunk 3 wires it into the daemon and removes old code.
