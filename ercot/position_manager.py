"""ERCOT position manager — evaluate/fortify/exit paper positions.

Mirrors kalshi/position_manager.py patterns but adapted for paper-only
ERCOT power price positions with no exchange API.
"""

from rich.console import Console
from rich.table import Table

from config import (
    ERCOT_FORTIFY_EDGE_INCREASE, ERCOT_EXIT_EDGE_DECAY,
    ERCOT_MAX_POSITIONS_PER_HUB, ERCOT_PAPER_BANKROLL,
)
from ercot.paper_trader import (
    get_open_positions, close_position, open_position, expire_positions,
)
from ercot.hubs import scan_all_hubs

console = Console()


def evaluate_ercot_position(position: dict, current_signal: dict) -> dict:
    """Evaluate a paper position against fresh signal. Returns action dict."""
    entry_edge = position["edge"]
    entry_signal = position["signal"]
    current_edge = current_signal["edge"]
    current_direction = current_signal["signal"]

    result = {
        "position_id": position["id"],
        "hub": position["hub"],
        "hub_name": position["hub_name"],
        "entry_edge": entry_edge,
        "current_edge": current_edge,
        "size_dollars": position["size_dollars"],
    }

    # EXIT: signal flipped
    if current_direction not in (entry_signal, "NEUTRAL") and current_direction in ("SHORT", "LONG"):
        result["action"] = "exit"
        result["reason"] = f"Signal flipped {entry_signal} → {current_direction}"
        return result

    # EXIT: edge decayed below threshold
    decay_threshold = entry_edge * ERCOT_EXIT_EDGE_DECAY
    if current_edge < decay_threshold:
        result["action"] = "exit"
        result["reason"] = f"Edge decay {current_edge:.2f} < {decay_threshold:.2f} ({ERCOT_EXIT_EDGE_DECAY:.0%} of {entry_edge:.2f})"
        return result

    # FORTIFY: edge strengthened significantly
    if (current_direction == entry_signal and
            current_edge > entry_edge + ERCOT_FORTIFY_EDGE_INCREASE):
        result["action"] = "fortify"
        result["reason"] = f"Edge strengthened {entry_edge:.2f} → {current_edge:.2f} (+{current_edge - entry_edge:.2f})"
        return result

    # HOLD: default
    result["action"] = "hold"
    result["reason"] = f"Edge {current_edge:.2f} (entry {entry_edge:.2f})"
    return result


def run_ercot_manager():
    """Evaluate all open ERCOT paper positions. Print rich table, execute actions."""
    # 1. Fetch signals first (needed for current_price), then expire
    # Note: scan runs before expire because expire_positions() needs a current
    # ERCOT price, which comes from the signal fetch. If scan fails, positions
    # won't expire this cycle (safe default — they'll expire next cycle).
    signals = scan_all_hubs()
    if signals:
        current_price = signals[0]["current_ercot_price"]
        expire_positions(current_price)

    # 2. Load remaining positions
    positions = get_open_positions()
    if not positions:
        return signals  # return signals for scanner to reuse

    # Build signal lookup by hub
    signal_by_hub = {s["hub"]: s for s in signals}

    table = Table(title=f"ERCOT Position Manager ({len(positions)} positions)")
    table.add_column("Hub", style="cyan")
    table.add_column("Side", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Entry Edge", justify="right")
    table.add_column("Curr Edge", justify="right")
    table.add_column("Action", style="bold")
    table.add_column("Reason", style="dim", max_width=40)

    exits = []
    fortifies = []

    for pos in positions:
        hub_signal = signal_by_hub.get(pos["hub"])
        if not hub_signal:
            continue

        result = evaluate_ercot_position(pos, hub_signal)

        side_color = "red" if pos["signal"] == "SHORT" else "green"
        action = result["action"].upper()
        action_color = {"EXIT": "red", "FORTIFY": "cyan", "HOLD": "green"}.get(action, "white")

        table.add_row(
            pos["hub_name"],
            f"[{side_color}]{pos['signal']}[/{side_color}]",
            f"${pos['size_dollars']:.0f}",
            f"{result['entry_edge']:.2f}",
            f"{result['current_edge']:.2f}",
            f"[{action_color}]{action}[/{action_color}]",
            result["reason"],
        )

        if result["action"] == "exit":
            exits.append((pos, hub_signal, result))
        elif result["action"] == "fortify":
            fortifies.append((pos, hub_signal, result))

    console.print(table)

    # Execute exits
    for pos, hub_signal, result in exits:
        console.print(f"  [red]EXIT[/red] {pos['hub_name']} {pos['signal']} ${pos['size_dollars']:.0f} — {result['reason']}")
        close_position(pos["id"], hub_signal["current_ercot_price"], hub_signal["signal"], result["reason"])

    # Execute fortifies (cap at existing position size — no more than doubling)
    for pos, hub_signal, result in fortifies:
        console.print(f"  [cyan]FORTIFY[/cyan] {pos['hub_name']} {pos['signal']} — {result['reason']}")
        fortify_signal = dict(hub_signal)
        open_position(fortify_signal, bankroll=ERCOT_PAPER_BANKROLL, max_size=pos["size_dollars"])

    return signals  # return for scanner to reuse
