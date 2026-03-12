import pytest
from weather.precip_model import empirical_precip_prob, PrecipForecast


def test_empirical_all_dry():
    """All 30 members predict 0 → P(>0.5) = 0."""
    result = empirical_precip_prob([0.0] * 30, threshold=0.5)
    assert result.prob_above == 0.0
    assert result.p_dry == 1.0
    assert result.method == "empirical"


def test_empirical_all_wet():
    """All 30 members predict > 0 → P(>0.0) = 1.0."""
    members = [0.5 + i * 0.1 for i in range(30)]
    result = empirical_precip_prob(members, threshold=0.0)
    assert result.prob_above == 1.0
    assert result.p_dry == 0.0


def test_empirical_mixed():
    """15 dry, 15 wet (0.5–3.0) → P(>2.0) counts wet members above 2.0."""
    wet = [0.5 + i * (2.5 / 14) for i in range(15)]  # 0.5 to 3.0
    members = [0.0] * 15 + wet
    result = empirical_precip_prob(members, threshold=2.0)
    expected_count = sum(1 for v in members if v > 2.0)
    assert result.prob_above == pytest.approx(expected_count / 30, abs=0.01)
    assert result.p_dry == pytest.approx(0.5)
    assert result.fraction_above == pytest.approx(expected_count / 30, abs=0.01)


def test_empirical_empty_members():
    """Empty member list → P(>X) = 0, p_dry = 1.0."""
    result = empirical_precip_prob([], threshold=1.0)
    assert result.prob_above == 0.0
    assert result.p_dry == 1.0
