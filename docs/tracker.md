# Draft tracker

`tracker/` is a standalone TypeScript Cloudflare Worker and static client. It
consumes the Python pipeline's `board.json` v1 contract, serves a manual live
draft at `/`, and provides an isolated roster-aware simulation at `/mock`.

## Runtime model

The immutable board blob lives in KV under `board:current`; the Worker streams
it verbatim from authenticated `GET /api/board`. The last valid `LeagueBundle`
v1 lives under `league:bundle:current` and is accepted by
`POST /api/league/bundle`. That ingest path validates the closed Python
`parse_bundle` contract, never writes DuckDB, and never reads or mutates live
or mock draft tables. Draft state lives in D1. The static shell is public so
the user can enter a shared API key, but every data or mutation route requires
`Authorization: Bearer <TRACKER_API_KEY>`.

The browser saves the key in `localStorage` with an in-memory fallback. Board
version drift or malformed data produces an explicit recovery message instead
of attempting to render an unknown contract.

Player rows may carry an additive injury object with a canonical status and the
Sleeper snapshot's `fetched_at`. The shared live/mock renderer displays a short
text badge plus a full accessible label on every viewport. `UNKNOWN` is rendered
as neutral “Status”; old published and saved mock boards without the optional
object remain valid.

## League bundle ingest

`POST /api/league/bundle` is a producer sink for the closed `LeagueBundle` v1
JSON. Auth is the same bearer key as other `/api/*` data routes. A valid body
replaces KV `league:bundle:current` and returns counts only. Extra keys,
incomplete roster coverage, and other `parse_bundle` failures return 400
`invalid_bundle` and leave the previous value in place. A bundle whose
`synced_at` is older than the stored one returns 409 `stale_bundle`, and a
bundle whose season differs from the published board returns 409
`season_mismatch`; neither replaces the stored value. Bodies over 10 MiB
return 413 `payload_too_large` before validation. `GET /api/league/bundle`
returns the last accepted bundle so the CLI can fetch it later.

## Live draft

First use configures 2–20 ordered teams, exactly one user team, and 1–30 rounds.
The My team view lists only that user team’s saved picks, newest first, with
position filters and the same read-only snapshots as Drafted. It includes picks
missing from the current board and explains empty or unconfigured rosters.
The Worker derives snake order rather than persisting a pick schedule. Each pick
write includes the expected overall pick so a stale tab cannot silently advance
the draft.

| Method and route | Purpose |
| --- | --- |
| `GET /api/draft` | Read configured draft, picks, and next turn |
| `PUT /api/draft` | Configure the current draft before picks exist |
| `DELETE /api/draft` | Delete picks, teams, then draft configuration |
| `POST /api/picks` | Record one board player or validated manual snapshot |
| `DELETE /api/picks/latest` | Undo the displayed latest pick only |

Pick rows snapshot player name, position, and team as well as the board key, so
history remains legible after a board republish. The write API retains a
validated `manual_player` path for a Yahoo pick missing from the board; the
current UI intentionally uses board-row selection.

The live Available board evaluates Brian’s roster against the published board’s
actual dedicated starting slots plus `W/T` and `W/R/T` flex slots. An exact
weighted slot assignment measures each candidate’s increase to the best total
projected starting lineup. A player occupies at most one slot; bench contributes
no starting points. Dedicated starters and flex upgrades compete on their usable
point gain, so a first WR can precede a second TE while an exceptional TE or a
second TE that improves flex can still lead.

Before the user’s first pick, Available retains published board order, including
after opponent picks and replay rewinds. Raw season points do not override the
opening scarcity ranking. Once the user has a pick, rows are ordered by one
continuous opportunity-cost score in projected season points.
[starter-priority.md](starter-priority.md) documents the model, reason strings,
fallbacks, and a worked example; in brief:

- **Value now** is the lineup gain plus a depth share of the player’s remaining,
  non-starting points (`DEPTH_WEIGHT`, 0.25). A full starter counts all points.
  While no bench slot remains, depth is worth nothing.
- **Survival** is the chance the player is still available at the user’s own
  pick after the one they are about to make, from board ADP and `adp_stdev`
  (fallback spread `max(3, 10% of ADP)`). A player without ADP is treated as
  uncontested.
- **Score** is value now minus the expected best value now among available
  players at the same position, the candidate included, at that pick, each
  weighted by surviving while every better player is gone. A player sure to
  last scores about zero. At the last own pick the cost is zero.

