#!/usr/bin/env python3
"""Elo rating model + backtest + edge finder, built on data/games.db.

  python3 model.py backtest              # Elo vs. the betting market on finished games
  python3 model.py picks                 # today's games: model vs. market, flags edges
  python3 model.py picks --date 2026-11-07 --min-edge 0.04 --bankroll 500

Elo is a simple, well-known rating method: every team has a number, it moves
after each game based on the surprise of the result. It is a starting point,
not a proven money-maker: always check `backtest` first.
"""
import argparse
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from injuries import team_impact, team_notes
from bet_math import american_to_decimal, devig, ev_per_dollar, kelly

DB = Path(__file__).parent / "data" / "games.db"
# per-league: home-court/ice edge in Elo points, K factor (how fast ratings move)
PARAMS = {
    "NHL": dict(home=35, k=6),
    "NBA": dict(home=70, k=20),
    "WNBA": dict(home=60, k=20),
    "NCAA Men": dict(home=90, k=24),
    "NCAA Women": dict(home=90, k=24),
    "MLB": dict(home=25, k=4),
}


def expected(r_home, r_away, home_adv):
    return 1 / (1 + 10 ** (-(r_home + home_adv - r_away) / 400))


def load(con, where="", args=()):
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM games %s ORDER BY game_date, start_time_utc" % where, args).fetchall()


def run_elo(rows):
    """Walk finished games in order. Returns ratings and per-game pre-game predictions."""
    ratings = defaultdict(lambda: 1500.0)
    games_played = defaultdict(int)
    preds = {}
    for g in rows:
        p = PARAMS[g["league"]]
        key_h, key_a = (g["league"], g["home_team"]), (g["league"], g["away_team"])
        neutral = g["neutral_site"]
        prob = expected(ratings[key_h], ratings[key_a], 0 if neutral else p["home"])
        preds[g["game_id"]] = prob
        if g["completed"] and g["home_score"] is not None and g["home_score"] != g["away_score"]:
            result = 1.0 if g["home_score"] > g["away_score"] else 0.0
            margin = abs(g["home_score"] - g["away_score"])
            mult = math.log(margin + 1) + 1  # bigger wins move ratings more, with diminishing returns
            delta = p["k"] * mult * (result - prob)
            ratings[key_h] += delta
            ratings[key_a] -= delta
            games_played[key_h] += 1
            games_played[key_a] += 1
    run_elo.games_played = games_played
    return ratings, preds


def market_prob(g):
    try:
        a, b = float(g["home_ml"]), float(g["away_ml"])
    except (TypeError, ValueError):
        return None
    return devig(a, b)[0]


def win_prob(con, g, ratings, use_injuries=True):
    """Home win probability from Elo. For NBA/WNBA, optionally lowers each team's rating by the
    estimated strength lost to injuries (see injuries.team_impact: an uncalibrated estimate)."""
    lg = g["league"]
    rh, ra = ratings[(lg, g["home_team"])], ratings[(lg, g["away_team"])]
    if use_injuries:
        rh -= team_impact(con, lg, g["home_team"])["elo"]
        ra -= team_impact(con, lg, g["away_team"])["elo"]
    return expected(rh, ra, 0 if g["neutral_site"] else PARAMS[lg]["home"])


def cmd_log(_a):
    """Save today's/tomorrow's pre-game predictions so they can be scored once games finish."""
    con = sqlite3.connect(str(DB))
    con.execute("CREATE TABLE IF NOT EXISTS predictions (game_id PRIMARY KEY, league, logged_at, "
                "elo_prob, elo_inj_prob, market_prob)")
    ratings, _ = run_elo(load(con, "WHERE completed = 1"))
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    n = 0
    for g in load(con, "WHERE completed = 0 AND start_time_utc > ?", (now[:16],)):
        if g["league"] not in PARAMS:
            continue
        pm = market_prob(g)
        if pm is None:
            continue  # only log games that have a line to compare against
        con.execute("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?)", (
            g["game_id"], g["league"], now, win_prob(con, g, ratings, False), win_prob(con, g, ratings, True), pm))
        n += 1
    con.commit()
    print("Logged %d pre-game predictions." % n)


def cmd_scorecard(_a):
    """Score logged predictions on finished games: Elo vs Elo+injuries vs the market."""
    con = sqlite3.connect(str(DB))
    try:
        rows = con.execute("SELECT p.league, p.elo_prob, p.elo_inj_prob, p.market_prob, g.home_score, g.away_score "
                           "FROM predictions p JOIN games g ON g.game_id = p.game_id "
                           "WHERE g.completed = 1 AND g.home_score != g.away_score").fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        print("No finished games with logged predictions yet. Let the 15-minute job run for a few weeks.")
        return
    ll = lambda p, y: -(y * math.log(min(max(p, 0.01), 0.99)) + (1 - y) * math.log(1 - min(max(p, 0.01), 0.99)))
    by = defaultdict(list)
    for lg, pe, pi, pm, hs, as_ in rows:
        by[lg].append((pe, pi, pm, 1.0 if hs > as_ else 0.0))
    print("%-11s %6s %12s %14s %12s  (log loss, lower is better)" % ("League", "Games", "Elo", "Elo+injuries", "Market"))
    for lg, v in by.items():
        n = len(v)
        print("%-11s %6d %12.4f %14.4f %12.4f" % (lg, n, sum(ll(a, y) for a, b, c, y in v) / n,
              sum(ll(b, y) for a, b, c, y in v) / n, sum(ll(c, y) for a, b, c, y in v) / n))
    print("\nElo+injuries below Elo = the injury adjustment helps. Market below both = no edge over the book yet.")
    print("Need roughly 100+ games per league before trusting any gap.")


