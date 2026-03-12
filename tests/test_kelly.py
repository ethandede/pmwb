import pytest
from risk.kelly import kelly_yes, kelly_no, kelly_fraction


def test_kelly_yes_positive_edge():
    """Model says 70% YES, market at 55% -> positive Kelly."""
    f = kelly_yes(model_prob=0.70, market_prob=0.55)
    # f* = (0.70 - 0.55) / (1 - 0.55) = 0.15 / 0.45 = 0.333
    assert f == pytest.approx(0.3333, abs=0.01)


def test_kelly_yes_no_edge():
    """Model agrees with market -> Kelly = 0."""
    f = kelly_yes(model_prob=0.55, market_prob=0.55)
    assert f == pytest.approx(0.0)


def test_kelly_yes_negative_edge():
    """Model says 40% YES, market at 55% -> negative (don't bet YES)."""
    f = kelly_yes(model_prob=0.40, market_prob=0.55)
    assert f < 0


def test_kelly_no_positive_edge():
    """Market YES = 0.70, model YES = 0.55 -> buy NO."""
    f = kelly_no(model_prob=0.55, market_prob=0.70)
    # f* = (0.70 - 0.55) / 0.70 = 0.214
    assert f == pytest.approx(0.2143, abs=0.01)


def test_kelly_no_spec_worked_example():
    """Spec worked example: market YES=0.70, model YES=0.55."""
    f = kelly_no(model_prob=0.55, market_prob=0.70)
    assert f == pytest.approx(0.214, abs=0.01)


def test_kelly_fraction_yes_bet():
    """Full pipeline: positive edge -> YES bet with fractional Kelly."""
    result = kelly_fraction(
        model_prob=0.70, market_prob=0.55,
        fractional=0.25, confidence=85,
        max_fraction=0.10,  # raise cap to test unclamped value
    )
    # raw = 0.333, adjusted = 0.333 * 0.25 * (85/100) = 0.0708
    assert result["side"] == "yes"
    assert result["fraction"] == pytest.approx(0.0708, abs=0.005)
    assert result["raw_kelly"] == pytest.approx(0.333, abs=0.01)


def test_kelly_fraction_no_bet():
    """Negative edge -> NO bet with correct Kelly math."""
    result = kelly_fraction(
        model_prob=0.40, market_prob=0.55,
        fractional=0.25, confidence=90,
        max_fraction=0.10,  # raise cap to test unclamped value
    )
    assert result["side"] == "no"
    # raw = (0.55 - 0.40) / 0.55 = 0.2727
    assert result["raw_kelly"] == pytest.approx(0.2727, abs=0.01)
    # adjusted = 0.2727 * 0.25 * (90/100) = 0.0614
    assert result["fraction"] == pytest.approx(0.0614, abs=0.005)


def test_kelly_yes_market_at_one():
    """Market price at 1.0 -> no YES bet possible."""
    assert kelly_yes(0.95, 1.0) == 0.0


def test_kelly_no_market_at_zero():
    """Market price at 0.0 -> no NO bet possible."""
    assert kelly_no(0.05, 0.0) == 0.0


def test_kelly_fraction_no_trade():
    """Tiny edge -> fraction rounds to 0 -> no trade."""
    result = kelly_fraction(
        model_prob=0.50, market_prob=0.50,
        fractional=0.25, confidence=50,
    )
    assert result["side"] is None
    assert result["fraction"] == 0.0


def test_kelly_fraction_clamps_extreme():
    """Even at huge edge, fraction never exceeds max_fraction."""
    result = kelly_fraction(
        model_prob=0.99, market_prob=0.10,
        fractional=0.5, confidence=95,
        max_fraction=0.03,
    )
    assert result["fraction"] <= 0.03
