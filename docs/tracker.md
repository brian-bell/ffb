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

## Live draft

First use configures 2–20 ordered teams, exactly one user team, and 1–30 rounds.
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
- Available positional views use tier dividers; Drafted remains chronological.
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
