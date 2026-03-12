import csv
import pytest
from backtesting.walk_forward import walk_forward_simulate


@pytest.fixture
def signals_csv(tmp_path):
    """Create a signal log spanning 5 days for walk-forward testing."""
    path = str(tmp_path / "signals.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "market_question", "city", "model_prob", "market_prob",
                         "edge", "direction", "dutch_book", "paper_trade", "confidence", "ticker"])
        # Day 1-5: some BUY YES with varying edges
        for day in range(1, 6):
            writer.writerow([
                f"2026-03-{day:02d}T10:00:00+00:00", f"NYC High {60+day}-{61+day}",
                "nyc (kalshi)", f"{0.5 + day*0.05:.2f}", "0.45",
                f"{0.05 + day*0.05:.2f}", "BUY YES", "False", "False", "80", f"TICK{day}",
            ])
    return path


def test_walk_forward_returns_results(signals_csv):
    results = walk_forward_simulate(
        signals_csv=signals_csv,
        edge_threshold=0.07,
        initial_bankroll=500.0,
        bet_fraction=0.02,
    )
    assert "daily_pnl" in results
    assert "total_return" in results
    assert "signals_traded" in results
    assert results["signals_traded"] > 0


def test_walk_forward_respects_edge_threshold(signals_csv):
    results_low = walk_forward_simulate(signals_csv=signals_csv, edge_threshold=0.05)
    results_high = walk_forward_simulate(signals_csv=signals_csv, edge_threshold=0.20)
    assert results_low["signals_traded"] >= results_high["signals_traded"]
