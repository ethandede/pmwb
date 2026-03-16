"""Settlement Resolver — fetch settled market results and update trades.db with P&L.

Checks all unresolved trades, fetches market result from Kalshi, and calculates
actual profit/loss for each fill.

Individual market lookups 404 after settlement, so we batch-fetch settled markets
via the events API (one call per series) and resolve all matching trades.

Usage: python -m kalshi.settler
"""

import re
import time

from kalshi.fill_tracker import (
    init_trades_db,
    get_unresolved_trades,
    resolve_trade,
    get_all_trades,
)

TRADES_DB_PATH = "data/trades.db"


def _extract_series(ticker: str) -> str | None:
    """Extract series ticker from a market ticker.

    KXHIGHNY-26MAR15-T56 → KXHIGHNY
    """
    parts = ticker.split("-")
    if len(parts) >= 2 and re.match(r"\d{2}[A-Z]{3}\d{2}", parts[1]):
        return parts[0]
    return None


def _batch_fetch_settlements(tickers: set[str], exchange) -> tuple[dict[str, dict], dict[str, dict]]:
    """Batch-fetch settlement results for a set of tickers.

    Groups tickers by series, fetches settled events for each series.
    Returns (matched, all_settled):
      - matched: {ticker: {...}} for tickers we're looking for
      - all_settled: {ticker: {...}} for ALL settled markets (for inference)
    """
    # Group by series
    series_tickers: dict[str, set[str]] = {}
    for ticker in tickers:
        series = _extract_series(ticker)
        if series:
            series_tickers.setdefault(series, set()).add(ticker)

    matched: dict[str, dict] = {}
    all_settled: dict[str, dict] = {}

    for series in sorted(series_tickers):
        wanted = series_tickers[series]
        try:
            settled_markets = exchange.get_settled_event_markets(series)
            for ticker, market_data in settled_markets.items():
                status = market_data.get("status", "")
                mkt_result = market_data.get("result", "")
                if status == "finalized" and mkt_result in ("yes", "no"):
                    all_settled[ticker] = market_data
                    if ticker in wanted:
                        matched[ticker] = market_data
            time.sleep(0.2)  # Rate limit between series
        except Exception as e:
            print(f"  Batch fetch error for series {series}: {e}")

    return matched, all_settled


def _fetch_market_result(ticker: str, exchange) -> dict | None:
    """Fetch a single market's settlement result (fallback for non-batch use).

    Returns {"result": "yes"|"no", "expiration_value": float} or None if not settled.
    """
    try:
        market = exchange.get_market(ticker)
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


def _is_exit_fill(side: str) -> bool:
    """Return True if the fill side represents an exit (sell/close), not an entry.

    Exit fills: sell_yes, sell_no
    Entry fills: buy_yes, buy_no, or legacy bare "yes", "no"
    """
    return side.startswith("sell")


def _calculate_pnl(side: str, fill_price: int, fill_qty: int, result: str) -> float:
    """Calculate P&L in dollars for an entry position held to settlement.

    Only applies to buy_yes/buy_no fills that were NOT exited before settlement.
    Exit fills (sell_yes, sell_no) should NOT use this function -- their P&L was
    already realized at the time of sale.

    Args:
        side: "buy_yes" or "buy_no" (entry sides only)
        fill_price: price in cents
        fill_qty: number of contracts
        result: "yes" or "no" (market settlement)

    Returns P&L in dollars (positive = profit, negative = loss).
    """
    if fill_qty == 0:
        return 0.0

    price_cents = fill_price

    if side in ("buy_yes", "yes"):
        if result == "yes":
            return fill_qty * (100 - price_cents) / 100.0  # win
        else:
            return -fill_qty * price_cents / 100.0  # lose

    elif side in ("buy_no", "no"):
        if result == "no":
            return fill_qty * (100 - price_cents) / 100.0  # win
        else:
            return -fill_qty * price_cents / 100.0  # lose

    return 0.0


