#!/bin/bash
cd ~/Projects/polymarket-weather-bot
source .venv/bin/activate
python -m kalshi.settler >> logs/settler.log 2>&1
