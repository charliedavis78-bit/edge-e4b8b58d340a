#!/usr/bin/env python3
"""Betting math: implied probability, vig removal, expected value, Kelly sizing,
and a simple bet log. Standard library only.

Examples:
  python3 bet_math.py edge --odds -170 --prob 0.65
  python3 bet_math.py devig --a -170 --b +142
  python3 bet_math.py log --game "CBJ @ BUF" --pick BUF --odds -170 --stake 10
  python3 bet_math.py result --id 1 --outcome win
  python3 bet_math.py report
"""
import argparse
import csv
from datetime import date
from pathlib import Path

LOG = Path(__file__).parent / "data" / "bets.csv"
FIELDS = ["id", "date", "game", "pick", "odds", "stake", "outcome", "profit"]


def american_to_decimal(o):
    o = float(o)
    return 1 + (o / 100 if o > 0 else 100 / abs(o))


def implied_prob(o):
    return 1 / american_to_decimal(o)


def devig(a, b):
    """Remove the bookmaker margin from a two-way market (proportional method)."""
    pa, pb = implied_prob(a), implied_prob(b)
    s = pa + pb
    return pa / s, pb / s, s - 1  # fair A, fair B, vig


def ev_per_dollar(o, p):
    return p * (american_to_decimal(o) - 1) - (1 - p)


def kelly(o, p, fraction=0.25):
    b = american_to_decimal(o) - 1
    f = (b * p - (1 - p)) / b
    return max(0.0, f * fraction)


def cmd_edge(a):
    p = a.prob
    imp = implied_prob(a.odds)
    ev = ev_per_dollar(a.odds, p)
    print("Book implied probability: %.1f%%" % (imp * 100))
    print("Your probability:         %.1f%%" % (p * 100))
    print("Edge:                     %+.1f points" % ((p - imp) * 100))
    print("EV per $1 staked:         %+.3f" % ev)
    if ev <= 0:
        print("Verdict: no bet (negative expected value).")
    else:
        k = kelly(a.odds, p, a.kelly)
        print("Suggested stake (%.0f%% Kelly): %.2f%% of bankroll" % (a.kelly * 100, k * 100))
        if a.bankroll:
            print("  = $%.2f on a $%.0f bankroll" % (k * a.bankroll, a.bankroll))


def cmd_devig(a):
    fa, fb, vig = devig(a.a, a.b)
    print("Fair probabilities: A %.1f%% | B %.1f%%   (book margin %.1f%%)" % (fa * 100, fb * 100, vig * 100))


def read_log():
    if not LOG.exists():
        return []
    with open(LOG, newline="") as f:
        return list(csv.DictReader(f))


def write_log(rows):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def cmd_log(a):
    rows = read_log()
    rows.append({"id": len(rows) + 1, "date": date.today().isoformat(), "game": a.game,
                 "pick": a.pick, "odds": a.odds, "stake": a.stake, "outcome": "", "profit": ""})
    write_log(rows)
    print("Logged bet #%d" % len(rows))


def cmd_result(a):
    rows = read_log()
    for r in rows:
        if r["id"] == str(a.id):
            r["outcome"] = a.outcome
            stake, odds = float(r["stake"]), float(r["odds"])
            r["profit"] = {"win": round(stake * (american_to_decimal(odds) - 1), 2),
                           "loss": -stake, "push": 0}[a.outcome]
            write_log(rows)
            print("Bet #%s marked %s (profit %s)" % (r["id"], a.outcome, r["profit"]))
            return
    print("No bet with that id.")


def cmd_report(_a):
    rows = [r for r in read_log() if r["outcome"]]
    if not rows:
        print("No settled bets yet.")
        return
    staked = sum(float(r["stake"]) for r in rows)
    profit = sum(float(r["profit"]) for r in rows)
    wins = sum(r["outcome"] == "win" for r in rows)
    print("Settled bets: %d | Wins: %d | Staked: $%.2f | Profit: $%+.2f | ROI: %+.1f%%"
          % (len(rows), wins, staked, profit, profit / staked * 100 if staked else 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edge"); e.set_defaults(fn=cmd_edge)
    e.add_argument("--odds", type=float, required=True)
    e.add_argument("--prob", type=float, required=True, help="your win probability, 0-1")
    e.add_argument("--kelly", type=float, default=0.25, help="Kelly fraction (default 0.25)")
    e.add_argument("--bankroll", type=float)
    d = sub.add_parser("devig"); d.set_defaults(fn=cmd_devig)
    d.add_argument("--a", type=float, required=True); d.add_argument("--b", type=float, required=True)
    l = sub.add_parser("log"); l.set_defaults(fn=cmd_log)
    l.add_argument("--game", required=True); l.add_argument("--pick", required=True)
    l.add_argument("--odds", type=float, required=True); l.add_argument("--stake", type=float, required=True)
    r = sub.add_parser("result"); r.set_defaults(fn=cmd_result)
    r.add_argument("--id", type=int, required=True); r.add_argument("--outcome", choices=["win", "loss", "push"], required=True)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
