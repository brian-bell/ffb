# Retro → sit/start feedback loop: design

Status: Phases 0–5 implemented (2026-10-14). Phase 5 backfills 2024–2025 priors. A third projection source was evaluated and not wired.

## TL;DR

- **Today there is no loop, and most of the data a loop needs is being thrown
  away.** `retro` grades one lineup snapshot against actuals, prints it,
  publishes it, and nothing reads it again. The per-source accuracy it computes
  (`retro.py:_source_accuracy`) is never kept.
- **Fix the data first (Phase 0).** Stop overwriting advice and projections:
  version every `lineup` snapshot, archive each raw weekly projection pull, and
  log projections for *every rostered player in the league*, not just Brian's
  15. That turns ~30 labelled player-weeks per week into ~350.
- **Then join and measure (Phase 1)** with a pure, read-time `outcomes.py`
  that builds a prediction-vs-actual fact table from snapshots. No DuckDB, so
  the "actuals never in DuckDB" invariant holds.
- **Then calibrate, in shadow (Phases 2–3):** per-position bias, per-source
  weights and residual spread, fit walk-forward on weeks `< W` with heavy
  shrinkage toward today's behaviour. The biggest practical win is not better
  point estimates; it is turning the fixed 1.5-point `CLOSE_CALL_POINTS` into a
  real win probability, and learning a haircut for Questionable players.
- **Promote only through a gate (Phase 4):** calibrated advice replaces raw
  advice only when a walk-forward backtest beats it on decision regret and
  Brier score across both leagues.

## 1. How it works today (what the loop has to plug into)

| Piece | Where | What matters for a feedback loop |
| --- | --- | --- |
| Weekly projections | `season sync` → `projections` table, PK `(player_key, season, source, scope)`; raw pulls at `snapshots/sleeper/projections_nfl_{S}_regular_week{W}` and the ESPN equivalent | Latest only. Each refresh replaces the week's snapshot and DB slice, so what the model believed on Wednesday vs Saturday is gone. |
| Consensus | `consensus.py` | Equal-weight mean of league-scored per-source points (`sleeper`, `espn`). No bias term, no weights, no variance. |
| Advice | `lineup.py:compare_lineup` | Greedy slot fill on consensus points; Out/Doubtful/IR excluded; Questionable played at full value; "close call" = a fixed 1.5-point gap. |
| Advice snapshot | `snapshots/lineup/[league/]{S}_week{W}.json`, schema v1, closed keys (`retro.py:LINEUP_SNAPSHOT_KEYS`) | Holds the user roster only, with `points`, `source_points`, `n`. The refresh job runs `lineup --force` Wed–Sat, so the file is overwritten daily and retro grades only the last (Saturday 07:00 ET) version. |
| Actuals | `WeeklyActualsBundle` v2 in `snapshots/actuals/` or Worker KV | League-scored points for **every rostered player in the league** (all teams), keyed by `native_id`. Free agents are absent. Invariant: never DuckDB, never git. |
| Retro | `retro.py:retro_report` | Advice vs started, hindsight optimum, per-source MAE/bias for the user roster. Rendered and published; not stored or aggregated. |

So the join key exists (`native_id`), the labels exist (league-scored actuals
for ~150–200 rostered players per league per week), and the scoring is already
league-correct. What is missing is a point-in-time record of predictions for
those players, and anything that reads more than one week at a time.

## 2. What to log

Three append-only records. All are JSON under `snapshots/`, written with
`put_json(..., mode=0o600)` like today, never edited after write.

### 2.1 Prediction ledger (new): one file per lineup run

`snapshots/predlog/[league/]{S}_week{W}/{generated_at}.json`

Written by `ffb lineup` every time it runs, independent of `--force`. It covers
every rostered player in the league (the CLI already loads
`league_roster_rows` for all teams and filters to the user afterwards), so the
cost is one extra file per run.

