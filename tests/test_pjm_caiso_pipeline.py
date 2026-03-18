"""Tests for PJM and CAISO pipeline config entries."""


def test_pjm_config_in_all_configs():
    from pipeline.config import ALL_CONFIGS
    names = {c.name for c in ALL_CONFIGS}
    assert "pjm" in names


def test_caiso_config_in_all_configs():
    from pipeline.config import ALL_CONFIGS
    names = {c.name for c in ALL_CONFIGS}
    assert "caiso" in names


def test_pjm_config_fields():
    from pipeline.config import ALL_CONFIGS
    pjm = [c for c in ALL_CONFIGS if c.name == "pjm"][0]
    assert pjm.display_name == "PJM Solar"
    assert pjm.exchange == "pjm"
    assert pjm.edge_gate == 0.03
    assert pjm.confidence_gate == 50
    assert callable(pjm.fetch_fn)
    assert callable(pjm.forecast_fn)
    assert callable(pjm.manage_fn)


def test_caiso_config_fields():
    from pipeline.config import ALL_CONFIGS
    caiso = [c for c in ALL_CONFIGS if c.name == "caiso"][0]
    assert caiso.display_name == "CAISO Solar"
    assert caiso.exchange == "caiso"
    assert callable(caiso.fetch_fn)
    assert callable(caiso.forecast_fn)
    assert callable(caiso.manage_fn)


def test_all_configs_count():
    """Should now have 5 configs: kalshi_temp, kalshi_precip, ercot, pjm, caiso."""
    from pipeline.config import ALL_CONFIGS
    assert len(ALL_CONFIGS) == 5
