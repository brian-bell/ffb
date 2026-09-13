# Starter priority

The live tracker's Available list ranks players for Brian's next pick by
**opportunity cost**: how many projected lineup points a player adds now, minus
what the same position is still expected to offer at Brian's following pick.
A player worth taking is one whose value will not be there later.

The model lives in `tracker/src/starter-priority.ts`
(`liveStarterPriority` and `lineupPriorityOrder`). It is pure compute over the
published `board.json` and the current `DraftState`; it stores nothing and
recomputes on every render, so picks, undo, and replay rewinds update it
immediately.

## When it applies

| Situation | Available order |
| --- | --- |
| No configured draft, no user team, or no board | Published board order |
| Draft configured, user has no pick yet | Published board order |
| User owns at least one pick | Opportunity-cost score (below) |
| Search active, or Drafted / My team view | Unchanged: relevance or pick history |
| Mock drafts (`/mock`) | Not used; mocks keep their own strategy |

The first pick deliberately keeps board order. With an empty roster, every
open slot makes raw season points look like gain, which would push QBs above the
board's scarcity ranking (`public/app.ts` gates this).

The score applies whether or not Brian is on the clock and during saved-draft
replay; it always looks at the next own pick. Positional views keep tier groups
and apply the order within each tier.

## Inputs

Everything comes from the board and the draft state; there are no hand-tuned
per-position weights.

- **Roster slots** — `board.roster_slots`, including `W/T`, `W/R/T`, and `BN`.
- **Brian's roster** — unique own picks, matched to board rows by key or player
  identity. DST normalizes to DEF.
- **Projections** — each player's `points`.
- **Market** — `adp` and `adp_stdev`.
- **Draft position** — `draft.next` (or picks so far), team order, and rounds,
  which determine Brian's upcoming snake picks.

## The score

### 1. Lineup gain

`projectedLineup` (in `roster-fit.ts`) finds the best starting lineup for a
roster: an exact weighted assignment of players to dedicated and flex slots,
one slot per player, bench scoring nothing.

```text
gain(p) = lineup(roster + p).total − lineup(roster).total
```

A first WR gains its full projection. A second TE gains only what it adds by
beating the current flex. A player who fits nowhere gains 0.

### 2. Value now

Gain alone treats every non-starter as worthless, which made late rounds
arbitrary. Value now adds a discounted share of the points that do not start:

```text
value_now(p) = gain(p) + DEPTH_WEIGHT × max(0, points(p) − gain(p))
```

`DEPTH_WEIGHT` is 0.25. A full starter counts all its points; a pure bench
player counts a quarter of them; a player who displaces a starter counts the
gain plus a quarter of the remainder. When Brian's bench slots are already
full, the depth weight is 0 and only real lineup gain counts.

### 3. Survival to the next pick

Survival is the probability that a player is still on the board at Brian's own
pick *after* the one about to be made:

```text
survival(q) = 1 − Φ((next_own_pick − adp) / σ)
σ = adp_stdev if > 0, else max(3, 0.1 × adp)
```

- A player with no ADP has survival 1 (treated as uncontested).
- If there is no later own pick, there is no survival and no cost.

### 4. Opportunity cost

For each available player `p`, look at every available player at the same
position, `p` included, sorted by value now. The cost is the expected best value
Brian can still take there at the next own pick: the best player if that
player survives, otherwise the next, and so on.

```text
cost(p) = Σᵢ valueᵢ × survivalᵢ × Πⱼ<ᵢ (1 − survivalⱼ)
score(p) = round(value_now(p) − cost(p), 0.1)
```

Only players with positive value count. Including `p` is what makes the score
measure urgency: a player who will certainly last scores about 0 however large
the drop-off behind that player, because Brian can take someone else now and
still get the player later. A player who will certainly be gone scores their
value minus the best surviving alternative.

## Ordering

`lineupPriorityOrder` sorts:

1. Players with a score, highest first.
2. For equal scores, board rank plus five places for each same-position player
   on Brian's roster with the same bye ("Bye clash"). Unknown byes are neutral.
3. Board rank.

Players without a score come after all scored players, ordered open starter
need, then RB/WR/TE depth in positions the league starts, then everything else,
each with the same bye/rank tiebreak.

A bye penalty only breaks ties; it never outranks a higher score.

## Reason strings

Each Available row explains its score:

