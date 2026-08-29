# Responsive desktop mock-draft route

Status: implemented

Route: `/mock`

Tracking: `ffb-oeb.9` under the desktop experience epic

Last updated: 2026-08-04

## Outcome

At desktop widths, the existing `/mock` route becomes a deliberate two-pane
draft room: the player board remains the primary work surface and the current
decision, suggestions, and mock lifecycle controls stay visible in a persistent
right rail. The same route, API, saved mock, board snapshot, and client state
continue to power the compact phone view.

This is a responsive presentation change. It does not create a desktop app, a
second mock client, or a parallel mock-draft model.

Implementation landed as semantic workspace wrappers, responsive CSS, a pure
pick-tools presentation helper, and a fixture-backed Playwright journey. The
browser coverage includes the fixed phone, desktop-minimum, desktop-standard,
and short-height viewports plus saved-board recovery and persisted page
lifecycle behavior. No Worker API, D1, `board.json` v1, strategy, or live-draft
storage boundary changed.

## Context

The current implementation is intentionally phone-first:

- `.screen` is capped at 560 px and fills one dynamic viewport height.
- Setup, active status, filters, board rows, and the pick dock are stacked.
- The pick controls are collapsed behind **Pick tools** so the board retains
  useful vertical space.
- `/mock` already shares `BoardViewState`, `buildPlayerPool`, `renderBoard`,
  selection behavior, identity-aware availability, progressive loading, and
  the snake clock with the live route.
- Mock creation, picks, pause/resume, undo, restart, and discard already use
  authoritative `MockState` responses and monotonic `mock_id` +
  `expected_revision` mutation guards.
- The saved immutable board—not the latest KV board—continues an existing mock.

Those behaviors are the source of truth. Desktop work should rearrange them,
not reinterpret them.

## Goals

1. Make `/mock` efficient and legible at common laptop and desktop viewport
   sizes without requiring a separate build or URL.
2. Keep the player board, current turn, selection, primary draft action,
   suggestions, and lifecycle controls simultaneously visible while a mock is
   active.
3. Preserve the current compact/mobile flow and touch targets.
4. Preserve all mock/live isolation, roster-safety, identity, stale-write, and
   immutable-board guarantees.
5. Cover the responsive split and the high-frequency desktop pick journey with
   browser-level tests.

## Non-goals

- Native packaging, an installable PWA, offline support, or a second desktop
  deployment.
- Changes to `board.json` v1, D1 tables, Worker API routes, opponent strategy,
  or live-draft data.
- Persisted mock history. Review (delivered by `ffb-w0y.5`, see “Completed-mock
  review”) covers only the most recent completed mock.
- Persisted CPU decision rationale. The current public mock state does not
  expose it.
- New keyboard shortcuts. Keyboard-first drafting is tracked separately by
  `ffb-oeb.3`; this work must still provide correct focus order and visible
  focus.
- Re-ranking, client-side projections, or desktop-only player eligibility.

## Supported responsive behavior

The desktop breakpoint is based on viewport space, not device detection.

| Viewport | Layout | Support expectation |
| --- | --- | --- |
| `< 768 px` | Existing compact single column and bottom pick dock | Fully supported; no intentional behavior change |
| `768–1023 px` | Comfortable single column, centered; bottom dock remains | Supported intermediate layout; no two-pane assumptions |
| `>= 1024 px` and `>= 720 px` high | Two-pane desktop draft room | Primary desktop target |
| `>= 1024 px` and `< 720 px` high | Two-pane content may document-scroll; no control may be clipped | Best-effort short-height fallback |

The desktop content width is capped at 1440 px and centered. The decision rail
uses `clamp(320px, 28vw, 400px)`; the board receives the remaining width with a
minimum usable width of 600 px. The page must not develop horizontal scrolling
at 1024 px.

The `1024 px` split is a contract for this implementation and its tests. It may
move later only with corresponding viewport test changes.

## Desktop information architecture

### Active mock

