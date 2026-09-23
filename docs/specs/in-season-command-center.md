# In-season command center

Status: implemented (MVP, all five delivery slices)

Route: `/command`

Tracking: `ffb-9hp`

Last updated: 2026-09-13

Prototype: `docs/prototypes/in-season-command-center.html`

## Outcome

One read-only desktop dashboard in the existing tracker Worker shows the four
in-season reports added in PRs 42–48 side by side, each with honest freshness:

| Card | Source command | PR | Time horizon |
| --- | --- | --- | --- |
| Lineup | `ffb lineup` | #43, #44 (weekly projections from #42) | this week, changes until kickoff |
| News | `ffb digest` | #47 | this week, changes daily |
| Retro | `ffb retro` | #48 | last week, fixed once actuals land |
| Rest of season | `ffb ros` | #45 | season, changes slowly |

The Python CLI stays the only place these reports are computed. Each command
gains `--publish`, which POSTs the exact report it just rendered to the Worker.
An external cron runs the commands; the Worker stores the reports in KV and
serves them to a read-only page.

This is a presentation and transport change. It adds no scoring, consensus,
ranking, or LLM computation and no DuckDB or D1 schema.

## Decisions

These were settled during design review on 2026-09-13.

1. **Tracker page fed by exported JSON.** The command center is a Worker route,
   not a local HTML file and not a TypeScript reimplementation. It copies the
   `board.json` pattern: Python produces a closed, versioned document and the
   tracker never imports Python.
2. **Desktop dashboard grid first.** The MVP is a desktop grid of four summary
   cards. A single-column "this week" scroll page is the future phone view and
   is out of MVP scope, but card order and panel content are designed to stack
   into it.
3. **Summary cards with a detail panel.** Each card shows a headline figure and
   its three to five most important rows. Clicking a card opens a wide
   right-side panel with the full report while the grid stays visible. Lineup
   is the dominant card because it is the only one that needs action before
   kickoff.
4. **One KV document per report and week.** Reports publish independently, so
   a Friday digest refresh does not rebuild the lineup. The trade-off is that
   one screen mixes generation times, which decision 5 makes visible.
5. **Per-card freshness with report-specific rules.** Every card header shows
   its age and a state badge. The header strip shows the week and the oldest
   source on screen.
6. **`--publish` on each existing command.** No new CLI verb. A published
   report is byte-for-byte the report the command rendered. Scheduling lives in
   an external cron; the repository only documents the runbook.
7. **Strict scope.** The page shows only fields the four report dicts already
   contain. No opponent projection, standings, waiver ranking, or usage trends.
   Gaps appear only as the reports' own notes (for example
   `usage_available: false`).

## Non-goals

- Any write action from the page: lineup changes, waiver claims, re-running
  sync, triggering the LLM.
- The phone layout (decision 2).
- New Python computation or new report fields beyond the publish envelope.
- Scheduling, email delivery, or GitHub Actions (tracked by `ffb-8yi`).
- Yahoo live authorization (tracked by `ffb-1ct.2`).

## Publish envelope

Every `--publish` POST sends one closed envelope. The `report` value is the
existing report dict, unchanged.

```json
{
  "schema_version": 1,
  "kind": "lineup",
  "season": 2026,
  "week": 2,
  "generated_at": "2026-09-20T13:05:12Z",
  "team_name": "Bell Curve",
  "context": {},
  "report": {}
}
```

| Field | Rule |
| --- | --- |
| `schema_version` | Exactly `1`. Breaking shape changes bump it. |
| `kind` | One of `lineup`, `digest`, `retro`, `ros`. Must match the route. |
| `season` | Must equal the published board season, same rule as the league bundle ingest. |
| `week` | Positive integer. `lineup` and `digest`: the report week. `retro`: the scored week. `ros`: the stored `current_week` at generation. |
| `generated_at` | UTC ISO-8601, set by the CLI when the report is built. |
| `team_name` | User team name, or `null` when league state has none. |
| `context` | Kind-specific provenance, closed per kind (below). |
| `report` | The dict the command passed to its renderer. |

Kind-specific `context`:

| Kind | Context keys |
| --- | --- |
| `lineup` | `league_synced_at` (stored league state sync time), `projection_sources` (active weekly sources), `snapshot_generated_at` (the locked sit/start snapshot that retro will grade, or `null` when none was written) |
| `digest` | `sources` (news sources ready at generation) |
| `retro` | `actuals_synced_at` (the actuals bundle `synced_at`) |
| `ros` | `projection_sources`, `playoff_weeks_requested` |

