#!/bin/bash
cd ~/Projects/polymarket-weather-bot && rsync -avz \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude 'node_modules' \
  --exclude '.DS_Store' \
  --exclude 'daemon.pid' \
  --exclude 'logs/' \
  --exclude '.worktrees' \
  --exclude '.venv' \
  -e 'ssh -i ~/.ssh/hetzner_ed25519' ./ edede@5.78.146.1:~/polymarket-weather-bot/
