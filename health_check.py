"""System Health Check — verifies the trading system is running correctly AND sensibly.

Two modes:
  - CLI: python -m health_check (prints report, exits 1 if critical issues)
  - API: imported by dashboard/api.py as /api/health endpoint

Checks three layers:
  1. Infrastructure — are services running, APIs responding?
  2. Data freshness — are scans/fills/settlements actually happening?
  3. Sanity — is the system doing something dumb?

Usage:
  python -m health_check           # full report
  python -m health_check --alert   # only send Telegram if critical issues found
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

TRADES_DB = "data/trades.db"
SCAN_CACHE_DB = "data/scan_cache.db"
BIAS_DB = "data/bias.db"


def _db_query(db_path: str, sql: str, params=()) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def check_infrastructure() -> list[dict]:
    """Check APIs and databases exist."""
    checks = []

    # Kalshi API
    try:
        from kalshi.trader import get_balance
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        portfolio = bal.get("portfolio_value", 0) / 100.0
        total = cash + portfolio
        checks.append({"name": "kalshi_api", "status": "ok", "detail": f"${total:.2f} ({cash:.2f} cash + {portfolio:.2f} positions)"})
        if total < 50:
            checks.append({"name": "low_balance", "status": "warn", "detail": f"Balance ${total:.2f} is below $50"})
    except Exception as e:
        checks.append({"name": "kalshi_api", "status": "critical", "detail": str(e)})

    # Databases exist
    for db, label in [(TRADES_DB, "trades_db"), (SCAN_CACHE_DB, "scan_cache_db"), (BIAS_DB, "bias_db")]:
        if os.path.exists(db):
            size = os.path.getsize(db)
            checks.append({"name": label, "status": "ok", "detail": f"{size / 1024:.0f} KB"})
        else:
            checks.append({"name": label, "status": "critical", "detail": "missing"})

    return checks


def check_data_freshness() -> list[dict]:
    """Check that scans, fills, and settlements are recent."""
    checks = []
    now = datetime.now(timezone.utc)

    # Recent fills
    recent_fills = _db_query(TRADES_DB, "SELECT COUNT(*) as cnt FROM trades WHERE fill_time > ?", (_hours_ago(6),))
    fill_count = recent_fills[0]["cnt"] if recent_fills else 0
    if fill_count > 0:
        checks.append({"name": "recent_fills", "status": "ok", "detail": f"{fill_count} fills in last 6h"})
    else:
        checks.append({"name": "recent_fills", "status": "warn", "detail": "no fills in last 6h"})

    # Recent settlements
    recent_settled = _db_query(TRADES_DB, "SELECT COUNT(*) as cnt FROM trades WHERE settlement_outcome IS NOT NULL")
    settled_count = recent_settled[0]["cnt"] if recent_settled else 0
    unresolved = _db_query(TRADES_DB, "SELECT COUNT(*) as cnt FROM trades WHERE settlement_outcome IS NULL")
    unresolved_count = unresolved[0]["cnt"] if unresolved else 0
    if settled_count > 0:
        checks.append({"name": "settlements", "status": "ok", "detail": f"{settled_count} settled, {unresolved_count} pending"})
    else:
        checks.append({"name": "settlements", "status": "warn", "detail": f"0 settled, {unresolved_count} pending — settler may not be running"})

    # Recent scan cache
    recent_scans = _db_query(SCAN_CACHE_DB, "SELECT MAX(scan_time) as latest FROM scan_results")
    if recent_scans and recent_scans[0]["latest"]:
        latest = recent_scans[0]["latest"]
        checks.append({"name": "last_scan", "status": "ok", "detail": latest})
    else:
        checks.append({"name": "last_scan", "status": "warn", "detail": "no scan data in cache"})

    return checks


def check_sanity() -> list[dict]:
    """The 'is it doing something dumb' checks."""
    checks = []

    # --- Check 1: Buying and selling the same ticker at a loss ---
    # Find tickers where we bought NO at high price and sold NO at low price (same day)
    round_trips = _db_query(TRADES_DB, """
        SELECT b.ticker,
               b.side as buy_side, b.fill_price as buy_price, b.fill_qty as buy_qty,
               s.side as sell_side, s.fill_price as sell_price, s.fill_qty as sell_qty,
               b.fill_time
        FROM trades b
        JOIN trades s ON b.ticker = s.ticker
        WHERE b.side IN ('buy_yes', 'buy_no')
          AND s.side IN ('sell_yes', 'sell_no', 'yes', 'no')
          AND s.fill_time > b.fill_time
          AND b.fill_price > 0 AND s.fill_price > 0
          AND s.fill_price < b.fill_price * 0.5
        ORDER BY b.fill_time DESC
        LIMIT 20
    """)
    if round_trips:
        worst = round_trips[0]
        checks.append({
            "name": "panic_sells",
            "status": "critical" if len(round_trips) > 5 else "warn",
            "detail": f"{len(round_trips)} positions sold at >50% loss vs entry. "
                      f"Worst: {worst['ticker']} bought at {worst['buy_price']}c, sold at {worst['sell_price']}c"
        })
    else:
        checks.append({"name": "panic_sells", "status": "ok", "detail": "no panic sells detected"})

    # --- Check 2: Penny bets (low expected value after fees) ---
    penny_bets = _db_query(TRADES_DB, """
        SELECT COUNT(*) as cnt, SUM(fill_qty) as total_qty
        FROM trades
        WHERE side IN ('buy_yes', 'buy_no')
          AND fill_price <= 5
          AND fill_time > ?
    """, (_hours_ago(24),))
    penny_count = penny_bets[0]["cnt"] if penny_bets and penny_bets[0]["cnt"] else 0
    penny_qty = penny_bets[0]["total_qty"] if penny_bets and penny_bets[0]["total_qty"] else 0
    if penny_count > 5:
        checks.append({
            "name": "penny_bets",
            "status": "warn",
            "detail": f"{penny_count} orders at <=5c in last 24h ({penny_qty} contracts) — fees likely exceed expected value"
        })
    else:
        checks.append({"name": "penny_bets", "status": "ok", "detail": f"{penny_count} penny bets in last 24h"})

    # --- Check 3: Overall P&L trend ---
    pnl_data = _db_query(TRADES_DB, """
        SELECT settlement_outcome, pnl
        FROM trades
        WHERE settlement_outcome IS NOT NULL
    """)
    if pnl_data:
        wins = sum(1 for t in pnl_data if t["pnl"] and t["pnl"] > 0)
        losses = sum(1 for t in pnl_data if t["pnl"] and t["pnl"] < 0)
        total_pnl = sum(t["pnl"] for t in pnl_data if t["pnl"])
        hit_rate = wins / max(wins + losses, 1)
        checks.append({
            "name": "pnl_summary",
            "status": "ok" if total_pnl >= 0 else "warn" if total_pnl > -50 else "critical",
            "detail": f"{wins}W/{losses}L ({hit_rate:.0%} hit rate), P&L: ${total_pnl:+.2f}"
        })
    else:
        checks.append({"name": "pnl_summary", "status": "warn", "detail": "no settled trades yet"})

    # --- Check 4: Fee drag ---
    all_fills = _db_query(TRADES_DB, """
        SELECT SUM(fill_qty) as total_qty, COUNT(*) as total_orders
        FROM trades
        WHERE side IN ('buy_yes', 'buy_no')
    """)
    if all_fills and all_fills[0]["total_qty"]:
        total_qty = all_fills[0]["total_qty"]
        total_orders = all_fills[0]["total_orders"]
        est_fees = total_orders * 0.035 + total_qty * 0.01
        if pnl_data:
            total_pnl = sum(t["pnl"] for t in pnl_data if t["pnl"])
            if est_fees > abs(total_pnl) * 2 and est_fees > 5:
                checks.append({
                    "name": "fee_drag",
                    "status": "warn",
                    "detail": f"Est. fees ${est_fees:.2f} are {est_fees / max(abs(total_pnl), 0.01):.1f}x gross P&L — fees dominating economics"
                })
            else:
                checks.append({"name": "fee_drag", "status": "ok", "detail": f"Est. fees ${est_fees:.2f}"})
        else:
            checks.append({"name": "fee_drag", "status": "ok", "detail": f"Est. fees ${est_fees:.2f} (no settled P&L to compare)"})

    # --- Check 5: Position concentration ---
    try:
        from kalshi.trader import get_positions
        positions = get_positions()
        held = [p for p in positions if float(p.get("position_fp", 0)) != 0]
        if len(held) > 15:
            checks.append({
                "name": "position_count",
                "status": "warn",
                "detail": f"{len(held)} open positions — may be over-diversified, spreading capital too thin"
            })
        else:
            checks.append({"name": "position_count", "status": "ok", "detail": f"{len(held)} open positions"})
    except Exception:
        checks.append({"name": "position_count", "status": "warn", "detail": "could not fetch positions"})

    # --- Check 6: Same-day positions being sold instead of settled ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d").replace("-", "")[-4:]  # MMDDD format varies
    sameday_exits = _db_query(TRADES_DB, """
        SELECT COUNT(*) as cnt
        FROM trades
        WHERE side IN ('sell_yes', 'sell_no')
          AND fill_time > ?
          AND ticker LIKE '%' || ? || '%'
    """, (_hours_ago(12), datetime.now(timezone.utc).strftime("%d")))
    # This is approximate — real check would parse ticker dates

    return checks


def run_health_check() -> dict:
    """Run all checks and return structured report."""
    infra = check_infrastructure()
    freshness = check_data_freshness()
    sanity = check_sanity()

    all_checks = infra + freshness + sanity
    critical = [c for c in all_checks if c["status"] == "critical"]
    warnings = [c for c in all_checks if c["status"] == "warn"]

    overall = "critical" if critical else "warn" if warnings else "healthy"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "checks": {
            "infrastructure": infra,
            "data_freshness": freshness,
            "sanity": sanity,
        },
    }


def send_alert(report: dict):
    """Send Telegram alert if critical issues found."""
    if report["overall"] == "healthy":
        return

    try:
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return

        import requests

        lines = [f"HEALTH CHECK: {report['overall'].upper()}"]
        lines.append(f"{report['critical_count']} critical, {report['warning_count']} warnings\n")

        for section, checks in report["checks"].items():
            problems = [c for c in checks if c["status"] != "ok"]
            for c in problems:
                icon = "X" if c["status"] == "critical" else "!"
                lines.append(f"[{icon}] {c['name']}: {c['detail']}")

        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Alert send failed: {e}")


def print_report(report: dict):
    """Pretty-print the health report."""
    status_icons = {"ok": "+", "warn": "!", "critical": "X"}

    print(f"\n=== SYSTEM HEALTH CHECK — {report['timestamp'][:19]} UTC ===")
    print(f"Overall: {report['overall'].upper()}\n")

    for section, checks in report["checks"].items():
        print(f"--- {section.replace('_', ' ').title()} ---")
        for c in checks:
            icon = status_icons.get(c["status"], "?")
            print(f"  [{icon}] {c['name']}: {c['detail']}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert", action="store_true", help="Send Telegram alert if issues found")
    args = parser.parse_args()

    report = run_health_check()

    if args.alert:
        send_alert(report)
    else:
        print_report(report)

    sys.exit(1 if report["overall"] == "critical" else 0)
