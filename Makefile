SHELL := /bin/zsh

TRACKER := npm --prefix tracker

.PHONY: help init check-app export-board deploy-board deploy-app deploy-all

help:
	@echo "make init          Install Python and tracker dependencies"
	@echo "make deploy-board  Refresh, export, and publish board.json to production KV"
	@echo "make deploy-app    Validate and deploy the tracker application"
	@echo "make deploy-all    Deploy the application, then publish the board"

init:
	uv sync
	cd tracker && npm i

check-app:
	$(TRACKER) run typecheck
	$(TRACKER) test

export-board:
	uv run ffb cheatsheet --refresh --export

deploy-board: export-board
	test -s exports/board.json
	$(TRACKER) run publish:board:remote

deploy-app: check-app
	cd tracker && npx wrangler d1 migrations apply ffb-tracker --remote
	$(TRACKER) run deploy

deploy-all: deploy-app deploy-board
