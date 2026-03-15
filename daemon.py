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
from dashboard.equity_db import init_equity_db, record_equity_snapshot
from config import PAPER_MODE
from exchanges.kalshi import KalshiExchange
from exchanges.ercot import ErcotExchange
from pipeline.config import ALL_CONFIGS
from pipeline.runner import PipelineRunner

console = Console()

LOOP_INTERVAL = 300  # 5 minutes
_last_equity_date: str | None = None  # track date to record equity once per day


def run_cycle(cycle_num: int, runner: PipelineRunner, exchanges: dict):
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
    console.print(f"\n[bold]Phase 2: Market Scan (Pipeline)[/bold]")
    try:
        runner.run_cycle(paper_mode=PAPER_MODE)
    except Exception as e:
        console.print(f"[red]Pipeline error: {e}[/red]")
        traceback.print_exc()

    # --- Phase 3: Settle resolved markets ---
    console.print(f"\n[bold]Phase 3: Settlement[/bold]")
    try:
        from kalshi.settler import run_settler
        run_settler()
    except Exception as e:
        console.print(f"[red]Settler error: {e}[/red]")
        traceback.print_exc()

    # --- Phase 3.5: Analytics ---
    try:
        from config import ANALYTICS_ENABLED
        if ANALYTICS_ENABLED:
            console.print(f"\n[bold]Phase 3.5: Analytics[/bold]")
            from analytics.optimizer import run_analytics
            run_analytics()
    except Exception as e:
        console.print(f"  [red]Analytics error: {e}[/red]")

    # --- Phase 4: Quick balance check + daily equity snapshot ---
    global _last_equity_date
    kalshi = exchanges.get("kalshi")
    if not kalshi:
        console.print(f"[dim]No Kalshi exchange configured — skipping balance check[/dim]")
        return
    try:
        bal = kalshi.get_balance()
        cash = bal.get("balance", 0) / 100.0
        portfolio = bal.get("portfolio_value", 0) / 100.0
        console.print(f"\n[bold]Balance: ${cash:.2f} cash + ${portfolio:.2f} positions = ${cash + portfolio:.2f}[/bold]")

        # Record equity snapshot once per day (first cycle of each day)
        today_str = now.strftime("%Y-%m-%d")
        if _last_equity_date != today_str:
            try:
                settled = kalshi.get_positions(limit=200, settlement_status="settled")
                realized_pnl = sum(float(p.get("realized_pnl_dollars", "0")) for p in settled)
                fees_paid = sum(float(p.get("fees_paid_dollars", "0")) for p in settled)
                wins = sum(1 for p in settled if float(p.get("realized_pnl_dollars", "0")) > 0)
                losses = sum(1 for p in settled if float(p.get("realized_pnl_dollars", "0")) < 0)
                init_equity_db()
                record_equity_snapshot(
                    date=today_str,
                    total_equity=cash + portfolio,
                    cash=cash,
                    portfolio_value=portfolio,
                    realized_pnl=realized_pnl,
                    fees_paid=fees_paid,
                    win_count=wins,
                    loss_count=losses,
                )
                _last_equity_date = today_str
                console.print(f"  [Equity] Recorded daily snapshot for {today_str}")
            except Exception as e:
                console.print(f"  [Equity] Snapshot error: {e}")
    except Exception as e:
        console.print(f"[dim]Balance check failed: {e}[/dim]")


def main():
    console.print("[bold green]Weather Edge Daemon starting...[/bold green]")
    console.print(f"Loop interval: {LOOP_INTERVAL}s ({LOOP_INTERVAL // 60} min)")
    console.print(f"Forecast cache TTL: {fcache.FORECAST_TTL}s ({fcache.FORECAST_TTL // 60} min)")
    console.print(f"Market cache TTL: {fcache.MARKET_TTL}s")
    console.print(f"Paper mode: {PAPER_MODE}\n")

    # Create exchange adapters and pipeline runner once
    exchanges = {
        "kalshi": KalshiExchange(),
        "ercot": ErcotExchange(),
    }
    runner = PipelineRunner(configs=ALL_CONFIGS, exchanges=exchanges)
    console.print(f"Pipeline: {len(ALL_CONFIGS)} configs, {len(exchanges)} exchanges\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                run_cycle(cycle, runner=runner, exchanges=exchanges)
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
