TRACKER := npm --prefix tracker
SEASON ?= 2026

.PHONY: help init check-app test-backend-e2e export-board deploy-board deploy-app deploy-all

help:
	@echo "make init          Install Python and tracker dependencies"
	@echo "make test-backend-e2e  Run the offline CLI-to-Worker backend journey"
	@echo "make deploy-board  Refresh, export, and publish board.json to production KV"
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
	uv run ffb season sync $(SEASON) --refresh
	uv run ffb board export $(SEASON) --output-dir exports

deploy-board: export-board
	test -s exports/board.json
	$(TRACKER) run publish:board:remote

deploy-app: check-app
	cd tracker && npx wrangler d1 migrations apply ffb-tracker --remote
	$(TRACKER) run deploy

deploy-all: deploy-app deploy-board
