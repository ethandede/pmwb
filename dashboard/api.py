# dashboard/api.py
"""FastAPI backend for Weather Edge dashboard.
Serves JSON API endpoints + static frontend.
Run: uvicorn dashboard.api:app --host 0.0.0.0 --port 8501
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from dashboard.scan_cache import init_scan_cache_db, get_latest_scan, get_scan_history
from dashboard.equity_db import init_equity_db, get_equity_curve
from dashboard.ticker_map import ticker_to_city

from kalshi.trader import get_balance, get_positions

TRADES_DB = Path(__file__).resolve().parent.parent / "data" / "trades.db"


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
        # Get all buy fills (ignore sells for cost basis — we know we hold the position)
        buy_fills = [f for f in fills if not (f["side"] or "").startswith("sell")]
        if not buy_fills:
            continue
        # Use the last buy fill as the current entry cost
        last_buy = buy_fills[-1]
        cost_cents = (last_buy["fill_price"] or 0) * (last_buy["fill_qty"] or 0)
        if cost_cents > 0:
            basis[ticker] = round(cost_cents / 100.0, 2)

    return basis

# Initialise DBs
init_scan_cache_db()
init_equity_db()

app = FastAPI(title="Weather Edge Dashboard")

# Serve static files and index.html
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"status": "Frontend not found"}


@app.get("/api/portfolio")
async def get_portfolio():
    try:
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        positions_val = bal.get("portfolio_value", 0) / 100.0
        equity = cash + positions_val

        cost_basis = _get_cost_basis()

        # Fetch GFS forecasts per city (self-hosted, instant)
        from kalshi.scanner import WEATHER_SERIES
        import requests as _req
        _forecast_cache = {}
        for prefix, info in WEATHER_SERIES.items():
            city_name = info["city"]
            if city_name in _forecast_cache:
                continue
            try:
                unit_param = "fahrenheit" if info.get("unit", "f") == "f" else "celsius"
                r = _req.get(
                    f"http://localhost:8080/v1/forecast?latitude={info['lat']}&longitude={info['lon']}"
                    f"&daily=temperature_2m_max,temperature_2m_min"
                    f"&hourly=temperature_2m"
                    f"&models=gfs_seamless&temperature_unit={unit_param}&timezone=auto&forecast_days=3",
                    timeout=3,
                )
                d = r.json()
                daily = d.get("daily", {})
                hourly = d.get("hourly", {})
                hourly_temps = hourly.get("temperature_2m", [])
                current = hourly_temps[-1] if hourly_temps else None
                _forecast_cache[city_name] = {
                    "high": daily.get("temperature_2m_max", [None, None]),
                    "low": daily.get("temperature_2m_min", [None, None]),
                    "current": current,
                }
            except Exception:
                _forecast_cache[city_name] = {"high": [None, None], "low": [None, None], "current": None}

        open_pos = []
        for p in get_positions():
            qty_fp = float(p.get("position_fp", 0))
            if qty_fp != 0:
                qty = abs(qty_fp)
                ticker = p.get("ticker", "")
                exposure = float(p.get("market_exposure_dollars", 0))
                cost = cost_basis.get(ticker, 0)
                # Negative cost means churn recovered more than current buy cost — position is "free"
                pnl = round(exposure - cost, 2) if cost != 0 else 0
                entry = round(max(0, cost) / qty, 2) if qty > 0 and cost > 0 else 0

                # Parse settlement date and contract from ticker
                # e.g. KXHIGHNY-26MAR14-T56 → date=2026-03-14, contract=">56°F"
                # e.g. KXHIGHNY-26MAR14-B51.5 → date=2026-03-14, contract="51-52°F"
                import re
                settles = ""
                contract = ""
                parts = ticker.split("-")
                if len(parts) >= 2:
                    m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", parts[1])
                    if m:
                        yr, mon_str, day = m.groups()
                        months = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
                                  "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}
                        mon = months.get(mon_str, "01")
                        settles = f"20{yr}-{mon}-{day}"
                if len(parts) >= 3:
                    strike = parts[2]
                    if strike.startswith("T"):
                        contract = f">{strike[1:]}\u00b0"
                    elif strike.startswith("B"):
                        val = float(strike[1:])
                        contract = f"{val:.0f}-{val+2:.0f}\u00b0"

                # Look up forecast for this city
                city_slug = None
                for prefix, info in WEATHER_SERIES.items():
                    if ticker.upper().startswith(prefix.upper()):
                        city_slug = info["city"]
                        break
                fc = _forecast_cache.get(city_slug, {})
                # Pick forecast day: day 0 for today's event, day 1 for tomorrow's
                from datetime import date as _date
                today_str = _date.today().isoformat()
                fc_idx = 0 if settles <= today_str else 1
                fc_high = fc.get("high", [None, None])
                fc_low = fc.get("low", [None, None])
                forecast_high = fc_high[fc_idx] if fc_idx < len(fc_high) else None
                forecast_low = fc_low[fc_idx] if fc_idx < len(fc_low) else None

                # Predict likely result based on forecast vs contract
                likely = None
                side_str = "YES" if qty_fp > 0 else "NO"
                if forecast_high is not None and len(parts) >= 3:
                    strike = parts[2]
                    if strike.startswith("T"):
                        threshold = float(strike[1:])
                        temp_above = forecast_high >= threshold
                        if side_str == "YES":
                            likely = "WIN" if temp_above else "LOSS"
                        else:
                            likely = "LOSS" if temp_above else "WIN"
                    elif strike.startswith("B"):
                        threshold = float(strike[1:])
                        in_bucket = threshold <= forecast_high < threshold + 2
                        if side_str == "YES":
                            likely = "WIN" if in_bucket else "LOSS"
                        else:
                            likely = "WIN" if not in_bucket else "LOSS"

                open_pos.append({
                    "ticker": ticker,
                    "city": ticker_to_city(ticker),
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
                    "current_temp": round(fc.get("current"), 1) if fc.get("current") is not None else None,
                    "likely": likely,
                })

        return {
            "balance": {
                "cash": round(cash, 2),
                "positions": round(positions_val, 2),
                "equity": round(equity, 2),
            },
            "open_positions": open_pos,
        }
    except Exception as e:
        return {"error": str(e), "balance": {"equity": 0}, "open_positions": []}


@app.get("/api/markets/{market_type}")
async def get_markets(market_type: str, force: bool = Query(False)):
    if market_type not in ("temp", "precip"):
        return {"error": "Invalid market_type"}

    if force:
        # You can call run_scanner() here later if you want "Force Rescan" to trigger a new scan
        pass

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
        "settled_daily": [],  # Expand later if needed
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
                  settlement_outcome, pnl
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
        SELECT ticker, city, side, fill_price, fill_qty, fill_time, settlement_outcome, pnl
        FROM trades WHERE settlement_outcome IN ('win', 'loss') AND fill_qty > 0
        ORDER BY fill_time DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

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
        })

    return {"summary": summary, "trades": trades}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "time": datetime.now(timezone.utc).isoformat()}