#!/usr/bin/env python3
"""Tune the points-rating model on last season (data/history/games.db), then check it on this season
(data/games.db). Prequential scoring: every game is predicted before it is used to update ratings.
Log loss, lower is better (a coin flip scores 0.6931).

  python3 pull_history.py --start 2025-10-01 --end 2026-04-20   # once, about 3 minutes
  python3 tune.py
"""
import itertools
import math
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from model import PARAMS

D = Path(__file__).parent / "data"
GRID = {
    "NBA": dict(hca=[0.5, 1, 1.5, 2, 2.5], k=[.01, .015, .02, .03, .05], scale=[4, 4.5, 5, 6], rest=[0, 1, 1.5, 2.5]),
    "NCAA Men": dict(hca=[2.5, 3, 3.5, 4], k=[.06, .08, .1, .14], scale=[5, 5.5, 6, 6.5], rest=[0]),
    "NHL": dict(hca=[0, .05, .1, .2], k=[.005, .01, .02, .03], scale=[1, 1.25, 1.5, 2], rest=[0, .1, .15, .25]),
}


def games(db, league):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM games WHERE league=? AND completed=1 AND home_score IS NOT NULL "
                       "AND home_score != away_score ORDER BY game_date, start_time_utc", (league,)).fetchall()


def ll(p, y):
    p = min(max(p, 0.01), 0.99)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def run(rows, hca, k, scale, rest=0.0, cap=25, ratings=None, skip=0):
    """Returns (mean log loss over rows after `skip`, final ratings, n scored, share of winners picked)."""
    r = defaultdict(float, ratings or {})
    last = {}
    tot = n = hit = 0
    for i, g in enumerate(rows):
        h, a = g["home_team"], g["away_team"]
        d = date.fromisoformat(g["game_date"])
        adj = (-rest if h in last and (d - last[h]).days == 1 else 0) + (rest if a in last and (d - last[a]).days == 1 else 0)
        pm = r[h] - r[a] + (0 if g["neutral_site"] else hca) + adj
        p = 1 / (1 + math.exp(-pm / scale))
        y = 1.0 if g["home_score"] > g["away_score"] else 0.0
        if i >= skip:
            tot += ll(p, y)
            n += 1
            hit += (p > 0.5) == (y == 1.0)
        m = max(-cap, min(cap, g["home_score"] - g["away_score"]))
        e = k * (m - pm)
        r[h] += e
        r[a] -= e
        last[h] = last[a] = d
    return (tot / n if n else float("nan")), dict(r), n, (hit / n if n else float("nan"))


def main():
    for lg, grid in GRID.items():
        hist, live = games(D / "history" / "games.db", lg), games(D / "games.db", lg)
        if len(hist) < 200:
            print("%s: only %d last-season games, skipping" % (lg, len(hist)))
            continue
        cur = PARAMS[lg]
        skip = len(hist) // 5  # warm-up: don't score the first 20% of the season
        base, _, n, acc = run(hist, cur["hca"], cur["k"], cur["scale"], cur["rest"], cur["cap"], skip=skip)
        print("\n%s  last season %d games (scored %d)" % (lg, len(hist), n))
        print("  current settings: log loss %.4f, picked winner %.1f%%" % (base, 100 * acc))
        best = min((run(hist, h, k, s, re, cur["cap"], skip=skip)[0], h, k, s, re)
                   for h, k, s, re in itertools.product(grid["hca"], grid["k"], grid["scale"], grid["rest"]))
        print("  best on last season: hca %s k %s scale %s rest %s  log loss %.4f (%+.4f vs current)" % (
            best[1], best[2], best[3], best[4], best[0], best[0] - base))
        _, end_r, _, _ = run(hist, cur["hca"], cur["k"], cur["scale"], cur["rest"], cur["cap"])
        print("  this season, %d games, current settings, by share of last season's rating kept:" % len(live))
        for keep in (0, 0.5, 0.67, 0.8):
            start = {t: keep * v for t, v in end_r.items()}
            print("    keep %3d%%: %.4f" % (100 * keep, run(live, cur["hca"], cur["k"], cur["scale"], cur["rest"], cur["cap"], ratings=start)[0]))


if __name__ == "__main__":
    main()
