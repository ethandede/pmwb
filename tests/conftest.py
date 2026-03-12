import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def clear_forecast_cache():
    """Clear the in-memory forecast cache before each test to prevent cross-test contamination."""
    from weather import cache as fcache
    fcache.clear()
    yield
    fcache.clear()