`digest` publishes after `_apply_digest_llm`, so `report.llm.error` travels
with the report. The Worker never holds an Anthropic key.

`ros` publishes the full report, not the `--limit` slice the terminal prints.
The `-p/--position` filter is rejected with `--publish` so the stored report is
always the all-position report.

Secrets never enter the envelope. The CLI reads `FFB_TRACKER_URL` and
`FFB_TRACKER_API_KEY` through the existing `TrackerConfig` and must not log
either value.

## Worker API

All routes use the existing bearer-key gate.

| Method and route | Purpose |
| --- | --- |
| `POST /api/inseason/{kind}` | Validate and store one envelope |
| `GET /api/inseason?season=&week=` | Read the dashboard view for one week |

### Ingest

`POST /api/inseason/{kind}` validates the envelope and a minimal closed shape
for `report` (required top-level keys and types for that kind; player rows are
checked for the fields the page renders). Retro hindsight keys
(`hindsight_total`, `hindsight_started_total`, `hindsight_delta`,
`hindsight_start`, `hindsight_sit`) are an all-or-none optional extension:
absent is a pre-hindsight schema-v1 envelope; if any key is present, all five
are required and typed. New CLI publishes include them. It stores the body under:

```text
inseason:v1:{season}:{kind}:{week}
```

Responses reuse the league bundle ingest error vocabulary:

| Status | Error | When |
| --- | --- | --- |
| 200 | — | Stored. Returns `{kind, season, week, generated_at}`. |
| 400 | `invalid_report` | Envelope or report shape fails validation, or `kind` mismatches the route. |
| 409 | `stale_report` | Stored document for the same key has a newer `generated_at`. |
| 409 | `season_mismatch` | Season differs from the published board. |
| 413 | `payload_too_large` | Body over the existing 10 MB cap. |

### Dashboard read

`GET /api/inseason?season=2026&week=2` composes one response so the page makes
one request:

```json
{
  "season": 2026,
  "week": 2,
  "server_now": "2026-09-20T15:40:00Z",
  "league": { "synced_at": "2026-09-20T12:22:00Z", "current_week": 2 },
  "actuals_available": { "1": true },
  "weeks": [1, 2],
  "cards": {
    "lineup": { "envelope": {} },
    "digest": { "envelope": {} },
    "retro": { "envelope": {} },
    "ros": { "envelope": {} }
  }
}
```

Week resolution for requested week `W`:

| Card | Reads |
| --- | --- |
| `lineup` | `inseason:v1:{season}:lineup:{W}` |
| `digest` | `inseason:v1:{season}:digest:{W}` |
| `retro` | `inseason:v1:{season}:retro:{W-1}`; absent for `W = 1` |
| `ros` | the newest `ros` document with week `≤ W` |

A card with no document returns `"envelope": null`. `league` comes from
`league:bundle:current` and is `null` when absent. `actuals_available` reports
whether that league's `actuals:v2:{season}:{league}:{W-1}` exists, falling back
to `actuals:v1:{season}:{W-1}` only for a Yahoo league. `weeks` lists weeks with at least
one `lineup` or `digest` document (KV prefix list). When `week` is omitted the
Worker uses `league.current_week`, then the newest week in `weeks`.

## Freshness

Freshness is a pure TypeScript function of the dashboard read response and the
client clock, `cardFreshness(kind, view, now)`. It returns one state and a
human reason. The page never infers freshness any other way.

| State | Badge | Meaning |
| --- | --- | --- |
| `fresh` | green dot + age | Safe to act on. |
| `stale` | amber stripe + reason | Newer inputs exist or the document aged out. |
| `degraded` | amber chip | Published, but part of the report is missing. |
| `waiting` | blue dashed card | Upstream data does not exist yet. |
| `missing` | neutral dashed card | Nothing published; shows the command to run. |

Rules, first match wins:

