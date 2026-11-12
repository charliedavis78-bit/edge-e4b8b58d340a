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

from injuries import team_impact
from bet_math import devig, ev_per_dollar, kelly

DB = Path(__file__).parent / "data" / "games.db"
# Points-rating model (replaced the old win/loss Elo on 2026-11-12; Elo scored worse than this on both
# last season and the logged games). Every team has a rating in points (goals for NHL). Predicted home
# margin = home rating - away rating + home edge (- rest penalty on a back-to-back). Win chance =
# logistic(margin / scale). After each game both ratings move by k * (actual margin - predicted margin).
# Settings tuned on the 2025-26 season with tune.py (prequential: each game predicted before it is used).
#   hca: home edge in points; k: how fast ratings move; scale: margin-to-probability spread;
#   rest: points lost on the second night of a back-to-back; cap: largest margin counted (blowouts);
#   keep: share of last season's rating carried into this season (rest regresses to average).
PARAMS = {
    "NBA": dict(hca=1.0, k=0.02, scale=4.5, rest=1.0, cap=25, keep=0.5),
    "NCAA Men": dict(hca=3.0, k=0.08, scale=5.5, rest=0.0, cap=25, keep=0.67),
    "NCAA Women": dict(hca=3.0, k=0.08, scale=5.5, rest=0.0, cap=25, keep=0.0),  # untested: no lines, never picked
    "NHL": dict(hca=0.05, k=0.01, scale=1.0, rest=0.15, cap=4, keep=0.8),
    "WNBA": dict(hca=1.5, k=0.03, scale=4.5, rest=0.0, cap=25, keep=0.0),  # untested (off-season)
    "MLB": dict(hca=0.1, k=0.01, scale=2.5, rest=0.0, cap=6, keep=0.0),  # untested (off-season)
}


def _season_start():
    """Last season's final ratings x keep (log/season_start.json, built by season_ratings.py)."""
    try:
        import json
        d = json.loads((Path(__file__).parent / "log" / "season_start.json").read_text())
        return {(lg, team): r for lg, teams in d.items() for team, r in teams.items()}
    except (OSError, ValueError):
        return {}


START = _season_start()


def _sig(x):
    return 1 / (1 + math.exp(-max(min(x, 30), -30)))


def margin_to_prob(margin, league):
    return _sig(margin / PARAMS[league]["scale"])


def load(con, where="", args=()):
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM games %s ORDER BY game_date, start_time_utc" % where, args).fetchall()


def _rest_adj(g, last, p):
    """Back-to-back penalty in points, from the home team's point of view."""
    if not p["rest"]:
        return 0.0
    d = date.fromisoformat(g["game_date"])
    adj = 0.0
    lh, la = last.get((g["league"], g["home_team"])), last.get((g["league"], g["away_team"]))
    if lh and (d - lh).days == 1:
        adj -= p["rest"]
    if la and (d - la).days == 1:
        adj += p["rest"]
    return adj


def run_ratings(rows):
    """Walk finished games in order. Returns ratings and per-game pre-game home win chances.
    Also sets run_ratings.games_played and run_ratings.last_played (for rest-day checks)."""
    ratings = defaultdict(float, START)
    games_played = defaultdict(int)
    last = {}
    preds = {}
    for g in rows:
        lg = g["league"]
        if lg not in PARAMS:
            continue
        p = PARAMS[lg]
        kh, ka = (lg, g["home_team"]), (lg, g["away_team"])
        pm = ratings[kh] - ratings[ka] + (0 if g["neutral_site"] else p["hca"]) + _rest_adj(g, last, p)
        preds[g["game_id"]] = margin_to_prob(pm, lg)
        if g["completed"] and g["home_score"] is not None and g["home_score"] != g["away_score"]:
            m = max(-p["cap"], min(p["cap"], g["home_score"] - g["away_score"]))
            delta = p["k"] * (m - pm)
            ratings[kh] += delta
            ratings[ka] -= delta
            games_played[kh] += 1
            games_played[ka] += 1
            last[kh] = last[ka] = date.fromisoformat(g["game_date"])
    run_ratings.games_played = games_played
    run_ratings.last_played = last
    return ratings, preds


