from dotenv import load_dotenv
load_dotenv()

CITIES = {
    "nyc": {"lat": 40.7931, "lon": -73.8720, "keywords": ["nyc", "new york", "laguardia"], "unit": "f"},
    "chicago": {"lat": 41.9742, "lon": -87.9073, "keywords": ["chicago", "o'hare"], "unit": "f"},
    "seattle": {"lat": 47.4502, "lon": -122.3088, "keywords": ["seattle"], "unit": "f"},
    "miami": {"lat": 25.7617, "lon": -80.1918, "keywords": ["miami"], "unit": "f"},
    "dallas": {"lat": 32.8968, "lon": -97.0380, "keywords": ["dallas"], "unit": "f"},
    "atlanta": {"lat": 33.6407, "lon": -84.4277, "keywords": ["atlanta"], "unit": "f"},
    "london": {"lat": 51.5053, "lon": 0.0553, "keywords": ["london"], "unit": "c"},
    "paris": {"lat": 48.8566, "lon": 2.3522, "keywords": ["paris"], "unit": "c"},
    "seoul": {"lat": 37.4633, "lon": 126.4400, "keywords": ["seoul", "incheon"], "unit": "c"},
    "tokyo": {"lat": 35.6762, "lon": 139.6503, "keywords": ["tokyo"], "unit": "c"},
}

EDGE_THRESHOLD = 0.105
DUTCH_BOOK_THRESHOLD = 0.975
GAMMA_BASE = "https://gamma-api.polymarket.com"
SCAN_LIMIT = 300
