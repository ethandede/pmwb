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