```json
{
  "schema_version": 1,
  "kind": "prediction_ledger",
  "season": 2026, "week": 6,
  "league_key": "sleeper:123…",
  "generated_at": "2026-10-10T11:02:14.123456Z",
  "scoring_weights": {"rec": 1.0, "…": 0.0},   // league ScoringConfig used
  "projection_sources": {                       // which raw pulls fed it
    "sleeper": {"key": "…week6", "sha256": "…", "modified_at": "…"},
    "espn":    {"key": "…week6", "sha256": "…", "modified_at": "…"}
  },
  "players": [
    {
      "team_key": "…", "is_user_team": false,
      "native_id": "4046", "native_player_key": "…",
      "player_key": "…", "projection_key": "…", "matched": true,
      "name": "…", "position": "RB", "nfl_team": "KC",
      "selected_position": "RB", "eligible_positions": ["RB", "W/R/T"],
      "injury_status": "QUESTIONABLE",
      "consensus": 13.5, "n": 2,
      "source_points": {"sleeper": 14.2, "espn": 12.8}
    }
  ]
}
```

The contract is closed (`predlog.py:parse_prediction_ledger`). Phase 2 adds a
`calibration` reference and per-player calibrated mean/sd under schema v2.
Runs for a week already past write no ledger, since post-hoc projections would
leak outcomes.

Why a new file instead of widening the lineup snapshot: the lineup snapshot is
a closed v1 contract that `retro` and `--publish` depend on. A sibling ledger
changes nothing that exists today.

### 2.2 Versioned advice (change): keep every lineup snapshot

Keep `lineup/{S}_week{W}.json` as the "latest" file retro reads today, and also
write `lineup/history/{S}_week{W}/{generated_at}.json`. This also makes the
architecture doc's "immutable sit/start snapshot" claim true again, since the
refresh job's daily `--force` currently defeats it.

### 2.3 Raw projection archive (change): never lose a weekly pull

When `season sync` is about to replace a weekly projection snapshot whose
sha256 differs, copy the old one to
`snapshots/archive/{source}/{key}/{modified_at}.json` first. This is the
expensive-to-recreate input: with it, any past ledger can be rebuilt under a
different scoring config or calibration, and free agents (not in the ledger)
can be added later. Size is a few MB per week.

### 2.4 Labels: no new capture

Actuals already land in `snapshots/actuals/` (Yahoo via the tracker pull,
Sleeper via `--from-matchups`). `retro` locks them before it looks for a
lineup snapshot, so the Wednesday `--week-roll` keeps a week's labels even
when there is no advice to grade.

## 3. Joining outcomes to predictions

A new pure module, `src/ffb/outcomes.py` (I/O-free, enforced by
`test_layering.py`), plus a thin CLI loader that walks the snapshots.

**Grain.** One row per `(league_key, season, week, native_id, generated_at)`.
Most analysis uses the **decision row**: the latest ledger before that player's
kickoff. Before Phase 0 lands, the Saturday lineup snapshot is the decision
row by definition.

**Join.**

```text
ledger.players[native_id]  ⟕  actuals.players[native_id]   (same league, season, week)
```

- `native_id` only, never names (same rule `retro.py` follows).
- Inner join for metrics; anti-join rows are reported, like
  `missing_actuals` today.
- A player whose actuals `team_key` differs from the ledger's was traded or
  dropped mid-week. Keep the row, since the projection is still a valid
  forecast, and tag it `moved=true`.

**Fact columns.** Identity (`league_key, season, week, native_id,
projection_key, position, nfl_team, team_key, is_user_team`); predictions
(`source_points.*, consensus, n, calibrated_mean, calibrated_sd`); context
(`injury_status, selected_position_at_advice, recommended_slot,
generated_at, hours_before_kickoff`); outcome (`actual_points,
actual_selected_position, actuals_synced_at`); derived
(`residual = actual − consensus` and per source, `dnp_proxy = actual == 0 and
position != "DEF"`).

**Decision table** (derived from the fact table, user team only). One row per
pairwise sit/start choice the advice made: for each slot, the recommended
starter vs each eligible bench player, with `projected_gap`, `realized_gap`
and `correct = sign(projected_gap) == sign(realized_gap)`. This is the label
that matters for sit/start, because point accuracy only matters insofar as it
changes these choices.

**Where it lives.** Computed at read time from snapshots, consistent with
"points, consensus, VORP and tiers are computed at read time" and with actuals
never entering DuckDB. At ~5k rows a season it is milliseconds of work. If SQL
over it becomes useful, write a rebuildable Parquet file under
`snapshots/derived/` and have `store.py` query it in memory. That is a
deliberate, documented change to the actuals invariant, not a default.

**Known biases to label, not hide.**