```text
+------------------------------------------------------------------------------+
| DRAFTMOCK   Rd 4 P37 · Brian   ISOLATED MODE   Return to live draft          |
| Mock picks only · live draft untouched                                       |
+------------------------------------------------------------------------------+
| Seed | Variance | Slot | Round | Overall | Status | Revision                  |
+-----------------------------------------------+------------------------------+
| Available | Drafted | Search                  | YOUR DECISION                |
| ALL  QB  RB  WR  TE  K  DEF                   | Round 4… Brian on the clock  |
| #  Player                    VORP  ADP  +/-    | Selected player              |
|-----------------------------------------------| [Draft player] [Clear]       |
|                                               |                              |
| Scrollable board rows                         | LIKELY NEXT                  |
|                                               | Suggestion 1                 |
|                                               | Suggestion 2                 |
|                                               | Suggestion 3                 |
|                                               |                              |
|                                               | LATEST TRANSITION            |
|                                               | Recent CPU picks             |
|                                               |                              |
|                                               | [Pause] [Undo decision]      |
|                                               | [Restart from seed]          |
|                                               | [Discard mock]               |
+-----------------------------------------------+------------------------------+
| board.json v1 · generated timestamp                                          |
+------------------------------------------------------------------------------+
```

The desktop workspace has one viewport-height shell:

- The app bar, safety message, compact status strip, and footer do not scroll.
- The board pane owns the main vertical scroll.
- The right rail remains visible and may scroll independently if its content
  exceeds the available height.
- The body itself does not scroll at supported desktop heights.

The visual hierarchy is board first, decision second, lifecycle utilities last.
Destructive controls must not visually compete with **Draft player**.

### Setup

Before a mock exists, the board workspace is replaced by a centered setup
surface with a maximum width of 900 px:

- Left: seeded-rehearsal explanation, isolation statement, and league shape.
- Right: draft slot, seed, opponent variance, validation message, and
  **Start seeded mock**.

At compact widths this collapses to the existing single-column order. Starting
a mock uses the same `POST /api/mocks` request and immediately transitions to
the active desktop workspace returned by the Worker.

### Locked/authentication state

The existing API-key modal remains centered above a blurred route shell. At
desktop widths the modal must not stretch with the workspace. Focus moves to
the key input when locked, the form submits with Enter, and a `401` clears the
stored key and returns focus to the same flow.

## Component behavior

### App bar and isolation treatment

- Keep `DRAFTMOCK`, the green isolated-mode treatment, the current snake-clock
  summary, and **Return to live draft**.
- Keep the explicit “Mock picks only · live draft untouched” safety message at
  every viewport. Desktop may render it more compactly, but it may not disappear
  or rely on color alone.
- The live-route link remains a normal navigation. It does not mutate, pause, or
  discard the mock.

### Status strip

Show the existing seven values: seed, variance, slot, round, overall, lifecycle,
and revision. At desktop widths they remain on one row. Values use tabular
numerals, truncate rather than resize the grid, and retain text labels.

Revision is diagnostic metadata, not a primary callout. It remains visible
because it explains stale-tab recovery during development and rehearsal.

### Board pane

- Preserve Available/Drafted modes, position filters, search replacement,
  progressive loading, tier dividers, and board ordering.
- Preserve the existing five columns: rank, player, VORP, ADP, and delta. More
  desktop width is used for player names and calmer spacing, not additional
  calculations or a second row model.
- The column header stays aligned with board rows and remains visible above the
  board scroller.
- Position filters stay horizontally usable at all widths; at desktop they
  should fit without scroll for the current standard position allowlist.
- Search continues to show identity-aware available results and temporarily
  disables mode and position controls as it does today.
- A player row is selectable only while the mock is active, the Worker is not
  processing a write, and Brian is on the clock. Paused and completed mocks
  render informational, non-selectable rows.
- Selecting a board row or suggestion updates only the affected row and the
  decision rail; it must not reset the board scroll position.
- `visibleLimit` remains stable through selection and a recorded pick, exactly
  as the shared `BoardViewState` contract requires.

No desktop-specific board renderer should be introduced. Changes needed for
semantic wrappers or CSS hooks belong in the shared rendering seam only when
they also preserve the live view.

### Decision rail

The right rail is the desktop form of the current pick dock.

- **Draft player** and **Clear** are always exposed at desktop widths. The
  compact **Pick tools** disclosure remains unchanged below 1024 px.
