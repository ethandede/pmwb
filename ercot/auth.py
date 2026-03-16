"""ERCOT API token manager — auto-refreshes OAuth token every 50 minutes.

ERCOT uses Azure AD B2C ROPC flow. Tokens expire after 1 hour.
We refresh at 50 minutes to avoid edge-case expiry during requests.
"""

import time
import requests
from config import ERCOT_API_KEY, ERCOT_USERNAME, ERCOT_PASSWORD

TOKEN_URL = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com"
    "/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)
CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"
TOKEN_LIFETIME = 50 * 60  # refresh 10 min before expiry

_token: str | None = None
_token_time: float = 0.0


def _fetch_token() -> str | None:
    """Request a new ID token from ERCOT's OAuth endpoint."""
    if not ERCOT_USERNAME or not ERCOT_PASSWORD:
        return None
    try:
        r = requests.post(TOKEN_URL, data={
            "username": ERCOT_USERNAME,
            "password": ERCOT_PASSWORD,
            "grant_type": "password",
            "scope": f"openid {CLIENT_ID} offline_access",
            "client_id": CLIENT_ID,
            "response_type": "id_token",
        }, timeout=15)
        r.raise_for_status()
        return r.json().get("id_token")
    except Exception as e:
        print(f"  ERCOT token error: {e}")
        return None


def get_ercot_headers() -> dict:
    """Return headers with valid Bearer token + subscription key.

    Auto-refreshes the token if expired or missing.
    """
    global _token, _token_time

    if not _token or (time.time() - _token_time) > TOKEN_LIFETIME:
        new_token = _fetch_token()
        if new_token:
            _token = new_token
            _token_time = time.time()
            print("  ERCOT token refreshed")

    headers = {}
    if ERCOT_API_KEY:
        headers["Ocp-Apim-Subscription-Key"] = ERCOT_API_KEY
    if _token:
        headers["Authorization"] = f"Bearer {_token}"
    return headers