- *Selection:* only rostered players have actuals, and the best and worst
  outcomes get dropped or added. Fine for sit/start, which is a rostered-player
  decision anyway; don't reuse it to grade waiver advice without nflverse
  actuals.
- *Inactives:* zero-point rows from late scratches are real outcomes for a
  sit/start forecast. Keep them in the target, but report metrics with and
  without `dnp_proxy` so a few scratches don't look like a projection-quality
  problem.
- *Timing:* Saturday's advice cannot see Sunday inactives. `hours_before_kickoff`
  lets you measure that cost once the ledger has several runs per week.

## 4. Updating the model

Keep it small and statistical. With two sources, ~14 weeks and ~350 labelled
rows a week, anything with more than a handful of parameters per position will
overfit. Everything below is fit **walk-forward** (weeks `< W` only), per
position, and **shrunk toward today's behaviour** so an empty or thin history
reproduces current advice exactly.

### 4.1 Calibrated mean

```text
calibrated = b_pos + Σ_s w_{s,pos} · p_s        (sources present for the player)
```

- **Weights:** inverse-MSE per source and position, shrunk toward equal:
  `w_s ∝ (n·(1/MSE_s) + k_w·(1/MSE_pooled)) / (n + k_w)`, renormalised over the
  sources the player has, clamped to `[0.25, 0.75]` with two sources. Start with
  `k_w ≈ 200` rows.
- **Bias:** shrunk mean residual of the weighted projection,
  `b_pos = Σ r / (n + k_b)` with `k_b ≈ 100`, clamped to ±2 points.
- **Questionable haircut:** a multiplier `h_Q = 1 − shrunk mean of
  (1 − actual/projected)` over Q-tagged rows, applied only to Q players. Today
  Q players are played at full value, which is probably the single most
  systematic error in the advice. Doubtful/Out stay excluded, as now.

Why not a learned model (GBM, regression on features)? Per-position bias
shifts every player in a position by the same amount, so it only changes a
choice in a FLEX slot. Source weights only move the order when the two sources
disagree. Both are cheap, explainable and hard to overfit. A feature model
(opponent, Vegas total, snap trends) only becomes worth it once an nflverse
stats ingest exists; `ros.py` already notes that dependency.

### 4.2 Uncertainty: where the payoff is

Fit residual spread per position, scaled with the projection, since big
projections miss by more:

```text
sd(p) = a_pos + c_pos · sqrt(max(p, 0))        fit on |residual| by position
```

Then for each sit/start choice between A and B:

```text
P(A outscores B) ≈ Φ( (μ_A − μ_B) / sqrt(sd_A² + sd_B²) )
```

This replaces `CLOSE_CALL_POINTS = 1.5`. A 1.5-point gap between two kickers is
a coin flip; between two WR1s it is also a coin flip, but the current rule
treats both identically and ignores a 3-point gap between two boom-or-bust
flex options. The advice still starts the higher mean. What changes is that
"close call" becomes "under 60% to be right", and the `/command` card can show
the probability.

### 4.3 Versioning the parameters

Each week's fit is written once to
`snapshots/calibration/[league/]{S}_week{W}.json` with
`fit_weeks`, `n_by_position`, priors, parameters and a sha256. The ledger
records which params file it used (`calibration.params_key`/`sha256`). Retro
can then always say which model made which call, and a re-fit never rewrites
history. Fit on both leagues pooled, since projections and NFL outcomes don't
depend on the league once points are league-scored. Per-league params are
an option to test, not the default.

## 5. Validation

**Walk-forward backtest:** a new `ffb calibrate backtest SEASON [--league]`,
the Python twin of the tracker's `npm run backtest`. For each week `W ≥ 3`, fit
on weeks `< W`, score week `W`, and compare **raw** vs **calibrated**:

| Metric | On | Why |
| --- | --- | --- |
| Decision regret: `hindsight_total − recommended_total` (actual points) | user rosters, both leagues | The thing Brian feels. Already computed by `retro`. |
| Pairwise decision accuracy, weighted by realized gap | decision table | Did the calls flip the right way? |
| Brier score and reliability bins for `P(A > B)` | decision table, then all same-slot pairs league-wide | Is "62%" really 62%? This validates §4.2 with ~10× more pairs than Brian's roster gives. |
| MAE / RMSE / bias by source × position | full fact table | Diagnostics, and an early warning for source drift (an ESPN endpoint change shows up as a bias jump). |
| Spearman ρ within team-position | full fact table | Ranking quality, which is what slot filling uses. |

