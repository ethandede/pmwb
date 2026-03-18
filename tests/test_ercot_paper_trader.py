import sqlite3
import pytest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    db_path = str(tmp_path / "ercot_paper.db")
    with patch("ercot.paper_trader.ERCOT_PAPER_DB", db_path):
        yield db_path


class TestBinarySchema:
    def test_init_creates_tables_with_new_columns(self, temp_db):
        from ercot.paper_trader import _init_db
        _init_db()
        conn = sqlite3.connect(temp_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ercot_positions)").fetchall()]
        assert "contract_hour" in cols
        assert "contract_date" in cols
        assert "dam_price" in cols
        assert "side" in cols
        assert "model_prob" in cols
        conn.close()


class TestBinaryOpenPosition:
    def test_open_position_with_binary_fields(self, temp_db):
        from ercot.paper_trader import open_position
        pos = open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-18", "contract_hour": 14,
            "side": "yes", "dam_price": 42.50, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        assert pos is not None
        assert pos["contract_hour"] == 14
        assert pos["dam_price"] == 42.50
        assert pos["side"] == "yes"

    def test_dedup_same_hub_date_hour(self, temp_db):
        from ercot.paper_trader import open_position
        sig = {
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-18", "contract_hour": 14,
            "side": "yes", "dam_price": 42.50, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }
        pos1 = open_position(sig, bankroll=10000)
        pos2 = open_position(sig, bankroll=10000)
        assert pos1 is not None
        assert pos2 is None

    def test_different_hours_same_hub_allowed(self, temp_db):
        from ercot.paper_trader import open_position
        base = {
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-18", "side": "yes",
            "dam_price": 42.50, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }
        pos1 = open_position({**base, "contract_hour": 14}, bankroll=10000)
        pos2 = open_position({**base, "contract_hour": 15}, bankroll=10000)
        assert pos1 is not None
        assert pos2 is not None


class TestBinarySettlement:
    def test_settle_rt_above_dam_yes_wins(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 45.0)
        assert len(settled) == 1
        assert settled[0]["settlement_value"] == 100
        assert settled[0]["pnl"] > 0

    def test_settle_rt_below_dam_yes_loses(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 35.0)
        assert len(settled) == 1
        assert settled[0]["settlement_value"] == 0
        assert settled[0]["pnl"] < 0

    def test_settle_rt_below_dam_no_wins(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "no", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.36, "edge": -0.14, "confidence": 70,
        }, bankroll=10000)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 35.0)
        assert len(settled) == 1
        assert settled[0]["settlement_value"] == 0
        assert settled[0]["pnl"] > 0

    def test_settle_skips_when_rt_unavailable(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours, get_open_positions
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: None)
        positions = get_open_positions()
        assert len(settled) == 0
        assert len(positions) == 1

    def test_does_not_settle_future_positions(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours, get_open_positions
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2099-12-31", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 45.0)
        assert len(settled) == 0
        assert len(get_open_positions()) == 1
