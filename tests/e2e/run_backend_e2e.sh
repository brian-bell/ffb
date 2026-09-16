#!/bin/sh

set -u

e2e_tmp=$(mktemp -d "${TMPDIR:-/tmp}/ffb-backend-e2e.XXXXXX") || exit 1
if [ -z "$e2e_tmp" ] || [ ! -d "$e2e_tmp" ]; then
    echo "backend e2e: mktemp did not create a usable directory" >&2
    exit 1
fi

snapshot_dir="$e2e_tmp/snapshots"
db_path="$e2e_tmp/data/ffb.duckdb"
export_dir="$e2e_tmp/exports"
board_path="$export_dir/board.json"
inseason_dir="$e2e_tmp/inseason"
capture_key="e2e-publish-key"
capture_pid=""
phase="setup"
phase_log="$e2e_tmp/phase.log"

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ -n "$capture_pid" ]; then
        kill "$capture_pid" 2>/dev/null || true
    fi
    if [ "$status" -ne 0 ]; then
        echo "backend e2e failed during: $phase" >&2
        if [ -s "$phase_log" ]; then
            cat "$phase_log" >&2
        fi
        echo "board path: $board_path" >&2
        if [ -f "$board_path" ]; then
            uv run python -c \
                'import json, sys; b=json.load(open(sys.argv[1])); print("board version: {}; player count: {}".format(b.get("version"), len(b.get("players", []))), file=sys.stderr)' \
                "$board_path" || true
        fi
    fi

    if [ "${FFB_E2E_KEEP_TMP:-0}" = "1" ]; then
        echo "backend e2e artifacts: $e2e_tmp" >&2
    else
        rm -rf -- "$e2e_tmp"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

run_phase() {
    phase=$1
    shift
    : >"$phase_log"
    if "$@" >"$phase_log" 2>&1; then
        return 0
    else
        phase_status=$?
        return "$phase_status"
    fi
}

run_phase "snapshot priming" \
    uv run python tests/e2e/prime_snapshots.py "$snapshot_dir" || exit $?

run_phase "Yahoo league fixture sync" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
    uv run ffb league sync 2024 --fixture tests/fixtures/yahoo_league_e2e.json || exit $?

run_phase "season data sync" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
    uv run ffb season sync 2024 --offline || exit $?

run_phase "board export" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" FFB_EXPORT_DIR="$export_dir" \
    uv run ffb board export 2024 --output-dir "$export_dir" || exit $?

run_phase "board validation" \
    uv run python -c '
import json
import sys

board = json.load(open(sys.argv[1]))
keys = [player.get("key") for player in board.get("players", [])]
assert board.get("version") == 1, "expected board version 1"
assert board.get("season") == 2024, "expected board season 2024"
assert all(isinstance(key, str) and key for key in keys), "player keys must be nonempty"
assert len(set(keys)) == len(keys), "player keys must be unique"
assert {"12626", "13971", "10976", "def:SFO"}.issubset(keys), \
    "expected all draftable matched fixture players"
assert "16000" not in keys, "inactive fixture player must be absent from the default board"
assert all(player.get("matched") is True for player in board["players"]), \
    "board must exclude unmatched players"
byes = {player["key"]: player.get("bye") for player in board["players"]}
assert byes.get("12626") == 4, "Henry bye must come from the schedule (4), not FFC (14)"
assert byes.get("def:SFO") == 2, "D/ST bye must come from the schedule (2), not FFC (9)"
henry = next(player for player in board["players"] if player["key"] == "12626")
assert henry.get("injury", {}).get("status") == "QUESTIONABLE", \
    "matched Sleeper injury status must reach board.json"
print("board version: {}; player count: {}".format(board["version"], len(keys)))
' "$board_path" || exit $?

# In-season publish: the four report commands POST their envelopes to a local
# capture server standing in for the Worker; the Worker e2e replays them.
run_phase "sit/start league fixture sync" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
    uv run ffb league sync 2024 --fixture tests/fixtures/yahoo_lineup_sitstart.json || exit $?

