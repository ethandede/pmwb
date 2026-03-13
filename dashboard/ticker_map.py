# dashboard/ticker_map.py
"""Ticker-to-city display name mapping.

Builds a reverse-lookup dict from WEATHER_SERIES and PRECIP_SERIES
at import time. Handles KXHIGH*, KXLOWT*, KXRAIN*, KXRAIN*CM prefixes.
"""
from kalshi.scanner import WEATHER_SERIES, PRECIP_SERIES

# Display name overrides for slug → human-readable
_CITY_DISPLAY = {
    "nyc": "NYC",
    "chicago": "Chicago",
    "miami": "Miami",
    "austin": "Austin",
    "los_angeles": "Los Angeles",
    "seattle": "Seattle",
    "houston": "Houston",
    "san_francisco": "San Francisco",
    "atlanta": "Atlanta",
    "washington_dc": "Washington DC",
    "boston": "Boston",
    "phoenix": "Phoenix",
    "san_antonio": "San Antonio",
    "las_vegas": "Las Vegas",
}


def _build_prefix_map() -> dict[str, str]:
    """Build ticker-prefix → display-city-name mapping."""
    m: dict[str, str] = {}

    for prefix, info in WEATHER_SERIES.items():
        city_slug = info["city"]
        display = _CITY_DISPLAY.get(city_slug, city_slug.replace("_", " ").title())
        m[prefix] = display
        # Register KXLOWT variant: replace only the KXHIGH portion (not the T in KXHIGHT)
        # KXHIGHNY   → KXLOWTNY
        # KXHIGHTSEA → KXLOWTSEA  (KXHIGHT → KXLOWT, leaving SEA)
        # Both cases: replace leading "KXHIGH" with "KXLOWT"
        if prefix.startswith("KXHIGH"):
            low_prefix = "KXLOWT" + prefix[len("KXHIGH"):]
            m[low_prefix] = display

    for prefix, info in PRECIP_SERIES.items():
        city_slug = info["city"]
        display = _CITY_DISPLAY.get(city_slug, city_slug.replace("_", " ").title())
        m[prefix] = display
        # Register progressively shorter variants so daily tickers (which drop
        # trailing city-code characters) still resolve.
        # e.g. KXRAINNYCM → also register KXRAINNYC, KXRAINNY, KXRAINN
        # Stop before we'd strip into the "KXRAIN" root (length 6).
        root = "KXRAIN"
        stripped = prefix
        while len(stripped) > len(root) + 1:
            stripped = stripped[:-1]
            if stripped not in m:
                m[stripped] = display

    return m


_PREFIX_MAP = _build_prefix_map()


def ticker_to_city(ticker: str) -> str:
    """Convert a Kalshi ticker like KXHIGHNY-26MAR13-B55 to a display city name.

    Returns the raw prefix if no match is found.
    """
    prefix = ticker.split("-")[0] if "-" in ticker else ticker
    return _PREFIX_MAP.get(prefix, prefix)
