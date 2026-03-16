#!/bin/bash
cd ~/Projects/polymarket-weather-bot && rsync -avz --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude 'node_modules' \
  --exclude '.DS_Store' \
  --exclude 'daemon.pid' \
  --exclude 'logs/' \
  --exclude 'data/' \
  --exclude '.worktrees' \
  --exclude '.venv' \
  --exclude '.env' \
  -e 'ssh -i ~/.ssh/hetzner_ed25519' ./ edede@5.78.146.1:~/polymarket-weather-bot/ \
  && ssh -i ~/.ssh/hetzner_ed25519 edede@5.78.146.1 "\
    find ~/polymarket-weather-bot -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; \
    echo 'Cleared __pycache__'; \
    sudo systemctl restart weather-daemon weather-dashboard weather-price-watcher open-meteo-sync 2>/dev/null && echo 'Services restarted' || echo 'Service restart failed'; \
  "