**Promotion gate** (Phase 4): calibrated becomes the advice only if, over at
least 6 backtest weeks pooled across both leagues, mean regret is not worse,
Brier improves, and no position's MAE gets worse by more than 0.3 points. Report
bootstrap intervals over weeks (not rows), since rows within a week are
correlated. Expect a modest effect: weekly projection error dwarfs anything
two-source reweighting can fix. The honest goal is better-calibrated
confidence and a few fewer bad Questionable starts, not a big jump in
points.

**Guardrails in production:** fall back to identity params when `n` for a
position is below the prior strength; clamp every parameter; when the fit fails
or the params file is missing, fall back to raw consensus with a printed
warning, the same way missing sources are handled today.

**Tests (TDD, per repo conventions):** pure `outcomes.py` and `calibration.py`
with synthetic fixtures: join correctness (moved players, missing actuals,
DEF ids), walk-forward never sees week `W`, empty history reproduces raw
advice exactly, shrinkage limits, and the snapshot v1→v2 upgrade shim.
`test_layering.py` gains both modules as I/O-free.

## 6. Phased plan

Each phase is a separate small PR and Beads issue. Nothing user-visible changes
until Phase 4.

**Phase 0: stop losing data.** Implemented.
- `lineup` writes the prediction ledger (§2.1) for all league rosters on every
  run for the current or a future week, plus the versioned advice copy (§2.2).
- Weekly Sleeper and ESPN projection refreshes archive the pull they replace
  when its content changed (§2.3); season-scope pulls are not archived.
- No change to the refresh job was needed: `retro` locks a week's actuals
  before it looks for advice, so `--week-roll` already keeps actuals for both
  leagues even when retro then exits for a missing snapshot.
- No contract changes: the lineup snapshot, `board.json` and the publish
  envelopes are untouched.

**Phase 1: join and measure.** Implemented.
- `outcomes.py` fact and decision tables (§3); a loader in the CLI.
- `ffb retro history SEASON [--league]`: cross-week source × position
  accuracy, regret trend and Q-player hit rate. Optional later: a `/command`
  card (needs an `inseason.py` kind and tracker work).
- Retro's existing `source_accuracy` stays unchanged.

**Phase 2: calibrate in shadow.** Implemented.
- `calibration.py` fit (§4.1–4.3) and the params file per week.
- `lineup` computes calibrated mean/sd into the ledger only. Advice, snapshot
  v1 and the published report are unchanged.
- `retro` gains a "shadow" section: what calibrated advice would have done.

**Phase 3: backtest and probabilities.** Implemented.
- `ffb calibrate backtest` (§5) with the promotion gate printed as pass/fail.
  The command reports the gate and does not change advice, snapshots, or
  published reports.
- Win-probability close calls computed in shadow alongside the 1.5-point rule.

**Phase 4: promote (gated).** Implemented.
- `lineup --model raw|calibrated`, defaulting to `calibrated` once the gate
  passes.
- Lineup snapshot schema v2 adds `model` and per-row `sd`/`p_start`, with a
  v1 upgrade shim in `parse_lineup_snapshot`, mirroring `actuals.py`.
- Report and envelope additions for the `/command` card, coordinated with
  `tracker/` (additive fields only).
- Re-run the gate weekly; it auto-reverts to raw if it stops passing.

**Phase 5: more history.** Implemented for the 2024–2025 backfill and Sleeper
free-agent grading. A third projection source was evaluated and not wired.

- `ffb calibrate priors` snapshots 2024 and 2025, weeks 1–18, of Sleeper and
  ESPN weekly projections, the nflverse schedule, nflverse weekly player
  stats, and the crosswalk. It writes snapshots only. It does not write
  DuckDB, call Yahoo, or publish. The daily refresh job does not run it.
  Repeat without `--refresh` fetches only missing keys. `--offline` replays
  the cache. A failed key is noted and does not abort the other seasons; the
  command exits 1 when any sync note remains.
