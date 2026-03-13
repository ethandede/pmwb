"""Backfill trades.db from Kalshi executed order history.

Fetches all executed orders via the API and records them in the fills database.
Safe to run multiple times — deduplicates on order_id.

Usage: python -m kalshi.backfill_trades
"""

from datetime import datetime, timezone
from kalshi.trader import _get
from kalshi.fill_tracker import init_trades_db, record_fill

TRADES_DB_PATH = "data/trades.db"


def _parse_city_from_ticker(ticker: str) -> str:
    """Best-effort city extraction from ticker prefix."""
    from kalshi.scanner import WEATHER_SERIES, PRECIP_SERIES
    for series_prefix, info in {**WEATHER_SERIES, **PRECIP_SERIES}.items():
        if ticker.upper().startswith(series_prefix.upper()):
            return info["city"]
    return ""


def backfill():
    init_trades_db(TRADES_DB_PATH)

    cursor = None
    total = 0
    new = 0

    while True:
        params = {"limit": 200, "status": "executed"}
        if cursor:
            params["cursor"] = cursor

        data = _get("/trade-api/v2/portfolio/orders", params)
        orders = data.get("orders", [])
        if not orders:
            break

        for o in orders:
            order_id = o.get("order_id", "")
            ticker = o.get("ticker", "")
            side = o.get("side", "")
            fill_qty = int(float(o.get("fill_count_fp", "0")))
            fill_time = o.get("last_update_time", "")

            # Parse price — use the side-appropriate price
            if side == "yes":
                price_dollars = float(o.get("yes_price_dollars", "0") or "0")
            else:
                price_dollars = float(o.get("no_price_dollars", "0") or "0")
            price_cents = int(price_dollars * 100)

            # Total cost from taker + maker fill cost
            taker_cost = float(o.get("taker_fill_cost_dollars", "0") or "0")
            maker_cost = float(o.get("maker_fill_cost_dollars", "0") or "0")

            city = _parse_city_from_ticker(ticker)
            action = o.get("action", "buy")

            record_fill(
                db_path=TRADES_DB_PATH,
                order_id=order_id,
                ticker=ticker,
                side=f"{action}_{side}",
                limit_price=price_cents,
                fill_price=price_cents,
                fill_qty=fill_qty,
                fill_time=fill_time,
                city=city,
            )
            total += 1

        cursor = data.get("cursor")
        if not cursor:
            break

    print(f"Backfill complete: {total} orders processed")

    # Show summary
    from kalshi.fill_tracker import get_all_trades
    trades = get_all_trades(TRADES_DB_PATH)
    print(f"Total trades in DB: {len(trades)}")

    cities = {}
    for t in trades:
        c = t.get("city", "unknown") or "unknown"
        cities[c] = cities.get(c, 0) + 1
    for city, count in sorted(cities.items(), key=lambda x: -x[1]):
        print(f"  {city}: {count}")


if __name__ == "__main__":
    backfill()
