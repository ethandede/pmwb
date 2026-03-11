from dotenv import load_dotenv
load_dotenv()

CITIES = {
    "chicago": {"lat": 41.9742, "lon": -87.9073, "keywords": ["chicago", "o'hare"]},
    "nyc": {"lat": 40.7931, "lon": -73.8720, "keywords": ["nyc", "new york", "laguardia"]},
    "london": {"lat": 51.5053, "lon": 0.0553, "keywords": ["london"]},
    "miami": {"lat": 25.7617, "lon": -80.1918, "keywords": ["miami"]},
    "seattle": {"lat": 47.4502, "lon": -122.3088, "keywords": ["seattle"]},
}

EDGE_THRESHOLD = 0.105      # 10.5% edge -> strong signal
DUTCH_BOOK_THRESHOLD = 0.975
GAMMA_BASE = "https://gamma-api.polymarket.com"
SCAN_LIMIT = 200
