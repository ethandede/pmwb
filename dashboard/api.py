# dashboard/api.py
"""FastAPI backend for Weather Edge dashboard.
Serves JSON API endpoints + static frontend.
Run: uvicorn dashboard.api:app --host 0.0.0.0 --port 8501
"""
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from dashboard.scan_cache import init_scan_cache_db, get_latest_scan, get_scan_history, cleanup_old_scans
from dashboard.equity_db import init_equity_db, get_equity_curve
from dashboard.ticker_map import ticker_to_city
from dashboard.ercot_api import ercot_router
from dashboard.pjm_api import pjm_router
from dashboard.caiso_api import caiso_router

from exchanges.kalshi import KalshiExchange
_kalshi = KalshiExchange()
from config import PAPER_MODE

TRADES_DB = Path(__file__).resolve().parent.parent / "data" / "trades.db"

# ── Shared month lookup ──────────────────────────────────────────────
_MONTHS = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
           "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}


def _parse_settle_date(ticker: str) -> str:
    """Extract settlement date from ticker like KXHIGHNY-26MAR14-T56 → '2026-03-14'.

    Also handles precip monthly tickers like KXRAINMIAM-26MAR-2 → end-of-month.
    Returns '' if unparseable.
    """
    parts = ticker.split("-")
    if len(parts) < 2:
        return ""
    # Daily: 26MAR14
    m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", parts[1])
    if m:
        yr, mon_str, day = m.groups()
        mon = _MONTHS.get(mon_str, "01")
        return f"20{yr}-{mon}-{day}"
    # Monthly precip: 26MAR (no day) → end of month
    m2 = re.match(r"(\d{2})([A-Z]{3})$", parts[1])
    if m2:
        import calendar as _cal
        yr, mon_str = m2.groups()
        mon = _MONTHS.get(mon_str, "01")
        last_day = _cal.monthrange(2000 + int(yr), int(mon))[1]
        return f"20{yr}-{mon}-{last_day:02d}"
    return ""


