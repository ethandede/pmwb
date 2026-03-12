"""CLI entry point: python -m backtesting"""

import argparse
from backtesting.reports import generate_report
from backtesting.walk_forward import walk_forward_simulate
from rich.console import Console

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Weather Bot Backtesting Reports")
    parser.add_argument("--signals", default="logs/signals.csv", help="Path to signals CSV")
    parser.add_argument("--trades", default="data/trades.db", help="Path to trades SQLite DB")
    parser.add_argument("--plot", action="store_true", help="Generate calibration plot")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward simulation")
    parser.add_argument("--edge-threshold", type=float, default=0.105, help="Edge threshold for walk-forward")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Initial bankroll for walk-forward")
    parser.add_argument("--kelly", action="store_true", help="Use Kelly sizing in walk-forward")
    parser.add_argument("--fractional-kelly", type=float, default=0.25, help="Fractional Kelly multiplier (default 0.25)")
    args = parser.parse_args()

    console.print("[bold cyan]Weather Bot — Backtesting Report[/bold cyan]\n")

    result = generate_report(
        signals_csv=args.signals,
        trades_db=args.trades,
        plot=args.plot,
    )

    console.print(f"\n[dim]Total signals logged: {result['total_signals']}[/dim]")
    console.print(f"[dim]Kalshi signals: {result['kalshi_signals']}[/dim]")

    if args.walk_forward:
        console.print("\n[bold cyan]Walk-Forward Monte Carlo Simulation[/bold cyan]")
        console.print("[dim](Outcomes simulated from model probs — use P&L Summary above for actual performance)[/dim]\n")
        wf = walk_forward_simulate(
            signals_csv=args.signals,
            edge_threshold=args.edge_threshold,
            initial_bankroll=args.bankroll,
            kelly_mode=args.kelly,
            fractional_kelly=args.fractional_kelly,
        )
        sizing_label = f"Kelly {args.fractional_kelly:.0%}x" if args.kelly else "Fixed 2%"
        console.print(f"Sizing mode: {sizing_label}")
        console.print(f"Signals traded: {wf['signals_traded']}")
        console.print(f"Final bankroll: ${wf['final_bankroll']:.2f} (started ${args.bankroll:.2f})")
        console.print(f"Total return: {wf['total_return']:.1%}")
        console.print(f"Max drawdown: {wf['max_drawdown']:.1%}")
        console.print(f"Sharpe ratio: {wf['sharpe_ratio']:.2f}")


if __name__ == "__main__":
    main()
