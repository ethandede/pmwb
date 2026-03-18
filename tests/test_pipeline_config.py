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
    assert ERCOT.settlement_timeline == "hourly_binary"


def test_all_configs_have_required_callables():
    """Every config must have fetch_fn, forecast_fn, execute_fn, manage_fn, settle_fn."""
    for cfg in [KALSHI_TEMP, KALSHI_PRECIP, ERCOT]:
        assert callable(cfg.fetch_fn), f"{cfg.name} missing fetch_fn"
        assert callable(cfg.forecast_fn), f"{cfg.name} missing forecast_fn"
        assert callable(cfg.execute_fn), f"{cfg.name} missing execute_fn"
        assert callable(cfg.manage_fn), f"{cfg.name} missing manage_fn"
        assert callable(cfg.settle_fn), f"{cfg.name} missing settle_fn"


def test_ercot_edge_gate_recalibrated():
    """ERCOT edge gate should be 0.03 for the new fair-price model."""
    assert ERCOT.edge_gate == 0.03
