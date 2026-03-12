"""Unified market type enum and bucket parsers for Kalshi weather markets.

Supports temperature (existing), precipitation (Phase 4), and snow (Phase 4b).
"""

import logging
import re
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class MarketType(Enum):
    HIGH_TEMP = "high_temp"
    LOW_TEMP = "low_temp"
    PRECIP = "precip"      # monthly cumulative + daily binary rain
    SNOW = "snow"          # Phase 4b


def detect_market_type(ticker: str) -> MarketType:
    """Detect market type from ticker string.

    Examples:
        KXHIGHNY-26MAR12-B65 → HIGH_TEMP
        KXLOWTNYC-26MAR12-B30 → LOW_TEMP
        kxrainchim-26mar-4 → PRECIP
        kxnycsnowm-26mar-3 → SNOW
    """
    t = ticker.upper()
    if "RAIN" in t:
        return MarketType.PRECIP
    if "SNOW" in t:
        return MarketType.SNOW
    if "LOW" in t:
        return MarketType.LOW_TEMP
    if "HIGH" in t:
        return MarketType.HIGH_TEMP
    logger.warning(f"Unknown ticker pattern: {ticker}, defaulting to HIGH_TEMP")
    return MarketType.HIGH_TEMP


def parse_precip_bucket(market: dict) -> Optional[Tuple[float, Optional[float]]]:
    """Parse precipitation threshold from Kalshi market data.

    Returns (threshold, None) in the same shape as parse_kalshi_bucket()
    for temperature so downstream edge computation works identically.
    For "above X inches" → (X, None). For daily binary → (0.0, None).

    Returns None if no threshold can be parsed.
    """
    # Primary: use strike data if available
    strike_type = market.get("strike_type", "")
    floor_strike = market.get("floor_strike")

    if strike_type == "greater" and floor_strike is not None:
        return (float(floor_strike), None)

    # Secondary: regex on ticker for trailing number (e.g., kxrainchim-26mar-4.5)
    ticker = market.get("ticker", "")
    m = re.search(r'-(\d+\.?\d*)$', ticker)
    if m:
        return (float(m.group(1)), None)

    # Tertiary: parse from title ("above X inches", "> X")
    title = market.get("title", "") + " " + market.get("yes_sub_title", "")
    m = re.search(r'(?:above|over|>|≥)\s*(\d+\.?\d*)\s*(?:in|inch)', title, re.IGNORECASE)
    if m:
        return (float(m.group(1)), None)

    return None
