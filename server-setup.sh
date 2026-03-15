#!/bin/bash
# Run this ON the server: bash ~/polymarket-weather-bot/server-setup.sh

set -e

# Create systemd service
sudo tee /etc/systemd/system/weather-dashboard.service << 'EOF'
[Unit]
Description=Weather Edge Dashboard
After=network.target

[Service]
User=edede
WorkingDirectory=/home/edede/polymarket-weather-bot
ExecStart=/usr/bin/python3 -m uvicorn dashboard.api:app --host 127.0.0.1 --port 8501
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Kill any orphaned uvicorn
pkill -f "uvicorn dashboard.api" 2>/dev/null || true

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable weather-dashboard
sudo systemctl start weather-dashboard

echo "Dashboard service status:"
sudo systemctl status weather-dashboard --no-pager