- The on-clock sentence and selected-player summary sit immediately above the
  primary action.
- **Draft player** remains disabled until a legal available row is selected and
  `mockActionState.can_pick` is true.
- The three market-leading, roster-safe **Likely next** suggestions remain
  advisory. Clicking one selects it; it does not draft it.
- The latest transition shows the current `appended_picks` summary. It must not
  imply a durable complete pick log; Drafted mode remains the authoritative
  history view.
- Pause/Resume, Undo decision, Restart from seed, and Discard mock retain their
  current labels, enablement, confirmations, and request semantics.
- Keep the undo explanation visible: one undo removes Brian’s latest decision
  and all CPU picks caused by it.

The rail does not calculate roster legality. Suggestions use the existing pure
`mockSuggestions` path, and the Worker remains the final authority.

### Responsive pick-tools presentation

`BoardViewState.pickToolsExpanded` remains the compact disclosure state. The
desktop presentation must not overwrite it:

- Entering desktop width exposes the pick buttons and hides the disclosure
  toggle as a presentation rule.
- Returning below 1024 px restores the prior compact expanded/collapsed state.
- Resizing never selects or clears a player, changes filters, resets progressive
  loading, or sends a request.

Use `matchMedia("(min-width: 1024px)")` only to keep `hidden` and accessibility
state correct for the disclosure. Layout itself stays in CSS. The listener must
be removable and must work with both modern `change` events and the test seam.

## State requirements

| Route state | Board pane | Decision rail | Required actions |
| --- | --- | --- | --- |
| Loading authenticated state | Stable shell or compact loading message; no fake rows | Mutations disabled, busy status announced | None until authoritative state arrives |
| No configured mock | Setup replaces board and rail | Not rendered | Start mock |
| Active, Brian on clock | Available rows selectable | Selection, suggestions, pick and lifecycle controls | Draft, clear, pause, undo if allowed, restart, discard |
| Write pending | Existing rows remain visible with `aria-busy=true` | All mutations disabled; submitted selection remains legible | Await one transition only |
| Paused | Board remains browsable but rows are not selectable | Resume is primary; suggestions say “Resume to see suggestions” | Resume, undo if allowed, restart, discard |
| Complete | Board remains available for Available/Drafted review but is not selectable; board view lands on Drafted (the ordered pick log) on entry | Review surface replaces selection, pick tools, suggestions, and latest transition; Pause disabled | Replay this mock, start another mock, return to live draft, undo if allowed |
| Stale `409` | Reload authoritative mock, clear an invalid selection, preserve valid view state when possible | Show returned conflict message after reconciliation | Retry only from refreshed revision |
| Saved board unreadable | Replace board pane with a specific recovery notice | Only Discard remains enabled | Discard and return to setup |
| Published board unavailable, no mock | Setup remains visible with league values unavailable | Not rendered | Start disabled until reload succeeds |
| Authentication failure | Locked overlay; underlying data is inert and obscured | Inert | Re-authenticate |

### Completed-mock review (`ffb-w0y.5`)

When the authoritative state reports `lifecycle: "complete"` with a usable
board, the decision rail becomes a review surface, derived purely from the
returned `MockState` (`src/mock-review.ts`):

- An outcome summary records the reproduction config verbatim: seed, variance,
  slot, rounds, strategy version, and board fingerprint.
- One roster accordion entry per team, grouped by draft slot in pick order with
  a position-count line. The user roster is listed first, open, and marked with
  a “YOU” chip; user-sourced picks are marked in every roster.
- The board view switches to Drafted once on entering completion (including a
  fresh load of a completed mock), so the ordered pick log is front and center;
  the user may switch back freely.
- Actions reuse the existing lifecycle endpoints and stale-revision guards:
  **Replay this mock** (reset from the saved seed), **Start another mock**
  (discard, returning to setup), and a prominent **Return to live draft** link.
  Undo still exits review back to the active workspace.

Review survives refresh because `GET /api/mocks/current` keeps returning the
most recent completed mock. Saved-board recovery always wins over review.

## Interaction details

### Selecting and drafting

