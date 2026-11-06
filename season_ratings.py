#!/usr/bin/env python3
"""Build log/season_start.json: last season's final Elo ratings, pulled back toward 1500, used as this
season's starting ratings. Run once per season after pulling last season into data/history/games.db.

  python3 season_ratings.py
"""
import json
import sqlite3
from pathlib import Path

from tune import games, run  # noqa: F401  (tune.py's report is skipped when imported)
from model import PARAMS

HERE = Path(__file__).parent
KEEP = {"NHL": 0.67, "NCAA Men": 0.67, "NCAA Women": 0.67}  # NBA is left out: carryover made it worse in testing
out = {}
for lg, keep in KEEP.items():
    rows = games(HERE / "data" / "history" / "games.db", lg)
    _, r, _, _ = run(rows, PARAMS[lg]["home"], PARAMS[lg]["k"])
    out[lg] = {t: round(1500 + keep * (v - 1500), 1) for t, v in r.items()}
    print(lg, len(out[lg]), "teams")
(HERE / "log").mkdir(exist_ok=True)
(HERE / "log" / "season_start.json").write_text(json.dumps(out, sort_keys=True, indent=0))
