"""Shared fixtures."""

import json
from pathlib import Path

import pytest

from ffb.ingest import resolve_rows
from ffb.sources.crosswalk import parse_crosswalk
from ffb.sources.sleeper import parse_projections
from ffb.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"
XWALK_FIXTURE = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


@pytest.fixture
def sample_rows():
    return parse_projections(json.loads(FIXTURE.read_text()))


@pytest.fixture
def crosswalk_rows():
    return parse_crosswalk(json.loads(XWALK_FIXTURE.read_text()))


@pytest.fixture
def store(tmp_path):
    """A fresh on-disk DuckDB store, schema initialized."""
    s = Store(tmp_path / "test.duckdb")
    s.init_schema()
    yield s
    s.close()


@pytest.fixture
def seeded_store(store, sample_rows):
    # No crosswalk seeded here, so every player resolves to a fallback key —
    # rankings still work off source identity. Tests that need matched keys seed
    # the crosswalk explicitly.
    resolved, _ = resolve_rows(store, sample_rows, "sleeper")
    store.upsert_projections(resolved)
    return store
