"""ERCOT dashboard API endpoints.

Reads from ercot_paper.db — no live API calls.
"""

from fastapi import APIRouter, Query

from ercot.paper_trader import (
    get_cached_signals, get_open_positions, get_trade_history, get_paper_summary,
)

ercot_router = APIRouter(prefix="/api/ercot", tags=["ercot"])


@ercot_router.get("/signals")
async def ercot_signals():
    return get_cached_signals()


@ercot_router.get("/positions")
async def ercot_positions():
    positions = get_open_positions()
    # Enrich with unrealized P&L from latest cached price
    cached = get_cached_signals()
    price_map = {s["hub"]: s["current_ercot_price"] for s in cached}

    for pos in positions:
        current_price = price_map.get(pos["hub"], pos["entry_price"])
        direction = -1.0 if pos["signal"] == "SHORT" else 1.0
        pos["unrealized_pnl"] = round(
            direction * (current_price - pos["entry_price"]) / pos["entry_price"] * pos["size_dollars"], 2
        )
        pos["current_price"] = current_price

    return positions


@ercot_router.get("/trades")
async def ercot_trades(limit: int = Query(50)):
    return get_trade_history(limit=limit)


@ercot_router.get("/summary")
async def ercot_summary():
    summary = get_paper_summary()
    # Add per-hub breakdown
    trades = get_trade_history(limit=500)
    hub_pnl = {}
    for t in trades:
        hub_pnl[t["hub"]] = hub_pnl.get(t["hub"], 0) + t["pnl"]

    if hub_pnl:
        summary["best_hub"] = max(hub_pnl, key=hub_pnl.get)
        summary["worst_hub"] = min(hub_pnl, key=hub_pnl.get)
        summary["hub_pnl"] = {k: round(v, 2) for k, v in hub_pnl.items()}
    else:
        summary["best_hub"] = None
        summary["worst_hub"] = None
        summary["hub_pnl"] = {}

    return summary
