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

    # Ensemble mean temperature from the fusion step, used by the
    # NWS deterministic sanity check in filter_signals() to detect
    # cases where our ensemble is an outlier vs the NWS point forecast.
    model_mean_temp: float | None = None

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
    passed_edge_gate: int = 0
    passed_confidence: int = 0
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
