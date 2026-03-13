import os
from dotenv import load_dotenv
load_dotenv()

CITIES = {
    "nyc": {"lat": 40.7931, "lon": -73.8720, "keywords": ["nyc", "new york", "laguardia"], "unit": "f"},
    "chicago": {"lat": 41.9742, "lon": -87.9073, "keywords": ["chicago", "o'hare"], "unit": "f"},
    "miami": {"lat": 25.7617, "lon": -80.1918, "keywords": ["miami"], "unit": "f"},
    "seattle": {"lat": 47.4502, "lon": -122.3088, "keywords": ["seattle"], "unit": "f"},
    "dallas": {"lat": 32.8968, "lon": -97.0380, "keywords": ["dallas"], "unit": "f"},
    "atlanta": {"lat": 33.6407, "lon": -84.4277, "keywords": ["atlanta"], "unit": "f"},
    "buenos_aires": {"lat": -34.8222, "lon": -58.5358, "keywords": ["buenos aires", "ezeiza", "argentina"], "unit": "c"},
    "london": {"lat": 51.5053, "lon": 0.0553, "keywords": ["london"], "unit": "c"},
    "paris": {"lat": 48.8566, "lon": 2.3522, "keywords": ["paris"], "unit": "c"},
    "seoul": {"lat": 37.4633, "lon": 126.4400, "keywords": ["seoul", "incheon"], "unit": "c"},
    "tokyo": {"lat": 35.6762, "lon": 139.6503, "keywords": ["tokyo"], "unit": "c"},
}

EDGE_THRESHOLD = 0.07       # trade threshold (7%)
SHOW_THRESHOLD = 0.05       # display threshold (shows interesting edges)
DUTCH_BOOK_THRESHOLD = 0.975
GAMMA_BASE = "https://gamma-api.polymarket.com"
SCAN_LIMIT = 300

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALERT_THRESHOLD = 0.07    # only alert/trade on |edge| >= 7%

_pk = os.getenv("PRIVATE_KEY", "")
PRIVATE_KEY = _pk if _pk.startswith("0x") else f"0x{_pk}" if _pk else None
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS")
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
MAX_POSITION_USD = 50.0
MAX_SCAN_BUDGET = 60.0    # max dollars to deploy per scan cycle
MAX_ORDER_USD = 20.0      # max dollars per individual order

# Multi-model fusion
BIAS_DB_PATH = "data/bias.db"
CONFIDENCE_THRESHOLD = 55
FUSION_WEIGHTS = {"ensemble": 0.40, "noaa": 0.35, "hrrr": 0.25}
HIGH_CONFIDENCE_MULTIPLIER = 1.5  # size boost when confidence >= 85
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 0  # 0 = EOA (direct wallet signing)

# Risk / Kelly sizing (Phase 2)
FRACTIONAL_KELLY = 0.25       # base Kelly multiplier (sigmoid floor)

# Liquidity filter — skip thin markets
MIN_VOLUME_24H = 500        # minimum 24h volume (contracts)
MIN_OPEN_INTEREST = 500     # minimum open interest (contracts)
KELLY_MAX_FRACTION = 0.03    # max 3% of bankroll per order
DRAWDOWN_THRESHOLD = 0.15    # 15% drawdown triggers circuit breaker
DAILY_STOP_PCT = 0.05        # -5% daily P&L stops trading
CIRCUIT_BREAKER_COOLDOWN_HOURS = 48

# Same-day aggressive pass — forecasts are near-locked, exploit harder
SAMEDAY_EDGE_THRESHOLD = 0.05      # 5% edge (vs 7% for multi-day)
SAMEDAY_CONFIDENCE_THRESHOLD = 50  # 50% confidence (vs 55%)
SAMEDAY_KELLY_FLOOR = 0.35         # start at 0.35x Kelly (vs 0.25x)

# Precipitation fusion weights (Phase 4)
PRECIP_FUSION_WEIGHTS = {"ensemble": 0.50, "noaa": 0.30, "hrrr": 0.20}
MAX_ENSEMBLE_HORIZON_DAYS = 16  # Open-Meteo ensemble limit; skip monthly contracts beyond this
