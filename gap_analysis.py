#!/usr/bin/env python3
"""What is the Elo model missing? Tests extra factors against finished games.

For each finished game we build features using ONLY information before tip-off,
then compare out-of-sample prediction quality (log loss, lower is better) with
and without each factor. Pure Python, no extra packages.

  python3 gap_analysis.py
"""
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from model import PARAMS, expected

DB = Path(__file__).parent / "data" / "games.db"


def sigmoid(z):
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def fit_logistic(X, y, l2=0.05, iters=1500, lr=0.2):
    """Plain gradient descent logistic regression with a bias term."""
    n, k = len(X), len(X[0])
    w = [0.0] * (k + 1)
    for _ in range(iters):
        g = [0.0] * (k + 1)
        for xi, yi in zip(X, y):
            p = sigmoid(w[0] + sum(w[j + 1] * xi[j] for j in range(k)))
            e = p - yi
            g[0] += e
            for j in range(k):
                g[j + 1] += e * xi[j]
        for j in range(k + 1):
            w[j] -= lr * (g[j] / n + (l2 * w[j] if j else 0))
    return w


def predict(w, xi):
    return sigmoid(w[0] + sum(w[j + 1] * xi[j] for j in range(len(xi))))


def logloss(ps, ys):
    return -sum(y * math.log(min(max(p, 1e-4), 1 - 1e-4)) + (1 - y) * math.log(1 - min(max(p, 1e-4), 1 - 1e-4))
                for p, y in zip(ps, ys)) / len(ys)


def build(league):
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM games WHERE league=? AND completed=1 AND home_score IS NOT NULL "
                       "AND home_score != away_score ORDER BY start_time_utc", (league,)).fetchall()
    p = PARAMS[league]
    elo = defaultdict(lambda: 1500.0)
    last_game = {}
    margins = defaultdict(list)
    played = defaultdict(int)
    data = []
    for g in rows:
        h, a = g["home_team"], g["away_team"]
        t = datetime.fromisoformat(g["start_time_utc"].replace("Z", "+00:00"))
        rest_h = min((t - last_game[h]).total_seconds() / 86400, 6) if h in last_game else 6
        rest_a = min((t - last_game[a]).total_seconds() / 86400, 6) if a in last_game else 6
        form_h = sum(margins[h][-5:]) / max(len(margins[h][-5:]), 1)
        form_a = sum(margins[a][-5:]) / max(len(margins[a][-5:]), 1)
        prob = expected(elo[h], elo[a], 0 if g["neutral_site"] else p["home"])
        y = 1.0 if g["home_score"] > g["away_score"] else 0.0
        data.append(dict(
            elo_logit=math.log(prob / (1 - prob)), y=y,
            b2b_diff=float(rest_h < 1.5) - float(rest_a < 1.5),      # home back-to-back minus away
            rest_diff=rest_h - rest_a,
            form_diff=form_h - form_a,                                  # last-5 average margin
            thin=float(min(played[h], played[a]) < 8),                  # a team with little history
            min_played=min(played[h], played[a]),
        ))
        margin = g["home_score"] - g["away_score"]
        mult = math.log(abs(margin) + 1) + 1
        d = p["k"] * mult * (y - prob)
        elo[h] += d; elo[a] -= d
        margins[h].append(margin); margins[a].append(-margin)
        last_game[h] = last_game[a] = t
        played[h] += 1; played[a] += 1
    return data


def standardize(train, test_, cols):
    stats = {}
    for c in cols:
        vals = [d[c] for d in train]
        m = sum(vals) / len(vals)
        sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        stats[c] = (m, sd)
    f = lambda d: [(d[c] - stats[c][0]) / stats[c][1] for c in cols]
    return f


def test(league, min_games=3):
    data = build(league)
    data = [d for d in data if d["min_played"] >= min_games]   # skip the first games where nothing is known
    n = len(data)
    if n < 80:
        print("%-11s only %d usable games: too few to test" % (league, n)); return
    cut = int(n * 0.6)
    train, test_ = data[:cut], data[cut:]
    ys = [d["y"] for d in test_]
    base = logloss([sigmoid(d["elo_logit"]) for d in test_], ys)
    coin = logloss([0.5] * len(ys), ys)
    print("\n%s: %d games (train %d / test %d). Coin flip %.4f | Elo alone %.4f" % (league, n, len(train), len(test_), coin, base))
    for name, feats in (("Rest / back-to-back", ["b2b_diff", "rest_diff"]), ("Recent form (last 5)", ["form_diff"]),
                        ("Both", ["b2b_diff", "rest_diff", "form_diff"])):
        cols = ["elo_logit"] + feats
        f = standardize(train, test_, cols)
        w = fit_logistic([f(d) for d in train], [d["y"] for d in train])
        ll = logloss([predict(w, f(d)) for d in test_], ys)
        wt = ", ".join("%s %+.2f" % (c, w[i + 1]) for i, c in enumerate(cols))
        print("   + %-22s logloss %.4f (%+.4f vs Elo)   standardized weights: %s" % (name, ll, ll - base, wt))
    # calibration of plain Elo: when it says X%, how often does home win?
    buckets = defaultdict(list)
    for d in test_:
        buckets[min(int(sigmoid(d["elo_logit"]) * 10), 9)].append(d["y"])
    print("   Elo calibration (says -> actual home win rate):",
          "; ".join("%d-%d%%: %.0f%% (n=%d)" % (b * 10, b * 10 + 10, 100 * sum(v) / len(v), len(v)) for b, v in sorted(buckets.items()) if len(v) >= 8))


if __name__ == "__main__":
    for lg in ("NHL", "NBA"):
        test(lg)
    for lg in ("NCAA Men", "NCAA Women"):
        test(lg, min_games=1)
    print("\nLower logloss = better. A coin flip scores 0.6931. Samples are small (weeks, not seasons): treat gaps under ~0.01 as noise.")
