"""Settlement Resolver — fetch settled market results and update trades.db with P&L.

Checks all unresolved trades, fetches market result from Kalshi, and calculates
actual profit/loss for each fill.

Usage: python -m kalshi.settler
"""

import time
from kalshi.trader import _get
from kalshi.fill_tracker import (
    init_trades_db,
    get_unresolved_trades,
    resolve_trade,
    get_all_trades,
)

TRADES_DB_PATH = "data/trades.db"


def _fetch_market_result(ticker: str) -> dict | None:
    """Fetch a market's settlement result from Kalshi API.

    Returns {"result": "yes"|"no", "expiration_value": float} or None if not settled.
    """
    try:
        data = _get(f"/trade-api/v2/markets/{ticker}")
        market = data.get("market", {})
        status = market.get("status", "")
        result = market.get("result", "")
        if status == "finalized" and result in ("yes", "no"):
            return {
                "result": result,
                "expiration_value": market.get("expiration_value"),
            }
        return None
    except Exception as e:
        print(f"  API error for {ticker}: {e}")
        return None


def _calculate_pnl(side: str, fill_price: int, fill_qty: int, result: str) -> float:
    """Calculate P&L in dollars for a settled trade.

    Args:
        side: "buy_yes", "buy_no", "sell_yes", "sell_no"
        fill_price: price in cents
        fill_qty: number of contracts
        result: "yes" or "no" (market settlement)

    Returns P&L in dollars (positive = profit, negative = loss).
    """
    if fill_qty == 0:
        return 0.0

    price_cents = fill_price

    if side == "buy_yes":
        if result == "yes":
            return fill_qty * (100 - price_cents) / 100.0  # win
        else:
            return -fill_qty * price_cents / 100.0  # lose

    elif side == "buy_no":
        if result == "no":
            return fill_qty * (100 - price_cents) / 100.0  # win
        else:
            return -fill_qty * price_cents / 100.0  # lose

    elif side == "sell_yes":
        if result == "no":
            return fill_qty * price_cents / 100.0  # win (collected premium)
        else:
            return -fill_qty * (100 - price_cents) / 100.0  # lose

    elif side == "sell_no":
        if result == "yes":
            return fill_qty * price_cents / 100.0  # win
        else:
            return -fill_qty * (100 - price_cents) / 100.0  # lose

    return 0.0


def run_settler():
    """Resolve all unresolved trades against settled markets."""
    init_trades_db(TRADES_DB_PATH)
    unresolved = get_unresolved_trades(TRADES_DB_PATH)

    if not unresolved:
        print("No unresolved trades.")
        return

    print(f"Checking {len(unresolved)} unresolved trades...")

    # Group by ticker to avoid duplicate API calls
    ticker_trades: dict[str, list[dict]] = {}
    for trade in unresolved:
        ticker_trades.setdefault(trade["ticker"], []).append(trade)

    resolved = 0
    still_open = 0
    total_pnl = 0.0
    wins = 0
    losses = 0

    for ticker, trades in sorted(ticker_trades.items()):
        settlement = _fetch_market_result(ticker)
        time.sleep(0.1)  # Rate limit

        if settlement is None:
            still_open += len(trades)
            continue

        result = settlement["result"]
        exp_val = settlement.get("expiration_value", "?")

        for trade in trades:
            pnl = _calculate_pnl(
                side=trade["side"],
                fill_price=trade["fill_price"],
                fill_qty=trade["fill_qty"],
                result=result,
            )

            outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "push"
            resolve_trade(TRADES_DB_PATH, trade["order_id"], outcome, pnl)

            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            total_pnl += pnl
            resolved += 1

            print(f"  {ticker} {trade['side']} {trade['fill_qty']}x@{trade['fill_price']}¢ → {result.upper()} → {outcome} ${pnl:+.2f}")

    print(f"\nResolved: {resolved} trades | Still open: {still_open}")
    if resolved > 0:
        print(f"Results: {wins}W / {losses}L | P&L: ${total_pnl:+.2f}")

    # Summary of all-time resolved trades
    all_trades = get_all_trades(TRADES_DB_PATH)
    settled = [t for t in all_trades if t["settlement_outcome"] is not None]
    if settled:
        total = sum(t["pnl"] for t in settled)
        w = sum(1 for t in settled if t["pnl"] > 0)
        l = sum(1 for t in settled if t["pnl"] < 0)
        print(f"\nAll-time: {w}W / {l}L ({w/(w+l)*100:.0f}% hit rate) | Total P&L: ${total:+.2f}")


if __name__ == "__main__":
    run_settler()
