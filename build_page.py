#!/usr/bin/env python3
"""Build page.html: a self-contained, phone-friendly slate from data/games.db.

  python3 build_page.py                 # writes page.html
  python3 build_page.py --bankroll 500  # bankroll used for example stake sizes
"""
import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from bet_math import ev_per_dollar, kelly
from injuries import team_notes
from injuries import team_impact
import track
from model import PARAMS, load, market_prob, run_elo, win_prob

HERE = Path(__file__).parent
DB = HERE / "data" / "games.db"


def impact_note(con, lg, team):
    i = team_impact(con, lg, team)
    if not i["elo"]:
        return None
    return {"team": team, "min": round(i["min_lost"]), "pts": round(i["pts_lost"]), "elo": round(i["elo"])}


def build(bankroll, min_edge=0.05, min_games=10, blend=0.3):
    con = sqlite3.connect(str(DB))
    today = date.today()
    days = [today.isoformat(), (today + timedelta(days=1)).isoformat()]

    history = load(con, "WHERE completed = 1")
    ratings, _ = run_elo(history)
    gp = run_elo.games_played

    rows = load(con, "WHERE game_date IN (?,?)", tuple(days))
    games, picks = [], []
    for g in rows:
        lg, h, a = g["league"], g["home_team"], g["away_team"]
        inj = {"home": team_notes(con, lg, h), "away": team_notes(con, lg, a)}
        item = {
            "id": g["game_id"], "league": lg, "day": g["game_date"], "start": g["start_time_utc"],
            "status": g["status"], "done": bool(g["completed"]),
            "home": h, "away": a, "hs": g["home_score"], "as": g["away_score"],
            "hrec": g["home_record"], "arec": g["away_record"],
            "hml": g["home_ml"], "aml": g["away_ml"], "spread": g["spread_home"],
            "total": g["total"], "book": g["book"], "inj": inj,
        }
        games.append(item)
        if g["completed"] or lg not in PARAMS:
            continue
        pm = market_prob(g)
        if pm is None or min(gp[(lg, h)], gp[(lg, a)]) < min_games:
            continue
        ph = win_prob(con, g, ratings, True)
        ph = (1 - blend) * pm + blend * ph
        ph0 = (1 - blend) * pm + blend * win_prob(con, g, ratings, False)  # same game with no injury adjustment
        for team, pmod, pmk, odds, pmod0 in ((h, ph, pm, g["home_ml"], ph0), (a, 1 - ph, 1 - pm, g["away_ml"], 1 - ph0)):
            edge = pmod - pmk
            inj_dependent = (pmod0 - pmk) < min_edge
            if edge > 0.15 or edge < min_edge:
                continue
            ev = ev_per_dollar(float(odds), pmod)
            if ev <= 0:
                continue
            stake = kelly(float(odds), pmod, 0.25)
            picks.append({
                "game": g["game_id"], "league": lg, "team": team, "odds": odds,
                "vs": a if team == h else h, "home": team == h, "start": g["start_time_utc"],
                "model": round(pmod * 100, 1), "market": round(pmk * 100, 1),
                "edge": round(edge * 100, 1), "ev": round(ev, 3),
                "stake_pct": round(stake * 100, 1), "stake_usd": round(stake * bankroll, 2),
                "inj_team": inj["home" if team == h else "away"], "inj_opp": inj["away" if team == h else "home"],
                "inj_dependent": inj_dependent,
                "imp_team": impact_note(con, lg, team), "imp_opp": impact_note(con, lg, a if team == h else h),
            })
    picks.sort(key=lambda p: -p["edge"])
    pulled = con.execute("SELECT max(pulled_at) FROM games").fetchone()[0]
    track.restore(con)
    track.save_picks(con, picks)
    tr = track.results(con)
    track.export(con)
    return {"track": tr, "pulled": pulled, "today": days[0], "tomorrow": days[1], "bankroll": bankroll,
            "games": games, "picks": picks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bankroll", type=float, default=500)
    ap.add_argument("--site", action="store_true", help="also write site/index.html for GitHub Pages")
    a = ap.parse_args()
    data = build(a.bankroll)
    tpl = (HERE / "page_template.html").read_text()
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    (HERE / "page.html").write_text(html)
    if a.site:
        # Standalone page for GitHub Pages: needs its own doctype, viewport and base styles.
        head = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                '<meta name="robots" content="noindex, nofollow">'
                '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px);color-scheme:light}'
                'body{margin:0;font-size:14px}[hidden]{display:none!important}</style></head><body>')
        out = HERE / "site"
        out.mkdir(exist_ok=True)
        (out / "index.html").write_text(head + html + "</body></html>")
        print("site/index.html written")
    print("page.html: %d games, %d flagged picks, data as of %s" % (len(data["games"]), len(data["picks"]), data["pulled"]))


if __name__ == "__main__":
    main()
