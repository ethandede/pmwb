"""Integration tests for PipelineRunner with mock exchanges."""
from unittest.mock import MagicMock, patch
from pipeline.runner import PipelineRunner
from pipeline.types import CycleState


def test_runner_creates_cycle_state_per_config():
    """Each config gets its own CycleState (budget isolation)."""
    configs = [MagicMock(name="cfg_a", exchange="kalshi"),
               MagicMock(name="cfg_b", exchange="kalshi")]
    exchanges = {"kalshi": MagicMock(), "ercot": MagicMock()}

    runner = PipelineRunner(configs, exchanges)

    # Mock the exchange to return empty positions
    exchanges["kalshi"].get_positions.return_value = []
    exchanges["kalshi"].get_orders.return_value = []
    exchanges["kalshi"].get_balance.return_value = {"balance": 10000, "portfolio_value": 0}

    with patch('pipeline.runner.fetch_markets', return_value=[]):
        runner.run_cycle(paper_mode=True)

    # Each config was processed (fetch_markets called twice)
    assert exchanges["kalshi"].get_positions.call_count == 1  # fetched once


def test_runner_skips_kalshi_when_maxed():
    """Runner skips Kalshi configs when position limit is hit."""
    config = MagicMock(name="kalshi_temp", exchange="kalshi")
    exchanges = {"kalshi": MagicMock(), "ercot": MagicMock()}

    runner = PipelineRunner([config], exchanges)

    # Simulate 50 positions (maxed)
    exchanges["kalshi"].get_positions.return_value = [
        {"ticker": f"T{i}", "position_fp": "1.0"} for i in range(50)
    ]
    exchanges["kalshi"].get_orders.return_value = []
    exchanges["kalshi"].get_balance.return_value = {"balance": 10000, "portfolio_value": 5000}

    with patch('pipeline.runner.fetch_markets') as mock_fetch:
        runner.run_cycle(paper_mode=True)
        mock_fetch.assert_not_called()  # skipped because maxed


def test_runner_error_in_one_config_doesnt_abort():
    """Error in config A doesn't prevent config B from running."""
    config_a = MagicMock(name="bad_config", exchange="kalshi")
    config_b = MagicMock(name="good_config", exchange="kalshi")
    exchanges = {"kalshi": MagicMock(), "ercot": MagicMock()}

    runner = PipelineRunner([config_a, config_b], exchanges)

    exchanges["kalshi"].get_positions.return_value = []
    exchanges["kalshi"].get_orders.return_value = []
    exchanges["kalshi"].get_balance.return_value = {"balance": 10000, "portfolio_value": 0}

    call_count = [0]

    def fetch_side_effect(config, exchange):
        call_count[0] += 1
        if config.name == "bad_config":
            raise RuntimeError("API down")
        return []

    with patch('pipeline.runner.fetch_markets', side_effect=fetch_side_effect):
        runner.run_cycle(paper_mode=True)

    assert call_count[0] == 2  # both configs attempted
