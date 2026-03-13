#!/bin/bash
cd ~/Projects/polymarket-weather-bot
source .venv/bin/activate
python -m kalshi.price_watcher
