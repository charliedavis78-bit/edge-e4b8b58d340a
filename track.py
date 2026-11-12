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

# elo_prob / elo_inj_prob: the retired Elo model (rows before 2026-11-12). model_prob: the points-rating model.
PRED_COLS = ["game_id", "league", "logged_at", "elo_prob", "elo_inj_prob", "market_prob", "model_prob"]
PICK_COLS = ["game_id", "team", "league", "vs", "start", "odds", "model", "market", "edge", "inj_dependent", "logged_at"]


# A league's picks are shown only after its model has been checked on this many finished games.
MIN_GRADED = 100
# ...and only if blending the model in lowers log loss below the market's by at least this much.
MIN_GAIN = 0.003


def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS predictions (game_id PRIMARY KEY, league, logged_at, "
                "elo_prob, elo_inj_prob, market_prob, model_prob)")
    cols = [r[1] for r in con.execute("PRAGMA table_info(predictions)")]
    if "model_prob" not in cols:  # databases created before the points model
        con.execute("ALTER TABLE predictions ADD COLUMN model_prob")
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
                vals = [r.get(c) if r.get(c) not in ("", None) else None for c in cols]
                con.execute("INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))), vals)
                if table == "predictions" and r.get("model_prob"):
                    con.execute("UPDATE predictions SET model_prob = ? WHERE game_id = ? AND model_prob IS NULL",
                                (r["model_prob"], r["game_id"]))
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


def _ll(p, y):
    p = min(max(p, .01), .99)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _logit(p):
    p = min(max(p, .01), .99)
    return math.log(p / (1 - p))


def blend(model, market, w):
    """Mix the model into the market price, in log-odds. w=0 is the market alone."""
    return 1 / (1 + math.exp(-((1 - w) * _logit(market) + w * _logit(model))))


WEIGHTS = [0.0] + [i / 20 for i in range(1, 11)]  # model share tried in the blend, 0 to 0.5
WARMUP = 30   # first games per league only train the weight, they are not graded
MIN_Z = 2.0   # the gain over the odds must be about 2 standard errors above zero


def league_status(con):
    """Per league: is the model proven to add anything to the betting market?

    Out-of-sample check, in game order: each game is graded with the blend weight fitted only on the
    games before it, so the weight never sees the game it is graded on. A league is proven when it has
    MIN_GRADED+ finished games and that out-of-sample gain over the odds is above MIN_GAIN and about
    two standard errors above zero. In testing on simulated data a model with no real edge passed this
    under 1% of the time (a version that fitted and graded on the same games passed 11-25%).
    Proven leagues use the weight fitted on all their games; every other league gets weight 0 (no picks)."""
    ensure(con)
    rows = con.execute("SELECT p.league, p.model_prob, p.market_prob, g.home_score, g.away_score FROM predictions p "
                       "JOIN games g ON g.game_id = p.game_id WHERE g.completed = 1 AND g.home_score != g.away_score "
                       "AND p.model_prob IS NOT NULL AND p.market_prob IS NOT NULL "
                       "ORDER BY g.start_time_utc, g.game_id").fetchall()
    by = {}
    for lg, pm, pk, hs, as_ in rows:
        by.setdefault(lg, []).append((float(pm), float(pk), 1.0 if hs > as_ else 0.0))
    out = {}
    for lg, v in sorted(by.items()):
        n = len(v)
        cum = [0.0] * len(WEIGHTS)
        gains = []
        for i, (a, b, y) in enumerate(v):
            if i >= WARMUP:
                w = WEIGHTS[min(range(len(WEIGHTS)), key=lambda j: cum[j])]
                gains.append(_ll(b, y) - _ll(blend(a, b, w), y))
            for j, w in enumerate(WEIGHTS):
                cum[j] += _ll(blend(a, b, w), y)
        g_n = len(gains)
        gain = sum(gains) / g_n if g_n else 0.0
        sd = (sum((x - gain) ** 2 for x in gains) / (g_n - 1)) ** 0.5 if g_n > 1 else 0.0
        z = gain / (sd / g_n ** 0.5) if sd else 0.0
        best_j = min(range(len(WEIGHTS)), key=lambda j: cum[j])
        proven = n >= MIN_GRADED and gain > MIN_GAIN and z > MIN_Z
        if proven:
            verdict = "Proven: picks on"
        elif n < MIN_GRADED:
            verdict = "Testing: %d of %d games" % (n, MIN_GRADED)
        else:
            verdict = "Not beating the odds: no picks"
        out[lg] = {"n": n, "model": round(sum(_ll(a, y) for a, b, y in v) / n, 4),
                   "market": round(sum(_ll(b, y) for a, b, y in v) / n, 4),
                   "gain": round(gain, 4), "z": round(z, 2),
                   "w": WEIGHTS[best_j] if proven else 0.0, "proven": proven, "verdict": verdict}
    return out


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
    pr = con.execute("SELECT p.model_prob, p.market_prob, g.home_score, g.away_score FROM predictions p "
                     "JOIN games g ON g.game_id = p.game_id WHERE g.completed = 1 AND g.home_score != g.away_score "
                     "AND p.model_prob IS NOT NULL AND p.market_prob IS NOT NULL").fetchall()
    scored = None
    if pr:
        pr = [(float(a), float(b), hs, as_) for a, b, hs, as_ in pr]
        scored = {"n": len(pr),
                  "model": round(sum(ll(a, 1.0 if hs > as_ else 0.0) for a, b, hs, as_ in pr) / len(pr), 4),
                  "market": round(sum(ll(b, 1.0 if hs > as_ else 0.0) for a, b, hs, as_ in pr) / len(pr), 4)}
    return {"n": n, "wins": wins, "pnl": round(sum(r["pnl"] for r in out), 2), "pending": pending,
            "by_league": {k: {"n": v["n"], "w": v["w"], "pnl": round(v["pnl"], 2)} for k, v in by.items()},
            "recent": out[:12], "scored": scored, "leagues": league_status(con)}


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
