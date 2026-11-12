#!/usr/bin/env python3
"""Pull who is hurt / not playing, from ESPN's public injuries feed, into data/games.db.
For NBA and WNBA it also looks up each missing player's minutes and points so the
model can estimate how much a team is weakened (see team_impact).

  python3 injuries.py                 # refresh all leagues the feed covers
  python3 injuries.py --team "Atlanta Hawks"

Coverage: NHL, NBA, WNBA, MLB lists. Impact scoring: NBA and WNBA only.
The feed has NO college basketball injuries.
Run it right before you bet: statuses change up to tip-off.
"""
import argparse
import json
import re
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).parent / "data" / "games.db"
FEEDS = {"NHL": "hockey/nhl", "NBA": "basketball/nba", "WNBA": "basketball/wnba", "MLB": "baseball/mlb"}
STATS_PATH = {"NBA": "basketball/nba", "WNBA": "basketball/wnba"}
# statuses that likely mean the player does not play (Day-To-Day = uncertain)
OUT = ("out", "injured reserve", "suspension", "doubtful")

# How much of a player's absence counts. ESTIMATES (not verified): Out = fully missing,
# Doubtful usually sits, Day-To-Day often plays.
STATUS_WEIGHT = {"out": 1.0, "injured reserve": 1.0, "suspension": 1.0, "doubtful": 0.75, "day-to-day": 0.25}

