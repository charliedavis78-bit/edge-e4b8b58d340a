#!/usr/bin/env python3
"""Permanent track record: saves the model's predictions and flagged picks to CSV files in log/
(so they survive a cache wipe) and scores them once games finish.

  python3 track.py restore   # load log/*.csv into data/games.db (run before logging)
  python3 track.py export    # write data/games.db tables out to log/*.csv (run after building)
"""
import csv
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "data" / "games.db"
LOG = HERE / "log"

PRED_COLS = ["game_id", "league", "logged_at", "elo_prob", "elo_inj_prob", "market_prob"]
PICK_COLS = ["game_id", "team", "league", "vs", "start", "odds", "model", "market", "edge", "inj_dependent", "logged_at"]


def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS predictions (game_id PRIMARY KEY, league, logged_at, "
                "elo_prob, elo_inj_prob, market_prob)")
    con.execute("CREATE TABLE IF NOT EXISTS picks_log (game_id, team, league, vs, start, odds, model, market, "
                "edge, inj_dependent, logged_at, PRIMARY KEY (game_id, team))")


def restore(con):
    """Bring saved rows back. Never overwrites a row already in the database."""
    ensure(con)
    for name, table, cols in (("predictions.csv", "predictions", PRED_COLS), ("picks.csv", "picks_log", PICK_COLS)):
        f = LOG / name
        if not f.exists():
            continue
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                con.execute("INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))),
                            [r[c] if r[c] != "" else None for c in cols])
    con.commit()


def save_picks(con, picks):
    """Record flagged picks. Only games that have not started, so the record is the pre-game call."""
    ensure(con)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for p in picks:
        if not p["start"] or p["start"][:16] <= now[:16]:
            continue
        con.execute("INSERT OR REPLACE INTO picks_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (p["game"], p["team"], p["league"], p["vs"], p["start"], str(p["odds"]), p["model"],
                     p["market"], p["edge"], 1 if p["inj_dependent"] else 0, now))
    con.commit()


def export(con):
    ensure(con)
    LOG.mkdir(exist_ok=True)
    for name, table, cols in (("predictions.csv", "predictions", PRED_COLS), ("picks.csv", "picks_log", PICK_COLS)):
        rows = con.execute("SELECT %s FROM %s ORDER BY 1, 2" % (",".join(cols), table)).fetchall()
        with (LOG / name).open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(rows)


def _profit(odds, stake=10.0):
    o = float(odds)
    return stake * o / 100 if o > 0 else stake * 100 / abs(o)


def results(con):
    """Score flagged picks that have finished. Flat $10 bet on each."""
    ensure(con)
    rows = con.execute(
        "SELECT p.league, p.team, p.vs, p.odds, p.model, p.market, p.inj_dependent, g.game_date, "
        "g.home_team, g.home_score, g.away_score FROM picks_log p JOIN games g ON g.game_id = p.game_id "
        "WHERE g.completed = 1 AND g.home_score != g.away_score ORDER BY g.start_time_utc DESC").fetchall()
    out, by = [], {}
    for lg, team, vs, odds, model, market, inj, day, home, hs, as_ in rows:
        won = (hs > as_) if team == home else (as_ > hs)
        try:
            pnl = _profit(odds) if won else -10.0
        except ValueError:
            continue
        out.append({"day": day, "league": lg, "team": team, "vs": vs, "odds": odds, "won": bool(won),
                    "pnl": round(pnl, 2), "model": model, "market": market, "weak": bool(inj)})
        b = by.setdefault(lg, {"n": 0, "w": 0, "pnl": 0.0})
        b["n"] += 1
        b["w"] += 1 if won else 0
        b["pnl"] += pnl
    n = len(out)
    wins = sum(1 for r in out if r["won"])
    pending = con.execute("SELECT count(*) FROM picks_log p LEFT JOIN games g ON g.game_id = p.game_id "
                          "WHERE g.completed IS NOT 1").fetchone()[0]
    # Probability scoring on every logged prediction (model vs the market). Lower log loss = better.
    ll = lambda p, y: -(y * math.log(min(max(p, .01), .99)) + (1 - y) * math.log(1 - min(max(p, .01), .99)))
    pr = con.execute("SELECT p.elo_inj_prob, p.market_prob, g.home_score, g.away_score FROM predictions p "
                     "JOIN games g ON g.game_id = p.game_id WHERE g.completed = 1 AND g.home_score != g.away_score").fetchall()
    scored = None
    if pr:
        scored = {"n": len(pr),
                  "model": round(sum(ll(a, 1.0 if hs > as_ else 0.0) for a, b, hs, as_ in pr) / len(pr), 4),
                  "market": round(sum(ll(b, 1.0 if hs > as_ else 0.0) for a, b, hs, as_ in pr) / len(pr), 4)}
    return {"n": n, "wins": wins, "pnl": round(sum(r["pnl"] for r in out), 2), "pending": pending,
            "by_league": {k: {"n": v["n"], "w": v["w"], "pnl": round(v["pnl"], 2)} for k, v in by.items()},
            "recent": out[:12], "scored": scored}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    con = sqlite3.connect(str(DB))
    if cmd == "restore":
        restore(con)
        print("restored from log/")
    elif cmd == "export":
        export(con)
        print("exported to log/")
    else:
        print(__doc__)
