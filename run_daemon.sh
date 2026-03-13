#!/bin/bash
# Weather Edge Daemon — runs continuously in background
# Start: ./run_daemon.sh
# Stop:  kill $(cat daemon.pid)
# Logs:  tail -f logs/daemon.log

cd ~/Projects/polymarket-weather-bot
source .venv/bin/activate

# Check if already running
if [ -f daemon.pid ] && kill -0 $(cat daemon.pid) 2>/dev/null; then
    echo "Daemon already running (PID $(cat daemon.pid))"
    exit 1
fi

mkdir -p logs

echo "Starting Weather Edge Daemon..."
nohup python daemon.py >> logs/daemon.log 2>&1 &
echo $! > daemon.pid
echo "Daemon started (PID $!). Logs: logs/daemon.log"
