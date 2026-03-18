# ERCOT Binary Options Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retarget the ERCOT module to score P(RT >= DAM) per hub per hour for Electron Exchange binary options, with hourly auto-settlement against ERCOT public data.

**Architecture:** Retrofit existing ERCOT infrastructure (auth, hubs, paper_trader, dashboard, pipeline). Replace the signal model internals and data fetchers while keeping the pipeline, dashboard, and risk management plumbing intact. No new files — all changes fit within existing modules.

**Tech Stack:** Python 3.12, SQLite, ERCOT public API (OAuth), Visual Crossing + Open-Meteo solar forecasts, existing pipeline framework.

**Spec:** `docs/superpowers/specs/2026-03-18-ercot-binary-options-design.md`

**Key conventions:**
- Hour Ending (HE): ERCOT standard. HE14 = 1:00-2:00 PM CT.
- P&L for YES side: `(settlement_value / 100.0 - entry_price) * size_dollars`
- P&L for NO side: `((100 - settlement_value) / 100.0 - entry_price) * size_dollars` (NO contract pays `100 - outcome`)
- `entry_price` is always a fraction (0.0-1.0). Initially 0.50 (assumed fair value, no Electron feed).
- Settlement expiry: compare `contract_date + contract_hour` against current CT datetime. A position for 2026-03-18 HE14 expires at 2:00 PM CT on 2026-03-18.
- DAM fetch failure: return None, skip all contracts for that date. Never use default prices.
- Per-hub position limit: increase `ERCOT_MAX_POSITIONS_PER_HUB` from 3 to 8 (one per solar hour).

---

### Task 1: Config — Add Binary Options Constants

**Files:**
- Modify: `config.py:130` (after `ERCOT_POSITION_TTL_HOURS`)

- [ ] **Step 1: Add ERCOT_SOLAR_HOURS, ERCOT_LOGISTIC_K, and update per-hub limit**

```python
# After ERCOT_POSITION_TTL_HOURS = 24
ERCOT_SOLAR_HOURS = range(11, 19)  # HE11-HE18 (10am-6pm CT)
ERCOT_LOGISTIC_K = 0.35            # Logistic sharpness for P(RT >= DAM); tune from paper data
```

Also change `ERCOT_MAX_POSITIONS_PER_HUB = 3` to `ERCOT_MAX_POSITIONS_PER_HUB = 8` (one per solar hour).

- [ ] **Step 2: Commit**

```bash
git add config.py
git commit -m "feat(ercot): add binary options config constants, increase per-hub limit to 8"
```

---

### Task 2: Paper Trader — New Binary Schema

**Files:**
- Modify: `ercot/paper_trader.py`
- Test: `tests/test_ercot_paper_trader.py`

- [ ] **Step 1: Write failing tests for new schema and binary settlement**

In `tests/test_ercot_paper_trader.py`, replace the existing tests with:

```python
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
        assert pos2 is None  # blocked by dedup

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
        # Use yesterday's date so it's always expired
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        # RT=45 >= DAM=40 → YES wins → settlement=100
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 45.0)
        assert len(settled) == 1
        assert settled[0]["settlement_value"] == 100
        # P&L: (100/100 - 0.50) * size > 0
        assert settled[0]["pnl"] > 0

    def test_settle_rt_below_dam_yes_loses(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        # RT=35 < DAM=40 → YES loses → settlement=0
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 35.0)
        assert len(settled) == 1
        assert settled[0]["settlement_value"] == 0
        # P&L: (0/100 - 0.50) * size < 0
        assert settled[0]["pnl"] < 0

    def test_settle_rt_below_dam_no_wins(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2026-03-17", "contract_hour": 14,
            "side": "no", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.36, "edge": -0.14, "confidence": 70,
        }, bankroll=10000)
        # RT=35 < DAM=40 → settlement=0 → NO wins (pays 100-0=100)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 35.0)
        assert len(settled) == 1
        assert settled[0]["settlement_value"] == 0
        # NO P&L: ((100-0)/100 - 0.50) * size > 0
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
        assert len(positions) == 1  # still open

    def test_does_not_settle_future_positions(self, temp_db):
        from ercot.paper_trader import open_position, settle_expired_hours, get_open_positions
        # Use a far-future date
        open_position({
            "hub": "West", "hub_name": "HB_WEST",
            "contract_date": "2099-12-31", "contract_hour": 14,
            "side": "yes", "dam_price": 40.0, "entry_price": 0.50,
            "model_prob": 0.64, "edge": 0.14, "confidence": 70,
        }, bankroll=10000)
        settled = settle_expired_hours(fetch_rt_fn=lambda hub, hour, date: 45.0)
        assert len(settled) == 0
        assert len(get_open_positions()) == 1  # still open
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ercot_paper_trader.py -v
```

