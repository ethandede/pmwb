---
name: optimize
description: Performance briefing and parameter optimization for the weather trading bot. Reads analytics.db, shows rolling stats, active recommendations, and proposes parameter changes.
user_invocable: true
---

# Weather Bot Optimizer

Read the analytics database and provide a performance briefing with parameter recommendations.

## Steps

1. Read `data/analytics.db` on the Hetzner server via SSH:
   ```bash
   ssh -i ~/.ssh/hetzner_ed25519 root@5.78.146.1 'cd /home/edede/polymarket-weather-bot && PYTHONPATH=. .venv/bin/python3 -c "
   import sqlite3, json
   conn = sqlite3.connect(\"data/analytics.db\")
   conn.row_factory = sqlite3.Row

   # Rolling stats
   stats = conn.execute(\"SELECT * FROM daily_stats ORDER BY date DESC LIMIT 7\").fetchall()

   # City breakdowns
   cities = conn.execute(\"SELECT * FROM bucket_stats WHERE bucket_type=\\\"city\\\" ORDER BY hit_rate ASC\").fetchall()

   # Recommendations
   recs = conn.execute(\"SELECT * FROM recommendations WHERE status=\\\"pending\\\" ORDER BY confidence DESC\").fetchall()

   conn.close()
   print(json.dumps({
       \"daily\": [dict(r) for r in stats],
       \"cities\": [dict(r) for r in cities],
       \"recommendations\": [dict(r) for r in recs],
   }))
   "'
   ```

2. Present a conversational briefing:
   - Rolling hit rate and P&L trend (improving, stable, degrading?)
   - Best/worst performing cities
   - Active parameter recommendations with supporting data
   - Specific change proposals

3. If user approves a parameter change:
   - Update `config.py` locally
   - Deploy to server: `rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519' config.py edede@5.78.146.1:~/polymarket-weather-bot/config.py`
   - Clear cache: `ssh -i ~/.ssh/hetzner_ed25519 root@5.78.146.1 'rm -rf /home/edede/polymarket-weather-bot/__pycache__'`
   - Mark recommendation as applied in analytics.db

## Key files
- Analytics DB: `data/analytics.db`
- Config: `config.py`
- Trades: `data/trades.db`
- Server: `5.78.146.1` via `ssh -i ~/.ssh/hetzner_ed25519`
