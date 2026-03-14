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
    """Compute net cost basis per ticker from trades.db.

    For each ticker, sums buy fills (cost) and sell fills (proceeds).
    Returns {ticker: net_cost_in_dollars}.
    """
    if not TRADES_DB.exists():
        return {}
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT ticker, side, fill_price, fill_qty
           FROM trades WHERE fill_qty > 0"""
    ).fetchall()
    conn.close()

    basis = {}
    for r in rows:
        ticker = r["ticker"]
        cents = (r["fill_price"] or 0) * (r["fill_qty"] or 0)
        side = r["side"] or ""
        if side.startswith("sell"):
            basis[ticker] = basis.get(ticker, 0) - cents
        else:
            # "buy_yes", "buy_no", or legacy bare "yes"/"no" — all are buys
            basis[ticker] = basis.get(ticker, 0) + cents
    return {k: round(v / 100.0, 2) for k, v in basis.items()}

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
                open_pos.append({
                    "ticker": ticker,
                    "city": ticker_to_city(ticker),
                    "side": "YES" if qty_fp > 0 else "NO",
                    "qty": int(qty),
                    "entry": entry,
                    "value": round(exposure, 2),
                    "pnl": pnl,
                    "fees": round(float(p.get("fees_paid_dollars", 0)), 2),
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
    return [
        {
            "ticker": r["ticker"],
            "city": ticker_to_city(r["ticker"]) if not r["city"] else r["city"],
            "action": "SELL" if (r["side"] or "").startswith("sell") else "BUY",
            "side": "YES" if "yes" in (r["side"] or "") else "NO",
            "price": r["fill_price"],
            "qty": r["fill_qty"],
            "time": r["fill_time"],
            "outcome": r["settlement_outcome"],
            "pnl": round(r["pnl"], 2) if r["pnl"] is not None else None,
        }
        for r in rows
    ]


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "time": datetime.now(timezone.utc).isoformat()}