Expected: FAIL — new functions and schema don't exist yet.

- [ ] **Step 3: Rewrite paper_trader.py with binary schema**

Rewrite `ercot/paper_trader.py`:

- Back up existing DB before dropping tables: `shutil.copy2(ERCOT_PAPER_DB, ERCOT_PAPER_DB + ".bak")`
- Drop and recreate all three tables with new schema from spec
- Rewrite `open_position(hub_signal: dict, bankroll: float)`:
  - Reads all fields from `hub_signal` dict: `hub`, `hub_name`, `contract_date`, `contract_hour`, `side`, `dam_price`, `entry_price`, `model_prob`, `edge`, `confidence`
  - Dedup: `SELECT COUNT(*) FROM ercot_positions WHERE hub = ? AND contract_date = ? AND contract_hour = ?`
  - Per-hub limit check (now 8), total limit check (still 10)
  - Kelly sizing uses `abs(edge)` and `ERCOT_PAPER_BANKROLL`
  - Insert with all binary fields
- Add `settle_expired_hours(fetch_rt_fn: callable) -> list[dict]`:
  - Get current CT time: `now_ct = datetime.now(ZoneInfo("America/Chicago"))`
  - Query: `SELECT * FROM ercot_positions` — for each, parse `contract_date` + `contract_hour` into a CT datetime. Settle if `now_ct >= expiry_ct`.
  - For each expired position: call `fetch_rt_fn(hub_name, contract_hour, contract_date)`
  - If RT is None: skip (retry next cycle)
  - If RT is not None: `settlement_value = 100 if rt_price >= dam_price else 0`
  - YES P&L: `(settlement_value / 100.0 - entry_price) * size_dollars`
  - NO P&L: `((100 - settlement_value) / 100.0 - entry_price) * size_dollars`
  - Insert into `ercot_trades`, delete from `ercot_positions`
  - Return list of settled trade dicts
- Update `write_scan_cache()` for new schema (contract_date, contract_hour, side, dam_price, model_prob)
- Keep `get_open_positions()`, `get_trade_history()`, `get_paper_summary()`, `get_cached_signals()` — update queries
- Remove `close_position()` and `expire_positions()`

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ercot_paper_trader.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ercot/paper_trader.py tests/test_ercot_paper_trader.py
git commit -m "feat(ercot): binary options paper trader with hourly settlement"
```

---

### Task 3: DAM + RT Data Fetchers in hubs.py

**Files:**
- Modify: `ercot/hubs.py`
- Test: `tests/test_ercot_hubs.py`

- [ ] **Step 1: Write failing tests for DAM fetch, RT fetch, and per-hour market discovery**

In `tests/test_ercot_hubs.py`, add/replace tests:

```python
from unittest.mock import patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

