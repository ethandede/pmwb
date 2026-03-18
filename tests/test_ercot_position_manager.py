from unittest.mock import patch

class TestBinaryManager:
    def test_run_ercot_manager_calls_settle(self):
        from ercot.position_manager import run_ercot_manager
        with patch("ercot.position_manager.settle_expired_hours") as mock_settle, \
             patch("ercot.position_manager.fetch_rt_settlement"):
            mock_settle.return_value = []
            run_ercot_manager()
            mock_settle.assert_called_once()
