# Wed–Sun refresh job

The scheduled morning job that keeps the `/command` dashboard current for both
leagues. It runs daily at about 07:00 ET and does work Wednesday through
Sunday; on Monday and Tuesday it does nothing. The schedule itself lives
outside this repository. This page holds everything an operator (human or
agent) needs to run one pass by hand. [operations.md](operations.md#in-season-refresh-runbook)
explains why the cadence and command order are what they are.

## Ground rules

- Never write to Yahoo: same-origin page reads only, no lineup, waiver, or
  setting changes, and no Yahoo API calls.
- Never edit, commit, stash, or switch branches in the checkout the job runs
  from. If it is clean and on `main`, `git pull --ff-only` first; otherwise
  run from whatever is checked out.
- Always refresh both leagues, and publish every card with its matching
  `--league` so the per-league Worker slots do not overwrite each other.
  Worker `league=` query parameters take full keys.
- Secrets come only from the repository `.env`. Never print values; list the
  names with `sed -E 's/=.*/=<set>/' .env`.
- Finish with a short summary, including on hard failure (see
  [Summary](#summary)).

## Leagues

| League | Full key | League state comes from |
| --- | --- | --- |
| Yahoo MCFFL (Turkey Supreme) | `yahoo:470.l.928421` | The live Yahoo UI, captured by the `yahoo-league-bundle` skill and POSTed to the tracker; the CLI imports it with `--from-tracker` |
| Sleeper | `sleeper:$FFB_SLEEPER_LEAGUE_ID` | The live Sleeper API; `make refresh` re-posts the bundle when the tracker's copy is stale |

## Environment

`.env` at the repository root must define:

| Name | Use |
| --- | --- |
| `FFB_TRACKER_URL` | Tracker base URL (`https://ffb.bbell.dev`) |
| `FFB_TRACKER_API_KEY` | Bearer token for every tracker route, reads included |
| `FFB_SLEEPER_LEAGUE_ID` | Sleeper league id |
| `FFB_SLEEPER_USER_ID` | Brian's Sleeper user id |
| `ANTHROPIC_API_KEY` | Digest narrative; without it digests publish with no narrative |

A missing `.env` or missing tracker/Sleeper key is a hard failure: stop and
report. `make refresh` loads `.env` itself, but the `ffb` CLI does not. For any
manual `ffb` or `curl` command, source it in the same shell invocation:

```sh
set -a && . ./.env && set +a
```

Tracker requests need both the bearer token and a browser User-Agent.
Cloudflare rejects bare script agents with error 1010, and every route returns
401 without the key:

```sh
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36'
curl -fsS -A "$UA" -H "Authorization: Bearer $FFB_TRACKER_API_KEY" "$FFB_TRACKER_URL/api/leagues"
```

## Fast path

One command does all the CLI work: `make refresh`
([scripts/inseason-refresh.sh](../scripts/inseason-refresh.sh)). Pick the
arguments by day:

| Day | Run |
| --- | --- |
| Mon–Tue | Nothing. Record "no run scheduled (Mon/Tue)". |
| Wednesday | Post Yahoo `W-1` actuals first ([Wednesday](#wednesday-week-roll)), then `make refresh ARGS="--week-roll"`, then post Sleeper actuals |
| Thu–Sat | `make refresh` |
| Sunday | `make refresh ARGS="--sunday"` (projections, injuries, and news only; pre-kickoff) |

What it does, in order:

1. Loads `.env` and checks the required keys.
2. Reads season and week from Sleeper `/state/nfl`.
3. Prints `bundle <key>: … -> fresh|STALE` for each tracker LeagueBundle.
   Fresh means synced today (local date) for the current week; missing counts
   as stale.
4. Runs `season sync`.
5. Syncs both leagues. The Sleeper sync adds `--push` when its bundle is stale
   so `/api/leagues` catches up.
6. Publishes `ros`, `lineup --force`, and `digest` for both leagues. With
   `--week-roll` it also runs the Sleeper `W-1` backfill and retro, plus the
   Yahoo retro when the Worker already holds `W-1` Yahoo actuals (otherwise it
   prints `skipped`).
7. Prints each dashboard card's `generated_at`.

Stdout is a filtered summary (`ready`, `Synced`, `Published`, `Not published`,
`Start`, `Sit`, `Close`, `⚠` lines). The full log goes to
`data/refresh-logs/<timestamp>.log`. A run takes about 30 seconds plus a few
minutes for the season sync.

Other options, combined in one quoted `ARGS` string:

| Option | Effect |
| --- | --- |
| `--dry-run` | Read-only preview: week, freshness, the commands it would run, and `would skip:` lines |
| `--skip-sync` | Skip `season sync` |
| `--yahoo-only` | Skip every Sleeper step |
| `--week N` | Override the Sleeper week |
| `--allow-stale` | Publish Yahoo cards from a stale Yahoo bundle. Do not use in scheduled runs. |

### Outcomes

- **Non-zero exit.** Hard failure. The `FAIL` line names the command; report
  it.
- **Stale Yahoo bundle.** The run still finishes the season sync and every
  Sleeper step, skips the Yahoo league sync and the three Yahoo cards
  (`[yahoo-…] skipped: … bundle is STALE`), and ends with:

  ```text
  next: yahoo:470.l.928421 bundle is STALE; run the yahoo-league-bundle skill, then: make refresh ARGS="--skip-sync --yahoo-only --week W"
  ```

  `make refresh` still exits 0. The script exits 3, but make cannot forward
  it, so the `next:` line is the signal. Run the
  [`yahoo-league-bundle`](../.claude/skills/yahoo-league-bundle/SKILL.md)
  skill to capture and POST the bundle, then run the printed command exactly
  once. It publishes only the Yahoo cards (about 30 seconds). Do not rerun the
  full refresh. If the rerun prints `next:` again, the POST did not land:
  report it instead of looping.
- **Stale Sleeper bundle.** Nothing to do; the run pushes it. `Not published`
  on the Sleeper sync (409 `stale_bundle`, meaning the Worker already holds a
  newer bundle) is not an error.

## Wednesday week roll

Before scraping anything, check whether each league's `W-1` actuals and retro
already exist:

```sh
GET $FFB_TRACKER_URL/api/actuals?season=S&week=W-1&league=<full key>
GET $FFB_TRACKER_URL/api/inseason?season=S&week=W&league=<full key>   # actuals_available
```

Skip a league's scrape and POST when both are present, unless a correction is
needed.

### 1. Yahoo actuals (before `make refresh`)

Scrape the `WeeklyActualsBundle` for the finished week `W-1`: all 10 teams,
bench included (15 players per team, 150 total), and 5 matchups. Starter
points must match the scoreboard. Never paste next week's projections into
actuals.

Read Yahoo in the Claude in Chrome extension (`navigate` plus
`javascript_tool`); Chrome is already signed in. Computer-use grants for Chrome
are refused in scheduled runs, and `get_page_text` returns only the page
header, so pull the table with JavaScript.

- Team page for a past week:
  `https://football.fantasysports.yahoo.com/f1/928421/<team_id>/team?week=<W-1>`
  (team ids 1–10; Brian is 9). The page without `?week=` shows the live week's
  partial points. Never use it for actuals.
- Cells in each `table tbody tr` `td`: `[0]` slot (`QB`, `RB`, `WR`, `TE`,
  `W/T`, `W/R/T`, `BN`, `DEF`); `[1]` player text such as
  `Name Tm - Pos Final W 20-3 vs Pit`; `[2]` bye; `[3]` fan points; `[4]`
  projected points. Strip `Video Forecast`, `Player Note`, and
  `New Player Note`, then split the name on `/ [A-Z][A-Za-z]{1,2} - /`. A
  trailing injury letter can stick to the name (`Rico DowdleO`). Skip rows with
  fewer than 6 cells (the tier legend) and `(Empty)` rows. On the live,
  editable week an extra slot-eligibility cell (for example `QBBN`) sits at
  `[1]` and shifts everything after it right by one; past weeks do not have it.
- Check that each team's non-`BN` starter points sum to its weekly score and
  that each matchup total matches the header and scoreboard.
- Stored shape (as `GET /api/actuals` returns it):

  ```text
  {schema_version: 2, source: "yahoo", synced_at,
   league: {league_id, league_key, name, season, week, num_teams},
   matchups: [{matchup_id, week, teams: [{team_key: "470.l.928421.t.N", points}]}],
   players: [{native_id, native_player_key, name, team_key, selected_position, points}]}
  ```

POST it to `/api/actuals?league=yahoo:470.l.928421` with the browser
User-Agent. The contract is in [grok-bot-prompt.md](grok-bot-prompt.md) and
[tracker.md](tracker.md).

To correct actuals that were already graded, POST the corrected bundle, save
the same JSON to a scratch file, and regrade from it:

```sh
uv run ffb retro S --week W-1 --league yahoo --fixture <file> --force --publish
```

Without `--fixture`, retro keeps grading the locked local copy; `--force`
alone does not re-pull from the tracker.

### 2. `make refresh ARGS="--week-roll"`

That run publishes the Yahoo retro from the actuals just posted and runs the
Sleeper backfill and retro:

```sh
ffb league sync S --league sleeper --week W-1                    # caches /matchups for W-1
ffb retro S --week W-1 --league sleeper --from-matchups --publish  # locks actuals, publishes retro
```

Retro does not POST the Sleeper actuals.

### 3. Sleeper actuals (after `make refresh`)

POST the locked file
`snapshots/actuals/sleeper_<FFB_SLEEPER_LEAGUE_ID>/<S>_week<W-1>.json` to
`/api/actuals?league=sleeper:<FFB_SLEEPER_LEAGUE_ID>` with the browser
User-Agent. Confirm `GET /api/actuals` for the Sleeper key returns
`source: "sleeper"` and that Yahoo's same-week actuals are intact.

## Manual steps

Use these when `make refresh` fails partway and a single step needs a rerun.
`S` is the season and `W` the current week. Source `.env` in every shell, and
pipe output through `tail` to keep it short. Each successful publish prints
`Published <kind> week W to the tracker.`

1. **Week.** `curl -s https://api.sleeper.app/v1/state/nfl`; read `.season`
   and `.week`.
2. **Bundle freshness.** `GET /api/leagues` returns each league's `synced_at`
   and `current_week`. If the Yahoo bundle is not from today for week `W`, run
   the `yahoo-league-bundle` skill first. If the Sleeper bundle is stale or
   missing, add `--push` to its league sync.
3. **Commands, in order:**

   ```sh
   uv run ffb season sync S --week W --refresh
   # Sunday instead: --source projections --source injuries --source news
   uv run ffb league sync S --from-tracker               # Yahoo: 10 teams, 150 roster players, all matched
   uv run ffb league sync S --league sleeper [--push]    # Sleeper: 12 teams, ~185 roster players, all matched
   for L in yahoo sleeper; do
     uv run ffb ros S --league $L --publish
     uv run ffb lineup S --week W --league $L --force --publish
     uv run ffb digest S --league $L --publish
   done
   ```

4. **Dashboard check.** For both full keys,
   `GET /api/inseason?season=S&week=W&league=<full key>`. Read
   `cards.{lineup,digest,ros,retro}.envelope.generated_at` and `week`, plus
   `league.synced_at` and `actuals_available`. Freshness badges are computed
   client-side, so report the timestamps. Retro shows week `W-1` until the
   next Wednesday; that is expected.

For a specific badge that is not green, use the card-by-card recovery table in
[operations.md](operations.md#in-season-refresh-runbook).

## Summary

Lead with actionable sit/start swaps for each league (the lineup `Start`/`Sit`
lines and their Δ). Then give publish status, including whether the Yahoo
bundle had to be rebuilt. End with soft failures, such as thin news or an
unmatched DEF, and emphasize late injury news on Sunday. Hard auth or tracker
failures stop the run and are reported as such.

Known baseline; mention these only if they get worse:

- About 1,085 Sleeper player-map rows do not match the crosswalk
  (fringe and practice-squad names). A couple of ESPN rows are unmatched.
- News is thin: roughly half of the headlines match a player.
- Not modeled: Yahoo "Extra Point Returned"; Sleeper `def_st_fum_rec`,
  `st_ff`, and `st_fum_rec`.
- Yahoo lineups have no K slot (QB, RB, WR, TE, W/T, W/R/T×2, DEF). Sleeper has
  K and W/R/T×2.
