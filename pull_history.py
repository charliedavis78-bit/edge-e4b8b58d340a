#!/usr/bin/env python3
"""Pull a past stretch of finished games into data/history/games.db (separate from the live data).
Safe to stop and rerun: games are upserted by id.

  python3 pull_history.py --start 2025-10-01 --end 2026-06-30
"""
import argparse
import time
from datetime import date, datetime, timedelta

import pull_games
from pathlib import Path

pull_games.DATA_DIR = Path(__file__).parent / "data" / "history"

ap = argparse.ArgumentParser()
ap.add_argument("--start", required=True)
ap.add_argument("--end", required=True)
ap.add_argument("--leagues", nargs="+", default=["nhl", "nba", "ncaam", "ncaaw"])
a = ap.parse_args()
d = datetime.strptime(a.start, "%Y-%m-%d").date()
end = datetime.strptime(a.end, "%Y-%m-%d").date()
total = 0
while d <= end:
    rows = pull_games.pull_day(d, a.leagues)
    if rows:
        pull_games.save_db(rows)
        total += len(rows)
    if d.day == 1 or d == end:
        print("%s  total so far %d" % (d, total), flush=True)
    time.sleep(0.25)
    d += timedelta(days=1)
print("DONE %d games" % total, flush=True)