Scores are rounded to 0.1 points. Rows explain their starter/flex gain or bench
depth value plus the survival phrase (“likely gone by your next pick”, “coin
flip to last”, or “should last to your next pick”), omitted at the last pick or
without value. These are season projections from a pre-draft ADP snapshot, not
weekly forecasts or a model of this league’s draft pace. Unprojected players
follow all scored rows: open starter fillers, then RB/WR/TE depth in positions
supported by the league, then other depth. If any own pick lacks a current
board projection, the entire roster falls back to open-slot matching and board
rank, with an explicit incomplete-projection message; no numeric gain is
invented. Missing and ambiguous identities retain their pick position for
occupancy. Unknown candidate projections are labeled on the row.

Original board ranks, VORP, and tiers remain visible as static scarcity context.
For equal scores (and within unscored groups), each same-position bye overlap
adds five board-rank places as a soft tiebreak. Unknown byes are neutral, and a
“Bye clash” badge explains overlaps. Bye penalties never erase a higher score.
Positional views preserve tier groups, applying lineup ordering within each
tier. Search relevance and newest-first history
remain unchanged. Only unique own picks count, and the Need summary and gains
recompute after picks and undo. Once starters are filled, the summary announces
depth building while scores continue to rank upgrades and depth.
Mock drafts retain their saved league shape and existing strategy.

## Mock draft

Starting a mock copies the published board into an immutable D1 snapshot. Team
count and rounds come from that snapshot's league shape. The user chooses a
draft slot, unsigned seed, and Calm, Realistic, or Wild opponent variance.

New simulations use the versioned `market-need-v1` strategy; existing
`seeded-market-v0` sessions remain resumable. Opponents combine market order,
open starter need, tier value, specialist timing, and seeded Gumbel variance.
Seed zero normalizes to a nonzero RNG state.

One user decision is one authoritative Worker transition: record the choice,
advance CPU turns until the next user decision or completion, and return the
full pick snapshot. The client rebuilds every view from that response and the
saved board rather than updating optimistically.

Mock lifecycle writes require both `mock_id` and the displayed monotonic
`expected_revision`:

| Route | Effect |
| --- | --- |
| `POST /api/mocks` | Start a new mock from the current board |
| `GET /api/mocks/current` | Load the current isolated session |
| `POST /api/mocks/current/picks` | Record the user's decision and CPU response |
| `POST /api/mocks/current/pause` | Pause the session |
| `POST /api/mocks/current/resume` | Resume the session |
| `DELETE /api/mocks/current/picks/latest` | Rewind the latest user decision and following CPU picks |
| `POST /api/mocks/current/reset` | Replay the seeded opening in the same session |
| `DELETE /api/mocks/current` | Discard only the mock session |

Before each user decision, the store checkpoints pick count and RNG state. Undo
removes the user's latest decision plus all CPU picks it caused and restores the
checkpoint. Restart keeps the mock id, board, strategy, variance, and user slot
while rebuilding the initial seeded prefix.

## Roster safety and identity

`roster-fit.ts` uses exact capacity matching across dedicated positions,
`W/T`, `W/R/T`, and bench. Every accepted user or CPU position must preserve a
path for every team to complete its roster from the remaining league-wide
supply.

`player-identity.ts` centralizes canonical, fallback, manual, and DEF/DST
equivalence. Search, availability, suggestions, and writes share the same
identity rules. Bridging stays conservative: if the board itself has duplicate
canonical rows with one normalized `(position, name, team)` signature, fallback
or manual picks do not hide either canonical row by guesswork.

## Shared client behavior

Live and mock clients share board view state, player-pool construction, search,
availability, rendering, selection, progressive loading, and snake-clock
presentation.

- Available and Drafted modes are independent from position filters.
- Available positional views use tier dividers; Drafted shows the most recent pick first.
- Lists render in 50-row windows and re-render a larger prefix on “load more.”
  Tier survivor counts always describe the full remaining tier.
- Pick recording preserves the grown list limit so DOM fast paths and state do
  not diverge.
- Mock “Likely next” suggestions show up to three market-leading available
  players restricted to roster-completable positions. Suggestions are advisory;
  the Worker remains authoritative.

