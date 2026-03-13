#!/bin/bash
cd ~/Projects/polymarket-weather-bot
source .venv/bin/activate
python -m weather.resolver >> logs/resolver.log 2>&1
