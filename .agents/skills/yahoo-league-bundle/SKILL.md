---
name: yahoo-league-bundle
description: Capture Brian's Yahoo MCFFL league (settings, teams, current-week rosters) from the live Yahoo Fantasy UI into a closed-schema LeagueBundle and ingest it through the tracker's POST /api/league/bundle. Use when the tracker's Yahoo bundle is stale or the wrong week (make refresh prints "bundle yahoo:470.l.928421 ... STALE"), when rosters changed after the morning post, or when asked to run, rebuild, or re-post the Yahoo league bundle.
---

# Yahoo league bundle

Produce one `LeagueBundle` for MCFFL (`470.l.928421`, 10 teams, user team
Turkey Supreme) from the live Yahoo UI, validate it with the repo's own
contract, POST it to the tracker, and read it back. The contract, stat map,
and error table live in [docs/grok-bot-prompt.md](../../../docs/grok-bot-prompt.md);
the Worker side is in [docs/tracker.md](../../../docs/tracker.md).

## Guardrails

- Read-only on Yahoo: same-origin GETs only. Never change a lineup, waiver,
  or setting, and never call Yahoo APIs.
- Secrets come only from the repo `.env` (`FFB_TRACKER_URL`,
  `FFB_TRACKER_API_KEY`). Source it inside each Bash call and never print it.
- Manager nicknames only. The capture never reads the Teams page email
  column; the builder rejects any `@` or `<` in the bundle.
- Keep captures and bundles in the scratchpad, never in git.
- Do not run `uv run ffb league sync` or `make refresh` unless asked; this
  skill ends at the tracker ingest.

## Steps

Run shell steps from the repo root. Shell state does not persist between
tool calls, so set `SCRATCH` (the session scratchpad, or `mktemp -d`), `UA`,
and the sourced `.env` in every call that uses them.

1. **Previous bundle and freshness.** Save what the Worker holds now so the
   builder can diff against it:

   ```bash
   set -a && . ./.env && set +a
   UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36'
   curl -s -H "Authorization: Bearer $FFB_TRACKER_API_KEY" -A "$UA" \
     "$FFB_TRACKER_URL/api/league/bundle?league=yahoo:470.l.928421" > "$SCRATCH/previous.json"
   ```

2. **Capture.** In Chrome (Claude in Chrome: `navigate` +
   `javascript_tool`; Chrome is already signed in to Yahoo), open
   `https://football.fantasysports.yahoo.com/f1/928421` and run the full
   contents of [scripts/capture.js](scripts/capture.js). It reads the league
   home, Settings, Teams, and all ten roster pages and returns a summary.
   Expect `game_keys: ["470"]`, `league_name: "MCFFL"`, the current `week`,
   `teams: 10`, and 15 players per team. A thrown error names the roster row
   it could not parse; stop and report it rather than patching data by hand.

3. **Export.** Browser tool output truncates long results. Run
   [scripts/export-chunk.js](scripts/export-chunk.js) once per chunk
   (`CHUNK = 0 … chunks-1`) and concatenate the slices verbatim, in order:

   ```bash
   cat <<'EOF' | tr -d '\n' > "$SCRATCH/capture.json"
   <chunk 0>
   <chunk 1>
   …
   EOF
   ```

   Each chunk goes on its own line; `tr` removes the joins. If the extension
   blocks an output as sensitive, narrow what you return; do not encode
   around the filter.

4. **Build and validate.**

   ```bash
   uv run python .agents/skills/yahoo-league-bundle/scripts/build_bundle.py \
     "$SCRATCH/capture.json" --season 2026 --out "$SCRATCH/bundle.json" \
     --previous "$SCRATCH/previous.json"
   ```

   It maps the Settings scoring rows through the stat map, derives roster
   slots from "Roster Positions", builds player keys as `470.p.<id>`, checks
   team count, one user team, roster/team key parity, and unique player ids,
   then runs `ffb.league.parse_bundle`. It prints a one-line summary and the
   roster changes versus the previous bundle.

5. **POST, then read back.**

   ```bash
   set -a && . ./.env && set +a
   H=(-H "Authorization: Bearer $FFB_TRACKER_API_KEY" -A "$UA"
      -H "Origin: $FFB_TRACKER_URL" -H "Referer: $FFB_TRACKER_URL/command")
   URL="$FFB_TRACKER_URL/api/league/bundle?league=yahoo:470.l.928421"
   curl -s -w '\nHTTP %{http_code}\n' -X POST "${H[@]}" \
     -H 'Content-Type: application/json' --data-binary @"$SCRATCH/bundle.json" "$URL"
   curl -s "${H[@]}" "$URL" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["synced_at"], d["league"]["current_week"], len(d["teams"]), sum(len(r["players"]) for r in d["rosters"]))'
   ```

   Expect `200` with `{ok, season, current_week, teams, players, source,
   synced_at}` and a read-back with the same `synced_at`. Cloudflare rejects
   bare script user agents (error 1010), so always send the browser UA. For
   `400`/`409`/`401`/`5xx`, follow the error table in
   `docs/grok-bot-prompt.md`.

6. **Report** season, week, teams, players, `synced_at`, and the roster
   changes the builder printed. Notify Brian if he is away. Offer
   `make refresh ARGS="--skip-sync"` if the command-center cards should pick
   up the new rosters.

## What the page gives you (verified 2026-09-26)

- Roster pages list every player as a `table tbody tr` with at least six
  cells. The slot is the first cell (`QB`, `W/T`, `W/R/T`, `BN`, `DEF`, `IR`).
  The player id is in `pid=`/`playerid="…"` attributes, the name is `a.name`,
  and `"<Tm> - <POS[,POS]>"` gives team and positions. Brian's own team page
  adds a slot-eligibility cell, which is why the script does not index cells.
- Yahoo never renders player keys; `470.l.928421` on the Settings page
  confirms the game key, so keys are `<game>.p.<id>`.
- Team defenses have ids `1000NN` and positions `DEF`. Their display names are
  nicknames ("Lions", "49ers").
- Team abbreviations are passed through upper-cased (`JAX`, `KC`, `LV`); the
  pipeline canonicalizes them in `identity.canonical_team`.
- Settings list 7 bench slots (15-man rosters) as of 2026, not 8.
- Scoring rows sit under "Offense" and "Defense/Special Teams" headers; the
  section disambiguates labels like "Interception" and "Touchdown". An
  unknown row stops the builder. Add it to `STAT_MAP` or `UNMAPPED` in
  `build_bundle.py` with the real Yahoo stat id; never guess an id. "Extra
  Point Returned" (id 82) has no projection line and stays unmapped.
- The same player on two rosters means a transaction is in flight: stop and
  retry later rather than choosing one.

The builder emits schema v2 (`native_id` / `native_player_key`). The Worker
still upgrades v1 (`yahoo_player_id` / `yahoo_player_key`) on ingest, but v2
is the current contract.
