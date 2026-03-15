import json
from datetime import datetime, timezone
from dashboard.scan_cache import init_scan_cache_db, write_scan_results, cleanup_old_scans
from rich.console import Console
from rich.table import Table
from config import CITIES, EDGE_THRESHOLD, SHOW_THRESHOLD, DUTCH_BOOK_THRESHOLD, ALERT_THRESHOLD, PAPER_MODE, CONFIDENCE_THRESHOLD
from weather.forecast import get_ensemble_max_temps, get_bucket_prob
from weather.multi_model import fuse_forecast, _get_liquidity_score
from polymarket.gamma import get_active_weather_markets, parse_bucket
from alerts.telegram_alert import send_signal_alert
from logging_utils import log_signal
from trading.trader import execute_signal
from kalshi.scanner import get_kalshi_weather_markets, parse_kalshi_bucket, get_kalshi_precip_markets, get_kalshi_price
from kalshi.trader import execute_kalshi_signal, reset_scan_budget
from weather.forecast_logger import log_forecast, parse_ticker_date
from weather.multi_model import fuse_precip_forecast
from weather.forecast import calculate_remaining_month_days
from config import MAX_ENSEMBLE_HORIZON_DAYS, MIN_VOLUME_24H, MIN_OPEN_INTEREST
from config import SAMEDAY_EDGE_THRESHOLD, SAMEDAY_CONFIDENCE_THRESHOLD, SAMEDAY_KELLY_FLOOR
from config import MIN_TRADE_EDGE, MAX_POSITIONS_TOTAL, SKIP_RAIN_MARKETS
from ercot.position_manager import run_ercot_manager
from ercot.hubs import scan_all_hubs
from ercot.paper_trader import open_position as ercot_open_position, get_open_positions as ercot_get_positions, get_paper_summary as ercot_summary, write_scan_cache as ercot_write_cache
from config import ERCOT_MIN_EDGE, ERCOT_MIN_CONFIDENCE, ERCOT_PAPER_BANKROLL

console = Console()


def _is_liquid(market: dict) -> bool:
    volume = float(market.get("volume_24h_fp", "0") or "0")
    oi = float(market.get("open_interest_fp", "0") or "0")
    return volume > MIN_VOLUME_24H or oi > MIN_OPEN_INTEREST


def _buckets_overlap(lo1, hi1, lo2, hi2) -> bool:
    a_lo = lo1 if lo1 is not None else -999
    a_hi = hi1 if hi1 is not None else 999
    b_lo = lo2 if lo2 is not None else -999
    b_hi = hi2 if hi2 is not None else 999
    return a_lo < b_hi + 2 and b_lo < a_hi + 2


def _get_recently_sold_tickers(cooldown_minutes: int = 60) -> set:
    """Return tickers sold within the cooldown window. Prevents re-entry churn."""
    try:
        import sqlite3
        from pathlib import Path
        db = Path("data/trades.db")
        if not db.exists():
            return set()
        conn = sqlite3.connect(str(db))
        cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=cooldown_minutes)).isoformat()
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM trades WHERE side LIKE 'sell_%' AND fill_time > ? AND fill_qty > 0",
            (cutoff,),
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _get_existing_positions() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        from kalshi.trader import get_positions, get_orders
        positions = get_positions()
        for p in positions:
            qty = int(abs(float(p.get("position_fp", "0"))))
            if qty > 0:
                result[p.get("ticker", "")] = qty
    except Exception:
        pass
    try:
        from kalshi.trader import get_orders
        resting = get_orders(status="resting")
        for o in resting:
            if o.get("action") == "buy":
                ticker = o.get("ticker", "")
                remaining = int(float(o.get("remaining_count_fp", "0") or "0"))
                if remaining > 0:
                    result[ticker] = result.get(ticker, 0) + remaining
    except Exception:
        pass
    return result


