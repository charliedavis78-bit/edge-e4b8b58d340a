#!/usr/bin/env python3
"""Build page.html: a self-contained, phone-friendly slate from data/games.db.

  python3 build_page.py                 # writes page.html
  python3 build_page.py --bankroll 500  # bankroll used for example stake sizes
"""
import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from injuries import team_notes
from injuries import team_impact
import track
from model import find_picks, load

HERE = Path(__file__).parent
DB = HERE / "data" / "games.db"


def impact_note(con, lg, team):
    i = team_impact(con, lg, team)
    if not i["elo"]:
        return None
    return {"team": team, "min": round(i["min_lost"]), "pts": round(i["pts_lost"]), "elo": round(i["elo"])}


def build(bankroll):
    con = sqlite3.connect(str(DB))
    today = date.today()
    days = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    track.restore(con)  # bring back saved predictions first: the proof check below needs them

    rows = load(con, "WHERE game_date IN (?,?)", tuple(days))
    games = []
    for g in rows:
        lg, h, a = g["league"], g["home_team"], g["away_team"]
        games.append({
            "id": g["game_id"], "league": lg, "day": g["game_date"], "start": g["start_time_utc"],
            "status": g["status"], "done": bool(g["completed"]),
            "home": h, "away": a, "hs": g["home_score"], "as": g["away_score"],
            "hrec": g["home_record"], "arec": g["away_record"],
            "hml": g["home_ml"], "aml": g["away_ml"], "spread": g["spread_home"],
            "total": g["total"], "book": g["book"],
            "inj": {"home": team_notes(con, lg, h), "away": team_notes(con, lg, a)},
        })

    found, _status = find_picks(con, days, bankroll)
    picks = []
    for p in found:
        g, lg, team, vs = p["g"], p["league"], p["team"], p["vs"]
        picks.append({
            "game": g["game_id"], "league": lg, "team": team, "odds": p["odds"], "vs": vs, "home": p["home"],
            "start": g["start_time_utc"], "model": round(p["model"] * 100, 1), "market": round(p["market"] * 100, 1),
            "edge": round(p["edge"] * 100, 1), "ev": round(p["ev"], 3),
            "stake_pct": round(p["stake"] * 100, 1), "stake_usd": round(p["stake_usd"], 2),
            "inj_team": team_notes(con, lg, team), "inj_opp": team_notes(con, lg, vs),
            "inj_dependent": p["inj_dependent"],
            "imp_team": impact_note(con, lg, team), "imp_opp": impact_note(con, lg, vs),
        })
    pulled = con.execute("SELECT max(pulled_at) FROM games").fetchone()[0]
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
    # The data sits inside a <script> tag: escape "</" so text from ESPN (team, player or status names)
    # can never close the tag early and break or inject into the page. "<\/" is the same string in JS.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", payload)
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
