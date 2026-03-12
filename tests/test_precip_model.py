import pytest
from weather.precip_model import empirical_precip_prob, PrecipForecast, gamma_precip_prob


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


def test_csgd_all_dry():
    """All members = 0 → P(>X) = 0 for any X > 0."""
    result = gamma_precip_prob([0.0] * 30, threshold=0.5, nws_pop=0.0)
    assert result.prob_above == 0.0
    assert result.method == "csgd"


def test_csgd_all_wet_above_zero():
    """All members > 0, threshold=0.0 → P(>0.0) = 1.0 (or very close)."""
    members = [0.5 + i * 0.1 for i in range(30)]
    result = gamma_precip_prob(members, threshold=0.0, nws_pop=1.0)
    assert result.prob_above == pytest.approx(1.0, abs=0.01)


def test_csgd_few_nonzero_falls_back():
    """Fewer than 3 non-zero → fallback to empirical."""
    members = [0.0] * 28 + [1.0, 2.0]
    result = gamma_precip_prob(members, threshold=0.5, nws_pop=0.1)
    assert result.method == "empirical"  # fallback


def test_csgd_reasonable_probability():
    """25 zeros, 5 non-zero [0.1, 0.2, 0.3, 0.5, 1.0] → P(>0.25) roughly 0.05-0.20."""
    members = [0.0] * 25 + [0.1, 0.2, 0.3, 0.5, 1.0]
    result = gamma_precip_prob(members, threshold=0.25, nws_pop=0.2)
    assert 0.01 < result.prob_above < 0.30
    assert result.method == "csgd"
    assert result.shape > 0
    assert result.scale > 0
