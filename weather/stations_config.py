"""Settlement station mapping: (city, market_type) → NOAA station metadata.

Curated manually from each Kalshi market's rules page. Used for settlement
verification, logging, and future auto-resolver. Not in the hot trading path.

To add a station: check the market rules page on Kalshi for the exact
NOAA station ID and CLI report link.
"""

STATIONS: dict[tuple[str, str], dict] = {
    # Precipitation stations (from Kalshi KXRAIN* market rules)
    ("nyc", "precip"): {
        "station": "USW00094728",
        "name": "Central Park",
        "report": "CLI",
    },
    ("chicago", "precip"): {
        "station": "USW00094846",
        "name": "O'Hare International",
        "report": "CLI",
    },
    ("los_angeles", "precip"): {
        "station": "USW00023174",
        "name": "Los Angeles International",
        "report": "CLI",
    },
    ("seattle", "precip"): {
        "station": "USW00024233",
        "name": "Seattle-Tacoma International",
        "report": "CLI",
    },
    ("miami", "precip"): {
        "station": "USW00012839",
        "name": "Miami International",
        "report": "CLI",
    },
    ("houston", "precip"): {
        "station": "USW00012960",
        "name": "Houston Intercontinental",
        "report": "CLI",
    },
    ("san_francisco", "precip"): {
        "station": "USW00023234",
        "name": "San Francisco International",
        "report": "CLI",
    },
}


def get_station(city: str, market_type: str) -> dict | None:
    """Look up station metadata for a city/market_type pair."""
    return STATIONS.get((city, market_type))
