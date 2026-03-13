#!/bin/bash
cd ~/Projects/polymarket-weather-bot
source .venv/bin/activate
python -m kalshi.position_manager >> logs/position_manager.log 2>&1