| Card | Rule | State and reason |
| --- | --- | --- |
| Lineup | no envelope | `missing` — "Not published for week W" |
| Lineup | `league.synced_at` > `context.league_synced_at` | `stale` — "Roster changed after this lineup was built" |
| Lineup | both `injury_as_of` set and digest's is newer than lineup's | `stale` — "Newer injury report available" (lineup's is `null` for any non-current week; `null` never counts as stale) |
| Lineup | age > 5 days | `stale` — "Built more than 5 days ago" |
| Lineup | `report.missing_projections` non-empty or `undecidable` non-empty | `degraded` — "N players have no projection" |
| News | no envelope | `missing` |
| News | age > 5 days | `stale` — "Headlines are more than 5 days old" |
| News | `report.llm.error` set | `degraded` — "LLM skipped — headlines only" |
| Retro | `W = 1` | `waiting` — "No prior week to grade" |
| Retro | no envelope and `actuals_available[W-1]` false | `waiting` — "Waiting for week W-1 actuals" |
| Retro | no envelope and actuals present | `missing` — "Actuals are in; retro not published" |
| Retro | `report.missing_actuals` non-empty | `degraded` — "N players have no actuals" |
| Rest of season | no envelope | `missing` |
| Rest of season | age > 8 days | `stale` — "Built more than 8 days ago" |
| Any | otherwise | `fresh` |

Age thresholds follow the twice-weekly refresh cadence in the runbook
(Wednesday and Sunday): 5 days covers the Wednesday-to-Sunday gap with slack,
and 8 days covers one missed Wednesday `ros` run. Retro never goes stale by
age because actuals are fixed once graded.

The header strip shows `Week W`, the team name, and "Oldest source: {card},
{age}". The lineup card additionally shows "Retro grades the {time} snapshot"
from `context.snapshot_generated_at`, because `ffb lineup` does not replace a
locked snapshot without `--force` even when it publishes a newer report.

## Page

Route `GET /command` serves static HTML and a client bundle, same as `/` and
`/mock`. The page reuses the stored API key flow and the tracker's dark tokens.

### Layout (≥ 1100 px)

```text
┌──────────────────────────────────────────────────────────────────────┐
│ FFB Command  League [Money League ▾]  Week ‹ 2 ›  Bell Curve  ROS, 6d│
├───────────────────────────────────────────────┬──────────────────────┤
│ LINEUP (2 columns)                            │ NEWS                 │
│ Δ +5.2 to optimal · moves · close calls       │ flagged roster       │
├───────────────────────┬───────────────────────┤ brief excerpt        │
│ RETRO (week − 1)      │ REST OF SEASON        │ watch list           │
│ recommended vs started│ playoff exposure, byes│                      │
└───────────────────────┴───────────────────────┴──────────────────────┘
```

Between 760 and 1100 px the grid collapses to two columns (lineup full width);
below that the MVP stacks cards in the future phone order (lineup, news,
retro, rest of season) without phone-specific design work.

### League picker

The header carries a league `<select>` fed by `GET /api/leagues`. Every report,
week list and freshness input is per-league, so the league is a page-level
dimension like the week rather than a card-level filter — it belongs beside the
week picker and above the grid.

It is hidden whenever there is nothing to choose: one league, or a directory
that did not load. That failure is deliberately silent — the selected league's
dashboard has already rendered, and a navigation control that is merely absent
is better than an error banner over working content. Below 760 px the label is
dropped and the select narrows, but the picker itself stays: it is the only way
to reach another league.

Choosing a league is a reload, not a filter. Season, week and `weeks` all
belong to the league being left, so the view is dropped and the request names
no week; the API answers with the new league's own current week. `?league=`
is written to the URL (and the old `week` removed) so the selection is
shareable and survives a refresh. A `?league=` the directory does not list
still appears, selected, marked as not published — a bookmark must not be
quietly redirected to a different league's roster.

### Card summaries

| Card | Headline | Rows | Panel |
| --- | --- | --- | --- |
| Lineup | `delta` to optimal, current vs optimal totals | `start`, `sit`, `undecidable`, top `close_calls` | aligned slot table, all close calls, missing projections, injury timestamp, projection sources |
| News | count of flagged roster players | roster players with `flag` or injury badge, first sentence of `narrative` | full narrative, roster and watch lists with notes and headlines, other headlines, `news_as_of` |
| Retro | `delta` recommended vs started, matchup score; muted second figure when `hindsight_delta` > `delta` | advice start/sit misses, then hindsight start/sit not already in the advice lists, then advice hits | all four hit/miss lists, hindsight start/sit, source accuracy table, missing actuals |
| Rest of season | playoff weeks and roster exposure | next `bye_plan` group with `thin_positions`, rostered players' playoff difficulty | ROS table with position filter, playoff schedule strength, full bye plan, usage note |

Retro hit and miss labels follow the report: a "hit" means the started lineup
followed the advice; a "miss" means it did not. The panel says so in one line.
Hindsight is a second metric: the greedy actuals-optimal lineup from the
snapshotted roster (`hindsight_total`), ranked on unrounded actual points.
`hindsight_delta` compares it to `hindsight_started_total`, the started
players who were on the snapshot roster, so waiver adds are ignored on both
sides. The headline stays the advice Δ (`signed(-delta)`). Pool membership
follows the actuals: players on IR/IL at game time and players dropped to
another team stay out. `missing_actuals` covers every pool player without an
actuals row, not only advised or started players.

`narrative`, notes, and headlines are LLM or third-party text. The client
renders them with `textContent`, never HTML. Headline links open in a new tab
with `rel="noopener noreferrer"` only when the URL is `https:`.

## CLI changes

| Command | Change |
| --- | --- |
| `ffb lineup SEASON [--week N] [--force] --publish` | POST `lineup` after rendering |
| `ffb digest SEASON [--week N] --publish` | POST `digest` after the LLM step |
| `ffb retro SEASON --week N [--fixture PATH] --publish` | POST `retro` after rendering |
| `ffb ros SEASON [--playoff-weeks …] --publish` | POST full `ros`; rejects `-p` (a behavior change: `-p` and `--publish` together exit 2) |
| `ffb league sync SEASON --from-tracker` | Pull `GET /api/league/bundle`, validate it as a `LeagueBundle`, and import it exactly as `--fixture` does. Mutually exclusive with `--fixture` and `--refresh`. |

`--from-tracker` mirrors how `ffb retro` already pulls actuals from the Worker.
It makes the Worker the single source of league state for the cron, so the
Worker's `league.synced_at` and the local `context.league_synced_at` come from
the same bundle and the "roster changed" rule fires only when a bundle was
posted without a CLI run following it.

A publish failure prints the Worker error, exits 1, and leaves local snapshots
untouched. Envelope building is a pure function in a new `src/ffb/inseason.py`;
HTTP lives beside `fetch_actuals` in `sources/tracker.py`.

## Refresh runbook

Two scheduled runs per week, both driven by the Grok bot. Grok scrapes Yahoo
and posts to the two existing Worker routes, then runs the CLI from the
repository root with `FFB_TRACKER_URL` and `FFB_TRACKER_API_KEY` set. `S` is
the season and `W` the current week. Grok never runs `season sync` against
Yahoo; the free sources (Sleeper, ESPN, FFC, nflverse) are fetched by the CLI.

This replaces the current Wednesday and Sunday league bundle refresh, which
stays as steps 1 and 2 of each run.

### Wednesday 10:00 ET — week roll

Mid-morning gives Yahoo slack to roll the week and apply Monday's stat
corrections.

1. Grok posts the week `W-1` `WeeklyActualsBundle` to `POST /api/actuals`.
2. Grok posts the week `W` `LeagueBundle` to `POST /api/league/bundle`.
3. Grok runs, in order:

```sh
ffb retro S --week W-1 --publish
ffb season sync S --week W --refresh
ffb league sync S --from-tracker
ffb ros S --publish
ffb lineup S --week W --publish
ffb digest S --publish
```

Retro runs first: it pulls actuals from the Worker and grades the locked
`W-1` snapshot, and needs nothing from the new league bundle. If the actuals
scrape lags, retry retro alone later. Lineup runs after league sync and writes
the week `W` snapshot.

Updates: Retro, Rest of season, Lineup, News.

### Sunday 07:00 ET — pre-kickoff refresh

Early enough to act before the 9:30 AM ET international games; Friday and
Saturday injury designations are already in. Sunday inactives (90 minutes
before each kickoff) are out of reach at this cadence and are an accepted gap.

1. Grok posts the week `W` `LeagueBundle` to `POST /api/league/bundle`.
2. Grok runs, in order:

```sh
ffb season sync S --week W --source projections --source injuries --source news --refresh
ffb league sync S --from-tracker
ffb lineup S --week W --force --publish
ffb digest S --publish
```

`--force` replaces the Wednesday snapshot, so retro grades the Sunday advice,
the last advice that was actionable before kickoff. Thursday night players get
Wednesday's advice; that is the accepted cost of two runs per week.

Updates: Lineup, News.

### Not scheduled

- No Thursday or Monday slate runs. Adding them would replace the snapshot
  again after most of the week's games are played.
- No daily news run. Digest freshness is 5 days, matched to this cadence.
- Yahoo live authorization (`ffb-1ct.2`) does not change this runbook; when it
  lands, `ffb league sync S --refresh` becomes an alternative to Grok's bundle
  post plus `--from-tracker`, not an extra step.

Card-by-card recovery when a badge is not green:

| Badge | Fix |
| --- | --- |
| Lineup: roster changed | `ffb league sync S --from-tracker` → `ffb lineup S --week W --force --publish` |
| Lineup: newer injury report | `ffb season sync S --source injuries --refresh` → `ffb lineup S --week W --force --publish` |
| Lineup: N players have no projection | `ffb season sync S --week W --source projections --refresh` → republish lineup |
| News: LLM skipped | set `ANTHROPIC_API_KEY` or `FFB_ANTHROPIC_API_KEY` → `ffb digest S --publish` |
| News: stale | news/injury sync → `ffb digest S --publish` |
| Retro: waiting for actuals | confirm Grok posted `/api/actuals` for week W-1, then `ffb retro S --week W-1 --publish` |
| Retro: not published | `ffb retro S --week W-1 --publish` |
| Retro: no sit/start snapshot (CLI error) | not recoverable for that week; `ffb lineup S --week W-1 --force --publish` writes post-hoc advice, which the retro then grades |
| Rest of season: stale | `ffb season sync S --refresh` → `ffb ros S --publish` |

## Testing

TDD per slice, smallest tests first.

- Python: envelope builders (pure, closed keys, `ros` full report, `-p`
  rejection), `--publish` CLI paths with a mocked `httpx` transport, and secret
  redaction in errors.
- Tracker unit: envelope and per-kind validation, `stale_report`,
  `season_mismatch`, size cap, KV key and week resolution, and a table test for
  every `cardFreshness` rule row.
- Tracker browser: `/command` at desktop-standard and desktop-minimum viewports
  with fixture KV covering fresh, stale, degraded, waiting, and missing; panel
  open and close by keyboard.
- `make test-backend-e2e`: extend the offline journey to publish all four
  reports from fixtures and read `GET /api/inseason`.

## Delivery slices

Each slice is a vertical tracer bullet that ships a working card.

1. Lineup end to end: envelope, `lineup --publish`, POST and GET routes,
   `/command` shell with header strip, lineup card and panel, freshness rules
   for lineup.
2. Retro card, including `waiting` via `actuals_available`.
3. News card, including `degraded` for LLM skipped and safe text rendering.
4. Rest-of-season card and the `ros` week fallback.
5. Week picker, oldest-source header, and the runbook in `docs/operations.md`.

## Beads fit

| Issue | Fit |
| --- | --- |
| `ffb-yil` 13. LLM news digest | Appears delivered by PR #47 but still open. Close or confirm before slicing. |
| `ffb-0gi` 14. Snapshot + weekly retro | Appears delivered by PR #48 but still open. Close or confirm; "Tuesday brief opens with a retro" is satisfied by the retro card. |
| `ffb-8yi` 12. Scheduled runs + email | Owns the cron. The runbook above is its command list; email stays out of scope here. |
| `ffb-1ct.2` Yahoo OAuth | Blocks unattended league sync in the runbook. |
| `ffb-qmw` 11. Waiver report | Natural fifth card after MVP; not in scope (decision 7). |
| `ffb-oeb` Desktop app experience | Same responsive principles; the command center is not a draft workflow, so track it separately rather than under this epic. |

## Open questions

Settled on 2026-09-13 (kept for the record):

- **League state source for cron.** The CLI gains `league sync --from-tracker`
  (see [CLI changes](#cli-changes)). Grok keeps posting bundles to the Worker
  and the CLI pulls them; the cron does not wait for Yahoo live auth.
- **Which lineup does retro grade?** The Sunday run uses `--force`, so retro
  grades the last advice actionable before kickoff. Wednesday's snapshot is a
  draft that Sunday replaces.
- **Cadence.** Two runs per week, Wednesday 10:00 ET and Sunday 07:00 ET. No
  daily news run and no short-slate runs.

Still open:

1. **Retention.** KV keeps every week forever by default. Acceptable for one
   season (~18 weeks × 4 documents), or add a TTL?
2. **Past weeks.** The age-based stale rules assume the current week. When the
   requested week is before `league.current_week`, should cards show an
   `archived` state instead of `stale`?
3. **Partial-run recovery.** The Wednesday run is six commands. If `season
   sync` fails after retro published, lineup and digest are skipped and the
   cards show `missing` until Sunday. Should the cron retry the tail on its
   own, or is a `missing` badge until Sunday acceptable?
4. **Equal timestamps on republish.** `stale_report` rejects a document whose
   `generated_at` is older than the stored one. Confirm that an equal
   `generated_at` is accepted (idempotent re-POST after a network error) rather
   than rejected.
5. **Week `W` for `ros`.** The envelope `week` for `ros` is the stored
   `current_week` at generation. On Wednesday that value comes from the bundle
   Grok just posted; if Grok's scrape runs before Yahoo rolls the week, the
   `ros` document lands under `W-1` and the fallback ("newest `ros` with week
   `≤ W`") still serves it. Confirm the fallback is enough, or move the
   Wednesday run later.
