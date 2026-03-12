"""Weather Edge Daemon — continuous scanning + position management.

Runs in a loop:
  - Every 5 min: check positions against current market prices, exit bad ones
  - Every 5 min: scan for new entry opportunities (forecasts cached 30 min)
  - Forecast cache means only ~1 API burst per 30 min to Open-Meteo/NOAA

Usage: python daemon.py
"""

import sys
import time
import traceback
from datetime import datetime, timezone

from rich.console import Console
from weather import cache as fcache

console = Console()

LOOP_INTERVAL = 300  # 5 minutes


def run_cycle(cycle_num: int):
    """Run one scan + position management cycle."""
    now = datetime.now(timezone.utc)
    console.print(f"\n{'='*60}")
    console.print(f"[bold cyan]Cycle #{cycle_num} — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}[/bold cyan]")
    cache_info = fcache.stats()
    console.print(f"[dim]Forecast cache: {cache_info['active']} active entries[/dim]")

    # --- Phase 1: Position management (exits) ---
    console.print(f"\n[bold]Phase 1: Position Management[/bold]")
    try:
        from kalshi.position_manager import run_position_manager
        run_position_manager()
    except Exception as e:
        console.print(f"[red]Position manager error: {e}[/red]")
        traceback.print_exc()

    # --- Phase 2: New signal scan + entries ---
    console.print(f"\n[bold]Phase 2: Market Scan[/bold]")
    try:
        from scanner import run_scanner
        run_scanner()
    except Exception as e:
        console.print(f"[red]Scanner error: {e}[/red]")
        traceback.print_exc()

    # --- Phase 3: Quick balance check ---
    try:
        from kalshi.trader import get_balance
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        portfolio = bal.get("portfolio_value", 0) / 100.0
        console.print(f"\n[bold]Balance: ${cash:.2f} cash + ${portfolio:.2f} positions = ${cash + portfolio:.2f}[/bold]")
    except Exception as e:
        console.print(f"[dim]Balance check failed: {e}[/dim]")


def main():
    console.print("[bold green]Weather Edge Daemon starting...[/bold green]")
    console.print(f"Loop interval: {LOOP_INTERVAL}s ({LOOP_INTERVAL // 60} min)")
    console.print(f"Forecast cache TTL: {fcache.FORECAST_TTL}s ({fcache.FORECAST_TTL // 60} min)")
    console.print(f"Market cache TTL: {fcache.MARKET_TTL}s\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                run_cycle(cycle)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                console.print(f"\n[red]Cycle {cycle} failed: {e}[/red]")
                traceback.print_exc()

            # Sleep until next cycle
            next_run = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            console.print(f"\n[dim]Sleeping {LOOP_INTERVAL}s... next cycle at ~{next_run}[/dim]")
            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped by user.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
