#!/usr/bin/env python3
"""Pull daily games, scores and betting lines for hockey, basketball and baseball.

Source: ESPN public scoreboard feed (unofficial, no API key). Odds are DraftKings
lines as shown by ESPN, when posted. Standard library only (Python 3.9+).

Usage:
  python3 pull_games.py                      # today, all leagues
  python3 pull_games.py --days-back 7        # also pull the last 7 days of results
  python3 pull_games.py --days-ahead 2       # also pull the next 2 days of schedule
  python3 pull_games.py --leagues nhl nba    # only some leagues
  python3 pull_games.py --date 2026-11-06    # one specific date

Output: data/games.db (SQLite, one row per game, updated in place),
        data/games_YYYY-MM-DD.csv (the pulled day's snapshot),
        and a printed daily slate.
"""
import argparse
import csv
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = "https://site.api.espn.com/apis/site/v2/sports"
# key -> (sport/league path, label, extra query string)
LEAGUES = {
    "nhl": ("hockey/nhl", "NHL", ""),
    "nba": ("basketball/nba", "NBA", ""),
    "wnba": ("basketball/wnba", "WNBA", ""),
    "ncaam": ("basketball/mens-college-basketball", "NCAA Men", "&groups=50"),
    "ncaaw": ("basketball/womens-college-basketball", "NCAA Women", "&groups=50"),
    "mlb": ("baseball/mlb", "MLB", ""),
}
DATA_DIR = Path(__file__).parent / "data"

COLUMNS = [
    "game_id", "league", "game_date", "start_time_utc", "status", "completed",
    "home_team", "away_team", "home_score", "away_score",
    "home_record", "away_record",
    "book", "home_ml", "away_ml", "spread_home", "spread_home_odds",
    "spread_away_odds", "total", "over_odds", "under_odds",
    "venue", "neutral_site", "pulled_at",
]


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as exc:  # network hiccup: back off and retry
            if attempt == retries - 1:
                print("  ! failed: %s (%s)" % (url, exc), file=sys.stderr)
                return None
            time.sleep(1.5 * (attempt + 1))


def close_odds(block, *path):
    """Dig block[path...]['close'] -> dict, tolerating missing pieces."""
    cur = block
    for p in path:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(p)
    if isinstance(cur, dict):
        return cur.get("close") or {}
    return {}


def to_num(x):
    try:
        return float(str(x).replace("o", "").replace("u", ""))
    except (TypeError, ValueError):
        return None


def parse_event(ev, league_key, pulled_at):
    comp = ev["competitions"][0]
    teams = {c["homeAway"]: c for c in comp["competitors"]}
    if "home" not in teams or "away" not in teams:
        return None
    home, away = teams["home"], teams["away"]

    def rec(t):
        for r in t.get("records") or []:
            if r.get("type") == "total":
                return r.get("summary")
        return None

    odds = (comp.get("odds") or [{}])[0]
    ml = odds.get("moneyline") or {}
    sp = odds.get("pointSpread") or {}
    tot = odds.get("total") or {}
    status = ev["status"]["type"]
    venue = (comp.get("venue") or {}).get("fullName")

    return {
        "game_id": "%s-%s" % (league_key, ev["id"]),
        "league": LEAGUES[league_key][1],
        "game_date": None,  # filled by caller (the requested date)
        "start_time_utc": ev.get("date"),
        "status": status.get("description"),
        "completed": int(bool(status.get("completed"))),
        "home_team": home["team"]["displayName"],
        "away_team": away["team"]["displayName"],
        "home_score": to_num(home.get("score")) if status.get("state") != "pre" else None,
        "away_score": to_num(away.get("score")) if status.get("state") != "pre" else None,
        "home_record": rec(home),
        "away_record": rec(away),
        "book": (odds.get("provider") or {}).get("name"),
        "home_ml": close_odds(ml, "home").get("odds"),
        "away_ml": close_odds(ml, "away").get("odds"),
        "spread_home": to_num(close_odds(sp, "home").get("line")),
        "spread_home_odds": close_odds(sp, "home").get("odds"),
        "spread_away_odds": close_odds(sp, "away").get("odds"),
        "total": to_num(close_odds(tot, "over").get("line")) or odds.get("overUnder"),
        "over_odds": close_odds(tot, "over").get("odds"),
        "under_odds": close_odds(tot, "under").get("odds"),
        "venue": venue,
        "neutral_site": int(bool(comp.get("neutralSite"))),
        "pulled_at": pulled_at,
    }


