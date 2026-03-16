import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

# Track sent alerts to avoid spam (reset on restart)
_sent_alerts: dict[str, float] = {}
ALERT_COOLDOWN = 3600  # 1 hour between duplicate alerts


def _send(message: str):
    """Low-level Telegram send."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — skipping alert")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


def send_alert(title: str, body: str, dedup_key: str | None = None):
    """Send a general alert with optional dedup (1 per hour per key)."""
    import time
    if dedup_key:
        last = _sent_alerts.get(dedup_key, 0)
        if time.time() - last < ALERT_COOLDOWN:
            return
        _sent_alerts[dedup_key] = time.time()
    _send(f"⚠️ {title}\n\n{body}")
    print(f"Alert sent: {title}")


def send_signal_alert(market_question: str, city: str, model_prob: float, market_prob: float, edge: float, direction: str):
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
    _send(message)
    print(f"Telegram alert sent for {city}")