run_elo = run_ratings  # old name, kept so other scripts keep working


def market_prob(g):
    try:
        a, b = float(g["home_ml"]), float(g["away_ml"])
    except (TypeError, ValueError):
        return None
    return devig(a, b)[0]


def win_prob(con, g, ratings, use_injuries=True):
    """Home win chance from the points ratings. For NBA/WNBA, optionally lowers each team's rating by the
    estimated strength lost to injuries (injuries.team_impact, an uncalibrated estimate in Elo points,
    converted to points here)."""
    lg = g["league"]
    p = PARAMS[lg]
    rh, ra = ratings[(lg, g["home_team"])], ratings[(lg, g["away_team"])]
    if use_injuries:
        to_pts = math.log(10) / 400 * p["scale"]  # Elo points -> rating points at this league's scale
        rh -= team_impact(con, lg, g["home_team"])["elo"] * to_pts
        ra -= team_impact(con, lg, g["away_team"])["elo"] * to_pts
    last = getattr(run_ratings, "last_played", {})
    pm = rh - ra + (0 if g["neutral_site"] else p["hca"]) + _rest_adj(g, last, p)
    return margin_to_prob(pm, lg)


def cmd_log(_a):
    """Save today's/tomorrow's pre-game predictions so they can be scored once games finish."""
    con = sqlite3.connect(str(DB))
    import track
    track.ensure(con)
    ratings, _ = run_ratings(load(con, "WHERE completed = 1"))
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    n = 0
    for g in load(con, "WHERE completed = 0 AND start_time_utc > ?", (now[:16],)):
        if g["league"] not in PARAMS:
            continue
        pm = market_prob(g)
        if pm is None:
            continue  # only log games that have a line to compare against
        # Re-logging replaces the row, so the saved call is the last one made before the game started.
        con.execute("INSERT OR REPLACE INTO predictions (game_id, league, logged_at, market_prob, model_prob) "
                    "VALUES (?,?,?,?,?)", (g["game_id"], g["league"], now, pm, win_prob(con, g, ratings, True)))
        n += 1
    con.commit()
    print("Logged %d pre-game predictions." % n)


def cmd_scorecard(_a):
    """Score logged predictions on finished games, model vs the market, and say which leagues are proven."""
    import track
    con = sqlite3.connect(str(DB))
    st = track.league_status(con)
    if not st:
        print("No finished games with logged predictions yet. Let the 15-minute job run for a few weeks.")
        return
    print("%-11s %6s %9s %9s %9s %6s %5s %7s  %s" % ("League", "Games", "Model", "Market", "OOS gain", "z", "w", "Replay", "Verdict"))
    f4 = lambda x: "-" if x is None else "%.4f" % x
    for lg, v in st.items():
        print("%-11s %6d %9s %9s %+9.4f %6.2f %5.2f %7d  %s" % (lg, v["n"], f4(v["model"]), f4(v["market"]), v["gain"], v["z"], v["w"],
                                                              v["replay"], v["verdict"]))
    print("\nReplay: predictions logged before the points model went live (%s). Shown for reference, not graded." % track.CUTOVER)
    print("\nModel/Market: log loss, lower is better. OOS gain: how much the blend beat the odds on games it had not")
    print("seen (positive = better). Picks only in proven leagues: %d+ games, gain > %.3f, z > %.0f." % (track.MIN_GRADED, track.MIN_GAIN, track.MIN_Z))


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
          ("League", "Games", "Win%", "w/odds", "Model ll", "Mkt logloss"))
    for lg, s in stats.items():
        nm = s["nm"]
        print("%-11s %6d %7.1f%% | %6d %12s %12s" % (
            lg, s["n"], 100 * s["hit"] / s["n"], nm,
            "%.4f" % (s.get("ll_e_m", 0) / nm) if nm else "-",
            "%.4f" % (s["ll_m"] / nm) if nm else "-"))
    print("\nIf the model log loss is not below the market's, the model has no edge over the book: do not bet it.")
    print("Odds only exist for games pulled with lines; early-season sample sizes are small.")


