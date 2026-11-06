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

## Model (model.py)

- `python3 model.py backtest`: Elo vs. the market. Needs saved pre-game lines, which ESPN removes after games end. The puller now keeps the last lines it saw, so **run `python3 pull_games.py` every day before games start**; after a few weeks the backtest has data. Until then the model is unproven.
- `python3 model.py picks --bankroll 500`: flags games where a market-blended Elo beats the line. Guards: teams need 10+ rated games, the model is blended 30/70 with the market, and edges over 15 points are discarded as model error.

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
