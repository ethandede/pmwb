#!/usr/bin/env python3
"""
Daily P&L Summary for Weather Edge Bot
Runs in ~4 seconds. Prints summary + sends via Telegram.
Add to cron: 0 8 * * * cd /path/to/polymarket-weather-bot && python scripts/daily_pnl_summary.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
from datetime import datetime, timezone
from kalshi.trader import get_balance, get_positions
from alerts.telegram_alert import send_signal_alert
from config import PAPER_MODE
from dashboard.equity_db import init_equity_db, record_equity_snapshot

DB_PATH = "data/trades.db"


def get_realized_pnl():
    """Sum realized P&L from settled trades in trades.db."""
    if not Path(DB_PATH).exists():
        return 0.0, 0, 0.0

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            SUM(CASE
                WHEN side LIKE '%yes%' THEN fill_qty * (fill_price - limit_price) / 100.0
                WHEN side LIKE '%no%' THEN fill_qty * (limit_price - fill_price) / 100.0
                ELSE 0
            END) as pnl,
            COUNT(*) as trades,
            SUM(fill_qty) as total_contracts
        FROM trades
        WHERE fill_qty > 0 AND fill_price > 0
    """)
    row = cursor.fetchone()
    conn.close()

    pnl = float(row[0]) if row and row[0] else 0.0
    trades = int(row[1]) if row else 0
    contracts = float(row[2]) if row and row[2] else 0.0
    return pnl, trades, contracts


def get_unrealized_and_positions():
    """Current open positions value (mark-to-market)."""
    try:
        positions = get_positions(settlement_status="unsettled")
        total_value = 0.0
        open_count = len(positions)
        for p in positions:
            qty = float(p.get("position_fp", 0))
            price = float(p.get("last_price_dollars", 0.50))
            total_value += qty * price
        return open_count, round(total_value, 2)
    except Exception:
        return 0, 0.0


def main():
    print(f"\n=== Weather Edge Daily P&L — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1. Bankroll
    try:
        bal = get_balance()
        cash = bal.get("balance", 0) / 100.0
        portfolio = bal.get("portfolio_value", 0) / 100.0
        total_equity = round(cash + portfolio, 2)
        print(f"Total Equity   : ${total_equity:,.2f} (Cash ${cash:,.2f} | Positions ${portfolio:,.2f})")
    except Exception:
        total_equity = 0.0
        cash = 0.0
        portfolio = 0.0
        print("Bankroll sync failed — using last known")

    # 2. Realized P&L
    realized, trades, contracts = get_realized_pnl()
    print(f"Realized P&L   : ${realized:,.2f} ({trades} trades, {contracts:.0f} contracts)")

    # 3. Unrealized
    open_count, unrealized = get_unrealized_and_positions()
    print(f"Unrealized     : ${unrealized:,.2f} ({open_count} open positions)")

    # 4. Total
    total_pnl = round(realized + unrealized, 2)
    print(f"**Total P&L**  : ${total_pnl:,.2f}")

    # Record equity snapshot
    try:
        init_equity_db()
        settled_positions = get_positions(settlement_status="settled")
        wins = sum(1 for p in settled_positions if float(p.get("realized_pnl_dollars", "0")) > 0)
        losses = sum(1 for p in settled_positions if float(p.get("realized_pnl_dollars", "0")) < 0)
        settled_fees = sum(float(p.get("fees_paid_dollars", "0")) for p in settled_positions)
        record_equity_snapshot(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_equity=total_equity,
            cash=cash,
            portfolio_value=portfolio,
            realized_pnl=realized,
            fees_paid=settled_fees,
            win_count=wins,
            loss_count=losses,
        )
        print("Equity snapshot recorded.")
    except Exception as e:
        print(f"Equity snapshot failed: {e}")

    # Telegram summary
    message = (
        f"*Weather Edge Daily Summary — {datetime.now().strftime('%b %d')}*\n\n"
        f"**Total Equity:** ${total_equity:,.2f}\n"
        f"**Realized P&L:** ${realized:,.2f} ({trades} trades)\n"
        f"**Unrealized:** ${unrealized:,.2f} ({open_count} positions)\n"
        f"**Total P&L:** ${total_pnl:,.2f}\n\n"
        f"Mode: {'PAPER' if PAPER_MODE else 'LIVE'}"
    )

    send_signal_alert("Daily P&L", "Weather Edge Bot", 0, 0, 0, message)
    print("\nSummary sent to Telegram.")


if __name__ == "__main__":
    main()