def cmd_backtest(_a):
    con = sqlite3.connect(str(DB))
    rows = load(con)
    _, preds = run_elo(rows)
    stats = defaultdict(lambda: dict(n=0, hit=0, ll_m=0.0, ll_e=0.0, br_m=0.0, br_e=0.0, nm=0))
    for g in rows:
        if not g["completed"] or g["home_score"] is None or g["home_score"] == g["away_score"]:
            continue
        y = 1.0 if g["home_score"] > g["away_score"] else 0.0
        pe = min(max(preds[g["game_id"]], 0.01), 0.99)
        s = stats[g["league"]]
        s["n"] += 1
        s["hit"] += (pe > 0.5) == (y == 1.0)
        s["ll_e"] += -(y * math.log(pe) + (1 - y) * math.log(1 - pe))
        s["br_e"] += (pe - y) ** 2
        pm = market_prob(g)
        if pm is not None:
            pm = min(max(pm, 0.01), 0.99)
            # compare on the same games only
            s["nm"] += 1
            s["ll_m"] += -(y * math.log(pm) + (1 - y) * math.log(1 - pm))
            s["br_m"] += (pm - y) ** 2
            s.setdefault("ll_e_m", 0.0)
            s["ll_e_m"] += -(y * math.log(pe) + (1 - y) * math.log(1 - pe))
    print("%-11s %6s %8s | %6s %12s %12s   (log loss: lower is better)" %
          ("League", "Games", "Elo win%", "w/odds", "Elo logloss", "Mkt logloss"))
    for lg, s in stats.items():
        nm = s["nm"]
        print("%-11s %6d %7.1f%% | %6d %12s %12s" % (
            lg, s["n"], 100 * s["hit"] / s["n"], nm,
            "%.4f" % (s.get("ll_e_m", 0) / nm) if nm else "-",
            "%.4f" % (s["ll_m"] / nm) if nm else "-"))
    print("\nIf Elo log loss is not below the market's, the model has no edge over the book: do not bet it.")
    print("Odds only exist for games pulled with lines; early-season sample sizes are small.")


def cmd_picks(a):
    con = sqlite3.connect(str(DB))
    day = a.date or date.today().isoformat()
    history = load(con, "WHERE game_date < ? OR completed = 1", (day,))
    ratings, _ = run_elo([g for g in history if g["completed"]])
    todays = load(con, "WHERE game_date = ? AND completed = 0", (day,))
    print("%s: %d upcoming games\n" % (day, len(todays)))
    flagged = 0
    gp = run_elo.games_played
    for g in todays:
        p = PARAMS[g["league"]]
        if min(gp[(g["league"], g["home_team"])], gp[(g["league"], g["away_team"])]) < a.min_games:
            continue  # ratings for teams with few games are noise
        ph = win_prob(con, g, ratings, not a.no_injuries)
        pm = market_prob(g)
        if pm is None:
            continue
        ph = (1 - a.blend) * pm + a.blend * ph  # shrink toward the market: it is usually the better forecaster
        for side, prob_model, prob_mkt, odds, team in (
            ("home", ph, pm, g["home_ml"], g["home_team"]),
            ("away", 1 - ph, 1 - pm, g["away_ml"], g["away_team"]),
        ):
            edge = prob_model - prob_mkt
            ev = ev_per_dollar(float(odds), prob_model)
            if edge > 0.15:
                continue  # an edge this big almost always means the model is wrong, not the book
            if edge >= a.min_edge and ev > 0:
                flagged += 1
                stake = kelly(float(odds), prob_model, 0.25)
                msg = "  %-10s %-28s %s  model %.1f%% vs market %.1f%%  edge %+.1f pts  EV %+.3f/$1  stake %.1f%%" % (
                    g["league"], team, odds, prob_model * 100, prob_mkt * 100, edge * 100, ev, stake * 100)
                if a.bankroll:
                    msg += " ($%.2f)" % (stake * a.bankroll)
                print(msg)
                for tm in (g["home_team"], g["away_team"]):
                    notes = team_notes(con, g["league"], tm)
                    if notes:
                        print("      %s not playing/uncertain: %s" % (tm, "; ".join(notes[:8])))
                    imp = team_impact(con, g["league"], tm)
                    if imp["elo"]:
                        print("      %s injury penalty: -%.0f Elo (%.0f min, %.0f pts/game missing)" % (tm, imp["elo"], imp["min_lost"], imp["pts_lost"]))
                if g["league"].startswith("NCAA"):
                    print("      (no injury data exists for college games: check team news yourself)")
    if not flagged:
        print("No games clear the %.0f-point edge threshold. Passing is a valid result." % (a.min_edge * 100))
    print("\nModel = past scores + (NBA/WNBA only) an estimated injury penalty. No NHL/MLB goalie or pitcher, rest or lineup data. Leads to check, not locks.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backtest").set_defaults(fn=cmd_backtest)
    sub.add_parser("log").set_defaults(fn=cmd_log)
    sub.add_parser("scorecard").set_defaults(fn=cmd_scorecard)
    p = sub.add_parser("picks"); p.set_defaults(fn=cmd_picks)
    p.add_argument("--date"); p.add_argument("--min-edge", type=float, default=0.05)
    p.add_argument("--bankroll", type=float)
    p.add_argument("--no-injuries", action="store_true", help="ignore the injury penalty")
    p.add_argument("--min-games", type=int, default=10, help="skip teams with fewer rated games")
    p.add_argument("--blend", type=float, default=0.3, help="weight on Elo vs. market (0=market only)")
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
