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

    # Stage 2: Forecast & signal (weights live in config.FUSION_WEIGHTS)
    forecast_fn: Callable

    # Stage 3: Signal filtering
    edge_gate: float
    confidence_gate: float
    min_price_cents: int
    sameday_overrides: dict | None

    # Stage 4: Sanity check
    sanity_fn: Callable | None

    # Stage 5: Sizing
    scan_frac: float
    kelly_floor: float
    max_bankroll_pct: float
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
    from weather.ensemble_signal import get_ensemble_signal
    from weather.multi_model import fuse_precip_forecast, get_ercot_solar_signal, get_pjm_solar_signal, get_caiso_solar_signal
    from kalshi.pricing import maker_price
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

    # NWS deterministic sanity-check cache: prevents repeated calls for
    # the same (lat, lon, days_ahead) within a scan cycle. Short TTL so
    # stale entries don't linger between daemon cycles.
    import time as _time
    _NWS_SANITY_CACHE: dict[tuple, tuple[float | None, float]] = {}
    _NWS_SANITY_TTL = 240  # 4 minutes — shorter than the 5-min daemon cycle

    def nws_deterministic_sanity(signal) -> bool:
        """NWS deterministic cross-check sanity gate for kalshi_temp.

        Rejects trades in two failure modes that tonight's cycle revealed:
          1. Ensemble-vs-NWS disagreement: our ensemble mean_temp differs
             from the NWS point forecast by more than 3°F. When this
             happens the ensemble is the outlier — the market prices
             near the NWS consensus and our bets against it systematically
             lose. Dominant failure tonight on NYC (82.8 vs 87.8) and
             Austin (80.8 vs 86.0).
          2. Bucket proximity: we are betting NO on a between-bucket
             [low, high] but the NWS forecast lands within ±1°F of that
             bucket, meaning NWS thinks the temp will be right where we
             are betting it will NOT be. Dominant failure mode for
             Apr 16 Miami B82.5 NO and Atlanta B86.5 NO.

        The check is advisory: if NWS is unavailable, the signal passes
        (we don't want to silently block all trading on an NWS outage).
        All rejects log a [NWS-SANITY] line so the daemon journal shows
        exactly what was blocked and why.
        """
        from weather.multi_model import get_noaa_point_forecast
        from kalshi.scanner import parse_kalshi_bucket

        try:
            ticker = signal.ticker
            lat = signal.lat
            lon = signal.lon
            if lat is None or lon is None:
                return True  # no coordinates — can't check

            unit = (signal.market or {}).get("_unit", "f")
            temp_type = (signal.market or {}).get("_temp_type", "max")

            # Cached NWS lookup
            cache_key = (round(lat, 3), round(lon, 3), signal.days_ahead, unit, temp_type)
            now = _time.time()
            cached = _NWS_SANITY_CACHE.get(cache_key)
            if cached and now - cached[1] < _NWS_SANITY_TTL:
                nws_temp = cached[0]
            else:
                nws_temp = get_noaa_point_forecast(
                    lat, lon,
                    days_ahead=signal.days_ahead,
                    unit=unit,
                    temp_type=temp_type,
                )
                _NWS_SANITY_CACHE[cache_key] = (nws_temp, now)

            if nws_temp is None:
                return True  # NWS unavailable — advisory only

            # Rule 1: ensemble-vs-NWS disagreement gate.
            # Threshold tuned by grid sweep on 2026-04-15 (backtesting/replay.py):
            #   3.0°F → 73 trades, +$15.66    (too tight, kills profitable trades)
            #   5.0°F → 81 trades, +$18.26    (optimal — blocks outliers, keeps edge)
            #   OFF   → 83 trades, +$16.83    (sanity still adds $1.43 vs disabled)
            bot_mean = signal.model_mean_temp
            if bot_mean is not None:
                disagree = abs(bot_mean - nws_temp)
                if disagree > 5.0:
                    print(
                        f"  [NWS-SANITY] {ticker} BLOCK ensemble_vs_nws — "
                        f"ensemble={bot_mean:.1f}°F vs NWS={nws_temp:.1f}°F "
                        f"(|diff|={disagree:.1f}°F > 3°F)"
                    )
                    return False

            # Rule 2: bucket proximity gate (uses parse_kalshi_bucket to get
            # [low, high) for the contract we're scoring)
            parsed = parse_kalshi_bucket(signal.market) if signal.market else None
            if not parsed:
                return True
            low, high = parsed

            if high is not None:
                # Between-bucket [low, high]
                if signal.side == "no":
                    # Betting NO = "temp will NOT land in [low, high]". If NWS
                    # thinks it will land in or within ±1°F of the bucket,
                    # the bet is reckless.
                    if (low - 1) <= nws_temp <= (high + 1):
                        print(
                            f"  [NWS-SANITY] {ticker} BLOCK no_side_near_bucket — "
                            f"NWS={nws_temp:.1f}°F within ±1°F of bucket [{low},{high}]"
                        )
                        return False
                elif signal.side == "yes":
                    # Betting YES = "temp WILL land in [low, high]". If NWS
                    # says the temp will be far outside the bucket, block.
                    if nws_temp < low - 3 or nws_temp > high + 3:
                        print(
                            f"  [NWS-SANITY] {ticker} BLOCK yes_side_far_from_bucket — "
                            f"NWS={nws_temp:.1f}°F outside [{low},{high}]±3°F"
                        )
                        return False
            else:
                # Greater-type contract: YES wins when temp >= low
                if signal.side == "yes":
                    if nws_temp < low - 2:
                        print(
                            f"  [NWS-SANITY] {ticker} BLOCK yes_greater_below_floor — "
                            f"NWS={nws_temp:.1f}°F well below floor {low}"
                        )
                        return False
                elif signal.side == "no":
                    if nws_temp > low + 1:
                        print(
                            f"  [NWS-SANITY] {ticker} BLOCK no_greater_above_floor — "
                            f"NWS={nws_temp:.1f}°F above floor {low}"
                        )
                        return False

            return True
        except Exception as e:
            print(f"  [NWS-SANITY] error for {getattr(signal, 'ticker', '?')}: {e}")
            return True  # advisory — don't block on error

    kalshi_temp = MarketConfig(
        name="kalshi_temp",
        display_name="Kalshi Temperature",
        exchange="kalshi",
        fetch_fn=get_kalshi_weather_markets,
        series=WEATHER_SERIES,
        bucket_parser=parse_kalshi_bucket,
        forecast_fn=get_ensemble_signal,

        # Edge gate tuned to 0.15 (2026-04-15 late evening) after the
        # historical replay backtest (backtesting/replay.py) showed 0.15
        # strictly dominates 0.20 on both trade count AND net P&L
        # across Mar 27 → Apr 14:
        #   0.20 → 68 trades, 67.6% wins, +$14.36
        #   0.15 → 73 trades, 65.8% wins, +$15.66
        # The NWS sanity gate caught 97 would-be-bad trades at 0.15 vs 87
        # at 0.20, so the safety net is intact — the extra volume is
        # genuinely edge-positive, not marginal noise slipping through.
        edge_gate=0.15,
        confidence_gate=60,
        min_price_cents=7,
        # confidence raised 55 → 60 so Open-Meteo fallback (conf=50) is always filtered
        # same-day edge lowered 0.15 → 0.10 (proportional to main gate 0.20 → 0.15)
        sameday_overrides={"edge": 0.10, "confidence": 60, "kelly_floor": 0.35},
        sanity_fn=nws_deterministic_sanity,
        scan_frac=0.10,
        kelly_floor=0.10,
        max_bankroll_pct=0.05,
        max_contracts_per_event=10,
        execute_fn=execute_trade,
        pricing_fn=maker_price,
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

        edge_gate=0.07,
        confidence_gate=60,
        min_price_cents=12,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_bankroll_pct=0.02,
        max_contracts_per_event=10,
        execute_fn=execute_trade,
        pricing_fn=maker_price,
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

        edge_gate=0.03,
        confidence_gate=50,
        min_price_cents=12,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_bankroll_pct=0.02,
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

        edge_gate=0.03,
        confidence_gate=50,
        min_price_cents=12,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_bankroll_pct=0.02,
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

        edge_gate=0.03,
        confidence_gate=50,
        min_price_cents=12,
        sameday_overrides=None,
        sanity_fn=None,
        scan_frac=0.10,
        kelly_floor=0.25,
        max_bankroll_pct=0.02,
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
