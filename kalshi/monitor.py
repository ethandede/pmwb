"""Position monitor — check current Kalshi positions and P&L.

Usage: python -m kalshi.monitor
"""

from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from kalshi.trader import get_balance, get_positions, get_orders

console = Console()


def _parse_dollars(val) -> float:
    """Parse Kalshi dollar string or cent int to float dollars."""
    if val is None:
        return 0.0
    if isinstance(val, str):
        return float(val)
    return val / 100.0  # legacy cent format


def show_portfolio():
    """Display current positions, resting orders, and P&L."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"\n[bold cyan]Kalshi Portfolio — {now}[/bold cyan]\n")

    # Balance
    bal = get_balance()
    cash = bal.get("balance", 0) / 100.0
    portfolio = bal.get("portfolio_value", 0) / 100.0
    total = cash + portfolio
    console.print(f"  Cash: ${cash:.2f}  |  Positions: ${portfolio:.2f}  |  Total: ${total:.2f}\n")

    # Active positions
    positions = get_positions()
    if positions:
        ptable = Table(title="Open Positions")
        ptable.add_column("Ticker", style="cyan")
        ptable.add_column("Side", style="bold")
        ptable.add_column("Qty", justify="right")
        ptable.add_column("Exposure", justify="right")
        ptable.add_column("Fees", justify="right", style="dim")

        for pos in positions:
            ticker = pos.get("ticker", "")
            # position_fp is signed: positive = yes, negative = no
            qty_str = pos.get("position_fp", "0")
            qty = float(qty_str)
            if qty == 0:
                continue

            side = "YES" if qty > 0 else "NO"
            exposure = _parse_dollars(pos.get("market_exposure_dollars"))
            fees = _parse_dollars(pos.get("fees_paid_dollars"))

            side_color = "green" if side == "YES" else "red"
            ptable.add_row(
                ticker,
                f"[{side_color}]{side}[/{side_color}]",
                f"{abs(qty):.0f}",
                f"${exposure:.2f}",
                f"${fees:.2f}",
            )

        console.print(ptable)
    else:
        console.print("[dim]No open positions.[/dim]")

    # Resting orders
    resting = get_orders(status="resting")
    if resting:
        console.print()
        otable = Table(title="Resting Orders (unfilled)")
        otable.add_column("Ticker", style="cyan")
        otable.add_column("Side", style="bold")
        otable.add_column("Price", justify="right")
        otable.add_column("Qty", justify="right")
        otable.add_column("Remaining", justify="right")

        for order in resting:
            ticker = order.get("ticker", "")
            side = order.get("side", "").upper()
            yes_p = _parse_dollars(order.get("yes_price_dollars"))
            no_p = _parse_dollars(order.get("no_price_dollars"))
            price = yes_p if side == "YES" else no_p
            initial = float(order.get("initial_count_fp", "0"))
            remaining = float(order.get("remaining_count_fp", "0"))

            side_color = "green" if side == "YES" else "red"
            otable.add_row(
                ticker,
                f"[{side_color}]{side}[/{side_color}]",
                f"{price:.0f}¢" if price < 1 else f"${price:.2f}",
                f"{initial:.0f}",
                f"{remaining:.0f}",
            )

        console.print(otable)
    else:
        console.print("\n[dim]No resting orders.[/dim]")

    # Recently executed orders (last few)
    executed = get_orders(status="executed", limit=10)
    if executed:
        console.print()
        etable = Table(title="Recent Fills (last 10)")
        etable.add_column("Ticker", style="cyan")
        etable.add_column("Side", style="bold")
        etable.add_column("Price", justify="right")
        etable.add_column("Filled", justify="right")
        etable.add_column("Cost", justify="right")

        for order in executed:
            ticker = order.get("ticker", "")
            side = order.get("side", "").upper()
            yes_p = _parse_dollars(order.get("yes_price_dollars"))
            no_p = _parse_dollars(order.get("no_price_dollars"))
            price = yes_p if side == "YES" else no_p
            filled = float(order.get("fill_count_fp", "0"))
            cost = _parse_dollars(order.get("taker_fill_cost_dollars")) + _parse_dollars(order.get("maker_fill_cost_dollars"))

            side_color = "green" if side == "YES" else "red"
            etable.add_row(
                ticker,
                f"[{side_color}]{side}[/{side_color}]",
                f"{price*100:.0f}¢",
                f"{filled:.0f}",
                f"${cost:.2f}",
            )

        console.print(etable)


if __name__ == "__main__":
    show_portfolio()
