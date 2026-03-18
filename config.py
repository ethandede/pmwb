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

# HARD RISK RAILS — Super Heavy Grok (March 2026)
MIN_TRADE_EDGE = 0.12                    # Only trade |edge| >= 12%
MAX_POSITIONS_TOTAL = 50                 # Never exceed 50 open positions
MAX_BANKROLL_PCT_PER_TRADE = 0.02        # Hard 2% bankroll cap per trade
SKIP_RAIN_MARKETS = False                # Rain markets re-enabled

DAILY_LOSS_BREAKER_PCT = 0.05            # -5% daily P&L stops new entries

EDGE_THRESHOLD = 0.07
SHOW_THRESHOLD = 0.05
DUTCH_BOOK_THRESHOLD = 0.975
GAMMA_BASE = "https://gamma-api.polymarket.com"
SCAN_LIMIT = 300

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALERT_THRESHOLD = 0.07

_pk = os.getenv("PRIVATE_KEY", "")
PRIVATE_KEY = _pk if _pk.startswith("0x") else f"0x{_pk}" if _pk else None
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS")
PAPER_MODE = True  # Paper mode until fixes are verified (2026-03-15)
MAX_POSITION_USD = 50.0
MAX_SCAN_BUDGET = 60.0
MAX_ORDER_USD = 20.0

BIAS_DB_PATH = "data/bias.db"
CONFIDENCE_THRESHOLD = 60
VISUAL_CROSSING_API_KEY = os.getenv("VISUAL_CROSSING_API_KEY", "")
FUSION_WEIGHTS = {"ensemble": 0.15, "noaa": 0.10, "hrrr": 0.30, "visualcrossing": 0.20, "ecmwf": 0.25}
HIGH_CONFIDENCE_MULTIPLIER = 1.5

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 0

FRACTIONAL_KELLY = 0.25

MIN_VOLUME_24H = 500
MIN_OPEN_INTEREST = 500
KELLY_MAX_FRACTION = 0.03
DRAWDOWN_THRESHOLD = 0.15
CIRCUIT_BREAKER_COOLDOWN_HOURS = 48

SAMEDAY_EDGE_THRESHOLD = 0.05
SAMEDAY_CONFIDENCE_THRESHOLD = 45
SAMEDAY_KELLY_FLOOR = 0.35

PRECIP_FUSION_WEIGHTS = {"ensemble": 0.60, "noaa": 0.40}
MAX_ENSEMBLE_HORIZON_DAYS = 16

# METAR station mapping — city internal name → ICAO airport code
# Stations chosen to match Kalshi settlement observation points
METAR_STATIONS = {
    "nyc": "KLGA",           # LaGuardia — matches Kalshi NYC settlement
    "chicago": "KORD",       # O'Hare
    "miami": "KMIA",         # Miami Intl
    "austin": "KAUS",        # Austin-Bergstrom
    "los_angeles": "KLAX",   # LAX
    "seattle": "KSEA",       # Sea-Tac
    "houston": "KIAH",       # George Bush Intercontinental
    "san_francisco": "KSFO", # SFO
    "atlanta": "KATL",       # Hartsfield-Jackson
    "washington_dc": "KDCA", # Reagan National
    "boston": "KBOS",         # Logan
    "phoenix": "KPHX",       # Sky Harbor
    "san_antonio": "KSAT",   # San Antonio Intl
    "las_vegas": "KLAS",     # Harry Reid Intl
}

# City timezone mapping for METAR time-of-day guard
CITY_TIMEZONES = {
    "nyc": "America/New_York",
    "chicago": "America/Chicago",
    "miami": "America/New_York",
    "austin": "America/Chicago",
    "los_angeles": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "houston": "America/Chicago",
    "san_francisco": "America/Los_Angeles",
    "atlanta": "America/New_York",
    "washington_dc": "America/New_York",
    "boston": "America/New_York",
    "phoenix": "America/Phoenix",
    "san_antonio": "America/Chicago",
    "las_vegas": "America/Los_Angeles",
}

ANALYTICS_ENABLED = True
TELEGRAM_DAILY_SCORECARD = True

# --- ERCOT Power Price Signal ---
ERCOT_API_KEY = os.getenv("ERCOT_API_KEY", "")
ERCOT_USERNAME = os.getenv("ERCOT_USERNAME", "")
ERCOT_PASSWORD = os.getenv("ERCOT_PASSWORD", "")
ERCOT_HUBS = {
    "North":     {"city": "Dallas",      "lat": 32.78,  "lon": -96.80,  "hub_name": "HB_NORTH",   "solar_sensitivity": 0.15},
    "Houston":   {"city": "Houston",     "lat": 29.76,  "lon": -95.37,  "hub_name": "HB_HOUSTON", "solar_sensitivity": 0.10},
    "South":     {"city": "San Antonio", "lat": 29.42,  "lon": -98.49,  "hub_name": "HB_SOUTH",   "solar_sensitivity": 0.20},
    "West":      {"city": "Midland",     "lat": 31.99,  "lon": -102.08, "hub_name": "HB_WEST",    "solar_sensitivity": 0.35},
    "Panhandle": {"city": "Amarillo",    "lat": 35.22,  "lon": -101.83, "hub_name": "HB_PAN",     "solar_sensitivity": 0.25},
}

ERCOT_PAPER_BANKROLL = 10_000.0
ERCOT_PAPER_MODE = True
ERCOT_MIN_EDGE = 0.03  # Reference only — live gate is pipeline/config.py edge_gate
ERCOT_MIN_CONFIDENCE = 50
ERCOT_FORTIFY_EDGE_INCREASE = 0.03
ERCOT_EXIT_EDGE_DECAY = 0.30
ERCOT_MAX_POSITIONS_PER_HUB = 8
ERCOT_MAX_POSITIONS_TOTAL = 10
ERCOT_POSITION_TTL_HOURS = 24
ERCOT_SOLAR_HOURS = range(11, 19)  # HE11-HE18 (10am-6pm CT)
ERCOT_LOGISTIC_K = 0.35            # Logistic sharpness for P(RT >= DAM); tune from paper data

ERCOT_LOAD_SENSITIVITY = 0.15

ERCOT_SEASONAL_NORMS = {
    1:  {"solar": 10.0, "load": 50_000},
    2:  {"solar": 12.0, "load": 47_000},
    3:  {"solar": 16.0, "load": 45_000},
    4:  {"solar": 20.0, "load": 43_000},
    5:  {"solar": 22.0, "load": 48_000},
    6:  {"solar": 25.0, "load": 60_000},
    7:  {"solar": 26.0, "load": 70_000},
    8:  {"solar": 25.0, "load": 68_000},
    9:  {"solar": 20.0, "load": 55_000},
    10: {"solar": 16.0, "load": 45_000},
    11: {"solar": 12.0, "load": 43_000},
    12: {"solar": 10.0, "load": 48_000},
}