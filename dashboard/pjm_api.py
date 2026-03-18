"""PJM dashboard API endpoints.

Reads from pjm_paper.db — no live API calls.
"""

from fastapi import APIRouter, Query

from pjm.paper_trader import (
    get_cached_signals, get_open_positions, get_trade_history, get_paper_summary,
)

pjm_router = APIRouter(prefix="/api/pjm", tags=["pjm"])


@pjm_router.get("/signals")
async def pjm_signals():
    return get_cached_signals()


@pjm_router.get("/positions")
async def pjm_positions():
    positions = get_open_positions()
    cached = get_cached_signals()
    price_map = {s["hub"]: s.get("current_pjm_price", 0) for s in cached}

    for pos in positions:
        current_price = price_map.get(pos["hub"], pos["entry_price"])
        direction = -1.0 if pos["signal"] == "SHORT" else 1.0
        pos["unrealized_pnl"] = round(
            direction * (current_price - pos["entry_price"]) / pos["entry_price"] * pos["size_dollars"], 2
        )
        pos["current_price"] = current_price

    return positions


@pjm_router.get("/trades")
async def pjm_trades(limit: int = Query(50)):
    return get_trade_history(limit=limit)


@pjm_router.get("/summary")
async def pjm_summary():
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
