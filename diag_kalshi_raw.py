"""Diagnostic script: fetch raw JSON from Kalshi portfolio endpoints."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import requests
from kalshi.trader import KALSHI_BASE, _sign_request

BASE_URL = KALSHI_BASE.replace("/trade-api/v2", "")

ENDPOINTS = [
    ("GET", "/trade-api/v2/portfolio/balance", {}),
    ("GET", "/trade-api/v2/portfolio/positions", {"limit": "5"}),
    ("GET", "/trade-api/v2/portfolio/orders", {"limit": "5"}),
]


def fetch(method, path, params):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{qs}"
        url = f"{url}?{qs}"
    else:
        full_path = path
    headers = _sign_request(method, full_path)
    resp = requests.request(method, url, headers=headers, timeout=15)
    return resp.status_code, resp.text


def main():
    for method, path, params in ENDPOINTS:
        label = f"{method} {path}"
        if params:
            label += f" ({params})"
        print(f"\n{'='*80}")
        print(f"  {label}")
        print(f"{'='*80}")
        status, body = fetch(method, path, params)
        print(f"Status: {status}")
        try:
            parsed = json.loads(body)
            print(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            print(f"Raw response (not JSON):\n{body}")


if __name__ == "__main__":
    main()