def _parse_contract(ticker: str, side: str = "YES") -> str:
    """Extract contract display string from ticker strike.

    Handles temp (T56 → '>56°', B51.5 → '51-52°') and
    precip (numeric → '> 2 in' / '< 2 in' depending on side).
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return ""
    strike = parts[2]
    is_precip = "RAIN" in ticker.upper()
    if is_precip:
        try:
            threshold_in = float(strike)
            direction = ">" if side == "YES" else "<"
            return f"{direction} {threshold_in:.0f} in"
        except ValueError:
            return strike
    if strike.startswith("T"):
        return f">{strike[1:]}\u00b0"
    if strike.startswith("B"):
        try:
            val = float(strike[1:])
            return f"{val:.0f}-{val+2:.0f}\u00b0"
        except ValueError:
            return strike
    return ""


def _predict_likely(ticker: str, side: str, forecast_high: float | None,
                    forecast_precip: float | None) -> str | None:
    """Predict WIN/LOSS based on forecast vs contract strike."""
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    strike = parts[2]
    is_precip = "RAIN" in ticker.upper()

    if is_precip and forecast_precip is not None:
        try:
            threshold_in = float(strike)
            # Kalshi precip: YES = "above threshold", NO = "under threshold"
            above = forecast_precip >= threshold_in
            return ("WIN" if above else "LOSS") if side == "YES" else ("WIN" if not above else "LOSS")
        except ValueError:
            return None

    if not is_precip and forecast_high is not None:
        if strike.startswith("T"):
            threshold = float(strike[1:])
            # Kalshi T-type: YES = "below threshold" (e.g. T93 = "92° or below")
            temp_below = forecast_high < threshold
            return ("WIN" if temp_below else "LOSS") if side == "YES" else ("LOSS" if temp_below else "WIN")
        if strike.startswith("B"):
            threshold = float(strike[1:])
            in_bucket = threshold <= forecast_high < threshold + 2
            return ("WIN" if in_bucket else "LOSS") if side == "YES" else ("WIN" if not in_bucket else "LOSS")

    return None


def _get_cost_basis() -> dict:
    """Compute cost basis of CURRENT position per ticker from trades.db.

    Finds the most recent entry (after the last sell) for each ticker.
    This reflects what you actually paid for the contracts you're holding now,
    not the cumulative churn damage from previous round-trips.
    Returns {ticker: cost_in_dollars}.
    """
    if not TRADES_DB.exists():
        return {}
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    # Exclude sells that haven't actually filled (recorded at order time with assumed qty)
    # A real sell fill has settlement_outcome set, or was recorded before the position was re-opened
    rows = conn.execute(
        """SELECT ticker, side, fill_price, fill_qty, fill_time, settlement_outcome
           FROM trades WHERE fill_qty > 0
           ORDER BY fill_time ASC"""
    ).fetchall()
    conn.close()

    # Group by ticker, then find cost of current position
    from collections import defaultdict
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)

    basis = {}
    for ticker, fills in by_ticker.items():
        # Walk fills in order: buys accumulate, sells reset the position.
        # After the last sell, remaining buys are the current position's cost.
        buy_fills_since_last_sell = []
        for f in fills:
            if (f["side"] or "").startswith("sell"):
                buy_fills_since_last_sell = []
            else:
                buy_fills_since_last_sell.append(f)
        if not buy_fills_since_last_sell:
            continue
        # Weighted average cost across all buys in current position
        total_cost_cents = sum((f["fill_price"] or 0) * (f["fill_qty"] or 0)
                               for f in buy_fills_since_last_sell)
        if total_cost_cents > 0:
            basis[ticker] = round(total_cost_cents / 100.0, 2)

    return basis


def _get_paper_positions() -> list:
    """Get open (unsettled) paper positions from trades.db.

    Only returns actual paper trades (order_id LIKE 'paper-%'), deduplicated
    to one row per ticker (the latest fill). Live Kalshi orders are shown
    separately via the exchange API.
    """
    if not TRADES_DB.exists():
        return []
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT ticker, city, side,
                  ROUND(SUM(fill_price * fill_qty) * 1.0 / SUM(fill_qty)) as fill_price,
                  SUM(fill_qty) as fill_qty,
                  MAX(fill_time) as fill_time
           FROM trades
           WHERE settlement_outcome IS NULL
             AND order_id LIKE 'paper-%'
             AND fill_qty > 0
             AND ticker NOT LIKE 'HB_%'
           GROUP BY ticker, side
           ORDER BY fill_time DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialise DBs + prune stale scan data
init_scan_cache_db()
cleanup_old_scans()
init_equity_db()

app = FastAPI(title="Weather Edge Dashboard")
app.include_router(ercot_router)
app.include_router(pjm_router)
app.include_router(caiso_router)

# Serve static files and index.html
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"status": "Frontend not found"}


@app.get("/ercot")
async def ercot_page():
    ercot_file = STATIC_DIR / "ercot.html"
    if ercot_file.exists():
        return FileResponse(str(ercot_file))
    return {"status": "ERCOT frontend not found"}


@app.get("/power")
async def power_page():
    power_file = STATIC_DIR / "power.html"
    if power_file.exists():
        return FileResponse(str(power_file))
    return {"status": "Power frontend not found"}


@app.get("/api/portfolio")
async def get_portfolio():
    try:
        bal = _kalshi.get_balance()
        cash = bal.get("balance", 0) / 100.0
        positions_val = bal.get("portfolio_value", 0) / 100.0
        equity = cash + positions_val

        cost_basis = _get_cost_basis()

        # Read pre-computed forecasts from scan_cache.db (written by daemon)
        from kalshi.scanner import WEATHER_SERIES, PRECIP_SERIES
        from dashboard.scan_cache import get_city_forecasts
        city_fc = get_city_forecasts()

        _forecast_cache = {}
        _precip_cache = {}
        for prefix, info in WEATHER_SERIES.items():
            city_name = info["city"]
            if city_name in _forecast_cache:
                continue
            fc = city_fc.get(city_name, {})
            _forecast_cache[city_name] = {
                "high": [fc.get("forecast_high_today"), fc.get("forecast_high_tomorrow")],
                "low": [fc.get("forecast_low_today"), fc.get("forecast_low_tomorrow")],
                "current": fc.get("current_temp"),
            }
        for prefix, info in PRECIP_SERIES.items():
            city_name = info["city"]
            if city_name in _precip_cache:
                continue
            fc = city_fc.get(city_name, {})
            _precip_cache[city_name] = {
                "remaining_forecast": None,
                "mtd_observed": fc.get("mtd_precip_inches"),
                "month_total_forecast": fc.get("forecast_precip_total"),
            }

        from datetime import date as _date
        today_str = _date.today().isoformat()

        open_pos = []
        for p in _kalshi.get_positions():
            qty_fp = float(p.get("position_fp", 0))
            if qty_fp != 0:
                qty = abs(qty_fp)
                ticker = p.get("ticker", "")
                exposure = float(p.get("market_exposure_dollars", 0))
                total_traded = float(p.get("total_traded_dollars", 0) or 0)
                cost = cost_basis.get(ticker, 0)
                # If no cost basis in trades.db, use Kalshi's total_traded_dollars
                if cost == 0 and total_traded > 0:
                    cost = total_traded
                pnl = round(exposure - cost, 2) if cost != 0 else 0
                entry = round(min(0.99, max(0, cost) / qty), 2) if qty > 0 and cost > 0 else 0

                side_str = "YES" if qty_fp > 0 else "NO"
                settles = _parse_settle_date(ticker)
                contract = _parse_contract(ticker, side_str)

                # Look up forecast for this city
                city_slug = None
                is_precip = "RAIN" in ticker.upper()
                series = {**WEATHER_SERIES, **PRECIP_SERIES} if is_precip else WEATHER_SERIES
                for prefix, info in series.items():
                    if ticker.upper().startswith(prefix.upper()):
                        city_slug = info["city"]
                        break

                forecast_high = None
                forecast_low = None
                forecast_precip = None
                current_val = None

                if is_precip:
                    pc = _precip_cache.get(city_slug, {})
                    forecast_precip = pc.get("month_total_forecast")
                    current_val = pc.get("mtd_observed")
                else:
                    fc = _forecast_cache.get(city_slug, {})
                    fc_idx = 0 if settles <= today_str else 1
                    fc_high = fc.get("high", [None, None])
                    fc_low = fc.get("low", [None, None])
                    forecast_high = fc_high[fc_idx] if fc_idx < len(fc_high) else None
                    forecast_low = fc_low[fc_idx] if fc_idx < len(fc_low) else None
                    current_val = fc.get("current")

                likely = _predict_likely(ticker, side_str, forecast_high, forecast_precip)

                market_type = "precip" if is_precip else "temp"
                open_pos.append({
                    "ticker": ticker,
                    "city": ticker_to_city(ticker),
                    "market_type": market_type,
                    "side": side_str,
                    "contract": contract,
                    "qty": int(qty),
                    "entry": entry,
                    "value": round(exposure, 2),
                    "pnl": pnl,
                    "fees": round(float(p.get("fees_paid_dollars", 0)), 2),
                    "settles": settles,
                    "forecast_high": round(forecast_high, 1) if forecast_high is not None else None,
                    "forecast_low": round(forecast_low, 1) if forecast_low is not None else None,
                    "forecast_precip": round(forecast_precip, 2) if forecast_precip is not None else None,
                    "current": round(current_val, 1) if current_val is not None else None,
                    "likely": likely,
                })

        # Paper positions from trades.db (unsettled paper trades)
        paper_pos = []
        if PAPER_MODE:
            for r in _get_paper_positions():
                ticker = r["ticker"]
                side_raw = r["side"] or ""
                is_no = "no" in side_raw
                side_str = "NO" if is_no else "YES"

                is_precip = "RAIN" in ticker.upper()
                settles = _parse_settle_date(ticker)
                contract = _parse_contract(ticker, side_str)

                # Look up city from both temp and precip series
                city_slug = None
                for prefix, info in {**WEATHER_SERIES, **PRECIP_SERIES}.items():
                    if ticker.upper().startswith(prefix.upper()):
                        city_slug = info["city"]
                        break

                forecast_high = None
                forecast_precip = None

                if is_precip:
                    pc = _precip_cache.get(city_slug, {})
                    forecast_precip = pc.get("month_total_forecast")
                    current_val = pc.get("mtd_observed")
                else:
                    fc = _forecast_cache.get(city_slug, {})
                    fc_idx = 0 if settles <= today_str else 1
                    fc_high = fc.get("high", [None, None])
                    forecast_high = fc_high[fc_idx] if fc_idx < len(fc_high) else None
                    current_val = fc.get("current")

                likely = _predict_likely(ticker, side_str, forecast_high, forecast_precip)

                fill_cost = (r["fill_price"] or 0) * (r["fill_qty"] or 0) / 100.0
                market_type = "precip" if is_precip else "temp"
                paper_pos.append({
                    "ticker": ticker,
                    "city": ticker_to_city(ticker) or (r["city"] or "").replace("_", " ").title(),
                    "market_type": market_type,
                    "side": side_str,
                    "contract": contract,
                    "qty": r["fill_qty"] or 0,
                    "entry": round((r["fill_price"] or 0) / 100.0, 2),
                    "value": round(fill_cost, 2),
                    "settles": settles,
                    "forecast_high": round(forecast_high, 1) if forecast_high is not None else None,
                    "forecast_precip": round(forecast_precip, 2) if forecast_precip is not None else None,
                    "current": round(current_val, 1) if current_val is not None else None,
                    "likely": likely,
                })

        return {
            "mode": "PAPER" if PAPER_MODE else "LIVE",
            "balance": {
                "cash": round(cash, 2),
                "positions": round(positions_val, 2),
                "equity": round(equity, 2),
            },
            "live_positions": open_pos,
            "paper_positions": paper_pos,
            # Back-compat: open_positions = paper in paper mode, live otherwise
            "open_positions": paper_pos if PAPER_MODE else open_pos,
        }
    except Exception as e:
        return {"error": str(e), "balance": {"equity": 0}, "open_positions": [], "live_positions": [], "paper_positions": [], "mode": "PAPER" if PAPER_MODE else "LIVE"}


@app.get("/api/markets/{market_type}")
async def get_markets(market_type: str, force: bool = Query(False)):
    if market_type not in ("temp", "precip"):
        return {"error": "Invalid market_type"}

    # TODO: wire force rescan to pipeline — scan_cache.db is currently only
    # populated when the pipeline writes results (see pipeline/runner.py).
    # Until then, force=true returns the latest cached scan like a normal request.

    return get_latest_scan(market_type)


@app.get("/api/markets/{market_type}/history")
async def market_history(market_type: str, days: int = Query(30)):
    if market_type not in ("temp", "precip"):
        return {"error": "Invalid market_type"}
    return get_scan_history(market_type, days=days)


@app.get("/api/performance")
async def get_performance():
    return {
        "equity_curve": get_equity_curve(),
    }


@app.get("/api/config")
async def get_config():
    from config import PAPER_MODE, ALERT_THRESHOLD, CONFIDENCE_THRESHOLD, MAX_ORDER_USD, MAX_SCAN_BUDGET, FRACTIONAL_KELLY, MAX_POSITIONS_TOTAL, DRAWDOWN_THRESHOLD
    return {
        "mode": "PAPER" if PAPER_MODE else "LIVE",
        "edge_gate": ALERT_THRESHOLD,
        "confidence_gate": CONFIDENCE_THRESHOLD,
        "kelly_range": [FRACTIONAL_KELLY, 0.5],
        "max_order_usd": MAX_ORDER_USD,
        "scan_budget_usd": MAX_SCAN_BUDGET,
        "max_positions_total": MAX_POSITIONS_TOTAL,
        "drawdown_threshold": DRAWDOWN_THRESHOLD,
    }


@app.get("/api/activity")
async def get_activity(limit: int = Query(50)):
    if not TRADES_DB.exists():
        return []
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT ticker, city, side, fill_price, fill_qty, fill_time,
                  settlement_outcome, pnl, strategy, fee
           FROM trades WHERE fill_qty > 0
           ORDER BY fill_time DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()

    # Look up edge/confidence from scan cache for each ticker
    scan_db = Path(__file__).resolve().parent.parent / "data" / "scan_cache.db"
    scan_lookup = {}
    if scan_db.exists():
        sc = sqlite3.connect(str(scan_db))
        sc.row_factory = sqlite3.Row
        scan_rows = sc.execute(
            """SELECT ticker, edge, confidence FROM scan_results
               WHERE id IN (SELECT MAX(id) FROM scan_results GROUP BY ticker)"""
        ).fetchall()
        sc.close()
        for sr in scan_rows:
            scan_lookup[sr["ticker"]] = {"edge": sr["edge"], "confidence": sr["confidence"]}

    result = []
    for r in rows:
        ticker = r["ticker"]
        scan = scan_lookup.get(ticker, {})
        side_str = r["side"] or ""
        is_no = "no" in side_str
        raw_edge = scan.get("edge")
        # Scan cache stores edge from YES perspective; flip for NO trades
        edge = -raw_edge if raw_edge is not None and is_no else raw_edge
        result.append({
            "ticker": ticker,
            "city": ticker_to_city(ticker) or (r["city"] or "").replace("_", " ").title(),
            "action": "SELL" if side_str.startswith("sell") else "BUY",
            "side": "NO" if is_no else "YES",
            "price": r["fill_price"],
            "qty": r["fill_qty"],
            "time": r["fill_time"],
            "outcome": r["settlement_outcome"],
            "pnl": round(r["pnl"], 2) if r["pnl"] is not None else None,
            "edge": round(edge, 4) if edge is not None else None,
            "confidence": round(scan["confidence"], 1) if "confidence" in scan else None,
            "strategy": r["strategy"],
            "fee": round(r["fee"], 4) if r["fee"] else 0.0,
        })
    return result


@app.get("/api/resting")
async def get_resting():
    try:
        resting = _kalshi.get_orders(status="resting")
    except Exception as e:
        return []

    result = []
    for o in resting:
        ticker = o.get("ticker", "")
        action = o.get("action", "")
        side = o.get("side", "")
        remaining = int(float(o.get("remaining_count_fp", "0")))
        if remaining == 0:
            continue
        price = o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars")
        price_cents = int(float(price or 0) * 100)
        created = (o.get("created_time", "")[:16].replace("T", " "))

        contract = _parse_contract(ticker, side.upper())

        result.append({
            "city": ticker_to_city(ticker),
            "action": action.upper(),
            "side": side.upper(),
            "contract": contract,
            "remaining": remaining,
            "price": price_cents,
            "created": created,
        })

    return result


@app.get("/api/settled")
async def get_settled(limit: int = Query(50)):
    if not TRADES_DB.exists():
        return {"summary": {}, "trades": []}
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row

    # Summary
    summary_rows = conn.execute("""
        SELECT settlement_outcome, COUNT(*) as cnt, COALESCE(SUM(pnl), 0) as total_pnl
        FROM trades WHERE settlement_outcome IS NOT NULL AND NOT side LIKE 'sell_%'
        GROUP BY settlement_outcome
    """).fetchall()
    summary = {}
    for r in summary_rows:
        summary[r["settlement_outcome"]] = {"count": r["cnt"], "pnl": round(r["total_pnl"], 2)}

    # Recent settled trades (entry fills only, not exit fills)
    rows = conn.execute("""
        SELECT ticker, city, side, fill_price, fill_qty, fill_time, settlement_outcome, pnl, strategy, fee
        FROM trades WHERE settlement_outcome IN ('win', 'loss') AND fill_qty > 0
        ORDER BY fill_time DESC LIMIT ?
    """, (limit,)).fetchall()

    fee_row = conn.execute("""
        SELECT COALESCE(SUM(fee), 0) as total_fees,
               SUM(CASE WHEN strategy='maker' THEN 1 ELSE 0 END) as maker_count,
               SUM(CASE WHEN strategy='taker' THEN 1 ELSE 0 END) as taker_count
        FROM trades WHERE strategy IS NOT NULL AND settlement_outcome IN ('win', 'loss')
    """).fetchone()
    conn.close()

    summary["total_fees"] = round(fee_row["total_fees"], 4)
    summary["maker_count"] = fee_row["maker_count"]
    summary["taker_count"] = fee_row["taker_count"]

    trades = []
    for r in rows:
        trades.append({
            "city": ticker_to_city(r["ticker"]) or (r["city"] or "").replace("_", " ").title(),
            "side": "NO" if "no" in (r["side"] or "") else "YES",
            "price": r["fill_price"],
            "qty": r["fill_qty"],
            "time": r["fill_time"],
            "outcome": r["settlement_outcome"],
            "pnl": round(r["pnl"], 2) if r["pnl"] is not None else None,
            "strategy": r["strategy"],
            "fee": round(r["fee"], 4) if r["fee"] else 0.0,
        })

    return {"summary": summary, "trades": trades}


@app.get("/api/performance/fees")
async def get_fee_chart():
    """Cumulative P&L vs fees time series for chart."""
    from dashboard.equity_db import EQUITY_DB
    equity_path = Path(EQUITY_DB)
    if not equity_path.exists():
        return []
    conn = sqlite3.connect(str(equity_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date, realized_pnl, fees_paid FROM equity_snapshots ORDER BY date"
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        # realized_pnl and fees_paid are already cumulative lifetime totals
        # from Kalshi settled positions (recorded daily in daemon Phase 4)
        result.append({
            "date": r["date"],
            "cumulative_pnl": round(r["realized_pnl"], 2),
            "cumulative_fees": round(r["fees_paid"], 2),
        })
    return result


@app.get("/api/fees/summary")
async def get_fee_summary():
    """Fee summary: total fees, maker/taker counts, savings estimate."""
    from kalshi.pricing import kalshi_fee
    from dashboard.equity_db import EQUITY_DB

    # Get maker/taker counts and savings estimate from trades.db
    maker_count = 0
    taker_count = 0
    estimated_taker_fees_for_makers = 0.0

    if TRADES_DB.exists():
        conn = sqlite3.connect(str(TRADES_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT strategy, fill_price, fill_qty FROM trades WHERE strategy IS NOT NULL"
        ).fetchall()
        conn.close()

        for r in rows:
            if r["strategy"] == "maker":
                maker_count += 1
                if r["fill_price"] and r["fill_qty"]:
                    estimated_taker_fees_for_makers += kalshi_fee(
                        r["fill_price"], r["fill_qty"], is_taker=True
                    )
            elif r["strategy"] == "taker":
                taker_count += 1

    # Get total fees from equity_db (recorded daily, avoids live API call)
    total_fees = 0.0
    equity_path = Path(EQUITY_DB)
    if equity_path.exists():
        conn = sqlite3.connect(str(equity_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT fees_paid FROM equity_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            total_fees = row["fees_paid"]

    return {
        "total_fees_paid": round(total_fees, 2),
        "maker_trades": maker_count,
        "taker_trades": taker_count,
        "fee_savings": round(estimated_taker_fees_for_makers, 2),
    }


ANALYTICS_DB = Path(__file__).resolve().parent.parent / "data" / "analytics.db"


@app.get("/api/analytics/scorecard")
async def analytics_scorecard():
    if not ANALYTICS_DB.exists():
        return {"today": None, "yesterday": None, "rolling_7d": None}
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")

    today_row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
    yesterday_row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (yesterday,)).fetchone()
    rolling = conn.execute("""
        SELECT SUM(wins) as wins, SUM(losses) as losses, SUM(net_pnl) as pnl,
               AVG(avg_win) as avg_win, AVG(avg_loss) as avg_loss
        FROM daily_stats WHERE date >= date('now', '-7 days')
    """).fetchone()
    conn.close()

    return {
        "today": dict(today_row) if today_row else None,
        "yesterday": dict(yesterday_row) if yesterday_row else None,
        "rolling_7d": dict(rolling) if rolling else None,
    }


@app.get("/api/analytics/trends")
async def analytics_trends():
    if not ANALYTICS_DB.exists():
        return {"daily": []}
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, hit_rate, net_pnl, wins, losses
        FROM daily_stats ORDER BY date DESC LIMIT 30
    """).fetchall()
    conn.close()
    return {"daily": [dict(r) for r in reversed(rows)]}


