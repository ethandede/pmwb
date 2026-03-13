"""Price Watcher — fast polling loop that detects big price moves between scans.

Polls prices every 60 seconds for:
  - All open positions (protect existing bets)
  - Markets we recently scanned with edge (catch opportunities)

When a price moves >MOVE_THRESHOLD since last check, triggers a full
scanner + position manager cycle immediately.

Usage: python -m kalshi.price_watcher
"""

import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, Optional

from rich.console import Console

from kalshi.trader import get_positions, get_balance
from kalshi.scanner import (
    get_kalshi_weather_markets,
    get_kalshi_precip_markets,
    get_kalshi_price,
    WEATHER_SERIES,
    PRECIP_SERIES,
)
from config import MIN_VOLUME_24H, MIN_OPEN_INTEREST

console = Console()

# --- Configuration ---
POLL_INTERVAL = 60       # seconds between price checks
MOVE_THRESHOLD = 0.10    # 10¢ move triggers a full cycle
POSITION_MOVE = 0.07     # 7¢ move on a position we hold triggers cycle
MAX_TICKERS = 80         # max tickers to watch (API budget)


def _fetch_position_tickers() -> set[str]:
    """Get tickers for all open positions."""
    try:
        positions = get_positions()
        return {
            p.get("ticker", "")
            for p in positions
            if float(p.get("position_fp", "0")) != 0
        }
    except Exception:
        return set()


def _fetch_market_prices() -> Dict[str, float]:
    """Fetch current prices for all active weather markets.

    Returns {ticker: price} where price is a float in [0, 1].
    """
    prices = {}
    try:
        for m in get_kalshi_weather_markets():
            ticker = m.get("ticker", "")
            price = get_kalshi_price(m)
            if ticker and price is not None:
                prices[ticker] = price
    except Exception as e:
        console.print(f"[dim]Temp market fetch error: {e}[/dim]")

    try:
        for m in get_kalshi_precip_markets():
            ticker = m.get("ticker", "")
            price = get_kalshi_price(m)
            if ticker and price is not None:
                prices[ticker] = price
    except Exception as e:
        console.print(f"[dim]Precip market fetch error: {e}[/dim]")

    return prices


def _run_full_cycle(reason: str):
    """Trigger a full position management + scanner cycle."""
    console.print(f"\n[bold yellow]>>> TRIGGERED: {reason}[/bold yellow]")
    console.print(f"[bold]Running full cycle...[/bold]\n")

    try:
        from kalshi.position_manager import run_position_manager
        run_position_manager()
    except Exception as e:
        console.print(f"[red]Position manager error: {e}[/red]")
        traceback.print_exc()

    try:
        from scanner import run_scanner
        run_scanner()
    except Exception as e:
        console.print(f"[red]Scanner error: {e}[/red]")
        traceback.print_exc()

    try:
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        portfolio = bal.get("portfolio_value", 0) / 100.0
        console.print(f"\n[bold]Balance: ${cash:.2f} cash + ${portfolio:.2f} positions = ${cash + portfolio:.2f}[/bold]")
    except Exception:
        pass


def run_watcher():
    """Main price watching loop."""
    console.print("[bold green]Price Watcher starting...[/bold green]")
    console.print(f"  Poll interval: {POLL_INTERVAL}s")
    console.print(f"  Move threshold: {MOVE_THRESHOLD*100:.0f}¢ (general) / {POSITION_MOVE*100:.0f}¢ (positions)")
    console.print()

    last_prices: Dict[str, float] = {}
    held_tickers: set[str] = set()
    cooldown_until: float = 0  # don't re-trigger within 90s of last trigger

    poll = 0
    while True:
        poll += 1
        now = datetime.now(timezone.utc)

        try:
            # Refresh which tickers we hold every poll
            held_tickers = _fetch_position_tickers()

            # Fetch all current prices
            current_prices = _fetch_market_prices()

            if not last_prices:
                # First poll — just record baseline
                last_prices = current_prices.copy()
                console.print(f"[dim]{now.strftime('%H:%M:%S')} Baseline set: {len(current_prices)} markets[/dim]")
                time.sleep(POLL_INTERVAL)
                continue

            # Check for big moves
            triggered = False
            trigger_reason = ""

            for ticker, price in current_prices.items():
                prev = last_prices.get(ticker)
                if prev is None:
                    continue

                move = abs(price - prev)
                threshold = POSITION_MOVE if ticker in held_tickers else MOVE_THRESHOLD

                if move >= threshold:
                    direction = "UP" if price > prev else "DOWN"
                    held_tag = " [HELD]" if ticker in held_tickers else ""
                    console.print(
                        f"[bold {'green' if direction == 'UP' else 'red'}]"
                        f"  {ticker}: {prev*100:.0f}¢ → {price*100:.0f}¢ "
                        f"({direction} {move*100:.0f}¢){held_tag}"
                        f"[/bold {'green' if direction == 'UP' else 'red'}]"
                    )

                    if not triggered:
                        trigger_reason = f"{ticker} moved {move*100:.0f}¢ {direction}{held_tag}"
                        triggered = True

            # Update baseline
            last_prices = current_prices.copy()

            if triggered and time.time() > cooldown_until:
                _run_full_cycle(trigger_reason)
                cooldown_until = time.time() + 90  # 90s cooldown
            elif not triggered and poll % 5 == 0:
                # Quiet heartbeat every 5 polls
                console.print(
                    f"[dim]{now.strftime('%H:%M:%S')} "
                    f"Watching {len(current_prices)} markets, "
                    f"{len(held_tickers)} positions — no big moves[/dim]"
                )

        except KeyboardInterrupt:
            raise
        except Exception as e:
            console.print(f"[red]Poll {poll} error: {e}[/red]")
            traceback.print_exc()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        run_watcher()
    except KeyboardInterrupt:
        console.print("\n[yellow]Price Watcher stopped.[/yellow]")
        sys.exit(0)
