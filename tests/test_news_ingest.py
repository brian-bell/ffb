"""News headline ingest: snapshot, resolve ESPN athlete ids, replace slices."""

import json
from pathlib import Path

import pytest

from ffb.ingest import ensure_news_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.crosswalk import parse_crosswalk
from ffb.sources.espn_news import rss_snapshot_key, snapshot_key
from ffb.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def _store(tmp_path):
    store = Store(tmp_path / "ffb.duckdb")
    store.init_schema()
    raw = json.loads((FIXTURES / "ff_playerids_sample.json").read_text())
    store.replace_crosswalk(parse_crosswalk(raw))
    return store


def test_news_ingest_resolves_athlete_ids_and_keeps_unmatched(tmp_path):
    cache = SnapshotCache(tmp_path / "snapshots")
    store = _store(tmp_path)
    result = ensure_news_ingested(
        store,
        cache,
        2024,
        fetch=lambda: json.loads((FIXTURES / "espn_news_sample.json").read_text()),
        fetch_rss=lambda: json.loads((FIXTURES / "espn_news_rss_sample.json").read_text()),
        fetched_at="2026-09-12T12:00:00Z",
    )
    headlines = {row["native_id"]: row for row in store.headline_rows(2024)}
    mentions = store.headline_mention_rows(2024)
    store.close()

    assert result.n_rows == 6
    assert result.matched == 2
    assert headlines["49800111"]["headline"].startswith("Derrick Henry")
    assert headlines["49800111"]["source"] == "espn"
    assert headlines["US-EN-49800121"]["source"] == "espn_rss"
    assert cache.has(snapshot_key())
    assert cache.has(rss_snapshot_key())
    by_native = {row["native_id"]: row for row in mentions}
    assert by_native["3043078"]["player_key"] == "12626"
    assert by_native["3043078"]["matched"] is True
    assert by_native["4500000"]["player_key"] == "16000"
    assert by_native["8888888"]["matched"] is False
    assert by_native["8888888"]["player_key"] == "espn:8888888"


def test_news_ingest_rejects_empty_espn_payload(tmp_path):
    store = _store(tmp_path)
    cache = SnapshotCache(tmp_path / "snapshots")
    with pytest.raises(ValueError, match="no usable"):
        ensure_news_ingested(
            store,
            cache,
            2024,
            fetch=lambda: {"articles": []},
            fetch_rss=lambda: json.loads((FIXTURES / "espn_news_rss_sample.json").read_text()),
        )
    store.close()


def test_news_ingest_keeps_known_espn_when_refresh_is_empty(tmp_path):
    store = _store(tmp_path)
    cache = SnapshotCache(tmp_path / "snapshots")
    ensure_news_ingested(
        store,
        cache,
        2024,
        fetch=lambda: json.loads((FIXTURES / "espn_news_sample.json").read_text()),
        fetch_rss=lambda: {"xml": "<rss/>"},
    )
    with pytest.raises(ValueError, match="no usable"):
        ensure_news_ingested(
            store,
            cache,
            2024,
            refresh=True,
            fetch=lambda: {"articles": []},
            fetch_rss=lambda: {"xml": "<rss/>"},
        )
    assert len(store.headline_rows(2024, source="espn")) == 4
    store.close()


def test_source_counts_and_unmatched_news(tmp_path):
    store = _store(tmp_path)
    cache = SnapshotCache(tmp_path / "snapshots")
    ensure_news_ingested(
        store,
        cache,
        2024,
        fetch=lambda: json.loads((FIXTURES / "espn_news_sample.json").read_text()),
        fetch_rss=lambda: json.loads((FIXTURES / "espn_news_rss_sample.json").read_text()),
    )
    assert store.source_counts(2024, "news") == (6, 2)
    unmatched = store.unmatched_rows(2024, "news")
    store.close()
    assert [row["native_id"] for row in unmatched] == ["8888888"]
    assert unmatched[0]["source"] == "news"
