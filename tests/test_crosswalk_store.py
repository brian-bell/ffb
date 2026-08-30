"""Store side of the crosswalk: load the spine and resolve native ids to keys."""


def test_resolve_maps_native_ids_to_player_key(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    # Both sources' native ids resolve to the same canonical key (Henry).
    assert store.resolve("sleeper", "3198") == "12626"
    assert store.resolve("espn", "3043078") == "12626"


def test_resolve_returns_none_on_miss(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    assert store.resolve("sleeper", "does-not-exist") is None


def test_resolve_skips_ambiguous_native_id(store):
    store.replace_crosswalk(
        [
            {
                "player_key": "12571",
                "sleeper_id": "2295",
                "espn_id": None,
                "yahoo_id": None,
                "gsis_id": None,
                "full_name": "Kevin Smith",
                "position": "WR",
                "team": "SEA",
            },
            {
                "player_key": "12459",
                "sleeper_id": "2295",
                "espn_id": None,
                "yahoo_id": None,
                "gsis_id": None,
                "full_name": "Fred Williams",
                "position": "WR",
                "team": "KCC",
            },
        ]
    )
    assert store.resolve("sleeper", "2295") is None
    assert store.resolve_batch("sleeper", ["2295"]) == {}


def test_resolve_on_empty_crosswalk_returns_none(store):
    assert store.resolve("sleeper", "3198") is None


def test_upsert_crosswalk_is_idempotent(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    store.upsert_crosswalk(crosswalk_rows)  # same rows again, must not error
    assert store.resolve("espn", "4362628") == "13971"  # Ja'Marr Chase
