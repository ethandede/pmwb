"""Shared HTTP session for weather API calls with automatic retry."""

import requests
from requests.adapters import HTTPAdapter, Retry

_session = None


def get_session() -> requests.Session:
    """Return a shared session with retry on 5xx only.

    429 (rate limit) is NOT retried — we fail fast and let the forecast
    cache handle it on the next cycle. Retrying 429s across 14 cities
    was adding 20+ minutes of dead wait time per scan.
    """
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.5,       # waits 0.5s, 1s
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        _session.mount("https://", HTTPAdapter(max_retries=retry))
        _session.mount("http://", HTTPAdapter(max_retries=retry))
    return _session


def get(url: str, timeout: int = 15, **kwargs) -> requests.Response:
    """GET with retry. Drop-in replacement for requests.get."""
    return get_session().get(url, timeout=timeout, **kwargs)
