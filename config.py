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

EDGE_THRESHOLD = 0.105      # trade threshold (10.5%)
SHOW_THRESHOLD = 0.07       # display threshold (shows interesting edges)
DUTCH_BOOK_THRESHOLD = 0.975
GAMMA_BASE = "https://gamma-api.polymarket.com"
SCAN_LIMIT = 300

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALERT_THRESHOLD = 0.105   # only alert on |edge| >= 10.5%

_pk = os.getenv("PRIVATE_KEY", "")
PRIVATE_KEY = _pk if _pk.startswith("0x") else f"0x{_pk}" if _pk else None
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS")
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
MAX_POSITION_USD = 50.0
MAX_SCAN_BUDGET = 10.0    # max dollars to deploy per scan cycle
MAX_ORDER_USD = 2.0       # max dollars per individual order

# Multi-model fusion
BIAS_DB_PATH = "data/bias.db"
CONFIDENCE_THRESHOLD = 70
FUSION_WEIGHTS = {"ensemble": 0.40, "noaa": 0.35, "hrrr": 0.25}
HIGH_CONFIDENCE_MULTIPLIER = 1.5  # size boost when confidence >= 85
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 0  # 0 = EOA (direct wallet signing)

# Risk / Kelly sizing (Phase 2)
FRACTIONAL_KELLY = 0.25       # start at 25% Kelly
KELLY_MAX_FRACTION = 0.03    # max 3% of bankroll per order
DRAWDOWN_THRESHOLD = 0.15    # 15% drawdown triggers circuit breaker
DAILY_STOP_PCT = 0.05        # -5% daily P&L stops trading
CIRCUIT_BREAKER_COOLDOWN_HOURS = 48
