import logging
from config import HOST, CHAIN_ID, SIGNATURE_TYPE, FUNDER_ADDRESS, PRIVATE_KEY, PAPER_MODE, MAX_POSITION_USD
from alerts.telegram_alert import send_signal_alert

logger = logging.getLogger(__name__)

_client = None

def init_clob_client():
    global _client
    if _client is None:
        from py_clob_client.client import ClobClient
        _client = ClobClient(
            HOST,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=SIGNATURE_TYPE,
            funder=FUNDER_ADDRESS,
        )
        _client.set_api_creds(_client.create_or_derive_api_creds())
    return _client


def execute_signal(market: dict, city: str, model_prob: float, market_prob: float, edge: float, direction: str):
    token_ids = market.get("clobTokenIds", [])
    if not token_ids:
        print("No clobTokenIds in market data — skipping execution")
        return

    # clobTokenIds[0] = Yes token, clobTokenIds[1] = No token
    yes_token = token_ids[0]

    # Determine side: positive edge = buy YES (underpriced), negative = sell YES (overpriced)
    side = "BUY" if edge > 0 else "SELL"

    # Position sizing: $50 max per leg
    size = min(MAX_POSITION_USD / max(market_prob, 0.01), MAX_POSITION_USD)
    size = max(1, int(size))

    # Limit price with buffer toward our edge
    # BUY: bid slightly above market (but below model) to improve fill
    # SELL: ask slightly below market (but above model) to improve fill
    if edge > 0:
        limit_price = market_prob + (edge * 0.3)
    else:
        limit_price = market_prob - (abs(edge) * 0.3)
    limit_price = max(0.01, min(0.99, round(limit_price, 2)))

    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    print(f"\n  [{mode_label}] {side} {size} shares @ ${limit_price:.2f}")
    print(f"  Token: {yes_token[:16]}... | Edge: {edge:+.1%} | City: {city}")

    if PAPER_MODE:
        print(f"  PAPER MODE — no order sent. Set PAPER_MODE=false in .env when ready.")
        return

    # Live execution
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY as BUY_SIDE, SELL as SELL_SIDE

        clob = init_clob_client()
        order_side = BUY_SIDE if side == "BUY" else SELL_SIDE
        order_args = OrderArgs(
            token_id=yes_token,
            price=limit_price,
            size=size,
            side=order_side,
        )
        signed = clob.create_order(order_args)
        resp = clob.post_order(signed, OrderType.GTC)
        order_id = resp.get("orderID", "unknown")
        print(f"  Order posted! ID: {order_id}")
        send_signal_alert(
            market["question"], city, model_prob, market_prob, edge,
            f"{direction} (LIVE order {order_id})"
        )
    except Exception as e:
        print(f"  Order failed: {e}")
