import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

def send_signal_alert(market_question: str, city: str, model_prob: float, market_prob: float, edge: float, direction: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — skipping alert")
        return

    edge_pct = edge * 100
    arrow = "^" if edge > 0 else "v"
    message = (
        f"Polymarket Weather Edge\n\n"
        f"{city.replace('_', ' ').title()} — {market_question}\n"
        f"Model: {model_prob:.1%} | Market: {market_prob:.1%}\n"
        f"Edge: {edge_pct:+.1f}%\n"
        f"Signal: {arrow} {direction}\n\n"
        f"https://polymarket.com"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    try:
        requests.post(url, json=payload, timeout=10)
        print(f"Telegram alert sent for {city}")
    except Exception as e:
        print(f"Telegram error: {e}")
