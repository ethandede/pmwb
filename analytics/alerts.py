"""Telegram alerts for analytics thresholds."""


def send_daily_scorecard(stats: dict):
    try:
        if not stats:
            return
        from alerts.telegram_alert import send_signal_alert
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        pnl = stats.get("net_pnl", 0)
        hit_rate = stats.get("hit_rate", 0)

        msg = (
            f"Daily Scorecard\n"
            f"Record: {wins}W / {losses}L ({hit_rate:.0%})\n"
            f"P&L: {'+'if pnl>=0 else ''}${pnl:.2f}"
        )
        send_signal_alert(msg, "", 0, 0, 0, "")
    except Exception:
        pass


def send_recommendation_alert(rec: dict):
    try:
        from alerts.telegram_alert import send_signal_alert
        msg = (
            f"Parameter Alert: {rec['param_name']}\n"
            f"Current: {rec['current_value']} -> Suggested: {rec['suggested_value']}\n"
            f"Reason: {rec['reason']}\n"
            f"Confidence: {rec['confidence'].upper()} ({rec['sample_size']} trades)"
        )
        send_signal_alert(msg, "", 0, 0, 0, "")
    except Exception:
        pass
