"""Stale maker order cleanup.

Cancels resting orders that are too old or too close to event settlement.
Runs each daemon cycle — most cycles cancel nothing.
"""

import re
from datetime import datetime, timezone, timedelta

_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})-")
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_event_date(ticker: str) -> datetime | None:
    """Extract event date from ticker like KXHIGHNY-26MAR16-T58."""
    m = _DATE_RE.search(ticker)
    if not m:
        return None
    year = 2000 + int(m.group(1))
    month = _MONTHS.get(m.group(2))
    day = int(m.group(3))
    if not month:
        return None
    try:
        return datetime(year, month, day, 14, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


def _send_cleanup_alert(cancelled: list[dict]):
    """Send Telegram alert summarizing cancelled orders."""
    if not cancelled:
        return
    try:
        from alerts.telegram_alert import send_alert
        lines = [f"  {c['ticker']} — {c['reason']} ({c['age_hours']:.1f}h old)" for c in cancelled]
        send_alert(
            f"Cancelled {len(cancelled)} stale order(s)",
            "\n".join(lines),
            dedup_key="stale_order_cleanup",
        )
    except Exception:
        pass


def cleanup_stale_orders(
    exchange,
    max_age_hours: float = 6,
    close_proximity_hours: float = 2,
) -> list[dict]:
    """Cancel resting orders that are stale by age or event proximity.

    Returns list of successfully cancelled order summaries.
    """
    now = datetime.now(timezone.utc)
    age_cutoff = now - timedelta(hours=max_age_hours)

    resting = exchange.get_orders(status="resting", limit=200)
    cancelled = []

    for order in resting:
        order_id = order.get("order_id", "")
        ticker = order.get("ticker", "")
        created_str = order.get("created_time", "")

        if not order_id or not created_str:
            continue

        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        age_hours = (now - created).total_seconds() / 3600
        reason = None

        if created < age_cutoff:
            reason = "age"

        if not reason:
            event_date = _parse_event_date(ticker)
            if event_date:
                hours_to_close = (event_date - now).total_seconds() / 3600
                if hours_to_close < close_proximity_hours:
                    reason = "event_close"

        if not reason:
            continue

        try:
            exchange.cancel_order(order_id)
            cancelled.append({
                "order_id": order_id,
                "ticker": ticker,
                "reason": reason,
                "age_hours": round(age_hours, 1),
            })
            print(f"  Cancelled stale order {order_id} ({ticker}): {reason}, {age_hours:.1f}h old")
        except Exception as e:
            print(f"  Failed to cancel {order_id}: {e}")

    _send_cleanup_alert(cancelled)
    return cancelled