def pull_day(day, league_keys):
    rows = []
    stamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for key in league_keys:
        path, label, extra = LEAGUES[key]
        url = "%s/%s/scoreboard?dates=%s&limit=400%s" % (BASE, path, day.strftime("%Y%m%d"), extra)
        data = fetch(url)
        if not data:
            continue
        for ev in data.get("events", []):
            try:
                row = parse_event(ev, key, stamp)
            except (KeyError, IndexError):
                continue
            if row:
                row["game_date"] = day.isoformat()
                rows.append(row)
    return rows


def save_db(rows):
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DATA_DIR / "games.db"))
    cols = ", ".join(COLUMNS)
    con.execute("CREATE TABLE IF NOT EXISTS games (%s, PRIMARY KEY (game_id))" % cols)
    marks = ", ".join("?" for _ in COLUMNS)
    odds_cols = ["book", "home_ml", "away_ml", "spread_home", "spread_home_odds",
                 "spread_away_odds", "total", "over_odds", "under_odds"]
    # ESPN drops lines once a game ends. Keep the last lines we saw (needed to backtest vs. the market).
    sets = ", ".join(
        "%s = COALESCE(excluded.%s, games.%s)" % (c, c, c) if c in odds_cols else "%s = excluded.%s" % (c, c)
        for c in COLUMNS if c != "game_id")
    con.executemany(
        "INSERT INTO games (%s) VALUES (%s) ON CONFLICT(game_id) DO UPDATE SET %s" % (cols, marks, sets),
        [[r[c] for c in COLUMNS] for r in rows],
    )
    con.commit()
    con.close()


def save_csv(day, rows):
    DATA_DIR.mkdir(exist_ok=True)
    with open(DATA_DIR / ("games_%s.csv" % day.isoformat()), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def print_slate(day, rows):
    print("\n=== %s: %d games ===" % (day.isoformat(), len(rows)))
    for lg in dict.fromkeys(r["league"] for r in rows):
        print("\n%s" % lg)
        for r in [x for x in rows if x["league"] == lg]:
            if r["completed"]:
                line = "FINAL  %s %s - %s %s" % (r["away_team"], int(r["away_score"] or 0), int(r["home_score"] or 0), r["home_team"])
            else:
                line = "%-10s %s @ %s" % (r["status"], r["away_team"], r["home_team"])
            if r["home_ml"]:
                line += "  | ML %s/%s  spread %s  total %s (%s)" % (
                    r["away_ml"], r["home_ml"], r["spread_home"], r["total"], r["book"])
            print("  " + line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--days-back", type=int, default=0)
    ap.add_argument("--days-ahead", type=int, default=0)
    ap.add_argument("--leagues", nargs="+", choices=list(LEAGUES), default=list(LEAGUES))
    ap.add_argument("--quiet", action="store_true", help="don't print the slate")
    args = ap.parse_args()

    center = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    days = [center + timedelta(days=d) for d in range(-args.days_back, args.days_ahead + 1)]
    total = 0
    for day in days:
        rows = pull_day(day, args.leagues)
        total += len(rows)
        if rows:
            save_db(rows)
            save_csv(day, rows)
        if not args.quiet:
            print_slate(day, rows)
    print("\nSaved %d games to %s" % (total, DATA_DIR / "games.db"))


if __name__ == "__main__":
    main()
