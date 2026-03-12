"""Monte Carlo walk-forward simulator: simulates trading on signal log.

IMPORTANT: This is a Monte Carlo simulation, NOT a historical backtest.
Outcomes are simulated using model_prob as the true probability (self-reinforcing).
For real historical performance, use generate_report() with actual settlement
data from trades.db. This simulator is useful for:
- Stress-testing position sizing parameters
- Estimating drawdown characteristics
- Comparing different edge thresholds

When trades.db has resolved trades, prefer reports.py for actual performance.
"""

import pandas as pd
import numpy as np
from backtesting.data_loader import load_signals


def walk_forward_simulate(
    signals_csv: str = "logs/signals.csv",
    edge_threshold: float = 0.105,
    confidence_threshold: float = 70.0,
    initial_bankroll: float = 1000.0,
    bet_fraction: float = 0.02,
    max_bet_pct: float = 0.03,
) -> dict:
    """Simulate historical trading using signal log.

    Walks forward day by day, "betting" on signals that meet thresholds.
    Uses a simple fixed-fraction sizing (placeholder for Kelly in Phase 2).

    Settlement is simulated: if model_prob > 0.5 for BUY YES, we assume
    the signal was correct with probability = model_prob. This is an
    approximation -- real settlement data from trades.db is better.

    Args:
        signals_csv: Path to signals CSV
        edge_threshold: Minimum |edge| to trade
        confidence_threshold: Minimum confidence to trade (NaN treated as meeting threshold)
        initial_bankroll: Starting capital
        bet_fraction: Fraction of bankroll per trade
        max_bet_pct: Maximum fraction of bankroll per trade

    Returns:
        Dict with daily_pnl (Series), total_return, signals_traded, etc.
    """
    signals = load_signals(signals_csv)
    if signals.empty:
        return {"daily_pnl": pd.Series(), "total_return": 0.0, "signals_traded": 0}

    # Filter to tradeable signals
    signals = signals[signals["edge"].abs() >= edge_threshold].copy()
    conf_mask = signals["confidence"].isna() | (signals["confidence"] >= confidence_threshold)
    signals = signals[conf_mask]

    if signals.empty:
        return {"daily_pnl": pd.Series(), "total_return": 0.0, "signals_traded": 0}

    signals["date"] = signals["timestamp"].dt.date

    bankroll = initial_bankroll
    daily_pnl = {}
    trades_taken = 0

    for date, day_signals in signals.groupby("date"):
        day_pnl = 0.0

        for _, sig in day_signals.iterrows():
            # Position size
            bet_size = min(bankroll * bet_fraction, bankroll * max_bet_pct)
            if bet_size < 0.50:
                continue

            edge = sig["edge"]
            market_prob = sig["market_prob"]
            model_prob = sig["model_prob"]

            # Simulate outcome using model_prob as true probability
            # In a real backtest with settlement data, use actual outcomes instead
            np.random.seed(hash((str(date), sig.get("ticker", trades_taken))) % (2**31))
            outcome_yes = np.random.random() < model_prob

            if sig["direction"] == "BUY YES":
                # Bought YES at market_prob, settles to $1 if yes, $0 if no
                if outcome_yes:
                    trade_pnl = bet_size * (1.0 - market_prob) / market_prob
                else:
                    trade_pnl = -bet_size
            else:
                # Sold YES (bought NO) at (1 - market_prob)
                if not outcome_yes:
                    trade_pnl = bet_size * market_prob / (1.0 - market_prob)
                else:
                    trade_pnl = -bet_size

            day_pnl += trade_pnl
            trades_taken += 1

        bankroll += day_pnl
        daily_pnl[date] = day_pnl

    daily_series = pd.Series(daily_pnl)
    total_return = (bankroll - initial_bankroll) / initial_bankroll

    return {
        "daily_pnl": daily_series,
        "total_return": total_return,
        "final_bankroll": bankroll,
        "signals_traded": trades_taken,
        "max_drawdown": _max_drawdown(daily_series, initial_bankroll),
        "sharpe_ratio": _sharpe_ratio(daily_series) if len(daily_series) > 1 else 0.0,
    }


def _max_drawdown(daily_pnl: pd.Series, initial_bankroll: float) -> float:
    """Calculate maximum drawdown as a fraction of peak bankroll."""
    cumulative = daily_pnl.cumsum() + initial_bankroll
    peak = cumulative.cummax()
    drawdown = (peak - cumulative) / peak
    return float(drawdown.max()) if not drawdown.empty else 0.0


def _sharpe_ratio(daily_pnl: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualized Sharpe ratio from daily P&L."""
    if daily_pnl.std() == 0:
        return 0.0
    daily_mean = daily_pnl.mean() - risk_free_rate / 252
    return float(daily_mean / daily_pnl.std() * np.sqrt(252))
