"""Legacy entry point — runs one pipeline cycle.

Use daemon.py for production. This is kept for quick manual testing.
"""

if __name__ == "__main__":
    from config import PAPER_MODE
    from pipeline.runner import PipelineRunner
    from pipeline.config import ALL_CONFIGS
    from exchanges.kalshi import KalshiExchange
    from exchanges.ercot import ErcotExchange

    exchanges = {"kalshi": KalshiExchange(), "ercot": ErcotExchange()}
    runner = PipelineRunner(ALL_CONFIGS, exchanges)
    runner.run_cycle(paper_mode=PAPER_MODE)