def run_scanner():
    console.print("[bold cyan]Weather Edge Scanner — Bidirectional Signals[/bold cyan]\n")
    reset_scan_budget()

    held_positions = _get_existing_positions()
    recently_sold = _get_recently_sold_tickers(cooldown_minutes=60)
    if len(held_positions) >= MAX_POSITIONS_TOTAL:
        console.print(f"[red]MAX POSITIONS REACHED ({len(held_positions)}/{MAX_POSITIONS_TOTAL}) — skipping all new trades this cycle[/red]")
        return
    if held_positions:
        console.print(f"[dim]Existing positions: {len(held_positions)}/{MAX_POSITIONS_TOTAL} tickers[/dim]")
    if recently_sold:
        console.print(f"[dim]Re-entry cooldown: {len(recently_sold)} tickers sold in last 60min[/dim]")

    console.print("Fetching weather markets from Gamma API...")
    markets = get_active_weather_markets()
    console.print(f"Found {len(markets)} weather-related markets\n")

    if not markets:
        console.print("[yellow]No weather markets found. Try again later or expand keywords.[/yellow]")
        return

    table = Table(title="Signal Opportunities (|edge| >= 7%)")
    table.add_column("Market", style="cyan", max_width=45)
    table.add_column("City", style="green")
    table.add_column("Model", justify="right")
    table.add_column("Market", justify="right")
    table.add_column("Edge", justify="right", style="bold")
    table.add_column("Conf", justify="right")
    table.add_column("Signal", style="bold")
    table.add_column("Note", style="dim")

    signals_found = 0

    for market in markets:
        q = market["question"]
        outcome_prices = market.get("outcomePrices", "")

        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                continue

        if not outcome_prices:
            continue

        yes_price = float(outcome_prices[0])

        q_lower = q.lower()
        city_key = None
        for key, data in CITIES.items():
            if any(kw in q_lower for kw in data["keywords"]):
                city_key = key
                break
        if not city_key:
            continue
        city = CITIES[city_key]
        unit = city.get("unit", "f")

        bucket = parse_bucket(q)
        if not bucket:
            console.print(f"[dim]Skipped (no bucket parse): {q}[/dim]")
            continue
        low, high = bucket

        try:
            temps = get_ensemble_max_temps(city["lat"], city["lon"], days_ahead=1, unit=unit)
        except Exception as e:
            console.print(f"[red]Forecast error for {city_key}: {e}[/red]")
            continue

        if not temps:
            continue

        model_prob = get_bucket_prob(temps, low, high)
        edge = model_prob - yes_price

        sum_prices = sum(float(p) for p in outcome_prices) if len(outcome_prices) > 1 else 1.0
        dutch = sum_prices < DUTCH_BOOK_THRESHOLD

        note = ""
        if dutch:
            note = f"DUTCH ({sum_prices:.3f})"

        if abs(edge) >= SHOW_THRESHOLD or dutch:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            arrow = "[green]^ BUY YES[/green]" if edge > 0 else "[red]v SELL YES[/red]"
            color = "green" if edge > 0 else "red"
            edge_str = f"[{color}]{edge:+.1%}[/{color}]"

            display_q = q[:42] + "..." if len(q) > 45 else q
            table.add_row(
                display_q,
                city_key.replace("_", " ").title(),
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                edge_str,
                "[dim]—[/dim]",
                arrow,
                note,
            )
            signals_found += 1

            log_signal(q, city_key, model_prob, yes_price, edge, direction, dutch, PAPER_MODE, confidence=0, ticker="")

    console.print("\nFetching weather markets from Kalshi API...")
    kalshi_markets = get_kalshi_weather_markets()
    console.print(f"Found {len(kalshi_markets)} Kalshi temp markets\n")

    month = datetime.now(timezone.utc).month
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    city_date_positions = {}

    tradeable_temp_signals = []
    all_displayed_temp_signals = []

    for market in kalshi_markets:
        title = market.get("title", "") + " " + market.get("subtitle", "")
        city_key = market["_city"]
        unit = market["_unit"]

        yes_price = get_kalshi_price(market)
        if yes_price is None:
            continue

        bucket = parse_kalshi_bucket(market)
        if not bucket:
            continue
        low, high = bucket

        ticker = market.get("ticker", "")
        target_date = parse_ticker_date(ticker)
        if target_date and target_date == today_str:
            days_ahead = 0
        elif target_date and target_date > today_str:
            from datetime import timedelta
            target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
            today_dt = datetime.now(timezone.utc).date()
            days_ahead = (target_dt - today_dt).days
        else:
            days_ahead = 1

        temp_type = market.get("_temp_type", "max")
        try:
            model_prob, confidence, details = fuse_forecast(
                market["_lat"], market["_lon"], city_key, month,
                low, high, days_ahead=days_ahead, unit=unit, temp_type=temp_type,
                liquidity_score=_get_liquidity_score(market),
            )
        except Exception as e:
            console.print(f"[red]Fusion error for {city_key}: {e}[/red]")
            continue

        ticker = market.get("ticker", "")
        target_date = parse_ticker_date(ticker)
        if target_date:
            for model_name in ["ensemble", "noaa", "hrrr"]:
                temp_val = details.get(model_name, {}).get("temp")
                if temp_val is not None:
                    log_forecast(city_key, target_date, model_name, temp_val, temp_type)

        edge = model_prob - yes_price

        # HARD RAIN SKIP + MIN EDGE FILTER
        if SKIP_RAIN_MARKETS and ("Rain in" in title.lower() or "precip" in title.lower()):
            continue

        if abs(edge) >= SHOW_THRESHOLD:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            arrow = "[green]^ BUY YES[/green]" if edge > 0 else "[red]v SELL YES[/red]"
            color = "green" if edge > 0 else "red"
            edge_str = f"[{color}]{edge:+.1%}[/{color}]"

            n_models = details.get("models_used", 1)
            conf_color = "green" if confidence >= CONFIDENCE_THRESHOLD else "yellow" if confidence >= 45 else "red"
            conf_str = f"[{conf_color}]{confidence:.1f}% ({n_models}m)[/{conf_color}]"

            display_q = title[:42] + "..." if len(title) > 45 else title
            type_label = "Lo" if temp_type == "min" else "Hi"
            table.add_row(
                display_q,
                f"{city_key.replace('_', ' ').title()} (K)",
                f"{model_prob:.1%}",
                f"{yes_price:.1%}",
                edge_str,
                conf_str,
                arrow,
                f"K-{type_label}",
            )
            signals_found += 1

            log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE, confidence=confidence, ticker=ticker)

            all_displayed_temp_signals.append({
                "market": market, "title": title, "city_key": city_key,
                "model_prob": model_prob, "yes_price": yes_price, "edge": edge,
                "direction": direction, "confidence": confidence,
                "n_models": n_models, "ticker": ticker, "target_date": target_date,
                "temp_type": temp_type, "low": low, "high": high,
                "is_sameday": (days_ahead == 0),
            })

            is_sameday = (days_ahead == 0)
            edge_gate = SAMEDAY_EDGE_THRESHOLD if is_sameday else MIN_TRADE_EDGE
            conf_gate = SAMEDAY_CONFIDENCE_THRESHOLD if is_sameday else CONFIDENCE_THRESHOLD
            kelly_floor = SAMEDAY_KELLY_FLOOR if is_sameday else None

            if abs(edge) >= edge_gate and confidence >= conf_gate and _is_liquid(market):
                rank_score = abs(edge) * confidence
                tradeable_temp_signals.append({
                    "market": market, "title": title, "city_key": city_key,
                    "model_prob": model_prob, "yes_price": yes_price, "edge": edge,
                    "direction": direction, "confidence": confidence,
                    "n_models": n_models, "ticker": ticker, "target_date": target_date,
                    "temp_type": temp_type, "low": low, "high": high,
                    "is_sameday": is_sameday, "kelly_floor": kelly_floor,
                    "rank_score": rank_score,
                })

    tradeable_temp_signals.sort(key=lambda s: s["rank_score"], reverse=True)
    if tradeable_temp_signals:
        console.print(f"\n[bold]Executing {len(tradeable_temp_signals)} temp signals (sorted by strength)[/bold]")

    for sig in tradeable_temp_signals:
        if len(held_positions) >= MAX_POSITIONS_TOTAL:
            console.print(f"[red]MAX POSITIONS REACHED ({len(held_positions)}/{MAX_POSITIONS_TOTAL}) — stopping execution[/red]")
            break

        if sig["ticker"] in recently_sold:
            console.print(f"[yellow]  COOLDOWN {sig['ticker']} — sold recently, skipping re-entry[/yellow]")
            continue

        if sig["ticker"] in held_positions:
            console.print(f"[dim]  SKIP {sig['ticker']} — already holding this ticker[/dim]")
            continue

        pos_key = (sig["city_key"], sig["target_date"] or "unknown", sig["temp_type"])
        side = "yes" if sig["edge"] > 0 else "no"
        existing = city_date_positions.get(pos_key, [])

        skip = False
        for prev_side, prev_bucket in existing:
            if prev_side != side:
                p_lo, p_hi = prev_bucket
                if _buckets_overlap(sig["low"], sig["high"], p_lo, p_hi):
                    skip = True
                    console.print(f"[dim]  Skipping {sig['ticker']} — conflicts with existing {prev_side.upper()} on {sig['city_key']}[/dim]")
                    break

        if not skip:
            city_date_positions.setdefault(pos_key, []).append((side, (sig["low"], sig["high"])))
            tag = "SAMEDAY " if sig["is_sameday"] else ""
            send_signal_alert(
                sig["title"], sig["city_key"] + " (Kalshi)", sig["model_prob"], sig["yes_price"], sig["edge"],
                f"{tag}{sig['direction']} (Conf {sig['confidence']:.1f}%, {sig['n_models']} models)"
            )
            execute_kalshi_signal(sig["market"], sig["city_key"], sig["model_prob"], sig["yes_price"], sig["edge"], sig["direction"], sig["confidence"], existing_contracts=held_positions.get(sig["ticker"], 0), kelly_floor=sig["kelly_floor"])

    precip_markets = get_kalshi_precip_markets()
    console.print(f"\n  Found {len(precip_markets)} precip markets")

    tradeable_precip_signals = []
    all_displayed_precip_signals = []

    for market in precip_markets:
        ticker = market.get("ticker", "")
        title = market.get("title", ticker)
        city_key = market.get("_city", "unknown")
        threshold = market.get("_threshold", 0.0)

        yes_price = get_kalshi_price(market)
        if yes_price is None:
            continue

        remaining_days = calculate_remaining_month_days()
        forecast_days = min(remaining_days, MAX_ENSEMBLE_HORIZON_DAYS)
        if forecast_days <= 0:
            continue

        blind_days = remaining_days - forecast_days

        month = datetime.now(timezone.utc).month

        try:
            model_prob, confidence, details = fuse_precip_forecast(
                market["_lat"], market["_lon"], city_key, month,
                threshold=threshold, forecast_days=forecast_days,
                liquidity_score=_get_liquidity_score(market),
            )
            if blind_days > 0:
                from weather.climate import estimate_blind_day_precip
                blind_expected, blind_std = estimate_blind_day_precip(
                    city_key, month, market["_lat"], market["_lon"], blind_days,
                )
                adjusted_threshold = max(0.0, threshold - blind_expected)
                if adjusted_threshold != threshold:
                    model_prob_adj, _, _ = fuse_precip_forecast(
                        market["_lat"], market["_lon"], city_key, month,
                        threshold=adjusted_threshold, forecast_days=forecast_days,
                    )
                    coverage = forecast_days / max(remaining_days, 1)
                    model_prob = model_prob_adj
                    confidence = confidence * coverage
                    details["blind_days"] = blind_days
                    details["blind_expected_inches"] = round(blind_expected, 2)
                    details["adjusted_threshold"] = round(adjusted_threshold, 2)
                    details["coverage"] = round(coverage, 2)
        except Exception as e:
            console.print(f"[red]Precip fusion error for {city_key}: {e}[/red]")
            continue

        edge = model_prob - yes_price

        if SKIP_RAIN_MARKETS:
            continue

        if abs(edge) >= SHOW_THRESHOLD:
            direction = "BUY YES" if edge > 0 else "SELL YES"
            edge_color = "green" if edge > 0 else "red"
            table.add_row(
                title[:40], city_key, f"{model_prob:.0%}", f"{yes_price:.0%}",
                f"[{edge_color}]{edge:+.1%}[/{edge_color}]",
                f"[bold]{direction}[/bold]", f"{confidence:.1f}%", ticker,
            )
            log_signal(title, city_key + " (kalshi)", model_prob, yes_price, edge, direction, False, PAPER_MODE, confidence=confidence, ticker=ticker)
            signals_found += 1

            all_displayed_precip_signals.append({
                "market": market, "city_key": city_key, "model_prob": model_prob,
                "yes_price": yes_price, "edge": edge, "direction": direction,
                "confidence": confidence, "ticker": ticker, "title": title,
            })

            if abs(edge) >= ALERT_THRESHOLD and confidence >= CONFIDENCE_THRESHOLD and _is_liquid(market):
                rank_score = abs(edge) * confidence
                tradeable_precip_signals.append({
                    "market": market, "city_key": city_key, "model_prob": model_prob,
                    "yes_price": yes_price, "edge": edge, "direction": direction,
                    "confidence": confidence, "ticker": ticker, "title": title,
                    "rank_score": rank_score,
                })

    tradeable_precip_signals.sort(key=lambda s: s["rank_score"], reverse=True)
    if tradeable_precip_signals:
        console.print(f"\n[bold]Executing {len(tradeable_precip_signals)} precip signals (sorted by strength)[/bold]")

    for sig in tradeable_precip_signals:
        if sig["ticker"] in recently_sold:
            console.print(f"[yellow]  COOLDOWN {sig['ticker']} — sold recently, skipping re-entry[/yellow]")
            continue
        execute_kalshi_signal(sig["market"], sig["city_key"], sig["model_prob"], sig["yes_price"], sig["edge"], sig["direction"], confidence=sig["confidence"], existing_contracts=held_positions.get(sig["ticker"], 0))

    # --- ERCOT Solar / Power Price Signal (5 hubs) ---
    console.print("\n[bold magenta]ERCOT Solar Signals[/bold magenta]")

    # 1. Manage existing positions first
    hub_signals = run_ercot_manager()
    if hub_signals is None:
        hub_signals = scan_all_hubs()

    # 2. Cache signals for dashboard
    ercot_write_cache(hub_signals)

    # 3. Display hub signal table
    ercot_table = Table(title="ERCOT Solar Signals (5 hubs)")
    ercot_table.add_column("Hub", style="cyan")
    ercot_table.add_column("City", style="green")
    ercot_table.add_column("Signal", style="bold")
    ercot_table.add_column("Edge", justify="right")
    ercot_table.add_column("Solrad", justify="right")
    ercot_table.add_column("Solar MW", justify="right")
    ercot_table.add_column("ERCOT$", justify="right")
    ercot_table.add_column("Conf", justify="right")
    ercot_table.add_column("Action", style="bold")

    existing_hubs = {p["hub"]: p for p in ercot_get_positions()}

    for sig in hub_signals:
        sig_color = {"SHORT": "red", "LONG": "green"}.get(sig["signal"], "dim")
        edge_str = f"[{sig_color}]{sig['edge']:+.2f}[/{sig_color}]"

        # Determine action
        action = "--"
        if abs(sig["edge"]) >= ERCOT_MIN_EDGE and sig["confidence"] >= ERCOT_MIN_CONFIDENCE:
            existing = existing_hubs.get(sig["hub"])
            if existing:
                if existing["signal"] == sig["signal"]:
                    action = f"HOLD (${existing['size_dollars']:.0f})"
                else:
                    action = "FLIP"
            else:
                action = "OPEN"

        ercot_table.add_row(
            sig["hub"], sig["city"],
            f"[{sig_color}]{sig['signal']}[/{sig_color}]",
            edge_str,
            f"{sig['expected_solrad_mjm2']:.1f}",
            f"{sig['actual_solar_mw']:.0f}",
            f"${sig['current_ercot_price']:.1f}",
            f"{sig['confidence']}%",
            action,
        )

        # 4. Open new positions / handle flips
        if abs(sig["edge"]) >= ERCOT_MIN_EDGE and sig["confidence"] >= ERCOT_MIN_CONFIDENCE:
            existing = existing_hubs.get(sig["hub"])
            if existing and existing["signal"] != sig["signal"]:
                # Flip: close existing, open new
                from ercot.paper_trader import close_position as ercot_close
                ercot_close(existing["id"], sig["current_ercot_price"], sig["signal"], "signal flipped")
                ercot_open_position(sig, bankroll=ERCOT_PAPER_BANKROLL)
            elif not existing:
                ercot_open_position(sig, bankroll=ERCOT_PAPER_BANKROLL)

    console.print(ercot_table)

    # 5. Paper P&L summary
    summary = ercot_summary()
    console.print(
        f"\n[bold magenta]ERCOT Paper:[/bold magenta] "
        f"{summary['open_count']} open (${summary['open_exposure']:.0f} exposure) | "
        f"Closed: {summary['wins']}W/{summary['losses']}L | "
        f"Net P&L: ${summary['total_pnl']:+.2f}"
    )

    try:
        from dashboard.ticker_map import ticker_to_city
        init_scan_cache_db()
        cache_rows = []
        for sig in all_displayed_temp_signals:
            ticker = sig["ticker"]
            low = sig["low"]
            high = sig["high"]
            bucket_str = f"{low:.0f}-{high:.0f}" if high is not None else f">{low:.0f}"
            cache_rows.append({
                "market_type": "temp",
                "ticker": ticker,
                "city": ticker_to_city(ticker),
                "model_prob": sig["model_prob"],
                "market_price": sig["yes_price"],
                "edge": sig["edge"],
                "direction": sig["direction"],
                "confidence": sig["confidence"],
                "method": sig["temp_type"],
                "threshold": bucket_str,
                "days_left": 0 if sig["is_sameday"] else 1,
            })
        for sig in all_displayed_precip_signals:
            ticker = sig["ticker"]
            raw_threshold = sig["market"].get("_threshold", "")
            threshold_str = str(raw_threshold) if raw_threshold != "" else ""
            cache_rows.append({
                "market_type": "precip",
                "ticker": ticker,
                "city": ticker_to_city(ticker),
                "model_prob": sig["model_prob"],
                "market_price": sig["yes_price"],
                "edge": sig["edge"],
                "direction": sig["direction"],
                "confidence": sig["confidence"],
                "method": "",
                "threshold": threshold_str,
                "days_left": None,
            })
        if cache_rows:
            write_scan_results(cache_rows)
            cleanup_old_scans(days=30)
            console.print(f"  [Cache] Wrote {len(cache_rows)} scan results to cache")
    except Exception as e:
        console.print(f"  [Cache] Error writing scan cache: {e}")

    from kalshi.trader import poll_and_update_fills
    poll_and_update_fills()

    if signals_found:
        console.print(table)
    else:
        console.print("[yellow]No signals >= 7% edge this scan.[/yellow]")

    total_markets = len(markets) + len(kalshi_markets) + len(precip_markets)
    console.print(f"\n[dim]Scanned {total_markets} markets (Polymarket: {len(markets)}, Kalshi temp: {len(kalshi_markets)}, Kalshi precip: {len(precip_markets)}), {signals_found} signals found.[/dim]")
    console.print(f"[dim]Signals >= 10.5% are trade-worthy. Run every 15 min.[/dim]")