1. The user filters, searches, or scrolls the board.
2. Selecting a row highlights it and updates the rail without moving focus or
   scroll.
3. Selecting the same row again clears the selection.
4. **Clear** removes only the selection.
5. **Draft player** submits the selected key with the displayed mock ID and
   revision.
6. While pending, every mutation control is disabled and busy state is exposed.
7. On success, the client applies the full returned `MockState`, clears search
   and selection, preserves the grown list limit, announces the number of picks
   recorded and next team, and rebuilds board availability and suggestions.
8. On a conflict, the client reconciles from the Worker before enabling another
   write.

There is no optimistic pick mutation.

### Pause, undo, restart, and discard

- Pause/Resume and Undo keep the current board filter and grown list; they clear
  selection if it can no longer be used.
- Restart uses the existing confirmation, preserves the saved mock identity and
  configuration, resets the board view, and replays the seeded opening.
- Discard uses the existing confirmation, removes only the mock session, loads
  the current published board for setup, and generates a new seed.
- Confirmation copy must continue to state whether the mock ID/configuration is
  preserved and that the live draft is untouched.

## Accessibility

- Preserve logical DOM order: app bar and safety context, status, board
  controls, board, decision controls, footer. CSS grid may place the decision
  rail visually beside the board but must not create a confusing reading order.
- Every actionable control is reachable by keyboard and has a visible
  `:focus-visible` treatment with at least 3:1 contrast against adjacent colors.
- Do not turn status cards or decorative layout containers into tab stops.
- Keep native buttons, inputs, selects, labels, `aria-pressed`, `aria-selected`,
  `aria-expanded`, and `aria-controls` semantics.
- The desktop-always-open pick tools must not retain a collapsed announcement.
- Preserve at least 44 × 44 px targets in compact mode. Desktop controls may be
  denser, but primary and destructive actions remain at least 40 px high.
- `aria-live` announcements remain concise and cover authentication errors,
  rejected writes, stale reconciliation, and successful multi-pick transitions.
- Busy containers keep their current content visible; do not replace the board
  with a spinner that causes focus loss.
- Do not encode Active/Paused/Complete, reach/value, or isolation using color
  alone.
- Respect `prefers-reduced-motion`; the implementation requires no essential
  animation.

## Visual and content rules

- Continue the committed dark Draft Room theme and green mock-isolation accent.
- Amber remains the board selection/tier/action accent; green communicates mock
  isolation and successful/active mock context. Red is reserved for destructive
  actions and errors.
- Use existing typography and numeric styles. Do not introduce a desktop-only
  design system.
- Keep the board denser than the rail. The rail should read as one decision
  surface, not a collection of equally weighted cards.
- Long player/team names, a 10-digit seed, and a three-digit overall pick must
  not overlap adjacent controls.
- All empty, paused, complete, recovery, and error copy must remain meaningful
  without the color treatment.

## Architecture and implementation boundaries

Expected implementation surfaces:

- `tracker/public/mock.html`: add the smallest semantic board/workspace/rail
  wrappers and headings needed for layout and accessible landmarks.
- `tracker/public/styles.css`: add the intermediate and desktop media queries;
  preserve compact rules as the base layer.
- `tracker/public/mock-app.ts`: add only the responsive pick-tools presentation
  seam and any wrapper visibility updates required by the new markup.
- A small pure presentation helper under `tracker/src/` is acceptable if it
  makes breakpoint/disclosure state independently testable. It must not own mock
  or board domain state.
- Existing `mock-ui.ts`, `mock-view.ts`, `board-view.ts`, `player-pool.ts`, and
  `render.ts` remain the behavior sources of truth.

The following are prohibited for this slice:

- New `/api/mocks*` endpoints or response fields.
- Reads or writes to live draft tables from mock code.
- A desktop copy of `MockState`, `BoardViewState`, `PlayerPool`, or the board
  renderer.
- Viewport width persisted in D1, local storage, or mock state.
- CSS that hides a function at one supported viewport without an equivalent
  reachable control.

## Testing strategy

### Pure and controller tests

Extend Vitest coverage for any responsive presentation helper and for the
desktop-always-open/compact-restored pick-tools behavior. Existing tests remain
the regression boundary for:

