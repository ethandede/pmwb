import csv
import os
from datetime import datetime, timezone

SIGNALS_CSV = "logs/signals.csv"

def log_signal(market_question: str, city: str, model_prob: float, market_prob: float, edge: float, direction: str, dutch_book: bool, paper_trade: bool):
    """Append a signal to the CSV log."""
    file_exists = os.path.exists(SIGNALS_CSV)
    os.makedirs(os.path.dirname(SIGNALS_CSV), exist_ok=True)

    with open(SIGNALS_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob", "edge", "direction", "dutch_book", "paper_trade"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            market_question,
            city,
            f"{model_prob:.4f}",
            f"{market_prob:.4f}",
            f"{edge:.4f}",
            direction,
            dutch_book,
            paper_trade,
        ])
