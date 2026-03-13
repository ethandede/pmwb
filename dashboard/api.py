# dashboard/api.py
"""FastAPI backend for Weather Edge dashboard.

Serves JSON API endpoints + static frontend files.
Run: uvicorn dashboard.api:app --host 127.0.0.1 --port 8501
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from datetime import datetime, timezone

from dashboard.ticker_map import ticker_to_city
from dashboard.scan_cache import (
    init_scan_cache_db, get_latest_scan, get_scan_history,
    get_model_outcomes, write_scan_results, cleanup_old_scans,
)
from dashboard.equity_db import init_equity_db, get_equity_curve

from config import (
    PAPER_MODE, ALERT_THRESHOLD, CONFIDENCE_THRESHOLD,
    MAX_ORDER_USD, MAX_SCAN_BUDGET, KELLY_MAX_FRACTION,
    DRAWDOWN_THRESHOLD, FRACTIONAL_KELLY, DAILY_STOP_PCT,
    MAX_ENSEMBLE_HORIZON_DAYS,
)

# Initialise DBs eagerly so they exist even before the lifespan fires
# (covers test client usage where lifespan may not run)
init_scan_cache_db()
init_equity_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-run init in case paths differ at runtime
    init_scan_cache_db()
    init_equity_db()
    yield


app = FastAPI(title="Weather Edge API", lifespan=lifespan)

# Serve static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"error": "Frontend not built yet"}, status_code=404)


@app.get("/api/portfolio")
async def portfolio():
    try:
        from kalshi.trader import get_balance, get_positions, get_orders

        # Balance
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        positions_val = bal.get("portfolio_value", 0) / 100.0
        equity = cash + positions_val
        deployed_pct = round(positions_val / equity * 100, 1) if equity > 0 else 0

        # Settled P&L
        settled = get_positions(limit=200, settlement_status="settled")
        gross_pnl = sum(float(p.get("realized_pnl_dollars", "0")) for p in settled)
        fees = sum(float(p.get("fees_paid_dollars", "0")) for p in settled)
        net_pnl = gross_pnl - fees
        wins = sum(1 for p in settled if float(p.get("realized_pnl_dollars", "0")) > 0)
        losses = sum(1 for p in settled if float(p.get("realized_pnl_dollars", "0")) < 0)
        total_settled = wins + losses
        hit_rate = round(wins / total_settled * 100, 1) if total_settled > 0 else 0

        # Open positions
        all_pos = get_positions()
        open_pos = [p for p in all_pos if float(p.get("position_fp", "0")) != 0]
        open_positions = []
        for p in open_pos:
            qty = float(p.get("position_fp", "0"))
            abs_qty = abs(qty)
            cost = float(p.get("total_traded_dollars", "0"))
            exposure = float(p.get("market_exposure_dollars", "0"))
            realized = float(p.get("realized_pnl_dollars", "0"))
            ticker = p.get("ticker", "")
            open_positions.append({
                "ticker": ticker,
                "city": ticker_to_city(ticker),
                "side": "YES" if qty > 0 else "NO",
                "qty": int(abs_qty),
                "entry": round(cost / abs_qty, 2) if abs_qty > 0 else 0,
                "exposure": round(exposure, 2),
                "pnl": round(exposure - cost + realized, 2),
                "fees": round(float(p.get("fees_paid_dollars", "0")), 2),
            })

        # Resting orders
        resting = get_orders(status="resting")
        resting_orders = []
        for o in resting:
            side = o.get("side", "")
            price = o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars")
            resting_orders.append({
                "ticker": o.get("ticker", ""),
                "action": o.get("action", "").upper(),
                "side": side.upper(),
                "remaining": int(float(o.get("remaining_count_fp", "0"))),
                "price": float(price) if price else 0,
                "created": (o.get("created_time", "")[:16].replace("T", " ")),
            })

        return {
            "balance": {
                "cash": round(cash, 2),
                "positions": round(positions_val, 2),
                "equity": round(equity, 2),
                "deployed_pct": deployed_pct,
            },
            "settled": {
                "gross_pnl": round(gross_pnl, 2),
                "fees": round(fees, 2),
                "net_pnl": round(net_pnl, 2),
                "wins": wins,
                "losses": losses,
                "hit_rate": hit_rate,
                "total_settled": total_settled,
            },
            "open_positions": open_positions,
            "resting_orders": resting_orders,
        }
    except Exception as e:
        return JSONResponse({"error": str(e), "cached_at": None}, status_code=502)


@app.get("/api/markets/{market_type}")
async def markets(market_type: str, force: bool = Query(False)):
    if market_type not in ("temp", "precip"):
        return JSONResponse({"error": "market_type must be 'temp' or 'precip'"}, status_code=400)

    if force:
        try:
            from kalshi.scanner import (
                get_kalshi_weather_markets, get_kalshi_precip_markets,
                get_kalshi_price, parse_kalshi_bucket,
            )
            from weather.multi_model import fuse_forecast, fuse_precip_forecast
            from weather.forecast import calculate_remaining_month_days

            month = datetime.now(timezone.utc).month
            remaining_days = calculate_remaining_month_days()
            cache_rows = []

            if market_type == "temp":
                raw_markets = get_kalshi_weather_markets()
                for m in raw_markets:
                    try:
                        yes_price = get_kalshi_price(m)
                        if yes_price is None:
                            continue
                        bucket = parse_kalshi_bucket(m)
                        if not bucket:
                            continue
                        low, high = bucket
                        city = m.get("_city", "unknown")
                        ticker = m.get("ticker", "")
                        prob, confidence, details = fuse_forecast(
                            m["_lat"], m["_lon"], city, month,
                            low, high, days_ahead=1, unit=m.get("_unit", "f"),
                        )
                        edge = prob - yes_price
                        bucket_str = f"{low:.0f}-{high:.0f}" if high else f">{low:.0f}"
                        cache_rows.append({
                            "market_type": "temp",
                            "ticker": ticker,
                            "city": ticker_to_city(ticker),
                            "model_prob": round(prob, 4),
                            "market_price": round(yes_price, 4),
                            "edge": round(edge, 4),
                            "direction": "BUY YES" if edge > 0 else "SELL YES",
                            "confidence": confidence,
                            "method": details.get("ensemble", {}).get("method", ""),
                            "threshold": bucket_str,
                            "days_left": 1,
                        })
                    except Exception:
                        pass
            else:
                raw_markets = get_kalshi_precip_markets()
                forecast_window = min(remaining_days, MAX_ENSEMBLE_HORIZON_DAYS)
                for m in raw_markets:
                    try:
                        yes_price = get_kalshi_price(m)
                        if yes_price is None:
                            continue
                        threshold = m.get("_threshold", 0.0)
                        city = m.get("_city", "unknown")
                        ticker = m.get("ticker", "")
                        prob, confidence, details = fuse_precip_forecast(
                            m["_lat"], m["_lon"], city, month,
                            threshold=threshold, forecast_days=forecast_window,
                        )
                        edge = prob - yes_price
                        cache_rows.append({
                            "market_type": "precip",
                            "ticker": ticker,
                            "city": ticker_to_city(ticker),
                            "model_prob": round(prob, 4),
                            "market_price": round(yes_price, 4),
                            "edge": round(edge, 4),
                            "direction": "BUY YES" if edge > 0 else "SELL YES",
                            "confidence": confidence,
                            "method": details.get("ensemble", {}).get("method", ""),
                            "threshold": f">{threshold:.1f} in",
                            "days_left": remaining_days,
                        })
                    except Exception:
                        pass

            if cache_rows:
                write_scan_results(cache_rows)
                cleanup_old_scans(days=30)

            scan_time = datetime.now(timezone.utc).isoformat()
            return {"scan_time": scan_time, "markets": cache_rows}

        except Exception as e:
            cached = get_latest_scan(market_type)
            cached["error"] = str(e)
            return cached

    return get_latest_scan(market_type)


@app.get("/api/markets/{market_type}/history")
async def market_history(market_type: str, days: int = Query(30)):
    if market_type not in ("temp", "precip"):
        return JSONResponse({"error": "market_type must be 'temp' or 'precip'"}, status_code=400)
    return get_scan_history(market_type, days=days)


@app.get("/api/performance")
async def performance():
    return {
        "equity_curve": get_equity_curve(),
        "model_accuracy": get_model_outcomes(),
    }


@app.get("/api/config")
async def config():
    return {
        "mode": "PAPER" if PAPER_MODE else "LIVE",
        "scan_interval_min": 15,
        "edge_gate": ALERT_THRESHOLD,
        "confidence_gate": CONFIDENCE_THRESHOLD,
        "kelly_range": [FRACTIONAL_KELLY, 0.50],
        "max_order_bankroll_pct": KELLY_MAX_FRACTION,
        "max_order_usd": MAX_ORDER_USD,
        "scan_budget_usd": MAX_SCAN_BUDGET,
        "drawdown_threshold": DRAWDOWN_THRESHOLD,
        "daily_stop_pct": DAILY_STOP_PCT,
    }