- Verified 2026-10-14: `GET https://api.sleeper.com/projections/nfl/{season}/{week}`
  returns per-player weekly Rotowire forecasts for 2024 and 2025. The lines
  are fractional counting stats (`gp` near 1), not season totals and not box
  scores. 2024 week 1 Mahomes projects 274.28 pass yards and 2.06 pass TDs;
  the nflverse actual that week is 291 yards and 1 TD. `updated_at` is a
  later storage rewrite: 2024 week 1 Mahomes is `2024-09-10T03:45:16Z`, after
  that Thursday kickoff and the Sunday slate, and 2025 week 1 is
  `2025-10-06T15:45:02Z`. The live payload's only `company` is `rotowire`.
  Those stamps are copied to `projection_updated_at` and are never
  `generated_at`.
- Kickoff safety. Every prior fact uses `generated_at` `"prior"`
  (`outcomes.PRIOR_GENERATED_AT`). `select_scoring_facts` keeps it only when
  that team-week has a kickoff in the same season's schedule, and leaves
  `hours_before_kickoff` null. An empty kickoff map drops every prior row.
  `fit_calibration` trains on prior rows from seasons before the target (all
  of those weeks) and on earlier weeks of the target season. `fit_weeks`
  lists only target-season weeks, so a 2024 week 18 cannot look like it was
  inside a 2026 week-3 fit. A non-prior row from another season stays out.
  For the same `(league, season, week, native_id)`, a real ledger stamp
  replaces `"prior"` and `"prior"` never replaces a real stamp.
- The lineup fit and `ffb calibrate backtest` load prior seasons only, not
  the target season's prior rows. Ledger native ids and Sleeper ids would
  otherwise count the same player twice. Prior rows train the fit. They are
  not sit/start decisions, regret, Brier, or gate MAE. Each season uses its
  own kickoff map; a missing map is empty and drops that season.
- Points are scored at read time with each league's present `ScoringConfig`,
  not a reconstructed 2024 ruleset. Yahoo uses the synced league for 2026,
  then 2025, then 2024, and otherwise `config.LEAGUE_SCORING`. Sleeper is
  omitted until `ffb league sync SEASON --league sleeper` has stored its own
  rules. Sleeper never borrows Yahoo weights. League keys stay
  `yahoo:470.l.928421` and `sleeper:1395854363380965376`.
- ESPN weekly projections are the existing second source, so two-source
  weights can leave identity. They are not a new third source. One snapshotted
  source still leaves weights at identity.
- nflverse actuals include `pass_int` and `fum_lost` (sack, rush, and
  receiving fumbles lost). Two-point conversions map to `pass_2pt`,
  `rush_2pt`, and `rec_2pt`. `def_tds` excludes fumble-recovery touchdowns
  and maps to `pass_int_td`. A skill player's `fumble_recovery_tds` maps to
  `fum_rec_td`; any other position's maps to `def_fum_td` on the team row.
  `special_teams_tds` maps to `def_ret_td` on the team row. A game with no
  score does not become a shutout. Skill projections whose stat keys are
  absent from the scoring config are omitted, so unscored kickers do not
  inflate `n` with zeros. `--refresh` keeps a snapshot when its parser
  returns no usable rows.
- Free agents: `ffb calibrate waivers SEASON --week N` grades prior facts
  with no fantasy `team_key`, after the same kickoff selection. The default
  league is `sleeper:1395854363380965376`. It does not publish. Yahoo free
  agents are not graded. Rostered ledger rows are not graded.

Third projection source, evaluated 2026-10-14 and not wired:

- FantasyPros `https://api.fantasypros.com/public/v2/json/nfl/2024/consensus-rankings`
  and `.../nfl/2024/projections` returned HTTP 403 without an API key.
- Sleeper's historical weekly payload does not include a second `company`.
  The docs mention `sportradar`; it was absent from the pulls checked.
- nflverse has player stats and schedules, not projections.
- Yahoo projections need OAuth. This phase does not call or write Yahoo.
- NumberFire, CBS, PFF, and 4for4 do not offer a stable free JSON stat line
  that matches `{native_id, position, team, stats}`. HTML scraping is out of
  scope.

Source weighting stays on Sleeper plus ESPN.

## 7. Decisions

Brian approved the following on 2026-10-11:

1. Actuals stay out of DuckDB. The join is read-time over snapshots, with an
   optional Parquet file under `snapshots/derived/` later.
2. Fits pool both leagues; per-league fits are only a backtest variant.
3. Phase 0 shipped first.
