#!/usr/bin/env python3
"""Tune the Elo model on last season (data/history/games.db), then test on the current season (data/games.db).
Prequential scoring: every game is predicted before it is used to update ratings. Log loss, lower is better.

  python3 tune.py
"""
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from model import PARAMS, expected

D = Path(__file__).parent / "data"


def games(db, league):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM games WHERE league=? AND completed=1 AND home_score IS NOT NULL "
                       "AND home_score != away_score ORDER BY game_date, start_time_utc", (league,)).fetchall()


def ll(p, y):
    p = min(max(p, 0.01), 0.99)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def run(rows, home, k, ratings=None, skip=0, margin=True):
    """Returns (mean log loss over rows after `skip`, ratings, n_scored, accuracy)."""
    r = defaultdict(lambda: 1500.0, ratings or {})
    tot = n = hit = 0
    for i, g in enumerate(rows):
        h, a = g["home_team"], g["away_team"]
        p = expected(r[h], r[a], 0 if g["neutral_site"] else home)
        y = 1.0 if g["home_score"] > g["away_score"] else 0.0
        if i >= skip:
            tot += ll(p, y)
            n += 1
            hit += (p > 0.5) == (y == 1.0)
        mult = (math.log(abs(g["home_score"] - g["away_score"]) + 1) + 1) if margin else 1.0
        d = k * mult * (y - p)
        r[h] += d
        r[a] -= d
    return (tot / n if n else float("nan")), r, n, (hit / n if n else float("nan"))



def main():
  for lg in ["NHL", "NBA", "NCAA Men", "NCAA Women"]:
      hist, live = games(D / "history" / "games.db", lg), games(D / "games.db", lg)
      if len(hist) < 200:
          print("%s: only %d last-season games, skipping" % (lg, len(hist)))
          continue
      cur = PARAMS[lg]
      skip = len(hist) // 5  # warm-up: don't score the first 20% of the season
      base, _, n, acc = run(hist, cur["home"], cur["k"], skip=skip)
      print("\n%s  last season %d games (scored %d)  coin flip 0.6931" % (lg, len(hist), n))
      print("  current settings (home %d, K %d): log loss %.4f, picked winner %.1f%%" % (cur["home"], cur["k"], base, 100 * acc))
      best = None
      for home in (0, 20, 35, 50, 70, 90, 110):
          for k in (3, 6, 10, 15, 20, 30, 40):
              v = run(hist, home, k, skip=skip)[0]
              if best is None or v < best[0]:
                  best = (v, home, k)
      print("  best on last season (home %d, K %d): log loss %.4f  (%+.4f vs current)" % (best[1], best[2], best[0], best[0] - base))
      # Out-of-sample check on the current season, with and without carrying ratings over.
      _, end_r, _, _ = run(hist, best[1], best[2])
      print("  current season %d games, using the tuned settings:" % len(live))
      flat = run(live, best[1], best[2])[0]
      print("    start everyone at 1500 (what the live page does now): %.4f" % flat)
      for keep in (0.5, 0.67, 0.8):
          start = {t: 1500 + keep * (v - 1500) for t, v in end_r.items()}
          print("    carry last season's ratings, keep %d%%: %.4f  (%+.4f)" % (100 * keep, run(live, best[1], best[2], ratings=start)[0], run(live, best[1], best[2], ratings=start)[0] - flat))
      cur_live = run(live, cur["home"], cur["k"])[0]
      print("    live page's current settings, fresh start: %.4f" % cur_live)


if __name__ == "__main__":
    main()
