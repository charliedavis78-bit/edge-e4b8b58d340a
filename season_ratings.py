#!/usr/bin/env python3
"""Build log/season_start.json: last season's final points ratings x PARAMS[league]["keep"], used as this
season's starting ratings (the rest regresses to average). Run once per season after pulling last season
into data/history/games.db with pull_history.py.

  python3 season_ratings.py
"""
import json
from pathlib import Path

from tune import games, run
from model import PARAMS

HERE = Path(__file__).parent
out = {}
for lg, p in PARAMS.items():
    if not p["keep"]:
        continue
    rows = games(HERE / "data" / "history" / "games.db", lg)
    if not rows:
        continue
    _, r, _, _ = run(rows, p["hca"], p["k"], p["scale"], p["rest"], p["cap"])
    out[lg] = {t: round(p["keep"] * v, 3) for t, v in r.items()}
    print(lg, len(out[lg]), "teams")
(HERE / "log").mkdir(exist_ok=True)
(HERE / "log" / "season_start.json").write_text(json.dumps(out, sort_keys=True, indent=0))
