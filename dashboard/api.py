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
    rows = conn.execute(
        """SELECT ticker, side, fill_price, fill_qty, fill_time
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
        # Walk fills in chronological order
        # After each sell, reset cost — only count buys since last full exit
        cost_cents = 0
        for f in fills:
            side = f["side"] or ""
            cents = (f["fill_price"] or 0) * (f["fill_qty"] or 0)
            if side.startswith("sell"):
                # Full exit resets cost basis; partial sell reduces proportionally
                cost_cents = 0
            else:
                cost_cents += cents
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

        open_pos = []
        for p in get_positions():
            qty_fp = float(p.get("position_fp", 0))
            if qty_fp != 0:
                qty = abs(qty_fp)
                ticker = p.get("ticker", "")
                exposure = float(p.get("market_exposure_dollars", 0))
                cost = cost_basis.get(ticker, 0)
                pnl = round(exposure - cost, 2) if cost > 0 else 0
                entry = round(cost / qty, 2) if qty > 0 and cost > 0 else 0

                # Parse settlement date from ticker (e.g. KXHIGHNY-26MAR14 → 2026-03-14)
                settles = ""
                parts = ticker.split("-")
                if len(parts) >= 2:
                    import re
                    m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", parts[1])
                    if m:
                        yr, mon_str, day = m.groups()
                        months = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
                                  "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}
                        mon = months.get(mon_str, "01")
                        settles = f"20{yr}-{mon}-{day}"

                open_pos.append({
                    "ticker": ticker,
                    "city": ticker_to_city(ticker),
                    "side": "YES" if qty_fp > 0 else "NO",
                    "qty": int(qty),
                    "entry": entry,
                    "value": round(exposure, 2),
                    "pnl": pnl,
                    "fees": round(float(p.get("fees_paid_dollars", 0)), 2),
                    "settles": settles,
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


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "time": datetime.now(timezone.utc).isoformat()}