TRACKER := npm --prefix tracker
SEASON ?= 2026
# Publish gate: a real draftable board is several hundred players, so anything
# near-empty means a source degraded rather than a smaller draft pool.
MIN_BOARD_PLAYERS ?= 100

.PHONY: help init check-app test-backend-e2e export-board deploy-board deploy-app deploy-all

help:
	@echo "make init          Install Python and tracker dependencies"
	@echo "make test-backend-e2e  Run the offline CLI-to-Worker backend journey"
	@echo "make deploy-board  Export and publish the persisted board.json to production KV"
	@echo "make deploy-app    Validate and deploy the tracker application"
	@echo "make deploy-all    Deploy the application, then publish the board"

init:
	uv sync
	cd tracker && npm i

check-app:
	$(TRACKER) run typecheck
	$(TRACKER) test

test-backend-e2e:
	./tests/e2e/run_backend_e2e.sh

export-board:
	uv run ffb board export $(SEASON) --output-dir exports

deploy-board: export-board
	test -s exports/board.json
	uv run python -c "import json,sys; n=len(json.load(open('exports/board.json')).get('players') or []); sys.exit(0 if n >= $(MIN_BOARD_PLAYERS) else f'exports/board.json holds {n} player(s), under MIN_BOARD_PLAYERS=$(MIN_BOARD_PLAYERS); refusing to publish')"
	$(TRACKER) run publish:board:remote

deploy-app: check-app
	cd tracker && npx wrangler d1 migrations apply ffb-tracker --remote
	$(TRACKER) run deploy

deploy-all: deploy-app deploy-board