run_phase "weekly projection and news sync" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
    uv run ffb season sync 2024 --offline --week 1 --source projections --source news || exit $?

phase="publish capture server"
mkdir -p "$inseason_dir"
uv run python tests/e2e/capture_publish.py "$inseason_dir" --api-key "$capture_key" 2>"$e2e_tmp/capture.log" &
capture_pid=$!
attempts=0
while [ ! -s "$inseason_dir/port" ]; do
    attempts=$((attempts + 1))
    if [ "$attempts" -gt 100 ] || ! kill -0 "$capture_pid" 2>/dev/null; then
        echo "backend e2e: publish capture server did not start" >&2
        cat "$e2e_tmp/capture.log" >&2
        exit 1
    fi
    sleep 0.1
done
capture_url="http://127.0.0.1:$(cat "$inseason_dir/port")"

run_phase "lineup publish" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
        FFB_TRACKER_URL="$capture_url" FFB_TRACKER_API_KEY="$capture_key" \
    uv run ffb lineup 2024 --publish || exit $?

run_phase "rest-of-season publish" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
        FFB_TRACKER_URL="$capture_url" FFB_TRACKER_API_KEY="$capture_key" \
    uv run ffb ros 2024 --publish || exit $?

run_phase "digest publish" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
        FFB_TRACKER_URL="$capture_url" FFB_TRACKER_API_KEY="$capture_key" \
        ANTHROPIC_API_KEY= FFB_ANTHROPIC_API_KEY= \
    uv run ffb digest 2024 --publish || exit $?

run_phase "retro publish" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" \
        FFB_TRACKER_URL="$capture_url" FFB_TRACKER_API_KEY="$capture_key" \
    uv run ffb retro 2024 --week 1 --fixture tests/fixtures/weekly_actuals_minimal.json --publish || exit $?

run_phase "publish envelope validation" \
    uv run python -c '
import json
import sys

out = sys.argv[1]
for kind in ("lineup", "digest", "retro", "ros"):
    envelope = json.load(open(f"{out}/{kind}.json"))
    assert envelope["schema_version"] == 1, f"{kind}: schema_version must be 1"
    assert envelope["kind"] == kind, f"{kind}: kind mismatch"
    assert envelope["season"] == 2024 and envelope["week"] == 1, f"{kind}: expected 2024 week 1"
    assert envelope["team_name"] == "Brian\x27s Team", f"{kind}: team_name"
    assert envelope["generated_at"].endswith("Z"), f"{kind}: generated_at"
    assert "FFB_TRACKER_API_KEY" not in json.dumps(envelope) and "e2e-publish-key" not in json.dumps(envelope)
lineup = json.load(open(f"{out}/lineup.json"))
assert lineup["context"]["snapshot_generated_at"], "lineup must reference the locked snapshot"
assert lineup["report"]["start"], "lineup fixture should recommend a swap"
digest = json.load(open(f"{out}/digest.json"))
assert digest["report"]["llm"]["error"], "digest must record the skipped LLM"
retro = json.load(open(f"{out}/retro.json"))
assert retro["context"]["actuals_synced_at"] == "2026-09-16T16:00:00Z"
assert isinstance(retro["report"]["hindsight_total"], (int, float))
assert isinstance(retro["report"]["hindsight_started_total"], (int, float))
assert isinstance(retro["report"]["hindsight_delta"], (int, float))
assert isinstance(retro["report"]["hindsight_start"], list)
assert isinstance(retro["report"]["hindsight_sit"], list)
ros = json.load(open(f"{out}/ros.json"))
assert ros["context"]["playoff_weeks_requested"] == [15, 16, 17]
assert ros["report"]["bye_plan"], "ros must publish the bye plan for the stored roster"
print("captured envelopes:", ", ".join(("lineup", "digest", "retro", "ros")))
' "$inseason_dir" || exit $?

run_phase "Worker draft journeys" \
    env FFB_E2E_BOARD_PATH="$board_path" FFB_E2E_INSEASON_DIR="$inseason_dir" \
    npm --prefix tracker run test:e2e || exit $?

echo "backend e2e passed"
