# Draft tracker

`tracker/` is a standalone TypeScript Cloudflare Worker and static client. It
consumes the Python pipeline's `board.json` v1 contract, serves a manual live
draft at `/`, and provides an isolated roster-aware simulation at `/mock`.

## Runtime model

The immutable board blob lives in KV under `board:current`; the Worker streams
it verbatim from authenticated `GET /api/board`. Draft state lives in D1. The
static shell is public so the user can enter a shared API key, but every data or
mutation route requires `Authorization: Bearer <TRACKER_API_KEY>`.

The browser saves the key in `localStorage` with an in-memory fallback. Board
version drift or malformed data produces an explicit recovery message instead
of attempting to render an unknown contract.

Player rows may carry an additive injury object with a canonical status and the
Sleeper snapshot's `fetched_at`. The shared live/mock renderer displays a short
text badge plus a full accessible label on every viewport. `UNKNOWN` is rendered
as neutral “Status”; old published and saved mock boards without the optional
object remain valid.

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
second TE that improves flex can still lead. Each available row explains its
starter/flex contribution or depth role. These are season projection gains,
not weekly forecasts or estimates of the cost of waiting until the next pick.

Positive lineup gains lead, ordered largest first. An unprojected player who can
fill an open starting slot follows measured gains, then RB/WR/TE depth in
positions supported by the league, then other depth. If any own pick lacks a
current board projection, the entire roster falls back to open-slot matching
and board rank, with an explicit incomplete-projection message; no numeric gain
is invented. Missing and ambiguous identities retain their pick position for
occupancy. Unknown candidate projections are labeled on the row.

Original board ranks, VORP, and tiers remain visible as static scarcity context.
For equal lineup gains (and within fallback/depth groups), each same-position
bye overlap adds five board-rank places as a soft tiebreak. Unknown byes are
neutral, and a “Bye clash” badge explains overlaps. Bye penalties never erase a
larger measured lineup gain. Positional views preserve tier groups, applying
lineup ordering within each tier. Search relevance and newest-first history
remain unchanged. Only unique own picks count, and the Need summary and gains
recompute after picks and undo. Once starters are filled, the summary announces
depth building, while any remaining positive lineup upgrades still lead.
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
