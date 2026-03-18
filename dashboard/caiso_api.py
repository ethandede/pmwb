"""CAISO dashboard API endpoints.

Reads from caiso_paper.db — no live API calls.
"""

from fastapi import APIRouter, Query

from caiso.paper_trader import (
    get_cached_signals, get_open_positions, get_trade_history, get_paper_summary,
)

caiso_router = APIRouter(prefix="/api/caiso", tags=["caiso"])


@caiso_router.get("/signals")
async def caiso_signals():
    return get_cached_signals()


@caiso_router.get("/positions")
async def caiso_positions():
    positions = get_open_positions()
    cached = get_cached_signals()
    price_map = {s["hub"]: s.get("current_caiso_price", 0) for s in cached}

    for pos in positions:
        current_price = price_map.get(pos["hub"], pos["entry_price"])
        direction = -1.0 if pos["signal"] == "SHORT" else 1.0
        pos["unrealized_pnl"] = round(
            direction * (current_price - pos["entry_price"]) / pos["entry_price"] * pos["size_dollars"], 2
        )
        pos["current_price"] = current_price

    return positions


@caiso_router.get("/trades")
async def caiso_trades(limit: int = Query(50)):
    return get_trade_history(limit=limit)


@caiso_router.get("/summary")
async def caiso_summary():
    summary = get_paper_summary()
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
