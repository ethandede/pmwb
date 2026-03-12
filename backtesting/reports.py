"""Generate backtesting reports: CLI tables and calibration plots."""

import os
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from backtesting.data_loader import load_signals, load_trades
from backtesting.scorer import brier_score, log_loss_score, hit_rate_by_confidence, pnl_by_city
from backtesting.calibration import calibration_curve

console = Console()


def generate_report(
    signals_csv: str = "logs/signals.csv",
    trades_db: str = "data/trades.db",
    plot: bool = False,
) -> dict:
    """Generate a comprehensive backtesting report.

    Returns a dict of key metrics for programmatic use.
    Prints Rich tables to console for human consumption.
    Optionally generates calibration plot.
    """
    signals = load_signals(signals_csv)
    trades = load_trades(trades_db) if os.path.exists(trades_db) else pd.DataFrame()

    result = {}

    # --- P&L Summary ---
    if not trades.empty and "pnl" in trades.columns:
        resolved = trades.dropna(subset=["settlement_outcome"])
        result["total_pnl"] = float(resolved["pnl"].sum()) if not resolved.empty else 0.0
        result["trade_count"] = len(resolved)
        result["win_count"] = int((resolved["pnl"] > 0).sum())
        result["loss_count"] = int((resolved["pnl"] < 0).sum())
        result["win_rate"] = result["win_count"] / max(result["trade_count"], 1)
        result["avg_win"] = float(resolved.loc[resolved["pnl"] > 0, "pnl"].mean()) if result["win_count"] > 0 else 0.0
        result["avg_loss"] = float(resolved.loc[resolved["pnl"] < 0, "pnl"].mean()) if result["loss_count"] > 0 else 0.0
    else:
        result["total_pnl"] = 0.0
        result["trade_count"] = 0
        result["win_count"] = 0
        result["loss_count"] = 0
        result["win_rate"] = 0.0
        result["avg_win"] = 0.0
        result["avg_loss"] = 0.0

    # --- Calibration (Brier/Log Loss) ---
    # Match signals to trade outcomes via ticker
    if not trades.empty and not signals.empty:
        merged = signals.merge(
            trades[["ticker", "settlement_outcome"]].drop_duplicates(subset=["ticker"]),
            on="ticker",
            how="inner",
        )
        if not merged.empty:
            # Convert settlement to binary: model predicted YES probability,
            # outcome is 1 if settled YES, 0 if settled NO
            merged["outcome_binary"] = (merged["settlement_outcome"] == "yes").astype(int)
            probs = merged["model_prob"].values
            outcomes = merged["outcome_binary"].values

            result["brier_score"] = brier_score(probs, outcomes)
            result["log_loss"] = log_loss_score(probs, outcomes)
            result["calibration_n"] = len(merged)
        else:
            result["brier_score"] = None
            result["log_loss"] = None
            result["calibration_n"] = 0
    else:
        result["brier_score"] = None
        result["log_loss"] = None
        result["calibration_n"] = 0

    # --- Print Report ---
    _print_pnl_table(result)

    if not trades.empty:
        _print_pnl_by_city(trades)

    if result["brier_score"] is not None:
        _print_calibration_table(result)

    # --- Calibration Plot ---
    if plot and result["brier_score"] is not None:
        _plot_calibration(merged["model_prob"].values, merged["outcome_binary"].values)

    # --- Signal Summary ---
    result["total_signals"] = len(signals)
    result["kalshi_signals"] = len(signals[signals["city"].str.contains("kalshi", case=False, na=False)])

    return result


def _print_pnl_table(result: dict):
    table = Table(title="P&L Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total P&L", f"${result['total_pnl']:.2f}")
    table.add_row("Trades", str(result["trade_count"]))
    table.add_row("Wins / Losses", f"{result['win_count']} / {result['loss_count']}")
    table.add_row("Win Rate", f"{result['win_rate']:.1%}")
    table.add_row("Avg Win", f"${result['avg_win']:.2f}")
    table.add_row("Avg Loss", f"${result['avg_loss']:.2f}")
    console.print(table)


def _print_pnl_by_city(trades: pd.DataFrame):
    resolved = trades.dropna(subset=["settlement_outcome"])
    if resolved.empty:
        return

    if "city" not in resolved.columns:
        return

    city_pnl = pnl_by_city(resolved)
    table = Table(title="P&L by City")
    table.add_column("City", style="cyan")
    table.add_column("Total P&L", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Avg P&L", justify="right")

    for city, row in city_pnl.iterrows():
        color = "green" if row["total_pnl"] > 0 else "red"
        table.add_row(
            str(city),
            f"[{color}]${row['total_pnl']:.2f}[/{color}]",
            str(int(row["count"])),
            f"${row['avg_pnl']:.2f}",
        )
    console.print(table)


def _print_calibration_table(result: dict):
    table = Table(title="Calibration Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    brier = result["brier_score"]
    table.add_row("Brier Score", f"{brier:.4f}" if brier is not None else "N/A")
    table.add_row("Log Loss", f"{result['log_loss']:.4f}" if result["log_loss"] is not None else "N/A")
    table.add_row("Calibration Samples", str(result["calibration_n"]))

    # Brier score interpretation
    if brier is not None:
        if brier < 0.1:
            table.add_row("Rating", "[green]Excellent[/green]")
        elif brier < 0.2:
            table.add_row("Rating", "[yellow]Good[/yellow]")
        elif brier < 0.25:
            table.add_row("Rating", "[yellow]Fair (coin-flip territory)[/yellow]")
        else:
            table.add_row("Rating", "[red]Poor[/red]")

    console.print(table)


def _plot_calibration(probs, outcomes, save_path: str = "logs/calibration.png"):
    """Generate and save calibration curve plot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bin_means, bin_rates, bin_counts = calibration_curve(probs, outcomes, n_bins=10)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Calibration curve
    ax1.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
    valid = ~np.isnan(bin_rates)
    ax1.plot(bin_means[valid], bin_rates[valid], "bo-", label="Model")
    ax1.set_xlabel("Predicted probability")
    ax1.set_ylabel("Observed frequency")
    ax1.set_title("Calibration Curve")
    ax1.legend()
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)

    # Histogram of predictions
    ax2.bar(bin_means, bin_counts, width=0.08, alpha=0.7)
    ax2.set_xlabel("Predicted probability")
    ax2.set_ylabel("Count")
    ax2.set_title("Prediction Distribution")

    plt.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, dpi=100)
    plt.close()
    console.print(f"[dim]Calibration plot saved to {save_path}[/dim]")