# Pick rules. Edges beyond MAX_EDGE almost always mean the model is wrong, not the book. Long shots
# (beyond MAX_DOG) are where books overcharge the most, and big favorites (beyond MAX_FAV) pay too little
# for the risk, so both are skipped.
MIN_EDGE, MAX_EDGE = 0.03, 0.10
MAX_DOG, MAX_FAV = 250, -250
MAX_STAKE = 0.02  # never suggest more than 2% of the bankroll on one game


def find_picks(con, days, bankroll, min_games=10):
    """Flag value bets on the given days. Only leagues whose model is proven (track.league_status) are
    considered; elsewhere the model has not shown it beats the odds, so nothing is flagged."""
    import track
    status = track.league_status(con)
    ratings, _ = run_ratings(load(con, "WHERE completed = 1"))
    gp = run_ratings.games_played
    picks = []
    for g in load(con, "WHERE game_date IN (%s) AND completed = 0" % ",".join("?" * len(days)), tuple(days)):
        lg, h, a = g["league"], g["home_team"], g["away_team"]
        w = status.get(lg, {}).get("w", 0.0)
        pm = market_prob(g)
        if not w or pm is None or lg not in PARAMS or min(gp[(lg, h)], gp[(lg, a)]) < min_games:
            continue
        ph = track.blend(win_prob(con, g, ratings, True), pm, w)
        ph0 = track.blend(win_prob(con, g, ratings, False), pm, w)
        for team, pmod, pmk, odds, pmod0 in ((h, ph, pm, g["home_ml"], ph0), (a, 1 - ph, 1 - pm, g["away_ml"], 1 - ph0)):
            try:
                o = float(odds)
            except (TypeError, ValueError):
                continue
            edge = pmod - pmk
            if not (MIN_EDGE <= edge <= MAX_EDGE) or o > MAX_DOG or o < MAX_FAV:
                continue
            ev = ev_per_dollar(o, pmod)
            if ev <= 0:
                continue
            stake = min(kelly(o, pmod, 0.25), MAX_STAKE)
            picks.append({"g": g, "league": lg, "team": team, "vs": a if team == h else h, "home": team == h,
                          "odds": odds, "model": pmod, "market": pmk, "edge": edge, "ev": ev, "stake": stake,
                          "stake_usd": stake * bankroll, "inj_dependent": (pmod0 - pmk) < MIN_EDGE})
    picks.sort(key=lambda p: -p["edge"])
    return picks, status


def cmd_picks(a):
    con = sqlite3.connect(str(DB))
    day = a.date or date.today().isoformat()
    picks, status = find_picks(con, [day], a.bankroll or 0)
    for lg, v in status.items():
        print("  %-11s %s" % (lg, v["verdict"]))
    print()
    for p in picks:
        msg = "  %-10s %-28s %s  model %.1f%% vs market %.1f%%  edge %+.1f pts  EV %+.3f/$1  stake %.1f%%" % (
            p["league"], p["team"], p["odds"], p["model"] * 100, p["market"] * 100, p["edge"] * 100, p["ev"], p["stake"] * 100)
        if a.bankroll:
            msg += " ($%.2f)" % p["stake_usd"]
        print(msg)
    if not picks:
        print("No bets: either no league has proven its model yet, or no game clears the edge rules. Passing is a valid result.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backtest").set_defaults(fn=cmd_backtest)
    sub.add_parser("log").set_defaults(fn=cmd_log)
    sub.add_parser("scorecard").set_defaults(fn=cmd_scorecard)
    p = sub.add_parser("picks"); p.set_defaults(fn=cmd_picks)
    p.add_argument("--date")
    p.add_argument("--bankroll", type=float)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
