#!/bin/bash
cd ~/Projects/polymarket-weather-bot
source venv/bin/activate
python main.py >> logs/cron.log 2>&1
