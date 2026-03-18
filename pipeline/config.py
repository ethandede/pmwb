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
# Imports are deferred inside _build_configs to avoid circular imports at module load.

def _build_configs() -> tuple:
    """Build all 5 configs. Called once at import time."""
    from kalshi.scanner import (
        get_kalshi_weather_markets, get_kalshi_precip_markets,
        parse_kalshi_bucket, WEATHER_SERIES, PRECIP_SERIES,
    )
    from kalshi.market_types import parse_precip_bucket
    from weather.multi_model import fuse_forecast, fuse_precip_forecast, get_ercot_solar_signal, get_pjm_solar_signal, get_caiso_solar_signal
    from kalshi.pricing import choose_price_strategy
    from ercot.hubs import fetch_ercot_markets
    from pjm.hubs import fetch_pjm_markets
    from caiso.hubs import fetch_caiso_markets
    from config import ERCOT_HUBS, PJM_HUBS, CAISO_HUBS

    from pipeline.stages import execute_trade
    from kalshi.position_manager import run_position_manager
    from kalshi.settler import run_settler
    from ercot.position_manager import run_ercot_manager
    from pjm.position_manager import run_pjm_manager
    from caiso.position_manager import run_caiso_manager

    def gfs_temp_sanity(signal) -> bool:
        """GFS cross-reference sanity check for temperature signals.

        Fetches GFS forecast from self-hosted server and blocks trades where
        the forecast strongly contradicts the signal direction.
        """
        try:
            import requests as _req
            bucket = signal.market
            ticker = signal.ticker
            lat = signal.lat or 0
            lon = signal.lon or 0
            unit = signal.market.get("_unit", "f") if signal.market else "f"
            temp_type = signal.market.get("_temp_type", "max") if signal.market else "max"
            unit_param = "fahrenheit" if unit == "f" else "celsius"
            daily_var = f"temperature_2m_{temp_type}"

            r = _req.get(
                f"http://localhost:8080/v1/forecast?latitude={lat}&longitude={lon}"
                f"&daily={daily_var}&models=gfs_seamless"
                f"&temperature_unit={unit_param}&timezone=auto&forecast_days=2",
                timeout=5,
            )
            gfs_temp = r.json().get("daily", {}).get(daily_var, [None])[0]
            if gfs_temp is None:
                return True

            # Parse bucket from market data
            from kalshi.scanner import parse_kalshi_bucket
            parsed = parse_kalshi_bucket(signal.market) if signal.market else None
            if not parsed:
                return True
            low, high = parsed

            if high is None and low is not None:
                # "above X" contract
                if signal.side == "yes" and gfs_temp < low - 3:
                    return False
                if signal.side == "no" and gfs_temp > low + 3:
                    return False
            elif low is not None and high is not None:
                mid = (low + high) / 2
                if signal.side == "yes" and abs(gfs_temp - mid) > 15:
                    return False
            return True
        except Exception:
            return True  # sanity check is advisory

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
        execute_fn=execute_trade,
        pricing_fn=choose_price_strategy,
        manage_fn=run_position_manager,
        exit_rules={"reversal_edge": -0.08, "sameday_reversal": -0.15,
                    "profit_take_pct": 0.88, "min_confidence": 70},
        settlement_timeline="daily",
        settle_fn=run_settler,
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
        execute_fn=execute_trade,
        pricing_fn=choose_price_strategy,
        manage_fn=run_position_manager,
        exit_rules={"reversal_edge": -0.10, "profit_take_pct": 0.90,
                    "min_confidence": 60},
        settlement_timeline="monthly",
        settle_fn=run_settler,
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
        edge_gate=0.03,
        confidence_gate=50,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_contracts_per_event=3,
        execute_fn=execute_trade,
        pricing_fn=None,
        manage_fn=run_ercot_manager,
        exit_rules={},
        settlement_timeline="hourly_binary",
        settle_fn=run_ercot_manager,
    )

    pjm = MarketConfig(
        name="pjm",
        display_name="PJM Solar",
        exchange="pjm",
        fetch_fn=fetch_pjm_markets,
        series=PJM_HUBS,
        bucket_parser=None,
        forecast_fn=get_pjm_solar_signal,
        fusion_weights=None,
        edge_gate=0.03,
        confidence_gate=50,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_contracts_per_event=3,
        execute_fn=execute_trade,
        pricing_fn=None,
        manage_fn=run_pjm_manager,
        exit_rules={"edge_decay_pct": 0.30, "signal_flip": True, "ttl_hours": 24},
        settlement_timeline="hourly",
        settle_fn=run_pjm_manager,
    )

    caiso = MarketConfig(
        name="caiso",
        display_name="CAISO Solar",
        exchange="caiso",
        fetch_fn=fetch_caiso_markets,
        series=CAISO_HUBS,
        bucket_parser=None,
        forecast_fn=get_caiso_solar_signal,
        fusion_weights=None,
        edge_gate=0.03,
        confidence_gate=50,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_contracts_per_event=3,
        execute_fn=execute_trade,
        pricing_fn=None,
        manage_fn=run_caiso_manager,
        exit_rules={"edge_decay_pct": 0.30, "signal_flip": True, "ttl_hours": 24},
        settlement_timeline="hourly",
        settle_fn=run_caiso_manager,
    )

    return kalshi_temp, kalshi_precip, ercot, pjm, caiso


KALSHI_TEMP, KALSHI_PRECIP, ERCOT, PJM, CAISO = _build_configs()
ALL_CONFIGS = [KALSHI_TEMP, KALSHI_PRECIP, ERCOT, PJM, CAISO]