# Impact model constants. ESTIMATES, not fitted to data: they exist so the effect can be
# tested later with logged predictions. team_min / team_pts = full-strength team totals;
# elo_per_share = Elo points removed per 100% of a team's output missing; cap = max penalty.
IMPACT = {
    "NBA": dict(team_min=240.0, team_pts=112.0, elo_per_share=300.0, cap=150.0),
    "WNBA": dict(team_min=200.0, team_pts=82.0, elo_per_share=300.0, cap=150.0),
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def _athlete_id(a):
    for l in a.get("links", []):
        if "playercard" in l.get("rel", []):
            m = re.search(r"/id/(\d+)", l.get("href", ""))
            if m:
                return m.group(1)
    return None


def _fetch_stats(league, athlete_id):
    """Return (gp, min, pts) using this season, or career when this season is a tiny sample."""
    d = _get("https://site.web.api.espn.com/apis/common/v3/sports/%s/athletes/%s/overview" % (STATS_PATH[league], athlete_id))
    st = d.get("statistics", {})
    labels = st.get("labels", [])
    splits = {s.get("displayName"): dict(zip(labels, s.get("stats", []))) for s in st.get("splits", [])}

    def num(row, k):
        try:
            return float(row.get(k))
        except (TypeError, ValueError):
            return 0.0

    season, career = splits.get("Regular Season", {}), splits.get("Career", {})
    s_gp, c_gp = num(season, "GP"), num(career, "GP")
    row = season if (s_gp >= 5 or c_gp < 20) else career
    return num(row, "GP"), num(row, "MIN"), num(row, "PTS")


def refresh():
    con = sqlite3.connect(str(DB))
    cols = "(league, team, player, position, status, detail, updated, pulled_at, athlete_id)"
    con.execute("CREATE TABLE IF NOT EXISTS injuries " + cols)
    # Fill a fresh table first and swap it in only if at least one feed answered, so a feed outage
    # keeps the last good list instead of wiping it (which would also drop the model's injury penalty).
    con.execute("DROP TABLE IF EXISTS injuries_new")
    con.execute("CREATE TABLE injuries_new " + cols)
    con.execute("CREATE TABLE IF NOT EXISTS player_stats (league, athlete_id, gp, min, pts, fetched_at, PRIMARY KEY (league, athlete_id))")
    # one row per day per player/status: history for testing the injury effect later
    con.execute("CREATE TABLE IF NOT EXISTS injury_history (day, league, team, player, athlete_id, status, PRIMARY KEY (day, league, team, player, status))")
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    day = datetime.now().date().isoformat()
    ok = 0
    for lg, path in FEEDS.items():
        try:
            data = _get("https://site.api.espn.com/apis/site/v2/sports/%s/injuries" % path)
        except Exception as e:
            print("  ! %s failed: %s" % (lg, e)); continue
        ok += 1
        for t in data.get("injuries", []):
            for i in t.get("injuries", []):
                a = i.get("athlete", {})
                aid = _athlete_id(a)
                con.execute("INSERT INTO injuries_new VALUES (?,?,?,?,?,?,?,?,?)", (
                    lg, t.get("displayName"), a.get("displayName"), (a.get("position") or {}).get("abbreviation"),
                    i.get("status"), i.get("shortComment"), i.get("date"), now, aid))
                con.execute("INSERT OR IGNORE INTO injury_history VALUES (?,?,?,?,?,?)",
                            (day, lg, t.get("displayName"), a.get("displayName"), aid, i.get("status")))
    if ok:
        con.execute("DROP TABLE injuries")
        con.execute("ALTER TABLE injuries_new RENAME TO injuries")
    else:
        con.execute("DROP TABLE injuries_new")
        print("  ! every injury feed failed: keeping the previous injury list")
    con.commit()

    # player stats for anyone who might miss time (cached 12 hours)
    fresh = (datetime.utcnow() - timedelta(hours=12)).isoformat(timespec="seconds") + "Z"
    fetched = 0
    for lg in STATS_PATH:
        for (aid,) in con.execute("SELECT DISTINCT athlete_id FROM injuries WHERE league=? AND athlete_id IS NOT NULL", (lg,)).fetchall():
            if con.execute("SELECT 1 FROM player_stats WHERE league=? AND athlete_id=? AND fetched_at>?", (lg, aid, fresh)).fetchone():
                continue
            try:
                gp, mn, pts = _fetch_stats(lg, aid)
            except Exception:
                continue
            con.execute("INSERT OR REPLACE INTO player_stats VALUES (?,?,?,?,?,?)", (lg, aid, gp, mn, pts, now))
            fetched += 1
    con.commit()
    n = con.execute("SELECT league, count(*) FROM injuries GROUP BY league").fetchall()
    print("Injuries saved:", dict(n), "| player stats fetched:", fetched)
    con.close()


def _weight(status):
    s = (status or "").lower()
    for k, w in STATUS_WEIGHT.items():
        if k in s:
            return w
    return 0.0


def team_notes(con, league, team):
    """Players likely not playing for a team. Returns list of 'Name (POS, status)'."""
    try:
        rows = con.execute("SELECT player, position, status FROM injuries WHERE league=? AND team=?",
                           (league, team)).fetchall()
    except sqlite3.OperationalError:
        return []
    return ["%s (%s, %s)" % (p, pos, st) for p, pos, st in rows if (st or "").lower() in OUT or "day" in (st or "").lower()]


def team_impact(con, league, team):
    """Estimate how much a team is weakened by absences (NBA/WNBA only).

    Returns dict: elo (penalty in Elo points, 0 if none/unknown), min_lost, pts_lost, share,
    players (name, weight, min, pts). share = average of the minutes share and points share missing.
    """
    out = dict(elo=0.0, min_lost=0.0, pts_lost=0.0, share=0.0, players=[])
    if league not in IMPACT:
        return out
    c = IMPACT[league]
    try:
        rows = con.execute(
            "SELECT i.player, i.status, s.min, s.pts, s.gp FROM injuries i "
            "LEFT JOIN player_stats s ON s.league=i.league AND s.athlete_id=i.athlete_id "
            "WHERE i.league=? AND i.team=?", (league, team)).fetchall()
    except sqlite3.OperationalError:
        return out
    for name, status, mn, pts, gp in rows:
        w = _weight(status)
        if w == 0 or mn is None or not gp:
            continue
        out["min_lost"] += w * mn
        out["pts_lost"] += w * pts
        out["players"].append((name, w, mn, pts))
    mshare = min(out["min_lost"] / c["team_min"], 1.0)
    pshare = min(out["pts_lost"] / c["team_pts"], 1.0)
    out["share"] = (mshare + pshare) / 2
    out["elo"] = min(c["elo_per_share"] * out["share"], c["cap"])
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team")
    a = ap.parse_args()
    refresh()
    if a.team:
        c = sqlite3.connect(str(DB))
        for r in c.execute("SELECT player, position, status, detail FROM injuries WHERE team=?", (a.team,)):
            print(" ", r)
        for lg in IMPACT:
            imp = team_impact(c, lg, a.team)
            if imp["players"]:
                print("  Impact: -%.0f Elo (%.0f min, %.0f pts/game missing)" % (imp["elo"], imp["min_lost"], imp["pts_lost"]))
