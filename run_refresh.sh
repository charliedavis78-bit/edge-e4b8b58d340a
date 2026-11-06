#!/bin/bash
# Refresh today + tomorrow's games/lines/scores, then injuries. Runs every 15 min via launchd.
cd "$(dirname "$0")" || exit 1
mkdir -p data
{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S')"
  /usr/bin/python3 pull_games.py --days-back 1 --days-ahead 1 --quiet
  /usr/bin/python3 injuries.py
  /usr/bin/python3 model.py log
} >> data/refresh.log 2>&1
