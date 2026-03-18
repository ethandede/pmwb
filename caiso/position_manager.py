"""CAISO position manager — evaluate/fortify/exit logic for directional positions.

Evaluates open positions against current signals to decide hold/fortify/exit.
"""

from config import (
    CAISO_FORTIFY_EDGE_INCREASE,
    CAISO_EXIT_EDGE_DECAY,
    CAISO_MAX_POSITIONS_PER_HUB,
    CAISO_PAPER_BANKROLL,
)
from caiso.paper_trader import (
    open_position, close_position, expire_positions,
    get_open_positions,
)
from caiso.hubs import scan_all_caiso_hubs


def evaluate_caiso_position(position: dict, current_signal: dict) -> dict:
    """Evaluate an open position against the current signal.

    Returns dict with:
        action: "hold" | "exit" | "fortify"
        reason: human-readable explanation
    """
    pos_signal = position["signal"]
    cur_signal = current_signal["signal"]

    # Signal flipped -> exit
    if pos_signal != cur_signal and cur_signal != "NEUTRAL":
        return {
            "action": "exit",
            "reason": f"Signal flipped from {pos_signal} to {cur_signal}",
        }

    # Edge decay check: if current edge / entry edge < threshold -> exit
    entry_edge = abs(position["edge"])
    current_edge = abs(current_signal["edge"])

    if entry_edge > 0:
        edge_ratio = current_edge / entry_edge
        if edge_ratio < CAISO_EXIT_EDGE_DECAY:
            return {
                "action": "exit",
                "reason": f"Edge decay: {current_edge:.2f} / {entry_edge:.2f} = {edge_ratio:.2f} < {CAISO_EXIT_EDGE_DECAY}",
            }

    # Fortify check: if current edge exceeds entry edge by threshold -> fortify
    edge_increase = current_edge - entry_edge
    if edge_increase > CAISO_FORTIFY_EDGE_INCREASE and pos_signal == cur_signal:
        return {
            "action": "fortify",
            "reason": f"Edge increased by {edge_increase:.2f} (>{CAISO_FORTIFY_EDGE_INCREASE})",
        }

    # Neutral signal with no flip -> hold
    return {
        "action": "hold",
        "reason": f"Signal agrees ({cur_signal}), edge ratio OK",
    }


def run_caiso_manager():
    """Run position management cycle: expire, evaluate, act.

    1. Expire TTL-exceeded positions
    2. Scan current signals
    3. Evaluate each open position
    4. Execute exit/fortify actions
    """
    from caiso.hubs import _fetch_caiso_market_data

    # Get current market price for expiry settlement
    market_data = _fetch_caiso_market_data()
    current_price = market_data.get("price", 40.0)

    # Expire old positions
    expire_positions(current_price)

    # Scan current signals
    current_signals = scan_all_caiso_hubs()
    signal_by_hub = {s["hub"]: s for s in current_signals}

    # Evaluate each open position
    positions = get_open_positions()
    actions = []

    for pos in positions:
        hub = pos["hub"]
        current = signal_by_hub.get(hub)
        if current is None:
            continue

        result = evaluate_caiso_position(pos, current)
        result["hub"] = hub
        result["position_id"] = pos["id"]

        if result["action"] == "exit":
            close_position(
                pos["id"],
                exit_price=current["current_caiso_price"],
                exit_signal=current["signal"],
                reason=result["reason"],
            )
        elif result["action"] == "fortify":
            # Open additional position in same direction
            open_position(current, bankroll=CAISO_PAPER_BANKROLL)

        actions.append(result)

    if actions:
        print(f"  CAISO manager: {len(actions)} positions evaluated")
        for a in actions:
            print(f"    {a['hub']}: {a['action']} — {a['reason']}")

    return actions