| Case | Example |
| --- | --- |
| Starter or flex gain | `WR starter · +180.0 lineup pts · likely gone by your next pick` |
| Flex upgrade | `W/R/T flex · +120.0 lineup pts · coin flip to last` |
| Bench depth | `RB depth · +50.0 (bench) · should last to your next pick` |
| No value | `Depth · no projected lineup gain` |
| Unprojected player | `Open starter/flex need · projection unavailable` |
| Incomplete roster | `Depth · roster projections incomplete` |

The survival phrase uses the row's own survival: below 25% "likely gone by your
next pick", 25–75% "coin flip to last", above 75% "should last to your next
pick". It is omitted at the last own pick and on rows with no value.

The summary above the list shows open starting slots (`Need: QB 1 · TE 1`) or
`Starters filled · Building depth`.

## Worked example

Slots `QB 1, RB 1, BN 2`, two teams, Brian picks 1 and 4. The roster is empty
(scores shown for illustration; the live UI would still use board order before
the first own pick).

| Player | Points | ADP (σ 0.5) | Survival to pick 4 |
| --- | --- | --- | --- |
| QB A | 330 | 30 | ~1 |
| QB B | 320 | 60 | ~1 |
| RB A | 200 | 2 | ~0 |
| RB B | 190 | 1.5 | ~0 |

- QB A: value 330, cost ≈ 330 because QB A will still be there →
  **score 0**.
- RB A: value 200, cost ≈ 0 because both RBs will be gone → **score 200**.

RB A ranks first even though QB A adds more raw points, because QB A will still
be there next turn. This is the case the old grouped ranking got wrong in the
2026 draft (Drake Maye recommended in rounds 2 and 3). The same rule now passes
on Maye at pick 39 (90% to last) in favor of Garrett Wilson.

## Fallbacks and edge cases

- **Incomplete roster.** If any of Brian's picks has no current board
  projection (for example, a board republish dropped a player), no scores are
  computed. Rows fall back to open-slot matching and board rank, and the
  summary says `roster projections incomplete`. The missing player still
  occupies a slot by position.
- **Unprojected candidates** get no score and a `projection unavailable` reason.
- **Last own pick.** Cost is 0, so the score is value now.
- **Stale ADP.** ADP is a pre-draft snapshot, not this league's pace. A player
  who has fallen past their ADP shows "likely gone" every turn until taken. This
  was accepted for v1; league-tendency survival was not needed to win the
  backtest.
- **Players without ADP** (most of the deep pool) count as always available,
  so they score about 0 and fall back to board rank among themselves. Late
  rounds are ordered mostly by contested players' depth value.
- **Back-to-back picks.** At a snake turn (for example picks 10 and 11) the
  next own pick is one spot later, so nearly everyone survives and scores sit
  near 0, leaving board rank to decide. This rarely matters while board order
  is kept before the first own pick; measuring the cost at the following
  contested pick showed no backtest gain and was left out.
- **Backup QBs** carry more depth value than backup RBs/WRs because QB
  projections are larger; followed rosters average 2.5 QBs.

## Validation

- Unit tests: `tracker/test/starter-priority.test.ts` cover survival math,
  expected-best alternatives, the QB-cliff case, the candidate's own survival,
  depth, a full bench, reason strings, the last pick,
  tiebreaks, and fallbacks.
- Browser: `tracker/test/browser/live-priority.spec.ts`.
- Backtest (see [tracker.md](tracker.md#recommendation-backtest)), September 6
  board, saved MCFFL 2026 draft:
  - Brian's seat (slot 2): 1865.3, against 1850.2 for board rank, 1826.2 for
    the former gain-group ranking, and 1791.7 actual.
  - Every seat (`--all-teams`): 18362.6 against 18387.2 for board rank, with
    wins or ties at seven of ten slots. The largest loss (slot 8, −44) comes
    from CeeDee Lamb lasting to pick 33 against an ADP of 12. Measuring cost
    only against other players at the position scored 18274.6.
  - Depth weights 0.1–0.25 stay within 12 points with one DEF per roster. A
    weight of 0 scores 18392 but drafts up to five DEFs, as board rank does,
    because the followed total never sees the bench.

These totals come from one draft with a stale ADP snapshot; per-slot swings of
±40 points are common. Use the backtest to catch gross failures (early QBs,
stacked DEFs, stuck depth picks), not to tune differences of a few points. Any
change to the model should keep the unit tests passing.
