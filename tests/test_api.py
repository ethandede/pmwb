# tests/test_api.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from dashboard.api import app

client = TestClient(app)


def test_root_serves_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_portfolio_endpoint():
    resp = client.get("/api/portfolio")
    assert resp.status_code in (200, 502)
    data = resp.json()
    assert "balance" in data
    assert "open_positions" in data
    assert isinstance(data["open_positions"], list)


def test_config_endpoint():
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert "edge_gate" in data
    assert "kelly_range" in data
    assert "max_positions_total" in data
    assert "drawdown_threshold" in data
    assert isinstance(data["kelly_range"], list)


def test_performance_endpoint():
    resp = client.get("/api/performance")
    assert resp.status_code == 200
    data = resp.json()
    assert "equity_curve" in data
    assert isinstance(data["equity_curve"], list)


def test_markets_temp_cached():
    resp = client.get("/api/markets/temp")
    assert resp.status_code == 200
    data = resp.json()
    assert "scan_time" in data
    assert "markets" in data


def test_markets_precip_cached():
    resp = client.get("/api/markets/precip")
    assert resp.status_code == 200
    data = resp.json()
    assert "scan_time" in data
    assert "markets" in data


def test_activity_endpoint():
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
