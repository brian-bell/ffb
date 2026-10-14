"""A rostered player whose nflverse row lacks sleeper_id still gets a projection.

Captured 2026-10-14 from nflverse, api.sleeper.app/v1/players/nfl,
api.sleeper.com projections for 2026 week 6, and ESPN kona_player_info period 6.
Trey Smack (Sleeper 13545, ESPN 4869461, mfl 17668) is the regression case.
"""

import json
from pathlib import Path

from ffb.config import ScoringConfig
from ffb.consensus import consensus_rows
from ffb.ingest import ensure_crosswalk, ensure_espn_ingested, ensure_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources import crosswalk, espn, sleeper, sleeper_players

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_id_gap_week6.json"


def _no_network():
    raise AssertionError("must not fetch when the snapshot exists")


def test_week_projection_joins_after_the_player_map_fills_sleeper_id(store, tmp_path):
    gap = json.loads(FIXTURE.read_text())
    cache = SnapshotCache(tmp_path / "snap")
    cache.put_json(crosswalk.snapshot_key(), gap["crosswalk"])
    cache.put_json(sleeper.snapshot_key(2026, week=6), gap["sleeper_week"])
    cache.put_json(espn.snapshot_key(2026, week=6), gap["espn_week"])

    ensure_crosswalk(store, cache, fetch=_no_network)
    assert store.resolve("sleeper", "13545") is None
    assert store.resolve("espn", "4869461") == "17668"

    sleeper_recon = ensure_ingested(store, cache, 2026, week=6, fetch=_no_network)
    espn_recon = ensure_espn_ingested(store, cache, 2026, week=6, fetch=_no_network)
    assert sleeper_recon.unmatched == 1
    assert sleeper_recon.matched == 0
    assert espn_recon.matched == 1
    sleeper_row = store.projection_rows(2026, source="sleeper", scope="week6")[0]
    assert sleeper_row["player_key"] == "sleeper:13545"
    assert sleeper_row["matched"] is False
    assert sleeper_row["position"] == "K"
    espn_row = store.projection_rows(2026, source="espn", scope="week6")[0]
    assert espn_row["player_key"] == "17668"
    assert espn_row["matched"] is True
    assert espn_row["position"] == "K"

    cache.put_json(sleeper_players.snapshot_key(), gap["players"])
    healed = ensure_ingested(store, cache, 2026, week=6, fetch=_no_network)
    assert store.resolve("sleeper", "13545") == "17668"
    assert healed.matched == 1
    assert healed.unmatched == 0
    healed_row = store.projection_rows(2026, source="sleeper", scope="week6")[0]
    assert healed_row["player_key"] == "17668"
    assert healed_row["matched"] is True
    assert healed_row["position"] == "K"
    assert healed_row["native_id"] == "13545"

    consensus = consensus_rows(store, 2026, week=6, cfg=ScoringConfig({"xpm": 1.0}))
    smack = next(row for row in consensus if row["player_key"] == "17668")
    assert smack["n"] == 2
    assert smack["position"] == "K"
    assert smack["consensus"] == round((2.75 + 2.831773743) / 2, 2)


def test_crosswalk_refresh_links_sleeper_id_from_the_cached_player_map(store, tmp_path):
    gap = json.loads(FIXTURE.read_text())
    cache = SnapshotCache(tmp_path / "snap")
    cache.put_json(sleeper_players.snapshot_key(), gap["players"])
    ensure_crosswalk(store, cache, fetch=lambda: gap["crosswalk"])
    assert store.resolve("sleeper", "13545") == "17668"
    assert store.resolve("espn", "4869461") == "17668"