@app.get("/api/analytics/actions")
async def analytics_actions():
    if not ANALYTICS_DB.exists():
        return {"summary": {}, "recent": []}
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row

    # Action counts last 7 days
    cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=7)).isoformat()
    counts = conn.execute("""
        SELECT action, COUNT(*) as count FROM manager_actions
        WHERE timestamp > ? GROUP BY action
    """, (cutoff,)).fetchall()

    spread_blocked = conn.execute("""
        SELECT COUNT(*) as count FROM manager_actions
        WHERE timestamp > ? AND reason LIKE '%spread%'
    """, (cutoff,)).fetchone()

    # Recent non-hold actions
    recent = conn.execute("""
        SELECT * FROM manager_actions
        WHERE action != 'hold' ORDER BY timestamp DESC LIMIT 20
    """).fetchall()

    conn.close()

    summary = {r["action"]: r["count"] for r in counts}
    summary["spread_blocked"] = spread_blocked["count"] if spread_blocked else 0

    return {
        "summary": summary,
        "recent": [dict(r) for r in recent],
    }


@app.get("/api/analytics/recommendations")
async def analytics_recommendations():
    if not ANALYTICS_DB.exists():
        return []
    conn = sqlite3.connect(str(ANALYTICS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM recommendations WHERE status='pending'
        ORDER BY CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "time": datetime.now(timezone.utc).isoformat()}