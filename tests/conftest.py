"""Shared fixtures."""

import json
from pathlib import Path

import pytest

from ffb.sources.sleeper import parse_projections
from ffb.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"


@pytest.fixture
def sample_rows():
    return parse_projections(json.loads(FIXTURE.read_text()))


@pytest.fixture
def store(tmp_path):
    """A fresh on-disk DuckDB store, schema initialized."""
    s = Store(tmp_path / "test.duckdb")
    s.init_schema()
    yield s
    s.close()


@pytest.fixture
def seeded_store(store, sample_rows):
    store.upsert_projections(sample_rows)
    return store
