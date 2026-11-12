# Full Spectrum Audit: Edge Sheet

**Date**: 2026-11-12
**Mode**: Repo Health
**Source**: `main` at `b8cb8fa` (clean working tree, even with origin)
**Lenses Run**: 8/10 (Design, UX, Frontend, Backend, Structural, Repo Maintainer, Production Readiness, Performance)
**Lenses Skipped**: DX Engineer (no developer-facing product; the CLI notes are folded into Repo Maintainer), Accessibility Engineer (owner chose the 8-lens set; the page is small and already uses labels, `aria-pressed` and focus rings, see Design notes)

Adaptation note: these lenses were written for a React + Rust desktop app. Here "Frontend" means `page_template.html` (vanilla JS) and "Backend" means the Python data pipeline and SQLite. Checks that only apply to React, Zustand, Tauri or Rust are marked N/A.

---

## Master Verdict: APPROVE WITH CHANGES

The core design is sound. The proof lock grades out of sample, the pick limits are conservative, the code compiles cleanly on Python 3.11, and the two copies of the rating engine give identical ratings (checked: max difference 0.0000 on this season's NBA, NHL and NCAA Men games). Two things should be fixed before any league can reach 100 graded games. First, the lock currently counts only replayed predictions (359 of 359 rows were logged before the new model went live), and one of the model's settings was tuned on those same games. Second, the page footer still tells readers that win chances are "blended 30/70 with the market", which is the retired method behind the 0-8 record. The rest is hardening: silent failures, a cache-rebuild window that becomes too short on Nov 20, stale docs, and duplicated settings.

## Verdict by Lens

| # | Lens | Verdict | Critical | Recommendations |
|---|------|---------|----------|-----------------|
| 1 | Design Engineer | APPROVE WITH CHANGES | 0 | 3 |
| 2 | UX Engineer | APPROVE WITH CHANGES | 1 | 4 |
| 3 | Frontend Engineer | APPROVE WITH CHANGES | 0 | 3 |
| 4 | Backend Engineer | APPROVE WITH CHANGES | 1 | 6 |
| 5 | Structural Engineer | APPROVE WITH CHANGES | 0 | 4 |
| 6 | Repo Maintainer | APPROVE WITH CHANGES | 1 | 5 |
| 7 | Production Readiness | APPROVE WITH CHANGES | 1 | 5 |
| 8 | DX Engineer | Skipped | - | - |
| 9 | Performance Engineer | APPROVE | 0 | 1 |
| 10 | Accessibility Engineer | Skipped | - | - |

## All Critical Issues (Must Fix)

| # | Issue | Lens(es) | File(s) | Impact | Fix |
|---|-------|----------|---------|--------|-----|
| C1 | The proof lock counts replayed predictions. All 359 rows in `log/predictions.csv` (NBA 51, NHL 48, NCAA Men 260) were logged before the points model went live and had `model_prob` filled in by replaying the season. `keep` (share of last season's rating carried over) was chosen using this season's games (`tune.py` "this season" block), so these rows are partly in-sample. | Backend, Production Readiness | `track.py:111-114` (`league_status` query), `tune.py:75-79` | The money gate could open partly on flattering data. That is the same kind of leak the team already rejected when it replaced the in-sample lock (11-25% false pass rate). NBA (47 of 100) and NHL (37 of 100) are about halfway there. | Add a `CUTOVER = "2026-11-12T..."` constant and grade only `logged_at >= CUTOVER` rows toward `MIN_GRADED`, gain and z. Show the replayed rows separately on the Model check as "replay, not counted". This tightens the lock and does not loosen it. Owner approval needed, because it resets the counts. |
| C2 | The page footer says "Win chances: team ratings from past scores, blended 30/70 with the market." That describes the retired Elo method, not the current one (model weight 0 unless proven, then a fitted weight). | UX, Design, Repo Maintainer | `page_template.html:119` | Readers are told the opposite of what the safety design does, on a page about money. | Replace with: "Win chances: team ratings from past scores. The model is mixed into the odds only in leagues where it has proven it beats them; elsewhere it makes no picks." |

## Cross-Cutting Concerns

| Concern | Flagged By | Root Cause | Unified Fix |
|---------|-----------|------------|-------------|
| Leftover text from the old model in several places | UX, Design, Repo Maintainer | The Nov 12 upgrade changed the code but not every description of it | One sweep: footer (`page_template.html:119`), `model.py:2-11` docstring and the `backtest` header ("Elo vs. the betting market"), README "Suggested next steps" and "Season status as of 2026-11-06", and `run_refresh.sh` (describes launchd every 15 minutes, uses `/usr/bin/python3` 3.9, skips restore and build). |
| Failures that stay silent | Backend, Production Readiness, UX | Errors are printed and swallowed, so the workflow stays green | Make `pull_games.py` exit non-zero when every fetch fails or zero games come back in season. Make `injuries.py` keep the old table if all feeds fail. Let GitHub's failed-run email be the alert. |
| Refresh timing and staleness | Production Readiness, UX, Performance | GitHub cron runs about 2 times a day instead of 36 | The Mac job (4:00, 5:30, 6:30 PM CT) helps while the Mac is awake. For picks, add an external trigger such as cron-job.org. Hide picks whose odds snapshot is over about 2 hours old. |
| What data feeds the model and the lock | Backend, Production Readiness | Replayed rows (C1), preseason games in the ratings, and a 50-day cache rebuild all affect what the model learns from | Write down and enforce the data rules: regular season only, cutover-dated grading, and a rebuild from season start. |
| Settings and logic kept in more than one place | Structural, Repo Maintainer | No shared config module | Make one `config.py` (paths, `MIN_GRADED`, pick limits, cutover) and one `ratings.py` engine used by both `model.py` and `tune.py`. Send the lock thresholds to the page in the data instead of hard-coding "100" in the template. |

## All Recommendations

| # | Recommendation | Lens | File(s) | Fix |
|---|---------------|------|---------|-----|
| R1 | The cache-miss rebuild pulls only 50 days for 4 leagues. Oct 1 + 50 days = Nov 20, so from Nov 21 a lost cache would silently drop early-season games from the ratings. | Production Readiness, Backend | `.github/workflows/refresh.yml` (rebuild step) | Rebuild from a fixed `SEASON_START` (for example `--date today --days-back $(days since 2026-10-01)`), or loop `pull_history.py` into `data/`. |
| R2 | Preseason games feed the ratings: about 70 NBA and up to 143 NHL games before Oct 21 this season (ESPN `season.type == 1`). The same was true last season, so the tuning is consistent with it, but it adds noise. | Backend | `pull_games.py:82-127`, `model.py:81-106`, `tune.py:26-31` | Store `season_type`, rate regular season and postseason only, re-run `tune.py` and compare log loss before shipping (per the doc's rules). |
| R3 | `json.dumps` output is placed inside `<script>` without escaping `</`. A team, player or status string from ESPN containing `</script>` would break the page or inject markup. Confirmed: `json.dumps({'x':'</script>'})` gives `"</script>"`. | Frontend, Production Readiness | `build_page.py:77` | `json.dumps(data, separators=(",", ":")).replace("</", "<\\/")` |
| R4 | `injuries.refresh` drops the table first. If every feed fails, there are no injuries, predictions are logged without the injury penalty, and the page shows no injuries. | Backend, Production Readiness | `injuries.py:75-96` | Fill a temporary table, then swap it in only if at least one feed succeeded. |
| R5 | Fetch errors are swallowed and the run still succeeds with zero data. | Backend, Production Readiness | `pull_games.py:50-60, 139-143` | Count failures and exit 1 if all calls for a day fail. Log `parse_event` skips. |
| R6 | `pull_history.py` crashes on a fresh clone: `DATA_DIR.mkdir(exist_ok=True)` cannot create `data/history` when `data/` is missing. Seen on Nov 12. | Backend, Repo Maintainer | `pull_games.py:151, 171` | `DATA_DIR.mkdir(parents=True, exist_ok=True)` |
| R7 | Hidden global state: `win_prob` reads `run_ratings.last_played`, a function attribute. If it is called before `run_ratings`, the rest penalty is silently 0. | Structural, Backend | `model.py:104-105, 131` | Return `last_played` and `games_played` from `run_ratings` and pass them in. |
| R8 | The rating engine exists twice (`model.run_ratings`, `tune.run`). They are identical today (verified) but could drift, and `season_ratings.py` uses the `tune` copy. | Structural, Repo Maintainer | `model.py:81`, `tune.py:38`, `season_ratings.py:11` | Make one engine. Keep a quick parity check. |
| R9 | Lock thresholds are hard-coded in the page ("100 finished games"). | Structural, UX | `page_template.html:247` | Send `MIN_GRADED` in `D.track` and render from it. |
| R10 | The Model check gives no progress toward the gain and z bars, only the game count. "Slightly ahead" is shown for any margin. | UX | `page_template.html:237-248` | Show "gain X (needs > 0.003), z Y (needs > 2)". Word the comparison by size. |
| R11 | "Today" and "Tomorrow" chips come from build time. On a stale page viewed the next morning, "Today" is yesterday. | UX, Frontend | `page_template.html:129, 187` | Label the chips with dates, or compare `D.today` with the viewer's date and say so. |
| R12 | A stake of 0 or an empty stake shows $0.00 everywhere with no hint. | UX, Design | `page_template.html:231-235` | Show "Enter an amount" when the stake is 0 or less. |
| R13 | `page_build` writes the permanent record (`save_picks`, `export`). A page-rendering step owns data persistence. | Structural | `build_page.py:63-65` | Move it to an explicit `track.py save` step in the workflow. |
| R14 | Stale docs and helper script (see cross-cutting row 1). | Repo Maintainer | `README.md`, `model.py`, `run_refresh.sh` | Update, or delete `run_refresh.sh`. |
| R15 | No tests. The proof lock and pick rules are the money gate. | Repo Maintainer | (none) | Add `tests/test_track.py`: a no-edge simulation must pass under about 1%, plus a blend-weight leak test, using `unittest` (standard library). |
| R16 | Unused imports. | Repo Maintainer | `model.py:19-20` (`team_notes`, `american_to_decimal`), `build_page.py:10` (`datetime`), `pull_history.py:10` (`date`) | Remove. |
| R17 | `datetime.utcnow()` is deprecated (5 uses). | Repo Maintainer, Backend | `model.py`, `track.py`, `pull_games.py`, `injuries.py` | `datetime.now(timezone.utc)`, keeping the same `...Z` string format. |
| R18 | No `CLAUDE.md` or rules file in the repo. The "Rules to keep" live only in the Google Doc. | Repo Maintainer | (repo root) | Add a short `CLAUDE.md` with the rules and the run commands. |
| R19 | The Google Fonts stylesheet blocks rendering on slow phones. | Performance, Design | `page_template.html:3` | Add `<link rel="preconnect">` for fonts.googleapis.com and fonts.gstatic.com. |

---

# Lens Reports

---

## 1. Design Engineer

**Verdict**: APPROVE WITH CHANGES

**Plan summary**: A one-page, phone-first slate: header and data age, stake input, picks, Model check, track record, explainer and slate with chips.

### Files Reviewed
| File | Visual Role | Risk |
|------|-------------|------|
| `page_template.html` | All styles and markup | Low |
| `build_page.py` (`--site` head) | Doctype, viewport, base styles for Pages | Low |

### Critical Issues
None.

### Recommendations
| # | Issue | File(s) | Why | Fix |
|---|-------|---------|-----|-----|
| 1 | The footer copy describes the retired 30/70 blend (master C2) | `page_template.html:119` | Visible, trust-relevant text that is wrong | Rewrite (see C2) |
| 2 | Chips have `cursor:pointer` but no hover style and no transition | `page_template.html:46-48` | Weak affordance on desktop | `.chip:hover{border-color:var(--accent)}` with a 150ms ease-out color transition, inside `@media (hover:hover)` |
| 3 | The stake input has no invalid or empty styling | `page_template.html:65` | A stake of 0 renders $0.00 silently | Add a `:invalid` or empty hint (also UX R12) |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | Use a `.card` class for Model check and Track rows instead of reusing `.game` | Class names would match their meaning |
| 2 | Move the inline `style="min-width:0"` on `.pick-top` children into a class | Keeps all styles in one place |
| 3 | Check the contrast of `--warn` #b4540a on `--warn-bg` #fbeedd at 13px | It may sit near the 4.5:1 line |
| 4 | The `--site` head sets `color-scheme:light` and body 14px, and the template then overrides both | The overrides work (higher specificity, later rule), but one source would be clearer |

### Design System Compliance
Every color comes from custom properties on `:root`, with complete dark-mode redefinitions under `prefers-color-scheme` (guarded by `:not([data-theme="light"])`) and under `[data-theme="dark"]`. Radii are consistent (6, 8 and 10 px by element size; 999px for chips). Typography uses three variables (display, body, mono), with tabular numbers on figures. No color is hard-coded outside the tokens. Hex values are used instead of oklch, which is fine for this project.

### Visual State Coverage
| Element | Hover | Focus | Active | Disabled | Loading | Empty | Error | Dark Mode |
|---------|-------|-------|--------|----------|---------|-------|-------|-----------|
| Day/league chips | No | Yes (focus-visible) | Yes (aria-pressed) | N/A | N/A | N/A | N/A | Yes |
| Stake input | N/A | Yes | N/A | N/A | N/A | Partial (0 shows $0.00) | No | Yes |
| Picks list | N/A | N/A | N/A | N/A | N/A | Yes (explains no pick) | N/A | Yes |
| Model check | N/A | N/A | N/A | N/A | N/A | Yes | N/A | Yes |
| Track record | N/A | N/A | N/A | N/A | N/A | Yes (pending count) | N/A | Yes |
| Slate | N/A | N/A | N/A | N/A | N/A | Yes ("No games match") | N/A | Yes |
| Data age | N/A | N/A | N/A | N/A | N/A | N/A | Yes (stale warning) | Yes |
| `details` summaries | No | Yes | N/A | N/A | N/A | N/A | N/A | Yes |

Loading is N/A: the data is embedded at build time.

### Verdict Details
- Design Token Fidelity: PASS
- Visual State Coverage: CONCERNS (hover, stake empty state)
- Dark Mode Parity: PASS
- Animation Quality: PASS (no motion)
- Visual Consistency: PASS

---

## 2. UX Engineer

**Verdict**: APPROVE WITH CHANGES

**Plan summary**: A reader opens the page to decide whether to bet tonight. The intended outcome is usually "no bet", which the page explains.

### User Journey Map
1. OK. Open the page, and the header shows "Data pulled ... (N min ago)".
2. WARN. With about 2 refreshes a day, the page often shows the stale warning ("Lines may be out of date"). Readers who see it constantly may learn to ignore it.
3. OK. Enter a stake. Payouts update as you type, and the value is remembered in localStorage.
4. OK. Who to bet on: "No bet recommended right now... see Model check."
5. WARN. Model check shows games and errors but not how far each league is from the gain and z bars. "Slightly ahead" is shown for any margin.
6. OK. Track record shows the 0-8 record with a note that the leans came from the old model.
7. FAIL. Footer: the method description is wrong (30/70 blend).
8. WARN. Slate: "Today" is the build day, not necessarily the viewer's day.

### Critical Issues
| # | Issue | Step in Journey | Impact on User | Fix |
|---|-------|----------------|----------------|-----|
| 1 | The footer states a retired method (C2) | 7 | Misleads the reader about how picks are made | Rewrite the footer |

### Recommendations
| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 1 | No progress toward the lock bars (R10) | Readers can't tell "close" from "never" | Show gain and z against their thresholds |
| 2 | "Today" chip on a stale page (R11) | Wrong day shown as today | Show dates on the chips |
| 3 | A stake of 0 shows $0.00 (R12) | Confusing payouts | Show a hint |
| 4 | Stale-warning fatigue | The warning stops working when it's always on | Fix the refresh cadence, and hide picks with stale odds |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | On Model check, mark replayed games as "replay, not counted" (pairs with C1) | Honest progress |
| 2 | Put a "Last refresh: ok / failed" line in the header | Readers can see when a refresh failed |

### Interaction Consistency Check
Chips use `aria-pressed` toggles throughout. `details` is used for every expandable section. The stake input updates everything live. It's consistent.

### Verdict Details
- Discoverability: PASS
- Flow Completeness: PASS
- Error Recovery: CONCERNS (staleness)
- Feedback & Status: CONCERNS (Model check progress, footer)
- Consistency: PASS

---

## 3. Frontend Engineer

**Verdict**: APPROVE WITH CHANGES

**Plan summary**: Vanilla JS renders an embedded `D` JSON object into innerHTML. React, Zustand, TypeScript and Tailwind checks are N/A.

### Files Reviewed
| File | Frontend Role | Risk |
|------|--------------|------|
| `page_template.html` (script) | Rendering, state, events | Low |
| `build_page.py` | Builds the `D` payload, injects it into the template | Medium (escaping) |

### Critical Issues
None.

### Recommendations
| # | Issue | File(s) | Why | Fix |
|---|-------|---------|-----|-----|
| 1 | `</script>` is not escaped in the embedded JSON (R3) | `build_page.py:77` | Third-party (ESPN) strings could break the page or inject markup | `.replace("</", "<\\/")` |
| 2 | `state.day = D.today` from build time (R11) | `page_template.html:129` | Stale "Today" | Compare with `new Date()` |
| 3 | The Python-to-JS payload has no schema (keys like `as`, `hs`, `hml`) | `build_page.py:39-61` | Silent breakage if a key is renamed | Write a one-line comment of the shape at the top of the script, or run a tiny assert in `build_page.py` |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | Debounce the stake `input` handler by about 150ms | Avoids a full innerHTML rebuild on every keystroke (cheap at today's size) |
| 2 | `esc()` does not escape `'` | Safe today, because every attribute uses double quotes. Add it to be future-proof. |

### Render Path Analysis
Load triggers `ageText`, `renderPicks`, `renderCheck`, `renderTrack` and `renderGames`. A chip click triggers `renderGames` only, through one delegated document listener with no leaks. A stake keystroke triggers `renderPicks` plus `renderGames`, a full innerHTML rebuild of about 10-80 cards. That is fine at this size. No timers or listeners pile up.

### Type Safety Assessment
N/A for TypeScript. The weak point is the untyped data contract between `build_page.build()` and the template. Numbers are inserted unescaped, but they are numbers that Python produces. Strings go through `esc()`.

### Verdict Details
- Component Architecture: PASS
- State Management: PASS
- Type Safety: CONCERNS (untyped payload)
- Rendering Performance: PASS
- Bundle Impact: PASS (no JS dependencies; Google Fonts only)

---

## 4. Backend Engineer

**Verdict**: APPROVE WITH CHANGES

**Plan summary**: The Python standard-library pipeline: ESPN to SQLite (`pull_games`, `injuries`), then ratings, logging, lock and picks (`model`, `track`), then the page (`build_page`).

### Files Reviewed
| File | Backend Role | Risk |
|------|-------------|------|
| `track.py` | Proof lock, grading, persistence | High (money gate) |
| `model.py` | Ratings, prediction logging, pick rules | High |
| `pull_games.py` | Ingest | Medium |
| `injuries.py` | Injury ingest and impact | Medium |
| `tune.py`, `season_ratings.py` | Offline tuning and starting ratings | Medium |
| `bet_math.py` | Odds math | Low (checked: devig, EV, Kelly are correct) |
| `gap_analysis.py` | Research, not on the page | Low |

### Critical Issues
| # | Issue | File(s) | Failure Mode | Fix |
|---|-------|---------|-------------|-----|
| 1 | The lock grades replayed and partly in-sample predictions (C1) | `track.py:111-114` | The lock could pass on flattering early rows | Grade only rows with `logged_at >= CUTOVER` |

### Recommendations
| # | Issue | File(s) | Why | Fix |
|---|-------|---------|-----|-----|
| 1 | Swallowed fetch and parse errors (R5) | `pull_games.py` | A green run with zero data | Exit non-zero when every call fails |
| 2 | Injury table drop-then-recreate (R4) | `injuries.py:75` | Lost injury data if every feed fails | Swap in a filled temporary table |
| 3 | `mkdir` without `parents` (R6) | `pull_games.py:151,171` | Crashes on a fresh clone | `parents=True` |
| 4 | Function-attribute global state (R7) | `model.py:104,131` | Rest penalty silently 0 if called out of order | Pass the values explicitly |
| 5 | Preseason in the ratings (R2) | `pull_games.py`, `model.py`, `tune.py` | Noise in the early-season ratings | Filter by `season_type` |
| 6 | `utcnow()` deprecation (R17) | 4 files | Future break | Use timezone-aware times |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | `tune.run` does not clamp the sigmoid argument the way `model._sig` does | Parity, and avoids an overflow if `cap` is raised |
| 2 | Close SQLite connections explicitly | Short-lived scripts make this harmless, but it's cleaner |

### Error Path Analysis
- **ESPN scoreboard fetch**: 3 tries with backoff, then a message to stderr and `None`. `pull_day` skips the league. If everything fails, `save_db` is never called, the workflow is green, and the page keeps the old `pulled` time. The only signal is the "stale" banner after 90 minutes.
- **ESPN event shape changes**: `parse_event` raises `KeyError` or `IndexError`, which is caught and the event dropped silently. A format change means zero games and no error.
- **Injury feed fails**: prints "! NBA failed", continues. If every feed fails, the table is empty, `team_impact` returns 0 and the penalty disappears silently.
- **Player stats fetch fails**: bare `except: continue`. The player stays uncounted.
- **`track.restore` with a malformed CSV row**: the values are inserted as strings. `league_status` calls `float()` on them, so a non-numeric value would crash the build. That is visible (red run), which is the right behavior.
- **`git push` conflict**: the workflow fails and is visible. The concurrency group prevents overlapping runs.

### Resource Lifecycle Audit
| Resource | Allocated In | Cleaned Up In | Error Path Cleanup | Risk |
|----------|-------------|---------------|-------------------|------|
| SQLite connection | each command | process exit (most), `save_db` closes | process exit | Low |
| HTTP response | `fetch`, `_get` | `with` in `fetch`; not in `injuries._get` | GC | Low |
| `data/` cache | Actions cache per run | GitHub eviction (7 days unused, 10 GB cap) | Rebuild step (50 days, R1) | Medium |
| `log/*.csv` | `track.export` | committed every run | the restore step re-reads them | Low |

### Verdict Details
- Error Handling: CONCERNS (silent failures)
- Concurrency Safety: PASS (Actions concurrency group)
- Resource Management: PASS
- IPC Correctness: N/A (Python to JSON payload covered in Frontend)
- File System Safety: CONCERNS (`mkdir` parents)

---

## 5. Structural Engineer

**Verdict**: APPROVE WITH CHANGES

### Files Reviewed
| File | Structural Role | Layer | Risk |
|------|----------------|-------|------|
| `pull_games.py`, `pull_history.py`, `injuries.py` | Ingest | Data | Medium |
| `bet_math.py` | Pure math | Core | Low |
| `model.py`, `track.py` | Model, lock, persistence | Domain | Medium |
| `tune.py`, `season_ratings.py` | Offline tuning | Tooling | Medium |
| `build_page.py`, `page_template.html` | Presentation | UI | Low |

### Critical Issues
None.

### Recommendations
| # | Issue | Why | Fix |
|---|-------|-----|-----|
| 1 | Two rating engines (R8) | One rule change means two edits | Make one engine |
| 2 | Config scattered: `DB` in 4 files, thresholds in `track`, pick limits in `model`, "100" in the template | Change propagation | `config.py`, and send thresholds in the payload |
| 3 | `pull_history` changes `pull_games.DATA_DIR` at module level | Coupling through mutation | Pass `data_dir` as a parameter |
| 4 | `build_page.build()` writes the permanent log (R13) | The renderer owns persistence | A separate save step |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | Remove the `run_elo` alias once `gap_analysis` no longer needs it | Dead name |
| 2 | Share one log-loss helper (now `track._ll`, `tune.ll`, a lambda in `track.results`, and inline in `model.cmd_backtest`) | Four copies |

### Dependency Map
```
pull_history ──► pull_games ──► (ESPN) ──► data/games.db
injuries ─────────────────────► (ESPN) ──► data/games.db
bet_math ◄── model ──► injuries
              │  └─(lazy import)─► track
build_page ──► model, track, injuries ──► page.html / site/
tune ──► model.PARAMS        season_ratings ──► tune.run, model.PARAMS ──► log/season_start.json
model ──► log/season_start.json (read at import)
gap_analysis (standalone)
```
There are no import cycles: `model` imports `track` lazily and `track` imports nothing local. The concerning arrow is `season_ratings ──► tune.run`, a second engine that sets the live model's starting ratings.

### Boundary Crossing Analysis
| Crossing | From -> To | Clean? | Notes |
|----------|-----------|--------|-------|
| ESPN to SQLite | `pull_games` | Mostly | Silent drops (Backend R5) |
| SQLite to page payload | `build_page` | Mostly | Untyped dict, `</` escaping |
| Offline tuning to live | `season_ratings` to JSON to `model` | Yes | File contract is clear; the engine is duplicated |
| Repo log to DB | `track.restore` | Yes | `INSERT OR IGNORE` never overwrites |

### Verdict Details
- Dependency Direction: PASS
- Module Coupling: CONCERNS (DATA_DIR mutation, function attributes)
- Cohesion: CONCERNS (page build writes the log)
- Layer Boundaries: PASS
- Change Propagation: CONCERNS (duplicated engine and config)

---

## 6. Repo Maintainer

**Verdict**: APPROVE WITH CHANGES

### Files Reviewed
| File | Convention Status | Risk |
|------|------------------|------|
| `README.md` | Out of date | Medium |
| `model.py` | Docstring and backtest text out of date, unused imports | Low |
| `run_refresh.sh` | Out of date and unused | Medium (misleading) |
| `page_template.html` | Footer out of date | High (C2) |
| Others | OK | Low |

### Critical Issues
| # | Issue | File(s) | Convention Violated | Fix |
|---|-------|---------|-------------------|-----|
| 1 | The public page documents the retired method (C2) | `page_template.html:119` | Docs must match behavior | Rewrite |

### Recommendations
| # | Issue | Why | Fix |
|---|-------|-----|-----|
| 1 | README, `model.py` docstring and `run_refresh.sh` are out of date (R14) | A new reader learns the wrong system | Update or delete |
| 2 | No tests (R15) | The money gate has no regression guard | `unittest` for `league_status` and `find_picks` |
| 3 | Unused imports (R16) | Noise | Remove |
| 4 | `utcnow` (R17) | Deprecation | Use timezone-aware times |
| 5 | No `CLAUDE.md` (R18) | The rules live outside the repo | Add one |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | Add `.python-version` (3.11) | Matches CI, avoids the 3.9 on the Mac |
| 2 | Note in the README that python.org installs need "Install Certificates" | Hit on Nov 12 |

### Convention Compliance Checklist
| Convention | Status | Notes |
|-----------|--------|-------|
| File placement | Pass | Flat scripts suit a project this size |
| Naming conventions | Pass | snake_case throughout |
| Barrel exports | N/A | Python scripts |
| Import ordering | Pass | stdlib, then local |
| Type imports separated | N/A | |
| Path aliases used | N/A | `Path(__file__).parent` used consistently |
| No dead code left | Concern | `run_refresh.sh`, `run_elo` alias, `elo_prob` columns (kept on purpose for history) |
| Structured logging | Concern | `print` only (acceptable for CI scripts) |
| No lint suppressions | Pass | None |
| CLAUDE.md present | Fail | Rules only in the Google Doc |

### Reuse Opportunities
`track._ll` and `track._logit` can replace `tune.ll`, the lambda in `track.results`, and the inline log loss in `model.cmd_backtest`. `tune.run` and `model.run_ratings` should be one function.

### Verdict Details
- File Organization: PASS
- Naming Consistency: PASS
- Import Hygiene: CONCERNS (unused imports)
- Code Reuse: CONCERNS (duplicate engine and helpers)
- Lint Compliance: PASS

---

## 7. Production Readiness

**Verdict**: APPROVE WITH CHANGES

### Files Reviewed
| File | Production Role | Risk |
|------|----------------|------|
| `.github/workflows/refresh.yml` | Scheduler, build, deploy | High |
| `track.py` | Permanent record and lock | High |
| `pull_games.py`, `injuries.py` | External dependencies | Medium |

### Critical Issues
| # | Issue | File(s) | Production Failure Mode | Fix |
|---|-------|---------|------------------------|-----|
| 1 | Lock data provenance (C1) | `track.py` | The money gate opens on replayed data | Cutover-only grading |

### Recommendations
| # | Issue | Why | Fix |
|---|-------|-----|-----|
| 1 | Cache rebuild window of 50 days (R1) | From Nov 21 a lost cache silently drops early-season games | Rebuild from season start |
| 2 | Refresh about 2 times a day on GitHub cron (seen Nov 6-12) | Odds snapshots can be hours old. Once picks exist, stale prices cost money | External trigger, and hide picks whose odds are over about 2 hours old |
| 3 | Silent ingest failure (R5) | Green runs with no data | Fail loudly |
| 4 | Injury table drop-then-recreate (R4) | Partial data loss | Swap in a filled table |
| 5 | GitHub disables scheduled workflows in public repos after 60 days without repository activity | Off-season risk | Unverified whether bot commits count. Check in spring, or keep the Mac or external trigger. |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | Write a `site/status.json` with the last run time, games pulled and failures | A quick health check |
| 2 | Pin action versions to SHAs | Supply-chain hygiene |

### Failure Mode Analysis
| Scenario | What Fails | User Impact | Recovery | Handled? |
|----------|-----------|-------------|----------|----------|
| ESPN down for one run | No new games or odds | Stale page, banner after 90 minutes | Next run | Partly (no alert) |
| ESPN changes its JSON | Every event dropped | Page frozen, run green | Manual | No |
| Injury feeds down | Injury table emptied | Picks lose the injury penalty | Next good run | No |
| Actions cache evicted | `data/` rebuilt from 50 days | Ratings differ after Nov 20 | None | No (R1) |
| GitHub cron delayed or skipped | Few refreshes | Old odds | Mac job, manual run | Partly |
| Two runs overlap | - | - | Concurrency group | Yes |
| Push conflict on `log/` | Run fails | Visible red run | Re-run | Yes |
| Malformed CSV row in `log/` | `float()` crash | Red run | Fix the row | Yes (loud) |

### Environment Checklist
| Concern | Dev (Mac) | Prod (Actions) | Gap? |
|---------|-----|------|------|
| Python | 3.9 system, 3.11.9 python.org | 3.11 | Yes: use `python3.11` locally; `run_refresh.sh` uses 3.9 |
| TLS certificates | python.org needs Install Certificates | OK | Fixed on the Mac Nov 12 |
| File paths | `data/` missing on a fresh clone | Created by the cache | Yes (R6) |
| Network | ESPN unofficial | Same | No |
| Config | None | Cron, TZ=America/Chicago | No |
| Build output | `page.html` | `site/index.html` to Pages | No |

### Verdict Details
- E2E Flow: PASS
- Error Recovery: CONCERNS
- Data Integrity: CONCERNS (C1, R4)
- Environment Parity: CONCERNS (Python, mkdir)
- Observability: CONCERNS (no alerting)

---

## 8. DX Engineer

**Skipped**: no developer-facing product. The CLI and onboarding notes (Python version, certificates, missing `data/`, stale `run_refresh.sh`) are covered under Repo Maintainer and Production Readiness.

---

## 9. Performance Engineer

**Verdict**: APPROVE

### Files Reviewed
| File | Performance Role | Risk |
|------|-----------------|------|
| `refresh.yml` | Runtime per run (27s to 1m10s observed) | Low |
| `injuries.py` | Per-player stats calls, 12-hour cache | Low |
| `track.py` | `league_status` O(n × 11) | Low |
| `page_template.html` | Render on input | Low |

### Critical Issues
None.

### Recommendations
| # | Issue | File(s) | Why | Fix |
|---|-------|---------|-----|-----|
| 1 | Render-blocking Google Fonts (R19) | `page_template.html:3` | Slower first paint on phones | Preconnect, or self-host |

### Nice-to-Haves
| # | Suggestion | Rationale |
|---|------------|-----------|
| 1 | `league_status` runs twice per build (in `find_picks` and `track.results`) | Compute once and pass it in. Trivial today. |
| 2 | Debounce the stake input | Fewer full re-renders |

### Resource Budget
| Resource | Current | Concern? |
|----------|---------|----------|
| HTTP calls per run | 18 scoreboard + 4 injury feeds + stats (cached 12 hours) | No |
| Cache entries | 1 per run (17 now, about 290 KB each) | No (10 GB cap) |
| `predictions.csv` growth | About 30-80 rows a day, fully rewritten each run | No |
| Page payload | Today and tomorrow only | No |
| Run time | 27-70 s against a 20-minute timeout | No |

### Hot Path Analysis
The only hot path is the stake input, which rebuilds the picks and games innerHTML. At under 100 cards that's under a frame. Server side, `run_ratings` replays the season (about 2,000 games) in milliseconds. A cold-cache rebuild (50 days × 4 leagues plus player stats) is the slowest case and still well inside the timeout.

### Verdict Details
- Memory Management: PASS
- IPC Efficiency: N/A
- I/O Patterns: PASS
- Startup Impact: CONCERNS (fonts, minor)
- Long-Session Stability: PASS

---

## 10. Accessibility Engineer

**Skipped**: not in the chosen 8-lens set. Seen during the Design review: the stake input has a `<label for>`, the chips use `aria-pressed` with `role="group"` and `aria-label`, focus-visible rings are present, and `details`/`summary` are native. No full keyboard or screen-reader walkthrough was done.
