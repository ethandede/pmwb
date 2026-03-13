#!/bin/bash
cd /opt/weather-bot
source venv/bin/activate

# Kill existing dashboard
pkill -f "uvicorn dashboard.api" 2>/dev/null || true
sleep 1

# Start FastAPI dashboard
nohup uvicorn dashboard.api:app --host 127.0.0.1 --port 8501 >> logs/dashboard.log 2>&1 &
echo "Dashboard started (PID $!)"
