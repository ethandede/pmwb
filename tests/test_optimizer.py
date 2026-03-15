import os
import sqlite3
import tempfile
import unittest
from analytics.optimizer import init_analytics_db


class TestAnalyticsSchema(unittest.TestCase):
    def test_init_creates_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_analytics_db(db_path)
            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
            self.assertIn("daily_stats", tables)
            self.assertIn("bucket_stats", tables)
            self.assertIn("recommendations", tables)
        finally:
            os.unlink(db_path)


class TestDailyStats(unittest.TestCase):
    def setUp(self):
        self.analytics_db = tempfile.mktemp(suffix=".db")
        self.trades_db = tempfile.mktemp(suffix=".db")
        init_analytics_db(self.analytics_db)
        conn = sqlite3.connect(self.trades_db)
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, ticker TEXT,
            city TEXT, side TEXT, limit_price INTEGER, fill_price INTEGER,
            fill_qty INTEGER, fill_time TEXT, settlement_outcome TEXT, pnl REAL
        )""")
        trades = [
            ("o1", "T1", "nyc", "buy_yes", 50, 50, 10, "2026-03-14T10:00:00", "win", 4.0),
            ("o2", "T2", "nyc", "buy_no", 30, 30, 10, "2026-03-14T11:00:00", "win", 7.0),
            ("o3", "T3", "chicago", "buy_yes", 60, 60, 10, "2026-03-14T12:00:00", "loss", -6.0),
            ("o4", "T4", "chicago", "buy_no", 40, 40, 10, "2026-03-14T13:00:00", "win", 6.0),
            ("o5", "T5", "miami", "buy_yes", 70, 70, 10, "2026-03-14T14:00:00", "loss", -7.0),
        ]
        for t in trades:
            conn.execute(
                "INSERT INTO trades (order_id,ticker,city,side,limit_price,fill_price,fill_qty,fill_time,settlement_outcome,pnl) VALUES (?,?,?,?,?,?,?,?,?,?)", t)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.analytics_db)
        os.unlink(self.trades_db)

    def test_compute_daily_stats(self):
        from analytics.optimizer import compute_daily_stats
        compute_daily_stats(self.trades_db, self.analytics_db)
        conn = sqlite3.connect(self.analytics_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM daily_stats WHERE date='2026-03-14'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["wins"], 3)
        self.assertEqual(row["losses"], 2)
        self.assertAlmostEqual(row["hit_rate"], 0.6)
        self.assertAlmostEqual(row["net_pnl"], 4.0)

    def test_compute_bucket_stats(self):
        from analytics.optimizer import compute_bucket_stats
        compute_bucket_stats(self.trades_db, self.analytics_db)
        conn = sqlite3.connect(self.analytics_db)
        conn.row_factory = sqlite3.Row
        city_rows = conn.execute(
            "SELECT * FROM bucket_stats WHERE bucket_type='city'"
        ).fetchall()
        conn.close()
        cities = {r["bucket_value"]: r for r in city_rows}
        self.assertIn("nyc", cities)
        self.assertEqual(cities["nyc"]["wins"], 2)


class TestRecommendations(unittest.TestCase):
    def setUp(self):
        self.analytics_db = tempfile.mktemp(suffix=".db")
        self.trades_db = tempfile.mktemp(suffix=".db")
        init_analytics_db(self.analytics_db)
        conn = sqlite3.connect(self.trades_db)
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, ticker TEXT,
            city TEXT, side TEXT, limit_price INTEGER, fill_price INTEGER,
            fill_qty INTEGER, fill_time TEXT, settlement_outcome TEXT, pnl REAL
        )""")
        for i in range(25):
            outcome = "win" if i < 2 else "loss"
            pnl = 3.0 if outcome == "win" else -5.0
            conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (i, f"o{i}", f"T{i}", "miami", "buy_yes", 50, 50, 10,
                          f"2026-03-14T{10 + i // 4}:00:00", outcome, pnl))
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.analytics_db)
        os.unlink(self.trades_db)

    def test_generates_city_exclusion(self):
        from analytics.optimizer import generate_recommendations
        generate_recommendations(self.trades_db, self.analytics_db)
        conn = sqlite3.connect(self.analytics_db)
        conn.row_factory = sqlite3.Row
        recs = conn.execute("SELECT * FROM recommendations WHERE param_name='city_exclusion'").fetchall()
        conn.close()
        self.assertTrue(len(recs) > 0)
        self.assertIn("miami", recs[0]["suggested_value"])


if __name__ == "__main__":
    unittest.main()