class TestFetchDamPrices:
    def test_returns_hourly_prices_all_hubs(self):
        """DAM fetch returns all hubs' prices from a single API call, cached per date."""
        from ercot.hubs import fetch_dam_prices
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [
            ["2026-03-18", 11, "HB_WEST", "Hub", 42.50],
            ["2026-03-18", 12, "HB_WEST", "Hub", 38.00],
            ["2026-03-18", 14, "HB_WEST", "Hub", 45.00],
            ["2026-03-18", 11, "HB_NORTH", "Hub", 40.00],
            ["2026-03-18", 12, "HB_NORTH", "Hub", 36.00],
        ]}
        with patch("ercot.hubs.requests.get", return_value=mock_response):
            result = fetch_dam_prices("2026-03-18")
        assert result is not None
        assert result["HB_WEST"][11] == 42.50
        assert result["HB_WEST"][14] == 45.00
        assert result["HB_NORTH"][11] == 40.00

    def test_returns_none_on_failure(self):
        from ercot.hubs import fetch_dam_prices
        with patch("ercot.hubs.requests.get", side_effect=Exception("timeout")):
            result = fetch_dam_prices("2026-03-18")
        assert result is None

    def test_caches_per_date(self):
        from ercot.hubs import fetch_dam_prices
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [
            ["2026-03-18", 11, "HB_WEST", "Hub", 42.50],
        ]}
        with patch("ercot.hubs.requests.get", return_value=mock_response) as mock_get:
            fetch_dam_prices("2026-03-18")
            fetch_dam_prices("2026-03-18")  # should hit cache
            assert mock_get.call_count == 1


class TestFetchRtSettlement:
    def test_averages_four_intervals(self):
        from ercot.hubs import fetch_rt_settlement
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [
            ["2026-03-18", 14, 1, "HB_WEST", "Hub", 40.0, "N"],
            ["2026-03-18", 14, 2, "HB_WEST", "Hub", 42.0, "N"],
            ["2026-03-18", 14, 3, "HB_WEST", "Hub", 38.0, "N"],
            ["2026-03-18", 14, 4, "HB_WEST", "Hub", 44.0, "N"],
        ]}
        with patch("ercot.hubs.requests.get", return_value=mock_response):
            result = fetch_rt_settlement("HB_WEST", 14, "2026-03-18")
        assert result == 41.0  # avg(40, 42, 38, 44)

    def test_returns_none_on_failure(self):
        from ercot.hubs import fetch_rt_settlement
        with patch("ercot.hubs.requests.get", side_effect=Exception("timeout")):
            result = fetch_rt_settlement("HB_WEST", 14, "2026-03-18")
        assert result is None


