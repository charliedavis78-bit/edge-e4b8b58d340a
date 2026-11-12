# Sports Betting System

DRAFT: needs owner approval. A data and discipline tool, not a guarantee of profit.

## What it does

- `pull_games.py` pulls each day's games, final scores, records and DraftKings lines (moneyline, spread, total) for NHL, NBA, WNBA, NCAA men, NCAA women and MLB. Source: ESPN's public scoreboard feed (unofficial, no key). Label: Verified working 2026-11-06.
- `bet_math.py` does the math: implied probability, vig removal, expected value, fractional Kelly stake sizing, and a bet log with ROI report.
- `data/games.db` (SQLite) is the history that builds up. `data/games_YYYY-MM-DD.csv` is each day's snapshot.

## Daily use

```bash
python3 pull_games.py                      # today's slate with lines
python3 pull_games.py --days-back 30       # backfill results to build history
python3 pull_games.py --days-ahead 2       # upcoming schedule
python3 pull_games.py --leagues nhl nba    # only some leagues
python3 bet_math.py edge --odds -170 --prob 0.65 --bankroll 500
python3 bet_math.py log --game "CBJ @ BUF" --pick BUF --odds -170 --stake 10
python3 bet_math.py result --id 1 --outcome win
python3 bet_math.py report
```

## Model (model.py), updated 2026-11-12

- **Points rating** (replaced the win/loss Elo): each team has a rating in points (goals for NHL). Predicted margin = home rating - away rating + home edge, minus a back-to-back penalty (NBA, NHL). Settings are tuned on the 2025-26 season with `tune.py`; part of last season's rating carries over (`season_ratings.py`). On the 320 logged games it scored better than the old Elo in NBA (0.489 vs 0.573) and NCAA Men (0.514 vs 0.532), and about the same in NHL. Model probabilities for games logged before 2026-11-12 were filled in by replaying the season (each game predicted only from earlier games, no injury data).
- **Proof gate** (`track.league_status`): every pre-game prediction is saved and scored. A league gets picks only after 100+ finished games and only if mixing the model into the odds (log-odds blend) beats the odds alone on games it had not seen: each game is graded with a weight fitted only on earlier games, and the gain must be about two standard errors above zero. In simulations a worthless model passes this under 1% of the time. Otherwise its weight is 0 and nothing is picked. The old fixed 30% blend let a weaker model push picks onto underdogs, which is what produced the 0-8 start.
- **Pick rules**: edge 3 to 10 points, odds between -250 and +250, positive EV, quarter Kelly capped at 2% of bankroll.
- `python3 model.py scorecard` shows each league's status. `python3 model.py picks --bankroll 500` lists picks (only in proven leagues).

## Season status (as of 2026-11-06)

In season: NHL, NBA, NCAA men and women. Off-season (no games until spring): MLB and WNBA, so those return zero games now. They fill in on their own when play starts.

## Known limits

- NCAA women's games had no lines in the 2026-11-06 pull. Many small college games have none.
- Lines are one book (DraftKings) at the latest "close" value ESPN shows. Compare several books before betting; the free tier of The Odds API (needs your own key) is the usual next step.
- The feed is unofficial and can change without notice.
- No injury, rest or lineup data yet. That is the next layer and where most real edge comes from.

## Honest expectations

Books build in roughly 4 to 5 percent margin (e.g. -170/+142 is 4.3 percent). Most bettors lose long term. A model only helps if its probabilities beat the market's, and that must be proven on tracked results before real stakes. Use `bet_math.py edge`, only bet positive EV, keep stakes at quarter Kelly or less, and never bet money you need. This is not financial advice. Problem gambling help: 1-800-GAMBLER.

## Suggested next steps

1. Run `--days-back 30` daily for a few weeks to build history. Owner: Charlie, start now.
2. Add injury and rest-day data, then a simple rating model (Elo) per league.
3. Paper-trade (log bets with no money) for 100+ bets and check ROI before staking real money.