def run_settler(exchange=None):
    """Resolve all unresolved trades against settled markets."""
    if exchange is None:
        from exchanges.kalshi import KalshiExchange
        exchange = KalshiExchange()

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

    # Filter out ERCOT paper positions — these aren't Kalshi tickers
    ercot_tickers = [t for t in ticker_trades if t.startswith("HB_")]
    if ercot_tickers:
        ercot_count = sum(len(ticker_trades[t]) for t in ercot_tickers)
        print(f"Skipping {ercot_count} ERCOT paper trades ({', '.join(sorted(ercot_tickers))})")
        for t in ercot_tickers:
            del ticker_trades[t]

    # Batch-fetch settlements via events API (avoids 404s on individual lookups)
    settlement_cache, all_settled = _batch_fetch_settlements(set(ticker_trades.keys()), exchange)
    n_series = len(set(_extract_series(t) for t in ticker_trades if _extract_series(t)))
    print(f"  Found {len(settlement_cache)} settled markets across {n_series} series")

    # Infer results for missing tickers from same-event settled markets.
    # Safe monotonic inferences:
    #   - T_X result=no (high<X) → T_Y for Y>X is also no
    #   - B_X result=yes (high<X) → B_Y for Y>X is also yes
    missing = set(ticker_trades.keys()) - set(settlement_cache.keys())
    if missing:
        # Group ALL settled results by event (not just matched ones)
        event_results: dict[str, list[tuple[str, str, str]]] = {}
        for ticker, data in all_settled.items():
            parts = ticker.split("-")
            if len(parts) >= 3:
                event_key = f"{parts[0]}-{parts[1]}"
                event_results.setdefault(event_key, []).append(
                    (ticker, parts[2], data.get("result", ""))
                )

        inferred = 0
        for ticker in list(missing):
            parts = ticker.split("-")
            if len(parts) < 3:
                continue
            event_key = f"{parts[0]}-{parts[1]}"
            bucket = parts[2]
            if not bucket or bucket[0] not in ("B", "T"):
                continue
            try:
                threshold = float(bucket[1:])
            except (ValueError, IndexError):
                continue

            settled_in_event = event_results.get(event_key, [])
            inferred_result = None

            if bucket[0] == "T":
                # If any T with lower threshold has result=no, this T is also no
                for _, stype, sresult in settled_in_event:
                    if stype.startswith("T") and sresult == "no":
                        try:
                            settled_thresh = float(stype[1:])
                            if settled_thresh <= threshold:
                                inferred_result = "no"
                                break
                        except ValueError:
                            pass
            elif bucket[0] == "B":
                # If any B with higher threshold has result=yes, this B is also yes
                for _, stype, sresult in settled_in_event:
                    if stype.startswith("B") and sresult == "yes":
                        try:
                            settled_thresh = float(stype[1:])
                            if settled_thresh <= threshold:
                                inferred_result = "yes"
                                break
                        except ValueError:
                            pass

            if inferred_result:
                settlement_cache[ticker] = {
                    "status": "inferred",
                    "result": inferred_result,
                }
                inferred += 1

        if inferred:
            print(f"  Inferred {inferred} results from same-event settlements")

    resolved = 0
    still_open = 0
    total_pnl = 0.0
    wins = 0
    losses = 0

    for ticker, trades in sorted(ticker_trades.items()):
        settlement = settlement_cache.get(ticker)

        if settlement is None:
            still_open += len(trades)
            continue

        result = settlement["result"]

        for trade in trades:
            if _is_exit_fill(trade["side"]):
                # Exit fills already realized P&L at sale time; mark as exited with $0
                resolve_trade(TRADES_DB_PATH, trade["order_id"], "exited", 0.0)
                resolved += 1
                print(f"  {ticker} {trade['side']} {trade['fill_qty']}x@{trade['fill_price']}¢ → EXITED (P&L already realized)")
                continue

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

    # Summary of all-time resolved trades (only entry positions held to settlement)
    all_trades = get_all_trades(TRADES_DB_PATH)
    settled = [
        t for t in all_trades
        if t["settlement_outcome"] is not None and t["settlement_outcome"] != "exited"
    ]
    exited = [t for t in all_trades if t["settlement_outcome"] == "exited"]
    if settled:
        total = sum(t["pnl"] for t in settled)
        w = sum(1 for t in settled if t["pnl"] > 0)
        l = sum(1 for t in settled if t["pnl"] < 0)
        print(f"\nAll-time (held to settlement): {w}W / {l}L ({w/(w+l)*100:.0f}% hit rate) | Total P&L: ${total:+.2f}")
    if exited:
        print(f"Exited before settlement: {len(exited)} fills (P&L realized at exit)")


if __name__ == "__main__":
    run_settler()