class TestFetchErcotMarketsHourly:
    def test_returns_per_hour_contracts(self):
        from ercot.hubs import fetch_ercot_markets
        dam_all = {
            "HB_WEST": {11: 42.0, 12: 38.0, 14: 45.0},
            "HB_NORTH": {11: 40.0, 12: 36.0},
            "HB_HOUSTON": {11: 39.0},
            "HB_SOUTH": {11: 41.0},
            "HB_PAN": {11: 37.0},
        }
        # Pin time to before HE11 so nothing is skipped
        mock_now = datetime(2026, 3, 18, 5, 0, tzinfo=CT)
        with patch("ercot.hubs.fetch_dam_prices", return_value=dam_all), \
             patch("ercot.hubs._fetch_ercot_market_data", return_value={
                 "price": 40.0, "solar_mw": 12000, "load_forecast": 45000,
                 "hub_prices": {"HB_WEST": 40.0, "HB_NORTH": 38.0,
                                "HB_HOUSTON": 39.0, "HB_SOUTH": 41.0, "HB_PAN": 37.0},
             }), \
             patch("ercot.hubs._get_ct_now", return_value=mock_now):
            markets = fetch_ercot_markets()
        # 3 + 2 + 1 + 1 + 1 = 8 contracts across hubs
        assert len(markets) == 8
        west_markets = [m for m in markets if m["hub_name"] == "HB_WEST"]
        assert len(west_markets) == 3
        assert west_markets[0]["ticker"].startswith("BOPT-ERCOT-HB_WEST")
        assert "dam_price" in west_markets[0]
        assert "contract_hour" in west_markets[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ercot_hubs.py -v -k "Dam or Rt or Hourly"
```

- [ ] **Step 3: Implement DAM fetch, RT fetch, and refactor fetch_ercot_markets()**

In `ercot/hubs.py`:

- Add `_get_ct_now() -> datetime` helper (returns `datetime.now(ZoneInfo("America/Chicago"))`) — extracted for testability.
- Add `fetch_dam_prices(date: str) -> dict[str, dict[int, float]] | None`:
  - Single API call fetches ALL hubs' DAM prices for the given date
  - Report ID: `np4-190-cd` (fallback `np4-183-cd`)
  - Filter to `ERCOT_SOLAR_HOURS` and hub names starting with `HB_`
  - Returns `{hub_name: {hour_ending: price}}` for all hubs at once
  - Cache per date string (module-level dict, no TTL — DAM prices don't change)
  - Returns None on any failure
- Add `fetch_rt_settlement(hub_name: str, hour: int, date: str) -> float | None`:
  - Uses existing `spp_node_zone_hub` endpoint filtered by hub/hour/date
  - Averages the 4 fifteen-minute intervals
  - Returns None on failure
- Refactor `fetch_ercot_markets()`:
  1. Call `_fetch_ercot_market_data()` for load/solar (existing)
  2. Call `fetch_dam_prices()` for today and tomorrow
  3. If DAM is None, return empty list (no contracts)
  4. For each hub × hour with DAM data, skip hours in the past (`_get_ct_now().hour >= contract_hour`)
  5. Emit a market dict per the spec schema
  6. `ticker = f"BOPT-ERCOT-{hub_name}-{date_str.replace('-','')[2:]}-{hour:02d}"`

Note: the ERCOT DAM SPP report field layout may differ from the mock. Implementer should verify field positions against actual API response (handle both list-style and dict-style records like the existing SPP parser).

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ercot_hubs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ercot/hubs.py tests/test_ercot_hubs.py
git commit -m "feat(ercot): DAM/RT price fetchers and per-hour market discovery"
```

---

### Task 4: Signal Model — P(RT >= DAM) with Hourly Solar Curve

**Files:**
- Modify: `weather/multi_model.py:556-686`
- Test: `tests/test_ercot_signal.py`

- [ ] **Step 1: Write failing tests for new signal model**

In `tests/test_ercot_signal.py`, add/replace:

```python
import math
from unittest.mock import patch, MagicMock

class TestHourlySolarCurve:
    def test_peak_at_midday(self):
        from weather.multi_model import _hourly_solar_curve
        he13 = _hourly_solar_curve(daily_solar=20.0, hour_ending=13, month=3)
        he11 = _hourly_solar_curve(daily_solar=20.0, hour_ending=11, month=3)
        he18 = _hourly_solar_curve(daily_solar=20.0, hour_ending=18, month=3)
        assert he13 > he11
        assert he13 > he18

    def test_sums_to_daily_total(self):
        from weather.multi_model import _hourly_solar_curve
        daily = 20.0
        total = sum(_hourly_solar_curve(daily, he, 3) for he in range(11, 19))
        assert abs(total - daily) < 0.01

    def test_all_non_negative(self):
        from weather.multi_model import _hourly_solar_curve
        for he in range(11, 19):
            assert _hourly_solar_curve(20.0, he, 3) >= 0


class TestProbRtGteDam:
    @patch("weather.multi_model.http_get")
    def test_solar_deficit_gives_high_prob(self, mock_get):
        """Less solar than normal → RT likely > DAM → P > 0.50"""
        from weather.multi_model import get_ercot_solar_signal
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"days": [{"solarenergy": 8.0}]}
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
            contract_hour=14, dam_price=40.0,
            ercot_data={"hub_price": 40.0, "load_forecast": 45000, "solar_mw": 12000},
        )
        assert result["model_prob"] > 0.50
        assert result["signal"] == "LONG"

    @patch("weather.multi_model.http_get")
    def test_solar_surplus_gives_low_prob(self, mock_get):
        """More solar than normal → RT likely < DAM → P < 0.50"""
        from weather.multi_model import get_ercot_solar_signal
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"days": [{"solarenergy": 24.0}]}
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
            contract_hour=14, dam_price=40.0,
            ercot_data={"hub_price": 40.0, "load_forecast": 45000, "solar_mw": 12000},
        )
        assert result["model_prob"] < 0.50
        assert result["signal"] == "SHORT"

    @patch("weather.multi_model.http_get")
    def test_normal_solar_gives_fifty_percent(self, mock_get):
        """Solar at norm → P ≈ 0.50"""
        from weather.multi_model import get_ercot_solar_signal
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"days": [{"solarenergy": 16.0}]}
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
            contract_hour=14, dam_price=40.0,
            ercot_data={"hub_price": 40.0, "load_forecast": 45000, "solar_mw": 12000},
        )
        assert 0.48 <= result["model_prob"] <= 0.52

    @patch("weather.multi_model.http_get")
    def test_backward_compat_no_contract_hour(self, mock_get):
        """Without contract_hour, falls back to daily signal (old behavior)."""
        from weather.multi_model import get_ercot_solar_signal
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"days": [{"solarenergy": 10.0}]}
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        result = get_ercot_solar_signal(
            32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
            ercot_data={"hub_price": 40.0, "load_forecast": 45000, "solar_mw": 12000},
        )
        assert "signal" in result
        assert "edge" in result
        assert result["signal"] in ("LONG", "SHORT", "NEUTRAL")

    @patch("weather.multi_model.http_get")
    def test_zero_norm_solar_no_crash(self, mock_get):
        """If norm_solar is 0 (edge case), should not crash."""
        from weather.multi_model import get_ercot_solar_signal
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"days": [{"solarenergy": 10.0}]}
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        with patch("config.ERCOT_SEASONAL_NORMS", {3: {"solar": 0.0, "load": 45000}}):
            result = get_ercot_solar_signal(
                32.78, -96.80, hub_key="North", solar_sensitivity=0.15,
                contract_hour=14, dam_price=40.0,
                ercot_data={"hub_price": 40.0, "load_forecast": 45000, "solar_mw": 12000},
            )
        assert 0.0 < result["model_prob"] < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ercot_signal.py -v -k "Hourly or Prob or backward or zero_norm"
```

- [ ] **Step 3: Refactor get_ercot_solar_signal()**

In `weather/multi_model.py`:

- Add `_hourly_solar_curve(daily_solar: float, hour_ending: int, month: int) -> float`:
  - `weight = max(0, cos((he - 13.5) * pi / 10))`
  - `sum_weights = sum(max(0, cos((h - 13.5) * pi / 10)) for h in ERCOT_SOLAR_HOURS)`
  - Return `daily_solar * weight / sum_weights` (or 0 if sum_weights == 0)
- Modify `get_ercot_solar_signal()` — add parameters `contract_hour: int = 0, dam_price: float = 0.0`
- Keep existing solar fetch (VC primary, OM cross-ref, norm fallback). Add `expected_solrad = max(0, ...)` guard.
- When `contract_hour > 0 and dam_price > 0` (binary mode):
  - Use **daily-level** solar_deviation: `(norm_solar - expected_solrad) / norm_solar` (guard: if `norm_solar <= 0`, `solar_deviation = 0`)
  - `estimated_rt_shift = solar_deviation * solar_sensitivity * dam_price`
  - `model_prob = 1 / (1 + exp(-ERCOT_LOGISTIC_K * estimated_rt_shift))`
  - `edge = round(model_prob - 0.50, 4)`
  - `signal = "LONG" if edge > 0 else "SHORT" if edge < 0 else "NEUTRAL"`
  - Add `model_prob` to returned dict
- When `contract_hour == 0` (legacy daily mode): keep existing logic unchanged
- `_hourly_solar_curve` is used only for populating `expected_solrad_mjm2` per-hour in scan cache (not for deviation — daily is equivalent and simpler)

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ercot_signal.py -v
```

- [ ] **Step 5: Commit**

```bash
git add weather/multi_model.py tests/test_ercot_signal.py
git commit -m "feat(ercot): P(RT >= DAM) signal model with hourly solar curve"
```

---

### Task 5: Pipeline Integration — Scoring and Execution

**Files:**
- Modify: `pipeline/stages.py:37-50` (ERCOT scoring branch)
- Modify: `pipeline/stages.py:396-418` (ERCOT execute branch)
- Modify: `pipeline/config.py:169-192` (ERCOT config)
- Test: `tests/test_ercot_pipeline_signal.py`

- [ ] **Step 1: Write failing test for hourly pipeline scoring**

```python
from unittest.mock import patch, MagicMock
from pipeline.types import Signal

class TestErcotHourlyPipeline:
    def test_score_signal_passes_contract_hour_and_dam(self):
        from pipeline.stages import score_signal
        from pipeline.config import ERCOT
        market = {
            "ticker": "BOPT-ERCOT-HB_WEST-260318-14",
            "hub_key": "West", "hub_name": "HB_WEST",
            "city": "Midland", "lat": 31.99, "lon": -102.08,
            "solar_sensitivity": 0.35,
            "contract_date": "2026-03-18", "contract_hour": 14,
            "dam_price": 42.50,
            "_ercot_data": {"hub_price": 42.50, "load_forecast": 45000, "solar_mw": 12000},
        }
        with patch("config.VISUAL_CROSSING_API_KEY", "test-key"):
            with patch("weather.multi_model.http_get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"days": [{"solarenergy": 10.0}]}
                mock_resp.raise_for_status = lambda: None
                mock_get.return_value = mock_resp
                signal = score_signal(ERCOT, market)
        assert isinstance(signal, Signal)
        assert 0.0 < signal.model_prob < 1.0
        assert signal.side in ("yes", "no")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_ercot_pipeline_signal.py -v -k "hourly"
```

- [ ] **Step 3: Update pipeline scoring, execution, and config**

In `pipeline/stages.py`, update the ERCOT scoring branch (lines 37-50):
- Pass `contract_hour=market.get("contract_hour", 0)` and `dam_price=market.get("dam_price", 0)` to `config.forecast_fn()`
- When `forecast_result` contains `model_prob` key, use it directly instead of computing `0.5 + edge`
- Side: `"yes" if model_prob > 0.5 else "no"`

In `pipeline/stages.py`, update the ERCOT execute branch to build the new `hub_signal` dict:

```python
elif config.exchange == "ercot":
    from ercot.paper_trader import open_position
    from config import ERCOT_PAPER_BANKROLL
    hub_signal = {
        "hub": signal.market.get("hub_key", signal.city),
        "hub_name": signal.market.get("hub_name", signal.ticker),
        "contract_date": signal.market.get("contract_date", ""),
        "contract_hour": signal.market.get("contract_hour", 0),
        "side": size.side or signal.side,
        "dam_price": signal.market.get("dam_price", 0),
        "entry_price": 0.50,  # assumed fair value until Electron API
        "model_prob": signal.model_prob,
        "edge": abs(signal.edge),
        "confidence": int(signal.confidence),
    }
    pos = open_position(hub_signal, bankroll=ERCOT_PAPER_BANKROLL)
    if pos is None:
        return TradeResult(
            ticker=signal.ticker, side=signal.side,
            count=0, price_cents=price_cents, cost=0,
            order_id="", status="ercot_blocked", paper=True,
        )
```

In `pipeline/config.py`, update ERCOT config:
- `settlement_timeline="hourly_binary"`
- `exit_rules={}` (empty — binary contracts auto-settle, no active management)
- Point `settle_fn` to `run_ercot_manager` (which now delegates to `settle_expired_hours`)

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages.py pipeline/config.py tests/test_ercot_pipeline_signal.py
git commit -m "feat(ercot): wire hourly binary scoring and execution into pipeline"
```

---

### Task 6: Position Manager — Simplify to Settlement Only

**Files:**
- Modify: `ercot/position_manager.py`
- Modify: `pipeline/runner.py` (update expiry call)
- Test: `tests/test_ercot_position_manager.py`

- [ ] **Step 1: Write test for simplified manager**

```python
from unittest.mock import patch

class TestBinaryManager:
    def test_run_ercot_manager_calls_settle(self):
        from ercot.position_manager import run_ercot_manager
        with patch("ercot.position_manager.settle_expired_hours") as mock_settle, \
             patch("ercot.position_manager.fetch_rt_settlement"):
            mock_settle.return_value = []
            run_ercot_manager()
            mock_settle.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_ercot_position_manager.py -v -k "Binary"
```

- [ ] **Step 3: Simplify position_manager.py and update runner.py**

Rewrite `ercot/position_manager.py`:

```python
"""ERCOT position manager — binary contract settlement.

Binary contracts auto-settle at hour end. No exit/fortify logic needed.
"""
from ercot.paper_trader import settle_expired_hours
from ercot.hubs import fetch_rt_settlement


def run_ercot_manager():
    """Settle any expired binary option positions."""
    settled = settle_expired_hours(fetch_rt_fn=fetch_rt_settlement)
    if settled:
        print(f"  ERCOT settled {len(settled)} positions")
    return settled
```

Update `pipeline/runner.py`:
- Replace `expire_positions(current_price=0)` with:

```python
try:
    from ercot.paper_trader import settle_expired_hours
    from ercot.hubs import fetch_rt_settlement
    settle_expired_hours(fetch_rt_fn=fetch_rt_settlement)
except Exception as e:
    print(f"  ERCOT settlement check failed: {e}")
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ercot/position_manager.py pipeline/runner.py tests/test_ercot_position_manager.py
git commit -m "feat(ercot): simplify position manager to binary settlement only"
```

---

### Task 7: Runner Scan Cache Update

**Files:**
- Modify: `pipeline/runner.py` (ERCOT scan cache write block)

- [ ] **Step 1: Update ERCOT scan cache write in runner.py**

The ERCOT scan cache block in `_write_scan_cache` needs the new binary fields. Update the `ercot_signals` list comprehension:

```python
ercot_signals.append({
    "hub": s.city or s.ticker,
    "hub_name": s.market.get("hub_name", s.ticker) if s.market else s.ticker,
    "signal": "SHORT" if s.side == "no" else "LONG",
    "edge": abs(s.edge),
    "contract_date": s.market.get("contract_date", "") if s.market else "",
    "contract_hour": s.market.get("contract_hour", 0) if s.market else 0,
    "side": s.side,
    "dam_price": s.market.get("dam_price", 0) if s.market else 0,
    "model_prob": s.model_prob,
    "expected_solrad_mjm2": s.market.get("expected_solrad_mjm2", 0) if s.market else 0,
    "confidence": int(s.confidence),
})
```

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -x -q
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/runner.py
git commit -m "feat(ercot): pass binary fields to scan cache write"
```

---

### Task 8: Deploy and Verify

**Files:** None (operational)

- [ ] **Step 1: Run full test suite one final time**

```bash
pytest tests/ -x -q
```

Expected: All pass.

- [ ] **Step 2: Push and deploy**

```bash
git push origin master
cd ~/Projects/polymarket-weather-bot && bash deploy.sh
```

- [ ] **Step 3: Verify on server**

Wait 5-10 minutes for one scan cycle, then check:

```bash
ssh hetzner 'journalctl -u weather-daemon --since "10 min ago" --no-pager | grep -i ercot'
```

Expected: "ERCOT Solar: X scored, Y filtered, Z traded" with hourly contracts (X should be ~40 for 5 hubs × 8 hours).

```bash
ssh hetzner 'cd /home/edede/polymarket-weather-bot && .venv/bin/python -c "
from ercot.paper_trader import get_open_positions, get_cached_signals
positions = get_open_positions()
print(f\"Open positions: {len(positions)}\")
for p in positions[:3]:
    print(f\"  {p[\"hub_name\"]} HE{p[\"contract_hour\"]} {p[\"side\"]} DAM={p[\"dam_price\"]}\")
sigs = get_cached_signals()
print(f\"Scan cache: {len(sigs)} signals\")
for s in sigs[:3]:
    print(f\"  {s[\"hub_name\"]} HE{s[\"contract_hour\"]} P={s[\"model_prob\"]:.2f} edge={s[\"edge\"]:.3f}\")
"'
```

Expected: Positions with `contract_hour`, `dam_price`, `side` fields. Scan cache shows per-hour P(RT >= DAM) signals.

- [ ] **Step 4: Commit deploy confirmation**

```bash
git commit --allow-empty -m "chore: ERCOT binary options deployed and verified"
```
