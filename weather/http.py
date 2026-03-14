"""Shared HTTP session for weather API calls with automatic 429 retry."""

import time
import requests
from requests.adapters import HTTPAdapter, Retry

_session = None


def get_session() -> requests.Session:
    """Return a shared session with automatic retry on 429/5xx."""
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=1.5,       # waits 1.5s, 3s, 4.5s, 6s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        _session.mount("https://", HTTPAdapter(max_retries=retry))
        _session.mount("http://", HTTPAdapter(max_retries=retry))
    return _session


def get(url: str, timeout: int = 15, **kwargs) -> requests.Response:
    """GET with retry. Drop-in replacement for requests.get."""
    return get_session().get(url, timeout=timeout, **kwargs)
