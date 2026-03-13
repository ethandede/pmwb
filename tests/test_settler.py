"""Tests for kalshi.settler — P&L calculation and exit fill handling."""

import os
import sys
import tempfile
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kalshi.settler import _calculate_pnl, _is_exit_fill
from kalshi.fill_tracker import init_trades_db, record_fill, get_unresolved_trades, resolve_trade, get_all_trades


class TestIsExitFill(unittest.TestCase):
    def test_entry_fills(self):
        self.assertFalse(_is_exit_fill("buy_yes"))
        self.assertFalse(_is_exit_fill("buy_no"))

    def test_exit_fills(self):
        self.assertTrue(_is_exit_fill("sell_yes"))
        self.assertTrue(_is_exit_fill("sell_no"))
        self.assertTrue(_is_exit_fill("yes"))
        self.assertTrue(_is_exit_fill("no"))


class TestCalculatePnl(unittest.TestCase):
    def test_buy_yes_wins(self):
        # Buy yes at 60c, market settles yes -> win (100-60)/100 per contract
        pnl = _calculate_pnl("buy_yes", 60, 10, "yes")
        self.assertAlmostEqual(pnl, 10 * 40 / 100.0)  # $4.00

    def test_buy_yes_loses(self):
        # Buy yes at 60c, market settles no -> lose 60c per contract
        pnl = _calculate_pnl("buy_yes", 60, 10, "no")
        self.assertAlmostEqual(pnl, -10 * 60 / 100.0)  # -$6.00

    def test_buy_no_wins(self):
        # Buy no at 30c, market settles no -> win (100-30)/100 per contract
        pnl = _calculate_pnl("buy_no", 30, 5, "no")
        self.assertAlmostEqual(pnl, 5 * 70 / 100.0)  # $3.50

    def test_buy_no_loses(self):
        # Buy no at 30c, market settles yes -> lose 30c per contract
        pnl = _calculate_pnl("buy_no", 30, 5, "yes")
        self.assertAlmostEqual(pnl, -5 * 30 / 100.0)  # -$1.50

    def test_sell_side_returns_zero(self):
        # sell_yes/sell_no should NOT be passed to this function;
        # if they are, it returns 0 (no longer has sell branches)
        self.assertEqual(_calculate_pnl("sell_yes", 60, 10, "yes"), 0.0)
        self.assertEqual(_calculate_pnl("sell_no", 40, 10, "no"), 0.0)

    def test_zero_qty(self):
        self.assertEqual(_calculate_pnl("buy_yes", 60, 0, "yes"), 0.0)


class TestSettlerIntegration(unittest.TestCase):
    """Integration test using a temp DB to verify exit fills are handled correctly."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        init_trades_db(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_exit_fills_marked_exited(self):
        """Exit fills should be resolved as 'exited' with pnl=0."""
        # Record an entry and its exit
        record_fill(self.db_path, "entry-1", "TICKER-A", "buy_yes", 60, 58, 10, "2025-01-01T00:00:00Z")
        record_fill(self.db_path, "exit-1", "TICKER-A", "sell_yes", 70, 72, 10, "2025-01-02T00:00:00Z")

        unresolved = get_unresolved_trades(self.db_path)
        self.assertEqual(len(unresolved), 2)

        # Simulate what run_settler does for exit fills
        for trade in unresolved:
            if _is_exit_fill(trade["side"]):
                resolve_trade(self.db_path, trade["order_id"], "exited", 0.0)
            else:
                pnl = _calculate_pnl(trade["side"], trade["fill_price"], trade["fill_qty"], "yes")
                outcome = "win" if pnl > 0 else "loss"
                resolve_trade(self.db_path, trade["order_id"], outcome, pnl)

        all_trades = get_all_trades(self.db_path)
        entry = [t for t in all_trades if t["order_id"] == "entry-1"][0]
        exit_t = [t for t in all_trades if t["order_id"] == "exit-1"][0]

        # Entry should have real P&L
        self.assertEqual(entry["settlement_outcome"], "win")
        self.assertAlmostEqual(entry["pnl"], 10 * 42 / 100.0)  # (100-58)*10/100

        # Exit should be marked exited with 0 P&L
        self.assertEqual(exit_t["settlement_outcome"], "exited")
        self.assertAlmostEqual(exit_t["pnl"], 0.0)

    def test_summary_excludes_exited(self):
        """All-time summary should exclude exited fills from W/L counts."""
        record_fill(self.db_path, "e1", "T1", "buy_yes", 60, 58, 10, "2025-01-01T00:00:00Z")
        record_fill(self.db_path, "x1", "T1", "sell_yes", 70, 72, 10, "2025-01-02T00:00:00Z")

        resolve_trade(self.db_path, "e1", "win", 4.20)
        resolve_trade(self.db_path, "x1", "exited", 0.0)

        all_trades = get_all_trades(self.db_path)
        settled = [
            t for t in all_trades
            if t["settlement_outcome"] is not None and t["settlement_outcome"] != "exited"
        ]
        exited = [t for t in all_trades if t["settlement_outcome"] == "exited"]

        self.assertEqual(len(settled), 1)
        self.assertEqual(len(exited), 1)
        self.assertEqual(settled[0]["order_id"], "e1")


if __name__ == "__main__":
    unittest.main()