At widths of 1024px and above, `/mock` uses a two-pane workspace with an
independently scrolling board and bounded decision rail. Below that breakpoint,
the same state and controls use a single-column layout with a remembered Pick
tools disclosure. Viewport changes never mutate board state or send a request.
The detailed implemented specification remains in
[specs/mock-draft-responsive-desktop.md](specs/mock-draft-responsive-desktop.md).

The speaker button in both headers plays the ESPN draft chime on demand.
Repeated clicks restart the same clip; playback never starts automatically.
The bundled `public/audio/espn-draft-chime.mp3` comes from the
[NFL Draft Chime download](https://instantsbutton.com/sound/nfl-draft-chime)
(retrieved September 6, 2026). Playback failures show a retry message.

## Local validation

Use the exact versions pinned by `.nvmrc` and `package.json`:

```sh
cd tracker
nvm use
npm ci
npm run typecheck
npm test
npm run build:client
npm run test:browser
```

The Playwright suite covers phone, minimum desktop, standard desktop, and short
desktop viewports against committed fixtures. It does not read or mutate local
Wrangler KV or D1 state. Run `make test-backend-e2e` from the repository root
when Worker routes, APIs, D1 behavior, or the board boundary change.

## Recommendation backtest

`tracker/src/backtest.ts` scores draft rankers against a completed saved draft
without network, D1, or KV. A ranker is any `{ name, order(context) }` that
returns the available pool best-first given the board and the draft state at
the user's turn, with an optional `explain(context, player)` note for the
per-turn report. `backtestDraft(saved, board, rankers)` reports two things per
ranker:

- **Per-turn comparison.** At each recorded own pick, given the actual history
  to that point, what the ranker would have recommended, whether it agreed with
  the recorded pick, and the projected starting-lineup gain of each choice
  against the roster actually held then.
- **Followed replay.** The whole draft replayed with every own pick following
  the ranker while every opponent pick stays as recorded. A recorded opponent
  pick the ranker already took vanishes from that opponent rather than
  displacing the user. The result is the final projected starting lineup
  (`projectedLineup` over the board's roster slots; bench never scores).

Built-in rankers are `board` (published rank), `market` (ADP order, as mock
suggestions use), and `live` (the live Available ordering, sharing
`liveStarterPriority` and `lineupPriorityOrder` with the renderer; its per-turn
lines print the score, survival, and row reason). Output is deterministic for a
given board and draft. Run it locally against an exported board:

```sh
cd tracker
npm run backtest -- --board ../exports/board.json --draft ../drafts/mcffl-2026.json
npm run backtest -- --delta 0.1,0.25   # sweep DEPTH_WEIGHT as live@N rankers
npm run backtest -- --all-teams           # replay from every team's seat
npm run backtest -- --json    # machine-readable report
```

The saved league draft has no frozen projections, so results depend on the
board you pass; the September 6, 2026 export is the one the draft was made
with. The saved pick order is Brian's own record and may contain errors.
`--all-teams` reruns the same comparison with each team in turn as the user
(`asUserTeam`), keeping every other team's recorded picks, and prints followed
totals by draft slot. Results for the September 6 board are in
[starter-priority.md](starter-priority.md#validation). A replacement ranker
should not lower `live`'s followed total for Brian's seat or across every
seat by more than noise.

## Saved draft replay

Board settings offers **Replay MCFFL 2026 Draft**, using the completed 150-pick
archive bundled into the client at build time. Resetting the live draft only
deletes its live picks and teams; the bundled archive remains replayable. Replay
uses the normal live-board client, current `GET /api/board` data, and current
recommendation code. Only a prefix of the saved pick list is presented as draft
state. No replay action writes live or mock D1
state, and the shared write function refuses all writes while replay is active.

The replay controls step backward/forward, jump to just before the next own
pick, seek to any saved pick, and refresh the current board. The original teams
and picks remain fixed; a missing player retains its saved identity and position
for the existing incomplete-projection fallback. The board is not frozen into
the archive. Refresh and reload use the current published board.

Replay state is held in sessionStorage for this tab and survives reload when
storage is available. It falls back to memory if storage fails. Exiting replay
reloads the live draft without changing it. Saved replay sessions must contain
a valid ordered snake draft with unique players, contiguous picks, and one user team.
The saved JSON contains teams and picks, not credentials or a board snapshot.
The completed MCFFL 2026 draft is preserved in
[`drafts/mcffl-2026.json`](../drafts/mcffl-2026.json) for repeatable replay.
No file import or download is needed to replay it.
