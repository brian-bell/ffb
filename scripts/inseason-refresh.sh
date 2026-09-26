#!/usr/bin/env bash
# In-season refresh: the CLI half of the scheduled runs in docs/operations.md.
# Yahoo scrapes and their POSTs (LeagueBundle, WeeklyActualsBundle) happen
# outside this script; it syncs free sources and both leagues, publishes the
# command-center cards for Yahoo and Sleeper, and prints card freshness.
#
# Usage: scripts/inseason-refresh.sh [--dry-run] [--week-roll] [--sunday]
#                                    [--skip-sync] [--week N]
#   --dry-run    read-only: show week, bundle freshness, and the commands
#   --week-roll  Wednesday: add the Sleeper W-1 backfill and retro, and the
#                Yahoo retro when the Worker already holds W-1 Yahoo actuals
#   --sunday     sync projections/injuries/news only (pre-kickoff)
#   --skip-sync  skip season sync
#   --week N     override the week (default: Sleeper /state/nfl)
#
# Configuration comes from the environment, loaded from .env (or
# $FFB_ENV_FILE) when present: FFB_TRACKER_URL, FFB_TRACKER_API_KEY,
# FFB_SLEEPER_LEAGUE_ID, FFB_SLEEPER_USER_ID, optional FFB_YAHOO_LEAGUE_KEY,
# and ANTHROPIC_API_KEY or FFB_ANTHROPIC_API_KEY for the digest narrative.
# Full CLI output goes to data/refresh-logs/<timestamp>.log; stdout carries
# only summary lines. Exits non-zero on the first failed command.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36'

dry=false week_roll=false sunday=false skip_sync=false W=''
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=true ;;
    --week-roll) week_roll=true ;;
    --sunday) sunday=true ;;
    --skip-sync) skip_sync=true ;;
    --week) W="${2:?--week needs a value}"; shift ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$REPO"
ENV_FILE="${FFB_ENV_FILE:-$REPO/.env}"
# shellcheck source=/dev/null
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
for v in FFB_TRACKER_URL FFB_TRACKER_API_KEY FFB_SLEEPER_LEAGUE_ID FFB_SLEEPER_USER_ID; do
  [ -n "${!v:-}" ] || { echo "error: $v is not set (env or $ENV_FILE)" >&2; exit 1; }
done
[ -n "${ANTHROPIC_API_KEY:-}${FFB_ANTHROPIC_API_KEY:-}" ] \
  || echo "warning: no Anthropic key; digests publish without the narrative" >&2

YAHOO_LEAGUE="${FFB_YAHOO_LEAGUE_KEY:-470.l.928421}"
YAHOO_KEY="yahoo:${YAHOO_LEAGUE#yahoo:}"
SLEEPER_KEY="sleeper:$FFB_SLEEPER_LEAGUE_ID"

export COLUMNS=200  # keep rich output on one line for the summary filter
LOG_DIR="$REPO/data/refresh-logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"

api() {  # GET a tracker route; fails on 4xx/5xx
  curl -fsS -A "$UA" -H "Authorization: Bearer $FFB_TRACKER_API_KEY" "$FFB_TRACKER_URL$1"
}

state=$(curl -fsS https://api.sleeper.app/v1/state/nfl)
S=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["season"])' <<<"$state")
[ -n "$W" ] || W=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["week"])' <<<"$state")
PREV=$((W - 1))
echo "season $S week $W  (log: $LOG)"

leagues=$(api /api/leagues) || { echo "error: tracker GET /api/leagues failed" >&2; exit 1; }
LEAGUES_JSON="$leagues" python3 - "$W" <<'EOF'
import datetime as dt, json, os, sys
week = int(sys.argv[1])
today = dt.datetime.now(dt.timezone.utc).date()
for league in json.loads(os.environ["LEAGUES_JSON"])["leagues"]:
    synced = dt.datetime.fromisoformat(league["synced_at"].replace("Z", "+00:00"))
    fresh = synced.date() == today and league["current_week"] == week
    print(f"bundle {league['league_key']}: week {league['current_week']} "
          f"synced {league['synced_at']} -> {'fresh' if fresh else 'STALE'}")
EOF

# run LABEL ffb-args...: full output to the log, summary lines to stdout.
run() {
  local label="$1" out
  shift
  if $dry; then echo "would run: ffb $*"; return; fi
  echo "== $label: ffb $*" >>"$LOG"
  if ! out=$(uv run --quiet ffb "$@" 2>&1); then
    printf '%s\n' "$out" >>"$LOG"
    echo "FAIL $label: ffb $*"
    printf '%s\n' "$out" | tail -n 15
    exit 1
  fi
  printf '%s\n' "$out" >>"$LOG"
  printf '%s\n' "$out" \
    | grep -E '^(ready|Synced|Published|Start |Sit |Close |Current |Stored lineup|⚠)|unmatched' \
    | cut -c1-220 | sed "s/^/  [$label] /" || true
}

if ! $skip_sync; then
  if $sunday; then
    run sync season sync "$S" --week "$W" --source projections --source injuries --source news --refresh
  else
    run sync season sync "$S" --week "$W" --refresh
  fi
fi
run yahoo-league league sync "$S" --from-tracker
run sleeper-league league sync "$S" --league sleeper

if $week_roll; then
  run sleeper-backfill league sync "$S" --league sleeper --week "$PREV"
  run sleeper-retro retro "$S" --week "$PREV" --league sleeper --from-matchups --publish
  if api "/api/actuals?season=$S&week=$PREV&league=$YAHOO_KEY" >/dev/null 2>&1; then
    run yahoo-retro retro "$S" --week "$PREV" --league yahoo --publish
  else
    echo "  [yahoo-retro] skipped: no week $PREV Yahoo actuals on the Worker yet"
  fi
fi

for league in yahoo sleeper; do
  run "$league-ros" ros "$S" --league "$league" --publish
  run "$league-lineup" lineup "$S" --week "$W" --league "$league" --force --publish
  run "$league-digest" digest "$S" --league "$league" --publish
done

for key in "$YAHOO_KEY" "$SLEEPER_KEY"; do
  view=$(api "/api/inseason?season=$S&week=$W&league=$key") || { echo "error: GET /api/inseason for $key failed" >&2; exit 1; }
  VIEW_JSON="$view" python3 - "$key" <<'EOF'
import json, os, sys
view = json.loads(os.environ["VIEW_JSON"])
print(f"dashboard {sys.argv[1]}: actuals weeks {sorted(view.get('actuals_available', {}))}")
for kind, card in sorted((view.get("cards") or {}).items()):
    envelope = (card or {}).get("envelope") or {}
    print(f"  {kind:7} week {envelope.get('week')}  generated {envelope.get('generated_at')}")
EOF
done
