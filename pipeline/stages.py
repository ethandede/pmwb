"""Generic pipeline stage functions.

Each stage takes a MarketConfig + inputs, returns outputs.
No globals, no module state — everything through parameters.
"""

import re
from datetime import date

from pipeline.types import Signal, CycleState, TradeResult


def fetch_markets(config, exchange) -> list[dict]:
    """Stage 1: Call config's fetch function to discover markets.

    Existing Kalshi fetch functions use the public API directly (no exchange needed).
    ERCOT fetch_ercot_markets also needs no exchange param.
    The exchange adapter is available for future use.
    """
    return config.fetch_fn()


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
        side = "no" if ercot_signal == "SHORT" else "yes"
        days_ahead = 0
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

        # Extract model_prob and confidence from forecast result
        if isinstance(forecast_result, tuple) and len(forecast_result) >= 2:
            model_prob = forecast_result[0]
            confidence = forecast_result[1]
        elif hasattr(forecast_result, 'prob'):
            model_prob = forecast_result.prob
            confidence = forecast_result.confidence
        else:
            model_prob = float(forecast_result)
            confidence = 50.0

        edge = model_prob - market_prob
        side = "yes" if edge > 0 else "no"

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
        days_ahead=days_ahead,
        yes_bid=market.get("yes_bid"),
        yes_ask=market.get("yes_ask"),
        lat=market.get("_lat") or market.get("lat"),
        lon=market.get("_lon") or market.get("lon"),
        market=market,
    )


def filter_signals(config, signals: list[Signal], held_positions: list,
                   resting_tickers: set[str]) -> list[Signal]:
    """Stage 3: Apply edge gate, confidence gate, liquidity, dedup, cross-contract.

    Returns filtered and de-conflicted signal list, sorted by absolute edge descending.
    """
    results = []

    # Sort by absolute edge descending (strongest signals first)
    ranked = sorted(signals, key=lambda s: abs(s.edge), reverse=True)

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
        if signal.market and config.exchange == "kalshi":
            volume = float(signal.market.get("volume_24h_fp", 0) or 0)
            oi = float(signal.market.get("open_interest_fp", 0) or 0)
            if volume < 500 and oi < 500:
                continue

        results.append(signal)

    return results


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
    import calendar
    today = date.today()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return last_day - today.day + 1


def _extract_month(ticker: str) -> int:
    """Extract month number from ticker like KXHIGHNY-26MAR15-T56."""
    parts = ticker.split("-")
    if len(parts) >= 2:
        m = re.match(r"\d{2}([A-Z]{3})", parts[1])
        if m:
            months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                      "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
            return months.get(m.group(1), 3)
    return date.today().month
