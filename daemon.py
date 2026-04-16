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
from pipeline.config import ALL_CONFIGS
from pipeline.runner import PipelineRunner

console = Console()

LOOP_INTERVAL = 300  # 5 minutes
_last_equity_date: str | None = None  # track date to record equity once per day


def _write_dashboard_forecasts():
    """Fetch forecast data and write to scan_cache.db for dashboard consumption.

    Runs once per daemon cycle. If any individual city fails, it's skipped
    and previous data stays in the table.
    """
    import requests as _req
    from statistics import median
    from datetime import date
    import calendar
    from kalshi.scanner import WEATHER_SERIES, PRECIP_SERIES
    from weather.forecast import get_ensemble_precip, get_observed_mtd_precip
    from dashboard.scan_cache import write_city_forecasts

    rows = []

    # Temperature cities — fetch from local Open-Meteo
    for prefix, info in WEATHER_SERIES.items():
        city_name = info["city"]
        if any(r["city"] == city_name for r in rows):
            continue
        row = {"city": city_name, "unit": info.get("unit", "f"),
               "forecast_high_today": None, "forecast_high_tomorrow": None,
               "forecast_low_today": None, "forecast_low_tomorrow": None,
               "current_temp": None, "mtd_precip_inches": None,
               "forecast_precip_total": None}
        try:
            unit_param = "fahrenheit" if info.get("unit", "f") == "f" else "celsius"
            r = _req.get(
                f"http://localhost:8080/v1/forecast?latitude={info['lat']}&longitude={info['lon']}"
                f"&daily=temperature_2m_max,temperature_2m_min"
                f"&hourly=temperature_2m"
                f"&models=gfs_seamless&temperature_unit={unit_param}&timezone=auto&forecast_days=3",
                timeout=5,
            )
            d = r.json()
            daily = d.get("daily", {})
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            hourly_temps = d.get("hourly", {}).get("temperature_2m", [])
            row["forecast_high_today"] = highs[0] if len(highs) > 0 else None
            row["forecast_high_tomorrow"] = highs[1] if len(highs) > 1 else None
            row["forecast_low_today"] = lows[0] if len(lows) > 0 else None
            row["forecast_low_tomorrow"] = lows[1] if len(lows) > 1 else None
            row["current_temp"] = hourly_temps[-1] if hourly_temps else None
        except Exception:
            pass  # keep Nones — previous data stays in DB
        rows.append(row)

    # Precip cities — fetch MTD + ensemble
    today = date.today()
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    for prefix, info in PRECIP_SERIES.items():
        city_name = info["city"]
        # Find existing row or create new one
        existing = next((r for r in rows if r["city"] == city_name), None)
        if existing is None:
            existing = {"city": city_name, "unit": info.get("unit", "f"),
                        "forecast_high_today": None, "forecast_high_tomorrow": None,
                        "forecast_low_today": None, "forecast_low_tomorrow": None,
                        "current_temp": None, "mtd_precip_inches": None,
                        "forecast_precip_total": None}
            rows.append(existing)
        try:
            mtd = get_observed_mtd_precip(info["lat"], info["lon"])
            if mtd is not None:
                existing["mtd_precip_inches"] = round(mtd, 2)
        except Exception:
            pass
        try:
            members = get_ensemble_precip(info["lat"], info["lon"], forecast_days=days_left)
            if members:
                remaining = median(members)
                mtd_val = existing.get("mtd_precip_inches") or 0.0
                existing["forecast_precip_total"] = round(mtd_val + remaining, 2)
        except Exception:
            pass

    if rows:
        write_city_forecasts(rows)


def run_cycle(cycle_num: int, runner: PipelineRunner, exchanges: dict):
    """Run one scan + position management cycle."""
    now = datetime.now(timezone.utc)
    console.print(f"\n{'='*60}")
    console.print(f"[bold cyan]Cycle #{cycle_num} — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}[/bold cyan]")
    cache_info = fcache.stats()
    console.print(f"[dim]Forecast cache: {cache_info['active']} active entries[/dim]")

    # --- Phase 0: Sanity checks ---
    from alerts.telegram_alert import send_alert
    try:
        from weather.sanity import run_bias_check
        bias_warnings = run_bias_check()
        for w in bias_warnings:
            console.print(f"  [bold red]{w}[/bold red]")
            send_alert("Bias Sanity Failed", w, dedup_key="bias_sanity")
    except Exception as e:
        console.print(f"  [dim]Sanity check error: {e}[/dim]")

    # --- Phase 1: Position management (exits) ---

    console.print(f"\n[bold]Phase 1: Position Management[/bold]")
    try:
        from kalshi.position_manager import run_position_manager
        run_position_manager()
    except Exception as e:
        console.print(f"[red]Position manager error: {e}[/red]")
        traceback.print_exc()
        send_alert("Position Manager Failed", str(e), dedup_key="pos_mgr_error")

    # --- Phase 1.5: Stale order cleanup ---
    kalshi = exchanges.get("kalshi")
    if kalshi:
        try:
            from kalshi.order_cleanup import cleanup_stale_orders
            cancelled = cleanup_stale_orders(kalshi)
            if cancelled:
                console.print(f"  Cancelled {len(cancelled)} stale order(s)")
        except Exception as e:
            console.print(f"  [red]Order cleanup error: {e}[/red]")

    # --- Phase 2: New signal scan + entries ---
    console.print(f"\n[bold]Phase 2: Market Scan (Pipeline)[/bold]")
    try:
        runner.run_cycle(paper_mode=PAPER_MODE)
    except Exception as e:
        console.print(f"[red]Pipeline error: {e}[/red]")
        traceback.print_exc()
        send_alert("Pipeline Scan Failed", str(e), dedup_key="pipeline_error")

    # --- Phase 2.5: Cache forecast data for dashboard ---
    try:
        _write_dashboard_forecasts()
    except Exception as e:
        console.print(f"  [dim]Forecast cache write failed: {e}[/dim]")

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
