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

        # Include ALL unsettled positions from trades.db for dedup + cross-contract
        import sqlite3
        held_sides: dict[str, str] = {}  # ticker -> side for cross-contract checks
        try:
            conn = sqlite3.connect("data/trades.db")
            rows = conn.execute(
                """SELECT ticker,
                    CASE WHEN side IN ('no', 'buy_no', 'sell_no') THEN 'no' ELSE 'yes' END as pos_side,
                    SUM(CASE
                        WHEN side IN ('no', 'buy_no', 'yes', 'buy_yes') THEN fill_qty
                        WHEN side LIKE 'sell_%' THEN -fill_qty
                        ELSE 0
                    END) as net_qty
                FROM trades
                WHERE settlement_outcome IS NULL
                GROUP BY ticker,
                    CASE WHEN side IN ('no', 'buy_no', 'sell_no') THEN 'no' ELSE 'yes' END
                HAVING net_qty > 0"""
            ).fetchall()
            conn.close()
            for ticker, pos_side, net_qty in rows:
                held_positions.append({"ticker": ticker, "position_fp": str(net_qty)})
                held_sides[ticker] = pos_side
        except Exception as e:
            print(f"  Paper dedup query failed: {e}")

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

                filtered = filter_signals(config, signals, held_positions, resting_tickers,
                                          held_sides=held_sides)
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
                # Alert on 3rd consecutive error (not first — transient failures are normal)
                if self._consecutive_errors[config.name] == 3:
                    try:
                        from alerts.telegram_alert import send_alert
                        send_alert(
                            f"{config.display_name} API Down",
                            f"3 consecutive failures.\nLast error: {e}",
                            dedup_key=f"pipeline_{config.name}_down",
                        )
                    except Exception:
                        pass

            # Log cycle stats
            if state.trades_executed > 0 or state.errors:
                print(f"  {config.display_name}: {state.signals_scored} scored, "
                      f"{state.signals_filtered} filtered, {state.trades_executed} traded, "
                      f"${state.scan_spent:.2f} spent"
                      + (f", {len(state.errors)} errors" if state.errors else ""))
