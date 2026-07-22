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
phase="setup"
phase_log="$e2e_tmp/phase.log"

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
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
    uv run ffb league sync --season 2024 --fixture tests/fixtures/yahoo_league_minimal.json || exit $?

run_phase "board export" \
    env FFB_DB_PATH="$db_path" FFB_SNAPSHOT_DIR="$snapshot_dir" FFB_EXPORT_DIR="$export_dir" \
    uv run ffb cheatsheet --season 2024 --export --export-dir "$export_dir" || exit $?

run_phase "board validation" \
    uv run python -c '
import json
import sys

board = json.load(open(sys.argv[1]))
keys = [player.get("key") for player in board.get("players", [])]
assert board.get("version") == 1, "expected board version 1"
assert board.get("season") == 2024, "expected board season 2024"
assert all(isinstance(key, str) and key for key in keys), "player keys must be nonempty"
assert len(set(keys)) >= 6, "expected at least six unique player keys"
print("board version: {}; player count: {}".format(board["version"], len(keys)))
' "$board_path" || exit $?

run_phase "Worker draft journeys" \
    env FFB_E2E_BOARD_PATH="$board_path" npm --prefix tracker run test:e2e || exit $?

echo "backend e2e passed"
