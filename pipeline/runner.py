"""PipelineRunner — orchestrates market scanning cycles.

Replaces scanner.py + kalshi/trader.py globals.
Holds shared state (bankroll, circuit breaker).
Dispatches per-config with isolated CycleState.
"""

from pipeline.types import CycleState
from pipeline.stages import (
    fetch_markets, score_signal, filter_signals,
    sanity_check, size_position, execute_trade,
)
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker
from config import MAX_POSITIONS_TOTAL

MAX_CONSECUTIVE_ERRORS = 5


class PipelineRunner:
    def __init__(self, configs: list, exchanges: dict):
        self.configs = configs
        self.exchanges = exchanges
        self.bankroll = BankrollTracker(initial_bankroll=500.0)
        self.circuit_breaker = CircuitBreaker()
        self._consecutive_errors: dict[str, int] = {}

    def run_cycle(self, paper_mode: bool):
        """Run one full scan cycle across all configs."""
        # Sync bankroll from Kalshi API
        kalshi = self.exchanges.get("kalshi")
        if kalshi:
            try:
                bal = kalshi.get_balance()
                self.bankroll.update_from_api(
                    balance_cents=bal.get("balance", 0),
                    portfolio_value_cents=bal.get("portfolio_value", 0),
                )
            except Exception as e:
                print(f"  Bankroll sync failed: {e}")

        # Pre-dispatch: fetch shared state once
        held_positions = []
        resting_tickers = set()
        kalshi_position_count = 0

        if kalshi:
            try:
                held_positions = kalshi.get_positions()
                resting_orders = kalshi.get_orders(status="resting")
                resting_tickers = {o.get("ticker", "") for o in resting_orders
                                   if o.get("action") == "buy"}
                kalshi_position_count = sum(
                    1 for p in held_positions
                    if float(p.get("position_fp", 0)) != 0
                )
            except Exception as e:
                print(f"  Position fetch failed: {e}")

        # Also include paper positions from trades.db for dedup
        import sqlite3
        try:
            conn = sqlite3.connect("data/trades.db")
            paper_rows = conn.execute(
                "SELECT DISTINCT ticker FROM trades WHERE settlement_outcome IS NULL AND order_id LIKE 'paper-%'"
            ).fetchall()
            paper_tickers = {r[0] for r in paper_rows}
            conn.close()
            # Add paper tickers to held_positions so filter_signals skips them
            for t in paper_tickers:
                held_positions.append({"ticker": t, "position_fp": "1.0"})
        except Exception:
            pass

        # Process each config
        for config in self.configs:
            # Skip configs with too many consecutive errors
            if self._consecutive_errors.get(config.name, 0) >= MAX_CONSECUTIVE_ERRORS:
                print(f"  SKIP {config.name} — {MAX_CONSECUTIVE_ERRORS} consecutive errors")
                continue

            # Cross-config position limit
            if config.exchange == "kalshi" and kalshi_position_count >= MAX_POSITIONS_TOTAL:
                continue

            state = CycleState()
            exchange = self.exchanges.get(config.exchange)

            try:
                markets = fetch_markets(config, exchange)
                state.signals_scored = len(markets)

                signals = []
                for m in markets:
                    try:
                        signals.append(score_signal(config, m))
                    except Exception as e:
                        state.errors.append(f"score: {e}")

                filtered = filter_signals(config, signals, held_positions, resting_tickers)
                state.signals_filtered = len(filtered)

                for signal in filtered:
                    if not sanity_check(config, signal):
                        continue

                    signal.size = size_position(
                        config, signal, self.bankroll,
                        self.circuit_breaker, state,
                    )

                    if signal.size and signal.size.count > 0:
                        signal.trade_result = execute_trade(
                            config, signal, signal.size, exchange, paper_mode,
                        )
                        if signal.trade_result and signal.trade_result.count > 0:
                            state.scan_spent += signal.trade_result.cost
                            state.trades_executed += 1
                            state.total_edge += abs(signal.edge)

                # Reset error counter on success
                self._consecutive_errors[config.name] = 0

            except Exception as e:
                state.errors.append(str(e))
                self._consecutive_errors[config.name] = \
                    self._consecutive_errors.get(config.name, 0) + 1
                print(f"  Pipeline error ({config.name}): {e}")

            # Log cycle stats
            if state.trades_executed > 0 or state.errors:
                print(f"  {config.display_name}: {state.signals_scored} scored, "
                      f"{state.signals_filtered} filtered, {state.trades_executed} traded, "
                      f"${state.scan_spent:.2f} spent"
                      + (f", {len(state.errors)} errors" if state.errors else ""))