- `mockActionState` lifecycle enablement;
- `mockSuggestions` roster-safe ordering;
- `reconcileMockBoardView` selection preservation/clearing;
- board filters, search, selection, and progressive loading;
- Worker authorization, stale revisions, lifecycle actions, board recovery,
  and mock/live isolation.

### Browser viewport tests

Add a real browser layout suite for the served `/mock` shell. A DOM-free Vitest
assertion is not sufficient to prove responsive CSS. Use these fixed viewports:

| Name | Size | Core assertions |
| --- | --- | --- |
| Phone | `390 × 844` | Single column; no horizontal overflow; bottom dock and disclosure retained; 44 px targets |
| Desktop minimum | `1024 × 768` | Board and rail are side by side; both visible; no body horizontal scroll; board has its own scroll area |
| Desktop standard | `1440 × 900` | Content cap/centering; status is one row; rail stays visible while board scrolls |
| Short desktop | `1280 × 650` | Every action remains reachable through document or rail scroll; no clipped confirmation/action area |

The browser journey should:

1. Unlock with the test key.
2. Render setup and start a deterministic mock.
3. Search or filter, select a player, and verify the decision rail updates.
4. Record the pick and verify the board, latest transition, clock, and
   suggestions reconcile from the response.
5. Pause and resume.
6. Exercise undo and restart confirmations.
7. Verify mobile layout after resizing the same session and confirm the compact
   pick-tools state is restored.

Prefer structural and computed-layout assertions over broad pixel snapshots.
One focused screenshot at phone and desktop sizes may be retained as a visual
regression aid, but interaction and geometry assertions are the acceptance
gate.

### Validation commands

The implementation closeout runs, at minimum:

```sh
cd tracker
npm run typecheck
npm test
npm run build:client
npm run test:browser
```

Run `make test-backend-e2e` if implementation work changes the served route,
Worker routing, API behavior, or `board.json` boundary. A CSS-only iteration
does not by itself require a backend contract rerun.

## Acceptance criteria

1. At `1024 × 768` and `1440 × 900`, an authenticated configured mock renders a
   two-pane workspace with the board on the left and the visible decision rail
   on the right.
2. The board, current turn, selected player, Draft player action, suggestions,
   latest transition, and lifecycle controls are simultaneously reachable
   without document scrolling at supported desktop heights.
3. The board owns vertical scrolling; scrolling it does not move the decision
   rail or reset selection.
4. Setup, locked, active, pending, paused, complete, stale-conflict, unavailable
   board, and unreadable saved-board states have deliberate desktop layouts with
   no clipped or overlapping content.
5. At `390 × 844`, the current single-column layout, touch targets, bottom dock,
   pick-tools disclosure, filters, search, row selection, and lifecycle actions
   remain functional.
6. Crossing the `1024 px` breakpoint neither changes `BoardViewState` nor sends
   a network request; compact pick-tools expansion is restored when returning
   below the breakpoint.
7. Desktop actions use the existing `mockActionState` enablement and current API
   requests. No optimistic pick, duplicate write, or stale revision can be
   submitted.
8. Search, identity-aware availability, roster-safe suggestions, progressive
   loading, and Drafted history produce the same results at every viewport.
9. Mock activity remains isolated to `mock_*` storage and never changes the live
   draft.
10. No Worker API, D1 migration, `board.json` version, or opponent-strategy
    change is required.
11. Keyboard focus, native semantics, live announcements, reduced motion, and
    contrast requirements above are met.
12. Type checking, Vitest, client build, and browser viewport tests pass.

## Implementation sequence

1. Add semantic workspace/board/rail wrappers while keeping the compact DOM
   order and behavior intact.
2. Add the desktop CSS grid, bounded rail, independent scroll regions, setup
   layout, and short-height fallback.
3. Add the `matchMedia` presentation seam for always-visible desktop pick tools
   without mutating `BoardViewState`.
4. Exercise every current render branch in both layouts and correct focus/busy
   semantics.
5. Add viewport-level browser coverage, then run the existing tracker gates.

Each step should leave `/mock` usable. Domain or API changes discovered during
implementation require a separate Beads issue rather than expansion of this
responsive slice.
